"""Stage 4 rehearsal: the broader consecutive-game trial runs, resumes and defers without inference.

The trial under preparation is ``build/work/bb9/consecutive-games-v3``: the whole-minute interval
``[1200, 2200)`` opened by a documented time rule after the newest crop reading on disk, 17 decision
windows, 51 frames, 102 images, and nothing inside it ever sent. Only the transport is replaced here, so
request identity, the attempt allowance, the retry loop, per-group persistence, the merge and the tracker
all run for real and cost nothing.

These tests are a rehearsal of the machinery and never evidence about the model: the answers come from
the frozen evaluator labels, so a clean score here is arithmetic, not perception. What would be news --
and what the approved run has to establish -- is whether *the model's* readings recover the four games
those labels describe.

    env PYTHONPATH=. .venv/bin/python -m pytest tests/test_consecutive_games_v3_trial.py -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

from _consecutive_trial_harness import TrialRehearsal, load

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tekken_vod_helper import boundary_games as bg                    # noqa: E402
from tekken_vod_helper import boundary_pips as bp                     # noqa: E402
from tekken_vod_helper import boundary_windows as bw                  # noqa: E402

TRIAL = ROOT / "build/work/bb9/consecutive-games-v3"
TRIAL_MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
SELECT_SCREENS = {"stage_select", "character_select"}

pytestmark = pytest.mark.skipif(
    not (TRIAL / "selected-frames.json").is_file(),
    reason="the prepared trial lives under build/ and is absent on a fresh clone")


@pytest.fixture(scope="module")
def trial():
    return TrialRehearsal(TRIAL)


def clock_only_stems(trial):
    return {Path(e["frame"]).stem for e in trial.plan["requested"]
            if e["window"] in {w["sequence"] for w in trial.plan["windows"]
                               if w["end_basis"] == "last_readable_clock"}}


def tracked(trial, run_dir):
    """The canonical merge and tracker for this trial, exactly as production runs them."""
    import validate_game_one as vg
    rows, _ = vg.merged_rows(vg.crop_observations([Path(run_dir)]), trial.interval)
    return rows, bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)


# ---------------------------------------------------------------- the rule, not the result

def test_the_interval_is_the_documented_time_rule_applied_to_the_recorded_reading(trial):
    """The plan's own recorded newest reading, verified against the cache, then the rule applied to it.

    The rule reads one number off the saved evidence -- the newest crop reading anywhere -- and adds a span
    fixed in advance. It cannot see a winner, a pip value, a mark, a name or a score, and it keeps stage
    and character selects inside the trial because a select proves nothing about where a set begins.

    This used to re-derive that newest reading by discovering whatever crop runs happened to be on disk.
    That made a historical claim about 2026-10-30 depend on the live corpus, and every batch since has
    moved it: an executed batch added readings newer than 1,175 s and the re-derivation stopped naming the
    interval the trial actually used. Re-selecting a window, or listing runs until the number fitted again
    (the pre-trial runs I could name hold 72 readings, not the 59 the trial recorded -- so no honest
    allowlist reproduces it), would have been the same mistake in a tidier costume. What is checkable
    forever is narrower and real: the reading the plan recorded belongs to a frame that exists and draws
    that instant, and the rule applied to that reading is the interval the plan wrote down.
    """
    selection = trial.plan["selection"]
    recorded = selection["newest_saved_reading"]
    newest = float(recorded["timestamp"])
    assert selection["reads"].startswith("saved crop readings' timestamps and nothing else")

    # The recorded number refers to a real cache frame at that instant, not to a number in a file.
    manifest = json.loads(TRIAL_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["frames"] if isinstance(manifest, dict) else manifest
    name = Path(str(recorded["frame"])).name
    matches = [e for e in entries if Path(str(e.get("path", ""))).name == name]
    assert matches, "the newest reading names a frame the cache does not hold: %s" % name
    assert abs(float(matches[0]["timestamp"]) - newest) <= 0.05, \
        "%s draws %.6f, not the recorded %.6f" % (name, float(matches[0]["timestamp"]), newest)

    start = 60.0 * (int(newest // 60) + 1)
    assert (start, start + 1000.0) == (trial.interval[0], trial.interval[1]) == (1200.0, 2200.0)
    rule = trial.plan["interval"]["rule"]
    # the rule *names* the outcome fields only to disclaim them; what it must not do is use them, and
    # the only value it quotes as an input is the reading it opened after
    assert "not marks, names, scores, winners or outcomes" in rule
    assert "1175.174" in rule and "1000 s" in rule
    assert re.search(r"opened after|Opened at the first whole minute", rule)




def bp_default_runs():
    """Every crop run on disk, so the rule sees the whole body of prior readings."""
    import evaluate_consecutive_game_trial as ev
    return [str(ROOT / name) for name in ev.crop_run_dirs()]


def test_a_source_framing_answer_cannot_join_the_trial_timeline():
    """A master crop of an answered instant is a second row at that instant, and rows are read as motion.

    The executed smoke run put crop answers at 1372.371, 1708.707, 1800.799 and 2133.131 s -- four
    instants the trial had already read from the 640x360 cache -- into a directory the reuse discovery
    finds automatically. Merged naively, the replay gained four duplicate instants (28 appended rows
    where 24 belong), and any future disagreement between the two framings would show up as the pips
    moving. Framing separation is now enforced in the reader, and reported rather than implied.
    """
    import evaluate_consecutive_game_trial as ev
    from tekken_vod_helper import boundary_pips as bp

    assert any("source-crop-smoke" in d for d in bp_default_runs()), \
        "the smoke run must still be discoverable, or this test proves nothing"
    executed = ev.Trial(str(TRIAL))          # the real trial, whose interval the merge must respect
    rows, joined, observations = ev.tracking_rows(executed,
                                                  ev.crop_run_dirs() + ev.trial_run_dirs(executed))
    instants = [round(float(r["timestamp"]), 3) for r in rows]
    duplicates = sorted({t for t in instants if instants.count(t) > 1})
    assert duplicates == [], "two rows share an instant, which the tracker reads as the dots moving"
    assert joined["skipped_other_framing"] >= 4, \
        "the source answers were not refused, they were silently merged: %s" % joined

    cache_group = {"arm": "crops", "frame_size": [640, 360], "observations": []}
    master_group = {"arm": "crops", "frame_size": [2560, 1440], "observations": []}
    legacy_group = {"arm": "crops", "observations": []}      # predates the field entirely
    assert bp.is_cache_framing(cache_group) and not bp.is_cache_framing(master_group)
    assert bp.is_cache_framing(legacy_group), "an old group file is a cache reading by construction"


def test_the_trial_targets_four_unread_games_and_keeps_the_transition_screens_inside(trial):
    """Nothing inside had been sent when the plan was made, and selects stay in the interval.

    The rejected stage-2 interval was cut partly because a stage select sat inside it. That reasoning
    was withdrawn -- a select can occur within a set -- so a trial that dodges every select is testing
    a tidier recording than the one that exists.
    """
    assert trial.plan["reused_readings"] == [] and trial.plan["requests_planned"] == 51
    assert trial.plan["requests_deferred"] == 0 and trial.plan["budget_fits"] is True
    rows = load(TIMELINE)
    inside = {r["state"] for r in rows if trial.interval[0] <= r.get("timestamp", 0) <
              trial.interval[1]}
    assert SELECT_SCREENS <= inside, "this interval is expected to contain both select screens"
    # Foreign readings: none. Everything inside the interval belongs to this trial's own 51 frames,
    # which is the durable form of "nothing was reused" -- it stays true after the run answers them.
    own = {Path(e["frame"]).name for e in trial.plan["requested"]}
    foreign = [(Path(k).name, v.get("timestamp")) for k, v in
               bw.load_saved_readings(bp_default_runs()).items()
               if trial.interval[0] <= (v.get("timestamp") or 0) < trial.interval[1]
               and Path(k).name not in own]
    assert not foreign, "a reading from another experiment fell inside the trial: %s" % foreign[:3]
    # ("Nothing was reused when this plan was written" is asserted above from the plan's own
    # ``reused_readings: []``. Re-checking it by scanning the corpus states something different -- that no
    # experiment has since answered anything inside 1200-2200 -- and that is already false three batches
    # later. The historical claim belongs to the artifact, not to whatever is on disk today.)
    # A first-to-3 game runs 3-5 rounds, so 17 windows covers 3.4-5.7 games: the target band, arrived
    # at by a span in seconds. How many games the pip rows actually conclude is the trial's question.
    windows = len(trial.plan["windows"])
    assert windows == 17
    assert 3.0 <= windows / 5.0 and windows / 3.0 <= 6.0


def test_every_requested_frame_is_the_manifests_own_identity(trial):
    """Ids, paths and timestamps come from the frame cache, not from arithmetic on a second.

    The stage-3 evaluator rebuilt frame numbers as ``timestamp / 1.000986`` to choose follow-up frames.
    That turns a lookup into a rounding guess, and a frame named by a guess cannot be defended later.
    """
    index = bw.FrameIndex.load(trial.plan["manifest"]["path"])
    by_id = {f["id"]: f for f in index.frames}
    for entry in trial.plan["requested"]:
        frame = by_id[entry["id"]]
        assert frame["path"] == entry["frame"]
        assert frame["timestamp"] == pytest.approx(entry["timestamp"], abs=1e-9), entry["id"]
        assert (ROOT / entry["frame"]).is_file(), entry["frame"]
    import evaluate_consecutive_game_trial as ev
    assert not hasattr(ev, "index_for"), "the evaluator must not convert seconds into frame numbers"


def test_the_windows_are_the_canonical_anchors_and_every_one_is_qualified(trial):
    bases = {}
    for window in trial.plan["windows"]:
        assert window["end_basis"] in ("ko", "time_up", "last_readable_clock")
        bases[window["end_basis"]] = bases.get(window["end_basis"], 0) + 1
        if window["end_basis"] == "last_readable_clock":
            assert window["gaps"], "a clock-only ending must record why it is uncertain"
        assert trial.interval[0] <= window["opened_at"]
        assert window["end_at"] < trial.interval[1]
    # measured, and the reason the adaptive path is the normal path here rather than an edge case
    assert bases == {"ko": 7, "last_readable_clock": 10}, bases


# ---------------------------------------------------------------- labels: evaluator-only, frozen first

def test_labels_stay_out_of_everything_the_run_reads(trial):
    """Truth lives in one file the scorer alone opens; selection, prompts and requests carry nulls."""
    runtime = load(trial.runtime)
    assert all(f["pips"] == {"LEFT": None, "RIGHT": None} for f in runtime["frames"])
    assert all(f.get("row_drawn") is None for f in runtime["frames"])
    assert runtime["provenance"]["expected_values"].startswith("left null on purpose")
    for name in ("plan-crops.json", "selected-frames.json", "annotations-window.json"):
        assert "labels-eval-frozen" not in (TRIAL / name).read_text(encoding="utf-8"), name
    probe_plan = (TRIAL / "plan-crops.json").read_text(encoding="utf-8")
    for frame in trial.labels["frames"]:
        if not frame["graded"]:
            continue
        for side, value in frame["pips"].items():
            assert not re.search(r'"%s"\s*:\s*%d\b' % (side, value), probe_plan), \
                "an expected count reached a file the request is built from"
    assert [f["frame"] for f in trial.labels["frames"]] == [e["frame"] for e in trial.plan["requested"]]


def test_planning_the_trial_never_opens_the_label_file(trial, tmp_path):
    """Not merely absent from the output: the selector does not read it, so it cannot be shaped by it."""
    opened = []
    real = Path.read_text

    def spy(self, *args, **kwargs):
        if "labels-eval-frozen" in str(self):
            opened.append(str(self))
        return real(self, *args, **kwargs)

    Path.read_text = spy
    try:
        rows = load(TIMELINE)
        plan = bw.plan_interval(bg.build_observations(rows), {},
                                bw.FrameIndex.load(trial.plan["manifest"]["path"]),
                                start_at=1200.0, end_at=2200.0, max_new_requests=51)
        # rendered into the scratch dir: the frozen trial's own crops are an artifact, not a test fixture
        bp.plan_requests(bp.build_records(load(trial.runtime)["frames"], tmp_path / "crops"),
                         "crops", max_requests=51)
    finally:
        Path.read_text = real
    assert opened == [], "the label file was opened while planning: %s" % opened
    assert plan["requests_planned"] == 51
    # and against the corpus as it stands now -- this trial's own 51 readings included -- reuse is
    # decided by whether a frame was read, not by a timestamp: nothing already answered is billed twice.
    after = bw.plan_interval(bg.build_observations(rows), bw.load_saved_readings(bp_default_runs()),
                             bw.FrameIndex.load(trial.plan["manifest"]["path"]),
                             start_at=1200.0, end_at=2200.0, max_new_requests=51)
    planned = {Path(entry["frame"]).name for entry in after["requested"]}
    already = {Path(entry["frame"]).name for entry in trial.plan["requested"]}
    assert not (planned & already), \
        "a frame this trial already answered was billed again: %s" % sorted(planned & already)
    assert len(after["reused_readings"]) >= 47, \
        "reuse regressed: only %d of the 51 saved readings were taken as answers" % len(
            after["reused_readings"])
    # Four new requests, not zero, and the cause is a correction rather than a defect: the round-end
    # family now reads "Round-ending PERFECT banner" at 1371.370 s and "PERFECT round-end in-game banner"
    # at 2039.037 s as the conclusion events they describe, and every conclusion opens a pip window at
    # +0/+2/+4 s that this trial never sampled. Pinned by name so the next change to that arithmetic is
    # a decision somebody has to make out loud.
    assert sorted(planned) == ["frame-1371.jpg", "frame-1373.jpg", "frame-1375.jpg", "frame-2038.jpg"], \
        "the residual questions changed shape: %s" % sorted(planned)


def test_withheld_frames_are_unknown_truth_never_absent_and_never_zero(trial):
    graded = [f for f in trial.labels["frames"] if f["graded"]]
    withheld = [f for f in trial.labels["frames"] if not f["graded"]]
    assert len(graded) == 34 and len(withheld) == 17, \
        "the freeze is quoted in the plan and the doc; changing it means a new version and a new freeze"
    for frame in withheld:
        assert frame["row_drawn"] is None and frame["pips"] == {"LEFT": None, "RIGHT": None}
        assert re.search(r"flash|close-up|off-frame|not in frame|black|no row|no HUD|fencework",
                         frame["basis"], re.I), \
            "a withheld frame has to say what made it unreadable: %s" % frame["frame"]
    for frame in graded:
        assert frame["row_drawn"] in (True, False)
        counts = [frame["pips"]["LEFT"], frame["pips"]["RIGHT"]]
        assert all(value is None or 0 <= value <= 3 for value in counts)
        if not any(counts):
            assert frame["row_drawn"] is True, "an all-empty readable row is a real 0-0"
    assert sum(1 for f in graded if f["pips"] == {"LEFT": 0, "RIGHT": 0}) >= 1, \
        "the trial needs a drawn-and-empty row, which is not an abstention"
    assert sum(1 for f in graded if 3 in (f["pips"]["LEFT"], f["pips"]["RIGHT"])) == 4, \
        "one closing row per game: these are the frames that name winners"


def test_the_freeze_records_what_was_read_before_and_what_was_never_chosen(trial):
    disclosure = trial.labels["independence_disclosure"]
    assert disclosure["model_under_test"] == "qwen3.8-flash-next"
    assert "same family" in disclosure["limitation"]
    assert any("no model answer exists" in line for line in disclosure["prior_exposure"])
    assert trial.labels["read_from"]["crop_digest_of_the_102_pngs_the_trial_sends"]
    assert trial.labels["version"].startswith("consecutive-games-v3-frozen")


def test_a_refreeze_may_move_prose_but_no_expectation(trial):
    """The one permitted edit to a freeze, and the proof that is all it was.

    Before anything is sent a summary line can still be wrong, and correcting it is not the same act
    as correcting a count after seeing an answer -- but the difference has to be demonstrable, not
    asserted. So every re-issue names the version it supersedes and carries a digest over exactly the
    claim each frame makes (frame, both counts, drawn state, graded flag), and those two digests match.
    """
    revision = trial.labels["revision"]
    assert trial.labels["version"] == "consecutive-games-v3-frozen-6"
    assert len(revision["superseded_sha256"]) == 64
    assert revision["truth_digest_before"] == revision["truth_digest_after"], \
        "a re-freeze moved an expectation, which needs a new run's approval, not an edit"
    # the chain reaches back to the original freeze, so no revision can be quietly dropped
    chain = [revision["supersedes"]] + [r["supersedes"] for r in revision["history"]]
    assert chain == ["consecutive-games-v3-frozen-5", "consecutive-games-v3-frozen-4",
                     "consecutive-games-v3-frozen-3", "consecutive-games-v3-frozen-2",
                     "consecutive-games-v3-frozen"], chain

    # ...and every link has to carry its own proof *as written on disk*. The -4 revision serialized the
    # file before filling in its own digest, so the field it existed to assert was null in the file
    # while passing in memory: a claim that cannot be read back is not a claim.
    proofs = [revision["truth_digest_after"]] + [r["truth_digest_after"] for r in revision["history"]]
    assert all(proof and proof == revision["truth_digest_before"] for proof in proofs), \
        "a revision lost its digest on disk: %s" % proofs

    def digest(frames):
        rows = [(f["frame"], f["pips"]["LEFT"], f["pips"]["RIGHT"], f["row_drawn"], f["graded"])
                for f in frames]
        return json.dumps(rows, sort_keys=True).encode("utf-8")

    assert __import__("hashlib").sha256(digest(trial.labels["frames"])).hexdigest() == \
        revision["truth_digest_after"], "the recomputed claim digest disagrees with the freeze"


def test_every_withheld_frame_says_which_side_was_unreadable_and_what_it_would_have_meant(trial):
    """A withheld frame is a claim about the crop, so it has to be checkable against the crop.

    Stage 2's review caught a basis that described the wrong half of a frame, and that is not harmless:
    the next reader decides from it whether to re-cut the label. So a basis that claims one side *was*
    readable has to name that side and say how wide the doubt is on the other. A frame where the whole
    band is blown out is a different case -- it claims nothing about a half, and must not be forced to.
    """
    claimed_a_half, whole_band = 0, 0
    for frame in trial.labels["frames"]:
        if frame["graded"]:
            continue
        basis = frame["basis"]
        # "the left group reads three dark rings" is a claim about a half; "no row to read" is not.
        if re.search(r"(left|right) group read", basis, re.I):
            claimed_a_half += 1
            assert re.search(r"left group|right group", basis, re.I), \
                "%s: a readable half has to be named by side" % frame["frame"]
            # say that the easy half is not being graded -- the wording varies, the promise may not
            assert re.search(r"withheld (whole|with the frame)", basis, re.I), \
                "%s: name the withholding, not just the unreadable side: %s" % (frame["frame"], basis)
            # what makes a withheld frame reviewable is *where* the light falls and how far the doubt
            # reaches: either name the slot, or say the resolution cannot fix it and bound the count by
            # the fill order instead. A basis that only says "unreadable" is neither checkable nor
            # falsifiable -- which is the mistake stage 2's review caught.
            assert re.search(r"slots?", basis, re.I), \
                "%s: say which slot the light covers: %s" % (frame["frame"], basis)
            assert (re.search(r"nearest|farthest|first|second|middle|third", basis, re.I)
                    or re.search(r"cannot fix which|cannot be separated|not separable", basis, re.I)), \
                "%s: name the buried slot or say it cannot be fixed: %s" % (frame["frame"], basis)
            # Either bound the remaining counts, or say plainly that the light and the pip cannot be
            # separated -- both are checkable, "unreadable" alone is not.
            assert re.search(r"\b\d or \d\b|\b\d to \d\b|not separable|cannot be separated", basis), \
                "%s: bound what the covered group could still have been: %s" % (frame["frame"], basis)
        elif re.search(r"both crops|in both|no HUD band|off-frame|not in frame|black|fencework|no row",
                       basis, re.I):
            whole_band += 1
        else:
            raise AssertionError("%s: a withheld frame must say what made it unreadable" % frame["frame"])
    assert claimed_a_half == 4 and whole_band == 13, (claimed_a_half, whole_band)
    assert claimed_a_half + whole_band == 17, "the withheld frames stop dividing as documented"


# ---------------------------------------------------------------- execution, resume, budget

def test_prepare_sends_nothing_and_execute_answers_every_frame(tmp_path, monkeypatch, trial):
    run = trial.prepared(tmp_path, monkeypatch)
    assert load(run / "plan-crops.json")["requests_planned"] == 51
    assert not (run / "crops" / "groups").exists(), "--prepare renders crops and answers nothing"
    assert len(list((run / "crops").glob("*.png"))) == 102

    result = trial.execute(run, monkeypatch, trial.transport())
    assert result["requests_sent"] == 51 and result["groups_answered"] == 51
    assert result["groups_failed"] == 0 and result["not_attempted"] == 0
    assert result["stopped_reason"] is None and result["status"] == "complete"

    # the runtime annotations grade nothing; the frozen labels grade the readable frames and no further
    assert load(run / "score-crops.json")["accuracy_is_reportable"] is False
    scored = bp.score_readings(result["observations"], trial.labels["frames"])
    assert scored["accuracy_is_reportable"] is True
    assert scored["frames"] == 34 and scored["frames_correct"] == 34, scored["per_frame"][:2]
    assert scored["ungraded_frames"] == 17 and scored["sides_exact"] == 68

    again = trial.transport()
    resumed = trial.execute(run, monkeypatch, again)
    assert again.bodies == [], "the resume re-asked %d group(s)" % len(again.bodies)
    assert resumed["groups_reused"] == 51 and resumed["requests_sent"] == 0
    assert resumed["observations"] == result["observations"]


def test_cancelling_and_resuming_loses_no_answer_and_a_moved_allowance_is_refused(tmp_path,
                                                                                  monkeypatch, trial):
    run = trial.prepared(tmp_path, monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        trial.execute(run, monkeypatch, trial.transport(cancel_on=20))
    assert len(list((run / "crops" / "groups").glob("*.json"))) == 19
    assert not (run / "result-crops.json").exists(), "a run that never finished prints no report"

    transport = trial.transport()
    result = trial.execute(run, monkeypatch, transport)
    assert result["groups_reused"] == 19 and result["requests_sent"] == 32
    assert len(transport.bodies) == 32, "the resume re-asked a group it already had"

    # and the same directory against a different budget is refused rather than quietly re-planned
    other = trial.prepared(tmp_path / "second", monkeypatch, allowance=30)
    with pytest.raises(SystemExit) as refused:
        trial.execute(other, monkeypatch, trial.transport(), allowance=12)
    assert "attempt_allowance changed since prepare" in str(refused.value)


def test_a_smaller_budget_defers_the_tail_by_name_in_time_order(trial, tmp_path):
    """An allowance that cannot cover the interval says which frames it is not doing."""
    # rendered into the scratch dir: the trial's own crops are frozen evidence, not a fixture to rewrite
    records = bp.build_records(load(trial.runtime)["frames"], tmp_path / "crops")
    plan = bp.plan_requests(records, "crops", max_requests=20)
    assert plan["requests_planned"] == 20 and plan["frames_deferred"] == 31
    # the deferral is the *tail in recording time*, which is the only order the trial can promise: a
    # budget that dropped frames from the middle would silently change what interval was tested
    assert plan["deferred_frames"] == [r["frame"] for r in records[20:]]
    assert [Path(r["frame"]).name for r in records[:20]] == sorted(
        Path(r["frame"]).name for r in records[:20])
    assert plan["attempt_allowance"] == 20


# ---------------------------------------------------------------- tracking the trial's own numbers

def test_the_rehearsal_recovers_four_games_and_keeps_the_resets_between_them(tmp_path, monkeypatch,
                                                                             trial):
    """What the trial is for, in the only form available before inference: labels in, games out.

    The four falls in this interval are 3 -> 0 *between* games. Grouped correctly they are four
    conclusions; grouped wrongly they are a pip row falling inside one game, which is what the corrected
    temporal rule has to keep contested. A rehearsal that mixed the two would let the trial report a
    winner the readings never named.
    """
    run = trial.prepared(tmp_path, monkeypatch)
    trial.execute(run, monkeypatch, trial.transport())
    _rows, out = tracked(trial, run)
    games = out["games"]
    assert len(games) == 4, [(g["start"], g["end"], g["rounds"]) for g in games]
    assert [g["winner"] for g in games] == ["p2", "p1", "p2", "p1"], \
        [(g["start"], g["winner"], g["winner_basis"]) for g in games]
    for game in games:
        assert game["status"] == "complete" and game["counts_toward_set"] is True
        assert game["contested"] is False and game["dot_regressions"] == {}, \
            "a between-game reset was adjudicated inside a game: %s" % game
    assert not [q for rnd in out["rounds"] for q in (rnd.get("win_dots_rejected") or [])], \
        "nothing was misread here, so no reading may be set aside"
    # The set layer used to report nothing (``sets is None``) and to accumulate wins across whatever games
    # were adjacent, which built one fictitious 2-2 "set" out of four games played by two different pairs.
    # Two candidates now, the cut between them naming its evidence -- and one of them complete, which it
    # was not until the recording was allowed to conclude a set on its own. This interval is configured
    # first-to-three and the candidate holds two wins to one, so no target closes it; what closes it is that
    # the players put the controllers down at both ends of the three games. Its *winner* stays unnamed for
    # exactly that reason: the boundary is the footage's claim, the score is the format's.
    counted = out["accounting"]["sets"]
    assert counted["candidates"] == len(out["sets"]) == 2, \
        "the four games did not split into their two matches: %s" % out["sets"]
    assert counted["complete"] == 1 and counted["games_covered"] == len(games)
    first, second = out["sets"]
    assert first["games"] == 1 and second["games"] == 3
    assert second["membership"] == "supported", [l["verdict"] for l in second["links"]]
    assert second["attribution"]["safe_to_read_as_players"] is True
    assert second["status"] == "complete" and second["concluded_by"] == ["station_cleared"]
    assert second["basis"] == "set_ended_on_the_recording_target_not_reached"
    assert second["set_winner"] is None, "a boundary the format does not authorise names no champion"
    assert second["winner_seat"] == "p1"
    assert second["games_without_winner"] == 0
    # The other candidate is the case the route must not overreach on: its end is on tape, its beginning is
    # not (the window opened mid-set, and the screen before its only game is a between-games screen, not a
    # station change), so the set is bounded and uncounted -- partial, with the reason.
    assert first["status"] == "partial"
    assert first["boundary_confirmed"] is True and first["footage_complete"] is False
    assert first["basis"] == "set_ended_on_the_recording_footage_incomplete"
    assert first["edges"]["start"]["state"] == "between_games_screen_before_this_bound"
    assert any("the start of this set" in m for m in first["missing_evidence"])
    cut = first["cut_after"]
    assert cut and cut["verdict"] == "separated" and cut["identity"] == "different"
    assert "controller_assignment" in (cut["preferred_cues"] or [])
    assert cut["identities"] == {"before": ["derpingtoast", "PWDDA | Ariz"],
                                 "after": ["Buk", "Rebix"]}, cut["identities"]
    assert any(u["type"] == "set_unresolved" for u in out["unresolved"])


def test_the_series_spliced_into_one_game_is_contested_and_not_resolved(tmp_path, monkeypatch, trial):
    """The control the previous trial could not run: a real fall *inside* one game, on fresh footage.

    Here the four between-game resets are forced into a single round, which is what one missed game
    boundary looks like from the inside. The rule must keep the game open, keep the low run as evidence,
    and queue the question -- never publish a winner off a row that fell to zero.
    """
    run = trial.prepared(tmp_path, monkeypatch)
    trial.execute(run, monkeypatch, trial.transport())
    rows, _ = tracked(trial, run)
    readings = sorted((r["timestamp"], r["win_dots"]) for r in rows
                      if any(v for v in (r.get("win_dots") or [])))
    assert len(readings) >= 20, len(readings)
    clocks = [60 - 3 * i for i in range(len(readings))]
    spliced = [{"id": i + 1, "timestamp": stamp, "path": "frames/frame-%04d.jpg" % i,
                "state": "gameplay", "evidence": "timer %d" % clock, "round_clock": clock,
                "win_dots": dots, "win_dots_drawn": [True, True], "win_dots_confidence": [0.9, 0.9],
                "confidence": 0.95}
               for i, ((stamp, dots), clock) in enumerate(zip(readings, clocks))]
    out = bg.track_games(bg.build_observations(spliced), rounds_per_game=3, games_per_set=3)
    assert len(out["games"]) == 1, [(g["start"], g["rounds"]) for g in out["games"]]
    game = out["games"][0]
    assert game["contested"] is True and game["winner"] is None, \
        "a spliced pair of games published a winner: %s" % game
    assert game["counts_toward_set"] is False
    reasons = "+".join(game["contested_reasons"])
    assert "win_dots_regressed" in reasons, game["contested_reasons"]
    for side in ("p1", "p2"):
        assert side in reasons, "only one side's reset was reported: %s" % game["contested_reasons"]
    queued = [u for u in out["unresolved"] if u["type"] == "win_dots_regressed"]
    assert {u["side"] for u in queued} == {"p1", "p2"}, "both resets have to reach the queue"
    for item in queued:
        assert any(abs(entry["at"] - item["at"]) < 0.001 for rnd in out["rounds"]
                   for side in ("p1", "p2")
                   for entry in (rnd.get("win_dots_evidence") or {}).get(side) or []), \
            "the queue must point at the reading that fell: %s" % item
    raw = [e["value"] for rnd in out["rounds"] for side in ("p1", "p2")
           for e in (rnd.get("win_dots_evidence") or {}).get(side) or []]
    assert 0 in raw and 3 in raw, "the whole run has to survive as evidence, not just the conclusion"


def test_partials_survive_and_the_boundaries_are_disclosed_not_silently_trimmed(tmp_path, monkeypatch,
                                                                                trial):
    """Leading and trailing partials stay in the record with their evidence, whatever they do to totals."""
    import evaluate_consecutive_game_trial as ev
    evaluated = ev.Trial(trial.dir)
    run = trial.prepared(tmp_path, monkeypatch)
    trial.execute(run, monkeypatch, trial.transport())
    rows, joined, observations = ev.tracking_rows(evaluated, [str(run)])
    described = ev.describe_tracking(evaluated, rows, joined, {}, observations)
    assert described["pip_values_not_from_crops"] == [], "a live pip value must trace to a crop"
    assert described["crop_readings_merged"] == 51
    # Measured, and worth stating: unlike the stage-3 interval, no timeline row inside [1200, 2200)
    # carries a full-frame dot value, so the quarantine has nothing to set aside here. That is a fact
    # about this stretch of the recording, not evidence that the quarantine works; it is reported so a
    # reader cannot mistake an empty count for a passing one.
    assert described["full_frame_dots_quarantined"] == 0
    assert not [r for r in load(TIMELINE) if trial.interval[0] <= r.get("timestamp", 0) <
                trial.interval[1] and r.get("win_dots")]
    for game in described["games"]:
        assert "leading_partial" in game and "trailing_partial" in game
        assert game["leading_evidence"] and game["trailing_evidence"]
    for boundary in described["game_boundaries"]:
        assert boundary["set_meaning"].startswith("not decided here")
    assert described["accounting"]["observations"] == len(rows), "rows outside a game still counted"


# ---------------------------------------------------------------- adaptive follow-up, by manifest identity

def test_unread_clock_only_rows_queue_crop_questions_that_name_manifest_frames(tmp_path, monkeypatch,
                                                                              trial):
    """The adaptive path: abstentions become questions, and the questions name manifest frames.

    Eleven of the seventeen windows end on a clock reading with no K.O. or TIME UP row, so a follow-up
    tranche is the normal outcome here rather than an edge case. Its frames come from the same manifest
    the plan used, and anything already read is reused instead of re-asked.
    """
    import evaluate_consecutive_game_trial as ev
    run = trial.prepared(tmp_path, monkeypatch)
    trial.execute(run, monkeypatch, trial.transport(abstain_on=sorted(clock_only_stems(trial))))

    evaluated = ev.Trial(trial.dir)
    rows, joined, observations = ev.tracking_rows(evaluated, [str(run)])
    described = ev.describe_tracking(evaluated, rows, joined, {}, observations)
    assert [u for u in described["unresolved"] if u["type"] == "win_dots_never_read"], \
        "a window answered with no reading must queue a question, not a silent gap"

    read = list(observations)
    plan = ev.follow_up_plan(evaluated, described, attempts_left=6, already_read=read)
    assert plan["requests_planned"] == 6, plan["reason_no_further_attempt_was_justified"]
    assert plan["images_planned"] == 12
    index = bw.FrameIndex.load(evaluated.manifest)
    by_path = {f["path"]: f for f in index.frames}
    for entry in plan["frames"]:
        assert trial.interval[0] <= entry["timestamp"] < trial.interval[1], entry
        assert entry["frame"] in by_path, "not a manifest frame: %s" % entry["frame"]
        assert by_path[entry["frame"]]["id"] == entry["id"]
        assert by_path[entry["frame"]]["timestamp"] == pytest.approx(entry["timestamp"], abs=1e-9)
        assert entry["frame"] not in read, "the follow-up re-asked a frame that was already answered"
        assert entry["selection_role"] == "follow_up_pip_crop"
        assert entry["row_drawn"] is None and entry["pips"] == {"LEFT": None, "RIGHT": None}

    # a frame the trial has already read is named as reused, not asked again
    first = plan["frames"][0]["frame"]
    reused = ev.follow_up_plan(evaluated, described, attempts_left=6, already_read=read + [first])
    assert first not in [entry["frame"] for entry in reused["frames"]], "a read frame was re-asked"
    assert any(skip["frame"] == first for skip in reused["reused_instead_of_reasking"]), \
        "the reuse has to be reported, not just performed"
    for skip in reused["reused_instead_of_reasking"]:
        assert skip["id"] and skip["because_of"] in \
            {"win_dots_never_read", "win_dots_regressed", "game_winner_unobserved"}

    broke = ev.follow_up_plan(evaluated, described, attempts_left=0, already_read=read)
    assert broke["requests_planned"] == 0 and broke["frames"] == []
    assert broke["deferred_for_budget"], "deferral has to name the work it is not doing"
    assert broke["reason_no_further_attempt_was_justified"]
    for frame in broke["deferred_for_budget"]:
        assert frame["id"] and frame["path"] and frame["timestamp"]

    # a question that is not about the pip row is deferred with its reason, never asked with crops
    other = ev.follow_up_plan(evaluated, {"unresolved": [{"type": "round_clock_unread", "at": 1900.0,
                                                          "would_resolve": "the clock digits"}]},
                              attempts_left=5, already_read=read)
    assert other["requests_planned"] == 0 and other["deferred_as_other_kind"]
    assert "pip row" in other["deferred_as_other_kind"][0]["why_not_asked_with_crops"]


def test_a_follow_up_frame_comes_from_the_manifest_not_from_the_caches_spacing(trial):
    """The lookup refuses to invent a frame, and the formula it replaces is measurably off."""
    index = bw.FrameIndex.load(trial.plan["manifest"]["path"])
    at = index.at_or_after(1802.80)
    assert at["id"] == 1802 and at["path"].endswith("frame-1802.jpg")
    assert at["timestamp"] == pytest.approx(1802.800905, abs=1e-4), "the timestamp is the manifest's"
    assert index.at_or_after(10_000.0) is None, "past the end of the cache there is nothing to ask"
    # The deleted evaluator computed ``round(timestamp / 1.000986) + 1`` and formatted that back into a
    # filename. Against this manifest the assumption drifts by up to ~96 ms by the end of the recording,
    # so the recovered number is a guess about which frame a second names -- and at a HUD transition a
    # guess is the difference between three lit pips and a row that is no longer on screen.
    drift = [abs(f["timestamp"] - 1.000986 * (f["id"] - 1)) for f in index.frames]
    assert max(drift) > 0.09, "the drift this test exists to forbid has changed shape: %s" % max(drift)
    assert drift[0] == 0.0


# ---------------------------------------------------------------- label disputes, reported neutrally

def test_a_label_disagreement_is_reported_without_being_decided(tmp_path, monkeypatch, trial):
    """Where the model and the frozen label differ, the record says what each claimed -- no more.

    The stage-3 evaluator asserted, in a field named ``assessment``, that the label was the likelier
    error. That is a verdict on evidence the file does not hold, and it is the same mistake the trial
    exists to catch, so the reporting lists both readings and leaves the call to whoever can see the
    crop. An instance, not a function: ``_post`` is patched onto the class, and a plain function would
    bind ``self`` into it.
    """
    import evaluate_consecutive_game_trial as ev
    evaluated = ev.Trial(trial.dir)
    run = trial.prepared(tmp_path, monkeypatch)
    victim = next(f for f in trial.labels["frames"] if f["graded"] and f["row_drawn"]
                  and f["pips"]["LEFT"] != f["pips"]["RIGHT"])

    class Contradicting:
        """Answers the victim frame with each side's count on the other side, and every other truly.

        A swap rather than a random number: it is the error class this project has actually caught
        (``[2, 1]`` reported for an ``[1, 2]`` row), and a dispute record that could only fire on a
        made-up count would not be tested on the shape disputes really take.
        """

        def __init__(self, base, frame):
            # the crops request names the two PNGs it sends, never the source JPG, so the match is on
            # the frame stem both of them carry
            self.base, self.stem = base, Path(frame).stem

        def __call__(self, body):
            reply = self.base(body)
            if self.stem not in json.dumps(body):
                return reply
            truth = json.loads(reply["choices"][0]["message"]["content"])
            for entry in truth:
                other = "RIGHT" if entry["label"] == "LEFT" else "LEFT"
                lit = victim["pips"][other]
                entry["lit"], entry["positions"] = lit, list(range(1, lit + 1)) if lit else []
            reply["choices"][0]["message"]["content"] = json.dumps(truth)
            return reply

    trial.execute(run, monkeypatch, Contradicting(trial.transport(), victim["frame"]))
    rows, joined, observations = ev.tracking_rows(evaluated, [str(run)])
    described = ev.describe_tracking(evaluated, rows, joined, {}, observations)
    scored = {"scored": bp.score_readings(load(run / "result-crops.json")["observations"],
                                          trial.labels["frames"])}
    disputes = ev.label_disputes(evaluated, scored, described, rows, trial.labels["frames"])
    assert disputes, "the altered reading should have produced a dispute"
    for dispute in disputes:
        text = json.dumps(dispute)
        for banned in ("more likely error", "the label is wrong", "the model is wrong",
                       "likelier", "probably mislabelled"):
            assert banned not in text, "the dispute record decided %r" % banned
        assert dispute["frozen_basis"] and dispute["model_reported"] is not None
        assert dispute["frozen_label"] != dispute["model_reported"]
        assert "changed_any_conclusion" in dispute and "not_decided_here" in dispute
        assert "new version" in dispute["decision"]
        assert "flash" in dispute["not_decided_here"] or "disagree" in dispute["not_decided_here"]
        for neighbour in dispute["neighbouring_frozen_labels"]:
            assert "seconds_relative_to_dispute" in neighbour and "graded" in neighbour
            assert "basis" in neighbour, "a neighbour label is quoted so its own claim is visible"
