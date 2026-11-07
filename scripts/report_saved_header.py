#!/usr/bin/env python3
"""Re-run the set layer over real evidence with the preserved band answers, and price what is left.

Sends nothing, ever. There is no client, no endpoint argument and no budget-spend flag here: this reads
responses that already exist and answers that are already on disk, and the only thing it writes is a
report.

What it is for. Six band readings were spent, off-ledger, and the review's answer was to use them rather
than re-ask them. Using them means running them through the real adjudication path and reporting, per
boundary, whether the band settled *who sat on which side*, contradicted the overlay, or said nothing
because no reading falls inside the game. The third answer is the useful one for a budget: it is the list
of observations that are genuinely missing, as opposed to observations that were merely never asked for.

The evidence loading, the three-way adjudication and the set-level summary all come from
:mod:`tekken_vod_helper.boundary_evidence` -- the same functions the public recovery command
(``investigate-games``) calls -- so this report and that command cannot disagree about one recording by
joining its rows differently.

    env PYTHONPATH=. .venv/bin/python scripts/report_saved_header.py \
        --responses build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30 \
        --timeline  build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
        --region 3200 4000 --no-format \
        --output build/work/bb9/header-integration-v1/report.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tekken_vod_helper import boundary_evidence as be        # noqa: E402
from tekken_vod_helper import boundary_format                # noqa: E402
from tekken_vod_helper import boundary_header as bh          # noqa: E402

#: Statuses that mean the mapping is still unknown, and so still costable. Re-exported from the canonical
#: module rather than restated here: two lists of one set of statuses is how a gap stops being priced.
OPEN_STATUSES = be.OPEN_STATUSES
SETTLED_STATUSES = be.SETTLED_STATUSES

#: The clock/state rows this report adjudicates when the caller does not name another timeline: the
#: classified facts the pilot built, which is what every saved copy of this report was generated from.
DEFAULT_TIMELINE = be.DEFAULT_FACTS


def shape(sets):
    """Only the grouping facts, so a before/after diff cannot hide an attribution change inside it."""
    return be.grouping(sets)


def questions_of(sets):
    """One record per boundary question, not per candidate that mentions it (see the canonical module)."""
    return be.band_questions(sets)


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
    parser.add_argument("--responses", required=True,
                        help="Preserved header response directories -- the runs' own directories, as "
                             "written by run_header_trial.py, comma-separated when the evidence came from "
                             "more than one. They are loaded side by side and never merged into one "
                             "folder: a copied-together directory has one provenance for all its answers, "
                             "which is how six ungoverned responses got reported as ledger-counted.")
    parser.add_argument("--timeline", default=str(DEFAULT_TIMELINE),
                        help="The classified clock/state rows to adjudicate. Default: the pilot facts "
                             "every saved copy of this report was built from. Name the same timeline you "
                             "would hand investigate-games, or the two tools are describing different "
                             "recordings of the same match.")
    parser.add_argument("--crop-runs", default=None,
                        help="Comma list of saved pip crop answer directories to merge. Default: every "
                             "crop-answer directory discovered under this recording's evidence root, which "
                             "is what the planner prices against. Name the list only to reproduce an older "
                             "plan's inputs.")
    parser.add_argument("--state-only", action="store_true",
                        help="Skip the pip merge. Comparitor only: on this recording the state timeline "
                             "concludes games with no winner, so a winner count from it means nothing.")
    parser.add_argument("--region", type=float, nargs=2, required=True, metavar=("START", "END"),
                        help="The interval to adjudicate, in seconds. Required: an unbounded trace over "
                             "the whole VOD is not a report anyone asked for.")
    parser.add_argument("--rounds-per-game", type=int, default=3)
    parser.add_argument("--games-per-set", type=int, default=3,
                        help="One target for every candidate. Superseded per candidate by "
                             "--format-policy, which selects the target from the stage the band names.")
    parser.add_argument("--format-policy", default=None,
                        help="A user-confirmed event format file (see boundary_format.load_policy). Each "
                             "candidate is then measured against the target its own span's stage names -- "
                             "first to two for a regular set, three for the named top four -- and against "
                             "every target the format admits when no stage was readable there, reporting a "
                             "set as complete only where all of them agree.")
    parser.add_argument("--no-format", action="store_true",
                        help="Format-removed comparison: no target at all, so no candidate can close. "
                             "This is what shows how much of a recovery came from the recording.")
    parser.add_argument("--output", default=None, help="Where to write the report.")
    parser.add_argument("--budget-file", default=None,
                        help="The attempt ledger that counted these answers, when one did. Report-only: "
                             "it changes what the provenance says, never what is adjudicated. A ledger "
                             "with no answered attempt naming these frames is refused, not adopted.")
    args = parser.parse_args()
    if args.no_format and args.format_policy:
        raise SystemExit("--no-format and --format-policy contradict each other: one supplies the event's "
                         "rules and the other removes them")
    # A target is only ever applied where a stage or a caller put it there. --no-format is the honest
    # comparison, not a broken run: with no target nothing closes, and the gap between that report and the
    # configured one is exactly how much the format contributed.
    policy = (boundary_format.load_policy(args.format_policy) if args.format_policy else None)
    games_per_set = None if args.no_format else args.games_per_set

    saved = bh.saved_readings(bh.response_directories(args.responses), ledger=args.budget_file)
    # The canonical pip merge, shared with scripts/plan_set_end_to_end.py and the public recovery command.
    # Reading the state facts alone yields games with no winners -- true of the state evidence, useless as
    # a claim about the recording, and exactly the mismatch that let this report contradict the planner.
    evidence = be.evidence(args.region, runs=args.crop_runs, state_only=args.state_only,
                           facts=args.timeline)
    # Three runs over the one evidence base, each isolating one input: without the band's pairings (its
    # stage text still applies, so the comparison is about *who is playing*, not about which round the
    # band says this is), with them, and with no target anywhere. From the canonical module, because a
    # report that ran its own three passes is a second implementation of the comparison.
    tracked = be.adjudicate(evidence["observations"], rounds_per_game=args.rounds_per_game,
                            games_per_set=games_per_set, header=saved, readings=saved["readings"],
                            policy=policy, region=args.region)
    recovered = be.recovery(evidence, tracked, header=saved, policy=policy)
    merge = dict(recovered["pip_evidence"]["merge"])
    # The key this report has always published, spelled as it was when the merge lived in the game-one
    # harness. Only the name is historical; the count comes from the shared merge. A state-only run has no
    # merge at all, and an empty comparitor is not a merge whose keys went missing.
    if "unjoinable_rows" in merge:
        merge["unjoinable_pilot_rows"] = merge.pop("unjoinable_rows")
        recovered["pip_evidence"]["merge"] = merge
    recovered["responses"] = {"directory": saved["source_directory"],
                              "readings": len(saved["readings"]), "discarded": saved["discarded"],
                              "provenance": saved["provenance"], "inference_sent": False,
                              "attempts_charged_now": 0}
    recovered["what_this_shows"] = [
        "which boundary questions the preserved readings settle, and on which crops",
        "that reusing an answer is not the same act as spending one: nothing here charged an attempt",
    ]
    recovered["what_this_does_not_show"] = [
        "that any set was recovered: a candidate with no game winners has no tally, attributed or not",
        "that a band reading is validated pip evidence: every pairing here carries the "
        "unvalidated_header_diagnostic quarantine",
        "anything about a grouping decision: the band is consulted for identity only, and this report "
        "asserts that as a fact (grouping_changed_by_band)",
    ]
    recovered["kind"] = "saved-header-integration-report"
    recovered["timeline"] = str(args.timeline)
    report = recovered

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if report["grouping_changed_by_band"]:
        # Loud, because the whole design rests on it: an identity witness that moved a boundary is a bug.
        raise SystemExit("BAND EVIDENCE CHANGED THE GROUPING: that is not what it is for")


if __name__ == "__main__":
    run()
