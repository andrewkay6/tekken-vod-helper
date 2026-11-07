#!/usr/bin/env python3
"""How much looking stands behind each credited winner, and does removing it actually change the claim?

Two questions, kept apart on purpose, because the first version of this instrument conflated them and
published an explanation it had not earned.

1. **Support, counted on the credit's own path.** ``game["winner_support"]`` (written by
   :func:`boundary_games._winner_support`) reads the raw per-frame log, not the compressed series, so a
   second frame that agrees is visible. Nothing here guesses at a window: the earlier version scanned a
   span of its own choosing and under-counted, and a review pointed out that surviving a deletion says
   nothing about *why* it survived.

2. **Load-bearing, tested by a deletion proved to have happened.** Each supporting frame is withheld by
   *identity* -- the crop answer for that picture and the timeline row's own ``win_dots`` slot, since the
   merge lets one overwrite the other -- and the recording is re-tracked. A column whose side-reading count
   did not fall is a failed experiment, reported as one rather than as evidence.

What version one got wrong, and why this file exists: it reported "22 of 27 winners change on one withheld
frame, and 5 survived because a reading outside the window carried them". For at least two of those five
nothing was withheld at all -- a crop answer stamped 237.24 joins the row at 237.236988, so matching by
timestamp missed it -- and the explanation was invented after the fact rather than measured.

    env PYTHONPATH=.qwen/tmp/pylibs:.venv/lib/python3.14/site-packages:. /usr/bin/python3 \\
        scripts/audit_winner_support.py --out build/work/bb9/winner-support-v1.json
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be        # noqa: E402
from tekken_vod_helper import boundary_games as bg           # noqa: E402

TIMELINE = "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DURATION = 6928.421136


def track(readings, rows):
    merged, _ = be.merge_crop_readings(rows, readings, (0.0, float("inf")))
    graded, _ = be.grade_rows(merged)
    return bg.track_games(bg.build_observations(graded), games_per_set=None,
                          source=bg.source_bounds(manifest=MANIFEST, duration=DURATION))


def side_readings(readings, rows):
    """Every pip value the corpus can put on a row, per side. The number a drop has to move."""
    merged, _ = be.merge_crop_readings(rows, readings, (0.0, float("inf")))
    return sum(1 for row in merged for value in (row.get("win_dots") or []) if value is not None)


def withhold(readings, rows, identity, side):
    # type: (dict, list, str, str) -> tuple
    """Drop one side's reading of one picture, from the crop answers *and* from the timeline rows.

    Both sources are needed and neither is enough: pip values arrive as crop answers and as fields the
    classifier row already carried, and the merge lets the crop overwrite the row. Returns the edited
    copies and how many readings were removed, so a caller can never mistake a no-op for a result.
    """
    slot = 0 if side == "p1" else 1
    readings = {key: dict(entry) for key, entry in readings.items()}
    rows = [dict(row) for row in rows]
    removed = 0
    for key, entry in readings.items():
        if (bg._frame_identity(key) != identity
                and bg._frame_identity(entry.get("frame")) != identity):
            continue
        values = list(entry.get("win_dots") or [None, None])
        if len(values) <= slot or values[slot] is None:
            continue
        values[slot] = None
        entry["win_dots"] = values
        drawn = list(entry.get("win_dots_drawn") or [None, None])
        if len(drawn) > slot:
            drawn[slot] = None
            entry["win_dots_drawn"] = drawn
        removed += 1
    for row in rows:
        if bg._frame_identity(row.get("frame") or row.get("path")) != identity:
            continue
        values = list(row.get("win_dots") or [None, None])
        if len(values) <= slot or values[slot] is None:
            continue
        values[slot] = None
        row["win_dots"] = values
        removed += 1
    return readings, rows, removed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="build/work/bb9/winner-support-v1.json")
    args = parser.parse_args()

    readings = dict(be.crop_observations(be.crop_runs()))
    rows = be.read_facts(TIMELINE)
    before = side_readings(readings, rows)
    tracked = track(readings, rows)
    games = tracked["games"]
    credited = [g for g in games if g.get("winner") and g.get("winner_basis") == "win_dots"]

    report = {"kind": "winner-support-audit", "inference_sent": False,
              "inputs": {"timeline": TIMELINE, "frame_manifest": MANIFEST, "duration": DURATION,
                         "crop_runs": len(list(be.crop_runs())),
                         "games": len(games),
                         "winners": sum(1 for g in games if g.get("winner")),
                         "winners_credited_from_the_row": len(credited),
                         "side_readings_in_base": before},
              "support_histogram": dict(Counter((g.get("winner_support") or {}).get("frame_count")
                                                for g in credited)),
              "games": []}
    for game in credited:
        support = game.get("winner_support") or {}
        side, start = game["winner"], float(game["start"])
        columns = []
        for identity in support.get("frame_paths") or []:
            edited_readings, edited_rows, removed = withhold(readings, rows, identity, side)
            after = side_readings(edited_readings, edited_rows)
            again = track(edited_readings, edited_rows)
            other = [g for g in again["games"] if abs(float(g["start"]) - start) < 0.01]
            now = other[0] if other else {}
            landed = bool(removed) and after < before
            columns.append({"withheld_frame": Path(identity).name, "side": side,
                            "readings_removed": removed, "side_readings_after": after,
                            "drop_landed": landed,
                            "winner_after": now.get("winner"),
                            "winner_basis_after": now.get("winner_basis"),
                            "game_still_there": bool(other),
                            "support_after": (now.get("winner_support") or {}).get("frame_count"),
                            "load_bearing_on_this_frame": landed
                            and now.get("winner") != side})
        report["games"].append({
            "start": round(start, 6), "end": round(float(game["end"]), 6), "side": side,
            "supporting_frames": support.get("frame_count"), "frames": support.get("frames"),
            "seconds_apart": support.get("seconds_apart"), "deciding_at": support.get("deciding_at"),
            "single_reading": bool(support.get("single_reading")),
            "set_aside_excluded": support.get("set_aside_excluded"),
            "duplicates_excluded": support.get("duplicate_identities_excluded"),
            "drop_columns": columns,
            "load_bearing": any(entry["load_bearing_on_this_frame"] for entry in columns),
            "unverified_drop": any(not entry["drop_landed"] for entry in columns)})

    report["totals"] = {
        "winners_credited_from_the_row": len(credited),
        "single_supported": sum(1 for r in report["games"] if r["single_reading"]),
        "multi_supported": sum(1 for r in report["games"] if not r["single_reading"]),
        "load_bearing": sum(1 for r in report["games"] if r["load_bearing"]),
        "every_supporting_frame_alone_is_load_bearing": all(
            entry["load_bearing_on_this_frame"] for r in report["games"]
            for entry in r["drop_columns"]),
        "experiments_with_a_failed_drop": sum(1 for r in report["games"] if r["unverified_drop"])}
    report["queue"] = dict(Counter(item["type"] for item in tracked["unresolved"]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"inputs": report["inputs"], "support_histogram": report["support_histogram"],
                      "totals": report["totals"], "queue": report["queue"]}, indent=1, sort_keys=True))
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
