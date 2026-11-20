import json

from tekken_vod_helper.models import OverlayState
from tekken_vod_helper.overlay_server import OverlayServer


def _server(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    server = OverlayServer(static, history_dir=tmp_path / "history")
    server.runtime_dir = tmp_path / "runtime"
    return server


def _lines(tmp_path):
    files = list((tmp_path / "history").glob("overlay-*.jsonl"))
    assert len(files) == 1
    return [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]


def test_each_visible_change_is_logged_once_with_time(tmp_path):
    server = _server(tmp_path)
    state = OverlayState(description="Basement Brawl #11", subtitle="Winners Round 1", p1name="Lap Sap", p2name="SgHen")
    server.write_state(state)
    server.write_state(state)                       # no change: no new line
    state.p1score = 1
    server.write_state(state)
    state.subtitle = "Winners Quarter-Final"
    state.p1name, state.p2name, state.p1score = "andy", "LtHusky", 0
    server.write_state(state)
    lines = _lines(tmp_path)
    assert [(l["subtitle"], l["p1name"], l["p1score"]) for l in lines] == [
        ("Winners Round 1", "Lap Sap", 0), ("Winners Round 1", "Lap Sap", 1), ("Winners Quarter-Final", "andy", 0)]
    assert all(l["epoch"] > 0 and "T" in l["at"] for l in lines)
    assert json.loads((tmp_path / "runtime" / "state.json").read_text())["p1name"] == "andy"


def test_appearance_only_changes_are_not_logged(tmp_path):
    server = _server(tmp_path)
    state = OverlayState(p1name="a", p2name="b")
    server.write_state(state)
    state.font, state.opacity = "Arial", 0.5
    server.write_state(state)
    assert len(_lines(tmp_path)) == 1


def test_history_failure_does_not_break_the_overlay(tmp_path):
    blocker = tmp_path / "history"
    blocker.write_text("not a directory")
    static = tmp_path / "static"
    static.mkdir()
    server = OverlayServer(static, history_dir=blocker)
    server.runtime_dir = tmp_path / "runtime"
    server.write_state(OverlayState(p1name="a"))
    assert json.loads((tmp_path / "runtime" / "state.json").read_text())["p1name"] == "a"
