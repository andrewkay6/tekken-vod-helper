"""Per-set character verdicts from the analysis's per-frame reads, with spots to check when unsure.

A player's character reads across a set's gameplay frames fall into one of three shapes:

* ``single``  -- one character, apart from at most a few stray reads. Confident; nothing to ask.
* ``switch``  -- two solid blocks, each at least a game long, changing once. A real mid-set switch (BB11's
  Grand Final: Nina for games 1-2, Anna after). The slot becomes "A / B" in the order played.
* ``unsure``  -- anything else (scattered minority reads, three or more blocks, a short block mid-set). The
  dominant character is kept and every doubtful stretch becomes a spot for the reviewer to jump to.

Reads in the first ``settle_s`` seconds of a set are not trusted on their own: the scoreboard's sides often
lag the new set (BB11 set 10 read andy's Dragunov on googy's side for 8 frames), so a short block there is
ignored rather than treated as a switch.
"""
import difflib
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: A block shorter than this many frames is noise, never a game.
MIN_BLOCK_FRAMES = 25
#: Stray reads tolerated before a single-character set counts as unsure (fraction of the set's reads).
STRAY_FRACTION = 0.04
SETTLE_S = 45.0


@lru_cache(maxsize=1)
def _roster() -> Dict[str, str]:
    path = Path(__file__).with_name("characters.txt")
    names = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return {n.lower(): n for n in names}


def _norm(name: Optional[str]) -> str:
    """Snap an OCR read to the roster ("Dracunov" -> "Dragunov", "Hwaorang" -> "Hwoarang")."""
    raw = " ".join((name or "").replace("_", " ").split())
    if not raw:
        return ""
    roster = _roster()
    if raw.lower() in roster:
        return roster[raw.lower()]
    close = difflib.get_close_matches(raw.lower(), list(roster), n=1, cutoff=0.75)
    return roster[close[0]] if close else raw.title()


def runs_for(frames: Sequence[Dict], player: str, aliases: Sequence[str] = ()) -> List[Dict]:
    """Contiguous runs of one character for ``player`` across gameplay frames, in time order."""
    wanted = {player.strip().lower()} | {a.strip().lower() for a in aliases}
    out: List[Dict] = []
    for e in sorted(frames, key=lambda f: f["timestamp"]):
        if e.get("state") != "gameplay":
            continue
        side = next((s for s in (1, 2) if (e.get("player%d" % s) or "").strip().lower() in wanted), None)
        if side is None:
            continue
        ch = _norm(e.get("character%d" % side))
        if not ch:
            continue
        if out and out[-1]["character"] == ch:
            out[-1]["end"] = e["timestamp"]
            out[-1]["frames"] += 1
        else:
            out.append({"character": ch, "start": e["timestamp"], "end": e["timestamp"], "frames": 1})
    return out


def _merge(runs: List[Dict]) -> List[Dict]:
    merged: List[Dict] = []
    for r in runs:
        if merged and merged[-1]["character"] == r["character"]:
            merged[-1]["end"] = r["end"]
            merged[-1]["frames"] += r["frames"]
        else:
            merged.append(dict(r))
    return merged


def verdict(runs: List[Dict], set_start: float, settle_s: float = SETTLE_S,
            min_block: int = MIN_BLOCK_FRAMES, stray_fraction: float = STRAY_FRACTION) -> Dict:
    """Classify one player's character reads for one set. ``spots`` lists times worth checking."""
    if not runs:
        return {"kind": "unknown", "character": "", "spots": [], "counts": {}}
    # Short blocks while the scoreboard settles at the start of a set are ignored outright.
    trusted = [r for r in runs if not (r["start"] < set_start + settle_s and r["frames"] < min_block)]
    ignored = [r for r in runs if r not in trusted]
    trusted = trusted or runs
    counts = Counter()
    for r in trusted:
        counts[r["character"]] += r["frames"]
    total = sum(counts.values())
    main, main_n = counts.most_common(1)[0]
    blocks = _merge([r for r in trusted if r["frames"] >= min_block]) or _merge(trusted)
    strays = [r for r in trusted if r["character"] != main and r["frames"] < min_block]
    base = {"counts": dict(counts), "ignored_at_start": [r["character"] for r in ignored]}

    if len(counts) == 1 or (total - main_n) <= max(2, stray_fraction * total) and len(blocks) == 1:
        return dict(base, kind="single", character=main, spots=[])

    if len(blocks) == 2 and blocks[0]["character"] != blocks[1]["character"] \
            and all(b["frames"] >= min_block for b in blocks) and not strays:
        first, second = blocks
        return dict(base, kind="switch", character="{} / {}".format(first["character"], second["character"]),
                    spots=[{"at": round(first["end"], 1), "label": "last {} read".format(first["character"])},
                           {"at": round(second["start"], 1), "label": "switch to {}".format(second["character"])}])

    spots = [{"at": round(r["start"], 1), "label": "reads {} here ({} frames)".format(r["character"], r["frames"])}
             for r in sorted((r for r in trusted if r["character"] != main), key=lambda r: -r["frames"])[:5]]
    spots.sort(key=lambda s: s["at"])
    return dict(base, kind="unsure", character=main, spots=spots)


def set_characters(frames: Sequence[Dict], start: float, end: float, player1: str, player2: str,
                   aliases: Optional[Dict[str, Sequence[str]]] = None) -> Dict[str, Dict]:
    """Verdicts for both players of the set ``[start, end]``."""
    aliases = aliases or {}
    in_set = [f for f in frames if start <= f["timestamp"] <= end]
    return {p: verdict(runs_for(in_set, p, aliases.get(p, ())), start) for p in (player1, player2)}
