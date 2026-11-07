"""Are the crop windows pointed at a conclusion, or at a guess at one?

The crop arm reads the pip row at a round's end anchor and at +2 s / +4 s after it. That only names a
winner when the anchor is the round's *conclusion*. Where the end was never observed the anchor is the last
frame whose digits were read falling -- and if the reading stopped early, those three frames can sit a
whole round before the deciding increment.

This walks every crop window the planner would fund and reports, per window, whether its anchor round had
an observed end marker, and how far the anchor sits from the next thing that says the round ended (a
result transition, an end marker on a later row, or a screen that is not play). Nothing is sent.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_evidence as be          # noqa: E402
from tekken_vod_helper import boundary_games as bg             # noqa: E402
from tekken_vod_helper import boundary_investigate as bi       # noqa: E402

# The clock stated *before* its word ("29s timer", "60-second timer"), which the tracker's pattern reads
# only after the word, so those rows carry no reading for it.
_CLOCK_BEFORE_WORD = __import__("re").compile(
    r"\b(\d{1,2})\s*[- ]?\s*(?:s\b|secs?\b|seconds?\b)[^.;,]{0,14}?\b(?:round\s+)?(?:timer|clock)\b",
    __import__("re").I)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--step", type=float, default=2.0)
    parser.add_argument("--region", nargs=2, type=float, default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    window = tuple(args.region) if args.region else None
    planned = bi.plan_saved_investigation(args.timeline, args.frame_manifest, args.duration,
                                          region=window, step=args.step, expand=0.0,
                                          max_requests=4000, state_only=False)
    evidence = be.evidence(window, facts=args.timeline)
    tracked = bg.track_games(evidence["observations"], games_per_set=None)
    rows = sorted(evidence["row_records"], key=lambda r: r["timestamp"])

    def clock_on(row):
        """The round clock a row states, whether or not the tracker's pattern could read it.

        The second half is the point. "29s timer" and "60-second timer" put the number before the word, and
        the pattern reads it after, so the tracker holds no series there while the recording does state one.
        Reported apart so a gap in the parsing is never counted as a gap in the footage.
        """
        fact = bg.parse_round_facts(dict(row))
        if fact["round_clock"] is not None:
            return fact["round_clock"], True
        match = _CLOCK_BEFORE_WORD.search(row.get("evidence") or "")
        if match and int(match.group(1)) <= 60:
            return int(match.group(1)), False
        return None, None

    def continued_play(anchor, owner):
        """Digits that fall *after* the anchor, inside the round the anchor claims to end.

        Two decreasing readings is the proof that the crop would look too early: a round still counting
        down has not produced its final pip row. The walk stops at the first thing that ends the question --
        a row that leaves gameplay, an end marker, or the following round's own opening (a clock that came
        back *up*, or any row at or after the next round's start). Without that stop this measures the next
        round's countdown as if it were this one's and every anchor looks premature, which is exactly the
        alarm this script exists not to raise.
        """
        if owner is None:
            return None
        following = sorted(r["start"] for r in tracked["rounds"] if r["start"] > owner["start"] + 0.5)
        boundary = next((start for start in following if start > anchor), anchor + 120.0)
        seen, previous = [], None
        for row in rows:
            ts = row["timestamp"]
            if ts <= anchor + 0.001 or ts >= boundary:
                continue
            if row.get("state") != "gameplay":
                break
            marker = bg.parse_round_facts(dict(row))
            if marker["round_end"] or marker["result_transition"]:
                break
            value, read_by_tracker = clock_on(row)
            if value is None:
                continue
            if previous is not None and value > previous + 2:
                break                                   # the digits rose: a different round
            seen.append({"at": round(ts, 3), "clock": value, "read_by_the_tracker": read_by_tracker})
            previous = value
        falling = len(seen) >= 2 and seen[-1]["clock"] < seen[0]["clock"]
        return {"readings_after_anchor": seen, "play_continued": falling,
                "seconds_of_continued_play": round(seen[-1]["at"] - anchor, 3) if (seen and falling) else 0.0,
                "only_the_prose_stated_them": bool(seen) and not any(
                    entry["read_by_the_tracker"] for entry in seen)}

    def conclusion_after(anchor):
        """The next row that says the round is over: an end marker, a result transition, or non-play.

        Not the same question as whether play went on past the anchor, which is what :func:`continued_play`
        answers. A marker 60 s late can mean the tail was unreadable, and only the digits tell the two apart.
        """
        for row in rows:
            if row["timestamp"] <= anchor + 0.001:
                continue
            if row.get("state") != "gameplay":
                return {"at": round(row["timestamp"], 3), "seconds_after_anchor":
                        round(row["timestamp"] - anchor, 3), "kind": "left_play:%s" % row["state"]}
            fact = bg.parse_round_facts(dict(row))
            if fact["round_end"] or fact["result_transition"]:
                return {"at": round(row["timestamp"], 3),
                        "seconds_after_anchor": round(row["timestamp"] - anchor, 3),
                        "kind": fact["round_end"] or "result_transition"}
            if row["timestamp"] - anchor > 90.0:
                break
        return None

    items = []
    for entry in planned.get("pip_requests") or []:
        if entry.get("role") != "round_end_evidence_frame":
            continue
        anchor = float(entry["timestamp"])
        owner = next((r for r in tracked["rounds"]
                      if r["start"] - 0.5 <= anchor <= (r.get("end") or 0) + 0.5), None)
        basis = (owner or {}).get("end_basis")
        observed = bool(basis and basis != "round_end_not_observed")
        continued = continued_play(anchor, owner)
        items.append({"at": round(anchor, 3), "frame": Path(str(entry["frame"])).name,
                      "round_end_basis": basis, "anchor_is_observed_conclusion": observed,
                      "play_continued_past_the_anchor": bool(continued and continued["play_continued"]),
                      "seconds_of_continued_play": (continued or {}).get("seconds_of_continued_play"),
                      "continued_only_in_prose_the_tracker_cannot_read":
                          bool(continued and continued.get("only_the_prose_stated_them")),
                      "next_conclusion": conclusion_after(anchor) if not observed else None})
    tally = Counter(("observed" if item["anchor_is_observed_conclusion"]
                     else str(item["round_end_basis"])) for item in items)
    continued = [item for item in items if item["play_continued_past_the_anchor"]]
    late = [item for item in items if not item["anchor_is_observed_conclusion"]
            and (item["next_conclusion"] or {}).get("seconds_after_anchor", 0) > 6.0]
    report = {"inference_sent": False, "region": list(window) if window else None,
              "windows": len(items), "by_end_basis": dict(tally),
              "anchors_not_a_conclusion": sum(1 for i in items
                                              if not i["anchor_is_observed_conclusion"]),
              "anchors_where_play_clearly_continued": len(continued),
              "of_which_the_digits_were_only_in_prose": sum(
                  1 for i in continued if i["continued_only_in_prose_the_tracker_cannot_read"]),
              "continued_seconds": sorted({i["seconds_of_continued_play"] for i in continued}),
              "of_which_the_next_conclusion_is_over_6s_later": len(late),
              "worst_gap_seconds": max([i["next_conclusion"]["seconds_after_anchor"]
                                        for i in late], default=None),
              "items": items}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("windows", "by_end_basis",
                                             "anchors_not_a_conclusion",
                                             "of_which_the_next_conclusion_is_over_6s_later",
                                             "worst_gap_seconds")}, indent=2))
    for item in continued[:12]:
        print("  anchor %9.3f (%s) basis=%-24s play continued %5.1f s  prose-only=%s" % (
            item["at"], item["frame"], item["round_end_basis"], item["seconds_of_continued_play"],
            item["continued_only_in_prose_the_tracker_cannot_read"]))
    for item in late[:12]:
        print("  anchor %9.3f (%s) basis=%-24s next conclusion %s s later: %s" % (
            item["at"], item["frame"], item["round_end_basis"],
            item["next_conclusion"]["seconds_after_anchor"], item["next_conclusion"]["kind"]))
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
