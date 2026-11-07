#!/usr/bin/env python3
"""What a real end-to-end set trial still needs, priced from the evidence that already exists.

Offline: no client, no endpoint argument, no budget flag. It reads the crop answers, the band answers and
the clock/state facts already on disk, runs the adjudication over one bounded interval, and reports the
**gap** -- which observations would have to be requested, and what each costs.

Why a gap list rather than a fresh batch plan. Six band readings have been paid for, and 110 crop readings
before them. Re-asking any of them to make a run look properly ledger-backed would spend real attempts to
manufacture a duplicate of data already held.

Why the evidence base is imported and not reimplemented: the planner and the integration report once
disagreed about the same interval -- four validated winners here, zero there -- because one merged the crop
answers and the other read the state timeline alone. Both now call
:func:`pip_evidence.evidence`, so a winner count means one thing in this repository.

    env PYTHONPATH=. .venv/bin/python scripts/plan_set_end_to_end.py \
        --responses build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30 \
        --region 3200 4000 --games-per-set 3 \
        --attribution-labels build/work/bb9/set-end2end-v1/attribution-labels-eval-frozen.json \
        --output build/work/bb9/set-end2end-v1/missing-observations.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tekken_vod_helper import boundary_format                        # noqa: E402
from tekken_vod_helper import boundary_games as bg                   # noqa: E402
from tekken_vod_helper import boundary_header as bh                  # noqa: E402
from tekken_vod_helper import boundary_pips as bp                    # noqa: E402
from tekken_vod_helper import boundary_windows as bw                 # noqa: E402

import pip_evidence                                                   # noqa: E402

#: Intervals where a set could plausibly be recovered today, chosen because crop or band answers exist in
#: them. Surveyed to find out what the two evidence classes cover, not to pick a flattering one.
SURVEYED = [(44.0, 262.0), (226.0, 430.0), (736.0, 1080.0), (1200.0, 2200.0), (3200.0, 4000.0)]

#: A pip reading costs three frames per window: the end-evidence frame and the two after it, because the
#: decisive dot can land late. Anything smaller would quietly shrink what one request can prove.
CROP_FRAMES_PER_REQUEST = 3


def survey(band, runs, rounds_per_game=3, games_per_set=3, format_policy=None):
    # type: (dict, str, int, int, Optional[dict]) -> dict
    """For each surveyed interval: winners, band coverage, complete sets. Measured, never estimated.

    The same ``format_policy`` the priced interval is measured under, because a survey using a different
    format from the plan beside it would print two candidate counts that mean different things -- and the
    reason this function exists is that both tools now read one evidence base.
    """
    rows_out, any_overlap = [], False
    for start_at, end_at in SURVEYED:
        evidence = pip_evidence.evidence((start_at, end_at), runs=runs)
        result = bg.track_games(evidence["observations"], rounds_per_game=rounds_per_game,
                                games_per_set=games_per_set, format_policy=format_policy,
                                stage_readings=band["readings"])
        winners = [g for g in result["games"]
                   if g.get("counts_toward_set") and g.get("winner")]
        covered = sum(1 for g in result["games"]
                      if any(r.get("at") is not None
                             and float(g["start"]) <= float(r["at"]) <= float(g["end"])
                             for r in band["readings"]))
        creditable = sum(1 for s in result["sets"]
                         if (s.get("attribution") or {}).get("game_wins_by_identity"))
        if winners and covered:
            any_overlap = True
        rows_out.append({"interval": [start_at, end_at], "games": len(result["games"]),
                         "games_with_a_validated_winner": len(winners),
                         "games_with_a_band_reading_inside": covered,
                         "candidates_with_a_per_participant_tally": creditable,
                         "complete_sets": sum(1 for s in result["sets"] if s["status"] == "complete"),
                         "crop_readings_merged": evidence["crop_readings"]})
    # Written as a reading, not as prose. This sentence used to assert that no win on disk was creditable
    # to a named participant, which was true when it was written and became false the day both trials
    # finished -- a consequence that is computed cannot go stale in the middle of a document.
    return {"intervals_surveyed": rows_out, "any_interval_has_both_evidences": any_overlap,
            "consequence": (
                "at least one interval holds both a crop-validated winner and a band reading inside the "
                "same game, so a win on disk can be credited to a named participant there"
                if any_overlap else
                "no win on disk is currently creditable to a named participant: the crop readings that "
                "decided games and the band answers that name players do not overlap in any surveyed "
                "interval"),
            "method": "surveyed from saved answers through the canonical merge; nothing was requested to "
                      "produce this"}


def _why_nothing_is_credited(result, games):
    # type: (dict, list) -> str
    """Say what actually stops a win being credited to a person, in the words the tracker used.

    This string used to be one fixed sentence claiming the interval held no wins to credit, and it kept
    saying so beside a line reporting three concluded winners -- because the reason had changed from
    "nothing won" to "the naming was withheld" and the prose had not. So the reason is read from the
    candidates now: an interval with winners and no per-participant tally is a naming problem, and quoting
    the tracker's own withholding is the only honest way to say so.
    """
    winners = sum(1 for g in games if g.get("counts_toward_set") and g.get("winner"))
    credited = [s for s in result["sets"] if (s.get("attribution") or {}).get("game_wins_by_identity")]
    if credited:
        return "not empty: %d candidate(s) publish a per-participant tally" % len(credited)
    if not winners:
        return ("attribution credits a win to a person, and this interval has no wins to credit: %d game(s) "
                "and none concluded with a winner" % len(games))
    withheld = [s.get("attribution_withheld") for s in result["sets"] if s.get("attribution_withheld")]
    return ("wins exist and nobody is credited: %d game(s) concluded on a screen side, and the band could "
            "not name that side -- %s" % (winners, " | ".join(withheld)[:420] or
                                          "no usable reading fell inside the winning games"))


def stages(surveyed, crop_requests, crop_images, band_requests_for_interval, labels,
           crop_reused=0, interval_sets=None):
    # type: (dict, int, int, int, dict, int, Optional[dict]) -> list
    """Two priced steps, ordered so the cheapest knowledge is bought first.

    Every figure here is read out of the survey, *including* "nothing". This function used to assert its
    own premises: Stage A cost one band reading per validated winner because that interval had no band
    readings, and Stage B came with the sentence "no crop reading on disk at all". Both were true the day
    they were written and both went false the moment the trials they priced finished -- which is how a plan
    ends up billing for 45 images that are already answered.
    """
    out = []
    band_ready = next((i for i in surveyed["intervals_surveyed"]
                       if i["games_with_a_validated_winner"] >= 3), None)
    if band_ready:
        outstanding = max(0, band_ready["games_with_a_validated_winner"]
                          - band_ready["games_with_a_band_reading_inside"])
        out.append({
            "stage": "A", "name": "attribute winners the crop arm already validated",
            "interval": band_ready["interval"],
            "requests": outstanding, "images": outstanding,
            "already_on_disk": band_ready["games_with_a_band_reading_inside"],
            "why": ("this interval holds %d games with crop-validated winners and no band reading inside any "
                    "of them, so the cheapest new observation available is the nameplate -- and it tests win "
                    "attribution without asking the pip arm anything again"
                    % band_ready["games_with_a_validated_winner"] if not outstanding else
                    "this interval's %d validated winners already have %d readings inside their games: the "
                    "attribution pass is bought, and re-asking it buys a duplicate, not evidence"
                    % (band_ready["games_with_a_validated_winner"],
                       band_ready["games_with_a_band_reading_inside"])),
            "labels": labels,
            "what_it_proves": "that a concluded game's win is credited to the person the broadcast named on "
                              "that side, or left explicitly unattributed",
            "what_it_cannot_prove": (
                "that any set was recovered here. The interval was chosen for the winners it has, and no "
                "candidate in it reaches the configured target on this evidence"
                if not (interval_sets or {}).get("complete") else
                "that attribution alone closed the set: this interval reports %d complete candidate(s) "
                "under the format applied, and a closure deserves the same scrutiny as a recovery"
                % (interval_sets or {}).get("complete"))})
    outstanding_b = crop_requests + band_requests_for_interval
    out.append({
        "stage": "B", "name": "recover one bounded set end to end",
        "requests": outstanding_b,
        "images": crop_images + band_requests_for_interval,
        "already_on_disk": crop_reused,
        "why": ("%d round windows in this interval have no crop reading on disk, and %d game(s) have no "
                "band reading: that is what a conclusion costs here"
                % (crop_requests, band_requests_for_interval) if outstanding_b else
                "this interval is already read -- %d crop windows reused and no game without a band reading "
                "-- so its price is zero, and any further request has to be argued from a named missing "
                "observation rather than from a plan template" % crop_reused),
        "precondition": "its conclusion evidence has to be inspected off the footage first -- without it "
                        "the run grades itself -- and the first-to-N target is the operator's to set, never "
                        "read off the detector",
        "what_it_proves": "one candidate closing on wins observed in the recording, each credited to a "
                          "named participant where the band saw the side, with every remaining ambiguity "
                          "still reported",
        "what_it_cannot_prove": "that the recovery generalises from one interval"})
    return out


def label_status(path):
    # type: (str) -> dict
    """What has been inspected for a trial, or the reason it is not yet scoreable."""
    if not path:
        return {"inspected": False,
                "because": "no label file was supplied: this stage is priced but cannot be graded"}
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not document.get("frozen"):
        raise SystemExit("{} is not a frozen label file".format(path))
    if document.get("annotations_for"):
        raise SystemExit("{} is a runtime annotation, not evaluator truth".format(path))
    return {"inspected": True, "path": str(path), "version": document.get("version"),
            "labels": len(document.get("labels") or ()),
            "pip_rows_scored": not document.get("pip_readings_are_not_scored", True),
            "known_disagreement": bool(document.get("known_disagreement"))}


def emit_spec(path, games, labels, note):
    # type: (str, list, dict, str) -> dict
    """Write the Stage A runtime spec: instants and reasons, and no expected values whatsoever."""
    instants = []
    for entry in games:
        at = round((float(entry["start"]) + float(entry["end"])) / 2.0, 6)
        instants.append({"at": at, "question": "identity_mapping",
                         "selected_by": "midpoint of a game whose winner is already validated by the crop "
                                        "arm; the band is asked who sat on each side, not whether the game "
                                        "was won",
                         "because": "credit the win of the game at %s s to a named participant instead of a "
                                    "screen side" % round(float(entry["start"]), 2)})
    if not instants:
        raise SystemExit("nothing to emit: no game in that interval has a validated winner to attribute")
    spec = {"kind": bh.TRIAL_KIND, "version": "attribution-stage-a-v1",
            "purpose": "Attribute already-validated game wins to the participants the broadcast credited. "
                       "Not a set-recovery trial, and it claims nothing about a set boundary.",
            "frame_manifest": bw.DEFAULT_MANIFEST, "radius_seconds": 1.0, "scale": 3,
            "selection_rule": "one instant per game in the interval, at its midpoint. The games come from "
                              "crop readings already on disk, so unlike the header trial's fixed grid this "
                              "selection does read a validated winner -- disclosed because it changes what "
                              "the trial can claim: whose win it was, not whether there was one",
            "evaluation_labels": (labels.get("path") if labels.get("inspected")
                                  else "none yet -- inspect the exact crops before any answer exists"),
            "labels_are_runtime_input": False,
            "note": note, "instants": instants}
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(out), "instants": len(instants), "at": [i["at"] for i in instants]}


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
                        help="Preserved header response directories, comma-separated. Named per run so "
                             "each answer keeps its own provenance; do not copy runs into one folder.")
    parser.add_argument("--region", type=float, nargs=2, required=True, metavar=("START", "END"),
                        help="The interval a set could be recovered in, for the crop price.")
    parser.add_argument("--rounds-per-game", type=int, default=3)
    parser.add_argument("--games-per-set", type=int, default=3,
                        help="One target for every candidate. Superseded per candidate by --format-policy.")
    parser.add_argument("--format-policy", default=None,
                        help="A user-confirmed event format file (see boundary_format.load_policy). Each "
                             "candidate is then priced and measured against the target the stage in its own "
                             "span names, so a first-to-two quarter-final is not billed as an unfinished "
                             "first-to-three set.")
    parser.add_argument("--no-format", action="store_true",
                        help="Format-removed comparison: apply no target anywhere.")
    parser.add_argument("--crop-runs", default=None,
                        help="Comma list of saved pip crop answer directories. Default: every crop-answer "
                             "directory discovered under this recording's evidence root -- the same set the "
                             "integration report merges. Name the list only to reproduce an older plan.")
    parser.add_argument("--max-crop-requests", type=int, default=None,
                        help="Cap the crop list. Default: every window the interval needs, because a "
                             "deferred round reading leaves a game uncounted rather than half answered.")
    parser.add_argument("--attribution-interval", type=float, nargs=2, metavar=("START", "END"),
                        default=None, help="Interval to emit the Stage A spec for. Default: the surveyed "
                                           "interval with at least three validated winners.")
    parser.add_argument("--attribution-labels", default=None,
                        help="Frozen labels for the Stage A crops. With them the plan reports what has been "
                             "inspected; without them Stage A is priced but not scoreable, and says so.")
    parser.add_argument("--emit-attribution-spec", default=None, metavar="PATH")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if args.no_format and args.format_policy:
        raise SystemExit("--no-format and --format-policy contradict each other")
    policy = boundary_format.load_policy(args.format_policy) if args.format_policy else None
    if args.no_format:
        args.games_per_set = None

    start_at, end_at = args.region
    band = bh.saved_readings(bh.response_directories(args.responses))
    evidence = pip_evidence.evidence(args.region, runs=args.crop_runs)
    result = bg.track_games(evidence["observations"], rounds_per_game=args.rounds_per_game,
                            games_per_set=args.games_per_set, header=band, format_policy=policy)
    games = result["games"]

    def inside(entry):
        return [r for r in band["readings"] if r.get("at") is not None
                and float(entry["start"]) <= float(r["at"]) <= float(entry["end"])]

    games_missing_band = [{"span": [g["start"], g["end"]], "status": g["status"],
                           "winner_side": g.get("winner"),
                           "because": "no preserved band reading falls inside this game, so a win here "
                                      "cannot be credited to a named participant"}
                          for g in games if not inside(g)]
    between = [{"at": r["at"], "sides": [r["pairing"]["left"].get("player"),
                                         r["pairing"]["right"].get("player")],
                "use": "bears on where the games and matches change, not on who won them"}
               for r in band["readings"]
               if not any(float(g["start"]) <= float(r["at"]) <= float(g["end"]) for g in games)]

    plan = bw.plan_interval(evidence["observations"],
                            bw.load_saved_readings(pip_evidence.crop_runs(args.crop_runs)),
                            bw.FrameIndex.load(), start_at, end_at,
                            args.max_crop_requests or 10 ** 6)
    crop_requests = plan["requests_planned"]
    labels = label_status(args.attribution_labels)
    surveyed = survey(band, args.crop_runs, args.rounds_per_game, args.games_per_set, policy)

    missing = [
        {"kind": "pip_crop_readings_at_round_endings",
         "requests": crop_requests, "images": plan["images_total"],
         "reused": len(plan["reused_readings"]), "deferred": plan["requests_deferred"],
         "windows": len(plan["windows"]),
         "why": "the clock/state timeline carries no trustworthy win-dot row at this resolution, so every "
                "game in the interval is a game without a winner, and the crop arm is the only reading this "
                "project has validated against a frozen geometry",
         "cost_basis": "measured on this endpoint, same arm and crop count: median 4.2 s and p95 9.0 s per "
                       "request, median 459 completion tokens against a 3,000 ceiling"},
        {"kind": "header_band_readings_inside_unwatched_games",
         "requests": len(games_missing_band), "images": len(games_missing_band),
         "reused": len(band["readings"]) - len(between),
         "why": "a concluded game credits a win to a screen side; only the nameplate says which person held "
                "that side, and three of the six preserved readings fall between games",
         "cost_basis": "measured on this endpoint for the same band crop: 829 prompt and ~500 completion "
                       "tokens per request, against a 1,500 ceiling",
         "for_games": games_missing_band},
        {"kind": "independently_inspected_conclusion_labels", "requests": 0, "images": 0,
         "why": "without labels read off the footage by something other than the pipeline, a recovery that "
                "agrees with itself is the only evidence it has; inspecting them costs no inference",
         "scope": "one label per round ending, per game conclusion and for the candidate's set end -- %d "
                  "windows, %d games in this interval" % (len(plan["windows"]), len(games))},
    ]

    report = {
        "kind": "set-trial-missing-observations",
        "region": [start_at, end_at], "seconds": end_at - start_at,
        "format": result["format"],
        "evidence_base": {"name": evidence["evidence_base"], "facts": evidence["facts"],
                          "crop_runs": evidence["crop_runs"],
                          "crop_readings_merged": evidence["crop_readings"],
                          "shared_with": "scripts/report_saved_header.py"},
        "state_of_the_evidence_now": {
            "rows": evidence["rows"], "rounds": len(result["rounds"]), "games": len(games),
            "games_with_a_winner": sum(1 for g in games
                                       if g.get("counts_toward_set") and g.get("winner")),
            "set_candidates": len(result["sets"]),
            "complete_sets": sum(1 for s in result["sets"] if s["status"] == "complete"),
            "band_readings_available": len(band["readings"]),
            "band_readings_inside_a_game": len(band["readings"]) - len(between),
            "deciding_wins_units": sorted({record["deciding_wins_unit"] for record in result["sets"]}),
            "why_attribution_is_empty_here": _why_nothing_is_credited(result, games),
            "candidates_measured_on_identities": sum(1 for record in result["sets"]
                                                     if record["deciding_wins_unit"] == "identities"),
        },
        "games_without_a_band_reading": games_missing_band,
        "band_readings_falling_between_games": between,
        "missing_observations": missing,
        "evidence_overlap": surveyed,
        "stages": stages(surveyed, crop_requests, plan["images_total"], len(games_missing_band), labels,
                         crop_reused=len(plan["reused_readings"]),
                         interval_sets={"complete": sum(1 for s in result["sets"]
                                                        if s["status"] == "complete")}),
        "totals": {"new_requests": crop_requests + len(games_missing_band),
                   "new_images": plan["images_total"] + len(games_missing_band),
                   "requests_reused_not_resent": len(plan["reused_readings"]) + len(band["readings"]),
                   "completion_token_ceiling": crop_requests * bp.PipReadClient.request_max_tokens
                                               + len(games_missing_band)
                                               * bh.HeaderReadClient.request_max_tokens},
        "pass_condition": "one candidate reaches the configured target on wins observed in the recording, "
                          "with every counted win credited to a named participant and every ambiguity still "
                          "reported; a partial recovery that names its gaps also passes, a complete-looking "
                          "tally built on unattributed sides does not",
        "not_included": [
            "the six band readings already preserved: re-asking them buys a duplicate, not evidence",
            "any final tally inferred from an unlabelled stretch of this interval",
            "a full-VOD pass, and any expectation about how many sets the interval holds",
        ],
        "inference_sent": False,
    }
    if args.emit_attribution_spec:
        window = list(args.attribution_interval) if args.attribution_interval else next(
            (i["interval"] for i in reversed(surveyed["intervals_surveyed"])
             if i["games_with_a_validated_winner"] >= 3), None)
        if not window:
            raise SystemExit("no surveyed interval has validated winners to attribute: nothing to emit")
        interval_evidence = pip_evidence.evidence(window, runs=args.crop_runs)
        interval_games = bg.track_games(interval_evidence["observations"],
                                        rounds_per_game=args.rounds_per_game,
                                        games_per_set=args.games_per_set,
                                        format_policy=policy)["games"]
        report["attribution_spec_written"] = emit_spec(
            args.emit_attribution_spec,
            [g for g in interval_games if g.get("counts_toward_set") and g.get("winner")],
            labels, "labels are evaluator-side truth and this spec must not carry them")

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    try:
        main()
    except bh.HeaderError as exc:
        # A named refusal, not a traceback: the common case for this path is pointing it at a run that has
        # not been executed yet, which is a fact about the next step rather than a crash.
        raise SystemExit(str(exc))
