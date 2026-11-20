#!/usr/bin/env python3
"""Mobile review PoC server: real byte-range streaming of the draft clips, mock notes only.

Deliberately small and standard-library only. It exists to answer one question on a phone: does a streaming
clip selector with a whole-recording coverage timeline feel usable enough to be worth building properly. It is
not the production app, it makes no boundary decisions, and it never writes outside its own scratch directory.

Nothing about the recordings is hardcoded. Clip, still and master paths are read from the draft manifest, and
every media request must resolve through the whitelist built from it, so there is no arbitrary file read, no
directory listing and no host path in any URL the UI sees.

    .venv/bin/python prototypes/mobile-review/server.py --port 8787
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]


def clamp(value, low, high):
    return min(max(value, low), high)


CHUNK = 1 << 20
#: Largest answer to an open-ended range, and the reason playback was stalling.
#:
#: Clamping hard was my own idea, and at 8 MB it was the bottleneck: a 5.5 GB draft became ~700 sequential
#: fetches, each ending in a gap while the browser re-issued the next range, so playback starved near the
#: seams even though the measured per-connection rate (~2.5 MB/s) was several times the stream bitrate.
#: A large window keeps the original point -- no response claims the whole remaining file -- without
#: turning one continuous read into hundreds of discontinuous ones.
MAX_WINDOW = 256 << 20
MAX_NOTES_BYTES = 256_000
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
# A leading proxy prefix is stripped so one build works bare on loopback and under a future
# /tvh-review mount. The list is literal on purpose: guessing that "the first segment is a prefix"
# would eat a real path (/api/state) the moment the app is served bare.
KNOWN_PREFIXES = ("/tvh-review", "")


def strip_prefix(path):
    for prefix in KNOWN_PREFIXES:
        if prefix and path.startswith(prefix + "/"):
            return path[len(prefix):]
        if prefix and path == prefix:
            return "/"
    return path


def build_id():
    """Short fingerprint of the shipped script, so a phone's report identifies the code it is running."""
    try:
        data = (HERE / "app.js").read_bytes()
    except OSError:
        return "unknown"
    return hashlib.sha256(data).hexdigest()[:8]


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _tally(names):
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return counts


def _readable(text):
    """One backend sentence, fit for a 390 px card.

    The recovery layer's own reason text quotes the share as a Python dict, which is right in a JSON
    artifact and unreadable on a phone. Only the formatting is touched here: the number it reports is the
    number the backend wrote.
    """
    return re.sub(r"\{'player1': ([0-9.]+), 'player2': ([0-9.]+)\}",
                  lambda m: "%d%%" % round(min(float(m.group(1)), float(m.group(2))) * 100), str(text))


def confidence_summary(recovery):
    """What the backend said about how much its own names are worth, in the shape a card can render.

    Reduced on purpose: the full report (every factor, weight, provenance instant and excluded name) stays
    in the project file and is reachable from the detail sheet. What belongs on a card is the name, the
    tier, whether a person has to look, and the two or three reasons that decide it. Nothing here is
    recomputed -- a prototype that scored the evidence on its own would be a second opinion nobody asked
    for, and one that disagreed with the backend in silence would be worse than no opinion at all.

    A project built before the report existed reports `available: false` rather than a zero: an absent
    field is not a low score.
    """
    report = recovery.get("attribution_confidence") or {}
    if not report:
        return {"available": False,
                "why": ("this project was built before the attribution-confidence report existed, so "
                        "nothing here can honestly say how much its names are worth")}
    estimate = report.get("winner_estimate") or {}
    reasons = [str(row.get("why") or "") for row in (report.get("negative_factors") or [])][:3]
    reasons += [str(row.get("why") or "") for row in (report.get("positive_factors") or [])][:2]
    if not reasons:
        # A candidate can be perfectly ordinary and still score low: nothing wrong at set level, every game
        # read off a nameplate with no crop behind it. The games' own factors are the answer then, and a
        # score with no reason beside it is the thing a reviewer cannot act on.
        reasons = ["%d of %d games: %s" % (row.get("count"), len((report.get("games") or [])),
                                           row.get("why"))
                   for row in (report.get("why_this_score") or []) if row.get("why")][:4]
    reasons = [_readable(row) for row in reasons if row]
    characters = recovery.get("character_bindings") or {}
    return {
        "available": True,
        "score": report.get("score"),
        "tier": report.get("tier"),
        "needs_review": bool(report.get("needs_review")),
        "identity_conflict": bool(report.get("identity_conflict")),
        # Which spellings this project's own name tolerance cannot place, per set. Reported as uncertainty:
        # not a second participant, not one participant, and a crop is what would settle it.
        "alias_uncertainty": [{"spellings": row.get("spellings"), "side": row.get("side"),
                               "similarity": row.get("similarity"), "relation": row.get("relation"),
                               "settled_by": row.get("settled_by")}
                              for row in (report.get("alias_uncertainty") or [])],
        "single_witness_capped": bool(report.get("single_witness_capped")),
        "independent_witnesses": report.get("independent_witnesses"),
        "authoritative_winner": recovery.get("set_winner"),
        "estimate": estimate.get("name"),
        # "decisive" is a claim about arithmetic only. It travels with the tier and the score or it becomes
        # the thing it was written to avoid: a name that looks settled because a word said so.
        "estimate_status": estimate.get("status"),
        "estimate_decisive": bool(estimate.get("decisive")),
        "estimate_conditional_because": estimate.get("conditional_because"),
        "estimate_why_not": estimate.get("why_not"),
        "estimate_source": estimate.get("source"),
        "estimate_wins": estimate.get("wins"),
        "agrees_with_authoritative": bool(estimate.get("agrees_with_authoritative")),
        "why_not": estimate.get("why_not"),
        "because": estimate.get("because"),
        "estimate_blockers": report.get("estimate_blockers") or [],
        "duplicates_quarantined": report.get("duplicates_quarantined") or [],
        "leader": estimate.get("leader"),
        "leader_wins": estimate.get("leader_wins"),
        "excluded_names": [{"name": row.get("name"), "because": row.get("because")}
                           for row in (estimate.get("excluded") or [])],
        "targets": estimate.get("targets"),
        "top_target": estimate.get("top_target"),
        "format_basis": (estimate.get("format") or {}).get("basis"),
        "ambiguous_format": (estimate.get("format") or {}).get("ambiguous"),
        "named_candidates": [str(name) for name in (report.get("named_candidates") or [])],
        "credits": report.get("credits") or {},
        "reasons": [row for row in reasons if row],
        # "no band reading for this game" repeated three times is one gap of a known size, and a card that
        # lists it three times reads like three separate problems.
        "unavailable": ["%s (%d games)" % (name, count) if count > 1 else name
                        for name, count in sorted(_tally(row.get("evidence")
                                                         for row in (report.get("unavailable_evidence") or [])
                                                         if row.get("evidence")).items())],
        "witness_note": report.get("witness_note") or "",
        "score_meaning": report.get("score_meaning") or "",
        "characters_side_bound": bool(characters.get("games_side_bound")),
        # Corrected display text, one entry per unique scoped reading: what the classifier wrote, what the
        # frame drew, which frame and its digest, and the role it actually played -- inside this candidate's
        # gameplay, only as the name carried in from before it, or on a versus screen where it supports
        # nothing. A reading of pixels is not an identity and not an acceptance, and these fields say so
        # rather than leaving the card to imply it.
        "display_text_observations": [
            {"at": row.get("at"), "side": row.get("side"), "row_text": row.get("row_text"),
             "observed_text": row.get("observed_text"), "frame_file": row.get("frame_file"),
             "frame_sha256": (row.get("frame_sha256") or "")[:12],
             "role": row.get("role"), "role_meaning": row.get("role_meaning"),
             "reference_count": row.get("reference_count"),
             "reviewed_by": row.get("reviewed_by"), "evidence_tier": row.get("evidence_tier"),
             "accepted_by": row.get("accepted_by"),
             "bound_participant_identity": bool(row.get("bound_participant_identity"))}
            for row in (report.get("display_text_observations") or [])],
        # Admitted by the loader and refused by the row it named. Its own field, so a card cannot render one
        # of these as a correction that happened.
        "display_text_correction_refusals": [
            {"at": row.get("at"), "side": row.get("side"), "row_text": row.get("row_text"),
             "declared_text": row.get("declared_text"), "observed_text": row.get("observed_text"),
             "why": row.get("why"), "frame_sha256": (row.get("frame_sha256") or "")[:12],
             "accepted_by": row.get("accepted_by"),
             "bound_participant_identity": bool(row.get("bound_participant_identity"))}
            for row in (report.get("display_text_correction_refusals") or [])],
        "characters_with_a_pair": characters.get("games_with_a_pair"),
        "games_observed": characters.get("games_observed"),
    }


class Data:
    """The draft output, projected into the small contract the UI consumes."""

    def __init__(self, manifest_path: Path, notes_path: Path, preview_dir=None):
        self.lock = threading.Lock()
        self.notes_path = notes_path
        self.preview_lock = threading.Lock()
        self.preview_dir = Path(preview_dir) if preview_dir else (
            PROJECT_ROOT / "build/work/bb9/mobile-review-poc/preview")
        try:                                     # created once so a request never races to make it
            self.preview_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.preview_dir = None
        manifest = read_json(manifest_path)
        self.manifest_path = manifest_path
        self.review_run = manifest.get("review_run")
        self.master = Path(manifest["source"])
        self.project_path = Path(manifest["project"])
        # Read once, here: this is the identity a reviewer compares against the manifest, so it has to be the
        # bytes actually serving, not a value some earlier run claimed.
        self.project_sha256 = (hashlib.sha256(self.project_path.read_bytes()).hexdigest()
                               if self.project_path.is_file() else None)
        project = read_json(self.project_path)
        self.duration = float(project.get("duration") or 0.0)
        matches = project.get("matches") or []

        self.clips = []
        self.media = {}                       # token -> absolute path
        self.master_token = "master"
        if self.master.is_file():
            self.media[self.master_token] = self.master.resolve()

        for job in manifest.get("jobs") or []:
            number = int(job["number"])
            clip_path = Path(job["path"])
            if not clip_path.is_file():
                continue
            cid = "set%02d" % number
            self.media[cid] = clip_path.resolve()
            receipt = {}
            try:
                receipt = read_json(job["receipt"])
            except (OSError, ValueError, KeyError):
                receipt = {}
            stills = {}
            for check in job.get("checks") or []:
                image = Path(check.get("image") or "")
                if image.is_file():
                    key = "%s_%s" % (cid, check.get("position"))
                    self.media[key] = image.resolve()
                    stills[str(check.get("position"))] = key
            match = matches[number - 1] if number - 1 < len(matches) else {}
            recovery = match.get("recovery") or {}
            hypotheses = recovery.get("nameplate_hypotheses") or {}
            bound = ((match.get("review") or {}).get("machine_bounds") or [{}])[0]
            evidence = bound.get("evidence") or {}
            container = ((job.get("container") or {}).get("format") or {})
            requested_start = float(job["requested_start"])
            requested_end = float(job["requested_end"])
            self.clips.append({
                "id": cid,
                "number": number,
                "requested": {"start": requested_start, "end": requested_end,
                              "duration": round(requested_end - requested_start, 3)},
                "delivered": {
                    "container_seconds": round(float(container.get("duration") or 0.0), 3),
                    "bytes": clip_path.stat().st_size,
                    "mbytes_per_second": round(clip_path.stat().st_size / 1e6
                                               / max(0.001, float(container.get("duration") or 1.0)), 2),
                    "length_delta_seconds": round(float(job.get("duration_delta_seconds") or 0.0), 3),
                    "mode": receipt.get("effective_cut_mode") or "unknown",
                    "start_is_exact": bool(receipt.get("frame_exact_start_intended")),
                    "source_range_verified": bool(receipt.get("delivered_source_range_verified")),
                    "note": ("stream copy may include surrounding footage before the planned start"
                             if not receipt.get("frame_exact_start_intended")
                             else "re-encode intends the planned frame"),
                },
                "media": {"clip": "media/file/" + cid,
                          "poster": ("media/file/" + stills["first"]) if stills.get("first") else None,
                          "lastStill": ("media/file/" + stills["last"]) if stills.get("last") else None},
                # The recovery layer withholds a *verdict*, which is right -- but it recorded a great deal of
                # lower-trust evidence alongside it (nameplate hypotheses, observed characters, the attributed
                # win tally). Showing none of that made a reviewed set look empty, so a reviewer could not
                # tell "nothing was found" from "found, but not enough to name a winner".
                "identity": {
                    "player1": match.get("player1") or "",
                    "player2": match.get("player2") or "",
                    "round": match.get("round_name") or "",
                    "confirmed": bool((match.get("player1") or "") and (match.get("player2") or "")),
                    "withheld": recovery.get("attribution_withheld") or "",
                    "hypotheses": sorted({name for row in (hypotheses.get("games") or [])
                                          for name in (row.get("sides") or {}).values() if name}),
                    "hypothesis_note": hypotheses.get("trust_note") or "",
                    "characters": sorted({c for game in (recovery.get("games") or [])
                                          for c in (game.get("characters_observed") or [])}),
                    "characters_side_bound": any((game.get("characters_side_bound")
                                                  for game in (recovery.get("games") or []))),
                    "attributed_wins": [{"name": row.get("name"), "wins": row.get("wins")}
                                        for row in (hypotheses.get("possible_winners") or [])],
                    "games_with_winner": sum(1 for game in (recovery.get("games") or [])
                                             if game.get("winner")),
                    "games_total": len(recovery.get("games") or []),
                    # The tier beside the names, not underneath them: which name is estimated, from which
                    # route, how much the backend thinks that is worth, and what it says is still missing.
                    "confidence": confidence_summary(recovery),
                },
                "evidence": {
                    "startBasis": bound.get("basis") or "",
                    "onsetUncertaintySeconds": evidence.get("active_onset_uncertainty_seconds"),
                    "endBasis": recovery.get("clip_end_basis") or "",
                    "endUncertainty": recovery.get("clip_uncertainty") or {},
                    "missing": recovery.get("missing_evidence") or [],
                    "winner": recovery.get("set_winner"),
                },
                "status": job.get("status") or "",
            })

        self.limitations = list(manifest.get("limitations") or [])
        self.provisional = bool(manifest.get("provisional"))
        self.accepted = bool(manifest.get("operator_accepted"))
        self.validated = bool(manifest.get("independently_validated"))
        self.gaps = union_gaps([(c["requested"]["start"], c["requested"]["end"]) for c in self.clips],
                               self.duration)
        claimed = sum(end - start for start, end in self.gaps["intervals"])
        self.coverage = {"claimed_seconds": round(claimed, 3),
                         "unclaimed_seconds": round(max(0.0, self.duration - claimed), 3),
                         "fraction": round(claimed / self.duration, 4) if self.duration else 0.0,
                         "method": "union of requested intervals, not a sum of clip lengths"}

    def preview_path(self, cid):
        """Where a clip's low-bitrate preview would live, or None if previews cannot be written here."""
        if not self.preview_dir or not re.match(r"^[A-Za-z0-9_]+$", cid or ""):
            return None
        return self.preview_dir / ("%s.preview.mp4" % cid)

    def preview_paths(self, cid):
        """(finished, in-progress) for one clip's preview.

        The temporary file must still end in .mp4: libavformat chooses the muxer from the extension, and a
        bare `.part` name makes the encoder fail with "Error initializing the muxer ... Invalid argument".
        """
        final = self.preview_path(cid)
        if final is None:
            return None, None
        return final, final.with_name(final.stem + ".part.mp4")

    def preview_info(self, cid):
        path, part = self.preview_paths(cid)
        if path is None:
            return {"state": "unavailable"}
        if path.is_file():
            size = path.stat().st_size
            row = next((c for c in self.clips if c["id"] == cid), None)
            seconds = (row or {}).get("delivered", {}).get("container_seconds") or 0
            return {"state": "ready", "media": "media/file/prev_" + cid, "bytes": size,
                    "mbytes_per_second": round(size / 1e6 / seconds, 2) if seconds else None}
        if part.exists():
            return {"state": "generating"}
        if (path.parent / (path.name + ".error")).exists():
            return {"state": "failed"}
        return {"state": "none"}

    def state(self):
        return {"kind": "tvh-mobile-review-poc", "schema_version": 1,
                "build": build_id(),
                "review_run": self.review_run,
                # The project's own digest, computed once at load: a reviewer checking "which artifact is this
                # phone actually showing?" needs something to compare against the manifest, and a file name is
                # not it -- four revisions all call their package bb9-*-linked.tvh.json.
                "provenance": {"draft_manifest": self.manifest_path.name,
                               "project": self.project_path.name,
                               "project_sha256": self.project_sha256,
                               "provisional": self.provisional, "operator_accepted": self.accepted,
                               "independently_validated": self.validated,
                               "notes_are_mock_edits": True},
                "source": {"duration_seconds": round(self.duration, 3),
                           "bytes": self.master.stat().st_size if self.master.is_file() else 0,
                           "mbytes_per_second": round(self.master.stat().st_size / 1e6
                                                      / max(0.001, self.duration), 2)
                           if self.master.is_file() else None,
                           "media": {"master": "media/file/" + self.master_token
                                     if self.master_token in self.media else None},
                           "limitations": self.limitations},
                "coverage": self.coverage, "gaps": self.gaps["gaps"],
                # Each clip carries its own preview state, because a preview is made lazily and the phone
                # needs to know whether "Smooth" is ready before it offers it.
                "clips": [dict(row, preview=self.preview_info(row["id"])) for row in self.clips],
                "actions": ["good", "adjust", "inaccurate", "unsure", "missing"],
                "nudges_seconds": [1, 5]}

    def notes(self):
        with self.lock:
            if not self.notes_path.is_file():
                return {"kind": "tvh-mobile-review-notes-mock", "schema_version": 1, "revision": 0,
                        "provenance": provenance(self), "items": {}, "missing_clips": []}
            try:
                return read_json(self.notes_path)
            except (ValueError, OSError):
                return {"kind": "tvh-mobile-review-notes-mock", "schema_version": 1, "revision": 0,
                        "provenance": provenance(self), "items": {}, "missing_clips": [],
                        "warning": "stored notes were unreadable; nothing was overwritten"}

    def save_notes(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("notes must be a JSON object")
        if len(json.dumps(payload)) > MAX_NOTES_BYTES:
            raise ValueError("notes too large for a mock file")
        if not isinstance(payload.get("items"), dict):
            raise ValueError("notes.items must be an object")
        with self.lock:
            current = 0
            if self.notes_path.is_file():
                try:
                    current = int(read_json(self.notes_path).get("revision") or 0)
                except (ValueError, OSError):
                    current = 0
            payload["revision"] = current + 1
            payload["kind"] = "tvh-mobile-review-notes-mock"
            payload["schema_version"] = 1
            payload["provenance"] = provenance(self)
            self.notes_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.notes_path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temp, self.notes_path)
            return payload


def provenance(data):
    return {"draft_manifest": data.manifest_path.name, "project": data.project_path.name,
            "provisional": True, "operator_accepted": False, "independently_validated": False,
            "mock_edits_only": True,
            "review_run": ({key: data.review_run[key] for key in ("id", "round", "artifact_sha256")}
                           if data.review_run else None),
            "note": "these notes live in the PoC scratch directory and change no recovery file"}


def union_gaps(intervals, duration):
    """Complement of the union, computed on merged intervals so overlaps are never double counted."""
    merged = []
    for start, end in sorted((float(a), float(b)) for a, b in intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor > 0.5:
            gaps.append({"start": round(cursor, 3), "end": round(start, 3),
                         "seconds": round(start - cursor, 3)})
        cursor = max(cursor, end)
    if duration and duration - cursor > 0.5:
        gaps.append({"start": round(cursor, 3), "end": round(duration, 3),
                     "seconds": round(duration - cursor, 3)})
    return {"intervals": [[round(a, 3), round(b, 3)] for a, b in merged],
            "gaps": [g for g in gaps if g["start"] < duration]}


class Handler(BaseHTTPRequestHandler):
    server_version = "TVHReviewPoC/1"
    protocol_version = "HTTP/1.1"
    data = None

    def log_message(self, *args):
        pass                                     # a prototype should not spam a terminal

    def log_media_open(self, path, total):
        print("%-8s opening %8.1f MB  %s" % ("MEDIA", total / 1_048_576, path), flush=True)

    def log_media_progress(self, path, sent, total, seconds):
        rate = sent / seconds / 1_048_576 if seconds else 0.0
        print("%-8s %6.1f of %6.1f MB in %5.1f s = %5.2f MB/s  %s"
              % ("MEDIA", sent / 1_048_576, total / 1_048_576, seconds, rate, path), flush=True)

    def log_media(self, path, sent, seconds, complete):
        """One line per media response, so a buffering complaint can be diagnosed from the server.

        Rate and whether the client cut the connection short are the two facts that separate "the pipe is too
        slow for this file" from "the browser is asking and giving up", and neither is visible from a status
        code. Written to the server's own stdout, which tmux tees into the PoC scratch log.
        """
        rate = (sent / seconds / 1_048_576) if seconds and seconds > 0 else 0.0
        print("%-8s %10.1f MB  %6.2f MB/s  %s  %s%s"
              % ("MEDIA", sent / 1_048_576, rate, "complete" if complete else "ABORTED", path,
                 "" if complete else "  <- client stopped early"), flush=True)

    # -- responses ---------------------------------------------------------------
    def send_bytes(self, body, ctype="application/json", status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, status, reason):
        body = json.dumps({"error": reason}).encode()
        self.send_bytes(body, status=status)

    def resolve_media(self, token):
        table = self.data.media
        if token in table:
            return table[token]
        if token.startswith("prev_"):                       # previews are made after startup, so re-check
            candidate = self.data.preview_path(token[5:])
            return candidate if candidate and candidate.is_file() else None
        return None

    # -------------------------------------------------------------- low-bitrate preview
    def start_preview(self, cid):
        """Transcode one draft to 720p/3 Mbit/s so a phone can watch it without a 11 MB/s stream.

        Per clip and only when asked: the drafts exist once, at archive rate, and 54 GB of them must not be
        re-encoded to answer a review question. A `.part` file is what "generating" means, so a second request
        cannot start a second encoder and a crashed one leaves evidence rather than a truncated mp4.
        """
        data = self.data
        clip = next((row for row in data.clips if row["id"] == cid), None)
        if not clip:
            return self.fail(404, "no such clip")
        source = data.media.get(cid)
        destination, part = data.preview_paths(cid)
        if destination is None:
            return self.fail(500, "preview directory is not writable")
        if destination.is_file():
            return self.send_bytes(json.dumps({"clip": cid, "state": "ready",
                                               "media": "media/file/prev_" + cid}).encode())
        with data.preview_lock:
            if part.exists():
                return self.send_bytes(json.dumps({"clip": cid, "state": "generating"}).encode())
            command = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
                       "-vf", "scale=1280:720:flags=bicubic", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "26", "-maxrate", "3M", "-bufsize", "6M", "-pix_fmt", "yuv420p", "-g", "150",
                       "-c:a", "aac", "-b:a", "96k", "-f", "mp4", "-movflags", "+faststart", "-y", str(part)]
            stale = destination.parent / (destination.name + ".error")
            stale.unlink(missing_ok=True)

            def worker(part=part, target=destination, run=command):
                try:
                    done = subprocess.run(run, capture_output=True, text=True, timeout=7200,
                                          stdin=subprocess.DEVNULL)
                    if done.returncode == 0 and part.is_file():
                        os.replace(part, target)
                    else:
                        part.unlink(missing_ok=True)
                        (target.parent / (target.name + ".error")).write_text(
                            (done.stderr or "ffmpeg failed")[-2000:], encoding="utf-8")
                except (OSError, subprocess.SubprocessError):
                    part.unlink(missing_ok=True)

            destination.parent.mkdir(parents=True, exist_ok=True)
            threading.Thread(target=worker, daemon=True).start()
        return self.send_bytes(json.dumps({"clip": cid, "state": "generating"}).encode())

    def preview_state(self, cid):
        data = self.data
        destination, part = data.preview_paths(cid)
        if destination is None:
            return self.send_bytes(json.dumps({"clip": cid, "state": "unavailable"}).encode())
        if destination.is_file():
            return self.send_bytes(json.dumps({"clip": cid, "state": "ready",
                                               "media": "media/file/prev_" + cid,
                                               "bytes": destination.stat().st_size}).encode())
        if part.exists():
            return self.send_bytes(json.dumps({"clip": cid, "state": "generating"}).encode())
        failure = destination.parent / (destination.name + ".error")
        if failure.is_file():
            detail = failure.read_text(encoding="utf-8")[-300:]
            return self.send_bytes(json.dumps({"clip": cid, "state": "failed", "detail": detail}).encode())
        return self.send_bytes(json.dumps({"clip": cid, "state": "none"}).encode())

    def reject_range(self, size):
        self.send_response(416)
        self.send_header("Content-Range", "bytes */%d" % size)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_file(self, path, download_name=None, ctype=None, cache="public, max-age=3600"):
        try:
            size = path.stat().st_size
        except OSError:
            return self.fail(404, "not found")
        media_type = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        headers = {"Accept-Ranges": "bytes"}
        if download_name:
            headers["Content-Disposition"] = 'attachment; filename="%s"' % download_name
        start, end = 0, size - 1
        status = 200
        raw = self.headers.get("Range")
        if raw:
            match = RANGE_RE.match(raw.strip())
            if not match or (not match.group(1) and not match.group(2)):
                return self.reject_range(size)
            first, last = match.groups()
            if first == "":
                length = min(int(last), size)
                start, end = size - length, size - 1
            else:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
                if not last and end - start + 1 > MAX_WINDOW:
                    # An open-ended range asks for the remaining 5.5 GB. Chrome issues several of these at
                    # once while probing a large file, and each one holds a socket and a share of the link
                    # for the rest of the session. Closing after a bounded window is a legal 206 and lets the
                    # client ask for the next piece when it actually wants it.
                    end = start + MAX_WINDOW - 1
            if start > end or start >= size:
                return self.reject_range(size)
            headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command == "HEAD":
            return
        started = time.monotonic()
        sent = 0
        complete = False
        mark = started
        total = end - start + 1
        # Progress is reported while the response is live, not only at the end. A reviewer who watches for
        # thirty seconds and sees a spinner leaves a stream that never finishes, and a log written only on
        # completion records nothing about the one case that matters.
        self.log_media_open(self.path, total)
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = total
                while remaining > 0:
                    block = handle.read(min(CHUNK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    self.wfile.flush()
                    sent += len(block)
                    remaining -= len(block)
                    now = time.monotonic()
                    if now - mark >= 2.0:
                        self.log_media_progress(self.path, sent, total, now - started)
                        mark = now
            complete = True
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass                                 # a phone locking its screen is normal
        finally:
            if "media/" in (self.path or "") and sent:
                self.log_media(self.path, sent, time.monotonic() - started, complete)

    def speed_test(self, megabytes):
        """Stream N MB of incompressible padding so a phone can report its real sustained rate.

        The draft clips move at about 11 MB/s, which is why playback is the question in the first place; this
        measures the path, not the player, and writes nothing. Bytes are pseudo-random so no middlebox can
        collapse them, and the block is generated once and reused.
        """
        total = clamp(megabytes, 1, 200) * 1_048_576
        block = bytes((index * 7 + index // 31) & 0xFF for index in range(1 << 16))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if self.command == "HEAD":
            return
        started = time.monotonic()
        sent = 0
        try:
            while sent < total:
                chunk = block[:min(len(block), total - sent)]
                self.wfile.write(chunk)
                self.wfile.flush()
                sent += len(chunk)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            self.log_media("speedtest %d MB requested" % megabytes, sent, time.monotonic() - started,
                           sent == total)

    # -- routing -----------------------------------------------------------------
    def route(self, method):
        path = strip_prefix(unquote(urlparse(self.path).path))
        try:
            if path in ("/", "/index.html"):
                return self.serve_file(HERE / "index.html", ctype="text/html; charset=utf-8",
                                          cache="no-store")
            if path == "/app.js":
                return self.serve_file(HERE / "app.js", ctype="text/javascript; charset=utf-8",
                                   cache="no-store")
            if path == "/styles.css":
                return self.serve_file(HERE / "styles.css", ctype="text/css; charset=utf-8",
                                    cache="no-store")
            if path == "/api/state":
                return self.send_bytes(json.dumps(self.data.state()).encode())
            if path == "/api/notes":
                if method == "GET":
                    return self.send_bytes(json.dumps(self.data.notes()).encode())
                if method == "PUT":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length <= 0 or length > MAX_NOTES_BYTES:
                        return self.fail(413, "notes body must be between 1 and %d bytes" % MAX_NOTES_BYTES)
                    try:
                        payload = json.loads(self.rfile.read(length).decode("utf-8"))
                        saved = self.data.save_notes(payload)
                    except (ValueError, UnicodeDecodeError) as exc:
                        return self.fail(400, str(exc))
                    return self.send_bytes(json.dumps(saved).encode())
                if method == "POST":
                    with self.data.lock:
                        if self.data.notes_path.is_file():
                            self.data.notes_path.unlink()
                    return self.send_bytes(json.dumps(self.data.notes()).encode())
                return self.fail(405, "method not allowed")
            if path == "/api/clientstat":
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 4096:
                    return self.fail(400, "bad clientstat body")
                try:
                    note = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self.fail(400, "clientstat must be JSON")
                # The browser's own measurement is the only one that covers the last hop, and that segment
                # cannot be reached from this machine. Reporting it here means a buffering question gets
                # answered from the log instead of by asking the reviewer to read a number off a phone.
                print("CLIENT  %s" % json.dumps(note, sort_keys=True), flush=True)
                return self.send_bytes(b'{"ok": true}')
            if path == "/api/speedtest":
                query = urlparse(self.path).query
                wanted = 48
                for pair in query.split("&"):
                    if pair.startswith("mb="):
                        try:
                            wanted = int("".join(character for character in pair[3:] if character.isdigit())
                                         or 48)
                        except ValueError:
                            wanted = 48
                return self.speed_test(wanted)
            if path.startswith("/api/preview/"):
                cid = path[len("/api/preview/"):].strip("/")
                if not re.match(r"^[A-Za-z0-9_]+$", cid):
                    return self.fail(400, "bad clip id")
                if method == "POST":
                    return self.start_preview(cid)
                if method == "GET":
                    return self.preview_state(cid)
                return self.fail(405, "method not allowed")
            if path.startswith("/media/file/"):
                token = path[len("/media/file/"):].strip("/")
                if not re.match(r"^[A-Za-z0-9_]+$", token):
                    return self.fail(400, "bad media token")
                resolved = self.resolve_media(token)
                if not resolved:
                    return self.fail(404, "unknown media")
                return self.serve_file(resolved)
            return self.fail(404, "nothing here")
        except OSError as exc:
            return self.fail(500, "server error: %s" % type(exc).__name__)

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_PUT(self):
        self.route("PUT")

    def do_POST(self):
        self.route("POST")


class PoCServer(ThreadingHTTPServer):
    daemon_threads = True
    # Chrome holds several range reads open at once on a big file. The inherited backlog of 5 means a later
    # request waits behind long-lived streams instead of being served, which looks to a viewer exactly like a
    # slow network.
    request_queue_size = 128


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "build/work/bb9/draft-split-2026-11-08"
                                                  / "manifest.json"))
    parser.add_argument("--notes", default=str(PROJECT_ROOT / "build/work/bb9/mobile-review-poc/notes.json"))
    parser.add_argument("--previews", default=str(PROJECT_ROOT / "build/work/bb9/mobile-review-poc/preview"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    manifest = Path(args.manifest)
    if not manifest.is_file():
        raise SystemExit("draft manifest not found at %s" % manifest)
    Handler.data = Data(manifest, Path(args.notes))
    state = Handler.data.state()
    print("serving http://%s:%d/  clips=%d  duration=%.1fs  coverage=%.1f%%  media tokens=%d"
          % (args.host, args.port, len(state["clips"]), state["source"]["duration_seconds"],
             100.0 * state["coverage"]["fraction"], len(Handler.data.media)))
    httpd = PoCServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
