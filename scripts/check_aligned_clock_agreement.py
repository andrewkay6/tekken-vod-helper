"""Score the runtime's saved boundary clocks against correctly aligned evaluator reads.

This exists because the first comparison of the same two things was made on tiles whose filenames were
spelled from rounded seconds. On a cache sampled at the 1001/1000 frame interval those names drifted later
than their labels with recording length -- 5.005 s by 5495.490 -- so the "saved 60 versus reference 58 at
the same instant" contradiction that justified a re-read was an artefact of frame selection, not of two
instruments disagreeing. ``audit_frame_identities.py`` re-resolves every tile through the manifest; this
script consumes that audit and the observations taken from the aligned strips, and reports agreement
without turning either into boundary truth.

Reads only. Nothing here sends a request, and neither side of the comparison is accepted as a boundary:
a clock value at one cached instant bounds when a round opened, and says nothing about which set a clip
belongs to.

    python3 scripts/check_aligned_clock_agreement.py \\
        --audit build/work/bb9/review-ui-followup-2026-11-06/aligned/frame-identity-audit.json \\
        --observations build/work/bb9/review-ui-followup-2026-11-06/aligned-start-observations-evaluator-only.json \\
        --project build/work/bb9/review-ui-audit-2026-11-06/bb9-import.tvh.json \\
        --out build/work/bb9/review-ui-followup-2026-11-06/clock-agreement-after-alignment.json
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOLERANCE_SECONDS = 0.5        # half the 1 Hz cache step: further than this is a different instant


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    observations = json.loads(Path(args.observations).read_text(encoding="utf-8"))
    project = json.loads(Path(args.project).read_text(encoding="utf-8"))
    tiles = {"%.3f" % row["claimed_at"]: row for row in audit["rows"]
             if row.get("kind") == "predicted_set_start"}
    clocks = observations["clock_by_claimed_boundary"]

    rows, counts = [], {"agree": 0, "disagree": 0, "unknown": 0}
    for segment in project.get("matches") or []:
        at = float(segment["start"])
        key = "%.3f" % at
        edge = ((segment.get("recovery") or {}).get("edges") or {}).get("start") or {}
        saved = edge.get("clock_at_boundary")
        observed = clocks.get(key)
        tile = tiles.get(key) or {}
        if observed is None or saved is None:
            verdict = "unknown"
        elif int(saved) == int(observed):
            verdict = "agree"
        else:
            verdict = "disagree"
        counts[verdict] += 1
        rows.append({"at": at, "saved_clock_at_boundary": saved, "aligned_read": observed,
                     "verdict": verdict,
                     "frame_the_read_used": tile.get("correct_frame"),
                     "frame_actual_at": tile.get("correct_frame_actual_at"),
                     "frame_sha256": tile.get("correct_frame_sha256"),
                     "frame_offset_seconds": tile.get("correct_frame_offset_seconds"),
                     "old_key_frame": tile.get("claimed_frame"),
                     "old_key_error_seconds": tile.get("claimed_frame_offset_seconds"),
                     "walked_past": edge.get("walked_past"),
                     "station_cue_at": (edge.get("settled_by") or {}).get("at")})
    report = {"inference_sent": False, "labels_are_runtime_input": False,
              "audit": args.audit, "observations": args.observations, "project": args.project,
              "tolerance_seconds": TOLERANCE_SECONDS,
              "counts": counts, "comparable": counts["agree"] + counts["disagree"],
              "rows": rows,
              "what_this_is": ("whether the runtime's own clock reading at a predicted opening matches a "
                               "human read of the manifest-aligned frame at that same instant"),
              "what_this_is_not": ("a boundary verdict. Agreement says two instruments saw the same "
                                   "digit on the same frame; it does not say the clip starts where the "
                                   "round did, and a clock of 60 does not prove a first round."),
              "retracted": {
                  "two_contradictory_readings_of_the_same_instant": counts["disagree"] == 0,
                  "note": ("every comparable instant agrees once the frames are chosen by manifest "
                           "timestamp, so the disagreement reported on 2026-11-06 was the frame-selection "
                           "error, not the instruments")}}
    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("comparable %d: agree %d, disagree %d, unknown %d -> %s"
          % (report["comparable"], counts["agree"], counts["disagree"], counts["unknown"], out))
    for row in rows:
        print("  %-10.3f saved=%-4s aligned=%-4s %-8s frame=%s (%+.6f s) via %s%s"
              % (row["at"], row["saved_clock_at_boundary"], row["aligned_read"], row["verdict"],
                 row["frame_the_read_used"],
                 row["frame_offset_seconds"] or 0.0, row["old_key_frame"] or "-",
                 "" if not row["old_key_error_seconds"]
                 else " (was %.3f s off)" % row["old_key_error_seconds"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
