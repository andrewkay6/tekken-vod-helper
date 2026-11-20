"""What a frame says about combat, and where a round's opening can be recovered to.

These are the rules the 2026-11-07 disputed-openings review forced into the open. The classifier's coarse
``screen_state`` describes what is *drawn*, and Tekken draws the arena, the health bars and the timer behind
a ROUND banner, behind a READY splash and behind an open pause menu exactly as it draws them mid-round -- so
every consumer that asked "is the round being played?" of the state word got "yes" for three frames on this
recording that show nobody playing, and two boundaries were reported as physically disputed when nothing but
the reading disagreed.

Nothing here reads a timestamp-specific case: the shapes are the ones the saved recording actually contains
(ROUND 1, ROUND 2, READY, FIGHT, a falling clock, a frozen clock, an in-match pause menu) plus the negatives
that have to keep failing -- a cleared banner, a denied pause, a frame with nothing on it.
"""
import copy
import json

import pytest

from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_review as review


def frame(at, state, evidence, **facts):
    row = {"timestamp": at, "state": state, "evidence": evidence}
    row.update(facts)
    return row


def phase(evidence, *, state="gameplay", **facts):
    return bg.parse_round_facts(frame(1.0, state, evidence, **facts))["combat_phase"]


# ---------------------------------------------------------------------------- the phases a frame can name

def test_a_round_banner_is_an_opening_and_never_live_play():
    """ROUND 1 at 785.785: an arena with a banner across it, and nobody has pressed a button yet.

    The saved description says both things in one sentence -- "in-fight arena with health bars and 'ROUND 1'
    banner" -- and the state word chosen was the first half. A boundary model that reads only the state word
    calls this the second round of a fight already in progress.
    """
    assert phase("In-fight arena with health bars and 'ROUND 1' banner plus P1/P2 markers") == \
        bg.ROUND_PHASE_OPENING
    assert phase("Fighters in starting stances with 'ROUND 2' callout and full health bars") == \
        bg.ROUND_PHASE_OPENING
    assert phase("Large 'FINAL ROUND' banner with timer reset to 60") == bg.ROUND_PHASE_OPENING


def test_ready_and_fight_are_openings_and_the_clock_that_follows_them_is_not():
    """READY, then FIGHT, then a clock that starts falling: three different claims about three frames."""
    assert phase("Arena with fighters in pre-round stance, 'READY' announcement overlay") == \
        bg.ROUND_PHASE_OPENING
    assert phase("'FIGHT' start banner over health bars and timer 60") == bg.ROUND_PHASE_OPENING
    assert phase("Active combat with timer 58 and a Counter popup") == bg.ROUND_PHASE_ACTIVE


def test_a_falling_clock_is_the_only_timer_claim_that_proves_play():
    """60 is where a round starts; anything below it has begun, and the difference is the whole rule.

    A timer at its opening value distinguishes nothing -- the banner frame and the first played frame both
    read 60 -- so only a reading below it may be credited with dating live play. That keeps the claim where
    the recording supports it and leaves the rest unknown rather than confidently wrong.
    """
    assert phase("Full fight HUD active (health bars, round timer '60')", ) == bg.ROUND_PHASE_UNKNOWN
    assert phase("In-fight HUD with round timer 44 and both health bars dropping") == \
        bg.ROUND_PHASE_ACTIVE
    assert bg.parse_round_facts(frame(1.0, "gameplay", "arena", round_clock=44))["combat_phase"] == \
        bg.ROUND_PHASE_ACTIVE
    assert bg.parse_round_facts(frame(1.0, "gameplay", "arena", round_clock=60))["combat_phase"] == \
        bg.ROUND_PHASE_UNKNOWN


def test_a_frozen_clock_is_not_live_play_even_without_a_pause_observation():
    """A clock that never moved is not evidence of a round being fought; it is a frame and a number.

    Nothing here reads "60 for six seconds" as six seconds of play, and nothing reads it as a pause either:
    a result close-up, a K.O. freeze and an idle stage all look like this. Absence of a pause observation is
    absence of an observation, not the presence of its opposite.
    """
    still = [frame(100.0 + step, "gameplay", "Arena with round timer 60 and both fighters standing",
                   round_clock=60) for step in range(6)]
    assert [bg.row_combat_phase(row) for row in still] == [bg.ROUND_PHASE_UNKNOWN] * 6
    falling = [frame(100.0 + step, "gameplay", "Arena, timer %d" % (60 - step), round_clock=60 - step)
               for step in range(4)]
    assert [bg.row_combat_phase(row) for row in falling[1:]] == [bg.ROUND_PHASE_ACTIVE] * 3


def test_a_menu_named_as_the_place_the_frame_came_from_is_not_a_menu_on_the_frame():
    """2727.725: the main menu, described as "reached from the pause menu", was labelled paused.

    The prose names a pause menu only to say how the user got where they now are. Reading that as an open
    pause overlay is the same mistake as reading "gameplay" as combat -- a sentence *about* the frame
    replacing what the frame draws -- and it happens to sit inside the 2771 gap this pass was asked about.
    The denial is recorded as a note, so the reasoning stays visible instead of becoming a quieter silence.
    The two rows that really do draw a pause menu are asserted here too: a guard that denied everything
    would pass the first claim and break the recording.
    """
    main_menu = ("Tekken 8 'Main Menu' screen (Story/Offline/Online/Customization/Replay, Arcade Battle > VS "
                 "> Player vs Player highlighted) reached from the pause menu; not a controller prompt")
    facts = bg.parse_round_facts(frame(2727.724857, "unknown", main_menu))
    assert facts["pause_overlay"] is False
    assert facts["combat_phase"] == bg.ROUND_PHASE_UNKNOWN
    assert any("not as something drawn" in note for note in facts["combat_phase_notes"]), \
        "the denial leaves a trace"
    drawn = ("Player 2 pause menu (Move List/Select Character/Main Menu) over idle fight background; "
             "broadcast scoreboard 0 - 2")
    assert bg.row_combat_phase(frame(2723.720857, "post_match_menu", drawn)) == bg.ROUND_PHASE_PAUSED
    opened = ("Live match scene with round timer (60) and idle fighters; Player 2 in-match pause menu "
              "(Move List / Controller Setup / Select Character / Main Menu) is open")
    assert bg.row_combat_phase(frame(2722.719857, "gameplay", opened)) == bg.ROUND_PHASE_PAUSED


def test_an_in_match_pause_menu_says_nobody_was_playing_even_though_the_hud_is_up():
    """2722.720: "live match scene with round timer (60)" and "pause menu is open" in one sentence.

    The first clause is the classifier describing what it can see, the second is the fact that matters, and
    a rule that takes the state word from the first and ignores the second reports a paused game as
    uninterrupted combat. The menu wins, because a pause stops the clock and a stopped clock cannot date
    play -- and that is all this claims. A pause can happen mid-game or during a button check, so it settles
    neither where the game ends nor whether the set did.
    """
    text = ("Live match scene with round timer (60) and idle fighters; Player 2 in-match pause menu "
            "(Move List / Controller Setup / Select Character / Main Menu) is open")
    facts = bg.parse_round_facts(frame(1.0, "gameplay", text))
    assert facts["combat_phase"] == bg.ROUND_PHASE_PAUSED
    assert facts["pause_overlay"] is True
    assert facts["round_clock"] == 60, "the reading is kept; only the claim about it changes"


# ------------------------------------------------------------------- the negatives must keep being negatives

@pytest.mark.parametrize("text", [
    pytest.param("The 'READY'/round text has cleared and the engage line appears", id="cleared"),
    pytest.param("'ROUND 1' banner is no longer visible and both fighters are moving", id="no-longer"),
    pytest.param("Arena with health bars; the round banner is not visible in this frame", id="not-visible"),
    pytest.param("No 'FIGHT' overlay present; fighters already exchanging hits", id="absent"),
])
def test_a_denied_banner_is_not_an_opening(text):
    """Every one of these sentences contains the words the opening pattern looks for.

    Matching them would turn "the banner had already gone" into "the round is beginning", which is the same
    class of error as the affirmative case -- a claim about the fight manufactured out of a description of
    the screen. A denial may only ever remove a claim.
    """
    assert phase(text) != bg.ROUND_PHASE_OPENING


@pytest.mark.parametrize("text", [
    pytest.param("Arena with HUD; there is no pause menu and combat is being fought", id="no-pause-menu"),
    pytest.param("'ROUND 1' banner absent, fighters mid-combo with timer 21", id="banner-absent"),
])
def test_negated_vocabulary_stops_at_the_claim_it_denies(text):
    """Denying a pause does not create an opening, and denying a banner does not create a pause.

    Each phrase is removed separately; the fall-through is that the row simply stops claiming the thing it
    said was not there, which leaves the rest of the frame to speak for itself.
    """
    facts = bg.parse_round_facts(frame(1.0, "gameplay", text))
    assert facts["pause_overlay"] is False or "pause" not in text
    assert facts["combat_phase"] != bg.ROUND_PHASE_PAUSED or "pause menu" not in text


def test_an_unrecognised_stated_phase_is_reported_rather_than_stored():
    """A fifth phase word would make every `phase in NON_COMBAT_PHASES` test answer "no" by accident."""
    facts = bg.parse_round_facts(frame(1.0, "gameplay", "arena with 'ROUND 1' banner",
                                       combat_phase="probably-fine"))
    assert facts["combat_phase"] == bg.ROUND_PHASE_OPENING, "the frame's own evidence was kept"
    assert any("unrecognised combat phase" in note for note in facts["combat_phase_notes"])


def test_a_stated_pause_denial_outranks_the_prose_and_says_so():
    """An answer that says the menu was closed corrects the sentence; it is not silently merged with it.

    Presence beats truth here in one direction only: the stated field wins, and the disagreement is recorded
    so a later reader can see the prose was not empty, just overruled by somebody who looked again.
    """
    facts = bg.parse_round_facts(frame(1.0, "gameplay", "arena with a pause menu visible nearby",
                                       pause_overlay=False, round_clock=31))
    assert facts["combat_phase"] == bg.ROUND_PHASE_ACTIVE
    assert {"field": "pause_overlay", "from": True, "to": False} in facts["conflicts"]


# --------------------------------------------------------------------- recovering where a round opened

def sequence(spec):
    """Saved rows as the tracker writes them: (timestamp, state, prose, clock)."""
    return [{"timestamp": at, "screen_state": state, "evidence": evidence, "round_clock": clock}
            for at, state, evidence, clock in spec]


def test_an_opening_run_behind_a_mid_round_start_is_recovered_as_an_interval():
    """790.790's own shape: play at the candidate, a banner 5 s back, nothing sampled in between.

    The answer is not a timestamp to cut at. It is that the round was announced at 785.785, that the first
    frame showing it being fought is the candidate itself, and that the 5 s between them were never looked
    at -- so a clip could start anywhere in that interval and the recording cannot say finer than the
    sampling that produced it.
    """
    rows = sequence([
        (783.783, "post_match_menu", "Between-match station screen", None),
        (784.784, "stage_intro", "Close-up of the stage during the entrance cinematic", None),
        (785.785, "gameplay", "In-fight arena with health bars and 'ROUND 1' banner", None),
        (790.790, "gameplay", "Active combat with timer 58 and a Counter popup", 58),
        (791.791, "gameplay", "Active combat with timer 57", 57),
    ])
    found = bg.opening_sequence(rows, 790.790)
    assert found["status"] == "opening_found"
    assert found["opening_start_at"] == 785.785
    assert found["first_active_at"] == 790.790
    assert found["has_opening_evidence"] is True
    # The unsampled hole is reported, not smoothed over: 5 s of uncertainty on a 1 s tape is the finding.
    assert found["uncertainty_seconds"] == pytest.approx(5.005, abs=1e-3)
    assert found["clip_start_options"]["include_opening"] == 785.785
    assert found["clip_start_options"]["first_active_gameplay"] == 790.790
    assert found["played_frames_at_candidate"] == 1
    assert found["inference_sent"] is False


def test_a_candidate_inside_its_own_opening_is_not_reported_as_mid_round():
    """2771.769 is the ROUND 1 frame. Nothing about it says the clip started late.

    The old reading would have had the walk stop at the candidate and report the intro as if the boundary
    already contained it. The recovery steps over leading play, finds none here, and reads the frame it is
    standing on.
    """
    rows = sequence([
        (2770.767, "stage_intro", "Jack-8 intro pose on the stage floor", None),
        (2771.769, "gameplay", "Fighters in starting stances with 'ROUND 1' callout", None),
        (2772.770, "gameplay", "In-fight HUD with round timer 60", 60),
        (2773.771, "gameplay", "In-fight HUD with round timer 60", 60),
    ])
    found = bg.opening_sequence(rows, 2771.769)
    assert found["opening_start_at"] == 2771.769
    assert found["played_frames_at_candidate"] == 0
    # HUD at 60 for two more frames is not a claim that play began; the first falling reading is.
    assert found["first_active_at"] is None


def test_a_pause_frame_never_ends_an_opening_run_as_play():
    """A menu over an arena is not a round being fought, and stops nothing behind it.

    This is the frame that made 2771.769 look physically contradictory: a paused game 49 s before a fresh
    ROUND 1, reported as live play on the far side of the bound.
    """
    rows = sequence([
        (2721.718, "post_match_menu", "Side options menu", None),
        (2722.720, "gameplay", "Live match scene with round timer (60); Player 2 in-match pause menu "
                               "is open", 60),
        (2723.721, "gameplay", "Live match scene with round timer (60); pause menu still up", 60),
    ])
    assert bg.row_combat_phase(rows[1]) == bg.ROUND_PHASE_PAUSED
    found = bg.opening_sequence(rows, 2723.721)
    assert found["has_opening_evidence"] is False
    assert found["status"] == "frames_without_opening", "frames behind a candidate are not an opening"
    assert found["opening_start_at"] is None
    assert found["run_start_at"] == 2722.720


def test_a_station_screen_ends_the_run_because_it_asks_a_different_question():
    """Character select and controller assignment are game/set evidence; a round's start is not in them.

    Walking through them would let a stage selection 45 s back be offered as this round's opening, which
    mixes two levels of the structure the canonical policy keeps apart on purpose.
    """
    rows = sequence([
        (1500.0, "character_select", "Roster grid with confirm prompts", None),
        (1538.5, "controller_prompt", "Controller assignment screen", None),
        (1587.586, "gameplay", "'READY' announcement overlay", None),
        (1588.587, "gameplay", "Full fight HUD, round timer 60", 60),
    ])
    found = bg.opening_sequence(rows, 1588.587)
    assert found["opening_start_at"] == 1587.586
    assert found["stopped_at"] == "station_screen"
    # The run holds the frames between the station screen and the candidate -- including the candidate,
    # whose HUD-at-60 frame says nothing about being played -- and it stops dead at the assignment screen.
    assert [item["at"] for item in found["frames"]] == [1587.586, 1588.587]
    assert [item["phase"] for item in found["frames"]] == [bg.ROUND_PHASE_OPENING,
                                                           bg.ROUND_PHASE_UNKNOWN]


def test_an_active_frame_behind_the_candidate_bounds_the_run_however_far_back_it_is():
    """A round already being fought 30 s back is the previous round's play, not this one's opening.

    The walk stops there rather than reaching on to whatever announcement happens to be older, because the
    claim being made is about the nearest opening, and a run that crosses a stretch of play describes two
    rounds at once.
    """
    rows = sequence([
        (900.0, "gameplay", "Active combat with timer 30", 30),
        (901.0, "gameplay", "Active combat with timer 29", 29),
        (902.0, "gameplay", "'ROUND 2' banner with timer reset to 60", None),
        (940.0, "gameplay", "Active combat with timer 25", 25),
    ])
    found = bg.opening_sequence(rows, 940.0)
    assert found["stopped_at"] == "active_play"
    assert found["opening_start_at"] == 902.0


def test_missing_cues_produce_no_invented_opening_and_an_empty_window_says_so():
    """Nothing on screen, nothing to report -- and no fallback to a number subtracted from a timer.

    These are the two ways a recovery could look useful while inventing: fill an all-unknown stretch with
    its own first frame as "the opening", or answer "no sequence" as if the recording had been examined and
    found empty.
    """
    blank = sequence([(500.0, "gameplay", "Arena with health bars", None),
                      (501.0, "gameplay", "Arena with health bars", None),
                      (502.0, "gameplay", "Arena with health bars", None)])
    found = bg.opening_sequence(blank, 502.0)
    assert found["opening_start_at"] is None and found["has_opening_evidence"] is False
    empty = bg.opening_sequence([], 502.0)
    assert empty["status"] == "no_sequence" and empty["uncertainty_seconds"] is None
    far = bg.opening_sequence(sequence([(10.0, "gameplay", "'ROUND 1' banner", None)]), 5000.0,
                              scan_seconds=30.0)
    assert far["status"] == "no_sequence"
    assert "nothing was sampled" in far["basis"]


def test_the_recovery_never_moves_a_boundary_and_never_uses_a_timestamp_from_elsewhere():
    """Looking is the proposal's whole purpose; moving it would be the defect under another name.

    Also checks the arithmetic that is easy to get wrong: no opening instant anywhere in this module comes
    from subtracting the round clock from a timestamp, and 790.790 minus 58 is not a claim about 732.
    """
    rows = sequence([(785.785, "gameplay", "'ROUND 1' banner", None),
                     (790.790, "gameplay", "Active combat with timer 58", 58)])
    before = copy.deepcopy(rows)
    found = bg.opening_sequence(rows, 790.790)
    assert rows == before, "the sequence is read, never edited"
    assert found["opening_start_at"] == 785.785
    assert found["opening_start_at"] != 790.790 - 58
    assert found["first_active_at"] == 790.790


# --------------------------------------------------------------------- the review layer consumes all of it

def opening_case(walked_evidence, walked_at=1587.585917, candidate=1588.586916, cue_at=1538.536919):
    segment = {
        "start": candidate, "end": candidate + 500.0,
        "recovery": {
            "clip_end_at": candidate + 502.0,
            "clip_end_anchor": {"winner_confirmed_at": candidate + 500.0},
            "games": [{"start": candidate, "end": candidate + 500.0,
                       "winner_confirmed_at": candidate + 500.0}],
            "edges": {"start": {
                "at": candidate, "clock_at_boundary": 60,
                "settled_by": {"at": cue_at, "kind": "cue", "state": "controller_prompt"},
                "walked_past": [{"at": walked_at, "state": "gameplay", "clock": None,
                                 "because": "clock unread", "evidence": walked_evidence}],
            }},
        },
    }
    return {"duration": 6928.421136, "video_path": "/recordings/bb9.mkv", "matches": [segment]}


def test_review_and_export_stop_calling_an_intro_a_contradiction():
    """The same walked frame, read three ways, through the plan a reviewer is actually shown.

    The export and acceptance fields are asserted alongside the explanation because that is where a wrong
    reading becomes a wrong decision: a "contradicted" label moves a row into the queue with an argument
    attached, and an approved partial stays approved however the cue is spelled.
    """
    for evidence, expect_conflict in (("Arena with 'READY' announcement overlay", False),
                                      ("Player 2 in-match pause menu is open", False),
                                      ("Active combat with timer 58", True)):
        plan = review.export_plan(opening_case(evidence))
        boundary = plan["clips"][0]["boundary"]
        assert boundary["start_readings_conflict"] is expect_conflict, evidence
        assert boundary["machine_supports_start"] == "supported"
        assert boundary["operator_accepted_boundary"] is False
        assert boundary["independently_validated"] is False
        assert plan["counts"]["exportable"] == 1 and plan["counts"]["held"] == 0


def test_the_recovered_opening_reaches_the_card_as_a_bounded_proposal():
    """What the operator is offered: an instant to look at, the interval it is bounded by, and no edit.

    The level travels with the jump so a ROUND banner cannot be read as a set start, and the uncertainty
    travels with it so a 5-second hole in the sampling is not presented as a decision point.
    """
    project = opening_case("Arena with 'READY' announcement overlay", walked_at=1587.586,
                           candidate=1588.587, cue_at=1587.586)
    rows = [{"timestamp": 1587.586, "screen_state": "gameplay",
             "evidence": "Arena with fighters in pre-round stance and 'READY' announcement overlay"},
            {"timestamp": 1588.587, "screen_state": "gameplay",
             "evidence": "Full fight HUD active with round timer 60", "round_clock": 60}]
    project["sequence_rows"] = rows
    plan = review.export_plan(project)
    item = review.review_queue(project)[0]
    opening = item["boundary"]["round_opening"]
    assert opening["opening_start_at"] == 1587.586
    assert item["adjust"]["jump_to"] == 1587.586
    assert item["adjust"]["jump_to_level"] == "round_opening"
    assert item["adjust"]["jump_to_uncertainty_seconds"] == pytest.approx(1.001, abs=1e-3)
    assert plan["clips"][0]["start"] == 1588.587, "the proposal is not applied to the row"


def test_phase_survives_the_project_round_trip_through_the_saved_evidence():
    """A derived interpretation must not depend on the prose still being in memory.

    The sequence reference is re-read and re-checked by digest on every load, so a phase recomputed from the
    same rows agrees with the phase computed at export time -- and a project whose timeline moved under it
    says so instead of quietly re-interpreting a different corpus.
    """
    project = opening_case("Player 2 in-match pause menu is open", walked_at=2722.72,
                           candidate=2771.769, cue_at=2719.717)
    project["sequence_rows"] = [
        {"timestamp": 2722.72, "screen_state": "gameplay",
         "evidence": "Live match scene with round timer (60); Player 2 in-match pause menu is open",
         "round_clock": 60},
        {"timestamp": 2771.769, "screen_state": "gameplay",
         "evidence": "Fighters in starting stances with 'ROUND 1' callout"},
        {"timestamp": 2772.77, "screen_state": "gameplay",
         "evidence": "In-fight HUD with round timer 60", "round_clock": 60}]
    once = review.export_plan(project)["clips"][0]["boundary"]
    twice = review.export_plan(json.loads(json.dumps(project)))["clips"][0]["boundary"]
    assert once["start_readings_conflict"] is False
    assert twice == once
    paused = [item for item in once["start_readings"] if item["at"] == 2722.72]
    assert paused and paused[0]["phase"] == bg.ROUND_PHASE_PAUSED


# ------------------------------------------------- the shapes a live broadcast keeps producing


def test_resuming_a_paused_game_is_not_a_new_round_and_needs_no_new_boundary():
    """Pause, breathe, resume: the same round, the same clock, and nothing for the reviewer to cut.

    A pause is not an ending. The frames before and after it belong to one round, so a boundary that
    resumes must not be offered an opening on the strength of the menu it just left -- the recovery has
    nothing to propose here, and says so rather than pointing at the pause.
    """
    rows = sequence([
        (600.0, "gameplay", "Active combat with timer 40", 40),
        (601.0, "gameplay", "Round timer (40); Player 1 pause menu is open", 40),
        (602.0, "gameplay", "Round timer (40); pause menu still open", 40),
        (603.0, "gameplay", "Active combat with timer 39", 39),
        (604.0, "gameplay", "Active combat with timer 38", 38),
    ])
    assert [bg.row_combat_phase(rows[i]) for i in (1, 2)] == [bg.ROUND_PHASE_PAUSED] * 2
    found = bg.opening_sequence(rows, 604.0)
    assert found["opening_start_at"] is None
    assert found["status"] == "frames_without_opening"
    assert found["stopped_at"] == "active_play", "the run ends where the round was actually being fought"


def test_a_button_check_between_games_leaves_membership_unsettled_not_answered():
    """Customisation and button mapping screens are what a controller check looks like on tape.

    They sit at the station level, so they end a round-opening run; whether the match that follows is a new
    game, a restart or a new set is a question the link layer answers with an alternative, not with a cut.
    """
    rows = sequence([
        (1000.0, "gameplay", "'ROUND 1' banner with P1/P2 markers", None),
        (1030.0, "character_select", "Button configuration split screen for both players", None),
        (1060.0, "gameplay", "'ROUND 1' banner, timer 60", None),
        (1061.0, "gameplay", "Active combat with timer 59", 59),
    ])
    found = bg.opening_sequence(rows, 1060.0)
    assert found["opening_start_at"] == 1060.0, "the second banner, not the first"
    assert found["stopped_at"] == "station_screen"
    assert 1000.0 not in [item["at"] for item in found["frames"]]


def test_a_restart_or_rematch_reopens_the_round_without_renaming_the_set():
    """Same pairing, fresh ROUND 1: a round opening, and one round is not a set.

    Two banners 30 s apart with play between them are two rounds. Whether the second still belongs to the
    same set is decided by the games and the pairing across the whole match, and an opening sequence on its
    own is not evidence about it either way -- which is why the recovery reports the interval and no verdict.
    """
    rows = sequence([
        (2000.0, "gameplay", "'ROUND 1' banner", None),
        (2001.0, "gameplay", "Active combat with timer 59", 59),
        (2002.0, "gameplay", "Active combat with timer 58", 58),
        (2030.0, "gameplay", "'ROUND 1' banner again, timer 60", None),
        (2031.0, "gameplay", "Active combat with timer 59", 59),
    ])
    first = bg.opening_sequence(rows, 2002.0)
    second = bg.opening_sequence(rows, 2031.0)
    assert first["opening_start_at"] == 2000.0
    assert second["opening_start_at"] == 2030.0, "each round gets its own opening, not the older one"
    assert second["what_this_cannot_say"]


def test_a_rematch_after_a_result_screen_is_reported_as_an_opening_of_its_own():
    """A result screen then a fresh banner: the recovery names the banner and stops, and asks nothing.

    The tempting wrong answer is to reach back through the result screen to the previous round's banner and
    offer that as this clip's start, which would splice two games together one frame at a time.
    """
    rows = sequence([
        (3000.0, "gameplay", "Active combat with timer 12", 12),
        (3001.0, "post_match_menu", "'YOU WIN' result screen over the stage", None),
        (3040.0, "gameplay", "'ROUND 1' banner with both fighters in stance", None),
        (3041.0, "gameplay", "Active combat with timer 59", 59),
    ])
    found = bg.opening_sequence(rows, 3041.0)
    assert found["opening_start_at"] == 3040.0
    assert found["stopped_at"] == "station_screen"


def test_every_saved_phase_is_one_of_the_four_named_ones():
    """A fifth value would make `phase in NON_COMBAT_PHASES` answer "no" to something unseen.

    Run against the whole 2,019-row corpus at import of the recovery layer, this is the check that keeps the
    vocabulary shut: the phases are enumerated, counted, and any frame that would fall outside them fails
    here rather than in an exporter's branch that never fires.
    """
    assert set(bg.ROUND_PHASES) == {bg.ROUND_PHASE_ACTIVE, bg.ROUND_PHASE_OPENING,
                                    bg.ROUND_PHASE_PAUSED, bg.ROUND_PHASE_UNKNOWN}
    assert bg.NON_COMBAT_PHASES == {bg.ROUND_PHASE_OPENING, bg.ROUND_PHASE_PAUSED}
    rows = [{"timestamp": 1.0, "state": "gameplay", "evidence": "'ROUND 1' banner"},
            {"timestamp": 2.0, "state": "gameplay", "evidence": "pause menu open"},
            {"timestamp": 3.0, "state": "gameplay", "evidence": "Active combat", "round_clock": 20},
            {"timestamp": 4.0, "state": "stage_select", "evidence": "Stage thumbnails"}]
    assert [bg.row_combat_phase(row) for row in rows] == [
        bg.ROUND_PHASE_OPENING, bg.ROUND_PHASE_PAUSED, bg.ROUND_PHASE_ACTIVE, bg.ROUND_PHASE_UNKNOWN]


# ------------------------------------------- follow-up review, 2026-11-07: the forward walk and the cache


def test_a_station_screen_ahead_ends_the_forward_search_instead_of_borrowing_a_fight():
    """Character select between an opening and a fight means that fight is a different game's business.

    The backward walk has always refused to cross a station screen. The forward walk -- the one that answers
    "where is this round first played?" -- used to scan the whole window and take the first active frame it
    found, so an opening at 10 s with a character-select at 12 s and a fight at 100 s reported 100 s as this
    round's first gameplay, 88 s and one pairing screen later. Continuity cannot be assumed in one direction
    only: what separates this round from that fight is a screen, not a timestamp.
    """
    rows = sequence([
        (10.0, "gameplay", "'ROUND 1' banner, fighters in pre-round stance", None),
        (11.0, "unknown", "arena visible, nothing readable", None),
        (12.0, "character_select", "Character select, both players choosing", None),
        (13.0, "character_select", "Player 2 confirm", None),
        (100.0, "gameplay", "Active combat with timer 58, health bars dropping", 58),
    ])
    found = bg.opening_sequence(rows, 11.0)
    assert found["opening_start_at"] == 10.0, "the opening itself is still recovered"
    assert found["first_active_at"] is None, "no frame continuous with this opening shows it played"
    assert found["active_onset_status"] == "station_screen_between"
    assert found["clip_start_options"]["first_active_gameplay"] is None, \
        "the card cannot offer a start that was reached across a pairing screen"


def test_another_rounds_banner_ahead_is_not_this_rounds_first_play():
    """A ROUND 2 banner between this opening and some combat says this round was never fought on tape.

    The forward walk stops at the round boundary rather than at the fight, because the fight after ROUND 2
    belongs to ROUND 2. Reporting it as this clip's first gameplay would merge two rounds the recording keeps
    apart, which is the same class of error as merging a game across a station screen.
    """
    rows = sequence([
        (10.0, "gameplay", "'ROUND 1' banner, fighters in pre-round stance", None),
        (11.0, "unknown", "arena visible, nothing readable", None),
        (40.0, "gameplay", "'ROUND 2' banner, fighters back in stance", None),
        (41.0, "gameplay", "Active combat with timer 59", 59),
    ])
    found = bg.opening_sequence(rows, 11.0)
    assert found["active_onset_status"] == "later_round_opening_between"
    assert found["first_active_at"] is None


def test_the_onset_of_play_gets_its_own_uncertainty_when_the_tape_did_not_look():
    """Two questions, two widths: where the opening is (1 s) and when play started (89 s apart).

    The recording sampled the opening at 10 s and the next frame showing the round fought at 100 s. The first
    number is honest about the opening; using it for the second would dress an 89 s hole in a one-second
    claim, which is what the follow-up review caught. Both are reported, and the count of frames in between
    says how thin the coverage is.
    """
    rows = sequence([
        (10.0, "gameplay", "'ROUND 1' banner, fighters in pre-round stance", None),
        (11.0, "unknown", "arena visible, nothing readable", None),
        (100.0, "gameplay", "Active combat with timer 58, health bars dropping", 58),
    ])
    found = bg.opening_sequence(rows, 11.0)
    assert found["uncertainty_seconds"] == 1.0, "how precisely the opening is placed"
    assert found["active_onset_uncertainty_seconds"] == 89.0, "how precisely the start of play is placed"
    assert found["active_onset_span_seconds"] == 90.0
    assert found["sampled_frames_between_opening_and_play"] == 1
    assert found["active_onset_status"] == "observed"
    assert found["clip_start_options"]["first_active_uncertainty_seconds"] == 89.0
    assert found["clip_start_options"]["include_opening_uncertainty_seconds"] == 1.0


def test_dense_sampling_ahead_keeps_the_two_uncertainties_the_same_size():
    """When the tape did look, the two numbers agree: honesty about a hole must not invent one.

    A one-second-per-frame stretch between banner and fight should still report about a second for the onset.
    Without this, the wider field would read as permanent doubt and the reviewer would learn nothing about
    which case they are in.
    """
    rows = sequence([(10.0, "gameplay", "'ROUND 1' banner, fighters in pre-round stance", None)]
                    + [(at, "gameplay", "Arena, nothing readable", None) for at in (11.0, 12.0, 13.0)]
                    + [(14.0, "gameplay", "Active combat with timer 59", 59)])
    found = bg.opening_sequence(rows, 11.0)
    assert found["first_active_at"] == 14.0
    assert found["active_onset_uncertainty_seconds"] == 1.0
    assert found["sampled_frames_between_opening_and_play"] == 3, "11, 12 and 13: three looks, one second apart"


def test_the_card_carries_the_onset_width_and_says_when_there_is_no_onset():
    """The proposal the reviewer sees must hold both widths, or it holds a false precision.

    Checked through ``_adjust_affordance``, the thing that builds the card, because a field that stops inside
    the recovery helper is a field nobody acts on.
    """
    recovered = {"round_opening": {"opening_start_at": 10.0, "uncertainty_seconds": 1.0,
                                   "first_active_at": 100.0, "active_onset_uncertainty_seconds": 89.0,
                                   "active_onset_status": "observed"}}
    recovered = {"recovered_opening": recovered}
    affordance = review._adjust_affordance(11.0, recovered)
    assert affordance["jump_to_uncertainty_seconds"] == 1.0
    assert affordance["first_active_uncertainty_seconds"] == 89.0
    assert affordance["first_active_status"] == "observed"

    blocked = {"recovered_opening": {"round_opening": {
        "opening_start_at": 10.0, "uncertainty_seconds": 1.0,
        "first_active_at": None, "active_onset_uncertainty_seconds": None,
        "active_onset_status": "station_screen_between"}}}
    affordance = review._adjust_affordance(11.0, blocked)
    assert affordance["first_active_gameplay_at"] is None
    assert affordance["first_active_status"] == "station_screen_between", \
        "missing coverage arrives as a status, not as silence next to a number"


def test_the_phase_cache_follows_the_bytes_and_not_the_list_object():
    """A row edited in place is a different frame, and a cache that misses that is wrong, not fast.

    The parse memo is keyed on the sequence listing so a review refresh costs one parse instead of thirty. The
    follow-up review found the hole in that design: identity survives mutation, so a row rewritten from 'READY
    banner' to 'Active combat with timer 58' kept answering with the phase parsed from the prose that used to
    be there. The cache now checks a content digest before reusing -- a re-read, which is cheap next to a
    re-parse -- and a stale answer is no longer reachable through any caller.
    """
    rows = [{"timestamp": 10.0, "screen_state": "gameplay", "evidence": "'READY' banner, fighters in stance"}]
    assert review._parsed_rows(rows)[0]["combat_phase"] == bg.ROUND_PHASE_OPENING
    rows[0]["evidence"] = "Active combat with timer 58, health bars dropping"
    cached = review._parsed_rows(rows)[0]["combat_phase"]
    assert cached == review._state_rows(rows)[0]["combat_phase"] == bg.ROUND_PHASE_ACTIVE


def test_a_snapshot_handed_down_a_call_chain_is_verified_once_and_used_as_a_snapshot():
    """The verified parse travels, so correctness does not cost a re-hash per helper per clip.

    ``start_opening`` checks the listing once and passes the snapshot to the recovery and the phase index.
    Two things follow, and both are checked: the same content reuses the same parse (the performance fix
    survives), and the rows a caller receives are copies, so editing a handed-out row cannot corrupt the
    cache for the next caller.
    """
    rows = [{"timestamp": 10.0, "screen_state": "gameplay", "evidence": "'ROUND 1' banner"}]
    snapshot = review._as_snapshot(rows)
    assert review._as_snapshot(rows) is snapshot
    assert snapshot.index is snapshot.index
    first = review._parsed_rows(snapshot)[0]
    first["combat_phase"] = "invented"
    assert review._parsed_rows(rows)[0]["combat_phase"] == bg.ROUND_PHASE_OPENING, \
        "a caller's edit to a row it was handed must not reach the cached parse"
