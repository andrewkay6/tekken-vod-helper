import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tekken_vod_helper.ffmpeg_tools import extract_frame, probe_duration
from tekken_vod_helper.match_analyzer import (  # noqa: E402
    AnalyzerConfig,
    analyze_match_boundaries,
    identify_frame_state,
)
from tekken_vod_helper.models import MatchSegment, ProjectState  # noqa: E402
from tekken_vod_helper.thumbnails import Image  # noqa: E402
from tekken_vod_helper.util import seconds_to_timestamp  # noqa: E402


def main() -> int:
    args = _parse_args()

    if args.agentic:
        if not args.truth:
            raise SystemExit("--truth is required in agentic mode.")
        source = args.video or args.url
        if not source:
            raise SystemExit("--url or --video is required in agentic mode.")
        ffmpeg = args.ffmpeg or _require_command("ffmpeg")
        ffprobe = args.ffprobe or _require_command("ffprobe")
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        from tekken_vod_helper.boundary_workflow import backtest as _bw_backtest
        result = _bw_backtest(
            source, args.truth, str(out_dir),
            ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            yt_dlp=args.yt_dlp or "",
        )
        report_path = out_dir / "backtest-report.json"
        report_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print("Wrote {}".format(report_path))
        return 0

    if not args.start_reference:
        raise SystemExit("--start-reference is required in legacy mode.")
    if not args.end_reference:
        raise SystemExit("--end-reference is required in legacy mode.")

    ffmpeg = args.ffmpeg or _require_command("ffmpeg")
    ffprobe = args.ffprobe or _require_command("ffprobe")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.video:
        metadata = {
            "title": Path(args.video).name,
            "id": "",
            "webpage_url": "",
        }
        stream_url = args.video
    else:
        yt_dlp = args.yt_dlp or _require_command("yt-dlp")
        metadata = _youtube_metadata(yt_dlp, args.url)
        stream_url = _youtube_stream_url(yt_dlp, args.url, args.format)
    duration = float(metadata.get("duration") or 0.0)
    if duration <= 0:
        duration = probe_duration(stream_url, ffprobe)

    if args.timeline_scan:
        report = _timeline_scan(
            stream_url=stream_url,
            duration=duration,
            output_dir=output_dir,
            ffmpeg_path=ffmpeg,
            start_reference=args.start_reference,
            step=args.timeline_step,
            save_frames=args.timeline_save_frames,
        )
    elif args.harvest:
        report = _harvest_candidates(
            stream_url=stream_url,
            duration=duration,
            output_dir=output_dir,
            ffmpeg_path=ffmpeg,
            start_reference=args.start_reference,
            end_reference=args.end_reference,
            step=args.harvest_step,
            limit=args.harvest_limit,
        )
    else:
        if not args.project:
            raise SystemExit("--project is required unless --harvest or --timeline-scan is set.")
        report = _backtest_project(
            project_path=Path(args.project),
            stream_url=stream_url,
            duration=duration,
            output_dir=output_dir,
            ffmpeg_path=ffmpeg,
            start_reference=args.start_reference,
            end_reference=args.end_reference,
            save_candidate_frames=args.save_candidate_frames,
            first_matches=args.first_matches,
            match_index=args.match_index,
            progress_every=args.progress_every,
        )

    report["youtube"] = {
        "url": args.url,
        "video": args.video,
        "title": metadata.get("title", ""),
        "id": metadata.get("id", ""),
        "duration": duration,
        "duration_timestamp": seconds_to_timestamp(duration),
    }
    report_path = output_dir / "boundary-backtest-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Wrote {}".format(report_path))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest match boundary detection against a YouTube VOD without downloading the whole video."
    )
    parser.add_argument("--url", default="", help="YouTube full VOD URL.")
    parser.add_argument("--video", default="", help="Local full VOD file path. Skips yt-dlp when set.")
    parser.add_argument("--project", help="Optional .tvh.json project with rough match timestamps.")
    parser.add_argument("--start-reference", default="", help="Stage select reference screenshot (required in legacy mode).")
    parser.add_argument("--end-reference", default="", help="Press-start end reference screenshot (required in legacy mode).")
    parser.add_argument("--agentic", action="store_true", help="Use autonomous boundary detection (no reference images).")
    parser.add_argument("--truth", default="", help="Truth JSON path for scoring (required in agentic mode).")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "backtests" / "reports" / "latest"))
    parser.add_argument("--save-candidate-frames", action="store_true", help="Save detected start/end frames as small PNGs.")
    parser.add_argument("--first-matches", type=int, default=0, help="Only analyze the first N project matches.")
    parser.add_argument("--match-index", type=int, default=0, help="Only analyze one 1-based project match index.")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress every N extracted frames.")
    parser.add_argument("--harvest", action="store_true", help="Scan the whole VOD and save top candidate frames for review.")
    parser.add_argument("--harvest-step", type=float, default=20.0, help="Seconds between samples in --harvest mode.")
    parser.add_argument("--harvest-limit", type=int, default=30, help="Candidate frames to save per kind in --harvest mode.")
    parser.add_argument("--timeline-scan", action="store_true", help="Classify the whole VOD into state segments without project JSON.")
    parser.add_argument("--timeline-step", type=float, default=5.0, help="Seconds between samples in --timeline-scan mode.")
    parser.add_argument("--timeline-save-frames", action="store_true", help="Save timeline samples into dataset/predicted/<state> folders for labeling.")
    parser.add_argument("--format", default="bestvideo[height<=1440][ext=mp4]/best[ext=mp4]/best")
    parser.add_argument("--ffmpeg", default="")
    parser.add_argument("--ffprobe", default="")
    parser.add_argument("--yt-dlp", default="")
    return parser.parse_args()


def _backtest_project(
    project_path: Path,
    stream_url: str,
    duration: float,
    output_dir: Path,
    ffmpeg_path: str,
    start_reference: str,
    end_reference: str,
    save_candidate_frames: bool,
    first_matches: int,
    match_index: int,
    progress_every: int,
) -> Dict[str, object]:
    state = ProjectState.from_dict(json.loads(project_path.read_text(encoding="utf-8")))
    config = AnalyzerConfig(
        start_reference_path=start_reference,
        end_reference_path=end_reference,
    )
    start_references = None
    if save_candidate_frames:
        from tekken_vod_helper import match_analyzer

        start_references = match_analyzer._load_references(start_reference)
    all_matches = state.sorted_matches()
    indexed_matches = list(enumerate(all_matches))
    if match_index:
        if match_index < 1 or match_index > len(indexed_matches):
            raise SystemExit("--match-index must be between 1 and {}.".format(len(indexed_matches)))
        indexed_matches = [indexed_matches[match_index - 1]]
    elif first_matches:
        indexed_matches = indexed_matches[: max(first_matches, 0)]
    matches_to_analyze = [match for _source_index, match in indexed_matches]
    source_indices = [source_index for source_index, _match in indexed_matches]
    next_starts = [
        all_matches[source_index + 1].start if source_index + 1 < len(all_matches) else duration
        for source_index in source_indices
    ]
    print(
        "Analyzing {} of {} project match{}.".format(
            len(matches_to_analyze),
            len(all_matches),
            "" if len(matches_to_analyze) == 1 else "es",
        ),
        flush=True,
    )
    frame_count = {"value": 0}

    def extract_with_progress(timestamp: float, output_path: str) -> None:
        extract_frame(stream_url, timestamp, output_path, ffmpeg_path, width=960)
        frame_count["value"] += 1
        if progress_every > 0 and frame_count["value"] % progress_every == 0:
            print(
                "  extracted {:>4} frames; latest sample {}".format(
                    frame_count["value"],
                    seconds_to_timestamp(timestamp),
                ),
                flush=True,
            )

    proposals = analyze_match_boundaries(
        stream_url,
        duration or state.duration,
        matches_to_analyze,
        ffmpeg_path,
        config,
        logger=lambda message: print(message, flush=True),
        frame_extractor=extract_with_progress,
        next_starts=next_starts,
    )
    rows = []
    candidate_dir = output_dir / "candidate_frames"
    if save_candidate_frames:
        candidate_dir.mkdir(parents=True, exist_ok=True)
    matches = all_matches
    for proposal in proposals:
        source_index = source_indices[proposal.match_index]
        match = matches[source_index]
        manual_end = match.end
        next_start = matches[source_index + 1].start if source_index + 1 < len(matches) else duration
        row = {
            "match_index": source_index + 1,
            "players": "{} vs {}".format(match.player1 or "Player 1", match.player2 or "Player 2"),
            "round": match.round_name,
            "rough_start": match.start,
            "rough_start_timestamp": seconds_to_timestamp(match.start),
            "detected_start": proposal.proposed_start,
            "detected_start_timestamp": _optional_timestamp(proposal.proposed_start),
            "start_score": proposal.start_score,
            "manual_end": manual_end,
            "manual_end_timestamp": _optional_timestamp(manual_end),
            "next_start_end": next_start,
            "next_start_end_timestamp": seconds_to_timestamp(next_start),
            "detected_end": proposal.proposed_end,
            "detected_end_timestamp": _optional_timestamp(proposal.proposed_end),
            "end_score": proposal.end_score,
            "detected_vs_manual_end_delta": (
                proposal.proposed_end - manual_end
                if proposal.proposed_end is not None and manual_end is not None
                else None
            ),
        }
        if save_candidate_frames:
            start_frame_path = candidate_dir / "match-{:03d}-start.png".format(source_index + 1)
            end_frame_path = candidate_dir / "match-{:03d}-end.png".format(source_index + 1)
            _save_candidate_frame(stream_url, ffmpeg_path, proposal.proposed_start, start_frame_path)
            _save_candidate_frame(stream_url, ffmpeg_path, proposal.proposed_end, end_frame_path)
            row["detected_start_identifier"] = _identify_candidate_frame(start_frame_path, start_references)
            row["detected_end_identifier"] = _identify_candidate_frame(end_frame_path, start_references)
        rows.append(row)
    return {
        "mode": "project",
        "project": str(project_path),
        "event_name": state.event_name,
        "analyzed_project_matches": [source_index + 1 for source_index in source_indices],
        "extracted_frame_count": frame_count["value"],
        "matches": rows,
    }


def _harvest_candidates(
    stream_url: str,
    duration: float,
    output_dir: Path,
    ffmpeg_path: str,
    start_reference: str,
    end_reference: str,
    step: float,
    limit: int,
) -> Dict[str, object]:
    from tekken_vod_helper import match_analyzer

    start_refs = match_analyzer._load_references(start_reference)
    end_refs = match_analyzer._load_references(end_reference)
    samples: List[Dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="tekken_vod_harvest_") as temp_dir:
        temp_root = Path(temp_dir)
        timestamp = 0.0
        while timestamp <= duration:
            frame_path = temp_root / "sample-{:.3f}.png".format(timestamp)
            extract_frame(stream_url, timestamp, str(frame_path), ffmpeg_path, width=960)
            with Image.open(str(frame_path)) as raw_frame:
                frame = raw_frame.convert("RGB")
                start_score = match_analyzer._best_reference_score(frame, start_refs, match_analyzer._stage_select_similarity)
                end_score = match_analyzer._best_reference_score(frame, end_refs, match_analyzer._center_dialog_similarity)
            samples.append(
                {
                    "timestamp": timestamp,
                    "timestamp_text": seconds_to_timestamp(timestamp),
                    "start_score": start_score,
                    "end_score": end_score,
                }
            )
            timestamp += max(step, 1.0)

    start_top = sorted(samples, key=lambda item: item["start_score"], reverse=True)[:limit]
    end_top = sorted(samples, key=lambda item: item["end_score"], reverse=True)[:limit]
    candidate_dir = output_dir / "harvest_candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    _save_ranked_frames(stream_url, ffmpeg_path, candidate_dir / "start", start_top, "start")
    _save_ranked_frames(stream_url, ffmpeg_path, candidate_dir / "end", end_top, "end")
    return {
        "mode": "harvest",
        "sample_step": step,
        "sample_count": len(samples),
        "top_start_candidates": start_top,
        "top_end_candidates": end_top,
    }


def _timeline_scan(
    stream_url: str,
    duration: float,
    output_dir: Path,
    ffmpeg_path: str,
    start_reference: str,
    step: float,
    save_frames: bool,
) -> Dict[str, object]:
    from tekken_vod_helper import match_analyzer

    start_refs = match_analyzer._load_references(start_reference)
    samples: List[Dict[str, object]] = []
    dataset_dir = output_dir / "dataset" / "predicted"
    if save_frames:
        dataset_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tekken_vod_timeline_") as temp_dir:
        temp_root = Path(temp_dir)
        timestamp = 0.0
        count = 0
        while timestamp <= duration:
            frame_path = temp_root / "sample-{:.3f}.png".format(timestamp)
            extract_frame(stream_url, timestamp, str(frame_path), ffmpeg_path, width=960)
            with Image.open(str(frame_path)) as raw_frame:
                identifier = identify_frame_state(raw_frame.convert("RGB"), start_refs)
            frame_sample_path = None
            if save_frames:
                frame_sample_path = dataset_dir / identifier.state / "sample-{}.png".format(_filename_timestamp(timestamp))
                frame_sample_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(frame_path), str(frame_sample_path))
            samples.append(
                {
                    "timestamp": timestamp,
                    "timestamp_text": seconds_to_timestamp(timestamp),
                    "state": identifier.state,
                    "confidence": identifier.confidence,
                    "features": identifier.features,
                    "frame_path": str(frame_sample_path) if frame_sample_path else "",
                }
            )
            count += 1
            if count % 100 == 0:
                print(
                    "  scanned {:>4} frames; latest sample {}".format(
                        count,
                        seconds_to_timestamp(timestamp),
                    ),
                    flush=True,
                )
            timestamp += max(step, 1.0)

    segments = _state_segments(samples, max(step, 1.0))
    candidate_matches = _candidate_matches_from_segments(segments)
    return {
        "mode": "timeline_scan",
        "sample_step": max(step, 1.0),
        "sample_count": len(samples),
        "dataset_dir": str(dataset_dir) if save_frames else "",
        "segments": segments,
        "candidate_matches": candidate_matches,
        "samples": samples,
    }


def _state_segments(samples: List[Dict[str, object]], step: float) -> List[Dict[str, object]]:
    if not samples:
        return []
    segments: List[Dict[str, object]] = []
    current_state = str(samples[0]["state"])
    current_start = float(samples[0]["timestamp"])
    current_scores = [float(samples[0]["confidence"])]
    last_timestamp = current_start
    max_gap = max(step * 1.6, step + 0.01)
    for sample in samples[1:]:
        timestamp = float(sample["timestamp"])
        state = str(sample["state"])
        if state == current_state and timestamp - last_timestamp <= max_gap:
            current_scores.append(float(sample["confidence"]))
            last_timestamp = timestamp
            continue
        segments.append(_state_segment(current_state, current_start, last_timestamp, current_scores, step))
        current_state = state
        current_start = timestamp
        current_scores = [float(sample["confidence"])]
        last_timestamp = timestamp
    segments.append(_state_segment(current_state, current_start, last_timestamp, current_scores, step))
    return segments


def _state_segment(state: str, start: float, last_timestamp: float, scores: List[float], step: float) -> Dict[str, object]:
    end = last_timestamp + step
    return {
        "state": state,
        "start": start,
        "start_timestamp": seconds_to_timestamp(start),
        "end": end,
        "end_timestamp": seconds_to_timestamp(end),
        "duration": max(0.0, end - start),
        "samples": len(scores),
        "mean_confidence": sum(scores) / float(len(scores)) if scores else 0.0,
        "max_confidence": max(scores) if scores else 0.0,
    }


def _candidate_matches_from_segments(segments: List[Dict[str, object]]) -> List[Dict[str, object]]:
    candidates: List[Dict[str, object]] = []
    current: Optional[Dict[str, object]] = None
    setup_states = {"character_select", "stage_select", "controller_prompt", "vs_screen", "stage_intro"}
    end_states = {"post_match_menu", "controller_prompt", "character_select"}
    for segment in segments:
        state = str(segment["state"])
        if state in setup_states:
            if current is None:
                current = {
                    "setup_start": segment["start"],
                    "setup_start_timestamp": segment["start_timestamp"],
                    "states": [],
                }
            current["states"].append(state)
            if state == "stage_select":
                current["stage_select_start"] = segment["start"]
                current["stage_select_timestamp"] = segment["start_timestamp"]
            if state == "vs_screen":
                current["vs_start"] = segment["start"]
                current["vs_timestamp"] = segment["start_timestamp"]
        elif state == "gameplay":
            if current is None:
                current = {
                    "setup_start": segment["start"],
                    "setup_start_timestamp": segment["start_timestamp"],
                    "states": [],
                }
            current["gameplay_start"] = segment["start"]
            current["gameplay_start_timestamp"] = segment["start_timestamp"]
            current["states"].append(state)
        elif state in end_states and current and current.get("gameplay_start") is not None:
            current["end_start"] = segment["start"]
            current["end_start_timestamp"] = segment["start_timestamp"]
            current["end_state"] = state
            current["duration"] = float(segment["start"]) - float(current.get("stage_select_start") or current["setup_start"])
            candidates.append(current)
            current = {
                "setup_start": segment["start"],
                "setup_start_timestamp": segment["start_timestamp"],
                "states": [state],
            }
    if current and current.get("gameplay_start") is not None:
        current["end_start"] = None
        current["end_start_timestamp"] = None
        current["end_state"] = ""
        candidates.append(current)
    return candidates


def _save_ranked_frames(stream_url: str, ffmpeg_path: str, folder: Path, rows: List[Dict[str, object]], label: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for rank, row in enumerate(rows, start=1):
        timestamp = float(row["timestamp"])
        output_path = folder / "{:02d}-{}-{}.png".format(rank, label, _filename_timestamp(timestamp))
        _save_candidate_frame(stream_url, ffmpeg_path, timestamp, output_path)


def _save_candidate_frame(stream_url: str, ffmpeg_path: str, timestamp: Optional[float], output_path: Path) -> None:
    if timestamp is None:
        return
    extract_frame(stream_url, timestamp, str(output_path), ffmpeg_path, width=960)


def _identify_candidate_frame(path: Path, start_references) -> Optional[Dict[str, object]]:
    if not path.exists():
        return None
    with Image.open(str(path)) as raw_frame:
        identifier = identify_frame_state(raw_frame.convert("RGB"), start_references)
    return {
        "state": identifier.state,
        "confidence": identifier.confidence,
        "features": identifier.features,
    }


def _youtube_metadata(yt_dlp: str, url: str) -> Dict[str, object]:
    command = [yt_dlp, "--dump-single-json", "--no-playlist", url]
    completed = _run(command)
    return json.loads(completed.stdout or "{}")


def _youtube_stream_url(yt_dlp: str, url: str, fmt: str) -> str:
    command = [yt_dlp, "-f", fmt, "-g", "--no-playlist", url]
    completed = _run(command)
    urls = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not urls:
        raise SystemExit("yt-dlp did not return a playable stream URL.")
    return urls[0]


def _run(command: List[str]) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise SystemExit((completed.stderr or "Command failed: {}".format(" ".join(command))).strip())
    return completed


def _require_command(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(
            "{} was not found on PATH. Install it or pass --{} with an explicit path.".format(
                name,
                name,
            )
        )
    return resolved


def _optional_timestamp(value: Optional[float]) -> Optional[str]:
    return seconds_to_timestamp(value) if value is not None else None


def _filename_timestamp(seconds: float) -> str:
    return seconds_to_timestamp(seconds).replace(":", "-").replace(".", "-")


if __name__ == "__main__":
    raise SystemExit(main())
