import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from .util import seconds_to_timestamp


class FfmpegError(RuntimeError):
    pass


def resolve_tool(configured_path: str, executable_name: str) -> Optional[str]:
    configured_path = (configured_path or "").strip().strip('"')
    if configured_path:
        candidate = Path(configured_path)
        if candidate.exists():
            return str(candidate)
    local = _resolve_local_tool(executable_name)
    if local is not None:
        return str(local)
    return shutil.which(executable_name)


def _resolve_local_tool(executable_name: str) -> Optional[Path]:
    candidates = []
    names = [executable_name]
    if not executable_name.lower().endswith(".exe"):
        names.append(executable_name + ".exe")

    runtime_root = Path(sys.executable).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", runtime_root)).resolve()
    source_root = Path(__file__).resolve().parents[1]

    for root in (runtime_root, bundle_root, source_root):
        for name in names:
            candidates.append(root / "ffmpeg" / name)
            candidates.append(root / name)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def require_tool(configured_path: str, executable_name: str) -> str:
    resolved = resolve_tool(configured_path, executable_name)
    if not resolved:
        raise FfmpegError(
            "{} was not found. Install FFmpeg or browse to {}.exe in the app.".format(
                executable_name, executable_name
            )
        )
    return resolved


def probe_duration(video_path: str, ffprobe_path: str) -> float:
    ffprobe = require_tool(ffprobe_path, "ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        video_path,
    ]
    completed = _run(command)
    payload = json.loads(completed.stdout or "{}")
    duration = payload.get("format", {}).get("duration")
    if duration is None:
        raise FfmpegError("ffprobe did not return a video duration.")
    return float(duration)


def extract_frame(
    video_path: str,
    timestamp: float,
    output_path: str,
    ffmpeg_path: str,
    width: int = 960,
) -> None:
    ffmpeg = require_tool(ffmpeg_path, "ffmpeg")
    command = [
        ffmpeg,
        "-y",
        "-ss",
        # Same defect as the cut: three places can round past the frame a caller named, and this function
        # feeds boundary crops to the vision route, where one frame is the difference between a round-open
        # banner and the HUD after it.
        _seconds(max(timestamp, 0.0)),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        "scale={}:-2".format(width),
        output_path,
    ]
    _run(command)


#: Decimal places used when a time is handed to ffmpeg as a seek or duration argument.
#:
#: Three places rounded the planned start 3040.036840 up to 3040.037 -- which is *after* that frame's own
#: presentation time -- and an input-side `-ss` delivers frames at or after the target, so the intended
#: frame was skipped and the clip opened one frame late. Measured on the BB9 master: `-ss 3040.037` delivers
#: a picture 21.0 dB from the requested frame, while `-ss 3040.036840` delivers that frame itself (41.1 dB,
#: indistinguishable from a self-comparison).
#:
#: Why six places is enough here, and where that stops being true. A cut argument is only ever compared
#: against an integer presentation time in the stream's own timescale: ffmpeg reads the decimal as
#: microseconds and rescales to the stream timebase, rounding to nearest. Measured over every 7th frame time
#: of the BB9 master's grid (59,328 instants, 1/19001 s timescale, 317 ticks per frame), three places put
#: the target above its own frame's tick 47.4% of the time, which skips that frame. Six places put
#: every target on its own tick, 0 skips; five was already clean. That is the tested precision: a source
#: whose timescale is coarser than about 1 MHz, or a caller naming an instant that is not a frame's
#: presentation time, sits outside what was measured, and none of it says whether the *content* delivered
#: matches the plan -- a re-encode can be wrong for reasons unrelated to seek arithmetic.
TIME_DECIMALS = 6


def _seconds(value: float) -> str:
    return "{:.{d}f}".format(float(value), d=TIME_DECIMALS)


def _mux_times(output_path: str, ffprobe_path: str):
    """First and last video packet times of the finished clip, as the container reports them.

    These are clip-local mux timestamps, not source presentation times: a copy is rebased by the muxer and
    an edit list can move the origin again. They are recorded because a clip whose packet span is nothing
    like the requested duration is a defect worth seeing in the log; they are never presented as proof of
    which source frames the clip displays.
    """
    probe_tool = resolve_tool(ffprobe_path, "ffprobe")
    if not probe_tool:
        return {"available": False, "why": "ffprobe was not found, so the delivered container was not probed"}
    try:
        probe = subprocess.run(
            [probe_tool, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", output_path],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        if probe.returncode != 0:
            return {"available": False, "why": "ffprobe exited %d" % probe.returncode}
        stamps = [float(line) for line in (probe.stdout or "").splitlines() if line.strip()
                  not in ("N/A", "")]
        if not stamps:
            return {"available": False, "why": "no video packets read from the delivered clip"}
        return {"available": True, "first_pts_seconds": round(min(stamps), 6),
                "last_pts_seconds": round(max(stamps), 6), "packet_count": len(stamps),
                "meaning": "clip-local mux timestamps, not verified source frames"}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"available": False, "why": str(exc)[:160]}


def slice_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    ffmpeg_path: str,
    reencode: bool,
    logger: Optional[Callable[[str], None]] = None,
    ffprobe_path: str = "",
) -> dict:
    ffmpeg = require_tool(ffmpeg_path, "ffmpeg")
    duration = max(end - start, 0.001)
    if logger:
        logger(
            "Cutting {} to {} -> {}".format(
                seconds_to_timestamp(start), seconds_to_timestamp(end), output_path
            )
        )

    requested_mode = "re-encode" if reencode else "stream-copy"
    command = _slice_command(ffmpeg, video_path, start, duration, output_path, reencode)
    fallback_reason = None
    try:
        _run(command)
    except FfmpegError as exc:
        if reencode:
            raise
        fallback_reason = str(exc)[:400]
        if logger:
            logger("Stream copy failed; retrying this clip with re-encode.")
        command = _slice_command(ffmpeg, video_path, start, duration, output_path, True)
        _run(command)

    # A receipt, so a reviewer is not left believing a clip begins where the plan said. It records three
    # different things that must not be confused: what the caller asked for, what the mode now in force is
    # intended to deliver, and what was actually measured (nothing -- the source range of a delivered clip
    # is only known by matching its pictures against the master, which a normal export does not do). Callers
    # that ignored the return value keep working unchanged.
    reencode_effective = reencode or fallback_reason is not None
    receipt = {
        "requested_bounds_seconds": {"start": float(start), "end": float(end),
                                     "duration": round(duration, 9)},
        "requested_cut_mode": requested_mode,
        "effective_cut_mode": (requested_mode if fallback_reason is None
                               else "re-encode (after a failed stream copy)"),
        "fallback_reencode_used": fallback_reason is not None,
        "fallback_reason": fallback_reason,
        # The selection convention, which is a convention and not a measurement. A plan's bound is one
        # frame's presentation time; a clip is made of whole frames, so a request between two frames
        # selects one of them and no amount of decimal places changes that.
        "intended_cut_behavior": (
            "start: the first frame whose presentation time is at or after the requested start; "
            "end: exclusive, so the last displayed frame is the one before the requested end, not the frame "
            "at it; a requested instant that is not a frame's presentation time selects a neighbour rather "
            "than an instant"),
        "frame_exact_start_intended": reencode_effective,
        "known_constraint": (
            None if reencode_effective else
            "a stream copy can only begin on a keyframe, so it is expected to start at or before the "
            "requested start -- up to one GOP early, and one GOP is a property of the source, not of this "
            "clip -- and to stop on a packet boundary that may start after the requested end"),
        "command": [str(part) for part in command],
        "delivered_container": _mux_times(output_path, ffprobe_path),
        # A re-encode intends an exact cut; intending one is not the same as having checked. Nothing here
        # has compared the delivered pictures with the master, in either mode.
        "delivered_source_range_verified": False,
        "verification": ("not measured: the container's own packet times are clip-local and say nothing "
                         "about which source frames are displayed"),
    }
    if logger:
        logger("Cut receipt: {}, requested {:.6f} -> {:.6f}; {}. Delivered source range not measured.".format(
            receipt["effective_cut_mode"], start, end,
            "intended to begin on the requested frame" if reencode_effective
            else "a copy begins on a keyframe, so it may open early"))
    return receipt


def _slice_command(
    ffmpeg: str,
    video_path: str,
    start: float,
    duration: float,
    output_path: str,
    reencode: bool,
) -> List[str]:
    command = [
        ffmpeg,
        "-y",
        "-ss",
        _seconds(start),
        "-i",
        video_path,
        "-t",
        _seconds(duration),
        "-map",
        "0",
    ]
    if reencode:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
            ]
        )
    else:
        command.extend(
            [
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                "-f",
                "mp4",
            ]
        )
    command.append(output_path)
    return command


def _run(command: List[str]) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise FfmpegError(stderr or "Command failed: {}".format(" ".join(command)))
    return completed
