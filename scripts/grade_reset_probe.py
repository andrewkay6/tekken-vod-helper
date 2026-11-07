#!/usr/bin/env python3
"""Grade the executed win-row reset probe against the reviewed labels. Offline: nothing is sent.

The runtime asked six frames what the win row read and kept the truth out of every file it sent --
``annotations.json`` and ``prepared.json`` carry ``expected: null`` on all six. This is the pairing done
afterwards, through the same scorer the earlier crop experiments used
(:func:`tekken_vod_helper.boundary_pips.score_readings`), so the error classes stay comparable:
``false_zero``, ``extra_pips``, ``side_swap``, ``row_invented`` and the rest, never one blended rate.

Frame-4066's LEFT slot is **unknown truth** -- the K.O. flash blooms across the first two slots -- so it is
withheld from every denominator rather than scored. A model count there is neither credited nor penalised:
it is a third state, and pretending otherwise would turn an unlabelable pixel into an accuracy claim. Its
RIGHT side *was* labelled and is reported separately, outside the rate, because withholding the frame is
not the same as withholding the half of it that has truth.

    env PYTHONPATH=.qwen/tmp/pylibs:.venv/lib/python3.14/site-packages:. /usr/bin/python3 \\
        scripts/grade_reset_probe.py \\
        --run build/work/bb9/game-reset-probe-v1/run \\
        --annotations build/work/bb9/game-reset-probe-v1/annotations.json \\
        --labels build/work/bb9/game-reset-probe-v1/labels-codex-reviewed-2026-11-04.json \\
        --out build/work/bb9/game-reset-probe-v1/grade-v1.json

The same command grades any prepared crop batch, and ``--labels`` is the only place truth lives: for
``winner-corroboration-v2`` it is ``labels-codex-reviewed-2026-11-05.json`` -- the reviewed correction
that keeps ``frame-2465`` presence unknown (RIGHT partly drawn, LEFT unsettled) and grades ``frame-3545``
absent on both sides. A superseded freeze sitting in the same directory is refused outright, because a
grader that silently evaluates against the label the reviewer corrected reports confidence nobody earned.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_media as bm        # noqa: E402
from tekken_vod_helper import boundary_pips as bp        # noqa: E402

#: The reviewer recorded left/right; the scorer speaks LEFT/RIGHT, as the crops are labelled.
SIDE = {"left": "LEFT", "right": "RIGHT"}


def read_label_file(path):
    # type: (str) -> dict
    """One shape for the label files this pipeline has produced, with the no-runtime flag preserved.

    ``game-reset-probe-v1`` records ``labels: [{frame, left, right, row_drawn: {left, right}}]`` with
    ``never_feed_to_runtime: true``; the batch freezes record ``frames: [{frame, pips: {LEFT, RIGHT},
    row_drawn: true}]`` with ``labels_are_runtime_input: false``; and a Codex review of a freeze records
    the same batch shape plus ``row_drawn_by_side: {LEFT, RIGHT}`` where a side's presence is separately
    known, keeping ``row_drawn: null`` as unknown where the reviewer declined to call either way. Presence
    travels as ``True``/``False``/``None`` from here on: an unknown side is never graded and never shown
    as absent, because ``bool(null) == False`` is how an honest "could not read" becomes a fake "knows
    it is not there".
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("labels") is not None:
        entries, inspector, allowed = (raw["labels"], raw.get("independent_inspector"),
                                       bool(raw.get("never_feed_to_runtime")))
        normalise = lambda label, side: label.get(side)
        drawn = lambda label, side: bool((label.get("row_drawn") or {}).get(side))
        reason = lambda label: label.get("reason")
    else:
        entries = raw.get("frames") or []
        inspector = raw.get("inspector")
        allowed = raw.get("labels_are_runtime_input") is False
        normalise = lambda label, side: (label.get("pips") or {}).get(SIDE[side])

        def drawn(label, side):
            by = label.get("row_drawn_by_side")
            if by is not None:
                return by.get(side.upper(), by.get(side))
            return label.get("row_drawn")
        reason = lambda label: label.get("basis")
    if not allowed:
        raise SystemExit("%s does not declare its labels evaluator-only" % path)
    # A freeze that a reviewed file declares superseded is evidence, not an answer key: refuse it here so
    # a grading run cannot quietly evaluate against the label the reviewer corrected.
    name = Path(path).name
    for sibling in sorted(Path(path).parent.glob("labels-codex-reviewed*.json")):
        if sibling.name == name:
            continue
        try:
            note = (json.loads(sibling.read_text(encoding="utf-8"))
                    .get("supersedes_for_evaluation") or "")
        except (ValueError, OSError):
            continue
        if name in note:
            raise SystemExit("%s is superseded for evaluation by %s (%s): grade against the reviewed "
                             "file -- the freeze stays preserved as the original inspection record, "
                             "not as the answer key" % (path, sibling.name, note))
    return {"labels": [{"frame": Path(entry["frame"]).name, "left": normalise(entry, "left"),
                        "right": normalise(entry, "right"),
                        "row_drawn": {"left": drawn(entry, "left"), "right": drawn(entry, "right")},
                        "reason": reason(entry), "game": entry.get("game")}
                       for entry in entries],
            "inspector": inspector, "declared_evaluator_only": True, "path": path}


def graded_frames(labels, annotations):
    # type: (dict, dict) -> tuple
    """The label file as ``score_readings`` annotations, plus the frames held back from grading.

    A frame whose reviewer could not count one side becomes an *unknown truth* frame in full: the scorer
    puts those outside every rate, which is the only honest place for a pixel nobody can read. The sides it
    does carry are still reported, by ``side_report``, so nothing is quietly dropped.
    """
    by_name = {Path(entry["frame"]).name: entry for entry in annotations.get("frames") or []}
    frames, withheld = [], []
    for label in labels.get("labels") or []:
        frame = by_name.get(label["frame"])
        if frame is None:
            raise SystemExit("label for %s is not a frame the probe asked about" % label["frame"])
        unknown = [side for side in ("left", "right") if label.get(side) is None]
        entry = dict(frame)
        if unknown:
            entry["pips"] = {"LEFT": None, "RIGHT": None}
            entry["row_drawn"] = None
            withheld.append({"frame": label["frame"], "timestamp": frame["timestamp"],
                             "unknown_sides": unknown,
                             "reviewed": {side: label.get(side) for side in ("left", "right")},
                             "reviewed_row_drawn": label.get("row_drawn"),
                             "reviewed_reason": label.get("reason")})
        else:
            entry["pips"] = {SIDE[side]: label.get(side) for side in ("left", "right")}
            entry["row_drawn"] = all(bool((label.get("row_drawn") or {}).get(side))
                                     for side in ("left", "right"))
        frames.append(entry)
    return frames, withheld


def side_report(observations, labels, annotations):
    # type: (list, dict, dict) -> list
    """Every side of every frame, truth and reply together, marked graded or withheld.

    The rate comes from ``score_readings``; this is the audit trail beside it, including the sides the rate
    deliberately excludes. ``confidence`` is the model's own claim about the crop, never a probability, and
    it is the only per-side evidence of how the count was read.
    """
    by_key = {(o.get("timestamp"), o.get("label")): o for o in observations}
    names = {Path(entry["frame"]).name: entry for entry in annotations.get("frames") or []}
    rows = []
    for label in labels.get("labels") or []:
        frame = names[Path(label["frame"]).name]
        for side in ("left", "right"):
            observed = by_key.get((frame["timestamp"], SIDE[side])) or {}
            truth = label.get(side)
            rows.append({"frame": label["frame"], "timestamp": frame["timestamp"], "side": SIDE[side],
                         "label": truth, "label_drawn": (label.get("row_drawn") or {}).get(side),
                         "reported": observed.get("lit"), "reported_drawn": observed.get("drawn"),
                         "model_confidence": observed.get("confidence"),
                         "graded": truth is not None,
                         "agrees": (None if truth is None
                                    else observed.get("lit") == truth and observed.get("drawn") is True)})
    return rows


def truth_bearing_fields(doc):
    # type: (object) -> list
    """Every ``expected``/``pips`` field in a runtime document that carries a value.

    The guarantee the probe's design rests on is that the file the runtime reads holds no truth: the field
    is present and null. So this walks the real documents rather than asserting intention -- and it walks
    the recorded request payloads too, because a leaked label would show up in what was actually sent.
    """
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("expected", "pips") and value is not None:
                    found.append("%s.%s=%s" % (path, key, json.dumps(value)[:60]))
                walk(value, "%s.%s" % (path, key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, "%s[%d]" % (path, index))

    walk(doc, "")
    return found


def unknown_slot_sensitivity(withheld_timestamps, timeline, manifest, duration):
    # type: (set, str, str, float) -> dict
    """Regroup the recording with each named reading withheld in turn, through the canonical merge.

    Grading says what the model answered. This says what the answer *did*: every timestamp named here is
    dropped once on its own and once all together, so a claim that rests on a single frame is visible as a
    column that moves, and a claim two frames carry independently does not move at all. Every column comes
    from the same code path, and no column is a truth claim about a frame.

    A withheld side is not a penalised side: the count-unknown slot is dropped here precisely because it
    may not decide anything on its own, and the extra columns exist to show whether it does.
    """
    from tekken_vod_helper import boundary_evidence as be
    from tekken_vod_helper import boundary_games as bg

    stamps = sorted(withheld_timestamps)
    columns = [("as_answered", ())] + [("without_%s" % stamp, (stamp,)) for stamp in stamps]
    if len(stamps) > 1:
        columns.append(("without_all", tuple(stamps)))

    out = {}
    for label, drop in columns:
        readings = dict(be.crop_observations(be.crop_runs()))
        if drop:
            for entry in readings.values():
                if (entry.get("timestamp") in drop
                        and (entry.get("win_dots") or [None])[0] is not None):
                    entry["win_dots"] = [None] + list(entry["win_dots"][1:])
                    entry["win_dots_drawn"] = [None] + list((entry.get("win_dots_drawn") or [])[1:])
                    entry["withheld_for_this_column"] = True
        merged, _ = be.merge_crop_readings(be.read_facts(timeline), readings, (0.0, float("inf")))
        graded, _ = be.grade_rows(merged)
        tracked = bg.track_games(bg.build_observations(graded), games_per_set=None,
                                 source=bg.source_bounds(manifest=manifest, duration=duration))
        spans = []
        for game in tracked["games"]:
            if float(game["start"]) < 4400 and float(game["end"] or 0) > 3900:
                spans.append({"start": round(float(game["start"]), 3),
                              "end": round(float(game["end"]), 3), "rounds": game["rounds"],
                              "status": game["status"], "winner": game.get("winner"),
                              "winner_basis": game.get("winner_basis"),
                              "contested": bool(game.get("contested")),
                              "boundary_evidence": [note.get("type")
                                                    for note in game.get("boundary_evidence") or []]})
        queue = [item.get("type") for item in tracked.get("unresolved") or []]
        out[label] = {"games": len(tracked["games"]),
                      "games_with_a_winner": sum(1 for g in tracked["games"] if g.get("winner")),
                      "contested_games": sum(1 for g in tracked["games"] if g.get("contested")),
                      "rounds": len(tracked["rounds"]),
                      # The published columns the rest of the report derives from: if these agree between
                      # two columns, the prices, membership and clip claims below them agree too, because
                      # they are functions of this recovery and nothing else.
                      "candidates": [{"start": round(float(c["start"]), 3),
                                      "end": round(float(c["end"]), 3), "games": c.get("games"),
                                      "status": c.get("status"), "set_winner": c.get("set_winner"),
                                      "footage_complete": c.get("footage_complete")}
                                     for c in (tracked.get("sets") or [])],
                      "queue": {key: queue.count(key) for key in sorted(set(queue))},
                      # Counted per side, not per frame: a column that withheld a reading has to show one
                      # fewer, or the drop never happened and every number below it is a lie by omission.
                      "side_readings_joined": sum(1 for entry in readings.values()
                                                  for value in (entry.get("win_dots") or [])
                                                  if value is not None),
                      "inputs": {"timeline": timeline, "frame_manifest": manifest,
                                 "duration": duration, "withheld_timestamps": list(drop),
                                 "games_per_set": None},
                      "span_3958_4174": spans}
    named = [key for key in out if key != "as_answered"]
    out["every_column_matches_as_answered"] = {
        key: (out[key]["games"] == out["as_answered"]["games"]
              and out[key]["games_with_a_winner"] == out["as_answered"]["games_with_a_winner"]
              and out[key]["candidates"] == out["as_answered"]["candidates"]
              and out[key]["queue"] == out["as_answered"]["queue"])
        for key in named}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeline", default="build/work/bb9/games-pilot-game1/run1/"
                                              "timeline-with-facts.json",
                        help="Set to measure what the withheld slot decided; omit for grading alone.")
    parser.add_argument("--frame-manifest",
                        default="build/work/bb9/detect-1440p-v1/frames/sample-manifest.json")
    parser.add_argument("--duration", type=float, default=6928.421136)
    parser.add_argument("--no-sensitivity", action="store_true",
                        help="Skip the re-grouping and grade only.")
    parser.add_argument("--withhold", action="append", default=[], type=float,
                        help="Timestamp whose LEFT reading is withheld in the sensitivity column, in "
                             "addition to any count-unknown slot the reviewer recorded. This is how a "
                             "reading that has truth is still tested for being load-bearing.")
    args = parser.parse_args()

    run = Path(args.run)
    result = json.loads((run / "result-crops.json").read_text(encoding="utf-8"))
    annotations = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    labels = read_label_file(args.labels)
    ledger_path = run.parent / "attempts.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}

    frames, withheld = graded_frames(labels, annotations)
    scored = bp.score_readings(result.get("observations") or [], frames)
    rows = side_report(result.get("observations") or [], labels, annotations)
    # Two different questions, two figures. "What did the accepted answers cost to obtain" is a scoring
    # diagnostic; "what did this batch spend" includes every attempt that got a reply back, including the
    # one whose JSON could not be parsed and had to be asked again. The ledger is authoritative for
    # charges, so the two are published side by side and cross-checked rather than one standing in for
    # the other.
    usage = bm.summarize_usage(result.get("usage") or [])
    ledger_usage = bm.summarize_usage([{"tokens": entry.get("tokens") or {},
                                        "accepted": entry.get("state") == "answered"}
                                       for entry in ledger.get("entries") or []])

    def choose_spend():
        """Pick the source that actually accounts for the charges, and say why it was chosen.

        Equal record counts are not coverage: two sources can each hold six records and disagree, and an
        absent ledger must never win by presenting an empty total over a report that has numbers. So a
        candidate only wins if it has records, and preference goes to full charge coverage, then to
        per-field completeness, then to breadth.
        """
        charged = ledger.get("attempts_charged")
        report_records = usage["all_attempts"]["records"]
        ledger_records = ledger_usage["all_attempts"]["records"]
        candidates = (("run report", usage["all_attempts"], report_records),
                      ("ledger", ledger_usage["all_attempts"], ledger_records))
        usable = [c for c in candidates if c[2] > 0]
        if not usable:
            return ("none", None, "no source reported any attempt usage")
        covering = [c for c in usable if charged and c[2] == charged]
        if covering:
            chosen = covering[0]
            reason = "covers every charged attempt (%d of %s)" % (chosen[2], charged)
        else:
            best = max(usable, key=lambda c: (c[1]["complete"], c[2]))
            reason = ("no source covers all %s charges; using the broadest with complete fields "
                      "(%d records)" % (charged, best[2]))
            chosen = best
        return (chosen[0], chosen[1], reason)

    spend_source, spend, spend_reason = choose_spend()
    both_report_usage = (usage["all_attempts"]["records"] > 0
                         and ledger_usage["all_attempts"]["records"] > 0)
    coverage_equal = (usage["all_attempts"]["records"] == ledger_usage["all_attempts"]["records"])

    # The runtime must still be carrying no truth, after the fact and not just by intention.
    prepared = json.loads((run / "prepared.json").read_text(encoding="utf-8"))
    plan_path = run / "plan-crops.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
    unknown_at = {w["timestamp"] for w in withheld} | set(args.withhold)
    report = {
        "kind": "pip-crop-probe-grade",
        "probe": run.parent.name,
        "inference_sent": False,
        "inputs": {"run": str(run), "annotations": args.annotations, "labels": args.labels,
                   "label_inspector": labels["inspector"],
                   "labels_declared_evaluator_only": labels["declared_evaluator_only"],
                   "model": result.get("model"), "endpoint": prepared.get("endpoint"),
                   "arm": result.get("arm"), "crop_scale_as_sent": bp.CROP_SCALE,
                   "annotated_frames": len(annotations.get("frames") or []),
                   "withheld_for_sensitivity": sorted(unknown_at)},
        "labels_kept_out_of_runtime": {
            "documents_walked": [args.annotations, str(run / "prepared.json"), str(plan_path)]
                + sorted(str(p) for p in (run / "crops" / "groups").glob("*.json")),
            "truth_bearing_fields_found": (truth_bearing_fields(annotations)
                                           + truth_bearing_fields(prepared)
                                           + truth_bearing_fields(plan)
                                           + [field for group in sorted((run / "crops" / "groups")
                                                                         .glob("*.json"))
                                              for field in truth_bearing_fields(
                                                  json.loads(group.read_text(encoding="utf-8")))]),
            "annotations_expected_all_null": all(entry.get("expected") is None
                                                 for entry in annotations.get("frames") or []),
            "prepared_expected_all_null": all(entry.get("expected") is None
                                              for entry in prepared.get("frames") or []),
            "truth_files_read_by_this_script": [args.labels]},
        "spent": {"requests_sent": result.get("requests_sent"),
                  "groups_answered": result.get("groups_answered"),
                  "groups_failed": result.get("groups_failed"),
                  "attempts_charged": ledger.get("attempts_charged"),
                  "attempts_remaining": ledger.get("attempts_remaining"),
                  "cap": ledger.get("cap"), "minutes": ledger.get("minutes"),
                  "deadline_one_stamp": [ledger.get("created_at"), ledger.get("deadline_at")],
                  "uncertain_attempts": ledger.get("uncertain_attempts"),
                  # Published separately on purpose. ``tokens`` is kept with its old meaning -- the
                  # accepted replies only -- so any reader who already knows that figure is not silently
                  # handed a bigger number under the same name.
                  "tokens": {**usage["accepted_only"], "records": usage["accepted_replies"]},
                  "usage_all_attempts": usage["all_attempts"],
                  "usage_rejected_retried": usage["rejected_only"],
                  "usage_rejected_reply_count": usage["rejected_replies_retried"],
                  "usage_from_ledger": ledger_usage["all_attempts"],
                  # The number a reader wants when they ask "what did this cost". Chosen by coverage of the
                  # charges rather than by which file was looked at first: an absent ledger can never beat a
                  # report that has numbers, and equal record counts are not evidence of agreement.
                  "spend": spend,
                  "spend_source": spend_source,
                  "spend_source_reason": spend_reason,
                  "usage_provenance": {
                      "report_usage_records": usage["attempts_with_a_response"],
                      "ledger_present": bool(ledger),
                      "ledger_charged_attempts": ledger.get("attempts_charged"),
                      "ledger_entries": len((ledger or {}).get("entries") or []),
                      "report_covers_every_charged_attempt":
                          bool(ledger.get("attempts_charged")) and usage["attempts_with_a_response"]
                          == ledger.get("attempts_charged"),
                      # Comparing two totals that came from different numbers of records proves nothing, so
                      # agreement is only asserted when both sources reported usage at all.
                      "agrees_with_ledger": (usage["all_attempts"]["completion"]
                                             == ledger_usage["all_attempts"]["completion"]
                                             if both_report_usage else None),
                      "agreement_is_meaningful": both_report_usage,
                      "record_counts_equal": coverage_equal,
                      "record_count_equality_is_not_coverage": True,
                      "records_without_usage": (usage["all_attempts"]["records_without_usage"]
                                                + ledger_usage["all_attempts"]["records_without_usage"]),
                      "missing_usage_is_treated_as": "unknown, never zero",
                      "authoritative_for_spend": ("whatever source covers the charges; the ledger records "
                                                  "charges, the run report records replies"),
                      "note": ("a reply that arrived, was rejected and was retried is billed twice and "
                               "answered once; the accepted-only total is not the spend")},
                  "status": result.get("status"), "stopped_reason": result.get("stopped_reason")},
        "score": scored,
        "unknown_truth_withheld_from_grading": withheld,
        "sides": rows,
        "sides_outside_the_rate": {
            "with_reviewed_truth_including_the_withheld_frame": sum(1 for r in rows if r["graded"]),
            "inside_the_accuracy_rate": scored["sides_total"],
            "agreeing_inside_the_rate": sum(1 for r in rows
                                            if r["graded"] and r["agrees"]
                                            and r["timestamp"] not in {w["timestamp"] for w in withheld}),
            "agreeing_in_the_withheld_frame": sorted("%s/%s" % (Path(r["frame"]).name, r["side"])
                                                     for r in rows
                                                     if r["graded"] and r["agrees"]
                                                     and r["timestamp"] in {w["timestamp"]
                                                                            for w in withheld}),
            "note": "the withheld frame's labelled side is reported here, never inside the rate: excluding "
                    "a frame over one unreadable slot does not mean throwing away the slot that has truth"},
        "unknown_slot_sensitivity": (None if (args.no_sensitivity or not unknown_at)
                                     else unknown_slot_sensitivity(unknown_at, args.timeline,
                                                                   args.frame_manifest,
                                                                   args.duration)),
        "reading_of_this": "accuracy covers only frames whose every side has independent truth; any withheld "
                           "side or frame is listed and reported outside the rate, so a count nobody can "
                           "verify is neither credited nor penalised. unknown_slot_sensitivity regroups the "
                           "recording with the named LEFT readings dropped, which is what shows whether a "
                           "boundary or a winner rests on one reading rather than on the evidence around it.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"score": {k: scored[k] for k in
                                ("frames", "frames_correct", "scored_visible_count_frames",
                                 "scored_visible_count_correct", "unknown_truth_frames", "sides_exact",
                                 "sides_total", "error_kinds")},
                      "withheld": [{"frame": w["frame"], "unknown_sides": w["unknown_sides"],
                                    "reviewed": w["reviewed"],
                                    "model_reported": {r["side"]: r["reported"] for r in rows
                                                       if r["timestamp"] == w["timestamp"]}}
                                   for w in withheld],
                      "labels_leaked_into_runtime": report["labels_kept_out_of_runtime"][
                          "truth_bearing_fields_found"],
                      "sides_outside_the_rate": report["sides_outside_the_rate"],
                      "unknown_slot_sensitivity": report["unknown_slot_sensitivity"],
                      "spent": report["spent"]}, indent=1, sort_keys=True))
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
