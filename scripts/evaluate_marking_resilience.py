#!/usr/bin/env python3
"""Resilience report: does set recovery survive deliberately bad live markings?

Markings are the least trustworthy input this pipeline has -- made live, from memory, while a bracket
runs. This command recovers the same timeline under each marking fault and checks the invariants that
matter: the boundaries must not move, no confident gameplay may disappear, and every mark must be
accounted for as anchored, late or unanchored.

    .venv/bin/python scripts/evaluate_marking_resilience.py \\
        --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \\
        --marks build/work/bb9/oct22.tvh.json --duration 6928.421136

Reads only. Makes no model or network calls.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_marks  # noqa: E402


def load_marks(path):
    # type: (Path) -> list
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("matches", data) if isinstance(data, dict) else data
    marks = [r.get("start", r.get("timestamp")) for r in rows if isinstance(r, dict)]
    return [float(m) for m in marks if isinstance(m, (int, float)) and not isinstance(m, bool)]


def faults(marks, duration, seed=7):
    # type: (list, float, int) -> list
    """The marking errors a live operator actually produces, plus combinations."""
    rng = random.Random(seed)
    late = [m + 240.0 for m in marks]
    early = [max(0.0, m - 300.0) for m in marks]
    inside = marks and [m + 90.0 for m in marks] or []
    return [
        ("none: every match unmarked", []),
        ("faithful", list(marks)),
        ("shifted late (+240s)", late),
        ("shifted early (-300s)", early),
        ("mid-set marks", inside),
        ("duplicated x3", [m for m in marks for _ in range(3)]),
        ("halved (random 50% dropped)", [m for m in marks if rng.random() < 0.5]),
        ("reversed order", list(reversed(marks))),
        ("one bogus mark far from play", list(marks) + [duration * 1.5 + 1000.0]),
        ("all marks inside the first set", [marks[0] + d for d in (5, 30, 90, 200)] if marks else []),
    ]


def boundaries(result):
    return [(round(s["start"], 3), round(s["end"], 3)) for s in result["sets"]]


def corrupt_names(rows, kind):
    # type: (list, str) -> list
    """Reproduce how broadcast names are actually misread: dropped prefixes, dropped letters."""
    out = []
    for source in rows:
        copy = dict(source)
        for key in ("player1", "player2"):
            value = copy.get(key)
            if not isinstance(value, str) or not value:
                continue
            if kind == "prefix":
                copy[key] = value.split("|")[-1].strip()
            elif kind == "letters":
                copy[key] = value[:max(2, len(value) - 3)]
            elif kind == "flicker":
                # Alternate readings, the way one tag appeared as both Spitfire2929 and Spire2929.
                copy[key] = value if out and len(out) % 2 else value.replace("a", "").replace("i", "")
            elif kind == "blank":
                copy[key] = ""
        out.append(copy)
    return out


def corrupt_scores(rows, kind):
    # type: (list, str) -> list
    out = []
    for source in rows:
        copy = dict(source)
        if kind == "unreadable":
            copy["score1"] = copy["score2"] = None
        elif kind == "rounds":
            copy["score1"], copy["score2"] = 1, 1
        out.append(copy)
    return out


def lost_play(result):
    # type: (dict) -> int
    return (result["coverage"]["gameplay_rows"] - result["coverage"]["rows_inside_sets"]
            - sum(r["gameplay_rows"] for r in result["residuals"]))


def moved_boundaries(result, expected):
    # type: (dict, list) -> int
    got = boundaries(result)
    return sum(1 for a, b in zip(expected, got) if a != b) + abs(len(expected) - len(got))


def _ok(result, expected):
    # type: (dict, list) -> bool
    return (moved_boundaries(result, expected) == 0 and lost_play(result) == 0
            and result["coverage"]["fraction_accounted"] == 1.0)


def report(label, result, expected, total_rows):
    # type: (str, dict, list, int) -> None
    print("%-36s %6d %8d %10d %8d %9d  %s" % (
        label, len(result["sets"]), moved_boundaries(result, expected), lost_play(result),
        sum(1 for m in result["marks"] if m.get("status") in ("anchored", "nearest_set")),
        sum(1 for m in result["marks"] if m.get("status") == "unanchored"),
        "PASS" if _ok(result, expected) else "FAIL"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", required=True, help="Classified timeline JSON")
    parser.add_argument("--marks", default="", help="Marked matches JSON used as approximate anchors")
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--pad-before", dest="pad_before", type=float, default=0.0)
    parser.add_argument("--pad-after", dest="pad_after", type=float, default=0.0)
    parser.add_argument("--split-on-names", dest="split_on_names", default="corroborate",
                        choices=("corroborate", "always", "never"),
                        help="How much weight the broadcast pairing carries (default corroborate)")
    args = parser.parse_args(argv)

    rows = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    marks = load_marks(args.marks) if args.marks else []
    duration = args.duration or (rows[-1]["timestamp"] if rows else 0.0)

    def run(timeline, given_marks):
        return boundary_marks.recover_sets(timeline, marks=given_marks, duration=duration,
                                           pad_before=args.pad_before, pad_after=args.pad_after,
                                           split_on_names=args.split_on_names)

    clean = run(rows, marks)
    expected = boundaries(clean)
    print("timeline: %d rows | %d marks | %d sets recovered | play rows accounted %.1f%% | split_on_names=%s"
          % (len(rows), len(marks), len(clean["sets"]),
             clean["coverage"]["fraction_accounted"] * 100, args.split_on_names))

    print("\n%-36s %6s %8s %10s %8s %9s  %s" % ("marking fault", "sets", "moved", "lost play",
                                                "anchored", "unanchored", "verdict"))
    marking_cases = [(label, run(rows, given)) for label, given in faults(marks, duration)]
    for label, result in marking_cases:
        report(label, result, expected, clean["coverage"]["gameplay_rows"])

    evidence_cases = [(label, run(mutated, marks)) for label, mutated in (
        ("names lose their prefix", corrupt_names(rows, "prefix")),
        ("names lose letters", corrupt_names(rows, "letters")),
        ("names flicker between readings", corrupt_names(rows, "flicker")),
        ("names blanked entirely", corrupt_names(rows, "blank")),
        ("scores unreadable", corrupt_scores(rows, "unreadable")),
        ("scores read as constant 1-1", corrupt_scores(rows, "rounds")))]
    print("\n%-36s %6s %8s %10s %9s  %s" % ("evidence fault", "sets", "drift", "lost play",
                                            "coverage", "verdict"))
    drift_of = {}
    for label, result in evidence_cases:
        # Play may never be lost, and that is the promise this section can keep. Set *counting*
        # depends on reading who is playing, so name damage is reported as measured drift rather than
        # passed off as harmless: the footage cannot tell two players apart once its names are gone.
        drift_of[label] = moved_boundaries(result, expected)
        print("%-36s %6d %8d %10d %8.1f%%  %s" % (
            label, len(result["sets"]), drift_of[label], lost_play(result),
            result["coverage"]["fraction_accounted"] * 100,
            "DEGRADED" if drift_of[label] else "PASS"))

    marking_failures = sum(1 for _, result in marking_cases if not _ok(result, expected))
    # Only the promise that still holds under name damage: no play may be lost, and every row must
    # remain accounted for inside a set or a named residual. Boundary invariance is deliberately not
    # claimed here -- reading who is playing is exactly what the name field is for, so corrupting it
    # moves boundaries, and the table above reports how many.
    evidence_failures = sum(1 for _, result in evidence_cases
                            if lost_play(result) or result["coverage"]["fraction_accounted"] != 1.0
                            or result["overlaps"])
    # Counted from the same numbers the table printed, against the same baseline. Comparing against a
    # different reference made this line contradict its own rows.
    name_drift = sum(1 for label in drift_of if drift_of[label])
    print("\npartial sets preserved: %d of %d | truncated edges: %d | hard cuts: %d" % (
        sum(1 for s in clean["sets"] if s["partial"]), len(clean["sets"]),
        sum(1 for s in clean["sets"] for v in s["truncated"].values() if v),
        len(clean["hard_cuts"])))
    for span in clean["sets"]:
        print("  %9.1f -> %9.1f  rows=%-4d %-30s start=%-13s end=%-22s partial=%s" % (
            span["start"], span["end"], span["gameplay_rows"],
            " vs ".join("|".join(p) for p in span["pairings"])[:30],
            span["start_evidence"], span["end_evidence"], span["partial"] or "-"))
    for residual in clean["residuals"]:
        print("  UNASSIGNED %9.1f -> %9.1f rows=%d %s" % (residual["start"], residual["end"],
                                                          residual["gameplay_rows"], residual["reason"]))
    print("\nmarkings: %d/%d faults handled with no boundary moved and no play lost" % (
        len(marking_cases) - marking_failures, len(marking_cases)))
    print("evidence: %d/%d faults kept every play row accounted for; %d of %d moved a boundary "
          "(reported above, not forgiven)" % (
              len(evidence_cases) - evidence_failures, len(evidence_cases),
              name_drift, len(evidence_cases)))
    print("coverage is a property of the partition, not accuracy: no set count here is validated "
          "against truth by this harness.")
    return 1 if (marking_failures + evidence_failures) else 0


if __name__ == "__main__":
    sys.exit(main())
