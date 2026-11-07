"""Game detection from the game's own sequence, independent of the broadcast overlay.

Every test here asks the same question in a different way: the header above the fight may lie, the
score box may be stale, and the recording may join or leave mid-round, but a round clock that only
counts down, a K.O. that ends a round and a win dot that fills cannot be contradicted into a
different sequence. The BB9 cases at the bottom are real cached observations, not fixtures.
"""
import copy
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_evidence as be
from tekken_vod_helper import boundary_games as bg
import _frozen_corpus as frozen  # noqa: E402  (tests/_frozen_corpus.py)


def row(t, state="gameplay", clock=None, call=None, end=None, res=False, dots=None, cue=None,
        p1="Ann", p2="Zoe", s1=0, s2=0, c=0.95, chars=("Reina", "Azucena")):
    evidence = []
    if cue:
        # Worded the way the state route actually wrote it: the banner it read, quoted.
        evidence.append("central '{}' banner".format(cue))
    if clock is not None:
        evidence.append("timer {}".format(clock))
    if call is not None:
        evidence.append("'ROUND {}' callout".format(call))
    if end:
        evidence.append("{} announcement".format(end.upper().replace("_", " ")))
    if res:
        evidence.append("'YOU WIN' victory banner over KO camera")
    if dots is not None:
        evidence.append("WIN {} indicator".format(dots))
    if state != "gameplay":
        evidence.append("{} screen".format(state.replace("_", " ")))
    return {"timestamp": float(t), "state": state, "confidence": c,
            "player1": p1, "player2": p2, "character1": chars[0], "character2": chars[1],
            "score1": s1, "score2": s2, "evidence": "; ".join(evidence),
            "path": "frames/frame-{:04d}.jpg".format(int(t))}


def track(rows, **kw):
    return bg.track_games(bg.build_observations(rows), **kw)


def starts(result):
    return [round(r["start"], 2) for r in result["rounds"]]


# ---------- facts, before any inference ----------

def test_round_clock_and_events_are_separated_from_the_overlay():
    fact = bg.parse_round_facts(row(10, clock=42, call=1, p1="Mattiniero", s1=3))
    assert fact["round_clock"] == 42
    assert fact["round_call"] == 1
    # The overlay is recorded, so a reviewer can see it was contradicted, and marked untrusted.
    assert fact["overlay"] == {"player1": "Mattiniero", "player2": "Zoe", "score": [3, 0],
                               "trust": "untrusted"}


def test_a_row_that_never_read_the_clock_has_no_clock():
    fact = bg.parse_round_facts(row(10))
    fact["evidence"] = "fighters exchanging blows"
    assert bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay",
                                 "evidence": "fighters exchanging blows"})["round_clock"] is None


# ---------- the clock stated ahead of its word ----------
#
# "29s timer" and "timer 29" are the same digits on the same screen, and the pattern read only the second
# one. Across the 2,019 classified BB9 rows that cost 34 clocks, bounded one round 16 s before its last
# falling reading, and left three rows of live play inside no game. So the fix is a second wording, not a
# looser one: every test below that refuses a number refuses it for a reason the recording actually
# contains, because the same shapes -- "1-1", "24 DAMAGE", "3 Hit Combo" -- appear on these rows constantly.

@pytest.mark.parametrize("evidence, value", [
    ("Volcanic stage active fight; health bars + 29s timer + combo/Punish HUD", 29),
    ("Active round with 19s timer, 2 HIT COMBO / 24 DAMAGE overlay", 19),
    ("Active round HUD with full health bars and 60-second timer", 60),
    ("health bars and 37s round timer visible", 37),
    ("characters grappling in stage, health bars and 15s round timer present", 15),
    ("In-arena round start with 'READY' overlay, P1/P2 tags, live 60s timer and full HUD", 60),
    ("Lightning-hit combat at 48s timer; top broadcast bar Mattiniero (1) vs Spitfire2929 (0)", 48),
    ("Round-start 'FINAL ROUND' banner over active in-match scene (60s timer, P1/P2 tags)", 60),
])
def test_a_clock_written_before_its_word_is_still_the_round_clock(evidence, value):
    fact = bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay", "evidence": evidence})
    assert fact["round_clock"] == value
    assert fact["round_clock_wording"] == "before_word"


@pytest.mark.parametrize("evidence", [
    # A round number is not a clock: "ROUND 1" alone says which round, never what time it is.
    "Fighters in starting stances with 'ROUND 1' callout and full health bars",
    # Damage and combo counters are the numbers that live next to a real clock on these rows.
    "Active fight, 3 Hit Combo / 21 Damage readout, timer unreadable",
    "health bars + 24 DAMAGE overlay, the clock is not legible",
    # A set score is two digits with a dash, which is the shape a seconds count would have.
    "Top broadcast scoreboard shows CrispyBacon vs Rebix with set score 1-1; no round HUD",
    "broadcast set score 0-0; both fighters idle, no timer on screen",
    # And a clock the row reports as unreadable stays unreadable, whatever digits sit nearby.
    "the 30s timer is not visible in this frame",
    "no 45s timer readable through the effect",
    # A bare "s" that is not a unit: the plural of an abbreviation is not a seconds count.
    "both players' bars flash red, 18 hits landed",
])
def test_a_clock_shaped_number_that_is_not_the_clock_stays_unread(evidence):
    fact = bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay", "evidence": evidence})
    assert fact["round_clock"] is None, evidence
    assert fact["round_clock_wording"] is None, evidence


def test_the_two_wordings_disagreeing_keeps_the_one_the_pattern_always_trusted():
    """"timer 41" ahead of "29s timer" is one row stating two clocks; the forward reading wins."""
    fact = bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay",
                                 "evidence": "timer 41 on screen, 29s timer also mentioned below"})
    assert fact["round_clock"] == 41
    assert fact["round_clock_wording"] == "after_word"


def test_a_stated_clock_overrides_both_wordings_and_says_so():
    """A vision answer's field outranks the prose, and the wording credits the field that won."""
    stated = bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay",
                                   "evidence": "health bars + 29s timer", "round_clock": 41})
    assert (stated["round_clock"], stated["round_clock_wording"]) == (41, "stated_field")
    # An answer that looked and could not read is not a parser gap: it must not fall back to the prose.
    unreadable = bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay",
                                       "evidence": "health bars + 29s timer", "round_clock": None})
    assert unreadable["round_clock"] is None
    assert unreadable["round_clock_wording"] == "stated_unreadable"
    assert unreadable["conflicts"] == [{"field": "round_clock", "from": 29, "to": None}]


def test_a_clock_above_the_rounds_full_time_is_still_refused_whatever_the_wording():
    """The ceiling on a plausible reading is the round's own full time, in both directions."""
    assert bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay",
                                 "evidence": "90s timer"})["round_clock"] is None


def test_the_repaired_clock_extends_the_round_it_bounded_early():
    """The 6,201.195 case, end to end: three rows that only the prose dated are live play.

    Those are the saved rows from the round this whole repair is about. Before it the series stopped at
    34 and the round was bounded there; the digits falling to 19 s later were stated in the prose and lost.
    """
    rows = [{"timestamp": 6201.195, "state": "gameplay", "confidence": 0.9,
             "evidence": "Gameplay: Nina and Azucena at close range, timer 34", "path": "f1.jpg"},
            {"timestamp": 6206.200, "state": "gameplay", "confidence": 0.9,
             "evidence": "Volcanic stage active fight; health bars + 29s timer + combo/Punish HUD",
             "path": "f2.jpg"},
            {"timestamp": 6211.205, "state": "gameplay", "confidence": 0.9,
             "evidence": "Continued in-game combat (air kick, spark effects) with live health bars",
             "path": "f3.jpg"},
            {"timestamp": 6216.210, "state": "gameplay", "confidence": 0.9,
             "evidence": "Active round with 19s timer, 2 HIT COMBO / 24 DAMAGE overlay", "path": "f4.jpg"},
            {"timestamp": 6221.215, "state": "post_match_menu", "confidence": 0.9,
             "evidence": "Large 'YOU WIN' end-of-match banner over win pose; timer gone, round resolved",
             "path": "f5.jpg"},
            {"timestamp": 6246.240, "state": "gameplay", "confidence": 0.9, "evidence": "timer 60",
             "path": "f6.jpg"}]
    out = bg.track_games(bg.build_observations(rows))
    round_ = out["rounds"][0]
    assert round_["clock_readings"] == 3, "the prose-dated rows are still not being counted"
    assert round_["last_live_at"] == 6216.210
    assert round_["first_fall_at"] == 6206.200
    # Nothing invented: the round ends where the readings stop, and says so rather than claiming a marker.
    assert round_["end"] == 6216.210
    # The result screen says the round resolved; it does not say *when* inside the 5 s since the last
    # reading, so the end stays a bound and the bound is disclosed rather than rounded to the menu.
    assert round_["end_basis"] == "round_end_not_observed"
    assert [u["type"] for u in out["unresolved"] if u["type"] == "round_end_not_observed"]
    assert not [u for u in out["unresolved"] if u["type"] == "round_clock_impossible_fall"]


def test_observation_ids_are_stable_and_reference_the_frame():
    obs = bg.build_observations([row(0, clock=60), row(5, clock=55)])
    assert [o["id"] for o in obs] == ["gobs-00000", "gobs-00001"]
    assert obs[0]["frame"].endswith("frame-0000.jpg")


# ---------- rounds ----------

def test_a_timer_reset_opens_a_round_and_a_ko_closes_one():
    rows = [row(0, clock=60), row(20, clock=40), row(40, clock=20, end="ko"),
            row(60, clock=60), row(80, clock=40)]
    out = track(rows)
    assert starts(out) == [0.0, 60.0]
    assert out["rounds"][0]["end_basis"] == "round_end_ko"
    assert out["rounds"][0]["end"] == 40.0


def test_a_time_up_ends_a_round_and_the_clock_holding_at_zero_does_not_open_another():
    rows = [row(0, clock=60), row(59, clock=1), row(60, clock=0, end="time_up"),
            row(61, clock=0), row(62, clock=0, res=True), row(63, clock=0)]
    out = track(rows)
    assert len(out["rounds"]) == 1, "the victory pose is the same round end seen for seconds more"
    assert out["rounds"][0]["end_basis"] == "round_end_time_up"


def test_a_round_announcement_opens_a_round_without_a_grid():
    rows = [row(0, call=1), row(30, clock=30), row(61, clock=60, call=2)]
    out = track(rows)
    assert starts(out) == [0.0, 61.0]
    assert out["rounds"][1]["start_basis"] == "round_clock_reset"


def test_a_missing_round_announcement_is_not_evidence_that_no_round_began():
    """Joining mid-round keeps the round and says the start was inferred."""
    rows = [row(0, clock=25), row(10, clock=15), row(24, clock=1, end="ko")]
    out = track(rows)
    assert len(out["rounds"]) == 1
    assert out["rounds"][0]["start_basis"] == "first_gameplay_seen"
    assert any(u["type"] == "round_start_not_observed" for u in out["unresolved"])


# ---------- the round's own opening, read from what is already saved ----------
#
# The recording names its round starts twice over, and both namings are already on disk: the game draws
# READY and FIGHT for a second before its digits move, and a HUD arriving at its full clock straight off a
# pre-round screen is the same event seen through the clock. Neither naming is allowed to do two things:
# invent a round number the frame never showed, or promote a frame that merely *reads* 60 into an opener.

def test_a_saved_ready_banner_before_the_digits_moved_makes_the_start_observed():
    """The 2271.269 case: the HUD first seen one row in front of the banner that opens it.

    Worded as the recording words it -- "Active round HUD with full health bars and 60-second timer" at
    2271.269, "central 'READY' banner; timer 60" one row later, the digits first falling at 2274.272. The
    start is the first frame of the round, not the banner frame, and it costs no request.
    """
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9,
             "evidence": "Active round HUD with full health bars and 60-second timer",
             "path": "frames/frame-0001.jpg"},
            row(1.0, state="stage_intro", clock=60, cue="READY"),
            row(2.0, clock=60), row(3.0, clock=59), row(4.0, clock=58)]
    out = track(rows)
    assert len(out["rounds"]) == 1, "the banner opened a second round instead of corroborating one"
    opened = out["rounds"][0]
    assert opened["start"] == 0.0, "the boundary moved to the banner row off the first HUD frame"
    assert opened["start_basis"] == "round_open_cue"
    assert "start_inferred" not in opened
    assert not [u for u in out["unresolved"] if u["type"] == "round_start_not_observed"]
    # The claim names the frame it rests on, so a reviewer can go and look at it.
    assert opened["round_start_observed"]["banner"] == "ready"
    assert opened["round_start_observed"]["at"] == 1.0
    assert opened["round_start_observed"]["clock_read"] == 60
    assert opened["round_start_observed"]["evidence"] in opened["start_evidence"]


def test_a_start_signal_never_names_a_round_it_was_not_told():
    """READY is not ROUND 1: settling a start must not hand the round a number."""
    out = track([row(0.0, clock=60, cue="READY"), row(1.0, clock=59), row(2.0, clock=58)])
    assert out["rounds"][0]["start_basis"] == "round_open_cue"
    assert out["rounds"][0]["number_observed"] is None


def test_a_banner_on_the_screen_just_before_play_opens_the_round_it_precedes():
    """The coarse label keeps a banner screen out of gameplay; the banner still says where it began."""
    out = track([row(0.0, state="stage_intro", cue="FIGHT"), row(1.0, clock=60),
                 row(2.0, clock=59)])
    opened = out["rounds"][0]
    assert opened["start"] == 1.0 and opened["start_basis"] == "round_open_cue"
    assert opened["round_start_observed"]["before_opening_row"] is True


def test_a_full_clock_off_a_pre_round_screen_is_an_observed_opening():
    """No banner read anywhere: the HUD arriving at its full clock off a stage screen and then running."""
    out = track([row(0.0, state="stage_intro"), row(1.0, clock=60), row(2.0, clock=59),
                 row(3.0, clock=58)])
    opened = out["rounds"][0]
    assert opened["start"] == 1.0 and opened["start_basis"] == "round_open_full_clock"
    assert opened["round_start_observed"]["previous_screen"] == "stage_intro"
    assert opened["round_start_observed"]["corroborated_by"]["clock_fell_at"] == 2.0
    assert opened["number_observed"] is None
    assert not [u for u in out["unresolved"] if u["type"] == "round_start_not_observed"]


def test_a_clock_reading_60_is_not_on_its_own_an_observed_opener():
    """A query that happens to open on a full clock still joined the round, and still says so."""
    out = track([row(0.0, clock=60), row(1.0, clock=59), row(2.0, clock=58)])
    assert out["rounds"][0]["start_basis"] == "first_gameplay_seen"
    assert out["rounds"][0].get("start_inferred") is True
    assert [u["at"] for u in out["unresolved"]
            if u["type"] == "round_start_not_observed"] == [0.0]


def test_a_full_clock_that_never_falls_is_the_digits_frozen_not_a_round_beginning():
    """60, 60, 60 off a pre-round screen is an overlay being drawn, which proves nothing about the start."""
    out = track([row(0.0, state="vs_screen"), row(1.0, clock=60), row(2.0, clock=60),
                 row(3.0, clock=60)])
    opened = out["rounds"][0]
    assert opened["start_basis"] == "first_gameplay_seen"
    assert opened.get("start_inferred") is True
    assert opened["opening_candidate"]["clock_read"] == 60, "the candidate vanished instead of refusing"
    assert any(u["type"] == "round_start_not_observed" for u in out["unresolved"])


def test_a_banner_drawn_after_the_digits_had_been_running_is_not_this_rounds_opener():
    """A READY read seconds into a counting-down round says nothing about where that round began.

    This is the bound that keeps a true mid-round truncation truncated: the banner lands *inside* the
    round, so "a banner was seen" alone is both true and worthless.
    """
    rows = [row(0.0, clock=40), row(1.0, clock=39), row(2.0, clock=38, cue="READY"),
            row(3.0, clock=37)]
    out = track(rows)
    opened = out["rounds"][0]
    assert opened["first_fall_at"] == 1.0
    assert opened["open_cues"][0]["at"] == 2.0
    assert opened["start_basis"] == "first_gameplay_seen"
    assert opened.get("start_inferred") is True
    assert any(u["type"] == "round_start_not_observed" for u in out["unresolved"])


def test_the_first_fall_is_the_first_and_not_the_latest_one():
    """The bound only binds if later falls do not move it forward to cover their own round."""
    rows = [row(0.0, clock=60), row(1.0, clock=59), row(2.0, clock=58), row(3.0, clock=57),
            row(4.0, clock=56, cue="READY")]
    out = track(rows)
    assert len(out["rounds"]) == 1, "a banner mid-round opened a round of its own"
    assert out["rounds"][0]["first_fall_at"] == 1.0
    assert out["rounds"][0].get("start_inferred") is True


def test_a_denied_banner_is_not_a_start_signal_even_beside_a_full_clock():
    """Prose that looked for the banner and did not find it is evidence *against* the opening."""
    rows = [{"timestamp": 0.0, "state": "stage_intro", "confidence": 0.9,
             "evidence": "pre-round screen; no round timer yet", "path": "frames/f0000.jpg"},
            {"timestamp": 1.0, "state": "gameplay", "confidence": 0.9,
             "evidence": "timer 60; no 'READY' or 'FIGHT' banner", "path": "frames/f0001.jpg"},
            {"timestamp": 2.0, "state": "gameplay", "confidence": 0.9,
             "evidence": "timer 59", "path": "frames/f0002.jpg"}]
    fact = bg.parse_round_facts(rows[1])
    assert fact["round_open_cue"] is None, "a denial was read as the banner it denies"
    assert "round_open" in fact["prose_negated"]
    out = track(rows)
    assert out["rounds"][0].get("start_inferred") is True


def test_prose_about_fighting_is_not_the_fight_banner():
    """The capitals are the whole difference: 43 banner rows against 190 rows of ordinary prose."""
    fact = bg.parse_round_facts({"timestamp": 1.0, "state": "gameplay", "confidence": 0.9,
                                 "evidence": "Active fight with timer 46; mid-fight frame; "
                                             "fighters in ready stance"})
    assert fact["round_open_cue"] is None


def test_a_span_whose_only_content_is_a_start_banner_is_still_an_opener_fragment():
    """The absorber looks for a span that is nothing but a start signal, whichever banner it read.

    A READY screen sampled in the second before its digits appeared is the next round's opener, and the
    absorption that puts it in front of that round is keyed to this shape.
    """
    sliver = {"start": 50.0, "end": 50.0, "clock_readings": 0, "number_observed": None,
              "start_basis": "round_open_cue"}
    assert bg._is_callout_sliver(sliver)


def test_a_structured_answer_may_state_the_opening_banner_directly():
    """A vision row that says ``round_open_cue`` is trusted over the prose, like every other field."""
    fact = bg.parse_round_facts({"timestamp": 1.0, "state": "stage_intro", "confidence": 0.9,
                                 "evidence": "two fighters in starting stances", "round_clock": 60,
                                 "round_open_cue": "fight"})
    assert fact["round_open_cue"] == "fight"
    out = track([{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9,
                  "evidence": "two fighters in starting stances", "round_clock": 60,
                  "round_open_cue": "fight"},
                 {"timestamp": 1.0, "state": "gameplay", "confidence": 0.9,
                  "evidence": "combat", "round_clock": 59}])
    assert out["rounds"][0]["start_basis"] == "round_open_cue"
    assert out["rounds"][0]["number_observed"] is None


def test_one_misread_clock_digit_does_not_invent_a_round():
    """A reading of 60 followed by 58 then 61 is jitter; only a rise past tolerance is a boundary."""
    rows = [row(0, clock=60), row(2, clock=58), row(4, clock=61), row(30, clock=30)]
    out = track(rows)
    assert len(out["rounds"]) == 1


def test_a_falling_clock_that_outruns_the_recording_is_reported_not_acted_on():
    rows = [row(0, clock=60), row(5, clock=55), row(10, clock=10), row(15, clock=5)]
    out = track(rows)
    assert len(out["rounds"]) == 1, "a clock that fell too fast is a bad reading, not a new round"
    falls = [u for u in out["unresolved"] if u["type"] == "round_clock_impossible_fall"]
    assert falls and falls[0]["at"] == 10.0 and falls[0]["read"] == 10


def test_a_round_longer_than_its_own_clock_is_queued_instead_of_silently_split():
    rows = [row(0, clock=60), row(50, clock=10), row(100, clock=60)]
    out = track(rows)
    queued = [u for u in out["unresolved"] if u["type"] == "round_longer_than_the_clock_allows"]
    assert queued and queued[0]["at"] == 0.0
    assert starts(out) == [0.0, 100.0]


# ---------- games ----------

def test_a_result_transition_followed_by_a_menu_closes_a_game():
    rows = [row(0, clock=60), row(40, clock=20, end="ko"), row(41, clock=20, res=True),
            row(45, state="post_match_menu"), row(100, clock=60), row(140, clock=20, end="ko")]
    out = track(rows)
    assert len(out["games"]) == 2
    assert out["games"][0]["status"] == "complete"
    assert out["games"][0]["basis"] == "result_transition_then_menu"
    assert out["games"][0]["closing_screen"] == "post_match_menu"


def test_win_dots_reaching_the_target_closes_a_game_and_names_the_side():
    """Side-specific dots are the only thing that can decide a game, so they also decide the winner."""
    rows = [row(0, clock=60), row(30, clock=30, end="ko"),
            {"timestamp": 31.0, "state": "gameplay", "confidence": 0.95, "player1": "Ann",
             "player2": "Zoe", "characters": [], "win_dots": [3, 1], "evidence": "read from frame"}]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    assert game["status"] == "complete"
    assert game["basis"] == "win_dots_reached_target"
    assert game["winner"] == "p1"


def test_an_unattributed_dot_count_cannot_name_a_winner():
    """Prose that says "WIN 1 indicator" without a side proves rounds were won and nothing about who.

    Reducing a pair to a maximum used to let exactly this decide a game, and a game winner is the
    only thing a set can be built from -- so an unattributed reading ends the game at most, never
    completes a set.
    """
    rows = [row(0, clock=60), row(30, clock=30, end="ko"), row(31, dots=3, res=True),
            row(35, state="post_match_menu")]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    assert game["status"] == "complete"
    assert game["winner"] is None
    assert game["winner_basis"] == "not_observed"
    queued = [u for u in out["unresolved"] if u["type"] == "game_winner_unobserved"]
    assert queued and queued[0]["anchor"] == "game_end"


def test_no_set_is_ever_completed_without_a_winner_on_each_side_of_the_score():
    games = [dict(start=index * 100, end=index * 100 + 90, status="complete", winner=None)
             for index in range(9)]
    sets = bg._group_into_sets(games, 3)
    # ``partial`` is what a set that cannot be closed is called now; the older single ``unresolved`` word
    # could not tell "short of the target" from "these games may not even be one set", which is the
    # distinction the membership work exists to make. Nothing here may claim completeness.
    assert sets and all(record["status"] == "partial" for record in sets)
    assert all(record["game_wins"] == {"p1": 0, "p2": 0} for record in sets)
    assert not any(record["status"] == "complete" for record in sets)



def test_an_unconcluded_game_is_preserved_and_queued_never_dropped():
    rows = [row(0, clock=60), row(30, clock=30, end="ko")]
    out = track(rows)
    assert len(out["games"]) == 1
    assert out["games"][0]["status"] == "incomplete"
    assert out["games"][0]["concluded_level"] == "unknown"
    incomplete = [u for u in out["unresolved"] if u["type"] == "game_incomplete"]
    assert incomplete and incomplete[0]["would_resolve"]


def test_five_rounds_without_a_conclusion_is_an_open_question_not_a_finished_game():
    rows = []
    t = 0.0
    for _ in range(5):
        rows.append(row(t, clock=60))
        rows.append(row(t + 30, clock=30, end="ko"))
        t += 60
    out = track(rows, rounds_per_game=3)
    assert out["games"][0]["status"] == "incomplete"
    assert out["games"][0]["basis"] == "round_count_exceeded_without_conclusion"
    assert sum(g["rounds"] for g in out["games"]) == 5, "no round may vanish with the label"


def test_the_clock_alone_cannot_tell_a_game_end_from_a_set_end():
    """Both look identical mid-broadcast, so the trace has to say which one it guessed."""
    rows = [row(0, clock=60), row(30, clock=30, end="ko"), row(31, res=True)]
    out = track(rows)
    assert out["games"][0]["status"] == "incomplete"
    assert out["games"][0]["concluded_level"] == "unknown"


def test_set_format_is_configured_and_never_inferred_from_the_footage():
    """The format is an input, and it changes nothing about what the footage can prove.

    The previous version of this test asserted that three games make a set and five do not, which is
    exactly the arithmetic error the set layer was built to stop making: three completed games can be
    a 2-1 still in progress. The format is now recorded as configured, and set completion waits on
    winners regardless of what it says.
    """
    rows = []
    t = 0.0
    for _ in range(7):
        rows += [row(t, clock=60), row(t + 30, clock=30, end="ko"),
                 row(t + 31, res=True), row(t + 35, state="post_match_menu")]
        t += 60
    out3 = track(rows, rounds_per_game=3, games_per_set=3)
    out5 = track(rows, rounds_per_game=3, games_per_set=5)
    assert out3["format"] == {"rounds_per_game": 3, "games_per_set": 3,
                              "source": "configured, not inferred from labels",
                              # Nothing was supplied, so nothing is stated: the bracket's format is a route
                              # a caller may read from, never one the tracker takes on its own. ``policy``
                              # is the event's stage-specific format, and with none supplied the one
                              # configured target is what every candidate is measured against -- exactly
                              # the behaviour that predates it.
                              "policy": None,
                              "bracket_states": None}
    assert out5["format"]["games_per_set"] == 5
    assert out3["evidence_routes"]["bracket"] == {"supplied": False, "route": "recording_only"}
    for result in (out3, out5):
        for record in result["sets"]:
            # No game in this fixture names a winner, because nothing in it says which side the dots
            # favoured -- so no set may be closed at any configured length.
            assert record["status"] == "partial", \
                "a set with no winner on any game was closed at games_per_set=%d" % \
                record["games_per_set_expected"]
            assert record["games_without_winner"] == record["games"]
            assert record["missing_evidence"]
    unresolved = [u for u in out3["unresolved"] if u["type"] == "set_unresolved"]
    assert unresolved and unresolved[0]["games_without_winner"] == unresolved[0]["games_seen"]
    assert "per-side game wins reaching 3" in " ".join(unresolved[0]["missing_evidence"])
    # The two configured lengths must not be silently interchangeable: five games short of a target of
    # five is a different situation from three games short of three.
    assert {r["games_per_set_expected"] for r in out5["sets"]} == {5}


# ---------- the overlay may not touch any of it ----------

@pytest.mark.parametrize("corrupt", ["swap_names", "freeze_scores", "shuffle_scores", "blank_names"])
def test_broadcast_names_and_scores_cannot_move_a_round_or_game_boundary(corrupt):
    base = [row(0, clock=60, s1=0, s2=0), row(30, clock=30, end="ko", s1=1, s2=0),
            row(60, clock=60, s1=3, s2=2), row(90, clock=30, end="ko", s1=0, s2=3),
            row(91, res=True, s1=5, s2=5), row(95, state="post_match_menu", s1=0, s2=0)]
    rows = copy.deepcopy(base)
    for i, r in enumerate(rows):
        if corrupt == "swap_names":
            r["player1"], r["player2"] = r["player2"], r["player1"]
        elif corrupt == "freeze_scores":
            r["score1"] = r["score2"] = 3
        elif corrupt == "shuffle_scores":
            r["score1"], r["score2"] = i % 4, (3 - i) % 4
        elif corrupt == "blank_names":
            r["player1"] = r["player2"] = ""
    clean = track(base)
    broken = track(rows)
    assert starts(broken) == starts(clean)
    assert [(g["start"], g["status"]) for g in broken["games"]] == \
           [(g["start"], g["status"]) for g in clean["games"]]


def test_sampling_density_changes_move_no_round_boundary():
    """A round is found at whatever spacing the evidence allows, and never more than one per reset.

    Thinning the samples cannot invent a boundary that was not there, and must still land within one
    sampling interval of the true reset -- which is the point the investigation loop exists to close.
    """
    rows = []
    for game in range(3):
        base = game * 200.0
        for second in range(0, 56):
            rows.append(row(base + second, clock=60 - second))
        rows.append(row(base + 56, clock=4, end="ko"))
    dense = track(rows)
    assert len(dense["rounds"]) == 3
    for step in (5, 10):
        thinned = track(rows[::step])
        got = starts(thinned)
        assert len(got) == 3, "step {} found {} rounds: {}".format(step, len(got), got)
        for found, true in zip(got, starts(dense)):
            assert abs(found - true) <= step, "step {} moved {} to {}".format(step, true, found)


def test_a_recording_that_joins_mid_round_keeps_that_play_accounted_for():
    rows = [row(100, clock=40), row(120, clock=20, end="ko"), row(130, clock=60)]
    out = track(rows)
    assert out["accounting"]["gameplay_observations"] == 3
    assert out["accounting"]["gameplay_inside_games"] == 3


# ---------- BB9, from the cached classification only ----------

BB9_TIMELINE = "build/work/bb9/detect-1440p-v2/timeline-refined.json"


@pytest.fixture(scope="module")
def bb9():
    import json
    from pathlib import Path
    path = Path(BB9_TIMELINE)
    if not path.is_file():
        pytest.skip("BB9 cached timeline is not present")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("before,after", [
    (3918.914794, 3923.919794),   # Buk/Ariz -> Mattiniero/Spitfire, clock 43 -> 42
    (4524.519762, 4529.524762),   # Mattiniero/Spitfire -> Ariz/derpingtoast, 52 -> 47
    (6646.639651, 6651.644650),   # derpingtoast -> Mattiniero, 49 -> 45
])
def test_a_delayed_overlay_change_on_bb9_lands_inside_one_round(bb9, before, after):
    """The three alleged recording cuts are mid-round overlay corrections.

    Each was confirmed against the cached frames by hand: same stage, same characters, health already
    spent, and the round clock down exactly the seconds between the frames. The tracker never reads
    the names or the score boxes, so it must not split there either.
    """
    out = track(bb9)
    inside = [r for r in out["rounds"] if r["start"] <= before and (r["end"] or 0) >= after]
    assert inside, "no round contains {} -> {}".format(before, after)
    nearby = [r["start"] for r in out["rounds"] if abs(r["start"] - after) < 3.0]
    assert nearby == [], "a boundary was opened at the overlay change: {}".format(nearby)


def test_bb9_round_durations_stay_inside_one_round_clock(bb9):
    """What the clock alone achieves on the cached footage, and what it admits it missed.

    Every round must be a round the game could have played, and any span that cannot be one round has
    to appear in the queue as the question it is. The count of those spans is reported, not asserted
    away: they are where the 5 s sampling let a reset go unread.
    """
    out = track(bb9)
    rounds = out["rounds"]
    durations = [(r["end"] or r["start"]) - r["start"] for r in rounds]
    assert out["accounting"]["rounds"] > 100, "round detection found almost nothing"
    assert sum(1 for d in durations if d < bg.MIN_ROUND_SECONDS) == 0
    # A round occupies the recording until the next one opens: that is where an unread reset hides,
    # and measuring only to the observed end makes the impossible spans look safe.
    occupied = [(rounds[i + 1]["start"] if i + 1 < len(rounds) else (r.get("end") or r["start"]))
                - r["start"] for i, r in enumerate(rounds)]
    ceiling = bg.ROUND_CLOCK_START + bg.ROUND_CAP_SLACK
    over = [round(d) for d in occupied if d > ceiling]
    impossible = [u for u in out["unresolved"] if u["type"] == "round_longer_than_the_clock_allows"]
    dwells = [u for u in out["unresolved"] if u["type"] == "dwell_after_observed_end"]
    # Two different questions live in a span longer than one round clock. An end that was never observed
    # may hide an unread reset; an end the recording *did* show leaves a dwell behind it that is not the
    # round's problem. Either way the span has to be accounted for, and "accounted for" means queued or
    # decomposed into a round the game could have played plus a dwell that is itself queued. What may not
    # exist is a long span with neither, which is the shape that used to read as a clean report.
    assert all((d.get("end_basis") or "").startswith("round_end_") and
               d.get("end_basis") != "round_end_not_observed" for d in dwells), \
        "a dwell may only be charged to a round whose own end was on screen"
    charged = {u["at"] for u in impossible} | {u["at"] for u in dwells}
    unexplained = []
    for i, rnd in enumerate(rounds):
        if occupied[i] <= ceiling:
            continue
        own = (rnd["end"] or rnd["start"]) - rnd["start"]
        dwell = rounds[i + 1]["start"] - (rnd["end"] or rnd["start"]) if i + 1 < len(rounds) else 0.0
        ends_on_screen = (rnd.get("end_basis") or "").startswith("round_end_") and \
            rnd.get("end_basis") != "round_end_not_observed"
        if ends_on_screen and own <= ceiling and dwell <= ceiling:
            continue                       # a legal round and a legal dwell, both within the clock
        if rnd["start"] in charged or rnd.get("end") in charged:
            continue                       # queued by one of the two reports
        unexplained.append((round(rnd["start"], 1), round(occupied[i], 1)))
    assert unexplained == [], "long spans nobody reported: %s" % unexplained
    # Measured 2026-10-28: 10 of 122 rounds sat in a span the 5 s sampling left unread. The clock
    # settles the rest, and the queue is where the pilot is aimed, so it must stay the exception.
    assert len(over) * 8 <= len(occupied), "the clock is settling too little of the footage"
    assert all(u["seconds"] > ceiling for u in impossible + dwells)


def test_bb9_games_close_on_the_game_sequence_never_on_the_overlay(bb9):
    """What reuse can and cannot establish, measured rather than asserted away.

    Rounds come out of the clock the classifier already wrote down; games close on a result transition
    followed by a between-game screen. The win-dot row beside the clock was never read for most rounds,
    so those rounds cannot prove who won the game and stay in the queue. No basis here may name a
    broadcast field, because the header changed mid-round three times on this very recording.
    """
    out = track(bb9)
    assert out["accounting"]["rounds"] > 100
    assert out["games"]
    overlay_bases = ({g["basis"] for g in out["games"]}
                     & {"name_change", "score_box_target", "pairing_change"})
    assert overlay_bases == set()
    complete_bases = {g["basis"] for g in out["games"] if g["status"] == "complete"}
    assert complete_bases <= {"result_transition_then_menu", "win_dots_reached_target"}
    incomplete = [g for g in out["games"] if g["status"] == "incomplete"]
    assert incomplete, "the recording does not always show a game ending"
    assert all(g["concluded_level"] == "unknown" for g in incomplete)
    queued = [u for u in out["unresolved"] if u["type"] == "win_dots_never_read"]
    assert len(queued) > len(out["rounds"]) // 2, "the missing primitive must be named to go get it"
    over_budget = [u for u in out["unresolved"] if u["type"] == "round_longer_than_the_clock_allows"]
    assert over_budget, "spans with an unread reset are exactly what denser frames are for"


# ---------- reconciliation of the conclusion signals ----------

def test_a_round_numbering_that_continues_contradicts_a_claimed_game_end():
    """A menu plus a victory pose is not a game end if the game then announces ROUND 3.

    The three signals are recorded separately and the disagreement is queued rather than being
    outvoted: result transition, screen and numbering can each be right about a different thing.
    """
    rows = [row(0, clock=60), row(30, clock=30, end="ko"), row(31, res=True),
            row(35, state="post_match_menu"), row(70, clock=40, call=3), row(100, clock=10)]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    assert game["basis"] == "result_transition_then_menu"
    signals = game["conclusion_signals"]
    assert signals["result_transition"] and signals["closing_screen"] == "post_match_menu"
    assert signals["round_numbering_contradicts"] is True
    assert signals["next_round_number"] == 3
    assert game["winner"] is None
    contradicted = [u for u in out["unresolved"] if u["type"] == "game_conclusion_contradicted"]
    assert contradicted and contradicted[0]["next_round_number"] == 3


def test_a_numbering_that_restarts_corroborates_the_game_end():
    rows = [row(0, clock=60), row(30, clock=30, end="ko"), row(31, res=True),
            row(35, state="post_match_menu"), row(70, clock=50, call=1), row(100, clock=20)]
    out = track(rows, rounds_per_game=3)
    signals = out["games"][0]["conclusion_signals"]
    assert signals["round_numbering_confirms_new_game"] is True
    assert signals["round_numbering_contradicts"] is False


def test_a_negated_banner_in_prose_is_not_a_round_end():
    """The classifier writing "no K.O. banner" describes its absence, not a K.O."""
    rows = [row(0, clock=60), row(30, clock=30), row(31, clock=29), row(60, clock=60)]
    rows[2]["evidence"] = "No K.O. or TIME UP banner is visible; no victory pose."
    out = track(rows)
    assert len(out["rounds"]) == 2, "a negated sentence must not end a round early"
    first = out["rounds"][0]
    assert first["end_basis"] == "round_end_not_observed"


def test_prose_negation_is_recorded_as_a_searched_for_absence():
    fact = bg.parse_round_facts(dict(timestamp=1.0, state="gameplay",
                                     evidence="No TIME UP banner visible; timer 30"))
    assert fact["round_end"] is None
    assert "round_end" in fact["prose_negated"]


def test_sets_stay_unresolved_whenever_no_game_names_a_winner(bb9_or_fixture):
    """The BB9 shape: many closed games, zero winners, so exactly one open question about sets."""
    out = track(bb9_or_fixture)
    assert out["games"], "the fixture should produce games"
    assert all(game["winner"] is None for game in out["games"])
    assert all(record["status"] == "partial" for record in out["sets"])
    assert all(record["game_wins"] == {"p1": 0, "p2": 0} for record in out["sets"])


@pytest.fixture(scope="module")
def bb9_or_fixture():
    import json
    from pathlib import Path
    path = Path(BB9_TIMELINE)
    if not path.is_file():
        pytest.skip("BB9 cached timeline is not present")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- contradiction must not become a winner ----------

def _dotrow(t, dots=None, **kw):
    row = {"timestamp": float(t), "state": "gameplay", "confidence": 0.95,
           "evidence": "timer 30"}
    if dots is not None:
        row["win_dots"] = dots
    row.update(kw)
    return row


def test_dots_that_fall_inside_a_game_contest_the_winner_and_are_queued():
    """A win pip lights and stays lit until the game resets, so a fall is a misread, not a lost round.

    The old code took the maximum of every reading, which is how 0-0, a bad 3-0, a corrected 0-0 and
    1-0 published a game won 3-0 by a side that had taken one round.
    """
    rows = [_dotrow(0, [0, 0], round_clock=60), _dotrow(20, [3, 0], round_clock=50, round_end="ko"),
            _dotrow(40, [0, 0], round_clock=40),
            _dotrow(60, [1, 0], round_clock=30, round_end="ko", result_transition=True),
            {"timestamp": 65.0, "state": "post_match_menu", "confidence": 0.95, "evidence": "menu"}]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    # The whole series survives, so the first fall is what gets reported: 3 to 0, not the 3 to 1 that
    # value-deduplication used to manufacture from whichever readings happened to differ.
    assert game["dot_regressions"] == {"p1": {"from": 3, "to": 0, "at": 40.0}}
    assert out["rounds"][0]["win_dots_seen"]["p1"] == [0, 3, 0, 1]
    assert game["contested"] is True
    assert "win_dots_regressed:p1" in game["contested_reasons"]
    assert game["winner"] is None, "a contradictory game may not name a winner"
    assert game["counts_toward_set"] is False
    queued = {u["type"] for u in out["unresolved"]}
    assert "win_dots_regressed" in queued and "game_conclusion_contradicted" in queued
    fall = next(u for u in out["unresolved"] if u["type"] == "win_dots_regressed")
    assert fall["at"] == 40.0, "the queue points at the reading that fell, not at the game start"
    assert fall["game_start"] == game["start"], "and it still says which game the fall is inside"

def test_a_monotonic_game_still_earns_its_winner():
    rows = [_dotrow(0, [0, 0], round_clock=60), _dotrow(20, [1, 0], round_clock=50, round_end="ko"),
            _dotrow(40, [2, 0], round_clock=45, round_end="ko"),
            _dotrow(60, [3, 0], round_clock=40, round_end="ko", result_transition=True),
            {"timestamp": 65.0, "state": "post_match_menu", "confidence": 0.95, "evidence": "menu"}]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    assert game["winner"] == "p1" and game["contested"] is False
    assert game["counts_toward_set"] is True
    # One game is not a set: the win is recorded and the set stays open.
    assert out["sets"][0]["game_wins"] == {"p1": 1, "p2": 0}
    assert out["sets"][0]["status"] == "partial"


def test_a_fourth_round_number_is_a_fact_not_a_malformed_reply():
    """First-to-three reaches round 4 at 2-1; refusing it would pressure the reader to mislabel."""
    fact = bg.parse_round_facts(dict(timestamp=1.0, state="gameplay",
                                     evidence="ROUND 4 banner; timer 55"))
    assert fact["round_call"] == 4
    assert fact["hud_present"] is True


def test_a_corrected_dot_reading_is_not_silenced_by_value_deduplication():
    """2-3-2 is a contradiction, not two readings of one value.

    Keeping only each distinct value inside a round collapsed this to [2, 3] -- monotonic -- so a game
    whose own evidence said the 3 was wrong still published a winner that counted toward a set. The
    series has to stay chronological, compressing only consecutive repeats.
    """
    rows = [dict(timestamp=float(t), state="gameplay", confidence=0.95,
                 evidence="timer {}".format(clock), win_dots=dots)
            for t, clock, dots in [(0, 60, [2, 0]), (20, 40, [3, 0]), (30, 30, [2, 0]),
                                   (40, 20, [2, 0])]]
    out = track(rows, rounds_per_game=3)
    assert out["rounds"][0]["win_dots_seen"]["p1"] == [2, 3, 2]
    game = out["games"][0]
    assert game["dot_regressions"] == {"p1": {"from": 3, "to": 2, "at": 30.0}}
    assert game["winner"] is None
    assert game["counts_toward_set"] is False
    assert any(u["type"] == "win_dots_regressed" for u in out["unresolved"])


def test_one_unchanged_pip_row_across_seconds_stays_one_reading():
    """Compression still applies where it is honest: the same row seen for four seconds."""
    rows = [dict(timestamp=float(t), state="gameplay", confidence=0.95,
                 evidence="timer {}".format(60 - t), win_dots=[1, 0])
            for t in (0, 5, 10, 15)]
    out = track(rows)
    assert out["rounds"][0]["win_dots_seen"] == {"p1": [1], "p2": [0]}


# ---------- a reading that contradicts itself about the shared pip row ----------

def dots(timestamp, value, presence=(True, True), confidence=0.95):
    return {"timestamp": timestamp, "path": "build/work/bb9/detect-1440p-v1/frames/frame-0001.jpg",
            "state": "gameplay", "evidence": "", "round_clock": 30, "win_dots": list(value),
            "win_dots_drawn": list(presence), "confidence": confidence}


def _series(result, side, game_index=0):
    game = result["games"][game_index]
    out = []
    for rid in game["round_ids"]:
        rnd = next(r for r in result["rounds"] if r.get("id") == rid)
        out.extend((rnd.get("win_dots_seen") or {}).get(side) or [])
    return out


def test_a_mixed_presence_reading_is_set_aside_by_its_own_side_series_not_by_the_row_claim():
    """Two crops have two visibilities: an unseen left row is not a contradiction of the right count.

    The BB9 frame 1131 shape. What settles the right side's lone zero is what the *right side* read
    immediately before and after it, not a rule that both sides must always be visible together, and
    not a presence flag that quarantines a reading nobody disagrees with.
    """
    rows = [dots(10, [0, 0]), dots(20, [1, 1]), dots(30, [None, 0], presence=(False, True)),
            dots(40, [2, 1]), dots(50, [3, 1])]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["winner"] == "p1" and game["winner_basis"] == "win_dots"
    assert not game["contested"] and game["counts_toward_set"] is True
    series = _series(result, "p2")
    assert series == [0, 1], "the rejected zero must not appear as a fall back from 1"
    assert all(b >= a for a, b in zip(series, series[1:])), "a side's pips only hold or rise in a game"
    rejected = [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert [q["side"] for q in rejected] == ["p2"], "the unseen left side is not what was rejected"


def test_the_rejected_observation_is_preserved_with_provenance_not_deleted():
    """Resolution is a judgement filed next to the evidence, never an edit of what the frame read."""
    rows = [dots(10, [0, 0]), dots(20, [1, 1]), dots(30, [None, 0], presence=(False, True)),
            dots(40, [2, 1]), dots(50, [3, 1])]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    rejected = [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert len(rejected) == 1
    kept = rejected[0]
    assert kept["side"] == "p2" and kept["value"] == 0 and kept["at"] == 30
    assert kept["row_drawn"] is True, "the count was rejected, not the claim that the row was visible"
    assert kept["frame"].endswith("frame-0001.jpg")
    assert kept["reason"], "a rejected observation must say why it was rejected"
    assert [s["value"] for s in kept["supported_by"]] == [1, 1], "and name the readings it yielded to"
    raw = [e["value"] for rnd in result["rounds"]
           for e in (rnd.get("win_dots_evidence") or {}).get("p2") or [] if e["at"] == 30]
    assert raw == [0], "the raw series still carries the rejected reading so the call can be reversed"


def test_an_isolated_bad_read_with_agreeing_presence_is_still_resolved():
    """Presence agreement is not a licence to contest: one weak reading inside a stable row yields."""
    rows = [dots(10, [0, 0]), dots(20, [1, 1]), dots(30, [1, 0], presence=(True, True)),
            dots(40, [2, 1]), dots(50, [3, 1])]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["winner"] == "p1" and not game["contested"]
    assert _series(result, "p2") == [0, 1]
    assert [q["value"] for rnd in result["rounds"]
            for q in (rnd.get("win_dots_rejected") or [])] == [0]


def test_only_the_visible_side_needs_to_reach_the_target():
    """A one-side-visible completion is a completion: the unseen side contributes nothing, ever."""
    rows = [dots(10, [0, None], presence=(True, False)), dots(20, [1, None], presence=(True, False)),
            dots(30, [2, None], presence=(True, False)), dots(40, [3, None], presence=(True, False))]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["winner"] == "p1" and game["winner_basis"] == "win_dots"
    assert not game["contested"] and game["counts_toward_set"] is True
    assert _series(result, "p2") == [], "an unseen row is not a zero"


def test_agreeing_presence_still_records_a_real_regression_and_still_contests():
    """A fall that nothing immediately contradicts is a real contradiction: an unrecovered drop bites."""
    rows = [dots(10, [0, 0]), dots(20, [1, 1]), dots(30, [1, 0], presence=(True, True)),
            dots(40, [2, 0]), dots(50, [3, 0])]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["contested"] is True and game["winner"] is None
    assert game["counts_toward_set"] is False
    assert "win_dots_regressed:p2" in game["contested_reasons"]
    assert result["unresolved"], "a contradiction must still queue for investigation"


def test_nothing_is_resolved_when_presence_is_not_stated():
    """Older producers and hand-built controls carry no presence claim and must behave unchanged."""
    rows = []
    for timestamp, value in ((10, [0, 0]), (20, [1, 1]), (30, [1, 0]), (40, [2, 0]), (50, [3, 0])):
        row = dots(timestamp, value)
        row.pop("win_dots_drawn")
        rows.append(row)
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    assert not [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert result["games"][0]["contested"] is True, "a fall without presence data is still a fall"


def test_a_fully_absent_row_still_contributes_no_counts():
    rows = [dots(10, [0, 0]), dots(20, [1, 1]), dots(30, [None, None], presence=(False, False)),
            dots(40, [2, 1]), dots(50, [3, 1])]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    assert result["games"][0]["winner"] == "p1"
    assert not [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])], \
        "a consistent absence is a clean abstention, not a contradiction"


# ---------- negative controls: what the temporal correction must refuse to resolve ----------

def _conf_row(timestamp, value, confidence):
    return {"timestamp": float(timestamp), "path": "frames/frame-%04d.jpg" % timestamp,
            "state": "gameplay", "evidence": "", "win_dots": [value, 0],
            "win_dots_drawn": [True, True], "win_dots_confidence": [confidence, 0.9],
            "confidence": 0.95}


def test_a_sustained_reset_and_rebuild_stays_ambiguous_and_keeps_every_reading():
    """LEFT 2,0,1,2,3 at full confidence is a new game being rebuilt, not two bad frames to delete.

    The rule used to reach across the whole game for *any* later value that matched the old high, so
    the 2 at the end invalidated the 0 and the 1 as well and the game published an uncontested p1
    winner -- while the same arithmetic sitting on top of a grouping it was supposed to be validating.
    A run of low counts that climbs back is the reset-and-rebuild hypothesis, and these frames cannot
    separate it from a wrong game boundary, so the game is contested and the raw series survives.
    """
    rows = [_conf_row(t, value, 0.95)
            for t, value in zip((0, 10, 20, 30, 40), (2, 0, 1, 2, 3))]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["winner"] is None, "a rebuild may not be resolved into a winner by arithmetic"
    assert game["contested"] is True and "win_dots_regressed:p1" in game["contested_reasons"]
    assert game["counts_toward_set"] is False
    assert game["dot_regressions"] == {"p1": {"from": 2, "to": 0, "at": 10.0}}, \
        "the first fall is the one reported"
    assert _series(result, "p1") == [2, 0, 1, 2, 3], "both low readings survive as evidence"
    assert not [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    fall = next(u for u in result["unresolved"] if u["type"] == "win_dots_regressed")
    assert fall["at"] == 10.0 and fall["side"] == "p1"


def test_a_single_reading_between_two_agreeing_firm_ones_is_resolved():
    """The other side of the same line, stated so a reviewer can disagree with it.

    ``3, 0, 3`` at full confidence is resolved where ``2, 0, 1, 2, 3`` is not, and the reason is the
    shape, not the confidence: a row that really reset cannot reach three again in the next observation
    of the same side, so the lone reading between two agreeing ones is the only claim that has to be
    wrong. It is still kept, and the game is decided with the disagreement visible.
    """
    rows = [_conf_row(t, value, 0.95) for t, value in zip((0, 10, 20), (3, 0, 3))]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["winner"] == "p1" and not game["contested"] and game["counts_toward_set"] is True
    rejected = [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert [(q["side"], q["value"], q["at"]) for q in rejected] == [("p1", 0, 10.0)]
    assert [s["value"] for s in rejected[0]["supported_by"]] == [3, 3]


def test_a_low_row_that_holds_for_two_readings_inside_one_round_is_still_one_anomaly():
    """Consecutive repeats compress, so what a dip has to survive is the round, not the frame count.

    A reset cannot happen inside a round -- a new game opens a new round -- so a row that reads low for
    several consecutive observations and is back at its old height before the round ends is one
    anomaly, not a rebuild. Low readings that land rounds apart are refused by the round bound instead;
    see the control that spaces the readings across rounds.
    """
    rows = [_conf_row(t, value, 0.95) for t, value in zip((0, 10, 20, 30), (2, 0, 0, 2))]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=2, games_per_set=3)
    game = result["games"][0]
    rejected = [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert len(rejected) == 1 and rejected[0]["value"] == 0
    assert game["winner"] == "p1" and not game["contested"]


def test_a_dip_bolder_than_its_neighbours_is_not_resolved():
    """Confidence bounds the rule too: the stronger claim is never the one thrown away."""
    rows = [_conf_row(t, value, confidence)
            for t, value, confidence in ((0, 3, 0.6), (10, 0, 0.95), (20, 3, 0.6))]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["contested"] is True and game["winner"] is None
    assert game["dot_regressions"] == {"p1": {"from": 3, "to": 0, "at": 10.0}}
    assert not [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]


def test_a_second_fall_on_one_side_is_not_another_isolated_misread():
    """One bad frame is a misread. A side whose row contradicts itself twice is simply untrustworthy."""
    rows = [_conf_row(t, value, 0.9)
            for t, value in zip((0, 10, 20, 30, 40, 50), (3, 0, 3, 0, 3, 0))]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    game = result["games"][0]
    assert game["contested"] is True and game["winner"] is None
    rejected = [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert len(rejected) == 1, "the correction is bounded per side and per game"
    assert game["dot_regressions"]["p1"]["to"] == 0


def test_a_fall_whose_neighbours_are_rounds_away_is_not_resolved():
    """Locality is measured in rounds, because a round is bounded by its own clock.

    Agreeing readings minutes either side of a low count are not local support: between them a whole
    game could have been played, which is precisely the alternative hypothesis being tested.
    """
    rows = []
    for timestamp, clock, value in [
            (0, 60, 3), (10, 50, None), (20, 60, None), (30, 50, None), (40, 60, 0),
            (50, 50, None), (60, 60, None), (70, 50, None), (80, 60, None), (90, 50, 3)]:
        rows.append({"timestamp": float(timestamp), "path": "frames/frame-%04d.jpg" % timestamp,
                     "state": "gameplay", "evidence": "timer %d" % clock, "round_clock": clock,
                     "win_dots": [value, 0], "win_dots_drawn": [True, True],
                     "win_dots_confidence": [0.5, 0.9], "confidence": 0.95})
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=5, games_per_set=3)
    p1_rounds = [(r["id"], (r.get("win_dots_evidence") or {}).get("p1") or [])
                 for r in result["rounds"]]
    assert [len(e) for _, e in p1_rounds].count(1) >= 3, "the fixture must space the reads across rounds"
    game = result["games"][0]
    assert not [q for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])], \
        "distant agreement is not local support"
    assert game["contested"] is True and game["winner"] is None


def test_a_short_callout_fragment_absorbs_into_the_round_carrying_the_winning_read():
    """Absorbing a fragment must not delete the neighbour it is absorbed into.

    A ROUND callout read one frame before the clock resets opens a span too short to be a round, and the
    absorber has to merge it somewhere. That branch used to compare the fragment's span with *itself*,
    which is always equal, so every absorption kept the fragment's near-empty record and threw away the
    round it landed on. Here the pip reading that names the game winner sits in that round, so the game
    came out with no winner -- and the surviving record still listed the merge as an absorbed span, which
    is exactly how a lost observation hides: reported as bookkeeping, not as a gap.
    """
    def pip_row(timestamp, clock, value, call=None):
        return {"timestamp": float(timestamp), "path": "frames/frame-%04d.jpg" % timestamp,
                "state": "gameplay", "evidence": "timer %d" % clock if clock is not None else
                "'ROUND 1' callout", "round_clock": clock, "round_call": call,
                "win_dots": [value, 0] if value is not None else None,
                "win_dots_drawn": [True, True] if value is not None else None,
                "win_dots_confidence": [0.9, 0.9] if value is not None else None,
                "confidence": 0.95}

    rows = [pip_row(0, 60, None), pip_row(10, 50, None), pip_row(20, 40, 3), pip_row(40, 20, 3),
            pip_row(50, None, None, call=1), pip_row(51, 60, None), pip_row(60, 50, None),
            pip_row(70, 40, None)]
    result = track(rows)
    holding = [r for r in result["rounds"] if (r.get("win_dots_seen") or {}).get("p1")]
    assert holding, "the winning pip reading vanished with the round it was absorbed into"
    assert holding[0]["start"] <= 20 < (holding[0]["end"] or 0), "the series moved off its own round"
    assert holding[0]["end"] == pytest.approx(40.0), \
        "the absorbed fragment dragged a round with a winning read out to %s" % holding[0]["end"]
    # The fragment here is a bare ROUND banner with no clock inside it, so it is absorbed *forward*,
    # into the round whose banner it shows. Absorption may go either way, but it always leaves a record.
    moved = [r for r in result["rounds"] if r.get("absorbed_spans") or r.get("absorbed_openers")]
    assert len(moved) == 1 and moved[0].get("absorbed_openers"), \
        "the fragment vanished instead of being recorded on the round that took it"
    assert moved[0]["start"] == pytest.approx(50.0) and moved[0]["number_observed"] == 1, \
        "the opener neither moved its round back nor brought its ROUND number with it"
    durations = [(r["end"] or r["start"]) - r["start"] for r in result["rounds"]]
    assert not [d for d in durations if d < bg.MIN_ROUND_SECONDS], "the fragment survived as a round"
    game = result["games"][0]
    assert game["winner"] == "p1", "a game whose winning read survived came out without a winner: %s" % game
    assert game["winner_basis"] == "win_dots" and game["contested"] is False


def test_play_between_two_games_is_not_counted_as_covered_by_one():
    """Coverage asks **one** game to span the row; two independent clauses will always say yes.

    The rows that matter are exactly the ones between games -- the victory pose after the K.O. that
    named a winner, the dwell before the next round opens. The old test asked only whether *some* game
    started at or before the row and *some* game ended at or after it, and a row in a gap satisfies
    both while lying inside neither. On the accepted stage-3 trial that reported 54 of 59 play rows
    covered when only 51 were; here the two victory-pose frames at 52 and 54 s are the same case.
    """
    def timed(t, clock=None, end=None, dots=None):
        evidence = []
        if clock is not None:
            evidence.append("timer {}".format(clock))
        if end:
            evidence.append("{} announcement".format(end.upper().replace("_", " ")))
        return {"timestamp": float(t), "path": "frames/frame-{:04d}.jpg".format(t),
                "state": "gameplay", "evidence": "; ".join(evidence), "round_clock": clock,
                "round_end": end, "win_dots": dots,
                "win_dots_drawn": [True, True] if dots else None,
                "win_dots_confidence": [0.9, 0.9] if dots else None, "confidence": 0.95}

    rows = [timed(0, clock=60), timed(10, clock=50, end="ko", dots=[1, 0]),
            timed(20, clock=60), timed(30, clock=50, end="ko", dots=[2, 0]),
            timed(40, clock=60), timed(50, clock=50, end="ko", dots=[3, 0]),
            timed(52), timed(54),                                   # play between the two games
            timed(60, clock=60), timed(70, clock=50, end="ko", dots=[1, 0])]
    out = track(rows)
    games, acc = out["games"], out["accounting"]
    assert len(games) == 2 and games[0]["winner"] == "p1", \
        "the fixture stopped producing a concluded game followed by another: %s" % games
    assert acc["gameplay_observations"] == 10
    assert acc["gameplay_inside_games"] == 8, "the two gap rows were counted as covered"
    assert acc["gameplay_between_games"] == 2
    assert acc["gameplay_inside_games"] + acc["gameplay_between_games"] == \
        acc["gameplay_observations"], "coverage has to close against the play count"


def pip(t, dots, clock=None, end=None, res=False, state="gameplay"):
    """A play row carrying a side-specific pip reading, which ``row()`` cannot express."""
    evidence = []
    if clock is not None:
        evidence.append("timer %d" % clock)
    if end:
        evidence.append("%s announcement" % end.upper().replace("_", " "))
    if res:
        evidence.append("'YOU WIN' victory banner over KO camera")
    return {"timestamp": float(t), "state": state, "confidence": 0.95,
            "evidence": "; ".join(evidence), "round_clock": clock, "round_end": end,
            "result_transition": res, "win_dots": dots, "win_dots_drawn": [True, True],
            "win_dots_confidence": [0.9, 0.9], "path": "frames/frame-%04d.jpg" % int(t)}


def test_a_ko_before_the_pip_update_is_a_correct_end_not_a_mismatch():
    """The K.O. stops the clock; the pip arrives with the victory pose seconds later.

    Measured on the executed trial at 1961.96 and 2133.13 s, where the K.O. row and the row naming the
    winner are 2.0 s apart. An earlier version of this file treated any gap between the two as a
    boundary error, which is wrong in direction: the end legitimately precedes its confirmation, so the
    record keeps both and claims neither as the other.
    """
    rows = [pip(0, None, clock=60), pip(30, [1, 0], clock=30, end="ko", res=True),
            pip(32, [2, 0]), pip(40, None, clock=60),
            # the K.O. row still shows two: the round is over, the pip is not yet drawn
            pip(70, [2, 0], clock=30, end="ko", res=True),
            pip(72, [3, 0])]                                    # the row that names the winner
    game = track(rows, rounds_per_game=3)["games"][-1]
    assert game["end"] == pytest.approx(70.0), \
        "a K.O. row is the conclusion event; the end must not chase the pip to %.2f" % game["end"]
    assert game["conclusion_evidence"] == "observed_end_event"
    assert game["winner_confirmed_at"] == pytest.approx(72.0)
    assert game["winner_confirmation_lag_seconds"] == pytest.approx(2.0)
    assert game["boundary_certain"] is True, "an observed end event is not an uncertain boundary"
    assert game["end_conflict"] is None
    # the clip still has to contain the confirmation, which is the only reason to keep watching
    assert game["clip_end_at"] >= 72.0, \
        "clip ends before the row that names the winner: %s" % game["clip_end_at"]


def test_an_end_that_trails_its_own_evidence_is_reported_uncertain_not_certain():
    """The other direction: a clock still drawn over a concluded round, and a dwell mistaken for an end.

    Both failure shapes come from the executed trial. Game 1 ended where the camera gave up on the
    scoreboard 216 s after the last readable clock, and game 2's clock was still on screen 18.0 s after
    the pip and the result menu had already concluded it. Neither is allowed to read as a resolved
    boundary now, and the winner is kept in both cases: the boundary is what is open, not the result.

    The first shape is no longer *merely* uncertain. The recording supplies its own tighter bound -- the
    row that names the winner and the result screen that follows it -- so an end that ignores them is
    reporting the menu dwell as doubt it never had. That is what this now asserts.
    """
    # Three rounds so a game actually closes, the last of them ending on a clock rather than an
    # event: 80-100 s is the round, 100 s its last readable clock, and nothing is read again until the
    # next round opens at 260 s.
    dwell = [pip(0, None, clock=60), pip(20, [1, 0], clock=40, end="ko", res=True),
             pip(22, [1, 0]),
             pip(40, None, clock=60), pip(60, [2, 0], clock=40, end="ko", res=True),
             pip(62, [2, 0]),
             pip(80, None, clock=60), pip(100, [3, 0], clock=40, res=True), pip(102, [3, 0])]
    dwell += [{"timestamp": float(x), "state": "gameplay", "confidence": 0.9,
               "evidence": "", "round_clock": None, "win_dots": None, "path": "f.jpg"}
              for x in range(110, 255, 10)]        # play-like frames while the camera holds a menu
    dwell += [pip(260, None, clock=60), pip(280, [1, 0], clock=40, end="ko", res=True)]
    result = track(dwell, rounds_per_game=3)
    first = result["games"][0]
    assert first["end"] == pytest.approx(100.0), \
        "the end fell back to the dwell (%.1f) instead of the round's last readable clock" % first["end"]
    assert first["conclusion_evidence"] == "last_readable_clock"
    assert first["winner"] == "p1", "the bounded game must still name its winner"
    # The bound is the pip that reached three, not the next round 160 s later.
    assert first["end_uncertainty"]["high"] == pytest.approx(100.0)
    assert first["end_uncertainty"]["bounded_by"] == "winner_confirmation"
    assert first["end_uncertainty"]["seconds_wide"] == pytest.approx(0.0)
    assert first["end_uncertainty"]["next_opening_at"] == pytest.approx(260.0)
    assert first["end_uncertainty"]["tape_after_conclusion_seconds"] == pytest.approx(160.0), \
        "shrinking the bracket must not hide the dwell: the dwell gets its own field"
    assert first["end_status"] == "bounded"
    assert first["provisional_end"] is True, "a bounded end is not an export timestamp"
    assert first["boundary_certain"] is True
    assert first["last_observed_play_at"] > first["end"], "the dwell should be reported, not absorbed"
    assert first["clip_end_at"] <= first["end_uncertainty"]["next_opening_at"]
    assert first["clip_end_at"] >= 102.0, "the clip still has to contain the row naming the winner"
    queued = [u for u in result["unresolved"] if u["type"] == "game_end_bounded"]
    assert len(queued) == 1 and queued[0]["winner"] == first["winner"], \
        "a bounded boundary stays an open question while the winner is retained"
    assert not [u for u in result["unresolved"] if u["type"] == "game_end_uncertain"], \
        "a conclusion pinned inside one sampling step is not the wide-open shape"

    # A narrow gap is not the same as a certain boundary either. Here the clock is still *falling* after
    # the pip reached three, which is a contradiction between two observations, not a bound: the digits
    # cannot tick down over a game that was already won.
    stale = [pip(0, None, clock=60), pip(20, [3, 0], clock=40, res=True), pip(22, [3, 1]),
             pip(40, None, clock=30), pip(41, None, clock=29),
             pip(60, None, clock=60), pip(80, [1, 0], clock=40, end="ko", res=True)]
    late = track(stale, rounds_per_game=3)["games"][0]
    assert late["conclusion_evidence"] == "last_readable_clock"
    assert late["end_conflict"], "a clock outliving the pip and the menu must be flagged, not trusted"
    assert late["end_status"] == "conflicting" and late["provisional_end"] is True
    assert late["boundary_certain"] is False, \
        "a 1 s gap read as certainty while the evidence sits %.1f s earlier" % (
            late["end_conflict"]["seconds_late_after_evidence"])
    kinds = {e["kind"] for e in (late["end_conflict"]["evidence_earlier_than_clock"])}
    assert "winner_confirmation" in kinds and "result_transition" in kinds


def test_a_frozen_clock_does_not_set_the_end_but_a_falling_one_does():
    """The digits keep drawing after the K.O., so a repeated reading is not proof of play.

    Measured on the executed trial's first game: the clock read 35 at 1370.369, 1371.370 and 1372.371 s,
    the pip naming the winner landed on the last of the three, and the end the tracker quoted was that
    last one. A clock that only repeats itself bounds the conclusion from above; only a clock that
    *falls* proves the round was still being played at the instant it was read.
    """
    rows = [pip(0, None, clock=60), pip(30, [1, 0], clock=30, end="ko", res=True),
            pip(32, [1, 0]),
            pip(60, None, clock=60), pip(90, [2, 0], clock=30, end="ko", res=True),
            pip(92, [2, 0]),
            pip(120, None, clock=60), pip(140, None, clock=40), pip(150, None, clock=35),
            pip(151, [3, 0], clock=35), pip(152, None, clock=35),
            pip(180, None, clock=60), pip(200, [1, 0], clock=40, end="ko", res=True)]
    game = track(rows, rounds_per_game=3)["games"][0]
    assert game["end"] == pytest.approx(150.0), \
        "the end sat on a frozen digit (%.1f) instead of the last clock that fell" % game["end"]
    assert game["end_uncertainty"]["live_bound"]["frozen_readings_after"] == 2
    assert game["end_uncertainty"]["live_bound"]["frozen_to_at"] == pytest.approx(152.0)
    assert game["winner_confirmed_at"] == pytest.approx(151.0)
    assert game["end_status"] == "bounded", "the pip inside the freeze is the tighter evidence"
    assert game["end_uncertainty"]["seconds_wide"] == pytest.approx(1.0)
    # A round whose clock keeps falling is watched to its end, and no freeze is claimed.
    moving = [pip(0, None, clock=60), pip(30, [1, 0], clock=30, end="ko", res=True), pip(32, [1, 0]),
              pip(60, None, clock=60), pip(90, [2, 0], clock=30, end="ko", res=True), pip(92, [2, 0]),
              pip(120, None, clock=60), pip(140, None, clock=40), pip(150, None, clock=30),
              pip(160, [3, 0], clock=20),
              pip(200, None, clock=60), pip(220, [1, 0], clock=40, end="ko", res=True)]
    held = track(moving, rounds_per_game=3)["games"][0]
    assert held["end"] == pytest.approx(160.0)
    assert held["end_uncertainty"]["live_bound"]["frozen_readings_after"] in (None, 0)


def test_a_conclusion_written_without_a_ko_banner_is_still_a_conclusion():
    """The game's own words for the end of a round are not limited to K.O. and TIME UP.

    The row that concludes the executed trial's first game reads "Round-ending PERFECT banner over active
    stage, timer 35", and a pattern that only knew the two famous banners left that boundary to a clock
    bound 216 s wide. The banner word alone is refused, though: 6 of the 14 rows saying PERFECT on this
    recording are the hit-feedback tag drawn in the middle of a live round, so the phrase has to say the
    round ended, and the negation control still has to survive a denial in an earlier clause.
    """
    ko_free = [pip(0, None, clock=60), pip(30, [1, 0], clock=30, end="ko", res=True), pip(32, [1, 0]),
               pip(60, None, clock=60), pip(90, [2, 0], clock=30, end="ko", res=True), pip(92, [2, 0]),
               {"timestamp": 120.0, "state": "gameplay", "confidence": 0.9, "path": "f.jpg",
                "evidence": "Round-ending PERFECT banner over active stage, timer 35",
                "round_clock": 35},
               pip(122, [3, 0], clock=35),
               pip(160, None, clock=60), pip(180, [1, 0], clock=40, end="ko", res=True)]
    game = track(ko_free, rounds_per_game=3)["games"][0]
    assert game["end"] == pytest.approx(120.0), \
        "a round-end callout was thrown away and the end drifted to %s" % game["end"]
    assert game["conclusion_evidence"] == "observed_end_event"
    assert game["end_status"] == "observed" and game["provisional_end"] is False
    assert game["winner_confirmation_lag_seconds"] == pytest.approx(2.0), \
        "the callout still precedes the pip, and that ordering is not an error to reconcile"

    # The hit-feedback PERFECT is not a round end, and a denial in one clause cannot cancel an
    # assertion in the next.
    for evidence in ["In-game 'PERFECT' hit banner and 'Counter' tag over active fight, timer 40",
                     "Broadcast scoreboard still shows 0-0 (set not updated); no round-end pose"]:
        row = {"timestamp": 200.0, "state": "gameplay", "confidence": 0.9, "path": "f.jpg",
               "evidence": evidence, "round_clock": 40}
        assert bg.parse_round_facts(row)["round_end"] is None, \
            "a hit-feedback tag or a denied phrase was read as a concluded round"
    row = {"timestamp": 200.0, "state": "gameplay", "confidence": 0.9, "path": "f.jpg",
           "evidence": "Broadcast scoreboard still shows 0-0 (set not updated); "
                       "round-end victory pose with the winner on the throne",
           "round_clock": 23}
    assert bg.parse_round_facts(row)["round_end"] == "callout", \
        "a scoreboard denial cancelled the round-end claim that followed it"


def test_a_round_banner_opens_the_next_game_not_the_one_just_won():
    """An absorbed opener used to drag a game's boundary twenty seconds past its own K.O.

    The trial's second round ends on a K.O. at 1800.799 s, its pip confirms the winner at 1802.801 s, the
    broadcast holds a menu for eighteen seconds, and the next round's "ROUND 1" banner is sampled at
    1820.819 s -- one whole second before that round's first clock reading. As a span of its own that
    banner is 0 s, so the absorber merged it backwards, and because the merge let any later end win, the
    game's boundary moved to the banner: 20.02 s after the fight ended, 18.0 s after the evidence that
    named its winner. An opener belongs in front of the round it opens.
    """
    rows = [pip(0, None, clock=60), pip(30, [1, 0], clock=30, end="ko", res=True), pip(32, [1, 0]),
            pip(60, None, clock=60), pip(90, [2, 0], clock=30, end="ko", res=True), pip(92, [2, 0])]
    # the deciding round: K.O. at 120, pip at 122, then eighteen seconds of menu and an opener at 140
    rows += [pip(120, None, clock=60),
             {"timestamp": 120.0, "state": "gameplay", "confidence": 0.9, "path": "f.jpg",
              "evidence": "in-game K.O. banner over the fighters, timer 23", "round_clock": 23,
              "round_end": "ko"},
             {"timestamp": 122.0, "state": "gameplay", "confidence": 0.9, "path": "f.jpg",
              "evidence": "victory pose, timer 23", "round_clock": 23, "result_transition": True,
              "win_dots": [3, 2], "win_dots_drawn": [True, True], "win_dots_confidence": [0.9, 0.9]},
            ]
    rows += [{"timestamp": float(x), "state": "post_match_menu", "confidence": 0.9,
              "evidence": "post-match menu", "path": "f.jpg"} for x in (126, 130, 134)]
    rows += [{"timestamp": 140.0, "state": "gameplay", "confidence": 0.9, "path": "f.jpg",
              "evidence": "'ROUND 1' banner with both fighters placed"},
             pip(141, None, clock=60), pip(160, [1, 0], clock=40, end="ko", res=True)]
    result = track(rows, rounds_per_game=3)
    won, following = result["games"]
    assert won["end"] == pytest.approx(120.0), \
        "the boundary chased the absorbed opener to %.3f instead of the K.O. that ended it" % won["end"]
    assert won["conclusion_evidence"] == "observed_end_event"
    assert won["end_status"] == "observed" and won["boundary_certain"] is True
    assert won["winner_confirmation_lag_seconds"] == pytest.approx(2.0)
    assert won["end_conflict"] is None, "a legitimate K.O.-before-pip order must not be flagged"
    assert following["start"] == pytest.approx(140.0), \
        "the opener stayed behind the game it belongs in front of"
    held = [r for r in result["rounds"] if (r.get("absorbed_openers"))]
    assert held and held[0]["number_observed"] == 1, \
        "the absorbed ROUND banner was not recorded on the round it opens"
    assert not [f for f in result["unresolved"]
                if f["type"] in ("game_end_uncertain", "game_end_bounded")]


def test_an_observed_end_survives_a_backward_absorption():
    """Where a fragment must still go backwards, the round it lands on keeps the end it was shown.

    The forward rule is only for an opener with no clock inside it. A fragment of any other shape is
    merged onto the round before it, and that is where the earlier defect lived: the merge let the later
    end win whatever its kind, so a fragment whose end was merely implied replaced a K.O. the recording
    had actually drawn.
    """
    rows = [pip(0, None, clock=60), pip(30, [1, 0], clock=30, end="ko", res=True), pip(32, [1, 0]),
            pip(60, None, clock=60), pip(90, [2, 0], clock=30, end="ko", res=True), pip(92, [2, 0]),
            pip(120, None, clock=60), pip(150, [3, 0], clock=30, end="ko", res=True), pip(152, [3, 0]),
            # a two-second sliver that has a clock of its own, so it is absorbed backwards: 154 opens it
            # (45 is above the round's 30) and 160 closes it by restarting again
            pip(154, None, clock=45), pip(160, None, clock=60),
            pip(180, [1, 0], clock=40, end="ko", res=True)]
    result = track(rows, rounds_per_game=3)
    won = result["games"][0]
    assert won["winner"] == "p1" and won["rounds"] == 3
    assert won["end"] == pytest.approx(150.0), \
        "an absorbed fragment moved the boundary off the K.O. to %.3f" % won["end"]
    assert won["end_status"] == "observed" and won["boundary_certain"] is True
    absorbed = [r for r in result["rounds"] if r.get("absorbed_spans")]
    assert absorbed, "the fixture stopped producing the backward absorption this guards"
    span = absorbed[0]["absorbed_spans"][0]
    assert span["end_was_observed"] is False and span["end_basis"] == "round_end_not_observed", \
        "the merge record cannot tell a bound from an observation: %s" % span


# ---------- the pip row read after its own round closed -----------------------------------------
# The unseen trial's fourth game turned on this: a genuine LEFT 3 at the K.O., and two seconds later a
# LEFT 1 that did not exist. Read as a fall it contested the game and withheld a winner the footage named.
# Timing and direction only *find* the candidate; what excuses it is a frame that contradicts itself about
# whether the HUD was on screen at all, plus a fall that does not claim to be the bolder reading. So the
# fixture's default is a corroborated frame, and each case below removes exactly one ground for excusing.

def _closed_round(readings, end_at=20.0, end_basis="round_end_ko", counterpart=False):
    """One side's series on one round, as the merge records it.

    ``counterpart`` is what the same answers said about the *other* side's row at each of these instants:
    False = drawn:false (the frame is not a complete scoreboard), True = drawn:true (a live scoreboard),
    None = the answers said nothing. Only p1 carries evidence, which is the one-sided shape the rule has
    to keep working for: a side is judged on its own series, never on whether both crops were readable.
    """
    return {"start": 0.0, "end": end_at, "round_end_at": end_at, "end_basis": end_basis,
            "win_dots_evidence": {"p1": [{"value": v, "at": t, "confidence": c,
                                          "counterpart_row_drawn": counterpart}
                                         for v, t, c in readings]}}


def test_a_fall_read_inside_the_result_sequence_does_not_contest_the_round_that_settled_it():
    rnd = _closed_round([(3, 20.0, 0.92), (1, 22.002, 0.55)])
    assert bg._dot_regressions([rnd]) == {}, \
        "a concluded round cannot have a pip taken back by a row that is leaving the frame"
    set_aside = rnd["win_dots_after_round_end"][0]
    assert set_aside["value"] == 1 and set_aside["standing_count"] == 3
    assert 2.0 < set_aside["seconds_after_round_end"] < 2.1
    assert "counterpart row" in set_aside["reason"]
    # The raw series is what a reviewer has to be able to re-judge: nothing is deleted here.
    assert [e["value"] for e in rnd["win_dots_evidence"]["p1"]] == [3, 1]
    assert rnd.get("win_dots_rejected") is None, "this is not the isolated-misread path"
    assert rnd.get("win_dots_after_round_end_declined") is None


def test_a_fall_on_a_frame_that_still_draws_both_rows_is_the_contradiction_it_was():
    # The count dropped while the same answer insists both rows are on screen. That is a scoreboard saying
    # a pip went out, and nothing about the round end excuses it.
    rnd = _closed_round([(3, 20.0, 0.92), (1, 22.002, 0.55)], counterpart=True)
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 1, "at": 22.002}}
    assert rnd["win_dots_after_round_end_declined"][0]["declined_because"] == \
        "no frame reports this count on a HUD whose counterpart row is absent"


def test_a_counterpart_row_nobody_read_is_not_evidence_that_the_hud_left():
    # Silence is not a presence claim. Treating a missing counterpart as corroboration would excuse every
    # fall on every one-crop-at-a-time recording, which is the opposite of the caution this rule needs.
    rnd = _closed_round([(3, 20.0, 0.92), (1, 22.002, 0.55)], counterpart=None)
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 1, "at": 22.002}}
    assert "counterpart row is absent" in \
        rnd["win_dots_after_round_end_declined"][0]["declined_because"]


def test_a_bolder_correction_is_not_set_aside_however_well_the_hud_transition_agrees():
    # The review's probe: a 3 read at 0.30 on the K.O. frame and a 2 two seconds later at 0.99. A K.O.
    # proves the round ended; it does not prove the 3 was ever right, and here the later reading claims
    # louder to be the correction. Confidence is used only as an ordering between two readings of one row.
    rnd = _closed_round([(3, 20.0, 0.30), (2, 22.002, 0.99)])
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 2, "at": 22.002}}, \
        "the exception must not quietly prefer the shakier older number"
    assert "more confidently" in rnd["win_dots_after_round_end_declined"][0]["declined_because"]


def test_a_fall_the_recording_cannot_order_is_left_contested():
    # No confidence on one of the pair means no ordering, and an unordered pair is not a settled answer
    # about which reading to keep. Uncertainty is preserved by leaving the contradiction standing.
    rnd = _closed_round([(3, 20.0, None), (1, 22.002, 0.55)])
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 1, "at": 22.002}}
    assert "cannot be ordered" in rnd["win_dots_after_round_end_declined"][0]["declined_because"]


def test_the_same_fall_long_after_the_round_is_still_the_contradiction_it_was():
    # 40 s later is not a victory pose. Without the bound this rule would excuse every late fall, which
    # is the maximum-of-everything bug the fall rule was written to replace.
    rnd = _closed_round([(3, 20.0, 0.92), (1, 60.0, 0.55)])
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 1, "at": 60.0}}
    assert "win_dots_after_round_end" not in rnd


def test_only_a_trailing_reading_is_excused_a_reading_that_climbs_back_contests_again():
    # 3, 1, 2 is not a row leaving the frame; it is a series disagreeing with itself, and the game has to
    # stay contested or the rule becomes a way to prefer whichever number was read last. Corroborated
    # throughout, so the only thing refusing the exception is that the series did not end on the fall.
    rnd = _closed_round([(3, 20.0, 0.92), (1, 22.002, 0.55), (2, 24.0, 0.6)])
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 1, "at": 22.002}}


def test_a_late_pip_that_raises_the_row_is_evidence_not_an_anomaly():
    # Measured twice in this recording: the winning dot arrives after the K.O. that decided it, so the
    # direction is the whole rule and a rise must never be bounded away.
    rnd = _closed_round([(1, 20.0, 0.95), (3, 22.002, 0.92)])
    assert bg._dot_regressions([rnd]) == {}
    assert "win_dots_after_round_end" not in rnd


def test_a_round_whose_end_was_never_observed_bounds_nothing():
    # An inferred end is a bound, not an observation, and a bound cannot certify a settled score. The
    # fall stays exactly as contested as it was before this rule existed.
    rnd = _closed_round([(3, 20.0, 0.92), (1, 22.002, 0.55)], end_basis="round_end_not_observed")
    assert bg._dot_regressions([rnd]) == {"p1": {"from": 3, "to": 1, "at": 22.002}}


def test_a_reading_keeps_what_the_same_answer_said_about_the_other_row():
    # The corroboration above is worth nothing if the merge does not carry the counterpart's presence
    # claim down to the reading, which is the one place a fall is judged. Measured on the trial's own
    # frames: 3052.04884 s answered LEFT 1 with RIGHT drawn:false, 3054.050839 s the mirror image.
    result = track([dots(10, [1, 0]), dots(20, [2, 1]), dots(30, [3, None], presence=(True, False))],
                   rounds_per_game=3)
    entries = [e for rnd in result["rounds"] for e in (rnd.get("win_dots_evidence") or {}).get("p1") or []]
    assert [e["value"] for e in entries] == [1, 2, 3]
    assert entries[0]["counterpart_row_drawn"] is True
    assert entries[-1]["counterpart_row_drawn"] is False, \
        "the frame's claim about the other row was dropped, so no fall on it can ever be corroborated"


# ---------- an announcement drawn while its own round is still counting down ----------

def pip_row(t, value, presence=(True, True), confidence=(0.95, 0.95), clock=None):
    """A row that read the win row, and optionally the digits, in the same answer."""
    return {"timestamp": float(t), "state": "gameplay", "confidence": 0.9, "player1": "Ann",
            "player2": "Zoe", "characters": [], "evidence": "",
            "round_clock": clock, "win_dots": list(value) if value else None,
            "win_dots_drawn": list(presence) if value else None,
            "win_dots_confidence": list(confidence) if value else None,
            "path": "frames/frame-%04d.jpg" % int(t)}


def test_a_round_announcement_inside_a_falling_clock_is_the_same_round():
    """The measured 6,071.065-6,096.090 case: 60, 58, 53 with ROUND 2 still on screen.

    One round was cut in two because the banner was read twice, and the extra unit is what let the game
    layer's round count open a boundary the win row later contradicted. The digits are the primitive
    here: a round begins by taking the clock back up, and this one kept falling.
    """
    rows = [row(6071.065, clock=60, call=2), row(6076.070, clock=58, call=2),
            row(6081.075, clock=53, call=2), row(6086.080, clock=48),
            row(6091.085, clock=43), row(6096.090, clock=38)]
    out = track(rows)
    assert starts(out) == [6071.06], [r["start"] for r in out["rounds"]]
    round_ = out["rounds"][0]
    assert round_["clock_readings"] == 6
    assert round_["number_observed"] == 2
    banners = round_.get("banners_seen_inside") or []
    assert len(banners) == 1 and banners[0]["clock"] == 53 and banners[0]["open_round_clock"] == 58
    assert banners[0]["number_matches"] is True, "the banner that agreed with the open round is missing"


def test_a_clock_taken_back_up_opens_a_round_even_with_the_same_announcement_on_screen():
    """The genuine reset must survive the new rule: 38 falling, then 60 again."""
    rows = [row(0, clock=60, call=1), row(12, clock=48), row(25, clock=35),
            row(60, clock=60, call=2), row(75, clock=45)]
    out = track(rows)
    assert starts(out) == [0.0, 60.0]
    assert out["rounds"][1]["start_basis"] == "round_clock_reset"


def test_an_announcement_on_a_row_that_never_read_the_clock_still_opens_a_round():
    """An absence is not continuity. A banner with no digits is judged on the banner alone, as before."""
    rows = [row(0, clock=60, call=1), row(12, clock=48), row(30, call=2), row(42, clock=40)]
    out = track(rows)
    assert starts(out) == [0.0, 30.0]
    assert out["rounds"][1]["start_basis"] == "round_callout"
    assert not out["rounds"][0].get("banners_seen_inside")


def test_a_clock_stalled_at_a_full_time_does_not_make_a_later_announcement_the_same_round():
    """60 read again 45 s later proves nothing about a restart, which is why the banner rule exists.

    The new rule refuses to merge only when there was a fall to continue. With the digits still at 60
    there is none, so the announcement keeps its traditional power and opens the round.
    """
    rows = [row(0, clock=60, call=1), row(45, clock=60, call=2)]
    out = track(rows)
    assert starts(out) == [0.0, 45.0]


def test_an_announcement_naming_a_different_round_while_the_clock_falls_is_reported_not_swallowed():
    """Two positive readings that disagree: the banner still opens, and the conflict is kept.

    Merging here would be choosing the digits over the game's own number, which is a different claim
    about the recording than reading one banner twice.
    """
    rows = [row(0, clock=60, call=2), row(12, clock=48), row(30, clock=40, call=3), row(45, clock=25)]
    out = track(rows)
    assert starts(out) == [0.0, 30.0]
    conflicts = out["rounds"][0].get("round_numbering_conflicts") or []
    assert len(conflicts) == 1 and conflicts[0]["announced"] == 3
    assert conflicts[0]["open_round_number"] == 2 and conflicts[0]["clock"] == 40


# ---------- a round count the win row refuses to agree with ----------

def _five_rounds_without_a_conclusion():
    """Six reset-opened rounds whose clocks fall only as fast as the recording advances.

    A clock that outruns the tape is quarantined as a bad reading, so fixtures have to count down
    honestly or they test the guard instead of the rule. Round 5 closes 2-2 and round 6 opens 2-2, which
    is the shape that made the round-count cap want a boundary the win row refuses.
    """
    rows = []
    for index in range(6):
        base = index * 40.0
        rows.extend([row(base, clock=60), row(base + 12, clock=48), row(base + 25, clock=35)])
        if index == 4:
            rows.append(pip_row(base + 27, [2, 2]))
        if index == 5:
            # Read *after* the opening row, or the sorted fixture hands it to the round before it.
            rows.append(pip_row(base + 0.5, [2, 2]))
            rows.append(pip_row(base + 27, [3, 2]))
    return sorted(rows, key=lambda r: r["timestamp"])


def test_a_round_count_may_not_open_a_game_the_win_row_says_was_not_a_boundary():
    """2-2 read at the end of the fifth round and again at the opening of the sixth: no game split.

    The cap still does its job -- it stops the run pretending nothing happened -- by recording the cut it
    refused to make and leaving the group open, which the queue reports as an incomplete game rather
    than a concluded one.
    """
    out = track(_five_rounds_without_a_conclusion(), rounds_per_game=3)
    assert [g["start"] for g in out["games"]] == [0.0], "the contradicted boundary was made anyway"
    game = out["games"][0]
    assert game["rounds"] == 6 and game["winner"] == "p1" and game["status"] == "complete"
    notes = game["round_count_beyond_target"]
    assert len(notes) == 1 and notes[0]["type"] == "win_row_persisted_across_the_candidate_boundary"
    assert notes[0]["readings"] == {"p1": [2, 2], "p2": [2, 2]}
    assert notes[0]["at"] == 200.0 and notes[0]["rounds_seen"] == 5


def test_the_round_count_cap_still_closes_a_run_that_no_win_row_can_settle():
    """Negative control: with nothing read on the win row, the cap behaves exactly as it always did."""
    rows = []
    for index in range(6):
        base = index * 40.0
        rows.extend([row(base, clock=60), row(base + 12, clock=48), row(base + 25, clock=35)])
    out = track(rows, rounds_per_game=3)
    assert len(out["games"]) == 2
    first = out["games"][0]
    assert first["basis"] == "round_count_exceeded_without_conclusion"
    assert first["status"] == "incomplete" and first["winner"] is None
    assert first["round_count_beyond_target"] == []
    assert [u for u in out["unresolved"] if u["type"] == "game_incomplete"]


def test_a_row_that_cleared_across_the_candidate_boundary_is_a_new_game_and_stays_one():
    """The genuine case the rule must not eat: 2-2 at the end of the fifth, 0-0 at the next opening."""
    rows = []
    for index in range(6):
        base = index * 40.0
        rows.extend([row(base, clock=60), row(base + 12, clock=48), row(base + 25, clock=35)])
        if index == 4:
            rows.append(pip_row(base + 27, [2, 2]))
        if index == 5:
            rows.append(pip_row(base, [0, 0]))
    out = track(sorted(rows, key=lambda r: r["timestamp"]), rounds_per_game=3)
    assert len(out["games"]) == 2, "a cleared win row was treated as continuity"
    assert out["games"][0]["round_count_beyond_target"] == []


def test_reaching_the_target_is_a_conclusion_not_a_contradiction():
    """The cap must not defer to a persisted row once somebody has actually won the game."""
    rows = []
    for index in range(6):
        base = index * 40.0
        rows.extend([row(base, clock=60), row(base + 12, clock=48), row(base + 25, clock=35)])
        if index == 4:
            rows.append(pip_row(base + 27, [3, 2]))
        if index == 5:
            rows.append(pip_row(base, [0, 0]))
    out = track(sorted(rows, key=lambda r: r["timestamp"]), rounds_per_game=3)
    game = out["games"][0]
    assert game["status"] == "complete" and game["winner"] == "p1"
    assert game["basis"] == "win_dots_reached_target"


# ---------- the four claims about where a game stops ----------

def test_an_unwatched_conclusion_keeps_its_live_bound_confirmation_and_clip_apart():
    """The 6,016.010 game in miniature: the clock stops, the pip lands two seconds later, the menu is 25 s on.

    Four numbers, four claims. `end` is the estimate, `last_live_at` the evidence under it,
    `winner_confirmed_at` the read naming the side (which is legitimately *after* the end), and the clip
    has to contain that read without following the camera into the dwell.
    """
    rows = [row(1000, clock=60), row(1010, clock=50), row(1020, clock=40), row(1028, clock=32),
            pip_row(1030, [2, 3], confidence=(0.9, 0.55)),
            row(1033, res=True, state="post_match_menu"),
            row(1055, clock=60), row(1067, clock=48), row(1078, clock=37)]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    assert game["end"] == game["conclusion_event_at"] == game["last_live_at"] == 1028.0
    assert game["conclusion_evidence"] == "last_readable_clock"
    assert game["winner"] == "p2"
    assert game["winner_confirmed_at"] == 1030.0 and game["winner_confirmation_lag_seconds"] == 2.0
    assert game["end_status"] == "bounded" and game["provisional_end"] is True
    band = game["end_uncertainty"]
    assert band["low"] == 1028.0 and band["high"] == 1030.0 and band["bounded_by"] == "winner_confirmation"
    # The dwell is a fact about how long the broadcast held a menu, never a bound on the end.
    assert band["next_opening_at"] == 1055.0 and band["tape_after_conclusion_seconds"] == 27.0
    # The clip reaches the read naming the winner, padded, and stops there.
    assert game["clip_end_at"] == 1030.0 + bg.CLIP_PAD_SECONDS < 1055.0
    assert game["footage_after_conclusion_seconds"] == 2.0
    assert game["winner_confirmation_readings"] == [{"at": 1030.0, "value": 3, "confidence": 0.55}]


def test_a_watched_conclusion_is_never_bound_from_below_by_the_footage_after_it():
    """Play labelled gameplay after an observed K.O. is the victory camera, not a later conclusion."""
    rows = [row(500, clock=60), row(520, clock=20, end="ko"), row(560, clock=20), row(619, clock=20),
            row(640, state="character_select")]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    assert game["end_status"] == "observed" and game["end"] == 520.0
    assert game["last_live_at"] == 520.0, "the lower bound drifted above an observed conclusion"
    assert game["last_observed_play_at"] == 619.0
    assert game["footage_after_conclusion_seconds"] == 99.0
    assert "victory camera" in game["footage_after_conclusion_kind"]


def test_a_short_dwell_caps_the_clip_at_the_next_round_rather_than_padding_past_it():
    """Padding exists to keep the decisive row inside the clip, not to eat the round that followed."""
    rows = [row(100, clock=60), row(106, clock=54), row(108, clock=52), pip_row(110, [2, 3]),
            row(111, clock=60), row(118, clock=53), row(125, clock=46)]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    # Confirmation at 110 plus 2 s of padding would run to 112; the next round opened at 111, so the
    # clip stops at the round that followed instead of eating a second of it.
    assert game["winner_confirmed_at"] == 110.0
    assert game["clip_end_at"] == 111.0, "the clip ran past the opening of the next round"


def test_a_game_with_no_winner_claims_no_confirmation_reading():
    rows = [row(100, clock=60), row(108, clock=52), row(110, clock=50),
            row(112, state="post_match_menu", res=True)]
    out = track(rows, rounds_per_game=3)
    game = out["games"][0]
    # A menu proves the game ended and nothing about who took it, so there is no confirmation to reach.
    assert game["winner"] is None and game["winner_confirmation_readings"] == []
    assert game["winner_confirmed_at"] is None and game["end"] == 110.0
    assert game["clip_end_at"] == 110.0 + bg.CLIP_PAD_SECONDS


# ---------- a set's clip, which is the number an export actually cuts on ----------

def _set_clip_fixture(next_open=1055.0):
    """Last live 1028, pip confirmation 1030, configured padding 2: the clip must reach 1032.

    The set used to anchor on the conclusion estimate and pad from there, which cut the exported clip two
    seconds short of the read that names the winner -- the one number the whole investigation bought.
    """
    return [row(1000, clock=60), row(1010, clock=50), row(1020, clock=40), row(1028, clock=32),
            pip_row(1030, [2, 3], confidence=(0.9, 0.55)),
            row(1033, res=True, state="post_match_menu"),
            row(next_open, clock=60), row(next_open + 12, clock=48), row(next_open + 23, clock=37)]


def test_a_set_clip_reaches_the_confirmation_its_last_game_reached():
    out = track(_set_clip_fixture(), rounds_per_game=3, games_per_set=1)
    game, candidate = out["games"][0], out["sets"][0]
    assert game["clip_end_at"] == candidate["clip_end_at"] == 1032.0, (
        game["clip_end_at"], candidate["clip_end_at"])
    anchor = candidate["clip_end_anchor"]
    assert (anchor["conclusion_event_at"], anchor["winner_confirmed_at"], anchor["set_end_at"]) == (
        1028.0, 1030.0, 1028.0), "the set stopped reporting which claim its cut rests on"
    # The basis has to name the policy, not just a number: what the clip is anchored on, that the
    # configured padding was applied once, and that no marking extends it.
    basis = candidate["clip_end_basis"]
    assert "conclusion plus winner confirmation" in basis and "configured padding" in basis
    assert "applied once at the game layer" in basis and "never re-pads" in basis
    assert candidate["clip_uncertainty"]["high"] == 1030.0


def test_a_set_clip_never_pads_a_bound_the_game_already_padded():
    """Double padding would put the cut past the confirmation by another configured 2 s."""
    out = track(_set_clip_fixture(), rounds_per_game=3, games_per_set=1)
    candidate = out["sets"][0]
    assert candidate["clip_end_at"] != 1032.0 + bg.CLIP_PAD_SECONDS
    assert candidate["clip_end_at"] == candidate["clip_end_anchor"]["game_clip_end_at"]


def test_a_set_clip_stops_at_the_next_opening_rather_than_at_its_own_padding():
    """Confirmation at 1030 plus 2 s of padding would run to 1032; the next round opened at 1031.

    The cap is the game layer's, and the set inherits the capped number rather than re-padding past it
    into the round that followed.
    """
    out = track(_set_clip_fixture(next_open=1031.0), rounds_per_game=3, games_per_set=1)
    game, candidate = out["games"][0], out["sets"][0]
    assert game["clip_end_at"] == 1031.0 and candidate["clip_end_at"] == 1031.0
    assert candidate["clip_end_anchor"]["game_clip_end_at"] == 1031.0


def test_a_set_clip_is_not_stretched_across_a_long_dwell():
    """A menu held for minutes is a fact about the broadcast, not room to extend the cut."""
    out = track(_set_clip_fixture(next_open=1000.0 + 400.0), rounds_per_game=3, games_per_set=1)
    candidate = out["sets"][0]
    assert candidate["clip_end_at"] == 1032.0
    # The dwell is next opening minus the conclusion estimate: 1,400 - 1,028, not the 400 s of padding
    # a reader might assume from how the fixture is written.
    assert candidate["clip_uncertainty"]["tape_after_conclusion_seconds"] == 372.0


# ---------- a callout and a win row that disagree about a game boundary ----------

def _six_rounds(next_call=None):
    """Six rounds, none concluding, with the fifth and the opening of the sixth reading the same 2-2.

    `next_call` puts an explicit ROUND announcement on the sixth round's opening row: the shape that made
    the round-count cap's continuity guard override a positive new-game signal it should never have
    outweighed.
    """
    rows = []
    for index in range(6):
        base = index * 40.0
        call = next_call if (index == 5 and next_call) else (2 if index == 4 else None)
        rows.extend([row(base, clock=60, call=call), row(base + 12, clock=48), row(base + 25, clock=35)])
        if index == 4:
            rows.append(pip_row(base + 27, [2, 2]))
        if index == 5:
            rows.append(pip_row(base + 0.5, [2, 2]))
            rows.append(pip_row(base + 27, [3, 2]))
    return sorted(rows, key=lambda r: r["timestamp"])


def test_an_explicit_round_one_against_a_persisted_win_row_leaves_the_boundary_contested():
    """End to end through track_games: neither reading may erase the other in silence."""
    out = track(_six_rounds(next_call=1), rounds_per_game=3)
    assert len(out["games"]) == 2, "the merge overrode the announced new game"
    first, second = out["games"]
    assert (first["rounds"], second["rounds"]) == (5, 1)
    assert first["winner"] is None and first["contested"] is True
    assert first["contested_reasons"] == ["pip_continuity_contradicts_round_1_callout"]
    conflict = first["boundary_conflicts"][0]
    assert conflict["new_game_callout"] == {"at": 200.0, "round_number_announced": 1,
                                          "conflicts_with_pip_continuity": True}
    assert conflict["readings"] == {"p1": [2, 2], "p2": [2, 2]} and "resolution" in conflict
    queue = [u for u in out["unresolved"] if u["type"] == "game_boundary_conflict"]
    assert queue and queue[0]["at"] == 200.0 and queue[0]["round_number_announced"] == 1
    assert "ROUND callout" in queue[0]["would_resolve"], "the queue must say what would settle it"


def test_the_disputed_boundary_is_carried_by_the_game_that_starts_on_it_too():
    out = track(_six_rounds(next_call=1), rounds_per_game=3)
    second = out["games"][1]
    dispute = second["start_is_disputed"]
    assert dispute["at"] == 200.0 and dispute["round_number_announced"] == 1
    assert dispute["alternative_grouping"]["as"].startswith("one game")
    assert second["contested"] is True and second["winner"] is None
    assert second["winner_if_boundary_untouched"] == "p1", "withholding is not forgetting"
    assert second["counts_toward_set"] is False


def test_a_round_two_announcement_claims_no_new_game_so_the_row_still_refuses_the_cut():
    """Only ROUND 1 says a game began. ROUND 2 beside a falling row is continuity, and the guard holds."""
    out = track(_six_rounds(next_call=2), rounds_per_game=3)
    assert len(out["games"]) == 1
    game = out["games"][0]
    assert game["rounds"] == 6 and game["winner"] == "p1" and game["contested"] is False
    assert game["boundary_conflicts"] == []
    assert game["round_count_beyond_target"][0]["new_game_callout"]["round_number_announced"] == 2


def test_no_callout_at_all_keeps_the_refusal_that_repaired_bb9():
    out = track(_six_rounds(), rounds_per_game=3)
    assert len(out["games"]) == 1 and out["games"][0]["winner"] == "p1"
    assert out["games"][0]["round_count_beyond_target"][0]["new_game_callout"] is None


# ---------- the correction that started this, pinned against the real recording ----------

BB9_TIMELINE = Path("build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json")
BB9_MANIFEST = Path("build/work/bb9/detect-1440p-v1/frames/sample-manifest.json")
requires_bb9 = pytest.mark.skipif(not (BB9_TIMELINE.is_file() and BB9_MANIFEST.is_file()),
                                  reason="saved BB9 evidence is not in this checkout")


@requires_bb9
def test_the_repaired_timeline_keeps_the_bb9_false_split_out_of_canonical_output():
    """122 rounds, 29 games, and nothing starting at 6,161.155 -- re-measured, not quoted.

    The evidence base is named, not discovered: this asserts what the repaired timeline yields over the
    corpus it was measured against. Under discovery the same line re-scores itself against whichever
    batches have run since, and the winner count becomes a fact about the directory instead of the repair.
    """
    observations = be.evidence(None, runs=frozen.crop_runs(),
                               facts=str(BB9_TIMELINE))["observations"]
    source = bg.source_bounds(manifest=str(BB9_MANIFEST), duration=6928.421136)
    out = bg.track_games(observations, games_per_set=None, source=source)
    assert len(out["rounds"]) == 122 and len(out["games"]) == 29
    # 16 winners until game-final-pips-v1 named three more from observed conclusion anchors, and
    # unknown-winners-batch-v1 named three beyond that the same evening: 16 -> 19 -> 22 of 29. Each gain
    # came from reading the win row on a frame the earlier grid had never offered, and none of the six
    # reads in the second batch disagreed with the evaluator's inspected label.
    assert sum(1 for g in out["games"] if g.get("winner")) == 22
    credited = {(round(g["start"], 3), g["winner"]) for g in out["games"]
                if any(abs(g["start"] - x) < 0.01 for x in (6486.480, 6601.595, 6726.720))}
    assert credited == {(6486.48, "p1"), (6601.595, "p1"), (6726.72, "p1")}, credited
    assert not [g for g in out["games"] if abs(g["start"] - 6161.154676) < 0.01], \
        "the fabricated game boundary is back in canonical output"
    game = [g for g in out["games"] if abs(g["start"] - 6016.009684) < 0.01][0]
    assert (game["rounds"], game["status"], game["winner"], game["contested"]) == (
        5, "complete", "p2", False)
    assert game["round_count_beyond_target"] == [] and game["boundary_conflicts"] == []
    # The clip policy on real footage: the cut reaches the pip that named the side, padded once.
    assert game["clip_end_at"] == round(6218.211673 + bg.CLIP_PAD_SECONDS, 6)
    assert game["last_live_at"] == 6216.209673 <= game["end"]


# ---------- the set queue: a concluded set is not a settled set ----------

def _three_concluded_games(last_winnerless=True, tail_play=False):
    """Three games that each conclude on a result transition into the between-game screen.

    Games one and two reach three on the left. The third either does the same or concludes with the row at
    2-1, which is the difference between a candidate that owes nothing and one that owes a game result --
    and the latter is the case that used to disappear from the queue once its footage read as complete.
    """
    rows = []
    for game in range(3):
        base = game * 120.0
        rows.extend([row(base, clock=60), row(base + 20, clock=40), row(base + 40, clock=20),
                     row(base + 42, clock=18, end="ko")])
        rows.append(pip_row(base + 41, [2, 1] if (game == 2 and last_winnerless) else [3, game]))
        rows.append(row(base + 60, res=True, state="post_match_menu"))
    if tail_play:
        rows.extend([row(400, clock=60), row(420, clock=40), row(440, clock=20),
                     row(460, state="controller_assignment")])
    return sorted(rows, key=lambda r: r["timestamp"])


def _entry_for(out, start):
    return next((u for u in out["unresolved"] if u["type"] == "set_unresolved"
                 and abs(u["at"] - start) < 1.0), None)


def _claims(entry):
    return {c["claim"]: c for c in (entry or {}).get("unresolved_claims", [])}


def test_a_candidate_that_owes_a_game_result_reports_it_as_a_purchasable_claim():
    out = track(_three_concluded_games(), rounds_per_game=3, games_per_set=None)
    entry = _entry_for(out, out["sets"][0]["start"])
    by_name = _claims(entry)
    assert by_name, "the candidate left the queue with an unwon game inside it"
    assert by_name["game_results"]["route"] == "pip_crops"
    assert by_name["game_results"]["costs_inference"] is True
    assert "game_results" in entry["needs_inference"]


def test_an_unsupplied_format_asks_the_user_and_not_the_model():
    out = track(_three_concluded_games(last_winnerless=False), rounds_per_game=3, games_per_set=None)
    entry = _entry_for(out, out["sets"][0]["start"])
    by_name = _claims(entry)
    # This synthetic span never showed a handover or a stage name, so continuity is owed too -- but the
    # claim under test is the format one, and it must be charged to the user, never to the model.
    assert {"format", "set_continuity"} == set(by_name), by_name
    assert by_name["format"]["costs_inference"] is False
    assert by_name["format"]["route"] == "user_policy"
    assert "no frame" in by_name["format"]["would_resolve"]
    assert "format" not in entry["needs_inference"] and "format" in entry["needs_from_user"]
    # A missing format is not a missing completion. Calling it completion would send the pipeline looking
    # for frames that cannot contain the answer, on every run, for ever.
    assert "completion" not in by_name


def test_the_claims_of_a_record_are_exposed_on_the_record_too():
    out = track(_three_concluded_games(), rounds_per_game=3, games_per_set=None)
    assert out["sets"]
    for record in out["sets"]:
        assert "unresolved_claims" in record, record["start"]
        assert all(c["route"] in ("pip_crops", "header", "state", "user_policy")
                   for c in record["unresolved_claims"])
        assert set(c["claim"] for c in record["unresolved_claims"])


def test_a_complete_record_with_an_unwon_game_owes_a_purchasable_claim():
    """The pre-probe shape, written directly: footage complete, every game concluded, one game unwon.

    That record used to be skipped by the queue entirely, because skipping was keyed to `status` instead of
    to whether any claim was still open.
    """
    record = {"status": "complete", "games_without_winner": 1, "closed_by_target": False,
              "boundary_confirmed": True, "membership": "supported", "deciding_wins": {"p1": 2, "p2": 0},
              "game_winners": ["p1", "p1", None],
              "attribution": {"unit": "persistent_players"}, "applicable_formats": {"format_known": False}}
    claims = {c["claim"] for c in bg._set_unresolved_claims(record)}
    assert claims == {"game_results", "format"}, claims
    # The players are mapped here, so identity is not owed -- and the format claim must never cost
    # inference, or every run would re-ask a question no frame can answer.
    purchasable = {c["claim"] for c in bg._set_unresolved_claims(record) if c["costs_inference"]}
    assert purchasable == {"game_results"}, purchasable


def test_a_set_closed_by_a_target_with_named_players_owes_nothing():
    record = {"status": "complete", "games_without_winner": 0, "closed_by_target": True,
              "boundary_confirmed": True, "membership": "supported", "deciding_wins": {"p1": 3, "p2": 0},
              "game_winners": ["p1", "p1", "p1"], "attribution": {"unit": "persistent_players"},
              "applicable_formats": {"format_known": True}}
    assert bg._set_unresolved_claims(record) == []


@requires_bb9
def test_the_queue_is_no_longer_keyed_to_footage_status_on_the_real_recording():
    """All ten of BB9's candidates now report `status: complete`; before this fix none were even looked at.

    Six of the ten still owe something the model could look at (a game result, a continuity signal, an
    identity mapping); four owe only a format, which no frame of this recording can supply. The count fell
    from seven when unknown-winners-batch-v1 read the last unwon game of one candidate: a candidate whose
    games are all decided and whose pairing is already attributed needs no further look, only a target. The
    two `partial` candidates went complete when game-reset-probe-v1 answered the pip row across the break at
    5495.490: the boundary that split them was the missing footage claim, not a missing frame.
    """
    observations = be.evidence(None, facts=str(BB9_TIMELINE))["observations"]
    out = bg.track_games(observations, games_per_set=None,
                         source=bg.source_bounds(manifest=str(BB9_MANIFEST), duration=6928.421136))
    entries = [u for u in out["unresolved"] if u["type"] == "set_unresolved"]
    assert len(entries) == 10, len(entries)
    # Every candidate reports a footage status now; before the fix a "complete" one was skipped entirely.
    assert sum(1 for u in entries if u["status"] == "complete") == 10
    assert sum(1 for u in entries if u["needs_inference"]) == 6
    assert sum(1 for u in entries if not u["needs_inference"]) == 4, \
        "four candidates owe only a format, which no request can supply"


@requires_bb9
def test_identity_and_format_are_separate_claims_on_real_candidates():
    """Two BB9 candidates that won every game differ in exactly one claim.

    790.790: three games all to p1 with the same pair named throughout, so nothing about *who* is missing --
    only the format is unknown. 6,486.480: three games all to p1 as well, but the right-hand name changes
    inside the span, so the wins stay screen sides and identity is genuinely open. `set_winner is None`
    reads the two identically, which is why the fix is a claim list rather than that test.
    """
    observations = be.evidence(None, facts=str(BB9_TIMELINE))["observations"]
    out = bg.track_games(observations, games_per_set=None,
                         source=bg.source_bounds(manifest=str(BB9_MANIFEST), duration=6928.421136))
    cheap, dear = {}, {}
    for record in out["sets"]:
        entry = _entry_for(out, record["start"])
        cheap[round(record["start"], 3)] = {c["claim"] for c in entry["unresolved_claims"]}
        dear[round(record["start"], 3)] = (entry["needs_inference"], entry["needs_from_user"])
    assert cheap[790.79] == {"format"}, cheap[790.79]
    assert cheap[6486.48] == {"format", "identity"}, cheap[6486.48]
    assert dear[790.79] == ([], ["format"])
    assert dear[6486.48] == (["identity"], ["format"])
    # The difference is not cosmetic. With the optional format supplied, the mapped candidate names a set
    # winner and the unmapped one cannot, even though both won three games to nil -- so identity is what
    # stands between "3-0" and "who won the set", and reporting it as a completion question would be a lie.
    of = json.loads(Path("build/work/bb9/coverage-audit-v10/coverage-audit.json")
                    .read_text())["optional_format"]["coverage"]["candidates"]
    named = {round(c["start"], 2): c.get("set_winner") for c in of if c.get("set_winner")}
    assert named.get(1588.59) == "Buk", named
    assert 6486.48 not in named, named


@requires_bb9
def test_the_queue_fix_bought_no_new_work():
    """A disclosure fix that quietly added requests would be the same mistake wearing the opposite face.

    The probe answered three crop windows, so exactly three crop requests leave the bill, and the nine new
    queue entries must add none of their own.
    """
    v10 = json.loads(Path("build/work/bb9/coverage-audit-v10/coverage-audit.json").read_text())
    v9 = json.loads(Path("build/work/bb9/coverage-audit-v9/coverage-audit.json").read_text())
    kinds = v10["no_format"]["priced"]["unresolved_kinds"]
    assert (kinds["set_unresolved"], kinds["win_dots_never_read"], kinds["game_winner_unobserved"]) \
        == (10, 52, 10)
    # Same corpus, same plan, one code change between the two artifacts: the queue grew eight entries and
    # the bill did not move at all.
    assert v9["no_format"]["priced"]["requests_planned"] == \
        v10["no_format"]["priced"]["requests_planned"] == 231, (
            v9["no_format"]["priced"]["requests_planned"], v10["no_format"]["priced"]["requests_planned"])
    assert v9["no_format"]["priced"]["unresolved_kinds"]["set_unresolved"] == 2
    assert all(v9[n]["priced"]["requests_planned"] == v10[n]["priced"]["requests_planned"]
               for n in ("no_format", "optional_format"))

def test_a_claim_that_costs_nothing_is_never_also_a_purchase():
    """The anti-loop invariant: a user question and a purchasable ask are never the same claim.

    A candidate can owe both -- a game result to read and a format to be told -- and the entry has to keep
    them apart, so "8 of 10 candidates unresolved" is never misread as "8 candidates of work".
    """
    out = track(_three_concluded_games(), rounds_per_game=3, games_per_set=None)
    entries = [u for u in out["unresolved"] if u["type"] == "set_unresolved"]
    assert entries, "the fixture produced no candidate at all"
    for entry in entries:
        assert set(entry["needs_inference"]).isdisjoint(entry["needs_from_user"]), entry
        for claim in entry["unresolved_claims"]:
            assert (claim["route"] == "user_policy") is (not claim["costs_inference"]), claim
            assert claim["missing"] and claim["would_resolve"], claim
