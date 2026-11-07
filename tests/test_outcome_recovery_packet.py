"""The held outcome-recovery packet: evaluator wiring, budget arithmetic and honest provenance.

These are the checks the review found missing. They run offline against the packet on disk, plus one
synthetic run directory: a fake five-answer result goes through the real grader command, with the reviewed
five-frame label file, and an injected wrong answer must be reported. That is the difference between a
label file that exists and an evaluation path that works.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "build/work/bb9/outcome-recovery-v1"
REVIEWED = BATCH / "labels-codex-reviewed-2026-11-06.json"
POOL = BATCH / "labels-evaluator-only.json"
ANNOTATIONS = BATCH / "annotations.json"
PREPARED = BATCH / "run/prepared.json"
ENV_PATHS = ":".join([str(ROOT / ".qwen/tmp/pylibs"),
                      str(ROOT / ".venv/lib/python3.14/site-packages"), str(ROOT)])
requires_packet = pytest.mark.skipif(not (REVIEWED.is_file() and PREPARED.is_file()),
                                     reason="the outcome-recovery-v1 packet lives in build/work")


def _observation(frame, timestamp, label, value, drawn=True):
    path = "build/work/bb9/detect-1440p-v1/frames/%s" % frame
    raw = {"confidence": 0.9, "drawn": drawn, "item": 1, "label": label, "lit": value,
           "positions": []}
    return dict(raw, frame=path, timestamp=timestamp, arm="crops", raw=raw)


def _group(frame, timestamp, left, right, drawn=(True, True), identity=""):
    path = "build/work/bb9/detect-1440p-v1/frames/%s" % frame
    return {"arm": "crops", "errors": [], "fingerprint": identity, "frame": path,
            "frame_size": [640, 360], "model": "qwen3.8-flash-next", "timestamp": timestamp,
            "identity": {"arm": "crops", "version": "pip-crops-v1", "scale": 4},
            "crop_images": [],
            "observations": [_observation(frame, timestamp, "LEFT", left, drawn[0]),
                             _observation(frame, timestamp, "RIGHT", right, drawn[1])]}


def _fake_run(tmp_path, groups):
    """A run directory shaped exactly like an executed one, built from the packet's own preparation."""
    run = tmp_path / "run"
    __import__("shutil").copytree(BATCH / "run", run, dirs_exist_ok=True)
    groups_dir = run / "crops" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    for stale in groups_dir.glob("*.json"):
        stale.unlink()
    observations = []
    for index, group in enumerate(groups):
        (groups_dir / ("crops-%016x.json" % index)).write_text(json.dumps(group), encoding="utf-8")
        observations.extend(group["observations"])
    (run / "result-crops.json").write_text(json.dumps({
        "arm": "crops", "model": "qwen3.8-flash-next", "status": "complete",
        "stopped_reason": None, "requests_sent": len(groups), "groups_planned": len(groups),
        "groups_answered": len(groups), "groups_failed": 0, "groups_reused": 0,
        "not_attempted": 0, "errors": [], "attempt_allowance": 7,
        "usage": [{"tokens": {"prompt_tokens": 631, "completion_tokens": 700 + 100 * index,
                              "completion_tokens_details": {"reasoning_tokens": 600}}}
                  for index in range(len(groups))],
        "observations": observations}, indent=1), encoding="utf-8")
    return run


def _grade(tmp_path, groups, labels=REVIEWED, ledger=None):
    run = _fake_run(tmp_path, groups)
    if ledger is not None:
        # The grader looks for the ledger beside the run directory, exactly where the probe writes it.
        (run.parent / "attempts.json").write_text(json.dumps(ledger), encoding="utf-8")
    out = tmp_path / "grade.json"
    command = [sys.executable, str(ROOT / "scripts" / "grade_reset_probe.py"),
               "--run", str(run), "--annotations", str(ANNOTATIONS), "--labels", str(labels),
               "--out", str(out), "--no-sensitivity"]
    result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                            env=dict(__import__("os").environ, PYTHONPATH=ENV_PATHS))
    assert result.returncode == 0, result.stdout[-700:] + result.stderr[-700:]
    return json.loads(out.read_text(encoding="utf-8"))


def _ledger(charged, entries):
    return {"attempts_charged": charged, "attempts_remaining": 0, "cap": charged, "minutes": 15.0,
            "created_at": 1.0, "deadline_at": 901.0,
            "entries": [{"state": "answered", "tokens": t} for t in entries]}


@requires_packet
def test_grader_spend_survives_an_absent_ledger_instead_of_becoming_empty(tmp_path):
    """A missing ledger must not win the choice and publish a complete-looking zero over real numbers."""
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    groups = [_group(f["frame"].split("/")[-1], f["timestamp"], f["pips"]["LEFT"], f["pips"]["RIGHT"],
                     identity=f["identity"]) for f in reviewed["frames"]]
    with_ledger = _grade(tmp_path, groups,
                         ledger=_ledger(6, [{"prompt_tokens": 629, "completion_tokens": n} for n in
                                            (431, 331, 344, 615, 1379, 542)]))
    spent = with_ledger["spent"]
    assert spent["spend_source"] == "ledger", spent["spend_source_reason"]
    assert spent["spend"]["completion"] == 3642, spent["spend"]
    # Re-run against a tree with no ledger at all: the report has to carry the spend.
    (tmp_path / "attempts.json").unlink()
    back = _grade(tmp_path, groups)["spent"]
    assert back["spend_source"] == "run report", back["spend_source_reason"]
    assert back["spend"]["records"] == 5 and back["spend"]["completion"] > 0, back["spend"]
    assert back["usage_provenance"]["ledger_present"] is False
    assert back["usage_provenance"]["agrees_with_ledger"] is None, "nothing to agree with"
    assert back["usage_provenance"]["agreement_is_meaningful"] is False


@requires_packet
def test_grader_reports_a_partial_usage_record_as_a_subtotal(tmp_path):
    """One reply with an empty usage block must not leave the total reading as fully measured."""
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    groups = [_group(f["frame"].split("/")[-1], f["timestamp"], f["pips"]["LEFT"], f["pips"]["RIGHT"],
                     identity=f["identity"]) for f in reviewed["frames"]]
    ledger = _ledger(6, [{"prompt_tokens": 629, "completion_tokens": 431}] * 5 + [{}])
    spent = _grade(tmp_path, groups, ledger=ledger)["spent"]
    assert spent["spend"]["complete"] is False, spent["spend"]
    assert spent["spend"]["subtotal"] is True
    assert spent["spend"]["fields"]["completion"]["records_missing"] == 1, spent["spend"]["fields"]
    assert spent["usage_provenance"]["records_without_usage"] == 1
    assert spent["spend_source"] == "ledger"
    assert "covers every charged attempt" in spent["spend_source_reason"]


@requires_packet
def test_grader_prefers_the_source_that_covers_the_charges_and_says_so(tmp_path):
    """Under-reporting must lose to a source that accounts for every charge -- and equal counts are not
    agreement.

    The fixture reproduces the real shape rather than an arithmetic coincidence: five replies the run kept,
    plus one discarded reply only the ledger admits to.
    """
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    groups = [_group(f["frame"].split("/")[-1], f["timestamp"], f["pips"]["LEFT"], f["pips"]["RIGHT"],
                     identity=f["identity"]) for f in reviewed["frames"]]
    ledger = _ledger(6, [{"prompt_tokens": 629, "completion_tokens": n} for n in
                         (431, 331, 344, 615, 1379, 542)])
    spent = _grade(tmp_path, groups, ledger=ledger)["spent"]
    assert spent["spend_source"] == "ledger"
    assert spent["spend"]["completion"] == 3642, spent["spend"]
    assert spent["usage_all_attempts"]["records"] == 5 < spent["usage_from_ledger"]["records"] == 6
    prov = spent["usage_provenance"]
    assert prov["agrees_with_ledger"] is False
    assert prov["report_covers_every_charged_attempt"] is False
    # A report that answers for every charge is chosen on its own merits, even though a ledger exists.
    # Same record count, different totals: this is the case where equality of counts must not be read as
    # agreement, so the ledger deliberately disagrees with the report field by field.
    covered = _grade(tmp_path, groups, ledger=_ledger(5, [
        {"prompt_tokens": 1, "completion_tokens": 2} for _ in range(5)]))["spent"]
    assert covered["spend_source"] == "run report", covered["spend_source_reason"]
    assert covered["usage_provenance"]["report_covers_every_charged_attempt"] is True
    assert covered["usage_provenance"]["record_counts_equal"] is True
    assert covered["usage_provenance"]["agrees_with_ledger"] is False, "equal counts, different totals"
    assert covered["usage_provenance"]["record_count_equality_is_not_coverage"] is True


@requires_packet
def test_regrading_the_real_batch_publishes_the_new_fields_and_keeps_the_old_report(tmp_path):
    """The finished batch is evidence. Regrade into a new file; never rewrite the report that was signed off.

    The historical ``grade-v1.json`` was written by the grader as it then was, and has no spend split at
    all. Asserting new keys on it would be asserting them on a document that predates them, so this test
    runs the current grader over the same read-only run and compares the two.
    """
    historical_path = BATCH / "grade-v1.json"
    before = historical_path.read_bytes()
    out = tmp_path / "grade-v2.json"
    command = [sys.executable, str(ROOT / "scripts" / "grade_reset_probe.py"),
               "--run", str(BATCH / "run"), "--annotations", str(ANNOTATIONS),
               "--labels", str(REVIEWED), "--out", str(out), "--no-sensitivity"]
    done = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                          env=dict(__import__("os").environ, PYTHONPATH=ENV_PATHS))
    assert done.returncode == 0, done.stdout[-500:] + done.stderr[-500:]
    spent = json.loads(out.read_text(encoding="utf-8"))["spent"]

    assert spent["tokens"]["completion"] == 2263, "accepted replies, unchanged meaning"
    assert spent["usage_all_attempts"]["completion"] == 2263
    assert spent["usage_from_ledger"]["completion"] == 3642
    assert spent["spend"]["completion"] == 3642 and spent["spend_source"] == "ledger"
    assert 3642 - 2263 == 1379, "the discarded reply, now visible"
    assert spent["usage_provenance"]["agrees_with_ledger"] is False
    assert spent["usage_provenance"]["report_covers_every_charged_attempt"] is False
    # The document that was reviewed stays byte-identical, and the new fields are genuinely new to it.
    assert historical_path.read_bytes() == before
    historical = json.loads(before.decode("utf-8"))["spent"]
    assert "spend" not in historical and "usage_all_attempts" not in historical
    assert historical["tokens"]["completion"] == 2263 == spent["tokens"]["completion"]


@requires_packet
def test_the_reviewed_five_frame_file_is_the_evaluation_authority():
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    annotations = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    assert reviewed["labels_are_runtime_input"] is False
    assert len(reviewed["frames"]) == 5 and len(pool["frames"]) == 8, "the pool stays, the key narrows"
    assert {f["frame"].split("/")[-1] for f in reviewed["frames"]} == \
        {f["frame"].split("/")[-1] for f in annotations["frames"]}
    assert reviewed["denominators"] == {
        "selected_frames": 5, "selected_sides": 10, "known_count_sides": 9, "unknown_count_sides": 1,
        "frames_with_both_counts_known": 4, "sides_in_strict_frame_accuracy_rate": 8,
        "known_left_count_outside_frame_accuracy_rate": 1, "known_drawn_presence_sides": 10}
    assert reviewed["supersedes_for_evaluation"].startswith("labels-evaluator-only.json")
    # An unknown count is never absence: the covered side is drawn with an unknown number.
    covered = next(f for f in reviewed["frames"] if f["pips"]["RIGHT"] is None)
    assert covered["row_drawn_by_side"]["RIGHT"] is True


@requires_packet
def test_a_fake_result_grades_end_to_end_against_the_reviewed_labels(tmp_path):
    """No endpoint, no ledger: five answers that match the labels must produce the strict denominators."""
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    groups = [_group(f["frame"].split("/")[-1], f["timestamp"], f["pips"]["LEFT"], f["pips"]["RIGHT"],
                     identity=f["identity"]) for f in reviewed["frames"]]
    score = _grade(tmp_path, groups)
    body = score["score"]
    assert (body["frames"], body["frames_correct"]) == (4, 4), body
    assert (body["sides_exact"], body["sides_total"]) == (8, 8), body
    assert body["error_kinds"] == {"exact": 8}, body
    assert body["accuracy_is_reportable"] is True
    withheld = score["unknown_truth_withheld_from_grading"]
    assert len(withheld) == 1 and withheld[0]["frame"].endswith("frame-6340.jpg")
    sides = {s["side"]: s for s in score["sides"] if "6340" in s["frame"]}
    assert sides["LEFT"]["graded"] is True and sides["LEFT"]["agrees"] is True, "LEFT=3 graded"
    assert sides["RIGHT"]["graded"] is False and sides["RIGHT"]["label"] is None
    assert sides["RIGHT"]["label_drawn"] is True, "drawn with an unknown count is not absence"
    assert score["labels_kept_out_of_runtime"]["truth_bearing_fields_found"] == []
    assert score["labels_kept_out_of_runtime"]["annotations_expected_all_null"] is True
    assert score["labels_kept_out_of_runtime"]["prepared_expected_all_null"] is True
    assert score["inference_sent"] is False


@requires_packet
def test_a_wrong_answer_is_reported_rather_than_silently_averaged(tmp_path):
    """The control that makes the pass above mean something: flip one count and see it caught."""
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    groups = [_group(f["frame"].split("/")[-1], f["timestamp"], f["pips"]["LEFT"], f["pips"]["RIGHT"],
                     identity=f["identity"]) for f in reviewed["frames"]]
    groups[0]["observations"][0]["lit"] = 1
    score = _grade(tmp_path, groups)
    body = score["score"]
    assert body["frames_correct"] == 3 and body["sides_exact"] == 7, body
    assert body["error_kinds"] == {"exact": 7, "missed_pips": 1}, body
    wrong = [s for s in score["sides"] if s["graded"] and not s["agrees"]]
    assert len(wrong) == 1 and wrong[0]["frame"].endswith("frame-5213.jpg"), wrong
    assert (wrong[0]["label"], wrong[0]["reported"]) == (3, 1), wrong[0]


@requires_packet
def test_the_grader_refuses_the_eight_frame_pool_as_an_answer_key(tmp_path):
    """The superseded pool must not be silently usable now that five frames are the packet."""
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    groups = [_group(f["frame"].split("/")[-1], f["timestamp"], f["pips"]["LEFT"], f["pips"]["RIGHT"],
                     identity=f["identity"]) for f in reviewed["frames"]]
    run = _fake_run(tmp_path, groups)
    command = [sys.executable, str(ROOT / "scripts" / "grade_reset_probe.py"),
               "--run", str(run), "--annotations", str(ANNOTATIONS), "--labels", str(POOL),
               "--out", str(tmp_path / "g.json"), "--no-sensitivity"]
    result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                            env=dict(__import__("os").environ, PYTHONPATH=ENV_PATHS))
    assert result.returncode != 0
    assert "supersede" in (result.stderr + result.stdout).lower()


@requires_packet
def test_the_budget_arithmetic_is_the_cap_not_the_nominal_batch():
    annotations = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    prepared = json.loads(PREPARED.read_text(encoding="utf-8"))
    plan = json.loads((BATCH / "run/plan-crops.json").read_text(encoding="utf-8"))
    assert len(annotations["frames"]) == len(prepared["frames"]) == 5
    assert prepared["attempt_allowance"] == 7
    assert plan["requests_planned"] == 5 and plan["images_total"] == 10
    assert plan["completion_tokens_per_request"] == 3000
    assert 5 * 3000 == 15000                       # nominal batch ceiling
    assert 7 * 2 == 14 and 7 * 3000 == 21000       # cap including both retries
    readme = (BATCH / "README.md").read_text(encoding="utf-8")
    assert "14 images" in readme and "21,000" in readme
    assert "9 images" not in readme, "the reserve doubles two-image reads, not one"
    assert "executed 2026-11-06" in readme, "the README must not still read as a held proposal"


@requires_packet
def test_runtime_stays_label_free_and_claims_are_real():
    annotations = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    prepared = json.loads(PREPARED.read_text(encoding="utf-8"))
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8"))
    for entry in annotations["frames"] + prepared["frames"]:
        assert entry.get("expected") is None
    text = json.dumps(annotations)
    for count in {str(f["pips"]["LEFT"]) for f in reviewed["frames"]} | \
                {str(f["pips"]["RIGHT"]) for f in reviewed["frames"]}:
        if count.isdigit():
            assert not any('"%s"' % count in chunk for chunk in text.replace(
                '"timestamp"', '').split('"role"')[1:]), "a label count must not sit in runtime prose"
    assert annotations["selection_status"].startswith("reviewer-selected")
    kinds = {tuple(entry["claims"]) for entry in annotations["frames"]}
    assert kinds and all(k for k in kinds), "every request must name the public claim it answers"
    assert any("game_winner_unobserved" in k for k in kinds)
    for key in ("would_resolve", "claim_at", "asked_by"):
        assert all(entry.get(key) for entry in annotations["frames"]), key


@requires_packet
def test_provenance_does_not_claim_automatic_prioritization():
    provenance = json.loads((BATCH / "selection-provenance.json").read_text(encoding="utf-8"))
    assert provenance["stage_1_public_scope"]["requests"] == 35
    assert provenance["stage_2_public_narrowing"]["requests_generated"] == 29
    assert provenance["stage_2_public_narrowing"]["rule_reproduces_the_inspected_set"] is False
    assert provenance["stage_3_reviewer_pick"]["automatic_prioritization"].startswith("unvalidated")
    assert provenance["labels_used_for_selection_and_never_for_runtime"] is True


@requires_packet
def test_legibility_is_recorded_per_frame_and_a_rejection_is_not_coverage():
    classification = json.loads((BATCH / "candidate-classification.json").read_text(encoding="utf-8"))
    counts = classification["counts"]
    assert counts["priced"] == 14 and counts["readable"] == 8
    assert counts["not_cleanly_readable"] == 6
    kinds = {c["legibility"] for c in classification["candidates"]}
    assert kinds <= {"readable", "ambiguous", "occluded", "absent", "unreadable"}
    assert all(c["coverage"] == "unpaid" for c in classification["candidates"])
    for candidate in classification["candidates"]:
        assert candidate["offline_evaluator_rejection_is_not_runtime_coverage"] is True
        if candidate["legibility"] != "readable":
            assert candidate["what_the_pixels_show"], candidate["frame"]
    bogus = [c["frame"] for c in classification["candidates"]
             if "not inspected" in c["what_the_pixels_show"].lower()
             or c["what_the_pixels_show"] in ("same reason as 5683",)]
    assert not bogus, "a deferral is not a pixel finding: %s" % bogus
