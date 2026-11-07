#!/usr/bin/env python3
"""Plan the game-facts investigation for one recording. Costs no inference to run.

Reads a classified timeline and a frame-cache manifest, asks the tracker what it could not settle from
the evidence already on disk, and writes the exact frame groups a run would send -- region by region,
group by group, with request and token totals. This is the document Andy approves before the machine is
occupied, and the same functions the run uses produce it, so the plan cannot drift from the work.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/plan_game_investigation.py \
        --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
        --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
        --duration 6928.421136 --output build/work/bb9/games-pilot-v1/plan.json \
        [--region START END] [--max-requests N] [--marks FILE]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_games as bg  # noqa: E402
from tekken_vod_helper import boundary_investigate as bi  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--output", required=True, help="Fresh path for the plan JSON.")
    parser.add_argument("--region", nargs=2, type=float, metavar=("START", "END"), default=None,
                        help="Restrict the pilot to one stretch of the recording.")
    parser.add_argument("--marks", default=None, help="Live markings: prioritise, never define.")
    parser.add_argument("--max-requests", type=int, default=bi.DEFAULT_MAX_REQUESTS)
    parser.add_argument("--batch-frames", type=int, default=bi.DEFAULT_BATCH_FRAMES)
    parser.add_argument("--completion-tokens", type=int, default=bi.DEFAULT_COMPLETION_TOKENS)
    parser.add_argument("--step", type=float, default=bi.DENSIFY_STEPS[0])
    parser.add_argument("--max-regions", type=int, default=None)
    args = parser.parse_args()

    out = Path(args.output)
    if out.exists():
        raise SystemExit("refusing to overwrite an existing plan: {}".format(out))
    rows = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("observations")
    cache = bi.load_cache(args.frame_manifest)
    observations = bg.build_observations(rows)
    tracked = bg.track_games(observations)

    unresolved = tracked["unresolved"]
    marks = []
    if args.marks:
        raw = json.loads(Path(args.marks).read_text(encoding="utf-8"))
        entries = raw.get("matches", raw) if isinstance(raw, dict) else raw
        for entry in entries or []:
            value = entry.get("start", entry) if isinstance(entry, dict) else entry
            if _finite(value):
                marks.append(float(value))

    plan = bi.plan_investigation(unresolved, cache, args.duration, observations=observations,
                                 marks=marks, batch_frames=args.batch_frames,
                                 max_requests=args.max_requests,
                                 max_completion_tokens=args.completion_tokens,
                                 step=args.step, max_regions=args.max_regions,
                                 window=tuple(args.region) if args.region else None)
    plan["window"] = list(args.region) if args.region else None
    plan["source"] = {"timeline": str(args.timeline), "frame_manifest": str(args.frame_manifest)}
    plan["tracker_before"] = tracked["accounting"]
    plan["unresolved_kinds"] = _kinds(unresolved)
    plan["what_the_pilot_cannot_prove"] = [
        "the winner of a round, which needs the win dots read on frames where they are visible",
        "that a game ended rather than a set, where no between-game screen was broadcast",
        "any set count: this pilot answers rounds and games, not how many sets BB9 held",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps({key: plan[key] for key in (
        "status", "region_count", "groups_total", "frames_total", "requests_planned",
        "request_allowance", "regions_deferred_for_budget", "completion_tokens_per_request",
        "completion_tokens_worst_case", "step_seconds", "batch_frames")}, indent=2))
    print("\nunresolved queue it answers:", json.dumps(plan["unresolved_kinds"], indent=2))
    print("\nfirst five regions:")
    for region in plan["regions"][:5]:
        print("  {:8.1f} -> {:8.1f}  groups={:2d}  kinds={}".format(
            region["start"], region["end"], len(region["groups"]), ",".join(region["kinds"])))
    print("\nplan written to {}".format(out))


def _finite(value):
    # type: (object) -> bool
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _kinds(items):
    out = {}
    for item in items:
        out[item.get("type")] = out.get(item.get("type"), 0) + 1
    return out


if __name__ == "__main__":
    main()
