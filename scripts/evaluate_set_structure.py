#!/usr/bin/env python3
"""Grade a set trial's structure: every grouping, the temporal verdict beside the display column.

The trial's transcription score (did each answer match the label for its own frame) comes from
``evaluate_consecutive_game_trial.py``. This is the other half, and it is deliberately not a score: a
grouping of the tracked games is graded against the inspected labels and the recording's own sequence, and
the report keeps every grouping that survives. It cannot publish one answer while more than one is left
open, and it cannot let the scoreboard column or a menu screen decide a set -- the column is measured
moving tens of seconds after the round that decided it, and this recording has an identity change inside a
running round.

Reads only files that already exist: nothing here sends a request, and nothing here opens the runtime
annotation file.

    env PYTHONPATH=. .venv/bin/python scripts/evaluate_set_structure.py \\
        --labels build/work/bb9/unseen-set-structure-v1/labels-eval-frozen.json \\
        --games 2402.399874:2467.464870,2493.490869:2603.600863,\\
2771.768854:2880.877849,2891.888848:3050.046840 \\
        --recovery build/work/bb9/public-recovery-integration-v1/cli-2400-3200-noformat.json \\
        --out build/work/bb9/unseen-set-structure-v1/set-structure-evaluation.json

``--games`` is a list of ``start:end`` spans in time order, and it is an argument rather than a lookup on
purpose: the grouping under test has to be able to be somebody else's, including wrong. ``--recovery``
supplies the pipeline's **own** edge proposals -- which cut it drew and on what family -- and they are
graded alongside the labels, never instead of them: an edge the detector proposed cannot contradict a
grouping, because that would let the trial mark its own hypothesis correct by construction. Independent
temporal evidence is a caller's input, not this file's. Without ``--recovery`` nothing is proposed and
nothing is contradicted, which is itself the honest report. A bracket target may be supplied with
``--target`` and stays optional: with none, no score and no winner is published, and no closure is vetoed.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_structure as bstruct       # noqa: E402


def parse_games(spec):
    # type: (str) -> list
    games = []
    for item in [part for part in spec.replace(" ", "").split(",") if part]:
        start, _, end = item.partition(":")
        if not _ or float(end) <= float(start):
            raise SystemExit("game span {!r} is not start:end with end after start".format(item))
        games.append({"start": float(start), "end": float(end)})
    return sorted(games, key=lambda game: game["start"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", required=True,
                        help="Frozen evaluator labels (the file with the inspected pip rows and bands).")
    parser.add_argument("--games", required=True, help="start:end spans in time order, comma separated.")
    parser.add_argument("--recovery", default=None,
                        help="A saved recovery report whose candidate edges carry the temporal verdicts.")
    parser.add_argument("--target", type=int, default=None,
                        help="Games needed to win a set. Optional: with none, no score or winner exists.")
    parser.add_argument("--rounds-per-game", type=int, default=3)
    parser.add_argument("--transcription", default=None,
                        help="Optional saved transcription score, reported beside the structure.")
    parser.add_argument("--out", default=None, help="Where to write the report. Default: stdout only.")
    args = parser.parse_args(argv)

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if not labels.get("inspected"):
        raise SystemExit("{} is a frozen selection, not inspected truth: the labels were never read by a "
                         "person, so there is nothing to grade against.".format(args.labels))
    games = parse_games(args.games)
    boundaries = {}
    if args.recovery:
        report = json.loads(Path(args.recovery).read_text(encoding="utf-8"))
        boundaries = bstruct.boundaries_from_recovery(report, games)
    transcription = json.loads(Path(args.transcription).read_text(encoding="utf-8")) \
        if args.transcription else None
    result = bstruct.evaluate(labels, games, boundaries=boundaries, target=args.target,
                              rounds_per_game=args.rounds_per_game, transcription=transcription)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"kind": result["kind"], "games": len(games),
                      "groupings_enumerated": result["groupings_enumerated"],
                      "exhaustive_over_the_space": result["groupings_exhaustive_over_the_space"],
                      "surviving": result["surviving"], "supported": result["supported"],
                      "contradicted": len(result["contradicted"]),
                      "clashes": [clash["boundary"] for clash in result["clashes"]],
                      "unresolved": result["unresolved"],
                      "one_answer_published": result["one_answer_published"],
                      "format_supplied": result["format_supplied"],
                      "labels": result["labels"],
                      "written": args.out}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
