"""What does reading the clock ahead of its word change, end to end?

Before/after on identical inputs. "Before" is the shipped code with the backwards wording switched off --
one guarded branch in ``parse_round_facts``, consulted only when the forward pattern found nothing, so a
pattern that cannot match is exactly the old parser and nothing else in the reading path moves.

Offline only: reads the saved classification and the frame cache. Nothing is sent, no ledger is touched.

    .venv/bin/python scripts/measure_clock_repair.py \\
        --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \\
        --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \\
        --duration 6928.421136 --out build/work/bb9/clock-repair-v1/before-after.json
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_evidence as be          # noqa: E402
from tekken_vod_helper import boundary_games as bg             # noqa: E402

REGIONS = [(None, 'whole-recording'), ((1200, 2200), '1200-2200'), ((2220, 3200), '2220-3200'),
           ((3200, 4000), '3200-4000'), ((2400, 3200), '2400-3200 pinned'),
           ((5976, 6160), '5976-6160'), ((6156, 6246), '6156-6246'), ((5545, 6342), '5545-6342'),
           ((4464, 5546), '4464-5546'), ((6486, 6867), '6486-6867')]


def structure(region, source, timeline):
    evidence = be.evidence(region, facts=timeline)
    tracked = bg.track_games(evidence["observations"], games_per_set=None, source=source)
    rounds, games = tracked["rounds"], tracked["games"]
    return {
        "observations": len(evidence["observations"]),
        "rows_with_a_clock_reading": sum(1 for o in evidence["observations"]
                                         if o.get("round_clock") is not None),
        "rounds": len(rounds),
        "rounds_with_inferred_start": sum(1 for r in rounds if r.get("start_inferred")),
        "games": len(games),
        "games_with_a_winner": sum(1 for g in games if g.get("winner")),
        "game_bounds_and_winners": [[round(g["start"], 3), round(g["end"], 3), g.get("winner")]
                                    for g in games],
        "round_bounds": [[round(r["start"], 3), round(r.get("end") or -1, 3), r.get("end_basis"),
                          r.get("clock_readings")] for r in rounds],
        "start_bases": dict(Counter(r["start_basis"] for r in rounds)),
        "end_bases": dict(Counter(str(r.get("end_basis")) for r in rounds)),
        "unresolved_kinds": dict(Counter(u["type"] for u in tracked["unresolved"])),
        "candidates": [[round(c["start"], 3), round(c["end"], 3), c["status"], c["games"]]
                       for c in tracked["sets"]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source = bg.source_bounds(manifest=args.frame_manifest, duration=args.duration)
    backwards = bg._CLOCK_BACKWARDS_RE
    arms = {}
    for label, enabled in (("before", False), ("after", True)):
        bg._CLOCK_BACKWARDS_RE = backwards if enabled else re.compile(r"(?!)")
        arms[label] = {name: structure(region, source, args.timeline) for region, name in REGIONS}
    bg._CLOCK_BACKWARDS_RE = backwards

    report = {"inference_sent": False, "arms": arms, "moved": {}}
    for name in arms["after"]:
        before, after = arms["before"][name], arms["after"][name]
        moved = {"rows_newly_read": after["rows_with_a_clock_reading"]
                 - before["rows_with_a_clock_reading"],
                 "rounds": [before["rounds"], after["rounds"]],
                 "games": [before["games"], after["games"]],
                 "games_with_a_winner": [before["games_with_a_winner"], after["games_with_a_winner"]],
                 "rounds_with_inferred_start": [before["rounds_with_inferred_start"],
                                                after["rounds_with_inferred_start"]],
                 "bounds_identical": before["round_bounds"] == after["round_bounds"],
                 "games_identical": before["game_bounds_and_winners"] == after["game_bounds_and_winners"],
                 "candidates": [before["candidates"], after["candidates"]],
                 "end_bases": {k: [before["end_bases"].get(k, 0), after["end_bases"].get(k, 0)]
                               for k in sorted(set(before["end_bases"]) | set(after["end_bases"]))
                               if before["end_bases"].get(k, 0) != after["end_bases"].get(k, 0)},
                 "unresolved": {k: [before["unresolved_kinds"].get(k, 0),
                                    after["unresolved_kinds"].get(k, 0)]
                                for k in sorted(set(before["unresolved_kinds"])
                                                | set(after["unresolved_kinds"]))
                                if before["unresolved_kinds"].get(k, 0)
                                != after["unresolved_kinds"].get(k, 0)}}
        report["moved"][name] = moved
        print('== %-20s rows read +%d | rounds %s -> %s | games %s -> %s | winners %s -> %s | '
              'bounds identical: %s | games identical: %s' % (
                  name, moved["rows_newly_read"], moved["rounds"][0], moved["rounds"][1],
                  moved["games"][0], moved["games"][1], moved["games_with_a_winner"][0],
                  moved["games_with_a_winner"][1], moved["bounds_identical"],
                  moved["games_identical"]))
        if moved["end_bases"]:
            print('     end bases:', json.dumps(moved["end_bases"]))
        if moved["unresolved"]:
            print('     queue:', json.dumps(moved["unresolved"]))
        if moved["candidates"] != [moved["candidates"][0]] * 2:
            print('     candidates before:', json.dumps(moved["candidates"][0]))
            print('     candidates after: ', json.dumps(moved["candidates"][1]))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
