"""JSON CLI for autonomous match boundary detection and backtesting.

Usage: python -m tekken_vod_helper.boundary_cli [GLOBAL_OPTS] COMMAND [ARGS]
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from . import (boundary_budget, boundary_investigate, boundary_reasoning, boundary_recovery,
               boundary_review, boundary_workflow, local_model)


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


def _add_adjudication_options(parser):
    """Temporal adjudication sizing, shared by every command that can adjudicate."""
    parser.add_argument("--adjudication-requests", dest="adjudication_requests", type=int,
                        default=None,
                        help="Model requests the temporal stage may spend, reserved from "
                             "--max-requests (default {} for --temporal).".format(
                                 boundary_reasoning.DEFAULT_ADJUDICATION_REQUESTS))
    parser.add_argument("--adjudication-max-chars", dest="adjudication_max_chars", type=int,
                        default=boundary_reasoning.MAX_INPUT_CHARS,
                        help="Hard character cap per adjudication prompt (default {}).".format(
                            boundary_reasoning.MAX_INPUT_CHARS))
    parser.add_argument("--adjudication-max-tokens", dest="adjudication_max_tokens", type=int,
                        default=boundary_reasoning.DEFAULT_COMPLETION_TOKENS,
                        help="Completion budget per adjudication call, reasoning included (default "
                             "{}). A reasoning model that spends it all thinking returns no "
                             "content.".format(boundary_reasoning.DEFAULT_COMPLETION_TOKENS))
    parser.add_argument("--adjudication-overlap", dest="adjudication_overlap", type=int,
                        default=boundary_reasoning.DEFAULT_WINDOW_OVERLAP,
                        help="Observations of neighbour context each adjudication window asks for "
                             "(default {}).".format(boundary_reasoning.DEFAULT_WINDOW_OVERLAP))


def _parse_provenance(value):
    # type: (str) -> Dict[str, Any]
    """Links to the evidence a recovery stands on: a JSON object, or @path to one."""
    text = (value or "").strip()
    if not text:
        return {}
    if text.startswith("@"):
        text = Path(text[1:]).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("--provenance is not valid JSON: {}".format(exc))
    if not isinstance(data, dict):
        raise ValueError("--provenance must be a JSON object")
    return data


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
    p.add_argument("--endpoint", default=None,
                   help="Vision model endpoint. Default: $QWEN_BASE_URL or {}.".format(local_model.DEFAULT_ENDPOINT))
    p.add_argument("--model", default=None,
                   help="Model name. Default: $QWEN_MODEL or {}.".format(local_model.DEFAULT_MODEL))
    p.add_argument("--samples-manifest", default="", help="Pre-existing samples manifest JSON path.")
    p.add_argument("--event", default="", help="Event name for the generated project.")
    p.add_argument("--temporal", action="store_true", help="Enable temporal adjudication for boundary proposals.")
    p.add_argument("--cache-fine", action="store_true", help="Enable single-pass fine-frame caching.")
    p.add_argument("--max-cache-frames", type=int, default=12000, help="Max cache frames for fine sampling (default 12000).")
    p.add_argument("--recognition-crops", dest="recognition_crops", action="store_true", help="Enable recognition crops for vision model.")
    p.add_argument("--no-vision-thinking", dest="vision_thinking", action="store_false", default=True, help="Disable vision model thinking.")
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=3, help="Requests per batch before giving up; each attempt charges --max-requests (default 3).")
    _add_adjudication_options(p)

    # backtest-boundaries
    p = sub.add_parser("backtest-boundaries", help="Detect and score against truth labels.")
    p.add_argument("--ffmpeg", default="", help="Path to ffmpeg binary.")
    p.add_argument("--ffprobe", default="", help="Path to ffprobe binary.")
    p.add_argument("source", help="YouTube URL or local file path.")
    p.add_argument("--truth", required=True, help="Truth JSON path (scoring only, post-persist).")
    p.add_argument("--truth-duration", type=float, default=None,
                   help="Recording length in seconds used to resolve a truth row with end=null "
                        "when the truth project reports no usable duration. Does not edit the truth file.")
    p.add_argument("--output-dir", required=True, help="Output directory.")
    p.add_argument("--duration", type=float, default=None, help="Clamp duration (seconds).")
    p.add_argument("--interval", type=float, default=5.0, help="Sampling interval (default 5).")
    p.add_argument("--fine-step", type=float, default=1.0, help="Fine refinement step (default 1).")
    p.add_argument("--max-frames", type=int, default=2000, help="Max frames (default 2000).")
    p.add_argument("--batch-size", type=int, default=8, help="Classification batch size (default 8).")
    p.add_argument("--max-requests", type=int, default=300, help="Max model requests (default 300).")
    p.add_argument("--yt-dlp", default="", help="Path to yt-dlp binary.")
    p.add_argument("--endpoint", default=None,
                   help="Vision model endpoint. Default: $QWEN_BASE_URL or {}.".format(local_model.DEFAULT_ENDPOINT))
    p.add_argument("--model", default=None,
                   help="Model name. Default: $QWEN_MODEL or {}.".format(local_model.DEFAULT_MODEL))
    p.add_argument("--samples-manifest", default="", help="Pre-existing samples manifest JSON path.")
    p.add_argument("--event", default="", help="Event name for the generated project.")
    p.add_argument("--temporal", action="store_true", help="Enable temporal adjudication for boundary proposals.")
    p.add_argument("--cache-fine", action="store_true", help="Enable single-pass fine-frame caching.")
    p.add_argument("--max-cache-frames", type=int, default=12000, help="Max cache frames for fine sampling (default 12000).")
    p.add_argument("--recognition-crops", dest="recognition_crops", action="store_true", help="Enable recognition crops for vision model.")
    p.add_argument("--no-vision-thinking", dest="vision_thinking", action="store_false", default=True, help="Disable vision model thinking.")
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=3, help="Requests per batch before giving up; each attempt charges --max-requests (default 3).")
    _add_adjudication_options(p)

    # adjudicate-timeline
    p = sub.add_parser("adjudicate-timeline",
                       help="Adjudicate a saved classified timeline. No source, frames or vision calls.")
    p.add_argument("--timeline", required=True,
                   help="Classified timeline JSON from an earlier detect run (e.g. timeline-refined.json).")
    p.add_argument("--output-dir", required=True, help="Fresh output directory for the recovered run.")
    p.add_argument("--source-id", required=True,
                   help="Identity of the recording the timeline came from, recorded verbatim.")
    p.add_argument("--duration", type=float, required=True,
                   help="Recording length in seconds. Nothing here probes a media file.")
    p.add_argument("--source", default="", help="Optional path to the master, recorded for provenance only.")
    p.add_argument("--event", default="", help="Event name for the generated project.")
    p.add_argument("--endpoint", default=None,
                   help="Adjudication model endpoint. Default: $QWEN_BASE_URL or {}.".format(local_model.DEFAULT_ENDPOINT))
    p.add_argument("--model", default=None,
                   help="Model name. Default: $QWEN_MODEL or {}.".format(local_model.DEFAULT_MODEL))
    p.add_argument("--adjudication-requests", dest="requests", type=int, default=None,
                   help="Model requests this recovery may spend (default {}).".format(
                       boundary_reasoning.DEFAULT_ADJUDICATION_REQUESTS))
    p.add_argument("--max-input-chars", dest="max_input_chars", type=int,
                   default=boundary_reasoning.MAX_INPUT_CHARS,
                   help="Hard character cap per adjudication prompt (default {}).".format(
                       boundary_reasoning.MAX_INPUT_CHARS))
    p.add_argument("--window-overlap", dest="overlap", type=int,
                   default=boundary_reasoning.DEFAULT_WINDOW_OVERLAP,
                   help="Neighbour observations each window asks for as context (default {}).".format(
                       boundary_reasoning.DEFAULT_WINDOW_OVERLAP))
    p.add_argument("--adjudication-max-tokens", dest="max_tokens", type=int,
                   default=boundary_reasoning.DEFAULT_COMPLETION_TOKENS,
                   help="Completion budget per call, reasoning included (default {}). A reasoning "
                        "model that spends it all thinking returns no content.".format(
                            boundary_reasoning.DEFAULT_COMPLETION_TOKENS))
    p.add_argument("--timeout", type=int, default=1800, help="Seconds per adjudication request.")
    p.add_argument("--policy", dest="policy_path", default=None,
                   help="Cutting policy to adjudicate with (default docs/cutting-policy.md).")
    p.add_argument("--provenance", default="",
                   help="JSON object or @path linking the evidence this recovery is built on.")
    p.add_argument("--resume", action="store_true",
                   help="Reuse verified chunks already in --output-dir instead of re-asking the model.")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Report the planned windows and call allowance, then stop without any request.")

    # investigate-games
    p = sub.add_parser("investigate-games",
                       help="Plan or run the frame questions that settle rounds and games, reading this "
                            "recording's saved crop and band answers. --dry-run costs no inference; a live "
                            "run requires --budget-file.")
    p.add_argument("--timeline", required=True,
                   help="Classified timeline JSON from an earlier detect run.")
    p.add_argument("--frame-manifest", required=True,
                   help="Sample manifest of the existing frame cache to read from.")
    p.add_argument("--duration", type=float, required=True, help="Recording length in seconds.")
    p.add_argument("--output-dir", required=True, help="Fresh output directory.")
    p.add_argument("--region", nargs=2, type=float, metavar=("START", "END"), default=None,
                   help="Keep the whole run inside this stretch of the recording.")
    p.add_argument("--marks", default="",
                   help="Live markings JSON: prioritises regions, never defines a boundary.")
    p.add_argument("--header-responses", dest="header_responses", default=None,
                   help="Directory of already-saved broadcast-band answers to credit wins against; "
                        "comma-separated when they came from more than one run, and never a merged copy. "
                        "Read-only: it re-reads responses, it never re-asks them and it charges nothing.")
    p.add_argument("--header-ledger", dest="header_ledger", default=None,
                   help="Report-only: the attempt ledger that counted those band answers, when one did. "
                        "It changes what the provenance says, never what is adjudicated.")
    p.add_argument("--crop-runs", dest="crop_runs", default=None,
                   help="Comma list of saved pip crop answer directories to merge through the canonical "
                        "join. Default: every crop-answer directory discovered under this recording's "
                        "evidence root -- the same corpus the read-only reports merge. Name the list only "
                        "to reproduce an older plan's inputs.")
    p.add_argument("--evidence-root", dest="evidence_root", default=None,
                   help="Where this recording's saved answers live (default build/work/bb9). Scoped to "
                        "one recording on purpose: two recordings' pip series must never be joined.")
    p.add_argument("--state-only", action="store_true",
                   help="Comparitor: skip the crop merge and adjudicate the classified timeline alone. "
                        "On this recording that base concludes games with no winner, so no winner count "
                        "may be quoted from it.")
    p.add_argument("--format-policy", dest="format_policy", default=None,
                   help="A user-confirmed event format file (see boundary_format.load_policy): each "
                        "candidate is measured against the target the stage in its own span names. "
                        "Optional -- nothing here infers the rules from the footage.")
    p.add_argument("--no-format", dest="no_format", action="store_true",
                   help="Recording-only mode: apply no target anywhere, so a closure can only be the "
                        "footage's. This is the column that shows what a recovery needed no rules for.")
    p.add_argument("--expand-window", dest="expand_window", type=float,
                   default=boundary_investigate.EXPANSION_SECONDS_PER_SIDE,
                   help="Per side, how far this query may widen itself when a candidate's own edge says the "
                        "reason nothing settled it is that the window stopped there (%.0f s by default; 0 "
                        "turns widening off). It never reaches past what the cache and the recording are "
                        "known to cover, saved rows are reused before any frame is priced, and every stop "
                        "carries its reason." % boundary_investigate.EXPANSION_SECONDS_PER_SIDE)
    p.add_argument("--expansion-progress", dest="expansion_progress", default=None,
                   help="expansion.json from an earlier run: resume a widening from where it got to instead "
                        "of re-walking ground the last run covered and pricing the same reads twice.")
    p.add_argument("--max-requests", dest="max_requests", type=int,
                   default=boundary_investigate.DEFAULT_MAX_REQUESTS,
                   help="Request allowance and hard stop (default {}).".format(
                       boundary_investigate.DEFAULT_MAX_REQUESTS))
    p.add_argument("--budget-file", dest="budget_file", default=None,
                   help="Shared attempt ledger: one cap and one wall-time deadline across every command "
                        "and every resume. REQUIRED unless --dry-run: without it --max-requests is "
                        "per-process only, so every re-run re-arms the allowance.")
    p.add_argument("--budget-cap", dest="budget_cap", type=int,
                   default=boundary_budget.DEFAULT_CAP,
                   help="Total attempts the ledger may charge, retries included (default {}).".format(
                       boundary_budget.DEFAULT_CAP))
    p.add_argument("--budget-minutes", dest="budget_minutes", type=float,
                   default=boundary_budget.DEFAULT_MINUTES,
                   help="One deadline, stamped at the first send and obeyed by every later command.")
    p.add_argument("--purpose", default="investigate-games",
                   help="Why these attempts are spent: recorded on every charged attempt.")
    p.add_argument("--routes", default=None,
                   help="Comma list of executors this run may send on: pip_crops, state, header "
                        "(default: all three). A route left out is planned at its real price and reported "
                        "as unfunded, which is a different fact from having nothing to ask.")
    p.add_argument("--passes", type=int, default=boundary_investigate.DEFAULT_PASSES,
                   help="Plan-run-replan cycles in one invocation (default {}). Each pass re-plans from "
                        "the answers the last one produced, so a new winner can raise a band question; a "
                        "pass that asks nothing costs nothing.".format(
                            boundary_investigate.DEFAULT_PASSES))
    p.add_argument("--batch-frames", dest="batch_frames", type=int,
                   default=boundary_investigate.DEFAULT_BATCH_FRAMES,
                   help="Frames per request (default {}).".format(
                       boundary_investigate.DEFAULT_BATCH_FRAMES))
    p.add_argument("--step", type=float, default=boundary_investigate.DENSIFY_STEPS[0],
                   help="Seconds between requested frames (default 1, i.e. the existing cache).")
    p.add_argument("--max-regions", dest="max_regions", type=int, default=None,
                   help="Cap the number of regions considered.")
    p.add_argument("--rounds-per-game", dest="rounds_per_game", type=int, default=3,
                   help="Configured bracket format: rounds that win a game (default 3).")
    p.add_argument("--games-per-set", dest="games_per_set", type=int, default=3,
                   help="Configured bracket format: games that win a set (default 3).")
    p.add_argument("--endpoint", default=None,
                   help="Vision model endpoint. Default: $QWEN_BASE_URL or {}.".format(
                       local_model.DEFAULT_ENDPOINT))
    p.add_argument("--model", default=None,
                   help="Model name. Default: $QWEN_MODEL or {}.".format(
                       local_model.DEFAULT_MODEL))
    p.add_argument("--timeout", type=int, default=1800, help="Seconds per request.")
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=2,
                   help="Requests per group before it is recorded as failed (default 2).")
    p.add_argument("--resume", action="store_true",
                   help="Continue an interrupted investigate-games run in its existing output dir. "
                        "Refuses unless the saved inputs match this command exactly.")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Write the exact frame groups, the request total and the set recovery this "
                        "recording's saved evidence already supports, then stop. Costs no inference and "
                        "needs no ledger.")

    # score-predictions
    p = sub.add_parser("score-predictions",
                       help="Re-score a finished detect run against truth labels with zero model calls.")
    p.add_argument("--run", required=True, help="run.json written by an earlier detect/backtest run.")
    p.add_argument("--truth", required=True, help="Truth JSON path (scoring only).")
    p.add_argument("--output-dir", required=True, help="Directory holding the run; report is written here.")
    p.add_argument("--truth-duration", type=float, default=None,
                   help="Recording length in seconds used to resolve a truth row with end=null.")
    p.add_argument("--source", default="", help="Source identity recorded in the report for provenance.")

    p = sub.add_parser("review-project",
                       help="Turn a saved recovery into a reviewable project: zero model calls, no media.")
    p.add_argument("--recovery", required=True,
                   help="A saved plan/investigation JSON whose 'recovery' block holds the candidates.")
    p.add_argument("--output", required=True, help="Project JSON to write (an existing one is merged "
                                                   "by start, so decisions already made survive).")
    p.add_argument("--event", default="", help="Event name for the generated project.")
    p.add_argument("--duration", type=float, default=None,
                   help="Recording length in seconds; taken from the recovery when omitted.")
    p.add_argument("--games-from", dest="games_from", default=None,
                   help="Saved per-game records (a games list, or a document with a 'games' key) to "
                        "attach by containment. Omitted: sets import without game evidence and the "
                        "report says how many.")

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
                max_attempts=args.max_attempts,
                adjudication_requests=args.adjudication_requests,
                adjudication_max_chars=args.adjudication_max_chars,
                adjudication_overlap=args.adjudication_overlap,
                adjudication_max_tokens=args.adjudication_max_tokens,
            )
        elif args.command == "backtest-boundaries":
            result = boundary_workflow.backtest(
                args.source, args.truth, args.output_dir,
                truth_duration=args.truth_duration,
                duration=args.duration, interval=args.interval,
                fine_step=args.fine_step, max_frames=args.max_frames,
                batch_size=args.batch_size, max_requests=args.max_requests,
                yt_dlp=args.yt_dlp, endpoint=args.endpoint, model=args.model,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                event_name=args.event, samples_manifest=args.samples_manifest or None,
                temporal=args.temporal,
                cache_fine=args.cache_fine, max_cache_frames=args.max_cache_frames,
                recognition_crops=args.recognition_crops, vision_thinking=args.vision_thinking,
                max_attempts=args.max_attempts,
                adjudication_requests=args.adjudication_requests,
                adjudication_max_chars=args.adjudication_max_chars,
                adjudication_overlap=args.adjudication_overlap,
                adjudication_max_tokens=args.adjudication_max_tokens,
            )
        elif args.command == "adjudicate-timeline":
            if args.dry_run:
                result = boundary_recovery.plan_saved_timeline(
                    args.timeline, duration=args.duration, endpoint=args.endpoint,
                    model=args.model, requests=args.requests,
                    max_input_chars=args.max_input_chars, overlap=args.overlap,
                    policy_path=args.policy_path, max_completion_tokens=args.max_tokens,
                )
            else:
                result = boundary_recovery.adjudicate_saved_timeline(
                    args.timeline, args.output_dir,
                    source_id=args.source_id, duration=args.duration,
                    event_name=args.event, source_path=args.source,
                    endpoint=args.endpoint, model=args.model, requests=args.requests,
                    max_input_chars=args.max_input_chars, overlap=args.overlap,
                    timeout=args.timeout, provenance=_parse_provenance(args.provenance),
                    resume=args.resume, policy_path=args.policy_path,
                    max_completion_tokens=args.max_tokens,
                )
        elif args.command == "investigate-games":
            if args.no_format and args.format_policy:
                raise ValueError("--no-format and --format-policy contradict each other: one supplies "
                                 "the event's rules and the other removes them")
            if args.state_only and args.crop_runs:
                raise ValueError("--state-only and --crop-runs contradict each other: one skips the crop "
                                 "readings and the other names the directories to merge")
            if not args.dry_run and not args.budget_file:
                # Refused here, before a timeline is read, a plan is priced or a client exists. An
                # optional governance flag is the failure mode: with no ledger, --max-requests is
                # per-process, every re-run re-arms the allowance, and nothing records what was spent.
                raise ValueError("investigate-games without --dry-run sends real requests and requires "
                                 "--budget-file: attempts that no ledger counts are attempts nobody "
                                 "approved. Use --dry-run to price the plan for free.")
            games_per_set = None if args.no_format else args.games_per_set
            header = boundary_investigate.load_header_responses(args.header_responses,
                                                                ledger=args.header_ledger)
            format_policy = boundary_investigate.load_format_policy(args.format_policy)
            # Which crop corpus the run reads: an explicit list, none at all, or (the default) every
            # crop answer this recording has. Naming it here rather than defaulting it in the library is
            # deliberate -- a library that discovered by default would merge this recording's win dots
            # into whatever timeline a test happened to hand it.
            corpus = args.crop_runs or (boundary_investigate.DISCOVER_CROPS
                                        if not args.state_only else None)
            # None means "every route this command knows"; a list funds only those, and the plan reports
            # the rest as unfunded rather than silently dropping their questions.
            routes = ([part.strip() for part in str(args.routes).split(",") if part.strip()]
                      if args.routes else None)
            evidence_options = dict(crop_runs=corpus, state_only=args.state_only,
                                    evidence_root=args.evidence_root, format_policy=format_policy,
                                    routes=routes)
            if args.dry_run:
                result = boundary_investigate.plan_saved_investigation(
                    args.timeline, args.frame_manifest, args.duration,
                    marks=args.marks or None, region=args.region, output_dir=args.output_dir,
                    header=header, games_per_set=games_per_set,
                    batch_frames=args.batch_frames, max_requests=args.max_requests,
                    step=args.step, max_regions=args.max_regions,
                    expand=getattr(args, "expand_window", 0.0),
                    expansion_progress=(json.loads(Path(args.expansion_progress).read_text())
                                        if getattr(args, "expansion_progress", None) else None),
                    rounds_per_game=args.rounds_per_game,
                    endpoint=args.endpoint, model=args.model, **evidence_options)
            else:
                result = boundary_investigate.investigate_saved_games(
                    args.timeline, args.frame_manifest, args.duration, args.output_dir,
                    marks=args.marks or None, region=args.region,
                    header=header, games_per_set=games_per_set,
                    endpoint=args.endpoint, model=args.model, timeout=args.timeout,
                    max_attempts=args.max_attempts, batch_frames=args.batch_frames,
                    max_requests=args.max_requests, step=args.step,
                    max_regions=args.max_regions, rounds_per_game=args.rounds_per_game,
                    resume=args.resume, budget_file=args.budget_file, budget_cap=args.budget_cap,
                    budget_minutes=args.budget_minutes, purpose=args.purpose, passes=args.passes,
                    expand=getattr(args, "expand_window", 0.0),
                    expansion_progress=(json.loads(Path(args.expansion_progress).read_text())
                                        if getattr(args, "expansion_progress", None) else None),
                    **evidence_options)
        elif args.command == "score-predictions":
            result = boundary_workflow.score_from_run(
                args.run, args.truth, args.output_dir,
                source=args.source, truth_duration=args.truth_duration,
            )
        elif args.command == "review-project":
            document = json.loads(Path(args.recovery).read_text(encoding="utf-8"))
            recovery = document.get("recovery") or document
            games = None
            if args.games_from:
                loaded = json.loads(Path(args.games_from).read_text(encoding="utf-8"))
                games = loaded.get("games") if isinstance(loaded, dict) else loaded
            imported = boundary_review.import_recovery(recovery, source=args.recovery, games=games)
            target = Path(args.output)
            project = boundary_review.merge_into_project(
                json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {},
                imported["segments"], event=args.event,
                duration=args.duration if args.duration is not None else document.get("duration"))
            queue = boundary_review.review_queue(project)
            # Provenance travels in the project, so a reviewer opening it months later can see what the
            # import was told, without needing the terminal output that produced it.
            project["import_provenance"] = {"source": args.recovery,
                                            "games_from": args.games_from or "",
                                            "inference_sent": False, "media_rendered": False,
                                            **imported["accounting"],
                                            "questions_queued": len(queue),
                                            "by_kind": {kind: sum(1 for item in queue
                                                                  if item["kind"] == kind)
                                                        for kind in sorted({item["kind"]
                                                                            for item in queue})}}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(project, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            result = {"written": str(target), **project["import_provenance"]}
        else:
            parser.print_help()
            sys.exit(1)
    except Exception as exc:
        _error(str(exc))
        return

    _output(result)


if __name__ == "__main__":
    main()
