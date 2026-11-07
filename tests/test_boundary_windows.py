"""Offline checks for automatic decision-window selection. No inference and no outcome labels.

Expectations come from the frame manifest itself, never from a hardcoded frame duration, so a test cannot
agree with the code by sharing its arithmetic.
"""
import copy
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_pips as bp
from tekken_vod_helper import boundary_windows as bw

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
SAVED_RUNS = [ROOT / "build/work/bb9/pip-crops-v2", ROOT / "build/work/bb9/game1-validate-v1/run",
              ROOT / "build/work/bb9/game2-validate-v1/run", ROOT / "build/work/bb9/game2-validate-v1/run2"]


@pytest.fixture(scope="module")
def frames():
    return bw.FrameIndex.load(str(MANIFEST))


@pytest.fixture(scope="module")
def observations():
    return bg.build_observations(json.loads(TIMELINE.read_text(encoding="utf-8")))


def fact(timestamp, clock=None, state="gameplay", round_end=None, characters=("A", "B")):
    return {"timestamp": timestamp, "screen_state": state, "round_clock": clock, "round_end": round_end,
            "result_transition": False, "win_dots": [None, None], "characters": characters,
            "hud_present": state == "gameplay", "round_call": None}


#: A round that ended on a K.O. with the next round already opening. `end_at` is replaced by tests that
#: want the anchor to land on a specific cached frame, so the expectation is the manifest's, not a literal.
KO_WINDOW = {"sequence": 0, "basis": ["ko"], "end_at": 1131.129941, "end_basis": "ko",
             "event_at": 1131.129941, "last_clock_at": 1126.124941, "last_clock": 36,
             "clock_after": 60, "opening_at": 1136.13494, "opened_at": 1096.0, "gaps": [],
             "quarantined_clocks": []}


# ---------- 4. the manifest is the timestamp source of truth ----------

def test_timestamps_come_from_the_manifest_not_from_frame_arithmetic(frames):
    # A hardcoded 1.000986 s step gives 994.980084 for this frame; the manifest says otherwise.
    frame = frames.nearest(994.99)
    assert frame["id"] == 995
    assert frame["timestamp"] == pytest.approx(994.993948, abs=1e-5)
    assert frames.nearest(55.05)["timestamp"] == pytest.approx(55.054997, abs=1e-5)
    assert frame["path"].endswith("frame-0995.jpg")


def test_a_missing_manifest_raises_instead_of_reconstructing_times(tmp_path):
    with pytest.raises(ValueError, match="manifest missing"):
        bw.FrameIndex.load(str(tmp_path / "absent.json"))
    with pytest.raises(ValueError, match="no frames"):
        bw.FrameIndex([])


def test_at_or_after_refuses_to_return_an_earlier_frame(frames):
    assert frames.at_or_after(6928.5) is None, "end of cache is a gap, not a wrap to the last frame"


# ---------- 1. round intros are not game openings ----------

def test_internal_round_intros_are_not_corroborated_game_openings():
    rows = [fact(50.0, state="stage_intro"), fact(53.0, 60), fact(95.0, 21, round_end="ko"),
            fact(98.0, state="stage_intro"), fact(101.0, 60)]
    hypotheses = bw.game_opening_hypotheses(rows)
    assert len(hypotheses) == 2
    assert all(h["corroborated"] is False for h in hypotheses), "same pairing means a round intro"
    lower = fact(50.0, state="stage_intro", characters=("alisa", "asuka"))
    upper = fact(98.0, state="stage_intro", characters=("ALISA", "ASUKA"))
    mixed = bw.game_opening_hypotheses([fact(10.0, 60, state="stage_intro", characters=("Alisa","Asuka")),
                                        fact(20.0, 60), fact(98.0, state="stage_intro",
                                                             characters=("ALISA", "ASUKA"))])
    assert all(h["corroborated"] is False for h in mixed), "case differences are not new pairings"
    del lower, upper


def test_mid_game_intros_in_validated_game_one_are_not_openings(observations):
    inside = [h for h in bw.game_opening_hypotheses(observations) if 97.0 <= h["timestamp"] <= 130.0]
    assert inside, "validated game 1 really does contain mid-game intro screens"
    assert all(h["corroborated"] is False for h in inside), \
        "case-only and blank-name differences must not claim a new game"
    assert all("round intro" in h["corroboration"] for h in inside)


def test_an_interval_must_be_a_real_range(observations, frames):
    with pytest.raises(ValueError, match="not after"):
        bw.plan_interval(observations, {}, frames, start_at=1011.0, end_at=1011.0, max_new_requests=4)
    with pytest.raises(ValueError, match="no saved observations"):
        bw.plan_interval(observations, {}, frames, start_at=7000.0, end_at=7010.0, max_new_requests=4)


# ---------- 2. a post-event probe can never precede its K.O. ----------

def test_a_probe_after_the_flash_never_precedes_the_ko_evidence(frames):
    """Regression: this window anchored its 'after impact flash' probe 5 s before the K.O. itself."""
    window = {"sequence": 0, "basis": ["ko"], "end_at": 1131.129941, "end_basis": "ko",
              "event_at": 1131.129941, "last_clock_at": 1126.124941, "last_clock": 36,
              "clock_after": 60, "opening_at": 1136.13494, "opened_at": 1096.0, "gaps": [],
              "quarantined_clocks": []}
    for candidate in bw.decision_frames(window, frames):
        assert candidate["timestamp"] >= window["end_at"] - 0.6, \
            "a frame before the K.O. is not 'after impact flash': %r" % candidate
    assert window["end_basis"] == "ko"


def test_refinement_asks_the_cached_frames_the_coarse_probe_stepped_over(frames):
    """The probe that settles a round is sometimes the frame the coarse grid never asked for.

    Expectations come from the index rather than a constant: whatever the cache's own cadence puts strictly
    between the coarse offsets is eligible, so a coarser cache refines less. The point is that the frame
    exists between +0 s and +2 s and the first pass cannot see it.
    """
    base = frames.nearest(1000.0)
    window = dict(KO_WINDOW, end_at=base["timestamp"])
    coarse = bw.decision_frames(window, frames)
    assert [entry["role"] for entry in coarse] == list(bw.END_PROBE_ROLES)
    refined = bw.decision_frames(window, frames, refine=True)
    assert refined[:len(coarse)] == coarse, "refinement appends; it never widens the coarse triple"
    interiors = refined[len(coarse):]
    assert interiors, "a cache finer than the probe grid must offer its own frames"
    assert all(0.0 < entry["offset_seconds"] < max(bw.END_PROBE_OFFSETS) for entry in interiors)
    assert {entry["id"] for entry in coarse}.isdisjoint(entry["id"] for entry in interiors)
    assert all(entry["role"].startswith("freeze_interior") for entry in interiors)
    assert len(interiors) <= len(bw.END_PROBE_OFFSETS) - 1, "the second pass has to stay bounded"


def test_refinement_stops_where_the_row_stopped_being_readable(frames):
    """The bound is the caller's own answer, not a duration: past the lost row there is nothing to ask."""
    base = frames.nearest(1000.0)
    window = dict(KO_WINDOW, end_at=base["timestamp"])
    coarse = bw.decision_frames(window, frames)
    tight = bw.decision_frames(window, frames, refine=True, interior_end=base["timestamp"] + 1.6)
    assert [round(entry["offset_seconds"], 2) for entry in tight[len(coarse):]] == [1.0]
    wide = bw.decision_frames(window, frames, refine=True)
    assert len(wide) > len(tight), "the unbounded scan is exactly what the lost-row bound stops"


def test_the_end_anchor_prefers_the_event_over_the_last_readable_clock(frames):
    windows = bw.round_ending_windows([fact(100.0, 30), fact(103.0, 27), fact(105.0, round_end="ko"),
                                       fact(110.0, 60)], frames)
    assert windows[0]["end_at"] == 105.0 and windows[0]["end_basis"] == "ko"
    assert windows[0]["evidence_bounds"]["last_clock_time"] == 103.0, "the clock bound is kept, not lost"


def test_a_window_bounded_only_by_the_clock_says_so(frames):
    windows = bw.round_ending_windows([fact(100.0, 30), fact(103.0, 27), fact(110.0, 60)], frames)
    assert windows[0]["end_basis"] == "last_readable_clock"
    assert any("last readable clock" in g for g in windows[0]["gaps"])
    bounds = windows[0]["evidence_bounds"]
    assert bounds["uncertain_from"] <= bounds["uncertain_to"]


def test_a_contradictory_clock_is_quarantined_not_used_as_a_boundary(frames):
    windows = bw.round_ending_windows([fact(100.0, 40), fact(101.0, 5), fact(102.0, 4), fact(103.0, 3)],
                                      frames)
    assert len(windows) == 1, "an impossible 40 -> 5 fall must not open a round"
    assert windows[0]["quarantined_clocks"] and any("quarantined" in g for g in windows[0]["gaps"])


def test_a_rise_within_reading_tolerance_is_not_a_new_round(frames):
    rows = [fact(10.0, 40), fact(15.0, 40 + bw.CLOCK_RISE_TOLERANCE), fact(20.0, 35)]
    assert len(bw.round_ending_windows(rows, frames)) == 1


# ---------- 3. unknown truth is ungraded, never absent ----------

def test_an_unlabelled_frame_cannot_be_scored_as_correctly_absent():
    observations = [{"timestamp": 1.0, "label": s, "drawn": False, "lit": None} for s in bp.LABELS]
    unlabelled = [{"timestamp": 1.0, "frame": "f", "row_drawn": None, "pips": {"LEFT": None,
                                                                              "RIGHT": None}}]
    score = bp.score_readings(observations, unlabelled)
    assert score["frames"] == 0 and score["frames_correct"] == 0
    assert score["ungraded_frames"] == 1 and score["accuracy_is_reportable"] is False
    labelled = [{"timestamp": 1.0, "frame": "f", "row_drawn": False, "pips": {"LEFT": None,
                                                                             "RIGHT": None}}]
    graded = bp.score_readings(observations, labelled)
    assert graded["frames"] == 1 and graded["frames_correct"] == 1
    assert "correctly_absent" in graded["error_kinds"]


def test_build_records_keeps_row_drawn_unknown_without_coercing_it(tmp_path):
    fixture = json.loads((ROOT / "build/work/bb9/pip-crops-v1/annotations.json").read_text())
    entry = copy.deepcopy(fixture["frames"][0])
    entry["row_drawn"] = None
    assert bp.build_records([entry], tmp_path)[0]["row_drawn"] is None


# ---------- reuse, budget, honest reporting ----------

def test_saved_readings_are_reused_by_frame_identity_and_never_resent(observations, frames):
    saved = bw.load_saved_readings([str(r) for r in SAVED_RUNS])
    assert len(saved) == 33, "eight fixture, twelve game 1, thirteen game 2"
    plan = bw.plan_interval(observations, saved, frames, start_at=50.0, end_at=260.0,
                            max_new_requests=99)
    assert plan["reused_readings"], "game 1 windows must land on frames already read"
    assert not ({r["frame"] for r in plan["requested"]} & set(saved)), "a read frame was queued again"


def test_selection_over_both_validated_games_needs_no_outcome_labels(observations, frames):
    for start, end in ((50.0, 260.0), (780.0, 1010.0)):
        plan = bw.plan_interval(observations, {}, frames, start_at=start, end_at=end,
                                max_new_requests=99)
        assert len(plan["windows"]) >= 3, (start, end)
        assert all(w["end_at"] is not None or w["gaps"] for w in plan["windows"])
        assert plan["grading"]["graded"] is False and plan["grading"]["accuracy"] is None


def test_the_budget_stops_and_reports_instead_of_batching(observations, frames):
    full = bw.plan_interval(observations, {}, frames, start_at=1006.0, end_at=1206.0,
                            max_new_requests=99)
    tight = bw.plan_interval(observations, {}, frames, start_at=1006.0, end_at=1206.0,
                             max_new_requests=4)
    assert tight["requests_planned"] == 4 and tight["images_total"] == 8
    assert tight["requests_deferred"] == full["requests_planned"] - 4
    assert tight["budget_fits"] is False
    assert [r["id"] for r in tight["requested"]] == [r["id"] for r in full["requested"][:4]]


def test_no_window_is_dropped_and_no_frame_is_requested_twice(observations, frames):
    plan = bw.plan_interval(observations, {}, frames, start_at=1006.0, end_at=1206.0,
                            max_new_requests=99)
    ids = [r["id"] for r in plan["requested"] + plan["deferred_for_budget"] + plan["reused_readings"]]
    assert len(ids) == len(set(ids)), "one frame, one request"
    seen = {c["window"] for c in plan["requested"] + plan["reused_readings"] +
            plan["deferred_for_budget"]}
    assert {w["sequence"] for w in plan["windows"]} - seen <= {plan["windows"][-1]["sequence"]}


def test_hud_disappearance_alone_never_concludes_a_game(frames):
    rows = [fact(10.0, 40), fact(20.0, None, state="post_match_menu")]
    plan = bw.plan_interval(rows, {}, frames, start_at=0.0, end_at=30.0, max_new_requests=9)
    assert all(w["end_basis"] != "hud_absent" for w in plan["windows"])
    assert any("winner" in n for n in plan["cannot_be_settled_by_this_selection"])


def test_the_left_winner_path_is_calibration_only_and_not_blind_coverage():
    """Neither validated game was won leftward, so left completion is exercised by construction only."""
    def row(timestamp, clock, dots):
        return {"timestamp": timestamp, "path": "build/work/bb9/detect-1440p-v1/frames/frame-0001.jpg",
                "state": "gameplay", "evidence": "timer %d" % clock, "round_clock": clock,
                "win_dots": list(dots), "pip_row_drawn": True}

    rows = [row(10, 60, [0, 0]), row(40, 30, [0, 0]), row(60, 60, [1, 0]), row(100, 25, [1, 0]),
            row(120, 60, [2, 0]), row(160, 20, [2, 0]), row(180, 60, [3, 0])]
    game = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)["games"][0]
    assert game["winner"] == "p1" and game["winner_basis"] == "win_dots"
    assert game["counts_toward_set"] is True and not game["contested"]
    undecisive = bg.track_games(bg.build_observations(rows[:-1]), rounds_per_game=3,
                               games_per_set=3)["games"][0]
    assert undecisive["winner"] is None, "three rounds seen is not three pips won"


# ---------- the public execution path, with ungraded selection placeholders ----------

def test_the_public_probe_reports_ungraded_rather_than_a_verdict(tmp_path, monkeypatch, capsys):
    """A frame the evaluator never labelled must not be able to produce accuracy or success."""
    import test_pip_crop_execution as tpe
    fixture = json.loads((ROOT / "build/work/bb9/pip-crops-v1/annotations.json").read_text())
    ungraded = [dict(copy.deepcopy(f), row_drawn=None, pips={"LEFT": None, "RIGHT": None})
                for f in fixture["frames"][:3]]
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"kind": "pip-row-annotations", "version": "window-plan-test-1",
                                       "frames": ungraded}))
    budget = ["--budget-file", str(tmp_path / "attempts.json"), "--budget-cap", "35",
              "--budget-minutes", "60"]      # --execute now refuses an uncounted attempt
    module, _ = tpe.run_script(monkeypatch,
                              ["--annotations", str(annotations), "--output-dir", str(tmp_path / "run"),
                               "--arms", "crops", "--max-requests", "3", "--model", "test-model",
                               "--endpoint", "http://endpoint.test"] + budget + ["--prepare"],
             capsys=capsys)
    class Absent:
        def __call__(self, body):
            asked = [(i, l) for i, l in __import__("re").findall(r"item (\d+) label=(\w+)",
                                                                 json.dumps(body))]
            payload = [{"item": int(i), "label": l, "drawn": False, "lit": None, "positions": [],
                        "confidence": 0.9} for i, l in asked]
            return {"choices": [{"message": {"content": json.dumps(payload)}}],
                    "usage": {"prompt_tokens": 600, "completion_tokens": 400}}

    _, out = tpe.run_script(monkeypatch,
                            ["--annotations", str(annotations), "--output-dir", str(tmp_path / "run"),
                             "--arms", "crops", "--max-requests", "3", "--model", "test-model",
                             "--endpoint", "http://endpoint.test"] + budget + ["--execute"],
                            transport=Absent(), capsys=capsys)
    report = json.loads(out)["arms"]["crops"]
    score = report["score"]
    assert report["groups_answered"] == 3, "the frames were answered; only the truth is missing"
    assert score["frames"] == 0 and score["frames_correct"] == 0
    assert score["ungraded_frames"] == 3 and score["accuracy_is_reportable"] is False
    assert score.get("error_kinds", {}).get("correctly_absent") is None, \
        "an abstention on an unlabelled frame earned absence credit"
    assert "verdict" not in json.loads(out)
