#!/usr/bin/env python3
"""Replay the three BB9 games whose readings are already on disk, under the corrected pip rule.

No model is called and no frame is fetched: every reading below comes from a saved crop reply. This
exists because the temporal correction changes how a falling win-dot count is adjudicated, and the two
accepted games must keep naming the same winner while the fixed-interval game must stop being resolved
by a rule that reaches across a whole game for support.

    boundaries    each game's span, round count and winner are re-derived from the canonical merge in
                  ``validate_game_one`` -- not from an ad-hoc reconstruction of the timeline
    no regression  a side whose count fell and stayed fallen is still contested, and its raw readings
                  are still on the round that produced them
    provenance    a reading set aside as an isolated misread says which local readings it yielded to,
                  and stays in ``win_dots_evidence`` so the call can be reversed by anyone who looks

Games 1 and 2 are compared against their saved ``validation.json``. Game 3 (the fixed interval
1006-1206 s) has no accepted outcome to compare with -- the interval was chosen without reference to
winners -- so its record is reported, and the evaluator's per-frame labels are quoted only as a
disclosed post-results observation, never as an input.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/revalidate_temporal_rule.py
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tekken_vod_helper import boundary_games  # noqa: E402
import validate_game_one as vg  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/work/bb9"

GAMES = [
    {"name": "game1", "label": "Nina vs Reina pool winner, four rounds, RIGHT took it",
     "window": (44.0, 262.0),
     "run_dirs": [WORK / "pip-crops-v2", WORK / "game1-validate-v1" / "run"],
     "baseline": WORK / "game1-validate-v1" / "validation.json"},
    {"name": "game2", "label": "independent match, five rounds, 3-2 decider, RIGHT took it",
     "window": (736.0, 1080.0),
     "run_dirs": [WORK / "game2-validate-v1" / "run", WORK / "game2-validate-v1" / "run2"],
     "baseline": WORK / "game2-validate-v1" / "validation.json"},
    {"name": "game3-interval",
     "label": "the fixed 200 s interval, chosen with no reference to openings, marks or winners",
     "window": (1006.0, 1206.0),
     "run_dirs": [WORK / "windows-interval-v2" / "run"],
     "baseline": None},
]

# The fields an accepted game is *known by*. If one of these moves, the historical result moved with it
# and the run has to be re-read; anything else is the record getting more honest about what it always
# meant. `end` is on the list precisely because the executed trial showed it could be a dwell.
CONCLUSION_FIELDS = ("start", "end", "rounds", "status", "winner", "winner_basis", "contested",
                     "contested_reasons", "counts_toward_set", "basis", "dot_regressions")

# The reading the interval replay sets aside, and the independent label for the same frame: the row is
# occluded by the K.O. close-up there, so it is ungraded rather than wrong.
OCCLUDED_FRAME_AT = 1131.129941


def replay(spec, rounds_per_game=3):
    # type: (Dict[str, Any], int) -> Dict[str, Any]
    """Run one game through the canonical merge and the tracker, exactly as the acceptance script does."""
    observations = vg.crop_observations(spec["run_dirs"])
    rows, joined = vg.merged_rows(observations, spec["window"])
    duplicated = [round(r["timestamp"], 2) for r in rows
                  if sum(1 for o in rows if abs(o["timestamp"] - r["timestamp"]) < 0.005) > 1]
    if duplicated:
        raise SystemExit("{}: two rows share an instant, so the tracker would read the dots as "
                         "moving: {}".format(spec["name"], sorted(set(duplicated))))
    result = boundary_games.track_games(boundary_games.build_observations(rows),
                                        rounds_per_game=rounds_per_game, games_per_set=3)
    built = boundary_games.build_observations(rows)
    return {"observations": len(observations), "rows": len(rows), "added_rows": joined["added"],
            # An evidence id is a position in this list, so it renames itself whenever a row is added
            # anywhere earlier. Kept so a provenance comparison can ask what an id *named* rather than
            # what it is called.
            "evidence_times": {o["id"]: round(float(o["timestamp"]), 6)
                               for o in built if o.get("id")},
            "result": result}


def baseline_evidence_times(saved):
    # type: (Dict[str, Any]) -> Dict[str, float]
    """What each id in an accepted report named, recovered from the rounds it was attached to."""
    times = {}
    for rnd in saved.get("rounds") or []:
        for key, stamp in (("start_evidence", rnd.get("start")),
                           ("end_evidence", rnd.get("round_end_at") or rnd.get("end"))):
            if stamp is None:
                continue
            for identifier in rnd.get(key) or []:
                times.setdefault(identifier, round(float(stamp), 6))
    return times


def game_record(game):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {k: game.get(k) for k in ("start", "end", "rounds", "status", "basis", "winner",
                                     "winner_basis", "contested", "contested_reasons",
                                     "dot_regressions", "counts_toward_set")}


def set_aside_readings(rounds):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    out = []
    for rnd in rounds:
        for reading in rnd.get("win_dots_rejected") or []:
            entry = dict(reading)
            entry["round_id"] = rnd.get("id")
            raw = [e for e in ((rnd.get("win_dots_evidence") or {}).get(reading["side"]) or [])
                   if e.get("at") == reading.get("at")]
            entry["raw_copy_survives"] = bool(raw)
            out.append(entry)
    return out


def stored(value):
    # type: (Any) -> Any
    """A record as its saved report holds it: the round trip is the comparison, not the objects."""
    return json.loads(json.dumps(value, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(WORK / "temporal-revalidation-v1"))
    parser.add_argument("--rounds-per-game", type=int, default=3,
                        help="Configured format. Never inferred from how many rounds were seen.")
    args = parser.parse_args()

    report = {"kind": "temporal-rule-revalidation", "inference_requests_sent": 0,
              "rounds_per_game": args.rounds_per_game, "games_per_set": 3,
              "blind": False,
              "disclosure": "these three games were inspected before this rule was written, and the "
                            "interval labels at frame %s were read before it was run again; nothing "
                            "here is a blind test" % OCCLUDED_FRAME_AT,
              "independent_crop_inspection": {
                  "at": OCCLUDED_FRAME_AT,
                  "seen": "the RIGHT crop at this frame holds the K.O. close-up across the pip row and "
                          "shows unlit slots, while the two crops bracketing it (1096.09 s and "
                          "1133.13 s) each show one lit slot and two unlit",
                  "role": "checked after the rule was written and recorded so a reviewer can judge the "
                          "reading that was set aside; the rule consumed none of this",
              },
              # Differences that are not results: renamed positions, added fields. Read the failures
              # first, but do not conclude that an empty failure list means nothing changed.
              "games": [], "failures": [], "disclosures": []}

    for spec in GAMES:
        replayed = replay(spec, args.rounds_per_game)
        result = replayed["result"]
        games = result["games"]
        entry = {"name": spec["name"], "label": spec["label"], "window": list(spec["window"]),
                 "crop_readings": replayed["observations"], "rows_in_window": replayed["rows"],
                 "rounds": len(result["rounds"]), "games_found": len(games),
                 "game": game_record(games[0]) if games else None,
                 "readings_set_aside": [
                     {k: r.get(k) for k in ("side", "value", "at", "confidence", "row_drawn",
                                            "frame", "round_id", "supported_by", "reason",
                                            "raw_copy_survives")}
                     for r in set_aside_readings(result["rounds"])],
                 "regressions": [game.get("dot_regressions") for game in games],
                 "queued": [u for u in result["unresolved"]
                            if u.get("type") in ("win_dots_regressed", "game_conclusion_contradicted")]}
        if spec["baseline"] and spec["baseline"].is_file():
            saved = json.loads(spec["baseline"].read_text(encoding="utf-8"))
            # The saved report went through ``json.dumps(default=str)``, so a tuple on this side is a
            # list on that one. Comparing the records as they are stored is the comparison that means
            # "unchanged"; comparing raw objects reported a difference that was only the round trip.
            #
            # Whole-record equality stopped being a useful check the moment a game record grew fields:
            # it reads False because the record is *bigger*, which hides the one question that matters.
            # So the shared keys are compared for value and the new ones are named separately -- an
            # added claim is a disclosure, a changed `end` is a result, and only one of those is a
            # reason to re-read an accepted game.
            old_record = saved["games"][0]
            new_record = stored(games[0]) if games else {}
            shared = sorted(set(old_record) & set(new_record))
            # ``evidence_ids`` is a list of *positions*, and the corpus of crop readings it indexes has
            # grown since these reports were frozen -- this window holds 144 rows where the accepted
            # report saw 124, so the same K.O. row is gobs-00070 there and gobs-00090 here. Resolving
            # each id to the instant it names is what separates a rename from a change of evidence, and
            # it is recorded to explain the difference -- not to make the check pass: the field stays
            # shared, so any remaining difference still fails the run.
            renumbered = None
            if "evidence_ids" in shared and old_record["evidence_ids"] != new_record["evidence_ids"]:
                old_times = baseline_evidence_times(saved)
                new_times = replayed["evidence_times"]
                resolved = [{"accepted_id": old, "now_id": new,
                             "named_at": old_times.get(old), "names_now": new_times.get(new),
                             "same_row": old_times.get(old) is not None
                                         and old_times.get(old) == new_times.get(new)}
                            for old, new in zip(old_record["evidence_ids"],
                                                new_record["evidence_ids"])]
                renumbered = resolved
            entry["baseline"] = {
                "path": str(spec["baseline"]),
                "accepted": saved.get("accepted"),
                "game_identical": bool(games) and all(old_record[k] == new_record[k] for k in shared),
                "conclusion_fields_changed": {
                    k: [old_record[k], new_record[k]] for k in shared
                    if k in CONCLUSION_FIELDS and old_record[k] != new_record[k]},
                "other_fields_changed": [k for k in shared
                                         if k not in CONCLUSION_FIELDS
                                         and old_record[k] != new_record[k]],
                "fields_added_since_baseline": sorted(set(new_record) - set(old_record)),
                "fields_dropped_since_baseline": sorted(set(old_record) - set(new_record)),
                # Named rather than dropped: the ids differ, the rows they point at do not.
                "evidence_ids_renumbered": renumbered,
                "unresolved_identical":
                    stored(result["unresolved"]) == saved.get("unresolved")}
        elif spec["baseline"]:
            entry["baseline"] = {"path": str(spec["baseline"]), "status": "missing, so no comparison "
                                                                         "is claimed"}
        else:
            entry["baseline"] = {"status": "no accepted outcome exists for this interval"}
        report["games"].append(entry)

    for entry in report["games"]:
        game = entry["game"] or {}
        baseline = entry.get("baseline") or {}
        if baseline.get("evidence_ids_renumbered"):
            rows = baseline["evidence_ids_renumbered"]
            renamed = [r for r in rows if r["same_row"] and r["accepted_id"] != r["now_id"]]
            repointed = [r for r in rows if not r["same_row"]]
            report["disclosures"].append(
                "{}: evidence_ids differ from the accepted baseline. {} name a different row than they "
                "did: {}. {} are the same row under a new position number.".format(
                    entry["name"], len(repointed),
                    "; ".join("{} ({}) -> {} ({})".format(
                        r["accepted_id"], r["named_at"], r["now_id"], r["names_now"])
                        for r in repointed) or "none",
                    len(renamed)))
        if game.get("contested") and game.get("winner") is not None:
            report["failures"].append("{}: a contested game still names a winner".format(entry["name"]))
        if game.get("winner") and not game.get("contested") and not game.get("counts_toward_set"):
            report["failures"].append("{}: an uncontested winner does not count toward a set".format(
                entry["name"]))
        for reading in entry["readings_set_aside"]:
            if not reading.get("raw_copy_survives"):
                report["failures"].append(
                    "{}: the reading set aside at {} is gone from win_dots_evidence".format(
                        entry["name"], reading.get("at")))
            if not reading.get("supported_by"):
                report["failures"].append(
                    "{}: a reading was set aside without naming its local support".format(entry["name"]))
        baseline = entry.get("baseline") or {}
        # Two different outcomes, and reporting them alike is how a real regression hides inside a
        # bookkeeping change. A conclusion field moving means the accepted result moved; a record simply
        # carrying more claims than it did is the disclosure this script asked for.
        moved = baseline.get("conclusion_fields_changed") or {}
        if moved:
            report["failures"].append(
                "{}: an accepted conclusion field changed -- {}".format(
                    entry["name"], "; ".join("{} {} -> {}".format(k, json.dumps(v[0]), json.dumps(v[1]))
                                             for k, v in sorted(moved.items()))))
        elif baseline.get("game_identical") is False:
            report["failures"].append(
                "{}: a non-conclusion field changed against the accepted baseline ({})".format(
                    entry["name"], ", ".join(baseline.get("other_fields_changed") or [])))
        if baseline.get("fields_dropped_since_baseline"):
            report["failures"].append("{}: fields an accepted game carried are gone: {}".format(
                entry["name"], baseline["fields_dropped_since_baseline"]))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "revalidation.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str),
                                           encoding="utf-8")
    print(json.dumps({"revalidation": str(out / "revalidation.json"),
                      "inference_requests_sent": 0,
                      "failures": report["failures"],
                      # Printed beside the failures, not underneath them: a difference that is explained
                      # is still a difference, and a reader who only sees the exit code deserves to see
                      # what the explanation was.
                      "disclosures": report["disclosures"],
                      "games": [{k: entry.get(k) for k in ("name", "game", "baseline")}
                                for entry in report["games"]],
                      "readings_set_aside": [{e["name"]: e["readings_set_aside"]}
                                             for e in report["games"]
                                             if e["readings_set_aside"]]},
                     indent=2, sort_keys=True, default=str))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
