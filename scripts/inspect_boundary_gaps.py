"""Label-guided diagnostics: python scripts/inspect_boundary_gaps.py PROJECT.json."""

import json
import math
import statistics
import argparse
import sys

def inspect_gaps(project, limit=5):
    if not isinstance(project, dict):
        raise ValueError("project must be a dict")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    matches = project.get("matches", project.get("segments", []))
    if not isinstance(matches, list):
        raise ValueError("matches/segments must be a list")

    proj_dur = project.get("duration", None)
    if proj_dur is not None and (
        isinstance(proj_dur, bool) or not isinstance(proj_dur, (int, float))
        or not math.isfinite(proj_dur) or proj_dur <= 0
    ):
        raise ValueError("duration must be positive and finite")

    norm = []
    for i, m in enumerate(matches):
        if not isinstance(m, dict):
            raise ValueError("each match must be an object")
        s = m.get("start", m.get("start_time"))
        e = m.get("end", m.get("end_time"))
        if isinstance(s, bool) or (e is not None and isinstance(e, bool)):
            raise ValueError("Boolean values not allowed for time fields")
        if not isinstance(s, (int, float)):
            raise ValueError("start must be numeric")
        if not math.isfinite(s) or s < 0:
            raise ValueError("start must be nonnegative and finite")

        e_is_none = e is None
        if not e_is_none:
            if not isinstance(e, (int, float)):
                raise ValueError("end must be numeric")
            if not math.isfinite(e):
                raise ValueError("end must be finite")

        s_f = float(s)
        e_f = float(e) if not e_is_none else None

        if e_f is not None and s_f >= e_f:
            raise ValueError("start must be less than end")

        if proj_dur is not None:
            if s_f >= proj_dur or (e_f is not None and e_f > proj_dur):
                raise ValueError("Boundaries exceed project duration")

        norm.append({"idx": i, "start": s_f, "end": e_f, "end_derived": False})

    norm.sort(key=lambda x: x["start"])

    prev_end = -float('inf')
    for m in norm:
        if m["start"] < prev_end:
            raise ValueError("Overlapping intervals detected")
        if m["end"] is not None:
            prev_end = m["end"]

    unresolved = []
    for j in range(len(norm)):
        if norm[j]["end"] is None:
            if j + 1 < len(norm):
                norm[j]["end"] = norm[j+1]["start"]
                norm[j]["end_derived"] = True
            elif proj_dur is not None:
                norm[j]["end"] = proj_dur
                norm[j]["end_derived"] = True
            else:
                unresolved.append(norm[j]["idx"])

    gaps = []
    for j in range(len(norm) - 1):
        curr, nxt = norm[j], norm[j+1]
        if curr["end"] is not None and not curr["end_derived"]:
            gap = nxt["start"] - curr["end"]
            if gap > 0:
                gaps.append({
                    "prev_idx": curr["idx"],
                    "next_idx": nxt["idx"],
                    "previous_end": round(curr["end"], 4),
                    "next_start": round(nxt["start"], 4),
                    "gap_seconds": round(gap, 4),
                    "relative_position": round(curr["end"] / proj_dur, 4) if proj_dur else None
                })

    gaps.sort(key=lambda x: x["gap_seconds"], reverse=True)
    ranked_gaps = gaps[:limit]

    if ranked_gaps and proj_dur and gaps:
        med = statistics.median([g["gap_seconds"] for g in gaps])
        for g in ranked_gaps:
            g["median_positive_gap"] = round(med, 4)
            g["ratio_to_median"] = round(g["gap_seconds"] / med, 4) if med > 0 else None

    long_matches = []
    exp_bounded = [(m["idx"], m["end"] - m["start"]) for m in norm if m["end"] is not None and not m["end_derived"]]
    if exp_bounded:
        lens = [d for _, d in exp_bounded]
        med_dur = statistics.median(lens)
        threshold = max(1.0, med_dur * 2.0)
        flagged = [(i, d) for i, d in exp_bounded if d > threshold]
        flagged.sort(key=lambda x: x[1], reverse=True)
        for i, d in flagged[:limit]:
            long_matches.append({
                "match_idx": i,
                "duration_seconds": round(d, 4),
                "median_duration": round(med_dur, 4),
                "ratio_to_median": round(d / med_dur, 4) if med_dur > 0 else None
            })

    return {
        "disclaimer": "These are label-guided candidates, not proof of a false start. Never alters labels or detector.",
        "project_duration": round(proj_dur, 4) if proj_dur else 0,
        "derived_indices": [m["idx"] for m in norm if m["end_derived"]],
        "unresolved_indices": unresolved,
        "gaps": ranked_gaps,
        "long_matches": long_matches
    }


def main():
    parser = argparse.ArgumentParser(description="Historical gap diagnostic evaluator.")
    parser.add_argument("project", help="Path to JSON project file")
    parser.add_argument("--limit", type=int, default=5, help="Max candidates to report")
    args = parser.parse_args()
    try:
        with open(args.project, encoding="utf-8") as f:
            data = json.load(f)
        result = inspect_gaps(data, limit=args.limit)
        print(json.dumps(result, indent=2))
    except (ValueError, json.JSONDecodeError, OSError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
