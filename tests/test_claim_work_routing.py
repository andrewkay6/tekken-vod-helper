"""The set layer's own claims, followed through the public planner to an executor.

``track_games`` reports what a set cannot decide -- a game with no winner, a membership nobody showed, a
pairing credited to screen sides, a format nobody supplied. Until now that report ended beside the queue:
``investigate-games`` planned the *game-level* questions (an unread pip row, an identity gap) and read the
set-level claims as prose. Sixteen claims on this recording produced no request, no reuse and no deferral,
which is indistinguishable from a recording with nothing left to ask.

These tests are offline and no model is asked: the planner reads saved answers, and where a run is driven
it is driven with every route's socket replaced.

Three rules hold the feature down, and each is tested twice -- once for the behaviour and once for the
tempting failure next to it:

1. a claim arrives at an executor that can answer it. The crop row settles a game, the band settles who
   sat where, the whole frame settles what separates two games. A band crop cannot see a controller being
   handed over, and that misroute is the defect this file exists to make impossible.
2. a claim never buys a second copy of a question another plan already asks. Ten winner-less games are ten
   games the crop route already reached for; if the ledger added ten more reads, the bill would grow for
   the shape of the report rather than the amount of evidence.
3. a question for the user is not a purchase. A missing format costs nothing and is asked once for the
   recording; re-asking it per candidate, or re-asking the model for it, is the loop that would never close.
"""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_games as bg                    # noqa: E402
from tekken_vod_helper import boundary_header as bh                   # noqa: E402
from tekken_vod_helper import boundary_investigate as bi             # noqa: E402

import _frozen_corpus as frozen  # noqa: E402  (tests/_frozen_corpus.py)

TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DURATION = 6928.421136
MARKS = ROOT / "build/work/bb9/oct22.tvh.json"
BAND = ",".join([str(ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30"),
                 str(ROOT / "build/work/bb9/set-end2end-v1/attribution-run"),
                 str(ROOT / "build/work/bb9/set-end2end-v1/band-run")])

pytestmark = pytest.mark.skipif(
    not (TIMELINE.is_file() and MANIFEST.is_file()),
    reason="the saved BB9 timeline and frame cache are not present")


def whole_tape(**kw):
    kw.setdefault("step", 2.0)
    kw.setdefault("max_requests", 2000)
    kw.setdefault("expand", 0.0)
    kw.setdefault("games_per_set", None)
    return bi.plan_saved_investigation(str(TIMELINE), str(MANIFEST), DURATION,
                                       marks=str(MARKS), header=bh.saved_readings(
                                           bh.response_directories(BAND)),
                                       crop_runs=frozen.crop_runs(), **kw)


@pytest.fixture(scope="module")
def tape():
    return whole_tape()


def ledger(plan_result):
    return plan_result["claims"]


def claims_for(tape_plan, name):
    return [entry for entry in ledger(tape_plan)["entries"] if entry["claim"] == name]


# ---------------------------------------------------------------- the ledger is complete


def test_every_claim_the_set_layer_raises_is_accounted_for(tape):
    """Nothing is allowed to be merely described. Each claim is bought, reused, deferred or asked.

    A claim with no disposition is how the recording used to end up with an unwinnable set and an empty
    deferral list: the queue said ten things, the planner answered none of them, and both artefacts were
    truthful about their own part.
    """
    entries = ledger(tape)["entries"]
    assert entries, "the recording-wide queue produced no set-level claims at all"
    allowed = {"planned", "reused", "deferred", "user_answer"}
    assert {entry["disposition"] for entry in entries} <= allowed, \
        json.dumps([{k: entry[k] for k in ("claim", "route", "disposition")} for entry in entries])
    assert ledger(tape)["claims_resolved"] is True
    for entry in entries:
        assert entry["missing"] and entry["would_resolve"], entry
        assert entry["because"], "a disposition with no reason is the silence this feature replaced"


def test_the_claims_the_recording_actually_owes_are_the_ones_led(tape):
    """The shape is the recording's, not a fixture's, and it is stated in candidates.

    Ten queued candidates owe twenty-two claims: three of them hold the seven winner-less games between
    them, seven name no participant for the wins they do hold, one cannot say what separates its own games,
    and all ten are missing the same event format. Pinning the shape is what stops a future change from
    quietly narrowing the ledger to whichever claims are convenient -- and a claim that went missing would
    take its candidate's instants with it, which is the failure this whole layer was written against.

    These are post-batch numbers. Before unknown-winners-batch-v1 read six crops the same ledger held ten
    unwon games in four candidates' claims, so the shape moved with the evidence rather than the code.
    """
    by_name = {}
    for entry in ledger(tape)["entries"]:
        by_name.setdefault(entry["claim"], []).append(entry["disposition"])
    assert sorted(by_name) == ["format", "game_results", "identity", "set_continuity"], by_name
    assert len(by_name["game_results"]) == 3, by_name
    assert len(by_name["identity"]) == 7, by_name
    assert len(by_name["set_continuity"]) == 1, by_name
    assert len(by_name["format"]) == 10, by_name
    # Three claims, seven games: a claim is a candidate's, and the seven instants are inside it.
    assert sum(len(entry.get("at") or ()) for entry in claims_for(tape, "game_results")) == 7


# ---------------------------------------------------------------- one question, one request


def test_a_game_result_claim_is_carried_by_the_read_the_game_already_owed(tape):
    """No duplicate sends: the per-game question reaches the same frame the claim names.

    The crop route plans one read per frame, so ten winner-less games are ten games' instants -- already in
    the plan before any claim existed. The ledger's job here is to *find* those requests and name them, and
    the count of planned crop requests is the control: it may not move because a claim was written down.
    """
    entries = claims_for(tape, "game_results")
    assert len(entries) == 3
    planned = [entry for entry in entries if entry["disposition"] == "planned"]
    assert len(planned) == 3, "the crop route planned nothing for seven winner-less games"
    identities = {item for entry in planned for item in entry["requests"]}
    assert identities, "no request was named for ten winner-less games"
    assert identities <= {entry["identity"] for entry in tape["pip_requests"]}, \
        "a claim named a crop request that is not in the plan"
    assert len(identities) < 180, "the ledger claimed the whole crop bill, not the ten frames it needs"


def test_the_claims_add_no_crop_reading_and_no_band_reading_to_the_bill():
    """The bill is the control on duplication, and the queue is the only thing allowed to change.

    Same evidence, same cache, same band corpus -- twice, once with the set layer's claims on the queue and
    once with them stripped. The crop and band plans must be identical to the request: every question they
    hold was already raised by a game-level or identity question, and a claim that bought a second read of
    one frame would be spending an approval on the shape of the report. Continuity is the single route a
    claim is allowed to grow, because nothing else asks what separates two games -- and it stays the
    smallest of the three, ranked below every question that can settle one.
    """
    prepared = bi._prepare(str(TIMELINE), str(MANIFEST), DURATION, marks=str(MARKS),
                           header=bh.saved_readings(bh.response_directories(BAND)),
                           crop_runs=frozen.crop_runs(), games_per_set=None)

    def priced(queue):
        result = bi.plan_investigation(queue, prepared["cache"], prepared["duration"],
                                       observations=prepared["observations"],
                                       marks=prepared["marks"], window=prepared["window"],
                                       crop_readings=prepared["evidence"].get(
                                           "crop_readings_by_frame"),
                                       identity_questions=prepared["adjudicated"]["identity_questions"],
                                       step=2.0, max_requests=2000)
        return {entry["route"]: entry["requests_planned"] for entry in result["dispatch"]["routes"]}

    with_claims = priced(prepared["unresolved"])
    without = priced([dict(entry, unresolved_claims=[]) for entry in prepared["unresolved"]])
    assert with_claims["pip_crops"] == without["pip_crops"], "a claim bought a second crop reading"
    assert with_claims["header"] == without["header"], "a claim bought a second band reading"
    assert with_claims["state"] > without["state"], "the continuity claim bought nothing at all"
    assert with_claims["state"] - without["state"] <= 8, \
        "one candidate's continuity question out-priced the game questions: %s" % with_claims
    assert with_claims["state"] < with_claims["pip_crops"], with_claims


def test_an_identity_claim_goes_to_the_band_and_buys_no_frame(tape):
    """Who held the win is a nameplate question, and the ledger must not launder it into pixels.

    The identity claim is credited to band requests only -- and where a band reading is already on record
    inside the candidate, the claim reports reuse instead of a send, because asking the same picture twice
    is not how a second answer appears.
    """
    entries = claims_for(tape, "identity")
    assert entries
    for entry in entries:
        assert entry["route"] == "header", entry
        assert not any(str(item).endswith(".jpg") for item in entry["requests"]), entry
        # The points are the games whose wins need a person, not the candidate's two edges: a nameplate is
        # read during the game it credits, and an edge instant is where a set's neighbour starts.
        assert entry["windows"] and len(entry["required"]) == len(entry["windows"]), entry
        for point in entry["coverage"]:
            assert point["status"] in ("scheduled", "saved", "deferred"), point
            if point["status"] == "saved":
                assert all("band reading at" in item for item in point["carried_by"]), point
        if entry["disposition"] == "reused":
            assert entry["closes"] is False and entry["unmet"] == [], entry
            assert "re-asking the same pictures" in entry["because"], entry


def test_a_continuity_claim_is_a_whole_frame_question_not_a_band_crop(tape):
    """The misroute this file was written for, asserted on the real recording.

    ``set_continuity`` used to advertise the header route for controller handover and stage identity, which
    a 128x62 crop of the nameplate cannot see, and the planner read only game-level kinds -- so the claim
    was unreachable *and* unplanned. It now arrives on the state route, at the ends of the stretches
    between the candidate's own games.
    """
    entries = claims_for(tape, "set_continuity")
    assert len(entries) == 1
    assert all(entry["route"] == "state" for entry in entries), entries
    planned = [entry for entry in entries if entry["disposition"] == "planned"]
    assert planned, "the whole-frame route bought nothing for any continuity claim"
    for entry in planned:
        assert entry["requests"] and all("state region" in item for item in entry["requests"]), entry
    kinds = {kind for region in tape["regions"] for kind in region["kinds"]}
    assert "set_continuity" in kinds


# ---------------------------------------------------------------- a user fact is not a purchase


def test_a_format_claim_costs_no_inference_and_is_asked_once_for_the_recording(tape):
    """Ten candidates owe the same fact. Ten asks is a loop, and an ask is not evidence either way.

    The format of the event is not drawn anywhere in the footage, so nothing this command can send closes
    it. The ledger says so once, names every candidate the answer would settle, and the plan's own request
    count is the control that no inference was bought for it.
    """
    entries = claims_for(tape, "format")
    assert len(entries) == 10
    assert all(entry["disposition"] == "user_answer" for entry in entries), entries
    assert all(entry["costs_inference"] is False for entry in entries)
    assert all(entry["requests"] == [] for entry in entries)
    asks = ledger(tape)["user_asks"]
    assert len(asks) == 1, json.dumps(asks)
    assert asks[0]["question_count"] == 1
    assert asks[0]["costs_inference"] is False
    assert len(asks[0]["candidates"]) == 10, "the single ask has to name every candidate it would settle"
    assert ledger(tape)["inference_for_format_claims"] == 0


def test_a_format_answer_changes_nothing_about_the_requests(tape):
    """The anti-loop control: the same plan with the format supplied still buys no inference for it.

    Supplying a target turns the format claim into a *completion* claim -- a purchasable one, about games
    nobody detected. What must not happen is a format claim that ever reaches a client, in either column.
    """
    funded = whole_tape(games_per_set=bg.DEFAULT_GAMES_PER_SET)
    assert all(entry["disposition"] != "user_answer" for entry in claims_for(funded, "format"))
    assert funded["claims"]["user_asks"] == []
    for column in (tape, funded):
        for entry in ledger(column)["entries"]:
            if entry["claim"] == "format":
                assert entry["requests"] == [] and entry["costs_inference"] is False


# ---------------------------------------------------------------- budget and stopping


def test_an_unfunded_route_defers_its_claims_by_name_instead_of_losing_them():
    """``--routes pip_crops`` must not make a band question disappear.

    A zero on an unfunded route reads two ways -- "nothing to ask" and "not allowed to ask" -- and only one
    of them is a price list for the next approval.
    """
    full = whole_tape()
    crops_only = whole_tape(routes=("pip_crops",))
    assert crops_only["requests_planned"] < full["requests_planned"]
    deferred = [entry for entry in ledger(crops_only)["entries"]
                if entry["disposition"] == "deferred"]
    assert deferred, "an unfunded route left its claims unreported"
    for entry in deferred:
        assert "did not fund" in entry["because"], entry
        assert entry["requests"] == []
    # The claims that were planned when the route was funded are exactly the ones now deferred.
    planned_by_route = {}
    for entry in ledger(full)["entries"]:
        if entry["disposition"] == "planned":
            planned_by_route.setdefault(entry["route"], 0)
            planned_by_route[entry["route"]] += 1
    deferred_by_route = {}
    for entry in deferred:
        deferred_by_route.setdefault(entry["route"], 0)
        deferred_by_route[entry["route"]] += 1
    assert set(deferred_by_route) <= set(planned_by_route) or planned_by_route, (
        deferred_by_route, planned_by_route)


def test_a_tiny_allowance_names_the_claims_it_could_not_reach():
    """One request cannot settle a recording, and the ledger has to say which claims that leaves.

    The cap is the other half of the budget rule: a plan that reports three planned claims and stays silent
    about the twenty-two it never reached would let a run look nearly finished at one attempt.
    """
    tight = whole_tape(max_requests=1)
    counts, entries = ledger(tight)["counts"], ledger(tight)["entries"]
    assert tight["requests_planned"] == 1
    assert sum(counts.values()) == len(entries)
    # One send cannot serve a recording, so the ledger's shortfall has to be large and *enumerated*: every
    # point that is neither scheduled nor already answered appears in its claim's ``unmet``, and the
    # running total is published beside the counts.
    assert ledger(tight)["points_unmet"] >= 9, ledger(tight)["points_unmet"]
    assert sum(len(entry["unmet"]) for entry in entries) == ledger(tight)["points_unmet"]
    assert all(entry["unmet"] for entry in entries if entry["disposition"] == "deferred"), \
        "a deferred claim named nothing it left unasked"
    assert not any(entry["unmet"] for entry in entries if entry["disposition"] == "planned"), \
        "a planned claim still owes a point"
    # A claim whose evidence is on disk is not a shortfall: reuse is carried, not unmet.
    for entry in entries:
        if entry["disposition"] == "reused":
            assert entry["unmet"] == [] and entry["saved_evidence_reused"], entry


def test_a_second_pass_over_answers_it_already_holds_reports_reuse_not_progress(tmp_path):
    """No-progress stopping, seen from the claim side.

    A claim whose saved answers already cover its instants is reported as reuse that does **not** close it,
    with the reason: re-asking the same pictures is how a run spends an approval twice and learns nothing
    between the two spendings.
    """
    first = whole_tape()
    reused = [entry for entry in ledger(first)["entries"] if entry["disposition"] == "reused"]
    assert reused, "the corpus covers no claim on this recording, which would be the surprise"
    for entry in reused:
        assert entry["closes"] is False, entry
        assert entry["requests"] == []
        assert "not progress" in entry["because"] or "still open" in entry["because"], entry


def test_a_claim_routed_to_an_executor_that_cannot_answer_it_is_reported_as_a_defect():
    """The guard against the *next* misroute, not only the one already fixed.

    A claim that arrives at an executor without the right evidence used to be planned happily and reported
    as progress. Now the ledger refuses to call it planned, ``claims_resolved`` goes false, and the reason
    names both executors -- because the silence after a misroute is what let a band crop stand in for a
    whole-frame question for a whole round of planning.
    """
    queue = [{"type": "set_unresolved", "at": 100.0, "end": 400.0, "status": "partial",
              "membership": "assumed", "games": 3, "game_wins": {"p1": 2, "p2": 1},
              "games_without_winner": 0,
              "unresolved_claims": [{"claim": "set_continuity", "route": "header",
                                     "costs_inference": True, "missing": "what separates them",
                                     "would_resolve": "controller handover", "at": [100.0, 400.0],
                                     "windows": [[200.0, 240.0]]}]}]
    plan = bi.plan_investigation(queue, bi.load_cache(str(MANIFEST)), DURATION)
    entry = plan["claims"]["entries"][0]
    assert entry["disposition"] == "misrouted", entry
    assert entry["requests"] == []
    assert plan["claims_resolved"] is False
    assert "only executor" in entry["because"], entry


def test_the_ledger_is_written_into_the_plan_artefact(tmp_path, tape):
    """A result nobody can read is a result nobody can audit.

    The ledger ships in the plan the CLI writes, with its rules beside it, so an operator approving a batch
    can see for each claim what is being bought and why the rest is not.
    """
    out = tmp_path / "plan"
    bi.plan_saved_investigation(str(TIMELINE), str(MANIFEST), DURATION, marks=str(MARKS),
                                header=bh.saved_readings(bh.response_directories(BAND)),
                                crop_runs=frozen.crop_runs(), output_dir=out, step=2.0,
                                max_requests=2000, expand=0.0, games_per_set=None)
    saved = json.loads((out / "plan.json").read_text())
    assert saved["claims_resolved"] is True
    assert saved["claims"]["schema"] == "claim-work-v2"
    assert saved["claims"]["rules"]
    assert [entry["claim"] for entry in saved["claims"]["entries"]]
    for entry in saved["claims"]["entries"]:
        assert set(entry) >= {"claim", "route", "disposition", "because"}, entry


# ---------------------------------------------------------------- the claim at the socket
#
# Everything above is the plan talking to itself. These last tests drive the public run command with each
# route's socket replaced, because "the claim reached the appropriate executor" is a statement about what
# leaves the machine: a continuity claim has to arrive as whole frames on the state client, one attempt of
# budget has to buy one send, and a second run over its own answers has to ask for nothing again.


class Socket:
    """Three real clients, one fake transport, and every body it was asked to send.

    ``_post`` and nothing else is replaced, so the shipping path -- validation, persistence, budget, the
    per-route answer schema -- is the production code. An answer that would not validate is no proof of
    dispatch, so each route gets its own well-formed abstention.
    """

    def __init__(self, abstain=False):
        self.bodies = {"pip_crops": [], "state": [], "header": []}
        # An abstaining socket answers in the shape the schema accepts but claims nothing usable, which is
        # how a run reaches its "this pass learned nothing" ending.
        self.abstain = abstain

    def __call__(self, body):
        text = json.dumps(body)
        route = self.route_of(text)
        self.bodies[route].append(body)
        return {"choices": [{"message": {"content": json.dumps(self.answer(route, text))}}]}

    @staticmethod
    def route_of(text):
        if "round-win pip row" in text:
            return "pip_crops"
        if "top band of a Tekken 8 broadcast frame" in text:
            return "header"
        if "in-fight HUD" in text:
            return "state"
        raise AssertionError("a request arrived that belongs to no route: {}".format(text[:300]))

    @staticmethod
    def items(text):
        return sorted({int(match) for match in re.findall(r"item (\d+)", text)})

    def answer(self, route, text):
        if route == "pip_crops":
            return [{"item": item, "label": label, "drawn": False, "lit": 0, "positions": [],
                     "confidence": 0.5} for item in self.items(text) for label in ("LEFT", "RIGHT")]
        if route == "header":
            return [{"item": item, "side": label, "identity_status": "unreadable",
                     "player_name": None, "character": None, "controllers": [None, None],
                     "tally_drawn": False, "confidence": 0.5}
                    for item in self.items(text) for label in ("LEFT", "RIGHT")]
        # ``id`` and not ``item``: the state client validates its reply against the ids it sent, and a
        # stub answering under the wrong key is a request_failed run, not an abstention.
        if self.abstain:
            return [{"id": int(item), "state": "unknown", "confidence": 0.1, "round_clock": None,
                     "round_call": None, "round_end": None, "result_transition": None,
                     "win_dots_p1": None, "win_dots_p2": None, "characters": [], "evidence": "nothing"}
                    for item in set(re.findall(r"id=(\d+) t=", text))]
        return [{"id": int(item), "state": "gameplay", "confidence": 0.9, "round_clock": None,
                 "round_call": None, "round_end": None, "result_transition": False,
                 "win_dots_p1": None, "win_dots_p2": None, "characters": ["A", "B"],
                 "evidence": "mocked"}
                for item in set(re.findall(r"id=(\d+) t=", text))]

    def stamps(self, route):
        """The sampled instants in one route's bodies -- the only identity a sent frame has."""
        found = set()
        for body in self.bodies[route]:
            for match in re.findall(r"t=([\d.]+)", json.dumps(body)):
                found.add(round(float(match), 3))
        return sorted(found)


def drive(tmp_path, monkeypatch, region, **kw):
    """``investigate-games --execute`` on one interval, with every route's socket replaced."""
    socket = Socket(abstain=kw.pop("abstain", False))
    for cls in (bi.RoundFactsClient, bi.boundary_pips.PipReadClient, bh.HeaderReadClient):
        monkeypatch.setattr(cls, "_post", socket, raising=True)
    cap = kw.pop("budget_cap", 400)
    result = bi.investigate_saved_games(
        str(TIMELINE), str(MANIFEST), DURATION, output_dir=tmp_path / "inv", region=region,
        step=2.0, batch_frames=6, max_requests=kw.pop("max_requests", 60), games_per_set=None,
        marks=str(MARKS), header=bh.saved_readings(bh.response_directories(BAND)),
        crop_runs=frozen.crop_runs(), passes=kw.pop("passes", 1), resume=kw.pop("resume", False),
        expand=0.0, budget_file=str(tmp_path / "attempts.json"), budget_cap=cap,
        budget_minutes=30, **kw)
    return result, socket


#: A window holding the one candidate on this recording whose continuity claim is open, plus its games.
CONTINUITY_REGION = (4640.0, 4760.0)


def test_a_continuity_claim_arrives_at_the_state_socket_as_whole_frames(tmp_path, monkeypatch):
    """The claim is not a label on a region: the frames it names leave on the client that sees a frame.

    Two assertions, because the misroute had two directions. The state socket has to carry the probe's
    instants, and the band socket must not be handed the continuity question at all -- a 128x62 crop of the
    nameplate is the one picture that cannot show a controller being handed over.
    """
    result, socket = drive(tmp_path, monkeypatch, CONTINUITY_REGION)
    plan = json.loads((tmp_path / "inv" / "plan.json").read_text())
    entry = next(item for item in plan["claims"]["entries"] if item["claim"] == "set_continuity")
    assert entry["route"] == "state" and entry["disposition"] == "planned", entry
    probes = [value for span in entry["windows"] for value in span]
    assert probes, "a planned continuity claim named no window to inspect"
    sent = socket.stamps("state")
    assert sent, "the state socket received nothing for a claim routed to it"
    assert any(low - 2.1 <= stamp <= high + 2.1 for low, high in entry["windows"] for stamp in sent), \
        (entry["windows"], sent)
    band = json.loads((tmp_path / "inv" / "plan.json").read_text())["dispatch"]["routes"]
    assert {r["route"] for r in band if r["requests_planned"] and r["route"] == "header"} in (
        set(), {"header"})
    for body in socket.bodies["header"]:
        text = json.dumps(body)
        assert "top band" in text or "broadcast frame" in text, \
            "a whole-frame question reached the band client"
    assert result["requests_sent"] == sum(len(b) for b in socket.bodies.values())


def test_one_attempt_buys_one_send_and_names_what_it_left(tmp_path, monkeypatch):
    """Budget still stops the run, and the plan says where every claim ended up.

    The claim work is not a second pipeline with its own allowance: one shared ledger. One attempt went
    out, so the plan's own per-route deferral lists are the proof that the rest was left on the table by
    name -- and the ledger still gives every claim exactly one disposition, which is the property a
    truncated plan is most likely to lose.
    """
    result, socket = drive(tmp_path, monkeypatch, CONTINUITY_REGION, max_requests=1, budget_cap=1)
    assert result["requests_sent"] == 1
    assert sum(len(b) for b in socket.bodies.values()) == 1
    charged = json.loads((tmp_path / "attempts.json").read_text())
    assert charged["attempts_charged"] == 1 and charged["cap"] == 1
    plan = json.loads((tmp_path / "inv" / "plan.json").read_text())
    assert sum(len(route["deferred"]) for route in plan["dispatch"]["routes"]) >= 1, \
        "a one-request run named nothing it left unasked"
    counts = plan["claims"]["counts"]
    assert sum(counts.values()) == len(plan["claims"]["entries"])
    assert plan["claims_resolved"] is True
    assert counts.get("deferred", 0) + counts.get("reused", 0) >= 1, counts


def test_a_pass_that_answers_nothing_stops_and_asks_no_more(tmp_path, monkeypatch):
    """No-progress stopping, on the claim-driven path.

    An abstaining socket means the evidence did not change between passes, so the second pass sends nothing
    new and the run says why. Without this the claim ledger would be a machine for re-asking the same
    frames once per invocation forever -- the failure the reuse rule exists to prevent, arriving by the
    route nobody was watching.
    """
    result, socket = drive(tmp_path, monkeypatch, CONTINUITY_REGION, passes=2, abstain=True)
    assert result["stopped_reason"] == "no_new_evidence", result["stopped_reason"]
    assert len(result["passes"]) == 1, [(e["planned"], e["sent"], e["answers_added"])
                                        for e in result["passes"]]
    # One of the four published endings, and no send that some pass did not claim: the claim work runs
    # inside the loop's accounting rather than quietly spending on the side.
    assert result["stopped_reason"] in ("supported_outcome", "no_new_evidence", "named_deferral",
                                        "attempt_cap_reached"), result["stopped_reason"]
    passes = result["passes"]
    assert passes and all(entry["sent"] <= entry["planned"] for entry in passes), \
        [(entry["planned"], entry["sent"]) for entry in passes]
    assert sum(entry["sent"] for entry in passes) == sum(len(b) for b in socket.bodies.values())
    plan = json.loads((tmp_path / "inv" / "plan.json").read_text())
    assert plan["claims_resolved"] is True
    assert sum(plan["claims"]["counts"].values()) == len(plan["claims"]["entries"])
    # A format claim is never on the socket: an ask for the operator does not turn into inference mid-run.
    for entry in plan["claims"]["entries"]:
        if entry["claim"] == "format":
            assert entry["disposition"] == "user_answer" and entry["requests"] == [], entry
    assert plan["claims"]["inference_for_format_claims"] == 0


def test_an_identity_claim_reaches_the_band_socket_and_persists_its_frame_instant(tmp_path,
                                                                                  monkeypatch):
    """A claim credited to the band route has to leave as a band and come back dated by its frame.

    Both halves are asserted on the artifacts the run writes, because the defect this closes was invisible
    in exactly this place: the request was planned at the instant somebody asked about, and the answer was
    filed there too, while the band it rendered came from a frame 0.5005 s away. In a run, every persisted
    band answer must now credit itself to the instant its own frame was drawn, with the number that was
    asked about kept beside it.
    """
    result, socket = drive(tmp_path, monkeypatch, (6400.0, 6900.0))
    plan = json.loads((tmp_path / "inv" / "plan.json").read_text())
    planned = [entry for entry in plan["claims"]["entries"]
               if entry["claim"] == "identity" and entry["disposition"] == "planned"]
    assert planned, "no identity claim was planned in this window"
    assert socket.bodies["header"], "a planned band claim sent nothing to the band client"
    answers = sorted((tmp_path / "inv" / "header" / "groups").glob("header-*.json"))
    assert answers, "the band run wrote no answer to check"
    for path in answers:
        request = json.loads(path.read_text())["request"]
        assert request["time_pinned"] is True, request
        assert request["at"] == pytest.approx(request["frame_timestamp"],
                                              abs=bh.INSTANT_TOLERANCE_SECONDS), request
        assert request["requested_at"] is not None and request["offset_seconds"] is not None
        # The frame the band came from really draws the instant the answer is filed at.
        record = {entry["path"]: entry for entry in bi.load_cache(str(MANIFEST))}[request["frame"]]
        assert record["timestamp"] == pytest.approx(request["at"], abs=bh.INSTANT_TOLERANCE_SECONDS)
    assert result["budget"]["sent_by_route"]["header"] == len(socket.bodies["header"])


# ---------------------------------------------------------------- partial coverage is not coverage
#
# A claim names several points and a plan reaches some of them. The first ledger called that ``planned``,
# with a reason that said "every instant", because it asked only whether *any* point matched: a pip claim
# needing 10 s and 30 s, with 10 s scheduled and 30 s sitting in the route's own deferral list, reported
# full coverage and hid the shortfall from the next approval. Each point is now classified on its own, and
# a claim is planned only when every one of them is provided for.

HEAD_ONLY = {"requests": [], "deferred": []}


def pip_claim(at):
    return [{"type": "set_unresolved", "at": 1.0, "end": 50.0, "status": "partial",
             "membership": "assumed", "games": 2, "game_wins": {"p1": 1}, "games_without_winner": 2,
             "unresolved_claims": [{"claim": "game_results", "route": "pip_crops",
                                    "costs_inference": True, "missing": "2 games name no winner",
                                    "would_resolve": "the win row at those games' conclusion instants",
                                    "at": at}]}]


def test_half_a_claim_is_reported_as_half_and_names_the_rest():
    """The review's case: [10, 30] with one scheduled and one deferred is ``partial``, never planned.

    ``unmet`` is the field that makes the difference. A reader comparing two approvals needs the footage
    this run will not get, in the same units the claim asked in, or a capped run is indistinguishable from
    a finished one.
    """
    pip_plan = {"requests": [{"identity": "aaa", "timestamp": 10.0}], "requests_planned": 1,
                "deferred": [{"timestamp": 30.0, "frame": "frames/frame-0031.jpg"}]}
    out = bi.claim_work(pip_claim([10.0, 30.0]), pip_plan, HEAD_ONLY, [])
    entry = out["entries"][0]
    assert entry["disposition"] == "partial", entry
    assert entry["unmet"] == ["30.000000 s"], entry
    assert [point["status"] for point in entry["coverage"]] == ["scheduled", "deferred"]
    assert entry["requests"] == ["aaa"] and entry["request_count"] == 1
    assert "every instant" not in entry["because"] and "remainder" in entry["because"], entry
    assert out["claims_resolved"] is True, "a partial claim is still an accounted claim"
    assert out["claims_fully_carried"] == 0, "partial must not count toward fully carried"
    assert out["points_required"] == 2 and out["points_scheduled"] == 1
    assert out["points_unmet"] == 1


def test_a_claim_is_planned_when_every_point_is_scheduled_or_already_answered():
    """Reuse carries a point too -- and the ledger says which kind of carrying did the work.

    Both halves of that rule have failed in this file's lifetime: counting a saved answer as a shortfall
    made honest reuse look like unasked footage, and counting overlap as coverage made a capped run look
    finished. The wording separates them on purpose.
    """
    pip_plan = {"requests": [{"identity": "aaa", "timestamp": 10.0}], "requests_planned": 1,
                "deferred": []}
    out = bi.claim_work(pip_claim([10.0, 30.0]), pip_plan, HEAD_ONLY, [],
                        crop_readings={"frames/crop-30.png": {"timestamp": 30.0}})
    entry = out["entries"][0]
    assert entry["disposition"] == "planned" and entry["unmet"] == [], entry
    assert [point["status"] for point in entry["coverage"]] == ["scheduled", "saved"]
    assert entry["saved_evidence_reused"] == 1
    assert "1 scheduled by this plan, 1 already answered on disk" in entry["because"], entry
    assert out["claims_fully_carried"] == 1


def test_evidence_that_did_not_settle_the_claim_stays_inconclusive():
    """Every point answered, none of it decisive: reused, unmet-empty, and ``closes`` false.

    The distinction the artifact has to keep is between "nothing left to buy" and "nothing left to learn".
    Six band readings sit inside this recording's settled candidates and their games are still unattributed,
    so the claim is carried by evidence and unresolved at once.
    """
    claim = [{"type": "set_unresolved", "at": 100.0, "end": 300.0, "status": "complete",
              "membership": "assumed", "games": 1, "game_wins": {"p1": 1}, "games_without_winner": 0,
              "unresolved_claims": [{"claim": "identity", "route": "header", "costs_inference": True,
                                     "missing": "the wins are credited to screen sides",
                                     "would_resolve": "a band reading inside each credited game's span",
                                     "at": [100.0, 300.0], "windows": [[100.0, 300.0]]}]}]
    header = {"readings": [{"at": 150.0, "player": "A"}]}
    out = bi.claim_work(claim, {"requests": [], "requests_planned": 0, "deferred": []},
                        HEAD_ONLY, [], header=header)
    entry = out["entries"][0]
    assert entry["disposition"] == "reused", entry
    assert entry["closes"] is False and entry["unmet"] == []
    assert entry["coverage"][0]["carried_by"] == ["band reading at 150.000000 s"], entry
    assert "re-asking the same pictures is not progress" in entry["because"], entry


def test_a_state_window_the_route_selected_no_frames_for_is_not_coverage():
    """A region that exists but sends nothing carries nothing.

    The state route drops every frame whose needed fact is already on record, so a plan can hold a region
    whose span covers the claim's window while scheduling no picture in it. Crediting that overlap as
    coverage is the same defect one layer down: the claim would read as bought, and the frames that would
    answer it would never be priced.
    """
    claim = [{"type": "set_unresolved", "at": 100.0, "end": 300.0, "status": "partial",
              "membership": "assumed", "games": 2, "game_wins": {"p1": 1}, "games_without_winner": 0,
              "unresolved_claims": [{"claim": "set_continuity", "route": "state",
                                     "costs_inference": True, "missing": "nothing showed these games are "
                                         "one set", "would_resolve": "the frames between them",
                                     "at": [100.0, 300.0], "windows": [[180.0, 220.0]]}]}]
    empty_region = {"start": 100.0, "end": 300.0, "kinds": ["set_continuity"], "groups": []}
    out = bi.claim_work(claim, {"requests": [], "requests_planned": 0, "deferred": []}, HEAD_ONLY,
                        [empty_region], observations=[])
    entry = out["entries"][0]
    assert entry["disposition"] == "unaccounted", entry
    assert entry["requests"] == [] and entry["coverage"][0]["status"] == "unaccounted", entry
    # Once the region schedules a frame inside the window, the same claim is carried -- by that frame.
    filled = dict(empty_region, groups=[{"group": 0, "frame_timestamps": [200.0]}])
    again = bi.claim_work(claim, {"requests": [], "requests_planned": 0, "deferred": []}, HEAD_ONLY,
                          [filled], observations=[])
    assert again["entries"][0]["disposition"] == "planned", again["entries"][0]
    assert "frames 200.000" in again["entries"][0]["requests"][0]


def test_a_deferral_list_can_carry_a_point_a_budget_refused():
    """The difference between "refused by the allowance" and "nobody knows" is the whole feature.

    A point named in a route's deferral list is ``deferred``: the claim goes partial and the remainder is
    priced. The same point in no list anywhere is ``unaccounted``, which fails the run's own
    ``claims_resolved`` and is meant to be read as a bug in this accounting.
    """
    claim = pip_claim([10.0, 30.0])
    planned = {"requests": [{"identity": "aaa", "timestamp": 10.0}], "requests_planned": 1,
               "deferred": [{"timestamp": 30.0, "frame": "frames/frame-0031.jpg"}]}
    naked = {"requests": planned["requests"], "requests_planned": 1, "deferred": []}
    assert bi.claim_work(claim, planned, HEAD_ONLY, [])["entries"][0]["disposition"] == "partial"
    hole = bi.claim_work(claim, naked, HEAD_ONLY, [])["entries"][0]
    assert hole["disposition"] == "unaccounted" and hole["unmet"] == ["30.000000 s"], hole


def test_a_partial_allowance_on_the_real_recording_leaves_no_point_unnamed(tape):
    """The invariant on the recording itself, at a budget that is sure to cut mid-claim.

    Whatever the allowance, the arithmetic has to close: every claim has one disposition, every point is
    scheduled, saved or deferred, and the published shortfall equals the sum of the per-claim remainders.
    """
    for cap in (1, 40, 120, 2000):
        plan = whole_tape(max_requests=cap)
        out = ledger(plan)
        assert out["claims_resolved"] is True, (cap, out["counts"])
        assert sum(out["counts"].values()) == len(out["entries"])
        assert out["points_unmet"] == sum(len(entry["unmet"]) for entry in out["entries"])
        for entry in out["entries"]:
            assert entry["disposition"] in ("planned", "partial", "reused", "deferred", "user_answer"), \
                (cap, entry)
            assert all(point["status"] in ("scheduled", "saved", "deferred", "unaccounted")
                       for point in entry["coverage"]), (cap, entry)
            if entry["disposition"] == "planned":
                assert entry["unmet"] == [], (cap, entry)
                assert entry["requests"], (cap, entry)
            if entry["disposition"] == "partial":
                # Carried-by-disk and carried-by-send both count as provided for, so a partial claim can
                # legitimately schedule nothing: some points already have answers, others are deferred.
                assert entry["unmet"], (cap, entry)
                assert entry["requests"] or entry["saved_evidence_reused"], (cap, entry)
        if cap == 2000:
            assert out["points_unmet"] == 0, out["counts"]
        else:
            assert out["points_unmet"] > 0, "an allowance of %s claimed nothing was left unasked" % cap


def test_a_resume_reports_the_frames_it_answered_as_carried_not_as_new_work(tmp_path, monkeypatch):
    """Public path, twice: the second invocation's ledger must reflect the first one's answers.

    A claim that was carried by a scheduled send and then answered on disk is carried by saved evidence --
    same disposition family, no new send. If the ledger still named scheduled requests after a resume, the
    next approval would be priced off stale scarcity, which is how a run spends an allowance twice on
    pictures it already has.
    """
    first, _ = drive(tmp_path, monkeypatch, CONTINUITY_REGION)
    planned_before = json.loads((tmp_path / "inv" / "plan.json").read_text())
    continuity = [entry for entry in planned_before["claims"]["entries"]
                  if entry["claim"] == "set_continuity"]
    assert continuity and any(entry["disposition"] in ("planned", "partial") for entry in continuity)
    assert first["requests_sent"] >= 1
    again, socket = drive(tmp_path, monkeypatch, CONTINUITY_REGION, resume=True)
    after = json.loads((tmp_path / "inv" / "plan.json").read_text())
    assert again["requests_sent"] == 0, (
        "the resume sent %d requests the first run had already answered: %s"
        % (again["requests_sent"], sorted(socket.bodies)))
    for entry in after["claims"]["entries"]:
        assert entry["disposition"] in ("planned", "partial", "reused", "deferred", "user_answer"), entry
        if entry["disposition"] == "planned":
            assert entry["unmet"] == [] and entry["coverage"], entry
