#!/usr/bin/env python3
"""How much of a recording can be concluded without being told the rules. Read-only, asks nothing.

One number decides how far this project can go on footage alone: the share of game conclusions that a
station change settles. Below that line a set's end is a fact about the event (its format) and nothing in the
pixels; above it, the tape says the players stopped and no target is needed to agree. Both halves are worth
knowing before a trial is priced, and neither is guessable -- so this measures them.

It runs the shipped edge walk, `boundary_games._edge_events`, at every between-game screen on a classified
timeline. Not an approximation of it: an earlier hand-rolled count treated *any* gameplay frame past a bound
as live play, which is the overreach the locality fix removed, and it happened to agree only because this
recording's frozen result frames sit behind their cues.

    env PYTHONPATH=. .venv/bin/python scripts/measure_set_concludability.py
    env PYTHONPATH=. .venv/bin/python scripts/measure_set_concludability.py \\
        --timeline path/to/timeline-with-facts.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_games as bg  # noqa: E402

DEFAULT_TIMELINE = "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
STATES = {"cue": "a station change settles it: the set can be concluded with no format supplied",
          "live": "an observed clock change settles it: the set is still running, and stays open",
          "nothing": "nothing within the walk settles it: unresolved"}


def episodes(observations):
    """Consecutive rows sharing a state, collapsed -- a screen held for 110 s is one episode."""
    out = []
    for obs in sorted(observations, key=lambda o: float(o.get("timestamp", 0.0))):
        state = str(obs.get("screen_state") or "unknown")
        at = float(obs["timestamp"])
        if out and out[-1]["state"] == state:
            out[-1]["last"] = at
            continue
        out.append({"state": state, "first": at, "last": at})
    return out


def measure(timeline, lookahead=bg.EDGE_LOOKAHEAD_SECONDS):
    rows = [entry for entry in json.loads(Path(str(timeline)).read_text(encoding="utf-8"))
            if isinstance(entry.get("timestamp"), (int, float))]
    if not rows:
        raise SystemExit("%s holds no classified rows" % timeline)
    observations = bg.build_observations(sorted(rows, key=lambda r: r["timestamp"]))
    ordered = sorted(observations, key=lambda o: float(o.get("timestamp", 0.0)))
    conclusions = [e for e in episodes(ordered) if e["state"] == "post_match_menu"]
    if not conclusions:
        raise SystemExit("no between-game screen in %s: nothing on this timeline looks like a game ending"
                         % timeline)

    tally, examples = {}, {}
    for episode in conclusions:
        bound = episode["last"]
        inside = [o for o in ordered
                  if float(o.get("timestamp", 0.0)) <= bound and o.get("round_clock") is not None]
        anchor = inside[-1]["round_clock"] if inside else None
        events, _walked = bg._edge_events(ordered, bound, bound + lookahead, anchor, bound, "after")
        kind = events[0]["kind"] if events else "nothing"
        tally[kind] = tally.get(kind, 0) + 1
        examples.setdefault(kind, []).append(round(bound, 3))

    total = sum(tally.values())
    return {"timeline": str(timeline), "classified_rows": len(rows),
            "game_conclusions": total, "lookahead_seconds": lookahead,
            "settled": {kind: {"count": tally.get(kind, 0),
                               "share": round(tally.get(kind, 0) / float(total), 4),
                               "meaning": label, "examples": examples.get(kind, [])[:6]}
                        for kind, label in STATES.items()},
            "reading": "'cue' is the ceiling on format-free recovery: those sets end on the tape. 'live' is "
                       "not a failure -- a conclusion followed by another round is the same set going on, "
                       "and reading it as a boundary is the error this measure exists to catch. 'nothing' "
                       "counts bounds where the recording simply stops being legible, which is a question "
                       "for the operator or the next frame budget, not an answer",
            "inference_sent": False, "attempts_charged_now": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeline", default=DEFAULT_TIMELINE)
    parser.add_argument("--lookahead", type=float, default=bg.EDGE_LOOKAHEAD_SECONDS,
                        help="How far past a conclusion the walk may reach; defaults to the code's own.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    report = measure(args.timeline, args.lookahead)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
