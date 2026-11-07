"""The read-only halves: reusing preserved answers, and pricing what is still missing.

Both scripts exist to stop a question being re-asked. One runs the real adjudication path over the band
answers that were already spent, another turns what is left into a priced list, and a third grades an
attribution run against labels inspected off the exact crops. All three read the pip evidence through the
same canonical merge -- because they once disagreed about one interval (four validated winners in one
report, zero in the other) purely by joining the rows differently.

None of them may *build* a client, which is enforced here by making construction an error. The ceiling
constants are still read from the client classes, so ``__init__`` is forbidden rather than the class:
reporting what a request would cost is not sending one.

Fixtures are real wherever the claim is about reality (the preserved answers, the saved crop readings, the
frozen Stage A labels) and synthetic where the point is a control the recording does not contain -- a band
answer naming both sides of a game the crop arm already concluded. That one is invented, and the test says
so while asserting that the winners and labels grading it are not.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_evidence as be
from tekken_vod_helper import boundary_header as bh
from tekken_vod_helper import boundary_pips as bp

ROOT = Path(__file__).resolve().parents[1]
PRESERVED = ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30"
TRIAL = ROOT / "build/work/bb9/set-end2end-v1"
STAGE_A_LABELS = TRIAL / "attribution-labels-eval-frozen.json"
STAGE_A_SPEC = TRIAL / "trial.json"
PREPARED_A = TRIAL / "attribution-run"
#: Every band answer this recording has, named by the run that produced it. Listed, never merged: a
#: combined directory carries one provenance for eleven answers, and that is how six unapproved responses
#: came to be reported as ledger-counted on 2026-10-31.
BAND_RUNS = [str(PRESERVED), str(TRIAL / "attribution-run"), str(TRIAL / "band-run")]
#: The six preserved answers on their own -- the corpus before any of this batch's governed spend.
PRESERVED_ONLY = [str(PRESERVED)]

#: The event's user-confirmed set format (regular sets first to two, the named top four first to three).
#: Every "complete_sets == 0" figure in this file is a statement about a *target*, so the target has to be
#: named for the number to mean anything.
FORMAT_POLICY = TRIAL / "format-user-confirmed-2026-10-31.json"

pytestmark = pytest.mark.skipif(
    not (PRESERVED.is_dir() and STAGE_A_LABELS.is_file() and STAGE_A_SPEC.is_file()
         and PREPARED_A.is_dir()),
    reason="BB9 evidence (preserved band answers, Stage A spec/labels) is not present")

#: Several tests below quote the corpus the two executed trials left behind -- 45 crop answers in
#: [3200, 4000), the attribution answers, and the confirmed format file. Without them those numbers are not
#: wrong, they are undeterminable, so the tests skip rather than assert a state this checkout never had.
requires_trial_corpus = pytest.mark.skipif(
    not ((TRIAL / "run" / "crops" / "groups").is_dir() and FORMAT_POLICY.is_file()),
    reason="the Stage B crop corpus and the confirmed format file are not present")

#: Stage A's interval: four games whose winners the crop arm already validated, none of them covered by a
#: band reading. Measured, and the reason Stage A costs four requests rather than forty-six.
STAGE_A = (1200.0, 2200.0)
#: Stage B's interval: three of the six preserved readings live here, and since 2026-10-31 so do 45
#: crop answers -- three validated winners, which is what the interval was read for.
STAGE_B = (3200.0, 4000.0)


def load_module(name, script, monkeypatch):
    path = ROOT / "scripts" / script
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_clients(monkeypatch):
    """Neither arm may be built in these tests: a client here would be an endpoint call."""
    def forbidden(self, *args, **kwargs):
        raise AssertionError("a vision client was constructed by a read-only script")
    monkeypatch.setattr(bh.HeaderReadClient, "__init__", forbidden)
    monkeypatch.setattr(bp.PipReadClient, "__init__", forbidden)


def run_script(script, name, argv_tail, monkeypatch, capsys, expect_exit=False):
    """Invoke a read-only script the way the docs do, and hand back the JSON it printed."""
    module = load_module(name, script, monkeypatch)
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts" / script)] + [str(a) for a in argv_tail])
    if expect_exit:
        with pytest.raises(SystemExit):
            module.run()
    else:
        module.run()
    return json.loads(capsys.readouterr().out)


def report(extra, monkeypatch, capsys, responses=None, ledger=None):
    argv = ["--responses", str(responses or PRESERVED)]
    if ledger:
        argv += ["--budget-file", str(ledger)]
    return run_script("report_saved_header.py", "report_saved_header", [*argv, *extra],
                      monkeypatch, capsys)


def plan(extra, monkeypatch, capsys, labels=True):
    argv = ["--responses", str(PRESERVED), *extra]
    if labels:
        argv += ["--attribution-labels", str(STAGE_A_LABELS)]
    return run_script("plan_set_end_to_end.py", "plan_set_end_to_end", argv, monkeypatch, capsys)


# ---------------------------------------------------------------- one evidence base, three tools


@requires_trial_corpus
def test_the_report_and_the_plan_agree_about_how_many_winners_exist(tmp_path, monkeypatch, capsys,
                                                                   no_clients):
    """The contradiction this round was written to remove, asserted as an equality.

    One tool merged the saved crop answers and reported four validated winners in ``[1200, 2200)``; the
    other read the state timeline alone for the same interval and reported zero. Both were faithful to the
    rows they fed the tracker, and a reader could not tell that the trial was being priced on evidence the
    report could not see -- which is also why the report's own A2 command could not show the winners Stage
    A exists to attribute.
    """
    reported = report(["--region", *STAGE_A, "--output", str(tmp_path / "report.json")],
                      monkeypatch, capsys)
    priced = plan(["--region", *STAGE_A, "--output", str(tmp_path / "plan.json")], monkeypatch, capsys)
    assert reported["pip_evidence"]["base"] == priced["evidence_base"]["name"] == "canonical_pip_merge"
    assert reported["games_with_a_winner"] == 4, "the report must see the winners Stage A is priced on"
    assert priced["state_of_the_evidence_now"]["games_with_a_winner"] == 4
    assert reported["pip_evidence"]["crop_readings_merged"] == \
        priced["evidence_base"]["crop_readings_merged"] > 0
    assert reported["grouping_changed_by_band"] is False
    # The same equality over the interval Stage B read, where both evidence classes now exist: two tools
    # that share one merge must agree whatever the corpus has grown to. This assertion used to read
    # ``games_with_a_winner == 0`` for this interval, which was true of the *evidence list* the report
    # carried, not of the recording -- the 45 crop answers that make it three were on disk at the time and
    # were not being read. Zero is the stale-list bug's fingerprint, so it is not what goes here now.
    both = report(["--region", *STAGE_B, "--output", str(tmp_path / "both.json")], monkeypatch, capsys)
    both_priced = plan(["--region", *STAGE_B, "--output", str(tmp_path / "both-plan.json")],
                       monkeypatch, capsys)
    # Three of the six preserved answers fall inside a game here, and the 45 crop readings this interval
    # was read for decide three winners: the pairing evidence and the win evidence overlap for the first
    # time on this recording.
    assert both["games_with_a_winner"] == 3 and both["band_readings_inside_a_game"] == 3
    assert both_priced["state_of_the_evidence_now"]["games_with_a_winner"] == 3
    assert both["pip_evidence"]["crop_readings_merged"] == \
        both_priced["evidence_base"]["crop_readings_merged"] > 0
    # And the mirror image still exists on this recording: an interval with a crop-validated winner and no
    # band reading inside that game, where a win is real and nobody can be named for it.
    lonely = report(["--region", 736, 1080, "--output", str(tmp_path / "lonely.json")],
                    monkeypatch, capsys)
    assert lonely["games_with_a_winner"] == 1 and lonely["band_readings_inside_a_game"] == 0
    assert lonely["pip_evidence"]["base"] == "canonical_pip_merge"


def test_the_state_only_comparitor_still_exists_and_labels_itself(tmp_path, monkeypatch, capsys,
                                                                 no_clients):
    """State alone is a legitimate baseline and an illegitimate source of a winner count. Both apply."""
    state = report(["--region", *STAGE_A, "--state-only"], monkeypatch, capsys)
    assert state["pip_evidence"]["base"] == "state_only"
    assert state["games_with_a_winner"] == 0
    assert state["pip_evidence"]["attribution_requires_crop_readings"] is True
    merged = report(["--region", *STAGE_A], monkeypatch, capsys)
    assert merged["games_with_a_winner"] == 4, "a baseline only means something against the merge"


def test_a_prepared_but_unexecuted_run_is_a_named_refusal_not_a_crash(tmp_path, monkeypatch, capsys,
                                                                       no_clients):
    """The ordinary mistake -- running A2 before A1 -- must read as a next step, not a traceback.

    Rebuilt in a temporary directory rather than pointed at the real Stage A run: that directory is
    prepared-and-unexecuted only until the trial is approved, and a test whose subject is "not yet sent"
    cannot be allowed to flip when the sending happens.
    """
    staged = tmp_path / "attribution-run"
    staged.mkdir()
    (staged / "prepared.json").write_text((PREPARED_A / "prepared.json").read_text(encoding="utf-8"),
                                          encoding="utf-8")
    module = load_module("report_refusal", "report_saved_header.py", monkeypatch)
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/report_saved_header.py"), "--responses",
                                      str(staged), "--region", *[str(v) for v in STAGE_A]])
    with pytest.raises(SystemExit) as refused:
        module.run()
    assert "no groups/" in str(refused.value)


def test_the_report_refuses_loudly_if_the_band_ever_moves_a_boundary(monkeypatch, capsys, no_clients):
    """The report's own guard, tested rather than trusted.

    The comparison now lives in :mod:`tekken_vod_helper.boundary_evidence`, shared with the public recovery
    command, so the bug is imagined where the grouping is actually compared. ``grouping()`` is fed different
    answers on successive calls, because the only way to test this is to assume the failure -- and both
    calls returning the same mutated value would prove nothing.
    """
    module = load_module("report_saved_header_guard", "report_saved_header.py", monkeypatch)
    answers = iter([("grouping", "as", "prepared"), ("grouping", "moved", "somehow")])
    monkeypatch.setattr(be, "grouping", lambda sets: next(answers))
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/report_saved_header.py"), "--responses",
                                      str(PRESERVED), "--region", *[str(v) for v in STAGE_B]])
    with pytest.raises(SystemExit) as raised:
        module.run()
    assert "CHANGED THE GROUPING" in str(raised.value)


# ---------------------------------------------------------------- the priced gap


@requires_trial_corpus
def test_the_plan_prices_only_what_no_existing_answer_covers(tmp_path, monkeypatch, capsys, no_clients):
    priced = plan(["--region", *STAGE_B, "--output", str(tmp_path / "missing.json")], monkeypatch,
                  capsys)
    assert priced["inference_sent"] is False
    by_kind = {entry["kind"]: entry for entry in priced["missing_observations"]}
    crops = by_kind["pip_crop_readings_at_round_endings"]
    # Measured, and it is a different measurement than the one this line first held. The interval has 15
    # round-ending windows and all 45 of its crop answers are on disk now, so the price is zero and the
    # reuse is 45: the plan bills a window only when nobody has read it. It used to assert 45 requests
    # against an empty corpus, and that figure was right then too -- what would have been wrong is for the
    # number to stay put after the trial it priced had run.
    assert crops["windows"] == 15 and crops["requests"] == 0 and crops["images"] == 0
    assert crops["reused"] == 45 and crops["deferred"] == 0
    band = by_kind["header_band_readings_inside_unwatched_games"]
    # One game still has no band reading: the 3,958.95-3,993.99 game, whose only reading so far sits past
    # its end. Priced against the combined directory -- the six preserved answers plus this batch's -- the
    # same figure is 0, which is the other half of this test and is checked below.
    assert band["requests"] == 1 and [g["span"][0] for g in band["for_games"]] == [3958.954792]
    assert band["reused"] == 3
    assert len(priced["band_readings_falling_between_games"]) == 3
    # Conclusion labels are inspected off the footage; asking for them would be a category error.
    assert by_kind["independently_inspected_conclusion_labels"]["requests"] == 0

    stages = {entry["stage"]: entry for entry in priced["stages"]}
    assert stages["A"]["requests"] == 4 and stages["A"]["interval"] == list(STAGE_A)
    assert stages["A"]["labels"]["inspected"] is True and stages["A"]["labels"]["labels"] == 4
    # Stage A's own words now depend on whether the interval it priced has since closed a set, which it
    # has: the sentence that used to be a standing disclaimer is now the one that flags the closure.
    assert "attribution alone closed the set" in stages["A"]["what_it_cannot_prove"]
    assert stages["B"]["requests"] == 1 and stages["B"]["already_on_disk"] == 45
    # One request still stands (a game whose only band reading sits past its end), so Stage B is priced,
    # not free -- and the free case is asserted where the corpus actually is free, below.
    assert "no crop reading on disk" in stages["B"]["why"]
    assert "inspected off the footage first" in stages["B"]["precondition"]

    totals = priced["totals"]
    assert totals["new_requests"] == 1 and totals["new_images"] == 1
    assert totals["requests_reused_not_resent"] == 51       # 45 crops + 6 band answers
    assert totals["completion_token_ceiling"] == 1 * bh.HeaderReadClient.request_max_tokens
    joined = " ".join(priced["not_included"])
    assert "six band readings already preserved" in joined and "full-VOD" in joined

    # A price of zero has to be distinguishable from a price function that always says zero. An interval
    # nobody has read still costs what the template says it costs.
    fresh = plan(["--responses", str(PRESERVED), "--region", "4200", "5000"], monkeypatch, capsys)
    fresh_kinds = {entry["kind"]: entry for entry in fresh["missing_observations"]}
    # 45 windows, not the 42 this asserted before Step 0 of docs/set-recovery-stage5-plan.md: reading a
    # clock the prose puts ahead of its word splits 4,919.915-4,984.98 into three rounds' falling digits
    # instead of two, and a third round is a third window nobody has read.
    #
    # Stated as new + reused rather than as a bare 45, because the 4200-5000 interval is no longer unseen:
    # executed batches answered three of these frames, so a pinned "45 new" would fail for a reason that
    # has nothing to do with the claim. What must stay true is that the interval prices 45 windows and
    # that a zero price is distinguishable from a price function that always says zero.
    crops = fresh_kinds["pip_crop_readings_at_round_endings"]
    assert crops["requests"] + crops["reused"] == 45, crops
    assert crops["requests"] > 0, "an interval that still owes reads priced them as free"
    assert fresh["totals"]["new_requests"] == crops["requests"] + 4   # + the 4 reads that are not crops
    assert "no crop reading on disk" in fresh["stages"][1]["why"]


@requires_trial_corpus
def test_the_plan_prices_zero_once_the_interval_is_read(tmp_path, monkeypatch, capsys, no_clients):
    """The whole batch of saved answers, offered at once: the interval is free and says why it is free.

    This is the executed state of the trial, kept as its own assertion because the point of running the
    trial was to reach it -- and because a plan that still asked for the interval's whole crop batch with 56
    answers on disk
    would be the defect this repository has now been bitten by twice.
    """
    priced = plan(["--responses", ",".join(BAND_RUNS), "--region", *STAGE_B,
                   "--output", str(tmp_path / "post.json")], monkeypatch, capsys)
    kinds = {entry["kind"]: entry for entry in priced["missing_observations"]}
    assert priced["totals"]["new_requests"] == 0
    assert priced["totals"]["requests_reused_not_resent"] == 56
    assert kinds["pip_crop_readings_at_round_endings"] == {
        **kinds["pip_crop_readings_at_round_endings"], "requests": 0, "reused": 45, "windows": 15}
    assert kinds["header_band_readings_inside_unwatched_games"]["requests"] == 0
    stage_b = next(entry for entry in priced["stages"] if entry["stage"] == "B")
    assert stage_b["requests"] == 0 and "argued from a named missing observation" in stage_b["why"]


@requires_trial_corpus
def test_the_overlap_measurement_is_why_the_trial_is_staged(monkeypatch, capsys, no_clients):
    """The finding that reframed the ask, re-measured rather than kept as prose.

    No interval on disk used to hold both a crop-validated winner and a band reading inside the same game,
    which is why the ask was staged. Both trials have now run, so this asserts the measurement moved -- the
    claim under test is that the number is *computed from the corpus*, in either direction, and that the
    consequence sentence follows it instead of repeating a historical finding.
    """
    priced = plan(["--region", *STAGE_B], monkeypatch, capsys)
    surveyed = {tuple(entry["interval"]): entry for entry in
                priced["evidence_overlap"]["intervals_surveyed"]}
    assert priced["evidence_overlap"]["any_interval_has_both_evidences"] is True
    assert surveyed[STAGE_A]["games_with_a_validated_winner"] == 4
    assert surveyed[STAGE_A]["games_with_a_band_reading_inside"] == 0
    assert surveyed[STAGE_B]["games_with_a_validated_winner"] == 3
    assert surveyed[STAGE_B]["games_with_a_band_reading_inside"] == 3
    assert "at least one interval holds both" in priced["evidence_overlap"]["consequence"]
    # And the closure no longer belongs to the format at all: with a first-to-three target configured, no
    # target configured, or the confirmed stage policy, the survey reports the same one complete set per
    # interval, because the station changes on tape conclude them either way. That equality is the point of
    # removing the veto -- a zero here would now mean "the footage does not show it ending", never "the
    # rules were not supplied". The three intervals nobody ran a trial on stay at zero even with winners on
    # disk, which is the control that keeps this from being a universal closer: their bounds are analysis
    # edges, not station changes, and the survey says so.
    assert [surveyed[key]["complete_sets"] for key in sorted(surveyed)] == [0, 0, 0, 1, 1]
    # With the confirmed format *and* the answers that name the round inside each interval, one candidate
    # in each closes: [1200, 2200) reaches first-to-two in a losers round-1 set, [3200, 4000) reaches it in
    # the losers quarter-final. Offering only the six preserved readings instead leaves the first interval
    # with no stage label at all, and an unlabeled candidate is measured against every target the format
    # admits -- so it stays open rather than closing on the flattering one.
    formatted = plan(["--responses", ",".join(BAND_RUNS), "--region", *STAGE_B,
                      "--format-policy", str(FORMAT_POLICY)], monkeypatch, capsys)
    by_at = {tuple(entry["interval"]): entry
             for entry in formatted["evidence_overlap"]["intervals_surveyed"]}
    assert by_at[STAGE_B]["complete_sets"] == 1 and by_at[STAGE_A]["complete_sets"] == 1
    # The format still changes what a closure *says*: with the policy, the interval whose winners are named
    # publishes a target and a champion; with no target at all the same set is still concluded and no name
    # is claimed. Compared on the report, which is the tool that publishes both columns side by side.
    named = report(["--responses", ",".join(BAND_RUNS), "--region", *STAGE_A, "--format-policy",
                    str(FORMAT_POLICY)], monkeypatch, capsys)
    unclaimed = report(["--responses", ",".join(BAND_RUNS), "--region", *STAGE_A, "--no-format"],
                       monkeypatch, capsys)
    assert named["format_comparison"]["with_format"]["winners_named"] == 1
    assert unclaimed["format_comparison"]["with_format"]["winners_named"] == 0
    assert named["format_comparison"]["with_format"]["complete"] == 1
    assert unclaimed["format_comparison"]["without_any_target"]["complete"] == 1


def test_the_plan_never_calls_itself_scoreable_without_labels(monkeypatch, capsys, no_clients):
    priced = plan(["--region", *STAGE_B], monkeypatch, capsys, labels=False)
    stage_a = next(entry for entry in priced["stages"] if entry["stage"] == "A")
    assert stage_a["labels"]["inspected"] is False
    assert "cannot be graded" in stage_a["labels"]["because"]


def test_an_unfrozen_label_file_is_refused_rather_than_adopted(tmp_path, monkeypatch, no_clients):
    thawed = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))
    thawed["frozen"] = False
    path = tmp_path / "thawed.json"
    path.write_text(json.dumps(thawed), encoding="utf-8")
    module = load_module("plan_thawed", "plan_set_end_to_end.py", monkeypatch)
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/plan_set_end_to_end.py"), "--responses",
                                      str(PRESERVED), "--region", *[str(v) for v in STAGE_B],
                                      "--attribution-labels", str(path)])
    with pytest.raises(SystemExit, match="not a frozen label file"):
        module.run()


def test_the_plan_stops_billing_for_windows_that_are_already_read(tmp_path, monkeypatch, capsys,
                                                                 no_clients):
    """Reuse has to be measured, not asserted: an interval the crop arm covered costs nothing new.

    ``[1980, 2140)`` sits inside the executed stage-4 run, so its round windows come back as reused
    readings. A plan that billed them again would be selling the same evidence twice.
    """
    priced = plan(["--region", "1980", "2140"], monkeypatch, capsys)
    crops = {entry["kind"]: entry for entry in priced["missing_observations"]}[
        "pip_crop_readings_at_round_endings"]
    assert crops["reused"] > 0, "this interval is covered by saved crop answers"
    # Measured: every window in [1980, 2140) is already read except the one at the interval's edge, whose
    # post-K.O. frames fall past 2137.13 s where the saved corpus stops. Reuse is counted, not assumed.
    assert crops["windows"] == 1 and crops["requests"] == 0 or crops["requests"] < 3, crops
    assert crops["deferred"] == 0


# ---------------------------------------------------------------- the artifacts themselves


def test_the_stage_a_spec_and_labels_describe_the_same_crops_and_carry_no_answers():
    """Independence and identity, checked on the artifacts the commands actually use.

    A label is a claim about the bytes that leave the machine; a runtime spec that also carried the answers
    would end the trial before it started and turn its stop rules into label checks.
    """
    spec = bh.load_trial(STAGE_A_SPEC)
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))
    assert spec["version"] == "attribution-stage-a-v1" and labels["frozen"] is True
    assert labels["annotations_for"] is None and labels["pip_readings_are_not_scored"] is True
    assert len(spec["instants"]) == len(labels["labels"]) == 4
    assert spec["labels_are_runtime_input"] is False
    # The spec's own selection rule reads a validated winner, and says so: it is not the header trial's
    # fixed grid, and hiding that would hide what this stage may and may not claim.
    assert "validated winner" in spec["selection_rule"]
    assert "pip-crop" in labels["independence_disclosure"]["pip_labels_are_not_band_labels"]

    prepared = json.loads((PREPARED_A / "plan-header.json").read_text(encoding="utf-8"))
    sent = {round(float(row["at"]), 6): row["image_sha256"] for row in prepared["requests"]}
    for entry in labels["labels"]:
        assert entry["band_sha256"] == sent[round(float(entry["at"]), 6)], entry["at"]
        assert Path(entry["band"]).is_file(), "the labelled crop must still be the one on disk"
        assert entry["names"]["left"] and entry["names"]["right"]
        assert entry["games_won"]["left"] is not None and entry["games_won"]["right"] is not None
    # The disagreement the labels record is between two sources, neither edited to fit the other.
    assert labels["known_disagreement"]["band_displays"] == "Buk 1 - 0 Rebix"
    assert "1961.96-1987.99" in " ".join(labels["unlabelled_gaps"])


def test_labels_are_never_reachable_from_the_runtime_spec(tmp_path):
    """The guard that keeps a cross-check from becoming a self-confirmation."""
    smuggled = json.loads(STAGE_A_SPEC.read_text(encoding="utf-8"))
    smuggled["instants"][0]["names"] = {"left": "Buk", "right": "Rebix"}
    path = tmp_path / "cheating.json"
    path.write_text(json.dumps(smuggled), encoding="utf-8")
    with pytest.raises(bh.HeaderError, match="evaluator truth"):
        bh.load_trial(path)


# ---------------------------------------------------------------- the attribution evaluator


def write_answers(directory, labels, mutate=None):
    """Store answers in the real stored format, so the evaluator faces its own input contract."""
    store = Path(directory) / "groups"
    store.mkdir(parents=True, exist_ok=True)
    for entry in labels:
        payload = []
        for side, key in (("LEFT", "left"), ("RIGHT", "right")):
            row = {"item": 1, "side": side, "player": entry["names"][key],
                   "games": entry["games_won"][key], "games_drawn": True,
                   "pips": (entry["pip_row"].get(key) if entry["pip_row"]["state"] == "drawn" else None),
                   "pips_drawn": entry["pip_row"]["state"] == "drawn",
                   "label": entry["label_read"], "confidence": 0.9}
            if mutate:
                mutate(row, side)
            payload.append(row)
        parsed, errors = bh.validate_reply(payload, [{"id": "x"}])
        assert errors == []
        request = {"id": "header-{}".format(entry["frame_id"]), "arm": bh.ARM, "at": entry["at"],
                   "frame": entry["frame"], "frame_timestamp": entry["frame_timestamp"],
                   "frame_id": entry["frame_id"], "question": "identity_mapping",
                   "because": "test", "selected_by": "midpoint", "image": entry["band"],
                   "image_sha256": entry["band_sha256"],
                   "images": [{"path": entry["band"], "sha256": entry["band_sha256"],
                               "size": entry["band_size"], "box": entry["band_box"]}]}
        fingerprint = bh.request_fingerprint(request, "test-model")
        bh._write_group(store / bh.group_filename(fingerprint), request, fingerprint, payload, parsed,
                        "", "test-model")
    return Path(directory)


def test_the_evaluator_refuses_to_report_a_rate_it_did_not_get(tmp_path, monkeypatch, capsys,
                                                             no_clients):
    """Scored against the preserved answers, which cover a different interval: an honest null.

    Four crops expected, none answered, every credited win reported unattributed, and the cross-check
    refusing to read a missing band answer as the scoreboard being wrong. Non-zero exit, because a scripted
    gate must not mistake an unfinished evaluation for a finished one.
    """
    evaluated = run_script("evaluate_attribution.py", "evaluate_null",
                           ["--run", str(PRESERVED), "--labels", str(STAGE_A_LABELS), "--region",
                            *STAGE_A], monkeypatch, capsys, expect_exit=True)
    assert evaluated["complete"] is False and evaluated["accuracy_is_reportable"] is False
    assert evaluated["score"] is None
    assert evaluated["transcription"]["crops_answered"] == 0
    assert evaluated["transcription"]["no_answer"] == 4
    assert evaluated["inference_sent"] is False
    assert evaluated["responses"]["provenance"]["incident_recorded"] is True
    assert {entry["outcome"] for entry in evaluated["attribution"]["games"]} == {"unattributed"}
    assert all(entry["credited_to"] is None for entry in evaluated["attribution"]["games"])
    assert all(entry["why_unattributed"] for entry in evaluated["attribution"]["games"])
    assert evaluated["attribution"]["deciding_units_seen"] == ["sides"]
    # An absent reading is never a contradiction. What remains are findings about the interval's left edge.
    assert {entry["kind"] for entry in evaluated["tally_cross_check"]["disagreements"]} <= {
        "band_ahead_candidate_starts_mid_match"}, evaluated["tally_cross_check"]["disagreements"]
    assert evaluated["tally_cross_check"]["labels_uncomparable"]


def test_the_evaluator_credits_each_validated_win_to_the_player_the_label_names(tmp_path, monkeypatch,
                                                                               capsys, no_clients):
    """The public-path combination the review asked for: saved pip answers plus header answers, scored.

    The four answers here are invented -- they stand in for the run Stage A has not been approved to send.
    What grades them is not: the game winners come from the crop answers already on disk through the
    canonical merge, and the names come from labels inspected off these exact crops. A match between the two
    is a cross-check, not a restatement.
    """
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))
    run_dir = write_answers(tmp_path / "attribution-run", labels["labels"])
    evaluated = run_script("evaluate_attribution.py", "evaluate_real",
                           ["--run", str(run_dir), "--labels", str(STAGE_A_LABELS), "--region",
                            *STAGE_A], monkeypatch, capsys)
    assert evaluated["complete"] is True and evaluated["accuracy_is_reportable"] is True
    assert evaluated["attribution"]["evidence_base"] == "canonical_pip_merge"
    assert evaluated["transcription"]["matched"] == 4
    assert evaluated["transcription"]["fields"] == {"matched": 16, "mismatched": 0, "not_claimed": 0}
    assert all(row["crop_matches_label"] for row in evaluated["transcription"]["rows"])
    credited = evaluated["attribution"]["games"]
    assert len(credited) == 4, "the merge must yield the four winners Stage A was priced on"
    assert {entry["outcome"] for entry in credited} == {"matched"}, credited
    # Two for Buk (1588.6 s and 1988.0 s), one for Rebix (1820.8 s), one for Ariz (1216.2 s): each credited
    # because the crop named the winning side and the label names who held that side at that moment.
    assert [entry["credited_to"] for entry in credited].count("Buk") == 2
    assert [entry["credited_to"] for entry in credited].count("Rebix") == 1
    assert [entry["credited_to"] for entry in credited].count("PWDDA | Ariz") == 1
    assert all(entry["label_crop_sha256"] for entry in credited)
    assert evaluated["attribution"]["deciding_units_seen"] == ["identities"]
    # The board's own tally is where the two sources part company, and the finding is classified rather than
    # resolved: a board ahead of a candidate that had concluded nothing describes the interval's left edge.
    kinds = {entry["kind"] for entry in evaluated["tally_cross_check"]["disagreements"]}
    assert "band_ahead_candidate_starts_mid_match" in kinds, evaluated["tally_cross_check"]
    agreed = [check for check in evaluated["tally_cross_check"]["checks"]
              if check.get("label_at") == 1694.69291]
    assert agreed and agreed[0]["agree"] is True, "here the board and the recovery do agree"
    assert evaluated["claims_this_cannot_support"], "a score must arrive with its limits"


def test_a_wrong_name_is_a_mismatch_and_never_a_set_winner(tmp_path, monkeypatch, capsys, no_clients):
    """Negative control: an answer that confidently names the wrong people."""
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))
    wrong = json.loads(json.dumps(labels))
    for entry in wrong["labels"]:
        entry["names"]["left"], entry["names"]["right"] = "Impostor One", "Impostor Two"
    run_dir = write_answers(tmp_path / "wrong", wrong["labels"])
    evaluated = run_script("evaluate_attribution.py", "evaluate_wrong",
                           ["--run", str(run_dir), "--labels", str(STAGE_A_LABELS), "--region",
                            *STAGE_A], monkeypatch, capsys)
    fields = evaluated["transcription"]["fields"]
    assert fields["mismatched"] == 8 and fields["matched"] == 8, fields
    # Eight mismatched: the four nameplates on both sides. Eight matched: the displayed tallies, which this
    # control copies faithfully -- a wrong name and a right score are separable, and the grade keeps them so.
    assert {entry["sides"][s]["player_outcome"] for entry in evaluated["transcription"]["rows"]
            for s in ("left", "right")} == {"mismatched"}
    assert {entry["outcome"] for entry in evaluated["attribution"]["games"]} == {"mismatched"}
    assert all(candidate["set_winner"] is None for candidate in evaluated["attribution"]["candidates"])


def test_a_declining_answer_is_counted_as_declining_not_as_wrong(tmp_path, monkeypatch, capsys,
                                                                no_clients):
    """The point of permitting unreadability, scored honestly rather than punished for it."""
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))

    def decline(row, side):
        row.update(player=None, games=None, games_drawn=True, pips=None, pips_drawn=True, label=None,
                   confidence=0.6)

    run_dir = write_answers(tmp_path / "declined", labels["labels"], mutate=decline)
    evaluated = run_script("evaluate_attribution.py", "evaluate_decline",
                           ["--run", str(run_dir), "--labels", str(STAGE_A_LABELS), "--region",
                            *STAGE_A], monkeypatch, capsys)
    fields = evaluated["transcription"]["fields"]
    assert fields["matched"] == 0 and fields["mismatched"] == 0
    assert fields["not_claimed"] == 16, "a refusal is a refusal, and it is counted as one"
    assert evaluated["transcription"]["partially_declined"] == 4
    assert evaluated["attribution"]["unattributed_games"] == 4
    # An unusable reading may not move the target onto identities.
    assert all(candidate["deciding_wins_unit"] == "sides"
               for candidate in evaluated["attribution"]["candidates"])


def test_the_evaluator_refuses_unfrozen_or_empty_label_files(tmp_path, monkeypatch, no_clients):
    thawed = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))
    thawed["frozen"] = False
    path = tmp_path / "thawed.json"
    path.write_text(json.dumps(thawed), encoding="utf-8")
    module = load_module("evaluate_thawed", "evaluate_attribution.py", monkeypatch)
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/evaluate_attribution.py"), "--run",
                                      str(PRESERVED), "--labels", str(path), "--region",
                                      *[str(v) for v in STAGE_A]])
    with pytest.raises(SystemExit, match="not frozen"):
        module.run()

    empty = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))
    empty["labels"] = []
    path2 = tmp_path / "empty.json"
    path2.write_text(json.dumps(empty), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/evaluate_attribution.py"), "--run",
                                      str(PRESERVED), "--labels", str(path2), "--region",
                                      *[str(v) for v in STAGE_A]])
    with pytest.raises(SystemExit, match="no labels"):
        module.run()


# ---------------------------------------------------------------- what a ledger is allowed to say
def ledger_at(path, frames, cap=70, minutes=115.0):
    """A ledger in the real on-disk shape, with one answered attempt naming each frame."""
    entries = [{"n": i + 1, "request": Path(f).stem, "purpose": "attribution", "state": "answered",
                "charged_at": 1.0, "deadline_at": 1.0 + minutes * 60.0}
               for i, f in enumerate(frames)]
    Path(path).write_text(json.dumps({
        "kind": "attempt-ledger", "path": str(path), "label": "attribution-run:attribution",
        "cap": cap, "attempts_charged": len(entries), "attempts_remaining": cap - len(entries),
        "minutes": minutes, "deadline_at": 1.0 + minutes * 60.0, "in_flight": 0, "dry_run": False,
        "entries": entries}), encoding="utf-8")
    return Path(path)


def test_answers_charged_to_a_ledger_say_so(tmp_path):
    """A governed run must not be described as off-books, or its record is wrong in the other direction.

    The incident taught this loader to refuse a ledger it never had. Refusing one it *did* have would turn
    a capped run into an unaccountable one on paper, which is the same defect pointing the other way.
    """
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    run_dir = write_answers(tmp_path / "governed", labels)
    book = ledger_at(tmp_path / "attempts.json", [entry["frame"] for entry in labels])

    loaded = bh.saved_readings(run_dir, ledger=book)
    assert loaded["inference_sent"] is False and loaded["attempts_charged_now"] == 0
    provenance = loaded["provenance"]
    assert provenance["ledger_recorded"] is True
    assert provenance["attempts_governed_by_a_cap"] is True
    assert provenance["attempts_governed_by_a_deadline"] is True
    assert provenance["incident_recorded"] is False
    assert provenance["answers_matched_to_a_charged_attempt"] == len(labels)
    assert provenance["answers_unmatched"] == 0
    assert all(entry["charged_to_ledger"] is True for entry in loaded["readings"])


def test_an_unrelated_ledger_borrows_no_governance(tmp_path):
    """A path that exists is not provenance: the ledger has to name the frames these answers read."""
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    run_dir = write_answers(tmp_path / "off-books", labels)
    other = ledger_at(tmp_path / "other.json", ["/x/frames/frame-0001.jpg", "/x/frames/frame-0002.jpg"])

    loaded = bh.saved_readings(run_dir, ledger=other)
    provenance = loaded["provenance"]
    assert provenance["ledger_recorded"] is False
    assert provenance["attempts_governed_by_a_cap"] is False
    assert provenance["answers_unmatched"] == len(labels)
    assert all(entry["charged_to_ledger"] is False for entry in loaded["readings"])
    assert "no answered attempt" in provenance["disclosure"]


def test_no_ledger_keeps_the_honest_default(tmp_path):
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    run_dir = write_answers(tmp_path / "unclaimed", labels)
    loaded = bh.saved_readings(run_dir)
    assert loaded["provenance"]["ledger_recorded"] is False
    assert loaded["provenance"]["ledger"] == "not recorded here"
    assert all("charged_to_ledger" not in entry for entry in loaded["readings"])


def test_a_pointed_at_non_ledger_is_a_refusal_not_a_shrug(tmp_path):
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    run_dir = write_answers(tmp_path / "wrongfile", labels)
    not_a_ledger = tmp_path / "notes.json"
    not_a_ledger.write_text(json.dumps({"kind": "something-else"}), encoding="utf-8")
    with pytest.raises(bh.HeaderError, match="not an attempt ledger"):
        bh.saved_readings(run_dir, ledger=not_a_ledger)
    with pytest.raises(bh.HeaderError, match="no attempt ledger"):
        bh.saved_readings(run_dir, ledger=tmp_path / "absent.json")


def test_the_report_publishes_the_ledger_it_was_handed(tmp_path, monkeypatch, capsys, no_clients):
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    run_dir = write_answers(tmp_path / "reported", labels)
    book = ledger_at(tmp_path / "shared.json", [entry["frame"] for entry in labels])
    reported = report(["--region", *STAGE_A, "--output", str(tmp_path / "r.json")],
                      monkeypatch, capsys, responses=run_dir, ledger=book)
    assert reported["responses"]["provenance"]["ledger_recorded"] is True
    assert reported["responses"]["provenance"]["ledger_cap"] == 70
    # and the adjudication is untouched by who counted the spend
    assert reported["grouping_changed_by_band"] is False


def _copied_together_run(tmp_path, labels, governed_frames):
    """One directory holding answers from two runs, which is how the aggregate went wrong.

    Reproduced rather than described: a merged ``groups/`` folder is the shape anyone reaches for when a
    tool takes one directory, and its eleven answers carry eleven frames but only one provenance.
    """
    import shutil
    directory = tmp_path / "merged"
    (directory / "groups").mkdir(parents=True)
    for source in (PRESERVED / "groups").glob("header-*.json"):
        shutil.copy2(source, directory / "groups" / source.name)
    answered = write_answers(tmp_path / "governed", labels)
    for source in (answered / "groups").glob("header-*.json"):
        shutil.copy2(source, directory / "groups" / source.name)
    book = ledger_at(tmp_path / "attempts.json", governed_frames)
    return directory, book


def test_a_mixed_load_never_claims_its_cap(tmp_path):
    """Five governed answers do not govern the six that nothing counted.

    The defect was an ``any()``: one match inside a merged directory printed
    ``attempts_governed_by_a_cap: true`` beside the claim that "each answer names an attempt this ledger
    charged", over a load that was majority unapproved spend. Governance is a statement about every answer
    being read, so it is computed all-or-nothing, and a partial match is its own state rather than something
    to round up.
    """
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    directory, book = _copied_together_run(tmp_path, labels, [entry["frame"] for entry in labels])

    merged = bh.saved_readings(directory, ledger=book)
    provenance = merged["provenance"]
    assert provenance["approval"] == "mixed"
    assert provenance["attempts_governed_by_a_cap"] is False
    assert provenance["attempts_governed_by_a_deadline"] is False
    assert provenance["ledger_recorded"] is False
    assert provenance["answers_matched_to_a_charged_attempt"] == len(labels)
    assert provenance["answers_unmatched"] == 6
    assert "NOT governed as a whole" in provenance["disclosure"]
    assert sum(1 for r in merged["readings"] if r["charged_to_ledger"]) == len(labels)
    assert len(merged["readings"]) == len(labels) + 6


def test_the_run_owns_its_own_band_answers_while_the_borrowed_keep_their_history(tmp_path):
    """Per origin, because one sentence about a mixed load is always wrong about half of it.

    The executed unseen trial charged 8 band answers to its own ledger, then read them back beside 11
    answers from earlier runs. The corpus provenance described the whole load with the sentence written
    for the borrowed half -- "preserved from a run that has already happened... no ledger is claimed" --
    and counted the fresh 8 among the answers with no record of their spend, which understated a governed
    run. A borrowed answer's history is not this run's, and this run's spend is not historical.
    """
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    fresh = write_answers(tmp_path / "this-run", labels)
    # investigate-games charges the route-scoped tag `header:frame-2401` while run_header_trial.py
    # charges the bare stem. A claim matching only one of the two reported a counted spend as though
    # nothing had counted it.
    own_book = ledger_at(tmp_path / "attempts.json",
                         ["header:%s" % Path(entry["frame"]).stem for entry in labels])

    loaded = bh.saved_readings([PRESERVED, fresh], own_run=(fresh, own_book))
    provenance = loaded["provenance"]
    per_origin = {Path(entry["directory"]).name: entry for entry in provenance["per_origin"]}

    assert per_origin["this-run"]["ledger_claim"] == "governed"
    assert per_origin["this-run"]["answers_charged_to_the_named_ledger"] == len(labels)
    assert Path(per_origin["this-run"]["ledger"]).name == "attempts.json"
    assert per_origin["unapproved-execute-2026-10-30"]["ledger_claim"] == "incident_recorded"
    # An incident record is a record -- the wrong one, and it stays visible as that. Counting those six
    # as "no record of their spend" would soften what happened on 2026-10-30 into an omission.
    assert provenance["answers_with_no_record_of_their_spend"] == 0
    assert provenance["answers_with_an_incident_record"] == 6
    assert provenance["this_run"] == {"directory": str(fresh), "ledger": str(own_book),
                                      "answers": len(labels)}
    assert provenance["approval"] == "mixed"
    assert provenance["attempts_governed_by_a_cap"] is False
    assert "sent by this command" in provenance["disclosure"]
    assert "preserved from earlier runs" in provenance["disclosure"]


def test_a_load_of_nothing_but_this_runs_answers_reports_itself_governed(tmp_path):
    """The other half of the fix: a clean run must not inherit the borrowed corpus's disclaimer."""
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    fresh = write_answers(tmp_path / "this-run", labels)
    book = ledger_at(tmp_path / "attempts.json",
                     ["header:%s" % Path(entry["frame"]).stem for entry in labels])

    mine = bh.saved_readings([fresh], own_run=(fresh, book))
    assert mine["provenance"]["approval"] == "governed"
    assert mine["provenance"]["attempts_governed_by_a_cap"] is True
    assert mine["provenance"]["answers_unmatched"] == 0

    # Same answers, no ledger named: the honest answer is that nothing is claimed for them.
    bare = bh.saved_readings([fresh])
    assert bare["provenance"]["approval"] == "ungoverned"
    assert bare["provenance"]["answers_with_no_record_of_their_spend"] == len(labels)


def test_the_command_binds_its_own_ledger_when_it_reads_back_its_own_band_store(tmp_path):
    """The call-site half. ``_combined_header`` used to append the run's store with no ledger at all."""
    from tekken_vod_helper import boundary_investigate as bi

    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    out = tmp_path / "run"
    write_answers(out / "header", labels)
    book = ledger_at(out / "attempts.json",
                     ["header:%s" % Path(entry["frame"]).stem for entry in labels])

    combined = bi._combined_header(None, out, own_ledger=book)
    assert combined["provenance"]["approval"] == "governed"
    assert combined["provenance"]["this_run"]["answers"] == len(labels)
    assert bi._combined_header(None, out)["provenance"]["approval"] == "ungoverned"


def test_the_runs_side_by_side_keep_each_answer_apart(tmp_path):
    """The fix: name the runs, and each answer carries its own origin instead of the folder's.

    Loading the same eleven answers as two directories reports the same mixed aggregate, but now the
    per-origin split is a fact in the record rather than something a sidecar asserts -- and the governed
    pair on its own still reports ``governed`` with the cap intact, so cleaning up the lie does not cost a
    clean run its credit.
    """
    labels = json.loads(STAGE_A_LABELS.read_text(encoding="utf-8"))["labels"]
    governed = write_answers(tmp_path / "governed", labels)
    book = ledger_at(tmp_path / "attempts.json", [entry["frame"] for entry in labels])

    together = bh.saved_readings([PRESERVED, governed], ledger=book)
    origins = together["provenance"]["origins"]
    assert together["provenance"]["approval"] == "mixed"
    assert [o["answers"] for o in origins] == [6, len(labels)]
    assert [o["answers_charged_to_the_named_ledger"] for o in origins] == [0, len(labels)]
    assert [o["incident_recorded"] for o in origins] == [True, False]
    assert together["provenance"]["incident_recorded"] is True
    for reading in together["readings"]:
        assert reading["charged_to_ledger"] == (reading["origin_directory"] == str(governed))

    clean = bh.saved_readings([governed], ledger=book)
    assert clean["provenance"]["approval"] == "governed"
    assert clean["provenance"]["attempts_governed_by_a_cap"] is True
    assert clean["provenance"]["incident_recorded"] is False

    historic = bh.saved_readings(PRESERVED)
    assert historic["provenance"]["approval"] == "ungoverned"
    assert historic["provenance"]["attempts_governed_by_a_cap"] is False
    assert historic["provenance"]["incident_recorded"] is True
