"""The consequence instrument must not call an absent count a failed drop, nor miss a vanished game.

Three failures are pinned here, all of them reporting failures rather than recording failures:

1. A frame whose answer carries no pip count has nothing to withhold. Counting those trials as "failed
   drops" tells a reader the instrument misfired when the honest answer is "not applicable".
2. Comparing only the keys that survive a deletion makes a game that *disappears* invisible -- the largest
   possible consequence of a removal, reported as no change at all. The diff now walks the union.
3. Blanking a count is not deleting an answer. A presence-only answer survives a count-withholding trial
   intact -- entry, ``pip_row_drawn`` and confidence and all -- so "removing it changed nothing" proves
   nothing unless the removal was a real one.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from tekken_vod_helper import boundary_games as bg  # noqa: E402

SCRIPT = ROOT / "scripts" / "audit_reading_consequence.py"
spec = importlib.util.spec_from_file_location("audit_reading_consequence", str(SCRIPT))
consequence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consequence)

FRAME = "build/work/bb9/detect-1440p-v1/frames/frame-0100.jpg"


def _readings(counts, drawn=(True, True)):
    return FRAME, {FRAME: {"frame": FRAME, "timestamp": 100.0, "win_dots": list(counts),
                           "win_dots_drawn": list(drawn), "win_dots_confidence": [0.9, 0.9],
                           "pip_row_drawn": any(drawn)}}


def _fingerprint(games):
    """A fingerprint built by hand: the shape of the producer's own, minus the tracker."""
    return {"rounds": 1, "totals": {"games": len(games), "winners": sum(1 for g in games.values()
                                                                        if g["winner"])},
            "queue": {}, "games": games, "sets": {}}


def _game(winner="p2", support=1, status="complete", clip=9.0, uncertainty="null"):
    return {"winner": winner, "basis": "win_dots" if winner else "not_observed", "support": support,
            "status": status, "clip_end_at": clip, "end_uncertainty": uncertainty,
            "conclusion_event_at": 8.0}


def test_a_frame_with_a_count_is_removed_by_identity_and_reported_as_landed():
    frame, readings = _readings([2, 1])
    rows = [{"timestamp": 100.0, "frame": frame, "win_dots": [None, None]}]
    edited, edited_rows, removed = consequence.withhold_counts(readings, rows,
                                                               bg._frame_identity(frame), ("p1",))
    assert removed == 1
    assert edited[frame]["win_dots"] == [None, 1]


def test_a_presence_only_answer_is_not_applicable_rather_than_a_failed_experiment():
    """A row that reports no pips has no count to delete; blaming the instrument would be a lie."""
    frame, readings = _readings([None, None], drawn=(False, False))
    rows = [{"timestamp": 100.0, "frame": frame, "win_dots": [None, None]}]
    _edited, _rows, removed = consequence.withhold_counts(readings, rows,
                                                          bg._frame_identity(frame), ("p1", "p2"))
    assert removed == 0


def test_withholding_a_count_leaves_the_answer_in_place_so_ablation_cannot_use_it():
    """The reason the two instruments exist: a blanked number is still an observation."""
    frame, readings = _readings([None, None], drawn=(False, False))
    rows = [{"timestamp": 100.0, "frame": frame, "win_dots": [None, None]}]
    identity = bg._frame_identity(frame)
    after_counts, _rows, counts_removed = consequence.withhold_counts(readings, rows, identity,
                                                                      ("p1", "p2"))
    assert counts_removed == 0
    assert identity == bg._frame_identity(next(iter(after_counts)))
    survivor = next(iter(after_counts.values()))
    assert survivor["pip_row_drawn"] is False and survivor["win_dots_confidence"] == [0.9, 0.9]

    after_full, _rows, proof = consequence.remove_observations(readings, rows, identity)
    assert proof["crop_entries_removed"] == 1
    assert proof["identity_still_in_crop_entries"] is False
    assert after_full == {}, "the observation has to be gone, not merely emptied"


def test_full_removal_clears_pip_fields_but_keeps_the_clock_facts_it_travels_with():
    frame, readings = _readings([2, 0])
    rows = [{"timestamp": 100.0, "frame": frame, "win_dots": [2, 0], "clock": 59,
             "screen_state": "gameplay", "characters": ["NINA", "REINA"]}]
    _edited, edited_rows, proof = consequence.remove_observations(readings, rows,
                                                                  bg._frame_identity(frame))
    row = edited_rows[0]
    assert "win_dots" not in row and "win_dots_drawn" not in row and "pip_row_drawn" not in row
    assert row["clock"] == 59 and row["screen_state"] == "gameplay"
    assert row["characters"] == ["NINA", "REINA"]
    assert proof["crop_entries_removed"] == 1


def test_a_deleted_creditied_game_is_reported_not_swallowed_by_iterating_survivors():
    """The saved counterexample: a credited game vanishes and the old diff returned ({}, {})."""
    before = _fingerprint({"5.000": _game(), "9.000": _game(winner="p1")})
    after = _fingerprint({"9.000": _game(winner="p1")})
    delta = consequence.diff(before, after)
    assert delta["games_removed"] == [{"start": "5.000", "winner": "p2"}]
    assert delta["winner_claims_lost"] == [{"start": "5.000", "winner": "p2"}]
    assert delta["games_added"] == []


def test_a_new_game_from_a_regrouping_is_visible_too():
    before = _fingerprint({"5.000": _game()})
    after = _fingerprint({"5.000": _game(), "7.000": _game(winner="p1")})
    delta = consequence.diff(before, after)
    assert [g["start"] for g in delta["games_added"]] == ["7.000"]


def test_support_count_and_winner_identity_are_separate_columns():
    one = lambda count: _fingerprint({"5.000": _game(support=count)})  # noqa: E731
    delta = consequence.diff(one(1), one(2))
    assert delta["winner_identity_changes"] == {}
    assert delta["support_changes"] == {"5.000": ["p2/1", "p2/2"]}
    flipped = consequence.diff(one(2), _fingerprint({"5.000": _game(winner="p1", support=2)}))
    assert flipped["winner_identity_changes"] == {"5.000": ["p2", "p1"]}
    assert flipped["support_changes"] == {}


def test_presence_only_changes_are_visible_when_they_move_a_published_fact():
    """The negative control the corpus cannot supply: a presence answer that gates a clip end.

    Across all 57 presence-only crop answers on BB9, genuine removal moves nothing (measured in
    build/work/bb9/presence-only-ablation-control-2026-11-06.json), because the row's own not-drawn facts
    arrive on state rows. So detectability is shown here instead: if a presence claim ever does gate a
    published fact, the report says so, and an inert verdict means the instrument looked.
    """
    before = _fingerprint({"5.000": _game(clip=9.0, uncertainty='{"bounded_by": "observed_end_event"}')})
    after = _fingerprint({"5.000": _game(clip=8.0, uncertainty='{"bounded_by": "pip_row_not_drawn"}')})
    delta = consequence.diff(before, after)
    assert delta["clip_end_changes"] == {"5.000": [9.0, 8.0]}
    assert delta["uncertainty_changes"] == {"5.000": ['{"bounded_by": "observed_end_event"}',
                                                     '{"bounded_by": "pip_row_not_drawn"}']}
    assert consequence.changed(delta) == ["clip_end_changes", "uncertainty_changes"]


def test_queue_and_round_changes_are_reported_as_changes():
    before = _fingerprint({"5.000": _game()})
    after = _fingerprint({"5.000": _game()})
    before["queue"] = {"win_dots_never_read": 40}
    after["queue"] = {"win_dots_never_read": 39}
    before["rounds"], after["rounds"] = 122, 123
    delta = consequence.diff(before, after)
    assert delta["queue_changes"] == {"win_dots_never_read": [40, 39]}
    assert delta["rounds_changed"] == [122, 123]


def test_a_lost_winner_and_a_flipped_winner_are_not_the_same_column():
    """credited -> none is a lost claim; p1 -> p2 is a flip. One aggregate must not cover both."""
    lost = consequence.diff(_fingerprint({"5.000": _game(winner="p1")}),
                            _fingerprint({"5.000": _game(winner=None)}))
    assert lost["winner_identity_changes"] == {}
    assert lost["winner_claims_lost"] == [{"start": "5.000", "winner": "p1"}]

    flipped = consequence.diff(_fingerprint({"5.000": _game(winner="p1")}),
                               _fingerprint({"5.000": _game(winner="p2")}))
    assert flipped["winner_identity_changes"] == {"5.000": ["p1", "p2"]}
    assert flipped["winner_claims_lost"] == []

    gained = consequence.diff(_fingerprint({"5.000": _game(winner=None)}),
                              _fingerprint({"5.000": _game(winner="p2")}))
    assert [g["start"] for g in gained["winner_claims_gained"]] == ["5.000"]
    assert gained["winner_identity_changes"] == {}


def test_the_producer_keeps_two_instruments_and_one_name_each():
    """Names are the interface here: one function per question, and no aliasing between them."""
    for instrument, question in (("withhold_counts", "did that digit matter"),
                                 ("remove_observations", "did that answer matter")):
        assert callable(getattr(consequence, instrument, None)), "%s missing (%s)" % (
            instrument, question)
    assert not hasattr(consequence, "strip"), "one name for two different operations is the original bug"
    assert callable(consequence.diff) and callable(consequence.changed)
    delta = consequence.diff(_fingerprint({"5.000": _game()}), _fingerprint({}))
    assert delta["games_removed"] and delta["winner_claims_lost"], "diff must walk the union of keys"


def test_the_normalized_report_distinguishes_the_two_instruments_and_keeps_raw_evidence():
    saved = ROOT / "build/work/bb9/winner-corroboration-v2-consequence-normalized-v2.json"
    if not saved.is_file():
        pytest.skip("produced by scripts/audit_reading_consequence.py")
    report = json.loads(saved.read_text(encoding="utf-8"))
    totals = report["totals"]
    assert totals["count_drops_landed"] == 18 and totals["count_drops_not_applicable"] == 6
    assert totals["count_drops_failed"] == 0 and totals["experiments_changing_support"] == 6
    assert totals["experiments_changing_winner_identity"] == 0
    ablation = report["ablation_totals"]
    assert ablation["frames"] == 8
    assert ablation["observations_really_removed"] == 8, "every ablation must prove removal"
    assert ablation["ablations_changing_any_published_claim"] == 3
    assert {entry["frame"] for entry in report["full_answer_ablation"]
            if entry["published_claims_changed"]} == {"frame-0237.jpg", "frame-3372.jpg",
                                                      "frame-3543.jpg"}
    raw = json.loads((ROOT / "build/work/bb9/winner-corroboration-v2-consequence-2026-11-06.json")
                     .read_text(encoding="utf-8"))
    assert raw["totals"]["drops_that_failed"] == 6, "raw evidence is preserved, not tidied"
