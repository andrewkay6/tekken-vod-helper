"""Truth-label resolution and re-scoring without spending an inference run.

The BB9 project reproduces a historical failure: six matches whose last row has
end=null while the project reports duration 0.0, which used to be discovered only
after detect() had already spent every model call.
"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from tekken_vod_helper import boundary_workflow

NULL_END_PROJECT = {
    "duration": 0.0,
    "matches": [
        {"start": 36.886, "end": 775.207},
        {"start": 775.207, "end": 1577.842},
        {"start": 4631.81, "end": None},
    ],
}


def test_derive_end_from_next_start():
    raw = {"duration": 0.0, "matches": [{"start": 10, "end": None}, {"start": 50, "end": 90}]}
    resolved, derived, unresolved = boundary_workflow.resolve_truth_matches(raw, eval_end=100.0)
    assert resolved[0]["end"] == 50
    assert derived == [0] and unresolved == []


def test_zero_duration_cannot_resolve_a_trailing_null_end():
    with pytest.raises(ValueError) as exc:
        boundary_workflow.resolve_truth_matches(NULL_END_PROJECT, eval_end=6928.42)
    assert "Truth match 2 has end=None" in str(exc.value)


def test_error_names_the_override_that_fixes_it():
    with pytest.raises(ValueError) as exc:
        boundary_workflow.resolve_truth_matches(NULL_END_PROJECT, eval_end=6928.42)
    assert "--truth-duration" in str(exc.value)


def test_measured_duration_override_resolves_without_editing_labels():
    resolved, derived, unresolved = boundary_workflow.resolve_truth_matches(
        NULL_END_PROJECT, eval_end=6928.42, project_duration=6928.421136)
    assert resolved[-1] == {"start": 4631.81, "end": 6928.421136}
    assert derived == [2] and unresolved == []


def test_row_starting_past_the_range_stays_unresolved():
    # Historic prefix behaviour: an end past the evaluated window is excluded, not an error.
    raw = {"duration": 0.0, "matches": [{"start": 5000, "end": None}]}
    resolved, derived, unresolved = boundary_workflow.resolve_truth_matches(raw, eval_end=1000.0)
    assert resolved == [{"start": 5000, "end": None}]
    assert unresolved == [0] and derived == []


def test_legacy_timestamp_spellings_still_resolve():
    raw = {"matches": [{"start_time": 10, "end_time": None}, {"start_time": 30, "end_time": 60}]}
    resolved, derived, _ = boundary_workflow.resolve_truth_matches(raw, eval_end=100.0)
    assert resolved == [{"start": 10, "end": 30}, {"start": 30, "end": 60}]
    assert derived == [0]


def test_bare_list_truth_is_accepted():
    resolved, _, _ = boundary_workflow.resolve_truth_matches(
        [{"start": 1, "end": 2}], eval_end=10.0)
    assert resolved == [{"start": 1, "end": 2}]


def test_backtest_rejects_unusable_truth_before_detecting(tmp_path, monkeypatch):
    truth = tmp_path / "truth.tvh.json"
    truth.write_text(json.dumps(NULL_END_PROJECT), encoding="utf-8")

    def _no_detect(*a, **kw):
        raise AssertionError("detect must not run when the labels cannot be scored")

    monkeypatch.setattr(boundary_workflow, "detect", _no_detect)
    with pytest.raises(ValueError) as exc:
        boundary_workflow.backtest("http://remote/v", str(truth), str(tmp_path / "out"), duration=6928.42)
    assert "Truth match 2" in str(exc.value)


def test_backtest_precheck_is_skipped_for_an_unprobeable_remote_source(tmp_path, monkeypatch):
    # Without a known evaluated range the pre-check cannot judge, so detection must still run.
    truth = tmp_path / "truth.tvh.json"
    truth.write_text(json.dumps(NULL_END_PROJECT), encoding="utf-8")
    called = {}

    def _fake_detect(*a, **kw):
        called["yes"] = True
        raise RuntimeError("stop after the pre-check")

    monkeypatch.setattr(boundary_workflow, "detect", _fake_detect)
    with pytest.raises(RuntimeError):
        boundary_workflow.backtest("http://remote/v", str(truth), str(tmp_path / "out"))
    assert called.get("yes") is True


def test_local_source_probes_with_the_real_two_argument_signature(tmp_path, monkeypatch):
    # An earlier version called probe_duration with one argument; the resulting TypeError was
    # swallowed, silently disabling the pre-check for every local file.
    from tekken_vod_helper.ffmpeg_tools import FfmpegError  # noqa: F401  (documented failure mode)
    media = tmp_path / "rec.mp4"
    media.write_bytes(b"not really video")
    seen = {}

    def _fake_probe(video_path, ffprobe_path):
        seen["video_path"] = video_path
        seen["ffprobe_path"] = ffprobe_path
        return 6928.421136

    monkeypatch.setattr(boundary_workflow, "probe_duration", _fake_probe)
    assert boundary_workflow._evaluated_end(str(media), None, "/usr/bin/ffprobe") == 6928.421136
    assert seen == {"video_path": str(media), "ffprobe_path": "/usr/bin/ffprobe"}


def test_probe_failure_degrades_to_no_precheck(tmp_path, monkeypatch):
    from tekken_vod_helper.ffmpeg_tools import FfmpegError
    media = tmp_path / "rec.mp4"
    media.write_bytes(b"not really video")

    def _boom(video_path, ffprobe_path):
        raise FfmpegError("ffprobe was not found")

    monkeypatch.setattr(boundary_workflow, "probe_duration", _boom)
    assert boundary_workflow._evaluated_end(str(media)) is None


def test_programming_error_in_the_probe_is_not_swallowed(tmp_path, monkeypatch):
    media = tmp_path / "rec.mp4"
    media.write_bytes(b"not really video")

    def _wrong_arity(video_path):
        return 1.0

    monkeypatch.setattr(boundary_workflow, "probe_duration", _wrong_arity)
    with pytest.raises(TypeError):
        boundary_workflow._evaluated_end(str(media))


def test_backtest_refuses_a_local_source_before_detecting(tmp_path, monkeypatch):
    truth = tmp_path / "truth.tvh.json"
    truth.write_text(json.dumps(NULL_END_PROJECT), encoding="utf-8")
    media = tmp_path / "rec.mp4"
    media.write_bytes(b"not really video")
    monkeypatch.setattr(boundary_workflow, "probe_duration", lambda v, f: 6928.421136)

    def _no_detect(*a, **kw):
        raise AssertionError("detect must not run when the labels cannot be scored")

    monkeypatch.setattr(boundary_workflow, "detect", _no_detect)
    with pytest.raises(ValueError) as exc:
        boundary_workflow.backtest(str(media), str(truth), str(tmp_path / "out"))
    assert "Truth match 2" in str(exc.value)


def _write_run(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    predictions = [{"start": 10.0, "end": 100.0}]
    (out / "predictions.json").write_text(json.dumps(predictions), encoding="utf-8")
    run = {"status": "complete", "range": [0.0, 6928.421136], "source_id": "bb9",
           "paths": {"predictions": str(out / "predictions.json")}}
    (out / "run.json").write_text(json.dumps(run), encoding="utf-8")
    return out, run


def test_score_from_run_needs_no_model_calls(tmp_path, monkeypatch):
    out, _ = _write_run(tmp_path)
    truth = tmp_path / "truth.tvh.json"
    truth.write_text(json.dumps(NULL_END_PROJECT), encoding="utf-8")

    def _forbidden(*a, **kw):
        raise AssertionError("scoring a saved run must not call the model")

    monkeypatch.setattr(boundary_workflow, "detect", _forbidden)
    monkeypatch.setattr("tekken_vod_helper.boundary_media.VisionClient", _forbidden)
    report = boundary_workflow.score_from_run(str(out / "run.json"), str(truth), str(out),
                                              truth_duration=6928.421136)
    assert report["derived_truth_ends"] == [2]
    assert report["truth_duration_override"] == 6928.421136
    assert report["detect"]["status"] == "complete"
    assert report["score"]["reference_count"] == 3
    written = json.loads((out / "backtest-report.json").read_text(encoding="utf-8"))
    assert written["calibration"] == "heldout"


def test_score_predictions_cli_dispatch(tmp_path, monkeypatch):
    out, _ = _write_run(tmp_path)
    truth = tmp_path / "truth.tvh.json"
    truth.write_text(json.dumps(NULL_END_PROJECT), encoding="utf-8")
    seen = {}

    def _fake_score(run_path, truth_path, output_dir, source="", truth_duration=None):
        seen.update(run_path=run_path, truth=truth_path, out=output_dir,
                    source=source, duration=truth_duration)
        return {"status": "scored", "report_path": str(Path(output_dir) / "backtest-report.json")}

    monkeypatch.setattr(boundary_workflow, "score_from_run", _fake_score)
    from tekken_vod_helper.boundary_cli import main
    f = io.StringIO()
    with redirect_stdout(f):
        main(["score-predictions", "--run", str(out / "run.json"), "--truth", str(truth),
              "--output-dir", str(out), "--truth-duration", "6928.421136", "--source", "bb9"])
    assert seen["run_path"] == str(out / "run.json")
    assert seen["truth"] == str(truth)
    assert seen["duration"] == 6928.421136
    assert seen["source"] == "bb9"
    assert json.loads(f.getvalue())["status"] == "scored"
