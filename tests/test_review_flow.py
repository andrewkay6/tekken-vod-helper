"""The review panel's consumer paths, exercised without tkinter.

Everything here runs against real :class:`ProjectState`/``MatchSegment`` objects and the real
``boundary_review`` journal, because the gap this closes was structural: the model helpers existed and
passed their own tests, and nothing the application called used them. So these are consumer regressions
-- decisions through the app's own serialization path, the export plan the app builds its jobs from, the
rows a grouping answer moves, and the panel state the widgets render.
"""

import copy
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_review as review
from tekken_vod_helper.boundary_review_flow import ReviewFlow, ReviewPanel, _candidate_label
from tekken_vod_helper.models import MatchSegment, ProjectState

RECOVERY = {
    "candidates": [
        {"start": 100.0, "end": 200.0, "clip_end_at": 202.0, "games": 1,
         "clip_end_basis": "conclusion plus confirmation plus padding, applied once",
         "clip_end_anchor": {"winner_confirmed_at": 205.0},
         "identities": [], "membership": "supported", "footage_complete": True,
         "game_wins": {"p1": 1, "p2": 0}, "edges": {"start": {
             "at": 100.0, "clock_at_boundary": 58, "scanned": [40.0, 100.0],
             "settled_by": {"at": 96.0, "kind": "cue", "state": "controller_prompt"},
             "walked_past": []}},
         "game_spans": [[100.0, 200.0]]},
        {"start": 300.0, "end": 400.0, "clip_end_at": 402.0, "games": 1,
         "clip_end_basis": "conclusion plus confirmation plus padding, applied once",
         "clip_end_anchor": {"winner_confirmed_at": 401.0},
         "identities": [{"after": ["Slowly", "Dunders"], "before": ["Slowly", "Dunders"],
                         "share": {"after": {"player1": 1.0, "player2": 1.0}}}],
         "membership": "ambiguous", "footage_complete": True, "game_wins": None,
         "alternative_groupings": [{"junction": 350.0, "games": [1, 2]}],
         "edges": {"start": {"at": 300.0, "clock_at_boundary": 60, "scanned": [240.0, 300.0],
                             "settled_by": None, "walked_past": []}},
         "game_spans": [[300.0, 355.0], [360.0, 400.0]], "games": 2},
    ],
}
GAMES = [{"start": 100.0, "end": 200.0, "status": "complete", "winner": "p1",
          "winner_basis": "win_dots", "characters": ["Azucena", "Nina"],
          "conclusion_event_at": 198.0, "winner_confirmed_at": 205.0, "clip_end_at": 202.0,
          "rounds": 2, "counts_toward_set": True},
         {"start": 300.0, "end": 355.0, "status": "complete", "winner": None,
          "winner_basis": None, "characters": ["Jin", "Noctis"],
          "conclusion_event_at": 353.0, "winner_confirmed_at": 354.0, "clip_end_at": 356.0,
          "rounds": 1, "counts_toward_set": True},
         {"start": 360.0, "end": 400.0, "status": "complete", "winner": None,
          "winner_basis": None, "characters": ["Jin", "Noctis"],
          "conclusion_event_at": 400.0, "winner_confirmed_at": 401.0, "clip_end_at": 402.0,
          "rounds": 1, "counts_toward_set": True}]


def imported_state(**overrides) -> ProjectState:
    """A project built the way the CLI builds one, then loaded the way the app loads it."""
    imported = review.import_recovery(RECOVERY, source="test-recovery", games=GAMES)
    project = review.merge_into_project({"duration": 1000.0}, imported["segments"],
                                        event="Basement Brawl 9")
    project.update({"video_path": "/recordings/bb9.mkv", "import_provenance": {"inference_sent": False}})
    project.update(overrides)
    return ProjectState.from_dict(project)


def fingerprint(flow: ReviewFlow, kind: str, start: float) -> str:
    for item in flow.queue("all"):
        if item["kind"] == kind and item["segment"]["start"] == start:
            return item["fingerprint"]
    raise AssertionError("no %s question for %s" % (kind, start))


# ---------------------------------------------------------------------------- the app's own round trip

def test_saving_and_reloading_through_project_state_keeps_the_decision_journal(tmp_path):
    """The reproduction this fixes: ``ProjectState.from_dict(p).to_dict()`` dropped ``p["review"]``."""
    state = imported_state()
    flow = ReviewFlow(state)
    flow.answer(fingerprint(flow, "orientation", 100.0), "swapped")
    written = json.loads(json.dumps(state.to_dict()))
    assert written["review"]["decisions"], "the journal has to be in the bytes the app writes"
    again = ProjectState.from_dict(written)
    reopened = ReviewFlow(again)
    assert reopened.store().governing()[0]["answer"] == "swapped"
    assert again.matches[0].review["orientation"] == "swapped"
    assert [i for i in reopened.queue() if i["kind"] == "orientation"
            and i["segment"]["start"] == 100.0] == [], "a settled question stays settled"
    # The machine's suggestion survives beside the human answer, unmodified.
    assert again.matches[0].recovery["orientation_required"] is True


def test_a_project_that_never_saw_the_review_path_still_saves_the_same_fields():
    plain = ProjectState(video_path="a.mkv", matches=[MatchSegment(start=1.0, end=2.0)])
    assert "review" not in plain.to_dict()
    assert ProjectState.from_dict(plain.to_dict()).review == {}


def test_reopening_a_project_rebuilds_the_projection_from_the_journal(tmp_path):
    """An invalidated answer must not resume governing just because the app was closed and opened."""
    state = imported_state()
    flow = ReviewFlow(state)
    end = fingerprint(flow, "clip_end", 100.0)
    flow.answer(end, "extend", end_at=212.0)
    flow.store().invalidate(100.0, "the conclusion reading moved")
    again = ProjectState.from_dict(json.loads(json.dumps(state.to_dict())))
    reopened = ReviewFlow(again)
    clip = review.resolve_clip_end(reopened.rows()[0][1])
    assert clip["end"] == 202.0, "the machine's padded end is what governs once the answer is withdrawn"
    assert reopened.export_plan()["held"], "and the clip goes back on hold"


# ------------------------------------------------------------------------------------- answers

def test_unsure_leaves_the_open_view_and_stays_resumable_and_consequential():
    state = imported_state()
    flow = ReviewFlow(state)
    orientation = fingerprint(flow, "orientation", 100.0)
    flow.answer(orientation, "unsure")
    assert flow.store().decision(orientation)["status"] == review.DEFERRED
    assert flow.queue("open") and not [i for i in flow.queue("open") if i["fingerprint"] == orientation]
    assert [i for i in flow.queue("deferred") if i["fingerprint"] == orientation]
    assert state.matches[0].review["orientation"] == review.UNRESOLVED
    assert flow.summary()["deferred"] == 1 and flow.summary()["inference_sent"] is False
    # Still consequential: the end question underneath it is untouched and the clip is still held.
    assert flow.export_plan()["held"]


def test_a_start_and_an_end_are_asked_about_for_every_row():
    state = imported_state()
    kinds = [(item["kind"], item["segment"]["start"]) for item in ReviewFlow(state).queue()]
    assert ("new_match", 100.0) in kinds and ("clip_end", 100.0) in kinds
    assert ("new_match", 300.0) in kinds and ("clip_end", 300.0) in kinds
    assert ("membership", 300.0) in kinds, "an open alternative grouping is a question, not a footnote"
    assert ("orientation", 100.0) in kinds and ("orientation", 300.0) not in kinds, \
        "the second set's sides carry names, so it is not asked to invent them"


def test_the_late_start_is_asked_about_from_saved_evidence_alone():
    state = imported_state()
    item = next(i for i in ReviewFlow(state).queue() if i["kind"] == "new_match"
                and i["segment"]["start"] == 100.0)
    assert "clock already reads 58" in item["why_flagged"]
    assert item["boundary"]["late_by_at_least_seconds"] == 2.0
    assert item["boundary"]["earliest_supported_opening"] == 96.0
    assert item["costs_inference"] is False


def test_nudging_a_start_moves_the_row_and_withdraws_the_answers_it_invalidated():
    state = imported_state()
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 100.0)
    flow.answer(fingerprint(flow, "clip_end", 100.0), "extend", end_at=212.0)
    decision = flow.nudge_boundary(start, -2.0)
    assert state.matches[0].start == 98.0 and decision["answer"] == "other_adjust"
    assert review.resolve_clip_end(flow.rows()[0][1])["end"] == 202.0, \
        "the end answer belonged to the opening at 100.000, not this one"
    assert flow.store().decision(fingerprint(flow, "clip_end", 98.0)) is None, \
        "and the moved boundary is a question that has not been asked yet"


def test_an_opening_inside_the_previous_clip_is_refused_with_the_answer_that_fits():
    state = imported_state()
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 300.0)
    with pytest.raises(review.InvalidAnswer) as exc:
        flow.move_start(start, 201.0)
    assert "folding the two rows together" in str(exc.value)


def test_a_manual_end_edit_becomes_the_authority_the_export_obeys():
    """The old controls wrote ``segment.end``, which sits below ``recovery.clip_end_at`` -- so nothing."""
    state = imported_state()
    flow = ReviewFlow(state)
    flow.set_human_end(100.0, 206.0)
    clip = review.resolve_clip_end(flow.rows()[0][1])
    assert clip["end"] == 206.0 and clip["basis"] == "human decision"
    assert state.matches[0].end == 200.0, "the recorded set end is evidence and stays put"
    assert clip["excluded_gameplay"] == [], "the clip now contains the confirmation it cites"
    assert flow.export_plan()["held"] == []


def test_the_end_question_offers_a_choice_not_a_number_and_approval_releases_the_hold():
    state = imported_state()
    flow = ReviewFlow(state)
    end = fingerprint(flow, "clip_end", 100.0)
    assert flow.export_plan()["held"], "the clip stops before the reading that names its winner"
    with pytest.raises(review.InvalidAnswer):
        flow.answer(end, "206")
    flow.answer(end, "confirmed")
    plan = flow.export_plan()
    assert plan["held"] == [] and plan["clips"][0]["approved"] is True
    assert plan["clips"][0]["excluded_gameplay"], "approval records the loss, it does not erase it"
    flow.undo(end)
    assert flow.export_plan()["held"], "undoing the approval puts the clip back under hold"


# ---------------------------------------------------------------------------- structural answers

def test_folding_a_candidate_into_its_neighbour_changes_the_rows_and_the_export():
    state = imported_state()
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 300.0)
    before = len(flow.export_plan()["clips"])
    flow.answer(start, "same_match_next_game")
    assert len(state.matches) == before - 1, "one clip, not two"
    games = state.matches[0].recovery["games"]
    assert [game["start"] for game in games] == [100.0, 300.0, 360.0], \
        "both rows' games are inside the surviving row"
    plan = flow.export_plan()
    assert [clip["end"] for clip in plan["clips"]] == [402.0]
    assert [row for row in plan["unplaced_play"] if row["edge"] == "between"] == [], \
        "the fold closed the gap that used to be unclaimed footage; the tape's own ends are still there"
    assert state.matches[0].review["merged_from"][0]["start"] == 300.0


def test_an_undoable_structure_comes_back_exactly(tmp_path):
    state = imported_state()
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 300.0)
    answer = flow.answer(start, "same_match_next_game")
    assert answer["restorable"] == "merge"
    assert state.review["snapshots"][start]["kind"] == "merge", "the snapshot rides beside the journal"
    assert flow.undo(start) is True
    assert [match.start for match in state.matches] == [100.0, 300.0]
    assert flow.store().decision(start) is None
    assert len(flow.export_plan()["clips"]) == 2
    # The rows came back as they were, evidence and all: the second half's game is its own again.
    assert state.matches[1].recovery["published_game_count"] == 2


def test_saying_not_the_same_set_cuts_the_row_at_the_instant_a_person_chose():
    state = imported_state()
    flow = ReviewFlow(state)
    membership = fingerprint(flow, "membership", 300.0)
    decision = flow.answer(membership, "no_split", end_at=350.0)
    assert decision["restorable"] == "split"
    assert [match.start for match in state.matches] == [100.0, 300.0, 350.0]
    assert [game["start"] for game in state.matches[1].recovery["games"]] == [300.0]
    assert [game["start"] for game in state.matches[2].recovery["games"]] == [360.0]
    assert state.matches[1].end == 355.0 and state.matches[2].start == 350.0
    assert flow.undo(membership) is True
    assert [match.start for match in state.matches] == [100.0, 300.0]


def test_undoing_a_boundary_move_restores_the_row_and_the_answers_it_withdrew():
    """The widget driver caught this: undo used to put the answer away and leave the row where it was."""
    state = imported_state()
    flow = ReviewFlow(state)
    end = fingerprint(flow, "clip_end", 100.0)
    flow.answer(end, "extend", end_at=212.0)
    start = fingerprint(flow, "new_match", 100.0)
    moved = flow.nudge_boundary(start, -2.0)
    assert state.matches[0].start == 98.0 and moved["restorable"] == "move"
    assert review.resolve_clip_end(flow.rows()[0][1])["end"] == 202.0, "the move withdrew the end answer"
    assert flow.undo(start) is True
    assert state.matches[0].start == 100.0, "undo has to move the boundary back, not just say it did"
    assert review.resolve_clip_end(flow.rows()[0][1])["end"] == 212.0, \
        "and the answer that belonged to that opening governs again"
    assert start not in state.review["snapshots"], "a used snapshot is not kept around"


def test_splitting_somewhere_with_nothing_to_split_is_refused():
    state = imported_state()
    flow = ReviewFlow(state)
    with pytest.raises(review.InvalidAnswer):
        flow.split_at(fingerprint(flow, "membership", 300.0), 399.5)


# ------------------------------------------------------------------------ bracket, sides, provenance

def test_a_bracket_match_settles_identity_and_asks_which_side_is_which():
    state = imported_state()
    flow = ReviewFlow(state)
    result = flow.assign_bracket_set(300.0, ["Slowly", "Dunders"], label="Winners Round 2")
    assert result["questions"] and result["questions"][0]["kind"] == "orientation"
    assert state.matches[1].review["bracket"] == {"players": ["Slowly", "Dunders"],
                                                  "label": "Winners Round 2", "orientation": None,
                                                  "source": "start.gg"}
    assert state.matches[1].character1 == "" and state.matches[1].character2 == "", \
        "an unordered character pair is not bound to a side by choosing a bracket match"
    # The names this row's own identity readings already placed stay where the recording put them.
    assert (state.matches[1].player1, state.matches[1].player2) == ("Slowly", "Dunders")
    # On a row whose sides were never named, choosing a pairing binds nothing at all.
    flow.assign_bracket_set(100.0, ["Slowly", "Dunders"], label="Winners Round 3")
    assert state.matches[0].player1 == "" and state.matches[0].player2 == ""
    assert [i for i in flow.queue() if i["kind"] == "orientation"
            and i["segment"]["start"] == 100.0], "and it is still asked which side is which"
    assert [i for i in flow.queue() if i["kind"] == "orientation"
            and i["segment"]["start"] == 300.0] == [], "this row's sides were named, so it is not asked"


def test_orienting_the_sides_binds_names_and_keeps_who_said_what_separate():
    state = imported_state()
    state.player_characters = {"slowly": "Jin", "dunders": "Noctis"}
    flow = ReviewFlow(state)
    flow.assign_bracket_set(300.0, ["Slowly", "Dunders"], label="Winners Round 2")
    result = flow.assign_bracket_set(300.0, ["Dunders", "Slowly"], label="Winners Round 2",
                                     orientation="as-listed")
    assert state.matches[1].player1 == "Dunders" and state.matches[1].player2 == "Slowly"
    assert state.matches[1].character1 == "Noctis" and state.matches[1].character2 == "Jin"
    sources = {side: entry["source"] for side, entry in state.matches[1].review["characters"].items()}
    assert sources == {"p1": "remembered", "p2": "remembered"}, \
        "an operator's habit fills a blank and is labelled as habit, never as footage"
    assert result["export_ready"] is True
    assert [i for i in flow.queue() if i["kind"] == "orientation"
            and i["segment"]["start"] == 300.0] == []


def test_an_observed_side_character_survives_a_remembered_default_and_a_reorientation():
    state = imported_state()
    state.player_characters = {"dunders": "Noctis"}
    flow = ReviewFlow(state)
    match, view = flow.rows()[0]
    view["recovery"]["sides"]["p2"] = {"player": "", "character": "Azucena", "source": "observed"}
    flow.assign_bracket_set(100.0, ["Slowly", "Dunders"], orientation="as-listed")
    assert state.matches[0].character2 == "Azucena", "the tape said so; a habit cannot overrule it"
    assert state.matches[0].review["characters"]["p2"]["source"] == "observed"
    assert state.matches[0].character1 == "" and state.matches[0].player1 == "Slowly"


def test_a_typed_name_is_marked_human_and_survives_the_next_import():
    state = imported_state()
    flow = ReviewFlow(state)
    flow.set_field(100.0, "player1", "CrispyBacon")
    assert state.matches[0].player1 == "CrispyBacon"
    assert state.matches[0].review["field_edits"]["player1"]["source"] == "human"
    again = review.import_recovery(RECOVERY, source="test-recovery", games=GAMES)
    merged = review.merge_into_project({"duration": 1000.0,
                                        "matches": [state.to_dict()["matches"][0]]},
                                       again["segments"])
    assert merged["matches"][0]["player1"] == "CrispyBacon"


def test_a_character_side_a_person_names_is_recorded_as_human():
    state = imported_state()
    flow = ReviewFlow(state)
    flow.set_character(100.0, "p1", "Azucena")
    assert state.matches[0].character1 == "Azucena"
    assert state.matches[0].review["characters"]["p1"] == {"player": "", "character": "Azucena",
                                                           "source": "human"}
    with pytest.raises(review.InvalidAnswer):
        flow.set_character(100.0, "p3", "Nobody")


def test_reimporting_a_moved_start_keeps_one_row_and_invalidates_the_answers_it_moved():
    state = imported_state()
    flow = ReviewFlow(state)
    flow.answer(fingerprint(flow, "clip_end", 100.0), "extend", end_at=212.0)
    moved = copy.deepcopy(RECOVERY)
    moved["candidates"][0]["start"] = 99.0
    moved["candidates"][0]["edges"]["start"]["at"] = 99.0
    imported = review.import_recovery(moved, source="test-recovery-2", games=GAMES)
    reloaded = ProjectState.from_dict(review.merge_into_project(
        state.to_dict(), imported["segments"], duration=1000.0))
    assert [match.start for match in reloaded.matches] == [99.0, 300.0], "one row for one recording"
    reopened = ReviewFlow(reloaded)
    invalidated = reopened.store().state["decisions"][0]
    assert invalidated["status"] == review.INVALIDATED and "re-import" in invalidated["invalidated_reason"]
    assert invalidated["identity"]["start_was"] == 100.0
    assert review.resolve_clip_end(reopened.rows()[0][1])["end"] == 202.0


# ------------------------------------------------------------------------------------- the panel

def test_the_panel_shows_the_source_it_will_play_and_what_it_costs():
    state = imported_state()
    view = ReviewPanel(state).state_view()
    assert view["source"] == {"video_path": "/recordings/bb9.mkv", "has_source": True,
                              "duration": 1000.0, "event_name": "Basement Brawl 9"}
    assert view["costs_inference"] is False and view["summary"]["inference_sent"] is False
    assert view["question"]["context"]["play_to"] - view["question"]["context"]["play_from"] == \
        pytest.approx(24.0), "short context around the boundary, not the whole set"
    assert any(button["action"] == "answer:unsure" for button in view["question"]["controls"])
    assert view["held_clips"], "the panel opens with the hold visible"


def test_a_project_with_no_source_tells_the_operator_instead_of_failing():
    view = ReviewPanel(imported_state(video_path="")).state_view()
    assert view["source"]["has_source"] is False
    assert view["question"] is not None, "the queue is still reviewable from saved evidence"


def test_pressing_a_button_answers_and_the_message_says_what_happened():
    panel = ReviewPanel(imported_state())
    first = panel.state_view()["question"]
    assert first["kind"] == "new_match"
    panel.press("select:" + first["fingerprint"])
    message = panel.press("answer:new_match")
    assert "recorded as decided" in message
    assert panel.state_view()["summary"]["answered"] == 1


def test_pressing_undo_rebuilds_the_consequence_and_the_row_count():
    panel = ReviewPanel(imported_state())
    view = panel.state_view()
    end = next(row for row in view["rows"] if row["kind"] == "clip_end")
    panel.press("select:" + end["fingerprint"])
    panel.press("answer:extend")  # no target: refused, and the message says so
    assert panel.message.startswith("refused")
    panel.flow.answer(end["fingerprint"], "extend", end_at=212.0)
    assert panel.flow.export_plan()["held"] == []
    panel.press("undo")
    assert "undid" in panel.message
    assert panel.flow.export_plan()["held"], "the hold came back with the question"


def test_more_footage_grows_the_window_without_touching_the_media(tmp_path):
    panel = ReviewPanel(imported_state())
    view = panel.state_view()
    before = view["question"]["context"]
    panel.press("play:back")
    after = panel.state_view()["question"]["context"]
    assert after["play_from"] < before["play_from"]
    assert panel.playback["from"] == after["play_from"]
    assert "nothing new was analysed" in panel.message
    panel.press("play:forward")
    assert panel.state_view()["question"]["context"]["play_to"] > after["play_to"]


def test_an_unknown_button_is_reported_and_an_impossible_one_is_refused():
    panel = ReviewPanel(imported_state())
    panel.state_view()
    assert panel.press("self_destruct") == "the panel has no action self_destruct"
    assert panel.press("end:now").startswith("refused"), "no clip-end question is selected"


def test_orientation_is_a_button_that_needs_the_pairing_it_orientates():
    panel = ReviewPanel(imported_state())
    orientation = next(row for row in panel.state_view()["rows"] if row["kind"] == "orientation")
    panel.press("select:" + orientation["fingerprint"])
    controls = {button["action"]: button for button in panel.state_view()["question"]["controls"]}
    assert "orientation:as-listed" in controls and controls["orientation:as-listed"]["enabled"] is False
    panel.press("orientation:as-listed")
    assert panel.message.startswith("refused") and "choose the bracket match" in panel.message
    panel.choose_bracket(["Slowly", "Dunders"], label="Winners Round 2")
    controls = {button["action"]: button for button in panel.state_view()["question"]["controls"]}
    assert controls["orientation:swapped"]["enabled"] is True
    panel.press("orientation:swapped")
    match = panel.flow.state.matches[0]
    assert (match.player1, match.player2) == ("Dunders", "Slowly")
    assert match.review["orientation"] == "swapped"
    assert match.review["bracket"]["label"] == "Winners Round 2"


def test_answering_stays_on_the_clip_being_reviewed():
    """The panel used to snap back to the top of the queue after every answer."""
    panel = ReviewPanel(imported_state())
    first = panel.state_view()["question"]
    assert first["kind"] == "new_match"
    panel.press("answer:new_match")
    stayed = panel.state_view()["question"]
    assert stayed["kind"] == "clip_end" and stayed["segment"] == first["segment"], \
        "the next question about the same clip should be next, not the top of the list"
    panel.press("answer:confirmed")
    assert panel.state_view()["question"]["segment"] == first["segment"]


def test_the_review_layers_cannot_reach_a_model_or_the_encoder():
    """The zero-inference promise is checked statically, because a click must never be able to spend one.

    A monkeypatched call site would only prove the one path this test walked. Importing no model, media or
    reasoning module is the stronger claim, and it is the one that keeps holding as the panel grows.
    """
    import ast
    from pathlib import Path as _Path
    forbidden = {"local_model", "boundary_media", "boundary_reasoning", "boundary_investigate",
                 "boundary_games", "boundary_cli", "ffmpeg_tools", "vlc"}
    for name in ("boundary_review.py", "boundary_review_flow.py"):
        tree = ast.parse((_Path(__file__).resolve().parents[1] / "tekken_vod_helper" / name).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[-1] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[-1])
        assert imported & forbidden == set(), "%s may not reach %s" % (name, imported & forbidden)


def test_the_panel_reports_deferring_and_reading_as_free():
    panel = ReviewPanel(imported_state())
    question = panel.state_view()["question"]
    panel.press("select:" + question["fingerprint"])
    panel.press("answer:unsure")
    panel.press("play:context")
    view = panel.state_view()
    assert view["costs_inference"] is False
    assert view["summary"]["inference_sent"] is False
    assert panel.flow.item(question["fingerprint"], view="deferred")["costs_inference"] is False




def test_the_export_plan_is_the_same_object_the_app_builds_jobs_from():
    """Guard against the two halves drifting: the plan the panel shows is the plan the app obeys."""
    state = imported_state()
    flow = ReviewFlow(state)
    plan = flow.export_plan()
    assert [clip["start"] for clip in plan["exportable"]] == [300.0]
    # Held footage counts as covered for the candidates and as unclaimed for the export: 204 s of the tape
    # is inside a candidate, and only 102 s of it would actually be written.
    assert plan["counts"]["segments"] == 2 and plan["counts"]["exportable"] == 1 \
        and plan["counts"]["held"] == 1
    assert plan["coverage"]["candidates"]["seconds"] == 204.0
    assert plan["coverage"]["exportable_jobs"]["seconds"] == 102.0
    assert plan["counts"]["unplaced_intervals"] == 2, "leading and trailing edges, not between-clips only"
    assert plan["inference_sent"] is False and plan["media_rendered"] is False


def test_unresolved_gameplay_lists_gaps_and_holds_in_tape_order():
    """The list a reviewer uses to find a missed match has to include the ends of the tape.

    With one clip on hold at 100-202 the held interval is unclaimed footage too, so the account starts with
    the leading stretch that already contains it, and the two are distinguishable by kind and by whether
    saved evidence puts play inside them.
    """
    state = imported_state()
    rows = ReviewFlow(state).unresolved_gameplay()
    assert [row["kind"] for row in rows] == ["gap", "held", "gap"]
    assert [row["edge"] for row in rows if row["kind"] == "gap"] == ["leading", "trailing"]
    assert rows[0]["from"] == 0.0 and rows[0]["to"] == 300.0
    assert rows[0]["gameplay_seen_in_saved_evidence"] is True, "a game opens inside the held clip's tape"
    assert rows[2]["from"] == 402.0 and rows[2]["gameplay_seen_in_saved_evidence"] is False
    assert all(row["playable"] is True for row in rows)


# ---------------------------------------------------------------- the sequence through the real consumers

SEQUENCE_ROWS = [{"timestamp": 100.0, "screen_state": "gameplay", "round_clock": 4},
                 {"timestamp": 103.0, "screen_state": "controller_prompt"},
                 {"timestamp": 106.0, "screen_state": "character_select"},
                 {"timestamp": 110.0, "screen_state": "gameplay", "round_clock": 60}]


def segment_row(**over):
    row = {"start": 110.0, "end": 200.0,
           "recovery": {"clip_end_at": 202.0, "clip_end_anchor": {"winner_confirmed_at": 200.0},
                        "edges": {"start": {"at": 110.0}}, "games": [{"start": 110.0, "end": 200.0}]}}
    row.update(over)
    return row


def timeline_at(tmp_path, rows=None, name="timeline.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"observations": rows or SEQUENCE_ROWS}), encoding="utf-8")
    return path


def test_project_state_carries_the_sequence_source_through_a_save_and_reopen(tmp_path):
    """The desktop shipped this broken: the helper took rows, the model dropped them, the app saw none.

    A reference rather than a copy of the rows, because a project is rewritten on every decision and the
    timeline is 2,019 rows -- but the reference is re-checked by digest, so "the file changed underneath"
    is an answer the panel can give, not a claim nobody noticed.
    """
    from tekken_vod_helper import boundary_review as review
    state = imported_state()
    assert state.sequence_source == {} and state.sequence_rows == []
    reference = review.sequence_reference(timeline_at(tmp_path), note="test rows")
    state.sequence_source = reference
    again = ProjectState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert again.sequence_source["sha256"] == reference["sha256"]
    rows, provenance = ReviewFlow(again).opening_candidate(
        next(i for i in ReviewFlow(again).queue("all") if i["kind"] == "new_match")["fingerprint"]
    )["at"], ReviewFlow(again).sequence_provenance()
    assert provenance["available"] is True and provenance["rows_read"] == len(SEQUENCE_ROWS)


def test_raw_helper_and_app_flow_report_the_same_candidate(tmp_path):
    """Codex's divergence, made a test: raw jump=105 while the app's own flow said None."""
    from tekken_vod_helper import boundary_review as review
    for label, extra in (("inline", {"sequence_rows": SEQUENCE_ROWS}),
                         ("reference", {"sequence_source": review.sequence_reference(
                             timeline_at(tmp_path))})):
        project = {"duration": 300.0, "matches": [segment_row()], **extra}
        raw = next(item for item in review.review_queue(project) if item["kind"] == "new_match")
        flow_item = next(item for item in ReviewFlow(ProjectState.from_dict(project)).queue()
                         if item["kind"] == "new_match")
        assert raw["adjust"]["jump_to"] == flow_item["adjust"]["jump_to"] == 103.0, label
        assert flow_item["adjust"]["jump_to_level"] == "pre_pairing_context", label


def test_a_missing_or_swapped_sequence_file_is_reported_and_changes_nothing(tmp_path):
    from tekken_vod_helper import boundary_review as review
    timeline = timeline_at(tmp_path)
    reference = review.sequence_reference(timeline)
    missing = {"duration": 300.0, "matches": [segment_row()],
               "sequence_source": dict(reference, path=str(tmp_path / "gone.json"))}
    item = next(i for i in ReviewFlow(ProjectState.from_dict(missing)).queue()
                if i["kind"] == "new_match")
    assert item["adjust"]["jump_to"] is None, "a file that is not there cannot supply an instant"
    provenance = ReviewFlow(ProjectState.from_dict(missing)).sequence_provenance()
    assert provenance["available"] is False and "not here" in provenance["reason"]

    # A different file that kept the old path: refuse, do not quietly read the new corpus.
    other = timeline_at(tmp_path, rows=[{"timestamp": 100.0, "screen_state": "controller_prompt"},
                                        {"timestamp": 101.0, "screen_state": "vs_screen"}],
                        name="other.json")
    swapped = {"duration": 300.0, "matches": [segment_row()],
               "sequence_source": dict(reference, path=str(other))}
    swapped_item = next(i for i in ReviewFlow(ProjectState.from_dict(swapped)).queue()
                        if i["kind"] == "new_match")
    assert swapped_item["adjust"]["jump_to"] is None
    assert "refusing to read openings from a different corpus" in \
        ReviewFlow(ProjectState.from_dict(swapped)).sequence_provenance()["reason"]


def test_looking_at_a_candidate_never_moves_the_boundary():
    panel = ReviewPanel(ProjectState.from_dict({"duration": 300.0, "matches": [segment_row()],
                                                "sequence_rows": SEQUENCE_ROWS}))
    view = panel.state_view()
    button = next(b for b in view["question"]["controls"] if b["action"] == "play:candidate")
    assert button["enabled"] is True and "pre_pairing_context" in button["label"]
    message = panel.press("play:candidate")
    assert panel.playback["boundary_at"] == 103.0
    assert "the clip still opens at 110.000 until you move it" in message
    assert panel.flow.state.matches[0].start == 110.0, "a look was turned into an edit"
    assert panel.flow.state.matches[0].review.get("decisions") in (None, [])


def test_the_candidate_button_names_the_width_of_the_instant_it_offers():
    """An interval offered as a point is a measurement nobody took.

    The label is the one place a reviewer meets the recovery before deciding to look, so the width has to be
    in it -- and only where the recovery actually bounds the instant. A ``pre_pairing_context`` candidate gets
    no number rather than a borrowed one, and a ``None`` width never prints as zero.
    """
    assert _candidate_label({"jump_to_level": "round_opening",
                             "jump_to_uncertainty_seconds": 5.004999}) == \
        "Look just before the candidate (round_opening, ±5.005 s)"
    assert _candidate_label({"jump_to_level": "pre_pairing_context",
                             "jump_to_uncertainty_seconds": 5.004999}) == \
        "Look just before the candidate (pre_pairing_context)"
    assert _candidate_label({"jump_to_level": "round_opening"}) == \
        "Look just before the candidate (round_opening)"
    assert _candidate_label({}) == "Look just before the candidate (none)"
    assert _candidate_label({"jump_to_level": "round_opening",
                             "jump_to_uncertainty_seconds": True}) == \
        "Look just before the candidate (round_opening)", "a bool is not a duration"


def test_a_row_with_no_candidate_offers_no_button_and_says_why():
    rows = [{"timestamp": 108.0, "screen_state": "gameplay", "round_clock": 20},
            {"timestamp": 110.0, "screen_state": "gameplay", "round_clock": 18}]
    panel = ReviewPanel(ProjectState.from_dict({"duration": 300.0, "matches": [segment_row()],
                                               "sequence_rows": rows}))
    view = panel.state_view()
    button = next(b for b in view["question"]["controls"] if b["action"] == "play:candidate")
    assert button["enabled"] is False
    assert panel.press("play:candidate").startswith("refused")
    assert view["coverage"]["sequence"]["available"] is True, "the rows were read; they just say nothing"


def test_the_summary_keeps_exportable_supported_accepted_and_validated_apart():
    """One word used to cover all four, and an unreviewed row came out 'accepted'."""
    panel = ReviewPanel(ProjectState.from_dict({"duration": 300.0, "matches": [segment_row(
        recovery={"clip_end_at": 202.0, "clip_end_anchor": {"winner_confirmed_at": 200.0},
                  "edges": {"start": {"at": 110.0}}})]}))
    summary = panel.state_view()["summary"]
    assert summary["exportable"] == 1 and summary["held"] == 0
    assert summary["operator_accepted"] == 0
    assert summary["unknown_starts"] == 1 and summary["machine_supported_starts"] == 0
    assert summary["independently_validated"] == 0, "nothing on this machine validates a boundary"
    assert summary["inference_sent"] is False
    note = panel.state_view()["coverage"]["boundary_summary"]["note"]
    assert "validated needs a reference outside both" in note


def test_review_project_cli_records_the_sequence_it_used(tmp_path, capsys):
    """The public import path has to leave the evidence pointer in the file the app opens."""
    from tekken_vod_helper.boundary_cli import main as cli_main
    timeline = timeline_at(tmp_path)
    plan = tmp_path / "public-plan.json"
    plan.write_text(json.dumps({"duration": 300.0,
                                "evidence": {"facts": str(timeline)},
                                "recovery": {"candidates": [{
                                    "start": 110.0, "end": 200.0, "clip_end_at": 202.0,
                                    "clip_end_anchor": {"winner_confirmed_at": 200.0},
                                    "edges": {"start": {"at": 110.0, "clock_at_boundary": 60}},
                                    "membership": "supported", "footage_complete": True,
                                    "game_wins": {"p1": 1, "p2": 0}}]}}, indent=1), encoding="utf-8")
    out = tmp_path / "project.tvh.json"
    cli_main(["review-project", "--recovery", str(plan), "--output", str(out)])
    project = json.loads(out.read_text(encoding="utf-8"))
    assert project["sequence_source"]["path"] == str(timeline)
    assert project["sequence_source"]["rows"] == len(SEQUENCE_ROWS)
    assert project["import_provenance"]["sequence_source"] == str(timeline)
    # An explicit flag wins over the plan's own association, and says which it was.
    other = timeline_at(tmp_path, name="explicit.json")
    cli_main(["review-project", "--recovery", str(plan), "--output", str(out),
              "--sequence-timeline", str(other)])
    chosen = json.loads(out.read_text(encoding="utf-8"))
    assert chosen["sequence_source"]["path"] == str(other)
    assert chosen["sequence_source"]["note"] == "named by --sequence-timeline"


def test_review_project_cli_carries_the_saved_observation_disposition(tmp_path):
    """A project has to say what its saved readings did, not merely that some strings changed.

    The per-set rows name the readings a candidate quotes; without the run block a reader could not tell how
    many readings there were, how many corrected a gameplay row, and how many were kept as off-play evidence
    or refused -- which is the exact ambiguity that let one frame be reported as two corrections.
    """
    from tekken_vod_helper.boundary_cli import main as cli_main

    disposition = {"admitted_text_observations": 2, "unique_text_observations": 2,
                   "candidate_references": 3,
                   "counts": {"applied_within_game": 1, "carry_in_context_only": 0, "off_play_only": 1,
                              "refused_on_row_text": 0, "unused": 0},
                   "observations": [{"observation_id": "a", "role": "within_game"},
                                    {"observation_id": "b", "role": "off_play"}],
                   "identity_bound_by_observations": 0, "accepted_by_operator": 0,
                   "rule": "one observation and two references is one observation"}
    timeline = timeline_at(tmp_path)
    plan = tmp_path / "plan-with-observations.json"
    plan.write_text(json.dumps({"duration": 300.0, "evidence": {"facts": str(timeline)},
                                "observations": {"accepted": 2, "unique_text_observations": 2,
                                                 "inference_sent": False, "authoritative": False,
                                                 "binds_no_participant_identity": True,
                                                 "counts_as_acceptance": False,
                                                 "disposition": disposition},
                                "recovery": {"candidates": [{
                                    "start": 110.0, "end": 200.0, "clip_end_at": 202.0,
                                    "clip_end_anchor": {"winner_confirmed_at": 200.0},
                                    "edges": {"start": {"at": 110.0, "clock_at_boundary": 60}},
                                    "membership": "supported", "footage_complete": True,
                                    "game_wins": {"p1": 1, "p2": 0}}]}}, indent=1), encoding="utf-8")
    out = tmp_path / "project.tvh.json"
    cli_main(["review-project", "--recovery", str(plan), "--output", str(out)])

    carried = json.loads(out.read_text(encoding="utf-8"))["import_provenance"]["saved_observations"]
    assert carried["disposition"]["counts"] == disposition["counts"]
    assert carried["disposition"]["candidate_references"] == 3
    assert carried["disposition"]["identity_bound_by_observations"] == 0
    assert carried["authoritative"] is False and carried["counts_as_acceptance"] is False
    # and a save/load round trip keeps it, or the claim dies with the terminal that printed it
    again = json.loads(json.dumps(json.loads(out.read_text(encoding="utf-8"))))
    assert again["import_provenance"]["saved_observations"]["disposition"]["counts"] == \
        disposition["counts"]


def test_review_project_without_any_sequence_says_so_in_the_project(tmp_path):
    from tekken_vod_helper.boundary_cli import main as cli_main
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"duration": 300.0, "recovery": {"candidates": [
        {"start": 110.0, "end": 200.0, "clip_end_at": 202.0,
         "clip_end_anchor": {"winner_confirmed_at": 200.0},
         "edges": {"start": {"at": 110.0}}}]}}, indent=1), encoding="utf-8")
    out = tmp_path / "project.tvh.json"
    cli_main(["review-project", "--recovery", str(plan), "--output", str(out)])
    project = json.loads(out.read_text(encoding="utf-8"))
    assert "sequence_source" not in project
    assert project["import_provenance"]["sequence_source"] == "none: openings were not considered"
    assert project["import_provenance"]["sequence_rows"] == 0


# --------------------------------------------------------- a partial is not an accepted complete boundary


def partial_project(start=100.0, end=200.0, edge_at=None, clock=58, confirmed_at=None,
                    **recovery_extra):
    """A one-row project whose opening carries a saved reading.

    ``confirmed_at`` defaults to inside the clip's tape, so a row's end loses nothing until a test asks for
    the version of this fixture where it does.
    """
    confirmed = end + 1.0 if confirmed_at is None else confirmed_at
    recovery = {"clip_end_at": end + 2.0,
                "clip_end_anchor": {"winner_confirmed_at": confirmed},
                "clip_end_basis": "conclusion plus confirmation plus padding, applied once",
                "games": [{"start": start, "end": end, "conclusion_event_at": end - 2.0,
                           "winner_confirmed_at": confirmed}],
                "edges": {"start": {"at": edge_at if edge_at is not None else start,
                                    "clock_at_boundary": clock, "walked_past": []}}}
    recovery.update(recovery_extra)
    return ProjectState.from_dict({"duration": 600.0, "video_path": "/recordings/bb9.mkv",
                                   "matches": [{"start": start, "end": end, "recovery": recovery}]})


@pytest.mark.parametrize("order", [("start", "end"), ("end", "start")])
def test_an_approved_partial_stays_a_partial_whatever_order_the_two_answers_came_in(tmp_path, order):
    """Confirming an end cannot finish a boundary a person only agreed to ship half of.

    ``accept_partial`` is the button that says "I know the opening is missing; export it anyway". The row
    counted as an operator-accepted boundary as soon as its end was confirmed, because acceptance looked for
    two answered questions rather than for what the answers said -- so the order of two clicks decided
    whether the mistake showed up at all.
    """
    state = partial_project()
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 100.0)
    end = fingerprint(flow, "clip_end", 100.0)
    for which in order:
        if which == "start":
            flow.answer(start, "accept_partial",
                        note="the opening is inside a round; the set is watchable without it")
        else:
            flow.answer(end, "confirmed")

    plan = flow.export_plan()
    position = plan["clips"][0]["boundary"]
    assert position["exportable"] is True, "the partial still ships, and that was never the question"
    assert position["operator_answered"] == {"start": True, "end": True}
    assert position["operator_confirms"] == {"start": False, "end": True}, \
        "having answered is not the same as saying this is the boundary"
    assert position["operator_accepted_boundary"] is False
    assert position["partial_override"] is True
    assert position["partial_approval"] == {"opening_approved_partial": True,
                                            "end_excludes_gameplay": False}
    assert position["independently_validated"] is False, "no answer of anybody's marks a second measurement"
    assert position["claim_this_row_may_make"] \
        == "exportable; operator approved a partial here, not a complete accepted boundary"
    assert plan["counts"]["operator_accepted"] == 0, "the totals have to agree with the sentence"
    assert plan["counts"]["operator_approved_partials"] == 1
    assert flow.summary()["operator_accepted"] == 0
    assert flow.summary()["operator_approved_partials"] == 1
    boundary_view = ReviewPanel(state).state_view()["coverage"]["boundary_summary"]
    assert boundary_view["operator_accepted"] == 0
    assert boundary_view["operator_approved_partial"] == 1

    # Save, reopen, and the row says the same thing about itself.
    flow.store().save(tmp_path / "partial.tvh.json")
    again = ReviewFlow(ProjectState.from_dict(
        json.loads((tmp_path / "partial.tvh.json").read_text(encoding="utf-8"))))
    reopened = again.export_plan()["clips"][0]["boundary"]
    assert reopened["operator_accepted_boundary"] is False
    assert reopened["partial_override"] is True
    assert again.summary()["operator_approved_partials"] == 1

    # Replacing the partial with a real answer is what accepts a boundary, not a second click somewhere.
    flow.answer(start, "new_match")
    accepted = flow.export_plan()["clips"][0]["boundary"]
    assert accepted["operator_accepted_boundary"] is True
    assert accepted["partial_override"] is False
    assert accepted["claim_this_row_may_make"] == \
        "exportable; operator-accepted, not independently validated"
    assert flow.export_plan()["counts"]["operator_accepted"] == 1
    assert flow.export_plan()["counts"]["operator_approved_partials"] == 0

    # Withdrawing the replacement puts the partial back; withdrawing it entirely leaves neither claim.
    flow.answer(start, "accept_partial", note="changed my mind, the opening is gone")
    assert flow.export_plan()["clips"][0]["boundary"]["operator_accepted_boundary"] is False
    flow.undo(start)
    withdrawn = flow.export_plan()["clips"][0]["boundary"]
    assert withdrawn["operator_accepted_boundary"] is False
    assert withdrawn["partial_override"] is False
    assert withdrawn["operator_answered"]["start"] is False


def test_an_end_that_loses_play_is_an_approved_partial_even_when_both_sides_are_answered(tmp_path):
    """The same rule at the other boundary: approving a cut that drops gameplay is not accepting it.

    Here the confirmation really is outside the clip -- the operator said so by confirming a stop before it
    -- so the row is a partial in the end direction, and answering the opening with "new match" cannot turn
    an approved loss back into a certified pair of boundaries.
    """
    # The winner confirmation sits at 205.0 and the machine's tape stops at 202.0, so any end the operator
    # approves here is an approval of a clip that loses the end of the game.
    state = partial_project(confirmed_at=205.0)
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 100.0)
    end = fingerprint(flow, "clip_end", 100.0)
    flow.answer(start, "new_match")
    flow.answer(end, "confirmed")
    plan = flow.export_plan()
    clip = plan["clips"][0]
    assert clip["excluded_gameplay"], "the clip stops before the confirmation it cites"
    position = clip["boundary"]
    assert position["operator_answered"] == {"start": True, "end": True}
    assert position["operator_confirms"] == {"start": True, "end": True}
    assert position["partial_approval"] == {"opening_approved_partial": False,
                                            "end_excludes_gameplay": True}
    assert position["operator_accepted_boundary"] is False
    assert plan["counts"]["operator_approved_partials"] == 1
    assert plan["counts"]["operator_accepted"] == 0


def test_moving_an_opening_invalidates_a_boundary_it_no_longer_describes(tmp_path):
    """A move is not an acceptance, and an approval that travelled with the row would be one.

    The journal already withdraws answers whose evidence moved; the boundary claim has to follow it, or a
    person who nudged a start by ten seconds keeps the acceptance they gave a different frame.
    """
    state = partial_project()
    flow = ReviewFlow(state)
    start = fingerprint(flow, "new_match", 100.0)
    flow.answer(start, "new_match")
    flow.answer(fingerprint(flow, "clip_end", 100.0), "confirmed")
    assert flow.export_plan()["clips"][0]["boundary"]["operator_accepted_boundary"] is True
    moved = flow.move_start(start, 95.0)
    assert moved["answer"] == "other_adjust"
    position = next(clip for clip in flow.export_plan()["clips"] if clip["start"] == 95.0)["boundary"]
    assert position["operator_answered"]["end"] is False, \
        "the end was answered about the old row, and moving the row moves that question"
    assert position["operator_accepted_boundary"] is False


# ----------------------------------------------------------- evidence is only evidence at its own instant


def test_a_moved_start_is_not_still_contradicted_by_a_clock_it_never_read(tmp_path):
    """The reading was taken at 100.000; the clip now opens at 90.000, and those are different frames.

    Inside a round the clock falls, so a 58 read at one instant says what it said *there*. Carrying it to
    wherever the reviewer last nudged reports a contradiction nobody measured, about a frame nobody read.
    The support becomes unknown with the reason naming both instants, the original observation stays in the
    record where it was made, and nothing is re-derived by subtraction.
    """
    flow = ReviewFlow(partial_project())
    flow.move_start(fingerprint(flow, "new_match", 100.0), 90.0)
    clip = next(c for c in flow.export_plan()["clips"] if c["start"] == 90.0)
    position = clip["boundary"]
    assert position["machine_supports_start"] == "unknown", \
        "a reading taken elsewhere cannot contradict this instant"
    assert "100.000" in position["start_why"] and "90.000" in position["start_why"]
    assert clip["opening"]["late_by_at_least_seconds"] is None

    # The evidence itself is frozen: re-evaluating applicability never rewrites where a reading was taken.
    edge = next(match for match in flow.state.matches if match.start == 90.0).recovery["edges"]["start"]
    assert edge["at"] == 100.0 and edge["clock_at_boundary"] == 58

    # The queue says it too, because that is the screen the reviewer is standing on.
    item = next(i for i in flow.queue("all")
                if i["kind"] == "new_match" and i["segment"]["start"] == 90.0)
    assert item["boundary"]["evidence_describes_boundary"] is False
    assert item["boundary"]["saved_reading_at"] == 100.0
    assert item["boundary"]["late_by_at_least_seconds"] is None
    assert "100.000" in item["boundary"]["needs_review_why"]
    assert item["evidence"]["start_edge_observation"]["clock_at_boundary"] == 58, \
        "the saved reading stays visible where it was taken"
    assert item["evidence"]["start_clock_at_boundary"] is None

    flow.store().save(tmp_path / "moved.tvh.json")
    again = ReviewFlow(ProjectState.from_dict(
        json.loads((tmp_path / "moved.tvh.json").read_text(encoding="utf-8"))))
    reopened = next(c for c in again.export_plan()["clips"] if c["start"] == 90.0)["boundary"]
    assert reopened["machine_supports_start"] == "unknown"
    assert "100.000" in reopened["start_why"]


def test_a_small_nudge_keeps_the_reading_because_it_is_still_the_same_frame(tmp_path):
    """One sample of the saved series is the line: within it the reading is this instant's best word."""
    flow = ReviewFlow(partial_project())
    flow.move_start(fingerprint(flow, "new_match", 100.0), 99.9)
    position = next(c for c in flow.export_plan()["clips"] if c["start"] == 99.9)["boundary"]
    assert position["machine_supports_start"] == "contradicted"
    assert "reads 58" in position["start_why"]


def settled_project(rows):
    """The same one-row project with saved sequence rows beside it, so an instant can be read again."""
    state = ProjectState.from_dict({"duration": 600.0, "video_path": "/recordings/bb9.mkv",
                                    "sequence_rows": rows,
                                    "matches": [{"start": 100.0, "end": 200.0, "recovery": {
                                        "clip_end_at": 202.0,
                                        "clip_end_anchor": {"winner_confirmed_at": 201.0},
                                        "games": [{"start": 100.0, "end": 200.0,
                                                   "winner_confirmed_at": 201.0}],
                                        "edges": {"start": {"at": 100.0, "clock_at_boundary": 58,
                                                            "walked_past": []}}}}]})
    return state


def test_a_moved_boundary_the_sequence_can_read_is_settled_by_its_own_rows(tmp_path):
    """What does count once a boundary moves: evidence taken *there*, at its own value, unmodified.

    Three instants at the new start of 90.000 -- a play row that reads its own clock, an opening screen at
    this frame, and context that lives only further back -- and each gets exactly the verdict its evidence
    supports, the dullest of them being unknown.
    """
    playing = ReviewFlow(settled_project([
        {"timestamp": 80.0, "screen_state": "gameplay", "round_clock": 60},
        {"timestamp": 89.5, "screen_state": "gameplay", "round_clock": 40}]))
    playing.move_start(fingerprint(playing, "new_match", 100.0), 90.0)
    contradicted = next(c for c in playing.export_plan()["clips"] if c["start"] == 90.0)["boundary"]
    assert contradicted["machine_supports_start"] == "contradicted"
    assert "reads 40" in contradicted["start_why"], "the new instant's own reading, not the old one"
    assert "58" not in contradicted["start_why"]

    opening = ReviewFlow(settled_project([
        {"timestamp": 80.0, "screen_state": "gameplay", "round_clock": 30},
        {"timestamp": 89.4, "screen_state": "controller_prompt", "round_clock": None}]))
    opening.move_start(fingerprint(opening, "new_match", 100.0), 90.0)
    supported = next(c for c in opening.export_plan()["clips"] if c["start"] == 90.0)["boundary"]
    assert supported["machine_supports_start"] == "supported"
    assert "controller_prompt" in supported["start_why"] and "this instant" in supported["start_why"]

    behind_only = ReviewFlow(settled_project([
        {"timestamp": 70.0, "screen_state": "controller_prompt"},
        {"timestamp": 72.0, "screen_state": "character_select"}]))
    behind_only.move_start(fingerprint(behind_only, "new_match", 100.0), 90.0)
    unknown = next(c for c in behind_only.export_plan()["clips"] if c["start"] == 90.0)["boundary"]
    assert unknown["machine_supports_start"] == "unknown"
    assert "pre_pairing_context at 70.000" in unknown["start_why"]
    assert "place to look" in unknown["start_why"]


def test_earlier_context_is_a_place_to_look_and_never_support_for_the_frame_it_is_not(tmp_path):
    """A screen behind the boundary used to be enough to call a start supported. It is only somewhere to go.

    The jump-to control keeps offering it, with its level, and moves nothing: looking is the reviewer's,
    and so is deciding.
    """
    state = ProjectState.from_dict({"duration": 600.0, "video_path": "/recordings/bb9.mkv",
                                     "sequence_rows": [
                                         {"timestamp": 96.0, "screen_state": "controller_prompt"},
                                         {"timestamp": 97.5, "screen_state": "character_select"}],
                                     "matches": [{"start": 100.0, "end": 200.0, "recovery": {
                                         "clip_end_at": 202.0,
                                         "clip_end_anchor": {"winner_confirmed_at": 201.0},
                                         "games": [{"start": 100.0, "end": 200.0,
                                                    "winner_confirmed_at": 201.0}],
                                         "edges": {"start": {"at": 100.0, "clock_at_boundary": 60,
                                                             "walked_past": []}}}}]})
    flow = ReviewFlow(state)
    clip = flow.export_plan()["clips"][0]
    assert clip["boundary"]["machine_supports_start"] == "unknown"
    assert clip["boundary"]["start_why"] == \
        "the rows name earlier context (pre_pairing_context at 96.000, game_opening_context at 97.500), " \
        "which is a place to look, not a reading of this instant"
    item = next(i for i in flow.queue("all") if i["kind"] == "new_match")
    assert item["adjust"]["jump_to"] == 96.0
    assert item["adjust"]["jump_to_level"] == "pre_pairing_context"
    assert flow.opening_candidate(item["fingerprint"])["at"] == 96.0
    assert state.matches[0].start == 100.0, "a proposal never moves a boundary by being displayed"


# ------------------------------------------------------------ footage nobody marked, and what to say


def gap_project():
    """A recording with a stretch of tape no candidate covers, which is where a missed match would be."""
    return ProjectState.from_dict({"duration": 600.0, "video_path": "/recordings/bb9.mkv",
                                   "matches": [{"start": 100.0, "end": 200.0, "recovery": {
                                       "clip_end_at": 202.0,
                                       "clip_end_anchor": {"winner_confirmed_at": 201.0},
                                       "edges": {"start": {"at": 100.0, "clock_at_boundary": 60}}}}]})


def test_footage_nothing_claims_can_be_claimed_as_a_match_and_asks_its_own_questions(tmp_path):
    """A match can be watched for and still never reach an export, because nothing marked it.

    The unclaimed list used to be read-only: an operator could play a stretch, recognise a set, and the
    only thing left to do was edit the JSON by hand. Claiming makes a row, and a row is what the questions,
    the coverage and the jobs are all computed from -- so the footage moves from "unplaced" to "exportable,
    nobody has accepted it yet", which is exactly the honest state of a match somebody saw but the pipeline
    never found.
    """
    state = gap_project()
    flow = ReviewFlow(state)
    before = flow.export_plan()
    gaps = [row for row in flow.unresolved_gameplay() if row["kind"] == "gap"]
    assert [(row["from"], row["to"]) for row in gaps] == [(0.0, 100.0), (202.0, 600.0)]

    claimed = flow.claim_footage(300.0, 400.0, note="a set was played here and nothing marked it")
    plan = flow.export_plan()
    assert plan["coverage"]["candidates"]["seconds"] == \
        before["coverage"]["candidates"]["seconds"] + 100.0
    assert plan["counts"]["unplaced_seconds"] == before["counts"]["unplaced_seconds"] - 100.0
    assert len(plan["exportable"]) == len(before["exportable"]) + 1, "a claim has an export consequence"

    clip = [item for item in plan["clips"] if item["start"] == 300.0][0]
    assert clip["boundary"]["exportable"] is True
    assert clip["boundary"]["machine_supports_start"] == "unknown", "no machine evidence came with the row"
    assert clip["boundary"]["operator_accepted_boundary"] is False
    assert clip["boundary"]["independently_validated"] is False
    assert clip["boundary"]["claim_this_row_may_make"] == "exportable; boundary not accepted by anyone"
    # Saying where a stretch ends answers that end; the opening stays the question it is -- and a claim
    # with no end at all leaves both of them open.
    asked = [(item["kind"], item["segment"]["start"]) for item in flow.queue("open")
             if item["segment"]["start"] == 300.0]
    assert asked == [("new_match", 300.0)]
    open_ended = flow.claim_footage(500.0)
    later = [(item["kind"], item["segment"]["start"]) for item in flow.queue("open")
             if item["segment"]["start"] == 500.0]
    assert ("new_match", 500.0) in later and ("clip_end", 500.0) in later
    unfinished = [item for item in flow.export_plan()["clips"] if item["start"] == 500.0][0]
    assert unfinished["end"] == 600.0 and "closed at the end of the recording" in unfinished["basis"]
    assert unfinished["boundary"]["operator_accepted_boundary"] is False
    assert flow.undo(open_ended["question"])
    assert state.matches[-1].review["claim"]["note"] == "a set was played here and nothing marked it"

    flow.store().save(tmp_path / "claimed.tvh.json")
    again = ReviewFlow(ProjectState.from_dict(
        json.loads((tmp_path / "claimed.tvh.json").read_text(encoding="utf-8"))))
    assert [item.start for item in again.state.matches] == [100.0, 300.0]
    assert again.export_plan()["counts"]["unplaced_seconds"] == plan["counts"]["unplaced_seconds"]

    # A snapshot rolls the rows back to the moment before the claim it belongs to, so withdrawing the
    # first claim is what leaves the one row the project started with.
    assert flow.undo(claimed["question"]) is True
    assert [item.start for item in state.matches] == [100.0]
    assert flow.export_plan()["counts"]["unplaced_seconds"] == before["counts"]["unplaced_seconds"]


def test_claiming_footage_another_clip_already_covers_is_refused_because_that_is_a_split(tmp_path):
    """Two claims over one stretch is not a new match; it is a question about the row that is there."""
    flow = ReviewFlow(gap_project())
    flow.claim_footage(300.0, 400.0)
    with pytest.raises(review.InvalidAnswer, match="split or a merge"):
        flow.claim_footage(350.0)
    with pytest.raises(review.InvalidAnswer, match="is not after the match start"):
        flow.claim_footage(450.0, 440.0)
    assert [item.start for item in flow.state.matches] == [100.0, 300.0], "a refusal stores nothing"


def test_the_panel_offers_the_claim_and_the_dispositions_for_every_unclaimed_stretch():
    """Every gap has to be actionable from the list that shows it, or the list is a wall of text."""
    state = gap_project()
    panel = ReviewPanel(state)
    actions = panel.state_view()["footage_actions"]
    assert [row["from"] for row in actions] == [0.0, 202.0]
    labels = [control["label"] for control in actions[0]["controls"]]
    assert "Claim it as a match" in labels
    assert sum(1 for control in actions[0]["controls"]
               if control["action"].startswith("claim-interval:")) == 1
    assert sum(1 for control in actions[0]["controls"]
               if control["action"].startswith("dispose:")) == len(review.FOOTAGE_DISPOSITIONS) - 1, \
        "the opening itself is claimed as a row, not annotated around the edge"
    assert panel.press("dispose:not_play:0.0_100.0", note="lobby screen") == \
        "noted 0.000-100.000 as not_play; nothing about this project moved"
    assert state.matches[0].start == 100.0 and len(state.matches) == 1
    assert not [item for item in state.review["decisions"]]


def test_a_disposition_says_what_the_tape_was_without_touching_a_boundary_or_claiming_a_review(tmp_path):
    """A note about footage is evidence that somebody looked. It is not a reviewed boundary.

    The reference pass these records are for has to be comparable against the pipeline later, so a record
    carries the exact instant a watcher says a match opened, what was on screen there, and their words. What
    it must never do is move a boundary, mark a question answered, or make an unwatched stretch look
    inspected -- which is why the coverage report keeps the remainder in the same sentence.
    """
    state = gap_project()
    flow = ReviewFlow(state)
    opening = flow.annotate_footage("play_new_match", 450.0, 500.0, boundary=462.5,
                                   view="the players stop and the pair names", note="third set")
    assert opening["boundary_at"] == 462.5 and opening["is_not_a_boundary_decision"] is True
    assert flow.annotate_footage("not_play", 20.0, 60.0, note="a lobby screen")["kind"] == "not_play"
    assert flow.annotate_footage("unsure", 520.0)["kind"] == "unsure"
    assert [item.start for item in state.matches] == [100.0], "a record about footage adds no row"
    assert not state.review["decisions"], "and answers no question"
    plan = flow.export_plan()
    assert plan["counts"]["operator_accepted"] == 0
    assert all(clip["boundary"]["independently_validated"] is False for clip in plan["clips"])

    coverage = flow.footage_coverage()
    assert coverage["records"] == 3
    assert coverage["seconds_annotated"] == 40.0 + 50.0 + 80.0, "an open-ended note reaches the tape's end"
    assert coverage["seconds_unwatched"] == 600.0 - coverage["seconds_annotated"]
    assert coverage["whole_recording_annotated"] is False, "a third of a tape is not a reviewed tape"

    flow.store().save(tmp_path / "notes.tvh.json")
    again = ProjectState.from_dict(json.loads((tmp_path / "notes.tvh.json").read_text(encoding="utf-8")))
    assert len(again.review["annotations"]) == 3
    assert ReviewFlow(again).footage_coverage()["records"] == 3

    for kind, args, kwargs, why in (
            ("play_new_match", (10.0, 20.0), {}, "names the exact instant"),
            ("watched_it", (10.0, None), {}, "an unknown disposition"),
            ("not_play", (700.0, 800.0), {}, "past the end of the recording"),
            ("play_new_match", (10.0, 20.0), {"boundary": 35.0}, "opening outside the stretch")):
        with pytest.raises(review.InvalidAnswer):
            flow.annotate_footage(kind, *args, boundary=kwargs.get("boundary"), note=why)
    assert [record["sequence"] for record in again.review["annotations"]] == [1, 2, 3]


def test_the_footage_coverage_line_says_what_nobody_has_look_at_yet():
    """Unwatched is a number on the page, not an absence of one."""
    state = gap_project()
    view = ReviewPanel(state).state_view()
    assert view["footage_coverage"]["records"] == 0
    assert view["footage_coverage"]["seconds_unwatched"] == 600.0
    ReviewFlow(state).annotate_footage("unsure", 100.0, 200.0)
    after = ReviewPanel(state).state_view()["footage_coverage"]
    assert after["seconds_annotated"] == 100.0 and after["seconds_unwatched"] == 500.0


def test_a_claim_is_undone_by_its_own_question_and_leaves_the_end_answer_alone():
    """Two undos, two different things, and the row has to survive the smaller one.

    A claim records an end answer on the reviewer's behalf, and undoing *that* answer withdraws the answer and
    keeps the footage in the project -- the row was created by the claim, not by the answer. Undoing the
    claim's own question is what takes the row out again. Conflating the two would mean either an undo that
    destroys work the reviewer wanted kept, or an "undone" claim whose row silently stays behind.
    """
    state = gap_project()
    flow = ReviewFlow(state)
    claimed = flow.claim_footage(300.0, 400.0, note="a set was played here")
    end_question = next(item["fingerprint"] for item in flow.queue("all")
                        if item["kind"] == "clip_end" and item["segment"]["start"] == 300.0)
    assert flow.undo(end_question), "an answered question is undoable wherever it is standing"
    assert [item.start for item in state.matches] == [100.0, 300.0], "the row outlives its end answer"
    still_asked = [item for item in flow.queue("open") if item["segment"]["start"] == 300.0]
    assert {item["kind"] for item in still_asked} == {"new_match", "clip_end"}, \
        "withdrawing the end puts that question back on the list"

    assert flow.undo(claimed["question"]) is True
    assert [item.start for item in state.matches] == [100.0]


def test_undo_last_steps_back_over_a_claim_and_not_only_its_end_answer():
    """"Undo last" owes the reviewer the last *action*; a claim is the action, its end answer is detail."""
    flow = ReviewFlow(gap_project())
    flow.claim_footage(300.0, 400.0)
    assert len(flow.state.matches) == 2
    fingerprint = flow.undo_last()
    assert fingerprint and fingerprint.startswith("new_match|300.0|")
    assert flow.undo(fingerprint) is True
    assert [item.start for item in flow.state.matches] == [100.0]


def test_undo_last_skips_an_answer_it_already_withdrew():
    """A plain undo deletes the decision but leaves its log entry, so "Undo last" must not offer it twice.

    Found on the BB9 opening-proposal pass: a reviewer who extended one end and moved one opening, then
    pressed undo twice, was told "nothing to undo" on the second press while the move was still in force --
    the scan named the already-deleted end answer forever.
    """
    flow = ReviewFlow(gap_project())
    end_question = next(item["fingerprint"] for item in flow.queue("all")
                        if item["kind"] == "clip_end" and item["segment"]["start"] == 100.0)
    start_question = next(item["fingerprint"] for item in flow.queue("all")
                          if item["kind"] == "new_match" and item["segment"]["start"] == 100.0)
    flow.answer(end_question, "confirmed")
    flow.answer(start_question, "new_match")

    assert flow.undo_last() == start_question, "the most recent answer goes first"
    assert flow.undo(start_question) is True
    assert flow.undo_last() == end_question, \
        "the withdrawn answer is gone, so the one behind it is now the last thing to step back over"
    assert flow.undo(end_question) is True
    assert flow.undo_last() is None, "with nothing left in force, say so instead of naming a ghost"


def test_a_moved_opening_is_undoable_in_the_resumed_project():
    """The way back from a proposal has to survive the save that closes the review session."""
    flow = ReviewFlow(gap_project())
    start_question = next(item["fingerprint"] for item in flow.queue("all")
                          if item["kind"] == "new_match" and item["segment"]["start"] == 100.0)
    end_question = next(item["fingerprint"] for item in flow.queue("all")
                        if item["kind"] == "clip_end" and item["segment"]["start"] == 100.0)
    flow.answer(end_question, "confirmed")
    flow.move_start(start_question, 90.0)
    assert [item.start for item in flow.state.matches] == [90.0]

    resumed = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(flow.state.to_dict()))))
    assert [item.start for item in resumed.state.matches] == [90.0], "the move survived the save"
    fingerprint = resumed.undo_last()
    assert fingerprint == "new_match|90.0|90.0..200.0", \
        "the panel is asked to undo the move, so the move is what it names"
    assert resumed.undo(fingerprint) is True
    assert [item.start for item in resumed.state.matches] == [100.0], "the row goes back where it was"
    assert resumed.undo_last() != fingerprint, \
        "the withdrawn move is never offered again, whatever stands behind it"


def proposed_project():
    """One recovered set whose saved sequence supports an earlier opening, with no operator answer yet."""
    return ProjectState.from_dict({
        "duration": 600.0, "video_path": "/recordings/bb9.mkv",
        "sequence_source": "seq.json",
        "sequence_rows": [
            {"timestamp": 84.0, "state": "gameplay", "round_clock": 60, "round_call": 1,
             "player1": "Ana", "player2": "Bo", "evidence": "ROUND 1 banner, arena drawn"},
            {"timestamp": 89.0, "state": "gameplay", "round_clock": 58, "player1": "Ana",
             "player2": "Bo", "evidence": "fighters engaged, health spent"},
        ],
        "matches": [{"start": 90.0, "end": 200.0, "recovery": {
            "clip_end_at": 202.0, "clip_end_basis": "conclusion plus confirmation plus padding, applied once",
            "clip_end_anchor": {"winner_confirmed_at": 201.0},
            "orientation_required": True, "unnamed_sides": ["p1", "p2"],
            "edges": {"start": {"at": 90.0, "clock_at_boundary": 58, "scanned": [89.0]}}}}]})


def test_a_machine_proposal_moves_nothing_and_leaves_the_owners_question_open():
    """The defect this exists to prevent: a suggestion that reads as an answer wherever it is consumed.

    The first demonstration called ``move_start`` and stamped labels on the decision afterwards, so the
    question left the open queue and the plan reported a boundary a person had never approved. A proposal
    has to be visible without being authoritative, which means the queue, the row, the export plan and the
    acceptance counts all have to stay exactly as they were.
    """
    state = proposed_project()
    flow = ReviewFlow(state)
    before = flow.export_plan()
    open_before = {item["kind"] for item in flow.queue("open") if item["segment"]["start"] == 90.0}
    assert "new_match" in open_before

    record = flow.propose_opening(90.0, at=84.0, why="the round's own banner is at 84.0",
                                 evidence={"frame": "frame-0085.jpg"})
    after = flow.export_plan()

    assert record["status"] == review.PROPOSAL_PENDING and record["operator_decision"] is False
    assert [item.start for item in flow.state.matches] == [90.0], "a suggestion is not a boundary"
    assert {item["kind"] for item in flow.queue("open") if item["segment"]["start"] == 90.0} == open_before, \
        "the owner is still asked where the set opens, proposal and all"
    assert after["counts"]["covered_seconds_by_jobs"] == before["counts"]["covered_seconds_by_jobs"], \
        "and no job moved because the machine expressed a preference"
    assert after["counts"]["operator_accepted"] == 0
    assert after["counts"]["machine_proposals_pending"] == 1
    clip = after["clips"][0]
    assert clip["boundary"]["proposed_start"] == 84.0 and clip["start"] == 90.0, \
        "proposed and current are both on the page, never one instead of the other"
    assert "machine proposal" in clip["boundary"]["plan_basis"]
    assert (flow.state.matches[0].review.get("ends") is None), "no end basis from an opening suggestion"


def test_applying_a_proposal_is_the_operators_decision_and_undoing_it_restores_the_suggestion():
    state = proposed_project()
    flow = ReviewFlow(state)
    flow.propose_opening(90.0, at=84.0, why="the round's own banner is at 84.0")

    decision = flow.apply_proposal(90.0, "opening", note="agreed, the banner is the opening")
    assert [item.start for item in flow.state.matches] == [84.0], "applying is what moves the row"
    assert decision["status"] == review.DECIDED and decision.get("answer") == "other_adjust"
    assert "proposed_by" not in decision, "the decision is the reviewer's, with no machine field on it"
    assert not any(item["kind"] == "new_match" for item in flow.queue("open")
                   if item["segment"]["start"] == 84.0), "answering the question closes it"
    pending = flow.proposals(84.0)
    assert pending and pending[0]["status"] == review.PROPOSAL_APPLIED

    plan = flow.export_plan()
    assert plan["clips"][0]["start"] == 84.0 and plan["counts"]["machine_proposals_pending"] == 0

    resumed = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(flow.state.to_dict()))))
    assert [item.start for item in resumed.state.matches] == [84.0], "the applied proposal survives a save"
    assert resumed.undo_last()
    assert resumed.undo(resumed.undo_last()) is True
    assert [item.start for item in resumed.state.matches] == [90.0], "undo takes the boundary back"
    assert resumed.proposals(90.0)[0]["status"] == review.PROPOSAL_PENDING, \
        "and the suggestion is waiting again, because the only thing that had answered it was withdrawn"


def test_rejecting_a_proposal_changes_no_boundary_and_still_asks_the_owner():
    state = proposed_project()
    flow = ReviewFlow(state)
    flow.propose_opening(90.0, at=84.0, why="the round's own banner is at 84.0")
    record = flow.reject_proposal(90.0, "opening", note="the banner belongs to the match before it")
    assert record["status"] == review.PROPOSAL_REFUSED
    assert [item.start for item in flow.state.matches] == [90.0]
    assert any(item["kind"] == "new_match" for item in flow.queue("open"))
    assert flow.export_plan()["clips"][0]["start"] == 90.0


def test_an_automatic_proposal_cannot_be_filed_over_an_operators_answer():
    """A refresh that runs after an edit has to lose, every time, in the direction that keeps the edit."""
    state = proposed_project()
    flow = ReviewFlow(state)
    start_question = flow.start_fingerprint(90.0)
    flow.answer(start_question, "new_match")
    with pytest.raises(review.InvalidAnswer, match="already answered"):
        flow.propose_opening(90.0, at=84.0, why="a later pass thinks the banner is earlier")
    assert [item.start for item in flow.state.matches] == [90.0]

    moved = proposed_project()
    other = ReviewFlow(moved)
    other.propose_opening(90.0, at=84.0, why="banner")
    other.apply_proposal(90.0, "opening")
    assert [item.start for item in other.state.matches] == [84.0]
    with pytest.raises(review.InvalidAnswer, match="already answered"):
        other.propose_opening(84.0, at=80.0, why="a second pass, after the operator had spoken")


def test_an_unknown_sampling_width_is_reported_as_unknown_not_as_zero():
    """The claim that a proposal's interval is unbounded must not render as a perfect measurement."""
    state = proposed_project()
    flow = ReviewFlow(state)
    record = flow.propose_opening(90.0, at=84.0, why="character and stage continuity only")
    assert record["uncertainty_seconds"] is None

    # Read back through a resumed project, which is how the tab will see it.
    flowed = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(state.to_dict()))))
    question = next(i for i in flowed.queue("all") if i["kind"] == "new_match")
    label = flowed.item(question["fingerprint"], view="all")["proposal"]
    assert label["uncertainty_text"] == "unknown"
    assert "unknown" in label["summary"]
    # The instants themselves are printed to three decimals, so the check is that no *width* was faked.
    assert "+-0" not in label["summary"] and "width unknown" in label["summary"]


def test_a_proposed_end_is_disclosed_without_becoming_the_governing_end():
    """A victory camera is a publishing choice; saying so may not quietly change the clip's authority."""
    state = proposed_project()
    flow = ReviewFlow(state)
    record = flow.propose_end(90.0, 205.0, why="the victory close-up runs to here",
                              purpose="presentation", uncertainty_seconds=None)
    clip = flow.export_plan()["clips"][0]
    assert record["purpose"] == "presentation"
    assert clip["boundary"]["proposed_end"] == 205.0
    assert clip["end"] == 202.0, "the padded machine end still governs"
    assert clip["boundary"]["machine_supports_end"] is not None
    row = next(view for _match, view in flow.rows())
    resolved = review.resolve_clip_end(dict(row))
    assert resolved["basis"] == "conclusion plus confirmation plus padding, applied once"
    assert resolved["end_governs"] == "machine evidence"
    assert resolved["end_proposal"]["purpose"] == "presentation"


def two_proposals_project():
    """One row carrying both an opening and an end suggestion, which is where selection gets tested."""
    return ProjectState.from_dict({
        "duration": 600.0, "video_path": "/recordings/bb9.mkv",
        "matches": [
            {"start": 0.0, "end": 90.0, "recovery": {
                "clip_end_at": 92.0, "clip_end_basis": "conclusion plus padding, applied once",
                "edges": {"start": {"at": 0.0, "clock_at_boundary": 60, "scanned": []}}}},
            {"start": 100.0, "end": 200.0, "recovery": {
                "clip_end_at": 202.0, "clip_end_basis": "conclusion plus padding, applied once",
                "clip_end_anchor": {"winner_confirmed_at": 201.0},
                "edges": {"start": {"at": 100.0, "clock_at_boundary": 58, "scanned": [99.0]}}}}]})


@pytest.mark.parametrize("first,second", [("opening", "end"), ("end", "opening")])
def test_a_cards_apply_button_acts_on_the_proposal_that_card_shows(first, second):
    """The reproduced defect: the end card's Apply accepted the row's opening proposal and moved the start.

    Both filing orders are checked because the old code took "the first pending proposal on the row", which
    is whichever the machine happened to write first -- so one order looked right and the other silently
    changed a boundary the reviewer was not looking at.
    """
    flow = ReviewFlow(two_proposals_project())
    for kind in (first, second):
        if kind == "opening":
            flow.propose_opening(100.0, at=94.0, why="the round's own banner")
        else:
            flow.propose_end(100.0, 205.0, why="the victory close-up", purpose="presentation")
    panel = ReviewPanel(flow.state)
    end_q = next(i for i in flow.queue("open")
                 if i["kind"] == "clip_end" and i["segment"]["start"] == 100.0)
    panel.press("select:" + end_q["fingerprint"])
    card = panel.state_view()["question"]
    assert card["kind"] == "clip_end"
    assert card["proposal"]["kind"] == "end" and card["proposal"]["at"] == 205.0, \
        "the end card shows the end suggestion"

    message = panel.press("proposal:apply")
    assert message.startswith("applied the end proposal"), message
    starts = [item.start for item in flow.state.matches]
    assert starts == [0.0, 100.0], "applying an end must not move a start"
    assert flow.export_plan()["clips"][1]["end"] == 205.0, "and it must move the end it named"
    opening = next(item for item in flow.proposals(100.0) if item["kind"] == "opening")
    assert opening["status"] == review.PROPOSAL_PENDING, "the untouched proposal is still waiting"


def test_the_opening_card_applies_the_opening_and_leaves_the_end_alone():
    flow = ReviewFlow(two_proposals_project())
    flow.propose_opening(100.0, at=94.0, why="the round's own banner")
    flow.propose_end(100.0, 205.0, why="the victory close-up", purpose="presentation")
    panel = ReviewPanel(flow.state)
    start_q = next(i for i in flow.queue("open")
                   if i["kind"] == "new_match" and i["segment"]["start"] == 100.0)
    panel.press("select:" + start_q["fingerprint"])
    card = panel.state_view()["question"]
    assert card["proposal"]["kind"] == "opening"
    panel.press("proposal:reject")
    statuses = {item["kind"]: item["status"] for item in flow.proposals(100.0)}
    assert statuses == {"opening": review.PROPOSAL_REFUSED, "end": review.PROPOSAL_PENDING}, \
        "rejecting from the opening card refuses only the opening"
    assert [item.start for item in flow.state.matches] == [0.0, 100.0]


def test_a_row_starting_at_zero_can_still_act_on_its_own_proposal():
    """`if start` reads 0.0 as absent, which strands the first clip in any recording."""
    flow = ReviewFlow(two_proposals_project())
    flow.propose_end(0.0, 95.0, why="the closing pose runs past the padded end", purpose="presentation")
    panel = ReviewPanel(flow.state)
    end_q = next(i for i in flow.queue("open") if i["kind"] == "clip_end" and i["segment"]["start"] == 0.0)
    panel.press("select:" + end_q["fingerprint"])
    assert panel.state_view()["question"]["proposal"]["at"] == 95.0
    message = panel.press("proposal:apply")
    assert message.startswith("applied the end proposal"), message
    assert flow.export_plan()["clips"][0]["end"] == 95.0, "the zero-start row is not skipped as absent"


def test_rejecting_then_re_proposing_leaves_the_history_and_can_be_applied():
    """A rejection record must not be the reason a fresh suggestion cannot be taken.

    The old resolution took the first proposal of the kind, so after a rejection the row's newest pending
    suggestion was invisible to apply_proposal -- the queue showed it and the controller refused it.
    """
    flow = ReviewFlow(proposed_project())
    old = flow.propose_opening(90.0, at=84.0, why="a banner read")
    flow.reject_proposal(90.0, "opening", note="that frame is the match before it")
    fresh = flow.propose_opening(90.0, at=80.0, why="a better-read cue", evidence={"frame": "frame-0081.jpg"})
    assert fresh["proposal_id"] != old["proposal_id"], "each suggestion is addressable on its own"

    filed = {item["proposal_id"]: item for item in flow.proposals(90.0)}
    assert filed[old["proposal_id"]]["status"] == review.PROPOSAL_REFUSED
    assert filed[old["proposal_id"]]["at"] == 84.0, "the refused record keeps its instant"

    shown = flow.item(flow.start_fingerprint(90.0), view="all")["proposal"]
    assert shown["proposal_id"] == fresh["proposal_id"], "the card shows the live suggestion"
    decision = flow.apply_proposal(90.0, "opening", proposal_id=shown["proposal_id"])
    assert [item.start for item in flow.state.matches] == [80.0]
    assert decision["status"] == review.DECIDED

    after = {item["proposal_id"]: item for item in flow.proposals(80.0)}
    assert after[old["proposal_id"]]["status"] == review.PROPOSAL_REFUSED, "history survives the apply"
    assert after[fresh["proposal_id"]]["status"] == review.PROPOSAL_APPLIED
    assert after[fresh["proposal_id"]]["evidence"] == {"frame": "frame-0081.jpg"}

    resumed = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(flow.state.to_dict()))))
    ids = {item["proposal_id"]: item["status"] for item in resumed.proposals(80.0)}
    assert ids == {old["proposal_id"]: review.PROPOSAL_REFUSED,
                   fresh["proposal_id"]: review.PROPOSAL_APPLIED}, "ids survive a save and resume"


def test_applying_a_settled_proposal_by_id_refuses_instead_of_substituting():
    """A card left open after the suggestion it showed was dealt with must fail loudly."""
    flow = ReviewFlow(proposed_project())
    record = flow.propose_opening(90.0, at=84.0, why="a banner read")
    flow.reject_proposal(90.0, "opening", proposal_id=record["proposal_id"])
    flow.propose_opening(90.0, at=80.0, why="a better-read cue")
    with pytest.raises(review.InvalidAnswer, match="was already rejected"):
        flow.apply_proposal(90.0, "opening", proposal_id=record["proposal_id"])
    assert [item.start for item in flow.state.matches] == [90.0], "refusing changed nothing"


def test_rejecting_twice_names_the_live_one_and_not_the_oldest_record():
    flow = ReviewFlow(proposed_project())
    first = flow.propose_opening(90.0, at=84.0, why="a banner read")
    flow.reject_proposal(90.0, "opening")
    second = flow.propose_opening(90.0, at=80.0, why="a better-read cue")
    flow.reject_proposal(90.0, "opening")
    statuses = {item["proposal_id"]: item["status"] for item in flow.proposals(90.0)}
    assert statuses == {first["proposal_id"]: review.PROPOSAL_REFUSED,
                        second["proposal_id"]: review.PROPOSAL_REFUSED}
    with pytest.raises(review.InvalidAnswer, match="still waiting"):
        flow.reject_proposal(90.0, "opening")


def test_pressing_a_stale_card_refuses_instead_of_applying_the_replacement():
    """The button acts on what was drawn, not on whatever is live when it is clicked.

    Reproduced on the real tab: the card said 785.785, a second pass superseded it with 770.0, and pressing
    Apply moved the row to 770.0 -- the number the reviewer never saw. The panel now remembers the proposal
    identity it rendered and refuses by name when that record no longer waits.
    """
    flow = ReviewFlow(gap_project())
    panel = ReviewPanel(flow.state)
    question = next(item for item in flow.queue("all") if item["kind"] == "new_match")
    panel.press("select:" + question["fingerprint"])
    panel.state_view()                                     # what app._review_refresh does after a press
    flow.propose_opening(100.0, at=94.0, why="the round's own banner")
    panel.state_view()
    assert panel.rendered and panel.rendered["proposal_id"], "the card has an identity to be stale about"
    flow.propose_opening(100.0, at=90.0, why="a second pass displaced it")

    message = panel.press("proposal:apply")                # no redraw in between: the card is stale
    panel.state_view()
    assert "superseded" in message, message
    assert [item.start for item in flow.state.matches] == [100.0], \
        "a refusal must not move the boundary it refused to guess at"
    refreshed = panel.state_view()["question"]["proposal"]
    assert refreshed["at"] == 90.0, "and the redrawn card offers the suggestion that is actually live"


def test_a_press_without_a_rendered_card_acts_on_nothing():
    flow = ReviewFlow(gap_project())
    panel = ReviewPanel(flow.state)
    question = next(item for item in flow.queue("all") if item["kind"] == "new_match")
    panel.press("select:" + question["fingerprint"])
    panel.state_view()
    assert panel.rendered is None, "nothing was offered, so nothing was rendered"
    message = panel.press("proposal:apply")
    panel.state_view()
    assert "no proposal" in message
    assert [item.start for item in flow.state.matches] == [100.0]


def test_the_tab_shows_the_proposal_and_offers_apply_and_reject():
    state = proposed_project()
    flow = ReviewFlow(state)
    flow.propose_opening(90.0, at=84.0, why="the round's own banner is at 84.0",
                         uncertainty_seconds=1.0)
    panel = ReviewPanel(state)
    question = next(item for item in flow.queue("open") if item["kind"] == "new_match")
    # "select" is a prefixed verb, not a two-argument call: press(action, note) takes the note second, so
    # press("select", fp) silently selects nothing and shows the first card instead. Writing it correctly is
    # what makes this test exercise the row it names.
    panel.press("select:" + question["fingerprint"])
    assert panel.selected == question["fingerprint"], "the selection has to take, or the card is a stranger"
    shown = flow.item(question["fingerprint"], view="all")
    assert shown["proposal"]["at"] == 84.0 and shown["proposal"]["current"] == 90.0
    assert shown["proposal"]["uncertainty_text"] == "+-1.000 s"
    actions = [control["action"] for control in panel.state_view()["question"]["controls"]]
    assert "proposal:apply" in actions and "proposal:reject" in actions
    assert panel.press("proposal:apply").startswith("applied the opening proposal")
    assert [item.start for item in flow.state.matches] == [84.0]


def test_the_tab_undo_targets_the_question_the_reviewer_is_standing_on():
    """The panel's undo verb takes no argument, so the selection is the whole instruction.

    Worth pinning after the claim path landed: a claimed row answers its end on the reviewer's behalf, which
    drops that answer out of the open view, and undoing the *opening* is undoing the claim. Standing on the
    answered question in the answered view is the only way back to the end alone.
    """
    state = gap_project()
    panel = ReviewPanel(state)
    flow = panel.flow
    flow.claim_footage(300.0, 400.0)
    end_question = next(item["fingerprint"] for item in flow.queue("all")
                        if item["kind"] == "clip_end" and item["segment"]["start"] == 300.0)
    panel.press("view:answered")
    panel.press("select:" + end_question)
    assert panel.selected == end_question
    panel.press("undo")
    assert [item.start for item in state.matches] == [100.0, 300.0], "the row stays"
    assert not [decision for decision in flow.store().state["decisions"]
                if decision.get("kind") == "clip_end" and decision.get("status") == "confirmed"
                and abs((decision.get("segment_start") or 0.0) - 300.0) < 1e-6]


# ------------------------------------------------------ a boundary is stored at the precision it arrived at


def fractional_project():
    """Two clips whose edges agree to the microsecond, with the stretch between them offered as a gap.

    The instants are BB9's own: source timestamps arrive to six decimals, and the gap the plan offers ends
    exactly where the next candidate opens. A claim that rounds either bound has moved a boundary the
    operator never moved, and the exporter then holds the result for an overlap nobody wrote down.
    """
    return ProjectState.from_dict({"duration": 6928.421136, "video_path": "/recordings/bb9.mkv",
                                   "matches": [
                                       {"start": 53.052997, "end": 570.57, "recovery": {
                                           "clip_end_at": 574.57197,
                                           "clip_end_anchor": {"winner_confirmed_at": 573.5},
                                           "edges": {"start": {"at": 53.052997,
                                                               "clock_at_boundary": 60}}}},
                                       {"start": 790.789958, "end": 1371.37, "recovery": {
                                           "clip_end_at": 1374.370928,
                                           "clip_end_anchor": {"winner_confirmed_at": 1373.0},
                                           "edges": {"start": {"at": 790.789958,
                                                               "clock_at_boundary": 60}}}}]})


def test_claiming_a_gap_that_touches_the_next_clip_exports_instead_of_being_held(tmp_path):
    """The BB9 case: [574.57197, 790.789958] touches the next opening exactly, and must export.

    Rounding the end for storage made it 790.790 -- forty-two millionths of a second past a neighbour the
    operator had been offered as the boundary -- and the plan held the new clip for crossing it, printing
    both numbers as 790.790 so the disagreement looked like a bug in the reviewer's own arithmetic. The row
    existed and its footage reached no job, which is the worst kind of success to report.
    """
    state = fractional_project()
    flow = ReviewFlow(state)
    before = flow.export_plan()
    gap = next(row for row in flow.unresolved_gameplay()
               if row["kind"] == "gap" and row["from"] == 574.57197)
    assert gap["to"] == 790.789958

    claimed = flow.claim_footage(gap["from"], gap["to"], note="a set was played in here")
    assert claimed["claimed_from"] == 574.57197 and claimed["claimed_to"] == 790.789958, \
        "the row is stored at the instants it was offered, not at their display rounding"

    after = flow.export_plan()
    clip = next(item for item in after["clips"] if item["start"] == 574.57197)
    assert clip["end"] == 790.789958
    assert clip["held"] is False and clip["reasons"] == [], clip["reasons"]
    assert clip["boundary"]["exportable"] is True
    job_delta = after["coverage"]["exportable_jobs"]["seconds"] \
        - before["coverage"]["exportable_jobs"]["seconds"]
    assert job_delta == pytest.approx(216.217988, abs=1e-6), \
        "the job covers exactly the stretch that was unplaced"
    assert after["counts"]["unplaced_seconds"] == pytest.approx(
        before["counts"]["unplaced_seconds"] - 216.217988, abs=1e-6)
    assert not [row for row in after["coverage"]["uncovered_by_exportable_jobs"]
                if row["from"] == 574.57197], "the gap the plan offers is the gap the plan now covers"

    # And the claim reaches an export job through the file, not just the in-memory plan.
    flow.store().save(tmp_path / "claimed.tvh.json")
    again = ReviewFlow(ProjectState.from_dict(
        json.loads((tmp_path / "claimed.tvh.json").read_text(encoding="utf-8"))))
    plan = again.export_plan()
    job = next(item for item in plan["exportable"] if item["start"] == 574.57197)
    assert job["end"] == 790.789958
    assert [clip["start"] for clip in plan["clips"]] == [53.052997, 574.57197, 790.789958]
    assert not _duplicate_fingerprints(again)


def _duplicate_fingerprints(flow):
    seen, dupes = set(), []
    for item in flow.queue("all"):
        if item["fingerprint"] in seen:
            dupes.append(item["fingerprint"])
        seen.add(item["fingerprint"])
    return dupes


def test_undoing_a_fractional_claim_restores_the_project_and_both_coverage_accounts():
    state = fractional_project()
    flow = ReviewFlow(state)
    before = flow.export_plan()
    claimed = flow.claim_footage(574.57197, 790.789958, note="claimable, and undoable")
    assert flow.undo(claimed["question"]) is True
    after = flow.export_plan()
    assert [item.start for item in state.matches] == [53.052997, 790.789958]
    assert after["coverage"]["exportable_jobs"]["seconds"] \
        == before["coverage"]["exportable_jobs"]["seconds"]
    assert after["counts"]["unplaced_seconds"] == before["counts"]["unplaced_seconds"]
    assert not _duplicate_fingerprints(flow)


@pytest.mark.parametrize("value, expected", [
    (790.789958, 790.789958),          # a source instant survives untouched
    (790.7899584, 790.789958),         # past the microsecond is noise, and is dropped
    (790.7899585, 790.789959),         # ... by rounding, not by truncation
    (-0.0000004, None),                # still before the start of the tape
    (1e-9, 0.0),                       # a nanosecond is not a boundary anyone can aim at
    (1.5, 1.5),
])
def test_the_stored_precision_is_the_source_precision_whatever_the_sign(value, expected):
    """One policy, both directions: a boundary is stored to the microsecond or not at all.

    Sub-frame noise is what the microsecond cut discards, and it is discarded the same way on either side of
    zero -- a negative instant is refused outright rather than rounded up to the start of the tape, because
    "the clip begins a third of a microsecond before the recording" is not a thing an operator meant.
    """
    if expected is None:
        with pytest.raises(review.InvalidAnswer):
            review.validate_boundary_value(start=None, value=value, duration=5000.0)
        return
    stored, _note = review.validate_boundary_value(start=None, value=value, duration=5000.0)
    assert stored == expected


def test_a_message_about_two_nearly_equal_instants_shows_both_to_the_microsecond():
    """The sentence has to carry the disagreement it is reporting.

    Two disagreements print alike at the millisecond, and a reviewer told "790.790 runs past 790.790" learns
    nothing except that the tool is confused. So the digits widen when, and only when, they have to.
    """
    assert review.instant_text(790.789958) == "790.790"
    assert review.instant_text(790.789958, against=790.790000) == "790.789958"
    assert review.instant_text(790.790000, against=790.789958) == "790.790000"
    assert review.span_text(0.000042) == "0.000042"
    assert review.span_text(2.5) == "2.500"


def test_a_real_overlap_is_still_a_real_overlap_however_small_it_is_named():
    """The fix is about manufactured disagreement, never about tolerating collision.

    Forty-two microseconds of genuine overlap is still footage two clips claim, and the exporter still holds
    it. What changes is that the hold now says which two instants disagree: printed to the millisecond they
    are the same number, which is how a rounding bug came to look like a decision the operator had to make.
    """
    state = ProjectState.from_dict({"duration": 6928.421136, "video_path": "/recordings/bb9.mkv",
                                    "matches": [
                                        {"start": 53.052997, "end": 570.57, "recovery": {
                                            "clip_end_at": 790.79,
                                            "clip_end_anchor": {"winner_confirmed_at": 573.5}}},
                                        {"start": 790.789958, "end": 1371.37, "recovery": {
                                            "clip_end_at": 1374.370928,
                                            "clip_end_anchor": {"winner_confirmed_at": 1373.0}}}]})
    flow = ReviewFlow(state)
    plan = flow.export_plan()
    held = next(item for item in plan["held"] if item["start"] == 53.052997)
    assert any("790.790000" in reason and "790.789958" in reason for reason in held["reasons"]), \
        held["reasons"]
    # And the arithmetic is not thrown away on the way to the page either: coverage totals keep the same
    # microseconds the intervals were built from, so the two accounts of the tape still add up.
    assert plan["counts"]["unplaced_seconds"] == pytest.approx(6344.840166, abs=1e-6)
    assert plan["counts"]["exportable"] == 1, "the crossing clip is held, not quietly cut short"


def test_the_panel_offers_the_gap_at_its_real_instants_not_at_a_rounded_abbreviation():
    """The action string is the interface between the list and the controller.

    A button that sends "574.572" has already moved the boundary before the controller is told where it
    was, and no amount of care downstream notices. The offered action carries the digits the plan offered.
    """
    state = fractional_project()
    actions = ReviewPanel(state).state_view()["footage_actions"]
    row = next(row for row in actions if row["from"] == 574.57197)
    assert row["to"] == 790.789958
    claim = next(control for control in row["controls"] if control["label"] == "Claim it as a match")
    assert claim["action"] == "claim-interval:574.57197_790.789958"
    assert all(control["action"].endswith("574.57197_790.789958") for control in row["controls"]), \
        "a disposition has to describe the same stretch the button would claim"


# ---------------------------------------------------------------- a rejected claim must have never happened


REJECTIONS = [
    pytest.param((220.0, 350.0), "passes the next candidate's opening", id="end-crosses-next-row"),
    pytest.param((100.0, 150.0), "a row already opens at", id="exact-duplicate-opening"),
    pytest.param((150.0, None), "split or a merge", id="opening-inside-another-clip"),
    pytest.param((450.0, 440.0), "is not after the match start", id="reversed-stretch"),
    pytest.param((-5.0, None), "before the start of the recording", id="negative-instant"),
    pytest.param(("soon", None), "number of seconds", id="not-a-number"),
    pytest.param((float("inf"), None), "real instant", id="infinity"),
    pytest.param((float("nan"), None), "real instant", id="not-a-number-float-nan"),
]


@pytest.mark.parametrize("args, expect", REJECTIONS)
def test_a_refused_claim_leaves_the_project_exactly_as_it_was(args, expect):
    """A refusal is a no-op, and "it raised" is not what that means.

    The first cut appended the row and only then asked the end the question the exporter would ask later, so
    the refusal landed after the mutation: the count had changed, the coverage had changed, an export job
    existed for footage nobody had approved, and there was no undo entry to take any of it back -- because
    undo steps over answers, and a claim that never completed was never one. So the comparison here is the
    whole mutable project, not the row count.
    """
    state = two_row_project()
    flow = ReviewFlow(state)
    before = _whole_project(state)
    snapshots_before = len((state.review or {}).get("snapshots") or {})
    with pytest.raises(review.InvalidAnswer, match=expect):
        flow.claim_footage(*args, note="a rejection must leave nothing behind")
    assert _whole_project(state) == before
    assert len((state.review or {}).get("snapshots") or {}) == snapshots_before, \
        "and it files no undo snapshot for an action that did not happen"
    plan = flow.export_plan()
    assert [item["start"] for item in plan["clips"]] == [100.0, 300.0]
    assert not _duplicate_fingerprints(flow)
    # The refusal is not sticky: the same project still takes a legitimate claim afterwards.
    if args[0] == 220.0:
        again = flow.claim_footage(220.0, 290.0, note="the same row, ending before its neighbour")
        assert again["claimed_to"] == 290.0


def two_row_project():
    return ProjectState.from_dict({"duration": 500.0, "video_path": "/recordings/bb9.mkv",
                                   "matches": [{"start": 100.0, "end": 200.0, "recovery": {
                                       "clip_end_at": 202.0}},
                                       {"start": 300.0, "end": 400.0, "recovery": {"clip_end_at": 402.0}}]})


def _whole_project(state):
    """Everything an action can touch on a project, in one value a rejection has to leave alone."""
    plan = ReviewFlow(state).export_plan()
    return {"matches": [match.to_dict() for match in state.matches],
            "review": json.loads(json.dumps(state.review)),
            "counts": plan["counts"],
            "jobs": [(clip["start"], clip["end"]) for clip in plan["exportable"]],
            "jobs_seconds": plan["coverage"]["exportable_jobs"]["seconds"],
            "unplaced_seconds": plan["counts"]["unplaced_seconds"]}


def test_a_claim_running_past_the_end_is_clamped_and_says_so_rather_than_refused():
    """"Stop at the end of the tape" is a clear intention with a wrong number: keep the intention.

    The difference from the rejections above is that nothing else's footage is involved -- the operator
    cannot claim off the end of the recording, and the clamp is recorded on the row so a later reader sees
    the number was moved and by what authority.
    """
    state = two_row_project()
    flow = ReviewFlow(state)
    claimed = flow.claim_footage(450.0, 900.0, note="to the end of the tape")
    assert claimed["claimed_to"] == 500.0
    row = next(match for match in state.matches if match.start == 450.0)
    assert "clamped to the recording length 500.000" in row.review["claim"]["clamped"]

# ------------------------------------------- readings that genuinely disagree, and readings that do not


def one_row_project(walked_evidence, *, walked_at=1587.585917, candidate=1588.586916,
                    cue_at=1538.536919, clock_at_boundary=60):
    """One candidate clip whose saved edge carries a settled cue and exactly one walked-past frame.

    The walked frame is described the way the classifier describes it: state "gameplay", plus the prose
    that says what the arena was showing. What that prose says is the whole variable under test.
    """
    segment = {
        "start": candidate, "end": 2137.132888,
        "recovery": {
            "clip_end_at": 2137.132888,
            "clip_end_anchor": {"winner_confirmed_at": 2136.0},
            "games": [{"start": candidate, "end": 2137.132888, "winner_confirmed_at": 2136.0}],
            "edges": {"start": {
                "at": candidate,
                "clock_at_boundary": clock_at_boundary,
                "settled_by": {"at": cue_at, "kind": "cue", "state": "controller_prompt"},
                "walked_past": [{"at": walked_at, "state": "gameplay", "clock": None,
                                 "because": "clock unread", "evidence": walked_evidence}],
            }},
        },
    }
    return ProjectState.from_dict({"duration": 6928.421136, "video_path": "/recordings/bb9.mkv",
                                   "matches": [segment]})


INTRO = "Arena with fighters in pre-round stance, P1/P2 tags, 'READY' announcement overlay"
PAUSED = "Live match scene with round timer (60) and idle fighters; Player 2 in-match pause menu is open"
PLAYING = "Active combat with timer 58, health bars dropping and a Counter popup"


def test_an_intro_frame_behind_a_boundary_is_not_a_rival_to_the_cue():
    """The interpretation error this pass exists to remove, stated as a rule.

    A settled cue 50 s back and a ROUND/READY frame 1 s ahead are not two answers to where the set opened:
    they are the same opening seen twice. Reporting that as a physical disagreement sent a reviewer to look
    for a contradiction that the evidence never contained, so the dispute needs a reading that actually
    claims the round was being fought.
    """
    state = one_row_project(INTRO)
    clip = ReviewFlow(state).export_plan()["clips"][0]["boundary"]
    assert clip["machine_supports_start"] == "supported"
    assert clip["start_readings_conflict"] is False
    assert "does not agree with itself" not in clip["start_why"]
    opening = [item for item in clip["start_readings"] if item["at"] == 1587.585917]
    assert opening and opening[0]["phase"] == "round_opening"
    assert opening[0]["proves_play_was_running"] is False
    assert "round was being announced" in opening[0]["says"]


def test_a_pause_menu_is_not_evidence_that_play_was_running():
    """A clock behind an open menu is a stopped clock, and a stopped clock dates nothing.

    2722.720 on this recording reads "live match scene, round timer 60" in its first clause and "pause menu
    is open" in its second, and the walk only ever carried the state word forward. The frame shows nobody
    playing, so it cannot be the sighting that contradicts a boundary.
    """
    state = one_row_project(PAUSED, walked_at=2722.719857, candidate=2771.768854,
                            cue_at=2719.716857)
    clip = ReviewFlow(state).export_plan()["clips"][0]["boundary"]
    assert clip["start_readings_conflict"] is False
    paused = [item for item in clip["start_readings"] if item["at"] == 2722.719857]
    assert paused and paused[0]["phase"] == "paused"
    assert paused[0]["proves_play_was_running"] is False
    assert clip["machine_supports_start"] == "supported"


def test_a_frame_of_live_play_behind_a_boundary_is_still_a_real_dispute():
    """The correction must not become a blanket refusal to report disagreement.

    Swap the intro frame for one showing the round actually being fought 1 s before the candidate, and the
    settled cue 50 s back no longer accounts for it: one of the two is wrong about where this opened, and
    that is worth a card, a window and a press.
    """
    state = one_row_project(PLAYING)
    clip = ReviewFlow(state).export_plan()["clips"][0]["boundary"]
    assert clip["start_readings_conflict"] is True
    assert "does not agree with itself" in clip["start_why"]
    assert "1538.537" in clip["start_why"] and "1587.586" in clip["start_why"]
    assert "neither cue decides the other" in clip["start_why"]
    claims = [item for item in clip["start_readings"] if item["about"] == "opening"]
    assert [item["at"] for item in claims] == [1538.536919, 1587.585917]
    assert [item["proves_play_was_running"] for item in claims] == [False, True]


def test_the_disputed_frames_are_one_press_away_and_an_undisputed_one_is_not():
    """The window has to contain every reading in dispute, and offer nothing when none is."""
    panel = ReviewPanel(one_row_project(PLAYING))
    item = panel.state_view()["question"]
    dispute = next(control for control in item["controls"] if control["action"] == "play:dispute")
    assert dispute["enabled"] is True, item["boundary"]["readings"]
    window = item["boundary"]["dispute_window"]
    assert window["from"] <= 1538.536919 and window["to"] >= 1588.586916
    assert dispute["label"] == "Play the frames in dispute (%.0f s)" % window["seconds"]
    message = panel.press("play:dispute")
    assert panel.playback["from"] <= 1538.536919 and panel.playback["to"] >= 1588.586916
    assert "not which cue to believe" in message
    assert panel.flow.state.matches[0].start == 1588.586916, "looking is not moving"

    quiet = ReviewPanel(one_row_project(INTRO)).state_view()["question"]
    disabled = next(control for control in quiet["controls"] if control["action"] == "play:dispute")
    assert disabled["enabled"] is False
    assert quiet["boundary"]["dispute_window"] is None
    silent = ReviewPanel(one_row_project(INTRO))
    silent.press("select:" + silent.state_view()["question"]["fingerprint"])
    refused = silent.press("play:dispute")
    assert "no wider span" in refused, "pressing it anyway refuses in a message, not an exception: " + refused


def test_the_dispute_count_is_reported_apart_from_support_contradiction_and_unknown():
    """A fourth figure, because 'the pipeline disagrees with itself' is none of the other three."""
    for evidence, expected in ((PLAYING, 1), (INTRO, 0), (PAUSED, 0)):
        state = one_row_project(evidence)
        summary = ReviewPanel(state).state_view()["coverage"]["boundary_summary"]
        assert summary["start_readings_conflict"] == expected, evidence
        assert summary["start_supported"] == 1 and summary["start_contradicted"] == 0
        assert summary["operator_accepted"] == 0 and summary["independently_validated"] == 0
        assert "do not agree with each other" in summary["note"]


def test_reading_bb9_rows_by_phase_leaves_no_false_disputes():
    """The three openings the review found, on the real saved project, after the fix.

    Asserted as a set rather than a count: what changed is *why* each of the three is reported the way it
    is. 790.790 is still contradicted, honestly, by a clock reading 58 at its own frame; 1588.587 and
    2771.769 lose the dispute they were never in, and nothing else moved.
    """
    project = json.loads((Path(__file__).resolve().parents[1] / "build/work/bb9"
                          / "bb9-operator-review-2026-11-07/operator/bb9-review.tvh.json")
                         .read_text(encoding="utf-8"))
    flow = ReviewFlow(ProjectState.from_dict(project))
    plan = flow.export_plan()
    assert [clip["start"] for clip in plan["clips"] if clip["boundary"]["start_readings_conflict"]] == []
    contradicted = {round(clip["start"], 6): clip["boundary"] for clip in plan["clips"]
                    if clip["boundary"]["machine_supports_start"] == "contradicted"}
    assert sorted(contradicted) == [790.789958, 5495.489711, 6486.479659]
    assert all("clock already reads" in clip["start_why"] for clip in contradicted.values())
    summary = ReviewPanel(flow.state).state_view()["coverage"]["boundary_summary"]
    assert summary["start_readings_conflict"] == 0
    assert summary["start_supported"] == 7 and summary["start_contradicted"] == 3
    assert plan["counts"]["operator_accepted"] == 0
    assert plan["counts"]["independently_validated"] == 0
