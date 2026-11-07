"""Ask the frames what the text never recorded, in bounded, resumable groups.

The game layer establishes rounds from the round clock, but only where the classifier happened to
write the clock down: on BB9 that is 854 of 1,154 gameplay rows, and the win dots beside that clock
were never recorded at all. Those two gaps are the whole reason games cannot be closed from cached
observations, and both are settled by looking at frames that are already on disk.

This module turns an unresolved-queue from :mod:`tekken_vod_helper.boundary_games` into an exact,
numbered plan of frame groups, then runs it against a vision client with a hard request budget. It is
built around three constraints the last session paid for:

* **Reuse before inference.** Every group is resolved against the one-second cache manifest, so no
  frame is extracted and no seconds-long gap is re-watched to answer a question the cache can answer.
* **Compact findings on disk.** Each group is persisted with its request, its reply and a fingerprint
  of the prompt, so a resumed run re-asks nothing and the chat never holds the recording.
* **Visible exhaustion.** A budget that runs out is a result, not a crash: the plan states what was
  not attempted and the run reports it, rather than silently answering less than it promised.
* **One evidence base.** The rows this command plans from are joined to the recording's saved crop
  readings through :mod:`tekken_vod_helper.boundary_evidence`, and its band answers are read for
  identity, so the plan and the set-level recovery it publishes are the same numbers the read-only
  report publishes. A second join here would disagree with the report about the same minute.
* **No uncounted send.** A live run requires the shared attempt ledger, which carries one cap and one
  wall-time deadline across every command and every resume, and is charged before the socket opens.

Nothing here decides a boundary. It gathers facts; the tracker in ``boundary_games`` interprets them.
"""
import base64
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import (boundary_budget, boundary_evidence, boundary_format, boundary_games, boundary_header,
               boundary_media, boundary_pips, boundary_windows, local_model)

DEFAULT_BATCH_FRAMES = 6
DEFAULT_MAX_REQUESTS = 24      # a planning default, not an allowance: a dry-run that omits
                               # --max-requests prices only this many requests, so a bill read back from
                               # such a plan understates the run it is supposed to cost. Pass the funded
                               # number to both --max-requests and --budget-cap.
DEFAULT_COMPLETION_TOKENS = 8000
#: Stamped into every round-facts request and into the group fingerprint, so a batch asked of a
#: different model, density or question can never be reloaded as if it answered this one.
ROUND_FACTS_PROMPT_VERSION = "round-facts-v1"
#: Highest round number the schema accepts. First-to-three can reach a fifth round (3-2), so a reply
#: saying ROUND 4 is a valid observation, not a malformed one. Refusing it would pressure the next pass
#: into mislabelling a real banner just to survive validation.
MAX_ROUND_CALL = 9
#: How much of a between-game stretch the state route is given for a continuity claim: the question is what
#: separates two games -- a controller screen, a set-deciding screen, an ad, dead air -- and that lives at
#: the two ends of the stretch, so a long dwell is probed at both ends instead of sampled for minutes. Five
#: seconds is the shortest look that still spans a screen change at this cache's sampling, and it keeps one
#: probe edge to one request: continuity is the last thing this command spends on, and a probe costing two
#: batches per edge would out-price the winners it is queued behind.
CONTINUITY_PROBE_SECONDS = 5.0
#: Continuity ranks below every per-round and per-game question: it cannot settle a game, and an allowance
#: spent on dwells is an allowance not spent on winners.
CONTINUITY_PRIORITY = 7
#: How far around each kind of question to look, in seconds, as (before, after).
#:
#: Sizing is driven by how long the thing is on screen, not by convenience. A round's win dots light
#: as that round ends and stay lit, so the pip row is readable from the K.O. through the victory pose
#: and the reload -- a handful of seconds, not the whole round. A symmetric 12-second window asked
#: for 24 frames to answer a question four of them settle, and the recording has 121 of those
#: questions to ask. The clock-reset search is the one kind that genuinely needs width.
QUESTION_WINDOW = {
    "win_dots_never_read": (2.0, 6.0),
    "round_end_not_observed": (2.0, 6.0),
    "round_start_not_observed": (12.0, 3.0),
    "round_clock_impossible_fall": (6.0, 6.0),
    # The fall is a single reading between two that disagree with it, so the frames that settle it are
    # the seconds on either side of that reading -- not the game it happened to be grouped into.
    "win_dots_regressed": (6.0, 6.0),
    "game_incomplete": (6.0, 10.0),
    # A game's winner is decided by the dots at its last round end and confirmed by the next game
    # starting from zero, so this one reaches forward past the ending to that reset.
    "game_winner_unobserved": (3.0, 18.0),
    "possible_play_below_confidence": (10.0, 10.0),
    "round_longer_than_the_clock_allows": (10.0, 10.0),
    # A set-layer claim, and the only one the state route can answer: what separates these games is a
    # whole-frame fact, and the probe is placed at each end of the stretch rather than across it.
    "set_continuity": (CONTINUITY_PROBE_SECONDS, CONTINUITY_PROBE_SECONDS),
}
# A round is bounded by its own clock, so a reset is somewhere inside a span of about that length;
# searching the middle first is what lets a capped window still find it.
ROUND_WINDOW_SECONDS = 60.0
# Progressive densification: a region is first inspected at 1 s from the cache, and only the seconds
# still in dispute are re-asked at 5 Hz from the source. A round is bounded by its own clock, so a
# missed reset is the failure this ladder exists to catch.
DENSIFY_STEPS = (1.0, 0.2)

ROUND_FACT_FIELDS = (
    "id", "state", "confidence", "round_clock", "round_call", "round_end", "result_transition",
    "win_dots_p1", "win_dots_p2", "characters", "evidence")

ROUND_FACT_PROMPT = (
    "Read the game's own in-fight HUD from each frame. Report facts only, per frame id.\n"
    "Fields: id(int); state(one of gameplay,stage_select,character_select,vs_screen,stage_intro,"
    "controller_prompt,controller_assignment,post_match_menu,controller_disconnected,unknown); "
    "confidence(float 0-1);\n"
    "  round_clock: the large countdown numeral at top centre as an integer, or null if not visible.\n"
    "  round_call: the number in a 'ROUND n' / 'FINAL ROUND' banner as an integer or 'final', else "
    "null. Report the number actually shown: a game can reach round 4 or 5.\n"
    "  round_end: 'ko' if a K.O. announcement is on screen, 'time_up' if TIME UP is, else null.\n"
    "  result_transition: true if a victory pose, YOU WIN banner or result screen is shown.\n"
    "  win_dots_p1, win_dots_p2: how many of the round-win dots beside the clock are LIT, for the "
    "left and right player, 0-3, or null if the row cannot be read.\n"
    "  characters: the two character names the game displays, left then right.\n"
    "Do NOT report the broadcast player names or the broadcast score boxes, and do not infer any of "
    "these from them: they are a separate overlay and can change mid-round while the fight continues. "
    "Never guess a value to fill a field: use null.\n"
    "Return a JSON array only, one object per frame id, no markdown.\n"
)


def _finite(value):
    # type: (Any) -> bool
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def load_cache(manifest_path):
    # type: (Any) -> List[Dict[str, Any]]
    """The one-second frames already on disk, with their absolute-enough paths and timestamps."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    frames = data.get("frames") if isinstance(data, dict) else data
    if not isinstance(frames, list) or not frames:
        raise ValueError("frame manifest holds no frames: {}".format(manifest_path))
    out = []
    for frame in frames:
        if not _finite(frame.get("timestamp")) or not frame.get("path"):
            continue
        out.append({"timestamp": float(frame["timestamp"]), "path": frame["path"],
                    "id": frame.get("id")})
    out.sort(key=lambda f: f["timestamp"])
    if not out:
        raise ValueError("frame manifest has no usable frames: {}".format(manifest_path))
    return out


def _fingerprint(group, question=None, prompt_version=ROUND_FACT_PROMPT):
    # type: (Sequence[Dict[str, Any]], Optional[Dict[str, Any]], str) -> str
    """Identifies one request: the frames in it and the whole question asked about them.

    The frame path and timestamp are not enough. A group answered by another model, another thinking
    mode or another density is a different request, and reloading it as this one would let evidence
    from a run nobody approved appear inside this one.
    """
    payload = json.dumps({"frames": [[round(f["timestamp"], 6), f["path"]] for f in group],
                          "prompt": hashlib.sha256(prompt_version.encode("utf-8")).hexdigest()[:16],
                          "question": question or {}},
                         sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def candidate_regions(unresolved, duration, observations=None, marks=(), limit=None, window=None):
    # type: (Sequence[Dict[str, Any]], float, Optional[Sequence[Dict[str, Any]]], Sequence[float], Optional[int], Optional[Tuple[float, float]]) -> List[Dict[str, Any]]
    """Turn the tracker's unresolved queue into regions worth looking at, most useful first.

    Marks may raise a region's priority because that is where the operator believed a set was, and they
    may never define one: a mark is a hint about where to look, and this is the only place in the
    pipeline allowed to treat it as evidence of interest rather than evidence of a boundary.
    """
    wants = {
        "round_longer_than_the_clock_allows": ("clock_reset_missed", 0),
        "round_clock_impossible_fall": ("clock_reading_doubted", 1),
        # A pip row that fell is the tracker's own doubt about one reading, and the only queue item that
        # names which side to look at. Left unmapped it is silently uninvestigable, which turns "keep it
        # ambiguous and look again" into "keep it unresolved forever".
        "win_dots_regressed": ("win_dots_regressed", 1),
        "game_incomplete": ("game_conclusion_missing", 2),
        "game_winner_unobserved": ("game_winner_missing", 2),
        "round_end_not_observed": ("round_end_missing", 3),
        "win_dots_never_read": ("win_dots_missing", 4),
        "possible_play_below_confidence": ("low_confidence_play", 5),
        "round_start_not_observed": ("round_start_missing", 6),
    }
    entries = []  # type: List[Dict[str, Any]]
    for item in unresolved or []:
        kind = item.get("type")
        if kind not in wants:
            continue
        at = item.get("at")
        if not _finite(at):
            continue
        # Win dots are read at a round end, not across the round: one frame per round answers it.
        if kind == "win_dots_never_read":
            at = (item.get("at") or 0.0)
        # One region per question, not per time bucket. Bucketing by 120 s merged the round ends at
        # 125 s, 170 s and 235 s into a single target at 125 s, so two thirds of the rounds in the
        # pilot were never inspected -- and the one question that decides a game winner was the one
        # silently dropped. Overlapping questions still merge below, by actual overlap.
        entries.append({"centre": float(at), "priority": wants[kind][1], "kinds": [kind],
                        "span": float(item.get("seconds") or 0.0)})

    regions = []
    for entry in entries:
        span = entry["span"]
        centre = entry["centre"]
        kind = entry["kinds"][0]
        if kind == "round_longer_than_the_clock_allows" and span > ROUND_WINDOW_SECONDS:
            # Two rounds are hiding somewhere in the span and the middle is where a reset a 5 s grid
            # stepped over is most likely to be. A span this wide is searched in one capped window
            # and extended by the next pass only if the first leaves it open.
            low = centre + max(0.0, (span - ROUND_WINDOW_SECONDS) / 2.0)
            high = min(centre + span, low + ROUND_WINDOW_SECONDS)
        else:
            kinds = [key for key in entry["kinds"]]
            before = min(QUESTION_WINDOW.get(k, (20.0, 20.0))[0] for k in kinds)
            after = max(QUESTION_WINDOW.get(k, (20.0, 20.0))[1] for k in kinds)
            low, high = centre - before, centre + after
        low = max(0.0, low)
        high = min(duration, high)
        if window:
            # A bounded pilot has to stay inside its bounds: a question whose centre is in the window
            # must not drag the whole 570 s of a neighbouring round into the request count.
            low, high = max(low, window[0]), min(high, window[1])
        if high <= low:
            continue
        mark_near = any(_finite(m) and low - 60.0 <= m <= high + 60.0 for m in marks)
        regions.append({"start": round(low, 3), "end": round(high, 3),
                        "kinds": entry["kinds"], "span": round(span, 3),
                        "priority": entry["priority"],
                        "mark_near": mark_near})
    _merge_overlapping(regions)
    # A mark goes first, then the kind of question, then time. Nothing else about the queue changes,
    # so a mark decides what gets looked at inside the budget, never what gets looked at at all.
    regions.sort(key=lambda r: (0 if r["mark_near"] else 1, r["priority"], r["start"]))
    return regions[:limit] if limit else regions


def _merge_overlapping(regions):
    # type: (List[Dict[str, Any]]) -> None
    """Collapse regions that overlap, keeping every reason and the strongest priority."""
    regions.sort(key=lambda r: r["start"])
    index = 0
    while index + 1 < len(regions):
        current, following = regions[index], regions[index + 1]
        if following["start"] <= current["end"]:
            current["end"] = max(current["end"], following["end"])
            current["kinds"] = sorted(set(current["kinds"]) | set(following["kinds"]))
            current["priority"] = min(current["priority"], following["priority"])
            current["mark_near"] = current["mark_near"] or following["mark_near"]
            regions.pop(index + 1)
        else:
            index += 1


def continuity_regions(unresolved, duration, marks=(), window=None):
    # type: (Sequence[Dict[str, Any]], float, Sequence[float], Optional[Tuple[float, float]]) -> List[Dict[str, Any]]
    """Whole-frame regions for the set-layer claims that only a whole frame can answer.

    The tracker puts a route on every claim, and until now the set-level ones were prose beside a queue the
    planner read only for game-level kinds. A ``set_continuity`` claim asks what separates these games --
    controller handover, stage identity, what fills the dwell -- and a 128x62 crop of the top band cannot
    see any of that, so this is where that claim becomes frames for :class:`RoundFactsClient`.

    Each window the claim names is probed at both ends and no further, inside the run's own region, and the
    result goes through the same merge, sort and allowance as every other region: a continuity probe that
    competes with a missing winner loses, and says so in the plan's deferral list.
    """
    regions = []  # type: List[Dict[str, Any]]
    for item in unresolved or []:
        if item.get("type") != "set_unresolved":
            continue
        for claim in item.get("unresolved_claims") or ():
            if claim.get("route") != "state":
                continue
            for span in claim.get("windows") or ():
                if not (isinstance(span, (list, tuple)) and len(span) == 2):
                    continue
                low, high = float(min(span)), float(max(span))
                for probe in ((low - CONTINUITY_PROBE_SECONDS, low + CONTINUITY_PROBE_SECONDS),
                              (high - CONTINUITY_PROBE_SECONDS, high + CONTINUITY_PROBE_SECONDS)):
                    start, end = max(0.0, probe[0]), min(duration, probe[1])
                    if window:
                        start, end = max(start, window[0]), min(end, window[1])
                    if end <= start:
                        continue
                    mark_near = any(_finite(m) and start - 60.0 <= m <= end + 60.0 for m in marks)
                    regions.append({"start": round(start, 3), "end": round(end, 3),
                                    "kinds": ["set_continuity"], "span": round(end - start, 3),
                                    "priority": CONTINUITY_PRIORITY, "mark_near": mark_near,
                                    "because": claim.get("missing"),
                                    "would_resolve": claim.get("would_resolve")})
    _merge_overlapping(regions)
    regions.sort(key=lambda r: (0 if r["mark_near"] else 1, r["priority"], r["start"]))
    return regions


#: Which fact answers which queue item. Reuse has to be decided by the fact, not by the timestamp:
#: every frame in the recording has already been classified, and none of those requests asked for the
#: round-win dots, so a timestamp-level skip turns a whole unread-dots queue into zero requests.
KIND_FACT = {
    "win_dots_never_read": "win_dots",
    "win_dots_regressed": "win_dots",
    "game_winner_unobserved": "win_dots",
    # A winner credited from one frame is a published claim nobody looked at twice. The fact that answers
    # it is the same pip row on the neighbouring frame, so it routes like the others and buys its place in
    # the shared budget rather than a lane of its own.
    "game_winner_single_reading": "win_dots",
    "round_clock_unread": "round_clock",
    "round_longer_than_the_clock_allows": "round_clock",
    "round_clock_impossible_fall": "round_clock",
    "round_end_not_observed": "round_end",
    "round_start_not_observed": "round_call",
    "game_incomplete": "result_transition",
    "possible_play_below_confidence": "screen_state",
    # What separates two games is the transition itself: the old classification rows never carried it, so
    # this is a fact worth asking about even where a dwell's screen state is already on record.
    "set_continuity": "result_transition",
}

#: Fields where a structured answer can also be an explicit negative, which still counts as answered.
NEGATIVE_IS_ANSWER = frozenset(["round_end", "result_transition", "round_call"])


def fact_is_answered(observation, field):
    # type: (Optional[Dict[str, Any]], str) -> bool
    """Whether an existing observation already answers this field."""
    if not observation:
        return False
    if field == "win_dots":
        stated = observation.get("win_dots")
        return bool(stated) and any(value is not None for value in stated)
    if field == "screen_state":
        return (observation.get("state") is not None
                and (observation.get("confidence") or 0) >= 0.5)
    value = observation.get(field)
    if value is None:
        return False
    if field in NEGATIVE_IS_ANSWER and value in (False, "none"):
        return True
    return True


def frames_for_region(cache, region, step, answered=(), limit=None):
    # type: (Sequence[Dict[str, Any]], Dict[str, Any], float, Sequence[float], Optional[int]) -> List[Dict[str, Any]]
    """Cached frames inside a region at the requested density, skipping those already answered.

    ``answered`` is the set of timestamps whose *needed fact* is already on record, not the set of
    timestamps ever looked at. Skipping on timestamp alone made the first pass opaque to a second one:
    the recording was classified once, so every frame counted as read and the win dots -- the one
    thing nobody had asked for -- could never be requested anywhere.
    """
    done = {round(t, 3) for t in answered}
    lo, hi = region["start"], region["end"]
    picked = []
    previous = None
    for frame in cache:
        ts = frame["timestamp"]
        if ts < lo or ts > hi:
            continue
        if round(ts, 3) in done:
            continue
        if previous is not None and ts - previous < step - 1e-6:
            continue
        previous = ts
        picked.append(frame)
        if limit and len(picked) >= limit:
            break
    return picked


def group_frames(frames, size=DEFAULT_BATCH_FRAMES):
    # type: (Sequence[Dict[str, Any]], int) -> List[List[Dict[str, Any]]]
    """Contiguous batches: the model is asked about neighbouring seconds, never scattered ones."""
    if size < 1:
        raise ValueError("batch size must be at least 1")
    return [list(frames[i:i + size]) for i in range(0, len(frames), size)]


#: The band answers who sat on which screen side. It is a separate question, with its own executor and
#: its own saved-answer route; frames cannot answer it at all. This command now *sends* band requests for
#: identity gaps it cannot resolve from saved answers, and still refuses the run if a band reading ever
#: moves a boundary.
HEADER_ROUTE_NOTE = ("participant identity is a band question, never a frame question: this command "
                     "dispatches it to HeaderReadClient and uses the answer for attribution only")


#: The three executors this command dispatches to, in the order one pass spends them.
#:
#: Pip readings before state readings because on this recording the clock is already on record and the
#: dots never were, and a win-dot answer is the one that can settle a game. Both before the band,
#: because an identity reading is only worth an attempt once there is a win to attribute: on a frame
#: whose row has not been read, "who sat on the left" answers a question nobody has asked yet. That is
#: a scheduling rule, not a ranking of importance, and it is what keeps a small allowance from buying
#: eleven nameplates for four games with no winner.
ROUTE_ORDER = ("pip_crops", "state", "header")

#: What each route is asked, and what its answer is allowed to become.
ROUTE_CLIENT = {"pip_crops": "PipReadClient(arm=crops)", "state": "RoundFactsClient",
                "header": "HeaderReadClient"}
ROUTE_PROMPT = {"pip_crops": boundary_pips.CROP_VERSION, "state": ROUND_FACTS_PROMPT_VERSION,
                "header": boundary_header.VERSION}
ROUTE_EVIDENCE = {
    "pip_crops": ("validated_pip_evidence", "two magnified crops of one pip row, frozen geometry, "
                                           "control arm, measured accuracy"),
    "state": ("state_facts", "the round clock, the ROUND banner, the end cue and the transition, read "
                             "from the whole frame: measured reliable on this recording"),
    "header": ("identity_only", "the broadcast band: pairing and displayed tally, never a pip "
                                "measurement and never a boundary"),
}


def route_filter(routes):
    # type: (Optional[Sequence[str]]) -> tuple
    """The routes this plan and run may use, validated. An unknown name is a typo, not a no-op.

    Restricting routes is a real operation, not a test convenience: an approval often funds one executor
    at a time, and the difference between "the band route had nothing to ask" and "the band route was
    never funded" has to be visible in the artifact instead of inferred from a zero.
    """
    chosen = tuple(ROUTE_ORDER) if routes is None else tuple(routes)
    unknown = [route for route in chosen if route not in ROUTE_ORDER]
    if unknown:
        raise ValueError("unknown routes: {} (this command dispatches {})".format(
            unknown, ", ".join(ROUTE_ORDER)))
    return chosen


#: Which unresolved kinds are pip questions. ``game_winner_unobserved`` is included because the tracker
#: raises it when a game ended with no readable row, which is the same frame class the round-end queue
#: names from the other direction.
PIP_QUEUE_KINDS = ("win_dots_never_read", "game_winner_unobserved", "pip_row_not_drawn_at_round_end",
                   "game_winner_single_reading")


def pip_anchors(unresolved):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """The instants the tracker itself said need a pip reading, oldest first."""
    anchors = [{"at": float(item["at"]), "kind": item.get("type"),
                "anchor": item.get("anchor") or "round_end",
                # Carried so a claim this plan cannot act on can be named by side and frame in the stop
                # reason: "this instant is exhausted" only helps if it says which claim it means.
                "side": item.get("side"), "frame": item.get("frame"),
                "game_start": item.get("game_start")}
               for item in unresolved or []
               if item.get("type") in PIP_QUEUE_KINDS and _finite(item.get("at"))]
    return sorted(anchors, key=lambda entry: entry["at"])


def queue_windows(anchors, covered, first_sequence):
    # type: (Sequence[Dict[str, Any]], Sequence[Tuple[float, float]], int) -> List[Dict[str, Any]]
    """One-instant windows for queue anchors no clock-derived window reached.

    ``end_at`` is the anchor and nothing else, so ``decision_frames`` probes the anchor, +2 s and +4 s --
    the measured spacing for a pip landing after its cue, not a guessed denser grid. An anchor already
    inside a clock window is skipped rather than asked twice.
    """
    windows = []
    for position, entry in enumerate(anchors):
        at = entry["at"]
        if any(span[0] <= at <= span[1] for span in covered):
            continue
        windows.append({"end_at": at, "opened_at": at, "sequence": first_sequence + position,
                        "basis": ["tracker queue: {}".format(entry["kind"])],
                        "end_basis": "queue anchor ({})".format(entry["anchor"]),
                        "gaps": [], "clock_before": None, "last_clock": None})
    return windows


def pip_answered_frames(observations, crop_readings):
    # type: (Optional[Sequence[Dict[str, Any]]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Which frames the crop route need not ask for again, decided by the grade of what is on the row.

    A frame counts as answered when a crop reading for it is on disk, *or* when the row already carries a
    pip value whose source is a validated reading. Deciding it on the second half alone would miss the
    corpus (whose readings join by frame path, not by row), and deciding it on the first half alone would
    re-ask a frame the timeline already measured. A row holding only a ``win_dots_diagnostic`` is not
    answered: that value is why the route exists.
    """
    # Keyed by frame identity, not by the spelling a caller happened to use: an answer recorded under an
    # absolute path and a cache frame named by a relative one are the same picture, and treating them as
    # two re-priced six crop requests that had already been answered and paid for.
    answered = {boundary_evidence.frame_identity(key): entry
                for key, entry in (crop_readings or {}).items()}
    for row in observations or []:
        path = boundary_evidence.frame_identity(row.get("path") or row.get("frame") or "")
        if not path or path in answered:
            continue
        if row.get("win_dots") is None and not any(
                value is not None for value in (row.get("win_dots") or [])):
            continue
        if row.get("pip_evidence_class") == "validated_pip_evidence" or boundary_evidence.pip_grade(
                row.get("fact_source") or row.get("source")) == "validated":
            answered[path] = {"timestamp": row.get("timestamp"), "win_dots": row["win_dots"],
                              "from_row": True}
    return answered


#: What an unfunded route costs, said in the artifact so a zero is never read twice the same way. The
#: price of the work is kept (see :func:`plan_investigation`); only the promise to send it is withdrawn.
ROUTE_UNFUNDED = {
    "pip_crops": "this invocation did not fund the crop route: the pip questions stay open, and no winner "
                 "count derived from them is evidence",
    "state": "this invocation did not fund the state route: clocks, round banners, end cues and "
             "transitions stay unread in this window",
    "header": "this invocation did not fund the band route: wins may be counted and still name nobody",
}


def pip_requests(observations, frames, window, answered, max_requests, model=None, enabled=True,
                 unresolved=None):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]], Optional[Tuple[float, float]], Dict[str, Any], int, Optional[str]) -> Dict[str, Any]
    """Crop readings the corpus does not hold yet, at the instants that can settle a round.

    The frames are not a denser version of the state grid: they are the decision windows
    (:mod:`tekken_vod_helper.boundary_windows`), which place a candidate at the round end, at +2 s and at
    +4 s because the K.O. flash covers the third pip on the frame the round ends on and the victory
    close-up erases the row soon after. Asking the state route's 2 s grid instead would spend the same
    attempts on frames where the row is provably unreadable.

    Reuse is by frame path within the cache framing, so a source-framing answer to the same instant is
    not counted: it is a different picture, and treating it as answered is how a second framing once
    corrupted one pip series.
    """
    index = boundary_windows.FrameIndex(frames)
    identity = boundary_evidence.frame_identity
    # Normalised here as well as in :func:`pip_answered_frames`, because callers hand this function a
    # readings dict directly -- a run's own answers, a test fixture, a caller that already filtered the
    # corpus. Keying one side by identity and the other by whatever string arrived is how six answered
    # frames got priced a second time.
    answered = {identity(key): entry for key, entry in (answered or {}).items()}

    def answered_as(frame):
        # type: (str) -> Optional[Dict[str, Any]]
        """The saved reading for this frame, under whichever spelling of the path recorded it."""
        return (answered or {}).get(identity(frame))

    def is_answered(frame):
        # type: (str) -> bool
        return identity(frame) in (answered or {})
    lo, hi = window if window else (index.frames[0]["timestamp"], index.frames[-1]["timestamp"] + 0.001)
    inside = [row for row in observations
              if _finite(row.get("timestamp")) and lo <= float(row["timestamp"]) < hi]
    # Two sources of instants, one placement rule. The clock gives the round ends the tracker could see;
    # the unresolved queue gives the ones it could *not* see, and a window whose clock was never read is
    # exactly where a pip row is most worth looking at. Deriving frames from the clock alone would have
    # dropped those questions silently the moment the row stopped being a whole-frame field.
    route = "pip_crops"
    windows = boundary_windows.round_ending_windows(inside, index) if inside else []
    covered = [(float(entry.get("opened_at") or entry.get("end_at") or 0.0),
                float(entry.get("next_opening_at") or entry.get("end_at") or 0.0))
               for entry in windows]
    anchors = [entry for entry in pip_anchors(unresolved) if lo <= entry["at"] <= hi]
    windows = windows + queue_windows(anchors, covered, len(windows))
    # Frames the crop route cannot buy any more, by path: already answered on disk, or already requested by
    # an earlier window of this same plan. The refinement pool removes them *before* its two-look cap, so a
    # resume advances to the next bounded frame instead of watching the cap fill with frames it will only
    # count as reused -- the false exhaustion this repair exists to end.
    answered_paths = {frame["path"] for frame in index.frames if is_answered(frame["path"])}
    requests, deferred, seen, reused = [], [], set(), 0
    claims_stopped = []
    claims_continuing = []
    planned_by = {}    # frame path -> window that FUNDED its request, for the elsewhere report
    deferred_by = {}   # frame path -> window that DEFERRED it unfunded, for the same report: a frame
    #                    waiting on allowance is neither answered nor scheduled -- calling it exhausted is
    #                    the false report, and calling it scheduled would be its lie mirrored
    for window in windows:
        coarse = boundary_windows.decision_frames(window, index)
        # A second, bounded pass, and only for the window whose coarse triple is finished and whose question
        # is still open. "Finished" is every candidate already on disk. Two different things count as still
        # open:
        #
        #   * no answer in the window shows a side reaching the round target -- a row read as 2-2 on the
        #     frame a game is supposed to have ended on is a reading taken before the deciding pip was
        #     drawn, not an answer;
        #   * or the tracker says the reading that DID reach the target was looked at once
        #     (``game_winner_single_reading``). That claim is precisely the case the first test excludes,
        #     because the window is decided by construction -- and it is the same physical problem: the one
        #     frame that names the winner may be the flash frame, and the frame beside it may never have
        #     been sampled. Refinement here buys corroboration, not a winner.
        #
        # The refinement stops where the caller's own answers say the row stopped being readable, because
        # past that frame there is nothing to ask. No timestamps are special-cased: the span, the target and
        # the readable limit all come from what this recording already returned.
        readings = [(answered_as(entry["frame"]) or {}).get("win_dots") or []
                    for entry in coarse]
        exhausted = bool(coarse) and all(is_answered(entry["frame"]) for entry in coarse)
        decided = any(value is not None and int(value) >= boundary_windows.PIP_TARGET_DEFAULT
                      for values in readings for value in values)
        lost_row_at = next(((answered_as(entry["frame"]) or {}).get("timestamp")
                            for entry, values in zip(coarse, readings)
                            if not any(value is not None for value in values)), None)
        at = float(window.get("end_at") or 0.0)
        reach = max(boundary_windows.END_PROBE_OFFSETS) + 0.6
        questioned = [entry for entry in anchors if abs(float(entry["at"]) - at) <= reach]
        # The tracker's own question has to be live *here*. Without this, "no reading reached the round
        # target" would refine every ordinary round end in the recording -- most rounds finish 2-1 or 1-0, so
        # the target test alone cannot tell a settled round from an unanswered game.
        single_support = any(entry["kind"] == "game_winner_single_reading" for entry in questioned)
        # An answer with no row bounds the search on the side it was found: it says nothing lies that way,
        # not that nothing lies either way. So the backward limit is the round's own opening, and a window
        # whose forward side is already closed still gets its interior before the flash asked.
        far_usable = lost_row_at is None or float(lost_row_at) > at + 0.05
        opening_at = window.get("opened_at")
        back_usable = (_finite(opening_at) and float(opening_at) < at - 0.05) or not _finite(opening_at)
        pool_truncated_before = 0
        if exhausted and questioned and (not decided or single_support) \
                and ((far_usable and lost_row_at is not None) or (single_support and back_usable)):
            # Only the corroboration case looks backwards. An ordinary undecided round keeps the pass it was
            # tested with -- forward, bounded by the first frame where its own answer says the row vanished
            # -- because that behaviour is what recovered the accepted games, and widening it for every
            # window would silently re-scope the recording on the strength of one new claim kind.
            candidates = boundary_windows.decision_frames(
                window, index, refine=True,
                interior_end=float(lost_row_at) if lost_row_at is not None and far_usable else None,
                interior_start=float(opening_at) if (single_support and _finite(opening_at)) else None,
                skip=answered_paths | seen)
            # Bounded frames the two-look cap could not fit this pass. The claim is not exhausted and the
            # window is not done: it is a search-budget stop, and the queue says so in those words.
            pool_truncated_before = int(window.get("refine_pool_remaining") or 0)
        else:
            candidates = coarse
        asked_here = deferred_here = 0
        elsewhere_windows = set()
        deferred_windows = set()
        for candidate in candidates:
            if candidate["frame"] in seen:
                if candidate["frame"] in planned_by:
                    elsewhere_windows.add(planned_by[candidate["frame"]])
                elif candidate["frame"] in deferred_by:
                    deferred_windows.add(deferred_by[candidate["frame"]])
                continue
            seen.add(candidate["frame"])
            if is_answered(candidate["frame"]):
                reused += 1
                continue
            entry = {"route": "pip_crops", "frame": candidate["frame"],
                     "timestamp": candidate["timestamp"], "role": candidate["role"],
                     "anchor": candidate.get("anchor"), "window": candidate.get("window"),
                     "asked": ["win_dots"], "images": 2,
                     "client": ROUTE_CLIENT["pip_crops"], "prompt_version": ROUTE_PROMPT["pip_crops"]}
            entry["identity"] = pip_request_identity(entry, model)
            if len(requests) < max_requests:
                requests.append(entry)
                asked_here += 1
                planned_by[entry["frame"]] = candidate.get("window")
            else:
                left = {key: entry[key] for key in ("frame", "timestamp", "role", "window", "anchor")
                        if key in entry}
                deferred.append(dict(left, route=route))
                deferred_by[entry["frame"]] = candidate.get("window")
                deferred_here += 1
        # Frames the bounded pool held but the skip set removed before the cap are not candidates any
        # more, so the loop above cannot see them; a stop that names another window's work as this
        # window's exhaustion is the false report this whole repair exists to end -- and the work it
        # names has to be described as it truly stands: funded and on its way, or deferred and unfunded.
        for path in window.get("refine_pool_skipped") or ():
            if path in planned_by:
                elsewhere_windows.add(planned_by[path])
            elif path in deferred_by:
                deferred_windows.add(deferred_by[path])
        if single_support and asked_here and pool_truncated_before:
            # Work in hand, not evidence exhausted: the cap took the nearest two looks and left bounded
            # frames unread on purpose, so the next pass owes this window another ask. Saying it here is
            # what keeps "two looks this pass" from being read as "two looks, full stop".
            claims_continuing.append({
                "at": at, "side": next((entry.get("side") for entry in questioned
                                        if entry["kind"] == "game_winner_single_reading"), None),
                "window": window.get("sequence"), "asked_this_pass": asked_here,
                "bounded_frames_remaining": pool_truncated_before,
                "stopping": "search_budget",
                "because": "this pass takes the two bounded looks nearest the deciding reading and %d "
                           "further cached frame(s) inside the same bound stay unread -- they are offered "
                           "again on resume, so the claim is neither funded closed nor exhausted"
                           % pool_truncated_before})
        if single_support and not asked_here and not deferred_here:
            # A live claim this plan could not act on says why, and stays in the queue regardless: running
            # out of reachable frames here is evidence about this footage, not proof that the winner was
            # looked at twice. Nothing clears it silently, and nothing requires a second read to keep it.
            bound = ("the round opening at %.3f and the deciding reading at %.3f"
                     % (float(opening_at), at) if _finite(opening_at) and not far_usable else
                     "the deciding reading at %.3f and the frame where the row stops being drawn at %.3f"
                     % (at, float(lost_row_at)) if lost_row_at is not None else
                     "the %.0f s either side of the deciding reading at %.3f"
                     % (max(boundary_windows.END_PROBE_OFFSETS), at))
            if elsewhere_windows or deferred_windows:
                # Overlapping windows: the frame this window cannot re-ask is work another window of
                # this same plan holds -- bought and on its way, or named and unfunded. Either way that
                # is not exhausted evidence, and the reason has to name which window holds it and in
                # what state, so nobody reads a deferral as a send or a send as an answer.
                exhausted_bound = False
                order = lambda seq: ", ".join(str(w) for w in sorted(  # noqa: E731
                    seq, key=lambda w: (w is None, w)))
                held = []
                if elsewhere_windows:
                    held.append("requested by window %s" % order(elsewhere_windows))
                if deferred_windows:
                    held.append("left unfunded by window %s" % order(deferred_windows))
                because = ("every cached frame between %s is either already answered or %s of this plan "
                           "-- the frames nobody has answered here are that window's %s, not this "
                           "window's exhaustion, and the claim stays open until that work is funded and "
                           "answered" % (bound, " or ".join(held),
                                         "sends" if elsewhere_windows else "deferred looks"))
            else:
                exhausted_bound = True
                because = ("every cached frame between %s is already answered, and none of them "
                           "adds a second reading of this side at the target -- the winner stays "
                           "credited on the reading it has, and the claim stays open" % bound)
            claims_stopped.append({"at": at, "side": next((entry.get("side") for entry in questioned
                                                           if entry["kind"] == "game_winner_single_reading"),
                                                          None),
                                   "window": window.get("sequence"),
                                   "type": "game_winner_single_reading",
                                   "reachable_frames": len({entry["frame"] for entry in candidates}
                                                           | set(window.get("refine_pool_skipped") or ())),
                                   "exhausted": exhausted_bound,
                                   "stopping": ("evidence_exhausted" if exhausted_bound else
                                                "planned_elsewhere" if elsewhere_windows
                                                else "deferred_elsewhere"),
                                   "planned_elsewhere_in": sorted(elsewhere_windows,
                                                                  key=lambda w: (w is None, w)),
                                   "deferred_elsewhere_in": sorted(deferred_windows,
                                                                   key=lambda w: (w is None, w)),
                                   "because": because})
    return {"route": "pip_crops", "requests": requests, "requests_planned": len(requests),
            "images_total": 2 * len(requests), "windows": len(windows), "reused": reused,
            "deferred": deferred,
            # A live claim this plan could not act on, with the reason it could not. Listed separately from
            # ``deferred`` because deferral means "the allowance ran out", and this means "there is no frame
            # left to buy here" -- the first is next operator's problem, the second is evidence about the
            # footage, and conflating them would let a stopped claim look like an unfunded one.
            "claims_stopped": claims_stopped,
            # Bounded frames still unread behind a claim this pass did fund: a search-budget stop, kept
            # apart from ``claims_stopped`` because nothing is exhausted here -- the next pass owes work.
            "claims_continuing": claims_continuing,
            "completion_tokens_per_request": boundary_pips.PipReadClient.request_max_tokens,
            "evidence_class": ROUTE_EVIDENCE["pip_crops"][0],
            "why_this_route": ROUTE_EVIDENCE["pip_crops"][1],
            "reused_because": "a crop reading for that frame and framing is already on disk, so the "
                              "attempt that would re-take it is not planned"}


def pip_request_identity(request, model=None):
    # type: (Dict[str, Any], Optional[str]) -> str
    """What a crop answer is an answer *about*: the frame, the arm's geometry, the prompt, the model.

    Derived from content, never from a position in a list or a directory name, so moving a run's output
    or re-planning at a different density cannot make an answered request look new. The geometry is in
    ``boundary_pips.arm_identity`` and is what makes a re-crop at another scale a different request.
    """
    payload = json.dumps({"route": "pip_crops", "frame": request["frame"],
                          "timestamp": round(float(request["timestamp"]), 6),
                          "identity": boundary_pips.arm_identity("crops"),
                          "model": model or local_model.default_model()},
                         sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def header_requests(questions, frames, max_requests, model=None, enabled=True):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]], int, Optional[str]) -> Dict[str, Any]
    """Band readings for the identity gaps the saved answers could not reach.

    Two instants per disputed span, because a single sample cannot tell "the pairing changed" from "the
    band was mid-change on this frame" -- that ambiguity is the thing being investigated. The routing and
    the request shape are :mod:`tekken_vod_helper.boundary_header`'s, so what this command sends is byte
    for byte what the band executor would have sent, and one archive of answers serves both.
    """
    if not questions:
        return {"route": "header", "requests": [], "requests_planned": 0, "images_total": 0,
                "reused": 0, "deferred": [], "unrouted": [], "enabled": bool(enabled),
                "completion_tokens_per_request": boundary_header.HeaderReadClient.request_max_tokens,
                "evidence_class": ROUTE_EVIDENCE["header"][0],
                "why_this_route": ROUTE_EVIDENCE["header"][1],
                "never_answers": "a pip count, a round, a game or a boundary"}
    index = boundary_windows.FrameIndex(frames)

    def frame_at(at):
        # type: (float) -> Optional[Dict[str, Any]]
        """The manifest's own record for the nearest cached frame, timestamp included.

        Not just the path: on this cache the frame's number and the instant it was drawn differ, and a
        request that carried only the asked-for instant would have its answer credited to footage that
        never drew it. ``boundary_header.route_questions`` needs both to pin the reading.
        """
        return index.nearest(at)

    routed = boundary_header.route_questions(questions, frame_at)
    route = "header"
    requests, deferred = [], []
    for entry in routed["instants"]:
        built = {"route": "header", "at": entry["at"], "frame": entry["frame"],
                 # The instant asked about, kept beside the instant that will be credited. A reviewer
                 # checking a band answer against a frame needs to see both numbers and the distance.
                 "requested_at": entry.get("requested_at"),
                 "offset_seconds": entry.get("offset_seconds"),
                 "frame_timestamp": entry.get("frame_timestamp"),
                 "frame_id": entry.get("frame_id"), "time_pinned": True,
                 "question": entry.get("question") or "identity_mapping",
                 "because": entry.get("why"), "would_resolve": entry.get("would_resolve"),
                 "images": 1, "client": ROUTE_CLIENT["header"],
                 "prompt_version": ROUTE_PROMPT["header"]}
        built["identity"] = header_request_identity(built, model)
        if len(requests) < max_requests:
            requests.append(built)
        else:
            left = {key: built[key] for key in ("at", "frame", "question", "because")}
            deferred.append(dict(left, route=route))
    return {"route": "header", "requests": requests, "requests_planned": len(requests),
            "images_total": len(requests), "reused": 0, "deferred": deferred,
            "unrouted": routed["unrouted"],
            "instant_because": "a band request is credited to the instant its frame was drawn; "
                               "``requested_at`` is the number the question asked and "
                               "``offset_seconds`` the distance between them",
            "completion_tokens_per_request": boundary_header.HeaderReadClient.request_max_tokens,
            "evidence_class": ROUTE_EVIDENCE["header"][0],
            "why_this_route": ROUTE_EVIDENCE["header"][1],
            "never_answers": "a pip count, a round, a game or a boundary: a band reading names who sat "
                             "where, and this command refuses the run if it ever moves a cut"}


def header_request_identity(request, model=None):
    # type: (Dict[str, Any], Optional[str]) -> str
    """The band question's identity: instant, frame, prompt version and model.

    The rendered image's digest is added by :func:`boundary_header.request_fingerprint` once the band is
    actually rendered for a send, which is the stricter of the two and is what the persisted answer
    carries. This one is what the *plan* can state before any file exists.
    """
    payload = json.dumps({"route": "header", "at": round(float(request["at"]), 6),
                          "frame": request["frame"], "question": request["question"],
                          "version": boundary_header.VERSION,
                          "model": model or local_model.default_model()}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


#: How far from a claim's own instant a planned request or a saved answer still counts as being about it.
#: One frame's spacing on this cache is 1.000986 s, so this is "the same sampled instant", not "nearby".
CLAIM_INSTANT_TOLERANCE_SECONDS = 0.05

#: Which executor answers which claim the set layer raises. The tracker carries a route on every claim;
#: this map is the check on it, so a claim routed to an executor that cannot answer it is a defect the plan
#: reports instead of work it silently drops. A band crop cannot show a controller being handed over, and
#: that is how this table earned its existence.
CLAIM_EXECUTOR = {
    "game_results": "pip_crops",
    "set_membership": "pip_crops",
    "completion": "pip_crops",
    "set_continuity": "state",
    "identity": "header",
    "format": "user_policy",
}


def _near(instants, value, tolerance=CLAIM_INSTANT_TOLERANCE_SECONDS):
    # type: (Sequence[Any], Optional[float], float) -> bool
    return (value is not None and any(_finite(entry) and abs(float(entry) - float(value)) <= tolerance
                                      for entry in instants))


def claim_work(unresolved, pip_plan, header_plan, regions, observations=None, crop_readings=None,
               header=None, enabled_routes=ROUTE_ORDER, state_unasked=()):
    # type: (Sequence[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Sequence[Dict[str, Any]], Optional[Sequence[Dict[str, Any]]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Sequence[str]) -> Dict[str, Any]
    """Every claim the set layer says it cannot decide, and what this plan is doing about it.

    **Accounting is per point, not per claim.** A claim names the instants or windows it needs, and each one
    is classified on its own -- ``scheduled`` when this plan sends a named frame for it, ``saved`` when an
    answer already on disk covers it, ``deferred`` when a route's own deferral list names it, and
    ``unaccounted`` when none of those hold, which is a defect in this function rather than a result. The
    claim's disposition is then read off the whole set, because a claim that got half of its evidence is
    not a claim that was carried:

    ``planned``   -- every point the claim names is scheduled, and the request identities are listed. Not a
                     count: the next operator has to be able to find each send.
    ``partial``   -- some points are scheduled and some are not. ``unmet`` names every point that this plan
                     does not send, and ``coverage`` says which of planned, saved or deferred each one is.
                     This disposition exists because ``planned`` used to be awarded on overlap alone: a pip
                     claim needing 10 s and 30 s reported planned with a reason claiming "every instant",
                     while 30 s sat in the route's deferral list and nothing showed in the ledger.
    ``reused``    -- saved evidence covers every point and the claim is still open on it. ``closes`` is
                     false, and saying so is the point: re-asking the same pictures is not progress.
    ``deferred``  -- nobody is being asked, by name and with a reason: the route was not funded, the
                     allowance ended before these points, the footage says there is nothing further to read.
    ``user_answer`` -- the format claim only, and it is not a purchase. One question for the whole recording
                     however many candidates owe it (a per-candidate ask is the loop this guard exists to
                     stop), and never any inference.

    Duplicate requests are the other half of the rule. A claim whose instants the per-game questions already
    reach is attributed to those requests and buys nothing new -- the crop route plans one read per frame,
    not one per claim, or ten claims about ten games would ask for ten games' pip rows twice.
    """
    tolerance = CLAIM_INSTANT_TOLERANCE_SECONDS
    crops = crop_readings or {}
    rows = {round(row["timestamp"], 3): row for row in observations or ()
            if _finite(row.get("timestamp"))}
    pip_scheduled = list(pip_plan.get("requests") or ())
    pip_unasked = list(pip_plan.get("deferred") or ())
    band_scheduled = list(header_plan.get("requests") or ())
    band_unasked = list(header_plan.get("deferred") or ())
    state_scheduled = list(regions or ())
    state_unasked = list(state_unasked or ())
    readings = (header or {}).get("readings") or ()

    def label(kind, value):
        # type: (str, Any) -> str
        return ("%.6f s" % float(value) if kind == "instant"
                else "%.6f-%.6f s" % (float(value[0]), float(value[1])))

    def near(entry, key, value):
        # type: (Dict[str, Any], str, float) -> bool
        found = entry.get(key)
        return _finite(found) and abs(float(found) - float(value)) <= tolerance

    def frame_of(entry):
        # type: (Dict[str, Any]) -> str
        return Path(str(entry.get("frame") or "?")).name

    def pip_point(value):
        """Who answers one pip instant: a scheduled read, a saved reading, a named deferral, or nobody."""
        sent = [entry["identity"] for entry in pip_scheduled
                if near(entry, "timestamp", value)]
        if sent:
            return "scheduled", sent
        saved = ["saved crop reading on %s" % frame_of({"frame": frame})
                 for frame, entry in crops.items() if near(entry or {}, "timestamp", value)]
        if saved:
            return "saved", saved
        left = ["crop frame %s at %.3f s left unasked by the %s route"
                % (frame_of(entry), float(entry["timestamp"]), entry.get("route", "pip_crops"))
                for entry in pip_unasked if near(entry, "timestamp", value)]
        if left:
            return "deferred", left
        return "unaccounted", []

    def band_point(value):
        sent = [entry["identity"] for entry in band_scheduled if near(entry, "at", value)]
        if sent:
            return "scheduled", sent
        saved = ["band reading at %.6f s" % float(reading["at"]) for reading in readings
                 if near(reading, "at", value)]
        if saved:
            return "saved", saved
        left = ["band frame %s at %.3f s left unasked" % (frame_of(entry), float(entry["at"]))
                for entry in band_unasked if near(entry, "at", value)]
        if left:
            return "deferred", left
        return "unaccounted", []

    def band_window(low, high):
        """A game's stretch is carried by any band request or reading that falls inside it.

        An identity question is answered by a nameplate drawn *during* the game whose win needs a person,
        and that is exactly how ``missing_header_observations`` prices it -- so the containment test is the
        same one the join uses to credit a reading to a game. Matching on the span's two endpoints instead
        would ask for frames the planner has no reason to schedule and report honest coverage as missing.
        """
        inside = lambda entry: (_finite(entry.get("at"))
                                and low - tolerance <= float(entry["at"]) <= high + tolerance)
        sent = [entry["identity"] for entry in band_scheduled if inside(entry)]
        if sent:
            return "scheduled", sent
        saved = ["band reading at %.6f s" % float(reading["at"]) for reading in readings
                 if inside(reading)]
        if saved:
            return "saved", saved
        left = ["band frame %s at %.3f s left unasked" % (frame_of(entry), float(entry["at"]))
                for entry in band_unasked if inside(entry)]
        if left:
            return "deferred", left
        return "unaccounted", []

    def state_point(low, high, field):
        """One whole-frame window: carried only by frames this plan actually scheduled inside it.

        A region whose span overlaps the window proves nothing while all its frames are skipped -- the
        state route drops frames whose needed fact is already on record -- so a claim credited to such a
        region would read as bought while the plan sends no picture at all.
        """
        inside = [region for region in state_scheduled
                  if any(low - tolerance <= stamp <= high + tolerance
                         for group in region.get("groups") or ()
                         for stamp in group.get("frame_timestamps") or ())]
        sent = ["state region %.3f-%.3f s: frames %s" % (
            region["start"], region["end"],
            ", ".join("%.3f" % stamp for stamp in sorted({
                stamp for group in region.get("groups") or ()
                for stamp in group.get("frame_timestamps") or ()
                if low - tolerance <= stamp <= high + tolerance}))
        ) for region in inside]
        if sent:
            return "scheduled", sent
        saved = ["classified row at %.3f s already answering %s" % (stamp, field)
                 for stamp, row in rows.items()
                 if low - tolerance <= stamp <= high + tolerance and fact_is_answered(row, field)]
        if saved:
            return "saved", saved
        left = ["state window %.3f-%.3f s deferred" % (float(entry["start"]), float(entry["end"]))
                for entry in state_unasked
                if _finite(entry.get("start")) and _finite(entry.get("end"))
                and float(entry["start"]) <= high + tolerance
                and float(entry["end"]) >= low - tolerance]
        if left:
            return "deferred", left
        return "unaccounted", []

    entries, asks = [], {}
    for item in unresolved or []:
        if item.get("type") != "set_unresolved":
            continue
        span = [item.get("at"), item.get("end")]
        for claim in item.get("unresolved_claims") or ():
            name = claim.get("claim")
            route = claim.get("route")
            want = CLAIM_EXECUTOR.get(name)
            instants = [float(value) for value in claim.get("at") or () if _finite(value)]
            windows = [[float(value[0]), float(value[1])] for value in claim.get("windows") or ()
                       if isinstance(value, (list, tuple)) and len(value) == 2
                       and all(_finite(edge) for edge in value)]
            record = {"claim": name, "route": route, "candidate": [
                round(float(value), 6) if _finite(value) else None for value in span],
                "costs_inference": bool(claim.get("costs_inference")),
                "missing": claim.get("missing"), "would_resolve": claim.get("would_resolve"),
                # The claim's own decision points, carried to the ledger so a reviewer can check each one
                # against the frames instead of taking the disposition on trust.
                "at": [round(value, 6) for value in instants],
                "windows": [[round(value[0], 6), round(value[1], 6)] for value in windows]}
            if want and want != route:
                record.update({"disposition": "misrouted", "requests": [], "coverage": [],
                               "unmet": [],
                               "because": "the claim asks {} for evidence and {} is the only executor that "
                                          "has it -- the route on the claim is the defect".format(
                                              route, want)})
                entries.append(record)
                continue
            if route == "user_policy":
                # One ask per fact, not per candidate: `missing` names this candidate's own tally, so
                # keying on it would print ten identical questions and call them ten different ones.
                ask = asks.setdefault(name, {"claim": name, "ask": claim.get("would_resolve"),
                                             "question_count": 1, "candidates": [],
                                             "costs_inference": False,
                                             "routed_to": "the operator or the event listing, once"})
                ask["candidates"].extend(claim.get("applies_to") or [span])
                record.update({"disposition": "user_answer", "requests": [], "coverage": [],
                               "unmet": [],
                               "because": "no frame of this recording names the event's format, so this is "
                                          "one question for the recording, not one per candidate "
                                          "(inference: 0 requests)"})
                entries.append(record)
                continue
            # What the claim actually requires, in the shape its route answers it: a pip row is read at an
            # instant, a nameplate is read somewhere inside a game's own stretch, and a handover is read
            # across the stretch between two games. A stretch claim with no stretches falls back to its
            # instants, which is the only thing left to hold it to.
            # Each point once: a claim that names the same instant twice would be graded twice, and a
            # shortfall on it would look like two pieces of missing footage rather than one.
            seen_points, required = set(), []
            for kind, value in ([("instant", value) for value in instants] if route == "pip_crops"
                                else ([("window", value) for value in windows]
                                      or [("instant", value) for value in instants])):
                marker = (kind, round(float(value), 6) if kind == "instant"
                          else (round(float(value[0]), 6), round(float(value[1]), 6)))
                if marker in seen_points:
                    continue
                seen_points.add(marker)
                required.append((kind, value))
            if not required:
                record.update({"disposition": "deferred", "requests": [], "coverage": [], "unmet": [],
                               "no_evidence_to_buy": True,
                               "because": "the claim names no instant or window this cache can be asked "
                                          "about"})
                entries.append(record)
                continue
            if route not in enabled_routes:
                record.update({"disposition": "deferred", "requests": [],
                               "coverage": [{"needs": label(kind, value), "kind": kind, "value": value,
                                             "status": "deferred", "carried_by": []}
                                            for kind, value in required],
                               "unmet": [label(kind, value) for kind, value in required],
                               "because": "this invocation did not fund the {} route; every point the claim "
                                          "needs is listed unmet at its real price".format(route),
                               "price_if_funded": len(required)})
                entries.append(record)
                continue
            coverage = []
            for kind, value in required:
                if route == "pip_crops":
                    status, evidence = pip_point(value)
                elif route == "header":
                    (status, evidence) = (band_window(value[0], value[1]) if kind == "window"
                                          else band_point(value))
                elif kind == "window":
                    status, evidence = state_point(value[0], value[1], KIND_FACT.get(name))
                else:
                    status, evidence = state_point(value, value, KIND_FACT.get(name))
                coverage.append({"needs": label(kind, value), "kind": kind, "value": value,
                                 "status": status, "carried_by": evidence})
            scheduled = [entry for entry in coverage if entry["status"] == "scheduled"]
            saved = [entry for entry in coverage if entry["status"] == "saved"]
            unasked = [entry for entry in coverage if entry["status"] == "deferred"]
            gaps = [entry for entry in coverage if entry["status"] == "unaccounted"]
            record["coverage"] = coverage
            # The demand and the shortfall, side by side: ``at`` and ``windows`` are what the tracker asked
            # for, ``required`` is what this route can actually be held to, and ``unmet`` is the part of it
            # no send in this plan covers.
            record["required"] = [entry["needs"] for entry in coverage]
            # The remainder, named. This is the field the last round did not have: a claim that got half
            # its evidence said "planned" and the other half disappeared from the artifact. A point covered
            # by a saved answer is **not** unmet -- nothing is left to buy for it, even though it is still
            # inconclusive -- so this list is exactly the footage this run needs and will not get.
            record["unmet"] = [entry["needs"] for entry in coverage
                               if entry["status"] in ("deferred", "unaccounted")]
            record["requests"] = sorted({item for entry in scheduled
                                         for item in entry["carried_by"]})
            record["request_count"] = len(record["requests"])
            record["saved_evidence_reused"] = len(saved)
            record["closes"] = False
            # A point is *carried* when this plan schedules a frame for it or when a saved answer already
            # covers it. Counting only the scheduled half as coverage is how a claim needing 10 s and 30 s
            # read as fully planned while 30 s went unasked; counting a saved answer as unmet would call
            # honest reuse a shortfall, so both halves are carried and neither hides the other.
            carried = len(scheduled) + len(saved)
            if gaps:
                # Neither scheduled, already answered, nor sitting in a route's deferral list: a hole in
                # this accounting, and saying so is the only honest output. An answer that quietly
                # reclassified its own gaps is how a ledger becomes decoration.
                record.update({"disposition": "unaccounted",
                               "because": "no plan entry, saved answer or named deferral accounts for %s"
                                          % ", ".join(entry["needs"] for entry in gaps)})
            elif carried == len(coverage) and scheduled:
                record.update({"disposition": "planned",
                               "because": "every point this claim needs is provided for: %s scheduled by "
                                          "this plan, %s already answered on disk (of %s). Carried is not "
                                          "closed -- only an answer settles it"
                                          % (len(scheduled), len(saved), len(coverage))})
            elif carried and unasked:
                record.update({"disposition": "partial",
                               "because": "of %s points this claim needs, %s are provided for and %s are "
                                          "not (%s); the remainder is named in ``unmet`` rather than folded "
                                          "into a planned claim" % (len(coverage), carried, len(unasked),
                                                                    "; ".join(entry["needs"]
                                                                              for entry in unasked))})
            elif carried:
                record.update({"disposition": "reused",
                               "because": "saved answers already cover every point the claim names, and the "
                                          "claim is still open on them: re-asking the same pictures is not "
                                          "progress"})
            elif unasked:
                record.update({"disposition": "deferred",
                               "because": "the {} route reached its allowance before these points; each one "
                                          "is named in its route's own deferral list".format(route)})
            else:
                beyond = claim.get("beyond_footage") or {}
                reason = ("the candidate's own edge says the tape ends there (%s)"
                          % ", ".join(beyond.get("tape_ends") or [])) if beyond.get("tape_ends") else (
                    "no frame of this recording was sampled on the side the claim needs (%s)"
                    % ", ".join(beyond.get("unsampled") or []) if beyond.get("unsampled") else
                    "the route holds neither a request, a saved answer nor a deferral for these points")
                record.update({"disposition": "deferred", "because": reason,
                               "no_evidence_to_buy": True})
            entries.append(record)

    counts = {}
    for entry in entries:
        counts[entry["disposition"]] = counts.get(entry["disposition"], 0) + 1
    accounted = {"planned", "partial", "reused", "deferred", "user_answer"}
    return {"schema": "claim-work-v2",
            "rules": ["every claim gets exactly one disposition: planned / partial / reused / deferred / "
                      "user_answer",
                      "a claim is planned only when every instant or window it names is scheduled or "
                      "already answered; if any is left unasked the claim is partial and the remainder is "
                      "listed in ``unmet``",
                      "a claim never buys a request another question already planned",
                      "a format claim costs zero inference and is asked once per recording",
                      "reuse of evidence that did not settle the claim is reported inconclusive, never as "
                      "progress",
                      "a state window counts only when the plan scheduled frames inside it, not when a "
                      "region merely overlaps it"],
            "entries": entries, "counts": counts,
            "user_asks": sorted(asks.values(), key=lambda ask: ask["claim"]),
            # Two different questions, and one boolean used to answer them both wrongly: was every claim
            # accounted for, and did every claim get all of the evidence it needs.
            "claims_resolved": bool(entries) and not (
                {entry["disposition"] for entry in entries} - accounted),
            "claims_fully_carried": sum(1 for entry in entries
                                        if entry["disposition"] in ("planned", "reused")),
            # Three different counts, and a name that says which: what the claims asked for, what this
            # plan will actually send for, and what it will not get.
            "points_required": sum(len(entry["coverage"]) for entry in entries),
            "points_scheduled": sum(len([item for item in entry["coverage"]
                                         if item["status"] == "scheduled"]) for entry in entries),
            "points_unmet": sum(len(entry["unmet"]) for entry in entries),
            "inference_for_format_claims": 0}


def plan_investigation(unresolved, cache, duration, observations=None, marks=(),
                       batch_frames=DEFAULT_BATCH_FRAMES, max_requests=DEFAULT_MAX_REQUESTS,
                       max_completion_tokens=DEFAULT_COMPLETION_TOKENS,
                       step=DENSIFY_STEPS[0], max_regions=None, window=None,
                       model=None, endpoint=None, enable_thinking=True,
                       crop_readings=None, identity_questions=(), passes=1, routes=None, header=None):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]], float, Optional[Sequence[Dict[str, Any]]], Sequence[float], int, int, int, float, Optional[int], Optional[Tuple[float, float]], Optional[str], Optional[str], bool, Optional[Dict[str, Any]], Sequence[Dict[str, Any]], int) -> Dict[str, Any]
    """Exactly what a run would send, on which executor, for how many attempts. No model is asked.

    This is the number Andy approves before the machine is occupied, and it is computed from the same
    grouping the run uses, so the plan cannot drift from the work.

    The questions are **split by route**, because one client cannot answer them and pricing them together
    would fund the wrong sends. A win-dot question goes to the crops arm at the instants that can settle a
    round; a clock, banner, end-cue or transition question goes to the state client on its frame grid; an
    identity question goes to the band. A region that asks both a pip and a state question contributes to
    both plans -- and the whole plan shares one allowance, spent in :data:`ROUTE_ORDER`.
    """
    observed = {}  # type: Dict[float, Dict[str, Any]]
    for row in observations or []:
        if _finite(row.get("timestamp")):
            observed[round(row["timestamp"], 3)] = row
    question_identity = {
        "prompt_version": ROUND_FACTS_PROMPT_VERSION,
        "model": model or local_model.default_model(),
        "endpoint": str(endpoint or local_model.default_endpoint()),
        "enable_thinking": bool(enable_thinking),
        "batch_frames": int(batch_frames),
        "step_seconds": float(step),
        "max_completion_tokens": int(max_completion_tokens),
        # The other two routes' identities belong to the plan too: a run that reloaded a group answered
        # under another crop geometry or another band prompt would be crediting one question's answer to
        # another question, which is the failure this whole module exists to prevent.
        "pip_prompt_version": ROUTE_PROMPT["pip_crops"],
        "pip_arm_identity": boundary_pips.arm_identity("crops"),
        "header_prompt_version": ROUTE_PROMPT["header"],
    }
    # The pip route first, because its allowance is what is left after it is spent and it is the route
    # whose answers decide games. It is planned on the *observations*, not on the queue's regions: a
    # decision window is placed by the round end, and the K.O. flash and the victory close-up mean the
    # frames that settle it are three specific ones rather than a grid.
    enabled = route_filter(routes)
    # Every route is priced, funded or not. An unfunded route reports zero *planned sends* and its real
    # bill in ``requests_unfunded``, because the two questions a reader asks are different: "what will this
    # run spend" and "what will the next approval have to cover", and a zero that answers both answers
    # neither.
    pip_plan = pip_requests(observations or [], cache, window,
                            pip_answered_frames(observations, crop_readings),
                            max_requests if "pip_crops" in enabled else 500, model=model,
                            enabled="pip_crops" in enabled, unresolved=unresolved)
    if "pip_crops" not in enabled:
        # Keep the price, drop the promise: ``requests_planned`` is what this run would send, and an
        # unfunded route sends nothing. The bill it would have run moves to ``requests_unfunded`` and into
        # the deferral list, where the next approval can be written from it.
        pip_plan["requests_unfunded"] = pip_plan["requests_planned"]
        pip_plan["images_unfunded"] = pip_plan["images_total"]
        priced_crops = [dict(entry, route="pip_crops") for entry in pip_plan["requests"]]
        pip_plan["deferred"] = priced_crops + pip_plan["deferred"]
        pip_plan["requests"], pip_plan["requests_planned"], pip_plan["images_total"] = [], 0, 0
    allowance = max_requests - pip_plan["requests_planned"]
    pip_enabled = "pip_crops" in enabled
    pip_only = []  # type: List[Dict[str, Any]]
    regions = candidate_regions(unresolved, duration, observations, marks, limit=max_regions,
                                window=window)
    # The set layer's own whole-frame questions, merged into the same queue and the same allowance. They
    # rank last on purpose: a continuity probe never displaces a missing winner.
    extra = continuity_regions(unresolved, duration, marks, window)
    if extra:
        regions = list(regions) + extra
        _merge_overlapping(regions)
        regions.sort(key=lambda r: (0 if r["mark_near"] else 1, r["priority"], r["start"]))
        regions = regions[:max_regions] if max_regions else regions
    planned = []
    requests = 0
    deferred = []  # type: List[Dict[str, Any]]
    # The regions are walked even when the allowance is already gone, because a deferral that is not
    # written down is indistinguishable from a question nobody asked. When the crop route spends the whole
    # budget, the state route's work still has to appear in the plan by name -- that list is what the next
    # approval is priced from.
    unplanned = []
    state_allowance = allowance if "state" in enabled else 500
    if "state" not in enabled:
        # Priced as if it were funded, then reported as unfunded: the region list is the bill, and dropping
        # it would hide work from the next approval rather than deferring it.
        unplanned = list(regions)
        regions = []
    for region in list(regions):
        remaining = state_allowance - requests
        if remaining <= 0:
            deferred.append(region)
            regions.remove(region)
            continue
        # A frame is skipped only when it already answers every question this region asks. Two
        # questions in one region need the union of their gaps, not the intersection of what has
        # ever been looked at.
        wanted = [KIND_FACT.get(kind) for kind in region["kinds"]]
        # ``win_dots`` is asked on the crop route, so it never decides which frames the state client is
        # sent: a region whose only gap is the pip row contributes no state request at all, and the whole
        # frame is not looked at a second time to obtain a value that would only be a diagnostic.
        state_wanted = [field for field in wanted if field and field != "win_dots"]
        if not state_wanted:
            # A region whose only gap is the pip row is the crop route's work. It is not this route's
            # budget, but it is the plan's responsibility: when the crop route is unfunded or has run out,
            # the region has to surface as a named deferral, or the plan reads as though the question had
            # been asked somewhere it was not.
            if not pip_enabled:
                pip_only.append(region)
            continue
        answered = sorted(ts for ts, observation in observed.items()
                          if all(fact_is_answered(observation, field)
                                 for field in state_wanted if field))
        frames = frames_for_region(cache, region, step, answered)
        groups = group_frames(frames, batch_frames)
        dropped = groups[remaining:]
        groups = groups[:remaining]
        if dropped:
            # The allowance ended partway through this region. The frames it did not reach stay in the
            # report as unfinished work; otherwise a truncated plan looks finished and a run that got
            # nowhere near the footage reports "complete".
            deferred.append({"start": dropped[0][0]["timestamp"], "end": region["end"],
                             "kinds": region["kinds"], "groups": len(dropped),
                             "frames": sum(len(g) for g in dropped), "partial_region": True})
        if not groups:
            continue
        requests += len(groups)
        asked = sorted({field for field in state_wanted if field})
        also_pip = bool([field for field in wanted if field == "win_dots"])
        # Named on the region, because it is the next question a reader of the plan asks: this frame grid
        # settles clock and cue facts, and the pip row the same region needs is asked elsewhere, by the
        # route that can measure it.
        planned.append({
            "index": len(planned), "start": region["start"], "end": region["end"],
            "kinds": region["kinds"], "priority": region["priority"],
            "mark_near": region["mark_near"], "step_seconds": step,
            "asked_fields": asked,
            "route": "state", "routes": ["state"],
            "answer_grade": [ROUTE_EVIDENCE["state"][1]],
            "pip_question_elsewhere": also_pip,
            "header_route": HEADER_ROUTE_NOTE,
            "pip_answer_grade_on_this_route": "diagnostic: the state client is asked for the dot row and "
                                              "its answer cannot become a win_dots value",
            "frames": len(groups) * batch_frames if groups else 0,
            "groups": [{"group": position,
                        "route": "state",
                        "fingerprint": _fingerprint(group, question_identity),
                        "frame_paths": [f["path"] for f in group],
                        "frame_timestamps": [round(f["timestamp"], 3) for f in group]}
                       for position, group in enumerate(groups)],
        })
    deferred_regions = ([r for r in deferred if not r.get("partial_region")] + unplanned
                        + [dict(region, route="pip_crops") for region in pip_only])
    unfinished = [r for r in deferred if r.get("partial_region")]
    header_plan = header_requests(identity_questions, cache,
                                  max(0, allowance - requests) if "header" in enabled else 500,
                                  model=model, enabled="header" in enabled)
    if "header" not in enabled:
        header_plan["requests_unfunded"] = header_plan["requests_planned"]
        header_plan["images_unfunded"] = header_plan["images_total"]
        header_plan["deferred"] = [dict(entry, route="header")
                                   for entry in header_plan["requests"]] + header_plan["deferred"]
        header_plan["requests"], header_plan["requests_planned"], header_plan["images_total"] = [], 0, 0
    state_requests = sum(len(r["groups"]) for r in planned)
    total = (pip_plan["requests_planned"] + state_requests
             + header_plan["requests_planned"])
    # Last, because it reads what the three routes decided: every point of every claim the set layer says
    # it cannot settle is matched against the frames this plan actually scheduled, against the saved answers
    # it reused, or against the deferral lists that name what was left. A claim that got half its evidence
    # reports ``partial`` with the remainder in ``unmet`` -- overlap used to be credited as coverage, which
    # is how a capped run could mark a ten-game claim "planned" while nine of them went unasked.
    claims = claim_work(unresolved, pip_plan, header_plan, planned, observations=observations,
                        crop_readings=crop_readings, header=header, enabled_routes=enabled,
                        state_unasked=list(deferred_regions) + list(unfinished))
    images = (pip_plan["images_total"]
              + sum(len(r["groups"]) * batch_frames for r in planned)
              + header_plan["images_total"])
    return {
        "status": "plan",
        "kind": "bounded_typed_pass",
        "algorithm": "game-facts-investigation-v2",
        "pass": int(passes),
        # What a run does with what it learns: each pass re-plans from the evidence the last pass
        # produced, so an answer that resolves one question and uncovers another is dispatched to the
        # route that owns the new one. A pass stops for one of four reasons and says which.
        "adaptive_progression": "passes re-plan from merged evidence; a route is chosen per question, "
                                "never inherited from the last pass",
        "stop_conditions": ["supported_outcome: no question remains that this recording can answer",
                            "named_deferral: a question is left unasked and reported with its route",
                            "no_new_evidence: a pass answered nothing the last pass did not already have",
                            "budget: the shared ledger's cap or deadline is spent"],
        "question": ROUND_FACT_PROMPT,
        "question_identity": question_identity,
        "fields": list(ROUND_FACT_FIELDS),
        # Every claim the set layer raised, with the request that carries it, the saved answer that makes
        # it unnecessary, or the reason nobody is being asked. `claims_resolved` is false the moment a
        # claim arrives at an executor that cannot answer it -- a route that was never wired up reports as
        # a defect here rather than as a quiet zero.
        "claims": claims,
        "claims_resolved": claims["claims_resolved"],
        "user_asks": claims["user_asks"],
        "dispatch": {
            "order": list(ROUTE_ORDER),
            "routes": [
                {"route": "pip_crops", "client": ROUTE_CLIENT["pip_crops"],
                 "enabled": "pip_crops" in enabled,
                 "requests_unfunded": pip_plan.get("requests_unfunded", 0),
                 "unfunded_because": None if "pip_crops" in enabled else ROUTE_UNFUNDED["pip_crops"],
                 "prompt_version": ROUTE_PROMPT["pip_crops"],
                 "requests_planned": pip_plan["requests_planned"],
                 "images_total": pip_plan["images_total"],
                 "windows": pip_plan["windows"], "reused": pip_plan["reused"],
                 "deferred": pip_plan["deferred"],
                 "claims_stopped": pip_plan.get("claims_stopped", []),
                 "claims_continuing": pip_plan.get("claims_continuing", []),
                 "completion_tokens_per_request": pip_plan["completion_tokens_per_request"],
                 "evidence_class": pip_plan["evidence_class"],
                 "why_this_route": pip_plan["why_this_route"],
                 "reused_because": pip_plan.get("reused_because", "")},
                {"route": "state", "client": ROUTE_CLIENT["state"],
                 "enabled": "state" in enabled,
                 "requests_unfunded": 0 if "state" in enabled else state_requests,
                 "unfunded_because": None if "state" in enabled else ROUTE_UNFUNDED["state"],
                 "prompt_version": ROUND_FACTS_PROMPT_VERSION,
                 "requests_planned": state_requests,
                 "images_total": sum(len(r["groups"]) * batch_frames for r in planned),
                 "batch_frames": batch_frames, "step_seconds": step,
                 "deferred": unfinished + deferred_regions,
                 "completion_tokens_per_request": max_completion_tokens,
                 "evidence_class": ROUTE_EVIDENCE["state"][0],
                 "why_this_route": ROUTE_EVIDENCE["state"][1],
                 "pip_answers_from_this_route": "diagnostic only"},
                {"route": "header", "client": ROUTE_CLIENT["header"],
                 "enabled": "header" in enabled,
                 "requests_unfunded": header_plan.get("requests_unfunded", 0),
                 "unfunded_because": None if "header" in enabled else ROUTE_UNFUNDED["header"],
                 "prompt_version": ROUTE_PROMPT["header"],
                 "requests_planned": header_plan["requests_planned"],
                 "images_total": header_plan["images_total"], "reused": header_plan["reused"],
                 "deferred": header_plan["deferred"], "unrouted": header_plan["unrouted"],
                 "completion_tokens_per_request": header_plan["completion_tokens_per_request"],
                 "evidence_class": header_plan["evidence_class"],
                 "why_this_route": header_plan["why_this_route"],
                 "never_answers": header_plan["never_answers"]}],
            "requests_planned": total,
            "images_total": images,
            # Each route's own ceiling, added: the state client's 8,000 tokens is not the crop client's
            # 3,000, and quoting one figure for three questions hides which part of the bill is big.
            "completion_tokens_ceiling": (
                pip_plan["requests_planned"] * pip_plan["completion_tokens_per_request"]
                + state_requests * max_completion_tokens
                + header_plan["requests_planned"]
                * header_plan["completion_tokens_per_request"]),
            "one_ledger_for_all_routes": True,
            # A zero that means "nothing to ask" and a zero that means "nobody funded this executor" are
            # different facts, and a reader of a budget cannot tell them apart from a count.
            "funded": list(enabled),
            "unfunded_routes": [route for route in ROUTE_ORDER if route not in enabled],
        },
        "pip_requests": pip_plan["requests"],
        "header_requests": header_plan["requests"],
        "routes": {route: sum(1 for region in planned if region["route"] == route)
                   for route in sorted({region["route"] for region in planned})},
        "route_note": HEADER_ROUTE_NOTE,
        "duration": float(duration),
        "window": [float(window[0]), float(window[1])] if window else None,
        "cache_frames": len(cache),
        "regions": planned,
        "region_count": len(planned),
        "regions_deferred_for_budget": len(deferred_regions),
        "regions_partially_planned": unfinished,
        "deferred_regions": [{"start": r["start"], "end": r["end"], "kinds": r["kinds"],
                              "route": r.get("route", "state")} for r in deferred_regions[:40]],
        "groups_total": sum(len(r["groups"]) for r in planned),
        "frames_total": sum(len(g["frame_timestamps"]) for r in planned for g in r["groups"]),
        "request_allowance": max_requests,
        # The total a run would spend, across every route. Before the routes were dispatched this counted
        # only the state client's batches, which is how a plan could price 7 requests for a window whose
        # real bill was 43.
        "requests_planned": total,
        "state_requests_planned": state_requests,
        "pip_requests_planned": pip_plan["requests_planned"],
        "header_requests_planned": header_plan["requests_planned"],
        "completion_tokens_per_request": max_completion_tokens,
        "completion_tokens_worst_case": max_completion_tokens * state_requests,
        "batch_frames": batch_frames,
        "step_seconds": step,
        "stops_when": ["request_allowance_reached", "budget_exhausted_is_a_result_not_a_crash",
                       "every_group_persisted_before_the_next_is_sent",
                       "a_transport_uncertain_failure_stops_rather_than_retrying",
                       "a_refused_attempt_writes_no_answer_file"],
    }


#: Fields that count as an answer. A reply object carrying none of them said nothing about the frame.
ANSWER_FIELDS = ("round_clock", "round_call", "round_end", "result_transition",
                 "win_dots_p1", "win_dots_p2", "state")
#: Screen states the question allows, kept in step with the classifier's own vocabulary.
ALLOWED_SCREEN_STATES = frozenset(boundary_media.ALLOWED_STATES) | {"unknown"}


def validate_group_reply(reply, group):
    # type: (Any, Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]
    """Reject a reply that does not answer about exactly the frames that were sent, with real types.

    An object carrying only an ``id`` is not an answer, and neither is the string ``"false"`` where a
    boolean was asked for: ``bool("false")`` is True, so a lax check turns a model saying *no* into
    evidence that it said *yes*. Nulls are accepted everywhere -- an occluded pip row is a real
    result -- but a value of the wrong shape is not a null and must not be coerced away silently.
    """
    errors = []  # type: List[str]
    if not isinstance(reply, list):
        return [], ["reply is not a JSON array"]
    wanted = [frame.get("id") for frame in group if frame.get("id") is not None]
    by_id = {}
    for item in reply:
        if not isinstance(item, dict):
            errors.append("reply item is not an object")
            continue
        by_id[item.get("id")] = item
    if wanted and sorted(str(i) for i in by_id) != sorted(str(i) for i in wanted):
        errors.append("reply ids {} do not match requested ids {}".format(
            sorted(str(i) for i in by_id), sorted(str(i) for i in wanted)))
    out = []
    for frame in group:
        item = by_id.get(frame.get("id"))
        if item is None:
            errors.append("no answer for frame id {}".format(frame.get("id")))
            continue
        where = "frame {}".format(frame.get("id"))
        if not (set(item) & set(ANSWER_FIELDS)):
            errors.append("{} replied with no answer field (only {})".format(
                where, sorted(str(key) for key in item)))
            continue
        clock = item.get("round_clock")
        if clock is not None and (isinstance(clock, bool) or not isinstance(clock, int)
                                  or not 0 <= clock <= 99):
            errors.append("{} round_clock {!r} is not an integer 0-99 or null".format(
                where, clock))
            clock = None
        call = item.get("round_call")
        if call is not None and (isinstance(call, bool)
                                 or not (call == "final" or (isinstance(call, int)
                                                             and 1 <= call <= MAX_ROUND_CALL))):
            errors.append("{} round_call {!r} is not an integer 1-{} or 'final' or null".format(
                where, call, MAX_ROUND_CALL))
            call = None
        round_end = item.get("round_end")
        if round_end is not None and round_end not in ("ko", "time_up"):
            errors.append("{} round_end {!r} is not 'ko', 'time_up' or null".format(
                where, round_end))
            round_end = None
        transition = item.get("result_transition")
        if transition is not None and not isinstance(transition, bool):
            errors.append("{} result_transition {!r} is not a boolean or null".format(
                where, transition))
            transition = None
        dots = []
        for side, key in (("p1", "win_dots_p1"), ("p2", "win_dots_p2")):
            value = item.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or not 0 <= value <= 3):
                errors.append("{} win dots {} {!r} is not an integer 0-3 or null".format(
                    where, side, value))
                value = None
            dots.append(value)
        state = item.get("state")
        if state is not None and state not in ALLOWED_SCREEN_STATES:
            errors.append("{} state {!r} is not a known screen state".format(where, state))
        confidence = item.get("confidence")
        if confidence is not None and (isinstance(confidence, bool)
                                       or not isinstance(confidence, (int, float))
                                       or not 0.0 <= confidence <= 1.0):
            errors.append("{} confidence {!r} is not a number 0-1 or null".format(where, confidence))
            confidence = None
        characters = item.get("characters")
        if characters is not None and (not isinstance(characters, list)
                                       or not all(isinstance(c, str) for c in characters)):
            errors.append("{} characters {!r} is not a list of strings or null".format(
                where, characters))
            characters = None
        evidence = item.get("evidence")
        out.append({"timestamp": frame["timestamp"], "frame": frame["path"],
                    "state": state or "unknown", "confidence": confidence,
                    "round_clock": clock, "round_call": call, "round_end": round_end,
                    "result_transition": bool(transition),
                    # The pip answer is asked in the same breath as the clock, because the two share a
                    # frame and the framing experiment needs both. It is written down as a *diagnostic*
                    # and never as ``win_dots``: this route reads a 640x360 whole frame, the reading that
                    # lost the measured comparison against the crop arm, and an answer that could name a
                    # winner here is how a game the crop row says was won 1-3 gets recorded 0-0.
                    "win_dots_diagnostic": dots,
                    "pip_evidence_class": "unvalidated_pip_diagnostic",
                    "pip_diagnostic_reason": ROUND_FACTS_PIP_GRADE,
                    "characters": characters or [],
                    "evidence": evidence if isinstance(evidence, str) else "",
                    "fact_source": STATE_ROUTE})
    return out, errors


#: What the whole-frame route is allowed to settle, and what it only ever guesses at.
VALIDATED_STATE_FIELDS = ("round_clock", "round_call", "round_end", "result_transition", "state",
                          "characters", "screen_state")
STATE_ROUTE = "round_facts"
ROUND_FACTS_PIP_GRADE = (
    "read from a whole 640x360 frame by the state route: the same row of circles the cache-arm crop "
    "measures, under no frozen geometry and with no control arm. A diagnostic about the model's eyes, "
    "never a score.")


class InvestigationRun:
    """Runs one plan, persisting every group so an interrupted run re-asks nothing."""

    def __init__(self, client, output_dir, max_requests=DEFAULT_MAX_REQUESTS,
                 on_group=None):
        # type: (Any, Any, int, Optional[Callable[[Dict[str, Any]], None]]) -> None
        self.client = client
        self.out = Path(output_dir)
        self.max_requests = max_requests
        self.on_group = on_group
        self.out.mkdir(parents=True, exist_ok=True)
        self.requests_dir = self.out / "requests"
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.observations = []  # type: List[Dict[str, Any]]
        self.groups_done = 0
        self.groups_failed = 0
        self.groups_resumed = 0
        self.groups_reused_failed = 0
        # Groups whose answer was actually usable. Distinct from groups_done, which counts every group
        # the run touched, and from attempts, which counts requests: three different numbers that a
        # single "requests used" figure used to conflate.
        self.groups_answered = 0
        # Groups the shared ledger refused. They are neither done nor failed: they are work the run was
        # not entitled to, and a run that stopped for budget has to say so instead of looking finished.
        self.groups_deferred_by_ledger = 0
        self.stopped = None  # type: Optional[str]
        self.error = None  # type: Optional[str]
        # A group whose request died is attempted but never completes, so the two are
        # counted apart: "attempted" covering both once read as "finished".
        self.groups_failed_request = 0

    def _path(self, fingerprint):
        # type: (str) -> Path
        return self.requests_dir / "group-{}.json".format(fingerprint)

    def run(self, plan, cache_by_path=None):
        # type: (Dict[str, Any], Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]
        """Spend at most ``max_requests`` on the plan's groups, in priority order.

        A saved group is reused instead of re-asked, which is what makes a cancelled pilot cheap to
        resume and a finished one reproducible. A group whose reply fails validation is kept as a
        failure record with the reply that failed: an unusable answer is evidence about the request,
        not a reason to try forever.
        """
        try:
            self._walk(plan, cache_by_path or {})
        except ValueError as exc:
            # An endpoint that refuses or dies mid-run is a result to report, not an exception that
            # discards the groups already paid for. Everything persisted stays, the record says what
            # was reached, and a resume picks up from there.
            self.error = str(exc)
            self.groups_failed_request += 1
            self.stopped = "request_failed"
        result = self.summarise(plan)
        (self.out / "investigation.json").write_text(
            json.dumps({"plan": {k: v for k, v in plan.items() if k != "regions"},
                        "regions_requested": len(plan["regions"]),
                        "result": result,
                        "observations": self.observations}, indent=2, sort_keys=True),
            encoding="utf-8")
        return result

    def summarise(self, plan):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """The honest shape of a finished, stopped or failed run."""
        return {"status": "stopped" if self.stopped else "complete",
                "stopped_reason": self.stopped,
                "error": self.error,
                "requests_sent": self.requests_sent(),
                "request_allowance": self.max_requests,
                "groups_completed": self.groups_done,
                "groups_failed_request": self.groups_failed_request,
                "groups_resumed": self.groups_resumed,
                "groups_answered": self.groups_answered,
                "groups_deferred_by_ledger": self.groups_deferred_by_ledger,
                "groups_reused_with_errors": self.groups_reused_failed,
                "groups_failed_validation": self.groups_failed,
                "observations_written": len(self.observations),
                "not_attempted": max(0, plan.get("groups_total", 0) - self.groups_done)}

    def _walk(self, plan, index):
        # type: (Dict[str, Any], Dict[str, Dict[str, Any]]) -> None
        for region in plan["regions"]:
            for group in region["groups"]:
                if self.requests_sent() >= self.max_requests:
                    self.stopped = "request_allowance_reached"
                    return
                frames = [dict(index.get(path, {"path": path, "id": None, "timestamp": stamp}),
                               id=fid)
                          for path, stamp, fid in zip(group["frame_paths"],
                                                      group["frame_timestamps"],
                                                      range(1, len(group["frame_paths"]) + 1))]
                saved = self._path(group["fingerprint"])
                if saved.is_file():
                    record = json.loads(saved.read_text(encoding="utf-8"))
                    # A stored group is only evidence if it came back clean. Reloading a failed
                    # semantic reply would turn a rejected answer into an observation on the second
                    # run, so it is carried as a known failure and never merged. Re-asking it is not
                    # automatic: a group the model cannot answer would otherwise spend the allowance
                    # on every resume, so it is reported and left for the caller to decide.
                    self.groups_done += 1
                    if record.get("errors"):
                        self.groups_reused_failed += 1
                        continue
                    self.observations.extend(record.get("observations", []))
                    self.groups_resumed += 1
                    self.groups_answered += 1
                    continue
                try:
                    # Named for the ledger before the socket opens, so an attempt this run paid for can
                    # be matched to the answer file that proves it.
                    self.client.ledger_request_tag = _group_tag(group["fingerprint"])
                    reply = self.client.classify(frames)
                except boundary_budget.BudgetStopped as exc:
                    # The shared ledger said stop. No group file is written: a deferral is not an answer,
                    # and saving one would let a later resume reload it as work that was done.
                    self.stopped = exc.reason
                    self.error = str(exc)
                    self.groups_deferred_by_ledger += 1
                    return
                observations, errors = validate_group_reply(reply, frames)
                self._write(saved, group, frames, reply, observations, errors)
                if errors:
                    self.groups_failed += 1
                else:
                    self.observations.extend(observations)
                    self.groups_answered += 1
                self.groups_done += 1
                if self.on_group:
                    self.on_group({"region": region["start"], "fingerprint": group["fingerprint"],
                                   "errors": errors, "observations": len(observations)})

    def requests_sent(self):
        # type: () -> int
        return int(getattr(self.client, "calls", self.groups_done))

    def _write(self, path, group, frames, reply, observations, errors):
        # type: (Path, Dict[str, Any], Sequence[Dict[str, Any]], Any, Sequence[Dict[str, Any]], Sequence[str]) -> None
        path.write_text(json.dumps({"fingerprint": group["fingerprint"],
                                    "frame_timestamps": [f["timestamp"] for f in frames],
                                    "frame_paths": [f["path"] for f in frames],
                                    "question": ROUND_FACT_PROMPT,
                                    "reply": reply, "observations": observations,
                                    "errors": errors}, indent=2, default=str), encoding="utf-8")


def merge_observations(base, new):
    # type: (Sequence[Dict[str, Any]], Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]
    """One observation per timestamp, merged field by field, with the pip row graded.

    Timestamp-level replacement is what made a second investigation pass useless: the first pass had
    recorded a clock, so a later reading of the win dots at the same instant was thrown away, and a run
    that spent requests to learn something could not keep it. Each field is decided on its own -- a
    blank is filled, a stated value corrects a prose guess, and a genuine disagreement is kept in
    ``field_conflicts`` rather than silently overwriting either side.

    The pip row is the one field the merge does **not** let the later reading win. Its value is decided
    by the class of the reading, not by arrival order: a crop answer with frozen geometry outranks a
    whole-frame or band answer of the same instant, in both directions. Without that rule the command's
    own state route could overwrite the evidence it was told to treat as authoritative -- a crop row
    reading [1, 3] replaced by a full-frame [0, 0] is a won game turned into a lost one -- and a winner
    could be created out of a diagnostic on any frame nobody ever cropped.
    """
    by_time = {}  # type: Dict[float, Dict[str, Any]]
    for row in base:
        if _finite(row.get("timestamp")):
            by_time[round(row["timestamp"], 3)] = grade_pip_row(dict(row))
    for row in new:
        if not _finite(row.get("timestamp")):
            continue
        key = round(row["timestamp"], 3)
        merged = dict(by_time.get(key) or {})
        conflicts = list(merged.get("field_conflicts") or [])
        incoming = grade_pip_row(dict(row))
        for field, value in incoming.items():
            if field == "timestamp" or value is None:
                continue
            if field in PIP_EVIDENCE_FIELDS or field in PIP_DIAGNOSTIC_FIELDS:
                continue
            if field in PROVENANCE_FIELDS:
                merged[field] = merge_provenance(merged.get(field), value)
                continue
            if field in ("win_dots", "win_dots_drawn", "win_dots_confidence", "pip_row_drawn"):
                # The pip row is decided below, by grade; the generic slot rule must not touch it.
                continue
            previous = merged.get(field)
            if previous is None or previous == [] or previous == [None, None]:
                merged[field] = value
                continue
            if _same_fact(previous, value):
                continue
            filled, slot_clashes = _fill_slots(previous, value)
            if slot_clashes:
                # Both readings say something about the same side and they differ. The later pass
                # looked at this frame for this field on purpose, so it wins on that side, and both
                # readings stay in the conflict record.
                conflicts.append({"field": field, "previous": previous, "later": value,
                                  "sides": slot_clashes})
            merged[field] = filled
        merged = carry_pip_reading(merged, incoming)
        merged["timestamp"] = row["timestamp"]
        if conflicts:
            merged["field_conflicts"] = conflicts
        by_time[key] = merged
    return [by_time[key] for key in sorted(by_time)]


#: Fields that say where a value came from rather than what it is. They are merged by appending, never by
#: replacing: a diagnostic that overwrote ``fact_source`` on a row carrying a crop measurement would leave
#: that row claiming a whole-frame origin, and the next thing to grade it would demote the measurement the
#: row still holds -- thrown away twice, once by the grade and once by the provenance that recorded it.
PROVENANCE_FIELDS = ("fact_source", "source")


def merge_provenance(previous, later):
    # type: (Any, Any) -> str
    """Union two ``+``-joined source lists, oldest first, in the form the canonical merge writes."""
    parts = [part for part in re.split(r"\+", str(previous or "")) if part]
    for part in [item for item in re.split(r"\+", str(later or "")) if item]:
        if part not in parts:
            parts.append(part)
    return "+".join(parts)


#: The bookkeeping fields a pip reading travels with, plus the demoted diagnostics. None of them is
#: merged slot by slot like a fact: the grade decides which one is the reading, and the merge below is
#: where that decision is made.
PIP_EVIDENCE_FIELDS = ("pip_evidence_class", "pip_diagnostic_reason", "pip_reading_conflicts")
PIP_DIAGNOSTIC_FIELDS = tuple(field + boundary_evidence.DIAGNOSTIC_PIP_SUFFIX
                              for field in boundary_evidence.PIP_FIELDS)


def grade_pip_row(row):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """The canonical grade of this row's pip reading (see
    :func:`tekken_vod_helper.boundary_evidence.grade_pip_row`).

    Re-exported rather than reimplemented. A row that arrives with pip values from the whole-frame or
    band route leaves this function with **no** ``win_dots`` at all: the numbers survive as
    ``win_dots_diagnostic``, which the tracker never reads.
    """
    return boundary_evidence.grade_pip_row(row)


def be_source_of(merged, kept):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """The source that made the kept pip value evidence, out of the row's accumulated provenance."""
    parts = [part for part in re.split(r"\+", str(merged.get("fact_source") or "")) if part]
    for part in parts:
        if boundary_evidence.pip_grade(part) == "validated":
            return part
    return merged.get("fact_source") or boundary_evidence.VALIDATED_PIP_SOURCE


def carry_pip_reading(merged, incoming):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """Let the grade, not the arrival order, decide the pip row -- and keep the loser visible.

    Two readings of the *same* grade are merged slot by slot by the caller, which is the rule that lets a
    frame reading the left row and a frame reading the right row combine, and turns a genuine same-slot
    disagreement into a ``field_conflicts`` entry. This function decides the one question that rule cannot:
    what happens when the two readings have **different** grades.

    A validated (cache-arm crop) reading is the row's value, and it is already in place, because every row
    was graded on the way in and no diagnostic keeps a ``win_dots`` field. A diagnostic never *creates*
    one: on a frame nobody has ever cropped, the whole-frame numbers stay a diagnostic and the tracker
    sees an unread pip row, which is the true state of the evidence. Where both exist, the disagreement is
    recorded with both sides rather than resolved by whoever arrived last.

    ``incoming`` has already been through :func:`grade_pip_row`, so a diagnostic arrives with its numbers
    under the ``*_diagnostic`` names and no ``win_dots`` at all.
    """
    if incoming.get("pip_evidence_class") == "validated_pip_evidence":
        # Two measurements of one frame combine slot by slot, exactly as they did before grades existed:
        # a crop that could read the left row and a crop that could read the right row are two halves of
        # one answer, and neither is a clash. Only a slot that both sides claim is recorded as a conflict,
        # and there the earlier measurement is kept -- the request identity is the same, so a second crop
        # of one frame is a re-read, not new information about the frame.
        arriving_pip = {field: incoming[field] for field in boundary_evidence.PIP_FIELDS
                        if incoming.get(field) is not None}
        if not arriving_pip:
            return merged
        for field, value in arriving_pip.items():
            previous = merged.get(field)
            if previous is None or previous == [None, None]:
                merged[field] = value
                continue
            if _same_fact(previous, value):
                continue
            filled, clashes = _fill_slots(previous, value)
            if clashes:
                boundary_evidence.record_pip_conflict(merged, value,
                                                      incoming.get("fact_source") or "crop reading",
                                                      be_source_of(merged, {"win_dots": previous}))
                merged.setdefault("field_conflicts", []).append(
                    {"field": field, "previous": previous, "later": value, "sides": clashes})
            merged[field] = filled
        merged["pip_evidence_class"] = "validated_pip_evidence"
        return merged
    arriving = {field: incoming.get(field + boundary_evidence.DIAGNOSTIC_PIP_SUFFIX)
                for field in boundary_evidence.PIP_FIELDS}
    arriving = {field: value for field, value in arriving.items() if value is not None}
    if not arriving:
        return merged
    kept = {field: merged.get(field) for field in boundary_evidence.PIP_FIELDS
            if merged.get(field) is not None}
    if kept and not _same_fact(kept.get("win_dots"), arriving.get("win_dots")):
        # The kept side is named by the grade that protected it, not by ``merged["fact_source"]``: the
        # row's provenance now lists every route that has touched it, and quoting that would credit the
        # rejected reading with keeping its own value.
        boundary_evidence.record_pip_conflict(merged, arriving.get("win_dots"),
                                              incoming.get("fact_source") or "diagnostic route",
                                              be_source_of(merged, kept))
    for field, value in arriving.items():
        merged[field + boundary_evidence.DIAGNOSTIC_PIP_SUFFIX] = value
    # The class describes the row's pip *value*, not the last reading that touched it: a row carrying a
    # crop measurement alongside a rejected guess is still evidence, and stamping it unvalidated would
    # tell the next reader to distrust the one number that is trustworthy.
    if not kept:
        merged["pip_evidence_class"] = "unvalidated_pip_diagnostic"
        merged["pip_diagnostic_reason"] = incoming.get("pip_diagnostic_reason")
    return merged


def _fill_slots(previous, later):
    # type: (Any, Any) -> Tuple[Any, List[str]]
    """Combine two readings, one slot at a time, and name the slots where they clash.

    Side-indexed fields (win dots, characters) are read one side at a time: a frame whose left pip row
    is legible and whose right is occluded carries ``[3, None]``, and the complementary ``[None, 2]``
    from the next frame completes it rather than fighting it. Only a slot both readings actually fill
    with *different* values is a disagreement.
    """
    if not (isinstance(previous, (list, tuple)) and isinstance(later, (list, tuple))):
        return later, []
    size = max(len(previous), len(later))
    names = ("p1", "p2")
    combined = []
    clashes = []  # type: List[str]
    for index in range(size):
        one = previous[index] if index < len(previous) else None
        two = later[index] if index < len(later) else None
        combined.append(two if two is not None else one)
        if one is not None and two is not None and one != two:
            clashes.append(names[index] if index < len(names) else str(index))
    return combined, clashes


def _same_fact(one, two):
    # type: (Any, Any) -> bool
    """Whether two readings of one field agree, compared slot by slot.

    A side-pair is positional: ``[3, None]`` is "left player has three, right unread" and
    ``[None, 3]`` is the opposite reading of the same frame. Dropping the nulls and comparing the
    remainders calls those two agreement, which would let a half-read frame veto the conflict report
    on the other half. Unknown stays unknown and in its own slot, and a real disagreement is reported.
    """
    if one == two:
        return True
    if isinstance(one, (list, tuple)) and isinstance(two, (list, tuple)):
        if len(one) != len(two):
            return False
        return all(a == b for a, b in zip(one, two))
    return False


class RoundFactsClient(boundary_media.VisionClient):
    """Asks the game's own HUD question instead of the state-classification question.

    A subclass, not a new client, so the request budget, the retry-on-unusable-reply loop and the
    provenance records stay the ones already trusted. Only the prompt and its parser differ, and the
    prompt version differs loudly: a round-facts batch must never be replayable as classifier output.
    """

    prompt_version = ROUND_FACTS_PROMPT_VERSION
    # Six frames x ten fields with reasoning enabled needs more headroom than the classifier's 6,000;
    # a reasoning model that spends the whole budget thinking returns no content at all.
    request_max_tokens = DEFAULT_COMPLETION_TOKENS
    request_reasoning_effort = "low"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The cutting policy governs clip edges. It is not part of this question, and leaving its
        # hash on the instance would let a provenance record imply text that was never sent.
        self._policy_snapshot = ROUND_FACT_PROMPT
        self._policy_hash = hashlib.sha256(ROUND_FACT_PROMPT.encode("utf-8")).hexdigest()[:16]

    def _build_content(self, policy_text, frames, crop_regions=None):
        # type: (str, Sequence[Dict[str, Any]], Optional[bool]) -> List[Dict[str, Any]]
        listing = "\n".join("id={} t={}s".format(f["id"], f["timestamp"]) for f in frames)
        content = [{"type": "text",
                    "text": ROUND_FACT_PROMPT + "Frames to read:\n" + listing + "\n"}]
        for frame in frames:
            raw = Path(frame["path"]).read_bytes()
            mime = "image/png" if str(frame["path"]).lower().endswith(".png") else "image/jpeg"
            content.append({"type": "text", "text": "Frame id={}: ".format(frame["id"])})
            content.append({"type": "image_url",
                            "image_url": {"url": "data:{};base64,".format(mime)
                                          + base64.b64encode(raw).decode("ascii")}})
        return content

    def _parse_output(self, text, frames):
        # type: (str, Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
        """Structural gate only: parseable JSON about exactly the frames that were sent.

        Field values are rejected later by ``validate_group_reply``, which is what the runner counts as
        a failed group. Retrying a semantically odd reading would spend the budget re-asking a question
        the model answered truthfully -- null is a valid answer about an occluded win-dot row.
        """
        body = (text or "").strip()
        if body.startswith("```"):
            body = re.sub(r"^```\w*\n?", "", body)
            body = re.sub(r"\n?```$", "", body).strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("round-facts reply is not JSON: {}".format(exc))
        if not isinstance(payload, list):
            raise ValueError("round-facts reply is {} not an array".format(type(payload).__name__))
        asked = sorted(str(f["id"]) for f in frames)
        answered = sorted(str(item.get("id")) for item in payload if isinstance(item, dict))
        if asked != answered:
            raise ValueError("round-facts ids {} do not match requested {}".format(answered, asked))
        return payload


def _kind_counts(items):
    # type: (Sequence[Dict[str, Any]]) -> Dict[str, int]
    counts = {}  # type: Dict[str, int]
    for item in items:
        kind = str(item.get("type"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _load_rows(path):
    # type: (Any) -> List[Dict[str, Any]]
    data = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("observations")
    if not isinstance(data, list) or not data:
        raise ValueError("timeline holds no observations: {}".format(path))
    return data


def _load_marks(path):
    # type: (Optional[Any]) -> List[float]
    if not path:
        return []
    raw = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    entries = raw.get("matches", raw) if isinstance(raw, dict) else raw
    out = []
    for entry in entries or []:
        value = entry.get("start", entry) if isinstance(entry, dict) else entry
        if _finite(value):
            out.append(float(value))
    return out


def load_header_responses(path, ledger=None):
    # type: (Optional[Any], Optional[Any]) -> Optional[Dict[str, Any]]
    """Read preserved band-answer directories, or nothing. Sends nothing, ever.

    Kept in one place because the temptation this resists is specific: re-asking six questions to obtain
    answers that are already on disk, and calling the second copy the governed one.

    ``path`` is one run directory **or a comma-separated list of them**, never a directory assembled by
    copying several runs' answers together -- a merged folder carries one provenance for all its answers,
    which is how six unapproved responses came to be reported as ledger-counted on 2026-10-30.
    """
    if not path:
        return None
    return boundary_header.saved_readings(boundary_header.response_directories(path), ledger=ledger)


def load_format_policy(path):
    # type: (Optional[Any]) -> Optional[Dict[str, Any]]
    """The event's own rules, when the caller has them. Optional, and never guessed when absent."""
    return boundary_format.load_policy(path)


#: Ask for the corpus this recording holds: every crop-answer directory under the evidence root.
#: A sentinel rather than a default, because "discover" has to be *chosen* by a caller that knows which
#: recording it is reading -- a library default of discovery would merge BB9's win dots into a synthetic
#: timeline whose frames happen to share a filename, and every unit test would inherit someone else's
#: evidence.
DISCOVER_CROPS = "discover"


def load_evidence(rows, window=None, crop_runs=None, state_only=False, fixture=None,
                  evidence_root=None):
    # type: (Sequence[Dict[str, Any]], Optional[Tuple[float, float]], Optional[Any], bool, Optional[str], Optional[Any]) -> Dict[str, Any]
    """Join the saved crop readings onto these rows through the one canonical merge.

    ``crop_runs`` is :data:`DISCOVER_CROPS` for "everything this recording has" (what the command does), a
    comma list or sequence for a named corpus, or ``None`` for no crop readings at all. ``None`` is
    reported as its own evidence base rather than passing as the real thing: on this recording the state
    timeline alone concludes games with no winner, so a ``games_with_a_winner`` of zero quoted from it is
    a property of the join and not of the footage.
    """
    if crop_runs is None or state_only:
        evidence = boundary_evidence.load(rows=rows, window=window, state_only=True)
        evidence["crop_runs_source"] = ("operator_state_only" if state_only else "no_corpus_named")
        return evidence
    evidence = boundary_evidence.load(rows=rows, window=window,
                                      runs=(None if crop_runs == DISCOVER_CROPS else crop_runs),
                                      fixture=fixture, root=evidence_root)
    if crop_runs == DISCOVER_CROPS:
        evidence["crop_runs_source"] = "discovered"
    return evidence


def recovery_of(evidence, observations, header=None, policy=None,
                rounds_per_game=boundary_games.DEFAULT_ROUNDS_PER_GAME,
                games_per_set=boundary_games.DEFAULT_GAMES_PER_SET, region=None):
    # type: (Dict[str, Any], Sequence[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], int, Any, Optional[Tuple[float, float]]) -> Dict[str, Any]
    """The set-level result of this evidence base, in the shape the read-only report publishes.

    Three passes over one base (no band pairings / band pairings in / no target anywhere) come from
    :mod:`tekken_vod_helper.boundary_evidence`, so a candidate listed here cannot disagree with
    ``scripts/report_saved_header.py`` about what it was.

    ``games_per_set=None`` means no target at all -- the recording-only column -- and is passed through as
    such. It is a value here, not an omission, so nothing re-defaults it to the module's three.
    """
    tracked = adjudicate(evidence, observations, header=header, policy=policy,
                         rounds_per_game=rounds_per_game, games_per_set=games_per_set, region=region)
    return tracked["recovery"]


def adjudicate(evidence, observations, header=None, policy=None,
               rounds_per_game=boundary_games.DEFAULT_ROUNDS_PER_GAME,
               games_per_set=boundary_games.DEFAULT_GAMES_PER_SET, region=None, source=None):
    # type: (Dict[str, Any], Sequence[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], int, Any, Optional[Tuple[float, float]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """The three comparison passes, the recovery block, and the identity gaps that still need asking.

    Returned together because they are one computation over one evidence base: the plan needs the gaps to
    decide what to dispatch, and the report needs the same numbers to publish. Computing them twice in two
    places is how a plan and its own report came to disagree about who had won.
    """
    tracked = boundary_evidence.adjudicate(observations, rounds_per_game=rounds_per_game,
                                           games_per_set=games_per_set, header=header,
                                           readings=(header or {}).get("readings"), policy=policy,
                                           region=region, source=source)
    if header is not None and boundary_evidence.grouping(tracked["plain"]["sets"]) != \
            boundary_evidence.grouping(tracked["assisted"]["sets"]):
        # The band is an identity witness. If reading a nameplate moved a cut, the recovery is no longer
        # the recording's, and a command that printed numbers instead of refusing would hide where.
        raise ValueError("band evidence changed the grouping: an identity witness that moves a boundary "
                         "is a defect, not a result")
    readings = (header or {}).get("readings") or ()
    return {"recovery": boundary_evidence.recovery(evidence, tracked, header=header, policy=policy),
            "tracked": tracked,
            # The questions only the band can answer, in the shape ``boundary_header.route_questions``
            # takes: one disputed span each, which becomes two sampled instants, because a single band
            # frame cannot separate "the pairing changed" from "the band was mid-change here".
            "identity_questions": [{"at": gap["for_game"], "kind": "identity_mapping",
                                    "route": "header", "because": gap["because"],
                                    "status": gap["status"],
                                    "would_resolve": "which participant holds the win this game's "
                                                     "winning side took"}
                                   for gap in boundary_evidence.missing_header_observations(
                                       tracked["assisted"], readings)]}


def saved_route_observations(out):
    # type: (Path) -> List[Dict[str, Any]]
    """The state route's own answers, as this directory wrote them.

    A re-plan has to see what the last pass learned, or it plans the same questions again: the run reuses
    the answer files rather than re-sending them, so the mistake costs nothing twice and reports a queue
    that was already emptied. Failed groups are skipped for the reason the loop skips them -- a rejected
    reply is evidence about the request, not a fact about the frame.
    """
    store = Path(out) / "requests"
    if not store.is_dir():
        return []
    observations = []
    for path in sorted(store.glob("group-*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("errors"):
            continue
        observations.extend(document.get("observations") or [])
    return observations


def _prepare(timeline, frame_manifest, duration, marks=None, region=None,
             rounds_per_game=boundary_games.DEFAULT_ROUNDS_PER_GAME,
             games_per_set=boundary_games.DEFAULT_GAMES_PER_SET, header=None, crop_runs=None,
             state_only=False, fixture=None, evidence_root=None, format_policy=None, run_dir=None):
    # type: (Any, Any, float, Optional[Any], Optional[Sequence[float]], int, Any, Optional[Dict[str, Any]], Optional[Any], bool, Optional[str], Optional[Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    rows = _load_rows(timeline)
    # `--region` promises to keep the whole run inside that stretch, and the run's own accounting is part of
    # the run. Tracking the full 2,019-row recording while planning inside one 800 s window made the header
    # report 30 games as though it described the region the caller asked about -- a scope claim and a scope
    # disagreeing, which is the same defect the shared evidence base was built to close.
    window = tuple(region) if region else None
    if window is not None:
        rows = [entry for entry in rows if _finite(entry.get("timestamp"))
                and float(window[0]) <= float(entry["timestamp"]) <= float(window[1])]
        if not rows:
            raise ValueError("region %s..%s holds no classified rows in %s"
                             % (window[0], window[1], timeline))
    # The saved crop readings join here, through the canonical merge, before anything is tracked: the
    # questions the corpus already answers must not be planned, and the wins it already proves must not be
    # reported as missing. A second join written here would disagree with the report about the recording.
    evidence = load_evidence(rows, window=window, crop_runs=crop_runs, state_only=state_only,
                             fixture=fixture, evidence_root=evidence_root)
    # The rows came from a timeline the caller named, and `load` never sees its path. Recorded here so
    # every artifact says which clock/state facts a winner count was joined onto.
    evidence["facts"] = str(timeline)
    if run_dir is not None:
        # A re-plan merges this run's own state answers back onto the rows before anything is tracked.
        # The crop and band routes need no equivalent step: their answers are stored in the layouts their
        # loaders already read, and those loaders are pointed at the run directory.
        evidence["row_records"] = merge_observations(evidence["row_records"],
                                                     saved_route_observations(run_dir))
        evidence["observations"] = boundary_games.build_observations(evidence["row_records"])
        evidence["run_observations_merged"] = len(saved_route_observations(run_dir))
    observations = evidence["observations"]
    # What the tape and the cache actually cover, decided once here from the two inputs that can say it --
    # the frame manifest the caller named and the duration they supplied. Everything downstream inherits it,
    # so a candidate's leading edge is described by the source rather than by wherever this query started
    # reading, and an absent manifest stays an honest unknown instead of becoming a truncation claim.
    source = boundary_games.source_bounds(manifest=frame_manifest, duration=duration)
    adjudicated = adjudicate(evidence, observations, header=header, policy=format_policy,
                             rounds_per_game=rounds_per_game, games_per_set=games_per_set,
                             region=window, source=source)
    tracked = adjudicated["tracked"]["assisted"]
    return {"rows": evidence["row_records"], "evidence": evidence, "observations": observations,
            "tracked": tracked, "adjudicated": adjudicated, "header": header,
            "unresolved": tracked["unresolved"], "cache": load_cache(frame_manifest),
            "marks": _load_marks(marks), "duration": float(duration), "source": source,
            "rounds_per_game": rounds_per_game, "games_per_set": games_per_set,
            "format_policy": format_policy,
            "window": tuple(region) if region else None}


def _evidence_source(prepared, header=None, format_policy=None):
    # type: (Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Which saved answers this run read, in the words the read-only reports use.

    Named in the artifact because "which corpus?" is the first question a pair of disagreeing numbers has
    to answer, and a default that discovers must not look like a deliberate choice.
    """
    evidence = prepared["evidence"]
    out = {"base": evidence["evidence_base"], "facts": evidence.get("facts"),
           "crop_corpus": evidence.get("crop_runs_source"),
           "crop_runs": evidence["crop_runs"],
           "crop_readings_merged": evidence["crop_readings"], "merge": evidence["merge"],
           # Published, because this is how many pip readings the grade rule refused. A rule that fires
           # silently cannot be told apart from one that never ran, and "the crop arm decides the pip row"
           # is only checkable if a report says how many times it was enforced.
           "pip_readings_demoted": evidence.get("pip_readings_demoted"),
           "framings": evidence.get("framings"),
           "framing_excluded": evidence.get("framing_excluded"),
           "header_responses": _header_source(header),
           "header_readings": len((header or {}).get("readings") or ()),
           "header_provenance": (header or {}).get("provenance"),
           "format_policy": (format_policy or {}).get("source"),
           "format_supplied": bool(format_policy),
           "inference_sent": False, "attempts_charged_now": 0}
    if evidence["evidence_base"] == boundary_evidence.STATE_ONLY:
        # Loud in the artifact, because this base is not a neutral default: it holds no win dots, so
        # every game it tracks ends without a winner and no set can close on it.
        out["warning"] = (
            "no crop readings were merged ({}), so this base cannot credit a win: a "
            "games_with_a_winner of zero here describes the join, not the recording".format(
                evidence.get("crop_runs_source")))
    return out


#: How far one query may reach past its own edge, per side, when the reason it stopped is that it stopped.
#: Bounded because "widen until something appears" is how a bounded pilot turns into a full-VOD pass.
EXPANSION_SECONDS_PER_SIDE = 180.0
#: Which edge limits justify reaching further. `analysis_window` is the only one where the footage is known
#: to exist and merely unqueried; every other limit stops the walk with its reason published instead.
EXPANDABLE_LIMITS = ("analysis_window",)


def expansion_needs(tracked, source=None):
    # type: (Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Which sides of this query stopped short of the tape, and how far reaching would even help.

    Nothing here asks for a station cue, a result screen, a marking, a bracket or a known format: the
    trigger is an edge whose own disclosure says the reason nothing settled it is that this run did not
    look further. A candidate that opened at a proven recording start, or past the end of the cache, is
    explicitly *not* expanded -- widening cannot reach footage that is not there, and pretending otherwise
    spends an allowance on a question the source has already answered.
    """
    src = dict(source or {})
    out = {"before": None, "after": None, "blocked": [], "edges": []}
    for candidate in tracked.get("sets") or ():
        for side, key, direction in (("start", "before", "before"), ("end", "after", "after")):
            edge = (candidate.get("edges") or {}).get(side) or {}
            limit = edge.get("limit")
            if not limit:
                continue
            out["edges"].append({"candidate": [candidate.get("start"), candidate.get("end")],
                                 "side": side, "at": edge.get("at"), "limit": limit})
            if limit not in EXPANDABLE_LIMITS:
                if limit != "footage_never_sampled":
                    out["blocked"].append({"side": direction, "at": edge.get("at"), "limit": limit})
                continue
            at = float(edge.get("at") or 0.0)
            slot = out[key]
            if slot is None or (direction == "before" and at < slot["at"]) \
                    or (direction == "after" and at > slot["at"]):
                out[key] = {"at": at, "limit": limit, "candidate": [candidate.get("start"),
                                                                    candidate.get("end")]}
    for key, cache_key, tape_key in (("before", "cache_start", "recording_start"),
                                     ("after", "cache_end", "recording_end")):
        slot = out.get(key)
        if slot is None:
            continue
        # Reach no further than the cache holds and no further than the recording is known to go. For the
        # head the binding limit is the *greatest* of the two lower bounds and for the tail the *least* of
        # the two upper ones: taking the smaller lower bound would walk the window into territory neither
        # the cache nor the tape has ever covered.
        bounds = [float(value) for value in (src.get(cache_key), src.get(tape_key)) if value is not None]
        slot["reach_to"] = (max(bounds) if key == "before" else min(bounds)) if bounds else None
        slot["reaches_tape_edge"] = bool(src.get(tape_key) is not None and bounds
                                         and slot["reach_to"] == float(src[tape_key]))
    return out


def expansion_window(window, needs, per_side=EXPANSION_SECONDS_PER_SIDE, progress=None):
    # type: (Sequence[float], Dict[str, Any], float, Optional[Dict[str, Any]]) -> Dict[str, Any]
    """The widened query, bounded by the tape, by the configured reach, and by what an earlier run did.

    Each side moves at most ``per_side`` seconds, and never past the cache or the recording. A progress
    file from an earlier call caps it further: a resume that restarted from the *original* edge would walk
    back through ground it already covered and price the same reads twice.
    """
    lo, hi = float(window[0]), float(window[1])
    steps = []
    done = {entry.get("side"): entry for entry in (progress or {}).get("steps") or []}
    for direction, sign in (("before", -1.0), ("after", 1.0)):
        slot = needs.get(direction)
        if slot is None:
            continue
        # The base is the *window's* edge, not the candidate's. The footage this query failed to look at
        # begins where the query began; reaching from a timestamp inside it re-reads ground already covered,
        # and against a cache that spans the whole recording it walks straight to 0 s.
        base = float(done.get(direction, {}).get("expanded_to",
                                                 window[0] if sign < 0 else window[1]))
        wanted = base + sign * float(per_side)
        reach, stopped = slot.get("reach_to"), "expanded"
        if reach is None:
            limit, stopped = base, "source_bounds_unknown"
        else:
            # Nearer of the two bounds wins: never past what the cache and recording are known to cover,
            # never further than the configured per-side reach.
            limit = max(wanted, float(reach)) if sign < 0 else min(wanted, float(reach))
            if abs(limit - wanted) > 0.001:
                stopped = ("recording_head" if sign < 0 and slot.get("reaches_tape_edge") else
                           "recording_tail" if sign > 0 and slot.get("reaches_tape_edge") else "cache_edge")
        moved = round(abs(limit - base), 3)
        steps.append({"side": direction, "edge_at": float(slot["at"]), "from": round(base, 3),
                      "to": round(limit, 3), "requested": round(wanted, 3), "moved": moved,
                      "clamped_by": None if stopped == "expanded" else stopped,
                      "stopping_reason": stopped if moved > 0.001 else "nothing_further_to_reach",
                      "candidate": slot.get("candidate")})
        if sign < 0:
            lo = min(lo, limit)
        else:
            hi = max(hi, limit)
    return {"original_window": [float(window[0]), float(window[1])],
            "expanded_window": [lo, hi], "per_side_seconds": float(per_side), "steps": steps,
            "expanded": bool(steps) and (abs(lo - float(window[0])) > 0.001
                                         or abs(hi - float(window[1])) > 0.001)}


def expansion_report(timeline, frame_manifest, duration, region, base, expand,
                     expansion_progress=None, **kw):
    # type: (Any, Any, float, Sequence[float], Dict[str, Any], float, Optional[Dict[str, Any]], **Any) -> Dict[str, Any]
    """Re-plan over the widened query, reusing saved rows and pricing only the reads widening needs.

    The original plan is kept beside the expanded one instead of being replaced: an expansion that changed
    nothing is a result, and a reader who cannot see which window was asked for cannot check the one that
    was used. New cost is diffed by request **identity** rather than by count, so a frame that appears in
    both plans is not billed twice -- the same rule that stops the reuse layer double-charging a re-run.
    """
    source = (base.get("evidence") or {}).get("source_bounds") or {}
    block = {"inference_sent": False, "attempts_charged_now": 0,
             "per_side_seconds": float(expand), "original_window": [float(region[0]), float(region[1])],
             "expanded_window": [float(region[0]), float(region[1])], "triggered": False,
             "steps": [], "stopping_reasons": [], "edges": [], "blocked_by_edge": []}
    if not source.get("proven") and not source.get("cache_known"):
        block["stopping_reasons"] = ["source_bounds_unknown"]
        return block
    if not expand or float(expand) <= 0.0:
        block["stopping_reasons"] = ["expansion_disabled"]
        return block
    needs = expansion_needs({"sets": base.get("sets_before") or []}, source)
    block["edges"] = needs["edges"]
    block["blocked_by_edge"] = needs["blocked"]
    shape = expansion_window(region, needs, per_side=expand, progress=expansion_progress)
    block["steps"] = shape["steps"]
    block["expanded_window"] = shape["expanded_window"]
    block["stopping_reasons"] = sorted({str(step["stopping_reason"]) for step in shape["steps"]})
    if not shape["expanded"]:
        return block
    block["triggered"] = True
    wider = plan_saved_investigation(timeline, frame_manifest, duration,
                                     region=tuple(shape["expanded_window"]), expand=0.0, **kw)
    before = {(str(entry.get("route")), entry["identity"]) for entry in _plan_requests(base)}
    after = _plan_requests(wider)
    fresh = [entry for entry in after
             if (str(entry.get("route")), entry["identity"]) not in before]
    block["observations"] = {"original": (base.get("tracker_scope") or {}).get("observations"),
                             "expanded": (wider.get("tracker_scope") or {}).get("observations")}
    block["priced_only_missing"] = {
        "requests_original": base.get("requests_planned"),
        "requests_expanded": wider.get("requests_planned"),
        "new_reads": len(fresh),
        "new_images": sum(int(entry.get("images") or 1) for entry in fresh),
        "by_route": {route: sum(1 for entry in fresh if entry.get("route") == route)
                     for route in sorted({str(entry.get("route")) for entry in fresh})},
        "frames": [{"route": entry.get("route"), "timestamp": entry.get("timestamp"),
                    "role": entry.get("role"), "frame": Path(str(entry.get("frame"))).name}
                   for entry in fresh]}
    block["recovery_before"] = base.get("tracker_before")
    block["recovery_after"] = wider.get("tracker_before")
    block["sets_after"] = wider.get("sets_before")
    block["unresolved_before"] = base.get("unresolved_kinds")
    block["unresolved_after"] = wider.get("unresolved_kinds")
    # The widened plan is priced against the same allowance. `requests_planned` is what fits inside it, so
    # the honest measure is what the plan had to leave on the table -- but only for routes this invocation
    # actually funded. A route left out by `--routes` defers its whole workload by choice, and counting
    # that as a spent budget turned an operator's scoping decision into a phantom `budget_exhausted`.
    def _outside_allowance(one):
        # type: (Dict[str, Any]) -> int
        total = 0
        for route in (one.get("dispatch") or {}).get("routes") or []:
            if route.get("unfunded_because") or route.get("enabled") is False:
                continue          # not funded here, by the caller's own choice
            total += len(route.get("deferred") or []) + int(route.get("requests_unfunded") or 0)
        return total
    before_short, after_short = _outside_allowance(base), _outside_allowance(wider)
    block["within_allowance"] = after_short <= before_short
    if not block["within_allowance"]:
        block["over_allowance_by"] = after_short - before_short
        block["stopping_reasons"] = sorted(set(block["stopping_reasons"]) | {"budget_exhausted"})
    block["outside_allowance"] = {"original": before_short, "expanded": after_short}
    # Written so a resume starts from where the last run got to rather than from the edge it has already
    # walked past: the same allowance must not buy the same footage twice.
    block["expansion_progress"] = {"original_window": [float(region[0]), float(region[1])],
                                   "expanded_window": shape["expanded_window"],
                                   "steps": [{"side": step["side"], "expanded_to": step["to"],
                                              "moved": step["moved"],
                                              "stopping_reason": step["stopping_reason"]}
                                             for step in shape["steps"]]}
    block["expanded_plan"] = wider
    return block


def _plan_requests(plan):
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    """Every priced request in a plan, whichever key it filed itself under."""
    out = list(plan.get("pip_requests") or [])
    for route in plan.get("dispatch", {}).get("routes") or []:
        for entry in route.get("requests") or []:
            if entry not in out:
                out.append(entry)
    for region in plan.get("regions") or []:
        for entry in region.get("requests") or []:
            out.append(entry)
    return out


def plan_saved_investigation(timeline, frame_manifest, duration, marks=None, region=None,
                             output_dir=None, header=None, crop_runs=None, state_only=False,
                             fixture=None, evidence_root=None, format_policy=None,
                             routes=None, expand=EXPANSION_SECONDS_PER_SIDE,
                             expansion_progress=None, **kw):
    # type: (Any, Any, float, Optional[Any], Optional[Sequence[float]], Optional[Any], **Any) -> Dict[str, Any]
    """The frame groups a run would send, and the set recovery the saved evidence already supports.

    Costs no inference and asks the model nothing: every answer in ``recovery`` came off disk.
    """
    out = Path(str(output_dir)) if output_dir else None
    if out is not None and out.exists() and any(out.iterdir()):
        raise ValueError("refusing to overwrite a non-empty output dir: {}".format(out))
    rounds_per_game = kw.pop("rounds_per_game", boundary_games.DEFAULT_ROUNDS_PER_GAME)
    games_per_set = kw.pop("games_per_set", boundary_games.DEFAULT_GAMES_PER_SET)
    prepared = _prepare(timeline, frame_manifest, duration, marks=marks, region=region,
                        rounds_per_game=rounds_per_game, games_per_set=games_per_set, header=header,
                        crop_runs=crop_runs, state_only=state_only, fixture=fixture,
                        evidence_root=evidence_root, format_policy=format_policy)
    plan = plan_investigation(prepared["unresolved"], prepared["cache"], prepared["duration"],
                              observations=prepared["observations"], marks=prepared["marks"],
                              window=prepared["window"],
                              crop_readings=prepared["evidence"].get("crop_readings_by_frame"),
                              identity_questions=prepared["adjudicated"]["identity_questions"],
                              routes=routes, header=prepared.get("header"), **kw)
    plan["source"] = {"timeline": str(timeline), "frame_manifest": str(frame_manifest),
                      "header_responses": _header_source(header)}
    # Said at the top of the artifact, where a reader looks first: `requests_planned` is what a run *would*
    # spend, and nothing here spent it. A plan that did send would carry the same key as False wrongly,
    # which is why the run path reports its own count instead of inheriting this one.
    plan["inference_sent"] = False
    plan["attempts_charged_now"] = 0
    plan["evidence"] = _evidence_source(prepared, header, format_policy)
    plan["recovery"] = prepared["adjudicated"]["recovery"]
    plan["tracker_before"] = prepared["tracked"]["accounting"]
    # The accounting dict alone cannot be audited: it says how many candidates closed and not why. Carrying
    # the per-candidate claims means a reviewer can check a conclusion against the evidence the report
    # quotes, instead of taking a count on trust -- which is the difference between a report and a total.
    plan["sets_before"] = [{"start": s["start"], "end": s["end"], "status": s["status"],
                            "membership": s["membership"], "games": s["games"],
                            "game_wins": s["game_wins"], "deciding_wins": s["deciding_wins"],
                            "deciding_wins_unit": s["deciding_wins_unit"],
                            "set_winner": s["set_winner"], "concluded_by": s.get("concluded_by"),
                            "boundary_confirmed": s.get("boundary_confirmed"),
                            "footage_complete": s.get("footage_complete"),
                            "basis": s["basis"], "edges": s.get("edges"),
                            # The reading this candidate did not take. An ambiguous cut is a real
                            # disagreement, and the alternative has to survive to the caller or "kept as an
                            # alternative" is a phrase in a log rather than a result.
                            "alternative_groupings": [{"at": a.get("at"), "reading": a.get("reading"),
                                                       "would_merge": a.get("would_merge"),
                                                       "joined_with": a.get("joined_with"),
                                                       "reason": a.get("reason"),
                                                       "why_not_primary": a.get("why_not_primary"),
                                                       "questions": a.get("questions") or []}
                                                      for a in s.get("alternative_groupings") or []],
                            "missing_evidence": s["missing_evidence"]}
                           for s in prepared["tracked"]["sets"]]
    plan["tracker_scope"] = {"region": list(prepared["window"]) if prepared["window"] else None,
                             "observations": len(prepared["observations"]),
                             "note": "the accounting above describes the rows inside this region, not the "
                                     "whole recording: --region bounds the run, its numbers included"}
    plan["unresolved_kinds"] = _kind_counts(prepared["unresolved"])
    # What the tape and the cache actually cover, published beside the numbers that were read off them:
    # every leading/trailing limit in this artifact is measured against these bounds and nothing else.
    plan["evidence"]["source_bounds"] = prepared["source"]
    if prepared["window"]:
        plan["expansion"] = expansion_report(
            timeline, frame_manifest, duration, prepared["window"], plan, expand,
            expansion_progress=expansion_progress, marks=marks, header=header, crop_runs=crop_runs,
            state_only=state_only, fixture=fixture, evidence_root=evidence_root,
            format_policy=format_policy, routes=routes,
            **{key: value for key, value in kw.items() if key not in ("expand", "expansion_progress")})
    else:
        plan["expansion"] = {"triggered": False, "stopping_reasons": ["no_region_to_widen"],
                             "original_window": None, "expanded_window": None, "steps": []}
    plan["what_the_pilot_cannot_prove"] = [
        "the winner of a round, where no crop reading and no legible full-frame row gives the dots: this "
        "command's own reading of the dot row is superseded by a cache-arm crop whenever one exists",
        "that a game ended rather than a set, where no between-game screen was broadcast",
        "who sat on which screen side: that is a band question, and the answers here are read from saved "
        "band responses, never asked again",
        "any set count from the frame questions alone: the set-level claims in `recovery` are the ones "
        "that name a target, and boundary, score, winner and footage completeness are separate claims",
    ]
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        # The returned plan keeps the whole widened run for the caller; the artifact carries the widened
        # window and its numbers, not a second copy of every block, or a bounded report turns into two.
        on_disk = dict(plan)
        if on_disk.get("expansion"):
            on_disk["expansion"] = {key: value for key, value in on_disk["expansion"].items()
                                    if key != "expanded_plan"}
        (out / "plan.json").write_text(json.dumps(on_disk, indent=2, sort_keys=True), encoding="utf-8")
        # The resume cursor on its own, so a re-run can be pointed at it without loading a plan artifact
        # that carries the whole recovery block, and so an interrupted widening leaves a record of how far
        # it actually got rather than of what it meant to do.
        progress = (plan.get("expansion") or {}).get("expansion_progress")
        if progress:
            (out / "expansion.json").write_text(json.dumps(progress, indent=2, sort_keys=True),
                                                encoding="utf-8")
        (out / "games-trace-before.json").write_text(json.dumps(prepared["tracked"], indent=2,
                                                               sort_keys=True, default=list),
                                                     encoding="utf-8")
    return plan


def _sha(path):
    # type: (Path) -> str
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _input_identity(timeline, frame_manifest, duration, plan, header=None, evidence=None,
                    format_policy=None):
    # type: (Any, Any, float, Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """What a resumed run must prove it is continuing before it reuses a single saved byte.

    Group fingerprints already cover the frames and the question; this covers the *run*, so a resume
    into the wrong directory is refused by name rather than quietly merging evidence from two plans.
    """
    return {
        "timeline": str(timeline),
        "timeline_sha256": _sha(Path(str(timeline))),
        "frame_manifest": str(frame_manifest),
        "duration": float(duration),
        "question": plan.get("question_identity"),
        "window": plan.get("window"),
        "batch_frames": plan.get("batch_frames"),
        "step_seconds": plan.get("step_seconds"),
        # Which band answers were in the room. A resume that silently dropped or swapped them would
        # report attributions the saved run never had, and the groups it reuses would mean something else.
        "header_responses": _header_source(header),
        "header_evidence_sha256": _header_digest(header),
        # Which crop corpus and which rules. A resume that switched the evidence base would reuse groups
        # answered under one merge and report a recovery measured against another; switching the format
        # policy would change, mid-run, which candidates were allowed to close.
        "evidence_base": (evidence or {}).get("evidence_base"),
        "crop_runs": sorted((evidence or {}).get("crop_runs") or ()),
        "format_policy": (format_policy or {}).get("source"),
    }


def _group_tag(fingerprint):
    # type: (str) -> str
    """The ledger's name for one group: the fingerprint its answer file is stored under.

    ``boundary_budget.request_tag`` hashes the request body, and this route's body is six base64 frames --
    a hash nobody can recompute from the plan, which is exactly what an in-flight reconciliation needs. The
    group fingerprint already covers the frames, their timestamps and the question identity, so it names
    the one request and nothing else.
    """
    return "group-{}".format(fingerprint)


#: The per-route payload the loops return also carries every reply and every rendered image path. The run
#: writes each answer to its own store already, so the pass history keeps the counts and drops the bulk:
#: an ``investigation.json`` that repeats 36 base64 replies is an artifact nobody can read and a disk
#: write that dwarfs the evidence it describes.
HEAVY_ROUTE_KEYS = ("observations", "usage", "groups", "control", "reply", "crop_images", "images")


def _light(outcome):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    kept = {key: value for key, value in outcome.items() if key not in HEAVY_ROUTE_KEYS}
    if "observations" in outcome:
        kept["observations_written"] = len(outcome.get("observations") or [])
    return kept


class TypedInvestigationRun:
    """Spend one allowance and one ledger across the three executors, in :data:`ROUTE_ORDER`.

    Each route keeps its own answer store, in the layout its own executor writes: the state route's
    ``requests/group-<fingerprint>.json``, the crops route's ``pip/crops/groups/``, the band's
    ``header/groups/``. That is not tidiness -- it is what lets a run's new crop answers reach the tracker
    through :func:`boundary_evidence.crop_observations`, the same loader that reads the saved corpus, and
    its band answers through :func:`boundary_header.saved_readings`. A run that invented its own answer
    format would need its own reader, and a second reader is how a report and a plan disagree.

    Nothing here re-implements a send loop. The crop and band routes call the executors' own
    ``persist_probe`` / ``persist_readings``, which already carry the control checks, the per-group
    persistence, the transport-uncertain rule and the ledger's stop.
    """

    def __init__(self, clients, output_dir, max_requests=DEFAULT_MAX_REQUESTS, ledger=None):
        # type: (Dict[str, Any], Any, int, Optional[Any]) -> None
        self.clients = clients
        self.out = Path(output_dir)
        self.max_requests = int(max_requests)
        self.ledger = ledger
        self.stopped = None  # type: Optional[str]
        self.error = None  # type: Optional[str]

    def spent(self):
        # type: () -> int
        return sum(int(getattr(client, "calls", 0) or 0) for client in self.clients.values()
                   if client is not None)

    def remaining(self):
        # type: () -> int
        return max(0, self.max_requests - self.spent())

    def allowance_for(self, route):
        # type: (str) -> int
        """The absolute ceiling to hand one route's loop, given what the other routes already spent.

        Each executor enforces its allowance by comparing its own cumulative ``calls`` against the number
        it was handed. Passing it ``remaining()`` therefore stopped a second pass before it started: the
        crop client had made 36 calls and was told it had 31 to make, which read as "allowance reached"
        with nothing sent. The ceiling has to be absolute -- this client's own sends, plus whatever the run
        still has -- or the routes account their budgets twice against each other.
        """
        client = self.clients.get(route)
        already = int(getattr(client, "calls", 0) or 0)
        return already + self.remaining()

    def run(self, plan, cache_by_path=None, crop_records=None, band_requests=None):
        # type: (Dict[str, Any], Optional[Dict[str, Dict[str, Any]]], Optional[Sequence[Dict[str, Any]]], Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]
        """Dispatch one pass. Returns the per-route tallies, in the plan's route order."""
        summary = {"routes": [], "requests_sent": 0, "images_sent": 0, "stopped": None}
        for route in ROUTE_ORDER:
            if self.remaining() <= 0:
                self.stopped = self.stopped or "request_allowance_reached"
                summary["stopped"] = self.stopped
                break
            if route == "state":
                outcome = self._run_state(plan, cache_by_path or {})
            elif route == "pip_crops":
                outcome = self._run_pips(plan, crop_records or [])
            else:
                outcome = self._run_header(plan, band_requests or [])
            summary["routes"].append(outcome)
            summary["requests_sent"] += outcome.get("requests_sent", 0)
            summary["images_sent"] += outcome.get("images_sent", 0)
            if outcome.get("stopped_reason") or outcome.get("stopped"):
                self.stopped = outcome.get("stopped_reason") or outcome.get("stopped")
                summary["stopped"] = self.stopped
                break
        summary["requests_sent"] = self.spent()
        return summary

    def _planned(self, plan, route):
        # type: (Dict[str, Any], str) -> Dict[str, Any]
        for entry in plan["dispatch"]["routes"]:
            if entry["route"] == route:
                return entry
        return {}

    def _run_state(self, plan, index):
        # type: (Dict[str, Any], Dict[str, Dict[str, Any]]) -> Dict[str, Any]
        priced = self._planned(plan, "state")
        if self.clients.get("state") is None:
            return {"route": "state", "requests_sent": 0, "images_sent": 0, "unfunded": True}
        if not priced.get("requests_planned"):
            return {"route": "state", "requests_sent": 0, "images_sent": 0,
                    "skipped": "no state question is open in this pass"}
        runner = InvestigationRun(self.clients["state"], self.out,
                                  max_requests=self.allowance_for("state"))
        spent_before = int(getattr(self.clients["state"], "calls", 0) or 0)
        result = runner.run(plan, cache_by_path=index)
        self.state_runner = runner
        result["route"] = "state"
        # The state loop reports the client's cumulative calls; a pass has to report its own, or the
        # second pass appears to have re-spent the first pass's allowance.
        result["requests_sent"] = int(getattr(self.clients["state"], "calls", 0) or 0) - spent_before
        result["groups_resumed"] = result.get("groups_resumed", 0)
        result["groups_failed"] = int(result.get("groups_failed_validation") or 0) + \
            int(result.get("groups_failed_request") or 0)
        result["stopped"] = result.get("stopped_reason")
        result["images_sent"] = priced.get("images_total", 0)
        return result

    def _run_pips(self, plan, records):
        # type: (Dict[str, Any], Sequence[Dict[str, Any]]) -> Dict[str, Any]
        priced = self._planned(plan, "pip_crops")
        if self.clients.get("pip_crops") is None:
            return {"route": "pip_crops", "requests_sent": 0, "images_sent": 0, "unfunded": True}
        if not priced.get("requests_planned"):
            return {"route": "pip_crops", "requests_sent": 0, "images_sent": 0,
                    "reused": priced.get("reused", 0),
                    "skipped": "the crop corpus already answers every decision frame in this window"}
        client = self.clients["pip_crops"]
        before = int(getattr(client, "calls", 0) or 0)
        # The probe writes answers into ``<out>/groups``, so the directory handed to it is the one the
        # corpus loader walks *inside* -- ``pip/crops`` here, exactly as ``build/work/bb9/pip-crops-v2`` is
        # laid out. Pointing it at the run root instead would store answers where nothing reads them, and
        # the next pass would pay for the same crops a second time.
        result = boundary_pips.persist_probe(records, self.out / "pip" / "crops", "crops",
                                             client=client,
                                             max_requests=self.allowance_for("pip_crops"))
        result["route"] = "pip_crops"
        result["requests_sent"] = int(getattr(client, "calls", 0) or 0) - before
        result["images_sent"] = 2 * result["requests_sent"]
        result["evidence_class"] = ROUTE_EVIDENCE["pip_crops"][0]
        result["stopped_reason"] = result.get("stopped") or result.get("stopped_reason")
        return _light(result)

    def _run_header(self, plan, requests):
        # type: (Dict[str, Any], Sequence[Dict[str, Any]]) -> Dict[str, Any]
        priced = self._planned(plan, "header")
        if self.clients.get("header") is None:
            return {"route": "header", "requests_sent": 0, "images_sent": 0, "unfunded": True}
        if not priced.get("requests_planned"):
            return {"route": "header", "requests_sent": 0, "images_sent": 0,
                    "skipped": "no identity gap is open, or the band answers on disk already cover it"}
        client = self.clients["header"]
        before = int(getattr(client, "calls", 0) or 0)
        result = boundary_header.persist_readings(requests, self.out / "header", client=client,
                                                  max_requests=self.allowance_for("header"))
        result["route"] = "header"
        result["requests_sent"] = int(getattr(client, "calls", 0) or 0) - before
        result["images_sent"] = result["requests_sent"]
        result["stopped_reason"] = result.get("stopped") or result.get("stopped_reason")
        return _light(result)


def crop_records_for(requests, out_dir):
    # type: (Sequence[Dict[str, Any]], Any) -> List[Dict[str, Any]]
    """Render the two crops each planned pip request needs, and describe them as the probe expects.

    Rendering is local work with no inference in it, and it happens here rather than inside the send loop
    because the plan has to price the images it would send: a request whose crop cannot be rendered is a
    request that cannot be made, and saying so before the allowance is spent is the whole point of a plan.
    """
    entries = []
    for request in requests:
        frame = request["frame"]
        if not Path(str(frame)).is_file():
            raise ValueError("the crop route cannot be planned: {} is not on disk".format(frame))
        entries.append({"frame": frame, "timestamp": request["timestamp"]})
    return boundary_pips.build_records(entries, out_dir)


def route_request_tag(route, body):
    # type: (str, Any) -> str
    """One attempt's name in the shared ledger: the route, then the request it charged.

    ``boundary_budget.request_tag`` derives a name from the frame tokens in the body, and across three
    routes that is not a name -- the same frame can be asked twice, once for its pip row and once for its
    nameplate. Two attempts, two answers, two questions; keyed on the bare token, a resume would close the
    band's hanging attempt with the crop's answer file and call an unasked question settled.
    """
    return "{}:{}".format(route, boundary_budget.request_tag(body))


def route_answer_tag(route, path):
    # type: (str, Any) -> Optional[str]
    """The ledger name for an answered request, recomputed from the answer file alone."""
    token = frame_token(path)
    return "{}:{}".format(route, token) if token else None


def _tag_for(route):
    # type: (str) -> Any
    """A per-route tagging function for ``boundary_budget.attach``."""
    def tagged(body):
        return route_request_tag(route, body)
    return tagged


def band_requests_for(requests, out_dir, frames_by_path):
    # type: (Sequence[Dict[str, Any]], Any, Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]
    """Render the bands the identity gaps ask about, in the band executor's own request shape.

    The frame's own timestamp travels with the instant it was chosen for. On this cache they are not the
    same number, and an answer that does not say which of the two it describes is an off-by-one waiting
    to move across a HUD transition.
    """
    instants = []
    for request in requests:
        frame = request.get("frame")
        if not frame or not Path(str(frame)).is_file():
            raise ValueError("the band route cannot be planned: {} is not on disk".format(frame))
        entry = frames_by_path.get(frame) or {}
        drawn = boundary_header._instant(entry.get("timestamp"))
        claimed = boundary_header._instant(request.get("frame_timestamp"))
        if drawn is None:
            raise ValueError("the band route cannot be planned: the cache holds no sampled instant for "
                             "{}, so an answer about it could not be credited to a time".format(frame))
        if claimed is not None and abs(claimed - drawn) > boundary_header.INSTANT_TOLERANCE_SECONDS:
            raise ValueError(
                "the band route cannot be planned: {} asks to answer for {:.6f} s, and that file draws "
                "{:.6f} s. Nothing was sent: a band reading filed at a moment its frame did not draw is "
                "evidence about the wrong footage, and the join below decides which game -- and which "
                "participant -- it would be credited to.".format(Path(str(frame)).name, claimed, drawn))
        instants.append({"at": request["at"], "frame": frame,
                         "question": request.get("question"), "because": request.get("because"),
                         "frame_timestamp": drawn, "frame_id": entry.get("id"),
                         "requested_at": request.get("requested_at"),
                         "offset_seconds": request.get("offset_seconds"),
                         "selected_by": request.get("selected_by") or "identity_gap"})
    return boundary_header.plan(instants, out_dir)["requests"]


#: One pass per approved plan, and two by default: the first pass's answers are what make the next
#: question askable -- dots that reach three end a game, and a game with a winner now has an identity
#: question it did not have a pass ago. A pass that asks nothing costs nothing.
DEFAULT_PASSES = 2


def _client(route, endpoint=None, model=None, timeout=1800, max_attempts=2, out=None, allowance=24):
    # type: (str, Optional[str], Optional[str], int, int, Path, int) -> Any
    """The executor that owns one route, built the way that executor's own command builds it."""
    if route == "pip_crops":
        return boundary_pips.PipReadClient(arm="crops", endpoint=endpoint, model=model,
                                           max_requests=allowance, timeout=timeout,
                                           output_dir=str(out / "pip"))
    if route == "header":
        return boundary_header.HeaderReadClient(endpoint=endpoint, model=model,
                                                max_requests=allowance, timeout=timeout,
                                                output_dir=str(out / "header"))
    return RoundFactsClient(endpoint=endpoint, model=model, max_requests=allowance, timeout=timeout,
                            output_dir=str(out), max_attempts=max_attempts)


def route_answer_store(route, out):
    # type: (str, Path) -> Path
    """Where one route's answers live, in the layout its own executor reads them from."""
    if route == "pip_crops":
        return out / "pip" / "crops" / "groups"
    if route == "header":
        return out / "header" / "groups"
    return out / "requests"


def _answer_name(route, identity):
    # type: (str, str) -> str
    """The file one answered request is stored under, by the route that writes it."""
    if route == "pip_crops":
        return boundary_pips.group_filename("crops", identity)
    if route == "header":
        return boundary_header.group_filename(identity)
    return "group-{}.json".format(identity)


def dispatch_units(plan, out, clients, by_path):
    # type: (Dict[str, Any], Path, Dict[str, Any], Dict[str, Dict[str, Any]]) -> Dict[str, Any]
    """Render what each route would send, once, and name those sends by their content.

    Rendering happens here rather than inside a send loop for one reason: a request's identity is the
    digest of the pixels it would carry, and the same value has to answer "is this already on disk?" and
    "what am I about to send?" -- two questions that must never be answered by two different formulas.
    A plan that guessed the digest could count an answered request as owed and spend an attempt to take
    the same picture twice.
    """
    units = {"crop_records": [], "band_requests": [], "identities": {}, "owed": {}, "named": {}}
    if plan["pip_requests"]:
        units["crop_records"] = crop_records_for(plan["pip_requests"], out / "pip" / "crops")
        model = getattr(clients["pip_crops"], "model", None)
        units["identities"]["pip_crops"] = [
            boundary_pips.group_fingerprint([record], "crops", model)
            for record in units["crop_records"]]
        units["named"]["pip_crops"] = [{"frame": request["frame"], "at": request["timestamp"],
                                        "role": request["role"]}
                                       for request in plan["pip_requests"]]
    else:
        units["identities"]["pip_crops"] = []
        units["named"]["pip_crops"] = []
    if plan["header_requests"]:
        units["band_requests"] = band_requests_for(plan["header_requests"], out / "header" / "bands",
                                                   by_path)
        model = getattr(clients["header"], "model", None)
        units["identities"]["header"] = [boundary_header.request_fingerprint(request, model)
                                         for request in units["band_requests"]]
        units["named"]["header"] = [{"at": request["at"], "frame": request["frame"],
                                     "question": request["question"]}
                                    for request in plan["header_requests"]]
    else:
        units["identities"]["header"] = []
        units["named"]["header"] = []
    units["identities"]["state"] = [group["fingerprint"] for region in plan["regions"]
                                    for group in region["groups"]]
    units["named"]["state"] = [{"frames": group["frame_paths"],
                                "at": group["frame_timestamps"][0]}
                               for region in plan["regions"] for group in region["groups"]]
    for route in ROUTE_ORDER:
        store = route_answer_store(route, out)
        units["owed"][route] = [name for name in units["identities"][route]
                                if not (store / _answer_name(route, name)).is_file()]
    return units


def frame_token(path):
    # type: (Any) -> Optional[str]
    """The ``frame-0175`` token in a path, which is what the crop and band routes' attempt tag derives from.

    Those two loops charge the ledger through ``boundary_budget.request_tag``, which names an attempt by
    the frame tokens in the request body. Reconciling a hanging attempt therefore has to name it the same
    way, and the frame is the thing both the body and the answer file agree on.
    """
    match = re.search(r"frame-\d{4}", str(path or ""))
    return match.group(0) if match else None


def _owed(units):
    # type: (Dict[str, Any]) -> int
    return sum(len(names) for names in units["owed"].values())


def answered_tags(out):
    # type: (Path) -> Dict[str, float]
    """Every request this run's stores can prove was answered, named the way the ledger names it.

    Read from the stores and not from the current plan, and that is the whole point: a request the plan no
    longer lists is exactly the case where reuse worked -- it was answered, so it stopped being owed -- and
    reconciling only the planned set would leave its charged attempt hanging in flight forever. The store
    is the ground truth of what has an answer; the plan is only the ground truth of what is still missing.

    The value is the answer file's own modification time, which is what lets the ledger refuse to credit a
    reply written *before* an attempt was charged to it: an answer can only explain an attempt that could
    have caused it.
    """
    tags = {}
    for route in ROUTE_ORDER:
        store = route_answer_store(route, out)
        if not store.is_dir():
            continue
        pattern = {"pip_crops": "crops-*.json", "header": "header-*.json"}.get(
            route, "group-*.json")
        for path in sorted(store.glob(pattern)):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue                       # a half-written file proves nothing
            try:
                written = path.stat().st_mtime
            except OSError:
                written = None                 # no clock, so no attribution: the ledger will not guess
            if route == "state":
                if document.get("fingerprint"):
                    tags[_group_tag(document["fingerprint"])] = written
                continue
            tag = route_answer_tag(route, document.get("frame")
                                   or (document.get("request") or {}).get("frame"))
            if tag:
                tags[tag] = written
    return tags


def _reconcile(ledger, units, out, plan):
    # type: (Optional[Any], Dict[str, Any], Path, Dict[str, Any]) -> None
    """Close a hanging attempt only when an answer file can be attributed to *that* attempt.

    Run at the start of every pass, before the owed check and before any refusal: the point of the ledger's
    ``in_flight`` list is to name spends whose outcome is unknown, and an attempt whose request has since
    been answered by *this* run is no longer unknown. Leaving it would let a later process read the spend
    as still-pending and refuse to re-ask work that in fact still needs doing -- or, worse, re-ask work
    that does not.

    What the answer file does not prove is that the attempt that hung was the one it came back on. A
    resume that re-sent the frame and got a reply has explained its own attempt, and the earlier spend is
    left as an uncertain outcome, still charged, with the request recorded as satisfied.
    """
    if ledger is None:
        return
    report = ledger.reconcile_in_flight(answered_tags(out))
    if report["closed"]:
        plan["in_flight_reconciled"] = (plan.get("in_flight_reconciled") or 0) + len(report["closed"])
    if report["unattributable"]:
        kept = plan.setdefault("attempts_kept_uncertain", [])
        kept.extend(report["unattributable"])


def _before_sending(ledger, units, out, plan):
    # type: (Optional[Any], Dict[str, Any], Path, Dict[str, Any]) -> None
    """The two refusals that have to happen while the process can still afford to say no."""
    if ledger is None:
        return
    if ledger.deadline_at is not None and (ledger.seconds_left() or 0.0) <= 0:
        raise ValueError("refusing to start into {}: this ledger's single {} minute deadline passed "
                         "at {:.0f}. A new run needs a new approval, not a new process."
                         .format(out, ledger.minutes, ledger.deadline_at))
    owed = _owed(units)
    if owed > ledger.remaining:
        raise ValueError(
            "refusing to start into {}: {} requests are still owed across {} and the shared ledger at "
            "{} has {} of {} attempts left (deadline {}). Ask for more budget or defer the excess "
            "deliberately -- do not spend a later command's attempts.".format(
                out, owed, ", ".join(route for route in ROUTE_ORDER if units["owed"][route]),
                ledger.path, ledger.remaining, ledger.cap, ledger.deadline_at))


def _write_interruption(out, runner, ledger, history):
    # type: (Path, TypedInvestigationRun, Optional[Any], Sequence[Dict[str, Any]]) -> None
    """Say what was answered before the process dies, so the next invocation is a resume not a guess.

    Read from the live runners, not from the pass history: an interrupt lands *mid*-pass, so the entry the
    run would have appended does not exist, and a record built from it would report nothing answered in the
    pass where the operator actually stopped things. The answer files on disk are the ground truth, and
    those are counted directly.
    """
    answered = sum(len(list(route_answer_store(route, out).glob(
        {"pip_crops": "crops-*.json", "header": "header-*.json"}.get(route, "group-*.json"))))
        for route in ROUTE_ORDER if route_answer_store(route, out).is_dir())
    (out / "investigation-interrupted.json").write_text(
        json.dumps({"status": "stopped", "stopped_reason": "cancelled_by_operator",
                    "requests_sent": runner.spent(),
                    "groups_answered": answered,
                    "stores": {route: len(list(route_answer_store(route, out).glob("*")))
                               for route in ROUTE_ORDER if route_answer_store(route, out).is_dir()},
                    "passes": [{"pass": entry["pass"], "sent": entry.get("sent"),
                                "answers_added": entry.get("answers_added"),
                                "deferred": len(entry.get("deferred") or [])}
                               for entry in history],
                    "deferred": [entry for pass_entry in history
                                 for entry in pass_entry.get("deferred") or []],
                    "budget": ledger.summary() if ledger else None,
                    "answer_stores": {route: str(route_answer_store(route, out))
                                      for route in ROUTE_ORDER},
                    "resume": "re-run with --resume: every answered request is on disk under its own "
                              "content identity and nothing is re-asked"},
                   indent=2, sort_keys=True), encoding="utf-8")


def _refresh(prepared, out, header, timeline, frame_manifest, duration, marks, region,
             rounds_per_game, games_per_set, format_policy, state_only, fixture, evidence_root,
             crop_runs, own_ledger=None, own_state=True):
    # type: (Dict[str, Any], Path, Optional[Dict[str, Any]], Any, Any, float, Optional[Any], Optional[Sequence[float]], int, Any, Optional[Dict[str, Any]], bool, Optional[str], Optional[Any], Optional[Any], Optional[Any]) -> Dict[str, Any]
    """Rebuild the evidence state so it contains what this run has answered.

    The run's own crop store is added to the corpus list and its band store to the header runs, and the
    canonical loaders do the rest. Nothing here re-derives a merge: an answer this command produced joins
    the evidence exactly as an answer the same command reused from last week would, which is the property
    that makes a second pass trustworthy instead of merely later.

    ``own_state`` folds this run's state-route group answers into the rows as well. A pass boundary wants
    that; a *resume's* first plan deliberately leaves it out, because the state route packs batches off
    its frame grid and merging its answers at plan time re-grips every fingerprint mid-resume. A resumed
    state pass keeps the loop's own reuse, which matches a saved group by fingerprint before sending it.
    """
    runs = ",".join([str(path) for path in prepared["evidence"]["crop_runs"]]
                    + ([str(out / "pip")] if ((out / "pip" / "crops" / "groups").is_dir()
                                               and str(out / "pip")
                                               not in [str(path) for path in
                                                       prepared["evidence"]["crop_runs"]]) else []))
    if not runs:
        runs = None if not crop_runs else crop_runs
    return _prepare(timeline, frame_manifest, duration, marks=marks, region=region,
                    rounds_per_game=rounds_per_game, games_per_set=games_per_set,
                    header=_combined_header(header, out, own_ledger), crop_runs=runs,
                    state_only=state_only,
                    fixture=fixture, evidence_root=evidence_root, format_policy=format_policy,
                    run_dir=out if own_state else None)


def _combined_header(header, out, own_ledger=None):
    # type: (Optional[Dict[str, Any]], Path, Optional[Any]) -> Optional[Dict[str, Any]]
    """The caller's band answers plus any this run sent, through the one band reader.

    ``saved_readings`` reports the directories it read, and that list *is* the caller's configuration:
    re-deriving it by joining paths back into one string would be a parsing step with nothing to recommend
    it, and a run's own answers are simply the next directory on the list.

    ``own_ledger`` is this command's own attempt ledger, and it is passed because the two halves of that
    list have different histories. The caller's directories were charged to the ledger named by
    ``--header-ledger``; the answers in ``out/header`` were charged just now, to ``own_ledger``. Reading
    them as one corpus with no ledger attached described a run's own counted 61-attempt spend as eight
    preserved answers with no record of their spend -- provenance wrong in the direction that makes a
    governed run look ungoverned.
    """
    store = out / "header"
    if not (store / "groups").is_dir():
        return header
    source = (header or {}).get("source_directory")
    # A run that sent band answers but borrowed none has a store and no corpus, and the reader's
    # "name a directory" rule is about the caller's argument, not about this case: an empty source is
    # empty, not an error.
    directories = ([] if not source else
                   [str(item) for item in source] if isinstance(source, (list, tuple))
                   else list(boundary_header.response_directories(source)))
    prior = ((header or {}).get("provenance") or {}).get("ledger")
    preserved_ledger = prior if prior and Path(str(prior)).is_file() else None
    own = (store, own_ledger) if own_ledger and Path(str(own_ledger)).is_file() else None
    return boundary_header.saved_readings(directories + [store], ledger=preserved_ledger,
                                          own_run=own)


def _deferrals(units, out):
    # type: (Dict[str, Any], Path) -> List[Dict[str, Any]]
    """What this pass planned and did not get an answer for, named by frame or instant.

    A budget that ran out has to leave a list, not a count. "21 requests were not made" is true of the
    allowance and useless to the next operator; "these decision frames have no pip reading and nobody has
    looked at these games for a nameplate" is the work that is actually pending, and it is what the next
    approved plan is priced from.
    """
    left = []
    for route in ROUTE_ORDER:
        store = route_answer_store(route, out)
        for name, described in zip(units["identities"][route], units["named"][route]):
            if (store / _answer_name(route, name)).is_file():
                continue
            left.append(dict(described, route=route,
                             because="planned by this pass and not answered: the allowance or the ledger "
                                     "ran out before it was sent"))
    return left


def _replan(prepared, attempt, endpoint, model, kw, routes=None):
    # type: (Dict[str, Any], int, Optional[str], Optional[str], Dict[str, Any], Optional[tuple]) -> Dict[str, Any]
    """Plan the next pass from the evidence the last pass left behind, on the routes still funded."""
    plan = plan_investigation(prepared["unresolved"], prepared["cache"], prepared["duration"],
                              observations=prepared["observations"], marks=prepared["marks"],
                              window=prepared["window"], endpoint=endpoint, model=model,
                              crop_readings=prepared["evidence"].get("crop_readings_by_frame"),
                              identity_questions=prepared["adjudicated"]["identity_questions"],
                              passes=attempt, routes=routes, header=prepared.get("header"), **kw)
    return plan


def _dispatch_after(prepared, plan):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """What the last plan still owed, and what the run's own evidence now says is missing.

    The two are different questions and the run has to report both: the first is what a *resume* would
    send next, the second is what a reader needs to know about the conclusion (an identity gap is a
    property of the footage, not of the allowance).
    """
    return {"planned_by_route": {route["route"]: route["requests_planned"]
                                 for route in plan["dispatch"]["routes"]},
            "reused_by_route": {route["route"]: route.get("reused", 0)
                                for route in plan["dispatch"]["routes"]},
            "deferred_by_route": {route["route"]: route.get("deferred") or []
                                  for route in plan["dispatch"]["routes"]},
            "identity_gaps_left": len(prepared["adjudicated"]["identity_questions"])}


#: One vocabulary for what a route did. The crop and band loops report ``groups_reused`` and
#: ``groups_failed``; the state loop grew ``groups_resumed`` and ``groups_failed_validation`` first, and
#: its ``groups_completed`` counts a reused group as completed. Adding same-named keys across routes
#: would double-count every reuse in the state route and drop every failure in the other two, so each
#: route's outcome is translated before anything is summed.
ROUTE_TALLY_KEYS = ("groups_planned", "groups_answered", "groups_resumed", "groups_completed",
                    "groups_failed", "groups_failed_validation", "groups_failed_request",
                    "groups_deferred_by_ledger", "observations_written", "not_attempted")


def normalise_route_outcome(outcome):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """One route's outcome, in the run's dialect.

    The crop and band loops write ``groups_reused``/``groups_failed``; the state loop wrote
    ``groups_resumed``/``groups_failed_validation``/``groups_failed_request`` first, and its
    ``groups_completed`` counts a reused group as completed. Summing same-named keys across routes would
    double-count every reuse in the state route and drop every failure in the other two.
    """
    reused = int(outcome.get("groups_reused") or outcome.get("groups_resumed") or 0)
    answered = int(outcome.get("groups_answered") or 0)
    failed = int(outcome.get("groups_failed") or 0) + int(outcome.get("groups_failed_request") or 0)
    completed = int(outcome.get("groups_completed") or 0) or (answered + reused)
    # What the route put into the evidence base. The state loop counts rows; the crop and band loops count
    # one answer per frame, and their ``observations`` list is what the merge will read, so the run's
    # total is the number of readings this command added -- not the number of requests it spent.
    written = int(outcome.get("observations_written") or len(outcome.get("observations") or []) or 0)
    return {"route": outcome.get("route"),
            "requests_sent": int(outcome.get("requests_sent") or 0),
            "groups_planned": int(outcome.get("groups_planned") or 0),
            "groups_answered": answered, "groups_resumed": reused, "groups_completed": completed,
            # Reported under both spellings because both are public today: the state loop's name for a
            # reply that arrived and was rejected, and the run's name for every route's failures.
            "groups_failed": failed, "groups_failed_validation": failed,
            "groups_failed_request": int(outcome.get("groups_failed_request") or 0),
            "groups_deferred_by_ledger": int(outcome.get("groups_deferred_by_ledger") or 0),
            "observations_written": written,
            "not_attempted": int(outcome.get("not_attempted") or 0),
            "stopped_reason": outcome.get("stopped_reason") or outcome.get("stopped")}


def route_error(history):
    # type: (Sequence[Dict[str, Any]]) -> Optional[str]
    """The first route failure a run hit, newest pass first.

    Each executor keeps its own error string; the run reports one, because a caller that has to decide
    whether to resume wants the reason it stopped and not three places to look.
    """
    for entry in reversed(list(history)):
        for outcome in entry.get("routes") or []:
            for candidate in (outcome.get("error"),
                              *(outcome.get("errors") or [])[:1]):
                if candidate:
                    return str(candidate)
    return None


def aggregate_routes(history):
    # type: (Sequence[Dict[str, Any]]) -> Dict[str, int]
    """Sum the per-route tallies across every pass, in the one dialect."""
    totals = {key: 0 for key in ROUTE_TALLY_KEYS}
    for entry in history:
        for outcome in entry.get("routes") or []:
            normalised = normalise_route_outcome(outcome)
            for key in ROUTE_TALLY_KEYS:
                totals[key] += normalised[key]
    return totals


def apply_expansion(timeline, frame_manifest, duration, region, prepared, expand,
                    expansion_progress=None, **prepare_kw):
    # type: (Any, Any, float, Optional[Sequence[float]], Dict[str, Any], float, Optional[Dict[str, Any]], **Any) -> Tuple[Dict[str, Any], Dict[str, Any]]
    """Widen the live run's own query when a candidate says the reason is that the query stopped.

    This is the execution-side half: a run started from the original narrow region has to *execute* the
    expanded window by itself, on the same ledger, allowance and deadline, rather than making the operator
    retype a wider ``--region``. Deciding costs nothing -- it reads the tracker's edges and the rows already
    on disk -- so the widening happens before any client exists, and everything the run then sends is
    already scoped to the wider window.

    It writes nothing. A run that is going to refuse must refuse without leaving a directory behind, so the
    caller persists ``expansion_progress`` itself once its guards have passed.
    """
    block = {"inference_sent": False, "attempts_charged_now": 0, "per_side_seconds": float(expand or 0.0),
             "original_window": [float(region[0]), float(region[1])] if region else None,
             "expanded_window": [float(region[0]), float(region[1])] if region else None,
             "triggered": False, "steps": [], "stopping_reasons": [], "edges": [], "blocked_by_edge": []}
    if not region:
        block["stopping_reasons"] = ["no_region_to_widen"]
        return prepared, block
    source = prepared.get("source") or {}
    if not source.get("proven") and not source.get("cache_known"):
        block["stopping_reasons"] = ["source_bounds_unknown"]
        return prepared, block
    if not expand or float(expand) <= 0.0:
        block["stopping_reasons"] = ["expansion_disabled"]
        return prepared, block
    needs = expansion_needs(prepared["tracked"], source)
    block["edges"], block["blocked_by_edge"] = needs["edges"], needs["blocked"]
    shape = expansion_window(region, needs, per_side=expand, progress=expansion_progress)
    block["steps"], block["stopping_reasons"] = shape["steps"], sorted(
        {str(step["stopping_reason"]) for step in shape["steps"]})
    if not shape["expanded"]:
        return prepared, block
    block["triggered"] = True
    block["expanded_window"] = shape["expanded_window"]
    block["observations"] = {"original": len(prepared["observations"]), "expanded": None}
    wider = _prepare(timeline, frame_manifest, duration, region=tuple(shape["expanded_window"]),
                     **prepare_kw)
    block["observations"]["expanded"] = len(wider["observations"])
    block["windows"] = {"original": [float(region[0]), float(region[1])],
                        "expanded": list(shape["expanded_window"])}
    cursor = {"original_window": block["windows"]["original"],
              "expanded_window": list(shape["expanded_window"]),
              "steps": [{"side": step["side"], "expanded_to": step["to"], "moved": step["moved"],
                         "stopping_reason": step["stopping_reason"]} for step in shape["steps"]]}
    block["expansion_progress"] = cursor
    return wider, block


def investigate_saved_games(timeline, frame_manifest, duration, output_dir, marks=None,
                            region=None, endpoint=None, model=None, timeout=1800,
                            max_attempts=2, client_factory=None, client_factories=None,
                            resume=False, header=None, crop_runs=None, state_only=False,
                            fixture=None, evidence_root=None, format_policy=None,
                            budget_file=None, budget_cap=boundary_budget.DEFAULT_CAP,
                            budget_minutes=boundary_budget.DEFAULT_MINUTES,
                            purpose="investigate-games", passes=DEFAULT_PASSES, routes=None,
                            **kw):
    # type: (Any, Any, float, Any, Optional[Any], Optional[Sequence[float]], Optional[str], Optional[str], int, int, Optional[Any], bool, **Any) -> Dict[str, Any]
    """Ask the recording what it still owes, on the route that can answer it, and re-judge.

    One pass is: plan from the evidence in hand, dispatch the questions it names to the crops arm, the
    state client and the band, merge every answer back through the canonical merge, and re-adjudicate.
    Then plan again, because an answer is how the next question is found. A request whose answer is
    already on disk is not re-sent, whichever pass or directory or invocation put it there: the identity
    is the content of the send, not a row number.

    It stops for one of four reasons and says which: nothing is left that this recording can answer
    (``supported_outcome``), a question was left unasked and named (``named_deferral``), a pass produced
    no evidence the last one did not already hold (``no_new_evidence``), or the ledger is spent. Stopping
    is a result, not a crash: everything paid for is on disk and a resume re-asks nothing.

    A fresh run needs an empty directory. ``resume`` is the only way back into one that has work in it,
    and it refuses when the inputs differ from those that produced the saved groups: silently reusing
    another run's evidence is worse than repeating a few requests.

    **A live send requires a ledger.** ``budget_file`` names the shared attempt ledger that carries the cap
    and the single wall-time deadline across every command, every route and every resume; without it this
    refuses before any client exists. ``--max-requests`` alone is per-process, so every re-run re-armed it
    and nothing recorded what was spent -- the hole that sent six real band requests uncounted on
    2026-10-30 while somebody was checking that the command would refuse an unapproved execute. A
    transport may still be injected without a ledger, because a stand-in cannot spend anything; the
    injection is per route, and a route left uninjected still needs the ledger.
    """
    out = Path(str(output_dir))
    occupied = out.exists() and any(out.iterdir())
    if occupied and not resume:
        raise ValueError("refusing to overwrite a non-empty output dir: {} "
                         "(pass resume to continue it)".format(out))
    rounds_per_game = kw.pop("rounds_per_game", boundary_games.DEFAULT_ROUNDS_PER_GAME)
    games_per_set = kw.pop("games_per_set", boundary_games.DEFAULT_GAMES_PER_SET)
    allowance = int(kw.get("max_requests") or DEFAULT_MAX_REQUESTS)
    factories = dict(client_factories or {})
    if client_factory is not None:
        factories.setdefault("state", client_factory)
    # Injecting a transport for some routes and not the others must never leave a route holding a live
    # client. So an injection restricts the run to the routes injected, unless the caller also named
    # ``routes`` -- in which case naming a route with no transport is a deliberate request to send for
    # real, and the ledger guard below still refuses it without a budget file. Without this, a test that
    # mocked one socket would quietly build two clients pointed at a real endpoint, and the difference
    # between that and an unapproved spend is only whether anything happened to be listening.
    if factories and routes is None:
        routes = tuple(route for route in ROUTE_ORDER if route in factories)
    prepared = _prepare(timeline, frame_manifest, duration, marks=marks, region=region,
                        rounds_per_game=rounds_per_game, games_per_set=games_per_set, header=header,
                        crop_runs=crop_runs, state_only=state_only, fixture=fixture,
                        evidence_root=evidence_root, format_policy=format_policy)
    # Widen the query before a client exists, from the run's own original region: an operator asking about
    # 2400-3200 gets the footage the candidate says is missing, on this ledger and this allowance, without
    # retyping a wider --region. Reading back a previous cursor is what keeps a resume from walking ground
    # the first run already covered.
    expand = float(kw.pop("expand", EXPANSION_SECONDS_PER_SIDE) or 0.0)
    # A caller may hand a cursor in directly (the CLI does, from --expansion-progress); a run with its own
    # record in its output directory prefers that record, because it is the evidence of what was done
    # rather than of what was intended.
    passed_cursor = kw.pop("expansion_progress", None)
    progress_path = out / "expansion.json"
    saved_cursor = json.loads(progress_path.read_text(encoding="utf-8")) \
        if progress_path.is_file() else None
    carried = saved_cursor or passed_cursor
    if saved_cursor and resume:
        # The window a run executed is part of its input identity, and a resume that re-derived it from a
        # different --expand-window would refuse its own directory or, worse, silently work a wider span
        # than the attempts on disk were charged for. So a resume continues the widening that was decided
        # the first time and says so.
        prepared = _prepare(timeline, frame_manifest, duration, marks=marks,
                            region=tuple(saved_cursor["expanded_window"]),
                            rounds_per_game=rounds_per_game, games_per_set=games_per_set, header=header,
                            crop_runs=crop_runs, state_only=state_only, fixture=fixture,
                            evidence_root=evidence_root, format_policy=format_policy)
        expansion = {"inference_sent": False, "attempts_charged_now": 0,
                     "per_side_seconds": float(expand),
                     "original_window": list(saved_cursor["original_window"]),
                     "expanded_window": list(saved_cursor["expanded_window"]),
                     "triggered": False, "steps": [],
                     "stopping_reasons": ["already_expanded"], "edges": [], "blocked_by_edge": [],
                     "windows": {"original": list(saved_cursor["original_window"]),
                                 "expanded": list(saved_cursor["expanded_window"])},
                     "resumed_from": str(progress_path)}
    else:
        # Decided before any client exists and before any directory is created: a run that goes on to
        # refuse must refuse without leaving a half-made output dir behind.
        prepared, expansion = apply_expansion(
            timeline, frame_manifest, duration, region, prepared, expand, expansion_progress=carried,
            marks=marks, rounds_per_game=rounds_per_game,
            games_per_set=games_per_set, header=header, crop_runs=crop_runs, state_only=state_only,
            fixture=fixture, evidence_root=evidence_root, format_policy=format_policy)
    working_region = prepared["window"]
    if occupied:
        # A resume plans from *today's* evidence, and the answers this directory wrote are today's
        # evidence. Without this fold the first plan of a resumed run saw only the caller's corpus, the
        # store-level dedupe then talked it out of sending what it had priced, and the run exited
        # ``supported_outcome`` while bounded frames the answers had just retired were never even offered:
        # reuse that outruns continuation is the false exhaustion again. The fold carries the two stores
        # whose questions are per instant -- the crop reading of a frame, the band reading of a frame --
        # so an answer that landed between invocations retires its instant and advances the bounded pool
        # instead of being discovered only after it suppressed a send. The state route is deliberately
        # not folded here (``own_state=False``): it packs *batches* off its frame grid, and merging its
        # answers at plan time re-grips every fingerprint and re-sorts the queue between two invocations
        # of the same command. Its reuse stays the loop's, which matches a saved group by fingerprint
        # before sending and has its own reconciliation tests. The crop fold can still move a state
        # region through the games it concludes; that is the recording talking, and it is what the next
        # pass of a live run would price too. (``_input_identity`` below compares the *caller-named*
        # corpus: the run's own store is the point of a resume, not a changed input.)
        prepared = _refresh(prepared, out, header, timeline, frame_manifest, duration, marks,
                            working_region, rounds_per_game, games_per_set, format_policy, state_only,
                            fixture, evidence_root, crop_runs, own_ledger=budget_file, own_state=False)
    first = plan_investigation(
        prepared["unresolved"], prepared["cache"], prepared["duration"],
        observations=prepared["observations"], marks=prepared["marks"], window=prepared["window"],
        endpoint=endpoint, model=model,
        crop_readings=prepared["evidence"].get("crop_readings_by_frame"),
        identity_questions=prepared["adjudicated"]["identity_questions"], routes=routes,
        header=prepared.get("header") or header, **kw)
    enabled = route_filter(routes)
    priced = {route["route"]: route["requests_planned"] for route in first["dispatch"]["routes"]}
    unledgered = [route for route in enabled if priced[route] and route not in factories]
    if unledgered and not budget_file:
        raise ValueError(
            "investigate-games sends real requests on {} and requires --budget-file: attempts that no "
            "ledger counts are attempts nobody approved, and --max-requests alone resets every process. "
            "Pass --dry-run to price the plan, or inject a transport for testing.".format(unledgered))
    identity = _input_identity(timeline, frame_manifest, duration, first, header=header,
                               evidence=dict(prepared["evidence"],
                                             crop_runs=[str(path) for path in
                                                        prepared["evidence"].get("crop_runs") or ()
                                                        if str(path) != str(out / "pip")]),
                               format_policy=format_policy)
    identity_path = out / "investigation-inputs.json"
    if occupied and identity_path.is_file():
        saved = json.loads(identity_path.read_text(encoding="utf-8"))
        differing = {key: [saved.get(key), value] for key, value in identity.items()
                     if saved.get(key) != value}
        if differing:
            raise ValueError("resume refused: this directory came from different inputs: {}".format(
                differing))
    out.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8")
    # The window the run asked for, the window it decided to execute, and why: without this a widened run
    # is indistinguishable from one that was started wide, which is exactly the ambiguity an audit needs
    # least. `expanded_plan` is not carried -- the plan below is the expanded one.
    first["expansion"] = expansion
    (out / "plan.json").write_text(json.dumps(first, indent=2, sort_keys=True), encoding="utf-8")
    # The resume cursor, written only now that the guards have passed: a run that refuses must not leave it,
    # because a cursor is a promise about a window this directory never worked.
    if expansion.get("expansion_progress"):
        progress_path.write_text(json.dumps(expansion["expansion_progress"], indent=2, sort_keys=True),
                                 encoding="utf-8")

    ledger = (boundary_budget.AttemptLedger.open(
        budget_file, cap=budget_cap, minutes=budget_minutes,
        label="{}:{}".format(out.name, purpose)) if budget_file else None)
    clients = {}
    for route in ROUTE_ORDER:
        if route not in enabled:
            # An unfunded route gets no client at all. Building one "just in case" is how a route that was
            # never approved ends up with a live endpoint attached to it; the plan already says which
            # routes this invocation paid for, and the dispatch loop reports them as unfunded.
            clients[route] = None
            continue
        # A factory is handed the allowance, exactly as the single-route hook always was: the injected
        # transport enforces its own cap, and a test fake that counted requests would silently have none.
        client = factories[route](allowance) if route in factories else _client(
            route, endpoint=endpoint, model=model, timeout=timeout, max_attempts=max_attempts,
            out=out, allowance=allowance)
        if ledger is not None and route not in factories:
            if route == "state":
                # The state runner names each attempt with its group fingerprint as it walks, which already
                # identifies that request uniquely across routes.
                client = boundary_budget.attach(client, ledger,
                                                purpose="{}:{}".format(purpose, route))
            else:
                client = boundary_budget.attach(client, ledger, purpose="{}:{}".format(purpose, route),
                                                tag=_tag_for(route))
        clients[route] = client

    by_path = {frame["path"]: frame for frame in prepared["cache"]}
    # Snapshotted before anything is dispatched, because "before" is the whole point of the comparison. A
    # run that re-derived it from the evidence state at the end would report the answers it had just paid
    # for as the starting position, and every gain in the report would vanish -- which is a quieter
    # dishonesty than the one it replaces, because the numbers still look plausible.
    before = {"recovery": prepared["adjudicated"]["recovery"],
              "tracked": prepared["adjudicated"]["tracked"]["assisted"],
              "accounting": prepared["adjudicated"]["tracked"]["assisted"]["accounting"],
              "unresolved": _kind_counts(prepared["adjudicated"]["tracked"]["assisted"]["unresolved"])}
    runner = TypedInvestigationRun(clients, out, max_requests=allowance, ledger=ledger)
    history = []  # type: List[Dict[str, Any]]
    stop = {"reason": "passes_exhausted", "detail": "no pass reached a stop of its own"}
    try:
        last_plan = first
        for attempt in range(1, max(1, int(passes)) + 1):
            plan = first if attempt == 1 else _replan(prepared, attempt, endpoint, model, kw,
                                                       routes=routes)
            last_plan = plan
            (out / "plan-pass-{}.json".format(attempt)).write_text(
                json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
            for region_plan in plan["regions"]:
                for group in region_plan["groups"]:
                    for path, stamp in zip(group["frame_paths"], group["frame_timestamps"]):
                        by_path.setdefault(path, {"path": path, "timestamp": stamp, "id": None})
            if not plan["requests_planned"]:
                stop = {"reason": "supported_outcome",
                        "detail": "no route has a question left to ask inside this window"}
                history.append({"pass": attempt, "planned": 0, "sent": 0, "answers_added": 0,
                                "stopped": stop["reason"]})
                break
            units = dispatch_units(plan, out, clients, by_path)
            # Reconciled before anything else, including before the "nothing owed" exit below: closing a
            # hanging attempt is what an answer file on disk can prove, and a run that turns out to owe
            # nothing is exactly the run that would otherwise leave an attempt hanging forever -- the
            # ledger says spent, the store says answered, and no later invocation looks, because it has
            # nothing left to send.
            _reconcile(ledger, units, out, plan)
            if not _owed(units):
                # Every request this plan prices is already answered on disk. The questions that remain
                # are ones no route can close with a send, so the run is finished in the only sense a
                # budget can finish it -- and calling that "no new evidence" would hide the fact that the
                # next resume owes nothing at all.
                stop = {"reason": "supported_outcome",
                        "detail": "every request in this plan is answered on disk; the open questions "
                                  "are not answerable by sending"}
                history.append({"pass": attempt, "planned": plan["requests_planned"], "owed": 0,
                                "sent": 0, "answers_added": 0, "stopped": stop["reason"],
                                # Counted as reuse, because that is what it is: the run found every request
                                # in its own plan already answered and sent nothing. Reporting zero here
                                # would make a fully reused run indistinguishable from one that had no
                                # work to reuse.
                                "routes": [{"route": "reused", "requests_sent": 0,
                                            "groups_reused": sum(len(units["identities"][route])
                                                                 for route in ROUTE_ORDER)}],
                                "deferred": []})
                break
            _before_sending(ledger, units, out, plan)
            spent_before = runner.spent()
            open_before = sum(_kind_counts(prepared["unresolved"]).values())
            outcome = runner.run(plan, cache_by_path=by_path,
                                 crop_records=units["crop_records"],
                                 band_requests=units["band_requests"])
            added = sum(int(entry.get("groups_answered") or 0) for entry in outcome["routes"])
            history.append({"pass": attempt, "planned": plan["requests_planned"],
                            "owed_before_this_pass": _owed(units),
                            # The pass's own sends, not the run's running total: a report that repeated the
                            # cumulative figure made every later pass look like it had spent the whole
                            # allowance again.
                            "sent": runner.spent() - spent_before, "sent_to_date": runner.spent(),
                            "answers_added": added, "stopped": outcome.get("stopped"),
                            "questions_open": open_before,
                            "routes": outcome["routes"], "error": outcome.get("error"),
                            "deferred": _deferrals(units, out)})
            if outcome.get("stopped"):
                # The route that stopped knows why, and its reason is the one an operator acts on:
                # "the cap was reached on the crop route" is actionable, "the ledger is empty" is not.
                stop = {"reason": outcome["stopped"], "detail": ledger.summary() if ledger else None}
                break
            if ledger is not None and (not ledger.remaining or (ledger.deadline_at is not None
                                                                and ledger.seconds_left() <= 0)):
                stop = {"reason": "attempt_budget_spent", "detail": ledger.summary()}
                break
            if not added:
                stop = {"reason": "no_new_evidence",
                        "detail": "this pass persisted no answer the earlier passes did not hold"}
                break
            # The window this run is *working*, which after widening is not the one the operator typed: a
            # second pass that re-prepared the requested region would silently shrink the run back to where
            # it started and re-price footage the widening had just recovered for free.
            prepared = _refresh(prepared, out, header, timeline, frame_manifest, duration, marks,
                                working_region, rounds_per_game, games_per_set, format_policy, state_only,
                                fixture, evidence_root, crop_runs, own_ledger=budget_file)
            first = plan
            # Progress, not persistence, is what a loop like this should stop on. A pass can answer every
            # request it made and leave the queue exactly as long -- the state route reports "no cue here"
            # about one more frame and the tracker asks for the next -- and counting answers alone would
            # call that progress forever. Counting open questions stops it and says what it hit.
            open_after = sum(_kind_counts(prepared["unresolved"]).values())
            history[-1]["questions_after"] = open_after
            if open_after >= open_before:
                stop = {"reason": "no_new_evidence",
                        "detail": "{} requests were answered and no question closed: {} remain open, and "
                                  "the frames still owed do not settle them".format(added, open_after)}
                history[-1]["stopped"] = stop["reason"]
                break
    except KeyboardInterrupt as exc:
        _write_interruption(out, runner, ledger, history)
        raise exc

    if stop["reason"] == "passes_exhausted" and history and history[-1].get("stopped"):
        stop = {"reason": history[-1]["stopped"], "detail": None}
    # One last reconciliation before the ledger is reported and the process ends. A run that answered a
    # request it had once left hanging must not walk away describing that attempt as still unknown: the
    # answer is on disk, this run put it there, and the ledger it leaves behind is what the next operator
    # reads. Closing at the end is not tidiness -- without it, ``in_flight`` would name work that is done,
    # and a later refusal computed from what is still owed would be computed from a lie.
    reconciled_at_end = _reconcile(ledger, {}, out, {})
    # One refresh either way, so the after-state is built the same way every pass was: the crop and band
    # answers are read from their stores by the loaders that read the corpus, and the state answers from the
    # group files this directory wrote. Nothing here merges by hand, which is what would let the report and
    # the plan disagree about the run that produced both.
    prepared = _refresh(prepared, out, header, timeline, frame_manifest, duration, marks,
                        working_region, rounds_per_game, games_per_set, format_policy, state_only, fixture,
                        evidence_root, crop_runs, own_ledger=budget_file)
    merged = prepared["rows"]
    after_header = prepared["header"] or header
    after = boundary_games.track_games(prepared["observations"],
                                       rounds_per_game=rounds_per_game, games_per_set=games_per_set,
                                       header=after_header, format_policy=format_policy,
                                       # Bounded by the same region as the before-state: an unbounded
                                       # after-pass compares a whole recording against one window.
                                       analysis_window=prepared["window"], source=prepared["source"])
    sent = runner.spent()
    result = {"status": ("stopped" if stop["reason"] not in ("supported_outcome", "no_new_evidence")
                         else "complete"),
              "stopped_reason": stop["reason"], "stop_detail": stop.get("detail"),
              # The window the operator asked for and the one that was actually worked, so a widened run
              # reports the widening rather than hiding it inside a different region.
              "expansion": {key: value for key, value in expansion.items() if key != "expansion_progress"},
              "region_requested": [float(region[0]), float(region[1])] if region else None,
              "region_executed": [prepared["window"][0], prepared["window"][1]] if prepared["window"] else None,
              "unresolved_before": before["unresolved"],
              "unresolved_after": _kind_counts(after["unresolved"]),
              "requests_sent": sent,
              # What every route did, summed across every pass, in one dialect: a report that only said
              # "the state loop answered 2 groups" while the crop loop answered 36 has an accounting
              # problem, and the per-route breakdown sits beside these totals in ``passes``.
              **aggregate_routes(history),
              "error": route_error(history),
              "budget": {"attempt_allowance": allowance, "attempts_used": sent,
                         "attempts_remaining": max(0, allowance - sent),
                         "planned_by_route": priced,
                         # What the routes this run did not fund would still cost. Printed beside the
                         # sends because a resume and a re-approval are written from different columns,
                         # and a single zero cannot tell them apart.
                         "unfunded_by_route": {route["route"]: route.get("requests_unfunded", 0)
                                               for route in first["dispatch"]["routes"]},
                         "sent_by_route": {route: int(getattr(clients[route], "calls", 0) or 0)
                                           if clients.get(route) is not None else 0
                                           for route in ROUTE_ORDER},
                         "groups_planned": first["requests_planned"],
                         "groups_answered": sum(int(entry.get("groups_answered") or 0)
                                                for pass_entry in history
                                                for entry in pass_entry.get("routes") or []),
                         "groups_failed_validation": sum(
                             int(entry.get("groups_failed") or 0) for pass_entry in history
                             for entry in pass_entry.get("routes") or []),
                         "ledger": ledger.summary() if ledger else None,
                         "ungoverned": ledger is None},
              "token_usage": [dict(entry, route=route) for route in ROUTE_ORDER
                              for entry in (getattr(clients[route], "usage", []) or [])],
              "passes": history, "evidence": _evidence_source(prepared, header, format_policy),
              "dispatch_before": first["dispatch"],
              "dispatch_after": _dispatch_after(prepared, last_plan),
              "deferrals": [entry for pass_entry in history
                        for entry in pass_entry.get("deferred") or []],
              "recovery_before": before["recovery"],
              # Re-adjudication, not a re-count: every route's answers go back through the canonical merge
              # and the same three comparison passes the read-only report runs, so an answer this command
              # paid for is worth exactly what the same answer on disk from last week is worth.
              "recovery_after": prepared["adjudicated"]["recovery"],
              "tracker_before": before["accounting"],
              "tracker_after": after["accounting"],
              "resolved": _resolved_kinds(before["tracked"], after),
              "cache_frames_read": len(prepared["cache"]),
              "header_evidence": _header_summary(after_header, after)}
    (out / "investigation.json").write_text(json.dumps(
        {"plan": {key: value for key, value in first.items()
                  if key not in ("regions", "pip_requests", "header_requests")},
         "result": result}, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (out / "timeline-with-facts.json").write_text(json.dumps(merged, indent=2, default=str),
                                                 encoding="utf-8")
    (out / "games-trace-after.json").write_text(json.dumps(after, indent=2, sort_keys=True,
                                                          default=list), encoding="utf-8")
    result["status_name"] = "investigation"
    return result




def _header_source(header):
    # type: (Optional[Dict[str, Any]]) -> Optional[str]
    return header.get("source_directory") if header else None


def _header_digest(header):
    # type: (Optional[Dict[str, Any]]) -> Optional[str]
    """A fingerprint of exactly which answers were in the room, for the resume check."""
    if not header:
        return None
    seed = "|".join("{}={}".format(r.get("at"), r.get("image_sha256"))
                    for r in header.get("readings") or ())
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _header_summary(header, after):
    # type: (Optional[Dict[str, Any]], Dict[str, Any]) -> Dict[str, Any]
    if not header:
        return {"supplied": False, "route": "no_band_evidence", "inference_sent": False,
                "attempts_charged_now": 0}
    settled, open_questions = [], []
    for record in after.get("sets") or ():
        for alt in record.get("alternative_groupings") or ():
            entry = alt.get("band_mapping") or {}
            if not entry:
                continue
            (open_questions if entry.get("status") in ("uncovered", "contested") else
             settled if entry.get("status") != "no_mapping_question" else []).append(
                {"at": alt.get("at"), "status": entry.get("status")})
    return {"supplied": True, "route": "saved_readings_reused",
            "readings": len(header.get("readings") or ()),
            "source_directory": header.get("source_directory"), "provenance": header.get("provenance"),
            "inference_sent": False, "attempts_charged_now": 0,
            "mapping_questions_settled": settled, "mapping_questions_still_open": open_questions,
            "resolves": "who sat on which screen side, where a reading falls inside the game",
            "never_resolves": ["whether a set ended", "a game or round result"]}


def _resolved_kinds(before, after):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """What the extra frames actually changed, counted rather than described."""
    kinds = {}
    for label, result in (("before", before), ("after", after)):
        counts = {}
        for key in ("rounds", "games", "games_incomplete", "clock_readings"):
            counts[key] = result["accounting"].get(key)
        kinds[label] = counts
    kinds["delta"] = {key: kinds["after"][key] - kinds["before"][key]
                      for key in kinds["after"]}
    return kinds
