"""Offline tests for saved-timeline recovery: bounded prompts, reconciliation, budgets, resume.

Nothing here contacts a model, the source video or the frame cache: ``urlopen`` is replaced by a
fake that answers per adjudication window, and the media helpers are patched to raise if recovery
ever reaches for them.
"""
import json
import pathlib

import pytest

from tekken_vod_helper import boundary_media, boundary_recovery, boundary_workflow
from tekken_vod_helper.boundary_reasoning import (
    DEFAULT_ADJUDICATION_REQUESTS,
    DEFAULT_COMPLETION_TOKENS,
    MAX_INPUT_CHARS,
    TemporalAdjudicator,
    build_observations,
    plan_windows,
)

POLICY = boundary_workflow.read_policy()
PAD = "b" * 900  # ~1030 chars per observation, so a 9000-char cap yields a few rows per window


# ---------- timeline and model helpers ----------

def obs_row(index, state, timestamp=None, evidence="ev", pair=("Ann", "Zoe"),
            chars=("Jin", "Nina"), confidence=0.9):
    return {"id": index + 1,
            "timestamp": float(index * 10) if timestamp is None else float(timestamp),
            "state": state, "confidence": confidence,
            "player1": pair[0], "player2": pair[1],
            "character1": chars[0], "character2": chars[1], "evidence": evidence}


def rows_from(states, start_index=0, start_ts=0.0):
    """A timeline built straight from a state list, so a test can say exactly what is where."""
    return [obs_row(start_index + i, state, start_ts + (start_index + i) * 10)
            for i, state in enumerate(states)]


# A bracket pairs different people in each set, and that change is the only reliable sign a stage
# grid opens a new set rather than the next game of the set already in progress.
BRACKET_PAIRS = [("Ann", "Zoe"), ("Bo", "Cy"), ("Di", "Ed"), ("Fi", "Gil"), ("Hal", "Ivy"),
                 ("Jo", "Kim"), ("Lee", "Mo"), ("Nan", "Opal")]


def build_sets(count=2, pad="", games=3):
    """(rows, spans) for ``count`` complete sets: grid, one row of play per game, closing prompt."""
    rows, spans = [], []
    index, ts = 0, 0.0
    for n in range(count):
        pair = BRACKET_PAIRS[n % len(BRACKET_PAIRS)]
        start = index
        rows.append(obs_row(index, "stage_select", ts, evidence="grid" + pad, pair=pair))
        index, ts = index + 1, ts + 10
        for _ in range(games):
            rows.append(obs_row(index, "gameplay", ts, evidence="fight" + pad, pair=pair))
            index, ts = index + 1, ts + 10
        rows.append(obs_row(index, "controller_prompt", ts, evidence="press" + pad, pair=pair))
        index, ts = index + 1, ts + 10
        spans.append((start, index - 1))
    return rows, spans


def sets_timeline(count=2, pad="", games=3):
    return build_sets(count, pad=pad, games=games)[0]


def spans_as_times(rows, spans):
    return [(rows[start]["timestamp"], rows[end]["timestamp"]) for start, end in spans]


def proposal(start, end, disposition="candidate", reason="observed set"):
    return {"start_id": "obs_{:04d}".format(start), "end_id": "obs_{:04d}".format(end),
            "evidence_ids": ["obs_{:04d}".format(start)], "disposition": disposition,
            "reason": reason}


def closes_visible_sets(first, last, count=2, pad="", games=3):
    """Propose every set this window can see whole -- the honest model's answer."""
    _, spans = build_sets(count, pad=pad, games=games)
    return [proposal(start, end) for start, end in spans
            if start >= first and end <= last]


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeModel:
    """Answers each window from ``plan(first, last)``, recording the prompt it was actually sent."""

    def __init__(self, plan=None, tokens=13, broken=0):
        self.plan = plan or (lambda first, last: [])
        self.tokens = tokens
        self.broken = broken  # how many leading requests answer with unusable text
        self.windows = []

    def __call__(self, request, timeout=None):
        messages = json.loads(request.data.decode("utf-8"))["messages"]
        prompt, system = messages[1]["content"], messages[0]["content"]
        ids = [o["observation_id"]
               for o in json.loads(prompt.rsplit("Data:\n", 1)[1])["observations"]]
        self.windows.append({"first": int(ids[0][4:]), "last": int(ids[-1][4:]), "ids": ids,
                             "user_chars": len(prompt), "system_chars": len(system)})
        if len(self.windows) <= self.broken:
            content = "I could not decide, so here is prose instead of JSON"
        else:
            content = json.dumps(self.plan(int(ids[0][4:]), int(ids[-1][4:])))
        return _Response({"choices": [{"message": {"content": content}}],
                          "usage": {"total_tokens": self.tokens}})


@pytest.fixture
def serve(monkeypatch):
    def install(model):
        monkeypatch.setattr("urllib.request.urlopen", model)
        return model
    return install


@pytest.fixture
def tiny_policy(tmp_path):
    """A short stand-in policy, so a window's observation budget is predictable in tests."""
    path = pathlib.Path(tmp_path) / "policy.md"
    path.write_text("# Rules\n", encoding="utf-8")
    return str(path)


def write_timeline(tmp_path, rows, name="timeline-refined.json"):
    path = pathlib.Path(tmp_path) / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def review_items(result):
    return read_json(pathlib.Path(result["output_dir"]) / "review.json")["items"]


def predictions(result):
    return read_json(pathlib.Path(result["output_dir"]) / "predictions.json")


def recover(timeline, out, *, cap=9000, overlap=2, requests=4, resume=False, duration=600.0,
            source_id="yt:BB9TEST", policy_path=None):
    return boundary_recovery.adjudicate_saved_timeline(
        timeline, out, source_id=source_id, duration=duration,
        event_name="Basement Brawl #9 (test)", requests=requests, max_input_chars=cap,
        overlap=overlap, resume=resume, policy_path=policy_path)


def assert_every_set_is_published_or_explained(result, rows, spans):
    """The core honesty check: recovery may withhold a set, never lose track of one."""
    published = set(spans_as_times(rows, spans)) & {(p["start"], p["end"])
                                                     for p in predictions(result)}
    items = review_items(result)
    for start, end in spans_as_times(rows, spans):
        if (start, end) in published:
            continue
        start_index = next(i for i, row in enumerate(rows) if row["timestamp"] == start)
        start_id = "obs_{:04d}".format(start_index)
        assert any(item.get("start_id") == start_id or item.get("start") == start
                   or (item.get("candidates") and
                       any(c.get("start_id") == start_id or c.get("end") == end
                           for c in item["candidates"]))
                   for item in items), "silently dropped {}-{}".format(start, end)


# ---------- recovery does no vision or media work ----------

def test_recovery_module_never_names_the_media_or_vision_apis():
    source = pathlib.Path(boundary_recovery.__file__).read_text(encoding="utf-8")
    for forbidden in ("VisionClient", "sample_frames", "resolve_source", "probe_duration",
                      "ffmpeg", "ffprobe"):
        assert forbidden not in source


def test_recovery_touches_no_media_api(tmp_path, serve, monkeypatch):
    def trip(*args, **kwargs):
        raise AssertionError("recovery must not use the media layer")
    for name in ("resolve_source", "sample_frames", "probe_duration", "VisionClient"):
        monkeypatch.setattr(boundary_media, name, trip)
        monkeypatch.setattr(boundary_workflow, name, trip)
    serve(FakeModel(closes_visible_sets))
    result = recover(write_timeline(tmp_path, sets_timeline()), tmp_path / "recovered", cap=30_000)
    assert result["status"] == "complete"
    assert result["vision_model_calls"] == 0
    assert result["recovered_from"]["source_video"] == ""
    assert not (pathlib.Path(result["output_dir"]) / "frames").exists()


def test_plan_reports_windows_sizes_coverage_and_allowance_without_requesting(tmp_path, serve):
    model = serve(FakeModel())
    plan = boundary_recovery.plan_saved_timeline(
        write_timeline(tmp_path, sets_timeline(2, pad=PAD)),
        duration=600.0, max_input_chars=9000, overlap=3)
    assert model.windows == []
    assert plan["status"] == "plan"
    assert plan["request_allowance"] == DEFAULT_ADJUDICATION_REQUESTS
    assert plan["observations"] == len(sets_timeline(2))
    assert plan["window_count"] > 1
    assert plan["core_coverage_complete"] is True
    assert plan["prompt_chars_max"] <= plan["max_input_chars"]
    assert plan["timeline_sha256"] and plan["policy_hash"]
    assert any("refinement" in note.lower() for note in plan["notes"])


def test_dry_run_reports_the_plan_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from tekken_vod_helper import boundary_cli

    def no_network(*args, **kwargs):
        raise AssertionError("a dry run must not open a connection")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    timeline = write_timeline(tmp_path, sets_timeline(2, pad=PAD))
    out = tmp_path / "should-not-exist"
    boundary_cli.main(["adjudicate-timeline", "--timeline", str(timeline),
                       "--output-dir", str(out), "--source-id", "yt:BB9TEST",
                       "--duration", "6928.421136", "--dry-run",
                       "--max-input-chars", "9000", "--window-overlap", "3"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "plan" and plan["window_count"] > 1
    assert plan["observations"] == len(sets_timeline(2))
    assert not out.exists()


def test_the_cli_recovers_a_saved_timeline_end_to_end(tmp_path, serve, capsys):
    from tekken_vod_helper import boundary_cli
    serve(FakeModel(closes_visible_sets))
    timeline = write_timeline(tmp_path, sets_timeline())
    out = tmp_path / "recovered"
    boundary_cli.main(["adjudicate-timeline", "--timeline", str(timeline),
                       "--output-dir", str(out), "--source-id", "yt:BB9TEST",
                       "--event", "Basement Brawl #9", "--duration", "200",
                       "--provenance", json.dumps({"frame_cache": "build/work/bb9/keep"}),
                       "--max-input-chars", "30000"])
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "complete" and result["match_count"] == 2
    assert result["recovered_from"]["provenance"]["frame_cache"] == "build/work/bb9/keep"
    assert (out / "predictions.json").is_file()


# ---------- bounded prompts and stable ids ----------

def test_windows_stay_under_the_cap_and_cover_every_observation_once():
    rows = sets_timeline(3, pad=PAD)
    windows = plan_windows(build_observations(rows), POLICY, max_input_chars=12_000,
                           ratio=0.8, overlap=4)
    assert len(windows) > 1
    for window in windows:
        assert window["prompt_chars"] <= 12_000
        # System text, the policy and the JSON framing are all charged inside the window.
        assert window["prompt_chars"] > len(POLICY)
    cores = [(w["core_start"], w["core_end"]) for w in windows]
    assert cores[0][0] == 0 and cores[-1][1] == len(rows)
    assert [b for (_, a), (b, _) in zip(cores, cores[1:])] == [a for a, _ in cores[1:]]
    assert MAX_INPUT_CHARS == 500_000, "the existing cap stays the default ceiling"


def test_window_boundaries_are_placed_between_sets_when_the_data_shows_one():
    rows = sets_timeline(4, pad=PAD, games=1)  # clean prompt-then-grid breaks every 4 rows
    observations = build_observations(rows)
    windows = plan_windows(observations, "policy", max_input_chars=9000, ratio=0.8, overlap=1)
    assert len(windows) > 1
    for window in windows[:-1]:
        assert window["break_snapped"] is True
        assert window["break_kind"] == "new_pair"
        assert rows[window["core_end"] - 1]["state"] == "controller_prompt"
        assert rows[window["core_end"]]["state"] == "stage_select"
        ending = {rows[window["core_end"] - 1][k] for k in ("player1", "player2")}
        opening = {rows[window["core_end"]][k] for k in ("player1", "player2")}
        assert ending != opening, "a boundary is only clean when the pairing changes"


def test_a_grid_inside_one_set_is_not_a_boundary_even_after_a_rematch_menu():
    """The mistake the old rule made on BB9: a between-game stage vote is not a new set."""
    same = ("Ann", "Zoe")
    states = (["stage_select"] + ["gameplay"] * 6 + ["controller_prompt"]) * 2
    rows = [obs_row(i, state, evidence="y" * 900, pair=same) for i, state in enumerate(states)]
    windows = plan_windows(build_observations(rows), POLICY, max_input_chars=9000, overlap=6)
    assert len(windows) > 1, "these rows must be big enough to force a split"
    for window in windows[:-1]:
        assert not window["break_snapped"], "cutting here would split a set in progress"


def test_a_renamed_head_is_the_same_player_not_a_new_set():
    """BB9 read one gamer tag as both "Spitfire2929" and "Spire2929"; that is not a bracket change."""
    rows = ([obs_row(i, state, pair=("Spitfire2929", "derpingtoast")) for i, state in enumerate(
                ["stage_select"] + ["gameplay"] * 5)] +
            [obs_row(6, "controller_prompt", pair=("Spitfire2929", "derpingtoast"))] +
            [obs_row(i, state, pair=("Spire2929", "derpingtoast")) for i, state in enumerate(
                ["gameplay"] * 5, start=7)] +
            [obs_row(12, "stage_select", pair=("Spitfire2929", "derpingtoast")),
             obs_row(13, "gameplay", pair=("Spitfire2929", "derpingtoast"))])
    observations = build_observations(rows)
    from tekken_vod_helper.boundary_reasoning import _clean_break
    assert _clean_break(observations, 12) is None, "OCR drift must not open a set"
    # A genuinely different opponent still is one.
    rows[12]["player2"] = "Rebix"
    assert _clean_break(build_observations(rows), 12) == "new_pair"


def test_a_split_with_no_visible_boundary_between_sets_is_reported():
    rows = rows_from(["stage_select"] + ["gameplay"] * 30 + ["controller_prompt"])
    observations = build_observations(rows)
    windows = plan_windows(observations, "policy", max_input_chars=2600, ratio=0.8, overlap=1)
    assert len(windows) > 1
    assert any(not w["break_snapped"] for w in windows[:-1])
    adjudicator = TemporalAdjudicator("http://unused", "model")
    flags = [i for i in adjudicator._coverage_reviews(windows)
             if i["type"] == "set_may_cross_window_boundary"]
    assert len(flags) == sum(1 for w in windows[:-1] if not w["break_snapped"])
    assert flags[0]["disposition"] == "uncertain"


def test_a_timeline_that_fits_one_window_keeps_the_single_shot_prompt_bytes():
    rows = sets_timeline()
    observations = build_observations(rows)
    adjudicator = TemporalAdjudicator("http://unused", "model")
    single_shot = adjudicator._user_content(observations, POLICY)
    _, windows = adjudicator.plan(rows, POLICY)
    assert len(windows) == 1
    assert windows[0]["note"] == "", "one window must not be told it is a window"
    assert windows[0]["obs_start"] == 0 and windows[0]["obs_end"] == len(rows)
    assert adjudicator._user_content(observations, POLICY, windows[0]["note"]) == single_shot


def test_every_window_sees_global_ids_with_no_gap_or_invention(tmp_path, serve, tiny_policy):
    rows = sets_timeline(2, pad=PAD)
    model = serve(FakeModel())
    recover(write_timeline(tmp_path, rows), tmp_path / "recovered", requests=8,
            policy_path=tiny_policy)
    assert len(model.windows) > 1
    covered = set()
    for window in model.windows:
        assert window["ids"] == ["obs_{:04d}".format(i)
                                 for i in range(window["first"], window["last"] + 1)]
        assert window["system_chars"] == model.windows[0]["system_chars"]
        covered.update(range(window["first"], window["last"] + 1))
    assert covered == set(range(len(rows)))
    assert all(w["user_chars"] < 9000 for w in model.windows)


def test_an_observation_too_large_for_any_window_is_reported_not_dropped():
    huge = rows_from(["gameplay"])
    huge[0]["evidence"] = "x" * 40_000
    with pytest.raises(Exception) as raised:
        plan_windows(build_observations(huge), POLICY, max_input_chars=9_000, overlap=1)
    assert "window budget" in str(raised.value)


def test_a_timeline_too_big_for_one_prompt_is_split_where_the_single_shot_refused():
    """The saved BB9 run died at 757,243 chars against a 500,000 guard; that is now a split."""
    rows = sets_timeline(400, pad="v" * 300)
    observations = build_observations(rows)
    adjudicator = TemporalAdjudicator("http://unused", "model")
    single_shot = adjudicator._user_content(observations, POLICY)
    assert len(single_shot) + 2101 > MAX_INPUT_CHARS, "fixture must outgrow one prompt"
    with pytest.raises(Exception) as refused:
        adjudicator.propose(rows, POLICY)
    assert "Input too large" in str(refused.value)
    windows = plan_windows(observations, POLICY)
    assert len(windows) > 1
    assert all(w["prompt_chars"] <= MAX_INPUT_CHARS for w in windows)
    assert sum(w["core_count"] for w in windows) == len(rows)


# ---------- reconciliation ----------

def window(index, obs_start, obs_end, core_start, core_end, total):
    return {"index": index, "obs_start": obs_start, "obs_end": obs_end,
            "core_start": core_start, "core_end": core_end,
            "context_before": core_start - obs_start,
            "context_after": obs_end - core_end,
            "context_shortfall": 0, "prompt_chars": 1000,
            "start_timestamp": float(obs_start * 10), "end_timestamp": float(obs_end * 10 - 10),
            "first_observation_id": "obs_{:04d}".format(obs_start),
            "last_observation_id": "obs_{:04d}".format(obs_end - 1),
            "status": "ok", "calls": 1, "attempts": [], "error": None, "note": ""}


def reconcile(rows, results):
    adjudicator = TemporalAdjudicator("http://unused", "model")
    observations = build_observations(rows)
    by_id = {o["observation_id"]: o for o in observations}
    order = {o["observation_id"]: i for i, o in enumerate(observations)}
    matches, review, summary = adjudicator._reconcile(results, observations, by_id, order)
    return matches, review, summary, adjudicator, observations


# A set closed by a mid-set rematch menu at index 3 and really ended by the prompt at index 5.
MENU = rows_from(["stage_select", "gameplay", "gameplay", "controller_prompt",
                  "gameplay", "controller_prompt",
                  "stage_select", "gameplay", "controller_prompt"])


def test_a_set_crossing_a_split_is_taken_from_the_window_that_saw_it_finish():
    results = [
        # Window 0 stops at the mid-set menu and calls it a finished set.
        {"window": window(0, 0, 4, 0, 3, len(MENU)),
         "proposals": [proposal(0, 3, reason="window ends here")], "failed": False},
        # Window 1 carries the set in its leading context and can see the closing prompt.
        {"window": window(1, 0, len(MENU), 4, len(MENU), len(MENU)),
         "proposals": [proposal(0, 5, reason="this prompt closes the set")], "failed": False},
    ]
    matches, review, summary, _, _ = reconcile(MENU, results)
    assert [(m["start"], m["end"]) for m in matches] == [(0.0, 50.0)]
    assert matches[0]["adjudication_windows"] == [1]
    assert summary["conflicts"] == 0 and summary["edge_unverified_starts"] == 0
    assert not [i for i in review if i["type"] in ("conflicting_boundaries",
                                                   "window_edge_endpoint_unverified")]


def test_a_set_longer_than_the_overlap_stays_uncertain_instead_of_inventing_an_end():
    results = [
        {"window": window(0, 0, 4, 0, 3, len(MENU)),
         "proposals": [proposal(0, 3)], "failed": False},
        # The neighbour window starts after the set did, so no window can see it whole.
        {"window": window(1, 4, len(MENU), 4, len(MENU), len(MENU)),
         "proposals": [], "failed": False},
    ]
    matches, review, summary, _, _ = reconcile(MENU, results)
    assert matches == []
    edge = [i for i in review if i["type"] == "window_edge_endpoint_unverified"]
    assert len(edge) == 1 and edge[0]["disposition"] == "uncertain"
    assert edge[0]["start_id"] == "obs_0000"
    assert summary["edge_unverified_starts"] == 1


def test_exact_agreements_are_deduplicated_and_conflicts_are_reviewed():
    results = [
        {"window": window(0, 0, 6, 0, 4, len(MENU)), "proposals": [proposal(0, 5)], "failed": False},
        {"window": window(1, 4, len(MENU), 6, len(MENU), len(MENU)),
         "proposals": [proposal(0, 5), proposal(6, 8), proposal(6, 7)], "failed": False},
    ]
    matches, review, summary, _, _ = reconcile(MENU, results)
    assert [(m["start"], m["end"]) for m in matches] == [(0.0, 50.0)]
    assert matches[0]["adjudication_windows"] == [0, 1], "agreement must count once"
    conflicts = [i for i in review if i["type"] == "conflicting_boundaries"]
    assert len(conflicts) == 1
    assert {c["end_id"] for c in conflicts[0]["candidates"]} == {"obs_0007", "obs_0008"}
    assert conflicts[0]["start_id"] == "obs_0006"
    assert summary["conflicts"] == 1
    assert summary["adopted"] == 1


def test_the_better_supported_endpoint_wins_and_the_other_is_recorded():
    results = [
        {"window": window(0, 0, 6, 0, 5, len(MENU)), "proposals": [proposal(0, 3)], "failed": False},
        {"window": window(1, 0, 7, 6, 7, len(MENU)), "proposals": [proposal(0, 5)], "failed": False},
        {"window": window(2, 4, len(MENU), 7, len(MENU), len(MENU)),
         "proposals": [proposal(0, 5)], "failed": False},
    ]
    matches, review, summary, _, _ = reconcile(MENU, results)
    assert [(m["start"], m["end"]) for m in matches] == [(0.0, 50.0)]
    superseded = [i for i in review if i["type"] == "superseded_endpoint"]
    assert len(superseded) == 1 and superseded[0]["end_id"] == "obs_0003"
    assert summary["conflicts"] == 0


def test_windows_disagreeing_about_a_spans_disposition_retain_it_for_review():
    results = [
        {"window": window(0, 0, 7, 0, 6, len(MENU)),
         "proposals": [proposal(0, 5, reason="a set was played")], "failed": False},
        {"window": window(1, 0, len(MENU), 6, len(MENU), len(MENU)),
         "proposals": [proposal(0, 5, disposition="uncertain", reason="cannot tell")],
         "failed": False},
    ]
    matches, review, summary, _, _ = reconcile(MENU, results)
    assert matches == [], "the optimistic reading must not silently win a disposition argument"
    conflicts = [i for i in review if i["type"] == "conflicting_dispositions"]
    assert len(conflicts) == 1 and conflicts[0]["disposition"] == "uncertain"
    assert {c["disposition"] for c in conflicts[0]["candidates"]} == {"candidate", "uncertain"}
    assert summary["conflicts"] == 1
    retained = [i for i in review if i["type"] == "temporal_review"]
    assert len(retained) == 1 and retained[0]["start_id"] == "obs_0000"


def test_overlapping_adopted_proposals_are_both_withheld():
    rows = rows_from(["stage_select", "gameplay", "controller_prompt",
                      "stage_select", "gameplay", "controller_prompt",
                      "gameplay", "controller_prompt"])
    results = [{"window": window(0, 0, len(rows), 0, len(rows), len(rows)),
                "proposals": [proposal(0, 5), proposal(3, 7)], "failed": False}]
    matches, review, summary, _, _ = reconcile(rows, results)
    assert matches == []
    assert len([i for i in review if i["type"] == "conflicting_candidates"]) == 1
    assert summary["conflicts"] == 1


def test_a_proposal_outside_its_own_window_is_rejected_not_trusted(tmp_path, serve, tiny_policy):
    """A window may only cite observations it was actually shown."""
    rows = sets_timeline(2, pad=PAD)
    timeline = write_timeline(tmp_path, rows)
    serve(FakeModel(lambda first, last: [] if first == 0 else [proposal(0, last)]))
    result = recover(timeline, tmp_path / "recovered", requests=8, overlap=0,
                     policy_path=tiny_policy)
    rejected = [i for i in review_items(result) if i["type"] == "rejected_proposal"]
    assert rejected and "outside this window" in rejected[0]["reason"]
    assert result["status"] == "complete", "one bad proposal must not fail the whole recovery"
    assert result["adjudication"]["reconciliation"]["rejected_proposals"] >= 1
    assert predictions(result) == []


def test_play_that_never_closes_at_the_end_of_the_data_is_reported_not_cut():
    rows = MENU + rows_from(["gameplay", "gameplay"], start_index=len(MENU))
    results = [{"window": window(0, 0, len(rows), 0, len(rows), len(rows)),
                "proposals": [proposal(0, 5)], "failed": False}]
    matches, _, _, adjudicator, observations = reconcile(rows, results)
    unterminated = adjudicator._final_play_review(observations, matches)
    assert len(unterminated) == 1
    assert unterminated[0]["start"] == 50.0
    assert unterminated[0]["end"] == observations[-1]["timestamp"]


def test_closed_final_play_needs_no_unterminated_flag():
    results = [{"window": window(0, 0, len(MENU), 0, len(MENU), len(MENU)),
                "proposals": [proposal(0, 5)], "failed": False}]
    matches, _, _, adjudicator, observations = reconcile(MENU, results)
    assert adjudicator._final_play_review(observations, matches) == []


# ---------- budgets, failures, resume ----------

def test_exhausted_allowance_leaves_an_incomplete_run_that_resumes_without_reasking(tmp_path,
                                                                                   serve,
                                                                                   tiny_policy):
    rows, spans = build_sets(3, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    plan = boundary_recovery.plan_saved_timeline(timeline, duration=600.0, max_input_chars=9000,
                                                 overlap=2, policy_path=tiny_policy)
    assert plan["window_count"] > 2
    model = serve(FakeModel(lambda first, last: closes_visible_sets(first, last, count=3,
                                                                    pad=PAD, games=1)))
    out = tmp_path / "partial"
    with pytest.raises(ValueError) as raised:
        recover(timeline, out, requests=1, policy_path=tiny_policy)
    assert "no usable proposals" in str(raised.value)
    assert "never attempted" in str(raised.value)
    run = read_json(out / "run.json")
    assert run["status"] == "incomplete" and "no usable proposals" in run["error"]
    assert run["adjudication"]["model_calls"] == 1
    assert run["adjudication"]["budget_exhausted"] is True
    assert not (out / "predictions.json").exists()
    assert not (out / "project.tvh.json").exists()
    assert (out / "review.json").is_file(), "an incomplete run must still explain itself"
    assert len([i for i in review_items(run) if i["type"] == "adjudication_window_failed"]) == \
        plan["window_count"] - 1
    statuses = {p.name: read_json(p)["status"] for p in (out / "adjudication").glob("chunk-*.json")}
    assert statuses["chunk-0000.json"] == "ok"
    assert set(statuses.values()) == {"ok", "not_attempted"}

    model.windows.clear()
    result = recover(timeline, out, requests=plan["window_count"] + 2, resume=True,
                     policy_path=tiny_policy)
    assert result["status"] == "complete"
    assert result["adjudication"]["resumed_windows"] == [0]
    assert len(model.windows) == result["adjudication"]["window_count"] - 1
    assert result["adjudication"]["model_calls"] == len(model.windows)
    assert result["adjudication"]["usage"]["total_tokens"] == model.tokens * len(model.windows)
    assert_every_set_is_published_or_explained(result, rows, spans)
    assert {(p["start"], p["end"]) for p in predictions(result)} == set(spans_as_times(rows, spans))


def test_a_failed_window_keeps_its_raw_reply_and_is_never_replayed_as_success(tmp_path, serve,
                                                                             tiny_policy):
    rows, _ = build_sets(3, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    serve(FakeModel(broken=99))
    out = tmp_path / "broken"
    with pytest.raises(ValueError):
        recover(timeline, out, requests=2, policy_path=tiny_policy)
    chunks = sorted((out / "adjudication").glob("chunk-*.json"))
    assert chunks
    record = read_json(chunks[0])
    assert record["status"] == "failed" and record["proposals"] == []
    assert "not valid JSON" in record["error"]
    assert "prose instead of JSON" in record["raw_response"]
    assert record["window"]["core_start"] == 0
    assert {read_json(p)["status"] for p in chunks} <= {"failed", "not_attempted"}
    assert boundary_recovery.TemporalAdjudicator._load_chunk(
        chunks[0], record["fingerprint"]) is None


def test_a_lost_connection_is_not_retried_because_the_server_may_still_be_working(tmp_path,
                                                                                 tiny_policy,
                                                                                 monkeypatch):
    import socket
    import urllib.error

    def timeout(*args, **kwargs):
        raise urllib.error.URLError(reason=socket.timeout("timed out"))
    monkeypatch.setattr("urllib.request.urlopen", timeout)
    rows, _ = build_sets(2, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    out = tmp_path / "timed-out"
    with pytest.raises(ValueError) as raised:
        recover(timeline, out, requests=6, overlap=2, policy_path=tiny_policy)
    message = str(raised.value)
    assert "not retried automatically" in message or "may still be" in message
    run = read_json(out / "run.json")
    assert run["status"] == "incomplete"
    # One attempt per window and no more: a retry would queue a second job behind a first that may
    # still be running on a single-purpose box.
    assert run["adjudication"]["model_calls"] == run["adjudication"]["window_count"]
    assert run["adjudication"]["timed_out_windows"] == \
        list(range(run["adjudication"]["window_count"]))
    assert [len(w["attempts"]) for w in run["adjudication"]["windows"]] == \
        [1] * run["adjudication"]["window_count"]
    record = read_json(sorted((out / "adjudication").glob("chunk-*.json"))[0])
    assert record["status"] == "unresolved_timeout" and record["proposals"] == []


def test_a_refused_endpoint_proves_nothing_started_so_it_is_still_retried(tmp_path, tiny_policy,
                                                                         monkeypatch):
    import urllib.error
    attempts = []

    def refused(*args, **kwargs):
        attempts.append(True)
        raise urllib.error.URLError(reason=ConnectionRefusedError("Connection refused"))
    monkeypatch.setattr("urllib.request.urlopen", refused)
    rows, _ = build_sets(2, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    with pytest.raises(ValueError):
        recover(timeline, tmp_path / "refused", requests=6, overlap=2, policy_path=tiny_policy)
    assert len(attempts) > 1, "a refused socket cannot have queued work, so retrying is safe"
    run = read_json(tmp_path / "refused" / "run.json")
    assert run["adjudication"]["timed_out_windows"] == []
    assert all(read_json(p)["status"] == "failed"
               for p in (tmp_path / "refused" / "adjudication").glob("chunk-*.json"))


def test_only_connection_losses_that_could_have_queued_work_suppress_a_retry():
    import http.client
    import socket
    import urllib.error
    from tekken_vod_helper.boundary_reasoning import request_may_still_be_running as may_still_run

    assert may_still_run(socket.timeout())
    assert may_still_run(TimeoutError())
    assert may_still_run(urllib.error.URLError(reason=socket.timeout()))
    assert may_still_run(urllib.error.URLError(reason=ConnectionResetError()))
    assert may_still_run(http.client.RemoteDisconnected("closed"))
    assert may_still_run(http.client.IncompleteRead(b"partial"))
    assert not may_still_run(urllib.error.URLError(reason=ConnectionRefusedError()))
    assert not may_still_run(urllib.error.URLError(reason=OSError("Network is unreachable")))
    assert not may_still_run(urllib.error.HTTPError("http", 503, "busy", {}, None))
    assert not may_still_run(json.JSONDecodeError("x", "y", 0))


class ReasoningStarvationModel(FakeModel):
    """What the Mac actually returned on 2026-10-28: all 12k tokens reasoning, no content."""

    def __init__(self, budget):
        super().__init__()
        self.budget = budget
        self.seen = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        self.seen.append(body["max_tokens"])
        self.windows.append({"first": 0, "last": 0, "ids": ["obs_0000"], "user_chars": 0,
                             "system_chars": 0})
        return _Response({"choices": [{"message": {"content": "", "role": "assistant",
                                                   "reasoning_content": "thinking " * 500},
                                       "finish_reason": "length"}],
                          "usage": {"prompt_tokens": 269929, "completion_tokens": self.budget,
                                    "total_tokens": 269929 + self.budget,
                                    "completion_tokens_details": {"reasoning_tokens": self.budget}}})


def test_a_reply_that_ran_out_of_completion_budget_is_not_retried(tmp_path, serve, tiny_policy):
    model = serve(ReasoningStarvationModel(12000))
    rows, _ = build_sets(2, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    out = tmp_path / "starved"
    with pytest.raises(ValueError) as raised:
        recover(timeline, out, requests=6, overlap=2, policy_path=tiny_policy)
    run = read_json(out / "run.json")
    windows = run["adjudication"]["windows"]
    assert len(model.windows) == len(windows), "one call per window, and never a second for the same"
    assert [len(w["attempts"]) for w in windows] == [1] * len(windows)
    assert "completion budget" in str(raised.value)
    assert "--adjudication-max-tokens" in str(raised.value)
    assert run["adjudication"]["truncated_windows"] == list(range(len(windows)))
    assert run["adjudication"]["model_calls"] == len(windows)
    assert all(w["status"] == "unresolved_truncation" for w in windows)
    attempt = run["adjudication"]["attempts"][0]
    assert attempt["finish_reason"] == "length" and attempt["reasoning_tokens"] == 12000


def test_the_completion_budget_reaches_the_request_and_can_be_raised(tmp_path, serve, tiny_policy):
    model = serve(ReasoningStarvationModel(DEFAULT_COMPLETION_TOKENS))
    timeline = write_timeline(tmp_path, build_sets(1)[0], name="one.json")
    with pytest.raises(ValueError):
        boundary_recovery.adjudicate_saved_timeline(timeline, tmp_path / "a", source_id="yt:X",
                                                    duration=600.0, requests=2,
                                                    max_input_chars=30_000,
                                                    policy_path=tiny_policy)
    assert model.seen == [DEFAULT_COMPLETION_TOKENS]
    model = serve(ReasoningStarvationModel(90000))
    with pytest.raises(ValueError):
        boundary_recovery.adjudicate_saved_timeline(timeline, tmp_path / "b", source_id="yt:X",
                                                    duration=600.0, requests=2,
                                                    max_input_chars=30_000,
                                                    max_completion_tokens=90000,
                                                    policy_path=tiny_policy)
    assert model.seen == [90000]


def test_each_attempt_keeps_its_own_raw_reply(tmp_path, serve):
    timeline = write_timeline(tmp_path, sets_timeline())
    serve(FakeModel(closes_visible_sets, broken=1))
    recover(timeline, tmp_path / "raw", cap=30_000, requests=4)
    files = sorted(p.name for p in (tmp_path / "raw").glob("adjudication-response*"))
    assert len(files) == 2, "two calls must not overwrite each other's evidence"


def test_play_that_no_proposal_mentions_is_flagged_instead_of_looked_over():
    """BB9 shape: a set begins straight from gameplay, the model omits it, and the run says complete."""
    rows = (rows_from(["stage_select", "gameplay", "gameplay", "controller_prompt"]) +
            rows_from(["gameplay", "gameplay", "gameplay"], start_index=4) +
            rows_from(["stage_select", "gameplay", "controller_prompt"], start_index=7))
    results = [{"window": window(0, 0, len(rows), 0, len(rows), len(rows)),
                "proposals": [proposal(0, 3), proposal(7, 9)], "failed": False}]
    matches, review, summary, _, _ = reconcile(rows, results)
    gaps = [i for i in review if i["type"] == "unproposed_play"]
    assert len(gaps) == 1
    assert gaps[0]["start"] == 40.0 and gaps[0]["end"] == 60.0
    assert gaps[0]["gameplay_observations"] == 3
    assert gaps[0]["disposition"] == "uncertain"
    assert summary["unproposed_play_gaps"] == 1
    assert summary["unproposed_play_rows"] == 3


def test_play_inside_an_uncertain_proposal_counts_as_examined():
    rows = (rows_from(["stage_select", "gameplay", "controller_prompt"]) +
            rows_from(["gameplay", "gameplay", "controller_prompt"], start_index=3))
    results = [{"window": window(0, 0, len(rows), 0, len(rows), len(rows)),
                "proposals": [proposal(0, 2), proposal(3, 5, disposition="uncertain")],
                "failed": False}]
    _, review, summary, _, _ = reconcile(rows, results)
    assert [i for i in review if i["type"] == "unproposed_play"] == []
    assert summary["unproposed_play_gaps"] == 0


def test_menus_between_uncovered_games_yield_one_flag_not_several():
    """A set's own menus sit between its games; uncovered play either side is one stretch."""
    rows = rows_from(["stage_select", "gameplay", "controller_prompt",
                      "gameplay", "character_select", "vs_screen",
                      "gameplay", "controller_prompt"])
    results = [{"window": window(0, 0, 3, 0, 3, len(rows)),
                "proposals": [proposal(0, 2)], "failed": False}]
    _, review, summary, _, _ = reconcile(rows, results)
    gaps = [i for i in review if i["type"] == "unproposed_play"]
    assert len(gaps) == 1, [g["start"] for g in gaps]
    assert gaps[0]["start"] == 30.0 and gaps[0]["end"] == 60.0
    assert gaps[0]["gameplay_observations"] == 2
    assert summary["unproposed_play_gaps"] == 1


def test_a_missed_set_with_long_between_game_menus_is_one_flag():
    """BB9's missed Winners Final had 25 s and 160 s menu gaps inside it; that is one stretch."""
    rows = rows_from(["stage_select", "controller_prompt"])
    for i, ts in ((2, 20.0), (3, 45.0), (4, 70.0), (5, 230.0)):
        rows.append(obs_row(i, "gameplay", ts))
    rows.append(obs_row(6, "controller_prompt", 300.0))
    results = [{"window": window(0, 0, 2, 0, 2, len(rows)),
                "proposals": [proposal(0, 1)], "failed": False}]
    _, review, summary, _, _ = reconcile(rows, results)
    gaps = [i for i in review if i["type"] == "unproposed_play"]
    assert len(gaps) == 1
    assert gaps[0]["start"] == 20.0 and gaps[0]["end"] == 230.0
    assert summary["unproposed_play_gaps"] == 1


def test_review_flags_carry_timestamps_a_human_can_navigate_to():
    rows = rows_from(["stage_select", "gameplay", "gameplay", "controller_prompt"])
    results = [{"window": window(0, 0, len(rows), 0, len(rows), len(rows)),
                "proposals": [proposal(0, 3, disposition="uncertain", reason="cannot tell")],
                "failed": False}]
    _, review, _, _, _ = reconcile(rows, results)
    flagged = [i for i in review if i["type"] == "temporal_review"]
    assert len(flagged) == 1
    assert (flagged[0]["start"], flagged[0]["end"]) == (0.0, 30.0)


def test_malformed_reply_is_retried_inside_the_allowance(tmp_path, serve):
    timeline = write_timeline(tmp_path, sets_timeline())
    serve(FakeModel(closes_visible_sets, broken=1, tokens=5))
    result = recover(timeline, tmp_path / "retried", cap=30_000, requests=4)
    assert result["status"] == "complete"
    assert result["adjudication"]["model_calls"] == 2, "one broken reply and its retry"
    assert result["adjudication_model_calls"] == 2
    assert result["adjudication"]["usage"] == {"total_calls": 2, "total_tokens": 10}
    assert result["adjudication"]["windows"][0]["calls"] == 2


def test_resume_refuses_a_chunk_cache_from_different_inputs(tmp_path, serve):
    rows = sets_timeline()
    timeline = write_timeline(tmp_path, rows)
    serve(FakeModel(closes_visible_sets))
    out = tmp_path / "recovered"
    recover(timeline, out, cap=30_000)
    changed = write_timeline(tmp_path, rows + [obs_row(len(rows), "gameplay", 400.0)],
                             name="changed.json")
    with pytest.raises(ValueError) as raised:
        recover(changed, out, cap=30_000, duration=700.0, resume=True)
    assert "different inputs" in str(raised.value)
    assert "timeline_sha256" in str(raised.value) and "timeline_rows" in str(raised.value)


def test_resume_refuses_a_chunk_cache_from_another_model_or_policy(tmp_path, serve, tiny_policy):
    rows, _ = build_sets(2, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    serve(FakeModel(lambda first, last: closes_visible_sets(first, last, count=2,
                                                            pad=PAD, games=1)))
    out = tmp_path / "recovered"
    recover(timeline, out, requests=8, policy_path=tiny_policy)
    with pytest.raises(ValueError) as raised:
        boundary_recovery.adjudicate_saved_timeline(timeline, out, source_id="yt:BB9TEST",
                                                    duration=600.0, model="another-model",
                                                    requests=8, max_input_chars=9000, overlap=2,
                                                    resume=True, policy_path=tiny_policy)
    assert "different inputs" in str(raised.value) and "model" in str(raised.value)
    other_policy = pathlib.Path(tmp_path) / "other-policy.md"
    other_policy.write_text("# Different rules\n", encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        recover(timeline, out, requests=8, policy_path=str(other_policy), resume=True)
    assert "policy_hash" in str(raised.value)


def test_a_different_model_cannot_reuse_saved_chunks_even_in_the_same_directory(tmp_path,
                                                                                tiny_policy,
                                                                                monkeypatch):
    rows, _ = build_sets(2, pad=PAD, games=1)
    policy = pathlib.Path(tiny_policy).read_text(encoding="utf-8")
    chunk_dir = str(tmp_path / "chunks")
    answer = lambda first, last: closes_visible_sets(first, last, count=2, pad=PAD, games=1)

    def run(model_name, responder):
        monkeypatch.setattr("urllib.request.urlopen", responder)
        adjudicator = TemporalAdjudicator("http://unused", model_name, max_requests=20,
                                          max_input_chars=9000, overlap_observations=2,
                                          chunk_dir=chunk_dir)
        return adjudicator.propose_windowed(rows, policy)

    first = FakeModel(answer)
    run("model-one", first)
    assert len(first.windows) > 1
    second = FakeModel(answer)
    _, _, provenance = run("model-two", second)
    assert len(second.windows) == len(first.windows), "no chunk may be reused for another model"
    assert provenance["resumed_windows"] == []
    # Same model again: everything comes from disk with no requests at all.
    third = FakeModel()
    _, _, again = run("model-two", third)
    assert third.windows == []
    assert again["resumed_windows"] == list(range(again["window_count"]))


def test_resume_needs_the_directory_a_partial_recovery_left_behind(tmp_path, serve):
    timeline = write_timeline(tmp_path, sets_timeline())
    serve(FakeModel())
    with pytest.raises(ValueError) as raised:
        recover(timeline, tmp_path / "fresh", resume=True)
    assert "partial recovery" in str(raised.value)
    out = pathlib.Path(tmp_path / "cluttered")
    out.mkdir()
    (out / "stray.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        recover(timeline, out)
    assert "not empty" in str(raised.value)
    with pytest.raises(ValueError) as raised:
        recover(timeline, out, resume=True)
    assert "chunks" in str(raised.value)


def test_an_unusable_saved_timeline_is_refused_before_any_request(tmp_path, serve):
    model = serve(FakeModel())
    backwards = write_timeline(tmp_path, [obs_row(0, "gameplay", 5.0), obs_row(1, "gameplay", 1.0)],
                               name="backwards.json")
    with pytest.raises(ValueError) as raised:
        recover(backwards, tmp_path / "recovered")
    assert "backwards" in str(raised.value)
    with pytest.raises(ValueError):
        recover(write_timeline(tmp_path, [], name="empty.json"), tmp_path / "recovered")
    with pytest.raises(ValueError):
        recover(pathlib.Path(tmp_path) / "missing.json", tmp_path / "recovered")
    with pytest.raises(ValueError):
        boundary_recovery.adjudicate_saved_timeline(backwards, tmp_path / "r2", source_id="",
                                                     duration=600.0)
    with pytest.raises(ValueError):
        boundary_recovery.adjudicate_saved_timeline(backwards, tmp_path / "r3", source_id="yt:X",
                                                     duration=float("nan"))
    assert model.windows == []


# ---------- artifacts and scoring ----------

def test_recovered_run_emits_the_artifacts_downstream_tools_expect(tmp_path, serve):
    rows, spans = build_sets(2)
    timeline = write_timeline(tmp_path, rows)
    serve(FakeModel(closes_visible_sets))
    result = recover(timeline, tmp_path / "recovered", cap=30_000)
    out = pathlib.Path(result["output_dir"])
    for name in ("predictions.json", "project.tvh.json", "review.json", "run.json",
                 "adjudication.json", "recovery-inputs.json"):
        assert (out / name).is_file(), name
    assert [(p["start"], p["end"]) for p in predictions(result)] == spans_as_times(rows, spans)
    assert [(p["player1"], p["player2"], p["character1"], p["character2"])
            for p in predictions(result)] == \
        [(rows[s]["player1"], rows[s]["player2"], rows[s]["character1"], rows[s]["character2"])
         for s, _ in spans]
    project = read_json(out / "project.tvh.json")
    assert project["event_name"] == "Basement Brawl #9 (test)"
    assert project["video_path"] == "", "a recovery may not invent a resolved media path"
    assert [m["start"] for m in project["matches"]] == [rows[s]["timestamp"] for s, _ in spans]
    assert project["players"] == ["Ann", "Zoe", "Bo", "Cy"]
    assert project["characters"] == ["Jin", "Nina"]
    review = read_json(out / "review.json")
    assert review["usage"] == [] and review["counts"]["classified"] is None
    assert any(i["type"] == "recovered_refinement_history" for i in review["items"])
    assert review["policy_hash"] == read_json(out / "recovery-inputs.json")["policy_hash"]
    assert result["refinement_status"] == "not_recorded"
    assert result["recovered_from"]["timeline_sha256"]


def test_recovered_predictions_score_against_evaluator_only_labels(tmp_path, serve):
    timeline = write_timeline(tmp_path, sets_timeline())
    serve(FakeModel(closes_visible_sets))
    result = recover(timeline, tmp_path / "recovered", cap=30_000)
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps([{"start": 0.0, "end": 40.0}, {"start": 50.0, "end": None}]),
                     encoding="utf-8")
    report = boundary_workflow.score_from_run(pathlib.Path(result["output_dir"]) / "run.json",
                                               truth, result["output_dir"],
                                               source="yt:BB9TEST", truth_duration=100.0)
    assert report["cut_metrics"]["true_positives"] == 2
    assert report["cut_metrics"]["false_positives"] == 0
    assert report["derived_truth_ends"] == [1], "an open label end is a scoring decision"
    assert report["model_protocol_success"] is True
    assert report["truth_duration_override"] == 100.0


def test_an_incomplete_run_cannot_be_scored_as_a_result(tmp_path, serve, tiny_policy):
    rows, spans = build_sets(2, pad=PAD, games=1)
    timeline = write_timeline(tmp_path, rows)
    serve(FakeModel(broken=99))
    out = tmp_path / "partial"
    with pytest.raises(ValueError):
        recover(timeline, out, requests=1, policy_path=tiny_policy)
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps([{"start": 0.0, "end": 20.0}]), encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        boundary_workflow.score_from_run(out / "run.json", truth, out)
    assert "no predictions to score" in str(raised.value)


def test_no_label_content_reaches_an_adjudication_prompt(tmp_path, serve):
    timeline = write_timeline(tmp_path, sets_timeline(2, pad="n" * 400))
    model = serve(FakeModel())
    recover(timeline, tmp_path / "recovered", requests=6)
    assert model.windows
    sent = "".join(json.dumps(w["ids"]) for w in model.windows)
    for marker in ("truth", "4631", "Losers", "oct22"):
        assert marker not in sent
    assert not any(marker in POLICY for marker in ("4631", "Losers", "oct22"))
