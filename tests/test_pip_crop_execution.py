"""The advertised command has to work with the real client, so these tests use it.

The first version of this probe was tested by handing ``persist_probe`` a fake client, which is exactly
how it shipped broken: the real ``classify()`` success path stamps its usage and provenance records with
``frame["id"]``, a key no pip record had, and the base class files the resulting ``KeyError`` in with a
malformed reply -- so every group would have failed while looking like the model's fault. Only the real
client reaches that line.

So the seam here is ``_post`` and nothing else. Request serialization, the arm's prompt version, the
retry loop, persistence, validation, scoring and the prepare/execute/restart contract all run for real,
against the frozen crop geometry and the frozen eight-frame annotations.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_media as media
from tekken_vod_helper import boundary_pips as bp

ROOT = Path(__file__).resolve().parents[1]
ANNOTATIONS = ROOT / "build/work/bb9/pip-crops-v1/annotations.json"
SCRIPT = ROOT / "scripts/run_pip_crop_probe.py"

# Frozen by the Codex review: the rendered boxes and this fixture are settled, and a test that re-derived
# either would quietly bless a geometry change that invalidates every answer already on disk.
FROZEN_BOXES = {"LEFT": [214, 11, 302, 33], "RIGHT": [344, 11, 424, 33]}
FROZEN_FRAMES = [55.05, 100.1, 124.12, 129.13, 174.17, 237.24, 249.25, 251.25]


@pytest.fixture(scope="module")
def annotations():
    return json.loads(ANNOTATIONS.read_text(encoding="utf-8"))


def run_script(monkeypatch, argv, transport=None, capsys=None):
    """Invoke the public script's main() in-process, with only the transport replaced."""
    spec = importlib.util.spec_from_file_location("pip_probe_script", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "pip_probe_script", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + argv)
    if transport is not None:
        # A bare callable stored on the class is not a descriptor, so client._post(body) reaches
        # __call__ with the body and no stray self: the transport is the only thing faked here.
        monkeypatch.setattr(bp.PipReadClient, "_post", transport, raising=True)
    module.main()
    return module, (capsys.readouterr().out if capsys else "")


class FakeTransport:
    """Answers exactly as asked, from the frozen truth, and records what left the machine."""

    def __init__(self, annotations, behaviour="truth"):
        self.by_path = {}
        for frame in annotations["frames"]:
            answer = {"drawn": frame["row_drawn"], "LEFT": frame["pips"]["LEFT"],
                      "RIGHT": frame["pips"]["RIGHT"]}
            self.by_path[frame["frame"]] = answer
        self.behaviour = behaviour
        self.bodies = []
        # Set to N to make the (N+1)th request leave and never come back: the one case where sending
        # anything else is the expensive mistake.
        self.fail_after = None

    def register_crops(self, records):
        for record in records:
            for label, crop in record["crops"].items():
                self.by_path[crop["path"]] = {"drawn": record["row_drawn"],
                                               "LEFT": record["expected"]["LEFT"],
                                               "RIGHT": record["expected"]["RIGHT"]}

    def __call__(self, body):
        self.bodies.append(body)
        if self.fail_after is not None and len(self.bodies) > self.fail_after:
            # Recorded before raising on purpose: the request did leave this machine, which is exactly
            # why the run cannot simply continue as if nothing had happened.
            raise media.TransportUncertain("connection dropped after the request was sent")
        blob = json.dumps(body)
        asked = [(int(m[0]), m[1]) for m in re.findall(r"item (\d+) label=(\w+)", blob)]
        answer = next((v for path, v in self.by_path.items() if path in blob), None)
        if answer is None:
            raise AssertionError("request named no known frame or crop")
        payload = []
        for item, label in asked:
            for side in (list(bp.LABELS) if label == "FRAME" else [label]):
                drawn, lit = self._values(answer, side)
                payload.append({"item": item, "label": side, "drawn": drawn, "lit": lit,
                                 "positions": [] if lit in (None, 0) else list(range(1, lit + 1)),
                                 "confidence": 0.9})
        return {"choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 1423, "completion_tokens": 1038,
                           "completion_tokens_details": {"reasoning_tokens": 600}}}

    def _values(self, answer, side):
        if self.behaviour == "zeros":
            # The first pilot's failure shape: a scoreline for every frame, drawn or not.
            return True, 0
        if self.behaviour == "unreadable":
            return None, None
        return answer["drawn"], answer[side]


def ledger_args(tmp_path, cap=64, minutes=15):
    """A real temporary ledger, because --execute now refuses to spend attempts nothing counts.

    Every execute in this file runs against a fresh file in its own tmp_path: the guard is the point of
    one test, and everywhere else the ledger has to be present or the command under test would only ever
    exercise its own refusal.
    """
    return ["--budget-file", str(Path(tmp_path) / "attempts.json"), "--budget-cap", str(cap),
            "--budget-minutes", str(minutes)]


def prepared_args(tmp_path, arms="crops,full", cap=64):
    return ["--annotations", str(ANNOTATIONS), "--output-dir", str(tmp_path),
            "--arms", arms, "--model", "test-model", "--endpoint", "http://endpoint.test"] + \
        ledger_args(tmp_path, cap=cap)


def test_frozen_geometry_and_fixture_are_unchanged():
    """A reviewer should be able to fail this test, which is what makes the freeze worth having."""
    assert {label: [int(v) for v in box] for label, box in bp.crop_rectangles().items()} == FROZEN_BOXES
    assert [round(f["timestamp"], 3) for f in
            json.loads(ANNOTATIONS.read_text(encoding="utf-8"))["frames"]] == FROZEN_FRAMES


def test_the_agreement_controls_are_derived_not_asserted(annotations):
    """The coverage block once claimed three agreement frames when the per-frame flags said two.

    124.12s was listed as a frame the full-frame run got right while its own expected value (1-0) and
    recorded answer (0-0) disagree. A fixture that overstates how often the model was already correct
    makes the control arm look better than it was, so the summary list is derived here from the same
    fields the scorer uses.
    """
    def agrees(frame):
        said, expected = frame["full_frame_run_said"], frame["pips"]
        return bool(frame["row_drawn"]) and all(said.get(s) == expected.get(s) for s in bp.LABELS)

    derived = sorted(f["timestamp"] for f in annotations["frames"] if agrees(f))
    assert derived == sorted(annotations["coverage"]["model_agreement_cases"])
    for frame in annotations["frames"]:
        assert frame["agreement"] is agrees(frame), frame["timestamp"]
    assert derived, "a fixture with no agreement frames cannot show the control arm doing well either"


def test_kept_frames_state_their_class_and_exclusions_are_legibility_calls(annotations):
    """Exclusions must be justified by legibility, never by the score they would have produced."""
    ambiguous = " ".join(annotations["provenance"]["excluded_as_ambiguous"])
    assert "95.09" in ambiguous and "235.23" in ambiguous, "impact-flash frames stay excluded"
    assert "row is not drawn" in ambiguous or "not drawn" in ambiguous
    for frame in annotations["frames"]:
        assert frame["legibility"], "every kept frame states why it is legible"
        assert frame["case"], "every kept frame states what class it probes"


# ---------- blocker 1: the real client has to be constructible ----------

def test_the_default_client_constructs_for_each_arm_and_versions_its_prompt():
    """persist_probe builds this client itself; if construction failed, nothing ever ran."""
    crops = bp.PipReadClient(arm="crops", endpoint="http://endpoint.test", model="m")
    full = bp.PipReadClient(arm="full", endpoint="http://endpoint.test", model="m")
    assert crops.arm == "crops" and full.arm == "full"
    assert crops.prompt_version != full.prompt_version
    assert crops.model == "m" and crops.request_max_tokens == 3000
    # An arm installed after the base class runs could be recorded inconsistently by anything it
    # snapshots at construction, so the prompt identity has to already be right here.
    assert crops._versioned_prompt() == bp.CROP_VERSION


def test_an_unknown_arm_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown arm"):
        bp.PipReadClient(arm="sideways", endpoint="http://endpoint.test", model="m")


def test_records_carry_the_id_the_inherited_client_stamps_usage_with(tmp_path):
    """Without ``id`` the real path raised KeyError inside the retry handler: silent, total failure."""
    records = bp.build_records(json.loads(ANNOTATIONS.read_text(encoding="utf-8"))["frames"][:1],
                               tmp_path / "crops")
    assert records[0]["id"] == "frame-0056@55.05s"
    for key in ("id", "frame", "timestamp", "source_sha256", "crops"):
        assert key in records[0], key


# ---------- blocker 2: the public prepare/execute/restart path ----------

def test_prepare_then_execute_runs_the_real_client_and_scores_every_frame(tmp_path, annotations,
                                                                          monkeypatch, capsys):
    transport = FakeTransport(annotations)
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)

    run_script(monkeypatch, prepared_args(tmp_path) + ["--prepare"], capsys=capsys)
    assert (tmp_path / "prepared.json").is_file()
    assert (tmp_path / "plan-crops.json").is_file() and (tmp_path / "plan-full.json").is_file()
    # The dry run must leave the directory executable rather than poisoning it.
    assert json.loads((tmp_path / "plan-crops.json").read_text())["requests_planned"] == 8

    _, out = run_script(monkeypatch, prepared_args(tmp_path) + ["--execute"], transport=transport,
                        capsys=capsys)
    report = json.loads(out)
    assert report["step"] == "execute"
    for arm in ("crops", "full"):
        arm_report = report["arms"][arm]
        assert arm_report["requests_sent"] == 8, arm
        assert arm_report["groups_answered"] == 8, arm_report["errors"]
        assert arm_report["groups_failed"] == 0, arm_report["errors"]
        assert arm_report["score"]["frames_correct"] == 8, arm_report["per_frame"]
        assert arm_report["tokens"]["completion"] == 8 * 1038, arm
    assert (tmp_path / "comparison.json").is_file()
    assert report["completion_tokens_ceiling"] == 48000


def test_the_crops_arm_sends_two_images_and_never_the_frame(annotations, monkeypatch, tmp_path):
    transport = FakeTransport(annotations)
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)
    for arm, images in (("crops", 2), ("full", 1)):
        client = bp.PipReadClient(arm=arm, endpoint="http://endpoint.test", model="m")
        monkeypatch.setattr(client, "_post", transport)
        client.classify([records[0]])
        body = transport.bodies[-1]
        sent = [part for part in body["messages"][0]["content"] if part["type"] == "image_url"]
        assert len(sent) == images, arm
        blob = json.dumps(body)
        if arm == "crops":
            assert records[0]["frame"] not in blob, "the crops arm leaked the whole frame in"
            assert all(".png" in part["image_url"]["url"] or "data:image/png" in part["image_url"]["url"]
                       for part in sent)
        assert body["max_tokens"] == 3000 and body["model"] == "m"
        asked = body["messages"][0]["content"][0]["text"]
        # The shared question goes out verbatim in both arms; only the framing line differs. That is the
        # whole control, so it is asserted rather than assumed from the version string.
        assert bp.PIP_QUESTION in asked, arm
        assert bp.question(arm).splitlines()[0] in asked, arm
        assert bp.question("full" if arm == "crops" else "crops").splitlines()[0] not in asked, arm


def test_execute_refuses_a_directory_that_was_never_prepared(tmp_path, annotations, monkeypatch,
                                                             capsys):
    """The bug this replaces: a dry run occupied the directory and execution then refused it."""
    module, _ = run_script(monkeypatch, prepared_args(tmp_path) + ["--prepare"], capsys=capsys)
    other = tmp_path / "elsewhere"
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, prepared_args(other) + ["--execute"], capsys=None)
    assert "no prepared run" in str(refused.value)


def test_execute_refuses_to_spend_attempts_that_no_ledger_counts(tmp_path, annotations, monkeypatch,
                                                                capsys):
    """--execute without --budget-file used to send everything and record nothing.

    ``--max-requests`` is per-process, so each un-ledgered invocation re-armed the allowance and no file
    showed what was spent. The header command burned six real requests that way while being tested for
    exactly this, so the pip executor refuses before it can.
    """
    argv = ["--annotations", str(ANNOTATIONS), "--output-dir", str(tmp_path), "--arms", "crops",
            "--model", "test-model", "--endpoint", "http://endpoint.test", "--execute"]
    transport = FakeTransport(annotations)
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv, transport=transport, capsys=None)
    assert "--execute requires --budget-file" in str(refused.value)
    assert transport.bodies == [], "requests went out anyway"
    assert not list(Path(tmp_path).rglob(bp.group_filename("crops"))) \
        and not list(Path(tmp_path).rglob(bp.group_filename("full"))), "a refused run wrote answers"


def test_prepare_needs_no_ledger_because_it_spends_nothing(tmp_path, annotations, monkeypatch, capsys):
    """The guard is about attempts, so it must not make the free step require a budget file too."""
    argv = ["--annotations", str(ANNOTATIONS), "--output-dir", str(tmp_path), "--arms", "crops",
            "--model", "test-model", "--endpoint", "http://endpoint.test", "--prepare"]
    _, out = run_script(monkeypatch, argv, capsys=capsys)
    assert json.loads(out)["step"] == "prepare"
    assert not (tmp_path / "attempts.json").exists()


def test_execute_refuses_inputs_that_moved_after_prepare(tmp_path, annotations, monkeypatch, capsys):
    run_script(monkeypatch, prepared_args(tmp_path) + ["--prepare"], capsys=capsys)
    moved = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    # Relative to the file's own version: hardcoding a literal here stopped being a change the moment
    # the fixture was revised to that value, which is a silent-pass bug in a guard test.
    moved["version"] = "{}-mutated".format(moved["version"])
    moved["frames"][0]["pips"]["LEFT"] = 2
    rewritten = tmp_path / "moved.json"
    rewritten.write_text(json.dumps(moved), encoding="utf-8")
    argv = ["--annotations", str(rewritten), "--output-dir", str(tmp_path), "--arms", "crops,full",
            "--model", "test-model", "--endpoint", "http://endpoint.test"] + ledger_args(tmp_path)
    argv = argv + ["--execute"]
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv, capsys=None)
    message = str(refused.value)
    assert "annotation_version changed" in message and "expected values changed" in message


def test_execute_refuses_a_different_model_after_prepare(tmp_path, annotations, monkeypatch, capsys):
    run_script(monkeypatch, prepared_args(tmp_path) + ["--prepare"], capsys=capsys)
    argv = prepared_args(tmp_path, arms="crops,full") + ["--model", "another-model", "--execute"]
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, argv, capsys=None)
    assert "model changed" in str(refused.value)


def test_an_interrupted_run_resumes_by_asking_nothing(tmp_path, annotations, monkeypatch, capsys):
    """Resumability is the reason the manifest exists, so it is tested through the public command."""
    transport = FakeTransport(annotations)
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)
    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--prepare"], capsys=capsys)
    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--execute"],
               transport=transport, capsys=capsys)
    assert len(transport.bodies) == 8
    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--execute"],
               transport=transport, capsys=capsys)
    assert len(transport.bodies) == 8, "a resumed run re-asked a group it already had"
    report = json.loads((tmp_path / "result-crops.json").read_text())
    assert report["groups_reused"] == 8 and report["requests_sent"] == 0


def test_restart_discards_answers_so_the_allowance_can_be_spent_again(tmp_path, annotations,
                                                                      monkeypatch, capsys):
    transport = FakeTransport(annotations)
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)
    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--prepare"], capsys=capsys)
    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--execute"],
               transport=transport, capsys=capsys)
    groups = list((tmp_path / "crops" / "groups").glob(bp.group_filename("crops")))
    assert len(groups) == 8

    _, out = run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--restart"],
                        capsys=capsys)
    report = json.loads(out)
    assert report["step"] == "restart" and report["remaining_answers"] == []
    assert not list((tmp_path / "crops" / "groups").glob(bp.group_filename("crops")))
    assert (tmp_path / "prepared.json").is_file(), "restart clears answers, not the frozen plan"

    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--execute"],
               transport=transport, capsys=capsys)
    assert len(transport.bodies) == 16, "restart did not actually re-reserve the allowance"


# ---------- blocker 3: an absent row earns nothing without claiming absence ----------

def score_absent(drawn, lit):
    observations = [{"timestamp": 249.25, "label": side, "drawn": drawn, "lit": lit}
                    for side in bp.LABELS]
    return bp.score_readings(observations, [{"timestamp": 249.25, "frame": "f.jpg",
                                              "row_drawn": False,
                                              "pips": {"LEFT": None, "RIGHT": None}}])


def test_claiming_the_row_is_present_earns_no_credit_on_an_absent_row():
    """The reported bug: drawn=True, lit=null scored as a correct abstention."""
    result = score_absent(True, None)
    assert result["error_kinds"].get("row_invented") == 2
    assert "correctly_absent" not in result["error_kinds"]
    assert result["frames_correct"] == 0


def test_saying_nothing_about_row_presence_is_not_an_abstention_either():
    result = score_absent(None, None)
    assert result["error_kinds"].get("row_presence_unknown") == 2
    assert result["frames_correct"] == 0


def test_only_an_explicit_absence_is_credit():
    result = score_absent(False, None)
    assert result["error_kinds"].get("correctly_absent") == 2
    assert result["frames_correct"] == 1


def test_a_count_on_an_absent_row_outweighs_any_claim_about_presence():
    result = score_absent(False, 1)
    assert result["error_kinds"].get("pip_invented_on_undrawn_row") == 2
    assert result["frames_correct"] == 0


def test_both_sides_are_checked_before_a_frame_counts_as_correct():
    """One clean side must not carry a frame whose other side invented a row."""
    observations = [{"timestamp": 249.25, "label": "LEFT", "drawn": False, "lit": None},
                    {"timestamp": 249.25, "label": "RIGHT", "drawn": True, "lit": None}]
    result = bp.score_readings(observations, [{"timestamp": 249.25, "frame": "f.jpg",
                                                "row_drawn": False,
                                                "pips": {"LEFT": None, "RIGHT": None}}])
    assert result["frames_correct"] == 0
    assert len(result["per_frame"][0]["errors"]) == 2


# ---------- the failure shape the probe was built to catch ----------

def test_a_model_answering_zero_everywhere_scores_as_the_pilot_failed(tmp_path, annotations,
                                                                      monkeypatch, capsys):
    """The two agreement controls are what keep this honest: a constant-0 model passes exactly those.

    If the fixture were built only from failures, everything would score wrong and "we fixed the fixture"
    would be unfalsifiable. A model that answers a drawn row of zero pips for every frame still clears
    55.05s and 251.25s, because on those two it was right the first time.
    """
    transport = FakeTransport(annotations, behaviour="zeros")
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)
    run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, arms="crops") + ["--execute"],
                       transport=transport, capsys=capsys)
    arm_report = json.loads(out)["arms"]["crops"]
    kinds = arm_report["score"]["error_kinds"]
    assert [row["timestamp"] for row in arm_report["per_frame"] if row["correct"]] == [55.05, 251.25]
    assert arm_report["score"]["frames_correct"] == 2
    assert kinds.get("false_zero") == 6, "a real row read as empty"
    assert kinds.get("pip_invented_on_undrawn_row") == 4, "an absent row read as a scoreline"


def test_a_model_that_only_abstains_is_reported_as_lost_readings_not_wrong(tmp_path, annotations,
                                                                          monkeypatch, capsys):
    transport = FakeTransport(annotations, behaviour="unreadable")
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)
    run_script(monkeypatch, prepared_args(tmp_path, arms="full") + ["--prepare"], capsys=capsys)
    _, out = run_script(monkeypatch, prepared_args(tmp_path, arms="full") + ["--execute"],
                       transport=transport, capsys=capsys)
    arm_report = json.loads(out)["arms"]["full"]
    kinds = arm_report["score"]["error_kinds"]
    assert arm_report["score"]["frames_correct"] == 0
    # 12 sides on drawn rows are lost readings; 4 sides on the two undrawn rows refused even to say
    # whether a row was there, which is a different failure and is not credited as abstention.
    assert kinds.get("unreadable") == 12, kinds
    assert kinds.get("row_presence_unknown") == 4, kinds
    assert not kinds.get("false_zero") and not kinds.get("pip_invented_on_undrawn_row")


def test_comparison_reports_which_frames_changed_hands_not_just_a_total(annotations):
    crops = {"observations": [{"timestamp": 174.17, "label": "LEFT", "drawn": True, "lit": 1},
                               {"timestamp": 174.17, "label": "RIGHT", "drawn": True, "lit": 2}]}
    full = {"observations": [{"timestamp": 174.17, "label": "LEFT", "drawn": True, "lit": 2},
                              {"timestamp": 174.17, "label": "RIGHT", "drawn": True, "lit": 1}]}
    fixture = [f for f in annotations["frames"] if f["timestamp"] == 174.17]
    comparison = bp.compare_arms(crops, full, fixture)
    assert comparison["delta_frames_correct"] == 1
    assert comparison["frames_changed"] == 1
    row = comparison["paired"][0]
    assert row["crops"]["correct"] is True and row["full"]["correct"] is False
    assert row["full"]["errors"], "the side swap has to be named, not merely counted"
    assert comparison["does_not_establish"], "a framing win must not be allowed to mean resolution"


# ---------- one uncertain transport stops the whole comparison ----------

@pytest.fixture()
def armed(tmp_path, annotations, monkeypatch, capsys):
    """Prepares both arms and returns a transport whose requests can be counted."""
    transport = FakeTransport(annotations)
    transport.register_crops(bp.build_records(annotations["frames"], tmp_path / "crops"))
    run_script(monkeypatch, prepared_args(tmp_path) + ["--prepare"], capsys=capsys)
    return transport


def test_an_uncertain_transport_stops_the_other_arm_before_it_is_built(tmp_path, armed, monkeypatch,
                                                                       capsys):
    """Halting only the unlucky arm was the bug: the second arm would queue behind it.

    A request whose transport is uncertain may still be generating on the box. Sending the control arm
    then costs the first request's full latency and returns nothing comparable, so the run has to stop
    everywhere and say which arm never happened.
    """
    armed.fail_after = 2
    _, out = run_script(monkeypatch, prepared_args(tmp_path) + ["--execute"], transport=armed,
                        capsys=capsys)
    report = json.loads(out)
    assert len(armed.bodies) == 3, "requests were sent after the transport went uncertain"
    assert report["arms"]["crops"]["stopped_reason"] == "transport_uncertain"
    assert report["arms"]["crops"]["requests_sent"] == 3
    assert report["deferred_arms"] == ["full"]
    assert report["arms"]["full"]["status"] == "deferred"
    assert report["arms"]["full"]["requests_sent"] == 0
    assert report["arms"]["full"]["attempt_allowance_unspent"] == 8
    assert "transport_uncertain in arm crops" in report["halted"]
    assert not (tmp_path / "result-full.json").exists(), "a deferred arm must not look executed"
    assert not (tmp_path / "full").exists(), "the deferred arm was given a client and a directory"
    assert (tmp_path / "deferred-full.json").is_file()
    # The comparison is what a reader trusts, so it has to be absent rather than one-armed.
    assert not (tmp_path / "comparison.json").exists()
    assert report["comparison"] == "not run: full never executed"
    assert "verdict" not in report, "no verdict may be printed for a comparison that did not happen"
    assert len(armed.bodies) == 3, "reporting the halt sent more work"


def test_a_halt_earns_no_credit_and_leaves_the_answers_it_did_get(tmp_path, armed, monkeypatch,
                                                                  capsys):
    """Stopping is a reported result, not a clean frame count."""
    armed.fail_after = 2
    _, out = run_script(monkeypatch, prepared_args(tmp_path) + ["--execute"], transport=armed,
                        capsys=capsys)
    crops = json.loads((tmp_path / "score-crops.json").read_text())
    report = json.loads(out)["arms"]["crops"]
    assert crops["frames"] == 8, "every annotated frame stays in view, answered or not"
    assert crops["frames_correct"] == 2, "only the groups actually answered can score"
    assert report["not_attempted"] == 5
    assert report["groups_failed"] == 1, "the uncertain group is a failure, not a missing row"
    kinds = report["score"]["error_kinds"]
    # 6 of 8 frames have no answer at all: the 5 never attempted plus the one whose reply never came.
    assert kinds.get("no_answer") == 12, kinds


def test_the_deferred_arm_runs_when_the_endpoint_is_idle_again(tmp_path, armed, monkeypatch,
                                                               capsys):
    """Deferral has to be recoverable, or the fix would be a way to lose half an experiment."""
    armed.fail_after = 2
    run_script(monkeypatch, prepared_args(tmp_path) + ["--execute"], transport=armed, capsys=capsys)
    assert len(armed.bodies) == 3
    armed.fail_after = None
    _, out = run_script(monkeypatch, prepared_args(tmp_path) + ["--execute"], transport=armed,
                        capsys=capsys)
    report = json.loads(out)
    assert report["deferred_arms"] == [] and "halted" not in report
    # 3 already sent, 5 fresh crops groups, and the never-asked arm's 8.
    assert len(armed.bodies) == 16, "the resumed run re-asked a group it already had"
    assert report["arms"]["crops"]["groups_reused"] == 3, "2 clean + the uncertain one, neither re-asked"
    assert report["arms"]["crops"]["groups_answered"] == 7
    assert report["arms"]["crops"]["groups_failed"] == 1, "the uncertain group stays unresolved"
    assert report["arms"]["full"]["requests_sent"] == 8
    assert (tmp_path / "comparison.json").is_file()
    assert not (tmp_path / "deferred-full.json").exists(), "the stale deferral outlived its cause"
    kinds = report["arms"]["crops"]["score"]["error_kinds"]
    assert kinds.get("no_answer") == 2, "only the one uncertain group is still unanswered"


def test_a_run_whose_annotations_carry_no_labels_finishes_and_says_so():
    """Keeping truth out of the runtime file must not cost the run its own ending.

    The batch's ``annotations.json`` deliberately has no expected counts -- that is how labels stay
    evaluator-only -- and the probe still scored itself at the end of every send, so a fully answered
    six-request run finished with a ``KeyError: 'pips'`` traceback and no report. Every answer and every
    usage figure survived on disk, which is the only reason the spend was still reportable. A label-free
    run now grades nothing, says why, and exits cleanly.
    """
    annotations = [{"frame": "build/work/bb9/detect-1440p-v1/frames/frame-0571.jpg",
                    "timestamp": 570.56997, "role": "round_end_evidence_frame"},
                   {"frame": "build/work/bb9/detect-1440p-v1/frames/frame-0573.jpg",
                    "timestamp": 572.57197, "role": "after_end_plus_2s"}]
    observations = [{"timestamp": 570.56997, "label": "LEFT", "lit": 2, "drawn": True,
                     "confidence": 0.55},
                    {"timestamp": 572.57197, "label": "RIGHT", "lit": 1, "drawn": True,
                     "confidence": 0.9}]
    scored = bp.score_readings(observations, annotations)
    assert scored["frames"] == 0 and scored["frames_correct"] == 0
    assert len(scored["ungraded"]) == 2
    assert all("no evaluator labels" in entry["reason"] for entry in scored["ungraded"]), scored
    # A frame that *does* declare labels but declares them badly still raises: silence there would turn a
    # malformed truth file into a clean zero-scored run.
    with pytest.raises((KeyError, AttributeError, TypeError)):
        bp.score_readings(observations, [{"timestamp": 570.56997, "pips": None}])


class FlakyTransport(FakeTransport):
    """Answers correctly except the first time it is asked, when it returns text that is not JSON.

    This is the shape one real batch actually took: the reply arrived, spent 1,379 completion tokens,
    could not be parsed, and the retry was charged again. Every consumer that only looked at accepted
    replies reported five records and called that the spend.
    """

    def __init__(self, annotations, behaviour="truth", first_reply_broken=True,
                 broken_tokens=(629, 1379, 1096), good_tokens=(629, 542, 457)):
        super().__init__(annotations, behaviour=behaviour)
        self.broken = first_reply_broken
        self.token_cycle = [broken_tokens, good_tokens]

    def __call__(self, body):
        if self.broken:
            self.bodies.append(body)
            self.broken = False
            prompt, completion, reasoning = self.token_cycle[0]
            # Valid JSON on one line, trailing prose -- exactly what the real reply did.
            return {"choices": [{"message": {"content": '[{"item": 1, "label": "LEFT"}]\n\nDone.'}}],
                    "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                              "completion_tokens_details": {"reasoning_tokens": reasoning}}}
        prompt, completion, reasoning = self.token_cycle[1]
        reply = super().__call__(body)
        reply["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion,
                          "completion_tokens_details": {"reasoning_tokens": reasoning}}
        return reply


def test_a_retried_malformed_reply_is_billed_once_and_counted_twice(tmp_path, annotations, monkeypatch,
                                                              capsys):
    """Compute spent on an answer nobody kept still has to appear in the run's total.

    The defect was structural: usage was stamped on the success path only, so a rejected reply vanished
    from every consumer's figure while the ledger charged for it. Both totals are asserted here, plus the
    split between them -- merging them would be the mirror-image error.
    """
    transport = FlakyTransport(annotations)
    records = bp.build_records(annotations["frames"], tmp_path / "crops")
    transport.register_crops(records)
    # Eight groups plus the one wasted reply: --max-requests caps *sends*, so a budget of 8 would stop
    # after seven answers and the thrown-away reply, leaving a group never asked.
    argv = prepared_args(tmp_path, arms="crops") + ["--max-requests", "9"]
    run_script(monkeypatch, argv + ["--prepare"], capsys=capsys)
    capsys.readouterr()   # the prepare report is not the document under test
    _, out = run_script(monkeypatch, argv + ["--execute"], transport=transport, capsys=capsys)
    report = json.loads(out)
    arm = report["arms"]["crops"]

    usage = arm["tokens"]
    assert usage["accepted_replies"] == 8, usage
    assert usage["rejected_replies_retried"] == 1, usage
    assert usage["records"] == 9, "eight kept answers plus the reply that was thrown away"
    # 8 * 542 kept + 1,379 wasted, from the two-token-cycle above (one group takes the broken first hit).
    assert usage["completion"] == 8 * 542 + 1379, usage
    assert usage["accepted_only"]["completion"] == 8 * 542, usage["accepted_only"]
    assert usage["rejected_only"]["completion"] == 1379, usage["rejected_only"]
    assert usage["prompt"] == 9 * 629, usage
    assert usage["usage_complete"] is True and usage["records_without_usage"] == 0
    assert arm["groups_answered"] == 8 and arm["groups_failed"] == 0


def test_missing_usage_is_reported_as_unknown_not_as_zero():
    """A response that carried no usage block must not quietly become a free call."""
    summary = media.summarize_usage([{"tokens": {"prompt_tokens": 10, "completion_tokens": 5},
                                   "usage_reported": True, "accepted": True},
                                  {"tokens": {}, "usage_reported": False, "accepted": False}])
    assert summary["all_attempts"]["records"] == 2
    assert summary["all_attempts"]["records_without_usage"] == 1
    assert summary["all_attempts"]["complete"] is False
    assert summary["all_attempts"]["prompt"] == 10, "the known figure is still the known figure"
    assert summary["rejected_replies_retried"] == 1
    assert summary["accepted_only"]["records"] == 1 and summary["accepted_only"]["prompt"] == 10


def test_the_grader_publishes_spend_from_the_source_that_covers_every_charge(tmp_path, monkeypatch):
    """Accepted-reply usage is a scoring diagnostic; the headline spend follows the ledger."""
    grader_path = ROOT / "scripts/grade_reset_probe.py"
    spec = importlib.util.spec_from_file_location("grade_probe", str(grader_path))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "grade_probe", module)
    spec.loader.exec_module(module)
    assert module.bm.summarize_usage([])["all_attempts"]["records"] == 0  # grader shares the helper
    split = module.bm.summarize_usage([
        {"tokens": {"prompt_tokens": 629, "completion_tokens": 1379,
                    "completion_tokens_details": {"reasoning_tokens": 1096}},
         "usage_reported": True, "accepted": False},
        {"tokens": {"prompt_tokens": 629, "completion_tokens": 542,
                    "completion_tokens_details": {"reasoning_tokens": 457}},
         "usage_reported": True, "accepted": True}])
    assert split["all_attempts"]["completion"] == 1921
    assert split["accepted_only"]["completion"] == 542
    assert split["rejected_only"]["completion"] == 1379
