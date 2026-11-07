#!/usr/bin/env python3
"""What a paid reading actually changed, measured twice and never with the same instrument.

Two questions get two answers here, because the first version of this tool blurred them:

**1. Count withholding** (`withhold_counts`) -- blank one side's pip *number* on a frame, everywhere it is
carried, and re-run the recording. This asks "did that digit matter?" Trials are classified five ways and
the classification is the point:

  count_drops_landed            a count existed, was removed by identity, and the base got smaller
  count_drops_not_applicable    that side carried no count, so there was nothing to remove -- not a failure
  count_drops_failed            a count existed and the removal did not land -- a broken experiment
  experiments_changing_support  a winner stayed and its number of supporting looks did not
  experiments_changing_winner_identity   the credited side itself moved

**2. Full-observation removal** (`remove_observations`) -- delete the picture's *answer*: the crop entry
goes away as an entry, not as a number, and the row keeps its clock and state facts while losing every
pip field that answer stamped. This asks "did that observation matter?", which is the only fair question
about an answer that carried no count at all. A blanked number leaves the observation, its
``pip_row_drawn`` and its confidence in place, so it cannot answer that question -- and reporting it as if
it did is how "inert" got manufactured from an instrument that removed nothing.

Both comparisons walk the **union** of before and after keys. Iterating only the after side lets a game
that vanished under a deletion -- or regrouped into a different span -- disappear from the report entirely,
which is the worst possible failure for a consequence report: the biggest effect becomes invisible.

    env PYTHONPATH=.qwen/tmp/pylibs:.venv/lib/python3.14/site-packages:. /usr/bin/python3 \\
        scripts/audit_reading_consequence.py \\
        --annotations build/work/bb9/winner-corroboration-v2/annotations.json \\
        --out build/work/bb9/winner-corroboration-v2-consequence-normalized-v2.json \\
        --ablation-out build/work/bb9/winner-corroboration-v2-full-answer-ablation-v2.json

Offline: merge, tracker and planner only. Nothing is sent, and no raw artifact is rewritten.
"""
import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be        # noqa: E402
from tekken_vod_helper import boundary_games as bg           # noqa: E402
from tekken_vod_helper import boundary_header as bh          # noqa: E402

TIMELINE = "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DURATION = 6928.421136
BAND_RUNS = ["build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30",
             "build/work/bb9/set-end2end-v1/attribution-run",
             "build/work/bb9/set-end2end-v1/band-run",
             "build/work/bb9/unseen-set-structure-v1/run-attempt/header"]
BAND_LEDGER = "build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json"
SIDES = ("p1", "p2")
#: The pip fields a crop answer stamps onto a row. Clock and state facts are deliberately absent: removing
#: a pip observation must not silently remove the round it happened to be attached to.
PIP_ROW_FIELDS = ("win_dots", "win_dots_drawn", "win_dots_confidence", "pip_row_drawn",
                  "pip_source", "_pip_source", "win_dots_evidence")


def base():
    header = bh.saved_readings(bh.response_directories(",".join(BAND_RUNS)), BAND_LEDGER)
    return dict(be.crop_observations(be.crop_runs())), be.read_facts(TIMELINE), header


def track(readings, rows, header):
    merged, _ = be.merge_crop_readings(rows, readings, (0.0, float("inf")))
    graded, _ = be.grade_rows(merged)
    return bg.track_games(bg.build_observations(graded), games_per_set=None, header=header,
                          source=bg.source_bounds(manifest=MANIFEST, duration=DURATION))


def side_readings(readings, rows):
    merged, _ = be.merge_crop_readings(rows, readings, (0.0, float("inf")))
    return sum(1 for row in merged for value in (row.get("win_dots") or []) if value is not None)


def identity_of(entry):
    return bg._frame_identity(entry["frame"])


def _matches(key, entry, identity):
    """One picture, whichever spelling of its path a record happens to use."""
    return (bg._frame_identity(key) == identity
            or bg._frame_identity(entry.get("frame") or entry.get("path")) == identity)


def withhold_counts(readings, rows, identity, sides):
    """Blank one side's pip number on a picture, in the crop answer and in the row alike."""
    readings, rows = copy.deepcopy(readings), copy.deepcopy(rows)
    removed = 0
    for key, entry in readings.items():
        if not _matches(key, entry, identity):
            continue
        for side in sides:
            slot = 0 if side == "p1" else 1
            values = list(entry.get("win_dots") or [None, None])
            if len(values) > slot and values[slot] is not None:
                values[slot] = None
                entry["win_dots"] = values
                removed += 1
            drawn = list(entry.get("win_dots_drawn") or [None, None])
            if len(drawn) > slot:
                drawn[slot] = None
                entry["win_dots_drawn"] = drawn
    for row in rows:
        if bg._frame_identity(row.get("frame") or row.get("path")) != identity:
            continue
        for side in sides:
            slot = 0 if side == "p1" else 1
            values = list(row.get("win_dots") or [None, None])
            if len(values) > slot and values[slot] is not None:
                values[slot] = None
                row["win_dots"] = values
                removed += 1
    return readings, rows, removed


def remove_observations(readings, rows, identity):
    """Delete a picture's answer entirely: the entry is gone, and the row loses its pip fields only.

    Returns how many crop entries disappeared, how many rows lost pip fields, and whether the identity is
    still findable anywhere. An ablation that cannot say "the entry is gone" has not removed an answer.
    """
    readings, rows = copy.deepcopy(readings), copy.deepcopy(rows)
    gone = [key for key, entry in readings.items() if _matches(key, entry, identity)]
    for key in gone:
        del readings[key]
    cleared = 0
    for row in rows:
        if bg._frame_identity(row.get("frame") or row.get("path")) != identity:
            continue
        if any(field in row and row.get(field) not in (None, [], [None, None], [False, False])
               for field in PIP_ROW_FIELDS):
            cleared += 1
        for field in PIP_ROW_FIELDS:
            row.pop(field, None)
    still = any(_matches(key, entry, identity) for key, entry in readings.items())
    return readings, rows, {"crop_entries_removed": len(gone), "rows_cleared_of_pip_fields": cleared,
                            "identity_still_in_crop_entries": still}


def fingerprint(tracked):
    """Every published claim a deletion could move, keyed so additions *and* removals are visible."""
    games = {("%.3f" % float(g["start"])): {
        "winner": g.get("winner"), "basis": g.get("winner_basis"),
        "support": (g.get("winner_support") or {}).get("frame_count"),
        "status": g.get("status"), "clip_end_at": g.get("clip_end_at"),
        "end_uncertainty": json.dumps(g.get("end_uncertainty"), sort_keys=True, default=str),
        "conclusion_event_at": g.get("conclusion_event_at")} for g in tracked["games"]}
    sets = {("%.3f" % float(s["start"])): {
        "game_wins": json.dumps(s.get("game_wins"), sort_keys=True),
        "set_winner": s.get("set_winner"), "status": s.get("status"),
        "clip_end_at": s.get("clip_end_at"),
        "unresolved": json.dumps(s.get("unresolved_claims"), sort_keys=True, default=str)}
        for s in (tracked.get("sets") or [])}
    return {"rounds": len(tracked["rounds"]), "games": games, "sets": sets,
            "totals": {"games": len(tracked["games"]),
                       "winners": sum(1 for g in tracked["games"] if g.get("winner"))},
            "queue": dict(Counter(item["type"] for item in tracked["unresolved"]))}


def diff(before, after):
    """Compare the union of keys: a game that disappears is the loudest possible finding."""
    out = {"games_added": [], "games_removed": [], "winner_claims_lost": [],
           "winner_claims_gained": [], "winner_identity_changes": {}, "support_changes": {},
           "clip_end_changes": {}, "uncertainty_changes": {}, "status_changes": {},
           "sets_added": [], "sets_removed": [], "set_wins_changes": {},
           "set_winner_changes": {}, "set_clip_changes": {},
           "rounds_changed": [], "queue_changes": {}}
    if before["rounds"] != after["rounds"]:
        out["rounds_changed"] = [before["rounds"], after["rounds"]]
    for kind in sorted(set(before["queue"]) | set(after["queue"])):
        if before["queue"].get(kind) != after["queue"].get(kind):
            out["queue_changes"][kind] = [before["queue"].get(kind), after["queue"].get(kind)]
    for key in sorted(set(before["games"]) | set(after["games"])):
        was, now = before["games"].get(key), after["games"].get(key)
        if was is None:
            out["games_added"].append({"start": key, "winner": now["winner"]})
            continue
        if now is None:
            out["games_removed"].append({"start": key, "winner": was["winner"]})
            if was["winner"]:
                out["winner_claims_lost"].append({"start": key, "winner": was["winner"]})
            continue
        if was["winner"] != now["winner"]:
            # A winner going from credited to none is a *lost claim*; a winner moving p1 -> p2 is a flip.
            # They read the same in one column and mean opposite things, so they do not share one.
            if was["winner"] and now["winner"]:
                out["winner_identity_changes"][key] = [was["winner"], now["winner"]]
            elif was["winner"]:
                out["winner_claims_lost"].append({"start": key, "winner": was["winner"]})
            else:
                out["winner_claims_gained"].append({"start": key, "winner": now["winner"]})
        if was["support"] != now["support"] and was["winner"] == now["winner"] and now["winner"]:
            out["support_changes"][key] = ["%s/%s" % (was["winner"], was["support"]),
                                           "%s/%s" % (now["winner"], now["support"])]
        for field, bucket in (("clip_end_at", "clip_end_changes"),
                              ("end_uncertainty", "uncertainty_changes"),
                              ("status", "status_changes"),
                              ("conclusion_event_at", "clip_end_changes")):
            if was[field] != now[field]:
                out[bucket].setdefault(key, [was[field], now[field]])
    for key in sorted(set(before["sets"]) | set(after["sets"])):
        was, now = before["sets"].get(key), after["sets"].get(key)
        if was is None:
            out["sets_added"].append(key)
            continue
        if now is None:
            out["sets_removed"].append(key)
            continue
        if was["game_wins"] != now["game_wins"]:
            out["set_wins_changes"][key] = [was["game_wins"], now["game_wins"]]
        if was["set_winner"] != now["set_winner"]:
            out["set_winner_changes"][key] = [was["set_winner"], now["set_winner"]]
        if was["clip_end_at"] != now["clip_end_at"]:
            out["set_clip_changes"][key] = [was["clip_end_at"], now["clip_end_at"]]
    return out


def changed(diff_report):
    return sorted({name for name, value in diff_report.items() if value})


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ablation-out", default=None)
    args = parser.parse_args()

    annotations = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    readings, rows, header = base()
    before_count = side_readings(readings, rows)
    full = fingerprint(track(readings, rows, header))

    experiments, ablations = [], []
    for entry in annotations["frames"]:
        identity = identity_of(entry)
        for mode, sides in (("p1", ("p1",)), ("p2", ("p2",)), ("both", SIDES)):
            edited, edited_rows, removed = withhold_counts(readings, rows, identity, sides)
            after_count = side_readings(edited, edited_rows)
            delta = diff(full, fingerprint(track(edited, edited_rows, header)))
            had_count = removed > 0
            experiments.append({
                "frame": Path(entry["frame"]).name, "side": mode, "identity": identity,
                "counts_removed": removed, "side_readings_before": before_count,
                "side_readings_after": after_count,
                "count_drop_status": ("landed" if had_count and after_count < before_count else
                                      "not_applicable_no_count" if not had_count else "failed"),
                "published_claims_changed": changed(delta), "delta": delta})
        # A separate instrument for a separate question: does the observation itself carry anything?
        edited, edited_rows, proof = remove_observations(readings, rows, identity)
        delta = diff(full, fingerprint(track(edited, edited_rows, header)))
        ablations.append({"frame": Path(entry["frame"]).name, "identity": identity, **proof,
                          "observation_really_removed":
                              proof["crop_entries_removed"] > 0 and not
                              proof["identity_still_in_crop_entries"],
                          "side_readings_before": before_count,
                          "side_readings_after": side_readings(edited, edited_rows),
                          "published_claims_changed": changed(delta), "delta": delta})

    tally = Counter(experiment["count_drop_status"] for experiment in experiments)
    report = {
        "kind": "reading-consequence-normalized-v2", "inference_sent": False, "executed": False,
        "two_instruments": ("count withholding asks whether a digit mattered; full-observation removal "
                            "asks whether the answer did. A frame with no count cannot be called inert by "
                            "the first instrument, because the first instrument removes nothing from it."),
        "inputs": {"annotations": args.annotations, "frames": len(annotations["frames"]),
                   "side_readings_in_base": before_count, "games": full["totals"]["games"],
                   "winners": full["totals"]["winners"], "rounds": full["rounds"]},
        "experiments": experiments,
        "totals": {"experiments": len(experiments),
                   "count_drops_landed": tally["landed"],
                   "count_drops_not_applicable": tally["not_applicable_no_count"],
                   "count_drops_failed": tally["failed"],
                   "experiments_changing_support": sum(1 for e in experiments
                                                        if e["delta"]["support_changes"]),
                   "experiments_changing_winner_identity":
                       sum(1 for e in experiments if e["delta"]["winner_identity_changes"]),
                   "experiments_removing_a_game": sum(1 for e in experiments
                                                       if e["delta"]["games_removed"]
                                                       or e["delta"]["games_added"]),
                   "experiments_losing_a_winner_claim":
                       sum(1 for e in experiments if e["delta"]["winner_claims_lost"])},
        "full_answer_ablation": ablations,
        "ablation_totals": {
            "frames": len(ablations),
            "observations_really_removed": sum(1 for a in ablations if a["observation_really_removed"]),
            "ablations_changing_any_published_claim":
                sum(1 for a in ablations if a["published_claims_changed"]),
            "ablations_with_no_effect_at_all": sum(1 for a in ablations
                                                   if not a["published_claims_changed"])}}
    if args.ablation_out:
        Path(args.ablation_out).write_text(json.dumps(ablations, indent=1, sort_keys=True) + "\n",
                                           encoding="utf-8")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "ablation_totals": report["ablation_totals"],
                      "inputs": report["inputs"],
                      "frames_whose_ablation_moved_a_claim":
                          [a["frame"] for a in ablations if a["published_claims_changed"]]},
                     indent=1, sort_keys=True))
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
