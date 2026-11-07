"""Which groupings of a game sequence the inspected evidence leaves open -- and what each claim rests on.

A set trial asks the one question the clock cannot answer: the recording says where games end and the pip
row says who took them, and only the structure above that is in doubt. The temptation, when a trial is
written before it runs, is to name two candidate structures and grade the run as a choice between them.
That is honest only if the two are exhaustive, and they are not: four games group eight ways, and on this
tape the inspected scoreboard column contradicts both favourites at once -- it climbs across the boundary
the tracker drew between its two candidates and never resets at any of them.

So this module does not pick a winner among hypotheses. It

* enumerates the groupings of the observed games (every way of cutting the sequence into consecutive runs,
  not a pair of favourites), so a trial cannot restrict truth to what somebody guessed first;
* grades each grouping on **temporal** evidence -- the recording's own sequence of clocks, transitions and
  station states -- separately from its **transcription** score, because reading a pixel correctly says
  nothing about what a frame belongs to;
* treats the overlay's games column, a menu or controller prompt, and a bracket-reported match change as
  *supporting*: this recording contains a tally that moved tens of seconds after the round that decided it
  and an identity change inside a running round, so none of the three can be sufficient on its own;
* keeps every grouping that survives, names what contradicts the ones that do not, reports where two
  families point different ways instead of resolving it by precedence, and leaves a run that touches the
  edge of the analysed stretch **partial** rather than dropping it or closing it;
* runs with or without a bracket format: a target may name a winner, and its absence vetoes nothing the
  footage settled by itself.

Nothing here sends a request, and nothing reads a tracker's guess as truth: the game spans are an argument
precisely so the grouping under test can be somebody else's, including wrong.
"""
import itertools
from typing import Any, Dict, List, Optional, Sequence

#: What each family of evidence is allowed to do, kept beside the code that applies it so a role cannot be
#: quietly promoted downstream: only a pip row reaching its round target settles a game winner, and no
#: display column, menu screen or bracket note settles a set.
EVIDENCE_ROLES = {
    "pip_row": "sufficient_for_a_game_winner_within_one_game",
    "round_clock_and_transition_sequence": "temporal_boundary_support",
    "station_cleared_or_controller_prompt": "supporting_only",
    "overlay_games_tally": "supporting_only",
    "bracket_match_change": "supporting_only",
    "bracket_format": "optional_never_a_veto",
}

#: The claims a set trial publishes, in separate fields. Reporting one as though it settled the others is
#: the overreach this file exists to prevent.
CLAIMS = ("transcription", "temporal", "grouping", "score", "winner", "footage")

#: Four games give eight groupings, eight give 128. Past that the enumeration stops being a check on a
#: hypothesis and becomes a search, which is not what a bounded-interval trial should be doing.
MAX_GAMES = 8


# ---------------------------------------------------------------- per-game readings

def game_conclusions(labels: Dict[str, Any], games: Sequence[Sequence[float]],
                     rounds_per_game: int = 3,
                     conclusion_grace_seconds: float = 4.0) -> List[Dict[str, Any]]:
    """Per game span: what the inspected pip labels say, and where they say nothing.

    A winner is named only from a *graded* label whose row reaches the round target, and that target
    belongs to a game (three round pips), never to a set. The search runs a few seconds past the span's
    end because the row that proves a game concluded is drawn after the knockout it follows -- the trial's
    own samples are the anchor and +2 s and +4 s, so the grace is the sampling, not a licence to look
    anywhere. Each conclusion carries the instant it was seen, so a row landing past the boundary the
    tracker drew is visible as that rather than silently absorbed.

    A span whose deciding frame was occluded, flared or never sampled comes back ``unreadable``, holding
    the last row that could be read: the honest headline is how much of the structure the labels settle,
    not how much they can be made to settle.
    """
    frames = sorted(labels.get("frames") or [], key=lambda f: f["timestamp"])
    out = []
    for index, span in enumerate(games):
        start, end = float(span[0]), float(span[1])
        inside = [f for f in frames if start - 1e-6 <= f["timestamp"] <= end + conclusion_grace_seconds]
        graded = [f for f in inside if f.get("graded")]
        decided = None
        for frame in graded:
            for side in ("LEFT", "RIGHT"):
                lit = (frame.get("pips") or {}).get(side)
                if lit is not None and lit >= rounds_per_game and (
                        decided is None or frame["timestamp"] < decided["at"]):
                    decided = {"at": frame["timestamp"], "side": side, "lit": lit,
                               "against": (frame.get("pips") or {}).get(
                                   "RIGHT" if side == "LEFT" else "LEFT"),
                               "past_the_span_end": frame["timestamp"] > end + 1e-6,
                               "frame": frame["frame"]}
        last = graded[-1] if graded else None
        out.append({"game": index, "start": start, "end": end,
                    "winner": decided["side"] if decided else None,
                    "concluded_at": decided["at"] if decided else None,
                    "conclusion_row_lands_past_the_span_end": bool(decided and
                                                                    decided["past_the_span_end"]),
                    "state": "readable" if decided else "unreadable",
                    "conclusion_grace_seconds": conclusion_grace_seconds,
                    "graded_frames_in_span": len(graded),
                    "last_readable_row": None if last is None else {
                        "at": last["timestamp"],
                        "pips": [last["pips"]["LEFT"], last["pips"]["RIGHT"]]},
                    "why_unreadable": None if decided else _unreadable_reason(inside, graded)})
    return out


def _unreadable_reason(inside, graded):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> str
    """Why the labels cannot name this game's winner, in terms of the frames that were actually asked."""
    if not inside:
        return "no labelled frame falls inside this span"
    if not graded:
        return "every labelled frame in the span is ungraded: an absent row or unknown truth"
    for frame in inside:
        if frame.get("row_drawn") is None:
            return ("the span's deciding frame is drawn but not closeable: {}"
                    .format((frame.get("basis") or "")[:180]))
    return ("the last readable row is {} against {} at {:.3f} s and nothing later in the span is graded"
            .format(graded[-1]["pips"]["LEFT"], graded[-1]["pips"]["RIGHT"],
                    graded[-1]["timestamp"]))


# ---------------------------------------------------------------- supporting readings

def tally_events(labels: Dict[str, Any],
                 games: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
    """The displayed games column between consecutive inspected band samples, and what it straddles.

    ``reset`` is what a new set is expected to display and ``climb`` what a game win inside one set
    displays, which is why the sequence is worth printing -- and why its role stays ``supporting_only``:
    the column is measured to move tens of seconds after the round that decided it, so a run of
    ``unchanged`` samples neither proves a boundary nor denies one. An unreadable pair is ``unknown``, and a
    boundary no sample pair spans is ``unsampled``: a gap is not evidence.
    """
    bands = sorted(labels.get("band_frames") or [], key=lambda b: b["at"])
    events = []
    for before, after in zip(bands, bands[1:]):
        pair = [_tally(before), _tally(after)]
        events.append({"between": [before["at"], after["at"]], "displayed": pair,
                       "kind": _kind(pair),
                       "spans_boundary_after_game": _straddles(before["at"], after["at"], games),
                       "role": EVIDENCE_ROLES["overlay_games_tally"]})
    return events


def pairing_changes(labels: Dict[str, Any],
                    games: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
    """Where the inspected nameplates differ: identity evidence, and a boundary of nothing on its own."""
    bands = sorted(labels.get("band_frames") or [], key=lambda b: b["at"])
    out = []
    for before, after in zip(bands, bands[1:]):
        first, second = before.get("expected_players") or {}, after.get("expected_players") or {}
        if first and second and first != second:
            out.append({"between": [before["at"], after["at"]], "before": first, "after": second,
                        "spans_boundary_after_game": _straddles(before["at"], after["at"], games),
                        "role": EVIDENCE_ROLES["overlay_games_tally"]})
    return out


def _tally(entry):
    # type: (Dict[str, Any]) -> List[Optional[int]]
    tally = entry.get("expected_games_tally") or {}
    return [tally.get("LEFT"), tally.get("RIGHT")]


def _kind(pair):
    # type: (List[List[Optional[int]]]) -> str
    (left_before, right_before), (left_after, right_after) = pair
    if None in (left_before, left_after, right_before, right_after):
        return "unknown"
    if (left_after, right_after) == (0, 0) and (left_before or right_before):
        return "reset"
    if left_after < left_before or right_after < right_before:
        return "fall"
    if (left_after, right_after) == (left_before, right_before):
        return "unchanged"
    return "climb"


def _straddles(low, high, games):
    # type: (float, float, Sequence[Sequence[float]]) -> Optional[int]
    """Which game boundary this sample pair covers, by index of the game before it, or ``None``.

    A pair *inside* one game shows the column mid-game, which says nothing about any boundary, so both
    sides of a boundary have to fall between the samples before the pair is allowed to speak for it.
    """
    for index in range(len(games) - 1):
        end_of_this, start_of_next = float(games[index][1]), float(games[index + 1][0])
        if low <= end_of_this and high >= start_of_next:
            return index
    return None


# ---------------------------------------------------------------- the grouping space

def candidate_groupings(count: int) -> List[List[int]]:
    """Every way of cutting ``count`` consecutive games into consecutive runs, by cut positions.

    A grouping is the list of internal boundary indices it keeps -- of four games, ``[]`` is one run of
    four and ``[0, 1, 2]`` is four runs of one. Enumerating them is the point: naming two of the eight as
    "the hypotheses" makes the other six unfalsifiable by construction.
    """
    if count < 1:
        return []
    if count > MAX_GAMES:
        raise ValueError("enumerating {} games would name {} groupings; investigate a shorter span"
                         .format(count, 2 ** (count - 1)))
    internal = list(range(count - 1))
    return [list(kept) for size in range(len(internal) + 1)
            for kept in itertools.combinations(internal, size)]


def runs_for(count: int, kept: Sequence[int]) -> List[List[int]]:
    """The runs one set of boundary indices produces, as game indices."""
    cuts = sorted(set(kept))
    edges = [0] + [cut + 1 for cut in cuts] + [count]
    return [list(range(a, b)) for a, b in zip(edges, edges[1:])]


def boundaries_from_recovery(report: Dict[str, Any],
                             games: Sequence[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Read a recovery report's candidate edges as the pipeline's **proposal**, never as temporal truth.

    The report grades an edge by the evidence family that carried it, and that verdict is the thing under
    test: a `station_cleared` edge means the detector saw a controller prompt where it cut a set, not that
    an independent reading of the recording settled one. Feeding it back in as a `temporal` verdict is
    circular -- `grade` would then contradict every grouping that omits the cut, and the trial could no
    longer find the pipeline wrong, which is the only reason to run it. So everything this returns stays in
    `proposed` and `prompts`, both supporting: the report is graded *against*, not *by*.

    Independent temporal evidence is still welcome, and it arrives the other way: a caller passes its own
    `boundaries` mapping with a `temporal` verdict, and only that can contradict a grouping.
    """
    candidates = sorted((report.get("recovery") or report).get("candidates") or [],
                        key=lambda candidate: candidate["start"])
    total = sum(int(candidate.get("games") or 0) for candidate in candidates)
    if total != len(games):
        raise ValueError("the report groups {} games and {} were given: the two are not describing one "
                         "stretch of footage".format(total, len(games)))
    out, seen = {}, 0
    for position, candidate in enumerate(candidates):
        seen += int(candidate.get("games") or 0)
        if position == len(candidates) - 1:
            continue
        edge = ((candidate.get("edges") or {}).get("end") or {})
        families = sorted(set(edge.get("evidence_for") or []))
        settled = (edge.get("settled_by") or {}).get("state")
        out[seen - 1] = {
            "proposed": edge.get("state") or (families[0] if families else None),
            "evidence_for": families,
            "evidence_against": sorted(set(edge.get("evidence_against") or [])),
            "prompts": "supported" if settled in ("controller_prompt", "controller_assignment")
            else "undetermined",
            # Not a verdict. A proposal, kept so the report can say whether a grouping agrees with the
            # pipeline -- and nothing more.
            "temporal": "undetermined",
            "settled_by": settled,
            "at": edge.get("at"),
            "role": "proposal_under_test",
        }
    return out


def grade(grouping: Sequence[int], games: Sequence[Dict[str, Any]],
          boundaries: Optional[Dict[int, Dict[str, Any]]] = None,
          events: Optional[Sequence[Dict[str, Any]]] = None,
          target: Optional[int] = None) -> Dict[str, Any]:
    """Grade one grouping: what its boundaries rest on, and what survives them.

    ``boundaries`` maps a game index (the boundary *after* that game) onto ``{"temporal": ...}`` and
    optionally ``{"prompts": ...}``, each ``supported`` / ``contradicted`` / ``undetermined``. A grouping is
    ``contradicted`` when a boundary it keeps is contradicted or a boundary it *denies* is supported by the
    sequence; ``supported`` when every boundary it keeps is supported; ``open`` otherwise. The overlay
    column and the prompts are reported beside that verdict and never inside it, and ``score`` and
    ``winner`` exist only when a target was supplied.
    """
    boundaries, events = boundaries or {}, events or []
    count = len(games)
    kept = sorted(set(grouping))
    denied = [index for index in range(count - 1) if index not in kept]
    verdicts = {index: (boundaries.get(index) or {}).get("temporal", "undetermined") for index in kept}
    contradicted_by = [index for index, state in verdicts.items() if state == "contradicted"]
    denied_supported = [index for index in denied
                        if (boundaries.get(index) or {}).get("temporal") == "supported"]
    prompts_supported = [index for index in kept
                         if (boundaries.get(index) or {}).get("prompts") == "supported"]
    if contradicted_by or denied_supported:
        state = "contradicted"
    elif kept and all(known == "supported" for known in verdicts.values()):
        state = "supported"
    else:
        state = "open"

    runs, partial = runs_for(count, kept), []
    for run in runs:
        if run[0] == 0 and not games[0].get("start_is_observed", True):
            partial.append({"games": run, "edge": "leading",
                            "because": "the run opens before the analysed stretch, so whatever set it "
                                       "belongs to is not on film here"})
        if run[-1] == count - 1 and not games[-1].get("end_is_observed", True):
            partial.append({"games": run, "edge": "trailing",
                            "because": "the run continues past the analysed stretch, and footage that was "
                                       "not looked at cannot close it"})
    kept_kinds = [{"boundary": index, "kind": _event(index, events)} for index in kept]
    denied_kinds = [{"boundary": index, "kind": _event(index, events)} for index in denied]
    resets = [entry for entry in kept_kinds if entry["kind"] == "reset"]
    out = {"boundaries": kept, "runs": runs, "status": state,
           "temporal": {"per_boundary": {"{}".format(index): verdicts[index] for index in kept},
                        "contradicted_by": contradicted_by,
                        "denied_but_supported_by_the_sequence": denied_supported,
                        "note": "graded from the recording's own sequence; a prompt or a display column is "
                                "reported beside this, never inside it"},
           "prompts": {"supported": prompts_supported,
                       "role": EVIDENCE_ROLES["station_cleared_or_controller_prompt"]},
           "overlay_tally": {"kept": kept_kinds, "denied": denied_kinds,
                             "boundaries_implied": len(kept),
                             "resets_observed": [entry["boundary"] for entry in resets],
                             # Supporting, not decisive: a grouping that implies three set boundaries while
                             # the column resets nowhere is doubtful, and "doubtful" is a report line, not a
                             # contradiction -- the column is measured to lag the round that decided it.
                             "column_disagrees": bool(kept) and not resets,
                             "role": EVIDENCE_ROLES["overlay_games_tally"], "can_decide": False},
           "clashes": _clashes(kept, denied, verdicts, boundaries, events),
           # What the pipeline itself proposed here, kept for the reader to compare against this grouping.
           # It has no effect on ``status`` above, and an empty ``denied`` list of proposals is the whole
           # point of not reading a detector's edge as evidence.
           "pipeline_proposal": {"kept": [index for index in kept if boundaries.get(index)],
                                 "denied": [index for index in denied if boundaries.get(index)],
                                 "agrees_with_the_pipeline":
                                     sorted(boundaries) == kept if boundaries else None,
                                 "role": EVIDENCE_ROLES["station_cleared_or_controller_prompt"]},
           "partial_runs": partial,
           "games_of": count,
           "games_named_a_winner": sum(1 for game in games if game.get("winner")),
           "games_unreadable": sum(1 for game in games if game.get("state") == "unreadable"),
           "format_supplied": target is not None,
           "score": None, "winner": None, "missing_evidence": None}
    if target:
        # A tally belongs to a run, not to the whole sequence: four one-game matches are four scores of
        # 1-0, and one set of four games is one score of 2-1. Publishing the second number for the first
        # grouping is how a score would silently describe games it does not contain.
        scored = []
        for run in runs:
            members = games[run[0]:run[-1] + 1]
            score = {side: sum(1 for game in members if game.get("winner") == side)
                     for side in ("LEFT", "RIGHT")}
            decided = [side for side in ("LEFT", "RIGHT") if score[side] >= target]
            unreadable = sum(1 for game in members if game.get("state") == "unreadable")
            scored.append({"games": run, "score": score,
                           "winner": decided[0] if len(decided) == 1 else None,
                           "unreadable_games": unreadable,
                           "missing_evidence": ("{} of these games have no readable winner in the "
                                                "inspected labels, so this score is a floor, not a tally"
                                                .format(unreadable) if unreadable else None)})
        out["runs_scored"] = scored
        if len(scored) == 1:
            out["score"] = scored[0]["score"]
            out["winner"] = scored[0]["winner"]
            out["missing_evidence"] = scored[0]["missing_evidence"]
    return out


def _clashes(kept, denied, verdicts, boundaries, events):
    # type: (Sequence[int], Sequence[int], Dict[int, str], Dict[int, Dict[str, Any]], Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Where two families point different ways, named instead of resolved.

    Two things are checked, and neither one decides anything. Independent temporal evidence that supports a
    boundary the displayed column does not reset is a clash; so is the **pipeline's own proposal** -- a cut
    it drew on a controller prompt, where the column shows no reset. The second is the interesting one for a
    trial, because it says the thing under test and the supporting display disagree, and it is reported
    precisely so nobody has to promote either into a verdict to notice it.
    """
    out = []
    for index in kept:
        kind = _event(index, events)
        record = boundaries.get(index) or {}
        proposed = record.get("proposed")
        if verdicts.get(index) == "supported" and kind in ("climb", "unchanged", "fall"):
            out.append({"boundary": index, "between": "temporal_and_overlay_tally",
                        "temporal": "supported", "overlay_tally": kind,
                        "reading": "the sequence cuts a set here and the column does not reset here"})
        elif proposed in ("station_cleared", "bracket_match_change") and kind in ("climb", "unchanged",
                                                                                  "fall"):
            out.append({"boundary": index, "between": "proposal_and_overlay_tally",
                        "proposed": proposed, "settled_by": record.get("settled_by"),
                        "overlay_tally": kind, "decides": False,
                        "reading": "the pipeline cut a set here on its own edge evidence and the column "
                                   "shows no reset; both are claims about the same instant"})
    for index in denied:
        kind = _event(index, events)
        if kind == "reset" and (boundaries.get(index) or {}).get("temporal") == "contradicted":
            out.append({"boundary": index, "between": "temporal_and_overlay_tally",
                        "temporal": "contradicted", "overlay_tally": kind,
                        "reading": "the column resets here and the sequence says play went on"})
    return out


def _event(index, events):
    # type: (int, Sequence[Dict[str, Any]]) -> str
    kind = next((event["kind"] for event in events
                 if event.get("spans_boundary_after_game") == index), None)
    return kind or "unsampled"


# ---------------------------------------------------------------- the report

def evaluate(labels: Dict[str, Any], games: Sequence[Dict[str, Any]],
             boundaries: Optional[Dict[int, Dict[str, Any]]] = None,
             target: Optional[int] = None, rounds_per_game: int = 3,
             transcription: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Grade every grouping of these games against the inspected labels and the temporal verdicts.

    ``games`` is the span list under test -- ``start``, ``end``, and whether each end was observed on the
    recording -- and ``boundaries`` the per-boundary temporal verdicts. The report keeps the claims apart
    and refuses to name one grouping as the answer while more than one survives.
    """
    if not games:
        raise ValueError("no game spans were supplied, so there is no structure to grade")
    boundaries = boundaries or {}
    spans = [[game["start"], game["end"]] for game in games]
    merged = [dict(conclusion,
                   start_is_observed=bool(game.get("start_is_observed", True)),
                   end_is_observed=bool(game.get("end_is_observed", True)))
              for conclusion, game in zip(game_conclusions(labels, spans, rounds_per_game), games)]
    events = tally_events(labels, spans)
    groupings = [grade(grouping, merged, boundaries, events, target)
                 for grouping in candidate_groupings(len(merged))]
    surviving = [g for g in groupings if g["status"] in ("supported", "open")]
    return {"kind": "set-structure-evaluation",
            "claims": CLAIMS,
            "evidence_roles": EVIDENCE_ROLES,
            "rounds_per_game": rounds_per_game,
            "games": merged,
            "tally_sequence": [_tally(band) for band in
                               sorted(labels.get("band_frames") or [], key=lambda b: b["at"])],
            "tally_events": events,
            "pairing_changes": pairing_changes(labels, spans),
            "groupings": groupings,
            "groupings_enumerated": len(groupings),
            "groupings_exhaustive_over_the_space": len(groupings) == 2 ** max(0, len(merged) - 1),
            "surviving": [g["boundaries"] for g in surviving],
            "supported": [g["boundaries"] for g in surviving if g["status"] == "supported"],
            "open": [g["boundaries"] for g in surviving if g["status"] == "open"],
            "contradicted": [{"boundaries": g["boundaries"],
                              "kept_contradicted": g["temporal"]["contradicted_by"],
                              "denied_but_supported": g["temporal"][
                                  "denied_but_supported_by_the_sequence"],
                              "overlay_tally": g["overlay_tally"]["kept"]}
                             for g in groupings if g["status"] == "contradicted"],
            "clashes": _unique_clashes(groupings),
            "unresolved": len(surviving) > 1,
            "one_answer_published": False,
            "partial_runs": [{"games": run["games"], "edge": run["edge"], "because": run["because"]}
                             for grouping in groupings for run in grouping["partial_runs"]],
            "format_supplied": target is not None,
            "format_role": EVIDENCE_ROLES["bracket_format"],
            "labels": _label_counts(labels),
            "transcription": transcription or {
                "state": "not_supplied",
                "note": "reading pixels and belonging to a set are graded apart, and supplying one does "
                        "not settle the other"},
            "temporal": {"per_boundary": {"{}".format(index): value for index, value in
                                          sorted(boundaries.items())},
                         "note": "the recording's own sequence -- clock rises and falls, transitions, "
                                 "dwell, station state. The overlay column and a menu are never in here."}}


def _unique_clashes(groupings):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    seen, out = set(), []
    for grouping in groupings:
        for clash in grouping["clashes"]:
            key = (clash["boundary"], clash.get("between"), clash.get("temporal"),
                   clash.get("proposed"), clash["overlay_tally"])
            if key not in seen:
                seen.add(key)
                out.append(clash)
    return out


def _label_counts(labels):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    frames = labels.get("frames") or []
    return {"inspected": bool(labels.get("inspected")),
            "frozen_at": labels.get("frozen_at"),
            "graded_visible_count_frames": sum(1 for f in frames if f.get("graded")),
            "known_absent_frames": sum(1 for f in frames if f.get("row_drawn") is False),
            "unknown_truth_frames": sum(1 for f in frames if f.get("row_drawn") is None),
            "band_frames": len(labels.get("band_frames") or []),
            "labels_are_runtime_input": labels.get("labels_are_runtime_input")}
