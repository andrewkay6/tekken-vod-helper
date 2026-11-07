"""One boundary contract, and proof that the prompt, the validator and the artefacts all read it.

The failure this guards against is not a wrong threshold, it is two copies of a rule disagreeing: the
model was told a candidate must start at a stage grid and end at a controller prompt, the validator
rejected anything else, and the two BB9 sets whose edges were never broadcast were deleted rather than
published as partial. Any future edit has to move all three at once.
"""
import json

import pytest

from tekken_vod_helper import boundary_contract as bc
from tekken_vod_helper import boundary_reasoning as br
from tekken_vod_helper import boundary_workflow


def observation(state, confidence=0.9, **kw):
    row = {"timestamp": kw.get("timestamp", 0.0), "state": state, "confidence": confidence,
           "player1": "A", "player2": "B", "evidence": "x"}
    row.update(kw)
    return row


# ---------- the contract itself ----------

def test_a_preferred_screen_is_strong_a_supporting_one_is_not_and_play_alone_is_inferred():
    assert bc.start_completeness(observation("stage_select")) == "strong"
    assert bc.start_completeness(observation("character_select")) == "supporting"
    assert bc.start_completeness(observation("gameplay")) == "inferred"
    assert bc.end_completeness(observation("controller_prompt")) == "strong"
    assert bc.end_completeness(observation("post_match_menu")) == "supporting"
    assert bc.end_completeness(observation("controller_disconnected")) == "supporting"
    assert bc.end_completeness(observation("gameplay")) == "inferred"


def test_a_weakly_read_screen_is_not_an_edge():
    """Confidence is part of what makes an edge strong, so a 0.2 grid cannot close a set."""
    assert bc.start_completeness(observation("stage_select", confidence=0.2)) == "inferred"
    assert bc.end_completeness(observation("controller_prompt", confidence=0.49)) == "inferred"


def test_a_missing_observation_is_inferred_neither_strong_nor_an_error():
    assert bc.start_completeness(None) == "inferred"
    assert bc.end_completeness(None) == "inferred"


def test_supporting_evidence_is_partial_rather_than_silently_promoted():
    verdict = bc.span_verdict(observation("stage_select"), observation("post_match_menu"), True)
    assert verdict["partial"] == ["end"]
    assert verdict["firm"] is False


def test_play_without_any_screen_is_published_as_partial_not_dropped():
    verdict = bc.span_verdict(observation("gameplay"), observation("gameplay"), True)
    assert verdict["errors"] == [], "continuous play is a set somebody has to review, not a rejection"
    assert verdict["partial"] == ["start", "end"]
    assert verdict["notes"] and "play alone" in verdict["notes"][0]


def test_play_is_the_one_thing_a_set_cannot_lack():
    verdict = bc.span_verdict(observation("stage_select"), observation("controller_prompt"), False)
    assert verdict["errors"] == ["no observed gameplay between start and end"]


def test_names_and_scores_are_not_part_of_the_contract():
    """Whatever the header says, the contract has no field in which a pairing can open a set."""
    summary = bc.contract_summary()
    assert summary["names_may_create_a_boundary"] is False
    assert summary["absence_of_play_is_completion"] is False
    for group in ("STRONG_START_STATES", "STRONG_END_STATES", "SUPPORTING_START_STATES",
                  "SUPPORTING_END_STATES"):
        assert not [s for s in getattr(bc, group) if "player" in s or "score" in s or "name" in s]


# ---------- the three consumers agree ----------

def test_the_prompt_lists_the_states_the_validator_uses():
    rules = bc.prompt_rules()
    for state in list(bc.STRONG_START_STATES) + list(bc.STRONG_END_STATES) \
            + list(bc.SUPPORTING_START_STATES) + list(bc.SUPPORTING_END_STATES):
        assert state in rules, state
    assert "may never create one" in rules, "the prompt has to forbid a name-only boundary"


def test_the_adjudication_prompt_carries_the_shared_contract():
    for state in bc.STRONG_START_STATES + bc.STRONG_END_STATES + bc.SUPPORTING_START_STATES \
            + bc.SUPPORTING_END_STATES:
        assert state in br._SYSTEM_PROMPT
    # The retired hard gate must not survive anywhere in the instruction.
    assert "MUST: start at a confident stage_select" not in br._SYSTEM_PROMPT
    assert "end at a controller_prompt observation" not in br._SYSTEM_PROMPT


def test_the_contract_text_did_not_grow_the_fixed_prompt_overhead():
    """Rules text is paid for in requests: prompt overhead decides observations per window.

    Pinned at the size in force before the contract was extracted, because 51 extra characters were
    measured to turn a 6-window fixture into a 10-window one.
    """
    assert len(br._SYSTEM_PROMPT) <= 2101


def test_validator_annotates_the_span_it_accepts():
    obs = [observation("gameplay", timestamp=0.0), observation("gameplay", timestamp=5.0),
           observation("gameplay", timestamp=10.0)]
    built = br.build_observations(obs)
    by_id = {o["observation_id"]: o for o in built}
    order = {o["observation_id"]: i for i, o in enumerate(built)}
    proposal = {"start_id": "obs_0000", "end_id": "obs_0002", "evidence_ids": [],
                "disposition": "candidate", "reason": "continuous play"}
    errors = br._proposal_errors(proposal, 0, by_id, order, built)
    assert errors == []
    assert proposal["partial"] == ["start", "end"]
    assert proposal["start_completeness"] == "inferred"
    assert proposal["contract"] == bc.CONTRACT_VERSION


def test_finalisation_records_the_contract_it_applied(tmp_path):
    out = tmp_path / "run"
    result = boundary_workflow.finalize_run(
        out, [{"start": 0.0, "end": 10.0, "player1": "A", "player2": "B", "character1": "Reina",
               "character2": "Azucena", "round_name": "", "notes": "", "partial": [],
               "metadata_association": {}}],
        [], {"uri": "file:///x.mkv", "source": "yt:abc", "duration": 60.0}, 60.0, "Event",
        "deadbeefdeadbeef", "test-model", policy="# Rules\nshort policy\n",
        policy_source="docs/cutting-policy.md")
    review = json.loads((out / "review.json").read_text(encoding="utf-8"))
    assert review["edge_contract"] == bc.contract_summary()
    assert review["policy_chars"] == len("# Rules\nshort policy\n")
    assert review["policy_source"] == "docs/cutting-policy.md"
    assert result["status"] == "complete"


def test_finalisation_without_a_policy_object_still_states_the_contract(tmp_path):
    """An existing caller passing no policy must not lose the rules record."""
    out = tmp_path / "run2"
    boundary_workflow.finalize_run(out, [], [], {"uri": "u", "source": "s", "duration": 1.0},
                                   1.0, "Event", "h", "m")
    review = json.loads((out / "review.json").read_text(encoding="utf-8"))
    assert review["edge_contract"]["version"] == bc.CONTRACT_VERSION
    assert "policy_source" not in review, "not supplied, so not invented"
