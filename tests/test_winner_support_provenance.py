"""Winner provenance: how many looks stand behind a credited win, and what the plan does about one.

A winner credited from the pip row used to be a fact with no stated sample size. The 2026-11-05 probes made
the question concrete: the flash frame at 3,958.955's end and its confirmation 2.002 s later both read LEFT
3, and the compressed series -- right for "what did the score do" -- carried **one** entry for **two**
frames. So support is counted on a separate raw log, and the flag it raises is about how much looking
happened, never about whether the winner stands.

The direction matters both ways and both are tested: 2 then 3 is ordinary progression, and 3 then 2 is a
contradiction the temporal rules already own. This layer adds no equality rule and removes no winner.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be        # noqa: E402
from tekken_vod_helper import boundary_games as bg           # noqa: E402
from tekken_vod_helper import boundary_investigate as bi     # noqa: E402

TARGET = 3
CACHE_DIR = "build/work/bb9/detect-1440p-v1/frames"
BB9_TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
BB9_MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
WINDOW = (0.0, 28.0)          # stops short of the next round opening, as the planner's own fixtures do

pytestmark = pytest.mark.skipif(not BB9_TIMELINE.is_file(),
                                reason="the saved BB9 timeline is not present")


def pip_row(timestamp, frame, dots, confidence=0.9):
    """One classified row carrying a crop answer for the pip row.

    Built as the merge leaves them: a distinct frame file per look, so "how many frames" is answerable from
    the rows themselves and not inferred from how many list entries survived compression.
    """
    path = "%s/%s" % (CACHE_DIR, frame)
    return {"timestamp": timestamp, "path": path, "frame": path, "state": "gameplay", "evidence": "",
            "round_clock": 30, "confidence": confidence, "win_dots": list(dots),
            "win_dots_drawn": [True, True], "win_dots_confidence": [confidence, confidence]}


def clock(timestamp, frame, **fields):
    path = "%s/%s" % (CACHE_DIR, frame)
    row = {"timestamp": timestamp, "path": path, "frame": path, "state": "gameplay", "evidence": "",
           "confidence": 0.95}
    row.update(fields)
    return row


def game_rows(pips, base=1000.0, round_length=40.0, extras=()):
    """A game of ``len(pips)`` rounds, one pip reading per round opening, plus any ``extras``.

    ``extras`` are (offset-from-the-last-opening, frame, dots, confidence) rows: a second look at the row
    that closed the game has to sit **inside** that round, or the fixture has quietly built a second game.
    """
    rows = []
    last_opening = base + (len(pips) - 1) * round_length
    for index, value in enumerate(pips):
        opening = base + index * round_length
        rows.append(clock(opening, "open-%02d.jpg" % index, round_clock=60))
        rows.append(pip_row(opening + 1.0, "row-%02d.jpg" % index, [value, 0]))
        rows.append(clock(opening + round_length - 5.0, "tail-%02d.jpg" % index, round_clock=20))
        rows.append(clock(opening + round_length - 1.0, "late-%02d.jpg" % index, round_clock=0,
                          round_end="ko", evidence="K.O."))
    for offset, frame, dots, confidence in extras:
        rows.append(pip_row(last_opening + offset, frame, dots, confidence=confidence))
    return sorted(rows, key=lambda row: row["timestamp"])


def tracked(rows):
    return bg.track_games(bg.build_observations(rows), rounds_per_game=TARGET, games_per_set=None)


def only_game(result):
    assert len(result["games"]) == 1, [(round(float(g["start"]), 1), round(float(g["end"]), 1))
                                       for g in result["games"]]
    return result["games"][0]


# ---------------------------------------------------------------- counting looks, not entries
def test_two_frames_reading_the_same_third_pip_are_two_looks_at_one_claim():
    """The confirmation shape, as a fixture: a second frame that agrees is evidence of looking twice.

    Compression is untouched -- the row's series still ends at a single ``3`` -- because the series answers
    what the score did. Support is counted on the raw log, where both frames are still standing.
    """
    rows = game_rows([0, 1, 3], extras=[(2.0, "confirm.jpg", [3, 0], 0.95)])
    result = tracked(rows)
    game = only_game(result)
    assert (game["winner"], game["winner_basis"]) == ("p1", "win_dots")
    support = game["winner_support"]
    assert support["frame_count"] == 2, support
    assert support["seconds_apart"] == pytest.approx(1.0, abs=0.5), support
    assert support["single_reading"] is False
    assert not [item for item in result["unresolved"]
                if item["type"] == "game_winner_single_reading"], result["unresolved"]
    # The two views have to disagree, or the log is just a copy of the series: the second look changed
    # nothing about the row and is invisible there, and visible here.
    rounds = [r for r in result["rounds"] if len((r.get("win_dots_readings") or {}).get("p1") or [])
              > len((r.get("win_dots_evidence") or {}).get("p1") or [])]
    assert rounds, "the second frame must be invisible in the series and visible in the log"
    assert rounds[0]["win_dots_readings"]["p1"][-1]["changed_the_row"] is False


def test_the_same_picture_answered_twice_is_one_look_not_two():
    """Duplicate ingestion must not become corroboration by being carried a second time.

    The corpus holds the same frame under two spellings and, across a resumed run, the same answer twice.
    Identity is the dedupe key, so the claim stays single-supported and the flag still fires.
    """
    rows = game_rows([0, 1, 3])
    duplicate = pip_row(1082.0, "row-02.jpg", [3, 0])           # the frame that already read this 3
    duplicate["path"] = str(ROOT / CACHE_DIR / "row-02.jpg")    # same picture, other spelling
    duplicate["frame"] = duplicate["path"]
    rows = sorted(rows + [duplicate], key=lambda row: row["timestamp"])
    game = only_game(tracked(rows))
    support = game["winner_support"]
    assert support["frame_count"] == 1, support
    assert support["duplicate_identities_excluded"] == 1, support
    assert support["single_reading"] is True


def test_ordinary_progression_and_a_regressing_row_are_kept_apart():
    """2 then 3 credits a winner; 3 then 2 contests the game. Neither is treated as an equality failure.

    A support rule that demanded a second agreeing frame would turn every normal win into a question, and
    one that read a fall as support would launder a contradiction. The existing temporal rules judge the
    fall; this layer only counts who looked at the credited number.
    """
    progression = tracked(game_rows([0, 1, 2, 3]))
    game = only_game(progression)
    assert (game["winner"], game["contested"]) == ("p1", False)
    assert game["winner_support"]["frame_count"] == 1
    assert game["winner_support"]["deciding_at"] is not None
    items = [item for item in progression["unresolved"]
             if item["type"] == "game_winner_single_reading"]
    assert len(items) == 1 and items[0]["at"] == game["winner_support"]["deciding_at"], items

    contradictory = tracked(game_rows([0, 1, 2, 3], extras=[(2.0, "fell.jpg", [2, 0], 0.9)]))
    fall = only_game(contradictory)
    assert fall["winner"] is None and fall["contested"] is True, fall["contested_reasons"]
    assert fall.get("dot_regressions"), fall["contested_reasons"]
    assert fall["winner_support"] is None, "no winner credited, so no winner claim to size"
    assert not [item for item in contradictory["unresolved"]
                if item["type"] == "game_winner_single_reading"], contradictory["unresolved"]


def test_a_reading_the_fall_rule_sets_aside_supports_nothing():
    """Exclusion has to be real, not decorative: a set-aside count at or above the target does not count.

    A lone low reading bracketed by the same high is resolved as a misread by the temporal rules. Here it
    still sits at or above the target, so a naive "readings >= 3" count would book it as a second look and
    clear a claim that only ever had two real ones.
    """
    rows = game_rows([0, 1, 4], extras=[(1.5, "dip.jpg", [3, 0], 0.3),
                                        (2.0, "back.jpg", [4, 0], 0.9)])
    support = only_game(tracked(rows))["winner_support"]
    assert support["frame_count"] == 2, support       # the two 4s, not the dip between them
    assert support["set_aside_excluded"] == 1, support


def test_support_is_scoped_to_the_credited_game_and_not_to_the_recording():
    """A later game's identical row is another claim, and must not prop up this one."""
    first = game_rows([0, 1, 3], base=1000.0)
    second = game_rows([0, 1, 3], base=3000.0)
    result = tracked(sorted(first + second, key=lambda row: row["timestamp"]))
    assert len(result["games"]) == 2, [(g["start"], g["end"]) for g in result["games"]]
    for game in result["games"]:
        assert game["winner_support"]["frame_count"] == 1, game["winner_support"]
        assert float(game["winner_support"]["deciding_at"]) <= float(game["end"]) + 0.01, \
            game["winner_support"]


def test_a_reading_that_names_no_picture_is_still_its_own_instant():
    """Understating support by collapsing anonymous readings into one identity is its own error.

    Fixtures and older corpora can carry a pip value with no frame file. Keying them all on the empty string
    would fold distinct looks into one; they are keyed on their instant instead, and the count says how many
    of them were never identified, so "one frame" can never quietly mean "one unlabelled row".
    """
    rows = game_rows([0, 1, 3])
    anonymous = pip_row(1082.0, "untracked.jpg", [3, 0])
    anonymous.pop("path")
    anonymous.pop("frame")
    rows = sorted(rows + [anonymous], key=lambda row: row["timestamp"])
    support = only_game(tracked(rows))["winner_support"]
    assert support["frame_count"] == 2, support
    assert support["frames_unidentified"] == 1, support
    assert support["single_reading"] is False, "two looks, even if one of them is anonymous"


def test_the_support_identity_rule_matches_the_ones_the_merge_uses():
    """bg and be must agree on what "the same picture" means, or dedupe drifts from reuse silently."""
    for spelling in ("build/work/bb9/detect-1440p-v1/frames/frame-0238.jpg",
                     str(ROOT / "build/work/bb9/detect-1440p-v1/frames/frame-0238.jpg"), ""):
        assert bg._frame_identity(spelling) == be.frame_identity(spelling), spelling


# ---------------------------------------------------------------- routing the next look
def _cache(n=40, interval=1.0):
    return [{"id": "frame-%04d.jpg" % index, "path": "%s/frame-%04d.jpg" % (CACHE_DIR, index),
             "timestamp": round(index * interval, 6)} for index in range(n)]


def _round_end_rows(end_at=20.0):
    return [dict(timestamp=end_at - 3.0, screen_state="gameplay", round_clock=33),
            dict(timestamp=end_at, screen_state="gameplay", round_clock=33, round_end="ko"),
            dict(timestamp=end_at + 10.0, screen_state="gameplay", round_clock=60)]


def _single_reading_item(at=20.0, frame="frame-0020.jpg"):
    return {"type": "game_winner_single_reading", "at": at, "anchor": "game_end", "side": "p1",
            "game_start": 0.0, "value": TARGET, "frame": frame, "frames_read_at_or_above_target": 1,
            "would_resolve": "the same pip row on the next cached frame after this one"}


def _readings_for(timestamps, value=(3, 0)):
    return {"%s/frame-%04d.jpg" % (CACHE_DIR, int(t)): {"timestamp": float(t), "win_dots": list(value)}
            for t in timestamps}


def test_the_flag_routes_to_the_neighbour_the_round_end_probe_already_knows():
    """No new frame picker: the anchor is the deciding instant, the ask is +2 s and +4 s.

    That is where a pip lands after its cue -- it is the rule that priced the real confirmation frame at
    +2.002 s -- and per-instant coverage still applies, so the deciding frame already on disk is reused
    rather than asked for again.
    """
    plan = bi.pip_requests(_round_end_rows(), _cache(), WINDOW, _readings_for([20.0]), 5,
                           unresolved=[_single_reading_item()])
    asked = [(round(entry["timestamp"], 3), entry["role"], entry["asked"]) for entry in plan["requests"]]
    assert asked, "a winner nobody looked at twice has to be able to buy a second look"
    assert {entry["timestamp"] for entry in plan["requests"]} == {22.0, 24.0}, asked
    assert all(entry["images"] == 2 and entry["asked"] == ["win_dots"] for entry in plan["requests"])
    assert plan["reused"] == 1, "the deciding frame is on disk and is not asked for again"


def test_an_answered_neighbour_buys_nothing_and_says_it_reused():
    """Answered frames are reused, never re-priced -- and what is left is the interior, not nothing.

    This assertion had to change with the selector fix: answering +2 s and +4 s used to end the window's
    business, because a target-reaching reading counted as decided. Those three frames are now reused and
    the two unread samples beside the cue are what the plan still offers.
    """
    plan = bi.pip_requests(_round_end_rows(), _cache(), WINDOW, _readings_for([20.0, 22.0, 24.0]), 5,
                           unresolved=[_single_reading_item()])
    assert {Path(entry["frame"]).name for entry in plan["requests"]} == {"frame-0019.jpg",
                                                                        "frame-0021.jpg"}, \
        plan["requests"]
    assert plan["reused"] == 3, plan
    assert "already on disk" in plan["reused_because"]


def test_a_partial_allowance_defers_the_rest_by_name():
    """Shared budget, explicit deferral: an unaffordable second look is recorded, never dropped quietly."""
    plan = bi.pip_requests(_round_end_rows(), _cache(), WINDOW, {}, 1,
                           unresolved=[_single_reading_item()])
    assert plan["requests_planned"] == 1
    assert plan["deferred"], "the neighbour this budget could not fund has to be named"
    assert all(entry["route"] == "pip_crops" for entry in plan["deferred"]), plan["deferred"]


def test_the_public_plan_prices_it_and_a_resume_after_the_answer_does_not_reprice_it():
    """The path an operator approves is the path that resumes: same kind, no frame asked twice.

    A resumed plan is not necessarily *shorter* -- answering the coarse triple uncovers the interior, which
    is the whole point of the fix -- so the invariant asserted here is the one that actually protects the
    allowance: everything on disk is counted reused, and the frames still offered are new ones.
    """
    frames, rows, unresolved = _cache(), _round_end_rows(), [_single_reading_item()]

    def plan_with(answered):
        out = bi.plan_investigation(unresolved, frames, 40.0, observations=rows, window=WINDOW,
                                    max_requests=10, routes=("pip_crops",),
                                    crop_readings=_readings_for(answered))
        route = [entry for entry in out["dispatch"]["routes"] if entry["route"] == "pip_crops"][0]
        return out, route
    before, before_route = plan_with([20.0])
    assert before["pip_requests_planned"] >= 1, before["pip_requests_planned"]
    after, after_route = plan_with([20.0, 22.0, 24.0])
    assert after_route["reused"] > before_route["reused"], (before_route["reused"],
                                                            after_route["reused"])
    still_asked = {Path(entry["frame"]).name for entry in
                   bi.pip_requests(rows, frames, WINDOW, _readings_for([20.0, 22.0, 24.0]), 10,
                                   unresolved=unresolved)["requests"]}
    assert not still_asked & {"frame-0020.jpg", "frame-0022.jpg", "frame-0024.jpg"}, still_asked
    assert before["claims_resolved"] == after["claims_resolved"], "the ask changed, not the accounting"


# ---------------------------------------------------------------- the selector, through the public plan
def _freeze_window_rows():
    """A round that opened at 10 s and ended at 20 s, with the next round drawn at 30 s.

    One cached frame per second, so the coarse triple (20 / 22 / 24) leaves 21 unread ahead of the K.O. and
    16-19 unread behind it: the two directions a corroboration ask can take, with nothing special-cased.
    """
    return [dict(timestamp=10.0, screen_state="gameplay", round_clock=60),
            dict(timestamp=17.0, screen_state="gameplay", round_clock=33),
            dict(timestamp=20.0, screen_state="gameplay", round_clock=33, round_end="ko"),
            dict(timestamp=30.0, screen_state="gameplay", round_clock=60)]


def _cache_frames(n=60):
    return [{"id": "frame-%04d.jpg" % index, "path": "%s/frame-%04d.jpg" % (CACHE_DIR, index),
             "timestamp": float(index)} for index in range(n)]


def _crop_readings(values):
    return {"%s/frame-%04d.jpg" % (CACHE_DIR, int(t)):
            {"timestamp": float(t), "win_dots": list(v), "win_dots_drawn": [d is not None for d in v]}
            for t, v in values.items()}


def _claimed(side="p1"):
    return [{"type": "game_winner_single_reading", "at": 20.0, "anchor": "game_end", "side": side,
             "game_start": 10.0, "value": TARGET, "frame": "frame-0020.jpg",
             "frames_read_at_or_above_target": 1,
             "would_resolve": "the same pip row on the next cached frame after this one"}]


def test_a_single_reading_winner_buys_the_frames_beside_its_deciding_reading():
    """The gate that used to refuse this: the window *is* decided -- that is what the claim is about.

    A reading reaching the target no longer counts as a reason to stop looking. The ask goes to the cached
    frames beside the deciding instant, nearest first, inside the round that produced it.
    """
    plan = bi.pip_requests(_freeze_window_rows(), _cache_frames(), (0.0, 28.0),
                           _crop_readings({20.0: [3, 2], 22.0: [3, 2], 24.0: [3, 2]}), 5,
                           unresolved=_claimed())
    roles = {round(float(entry["timestamp"]), 3): entry["role"] for entry in plan["requests"]}
    assert roles, "a decided window with a live single-reading claim still has to be able to ask"
    assert set(roles) <= {19.0, 21.0}, roles
    assert all(role.startswith("freeze_interior_") for role in roles.values()), roles
    assert plan["claims_stopped"] == [], plan["claims_stopped"]


def test_an_answered_frame_with_no_row_closes_that_instant_and_not_the_other_side():
    """+1 s answered as "row not drawn" must not retire the frames before the K.O.

    That is how a freeze actually looks: the row is there at the cue, gone a second later, and the only
    unread samples are behind. Treating one empty answer as the end of the search would leave the claim
    unfunded and unexplained.
    """
    plan = bi.pip_requests(_freeze_window_rows(), _cache_frames(), (0.0, 28.0),
                           _crop_readings({20.0: [3, 2], 21.0: [None, None], 22.0: [None, None],
                                           24.0: [None, None]}), 5, unresolved=_claimed())
    asked = {round(float(entry["timestamp"]), 3) for entry in plan["requests"]}
    assert 19.0 in asked, "the backward interior is still buyable, so it is asked"
    assert 21.0 not in asked and plan["reused"] >= 1, (asked, plan["reused"])


def test_an_ordinary_round_still_refines_forward_only():
    """Nothing about the accepted recovery path moved: no claim, no backward look.

    The interior pass that recovered the accepted games asks the frame *after* the cue, bounded by where the
    recording's own answer says the row stopped being drawn. Widening it for every window would re-scope
    the recording on the strength of a claim kind that ordinary rounds never raise.
    """
    plan = bi.pip_requests(_freeze_window_rows(), _cache_frames(), (0.0, 28.0),
                           _crop_readings({20.0: [2, 2], 22.0: [None, None], 24.0: [None, None]}), 5,
                           unresolved=[{"type": "game_winner_unobserved", "at": 20.0,
                                        "anchor": "game_end"}])
    roles = {round(float(entry["timestamp"]), 3): entry["role"] for entry in plan["requests"]}
    assert roles == {21.0: "freeze_interior_plus_1s"}, roles
    assert plan["claims_stopped"] == []


def test_a_claim_with_nothing_left_to_buy_reports_the_bound_and_stays_claimed():
    """Exhausted evidence is a stated result, not a quiet clearing of the queue item."""
    answered = _crop_readings({16.0: [3, 2], 17.0: [3, 2], 18.0: [3, 2], 19.0: [3, 2], 20.0: [3, 2],
                               21.0: [3, 2], 22.0: [3, 2], 23.0: [3, 2], 24.0: [3, 2]})
    plan = bi.pip_requests(_freeze_window_rows(), _cache_frames(), (0.0, 28.0), answered, 5,
                           unresolved=_claimed())
    assert plan["requests"] == [], plan["requests"]
    assert len(plan["claims_stopped"]) == 1, plan["claims_stopped"]
    stopped = plan["claims_stopped"][0]
    assert stopped["type"] == "game_winner_single_reading" and stopped["at"] == 20.0
    assert stopped["side"] == "p1" and stopped["reachable_frames"]
    assert "already answered" in stopped["because"] and "claim stays open" in stopped["because"]


def test_the_corroboration_ask_shares_the_allowance_and_defers_by_name():
    frames, answered = _cache_frames(), _crop_readings({20.0: [3, 2], 22.0: [3, 2], 24.0: [3, 2]})
    plan = bi.pip_requests(_freeze_window_rows(), frames, (0.0, 28.0), answered, 1,
                           unresolved=_claimed())
    assert plan["requests_planned"] == 1, plan["requests"]
    assert plan["deferred"], "the second look the budget could not fund is named, not dropped"
    assert plan["claims_stopped"] == [], "unfunded is not the same as exhausted"
    assert all(entry["route"] == "pip_crops" for entry in plan["deferred"])


def test_the_public_plan_prices_the_interior_and_a_resume_reports_it_exhausted():
    """Approve-and-resume on the same selector: the ask appears once, then its stopping reason does."""
    frames, rows = _cache_frames(), _freeze_window_rows()
    coarse = _crop_readings({20.0: [3, 2], 22.0: [3, 2], 24.0: [3, 2]})
    first = bi.plan_investigation(_claimed(), frames, 40.0, observations=rows, window=(0.0, 28.0),
                                  max_requests=10, routes=("pip_crops",), crop_readings=coarse)
    assert first["pip_requests_planned"] >= 1, first["pip_requests_planned"]
    assert [entry for entry in first["dispatch"]["routes"]
            if entry["route"] == "pip_crops"][0]["claims_stopped"] == []
    # Resume: the interior frames are answered too, so nothing new is priced and the claim says why.
    done = dict(coarse)
    done.update(_crop_readings({19.0: [3, 2], 21.0: [3, 2], 17.0: [3, 2], 18.0: [3, 2], 23.0: [3, 2],
                                16.0: [3, 2]}))
    again = bi.plan_investigation(_claimed(), frames, 40.0, observations=rows, window=(0.0, 28.0),
                                  max_requests=10, routes=("pip_crops",), crop_readings=done)
    assert again["pip_requests_planned"] == 0, again["pip_requests_planned"]
    stopped = [entry for entry in again["dispatch"]["routes"]
               if entry["route"] == "pip_crops"][0]["claims_stopped"]
    assert len(stopped) == 1 and "claim stays open" in stopped[0]["because"], stopped
    assert again["claims_resolved"] == first["claims_resolved"], "the ask changed, not the accounting"


# ---------------------------------------------------------------- bounded continuation (2026-11-05 repair)


def _route_of(plan):
    return [entry for entry in plan["dispatch"]["routes"] if entry["route"] == "pip_crops"][0]


def test_answered_nearest_interiors_advance_to_the_frames_behind_them():
    """The reproduced defect, in one assertion: four answered neighbours may not hide the unread three.

    The review's fixture -- the deciding reading at 20 s and 19/21/22/24 answered "no row" -- used to
    price nothing and then report the bound as answered, because ``decision_frames`` capped its two
    refinement looks on the nearest frames *before* the caller filtered the answered ones out. Capping
    after skipping is the repair: the two looks go to 18 and 17, the third bounded frame is reported as
    still owed, and no claim is called exhausted while it is not.
    """
    answered = _crop_readings({20.0: [3, 2], 19.0: [None, None], 21.0: [None, None],
                               22.0: [None, None], 24.0: [None, None]})
    plan = bi.pip_requests(_freeze_window_rows(), _cache_frames(), (0.0, 28.0), answered, 5,
                           unresolved=_claimed())
    asked = {round(float(entry["timestamp"]), 3) for entry in plan["requests"]}
    assert asked == {17.0, 18.0}, asked
    assert plan["claims_stopped"] == [], plan["claims_stopped"]
    continuing = plan["claims_continuing"]
    assert len(continuing) == 1, continuing
    assert continuing[0]["stopping"] == "search_budget"
    assert continuing[0]["bounded_frames_remaining"] == 1
    assert "offered again on resume" in continuing[0]["because"], continuing[0]["because"]


def test_a_public_resume_advances_one_step_at_a_time_until_the_bound_is_answered():
    """Three resumes, three honest columns: asks, then one last ask, then exhaustion -- no fakes.

    Each step goes through ``plan_investigation`` with the answers its predecessor would have left on
    disk. A search-budget stop is not an exhaustion; the frame that remains inside the bound is offered
    again; and the claim is only called answered when the last bounded frame has been answered too.
    """
    frames, rows = _cache_frames(), _freeze_window_rows()
    base = {20.0: [3, 2], 19.0: [None, None], 21.0: [None, None], 22.0: [None, None],
            24.0: [None, None]}

    def plan_with(extra):
        values = dict(base)
        values.update(extra)
        return bi.plan_investigation(_claimed(), frames, 40.0, observations=rows, window=(0.0, 28.0),
                                     max_requests=10, routes=("pip_crops",),
                                     crop_readings=_crop_readings(values))

    first = plan_with({})
    assert {round(float(entry["timestamp"]), 3) for entry in first["pip_requests"]} == {17.0, 18.0}
    assert _route_of(first)["claims_stopped"] == []
    second = plan_with({17.0: [None, None], 18.0: [None, None]})
    assert {round(float(entry["timestamp"]), 3) for entry in second["pip_requests"]} == {16.0}
    assert _route_of(second)["claims_stopped"] == []
    assert _route_of(second)["claims_continuing"] == []
    third = plan_with({17.0: [None, None], 18.0: [None, None], 16.0: [None, None]})
    assert third["pip_requests_planned"] == 0, third["pip_requests_planned"]
    stopped = _route_of(third)["claims_stopped"]
    assert len(stopped) == 1, stopped
    assert stopped[0]["stopping"] == "evidence_exhausted" and stopped[0]["exhausted"] is True
    assert "already answered" in stopped[0]["because"] and "claim stays open" in stopped[0]["because"]
    assert third["claims_resolved"] == first["claims_resolved"], "the ask moved, the accounting did not"


def test_an_allowance_cut_mid_advance_defers_the_next_frames_by_name():
    """Per-instant deferral survives the repair: what the cap could not fund is listed, not lost."""
    answered = _crop_readings({20.0: [3, 2], 19.0: [None, None], 21.0: [None, None],
                               22.0: [None, None], 24.0: [None, None]})
    plan = bi.pip_requests(_freeze_window_rows(), _cache_frames(), (0.0, 28.0), answered, 1,
                           unresolved=_claimed())
    asked = {round(float(entry["timestamp"]), 3) for entry in plan["requests"]}
    left = {round(float(entry["timestamp"]), 3) for entry in plan["deferred"]}
    assert asked | left == {17.0, 18.0} and not (asked & left), (asked, left)
    assert all(entry["route"] == "pip_crops" and entry["frame"] for entry in plan["deferred"])
    assert plan["claims_stopped"] == [], "unfunded is not the same as exhausted"


def test_a_row_carried_validated_reading_retires_its_instant_like_a_crop_file():
    """Reuse does not care where the measurement came from, only that the row holds one.

    The 19 s and 21 s answers arrive as classified rows carrying a validated crop reading -- the shape
    the canonical merge produces for a frame read by the crop route weeks ago. ``plan_investigation``
    joins rows and crop files through ``pip_answered_frames`` before either route plans, so the advance
    skips them exactly as it skips the crop files: same instants retired, same bounded pool moved
    deeper, and neither is asked a second time.
    """
    rows = _freeze_window_rows() + [
        dict(pip_row(19.0, "frame-0019.jpg", [2, 1]), round_clock=None, fact_source="crop_readings"),
        dict(pip_row(21.0, "frame-0021.jpg", [2, 1]), round_clock=None, fact_source="crop_readings")]
    plan = bi.plan_investigation(_claimed(), _cache_frames(), 40.0, observations=rows,
                                 window=(0.0, 28.0), max_requests=10, routes=("pip_crops",),
                                 crop_readings=_crop_readings({20.0: [3, 2], 22.0: [None, None],
                                                               24.0: [None, None]}))
    asked = {round(float(entry["timestamp"]), 3) for entry in plan["pip_requests"]}
    assert asked == {17.0, 18.0}, asked
    assert _route_of(plan)["claims_stopped"] == []


def test_a_frame_bought_by_an_earlier_window_is_reported_as_scheduled_not_exhausted():
    """Overlapping windows share probes, and a stop must name where the work went.

    Two rounds end four seconds apart, so the frame at 23 s is the earlier window's corroboration ask and
    the later window's backward candidate. The later window must not claim that bound is ``answered``
    while the frame that would settle it is a request another window is making right now -- that report
    is what made a queued plan read as an exhausted one.
    """
    rows = [dict(timestamp=10.0, screen_state="gameplay", round_clock=60),
            dict(timestamp=20.0, screen_state="gameplay", round_clock=50, round_end="ko"),
            dict(timestamp=22.0, screen_state="gameplay", round_clock=60),
            dict(timestamp=24.0, screen_state="gameplay", round_clock=58, round_end="ko")]
    claims = [dict(_claimed()[0]), dict(_claimed()[0], at=24.0, frame="frame-0024.jpg",
                   game_start=22.0)]
    # The nearest looks around 20 s are answered, so window 0's two bounded looks land on 16 and 23 --
    # and 23 is the only frame left inside window 1's backward bound besides answered ones.
    readings = _crop_readings({20.0: [3, 2], 22.0: [3, 2], 24.0: [3, 2], 26.0: [None, None],
                               28.0: [3, 2], 17.0: [3, 2], 18.0: [3, 2], 19.0: [3, 2],
                               21.0: [3, 2], 25.0: [None, None]})
    plan = bi.pip_requests(rows, _cache_frames(), (0.0, 34.0), readings, 5, unresolved=claims)
    asked = {round(float(entry["timestamp"]), 3): entry["window"] for entry in plan["requests"]}
    assert asked == {23.0: 0, 16.0: 0}, asked
    stopped = {entry["at"]: entry for entry in plan["claims_stopped"]}
    assert set(stopped) == {24.0}, plan["claims_stopped"]
    entry = stopped[24.0]
    assert entry["stopping"] == "planned_elsewhere" and entry["exhausted"] is False
    assert entry["planned_elsewhere_in"] == [0]
    assert "requested by window 0" in entry["because"] and "not this window's exhaustion" in \
        entry["because"], entry["because"]


# The two tests above cover a shared frame that got funded. These cover the one that did not -- the
# second review's reproducible defect: deferrals entered the dedupe set without entering the ownership
# map, so an overlapping window called the un-asked frame ``evidence_exhausted``. Fixture identical to
# ``build/work/bb9/winner-corroboration-review-v2-2026-11-05/overlap-deferred-false-exhaustion.json``.


def _overlap_deferral_fixture():
    rows = [dict(timestamp=0.0, screen_state="gameplay", round_clock=60),
            dict(timestamp=5.0, screen_state="gameplay", round_clock=55, round_end="ko"),
            dict(timestamp=10.0, screen_state="gameplay", round_clock=60),
            dict(timestamp=20.0, screen_state="gameplay", round_clock=50, round_end="ko"),
            dict(timestamp=22.0, screen_state="gameplay", round_clock=60),
            dict(timestamp=24.0, screen_state="gameplay", round_clock=58, round_end="ko")]
    claims = [dict(_claimed()[0]), dict(_claimed()[0], at=24.0, frame="frame-0024.jpg",
                   game_start=22.0)]
    readings = _crop_readings({20.0: [3, 2], 22.0: [3, 2], 24.0: [3, 2], 26.0: [None, None],
                               28.0: [3, 2], 17.0: [3, 2], 18.0: [3, 2], 19.0: [3, 2],
                               21.0: [3, 2], 25.0: [None, None]})
    return rows, claims, readings


def test_a_deferred_frame_in_an_overlapping_bound_names_the_unfunded_window():
    """One funded look, four deferred by name -- and no window calls the shared frame exhausted.

    The first window spends the single funded attempt on its queue anchor and defers its rest; the
    second defers the pair its bounded pool holds; the third, whose backward bound shares 23 s with
    the second, must say so and stop. Not ``evidence_exhausted`` -- the frame that would corroborate
    it sits in this plan's own deferred list, owed an allowance, not an answer. And 23 s is deferred
    exactly once: a second window never books the same unfunded work twice.
    """
    rows, claims, readings = _overlap_deferral_fixture()
    plan = bi.plan_investigation(claims, _cache_frames(), 40.0, observations=rows,
                                 window=(0.0, 34.0), max_requests=1, routes=("pip_crops",),
                                 crop_readings=readings)
    route = _route_of(plan)
    assert [round(float(entry["timestamp"]), 3) for entry in plan["pip_requests"]] == [5.0]
    left = route["deferred"]
    assert {round(float(entry["timestamp"]), 3) for entry in left} == {7.0, 9.0, 23.0, 16.0}
    assert len(left) == len({entry["frame"] for entry in left}), "a shared frame deferred twice"
    stopped = {round(float(entry["at"]), 3): entry for entry in route["claims_stopped"]}
    assert set(stopped) == {24.0}, route["claims_stopped"]
    entry = stopped[24.0]
    assert entry["stopping"] == "deferred_elsewhere" and entry["exhausted"] is False
    assert entry["deferred_elsewhere_in"] == [1] and entry["planned_elsewhere_in"] == []
    assert "left unfunded by window 1" in entry["because"], entry["because"]
    assert "not this window's exhaustion" in entry["because"]
    assert "requested by" not in entry["because"], "a deferral is not a send"


def test_full_budget_moves_the_shared_frame_from_unfunded_to_a_named_send():
    """Positive budget, same geometry: the frame gets funded and the stop kind follows the truth.

    Six attempts buy every free frame in the fixture -- 23 s included, by window 1 -- so no deferral
    survives, nothing is spent twice, and the overlapping window's report is the funded-send kind it
    was before this defect existed. The two reports differ only in what the plan actually did, which
    is the whole point of typing them.
    """
    rows, claims, readings = _overlap_deferral_fixture()
    plan = bi.plan_investigation(claims, _cache_frames(), 40.0, observations=rows,
                                 window=(0.0, 34.0), max_requests=6, routes=("pip_crops",),
                                 crop_readings=readings)
    route = _route_of(plan)
    asked = {round(float(entry["timestamp"]), 3) for entry in plan["pip_requests"]}
    assert asked == {5.0, 7.0, 9.0, 16.0, 23.0}, asked
    assert route["deferred"] == []
    assert len(plan["pip_requests"]) == len({entry["frame"] for entry in plan["pip_requests"]})
    stopped = {round(float(entry["at"]), 3): entry for entry in route["claims_stopped"]}
    assert set(stopped) == {24.0}, route["claims_stopped"]
    entry = stopped[24.0]
    assert entry["stopping"] == "planned_elsewhere" and entry["exhausted"] is False
    assert entry["planned_elsewhere_in"] == [1] and entry["deferred_elsewhere_in"] == []


# --------------------------------------------- the offline evaluator reads the reviewed truth


def _grader():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "grade_reset_probe", str(ROOT / "scripts" / "grade_reset_probe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_grader_reads_the_reviewed_labels_and_refuses_the_superseded_freeze():
    """Presence, counts and the correction itself, at the only place any of it is consumed.

    The freeze stays on disk untouched -- it is the record of what was first believed. But an evaluation
    that quietly read it after Codex corrected it would grade against a retired answer key, and this
    correction is exactly the kind that silences itself: an unknown presence coerced through ``bool()``
    becomes "known absent", and nothing downstream can tell the difference. So the grader keeps ``None``
    unknown, prefers the per-side truth the review added (``frame-2465`` LEFT unsettled, RIGHT partly
    drawn), withholds both unknown-count frames from the rate, and refuses the superseded file the
    moment a sibling declares it replaced.
    """
    grp = _grader()
    batch = ROOT / "build/work/bb9/winner-corroboration-v2"
    labels = grp.read_label_file(str(batch / "labels-codex-reviewed-2026-11-05.json"))
    by_name = {entry["frame"]: entry for entry in labels["labels"]}
    assert labels["inspector"].startswith("Codex")
    assert by_name["frame-2465.jpg"]["row_drawn"] == {"left": None, "right": True}, \
        "unknown presence must not become absent"
    assert by_name["frame-3545.jpg"]["row_drawn"] == {"left": False, "right": False}
    assert by_name["frame-2465.jpg"]["left"] is None and by_name["frame-3545.jpg"]["right"] is None
    annotations = json.loads((batch / "annotations.json").read_text(encoding="utf-8"))
    frames, withheld = grp.graded_frames(labels, annotations)
    assert len(frames) == 8 and len(withheld) == 2
    assert {w["frame"] for w in withheld} == {"frame-2465.jpg", "frame-3545.jpg"}
    assert all(w["reviewed_row_drawn"] in ({"left": None, "right": True},
                                           {"left": False, "right": False}) for w in withheld)
    rows = grp.side_report([], labels, annotations)
    presence = [row["label_drawn"] for row in rows]
    assert (presence.count(True), presence.count(False), presence.count(None)) == (13, 2, 1)
    assert all(row["graded"] is False for row in rows if row["label"] is None)
    with pytest.raises(SystemExit) as refused:
        grp.read_label_file(str(batch / "labels-evaluator-only.json"))
    assert "labels-codex-reviewed-2026-11-05.json" in str(refused.value)
    # The freeze is still byte-stable evidence and still declares itself non-runtime; and no truth
    # leaked into anything the runtime reads.
    frozen = json.loads((batch / "labels-evaluator-only.json").read_text(encoding="utf-8"))
    assert frozen["labels_are_runtime_input"] is False and len(frozen["frames"]) == 8
    runtime = (json.loads((batch / "run/prepared.json").read_text(encoding="utf-8")), annotations)
    leaked = [hit for doc in runtime for hit in grp.truth_bearing_fields(doc)]
    assert leaked == [], leaked


# ---------------------------------------------------------------- the recording itself
def _tracked_from(withhold=()):
    """The recording regrouped with named pictures withheld by identity -- crop answers and rows alike.

    Matching the frame *key* as well as its recorded path matters: the crop answer is stamped 237.24 and
    the row it joins is 237.236988, so a timestamp-matched experiment silently drops nothing. That is how
    the first support audit got two of its explanations wrong.
    """
    readings = dict(be.crop_observations(be.crop_runs()))
    rows = be.read_facts(str(BB9_TIMELINE))
    wanted = {_identity(name) for name in withhold}
    for key, entry in readings.items():
        if {bg._frame_identity(key), bg._frame_identity(entry.get("frame"))} & wanted:
            entry["win_dots"] = [None] + list(entry.get("win_dots") or [None])[1:]
            entry["win_dots_drawn"] = [None] + list((entry.get("win_dots_drawn") or [None])[1:])
    for row in rows:
        if {_identity(Path(str(row.get("frame") or row.get("path") or "")).name)} & wanted:
            row["win_dots"] = [None] + list(row.get("win_dots") or [None])[1:]
    merged, _ = be.merge_crop_readings(rows, readings, (0.0, float("inf")))
    graded, _ = be.grade_rows(merged)
    return bg.track_games(bg.build_observations(graded), games_per_set=None,
                          source=bg.source_bounds(manifest=str(BB9_MANIFEST), duration=6928.421136))


def _identity(name):
    return bg._frame_identity("%s/%s" % (CACHE_DIR, name))


def _game_at(tracked_result, start):
    return [g for g in tracked_result["games"] if abs(float(g["start"]) - start) < .01][0]


def test_the_confirmation_pair_is_counted_as_two_looks_and_neither_alone_is_missing():
    """The real frames, the real merge: support says what the compressed series could not.

    Frame-4066 at 4069.064786 is the reviewer's count-unknown slot; frame-4068 at 4071.066786 is the
    confirmation. The compressed row carried one entry for both, which is why the first support audit
    reported every BB9 winner as single-read. Withholding either still credits p1, and the survivor is now
    honestly one look -- the flag returns, which is the point of counting instead of asserting.
    """
    both = _tracked_from()
    game = _game_at(both, 3958.954792)
    support = game["winner_support"]
    assert (game["winner"], game["winner_basis"]) == ("p1", "win_dots")
    assert support["frame_count"] == 2, support
    assert support["seconds_apart"] == pytest.approx(2.002, abs=0.01), support
    assert not [item for item in both["unresolved"]
                if item["type"] == "game_winner_single_reading"
                and abs(item["at"] - 4069.064786) < 0.01], both["unresolved"]

    for withheld in ("frame-4066.jpg", "frame-4068.jpg"):
        one = _tracked_from(withhold=(withheld,))
        other = _game_at(one, 3958.954792)
        assert other["winner"] == "p1", withheld
        assert other["winner_support"]["frame_count"] == 1, (withheld, other["winner_support"])
        assert [item for item in one["unresolved"] if item["type"] == "game_winner_single_reading"
                and abs(item["at"] - 4069.064786) < 2.1], withheld

    gone = _tracked_from(withhold=("frame-4066.jpg", "frame-4068.jpg"))
    assert _game_at(gone, 3958.954792)["winner"] is None


def test_the_recordings_winners_are_mostly_read_once_and_the_flag_names_them():
    """The published totals the next batch is priced from: 21 single-read of 27, and none silently sized.

    Live corpus, deliberately -- this is the figure a follow-on batch gets priced against. It moved
    twice on 2026-11-06. First the winner-corroboration-v2 batch answered eight interiors, so three winners
    (53.053 s, 3247.244 s, 3394.391 s) gained a second look at the deciding count: 24 / 3 became 21 / 6 with
    games, winners and rounds held at 30 / 27 / 122. Then the outcome-recovery-v1 batch named three winners
    the recording had never had (5060.055 s, 5495.490 s, 6246.240 s) and regrouped 5495-5710 into two games,
    taking credited winners 27 -> 30 and single-frame support 21 -> 24, because each of those three arrives
    read once. Nothing was lost and no winner ever changed side under an identity-based drop:
    build/work/bb9/outcome-recovery-v1/consequence-actual.json.
    """
    result = _tracked_from()
    credited = [g for g in result["games"] if g.get("winner_basis") == "win_dots"]
    assert (len(result["games"]), len(credited)) == (30, 30), (len(result["games"]), len(credited))
    assert sum(1 for g in credited if g["winner_support"]["single_reading"]) == 24
    assert sum(1 for g in credited if not g["winner_support"]["single_reading"]) == 6
    assert {(g["winner_support"] or {}).get("frame_count") for g in credited} == {1, 2}
    items = [item for item in result["unresolved"] if item["type"] == "game_winner_single_reading"]
    # 24, not 21: the three winners the outcome batch named each arrived from one readable instant, so
    # closing an unobserved winner opened a single-reading claim. That is the cost the proposal stated
    # before it was authorized, recorded in outcome-recovery-v1/necessity.json and paid on purpose.
    assert len(items) == 24, len(items)
    assert all(item["frame"] and item["would_resolve"] and item["side"] for item in items)
    for game in credited:
        # The flag is provenance, not a veto: every one of these winners still counts toward its set.
        assert game["counts_toward_set"] is True, game["start"]
        assert not game["contested"], game["start"]
