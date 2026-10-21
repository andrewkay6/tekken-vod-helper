import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tekken_vod_helper.ffmpeg_tools import extract_frame
from tekken_vod_helper.match_analyzer import (  # noqa: E402
    _best_reference_score,
    _center_dialog_similarity,
    _load_references,
    _stage_select_similarity,
    identify_frame_state,
)
from tekken_vod_helper.thumbnails import Image  # noqa: E402
from tekken_vod_helper.util import seconds_to_timestamp  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--step", type=float, default=5.0)
    parser.add_argument("--start-reference", required=True)
    parser.add_argument("--end-reference", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    frame_dir = output_dir / "candidate_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    start_refs = _load_references(args.start_reference)
    end_refs = _load_references(args.end_reference)
    rows = []
    with tempfile.TemporaryDirectory(prefix="tekken_window_scan_") as temp_dir:
        temp_root = Path(temp_dir)
        timestamp = args.start
        index = 0
        while timestamp <= args.end + 0.001:
            frame_path = temp_root / "frame-{:.3f}.png".format(timestamp)
            extract_frame(args.video, timestamp, str(frame_path), args.ffmpeg, width=960)
            with Image.open(str(frame_path)) as raw_frame:
                frame = raw_frame.convert("RGB")
                identifier = identify_frame_state(frame, start_refs)
                start_score = _best_reference_score(frame, start_refs, _stage_select_similarity)
                end_score = _best_reference_score(frame, end_refs, _center_dialog_similarity)
            rows.append(
                {
                    "timestamp": round(timestamp, 3),
                    "timestamp_label": seconds_to_timestamp(timestamp),
                    "state": identifier.state,
                    "confidence": identifier.confidence,
                    "start_score": start_score,
                    "end_score": end_score,
                    "features": identifier.features,
                }
            )
            index += 1
            if index % 100 == 0:
                print("scanned {} frames; latest {}".format(index, seconds_to_timestamp(timestamp)), flush=True)
            timestamp += args.step

    top_start = sorted(rows, key=lambda item: item["start_score"], reverse=True)[: args.top]
    top_end = sorted(rows, key=lambda item: item["end_score"], reverse=True)[: args.top]
    for kind, candidates in (("start", top_start), ("end", top_end)):
        for rank, row in enumerate(candidates, start=1):
            frame_path = frame_dir / "{}-{:02d}-{}.png".format(
                kind,
                rank,
                row["timestamp_label"].replace(":", "-"),
            )
            extract_frame(args.video, row["timestamp"], str(frame_path), args.ffmpeg, width=960)
            row["frame_path"] = str(frame_path)

    report = {
        "window": {
            "start": args.start,
            "start_label": seconds_to_timestamp(args.start),
            "end": args.end,
            "end_label": seconds_to_timestamp(args.end),
            "step": args.step,
        },
        "rows": rows,
        "top_start": top_start,
        "top_end": top_end,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "window-scan-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Wrote {}".format(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
