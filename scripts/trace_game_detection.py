#!/usr/bin/env python3
"""Trace rounds and games out of an already-classified timeline. No model, no frames, no source.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/trace_game_detection.py \
        --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
        --output-dir build/work/bb9/games-v1 [--region START END]

Writes ``games-trace.json`` (compact observations, rounds, games, unresolved items) and
``games-trace.md`` (a readable walk of the sequence) into a fresh directory, then prints the counts.
This is the offline half of the game-detection milestone: everything it reports comes from evidence
that is already on disk.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_games  # noqa: E402


def load_rows(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("observations")
    if not isinstance(data, list) or not data:
        raise SystemExit("timeline holds no observations: {}".format(path))
    return data


def _fmt(value, digits=1):
    return "none" if value is None else "{:.{}f}".format(value, digits)


def render_markdown(result, region):
    lines = ["# Round and game trace", ""]
    fmt = result["format"]
    lines.append("Format configured, never inferred from labels: first to **{} rounds** per game, "
                 "**{} games** per set.".format(fmt["rounds_per_game"], fmt["games_per_set"]))
    lines.append("")
    acc = result["accounting"]
    lines.append("## Accounting")
    for key in ("observations", "gameplay_observations", "clock_readings", "rounds", "games",
                "games_incomplete", "gameplay_inside_games"):
        lines.append("- {}: {}".format(key, acc[key]))
    lines.append("")
    lines.append("## Rounds")
    lines.append("| # | start | end | start basis | end basis | clock at start | readings | win dots | result |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, rnd in enumerate(result["rounds"], 1):
        if region and not (region[0] <= rnd["start"] <= region[1]):
            continue
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            i, _fmt(rnd["start"], 2), _fmt(rnd.get("end"), 2), rnd["start_basis"],
            rnd.get("end_basis"), _fmt(rnd.get("clock_at_start"), 0), rnd["readings"],
            rnd.get("win_dots_seen") or "-", "yes" if rnd.get("result_transition") else "-"))
    lines.append("")
    lines.append("## Games")
    for i, game in enumerate(result["games"], 1):
        lines.append("{}: {} -> {} | {} rounds | status **{}** | basis {}".format(
            i, _fmt(game["start"], 2), _fmt(game["end"], 2), game["rounds"], game["status"],
            game["basis"]))
    lines.append("")
    lines.append("## Unresolved")
    counts = {}
    for item in result["unresolved"]:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    for kind, count in sorted(counts.items()):
        lines.append("- {}: {}".format(kind, count))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--rounds-per-game", type=int, default=3)
    parser.add_argument("--games-per-set", type=int, default=3)
    parser.add_argument("--region", nargs=2, type=float, metavar=("START", "END"), default=None,
                        help="Restrict the readable trace to this time range.")
    parser.add_argument("--max-unresolved", type=int, default=200)
    args = parser.parse_args()

    out = Path(args.output_dir)
    if out.exists() and any(out.iterdir()):
        raise SystemExit("refusing to overwrite a non-empty output dir: {}".format(out))
    out.mkdir(parents=True, exist_ok=True)

    rows = load_rows(args.timeline)
    observations = boundary_games.build_observations(rows)
    result = boundary_games.track_games(observations, rounds_per_game=args.rounds_per_game,
                                        games_per_set=args.games_per_set)
    summary = {
        "schema": result["schema"],
        "format": result["format"],
        "accounting": result["accounting"],
        "rounds": result["rounds"],
        "games": result["games"],
        "sets": result["sets"],
        "unresolved": result["unresolved"][:args.max_unresolved],
        "unresolved_truncated": max(0, len(result["unresolved"]) - args.max_unresolved),
    }
    boundary_games.write_trace(summary, out / "games-trace.json")
    (out / "games-trace.md").write_text(render_markdown(result, args.region), encoding="utf-8")
    print(json.dumps({"output_dir": str(out), "accounting": summary["accounting"],
                      "unresolved_kinds": _kinds(result["unresolved"])}, indent=2))


def _kinds(unresolved):
    kinds = {}
    for item in unresolved:
        kinds[item["type"]] = kinds.get(item["type"], 0) + 1
    return kinds


if __name__ == "__main__":
    main()
