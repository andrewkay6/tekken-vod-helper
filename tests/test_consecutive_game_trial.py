"""Stage 2 rehearsal of the consecutive-game trial: it runs, stops, and resumes honestly.

Only the transport is replaced, so request identity, the attempt allowance, the retry loop, the
per-group persistence, the merge and the tracker all run for real. The point is to spend zero inference
while proving the things that would otherwise surface mid-run: that the frozen interval contains a game
boundary at all, that evaluation labels never reach a runtime input, and that a stopped or cancelled run
defers its remaining work *by name* instead of losing it.

Nothing in this interval has ever been sent, so the readings here come from the frozen evaluator labels,
which were written from the same crops before any output existed. That makes these tests a rehearsal of
the machinery and never evidence about the model.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_investigate as bi
from tekken_vod_helper import boundary_pips as bp

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

TRIAL = ROOT / "build/work/bb9/consecutive-games-v2"
RUNTIME = TRIAL / "annotations-window.json"
PLAN = TRIAL / "selected-frames.json"
LABELS = TRIAL / "labels-eval-frozen.json"
TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
SCRIPT = ROOT / "scripts/run_pip_crop_probe.py"
CACHE = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
WINDOW = (226.0, 430.0)
DURATION = 6928.421136

pytestmark = pytest.mark.skipif(
    not all(p.is_file() for p in (RUNTIME, PLAN, LABELS, TIMELINE, CACHE)),
    reason="the frozen trial artifacts live under build/ and are absent on a fresh clone")

SELECT_SCREENS = {"stage_select", "character_select"}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_step(monkeypatch, argv, transport=None):
    """Call the public script's main() in-process with only the transport replaced."""
    spec = importlib.util.spec_from_file_location("trial_probe_script", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "trial_probe_script", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + argv)
    if transport is not None:
        monkeypatch.setattr(bp.PipReadClient, "_post", transport, raising=True)
    module.main()
    return module


class LabelledTransport:
    """Answers each group from the frozen labels, and can abstain on chosen frames or meet Ctrl-C.

    The frame is read back out of the request body rather than assumed: a crops request names both
    groups, and answering the right side from the left side's record is the exact side-swap this project
    keeps having to hunt for, so a rehearsal that made it would prove nothing.
    """

    def __init__(self, labels, abstain_on=(), cancel_on=None):
        self.by_stem = {}
        for frame in labels["frames"]:
            drawn = frame["row_drawn"] if frame["row_drawn"] in (True, False) else None
            self.by_stem[Path(frame["frame"]).stem] = {"LEFT": (drawn, frame["pips"]["LEFT"]),
                                                       "RIGHT": (drawn, frame["pips"]["RIGHT"])}
        self.abstain_on = {str(stem).rjust(4, "0") for stem in abstain_on}
        self.cancel_on = cancel_on              # 1-based request number that meets Ctrl-C
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        if self.cancel_on is not None and len(self.bodies) == self.cancel_on:
            raise KeyboardInterrupt("operator stopped the run")
        blob = json.dumps(body)
        found = re.search(r"frame-(\d{4})", blob)
        if found is None or "frame-" + found.group(1) not in self.by_stem:
            raise AssertionError("the request named no frame this trial knows about")
        stem = found.group(1)
        payload = []
        for item, label in re.findall(r"item (\d+) label=(\w+)", blob):
            drawn, lit = (None, None) if stem in self.abstain_on \
                else self.by_stem["frame-" + stem][label]
            payload.append({"item": int(item), "label": label, "drawn": drawn, "lit": lit,
                            "positions": [] if not lit else list(range(1, lit + 1)),
                            "confidence": 0.9 if drawn is True else 0.4 if drawn is None else 0.8})
        return {"choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 627, "completion_tokens": 557,
                          "completion_tokens_details": {"reasoning_tokens": 473}}}


class CountingClient:
    """A round-facts client that says nothing useful, so the follow-up budget is what is measured."""

    def __init__(self, max_requests=24):
        self.max_requests = int(max_requests)
        self.calls = 0
        self.usage = []

    def classify(self, frames):
        self.calls += 1
        reply = [{"id": frame["id"], "state": "gameplay", "confidence": 0.6, "round_clock": None,
                  "round_call": None, "round_end": None, "result_transition": None,
                  "win_dots_p1": None, "win_dots_p2": None} for frame in frames]
        self.usage.append({"call": self.calls, "tokens": {"prompt_tokens": 900,
                                                          "completion_tokens": 200}})
        return reply


@pytest.fixture(scope="module")
def plan():
    return load(PLAN)


@pytest.fixture(scope="module")
def labels():
    return load(LABELS)


def ledger(tmp_path, requested, cap=None):
    """A real temporary ledger: the public command refuses --execute that no attempt ledger counts.

    The cap is deliberately above the allowance so these rehearsals keep testing the *allowance* and the
    deferral behaviour, not the guard; the two tests that exercise the guard pass their own budget.
    """
    return ["--budget-file", str(Path(tmp_path) / "attempts.json"),
            "--budget-cap", str(cap if cap is not None else requested + 32),
            "--budget-minutes", "60"]


def prepared(tmp_path, plan, monkeypatch, allowance=None):
    """--prepare the trial into a scratch directory. Renders crops; sends nothing."""
    def forbidden(body):
        raise AssertionError("--prepare sent a request: {}".format(str(body)[:160]))
    requested = int(allowance or plan["requests_planned"])
    run_step(monkeypatch, ["--annotations", str(RUNTIME), "--output-dir", str(tmp_path),
                           "--arms", "crops",
                           "--max-requests", str(requested)] + ledger(tmp_path, requested)
             + ["--prepare"], transport=forbidden)
    return tmp_path


def execute(tmp_path, monkeypatch, transport, allowance=14):
    run_step(monkeypatch, ["--annotations", str(RUNTIME), "--output-dir", str(tmp_path),
                           "--arms", "crops", "--max-requests", str(allowance)]
             + ledger(tmp_path, allowance) + ["--execute"], transport=transport)
    return json.loads((tmp_path / "result-crops.json").read_text(encoding="utf-8"))


def merged_rows(run_dir):
    import validate_game_one as vg
    rows, _ = vg.merged_rows(vg.crop_observations([Path(run_dir)]), WINDOW)
    return rows


# ---------------------------------------------------------------- the frozen plan itself

def test_the_trial_fits_the_new_allowance_and_defers_nothing(plan):
    assert plan["requests_planned"] == 14 and plan["images_total"] == 28
    assert plan["request_allowance"] == 14 and plan["budget_fits"] is True
    assert plan["requests_deferred"] == 0 and plan["deferred_for_budget"] == []
    # Reuse is decided by the fact that this exact frame was already answered, not by its timestamp.
    assert len(plan["reused_readings"]) == 1
    assert plan["manifest"]["path"].endswith("detect-1440p-v1/frames/sample-manifest.json")
    for entry in plan["requested"]:
        assert (ROOT / entry["frame"]).is_file(), entry["frame"]


def test_the_frozen_interval_actually_contains_a_game_boundary(plan):
    """A trial that spans no game boundary is not a consecutive-game trial.

    The first version of this plan sat on [1006, 1406), where a stage-selection screen sits between the
    two games. What that says about set membership is not settled here -- a stage select can occur inside
    a set -- but it is a different kind of transition from the one this trial is for, and half of that
    interval's evidence was already read, so it bought one new game while claiming consecutive ones.
    The frozen interval spans a transition with no select screen between two games, which is what a new
    game inside one match looks like, and the new game is unread. Set status stays out of scope.
    """
    rows = load(TIMELINE)
    windows = plan["windows"]
    assert len(windows) >= 2
    first, second = windows[0]["end_at"], windows[1]["end_at"]
    between = {r["state"] for r in rows if first < r.get("timestamp", 0) < second}
    between -= {"unknown", "gameplay"}
    assert not (between & SELECT_SCREENS), \
        "these two games are separated by a select screen, so the interval spans matches: %s" % between
    assert "post_match_menu" in between, "the boundary needs its own closing evidence, not just a gap"


def test_every_window_is_accounted_for_with_its_anchor_quality(plan):
    """Clock-only windows are allowed, but only if the plan says so out loud."""
    for window in plan["windows"]:
        assert window["end_basis"] in ("ko", "time_up", "last_readable_clock")
        if window["end_basis"] == "last_readable_clock":
            assert window["gaps"], "a clock-only ending must record why it is uncertain"
    assert any(w["end_basis"] != "last_readable_clock" for w in plan["windows"]), \
        "an all-clock-only interval could never conclude a game from its own anchors"


def test_labels_stay_out_of_everything_the_run_reads(plan, labels):
    """Selection, prompts and the runtime annotations carry no expected counts.

    The runtime annotation file is what the probe renders and sends; a filled ``pips`` there would put
    the answer inside the request pipeline. Truth lives in a separate file the scorer alone opens.
    """
    runtime = load(RUNTIME)
    assert all(f["pips"] == {"LEFT": None, "RIGHT": None} for f in runtime["frames"])
    assert all(f.get("row_drawn") is None for f in runtime["frames"])
    assert runtime["provenance"]["expected_values"].startswith("left null on purpose")
    probe_plan = json.dumps(load(TRIAL / "plan-crops.json"))
    assert "labels-eval-frozen" not in probe_plan and "labels-eval-frozen" not in json.dumps(plan)
    for frame in labels["frames"]:
        if not frame["graded"]:
            continue
        for side, value in frame["pips"].items():
            assert not re.search(r'"%s"\s*:\s*%d\b' % (side, value), probe_plan), \
                "an expected count reached a file the request is built from"
    assert len(labels["frames"]) == len(runtime["frames"]) == len(plan["requested"])


def test_unknown_truth_is_never_an_absent_row_or_a_zero(labels):
    graded = [f for f in labels["frames"] if f["graded"]]
    unknown = [f for f in labels["frames"] if not f["graded"]]
    assert unknown, "the labels must cover the ambiguous transitions, not only the clean crops"
    for frame in unknown:
        assert frame["pips"] == {"LEFT": None, "RIGHT": None}
        assert frame["row_drawn"] is None, "unknown truth is not 'the row is absent'"
    for frame in graded:
        assert frame["row_drawn"] in (True, False)
        counts = [frame["pips"]["LEFT"], frame["pips"]["RIGHT"]]
        assert all(value is None or 0 <= value <= 3 for value in counts)
        if not any(counts):
            assert frame["row_drawn"] is True, "an all-empty readable row is a real 0-0"


# ---------------------------------------------------------------- execution, budget, resume

def test_prepare_sends_nothing_and_execute_answers_every_frame(tmp_path, monkeypatch, plan, labels):
    run = prepared(tmp_path, plan, monkeypatch)
    assert load(run / "plan-crops.json")["requests_planned"] == 14

    result = execute(run, monkeypatch, LabelledTransport(labels))
    assert result["requests_sent"] == 14 and result["groups_answered"] == 14
    assert result["groups_failed"] == 0 and result["not_attempted"] == 0
    assert result["stopped_reason"] is None and result["status"] == "complete"
    # scored against the runtime annotations nothing is graded: those labels are not in that file
    score = load(run / "score-crops.json")
    assert score["accuracy_is_reportable"] is False and score["ungraded_frames"] == 14
    # against the frozen labels the same readings do score, and a truthful rehearsal scores clean
    graded = bp.score_readings(result["observations"], labels["frames"])
    assert graded["accuracy_is_reportable"] is True
    assert graded["ungraded_frames"] == sum(1 for f in labels["frames"] if not f["graded"])
    assert graded["frames_correct"] == graded["frames"], [
        frame for frame in graded["per_frame"] if not frame["correct"]]

    # a second execute is the resume: it sends nothing and reproduces the same readings
    again = LabelledTransport(labels)
    resumed = execute(run, monkeypatch, again)
    assert again.bodies == [], "resume re-asked %d group(s)" % len(again.bodies)
    assert resumed["groups_reused"] == 14 and resumed["requests_sent"] == 0
    assert resumed["observations"] == result["observations"]


def test_a_moved_allowance_is_refused_and_a_smaller_budget_names_its_deferrals(tmp_path, monkeypatch,
                                                                              plan, labels):
    """Two budgets, both explicit: the run refuses an allowance that moved after --prepare, and a
    smaller plan has to list the frames it is not doing."""
    run = prepared(tmp_path, plan, monkeypatch)
    with pytest.raises(SystemExit) as refused:
        execute(run, monkeypatch, LabelledTransport(labels), allowance=5)
    assert "attempt_allowance changed since prepare" in str(refused.value), \
        "resuming a half-spent allowance against a different budget must be refused, not attempted"
    assert len(list((run / "crops" / "groups").glob("*.json"))) == 0

    records = bp.build_records(load(RUNTIME)["frames"], tmp_path / "small-crops")
    plan5 = bp.plan_requests(records, "crops", max_requests=5)
    assert plan5["requests_planned"] == 5 and plan5["frames_deferred"] == 9
    assert len(plan5["deferred_frames"]) == 9, "a deferral must name the work it is not doing"
    assert plan5["attempt_allowance"] == 5


def test_cancelling_mid_run_loses_no_answer_and_resumes_without_reasking(tmp_path, monkeypatch, labels):
    """Ctrl-C is a supported stop, because every group is on disk before the next request leaves."""
    run = prepared(tmp_path, load(PLAN), monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        execute(run, monkeypatch, LabelledTransport(labels, cancel_on=6))
    assert len(list((run / "crops" / "groups").glob("*.json"))) == 5, \
        "a cancelled run must keep exactly the groups it finished"
    assert not (run / "result-crops.json").exists(), "a run that never finished prints no report"

    transport = LabelledTransport(labels)
    result = execute(run, monkeypatch, transport)
    assert result["groups_reused"] == 5 and result["requests_sent"] == 9
    assert result["groups_answered"] == 14 and result["not_attempted"] == 0
    assert len(transport.bodies) == 9, "the resume re-asked a group it already had"

    # and a finished run stays finished: a further --execute spends nothing at all
    idle = LabelledTransport(labels)
    final = execute(run, monkeypatch, idle)
    assert idle.bodies == [] and final["requests_sent"] == 0 and final["groups_reused"] == 14


# ---------------------------------------------------------------- unresolved readings

def test_a_clock_only_window_left_unread_gets_follow_up_then_an_explicit_deferral(tmp_path, monkeypatch,
                                                                                 plan, labels):
    """Most of this interval ends on a clock reading only, so the unresolved path is the normal path."""
    clock_only = {w["sequence"] for w in plan["windows"] if w["end_basis"] == "last_readable_clock"}
    assert clock_only, "the frozen plan is expected to contain clock-only windows"
    stems = {Path(entry["frame"]).stem.split("-")[1] for entry in plan["requested"]
             if entry["window"] in clock_only}
    run = prepared(tmp_path, plan, monkeypatch)
    stopped = execute(run, monkeypatch, LabelledTransport(labels, abstain_on=stems))
    assert stopped["groups_answered"] == 14, "an abstention is an answer, not a failed request"

    rows = merged_rows(run)
    tracked = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=3)
    assert [u for u in tracked["unresolved"] if u["type"] == "win_dots_never_read"], \
        "a window answered with no reading must queue a question, not a silent gap"
    assert bi.candidate_regions(tracked["unresolved"], DURATION, window=WINDOW), \
        "the queued questions produced no investigation region"

    full = bi.plan_investigation(tracked["unresolved"], bi.load_cache(str(CACHE)), DURATION,
                                 observations=rows, window=WINDOW, max_requests=60)
    tight = bi.plan_investigation(tracked["unresolved"], bi.load_cache(str(CACHE)), DURATION,
                                  observations=rows, window=WINDOW, max_requests=1)
    # The one-request plan has to name what it could not reach, on either route: a cap that simply
    # produced a shorter bill would look identical to a window with less left to ask.
    assert {entry["route"] for entry in tight["dispatch"]["routes"]} == {"pip_crops", "state", "header"}
    assert any(entry["requests_planned"] == 1 for entry in tight["dispatch"]["routes"])
    # Per route, and still an exact count: this window owes 5 crop readings and 4 state batches, and an
    # uncapped plan has to list both. Comparing the run's whole bill against the state loop's group count
    # is the mistake the routes were split to make impossible, so the assertion is written per route now.
    # Two of the four state batches are the set layer's own continuity claim: what separates these games is
    # a whole-frame question, and the probe sits at each end of the stretch between them.
    # The fifth crop read is new, and it is attributed rather than assumed: the plan is priced again with
    # the single-reading winners removed, and the difference must be exactly one. A game credited from one
    # frame now buys a second look at that row, which is a request the earlier bill did not make.
    assert full["state_requests_planned"] == full["groups_total"] == 4, full["dispatch"]
    unattributed = [item for item in tracked["unresolved"]
                    if item["type"] != "game_winner_single_reading"]
    assert unattributed != tracked["unresolved"], "this scenario must contain a single-reading winner"
    baseline = bi.plan_investigation(unattributed, bi.load_cache(str(CACHE)), DURATION,
                                     observations=rows, window=WINDOW, max_requests=60)
    assert baseline["pip_requests_planned"] == 4, baseline["dispatch"]
    assert full["pip_requests_planned"] == 5, full["dispatch"]
    assert full["requests_planned"] == 9, full["requests_planned"]
    assert tight["requests_planned"] < full["requests_planned"]
    assert tight["deferred_regions"], "an exhausted budget must defer the remainder by name"
    for region in tight["deferred_regions"]:
        assert WINDOW[0] <= region["start"] and region["end"] <= WINDOW[1], region
        assert region["kinds"], "a deferral has to say which question it leaves open"


def test_the_follow_up_run_spends_its_allowance_then_resumes_without_reasking(tmp_path, monkeypatch,
                                                                             labels):
    """An exhausted investigation is a result, not a crash, and the next run picks up where it stopped."""
    plan = load(PLAN)
    run = prepared(tmp_path, plan, monkeypatch)
    clock_only = {w["sequence"] for w in plan["windows"] if w["end_basis"] == "last_readable_clock"}
    stems = {Path(entry["frame"]).stem.split("-")[1] for entry in plan["requested"]
             if entry["window"] in clock_only}
    execute(run, monkeypatch, LabelledTransport(labels, abstain_on=stems))
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps(merged_rows(run)), encoding="utf-8")

    out = tmp_path / "follow-up"
    # One tranche per invocation (``passes=1``): this is a test of what a *tranche* spends and defers,
    # and the multi-pass loop inside a single invocation is tested in test_typed_recovery_dispatch.py.
    # ``expand=0.0`` pins the window for the same reason: widening is a real behaviour of the executor and
    # gets its own tests; here the numbers below are one fixed window's accounting, and a run that chose a
    # bigger window would honestly report different counts without anything about this guarantee changing.
    spent = bi.investigate_saved_games(timeline, str(CACHE), DURATION, out, region=WINDOW,
                                       max_requests=1, client_factory=CountingClient,
                                       rounds_per_game=3, games_per_set=3, routes=("state",),
                                       passes=1, expand=0.0)
    # A one-request budget is spent entirely on the highest-priority region, and the rest is written
    # down as deferred rather than dropped: the run completes what it could afford and names the rest.
    assert spent["requests_sent"] == 1 and spent["groups_answered"] == 1
    assert spent["budget"]["attempts_used"] == 1 and spent["budget"]["attempts_remaining"] == 0
    saved_plan = load(out / "plan.json")
    assert saved_plan["requests_planned"] == 1
    assert saved_plan["regions_deferred_for_budget"] >= 1 and saved_plan["deferred_regions"], \
        "an exhausted investigation budget must defer the remaining regions by name"
    for region in saved_plan["deferred_regions"]:
        assert region["kinds"], "a deferral has to say which question it leaves open"
        assert WINDOW[0] <= region["start"] and region["end"] <= WINDOW[1]

    # a second approved tranche continues the same directory and re-asks nothing already answered.
    # This run injects one transport and names one route, so the window's 4 crop readings are not on its
    # bill -- they are listed as pip_crops deferrals instead of disappearing, which is what a second
    # approval gets priced from.
    again = bi.investigate_saved_games(timeline, str(CACHE), DURATION, out, region=WINDOW,
                                       max_requests=3, client_factory=CountingClient, resume=True,
                                       rounds_per_game=3, games_per_set=3, routes=("state",),
                                       passes=1, expand=0.0)
    assert again["groups_resumed"] == 1, "the resume re-asked the group that was already answered"
    assert again["requests_sent"] == 2 and again["budget"]["attempts_used"] == 2
    state_plan = load(out / "plan.json")
    assert state_plan["dispatch"]["funded"] == ["state"]
    assert state_plan["dispatch"]["unfunded_routes"] == ["pip_crops", "header"]
    assert [entry["route"] for entry in state_plan["deferred_regions"] if entry["kinds"]]
    deferred_routes = {entry["route"] for entry in state_plan["deferred_regions"]}
    assert "pip_crops" in deferred_routes, \
        "the crop work this invocation cannot fund has to be named, not omitted"


def test_the_trial_series_spliced_into_one_game_stays_contested(tmp_path, monkeypatch, plan, labels):
    """The trial's real risk, stated as a control over the trial's own numbers.

    Across this boundary the right side reads 3 at 235.23 s (the leading game's closing row) then 0, 0,
    1, 1, 1 through the new game, and the left side falls 1 -> 0 before rebuilding to 3. Spliced into a
    single game -- which is what happens when the between-game screen goes unread -- each side is a fall
    followed by something. The pipeline must keep the game contested rather than publish a winner, and it
    must keep the whole low run: only a reading that the same side's immediate neighbours contradict may
    be set aside, and even then its raw copy has to survive.
    """
    run = prepared(tmp_path, plan, monkeypatch)
    execute(run, monkeypatch, LabelledTransport(labels))
    readings = [(row["timestamp"], row["win_dots"]) for row in merged_rows(run)
                if row.get("win_dots")]
    readings.sort()
    assert len(readings) >= 8, "the rehearsal produced too few rows to span the boundary: %s" % readings

    # one round, one game: a clock that only ever falls, and no menu to split on
    clocks = list(range(60, 60 - len(readings) * 4, -4))
    spliced = [{"id": index + 1, "timestamp": stamp, "path": "frames/frame-%04d.jpg" % stamp,
                "state": "gameplay", "evidence": "timer %d" % clock, "round_clock": clock,
                "win_dots": dots, "win_dots_drawn": [True, True],
                "win_dots_confidence": [0.9, 0.9], "confidence": 0.95}
               for index, ((stamp, dots), clock) in enumerate(zip(readings, clocks))]
    tracked = bg.track_games(bg.build_observations(spliced), rounds_per_game=3, games_per_set=3)
    assert len(tracked["games"]) == 1, [(g["start"], g["end"], g["rounds"]) for g in tracked["games"]]
    game = tracked["games"][0]
    assert game["contested"] is True and game["winner"] is None, \
        "a spliced pair of games published a winner: %s" % game
    assert game["counts_toward_set"] is False
    # RIGHT's 3 -> 0 -> 0 -> 1 is a sustained run below the previous high, so it is reported and the
    # game stays open. LEFT's single 0 between two agreeing 1s is the isolated case the rule does
    # resolve, and the two outcomes must not be confused for each other.
    assert game["contested_reasons"] == ["win_dots_regressed:p2"], game["contested_reasons"]
    assert game["dot_regressions"] == {"p2": {"from": 3, "to": 0, "at": readings[2][0]}}, \
        game["dot_regressions"]
    series = {side: [value for rnd in tracked["rounds"]
                     for value in (rnd.get("win_dots_seen") or {}).get(side) or []]
              for side in ("p1", "p2")}
    assert series["p2"] == [3, 0, 1] and max(series["p1"]) == 3, \
        "the sustained low run must survive as evidence: %s" % series["p2"]
    assert len([q for rnd in tracked["rounds"] for q in (rnd.get("win_dots_rejected") or [])]) == 1, \
        "exactly one reading may be set aside here, and only the isolated one"
    rejected = [q for rnd in tracked["rounds"] for q in (rnd.get("win_dots_rejected") or [])]
    assert [q["side"] for q in rejected] == ["p1"] and rejected[0]["value"] == 0
    assert [s["value"] for s in rejected[0]["supported_by"]] == [1, 1], rejected[0]
    raw = [e["value"] for rnd in tracked["rounds"]
           for e in (rnd.get("win_dots_evidence") or {}).get("p1") or []]
    assert 0 in raw, "the set-aside reading must still be in the raw series: %s" % raw
    for side in ("p1", "p2"):
        queued = [u for u in tracked["unresolved"] if u["type"] == "win_dots_regressed"
                  and u["side"] == side]
        if side == "p2":
            assert len(queued) == 1, "the reported fall must reach the investigation queue"
            assert queued[0]["at"] == game["dot_regressions"]["p2"]["at"], \
                "the queue must point at the reading that fell, not the game start"
            assert queued[0]["game_start"] == game["start"]
        else:
            assert not queued, "a resolved isolated read must not also queue a question"


def test_the_merged_trial_yields_two_games_and_names_the_new_winner(tmp_path, monkeypatch, plan,
                                                                   labels):
    """What the trial is for: a leading partial game and one new game concluded from its own pips."""
    run = prepared(tmp_path, plan, monkeypatch)
    execute(run, monkeypatch, LabelledTransport(labels))
    tracked = bg.track_games(bg.build_observations(merged_rows(run)), rounds_per_game=3,
                             games_per_set=3)
    games = tracked["games"]
    assert len(games) == 2, [(g["start"], g["end"], g["rounds"]) for g in games]
    fresh = [g for g in games if g["rounds"] >= 3]
    assert len(fresh) == 1
    game = fresh[0]
    assert game["winner"] == "p1", "the frozen labels show LEFT reaching three: %s" % game
    assert game["winner_basis"] == "win_dots" and game["contested"] is False
    assert game["counts_toward_set"] is True
    leading = [g for g in games if g is not game][0]
    assert leading["rounds"] < game["rounds"], "the leading game is a partial and must stay that way"
    assert leading["start"] >= WINDOW[0], "the partial must begin inside the interval, not before it"
    # Set accounting used to be a literal None -- "not counted yet". It is counted now, and what matters
    # is that nothing here is allowed to read as a finished set: a candidate exists, none of them is
    # complete, and every game still belongs to exactly one candidate.
    counted = tracked["accounting"]["sets"]
    assert counted["candidates"] == len(tracked["sets"]) >= 1
    assert counted["complete"] == 0 and counted["games_covered"] == len(games)
    assert any(u["type"] == "set_unresolved" for u in tracked["unresolved"]), \
        "an open set must be reported as open"
