"""The review panel's consumer paths, exercised without tkinter.

Everything here runs against real :class:`ProjectState`/``MatchSegment`` objects and the real
``boundary_review`` journal, because the gap this closes was structural: the model helpers existed and
passed their own tests, and nothing the application called used them. So these are consumer regressions
-- decisions through the app's own serialization path, the export plan the app builds its jobs from, the
rows a grouping answer moves, and the panel state the widgets render.
"""

import copy
import json

import pytest

from tekken_vod_helper import boundary_review as review
from tekken_vod_helper.boundary_review_flow import ReviewFlow, ReviewPanel
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
