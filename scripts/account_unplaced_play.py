"""Account for every play row that sits inside no game, and name the kind of footage it is.

The audit's job here is not to count unplaced rows -- it is to say what each one is, from the evidence
around it, because the two failure modes point in opposite directions: calling every frame after a K.O.
"missing gameplay" invents work that does not exist, and excusing a real unplaced round as "animation"
hides the one thing worth spending on. So every rule below cites the evidence it read, and a stretch that
no rule decides stays explicitly unresolved.

Offline only: reads the saved classification, the joined crop readings and the frame cache. No request.

    .venv/bin/python scripts/account_unplaced_play.py \\
        --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \\
        --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \\
        --out build/work/bb9/unplaced-play-v1/account.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_evidence as be          # noqa: E402
from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_header as bh
from tekken_vod_helper import boundary_investigate as bi             # noqa: E402

# Prose the state route uses for the result sequence that follows a round: a win banner, a pose, a
# close-up, the PERFECT that ends a round on no damage. These are how a frame says "this round is over"
# without naming an event word.
_CONCLUSION_RE = re.compile(r"victory|win pose|win-pose|you win|close-?up|winner|celebrat|knock-?out "
                            r"anim|round-?end|result|replay prompt|add a battle replay|perfect", re.I)
_OPENING_RE = re.compile(r"round start|new round|fight start|start of (?:the )?round|'ROUND [1-9]'|"
                         r"ready|pre-?round|starting stances|in-?arena round start", re.I)
_MENU_RE = re.compile(r"pause menu|move list|select character|main menu|rematch|side menu|menu overlay|"
                      r"black transition|loading", re.I)
# The clock the prose states before the word ("29s timer", "60-second timer"), which the tracker's clock
# pattern reads only after it. Named here because it is what leaves a live round with no reading.
_CLOCK_BEFORE_WORD = re.compile(r"\b(\d{1,2})\s*[- ]?\s*(?:s\b|secs?\b|seconds?\b)[^.;,]{0,14}?"
                                r"\b(?:round\s+)?(?:timer|clock)\b", re.I)
# A menu phrase is only a menu when the clause it sits in does not deny it. The tracker's own negation
# check looks back 32 characters, which misses "no controller assignment or post-match menu overlay" --
# 39 characters of denial ahead of the noun. This audit is reading prose as evidence about prose, so it
# looks back to the start of the clause instead, and says so rather than inheriting a shorter window.
_CLAUSE_BREAK = re.compile(r"[;)]")
_NEGATION = re.compile(r"\b(?:no|not|never|without|absent|nor|neither|lacks?|isn'?t|aren'?t)\b", re.I)


def menu_prose(text):
    # type: (str) -> str
    """The menu or transition a row states without denying it, or an empty string."""
    for match in _MENU_RE.finditer(text or ""):
        before = text[:match.start()]
        boundary = max((m.end() for m in _CLAUSE_BREAK.finditer(before)), default=0)
        if not _NEGATION.search(before[boundary:]):
            return match.group(0)
    return ""

KINDS = ("opening_footage", "post_conclusion_animation", "transition_or_false_gameplay_label",
         "unassigned_live_play", "unresolved")


def frame_lookup(manifest_path):
    frames = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["frames"]

    def nearest(timestamp):
        best = min(frames, key=lambda f: abs(f["timestamp"] - timestamp))
        return {"frame": Path(str(best["path"])).name, "at": round(best["timestamp"], 3),
                "offset": round(best["timestamp"] - timestamp, 3)}
    return nearest


def classify(stretch, games, rounds, full_clock=60):
    # type: (dict, list, list, int) -> dict
    """Name what one stretch of unplaced play is, and quote the evidence that says so.

    Precedence is deliberate. A clock that runs *down* across the stretch is positive evidence of a round
    being played, so it outranks every adjacency argument about animation; and prose that says the menu is
    *not* on screen is not a menu -- the same clause-bounded negation the tracker applies to its own cues,
    because "no rematch menu" read as a menu turns eleven result sequences into false labels.
    """
    rows = stretch["rows"]
    reasons, kind = [], "unresolved"
    lo, hi = rows[0]["at"], rows[-1]["at"]
    prior = [g for g in games if (g["end"] or 0) <= lo + 0.001]
    later = [g for g in games if g["start"] >= hi - 0.001]
    prev_game = max(prior, key=lambda g: g["end"] or 0) if prior else None
    next_game = min(later, key=lambda g: g["start"]) if later else None
    gap_after = None if prev_game is None else round(lo - (prev_game["end"] or lo), 3)
    gap_before = None if next_game is None else round(next_game["start"] - hi, 3)

    texts = [row["evidence"] or "" for row in rows]
    menus = [t for t in texts if menu_prose(t)]
    conclusions = [t for t in texts if _CONCLUSION_RE.search(t)]
    openings = [t for t in texts if _OPENING_RE.search(t)]
    ends_on_rows = [row for row in rows if row["round_end"]]
    transitions = [row for row in rows if row["transition"]]
    cues = [row["cue"] for row in rows if row["cue"]]
    clocks = [row["clock"] for row in rows if row["clock"] is not None]
    # The clock the prose states and the tracker could not read: named here because it is what leaves a
    # live round with no series, and the cause of at least one stretch below.
    prose_clocks = [int(m.group(1)) for t in texts for m in [_CLOCK_BEFORE_WORD.search(t)] if m]
    series = clocks or prose_clocks
    falling = len(series) >= 2 and series[-1] < series[0]
    frozen = len(set(clocks)) == 1 and len(clocks) >= 2

    if menus and not falling:
        kind = "transition_or_false_gameplay_label"
        reasons.append("the row's own prose puts %r on screen without denying it: %s"
                       % (menu_prose(menus[0]), menus[0][:160]))
    if kind == "unresolved" and gap_before is not None and gap_before <= 6.0 \
            and (gap_after is None or gap_after > 10.0) and not ends_on_rows and not falling:
        opening_evidence = openings[:1] or ([t for t in texts if full_clock in
                                            ([row["clock"] for row in rows] + prose_clocks)][:1])
        if opening_evidence or cues:
            kind = "opening_footage"
            reasons.append("%s s in front of the game that starts at %s, while the previous game ended "
                           "%s s ago, on an opening screen: %s" % (
                               gap_before, round(next_game["start"], 3), gap_after,
                               (", ".join(cues) + " banner") if not opening_evidence
                               else opening_evidence[0][:150]))
    if kind == "unresolved" and not falling and (ends_on_rows or transitions or conclusions) \
            and gap_after is not None and gap_after <= 6.0:
        kind = "post_conclusion_animation"
        reasons.append("%d row(s) carry an end marker and %d a result transition, %s s after the game "
                       "the tracker ended at %s; the digits %s" % (
                           len(ends_on_rows), len(transitions), gap_after,
                           round(prev_game["end"], 3),
                           ("held at %s across the stretch (a freeze is not a round running)" % clocks[0]
                            if frozen else
                            "were never read" if not clocks else "did not run down")))
        if conclusions:
            reasons.append("and the prose describes the result sequence: %s" % conclusions[0][:150])
    if kind == "unresolved" and falling:
        kind = "unassigned_live_play"
        reasons.append("the round clock runs down across the stretch (%s -> %s) with live combat on every "
                       "row, and no boundary explains it: the previous game ended %s s before, the next "
                       "starts %s s after" % (series[0], series[-1], gap_after, gap_before))
        if clocks and not prose_clocks == []:
            reasons.append("and only the prose states the later readings, so the tracker held no series")
    if kind == "unresolved":
        reasons.append("no rule decides it: gap after the previous game %s, gap before the next %s, "
                       "clock readings %s, prose-only clocks %s, end markers %d, transitions %d, cues %s"
                       % (gap_after, gap_before, clocks or "none", prose_clocks or "none",
                          len(ends_on_rows), len(transitions), cues or "none"))
    return {"kind": kind, "why": reasons, "gap_after_previous_game_seconds": gap_after,
            "gap_before_next_game_seconds": gap_before,
            "previous_game": None if prev_game is None else round(prev_game["end"], 3),
            "next_game": None if next_game is None else round(next_game["start"], 3),
            "clock_read_on_rows": clocks, "clock_claimed_by_prose_only": prose_clocks,
            "opening_cues": cues, "end_markers_on_rows": [r["round_end"] for r in ends_on_rows],
            "result_transition_on_rows": len(transitions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--frame-manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--gap", type=float, default=10.0)
    parser.add_argument("--header-responses", default=None,
                        help="band answer directories, the same four the coverage audit reads. They do "
                             "not move a boundary; they decide whether a concluded game is credited to a "
                             "person, which is what the clip list and the set layer's status are built on.")
    parser.add_argument("--header-ledger", default=None)
    args = parser.parse_args()

    evidence = be.evidence(None, facts=args.timeline)
    header = (bh.saved_readings(bh.response_directories(args.header_responses), args.header_ledger)
              if args.header_responses else None)
    tracked = bg.track_games(evidence["observations"], games_per_set=None, header=header)
    games, rounds = tracked["games"], tracked["rounds"]
    rows = sorted(evidence["row_records"], key=lambda r: r["timestamp"])
    nearest = frame_lookup(args.frame_manifest)

    def contains(row, game):
        return game["start"] <= row["timestamp"] <= (game["end"] or game["start"])

    groups = []
    for row in rows:
        if row.get("state") != "gameplay" or any(contains(row, g) for g in games):
            continue
        detail = bg.parse_round_facts(dict(row))
        item = {"at": round(row["timestamp"], 3), "state": row.get("state"),
                "clock": detail["round_clock"], "cue": detail["round_open_cue"],
                "call": detail["round_call"], "round_end": detail["round_end"],
                "transition": detail["result_transition"], "dots": detail["win_dots"],
                "negated": detail["prose_negated"],
                "evidence": (row.get("evidence") or ""), **nearest(row["timestamp"])}
        if groups and item["at"] - groups[-1][-1]["at"] <= args.gap:
            groups[-1].append(item)
        else:
            groups.append([item])

    # A row outside every detected game is not yet a row of omitted gameplay. Two questions have to be
    # asked first, and neither of them is answerable from the game list alone:
    #
    #   is it inside the clip that would be exported?  A cut runs from a set's start to ``clip_end_at``,
    #      which is the last conclusion plus padding, so the seconds after a game's own end are inside
    #      exported footage by construction. Batch reads at +2 s land there: they are not lost play, they
    #      are the tail the clip already carries.
    #   is the nearest conclusion a bound rather than an observation?  A game whose end was inferred (the
    #      clock fell, the next round opens) has an uncertain end by its own admission, and a row sitting
    #      just past that bound may belong to it. Calling such a row unassigned would turn the tracker's
    #      stated uncertainty into a finding about the tape.
    #
    # What survives both questions is reported as unassigned play and kept with its rows: that residue is
    # the actual gap in the games, and it is the number worth watching.
    source = bg.source_bounds(manifest=args.frame_manifest)
    # The same three-pass adjudication the coverage audit and the report run, so the clip list this account
    # measures against cannot disagree with the one the published numbers came from.
    recovered = bi.adjudicate(evidence, evidence["observations"], header=header,
                              games_per_set=None, source=source)["recovery"]
    clips = [(float(c["start"]), float(c.get("clip_end_at") or c["end"]), float(c["end"]))
             for c in (recovered.get("candidates") or [])]

    def placement(stretch):
        low, high = stretch["from"], stretch["to"]
        inside = [clip for clip in clips if clip[0] - 0.5 <= low and high <= max(clip[1], clip[2]) + 0.5]
        if inside:
            return "exported_with_the_clip", {"clip_start": round(inside[0][0], 6),
                                              "clip_end_at": round(inside[0][1], 6)}
        after = [game for game in games if game.get("end") and 0 <= low - float(game["end"]) <= 12.0]
        if after:
            parent = max(after, key=lambda game: float(game["end"]))
            if parent.get("end_uncertainty") or parent.get("boundary_certain") is False:
                return "next_to_an_unobserved_conclusion", {
                    "game": [round(float(parent["start"]), 6), round(float(parent["end"]), 6)],
                    "end_basis": parent.get("end_basis"),
                    "because": ((parent.get("end_uncertainty") or {}).get("cause")
                                or "the game's end is a bound, not an observation")}
        return "unassigned_play", {}

    items = []
    for number, group in enumerate(groups, start=1):
        stretch = {"id": "stretch-%02d" % number, "rows": group,
                   "from": group[0]["at"], "to": group[-1]["at"],
                   "seconds": round(group[-1]["at"] - group[0]["at"], 3)}
        verdict = classify(stretch, games, rounds)
        where, detail = placement(stretch)
        items.append({"id": stretch["id"], "from": stretch["from"], "to": stretch["to"],
                      "rows": len(group), "seconds": stretch["seconds"],
                      "frames": [r["frame"] for r in group], "placement": where,
                      "placement_evidence": detail, **verdict,
                      "rows_detail": group})

    by_placement = {}
    for item in items:
        bucket = by_placement.setdefault(item["placement"], {"stretches": 0, "rows": 0, "seconds": 0.0})
        bucket["stretches"] += 1
        bucket["rows"] += len(item["rows_detail"])
        bucket["seconds"] = round(bucket["seconds"] + item["seconds"], 3)

    tally = {kind: {"stretches": sum(1 for i in items if i["kind"] == kind),
                    "rows": sum(len(i["rows_detail"]) for i in items if i["kind"] == kind),
                    "seconds": round(sum(i["seconds"] for i in items if i["kind"] == kind), 3)}
             for kind in KINDS}
    report = {"inference_sent": False, "basis": "canonical_pip_merge",
              "detected_games": len(games), "detected_rounds": len(rounds),
              "unplaced_play_rows": sum(len(i["rows_detail"]) for i in items),
              "stretches": len(items), "by_kind": tally,
              "header_responses": sorted(args.header_responses.split(",")) if args.header_responses
              else [],
              "by_placement": by_placement,
              "placement_note": ("rows outside every detected game, sorted by what the recording already "
                                 "says about them: exported with an existing clip, adjacent to a "
                                 "conclusion the tracker itself admits it never observed, or genuinely "
                                 "unassigned. Only the last kind is a gap in the games."),
              "note": ("Counts here are what the detector reported and how its own evidence explains "
                       "them. None of it is independent verification that the recording holds this many "
                       "games or that nothing was missed."),
              "items": items}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("%d stretches, %d rows, %.1f s of play. By kind: %s" % (
        len(items), report["unplaced_play_rows"], sum(i["seconds"] for i in items),
        json.dumps({k: (v["stretches"], v["rows"]) for k, v in tally.items()})))
    print("By placement: %s" % json.dumps({k: (v["stretches"], v["rows"])
                                           for k, v in sorted(by_placement.items())}))
    for item in items:
        if item["placement"] == "unassigned_play":
            print("  UNASSIGNED stretch %s: %.3f-%.3f, %d rows, kind=%s" % (
                item["id"], item["from"], item["to"], len(item["rows_detail"]), item["kind"]))
    for item in items:
        print('\n%s %9.3f-%9.3f rows=%d -> %s' % (item['id'], item['from'], item['to'], item['rows'],
                                                  item['kind']))
        for reason in item['why']:
            print('     · %s' % reason)
    print('\nwrote %s' % args.out)


if __name__ == '__main__':
    main()
