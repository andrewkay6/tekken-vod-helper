"""Local model defaults, endpoint normalization, and the CLI defaults built on them."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tekken_vod_helper import local_model
from tekken_vod_helper.boundary_media import VisionClient
from tekken_vod_helper.boundary_reasoning import TemporalAdjudicator


def test_defaults_are_the_live_server():
    # Port 8010 is dead on this machine and qwen3.8-27b is the alias to avoid.
    assert local_model.DEFAULT_ENDPOINT == "http://127.0.0.1:8000"
    assert local_model.DEFAULT_MODEL == "qwen3.8-flash-next"


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv(local_model.ENDPOINT_ENV, "http://other.host:9000/v1")
    monkeypatch.setenv(local_model.MODEL_ENV, "some-other-model")
    assert local_model.default_endpoint() == "http://other.host:9000/v1"
    assert local_model.default_model() == "some-other-model"


def test_blank_environment_falls_back(monkeypatch):
    monkeypatch.setenv(local_model.ENDPOINT_ENV, "   ")
    monkeypatch.setenv(local_model.MODEL_ENV, "")
    assert local_model.default_endpoint() == local_model.DEFAULT_ENDPOINT
    assert local_model.default_model() == local_model.DEFAULT_MODEL


def test_endpoint_forms_all_normalize():
    for base in ("http://h:8000", "http://h:8000/", "http://h:8000/v1",
                 "http://h:8000/v1/chat/completions"):
        assert local_model.chat_completions_url(base) == "http://h:8000/v1/chat/completions"


def test_no_double_v1_path():
    # Appending blindly used to produce /v1/v1/chat/completions for a /v1 root.
    for base in ("http://h:8000", "http://h:8000/v1", "http://h:8000/v1/chat/completions"):
        assert "/v1/v1" not in local_model.chat_completions_url(base)


def test_empty_endpoint_rejected():
    for bad in ("", "   ", None):
        try:
            local_model.chat_completions_url(bad)
        except ValueError:
            continue
        raise AssertionError("expected ValueError for {!r}".format(bad))


def test_vision_client_defaults():
    vc = VisionClient()
    assert vc.endpoint == "http://127.0.0.1:8000/v1/chat/completions"
    assert vc.model == "qwen3.8-flash-next"


def test_vision_client_accepts_every_endpoint_shape():
    for base in ("http://h:1", "http://h:1/v1", "http://h:1/v1/chat/completions"):
        assert VisionClient(endpoint=base).endpoint == "http://h:1/v1/chat/completions"


def test_adjudicator_defaults_match_vision_client():
    adj = TemporalAdjudicator()
    assert adj._endpoint == "http://127.0.0.1:8000/v1/chat/completions"
    assert adj._model == "qwen3.8-flash-next"


def test_cli_leaves_endpoint_and_model_unset_so_defaults_apply():
    from tekken_vod_helper.boundary_cli import build_parser
    parser = build_parser()
    argv = [["detect", "src", "--output-dir", "/tmp/o"],
            ["backtest-boundaries", "src", "--output-dir", "/tmp/o", "--truth", "/tmp/t.json"],
            ["score-predictions", "--run", "/tmp/r.json", "--truth", "/tmp/t.json",
             "--output-dir", "/tmp/o"]]
    for command in argv:
        args = parser.parse_args(command)
        if command[0] == "score-predictions":
            assert not hasattr(args, "endpoint"), command
            continue
        assert args.endpoint is None, command
        assert args.model is None, command


def test_cli_flags_still_override(monkeypatch):
    from tekken_vod_helper.boundary_cli import build_parser
    monkeypatch.setenv(local_model.ENDPOINT_ENV, "http://env-host:8000")
    parser = build_parser()
    args = parser.parse_args(["detect", "src", "--output-dir", "/tmp/o",
                              "--endpoint", "http://explicit:1234", "--model", "explicit-model"])
    assert args.endpoint == "http://explicit:1234"
    assert args.model == "explicit-model"
