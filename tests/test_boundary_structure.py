"""Set-level evaluation: the grouping space is enumerated, and no supporting family decides it.

These are the properties the 2026-11-02 review asked for after the trial proposed two hypotheses about
four games. The proposal's defect was not that its favourites were wrong -- it was that naming two of the
eight possible groupings as *the* hypotheses would have made the trial unable to find out anything else,
and would have let a scoreboard column, which this recording shows moving 34-42 s after the round that
decided it, stand in for set truth.

So the tests here are about roles and about the shape of the answer: a grouping is decided on the
recording's own sequence, the overlay column and a menu prompt are reported beside that and never inside
it, alternatives that survive are all preserved, a run that touches the edge of the analysed stretch stays
partial, and no target is required for any of it.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_structure as bstruct        # noqa: E402

TRIAL = ROOT / "build/work/bb9/unseen-set-structure-v1"
LABELS = json.loads((TRIAL / "labels-eval-frozen.json").read_text(encoding="utf-8"))

# Four consecutive games, so three internal boundaries. The instants are the trial's real ones because a
# test about *this* evaluation must be checkable against the labels it will grade.
GAMES = [{"start": 2402.399874, "end": 2467.464870},
         {"start": 2493.490869, "end": 2603.600863},
         {"start": 2771.768854, "end": 2880.877849},
         {"start": 2891.888848, "end": 3050.046840}]


def labels(**overrides):
    labels = copy.deepcopy(LABELS)
    labels.update(overrides)
    return labels


def band_frames(pairs):
    return [{"at": at, "expected_games_tally": {"LEFT": left, "RIGHT": right},
             "expected_players": players} for at, left, right, players in pairs]


def labelled_frames(rows):
    """rows: (timestamp, LEFT, RIGHT, row_drawn). row_drawn None is the unknown-truth state."""
    return [{"timestamp": stamp, "frame": "frame-{:04d}.jpg".format(index),
             "row_drawn": drawn, "graded": bool(drawn),
             "pips": {"LEFT": left, "RIGHT": right},
             "basis": "test fixture"}
            for index, (stamp, left, right, drawn) in enumerate(rows)]


# ---------------------------------------------------------------- the space

def test_four_games_group_eight_ways_and_the_trial_named_two():
    """A hypothesis pair is not a hypothesis pair if it is also the whole answer space."""
    groupings = bstruct.candidate_groupings(4)
    assert len(groupings) == 8 == 2 ** 3
    assert [] in groupings, "one run of four games has to be expressible at all"
    assert [0, 1, 2] in groupings, "and so does four runs of one game"
    # The two the proposal offered: a cut between games 2 and 3, and a cut at every menu.
    proposed = [[1], [0, 1, 2]]
    assert all(one in groupings for one in proposed)
    unseen = [one for one in groupings if one not in proposed]
    assert len(unseen) == 6, "the trial would have been graded against a space with six holes in it"


def test_enumeration_is_capped_rather_than_exponential_in_front_of_a_whole_recording():
    assert len(bstruct.candidate_groupings(8)) == 128
    with pytest.raises(ValueError, match="investigate a shorter span"):
        bstruct.candidate_groupings(9)


def test_a_grouping_reports_the_runs_it_implies():
    assert bstruct.runs_for(4, []) == [[0, 1, 2, 3]]
    assert bstruct.runs_for(4, [1]) == [[0, 1], [2, 3]]
    assert bstruct.runs_for(4, [0, 1, 2]) == [[0], [1], [2], [3]]


# ---------------------------------------------------------------- roles

def test_a_scoreboard_reset_does_not_support_a_grouping_on_its_own():
    """The displayed column is supporting evidence: a reset is what a boundary looks like, not one."""
    sample = labels(band_frames=band_frames([
        (2410.0, 1, 0, {"LEFT": "a", "RIGHT": "b"}),
        (2460.0, 0, 0, {"LEFT": "a", "RIGHT": "b"})]),
        frames=labelled_frames([(2420.0, 3, 1, True), (2440.0, 0, 0, True)]))
    games = [{"start": 2400.0, "end": 2430.0}, {"start": 2440.0, "end": 2470.0}]
    report = bstruct.evaluate(sample, games)
    kept = [g for g in report["groupings"] if g["boundaries"] == [0]][0]
    assert kept["overlay_tally"]["kept"][0]["kind"] == "reset"
    assert kept["overlay_tally"]["can_decide"] is False
    assert kept["status"] == "open", "a display column moved a grouping from open to supported"
    assert report["evidence_roles"]["overlay_games_tally"] == "supporting_only"


def test_a_controller_prompt_supports_a_boundary_without_becoming_temporal_evidence():
    """The tracker's own vocabulary: a station cue is one family, and one family alone decides nothing."""
    games = [{"start": 2400.0, "end": 2430.0}, {"start": 2440.0, "end": 2470.0}]
    verdicts = {0: {"temporal": "undetermined", "prompts": "supported"}}
    grouped = bstruct.grade([0], bstruct.game_conclusions(labels(), [[g["start"], g["end"]]
                                                                     for g in games]), verdicts, [])
    assert grouped["prompts"]["supported"] == [0]
    assert grouped["status"] == "open", "a prompt alone closed a boundary the sequence never settled"
    assert grouped["temporal"]["per_boundary"] == {"0": "undetermined"}
    assert grouped["overlay_tally"]["role"] == "supporting_only"


def test_live_play_past_a_bound_is_what_contradicts_it_and_a_reset_cannot_outrank_that():
    games = [{"start": 2400.0, "end": 2430.0}, {"start": 2440.0, "end": 2470.0}]
    verdicts = {0: {"temporal": "contradicted"}}
    grouped = bstruct.grade([0], bstruct.game_conclusions(labels(), [[g["start"], g["end"]]
                                                                     for g in games]), verdicts, [])
    assert grouped["status"] == "contradicted"
    assert grouped["temporal"]["contradicted_by"] == [0]


def test_temporal_and_transcription_are_scored_apart_and_neither_buys_the_other():
    """Reading a pip row correctly says who won a round. It does not say what game they belong to."""
    report = bstruct.evaluate(labels(), GAMES, transcription={"state": "supplied",
                                                              "frames_correct": 22, "frames": 22})
    assert report["transcription"]["frames_correct"] == 22
    assert {g["status"] for g in report["groupings"]} <= {"open", "contradicted", "supported"}
    assert report["claims"] == ("transcription", "temporal", "grouping", "score", "winner", "footage")
    assert report["one_answer_published"] is False


# ---------------------------------------------------------------- alternatives and partials

def test_every_grouping_that_survives_is_kept_and_the_run_says_it_is_unresolved():
    report = bstruct.evaluate(labels(), GAMES)
    assert report["unresolved"] is True
    assert sorted(tuple(one) for one in report["surviving"]) == sorted(
        tuple(one) for one in bstruct.candidate_groupings(4)), \
        "nothing decided any of them, so nothing may be dropped"
    assert report["groupings_exhaustive_over_the_space"] is True


def test_a_boundary_the_sequence_supports_contradicts_the_grouping_that_denies_it():
    verdicts = {1: {"temporal": "supported"}}
    report = bstruct.evaluate(labels(), GAMES, boundaries=verdicts)
    by_boundaries = {tuple(one["boundaries"]): one for one in report["groupings"]}
    assert by_boundaries[(1,)]["status"] == "supported"
    assert by_boundaries[()]["status"] == "contradicted"
    assert by_boundaries[()]["temporal"]["denied_but_supported_by_the_sequence"] == [1]
    assert [one["boundaries"] for one in report["contradicted"]] and \
        () in [tuple(one["boundaries"]) for one in report["contradicted"]]


def test_a_run_beyond_the_edge_of_the_analysed_stretch_stays_partial_and_still_counts():
    games = [dict(GAMES[0], start_is_observed=False), GAMES[1],
             dict(GAMES[2], end_is_observed=False)]
    report = bstruct.evaluate(labels(), games)
    one_run = [g for g in report["groupings"] if g["boundaries"] == []][0]
    edges = sorted(run["edge"] for run in one_run["partial_runs"])
    assert edges == ["leading", "trailing"], "a partial run was dropped or silently closed"
    assert report["partial_runs"], "the report stopped naming the footage it cannot finish"
    assert one_run["status"] in ("open", "supported")


def test_no_target_is_required_and_no_target_publishes_no_score_or_winner():
    without = bstruct.evaluate(labels(), GAMES)
    assert without["format_supplied"] is False
    assert all(entry["score"] is None and entry["winner"] is None for entry in without["groupings"])
    assert all(entry["format_supplied"] is False for entry in without["groupings"])
    with_two = bstruct.evaluate(labels(), GAMES, target=2)
    assert with_two["format_role"] == "optional_never_a_veto"
    # A target adds columns and moves nothing else: the same boundaries, the same statuses.
    assert [g["boundaries"] for g in with_two["groupings"]] == \
        [g["boundaries"] for g in without["groupings"]]
    assert [g["status"] for g in with_two["groupings"]] == [g["status"] for g in without["groupings"]]


# ---------------------------------------------------------------- game winners from labels

def test_a_winner_needs_a_graded_row_that_reaches_the_target_and_says_so_when_it_does_not():
    sample = labels(frames=labelled_frames([(2420.0, 2, 1, True),      # target not reached
                                            (2435.0, None, None, None),   # drawn, not closeable
                                            (2450.0, 0, 3, True)]))      # RIGHT took the game
    games = [{"start": 2400.0, "end": 2440.0}, {"start": 2445.0, "end": 2470.0}]
    first, second = bstruct.game_conclusions(sample, [[g["start"], g["end"]] for g in games])
    assert first["state"] == "unreadable" and first["winner"] is None
    assert "not closeable" in first["why_unreadable"]
    assert second["winner"] == "RIGHT" and second["concluded_at"] == 2450.0
    assert second["last_readable_row"]["pips"] == [0, 3]


def test_a_row_past_the_span_end_is_allowed_by_the_sampling_and_says_it_landed_there():
    """The trial samples a round end and then +2 s and +4 s, so the deciding row can follow the bound.

    The grace is that sampling, stated: turn it off and the same labels no longer settle the game, which
    is the difference between a reading and a licence to look as far as needed.
    """
    sample = labels(frames=labelled_frames([(2438.0, 2, 0, True), (2442.0, 3, 0, True)]))
    conclusion = bstruct.game_conclusions(sample, [[2400.0, 2440.0]])[0]
    assert conclusion["winner"] == "LEFT" and conclusion["concluded_at"] == 2442.0
    assert conclusion["conclusion_row_lands_past_the_span_end"] is True
    tight = bstruct.game_conclusions(sample, [[2400.0, 2440.0]], conclusion_grace_seconds=0.0)[0]
    assert tight["state"] == "unreadable"


def test_an_unreadable_game_keeps_a_target_from_becoming_a_tally():
    """A score that omits a game nobody could read is a floor, and it has to say so."""
    sample = labels(frames=labelled_frames([(2420.0, 3, 0, True), (2455.0, 0, 2, True)]))
    games = [{"start": 2400.0, "end": 2430.0}, {"start": 2440.0, "end": 2470.0}]
    grouped = bstruct.grade([], bstruct.game_conclusions(sample, [[g["start"], g["end"]]
                                                                  for g in games]), {}, [], target=2)
    assert grouped["score"] == {"LEFT": 1, "RIGHT": 0}
    assert grouped["winner"] is None
    assert "floor" in grouped["missing_evidence"]


# ---------------------------------------------------------------- the trial's own labels

def test_the_trial_labels_are_inspected_frozen_and_separate_from_the_runtime():
    assert LABELS["inspected"] is True and LABELS["frozen_at"]
    assert LABELS["labels_are_runtime_input"] is False
    counts = LABELS["inspection"]["denominators"]
    assert counts["frames_total"] == 42 and counts["band_frames"] == 8
    assert counts["graded_visible_count_frames"] + counts["known_absent_frames"] + \
        counts["unknown_truth_frames"] == 42
    assert counts["unknown_truth_frames"] >= 1, "a freeze with nothing unreadable was not read honestly"
    runtime = json.loads((TRIAL / "annotations-window.json").read_text(encoding="utf-8"))
    assert all(frame["pips"] == {"LEFT": None, "RIGHT": None} and frame["row_drawn"] is None
               for frame in runtime["frames"]), "labels leaked into the file the executor reads"


def test_the_inspected_column_leaves_the_structure_open_and_clashes_with_the_trackers_cut():
    """The finding the review predicted, read off the pixels instead of argued.

    The column climbs twice and resets nowhere in [2400, 3200), so the cut the tracker drew between its two
    candidates -- a menu and a controller prompt, families this file keeps as supporting -- is not the
    boundary the display describes, and "four one-game matches" is not either.
    """
    recovered = json.loads((ROOT / "build/work/bb9/public-recovery-integration-v1"
                                  "/cli-2400-3200-noformat.json").read_text(encoding="utf-8"))
    proposals = bstruct.boundaries_from_recovery(recovered, GAMES)
    assert list(proposals) == [1], "the report draws one candidate boundary, after the second game"
    assert proposals[1]["proposed"] == "station_cleared"
    assert proposals[1]["prompts"] == "supported"
    assert proposals[1]["temporal"] == "undetermined", \
        "the adapter turned the detector's own edge into independent temporal truth"

    events = bstruct.tally_events(LABELS, [[g["start"], g["end"]] for g in GAMES])
    kinds = {event["spans_boundary_after_game"]: event["kind"] for event in events
             if event["spans_boundary_after_game"] is not None}
    assert kinds == {0: "climb", 1: "climb", 2: "unchanged"}, kinds
    assert "reset" not in kinds.values()

    report = bstruct.evaluate(LABELS, GAMES, boundaries=proposals)
    by_boundaries = {tuple(entry["boundaries"]): entry for entry in report["groupings"]}
    trackers = by_boundaries[(1,)]
    assert trackers["status"] == "open", "the pipeline's own cut graded itself as supported"
    assert trackers["overlay_tally"]["kept"] == [{"boundary": 1, "kind": "climb"}]
    assert trackers["overlay_tally"]["column_disagrees"] is True
    assert trackers["pipeline_proposal"]["agrees_with_the_pipeline"] is True
    clash = trackers["clashes"][0]
    assert clash["overlay_tally"] == "climb" and clash["between"] == "proposal_and_overlay_tally"
    assert clash["decides"] is False, "a clash was written where the code could also decide"
    four_matches = by_boundaries[(0, 1, 2)]
    assert [one["kind"] for one in four_matches["overlay_tally"]["kept"]] == \
        ["climb", "climb", "unchanged"], "four matches would need three resets"
    assert four_matches["overlay_tally"]["resets_observed"] == []
    assert by_boundaries[()]["status"] == "open", \
        "the grouping that omits the pipeline's cut was contradicted by the pipeline's own proposal"
    assert by_boundaries[()]["pipeline_proposal"]["denied"] == [1]
    assert report["contradicted"] == [], "nothing independent decided anything, so nothing is eliminated"
    assert report["unresolved"] is True and report["one_answer_published"] is False
    assert len(report["surviving"]) == 8


def test_a_lone_controller_prompt_between_two_games_leaves_both_groupings_possible():
    """Regression through the adapter itself, which is where the circularity lived.

    Two games, one candidate boundary, and the only thing on screen between them is a controller prompt:
    exactly what the detector reports as ``station_cleared``. Graded through `boundaries_from_recovery`,
    that must leave the one-set grouping and the two-set grouping equally possible. It used to eliminate
    the first, because the adapter handed the detector's edge back as a temporal verdict and `grade` then
    contradicted any grouping that did not draw the same cut -- the trial grading its own hypothesis
    against itself.
    """
    report = {"recovery": {"candidates": [
        {"start": 2400.0, "end": 2430.0, "games": 1,
         "edges": {"end": {"state": "station_cleared", "at": 2430.0,
                           "evidence_for": ["station_cleared"], "evidence_against": [],
                           "settled_by": {"kind": "cue", "at": 2432.0, "state": "controller_prompt"}}}},
        {"start": 2440.0, "end": 2470.0, "games": 1, "edges": {"end": {"state": "station_cleared"}}}]}}
    games = [{"start": 2400.0, "end": 2430.0}, {"start": 2440.0, "end": 2470.0}]
    proposals = bstruct.boundaries_from_recovery(report, games)
    assert proposals[0]["proposed"] == "station_cleared"
    assert proposals[0]["prompts"] == "supported"
    assert proposals[0]["temporal"] == "undetermined"

    graded = bstruct.grade([], bstruct.game_conclusions(labels(), [[g["start"], g["end"]]
                                                                   for g in games]), proposals, [])
    assert graded["status"] == "open", "the single-set grouping was eliminated by the detector's own cut"
    split = bstruct.grade([0], bstruct.game_conclusions(labels(), [[g["start"], g["end"]]
                                                                   for g in games]), proposals, [])
    assert split["status"] == "open"
    assert graded["boundaries"] != split["boundaries"] and graded["prompts"]["supported"] == []
    assert split["prompts"]["supported"] == [0], "the prompt stopped being recorded beside the verdict"
    both = bstruct.evaluate(labels(), games, boundaries=proposals)
    assert sorted(tuple(one) for one in both["surviving"]) == [(), (0,)]
    assert both["contradicted"] == [] and both["unresolved"] is True


def test_a_recovery_report_about_other_games_is_refused_rather_than_graded():
    recovered = json.loads((ROOT / "build/work/bb9/public-recovery-integration-v1"
                                  "/cli-2400-3200-noformat.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="not describing one"):
        bstruct.boundaries_from_recovery(recovered, GAMES[:3])


def test_the_labels_name_three_game_winners_and_leave_the_fourth_unreadable():
    """Two of the four games end on a row the frame cannot settle, and the report has to keep it that way."""
    conclusions = bstruct.game_conclusions(LABELS, [[g["start"], g["end"]] for g in GAMES])
    assert [one["winner"] for one in conclusions] == [None, "RIGHT", "LEFT", "LEFT"]
    assert conclusions[0]["state"] == "unreadable"
    assert "not closeable" in conclusions[0]["why_unreadable"]
    assert conclusions[3]["concluded_at"] == 3050.04684


def test_the_column_and_the_nameplates_are_read_from_the_same_eight_instants():
    """An evaluator reading that does not match the frozen digests is not the same observation."""
    bands = sorted(LABELS["band_frames"], key=lambda b: b["at"])
    assert [round(b["at"], 4) for b in bands][:3] == [2402.3999, 2467.4649, 2493.4909]
    assert all(b["inspected"] and b["band_sha256"] and b["read_from"]["sheet_sha256"] for b in bands)
    assert [tuple(b["expected_players"].values()) for b in bands] == \
        [("PWDDA | Ariz", "Mattiniero")] * 8
    assert [b["expected_games_tally"] for b in bands] == \
        [{"LEFT": 0, "RIGHT": tally} for tally in (0, 0, 1, 1, 2, 2, 2, 2)]
    assert bstruct.pairing_changes(LABELS, [[g["start"], g["end"]] for g in GAMES]) == []


def test_evaluate_refuses_an_empty_game_list_instead_of_grading_nothing_as_a_result():
    with pytest.raises(ValueError, match="no game spans"):
        bstruct.evaluate(labels(), [])
