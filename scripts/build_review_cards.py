"""Build the review page's set cards from a TVH detect run (the BB11 procedure, as one step).

Inputs: a `boundary_cli detect` output dir, the master to stream, and optionally the start.gg bracket.
For every set it fills in:

  * times, players and characters from the analysis (`project.tvh.json`), plus the sets two analysis windows
    disagreed on (`conflicting_candidates` in review.json), with both start options explained on the card;
  * player names cleaned up: OCR variants are folded into the bracket's spelling (or --alias);
  * the round from the start.gg bracket, matched by player pair (a pair that met twice gets Winners first);
  * characters from `character_runs`: confident -> filled in; a clean mid-set switch -> "A / B" in play order;
    anything doubtful -> the main character plus "Check here" jump spots on the card.

    .venv/bin/python scripts/build_review_cards.py --detect build/work/bb12/detect-<date> \
        --master "/var/lib/ai-sandbox-media/<event>/<event> [1440p].mp4" --event "Basement Brawl #12" \
        --bracket build/work/bb12/startgg-sets.json --out build/work/bb12/web-review-<date>

Then serve it with prototypes/mobile-review/server.py --manifest <out>/cards.manifest.json --notes <out>/owner-notes.json.
"""
import argparse
import copy
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tekken_vod_helper.character_runs import set_characters  # noqa: E402

ROUNDS = ["Winners Round 1", "Winners Round 2", "Winners Quarter-Final", "Winners Semi-Final", "Winners Final",
          "Losers Round 1", "Losers Round 2", "Losers Round 3", "Losers Round 4", "Losers Quarter-Final",
          "Losers Semi-Final", "Losers Final", "Grand Final", "Grand Final Reset"]


def fmt(s):
    s = float(s)
    h, m, sec = int(s // 3600), int(s % 3600 // 60), s % 60
    return "{}:{:02d}:{:04.1f}".format(h, m, sec) if h else "{:02d}:{:04.1f}".format(m, sec)


def canon_names(raw_names, bracket_names, aliases):
    """Map every name the OCR produced onto one spelling (bracket spelling when it's close)."""
    out = {}
    for n in raw_names:
        if n in aliases:
            out[n] = aliases[n]
            continue
        close = difflib.get_close_matches(n.lower(), [b.lower() for b in bracket_names], n=1, cutoff=0.8)
        out[n] = next((b for b in bracket_names if b.lower() == close[0]), n) if close else n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", required=True, type=Path)
    ap.add_argument("--master", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bracket", type=Path, help="start.gg sets JSON (round_name, player1, player2)")
    ap.add_argument("--alias", action="append", default=[], help='"OCR name=Real name" (repeatable)')
    ap.add_argument("--template", type=Path, default=ROOT / "build/work/bb10/web-review-2026-11-13/bb10-cards.manifest.json",
                    help="earlier manifest supplying the character/round lists")
    args = ap.parse_args()
    aliases = dict(a.split("=", 1) for a in args.alias)
    args.out.mkdir(parents=True, exist_ok=True)

    det = json.loads((args.detect / "project.tvh.json").read_text())
    review = json.loads((args.detect / "review.json").read_text())
    timeline = sorted(json.loads((args.detect / "timeline-refined.json").read_text()), key=lambda e: e["timestamp"])
    bracket = json.loads(args.bracket.read_text()) if args.bracket else []
    duration = float(det.get("duration") or timeline[-1]["timestamp"])

    sets = [dict(start=m["start"], end=m["end"], player1=m["player1"], player2=m["player2"],
                 character1=m.get("character1") or "", character2=m.get("character2") or "", notes=[])
            for m in det["matches"]]
    # Sets two windows disagreed on: neither proposal was published, so offer the earlier start and explain.
    for item in review.get("items", []):
        if item.get("type") != "conflicting_candidates":
            continue
        a, b = item["first"], item["second"]
        ids = sorted({a["start_id"], b["start_id"]}, key=lambda i: int(i.split("_")[1]))
        starts = [timeline[int(i.split("_")[1])] for i in ids]
        end = timeline[int(a["end_id"].split("_")[1])]
        sets.append(dict(start=starts[0]["timestamp"], end=end["timestamp"], player1=starts[0].get("player1") or "",
                         player2=starts[0].get("player2") or "", character1="", character2="",
                         notes=["Two analysis windows disagreed on the start: {}. The end is the last read at {}. "
                                "Pick the start in the middle of the stage select.".format(
                                    " or ".join(fmt(s["timestamp"]) for s in starts), fmt(end["timestamp"]))]))
    sets.sort(key=lambda s: s["start"])

    bracket_names = sorted({p for s in bracket for p in (s["player1"], s["player2"]) if p})
    names = canon_names({p for s in sets for p in (s["player1"], s["player2"]) if p}, bracket_names, aliases)
    reverse_alias = {}
    for raw, real in names.items():
        reverse_alias.setdefault(real, []).append(raw)
    by_pair = {}
    for s in bracket:
        by_pair.setdefault(frozenset((s["player1"].lower(), s["player2"].lower())), []).append(s["round_name"])
    used_pair = {}

    queue, jobs, matches = [], [], []
    for i, s in enumerate(sets, 1):
        p1, p2 = names.get(s["player1"], s["player1"]), names.get(s["player2"], s["player2"])
        pair = frozenset((p1.lower(), p2.lower()))
        rounds = sorted(by_pair.get(pair, []), key=lambda r: (r.startswith("Losers"), ROUNDS.index(r) if r in ROUNDS else 99))
        k = used_pair.get(pair, 0)
        used_pair[pair] = k + 1
        round_name = rounds[k] if k < len(rounds) else ""
        chars = set_characters(timeline, s["start"], s["end"], p1, p2,
                               {p1: reverse_alias.get(p1, []), p2: reverse_alias.get(p2, [])})
        c1, c2 = chars[p1], chars[p2]
        spots, notes = [], list(s["notes"])
        for player, v in ((p1, c1), (p2, c2)):
            if v["kind"] == "switch":
                notes.append("{} switched characters mid-set: {} (in play order). Check the jump spots.".format(
                    player, v["character"]))
            elif v["kind"] == "unsure":
                notes.append("{}'s character isn't certain ({}). Check the jump spots.".format(
                    player, ", ".join("{} {}".format(n, c) for c, n in v["counts"].items())))
            spots += [dict(sp, label="{}: {}".format(player, sp["label"])) for sp in v["spots"]]
        fields = {"start": round(s["start"], 3), "end": round(s["end"], 3), "player1": p1, "player2": p2,
                  "character1": c1["character"] or s["character1"], "character2": c2["character"] or s["character2"],
                  "round_name": round_name}
        jobs.append({"number": i, "path": args.master, "requested_start": fields["start"],
                     "requested_end": fields["end"], "status": "draft"})
        matches.append(dict(fields, notes=""))
        queue.append({
            "id": "set{:02d}-card".format(i), "clip_id": "set{:02d}".format(i), "kind": "set",
            "title": "Set {}".format(i) + (" (check)" if notes else ""), "source_at": fields["start"],
            "range": [round(max(0, s["start"] - 15), 2), round(min(duration, s["end"] + 15), 2)],
            "question": "Check each field and fix anything that's wrong, then save. The start goes in the middle of the stage select.",
            "observations": " ".join(notes) or "No open questions: times, names and characters from the analysis, "
                                                "round from start.gg. Just confirm.",
            "fields": fields, "spots": sorted(spots, key=lambda sp: sp["at"]),
            "sources": {"start": "detected", "end": "detected", "players": "scoreboard, names from start.gg",
                        "round_name": "start.gg bracket" if round_name else "not known"},
            "note_prompt": "Anything else about this set (optional)"})

    gaps, t = [], 0.0
    for s in sets:
        if s["start"] - t > 60:
            gaps.append("{}–{}".format(fmt(t), fmt(s["start"])))
        t = max(t, s["end"])
    if duration - t > 60:
        gaps.append("{}–{}".format(fmt(t), fmt(duration)))
    queue.append({"id": "missing-sets", "clip_id": "", "title": "Any sets missing?", "source_at": 0,
                  "question": "These {} cards cover the gameplay the analysis found. Was any other set played on stream "
                              "that isn't here?".format(len(sets)),
                  "observations": "Unclaimed stretches over a minute: " + (", ".join(gaps) or "none") + ".",
                  "choices": [{"id": "none", "label": "No, these are all the on-stream sets"},
                              {"id": "missing", "label": "Yes, a set is missing",
                               "mark": "Pause at its start and mark it (list any others in the note)"},
                              {"id": "unsure", "label": "Not sure"}],
                  "note_prompt": "Which set(s), and roughly when?"})

    proj = copy.deepcopy(det)
    proj.update(video_path=args.master, duration=duration, matches=matches,
                players=sorted({m[k] for m in matches for k in ("player1", "player2") if m[k]}, key=str.lower))
    (args.out / "draft.tvh.json").write_text(json.dumps(proj, indent=2, ensure_ascii=False))
    lists = json.loads(args.template.read_text())["review_run"]["lists"] if args.template.exists() else {}
    lists = dict(lists, players=sorted(set(lists.get("players", [])) | set(proj["players"]) | set(bracket_names), key=str.lower),
                 rounds=lists.get("rounds", ROUNDS))
    manifest = {"source": args.master, "project": str((args.out / "draft.tvh.json").resolve()), "provisional": True,
                "operator_accepted": False, "independently_validated": False,
                "limitations": ["Times and characters from the TVH analysis; names and rounds from start.gg."],
                "jobs": jobs,
                "review_run": {"id": args.out.name, "round": 1, "artifact_sha256": "", "candidate_applied_to_host": False,
                               "summary": "Confirm each {} set: start, end, players, characters and round.".format(args.event),
                               "followup": args.out.name,
                               "footnote": "{} draft. Nothing here changes a TVH project until you confirm.".format(args.event),
                               "notes_help": "Answer each card; answers save to a separate notes file.",
                               "lists": lists, "queue": queue}}
    (args.out / "cards.manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    for q in queue[:-1]:
        f = q["fields"]
        print("{:<18} {}-{} {} ({}) vs {} ({}) | {}{}".format(
            q["title"], fmt(f["start"]), fmt(f["end"]), f["player1"], f["character1"], f["player2"], f["character2"],
            f["round_name"] or "?", "  [{} spots]".format(len(q["spots"])) if q["spots"] else ""))
    print("gaps:", ", ".join(gaps) or "none")
    print("wrote", args.out / "cards.manifest.json")


if __name__ == "__main__":
    sys.exit(main())
