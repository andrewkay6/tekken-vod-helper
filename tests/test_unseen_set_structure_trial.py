"""The unseen trial's labels, pushed through the real executor and tracker with only the socket replaced.

This is step 2 of the trial's own gated sequence, and it costs nothing: the frames are on disk, the crops
are rendered by the code that would render them for a send, and the transport is a stand-in that answers
from the frozen evaluator labels. What it proves is that the pipeline can carry the inspection -- the
scorer reads the freeze, the merge places the counts, the tracker names the games -- and it proves nothing
at all about the model, because a run graded against the answers it was fed scores clean by construction.
That asymmetry is why the rehearsal runs before an allowance is asked for rather than after.

The labels stay out of the question set: the executor reads the runtime annotation file, which is still
null in every frame, so the trial's 42 requests are not the 42 answers.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from _consecutive_trial_harness import TrialRehearsal, load        # noqa: E402
import validate_game_one as vg                                     # noqa: E402
from tekken_vod_helper import boundary_games as bg                 # noqa: E402
from tekken_vod_helper import boundary_pips as bp                  # noqa: E402
from tekken_vod_helper import boundary_structure as bstruct        # noqa: E402

TRIAL = ROOT / "build/work/bb9/unseen-set-structure-v1"
LABELS = load(TRIAL / "labels-eval-frozen.json")
GAMES = [{"start": 2402.399874, "end": 2467.464870},
         {"start": 2493.490869, "end": 2603.600863},
         {"start": 2771.768854, "end": 2880.877849},
         {"start": 2891.888848, "end": 3050.046840}]


@pytest.fixture
def trial():
    return TrialRehearsal(TRIAL)


@pytest.fixture
def rehearsed(tmp_path, monkeypatch, trial):
    """Prepare, then execute the whole 42-frame plan from the frozen labels. No socket, no spend."""
    run = trial.prepared(tmp_path, monkeypatch)
    result = trial.execute(run, monkeypatch, trial.transport())
    return run, result


def test_the_labels_are_a_freeze_and_the_runtime_question_set_is_still_blind():
    """A label that reached the executor would make the trial a transcription of itself."""
    assert LABELS["inspected"] is True and LABELS["frozen_at"] == "2026-11-02"
    runtime = load(TRIAL / "annotations-window.json")
    assert len(runtime["frames"]) == 42
    assert all(frame["pips"] == {"LEFT": None, "RIGHT": None} and frame["row_drawn"] is None
               for frame in runtime["frames"])
    assert LABELS["labels_are_runtime_input"] is False


def test_the_freeze_scores_on_three_denominators_and_none_of_them_is_a_zero(rehearsed):
    """An absent row and an unreadable row are different claims, and the scorer keeps them apart.

    22 frames carry a countable row on both sides, 13 are known-absent rows graded as absence claims, and
    7 are drawn-but-not-closeable and sit outside every denominator. A freeze that wrote a zero for an
    occluded slot, or filed an unreadable row as absent, would move these numbers -- which is the point of
    pinning them here rather than quoting an accuracy.
    """
    run, result = rehearsed
    score = bp.score_readings(result["observations"], LABELS["frames"])
    assert score["scored_visible_count_frames"] == 22
    assert score["scored_visible_count_correct"] == 22, "the labels are the answers here, by construction"
    assert score["scored_known_absent_frames"] == 13 and score["scored_known_absent_correct"] == 13
    assert score["unknown_truth_frames"] == 7
    assert score["sides_exact"] == 44 and score["sides_total"] == 70
    assert score["error_kinds"] == {"exact": 44, "correctly_absent": 26}
    assert sum(1 for frame in LABELS["frames"] if frame["row_drawn"] is None
               and 0 in (frame["pips"]["LEFT"], frame["pips"]["RIGHT"])) == 0, \
        "an unreadable row was written as a zero"


def test_the_probe_scores_nothing_from_the_runtime_file_it_is_given(rehearsed):
    """The executor's own score stays empty, because the file it may read carries no labels.

    This is the leak test the freeze depends on: if the probe could grade itself here, a passing trial
    would not tell an answer from an echo.
    """
    run, _ = rehearsed
    probe = json.loads((run / "score-crops.json").read_text(encoding="utf-8"))
    assert probe["frames"] == 0 and probe["ungraded_frames"] == 42
    assert probe["accuracy_is_reportable"] is False


def test_the_rehearsal_recovers_the_games_the_inspections_describe(rehearsed):
    """The merge places the counts and the tracker names the games -- including the one it cannot close.

    The trial's first game ends on a row nobody could read, so its winner stays null: a tracker that
    produced four winners from these labels would have invented one out of bucket arithmetic, which is the
    exact failure this project has been corrected for twice. And no set is concluded, because nothing in
    this interval is allowed to conclude one.
    """
    run, result = rehearsed
    rows, _ = vg.merged_rows(vg.crop_observations([Path(run)]), trial_interval())
    tracked = bg.track_games(bg.build_observations(rows), rounds_per_game=3)
    assert len(tracked["games"]) == 4
    assert [game.get("winner") for game in tracked["games"]] == [None, "p2", "p1", "p1"]
    assert [game["status"] for game in tracked["games"]] == ["complete"] * 4
    assert all(candidate["set_winner"] is None for candidate in tracked["sets"]), \
        "a rehearsal concluded a set out of rows that name no set"
    assert tracked["accounting"]["rounds"] == 14


def trial_interval():
    plan = load(TRIAL / "selected-frames.json")
    return plan["interval"]["start_at"], plan["interval"]["end_at"]


def test_the_structure_evaluation_enumerates_the_space_and_refuses_to_choose(rehearsed):
    """The trial's set-level claim is a set of surviving groupings, and the report says so out loud."""
    recovered = load(ROOT / "build/work/bb9/public-recovery-integration-v1"
                            "/cli-2400-3200-noformat.json")
    boundaries = bstruct.boundaries_from_recovery(recovered, GAMES)
    report = bstruct.evaluate(LABELS, GAMES, boundaries=boundaries)
    assert report["groupings_enumerated"] == 8
    assert report["groupings_exhaustive_over_the_space"] is True
    assert report["one_answer_published"] is False and report["unresolved"] is True
    assert [game["winner"] for game in report["games"]] == [None, "RIGHT", "LEFT", "LEFT"]
    assert report["format_supplied"] is False
    assert all(grouping["score"] is None and grouping["winner"] is None
               for grouping in report["groupings"])
    # The column is supporting evidence: it disagrees with the cut the sequence drew, and that is reported
    # as a clash instead of being resolved by precedence or by dropping one of the two readings.
    assert [clash["overlay_tally"] for clash in report["clashes"]] == ["climb"]
    assert report["evidence_roles"]["overlay_games_tally"] == "supporting_only"


def test_adding_a_bracket_format_later_moves_no_verdict(rehearsed):
    """A target supplied afterwards may name a winner; it cannot retroactively group the games."""
    recovered = load(ROOT / "build/work/bb9/public-recovery-integration-v1"
                            "/cli-2400-3200-noformat.json")
    boundaries = bstruct.boundaries_from_recovery(recovered, GAMES)
    plain = bstruct.evaluate(LABELS, GAMES, boundaries=boundaries)
    formatted = bstruct.evaluate(LABELS, GAMES, boundaries=boundaries, target=3)
    assert [g["boundaries"] for g in plain["groupings"]] == \
        [g["boundaries"] for g in formatted["groupings"]]
    assert [g["status"] for g in plain["groupings"]] == \
        [g["status"] for g in formatted["groupings"]]
    one_set = [g for g in formatted["groupings"] if g["boundaries"] == []][0]
    assert one_set["score"] == {"LEFT": 2, "RIGHT": 1}, "three readable games: two left, one right"
    assert one_set["winner"] is None, "nothing reached first to three"
    assert "floor" in one_set["missing_evidence"].lower(), "an unreadable game must not vanish from the tally"


def test_the_executed_run_left_one_winner_on_the_floor_and_only_presence_errors():
    """What the 61-attempt run returned, pinned so the finding cannot be quietly re-graded.

    Transcription was clean everywhere the pixels could be read -- 22 of 22 countable frames, 44 of 44
    sides -- and its only two errors are one class: a pip count asserted on a frame where no row is drawn.
    Those two are not cosmetic. The invented LEFT 1 at 3052.048840 s, two seconds after the real LEFT 3,
    is what the tracker read as the row falling, withheld the fourth game's winner, and left a winner the
    inspected labels name unreported. This is the case any merge-side fix has to satisfy, and the answers
    it is graded against are now evidence rather than something to re-ask.
    """
    evaluation = load(TRIAL / "trial-evaluation.json")
    transcription = evaluation["claim_transcription"]
    assert transcription["scored_visible_count_frames"] == 22
    assert transcription["scored_visible_count_correct"] == 22
    assert transcription["sides_exact"] == 44 and transcription["sides_total"] == 70
    assert transcription["unknown_truth_frames"] == 7 and transcription["frames_correct"] == 33
    assert transcription["error_kinds"] == {"exact": 44, "correctly_absent": 24,
                                            "pip_invented_on_undrawn_row": 2}
    assert [round(frame["timestamp"], 4) for frame in transcription["misses"]] == \
        [3052.0488, 3054.0508]
    assert all("pip_invented_on_undrawn_row" in " ".join(frame["errors"])
               for frame in transcription["misses"])
    assert evaluation["budget"]["by_state"] == {"answered": 61, "failed": 0, "in_flight": 0,
                                                "uncertain": 0}, "a spend was written off"
    assert evaluation["budget"]["uncertain_attempts"] == 0

    winner = evaluation["claim_winner"]
    assert winner["from_the_frozen_labels"] == [None, "RIGHT", "LEFT", "LEFT"]
    assert winner["from_the_run"] == [None, "p2", "p1", None]
    contested = winner["games_contested_in_the_run"]
    assert [g["start"] for g in contested] == [2891.888848]
    assert contested[0]["dot_regressions"]["p1"] == {"at": 3052.048840, "from": 3, "to": 1}, \
        "the withheld winner no longer traces to the invented reading, so the finding has changed"

    identity = evaluation["claim_score_identity"]
    assert identity["bands_answered"] == 8
    assert identity["names_correct"] == 8 and identity["tally_correct"] == 8


def test_the_structure_report_after_the_run_still_publishes_no_single_answer():
    """The run ended with every grouping alive, and the pipeline's own cut kept its rank: proposal."""
    evaluation = load(TRIAL / "trial-evaluation.json")
    grouping = evaluation["claim_grouping"]
    assert grouping["groupings_enumerated"] == 8
    assert grouping["groupings_exhaustive_over_the_space"] is True
    assert grouping["contradicted"] == [] and grouping["unresolved"] is True
    assert grouping["one_answer_published"] is False and len(grouping["surviving"]) == 8
    assert [(clash["boundary"], clash["between"], clash["overlay_tally"], clash["decides"])
            for clash in grouping["clashes"]] == [(1, "proposal_and_overlay_tally", "climb", False)]
    assert grouping["evidence_roles"]["overlay_games_tally"] == "supporting_only"
    assert grouping["evidence_roles"]["station_cleared_or_controller_prompt"] == "supporting_only"
    assert any(run["edge"] == "leading" for run in grouping["partial_runs"]), \
        "the run that opens off the edge of the stretch stopped being partial"
    assert evaluation["claim_winner"]["sets_concluded"] == 0
