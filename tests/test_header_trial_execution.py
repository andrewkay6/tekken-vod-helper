"""The public header command has to work with the real client, so these tests use it.

``HeaderReadClient`` plus unit tests is not an executor: the six-request trial was advertised for a command
that never accepted the header arm. So the seam here is ``_post`` and nothing else, exactly as the pip
probe's tests do it. Request serialisation, the prompt version, the retry loop, per-answer persistence,
reuse on resume, the shared ledger, the transport-uncertain halt and the prepare/execute/restart contract
all run for real. Only the endpoint is fake, and no test in this file sends anything.

Two more things are checked here because they are the review's actual findings:

* the runtime never sees an expected value -- the trial spec is refused if it carries one, and the public
  script reads no label file at all;
* a wrong or declined transcription does not stop the batch. A stop rule that consulted the labels would
  make the labels the run's referee instead of its measurement.
"""
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_header as bh
from tekken_vod_helper import boundary_media as media

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_header_trial.py"
EVALUATOR = ROOT / "scripts/evaluate_header_trial.py"
LABELS = ROOT / "build/work/bb9/header-trial-v1/labels-eval-frozen.json"
MANIFEST = "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
TRIAL = ROOT / "build/work/bb9/header-trial-v1/trial.json"

#: Two of the trial's six instants, for the tests that only need the mechanism to work.
TEST_INSTATS = [3247.243829, 3426.42282]


def write_spec(tmp_path, instants=TEST_INSTATS, **extra):
    spec = {"kind": bh.TRIAL_KIND, "version": "header-trial-test",
            "frame_manifest": MANIFEST, "radius_seconds": 0.5,
            "instants": [{"at": at, "question": "identity_mapping", "because": "test",
                          "selected_by": "fixed grid"} for at in instants]}
    spec.update(extra)
    path = tmp_path / "trial.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def run_script(monkeypatch, argv, transport=None, capsys=None, script=SCRIPT):
    """Invoke the public script's main() in-process, with only the transport replaced."""
    spec = importlib.util.spec_from_file_location(script.stem, str(script))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, script.stem, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(script)] + argv)
    if transport is not None:
        # A bare callable on the class is not a descriptor, so client._post(body) reaches __call__ with
        # the body and no stray self: the transport is the only thing faked here.
        monkeypatch.setattr(bh.HeaderReadClient, "_post", transport, raising=True)
    module.main()
    return module, (capsys.readouterr().out if capsys else "")


class FakeTransport:
    """Answers each band from a per-frame table keyed by the image the request actually names."""

    def __init__(self, answers, behaviour="truth"):
        self.answers = answers
        self.behaviour = behaviour
        self.bodies = []
        self.fail_after = None

    def __call__(self, body):
        self.bodies.append(body)
        if self.fail_after is not None and len(self.bodies) > self.fail_after:
            raise media.TransportUncertain("connection dropped after the request was sent")
        blob = json.dumps(body)
        asked = [int(m) for m in re.findall(r"item (\d+) image", blob)]
        payload = []
        for item in asked:
            # The listing line names the band, so the answer follows the pixels that were sent rather
            # than a timestamp that could name a neighbouring frame.
            named = next((name for name in self.answers if name in blob), None)
            if named is None:
                raise AssertionError("request named no known band: {}".format(blob[:200]))
            for side in ("LEFT", "RIGHT"):
                payload.append(self._entry(item, side, self.answers[named]))
        return {"choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 130,
                          "completion_tokens_details": {"reasoning_tokens": 40}}}

    def _entry(self, item, side, answer):
        left = side == "LEFT"
        if self.behaviour == "unreadable":
            # A drawn value the model refuses to name: valid, and it must not be an error the run retries.
            return {"item": item, "side": side, "player": answer["player" + side],
                    "games": None, "games_drawn": True, "pips": None, "pips_drawn": True,
                    "label": answer["label"], "confidence": 0.6}
        return {"item": item, "side": side, "player": answer["player" + side],
                "games": answer["games" + side], "games_drawn": answer["games" + side] is not None,
                "pips": answer.get("pips" + side),
                "pips_drawn": answer.get("pips" + side) is not None,
                "label": answer["label"], "confidence": 0.9}


@pytest.fixture()
def answers():
    return {"frame-3245-band.png": {"playerLEFT": "PWDDA | Ariz", "playerRIGHT": "Buk",
                                    "gamesLEFT": 0, "gamesRIGHT": 0, "label": "LOSERS QUARTER-FINAL"},
            "frame-3424-band.png": {"playerLEFT": "Buk", "playerRIGHT": "PWDDA | Ariz",
                                    "gamesLEFT": 0, "gamesRIGHT": 1, "label": "LOSERS QUARTER-FINAL",
                                    "pipsLEFT": 1, "pipsRIGHT": 0}}


def prepared_args(tmp_path, spec, **over):
    # A ledger is always supplied, because --execute now refuses without one: an attempt nothing counts
    # is an attempt nobody approved.
    argv = ["--trial", str(spec), "--output-dir", str(tmp_path / "run"),
            "--model", over.get("model", "test-model"),
            "--endpoint", over.get("endpoint", "http://endpoint.test"),
            "--scale", str(over.get("scale", 3)),
            "--budget-file", str(over.get("budget_file", tmp_path / "attempts.json")),
            "--budget-cap", str(over.get("cap", 6)),
            "--budget-minutes", str(over.get("minutes", 15))]
    return argv


def test_execute_refuses_to_spend_attempts_that_no_ledger_counts(tmp_path, answers, monkeypatch,
                                                                 capsys):
    """The command once sent six real requests with no cap and no deadline in force. It cannot now."""
    spec = write_spec(tmp_path)
    transport = FakeTransport(answers)
    argv = ["--trial", str(spec), "--output-dir", str(tmp_path / "run"),
            "--model", "test-model", "--endpoint", "http://endpoint.test", "--prepare"]
    run_script(monkeypatch, argv, capsys=capsys)
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv[:-1] + ["--execute"], transport=transport, capsys=None)
    assert "--execute requires --budget-file" in str(refused.value)
    assert transport.bodies == [], "requests went out anyway"
    assert not (tmp_path / "run" / "groups").exists()


# ---------------------------------------------------------------- prepare: offline, and reversible


def test_prepare_renders_every_band_and_sends_nothing(tmp_path, monkeypatch, capsys):
    spec = write_spec(tmp_path)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    report = json.loads(out)
    assert report["step"] == "prepare" and report["executed"] is False
    assert report["requests_planned"] == 2 and report["images_total"] == 2
    assert report["completion_tokens_ceiling"] == 2 * bh.HeaderReadClient.request_max_tokens
    assert (tmp_path / "run" / "prepared.json").is_file()
    plan = json.loads((tmp_path / "run" / "plan-header.json").read_text())
    assert plan["inference_sent"] is False
    # The plan states its own limits, so a reader of the file alone cannot mistake six transcriptions
    # for a recovered set, or treat a disagreement with a label as a reason to halt.
    assert plan["never_stops_when"] and len(plan["what_this_cannot_show"]) == 3
    assert plan["completion_tokens_per_request"] == bh.HeaderReadClient.request_max_tokens
    assert "transport_uncertain_stops_without_retrying" in plan["stops_when"]
    assert [row["at"] for row in report["instants"]] == TEST_INSTATS
    for row in plan["requests"]:
        assert Path(row["image"]).is_file(), "the plan promised a crop it never rendered"
        assert row["frame_id"] is not None and row["frame_timestamp"] is not None
        assert row["offset_seconds"] <= 0.5, "a band must be read from the frame it labels"
        # An answer with no question attached to it is indistinguishable from a fishing expedition, and
        # the field was silently dropped here once.
        assert row["because"] == "test" and row["question"] == "identity_mapping"
        assert row["selected_by"] == "fixed grid"
    # Prepare leaves the directory executable rather than poisoning it.
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)


def test_a_frame_further_away_than_the_radius_is_a_refusal_not_a_substitute(tmp_path, monkeypatch,
                                                                           capsys):
    spec = write_spec(tmp_path, instants=[99999.0])
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=None)
    assert "no cached frame within" in str(refused.value)


def test_a_short_allowance_must_not_quietly_drop_instants(tmp_path, monkeypatch, capsys):
    """Two prepared bands and a --max-requests of one is a different experiment, not a smaller one."""
    spec = write_spec(tmp_path)
    argv = prepared_args(tmp_path, spec) + ["--max-requests", "1", "--prepare"]
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv, capsys=None)
    assert "silently drop" in str(refused.value)


def test_a_plan_wider_than_the_ledger_cap_is_refused_before_anything_is_spent(tmp_path, monkeypatch,
                                                                             capsys):
    spec = write_spec(tmp_path)
    ledger = tmp_path / "attempts.json"
    argv = prepared_args(tmp_path, spec, budget_file=ledger, cap=1) + ["--execute"]
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv, capsys=None)
    assert "ledger cap is 1" in str(refused.value)
    assert not ledger.exists(), "a refused command must not have opened the budget"


# ---------------------------------------------------------------- execute: the real client, for real


def test_execute_answers_every_band_and_persists_each_answer(tmp_path, answers, monkeypatch, capsys):
    spec = write_spec(tmp_path)
    transport = FakeTransport(answers)
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"],
                        transport=transport, capsys=capsys)
    report = json.loads(out)
    assert report["step"] == "execute" and report["executed"] is True
    assert report["run"]["requests_sent"] == 2
    assert report["run"]["groups_answered"] == 2 and report["run"]["groups_failed"] == 0
    assert report["run"]["status"] == "complete" and report["run"]["stopped_reason"] is None
    assert report["tokens"]["completion"] == 2 * 130
    groups = sorted((tmp_path / "run" / "groups").glob(bh.group_filename()))
    assert len(groups) == 2, "one answer per band, written before the next request"
    stored = json.loads(groups[0].read_text())
    assert stored["pip_evidence"]["may_replace_validated_pip_evidence"] is False
    assert stored["images"][0]["sha256"], "the group has to name the pixels it answered"
    report_file = json.loads((tmp_path / "run" / "report-header.json").read_text())
    assert len(report_file["pairings"]) == 2
    assert report_file["evidence_class"] == "unvalidated_header_diagnostic"
    assert any("set boundary" in claim for claim in report_file["cannot_claim"]), \
        "the report must say what six transcriptions cannot establish"


def test_a_resumed_run_asks_nothing_of_the_endpoint(tmp_path, answers, monkeypatch, capsys):
    spec = write_spec(tmp_path)
    transport = FakeTransport(answers)
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"], transport=transport,
               capsys=capsys)
    assert len(transport.bodies) == 2
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"],
                        transport=transport, capsys=capsys)
    assert len(transport.bodies) == 2, "a resumed run re-asked a band it already had"
    report = json.loads(out)
    assert report["groups_resumed"] == 2 and report["run"]["requests_sent"] == 0
    assert report["run"]["groups_answered"] == 2


def test_declining_to_guess_a_number_is_an_answer_and_does_not_stop_the_run(tmp_path, answers,
                                                                           monkeypatch, capsys):
    """Item 2 of the review, tested where it would otherwise bite: a batch that is refused its shrug.

    The answers here are all ``games: null`` with ``games_drawn: true``. If validation treated that as a
    bad reply, the group would be filed as failed and the run's only output would be retries; if a stop
    rule consulted the expected tally, the batch would halt on the first honest abstention.
    """
    spec = write_spec(tmp_path)
    transport = FakeTransport(answers, behaviour="unreadable")
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"],
                        transport=transport, capsys=capsys)
    report = json.loads(out)
    assert report["run"]["status"] == "complete" and report["run"]["groups_failed"] == 0
    assert report["run"]["sides_declining_a_value"] == 4, "four sides, all drawn, all declined"
    pairings = json.loads((tmp_path / "run" / "report-header.json").read_text())["pairings"]
    states = {p["pairing"]["left"]["games_state"] for p in pairings}
    assert states == {"unreadable"}
    assert all(p["pairing"]["tally"] == [None, None] for p in pairings)


def test_a_wrong_reading_is_reported_and_the_batch_continues(tmp_path, answers, monkeypatch, capsys):
    """No stop rule reads a label. A bad transcription is a result, with consequences, not a halt."""
    spec = write_spec(tmp_path)
    wrong = {name: dict(value, playerLEFT="Someone Else") for name, value in answers.items()}
    transport = FakeTransport(wrong)
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"],
                        transport=transport, capsys=capsys)
    report = json.loads(out)
    assert report["run"]["groups_answered"] == 2, "the run stopped because the model disagreed with truth"
    assert report["run"]["stopped_reason"] is None
    readouts = json.loads((tmp_path / "run" / "report-header.json").read_text())["pairings"]
    assert [p["pairing"]["left"]["player"] for p in readouts] == ["Someone Else", "Someone Else"], \
        "a bad read has to be visible in the record, not smoothed away"


def test_the_runtime_command_never_names_the_label_file():
    """Independence checked on the code, because a comment saying "we do not read it" is not evidence.

    Docstrings aside, no string the interpreter can see in the executor may point at the evaluator's
    labels: the moment the request builder can reach the answers, the trial measures nothing.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    body = [node for node in tree.body if not (isinstance(node, ast.Expr)
                                               and isinstance(node.value, ast.Constant))]
    strings = [node.value for node in ast.walk(ast.Module(body=body, type_ignores=()))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert strings, "the executor has no string literals at all?"
    assert not [s for s in strings if "labels-eval" in s or "set-trial" in s], \
        "the runtime command reaches an evaluator label file"


def test_an_uncertain_transport_stops_rather_than_re_asking(tmp_path, answers, monkeypatch, capsys):
    spec = write_spec(tmp_path)
    transport = FakeTransport(answers)
    transport.fail_after = 1
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"],
                        transport=transport, capsys=capsys)
    report = json.loads(out)
    assert len(transport.bodies) == 2, "a third request went out after the transport went uncertain"
    assert report["run"]["stopped_reason"] == "transport_uncertain"
    assert report["run"]["requests_sent"] == 2
    assert report["run"]["not_attempted"] == 0
    assert (tmp_path / "run" / "result-header.json").is_file(), "the answers earned before the halt stay"


# ---------------------------------------------------------------- the manifest is a promise


def test_execute_refuses_a_directory_that_was_never_prepared(tmp_path, monkeypatch, capsys):
    spec = write_spec(tmp_path)
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--execute"], capsys=None)
    assert "no prepared run" in str(refused.value)


@pytest.mark.parametrize("change", ["model", "endpoint", "scale"])
def test_execute_refuses_inputs_that_moved_after_prepare(tmp_path, answers, monkeypatch, capsys,
                                                         change):
    spec = write_spec(tmp_path)
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    moved = {"model": "another-model", "endpoint": "http://elsewhere.test", "scale": 4}[change]
    argv = prepared_args(tmp_path, spec, **{change: moved}) + ["--execute"]
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv, capsys=None)
    assert "{} changed since prepare".format(change) in str(refused.value)


def test_execute_refuses_a_trial_spec_that_changed_under_the_plan(tmp_path, answers, monkeypatch,
                                                                  capsys):
    spec = write_spec(tmp_path)
    run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    widened = write_spec(tmp_path, instants=TEST_INSTATS + [3606.60281])
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, prepared_args(tmp_path, widened) + ["--execute"], capsys=None)
    assert "instants, frames or rendered band images changed" in str(refused.value)


def test_restart_discards_answers_so_the_allowance_can_be_spent_again(tmp_path, answers, monkeypatch,
                                                                      capsys):
    spec = write_spec(tmp_path)
    transport = FakeTransport(answers)
    argv = prepared_args(tmp_path, spec)
    run_script(monkeypatch, argv + ["--prepare"], capsys=capsys)
    run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=capsys)
    assert len(list((tmp_path / "run" / "groups").glob(bh.group_filename()))) == 2
    _, out = run_script(monkeypatch, argv + ["--restart"], capsys=capsys)
    report = json.loads(out)
    assert report["step"] == "restart" and report["remaining_answers"] == []
    assert (tmp_path / "run" / "prepared.json").is_file(), "restart clears answers, not the frozen plan"
    run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=capsys)
    assert len(transport.bodies) == 4, "restart did not actually make the allowance spendable again"


# ---------------------------------------------------------------- the shared ledger governs spends


def test_the_ledger_is_one_cap_and_one_deadline_across_every_command(tmp_path, answers, monkeypatch,
                                                                     capsys):
    spec = write_spec(tmp_path)
    ledger = tmp_path / "attempts.json"
    argv = prepared_args(tmp_path, spec, budget_file=ledger, cap=4, minutes=15)
    transport = FakeTransport(answers)
    run_script(monkeypatch, argv + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=capsys)
    first = json.loads(out)
    assert first["budget"]["attempts_charged"] == 2 and first["budget"]["attempts_remaining"] == 2
    assert first["budget"]["deadline_at"], "the deadline is stamped at the first send"
    # A resume inherits the same deadline and whatever attempts still stand: a second command cannot
    # start a second clock or a second allowance.
    run_script(monkeypatch, argv + ["--restart"], capsys=capsys)
    _, out = run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=capsys)
    second = json.loads(out)
    assert second["budget"]["deadline_at"] == first["budget"]["deadline_at"]
    assert second["budget"]["attempts_charged"] == 4
    assert second["budget"]["attempts_remaining"] == 0
    assert len(transport.bodies) == 4


def test_a_resume_owing_more_than_the_ledger_has_left_refuses_to_start(tmp_path, answers, monkeypatch,
                                                                      capsys):
    """Asking for two bands with one attempt standing is a different run, and says so before spending."""
    spec = write_spec(tmp_path)
    ledger = tmp_path / "attempts.json"
    argv = prepared_args(tmp_path, spec, budget_file=ledger, cap=3, minutes=15)
    transport = FakeTransport(answers)
    run_script(monkeypatch, argv + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=capsys)
    assert json.loads(out)["budget"]["attempts_remaining"] == 1
    run_script(monkeypatch, argv + ["--restart"], capsys=capsys)
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=None)
    assert "still owed" in str(refused.value)
    assert len(transport.bodies) == 2, "the refusal sent something anyway"
    assert not list((tmp_path / "run" / "groups").glob(bh.group_filename())), \
        "a refused command must not have answered anything"


def test_the_endpoint_is_never_contacted_without_an_explicit_execute(tmp_path, answers, monkeypatch,
                                                                     capsys):
    """The prepare path is the one an operator runs first, so it must be unable to spend anything."""
    spec = write_spec(tmp_path)
    monkeypatch.setattr(media.VisionClient, "_post",
                        lambda body: pytest.fail("prepare reached the network"), raising=True)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, spec) + ["--prepare"], capsys=capsys)
    assert json.loads(out)["executed"] is False


# ---------------------------------------------------------------- the spec cannot smuggle truth


def test_a_trial_spec_carrying_expected_values_is_refused(tmp_path, monkeypatch, capsys):
    """Six transcriptions are only a measurement if the request builder never saw the answers."""
    spec = tmp_path / "cheating.json"
    spec.write_text(json.dumps({"kind": bh.TRIAL_KIND, "version": "x", "frame_manifest": MANIFEST,
                                "instants": [{"at": TEST_INSTATS[0], "expected": {"left": "Buk"}}]}),
                    encoding="utf-8")
    with pytest.raises(bh.HeaderError, match="carries evaluator truth"):
        bh.load_trial(spec)


def test_the_labels_are_frozen_and_describe_the_exact_crops_that_are_sent():
    """A label is a claim about the bytes that leave the machine, or it is not a control.

    The interval's first label sheet was read off 2560x196 master bands while the command sends a 3x
    upscale of a 640-wide cache band: different pixels, and a mismatch would have been unattributable.
    """
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    assert labels["frozen"] is True and labels["annotations_for"] is None
    plan = json.loads((ROOT / "build/work/bb9/header-trial-v1/run/plan-header.json").read_text())
    sent = {round(float(r["at"]), 6): r["image_sha256"] for r in plan["requests"]}
    assert len(labels["labels"]) == len(sent) == 6
    for entry in labels["labels"]:
        assert entry["band_sha256"] == sent[round(float(entry["at"]), 6)], entry["at"]
        assert Path(entry["band"]).is_file(), "the labelled crop must still be the one on disk"
    assert labels["pip_readings_are_not_scored"] is True


def test_evaluate_reports_a_matching_run_as_extraction_not_set_recovery(tmp_path, monkeypatch, capsys):
    """The scope line has to be printed by the scorer, not only promised by the plan."""
    spec = importlib.util.spec_from_file_location("header_eval", str(EVALUATOR))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "header_eval", module)
    spec.loader.exec_module(module)
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    first, swap = labels["labels"][0], labels["labels"][1]

    def pairing(names, games):
        def side(index):
            return {"player": names[index], "games": games[index],
                    "games_state": "read", "confidence": 0.9, "usable": True}
        return {"pairing": {"left": side(0), "right": side(1), "label": None, "mapping_usable": True,
                            "pips_unvalidated": {"left": None, "right": None, "left_state": "unknown",
                                                 "right_state": "unknown"},
                            "pip_evidence": dict(bh.UNVALIDATED_PIP_PROVENANCE)}}

    answers = [dict(pairing([first["names"]["left"], first["names"]["right"]],
                            [first["games_won"]["left"], first["games_won"]["right"]]),
                    at=first["at"], image_sha256=first["band_sha256"]),
               dict(pairing([swap["names"]["left"], swap["names"]["right"]],
                            [swap["games_won"]["left"], swap["games_won"]["right"]]),
                    at=swap["at"], image_sha256=swap["band_sha256"])]
    report = {"version": bh.VERSION, "model": "test-model", "run": {"status": "complete"},
              "pairings": answers}
    graded = module.evaluate(labels, report)
    assert graded["instants"]["matched"] == 2
    assert graded["complete"] is False, "four of six labelled instants went unanswered"
    assert graded["instants"]["no_answer"] == 4
    assert graded["swap_checks"][0]["verdict"] == "swap_read", \
        "the side swap is the mapping claim; a run that reads both names and misses the exchange failed"
    # A name read on the wrong side is a mismatch, not a half-credit.
    swapped_side = json.loads(json.dumps(answers))
    swapped_side[1]["pairing"]["left"], swapped_side[1]["pairing"]["right"] = (
        swapped_side[1]["pairing"]["right"], swapped_side[1]["pairing"]["left"])
    report["pairings"] = [answers[0], swapped_side[1]]
    graded = module.evaluate(labels, report)
    assert graded["swap_checks"][0]["verdict"] == "swap_missed"
    assert graded["fields"]["mismatched"] == 4, graded["rows"][1]
