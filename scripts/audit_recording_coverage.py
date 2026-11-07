#!/usr/bin/env python3
"""Recording-wide coverage audit from saved evidence only. Zero sends, zero new framework.

One question per line of the report, and each is answered from what is already on disk:

* **recovered** -- games and rounds the saved base tracks, and which of them name a winner;
* **unresolved** -- candidate sets still open, and the kind of claim that is missing from each;
* **unexamined** -- play rows the tracker never placed inside a game, cached frames that were never
  classified, and footage the query never reached;
* **still needs inference** -- the reads no saved evidence answers, priced by route, against the work
  that costs nothing (widening a window or re-reading a saved row).

Footage coverage and confidence are reported apart: a stretch can be fully watched and still decide
nothing, and a candidate can be complete on the tape with its membership unresolved. The optional
format is a second column, never merged into the first.

Marks are input like any other: each fault is re-run and compared, and a mark may change the order the
questions are asked in and nothing else.

    .venv/bin/python scripts/audit_recording_coverage.py \\
        --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \\
        --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \\
        --duration 6928.421136 --marks build/work/bb9/oct22.tvh.json \\
        --header-responses <band runs> --header-ledger <ledger> \\
        --format-policy build/work/bb9/set-end2end-v1/format-user-confirmed-2026-10-31.json \\
        --max-requests 2000 --out build/work/bb9/coverage-audit-v1
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_evidence as be                      # noqa: E402
from tekken_vod_helper import boundary_format as bf                        # noqa: E402
from tekken_vod_helper import boundary_games as bg                         # noqa: E402
from tekken_vod_helper import boundary_header as bh                         # noqa: E402
from tekken_vod_helper import boundary_investigate as bi                    # noqa: E402


def load_marks(path):
    # type: (Path) -> list
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("matches", data) if isinstance(data, dict) else data
    return [float(r["start"]) for r in rows
            if isinstance(r, dict) and isinstance(r.get("start"), (int, float))
            and not isinstance(r.get("start"), bool)]


def faults(marks):
    # type: (list) -> list
    """The marking faults the public path is already tested against, at recording scale."""
    openings = list(marks)
    middle = [(a + b) / 2.0 for a, b in zip(openings, openings[1:])]
    return [
        ("unmarked", []),
        ("faithful", openings),
        ("shifted early (-45s)", [m - 45.0 for m in openings]),
        ("shifted late (+45s)", [m + 45.0 for m in openings]),
        ("duplicated x3", [m for m in openings for _ in range(3)]),
        ("missing one match", openings[1:]),
        ("mid-set marks", middle[:4]),
    ]


def claims(result):
    # type: (dict) -> list
    """Every claim a marking could corrupt, in order, from the candidates the command publishes."""
    out = []
    for c in result["recovery"]["candidates"]:
        out.append((c["start"], c["end"], c["status"], c["membership"], c["games"],
                    json.dumps(c["game_wins"], sort_keys=True),
                    json.dumps(c.get("deciding_wins"), sort_keys=True),
                    c["deciding_wins_unit"], c["set_winner"],
                    tuple(c["concluded_by"] or ()), c["footage_complete"], c["clip_end_at"],
                    json.dumps(c.get("set_completion"), sort_keys=True),
                    c.get("grouping_unresolved"),
                    # A marking fault that moved a clip has to show up in the same comparison that catches
                    # one moving a boundary, and the uncertainty band travels with the cut it bounds.
                    c.get("clip_uncertainty") and round(
                        float(c["clip_uncertainty"].get("seconds_wide") or 0.0), 3),
                    len(c.get("boundary_conflicts") or [])))
    return out


def candidate_summaries(sets):
    # type: (Sequence[Dict[str, Any]]) -> list
    """The candidate claims this audit publishes, in one place so they can be tested without a recording.

    Extracted from ``coverage`` because a set's clip end is now one of the claims reported here: an export
    cuts on that number, and an audit that certified how much of the tape was watched while saying nothing
    about where its clips ended was checking everything except the cut a user actually gets.
    """
    return [{"start": c["start"], "end": c["end"], "status": c["status"],
                        "membership": c["membership"], "games": c["games"],
                        "game_wins": c["game_wins"], "game_winners": c["game_winners"],
                        "games_without_winner": c.get("games_without_winner"),
                        "deciding_wins": c.get("deciding_wins"),
                        "deciding_wins_unit": c.get("deciding_wins_unit"),
                        "set_winner": c.get("set_winner"),
                        "footage_complete": c["footage_complete"],
                        "concluded_by": c["concluded_by"],
                        "grouping_unresolved": c.get("grouping_unresolved"),
                        "boundary_conflicts": c.get("boundary_conflicts") or [],
                        # The cut an export would make, and the band that justifies it. Until now the audit
                        # reported how much of the tape was watched and never where a set's clip ended, so
                        # a clip that stopped short of its own winner-confirming read was invisible here.
                        "clip_end_at": c.get("clip_end_at"),
                        "clip_end_basis": c.get("clip_end_basis"),
                        "clip_end_anchor": c.get("clip_end_anchor"),
                        "clip_uncertainty": c.get("clip_uncertainty"),
                        "ambiguous_links_cut": c.get("ambiguous_links_cut"),
                        "alternative_groupings": len(c.get("alternative_groupings") or []),
                        "cut_before": (c.get("cut_before") or {}).get("verdict"),
                        "cut_after": (c.get("cut_after") or {}).get("verdict"),
                        "set_completion": c.get("set_completion"),
                        "missing_evidence": c["missing_evidence"]}
                       for c in sets]


def coverage(rows, tracked, cache, source):
    # type: (list, dict, list, dict) -> dict
    """What the tape holds, what was read, what was placed, and where the gaps are of each kind."""
    games = tracked["games"]
    play = [r for r in rows if r.get("state") == "gameplay"]

    def inside(row):
        hits = [g for g in games if g["start"] <= row["timestamp"] <= (g["end"] or g["start"])]
        return len(hits)

    unplaced = [r["timestamp"] for r in play if inside(r) != 1]
    classified = {round(float(r["timestamp"]), 6) for r in rows}
    never_read = [f["timestamp"] for f in cache if round(float(f["timestamp"]), 6) not in classified]

    def stretches(values, gap):
        out = []
        for value in sorted(values):
            if out and value - out[-1][1] <= gap:
                out[-1][1] = value
            else:
                out.append([value, value])
        return [(round(a, 3), round(b, 3)) for a, b in out]

    # The gap is set against the thing grouped: cached frames sit 1 s apart, so a 10 s tolerance would
    # merge unread runs straight across the frames that *were* read and report one unread block where
    # the tape was in fact sampled every few seconds. Play rows sit ~3.4 s apart, where 10 s is right.
    unread = stretches(never_read, 1.5)
    placed = stretches(unplaced, 10.0)
    # Read density per block, because "4,820 frames were never read" means nothing on its own: spread
    # evenly across a 6,928 s tape it says the sampling is coarse, concentrated in one corner of it it
    # says a stretch of the recording has never been looked at. Those are different next moves.
    block_seconds = 600.0
    blocks = []
    for index in range(int(math.ceil((cache[-1]["timestamp"] + 1) / block_seconds))):
        lo = index * block_seconds
        hi = lo + block_seconds
        in_block = [f for f in cache if lo <= f["timestamp"] < hi]
        if not in_block:
            continue
        read = [f for f in in_block if round(float(f["timestamp"]), 6) in classified]
        blocks.append({"from": round(lo, 1), "to": round(hi, 1), "cached": len(in_block),
                       "read": len(read), "unread": len(in_block) - len(read),
                       "longest_unread_run": max(
                           [(b - a) for a, b in stretches(
                               [f["timestamp"] for f in in_block
                                if round(float(f["timestamp"]), 6) not in classified], 1.5)] or [0])})
    limits = []
    for candidate in tracked["sets"]:
        edge = candidate.get("edges") or {}
        for side in ("start", "end"):
            entry = edge.get(side) or {}
            if not entry or (entry.get("limit") is None and not entry.get("unresolved_at")):
                continue
            # An edge can be observed *and* limited: the transition into the set was seen, and the tape
            # on one side of it was never sampled. Different disclosures, both reported.
            limits.append({"candidate": [candidate["start"], candidate["end"]], "side": side,
                           "at": entry.get("at"), "transition_observed": entry.get("observed"),
                           "limit": entry.get("limit"),
                           "cut_unresolved_at_edge": entry.get("unresolved_at")})
    return {
        "seconds_of_tape": [cache[0]["timestamp"], cache[-1]["timestamp"]] if cache else None,
        "source": source,
        "cached_frames": len(cache),
        "rows_in_the_joined_base": len(rows),
        "frames_never_read_by_any_route": len(never_read),
        "longest_unread_stretch_seconds": max([(b - a) for a, b in unread] or [0]),
        "unread_stretches_over_30s": [s for s in unread if s[1] - s[0] > 30.0],
        "read_density_per_600s": blocks,
        "play_rows": len(play),
        "play_rows_inside_one_game": sum(1 for r in play if inside(r) == 1),
        "play_rows_outside_every_game": sum(1 for r in play if inside(r) == 0),
        "play_rows_in_two_games": sum(1 for r in play if inside(r) > 1),
        "unplaced_play_stretches": placed,
        "rounds": len(tracked["rounds"]),
        "rounds_with_observed_start": sum(1 for r in tracked["rounds"]
                                          if not r.get("start_inferred")),
        "rounds_with_inferred_start": sum(1 for r in tracked["rounds"] if r.get("start_inferred")),
        "start_bases": dict(Counter(r["start_basis"] for r in tracked["rounds"])),
        "games": len(games),
        "games_complete": sum(1 for g in games if g["status"] == "complete"),
        "games_with_a_winner": sum(1 for g in games if g.get("winner")),
        "game_winners": [g.get("winner") for g in games],
        "candidates": candidate_summaries(tracked["sets"]),
        "edges_with_a_limit": limits,
    }


def price(plan_result):
    # type: (dict) -> dict
    """The bill for what the saved evidence cannot answer, by route and by candidate.

    Each route itemises its own outstanding reads -- ``regions`` for the state route, ``pip_requests``
    and ``header_requests`` for the two crop routes -- so the work is attributed to the candidate it
    falls inside. A total of 249 requests cannot say which set it would settle, and the next bounded
    investigation is chosen per candidate.
    """
    routes = {r["route"]: r for r in plan_result["dispatch"]["routes"]}
    work = [(region["route"], region["start"], region["end"], sorted(region["kinds"]),
             len(region["groups"]), sum(len(g["frame_timestamps"]) for g in region["groups"]))
            for region in plan_result["regions"]]
    # The two crop routes itemise one entry per request, and they spell their fields differently: a crop
    # read names its instant ``timestamp`` and what it asks ``asked``, a band read names it ``at`` and
    # ``question``. Both are handled; neither is guessed at.
    work += [(entry["route"], entry.get("timestamp", entry.get("at")),
              entry.get("timestamp", entry.get("at")),
              sorted(entry.get("asked") or [entry.get("question") or "unknown"]), 1,
              entry.get("images", 1))
             for entry in (plan_result.get("pip_requests") or [])
                          + (plan_result.get("header_requests") or [])]
    by_candidate = {}
    for route, lo, hi, kinds, requests, images in work:
        owner = next(("%0.3f-%0.3f" % (c["start"], c["end"])
                      for c in plan_result["recovery"]["candidates"]
                      if c["start"] - 1.0 <= lo <= (c["end"] or float("inf")) + 1.0),
                     "outside any candidate")
        bucket = by_candidate.setdefault(owner, {}).setdefault(
            route, {"requests": 0, "images": 0, "kinds": set(), "spans": []})
        bucket["requests"] += requests
        bucket["images"] += images
        bucket["kinds"].update(kinds)
        bucket["spans"].append((round(lo, 1), round(hi, 1)))
    for routes_in in by_candidate.values():
        for bucket in routes_in.values():
            bucket["kinds"] = sorted(bucket["kinds"])
            bucket["spans"] = sorted(set(bucket["spans"]))[:24]
    ceiling = sum(int(entry["requests_planned"]) * int(entry["completion_tokens_per_request"])
                  for entry in routes.values())
    # The plan's own request identities, so "the dry run priced what the live path sends" is a set
    # comparison a reader can repeat instead of a sentence to trust.
    identities = sorted({"%s:%s" % (region["route"], group.get("identity") or group.get("fingerprint"))
                         for region in plan_result["regions"] for group in region["groups"]}
                        | {"%s:%s" % (entry.get("route"), entry.get("identity"))
                           for entry in (plan_result.get("pip_requests") or [])
                                        + (plan_result.get("header_requests") or [])})
    return {
        "requests_planned": plan_result["requests_planned"],
        "request_identities": identities,
        "completion_tokens_ceiling_sum_all_routes": ceiling,
        "images_total": sum(int(entry["images_total"]) for entry in routes.values()),
        "completion_tokens_worst_case": plan_result.get("completion_tokens_worst_case"),
        "frames_total": plan_result.get("frames_total"),
        "groups_total": plan_result.get("groups_total"),
        "by_route": {name: {"planned": entry["requests_planned"],
                            "images": entry["images_total"],
                            "windows": entry.get("windows"),
                            "completion_tokens_ceiling_each": entry["completion_tokens_per_request"],
                            "reused": entry.get("reused", 0),
                            "unfunded": entry.get("requests_unfunded", 0),
                            "deferred": len(entry.get("deferred") or [])}
                     for name, entry in routes.items()},
        "by_candidate": by_candidate,
        "unresolved_kinds": plan_result["unresolved_kinds"],
        "stops_when": plan_result.get("stops_when"),
        # What the remaining asks are *not*: this arm was priced over the whole recording with widening
        # switched off, so nothing below is an artefact of a query stopping short. Any analysis_window
        # limit that still appears would be a bug in this script's own window, and it is reported.
        "free_work_check": {
            "expansion_seconds_per_side": plan_result.get("expansion", {}).get("per_side_seconds"),
            "edges_limited_by_the_query": [
                {"candidate": [c["start"], c["end"]], "side": side, "limit": entry.get("limit")}
                for c in plan_result["recovery"]["candidates"]
                for side, entry in ((c.get("edges") or {}).items())
                if isinstance(entry, dict) and entry.get("limit") == "analysis_window"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--marks", default=None)
    parser.add_argument("--header-responses", default=None)
    parser.add_argument("--header-ledger", default=None)
    parser.add_argument("--format-policy", default=None)
    parser.add_argument("--crop-runs", default=None)
    parser.add_argument("--step", type=float, default=2.0)
    parser.add_argument("--max-requests", type=int, default=2000)
    parser.add_argument("--expand-window", type=float, default=0.0,
                        help="Priced with widening off by default: the audit reports the whole tape, so "
                             "there is no outside a query reached by widening it.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval-expand", type=float, default=180.0,
                        help="Per side, how far an --interval arm may widen itself when its own edge says "
                             "the reason nothing settled is that the window stopped there. The whole-tape "
                             "arms do not use it: there is no outside their query to reach.")
    parser.add_argument("--interval", nargs=2, type=float, action="append", metavar=("START", "END"),
                        help="Also price this bounded interval as its own investigation, so the next "
                             "step can be approved with a number attached to it rather than to the "
                             "whole tape. Repeatable.")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    header = (bh.saved_readings(bh.response_directories(args.header_responses),
                                args.header_ledger) if args.header_responses else None)
    policy = bf.load_policy(args.format_policy) if args.format_policy else None
    cache = bi.load_cache(args.frame_manifest)
    source = bg.source_bounds(manifest=Path(args.frame_manifest), duration=args.duration)
    marks = load_marks(args.marks) if args.marks else []

    def arm(marks_, with_policy, label, region=None, expand=None):
        """``investigate-games --dry-run`` over the whole tape, or over one bounded interval.

        Marks travel as a file because that is the only form the public entry accepts -- ``_load_marks``
        reads a path, and a list handed straight to it would be a shape the product cannot produce. The
        file is written beside the report so the marking a number came from can be re-read.
        """
        slug = "".join(char if char.isalnum() else "-" for char in label).strip("-")
        path = out_dir / ("marks-%s.json" % slug)
        path.write_text(json.dumps({"matches": list(marks_)}), encoding="utf-8")
        return bi.plan_saved_investigation(
            args.timeline, args.frame_manifest, args.duration,
            marks=str(path) if marks_ else None, region=list(region) if region else None,
            # The planner's convention is the *opposite* of boundary_evidence's: here ``None`` means
            # "state rows only, no corpus" and the DISCOVER_CROPS sentinel means "everything this
            # recording has". Get it the wrong way round and the audit prices a state-only base and
            # reports the recording as owing pip reads it already holds.
            header=header, crop_runs=args.crop_runs or bi.DISCOVER_CROPS,
            format_policy=policy if with_policy else None,
            step=args.step, max_requests=args.max_requests,
            expand=args.expand_window if expand is None else expand,
            games_per_set=bg.DEFAULT_GAMES_PER_SET if with_policy else None)

    # ``None`` is the discovery signal here, not "no corpus": boundary_evidence.crop_runs resolves an
    # unset value by walking this recording's root, and a literal name handed to it would be looked up as
    # a directory and quietly become zero pip readings -- a base with no pip evidence reports no winner,
    # which is the exact wrong answer to be forced out of a config mistake.
    evidence = be.evidence(None, runs=args.crop_runs, facts=args.timeline)
    # Three passes over the one canonical base, from the shared module, so this audit cannot disagree
    # with the read-only report or with the command below about what a candidate was. ``unformatted`` is
    # the recording-only column and ``assisted`` is the same tape with the optional format applied; the
    # two are never added together.
    plans = {label: arm(marks, label == "with-format", label)
             for label in ("no-format", "with-format")}
    tracked = be.adjudicate(evidence["observations"], games_per_set=bg.DEFAULT_GAMES_PER_SET,
                            header=header, readings=(header or {}).get("readings"), policy=policy,
                            source=source)

    report = {
        "inference_sent": False,
        "evidence_base": {key: evidence[key] for key in
                          ("evidence_base", "rows", "crop_readings", "crop_runs_source",
                           "pip_readings_demoted", "framing_excluded", "crop_runs")},
        "no_format": {
            "coverage": coverage(evidence["row_records"], tracked["unformatted"], cache, source),
            "merge": evidence["merge"],
            "priced": price(plans["no-format"]),
        },
        "optional_format": {
            "policy": args.format_policy,
            "coverage": coverage(evidence["row_records"], tracked["assisted"], cache, source),
            "priced": price(plans["with-format"]),
        },
        "marking_matrix": {},
    }

    reference = claims(plans["no-format"])
    for label, mark_list in faults(marks):
        planned = arm(mark_list, False, label)
        now = claims(planned)
        report["marking_matrix"][label] = {
            "marks": [round(m, 3) for m in mark_list],
            "recovery_changed": now != reference,
            "candidates": [(c[0], c[1], c[2]) for c in now],
            "question_order": [round(r["start"], 1) for r in planned["regions"]],
            "requests_planned": planned["requests_planned"],
        }

    # Each candidate priced as its own bounded investigation: the recording-wide bill says what the tape
    # owes in total, and says nothing about which next step to approve.
    report["intervals"] = {}
    for lo, hi in args.interval or []:
        label = "interval-%g-%g" % (lo, hi)
        planned = arm(marks, False, label, region=(lo, hi), expand=args.interval_expand)
        expansion = planned.get("expansion") or {}
        # The number an approval has to be written against is the bill for the window the run would
        # *execute*, which is the widened one: a live `investigate-games` widens itself and then sends
        # against the wider query, while this dry run's own headline prices the window it was handed.
        # Both are published, and the agreement check below is what says they are the same plan.
        executable_window = expansion.get("expanded_window") or planned["window"]
        executable = arm(marks, False, label + "-executable", region=tuple(executable_window),
                         expand=0.0)
        print('   %s: requested %s priced %s | executable window %s priced %s (%s new reads from '
              'widening)' % (label, planned["window"], planned["requests_planned"],
                             list(executable_window), executable["requests_planned"],
                             (expansion.get("priced_only_missing") or {}).get("new_reads")))
        report["intervals"][label] = {
            "requested_window": [lo, hi],
            "window_priced_at_headline": planned["window"],
            "executable_window": list(executable_window),
            "expansion_triggered": expansion.get("triggered"),
            "expansion_steps": expansion.get("steps"),
            "stopping_reasons": expansion.get("stopping_reasons"),
            "blocked_by_edge": expansion.get("blocked_by_edge"),
            "observations": expansion.get("observations"),
            "priced_only_missing": expansion.get("priced_only_missing"),
            "headline_price_is_the_requested_window": price(planned),
            "requested_and_executable_are_the_same_window": (
                list(planned["window"]) == list(executable_window)),
            # The executable arm *is* the plan a live run builds: `investigate_saved_games` widens
            # through `apply_expansion` and then plans against the wider region, so pricing that region
            # with expansion switched off reproduces its request set. The stronger claim -- that a live
            # run sends exactly these identities -- is checked separately, against a stub transport, and
            # recorded next to this artifact rather than asserted here.
            "executable_price": price(executable),
            "candidates": [{"start": c["start"], "end": c["end"], "status": c["status"],
                            "games": c["games"], "footage_complete": c["footage_complete"],
                            "set_winner": c["set_winner"],
                            "missing_evidence": c["missing_evidence"]}
                           for c in executable["recovery"]["candidates"]],
            "inference_sent": bool(planned["inference_sent"] or executable["inference_sent"]),
        }
    (out_dir / "coverage-audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for column in ("no_format", "optional_format"):
        cov = report[column]["coverage"]
        print("%s: %d cached frames, %d rows in the joined base, %d frames never read by any route"
              % (column, cov["cached_frames"], cov["rows_in_the_joined_base"],
                 cov["frames_never_read_by_any_route"]))
        print("   unread footage: longest %.0f s, %d stretches over 30 s"
              % (cov["longest_unread_stretch_seconds"], len(cov["unread_stretches_over_30s"])))
        print("   rounds %d (start observed %d / inferred %d) | games %d, complete %d, with a winner %d"
              % (cov["rounds"], cov["rounds_with_observed_start"], cov["rounds_with_inferred_start"],
                 cov["games"], cov["games_complete"], cov["games_with_a_winner"]))
        print("   play rows %d: inside one game %d, outside every game %d, in two games %d"
              % (cov["play_rows"], cov["play_rows_inside_one_game"],
                 cov["play_rows_outside_every_game"], cov["play_rows_in_two_games"]))
        print("   candidates %d (%d complete, %d partial) | edges with a limit %d" % (
            len(cov["candidates"]),
            sum(1 for c in cov["candidates"] if c["status"] == "complete"),
            sum(1 for c in cov["candidates"] if c["status"] != "complete"),
            len(cov["edges_with_a_limit"])))
        for edge in cov["edges_with_a_limit"]:
            print("      %8.1f-%8.1f %-6s transition_observed=%s limit=%s cut_unresolved=%s" % (
                edge["candidate"][0], edge["candidate"][1], edge["side"],
                edge["transition_observed"], edge["limit"], edge["cut_unresolved_at_edge"]))
        print("   priced: %s" % json.dumps(report[column]["priced"]["by_route"]))
        print("   queue: %s" % json.dumps(report[column]["priced"]["unresolved_kinds"]))
    difference = [(c["start"], c["end"], c["status"], c["set_winner"])
                  for c in report["no_format"]["coverage"]["candidates"]] != \
                 [(c["start"], c["end"], c["status"], c["set_winner"])
                  for c in report["optional_format"]["coverage"]["candidates"]]
    print("\nthe optional format moved a boundary or a winner: %s" % difference)
    for label, entry in report["intervals"].items():
        print("\n%s -> requested %s priced %s | executable %s priced %s | stops %s" % (
            label, entry["window_priced_at_headline"],
            entry["headline_price_is_the_requested_window"]["requests_planned"],
            entry["executable_window"], entry["executable_price"]["requests_planned"],
            entry["stopping_reasons"]))
        for candidate in entry["candidates"]:
            print("   %8.1f-%8.1f %-8s games=%s footage=%s winner=%s" % (
                candidate["start"], candidate["end"], candidate["status"], candidate["games"],
                candidate["footage_complete"], candidate["set_winner"]))
        bill = entry["executable_price"]
        print("   executable bill: %s requests, %s images, summed ceiling %s" % (
            bill["requests_planned"], bill["images_total"],
            bill["completion_tokens_ceiling_sum_all_routes"]))
        print("   by_route %s" % json.dumps({k: (v["planned"], v["images"])
                                             for k, v in bill["by_route"].items()}))
        print("   queue %s" % json.dumps(bill["unresolved_kinds"]))
    for label, entry in report["marking_matrix"].items():
        print("marks %-24s recovery_changed=%s requests=%d" % (
            label, entry["recovery_changed"], entry["requests_planned"]))
    print("\nwrote %s" % (out_dir / "coverage-audit.json"))


if __name__ == "__main__":
    main()
