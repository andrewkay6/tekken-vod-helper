"""Price one bounded interval and print the exact frames each route would read.

Offline: the planner sends nothing and writes no ledger. Used to name the reads a proposal would make, so
the frames can be inspected and labelled before anybody approves an attempt.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_games as bg        # noqa: E402
from tekken_vod_helper import boundary_header as bh        # noqa: E402
from tekken_vod_helper import boundary_investigate as bi    # noqa: E402

CROPS = ",".join(["build/work/bb9/consecutive-games-v2/run", "build/work/bb9/consecutive-games-v3/run",
                  "build/work/bb9/game1-validate-v1/run", "build/work/bb9/game2-validate-v1/run",
                  "build/work/bb9/game2-validate-v1/run2", "build/work/bb9/pip-crops-v2",
                  "build/work/bb9/set-end2end-v1/follow-up", "build/work/bb9/set-end2end-v1/run",
                  "build/work/bb9/source-crop-smoke-v1/run", "build/work/bb9/windows-interval-v2/run",
                  "build/work/bb9/unseen-set-structure-v1/run-attempt/pip",
                  "build/work/bb9/game1-decider-probe-v1/run-single-frame",
                  "build/work/bb9/game1-decider-probe-v1/widening-run/pip"])
BANDS = ",".join(["build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30",
                  "build/work/bb9/set-end2end-v1/attribution-run",
                  "build/work/bb9/set-end2end-v1/band-run",
                  "build/work/bb9/unseen-set-structure-v1/run-attempt/header"])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--region", nargs=2, type=float, required=True)
    parser.add_argument("--expand", type=float, default=180.0)
    parser.add_argument("--crop-runs", default=CROPS)
    parser.add_argument("--band-runs", default=BANDS)
    parser.add_argument("--band-ledger",
                        default="build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    header = bh.saved_readings(bh.response_directories(args.band_runs), args.band_ledger)
    planned = bi.plan_saved_investigation(args.timeline, args.frame_manifest, args.duration,
                                          region=tuple(args.region), step=2.0, expand=args.expand,
                                          max_requests=2000, header=header,
                                          crop_runs=args.crop_runs or None, games_per_set=None)
    expansion = planned.get("expansion") or {}
    window = list((expansion.get("expanded_window") or planned["window"]))
    executable = bi.plan_saved_investigation(args.timeline, args.frame_manifest, args.duration,
                                             region=tuple(window), step=2.0, expand=0.0,
                                             max_requests=2000, header=header,
                                             crop_runs=args.crop_runs or None, games_per_set=None)
    routes = {entry["route"]: entry for entry in executable["dispatch"]["routes"]}
    report = {
        "inference_sent": bool(planned["inference_sent"] or executable["inference_sent"]),
        "requested_window": list(args.region),
        "executable_window": window,
        "expansion": {"triggered": expansion.get("triggered"),
                      "steps": expansion.get("steps"),
                      "stopping_reasons": expansion.get("stopping_reasons"),
                      "observations": expansion.get("observations"),
                      "priced_only_missing": expansion.get("priced_only_missing")},
        "headline_price_of_the_requested_window": planned["requests_planned"],
        "executable_price": {
            "requests": executable["requests_planned"],
            "images": sum(int(entry["images_total"]) for entry in routes.values()),
            "completion_token_ceiling": sum(int(entry["requests_planned"]) *
                                            int(entry["completion_tokens_per_request"])
                                            for entry in routes.values()),
            "by_route": {name: {"planned": entry["requests_planned"],
                                "images": entry["images_total"],
                                "reused": entry.get("reused", 0),
                                "ceiling_each": entry["completion_tokens_per_request"]}
                         for name, entry in routes.items()},
            "request_identities": sorted(
                {"%s:%s" % (region["route"], group.get("identity") or group.get("fingerprint"))
                 for region in executable["regions"] for group in region["groups"]}
                | {"%s:%s" % (entry.get("route"), entry.get("identity"))
                   for entry in (executable.get("pip_requests") or [])
                                + (executable.get("header_requests") or [])}),
        },
        "frames_to_read": {
            "pip_crops": [{"timestamp": round(entry["timestamp"], 3), "role": entry["role"],
                           "frame": Path(str(entry["frame"])).name, "images": entry["images"],
                           "asked": entry["asked"]}
                          for entry in (executable.get("pip_requests") or [])],
            "state": [{"region": [round(r["start"], 3), round(r["end"], 3)], "kinds": r["kinds"],
                       "frames": [{"timestamp": round(t, 3), "frame": Path(str(p)).name}
                                  for t, p in zip(g["frame_timestamps"], g["frame_paths"])]}
                      for r in executable["regions"] for g in r["groups"]],
            "header": [{"at": round(entry["at"], 3), "question": entry["question"],
                        "frame": Path(str(entry["frame"])).name}
                       for entry in (executable.get("header_requests") or [])]},
        "candidates": [{k: c.get(k) for k in ("start", "end", "status", "games", "game_wins",
                                              "game_winners", "deciding_wins", "deciding_wins_unit",
                                              "set_winner", "membership", "footage_complete",
                                              "grouping_unresolved", "missing_evidence")}
                       for c in executable["recovery"]["candidates"]],
        "unresolved_kinds": executable["unresolved_kinds"],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("requested_window", "executable_window",
                                             "headline_price_of_the_requested_window")}, indent=1))
    print("executable:", json.dumps(report["executable_price"]["by_route"]))
    print("requests=%s images=%s ceiling=%s" % (
        report["executable_price"]["requests"], report["executable_price"]["images"],
        report["executable_price"]["completion_token_ceiling"]))
    for route, items in report["frames_to_read"].items():
        print("%s: %d" % (route, len(items)))
        for item in items[:40]:
            print("   ", json.dumps(item)[:190])
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
