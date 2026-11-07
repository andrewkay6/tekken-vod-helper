"""Comparing broadcast names that were read off a 640-wide frame.

The same player is read as `Spitfire2929` in one frame and `Spire2929` in the next, and `PWDDA | Ariz`
loses letters whenever the overlay is mid-animation. Equality on those strings invents pairings, and a
pairing change is what both the window splitter and the set recovery use to decide that a new match
began, so a spelling wobble silently turns one match into several. Two names are treated as the same
player above a fixed similarity, measured on BB9 where OCR drift ran at 0.857 and real opponents never
exceeded 0.5.

Unreadable names compare equal on purpose: no evidence is not evidence of a change.
"""
from difflib import SequenceMatcher
from typing import Any, Dict, FrozenSet, Optional, Tuple

SAME_NAME_RATIO = 0.85


def normalise_name(value):
    # type: (Any) -> str
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value.casefold() if character.isalnum())


def names_close(left, right, threshold=SAME_NAME_RATIO):
    # type: (str, str, float) -> bool
    if left == right:
        return True
    if not left or not right:
        return False
    # The overlay sometimes renders only part of a tag ("PWDDA | Ariz" as "Ariz"), which similarity
    # alone reads as a different player because the shared letters are a small fraction of the longer
    # string. A name swallowed whole by the other is a truncation, not an opponent.
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 3 and shorter in longer:
        return True
    return SequenceMatcher(None, left, right).ratio() >= threshold


def pair_of(row, first="player1", second="player2"):
    # type: (Dict[str, Any], str, str) -> Optional[Tuple[str, str]]
    """The two names a header reports, normalised, or None if either side is unreadable."""
    left, right = normalise_name(row.get(first)), normalise_name(row.get(second))
    if not left or not right:
        return None
    return (left, right)


def same_pair(one, other):
    # type: (Optional[Tuple[str, str]], Optional[Tuple[str, str]]) -> bool
    if one is None or other is None:
        return True
    if len(one) != len(other):
        return False
    return all(any(names_close(a, b) for b in other) for a in one)


def pair_set(pair):
    # type: (Optional[Tuple[str, str]]) -> FrozenSet[str]
    return frozenset(pair) if pair else frozenset()


def cluster_names(rows, key=("player1", "player2")):
    # type: (Sequence[Dict[str, Any]], Tuple[str, str]) -> Dict[str, str]
    """Map every spelling seen in the footage onto one label per player.

    Names are a rough estimate, so the unit of comparison should not be the string. Reading each frame
    independently makes one player look like several (`Spitfire2929`, `Spire2929`) and lets a single
    misread frame split a match in two. Clustering first means a pairing change means a change of
    *player*, not a change of spelling.

    Members are merged when they are similar enough to the cluster's **representative** -- the most
    frequently read spelling -- or one contains the other. Comparing only to the representative is what
    makes this stable under heavy corruption: requiring closeness to every member means one badly
    mangled variant splits a player into two clusters, and the pairing then flickers between them. It
    also avoids chaining player A into player B through a rare shared substring, because the
    representative is the reading the footage agreed on most often.
    """
    counts = {}  # type: Dict[str, int]
    for row in rows:
        for name_key in key:
            name = normalise_name(row.get(name_key))
            if name:
                counts[name] = counts.get(name, 0) + 1

    representatives = []  # type: List[str]
    assigned = {}  # type: Dict[str, str]
    for name in sorted(counts, key=lambda n: (-counts[n], n)):
        for representative in representatives:
            if name == representative or names_close(name, representative):
                assigned[name] = representative
                break
        else:
            representatives.append(name)
            assigned[name] = name
    return assigned


def canonicalise_rows(rows, key=("player1", "player2")):
    # type: (Sequence[Dict[str, Any]], Tuple[str, str]) -> List[Dict[str, Any]]
    """Copy `rows` with every player name replaced by its cluster label."""
    mapping = cluster_names(rows, key)
    out = []
    for row in rows:
        copy = dict(row)
        for name_key in key:
            name = normalise_name(row.get(name_key))
            if name and name in mapping:
                copy[name_key] = mapping[name]
        out.append(copy)
    return out
