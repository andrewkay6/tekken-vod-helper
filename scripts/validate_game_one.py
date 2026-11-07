#!/usr/bin/env python3
"""Can one complete BB9 game be recovered end to end from pip-row readings and the clock?

Not a new engine: this merges crop-arm observations into the timeline facts the first pilot already
built, hands them to ``boundary_games.track_games`` -- the tracker under test, unmodified -- and checks
the three things the user asked acceptance to include:

    boundaries    one game, opened at the first round start and closed at the game's decisive pip
    progression   the per-round pip sequence, side-specific, with no invented pip on an absent row
    winner        a named side whose round-win pips reached three, contributing a set win

An unresolved outcome is a valid *report* and is printed as one, but it is recorded here as a failed
acceptance check, because "complete game recovered" has to mean the sequence was read, not that it was
bracketed. Readings that have not been requested yet are listed as missing evidence, never scored as
wrong and never counted as absent rows.

The round format is configured (first-to-three rounds), never inferred from labels or from how many
games happened to appear.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/validate_game_one.py
    env PYTHONPATH=. .venv/bin/python scripts/validate_game_one.py --out build/work/bb9/game1-validate-v1
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_games  # noqa: E402
from tekken_vod_helper import boundary_evidence as be  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WINDOW = (44.0, 262.0)          # the whole of BB9 game 1 plus the opening of game 2
FIXTURE = ROOT / "build/work/bb9/pip-crops-v1/annotations.json"
NEW = ROOT / "build/work/bb9/game1-validate-v1/annotations-new.json"
PILOT_FACTS = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
RUN_DIRS = [ROOT / "build/work/bb9/pip-crops-v2",          # the 8 verified readings, reused free
            ROOT / "build/work/bb9/game1-validate-v1/run"]  # the new frames, once sent
SIDES = ("LEFT", "RIGHT")


def crop_observations(run_dirs=None, stats=None):
    # type: (Optional[Sequence[Path]], Optional[dict]) -> dict
    """The canonical crop readings (see :func:`tekken_vod_helper.boundary_evidence.crop_observations`).

    Kept here as a name, not an implementation: this harness was the join's first home, and the tests that
    monkeypatch ``RUN_DIRS`` still work. The merge itself now lives in the package so the public recovery
    command cannot grow a second copy of it.
    """
    return be.crop_observations(run_dirs if run_dirs is not None else RUN_DIRS, stats)


def frame_of(row):
    # type: (Dict[str, Any]) -> str
    """The frame a row describes -- the package's rule, aliased for existing callers."""
    return be.frame_of(row)


def merged_rows(observations, window=None):
    # type: (dict, Optional[Tuple[float, float]]) -> tuple
    """The pilot's clock facts, joined to crop readings inside ``window`` by the canonical merge."""
    rows, stats = be.merge_crop_readings(be.read_facts(PILOT_FACTS, window or WINDOW), observations,
                                         window or WINDOW)
    # The key this harness has always published; renaming it would break every saved report's reader.
    stats["unjoinable_pilot_rows"] = stats.pop("unjoinable_rows")
    return rows, stats


def check_acceptance(result, expectations, observations, new_frames):
    # type: (dict, dict, dict, list) -> dict
    """Each criterion on its own, with the evidence or the missing evidence named.

    Progression is read from the tracker's own per-round ``win_dots_seen`` sequences, not from a
    time window I reconstruct here: if the check and the tracker disagree about which reading belongs
    to which round, the check would pass on a wrong segmentation.
    """
    games = [g for g in result.get("games") or []]
    rounds = {r.get("id", i): r for i, r in enumerate(result.get("rounds") or [])}
    game1 = games[0] if games else None
    game1_rounds = [rounds.get(i) for i in ((game1 or {}).get("round_ids") or [])]
    game1_rounds = [r for r in game1_rounds if r]

    progression = [[((r.get("win_dots_seen") or {}).get("p1") or [None])[-1],
                    ((r.get("win_dots_seen") or {}).get("p2") or [None])[-1]] for r in game1_rounds]
    wanted = expectations["pip_progression"]          # [[0,0],[1,0],[1,1],[1,2],[1,3]]
    next_game = games[1] if len(games) > 1 else None
    winner = game1 if game1 and game1.get("winner") else None
    expected_side = {"LEFT": "p1", "RIGHT": "p2"}.get(
        expectations["game_winner_side"], expectations["game_winner_side"])
    # Some pips update only after KO, while the round's timestamp is the KO itself.
    # The next round's opening can confirm the previous round's outcome; this is
    # temporal evidence, not a new segmentation based on evaluator timestamps.
    post_round = []
    for index, rnd in enumerate(game1_rounds):
        following = game1_rounds[index + 1] if index + 1 < len(game1_rounds) else None
        pair = []
        for side in ("p1", "p2"):
            values = list((rnd.get("win_dots_seen") or {}).get(side) or [])
            if following:
                values += list((following.get("win_dots_seen") or {}).get(side) or [])[:1]
            pair.append(max(values) if values else None)
        post_round.append(pair)
    missing_frames = [f["timestamp"] for f in new_frames
                      if Path(f["frame"]).name not in {Path(k).name for k in observations}]
    # Anchors are evaluator-side only: they grade a detected boundary, they never create one.
    anchors = expectations.get("boundary_anchors") or {}
    start_expected = anchors.get("game_start", 53.05)
    end_expected = anchors.get("game_end", 237.24)
    next_expected = anchors.get("next_game_open", 249.25)
    tolerance = anchors.get("tolerance_seconds", 3.0)
    expected_rounds = expectations.get("expected_rounds", len(wanted) - 1)
    checks = {
        "boundaries": {
            "pass": bool(game1 and len(game1_rounds) == expected_rounds and game1.get("end")
                         and next_game and next_game.get("start") > game1["end"]
                         and abs(game1["start"] - start_expected) <= tolerance
                         and abs(game1["end"] - end_expected) <= tolerance
                         and abs(next_game["start"] - next_expected) <= tolerance),
            "observed": {"games_found": len(games),
                         "game_1_rounds": len(game1_rounds),
                         "game_1_start": (game1 or {}).get("start"),
                         "game_1_end": (game1 or {}).get("end"),
                         "game_1_end_basis": (game1 or {}).get("basis"),
                         "game_2_start": (next_game or {}).get("start"),
                         "game_2_end": (next_game or {}).get("end")},
            "required": {"game_1_rounds": expected_rounds, "game_1_end_seen": True,
                         "game_2_opens_after_game_1_closes": True},
            "expected_start": start_expected, "expected_end": end_expected,
            "expected_next_game_open": next_expected, "tolerance_seconds": tolerance,
        },
        "progression": {
            # The scoreline after each round's conclusion. Round 1 starts at 0-0, so the progression
            # under test is the four post-round states.
            "pass": post_round == wanted[1:],
            "observed_per_round_end": progression, "required_after_each_round": wanted[1:],
            "confirmed_post_round": post_round,
            "full_reading_sequences": [r.get("win_dots_seen", {}) for r in game1_rounds],
        },
        "winning_side": {
            "pass": bool(winner and winner.get("winner") == expected_side
                         and winner.get("counts_toward_set") and not winner.get("contested")),
            "observed": {k: winner.get(k) for k in
                         ("winner", "winner_basis", "counts_toward_set", "contested",
                          "contested_reasons", "dot_regressions", "conclusion_signals")}
                        if winner else None,
            "required": {"winner": expectations["game_winner_side"], "counts_toward_set": True,
                         "contested": False},
        },
        "no_invented_pip_on_absent_row": {
            "pass": not [o["timestamp"] for o in (observations or {}).values()
                        if o["win_dots"] == [0, 0] and not o["pip_row_drawn"]],
            "observed": [o["timestamp"] for o in (observations or {}).values()
                        if o["win_dots"] == [0, 0] and not o["pip_row_drawn"]],
        },
        "all_requested_frames_answered": {"pass": not missing_frames,
                                          "missing": missing_frames},
    }
    # Evaluation truth is used only here, after detection, never in merged_rows.
    actual_by_name = {Path(k).name: o for k, o in observations.items()}
    mismatches = []
    for frame in new_frames:
        actual = actual_by_name.get(Path(frame["frame"]).name)
        expected = [frame["pips"][side] for side in SIDES]
        if actual is None or actual["win_dots"] != expected or \
                actual["pip_row_drawn"] is not frame["row_drawn"]:
            mismatches.append(frame["timestamp"])
    checks["new_readings_match_source_annotations"] = {"pass": not mismatches,
                                                       "mismatches": mismatches}
    return {"checks": checks,
            "accepted": all(c["pass"] for c in checks.values()),
            "games": games, "unresolved": result.get("unresolved"),
            "missing_evidence": {"new_frames_not_yet_read": missing_frames,
                                 "note": "unanswered frames are missing evidence, never read as an "
                                         "absent pip row or as a zero"},
            "readings_reused_from_the_crop_run": sorted(
                o["from_group"] for o in (observations or {}).values()
                if "pip-crops-v2" in o["from_group"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "build/work/bb9/game1-validate-v1"))
    parser.add_argument("--new-annotations", default=str(NEW),
                        help="Per-frame expectations for the game under validation (evaluator only).")
    parser.add_argument("--fixture-annotations", default=str(FIXTURE),
                        help="Earlier annotated fixture whose readings are reused, never re-sent.")
    parser.add_argument("--run-dirs", default=",".join(str(r) for r in RUN_DIRS),
                        help="Comma list of crop run directories holding saved replies.")
    parser.add_argument("--window", default="%g,%g" % WINDOW,
                        help="Seconds: the game plus the opening of the next one.")
    parser.add_argument("--rounds-per-game", type=int, default=3,
                        help="Configured format. Never inferred from how many rounds were seen.")
    args = parser.parse_args()

    window = tuple(float(v) for v in args.window.split(","))
    run_dirs = [Path(v.strip()) for v in args.run_dirs.split(",") if v.strip()]
    fixture = json.loads(Path(args.fixture_annotations).read_text(encoding="utf-8"))
    new = json.loads(Path(args.new_annotations).read_text(encoding="utf-8"))
    observations = crop_observations(run_dirs)
    rows, joined = merged_rows(observations, window)
    duplicate_times = [round(r["timestamp"], 2) for r in rows
                       if sum(1 for o in rows if abs(o["timestamp"] - r["timestamp"]) < 0.005) > 1]
    if duplicate_times:
        raise SystemExit("merged timeline has two rows at the same instant, so the tracker would read "
                         "the dots as moving: {}".format(sorted(set(duplicate_times))))
    result = boundary_games.track_games(boundary_games.build_observations(rows),
                                        rounds_per_game=args.rounds_per_game, games_per_set=3)
    result["observations"] = rows and [
        {"timestamp": r["timestamp"], "win_dots": r.get("win_dots"),
         "pip_row_drawn": r.get("pip_row_drawn", True)}
        for r in rows if r.get("win_dots") or r.get("pip_row_drawn") is not None]
    expectations = new.get("game_expectation") or new["game_one_expectation"]
    verdict = check_acceptance(result, expectations, observations, new["frames"])
    report = {"kind": "game-validation", "game": new["purpose"][:120],
              "window": list(window),
              "fixture_annotation_version": fixture["version"],
              "new_annotation_version": new["version"],
              "crop_readings_available": len(observations),
              "fixture_frames_reused": len(fixture["frames"]),
              "new_frames_requested": len(new["frames"]), "new_frames_added_to_timeline": joined["added"],
              "pilot_rows_without_a_frame_path": joined["unjoinable_pilot_rows"],
              "rows_in_window": len(rows), "format": {"rounds_per_game": args.rounds_per_game,
                                                      "games_per_set": 3,
                                                      "source": "configured, not inferred"},
              "rounds": result.get("rounds"), **verdict}
    out = Path(args.out) / "validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    summary = {k: v["pass"] for k, v in report["checks"].items()}
    print(json.dumps({"accepted": report["accepted"], "checks": summary,
                      "crop_readings_available": report["crop_readings_available"],
                      "new_frames_not_yet_read": len(
                          report["missing_evidence"]["new_frames_not_yet_read"]),
                      "progression_at_each_round_end":
                          report["checks"]["progression"]["observed_per_round_end"],
                      "winner_observed": report["checks"]["winning_side"]["observed"],
                      "boundaries_observed": report["checks"]["boundaries"]["observed"],
                      "report": str(out)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
