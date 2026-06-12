import json
import shutil
import subprocess
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
    return shutil.which(executable_name)


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
        "{:.3f}".format(max(timestamp, 0.0)),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        "scale={}:-2".format(width),
        output_path,
    ]
    _run(command)


def slice_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    ffmpeg_path: str,
    reencode: bool,
    logger: Optional[Callable[[str], None]] = None,
) -> None:
    ffmpeg = require_tool(ffmpeg_path, "ffmpeg")
    duration = max(end - start, 0.001)
    if logger:
        logger(
            "Cutting {} to {} -> {}".format(
                seconds_to_timestamp(start), seconds_to_timestamp(end), output_path
            )
        )

    command = _slice_command(ffmpeg, video_path, start, duration, output_path, reencode)
    try:
        _run(command)
    except FfmpegError:
        if reencode:
            raise
        if logger:
            logger("Stream copy failed; retrying this clip with re-encode.")
        fallback = _slice_command(ffmpeg, video_path, start, duration, output_path, True)
        _run(fallback)


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
        "{:.3f}".format(start),
        "-i",
        video_path,
        "-t",
        "{:.3f}".format(duration),
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
