"""The BB9 review path: import losslessness, side-aware characters, one padding, reviewable decisions.

These tests exist because the acceptance gate for this work is boundary correctness, and the code that
decides what a reviewer sees must be checkable without a display. Each block is written against a case that
would have been silently wrong before: a round-trip that discarded evidence, an unnamed observed side that
a remembered default replaced, a clip that ended before the reading naming its winner, and a decision that
survived the collapse of the evidence it rested on.
"""

import copy
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_review as review
from tekken_vod_helper.models import MatchSegment, ProjectState

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "build/work/bb9/outcome-recovery-post-review-2026-11-06/public-plan.json"
requires_saved_recovery = pytest.mark.skipif(not PLAN.is_file(),
                                            reason="saved BB9 recovery lives in build/work")

SEGMENT = {
    "start": 110.0, "end": 200.0, "player1": "", "player2": "",
    "recovery": {"orientation_required": True, "unnamed_sides": ["p1", "p2"],
                 "sides": {"p1": {"player": "", "character": "", "source": ""},
                           "p2": {"player": "", "character": "", "source": ""}},
                 "games": [{"start": 110.0, "characters_observed": ["Azucena", "Nina"],
                            "characters_side_bound": False, "conclusion_event_at": 198.0,
                            "winner_confirmed_at": 205.0}],
                 "clip_end_at": 202.0, "clip_end_anchor": {"winner_confirmed_at": 205.0},
                 "clip_end_basis": "conclusion plus confirmation plus padding, applied once",
                 "characters_side_bound": False, "alternative_groupings": [], "unresolved_claims": [],
                 "boundary_certain": True, "footage_complete": True},
    "review": {},
}


# ------------------------------------------------------------------ characters: names, sides, provenance

def test_an_unnamed_observation_is_not_evidence_about_the_player_being_asked_for():
    """The review's counterexample: ('', 'Jin') must not be handed to Alice as if the tape said so."""
    state = ProjectState(player_characters={"alice": "King"})
    assert state.character_for_player("Alice", observed=[("", "Jin")]) == "King"
    resolved, source = state.character_and_source_for_player("Alice", observed=[("", "Jin")])
    assert (resolved, source) == ("King", "remembered"), "the answer is a habit, and must be labelled so"
    # And the nameless evidence is still there, just not attributable to a player.
    assert state.observed_side_characters([("", "Jin"), ("Bob", "Noctis")]) == ["Jin"]


def test_a_named_observation_still_beats_a_remembered_default():
    state = ProjectState(player_characters={"buk": "Hwoarang"})
    assert state.character_and_source_for_player("Buk", observed=[("Buk", "Jin")]) == ("Jin", "observed")


def test_bracket_players_are_not_attached_to_sides_without_an_orientation():
    result = review.resolve_characters(SEGMENT, ["Alice", "Bob"], orientation=None, remembered={})
    assert result["questions"] and result["questions"][0]["kind"] == "orientation"
    assert result["export_ready"] is False
    assert all(side["player"] == "" for side in result["sides"].values())


def test_orientation_decides_which_side_gets_which_name_and_keeps_both_characters_distinct():
    segment = copy.deepcopy(SEGMENT)
    segment["recovery"]["sides"]["p1"] = {"player": "", "character": "Jin", "source": "observed"}
    as_listed = review.resolve_characters(segment, ["Alice", "Bob"], orientation="as-listed")
    swapped = review.resolve_characters(segment, ["Alice", "Bob"], orientation="swapped")
    assert as_listed["sides"]["p1"]["player"] == "Alice"
    assert swapped["sides"]["p1"]["player"] == "Bob"
    # Observed evidence on a side stays with the side across an orientation change, and keeps its provenance.
    assert as_listed["sides"]["p1"]["character"] == swapped["sides"]["p1"]["character"] == "Jin"
    assert as_listed["sides"]["p1"]["character_source"] == "observed"


def test_a_remembered_default_never_replaces_observed_side_evidence():
    segment = copy.deepcopy(SEGMENT)
    segment["recovery"]["sides"]["p2"] = {"player": "", "character": "Noctis", "source": "observed"}
    result = review.resolve_characters(segment, ["Alice", "Bob"], orientation="as-listed",
                                      remembered={"bob": "King"})
    assert result["sides"]["p2"]["character"] == "Noctis"
    assert result["sides"]["p2"]["character_source"] == "observed"


def test_a_gap_may_be_filled_by_memory_and_is_marked_as_such():
    result = review.resolve_characters(SEGMENT, ["Alice", "Bob"], orientation="as-listed",
                                      remembered={"alice": "King"})
    assert result["sides"]["p1"]["character"] == "King"
    assert result["sides"]["p1"]["character_source"] == "remembered"
    assert result["sides"]["p2"]["character"] == "" and result["sides"]["p2"]["character_source"] == ""


# -------------------------------------------------------------------------------------- clip ends, once

def test_a_clip_that_stops_before_the_confirmation_that_names_the_winner_says_so():
    """The synthetic case, treated as a specification rather than a BB9 claim."""
    clip = review.resolve_clip_end(SEGMENT)
    assert clip["end"] == 202.0
    assert clip["warnings"], "silence here is the defect"
    assert any("does not contain" in warning for warning in clip["warnings"])
    assert {"from": 202.0, "to": 205.0, "what": "winner confirmation"} in clip["excluded_gameplay"]
    # Padding also reaches past the recorded set end. That is extra footage being claimed, not gameplay
    # being lost, and the two must not share a list.
    assert clip["included_past_recorded_end"] == [
        {"from": 200.0, "to": 202.0,
         "what": "footage past the recorded set end that this clip now includes"}]
    assert clip["skipped_reason"] is None


def test_a_clip_that_includes_its_confirmation_is_clean_of_the_loss_warning():
    good = copy.deepcopy(SEGMENT)
    good["recovery"]["clip_end_at"] = 207.0
    clip = review.resolve_clip_end(good)
    assert clip["excluded_gameplay"] == [], "the confirmation is inside the clip, so nothing is lost"
    assert not any("does not contain" in warning for warning in clip["warnings"])
    assert clip["included_past_recorded_end"], "and the extra seven seconds are still disclosed"


def test_the_next_mark_is_offered_but_never_used_silently():
    following = {"start": 260.0}
    broken = copy.deepcopy(SEGMENT)
    broken["recovery"].pop("clip_end_at")
    broken["end"] = None
    clip = review.resolve_clip_end(broken, following=following)
    assert clip["end"] is None
    assert clip["skipped_reason"] == "unresolved end"
    assert clip["suggested_end"] == 260.0 and clip["suggestion_needs_approval"] is True
    assert any("refusing to default to the next mark" in warning for warning in clip["warnings"])


def test_a_human_end_overrides_the_machine_without_losing_the_record_of_why():
    decided = copy.deepcopy(SEGMENT)
    decided["review"] = {"ends": {"end": 208.0}}
    clip = review.resolve_clip_end(decided)
    assert clip["end"] == 208.0
    assert clip["basis"] == "human decision"
    assert clip["padding_applied_once"] is False, "a padded number is not what governs now"


def test_a_clip_running_past_the_end_of_the_recording_is_clamped_and_reported():
    beyond = copy.deepcopy(SEGMENT)
    beyond["recovery"]["clip_end_at"] = 7000.0
    clip = review.resolve_clip_end(beyond, duration=6928.421136)
    assert clip["end"] == pytest.approx(6928.421136)
    assert any("exceeds the recording length" in warning for warning in clip["warnings"])


# ---------------------------------------------------------------------------- decisions: save, undo, undo, undo

def _project():
    return {"duration": 6928.421136, "matches": [copy.deepcopy(SEGMENT)]}


def test_a_question_already_answered_is_not_asked_again():
    project = _project()
    store = review.ReviewStore(project)
    item = review.review_queue(project)[0]
    assert store.ask(item) is True
    store.decide(item, "new_match")
    after = review.review_queue(project)
    assert [i for i in after if i["fingerprint"] == item["fingerprint"]] == [], \
        "a resolved question must leave the queue, not merely stop being asked"
    assert store.ask(item) is False, "and the same item must never be answerable twice"


# ------------------------------------------------------------- "Unsure" is a state, not an answer

def test_unsure_defers_rather_than_answering():
    """The reproduction this fixes: ``unsure`` used to be stored as ``answered`` and vanish.

    The intended behaviour has three parts that all have to hold at once -- it must not nag (it leaves the
    open queue), it must be resumable (it is listed in its own deferred view and can be replaced by a real
    answer), and it must be consequential (it governs nothing, so what the recording left unresolved stays
    visibly unresolved instead of quietly counting as settled).
    """
    project = _project()
    store = review.ReviewStore(project)
    item = next(i for i in review.review_queue(project) if i["kind"] == "orientation")
    decision = store.decide(item, "unsure")
    assert decision["status"] == review.DEFERRED
    assert store.ask(item) is False, "deferred must not nag"
    assert [i for i in review.review_queue(project) if i["kind"] == "orientation"] == []
    resumable = review.review_queue(project, view="deferred")
    assert [i for i in resumable if i["fingerprint"] == item["fingerprint"]], \
        "a deferred question has to be findable again or it is a dead end, not a deferral"
    assert resumable[0]["state"] == "deferred" and resumable[0]["decision"]["answer"] == "unsure"
    assert store.deferred()[0]["fingerprint"] == item["fingerprint"]
    assert item["fingerprint"] in [d["fingerprint"] for d in store.unresolved()]
    # Governs nothing: the segment is still where the recording left it.
    assert project["matches"][0]["review"]["orientation"] == review.UNRESOLVED
    # And a later decision replaces the deferral instead of colliding with it.
    store.decide(item, "swapped")
    assert project["matches"][0]["review"]["orientation"] == "swapped"
    assert store.deferred() == []
    assert [i for i in review.review_queue(project, view="answered") if i["kind"] == "orientation"]


def test_answering_with_a_value_the_question_never_offered_is_refused():
    """The clip-end answer used to be run through a numeric parse, so "210" was a legal answer object."""
    project = _project()
    store = review.ReviewStore(project)
    end_item = next(i for i in review.review_queue(project) if i["kind"] == "clip_end")
    with pytest.raises(review.InvalidAnswer):
        store.decide(end_item, "210")
    start_item = next(i for i in review.review_queue(project) if i["kind"] == "new_match")
    with pytest.raises(review.InvalidAnswer):
        store.decide(start_item, "swapped")
    with pytest.raises(review.InvalidAnswer):
        store.decide(end_item, "extend")  # no target, no number, nothing applied
    assert project["matches"][0].get("review", {}).get("ends") is None


def test_extend_and_shorten_record_a_number_and_move_the_clip_end():
    project = _project()
    store = review.ReviewStore(project)
    item = next(i for i in review.review_queue(project) if i["kind"] == "clip_end")
    decision = store.decide(item, "extend", delta_seconds=10.0)
    assert decision["end_at"] == 212.0 and decision["moved_seconds"] == 10.0
    assert review.resolve_clip_end(project["matches"][0])["end"] == 212.0
    assert project["matches"][0]["review"]["ends"]["basis"] == "human decision at review"
    shortened = store.decide(item, "shorten", end_at=204.0)
    assert shortened["end_at"] == 204.0
    assert review.resolve_clip_end(project["matches"][0])["end"] == 204.0
    # A value the footage cannot hold is refused before anything is written.
    with pytest.raises(review.InvalidAnswer):
        store.decide(item, "shorten", end_at=5.0)
    assert review.resolve_clip_end(project["matches"][0])["end"] == 204.0
    # Past the end of the tape the intent is unambiguous and only the number is wrong: clamp and say so.
    clamped = store.decide(item, "extend", end_at=99999.0)
    assert clamped["end_at"] == pytest.approx(6928.421136)
    assert "clamped" in clamped["note"]
    assert review.resolve_clip_end(project["matches"][0])["end"] == pytest.approx(6928.421136)


def test_a_boundary_value_is_checked_against_the_tape_and_the_neighbours():
    assert review.validate_boundary_value(start=10.0, value=20.0, duration=None) == (20.0, None)
    clamped, note = review.validate_boundary_value(start=10.0, value=900.0, duration=100.0)
    assert clamped == 100.0 and "clamped to the recording length" in note
    with pytest.raises(review.InvalidAnswer):
        review.validate_boundary_value(start=10.0, value=10.0)
    with pytest.raises(review.InvalidAnswer):
        review.validate_boundary_value(start=10.0, value=300.0, following_start=260.0)
    with pytest.raises(review.InvalidAnswer):
        review.validate_boundary_value(start=10.0, value="soon")


# ---------------------------------------------------------------------------- decisions: save, undo, resume

def _orientation(project):
    return next(i for i in review.review_queue(project) if i["kind"] == "orientation")


def _clip_end(project):
    return next(i for i in review.review_queue(project) if i["kind"] == "clip_end")


def test_a_decision_is_persisted_undone_and_persisted_again_without_drift(tmp_path):
    project = _project()
    store = review.ReviewStore(project)
    store.decide(_orientation(project), "swapped")
    path = store.save(tmp_path / "project.tvh.json")
    reloaded = review.load_project(path)
    # The outcome lands on the segment; the decision itself is recorded once, at project level, so a
    # reviewer can see what was decided without two copies drifting apart.
    assert reloaded["matches"][0]["review"]["orientation"] == "swapped"
    assert reloaded["review"]["decisions"][0]["answer"] == "swapped"
    # The machine's suggestion is untouched by the human choice: both are still there and separate.
    assert reloaded["matches"][0]["recovery"]["orientation_required"] is True
    reopened = review.ReviewStore(reloaded)
    # Reopening the saved project must not re-ask what it already settled -- and undoing that decision has
    # to put the question straight back, or "resume" would silently strand an unanswered clip.
    assert [i for i in review.review_queue(reloaded) if i["kind"] == "orientation"] == []
    assert reopened.undo(reloaded["review"]["decisions"][0]["fingerprint"]) is True
    assert [i for i in review.review_queue(reloaded) if i["kind"] == "orientation"], \
        "an undone decision must reopen its question, not leave the clip unreviewed"
    assert reopened.save(path) == path
    assert json.loads(Path(path).read_text())["review"]["decisions"] == []


def test_a_decision_records_the_suggestion_it_was_made_against():
    project = _project()
    store = review.ReviewStore(project)
    item = _orientation(project)
    decision = store.decide(item, "swapped")
    assert decision["against"]["clip_end_at"] == 202.0
    assert decision["fingerprint"] == store.question_fingerprint(item)


def test_undo_withdraws_the_applied_end_and_not_just_the_record():
    """The reproduction this fixes: after undo the project still exported a clip ending at 210.

    An undo test that only checks the question came back is not testing the undo. The consequence is the
    part a reviewer cannot see again, so the export consequence is asserted on both sides of the undo.
    """
    project = _project()
    store = review.ReviewStore(project)
    item = _clip_end(project)
    store.decide(item, "extend", delta_seconds=10.0)
    assert review.resolve_clip_end(project["matches"][0])["end"] == 212.0
    assert review.export_plan(project)["held"] == [], "212 contains the confirmation, so it exports"
    fingerprint = store.question_fingerprint(item)
    assert store.undo(fingerprint) is True
    assert review.resolve_clip_end(project["matches"][0])["end"] == 202.0, \
        "the withdrawn answer must stop being the authority on where the clip stops"
    held = review.export_plan(project)["held"]
    assert held and held[0]["index"] == 0, "and the clip goes back to being held by the machine evidence"
    assert [i for i in review.review_queue(project) if i["kind"] == "clip_end"], \
        "the question is open again"
    assert store.undo(fingerprint) is False, "undoing twice is not a no-op worth reporting as success"


def test_changing_the_evidence_invalidates_downstream_rather_than_keeping_it_quietly():
    """A deferred answer has no consequence to withdraw, so invalidation must not pretend otherwise."""
    project = _project()
    project["matches"][0]["recovery"]["alternative_groupings"] = [{"junction": 150.0, "games": [1, 2]}]
    store = review.ReviewStore(project)
    items = {item["kind"]: item for item in review.review_queue(project)}
    assert set(items) == {"new_match", "clip_end", "orientation", "membership"}
    store.decide(items["clip_end"], "extend", end_at=212.0)
    for kind in ("new_match", "orientation", "membership"):
        store.decide(items[kind], "unsure" if kind == "membership" else items[kind]["options"][0])
    assert review.resolve_clip_end(project["matches"][0])["end"] == 212.0
    touched = store.invalidate(110.0, "set regrouped: a game moved to the neighbour")
    assert set(touched) == {store.question_fingerprint(items[kind]) for kind in
                            ("new_match", "clip_end", "orientation")}
    for kind in ("new_match", "clip_end", "orientation"):
        record = store.decision(store.question_fingerprint(items[kind]))
        assert record["status"] == review.INVALIDATED
        assert "regrouped" in record["invalidated_reason"]
    deferred = store.decision(store.question_fingerprint(items["membership"]))
    assert deferred["status"] == review.DEFERRED, "nothing was decided, so there is nothing to invalidate"
    assert review.resolve_clip_end(project["matches"][0])["end"] == 202.0, \
        "an invalidated answer must stop holding the boundary it was given about"
    assert len(store.unresolved()) == len(store.state["decisions"]), "every one of them stays visible"
    assert [i for i in review.review_queue(project) if i["kind"] == "clip_end"], \
        "and the withdrawn question is asked again"


def test_reopening_a_project_never_leaves_an_invalidated_answer_in_charge(tmp_path):
    """A hold has to survive Save, or closing the app quietly releases it."""
    project = _project()
    store = review.ReviewStore(project)
    store.decide(_clip_end(project), "extend", end_at=212.0)
    store.invalidate(110.0, "the conclusion reading moved")
    path = store.save(tmp_path / "held.tvh.json")
    again = review.load_project(path)
    assert review.resolve_clip_end(again["matches"][0])["end"] == 202.0
    assert review.export_plan(again)["held"], "the reopened project still holds the clip"


# ------------------------------------------------------------------------------------- queue honesty

def test_every_segment_is_asked_about_both_of_its_boundaries():
    """The saved BB9 import queued 17 questions and not one of them was about a start."""
    project = {"duration": 6928.421136, "matches": [copy.deepcopy(SEGMENT), copy.deepcopy(SEGMENT)]}
    project["matches"][1]["start"] = 260.0
    kinds = [item["kind"] for item in review.review_queue(project)]
    assert kinds.count("new_match") == 2 and kinds.count("clip_end") == 2
    assert kinds.count("orientation") == 2


def test_confident_clips_are_still_queued_because_a_flag_cannot_prove_the_absence_of_errors():
    project = _project()
    with_confident = review.review_queue(project)
    assert any(item["kind"] == "orientation" for item in with_confident)
    clean = copy.deepcopy(SEGMENT)
    clean["recovery"].update({"orientation_required": False, "unnamed_sides": [],
                              # A padded end that stays inside the recorded end: nothing disclosed, nothing
                              # lost. An earlier version of this fixture used 207.0 and so was genuinely
                              # flagged -- padding past the recorded end is a disclosure.
                              "clip_end_at": 199.0, "boundary_certain": True})
    clean["recovery"]["clip_end_anchor"] = {"winner_confirmed_at": 198.0}
    clean["recovery"]["sides"] = {"p1": {"player": "Alice", "character": "", "source": ""},
                                  "p2": {"player": "Bob", "character": "", "source": ""}}
    only_clean = review.review_queue({"duration": 1000.0, "matches": [clean]},
                                     include_confident=False)
    assert only_clean == [], "with nothing flagged and confident items excluded, the queue is empty"
    confident = review.review_queue({"duration": 1000.0, "matches": [clean]}, include_confident=True)
    assert [item["kind"] for item in confident] == ["new_match", "clip_end"], \
        "an unflagged clip is still asked about, once per boundary"
    assert "detector's own prediction" in confident[0]["why_flagged"]
    assert "non-fighting frame" in confident[1]["why_flagged"]


def test_an_unresolved_export_consequence_is_visible_on_the_item():
    project = _project()
    item = _clip_end(project)
    assert item["export_consequence"]["excluded_gameplay"], "the reviewer must see what the clip loses"
    assert item["export_consequence"]["held"] is True, "and whether it is being held for that"
    assert item["costs_inference"] is False, "looking at saved footage must not spend anything"
    assert "reaches_target_does_not_prove_set_ended" in item["evidence"]
    assert item["evidence"]["clock_at_60_does_not_prove_first_round"] is True


def test_questions_are_about_footage_and_offers_include_an_escape_hatch():
    project = _project()
    item = _orientation(project)
    assert item["ask"].endswith("?")
    assert "unsure" in item["options"] and "Unsure" in item["labels"]
    assert "why_flagged" in item and item["why_flagged"]
    # The old span ran from the set's start to its end, i.e. the whole clip again. Context belongs to the
    # frame being asked about, so it stays short and centred.
    assert item["context"]["play_from"] < item["segment"]["start"]
    assert item["context"]["play_to"] < (item["segment"]["end"] or 0)
    assert item["context"]["play_to"] - item["context"]["play_from"] == pytest.approx(
        2 * item["context"]["seconds_each_side"])
    end_item = _clip_end(project)
    assert end_item["context"]["boundary_at"] == pytest.approx(202.0)
    assert end_item["context"]["play_from"] < 202.0 < end_item["context"]["play_to"]


def test_a_start_the_saved_clock_calls_live_is_asked_about_generically():
    """No timestamp-specific offsets: the clock only ever falls inside a round, so a 58 bounds the opening."""
    late = copy.deepcopy(SEGMENT)
    late["recovery"]["edges"] = {"start": {
        "at": 790.79, "clock_at_boundary": 58, "scanned": [670.79, 790.79],
        "settled_by": {"at": 748.748, "kind": "cue", "state": "controller_prompt"},
        "walked_past": [{"at": 785.785, "state": "gameplay", "clock": None, "because": "clock unread"}]}}
    opening = review.start_opening(late["recovery"])
    assert opening["late_by_at_least_seconds"] == 2.0
    assert opening["earliest_supported_opening"] == 748.748
    assert opening["clock_bound_opening"] == pytest.approx(788.79)
    assert len(opening["contradicts"]) == 2, "the clock and the walked-past row are separate facts"
    assert "round-intro animation" in opening["contradicts"][1], "a gameplay row bounds, never names"
    question = review.review_queue({"duration": 6928.0, "matches": [late]})[0]
    assert question["kind"] == "new_match"
    assert "clock already reads 58" in question["why_flagged"]
    assert question["boundary"]["late_by_at_least_seconds"] == 2.0
    assert question["boundary"]["earliest_supported_opening"] == 748.748
    assert question["evidence"]["start_clock_at_boundary"] == 58
    # A start the saved evidence says nothing about is not silently called wrong.
    quiet = review.start_opening(copy.deepcopy(SEGMENT)["recovery"])
    assert quiet["flagged"] is False and quiet["contradicts"] == []


def test_a_walk_that_found_nothing_to_name_reports_the_gap_instead_of_guessing():
    thin = copy.deepcopy(SEGMENT)
    thin["recovery"]["edges"] = {"start": {"at": 790.79, "clock_at_boundary": None,
                                           "scanned": [780.79, 790.79], "settled_by": None,
                                           "walked_past": [{"at": 785.785, "state": "gameplay",
                                                             "clock": None, "because": "clock unread"}]}}
    opening = review.start_opening(thin["recovery"])
    assert opening["earliest_supported_opening"] is None
    assert opening["late_by_at_least_seconds"] is None
    assert opening["flagged"] is True
    assert "785.785" in opening["contradicts"][0]


# ------------------------------------------------------------------------ the export plan and its holds

def test_a_blocking_clip_is_held_with_a_reason_and_a_way_back_in():
    project = _project()
    plan = review.export_plan(project)
    assert plan["counts"]["exportable"] == 0 and plan["counts"]["held"] == 1
    held = plan["held"][0]
    assert held["end"] == 202.0, "the hold records the boundary it is about, not a blank"
    assert held["review_kind"] == "clip_end"
    assert any("does not contain" in reason for reason in held["reasons"])


def test_confirming_the_end_releases_the_hold_and_keeps_the_loss_on_the_record():
    """Approving a defect is a decision, so it must be visible after the fact and not before."""
    project = _project()
    store = review.ReviewStore(project)
    store.decide(_clip_end(project), "confirmed")
    plan = review.export_plan(project)
    assert plan["counts"]["exportable"] == 1 and plan["held"] == []
    clip = plan["clips"][0]
    assert clip["approved"] is True and clip["severity"] == "disclosure"
    assert clip["excluded_gameplay"] == [{"from": 202.0, "to": 205.0, "what": "winner confirmation"}]
    assert any("approved by a human decision" in warning for warning in
               review.resolve_clip_end(project["matches"][0])["warnings"])


def test_an_unresolved_end_is_held_and_never_defaults_to_the_tape_or_the_next_mark():
    broken = copy.deepcopy(SEGMENT)
    broken["recovery"].pop("clip_end_at")
    broken["end"] = None
    project = {"duration": 1000.0, "matches": [broken, {"start": 400.0, "end": 500.0}]}
    clip = review.resolve_clip_end(broken, following=project["matches"][1], duration=1000.0)
    assert clip["end"] is None and clip["held"] is True
    plan = review.export_plan(project)
    assert [row["end"] for row in plan["held"]] == [None]
    assert plan["held"][0]["reasons"], "a hold nobody can explain is a dead end"
    assert plan["counts"]["exportable"] == 1, "the other clip still goes out"


def test_a_hand_marked_row_keeps_the_operators_own_conventions():
    """No recovery block means no machine claim, so the app's legacy fallbacks stay legal."""
    handmade = {"duration": 1000.0, "matches": [{"start": 10.0}, {"start": 300.0}]}
    plan = review.export_plan(handmade)
    assert [row["end"] for row in plan["clips"]] == [300.0, 1000.0]
    assert plan["held"] == []
    assert any("next match's opening" in reason for reason in plan["clips"][0]["reasons"])
    assert any("end of the recording" in reason for reason in plan["clips"][1]["reasons"])


def test_play_between_two_clips_is_reported_as_unplaced():
    """Every stretch of the tape is accounted for, not just what falls between two neighbours.

    The first version of this arithmetic walked ``zip(clips, clips[1:])`` and stopped. On BB9 that hid the
    first 53.053 s and the last 57.559 s -- 110.612 s of recording that no clip claims, in a report whose
    headline said 1676.693 s -- and the shape of the answer ("footage between two clips") invited the reader
    to call all of it missed play. Each row now says which edge it is and whether saved evidence puts play
    inside it, so "unknown" and "known and unclaimed" stay different claims.
    """
    project = {"duration": 1000.0, "matches": [
        {"start": 10.0, "end": 100.0, "recovery": {"clip_end_at": 100.0, "games": [{"start": 10.0,
                                                                                     "end": 100.0}]}},
        {"start": 260.0, "end": 300.0, "recovery": {"clip_end_at": 300.0,
                                                    "games": [{"start": 260.0, "end": 300.0}]}}]}
    plan = review.export_plan(project)
    assert [(row["edge"], row["from"], row["to"], row["seconds"])
            for row in plan["unplaced_play"]] == [("leading", 0.0, 10.0, 10.0),
                                                  ("between", 100.0, 260.0, 160.0),
                                                  ("trailing", 300.0, 1000.0, 700.0)]
    assert plan["counts"]["unplaced_seconds"] == 870.0
    assert plan["coverage"]["candidates"]["seconds"] == 130.0
    assert all(row["gameplay_seen_in_saved_evidence"] is False for row in plan["unplaced_play"])
    # A candidate whose own game reaches past the clip makes the gap a known loss, not a mystery.
    project["matches"][0]["recovery"]["games"].append({"start": 110.0, "end": 250.0})
    moved = review.export_plan(project)["unplaced_play"]
    between = [row for row in moved if row["edge"] == "between"]
    assert between and between[0]["gameplay_seen_in_saved_evidence"] is True
    assert between[0]["gameplay_open_at"] == 110.0


def test_overlapping_and_unsorted_candidates_are_merged_not_double_counted():
    """A merge leaves one row covering another, and pairwise subtraction counted the overlap twice."""
    project = {"duration": 100.0, "matches": [
        {"start": 50.0, "end": 80.0, "recovery": {"clip_end_at": 80.0}},
        {"start": 10.0, "end": 60.0, "recovery": {"clip_end_at": 60.0}}]}
    plan = review.export_plan(project)
    assert plan["coverage"]["candidates"]["intervals"] == [{"from": 10.0, "to": 80.0, "seconds": 70.0}]
    # The two rows overlap, and the earlier-in-list row runs past the next candidate's opening, so it is
    # held: the jobs account stops where the renderable clip ends even though the candidates cover to 80.
    assert [clip["held"] for clip in plan["clips"]] == [True, False]
    assert plan["coverage"]["exportable_jobs"]["intervals"] == [{"from": 10.0, "to": 60.0,
                                                                "seconds": 50.0}]
    assert [(row["edge"], row["from"], row["to"]) for row in plan["unplaced_play"]] == [
        ("leading", 0.0, 10.0), ("trailing", 60.0, 100.0)]
    assert [(row["edge"], row["from"], row["to"])
            for row in plan["coverage"]["uncovered_by_candidates"]] == [("leading", 0.0, 10.0),
                                                                        ("trailing", 80.0, 100.0)]


def test_a_clip_with_no_usable_bounds_is_reported_as_invalid_not_as_zero_length():
    """A missing end is a hole in the accounting; silently dropping it makes the tape look covered."""
    project = {"duration": 100.0, "matches": [{"start": 10.0, "recovery": {}},
                                              {"start": 40.0, "end": 20.0, "recovery": {}}]}
    plan = review.export_plan(project)
    assert {row["index"] for row in plan["coverage"]["invalid_bounds"]} == {1}
    assert plan["coverage"]["candidates"]["intervals"] == [{"from": 10.0, "to": 40.0, "seconds": 30.0}]


def test_a_held_clip_covers_nothing_at_export_time():
    """The two accounts must differ by exactly what is on hold, or the report predicts the wrong folder."""
    project = {"duration": 100.0, "matches": [
        {"start": 10.0, "end": 20.0, "recovery": {"clip_end_at": 20.0,
                                                  "clip_end_anchor": {"winner_confirmed_at": 21.0}}},
        {"start": 30.0, "end": 40.0, "recovery": {"clip_end_at": 40.0}}]}
    plan = review.export_plan(project)
    assert plan["coverage"]["candidates"]["seconds"] == 20.0
    assert plan["coverage"]["exportable_jobs"]["seconds"] == 10.0
    assert plan["coverage"]["seconds_between_the_two_accounts"] == 10.0
    assert plan["coverage"]["held_intervals_not_rendered"]["intervals"] == [
        {"from": 10.0, "to": 20.0, "seconds": 10.0}]
    held_zone = [row for row in plan["unplaced_play"] if row["from"] <= 15.0 <= row["to"]]
    assert held_zone, "a clip nobody answered for has to show up as unclaimed footage"


def test_a_known_clipped_opening_is_exportable_but_not_accepted():
    """'Can be exported' and 'the boundary is right' are different questions, and one flag cannot answer both."""
    project = {"duration": 6928.421136, "matches": [{
        "start": 790.79, "end": 1371.37,
        "recovery": {"clip_end_at": 1374.37, "clip_end_anchor": {"winner_confirmed_at": 1372.37},
                     "edges": {"start": {"at": 790.79, "clock_at_boundary": 58,
                                         "walked_past": []}}}}]}
    plan = review.export_plan(project)
    clip = plan["clips"][0]
    assert clip["held"] is False, "the end contains its confirmation, so the clip is not held"
    assert clip["opening"]["flagged"] is True
    assert clip["opening"]["late_by_at_least_seconds"] == 2.0
    assert clip["opening_review_required"] is True and clip["boundary_accepted"] is False
    assert plan["counts"]["openings_needing_review"] == 1
    assert plan["counts"]["exportable"] == 1
    assert plan["exportable_is_not_accepted"] is True
    store = review.ReviewStore(project)
    question = next(item for item in review.review_queue(project) if item["kind"] == "new_match")
    with pytest.raises(review.InvalidAnswer):
        store.decide(question, "accept_partial")          # no reason, no approval
    store.decide(question, "accept_partial", note="opening is inside a round; the set is still watchable")
    approved = review.export_plan(project)
    assert approved["clips"][0]["opening"]["approved_partial"] is True
    assert approved["clips"][0]["boundary_accepted"] is False, \
        "an approved partial is exportable and disclosed, never silently re-labelled correct: agreeing to "\
        "ship the loss is not evidence that the opening is right"
    assert approved["clips"][0]["opening_review_required"] is False
    assert approved["counts"]["openings_needing_review"] == 0


def test_unknown_names_do_not_hold_a_clip():
    """A nameless match is still a match: labels and boundaries are different questions."""
    project = _project()
    store = review.ReviewStore(project)
    store.decide(_clip_end(project), "extend", end_at=212.0)
    plan = review.export_plan(project)
    assert plan["counts"]["exportable"] == 1
    assert plan["clips"][0]["names_known"] is False
    assert plan["clips"][0]["orientation"] == review.UNRESOLVED


# --------------------------------------------------------------------- re-import keeps a person's work

def test_reimporting_a_moved_start_reconciles_instead_of_duplicating_the_footage():
    """The reproduction this fixes: start 110 -> 109 on re-import left both rows in the project."""
    project = _project()
    store = review.ReviewStore(project)
    store.decide(_orientation(project), "swapped")
    moved = copy.deepcopy(SEGMENT)
    moved["start"] = 109.0
    moved["recovery"]["clip_end_at"] = 201.5
    merged = review.merge_into_project(project, [moved])
    assert [row["start"] for row in merged["matches"]] == [109.0], "one row for one recording"
    # The answer keeps the fingerprint it was given under -- the boundary it described is now a different
    # question, which is exactly why it has to be visible as invalidated rather than rewritten as new.
    record = store.decisions_for(109.0)[0]
    assert record["status"] == review.INVALIDATED, "the boundary the answer was about moved"
    assert "moved" in record["invalidated_reason"]
    assert record["segment_start"] == 109.0 and record["identity"] == {
        "start_was": 110.0, "now": 109.0, "matched_by": "moved_start"}
    assert merged["matches"][0]["review"]["orientation"] == review.UNRESOLVED
    assert [i for i in review.review_queue(merged) if i["kind"] == "orientation"], \
        "the moved boundary is asked about again from scratch"
    outcome = merged["review"]["reconciliations"][-1]["outcomes"][0]
    assert outcome["outcome"] == "moved_start" and outcome["matched_start"] == 110.0


def test_reimport_keeps_names_a_person_typed_and_says_that_it_did():
    project = _project()
    project["matches"][0]["player1"] = "Slowly"
    project["matches"][0]["review"]["field_edits"] = {"player1": {"source": "human", "value": "Slowly"}}
    again = copy.deepcopy(SEGMENT)
    again["recovery"]["clip_end_at"] = 203.0
    merged = review.merge_into_project(project, [again])
    kept = merged["matches"][0]
    assert kept["player1"] == "Slowly"
    assert kept["review"]["field_edits"]["player1"]["source"] == "human"
    invalidated = [d for d in merged["review"]["decisions"] if d["status"] == review.INVALIDATED]
    assert merged["review"]["reconciliations"][-1]["outcomes"][0]["invalidated_because"] == ["clip_end_at"]
    assert invalidated == [] or all("clip_end_at" in d["invalidated_reason"] for d in invalidated)


def test_genuinely_contending_candidates_are_both_kept_and_flagged():
    """Two intervals over the same footage is a split or a merge, and only a person can say which."""
    project = _project()
    half = copy.deepcopy(SEGMENT)
    half["start"] = 111.0
    half["end"] = 201.0
    merged = review.merge_into_project(project, [half])
    outcome = merged["review"]["reconciliations"][-1]["outcomes"][0]
    assert outcome["outcome"] in ("moved_start", "overlap")
    assert len(merged["matches"]) in (1, 2)


def test_a_row_with_a_decision_but_no_candidate_is_kept_not_discarded():
    """Retention is allowed; retention reported as removal is not.

    The reproduction that mattered here was the *undecided* case: `reconcile_import` logged
    ``dropped_machine_row`` and then returned that very row, so the reconciliation described an edit the
    project never got. Both branches now say "retained", and the branch with nothing decided behind it
    carries the export consequence that makes retention honest -- the row is held, so it shows up as
    footage no job will write.
    """
    project = _project()
    store = review.ReviewStore(project)
    store.decide(_clip_end(project), "extend", end_at=212.0)
    merged = review.merge_into_project(project, [])
    assert [row["start"] for row in merged["matches"]] == [110.0]
    kept = merged["matches"][0]["review"]["no_candidate"]
    assert kept["decisions"], "the reviewer must be able to see whose work is still in here"
    assert kept["kept_because"] == "a person decided something about it"
    outcome = merged["review"]["reconciliations"][-1]["outcomes"][-1]
    assert outcome["outcome"] == "retained_without_candidate"
    assert outcome["export_effect"].startswith("unchanged")
    plan = review.export_plan(merged)
    assert plan["counts"]["exportable"] == 1, "a row a person claimed still exports"

    plain = {"duration": 100.0, "matches": [{"start": 30.0, "end": 40.0, "recovery": {}}]}
    abandoned = review.merge_into_project(plain, [])
    assert [row["start"] for row in abandoned["matches"]] == [30.0], "nothing was deleted"
    second = abandoned["review"]["reconciliations"][-1]["outcomes"][-1]
    assert second["outcome"] == "retained_without_candidate" and second["decisions"] == 0
    assert second["export_effect"].startswith("held")
    held = review.export_plan(abandoned)
    assert held["counts"]["held"] == 1 and held["counts"]["exportable"] == 0
    assert any("no candidate at this opening" in reason for reason in held["held"][0]["reasons"])
    # A retained-and-held row is uncovered footage, so the coverage accounts have to agree about it: the
    # held interval is reported twice on purpose -- once as a candidate that covers tape, once inside the
    # single stretch no job will write.
    assert held["coverage"]["held_intervals_not_rendered"]["intervals"] == [
        {"from": 30.0, "to": 40.0, "seconds": 10.0}]
    assert held["coverage"]["candidates"]["seconds"] == 10.0
    assert held["coverage"]["exportable_jobs"]["seconds"] == 0.0
    assert [(row["from"], row["to"]) for row in held["unplaced_play"]] == [(0.0, 100.0)]


# ---------------------------------------------------------------------------- losslessness on round trip

def test_a_recovered_segment_survives_a_project_round_trip():
    """This is the bug that would have erased the review path: loading a project dropped the evidence."""
    segment = MatchSegment.from_dict(copy.deepcopy(SEGMENT))
    assert segment.recovery["games"][0]["winner_confirmed_at"] == 205.0
    assert segment.review == {}
    again = MatchSegment.from_dict(json.loads(json.dumps(segment.to_dict())))
    assert again.recovery == segment.recovery
    assert again.review == segment.review


def test_a_project_that_never_saw_the_review_path_saves_exactly_as_before():
    """Backward compatibility is a claim, so it is tested rather than asserted in a comment."""
    plain = MatchSegment(start=1.0, end=2.0, player1="A", player2="B")
    assert set(plain.to_dict()) == {"start", "end", "player1", "player2", "character1", "character2",
                                    "round_name", "notes"}
    assert MatchSegment.from_dict({"start": 1.0, "end": 2.0}).recovery == {}


@requires_saved_recovery
def test_the_real_bb9_recovery_imports_without_losing_a_set():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    imported = review.import_recovery(plan["recovery"], source=str(PLAN))
    candidates = len(plan["recovery"]["candidates"])
    assert imported["accounting"]["segments_out"] == candidates
    assert imported["accounting"]["candidates_in"] == candidates
    # The published candidate list is set-level, so importing it alone yields intervals with no per-game
    # evidence inside. That is reported, not hidden, and it is why the CLI takes the games explicitly.
    assert imported["accounting"]["segments_without_game_evidence"] == candidates
    assert all(segment["recovery"]["characters_side_bound"] is False
               for segment in imported["segments"]), \
        "the recovery does not bind a character to a side, so no segment may claim it did"


@requires_saved_recovery
def test_attaching_the_saved_games_gives_every_set_something_to_review():
    from tekken_vod_helper import boundary_evidence as be
    from tekken_vod_helper import boundary_games as bg
    evidence = be.evidence(None, facts=str(ROOT / "build/work/bb9/games-pilot-game1/run1/"
                                               "timeline-with-facts.json"))
    tracked = bg.track_games(evidence["observations"], games_per_set=None,
                             source=bg.source_bounds(
                                 manifest=str(ROOT / "build/work/bb9/detect-1440p-v1/frames/"
                                                    "sample-manifest.json"), duration=6928.421136))
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    imported = review.import_recovery(plan["recovery"], source=str(PLAN), games=tracked["games"])
    assert imported["accounting"]["segments_without_game_evidence"] == 0, \
        "a set with no games attached would export a clip nobody can check"
    assert len(imported["segments"]) == len(plan["recovery"]["candidates"])
    # Per-game facts survive as per-game facts: characters stay a list per game, never a single pair.
    for segment in imported["segments"]:
        for game in segment["recovery"]["games"]:
            assert isinstance(game["characters_observed"], list)
            assert game["characters_side_bound"] is False
    segments = [MatchSegment.from_dict(s) for s in imported["segments"]]
    assert all(seg.recovery.get("games") for seg in segments), "the round trip must not flatten them"


# ------------------------------------------------ recovering an opening from the saved sequence

def series(pairs):
    """Saved sequence rows as the tracker writes them: (timestamp, state, clock-or-None)."""
    return [{"timestamp": float(at), "screen_state": state, "round_clock": clock}
            for at, state, clock in pairs]


def test_the_pre_round_sequence_recovers_an_older_opening_than_the_candidate():
    """The rule the clock could not give: the only way onto a VS screen is a new game starting.

    The saved rows behind a BB9 set opening are ``controller_prompt`` -> ``character_select`` ->
    ``vs_screen`` -> ``stage_intro`` -> ``gameplay``. A walk that stops at "the clock reads 60 here" never
    uses them; this does, and it returns the first row of that run with the last fight row as its bound.
    """
    rows = series([(2592.6, "gameplay", 12), (2597.6, "result", None), (2602.6, "controller_prompt", None),
                   (2612.6, "character_select", None), (2622.6, "vs_screen", None),
                   (2632.6, "stage_intro", None), (2769.8, "stage_intro", None),
                   (2771.8, "gameplay", None)])
    recovered = review.recover_opening(rows, 2771.769)
    assert recovered["status"] == "resolved"
    assert recovered["opening_at"] == 2602.6, "the station prompt is the earliest frame the run supports"
    assert recovered["last_fight_before_run"] == 2597.6
    assert recovered["opening_uncertainty_seconds"] == pytest.approx(5.0)
    assert "vs_screen" in recovered["basis"] and "controller_prompt" in recovered["pre_run_states"]


def test_a_candidate_with_play_running_behind_it_and_no_select_screen_is_not_an_opening():
    rows = series([(1200.0, "gameplay", 40), (1201.0, "gameplay", 39), (1202.0, "gameplay", 38)])
    recovered = review.recover_opening(rows, 1202.0, scan_seconds=30.0)
    assert recovered["status"] == "contradicted" and recovered["opening_at"] is None
    assert "inside a running game" in recovered["basis"]


def test_a_candidate_already_on_a_select_screen_is_consistent_not_repaired():
    rows = series([(1200.0, "gameplay", 20), (1210.0, "character_select", None)])
    recovered = review.recover_opening(rows, 1210.0, scan_seconds=60.0)
    assert recovered["status"] == "consistent" and recovered["opening_at"] == 1210.0


def test_unknown_rows_may_sit_inside_a_run_but_never_make_one():
    with_unknown = series([(2597.0, "gameplay", 3), (2600.0, "unknown", None),
                           (2602.0, "vs_screen", None), (2700.0, "unknown", None),
                           (2771.0, "gameplay", None)])
    recovered = review.recover_opening(with_unknown, 2771.0)
    assert recovered["status"] == "resolved" and recovered["unknown_rows_in_run"] == 1
    assert recovered["last_fight_before_run"] == 2597.0
    only_unknown = series([(2700.0, "unknown", None), (2760.0, "unknown", None),
                           (2771.0, "gameplay", None)])
    assert review.recover_opening(only_unknown, 2771.0)["status"] == "unresolved"


def test_a_clock_series_without_states_fabricates_no_opening():
    """A clock row is a value, not a sequence. Without states the honest answer is "not settled here".

    The first version of this function walked clock values alone and called a rise an opening; on the real
    recording, where the clock exists only during play and is sampled every five seconds, that produced
    openings 139 s away from the candidate. It is now a bound, never a boundary.
    """
    clock_only = [{"timestamp": float(at), "round_clock": 60 - index}
                  for index, at in enumerate(range(2600, 2772, 10))]
    recovered = review.recover_opening(clock_only, 2770.0)
    assert recovered["status"] == "unresolved" and recovered["opening_at"] is None
    assert recovered["rows_in_window"] == 0 or recovered["basis"] == ""


def test_no_rows_at_all_names_the_window_that_has_none():
    recovered = review.recover_opening(series([(500.0, "gameplay", 20)]), 108.0, scan_seconds=60.0)
    assert recovered["status"] == "unresolved"
    assert recovered["missing_evidence_window"] == [48.0, 108.0]
    assert "no saved row" in recovered["what_would_settle_it"]


def test_the_clock_at_the_candidate_is_reported_as_a_bound_only():
    rows = series([(2762.0, "vs_screen", None), (2768.0, "stage_intro", None), (2771.0, "gameplay", 58)])
    recovered = review.recover_opening(rows, 2771.0, scan_seconds=30.0)
    assert recovered["clock_at_candidate"] == 58
    assert recovered["candidate_is_seconds_into_a_running_round"] == 2.0
    assert recovered["opening_at"] == 2762.0, "the bound is not used as the opening"
    assert "starts 2.0 s into a round" in recovered["basis"]


def test_start_opening_folds_the_recovered_instant_in_without_losing_the_edge_note():
    recovery = {"edges": {"start": {"at": 2771.769, "clock_at_boundary": 60, "walked_past": []}}}
    quiet = review.start_opening(recovery)
    assert quiet["earliest_supported_opening"] is None
    with_rows = review.start_opening(recovery, rows=series(
        [(2700.0, "gameplay", 5), (2740.0, "controller_prompt", None), (2750.0, "vs_screen", None),
         (2760.0, "stage_intro", None), (2771.769, "gameplay", 60)]))
    assert with_rows["earliest_supported_opening"] == 2740.0
    assert any("saved clock series" in text or "sequence" in text
               for text in with_rows["supported_by"])
    assert with_rows["recovered_opening"]["last_fight_before_run"] == 2700.0


def test_the_queue_offers_a_recovered_opening_without_moving_anything():
    """Candidate-local review: the reviewer gets an instant to jump to, and the row keeps its start."""
    rows = series([(748.0, "gameplay", 8), (750.0, "controller_prompt", None),
                   (760.0, "character_select", None), (770.0, "vs_screen", None),
                   (780.0, "stage_intro", None), (790.79, "gameplay", 58)])
    project = {"duration": 6928.0, "sequence_rows": rows, "matches": [{
        "start": 790.79, "end": 1371.37,
        "recovery": {"clip_end_at": 1374.37, "clip_end_anchor": {"winner_confirmed_at": 1372.37},
                     "edges": {"start": {"at": 790.79, "clock_at_boundary": 58,
                                         "walked_past": []}}}}]}
    item = next(i for i in review.review_queue(project) if i["kind"] == "new_match")
    assert item["boundary"]["earliest_supported_opening"] == 750.0
    assert item["adjust"]["jump_to"] == 750.0
    assert project["matches"][0]["start"] == 790.79, "a recovery proposal never rewrites the boundary"
    plan = review.export_plan(project)
    assert plan["clips"][0]["opening"]["recovered_opening_at"] == 750.0
    assert plan["clips"][0]["opening"]["recovered_opening_status"] == "resolved"
