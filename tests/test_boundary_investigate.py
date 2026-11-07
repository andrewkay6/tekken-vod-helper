"""The investigation loop plans in frames it already has, spends a stated budget, and stops honestly.

Every test here uses a fake client: the point is the scheduling, the accounting and the reuse, and a
test that reached the model would not be able to prove any of them.
"""
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_evidence
from tekken_vod_helper import boundary_investigate as bi


def cache(n=400, interval=1.0, start_dir="frames"):
    return [{"id": i + 1, "timestamp": round(i * interval, 6),
             "path": "{}/frame-{:04d}.jpg".format(start_dir, i + 1)} for i in range(n)]


class FakeClient:
    """Answers with a plausible round clock, counting calls like the real client."""

    def __init__(self, reply=None, fail_ids=()):
        self.calls = 0
        self.seen = []
        self.reply = reply
        self.fail_ids = set(fail_ids)

    def classify(self, frames):
        self.calls += 1
        self.seen.append([f["id"] for f in frames])
        if self.reply is not None:
            return self.reply
        return [{"id": f["id"], "state": "gameplay", "confidence": 0.9,
                 "round_clock": max(0, 60 - 5 * (f["id"] - 1)), "round_call": None,
                 "round_end": "ko" if f["id"] in self.fail_ids else None,
                 "result_transition": False, "win_dots_p1": 1, "win_dots_p2": 0,
                 "characters": ["Reina", "Azucena"], "evidence": "clock read from frame"}
                for f in frames]


UNRESOLVED = [
    {"type": "round_longer_than_the_clock_allows", "at": 300.0, "seconds": 120.0},
    {"type": "game_incomplete", "at": 900.0, "rounds_seen": 2},
    {"type": "win_dots_never_read", "at": 1500.0},
    {"type": "round_clock_unread", "count": 300, "of": 1154},
]


# ---------- regions ----------

def test_regions_come_from_the_unresolved_queue_not_from_a_guess():
    regions = bi.candidate_regions(UNRESOLVED, duration=6928.0)
    assert regions
    assert all(any(kind in {"round_longer_than_the_clock_allows", "game_incomplete",
                            "win_dots_never_read", "round_clock_impossible_fall",
                            "round_end_not_observed", "possible_play_below_confidence",
                            "round_start_not_observed"} for kind in r["kinds"])
               for r in regions)


def test_an_empty_queue_produces_no_work():
    assert bi.candidate_regions([], duration=100.0) == []
    plan = bi.plan_investigation([], cache(10), duration=10.0)
    assert plan["requests_planned"] == 0


def test_a_mark_prioritises_a_region_without_creating_one():
    # The mark lands on the least urgent question in the queue. It moves to the front and the set of
    # regions does not change, because a mark says where to look first, not where a set is.
    unmarked = bi.candidate_regions(UNRESOLVED, duration=6928.0)
    marked = bi.candidate_regions(UNRESOLVED, duration=6928.0, marks=[1505.0])
    assert len(marked) == len(unmarked)
    assert sorted(r["start"] for r in marked) == sorted(r["start"] for r in unmarked)
    assert marked[0]["mark_near"] and "win_dots_never_read" in marked[0]["kinds"]
    assert sum(1 for r in marked if r["mark_near"]) == 1


def test_overlapping_questions_are_one_region_not_three_requests():
    """Two questions whose windows genuinely meet are answered by one look at the same frames."""
    queue = [{"type": "game_incomplete", "at": 300.0},
             {"type": "round_end_not_observed", "at": 305.0},
             {"type": "win_dots_never_read", "at": 308.0}]
    regions = bi.candidate_regions(queue, duration=1000.0)
    assert len(regions) == 1
    assert len(regions[0]["kinds"]) == 3


# ---------- frames and budget ----------

def test_a_frame_is_skipped_only_when_it_already_answers_the_question():
    """Reuse is decided by the fact, not by the timestamp having been looked at once.

    Every frame of this recording has already been classified, and no classifier was ever asked for
    the round-win dots. Skipping on timestamp alone therefore planned zero requests for an entirely
    unread queue, so the investigation could not have learned anything new anywhere.
    """
    frames = cache(60)
    region = {"start": 0.0, "end": 59.0}
    answered = [index * 5.0 for index in range(12)]
    picked = bi.frames_for_region(frames, region, 1.0, answered=answered)
    assert all(round(f["timestamp"], 3) not in {round(t, 3) for t in answered} for f in picked)
    assert len(picked) == 48
    assert bi.frames_for_region(frames, region, 1.0, answered=[]) == frames


def test_an_unread_dot_queue_still_produces_requests_after_a_full_classification():
    """The regression the review found: a whole recording classified, a whole queue unread.

    The requests are crop requests now. Asking the whole frame for a row of nine-pixel circles is the
    reading that lost the measured comparison, so the queue's `win_dots_never_read` item routes to the
    crops arm at the anchor it named -- and the state route plans nothing, which is the point rather than
    an omission.
    """
    frames = cache(25)
    already_classified = [{"timestamp": float(i)} for i in range(25)]
    plan = bi.plan_investigation([{"type": "win_dots_never_read", "at": 10.0}], frames, 25,
                                 observations=already_classified)
    assert plan["requests_planned"] > 0
    assert plan["pip_requests_planned"] == plan["requests_planned"]
    assert plan["state_requests_planned"] == 0
    assert plan["pip_requests"][0]["asked"] == ["win_dots"]
    assert abs(plan["pip_requests"][0]["timestamp"] - 10.0) <= 1.0


def test_a_question_already_answered_on_every_frame_plans_nothing():
    """Answered means *measured*, and the row says which it is by its source.

    A pip value that arrived from the whole frame is a diagnostic and leaves the question open; a crop
    reading closes it. This is the difference between a plan that re-asks 25 frames and one that asks
    nothing, and it cannot be decided by whether a `win_dots` key happens to be non-null.
    """
    frames = cache(25)
    measured = [{"timestamp": float(i), "win_dots": [1, 0], "fact_source": "crop_readings",
                 "path": "frames/frame-{:04d}.jpg".format(i + 1)} for i in range(25)]
    plan = bi.plan_investigation([{"type": "win_dots_never_read", "at": 10.0}], frames, 25,
                                 observations=measured,
                                 crop_readings={entry["path"]: entry for entry in measured})
    assert plan["requests_planned"] == 0
    assert plan["groups_total"] == 0
    guessed = [{"timestamp": float(i), "win_dots": [1, 0], "fact_source": "vision_group",
                "path": "frames/frame-{:04d}.jpg".format(i + 1)} for i in range(25)]
    still_open = bi.plan_investigation([{"type": "win_dots_never_read", "at": 10.0}], frames, 25,
                                       observations=guessed)
    assert still_open["pip_requests_planned"] > 0, "a whole-frame guess closed the question"


def test_an_explicit_negative_answer_counts_as_an_answer():
    """A frame the model looked at and called "no banner" is answered, and must not be asked again."""
    assert bi.fact_is_answered({"round_end": None}, "round_end") is False
    assert bi.fact_is_answered({"round_end": False}, "round_end") is True
    assert bi.fact_is_answered({"win_dots": [None, None]}, "win_dots") is False
    assert bi.fact_is_answered({"win_dots": [0, 0]}, "win_dots") is True


def test_a_truncated_region_is_reported_as_unfinished_rather_than_zero_deferred():
    """An allowance ending mid-region used to report nothing deferred while dropping whole groups."""
    plan = bi.plan_investigation([{"type": "game_incomplete", "at": 100.0}], cache(600),
                                 duration=600.0, max_requests=1, batch_frames=6)
    assert plan["requests_planned"] == 1 and plan["groups_total"] == 1
    partial = plan["regions_partially_planned"]
    assert partial and partial[0]["partial_region"] is True
    assert partial[0]["groups"] >= 1 and partial[0]["frames"] >= 6


def test_a_group_is_contiguous_seconds_not_a_scatter_of_the_recording():
    groups = bi.group_frames(cache(13), size=6)
    assert [len(g) for g in groups] == [6, 6, 1]
    assert [f["timestamp"] for f in groups[0]] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_the_plan_states_the_requests_it_will_spend_before_any_are_sent():
    plan = bi.plan_investigation(UNRESOLVED, cache(2000), duration=6928.0,
                                 max_requests=7, batch_frames=6)
    # One allowance, three routes. `requests_planned` is the whole bill and the state loop's own batches
    # are `state_requests_planned`: conflating the two is what let a plan price 7 requests for a window
    # whose real bill was larger, and a budget approved on that figure is not the budget spent.
    assert plan["requests_planned"] <= 7
    assert plan["requests_planned"] == sum(entry["requests_planned"]
                                           for entry in plan["dispatch"]["routes"])
    assert plan["state_requests_planned"] == plan["groups_total"]
    assert plan["region_count"] == len(plan["regions"])
    for region in plan["regions"]:
        assert len(region["groups"]) * 6 >= len(region["groups"][0]["frame_timestamps"])
    assert plan["regions_deferred_for_budget"] >= 0


def test_the_plan_never_asks_for_more_than_the_allowance():
    wide = [{"type": "game_incomplete", "at": float(t)} for t in range(0, 6000, 100)]
    plan = bi.plan_investigation(wide, cache(6928), duration=6928.0, max_requests=5)
    assert plan["requests_planned"] == 5
    assert plan["regions_deferred_for_budget"] > 0


def test_fingerprints_are_stable_for_the_same_frames_and_change_with_the_question():
    frames = cache(4)
    one = bi._fingerprint(frames)
    assert one == bi._fingerprint(list(frames))
    assert one != bi._fingerprint(frames, prompt_version="different question")
    assert one != bi._fingerprint(frames[:3])


# ---------- running ----------

def test_a_run_persists_each_group_before_the_next_is_sent(tmp_path):
    plan = bi.plan_investigation(UNRESOLVED, cache(2000), duration=6928.0, max_requests=3,
                                 routes=("state",))
    client = FakeClient()
    run = bi.InvestigationRun(client, tmp_path / "inv", max_requests=3)
    saved = []
    run.on_group = lambda record: saved.append(record["fingerprint"])
    result = run.run(plan, cache_by_path={f["path"]: f for f in cache(2000)})
    assert result["status"] == "complete" or result["stopped_reason"] == "request_allowance_reached"
    assert result["requests_sent"] == len(client.seen) == run.groups_done
    assert result["observations_written"] > 0
    files = sorted((tmp_path / "inv" / "requests").glob("group-*.json"))
    assert len(files) == run.groups_done
    assert [json.loads(p.read_text())["fingerprint"] for p in files] == sorted(saved)


def test_a_resumed_run_asks_nothing_it_already_has(tmp_path):
    # The state route on its own: this is a test of the loop's reuse rule, and with three routes sharing
    # one two-request allowance the crop route would take it and there would be nothing to resume.
    plan = bi.plan_investigation(UNRESOLVED, cache(2000), duration=6928.0, max_requests=2,
                                 routes=("state",))
    first = bi.InvestigationRun(FakeClient(), tmp_path / "inv", max_requests=2)
    first.run(plan, cache_by_path={f["path"]: f for f in cache(2000)})
    client = FakeClient()
    again = bi.InvestigationRun(client, tmp_path / "inv", max_requests=2)
    result = again.run(plan, cache_by_path={f["path"]: f for f in cache(2000)})
    assert client.calls == 0
    assert result["groups_resumed"] == result["groups_completed"]
    assert again.observations == first.observations and len(first.observations) > 0


def test_exhausting_the_budget_is_reported_as_a_stop_not_a_success(tmp_path):
    wide = [{"type": "game_incomplete", "at": float(t)} for t in range(0, 6000, 60)]
    # The planner was allowed more than the runner is: the run has to notice it ran out mid-plan and
    # say how much of the plan it never touched.
    plan = bi.plan_investigation(wide, cache(6928), duration=6928.0, max_requests=40)
    assert plan["groups_total"] > 6
    run = bi.InvestigationRun(FakeClient(), tmp_path / "inv", max_requests=3)
    result = run.run(plan, cache_by_path={f["path"]: f for f in cache(6928)})
    assert result["stopped_reason"] == "request_allowance_reached"
    assert result["status"] == "stopped"
    assert result["requests_sent"] == 3
    assert result["not_attempted"] == plan["groups_total"] - 3


def test_an_unusable_reply_is_kept_and_not_counted_as_evidence(tmp_path):
    plan = bi.plan_investigation([{"type": "game_incomplete", "at": 100.0}], cache(200),
                                 duration=6928.0, max_requests=2)
    run = bi.InvestigationRun(FakeClient(reply=[{"id": 99, "state": "gameplay"}]),
                              tmp_path / "inv", max_requests=2)
    result = run.run(plan, cache_by_path={f["path"]: f for f in cache(200)})
    assert result["groups_failed_validation"] == result["groups_completed"]
    assert result["observations_written"] == 0
    saved = list((tmp_path / "inv" / "requests").glob("group-*.json"))
    record = json.loads(saved[0].read_text())
    assert record["errors"] and record["reply"], "the failed reply stays on disk to read"


# ---------- reply validation ----------

def test_a_reply_must_answer_every_frame_it_was_sent():
    group = [{"id": 1, "timestamp": 0.0, "path": "a.jpg"}, {"id": 2, "timestamp": 1.0, "path": "b.jpg"}]
    _, errors = bi.validate_group_reply([{"id": 1, "round_clock": 30}], group)
    assert errors


def test_a_clock_outside_the_round_clock_is_refused_not_clamped():
    group = [{"id": 1, "timestamp": 0.0, "path": "a.jpg"}]
    observations, errors = bi.validate_group_reply(
        [{"id": 1, "state": "gameplay", "round_clock": 61, "win_dots_p1": 9}], group)
    assert errors, "a 61-second round clock and 9 win dots are not observations"


def test_nulls_are_accepted_because_an_unreadable_frame_is_a_real_result():
    group = [{"id": 1, "timestamp": 0.0, "path": "a.jpg"}]
    observations, errors = bi.validate_group_reply(
        [{"id": 1, "state": "unknown", "round_clock": None, "win_dots_p1": None,
          "win_dots_p2": None}], group)
    assert errors == []
    assert observations[0]["round_clock"] is None


def test_merge_prefers_the_denser_reading_and_keeps_one_row_per_instant():
    base = [{"timestamp": 0.0, "state": "gameplay", "round_clock": None, "evidence": "timer unseen"},
            {"timestamp": 5.0, "state": "gameplay", "round_clock": 55}]
    new = [{"timestamp": 0.0, "state": "gameplay", "round_clock": 60, "win_dots": [1, 0]},
           {"timestamp": 6.0, "state": "gameplay", "round_clock": 59}]
    merged = bi.merge_observations(base, new)
    assert [m["timestamp"] for m in merged] == [0.0, 5.0, 6.0]
    assert merged[0]["round_clock"] == 60, "the unread row must take the reading that has one"
    assert merged[1]["round_clock"] == 55, "a row that already read the clock is not overwritten"


def test_denser_frames_resolve_the_question_that_produced_them():
    """The loop has to close a queue item, not just add rows: one missed reset, one extra round.

    A span longer than the round clock is the tracker admitting two rounds are hiding in it. Reading
    the clock between the two cached rows finds the reset, and the queue item disappears.
    """
    base = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "evidence": "timer 60"},
            {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "evidence": "timer 20"}]
    before = bg.track_games(bg.build_observations(base))
    queued = [u for u in before["unresolved"]
              if u["type"] == "round_longer_than_the_clock_allows"]
    assert len(before["rounds"]) == 1 and queued

    plan = bi.plan_investigation(queued, cache(120), duration=120.0, max_requests=4)
    assert plan["groups_total"] >= 1
    # The answer a real group would give: the round restarted at second 45.
    denser = [{"timestamp": 45.0, "state": "gameplay", "confidence": 0.95,
               "evidence": "timer 60, ROUND 2 callout"}]
    after = bg.track_games(bg.build_observations(bi.merge_observations(base, denser)))
    assert len(after["rounds"]) == 2
    assert [r["start"] for r in after["rounds"]] == [0.0, 45.0]
    assert not [u for u in after["unresolved"]
                if u["type"] == "round_longer_than_the_clock_allows"]


# ---------- end to end, no model ----------

def _workspace(tmp_path, rows, interval=1.0):
    """A synthetic cache and timeline on disk, so the orchestration reads real files."""
    end = max(r["timestamp"] for r in rows)
    frames = []
    for index in range(int(end / interval) + 2):
        stamp = index * interval
        path = tmp_path / "frames" / "frame-{:05d}.jpg".format(index + 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff\xe0fake")
        frames.append({"id": index + 1, "timestamp": stamp, "path": str(path)})
    manifest = tmp_path / "sample-manifest.json"
    manifest.write_text(json.dumps({"interval": interval, "frames": frames}), encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps(rows), encoding="utf-8")
    return timeline, manifest, frames


def test_a_run_densifies_retracks_and_persists_the_evidence(tmp_path):
    """The whole loop, with a fake client: plan, ask, merge, re-track, write, and name the evidence."""
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "timer 60; fighters engaged"},
            {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "timer 20; fighters engaged"},
            {"timestamp": 101.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "no clock readable"}]
    timeline, manifest, frames = _workspace(tmp_path, rows)

    class ClockAt45(FakeClient):
        """Replies with a fresh clock only for the frame at 45 s: that is where the reset is."""

        def classify(self, group):
            self.calls += 1
            self.seen.append([f["id"] for f in group])
            return [{"id": f["id"], "state": "gameplay", "confidence": 0.93,
                     "round_clock": 60 if abs(f["timestamp"] - 45.0) < 0.6 else None,
                     "round_call": 2 if abs(f["timestamp"] - 45.0) < 0.6 else None,
                     "round_end": None, "result_transition": False,
                     "win_dots_p1": 1, "win_dots_p2": 0,
                     "characters": ["Reina", "Azucena"], "evidence": "read from frame"}
                    for f in group]

    from tekken_vod_helper import boundary_investigate as module
    result = module.investigate_saved_games(
        timeline, manifest, duration=120.0, output_dir=tmp_path / "inv",
        max_requests=6, batch_frames=6, client_factory=lambda allowance: ClockAt45(), routes=("state",))

    assert result["status"] in ("complete", "stopped")
    assert result["requests_sent"] == result["groups_completed"] > 0
    assert result["resolved"]["delta"]["rounds"] == 1, "one missed reset should become one round"
    assert result["unresolved_after"].get("round_longer_than_the_clock_allows", 0) == 0

    out = tmp_path / "inv"
    assert (out / "investigation.json").is_file()
    assert (out / "timeline-with-facts.json").is_file()
    trace = json.loads((out / "games-trace-after.json").read_text(encoding="utf-8"))
    assert trace["rounds"] and all(r.get("start_evidence") for r in trace["rounds"])
    # Every group is on disk with the frames it covered: that is what makes a resumed run ask nothing.
    groups = sorted((out / "requests").glob("group-*.json"))
    assert len(groups) == result["groups_completed"]
    record = json.loads(groups[0].read_text(encoding="utf-8"))
    assert record["frame_paths"] and record["question"] and "errors" in record


def test_a_fresh_output_dir_must_ask_and_a_used_one_is_refused(tmp_path):
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 60"},
            {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 20"}]
    timeline, manifest, _ = _workspace(tmp_path, rows)
    first = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                       output_dir=tmp_path / "inv", max_requests=4,
                                       client_factory=lambda allowance: FakeClient(), routes=("state",))
    assert first["requests_sent"] > 0
    again = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                       output_dir=tmp_path / "inv2", max_requests=4,
                                       client_factory=lambda allowance: FakeClient(), routes=("state",))
    assert again["requests_sent"] > 0, "a fresh output dir has nothing banked and must ask"
    # The guard that protects a finished run's evidence from being written over.
    with pytest.raises(ValueError, match="refusing to overwrite"):
        bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                   output_dir=tmp_path / "inv", max_requests=4,
                                   client_factory=lambda allowance: FakeClient(), routes=("state",))


def test_resuming_an_existing_directory_reasks_no_group(tmp_path):
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 60"},
            {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 20"}]
    timeline, manifest, _ = _workspace(tmp_path, rows)
    plan = bi.plan_saved_investigation(timeline, manifest, duration=120.0, max_requests=4,
                                       batch_frames=6)
    client = FakeClient()
    run = bi.InvestigationRun(client, tmp_path / "inv", max_requests=4)
    by_path = {f["path"]: f for f in cache(200)}
    first = run.run(plan, cache_by_path=by_path)
    assert first["requests_sent"] == first["groups_completed"] > 0
    # A resumed run is a new process with an empty client and the same directory on disk.
    resumed_client = FakeClient()
    again = bi.InvestigationRun(resumed_client, tmp_path / "inv", max_requests=4)
    second = again.run(plan, cache_by_path=by_path)
    assert resumed_client.calls == 0, "the resumed run sent no new request"
    assert second["requests_sent"] == 0
    assert second["groups_resumed"] == first["groups_completed"]
    assert second["observations_written"] == first["observations_written"]


def test_the_round_facts_question_never_asks_for_the_overlay():
    client = bi.RoundFactsClient(max_requests=1)
    assert client.prompt_version == "round-facts-v1"
    assert client.prompt_version != bi.boundary_media.PROMPT_VERSION
    assert client.request_max_tokens == bi.DEFAULT_COMPLETION_TOKENS
    text = client._build_content("", [{"id": 1, "timestamp": 0.0,
                                       "path": "build/work/bb9/detect-1440p-v1/frames/"
                                               "frame-3921.jpg"}])[0]["text"]
    for field in ("round_clock", "round_call", "round_end", "result_transition",
                  "win_dots_p1", "win_dots_p2"):
        assert field in text, field
    assert "Do NOT report the broadcast player names" in text
    assert "score1" not in text and "player1" not in text


def test_a_dead_endpoint_is_reported_and_keeps_the_groups_already_paid_for(tmp_path):
    """Failure has to arrive as a result, not as a stack trace that throws the run away."""
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 60"},
            {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 20"}]
    timeline, manifest, frames = _workspace(tmp_path, rows)

    class Dead:
        calls = 0

        def classify(self, group):
            self.calls += 1
            raise ValueError("API request failed: Connection refused")

    dead = Dead()
    result = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                        output_dir=tmp_path / "inv", max_requests=4,
                                        client_factory=lambda allowance: dead, routes=("state",))
    assert result["status"] == "stopped"
    assert result["stopped_reason"] == "request_failed"
    assert "Connection refused" in result["error"]
    assert result["requests_sent"] == 1 and result["not_attempted"] > 0
    assert result["observations_written"] == 0
    # Attempted and completed are counted apart: this group was tried and never finished.
    assert result["groups_completed"] == 0 and result["groups_failed_request"] == 1
    # The run record exists and says so; a caller never has to guess whether the loop died silently.
    record = json.loads((tmp_path / "inv" / "investigation.json").read_text(encoding="utf-8"))
    assert record["result"]["error"] == result["error"]


# ---------- public restart behaviour ----------

ROWS = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
         "player2": "B", "evidence": "timer 60"},
        {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
         "player2": "B", "evidence": "timer 20"}]


def test_the_public_command_refuses_a_used_dir_and_continues_it_on_resume(tmp_path):
    """Resumability has to be reachable from the CLI surface, not only from the internal runner."""
    timeline, manifest, _ = _workspace(tmp_path, ROWS)
    first = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                       output_dir=tmp_path / "inv", max_requests=2,
                                       client_factory=lambda allowance: FakeClient(), routes=("state",))
    assert first["requests_sent"] > 0
    # The directory now holds paid-for work, so a bare re-run is refused rather than written over.
    with pytest.raises(ValueError, match="pass resume to continue it"):
        bi.investigate_saved_games(timeline, manifest, duration=120.0, output_dir=tmp_path / "inv",
                                   max_requests=2, client_factory=lambda allowance: FakeClient(), routes=("state",))
    resumed = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                         output_dir=tmp_path / "inv", max_requests=4,
                                         client_factory=lambda allowance: FakeClient(),
                                         resume=True, routes=("state",))
    assert resumed["groups_resumed"] > 0, "the saved groups were reused, not re-asked"
    assert resumed["budget"]["groups_answered"] >= resumed["groups_resumed"]


def test_resume_refuses_when_the_inputs_that_produced_the_dir_differ(tmp_path):
    timeline, manifest, _ = _workspace(tmp_path, ROWS)
    bi.investigate_saved_games(timeline, manifest, duration=120.0, output_dir=tmp_path / "inv",
                               max_requests=2, client_factory=lambda allowance: FakeClient(), routes=("state",))
    other_timeline, _other_manifest, _ = _workspace(tmp_path / "other", ROWS, interval=2.0)
    with pytest.raises(ValueError, match="resume refused"):
        bi.investigate_saved_games(other_timeline, manifest, duration=120.0,
                                   output_dir=tmp_path / "inv", max_requests=2,
                                   client_factory=lambda allowance: FakeClient(), resume=True, routes=("state",))


def test_a_run_persists_the_plan_and_the_inputs_it_ran_from(tmp_path):
    timeline, manifest, _ = _workspace(tmp_path, ROWS)
    bi.investigate_saved_games(timeline, manifest, duration=120.0, output_dir=tmp_path / "inv",
                               max_requests=2, client_factory=lambda allowance: FakeClient(), routes=("state",))
    out = tmp_path / "inv"
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    identity = json.loads((out / "investigation-inputs.json").read_text(encoding="utf-8"))
    assert plan["groups_total"] > 0 and plan["question_identity"]["prompt_version"]
    assert identity["timeline_sha256"] and identity["question"] == plan["question_identity"]
    # Fingerprints cover the model and question, so a saved group from another configuration cannot
    # be reloaded as if it answered this one.
    assert plan["regions"][0]["groups"][0]["fingerprint"] != bi._fingerprint(
        [{"path": f["path"], "timestamp": f["timestamp"]} for f in cache(3)],
        {"different": "question"})


def test_groups_answered_attempts_and_attempts_remaining_are_reported_separately(tmp_path):
    """One request per group is only true until a reply is retried, so the two budgets split."""
    timeline, manifest, _ = _workspace(tmp_path, ROWS)

    class TwicePerGroup(FakeClient):
        def classify(self, frames):
            self.calls += 2  # a request and its retry charge the same allowance
            self.seen.append([f["id"] for f in frames])
            return [{"id": f["id"], "state": "gameplay", "confidence": 0.9, "round_clock": 40,
                     "win_dots_p1": 1, "win_dots_p2": 0, "characters": ["Reina", "Azucena"]}
                    for f in frames]

    result = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                        output_dir=tmp_path / "inv", max_requests=6,
                                        client_factory=lambda allowance: TwicePerGroup(), routes=("state",))
    budget = result["budget"]
    assert budget["attempt_allowance"] == 6
    assert budget["attempts_used"] == 2 * budget["groups_answered"] > 0
    assert budget["attempts_remaining"] == 6 - budget["attempts_used"]
    assert budget["groups_answered"] <= budget["groups_planned"]


# ---------- targeting the frames that actually decide it ----------

def test_distinct_round_ends_are_each_a_target_not_one_merged_region():
    """Three round ends 45 s apart are three questions; merging them into one answered one, lost two."""
    queue = [{"type": "win_dots_never_read", "at": float(t), "round_start": float(t - 30)}
             for t in (95.0, 140.0, 185.0)]
    regions = bi.candidate_regions(queue, duration=300.0)
    assert len(regions) == 3
    # Each region brackets its own round end rather than a shared bucket midpoint.
    for region, moment in zip(sorted(regions, key=lambda r: r["start"]), (95.0, 140.0, 185.0)):
        assert region["start"] <= moment <= region["end"]


def test_questions_about_the_same_instant_are_one_region_not_several():
    """Merging is by real overlap, so a winner question is not doubled against its own round end."""
    queue = [{"type": "game_winner_unobserved", "at": 235.23, "anchor": "game_end"},
             {"type": "win_dots_never_read", "at": 235.23, "anchor": "round_end",
              "round_start": 175.17},
             {"type": "round_end_not_observed", "at": 235.23}]
    regions = bi.candidate_regions(queue, duration=600.0)
    covering = [r for r in regions if r["start"] <= 235.23 <= r["end"]]
    assert len(covering) == 1, "one instant must be looked at once: {}".format(covering)
    assert len(covering[0]["kinds"]) == 3


def test_a_win_dots_question_is_asked_at_the_round_end_it_was_read_at():
    """The queue anchors on the ending, and the region must be centred where the dots are drawn."""
    region = bi.candidate_regions([{"type": "win_dots_never_read", "at": 235.23,
                                    "round_start": 175.17, "anchor": "round_end"}],
                                  duration=600.0)[0]
    assert region["start"] <= 235.23 <= region["end"]
    assert region["end"] - region["start"] <= 12.0, "a pip row does not need the whole round re-watched"


def test_a_game_winner_question_reaches_the_next_game_reset():
    """A winner is confirmed by the dots *and* by the following game starting from zero."""
    region = bi.candidate_regions([{"type": "game_winner_unobserved", "at": 235.23,
                                    "anchor": "game_end"}], duration=600.0)[0]
    assert region["start"] <= 235.23
    assert region["end"] >= 235.23 + 15.0


def test_a_regressed_pip_row_is_investigated_where_it_fell():
    """A count the tracker refused to adjudicate still has to reach the frames that can settle it.

    The queue item carries the instant of the reading that fell and keeps the game start as context.
    Stamping the game start instead sent the investigation to the opening of a game that was over
    minutes before its own pip row disagreed with itself.
    """
    queue = [{"type": "win_dots_regressed", "at": 1066.06, "game_start": 1018.02, "side": "p2",
              "from": 3, "to": 1, "anchor": "game_rounds"}]
    region = bi.candidate_regions(queue, duration=1200.0)[0]
    assert region["start"] <= 1066.06 <= region["end"]
    assert region["start"] > 1018.02, "the game start is context, not where the row changed"
    assert "win_dots_regressed" in region["kinds"]
    assert region["end"] - region["start"] <= 15.0, "one pip row does not need the game re-watched"


def test_the_pilot_frames_sit_on_the_decisive_instants_not_minites_away():
    """The reviewed plan missed the first game's claimed ending by 94 s. This is the guard.

    Anchors are the ones the tracker itself produced for BB9's first game, taken from the cached
    clock, not assumed: the game opens at 53.05, its rounds end at 95.09 / 125.12 / 170.17 / 235.23,
    and the next game's ROUND 1 lands at 249.25.
    """
    decisive = [53.05, 95.09, 125.12, 170.17, 235.23, 249.25]
    queue = ([{"type": "win_dots_never_read", "at": t, "anchor": "round_end"}
              for t in decisive[1:4]]
             + [{"type": "game_winner_unobserved", "at": 235.23, "anchor": "game_end"},
                {"type": "round_start_not_observed", "at": 53.05}])
    frames = [{"id": i + 1, "timestamp": float(i), "path": "frames/frame-{:05d}.jpg".format(i + 1)}
              for i in range(300)]
    plan = bi.plan_investigation(queue, frames, 300.0, observations=[], max_requests=40)
    # Both routes, because the questions are split between them: the pip row is asked of crops at the
    # decision instants, and the clock and cue facts are asked of whole frames on a grid. A test that
    # looked only at the state grid would have watched the decisive instants vanish from the plan when the
    # pip question moved routes, and called that a fix.
    chosen = sorted([t for r in plan["regions"] for g in r["groups"] for t in g["frame_timestamps"]]
                    + [float(entry["timestamp"]) for entry in plan["pip_requests"]])
    assert chosen, "the plan asked for no frames at all"
    # The guard has two directions, and the old test only had one of them. Every instant the queue named
    # has to be covered -- and every frame asked has to be near *something* the queue named, which is what
    # "not a scatter of the recording" ever meant. 249.25 is the next game's opening: nobody asked a
    # question about it, so nothing is wrong with the plan for not looking there.
    anchors = sorted([float(item["at"]) for item in queue if item.get("at") is not None])
    for moment in anchors:
        nearby = [t for t in chosen if abs(t - moment) <= 6.0]
        assert nearby, "nothing within 6 s of the decisive instant {}".format(moment)
    # The other direction holds for the crop route, and only for it: a decision frame exists to read the
    # row at one instant, so a frame far from every anchor is a frame nobody asked about. The state route
    # is deliberately not held to that -- it grids the *span* of a region, because a clock that resets
    # between two sampled frames can only be found by walking the window.
    crops = [float(entry["timestamp"]) for entry in plan["pip_requests"]]
    adrift = [t for t in crops if all(abs(t - moment) > 6.0 for moment in anchors)]
    assert adrift == [], "the crop route asked {} frames no anchor pointed at: {}".format(
        len(adrift), adrift[:6])


def test_the_request_allowance_is_spent_on_groups_and_reports_both_numbers(tmp_path):
    """Attempts and answered groups are different counts once anything is retried."""
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 60"},
            {"timestamp": 100.0, "state": "gameplay", "confidence": 0.9, "player1": "A",
             "player2": "B", "evidence": "timer 20"}]
    timeline, manifest, _ = _workspace(tmp_path, rows)
    result = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                        output_dir=tmp_path / "inv", max_requests=4,
                                        client_factory=lambda allowance: FakeClient(), routes=("state",))
    budget = result["budget"]
    assert budget["groups_planned"] >= budget["groups_answered"]
    assert budget["attempts_used"] + budget["attempts_remaining"] == budget["attempt_allowance"]


def test_round_four_and_five_are_valid_answers_about_a_game():
    """First-to-three reaches a fourth round at 2-1 and a fifth at 2-2; the schema must not refuse it."""
    group = [dict(id=1, timestamp=1.0, path="x.jpg"), dict(id=2, timestamp=2.0, path="y.jpg")]
    observations, errors = bi.validate_group_reply(
        [dict(id=1, state="gameplay", round_call=4, win_dots_p1=2, win_dots_p2=1),
         dict(id=2, state="gameplay", round_call=5, win_dots_p1=2, win_dots_p2=2)], group)
    assert errors == []
    assert [o["round_call"] for o in observations] == [4, 5]
    _, bad = bi.validate_group_reply([dict(id=1, state="gameplay", round_call=0),
                                      dict(id=2, state="gameplay", round_call=10)], group)
    assert len(bad) == 2


def test_complementary_side_readings_combine_instead_of_clashing():
    """One frame reads the left pip row, the next reads the right: that is two halves of an answer.

    Both halves are crop readings, and that is not decoration: a pip value from any other route is a
    diagnostic and never reaches ``win_dots`` at all, so slot-wise merging is a rule about combining two
    measurements, not about letting a guess complete one.
    """
    merged = bi.merge_observations(
        [dict(timestamp=5.0, state="gameplay", win_dots=[3, None], fact_source="crop_readings")],
        [dict(timestamp=5.0, state="gameplay", win_dots=[None, 2], fact_source="crop_readings")])
    assert merged[0]["win_dots"] == [3, 2]
    assert "field_conflicts" not in merged[0]
    assert merged[0]["pip_evidence_class"] == "validated_pip_evidence"


def test_a_same_side_disagreement_is_reported_not_blended():
    """[3, None] and [None, 3] name opposite sides: comparing them after dropping nulls hid this."""
    merged = bi.merge_observations(
        [dict(timestamp=5.0, state="gameplay", win_dots=[3, 1], fact_source="crop_readings")],
        [dict(timestamp=5.0, state="gameplay", win_dots=[0, 1], fact_source="crop_readings")])
    assert merged[0]["win_dots"] == [0, 1]
    conflicts = merged[0]["field_conflicts"]
    assert conflicts and conflicts[0]["sides"] == ["p1"]
    assert conflicts[0]["previous"] == [3, 1] and conflicts[0]["later"] == [0, 1]


# ------------------------------ the bounded second pass over the cache's own finer frames

def _freeze_rows(end_at=20.0):
    """A round ending at 20.0 with the next opening at 30.0.

    ``PROBE_REGION`` stops short of that opening: a trailing readable clock is itself a window the planner
    will want to probe, and letting it into these fixtures tests the wrong question.
    """
    return [dict(timestamp=end_at - 3.0, screen_state="gameplay", round_clock=33),
            dict(timestamp=end_at, screen_state="gameplay", round_clock=33, round_end="ko"),
            dict(timestamp=end_at + 10.0, screen_state="gameplay", round_clock=60)]


PROBE_REGION = (0.0, 28.0)


def _readings(dots_by_time):
    """Crop answers keyed by frame path, the way the corpus stores them."""
    return {entry["path"]: {"timestamp": entry["timestamp"], "win_dots": dots_by_time[entry["timestamp"]]}
            for entry in cache(40) if entry["timestamp"] in dots_by_time}


def test_the_planner_asks_the_frame_between_its_own_probes_when_a_winner_is_still_missing():
    """The game-1 decider, reproduced as autonomy rather than as a hand-picked frame.

    Every coarse frame of the window has an answer, none shows a side reaching the round target, a later
    answer in the same window says the row was already gone, and the tracker still holds a pip question at
    that instant. That is a round whose deciding pip sits on an unsampled frame, and the frame between +0 s
    and +2 s is the only place left to look.
    """
    answered = _readings({20.0: [2, 2], 22.0: [None, None], 24.0: [None, None]})
    plan = bi.pip_requests(_freeze_rows(), cache(40), PROBE_REGION, answered, 5,
                           unresolved=[{"type": "game_winner_unobserved", "at": 20.0}])
    assert [(round(entry["timestamp"], 3), entry["role"]) for entry in plan["requests"]] == \
        [(21.0, "freeze_interior_plus_1s")]
    assert plan["requests"][0]["asked"] == ["win_dots"] and plan["requests"][0]["images"] == 2


def test_a_window_whose_answers_already_show_the_target_asks_nothing_more():
    answered = _readings({20.0: [3, 2], 22.0: [3, 2], 24.0: [3, 2]})
    plan = bi.pip_requests(_freeze_rows(), cache(40), PROBE_REGION, answered, 5,
                           unresolved=[{"type": "game_winner_unobserved", "at": 20.0}])
    assert plan["requests"] == [], "the increment is on tape: a denser grid is not the missing evidence"


def test_a_coarse_frame_still_unasked_outranks_any_refinement():
    """The second pass is a second pass. A window with its own first question open spends on that."""
    answered = _readings({20.0: [2, 2]})
    plan = bi.pip_requests(_freeze_rows(), cache(40), PROBE_REGION, answered, 5,
                           unresolved=[{"type": "game_winner_unobserved", "at": 20.0}])
    roles = [entry["role"] for entry in plan["requests"]]
    assert "after_end_plus_2s" in roles and "after_end_plus_4s" in roles, roles
    assert not any("freeze_interior" in role for role in roles), roles


def test_an_ordinary_round_end_the_tracker_left_alone_is_never_refined():
    """The over-eager version of this rule asked for 11 extra frames on the real recording.

    Most rounds close 2-1 or 1-0, so "no reading reached the round target" cannot tell a settled round from
    an unanswered game. Only the tracker's own open question at that instant can; without one the plan has
    to stay exactly as silent as it was before this existed.
    """
    answered = _readings({20.0: [2, 1], 22.0: [2, 1], 24.0: [2, 1]})
    plan = bi.pip_requests(_freeze_rows(), cache(40), PROBE_REGION, answered, 5, unresolved=[])
    assert plan["requests"] == [], [(round(e["timestamp"], 2), e["role"]) for e in plan["requests"]]


# ------------------------------------------------------ bounded automatic widening of the query

TAPE = {"proven": True, "recording_start": 0.0, "recording_end": 6000.0, "cache_start": 0.0,
        "cache_end": 5999.0, "cache_interval": 1.0, "cache_frames": 6000}


def _candidate_with_edge(at, limit, side="start"):
    other = "end" if side == "start" else "start"
    return {"sets": [{"start": 100.0, "end": 500.0,
                      "edges": {side: {"at": at, "limit": limit}, other: {"at": 500.0}}}]}


def test_only_an_unqueried_edge_widens_the_query():
    """Four edges, four answers, because widening helps exactly one of them.

    Nothing here asks for a controller screen, a result screen, a marking, a bracket or a format: the
    trigger is the candidate's own disclosure saying that nothing settled because this run stopped looking.
    Every other limit stops with its reason published rather than spending an allowance on footage that is
    not there.
    """
    opened = bi.expansion_needs(_candidate_with_edge(150.0, "analysis_window"), TAPE)
    assert opened["before"]["at"] == 150.0 and opened["blocked"] == []
    assert opened["before"]["reach_to"] == 0.0, "never reach before what the cache and tape allow"
    for limit, why in (("recording_head", "the tape starts here"),
                       ("not_in_cache", "the cache stops here"),
                       ("source_bounds_unknown", "nothing about the tape is known"),
                       ("footage_never_sampled", "a finer grid, not a wider window")):
        outcome = bi.expansion_needs(_candidate_with_edge(150.0, limit), TAPE)
        assert outcome["before"] is None, "%s was offered widening: %r" % (why, outcome)


def test_reach_is_capped_per_side_and_clamped_by_the_tape():
    needs = bi.expansion_needs(_candidate_with_edge(150.0, "analysis_window"), TAPE)
    got = bi.expansion_window((150.0, 500.0), needs, per_side=90.0)
    assert got["expanded_window"] == [60.0, 500.0] and got["expanded"] is True
    assert got["steps"][0]["stopping_reason"] == "expanded" and got["steps"][0]["moved"] == 90.0
    # A cache that stops 20 s short of the requested reach is honoured, and says which bound stopped it.
    short = bi.expansion_needs(_candidate_with_edge(150.0, "analysis_window"), dict(TAPE, cache_start=130.0))
    tight = bi.expansion_window((150.0, 500.0), short, per_side=90.0)
    assert tight["expanded_window"] == [130.0, 500.0] and tight["steps"][0]["moved"] == 20.0
    assert tight["steps"][0]["clamped_by"] == "cache_edge", tight["steps"]
    assert tight["steps"][0]["from"] == 150.0, "the base is the window edge, not an inner timestamp"


def test_a_resume_continues_from_where_the_last_widening_got_to():
    """Progress is what stops the same footage being paid for twice."""
    needs = bi.expansion_needs(_candidate_with_edge(150.0, "analysis_window"), TAPE)
    progress = {"steps": [{"side": "before", "expanded_to": 60.0, "moved": 90.0,
                           "stopping_reason": "expanded"}]}
    again = bi.expansion_window((150.0, 500.0), needs, per_side=90.0, progress=progress)
    step = again["steps"][0]
    # A resume reaching from 60 s would ask for -30 s, which is before the tape: the clamp stops it at the
    # recording's own start and says which bound stopped it.
    assert again["expanded_window"] == [0.0, 500.0], again["expanded_window"]
    assert step["from"] == 60.0 and step["moved"] == 60.0, step
    assert step["clamped_by"] == "recording_head" and step["stopping_reason"] == "recording_head", step
    # Against the real end of the tape there is nothing left to reach, and that is reported, not faked.
    at_floor = bi.expansion_window((0.0, 500.0), needs, per_side=90.0,
                                   progress={"steps": [{"side": "before", "expanded_to": 0.0}]})
    assert at_floor["expanded"] is False
    assert at_floor["steps"][0]["stopping_reason"] == "nothing_further_to_reach", at_floor["steps"]


def _row(timestamp, state, clock=None, evidence="", dots=None, score=(0, 0)):
    """One row in the timeline's own dialect: `state`, prose events, flat nameplate fields.

    Written this way on purpose -- the fixture must speak the format the loader reads, or it proves that
    the test agrees with itself instead of that the pipeline works.
    """
    return {"id": int(timestamp) + 1, "timestamp": float(timestamp),
            "path": "frames/frame-%04d.jpg" % (int(timestamp) + 1), "state": state,
            "confidence": 0.95, "player1": "Alpha", "player2": "Beta",
            "character1": "Alisa", "character2": "Asuka", "score1": score[0], "score2": score[1],
            "round_clock": clock, "evidence": evidence, "win_dots": list(dots or [None, None]),
            "result_transition": False, "frame": "frame-%04d" % (int(timestamp) + 1)}


def _scene_rows():
    """A set opening on a station clear at 96 s, one two-round game, and another clear at 360 s."""
    rows = [_row(96.0, "controller_prompt", evidence="Player 1 press a button on the controller"),
            _row(99.0, "controller_assignment", evidence="Who's using this controller?")]
    rows += [_row(120.0, "gameplay", clock=60, evidence="Round start: ROUND 1 banner, clock at 60",
                  dots=[0, 0]),
             _row(150.0, "gameplay", clock=30, evidence="fighters neutral", dots=[0, 0]),
             _row(200.0, "gameplay", clock=41, evidence="Round-ending 'K.O.' banner", dots=[1, 0]),
             _row(230.0, "gameplay", clock=60, evidence="Round start: ROUND 2 banner", dots=[1, 0]),
             _row(270.0, "gameplay", clock=20, evidence="fighters neutral", dots=[1, 0]),
             _row(300.0, "gameplay", clock=55, evidence="Round-ending 'K.O.' banner", dots=[2, 0]),
             _row(320.0, "post_match_menu", evidence="Rematch / Select Character / Main Menu")]
    rows += [_row(360.0, "controller_prompt", evidence="Player 2 press a button on the controller"),
             _row(363.0, "controller_assignment", evidence="Who's using this controller?")]
    return rows


def _write_scene(tmp_path, rows, end=600.0, interval=1.0, start=0.0, source_duration=None,
                 recording_start=None):
    """A sampling manifest. `start`/`end` are what was asked of the encoder, i.e. cache extent.

    ``source_duration`` / ``recording_start`` are the separate metadata that alone may support a claim
    about the tape; leaving them out is the realistic legacy case, and the boundary layer must then say
    "not in the cache" rather than "the recording ends here".
    """
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    manifest = frames_dir / "sample-manifest.json"
    header = {"source": str(tmp_path / "absent.mp4"), "start": start, "end": end, "interval": interval}
    if source_duration is not None:
        header["source_duration"] = source_duration
    if recording_start is not None:
        header["recording_start"] = recording_start
    header["frames"] = [{"id": index + 1, "timestamp": round(start + index * interval, 6),
                         "path": str(frames_dir / ("frame-%04d.jpg" % (index + 1)))}
                        for index in range(int((end - start) / interval))]
    manifest.write_text(json.dumps(header))
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps(rows))
    # Strings, not Path objects: these go straight into an argv list, and argparse walks its elements as
    # text.
    return str(timeline), str(manifest)


def test_widening_recovers_a_set_opening_from_saved_rows_and_asks_for_nothing(tmp_path):
    """The leading edge was a query limit, so widening reuses rows already on disk and prices nothing.

    This is the property in miniature: the unobserved start disappears because the footage was always in
    the timeline, and the cost of finding that out is zero requests.
    """
    timeline, manifest = _write_scene(tmp_path, _scene_rows())
    narrow = bi.plan_saved_investigation(str(timeline), str(manifest), 600.0, region=(140.0, 400.0),
                                         expand=0.0, routes=())
    edge = (narrow["sets_before"][0]["edges"] or {}).get("start") or {}
    assert edge.get("limit") == "analysis_window", edge
    assert narrow["expansion"]["stopping_reasons"] == ["expansion_disabled"]

    grown = bi.plan_saved_investigation(str(timeline), str(manifest), 600.0, region=(140.0, 400.0),
                                        expand=90.0, routes=())
    block = grown["expansion"]
    assert block["triggered"] is True and block["expanded_window"][0] == 50.0, block
    assert block["observations"]["expanded"] > block["observations"]["original"], block["observations"]
    assert block["priced_only_missing"]["new_reads"] == 0, \
        "rows already on disk were re-priced as reads"
    assert block["within_allowance"] is True
    widened = (block["sets_after"][0]["edges"] or {}).get("start") or {}
    assert widened.get("limit") is None, \
        "the set opening was recovered and the candidate still reports it missing: %r" % widened


def test_a_partial_cache_stops_the_walk_without_claiming_truncation(tmp_path):
    """A cache sampled from 150 s tells us nothing about 0-150 s, and must not widen or claim either.

    This is the review's repro through the public planner: the sampling bounds are not the recording's, so
    the head is `not_in_cache` -- unexamined footage, recoverable by sampling the source -- and the widener
    declines to reach for frames the cache has never held.
    """
    rows = [row for row in _scene_rows() if row["timestamp"] >= 150.0]
    timeline, manifest = _write_scene(tmp_path, rows, start=150.0, end=400.0)
    plan = bi.plan_saved_investigation(str(timeline), str(manifest), 400.0, region=(150.0, 399.0),
                                       expand=90.0, routes=())
    edge = (plan["sets_before"][0]["edges"] or {}).get("start") or {}
    assert edge.get("limit") == "not_in_cache", edge
    assert plan["expansion"]["triggered"] is False
    assert plan["expansion"]["blocked_by_edge"], "a blocked edge has to be named, not silently skipped"
    assert [entry["limit"] for entry in plan["expansion"]["blocked_by_edge"]] == ["not_in_cache"]
    bounds = plan["evidence"]["source_bounds"]
    assert bounds["cache_start"] == 150.0 and bounds["recording_start"] is None, bounds
    # The caller supplied a probed duration, which legitimately says where the tape ends; nothing here says
    # where it begins, and that asymmetry is the whole fix.
    assert bounds["recording_end"] == 400.0 and bounds["proven"] is True, bounds


def test_a_proven_recording_start_stops_the_walk_without_spending(tmp_path):
    """Same geometry, with explicit source metadata: now the truncation claim is earned."""
    rows = [row for row in _scene_rows() if row["timestamp"] >= 150.0]
    timeline, manifest = _write_scene(tmp_path, rows, start=150.0, end=400.0, source_duration=250.0,
                                      recording_start=150.0)
    plan = bi.plan_saved_investigation(str(timeline), str(manifest), 400.0, region=(150.0, 399.0),
                                       expand=90.0, routes=())
    edge = (plan["sets_before"][0]["edges"] or {}).get("start") or {}
    assert edge.get("limit") == "recording_head", edge
    assert plan["expansion"]["triggered"] is False
    assert plan["expansion"]["blocked_by_edge"]


def test_marks_are_never_a_prerequisite_for_recovery(tmp_path):
    """Shifting or dropping the operator's spans must not change whether recovery is offered.

    Marks were once the only way a run knew where to look, which quietly made an approximate human list a
    dependency of the pipeline. The trigger is a candidate's own edge, so a wrong mark moves nothing.
    """
    timeline, manifest = _write_scene(tmp_path, _scene_rows())
    unmarked = bi.plan_saved_investigation(str(timeline), str(manifest), 600.0, region=(140.0, 400.0),
                                           expand=90.0, routes=())
    marks_path = tmp_path / "marks.json"
    marks_path.write_text(json.dumps([{"start": 177.0, "end": 437.0}]))
    shifted = bi.plan_saved_investigation(str(timeline), str(manifest), 600.0, region=(140.0, 400.0),
                                          expand=90.0, routes=(), marks=str(marks_path))
    assert unmarked["expansion"]["triggered"] is True
    assert shifted["expansion"]["triggered"] is True, "a shifted operator span vetoed automatic recovery"
    assert shifted["expansion"]["expanded_window"] == unmarked["expansion"]["expanded_window"]


def test_an_expansion_that_outruns_the_allowance_says_so(tmp_path):
    """A widening that cannot fit the budget is a stopped widening, not a bigger invoice."""
    rows = _scene_rows() + [_row(100 + step, "gameplay", clock=60 - (step % 55),
                                 evidence="fighters neutral, clock falling")
                            for step in range(1, 250)]
    timeline, manifest = _write_scene(tmp_path, rows)
    plan = bi.plan_saved_investigation(str(timeline), str(manifest), 600.0, region=(150.0, 400.0),
                                       expand=250.0, max_requests=1)
    block = plan["expansion"]
    assert block["triggered"] is True, block["stopping_reasons"]
    assert "budget_exhausted" in block["stopping_reasons"], block["stopping_reasons"]
    assert block["within_allowance"] is False and block["over_allowance_by"] > 0, block
    assert block["outside_allowance"]["expanded"] > block["outside_allowance"]["original"], block


# ------------------------------------------- the public CLI, executing: every route mocked at one seam
#
# `_client` is the only place the executor builds a transport, so patching it covers all three routes at
# once. Patching one and trusting the others to have nothing to do is the mistake that once left two live
# clients pointed at a real endpoint inside a test -- the difference between that and an unapproved spend
# was only whether anything happened to be listening.

class _AnyRouteClient:
    """One stand-in for all three routes, at the seam the ledger itself wraps.

    ``boundary_budget.attach`` wraps ``client._post``, so a fake answering from ``classify`` alone would
    count calls the ledger never saw -- and the cap is partly what these tests are about. ``classify``
    therefore goes through ``_post``, exactly as the real vision client does.
    """

    instances = []

    def __init__(self, route, **kw):
        self.route = route
        self.calls = 0
        self.kw = kw
        _AnyRouteClient.instances.append(self)

    def _post(self, body):
        return {"choices": [{"message": {"content": "[]"}}], "usage": {"total_tokens": 10}}

    def classify(self, frames):
        self.calls += 1
        self._post({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "%s:%s" % (self.route,
                                                ",".join(str(f["id"]) for f in frames))}]}]})
        return [{"id": f["id"], "state": "gameplay", "confidence": 0.9,
                 "round_clock": max(0, 60 - 5 * (f["id"] % 12)), "round_call": None, "round_end": None,
                 "result_transition": False, "win_dots_p1": 1, "win_dots_p2": 2,
                 "characters": ["Reina", "Azucena"], "evidence": "clock read from frame"}
                for f in frames]


@pytest.fixture
def cli_clients(monkeypatch):
    _AnyRouteClient.instances = []

    def fake(route, **kw):
        return _AnyRouteClient(route, **kw)

    monkeypatch.setattr(bi, "_client", fake)
    return lambda: [client for client in _AnyRouteClient.instances]


def _run_cli(argv):
    """Drive ``boundary_cli.main`` and read back the JSON it prints, exactly as an operator would."""
    import io
    from contextlib import redirect_stdout
    from tekken_vod_helper.boundary_cli import main

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        main(argv)
    text = buffer.getvalue()
    return json.loads(text[text.index("{"):])


def _widening_scene(tmp_path):
    rows = _scene_rows() + [_row(float(t), "gameplay", clock=60 - (t % 55),
                                 evidence="fighters neutral, clock falling")
                            for t in range(55, 150)]
    timeline, manifest = _write_scene(tmp_path, rows, end=600.0)
    return str(timeline), str(manifest)


def test_the_public_cli_executes_the_widened_window_from_the_narrow_request(tmp_path, cli_clients):
    """One seam, all routes fake, and a run started at 150-400 that works 60-400 without being told to."""
    timeline, manifest = _widening_scene(tmp_path)
    out = tmp_path / "live"
    result = _run_cli([
        "investigate-games", "--timeline", timeline, "--frame-manifest", manifest,
        "--duration", "600", "--region", "150", "400", "--step", "2", "--no-format",
        "--routes", "state", "--expand-window", "90", "--max-requests", "12",
        "--budget-file", str(tmp_path / "attempts.json"), "--budget-cap", "12",
        "--budget-minutes", "10", "--purpose", "cli-widening", "--output-dir", str(out)])

    assert result["region_requested"] == [150.0, 400.0], result["region_requested"]
    assert result["region_executed"] == [60.0, 400.0], result["region_executed"]
    expansion = result["expansion"]
    assert expansion["triggered"] is True and expansion["stopping_reasons"] == ["expanded"], expansion
    assert expansion["windows"] == {"original": [150.0, 400.0], "expanded": [60.0, 400.0]}
    assert expansion["observations"]["expanded"] > expansion["observations"]["original"], expansion
    assert cli_clients(), "the run built no client at all, so nothing here proves it could send"
    assert sum(client.calls for client in cli_clients()) == result["requests_sent"] > 0
    # The ledger is the same single cap and deadline the widened run spent from, not a second allowance.
    ledger = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert ledger["attempts_charged"] == result["requests_sent"] <= ledger["cap"] == 12
    assert ledger.get("uncertain_attempts", 0) == 0
    # Both windows survive on disk, in the plan and in the resume cursor.
    persisted = json.loads((out / "expansion.json").read_text(encoding="utf-8"))
    assert persisted["original_window"] == [150.0, 400.0]
    assert persisted["expanded_window"] == [60.0, 400.0]
    assert persisted["steps"][0]["side"] == "before" and persisted["steps"][0]["moved"] == 90.0
    planned = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert planned["window"] == [60.0, 400.0], planned["window"]
    assert planned["expansion"]["windows"] == {"original": [150.0, 400.0], "expanded": [60.0, 400.0]}
    assert planned["expansion"]["triggered"] is True


def test_the_public_cli_refuses_to_widen_without_sourcing_the_tape(tmp_path, cli_clients):
    """No source metadata and no probed duration: the run keeps the window it was given."""
    rows = [row for row in _scene_rows() if row["timestamp"] >= 150.0]
    timeline, manifest = _write_scene(tmp_path, rows, start=150.0, end=400.0)
    out = tmp_path / "blind"
    result = _run_cli([
        "investigate-games", "--timeline", timeline, "--frame-manifest", manifest,
        "--duration", "0", "--region", "150", "399", "--step", "2", "--no-format", "--routes", "state",
        "--expand-window", "90", "--max-requests", "6",
        "--budget-file", str(tmp_path / "blind-attempts.json"), "--budget-cap", "6",
        "--budget-minutes", "10", "--purpose", "cli-blind", "--output-dir", str(out)])
    assert result["region_executed"] == [150.0, 399.0], result["region_executed"]
    assert result["expansion"]["triggered"] is False
    assert "not_in_cache" in [edge["limit"] for edge in result["expansion"]["edges"]], result["expansion"]


def test_a_resumed_cli_run_reuses_the_saved_expansion_instead_of_walking_again(tmp_path, cli_clients):
    """The second invocation must inherit the first's window, not re-derive a wider one from scratch."""
    timeline, manifest = _widening_scene(tmp_path)
    out = tmp_path / "live"
    first = _run_cli(["investigate-games", "--timeline", timeline, "--frame-manifest", manifest,
                      "--duration", "600", "--region", "150", "400", "--step", "2", "--no-format",
                      "--routes", "state", "--expand-window", "90", "--max-requests", "12",
                      "--budget-file", str(tmp_path / "attempts.json"), "--budget-cap", "24",
                      "--budget-minutes", "10", "--purpose", "cli-widening",
                      "--output-dir", str(out)])
    charged = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))["attempts_charged"]
    sent_before = first["requests_sent"]

    resumed = _run_cli(["investigate-games", "--timeline", timeline, "--frame-manifest", manifest,
                        "--duration", "600", "--region", "150", "400", "--step", "2", "--no-format",
                        "--routes", "state", "--expand-window", "900", "--max-requests", "12",
                        "--budget-file", str(tmp_path / "attempts.json"), "--budget-cap", "24",
                        "--budget-minutes", "10", "--purpose", "cli-widening",
                        "--output-dir", str(out), "--resume"])
    # A wider --expand-window on resume changes nothing: the run's own cursor is the record of what it
    # decided, and the window it executed is part of the identity a resume is checked against.
    assert resumed["region_executed"] == [60.0, 400.0], resumed["region_executed"]
    assert resumed["expansion"]["triggered"] is False
    assert resumed["expansion"]["stopping_reasons"] == ["already_expanded"], resumed["expansion"]
    assert resumed["expansion"]["expanded_window"] == [60.0, 400.0]
    after = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert after["attempts_charged"] >= charged
    assert resumed["requests_sent"] <= sent_before, \
        "the resume re-asked work the first run had already paid for: %s of %s" % (
            resumed["requests_sent"], sent_before)
    assert (out / "expansion.json").is_file()


def test_the_public_cli_stops_at_its_cap_with_the_widening_recorded(tmp_path, cli_clients):
    """Budget exhaustion on the live path, decided before the ledger was consulted.

    A second invocation over one shared ledger is the honest way to reach this stop: the cap belongs to the
    ledger, so the widening still happens -- it spends rows, not attempts -- and only the send that follows
    is refused. Enforcing a cap by *not* widening would change what a candidate is told about its own
    footage, which is the opposite of this feature.
    """
    timeline, manifest = _widening_scene(tmp_path)
    ledger_path = tmp_path / "capped-attempts.json"
    first = _run_cli(["investigate-games", "--timeline", timeline, "--frame-manifest", manifest,
                      "--duration", "600", "--region", "150", "400", "--step", "2", "--no-format",
                      "--routes", "state", "--expand-window", "90", "--max-requests", "2",
                      "--budget-file", str(ledger_path), "--budget-cap", "2", "--budget-minutes", "10",
                      "--purpose", "cli-cap", "--output-dir", str(tmp_path / "capped")])
    assert json.loads(ledger_path.read_text(encoding="utf-8"))["attempts_charged"] == 2

    out = tmp_path / "capped-again"
    # A fresh directory over a spent ledger is refused outright, before any client exists: the widening is
    # decided from rows, but the attempts it would need are already gone, and borrowing a later command's
    # allowance is not this command's to do.
    with pytest.raises(SystemExit):
        _run_cli(["investigate-games", "--timeline", timeline, "--frame-manifest", manifest,
                  "--duration", "600", "--region", "150", "400", "--step", "2", "--no-format",
                  "--routes", "state", "--expand-window", "90", "--max-requests", "2",
                  "--budget-file", str(ledger_path), "--budget-cap", "2", "--budget-minutes", "10",
                  "--purpose", "cli-cap", "--output-dir", str(out)])
    assert not out.exists() or not (out / "requests").exists(), "the refused run left sends behind"
    assert json.loads(ledger_path.read_text(encoding="utf-8"))["attempts_charged"] == 2
    # And the run that did spend charged exactly the shared cap, on the widened window it chose itself.
    assert first["requests_sent"] == 2, first["requests_sent"]
    assert json.loads((tmp_path / "capped/expansion.json").read_text(
        encoding="utf-8"))["expanded_window"] == [60.0, 400.0]
    assert first["region_executed"] == [60.0, 400.0]
    # Not asserted here: a resumed run whose ledger is spent stops with `supported_outcome` rather than a
    # budget reason, because in this scene its two sends had already answered everything the widened window
    # asked. The plan-side stop is `test_an_expansion_that_outruns_the_allowance_says_so`; this one is the
    # pre-flight refusal, which cannot spend at all.


# ---------------------------------------------------------------- frame identity is the file
#
# A crop reading is stored under the path the run was *given*, and callers spell that differently: the
# cache manifest says ``build/work/bb9/.../frame-4298.jpg`` and a hand-written annotation file says
# ``/var/home/andy/.../frame-4298.jpg``. Six executed crop answers landed under the second spelling and
# the crop route priced all six frames again, because reuse asked whether a string was in a dict. The two
# tests below pin the identity rule and then prove the recording itself stopped re-asking them.


def test_frame_identity_is_one_file_not_one_spelling(tmp_path):
    """The same picture through two paths is one identity; two pictures sharing a name are not.

    The second half is the reason this is not ``Path(...).name``: ``frame-0413.jpg`` exists in both of
    this recording's caches with different contents, and joining them into one pip series is the exact
    second-framing corruption the corpus guards already refuse elsewhere.
    """
    first = tmp_path / "detect-v1" / "frames" / "frame-0413.jpg"
    second = tmp_path / "detect-v2" / "frames" / "frame-0413.jpg"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pixels")
    import os
    assert boundary_evidence.frame_identity(str(first)) == boundary_evidence.frame_identity(
        os.path.relpath(first))
    assert boundary_evidence.frame_identity(str(first)) != boundary_evidence.frame_identity(str(second))
    assert boundary_evidence.frame_identity("") == ""


def test_a_reading_keyed_absolutely_still_counts_as_answered(tmp_path):
    """The reuse index is keyed by identity, so the spelling a group file happens to record is irrelevant."""
    frame = tmp_path / "frames" / "frame-0007.jpg"
    frame.parent.mkdir()
    frame.write_bytes(b"pixels")
    readings = {str(frame): {"timestamp": 7.0, "win_dots": [1, 0], "frame": str(frame)}}
    answered = bi.pip_answered_frames([], readings)
    keys = set(answered)
    assert boundary_evidence.frame_identity("frames/frame-0007.jpg") not in keys
    assert list(keys) == [boundary_evidence.frame_identity(str(frame))]


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[1]
         / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json").is_file(),
    reason="the saved BB9 evidence is not present")
def test_the_six_frames_this_recording_answered_are_not_priced_again():
    """The executed batch, asserted on the plan: those frames are reuse now, not work.

    Checked by frame name because that is the thing a reader would otherwise have to trust: the crop bill
    fell from 180 to 174 sends and the reused count rose from 186 to 192 on exactly these six.
    """
    from pathlib import Path as _P
    from tekken_vod_helper import boundary_evidence
    root = _P(__file__).resolve().parents[1]
    evidence = boundary_evidence.evidence(None, facts=str(
        root / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"))
    answered = bi.pip_answered_frames(evidence["observations"],
                                      evidence["crop_readings_by_frame"])
    asked = {_P(str(entry["frame"])).name for entry in
             bi.pip_requests(evidence["observations"],
                             bi.load_cache(str(root / "build/work/bb9/detect-1440p-v1/frames"
                                               "/sample-manifest.json")),
                             None, answered, 400)["requests"]}
    for name in ("frame-0571.jpg", "frame-0573.jpg", "frame-4296.jpg",
                 "frame-4298.jpg", "frame-5991.jpg", "frame-5993.jpg"):
        assert name not in asked, "%s was answered and is being priced again" % name
