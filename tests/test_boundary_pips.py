"""The crop probe measures framing, so everything that could quietly confound it is tested."""
import json

import pytest

from tekken_vod_helper import boundary_media as media
from tekken_vod_helper import boundary_pips as bp

FRAME = "build/work/bb9/detect-1440p-v1/frames/frame-0175.jpg"   # 174.17s, drawn 1-2


def record(path=FRAME, t=174.17, pips=None, drawn=True):
    return {"frame": path, "timestamp": t, "expected": pips or {"LEFT": 1, "RIGHT": 2},
            "row_drawn": drawn, "crops": {"LEFT": {"path": "L.png", "box": [1, 2, 3, 4],
                                                   "size": [9, 9]},
                                          "RIGHT": {"path": "R.png", "box": [1, 2, 3, 4],
                                                    "size": [9, 9]}},
            "source_sha256": "x"}


# ---------- geometry ----------

def test_each_crop_holds_all_three_slots_and_the_clock_edge():
    rects = bp.crop_rectangles()
    for side, span in (("LEFT", bp.LEFT_SPAN_X), ("RIGHT", bp.RIGHT_SPAN_X)):
        x0, _, x1, _ = rects[side]
        assert x0 <= span[0] and span[1] <= x1, side
        assert x0 < bp.CLOCK_X[1] and x1 > bp.CLOCK_X[0], side + " would carry no orientation cue"


def test_the_two_crops_do_not_overlap_each_other():
    left, right = bp.crop_rectangles()["LEFT"], bp.crop_rectangles()["RIGHT"]
    assert left[2] < right[0], "overlapping crops would let one side answer for the other"


def test_the_same_geometry_is_valid_at_master_resolution():
    """The guards inside ``crop_rectangles`` are written in the 640 reference; the boxes are not.

    Comparing a source-scale box against an unscaled reference constant refused every crop from the
    2560x1440 master -- "the LEFT group spans x 225-286 but its crop 856-1208 would clip it", where
    856-1208 is the box the master's own row needs -- and the same class of mistake sat in the
    clock-orientation check. A guard that passes at exactly one frame size is not validating geometry, it
    is hard-coding it.
    """
    reference = bp.crop_rectangles()
    source = bp.crop_rectangles((2560, 1440))
    measured = {"LEFT": (856, 44, 1208, 132), "RIGHT": (1376, 44, 1696, 132)}
    for side, (x0, y0, x1, y1) in source.items():
        # The 2026-10-30 source audit found this row by inspection; it is an independent statement of
        # the same box, so agreement is the check and not the assumption.
        assert (x0, y0, x1, y1) == measured[side], side
        for got, want in zip((x0, y0, x1, y1), reference[side]):
            assert abs(got - want * 4) <= 1, \
                "a coordinate is {} px from 4x the reference".format(got - want * 4)
        near, far = bp.LEFT_SPAN_X if side == "LEFT" else bp.RIGHT_SPAN_X
        assert x0 <= near * 4 and far * 4 <= x1, side + " would clip a circle"
        assert x0 < bp.CLOCK_X[1] * 4 and x1 > bp.CLOCK_X[0] * 4, \
            side + " loses its orientation cue at source size"
    assert source["LEFT"][2] < source["RIGHT"][0]
    # The one guard a smaller frame can trip is the row's height in pixels: proportions survive
    # downscaling, the circles do not. 40 px of height leaves the row band 3.8 px tall.
    with pytest.raises(ValueError, match="tall"):
        bp.crop_rectangles((640, 40))


def test_a_box_that_would_clip_a_circle_is_refused_not_silently_used():
    """A guard that cannot fire is worse than none: it advertises coverage it does not provide."""
    saved = bp.RIGHT_SPAN_X
    try:
        bp.RIGHT_SPAN_X = (300, 600)
        with pytest.raises(ValueError, match="clip"):
            bp.crop_rectangles()
    finally:
        bp.RIGHT_SPAN_X = saved


def test_crops_are_written_as_png_and_the_cache_is_left_alone(tmp_path):
    before = bp._sha(__import__("pathlib").Path(FRAME))
    result = bp.write_crops(FRAME, tmp_path)
    for entry in result["crops"].values():
        assert entry["path"].endswith(".png")
        assert "PNG" in open(entry["path"], "rb").read(4).decode("latin-1")
    assert bp._sha(__import__("pathlib").Path(FRAME)) == before, "the cache was modified"


def test_timestamps_come_from_the_annotations_and_never_from_the_filename(tmp_path):
    """frame-0056 is 55.05s, not 55.00s; guessing from the name mis-joins every expected value."""
    with pytest.raises(ValueError, match="timestamp"):
        bp.build_records([{"frame": FRAME}], tmp_path)


# ---------- the two arms differ in framing alone ----------

def test_arms_share_the_question_and_differ_only_in_what_is_shown():
    assert bp.question("crops") in bp.question("full") or \
        bp.PIP_QUESTION in bp.question("crops")
    assert bp.PIP_QUESTION in bp.question("crops") and bp.PIP_QUESTION in bp.question("full")
    assert bp.arm_identity("crops")["version"] != bp.arm_identity("full")["version"]


def test_the_control_arm_sends_the_frame_once_not_twice():
    """Duplicating the frame would add a second difference, and a gap would stop meaning 'crop'."""
    assert len(bp._images(record(), "crops")) == 2
    assert [label for label, _ in bp._images(record(), "full")] == ["FRAME"]


def test_a_crop_answer_cannot_be_reused_as_a_full_frame_answer():
    crops = bp.group_fingerprint([record()], "crops")
    full = bp.group_fingerprint([record()], "full")
    assert crops != full, "the same frame read differently must not share an identity"
    other = "build/work/bb9/detect-1440p-v1/frames/frame-0176.jpg"
    assert bp.group_fingerprint([record(path=other)], "crops") != crops, \
        "a different frame must not inherit this fingerprint"
    # The same image at a different timestamp is the same request: identity follows the pixels.
    assert bp.group_fingerprint([record(t=999.0)], "crops") == crops


# ---------- reply validation ----------

def good(item=1, label="LEFT", drawn=True, lit=1, positions=None, confidence=0.8):
    return {"item": item, "label": label, "drawn": drawn, "lit": lit,
            "positions": positions if positions is not None else ([1] if lit else []),
            "confidence": confidence}


def test_two_labels_are_required_per_frame_and_types_are_enforced():
    items = [record()]
    _, errors = bp.validate_reply([good()], items, "crops")
    assert any("expected item/label pairs" in e for e in errors)
    _, errors = bp.validate_reply([good(), good(label="RIGHT", lit=2, positions=[1, 2])],
                                  items, "crops")
    assert errors == []
    _, errors = bp.validate_reply([good(lit="false"), good(label="RIGHT", drawn="no")],
                                  items, "crops")
    assert len(errors) >= 2


def test_a_count_for_a_row_reported_absent_is_refused_and_recorded_as_null():
    """This is the exact failure the probe targets: the full-frame run scored rows that were not drawn."""
    items = [record()]
    out, errors = bp.validate_reply(
        [good(drawn=False, lit=1, positions=[1]),
         good(label="RIGHT", drawn=False, lit=0, positions=[])], items, "crops")
    assert errors and "not drawn" in errors[0]
    assert out[0]["lit"] is None, "a contradiction cannot survive validation as evidence"
    assert out[1]["lit"] == 0, "drawn=false with lit=0 is a coherent abstention, not a contradiction"


def test_lit_and_positions_must_agree():
    items = [record()]
    _, errors = bp.validate_reply([good(lit=1, positions=[2, 3]),
                                   good(label="RIGHT", lit=2, positions=[1, 2])], items, "crops")
    assert any("contradicts" in e for e in errors)


def test_nulls_are_accepted_because_absent_and_unreadable_are_real_results():
    items = [record()]
    out, errors = bp.validate_reply([good(drawn=None, lit=None, positions=None, confidence=None),
                                     good(label="RIGHT", drawn=False, lit=None, positions=None)],
                                    items, "crops")
    assert errors == []
    assert out[0]["lit"] is None and out[1]["drawn"] is False


# ---------- scoring ----------

def score(reported, drawn=True, expected=None):
    expected = expected or {"LEFT": 1, "RIGHT": 2}
    observations = [{"timestamp": 174.17, "label": "LEFT", "drawn": drawn, "lit": reported[0]},
                    {"timestamp": 174.17, "label": "RIGHT", "drawn": drawn, "lit": reported[1]}]
    annotations = [{"timestamp": 174.17, "frame": FRAME, "row_drawn": drawn, "pips": expected}]
    return bp.score_readings(observations, annotations)


def test_a_side_swap_is_named_rather_than_counted_as_two_wrong_sides():
    result = score([2, 1])
    assert "side_swap" in result["error_kinds"]
    assert result["per_frame"][0]["correct"] is False


def test_a_real_row_read_as_empty_is_a_false_zero_not_a_missed_pip():
    assert "false_zero" in score([0, 0])["error_kinds"]


def test_answering_a_score_for_an_undrawn_row_is_its_own_error_class():
    result = score([0, 1], drawn=False, expected={"LEFT": None, "RIGHT": None})
    assert "pip_invented_on_undrawn_row" in result["error_kinds"]
    clean = score([None, None], drawn=False, expected={"LEFT": None, "RIGHT": None})
    assert "correctly_absent" in clean["error_kinds"] and not clean["error_kinds"].get(
        "pip_invented_on_undrawn_row")


def test_abstention_is_counted_separately_from_being_wrong():
    """A frame reported unreadable is a lost reading; a frame reported confidently wrong is a cost."""
    unreadable = score([None, 2])
    assert unreadable["error_kinds"].get("unreadable") == 1
    assert unreadable["per_frame"][0]["correct"] is False
    wrong = score([3, 3])
    assert wrong["error_kinds"].get("extra_pips") == 2


def test_the_probe_does_not_claim_a_winner_from_one_decisive_frame():
    """compare_arms reports framing accuracy and explicitly refuses to name a game winner."""
    crops = {"observations": [{"timestamp": 174.17, "label": "LEFT", "drawn": True, "lit": 1},
                              {"timestamp": 174.17, "label": "RIGHT", "drawn": True, "lit": 3}]}
    full = {"observations": [{"timestamp": 174.17, "label": "LEFT", "drawn": True, "lit": 0},
                             {"timestamp": 174.17, "label": "RIGHT", "drawn": True, "lit": 0}]}
    annotations = [{"timestamp": 174.17, "frame": FRAME, "row_drawn": True,
                    "pips": {"LEFT": 1, "RIGHT": 3}}]
    comparison = bp.compare_arms(crops, full, annotations)
    assert comparison["crops"]["frames_correct"] == 1
    assert comparison["full"]["frames_correct"] == 0
    assert comparison["delta_frames_correct"] == 1
    assert any("winner" in line for line in comparison["does_not_establish"])


def test_cropping_is_recorded_as_a_hypothesis_whether_it_wins_or_not():
    """A probe that can only report success is a press release, and one that cannot is a dead end."""
    same = {"observations": [{"timestamp": 174.17, "label": "LEFT", "drawn": True, "lit": 0},
                             {"timestamp": 174.17, "label": "RIGHT", "drawn": True, "lit": 0}]}
    annotations = [{"timestamp": 174.17, "frame": FRAME, "row_drawn": True,
                    "pips": {"LEFT": 1, "RIGHT": 3}}]
    comparison = bp.compare_arms(same, dict(same), annotations)
    assert comparison["delta_frames_correct"] == 0
    assert "did not help" in comparison["verdict"]


# ---------- budget accounting ----------

def test_the_plan_reports_attempts_frames_and_images_as_three_numbers():
    eight = [record(path="build/work/bb9/detect-1440p-v1/frames/frame-%04d.jpg" % (200 + i),
                    t=100.0 + i) for i in range(8)]
    plan = bp.plan_requests(eight, "crops", max_requests=5)
    assert plan["requests_planned"] == 5, "one attempt per frame"
    assert plan["images_total"] == 10, "two labelled crops per frame"
    assert plan["frames_deferred"] == 3 and len(plan["deferred_frames"]) == 3


def test_transport_uncertain_stops_the_probe_without_retrying(tmp_path):
    class Dead:
        calls = 0

        def classify(self, frames):
            self.calls += 1
            raise media.TransportUncertain("timed out")

    names = ["frame-0175.jpg", "frame-0176.jpg", "frame-0177.jpg", "frame-0178.jpg"]
    records = [record(path="build/work/bb9/detect-1440p-v1/frames/" + n, t=float(i))
               for i, n in enumerate(names)]
    result = bp.persist_probe(records, tmp_path, "crops", client=Dead(), max_requests=4)
    assert result["requests_sent"] == 1, "one attempt, then stop"
    assert result["status"] == "stopped" and result["stopped_reason"] == "transport_uncertain"
    assert result["not_attempted"] == 3


def test_a_group_that_failed_validation_is_never_counted_as_answered(tmp_path):
    class Garbage:
        calls = 0

        def classify(self, frames):
            self.calls += 1
            return [{"item": 1, "label": "LEFT", "drawn": True, "lit": "yes"},
                    {"item": 1, "label": "RIGHT", "drawn": False, "lit": 2}]

    result = bp.persist_probe([record()], tmp_path, "crops", client=Garbage(), max_requests=2)
    assert result["groups_answered"] == 0 and result["groups_failed"] == 1
    assert result["observations"] == [], "a rejected reply must not reach the score"


def test_resuming_a_probe_reasks_nothing_and_scores_the_same(tmp_path):
    class Once:
        calls = 0

        def classify(self, frames):
            self.calls += 1
            return [{"item": 1, "label": "LEFT", "drawn": True, "lit": 1, "positions": [1],
                     "confidence": 0.9},
                    {"item": 1, "label": "RIGHT", "drawn": True, "lit": 2, "positions": [1, 2],
                     "confidence": 0.9}]

    second = "build/work/bb9/detect-1440p-v1/frames/frame-0176.jpg"
    records = [record(), record(path=second, t=175.17)]
    first = bp.persist_probe(records, tmp_path, "crops", client=Once(), max_requests=4)
    assert first["requests_sent"] == 2 and first["groups_answered"] == 2
    second = bp.persist_probe(records, tmp_path, "crops", client=Once(), max_requests=4)
    assert second["requests_sent"] == 0, "the same crops were already answered"
    assert second["groups_reused"] == 2
    annotations = [{"timestamp": r["timestamp"], "frame": r["frame"], "row_drawn": True,
                    "pips": {"LEFT": 1, "RIGHT": 2}} for r in records]
    assert bp.score_readings(second["observations"], annotations)["frames_correct"] == 2
