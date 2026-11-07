#!/usr/bin/env python3
"""Pre-flight for the win-row reset probe, offline: reviewed labels through the real merge and tracker.

The probe asks six frames what the win row read across two breaks. Before any of its allowance is spent,
this rehearses the answer: the evaluator-only labels are joined by
:func:`tekken_vod_helper.boundary_evidence.merge_crop_readings` -- the one merge the runtime uses -- onto
the same clock rows the published audit used, and grouped by :func:`boundary_games.track_games`. Only the
transport is replaced; nothing is invented and nothing is sent.

Two things are proved here and neither is about model accuracy. The grouping rule consumes the shape of
these answers and splits the runs it is designed to split (the 2026-11-04 coupling, where a dip the
temporal rules already resolved can no longer be read as a reset), and the answers stay in memory: the
discovered crop corpus this script reports is the corpus it found when it started, byte for byte.

A clean rehearsal is arithmetic, not evidence. The labels grade themselves; what the probe buys is a model
reading nobody has seen yet.

    .venv/bin/python scripts/rehearse_reset_probe.py \\
        --labels build/work/bb9/game-reset-probe-v1/labels-codex-reviewed-2026-11-04.json \\
        --annotations build/work/bb9/game-reset-probe-v1/annotations.json \\
        --prepared build/work/bb9/game-reset-probe-v1/run/prepared.json \\
        --probe-run build/work/bb9/game-reset-probe-v1/run \\
        --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \\
        --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \\
        --duration 6928.421136 \\
        --out build/work/bb9/game-reset-probe-v1/label-rehearsal-v1.json
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be          # noqa: E402
from tekken_vod_helper import boundary_games as bg             # noqa: E402

SIDES = ("left", "right")


def label_readings(labels, annotations):
    # type: (dict, dict) -> dict
    """The label file as the crop loader would have returned it, keyed by source frame path.

    Shape comes from :func:`boundary_evidence.crop_observations`: a count the inspector could not read
    stays ``None`` rather than becoming a zero, presence travels per side, and the row-level flag says
    whether the scoreboard was drawn at all.
    """
    roles = {entry["frame"]: entry for entry in annotations.get("frames") or []}
    by_name = {Path(entry["frame"]).name: entry for entry in annotations.get("frames") or []}
    readings = {}
    for label in labels.get("labels") or []:
        frame = by_name.get(label["frame"])
        if frame is None:
            raise SystemExit("label for %s has no annotated frame: the probe would never ask it"
                             % label["frame"])
        drawn = label.get("row_drawn") or {}
        readings[frame["frame"]] = {
            "timestamp": frame["timestamp"],
            "win_dots": [label.get(side) for side in SIDES],
            "win_dots_drawn": [drawn.get(side) for side in SIDES],
            # No confidence is invented: the reviewer recorded what the pixels showed, not how surely a
            # model would claim it. An unknown confidence can only *widen* what the temporal rules set
            # aside, so the rehearsal is the permissive case, and the honest one.
            "win_dots_confidence": [None, None],
            "pip_row_drawn": all(bool(drawn.get(side)) for side in SIDES),
            "source": "evaluator_only_rehearsal",
            "from_group": "labels:%s" % label["frame"]}
    for name, entry in roles.items():
        if not any(Path(label["frame"]).name == Path(name).name for label in labels.get("labels") or []):
            raise SystemExit("annotated frame %s has no reviewed label" % name)
    return readings


def track(rows, manifest, duration):
    # type: (list, str, float) -> dict
    return bg.track_games(bg.build_observations(rows), games_per_set=None,
                          source=bg.source_bounds(manifest=manifest, duration=duration))


def summary(tracked):
    # type: (dict) -> dict
    return {"games": len(tracked["games"]),
            "rounds": len(tracked["rounds"]),
            "games_with_a_winner": sum(1 for g in tracked["games"] if g.get("winner")),
            "games_without_a_winner": sum(1 for g in tracked["games"] if not g.get("winner")),
            "contested_games": sum(1 for g in tracked["games"] if g.get("contested"))}


def games_over(tracked, lo, hi):
    # type: (dict, float, float) -> list
    return [game for game in tracked["games"]
            if float(game["start"]) < hi and float(game["end"] or game["start"]) > lo]


def span_report(baseline, rehearsal, span):
    # type: (dict, dict, str) -> dict
    lo, hi = (float(part) for part in span.split("-"))
    out = {"span": span}
    for kind, tracked in (("saved_evidence", baseline), ("with_probe_labels_joined", rehearsal)):
        rows = []
        for game in games_over(tracked, lo, hi):
            rows.append({"start": round(float(game["start"]), 3),
                         "end": round(float(game["end"]), 3) if game.get("end") is not None else None,
                         "rounds": game.get("rounds"),
                         "status": game.get("status"),
                         "winner": game.get("winner"),
                         "contested": bool(game.get("contested")),
                         "contested_reasons": list(game.get("contested_reasons") or []),
                         "boundary_evidence": [{"type": note.get("type"),
                                                "verdict": note.get("verdict"),
                                                "at": note.get("at"),
                                                "confirms": note.get("confirms")}
                                               for note in game.get("boundary_evidence") or []]})
        out[kind] = {"games": len(rows), "detail": rows}
    return out


def digest(path):
    # type: (Path) -> str
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preparation_check(prepared_path, probe_run):
    # type: (Path, Path) -> dict
    """What the probe directory holds, read as the guard it is: prepared means prepared, not sent."""
    prepared = json.loads(Path(prepared_path).read_text(encoding="utf-8"))
    run = Path(probe_run)
    answers = {"groups": sorted(p.name for p in (run / "crops" / "groups").glob("*"))
               if (run / "crops" / "groups").is_dir() else [],
               "batches": sorted(p.name for p in (run / "crops" / "batches").glob("*"))
               if (run / "crops" / "batches").is_dir() else [],
               "ledger": sorted(str(p.relative_to(run)) for p in run.glob("*attempts*.json"))}
    crops = sorted(p.name for p in (run / "crops").glob("*.png")) if (run / "crops").is_dir() else []
    return {"model": prepared.get("model"), "endpoint": prepared.get("endpoint"),
            "attempt_allowance": prepared.get("attempt_allowance"),
            "frames": len(prepared.get("frames") or []),
            "rendered_crops": len(crops),
            "labels_are_evaluator_only": all((entry.get("expected") is None)
                                             for entry in prepared.get("frames") or []),
            "answers_on_disk": answers,
            "nothing_sent": not any(answers.values()),
            "prepared_file": str(prepared_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--probe-run", required=True)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out_path = Path(args.out)
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    annotations = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    if not labels.get("never_feed_to_runtime"):
        raise SystemExit("the label file does not declare never_feed_to_runtime; refusing to rehearse "
                         "answers a runtime path may read")

    # The corpus before, so "nothing leaked" is a measurement and not a promise.
    runs_before = [str(p) for p in be.crop_runs()]
    saved_readings = be.crop_observations(runs_before)
    evidence = be.evidence(None, facts=args.timeline)
    baseline = track(evidence["row_records"], args.frame_manifest, args.duration)

    # Rehearse against the base the executed probe would actually be read back into: the discovered crop
    # corpus plus these six answers. Joining the labels onto state alone would report every winner in the
    # recording disappearing, which is a property of the omitted input and not of the rule under test.
    readings = dict(saved_readings)
    labels_only = label_readings(labels, annotations)
    readings.update(labels_only)

    merged, merge_stats = be.merge_crop_readings(be.read_facts(args.timeline), readings,
                                                 (0.0, float("inf")))
    graded, demoted = be.grade_rows(merged)
    rehearsal = track(graded, args.frame_manifest, args.duration)

    spans = sorted({str(label.get("game")) for label in labels.get("labels") or []
                    if label.get("game")})
    unread_notes = [{"game": [round(float(game["start"]), 3), round(float(game["end"]), 3)],
                     "note_at": note.get("at"),
                     "readings_needed": note.get("readings_needed")}
                    for game in baseline["games"]
                    for note in game.get("boundary_evidence") or []
                    if note.get("type") == "cap_boundary_has_no_win_row_read"]

    unknown_kept_unknown = []
    for label in labels.get("labels") or []:
        for side, key in (("p1", "left"), ("p2", "right")):
            if label.get(key) is not None:
                continue
            stamp = next(frame["timestamp"] for frame in annotations["frames"]
                         if Path(frame["frame"]).name == Path(label["frame"]).name)
            entered = [entry for rnd in rehearsal["rounds"]
                       for entry in (rnd.get("win_dots_evidence") or {}).get(side) or []
                       if abs((entry.get("at") or 0.0) - stamp) < 0.01]
            unknown_kept_unknown.append({"frame": label["frame"], "side": side, "at": stamp,
                                         "expected_readings": 0,
                                         "readings_that_entered_the_series": len(entered)})

    runs_after = [str(p) for p in be.crop_runs()]
    report_frame_digests = [{"frame": label["frame"],
                             "matches_cached_frame": digest(
                                 ROOT / "build/work/bb9/detect-1440p-v1/frames" / label["frame"])
                             == label.get("frame_sha256")}
                            for label in labels.get("labels") or []]
    report = {
        "kind": "game-reset-probe-label-rehearsal",
        "inference_sent": False,
        "what_this_is": "the evaluator-only labels fed through the canonical merge and tracker with only "
                        "the transport replaced. A clean score here is arithmetic about the grouping "
                        "rule, not a measurement of what the model will read.",
        "inputs": {"labels": str(args.labels), "annotations": str(args.annotations),
                   "timeline": args.timeline, "frame_manifest": args.frame_manifest,
                   "duration": args.duration, "label_independent_inspector":
                       labels.get("independent_inspector"),
                   "inspection_status": labels.get("inspection_status"),
                   "crop_runs_discovered": len(runs_before),
                   "crop_readings_in_saved_base": evidence["crop_readings"]},
        "saved_evidence": summary(baseline),
        "with_probe_labels_joined": summary(rehearsal),
        "merge": {"rehearsal_labels_injected": len(labels_only),
                  "readings_in_joined_base": len(readings),
                  "rows_added": merge_stats["added"],
                  "skipped_outside_window": merge_stats["skipped_outside_window"],
                  "pip_readings_demoted": demoted},
        "count_unknown_stayed_unknown": unknown_kept_unknown,
        "spans_still_flagged_unread_in_saved_evidence": unread_notes,
        "spans": [span_report(baseline, rehearsal, span) for span in spans],
        "readiness": {
            "labels_independently_inspected": bool(labels.get("independent_inspector")),
            "every_annotated_frame_has_a_label": len(labels_only) == len(annotations.get("frames") or []),
            "every_frame_digest_matches": all(entry["matches_cached_frame"]
                                              for entry in report_frame_digests),
            "nothing_sent_yet": preparation_check(Path(args.prepared), Path(args.probe_run))[
                "nothing_sent"],
            "spans_the_probe_asks_about": spans,
            "note": "ready means prepared and consistent, not authorised: the labels are single-source "
                    "truth for this rehearsal only, and the probe stays held until its owner says send"},
        "probe_preparation": preparation_check(Path(args.prepared), Path(args.probe_run)),
        "frame_digests": report_frame_digests,
        "corpus_unchanged": {"runs_before": runs_before, "runs_after": runs_after,
                             "identical": runs_before == runs_after,
                             "rehearsal_wrote_no_crop_answers": not any(
                                 str(out_path.parent) in run for run in runs_after)},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    print("\nwrote %s" % out_path)


if __name__ == "__main__":
    main()
