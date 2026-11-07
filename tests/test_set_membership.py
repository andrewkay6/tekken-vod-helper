"""Set membership: combined evidence, preserved alternatives, no invented boundaries.

The defect that started this: wins accumulated across whatever games were adjacent, so one bracket
match's game and the next match's three games became a single "2-2 set" spanning 900 s that could never
close and never reported a fault. The fix must not swing into the opposite error. The two are not
symmetrical in effect: a wrong split yields two partial candidates and a named question, while a wrong
merge fabricates an entity that stops reporting its own doubt. So ambiguity is **cut in the primary
reading and preserved as an alternative**, rather than resolved by whichever default is nearer to hand.

Every threshold here is anchored to a measurement on this recording, quoted in the test that enforces it.
The nameplate facts -- 21 nameplate changes during a falling clock, 161 of 296 gameplay rows inside
Buk/Rebix's own span naming a different pairing, a band reading "LOSERS ROUND 3" at 2602.6 s -- were
established by rendering the scoreboard crops in
``build/work/bb9/set-recovery-v1/label-sheet/`` and reading them directly, which is also why the
operator's span list is treated as approximate anchors and never as identity truth.
"""
import json
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_games as bg

ROOT = Path(__file__).resolve().parents[1]
MATCHES = ROOT / "build/work/bb9/oct22.tvh.json"
TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"


def game(index, winner, start=None, end=None, counts=True, status="complete", concluded=None):
    item = dict(start=(index * 200.0 if start is None else start),
                end=(index * 200.0 + 150.0 if end is None else end),
                status=status, winner=winner, counts_toward_set=counts)
    item["conclusion_event_at"] = concluded if concluded is not None else item["end"]
    return item


def rows_for(items, cue=None, cue_at=None):
    """Built-observation rows -- what ``_group_into_sets`` consumes directly.

    ``items`` is ``(game, player1, player2, score)``, and a None name is a blank nameplate, which is what
    a close-up actually produces. A cue lands mid-dwell by default: exactly on a game's opening timestamp
    it would be read as part of that game instead of between two of them.
    """
    rows = []
    for item, left, right, score in items:
        for at in (item["start"] + 10, item["start"] + 80, item["end"] - 10):
            rows.append({"timestamp": at, "screen_state": "gameplay",
                         "overlay": {"player1": left, "player2": right, "score": list(score)}
                         if (left or right) else {}})
    if cue:
        spot = cue_at if cue_at is not None else items[0][0]["end"] + 30
        rows.append({"timestamp": spot, "screen_state": cue, "overlay": {}})
    return rows


def tally_rows(pair, games, winners=None, **kwargs):
    """Rows where the broadcast tally advances one win per game, as a real set's does."""
    winners = winners or ["p1"] * len(games)
    tally, index_of = [0, 0], {"p1": 0, "p2": 1}
    items = []
    for position, item in enumerate(games):
        items.append((item, pair[0], pair[1], list(tally)))
        if position < len(winners):
            tally[index_of[winners[position]]] += 1
    return rows_for(items, **kwargs)


def check(games, rows, games_per_set=3, **kwargs):
    """Group, then prove the cuts preserved every game and nothing overlapped."""
    sets = bg._group_into_sets(games, games_per_set, rows, **kwargs)
    assert sum(s["games"] for s in sets) == len(games), "a cut dropped games from the recovery"
    spans = [g for s in sets for g in s["game_spans"]]
    assert sorted(spans) == sorted([[g["start"], g["end"]] for g in games])
    return sets


# ---------------------------------------------------------------- no fictitious merges


def test_two_matches_are_not_one_set_even_when_the_tallies_would_fit():
    """The original defect, on the executed trial's own pairings and dwell lengths.

    Before the fix this came back as one permanently open candidate spanning both matches, with no fault
    raised -- because nothing had been broken, only fused. A set has to be claimed, not inherited from
    adjacency.
    """
    # The trial's real spans: game 0 1216.2-1371.4, then a 217.2 s dwell to game 1 at 1588.6.
    games = [game(0, "p2", start=1216.215, end=1371.370), game(1, "p1", start=1588.587, end=1800.799),
             game(2, "p2", start=1820.819, end=1961.960), game(3, "p1", start=1987.986, end=2133.131)]
    rows = (rows_for([(games[0], "derpingtoast", "PWDDA | Ariz", [0, 1])])
            + tally_rows(("Buk", "Rebix"), games[1:], winners=["p1", "p2"])
            # The controller assignment the recording actually shows, mid-dwell, not inside a game.
            + [{"timestamp": 1533.5, "screen_state": "controller_assignment", "overlay": {}}])
    sets = check(games, rows)
    assert len(sets) == 2, [s["game_spans"] for s in sets]
    assert sets[0]["games"] == 1 and sets[1]["games"] == 3
    assert sets[0]["game_wins"] == {"p1": 0, "p2": 1} and sets[1]["game_wins"] == {"p1": 2, "p2": 1}
    assert sets[0]["cut_after"]["verdict"] == "separated"
    assert "controller_assignment" in sets[0]["cut_after"]["preferred_cues"]
    assert sets[1]["membership"] == "supported"


def test_a_pairing_change_alone_never_cuts_and_never_blindly_merges():
    """Names are approximate evidence, so a change raises a question instead of deciding anything.

    21 nameplate changes on this recording happen while the round clock keeps falling, which means a
    change carries no reliable information about *when* the match changed. Neither reading is taken on
    faith: the primary splits, and the joined reading survives with the evidence that would settle it.
    """
    games = [game(0, "p1"), game(1, "p2")]
    rows = tally_rows(("Alpha", "Beta"), games[:1], winners=[]) + \
        tally_rows(("Gamma", "Delta"), games[1:], winners=[])
    sets = check(games, rows)
    assert len(sets) == 2
    cut = sets[0]["cut_after"]
    assert cut["verdict"] == "ambiguous", "a name change was treated as a boundary"
    assert [q["kind"] for q in cut["questions"]] == ["identity_mapping"]
    assert cut["questions"][0]["route"] == "header", "the question has no executor to route it to"
    alternatives = sets[1]["alternative_groupings"] or sets[0]["alternative_groupings"]
    assert alternatives, "the joined reading was discarded rather than kept as an alternative"
    assert "one set" in alternatives[0]["reading"]
    assert alternatives[0]["why_not_primary"]


def test_a_side_swap_is_a_participant_mapping_question_not_a_new_set():
    """Same names, opposite sides: the mapping moved. That says nothing about whether a set ended.

    And while the mapping is unresolved the tally must not be presented as a per-player score, because
    p1 in game 1 and p1 in game 2 may be different humans.
    """
    games = [game(0, "p1"), game(1, "p1")]
    rows = tally_rows(("Alpha", "Beta"), games[:1], winners=[]) + \
        tally_rows(("Beta", "Alpha"), games[1:], winners=[])
    sets = check(games, rows)
    cut = sets[0]["cut_after"]
    assert cut["identity"] == "swapped" and cut["verdict"] == "ambiguous"
    assert {q["kind"] for q in cut["questions"]} == {"identity_mapping"}
    assert cut["questions"][0].get("swap") is True
    mapping = bg._attribution(games, [dict(cut, verdict="joined")])
    assert mapping["safe_to_read_as_players"] is False
    assert mapping["unit"] == "screen_sides_only"
    assert "not a per-player score" in mapping["note"]
    # A candidate may only be read per-player when somebody actually named both sides of every game in
    # it. "No link contradicted one pairing" is not that: a candidate whose nameplates were never read
    # contradicts nothing, and calling its tally persistent-player wins is the vacuity this asserts
    # against.
    unnamed = bg._attribution(games, [{"verdict": "joined", "identity": "same", "questions": []}])
    assert unnamed["safe_to_read_as_players"] is False
    assert unnamed["unit"] == "screen_sides_only"
    assert unnamed["identity_support"]["overlay_named_every_game"] is False
    named = {(0.0, "Alpha", "Beta"), (200.0, "Alpha", "Beta")}
    pairings = {start: {"sides": {"p1": left, "p2": right}, "dominant": True, "share": 1.0,
                        "named_rows": 3} for start, left, right in named}
    stable = bg._attribution(games, [{"verdict": "joined", "identity": "same", "questions": []}],
                             pairings=pairings)
    assert stable["safe_to_read_as_players"] is True
    assert stable["unit"] == "persistent_players"
    assert stable["identity_support"]["overlay_named_every_game"] is True


# ---------------------------------------------------------------- cues, combined


def test_controller_prompt_is_preferred_evidence_and_still_not_sufficient():
    """Preferred, not categorically neutral -- and never on its own.

    Alone it means "worth looking"; with an independent pairing change agreeing, it separates.
    """
    games = [game(0, "p1"), game(1, "p2")]
    alone = check(games, tally_rows(("Alpha", "Beta"), games, cue="controller_prompt"))
    assert len(alone) == 2
    cut = alone[0]["cut_after"]
    assert cut["verdict"] == "ambiguous", "a preferred cue alone was allowed to settle the question"
    assert cut["preferred_cues"] == ["controller_prompt"]

    pair = [game(0, "p1"), game(1, "p2")]
    rows = (tally_rows(("Alpha", "Beta"), pair[:1], winners=[])
            + tally_rows(("Gamma", "Delta"), pair[1:], winners=[])
            + [{"timestamp": pair[0]["end"] + 30, "screen_state": "controller_prompt", "overlay": {}}])
    both = check(pair, rows)
    assert both[0]["cut_after"]["verdict"] == "separated", "a cue plus a change did not separate"


@pytest.mark.parametrize("screen", ["stage_select", "stage_intro", "vs_screen", "round_end",
                                   "gameplay"])
def test_a_screen_routine_between_games_of_one_set_cannot_be_a_boundary(screen):
    """Stages are picked between games of the same set.

    Making any of them decisive shatters every real set into single games and the report still says
    nothing is wrong.
    """
    games = [game(i, "p1") for i in range(3)]
    sets = check(games, tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1"], cue=screen))
    assert len(sets) == 1, "%s invented a set boundary" % screen
    assert sets[0]["status"] == "complete" and sets[0]["membership"] == "supported"


@pytest.mark.parametrize("screen", ["character_select", "post_match_menu", "lobby"])
def test_a_corroborating_screen_may_support_a_cut_but_never_cause_one(screen):
    """These appear between games of one set too, so they only ever add weight to other evidence."""
    games = [game(0, "p1"), game(1, "p2")]
    rows = tally_rows(("Alpha", "Beta"), games, winners=[], cue=screen)
    sets = check(games, rows)
    for candidate in sets:
        for key in ("cut_before", "cut_after"):
            link = candidate.get(key) or {}
            assert link.get("verdict") != "separated", \
                "%s alone was reported as a boundary" % screen
            assert screen in (link.get("corroborating_cues") or []) or link == {}


def test_an_unclassified_screen_is_reported_rather_than_treated_as_neutral():
    """A screen nobody has graded yet is not the same thing as a screen known to be routine."""
    games = [game(0, "p1"), game(1, "p1")]
    rows = tally_rows(("Alpha", "Beta"), games) + [
        {"timestamp": games[0]["end"] + 30, "screen_state": "bracket_recovery_screen", "overlay": {}}]
    sets = check(games, rows)
    assert "bracket_recovery_screen" in json.dumps(sets), "the unknown screen was dropped from the record"


# ---------------------------------------------------------------- dwell


def test_a_long_dwell_is_an_investigation_trigger_not_a_proven_boundary():
    """20.0 s and 26.0 s between games of Buk/Rebix's set; 217.2 s across a match edge.

    Long enough to be worth asking about, never long enough to be a fact.
    """
    near = [game(0, "p1", start=0.0, end=90.0), game(1, "p1", start=110.0, end=200.0)]
    assert len(check(near, tally_rows(("Alpha", "Beta"), near))) == 1, "20 s of menu split a set"

    gap = bg.SET_GAME_GAP_LIMIT + 30.0
    far = [game(0, "p1", start=0.0, end=90.0), game(1, "p1", start=90.0 + gap, end=180.0 + gap)]
    sets = check(far, tally_rows(("Alpha", "Beta"), far))
    assert len(sets) == 2
    cut = sets[0]["cut_after"]
    assert cut["verdict"] == "ambiguous" and cut["identity"] == "same"
    assert {q["kind"] for q in cut["questions"]} == {"dwell_content"}
    assert sets[1]["alternative_groupings"], "the dwell cut discarded the joined reading"
    assert "proving nothing" in " ".join(cut["reasons"])


# ---------------------------------------------------------------- score, thresholds, format


def test_the_broadcast_tally_never_cuts_on_its_own():
    """Trusting a score reset split one real set into three candidates; a disagreement is all it earns.

    The case that mattered kept the same dominant pairing on both sides, so nothing but the box disagreed
    -- and the box is read off an overlay measured changing mid-round. The games stay together, and the
    disagreement is recorded where a reviewer can see it rather than being spent as a boundary.
    """
    games = [game(0, "p1"), game(1, "p2")]
    rows = rows_for([(games[0], "Alpha", "Beta", [2, 0]), (games[1], "Alpha", "Beta", [0, 0])])
    sets = check(games, rows)
    assert len(sets) == 1, "a score reset alone was allowed to decide a boundary"
    link = sets[0]["links"][0]
    assert link["score_signal"] == "reset" and link["verdict"] == "joined"
    assert link["questions"] == [], "a hint was promoted into a routed question"
    assert any("tally went backwards" in r for r in link["reasons"]), \
        "the disagreement was silently dropped instead of recorded"
    # With a preferred screen agreeing, the same reset does separate: two signals, not one.
    with_cue = rows + [{"timestamp": games[0]["end"] + 30, "screen_state": "controller_prompt",
                        "overlay": {}}]
    split = check(games, with_cue)
    assert len(split) == 2 and split[0]["cut_after"]["verdict"] == "separated"


@pytest.mark.parametrize("share,dominant", [(0.6, False), (0.79, False), (0.80, True), (0.95, True),
                                           (None, False)])
def test_dominance_compares_share_with_the_threshold_instead_of_short_circuiting(share, dominant):
    """``share or 0 >= threshold`` parses as ``share or (0 >= threshold)`` and passes for any nonzero.

    Codex caught that precedence error here; this pins the comparison at the function that owns it, for
    both sides, so a 0.6 plurality can never authorise a split.
    """
    read = {"player1": "A", "player2": "B",
            "share": ({"player1": share, "player2": share} if share is not None else {})}
    assert bg._dominant(read) is dominant


def test_dominance_needs_both_sides():
    read = {"player1": "A", "player2": "B", "share": {"player1": 0.95, "player2": 0.6}}
    assert bg._dominant(read) is False


def test_no_configured_format_and_no_observed_end_closes_nothing():
    """Two ways to be short of a conclusion, and this fixture is both: no target, and no station change.

    This test used to read "no configured format closes nothing" and to assert that a missing target alone
    held every candidate open, which let the format veto a conclusion the recording had already reached. It
    stays as the narrower half: with neither the format nor an observed end, nothing is claimed. The
    complementary case -- no format, boundary observed -- is the test below it.
    """
    games = [game(i, "p1") for i in range(5)]
    sets = check(games, tally_rows(("Alpha", "Beta"), games), games_per_set=None)
    assert sets and all(s["status"] == "partial" for s in sets), "a set closed with no target and no end"
    assert all(s["games_per_set_expected"] is None for s in sets)
    assert all(s["concluded_by"] == [] for s in sets)
    assert any("never configured" in m for m in sets[0]["missing_evidence"])
    assert sets[0]["basis"] == "no_target_and_no_observed_end"
    # And with a target configured, the same games do close: the difference is the input, not the mood.
    assert any(s["status"] == "complete" for s in check(games, tally_rows(("Alpha", "Beta"), games)))


def station_change(rows, at):
    """The players putting the controllers down and someone else picking them up.

    Two rows, because that is what the recording shows: a prompt on screen while the seats are empty, then
    an assignment as the next pair plugs in (3,547.5 s and 3,658.7 s on BB9; 2,139.1 s and 2,209.2 s).
    """
    rows.append({"timestamp": at, "screen_state": "controller_prompt", "overlay": {}})
    rows.append({"timestamp": at + 1.0, "screen_state": "controller_assignment", "overlay": {}})
    return rows


def test_a_set_the_recording_bounds_on_both_sides_closes_without_any_format():
    """Format is optional, and this is what optional means: the conclusion stands on the tape.

    Three games, a station change after the last one and another before the first, and no target supplied
    by anybody. The players stopped, so the set ended; both edges are on tape, so every one of its games is
    here. What the recording does *not* supply is the score's meaning -- whether two wins was the target or
    one side conceded while behind -- so `set_winner` stays None and `games_per_set_expected` stays null.
    The version this replaces answered the whole question with "partial, no target configured", discarding
    a conclusion the footage supported because a fact about the *event* was missing.
    """
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p2", start=200.0, end=350.0),
             game(2, "p1", start=400.0, end=550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p2"])
    station_change(rows, -40.0)
    station_change(rows, 580.0)
    record = check(games, rows, games_per_set=None, analysis_window=(-1000.0, 5000.0))[0]
    assert record["status"] == "complete"
    assert record["basis"] == "set_ended_on_the_recording_without_a_format_supported"
    assert record["concluded_by"] == ["station_cleared"]
    assert record["boundary_confirmed"] is True and record["footage_complete"] is True
    assert record["games_per_set_expected"] is None and record["complete_under"] == []
    assert record["set_winner"] is None, "a boundary is not a winner"
    assert record["game_wins"] == {"p1": 2, "p2": 1}
    assert any("no format was supplied" in m for m in record["missing_evidence"])


def test_a_station_clear_is_an_observed_end_a_proposed_cut_and_not_a_confirmed_completion():
    """Three claims, one station clear.

    The tape shows the players stopping (an observed end). The pipeline decides to cut a set there (a
    proposed cut). Neither says a set *completed* -- that needs a tally reaching a target, and none was
    supplied. Before 2026-11-02 the recovery record published one word, ``complete``, across all three,
    and a report built from it announced a concluded set while the structure evaluation beside it kept
    every grouping of the same games open.
    """
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p2", start=200.0, end=350.0),
             game(2, "p1", start=400.0, end=550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p2"])
    station_change(rows, -40.0)
    station_change(rows, 580.0)
    record = check(games, rows, games_per_set=None, analysis_window=(-1000.0, 5000.0))[0]
    completion = record["set_completion"]
    assert record["end_transition_observed"] is True, "the recording did show the players stop"
    assert record["cut_proposed_by_pipeline"] is True, "and the pipeline did draw a boundary there"
    assert completion["state"] == "proposed_cut_only", \
        "a station clear was allowed to speak for a completed set"
    assert completion["target_supplied"] is False and completion["target_reached"] is False
    assert "not a confirmed completion" in completion["why"]
    # The valid combined evidence survives on its own terms: the footage claim is still complete, and the
    # score is the one the games actually produced.
    assert record["status"] == "complete" and record["game_wins"] == {"p1": 2, "p2": 1}
    assert record["set_winner"] is None, "a completion nobody confirmed names no winner"


def test_nothing_observed_at_the_edge_leaves_the_completion_unresolved_not_provisional():
    """With no observed end there is not even a proposed cut, and the record must not imply one."""
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p2", start=200.0, end=350.0),
             game(2, "p1", start=400.0, end=550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p2"])
    record = check(games, rows, games_per_set=None, analysis_window=(-1000.0, 5000.0))[0]
    assert record["end_transition_observed"] is False
    assert record["cut_proposed_by_pipeline"] is False
    assert record["set_completion"]["state"] == "unresolved"


def test_an_unobserved_trailing_edge_says_which_kind_of_unknown_it_is():
    """Play continuing past a bound is evidence against an end; nothing sampled is merely no evidence.

    These used to be the same line. They are not: one is a contradiction the recording states, the other is
    the caller having stopped watching, and a report that cannot tell them apart invites a reader to promote
    a window edge into a finished set -- or to go and re-watch footage that was never the problem.
    """
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p2", start=200.0, end=350.0)]
    continuing = tally_rows(("Alpha", "Beta"), games, winners=["p1"])
    # Two frames whose clock falls: a round being fought past the bound, which is what contradicts a
    # boundary. A single gameplay row, or one whose clock cannot be read, would say neither thing, and the
    # state below has to be able to tell those apart.
    continuing += [{"timestamp": 380.0, "screen_state": "gameplay", "round_clock": 30,
                    "overlay": {"player1": "Alpha", "player2": "Beta", "score": [1, 0]}},
                   {"timestamp": 385.0, "screen_state": "gameplay", "round_clock": 25,
                    "overlay": {"player1": "Alpha", "player2": "Beta", "score": [1, 0]}}]
    record = check(games, continuing, games_per_set=None, analysis_window=(0.0, 400.0))[0]
    assert record["status"] == "partial"
    assert record["edges"]["end"]["state"] == "play_continues_past_this_bound"
    assert any("still running past" in m for m in record["missing_evidence"])

    stops = tally_rows(("Alpha", "Beta"), games, winners=["p1"])
    blind = check(games, stops, games_per_set=None, analysis_window=(0.0, 355.0))[0]
    assert blind["status"] == "partial"
    assert blind["edges"]["end"]["state"] == "nothing_observed_after"
    assert blind["edges"]["end"]["beyond_analysis_window"] is True
    # This used to read "the recording (or the caller's window) stops there", offering the reader a choice
    # between two remedies the evidence has not decided between. With no source bounds supplied the honest
    # answer is that all three possibilities stay open.
    tail = [m for m in blind["missing_evidence"] if "after the last game" in m]
    assert tail and blind["edges"]["end"]["limit"] == "source_bounds_unknown", blind["edges"]["end"]
    assert "unknown whether the recording ended, the query ended" in tail[0], tail
    assert "the recording (or the caller's window) stops" not in " ".join(tail)

    seen = check(games, stops, games_per_set=None, analysis_window=(0.0, 355.0), source={
        "proven": True, "recording_start": 0.0, "recording_end": 4000.0, "cache_start": 0.0,
        "cache_end": 3600.0, "cache_interval": 1.0, "cache_frames": 3600})[0]
    assert seen["edges"]["end"]["limit"] == "analysis_window", seen["edges"]["end"]
    assert any("widening the window reuses rows already on disk" in m
               for m in seen["missing_evidence"]), seen["missing_evidence"]


def test_a_long_pause_and_a_quiet_screen_conclude_nothing_on_their_own():
    """Absence of a station cue is not a boundary either way, and neither is dwell by itself.

    A pause the model could not classify, inside one set, with a station change at both ends of it: the
    pause joins the games (it is not evidence that anything ended) and contributes nothing to the closure,
    which arrives from the cues alone. Flip the cues off and the same footage concludes nothing -- that is
    the pair the review asked for: a preferred cue by itself is not a rule, and neither is its absence.
    """
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p2", start=230.0, end=380.0)]
    quiet = tally_rows(("Alpha", "Beta"), games, winners=["p1"])
    quiet += [{"timestamp": 155.0 + step, "screen_state": "unknown", "overlay": {}}
              for step in range(0, 70, 10)]

    bounded = [dict(r) for r in quiet]
    station_change(bounded, -40.0)
    station_change(bounded, 400.0)
    both = check(games, bounded, games_per_set=None, analysis_window=(-1000.0, 5000.0))
    assert len(both) == 1, "the unclassified pause was read as a set boundary"
    assert both[0]["status"] == "complete" and both[0]["concluded_by"] == ["station_cleared"]
    assert both[0]["basis"] == "set_ended_on_the_recording_without_a_format_supported"

    unbounded = check(games, [dict(r) for r in quiet], games_per_set=None,
                      analysis_window=(-1000.0, 5000.0))
    assert len(unbounded) == 1, "the pause split the games without any cue to justify it"
    assert unbounded[0]["status"] == "partial"
    assert unbounded[0]["concluded_by"] == []
    assert unbounded[0]["basis"] == "no_target_and_no_observed_end"


def test_a_boundary_alone_recovers_nothing_when_no_game_has_a_winner():
    """The floor under the new route: a bounded stretch of undecided games is not a completed set.

    This is the BB9 shape that used to be the whole finding -- games closing with no winner named on any of
    them. Station changes at both edges make the stretch a real match; they do not make it a recovery, and
    calling it one would turn missing evidence about *who* into a claim about *what happened*.
    """
    games = [game(0, None, start=0.0, end=150.0, status="incomplete", counts=False),
             game(1, None, start=200.0, end=350.0, status="incomplete", counts=False)]
    rows = rows_for([(games[0], "Alpha", "Beta", [0, 0]), (games[1], "Alpha", "Beta", [0, 0])])
    station_change(rows, -40.0)
    station_change(rows, 380.0)
    record = check(games, rows, games_per_set=None, analysis_window=(-1000.0, 5000.0))[0]
    assert record["boundary_confirmed"] is True, "the fixture stopped exercising the boundary route"
    assert record["status"] == "partial"
    assert record["game_wins"] == {"p1": 0, "p2": 0}
    assert record["set_winner"] is None


def test_a_target_reached_while_play_continues_reports_the_tension_it_created():
    """Where the two routes disagree, publish the disagreement instead of resolving it by precedence.

    The format says the set ended at two wins; the tape says the same two players were still playing
    afterwards. Both can be true only if the target is wrong for this event -- which is a thing a reader
    has to be told, not a thing the code settles by preferring its favourite evidence.
    """
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p1", start=200.0, end=350.0),
             game(2, "p1", start=400.0, end=550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1"])
    # Live continuation, placed *at* the bound rather than somewhere later in the interval: the walk stops at
    # the next game's opening, and a cue or a clock beyond that belongs to another boundary. Two frames whose
    # clock falls say a round was being fought when this candidate supposedly ended, so the target closes it
    # and the tape contradicts -- which is the disagreement this test is about.
    rows += [{"timestamp": 355.0, "screen_state": "gameplay", "round_clock": 20,
              "overlay": {"player1": "Alpha", "player2": "Beta", "score": [2, 0]}},
             {"timestamp": 360.0, "screen_state": "gameplay", "round_clock": 15,
              "overlay": {"player1": "Alpha", "player2": "Beta", "score": [2, 0]}}]
    first = check(games, rows, games_per_set=2, analysis_window=(-1000.0, 5000.0))[0]
    assert first["status"] == "complete" and first["concluded_by"] == ["target_reached"]
    assert first["edges"]["end"]["state"] == "play_continues_past_this_bound"
    assert any("rests on the target alone" in m for m in first["missing_evidence"])


# ---------------------------------------------------------------- edge locality


def frame(at, clock=None, state="gameplay", pair=("Alpha", "Beta"), score=(1, 0)):
    """One classified frame, in the shape the real timeline writes: a `state`, plus the clock.

    `build_observations` normalises rows keyed on ``state`` and emits ``screen_state``, so the fixtures that
    go through it have to arrive the same way the tape does -- otherwise every cue here would be read as
    ``unknown`` and the walk would find nothing to settle.
    """
    return {"timestamp": at, "state": state, "round_clock": clock,
            "overlay": ({"player1": pair[0], "player2": pair[1], "score": list(score)}
                        if state == "gameplay" else {})}


LOCAL_WINDOW = (-120.0, 400.0)


def edge_of(rows, games=None, **kwargs):
    games = games or [game(0, "p1", start=10.0, end=100.0)]
    return bg._match_edges(games, bg.build_observations(rows), analysis_window=LOCAL_WINDOW, **kwargs)


def test_a_cue_behind_intervening_live_play_cannot_conclude_an_earlier_boundary():
    """The negative control, exactly as reported: play resumed, so the later cue is not this one's.

    The candidate ends at 100 s. A round is being fought at 110-120 s -- its clock falls from 60 to 50, the
    only kind of evidence that says a fight is in progress -- and the players put the controllers down at
    180 s. That prompt belongs to the transition *after* the intervening round, not to the bound at 100 s,
    and a scan over a fixed window in either direction cannot tell the two apart.
    """
    edges = edge_of([frame(0.0, state="controller_prompt"), frame(110.0, clock=60),
                     frame(120.0, clock=50), frame(180.0, state="controller_prompt")])
    assert edges["concluded"] is False and edges["footage_complete"] is False
    end = edges["end"]
    assert end["state"] == "play_continues_past_this_bound"
    assert end["live_play_at"] == 120.0 and end["station_cue_at"] is None
    assert end["settled_by"]["kind"] == "live"
    assert any("belongs to a later transition" in text for text in end["evidence_against"])


def test_a_controller_cue_beside_the_boundary_still_concludes_it():
    """The positive the negative control must not have taken away.

    Same shape with the intervening round removed: cue first, nothing fighting past the bound. Freezing the
    walk to avoid one overclaim would be the opposite error, so this pair pins the rule to *ordering* rather
    than to distance, and rather than to a general distrust of controller cues.
    """
    edges = edge_of([frame(95.0, clock=20), frame(100.0, state="post_match_menu"),
                     frame(104.0, state="controller_prompt"),
                     frame(120.0, state="controller_assignment")])
    assert edges["concluded"] is True
    assert edges["end"]["state"] == "station_cleared" and edges["end"]["station_cue_at"] == 104.0


def test_a_cue_beyond_the_next_candidates_opening_is_out_of_reach():
    """Adjacency, not distance, bounds the walk: the next game's transition is somebody else's finding.

    The cue sits well inside the 120 s ceiling but past the next candidate's opening, so it belongs to the
    later boundary. The record says the walk was bounded by the adjacent game rather than quietly reporting
    a shorter scan.
    """
    first, following = game(0, "p1", start=10.0, end=100.0), game(1, "p2", start=140.0, end=240.0)
    rows = bg.build_observations([frame(110.0, clock=60), frame(115.0, clock=55),
                                  frame(140.0, clock=60), frame(200.0, state="controller_prompt")])
    bounded = bg._match_edges([first], rows, analysis_window=(0.0, 400.0), next_game=following)["end"]
    assert bounded["state"] != "station_cleared"
    assert bounded["bounded_by_adjacent_game"] is True
    assert bounded["scanned"] == [100.0, 140.0]

    # Unbounded, the same cue is reached and used -- which is the defect. Stated as a pair so a future
    # change to either side of the rule has to break one of these two, not both.
    unbounded = bg._match_edges([first], rows, analysis_window=(0.0, 400.0))["end"]
    assert unbounded["bounded_by_adjacent_game"] is False


def test_a_frozen_victory_pose_is_not_observed_continuation():
    """The K.O. freeze: a clock that repeats itself is a result being drawn, not a round being fought.

    The recording holds the same digit for seconds after a round is decided, and the frame just past a game's
    end is very often exactly that. Counting it as continuation would contradict the cue that follows and
    lose every real closure, so a repeated clock -- and a lone frame with nothing beside it to change from --
    are walked past and named, never allowed to settle the bound either way.
    """
    frozen = edge_of([frame(95.0, clock=35), frame(101.0, clock=35), frame(103.0, clock=35),
                      frame(106.0, state="controller_prompt")])
    assert frozen["end"]["state"] == "station_cleared"
    assert frozen["end"]["live_play_at"] is None
    assert [w["clock"] for w in frozen["end"]["walked_past"]] == [35, 35]

    lone = edge_of([frame(102.0, clock=41), frame(106.0, state="controller_prompt")])
    assert lone["end"]["state"] == "station_cleared"
    assert "nothing beside it to change from" in lone["end"]["walked_past"][0]["because"]


def test_an_unreadable_clock_neither_settles_an_edge_nor_blocks_a_cue_behind_it():
    """Silence is silent in both directions, and that is a rule with two halves.

    A gameplay frame whose digits cannot be read proves nothing: it may be a round being fought or a result
    animation with the clock obscured, and the difference is exactly what an edge decision must not guess.
    So it cannot contradict a boundary -- and, for the same reason, it cannot create one either. Only a
    station cue or an observed clock change settles an edge; anything else is recorded as walked past, so an
    unresolved bound shows what was seen and why none of it was enough.
    """
    silent = edge_of([frame(102.0, clock=None), frame(104.0, clock=None)])["end"]
    assert silent["state"] == "sampled_but_unsettled"
    assert silent["observed"] is True and silent["settled_by"] is None
    assert silent["live_play_at"] is None and silent["station_cue_at"] is None
    assert any("none could settle" in text for text in silent["evidence_against"])

    # And the unreadable frames do not veto a cue that follows them: an obscured clock is not a round.
    with_cue = edge_of([frame(102.0, clock=None), frame(104.0, clock=None),
                        frame(110.0, state="controller_prompt")])["end"]
    assert with_cue["state"] == "station_cleared"
    assert [w["because"] for w in with_cue["walked_past"]] == ["clock unread", "clock unread"]


def test_a_recording_edge_and_an_analysis_edge_are_told_apart():
    """Truncation is not a conclusion: the caller stopping early says nothing about the broadcast.

    The same final candidate is unresolved either way, and the difference is which question to ask next --
    watch more of this tape, or widen the window. Collapsing them would let a `--region` bound read as
    evidence that a set ended there, which is the same mistake as a borrowed cue with the signs flipped.
    """
    rows = bg.build_observations([frame(10.0, clock=60), frame(60.0, clock=20)])
    games = [game(0, "p1", start=10.0, end=100.0)]
    truncated = bg._match_edges(games, rows, analysis_window=(0.0, 101.0))["end"]
    assert truncated["state"] == "nothing_observed_after"
    assert truncated["beyond_analysis_window"] is True
    assert any("analysis window" in text for text in truncated["evidence_against"])

    stopped = bg._match_edges(games, rows, analysis_window=(0.0, 1000.0))["end"]
    assert stopped["state"] == "nothing_observed_after"
    assert stopped["beyond_analysis_window"] is False
    assert stopped["unresolved_at"] is True


def test_no_preferred_cue_anywhere_leaves_the_candidate_open_without_inventing_a_bound():
    """Absence of a station cue is neither a boundary nor a refutation.

    Nothing was sampled past the bound, so the games stay one candidate and the candidate stays open.
    Removing the veto must not have installed its mirror: a cue is what concludes, and no cue is silence.
    """
    games = [game(0, "p1", start=10.0, end=100.0), game(1, "p1", start=130.0, end=230.0)]
    rows = bg.build_observations([frame(20.0, clock=50), frame(90.0, clock=20),
                                  frame(140.0, clock=50), frame(220.0, clock=20)])
    sets = bg._group_into_sets(games, None, rows, analysis_window=(0.0, 240.0))
    assert len(sets) == 1, "games were split with nothing separating them"
    record = sets[0]
    assert record["status"] == "partial" and record["concluded_by"] == []
    assert record["edges"]["end"]["state"] == "nothing_observed_after"
    assert record["set_winner"] is None and record["games_per_set_expected"] is None


def test_without_a_format_nothing_is_scored_even_when_a_set_is_concluded():
    """The narrower claim that survives the veto's removal.

    Without a format nothing is *scored*: no target is published, no winner is named, and a tally is never
    converted into a champion. That is separate from whether a set ended, which the recording can state on
    its own -- and keeping those two claims apart is this round's correction.
    """
    games = [game(0, "p1", start=0.0, end=150.0), game(1, "p2", start=200.0, end=350.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1"])
    station_change(rows, -40.0)
    station_change(rows, 380.0)
    sets = check(games, rows, games_per_set=None, analysis_window=(-1000.0, 5000.0))
    assert sets[0]["status"] == "complete", "the boundary route stopped concluding anything"
    assert all(s["games_per_set_expected"] is None for s in sets)
    assert all(s["set_winner"] is None for s in sets)
    assert all(s["applicable_formats"]["targets"] == [] for s in sets)
    assert all(not s["complete_under"] for s in sets)
    assert any("no target is claimed" in m for s in sets for m in s["missing_evidence"])


def test_membership_distinguishes_supported_from_merely_assumed():
    """Absence of contradiction is not evidence of membership, and the record may not imply that it is."""
    games = [game(0, "p1"), game(1, "p2")]
    shown = check(games, tally_rows(("Alpha", "Beta"), games, winners=["p1"]))[0]
    assert shown["membership"] == "supported"
    blank = [game(0, "p1", start=0.0, end=90.0), game(1, "p2", start=110.0, end=200.0)]
    assumed = check(blank, rows_for([(blank[0], None, None, [0, 0]), (blank[1], None, None, [0, 0])]))[0]
    assert assumed["membership"] == "assumed"
    assert any("nothing supported it" in m for m in assumed["missing_evidence"])


def test_a_won_set_stops_collecting_games():
    games = [game(i, "p1") for i in range(5)]
    sets = check(games, tally_rows(("Alpha", "Beta"), games, winners=["p1"] * 4))
    assert [s["games"] for s in sets] == [3, 2], [s["game_spans"] for s in sets]
    assert sets[0]["status"] == "complete" and sets[0]["closed_by_target"] is True
    assert sets[0]["cut_after"] is None, "a target was reported as an evidential boundary"
    assert sets[1]["status"] == "partial"


def test_an_uncountable_game_stays_visible_and_cannot_close_a_set():
    games = [game(0, "p1"), game(1, None, counts=False, status="incomplete"), game(2, "p1")]
    sets = check(games, tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1"]))
    assert len(sets) == 1 and sets[0]["games"] == 3
    assert sets[0]["game_wins"] == {"p1": 2, "p2": 0} and sets[0]["games_without_winner"] == 1
    assert sets[0]["status"] == "partial"
    assert any("name no winner" in m for m in sets[0]["missing_evidence"])


def test_clip_end_is_conclusion_evidence_plus_padding_only():
    games = [game(0, "p1", start=0.0, end=90.0, concluded=88.0),
             game(1, "p1", start=110.0, end=200.0, concluded=198.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1"])
    padded = check(games, rows, clip_pad_seconds=5.0)
    assert len(padded) == 1 and padded[0]["clip_end_at"] == pytest.approx(203.0)
    tight = check(games, rows, clip_pad_seconds=0.5)
    assert tight[0]["clip_end_at"] == pytest.approx(198.5)
    assert "configured padding" in padded[0]["clip_end_basis"]


# ---------------------------------------------------------------- the recording itself


def _recovered(bracket=None):
    sys.path.insert(0, str(ROOT / "scripts"))
    import validate_game_one as vg
    rows = json.loads(TIMELINE.read_text(encoding="utf-8"))
    merged, _ = vg.merged_rows(vg.crop_observations(vg.RUN_DIRS), (0.0, 10 ** 9))
    out = bg.track_games(bg.build_observations(merged), rounds_per_game=3, games_per_set=3,
                         bracket=bracket)
    return out["sets"], out["games"]


@pytest.mark.skipif(not TIMELINE.is_file(), reason="BB9 timeline is not present")
def test_a_bracket_that_names_nobody_in_this_recording_rewrites_nothing():
    """The guarantee behind dropping the controller screen's monopoly, measured on real footage.

    `separated` used to need a preferred cue on every branch, so "preferred" was a mandatory cue by
    another name. It is now one of two event signals, which makes the rest of the behaviour the thing that
    must not move: supplied a bracket that contains none of these pairings, the recovery has to come back
    candidate for candidate as the recording-only route reads it, with the only difference being a record
    that the bracket was present and silent.
    """
    from tekken_vod_helper import boundary_bracket as bb

    baseline, games = _recovered()
    silent = bb.normalize({"source": "startgg", "provenance": {"event": "another-event"},
                           "matches": [{"id": "x1", "players": ["Never", "Listed"]},
                                       {"id": "x2", "players": ["In", "This"]}]})
    assisted, _ = _recovered(bracket=silent)

    def shape(sets):
        return [(s["start"], s["end"], s["status"], s["membership"], s["games"]) for s in sets]

    def verdicts(sets):
        return [(l["verdict"], l["at"]) for s in sets for l in s["links"]]

    assert shape(assisted) == shape(baseline)
    assert sum(s["games"] for s in assisted) == len(games)
    assert verdicts(assisted) == verdicts(baseline)
    for candidate in assisted:
        for key in ("cut_before", "cut_after"):
            link = candidate.get(key) or {}
            # A candidate closed by the configured target is not a bracket judgement at all: its basis is
            # the target that was set before anything was watched, which is why it keeps recording_only.
            if link and link.get("verdict") != "target_reached":
                assert link["basis"] == "bracket_supplied_but_silent", \
                    "a link claimed a route the evidence did not take"
                assert (link.get("bracket") or {}).get("state") == "unknown"
    # And the recording-only route still says so in its own record, unchanged.
    for candidate in baseline:
        for key in ("cut_before", "cut_after"):
            if candidate.get(key):
                assert candidate[key]["basis"] == "recording_only"


@pytest.mark.skipif(not MATCHES.is_file(), reason="BB9 upload metadata is not present")
def test_the_operator_span_list_disagrees_with_the_nameplate_so_it_cannot_be_identity_truth():
    """Crossing an approximate anchor is a disagreement, not a defect -- and here it is provably so.

    The list puts Buk vs Rebix across 1577.8-3246.9 s. At 2602.6 s *inside* that span the nameplate reads
    ``PWDDA | Ariz`` against ``Mattiniero`` -- a pairing the list assigns elsewhere -- which was confirmed
    by rendering the band and reading it. So the list may prompt an investigation and may not be used to
    declare a boundary wrong, in either direction.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import validate_game_one as vg
    rows = json.loads(TIMELINE.read_text(encoding="utf-8"))
    merged, _ = vg.merged_rows(vg.crop_observations(vg.RUN_DIRS), (2595.0, 2615.0))
    built = bg.build_observations(merged)
    names = {(o["overlay"] or {}).get("player1") for o in built if (o.get("overlay") or {}).get("player1")}
    listed = json.loads(MATCHES.read_text(encoding="utf-8"))["matches"]
    owner = [m for m in listed if isinstance(m.get("start"), (int, float))
             and isinstance(m.get("end"), (int, float)) and m["start"] <= 2602.6 <= m["end"]]
    assert owner, "the span list no longer covers 2602.6 s, so this needs re-measuring"
    assert owner[0]["player1"] not in names, \
        "span and nameplate now agree, so re-read whether the list can stand for identity"


@pytest.mark.skipif(not TIMELINE.is_file(), reason="BB9 timeline is not present")
def test_recovered_candidates_cover_every_game_and_route_their_uncertainty():
    sets, games = _recovered()
    assert sets and games
    assert sum(s["games"] for s in sets) == len(games)
    assert all(s["status"] in ("complete", "partial") for s in sets)
    assert all(s["membership"] in ("supported", "assumed", "single", "contested") for s in sets)
    for candidate in sets:
        for key in ("cut_before", "cut_after"):
            link = candidate.get(key) or {}
            if link.get("verdict") == "ambiguous":
                assert link.get("questions") or candidate.get("alternative_groupings"), \
                    "an ambiguous cut left neither a question nor an alternative"
    routed = [q for s in sets for q in s["questions"]]
    assert all(q.get("route") for q in routed), "a question reached the report with no executor"


@pytest.mark.skipif(not TIMELINE.is_file(), reason="BB9 timeline is not present")
def test_the_two_crossings_of_operator_anchors_are_reported_as_disagreements():
    """Neither is called a defect, because the anchors are not verified and one is provably wrong.

    Asserting the old strict xfail would have hard-coded a conclusion I had not inspected: at 4631.8 s
    the frames around the "crossing" are one continuous round of a single pairing, and elsewhere the span
    list itself names the wrong pairing. So the invariant kept is the one the data supports.
    """
    sets, _ = _recovered()
    listed = json.loads(MATCHES.read_text(encoding="utf-8"))["matches"]
    edges = [m["start"] for m in listed if isinstance(m.get("start"), (int, float))]
    crossing = [s for s in sets for e in edges if s["start"] < e - 1.0 < s["end"] - 1.0]
    for candidate in crossing:
        inside = [l["verdict"] for l in candidate["links"]]
        assert "separated" not in inside, \
            "a candidate spanning an anchor contains a cut nobody honoured"
        assert candidate["status"] != "complete" or candidate["membership"] == "supported"


# ---------------------------------------------------------------- marks stay anchors


def test_marks_are_attributed_with_offsets_and_never_move_a_boundary():
    sets = [{"start": 100.0, "end": 500.0, "games": 3}, {"start": 700.0, "end": 1100.0, "games": 3}]
    marked = bg.assign_marks(sets, [{"at": 104.0, "label": "s1"}, {"at": 141.2, "label": "s1 again"},
                                    {"at": 702.5, "label": "s2"}])
    assert marked[0]["marks"]["count"] == 2
    assert marked[0]["marks"]["offsets_from_start"] == [4.0, 41.2]
    assert marked[0]["marks"]["spacing_seconds"] == [37.2], \
        "spacing is what separates a duplicate mark from an uncounted set, so it must be reported"
    assert [(s["start"], s["end"]) for s in marked[:2]] == [(100.0, 500.0), (700.0, 1100.0)]


def test_a_mark_outside_every_candidate_reports_both_neighbours():
    sets = [{"start": 100.0, "end": 500.0, "games": 3}, {"start": 520.0, "end": 900.0, "games": 3}]
    marked = bg.assign_marks(sets, [480.0, 505.0, 2400.0])
    assert marked[0]["marks"]["at"] == [480.0]
    assert marked[1]["marks"]["at"] == [], "a mark outside a candidate was attributed anyway"
    stray = [m for m in marked if m.get("kind") == "unattributed_marks"][0]["marks"]
    between = {m["at"]: m for m in stray}[505.0]
    assert between["previous_candidate"]["seconds_after_end"] == pytest.approx(5.0)
    assert between["next_candidate"]["seconds_before_start"] == pytest.approx(15.0)
    assert {m["at"] for m in stray} == {505.0, 2400.0}
    assert {m["at"]: m for m in stray}[2400.0]["next_candidate"] is None, \
        "a mark past everything invented a next candidate"
    assert [(s["start"], s["end"]) for s in marked[:2]] == [(100.0, 500.0), (520.0, 900.0)]


def test_no_marks_at_all_changes_nothing():
    sets = [{"start": 100.0, "end": 500.0, "games": 3, "questions": [], "links": [],
             "status": "partial", "membership": "supported"}]
    marked = bg.assign_marks(sets, [])
    assert len(marked) == 1 and marked[0]["marks"]["count"] == 0
    assert marked[0]["status"] == "partial" and marked[0]["membership"] == "supported"


# ---------------------------------------------------------------- a target never closes a disclosure

def test_reaching_the_configured_target_does_not_silence_an_unwatched_start():
    """The defect the game-1 decider probe exposed on the real recording.

    With the optional BB9 policy applied, candidate 1 became ``confirmed`` and its ``missing_evidence``
    went to ``[]`` -- while the analysis window still opened with the round clock already running, so the
    set's earlier games were exactly as unaccounted for as before. A target is a fact about the *rules*;
    where the queried tape begins is a fact about the *evidence*, and the second cannot be retired by the
    first.
    """
    games = [game(0, "p1", 100.0, 150.0), game(1, "p1", 200.0, 350.0), game(2, "p1", 400.0, 550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1", "p1"])
    station_change(rows, 600.0)
    record = check(games, rows, games_per_set=3, analysis_window=(95.0, 900.0), source={
        "proven": True, "recording_start": 0.0, "recording_end": 6000.0, "cache_start": 0.0,
        "cache_end": 5999.0, "cache_interval": 1.0, "cache_frames": 6000})[0]
    assert record["game_wins"] == {"p1": 3, "p2": 0}
    assert record["set_completion"]["state"] == "confirmed", record["set_completion"]
    assert record["footage_complete"] is False
    start = [m for m in record["missing_evidence"] if "the start of this set" in m]
    assert start, "a reached target silenced the only line admitting the set began off-tape"
    assert "analysis window" in start[0] and "limit of the query" in start[0], start[0]
    assert record["edges"]["start"]["limit"] == "analysis_window"


def test_only_a_proven_source_bound_may_claim_the_recording_was_truncated():
    """Where the tape begins is a fact about the source, and rows cannot establish it.

    Three readings of one shape -- a candidate opening at 100 s with nothing analysed before it -- because
    the remedy differs completely in each case. With no source evidence the limit is unknown and says so.
    With a manifest whose source starts at 100 s it is a genuine truncation. With a cache that reaches back
    to 0 s while the query started at 95 s it is the query's own edge, and widening it costs no inference.
    """
    games = [game(0, "p1", 100.0, 150.0), game(1, "p1", 200.0, 350.0), game(2, "p1", 400.0, 550.0)]

    def record(source=None, window=None):
        rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1", "p1"])
        station_change(rows, 600.0)
        return check(games, rows, games_per_set=3, analysis_window=window, source=source)[0]

    blind = record()
    assert blind["edges"]["start"]["limit"] == "source_bounds_unknown", blind["edges"]["start"]
    assert "unknown whether the set began there" in " ".join(blind["missing_evidence"]), \
        blind["missing_evidence"]
    assert "recording itself begins" not in " ".join(blind["missing_evidence"]), \
        "the first analysed row was allowed to testify about the tape"

    truncated = record(source={"proven": True, "recording_start": 100.0, "recording_end": 900.0,
                               "cache_start": 100.0, "cache_end": 899.0, "cache_interval": 1.0,
                               "cache_frames": 800})
    assert truncated["edges"]["start"]["limit"] == "recording_head"
    assert truncated["edges"]["start"]["limit_basis"].startswith("the source starts at 100.000")
    assert any("recording itself begins" in m for m in truncated["missing_evidence"])

    widened = record(source={"proven": True, "recording_start": 0.0, "recording_end": 6000.0,
                             "cache_start": 0.0, "cache_end": 5999.0, "cache_interval": 1.0,
                             "cache_frames": 6000}, window=(95.0, 900.0))
    assert widened["edges"]["start"]["limit"] == "analysis_window"
    assert any("limit of the query" in m for m in widened["missing_evidence"])
    # The claim names the source it was measured against, so a reader can check it rather than trust it.
    assert widened["edges"]["start"]["source_bounds"]["cache_start"] == 0.0


def test_a_sparse_cache_edge_is_not_read_as_source_truncation():
    """Four shapes of "nothing past this point", because their remedies are not interchangeable."""
    whole = {"proven": True, "cache_known": True, "recording_start": 0.0, "recording_end": 6000.0,
             "cache_start": 0.0, "cache_end": 560.0, "cache_interval": 1.0, "cache_frames": 560}
    inside = bg.footage_limit(540.0, "after", source=whole)
    assert inside["limit"] == "footage_never_sampled", inside
    assert "cache holds frames" in inside["basis"]
    past_cache = bg.footage_limit(559.5, "after", source=whole)
    assert past_cache["limit"] == "not_in_cache", past_cache
    assert "unexamined rather than absent" in past_cache["basis"]
    at_the_end = bg.footage_limit(5999.4, "after", source=dict(whole, cache_end=6000.0))
    assert at_the_end["limit"] == "recording_tail", at_the_end
    assert "the source ends at 6000.000" in at_the_end["basis"]
    # And the same instant with nothing to measure it against stays a question, not a verdict.
    unknown = bg.footage_limit(559.5, "after", source=None)
    assert unknown["limit"] == "source_bounds_unknown", unknown

    games = [game(0, "p1", 100.0, 150.0), game(1, "p1", 200.0, 350.0), game(2, "p1", 400.0, 550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1", "p1"])
    trailing = check(games, rows, games_per_set=3, source=whole)[0]
    assert trailing["edges"]["end"]["limit"] == "footage_never_sampled", trailing["edges"]["end"]
    # A side reaching its target does end the set -- recovery must not require a station cue, a marking, a
    # bracket or a result screen -- so the completion claim stands and the unexamined tail is disclosed
    # beside it rather than traded against it.
    assert trailing["set_completion"]["state"] == "confirmed", trailing["set_completion"]
    tail = [m for m in trailing["missing_evidence"] if "after the last game" in m]
    assert tail and "the cache holds frames beyond it and none was read" in tail[0], tail
    assert trailing["footage_complete"] is False

    short = check(games, rows, games_per_set=5, source=whole)[0]
    assert short["set_completion"]["state"] == "unresolved", short["set_completion"]
    assert any("after the last game" in m for m in short["missing_evidence"]), short["missing_evidence"]


def test_a_partial_cache_never_becomes_a_truncated_recording():
    """The repro: a cache sampled 100-200 s of a 1000 s recording is not a tape that begins at 100 s.

    Sampling bounds describe what was put on disk. Claiming the recording starts there tells a reader no
    earlier footage exists, which is the one thing a partial sample cannot support -- and it silently
    forbids the recovery that would find out.
    """
    partial = bg.source_bounds(cache=[{"timestamp": 100.0}, {"timestamp": 200.0}], duration=1000.0)
    assert partial["cache_start"] == 100.0 and partial["cache_end"] == 200.0
    assert partial["recording_start"] is None and partial["recording_end"] == 1000.0
    head = bg.footage_limit(100.0, "before", source=partial)
    assert head["limit"] == "not_in_cache", head
    assert "not the end of the footage" in head["basis"] or "unexamined rather than absent" in head["basis"]

    legacy = bg.footage_limit(100.0, "before", source=bg.source_bounds(cache=[{"timestamp": 100.0},
                                                                             {"timestamp": 200.0}]))
    assert legacy["limit"] == "not_in_cache", legacy
    only_rows = bg.footage_limit(100.0, "before", source=None)
    assert only_rows["limit"] == "source_bounds_unknown", only_rows


def test_the_source_bounds_helper_reads_the_manifest_it_is_given(tmp_path):
    """A sampling header establishes cache extent; only explicit source metadata establishes the tape."""
    legacy = tmp_path / "legacy-manifest.json"
    legacy.write_text(json.dumps({"source": str(tmp_path / "absent.mp4"), "start": 12.5,
                                  "end": 40.0, "interval": 0.5,
                                  "frames": [{"id": 1, "timestamp": 12.5, "path": "a.jpg"},
                                             {"id": 2, "timestamp": 39.5, "path": "b.jpg"}]}))
    bounds = bg.source_bounds(manifest=str(legacy))
    assert bounds["cache_start"] == 12.5 and bounds["cache_end"] == 39.5
    assert bounds["cache_frames"] == 2 and bounds["cache_known"] is True
    assert bounds["recording_start"] is None and bounds["recording_end"] is None
    assert bounds["proven"] is False, "a sampling window is not a source bound"
    assert bounds["source_available"] is False, \
        "a manifest whose media file is gone must say so rather than imply it is readable"

    honest = tmp_path / "manifest.json"
    honest.write_text(json.dumps({"source": str(tmp_path / "absent.mp4"), "start": 12.5, "end": 40.0,
                                  "source_duration": 40.0, "interval": 0.5,
                                  "frames": [{"id": 1, "timestamp": 12.5, "path": "a.jpg"}]}))
    tape = bg.source_bounds(manifest=str(honest))
    assert tape["recording_start"] is None and tape["recording_end"] == 40.0
    assert tape["proven"] is True and tape["cache_start"] == 12.5
    # `recording_start` is the media timeline's origin, so a duration plus an origin gives the end; the
    # sampling window's own start is a different number and is never used this way.
    offset = tmp_path / "offset-manifest.json"
    offset.write_text(json.dumps({"source": str(tmp_path / "absent.mp4"), "start": 12.5, "end": 40.0,
                                 "source_duration": 40.0, "recording_start": 2.5, "interval": 0.5,
                                 "frames": [{"id": 1, "timestamp": 12.5, "path": "a.jpg"}]}))
    shifted = bg.source_bounds(manifest=str(offset))
    assert shifted["recording_start"] == 2.5 and shifted["recording_end"] == 42.5
    assert bg.source_bounds(manifest=str(tmp_path / "nothing.json"))["proven"] is False
    assert bg.source_bounds(duration=30.0)["recording_end"] == 30.0
    # boundary_media writes the file's own length alongside the sampled span.
    media = tmp_path / "media-manifest.json"
    media.write_text(json.dumps({"source": str(tmp_path / "m.mp4"), "start": 100.0, "end": 200.0,
                                 "source_duration": 1000.0, "interval": 1.0,
                                 "frames": [{"id": 1, "timestamp": 100.0, "path": "c.jpg"}]}))
    sampled = bg.source_bounds(manifest=str(media))
    assert sampled["recording_end"] == 1000.0 and sampled["cache_start"] == 100.0
    assert sampled["recording_start"] is None, "100 s is the sample's start, not the tape's"


def test_a_set_that_opened_on_tape_keeps_its_completion_claim_clean():
    """The fix must not become a blanket downgrade: an observed start discloses nothing.

    Every candidate in the accepted 1200-2200 and 3200-4000 intervals opens on a station change, and an
    unconditional leading-footage line would have smeared this round's disclosure across evidence that
    does not need it.
    """
    games = [game(0, "p1", 100.0, 150.0), game(1, "p1", 200.0, 350.0), game(2, "p1", 400.0, 550.0)]
    rows = tally_rows(("Alpha", "Beta"), games, winners=["p1", "p1", "p1"])
    station_change(rows, 40.0)
    station_change(rows, 600.0)
    record = check(games, rows, games_per_set=3)[0]
    assert record["footage_complete"] is True
    assert record["set_completion"]["state"] == "confirmed"
    assert not [m for m in record["missing_evidence"] if "the start of this set" in m], \
        record["missing_evidence"]
