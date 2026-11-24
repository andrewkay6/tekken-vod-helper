"""Inspect and repair the HEVC/MKV frame-rate metadata seen in Source Record.

The intended rate is supplied by the operator/configuration, never guessed by
halving a header. A repaired copy is published only after checking timestamps,
non-video packet payloads, decoded pictures, and the unchanged original.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import statistics
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg_tools import FfmpegError, require_tool


class RecordingMediaError(ValueError):
    pass


def frame_rate(value) -> Fraction:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise RecordingMediaError("Expected a positive frame rate, such as 30000/1001.") from exc
    if rate <= 0:
        raise RecordingMediaError("Expected a positive frame rate.")
    return rate


def _tool(configured, name):
    try:
        return require_tool(configured, name)
    except FfmpegError as exc:
        raise RecordingMediaError(str(exc)) from exc


def _run(command, timeout=3600):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecordingMediaError("Media tool failed: %s" % exc) from exc
    if result.returncode:
        raise RecordingMediaError("Media tool exited %s: %s" % (result.returncode, result.stderr[-2000:]))
    return result.stdout


def _fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _probe(path, ffprobe):
    tool = _tool(ffprobe, "ffprobe")
    text = _run([tool, "-v", "error", "-show_format", "-show_streams", "-show_packets",
                 "-show_data_hash", "sha256", "-show_entries",
                 "format=format_name,duration:stream=index,codec_type,codec_name,width,height,"
                 "r_frame_rate,avg_frame_rate,time_base:packet=stream_index,pts,data_hash",
                 "-of", "json", str(path)])
    try:
        return json.loads(text)
    except ValueError as exc:
        raise RecordingMediaError("ffprobe did not return valid JSON.") from exc


def _video(document):
    videos = [s for s in document.get("streams", []) if s.get("codec_type") == "video"]
    if len(videos) != 1:
        raise RecordingMediaError("Exactly one video stream is required.")
    return videos[0]


def _packet_signatures(document):
    streams = {s["index"]: s for s in document["streams"]}
    signatures = {}
    for index in streams:
        signatures[index] = {"pts": hashlib.sha256(), "payload": hashlib.sha256(), "packets": 0}
    for packet in document.get("packets", []):
        index = packet["stream_index"]
        try:
            pts = Fraction(int(packet["pts"])) * Fraction(streams[index]["time_base"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise RecordingMediaError("A packet has no usable presentation timestamp.") from exc
        item = signatures[index]
        item["pts"].update((str(pts) + "\n").encode())
        if streams[index]["codec_type"] != "video":
            payload = packet.get("data_hash")
            if not payload:
                raise RecordingMediaError("Cannot verify a non-video packet's payload.")
            item["payload"].update((payload + "\n").encode())
        item["packets"] += 1
    return {str(index): {"codec_type": streams[index]["codec_type"],
                         "codec_name": streams[index]["codec_name"],
                         "packets": item["packets"], "width": streams[index].get("width"),
                         "height": streams[index].get("height"), "pts_sha256": item["pts"].hexdigest(),
                         "payload_sha256": item["payload"].hexdigest()
                         if streams[index]["codec_type"] != "video" else None}
            for index, item in signatures.items()}


def cadence(document, expected_fps):
    """Compare actual packet presentation times with the supplied constant-rate grid."""
    rate = frame_rate(expected_fps)
    video = _video(document)
    timebase = Fraction(video["time_base"])
    try:
        pts = sorted(Fraction(int(p["pts"])) * timebase for p in document.get("packets", [])
                     if p["stream_index"] == video["index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordingMediaError("Video packets need presentation timestamps.") from exc
    if len(pts) < 12 or len(set(pts)) != len(pts):
        raise RecordingMediaError("At least 12 distinct video presentation timestamps are required.")
    tolerance = max(2 * timebase, Fraction(1, 8) / rate)
    slots = [round((at-pts[0])*rate) for at in pts]
    steps = [b-a for a,b in zip(slots, slots[1:])]
    error = max(abs((at-pts[0])-Fraction(slot,1)/rate) for at,slot in zip(pts,slots))
    actual = Fraction(len(pts)-1, 1) / (pts[-1]-pts[0])
    # A genuine gap must stay a gap. Changing a rate header is safe only when
    # timestamps fit the intended grid and the ordinary interval is one slot.
    matches = error <= tolerance and min(steps) >= 1 and statistics.median(steps) == 1
    missing = sum(max(0,step-1) for step in steps)
    return {"expected_fps": str(rate), "observed_fps": float(actual),
            "video_packets": len(pts), "maximum_grid_error_seconds": float(error),
            "allowed_grid_error_seconds": float(tolerance), "matches": matches,
            "unfilled_frame_slots": missing, "all_slots_present": missing == 0,
            "maximum_slot_step": max(steps),
            "note": "Unfilled presentation slots are retained, not repaired or attributed to an encoder."}



def _assessment(document, expected_fps):
    video = _video(document)
    if video.get("codec_name") != "hevc" or "matroska" not in document.get("format", {}).get("format_name", ""):
        raise RecordingMediaError("This repair is validated only for HEVC in Matroska (.mkv).")
    rate = frame_rate(expected_fps)
    timing = cadence(document, rate)
    rates = {key: video.get(key) for key in ("r_frame_rate", "avg_frame_rate")}
    def agrees(value):
        try:
            return abs(frame_rate(value) / rate - 1) < Fraction(1, 100000)
        except RecordingMediaError:
            return False
    return {"expected_fps": str(rate), "declared_rates": rates, "cadence": timing,
            "needs_repair": not all(agrees(value) for value in rates.values()),
            "container_duration_seconds": document.get("format", {}).get("duration")}


def inspect_recording(path, expected_fps, *, ffprobe=""):
    source = Path(path).resolve(strict=True)
    return _assessment(_probe(source, ffprobe), expected_fps)


def _pictures(path, ffmpeg):
    tool = _tool(ffmpeg, "ffmpeg")
    text = _run([tool, "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
                 "-threads", "2", "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn",
                 "-fps_mode", "passthrough", "-f", "framemd5", "-"])
    digest, count = hashlib.sha256(), 0
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        value = line.rsplit(",", 1)[-1].strip()
        if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
            raise RecordingMediaError("Unexpected decoded-picture hash output.")
        digest.update(bytes.fromhex(value)); count += 1
    if not count:
        raise RecordingMediaError("No decoded video pictures were verified.")
    return {"frames": count, "ordered_picture_hashes_sha256": digest.hexdigest()}


def _temporary(parent, suffix):
    fd, name = tempfile.mkstemp(prefix=".tvh-metadata-", suffix=suffix, dir=parent)
    os.close(fd)
    return Path(name)


def repair_recording(path, output, expected_fps, *, receipt=None, ffmpeg="", ffprobe="",
                     logger: Optional[Callable[[str], None]] = None):
    source = Path(path).resolve(strict=True)
    destination = Path(output).resolve()
    sidecar = Path(receipt).resolve() if receipt else destination.with_name(destination.name + ".metadata-repair.json")
    if source.suffix.lower() != ".mkv" or destination.suffix.lower() != ".mkv":
        raise RecordingMediaError("Input and repaired copy must both be .mkv files.")
    if len({source, destination, sidecar}) != 3:
        raise RecordingMediaError("Input, repaired copy and receipt must have different paths.")
    if destination.exists() or sidecar.exists():
        raise RecordingMediaError("Refusing to overwrite an existing output or receipt.")
    rate = frame_rate(expected_fps)
    tell = logger or (lambda _message: None)
    tell("Inspecting the original packet timing.")
    original = _fingerprint(source)
    before = _probe(source, ffprobe)
    assessment = _assessment(before, rate)
    if not assessment["cadence"]["matches"]:
        raise RecordingMediaError("Packet timing does not match the intended rate; metadata repair would hide a timing problem.")
    if not assessment["needs_repair"]:
        raise RecordingMediaError("The declared rate already matches; no repair is needed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    staged, staged_receipt = _temporary(destination.parent, ".mkv"), None
    published = False
    try:
        tell("Writing a metadata-only copy; the original is untouched.")
        tool = _tool(ffmpeg, "ffmpeg")
        _run([tool, "-v", "error", "-nostdin", "-y", "-copyts", "-i", str(source),
              "-map", "0", "-c", "copy", "-r", str(rate), "-bsf:v",
              "hevc_metadata=tick_rate=" + str(rate), "-avoid_negative_ts", "disabled", str(staged)])
        after = _probe(staged, ffprobe)
        repaired = _assessment(after, rate)
        if repaired["needs_repair"] or not repaired["cadence"]["matches"]:
            raise RecordingMediaError("The repaired copy did not retain the intended rate and packet cadence.")
        signatures = _packet_signatures(before)
        if signatures != _packet_signatures(after):
            raise RecordingMediaError("Presentation timestamps, streams, or non-video packet payloads changed.")
        tell("Verifying every decoded picture in both files.")
        pictures = _pictures(source, ffmpeg)
        if pictures != _pictures(staged, ffmpeg):
            raise RecordingMediaError("Decoded video pictures changed.")
        if _fingerprint(source) != original:
            raise RecordingMediaError("The original changed during verification; no repaired output was published.")
        result = {"kind": "tvh-recording-metadata-repair", "schema_version": 1,
                  "source": original, "output": dict(_fingerprint(staged), name=destination.name),
                  "before": assessment, "after": repaired, "stream_copy": True,
                  "presentation_timestamps_preserved": True, "non_video_payloads_preserved": True,
                  "decoded_pictures_preserved": True, "decoded_pictures": pictures,
                  "source_unchanged": True, "streams": signatures,
                  "notes": ["Packet interleaving and inferred DTS can differ after remux.",
                            "Container duration can change with the corrected nominal final-frame duration.",
                            "This verifies metadata repair, not synchronization between separate recordings."]}
        staged_receipt = _temporary(sidecar.parent, ".json")
        with staged_receipt.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True); handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        # Hard-link publication refuses a destination created concurrently; replace() would overwrite it.
        os.link(staged, destination); published = True
        os.link(staged_receipt, sidecar)
        return result
    except BaseException:
        if published and destination.exists() and os.path.samefile(staged, destination):
            destination.unlink()  # Only the new copy created by this invocation.
        raise
    finally:
        staged.unlink(missing_ok=True)
        if staged_receipt is not None:
            staged_receipt.unlink(missing_ok=True)
