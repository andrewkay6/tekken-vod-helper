"""The public recovery command, running on the evidence this recording actually holds.

``investigate-games`` used to plan from the classified timeline alone. That made it the one tool in this
repository that could look at the same minute of footage as the read-only report and disagree with it, because
the report merged the saved crop answers and the command did not -- and a disagreement about
``games_with_a_winner`` reads as a discovery about the recording when it is a property of the join. These
tests pin the two together: the command now reads the canonical, recording-scoped evidence base, and the
candidates it publishes are the candidates ``scripts/report_saved_header.py`` publishes, to the field.

Three things are checked about every claim here:

* **Zero sends.** Not one of these tests reaches a model. Where a run is exercised, the transport is the
  real client class with ``_post`` replaced -- the rule this repository already follows for the pip and
  header executors -- and the assertion is both that the run behaved and that nothing left the process.
* **Spend is governed before it is possible.** ``--budget-file`` is part of what a live send means, not a
  refinement of it. A cap that resets every process is not a cap: six real band requests were sent through
  exactly that hole on 2026-10-30, while somebody was testing that the command would refuse an unapproved
  execute. So the refusal is tested with the transport installed and counted, never by running the command.
* **Boundary, score, winner and footage stay four claims.** A candidate that is complete on one and open on
  another is the normal case, and a test that conflated them would not notice.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_budget as bb                 # noqa: E402
from tekken_vod_helper import boundary_cli                          # noqa: E402
from tekken_vod_helper import boundary_evidence as be               # noqa: E402
from tekken_vod_helper import boundary_games as bg                   # noqa: E402
from tekken_vod_helper import boundary_header as bh                  # noqa: E402
from tekken_vod_helper import boundary_investigate as bi             # noqa: E402

import _frozen_corpus as frozen  # noqa: E402  (tests/_frozen_corpus.py)
from tekken_vod_helper import boundary_pips as bp                     # noqa: E402

TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DURATION = 6928.421136
#: The two intervals with crop answers and band answers on disk. Both are known to hold one complete
#: boundary-evidenced set and one partial *with no format supplied*, which is what makes them the fixtures
#: for "the command and the report say one thing".
STAGE_A = (1200.0, 2200.0)
STAGE_B = (3200.0, 4000.0)
#: Two *independent* state batches, three hundred seconds apart, so answering one leaves the other owed.
#: STAGE_A served the ledger tests below until the round-opening rule started reading its leading start
#: off the saved rows -- a READY banner at 1217.216 with the clock at 60, which costs no request -- and
#: this interval's own state questions stay separate whatever the answers say.
STAGE_C = (2220.0, 3200.0)
PRESERVED = ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30"
TRIAL = ROOT / "build/work/bb9/set-end2end-v1"
#: Named per run, never copied into one folder: a merged directory carries one provenance for all its
#: answers, which is how six unapproved responses came to be reported as ledger-counted.
BAND_RUNS = [str(PRESERVED), str(TRIAL / "attribution-run"), str(TRIAL / "band-run")]
LEDGER = TRIAL / "attempts-shared-2026-10-31.json"
FORMAT_POLICY = TRIAL / "format-user-confirmed-2026-10-31.json"
ONE_CROP_RUN = str(ROOT / "build/work/bb9/pip-crops-v2")

pytestmark = pytest.mark.skipif(
    not (TIMELINE.is_file() and MANIFEST.is_file() and PRESERVED.is_dir()
         and all(Path(run, "groups").is_dir() for run in BAND_RUNS)),
    reason="the saved BB9 timeline, manifest and band answers are not present")


def band(value):
    if not value:
        return None
    return bi.load_header_responses(",".join(BAND_RUNS) if value is True else value,
                                    str(LEDGER) if LEDGER.is_file() else None)


def plan(region=STAGE_B, header=True, crop_runs=frozen.crop_runs(), games_per_set=None, **kw):
    """``investigate-games --dry-run``, called the way the CLI calls it."""
    return bi.plan_saved_investigation(
        str(TIMELINE), str(MANIFEST), DURATION, region=region, step=2.0, batch_frames=6,
        max_requests=kw.pop("max_requests", 500), header=band(header), crop_runs=crop_runs,
        games_per_set=games_per_set, **kw)


def claims(candidate):
    """Boundary, score, identity, winner, footage, cut -- everything a marking fault could corrupt."""
    return {key: candidate.get(key) for key in
            ("start", "end", "status", "membership", "games", "game_wins", "deciding_wins",
             "deciding_wins_unit", "set_winner", "concluded_by", "footage_complete", "clip_end_at",
             "game_wins_by_identity", "attribution_withheld")}


def report_shape(window, policy=None, runs=None):
    """The same recovery, computed the way the read-only report computes it.

    Deliberately *not* read back from a saved report file: the claim is that the two tools run one
    implementation, and checking the command against a stale artifact would prove the artifact instead.

    ``runs`` defaults to the frozen corpus because the command side is frozen: leaving the report side on
    live discovery compares two evidence bases rather than two tools, and an executed batch then reads as
    a disagreement between them. It did exactly that on 2026-11-06 -- winner-corroboration-v2's answers
    moved one clip end (`3548.542814 -> 3547.541814`, the game at 3247.24 s confirming one frame earlier),
    and only the frozen-side half of this test saw it. What the live corpus looks like today is
    ``test_corpus_current_state.py``'s job.
    """
    saved = band(True)
    evidence = be.evidence(window, runs=runs if runs is not None else frozen.crop_runs(),
                           facts=str(TIMELINE))
    tracked = be.adjudicate(evidence["observations"], games_per_set=None, header=saved,
                            readings=saved["readings"], policy=policy, region=window)
    return be.recovery(evidence, tracked, header=saved, policy=policy)


# ---------------------------------------------------------------- one evidence base, two tools


@pytest.mark.parametrize("window,winners", [(STAGE_A, 4), (STAGE_B, 3)])
def test_the_public_command_recovers_what_the_read_only_report_recovers(window, winners):
    """The integration claim, as an equality against the report's own functions.

    ``[1200, 2200)`` holds four games with crop-validated winners and ``[3200, 4000)`` holds three; each
    interval recovers one complete candidate plus one partial with no format supplied. Those are the figures
    the report publishes, and the command now reaches them without asking a model anything.
    """
    from_report = report_shape(window)
    from_command = plan(region=window)["recovery"]
    assert ([claims(c) for c in from_command["candidates"]]
            == [claims(c) for c in from_report["candidates"]])
    assert (from_command["games_with_a_winner"] == from_report["games_with_a_winner"] == winners)
    statuses = [c["status"] for c in from_command["candidates"]]
    assert statuses.count("complete") == 1 and "partial" in statuses, statuses
    # The closure is the recording's, not a rule's: no target was supplied to either column here.
    assert from_command["format_comparison"]["without_any_target"]["targets_published"] == 0
    assert any("station_cleared" in (c["concluded_by"] or []) for c in from_command["candidates"]
               if c["status"] == "complete")


@pytest.mark.parametrize("window", [STAGE_A, STAGE_B])
def test_a_recovery_that_never_reached_a_model_says_so(window, monkeypatch):
    """Zero sends is a claim about this process, so it is tested as one.

    Constructing a client is made an error: a dry run that quietly opened one would still print the right
    numbers and would be a spend nobody approved.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("the zero-send path built a client")

    monkeypatch.setattr(bi, "RoundFactsClient", forbidden)
    result = plan(region=window)
    assert result["inference_sent"] is False
    assert result["recovery"]["inference_sent"] is False
    assert result["recovery"]["attempts_charged_now"] == 0
    assert result["evidence"]["inference_sent"] is False


def test_the_command_names_the_corpus_it_read_and_warns_when_it_read_none():
    """A base with no crop readings is not a neutral default, so it is labelled as its own base.

    ``state_only`` is a comparison the report runs too; what must never happen is a tool quoting
    ``games_with_a_winner: 0`` from it as a finding about the footage.
    """
    merged, bare, operator = plan(), plan(crop_runs=None), plan(state_only=True)
    assert merged["evidence"]["base"] == "canonical_pip_merge"
    # This suite reads the frozen corpus by name, so the honest label is "named_by_caller" -- and naming
    # it is the point of the test: a reader of the artifact can tell which evidence base produced the
    # number. Discovery is asserted alongside it, because the two are different bases and neither may be
    # mistaken for the other.
    assert merged["evidence"]["crop_corpus"] == "named_by_caller"
    assert merged["recovery"]["games_with_a_winner"] > 0
    discovered = plan(crop_runs=bi.DISCOVER_CROPS)
    assert discovered["evidence"]["crop_corpus"] == "discovered"
    assert discovered["evidence"]["base"] == "canonical_pip_merge"
    for skipped, label in ((bare, "no_corpus_named"), (operator, "operator_state_only")):
        assert skipped["evidence"]["base"] == be.STATE_ONLY
        assert skipped["evidence"]["crop_corpus"] == label
        assert skipped["recovery"]["games_with_a_winner"] == 0
        assert "describes the join, not the recording" in skipped["evidence"]["warning"]


def test_a_named_corpus_is_recorded_as_named():
    """Reproducing an older plan means naming its inputs, and the artifact has to say that it did."""
    result = plan(crop_runs=ONE_CROP_RUN)
    assert result["evidence"]["crop_corpus"] == "named_by_caller"
    assert result["evidence"]["crop_runs"] == [ONE_CROP_RUN]
    # One run directory is a fraction of the corpus, and the difference is visible in the winner count --
    # which is the whole reason the corpus has to be named at all.
    assert (result["recovery"]["games_with_a_winner"]
            < plan()["recovery"]["games_with_a_winner"])


def test_band_answers_keep_their_provenance_per_run():
    """Three runs, listed as three runs, disclosed as a mixed load.

    The five answers a ledger counted and the six it never saw cannot share one directory-shaped claim, so
    what is asserted here is that the disagreement survives into the plan.
    """
    evidence = plan()["evidence"]
    provenance = evidence["header_provenance"]
    assert evidence["header_responses"] == BAND_RUNS
    assert provenance["approval"] == "mixed"
    assert provenance["attempts_governed_by_a_cap"] is False
    assert provenance["answers_matched_to_a_charged_attempt"] == 5
    assert provenance["answers_unmatched"] == 6
    assert sorted(provenance["answers_from_separate_runs"]) == sorted(BAND_RUNS)
    assert evidence["header_readings"] == 11


def test_the_band_is_consulted_for_identity_and_never_for_a_boundary():
    """Two assertions, because "the band changed nothing here" only means something if it was in the room.

    The first half is a fact about this recording: the same answers, on the same rows, group the games
    identically with and without them, while identity -- who holds a win -- is only available with them.
    The second half is the guard that keeps it a fact.
    """
    with_band, without = plan()["recovery"], plan(header=False)["recovery"]
    assert with_band["grouping_changed_by_band"] is False
    assert ([c["start"] for c in with_band["candidates"]]
            == [c["start"] for c in without["candidates"]])
    assert with_band["band_readings_inside_a_game"] > 0
    assert any(c["game_wins_by_identity"] or c["attribution_withheld"]
               for c in with_band["candidates"])
    assert all(not c["game_wins_by_identity"] and not c["attribution_withheld"]
               for c in without["candidates"])
    assert without["band_mapping_questions"] is None


def test_a_band_reading_that_moved_a_boundary_stops_the_command(monkeypatch):
    """The imagined bug, tested where the comparison happens.

    Two different answers from successive calls is the only way to check that the guard compares the band
    run against the no-band run rather than one run against itself.
    """
    answers = iter([("cut", "as", "prepared"), ("cut", "moved", "somehow")])
    monkeypatch.setattr(be, "grouping", lambda sets: next(answers))
    with pytest.raises(ValueError, match="changed the grouping"):
        plan()


# ---------------------------------------------------------------- the format stays optional


@pytest.mark.skipif(not FORMAT_POLICY.is_file(), reason="the user-confirmed format file is absent")
def test_supplying_the_format_buys_a_score_claim_and_not_a_boundary():
    """Same rows, same band answers, one target applied and one removed.

    The boundaries are identical because the station changes on tape decide them. What a target buys is a
    published score and, where the wins are credited to people, a named winner -- and the plan reports both
    columns so a reader can measure the distance instead of taking it on trust.
    """
    policy = bi.load_format_policy(str(FORMAT_POLICY))
    with_format = plan(games_per_set=3, format_policy=policy)["recovery"]
    without = plan(games_per_set=None)["recovery"]
    assert ([c["start"] for c in with_format["candidates"]]
            == [c["start"] for c in without["candidates"]])
    assert with_format["format_comparison"]["with_format"]["targets_published"] > 0
    assert without["format_comparison"]["with_format"]["targets_published"] == 0
    assert (with_format["format_comparison"]["without_any_target"]["complete"]
            == without["format_comparison"]["without_any_target"]["complete"])
    assert "an unknown target never vetoes a conclusion" in with_format["format_comparison"]["note"]


def test_a_caller_named_target_applies_to_every_candidate_and_says_so():
    """``--games-per-set 3`` is an assumption the artifact has to carry, not a hidden default."""
    named = plan(games_per_set=3)["recovery"]
    withheld = plan(games_per_set=None)["recovery"]
    assert all(c["games_per_set_expected"] == 3 for c in named["candidates"])
    assert all(not c["games_per_set_expected"] for c in withheld["candidates"])


# ---------------------------------------------------------------- typed questions


def test_every_planned_question_names_the_route_that_owns_it():
    """Which executor's question a region is really asking, and how good its answer is.

    The win-dot row has one reading this project validated: the cache-arm crop. A region whose only open
    field is ``win_dots`` is therefore *not* a round-facts question, whatever this command ends up sending,
    and the plan has to say so or a later reader cannot tell an attempt spent on the weaker reading from
    one that out-ranks it.
    """
    result = plan()
    assert result["routes"] and "band question" in result["route_note"]
    for region in result["regions"]:
        assert region["route"] and region["routes"] and region["answer_grade"]
        assert region["header_route"], "a region that could be an identity question does not say so"
        if set(region["asked_fields"]) == {"win_dots"}:
            assert region["route"] == "pip_crops"
            assert "cache_arm_crops_are_the_measured_reading" in region["answer_grade"]
        elif "win_dots" in region["asked_fields"]:
            assert region["route"] == "round_facts_mixed"


def test_a_band_question_already_answered_on_disk_is_reused_and_not_sent():
    """An identity gap is listed with the game it belongs to, and never becomes a frame request.

    Frames cannot answer who sat on which side, so this route's job in the plan is to name the missing
    observation precisely enough to price it -- and to say when nothing is missing. Where a saved band
    answer already covers the span, that is what gets used: this is the interval the attribution pass read
    last week, so it owes nothing on this route and sends nothing (compare the unseen interval, where the
    same gaps become eight band requests).
    """
    recovery = plan(region=STAGE_A)["recovery"]
    assert all(entry["route"] == "header" for entry in recovery["missing_observations"])
    for entry in recovery["missing_observations"]:
        assert entry["for_game"][0] < entry["for_game"][1] and entry["because"]
    # [1200, 2200) is the interval the attribution pass already read: its games hold band answers.
    assert recovery["band_readings_inside_a_game"] > 0
    assert recovery["missing_observations"] == []
    assert recovery["band_mapping_questions"]["asked"] == 0


def test_a_frame_whose_only_open_question_the_corpus_answered_is_not_planned():
    """Reuse is decided by the reading, not by the timestamp having been looked at once.

    Compared by frame path, because that is how the merge joins: a crop reading and a cache frame agree by
    filename, and their float timestamps need not round the same way. And reuse is now priced *per route*:
    the corpus's value shows up as crop requests that are not made, which a plan that only counted
    whole-frame batches could never show.

    "Settled" means the coarse triple, not the window. Three games in this interval are credited on a
    single reading, and the frames beside their deciding reading were never sampled -- so the corroboration
    claim prices six interior frames that no saved answer can retire, and the old expectation of an empty
    crop bill here predates that claim. What reuse still owes is exactly what it has always owed: no
    coarse decision frame is planned twice, and every request that remains names a frame nobody looked at.
    """
    readings = be.crop_observations(be.crop_runs(None))
    answered = {be.frame_of({"path": path}) for path, reading in readings.items()
                if any(value is not None for value in (reading.get("win_dots") or []))}
    assert answered
    merged = plan()
    interior = [entry for entry in merged["pip_requests"]]
    assert interior and all(entry["role"].startswith("freeze_interior_") for entry in interior), \
        "what the corpus did not settle here is not the corroboration question"
    without = plan(crop_runs=None)
    routes = {entry["route"]: entry for entry in without["dispatch"]["routes"]}
    settled = {entry["route"]: entry for entry in merged["dispatch"]["routes"]}
    # 45 decision frames in this interval already have crop readings on disk. Before the routes were
    # dispatched, none of that reuse was visible in the bill: the plan priced 25 whole-frame batches and
    # the 45 measurements that actually decide the games were not asked by anyone.
    assert routes["pip_crops"]["requests_planned"] == 45
    assert routes["pip_crops"]["reused"] == 0
    assert settled["pip_crops"]["requests_planned"] == 6
    assert settled["pip_crops"]["reused"] == 45
    assert without["requests_planned"] == 55 and merged["requests_planned"] == 14
    for region in merged["regions"]:
        assert "win_dots" not in region["asked_fields"]


# ---------------------------------------------------------------- investigation, merge, re-adjudication


# ---------------------------------------------------------------- investigation, merge, re-adjudication
#
# The run path is tested in ``tests/test_typed_recovery_dispatch.py``, on the route that owns each
# question. What used to sit here fed known *crop* counts through a mocked ``RoundFactsClient`` and read
# the two resulting winners as proof that a run's answers reach the tracker. It proved that and nothing
# about the framing: the whole frame is the one picture this project measured as unable to read the pip
# row, so the test validated the wrong route and would have gone green while the crop route was never
# exercised at all. The replacement asserts the same loop end to end with a real ``PipReadClient`` whose
# transport is mocked, and inspects the images that were sent.


# ---------------------------------------------------------------- the ledger, before any send


class FakeTransport:
    """A real client whose socket is replaced: up to the bytes leaving, this is the shipping path.

    An instance rather than a function because a plain callable assigned to a class attribute binds
    ``self``, and this seam is ``_post`` and nothing else.
    """

    def __init__(self, unusable_first=False, cancel_after=None):
        self.requests = []
        self.unusable_first = unusable_first
        self.cancel_after = cancel_after

    def __call__(self, body):
        if self.cancel_after is not None and len(self.requests) >= self.cancel_after:
            raise KeyboardInterrupt("operator stopped the run")
        ids = re.findall(r"id=(\S+) t=", json.dumps(body))
        self.requests.append(ids)
        if self.unusable_first and len(self.requests) == 1:
            return {"choices": [{"message": {"content": "not json at all"}}]}
        return {"choices": [{"message": {"content": json.dumps([
            {"id": int(frame_id), "state": "gameplay", "confidence": 0.9, "round_clock": 30,
             "round_call": 1, "round_end": None, "result_transition": False,
             "win_dots_p1": None, "win_dots_p2": None, "characters": ["A", "B"],
             "evidence": "mocked"} for frame_id in ids])}}]}


def run_live(tmp_path, monkeypatch, budget_file=None, transport=None, ledger_path=None, **kw):
    """A run whose every route is answered by the same stand-in socket.

    Patching only the state client is not enough once a command dispatches three routes: the other two
    would build real clients pointed at a real endpoint, and a test suite would then be one open port away
    from spending an approval. ``routes=("state",)`` keeps these ledger tests about the ledger, and the
    blanket patch is the backstop that makes an accidental live send impossible.
    """
    transport = transport or FakeTransport()
    kw.setdefault("routes", ("state",))
    for cls in (bi.RoundFactsClient, bp.PipReadClient, bh.HeaderReadClient):
        monkeypatch.setattr(cls, "_post", transport, raising=True)
    # ``expand=0.0`` pins the window on purpose. Every number these ledger tests quote is priced on the
    # interval they were handed, and the run's automatic widening works a wider span -- which is right for
    # an operator and wrong for a test that asserts how many attempts a named window owes. The widening is
    # asserted on its own, elsewhere, on the run path.
    options = dict(region=STAGE_B, step=2.0, batch_frames=6, header=band(kw.pop("header", True)),
                   crop_runs=kw.pop("crop_runs", None), max_requests=4, expand=0.0)
    options.update(kw)
    result = bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                        output_dir=tmp_path / "inv",
                                        budget_file=budget_file, **options)
    return result, transport


def groups_on(tmp_path):
    return sorted((tmp_path / "inv" / "requests").glob("group-*.json"))


def test_a_live_run_without_a_ledger_is_refused_before_anything_is_sent(tmp_path, monkeypatch):
    """No bypass in this command: an execute path that can run uncounted eventually runs uncounted.

    The transport is installed and counted, because a refusal that was never able to send is not evidence
    that the guard fires -- that is exactly how six real requests were spent on 2026-10-30.
    """
    transport = FakeTransport()
    monkeypatch.setattr(bi.RoundFactsClient, "_post", transport, raising=True)
    with pytest.raises(ValueError, match="requires --budget-file"):
        bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                   output_dir=tmp_path / "inv", region=STAGE_B, step=2.0,
                                   max_requests=3)
    assert transport.requests == [], "an uncounted attempt left the process"
    assert not (tmp_path / "inv").exists(), "the refused run created its output directory anyway"


def test_an_empty_ledger_path_is_the_same_refusal(tmp_path):
    with pytest.raises(ValueError, match="requires --budget-file"):
        bi.investigate_saved_games(str(TIMELINE), str(MANIFEST), DURATION,
                                   output_dir=tmp_path / "inv", region=STAGE_B, budget_file="")


def test_the_cli_refuses_an_ungoverned_execute_before_planning_anything(tmp_path, monkeypatch, capsys):
    """The guard belongs at the command boundary too, and before the live path is even called."""
    called = []

    def never(*args, **kwargs):
        called.append(args)
        return {}

    monkeypatch.setattr(bi, "investigate_saved_games", never)
    with pytest.raises(SystemExit) as raised:
        boundary_cli.main(["investigate-games", "--timeline", str(TIMELINE),
                           "--frame-manifest", str(MANIFEST), "--duration", str(DURATION),
                           "--region", *[str(v) for v in STAGE_B],
                           "--output-dir", str(tmp_path / "inv")])
    assert raised.value.code == 1 and not called
    assert "budget-file" in json.loads(capsys.readouterr().out)["error"]
    assert not (tmp_path / "inv").exists()


def test_the_cli_refuses_the_two_contradictory_format_flags(tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        boundary_cli.main(["investigate-games", "--timeline", str(TIMELINE),
                           "--frame-manifest", str(MANIFEST), "--duration", str(DURATION),
                           "--dry-run", "--output-dir", str(tmp_path / "inv"), "--no-format",
                           "--format-policy", str(FORMAT_POLICY)])
    assert raised.value.code == 1
    assert "contradict" in json.loads(capsys.readouterr().out)["error"]


def test_the_cli_dry_run_costs_nothing_and_charges_nothing(tmp_path, capsys):
    """``--dry-run`` stays free: no ledger required, and the plan says it sent nothing.

    The guard is on spending, not on looking. A free step that also demanded a budget file would be worked
    around by pointing it at some other run's ledger, which is the laundering ``--header-ledger`` exists
    to make visible.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bi, "RoundFactsClient", lambda *a, **k: pytest.fail("a dry run built a client"))
        boundary_cli.main(["investigate-games", "--timeline", str(TIMELINE),
                           "--frame-manifest", str(MANIFEST), "--duration", str(DURATION),
                           "--region", *[str(v) for v in STAGE_B], "--no-format",
                           "--header-responses", ",".join(BAND_RUNS),
                           "--header-ledger", str(LEDGER), "--dry-run",
                           "--output-dir", str(tmp_path / "plan")])
    published = json.loads(capsys.readouterr().out)
    assert published["inference_sent"] is False and published["attempts_charged_now"] == 0
    assert published["recovery"]["games_with_a_winner"] == 3
    assert published["evidence"]["header_provenance"]["approval"] == "mixed"
    assert (tmp_path / "plan" / "plan.json").is_file()


def test_an_injected_transport_needs_no_ledger_because_it_cannot_spend(tmp_path):
    """The guard is about money. A test transport spends none, and stays injectable.

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
                                        output_dir=tmp_path / "inv", region=STAGE_B, step=2.0,
                                        batch_frames=6, max_requests=1, crop_runs=None,
                                        client_factory=Quiet, routes=("state",))
    assert result["budget"]["ungoverned"] is True and result["budget"]["ledger"] is None
    assert result["requests_sent"] == 1


def test_the_ledger_charges_every_attempt_and_a_resume_inherits_the_same_deadline(tmp_path, monkeypatch):
    """Every attempt is charged before it is sent, and the count a resume sees is the count that spent."""
    ledger = tmp_path / "attempts.json"
    first, transport = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=2,
                                budget_cap=40, budget_minutes=30)
    assert first["budget"]["ungoverned"] is False
    assert first["budget"]["ledger"]["attempts_charged"] == len(transport.requests) == 2
    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert document["cap"] == 40 and document["minutes"] == 30 and document["deadline_at"]
    assert all(entry["state"] == "answered" for entry in document["entries"])
    # The purpose names the route, because one ledger is now shared by three executors and "12 attempts"
    # cannot be audited against an approval that funded a particular kind of request.
    assert all(entry["purpose"] == "investigate-games:state" for entry in document["entries"])

    resumed, after_transport = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=2,
                                        budget_cap=40, budget_minutes=30, resume=True)
    assert after_transport.requests == [], "a resume re-asked a group it already paid for"
    assert resumed["groups_resumed"] == first["groups_completed"] == 2
    after = json.loads(ledger.read_text(encoding="utf-8"))
    # The deadline belongs to the approval, not to the process: a new invocation cannot re-arm it.
    assert after["deadline_at"] == document["deadline_at"]
    assert after["attempts_charged"] == 2


def test_a_plan_owed_more_than_the_ledger_has_is_refused_before_the_first_send(tmp_path, monkeypatch):
    """Asking for more than the remaining allowance is not a licence to spend part of it."""
    ledger = tmp_path / "attempts.json"
    transport = FakeTransport()
    with pytest.raises(ValueError, match="requests are still owed"):
        run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=20, budget_cap=2,
                 transport=transport)
    assert transport.requests == []
    assert not groups_on(tmp_path)
    # Charged nothing, because the guard fired before the socket: a refused plan leaves no trace in spend.
    assert json.loads(ledger.read_text(encoding="utf-8"))["attempts_charged"] == 0


def test_a_resume_owes_only_the_groups_nobody_answered(tmp_path, monkeypatch):
    """The pre-flight refusal counts what is still missing, or a one-frame repair is refused outright.

    Two groups are planned, one attempt is left in the ledger and one group has no answer: that run is
    entitled to continue. Comparing the *plan size* against the remaining attempts would refuse it, and a
    refused repair is how a partial run gets restarted from scratch and charged twice. The resume carries an
    allowance the size of the plan, because ``--max-requests`` shapes the plan rather than only cutting it
    short: a resume with a smaller allowance simply re-plans less work.
    """
    ledger = tmp_path / "attempts.json"
    options = dict(region=STAGE_C, crop_runs=frozen.crop_runs(), budget_cap=2)
    first, transport = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=1,
                                **options)
    # Two *state* batches, which is what this invocation funds: the same interval also owes eight band
    # readings, and those belong to the band route's approval, not to this cap. The eight crop requests
    # are the corroboration claim's, priced from the corpus's own gaps -- frames beside a single-reading
    # deciding frame that nothing on disk has ever looked at. One of the eight is the frame the bounded
    # continuation exposes once the nearest interior is answered: before that repair this interval priced
    # seven and then refused to advance past its own answers.
    priced = plan(region=STAGE_C)
    assert priced["state_requests_planned"] == 2, "this interval no longer exercises a partial plan"
    assert priced["header_requests_planned"] == 8 and priced["pip_requests_planned"] == 8
    assert len(transport.requests) == 1 and first["groups_completed"] == 1
    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert document["attempts_charged"] == 1 and document["attempts_remaining"] == 1
    resumed, second = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=2,
                               resume=True, **options)
    assert len(second.requests) == 1, "the resume neither continued the plan nor named what it owed"
    assert resumed["groups_resumed"] == 1 and resumed["groups_completed"] == 2


def test_an_exhausted_ledger_stops_the_run_and_names_what_it_deferred(tmp_path, monkeypatch):
    """A cap reached mid-run is a stop, not a crash, and a deferred group is written nowhere.

    The retries inside one group are what exhaust a cap the plan alone would have fitted under, which is
    why the cap is charged per attempt: an unusable reply is a real cost, not a free second opinion.
    """
    ledger = tmp_path / "attempts.json"
    transport = FakeTransport(unusable_first=True)
    # The cap is sized to the route this run funds. Sizing it to the whole bill would let the run start
    # and stop on the *other* route's budget, which is a different test.
    owed = plan(region=STAGE_B, header=True, crop_runs=None, routes=("state",))["requests_planned"]
    result, _ = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=owed + 2,
                         budget_cap=owed, transport=transport)
    assert result["status"] == "stopped"
    assert result["stopped_reason"] == "attempt_cap_reached"
    assert result["groups_deferred_by_ledger"] == 1
    assert len(transport.requests) == owed, "the cap is charged per attempt, not per group"
    assert len(groups_on(tmp_path)) == owed - 1, "a deferred group was stored as if it were answered"
    saved = json.loads(ledger.read_text(encoding="utf-8"))
    assert saved["attempts_charged"] == owed == saved["cap"]
    assert result["budget"]["ledger"]["attempts_remaining"] == 0
    # Nothing was sent for the refused group, so nothing beyond the cap was charged for it.
    assert saved["in_flight"] == 0


def test_an_expired_deadline_refuses_the_run_rather_than_handing_it_a_fresh_one(tmp_path, monkeypatch):
    """The deadline is the wall-clock the trial was approved under, so passing it is a refusal."""
    ledger = tmp_path / "attempts.json"
    handle = bb.AttemptLedger.open(ledger, cap=40, minutes=30, label="approved")
    handle.reserve(request="an attempt already spent", purpose="investigate-games")
    handle.settle(handle.entries[0], "answered")
    document = json.loads(ledger.read_text(encoding="utf-8"))
    document["deadline_at"] = document["deadline_at"] - 3600.0
    ledger.write_text(json.dumps(document), encoding="utf-8")
    transport = FakeTransport()
    with pytest.raises(ValueError, match="deadline"):
        run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=4, budget_cap=40,
                 budget_minutes=30, transport=transport)
    assert transport.requests == []
    # The spent attempt stays spent: an expired clock is not a reason to rewrite history and start again.
    assert json.loads(ledger.read_text(encoding="utf-8"))["attempts_charged"] == 1


def test_an_in_flight_attempt_is_reconciled_against_the_answer_on_disk(tmp_path, monkeypatch):
    """A charge whose reply arrived is closed by name, so the next process is not billed for it twice.

    The plan names each group by its fingerprint, which is also the answer file's name -- the only reason
    this reconciliation is possible at all. A request that died with no answer file is deliberately left
    in flight: whether the endpoint finished is unknown, and an unknown spend is never re-sent silently.

    The crash reproduced here is the narrow one this exists for: the reply was written, and the process
    died before it could settle its own attempt. So exactly one attempt hangs, and it is the one the file
    explains.
    """
    ledger = tmp_path / "attempts.json"
    run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=2, budget_cap=40)
    answered = json.loads(groups_on(tmp_path)[0].read_text(encoding="utf-8"))
    tag = bi._group_tag(answered["fingerprint"])
    document = json.loads(ledger.read_text(encoding="utf-8"))
    hung = [entry for entry in document["entries"] if entry["request"] == tag]
    assert len(hung) == 1
    hung[0]["state"] = "in_flight"          # died after the write, before the settle
    hung[0].pop("settled_at", None)
    hung[0].pop("request_satisfied", None)
    document["in_flight"] = 1
    ledger.write_text(json.dumps(document), encoding="utf-8")
    assert json.loads(ledger.read_text(encoding="utf-8"))["in_flight"] == 1

    resumed, transport = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=2,
                                  budget_cap=40, resume=True)
    assert transport.requests == [] and resumed["groups_resumed"] == 2
    states = {entry["request"]: entry for entry in
              json.loads(ledger.read_text(encoding="utf-8"))["entries"]}
    closed = states[tag]
    assert closed["state"] == "answered" and closed["reason"] == "answer on disk"


def test_a_reply_to_a_retry_does_not_answer_the_attempt_it_replaced(tmp_path, monkeypatch):
    """Request satisfaction is not attempt outcome, and the ledger has to keep them apart.

    The cancelled run left one attempt paid for and unresolved; the resume re-sent that frame and its own
    attempt got the reply. Crediting the older attempt with that answer would report two spends as one,
    which is the difference between an honest bill and a fabricated one -- and it would claim knowledge of
    an outcome nobody has: the first send's reply was never seen.
    """
    ledger = tmp_path / "attempts.json"
    cutting = FakeTransport(cancel_after=1)
    with pytest.raises(KeyboardInterrupt):
        run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=4, budget_cap=40,
                 transport=cutting)
    document = json.loads(ledger.read_text(encoding="utf-8"))
    cancelled = [entry for entry in document["entries"] if entry["state"] == "uncertain"]
    assert len(cancelled) == 1, [entry["state"] for entry in document["entries"]]
    unanswered_request = cancelled[0]["request"]

    resumed, transport = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=4,
                                  budget_cap=40, resume=True)
    document = json.loads(ledger.read_text(encoding="utf-8"))
    entry = [e for e in document["entries"] if e["request"] == unanswered_request]
    assert len(entry) == 2, "the resume re-sent the request, so two attempts carry one name"
    hung, answered = entry
    assert hung["state"] == "uncertain", "a later attempt's reply was credited to this one"
    assert hung.get("request_satisfied") is True, "the request is answered, and that is its own fact"
    # The reason stays the interrupt. Reconciliation adds the request-level fact beside it and rewrites
    # nothing: "what stopped this send" and "is the request answered now" are both true at once.
    assert hung["reason"] == "interrupted_before_reply"
    assert answered["state"] == "answered" and answered.get("settled_at") is not None
    # Every charge survives, in both directions: nothing was refunded for the interrupt and nothing was
    # written off as free because a retry succeeded. The first run charged two attempts (one answered,
    # one cancelled at the socket); the resume charged its own sends on top of those.
    assert document["attempts_charged"] == len(document["entries"]) == 2 + resumed["requests_sent"]
    assert document["in_flight"] == 0 and document["uncertain_attempts"] == 1
    assert document["requests_satisfied_by_an_answer_file"] == sorted(
        {entry["request"] for entry in document["entries"] if entry["state"] == "answered"}), \
        "satisfaction is a per-request fact, stamped on every attempt that carried an answered request"
    assert unanswered_request in document["requests_satisfied_by_an_answer_file"]


def test_resume_is_refused_when_the_evidence_base_changed(tmp_path, monkeypatch):
    """Reusing a group answered against a different corpus would present one join's wins as another's."""
    ledger = tmp_path / "attempts.json"
    run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=1, budget_cap=40)
    with pytest.raises(ValueError, match="resume refused"):
        run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=1, budget_cap=40,
                 resume=True, crop_runs=ONE_CROP_RUN)


def test_cancellation_keeps_every_attempt_already_paid_for(tmp_path, monkeypatch):
    """Ctrl-C between groups is a normal ending, and the resume that follows must not pay twice.

    The signal is raised by the transport at the socket, which is where an operator actually catches it.
    The process still dies of the interrupt -- what it leaves on disk is the point of the test.
    """
    ledger = tmp_path / "attempts.json"
    cutting = FakeTransport(cancel_after=1)
    with pytest.raises(KeyboardInterrupt):
        run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=4, budget_cap=40,
                 transport=cutting)
    interrupted = json.loads((tmp_path / "inv" / "investigation-interrupted.json").read_text(
        encoding="utf-8"))
    assert interrupted["stopped_reason"] == "cancelled_by_operator"
    assert interrupted["groups_answered"] == 1 == len(cutting.requests)
    assert len(groups_on(tmp_path)) == 1, "the paid-for group was not stored, so a resume would re-ask it"
    # The attempt that never left is still charged: it was reserved before the socket, which is the whole
    # design. What the interrupt may not do is make a spend invisible -- and it is closed as *uncertain*
    # by the process that saw the send stop, not left hanging for a later process to interpret.
    assert interrupted["budget"]["attempts_charged"] == 2
    assert interrupted["budget"]["in_flight"] == 0
    assert interrupted["budget"]["uncertain_attempts"] == 1

    resumed, transport = run_live(tmp_path, monkeypatch, budget_file=str(ledger), max_requests=4,
                                  budget_cap=40, resume=True)
    assert resumed["groups_resumed"] == 1
    assert len(transport.requests) == 3, "the resume re-sent the cancelled run's answered group"
    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert document["attempts_charged"] == 5
    states = {entry["request"]: entry for entry in document["entries"]}
    cancelled = [entry for entry in document["entries"]
                 if entry.get("reason") == "interrupted_before_reply"]
    assert len(cancelled) == 1 and cancelled[0]["state"] == "uncertain", \
        "a later attempt's reply was credited to the interrupted one"
    assert cancelled[0].get("request_satisfied") is True, "the request is answered; that is separate"
    assert document["in_flight"] == 0, document["in_flight_requests"]
    assert document["uncertain_attempts"] == 1
    # Satisfaction is a fact about the request, so it is stamped on every attempt that carried an
    # answered request -- including the interrupted one, whose own outcome is still unknown.
    assert document["requests_satisfied_by_an_answer_file"] == sorted(
        {entry["request"] for entry in document["entries"] if entry["state"] == "answered"})
    assert cancelled[0]["request"] in document["requests_satisfied_by_an_answer_file"]
    assert cancelled[0]["request"] in states


def test_an_identity_answer_is_reused_not_re_asked():
    """The six unapproved band answers stay six answers: re-reading them charges nothing.

    Pointing the header ledger at the file that counted the other five is the only honest way to describe a
    mixed load, and none of it sends a request.
    """
    loaded = bi.load_header_responses(",".join(BAND_RUNS), ledger=str(LEDGER))
    assert loaded["inference_sent"] is False and loaded["attempts_charged_now"] == 0
    assert loaded["reused_without_resending"] == len(loaded["readings"]) == 11
    assert loaded["provenance"]["ledger"] == str(LEDGER)
    per_origin = {origin["directory"]: origin["answers_charged_to_the_named_ledger"]
                  for origin in loaded["provenance"]["origins"]}
    assert sum(per_origin.values()) == 5 and 0 in per_origin.values()


def test_a_band_directory_that_is_not_one_is_refused_by_name(tmp_path):
    with pytest.raises(Exception, match="no groups/"):
        bi.load_header_responses(str(tmp_path))
# ---------------------------------------------------------------- output propagation
#
# A repaired rule that stops at the module boundary is not repaired: the two defects below were both found
# by reading published output, and both are asserted here at that layer.

def _clip_row(t, clock=None, call=None, state="gameplay", res=False):
    evidence = []
    if clock is not None:
        evidence.append("timer {}".format(clock))
    if call is not None:
        evidence.append("'ROUND {}' callout".format(call))
    if res:
        evidence.append("'YOU WIN' victory banner over win pose")
    if state != "gameplay":
        evidence.append("{} screen".format(state.replace("_", " ")))
    return {"timestamp": float(t), "state": state, "confidence": 0.9, "player1": "Ann",
            "player2": "Zoe", "character1": "Reina", "character2": "Azucena", "score1": 0, "score2": 0,
            "evidence": "; ".join(evidence), "path": "frames/frame-{:04d}.jpg".format(int(t))}


def _pip_row(t, value, confidence=(0.9, 0.95)):
    return {"timestamp": float(t), "state": "gameplay", "confidence": 0.9, "player1": "Ann",
            "player2": "Zoe", "characters": [], "evidence": "", "round_clock": None,
            "win_dots": list(value), "win_dots_drawn": [True, True],
            "win_dots_confidence": list(confidence), "path": "frames/frame-{:04d}.jpg".format(int(t))}


def _six_round_fixture(next_call=None):
    rows = []
    for index in range(6):
        base = index * 40.0
        call = next_call if (index == 5 and next_call) else (2 if index == 4 else None)
        rows.append(_clip_row(base, clock=60, call=call))
        rows.append(_clip_row(base + 12, clock=48))
        rows.append(_clip_row(base + 25, clock=35))
        if index == 4:
            rows.append(_pip_row(base + 27, [2, 2]))
        if index == 5:
            rows.append(_pip_row(base + 0.5, [2, 2]))
            rows.append(_pip_row(base + 27, [3, 2]))
    return sorted(rows, key=lambda r: r["timestamp"])


def _publish(rows, games_per_set=3):
    """The whole public path: adjudicate, project, and hand back the candidate the command would print."""
    observations = bg.build_observations(rows)
    tracked = be.adjudicate(observations, games_per_set=games_per_set, region=(0.0, 400.0))
    # The projection reads its evidence base for provenance, so the stub has to carry the same fields a
    # real one does -- otherwise the test passes by ducking the part of the projection it is checking.
    base = {"observations": observations, "rows": rows, "window": [0.0, 400.0],
            "evidence_base": "synthetic", "facts": "synthetic", "crop_runs": [],
            "crop_runs_source": "synthetic", "crop_readings": len(rows), "framings": {},
            "framing_excluded": 0, "merge": {"crop_readings_merged": 0, "discarded": []}}
    # The caller wants the arm the projection publishes from, and the projection wants all three.
    return tracked["assisted"], be.recovery(base, tracked)


def test_the_public_candidate_reports_the_disputed_game_boundary_and_its_alternative():
    tracked, recovery = _publish(_six_round_fixture(next_call=1))
    candidate = recovery["candidates"][0]
    assert candidate["grouping_unresolved"] is True
    conflicts = candidate["boundary_conflicts"]
    assert len(conflicts) == 1 and conflicts[0]["at"] == 200.0
    assert conflicts[0]["readings_persisted"] == {"p1": [2, 2], "p2": [2, 2]}
    assert conflicts[0]["round_number_announced"] == 1 and conflicts[0]["winners_withheld"] is True
    alternative = [a for a in tracked["sets"][0]["alternative_groupings"]
                   if a.get("kind") == "game_boundary_conflict"]
    assert len(alternative) == 1, "the merged reading was not preserved as an alternative grouping"
    assert candidate["alternative_groupings"] == tracked["sets"][0]["alternative_groupings"]
    assert alternative[0]["would_merge"] == [0.0, 200.0]
    assert alternative[0]["at"] == 200.0 and alternative[0]["joined_with"] == 0.0
    named = [line for line in candidate["missing_evidence"] if "disputed at 200.000 s" in line]
    assert len(named) == 1 and "their wins are withheld" in named[0], \
        "the candidate's own reasons never mention the boundary it cannot settle"


def test_grouping_uncertainty_is_not_merely_the_absence_of_a_completed_target():
    """The projection used to define this as "target not reached", which hid every other kind of doubt.

    Same fixture without the ROUND 1: no target is reached either way, but only the callout variant has a
    disputed boundary, and the two facts have to be distinguishable in the published record.
    """
    _, quiet = _publish(_six_round_fixture())
    _, argued = _publish(_six_round_fixture(next_call=1))
    assert quiet["candidates"][0]["boundary_conflicts"] == []
    assert argued["candidates"][0]["boundary_conflicts"]
    assert quiet["candidates"][0]["grouping_unresolved"] is True, \
        "a candidate with no completed target stopped reporting its own uncertainty"


def test_disputed_boundary_alternatives_stay_with_the_candidate_that_contains_them():
    rows = _six_round_fixture(next_call=1)
    rows += [_clip_row(229, res=True, state="post_match_menu"),
             _clip_row(232, state="controller_prompt")]
    later = [_clip_row(280, clock=60, call=1), _pip_row(280.5, [0, 0]),
             _clip_row(292, clock=48), _pip_row(317, [0, 3]),
             _clip_row(320, res=True, state="post_match_menu")]
    for entry in later:
        entry.update(player1="Kim", player2="Lee")
    tracked, _ = _publish(rows + later)
    assert len(tracked["sets"]) == 2
    first, second = tracked["sets"]
    alternatives = [a for a in first["alternative_groupings"]
                    if a.get("kind") == "game_boundary_conflict"]
    assert len(alternatives) == 1 and alternatives[0]["at"] == 200.0
    assert not any(a.get("kind") == "game_boundary_conflict"
                   for a in second["alternative_groupings"])


def test_audit_and_public_report_agree_when_grouping_is_open_without_a_conflict():
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_recording_coverage as audit
    tracked, recovery = _publish(_six_round_fixture())
    summaries = audit.candidate_summaries(tracked["sets"])
    assert len(summaries) == len(recovery["candidates"]) == 1
    assert summaries[0]["boundary_conflicts"] == []
    assert summaries[0]["grouping_unresolved"] is True
    assert summaries[0]["grouping_unresolved"] == recovery["candidates"][0]["grouping_unresolved"]


def test_the_public_candidate_forwards_the_clip_anchor_and_uncertainty():
    """`_set_record` supplied these and the projection dropped them, so no export could check a cut."""
    rows = [_clip_row(1000, clock=60), _clip_row(1010, clock=50), _clip_row(1020, clock=40),
            _clip_row(1028, clock=32), _pip_row(1030, [2, 3], confidence=(0.9, 0.55)),
            _clip_row(1033, res=True, state="post_match_menu"),
            _clip_row(1055, clock=60), _clip_row(1067, clock=48), _clip_row(1078, clock=37)]
    tracked, recovery = _publish(rows, games_per_set=1)
    candidate = recovery["candidates"][0]
    assert candidate["clip_end_at"] == 1032.0
    assert candidate["clip_end_anchor"] == {"conclusion_event_at": 1028.0, "winner_confirmed_at": 1030.0,
                                            "game_clip_end_at": 1032.0, "set_end_at": 1028.0}
    assert candidate["clip_uncertainty"]["high"] == 1030.0
    assert candidate["clip_uncertainty"]["tape_after_conclusion_seconds"] == 27.0
    assert "configured padding" in candidate["clip_end_basis"]
    # The numbers themselves are the ones the previous round pinned; propagation may not move a cut.
    assert tracked["sets"][0]["clip_end_at"] == candidate["clip_end_at"]


def test_the_audit_publishes_clip_ends_and_carries_them_into_its_claim_comparison():
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_recording_coverage as audit
    tracked, _ = _publish(_six_round_fixture(next_call=1))
    summaries = audit.candidate_summaries(tracked["sets"])
    assert len(summaries) == 1
    summary = summaries[0]
    summary_game = tracked["sets"][0]
    assert summary["clip_end_at"] == summary_game["clip_end_at"] is not None
    assert summary["clip_end_anchor"]["winner_confirmed_at"] == \
        summary_game["clip_end_anchor"]["winner_confirmed_at"]
    assert summary["clip_end_at"] >= summary["clip_end_anchor"]["winner_confirmed_at"], \
        "the published cut stops before the read that names the winner"
    assert summary["clip_uncertainty"] is not None and "configured padding" in summary["clip_end_basis"]
    assert len(summary["boundary_conflicts"]) == 1 and summary["grouping_unresolved"] is True
    row = audit.claims({"recovery": {"candidates": summaries}})[0]
    assert row[-1] == 1, "the conflict count is not part of what a marking fault is diffed against"
    assert row[-2] is not None, "the clip uncertainty is not part of what a marking fault is diffed against"
