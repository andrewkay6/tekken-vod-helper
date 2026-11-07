#!/usr/bin/env python3
"""Score a header trial against its frozen labels -- and say plainly which claim that score supports.

Separated from ``scripts/run_header_trial.py`` on purpose. The run reads the band; this reads the run and
the labels. Nothing that sends a request ever sees a label, and nothing here can change an answer: if the
transcription is wrong, the finding is that the route mis-read the band, not that the batch should have
stopped.

The scope line matters more than the score. Six band readings test **extraction and the mapping route**:
whether the pipeline reads the pairing and the displayed tally, and whether a side swap comes back as a
mapping question rather than silently becoming a boundary. They do not test end-to-end set recovery,
because the interval holds no validated pip game results and its final tally is deliberately unlabelled --
and this file prints that even when every label matches, so a good number cannot quietly become a better
claim than the one that was made.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/evaluate_header_trial.py \
        --run build/work/bb9/header-trial-v1/run \
        --labels build/work/bb9/set-trial-v1/labels-eval-frozen.json \
        --output build/work/bb9/header-trial-v1/evaluation.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_names as names  # noqa: E402

#: A band answer is joined to a label by instant, and the join has to be tight. The cache is 1 Hz and the
#: labels were read off the same frames, so anything wider than a frame boundary would be scoring a
#: reading of a different moment.
TOLERANCE_SECONDS = 0.6

SIDES = ("left", "right")


def _same(one, other):
    # type: (str, str) -> bool
    left, right = names.normalise_name(one), names.normalise_name(other)
    return bool(left) and bool(right) and names.names_close(left, right)


def _side_outcome(read, expected_player, expected_games):
    # type: (dict, str, int) -> dict
    """One side of one label, graded into matched / mismatched / not_claimed.

    A declination is its own outcome and is never called wrong. Scoring "that numeral is drawn and I will
    not guess it" as an error would train the route back toward invented numbers, which is the exact
    failure this band reading exists to avoid; crediting it as a match would be worse. It is counted, and
    it shrinks the denominator the accuracy figure is allowed to quote.
    """
    state = read.get("games_state") or "unknown"
    got_player = read.get("player")
    return {
        "expected_player": expected_player, "read_player": got_player,
        "player_outcome": ("matched" if got_player and _same(got_player, expected_player)
                           else "mismatched" if got_player else "not_claimed"),
        "expected_games": expected_games, "read_games": read.get("games"), "games_state": state,
        "games_outcome": ("matched" if state == "read" and read.get("games") == expected_games
                          else "mismatched" if state == "read" else "not_claimed"),
        "confidence": read.get("confidence"),
    }


def evaluate(labels, report):
    # type: (dict, dict) -> dict
    """Join labels to answers by instant, grade the two fields a band shows, and check the swap claim."""
    expected_labels = [entry for entry in labels.get("labels") or () if entry.get("usable")]
    answers = list(report.get("pairings") or [])
    rows, spare = [], list(answers)
    for entry in expected_labels:
        want_at = float(entry["at"])
        pair = next((candidate for candidate in spare
                     if abs(float(candidate.get("at") or 0.0) - want_at) <= TOLERANCE_SECONDS), None)
        if pair is None:
            rows.append({"at": want_at, "ordinal": entry.get("ordinal"), "answered": False,
                         "outcome": "no_answer",
                         "because": "the run sent no request for this instant, or its answer failed "
                                    "validation"})
            continue
        spare.remove(pair)
        pairing = pair.get("pairing") or {}
        row = {"at": want_at, "ordinal": entry.get("ordinal"), "answered": True,
               "frame_timestamp": pair.get("frame_timestamp"),
               "question": pair.get("question"),
               # The digest of the crop that was actually sent, so a "matched" row can be traced to the
               # pixels it was judged from rather than to a timestamp that names a different picture.
               "band_image_sha256": pair.get("image_sha256"),
               "expected_label": entry.get("label_read"), "read_label": pairing.get("label"),
               "mapping_usable": pairing.get("mapping_usable"),
               "pips_unvalidated": pairing.get("pips_unvalidated")}
        for side in SIDES:
            row["sides_" + side] = _side_outcome(pairing.get(side) or {},
                                                 (entry.get("names") or {}).get(side),
                                                 (entry.get("games_won") or {}).get(side))
        outcomes = [row["sides_" + s][k] for s in SIDES
                    for k in ("player_outcome", "games_outcome")]
        row["matched"] = outcomes.count("matched")
        row["mismatched"] = outcomes.count("mismatched")
        row["not_claimed"] = outcomes.count("not_claimed")
        row["outcome"] = ("mismatched" if row["mismatched"] else
                          "partially_declined" if row["not_claimed"] else "matched")
        rows.append(row)

    # The mapping claim, checked rather than asserted: where the labels say the two players exchanged
    # sides between one instant and the next, do the readings show the same exchange? A route that reads
    # both names correctly every time and still cannot see the swap has not earned the membership evidence
    # it is being funded to collect.
    answered_rows = [row for row in rows if row.get("answered")]
    mapping = []
    for marked in expected_labels:
        if not marked.get("side_mapping_changed_from_previous_label"):
            continue
        later_at = float(marked["at"])
        later = next((row for row in answered_rows if _joined(row["at"], later_at, TOLERANCE_SECONDS)),
                     None)
        earlier = next((row for row in sorted(answered_rows, key=lambda r: r["at"], reverse=True)
                        if row["at"] < later_at and (later is None or row is not later)), None)
        if not earlier or not later:
            mapping.append({"between": [earlier["at"] if earlier else None, later_at],
                            "verdict": "ungradeable",
                            "because": "one side of the labelled swap was never answered"})
            continue
        before = [earlier["sides_left"]["read_player"], earlier["sides_right"]["read_player"]]
        after = [later["sides_left"]["read_player"], later["sides_right"]["read_player"]]
        if not all(before) or not all(after):
            verdict = "ungradeable"
        elif _same(before[0], after[1]) and _same(before[1], after[0]):
            verdict = "swap_read"
        elif _same(before[0], after[0]) and _same(before[1], after[1]):
            verdict = "swap_missed"
        else:
            verdict = "ungradeable"
        mapping.append({"between": [earlier["at"], later["at"]], "before": before, "after": after,
                        "verdict": verdict,
                        "why_it_matters": "a side swap is an identity-mapping question; reading it as a "
                                          "boundary, or missing it, breaks per-player win attribution"})
    return {
        "kind": "header-trial-evaluation",
        "labels_version": labels.get("version"),
        "run_version": report.get("version"),
        "model": report.get("model"),
        "run_status": (report.get("run") or {}).get("status"),
        "stopped_reason": (report.get("run") or {}).get("stopped_reason"),
        "instants_expected": len(expected_labels),
        "instants_answered": sum(1 for r in rows if r.get("answered")),
        "instants": {"matched": sum(1 for r in rows if r.get("outcome") == "matched"),
                     "mismatched": sum(1 for r in rows if r.get("outcome") == "mismatched"),
                     "partially_declined": sum(1 for r in rows
                                               if r.get("outcome") == "partially_declined"),
                     "no_answer": sum(1 for r in rows if not r.get("answered"))},
        "fields": {"matched": sum(r.get("matched", 0) for r in rows),
                   "mismatched": sum(r.get("mismatched", 0) for r in rows),
                   "not_claimed": sum(r.get("not_claimed", 0) for r in rows)},
        "swap_checks": mapping,
        "rows": rows,
        "answers_with_no_label": [p.get("at") for p in spare],
        # A partial run prints no rate: "3 of 4 matched" on a denominator nobody agreed to is how an
        # unfinished batch becomes a headline.
        "complete": all(r.get("answered") for r in rows),
    }


def _joined(one, two, gap=1.0):
    # type: (float, float, float) -> bool
    """Whether two timestamps name the same instant within the cache's own sampling interval."""
    return abs(float(one) - float(two)) <= gap


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="Directory written by scripts/run_header_trial.py.")
    parser.add_argument("--labels", required=True, help="Frozen evaluator-side labels.")
    parser.add_argument("--output", default=None, help="Where to write evaluation.json.")
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if not labels.get("frozen"):
        raise SystemExit("refusing to score against labels that are not frozen: a moving truth cannot be "
                         "a control, and this file is read after the answers exist")
    if labels.get("annotations_for"):
        raise SystemExit("{} declares itself a runtime annotation, not evaluator truth".format(
            args.labels))
    report_path = Path(args.run) / "report-header.json"
    if not report_path.is_file():
        raise SystemExit("no {} yet: run --execute first, or point --run at the run directory".format(
            report_path))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result = evaluate(labels, report)
    result["accuracy_is_reportable"] = bool(result["complete"])
    result["score"] = (None if not result["complete"] else
                       "{}/{} instants".format(result["instants"]["matched"],
                                               result["instants_expected"]))
    result["claims_this_supports"] = [
        "whether the header route transcribes the pairing and the displayed tally at the instants it was "
        "asked about",
        "whether a side swap comes back as a mapping question rather than a boundary, on the frames where "
        "the labels show one",
    ]
    result["claims_this_cannot_support"] = [
        "end-to-end set recovery: the interval holds no validated pip game results, so nothing here "
        "exercises the merge that would group games into a set",
        "the set's final tally: no inspected label covers (3786.8, 3934.9), and the label file declines "
        "to assert what it did not see",
        "that a band pip count measures a round win: header pip readings are unvalidated diagnostics and "
        "are kept out of the crop arm's evidence entirely",
        "that the broadcast was right: a matched reading agrees with what the overlay displayed, which is "
        "a displayed state, not a record of what happened between the players",
    ]
    result["routes_not_exercised"] = {
        "bracket_context": "no bracket was supplied to this trial, so every outcome here is recording-only",
        "evaluator_labels_in_runtime": "labels were read only by this script, after the answers were on "
                                       "disk",
        "validated_pip_evidence": "none exists in this interval; the six calls ask the band, not the row",
    }
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    if not result["complete"]:
        # Exits non-zero so a scripted gate cannot read an unfinished batch as a finished evaluation. The
        # numbers are still printed: what was answered is evidence, it is just not a rate.
        raise SystemExit(1)


if __name__ == "__main__":
    main()
