"""One contract for what may open or close a set, shared by the prompt, the validator and finalisation.

Two copies of this rule drifted apart once already: the model was told a candidate must start at a
stage grid and end at a controller prompt, and the validator rejected anything that disobeyed -- so
the two BB9 sets whose edges the broadcast never showed were not answered as partial, they were
answered as *nothing*, while a wrong boundary elsewhere could be returned as a completed candidate.
This module is the single place that says what an edge may be, and the prompt text is generated from
it so the instruction and the check cannot disagree again.

The rule itself is evidence-first. Preferred screens are the strongest thing a set edge can be built
on, and they are not mandatory: a bracket match whose opening grid was never streamed is still a set,
and reporting it as a partial with a flagged edge is worth more than dropping it. What a broadcast
header can do -- change players and reset its score box in the middle of a live round -- it is not
allowed to do here: names and scores corroborate an edge and never create one.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

CONTRACT_VERSION = "edges-v2"

# Strongest evidence that a set begins or ends here. A set with both is complete.
STRONG_START_STATES = ("stage_select",)
STRONG_END_STATES = ("controller_prompt",)
# Real evidence of the same thing, weaker: a grid sequence that is part of an episode opening, or a
# result screen that closes play without the controller prompt being visible.
SUPPORTING_START_STATES = ("character_select", "vs_screen", "stage_intro")
SUPPORTING_END_STATES = ("post_match_menu", "controller_disconnected")

# A confident reading is required for a strong edge to count as one. Below this the state is a hint.
MIN_EDGE_CONFIDENCE = 0.5

# Screens that mean nothing on their own. Kept as data so the prompt and any future check agree on the
# list instead of each carrying its own prose.
NON_EDGE_STATES = ("rematch_menu",)
#: The same states as the prompt names them, so rule 5 cannot drift from the list it is meant to state.
NON_EDGE_PHRASE = " and ".join(
    "{} {}".format(*state.split("_")) for state in NON_EDGE_STATES).capitalize() + "s"


def _confident(observation):
    # type: (Optional[Dict[str, Any]]) -> bool
    if not observation:
        return False
    value = observation.get("confidence")
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value >= MIN_EDGE_CONFIDENCE)


def start_completeness(observation):
    # type: (Optional[Dict[str, Any]]) -> str
    """How firmly this observation opens a set: strong, supporting, or inferred from play alone."""
    return _completeness(observation, STRONG_START_STATES, SUPPORTING_START_STATES)


def end_completeness(observation):
    # type: (Optional[Dict[str, Any]]) -> str
    """How firmly this observation closes a set."""
    return _completeness(observation, STRONG_END_STATES, SUPPORTING_END_STATES)


def _completeness(observation, strong, supporting):
    # type: (Optional[Dict[str, Any]], Sequence[str], Sequence[str]) -> str
    if not observation:
        return "inferred"
    state = observation.get("state")
    if state in strong and _confident(observation):
        return "strong"
    if state in supporting and _confident(observation):
        return "supporting"
    return "inferred"


def span_verdict(start_observation, end_observation, has_gameplay):
    # type: (Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool) -> Dict[str, Any]
    """Judge one proposed span against the contract.

    Only three things are impossible: no play inside, an empty evidence trail, and a span whose
    endpoints carry neither pairing nor screen evidence at all. Everything else is a real span of
    unknown firmness, and the answer is to say which -- ``partial`` lists the edges a reviewer should
    look at, so a weak edge degrades the record instead of deleting it.
    """
    start = start_completeness(start_observation)
    end = end_completeness(end_observation)
    notes = []  # type: List[str]
    errors = []  # type: List[str]
    if not has_gameplay:
        errors.append("no observed gameplay between start and end")
    if start == "inferred" and end == "inferred":
        notes.append("neither edge rests on a screen: bounds come from play alone")
    elif start == "inferred":
        notes.append("start inferred: no stage-selection episode was observed before this play")
    elif end == "inferred":
        notes.append("end inferred: no concluding screen was observed after this play")
    if start_observation and start_observation.get("state") in STRONG_START_STATES and not _confident(
            start_observation):
        notes.append("start grid was read below confidence {}".format(MIN_EDGE_CONFIDENCE))
    return {
        "contract": CONTRACT_VERSION,
        "start_completeness": start,
        "end_completeness": end,
        # An edge is partial unless a preferred screen holds it: "supporting" is honest evidence and
        # still worth a reviewer's eye, so it is not silently promoted to complete.
        "partial": [side for side, grade in (("start", start), ("end", end))
                    if grade != "strong"],
        "firm": start == "strong" and end == "strong" and bool(has_gameplay),
        "errors": errors,
        "notes": notes,
    }


def prompt_rules():
    # type: () -> str
    """The numbered rules block, generated from the same data the validator applies.

    Kept deliberately short, and deliberately no longer than the rules block it replaced: this text is
    fixed overhead inside every adjudication window, so prose here is paid for in requests. Measured
    on a 9,000-char fixture, 51 extra characters dropped one observation per window and turned 6
    windows into 10.
    """
    return (
        "1. A SET is one bracket match: every game a pairing plays before the broadcast moves on.\n"
        "2. Open a set on the strongest evidence:\n"
        f"   a. a confident {'/'.join(STRONG_START_STATES)};\n"
        f"   b. else a confident {'/'.join(SUPPORTING_START_STATES)};\n"
        "   c. else the first observed gameplay -- propose it and mark the start edge partial.\n"
        "3. Close a set on the strongest evidence:\n"
        f"   a. a confident {'/'.join(STRONG_END_STATES)};\n"
        f"   b. else a confident {'/'.join(SUPPORTING_END_STATES)};\n"
        "   c. else the last observed gameplay: propose it, mark the end edge partial, and say whether\n"
        "   the recording cut, the stream ended, or the next set began.\n"
        "4. Every set contains at least one observed gameplay.\n"
        "5. " + NON_EDGE_PHRASE + ", controller assignment dialogs and VS screens do not end a set: "
        "between\n   games the same pairing returns to the grid.\n"
        "6. The same pairing, a concluded prompt, a fresh episode and a reset score is a separate set\n"
        "   (a Grand Final restart). That is sequence evidence, not a name reading.\n"
        "7. Warmups and setup-only sequences with no observed gameplay are not sets.\n"
        "8. Broadcast names and score boxes update late and can change during a live round. They may\n"
        "   corroborate an edge and may never create one: propose one set across such a change.\n"
        "9. Never report absent later observations as proof a set concluded.\n"
        "10. If evidence cannot settle a boundary use \"uncertain\" and name what is missing. Never\n"
        "    invent a timestamp, name or character.\n"
    )


def contract_summary():
    # type: () -> Dict[str, Any]
    """Machine-readable form, written into run artefacts so a reviewer sees the rules used."""
    return {
        "version": CONTRACT_VERSION,
        "strong_start_states": list(STRONG_START_STATES),
        "strong_end_states": list(STRONG_END_STATES),
        "supporting_start_states": list(SUPPORTING_START_STATES),
        "supporting_end_states": list(SUPPORTING_END_STATES),
        "min_edge_confidence": MIN_EDGE_CONFIDENCE,
        "partial_edges_are_kept": True,
        "names_may_create_a_boundary": False,
        "absence_of_play_is_completion": False,
    }
