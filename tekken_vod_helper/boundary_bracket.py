"""Optional event-bracket context: strong, fallible *supporting* evidence for set membership.

Why this exists. The set layer could only reach a `separated` verdict when a controller screen sat in the
gap between two games, which left a mandatory cue in place even after it was called "preferred". A bracket
report is a second route to the same question: if the event record says these two games belong to different
matches, and the recording independently shows a different pairing, that is two signals where before there
was one and a shrug. Andy's clarification (2026-10-30) is that start.gg set numbers are usually maintained
faithfully for these events, with grand finals sometimes unusual -- so the data is worth using and is not
proof. Both halves of that sentence are implemented here.

What an input bracket may and may not do:

* it **may** corroborate a boundary the recording also argues for, name the pairing it expects, state the
  format (first-to-N, grand-final reset) and say how many games a reported score implies were played;
* it **may not** decide a boundary alone, and a bracket-only disagreement with the footage is reported as
  uncertainty, never as a cut;
* its reported score **may not** be used as independent evaluation truth for the same recovered score --
  an input cannot verify the output it was fed to predict, so every record here carries
  ``independent_of_recording: False``;
* it **may not** fabricate or omit gameplay: an expectation of five games is a question when three were
  found, not three extra games.

Nothing in this module fetches anything. No endpoint, no event URL, no token. It normalises a document the
caller already has, because the bracket is optional context and the recording-only path has to stay
operational when it is missing -- which is what ``normalize(None) is None`` means, and why every caller has
to behave exactly as it did before when the answer is None.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import boundary_names

VERSION = "bracket-context-v1"

#: Where the bracket sits in a double elimination, because "Grand Final" is the one label whose set
#: structure cannot be assumed: a bracket reset means the same two players play a *second* match, so a
#: match change and an unchanged pairing are compatible there and incompatible anywhere else.
SECTIONS = ("winners", "losers", "grand_finals", "unknown")

#: What the caller said the data is. Required, because context with no provenance is indistinguishable
#: from a guess, and the whole point of recording it is to be able to discount it later.
SOURCES = ("startgg", "operator_sheet", "manual", "other")


class BracketError(ValueError):
    """A bracket document that cannot be trusted with a decision. Raised before any is taken."""


def _clean(value):
    # type: (Any) -> Optional[str]
    text = str(value or "").strip()
    return text or None


def _score(value):
    # type: (Any) -> Optional[List[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    out = []
    for side in value:
        if isinstance(side, bool) or not isinstance(side, int) or side < 0:
            return None
        out.append(side)
    return out


def normalize(raw):
    # type: (Any) -> Optional[Dict[str, Any]]
    """Coerce a supplied bracket document into the one shape the set layer reads, or None for "absent".

    ``None`` in, ``None`` out: no context is the normal case and it is never an error. A document that is
    *present* but malformed raises rather than degrading silently, because a half-parsed bracket would
    corroborate a boundary while meaning something else entirely.
    """
    if raw is None:
        return None
    if isinstance(raw, (str, Path)):
        raw = json.loads(Path(str(raw)).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BracketError("bracket context must be an object, not {}".format(type(raw).__name__))
    if raw.get("kind") == "bracket-context" and raw.get("version") == VERSION:
        return raw          # already normalized by this module; re-normalizing would drop provenance
    stated = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
    source = _clean(stated.get("source")) or _clean(raw.get("source"))
    if not source:
        raise BracketError("bracket context names no source: unattributed context cannot be discounted "
                           "later, which is the only reason to keep it")
    if source not in SOURCES:
        raise BracketError("unknown bracket source {!r}; expected one of {}".format(source, list(SOURCES)))
    provenance = stated
    matches = []  # type: List[Dict[str, Any]]
    for entry in raw.get("matches") or ():
        if not isinstance(entry, dict):
            raise BracketError("a bracket match must be an object")
        players = [_clean(p) for p in (entry.get("players") or [])]
        players = [p for p in players if p]
        if len(players) != 2:
            raise BracketError("bracket match {} needs exactly two players".format(entry.get("id")
                                                                                    or entry.get("label")))
        section = str(entry.get("section") or "unknown").strip().lower()
        if section not in SECTIONS:
            section = "unknown"
        games = _score(entry.get("games_won"))
        reported = _score(entry.get("reported_score")) if "reported_score" in entry else games
        games_per_set = entry.get("games_per_set")
        if games_per_set is not None and (isinstance(games_per_set, bool)
                                          or not isinstance(games_per_set, int) or games_per_set < 1):
            raise BracketError("bracket match {} has a games_per_set that is not a positive "
                               "integer".format(entry.get("id")))
        matches.append({
            "id": _clean(entry.get("id")) or "match-{:02d}".format(len(matches) + 1),
            "label": _clean(entry.get("label")),
            "section": section,
            "round": entry.get("round") if isinstance(entry.get("round"), int) else None,
            "players": players,
            # Normalised for comparison only: the display spellings above stay what the source said.
            "players_normalised": [boundary_names.normalise_name(p) for p in players],
            "games_won": games,
            "reported_score": reported,
            "games_per_set": games_per_set,
            # Explicit, never inferred from the score. A grand final whose format was not stated is
            # reported as unknown and buys nothing, rather than defaulting to "no reset".
            "grand_finals_reset": entry.get("grand_finals_reset")
            if isinstance(entry.get("grand_finals_reset"), bool) else None,
        })
    if not matches:
        raise BracketError("bracket context lists no matches; supply the matches you mean, or supply "
                           "nothing and stay on the recording-only path")
    return {
        "kind": "bracket-context",
        "version": VERSION,
        "provenance": {
            "source": source,
            "event": _clean(provenance.get("event") or raw.get("event")),
            "fetched_at": _clean(provenance.get("fetched_at") or raw.get("fetched_at")),
            # Whether anybody has actually checked this against anything. No bracket in this project has
            # been fetched or verified yet, and a record that implies otherwise would be a fabrication.
            "verified": bool(provenance.get("verified", raw.get("verified", False))),
            "note": _clean(provenance.get("note") or raw.get("note")),
        },
        "matches": matches,
        "independent_of_recording": False,
        "may_decide_a_boundary_alone": False,
        "supplies": ["pairing_hypotheses", "format", "expected_game_count", "match_change_agreement"],
        "never_supplies": ["a boundary without any recording evidence",
                           "independent proof of the score it reported",
                           "gameplay that the recording does not show"],
    }


def match_by_participants(context, names):
    # type: (Optional[Dict[str, Any]], Sequence[Optional[str]]) -> List[Dict[str, Any]]
    """Bracket matches whose two players are the pairing the recording showed, order-insensitive.

    A side that was never read is unknown, not a mismatch, so an unreadable name narrows nothing and a
    pairing with no bracket counterpart returns [] -- which callers must treat as "the bracket is silent",
    never as "this is not a real match".
    """
    if not context:
        return []
    seen = [boundary_names.normalise_name(n) for n in (names or ()) if n]
    out = []
    for entry in context.get("matches") or ():
        bracket = entry.get("players_normalised") or []
        if not seen:
            out.append(entry)
            continue
        if len(seen) == 1:
            if any(boundary_names.names_close(seen[0], b) for b in bracket):
                out.append(entry)
            continue
        first, second = seen[0], seen[1]
        if len(bracket) != 2:
            continue
        forward = boundary_names.names_close(first, bracket[0]) \
            and boundary_names.names_close(second, bracket[1])
        crossed = boundary_names.names_close(first, bracket[1]) \
            and boundary_names.names_close(second, bracket[0])
        if forward or crossed:
            out.append(entry)
    return out


def link_signal(context, before_names, after_names):
    # type: (Optional[Dict[str, Any]], Sequence[Optional[str]], Sequence[Optional[str]]) -> Optional[Dict[str, Any]]
    """What the bracket says about two adjacent games' pairings: same match, different match, or nothing.

    ``state`` is deliberately the only verdict offered, and ``same_match`` is a contradiction the caller has
    to carry rather than a green light. Two pairings that match no bracket entry, or that each match
    several, give ``unknown`` with the reason -- the bracket declining to answer is not the bracket agreeing.
    """
    if not context:
        return None
    readable = (any(before_names or ()), any(after_names or ()))
    if not all(readable):
        # Nothing to join on. A bracket with one match would otherwise "resolve" both blank pairings to
        # that match and report agreement, which is the bracket confirming an absence of evidence.
        return {"state": "unknown", "before_matches": [], "after_matches": [],
                "independent_of_recording": False, "provenance": context.get("provenance"),
                "grand_finals": False,
                "because": "one side of the link named no player, so there is nothing for the bracket to "
                           "match against"}
    before = match_by_participants(context, before_names)
    after = match_by_participants(context, after_names)
    base = {"before_matches": [m["id"] for m in before], "after_matches": [m["id"] for m in after],
            "independent_of_recording": False, "provenance": context.get("provenance"),
            "grand_finals": any((m.get("section") == "grand_finals") for m in before + after)}
    if not before or not after:
        base.update({"state": "unknown",
                     "because": "one pairing is not in the bracket supplied, so it says nothing about "
                                "whether these are the same match"})
        return base
    if len(before) > 1 or len(after) > 1:
        base.update({"state": "unknown",
                     "because": "a pairing appears in more than one listed match (a rematch, or a "
                                "grand-final reset), so match identity cannot be resolved from names"})
        return base
    one, two = before[0]["id"], after[0]["id"]
    if one != two:
        base.update({"state": "different_match", "before": one, "after": two,
                     "because": "the bracket lists these two pairings under different matches"})
    else:
        reset = before[0].get("grand_finals_reset")
        base.update({"state": "same_match", "before": one, "after": two,
                     "reset_possible": reset,
                     "because": "the bracket lists one match containing both pairings" +
                                ("" if reset is not False else
                                 ", and its format says a reset would be a second match")})
    return base


def format_for(context, names=None):
    # type: (Optional[Dict[str, Any]], Optional[Sequence[Optional[str]]]) -> Optional[Dict[str, Any]]
    """The format the bracket states for these players, or None when it does not state one.

    A target of ``games_per_set`` only counts when every candidate match agrees. Reporting a target that
    two listed matches disagree about is how an external guess becomes an internal fact, which is the
    mistake this whole module is meant to avoid.
    """
    if not context:
        return None
    candidates = match_by_participants(context, names) if names else list(context.get("matches") or ())
    targets = {m.get("games_per_set") for m in candidates if m.get("games_per_set")}
    resets = {m.get("grand_finals_reset") for m in candidates if m.get("grand_finals_reset") is not None}
    if len(targets) != 1 and len(candidates) > 1:
        targets = set()
    if not targets and not resets:
        return None
    return {"games_per_set": next(iter(targets), None),
            "grand_finals_reset": next(iter(resets), None) if len(resets) == 1 else None,
            "source": "bracket:{}".format((context.get("provenance") or {}).get("source")),
            "configured_by_a_human": False,
            "note": "reported by the event record; a set still closes only on wins observed in the "
                    "recording"}


def expectation(context, names=None):
    # type: (Optional[Dict[str, Any]], Optional[Sequence[Optional[str]]]) -> Optional[Dict[str, Any]]
    """How many games the reported score implies, offered as something to account for.

    A 3-2 is five games. That is a checklist, not a result: when the recovery finds three, the answer is a
    named gap of two, and when it finds five the reported score is *not* independent proof of them because
    it is the same number that generated the expectation.
    """
    if not context:
        return None
    candidates = match_by_participants(context, names) if names else list(context.get("matches") or ())
    if len(candidates) != 1:
        return None
    score = candidates[0].get("reported_score")
    if not score:
        return None
    return {"match": candidates[0]["id"], "reported_score": list(score),
            "expected_games": int(score[0]) + int(score[1]),
            "use": "account for this many games, or name the gap",
            "cannot": "supply games the recording does not show, or count as independent proof of "
                      "the same score"}


def provenance_block(context):
    # type: (Optional[Dict[str, Any]]) -> Dict[str, Any]
    """What to write into a run's output so a reader knows which route its confidence came from."""
    if not context:
        return {"supplied": False, "route": "recording_only"}
    return {"supplied": True, "route": "bracket_assisted", "version": context.get("version"),
            "provenance": context.get("provenance"), "matches": len(context.get("matches") or ()),
            "independent_of_recording": False,
            "evaluation_note": "a bracket score is an input here; it may not also be the evaluator's "
                               "truth for that same score"}


def summarize(context):
    # type: (Optional[Dict[str, Any]]) -> Dict[str, Any]
    """The compact form for a report: what was supplied, and what it was allowed to influence."""
    if not context:
        return {"supplied": False}
    return {"supplied": True, "source": (context.get("provenance") or {}).get("source"),
            "verified": (context.get("provenance") or {}).get("verified"),
            "match_count": len(context.get("matches") or ()),
            "sections": sorted({m.get("section") for m in context.get("matches") or ()}),
            "may_decide_a_boundary_alone": False,
            "independent_of_recording": False}
