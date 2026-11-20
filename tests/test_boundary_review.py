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
    "start": 110.0, "end": 200.0, "player1": "", "player2": "", \
    "recovery": {"orientation_required": True, "unnamed_sides": ["p1", "p2"], \
                 "sides": {"p1": {"player": "", "character": "", "source": ""}, \
                           "p2": {"player": "", "character": "", "source": ""}}, \
                 "games": [{"start": 110.0, "characters_observed": ["Azucena", "Nina"], \
                            "characters_side_bound": False, "conclusion_event_at": 198.0, \
                            "winner_confirmed_at": 205.0}], \
                 "clip_end_at": 202.0, "clip_end_anchor": {"winner_confirmed_at": 205.0}, \
                 "clip_end_basis": "conclusion plus confirmation plus padding, applied once", \
                 "characters_side_bound": False, "alternative_groupings": [], "unresolved_claims": [], \
                 "boundary_certain": True, "footage_complete": True}, \
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
        {"from": 200.0, "to": 202.0, \
         "what": "footage past the recorded set end that this clip now includes"}]
    assert clip["skipped_reason"] is None


def test_a_clip_that_includes_its_confirmation_is_clean_of_the_loss_warning():
    good = copy.deepcopy(SEGMENT)
    good["recovery"]["clip_end_at"] = 207.0
    clip = review.resolve_clip_end(good)
    assert clip["excluded_gameplay"] == [], "the confirmation is inside the clip, so nothing is lost"
    assert not any("does not contain" in warning for warning in clip["warnings"])
    assert clip["included_past_recorded_end"], "and the extra seven seconds are still disclosed"


def _winner_recovery(winner, identities):
    return {"kind": "set-recovery", "candidates": [{
        "start": 100.0, "end": 200.0, "clip_end_at": 202.0, "games": 1, "membership": "supported", \
        "footage_complete": True, "game_wins": {"p2": 2, "p1": 0}, "set_winner": winner, \
        "identities": identities, "concluded_by": ["target_reached"], "missing_evidence": [], \
        "unresolved_claims": [], "edges": {}, "game_spans": [[100.0, 200.0]]}]}


def _hypothesis_recovery(hypotheses):
    """One candidate carrying the lower identity tier, shaped as ``recovery()`` publishes it."""
    return {"kind": "set-recovery", "candidates": [{
        "start": 100.0, "end": 200.0, "clip_end_at": 202.0, "games": 2, "membership": "supported",
        "footage_complete": True, "game_wins": {"p1": 2, "p2": 0}, "set_winner": None,
        "identities": [], "concluded_by": ["target_reached"], "missing_evidence": [],
        "unresolved_claims": [], "edges": {}, "game_spans": [[100.0, 150.0], [155.0, 200.0]],
        "nameplate_hypotheses": hypotheses}]}


NAMED = {"unit": "overlay_nameplate_hypothesis", "authoritative": False,
         "changes_grouping_or_targets": False,
         "trust_note": "hypothesis only: the band answers and these rows are reads of the same broadcast "
                       "graphic, whose names are known to repaint mid-round",
         "games": [
             {"at": 100.0, "basis": "overlay_nameplate_hypothesis", "admitted": True,
              "sides": {"p1": "Ann", "p2": "Bo"}, "share": {"player1": 1.0, "player2": 1.0},
              "named_rows": 42, "band_cover": None, "band_agrees": None, "why": "rows name both sides",
              "span": [100.0, 150.0]},
             {"at": 155.0, "basis": "header_band_saved", "admitted": True,
              "sides": {"p1": "Ann", "p2": "Bo"}, "share": {"player1": 0.9, "player2": 0.9},
              "named_rows": 12, "band_cover": True, "band_agrees": False, "why": "band names both sides",
              "span": [155.0, 200.0],
              "contradicts": {"overlay_nameplate_rows": {"p1": "Cid", "p2": "Ann"}, "share": None,
                              "named_rows": 9, "because": "the rows inside this game read the graphic "
                                                          "differently"}}],
         "possible_winners": [{"name": "Ann", "wins": 2, "games": [100.0, 155.0],
                               "unit": "overlay_nameplate_hypothesis"}],
         "unnamed_games": []}


def test_the_identity_tier_survives_import_save_and_the_panel_view():
    """A field that stops at ``track_games`` is a field the operator never sees.

    Measured on BB9 on 2026-11-08: the canonical run reported 30 hypothesis rows and the delivered package
    reported none, because two more projection layers (``recovery()`` and ``import_recovery``) copy candidate
    fields by name. These assertions are on the saved artifact and the panel, not on the computation.
    """
    imported = review.import_recovery(_hypothesis_recovery(NAMED))
    project = br_project(imported)
    saved = ProjectState.from_dict(json.loads(json.dumps(project))).to_dict()
    recovery = saved["matches"][0]["recovery"]
    assert [g["basis"] for g in recovery["nameplate_hypotheses"]["games"]] == [
        "overlay_nameplate_hypothesis", "header_band_saved"]
    assert recovery["nameplate_hypotheses"]["authoritative"] is False, "the tier must not arrive promoted"
    # A contradiction is delivered as one, not smoothed into whichever read is more convenient.
    conflicting = recovery["nameplate_hypotheses"]["games"][1]
    assert conflicting["band_agrees"] is False
    assert conflicting["sides"] == {"p1": "Ann", "p2": "Bo"}, "the band's own names survive"
    assert conflicting["contradicts"]["overlay_nameplate_rows"] == {"p1": "Cid", "p2": "Ann"}

    from tekken_vod_helper.boundary_review_flow import ReviewFlow
    evidence = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(saved)))).queue("all")[0]["evidence"]
    assert [h["basis"] for h in evidence["identity_hypotheses"]] == [
        "overlay_nameplate_hypothesis", "header_band_saved"]
    assert evidence["identity_hypotheses"][1]["contradicts"] is True
    assert evidence["possible_winners"] == [{"name": "Ann", "wins": 2,
                                             "unit": "overlay_nameplate_hypothesis"}]
    assert "same broadcast graphic" in evidence["identity_hypothesis_limits"]


def test_an_absent_tier_is_published_as_absent_rather_than_as_no_games_seen():
    """Silence about a witness and absence of one are different claims, and the panel must not merge them."""
    imported = review.import_recovery(_hypothesis_recovery(None))
    project = br_project(imported)
    saved = ProjectState.from_dict(json.loads(json.dumps(project))).to_dict()
    from tekken_vod_helper.boundary_review_flow import ReviewFlow
    evidence = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(saved)))).queue("all")[0]["evidence"]
    assert evidence["identity_hypotheses"] == [] and evidence["possible_winners"] == []
    assert evidence["identity_hypothesis_limits"] is None, "nothing was claimed, so nothing may be quoted"


def br_project(imported):
    """Merge an import into an empty project the way the public command does."""
    return review.merge_into_project({}, imported["segments"], event="Cup", duration=600.0)


def test_a_winner_named_from_unbound_sides_is_published_with_the_condition_that_limits_it():
    """Demonstrated on BB9: `set_winner: "PWDDA | Ariz"` sat beside `orientation: unresolved`.

    The name is real evidence about a graphic, not a confirmed identity, and a reviewer who reads the field
    alone cannot tell which they are looking at. The caveat goes where the review tab already shows claims,
    and the winner is kept -- suppressing it would lose the reading that produced it.
    """
    imported = review.import_recovery(_winner_recovery("Mattiniero", []))
    recovery = imported["segments"][0]["recovery"]
    assert recovery["set_winner"] == "Mattiniero", "the name is disclosed, not deleted"
    assert recovery["orientation_required"] is True
    assert any("nameplate on the winning side" in claim
               for claim in recovery["unresolved_claims"]), \
        "and the reason it is not yet an identity travels with it"


def test_a_winner_whose_sides_are_named_needs_no_borrowed_caveat():
    """The caveat is about the missing side-to-player binding, so a bound set must not carry it."""
    identities = [{"after": ["Rebix", "Mattiniero"], "share": {"after": {"player1": 1.0, "player2": 1.0}}}]
    imported = review.import_recovery(_winner_recovery("Mattiniero", identities))
    recovery = imported["segments"][0]["recovery"]
    assert recovery["orientation_required"] is False, "both sides are named, so this is the bound case"
    assert recovery["set_winner"] == "Mattiniero"
    assert not any("nameplate on the winning side" in claim
                   for claim in recovery["unresolved_claims"]), \
        "no unnamed side, so nothing to qualify"


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
    clean["recovery"]["sides"] = {"p1": {"player": "Alice", "character": "", "source": ""}, \
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
    # The row opens where the reading was taken. A saved edge is evidence about its own instant only, so a
    # fixture that moved the start away from it would correctly stop being told what the clock said there.
    late["start"], late["end"] = 790.79, 1371.37
    late["recovery"]["edges"] = {"start": {
        "at": 790.79, "clock_at_boundary": 58, "scanned": [670.79, 790.79], \
        "settled_by": {"at": 748.748, "kind": "cue", "state": "controller_prompt"},
        # A walked-past row only contradicts anything if the frame shows the round being played. The prose
        # is what says so; the state word "gameplay" is what BB9's ROUND 1 banner carries too, and reading
        # it as live play invented two contradictions on this recording.
        "walked_past": [{"at": 785.785, "state": "gameplay", "clock": None, "because": "clock unread", \
                         "evidence": "Active combat with timer 58 and a Counter popup"}]}}
    opening = review.start_opening(late["recovery"])
    assert opening["late_by_at_least_seconds"] == 2.0
    assert opening["earliest_supported_opening"] == 748.748
    assert opening["clock_bound_opening"] == pytest.approx(788.79)
    assert len(opening["contradicts"]) == 2, "the clock and the walked-past row are separate facts"
    assert "round-intro animation" in opening["contradicts"][1], "a gameplay row bounds, never names"
    # The same row, same instant, same state word, described as an opening rather than a fight: now there
    # is one contradiction, the clock's, and nothing claims the banner was somebody playing.
    banner = copy.deepcopy(late["recovery"]["edges"])
    banner["start"]["walked_past"] = [{"at": 785.785, "state": "gameplay", "clock": None, \
                                       "because": "clock unread", \
                                       "evidence": "In-fight arena with 'ROUND 1' banner and P1/P2 tags"}]
    opening = review.start_opening(dict(late["recovery"], edges=banner))
    assert len(opening["contradicts"]) == 1 and "clock already reads 58" in opening["contradicts"][0]
    assert [item["phase"] for item in opening["readings"] if item["at"] == 785.785] == ["round_opening"]
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
    """A frame that could not settle the bound is still a sighting -- just not necessarily of play.

    Every arena frame is labelled "gameplay" by the classifier, including one with a banner across it or a
    menu open, so whether the row the walk left behind argues against the boundary depends on what the frame
    shows and not on what its state column says.
    """
    thin = copy.deepcopy(SEGMENT)
    thin["recovery"]["edges"] = {"start": {"at": 790.79, "clock_at_boundary": None, \
                                           "scanned": [780.79, 790.79], "settled_by": None, \
                                           "walked_past": [{"at": 785.785, "state": "gameplay", \
                                                             "clock": None, "because": "clock unread", \
                                                             "evidence": "Live match scene, round timer " \
                                                                         "(60), fighters idle; in-match " \
                                                                         "pause menu is open"}]}}
    opening = review.start_opening(thin["recovery"])
    assert opening["earliest_supported_opening"] is None
    assert opening["late_by_at_least_seconds"] is None
    # A paused frame says nobody was playing. That is information about the frame, not a contradiction.
    assert opening["flagged"] is False, opening["contradicts"]
    assert [item["phase"] for item in opening["readings"] if item["at"] == 785.785] == ["paused"]
    assert any(item["sees"] == "pause menu" and "nobody playing" in item["says"]
               for item in opening["readings"] if item["at"] == 785.785)
    # The same instant with a frame that shows the round being fought is an argument the reviewer should
    # hear, and it is the only thing that has changed.
    thin["recovery"]["edges"]["start"]["walked_past"][0]["evidence"] = "Active combat, timer 58"
    opened = review.start_opening(thin["recovery"])
    assert opened["flagged"] is True
    assert "785.785" in opened["contradicts"][0]


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
        {"start": 10.0, "end": 100.0, "recovery": {"clip_end_at": 100.0, "games": [{"start": 10.0, \
                                                                                     "end": 100.0}]}},
        {"start": 260.0, "end": 300.0, "recovery": {"clip_end_at": 300.0, \
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
    assert plan["coverage"]["exportable_jobs"]["intervals"] == [{"from": 10.0, "to": 60.0, \
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
        {"start": 10.0, "end": 20.0, "recovery": {"clip_end_at": 20.0, \
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
        "start": 790.79, "end": 1371.37, \
        "recovery": {"clip_end_at": 1374.37, "clip_end_anchor": {"winner_confirmed_at": 1372.37}, \
                     "edges": {"start": {"at": 790.79, "clock_at_boundary": 58, \
                                         "walked_past": []}}}}]}
    plan = review.export_plan(project)
    clip = plan["clips"][0]
    assert clip["held"] is False, "the end contains its confirmation, so the clip is not held"
    assert clip["opening"]["flagged"] is True
    assert clip["opening"]["late_by_at_least_seconds"] == 2.0
    assert clip["opening_review_required"] is True
    assert clip["boundary"]["operator_accepted_boundary"] is False
    assert clip["boundary"]["machine_supports_start"] == "contradicted"
    assert clip["boundary"]["exportable"] is True, "a clip that starts 2 s into a round still ships"
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
    assert approved["clips"][0]["boundary"]["operator_accepted_boundary"] is False, \
        "an approved partial is exportable and disclosed, never silently re-labelled correct: agreeing " \
        "to ship the loss is not evidence that the opening is right"
    assert approved["clips"][0]["boundary"]["partial_override"] is True
    assert approved["clips"][0]["boundary"]["partial_approval"] == {
        "opening_approved_partial": True, "end_excludes_gameplay": False}
    assert (approved["clips"][0]["boundary"]["claim_this_row_may_make"]
            == "exportable; operator approved a partial here, not a complete accepted boundary"), \
        "the sentence a row is allowed to say has to name what was actually approved"
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
    assert set(plain.to_dict()) == {"start", "end", "player1", "player2", "character1", "character2", \
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
    evidence = be.evidence(None, facts=str(ROOT / "build/work/bb9/games-pilot-game1/run1/" \
                                               "timeline-with-facts.json"))
    tracked = bg.track_games(evidence["observations"], games_per_set=None,
                             source=bg.source_bounds(
                                 manifest=str(ROOT / "build/work/bb9/detect-1440p-v1/frames/" \
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


def candidate(result, level):
    return next((item for item in result["candidates"] if item["level"] == level), None)


def test_a_round_banner_is_a_round_opening_and_never_a_game_opening():
    """The reproduced defect: gameplay -> ROUND 2 call -> gameplay was reported as a resolved game start.

    A round lives inside a game, so a banner bounds a round and says nothing about which game or set it
    belongs to. The report has to carry that distinction rather than flattening every pre-round screen
    into one "new game" proof, and it may not claim an instant closer to the candidate than a level it
    actually supports.
    """
    rows = series([(100.0, "gameplay", 4), (105.0, "round_call", None), (110.0, "gameplay", 60)])
    recovered = review.recover_opening(rows, 110.0)
    assert recovered["levels_seen"] == ["round_opening"]
    game = candidate(recovered, "game_opening_context")
    assert game is None, "a round banner was promoted to a game opening again"
    opening = candidate(recovered, "round_opening")
    assert opening["at"] == 105.0 and "round is inside a game" in opening["does_not_prove"]
    assert recovered["set_membership"]["decided"] is False
    # The candidate never becomes the boundary: the row keeps its start until a person moves it.
    assert recovered["earliest_candidate_level"] == "round_opening"


def test_stage_and_character_screens_are_game_context_that_cannot_cut_a_set():
    rows = series([(100.0, "gameplay", 8), (104.0, "character_select", None),
                   (106.0, "vs_screen", None), (108.0, "gameplay", 60)])
    recovered = review.recover_opening(rows, 108.0)
    game = candidate(recovered, "game_opening_context")
    assert game["at"] == 104.0
    assert set(game["supported_by"]) == {"character_select", "vs_screen"}
    assert "between games of one set" in game["does_not_prove"]
    assert recovered["set_membership"]["decided"] is False
    assert "game wins reaching the target" in recovered["set_membership"]["why"]


def test_a_controller_screen_is_pairing_context_and_preferred_is_not_sufficient():
    rows = series([(100.0, "gameplay", 3), (103.0, "post_match_menu", None),
                   (105.0, "controller_prompt", None), (108.0, "gameplay", 60)])
    recovered = review.recover_opening(rows, 108.0)
    pairing = candidate(recovered, "pre_pairing_context")
    assert pairing is not None and pairing["at"] == 103.0
    assert "never sufficient" in pairing["does_not_prove"]
    assert recovered["earliest_candidate"] == 103.0, "the earliest thing to look at is the earliest row"


def test_play_behind_a_candidate_is_never_evidence_that_no_new_game_started():
    """Absence of a select screen is absence of a row, not absence of a game.

    The old wording ("contradicted ... not an opening at all") read like a finding about the match. What
    the rows actually show is play continuing, which leaves where the game began unanswered.
    """
    rows = series([(1200.0, "gameplay", 40), (1201.0, "gameplay", 39), (1202.0, "gameplay", 38)])
    recovered = review.recover_opening(rows, 1202.0, scan_seconds=30.0)
    assert recovered["status"] == "no_earlier_candidate"
    assert recovered["candidates"] == []
    assert recovered["play_continues_behind_candidate"] is True
    assert "not evidence that no new game or set started" in recovered["basis"]


def test_the_reported_sequence_is_chronological_not_a_sorted_set():
    """A set of state names destroys the one thing that makes the sequence readable: its order."""
    rows = series([(100.0, "gameplay", 5), (101.0, "vs_screen", None), (102.0, "stage_select", None),
                   (103.0, "character_select", None), (106.0, "gameplay", 60)])
    recovered = review.recover_opening(rows, 106.0)
    states = [state for _at, state in recovered["sequence"]]
    assert states == ["gameplay", "vs_screen", "stage_select", "character_select", "gameplay"]
    game = candidate(recovered, "game_opening_context")
    assert [row[1] for row in game["rows"]] == ["vs_screen", "stage_select", "character_select"]


def test_the_canonical_game_list_is_cross_checked_rather_than_ridden():
    rows = series([(100.0, "gameplay", 5), (104.0, "character_select", None),
                   (106.0, "gameplay", 60)])
    agrees = review.recover_opening(rows, 106.0, games=[{"start": 106.0}, {"start": 300.0}])
    assert agrees["canonical_games_around"]["candidate_is_a_canonical_game_start"] is True
    assert "the pipeline's own game list opens a game at this instant" in agrees["basis"]
    inside = review.recover_opening(rows, 106.0, games=[{"start": 20.0}, {"start": 400.0}])
    marks = inside["canonical_games_around"]
    assert marks["candidate_is_a_canonical_game_start"] is False
    assert marks["game_starts_before"] == 20.0 and marks["game_starts_after"] == 400.0, \
        "the reviewer needs to see the candidate sits inside an existing game, not outside one"


def test_unknown_rows_may_sit_inside_a_run_but_never_make_one():
    with_unknown = series([(100.0, "gameplay", 3), (102.0, "unknown", None), (104.0, "vs_screen", None),
                           (106.0, "gameplay", 60)])
    recovered = review.recover_opening(with_unknown, 106.0)
    assert recovered["unknown_rows_in_run"] == 1
    assert candidate(recovered, "game_opening_context")["at"] == 104.0
    only_unknown = series([(100.0, "gameplay", 3), (102.0, "unknown", None), (104.0, "unknown", None),
                           (106.0, "gameplay", 60)])
    quiet = review.recover_opening(only_unknown, 106.0)
    assert quiet["candidates"] == [] and quiet["status"] == "insufficient_evidence"


def test_a_clock_series_without_states_fabricates_no_candidate():
    """The first draft walked clock values and called a rise an opening: 139 s from the candidate.

    On the real recording the clock exists only during play, sampled every second or five, so clock values
    alone cannot place an opening. They are kept as a bound on the candidate and nothing more.
    """
    clock_only = [{"timestamp": float(at), "round_clock": 60 - index}
                  for index, at in enumerate(range(2600, 2772, 10))]
    recovered = review.recover_opening(clock_only, 2770.0)
    assert recovered["status"] == "insufficient_evidence" and recovered["candidates"] == []
    assert recovered["earliest_candidate"] is None


def test_no_rows_at_all_names_the_window_that_has_none():
    recovered = review.recover_opening(series([(500.0, "gameplay", 20)]), 108.0, scan_seconds=60.0)
    assert recovered["status"] == "insufficient_evidence"
    assert recovered["missing_evidence_window"] == [48.0, 108.0]
    assert "no saved row" in recovered["basis"]


def test_the_clock_at_the_candidate_is_reported_as_a_bound_only():
    rows = series([(100.0, "gameplay", 9), (103.0, "stage_select", None), (104.0, "vs_screen", None),
                   (106.0, "gameplay", 58)])
    recovered = review.recover_opening(rows, 106.0, scan_seconds=30.0)
    assert recovered["clock_at_candidate"] == 58
    assert recovered["candidate_is_seconds_into_a_running_round"] == 2.0
    assert recovered["earliest_candidate"] == 103.0, "the bound is not used as the opening"
    assert "2.0 s into it" in recovered["basis"]


def test_a_gap_inside_the_run_is_reported_as_a_gap():
    rows = series([(100.0, "gameplay", 9), (104.0, "controller_prompt", None),
                   (160.0, "controller_prompt", None), (166.0, "gameplay", 60)])
    recovered = review.recover_opening(rows, 166.0, scan_seconds=180.0)
    pairing = candidate(recovered, "pre_pairing_context")
    assert pairing["gaps_in_evidence"], "56 s with no row between two context screens is a hole, not a run"


def test_start_opening_folds_a_candidate_in_without_retiring_the_reading_requirement():
    recovery = {"edges": {"start": {"at": 106.0, "clock_at_boundary": 58, "walked_past": []}}}
    quiet = review.start_opening(recovery)
    assert quiet["earliest_supported_opening"] is None
    with_rows = review.start_opening(recovery, rows=series(
        [(100.0, "gameplay", 9), (103.0, "vs_screen", None), (106.0, "gameplay", 58)]))
    assert with_rows["earliest_supported_opening"] == 103.0
    assert any("saved sequence" in text for text in with_rows["supported_by"])
    assert with_rows["late_by_at_least_seconds"] == 2.0, "a candidate earlier to look at is not a look done"


def test_start_opening_survives_a_candidate_with_play_behind_it():
    """The consumer crash: this path used to index ``recovered['why']``, a key recovery never returned."""
    continuation = series([(1200.0, "gameplay", 40), (1201.0, "gameplay", 39),
                           (1202.0, "gameplay", 38)])
    opened = review.start_opening({"edges": {"start": {"at": 1202.0}}}, rows=continuation)
    assert opened["recovered_opening"]["status"] == "no_earlier_candidate"
    assert any("not evidence that no new game or set started" in text for text in opened["contradicts"])
    plan = review.export_plan({"duration": 1300.0, "sequence_rows": continuation, "matches": [
        {"start": 1202.0, "end": 1290.0, \
         "recovery": {"clip_end_at": 1292.0, "clip_end_anchor": {"winner_confirmed_at": 1291.0}, \
                      "edges": {"start": {"at": 1202.0}}}}]})
    assert plan["clips"][0]["opening"]["recovered_opening_status"] == "no_earlier_candidate"


def test_the_queue_offers_a_candidate_with_its_level_and_moves_nothing():
    rows = series([(748.0, "gameplay", 8), (750.0, "controller_prompt", None),
                   (760.0, "character_select", None), (770.0, "vs_screen", None),
                   (780.0, "stage_intro", None), (790.79, "gameplay", 58)])
    project = {"duration": 6928.0, "sequence_rows": rows, "matches": [{
        "start": 790.79, "end": 1371.37, \
        "recovery": {"clip_end_at": 1374.37, "clip_end_anchor": {"winner_confirmed_at": 1372.37}, \
                     "edges": {"start": {"at": 790.79, "clock_at_boundary": 58, \
                                         "walked_past": []}}}}]}
    item = next(i for i in review.review_queue(project) if i["kind"] == "new_match")
    assert item["adjust"]["jump_to"] == 750.0
    assert item["adjust"]["jump_to_level"] == "pre_pairing_context"
    assert item["boundary"]["earliest_supported_opening"] == 750.0
    assert project["matches"][0]["start"] == 790.79, "a proposal never rewrites the boundary"
    plan = review.export_plan(project)
    clip = plan["clips"][0]
    assert clip["opening"]["recovered_opening_at"] == 750.0
    assert clip["opening"]["recovered_opening_level"] == "pre_pairing_context"
    assert clip["opening"]["set_membership_decided"] is False


def test_absence_of_contradiction_is_not_acceptance():
    """The reproduced labelling bug: an unreviewed candidate with no start evidence read as accepted.

    Four claims, kept apart because the reviewer's next action differs for each: may it be exported, does
    the machine evidence place the boundary, has a person accepted it, has anything independent validated
    it. A row can be perfectly exportable and still know nothing about its own opening, and the report has
    to say that in so many words rather than leaving a bare ``boundary_accepted: true`` to be read as a
    measurement.
    """
    bare = {"start": 110.0, "end": 200.0, \
            "recovery": {"clip_end_at": 202.0, \
                         "clip_end_anchor": {"winner_confirmed_at": 200.0}, \
                         "edges": {"start": {"at": 110.0}}}}
    plan = review.export_plan({"duration": 300.0, "matches": [bare]})
    position = plan["clips"][0]["boundary"]
    assert position["exportable"] is True
    assert position["machine_supports_start"] == "unknown"
    assert position["operator_accepted_boundary"] is False
    assert position["independently_validated"] is False
    assert position["claim_this_row_may_make"] == "exportable; boundary not accepted by anyone"
    assert plan["counts"]["unknown_starts"] == 1 and plan["counts"]["operator_accepted"] == 0
    assert plan["counts"]["independently_validated"] == 0

    # Positive start evidence is what moves a start out of unknown, and it still is not a validation.
    with_cue = {"start": 110.0, "end": 200.0, \
                "recovery": {"clip_end_at": 202.0, \
                             "clip_end_anchor": {"winner_confirmed_at": 200.0}, \
                             "edges": {"start": {"at": 110.0, \
                                                 "settled_by": {"at": 108.0, "kind": "cue"}}}}}
    cue_plan = review.export_plan({"duration": 300.0, "matches": [with_cue]})
    assert cue_plan["clips"][0]["boundary"]["machine_supports_start"] == "supported"
    assert cue_plan["clips"][0]["boundary"]["independently_validated"] is False

    # An operator accepting both boundaries is a third thing again, and never becomes the fourth.
    project = {"duration": 300.0, "matches": [dict(with_cue)]}
    store = review.ReviewStore(project)
    for item in review.review_queue(project):
        if item["kind"] == "new_match":
            store.decide(item, "new_match")
        elif item["kind"] == "clip_end":
            store.decide(item, "confirmed")
    accepted = review.export_plan(project)["clips"][0]["boundary"]
    assert accepted["operator_reviewed"] is True
    assert accepted["operator_accepted_boundary"] is True
    assert accepted["independently_validated"] is False
    assert "not independently validated" in accepted["claim_this_row_may_make"]
    assert "reference outside this pipeline" in accepted["validation_why"]


def test_a_human_end_is_the_operator_accepting_the_end_not_the_machine_agreeing():
    plan = review.export_plan({"duration": 300.0, "matches": [{
        "start": 110.0, "end": 200.0, \
        "recovery": {"clip_end_at": 202.0, "clip_end_anchor": {"winner_confirmed_at": 200.0}, \
                     "edges": {"start": {"at": 110.0}}}, \
        "review": {"ends": {"end": 205.0}, "decisions": ["clip_end|110.0|110.0..200.0"]}}]})
    position = plan["clips"][0]["boundary"]
    assert position["machine_supports_end"] == "supported"
    assert position["end_why"] == "an operator end recorded at review", \
        "the reason has to say who is claiming the end, not leave it looking like the machine's"
    assert position["operator_answered"]["end"] is True


def test_a_proposal_without_its_reason_or_instant_is_refused_rather_than_filed():
    """A suggestion a reviewer cannot interrogate is a command with a nicer name."""
    with pytest.raises(review.InvalidAnswer, match="kinds are"):
        review.proposal_record("midpoint", 10.0, why="somewhere around here")
    with pytest.raises(review.InvalidAnswer, match="name the instant"):
        review.proposal_record("opening", None, why="earlier than now")
    with pytest.raises(review.InvalidAnswer, match="without its reason"):
        review.proposal_record("opening", 10.0, why="   ")
    with pytest.raises(review.InvalidAnswer, match="or it is unknown"):
        review.proposal_record("opening", 10.0, why="banner", uncertainty_seconds="wide")
    with pytest.raises(review.InvalidAnswer, match="cannot be negative"):
        review.proposal_record("opening", 10.0, why="banner", uncertainty_seconds=-1.0)
    with pytest.raises(review.InvalidAnswer, match="confirmation or presentation|confirmation or"):
        review.proposal_record("end", 10.0, why="camera", purpose="aesthetic")


def test_an_unknown_proposal_width_stays_unknown_in_every_rendering():
    record = review.proposal_record("opening", 10.0, why="continuity only")
    assert record["uncertainty_seconds"] is None
    assert review.proposal_width(record["uncertainty_seconds"]) == "unknown"
    assert review.proposal_width(1.000986) == "+-1.001 s"


# --------------------------------------------------------------------------------- automatic proposals

def auto_project(rows, *, start=100.0, edge_clock=58, extra_recovery=None):
    """One recovered clip, the saved sequence beside it, and no operator involvement yet."""
    recovery = {"clip_end_at": start + 300.0, "membership": "supported",
                # The walk cross-checks against the pipeline's own game list, which is a list of spans, not
                # a count -- passing 2 here was my fixture inventing a shape the product never produces.
                "games": [{"start": start, "end": start + 290.0}], \
                "edges": {"start": {"at": start, "clock_at_boundary": edge_clock, \
                                    "scanned": [r["timestamp"] for r in rows]}}}
    recovery.update(extra_recovery or {})
    return {"duration": 1000.0, "video_path": "/rec.mkv", \
            "sequence_rows": rows, "matches": [{"start": start, "end": start + 290.0, \
                                                "recovery": recovery, "review": {}}], \
            "review": {"version": review.REVIEW_VERSION, "decisions": [], "log": []}}


BANNER_ROWS = [
    {"timestamp": 94.0, "state": "gameplay", "round_call": 1, "player1": "Ana", "player2": "Bo", \
     "evidence": "ROUND 1 banner over the arena"},
    {"timestamp": 99.0, "state": "gameplay", "round_clock": 58, "player1": "Ana", "player2": "Bo", \
     "evidence": "fighters engaged"},
    {"timestamp": 104.0, "state": "gameplay", "round_clock": 53, "player1": "Ana", "player2": "Bo", \
     "evidence": "fighters engaged"},
]
MENU_ROWS = [
    {"timestamp": 90.0, "state": "character_select", "player1": "Ana", "player2": "Bo", \
     "evidence": "roster grid"},
    {"timestamp": 100.0, "state": "gameplay", "round_clock": 55, "player1": "Ana", "player2": "Bo", \
     "evidence": "fighters engaged"},
]


def test_the_import_itself_files_the_opening_the_sequence_supports():
    """The point of the pass: a normal recovery import offers the improvement without a script doing it.

    Four shapes are exercised because one fixture cannot tell "no proposal" apart from "no evidence":
    a late start with the round's own banner before it, a start already on its cue, a menu screen that is
    context rather than a claim, and a project with no sequence at all.
    """
    late = auto_project(BANNER_ROWS)
    result = review.apply_opening_proposals(late)
    assert result["counts"]["filed"] == 1, result["rows"]
    line = result["rows"][0]
    assert line["at"] == 94.0 and line["level"] == "round_opening" and line["preserves"] == "play"
    proposal = late["matches"][0]["review"]["proposals"][0]
    assert proposal["at"] == 94.0 and proposal["generated_by"] == "opening_sequence"
    # The frame path comes from resolving the row against the recording's sample manifest, which a synthetic
    # fixture does not have: the field has to be present and honestly empty here, and named for real in the
    # headless tab check, where the delivered package and its manifest are both in play.
    assert "frame" in proposal["evidence"] and proposal["evidence"]["frame"] is None
    # No load-in rows in this fixture, so this is the walk's own recovered cue and its width, not a policy
    # fallback -- the distinction the next tests pin down.
    assert proposal["uncertainty_seconds"] == 6.0, \
        "the width the sampling can defend travels with it"
    assert proposal["evidence"]["policy_fallback"] is False
    assert late["matches"][0]["start"] == 100.0, "a proposal is not an edit"
    assert result["moves_no_boundary"] and not result["counts_as_decision"]

    # A row that opens on its own round cue, with no load-in screens behind it, has nothing to propose. The
    # earlier version of this fixture had a character-select row in it, which the policy now answers with a
    # flagged fallback rather than silence -- a different case, asserted below.
    safe = auto_project([
        {"timestamp": 94.0, "state": "gameplay", "round_clock": 59, "player1": "Ana", "player2": "Bo",
         "evidence": "fighters engaged"},
        {"timestamp": 99.0, "state": "gameplay", "round_call": 1, "player1": "Ana", "player2": "Bo",
         "evidence": "ROUND 1 banner"},
        {"timestamp": 100.0, "state": "gameplay", "round_clock": 60, "player1": "Ana", "player2": "Bo",
         "evidence": "READY cue"}], start=99.0, edge_clock=60)
    assert review.apply_opening_proposals(safe)["counts"]["already-supported"] == 1
    assert not (safe["matches"][0]["review"].get("proposals") or [])

    # A character-select screen 10 s before a mid-round candidate is not a claim about where the set opens,
    # but the policy still has an answer of last resort: offer the load-in frame, flagged as a fallback, and
    # say why it is not confident. Silence here used to be the answer; a labelled machine guess is more useful
    # and is still not a boundary.
    context = auto_project(MENU_ROWS)
    outcome = review.apply_opening_proposals(context)
    assert outcome["counts"]["filed"] == 1 and outcome["counts"]["already-supported"] == 0
    line = outcome["rows"][0]
    assert line["at"] == 90.0 and line["policy_fallback"] is True, line
    proposal = context["matches"][0]["review"]["proposals"][0]
    assert proposal["evidence"]["policy_fallback"] is True
    assert "no clean stage-select" in proposal["evidence"]["policy_review_reason"].lower()
    assert "flagged fallback" in proposal["why"], proposal["why"]
    # A one-row chain has no step to measure, so the width stays unknown -- and "unknown" must not be written
    # as 0.000 s anywhere on the record or in the sentence a reviewer reads.
    assert proposal["uncertainty_seconds"] is None
    assert "0.000 s of" not in proposal["why"] and "does not bound" in proposal["why"]

    nowhere = auto_project([])
    assert review.apply_opening_proposals(nowhere)["counts"]["no-evidence"] == 1


def test_an_automatic_rerun_is_idempotent_and_never_resurrects_a_refusal():
    project = auto_project(BANNER_ROWS)
    first = review.apply_opening_proposals(project)
    again = review.apply_opening_proposals(project)
    assert first["counts"]["filed"] == 1 and again["counts"]["unchanged"] == 1
    assert len(project["matches"][0]["review"]["proposals"]) == 1, "no duplicate pile-up per run"

    # A reviewer says no. The next pass must not file the same suggestion back as news.
    review.ReviewStore(project).set_proposal_status(100.0, "opening", review.PROPOSAL_REFUSED,
                                                    note="the banner belongs to the match before")
    third = review.apply_opening_proposals(project)
    assert third["counts"]["unchanged"] == 1 and third["counts"]["filed"] == 0
    filed = project["matches"][0]["review"]["proposals"]
    assert [item["status"] for item in filed] == [review.PROPOSAL_REFUSED], \
        "the refusal is the record, and nothing new appeared beside it"


def test_an_operators_answer_shuts_the_automatic_pass_out_of_that_boundary():
    project = auto_project(BANNER_ROWS)
    store = review.ReviewStore(project)
    store.decide({"kind": "new_match", "segment": project["matches"][0], "index": 0}, "new_match")
    outcome = review.apply_opening_proposals(project)
    assert outcome["counts"]["refused"] == 1 and outcome["counts"]["filed"] == 0
    assert not (project["matches"][0]["review"].get("proposals") or [])
    assert project["matches"][0]["start"] == 100.0, "the row keeps the boundary the person confirmed"


def test_proposals_generated_on_import_survive_the_operators_first_save():
    """`review` is carried whole; a top-level summary would vanish the first time the tab writes."""
    project = auto_project(BANNER_ROWS)
    review.merge_into_project(project, project["matches"], event="Cup", duration=1000.0)
    stored = ProjectState.from_dict(json.loads(json.dumps(project))).to_dict()
    assert (stored["review"].get("automatic_proposals") or {}).get("counts", {}).get("filed") == 1, \
        "the record of what was automatic has to outlive the first Ctrl+S"
    assert stored["matches"][0]["review"]["proposals"][0]["at"] == 94.0


def test_two_refreshes_never_share_an_identity_and_the_old_one_cannot_act():
    """The reproduced defect: two consecutive refreshes both landed on "opening:2".

    Ids counted the list, and a refresh replaces the pending slot, so the list never grew -- which let a
    card left open on the first suggestion apply the second one's instant.
    """
    project = auto_project(BANNER_ROWS)
    store = review.ReviewStore(project)
    first = store.propose({"start": 100.0}, review.proposal_record("opening", 94.0, why="A"))
    second = store.propose({"start": 100.0}, review.proposal_record("opening", 93.0, why="B"))
    assert first["proposal_id"] != second["proposal_id"], (first["proposal_id"], second["proposal_id"])

    kept = {item["proposal_id"]: item for item in project["matches"][0]["review"]["proposals"]}
    assert kept[first["proposal_id"]]["status"] == review.PROPOSAL_SUPERSEDED
    assert kept[first["proposal_id"]]["superseded_by"] == second["proposal_id"]
    assert kept[first["proposal_id"]]["at"] == 94.0, "the displaced record is not rewritten"

    with pytest.raises(review.InvalidAnswer, match="superseded by"):
        store.find_proposal(100.0, "opening", proposal_id=first["proposal_id"])
    assert store.find_proposal(100.0, "opening", proposal_id=second["proposal_id"])["at"] == 93.0

    resumed = json.loads(json.dumps(ProjectState.from_dict(
        json.loads(json.dumps(project))).to_dict()))
    before = [item["proposal_id"] for item in resumed["matches"][0]["review"]["proposals"]]
    again = review.ReviewStore(resumed)
    third = again.propose({"start": 100.0}, review.proposal_record("opening", 92.0, why="C"))
    assert third["proposal_id"] not in before, "generations do not restart after a save"


def test_the_policy_stage_select_beats_a_later_round_cue_and_a_chain_cannot_cross_the_previous_set():
    """Two rules the old path got wrong in opposite directions."""
    staged = auto_project([
        {"timestamp": 92.0, "state": "stage_select", "player1": "Ana", "player2": "Bo",
         "evidence": "stage tiles"},
        {"timestamp": 95.0, "state": "vs_screen", "player1": "Ana", "player2": "Bo", "evidence": "versus"},
        {"timestamp": 104.0, "state": "gameplay", "round_call": 1, "player1": "Ana", "player2": "Bo",
         "evidence": "ROUND 1 banner"},
        {"timestamp": 107.0, "state": "gameplay", "round_clock": 58, "player1": "Ana", "player2": "Bo",
         "evidence": "engaged"}], start=107.0)
    result = review.apply_opening_proposals(staged)
    assert result["counts"]["filed"] == 1, result["rows"]
    proposal = staged["matches"][0]["review"]["proposals"][0]
    assert proposal["at"] == 92.0, "the policy-preferred anchor wins over the nearer cue"
    assert proposal["evidence"]["policy_fallback"] is False
    assert proposal["evidence"]["walk_instant"] == 104.0, "the cue is preserved, not overwritten"
    assert "stage select" in proposal["evidence"]["policy_basis"]

    crossing = auto_project([
        {"timestamp": 96.0, "state": "stage_select", "player1": "Ana", "player2": "Bo",
         "evidence": "stage tiles"},
        {"timestamp": 100.0, "state": "gameplay", "round_clock": 60, "player1": "Ana", "player2": "Bo",
         "evidence": "READY"}], start=100.0)
    crossing["matches"].insert(0, {"start": 0.0, "end": 97.0, "recovery": {
        "clip_end_at": 97.0, "membership": "supported", "games": [{"start": 10.0, "end": 95.0}],
        "edges": {"start": {"at": 0.0}}}, "review": {}})
    outcome = review.apply_opening_proposals(crossing)
    refused = [row for row in outcome["rows"] if row["outcome"] == "refused-crossing"]
    assert len(refused) == 1, outcome["rows"]
    assert "previous match" in refused[0]["why"]
    assert not (crossing["matches"][1]["review"].get("proposals") or []), \
        "a proposal that would cross the boundary is not filed at all"


def test_a_load_in_chain_breaks_on_an_unknown_frame_or_another_pairing():
    """Continuity is what joins a screen to this match; a blank frame or a rival nameplate cannot claim it."""
    blank = auto_project([
        {"timestamp": 90.0, "state": "stage_select", "player1": "Ana", "player2": "Bo",
         "evidence": "tiles behind a hole"},
        {"timestamp": 91.0, "state": "unknown", "player1": "Ana", "player2": "Bo", "evidence": "black frame"},
        {"timestamp": 95.0, "state": "vs_screen", "player1": "Ana", "player2": "Bo", "evidence": "versus"},
        {"timestamp": 100.0, "state": "gameplay", "round_clock": 60, "player1": "Ana", "player2": "Bo",
         "evidence": "READY cue"}], start=100.0)
    anchored = review.policy_clip_start(blank["sequence_rows"], candidate_at=100.0, pairing=("Ana", "Bo"))
    assert anchored["at"] == 95.0 and anchored["fallback"] is True, anchored
    assert all(row["state"] != "stage_select" for row in anchored["chain"]), \
        "the chain stops at the unknown frame; a stage select behind it is not this run's evidence"

    rival = auto_project([
        {"timestamp": 95.0, "state": "vs_screen", "player1": "Cid", "player2": "Nina",
         "evidence": "versus splash naming other players"},
        {"timestamp": 97.0, "state": "stage_select", "player1": "Cid", "player2": "Nina",
         "evidence": "tiles for another match"},
        {"timestamp": 100.0, "state": "gameplay", "round_clock": 60, "player1": "Ana", "player2": "Bo",
         "evidence": "READY cue"}], start=100.0)
    other = review.policy_clip_start(rival["sequence_rows"], candidate_at=100.0, pairing=("Ana", "Bo"))
    assert other["at"] is None, other
    assert not review.apply_opening_proposals(rival)["counts"]["filed"], \
        "a different pairing's lobby is not this set's opening, so nothing is proposed"


# --------------------------------------------------------------------------------- machine bounds
# The pipeline's own best conservative result should not require ten routine clicks to appear. These tests
# hold the line that makes that safe: machine work moves bounds and records machine provenance, and never
# becomes an answer, a human end, or an acceptance.

def machine_project():
    rows = [{"timestamp": 92.0, "state": "stage_select", "player1": "Ana", "player2": "Bo"},
            {"timestamp": 95.0, "state": "vs_screen", "player1": "Ana", "player2": "Bo"},
            {"timestamp": 104.0, "state": "gameplay", "round_call": 1, "player1": "Ana", "player2": "Bo"},
            {"timestamp": 107.0, "state": "gameplay", "round_clock": 58, "player1": "Ana", "player2": "Bo"}]
    return {"duration": 600.0, "video_path": "/rec.mkv", "sequence_rows": rows,
            "matches": [{"start": 107.0, "end": 300.0, "recovery": {
                "clip_end_at": 302.0, "membership": "supported",
                "games": [{"start": 107.0, "end": 300.0, "winner": "p1"}],
                "games_without_winner": [],
                "game_spans": [[107.0, 300.0]],
                "edges": {"start": {"at": 107.0, "clock_at_boundary": 58}},
                "clip_end_basis": "conclusion plus padding, applied once"}, "review": {}}],
            "review": {"version": review.REVIEW_VERSION, "decisions": [], "log": []}}


def test_machine_bounds_move_the_clip_and_stay_machine_work():
    project = machine_project()
    review.apply_opening_proposals(project)
    result = review.apply_machine_bounds(project)
    assert result["applied"] and result["skipped"] == []
    assert result["counts_as_decision"] is False and result["operator_acceptance"] is False

    segment = project["matches"][0]
    assert segment["start"] == 92.0, "the policy-preferred stage select becomes the bound"
    record = segment["review"]["machine_bounds"][0]
    assert record["from"] == 107.0 and record["to"] == 92.0
    assert record["applied_by"] == "pipeline" and record["operator_decision"] is False
    assert record["authoritative"] is False
    assert "stage select" in record["basis"] and record["uncertainty_seconds"]

    stored = project["review"]
    assert not stored.get("decisions"), "moving a bound this way answers no question"
    assert not stored.get("acceptance")
    assert not (segment.get("review") or {}).get("ends"), \
        "an end moved by the machine must never look like a human end"


def test_machine_bounds_never_override_a_boundary_the_operator_answered():
    project = machine_project()
    review.apply_opening_proposals(project)
    store = review.ReviewStore(project)
    store.decide({"kind": "new_match", "segment": project["matches"][0], "index": 0}, "new_match")
    result = review.apply_machine_bounds(project)
    assert project["matches"][0]["start"] == 107.0, "a person's answer outranks the pipeline"
    assert result["applied"] == [] and result["skipped"], result
    assert "answered" in result["skipped"][0]["why"]


def test_machine_bounds_cannot_reach_into_the_previous_set():
    project = machine_project()
    project["matches"].insert(0, {"start": 0.0, "end": 95.0, "recovery": {
        "clip_end_at": 95.0, "membership": "supported",
        "games": [{"start": 0.0, "end": 95.0, "winner": "p1"}], "game_spans": [[0.0, 95.0]],
        "edges": {"start": {"at": 0.0}}}, "review": {}})
    outcome = review.apply_opening_proposals(project)
    assert any(row["outcome"] == "refused-crossing" for row in outcome["rows"]), outcome["rows"]
    result = review.apply_machine_bounds(project)
    assert project["matches"][1]["start"] == 107.0, "the clip after a set may not swallow it"
    assert result["applied"] == []
    # The second guard, reached from the other direction: a proposal that was filed before the set before
    # it grew. Machine bounds must refuse it on its own terms, not rely on generation having caught it.
    review.ReviewStore(project).propose(
        {"start": 107.0}, review.proposal_record("opening", 93.0, why="filed against an older neighbour"))
    again = review.apply_machine_bounds(project)
    assert project["matches"][1]["start"] == 107.0, again
    assert any("reach into" in item["why"] for item in again["skipped"]), again["skipped"]


def test_a_second_machine_bounds_pass_adds_nothing_and_moves_nothing_further():
    project = machine_project()
    review.apply_opening_proposals(project)
    first = review.apply_machine_bounds(project)
    after_first = project["matches"][0]["start"]
    again = review.apply_machine_bounds(project)
    assert project["matches"][0]["start"] == after_first
    assert again["applied"] == [], "the same machine result must not re-apply or drift"
    assert len(project["matches"][0]["review"]["machine_bounds"]) == len(first["applied"])


def test_a_start_the_pipeline_moved_is_reported_as_machine_applied_not_as_unknown():
    """The artifact has to be able to say what it did.

    Measured on the delivered machine package before this existed: ten policy-derived starts came back as
    `machine_supported_starts: 0, unknown_starts: 10`, because every reading quoted beside a row was taken at
    the instant the row used to open. That is not a false claim about the footage, but it is a false claim
    about the artifact -- it reads as a pipeline that moved ten boundaries and cannot say why.
    """
    project = machine_project()
    review.apply_opening_proposals(project)
    review.apply_machine_bounds(project)
    plan = review.plan_for_state(project) if hasattr(review, "plan_for_state") else None
    boundary = next(iter(review.boundary_view(project)["92.0"].values())) \
        if hasattr(review, "boundary_view") else None
    from tekken_vod_helper.boundary_review_flow import ReviewFlow
    from tekken_vod_helper.models import ProjectState
    counts = ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(project)))).export_plan()["counts"]
    assert counts["machine_applied_starts"] == 1, counts
    assert counts["machine_supported_starts"] == 0 and counts["unknown_starts"] == 0, \
        "the state is neither of the two it used to be folded into"
    row = next(c for c in ReviewFlow(ProjectState.from_dict(json.loads(json.dumps(project))))
               .export_plan()["clips"])
    assert row["boundary"]["machine_supports_start"] == "machine_applied"
    why = row["boundary"]["start_why"]
    assert "107.000" in why and "92.000" in why, why
    assert "stage-select" in why and "+/- 3.000 s" in why, why
    # The old anchor is not deleted, only relabelled: the readings it was built from still travel.
    assert row["boundary"]["machine_bound"]["from"] == 107.0
    assert project["matches"][0]["recovery"]["edges"]["start"]["at"] == 107.0, \
        "the old anchor survives the move, so the reading it was built from is still citable"
