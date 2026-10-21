"""JSON CLI for autonomous match boundary detection and backtesting.

Usage: python -m tekken_vod_helper.boundary_cli [GLOBAL_OPTS] COMMAND [ARGS]
"""
import argparse
import json
import sys
from typing import Any, Dict

from . import boundary_workflow


def _output(result: Dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, allow_nan=False))


def _error(message: str, details: Dict[str, Any] = None) -> None:
    payload: Dict[str, Any] = {"status": "error", "error": message}
    if details:
        payload["details"] = details
    print(json.dumps(payload, indent=2, allow_nan=False))
    sys.exit(1)


class _JsonArgParser(argparse.ArgumentParser):
    """Argparse subclass that emits JSON errors to stdout."""

    def error(self, message):
        print(json.dumps({"status": "error", "error": message}, indent=2, allow_nan=False))
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgParser(
        prog="tekken_vod_helper",
        description="Autonomous Tekken match boundary detection and backtesting.",
    )

    sub = parser.add_subparsers(dest="command")

    # detect
    p = sub.add_parser("detect", help="Autonomous boundary detection from a source.")
    p.add_argument("--ffmpeg", default="", help="Path to ffmpeg binary.")
    p.add_argument("--ffprobe", default="", help="Path to ffprobe binary.")
    p.add_argument("source", help="YouTube URL or local file path.")
    p.add_argument("--output-dir", required=True, help="Output directory.")
    p.add_argument("--duration", type=float, default=None, help="Clamp duration (seconds).")
    p.add_argument("--interval", type=float, default=5.0, help="Sampling interval (default 5).")
    p.add_argument("--fine-step", type=float, default=1.0, help="Fine refinement step (default 1).")
    p.add_argument("--max-frames", type=int, default=2000, help="Max frames (default 2000).")
    p.add_argument("--batch-size", type=int, default=8, help="Classification batch size (default 8).")
    p.add_argument("--max-requests", type=int, default=300, help="Max model requests (default 300).")
    p.add_argument("--yt-dlp", default="", help="Path to yt-dlp binary.")
    p.add_argument("--endpoint", default="http://127.0.0.1:8010", help="Vision model endpoint.")
    p.add_argument("--model", default="qwen3.8-27b", help="Model name.")
    p.add_argument("--samples-manifest", default="", help="Pre-existing samples manifest JSON path.")
    p.add_argument("--event", default="", help="Event name for the generated project.")
    p.add_argument("--temporal", action="store_true", help="Enable temporal adjudication for boundary proposals.")
    p.add_argument("--cache-fine", action="store_true", help="Enable single-pass fine-frame caching.")
    p.add_argument("--max-cache-frames", type=int, default=12000, help="Max cache frames for fine sampling (default 12000).")
    p.add_argument("--recognition-crops", dest="recognition_crops", action="store_true", help="Enable recognition crops for vision model.")
    p.add_argument("--no-vision-thinking", dest="vision_thinking", action="store_false", default=True, help="Disable vision model thinking.")

    # backtest-boundaries
    p = sub.add_parser("backtest-boundaries", help="Detect and score against truth labels.")
    p.add_argument("--ffmpeg", default="", help="Path to ffmpeg binary.")
    p.add_argument("--ffprobe", default="", help="Path to ffprobe binary.")
    p.add_argument("source", help="YouTube URL or local file path.")
    p.add_argument("--truth", required=True, help="Truth JSON path (scoring only, post-persist).")
    p.add_argument("--output-dir", required=True, help="Output directory.")
    p.add_argument("--duration", type=float, default=None, help="Clamp duration (seconds).")
    p.add_argument("--interval", type=float, default=5.0, help="Sampling interval (default 5).")
    p.add_argument("--fine-step", type=float, default=1.0, help="Fine refinement step (default 1).")
    p.add_argument("--max-frames", type=int, default=2000, help="Max frames (default 2000).")
    p.add_argument("--batch-size", type=int, default=8, help="Classification batch size (default 8).")
    p.add_argument("--max-requests", type=int, default=300, help="Max model requests (default 300).")
    p.add_argument("--yt-dlp", default="", help="Path to yt-dlp binary.")
    p.add_argument("--endpoint", default="http://127.0.0.1:8010", help="Vision model endpoint.")
    p.add_argument("--model", default="qwen3.8-27b", help="Model name.")
    p.add_argument("--samples-manifest", default="", help="Pre-existing samples manifest JSON path.")
    p.add_argument("--event", default="", help="Event name for the generated project.")
    p.add_argument("--temporal", action="store_true", help="Enable temporal adjudication for boundary proposals.")
    p.add_argument("--cache-fine", action="store_true", help="Enable single-pass fine-frame caching.")
    p.add_argument("--max-cache-frames", type=int, default=12000, help="Max cache frames for fine sampling (default 12000).")
    p.add_argument("--recognition-crops", dest="recognition_crops", action="store_true", help="Enable recognition crops for vision model.")
    p.add_argument("--no-vision-thinking", dest="vision_thinking", action="store_false", default=True, help="Disable vision model thinking.")

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "detect":
            result = boundary_workflow.detect(
                args.source, args.output_dir,
                duration=args.duration, interval=args.interval,
                fine_step=args.fine_step, max_frames=args.max_frames,
                batch_size=args.batch_size, max_requests=args.max_requests,
                yt_dlp=args.yt_dlp, endpoint=args.endpoint, model=args.model,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                event_name=args.event, samples_manifest=args.samples_manifest or None,
                temporal=args.temporal,
                cache_fine=args.cache_fine, max_cache_frames=args.max_cache_frames,
                recognition_crops=args.recognition_crops, vision_thinking=args.vision_thinking,
            )
        elif args.command == "backtest-boundaries":
            result = boundary_workflow.backtest(
                args.source, args.truth, args.output_dir,
                duration=args.duration, interval=args.interval,
                fine_step=args.fine_step, max_frames=args.max_frames,
                batch_size=args.batch_size, max_requests=args.max_requests,
                yt_dlp=args.yt_dlp, endpoint=args.endpoint, model=args.model,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                event_name=args.event, samples_manifest=args.samples_manifest or None,
                temporal=args.temporal,
                cache_fine=args.cache_fine, max_cache_frames=args.max_cache_frames,
                recognition_crops=args.recognition_crops, vision_thinking=args.vision_thinking,
            )
        else:
            parser.print_help()
            sys.exit(1)
    except Exception as exc:
        _error(str(exc))
        return

    _output(result)


if __name__ == "__main__":
    main()
