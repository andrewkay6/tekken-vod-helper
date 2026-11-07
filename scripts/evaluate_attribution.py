#!/usr/bin/env python3
"""Score an attribution run: did the band get transcribed, and did the win reach the right person?

Two questions, deliberately not merged. Transcription accuracy says the model read the crop; attribution
accuracy says the pipeline credited the game win to the participant the crop names on the winning side. A
run can pass one and fail the other, and the pair is what makes the claim checkable -- a score that fuses
them would let a pipeline that invents winners from the same label file look like agreement.

The labels are evaluator-side truth (`attribution-labels-eval-frozen.json`, sha256 recorded in
`docs/set-recovery-stage5-plan.md`). Nothing in the runtime path sees them: the trial spec carries instants
and reasons, and this script reads the answers *after* they are on disk.

A disagreement between the band and the recovery is a result, reported with the span that would settle it.
It is not a failure of the run and never a reason to edit either side.

    env PYTHONPATH=. .venv/bin/python scripts/evaluate_attribution.py \
        --run build/work/bb9/set-end2end-v1/attribution-run \
        --labels build/work/bb9/set-end2end-v1/attribution-labels-eval-frozen.json \
        --region 1200 2200 --output build/work/bb9/set-end2end-v1/attribution-evaluation.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tekken_vod_helper import boundary_games as bg          # noqa: E402
from tekken_vod_helper import boundary_header as bh         # noqa: E402
from tekken_vod_helper import boundary_names as names       # noqa: E402

import pip_evidence                                          # noqa: E402

TOLERANCE_SECONDS = 0.6


def agree(one, other):
    # type: (str, str) -> bool
    left, right = names.normalise_name(one), names.normalise_name(other)
    return bool(left) and bool(right) and names.names_close(left, right)


def transcribe(labels, saved):
    # type: (dict, dict) -> list
    """Question one: for each labelled crop, what did the run say?"""
    readings = list(saved["readings"])
    rows = []
    for entry in [label for label in labels.get("labels") or () if label.get("usable")]:
        want_at = float(entry["at"])
        match = next((r for r in readings
                      if abs(float(r.get("at") or 0.0) - want_at) <= TOLERANCE_SECONDS), None)
        if match is None:
            readings_by_digest = {r.get("image_sha256"): r for r in readings}
            # Fall back to the crop digest: a timestamp can be rounded, the bytes cannot.
            match = readings_by_digest.get(entry["band_sha256"])
        if match is None:
            rows.append({"at": want_at, "frame_id": entry.get("frame_id"), "answered": False,
                         "outcome": "no_answer",
                         "because": "the run holds no answer for this crop"})
            continue
        pairing = match["pairing"]
        sides = {}
        for side, key in (("left", "left"), ("right", "right")):
            read, want = pairing[key], entry["names"][side]
            want_games = entry["games_won"][side]
            sides[side] = {
                "expected_player": want, "read_player": read.get("player"),
                "player_outcome": ("matched" if read.get("player") and agree(want, read["player"])
                                   else "mismatched" if read.get("player") else "not_claimed"),
                "expected_games": want_games, "read_games": read.get("games"),
                "games_state": read.get("games_state"),
                "games_outcome": ("matched" if read.get("games") == want_games
                                  else "mismatched" if read.get("games_state") == "read"
                                  else "not_claimed"),
                "confidence": read.get("confidence")}
        outcomes = [sides[s][k] for s in ("left", "right")
                    for k in ("player_outcome", "games_outcome")]
        rows.append({"at": want_at, "frame_id": entry.get("frame_id"), "answered": True,
                     "crop_sha256": match.get("image_sha256"),
                     "crop_matches_label": match.get("image_sha256") == entry["band_sha256"],
                     "usable_for_mapping": pairing.get("mapping_usable"),
                     "sides": sides,
                     "matched": outcomes.count("matched"),
                     "mismatched": outcomes.count("mismatched"),
                     "not_claimed": outcomes.count("not_claimed"),
                     "outcome": ("mismatched" if outcomes.count("mismatched")
                                 else "partially_declined" if outcomes.count("not_claimed")
                                 else "matched")})
    return rows


def attribute(labels, saved, region, runs=None, rounds_per_game=3, games_per_set=3):
    # type: (dict, dict, tuple, object, int, int) -> dict
    """Question two: does each validated game win land on the participant the crop names?

    The winners come from the crop arm through the canonical merge; the names come from the band. Neither
    is derived from the other, which is what makes a match worth anything -- and a mismatch is reported
    against the span that could settle it rather than resolved by preferring one source.
    """
    evidence = pip_evidence.evidence(region, runs=runs)
    tracked = bg.track_games(evidence["observations"], rounds_per_game=rounds_per_game,
                             games_per_set=games_per_set, header=saved)
    covers = bg._band_map(saved, tracked["games"])
    per_game = []
    for game in tracked["games"]:
        winner = game.get("winner")
        if not (game.get("counts_toward_set") and winner in ("p1", "p2")):
            continue
        cover = covers.get(round(float(game["start"]), 6))
        credited = ((cover or {}).get("sides") or {}).get(winner)
        label = next((entry for entry in labels.get("labels") or ()
                      if float(entry["at"]) >= float(game["start"])
                      and float(entry["at"]) <= float(game["end"])), None)
        expected = ((label or {}).get("names") or {}).get({"p1": "left", "p2": "right"}[winner])
        per_game.append({
            "game": [game["start"], game["end"]], "winner_side": winner,
            "credited_to": credited,
            "label_names_on_that_side": expected,
            "band_reading_usable": bool((cover or {}).get("usable")),
            "outcome": ("unattributed" if not credited else
                        "no_label_for_this_game" if not expected else
                        "matched" if agree(credited, expected) else "mismatched"),
            # An absence has to name itself. "unattributed" without a reason is the same blank space that
            # a side-only tally used to fill with an implied winner.
            "why_unattributed": (None if credited else
                                 (cover or {}).get("why_unusable") if cover
                                 else "no band reading falls inside this game"),
            "crop_sha256": (cover or {}).get("image_sha256"),
            "label_crop_sha256": (label or {}).get("band_sha256")})
    candidates = [{"start": record["start"], "end": record["end"], "status": record["status"],
                   "game_wins": record["game_wins"],
                   "deciding_wins": record["deciding_wins"],
                   "deciding_wins_unit": record["deciding_wins_unit"],
                   "set_winner": record["set_winner"], "winner_unit": record["winner_unit"],
                   "attribution_withheld": record["attribution_withheld"]}
                  for record in tracked["sets"]]
    return {"evidence_base": evidence["evidence_base"], "crop_readings_merged":
            evidence["crop_readings"], "games": per_game, "candidates": candidates,
            "tracked": tracked,
            "deciding_units_seen": sorted({record["deciding_wins_unit"] for record in
                                           tracked["sets"]}),
            "credited_games": sum(1 for g in per_game if g["outcome"] != "unattributed"),
            "unattributed_games": sum(1 for g in per_game if g["outcome"] == "unattributed"),
            "matched": sum(1 for g in per_game if g["outcome"] == "matched"),
            "mismatched": sum(1 for g in per_game if g["outcome"] == "mismatched")}


def tally_cross_check(labels, tracked, saved):
    """Per participant: what the board said they had won, against what the recovery credited by then.

    Compared only where both sides are expressible. A game whose sides nobody identified cannot be
    credited to a person, and a label outside a game says nothing about a tally inside one, so those cases
    are reported as uncomparable rather than as agreement.

    A disagreement is a finding, not a failure. A board showing fewer games than the recovery has counted
    means one of two things -- a conclusion was invented upstream, or the display lags the game it
    describes -- and the only way to tell which is to look at the span between them. That span is named.
    """
    covers = bg._band_map(saved, tracked["games"])
    checks, disagreements = [], []
    for label in [entry for entry in labels.get("labels") or () if entry.get("usable")]:
        at = float(label["at"])
        game = next((g for g in tracked["games"]
                     if float(g["start"]) <= at <= float(g["end"])), None)
        candidate = next((s for s in tracked["sets"]
                          if float(s["start"]) <= at <= float(s["end"])), None)
        if not game or not candidate:
            checks.append({"label_at": at, "comparable": False,
                           "because": "the labelled instant falls outside any recovered game here"})
            continue
        # Games the candidate had already concluded by this instant, whether or not anyone can say who
        # won them: that distinction decides whether a mismatch is a contradiction or an absence.
        concluded, counted, unattributed = [], {}, []
        for other in tracked["games"]:
            if float(other["start"]) < float(candidate["start"]):
                continue
            if float(other.get("conclusion_event_at") or other["end"]) > at:
                continue
            if not (other.get("counts_toward_set") and other.get("winner") in ("p1", "p2")):
                continue
            concluded.append(other)
            cover = covers.get(round(float(other["start"]), 6))
            holder = ((cover or {}).get("sides") or {}).get(other["winner"])
            if not holder:
                unattributed.append(other["start"])
                continue
            key = next((k for k in counted if agree(k, holder)), holder)
            counted[key] = counted.get(key, 0) + 1
        if concluded and not counted:
            # The recovery has counted games here but cannot say who took any of them. Reporting that as a
            # disagreement with the board would blame the board for a missing reading.
            checks.append({"label_at": at, "comparable": False, "game": [game["start"], game["end"]],
                           "candidate": [candidate["start"], candidate["end"]],
                           "games_concluded_by_then": len(concluded),
                           "because": "the recovery concluded %d game(s) in this candidate by this instant "
                                      "and cannot name the winner of any of them" % len(concluded)})
            continue
        names_by_side = {"p1": (label.get("names") or {}).get("left"),
                         "p2": (label.get("names") or {}).get("right")}
        displayed = {"p1": int((label.get("games_won") or {}).get("left") or 0),
                     "p2": int((label.get("games_won") or {}).get("right") or 0)}
        per_participant, findings = [], []
        for side, who in names_by_side.items():
            if not who:
                continue
            key = next((k for k in counted if agree(k, who)), who)
            shown, credit = displayed[side], counted.get(key, 0)
            kind = ("agree" if shown == credit else
                    # The board is ahead of a candidate that had concluded nothing: the games it counts
                    # opened before this interval, so the interval starts mid-match. That is a fact about
                    # the left edge, not a contradiction of the recovery.
                    "band_ahead_candidate_starts_mid_match" if not concluded and shown > credit else
                    "recovery_ahead" if credit > shown else "side_mapping_disagreement")
            per_participant.append({"participant": who, "band_displays": shown, "recovery_credits": credit,
                                    "outcome": kind})
            if kind != "agree":
                findings.append({"label_at": at, "participant": who, "band_displays": shown,
                                 "recovery_credits": credit, "kind": kind,
                                 "within": [candidate["start"], candidate["end"]],
                                 "games_concluded_by_then": len(concluded),
                                 "inspect": [float(game["start"]), at]})
        checks.append({"label_at": at, "comparable": True, "game": [game["start"], game["end"]],
                       "candidate": [candidate["start"], candidate["end"]],
                       "games_concluded_by_then": len(concluded),
                       "games_unattributable_by_then": len(unattributed),
                       "per_participant": per_participant,
                       "agree": all(entry["outcome"] == "agree" for entry in per_participant)})
        disagreements.extend(findings)
    return {"checks": checks, "disagreements": disagreements,
            "labels_compared": sum(1 for c in checks if c.get("comparable")),
            "labels_uncomparable": [{"label_at": c["label_at"], "because": c.get("because")}
                                    for c in checks if not c.get("comparable")],
            "meaning": "a disagreement is reported against the span that would settle it; neither source "
                       "is edited to make the figure match"}


def run():
    """main(), with the ordinary mistakes turned into named refusals.

    A traceback is the wrong interface for "point me at answers that exist"; the classes raise
    HeaderError, and this is where that becomes an exit message for any caller, not only for
    the command line.
    """
    try:
        main()
    except bh.HeaderError as exc:
        raise SystemExit(str(exc))



def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True,
                        help="Directories holding preserved answers, comma-separated -- usually the run "
                             "being graded plus any earlier band evidence it borrows. Each answer keeps "
                             "the provenance of the run it came from.")
    parser.add_argument("--labels", required=True, help="Frozen evaluator-side labels for these crops.")
    parser.add_argument("--region", type=float, nargs=2, required=True, metavar=("START", "END"),
                        help="The interval the trial covers, so the crop evidence merged under it is the "
                             "same evidence the planner priced.")
    parser.add_argument("--crop-runs", default=None,
                        help="Saved pip crop answer directories. Default: the canonical set the planner "
                             "merges, so a winner count here is the same measurement as there.")
    parser.add_argument("--rounds-per-game", type=int, default=3)
    parser.add_argument("--games-per-set", type=int, default=3)
    parser.add_argument("--output", default=None)
    parser.add_argument("--budget-file", default=None,
                        help="The attempt ledger that counted this run's answers, when one did. Provenance "
                             "only: the score is computed the same way either way, and a ledger that names "
                             "none of these frames is reported as not governing them.")
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if not labels.get("frozen"):
        raise SystemExit("refusing to score against labels that are not frozen: a moving truth cannot be "
                         "a control, and this file is read after the answers exist")
    if labels.get("annotations_for"):
        raise SystemExit("{} declares itself a runtime annotation, not evaluator truth".format(
            args.labels))
    if not (labels.get("labels") or ()):
        raise SystemExit("{} carries no labels to score against".format(args.labels))
    saved = bh.saved_readings(bh.response_directories(args.run), ledger=args.budget_file)
    graded = transcribe(labels, saved)
    credited = attribute(labels, saved, tuple(args.region), runs=args.crop_runs,
                         rounds_per_game=args.rounds_per_game, games_per_set=args.games_per_set)
    tracked = credited.pop("tracked")

    complete = all(row["answered"] for row in graded)
    report = {
        "kind": "attribution-evaluation",
        "labels_version": labels.get("version"), "run": str(args.run),
        "region": list(args.region),
        "responses": {"reused": len(saved["readings"]), "discarded": saved["discarded"],
                      "provenance": saved["provenance"]},
        "responses_reused": len(saved["readings"]),
        "responses_discarded": saved["discarded"],
        "provenance": saved["provenance"],
        "inference_sent": False,
        "transcription": {
            "question": "did the run say what the crop displays?",
            "crops_expected": len(graded), "crops_answered": sum(1 for r in graded if r["answered"]),
            "matched": sum(1 for r in graded if r.get("outcome") == "matched"),
            "mismatched": sum(1 for r in graded if r.get("outcome") == "mismatched"),
            "partially_declined": sum(1 for r in graded if r.get("outcome") == "partially_declined"),
            "no_answer": sum(1 for r in graded if not r.get("answered")),
            "fields": {"matched": sum(r.get("matched", 0) for r in graded),
                       "mismatched": sum(r.get("mismatched", 0) for r in graded),
                       "not_claimed": sum(r.get("not_claimed", 0) for r in graded)},
            "rows": graded},
        "attribution": dict(credited, question="did each validated game win reach the participant the "
                                              "band names on the winning side?"),
        "complete": complete,
        "accuracy_is_reportable": complete,
        "score": None if not complete else "{}/{} crops, {} games credited".format(
            sum(1 for r in graded if r.get("outcome") == "matched"), len(graded),
            credited["credited_games"]),
        "tally_cross_check": tally_cross_check(labels, tracked, saved),
        "known_disagreement_in_the_labels": labels.get("known_disagreement"),
        "claims_this_supports": [
            "whether the band route transcribes these crops, field by field",
            "whether a game the crop arm concluded is credited to the participant the band named on the "
            "winning side, or left explicitly unattributed",
        ],
        "claims_this_cannot_support": [
            "that a set was recovered: no candidate here reaches the configured target unless the crop "
            "evidence says so on its own",
            "that a band pip count measures a round: pip rows are recorded, never graded",
            "that agreement between the band and the recovery is proof of either -- both read the same "
            "recording, and a displayed tally is a display, not a record of what happened",
        ],
        "labels_are_never_runtime_input": True,
    }
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not complete:
        # A run that answered less than it was asked for prints its numbers and exits non-zero: what was
        # answered is evidence, it is just not a rate.
        raise SystemExit(1)


if __name__ == "__main__":
    run()
