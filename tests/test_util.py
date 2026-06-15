from tekken_vod_helper import ffmpeg_tools
from tekken_vod_helper.util import parse_timestamp, seconds_to_timestamp, slugify


def test_timestamp_round_trip_shapes():
    assert seconds_to_timestamp(65.25) == "00:01:05.250"
    assert parse_timestamp("01:05.250") == 65.25
    assert parse_timestamp("00:01:05.250") == 65.25


def test_slugify_keeps_names_filesystem_safe():
    assert slugify("Alice / Bob: Grand Finals") == "Alice_Bob_Grand_Finals"


def test_resolve_tool_prefers_configured_path(tmp_path, monkeypatch):
    configured = tmp_path / "custom-ffmpeg.exe"
    configured.write_text("", encoding="utf-8")
    portable = tmp_path / "ffmpeg" / "ffmpeg.exe"
    portable.parent.mkdir()
    portable.write_text("", encoding="utf-8")
    monkeypatch.setattr(ffmpeg_tools.shutil, "which", lambda _name: None)

    assert ffmpeg_tools.resolve_tool(str(configured), "ffmpeg") == str(configured)


def test_resolve_tool_finds_portable_ffmpeg_folder(tmp_path, monkeypatch):
    app_exe = tmp_path / "TekkenVodHelper.exe"
    app_exe.write_text("", encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg" / "ffmpeg.exe"
    ffmpeg.parent.mkdir()
    ffmpeg.write_text("", encoding="utf-8")
    monkeypatch.setattr(ffmpeg_tools.sys, "executable", str(app_exe))
    monkeypatch.setattr(ffmpeg_tools.sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)
    monkeypatch.setattr(ffmpeg_tools.shutil, "which", lambda _name: None)

    assert ffmpeg_tools.resolve_tool("", "ffmpeg") == str(ffmpeg)
