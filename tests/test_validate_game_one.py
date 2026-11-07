"""The game-one validation harness has to fail loudly, or its later success means nothing.

These tests run with no endpoint and no new requests: they check that the harness reuses the eight
verified crop readings, treats an unread frame as missing evidence rather than as a zero or an absent
row, and reports acceptance as unmet until a named winning side exists.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_game_one.py"

spec = importlib.util.spec_from_file_location("validate_game_one", str(SCRIPT))
vg = importlib.util.module_from_spec(spec)
sys.modules["validate_game_one"] = vg
spec.loader.exec_module(vg)


def test_it_reads_only_the_crops_arm_and_skips_failed_groups(tmp_path, monkeypatch):
    good = {"arm": "crops", "errors": [], "frame": "x/frame-0100.jpg", "timestamp": 99.1,
            "observations": [{"label": "LEFT", "drawn": True, "lit": 1},
                              {"label": "RIGHT", "drawn": True, "lit": 0}]}
    other_arm = dict(good, arm="full", frame="x/frame-0101.jpg")
    broken = dict(good, errors=["no answer"], frame="x/frame-0102.jpg")
    half = dict(good, frame="x/frame-0103.jpg", observations=[good["observations"][0]])
    store = tmp_path / "crops" / "groups"
    store.mkdir(parents=True)
    # Names follow the writer's real "<arm>-<fingerprint>.json" pattern, so the glob is exercised too.
    for name, payload in (("crops-aaaa", good), ("crops-bbbb", other_arm),
                          ("crops-cccc", broken), ("crops-dddd", half)):
        (store / "{}.json".format(name)).write_text(json.dumps(payload))
    monkeypatch.setattr(vg, "RUN_DIRS", [tmp_path])
    found = vg.crop_observations()
    assert list(found) == ["x/frame-0100.jpg"], "only a complete clean crops reading is evidence"
    assert found["x/frame-0100.jpg"]["win_dots"] == [1, 0]


def test_an_absent_row_stays_none_and_never_becomes_a_scoreline(tmp_path, monkeypatch):
    absent = {"arm": "crops", "errors": [], "frame": "x/frame-0130.jpg", "timestamp": 129.13,
              "observations": [{"label": "LEFT", "drawn": False, "lit": None},
                                {"label": "RIGHT", "drawn": False, "lit": None}]}
    store = tmp_path / "crops" / "groups"
    store.mkdir(parents=True)
    (store / "crops-aaaa.json").write_text(json.dumps(absent))
    monkeypatch.setattr(vg, "RUN_DIRS", [tmp_path])
    reading = vg.crop_observations()["x/frame-0130.jpg"]
    assert reading["win_dots"] == [None, None]
    assert reading["pip_row_drawn"] is False


def test_the_pilot_file_is_never_written_to():
    """The merge overrides unreliable dot values in memory. The preserved run artefacts stay put."""
    before = {p: p.stat().st_mtime for p in (vg.PILOT_FACTS, vg.FIXTURE)}
    observations = vg.crop_observations()
    vg.merged_rows(observations)
    assert {p: p.stat().st_mtime for p in before} == before
    rows, _ = vg.merged_rows(observations)
    for row in rows:
        assert row.get("path"), "merge must give every row one frame key to be joined by"
    assert all(Path(r["path"]).name.startswith("frame-") for r in rows if r.get("path"))


def test_readings_join_by_frame_file_not_by_timestamp(tmp_path, monkeypatch):
    """The pilot's timestamps are float-rounded; a join on them would silently drop rows."""
    real = vg.crop_observations([ROOT / "build/work/bb9/pip-crops-v2"])
    assert real, "the eight verified readings must be present for this to mean anything"
    rows, joined = vg.merged_rows(real)
    assert joined["added"] == 0, "all eight fixture frames exist in the pilot timeline already"
    assert joined["unjoinable_pilot_rows"] == 0
    times = [round(r["timestamp"], 2) for r in rows]
    assert len(times) == len(set(times)), "two rows at one instant would read as the dots moving"
    by_name = {Path(r["path"]).name: r for r in rows if r.get("path")}
    for frame, reading in real.items():
        assert by_name[Path(frame).name]["win_dots"] == reading["win_dots"]
        assert "crop_readings" in by_name[Path(frame).name]["fact_source"]


def test_a_crop_reading_outside_the_window_cannot_join_the_replay(tmp_path):
    """A narrow window asked a narrow question; the corpus must not answer a wider one.

    ``merged_rows`` appends a saved reading whose frame has no row in the timeline, so feeding it every
    crop run in the repo dropped the accepted game 1's 55.05 s frames into game 2's 736-1080 s replay:
    a second game appeared inside it, and because observation ids are positions in the sorted list,
    every id after the insertion changed. The trial harness already filters its own readings for
    exactly this reason; this is the same guard where forgetting it is possible.
    """
    from tekken_vod_helper import boundary_games as bg

    real = vg.crop_observations([ROOT / "build/work/bb9/pip-crops-v2"])
    stamps = sorted(float(r["timestamp"]) for r in real.values())
    assert len(stamps) >= 2, "the fixture crop run is missing"
    early, late = stamps[0], stamps[-1]

    rows, joined = vg.merged_rows(real, (early - 500.0, early - 1.0))
    assert joined["added"] == 0 and joined["skipped_outside_window"] == len(real), \
        "every reading here is outside the window: %s" % joined
    assert rows, "the window has to fall inside the pilot timeline for this to mean anything"
    assert not [r for r in rows if any(abs(float(r["timestamp"]) - s) < 0.005 for s in stamps)]

    # And a window one second wide sees one second of footage, whatever the corpus holds.
    narrow, _ = vg.merged_rows(real, (late - 0.5, late + 0.5))
    wide, wide_joined = vg.merged_rows(real, (0.0, 10000.0))
    assert all(late - 0.5 <= float(r["timestamp"]) <= late + 0.5 for r in narrow)
    assert wide_joined["skipped_outside_window"] == 0
    assert len(wide) > len(narrow), "the wide window stopped seeing what the corpus holds"
    # The leak this closes: rows from two different games made the tracker find two games in one game.
    assert len(bg.track_games(bg.build_observations(narrow))["games"]) <= 1


def test_the_format_is_configured_not_inferred():
    data = run_validation("/tmp/gate-format", "--new-annotations",
                          "build/work/bb9/game1-validate-v1/annotations-new.json")
    assert data["format"] == {"rounds_per_game": 3, "games_per_set": 3,
                              "source": "configured, not inferred"}



def test_progression_is_taken_from_the_trackers_own_round_membership():
    """If the harness re-segmented rounds itself, a wrong split could still print a tidy sequence."""
    result = {"games": [{"round_ids": [0, 1], "end": 200.0, "start": 50.0, "winner": None}],
              "rounds": [{"win_dots_seen": {"p1": [0, 1], "p2": [0]}},
                          {"win_dots_seen": {"p1": [1], "p2": [0, 1]}}]}
    verdict = vg.check_acceptance(result, {"pip_progression": [[0, 0], [1, 0], [1, 1], [1, 2], [1, 3]],
                                            "game_winner_side": "RIGHT"}, {}, [])
    assert verdict["checks"]["progression"]["observed_per_round_end"] == [[1, 0], [1, 1]]
    assert verdict["checks"]["progression"]["pass"] is False, "two rounds cannot make four"


def _offline_oracle_control():
    """Known-correct measurements for testing logic only; never saved as model replies."""
    from tekken_vod_helper import boundary_games
    annotation = json.loads(vg.NEW.read_text())
    observed = vg.crop_observations()
    for frame in annotation["frames"]:
        observed[frame["frame"]] = {
            "timestamp": frame["timestamp"],
            "win_dots": [frame["pips"][side] for side in vg.SIDES],
            "pip_row_drawn": frame["row_drawn"], "from_group": "OFFLINE_TEST_ORACLE"}
    rows, _ = vg.merged_rows(observed)
    result = boundary_games.track_games(boundary_games.build_observations(rows))
    return annotation, observed, rows, result


def test_correct_observations_recover_one_game_and_pass_every_gate():
    annotation, observed, rows, result = _offline_oracle_control()
    verdict = vg.check_acceptance(result, annotation["game_one_expectation"], observed,
                                  annotation["frames"])
    assert verdict["accepted"], verdict["checks"]
    game = result["games"][0]
    assert game["rounds"] == 4 and game["winner"] == "p2"
    assert len(set(game["round_ids"])) == 4
    assert abs(game["start"] - 53.05) < .01
    assert abs(game["end"] - 235.23) < .01
    assert all("superseded_full_frame_win_dots" in row or "win_dots" not in row
               or "crop_readings" in row.get("fact_source", "") for row in rows)


@pytest.mark.parametrize("field", ["start", "end"])
def test_wrong_boundary_is_rejected_even_when_round_count_matches(field):
    annotation, observed, _, result = _offline_oracle_control()
    result["games"][0][field] += 20
    verdict = vg.check_acceptance(result, annotation["game_one_expectation"], observed,
                                  annotation["frames"])
    assert not verdict["checks"]["boundaries"]["pass"]


def test_wrong_target_game_winner_is_rejected():
    annotation, observed, _, result = _offline_oracle_control()
    result["games"][0]["winner"] = "p1"
    verdict = vg.check_acceptance(result, annotation["game_one_expectation"], observed,
                                  annotation["frames"])
    assert not verdict["checks"]["winning_side"]["pass"]


def test_unknown_or_invented_row_cannot_pass_source_accuracy():
    annotation, observed, _, result = _offline_oracle_control()
    frame = next(f for f in annotation["frames"] if not f["row_drawn"])
    observed[frame["frame"]]["pip_row_drawn"] = None
    verdict = vg.check_acceptance(result, annotation["game_one_expectation"], observed,
                                  annotation["frames"])
    assert not verdict["accepted"]
    assert not verdict["checks"]["new_readings_match_source_annotations"]["pass"]


# ---------- a second, independent game ----------

GAME2 = ["--new-annotations", "build/work/bb9/game2-validate-v1/annotations-new.json",
         "--fixture-annotations", "build/work/bb9/game2-validate-v1/annotations-new.json",
         "--run-dirs", "build/work/bb9/game2-validate-v1/run,build/work/bb9/game2-validate-v1/run2",
         "--window", "736,1080"]


def run_validation(out_dir, *extra):
    """Invoke the harness as written and read back the report it wrote."""
    report = subprocess.run([str(ROOT / ".venv/bin/python"), str(SCRIPT), "--out", str(out_dir),
                             *extra], capture_output=True, text=True, cwd=str(ROOT))
    assert report.returncode == 0, report.stderr[-900:]
    return json.loads(Path(json.loads(report.stdout)["report"]).read_text())


def test_game_two_is_a_harder_case_than_game_one():
    """Different match, different characters, five rounds and a decider -- checked, not assumed."""
    ann = json.loads((ROOT / "build/work/bb9/game2-validate-v1/annotations-new.json").read_text())
    assert ann["game_expectation"]["expected_rounds"] == 5
    assert [r["won_by"] for r in ann["game_expectation"]["rounds"]] == [
        "LEFT", "RIGHT", "RIGHT", "LEFT", "RIGHT"]
    drawn = [f for f in ann["frames"] if f["row_drawn"]]
    assert max(f["pips"]["RIGHT"] for f in drawn) == 3, "only a third pip can name the winner"
    # Independence: no frame is shared with the crop fixture or with game 1's validation frames.
    used = {f["frame"] for f in ann["frames"]}
    for other in (ROOT / "build/work/bb9/pip-crops-v1/annotations.json",
                  ROOT / "build/work/bb9/game1-validate-v1/annotations-new.json"):
        assert not used & {f["frame"] for f in json.loads(other.read_text())["frames"]}, other


def test_game_two_validates_end_to_end_from_real_crop_readings():
    data = run_validation("/tmp/gate-game2", *GAME2)
    assert data["accepted"] is True, json.dumps(data["checks"], indent=1)
    assert all(c["pass"] for c in data["checks"].values())
    assert data["checks"]["boundaries"]["observed"]["game_1_rounds"] == 5
    winner = data["checks"]["winning_side"]["observed"]
    assert winner["winner"] == "p2" and winner["winner_basis"] == "win_dots"
    assert winner["counts_toward_set"] is True and winner["contested"] is False
    assert data["checks"]["winning_side"]["required"]["winner"] == "RIGHT", "p2 is how the tracker says RIGHT"


def test_game_two_cannot_be_accepted_without_its_decisive_pip(tmp_path):
    """No third pip observed means no winner: passing here would mean the gate reads round counts only."""
    store = tmp_path / "noDecider" / "crops" / "groups"
    store.mkdir(parents=True)
    moved = 0
    for run in ("run", "run2"):
        for f in (ROOT / "build/work/bb9/game2-validate-v1" / run / "crops" / "groups").glob("*.json"):
            if json.loads(f.read_text())["timestamp"] == 993.98:
                moved += 1
                continue          # the only frame where RIGHT's third pip is visible
            (store / f.name).write_text(f.read_text())
    assert moved == 1, "the decisive frame must exist exactly once"
    data = run_validation(tmp_path / "out",
                          "--new-annotations", "build/work/bb9/game2-validate-v1/annotations-new.json",
                          "--fixture-annotations", "build/work/bb9/game2-validate-v1/annotations-new.json",
                          "--run-dirs", str(tmp_path / "noDecider"), "--window", "736,1080")
    assert data["accepted"] is False
    assert data["checks"]["winning_side"]["pass"] is False, "a 3-2 game cannot be decided below three pips"
    assert data["checks"]["new_readings_match_source_annotations"]["mismatches"] == [993.98]
