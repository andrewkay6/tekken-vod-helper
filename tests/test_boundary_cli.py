"""Tests for boundary detect/backtest CLI dispatch and legacy script agentic mode."""
import io
import importlib.util
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_legacy():
    spec = importlib.util.spec_from_file_location(
        "bt_script", REPO_ROOT / "scripts" / "backtest_youtube_boundaries.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_detect_dispatch():
    with patch("tekken_vod_helper.boundary_workflow.detect") as mock_detect:
        mock_detect.return_value = {"status": "complete", "match_count": 3,
                                    "paths": {}, "range": [0, 100],
                                    "review_count": 1, "model_calls": 10,
                                    "policy_hash": "abc", "source_id": "test",
                                    "output_dir": "/tmp/out"}
        from tekken_vod_helper.boundary_cli import main
        f = io.StringIO()
        with redirect_stdout(f):
            main(["detect", "http://example.com/v", "--output-dir", "/tmp/out",
                  "--duration", "600", "--interval", "3", "--fine-step", "0.5",
                  "--max-frames", "100", "--batch-size", "4", "--max-requests", "50",
                  "--yt-dlp", "/usr/bin/yt-dlp", "--endpoint", "http://ep",
                  "--model", "test-model", "--samples-manifest", "/tmp/m.json",
                  "--ffmpeg", "/usr/bin/ffmpeg", "--ffprobe", "/usr/bin/ffprobe"])
        kwargs = mock_detect.call_args[1]
        assert kwargs["duration"] == 600
        assert kwargs["interval"] == 3
        assert kwargs["fine_step"] == 0.5
        assert kwargs["max_frames"] == 100
        assert kwargs["batch_size"] == 4
        assert kwargs["max_requests"] == 50
        assert kwargs["yt_dlp"] == "/usr/bin/yt-dlp"
        assert kwargs["endpoint"] == "http://ep"
        assert kwargs["model"] == "test-model"
        assert kwargs["samples_manifest"] == "/tmp/m.json"
        assert kwargs["ffmpeg_path"] == "/usr/bin/ffmpeg"
        assert kwargs["ffprobe_path"] == "/usr/bin/ffprobe"
        assert mock_detect.call_args[0][0] == "http://example.com/v"
        assert mock_detect.call_args[0][1] == "/tmp/out"


def test_backtest_boundaries_dispatch():
    with patch("tekken_vod_helper.boundary_workflow.backtest") as mock_bt:
        mock_bt.return_value = {"score": {"f1": 0.9}, "detect": {}, "calibration": "heldout"}
        from tekken_vod_helper.boundary_cli import main
        f = io.StringIO()
        with redirect_stdout(f):
            main(["backtest-boundaries", "http://example.com/v",
                  "--truth", "/tmp/truth.json", "--output-dir", "/tmp/bt",
                  "--interval", "4", "--ffmpeg", "/usr/bin/ffmpeg",
                  "--ffprobe", "/usr/bin/ffprobe"])
        kwargs = mock_bt.call_args[1]
        assert kwargs["interval"] == 4
        assert kwargs["ffmpeg_path"] == "/usr/bin/ffmpeg"
        assert kwargs["ffprobe_path"] == "/usr/bin/ffprobe"
        assert mock_bt.call_args[0][0] == "http://example.com/v"
        assert mock_bt.call_args[0][1] == "/tmp/truth.json"
        assert mock_bt.call_args[0][2] == "/tmp/bt"


def test_legacy_parser_flags():
    script = _load_legacy()
    with patch("sys.argv", ["prog", "--agentic", "--truth", "/tmp/t.json",
                            "--url", "http://x", "--output-dir", "/tmp/o"]):
        args = script._parse_args()
        assert args.agentic is True
        assert args.truth == "/tmp/t.json"


def test_legacy_agentic_dispatch(tmp_path):
    out = str(tmp_path / "out")
    script = _load_legacy()
    with patch("sys.argv", ["prog", "--agentic", "--truth", "/tmp/t.json",
                            "--url", "http://x", "--output-dir", out,
                            "--ffmpeg", "/usr/bin/ffmpeg", "--ffprobe", "/usr/bin/ffprobe"]):
        with patch("tekken_vod_helper.boundary_workflow.backtest") as mock_bt:
            mock_bt.return_value = {"score": {}, "detect": {}}
            rc = script.main()
            assert rc == 0
            assert mock_bt.called
            assert mock_bt.call_args[0][1] == "/tmp/t.json"
