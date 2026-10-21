"""Tests for boundary_scoring."""
import json
from tekken_vod_helper.boundary_scoring import score_matches


def test_perfect_match():
    r = score_matches([{"start": 0, "end": 10}], [{"start": 0, "end": 10}])
    assert r["true_positives"] == 1
    assert r["precision"] == 1.0
    assert r["recall"] == 1.0
    assert r["matches"][0]["start_error"] == 0.0
    assert r["matches"][0]["end_error"] == 0.0
    assert r["matches"][0]["iou"] == 1.0


def test_missed_and_extra():
    r = score_matches([{"start": 0, "end": 5}, {"start": 20, "end": 30}],
                      [{"start": 0, "end": 5}])
    assert r["true_positives"] == 1
    assert r["false_positives"] == 1
    assert r["false_negatives"] == 0
    assert r["precision"] == 0.5
    assert r["recall"] == 1.0
    assert r["unmatched_predictions"] == [1]


def test_duplicate_cannot_boost_recall():
    r = score_matches([{"start": 0, "end": 10}, {"start": 0, "end": 10}],
                      [{"start": 0, "end": 10}])
    assert r["true_positives"] == 1
    assert r["recall"] == 1.0
    assert r["precision"] == 0.5
    assert r["false_positives"] == 1


def test_signed_errors():
    r = score_matches([{"start": 2, "end": 12}], [{"start": 0, "end": 10}])
    assert r["matches"][0]["start_error"] == 2.0
    assert r["matches"][0]["end_error"] == 2.0
    r2 = score_matches([{"start": 0, "end": 8}], [{"start": 2, "end": 10}])
    assert r2["matches"][0]["start_error"] == -2.0
    assert r2["matches"][0]["end_error"] == -2.0


def test_invalid_nan():
    try:
        score_matches([{"start": float("nan"), "end": 10}], [{"start": 0, "end": 10}])
        assert False, "should raise ValueError"
    except ValueError:
        pass


def test_booleans_rejected():
    try:
        score_matches([{"start": True, "end": 10}], [{"start": 0, "end": 10}])
        assert False, "should raise ValueError"
    except ValueError:
        pass


def test_crossing_range_exclusion():
    r = score_matches([{"start": 5, "end": 10}],
                      [{"start": 0, "end": 12}, {"start": 5, "end": 10}],
                      start=0, end=10)
    assert len(r["excluded_references"]) == 1
    assert r["excluded_references"][0]["index"] == 0
    assert r["reference_count"] == 2
    assert r["true_positives"] == 1


def test_empty_inputs():
    r = score_matches([], [])
    assert r["precision"] is None
    assert r["recall"] is None
    assert r["start_mae"] is None
    assert r["end_mae"] is None
    r2 = score_matches([{"start": 0, "end": 5}], [])
    assert r2["precision"] == 0.0
    assert r2["recall"] is None
    r3 = score_matches([], [{"start": 0, "end": 5}])
    assert r3["precision"] is None
    assert r3["recall"] == 0.0


def test_non_greedy_assignment():
    # IoU(P0,T0)=7/13, IoU(P1,T0)=8/12; DP picks higher IoU pair
    r = score_matches([{"start": 0, "end": 10}, {"start": 5, "end": 15}],
                      [{"start": 3, "end": 13}])
    assert r["true_positives"] == 1
    assert r["matches"][0]["prediction_index"] == 1
    assert r["matches"][0]["reference_index"] == 0


def test_json_safe():
    r = score_matches([{"start": 0, "end": 10}], [{"start": 0, "end": 10}])
    json.dumps(r, allow_nan=False)


def test_unresolved_end_outside_range_raises():
    try:
        score_matches([{"start": 0, "end": 10}], [{"start": 5, "end": None}], start=0, end=100)
        assert False, "should raise ValueError"
    except ValueError:
        pass


def test_unresolved_end_inside_range_excluded():
    r = score_matches([{"start": 0, "end": 10}], [{"start": 200, "end": None}], start=0, end=100)
    assert r["true_positives"] == 0
    assert len(r["excluded_references"]) == 1
    assert r["excluded_references"][0]["reason"] == "starts after evaluated range (unobserved end)"


def test_zero_duration_history_prefix_scoring():
    r = score_matches([], [{"start": 7366, "end": None}], start=0, end=1800)
    assert r["true_positives"] == 0
    assert len(r["excluded_references"]) == 1
    assert r["excluded_references"][0]["index"] == 0
