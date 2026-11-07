#!/usr/bin/env python3
"""Dry-run artifact for automatic decision-window selection. Sends nothing.

The interval is fixed on the command line and is independent of intro screens, marks, names,
scores and outcomes. Games cut by that interval remain partial.

Writes into a fresh directory:

    selected-frames.json   the plan: windows, candidates, reused vs requested, deferred, gaps
    annotations-window.json  the requested frames in the probe's own annotation format, with expected
                             values left null: the model decides, this file does not pre-suppose it
    plan-crops.json        the probe's own request plan for those frames (2 images, 1 request each)

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/plan_decision_windows.py \\
        build/work/bb9/windows-interval-v2
    env PYTHONPATH=. .venv/bin/python scripts/plan_decision_windows.py \\
        build/work/bb9/consecutive-games-v1 --start 1006 --end 1406 --allowance 14

    # or let the documented time rule pick the bounds: open at the first whole minute strictly after
    # the newest crop reading already on disk, close at the first whole minute at or after open+span
    env PYTHONPATH=. .venv/bin/python scripts/plan_decision_windows.py \\
        build/work/bb9/consecutive-games-v3 --fresh-interval 1000 --allowance 51 \\
        --rule "whole-minute [S, S+1000) with S the first whole minute strictly after the newest saved
        crop reading; no select screen, mark, name, score or outcome is read by this rule"
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_games as bg           # noqa: E402
from tekken_vod_helper import boundary_pips as bp            # noqa: E402
from tekken_vod_helper import boundary_windows as bw         # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
# Every crop reply already on disk. Reuse is decided by whether that exact frame was read, so leaving
# a finished run out of this list would re-request images the model has already answered.
SAVED_RUNS = ["build/work/bb9/pip-crops-v2", "build/work/bb9/game1-validate-v1/run",
              "build/work/bb9/game2-validate-v1/run", "build/work/bb9/game2-validate-v1/run2",
              "build/work/bb9/windows-interval-v2/run",
              "build/work/bb9/consecutive-games-v2/run"]
START_AT, END_AT = 1006.0, 1206.0
RULE = ("fixed contiguous {span:.0f} s interval [{start:.1f}, {end:.1f}), chosen independently of "
        "openings, marks, names, scores, winners and outcomes; cut games remain partial")
FRESH_RULE = ("contiguous interval [{start:.1f}, {end:.1f}) s of recording time, fixed before any answer "
              "exists. Opened at the first whole minute strictly after {newest}, the newest crop reading "
              "saved on disk when this plan was written, and closed exactly {span:.0f} s later. The span "
              "is the time-rule target for 3-5 games, from measured durations only: the two validated "
              "complete games of this recording ran 182.2 s and 164.2 s over 4 rounds each, so a round is "
              "43.3 s on average, and at the configured first-to-3 a game is 3-5 rounds (130-217 s) plus "
              "its between-game screen. {span:.0f} s therefore buys 3-5 games, and it is a whole number of "
              "seconds, not a number aimed at a result. Nothing else is read by the rule: not stage or "
              "character selects, which stay inside the trial because a select proves nothing about where "
              "a set begins, and not marks, names, scores, winners or outcomes.")
REMAINING_ALLOWANCE = 14     # conservative: 26 of 40 attempts spent, one possibly in flight


def fresh_interval(saved, span):
    # type: (dict, float) -> tuple
    """Apply the time rule to the readings on disk and return ``(start, end, newest, frame)``.

    Only the readings' timestamps are read. The opening is rounded up to a whole minute so the bound is
    a number a reviewer can restate from the recording's own clock, and the close is that plus the span,
    so the interval cannot widen or narrow depending on what is found inside it.
    """
    dated = [(v["timestamp"], k) for k, v in saved.items() if isinstance(v.get("timestamp"),
                                                                          (int, float))]
    if not dated:
        raise SystemExit("no saved crop reading with a timestamp: the rule has nothing to open after")
    newest, frame = max(dated)
    start = 60.0 * (int(newest // 60) + 1)         # first whole minute strictly after the newest read
    return start, start + float(span), newest, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", nargs="?", default="build/work/bb9/windows-interval-v1")
    parser.add_argument("--start", type=float, default=START_AT, help="Interval start, seconds.")
    parser.add_argument("--end", type=float, default=END_AT, help="Interval end, exclusive.")
    parser.add_argument("--allowance", type=int, default=REMAINING_ALLOWANCE,
                        help="New requests this plan may spend. Anything past it is deferred.")
    parser.add_argument("--saved-runs", default=",".join(SAVED_RUNS),
                        help="Comma list of crop runs to reuse. Drop a finished run to reproduce the "
                             "plan that made it, which is how an old artifact stays auditable.")
    parser.add_argument("--fresh-interval", type=float, default=None, metavar="SECONDS",
                        help="Let the documented time rule choose the bounds: open at the first whole "
                             "minute strictly after the newest crop reading on disk, close at the first "
                             "whole minute at or after open + SECONDS. Overrides --start/--end.")
    parser.add_argument("--rule", default=None,
                        help="The selection rule to record verbatim in the artifacts. Default: the "
                             "fixed-interval wording, or the fresh-interval wording with its inputs.")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit("refusing to write into a non-empty directory: {}".format(out))
    observations = bg.build_observations(json.loads(TIMELINE.read_text(encoding="utf-8")))
    saved = bw.load_saved_readings([v.strip() for v in args.saved_runs.split(",") if v.strip()])
    frames = bw.FrameIndex.load()
    selection = None
    if args.fresh_interval:
        start, end, newest, newest_frame = fresh_interval(saved, args.fresh_interval)
        selection = {"rule": "whole-minute interval opened after the newest saved crop reading",
                     "newest_saved_reading": {"frame": newest_frame, "timestamp": newest},
                     "saved_readings_considered": len(saved),
                     "span_requested_seconds": args.fresh_interval,
                     "start_at": start, "end_at": end,
                     "reads": "saved crop readings' timestamps and nothing else: no screen state, "
                              "mark, name, score, winner or outcome"}
        rule = args.rule or FRESH_RULE.format(start=start, end=end, span=args.fresh_interval,
                                              newest="{} at {:.3f} s".format(newest_frame, newest))
    else:
        if args.start is None or args.end is None:
            raise SystemExit("give --start and --end, or --fresh-interval")
        start, end = args.start, args.end
        rule = args.rule or RULE.format(span=end - start, start=start, end=end)
    plan = bw.plan_interval(observations, saved, frames, start_at=start,
                            end_at=end, max_new_requests=args.allowance)
    if selection:
        # The rule and its inputs travel with the plan: an interval that cannot say how it was picked is
        # indistinguishable from one that was picked by its result.
        plan["selection"] = selection
        plan["interval"]["rule"] = rule

    annotations = {"kind": "pip-row-annotations", "version": "window-plan-1",
                   "selected_by": rule,
                   "provenance": {
                       "selection": "automatic, from saved clock rises and KO/TIME UP round events "
                                    "over a fixed interval; no expected outcome was read",
                       "expected_values": "left null on purpose: the selector does not know what the "
                                          "frame shows, and a pre-filled value would be the answer "
                                          "leaking into the request",
                       "reused_readings": len(plan["reused_readings"]),
                       "saved_readings_available": len(saved)},
                   "frames": [{"frame": r["frame"], "timestamp": r["timestamp"],
                               "row_drawn": None, "pips": {"LEFT": None, "RIGHT": None},
                               "selection_role": r["role"], "window": r["window"],
                               "window_basis": r["basis"], "window_end_at": r["window_end_at"]}
                              for r in plan["requested"]]}

    out.mkdir(parents=True, exist_ok=True)
    records = bp.build_records(annotations["frames"], out / "crops")
    probe_plan = bp.plan_requests(records, "crops", max_requests=args.allowance,
                                  model="qwen3.8-flash-next")
    annotations["reused_frames"] = [{"frame": r["frame"], "timestamp": r["timestamp"],
                                     "observed": r["reading"]["win_dots"],
                                     "row_drawn": r["reading"]["row_drawn"]}
                                    for r in plan["reused_readings"]]
    (out / "selected-frames.json").write_text(json.dumps(plan, indent=2, sort_keys=True))
    (out / "annotations-window.json").write_text(json.dumps(annotations, indent=2, sort_keys=True))
    (out / "plan-crops.json").write_text(json.dumps(probe_plan, indent=2, sort_keys=True))

    print(json.dumps({
        "interval": {"start": round(plan["interval"]["start_at"], 2),
                     "end": round(plan["interval"]["end_at"], 2),
                     "seconds": round(plan["interval"]["end_at"] - plan["interval"]["start_at"], 1),
                     "rule": rule},
        "selection_inputs": plan.get("selection"),
        "saved_readings_on_disk": len(saved),
        "reused_in_interval": len(plan["reused_readings"]),
        "windows": len(plan["windows"]),
        "requests_planned": probe_plan["requests_planned"],
        "images_total": probe_plan["images_total"],
        "allowance_remaining": args.allowance,
        "completion_tokens_ceiling": probe_plan["requests_planned"]
                                     * probe_plan["completion_tokens_per_request"],
        "budget_fits": plan["budget_fits"],
        "requests_deferred": plan["requests_deferred"],
        "windows_with_gaps": [{"sequence": w["sequence"], "gaps": w["gaps"]}
                              for w in plan["windows"] if w["gaps"]],
        "frames": [{"index": r["id"], "t": r["timestamp"], "role": r["role"], "window": r["window"]}
                   for r in plan["requested"]],
        "not_yet_sent": True,
        "artifact_dir": str(out)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
