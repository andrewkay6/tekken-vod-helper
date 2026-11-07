"""A win-row reset is the only thing allowed to split a counted run -- tested both ways.

The boundary a round count draws is chosen by arithmetic, not by the screen, so two spans of this recording
(3958.955-4174.170 and 5060.055-5545.540) stand as games the detector never saw end. The inspecting crops
for both show a row that **cleared** -- 1-0 before the break and 0-0 after at the first, 2-1 then 0-0 at the
second -- and a cleared row can only mean a new game opened, because round wins are added to a game's row
and never taken away.

Nothing about the presentation is consulted. A fade and a stage intro sit inside **12 of the recording's 112
round transitions**, including inside games whose winners are accepted, so a rule keyed to new-game
*vocabulary* would cut decided games; these tests are the standing refusal to do that.

The one thing a cleared row may not be is a second opinion on a reading the temporal rules already judged.
A dip that :func:`boundary_games._dot_regressions` sets aside against its own neighbours is the same frame
reset detection reads as "the row went empty", and one frame cannot be both a misread and a game boundary.
The negatives below hold the two rules in agreement; where the frames genuinely cannot tell a reset from a
misread, the boundary is contested with both groupings kept rather than confirmed.
"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be        # noqa: E402
from tekken_vod_helper import boundary_games as bg          # noqa: E402

TARGET = 3
BB9_TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
BB9_MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"

pytestmark = pytest.mark.skipif(not BB9_TIMELINE.is_file(),
                                reason="the saved BB9 timeline is not present")

_evidence = be.evidence(None, facts=str(BB9_TIMELINE))
_tracked = bg.track_games(_evidence["observations"], games_per_set=None,
                          source=bg.source_bounds(manifest=str(BB9_MANIFEST),
                                                  duration=6928.421136))
#: A real round record, so every field the game recorder reads is present and shaped as production shapes it.
TEMPLATE = copy.deepcopy(_tracked["rounds"][0])


def round_at(offset, base, dots, end_offset=30.0, conf=0.9):
    """One round at `base + offset`, with the win row as a model read it at the opening."""
    rnd = copy.deepcopy(TEMPLATE)
    rnd["start"] = base + offset
    rnd["end"] = base + offset + end_offset
    rnd["conclusion_estimate"] = rnd["end"]
    rnd["id"] = "round@%.3f" % rnd["start"]
    rnd["result_transition"] = False
    for side in ("p1", "p2"):
        value = (dots or {}).get(side)
        rnd["win_dots_evidence"][side] = ([] if value is None else
                                         [{"at": rnd["start"], "value": value, "drawn": True,
                                           "confidence": conf, "frame": "frames/frame-x.jpg"}])
        rnd["win_dots_seen"] = {}
    seen = {}
    for side in ("p1", "p2"):
        if rnd["win_dots_evidence"][side]:
            seen[side] = [entry["value"] for entry in rnd["win_dots_evidence"][side]]
    rnd["win_dots_seen"] = seen
    return rnd


BASE = 10000.0


# ---------------------------------------------------------------- positive: the reset splits the run
def test_a_cleared_row_inside_a_run_opens_a_second_game():
    """1-0 read at one round's opening, 0-0 at the next: five counted rounds are two games, not one."""
    rows = [round_at(0, BASE, {"p1": 0, "p2": 0}),
            round_at(40, BASE, {"p1": 1, "p2": 0}),
            round_at(80, BASE, {"p1": 1, "p2": 0}),
            round_at(120, BASE, {"p1": 0, "p2": 0}),
            round_at(160, BASE, {"p1": 0, "p2": 1})]
    games = bg._group_into_games(rows, [], TARGET)
    assert len(games) == 2, [(round(g["start"], 1), round(g["end"], 1)) for g in games]
    reset = games[0]["boundary_evidence"]
    assert [e["type"] for e in reset] == ["win_row_cleared_across_the_candidate_boundary"], reset
    assert reset[0]["after_round_index"] == 3
    assert reset[0]["readings"] == {"p1": [1, 0], "p2": [0, 0]}, reset
    # The second game starts where the row cleared: its opening is evidence, not an assumption.
    assert games[1]["start"] == BASE + 120


def test_reaching_the_target_still_beats_a_reset():
    """A side that took three rounds won a game; the row clearing afterwards opens the *next* game."""
    rows = [round_at(0, BASE, {"p1": 3, "p2": 0}), round_at(40, BASE, {"p1": 0, "p2": 0}),
            round_at(80, BASE, {"p1": 0, "p2": 0})]
    games = bg._group_into_games(rows, [], TARGET)
    assert games[0]["winner"] == "p1"
    assert games[0]["boundary_evidence"] == [], games[0]["boundary_evidence"]


# ---------------------------------------------------------------- negative: everything else
def test_an_uncleared_row_across_a_break_does_not_split_the_run():
    """Ordinary rounds inside one game: the row only grows, so no boundary is invented between them."""
    # Six rounds: the cap fires after the fifth and there is a round on the far side of the cut, which is
    # the only situation where a cap-forced boundary exists to be verified or disbelieved.
    rows = [round_at(0, BASE, {"p1": 0, "p2": 0}), round_at(40, BASE, {"p1": 1, "p2": 0}),
            round_at(80, BASE, {"p1": 1, "p2": 1}), round_at(120, BASE, {"p1": 2, "p2": 1}),
            round_at(160, BASE, {"p1": 2, "p2": 2}), round_at(200, BASE, {"p1": 2, "p2": 2})]
    assert bg._row_reset_within(rows, TARGET) is None
    games = bg._group_into_games(rows, [], TARGET)
    # Stronger than "no split happened": the read row on both sides of the count's edge *refused* it, which
    # is the pre-existing guard this rule has to sit beside rather than over.
    assert len(games) == 1 and games[0]["winner"] is None, [(g["start"], g["end"]) for g in games]
    refused = [n["type"] for n in games[0]["round_count_beyond_target"]]
    assert refused == ["win_row_persisted_across_the_candidate_boundary"], refused
    assert games[0]["boundary_evidence"] == [], games[0]["boundary_evidence"]


def test_a_single_sides_dip_is_not_treated_as_a_reset():
    """One side dropping a pip is a misread or an excused fall; a scoreboard clearing is neither."""
    rows = [round_at(0, BASE, {"p1": 2, "p2": 1}), round_at(40, BASE, {"p1": 1, "p2": 1}),
            round_at(80, BASE, {"p1": 2, "p2": 1})]
    assert bg._row_reset_within(rows, TARGET) is None


def test_a_run_with_no_readings_is_preserved_and_the_missing_frames_are_named():
    """No row, no opinion: the count's boundary stands and the exact absent observation is written down."""
    rows = [round_at(offset, BASE, None) for offset in (0, 40, 80, 120, 160, 200)]
    games = bg._group_into_games(rows, [], TARGET)
    # Six unread rounds are cut at the cap: the first run carries the cut, and the cut is filed as
    # unverified because nothing on either side of it was ever read.
    assert len(games) == 2 and games[0]["winner"] is None, [(g["start"], g["end"]) for g in games]
    note = games[0]["boundary_evidence"][0]
    assert note["type"] == "cap_boundary_has_no_win_row_read"
    assert note["would_confirm"] and note["would_refute"]
    assert all(not side["read_before"] and not side["read_after"]
               for side in note["readings_needed"].values())


def test_a_game_the_screen_ended_is_never_tagged_as_count_forced():
    """Without this guard the disclaimer landed on fourteen decided games.

    It would have read as though the detector had guessed at boundaries it had watched close -- and a note
    attached to everything is a note that means nothing.
    """
    closing = copy.deepcopy(_tracked["games"][-1])
    rows = [round_at(0, BASE, {"p1": 1, "p2": 0}), round_at(40, BASE, {"p1": 2, "p2": 0})]
    rows[0]["result_transition"] = True
    rows[0]["closed_by"] = closing.get("closed_by") or {"at": rows[0]["end"], "basis": "menu"}
    games = bg._group_into_games(rows, [], TARGET)
    # The point is the disclaimer, not the count: a game the screen ended carries no note about counting,
    # whichever game in the list that turns out to be.
    assert all(game["boundary_evidence"] == [] for game in games), \
        [(game["start"], game["boundary_evidence"]) for game in games]


# ------------------------------------------------- adjudication first: one reading, one verdict
def _rows(spec):
    return [round_at(offset, BASE, dots, conf=conf) for offset, dots, conf in spec]


def crop_rows(series, confidences, base=20000.0, spacing=40.0):
    """Classified rows that track as one run of ``len(series)`` rounds, one pip reading per opening.

    Built as the pipeline sees them -- clock rows with crop answers attached -- so the assertion below is
    about :func:`boundary_games.track_games` and not about the reset helper in isolation.
    """
    rows = []
    for index, (value, confidence) in enumerate(zip(series, confidences)):
        opening = base + index * spacing
        rows.append({"timestamp": opening, "path": "frames/frame-%04d.jpg" % int(opening),
                     "state": "gameplay", "evidence": "", "round_clock": 60,
                     "win_dots": list(value), "win_dots_drawn": [True, True],
                     "win_dots_confidence": list(confidence), "confidence": 0.95})
        rows.append({"timestamp": opening + 20.0, "path": "frames/frame-%04d.jpg" % int(opening + 20),
                     "state": "gameplay", "evidence": "", "round_clock": 40, "confidence": 0.95})
    return rows


def test_a_dip_the_fall_rule_resolved_is_not_a_reset_end_to_end():
    """The reproduced regression, through the real tracker: one game, and no boundary is claimed.

    Before the fix this run split at the 0-0 its neighbours had already discredited, and the split record
    made that boundary look confirmed. What the two rules agree on now is a run of five rounds whose
    conclusion was never seen: incomplete, queued, and every raw reading still where it was sent.

    The series is Codex's negative of 2026-11-04 -- p1 [0,1,0,1,2], p2 all zero, the middle 0-0 at 0.1 --
    as classified rows rather than hand-built round records, so the clock and the merge are under test too.
    """
    result = bg.track_games(bg.build_observations(
        crop_rows([[0, 0], [1, 0], [0, 0], [1, 0], [2, 0]],
                  [[0.95, 0.95], [0.95, 0.95], [0.1, 0.1], [0.95, 0.95], [0.95, 0.95]])),
        rounds_per_game=TARGET, games_per_set=None)
    games = [g for g in result["games"] if 20000.0 <= float(g["start"]) < 20300.0]
    assert len(games) == 1, [(float(g["start"]), float(g["end"])) for g in games]
    game = games[0]
    assert game["status"] == "incomplete" and game["winner"] is None
    assert not game["contested"], "adjudication settled the dip, so nothing is left in dispute here"
    notes = [note for note in game["boundary_evidence"]
             if note["type"] == "win_row_reset_declined_by_temporal_adjudication"]
    assert len(notes) == 1, game["boundary_evidence"]
    assert notes[0]["confirms"] is None and notes[0]["declined_readings"] == ["p1"], notes[0]
    # Raw evidence survives: the discarded reading is still on its round, with the neighbours that
    # outweighed it, and only the adjudicated series the winner is computed from leaves it out.
    dipped = next(rnd for rnd in result["rounds"] if abs(float(rnd["start"]) - 20080.0) < 0.01)
    assert [e["value"] for e in dipped["win_dots_evidence"]["p1"]] == [0]
    assert dipped["win_dots_evidence"]["p1"][0]["confidence"] == 0.1
    assert [q["value"] for q in dipped["win_dots_rejected"]] == [0]
    assert [s["value"] for s in dipped["win_dots_rejected"][0]["supported_by"]] == [1, 1]


def test_a_reset_the_fall_rule_cannot_resolve_is_contested_not_confirmed():
    """Same frames, bolder dip: neither a misread nor a new game is supported, so both stay on record.

    Nothing here is a timestamp or a confidence cutoff. The split is withheld because the row came back to
    the height it held on its very next reading, which is the pair of hypotheses
    :func:`boundary_games._dot_regressions` already refuses to settle by arithmetic.
    """
    rows = _rows([(0, {"p1": 0, "p2": 0}, 0.4),
                  (40, {"p1": 1, "p2": 0}, 0.4),
                  (80, {"p1": 0, "p2": 0}, 0.99),
                  (120, {"p1": 1, "p2": 0}, 0.4),
                  (160, {"p1": 2, "p2": 0}, 0.4)])
    assert bg._row_reset_within(rows, TARGET) is None
    games = bg._group_into_games(rows, [], TARGET)
    assert len(games) == 1, [(g["start"], g["end"]) for g in games]
    game = games[0]
    assert game["contested"] is True and game["winner"] is None
    assert any(reason.startswith("win_row_reset_unresolved_at_")
               for reason in game["contested_reasons"]), game["contested_reasons"]
    note = game["boundary_evidence"][0]
    assert note["type"] == "win_row_reset_ambiguous" and note["confirms"] is None, note
    assert note["readings"] == {"p1": [1, 0], "p2": [0, 0]}, note
    assert note["rebuilt"]["p1"] == {"held_before": 1, "read_at": BASE + 120, "value": 1}, note
    assert note["alternative"]["after_round_index"] == 2, note["alternative"]
    assert game["dot_regressions"].get("p1"), "the fall itself still contests the game too"


def test_a_cleared_row_that_stays_dark_is_still_a_reset_after_adjudication():
    """The positive the probe is waiting on, re-tested against the coupling this fix added.

    ``game-reset-probe-v1`` expects 1-0 before a break to read 0-0 after it and stay dark while the new
    game fills on the other side. A row no neighbour contradicts is a boundary, and this is the case the
    fix must not swallow.
    """
    rows = _rows([(0, {"p1": 0, "p2": 0}, 0.9),
                  (40, {"p1": 1, "p2": 0}, 0.9),
                  (80, {"p1": 1, "p2": 0}, 0.9),
                  (120, {"p1": 0, "p2": 0}, 0.9),
                  (160, {"p1": 0, "p2": 1}, 0.9)])
    reset = bg._row_reset_within(rows, TARGET)
    assert reset and reset["after_round_index"] == 3 and reset["verdict"] == "reset", reset
    games = bg._group_into_games(rows, [], TARGET)
    assert len(games) == 2, [(g["start"], g["end"]) for g in games]
    boundary = games[0]["boundary_evidence"][0]
    assert boundary["confirms"] == ("a game boundary inside the run, ahead of the one the round count "
                                    "suggested"), boundary
    assert not games[0]["contested"] and not games[1]["contested"]


def test_an_ambiguous_edge_at_the_count_boundary_is_recorded_and_not_verified():
    """The cut at the end of the run gets the same treatment as a cut inside it.

    Here the row went dark exactly at the boundary the count drew and then read the old height again, with
    the dark frame the bolder claim of the three -- so the fall rule will not resolve it and the reset rule
    may not confirm it. Before this, a cap cut whose readings pointed different ways was recorded as nothing
    at all: `_cap_boundary_reset` declined and `_cap_boundary_unread` judged both ends read.
    """
    dark = [(0, {"p1": 0, "p2": 0}, 0.4),
            (40, {"p1": 1, "p2": 0}, 0.4),
            (80, {"p1": 1, "p2": 1}, 0.4),
            (120, {"p1": 2, "p2": 1}, 0.4),
            (160, {"p1": 2, "p2": 1}, 0.4),
            (200, {"p1": 0, "p2": 0}, 0.99),
            (240, {"p1": 2, "p2": 1}, 0.4)]
    games = bg._group_into_games(_rows(dark), [], TARGET)
    first = [g for g in games if float(g["start"]) == BASE][0]
    assert first["contested"] is True, first["contested_reasons"]
    note = first["boundary_evidence"][0]
    assert note["verdict"] == "ambiguous" and note["confirms"] is None, note
    assert note["at"] == BASE + 200 and note["alternative"]["at"] == BASE + 200, note
    assert note["reading_frames"] == {"p1": [BASE + 160, BASE + 200],
                                      "p2": [BASE + 160, BASE + 200]}, note

    # Weaken the dark frame and the same frames stop being ambiguous: the neighbours either side of the cut
    # bracket it, the fall rule resolves it as a misread, and the boundary is declined rather than contested.
    # Adjudication spans the cut for exactly this reason -- a bracket read from one side of it is no bracket.
    softer = list(dark)
    softer[5] = (200, {"p1": 0, "p2": 0}, 0.2)
    bracketed = _rows(softer)
    games = bg._group_into_games(bracketed, [], TARGET)
    first = [g for g in games if float(g["start"]) == BASE][0]
    note = first["boundary_evidence"][0]
    assert note["type"] == "win_row_reset_declined_by_temporal_adjudication", note
    assert note["confirms"] is None and first["contested"] is False, first["contested_reasons"]
    assert note["declined_readings"] == ["p1", "p2"], note
    # The note carries the bracket it yielded to, because the rejection itself is recorded by the fall rule
    # when it adjudicates the group the dark reading belongs to -- not this one.
    assert note["next_live_reading"] == {"p1": {"value": 2, "at": BASE + 240},
                                         "p2": {"value": 1, "at": BASE + 240}}, note
    assert [e["value"] for e in bracketed[5]["win_dots_evidence"]["p1"]] == [0], "raw kept as sent"


def test_a_split_head_is_judged_on_its_own_boundary_and_not_two_rounds_later():
    """The neighbour that bounds a split head is the round the row opened.

    ``game-reset-probe-v1``'s rehearsal caught this: with the round *after the whole counted run* announcing
    ROUND 2, the head of a genuine split was contested by ``round_numbering_continues_at_2`` -- an
    announcement that belongs to the new game two rounds later, where a second round is exactly what a game
    that just opened produces. Contesting a reset with its own new game's numbering is the same bypass this
    module exists to stop, pointed the other way.
    """
    rows = _rows([(0, {"p1": 0, "p2": 0}, 0.9),
                  (40, {"p1": 1, "p2": 0}, 0.9),
                  (80, {"p1": 1, "p2": 0}, 0.9),
                  (120, {"p1": 0, "p2": 0}, 0.9),
                  (160, {"p1": 0, "p2": 1}, 0.9),
                  (200, {"p1": 0, "p2": 1}, 0.9)])
    rows[5]["number_observed"] = 2          # the round after the run, two rounds past the boundary
    games = bg._group_into_games(rows, [], TARGET)
    assert len(games) == 2, [(g["start"], g["end"]) for g in games]
    assert games[0]["contested"] is False, games[0]["contested_reasons"]
    assert games[0]["conclusion_signals"]["next_round_number"] is None, games[0]["conclusion_signals"]

    # The same announcement moved to the round the boundary actually opens: now the cut has to answer it.
    adjacent = _rows([(0, {"p1": 0, "p2": 0}, 0.9),
                      (40, {"p1": 1, "p2": 0}, 0.9),
                      (80, {"p1": 1, "p2": 0}, 0.9),
                      (120, {"p1": 0, "p2": 0}, 0.9),
                      (160, {"p1": 0, "p2": 1}, 0.9),
                      (200, {"p1": 0, "p2": 1}, 0.9)])
    adjacent[3]["number_observed"] = 2
    head = bg._group_into_games(adjacent, [], TARGET)[0]
    assert head["contested"] is True and "round_numbering_continues_at_2" in head["contested_reasons"], \
        head["contested_reasons"]
    assert head["conclusion_signals"]["round_numbering_contradicts"] is True, head["conclusion_signals"]


# ---------------------------------------------------------------- the recording itself
#: Both probes that read the pip row around this boundary: the six frames of 2026-11-04 and the one
#: confirmation frame of 2026-11-05. Neither existed when the rule shipped, and each is evidence now.
PROBE_RUNS = ("game-reset-probe-v1", "game-reset-confirmation-v1")
# The 2026-11-06 five-read batch answered rows in 5060-5215, 5630, 5685, 5710 and 6341, which moved the
# recording-wide totals these older tests quote (winners 27 -> 30) and superseded the boundary note on the
# head game with a reached target. Neither test is about that batch, so both name a corpus that excludes it:
# a before/after over one probe's own answers has to hold every later answer fixed, or the comparison is
# measuring whichever batch ran last.
LATER_RUNS = ("outcome-recovery-v1",)
FLASH_LEFT_AT = 4069.064786           # frame-4066: the reviewer's count-unknown slot
AFTER_FLASH_LEFT_AT = 4071.066786     # frame-4068: the confirmation, 2.002 s on in the same freeze


def _tracked_from(exclude_runs=(), withhold_left_at=()):
    """The recording regrouped from a named crop corpus, through the canonical merge.

    ``exclude_runs`` drops whole answered runs (the state before those probes were spent) and
    ``withhold_left_at`` drops one side of each named frame (the state where that count is not believed).
    Both are needed because the answers are evidence like any other now: a test that quotes a
    recording-wide count without saying which corpus produced it is quoting a number, not a result.
    """
    runs = [run for run in be.crop_runs()
            if not any(name in str(run) for name in exclude_runs)]
    readings = dict(be.crop_observations(runs))
    stamps = set(withhold_left_at)
    if stamps:
        for entry in readings.values():
            if entry.get("timestamp") in stamps and (entry.get("win_dots") or [None])[0] is not None:
                entry["win_dots"] = [None] + list(entry["win_dots"][1:])
                entry["win_dots_drawn"] = [None] + list((entry.get("win_dots_drawn") or [])[1:])
    merged, _ = be.merge_crop_readings(be.read_facts(str(BB9_TIMELINE)), readings, (0.0, float("inf")))
    graded, _ = be.grade_rows(merged)
    return bg.track_games(bg.build_observations(graded), games_per_set=None,
                          source=bg.source_bounds(manifest=str(BB9_MANIFEST), duration=6928.421136))


def _span_of(tracked, lo, hi):
    return [(round(float(g["start"]), 3), round(float(g["end"]), 3), g["status"], g.get("winner"),
             [note.get("type") for note in g.get("boundary_evidence") or []])
            for g in tracked["games"] if float(g["start"]) < hi and float(g["end"] or 0) > lo]


def test_the_probe_answers_split_their_own_two_runs_and_nothing_else():
    """Before and after the seven answered crops, over the same clock rows: only those runs moved.

    Neither span came out as the rehearsal drew it. 5060.055-5545.540 split on the cleared row, as the rule
    intends. 3958.955-4174.170 never reached a cap-forced cut at all: a pip row that read **3** at
    4069.065 put p1 at the target inside the run, so the winner path closed it and the boundary is a
    concluded game rather than a reset. That is the honest output of the evidence, and the reason the
    rehearsal's totals were never the acceptance test.
    """
    before = _tracked_from(exclude_runs=PROBE_RUNS + LATER_RUNS)
    after = _tracked_from(exclude_runs=LATER_RUNS)
    assert (len(before["games"]), sum(1 for g in before["games"] if g.get("winner")),
            len(before["rounds"])) == (29, 26, 122), "the pre-probe corpus is the published one"
    assert (len(after["games"]), sum(1 for g in after["games"] if g.get("winner")),
            len(after["rounds"])) == (30, 27, 122), "one split landed; the other became a decided game"

    assert _span_of(before, 5040, 5546) == [(5060.055, 5545.54, "incomplete", None,
                                             ["cap_boundary_has_no_win_row_read"])], \
        _span_of(before, 5040, 5546)
    head, tail = _span_of(after, 5040, 5496)
    assert head[0] == 5060.055 and head[3] is None and head[4] == [
        "win_row_cleared_across_the_candidate_boundary"], head
    assert tail[0] == 5495.49, tail
    note = [g for g in after["games"] if abs(float(g["start"]) - 5060.055) < .01][0][
        "boundary_evidence"][0]
    assert note["verdict"] == "reset" and note["at"] == 5495.489711, note
    assert note["readings"] == {"p1": [2, 0], "p2": [1, 0]}, note
    assert bool(note["confirms"]), "a sustained dark row is a boundary, and this one says so"

    outside = lambda tracked: sorted((round(float(g["start"]), 3), round(float(g["end"]), 3),
                                      g.get("winner")) for g in tracked["games"]
                                     if not (3900 < float(g["start"]) < 4400
                                             or 5040 < float(g["start"]) < 5720))
    assert outside(before) == outside(after), "no game outside the two probed runs moved"

    # And the disclaimer still declines to touch a decided game: every screen-confirmed game in the
    # ~200 s band carries no boundary note, before the answers or after. The one that joined the band
    # (4094.09-4299.295) joined it as a game closed on the target, not on a count.
    for tracked in (before, after):
        accepted = [g for g in tracked["games"] if g.get("winner")
                    and 200.0 <= float(g["end"]) - float(g["start"]) <= 220.2]
        assert len(accepted) == (6 if tracked is before else 7), \
            [round(float(g["start"]), 2) for g in accepted]
        for game in accepted:
            assert game["boundary_evidence"] == [], (game["start"], game["boundary_evidence"])


def test_the_outcome_batch_supersedes_the_reset_note_without_moving_the_split():
    """What 2026-11-06 actually did to this finding, pinned so it cannot silently reverse.

    The split at 5495.489711 stands. What changed is the head game's *justification*: with a readable row at
    5217.212 the run 5060.055-5215.210 concludes because p1 reached three, so it never spans the candidate
    boundary and carries no `win_row_cleared_across_the_candidate_boundary` note at all. That is stronger
    evidence than an inferred clear, not a lost result -- but it is a change to a published claim, so it
    gets a test of its own rather than being folded into the numbers above.
    """
    now = _tracked_from()
    head = [g for g in now["games"] if abs(float(g["start"]) - 5060.055) < .01][0]
    assert (round(float(head["end"]), 3), head["status"], head["winner"]) == (5215.21, "complete", "p1")
    assert head["basis"] == "win_dots_reached_target", head["basis"]
    assert [note.get("type") for note in head.get("boundary_evidence") or []] == []
    # The boundary the probe drew is still the boundary the recording uses.
    assert any(abs(float(g["start"]) - 5495.49) < .01 for g in now["games"])
    assert [round(float(g["start"]), 3) for g in now["games"] if 5040 < float(g["start"]) < 5800] == [
        5060.055, 5495.49, 5660.655, 5775.77]
    # And no game anywhere still reports the crossing note: the batch superseded every instance of it.
    assert not [note for g in now["games"] for note in g.get("boundary_evidence") or []
                if note.get("type") == "win_row_cleared_across_the_candidate_boundary"]
    assert (len(now["games"]), sum(1 for g in now["games"] if g.get("winner"))) == (30, 30)

def test_the_flash_winner_survives_either_frame_alone_and_falls_with_both():
    """Two frames, one claim: the p1 winner at 3958.955 is carried by either and dies only with both.

    Frame-4066's LEFT is unknown truth -- the K.O. flash blooms across the first two slots -- so the `3` the
    model answered there was never scored, and on 2026-11-04 it was load-bearing: withholding it turned the
    run into a capped one and the reset rule drew the boundary instead. The confirmation probe asked the next
    cached frame in the same freeze (4071.066786, 2.002 s on), which an independent reviewer had read as 3-0.

    So the winner no longer rests on an unverifiable pixel, and this test is the shape of that claim: either
    reading alone keeps 30 games / 27 winners, and only withdrawing both returns to 31 / 26 with the split
    drawn by the row. What it does *not* buy is independence at the instant 4069.065 -- both frames sit in
    one clock freeze, one round, and one model's reading of the same JPEG family.
    """
    both = _tracked_from(exclude_runs=LATER_RUNS)
    only_confirmation = _tracked_from(exclude_runs=LATER_RUNS, withhold_left_at=(FLASH_LEFT_AT,))
    only_flash = _tracked_from(exclude_runs=LATER_RUNS, withhold_left_at=(AFTER_FLASH_LEFT_AT,))
    neither = _tracked_from(exclude_runs=LATER_RUNS,
                            withhold_left_at=(FLASH_LEFT_AT, AFTER_FLASH_LEFT_AT))
    for tracked, label in ((both, "both readings"), (only_confirmation, "flash withheld"),
                           (only_flash, "confirmation withheld")):
        assert (len(tracked["games"]), sum(1 for g in tracked["games"] if g.get("winner"))) == (30, 27), \
            label
        decided = _span_of(tracked, 3940, 4080)
        assert decided == [(3958.955, 4069.065, "complete", "p1", [])], (label, decided)

    assert (len(neither["games"]), sum(1 for g in neither["games"] if g.get("winner"))) == (31, 26)
    reset = _span_of(neither, 3940, 4049)
    assert reset == [(3958.955, 4019.015, "incomplete", None,
                      ["win_row_cleared_across_the_candidate_boundary"])], reset
    note = [g for g in neither["games"] if abs(float(g["start"]) - 3958.955) < .01][0][
        "boundary_evidence"][0]
    assert note["verdict"] == "reset" and note["at"] == 4049.044787, note
    assert note["readings"] == {"p1": [1, 0], "p2": [0, 0]}, note
    for tracked in (both, neither):
        assert not any(g.get("contested") for g in tracked["games"] if float(g["start"]) < 5800)


def test_five_rounds_alone_is_not_a_boundary_and_not_a_rejection():
    """Both halves of the mistake this nearly made twice.

    Codex rejected a duration-only *acceptance* of 3958.955-4174.170; I then inferred the opposite from the
    same span's five rounds and was wrong that way too -- eight decided games here also run five rounds.
    Neither direction is evidence. Only a row is.
    """
    runs = [g for g in _tracked["games"]
            if len([r for r in _tracked["rounds"]
                    if float(g["start"]) <= float(r["start"]) <= float(g["end"] or 0) + 0.5]) == 5]
    decided = [g for g in runs if g.get("winner")]
    assert len(decided) >= 5, [round(float(g["start"]), 1) for g in runs]
    assert bg._row_reset_within([copy.deepcopy(TEMPLATE)], TARGET) is None
