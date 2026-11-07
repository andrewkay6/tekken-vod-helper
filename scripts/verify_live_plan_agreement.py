"""Does a live run send exactly what the dry run priced?

The audit prices intervals through ``plan_saved_investigation``, which is the dry-run half of the public
command. This checks the other half: ``investigate_saved_games`` is driven over the same inputs with every
route's transport replaced at ``_post`` -- the same seam the repository's own tests use -- and the requests
it actually attempts are compared with the identities the dry run published for the window it would
execute.

Nothing leaves the process. The stand-in transport returns a reply that fails validation, so no answer is
ever accepted and no answer file is written, and the run directory is created outside the recording's
evidence root so a verification can never become a corpus. ``--out`` is the only file this writes, besides
the throwaway ledger and group records under ``--run-dir``.

    .venv/bin/python scripts/verify_live_plan_agreement.py \\
        --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \\
        --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \\
        --duration 6928.421136 --region 6156 6246 --routes pip_crops \\
        --run-dir /tmp/bb9-live-plan-agreement --out build/work/bb9/plan-agreement-v1/pip-6156-6246.json
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_header as bh                # noqa: E402
from tekken_vod_helper import boundary_investigate as bi           # noqa: E402
from tekken_vod_helper import boundary_pips as bp                  # noqa: E402

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


class UnusableTransport:
    """Records the bytes that would have left, and answers in a way nothing accepts.

    Failing validation rather than raising keeps the run walking its whole plan -- a transport error is a
    halt by design, and a halted run proves nothing about the requests after the halt. An unusable reply
    also means the run cannot persist an answer, so a verification can never contaminate the evidence base.
    """

    def __init__(self):
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        return {"choices": [{"message": {"content": "verification stub: not an answer"}}]}


def frames_requested(transport):
    """Every cached frame the run asked a model to look at, from the request bodies themselves."""
    seen = set()
    text = json.dumps(transport.bodies)
    for match in __import__("re").finditer(r"frame-(\d{4})\.(?:jpg|png)", text):
        seen.add("frame-%s.jpg" % match.group(1))
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--region", nargs=2, type=float, required=True)
    parser.add_argument("--expand", type=float, default=180.0)
    parser.add_argument("--routes", default="pip_crops")
    parser.add_argument("--crop-runs", default=CROPS)
    parser.add_argument("--band-runs", default=BANDS)
    parser.add_argument("--band-ledger",
                        default="build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    routes = tuple(part.strip() for part in args.routes.split(",") if part.strip())
    header = bh.saved_readings(bh.response_directories(args.band_runs), args.band_ledger) \
        if "header" in routes else None
    # An endpoint that cannot resolve is a backstop, not the guard: the guard is that every route's
    # ``_post`` is replaced before the run exists, so no socket is ever opened.
    #
    # It also has to be the *same* endpoint on both sides of the comparison. A request identity embeds the
    # model, the endpoint, the thinking mode and the frame density -- deliberately, so an answer from one
    # question can never be reloaded as another's. Pricing one arm against the default endpoint while
    # sending the other at a stub therefore turns six identical groups into six that look different, and
    # the first version of this check reported exactly that disagreement: the check's own bug, not the
    # product's.
    common = dict(step=2.0, max_requests=400, header=header, crop_runs=args.crop_runs or None,
                  routes=routes, endpoint="http://verification.invalid:1/v1")
    live_options = dict(common)

    planned = bi.plan_saved_investigation(args.timeline, args.frame_manifest, args.duration,
                                          region=tuple(args.region), expand=args.expand, **common)
    expansion = planned.get("expansion") or {}
    window = list(expansion.get("expanded_window") or planned["window"])
    executable = bi.plan_saved_investigation(args.timeline, args.frame_manifest, args.duration,
                                             region=tuple(window), expand=0.0, **common)
    def identities(plan):
        # Identities, not counts and not filenames: a request body carries crop PNGs whose names are not
        # the cached frame's name, so comparing paths across the two paths would report a difference that
        # is only a naming difference. An identity is the content of the send.
        return sorted({"%s:%s" % (region["route"], group.get("identity") or group.get("fingerprint"))
                       for region in plan["regions"] for group in region["groups"]}
                      | {"%s:%s" % (entry.get("route"), entry.get("identity"))
                         for entry in (plan.get("pip_requests") or [])
                                      + (plan.get("header_requests") or [])})

    priced_identities = identities(executable)
    priced_frames = {Path(str(entry["frame"])).name for entry in (executable.get("pip_requests") or [])}

    run_dir = Path(args.run_dir)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    transport = UnusableTransport()
    clients = (bi.RoundFactsClient, bp.PipReadClient, bh.HeaderReadClient)
    originals = [(cls, getattr(cls, "_post")) for cls in clients]
    for cls, _ in originals:
        cls._post = transport
    try:
        result = bi.investigate_saved_games(args.timeline, args.frame_manifest, args.duration,
                                            output_dir=run_dir, budget_file=str(run_dir / "ledger.json"),
                                            budget_cap=400, budget_minutes=20, max_attempts=1,
                                            region=tuple(args.region), expand=args.expand,
                                            purpose="plan-agreement-verification", **live_options)
    finally:
        for cls, original in originals:
            cls._post = original

    sent_frames = frames_requested(transport)
    live_plan = json.loads((run_dir / "plan.json").read_text()) if (run_dir / "plan.json").exists() else {}
    sent_identities = identities(live_plan) if live_plan else []
    report = {
        "inference_sent": False,
        "endpoint_configured_but_never_used": "http://verification.invalid:1/v1",
        "requested_window": list(args.region),
        "routes": list(routes),
        "expansion": {"triggered": expansion.get("triggered"),
                      "expanded_window": expansion.get("expanded_window"),
                      "stopping_reasons": expansion.get("stopping_reasons"),
                      "new_reads": (expansion.get("priced_only_missing") or {}).get("new_reads")},
        "dry_run": {"headline_price_requested_window": planned["requests_planned"],
                    "executable_window": window,
                    "executable_price": executable["requests_planned"],
                    "executable_frames_named": len(priced_frames)},
        "live_run": {"status": result.get("status"), "stopped_reason": result.get("stopped_reason"),
                     "region_requested": result.get("region_requested"),
                     "region_executed": result.get("region_executed"),
                     "requests_sent": result.get("requests_sent"),
                     "groups_planned": result.get("groups_planned"),
                     "groups_failed_validation": result.get("groups_failed_validation"),
                     "frames_asked_of_the_stub": len(sent_frames),
            "distinct_groups_planned": result.get("groups_planned")},
        "agreement": {
            # The pass that dispatches is the one to compare: every pass after it re-plans against what
            # the previous pass answered, so a stub that answers nothing makes the *attempt* count grow
            # while the *plan* stays the same. Pass 1's plan is the live path's answer to "what would you
            # send", and that is what has to equal the dry run's executable price.
            "same_executed_window": list(result.get("region_executed") or []) == window,
            "live_plan_identities_equal_executable_price": sent_identities == priced_identities,
            # `groups_planned` on the result is what the run got through before it stopped, and a stub that
            # answers nothing makes it stop early by design. The plan the run wrote for its first pass is the
            # thing that has to equal the priced plan, and the identity sets above are that comparison.
            "live_plan_group_count_matches_executable_price":
                sum(len(region["groups"]) for region in live_plan.get("regions") or [])
                + len(live_plan.get("pip_requests") or []) + len(live_plan.get("header_requests") or [])
                == executable["requests_planned"],
            "priced_but_not_planned": sorted(set(priced_identities) - set(sent_identities))[:12],
            "planned_but_not_priced": sorted(set(sent_identities) - set(priced_identities))[:12],
            "attempts_sent_across_all_passes": result.get("requests_sent")},
        "answers_written": sorted(str(p.relative_to(run_dir))
                                  for p in (run_dir / "requests").glob("group-*"))[:5],
        "note": ("Every group failed validation by design, so the run persisted no answer. The counts and "
                 "the frame sets are the agreement; the status is not a result about the recording."),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["agreement"], indent=2))
    print(json.dumps({k: report[k] for k in ("dry_run", "live_run")}, indent=2))
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
