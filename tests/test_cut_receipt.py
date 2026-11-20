"""What a cut receipt may promise, and what the export worker has to persist.

These tests exist because the exporter's own honesty had three holes, none of which the existing 73
app-level tests could see: the stream-copy fallback called `_run(fallback)`, a name that has never existed,
so the recovery path for a failed copy was an crash instead of a second attempt; a receipt that fell back to
re-encode still carried the *copy's* GOP warning, which teaches a reviewer to distrust the wrong line; and
`frame_exact_start_promised` / a log saying "frame-exact as requested" turned the presence of an encoder flag
into a claim about delivered footage while the same receipt recorded `delivered_source_range_verified: false`.

Everything here runs without ffmpeg: the receipt is a statement about what was asked for and what was
measured, so its contract is testable as data. Whether the pixels match the plan is measured separately and
bounded, on real clips, in build/work/bb9/ -- see the ledger -- and deliberately not asserted here.
"""
import json
import queue
import subprocess

import pytest

from tekken_vod_helper import app as app_module
from tekken_vod_helper import ffmpeg_tools
from tekken_vod_helper.app import TekkenVodHelperApp, cut_mode_disclosure, receipt_path_for
from tekken_vod_helper.ffmpeg_tools import FfmpegError, slice_clip
from tekken_vod_helper.models import ExportJob, MatchSegment, ProjectState

# The planned start that exposed the rounding defect: its exact presentation time on the BB9 master is
# 3040.036840166... s, so three decimals rounded it *up* past the frame and the clip opened one frame late.
PLANNED_START = 3040.036840
PLANNED_END = 3042.038840


def stub_ffmpeg(monkeypatch, fail_first=None):
    """Replace ffmpeg with a recorder. Returns the commands run, in order."""
    commands = []

    def fake_run(command):
        commands.append([str(part) for part in command])
        if fail_first is not None and len(commands) == fail_first:
            raise FfmpegError("simulated ffmpeg failure")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ffmpeg_tools, "_run", fake_run)
    monkeypatch.setattr(ffmpeg_tools, "require_tool", lambda configured, name: "/usr/bin/" + name)
    monkeypatch.setattr(ffmpeg_tools, "_mux_times",
                        lambda output_path, ffprobe_path="": {"available": False, "why": "stubbed probe"})
    return commands


def make_app(state):
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.project_state = state
    app.log_messages = []
    app.log = app.log_messages.append
    app._thread_log = app.log_messages.append
    app.log_queue = queue.Queue()
    return app


def make_job(clip_path, index=1):
    match = MatchSegment(start=PLANNED_START, end=PLANNED_END, player1="Alice", player2="Bob")
    return ExportJob(
        match=match,
        index=index,
        upload_id="tvh-001-test{:02d}".format(index),
        start=match.start,
        end=match.end,
        folder_name="match_{:02d}".format(index),
        clip_path=str(clip_path),
        thumbnail_path=str(clip_path.parent / "thumbnail.jpg"),
        metadata_path=str(clip_path.parent / "match.json"),
        youtube_metadata_path=str(clip_path.parent / "youtube.json"),
        title_path=str(clip_path.parent / "title.txt"),
        description_path=str(clip_path.parent / "description.txt"),
    )


def arg_after(command, flag):
    return command[command.index(flag) + 1]


# --------------------------------------------------------------------------- receipt content


def test_stream_copy_receipt_reports_the_copy_constraint_and_never_a_promise(tmp_path, monkeypatch):
    commands = stub_ffmpeg(monkeypatch)
    receipt = slice_clip("/master.mp4", PLANNED_START, PLANNED_END, str(tmp_path / "c.mp4"), "", False)

    assert commands, "slice_clip ran no command"
    assert receipt["requested_cut_mode"] == "stream-copy" == receipt["effective_cut_mode"]
    assert receipt["fallback_reencode_used"] is False and receipt["fallback_reason"] is None
    assert receipt["frame_exact_start_intended"] is False
    assert "keyframe" in receipt["known_constraint"]
    assert receipt["delivered_source_range_verified"] is False
    assert receipt["requested_bounds_seconds"] == {"start": PLANNED_START, "end": PLANNED_END,
                                                  "duration": 2.002}
    assert "copy" in commands[0]
    # The seek must carry the planned instant, not a value rounded to milliseconds.
    assert arg_after(commands[0], "-ss") == "3040.036840"
    assert arg_after(commands[0], "-t") == "2.002000"


def test_reencode_receipt_keeps_intended_cut_apart_from_measured_accuracy(tmp_path, monkeypatch):
    commands = stub_ffmpeg(monkeypatch)
    lines = []
    receipt = slice_clip("/master.mp4", PLANNED_START, PLANNED_END, str(tmp_path / "c.mp4"), "", True,
                         logger=lines.append)

    assert receipt["frame_exact_start_intended"] is True
    assert receipt["known_constraint"] is None
    # The two fields that used to contradict each other now have to agree: an encoder flag is an intention.
    assert receipt["delivered_source_range_verified"] is False
    assert "not measured" in receipt["verification"]
    assert "libx264" in commands[0]
    joined = " ".join(lines).lower()
    assert "frame-exact as requested" not in joined, lines
    assert "not measured" in joined, lines


def test_failed_stream_copy_retries_with_reencode_and_describes_the_mode_that_ran(tmp_path, monkeypatch):
    """The fallback used to raise NameError, and its receipt blamed GOPs on a re-encoded clip.

    A copy can fail on a source whose keyframes are farther apart than the clip, which is exactly when a
    reviewer most needs the receipt to say which mode produced the file.
    """
    commands = stub_ffmpeg(monkeypatch, fail_first=1)
    receipt = slice_clip("/master.mp4", PLANNED_START, PLANNED_END, str(tmp_path / "c.mp4"), "", False)

    assert len(commands) == 2, "the failed copy was not retried: {}".format(commands)
    assert "copy" in commands[0] and "libx264" in commands[1]
    assert receipt["requested_cut_mode"] == "stream-copy"
    assert receipt["effective_cut_mode"] == "re-encode (after a failed stream copy)"
    assert receipt["fallback_reencode_used"] is True
    assert "simulated ffmpeg failure" in receipt["fallback_reason"]
    assert receipt["frame_exact_start_intended"] is True
    assert receipt["known_constraint"] is None, "the copy's constraint was attached to a re-encoded clip"


def test_reencode_failure_is_raised_rather_than_retried(tmp_path, monkeypatch):
    commands = stub_ffmpeg(monkeypatch, fail_first=1)
    with pytest.raises(FfmpegError, match="simulated ffmpeg failure"):
        slice_clip("/master.mp4", PLANNED_START, PLANNED_END, str(tmp_path / "c.mp4"), "", True)
    assert len(commands) == 1, "a re-encode must not be silently retried as anything"


def test_no_receipt_field_claims_the_delivered_range_is_known(tmp_path, monkeypatch):
    """Whatever the mode, a normal export measures nothing about the footage it delivered."""
    stub_ffmpeg(monkeypatch)
    for reencode in (False, True):
        receipt = slice_clip("/master.mp4", PLANNED_START, PLANNED_END, str(tmp_path / "c.mp4"), "", reencode)
        serialised = json.dumps(receipt, sort_keys=True).lower()
        assert receipt["delivered_source_range_verified"] is False
        assert "verified" not in serialised.replace("delivered_source_range_verified", "")
        assert "_promised" not in serialised, "a promise field crept back into the receipt"


# --------------------------------------------------------------------------- seek arithmetic


def test_seek_argument_lands_on_the_frame_tick_it_names():
    """Six places is justified by this bound, not by being 'small'.

    ffmpeg parses a decimal seek as microseconds and rescales to the stream timebase rounding to nearest, so
    the integer tick decides which frame is at or after the target. On the BB9 master (19001 Hz timescale,
    317 ticks per frame) the worst six-place error is 0.0095 of a tick, so every frame time on the master's
    grid survives the round trip as its own tick. Three places did not: the defect instant lands 19 ticks
    above its frame, which is the measured one-frame-late cut.
    """
    denom = 19001
    checked = 0
    for frame in range(0, 415_290, 7):
        exact_seconds = frame * 317 / denom
        microseconds = round(float(ffmpeg_tools._seconds(exact_seconds)) * 1_000_000)
        tick = (microseconds * denom + 500_000) // 1_000_000  # av_rescale_q, rounding to nearest
        assert tick == frame * 317, (frame, ffmpeg_tools._seconds(exact_seconds))
        checked += 1
    assert checked > 50_000, "the grid was barely sampled, so this proves nothing"


# --------------------------------------------------------------------------- container probe


def test_container_probe_uses_the_configured_ffprobe(tmp_path, monkeypatch):
    """The probe must follow the same tool resolution as the cut, or a bundled ffmpeg setup loses it."""
    custom = tmp_path / "ffprobe"
    custom.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    seen = {}

    def fake_run(command, **kwargs):
        seen["tool"] = command[0]
        return subprocess.CompletedProcess(command, 0, "0.000000\n2.002000\n", "")

    monkeypatch.setattr(ffmpeg_tools.subprocess, "run", fake_run)
    block = ffmpeg_tools._mux_times(str(tmp_path / "c.mp4"), str(custom))
    assert seen["tool"] == str(custom)
    assert block["available"] is True
    assert block["first_pts_seconds"] == 0.0 and block["last_pts_seconds"] == 2.002
    assert "clip-local" in block["meaning"]


def test_container_probe_reports_a_missing_ffprobe_instead_of_blank_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, "resolve_tool", lambda configured, name: None)
    block = ffmpeg_tools._mux_times(str(tmp_path / "c.mp4"), "")
    assert block["available"] is False
    assert "ffprobe" in block["why"]


def test_container_probe_reports_a_failed_probe(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr(ffmpeg_tools, "resolve_tool", lambda configured, name: "/usr/bin/" + name)
    monkeypatch.setattr(ffmpeg_tools.subprocess, "run", fake_run)
    block = ffmpeg_tools._mux_times(str(tmp_path / "c.mp4"), "")
    assert block == {"available": False, "why": "ffprobe exited 1"}


# --------------------------------------------------------------------------- worker persistence


def test_export_worker_writes_one_receipt_per_clip_into_the_upload_folder(tmp_path, monkeypatch):
    """Clips are written flat into one folder, so a shared receipt filename is a lost promise.

    Two jobs in the same export used to overwrite the same `cut-receipt.json`, leaving the last clip's
    receipt as the only evidence for both.
    """
    state = ProjectState(video_path="/master.mp4", reencode=False, ffprobe_path="/custom/ffprobe")
    app = make_app(state)
    upload_root = tmp_path / "out" / "_youtube_uploads"
    jobs = [make_job(upload_root / "tvh-001-a.mp4", 1), make_job(upload_root / "tvh-001-b.mp4", 2)]
    kwargs = {}

    def fake_slice(video_path, start, end, output_path, ffmpeg_path, reencode, logger=None, ffprobe_path=""):
        kwargs["ffprobe_path"] = ffprobe_path
        return {"effective_cut_mode": "stream-copy", "clip": output_path,
                "delivered_source_range_verified": False}

    monkeypatch.setattr(app_module, "slice_clip", fake_slice)
    app._write_export_artifacts = lambda job: None
    app._write_youtube_manifest = lambda jobs, root: None

    app._export_worker(jobs, str(tmp_path / "out"))

    written = sorted(path.name for path in upload_root.glob("*.cut-receipt.json"))
    assert len(written) == 2, written
    contents = [json.loads((upload_root / name).read_text(encoding="utf-8")) for name in written]
    assert sorted(row["clip"] for row in contents) == sorted(job.clip_path for job in jobs)
    assert kwargs["ffprobe_path"] == "/custom/ffprobe"
    assert app.log_queue.queue[-1][0] == "done"


def test_export_worker_ignores_a_legacy_receipt_without_failing(tmp_path, monkeypatch):
    state = ProjectState(video_path="/master.mp4")
    app = make_app(state)
    job = make_job(tmp_path / "out" / "_youtube_uploads" / "tvh-001-a.mp4")
    monkeypatch.setattr(app_module, "slice_clip",
                        lambda *a, **k: None)  # an older slice_clip returned nothing
    app._write_export_artifacts = lambda job: None
    app._write_youtube_manifest = lambda jobs, root: None

    app._export_worker([job], str(tmp_path / "out"))

    assert not list((tmp_path / "out").rglob("*.cut-receipt.json"))
    assert app.log_queue.queue[-1][0] == "done"


def test_an_unwritable_receipt_is_logged_and_does_not_lose_the_export(tmp_path, monkeypatch):
    state = ProjectState(video_path="/master.mp4")
    app = make_app(state)
    upload_root = tmp_path / "out" / "_youtube_uploads"
    upload_root.mkdir(parents=True)
    job = make_job(upload_root / "tvh-001-a.mp4")
    (upload_root / "tvh-001-a.cut-receipt.json").mkdir()  # the receipt path exists as a directory
    monkeypatch.setattr(app_module, "slice_clip", lambda *a, **k: {"effective_cut_mode": "stream-copy"})
    app._write_export_artifacts = lambda job: None
    app._write_youtube_manifest = lambda jobs, root: None

    app._export_worker([job], str(tmp_path / "out"))

    assert any("cut receipt" in line for line in app.log_messages), app.log_messages
    assert app.log_queue.queue[-1][0] == "done"


# --------------------------------------------------------------------------- plan wording


def test_cut_mode_disclosure_states_what_copy_cannot_do():
    copy_line = cut_mode_disclosure(False)
    reencode_line = cut_mode_disclosure(True)
    assert "keyframe" in copy_line and "re-encode" in copy_line.lower()
    assert "measured" not in copy_line.lower().replace("not measured", "")
    assert "keyframe" not in reencode_line
    assert "intended" in reencode_line, "a re-encode is an intention until something measures it"


def test_receipt_path_is_unique_per_clip():
    root = "/out/_youtube_uploads"
    first = receipt_path_for(make_job(app_module.Path(root) / "tvh-001-a.mp4", 1))
    second = receipt_path_for(make_job(app_module.Path(root) / "tvh-001-b.mp4", 2))
    assert first != second
    assert first.startswith(root) and first.endswith(".cut-receipt.json")
