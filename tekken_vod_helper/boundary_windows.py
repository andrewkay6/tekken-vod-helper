"""Automatic decision-window selection from saved clock and round-event evidence.

Four contracts, each learned the expensive way:

* **The frame manifest is the timestamp source of truth.** Selection returns the manifest's own id, path
  and timestamp. Reconstructing time as ``index * 1.000986`` differs from the manifest by ~0.015 s in
  this interval and up to ~0.097 s across the cache, and cannot express a missing frame at all.
* **A round intro is not a game opening.** ``stage_intro`` plus a live clock happens before ordinary
  rounds inside a game (98.10 s and 129.13 s are mid-game in already-validated evidence), so openings
  are returned as uncorroborated hypotheses and never bound an interval.
* **A post-event probe must be after the end *evidence*.** The last readable clock is not the round end;
  a K.O. seconds later is. Anchors prefer observed events and carry their uncertainty bounds.
* **Unknown truth is ungraded, not absent.** Nothing here supplies a label.

No pixel threshold decides a boundary: byte-hash stasis and clock-crop MAD were both tried for locating
the freeze and both failed, because the K.O. that stops the clock also throws light across the digits.
"""
import bisect
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tekken_vod_helper import boundary_games, boundary_names, boundary_pips

DEFAULT_MANIFEST = "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
CLOCK_RISE_TOLERANCE = getattr(boundary_games, "ROUND_TICK_TOLERANCE", 5)
# An annotation's timestamp has to be this frame's own sampled instant, within 0.05 s -- a twentieth of the
# cache's 1.000986 s spacing, which is what makes it unambiguous rather than lenient: no neighbouring frame
# is anywhere near that close, so a claim inside this window can only name the frame it names. It is not
# zero, and not "one frame's spacing" either; a drift the size of a frame is the failure being refused, and
# the drift this batch actually caught was 1-3 s (a follow-up window extrapolated from the window beside it,
# which the merge would have filed at instants that never drew those pip rows).
#
# Not zero, deliberately: this corpus's own accepted annotations round timestamps to milliseconds (frame
# 56 is sampled at 55.054997 s and written as 55.05), and the join downstream keys rows at three decimals.
# Demanding microsecond equality would refuse the files that produced every accepted result in this project
# while a 0.005 s disagreement about a 1 Hz frame changes nothing. Drift the size of a frame does.
ANNOTATION_TIME_TOLERANCE = 0.05
PIP_TARGET_DEFAULT = 3
ROUND_OPENING_STATES = ("vs_screen", "stage_intro")
# Candidates, not assertions: none is claimed to be glare-free or HUD-bearing. That is what the answer decides.
END_PROBE_OFFSETS = (0.0, 2.0, 4.0)
END_PROBE_ROLES = ("round_end_evidence_frame", "after_end_plus_2s", "after_end_plus_4s")


class FrameIndex:
    """The cache's own frame records, looked up by real time rather than by arithmetic."""

    def __init__(self, frames):
        # type: (Sequence[Dict[str, Any]]) -> None
        self.frames = sorted(frames, key=lambda f: float(f["timestamp"]))
        if not self.frames:
            raise ValueError("frame manifest lists no frames")
        self.times = [float(f["timestamp"]) for f in self.frames]

    @classmethod
    def load(cls, manifest=DEFAULT_MANIFEST):
        # type: (str) -> "FrameIndex"
        path = Path(manifest)
        if not path.is_file():
            raise ValueError("frame manifest missing; refusing to reconstruct timestamps "
                             "arithmetically: {}".format(path))
        return cls(json.loads(path.read_text(encoding="utf-8"))["frames"])

    def nearest(self, timestamp):
        # type: (float) -> Dict[str, Any]
        position = bisect.bisect_left(self.times, timestamp)
        best = None
        for candidate in (position - 1, position, position + 1):
            if 0 <= candidate < len(self.frames):
                delta = abs(self.times[candidate] - timestamp)
                if best is None or delta < best[0]:
                    best = (delta, self.frames[candidate])
        return best[1]

    def at_or_after(self, timestamp, tolerance=0.5):
        # type: (float, float) -> Optional[Dict[str, Any]]
        """First frame no earlier than ``timestamp``, or None at the end of the cache.

        Refuses to fall back to an earlier frame: a candidate that predates the event it is meant to
        follow is not evidence about that event.
        """
        position = bisect.bisect_left(self.times, timestamp - tolerance)
        for index in range(max(position, 0), len(self.frames)):
            if self.frames[index]["timestamp"] >= timestamp - tolerance:
                return self.frames[index]
        return None

    def window(self, start, end):
        # type: (float, float) -> List[Dict[str, Any]]
        return [f for f in self.frames if start <= f["timestamp"] < end]


def is_source_framing(value):
    # type: (Any) -> bool
    """Whether an annotation document or entry declares master-resolution framing.

    Read off the document the source builder writes (`version: "source-crops-v1"`, or a `framing` string
    that says native resolution) rather than off a filename pattern: a decoded master frame is not a cache
    frame, and looking for it in the cache index would refuse a legitimate run -- while treating every
    unfindable path as source framing would let a mistyped cache path through unverified.
    """
    text = str(value or "").lower()
    return "source-crops" in text or "native resolution" in text or text.strip() == "source"


def verify_annotations(entries, manifest=DEFAULT_MANIFEST, framing="cache"):
    # type: (Sequence[Dict[str, Any]], str, str) -> Dict[str, Any]
    """Check every annotation's frame/timestamp pair against the index that governs it. Before sending.

    A hand-written or half-regenerated ``annotations-*.json`` can name a real frame and carry a timestamp
    that belongs to a neighbour -- extrapolated from a window it was copied from, or from an arithmetic
    step over a 1 Hz cache whose spacing is 1.000986 s and not 1.0. Nothing downstream would notice: the
    crop renders from the named file, the model answers about those pixels, and the reading is then *joined
    by timestamp*, which is exactly how one trial put game 1's frames inside game 2's replay. A pip reading
    whose instant is wrong by two seconds is not a slightly noisy reading; it is evidence about a different
    moment, and it will be merged as though it were about this one.

    So cache frames are checked against the manifest that indexes the cache, and a mismatch stops the run
    rather than being reported beside it. Three things are refused outright, each named with the value found
    and the value claimed:

    * a cache frame the manifest does not list -- the manifest is the index of what was watched, so an
      unlisted path is either a hand-typed filename or a manifest older than the folder beside it;
    * a timestamp that is not this frame's time within ``ANNOTATION_TIME_TOLERANCE``;
    * an entry with no frame or no timestamp, which no downstream guard can tell from an unlabelled frame.

    Native-source records (``framing`` declaring master decoding) are supported and checked by their own,
    **weaker** rule: the file exists and the timestamp is a finite number. They are deliberately not looked
    up in the cache index, because a master decode is not a cache frame, and their identity is never
    *reconstructed* from a frame id either. That means a source-framed answer is verified to be about a
    file that is there, **not** verified against an index of sampled instants -- so ``identity_proof`` says
    which kind of check ran, and no caller may report the weaker one as the stronger.
    """
    problems = []      # type: List[str]
    index, by_path, by_folder_name = None, {}, {}
    cache_checked = source_checked = 0
    document_is_source = is_source_framing(framing)
    for position, entry in enumerate(entries):
        frame = str(entry.get("frame") or "")
        stamp = entry.get("timestamp")
        label = "annotation %d (%s)" % (position, Path(frame).name or "no frame path")
        if not frame:
            problems.append("%s: names no frame file" % label)
            continue
        if stamp is None:
            problems.append("%s: names %s but carries no timestamp, which is not the same as an "
                            "unlabelled frame -- nothing can join this reading to an instant"
                            % (label, frame))
            continue
        if document_is_source or is_source_framing(entry.get("framing")):
            if not Path(frame).is_file():
                problems.append("%s: a source-framed record whose file is not on disk" % label)
            elif not isinstance(stamp, (int, float)) or not math.isfinite(float(stamp)):
                problems.append("%s: a source-framed record with a non-finite timestamp %r"
                                % (label, stamp))
            else:
                source_checked += 1
            continue
        if index is None:
            index = FrameIndex.load(manifest)
            # Compared resolved, because the same frame arrives as ``build/work/.../frame-3245.jpg`` from a
            # manifest and as an absolute path from a rendered request; a string mismatch is not evidence
            # that nobody indexed the file.
            by_path = {str(Path(str(record.get("path"))).resolve()): record for record in index.frames}
            by_folder_name = {(str(Path(str(record.get("path"))).parent.resolve()),
                               Path(str(record.get("path"))).name): record
                              for record in index.frames}
        resolved = Path(frame).resolve()
        record = by_path.get(str(resolved)) or by_folder_name.get((str(resolved.parent), resolved.name))
        if record is None:
            problems.append("%s: %s is not listed in %s, so no index says when this frame was sampled. If "
                            "these are master decodes rather than cache frames, the annotation has to say "
                            "so (version source-crops-v1 or a framing that names native resolution)"
                            % (label, frame, manifest))
            continue
        claimed, listed = float(stamp), float(record["timestamp"])
        if abs(claimed - listed) > ANNOTATION_TIME_TOLERANCE:
            problems.append("%s: claims %.6f s but the manifest records frame %s at %.6f s (drift %.6f s). "
                            "Fix the annotation to name the frame it means; do not bend the index or the "
                            "timestamp to make a plan fit"
                            % (label, claimed, record.get("id"), listed, claimed - listed))
        elif entry.get("id") is not None and int(entry["id"]) != int(record.get("id", entry["id"])):
            problems.append("%s: declares frame id %s while the manifest gives that path id %s"
                            % (label, entry["id"], record.get("id")))
        else:
            cache_checked += 1
    return {"entries": len(entries), "verified_against_manifest": cache_checked,
            "verified_native_source": source_checked, "problems": problems,
            "manifest": str(manifest),
            "tolerance_seconds": ANNOTATION_TIME_TOLERANCE,
            # Named separately because the two checks are not equally strong, and a run that mixed them must
            # not be able to describe its own identity guarantee as the cache-grade one.
            "identity_proof": {"cache_frames": "listed in the cache index at this frame's own timestamp",
                               "native_source": "declared source framing, file present, finite timestamp "
                                                "-- NOT an index of sampled instants"},
            "inference_sent": False}


def load_saved_readings(run_dirs):
    # type: (Sequence[str]) -> Dict[str, Dict[str, Any]]
    """Crop answers on disk, keyed by frame path so a reused reading can never be re-sent."""
    readings = {}
    for directory in run_dirs:
        store = Path(directory) / "crops" / "groups"
        if not store.is_dir():
            continue
        for path in sorted(store.glob("*.json")):
            group = json.loads(path.read_text(encoding="utf-8"))
            if group.get("arm") != "crops" or group.get("errors"):
                continue
            # A source-framing answer is a different picture of the same instant, so it cannot satisfy a
            # cache-framing request: counting it as answered would drop the frame the window needs while
            # making the plan look free. Reuse is only ever within one framing.
            if not boundary_pips.is_cache_framing(group):
                continue
            by_label = {o["label"]: o for o in group.get("observations") or []}
            if not {"LEFT", "RIGHT"} <= set(by_label):
                continue
            drawn = [by_label[s].get("drawn") for s in ("LEFT", "RIGHT")]
            readings[group["frame"]] = {
                "timestamp": group.get("timestamp"),
                "win_dots": [by_label[s].get("lit") for s in ("LEFT", "RIGHT")],
                "drawn": drawn,
                "row_drawn": all(d is True for d in drawn),
                "unknown_sides": [s for s, d in zip(("LEFT", "RIGHT"), drawn) if d is None],
                "from_group": str(path)}
    return readings


def _open_window(at, basis):
    # type: (float, str) -> Dict[str, Any]
    return {"opened_at": at, "basis": [basis], "end_at": None, "end_basis": None,
            "clock_before": None, "clock_after": None, "event_at": None, "event_kind": None,
            "opening_at": None, "last_clock": None, "last_clock_at": None,
            "quarantined_clocks": [], "gaps": []}


def _close_window(window, opening_row):
    # type: (Dict[str, Any], Optional[Dict[str, Any]]) -> None
    event_at, clock_at = window.get("event_at"), window.get("last_clock_at")
    if event_at is not None:
        window["end_at"] = event_at
        window["end_basis"] = window.get("event_kind") or "round_end_event"
    elif clock_at is not None:
        window["end_at"] = clock_at
        window["end_basis"] = "last_readable_clock"
    else:
        window["end_at"], window["end_basis"] = None, "no_end_evidence"
    if opening_row is not None:
        window["clock_after"] = opening_row.get("round_clock")
        window["opening_at"] = opening_row["timestamp"]
    known = [v for v in (clock_at, event_at) if v is not None]
    window["evidence_bounds"] = {
        "last_clock_before_end": window.get("last_clock"), "last_clock_time": clock_at,
        "event_time": event_at, "uncertain_from": min(known) if known else None,
        "uncertain_to": window.get("opening_at") or (max(known) if known else None)}


def _gap_notes(window, is_last):
    # type: (Dict[str, Any], bool) -> List[str]
    gaps = []
    if window["end_basis"] == "last_readable_clock":
        gaps.append("the end is bounded only by the last readable clock: the true K.O. may fall after "
                    "every anchor available here, between it and the next round opening")
    if window["end_basis"] == "no_end_evidence":
        gaps.append("no clock and no round-end event before the next opening: this ending cannot be "
                    "localised from the saved facts")
    if window["quarantined_clocks"]:
        gaps.append("%d clock reading(s) contradicted the countdown and were quarantined, not used as "
                    "boundaries" % len(window["quarantined_clocks"]))
    if window["basis"] == ["first_observation"]:
        gaps.append("opens on the first observed frame: the round may already have been in progress, so "
                    "the game may be partial at this end")
    if window.get("clock_before") is None and window.get("last_clock") is None:
        gaps.append("no clock value was recorded in this window")
    if is_last:
        gaps.append("no later round is observed inside the interval: this window is cut by the interval "
                    "bound, which says nothing about where the recording ends")
    return gaps


def round_ending_windows(observations, frames, pip_target=PIP_TARGET_DEFAULT):
    # type: (Sequence[Dict[str, Any]], FrameIndex, int) -> List[Dict[str, Any]]
    """Round-end hypotheses from saved clock rises and round-end events, with what they cannot settle.

    The tracker's reading rules are reused, including ``clock_contradiction``: a clock that fell faster
    than the recording advanced is a bad reading and is quarantined rather than becoming a boundary.
    """
    rows = sorted(observations, key=lambda o: o["timestamp"])
    if not rows:
        return []
    windows = []
    current = _open_window(rows[0]["timestamp"], "first_observation")
    last_clock_row = None
    for row in rows:
        clock, previous = row.get("round_clock"), (last_clock_row or {}).get("round_clock")
        bad = False
        if clock is not None and last_clock_row is not None and previous is not None:
            bad = bool(boundary_games.clock_contradiction(previous, last_clock_row["timestamp"], clock,
                                                          row["timestamp"]))
            if bad:
                current["quarantined_clocks"].append({"at": row["timestamp"], "clock": clock,
                                                      "after": previous})
        if clock is not None and previous is not None and not bad and \
                clock > previous + CLOCK_RISE_TOLERANCE:
            current["clock_before"] = previous
            _close_window(current, row)
            windows.append(current)
            current = _open_window(row["timestamp"], "round_opening_from_rise")
        elif row.get("round_end") and not current.get("event_at"):
            current["event_at"] = row["timestamp"]
            current["event_kind"] = str(row["round_end"])
            current["basis"].append(str(row["round_end"]))
        if clock is not None and not bad:
            last_clock_row = row
            current["last_clock"], current["last_clock_at"] = clock, row["timestamp"]
    if current["basis"] != ["first_observation"] or current["last_clock"] is not None:
        _close_window(current, None)
        windows.append(current)
    for position, window in enumerate(windows):
        window["sequence"] = position
        window["pip_target"] = pip_target
        following = windows[position + 1] if position + 1 < len(windows) else None
        window["next_opening_at"] = (following or {}).get("opened_at")
        window["gaps"] = _gap_notes(window, following is None)
    return windows


def decision_frames(window, frames, frame_cap=3, refine=False, interior_end=None, interior_start=None,
                    skip=()):
    # type: (Dict[str, Any], FrameIndex, int, bool, Optional[float], Optional[float], Sequence[str]) -> List[Dict[str, Any]]
    """Candidate frames for one window, each at or after the end evidence it is meant to follow.

    ``refine`` is the bounded second pass. When the coarse triple is already on record and the round it
    closes is still not decided, the cache holds frames *between* those samples, and the probe that settles
    a game is often one of them: the K.O. flash covers the row on the ending frame and the victory close-up
    has erased it two seconds on, which leaves the interior of the clock freeze as the only place that pip
    is drawn. The interiors come from the index and never from a list of instants, so a denser cache refines
    further and a coarser one refines not at all. ``interior_end`` is where the caller's own answers say the
    row stopped being readable -- asking past it would spend attempts on a frame that cannot hold an answer.
    ``interior_start`` opens the same question **backwards**, because an instant answered with no row says
    only that *that* instant holds nothing: the frames between the round's opening and the deciding reading
    may never have been sampled, and that is where a pip is drawn before the flash buries it.

    ``skip`` names frame paths this caller cannot buy any more -- already answered, or already requested by
    another window of the same plan. It is removed from the refinement pool **before** the two-look cap,
    because capping first and filtering after was the false exhaustion: with the two nearest interiors on
    record the pool was declared empty while the frames beside them, still inside the bound, were never
    even offered. A resume therefore advances to the next bounded frames instead of reporting that nothing
    is left to ask. The cap itself does not move -- two new looks per pass, whichever direction they are in.
    """
    anchor = window.get("end_at")
    candidates = []
    if anchor is not None:
        for offset, role in zip(END_PROBE_OFFSETS, END_PROBE_ROLES):
            frame = frames.nearest(anchor) if offset == 0 else frames.at_or_after(anchor + offset)
            if frame is None:
                window["cache_truncated"] = True
                continue
            candidates.append(_candidate(frame, role, window, anchor, offset))
    elif window.get("opened_at") is not None:
        candidates.append(_candidate(frames.nearest(window["opened_at"]), "round_opening_only",
                                    window, window["opened_at"], 0.0))
    opening = window.get("opening_at")
    if opening is not None and window.get("clock_after") is not None:
        frame = frames.nearest(opening)
        if frame["id"] not in {c["id"] for c in candidates}:
            candidates.append(_candidate(frame, "next_round_opening", window, opening, None))
    seen, unique = set(), []
    for candidate in candidates:
        if candidate["id"] in seen:
            continue
        seen.add(candidate["id"])
        unique.append(candidate)
    if not refine or anchor is None:
        return unique[:frame_cap]
    kept = unique[:frame_cap]
    have = {entry["id"] for entry in kept}
    coarse = [float(entry["offset_seconds"] or 0.0) for entry in kept]
    # The refinement is an append, never a widening of the coarse triple: the cap is what keeps this one
    # extra look per gap rather than a denser sweep of the whole window. The same cap covers a backward
    # look, so opening the other direction cannot spend more than opening ever did.
    room = len(END_PROBE_OFFSETS) - 1
    far = anchor + max(END_PROBE_OFFSETS)
    reach = min(float(interior_end), far) if interior_end is not None else far
    # Backwards the reach is symmetric and never crosses the round's own opening: a pip from the previous
    # round is that round's answer, not this one's.
    near = anchor - max(END_PROBE_OFFSETS)
    if interior_start is not None:
        near = max(float(interior_start), near)
    else:
        near = anchor
    usable = []
    skipped = []
    for frame in frames.window(near, reach):
        if frame["id"] in have:
            continue
        if frame["path"] in skip:
            # Recorded, not just dropped: a stop reason that says "every frame here is answered" has to
            # know whether the frames it never offered were answered or bought by another window.
            skipped.append(frame["path"])
            continue
        stamp = float(frame["timestamp"])
        offset = round(stamp - anchor, 3)
        if not (near <= stamp <= reach) or offset == 0.0 \
                or abs(offset) > max(END_PROBE_OFFSETS) \
                or any(abs(offset - held) < 0.25 for held in coarse):
            continue
        usable.append((abs(offset), frame, offset))
    window["refine_pool_skipped"] = skipped
    # Nearest the deciding instant first. The allowance is two looks and the freeze is barely four seconds
    # wide, so spending it on the far edge of the round while the frame beside the K.O. goes unsampled would
    # be a search order chosen for the code's convenience rather than the footage's.
    room = max(room, 0)
    window["refine_pool_remaining"] = max(0, len(usable) - room)
    for _, frame, offset in sorted(usable, key=lambda item: (item[0], item[1]["timestamp"]))[:room]:
        role = "freeze_interior_%s%gs" % ("plus_" if offset > 0 else "minus_", round(abs(offset), 2))
        kept.append(_candidate(frame, role, window, anchor, offset))
        have.add(frame["id"])
    return kept


def _candidate(frame, role, window, anchor, offset):
    # type: (Dict[str, Any], str, Dict[str, Any], float, Optional[float]) -> Dict[str, Any]
    return {"id": frame["id"], "frame": frame["path"], "timestamp": frame["timestamp"], "role": role,
            "anchor": anchor, "anchor_basis": window.get("end_basis"), "offset_seconds": offset,
            "window": window.get("sequence"), "basis": list(window.get("basis") or []),
            "window_end_at": window.get("end_at"),
            "glare_assumption": "candidate only: whether glare cleared and whether the row is drawn are "
                                "what the answer decides"}


def game_opening_hypotheses(observations, confirm_within_seconds=20.0):
    # type: (Sequence[Dict[str, Any]], float) -> List[Dict[str, Any]]
    """Where a game *might* open. Never used to bound an interval, because round intros match too.

    A hypothesis is corroborated only by a change of pairing; uncorroborated ones stay explicitly marked.
    Intro screens are never mandatory boundaries: their absence cannot erase surrounding gameplay.
    """
    rows = sorted(observations, key=lambda o: o["timestamp"])
    out = []
    for position, row in enumerate(rows):
        if row.get("screen_state") not in ROUND_OPENING_STATES:
            continue
        confirmation = None
        for later in rows[position:position + 8]:
            if later["timestamp"] - row["timestamp"] > confirm_within_seconds:
                break
            if later.get("round_clock") is not None:
                confirmation = later
                break
        if confirmation is None:
            continue
        if out and row["timestamp"] - out[-1]["timestamp"] <= confirm_within_seconds:
            continue
        pairing = tuple(row.get("characters") or ("", ""))
        previous = out[-1]["characters"] if out else None
        # A pairing change is the only corroboration, and only when both sides are readable. Case
        # differences and blank ("unreadable") names must never look like a new game: exact name
        # comparison is what once turned spelling flicker into phantom matches.
        readable = all(str(v).strip() for v in pairing) and bool(previous) and \
            all(str(v).strip() for v in previous)
        def _same(one, other):
            return all(boundary_names.names_close(boundary_names.normalise_name(a),
                                                  boundary_names.normalise_name(b))
                       for a, b in zip(one, other))
        # Either order counts: the broadcast can swap which side it lists first.
        changed_pairing = readable and not (_same(previous, pairing) or
                                            _same(tuple(reversed(previous)), pairing))
        out.append({"timestamp": row["timestamp"], "state": row["screen_state"],
                    "characters": pairing, "corroborated": bool(changed_pairing),
                    "corroboration": "pairing changed" if changed_pairing else
                    ("names unreadable, treated as no evidence" if not readable else
                     "same pairing: a round intro, not a game opening")})
    return out


def plan_interval(observations, saved_readings, frames, start_at, end_at, max_new_requests,
                  pip_target=PIP_TARGET_DEFAULT):
    # type: (Sequence[Dict[str, Any]], Dict[str, Dict[str, Any]], FrameIndex, float, float, int, int) -> Dict[str, Any]
    """Windows over one fixed contiguous interval, split into reused, requested and deferred.

    The interval is passed in from a rule stated elsewhere; this refuses to invent one from opening
    hypotheses. Frames are allocated in time order and allocation stops at the allowance, listing every
    deferral. Nothing is batched to make it fit.
    """
    if end_at <= start_at:
        raise ValueError("interval end %r is not after start %r" % (end_at, start_at))
    rows = [o for o in sorted(observations, key=lambda item: item["timestamp"])
            if start_at <= o["timestamp"] < end_at]
    if not rows:
        raise ValueError("no saved observations inside the interval %s-%s" % (start_at, end_at))
    windows = round_ending_windows(rows, frames, pip_target=pip_target)
    reused, requested, deferred = [], [], []
    allowance = int(max_new_requests)
    for window in windows:
        for candidate in decision_frames(window, frames):
            entry = dict(candidate, reused=candidate["frame"] in saved_readings)
            if entry["reused"]:
                reused.append(dict(entry, reading=saved_readings[candidate["frame"]]))
            elif any(r["id"] == candidate["id"] for r in requested):
                continue
            elif len(requested) < allowance:
                requested.append(entry)
            else:
                deferred.append(entry)
    return {
        "kind": "automatic_decision_window_plan",
        "interval": {"start_at": float(start_at), "end_at": float(end_at),
                     "seconds": round(float(end_at) - float(start_at), 3),
                     "basis": "fixed contiguous interval chosen without reference to openings, marks, "
                              "names, scores, winners or outcomes"},
        "manifest": {"path": DEFAULT_MANIFEST, "frames_in_interval": len(frames.window(start_at, end_at))},
        "windows": windows,
        "reused_readings": reused,
        "requested": requested,
        "deferred_for_budget": deferred,
        "request_allowance": allowance,
        "requests_planned": len(requested),
        "requests_deferred": len(deferred),
        "images_total": 2 * len(requested),
        "budget_fits": not deferred,
        "completion_tokens_ceiling_per_request": 3000,
        "grading": {"graded": False, "accuracy": None,
                    "reason": "no independent per-frame labels exist for this interval; unknown truth is "
                              "ungraded, so no accuracy figure or success verdict is printed"},
        "cannot_be_settled_by_this_selection": [
            "a game winner: only a side's pips reaching %d names one, and a HUD that disappears is "
            "never a conclusion" % pip_target,
            "rounds whose clock is never read, which stay gaps on their window",
            "frames answered as undrawn or unreadable, which stay unknown and re-schedulable"],
    }
