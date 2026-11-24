"""Repair Source Record HEVC/MKV frame-rate headers into a verified, separate copy.

Example:
  .venv/bin/python scripts/repair_recording_metadata.py camera.mkv repaired.mkv --expected-fps 30000/1001
Use --inspect without an output to check the actual packet cadence first.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tekken_vod_helper.recording_media import RecordingMediaError, inspect_recording, repair_recording


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--expected-fps", required=True, help="Intended rate from the recording configuration, e.g. 30000/1001")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--ffmpeg", default="")
    parser.add_argument("--ffprobe", default="")
    args = parser.parse_args()
    if args.inspect and (args.output or args.receipt):
        parser.error("--inspect does not write an output or receipt")
    if not args.inspect and args.output is None:
        parser.error("supply an output path or use --inspect")
    try:
        if args.inspect:
            result = inspect_recording(args.input, args.expected_fps, ffprobe=args.ffprobe)
        else:
            result = repair_recording(args.input, args.output, args.expected_fps, receipt=args.receipt,
                                      ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                                      logger=lambda message: print(message, file=sys.stderr, flush=True))
        print(json.dumps(result, indent=2))
    except (RecordingMediaError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
