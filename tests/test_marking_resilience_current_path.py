"""Marking resilience through the path recovery runs on today, on the real recording.

`tests/test_boundary_marks.py` exercises `boundary_marks.recover_sets` as a unit, and `scripts/
evaluate_marking_resilience.py` scores it. Neither is how markings reach the product now: the live route is
`boundary_investigate` behind ``python -m tekken_vod_helper.boundary_cli investigate-games``, which recovers
games and candidates from a saved classified timeline, keeps an unresolved queue, and lets a mark move only
the *order* of the questions asked. A mark that cannot corrupt a helper can still corrupt a pipeline, so
these tests drive the entry point with the saved BB9 timeline and its real frame manifest -- no fixtures
invented, no model called.

Two claims are checked over and over, in pairs:

* the **recovery does not move** when the markings move -- shifted, duplicated, deleted, dropped mid-set, or
  placed past the end of the tape; and
* the **plan may rearrange** around them, because a mark is a statement about where a human was looking and
  that is exactly what a work queue should listen to.

The evidence base is now a choice, and both choices are tested. Without a named crop corpus the command
plans from classified state, can show where sets begin and end, and cannot say who won them -- asserted in
``test_this_path_bounds_sets_and_cannot_score_them`` rather than left for a reader to infer from a null.
With the recording's saved answers loaded (``integrated`` below, and the section that follows it) the same
marking faults have to leave a *result* in place: one complete candidate, the original partial still
partial, and the wins the crop readings validated. Invariance under noise is only worth what the thing held
constant is worth, and a state-only recovery used to make that promise all cost and no conclusion.
"""
import json
import re
import tempfile
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_investigate as bi

import _frozen_corpus as frozen  # noqa: E402  (tests/_frozen_corpus.py)

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DURATION = 6928.0
REGION = (3200.0, 4000.0)

pytestmark = pytest.mark.skipif(
    not (TIMELINE.is_file() and MANIFEST.is_file()),
    reason="the saved BB9 timeline and frame manifest are not present")

# The interval's own set openings, read from the recovery: marks are planted on and around these.
SET_OPENINGS = [3247.243829, 3728.725761]
SET_CLOSES = [3543.539814, 3993.989790]


def plan(marks=(), region=REGION, **kw):
    """`investigate-games --dry-run`, called the way the CLI calls it.

    Marks go through as a file, because that is the only form the public entry accepts -- `_load_marks`
    reads a path and tolerates either a bare list or a `{"matches": [...]}` object. Handing it a list in a
    test would be testing a shape the product cannot produce.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"matches": list(marks)}, handle)
        marks_path = handle.name
    try:
        return bi.plan_saved_investigation(str(TIMELINE), str(MANIFEST), DURATION,
                                           marks=marks_path or None, region=region, step=2.0,
                                           max_requests=kw.pop("max_requests", 4), **kw)
    finally:
        Path(marks_path).unlink(missing_ok=True)


def recovery(result):
    """Everything a marking could corrupt, and nothing it could plausibly fix."""
    return [(s["start"], s["end"], s["status"], s["membership"], s["games"],
             json.dumps(s["deciding_wins"], sort_keys=True), s["set_winner"], s["concluded_by"],
             s["basis"]) for s in result["sets_before"]]


def questions(result):
    return sorted((r["start"], r["end"], tuple(sorted(r["kinds"]))) for r in result["regions"])


# ---------- markings cannot move a recovery ----------

def test_shifted_duplicated_missing_and_absent_marks_recover_identical_sets():
    """The same tape, seven different markings, one recovery.

    The shifted cases are not hypothetical: markings on this recording were observed up to tens of seconds
    off the openings they named, one whole match was never marked, and a mark copied twice is what a
    re-click looks like. If any part of a boundary came from a mark, the recovered sets would depend on who
    was holding the mouse -- which is the failure this pair of tests exists to keep impossible.
    """
    variants = [[], [SET_OPENINGS[0]], [SET_OPENINGS[0] - 45.0], [SET_OPENINGS[0] + 45.0],
                [SET_OPENINGS[0], SET_OPENINGS[0], SET_OPENINGS[0]],
                [SET_OPENINGS[0], SET_OPENINGS[1]], [SET_CLOSES[0]],
                [SET_OPENINGS[1] + 11_000.0], [-300.0]]
    truth = None
    for marks in variants:
        result = plan(marks=marks)
        now = recovery(result)
        if truth is None:
            truth = now
            assert now, "the region recovered no candidates at all, so the comparison below is vacuous"
            assert any(entry[7] for entry in now), "no candidate recorded a boundary, so nothing is tested"
        assert now == truth, "marks=%s changed the recovery" % (marks,)


def test_a_mark_dropped_mid_play_neither_splits_nor_merges_a_candidate():
    """A mark landing inside a game is the worst case for anything that treats marks as boundaries.

    The mark is not wrong about the operator's intent, and the recovery is not wrong about the tape; the
    grouping has to be unaffected by which of the two the tool decides to believe.
    """
    middle = (SET_OPENINGS[0] + SET_CLOSES[0]) / 2.0
    unmarked, marked = plan(), plan(marks=[middle])
    assert recovery(unmarked) == recovery(marked)
    inside = [entry for entry in recovery(marked) if entry[0] <= middle <= entry[1]]
    assert inside, "the mark was not actually inside a recovered candidate"


def test_marks_decide_only_which_question_is_asked_first():
    """Priority is the whole of a mark's authority, so that is what is pinned here.

    With the budget wide enough to reach every question, marks change the *order* and nothing else: same
    regions, same spans, same reasons. That is the assertion that matters, and it needs an uncapped plan --
    under a tight one a mark legitimately changes which questions are reached, because reaching them is what
    priority is for. Conflating the two is how this test first failed, and both halves are worth keeping.
    """
    wide = {"max_requests": 200}
    unmarked, marked = plan(**wide), plan(marks=[SET_CLOSES[1] - 8.0], **wide)
    assert questions(unmarked) == questions(marked)
    assert not any(r["mark_near"] for r in unmarked["regions"])
    assert any(r["mark_near"] for r in marked["regions"])
    assert marked["regions"][0]["mark_near"] is True
    assert [r["start"] for r in marked["regions"]] != [r["start"] for r in unmarked["regions"]]


def test_a_tight_budget_reorders_work_and_defers_the_rest_by_name():
    """Under a cap a mark may change what is reached, but never what the recording still owes.

    The unanswered remainder has to be listed as deferred, not dropped: a plan that quietly skipped
    questions would be indistinguishable from a plan that answered them, and the whole point of marking a
    priority is to spend a finite allowance on the operator's best guess about where it matters.
    """
    tight = plan(max_requests=1)
    # A cap now lands on whichever route is scheduled first, so the deferrals are read across routes
    # rather than from the state region list alone: what has to survive is that every question the cap
    # could not fund is still named, on the route that owes it.
    assert tight["requests_planned"] == 1
    deferred = [entry for route in tight["dispatch"]["routes"] for entry in route["deferred"]]
    assert deferred, "a capped plan deferred nothing, so the cap is not being exercised"
    assert {entry.get("route") for entry in deferred} - {None}, \
        "a deferral has to say which executor owes it"
    wide = plan(max_requests=200)
    reached = {kind for region in tight["regions"] for kind in region["kinds"]}
    deferred = {kind for region in (tight["deferred_regions"] or []) for kind in region.get("kinds") or ()}
    everything = {kind for region in wide["regions"] for kind in region["kinds"]}
    # Nothing the uncapped plan could ask is lost by capping this one: it is reached or it is deferred.
    assert reached | deferred >= everything, (everything - reached - deferred)
    assert reached <= everything and deferred <= everything


def test_a_mark_past_the_end_of_the_tape_creates_no_work_and_no_boundary():
    """A late mark is a stale note, not a request for frames that were never recorded.

    Regions are clamped to the recording and to the caller's window, and the recovery is identical with the
    mark present. Honouring it would mean inventing either the footage or a set boundary.
    """
    beyond = plan(marks=[DURATION + 5_000.0, -400.0, SET_OPENINGS[0] + 10_000.0])
    plain = plan()
    assert recovery(beyond) == recovery(plain)
    assert questions(beyond) == questions(plain)
    for region in beyond["regions"]:
        assert 0.0 <= region["start"] <= region["end"] <= DURATION


# ---------- accounting, edges and deferral ----------

def test_every_recovered_game_is_accounted_for_once_under_any_marking(tmp_path):
    """Coverage as a property of the partition, not a figure checked afterwards.

    The failure this guards is a set layer that quietly drops a game it cannot place: the candidate counts
    would still look tidy and the dropped game would take its conclusion with it.
    """
    for marks in ([], SET_OPENINGS, [SET_OPENINGS[0] - 12.0, SET_OPENINGS[0] + 12.0]):
        result = plan(marks=marks)
        accounting = result["tracker_before"]["sets"]
        assert accounting["games_covered"] == result["tracker_before"]["games"], accounting
        covered = sum(s["games"] for s in result["sets_before"])
        assert covered == accounting["games_covered"]
        for record in result["sets_before"]:
            if record["status"] != "complete":
                assert record["missing_evidence"], record["basis"]


def test_an_ambiguous_cut_survives_to_the_caller_as_a_named_alternative():
    """Absence of a preferred cue leaves two readings, and the public plan has to carry both.

    The primary cut is the one that invents nothing; the alternative is the reading in which these games were
    one set. If only the primary reaches the caller, an ambiguity silently becomes a conclusion -- which is
    the same overreach as a borrowed cue, in the opposite direction. The BB9 interval used here has both
    kinds of edge, so it is the fixture for the claim.
    """
    result = plan(max_requests=200)
    ambiguous = result["tracker_before"]["sets"]["ambiguous_links_kept_as_alternatives"]
    assert ambiguous >= 1, "the region has no ambiguous cut left to test"
    carried = [entry for record in result["sets_before"] for entry in record["alternative_groupings"]]
    assert len(carried) >= ambiguous, "%d ambiguous cuts, %d alternatives carried" % (
        ambiguous, len(carried))
    for entry in carried:
        assert entry["reading"] and entry["why_not_primary"], entry
        assert entry["would_merge"], entry
        # The alternative is not a shrug: it names the observation that would settle which reading is right.
        assert any(question.get("would_resolve") or question.get("because")
                   for question in entry["questions"]), entry
    assert any(record["concluded_by"] == [] for record in result["sets_before"]), \
        "every candidate concluded, so the uncued case is not exercised here"


def test_this_path_bounds_sets_and_cannot_score_them():
    """What the path can and cannot conclude, asserted rather than assumed by a caller.

    With no crop corpus named, `investigate-games` plans from the classified timeline alone, so every
    candidate here has its boundaries and none has a winner. The absence is a property of the evidence base
    rather than a finding about the recording -- and the section below proves it, by loading the corpus and
    watching the same candidates gain wins without moving a boundary. Stating both in tests is what stops a
    future reader treating `set_winner: null` as "nobody won".
    """
    result = plan()
    for record in result["sets_before"]:
        assert record["set_winner"] is None
        assert set(record["deciding_wins"].values()) <= {0}, record["deciding_wins"]
    assert result["tracker_before"]["sets"]["complete"] == 0
    assert any("winner" in text.lower()
               for record in result["sets_before"] for text in record["missing_evidence"])
    assert result["what_the_pilot_cannot_prove"], "the plan stopped declaring its own limits"


def test_a_region_edge_is_recorded_as_an_edge_and_not_as_the_end_of_a_set():
    """Analysis-window truncation, on real footage.

    The last candidate runs to the bound the caller chose. The walk finds frames there and none of them
    settles the bound, so the record says which kind of unknown this is instead of concluding -- and the
    scope block says the numbers describe the region rather than the recording.
    """
    result = plan(region=(SET_OPENINGS[1] - 60.0, SET_CLOSES[1] + 6.0))
    last = result["sets_before"][-1]
    end = (last["edges"] or {})["end"]
    assert last["status"] == "partial" and last["concluded_by"] == []
    assert end["state"] in ("nothing_observed_after", "sampled_but_unsettled",
                            "play_continues_past_this_bound"), end
    assert end["evidence_against"], end
    assert result["tracker_scope"]["region"] == [SET_OPENINGS[1] - 60.0, SET_CLOSES[1] + 6.0]


def test_uncertainty_is_deferred_by_name_with_the_evidence_that_would_resolve_it(tmp_path):
    """Automatic investigation, and honest deferral when the budget cannot cover it.

    Every open question names its kind and what would settle it; a region pushed out by the request ceiling
    is reported as deferred rather than quietly dropped, so a caller can tell "not asked yet" from
    "answered". A plan that silently skipped work would look exactly like a complete one.
    """
    tight = plan(max_requests=1)
    assert tight["requests_planned"] == 1, "the plan produced no questions at all"
    deferred = [entry for route in tight["dispatch"]["routes"] for entry in route["deferred"]]
    assert deferred, "a capped plan deferred nothing, so the cap is not being exercised"
    assert all(entry.get("route") or entry.get("kinds") or entry.get("frame") or entry.get("at")
               for entry in deferred), "a deferral has to name the work it leaves"
    for region in tight["regions"]:
        assert region["kinds"], region
    # The queue's own wording survives to the caller: each kind carries the observation that would close it.
    unresolved_kinds = tight["unresolved_kinds"]
    assert unresolved_kinds and all(kinds >= 1 for kinds in unresolved_kinds.values())


def test_the_quoted_concludability_figure_is_the_shipped_walks_answer():
    """The readiness table in the plan doc is a measurement, so it is re-measured here.

    `docs/set-recovery-stage5-plan.md` quotes 12 conclusions settled by a station change out of 35, 23 by
    live play, 0 unresolved. Those numbers came from a script, and a script that quietly diverges from the
    shipped walk is how a document ends up describing a rule the code no longer implements -- which is
    precisely what the hand-rolled version of this measure did before locality. So the test recomputes them
    with `_edge_events` and compares against the artifact the script writes.
    """
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import measure_set_concludability as msc

    measured = msc.measure(str(TIMELINE))
    settled = measured["settled"]
    assert measured["game_conclusions"] == 35, measured["game_conclusions"]
    assert settled["cue"]["count"] == 12 and settled["live"]["count"] == 23
    assert settled["nothing"]["count"] == 0
    assert abs(settled["cue"]["share"] - 12 / 35.0) < 1e-3
    assert measured["inference_sent"] is False and measured["attempts_charged_now"] == 0


def test_the_recovered_boundaries_match_the_evidence_the_report_quotes():
    """Cross-check against the set report on the same interval, so the two cannot drift apart silently.

    ``scripts/report_saved_header.py`` is now the second reader of one evidence base rather than a
    competing one: the command merges the same crop answers through the same function, so their boundaries
    *and* their scores have to agree. A divergence there would mean two implementations of one rule, which
    is what the shared merge in :mod:`tekken_vod_helper.boundary_evidence` exists to prevent.
    """
    report_path = ROOT / "build/work/bb9/set-end2end-v1/recovery-stage-b-no-format.json"
    if not report_path.is_file():
        pytest.skip("the set report for this interval has not been generated")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    from_report = sorted((round(c["start"], 3), round(c["end"], 3)) for c in report["candidates"])
    # The report on disk was written before the clock-wording repair (Step 0 of
    # docs/set-recovery-stage5-plan.md), which split rounds where prose stated a clock the old pattern read
    # only after its word. The artifact is a record of what that parser recovered, so it is not rewritten to
    # make this pass. Reproducing the old parser is one guarded pattern -- the backwards wording is consulted
    # only when the forward one found nothing -- and under it the two readers still have to agree exactly,
    # which is the drift this test exists to catch.
    backwards = bg._CLOCK_BACKWARDS_RE
    bg._CLOCK_BACKWARDS_RE = re.compile(r"(?!)")
    try:
        from_path = sorted((round(c["start"], 3), round(c["end"], 3)) for c in plan()["sets_before"])
    finally:
        bg._CLOCK_BACKWARDS_RE = backwards
    assert from_path == from_report, (from_path, from_report)
    repaired = sorted((round(c["start"], 3), round(c["end"], 3)) for c in plan()["sets_before"])
    assert [c for c in repaired if c not in from_report] == [(3723.72, 3993.99)], repaired
    assert (3728.725, 3993.99) in from_report, from_report
    concluded_report = sorted(round(c["start"], 3) for c in report["candidates"]
                              if c["concluded_by"])
    concluded_path = sorted(round(s["start"], 3) for s in plan()["sets_before"] if s["concluded_by"])
    assert concluded_path == concluded_report


# ---------- the same markings, with the validated observations loaded ----------

def integrated(marks=(), region=REGION, crop_runs=frozen.crop_runs(), max_requests=200, **kw):
    """``investigate-games --dry-run`` with everything this recording has already answered loaded.

    This is the public path the state-only comparison above cannot be: the saved crop readings are merged
    through the canonical join, the saved band answers are read for identity, and no target is supplied, so
    a candidate that closes here closes because the recording shows it ending.
    """
    from tekken_vod_helper import boundary_header as bh
    runs = [ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30",
            ROOT / "build/work/bb9/set-end2end-v1/attribution-run",
            ROOT / "build/work/bb9/set-end2end-v1/band-run"]
    header = bh.saved_readings(bh.response_directories(",".join(str(r) for r in runs)))
    return plan(marks=marks, region=region, crop_runs=crop_runs, header=header,
                games_per_set=None, max_requests=max_requests, **kw)


def scored_recovery(result):
    """The four claims a marking could corrupt, now including who the recording says won."""
    return [(c["start"], c["end"], c["status"], c["membership"], c["games"],
             json.dumps(c["game_wins"], sort_keys=True),
             json.dumps(c["deciding_wins"], sort_keys=True), c["deciding_wins_unit"], c["set_winner"],
             tuple(c["concluded_by"] or ()), c["footage_complete"], c["clip_end_at"])
            for c in result["recovery"]["candidates"]]


MARK_FAULTS = {
    "unmarked": [],
    "on the opening": [SET_OPENINGS[0], SET_OPENINGS[1]],
    "shifted early (-45s)": [m - 45.0 for m in SET_OPENINGS],
    "shifted late (+45s)": [m + 45.0 for m in SET_OPENINGS],
    "duplicated x3": [m for m in SET_OPENINGS for _ in range(3)],
    "missing one match": [SET_OPENINGS[0]],
    "mid-set": [(SET_OPENINGS[0] + SET_CLOSES[0]) / 2.0],
    "before the tape": [-300.0, SET_OPENINGS[0] - 90.0],
    "after the tape": [SET_CLOSES[1] + 8.0, DURATION + 5_000.0],
    "reversed order": list(reversed(SET_OPENINGS)),
}


def test_the_integrated_command_reaches_one_result_under_every_marking():
    """Marks are a statement about where a human was looking, and the tape is not that.

    Ten markings -- none, faithful, forty-five seconds early, forty-five seconds late, a match never
    marked, a mark copied three times, one dropped mid-set, one before the tape and one past its end --
    all reach the same scored recovery on this interval: one complete candidate and one partial, with the
    wins the crop readings validated. Under the state-only path the equivalent test could only promise
    that nothing *changed*; here the thing that does not change is a result.
    """
    truth = None
    for label, marks in MARK_FAULTS.items():
        result = integrated(marks=marks)
        now = scored_recovery(result)
        if truth is None:
            truth = now
            complete = [c for c in now if c[2] == "complete"]
            assert len(complete) == 1, "the clean run recovered no complete set: %s" % (now,)
            assert any(any(json.loads(c[5]).values()) for c in complete), \
                "the complete candidate credits no win, so 'complete' means nothing here"
            assert any(c[2] == "partial" for c in now), "no partial survived to be the original"
        assert now == truth, "marks=%s (%s) changed the recovery" % (marks, label)


def test_marks_still_move_only_the_questions_under_the_integrated_path():
    """The priority half of the promise survives the new evidence base: same questions, different order."""
    wide = dict(max_requests=200)
    unmarked, marked = integrated(**wide), integrated(marks=[SET_CLOSES[1] - 8.0], **wide)
    # The *set* of questions is the invariant; their order is what a mark is allowed to change.
    assert (sorted((r["start"], r["end"], tuple(sorted(r["kinds"]))) for r in unmarked["regions"])
            == sorted((r["start"], r["end"], tuple(sorted(r["kinds"]))) for r in marked["regions"]))
    assert not any(r["mark_near"] for r in unmarked["regions"])
    assert marked["regions"][0]["mark_near"] is True
    assert [r["start"] for r in marked["regions"]] != [r["start"] for r in unmarked["regions"]]


def test_loading_the_corpus_shrinks_the_work_without_touching_the_conclusion():
    """What the saved answers bought, in the two numbers that matter: attempts and the same recovery.

    The crop corpus answers eighteen of this interval's twenty-five questions before anything is sent, and
    the boundaries and scores are identical either way -- because the readings were always about this
    recording, and loading them only lets the tool see what it could not see before.
    """
    merged, state_only = integrated(), integrated(crop_runs=None)
    # Per route, because that is where the corpus's value is visible: 45 of this interval's decision
    # frames already have crop readings, so not one coarse frame is re-priced. The six requests that
    # remain are the corroboration claim's interiors -- the cached seconds beside a single-reading
    # deciding frame, which nothing on disk has ever sampled. "Settled window, empty crop bill" was
    # written before that claim existed; what reuse guarantees, then and now, is that no measurement
    # this corpus holds is asked a second time, and that every remaining request names a frame nobody
    # looked at. With no corpus the same window owes 45 crop requests plus 10 state batches, and no
    # interiors: with nothing answered the coarse triple is not exhausted, so no window has earned a
    # second look.
    assert merged["requests_planned"] == 14 and merged["pip_requests_planned"] == 6
    routes = {entry["route"]: entry for entry in state_only["dispatch"]["routes"]}
    assert routes["pip_crops"]["requests_planned"] == 45
    assert routes["state"]["requests_planned"] == 10
    assert state_only["requests_planned"] == 55
    # Boundaries and game counts only: the *status* is what the corpus changes, which is the whole point.
    assert ([(c[0], c[1], c[4]) for c in scored_recovery(merged)]
            == [(c[0], c[1], c[4]) for c in scored_recovery(state_only)]), \
        "adding readings moved a boundary, which is not what a merge does"
    assert [c[2] for c in scored_recovery(state_only)] == ["partial", "partial"]
    assert [c[2] for c in scored_recovery(merged)] == ["complete", "partial"]
    assert state_only["recovery"]["games_with_a_winner"] == 0
    assert merged["recovery"]["games_with_a_winner"] == 3


def test_the_original_partial_names_what_it_is_missing_and_the_padding_follows_the_conclusion():
    """The partial is a result with a reason, and the clip is derived from the evidence, not from a mark.

    ``clip_end`` is the last conclusion plus configured padding, so a mark planted anywhere on the tape has
    to leave it where the footage put it. The partial's ``missing_evidence`` has to say which claim is
    unmet, because "not complete" without a reason is indistinguishable from "nothing found".
    """
    clean, planted = integrated(), integrated(marks=[SET_OPENINGS[0] + 3.0, SET_CLOSES[1] - 2.0])
    assert scored_recovery(clean) == scored_recovery(planted)
    partial = [c for c in clean["recovery"]["candidates"] if c["status"] != "complete"]
    assert partial and all(c["missing_evidence"] for c in partial)
    for candidate in clean["recovery"]["candidates"]:
        assert candidate["clip_end_at"] and candidate["clip_end_at"] >= candidate["end"]
        assert "padding" in (candidate["clip_end_basis"] or "")


def test_an_exhausted_allowance_defers_questions_by_name_under_the_integrated_path():
    """Budget exhaustion is a planning outcome, so it is reported as one and never as a conclusion.

    The cap changes which questions get asked. It does not change the recovery, and it does not silently
    drop the questions it could not reach: each deferred region is listed with the kinds it still owes.
    """
    tight = integrated(max_requests=1)
    wide = integrated(max_requests=200)
    assert tight["requests_planned"] <= 1 or tight["regions_partially_planned"]
    assert tight["regions_deferred_for_budget"] or tight["deferred_regions"]
    reached = {kind for region in tight["regions"] for kind in region["kinds"]}
    deferred = {kind for region in (tight["deferred_regions"] or []) for kind in region.get("kinds") or ()}
    everything = {kind for region in wide["regions"] for kind in region["kinds"]}
    assert reached | deferred == everything, everything - reached - deferred
    assert scored_recovery(tight) == scored_recovery(wide)


def test_every_game_the_integrated_recovery_counts_is_inside_exactly_one_candidate():
    """Coverage as a property of the partition, with the crop readings in the base.

    A candidate count is only worth what covers it: every game the tracker found has to sit inside one
    candidate, and a game nobody could place has to be visible as a gap rather than missing.
    """
    for marks in ([], [SET_OPENINGS[0] - 45.0], [m for m in SET_OPENINGS for _ in range(3)]):
        result = integrated(marks=marks)
        accounting = result["tracker_before"]["sets"]
        assert accounting["games_covered"] == result["tracker_before"]["games"], accounting
        assert sum(c["games"] for c in result["recovery"]["candidates"]) == \
            accounting["games_covered"]
        assert result["recovery"]["accounting_with_band"]["sets"] == accounting
