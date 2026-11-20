"""Saved observations: what a frame showed, kept inside the scope of the reading that produced it.

Two kinds of entry live here, and both are *observations*, not conclusions:

* ``text_observation`` -- at one instant, on one seat, the nameplate drew a particular string, and the
  classifier wrote a different one. That is a correction of **displayed text at one timestamp**, and nothing
  else. It does not say two tags belong to one human: a player changes tag, a tag is reused, and one string
  can appear on two seats. Where the same pair shows up at another instant, that instant is another
  observation or it stays uncertain.
* ``stage_observation`` -- at one instant the broadcast banner drew a particular round label. It is fed to the
  format rule that already exists (`boundary_format.applicable_formats` via the ``stage_readings`` argument
  the tracker has always taken), with its provenance attached, and a single label at one candidate edge is
  exactly as trustworthy as it sounds: the overlay is documented to repaint mid-round, so consistency across
  samples is what earns weight and disagreement is reported rather than resolved.

Every entry carries who looked at it (``reviewed_by``), which frame, that frame's digest, and an explicit
statement of what it does *not* establish (``bound_participant_identity``, ``accepted_by``). Those are not
decoration: the validation in :func:`load` refuses an entry that tries to turn a machine's reading into an
operator's acceptance, an unverified guess into evidence, or a corrected string into a person.

Nothing in this module knows about any particular recording, player or timestamp. It loads a file the caller
names; the file is where the specifics live, and the file is data, reviewable and revocable.
"""
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .boundary_names import names_close

#: An observation of text must say it came from looking at pixels. A guess about what the pixels "must" have
#: shown is not an observation, and no amount of confident wording turns it into one.
OBSERVED_TEXT_BASES = ("saved_frame_pixels",)

#: The only things a scope may name. Keeping this closed is what stops a new field arriving that quietly
#: widens an observation -- or claims something about authority while pretending to describe a location.
SCOPE_FIELDS = frozenset({"source", "event", "side", "at", "game_span", "span", "frame_file",
                          "frame_sha256"})

TEXT_KIND = "text_observation"
STAGE_KIND = "stage_observation"
SIDES = ("p1", "p2")

#: How a unique reading can be used, and therefore what a count of them means. ``references`` is not
#: ``observations``: a lookback window reaching into the previous candidate quotes the same reading again.
DISPOSITIONS = ("applied_within_game", "carry_in_context_only", "off_play_only",
                "refused_on_row_text", "unused")

#: How close in time an observation may sit to a sampled row before it stops being about that row. Half a
#: second is far inside this recording's 5.005 s sampling interval, so a correction can only ever be applied
#: to the instant it was made at -- which is the whole point of scoping it.
TIME_TOLERANCE_SECONDS = 0.5


def _finite_seconds(value):
    # type: (Any) -> Tuple[bool, Optional[float]]
    """(ok, seconds). A malformed time is a refusal with a reason, never a traceback and never a zero."""
    if value is None or isinstance(value, bool):
        return False, None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return False, None
    if not math.isfinite(seconds):
        return False, None
    return True, seconds


def _span_seconds(value):
    # type: (Any) -> Tuple[bool, Optional[Tuple[float, float]]]
    """(ok, (lo, hi)) for a declared span: two finite seconds in order, or it scopes nothing."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False, None
    ok_lo, lo = _finite_seconds(value[0])
    ok_hi, hi = _finite_seconds(value[1])
    if not (ok_lo and ok_hi) or lo > hi:
        return False, None
    return True, (lo, hi)


def observation_id(entry):
    # type: (Dict[str, Any]) -> str
    """Which reading this is, independent of how many rows or candidates repeat it.

    The identity *is* the scope: kind, recording, frame digest, seat and instant. A digest rather than prose
    so a duplicate is recognised by what it covers instead of by how it was spelled -- and so "two corrections
    were applied" can only ever mean two readings were made, not two paragraphs mentioned one.
    """
    scope = entry.get("scope") or {}
    ok, seconds = _finite_seconds(scope.get("at"))
    parts = (str(entry.get("kind") or ""), str(scope.get("source") or ""),
             str(scope.get("frame_sha256") or ""), str(scope.get("side") or ""),
             ("%.3f" % seconds) if ok else repr(scope.get("at")))
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def sha256_file(path):
    # type: (Any) -> Optional[str]
    target = Path(str(path))
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject(reason, entry):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    return {"why": reason, "entry": entry}


def validate(entry, root=None, context=None):
    # type: (Dict[str, Any], Optional[Any], Optional[Dict[str, Any]]) -> Tuple[bool, str]
    """Is this entry an observation, and is it honest about what it observes?

    The checks are ordered so the shallowest failure is reported first: shape, then authority, then the text
    itself, then scope, then the frame. A rejection is not a refusal to cooperate -- it is the difference
    between "a machine read this string at this instant" and "these strings look alike, treat them as one
    person", and only the first is admissible here.
    """
    context = dict(context or {})
    if not isinstance(entry, dict):
        return False, "an observation must be an object, not %s" % type(entry).__name__
    kind = str(entry.get("kind") or "")
    if kind not in (TEXT_KIND, STAGE_KIND):
        return False, "unknown kind %r" % kind
    if entry.get("scope") is not None and not isinstance(entry.get("scope"), dict):
        return False, "scope must be an object naming where the observation applies, not %r" % (
            entry.get("scope"),)
    if entry.get("reviewed_by") not in ("machine", "operator"):
        return False, "reviewed_by must be machine or operator"
    if entry.get("authoritative") or entry.get("accepted_by") or entry.get("bound_participant_identity"):
        # A machine reading a frame is evidence about the frame. Acceptance and participant binding belong to
        # a person, and an entry may not grant them to itself by naming them here.
        return False, ("a saved observation may not declare itself authoritative, accepted, or a bound "
                       "participant identity")
    # Authority may not be smuggled in through the scope either: scope says *where* an observation applies,
    # and "everywhere" is not the same claim as "a person accepted this".
    for field in ("authoritative", "accepted_by", "bound_participant_identity", "operator_answer"):
        for holder in (entry, entry.get("scope") or {}):
            if holder.get(field) not in (None, False, ""):
                return False, ("an observation may not declare %s: acceptance and participant binding are a "
                               "person's act, recorded elsewhere" % field.replace("_", " "))
    if str(entry.get("settled_by") or "") not in OBSERVED_TEXT_BASES:
        return False, ("settled_by must be %s -- an entry inferred from rows or resemblance is not an "
                       "observation" % " or ".join(OBSERVED_TEXT_BASES))
    observed = str(entry.get("observed_text") or "").strip()
    raw = str(entry.get("raw_text") or "").strip()
    if not observed:
        return False, "observed_text is empty: there is nothing that was seen"
    if kind == TEXT_KIND:
        if not raw:
            return False, "raw_text is empty: a correction needs the reading it corrects"
        if observed == raw:
            return False, "observed_text equals raw_text, so nothing was corrected"
        if not names_close(raw, observed):
            return False, ("observed_text %r is not a variant of the text being corrected %r: an unrelated "
                           "string is a different observation, not this one" % (observed, raw))
    scope = entry.get("scope") or {}
    side = scope.get("side")
    if kind == TEXT_KIND and side not in SIDES:
        return False, "scope.side must be p1 or p2, not %r" % side
    # A banner label belongs to nobody's seat: the round name is drawn once, above both health bars. The side
    # stays optional here rather than required and meaningless, because inventing a seat for a centre-screen
    # label is how a scope field turns into a claim.
    if kind == STAGE_KIND and side is not None and side not in SIDES:
        return False, "a stage observation may omit scope.side or name p1/p2, not %r" % side
    ok_at, at = _finite_seconds(scope.get("at"))
    if not ok_at:
        return False, ("scope.at must be one finite instant, not %r: an unscoped or malformed correction "
                       "would apply everywhere the text happens to appear" % (scope.get("at"),))
    span = scope.get("game_span") or scope.get("span")
    if span:
        ok_span, bounds = _span_seconds(span)
        if not ok_span:
            return False, ("scope span %r is not an ordered pair of finite seconds, so it declares no "
                           "region an observation could be limited to" % (span,))
        if not (bounds[0] - 1e-9 <= at <= bounds[1] + 1e-9):
            return False, ("scope.at %s falls outside the span %s the entry itself declares, so it is either "
                           "mistimed or broader than its own scope" % (scope["at"], span))
    unknown = sorted(set(scope) - set(SCOPE_FIELDS))
    if unknown:
        return False, ("unknown scope field(s) %s: scope may only say where an observation applies"
                       % ", ".join(unknown))
    for field in ("source", "event"):
        want, have = context.get(field), scope.get(field)
        if want and have and str(want) != str(have):
            return False, "scope.%s is %r but this project records %r" % (field, have, want)
        if want and not have:
            return False, "scope.%s is missing and this project expects %r" % (field, want)
    frame = str(scope.get("frame_file") or "")
    if not frame:
        return False, "scope.frame_file is required: which frame was looked at"
    declared_digest = str(scope.get("frame_sha256") or "")
    if not declared_digest:
        return False, "scope.frame_sha256 is required: a frame reference can be retargeted without it"
    if root is not None:
        target = Path(root) / frame
        actual = sha256_file(target)
        if actual is None:
            return False, "scope.frame_file %r is not a readable file" % frame
        if actual != declared_digest:
            return False, ("scope.frame_sha256 does not match the file it names (file is %s...), so this "
                           "observation may not be about the frame it cites" % actual[:12])
    if kind == STAGE_KIND:
        named = entry.get("candidates")
        if not named:
            return False, ("a stage observation must name the candidate span(s) it is offered to: an "
                           "unscoped label would narrow whichever neighbouring set its instant happened to "
                           "fall inside")
        if not isinstance(named, (list, tuple)):
            return False, "candidates must be a list of [start, end] spans, not %r" % (named,)
        malformed = [span for span in named if not _span_seconds(span)[0]]
        if malformed:
            return False, ("candidate span(s) %r are not ordered pairs of finite seconds, so nothing could "
                           "enforce which candidate they scope this label to" % (malformed,))
    return True, "ok"


def load(path, root=None, context=None):
    # type: (Any, Optional[Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Read one observations file and split it into what is admissible and what was refused, with reasons.

    Accepted entries are unique by scope (see :func:`observation_id`): a file that lists the same reading
    twice is one observation and one refusal, because two mentions of one frame is exactly how a previous
    revision came to report "two corrections applied" from a file holding one usable entry.
    """
    target = Path(str(path))
    payload = json.loads(target.read_text(encoding="utf-8"))
    entries = payload.get("observations") if isinstance(payload, dict) else payload
    digest = sha256_file(target)
    if entries is not None and not isinstance(entries, list):
        return {"path": str(target), "sha256": digest, "accepted": [],
                "rejected": [_reject("observations must be a list of entries, not %s"
                                     % type(entries).__name__, None)],
                "authority_note": "nothing was loaded, so nothing was applied", "inference_sent": False}
    accepted, rejected, seen = [], [], set()
    for entry in entries or []:
        ok, why = validate(entry, root=root, context=context)
        if not ok:
            rejected.append(_reject(why, entry))
            continue
        ident = observation_id(entry)
        if ident in seen:
            rejected.append(_reject(("duplicate of an admitted observation (same kind, recording, frame "
                                     "digest, seat and instant %s): one reading is one observation" % ident),
                                    entry))
            continue
        seen.add(ident)
        accepted.append(dict(entry, observation_id=ident))
    return {"path": str(target), "sha256": digest, "accepted": accepted, "rejected": rejected,
            "authority_note": ("observations here are machine or operator readings of saved frames: they "
                              "correct displayed text at one instant and offer one stage label at one "
                              "instant. None of them is an acceptance, a bound participant identity, or a "
                              "verified bracket result"),
            "inference_sent": False}


def text_corrections(loaded, source=None, event=None):
    # type: (Dict[str, Any], Optional[str], Optional[str]) -> List[Dict[str, Any]]
    """The admitted text observations, narrowed to the recording and event being processed.

    ``observation_id`` is carried so a downstream mention can be traced to one reading: the same entry quoted
    by two candidates is one observation and two references, and a report that counted the references would
    claim corrections nobody made.
    """
    out = []
    for entry in loaded.get("accepted") or []:
        if entry.get("kind") != TEXT_KIND:
            continue
        scope = entry.get("scope") or {}
        if source and str(scope.get("source") or "") and str(scope.get("source")) != str(source):
            continue
        if event and str(scope.get("event") or "") and str(scope.get("event")) != str(event):
            continue
        out.append({"observation_id": entry.get("observation_id") or observation_id(entry),
                    "side": scope.get("side"), "at": float(scope.get("at")),
                    "raw_text": entry.get("raw_text"), "observed_text": entry.get("observed_text"),
                    "frame_file": scope.get("frame_file"), "frame_sha256": scope.get("frame_sha256"),
                    "reviewed_by": entry.get("reviewed_by"), "settled_by": entry.get("settled_by"),
                    "evidence_tier": entry.get("evidence_tier") or "machine_display_text",
                    "accepted_by": entry.get("accepted_by"),
                    "bound_participant_identity": False,
                    "requires_operator_confirmation": bool(entry.get("requires_operator_confirmation", True)),
                    "note": ("displayed text at this instant, corrected by reading the frame; the string "
                             "elsewhere is still whatever the classifier wrote there")})
    return out


def stage_readings(loaded, source=None, event=None):
    # type: (Dict[str, Any], Optional[str], Optional[str]) -> List[Dict[str, Any]]
    """Admitted stage labels in the shape ``_bucket_formats`` already accepts, with provenance attached.

    The label travels as a ``pairing.label`` because that is the existing, generic interface between a
    header reading and the format policy -- no second notion of "stage" is introduced here, and the policy
    still decides what a label means. ``candidates`` is repeated at the top of the record because that is
    where the tracker reads a scope from: it enforces the span(s) the author named (see
    :func:`boundary_games._reading_offered_to_candidate`) instead of letting an instant decide which set a
    label belongs to.
    """
    out = []
    for entry in loaded.get("accepted") or []:
        if entry.get("kind") != STAGE_KIND:
            continue
        scope = entry.get("scope") or {}
        if source and str(scope.get("source") or "") and str(scope.get("source")) != str(source):
            continue
        if event and str(scope.get("event") or "") and str(scope.get("event")) != str(event):
            continue
        out.append({"at": float(scope.get("at")), "pairing": {"label": entry.get("observed_text")},
                    "observation_id": entry.get("observation_id") or observation_id(entry),
                    "candidates": [list(span) for span in entry.get("candidates") or []],
                    "provenance": {"kind": STAGE_KIND, "frame_file": scope.get("frame_file"),
                                   "frame_sha256": scope.get("frame_sha256"),
                                   "reviewed_by": entry.get("reviewed_by"),
                                   "settled_by": entry.get("settled_by"),
                                   "raw_text": entry.get("raw_text"),
                                   "candidates": entry.get("candidates"),
                                   "how_read": entry.get("how_read"),
                                   "evidence_tier": entry.get("evidence_tier") or "machine_display_text",
                                   "accepted_by": entry.get("accepted_by"),
                                   "bound_participant_identity": False,
                                   "does_not_establish": entry.get("does_not_establish") or [
                                       "that the label was current for the whole candidate",
                                       "a verified bracket result"]}})
    return sorted(out, key=lambda row: row["at"])


def apply_to(loaded, source=None, event=None):
    # type: (Dict[str, Any], Optional[str], Optional[str]) -> Dict[str, Any]
    """What this run may use, or the reason it may use nothing.

    A run that cannot name its recording is refused rather than helped: an observation is a claim about one
    file's frames, and admitting it because the frame path exists on disk would apply another recording's
    reading to this one. That is the hole an optional ``--source`` opens, so the narrowing happens here, in
    one place, with the refusal written down.
    """
    admitted = loaded.get("accepted") or []
    if not source:
        return {"text_corrections": [], "stage_readings": [],
                "refused": [{"why": ("no recording named for this run: %d admitted observation(s) are scoped "
                                     "to a file this run did not name, and none of them is applied to an "
                                     "unnamed recording" % len(admitted)),
                             "entries": len(admitted)}],
                "applied_anything": False}
    corrections = text_corrections(loaded, source=source, event=event)
    readings = stage_readings(loaded, source=source, event=event)
    dropped = len([row for row in admitted if row.get("kind") == TEXT_KIND]) - len(corrections)
    dropped_stage = len([row for row in admitted if row.get("kind") == STAGE_KIND]) - len(readings)
    refused = []
    if dropped or dropped_stage:
        refused.append({"why": ("scoped to another recording or event than this run's %r" % str(source)),
                        "text_observations": dropped, "stage_observations": dropped_stage})
    return {"text_corrections": corrections, "stage_readings": readings, "refused": refused,
            "applied_anything": bool(corrections or readings)}


def describe(loaded):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """A summary small enough to live in a report: counts, the file digest, and every refusal's reason."""
    accepted = loaded.get("accepted") or []
    return {"path": loaded.get("path"), "sha256": loaded.get("sha256"),
            "accepted": len(accepted),
            "unique_text_observations": len({row.get("observation_id") or observation_id(row)
                                             for row in accepted if row.get("kind") == TEXT_KIND}),
            "unique_stage_observations": len({row.get("observation_id") or observation_id(row)
                                              for row in accepted if row.get("kind") == STAGE_KIND}),
            "rejected": [{"why": row["why"],
                          "observed_text": (row["entry"] or {}).get("observed_text"),
                          "raw_text": (row["entry"] or {}).get("raw_text"),
                          "at": ((row["entry"] or {}).get("scope") or {}).get("at")}
                         for row in loaded.get("rejected") or []],
            "kinds": {kind: sum(1 for entry in accepted
                                if entry.get("kind") == kind) for kind in (TEXT_KIND, STAGE_KIND)},
            # Restated on the summary because this dict is what reaches a report: an observations block that
            # could not say it asked nothing of a model is a block a reader has to trust instead of check.
            "inference_sent": bool(loaded.get("inference_sent")), "requests_charged": 0}


#: Which role wins when one observation did several jobs across candidates. Order is the claim's strength:
#: a reading that corrected one gameplay row is reported as used, with its weaker mentions listed beside it.
_DISPOSITION_PRIORITY = ("within_game", "off_play", "carry_in_context", "not_applied_row_text_mismatch")

_DISPOSITION_NAMES = {"within_game": "applied_within_game", "off_play": "off_play_only",
                      "carry_in_context": "carry_in_context_only",
                      "not_applied_row_text_mismatch": "refused_on_row_text"}


def disposition(loaded, sets, source=None, event=None):
    # type: (Dict[str, Any], Sequence[Dict[str, Any]], Optional[str], Optional[str]) -> Dict[str, Any]
    """Per unique observation: whether it was used, in what capacity, and how often it is merely repeated.

    Three numbers, because one number is always wrong about something. ``admitted`` counts the readings the
    file made. ``candidate_references`` counts how often a candidate quotes one, which grows by itself
    whenever a game's lookback window reaches into the set before it. ``counts.applied_within_game`` is the
    only figure a winner could be accused of resting on. A reading whose instant sits on a versus screen is
    ``off_play_only`` -- preserved, and used for nothing -- and a reading the row disagreed with is
    ``refused_on_row_text``. Publishing one aggregate over these is how one frame cited by two candidates
    became "two corrections applied".
    """
    unique = {}
    for row in text_corrections(loaded, source=source, event=event):
        unique[row["observation_id"]] = dict(row, roles=[], reference_count=0, candidates=[], why_not=[])
    references = 0
    untraceable = []
    for record in sets or []:
        report = record.get("attribution_confidence") or record
        start = record.get("start")
        for field, role_fallback in (("display_text_observations", None),
                                     ("display_text_correction_refusals",
                                      "not_applied_row_text_mismatch")):
            for row in report.get(field) or []:
                ident = str(row.get("observation_id") or "")
                hit = unique.get(ident)
                if hit is None:
                    untraceable.append({"field": field, "observation_id": ident or None,
                                        "at": row.get("at"), "side": row.get("side"),
                                        "observed_text": row.get("observed_text")})
                    continue
                references += int(row.get("reference_count") or 1)
                hit["reference_count"] += int(row.get("reference_count") or 1)
                role = str(row.get("role") or role_fallback or "unknown")
                if role not in hit["roles"]:
                    hit["roles"].append(role)
                if start is not None:
                    key = round(float(start), 3)
                    if key not in hit["candidates"]:
                        hit["candidates"].append(key)
    counts = {name: 0 for name in DISPOSITIONS}
    entries = []
    for row in unique.values():
        roles = set(row["roles"])
        disposition_name = "unused"
        for role in _DISPOSITION_PRIORITY:
            if role in roles:
                disposition_name = _DISPOSITION_NAMES[role]
                break
        counts[disposition_name] += 1
        if not roles:
            row["why_not"].append(
                "no sampled row at this seat and instant falls inside any candidate's window, gameplay or "
                "menu, so the reading is preserved here and applied nowhere in the recovery")
        if disposition_name == "off_play_only":
            row["why_not"].append(
                "the instant was drawn on a non-gameplay screen, so it is exposed as off-play display text "
                "and deliberately kept out of the nameplate trajectory and out of any winner's support")
        if disposition_name == "carry_in_context_only":
            row["why_not"].append(
                "every candidate that quotes it quotes it as the name carried in from before its own span, "
                "which is lookback context rather than a corrected row of its own")
        entries.append(dict(row, disposition=disposition_name,
                            candidates=sorted(row["candidates"]),
                            roles=sorted(roles),
                            supports_no_winner=True, binds_no_identity=True,
                            accepted_by=None))
    return {"admitted_text_observations": len(unique), "unique_text_observations": len(unique),
            "candidate_references": references,
            "counts": counts, "observations": sorted(entries, key=lambda row: (row["at"], row["side"])),
            "untraceable_references": untraceable,
            "identity_bound_by_observations": 0, "accepted_by_operator": 0,
            "rule": ("counts are per unique scoped reading (kind, recording, frame digest, seat, instant); a "
                     "reading a neighbouring candidate repeats as lookback context is one observation and two "
                     "references, and only the first kind is ever called a correction")}
