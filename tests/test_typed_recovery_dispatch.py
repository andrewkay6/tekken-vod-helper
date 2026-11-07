"""Typed dispatch: three executors, one allowance, and the pip row graded wherever it came from.

The plan used to *name* a route and then send every question to one whole-frame client. Typing a question
is not asking it: a ``pip_crops`` label on a region whose frames went to ``RoundFactsClient`` described an
intention, and the artifact read as though the measurement-grade route had been taken. This module tests
the dispatch, and the grade rule that only the dispatch makes meaningful:

* the crops arm answers the win-dot row, from two magnified crops of one frame;
* the state client answers clock, banner, cue and transition, and its pip answer is a **diagnostic**;
* the band answers who sat where, and can no more move a boundary than create a winner.

Two failure shapes this is shaped to catch. The first is the merge letting a whole-frame reading replace a
crop measurement -- ``[1, 3]`` from a crop and ``[0, 0]`` from the same instant's full frame is a won game
recorded as a lost one, and the review reproduced exactly that on 2026-11-02 using the live route's own
function. The second is subtler: a test that feeds known crop counts through a *whole-frame* reply
validates the wrong framing, and would have gone green while the command measured nothing of the kind. So
the requests themselves are inspected here -- a crop request is only accepted as one if two labelled PNG
crops were actually in the body.

No test here reaches a model. Every transport is the real client class with ``_post`` replaced, so what is
exercised is the shipping path -- budget, retry, validation, persistence -- and nothing leaves the process.
"""
import base64
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_budget as bb                   # noqa: E402
from tekken_vod_helper import boundary_evidence as be                 # noqa: E402
from tekken_vod_helper import boundary_games as bg                    # noqa: E402
from tekken_vod_helper import boundary_header as bh                   # noqa: E402
from tekken_vod_helper import boundary_investigate as bi              # noqa: E402

import _frozen_corpus as frozen  # noqa: E402  (tests/_frozen_corpus.py)
from tekken_vod_helper import boundary_pips as bp                     # noqa: E402

TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DURATION = 6928.421136
PRESERVED = ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30"
TRIAL = ROOT / "build/work/bb9/set-end2end-v1"
BAND_RUNS = [str(PRESERVED), str(TRIAL / "attribution-run"), str(TRIAL / "band-run")]
SAVED_LEDGER = TRIAL / "attempts-shared-2026-10-31.json"
#: An interval with no crop answer and no band answer anywhere on disk, so a run there has to *make* the
#: evidence. Testing dispatch on a window that is already settled proves only that reuse works.
UNSEEN = (4800.0, 5600.0)
SETTLED = (3200.0, 4000.0)

pytestmark = pytest.mark.skipif(
    not (TIMELINE.is_file() and MANIFEST.is_file() and PRESERVED.is_dir()
         and all(Path(run, "groups").is_dir() for run in BAND_RUNS)),
    reason="the saved BB9 timeline, manifest and band answers are not present")


def band(runs=True):
    if not runs:
        return None
    return bh.saved_readings(bh.response_directories(",".join(BAND_RUNS) if runs is True else runs))


def plan(region=UNSEEN, header=True, crop_runs=frozen.crop_runs(), **kw):
    """``investigate-games --dry-run``, called the way the CLI calls it."""
    kw.setdefault("max_requests", 500)
    kw.setdefault("step", 2.0)
    kw.setdefault("batch_frames", 6)
    return bi.plan_saved_investigation(str(TIMELINE), str(MANIFEST), DURATION, region=region,
                                       header=band(header), crop_runs=crop_runs,
                                       games_per_set=None, **kw)


def priced(plan_result):
    return {entry["route"]: entry for entry in plan_result["dispatch"]["routes"]}


# ---------------------------------------------------------------- the grade, at the merge


def test_a_whole_frame_reading_cannot_overwrite_a_crop_reading():
    """The counterexample from the review, kept as a test so it stays impossible.

    ``merge_observations`` is what the live run calls, and the behaviour it documented was "the later
    reading wins" -- right for a clock, catastrophic for a score. Arrival order is not evidence; the class
    of the reading is, and the loser stays on the row as a diagnostic with the clash recorded.
    """
    merged = bi.merge_observations(
        [{"timestamp": 10.0, "state": "gameplay", "win_dots": [1, 3], "fact_source": "crop_readings"}],
        [{"timestamp": 10.0, "state": "gameplay", "win_dots": [0, 0], "fact_source": "vision_group",
          "round_clock": 30}])[0]
    assert merged["win_dots"] == [1, 3], "the whole-frame guess replaced the measurement"
    assert merged["win_dots_diagnostic"] == [0, 0], "the rejected reading was dropped, not graded"
    assert merged["pip_evidence_class"] == "validated_pip_evidence"
    clash = merged["pip_reading_conflicts"]
    assert clash and clash[0]["rejected"] == [0, 0] and "crop" in str(clash[0]["kept_source"])
    # The same merge still takes the state facts this route *is* authoritative about.
    assert merged["round_clock"] == 30


def test_a_whole_frame_reading_cannot_create_a_winner_on_an_uncropped_frame():
    """The control the test above cannot be: no crop on the row at all.

    An ordering rule only protects a value that exists. If a diagnostic could *fill* an empty pip row, one
    confident whole-frame guess on a frame nobody measured would name a game winner -- which is the outcome
    the crops arm exists to make impossible.
    """
    merged = bi.merge_observations(
        [{"timestamp": 20.0, "state": "gameplay", "round_clock": 12}],
        [{"timestamp": 20.0, "state": "gameplay", "win_dots": [3, 0],
          "fact_source": "vision_group"}])[0]
    assert merged.get("win_dots") in (None, [None, None])
    assert merged["win_dots_diagnostic"] == [3, 0]
    assert merged["pip_evidence_class"] == "unvalidated_pip_diagnostic"
    assert bg.build_observations([merged])[0]["win_dots"] == [None, None]


def test_a_band_pip_reading_cannot_create_a_winner_either():
    """The band answers a different question, and the grade says so in the same words.

    ``boundary_header`` quarantines its pip counts at the source; this checks the quarantine survives into
    the tracker's rows instead of being a property of one reader.
    """
    merged = bi.merge_observations([{"timestamp": 40.0, "state": "gameplay"}],
                                   [{"timestamp": 40.0, "win_dots": [3, 3],
                                     "fact_source": "header_band"}])[0]
    assert merged.get("win_dots") in (None, [None, None])
    assert merged["win_dots_diagnostic"] == [3, 3]
    assert bg.build_observations([merged])[0]["win_dots"] == [None, None]


def test_a_crop_reading_wins_whichever_side_of_the_merge_it_arrives_on():
    """Both directions, because "first writer wins" and "last writer wins" are the same bug twice."""
    forwards = bi.merge_observations(
        [{"timestamp": 30.0, "state": "gameplay", "win_dots": [0, 0], "fact_source": "vision_group"}],
        [{"timestamp": 30.0, "state": "gameplay", "win_dots": [1, 2],
          "fact_source": "crop_readings"}])[0]
    assert forwards["win_dots"] == [1, 2]
    assert forwards["win_dots_diagnostic"] == [0, 0]
    assert forwards["pip_evidence_class"] == "validated_pip_evidence"
    arriving = bi.merge_observations([{"timestamp": 50.0, "state": "gameplay"}],
                                     [{"timestamp": 50.0, "win_dots": [2, 1],
                                       "fact_source": "crop_readings"}])[0]
    assert arriving["win_dots"] == [2, 1]


def test_two_crop_readings_still_combine_slot_by_slot():
    """Grading the source must not break the rule that let two half-answers make one answer.

    The left row from one frame and the right row from the next are two measurements of the same score, and
    merging them is what made a partial read usable. A grade check that demoted either half would silently
    throw away evidence this recording paid for.
    """
    merged = bi.merge_observations(
        [{"timestamp": 5.0, "state": "gameplay", "win_dots": [3, None],
          "fact_source": "crop_readings"}],
        [{"timestamp": 5.0, "state": "gameplay", "win_dots": [None, 2],
          "fact_source": "crop_readings"}])[0]
    assert merged["win_dots"] == [3, 2]
    assert "field_conflicts" not in merged


def test_an_unlisted_source_cannot_promote_itself_to_evidence():
    """A new producer is diagnostic until it is *named* as a measurement.

    The default is the whole point: a route added next month that never declares itself would otherwise
    arrive with the credibility of the crops arm by doing nothing at all.
    """
    assert be.pip_grade("crop_readings") == "validated"
    assert be.pip_grade("vision_group") == "diagnostic"
    assert be.pip_grade("some_future_whole_frame_route") == "diagnostic"
    # A composite source is graded by its strongest part: prose beside a crop is still a crop reading.
    assert be.pip_grade("classified_evidence_text+crop_readings") == "validated"
    merged = bi.merge_observations([{"timestamp": 60.0, "state": "gameplay"}],
                                   [{"timestamp": 60.0, "win_dots": [1, 0],
                                     "fact_source": "undreamed_of_route"}])[0]
    assert merged.get("win_dots") in (None, [None, None])


def test_a_timeline_row_needs_a_declared_source_before_it_plans_no_crop():
    """Reuse is a claim about the reading, and a row that makes no claim is not reused.

    Hand-built controls and legacy timelines carry ``win_dots`` with no source at all. Treating that as
    measured would let the exact value this module demotes close a question, so the crop route asks again
    and the corpus grows one more answer.
    """
    frames = [{"id": i + 1, "timestamp": float(i), "path": "frames/frame-{:04d}.jpg".format(i + 1)}
              for i in range(20)]
    rows = [{"timestamp": float(i), "win_dots": [1, 0], "path": entry["path"]}
            for i, entry in enumerate(frames)]
    unclaimed = bi.plan_investigation([{"type": "win_dots_never_read", "at": 10.0}], frames, 20.0,
                                      observations=rows)
    measured = bi.plan_investigation([{"type": "win_dots_never_read", "at": 10.0}], frames, 20.0,
                                     observations=[dict(row, fact_source="crop_readings")
                                                   for row in rows])
    assert unclaimed["pip_requests_planned"] > 0
    assert measured["pip_requests_planned"] == 0


def test_the_grade_rule_leaves_the_saved_windows_exactly_where_they_were():
    """The rule may not move a conclusion the validated evidence already supported.

    ``[1200, 2200)`` has four crop-validated winners and ``[3200, 4000)`` has three. If demoting
    diagnostics had also demoted the crops, both counts would fall to zero and every conclusion in the plan
    document would be resting on a rule that rejects its own evidence.
    """
    assert plan(region=(1200.0, 2200.0))["recovery"]["games_with_a_winner"] == 4
    assert plan(region=SETTLED)["recovery"]["games_with_a_winner"] == 3


# ---------------------------------------------------------------- the dispatch, as a plan


def test_one_region_two_questions_and_two_executors():
    """A mixed question is split, priced per route, and neither route is asked the other's question.

    Before this, ``win_dots`` in a mixed region meant the whole frame got looked at again to read a row it
    cannot read, and the plan reported one request for it. Now the state client gets the frames that
    settle clocks and cues and the crops arm gets the instants that settle the score.
    """
    result = plan()
    routes = priced(result)
    assert result["dispatch"]["order"] == ["pip_crops", "state", "header"]
    assert result["dispatch"]["funded"] == ["pip_crops", "state", "header"]
    # 13 round-end windows, 39 frames. The repaired clock reads 25 at 4,954.950 and 43 at 4,979.975, so the
    # stretch 4,919.915-4,984.98 is three rounds' falling digits and not two: that split is the extra window.
    assert routes["pip_crops"]["requests_planned"] == 39
    assert routes["pip_crops"]["images_total"] == 78
    assert routes["pip_crops"]["client"] == "PipReadClient(arm=crops)"
    assert routes["state"]["requests_planned"] == 9
    assert routes["header"]["requests_planned"] == 8
    assert result["requests_planned"] == 56
    for region in result["regions"]:
        assert "win_dots" not in region["asked_fields"], "the state client is still asked for pips"
        assert region["route"] == "state"
    assert any(region["pip_question_elsewhere"] for region in result["regions"])
    assert "diagnostic" in routes["state"]["pip_answers_from_this_route"]


def test_a_set_layer_continuity_claim_reaches_the_state_executor():
    """The whole-frame half of the set layer's doubt is now bought, on the route that can see a frame.

    ``set_continuity`` used to advertise the band route for a question about controller handover and stage
    identity, which a 128x62 crop of the nameplate cannot answer -- and the planner read only the game-level
    queue kinds, so the claim bought nothing anywhere. Both halves were wrong together: the route was
    unreachable and the reachable route was unplanned. The state bill of this window grew from 5 batches to
    9 because the probes sit at the ends of the between-game stretches of the candidate whose membership
    is not supported, and they rank last, so an allowance spent on winners spends nothing here.
    """
    result = plan()
    kinds = {kind for region in result["regions"] for kind in region["kinds"]}
    assert "set_continuity" in kinds, "the set layer's claim planned no frames anywhere"
    continuity = [region for region in result["regions"] if "set_continuity" in region["kinds"]]
    assert continuity and all(region["priority"] <= bi.CONTINUITY_PRIORITY for region in continuity), \
        "a continuity probe was allowed to outrank a question that can settle a game"
    claim = next(entry for entry in result["claims"]["entries"] if entry["claim"] == "set_continuity")
    assert claim["route"] == "state" and claim["disposition"] == "planned", claim
    assert claim["requests"], "a planned claim has to name the sends that carry it"
    assert priced(result)["state"]["requests_planned"] == 9


def test_the_plan_prices_each_route_with_its_own_client_ceiling():
    """One token ceiling for three questions hides which part of the bill is large.

    The crop client asks for 3,000 tokens on two small images and the state client for 8,000 on six whole
    frames. Quoting one figure across all 56 requests overstates or understates whichever route a reader
    was about to fund, and this number is what an approval is written against.
    """
    dispatch = plan()["dispatch"]
    routes = priced(plan())
    assert routes["pip_crops"]["completion_tokens_per_request"] == 3000
    assert routes["state"]["completion_tokens_per_request"] == 8000
    assert routes["header"]["completion_tokens_per_request"] == 1500
    assert dispatch["completion_tokens_ceiling"] == 39 * 3000 + 9 * 8000 + 8 * 1500
    assert dispatch["images_total"] == 140


def _requests_of(plan_result):
    # type: (dict) -> list
    """Every scheduled request in a plan, however the dispatch nests it.

    A route summary counts; it does not say *which* frames. Assertions about a specific role have to read
    the requests themselves. The same request appears in both the route block and the region that scheduled
    it, so the walk keys on the frame -- counting twice here would be the sort of arithmetic that makes a
    bill look bigger than the send.
    """
    found = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("route") == "pip_crops" and node.get("frame") and node.get("role"):
                found[node["frame"]] = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plan_result)
    return list(found.values())


def test_the_crop_corpus_is_reused_before_the_crop_route_is_priced():
    """Reuse has to happen per route, or the saved answers shrink the report and not the bill.

    The settled interval still owes clock and cue decisions; its 45 crop frames are on disk, so not one of
    them is asked for again. What the interval *does* still buy is six interior frames: three games were
    credited from a single reading, and the two cached seconds beside each deciding frame -- the freeze the
    K.O. flash sits inside -- were never sampled. Reuse covers what was looked at; it cannot cover what was
    never looked at, and pretending otherwise is what made a winner's provenance invisible. Remove the corpus
    and the same 45 readings become 45 requests, which is what a reader needs to see to know what reuse was
    worth.
    """
    settled = plan(region=SETTLED)
    routes = priced(settled)
    assert routes["pip_crops"]["reused"] == 45, "every saved crop frame is reused, not re-priced"
    interior = [entry for entry in _requests_of(settled)
                if entry["role"].startswith("freeze_interior_")]
    assert routes["pip_crops"]["requests_planned"] == len(interior) == 6, routes["pip_crops"]
    assert {entry["role"] for entry in interior} == {"freeze_interior_minus_1s",
                                                     "freeze_interior_plus_1s"}
    assert len({round(float(entry["anchor"]), 3) for entry in interior}) == 3, \
        "two looks per claim, one claim each anchor"
    assert routes["state"]["requests_planned"] == 8
    assert settled["requests_planned"] == 8 + 6
    bare = priced(plan(region=SETTLED, crop_runs=None))
    assert bare["pip_crops"]["requests_planned"] == 45, bare["pip_crops"]
    assert bare["pip_crops"]["reused"] == 0
    # Still exactly 45, and no interior asks: the second pass is a *second* pass. With nothing on disk the
    # coarse triple is not exhausted, so no window has earned a look between its samples -- which is also
    # why the six corroboration asks above cannot be the change inflating every window it touches.


def test_a_band_gap_is_routed_to_the_band_and_never_into_frame_requests():
    """Frames cannot answer who sat on which side, so an identity gap becomes a band request.

    Four unwatched games become eight sampled instants: one band frame cannot separate "the pairing
    changed" from "the band was caught mid-change here", and that ambiguity is the question.
    """
    result = plan()
    assert all(entry["route"] == "header" for entry in result["recovery"]["missing_observations"])
    assert len(result["header_requests"]) == 8
    for request in result["header_requests"]:
        assert request["client"] == "HeaderReadClient"
        assert request["identity"] and request["images"] == 1
        assert "band reading falls inside this game" in request["because"]
        assert request["would_resolve"]
    never = priced(result)["header"]["never_answers"]
    assert "boundary" in never and "pip count" in never, never


def test_a_fundied_route_is_reported_unfunded_rather_than_quietly_absent():
    """``pip_crops: 0`` from a plan that was never allowed to ask is a different fact from free work.

    Restricting routes is normal -- an approval often funds one executor at a time -- and the artifact has
    to say which kind of zero the reader is looking at, or a band-repair run reads as "identity is settled".
    """
    state_only = plan(routes=("state",))
    routes = priced(state_only)
    assert state_only["dispatch"]["funded"] == ["state"]
    assert state_only["dispatch"]["unfunded_routes"] == ["pip_crops", "header"]
    assert routes["pip_crops"]["requests_planned"] == 0
    assert routes["pip_crops"]["enabled"] is False
    assert "did not fund the crop route" in routes["pip_crops"]["unfunded_because"]
    # Every route this invocation cannot fund has to carry both a price and a reason. A bare zero is the
    # shape that makes an unfunded executor indistinguishable from a settled window, and a plan whose
    # numbers cannot be audited is not a plan anyone can approve against.
    for entry in state_only["dispatch"]["routes"]:
        if entry["enabled"]:
            continue
        assert entry["requests_unfunded"] > 0, entry["route"]
        assert entry["unfunded_because"], "%s unfunded with no reason recorded" % entry["route"]
        assert len(entry["deferred"]) >= entry["requests_unfunded"], entry["route"]
    # Pricing does not depend on the answer arriving: the same window owes the same crop bill whether or
    # not this invocation funded the route that would read it.
    assert routes["pip_crops"]["requests_unfunded"] == plan()["dispatch"]["routes"][0][
        "requests_planned"]
    # The questions have not vanished: the recovery still lists the gaps this run will not close.
    assert state_only["recovery"]["games_with_a_winner"] == 0
    assert len(state_only["recovery"]["missing_observations"]) > 0
    with pytest.raises(ValueError, match="unknown routes"):
        plan(routes=("pip",))


def test_a_smaller_allowance_defers_by_route_and_names_what_it_left():
    """Exhaustion is a planning outcome, so it has to leave a list and not a count.

    The order is the plan's: crops, then state, then band. A cap that lands mid-crop leaves the band
    questions untouched, and "not funded" has to be distinguishable from "not needed" in the artifact.
    """
    routes = priced(plan(max_requests=10))
    assert routes["pip_crops"]["requests_planned"] == 10
    assert routes["state"]["requests_planned"] == 0
    assert routes["header"]["requests_planned"] == 0
    assert len(routes["pip_crops"]["deferred"]) == 29
    assert routes["pip_crops"]["deferred"][0]["frame"]


def test_a_request_identity_names_the_frame_not_its_row_number():
    """Content identity, because a counter or a directory is how an answered request looks new.

    ``--step``, ``--max-regions`` and the output directory all move a request's position without moving
    what it asks about. The identity is built from the frame, the arm's frozen geometry, the prompt and
    the model, so reuse survives every one of those changes and a re-crop at another scale is genuinely a
    different request.
    """
    wide = plan(max_requests=500)
    tight = plan(max_requests=500, step=2.0, evidence_root=str(ROOT / "build/work"))
    by_frame = {entry["frame"]: entry["identity"] for entry in wide["pip_requests"]}
    assert by_frame == {entry["frame"]: entry["identity"] for entry in tight["pip_requests"]}
    first = by_frame[list(by_frame)[0]]
    moved = bi.pip_request_identity({"frame": list(by_frame)[0], "timestamp": 9999.0}, model="other")
    assert moved != first, "the frame's own identity survived a different model"


# ---------------------------------------------------------------- sending, and what left

class Routing:
    """Three real clients, one fake socket, and a record of every body asked to leave.

    An instance rather than a function because a plain callable assigned to a class attribute binds
    ``self``, and this seam is ``_post`` and nothing else.
    """

    def __init__(self, pip=None, state=None, header=None, interrupt_after=None):
        self.bodies = {"pip_crops": [], "state": [], "header": []}
        self.pip, self.state, self.header = pip, state, header
        self.interrupt_after = interrupt_after
        self.error = None

    def __call__(self, body):
        text = json.dumps(body)
        route = self.route_of(text)
        if self.interrupt_after is not None and self.total() >= self.interrupt_after:
            raise KeyboardInterrupt("operator stopped the run")
        self.bodies[route].append(body)
        return {"choices": [{"message": {"content": json.dumps(self.answer(route, body, text))}}]}

    def total(self):
        return sum(len(sent) for sent in self.bodies.values())

    @staticmethod
    def route_of(text):
        if "round-win pip row" in text:
            return "pip_crops"
        if "top band of a Tekken 8 broadcast frame" in text:
            return "header"
        if "in-fight HUD" in text:
            return "state"
        raise AssertionError("a request arrived that belongs to no route: {}".format(text[:300]))

    def answer(self, route, body, text):
        # An override is a plain function of (routing, route, body, text), so a test can watch the
        # transport's own counters while answering; the default is this class's bound responder.
        defaults = {"pip_crops": self.pip_default, "state": self.state_default,
                    "header": self.header_default}
        override = {"pip_crops": self.pip, "state": self.state, "header": self.header}[route]
        return override(self, route, body, text) if override else defaults[route](route, body, text)

    @staticmethod
    def items(text):
        return sorted({int(match) for match in re.findall(r"item (\d+)", text)})

    def pip_default(self, route, body, text):
        """The left row fills over the run, so a game can conclude: the ramp is the point, not a trick.

        Answering three lit pips on every frame would end several games in one round and make the winner
        count meaningless. One pip per twelfth request is a plausible series.
        """
        lit = min(3, 1 + len(self.bodies["pip_crops"]) // 12)
        return [{"item": item, "label": label, "drawn": True,
                 "lit": lit if label == "LEFT" else 0,
                 "positions": list(range(1, lit + 1)) if label == "LEFT" else [],
                 "confidence": 0.9}
                for item in self.items(text) for label in ("LEFT", "RIGHT")]

    def header_default(self, route, body, text):
        return [{"item": item, "side": side, "player": "Buk" if side == "LEFT" else "Rebix",
                 "games": 1, "games_drawn": True, "pips": 1, "pips_drawn": True,
                 "label": "LOSERS ROUND 3", "confidence": 0.9}
                for item in self.items(text) for side in ("LEFT", "RIGHT")]

    def state_default(self, route, body, text):
        """Answers the clock and cue fields, and names 3-0 in the dot row it was also asked about.

        The 3-0 is deliberate: it is the diagnostic that must never become a score, and every test about
        winners has to be able to tell the two apart.
        """
        # Only the fields the base does not already hold are claimed. A mock that answers
        # ``round_clock: 30`` on every frame rewrites the clock series the rounds are built from, and the
        # run's own re-plan then moves boundaries for a reason that belongs to the test double, not the
        # footage -- which is precisely the class of thing this suite must not mistake for a finding.
        return [{"id": int(match), "state": "gameplay", "confidence": 0.9, "round_clock": None,
                 "round_call": None, "round_end": None, "result_transition": False,
                 "win_dots_p1": 3, "win_dots_p2": 0, "characters": ["A", "B"],
                 "evidence": "mocked"}
                for match in re.findall(r"id=(\d+) t=", json.dumps(body))]

    def silence(self, route, body, text):
        """A reply that claims nothing, for the controls that must not move the conclusion."""
        if route == "pip_crops":
            return [{"item": item, "label": label, "drawn": None, "lit": None, "positions": None,
                     "confidence": 0.9} for item in self.items(text) for label in ("LEFT", "RIGHT")]
        if route == "state":
            return [{"id": int(match), "state": "unknown", "confidence": 0.1, "round_clock": None,
                     "round_call": None, "round_end": None, "result_transition": None,
                     "win_dots_p1": None, "win_dots_p2": None, "characters": [], "evidence": "nothing"}
                    for match in re.findall(r"id=(\d+) t=", json.dumps(body))]
        return self.header_default(route, body, text)


def images_in(body):
    # type: (Any) -> list
    """Every image actually in one request body, decoded, with the label that introduced it.

    Read out of the transport argument rather than from a client attribute, because the thing being proved
    is what would have left the machine.
    """
    parts = body.get("messages", [{}])[0].get("content", [])
    found, label = [], None
    for part in parts:
        if part.get("type") == "text":
            match = re.search(r"item (\d+) label=(\w+)|item (\d+) image (\S+)", part.get("text", ""))
            if match:
                label = (int(match.group(1) or match.group(3)),
                         match.group(2) or Path(match.group(4)).name)
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            mime, blob = url[len("data:"):].split(";base64,", 1)
            found.append({"label": label, "mime": mime, "bytes": base64.b64decode(blob)})
    return found


def run_unseen(tmp_path, monkeypatch, routing=None, max_requests=52, passes=1, resume=False,
               budget_cap=400, crop_runs=frozen.crop_runs(), header=True, expand=0.0,
               region=UNSEEN, **kw):
    """``investigate-games`` on the unseen interval, with every route's socket replaced.

    ``expand=0.0`` pins the window on purpose: this suite is about which route is charged for what, and the
    run's automatic widening changes the footage it works. The widening is asserted on its own in
    ``test_a_live_run_widens_its_own_narrow_request_by_default``.
    """
    routing = routing or Routing()
    for cls in (bi.RoundFactsClient, bp.PipReadClient, bh.HeaderReadClient):
        monkeypatch.setattr(cls, "_post", routing, raising=True)
    result = bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                        output_dir=tmp_path / "inv", region=region, step=2.0,
                                        batch_frames=6, max_requests=max_requests,
                                        games_per_set=None, header=band(header),
                                        crop_runs=crop_runs, passes=passes, resume=resume,
                                        expand=expand,
                                        budget_file=str(tmp_path / "attempts.json"),
                                        budget_cap=budget_cap, budget_minutes=30, **kw)
    return result, routing


def counts(routing):
    return {route: len(sent) for route, sent in routing.bodies.items()}


def test_a_live_run_widens_its_own_narrow_request_by_default(tmp_path, monkeypatch):
    """The default is on: a run asked about 4800-5600 works a wider window when a candidate says why.

    Checked against the real recording with every route's socket replaced, because the thing under test is
    that the executor -- not the planner -- moves, and that the move is recorded rather than silent.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, expand=120.0)
    assert result["region_requested"] == [4800.0, 5600.0], result["region_requested"]
    executed = result["region_executed"]
    assert executed[0] < 4800.0 or executed[1] > 5600.0, \
        "the run reported widening but worked the window it was handed: %r" % (executed,)
    block = result["expansion"]
    assert block["triggered"] is True and block["windows"]["original"] == [4800.0, 5600.0]
    assert block["windows"]["expanded"] == executed, block["windows"]
    assert block["observations"]["expanded"] > block["observations"]["original"], block["observations"]
    # Both windows, and the reason, are on disk: a widened run has to be tellable from a wide one.
    persisted = json.loads((tmp_path / "inv/expansion.json").read_text(encoding="utf-8"))
    assert persisted["original_window"] == [4800.0, 5600.0]
    assert persisted["expanded_window"] == list(executed), persisted
    planned = json.loads((tmp_path / "inv/plan.json").read_text(encoding="utf-8"))
    assert planned["window"] == list(executed) and planned["expansion"]["triggered"] is True
    # And the ledger is the single shared one the run was given, not a second allowance for the widening.
    ledger = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert ledger["attempts_charged"] == result["requests_sent"] <= ledger["cap"]


def test_a_pinned_run_works_exactly_the_window_it_was_handed(tmp_path, monkeypatch):
    """The counterpart: with widening off, nothing moves, so the default is what earns the difference."""
    result, _ = run_unseen(tmp_path, monkeypatch, expand=0.0)
    assert result["region_executed"] == [4800.0, 5600.0], result["region_executed"]
    assert result["expansion"]["triggered"] is False
    assert result["expansion"]["stopping_reasons"] == ["expansion_disabled"]
    assert not (tmp_path / "inv/expansion.json").exists()


def store(route, out):
    return bi.route_answer_store(route, out)


def _tokens(text):
    return set(re.findall(r"frame-\d+", text))


#: The answer-file pattern per route. A crop reading of a frame answers the *crop* question about it;
#: the same frame may legitimately be asked about by the state route and the band route. So "nothing
#: answered goes out twice" is a per-route comparison -- across routes it would refuse real work.
def _answered_tokens(out, route):
    pattern = {"pip_crops": "crops-*.json", "state": "group-*.json",
               "header": "header-*.json"}[route]
    answered = set()
    for path in store(route, out).glob(pattern):
        answered |= _tokens(path.read_text(encoding="utf-8"))
    return answered


def _sent_tokens(bodies):
    sent = set()
    for body in bodies:
        sent |= _tokens(json.dumps(body))
    return sent


def _answered_snapshot(out):
    """What each store answers *before* the invocation under test runs.

    Taken before, because a store read afterwards also holds that invocation's own sends, and comparing
    a run against its own answers is a tautology wearing a guard's name.
    """
    return {route: _answered_tokens(out, route) for route in ("pip_crops", "state", "header")}


def _assert_no_answered_resends(snapshot, routing):
    for route, bodies in routing.bodies.items():
        overlap = _sent_tokens(bodies) & snapshot.get(route, set())
        assert not overlap, "the {} route re-sent frames it already has answers for: {}".format(
            route, sorted(overlap))


def test_the_crop_route_sends_two_crops_and_nothing_else(tmp_path, monkeypatch):
    """The framing is proven from the bytes, because a plan's word for it is not evidence.

    Each crop request must carry two PNG images: the labelled LEFT and RIGHT boxes of one cache frame, at
    the arm's frozen scale -- not the whole frame, and not a JPEG re-encode of it. And the answer file has
    to record the same digests the request carried, or the answer cannot be tied to the picture.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=2)
    assert counts(routing) == {"pip_crops": 2, "state": 0, "header": 0}
    for body in routing.bodies["pip_crops"]:
        images = images_in(body)
        assert len(images) == 2, "a crop request carried {} images".format(len(images))
        assert {image["mime"] for image in images} == {"image/png"}
        assert sorted(image["label"][1] for image in images) == ["LEFT", "RIGHT"]
        for image in images:
            assert image["bytes"][:8] == b"\x89PNG\r\n\x1a\n"
        assert not any(image["mime"] == "image/jpeg" for image in images), \
            "the whole frame went along with the crops"


def test_the_state_route_sends_whole_frames_and_its_pips_stay_diagnostics(tmp_path, monkeypatch):
    """What the state client may settle, and what its answer is allowed to become.

    The mock names 3-0 on every frame it reads -- the value that would have ended games under the old
    merge. None of it may reach a score: the numbers survive as ``win_dots_diagnostic`` and the tracker
    goes on seeing an unread row.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=5,
                                 routing=Routing(state=Routing.state_default),
                                 routes=("state",))
    assert counts(routing) == {"pip_crops": 0, "state": 5, "header": 0}
    images = images_in(routing.bodies["state"][0])
    assert len(images) == 6 and {image["mime"] for image in images} == {"image/jpeg"}
    files = sorted(store("state", tmp_path / "inv").glob("group-*.json"))
    assert files
    row = json.loads(files[0].read_text(encoding="utf-8"))["observations"][0]
    assert row["result_transition"] is False and row["win_dots_diagnostic"] == [3, 0]
    assert "win_dots" not in row, "the state route wrote a pip measurement"
    assert row["pip_evidence_class"] == "unvalidated_pip_diagnostic"
    assert result["recovery_after"]["games_with_a_winner"] == 0, \
        "whole-frame guesses named a winner on a window with no crop evidence"


def test_the_band_route_sends_one_band_and_cannot_move_a_boundary(tmp_path, monkeypatch):
    """The band request carries a rendered band and nothing else, and its answer names people, not cuts.

    Asking for identity is the one thing this route may do, so the test checks both halves: the bytes that
    left were a band, and what came back left every boundary and clip cut where the footage put it.
    """
    routing = Routing()
    before = plan()
    result, _ = run_unseen(tmp_path, monkeypatch, routing=routing, max_requests=52, passes=1)
    assert routing.bodies["header"], "the identity gaps were priced and never dispatched"
    images = images_in(routing.bodies["header"][0])
    assert len(images) == 1 and images[0]["mime"] == "image/png"
    width, height = _size(images[0]["bytes"])
    assert width == 640 * 3 and height < 360, "the band crop is not a band: {}x{}".format(
        width, height)
    answers = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted(store("header", tmp_path / "inv").glob("header-*.json"))]
    assert answers and all(answer["pip_evidence"]["validated"] is False for answer in answers)
    assert ([c["start"] for c in result["recovery_before"]["candidates"]]
            == [c["start"] for c in before["recovery"]["candidates"]])
    # The rule under test is about the band, so it is asserted about the band: with these readings
    # credited, the grouping measured *with* them equals the grouping measured without them. A candidate
    # boundary moving because a pip row was read is the recording talking; a boundary moving because a
    # nameplate was read is the defect, and it is the second comparison that would notice.
    assert result["recovery_after"]["grouping_changed_by_band"] is False
    assert result["recovery_after"]["accounting_with_band"] == \
        result["recovery_after"]["accounting_without_band"]


def _size(blob):
    # type: (bytes) -> tuple
    from PIL import Image
    image = Image.open(io.BytesIO(blob))
    return image.size


def test_the_run_writes_the_executors_own_answer_layout(tmp_path, monkeypatch):
    """One format per route, because a second format needs a second reader.

    The files a run produces have to be readable by the loader that reads a week-old probe: the arm, the
    framing, the digests and the identity fields are what that loader insists on, and an answer the corpus
    cannot read is an answer that gets asked twice.
    """
    run_unseen(tmp_path, monkeypatch, max_requests=3)
    stored = [json.loads(path.read_text(encoding="utf-8"))
              for path in sorted(store("pip_crops", tmp_path / "inv").glob("crops-*.json"))]
    assert stored
    for answer in stored:
        assert answer["arm"] == "crops" and bp.is_cache_framing(answer)
        assert answer["identity"]["scale"] == bp.CROP_SCALE
        assert answer["source_sha256"] and answer["crop_images"]
        assert {image["label"] for image in answer["crop_images"]} == {"LEFT", "RIGHT"}
    readings = be.crop_observations([tmp_path / "inv" / "pip"])
    assert len(readings) == len(stored)
    assert all(entry["source"] == "crop_readings" for entry in readings.values())


def test_crop_answers_reach_the_tracker_through_the_same_merge_as_saved_ones(tmp_path, monkeypatch):
    """The end of the loop: an answer this run paid for has to be evidence, not a log entry.

    The window starts with no winner anywhere. After the crop route answers, the same recovery block
    credits wins and names the participants -- which only happens if the run's own answers were read
    through ``boundary_evidence``, the loader the saved corpus goes through.
    """
    assert plan()["recovery"]["games_with_a_winner"] == 0
    before_merged = plan()["recovery"]["pip_evidence"]["crop_readings_merged"]
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=52)
    assert counts(routing)["pip_crops"] == 39
    assert result["recovery_before"]["games_with_a_winner"] == 0
    assert result["recovery_after"]["games_with_a_winner"] > 0, "the run's crops never reached the tracker"
    after = result["recovery_after"]
    assert after["pip_evidence"]["crop_readings_merged"] > before_merged
    assert any(candidate["game_wins_by_identity"] or candidate["attribution_withheld"]
               for candidate in after["candidates"])


def test_a_run_s_answers_are_reused_by_the_next_invocation(tmp_path, monkeypatch):
    """Reuse across processes, not just across passes.

    The second invocation is a fresh command line pointed at the same directory, and the answers it finds
    were written by a run, not by an approved experiment. If reuse only worked inside one process, a
    resumed run would pay twice for the same pixels.

    A resumed crop plan folds this directory's own answers into its evidence before pricing -- the same
    thing the next pass of a live run does -- so the 12 answered frames are not priced and cannot be
    re-sent, and the reuse is visible in the plan instead of in the send loop's resume counter. That fold
    is also what lets the plan *advance*: a window whose coarse triple is complete now gets its bounded
    interior question priced, and the frames the first run answered retire one instant at a time rather
    than silently closing the whole search.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=12, passes=1)
    assert counts(routing)["pip_crops"] == 12
    snapshot = _answered_snapshot(tmp_path / "inv")
    assert len(snapshot["pip_crops"]) == 12, "the first invocation left no answers to reuse"
    assert not snapshot["state"] and not snapshot["header"]
    again, second = run_unseen(tmp_path, monkeypatch, max_requests=52, passes=1, resume=True)
    assert counts(second)["pip_crops"] == 27, "a resume re-asked frames the first run already answered"
    _assert_no_answered_resends(snapshot, second)
    resumed_plan = json.loads((tmp_path / "inv" / "plan.json").read_text(encoding="utf-8"))
    pip_route = [entry for entry in resumed_plan["dispatch"]["routes"]
                 if entry["route"] == "pip_crops"][0]
    assert pip_route["reused"] >= 12, "the folded answers were not even reported as reused"


def test_a_resume_advances_past_interiors_its_own_answers_left_unrowed(tmp_path, monkeypatch):
    """Bounded continuation through the shipped executor, on the settled interval, across processes.

    Three games in [3200, 4000) are credited on one reading each, and the coarse triple is answered by
    the saved corpus -- so the only crop work this interval can buy is the corroboration looks, and the
    only transport is a stand-in that answers "no row" every time. That is the review's fixture at real
    scale: a no-row answer retires its own instant and nothing else, so run one buys the two interiors
    nearest each deciding reading, the resume buys the bounded frames *behind* them, and only when the
    bound is honestly empty does a stopping reason appear. Across all three invocations the winner count
    never moves -- continuation buys context, not verdicts.
    """
    settled = (3200.0, 4000.0)

    def no_row(routing, route, body, text):
        return [{"item": item, "label": label, "drawn": False, "lit": None, "positions": None,
                 "confidence": 0.9} for item in routing.items(text) for label in ("LEFT", "RIGHT")]

    first = Routing(pip=no_row)
    run1, _ = run_unseen(tmp_path, monkeypatch, routing=first, passes=1, region=settled,
                         routes=("pip_crops",))
    assert counts(first)["pip_crops"] == 6, "the two looks nearest each claim, no more"
    asked_first = _sent_tokens(first.bodies["pip_crops"])
    assert run1["recovery_after"]["games_with_a_winner"] == 3

    snapshot = _answered_snapshot(tmp_path / "inv")
    assert len(snapshot["pip_crops"]) == 6, "every interior the first run bought is on disk"
    second = Routing(pip=no_row)
    run2, _ = run_unseen(tmp_path, monkeypatch, routing=second, passes=1, resume=True,
                         region=settled, routes=("pip_crops",))
    advanced = _sent_tokens(second.bodies["pip_crops"])
    assert counts(second)["pip_crops"] == 6, \
        "the bounded frames behind the answered interiors were never offered"
    assert not (advanced & snapshot["pip_crops"]), "the resume re-asked an interior it already has"
    assert not (advanced & asked_first)
    assert run2["recovery_after"]["games_with_a_winner"] == 3, "advancement must not move a winner"

    third = Routing(pip=no_row)
    run3, _ = run_unseen(tmp_path, monkeypatch, routing=third, passes=1, resume=True,
                         region=settled, routes=("pip_crops",))
    assert counts(third)["pip_crops"] == 0, "the bound holds nothing new and something still went out"
    plan = json.loads((tmp_path / "inv" / "plan.json").read_text(encoding="utf-8"))
    pip = [entry for entry in plan["dispatch"]["routes"] if entry["route"] == "pip_crops"][0]
    assert len(pip["claims_stopped"]) == 3, pip["claims_stopped"]
    assert all(entry["stopping"] == "evidence_exhausted" and entry["exhausted"] is True
               for entry in pip["claims_stopped"]), pip["claims_stopped"]
    assert pip["claims_continuing"] == []
    assert run3["recovery_after"]["games_with_a_winner"] == 3, \
        "exhausted corroboration must not uncredit a single-reading winner"
    assert run3["stopped_reason"] == "supported_outcome", run3["stopped_reason"]
    # Two runs bought twelve interiors and nothing answered went out twice; the ledger carries exactly
    # the twelve, because a resume that advances still charges only its own sends.
    charged = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert charged["attempts_charged"] == 12 == run1["requests_sent"] + run2["requests_sent"]


def test_a_partial_allowance_defers_the_unfunded_interiors_by_name_and_the_resume_picks_them_up(
        tmp_path, monkeypatch):
    """The corroboration ask shares the invocation's allowance, and an unfunded look is written down.

    Four attempts against six priced interiors: two windows get their pair, one keeps nothing, and the
    plan lists the unfunded frames by path, timestamp, role and window -- deferral, not disappearance,
    and explicitly not exhaustion. The resume then finds that plan whole: it re-prices the deferred pair
    *and* advances the windows the first run's answers retired, sending neither a duplicate nor a lie.
    """
    settled = (3200.0, 4000.0)

    def no_row(routing, route, body, text):
        return [{"item": item, "label": label, "drawn": False, "lit": None, "positions": None,
                 "confidence": 0.9} for item in routing.items(text) for label in ("LEFT", "RIGHT")]

    first = Routing(pip=no_row)
    run1, _ = run_unseen(tmp_path, monkeypatch, routing=first, passes=1, max_requests=4,
                         region=settled, routes=("pip_crops",))
    assert counts(first)["pip_crops"] == 4
    pip1 = [entry for entry in run1["dispatch_before"]["routes"]
            if entry["route"] == "pip_crops"][0]
    left = {(Path(entry["frame"]).name, round(float(entry["timestamp"]), 3))
            for entry in pip1["deferred"]}
    assert left == {("frame-3930.jpg", 3932.929), ("frame-3932.jpg", 3934.931)}, pip1["deferred"]
    assert all(entry["role"].startswith("freeze_interior_")
               and abs(float(entry["anchor"]) - 3933.929793) < 1e-6
               for entry in pip1["deferred"]), pip1["deferred"]
    assert len({entry["window"] for entry in pip1["deferred"]}) == 1, "one window was not funded"
    assert pip1["claims_stopped"] == [], "an unfunded look is not an exhausted bound"
    snapshot = _answered_snapshot(tmp_path / "inv")
    assert len(snapshot["pip_crops"]) == 4

    second = Routing(pip=no_row)
    run2, _ = run_unseen(tmp_path, monkeypatch, routing=second, passes=1, resume=True,
                         max_requests=10, region=settled, routes=("pip_crops",))
    assert counts(second)["pip_crops"] == 6, \
        "the resume owes the deferred pair plus the bounded frames behind the answered neighbours"
    sent2 = _sent_tokens(second.bodies["pip_crops"])
    assert not (sent2 & snapshot["pip_crops"]), "the resume re-asked a frame the first run answered"
    names2 = {Path(entry["frame"]).name for entry in json.loads(
        (tmp_path / "inv" / "plan.json").read_text(encoding="utf-8"))["pip_requests"]}
    assert {"frame-3930.jpg", "frame-3932.jpg"} <= names2, names2
    assert run2["recovery_after"]["games_with_a_winner"] == 3
    # With everything the bound holds funded, no window has any report to make -- and none sneaks one in
    # for the frames run 1 deferred: unfunded work was re-priced, not reclassified as answered evidence.
    pip2 = [entry for entry in json.loads((tmp_path / "inv" / "plan.json").read_text(
        encoding="utf-8"))["dispatch"]["routes"] if entry["route"] == "pip_crops"][0]
    assert pip2["deferred"] == [] and pip2["claims_stopped"] == []


# ---------------------------------------------------------------- the loop, and its endings


def test_a_second_pass_asks_the_route_the_first_pass_made_relevant(tmp_path, monkeypatch):
    """New evidence creates a new question, and the new question belongs to a different executor.

    Pass one reads pip rows, which ends games. A game with a winner in a window the band has never seen is
    an identity question that did not exist an hour ago, so pass two sends bands. This is the whole
    argument for dispatching instead of planning: a queue re-run against one client cannot follow a
    question from one route to the next.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=80, passes=2)
    first, second = result["passes"]
    assert {entry["route"]: entry["requests_sent"] for entry in first["routes"]} == {
        "pip_crops": 39, "state": 9, "header": 8}
    winners = sum(int(entry.get("groups_answered") or 0) for entry in first["routes"]
                  if entry["route"] == "pip_crops")
    assert winners == 39
    sent_two = {entry["route"]: entry["requests_sent"] for entry in second["routes"]}
    assert sent_two["header"] > 0, "pass two saw new winners and asked the band nothing"
    assert sent_two.get("pip_crops", 0) < 39, "pass two re-planned crops pass one had answered"
    assert routing.bodies["pip_crops"] and routing.bodies["header"]


def test_a_second_pass_asks_nothing_twice_and_says_why_it_stopped(tmp_path, monkeypatch):
    """Reuse across passes, and a stop reason that is not "we ran out of patience".

    Once every route's questions are answered on disk the next pass plans nothing and the run ends
    ``supported_outcome``. A run that instead re-planned the same frames into a fresh subdirectory would
    report the same word while spending a second allowance -- which is the failure the gate named: never
    re-ask a saved request by changing a directory or a counter.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=200, passes=3)
    # The loop stops because the queue stopped shrinking, not because it was told to: pass one closes 14
    # of 29 open questions, pass two answers three more requests and closes none.
    assert result["stopped_reason"] == "no_new_evidence", result["stopped_reason"]
    assert result["passes"][0]["questions_open"] > result["passes"][0]["questions_after"]
    assert "no question closed" in str(result["stop_detail"])
    assert result["requests_sent"] == routing.total() > 52
    ledger = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert ledger["attempts_charged"] == result["requests_sent"]
    assert len({entry["request"] for entry in ledger["entries"]}) == ledger["attempts_charged"], \
        "two routes shared one attempt name"
    # A resume of the same command re-prices from the answers the passes above left behind -- and the
    # questions those answers *opened* are real work, so "owes nothing at all" is no longer the property
    # this checks. The property that never relaxes: not one request goes out for a frame or an instant
    # that already has an answer on disk, whichever pass, directory or invocation put it there.
    answered_snapshot = _answered_snapshot(tmp_path / "inv")
    again, second = run_unseen(tmp_path, monkeypatch, max_requests=200, passes=1, resume=True)
    _assert_no_answered_resends(answered_snapshot, second)
    # And the discovery converges: once the resume's own answers are on disk too, the next invocation
    # plans nothing and says why.
    converged, third = run_unseen(tmp_path, monkeypatch, max_requests=200, passes=1, resume=True)
    assert third.total() == 0, "a third invocation re-asked what the resume had just answered"
    assert converged["stopped_reason"] == "supported_outcome", converged["stopped_reason"]


def test_a_crop_request_and_a_band_request_never_share_an_attempt_name(tmp_path, monkeypatch):
    """The same frame asked twice is two attempts, and reconciliation has to know which one answered.

    ``boundary_budget.request_tag`` names an attempt by the frame tokens in the body, and one frame can be
    cropped for its pip row and band-cropped for its nameplate. Keyed on the bare token, a resume would
    close the band's hanging attempt with the crop's answer file and call an unasked question settled.
    """
    run_unseen(tmp_path, monkeypatch, max_requests=52, passes=1)
    entries = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))["entries"]
    tags = [entry["request"] for entry in entries]
    assert len(tags) == len(set(tags)), "two routes charged the same attempt name"
    shared = [tag.split(":", 1)[1] for tag in tags if tag.startswith("pip_crops:")]
    assert any(tag.split(":", 1)[1] in shared for tag in tags if tag.startswith("header:")), \
        "no frame was asked by two routes, so this test would pass by accident"
    purposes = {entry["purpose"] for entry in entries}
    assert purposes == {"investigate-games:pip_crops", "investigate-games:state",
                       "investigate-games:header"}


def test_one_ledger_governs_all_three_routes(tmp_path, monkeypatch):
    """The cap is checked against what every route still owes, before the first byte leaves.

    A plan that fits the allowance on one route cannot quietly overrun the approval on another, and a
    refusal at the gate charges nothing: an attempt that was never owed must not become spend that some
    later command has to account for.
    """
    routing = Routing()
    with pytest.raises(ValueError, match="still owed"):
        run_unseen(tmp_path, monkeypatch, routing=routing, max_requests=52, budget_cap=10)
    assert routing.total() == 0
    ledger = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert ledger["attempts_charged"] == 0
    assert not store("pip_crops", tmp_path / "inv").exists() or not \
        list(store("pip_crops", tmp_path / "inv").glob("*.json"))


def test_an_exhausted_ledger_stops_mid_run_and_writes_no_answer_for_a_refused_attempt(
        tmp_path, monkeypatch):
    """A cap reached between routes is a stop, not a crash, and a deferred request is stored nowhere.

    The retry inside one request is what exhausts a cap the plan alone would have fitted under, which is
    why the cap is charged per attempt: an unusable reply is a real cost, not a free second opinion.
    """
    def unusable_once(routing, route, body, text):
        if not getattr(routing, "burned", False):
            # The transport has already recorded this call by the time the hook runs, so the flag says
            # "first reply", not "no requests yet" -- and a cap test that never actually delivered an
            # unusable reply would pass by spending nothing.
            routing.burned = True
            # An unusable reply -- parseable JSON, about none of the frames asked -- is charged and then
            # retried by the client, so one group spends two attempts. That is what makes a cap sized to
            # the request count reachable from the inside, which is the only place a cap matters.
            return {"choices": [{"message": {"content": "[]"}}]}
        return getattr(routing, {"pip_crops": "pip_default", "state": "state_default",
                                 "header": "header_default"}[route])(route, body, text)

    routing = Routing(pip=unusable_once, state=unusable_once, header=unusable_once)
    # 56 requests are owed and 56 attempts are allowed: the gate lets the run start, and the single
    # unusable reply is what spends the last attempt -- one group charged twice means the run is one
    # request short of finishing, and that is where the cap has to bite. A cap one below the owed count is
    # refused before the run starts, which is the other test.
    result, _ = run_unseen(tmp_path, monkeypatch, routing=routing, max_requests=60, budget_cap=56,
                           passes=1)
    assert result["stopped_reason"] == "attempt_cap_reached", result["stopped_reason"]
    # 56 attempts charged for 55 groups answered: the unusable reply was charged and retried, which is
    # what spent the cap one request early. Answered groups, charged attempts and refusals have to agree
    # about that arithmetic, because a cap that counted groups instead would have allowed a 57th attempt
    # and still read the run as finished.
    assert routing.total() == 56, counts(routing)
    charged = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert charged["attempts_charged"] == 56 == charged["cap"]
    assert charged["attempts_remaining"] == 0
    assert result["groups_completed"] == 55 == result["groups_answered"]
    assert len(list(store("pip_crops", tmp_path / "inv").glob("crops-*.json"))) == 39
    assert len(list(store("header", tmp_path / "inv").glob("header-*.json"))) == 7
    # The request the cap refused is written down as a deferral, with its route and its instant, and no
    # answer file was invented for it.
    assert len(result["deferrals"]) == 1 and result["deferrals"][0]["route"] == "header", \
        result["deferrals"]


def test_cancellation_across_routes_leaves_every_store_resumable(tmp_path, monkeypatch):
    """An interrupt between routes is the resume case that matters most.

    The crops route answers some frames, then the operator stops the run. A resume has to find both stores
    by content, ask nothing that answered, and finish what was left -- while the attempt that never
    completed stays charged and named, because "we do not know if it arrived" is not a reason to make a
    spend invisible or to re-send it silently.

    It also has to stay *uncertain*. The resume re-sends that frame and gets a reply, and that reply is
    evidence about the resume's attempt, not about the one that was interrupted: an answer to a request
    does not prove that every attempt which carried it received one.
    """
    cutting = Routing(interrupt_after=11)
    with pytest.raises(KeyboardInterrupt):
        run_unseen(tmp_path, monkeypatch, routing=cutting, max_requests=52, passes=1)
    answered_before = {json.loads(path.read_text(encoding="utf-8"))["frame"]
                       for path in store("pip_crops", tmp_path / "inv").glob("crops-*.json")}
    assert len(answered_before) == 11
    interrupted = json.loads((tmp_path / "inv" / "investigation-interrupted.json").read_text(
        encoding="utf-8"))
    assert interrupted["stopped_reason"] == "cancelled_by_operator"
    # 11 requests left this process; the twelfth was charged and then cancelled at the socket, so the
    # ledger holds an attempt with no answer and the runner counts a call that never completed.
    assert interrupted["requests_sent"] == 12 and cutting.total() == 11
    assert len(list(store("pip_crops", tmp_path / "inv").glob("crops-*.json"))) == 11
    charged = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert charged["attempts_charged"] == 12, "the interrupted attempt vanished from the ledger"
    # The interrupted attempt is closed by *this* process, which is the only one that can know its send
    # stopped: uncertain, charged, and no longer merely hanging.
    assert charged["in_flight"] == 0 and charged["uncertain_attempts"] == 1
    cancelled = [entry for entry in charged["entries"] if entry["state"] == "uncertain"][0]
    assert cancelled["reason"] == "interrupted_before_reply"
    assert cancelled["request"] not in [json.loads(path.read_text(encoding="utf-8"))["frame"]
                                        for path in store("pip_crops", tmp_path / "inv")
                                        .glob("crops-*.json")], "a cancelled send left an answer behind"

    resumed, second = run_unseen(tmp_path, monkeypatch, max_requests=60, passes=3, resume=True)
    # The property that matters is not how many requests the resume sent -- a later pass finds new anchors
    # and the count moves -- but that nothing already answered went out a second time. Frames are matched
    # by their ``frame-0175`` token: the request carries the rendered crop's name, the answer file carries
    # the source frame's, and they are the same frame at the same instant.
    def token(path):
        match = re.search(r"frame-\d+", str(path))
        return match.group(0) if match else None

    answered_before_tokens = {token(entry) for entry in answered_before} - {None}
    assert answered_before_tokens
    resent = {token(match) for body in second.bodies["pip_crops"]
              for match in re.findall(r"frame-\d+", json.dumps(body))}
    assert resent, "no frame could be read back out of the resume's own requests"
    assert not (resent & answered_before_tokens), \
        "the resume re-asked {} frames it already had answers for".format(
            sorted(resent & answered_before_tokens))
    assert resumed["stopped_reason"] in ("supported_outcome", "no_new_evidence"), \
        resumed["stopped_reason"]
    document = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    # The cancelled attempt is charged and stays charged, and it is still uncertain after the resume
    # answered the same request: what closes is the *request*, recorded apart from the attempt, so the
    # extra spend stays visible instead of being laundered into "answered".
    after = [entry for entry in document["entries"] if entry["request"] == cancelled["request"]]
    assert len(after) == 2, "the resume did not re-send the cancelled request, so the case is not tested"
    hung, retried = after
    assert hung["state"] == "uncertain" and hung["n"] == cancelled["n"]
    assert hung.get("request_satisfied") is True
    assert retried["state"] == "answered" and retried.get("settled_at") is not None
    assert document["in_flight"] == 0, document["in_flight_requests"]
    assert document["uncertain_attempts"] == 1
    # Nothing in this run was closed by "answer on disk", and that is the point: the attempt whose send
    # stopped was settled by the process that saw it stop, so no later reply is ever read as its outcome.
    # The reconciliation path that *does* cite an answer file is the hard-crash one, tested in
    # test_an_in_flight_attempt_is_reconciled_against_the_answer_on_disk.
    assert not [entry for entry in document["entries"] if entry.get("reason") == "answer on disk"], \
        "a reply was credited to an attempt that never received one"
    assert document["attempts_charged"] == 12 + resumed["requests_sent"], \
        (document["attempts_charged"], resumed["requests_sent"])
    # And the ledger stays consistent for the next process. A further resume may still find work -- the
    # second invocation's own answers open questions it could not ask, and the loop is *supposed* to
    # follow a question from one pass to the next -- but it may never send a frame or instant that
    # already has an answer on disk, and the discovery has to converge.
    answered_snapshot = _answered_snapshot(tmp_path / "inv")
    again, third = run_unseen(tmp_path, monkeypatch, max_requests=60, passes=1, resume=True)
    _assert_no_answered_resends(answered_snapshot, third)
    converged, fourth = run_unseen(tmp_path, monkeypatch, max_requests=60, passes=1, resume=True)
    assert fourth.total() == 0, "the loop did not converge once every answer it made was on disk"
    assert converged["stopped_reason"] == "supported_outcome", converged["stopped_reason"]
    assert json.loads((tmp_path / "attempts.json").read_text(
        encoding="utf-8"))["uncertain_attempts"] == 1


def test_resume_is_refused_when_the_evidence_base_or_a_route_changed(tmp_path, monkeypatch):
    """Reusing answers from a different question set would present one join's wins as another's."""
    run_unseen(tmp_path, monkeypatch, max_requests=6)
    with pytest.raises(ValueError, match="resume refused"):
        run_unseen(tmp_path, monkeypatch, max_requests=6, resume=True, header=False)


def test_an_unfunded_route_is_never_charged(tmp_path, monkeypatch):
    """A route this invocation did not fund gets no client and no attempt, even with a ledger open.

    The alternative is a live endpoint attached to a question nobody approved, which is what the ledger
    guard exists to prevent and which ``--routes`` would otherwise reintroduce.
    """
    result, routing = run_unseen(tmp_path, monkeypatch, max_requests=56, passes=1,
                                 routes=("state",))
    assert counts(routing) == {"pip_crops": 0, "state": 9, "header": 0}
    assert result["budget"]["sent_by_route"]["pip_crops"] == 0
    charged = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert {entry["purpose"] for entry in charged["entries"]} == {"investigate-games:state"}


def test_a_live_run_refuses_before_any_route_client_is_built(tmp_path, monkeypatch):
    """The ledger guard, asserted where it can only be: at the moment a live client would exist.

    Counting sends is not enough -- a refusal that happened after a real client was constructed has already
    pointed something at an endpoint. So the probe here is the constructor, and the run is invoked with no
    injected transport at all, which is the case the guard exists for.
    """
    built = []

    def spy(name):
        def make(*args, **kwargs):
            built.append(name)
            raise AssertionError("%s was constructed after the guard should have refused" % name)
        return make

    for cls in (bi.RoundFactsClient, bp.PipReadClient, bh.HeaderReadClient):
        monkeypatch.setattr(cls, "__init__", spy(cls.__name__), raising=True)
    with pytest.raises(ValueError, match="requires --budget-file"):
        bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                   output_dir=tmp_path / "inv", region=UNSEEN, step=2.0,
                                   batch_frames=6, max_requests=52, games_per_set=None,
                                   header=band(True), crop_runs=frozen.crop_runs())
    assert built == [], "a live client was built for a run with no ledger"
    assert not (tmp_path / "inv").exists(), "the refused run created its output directory anyway"


def test_an_injected_transport_narrows_the_run_instead_of_exposing_the_other_routes(tmp_path,
                                                                                   monkeypatch):
    """Injecting one route's transport must not leave the other two holding live clients.

    Before this rule the test suite itself was the hazard: a run that mocked the state socket and planned
    crop readings built a real ``PipReadClient`` against a real endpoint, and only an absent server stopped
    an unapproved send. An injection now means "this invocation runs these routes"; the rest are reported
    unfunded, priced as they were, and never given a client at all.
    """
    for cls in (bi.RoundFactsClient, bp.PipReadClient, bh.HeaderReadClient):
        monkeypatch.setattr(cls, "_post", Routing(), raising=True)
    result = bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                        output_dir=tmp_path / "inv", region=UNSEEN, step=2.0,
                                        batch_frames=6, max_requests=52, games_per_set=None,
                                        header=band(True), crop_runs=frozen.crop_runs(), expand=0.0,
                                        client_factories={
                                            "state": lambda allowance: bi.RoundFactsClient(
                                                max_requests=allowance)})
    assert result["dispatch_before"]["funded"] == ["state"]
    assert result["dispatch_before"]["unfunded_routes"] == ["pip_crops", "header"]
    assert result["budget"]["ungoverned"] is True, "a run with no live client has no spend to govern"
    unfunded = result["budget"]["unfunded_by_route"]
    assert unfunded["pip_crops"] == 39 and unfunded["header"] == 8, unfunded
    assert result["budget"]["planned_by_route"]["pip_crops"] == 0, \
        "an unfunded route must promise no sends"


def test_the_state_loop_still_needs_no_ledger_because_it_cannot_spend(tmp_path):
    """The guard is about money. An injected transport spends none, and stays injectable.

    Recorded because the exception has to stay deliberate: the moment a factory could reach an endpoint,
    this same signature would be a bypass.
    """
    class Quiet:
        def __init__(self, allowance):
            self.calls = 0
            self.usage = []

        def classify(self, group):
            self.calls += 1
            return [{"id": frame["id"], "state": "gameplay", "confidence": 0.9,
                     "round_clock": None, "round_call": None, "round_end": None,
                     "result_transition": None, "win_dots_p1": None, "win_dots_p2": None,
                     "characters": ["A", "B"], "evidence": "injected"} for frame in group]

    result = bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                        output_dir=tmp_path / "inv", region=UNSEEN, step=2.0,
                                        batch_frames=6, max_requests=1, crop_runs=None,
                                        client_factory=Quiet, routes=("state",))
    assert result["budget"]["ungoverned"] is True and result["budget"]["ledger"] is None
    assert result["requests_sent"] == 1


def test_nothing_about_the_run_leaves_a_silent_pip_fallback(tmp_path, monkeypatch):
    """The one property the whole change is for, stated as an assertion over a finished run.

    Every ``win_dots`` value in the timeline a run writes has to be a crop reading; every whole-frame
    guess has to be a diagnostic. If any path from a socket to a score were ungated, this is what would
    catch it, and it is checked over the merged output rather than over one merge call.
    """
    run_unseen(tmp_path, monkeypatch, max_requests=52, passes=1)
    merged = json.loads((tmp_path / "inv" / "timeline-with-facts.json").read_text(encoding="utf-8"))
    rows_with_pips = [row for row in merged if row.get("win_dots")
                      and any(value is not None for value in row["win_dots"])]
    assert rows_with_pips, "the run produced no pip evidence at all, so this asserts nothing"
    for row in rows_with_pips:
        assert be.pip_grade(row.get("fact_source") or row.get("source")) == "validated"
        assert row.get("pip_evidence_class") == "validated_pip_evidence"
    diagnostics = [row for row in merged if row.get("win_dots_diagnostic")]
    assert diagnostics and all(row.get("win_dots") in (None, [None, None]) or
                               be.pip_grade(row.get("fact_source")) == "validated"
                               for row in diagnostics)


def test_the_saved_header_answers_are_still_reused_rather_than_re_asked(tmp_path, monkeypatch):
    """The six unapproved band answers stay six answers: re-reading them charges nothing.

    Pointing the header ledger at the file that counted the other five is the only honest way to describe
    a mixed load, and none of it sends a request.
    """
    loaded = bi.load_header_responses(",".join(BAND_RUNS), ledger=str(SAVED_LEDGER))
    assert loaded["inference_sent"] is False and loaded["attempts_charged_now"] == 0
    assert loaded["reused_without_resending"] == len(loaded["readings"]) == 11
    assert loaded["provenance"]["ledger"] == str(SAVED_LEDGER)
    per_origin = {origin["directory"]: origin["answers_charged_to_the_named_ledger"]
                  for origin in loaded["provenance"]["origins"]}
    assert sum(per_origin.values()) == 5 and 0 in per_origin.values()
