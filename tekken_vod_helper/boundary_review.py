"""The BB9 review path: recovery → persisted project → review decisions → export consequences.

Everything here is deliberately free of Tkinter and free of inference, for two reasons. The first is
testability: the acceptance gate for this work is whether a *boundary* is right, and a rule buried in a
widget callback cannot be reviewed, only clicked. The second is honesty: this box has no `tkinter`, so any
UI logic that exists only inside `app.py` is unverified by definition. What lives here is measured; what is
left in `app.py` is presentation, and the manual checks for it are written down.

Three separations are enforced by the shape of the data rather than by discipline:

* **Machine suggestion is not human decision.** A segment carries a `recovery` block written by the
  pipeline and a `review` block written by a person. Reloading a project never overwrites the second from
  the first, and a decision records which suggestion it was made against.
* **Observed is not remembered.** A character that came from the tape, one that came from the operator's
  usual pick, and one that a person typed are three different provenances, stored per side. A remembered
  default must never be able to look like footage.
* **A name is side-bound and a character is not.** `boundary_games` publishes per-set `identities` with a
  player1/player2 slot share, but per-game `characters` as an unordered pair. Attaching bracket players to
  sides is therefore an orientation decision the recording does not make for us, so this module asks for it
  explicitly instead of assuming bracket order equals broadcast side.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from . import boundary_games as bg
from typing import Any, Dict, List, Optional, Sequence, Tuple

REVIEW_VERSION = "bb9-review-v1"
PADDING_SECONDS = 2.0
# A match is "the same" if two proposals agree to within this. It is a matching tolerance for scoring
# predictions against a reference, not a rule about the footage: nothing here decides a boundary.
EDGE_TOLERANCE_SECONDS = 2.0
UNRESOLVED = "unresolved"
SIDES = ("p1", "p2")

#: Who said a side's character, in decreasing order of authority over the field.
CHARACTER_SOURCES = ("human", "observed", "remembered", "")
#: Sources that came from the recording or a person, and so may not be replaced by a convenience default.
PRESERVE_SOURCES = ("human", "observed")
#: Sources an operator supplied from outside the recording. A start.gg name is not the operator typing, but
#: it is still somebody's deliberate statement about this set, so a re-import may not silently drop it.
PRESERVE_EDIT_SOURCES = ("human", "start.gg")


def _num(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------- timestamp policy

#: Every instant this project stores is carried to the microsecond, because that is the precision the
#: source manifests use (``790.789958``, not ``790.790``). Rounding is a *display* decision. When it is
#: applied to a stored boundary it silently moves that boundary -- and a clip end moved a millisecond late
#: can cross the start of the clip after it, which the exporter then holds for a disagreement of
#: forty-two millionths of a second that nobody wrote down. So: store at ``INSTANT_PRECISION``, print at
#: ``DISPLAY_PRECISION``, and let neither meet the other's job.
INSTANT_PRECISION = 6

#: What "the same instant" means when comparing two of them: a nanosecond, which is float noise and
#: nothing else. It is deliberately two orders finer than ``INSTANT_PRECISION``, so a stored disagreement
#: is always a disagreement -- and not, as it was when the comparison met the rounding, a difference the
#: reviewer could not see but the exporter could not forgive.
INSTANT_EPSILON = 1e-9

#: What a reviewer is shown by default. Three decimals is a millisecond, which is finer than a frame at
#: any frame rate this project handles, so it is enough to act on -- unless the message is *about* two
#: instants disagreeing, which is what ``instant_text`` is for.
DISPLAY_PRECISION = 3


def canonical_instant(value) -> Optional[float]:
    """The value as it is stored: the source instant, to the microsecond, no coarser."""
    number = _num(value)
    if number is None:
        return None
    return round(number, INSTANT_PRECISION)


def instant_text(value, *, against: Optional[float] = None) -> str:
    """An instant in words, with enough digits that a comparison is not a coincidence.

    ``against`` is the other instant the sentence is weighing. Print both to the millisecond when they
    differ by more than that; print them to the microsecond when they do not, because "clip end 790.790
    runs past the next candidate's start 790.790" reads as a bug in the reviewer's own arithmetic and
    hides a real disagreement of 0.000042 s.
    """
    number = _num(value)
    if number is None:
        return "an unknown instant"
    other = _num(against)
    digits = DISPLAY_PRECISION
    if other is not None and abs(number - other) < 0.5 * 10 ** -DISPLAY_PRECISION:
        digits = INSTANT_PRECISION
    return ("%." + str(digits) + "f") % number


def span_text(seconds: Optional[float]) -> str:
    """A duration in words, widened when the rounding would round a real disagreement out of existence."""
    number = _num(seconds)
    if number is None:
        return "an unknown span"
    digits = DISPLAY_PRECISION if abs(number) >= 0.5 * 10 ** -DISPLAY_PRECISION else INSTANT_PRECISION
    return ("%." + str(digits) + "f") % number


def is_nonfinite(value) -> bool:
    """True for the two numbers that are not instants at all: NaN and +/-inf.

    They deserve their own word because ``_num`` accepts both, and every comparison against NaN is False --
    so a NaN boundary slips past "is it after the start", "is it before the next opening" and "is it past
    the end of the tape" without ever being true, and lands in a JSON file as ``NaN``, which is not JSON.
    """
    number = _num(value)
    return number is not None and not math.isfinite(number)


def _key(player: str) -> str:
    return re.sub(r"\s+", " ", player or "").strip().casefold()


# --------------------------------------------------------------------------- importing recovery output

def _side_identity(identities: Sequence[Dict[str, Any]], side: str) -> Dict[str, Any]:
    """The strongest name evidence this set holds for one screen side.

    Reads are per-side with a share; a low share or `approximate` is kept rather than smoothed away, because
    "the nameplate was unreadable" is a fact a reviewer needs and not a gap to fill.
    """
    best = {"player": "", "share": 0.0, "approximate": True, "verdicts": []}
    for entry in identities or []:
        for stage in ("after", "before"):
            names = entry.get(stage) or []
            slot = "player1" if side == "p1" else "player2"
            share = float(((entry.get("share") or {}).get(stage) or {}).get(slot) or 0.0)
            index = 0 if side == "p1" else 1
            if index < len(names) and share >= best["share"] and names[index]:
                best = {"player": names[index], "share": share,
                        "approximate": bool(entry.get("approximate")),
                        "verdicts": sorted({str(e.get("verdict")) for e in identities
                                            if e.get("verdict")})}
    return best


def import_recovery(recovery: Dict[str, Any], *, source: str = "",
                    games: Optional[Sequence[Dict[str, Any]]] = None,
                    clip_padding: float = PADDING_SECONDS) -> Dict[str, Any]:
    """Turn a saved recovery document into reviewable segments, losing nothing on the way in.

    One candidate is one *set*, and a set is exported as one clip, so a set becomes the segment and its
    games become evidence underneath it. The flat character pair is filled only when a side can be named;
    otherwise it stays empty and `orientation_required` says why. Nothing is invented to make a field
    look complete: an unread name or an unbound character stays missing, with the reason attached.

    The published candidate list is set-level, so per-game evidence arrives separately -- from a
    ``track_games`` run over saved evidence, or from an ``investigation.json`` that kept it. Without it a
    segment would be an interval with nothing to review inside it, and that is reported rather than hidden.
    """
    candidates = (recovery.get("candidates") if isinstance(recovery, dict) else None) or []
    games = list(games) if games is not None else list(recovery.get("games") or [])
    # A published candidate names its games as a bare count and leaves `game_spans` null, so the per-game
    # evidence has to be associated by containment inside the set interval. The published count is kept
    # beside the result: when they disagree, that is a fact about the import, not something to reconcile by
    # taking whichever list came to hand.
    tolerance = EDGE_TOLERANCE_SECONDS

    def games_for(candidate):
        start, end = _num(candidate.get("start")), _num(candidate.get("end"))
        spans = candidate.get("game_spans") or []
        chosen = []
        for span in spans:
            if isinstance(span, (list, tuple)) and len(span) == 2:
                chosen.append(next((g for g in games if abs(_num(g.get("start")) - float(span[0]))
                                    <= tolerance), None))
        chosen = [g for g in chosen if g]
        if not chosen and start is not None and end is not None:
            chosen = [g for g in games
                      if _num(g.get("start")) is not None and _num(g.get("end")) is not None
                      and float(g["start"]) >= start - tolerance and float(g["end"]) <= end + tolerance]
        return chosen

    segments: List[Dict[str, Any]] = []
    count_mismatches: List[Dict[str, Any]] = []
    for candidate in candidates:
        start, end = _num(candidate.get("start")), _num(candidate.get("end"))
        evidence_games = [_game_evidence(g) for g in games_for(candidate)]
        published = candidate.get("games")
        if isinstance(published, int) and games and published != len(evidence_games):
            count_mismatches.append({"start": start, "published_game_count": published,
                                     "games_attached": len(evidence_games)})
        sides = {side: {"player": _side_identity(candidate.get("identities") or [], side)["player"],
                        "character": "", "source": ""} for side in SIDES}
        unnamed = [side for side in SIDES if not sides[side]["player"]]
        # A winner credited from screen sides is the broadcast graphic's claim, not a confirmed identity:
        # while no side is bound to a player and no orientation is answered, "Mattiniero" and "the p2 side"
        # are the same statement. Published in the channel the review tab already shows, so the name cannot
        # be read as settled without its condition arriving with it.
        claims = list(candidate.get("unresolved_claims") or [])
        if candidate.get("set_winner") and unnamed:
            claims.append(
                "the set's winner is credited from the nameplate on the winning side while neither side is "
                "bound to a player and no orientation is answered, so the name is a graphic reading and "
                "stays open to correction")
        segment = {
            "start": start, "end": end,
            "player1": sides["p1"]["player"], "player2": sides["p2"]["player"],
            "character1": "", "character2": "", "round_name": "", "notes": "",
            "recovery": {
                "version": REVIEW_VERSION,
                "source": source,
                "sides": sides,
                "games": evidence_games,
                "clip_end_at": _num(candidate.get("clip_end_at")),
                "clip_end_basis": candidate.get("clip_end_basis"),
                "clip_end_anchor": candidate.get("clip_end_anchor"),
                "clip_uncertainty": candidate.get("clip_uncertainty"),
                "concluded_by": candidate.get("concluded_by"),
                "footage_complete": candidate.get("footage_complete"),
                "game_wins": candidate.get("game_wins"),
                "set_winner": candidate.get("set_winner"),
                "membership": candidate.get("membership"),
                "deciding_wins_unit": candidate.get("deciding_wins_unit"),
                # Verdict provenance, imported as a set rather than one field at a time. A winner name
                # without the target it was measured against, the tally that reached it, and what that
                # tally counted is a claim nobody can check: an earlier import carried `set_winner` and
                # `deciding_wins_unit` while `applicable_formats`, `winner_unit` and `deciding_wins` were
                # dropped, so a saved project could not say which first-to-N closed a set, or whether the
                # number behind the name counted people or screen sides.
                "deciding_wins": candidate.get("deciding_wins"),
                "winner_unit": candidate.get("winner_unit"),
                "winner_seat": candidate.get("winner_seat"),
                "applicable_formats": candidate.get("applicable_formats"),
                "complete_under": candidate.get("complete_under"),
                "open_under": candidate.get("open_under"),
                "format_dependent": candidate.get("format_dependent"),
                "games_per_set_expected": candidate.get("games_per_set_expected"),
                "set_completion": candidate.get("set_completion"),
                "end_transition_observed": candidate.get("end_transition_observed"),
                "cut_proposed_by_pipeline": candidate.get("cut_proposed_by_pipeline"),
                "grouping_unresolved": candidate.get("grouping_unresolved"),
                "game_wins_by_identity": candidate.get("game_wins_by_identity"),
                "attribution_support": (candidate.get("attribution") or {}).get("support"),
                # The lower tier: who the graphic named, and how much that deserves to be believed. Both
                # carry their own `authoritative: False`, and both are inert -- nothing above this line is
                # recomputed from them.
                "attribution_confidence": candidate.get("attribution_confidence"),
                "character_bindings": candidate.get("character_bindings"),
                "display_text_observations": candidate.get("display_text_observations"),
                # The refusals travel with the corrections: a saved project that kept only the applied half
                # would make an observation that was quoted as lookback context, or refused on a text
                # mismatch, look like a correction that worked.
                "display_text_correction_refusals": candidate.get("display_text_correction_refusals"),
                "alternative_groupings": candidate.get("alternative_groupings") or [],
                "ambiguous_links_cut": candidate.get("ambiguous_links_cut", 0),
                "boundary_conflicts": candidate.get("boundary_conflicts") or [],
                "missing_evidence": candidate.get("missing_evidence") or [],
                "unresolved_claims": claims,
                "published_game_count": published if isinstance(published, int) else None,
                "edges": candidate.get("edges") or {},
                "attribution_withheld": candidate.get("attribution_withheld"),
                # Imported whole, not re-summarised. The per-game basis, share, row count and span, whether
                # a band cover existed and whether the two reads of the graphic agreed are the parts a
                # reviewer needs in order to disagree; a count of named games would keep none of them. The
                # tier carries its own flag (`authoritative: False`) and is read by nothing that decides a
                # winner, a target or a grouping.
                "nameplate_hypotheses": candidate.get("nameplate_hypotheses"),
                # The recording does not bind a character to a side, so this is a fact about the evidence
                # and not a field left blank by accident.
                "characters_side_bound": False,
                "orientation_required": bool(unnamed),
                "unnamed_sides": unnamed,
            },
            "review": {"decisions": [], "orientation": ("unresolved" if unnamed else "observed"),
                       "suggested_by": REVIEW_VERSION},
        }
        segments.append(segment)
    segments.sort(key=lambda s: (s.get("start") is None, s.get("start") or 0.0))
    empty = [s for s in segments if not s["recovery"]["games"]]
    return {"segments": segments,
            "accounting": {"candidates_in": len(candidates), "segments_out": len(segments),
                           "segments_without_game_evidence": len(empty),
                           "game_evidence_supplied": len(games),
                           "game_count_mismatches": count_mismatches,
                           "needing_orientation": sum(1 for s in segments
                                                      if s["recovery"]["orientation_required"]),
                           "with_alternative_groupings": sum(
                               1 for s in segments if s["recovery"]["alternative_groupings"])}}


def _game_evidence(game: Dict[str, Any]) -> Dict[str, Any]:
    """Per-game facts, kept per game rather than flattened into one pair for the whole set."""
    support = game.get("winner_support") or {}
    return {
        "start": _num(game.get("start")), "end": _num(game.get("end")),
        "status": game.get("status"), "basis": game.get("basis"),
        "winner": game.get("winner"), "winner_basis": game.get("winner_basis"),
        "counts_toward_set": game.get("counts_toward_set"),
        "rounds": game.get("rounds"), "round_ids": game.get("round_ids") or [],
        "characters_observed": list(game.get("characters") or []),
        "characters_side_bound": False,
        "conclusion_event_at": _num(game.get("conclusion_event_at")),
        "winner_confirmed_at": _num(game.get("winner_confirmed_at")),
        "clip_end_at": _num(game.get("clip_end_at")),
        "clip_end_basis": game.get("clip_end_basis"),
        "end_uncertainty": game.get("end_uncertainty"),
        "boundary_certain": game.get("boundary_certain"),
        "transition_at": _num(game.get("transition_at")),
        "closing_screen": game.get("closing_screen"),
        "footage_after_conclusion_seconds": _num(game.get("footage_after_conclusion_seconds")),
        "winner_support": {"frame_count": support.get("frame_count"),
                           "distinct_instants": support.get("distinct_instants"),
                           "deciding_at": _num(support.get("deciding_at")),
                           "single_reading": bool(support.get("single_reading")),
                           "frame_paths": [str(p) for p in (support.get("frame_paths") or [])]},
        "contested": game.get("contested"), "contested_reasons": game.get("contested_reasons") or [],
        "round_count_beyond_target": game.get("round_count_beyond_target") or [],
        "dot_regressions": game.get("dot_regressions") or [],
        "boundary_evidence": game.get("boundary_evidence") or [],
    }


# ------------------------------------------------------------------- character resolution and orientation

def resolve_characters(segment: Dict[str, Any], bracket_players: Sequence[str],
                       orientation: Optional[str] = None,
                       remembered: Optional[Dict[str, str]] = None
                       ) -> Dict[str, Any]:
    """Attach a bracket set's two names to the segment's two sides, keeping evidence separate by side.

    ``orientation`` is how the operator says the pairing goes: ``"as-listed"`` means bracket player one is
    the left side of the broadcast, ``"swapped"`` means the reverse, and ``None`` means nobody has said.
    A remembered default may fill a side that has no character evidence at all. It may not replace one that
    does, and whatever it fills is recorded as ``remembered`` so it cannot later be mistaken for footage.
    """
    remembered = remembered or {}
    recovery = segment.get("recovery") or {}
    sides = {side: dict((recovery.get("sides") or {}).get(side)
                        or {"player": segment.get("player1" if side == "p1" else "player2", ""),
                            "character": "", "source": ""}) for side in SIDES}
    for side in SIDES:
        # Preserve any human or observed character already decided for this side.
        stored = ((segment.get("review") or {}).get("characters") or {}).get(side) or {}
        if stored.get("character") and stored.get("source") in PRESERVE_SOURCES:
            sides[side]["character"] = stored["character"]
            sides[side]["source"] = stored["source"]
    left, right = (list(bracket_players) + ["", ""])[:2]
    assign = {"as-listed": {"p1": left, "p2": right},
              "swapped": {"p1": right, "p2": left}}.get(orientation or "")
    result = {"orientation": orientation, "sides": {}, "questions": []}
    if assign is None:
        result["questions"].append({
            "kind": "orientation",
            "ask": ("Which side of the screen is %s on? The recording names sides but never ties a "
                    "character to one, so this cannot be read off the footage." % (left or "player one")),
            "options": ["as-listed", "swapped", "unsure"],
            "why_flagged": ("bracket players are ordered by the listing, not by the broadcast; assigning "
                            "them without a decision would be a guess wearing a name"),
        })
    for side in SIDES:
        player = (assign or {}).get(side, "")
        evidence = sides[side]
        observed = (evidence.get("character") or "").strip()
        name_from_footage = (evidence.get("player") or "").strip()
        if observed:
            character, source = observed, evidence.get("source") or "observed"
        elif assign is not None and player:
            fallback = (remembered.get(_key(player)) or "").strip()
            character = fallback
            # A default is a starting point for a field nobody has filled, never an observation. Naming it
            # "remembered" is what keeps it from laundering into the record as footage.
            source = "remembered" if fallback else ""
        else:
            character, source = "", ""
        result["sides"][side] = {
            "player": player, "character": character, "character_source": source,
            "observed_names": [name_from_footage] if name_from_footage else [],
            "characters_observed_but_unbound": list(
                (recovery.get("games") or [{}])[0].get("characters_observed") or [])
            if not recovery.get("characters_side_bound") else [],
        }
    result["export_ready"] = assign is not None and all(
        result["sides"][side]["player"] for side in SIDES)
    return result


# ---------------------------------------------------------------------------- clip end and its export

def resolve_clip_end(segment: Dict[str, Any], following: Optional[Dict[str, Any]] = None,
                     duration: Optional[float] = None,
                     clip_padding: float = PADDING_SECONDS) -> Dict[str, Any]:
    """Where a clip stops, once, with the consequence of every fallback spelled out.

    Order of authority: a human end, then the padded bound the pipeline derived from conclusion evidence,
    then the recorded end. Stopping at the *next* mark is never assumed: it is offered only as a flagged
    choice, because a following mark is a different match's beginning and using it silently would export
    somebody else's footage. A job is never skipped in silence either -- every refusal comes back with a
    reason a UI can show.
    """
    review = segment.get("review") or {}
    recovery = segment.get("recovery") or {}
    ends = review.get("ends") or {}
    human_end = _num(ends.get("end"))
    # "Ends here" pressed on a clip this module flagged is a person accepting the loss on the record. It is
    # the only thing that may turn a hold into an export, so it travels with the human end rather than
    # living in a log line nobody re-reads.
    approved = bool(ends.get("approved_by_human"))
    machine_end = _num(recovery.get("clip_end_at"))
    recorded_end = _num(segment.get("end"))
    anchor = recovery.get("clip_end_anchor") or {}
    confirmed = _num(anchor.get("winner_confirmed_at"))
    warnings: List[str] = []
    excluded: List[Dict[str, Any]] = []
    extra: List[Dict[str, Any]] = []
    # Disclosed, never applied. A suggestion of an end has no bearing on which end governs: that is decided
    # by a human answer, then the machine's padded number, then the recorded set end -- in that order.
    suggested = pending_proposal(segment, "end")
    proposal_view = {"end_proposal": suggested,
                     "proposed_end": _num((suggested or {}).get("at")),
                     "end_proposal_purpose": (suggested or {}).get("purpose"),
                     "end_governs": "human decision" if human_end is not None else
                                    ("machine evidence" if machine_end is not None else "nothing yet")}
    if human_end is not None:
        end, basis = human_end, "human decision"
    elif machine_end is not None:
        end, basis = machine_end, str(recovery.get("clip_end_basis") or "recovery padded end")
    elif recorded_end is not None:
        end, basis = recorded_end, "recorded set end (no padded conclusion end was published)"
    else:
        end, basis = None, "no end evidence"
    if end is not None and confirmed is not None and end < confirmed - INSTANT_EPSILON:
        # The claim and the number disagree. Saying so is the fix; extending through the next opening
        # without being asked would be the same mistake in the other direction.
        warnings.append(
            "clip ends at %s but the winner confirmation is at %s: the exported clip does not contain "
            "the reading that names the winner, and the pipeline's own basis text says it does"
            % (instant_text(end, against=confirmed), instant_text(confirmed, against=end)))
        excluded.append({"from": end, "to": confirmed, "what": "winner confirmation"})
    if following is not None and end is not None:
        next_start = _num(following.get("start"))
        if next_start is not None and end > next_start + INSTANT_EPSILON:
            warnings.append("clip end %s runs past the next candidate's start %s"
                            % (instant_text(end, against=next_start),
                               instant_text(next_start, against=end)))
    if end is None:
        next_start = _num((following or {}).get("start"))
        return {"end": None, "basis": basis, "warnings": warnings + [
            "no conclusion evidence and no human end; refusing to default to the next mark at %s"
            % (instant_text(next_start) if next_start is not None else "an unknown position")],
                "excluded_gameplay": excluded, "included_past_recorded_end": [], "severity": "blocking",
                "skipped_reason": "unresolved end", "held": True, "human_governs": False,
                "approved": False, "review_kind": "clip_end",
                "suggested_end": next_start, "suggestion_needs_approval": True, **proposal_view}
    clamped = False
    if duration is not None and end > _num(duration) + INSTANT_EPSILON:
        warnings.append("clip end %s exceeds the recording length %s; clamped"
                        % (instant_text(end, against=_num(duration)), instant_text(duration)))
        end = _num(duration)
        clamped = True
    if recorded_end is not None and end > recorded_end + INSTANT_EPSILON:
        # Padding walks forward on purpose, so this is not a loss: it is extra tape that the clip now
        # claims, and it may contain the opening of something else. Listing it under excluded gameplay
        # would be the opposite of what happened.
        extra.append({"from": recorded_end, "to": end,
                      "what": "footage past the recorded set end that this clip now includes"})
        warnings.append("clip runs %s s past the recorded set end; confirm nothing else's footage is "
                        "being claimed" % span_text(end - recorded_end))
    next_start = _num((following or {}).get("start"))
    crosses = next_start is not None and end > next_start + INSTANT_EPSILON
    if crosses:
        warnings.append("clip end %s passes the next candidate's start %s"
                        % (instant_text(end, against=next_start), instant_text(next_start, against=end)))
    # Padding reaching a couple of seconds past a set's recorded end is what padding is for. Asking a
    # reviewer about all ten sets turns a queue into noise and teaches them to skip the flag that matters,
    # so an overhang is disclosed and only a loss or a collision is asked about. Demanding a decision on
    # every clip was my first design, and the real import showed it firing on 10 of 10.
    #
    # A hold is a function of whether a person has spoken to the defect, not of the defect alone: an
    # unapproved loss or collision blocks the clip, and the same loss approved on the record exports with
    # the disclosure attached. A collision is never approvable by silence either -- it claims footage that
    # belongs to the next candidate -- so an approved human end that still crosses is held again here.
    held = bool((excluded and not approved) or crosses or clamped)
    if excluded and approved:
        warnings.append("approved by a human decision: the clip still stops before the winner confirmation "
                        "at %.3f and the excluded interval is recorded below" % confirmed)
    severity = "blocking" if held else ("disclosure" if extra or excluded else "clean")
    return {"end": end, "basis": basis, "warnings": warnings, "excluded_gameplay": excluded,
            **proposal_view,
            "included_past_recorded_end": extra, "severity": severity,
            "skipped_reason": None, "held": held,
            "human_governs": human_end is not None, "approved": approved,
            "review_kind": "clip_end",
            "padding_applied_once": machine_end is not None and human_end is None,
            "suggested_end": None, "suggestion_needs_approval": False}


# -------------------------------------------------------------------------------------------------- decisions

#: The three states a decision record moves between. ``deferred`` is what "Unsure" means: it stays on the
#: record, it is not re-asked in the open view, and it governs nothing -- which is what makes it resumable
#: instead of terminal, and consequential instead of a shrug.
DECIDED = "decided"
DEFERRED = "deferred"
INVALIDATED = "invalidated"
#: Projects written before ``deferred`` existed recorded every answer as ``answered``.
LEGACY_ANSWERED = "answered"
GOVERNING_STATUSES = (DECIDED, LEGACY_ANSWERED)

#: The segment fields a decision derives. ``recompute`` clears exactly these and replays the journal, so an
#: undone or invalidated answer cannot leave its consequence behind. Everything else inside ``review`` is
#: import-time context and is never a decision in disguise.
DERIVED_REVIEW_KEYS = ("grouping", "ends", "orientation", "characters", "membership", "bracket",
                       # An approval to ship a partial is an answer like any other: it governs while the
                       # decision that spoke it governs, and a replaced or undone approval does not get to
                       # keep excusing a boundary nobody is currently approving.
                       "openings")

#: How far a re-import may move a candidate and still be the same set rather than a second row.
IDENTITY_TOLERANCE_SECONDS = EDGE_TOLERANCE_SECONDS

#: The value a Tekken round clock is drawn at when a round opens. Not an offset: the clock only ever falls
#: inside a round, so a reading below this at a candidate instant is proof play was already running.
ROUND_CLOCK_OPENING = 60

#: How far from an instant a saved reading may be taken and still be *about* that instant. One sample of
#: the saved sequence is about a second, so within a step the reading is the best available account of the
#: frame; beyond it the frame is a different one and has to be read there rather than reasoned about from
#: here -- inside a round the clock can fall, hold and fall again, and a new round can open in between.
INSTANT_TOLERANCE_SECONDS = 1.0


class InvalidAnswer(ValueError):
    """An answer a question never offered, or a boundary value the footage cannot hold."""


#: A machine's suggested boundary, kept in its own channel.
#:
#: A proposal is not a weak decision; it is a different kind of thing. It moves no row, closes no question,
#: holds no end basis and is counted as neither reviewed nor accepted, because the moment a suggestion
#: travels through the channel that carries a person's answer some consumer will read it as one. That is not
#: hypothetical: the first demonstration of this feature stamped ``proposed_by=machine`` onto the decision
#: records a controller returned, and every runtime reader consulted -- the open queue, ``resolve_clip_end``'s
#: basis, the plan's ``operator_accepted`` count -- went on treating the machine as the operator, because
#: that is what a decision means here.
#:
#: So the owner's question stays open beside a pending proposal, and only an explicit apply turns the
#: instant into a boundary, recorded under the person who applied it.
PROPOSAL_KINDS = ("opening", "end")
PROPOSAL_STATUSES = ("proposed", "accepted", "rejected", "superseded")
PROPOSAL_PENDING = "proposed"
PROPOSAL_APPLIED = "accepted"
PROPOSAL_REFUSED = "rejected"
#: A suggestion a later pass displaced. Kept on the row rather than rewritten, because a card still open on
#: the displaced number has to be able to say "this is no longer what is on offer" instead of quietly acting
#: on its replacement -- which is what a reused id let a reviewer do.
PROPOSAL_SUPERSEDED = "superseded"
PROPOSAL_GENERATION_KEY = "proposal_generation"
PROPOSAL_REVIEW_KEY = "proposals"
#: What a proposed end is for. A read that confirms the winner is evidence; a longer victory camera is a
#: publishing choice, and the two must not be filed under one word.
PROPOSAL_PURPOSES = ("confirmation", "presentation")


def proposal_record(kind: str, at: float, *, why: str, evidence: Optional[Dict[str, Any]] = None,
                    uncertainty_seconds: Optional[float] = None, purpose: str = "confirmation",
                    generated_by: str = "") -> Dict[str, Any]:
    """One suggested boundary, with the reason it is suggested and the width the sampling claims for it.

    ``uncertainty_seconds`` stays ``None`` where nothing bounds the instant. Printing an unknown as
    ``±0.000 s`` turns a guess into a measurement, and telling those apart is the only job this field has.
    """
    if kind not in PROPOSAL_KINDS:
        raise InvalidAnswer("no machine proposes %r about footage: the kinds are %s"
                            % (kind, ", ".join(PROPOSAL_KINDS)))
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        raise InvalidAnswer("a proposal has to name the instant it suggests, not %r" % (at,))
    if not str(why or "").strip():
        raise InvalidAnswer("a proposal without its reason is an instruction, not a suggestion")
    if uncertainty_seconds is not None:
        if isinstance(uncertainty_seconds, bool) or not isinstance(uncertainty_seconds, (int, float)):
            raise InvalidAnswer("uncertainty is a width in seconds or it is unknown, not %r"
                                % (uncertainty_seconds,))
        if float(uncertainty_seconds) < 0:
            raise InvalidAnswer("a width cannot be negative")
    if purpose not in PROPOSAL_PURPOSES:
        raise InvalidAnswer("a proposed end is for %s, not %r"
                            %(" or ".join(PROPOSAL_PURPOSES), purpose))
    return {"kind": kind, "at": float(at), "why": str(why), "evidence": dict(evidence or {}),
            "uncertainty_seconds": None if uncertainty_seconds is None else float(uncertainty_seconds),
            "purpose": purpose, "status": PROPOSAL_PENDING, "generated_by": str(generated_by or ""),
            "operator_decision": False}


def _proposal_serial(proposal_id: Optional[str]) -> int:
    """The number inside an id, for rows whose proposals were filed before generations were stored.

    Both spellings are read: ``opening:2`` from the first version of this feature and ``opening:g2`` from
    this one. A missing or unreadable id counts as zero, which makes a legacy row start above everything it
    already holds rather than colliding with it.
    """
    text = str(proposal_id or "")
    tail = text.rsplit(":", 1)[-1].lstrip("g")
    return int(tail) if tail.isdigit() else 0


#: Screen states the cutting policy treats as one match's own load-in, in the order a bracket event
#: normally walks through them: pick characters, pick a stage, the versus splash, the stage intro, play.
#: ``unknown`` is deliberately absent -- policy says a blank or overlay-only frame "supplies no intrinsic
#: evidence" -- and it therefore breaks the chain rather than extending it.
#: How far apart two rows may be and still count as one continuous load-in. The sampling of this cache is
#: about a second, and the widest legitimate step inside a load-in is one sample; anything larger is a hole
#: the evidence does not cover, and a chain must not be drawn across a hole.
MAX_LOAD_IN_GAP_SECONDS = 12.0
LOAD_IN_STATES = ("stage_select", "character_select", "controller_prompt", "controller_assignment",
                  "vs_screen", "stage_intro")
#: The one state the policy prefers to cut on: "prefer the final confident stage-select occurrence before
#: the set begins. Require visible stage-selection tiles/grid."
POLICY_PREFERRED_START = "stage_select"
POLICY_FALLBACK_REASON = (
    "no clean stage-select occurrence in this match's own load-in; the policy says a fallback to an early "
    "pre-intro frame is a review flag, not a confident start, so this instant is offered as a machine "
    "fallback with its uncertainty left at the sampling width")


def _is_opening_frame(row: Dict[str, Any]) -> bool:
    """A frame of the round that is beginning, which the load-in chain is allowed to stand on.

    The chain has to be able to cross the round's own opening frames -- a banner draws the arena and a HUD
    with it, so the row reads ``gameplay`` while nothing is being fought. What it must not cross is live
    combat: a row whose phase is active means the round was already running, and everything behind that
    belongs to a game, not to this load-in. ``unknown`` stops the chain too; a missing sighting is not
    permission to keep walking.
    """
    if row.get("state") != "gameplay":
        return False
    phase = row.get("combat_phase")
    if phase is not None:
        return phase == "round_opening"
    return bool(row.get("round_call") or row.get("round_open_cue")
                or row.get("round_banner_on_screen"))


def policy_clip_start(rows: Optional[Sequence[Dict[str, Any]]], *, candidate_at: float,
                      pairing: Sequence[Optional[str]] = (),
                      opening_at: Optional[float] = None) -> Dict[str, Any]:
    """Where the declared cutting policy would start this clip, from the saved rows alone.

    Walking back from the candidate through its own load-in -- consecutive presentation states, stopping at
    anything else, including an ``unknown`` frame -- gives the chain of screens that introduced this match.
    If a ``stage_select`` row is in that chain the policy has a preferred anchor and the answer is the last
    one; if it is not, the earliest presentation row is offered as a flagged fallback rather than as a
    confident start. Nothing here names a recording, an instant or a player: it reads states, and the row's
    own pairing, which is what the policy is written in.

    ``pairing`` is the candidate's broadcast names, matched loosely because the overlay truncates
    (``PWDDA | Ariz`` and ``PWD DA | Az`` are one nameplate). A chain row naming a *different* pairing breaks
    the chain: continuity of the picture is what joins these frames to this match, and a nameplate the
    recording is known to repaint cannot be used to claim a different one belongs here.
    """

    def same(one: Optional[str], other: Optional[str]) -> bool:
        a = re.sub(r"[^a-z0-9]", "", (one or "").lower())
        b = re.sub(r"[^a-z0-9]", "", (other or "").lower())
        if not a or not b:
            return True                              # a row that names nobody contradicts nothing
        return a in b or b in a or a[:4] == b[:4]

    wanted = [x for x in (pairing or ()) if x]
    chain: List[Dict[str, Any]] = []
    previous = min(float(candidate_at), float(opening_at)) if opening_at is not None else float(candidate_at)
    for row in sorted((r for r in rows or [] if _num(r.get("timestamp")) is not None),
                      key=lambda r: float(r["timestamp"]), reverse=True):
        at = float(row["timestamp"])
        if at >= previous - INSTANT_EPSILON:
            continue
        if previous - at > MAX_LOAD_IN_GAP_SECONDS:
            break                                    # a hole wider than the sampling: not one continuous run
        state = row.get("state")
        names = (row.get("player1"), row.get("player2"))
        if state not in LOAD_IN_STATES and not _is_opening_frame(row):
            break                                    # active play, a blank frame, or any other screen
        if not all(same(a, b) for a, b in zip(wanted, names)):
            break
        chain.append({"at": at, "state": state, "pairing": " / ".join(n for n in names if n)})
        previous = at
    chain.reverse()
    if not chain:
        return {"at": None, "basis": None, "fallback": False, "chain": [], "uncertainty_seconds": None,
                "review_reason": "no load-in rows precede this candidate in the saved sequence"}
    preferred = [row for row in chain if row["state"] == POLICY_PREFERRED_START]
    anchor = preferred[-1] if preferred else chain[0]
    width = None
    after = [row["at"] for row in chain if float(row["at"]) > float(anchor["at"])]
    if after:
        width = round(min(after) - float(anchor["at"]), INSTANT_PRECISION)
    return {"at": float(anchor["at"]),
            "basis": ("policy: final confident stage select before the set" if preferred
                      else "fallback: no clean stage select; flagged for review, not a confident start"),
            "fallback": not preferred, "chain": chain,
            "uncertainty_seconds": width,
            "review_reason": None if preferred else POLICY_FALLBACK_REASON}


def proposal_width(uncertainty_seconds: Optional[float]) -> str:
    """How wide the sampling says a proposed instant is -- and "unknown" when it does not say."""
    return "unknown" if uncertainty_seconds is None else "+-%.3f s" % float(uncertainty_seconds)


def pending_proposal(segment: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    """The live proposal of this kind -- the newest one nobody has acted on -- or nothing.

    Kept in step with :meth:`ReviewStore.find_proposal` so the card cannot display a different record from
    the one an action on that card would resolve. ``ReviewStore.propose`` replaces a pending proposal rather
    than adding a second, so at most one is ever waiting; taking the last is the defensive half of that.
    """
    live = [record for record in ((segment.get("review") or {}).get(PROPOSAL_REVIEW_KEY) or [])
            if isinstance(record, dict) and record.get("kind") == kind
            and record.get("status") == PROPOSAL_PENDING]
    return live[-1] if live else None


def _governs(decision: Dict[str, Any]) -> bool:
    return decision.get("status", LEGACY_ANSWERED) in GOVERNING_STATUSES


def machine_sides(segment: Dict[str, Any]) -> str:
    """What the recording alone says about orientation, before any human has spoken."""
    return UNRESOLVED if (segment.get("recovery") or {}).get("orientation_required") else "observed"


class ReviewStore:
    """Human decisions as data: additive, undoable, resumable, and invalidated by upstream change.

    The journal at project level is the authority and the segment's derived fields are its projection.
    Every write path (``decide``, ``undo``, ``invalidate``) ends in :meth:`recompute`, which rebuilds that
    projection from the decisions that still govern plus the machine evidence underneath. That is the whole
    reason undo exists: an answer that was withdrawn must not still be holding a clip end.
    """

    def __init__(self, project: Dict[str, Any]):
        self.project = project
        stored = project.get("review")
        if not isinstance(stored, dict):
            stored = {"version": REVIEW_VERSION, "decisions": [], "log": []}
            project["review"] = stored
        stored.setdefault("version", REVIEW_VERSION)
        stored.setdefault("decisions", [])
        stored.setdefault("log", [])
        self.state = stored

    def _segments(self) -> List[Dict[str, Any]]:
        return self.project.get("matches") or []

    def _segment(self, key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        wanted = _num(key.get("start"))
        if wanted is None and key.get("index") is not None:
            segments = self._segments()
            position = int(key["index"])
            return segments[position] if 0 <= position < len(segments) else None
        for segment in self._segments():
            if _num(segment.get("start")) == wanted:
                return segment
        return None

    def question_fingerprint(self, item: Dict[str, Any]) -> str:
        return "%s|%s|%s" % (item.get("kind"), _num((item.get("segment") or {}).get("start")),
                             item.get("subject", ""))

    def decisions_for(self, segment_start: Optional[float]) -> List[Dict[str, Any]]:
        wanted = _num(segment_start)
        return [d for d in self.state["decisions"] if _num(d.get("segment_start")) == wanted]

    def governing(self) -> List[Dict[str, Any]]:
        return [d for d in self.state["decisions"] if _governs(d)]

    def deferred(self) -> List[Dict[str, Any]]:
        return [d for d in self.state["decisions"] if d.get("status") == DEFERRED]

    def unresolved(self) -> List[Dict[str, Any]]:
        """Everything a reviewer has not settled: deferred by choice, invalidated by moved evidence."""
        return [d for d in self.state["decisions"] if not _governs(d)]

    def decision(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        return next((d for d in self.state["decisions"] if d.get("fingerprint") == fingerprint), None)

    def ask(self, item: Dict[str, Any]) -> bool:
        """False once anybody has spoken to this question, in any state.

        A deferred answer stops the nag and stays resumable through :meth:`deferred`; re-listing it in the
        open queue every time the panel refreshes is what teaches an operator to stop reading the list.
        """
        fingerprint = self.question_fingerprint(item)
        return not any(d.get("fingerprint") == fingerprint for d in self.state["decisions"])

    # --------------------------------------------------------------------------------- proposals

    def propose(self, segment_key: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
        """File a machine's suggested boundary on a row, and change nothing else.

        Refused when a person has already answered that boundary: an automatic pass that runs again after an
        edit must not be able to bury the edit, and the honest answer to "the operator already said where
        this set opens" is to leave the row alone and say so. Refusing to *replace* a decision is the easy
        half; the harder half is that this method also cannot close the question, because the question is
        closed by a decision and a proposal is not one.
        """
        segment = self._segment(segment_key)
        if segment is None:
            raise InvalidAnswer("no segment to propose about")
        kind = record.get("kind")
        if kind not in PROPOSAL_KINDS:
            raise InvalidAnswer("unknown proposal kind %r" % (kind,))
        answered_by = {"opening": "new_match", "end": "clip_end"}[kind]
        for decision in self.state["decisions"]:
            if (_num(decision.get("segment_start")) == _num(segment.get("start"))
                    and decision.get("kind") == answered_by and _governs(decision)):
                raise InvalidAnswer(
                    "a person has already answered where this %s boundary is, so a machine suggestion "
                    "cannot be filed over their answer" % kind)
        review = segment.setdefault("review", {}) or {}
        existing = [item for item in (review.get(PROPOSAL_REVIEW_KEY) or []) if isinstance(item, dict)]
        same_pending = next((item for item in existing if item.get("kind") == kind
                             and item.get("status") == PROPOSAL_PENDING
                             and abs(float(item.get("at") or 0.0) - float(record["at"])) <= INSTANT_EPSILON),
                            None)
        if same_pending is not None:
            # Same evidence, same instant: a refresh is not a new suggestion. Filing it again would make a
            # reviewer's ignored proposal reappear as news on every run.
            return dict(same_pending, unchanged=True)
        refused_before = next((item for item in existing if item.get("kind") == kind
                               and item.get("status") == PROPOSAL_REFUSED
                               and abs(float(item.get("at") or 0.0) - float(record["at"])) <= INSTANT_EPSILON),
                              None)
        if refused_before is not None:
            # A person said no to this instant. Re-filing it is not a refresh, it is a nag, and the next
            # automatic pass would have put back what the reviewer removed.
            return dict(refused_before, unchanged=True, already_answered=PROPOSAL_REFUSED)
        # A handle that is never reused. Counting the list would do for the first pass and then fail: two
        # consecutive refreshes both land on "opening:2" because the pending slot does not grow, and a card
        # left open on the first one would apply the second number. A per-row generation advances on every
        # filing and is stored, so it survives a save.
        generation = int(review.get(PROPOSAL_GENERATION_KEY) or 0)
        if not generation:
            generation = max([_proposal_serial(item.get("proposal_id")) for item in existing], default=0)
        review[PROPOSAL_GENERATION_KEY] = generation + 1
        record["proposal_id"] = "%s:g%d" % (kind, generation + 1)
        record["generation"] = generation + 1
        # A refresh displaces the live suggestion rather than overwriting it, so both the displaced record
        # and the number it carried stay on the row for the card that is still showing them.
        for item in existing:
            if item.get("kind") == kind and item.get("status") == PROPOSAL_PENDING:
                item["status"] = PROPOSAL_SUPERSEDED
                item["superseded_by"] = record["proposal_id"]
        existing.append(record)
        review[PROPOSAL_REVIEW_KEY] = existing
        segment["review"] = review
        self.state["log"].append({"action": "propose", "kind": kind,
                                  "fingerprint": "%s|proposal|%s" % (kind, _num(segment.get("start"))),
                                  "segment_start": _num(segment.get("start")), "at": record["at"]})
        return record

    def find_proposal(self, segment_start: Optional[float], kind: str, *,
                      proposal_id: Optional[str] = None,
                      status: Optional[str] = PROPOSAL_PENDING) -> Dict[str, Any]:
        """The one record an action means: by id when the caller has one, else the live one of that kind.

        Resolution never falls back to "the first of this kind". When a suggestion was already applied or
        refused, that is the answer, and silently acting on an older record of the same kind would let a
        reviewer's button do something other than what the card showed.
        """
        records = [item for item in self.proposals(segment_start) if item.get("kind") == kind]
        if proposal_id is not None:
            found = next((item for item in records if item.get("proposal_id") == proposal_id), None)
            if found is None:
                raise InvalidAnswer("no %s proposal %s on this row" % (kind, proposal_id))
            if status is not None and found.get("status") != status:
                state = found.get("status")
                if state == PROPOSAL_SUPERSEDED:
                    raise InvalidAnswer("proposal %s was superseded by %s, so this card is showing an "
                                        "offer that is no longer on the table"
                                        % (proposal_id, found.get("superseded_by") or "a newer suggestion"))
                raise InvalidAnswer("proposal %s was already %s" % (proposal_id, state))
            return found
        # ``status=None`` means "whatever is filed, newest first"; a named status means "only that".
        wanted = [item for item in records if status is None or item.get("status") == status]
        if status is None and records:
            return records[-1]
        live = wanted
        if live:
            return live[-1]
        settled = [item.get("status") for item in records]
        if not records:
            raise InvalidAnswer("no %s proposal on this row" % kind)
        raise InvalidAnswer("no %s proposal on this row is still waiting -- what is filed reads %s"
                            % (kind, " / ".join(str(name) for name in settled)))

    def proposals(self, segment_start: Optional[float]) -> List[Dict[str, Any]]:
        segment = self._segment({"start": segment_start})
        if segment is None:
            return []
        return [item for item in ((segment.get("review") or {}).get(PROPOSAL_REVIEW_KEY) or [])
                if isinstance(item, dict)]

    def set_proposal_status(self, segment_start: Optional[float], kind: str, status: str, *,
                            note: str = "",
                            record: Optional[Dict[str, Any]] = None,
                            proposal_id: Optional[str] = None) -> Dict[str, Any]:
        """Record what the person did with a suggestion, without becoming the thing that changes the plan."""
        if status not in PROPOSAL_STATUSES:
            raise InvalidAnswer("a proposal is %s, not %r" % (" / ".join(PROPOSAL_STATUSES), status))
        # A caller holding the record by identity can stamp it even after the row it belongs to has moved,
        # which is exactly what applying an opening proposal does: the start in the fingerprint is gone the
        # moment the start changes.
        if record is None:
            # Without an id the caller means the live suggestion, so an already-settled one is never stamped
            # twice; with an id the caller names a record and find_proposal reports what state it is in.
            record = self.find_proposal(segment_start, kind, proposal_id=proposal_id,
                                        status=None if proposal_id else PROPOSAL_PENDING)
        record["status"] = status
        record["operator_decision"] = status != PROPOSAL_PENDING
        if note:
            record["resolution_note"] = note
        self.state["log"].append({"action": "proposal:" + status, "kind": kind,
                                  "segment_start": segment_start, "fingerprint":
                                      "%s|proposal|%s" % (kind, segment_start), "at": record.get("at"),
                                  "proposal_id": record.get("proposal_id")})
        return record

    # ------------------------------------------------------------------------------------ writing

    def decide(self, item: Dict[str, Any], answer: str, *, note: str = "",
               end_at: Optional[float] = None, delta_seconds: Optional[float] = None,
               depends_on: Optional[Dict[str, Any]] = None,
               side: Optional[str] = None, bracket_player: Optional[str] = None,
               character_source: Optional[str] = None,
               context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record one answer and rebuild what it implies. Raises rather than storing a value silently.

        ``answer`` must be one of the question's own options, so a caller cannot invent a state the UI
        never offered; ``unsure`` is stored as :data:`DEFERRED` and applies nothing. A clip-end answer
        carries a resolved instant: ``confirmed`` adopts the end the machine derived, ``extend`` and
        ``shorten`` need a target, and every target is checked against the start, the tape length and the
        next candidate's opening before it is written.
        """
        segment = self._segment(item.get("segment") or {})
        if segment is None:
            raise InvalidAnswer("the segment this question was about is no longer in the project")
        options = list(item.get("options") or FOOTAGE_QUESTIONS.get(item.get("kind"), {}).get("options")
                       or [])
        if options and answer not in options:
            raise InvalidAnswer("%s is not one of this question's answers: %s" % (answer, ", ".join(options)))
        kind = item.get("kind")
        fingerprint = self.question_fingerprint(item)
        status = DEFERRED if answer == "unsure" else DECIDED
        decision = {"fingerprint": fingerprint, "kind": kind,
                    "segment_start": _num(segment.get("start")),
                    "subject": item.get("subject", ""), "answer": answer, "note": note,
                    "status": status,
                    "against": copy.deepcopy({key: (segment.get("recovery") or {}).get(key)
                                              for key in ("clip_end_at", "game_wins", "membership")}),
                    "depends_on": depends_on or {}}
        if side:
            decision["side"] = side
        if bracket_player:
            decision["bracket_player"] = bracket_player
        if character_source:
            decision["character_source"] = character_source
        if context:
            # What a statement was about when it was made (which two names, from which listing). The
            # projection needs it to rebuild the row, so it has to travel inside the journal.
            decision["context"] = copy.deepcopy(context)

        if status == DECIDED and kind == "clip_end":
            current = resolve_clip_end(segment, following=self._following(segment),
                                       duration=_num(self.project.get("duration")))
            if answer == "confirmed":
                if current["end"] is None:
                    raise InvalidAnswer("there is no end to confirm: the recording gave none, so this clip "
                                        "needs an extended or shortened value, not approval of a blank")
                decision["end_at"] = current["end"]
                decision["approved"] = True
            else:
                if end_at is None and delta_seconds is None:
                    raise InvalidAnswer("%s needs a target -- pass the end in seconds or a delta" % answer)
                base = current["end"]
                if end_at is not None:
                    target = _num(end_at)
                else:
                    if base is None:
                        raise InvalidAnswer("cannot nudge an end that was never published; give the "
                                            "end in seconds")
                    target = base + float(delta_seconds)
                if target is None:
                    raise InvalidAnswer("the end must be a number of seconds")
                value, clamp_note = validate_boundary_value(
                    start=_num(segment.get("start")), duration=_num(self.project.get("duration")),
                    following_start=_num(((self._following(segment) or {}).get("start"))), value=target)
                decision["end_at"] = value
                if clamp_note:
                    decision["note"] = " ".join(text for text in (note, clamp_note) if text)
                if base is not None:
                    decision["moved_seconds"] = round(value - base, INSTANT_PRECISION)
        if status == DECIDED and kind == "new_match" and answer == "accept_partial" and not note.strip():
            # "Export it anyway" is a decision about lost footage, and a decision with no reason attached is
            # indistinguishable later from nobody having looked.
            raise InvalidAnswer("accepting a partial opening needs a reason: say what is being given up "
                                "and why it is acceptable")
        if status == DECIDED and kind == "membership" and end_at is not None:
            decision["split_at"] = _num(end_at)

        decision["seq"] = len(self.state["log"]) + 1
        self.state["decisions"] = [d for d in self.state["decisions"]
                                   if d.get("fingerprint") != fingerprint] + [decision]
        self.state["log"].append({"action": "decide", "fingerprint": fingerprint, "kind": kind,
                                  "answer": answer, "status": status})
        self.recompute(segment)
        return decision

    def _following(self, segment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        segments = self._segments()
        position = next((i for i, item in enumerate(segments) if item is segment), None)
        if position is None:
            wanted = _num(segment.get("start"))
            position = next((i for i, item in enumerate(segments)
                             if _num(item.get("start")) == wanted), None)
        if position is None or position + 1 >= len(segments):
            return None
        return segments[position + 1]

    def undo(self, fingerprint: str) -> bool:
        """Withdraw an answer and rebuild every consequence it had applied."""
        before = len(self.state["decisions"])
        victim = self.decision(fingerprint)
        self.state["decisions"] = [d for d in self.state["decisions"]
                                   if d.get("fingerprint") != fingerprint]
        if len(self.state["decisions"]) == before:
            return False
        self.state["log"].append({"action": "undo", "fingerprint": fingerprint,
                                  "kind": (victim or {}).get("kind")})
        if victim is not None:
            segment = self._segment({"start": victim.get("segment_start")})
            if segment is not None:
                self.recompute(segment)
            else:
                self.recompute_all()
        else:
            self.recompute_all()
        return True

    def invalidate(self, segment_start: float, reason: str) -> List[str]:
        """Mark every decision about a segment as resting on changed evidence, and withdraw its force."""
        touched = []
        for decision in self.state["decisions"]:
            if _num(decision.get("segment_start")) == _num(segment_start) and _governs(decision):
                decision["status"] = INVALIDATED
                decision["invalidated_reason"] = reason
                touched.append(decision.get("fingerprint", ""))
        if touched:
            self.state["log"].append({"action": "invalidate", "start": _num(segment_start),
                                      "reason": reason, "count": len(touched)})
            segment = self._segment({"start": segment_start})
            if segment is not None:
                self.recompute(segment)
        return touched

    # --------------------------------------------------------------------------------- projecting

    def recompute(self, segment: Dict[str, Any]) -> Dict[str, Any]:
        """Rebuild the segment's derived review fields from the decisions that still govern.

        The order matters and is the whole design: the machine's reading is applied first (it is the
        fallback when nobody has spoken), then every governing human answer in the order it was given.
        A deferred or invalidated answer is skipped, which is why undoing a clip end returns the clip to
        the padded conclusion rather than leaving ``210.0`` authoritative forever.
        """
        review = segment.setdefault("review", {"decisions": [], "orientation": machine_sides(segment)})
        for key in DERIVED_REVIEW_KEYS:
            review.pop(key, None)
        review["orientation"] = machine_sides(segment)
        governing = sorted((d for d in self.decisions_for(segment.get("start")) if _governs(d)),
                           key=lambda d: (d.get("seq") or 0, str(d.get("fingerprint"))))
        for decision in governing:
            self._apply_consequence(segment, decision)
        review["decisions"] = [d["fingerprint"] for d in governing]
        # The pairing and the orientation are two records about one fact, so the row shows them in
        # agreement whichever order they were made in.
        if review.get("bracket") and any(d.get("kind") == "orientation" for d in governing):
            review["bracket"]["orientation"] = review.get("orientation")
        return review

    def recompute_all(self) -> int:
        for segment in self._segments():
            self.recompute(segment)
        return len(self._segments())

    @staticmethod
    def _apply_consequence(segment: Dict[str, Any], decision: Dict[str, Any]) -> None:
        review = segment.setdefault("review", {})
        kind, answer = decision.get("kind"), decision.get("answer")
        if kind == "new_match":
            review["grouping"] = answer
            if answer == "accept_partial":
                review["openings"] = {"approved_partial": True, "note": decision.get("note", ""),
                                      "from_decision": decision.get("fingerprint")}
        elif kind == "clip_end":
            ends = {"end": decision.get("end_at"), "basis": "human decision at review",
                    "answer": answer, "from_decision": decision.get("fingerprint")}
            if decision.get("approved"):
                ends["approved_by_human"] = True
            review["ends"] = ends
        elif kind == "orientation":
            review["orientation"] = answer
        elif kind == "character":
            side = decision.get("side") or "p1"
            characters = review.setdefault("characters", {})
            # The provenance the operator used is kept, because "remembered" and "human" mean different
            # things downstream and laundering one into the other is how a habit ends up quoted as tape.
            characters[side] = {"player": decision.get("bracket_player", ""),
                                "character": answer,
                                "source": decision.get("character_source") or "human"}
        elif kind == "membership":
            review["membership"] = answer
            if decision.get("split_at") is not None:
                review["split_at"] = decision["split_at"]
        elif kind == "bracket":
            context = decision.get("context") or {}
            review["bracket"] = {"players": list(context.get("players") or []),
                                 "label": context.get("label", ""),
                                 "orientation": context.get("orientation"),
                                 "source": context.get("source", "start.gg")}

    def save(self, path: Path) -> Path:
        self.recompute_all()
        Path(path).write_text(json.dumps(self.project, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
        return Path(path)


def validate_boundary_value(*, start: Optional[float], value: float,
                           duration: Optional[float] = None,
                           following_start: Optional[float] = None) -> Tuple[float, Optional[str]]:
    """One boundary instant, checked against the tape and the neighbours before anything stores it.

    A reversed bound and a collision with the next candidate are refused outright: the first is not a
    clip and the second exports somebody else's footage. Running past the end of the recording is clamped
    and reported, because the operator's intent (stop at the end) is clear and only the number is wrong.

    What comes back is the instant at storage precision, never at display precision. A bound rounded for
    presentation is a bound that has moved, and this function is asked "may the clip stop here", so a
    coarser number than the one the neighbours carry manufactures an overlap out of two timestamps that
    agree to the microsecond.
    """
    number = _num(value)
    if number is None:
        raise InvalidAnswer("a boundary has to be a number of seconds, not %r" % (value,))
    if is_nonfinite(number):
        raise InvalidAnswer("a boundary has to be a real instant in seconds, not %s -- it cannot be "
                            "compared with anything, so it cannot be checked or stored"
                            % ("NaN" if number != number else "an infinity"))
    if number < 0.0:
        raise InvalidAnswer("a boundary cannot sit before the start of the recording (%s)"
                            % instant_text(number))
    if start is not None and number <= _num(start) + INSTANT_EPSILON:
        raise InvalidAnswer("boundary %s is not after the match start %s"
                            % (instant_text(number, against=_num(start)), instant_text(start)))
    if following_start is not None and number > _num(following_start) + INSTANT_EPSILON:
        raise InvalidAnswer("boundary %s passes the next candidate's opening at %s, which would put "
                            "another match's footage in this clip"
                            % (instant_text(number, against=_num(following_start)),
                               instant_text(following_start, against=number)))
    note = None
    if duration is not None and number > _num(duration) + INSTANT_EPSILON:
        note = ("clamped to the recording length %s: the value asked for (%s) is past the end of the "
                "tape" % (instant_text(duration), instant_text(number, against=duration)))
        number = _num(duration)
    return canonical_instant(number), note


# --------------------------------------------------------------------------------------------- the queue

#: A boundary in a question's own words. ``options`` are the values :meth:`ReviewStore.decide` accepts;
#: ``labels`` are the buttons. Keeping them apart is what lets a UI say "Extend it" while the record keeps
#: ``extend`` and a test can assert the pair without touching a widget.
FOOTAGE_QUESTIONS = {
    "new_match": {
        "ask": "Does a new match start here?",
        "options": ["new_match", "same_match_next_game", "other_adjust", "accept_partial", "unsure"],
        "labels": ["New match", "Same match, next game", "Other / adjust",
                   "Start here anyway (partial)", "Unsure"],
        "boundary": "start",
    },
    "clip_end": {
        "ask": "Does this clip actually end where it does, with the winner visible before it stops?",
        "options": ["confirmed", "extend", "shorten", "unsure"],
        "labels": ["Ends here", "Extend it", "Shorten it", "Unsure"],
        "boundary": "end",
    },
    "orientation": {
        "ask": "Which side of the screen is the first-named player on?",
        "options": ["as-listed", "swapped", "unsure"],
        "labels": ["As listed", "Swapped", "Unsure"],
        "boundary": "set",
    },
    "membership": {
        "ask": "Do these games all belong to the same set?",
        "options": ["yes", "no_split", "unsure"],
        "labels": ["Same set", "Not the same set", "Unsure"],
        "boundary": "junction",
    },
}

#: The answers that say "yes, this instant is where the match opens". ``accept_partial`` is deliberately
#: absent from this list: the button means "start here anyway", which is a person agreeing to ship lost
#: footage, and agreeing to a loss is not evidence that the boundary is right.
START_CONFIRMING_ANSWERS = ("new_match", "other_adjust")

#: The answers that put the clip's stopping point where the operator says it goes.
END_CONFIRMING_ANSWERS = ("confirmed", "extend", "shorten")

#: The nudge a reviewer gets when a boundary is close but not right. Seconds, not samples: this UI is for a
#: person watching a clip, and the padding constant belongs to the pipeline, not to the mouse wheel.
ADJUST_STEPS_SECONDS = (0.1, 1.0, 5.0)


#: How far behind a candidate the backward walk looks, and how much slop a frozen clock may have. A round
#: lasts tens of seconds and the saved series is sampled at ~1 s, so both are deliberately generous: the
#: cost of looking too far is a longer explanation, the cost of looking too little is a wrong boundary.
OPENING_SCAN_SECONDS = 180.0
OPENING_CLOCK_SLOP = 1.0


def _clock_pairs(rows: Sequence[Any]) -> List[Tuple[float, int]]:
    """(timestamp, clock) for every row that actually shows a round clock, in tape order.

    Row shapes differ by producer -- the classifier writes ``round_clock``, a crop answer writes ``clock``
    -- and a row whose clock is unreadable is *absence of evidence*, not a zero. Dropping those here is
    what stops an unlit frame from looking like the clock jumped.
    """
    pairs: List[Tuple[float, int]] = []
    for row in rows or []:
        at = _num(row.get("timestamp", row.get("at")))
        value = row.get("round_clock", row.get("clock"))
        if at is None or value is None:
            continue
        try:
            pairs.append((float(at), int(value)))
        except (TypeError, ValueError):
            continue
    return sorted(pairs)


# --------------------------------------------------------------------- what a person saw, said in words

#: What somebody can say about a stretch of tape after watching it, when the pipeline had nothing to say
#: first. These are dispositions about footage, never answers to a review question: a question is about a
#: boundary the project already proposes, and a disposition is about stretch that has no proposal. Keeping
#: them apart is what stops "I looked at it" from being recorded as "the boundary was reviewed".
FOOTAGE_DISPOSITIONS = {
    "play_new_match": "this is play, and a match opens in it",
    "play_same_match": "this is play belonging to the match beside it",
    "partial": "a match is here, but only part of it survives on this tape",
    "not_play": "no match play here (menus, replays, idle tape, a dropped stream)",
    "unsure": "watched it, cannot tell",
}


def annotation_record(kind, start, end=None, *, boundary=None, view="", note="", observer="",
                      project=None):
    """One typed record of what somebody saw in a stretch of tape, validated before anything stores it.

    The exact instant an opening happens is the whole point of a reference pass, so a record claiming that a
    match opens, without a timestamp, is refused rather than stored with a guess. What the watcher could see
    at that instant goes in ``view`` in their own words, and ``note`` survives as written. A record moves no
    boundary, accepts no boundary and validates nothing: it is an observation, filed where a later pass can
    find it.
    """
    if kind not in FOOTAGE_DISPOSITIONS:
        raise InvalidAnswer("nobody can say %r about footage: the dispositions are %s"
                            % (kind, ", ".join(sorted(FOOTAGE_DISPOSITIONS))))
    from_ = _num(start)
    if from_ is None or is_nonfinite(start):
        raise InvalidAnswer("an annotation has to start at a real instant in seconds, not %r" % (start,))
    if from_ < 0.0:
        raise InvalidAnswer("an annotation cannot sit before the start of the recording (%s)"
                            % instant_text(from_))
    until = _num(end)
    if end is not None and (until is None or is_nonfinite(end)):
        raise InvalidAnswer("the end of an annotated stretch has to be a number of seconds, not %r" % (end,))
    if is_nonfinite(boundary):
        raise InvalidAnswer("an opening instant has to be a real number of seconds, not %r" % (boundary,))
    if until is not None and until <= from_ + INSTANT_EPSILON:
        raise InvalidAnswer("an annotated stretch has to end after it starts (%s is not after %s)"
                            % (instant_text(until, against=from_), instant_text(from_, against=until)))
    opening = _num(boundary)
    if kind == "play_new_match" and opening is None:
        raise InvalidAnswer("saying a match opens in a stretch has to name the exact instant it opens: "
                            "without a timestamp there is nothing for a reference pass to compare against")
    if opening is not None and not (from_ - INSTANT_EPSILON <= opening
                                    <= (until if until is not None else from_) + INSTANT_EPSILON):
        raise InvalidAnswer("the opening %s is outside the annotated stretch %s-%s"
                            % (instant_text(opening), instant_text(from_),
                               instant_text(until if until is not None else from_)))
    duration = _num((project or {}).get("duration"))
    if duration is not None and (from_ > duration + INSTANT_EPSILON
                                 or (until is not None and until > duration + INSTANT_EPSILON)):
        raise InvalidAnswer("the annotated stretch %s-%s runs past the end of the recording (%s)"
                            % (instant_text(from_),
                               instant_text(until if until is not None else from_),
                               instant_text(duration)))
    # Stored at source precision, because a reference record exists to be compared against: a boundary
    # rounded here is a boundary the reference got wrong by up to half a millisecond before anybody has
    # looked at a frame.
    return {"kind": kind, "from": canonical_instant(from_),
            "to": None if until is None else canonical_instant(until),
            "boundary_at": None if opening is None else canonical_instant(opening),
            "seconds": None if until is None else round(until - from_, INSTANT_PRECISION),
            "view": str(view or ""), "note": str(note or ""), "observer": str(observer or ""),
            # What the record is not, said inside the record: an annotation is an observation about footage
            # and never a claim that a boundary was reviewed, accepted or validated.
            "is_not_a_boundary_decision": True}


def annotate_footage(project, kind, start, end=None, **kwargs) -> Dict[str, Any]:
    """Append :func:`annotation_record` to a project, refusing to store an invalid one."""
    record = annotation_record(kind, start, end, project=project, **kwargs)
    block = project.setdefault("review", {"version": REVIEW_VERSION, "decisions": [], "log": []})
    if not isinstance(block, dict):
        raise InvalidAnswer("a project's review block has to be a mapping to hold annotations")
    existing = [item for item in (block.get("annotations") or []) if isinstance(item, dict)]
    record["sequence"] = len(existing) + 1
    block["annotations"] = existing + [record]
    return record


def footage_coverage(project, *, annotations=None) -> Dict[str, Any]:
    """How much of a recording somebody has said something about -- and how much nobody has looked at.

    The honest part is the remainder. A stretch with no record is *unwatched*, and reporting only the
    stretches somebody did annotate is how a recording comes to be described as reviewed because the numbers
    on the page happened to mention the parts that were.
    """
    duration = _num(project.get("duration"))
    records = [item for item in (annotations if annotations is not None
                                 else ((project.get("review") or {}).get("annotations") or []))
               if isinstance(item, dict) and _num(item.get("from")) is not None]
    covered = merge_intervals([(float(item["from"]),
                                # A record with no end was said as "from here on", so it reaches the end of
                                # the tape rather than being dropped for want of a bound.
                                float(item["to"]) if _num(item.get("to")) is not None else duration)
                               for item in records]) if duration else []
    watched = sum(float(to) - float(start) for start, to in covered) if duration else 0.0
    return {"records": len(records),
            "annotated_intervals": [{"from": float(start), "to": (None if to is None else float(to))}
                                    for start, to in covered],
            "seconds_annotated": round(watched, 6),
            "seconds_unwatched": None if duration is None else round(duration - watched, 6),
            # The stretches themselves, not only their total: a pass that is 90 % done still has to show
            # which 10 % nobody has described, or the number invites being read as a finished job. Said in
            # the vocabulary of a description pass, because "no candidate claims this" is a different fact
            # from "nobody has watched this" and neither one implies the other.
            "unwatched_intervals": [{"from": row["from"], "to": row["to"], "seconds": row["seconds"],
                                     "edge": row["edge"],
                                     "what_it_is": "a stretch of the recording nobody has described yet",
                                     "how_it_would_be_known": "watch it and record a disposition"}
                                    for row in (uncovered_intervals(covered, duration) if duration
                                                 else [])],
            "whole_recording_annotated": bool(duration) and len(covered) == 1 and covered[0][0] <= 1e-9
            and covered[0][1] is not None and covered[0][1] >= duration - 1e-9}


def reference_pass_document(records, *, recording, duration, observer="", procedure="",
                            prior_exposure="", pass_label="", rejected=()):
    """A watched-through description of a recording, in a form that cannot be mistaken for proof.

    Refusing to take a project argument is what stops a reference pass *reading* the boundaries it will
    later be compared against. It is not what makes the pass independent: that question is who watched,
    how, and whether they had already seen the machine's answers. A pass that cannot say is not
    disqualified -- it is unevaluated, and it records which of the three it cannot say. And nothing in this
    document may come from the detector: no expected number of matches, no boundary, no tolerance, because
    a reference that inherits its shape from the thing it is meant to check is not a second opinion.
    """
    who = (str(observer or "").strip() or "not recorded")
    method = (str(procedure or "").strip() or "not recorded")
    exposure = (str(prior_exposure or "").strip() or "not recorded")
    missing = [label for label, value in (("who watched", who), ("how they watched", method),
                                          ("what they had already seen", exposure))
               if value == "not recorded"]
    return {"kind": "footage-annotations", "schema": 2, "pass_label": str(pass_label or ""),
            "recording": str(recording), "duration": canonical_instant(duration),
            "records": records, "rejected": list(rejected),
            "coverage": footage_coverage({"duration": duration}, annotations=records),
            "method": {"observer": who, "procedure": method, "prior_exposure": exposure},
            # ``independence_claimable`` is about the record, not the watcher: an unrecorded exposure does
            # not mean somebody peeked, it means nobody wrote down whether they could have.
            "independence_claimable": not missing,
            "independence_unclaimed_because": None if not missing else (
                "the pass did not record %s, so nothing here can say whether the watcher had already seen "
                "the boundaries it would be compared against. That makes the pass unevaluated, not wrong: "
                "filling the field in is all that is missing" % " and ".join(missing)),
            "no_expected_count_from_the_detector": True,
            "independently_validated": False,
            "what_this_is": "a manual description of footage, kept outside any project",
            "what_this_is_not": "a validated reference, a gate result, or a count of matches the recording "
                                "has to contain",
            "inference_sent": False, "media_rendered": False}


def compare_reference(reference, boundaries, *, tolerance_seconds=None):
    """Where a reference pass and a project's boundaries agree, and where they do not.

    Agreement is a measurement, and it is reported as one: matched pairs, reference-only openings,
    project-only openings, and the size of every miss. What it is not is validation. Both sides were
    produced by people -- the reviewer and the watcher -- and two people agreeing about one recording is
    still two people, so ``validation`` comes back false with its reason attached at every tolerance and
    for every count. A future verified subset needs an independent verifier and rules frozen before the
    comparison; this function cannot produce either, and must not be quoted as if it could.

    The tolerance is stated by whoever runs the comparison and is never read from the project. A threshold
    taken from the thing being scored is a conclusion waiting to be widened.
    """
    if tolerance_seconds is None:
        raise InvalidAnswer("a comparison needs the tolerance stated by the person running it: taking one "
                            "from the project under test is how a threshold becomes a conclusion")
    tolerance = _num(tolerance_seconds)
    if tolerance is None or is_nonfinite(tolerance) or not tolerance > 0:
        raise InvalidAnswer("the tolerance has to be a positive number of seconds, not %r"
                            % (tolerance_seconds,))
    truth = sorted(value for value in (
        _num(row.get("boundary_at")) for row in ((reference or {}).get("records") or [])
        if isinstance(row, dict) and row.get("kind") == "play_new_match") if value is not None)
    proposed = sorted(value for value in (_num(item.get("start")) for item in boundaries or []
                                          if isinstance(item, dict)) if value is not None)
    matched, unmatched = [], list(truth)
    paired_starts = set()
    for start in proposed:
        hit = next((value for value in unmatched if abs(value - start) <= tolerance), None)
        if hit is None:
            continue
        unmatched.remove(hit)
        paired_starts.add(start)
        matched.append({"reference_at": hit, "project_at": start,
                        "difference_seconds": round(start - hit, INSTANT_PRECISION)})
    return {"tolerance_seconds": tolerance,
            "reference_openings": len(truth), "project_boundaries": len(proposed),
            "matched": matched,
            "in_reference_not_project": unmatched,
            "in_project_not_reference": [value for value in proposed if value not in paired_starts],
            "agreement_fraction": (round(len(matched) / float(len(truth)), 6) if truth else None),
            "validation": False,
            "validation_reason": ("two readings of one recording agreeing is a measurement of agreement, "
                                  "not a validation: an independent verifier and rules frozen before the "
                                  "comparison are what a validated boundary needs, and neither is here"),
            "inference_sent": False}


# ------------------------------------------------------------------ opening recovery, on canonical evidence

#: Which screens the tracker treats as a game opening. Imported rather than restated here: two tools with
#: two state lists end up disagreeing about the same recording, and the pipeline's list is the one that has
#: been held against this footage.
GAME_OPENING_STATES = tuple(sorted(bg.GAME_OPENING_STATES))
#: The coarse label has been measured calling an in-game frame ``stage_intro``, so an intro is the weakest
#: member of that list and is reported as weak rather than promoted to proof.
WEAK_GAME_INTRO_STATES = ("stage_intro",)
#: Screens that mean somebody was at the machine. The canonical set-cue policy weights these "preferred"
#: for a new pairing, and preferred is not sufficient -- nothing in that policy is.
PAIRING_CONTEXT_STATES = tuple(state for state, weight in sorted(bg.SET_CUE_WEIGHT.items())
                               if weight == "preferred")
#: A round's own opening banner. It bounds a round, and a round is inside a game: it cannot open a game.
ROUND_OPENING_STATES = ("round_call",)
#: Play on the screen.
PLAY_STATES = ("gameplay", "ko", "knockout", "match_end", "replay", "result")
#: Station menus: context, not play. Deliberately not derived from the corroborating weights, because
#: ``character_select`` is one of those and is a game-opening screen before it is a menu.
MENU_STATES = ("post_match_menu", "lobby", "rounds_select", "mode_select", "menu")
#: A row whose state nobody recognised proves nothing about the tape, so it may sit inside a run and never
#: carry one.
UNKNOWN_STATE = "unknown"

#: The four things an opening question can actually be about, in the order the review has to answer them.
#: Collapsing these is what let a round banner be reported as a game opening.
OPENING_LEVELS = ("pre_pairing_context", "game_opening_context", "round_opening", "set_membership")


def classify_opening_state(state):
    # type: (str) -> str
    """Which opening question a screen state speaks to, using the canonical vocabulary.

    ``neutral`` is the answer for play and for anything unrecognised: play says the candidate is inside
    something, and an unclassified frame says nothing at all.
    """
    if state in PAIRING_CONTEXT_STATES:
        return "pre_pairing_context"
    if state in GAME_OPENING_STATES:
        return "game_opening_context"
    if state in ROUND_OPENING_STATES:
        return "round_opening"
    if state in MENU_STATES:
        return "pre_pairing_context"
    if state in PLAY_STATES:
        return "play"
    return "neutral"


def _state_rows(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """Normalised saved sequence rows -- timestamp, state, clock, opening cue, combat phase -- in tape order.

    The state arrives under different keys depending on which stage wrote the row (``screen_state`` from
    the tracker, ``state`` from the classifier), and an absent clock is absence, not zero: a row with no
    readable round clock cannot make a claim about one.

    A row that carries no facts at all is a row that carries **prose**, and the prose is what the tracker's
    own extractor reads. Without that step the reviewer sees the coarse state word and nothing else: on the
    saved BB9 timeline these 2,019 rows expose 33 clock readings and **no** opening cue as stored fields,
    while the canonical extractor reads 1,091 clocks, 40 READY/FIGHT banners and 17 ROUND-number banners
    out of the same file. Every judgement about where a round opened was being made on the 1.6% of the
    evidence that happened to arrive pre-parsed. Parsing stays in :mod:`boundary_games`, one place for one
    vocabulary; this only asks it.
    """
    normalised: List[Dict[str, Any]] = []
    for row in rows or []:
        at = _num(row.get("timestamp", row.get("at")))
        if at is None:
            continue
        # Facts are honoured per field, never per row: a tracker row that states a clock but no phase still
        # has to be asked what that frame shows, and a vision answer that states ``round_clock: None`` has
        # read the frame and found no digits. ``parse_round_facts`` already keeps stated fields ahead of
        # prose for exactly that reason, so it is safe to run on a row that carries some of them.
        fact = row if row.get("combat_phase") in bg.ROUND_PHASES else bg.parse_round_facts(
            dict(row, timestamp=float(at)))
        normalised.append({
            "at": float(at),
            "state": str(row.get("screen_state") or row.get("state") or UNKNOWN_STATE).strip().lower()
                     or UNKNOWN_STATE,
            "clock": fact.get("round_clock"),
            # The cue word itself, not a boolean: which banner was drawn (READY or FIGHT) is the difference
            # between "a round is starting" and "this round has begun", and a reviewer reading the evidence
            # is owed the distinction the frame states.
            "round_open_cue": fact.get("round_open_cue"),
            "round_call": fact.get("round_call"),
            # What the frame says about combat, which the state word cannot: an arena is drawn behind a
            # ROUND banner, behind a pause menu and in the first second of a live round alike.
            "combat_phase": fact.get("combat_phase") or bg.ROUND_PHASE_UNKNOWN,
            "combat_phase_basis": list(fact.get("combat_phase_basis") or []),
            "combat_phase_notes": list(fact.get("combat_phase_notes") or []),
            "id": row.get("id"),
        })
    return sorted(normalised, key=lambda item: item["at"])


def frame_phase(row) -> str:
    """The combat phase of one saved row, using the shared reading rather than a review-only guess."""
    return bg.row_combat_phase(row) if isinstance(row, dict) else bg.ROUND_PHASE_UNKNOWN


def _canonical_game_marks(games: Optional[Sequence[Dict[str, Any]]], at: float) -> Dict[str, Any]:
    """What the pipeline's own game list says around this instant.

    Cross-checking the recovered candidate against it is the point: an opening sequence that agrees with a
    game the tracker already built is one story, and a candidate that sits inside an existing game is
    another. Neither is allowed to quietly overwrite the other.
    """
    starts = sorted(float(game["start"]) for game in games or [] if _num(game.get("start")) is not None)
    before = [value for value in starts if value <= at + 1e-9]
    after = [value for value in starts if value > at + 1e-9]
    return {"game_starts_before": before[-1] if before else None,
            "game_starts_after": after[0] if after else None,
            "candidate_is_a_canonical_game_start": any(abs(value - at) <= 0.5 for value in starts),
            "games_considered": len(starts)}


def recover_opening(rows, at, *, games=None, clock_start=ROUND_CLOCK_OPENING,
                    scan_seconds=OPENING_SCAN_SECONDS, slop=OPENING_CLOCK_SLOP,
                    cues: Sequence[Any] = ()):
    """What the saved sequence says about where this candidate's opening was -- as hypotheses, ranked.

    :func:`start_opening` restates the pipeline's own edge note. This reads the sequence rows behind a
    candidate and reports what each part of it can and cannot support, on the vocabulary the tracker
    already uses (``boundary_games.GAME_OPENING_STATES`` and ``SET_CUE_WEIGHT``) rather than a parallel
    review-only one, because the two would eventually disagree about the same recording.

    The hierarchy is the whole point, and it is why the previous version of this function was wrong. A
    round's banner is not a game opening; a game opening is not a set start; and stage and character
    selection happen *between the games of one set*, so on their own they are neutral for set membership
    -- that is the canonical policy, measured on this footage, and it is not improved by being restated
    more confidently here. Two consequences the code has to honour:

    * a run of pre-round screens yields a **candidate instant to look at**, never a resolution, and the
      level it speaks to is named with it;
    * the **absence** of a select or controller screen behind a candidate proves nothing: a new game or a
      new set can begin with no such row on disk at all, so ``play_behind`` is reported as what it is
      (play was continuing) and never as "not a new game".

    The clock is only ever a bound on the candidate: inside a round it falls, so a value below
    ``clock_start`` says play was already running there. Nothing subtracts it to invent an opening, and
    nothing here writes a boundary -- the caller's reviewer decides.

    ``rows`` are saved sequence rows and ``games`` the canonical per-game evidence, both from disk; never
    from an answer key, because a boundary found from last year's corrected timestamps was not found.
    """
    wanted = float(at)
    series = _parsed_rows(rows)
    window = [row for row in series if wanted - scan_seconds - 1e-9 <= row["at"] <= wanted + slop]
    at_candidate = max((row for row in window if row["at"] <= wanted + slop),
                       key=lambda row: row["at"], default=None)
    behind = [row for row in window if row["at"] < wanted - 1e-9]
    clock = at_candidate["clock"] if at_candidate else None
    if clock is None:
        for row in reversed(behind + ([at_candidate] if at_candidate else [])):
            if row["clock"] is not None and abs(row["at"] - wanted) <= slop:
                clock = row["clock"]
                break
    result: Dict[str, Any] = {
        "at": wanted, "status": "insufficient_evidence", "candidates": [], "levels_seen": [],
        "earliest_candidate": None, "earliest_candidate_level": None, "basis": "",
        "candidate_state": at_candidate["state"] if at_candidate else None,
        "clock_at_candidate": clock,
        "rows_in_window": len(window), "sequence": [(row["at"], row["state"]) for row in window],
        # Whether the frame immediately behind the candidate shows the round being *played*. The state word
        # cannot answer that -- Tekken draws the arena and its timer behind a ROUND banner and behind an
        # open pause menu exactly as it draws them mid-round -- and reading it as though it could is what
        # called 785.785 and 2722.720 evidence of play continuing. Only an active frame continues a round.
        "play_continues_behind_candidate": bool(behind)
        and frame_phase(behind[-1]) == bg.ROUND_PHASE_ACTIVE,
        "missing_evidence_window": [round(wanted - scan_seconds, 6), round(wanted, 6)]
        if not window else None,
        "what_this_cannot_say": "an opening sequence is a place to look. It does not certify a game, a set, "
                                "or that anything is missing between here and the candidate.",
        "set_membership": {"decided": False,
                           "why": "a set is decided by one side's game wins reaching the target, not by "
                                  "opening screens; stage and character selection happen between games of "
                                  "the same set, so they are neutral here"},
        "canonical_games_around": _canonical_game_marks(games, wanted),
        "scan_seconds": scan_seconds, "inference_sent": False}
    if clock is not None and clock < clock_start:
        result["candidate_is_seconds_into_a_running_round"] = round(float(clock_start) - float(clock),
                                                                   INSTANT_PRECISION)
        result["basis"] = ("the round clock reads %d at the candidate, so a round was already running "
                           "there: the clip opens %.1f s into it" % (clock, clock_start - clock))

    # Walk backward over one contiguous stretch of not-yet-played footage. What stops the walk is a frame
    # that shows the round actually being fought: the row behind the candidate that proves the clip cuts
    # live play. A ROUND banner, a READY cue and an open pause menu are all arena frames that prove the
    # opposite -- they are part of what an opening *is* -- so the walk used to break on them and the
    # candidate's own opening vanished from the report.
    run: List[Dict[str, Any]] = []
    for row in reversed(behind):
        if frame_phase(row) == bg.ROUND_PHASE_ACTIVE:
            if run:
                result["last_play_before_run"] = row["at"]
            break
        run.append(row)
    run.reverse()
    result["rows_in_run"] = len(run)
    result["unknown_rows_in_run"] = sum(1 for row in run if row["state"] == UNKNOWN_STATE)

    by_level: Dict[str, List[Dict[str, Any]]] = {}
    for row in run:
        # A frame the state word calls play but the phase calls an opening is an opening, and it belongs at
        # that level rather than being dropped for the sin of its coarse label.
        level = ("round_opening" if frame_phase(row) == bg.ROUND_PHASE_OPENING
                 else classify_opening_state(row["state"]))
        if level in ("neutral", "play"):
            continue
        by_level.setdefault(level, []).append(row)
    for cue in cues or []:
        if isinstance(cue, dict) and cue.get("round_open_cue"):
            at_cue = _num(cue.get("timestamp", cue.get("at")))
            if at_cue is not None and wanted - scan_seconds <= at_cue <= wanted:
                by_level.setdefault("round_opening", []).append(
                    {"at": at_cue, "state": "round_open_cue", "clock": None, "id": cue.get("id")})
    for row in run:
        if row["round_open_cue"]:
            by_level.setdefault("round_opening", []).append(dict(row, state="round_open_cue"))

    for level, claimed in (("game_opening_context", "a new game is consistent with this screen, and stage "
                            "or character selection also happens between games of one set, so it cannot "
                            "cut a set"),
                           ("pre_pairing_context", "somebody was at the machine, which is what a new "
                            "pairing looks like; the canonical policy weights these cues preferred for a "
                            "set cut and never sufficient alone"),
                           ("round_opening", "a round opened here. A round is inside a game, so this "
                            "bounds a round and says nothing about which game or set")):
        found = sorted(by_level.get(level, []), key=lambda row: row["at"])
        if not found:
            continue
        weakest = all(row["state"] in WEAK_GAME_INTRO_STATES for row in found)
        result["candidates"].append({
            "level": level,
            "at": round(found[0]["at"], 6),
            "to": round(found[-1]["at"], 6),
            "rows": [(round(row["at"], 6), row["state"]) for row in found],
            "supported_by": sorted({row["state"] for row in found}),
            "does_not_prove": claimed,
            "strength": "weak" if weakest else "context",
            "gaps_in_evidence": [round(a["at"], 6) - round(b["at"], 6)
                                 for a, b in zip(found[1:], found[:-1])
                                 if a["at"] - b["at"] > 2.0 * max(scan_seconds / 60.0, 1.0)]})
    result["candidates"].sort(key=lambda item: item["at"])
    result["levels_seen"] = [item["level"] for item in result["candidates"]]
    if result["candidates"]:
        strongest = {"pre_pairing_context": 0, "game_opening_context": 1, "round_opening": 2}
        best = sorted(result["candidates"], key=lambda item: (strongest.get(item["level"], 3),
                                                              item["at"]))[0]
        result["earliest_candidate"] = min(item["at"] for item in result["candidates"])
        result["earliest_candidate_level"] = next(item["level"] for item in result["candidates"]
                                                 if item["at"] == result["earliest_candidate"])
        result["strongest_candidate"] = best
        result["status"] = "candidate_found"
    elif not window:
        result["status"] = "insufficient_evidence"
        result["basis"] = ("no saved row lies within %.1f s behind this candidate, so the sequence cannot "
                           "say anything about where it opened" % scan_seconds)
    elif result["play_continues_behind_candidate"]:
        result["status"] = "no_earlier_candidate"
        result["basis"] = ("the row behind this candidate is play, so the candidate sits inside something "
                           "already running; where that something began is not answered by these rows, and "
                           "the absence of a select screen is not evidence that no new game or set started")
    else:
        result["status"] = "insufficient_evidence"
        result["basis"] = ("%d row(s) behind the candidate carry %s states, none of which speak to an "
                           "opening" % (len(behind), sorted({row["state"] for row in behind})[:4]))
    if at_candidate and at_candidate["state"] in MENU_STATES + PAIRING_CONTEXT_STATES:
        result["basis"] = "the candidate row is itself on a station screen, before any play"
    if result["canonical_games_around"]["candidate_is_a_canonical_game_start"]:
        result["agrees_with_canonical_game"] = True
        result["basis"] = ((result["basis"] + "; ") if result["basis"] else "") + \
            "the pipeline's own game list opens a game at this instant"
    # The round's own opening, recovered from the phase of each sampled frame. Asked separately from the
    # context hierarchy above because it answers a different question: those say what kind of screen stands
    # behind the candidate, this says how much of *this* round's start the clip leaves out. It is a
    # proposal with an interval on it, never a boundary, and it is computed by the layer that owns round
    # structure rather than here, so the review cannot develop its own idea of what an opening is.
    result["round_opening"] = bg.opening_sequence(series, wanted, scan_seconds=scan_seconds,
                                                  clock_start=clock_start)
    return result


#: A reading that claims nothing about combat -- here, a clock sitting at its opening value. It appears in
#: the list so the reviewer sees every sighting, and it is excluded from the dispute test below because a
#: claim that asserts nothing cannot disagree with anything.
PHASE_NO_CLAIM = "no_claim"


#: How many sequence listings keep their parsed rows and phase index. The cap is there because a long-lived
#: app session opens project after project, and an unbounded cache is a leak with good manners.
_PARSE_CACHE_LIMIT = 4
_parse_cache: List[Any] = []


def _rows_digest(rows: Sequence[Any]) -> str:
    """A content fingerprint of a saved sequence listing.

    Object identity answers "same listing?" and not "same bytes?", and the difference is not theoretical: an
    evidence row normalised in place keeps its id, and a cache keyed on identity keeps serving a phase parsed
    from prose that no longer exists. Re-reading the rows is cheap next to re-deriving them -- about 7 ms of
    hashing against about 160 ms of parsing for this recording's 2,019 frames -- so the cache verifies rather
    than trusts. A false miss only costs a re-parse; a false hit silently changes a boundary reading, which is
    the class of bug this whole pass exists to remove.
    """
    digest = hashlib.blake2b(digest_size=16)
    for row in rows:
        digest.update(repr(row).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class SequenceSnapshot:
    """One verified parse of one sequence listing, safe to hand down a call chain.

    Carrying the snapshot instead of the raw rows means a refresh verifies the bytes once, at the boundary
    where the rows arrive, rather than once per helper per clip -- and every number computed after that point
    is known to come from the content that is on the table now.
    """

    __slots__ = ("rows", "digest", "parsed", "_index")

    def __init__(self, rows: Sequence[Any], digest: str):
        self.rows = rows
        self.digest = digest
        self.parsed = _state_rows(rows)
        self._index = None

    @property
    def index(self) -> "PhaseIndex":
        if self._index is None:
            self._index = PhaseIndex(self.parsed)
        return self._index


def _as_snapshot(rows: Any) -> Optional["SequenceSnapshot"]:
    """The verified snapshot for this listing, rebuilding the parse only when its content changed."""
    if rows is None:
        return None
    if isinstance(rows, SequenceSnapshot):
        return rows                       # already verified at the boundary that produced it
    key, digest = id(rows), _rows_digest(rows)
    for position, entry in enumerate(_parse_cache):
        if entry[0] == key and entry[1] is rows:
            if entry[2] != digest:
                entry[2], entry[3] = digest, SequenceSnapshot(rows, digest)
            if position != len(_parse_cache) - 1:      # most-recent-last, evicted from the front
                _parse_cache.append(_parse_cache.pop(position))
            return entry[3]
    entry = [key, rows, digest, SequenceSnapshot(rows, digest)]
    _parse_cache.append(entry)
    while len(_parse_cache) > _PARSE_CACHE_LIMIT:
        _parse_cache.pop(0)
    return entry[3]


def _parsed_rows(rows: Any) -> List[Dict[str, Any]]:
    """:func:`_state_rows`, reused only after the listing's content has been checked.

    An export plan asked for the same 2,019 parsed frames once per clip and again per walked frame, which
    turned a quarter-second of reading into a two-second pause every time the review tab refreshed. The parse
    is pure, so the reuse is exact once the content matches. The listing handed back is a copy -- callers sort
    and append to it -- and the row dicts inside are read-only everywhere they travel.
    """
    snapshot = _as_snapshot(rows)
    # Row copies, not the cached dicts: 2019 of them costs a third of a millisecond, and without it a caller
    # that annotated a row in place would rewrite the parse every later reader sees. The cache is the one
    # place these bytes are kept, so it is the one place nothing may write.
    return [dict(row) for row in snapshot.parsed] if snapshot is not None else []


class PhaseIndex:
    """The parsed sequence rows, addressable by the nearest sampled instant.

    The lookup is a binary search because a recovery block asks this question for every frame it walked
    past, and scanning the whole index per question is the mistake the first cut of this class made.
    """

    def __init__(self, parsed: Sequence[Dict[str, Any]]):
        # Sorted because the lookup below bisects: the saved listing is in tape order today, and a binary
        # search over an unsorted one returns a confident wrong answer rather than an error.
        ordered = sorted(parsed, key=lambda row: row["at"])
        self.stamps = [row["at"] for row in ordered]
        self.by_instant = dict(zip(self.stamps, ordered))

    def phase(self, instant: Optional[float]) -> Optional[str]:
        at = _num(instant)
        if at is None or not self.stamps:
            return None
        position = bisect.bisect_left(self.stamps, at)
        best, best_gap = None, None
        for candidate in self.stamps[max(0, position - 1):position + 1]:
            gap = abs(candidate - at)
            if best_gap is None or gap < best_gap:
                best, best_gap = candidate, gap
        # A row further than a sample away was not this frame at all, and attaching a neighbour's phase to
        # it would be inventing a sighting. None means unknown, which is what it says.
        if best is None or best_gap > max(INSTANT_TOLERANCE_SECONDS, 0.05):
            return None
        return frame_phase(self.by_instant[best])


def _phase_lookup(rows: Sequence[Any]) -> Optional[PhaseIndex]:
    """Combat phase per sampled instant, from the canonical facts for the saved sequence.

    A walked-past row in a recovery block is a summary written by the edge walk: it carries the instant, the
    coarse state and the clock, and no prose to re-read. The frame it names is in the sequence, though, and
    so are its facts -- so the phase is joined by timestamp rather than guessed at from the state word the
    review was wrong to trust. An instant with no matching row stays unknown, which is a missing sighting,
    not a sighting of nothing.
    """
    snapshot = _as_snapshot(rows)
    return snapshot.index if snapshot is not None else None


def _row_phase(row: Optional[Dict[str, Any]], instant: Optional[float],
               index: Optional[PhaseIndex]) -> str:
    """This walked row's phase: from the sequence row at that instant, else from the row itself."""
    if index is not None:
        found = index.phase(instant)
        if found is not None:
            return found
    return frame_phase(row)


def _boundary_readings(subject, *, cue_at, edge, walked, clock, late_by, opening_here,
                       describes_subject, phases=None):
    """Every saved reading this boundary has, each one saying what it saw and at which frame.

    Deliberately a list and not a verdict. The pipeline's cues disagree sometimes, and a single
    supported/contradicted word cannot carry "the cue that settled this opening is fifty seconds behind the
    frame the clip starts on, and there is a row of live play one second ahead of it" -- which is two facts
    about two frames, both true, and the only thing that settles them is the footage in between.
    """
    readings: List[Dict[str, Any]] = []
    if cue_at is not None and describes_subject:
        settled = edge.get("settled_by") if isinstance(edge.get("settled_by"), dict) else {}
        readings.append({"at": cue_at, "about": "opening", "sees": settled.get("state")
                         or edge.get("station_cue_state") or "a station cue",
                         "phase": bg.ROUND_PHASE_OPENING,
                         "says": "the backward walk settled the opening on this frame, where the players "
                                 "stopped",
                         # A station cue marks where the players stopped; it is not a sighting of the round
                         # being fought, and a dispute has to be between claims of one kind.
                         "proves_play_was_running": False,
                         "agrees_with_candidate": subject is not None
                         and abs(cue_at - subject) <= INSTANT_TOLERANCE_SECONDS})
    for row in walked:
        at = _num(row.get("at"))
        if at is None:
            continue
        phase = _row_phase(row, at, phases)
        because = row.get("because") or "no clock read"
        if phase == bg.ROUND_PHASE_PAUSED:
            sees, says = ("pause menu", "a menu was open over the match at this frame, so the clock here "
                                         "is stopped and the frame shows nobody playing: %s" % because)
        elif phase == bg.ROUND_PHASE_OPENING:
            sees, says = ("round opening", "a round was being announced at this frame, which is the start "
                                           "of a round inside a game rather than play already under "
                                           "way: %s" % because)
        elif phase == bg.ROUND_PHASE_ACTIVE:
            sees, says = ("active play", "the round was being played at this frame, which cannot sit "
                                         "before the set opened if this is its first round: %s" % because)
        else:
            sees, says = (str(row.get("state") or "a saved row"),
                          "this frame shows arena content and says nothing either way about whether it "
                          "was being played: %s" % because)
        readings.append({"at": at, "about": "opening", "phase": phase, "sees": sees, "says": says,
                         "proves_play_was_running": phase == bg.ROUND_PHASE_ACTIVE,
                         "agrees_with_candidate": subject is not None
                         and abs(at - subject) <= INSTANT_TOLERANCE_SECONDS})
    if clock is not None and describes_subject:
        readings.append({"at": subject, "about": "round",
                         "phase": bg.ROUND_PHASE_ACTIVE if late_by else PHASE_NO_CLAIM,
                         "sees": "round clock %d" % clock,
                         "says": ("the round clock reads %d at this frame, so the round was already "
                                  "running %.1f s into it" % (clock, late_by or 0.0))
                         if late_by else ("the round clock reads %d at this frame, which is where a round "
                                          "opens -- and that is a round, not a set" % clock),
                         "proves_play_was_running": bool(late_by),
                         "agrees_with_candidate": not late_by})
    if opening_here is not None:
        readings.append({"at": opening_here["at"], "about": "opening",
                         "phase": opening_here.get("phase") or bg.ROUND_PHASE_OPENING,
                         "sees": opening_here["state"],
                         "says": "the saved sequence puts %s at this instant" % opening_here["state"],
                         "proves_play_was_running": False,
                         "agrees_with_candidate": True})
    return sorted(readings, key=lambda item: item["at"])


def _inspect_window(subject, readings):
    """The stretch of tape that contains every reading in dispute, padded so neither end is off-screen."""
    points = [item["at"] for item in readings if _num(item.get("at")) is not None]
    if subject is not None:
        points.append(subject)
    if not points:
        return None
    low, high = min(points), max(points)
    pad = max(2.0, INSTANT_TOLERANCE_SECONDS * 2)
    return {"from": round(max(0.0, low - pad), INSTANT_PRECISION),
            "to": round(high + pad, INSTANT_PRECISION),
            "seconds": round((high - low) + 2 * pad, INSTANT_PRECISION),
            "readings": len(points)}


def start_opening(recovery: Dict[str, Any], *, clock_opening: int = ROUND_CLOCK_OPENING,
                  rows: Sequence[Any] = (), cues: Sequence[Any] = (),
                  candidate_at: Optional[float] = None) -> Dict[str, Any]:
    """What the saved evidence says about the opening of the boundary *as it stands now*.

    Inside a round the clock only ever falls. So a clock reading below ``clock_opening`` **at the instant
    being asked about** proves play was already running, and the round opened at least that many seconds
    earlier; and a live-play row the backward walk had to pass says the same thing whatever the clock read.
    This never subtracts a constant from a timestamp to move a boundary -- it reports what the saved rows
    entail, keeps the cue the walk settled on as the earliest *named* frame, and says plainly when the
    evidence stops before either, which is a reading to be bought, not a guess to be made.

    ``candidate_at`` is the instant the question is about: the row's current start. An edge block records
    where a reading was *taken* (``edges.start.at``), and the two are the same frame only until somebody
    nudges, edits or regroups the boundary. Past one sample of the saved series they are different instants,
    and a reading taken at one is not evidence about the other, so: the original observation is kept whole
    under ``edge_observation``, the claim about the current instant is re-derived from whatever the saved
    rows settle there, and where nothing settles it the answer is ``unknown`` with a note saying what would
    settle it. Moving a reading so a contradiction keeps applying would be inventing a clock, and
    subtracting one to produce a new reading would be inventing a second one.
    """
    edge = ((recovery or {}).get("edges") or {}).get("start") or {}
    at = _num(edge.get("at"))
    subject = _num(candidate_at)
    if subject is None:
        subject = at
    # The one test of whether a saved reading still speaks to this boundary: same instant, same evidence.
    describes_subject = (at is None or subject is None
                         or abs(at - subject) <= INSTANT_TOLERANCE_SECONDS)
    edge_clock = edge.get("clock_at_boundary")
    edge_clock = (int(edge_clock) if isinstance(edge_clock, (int, float))
                  and not isinstance(edge_clock, bool) else None)
    clock = edge_clock
    scanned = edge.get("scanned") or []
    reaches = _num(scanned[0]) if len(scanned) == 2 else None
    walked = [row for row in (edge.get("walked_past") or []) if isinstance(row, dict)]
    # The saved sequence is checked against the parse cache exactly once here, and the verified snapshot is
    # what travels down. Re-checking inside every helper would either re-hash 2,019 rows a dozen times per
    # clip for nothing, or -- if it were optimised away by identity -- reopen the hole where a row edited in
    # place is answered from bytes nobody re-read.
    sequence = _as_snapshot(rows) if rows else None
    recovered = recover_opening(sequence, subject, games=recovery.get("games"),
                                clock_start=clock_opening) if (rows and subject is not None) else None
    # Which of those walked frames actually show the round being *played*. Every one of them carries the
    # coarse state word "gameplay", because that is what the classifier calls any frame with an arena in
    # it, and treating the word as a claim about combat is what told a reviewer that a ROUND 1 banner and
    # an open pause menu proved the clip cut live play. The phase comes from the canonical facts for the
    # same instants; a frame that cannot be looked up stays unknown and argues nothing.
    phases = sequence.index if sequence is not None else None
    live_walked = [row for row in walked
                   if describes_subject and _row_phase(row, _num(row.get("at")), phases)
                   == bg.ROUND_PHASE_ACTIVE]
    cue_at = _num((edge.get("settled_by") or {}).get("at")) if isinstance(edge.get("settled_by"), dict) else None
    if cue_at is None:
        cue_at = _num(edge.get("station_cue_at"))
    # Which clock the current instant may be judged by: the edge's own reading while it still describes
    # this frame, otherwise a row the saved sequence reads here. Nothing is carried across either way.
    if not describes_subject:
        clock = (recovered or {}).get("clock_at_candidate")
    late_by = None if clock is None else max(0.0, float(clock_opening - clock))
    reasons: List[str] = []
    if late_by and subject is not None:
        reasons.append("the round clock already reads %d at the candidate instant %.3f. It only ever falls "
                       "inside a round, so this round opened at least %.1f s earlier and the clip would "
                       "start on play already in progress" % (clock, subject, late_by))
    if live_walked:
        reasons.append("the backward walk found a gameplay row at %s ahead of the candidate and could not "
                       "settle the bound there (%s); that is on-screen play before this start, though a "
                       "round-intro animation can also draw as gameplay, so it bounds the opening without "
                       "naming it"
                       % (", ".join("%.3f" % _num(row.get("at")) for row in live_walked),
                          live_walked[0].get("because") or "clock unread"))
    earliest = None
    supported_by: List[str] = []
    if cue_at is not None and describes_subject and (subject is None or cue_at <= subject + 1e-9):
        settled = edge.get("settled_by") if isinstance(edge.get("settled_by"), dict) else {}
        earliest = cue_at
        supported_by = ["station cue %s at %.3f, the nearest frame the saved rows name where the players "
                        "stopped" % (settled.get("state") or "unnamed", cue_at)]
    if late_by and subject is not None:
        supported_by.append("clock-bound opening no later than %.3f" % (subject - late_by))
    needs_reading = bool(reasons) and earliest is None and late_by is None
    opening_here = None
    if recovered is not None and subject is not None:
        # A row that puts an opening *at* this instant is evidence about the boundary; a row behind it is a
        # place to look. Only the first belongs in "what supports this start", and it is the distinction
        # that let a pre-pairing screen ten seconds early be reported as a supported set start.
        for candidate in recovered.get("candidates") or []:
            for row_at, row_state in candidate.get("rows") or []:
                if abs(row_at - subject) <= INSTANT_TOLERANCE_SECONDS:
                    opening_here = {"at": row_at, "state": row_state,
                                    "level": classify_opening_state(row_state)}
        if opening_here is not None:
            supported_by.append("the saved sequence puts %s at this instant (%.3f): that is where a clip "
                                "would open, not somewhere earlier to go and look"
                                % (opening_here["state"], opening_here["at"]))
    if recovered is not None:
        # The sequence outranks the edge note when it has something to say, because it is the raw
        # observation and the edge block is somebody's summary of it. What it says is a candidate at a
        # named level, never a resolution: it is not written into a boundary, and it does not retire a
        # reading requirement either -- an earlier thing to look at is not a look that was done.
        supported_by = list(supported_by) + [
            "saved sequence: %s (earliest candidate %s at level %s, %d row(s) in the window)"
            % (recovered.get("basis") or "",
               "%.3f" % recovered["earliest_candidate"]
               if recovered.get("earliest_candidate") is not None else "none",
               recovered.get("earliest_candidate_level") or "none",
               recovered.get("rows_in_window") or 0)]
        if recovered.get("status") == "candidate_found" \
                and recovered.get("earliest_candidate") is not None:
            earliest = min(value for value in (earliest, recovered["earliest_candidate"])
                           if value is not None)
        if recovered.get("play_continues_behind_candidate"):
            reasons.append(recovered.get("basis")
                           or "play continues behind this candidate: the saved rows neither place the "
                              "opening here nor rule one out earlier")
    moved_note = ""
    if not describes_subject:
        moved_note = ("the saved start-edge reading was taken at %.3f while this boundary is at %.3f, so it "
                      "describes a frame %.1f s away from this one and says nothing about the clock here"
                      % (at, subject, abs(subject - at)))
        if clock is None:
            # Nothing settles the new instant. That is an unknown to be reported, not a verdict to be
            # inherited from the frame the boundary used to sit on.
            needs_reading = True
    readings = _boundary_readings(subject, cue_at=cue_at, edge=edge, walked=walked, clock=clock,
                                 late_by=late_by, opening_here=opening_here,
                                 describes_subject=describes_subject, phases=phases)
    # Two readings of one boundary that name different frames are only in *dispute* if they cannot both be
    # true. A cue the walk settled on fifty seconds back and a ROUND banner one second ahead of the
    # candidate are not rivals: they are the same opening sequence seen twice, and the second says the
    # first was early rather than contradicting it. What genuinely disputes a settled cue is a frame that
    # shows the round already being fought somewhere the cue cannot cover -- so the test is that the
    # readings disagree *and* at least one of them claims live play. Anything else is the pipeline
    # describing one event at two resolutions, and calling that a physical disagreement sends a reviewer to
    # look for a contradiction that was never there.
    claims = [item for item in readings if item.get("about") == "opening"]
    frames_named = {item["at"] for item in claims}
    asserts_play = [item for item in claims if item.get("proves_play_was_running")]
    readings_conflict = len(frames_named) > 1 and bool(asserts_play)
    return {"at": at, "candidate_at": subject, "edge_describes_candidate": describes_subject,
            "clock_at_boundary": clock, "clock_opening": clock_opening,
            "late_by_at_least_seconds": late_by,
            "earliest_supported_opening": earliest,
            "clock_bound_opening": None if (subject is None or not late_by) else subject - late_by,
            "opening_at_candidate": opening_here,
            "supported_by": supported_by,
            "contradicts": reasons,
            "evidence_reaches_to": reaches,
            "settled_by": edge.get("settled_by") if describes_subject else None,
            "station_cue_at": edge.get("station_cue_at") if describes_subject else None,
            "walked_past": walked if describes_subject else [],
            # The original observation, whole and where it was taken. It is never rewritten when the
            # boundary moves, and it is never read as a claim about a different instant.
            "edge_observation": {"at": at, "clock_at_boundary": edge_clock,
                                 "late_by_at_least_seconds": (
                                     None if edge_clock is None
                                     else max(0.0, float(clock_opening - edge_clock))),
                                 "settled_by": edge.get("settled_by"),
                                 "station_cue_at": edge.get("station_cue_at"),
                                 "walked_past": walked,
                                 "seconds_from_candidate": (None if (at is None or subject is None)
                                                            else round(subject - at, 6))},
            "recovered_opening": recovered,
            "readings": readings,
            "readings_conflict": readings_conflict,
            "inspect_window": _inspect_window(subject, readings) if readings_conflict else None,
            "flagged": bool(reasons),
            "needs_reading": needs_reading,
            "moved_boundary_note": moved_note,
            "reading_note": ((moved_note + "; " if moved_note else "") + (
                "saved rows stop at %s, which is not far enough back to name an opening"
                % ("%.3f" % reaches if reaches is not None else "the start of the window")))
            if needs_reading else ""}


# ---------------------------------------------------------------------------- the saved sequence itself

#: How a sequence reference records which file it points at: the same truncated digest the investigation
#: inputs use, so one convention covers both.
SEQUENCE_DIGEST_CHARS = 16


def sequence_reference(path, note=""):
    # type: (str | Path, str) -> Dict[str, Any]
    """A durable pointer to the saved sequence rows, checked by digest when it is read back.

    Rows are not copied into the project file: a BB9 timeline is 2,019 rows and a project is opened and
    saved on every decision, so embedding the corpus would grow a 187 KB file on every click and still
    leave the question of which corpus it came from. The reference names the file, its size and the
    digest of its bytes, which is what a later reader needs to know whether the openings it is being
    shown were considered against the evidence that is on disk now.
    """
    source = Path(path)
    payload = source.read_bytes()
    return {"kind": "timeline", "path": str(source), "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()[:SEQUENCE_DIGEST_CHARS],
            "note": note, "rows": len(_state_rows(_timeline_rows(source)))}


def _timeline_rows(source):
    # type: (Path) -> List[Dict[str, Any]]
    """The rows of a saved timeline document, whether it is a list or wraps one."""
    document = json.loads(Path(source).read_text(encoding="utf-8"))
    if isinstance(document, list):
        return [row for row in document if isinstance(row, dict)]
    for key in ("observations", "rows", "timeline", "frames"):
        if isinstance(document.get(key), list):
            return [row for row in document[key] if isinstance(row, dict)]
    return []


def load_sequence_rows(reference):
    # type: (Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]
    """Resolve a sequence reference into rows, or into an explanation of why it could not.

    A reference that no longer matches its file is *not* quietly used: rows from a different corpus would
    put openings at instants this recording does not contain, which is the same class of error as naming a
    frame from a rounded second. The caller gets the rows (empty) plus a provenance record saying what
    happened, and the panel says so out loud.
    """
    if not isinstance(reference, dict) or not reference:
        return [], {"available": False, "reason": "no sequence source is recorded for this project, so "
                                                  "openings were not considered",
                    "expected_rows": 0, "rows_read": 0}
    if reference.get("kind") == "inline":
        rows = [row for row in (reference.get("rows") or []) if isinstance(row, dict)]
        return rows, {"available": True, "kind": "inline", "rows_read": len(rows),
                      "durable": bool(reference.get("durable", False)),
                      "reason": "rows supplied in memory or inline in the project; no file to re-check"}
    path = Path(str(reference.get("path") or ""))
    expected = str(reference.get("sha256") or "")
    provenance = {"available": False, "kind": "timeline", "path": str(path),
                  "expected_sha256": expected, "expected_rows": int(reference.get("rows") or 0),
                  "rows_read": 0}
    if not path.is_file():
        provenance["reason"] = ("the recorded sequence file is not here (%s): openings are reported "
                                "without it rather than from a substitute" % path)
        return [], provenance
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()[:SEQUENCE_DIGEST_CHARS]
    provenance["sha256"] = actual
    provenance["digest_matches"] = bool(expected) and actual == expected
    if not provenance["digest_matches"]:
        provenance["reason"] = ("the sequence file on disk (%s) is not the one this project was opened "
                                "against (%s); refusing to read openings from a different corpus"
                                % (actual, expected or "no digest recorded"))
        return [], provenance
    rows = _timeline_rows(path)
    provenance.update({"available": True, "rows_read": len(rows),
                       "reason": "rows read from the recorded sequence file"})
    return rows, provenance


def project_sequence(project):
    # type: (Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]
    """The sequence rows a project can prove, from inline rows or from its durable reference."""
    inline = project.get("sequence_rows")
    if inline:
        return [row for row in inline if isinstance(row, dict)], {
            "available": True, "kind": "inline", "rows_read": len(inline), "durable": False,
            "reason": "rows were passed with the project rather than referenced by file"}
    return load_sequence_rows(project.get("sequence_source"))


def review_queue(project: Dict[str, Any], *, context_seconds: float = 12.0,
                 include_confident: bool = True, view: str = "open") -> List[Dict[str, Any]]:
    """Every clip a reviewer must look at, confident ones included, with context around the *boundary*.

    The queue is per question, not per segment: a set needs a decision about its opening and a separate one
    about its ending, because those are two different frames to watch and two different answers. Context is
    centred on the frame in question -- a set-wide span is not "short playable context", it is the whole
    clip again. ``view`` selects open questions (default), the deferred ones that can be resumed, the
    answered ones, or all of them; nothing here costs an inference.
    """
    segments = project.get("matches") or []
    store = ReviewStore(project)
    sequence_rows, _sequence_provenance = project_sequence(project)
    queue: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments):
        recovery = segment.get("recovery") or {}
        start, end = _num(segment.get("start")), _num(segment.get("end"))
        clip = resolve_clip_end(segment, following=segments[index + 1]
                                if index + 1 < len(segments) else None,
                                duration=_num(project.get("duration")))
        opening = start_opening(recovery, rows=sequence_rows, candidate_at=start)
        flags = []
        if recovery.get("orientation_required"):
            flags.append("unnamed sides need an orientation decision")
        if clip.get("held") or clip.get("skipped_reason"):
            flags.extend(clip["warnings"])
        elif clip.get("included_past_recorded_end"):
            flags.append("disclosure: %d second(s) of footage past the recorded set end are included"
                         % round(clip["end"] - (segment.get("end") or clip["end"])))
        if opening["flagged"]:
            flags.append("late start: " + "; ".join(opening["contradicts"]))
        if not recovery.get("boundary_certain", True):
            flags.append("boundary is not certain")
        if recovery.get("alternative_groupings"):
            flags.append("an alternative grouping is open")
        if (recovery.get("unresolved_claims") or []):
            flags.append("%d unresolved claim(s)" % len(recovery.get("unresolved_claims") or []))
        if not include_confident and not flags:
            continue
        for kind, why in _questions_for(recovery, clip, flags, opening):
            # A question's identity, not a second copy of its instants. The span is rounded here on purpose
            # and nowhere else: the first field carries the start unrounded, so two rows cannot collide
            # unless they open at the same instant (which a claim refuses), while re-spelling an existing
            # key would silently orphan every decision saved under the old one -- a worse failure than a
            # label with fewer digits on it. Comparisons and export jobs never read this string.
            fingerprint = "%s|%s|%s" % (kind, start, "%s..%s" % (round(start or 0, 3), round(end or 0, 3)))
            existing = store.decision(fingerprint)
            # An invalidated answer is not an answer any more -- its consequence was withdrawn -- so its
            # question is open again, with the withdrawn record shown beside it so the reviewer can see
            # what was said before the evidence moved and say it again knowingly if it was right.
            state = ("open" if existing is None or existing.get("status") == INVALIDATED else
                     "deferred" if existing.get("status") == DEFERRED else "answered")
            if view == "open" and state != "open":
                continue
            if view == "deferred" and state != "deferred":
                continue
            if view == "answered" and state != "answered":
                continue
            boundary_at = _boundary_at(kind, segment, clip, opening)
            item = {"kind": kind, "index": index, "state": state,
                    "fingerprint": fingerprint,
                    "segment": {"start": start, "end": end, "index": index},
                    "subject": "%s..%s" % (round(start or 0, 3), round(end or 0, 3)),
                    "boundary": {"kind": FOOTAGE_QUESTIONS[kind]["boundary"], "at": boundary_at,
                                 "late_by_at_least_seconds": opening["late_by_at_least_seconds"]
                                 if kind == "new_match" else None,
                                 "earliest_supported_opening": opening["earliest_supported_opening"]
                                 if kind == "new_match" else None,
                                 # Where the saved reading was actually taken, and whether it still
                                 # describes this frame. A reviewer who moved a boundary has to be told the
                                 # evidence moved out of range, not shown the old verdict as if it held.
                                 "saved_reading_at": opening["at"] if kind == "new_match" else None,
                                 "evidence_describes_boundary": (
                                     opening["edge_describes_candidate"] if kind == "new_match" else None),
                                 "needs_review_why": (
                                     (opening["reading_note"] or opening["moved_boundary_note"])
                                     if kind == "new_match" else ""),
                                 # The pipeline's own readings of this boundary, and whether they agree. When
                                 # they do not, the card says so and names the frames: the reviewer is being
                                 # asked what the footage shows, not which detector to believe.
                                 "readings": opening["readings"] if kind == "new_match" else [],
                                 # The round's own opening, recovered from the phase of the saved frames: an
                                 # interval and its resolution, offered as a place a clip *could* start. It
                                 # moves nothing, and it never states an exact first input the sampling
                                 # could not see.
                                 "round_opening": opening["recovered_opening"].get("round_opening")
                                 if kind == "new_match" and opening.get("recovered_opening") else None,
                                 "readings_conflict": bool(opening["readings_conflict"])
                                 if kind == "new_match" else False,
                                 "dispute_window": opening["inspect_window"] if kind == "new_match"
                                 else None},
                    "why_flagged": why,
                    "context": _context(boundary_at, context_seconds, project, clip),
                    "adjust": _adjust_affordance(boundary_at, opening),
                    # Proposed against current, on the question it belongs to. A reviewer is never shown a
                    # boundary that has already moved and asked to approve it: the row stays where it is,
                    # the suggestion says where the machine wants it and how much of that distance the
                    # sampling can defend.
                    "proposal": _proposal_view(segment, kind, boundary_at),
                    "decision": existing,
                    "evidence": _evidence_excerpt(recovery, clip, opening),
                    "costs_inference": False,
                    "export_consequence": _export_consequence(segment, clip),
                    }
            question = FOOTAGE_QUESTIONS.get(kind, {})
            item["ask"] = question.get("ask", "Is this right?")
            item["options"] = question.get("options", [])
            item["labels"] = question.get("labels", [])
            item["ask_once"] = True
            queue.append(item)
    return queue


def _boundary_at(kind: str, segment: Dict[str, Any], clip: Dict[str, Any],
                 opening: Dict[str, Any]) -> Optional[float]:
    """The frame a question is really about, so playback can stop near it instead of around the set."""
    if kind == "new_match":
        return _num(segment.get("start"))
    if kind == "clip_end":
        return _num(clip.get("end")) if clip.get("end") is not None else _num(segment.get("end"))
    if kind == "membership":
        junctions = [_num((group or {}).get("junction"))
                     for group in ((segment.get("recovery") or {}).get("alternative_groupings") or [])
                     if isinstance(group, dict)]
        junctions = [value for value in junctions if value is not None]
        if junctions:
            return junctions[0]
        return _num(segment.get("start"))
    return _num(segment.get("start"))


def _context(boundary_at: Optional[float], context_seconds: float, project: Dict[str, Any],
             clip: Dict[str, Any]) -> Dict[str, Any]:
    duration = _num(project.get("duration"))
    if boundary_at is None:
        boundary_at = clip.get("end") or 0.0
    play_from = max(0.0, boundary_at - context_seconds)
    play_to = boundary_at + context_seconds
    if duration is not None:
        play_to = min(duration, play_to)
    return {"boundary_at": boundary_at, "play_from": play_from, "play_to": play_to,
            "seconds_each_side": context_seconds, "step_seconds": context_seconds,
            "note": "short context around the frame in question; grow it with more-back / more-forward"}


def _proposal_view(segment: Dict[str, Any], kind: str,
                   current: Optional[float]) -> Optional[Dict[str, Any]]:
    """The row's pending proposal for this question, in the reviewer's terms, or nothing."""
    wanted_kind = {"new_match": "opening", "clip_end": "end"}.get(kind)
    if wanted_kind is None:
        return None
    wanted = pending_proposal(segment, wanted_kind)
    if wanted is None:
        return None
    at, now = float(wanted["at"]), float(current or 0.0)
    return dict(wanted, current=current,
                distance_seconds=round(at - now, INSTANT_PRECISION),
                uncertainty_text=proposal_width(wanted.get("uncertainty_seconds")),
                summary="machine suggests %.3f s, the row is at %.3f s, width %s"
                        % (at, now, proposal_width(wanted.get("uncertainty_seconds"))))


def _adjust_affordance(boundary_at: Optional[float], opening: Dict[str, Any]) -> Dict[str, Any]:
    """Where the card may jump to look, and what kind of instant that is.

    A proposal with a timestamp attached, never an edit: the boundary moves only when the reviewer answers.
    The round's own recovered opening is offered ahead of any earlier context, because it is the nearer
    claim and the one a frame's phase actually supports -- and the level travels with it, so jumping to a
    ROUND banner is never mistaken for jumping to a set start.
    """
    recovered = opening.get("recovered_opening") or {}
    round_opening = recovered.get("round_opening") or {}
    jump_to = round_opening.get("opening_start_at") or recovered.get("earliest_candidate")
    level = ("round_opening" if round_opening.get("opening_start_at")
             else recovered.get("earliest_candidate_level"))
    return {"steps_seconds": list(ADJUST_STEPS_SECONDS), "current": boundary_at,
            "to_current_time": True, "jump_to": jump_to, "jump_to_level": level,
            # The interval, when an opening was recovered: a reviewer offered a single instant for a start
            # the sampling can only bound would be shown a precision the tape does not have.
            "jump_to_uncertainty_seconds": round_opening.get("uncertainty_seconds")
            if level == "round_opening" else None,
            "first_active_gameplay_at": round_opening.get("first_active_at"),
            # A different width, because it is a different question. The jump's uncertainty bounds where the
            # opening is; this one bounds when play started, and it is wide exactly where the tape did not
            # look. A card that quoted only the first would offer a two-number proposal at one number's
            # confidence. None when no frame continuous with this opening shows the round fought at all --
            # which is missing coverage, not a late start.
            "first_active_uncertainty_seconds": round_opening.get("active_onset_uncertainty_seconds"),
            "first_active_status": round_opening.get("active_onset_status")}


def _questions_for(recovery: Dict[str, Any], clip: Dict[str, Any], flags: Sequence[str],
                   opening: Dict[str, Any]) -> List[Tuple[str, str]]:
    """What to ask at one segment: its opening and its ending always, plus whatever is still contested.

    Both boundary questions are asked even when nothing is flagged, because a flag list cannot prove the
    absence of a wrong boundary -- five of ten BB9 starts were late and none of them were flagged by
    anything. What changes with confidence is the *why*, never whether the reviewer is shown the frame.
    """
    start_reasons = list(opening["contradicts"])
    if opening.get("readings_conflict"):
        # The card is where this has to appear, not only the export plan: a reviewer who reads "supported"
        # in one place and a flag in another will trust whichever one is shorter.
        dispute = [item for item in opening.get("readings") or [] if item.get("about") == "opening"]
        start_reasons.append(
            "the saved evidence does not agree with itself about where this opening is: %s. Watch the "
            "frames between them -- neither cue decides the other, and you are not being asked to rank "
            "them"
            % "; ".join("%s at %s" % (item["sees"], instant_text(item["at"])) for item in dispute))
    start_reasons.extend(str(claim.get("missing") or claim.get("would_resolve"))[:200]
                         for claim in recovery.get("unresolved_claims") or [] if isinstance(claim, dict))
    out: List[Tuple[str, str]] = [
        ("new_match", "; ".join(start_reasons) or
         "the opening is the detector's own prediction: nothing here proves the previous footage belonged "
         "to this match, and a start two seconds late is invisible to a flag list"),
        ("clip_end", "; ".join(clip.get("warnings") or [clip.get("skipped_reason") or ""]) or
         "the ending is the padded bound the pipeline derived; a non-fighting frame is not proof the set "
         "concluded")]
    if recovery.get("orientation_required"):
        out.append(("orientation", "the recording names the sides at some point in the set but not this "
                                   "clip's players, and characters are never side-bound"))
    if recovery.get("alternative_groupings"):
        out.append(("membership", "%d alternative grouping(s) remain open"
                    % len(recovery.get("alternative_groupings") or [])))
    return out


def _evidence_excerpt(recovery: Dict[str, Any], clip: Dict[str, Any],
                      opening: Dict[str, Any]) -> Dict[str, Any]:
    """The signals themselves, separated from what they are taken to mean."""
    games = recovery.get("games") or []
    return {
        "conclusion_signals": [g.get("conclusion_event_at") for g in games],
        "winner_confirmations": [g.get("winner_confirmed_at") for g in games],
        "per_game_characters": [{"start": g.get("start"), "characters": g.get("characters_observed"),
                                 "side_bound": g.get("characters_side_bound")} for g in games],
        "game_wins": recovery.get("game_wins"),
        "deciding_wins_unit": recovery.get("deciding_wins_unit"),
        "concluded_by": recovery.get("concluded_by"),
        "attribution_withheld": recovery.get("attribution_withheld"),
        # Who the broadcast graphic named on each side, game by game, next to the reason a winner is still
        # withheld. Without this the panel says "withheld" and a reader cannot tell "the footage named
        # nobody" from "the footage named them, on a witness too weak to credit" -- which are different
        # things to act on. Reduced to the fields that carry the judgement, not the whole record: the full
        # share, span and crop digest stay in `recovery`.
        "identity_hypotheses": [
            {"at": row.get("at"), "basis": row.get("basis"), "sides": row.get("sides"),
             "share": row.get("share"), "named_rows": row.get("named_rows"),
             "band_agrees": row.get("band_agrees"), "contradicts": bool(row.get("contradicts"))}
            for row in ((recovery.get("nameplate_hypotheses") or {}).get("games") or [])],
        "identity_hypothesis_limits": (
            (recovery.get("nameplate_hypotheses") or {}).get("trust_note")
            if (recovery.get("nameplate_hypotheses") or {}).get("games") else None),
        "possible_winners": [
            {"name": row.get("name"), "wins": row.get("wins"), "unit": row.get("unit")}
            for row in ((recovery.get("nameplate_hypotheses") or {}).get("possible_winners") or [])],
        # The tier beside the names, not underneath them: what the estimate is, how much it is worth, and
        # the one-line reasons a reviewer would otherwise have to read the whole report for.
        "attribution_confidence": {
            key: (recovery.get("attribution_confidence") or {}).get(key)
            for key in ("score", "tier", "needs_review", "single_witness_capped",
                        "independent_witnesses")},
        "winner_estimate": (recovery.get("attribution_confidence") or {}).get("winner_estimate"),
        # The word that says whether the arithmetic is closed, kept separate from the name so a card cannot
        # show one without the other: "decisive" describes the tally against the admitted targets, never the
        # trustworthiness of the name it points at.
        "estimate_status": ((recovery.get("attribution_confidence") or {}).get("winner_estimate") or {})
                           .get("status"),
        "estimate_blockers": (recovery.get("attribution_confidence") or {}).get("estimate_blockers"),
        "duplicates_quarantined": (recovery.get("attribution_confidence") or {})
                                  .get("duplicates_quarantined"),
        "confidence_reasons": [
            row.get("why") for row in
            ((recovery.get("attribution_confidence") or {}).get("negative_factors") or [])][:4],
        "target_provenance": {
            "applicable_formats": recovery.get("applicable_formats"),
            "complete_under": recovery.get("complete_under"),
            "open_under": recovery.get("open_under"),
            "deciding_wins": recovery.get("deciding_wins"),
            "winner_unit": recovery.get("winner_unit")},
        "unnamed_games": list((recovery.get("nameplate_hypotheses") or {}).get("unnamed_games") or []),
        "start_clock_at_boundary": opening["clock_at_boundary"],
        # The saved reading stays visible where it was taken, even once it stops describing this frame:
        # "the evidence is gone" and "the evidence is elsewhere" are different things to a reviewer.
        "start_edge_observation": opening.get("edge_observation"),
        "start_walked_past": opening["walked_past"],
        "start_settled_by": opening["settled_by"],
        "clip": clip,
        "reaches_target_does_not_prove_set_ended": True,
        "clock_at_60_does_not_prove_first_round": True,
    }


def _export_consequence(segment: Dict[str, Any], clip: Dict[str, Any]) -> Dict[str, Any]:
    recovery = segment.get("recovery") or {}
    recorded = _num(segment.get("end"))
    return {"would_be_exported": clip.get("end") is not None and not clip.get("held"),
            "held": bool(clip.get("held")) or bool(clip.get("skipped_reason")),
            "skipped_reason": clip.get("skipped_reason"),
            "reasons": list(clip.get("warnings") or []),
            "severity": clip.get("severity"),
            "excluded_gameplay": clip.get("excluded_gameplay") or [],
            "included_past_recorded_end": clip.get("included_past_recorded_end") or [],
            "footage_complete": recovery.get("footage_complete"),
            "runs_past_recorded_end": bool(recorded is not None and clip.get("end") is not None
                                           and clip["end"] > recorded + 1e-9)}


# --------------------------------------------------------------------------------------- the export plan

def merge_intervals(intervals: Sequence[Tuple[Optional[float], Optional[float]]]
                    ) -> List[Tuple[float, float]]:
    """A union of half-open intervals, in tape order. Inputs may be unsorted, overlapping or incomplete.

    Candidates come out of a re-import in whatever order the caller stored them and may overlap after a
    merge, so coverage that walks the list pairwise reports a different recording than the one on disk --
    which is the defect this replaces. An interval with either bound missing is dropped by the caller's
    accounting and *listed* there as invalid, never silently treated as zero length.
    """
    usable = sorted((float(start), float(end)) for start, end in intervals
                    if _num(start) is not None and _num(end) is not None and float(end) > float(start))
    merged: List[List[float]] = []
    for start, end in usable:
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(start, 6), round(end, 6)) for start, end in merged]


def uncovered_intervals(covered: Sequence[Tuple[float, float]], duration: Optional[float]
                        ) -> List[Dict[str, Any]]:
    """What the recording holds that these intervals do not, edges included.

    ``duration`` is what makes the leading and trailing stretches visible: a gap list built from
    ``zip(clips, clips[1:])`` cannot see the first 53 seconds or the last 57 of a ten-set recording, and an
    operator told "nothing is unclaimed" after looking at that list would be wrong twice over.
    """
    if duration is None:
        return []
    top = float(duration)
    free, cursor = [], 0.0
    for start, end in merge_intervals(covered):
        if start > cursor + 1e-9:
            free.append((cursor, min(start, top)))
        cursor = max(cursor, min(end, top))
        if cursor >= top - 1e-9:
            break
    if cursor < top - 1e-9:
        free.append((cursor, top))
    rows = []
    for start, end in free:
        if end <= start + 1e-9:
            continue
        edge = ("between" if start > 1e-9 and end < top - 1e-9
                else "leading" if start <= 1e-9 else "trailing")
        rows.append({"from": round(start, 6), "to": round(end, 6),
                     "seconds": round(end - start, 6), "edge": edge,
                     "why": "%s of the recording, %.1f s, that no clip claims"
                            % ({"leading": "before the first candidate",
                               "trailing": "after the last candidate",
                               "between": "between two candidates"}[edge], end - start),
                     "what_it_is": "no candidate interval claims this stretch of the recording",
                     "gameplay_seen_in_saved_evidence": None,
                     "how_it_would_be_known": "the saved game spans and the sequence rows inside the "
                                              "window; if neither names play here, the honest answer is "
                                              "that the coverage is unknown, not that it was menus"})
    return rows


def coverage_accounting(project: Dict[str, Any], clips: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Two coverage accounts, because "the candidates cover it" and "an export will render it" differ.

    A held clip covers footage in the first account and none in the second: no job is written for it, so
    its interval belongs to the unclaimed tape until a person answers the question that holds it. Counting
    it as covered -- which the previous arithmetic did -- hid exactly the footage a reviewer was being told
    to go and look at.
    """
    duration = _num(project.get("duration"))
    segments = project.get("matches") or []
    claimed = [(clip.get("start"), clip.get("end")) for clip in clips]
    invalid = [{"index": clip["index"], "start": clip.get("start"), "end": clip.get("end"),
                "because": "no usable interval (a bound is missing or the end is not after the start)"}
               for clip in clips
               if _num(clip.get("start")) is None or _num(clip.get("end")) is None
               or _num(clip.get("end")) <= _num(clip.get("start"))]
    exportable = [(clip.get("start"), clip.get("end")) for clip in clips if not clip.get("held")]
    games = [(game.get("start"), game.get("end"))
             for segment in segments for game in ((segment.get("recovery") or {}).get("games") or [])]
    game_spans = merge_intervals(games)

    def account(intervals):
        merged = merge_intervals(intervals)
        return {"intervals": [{"from": start, "to": end, "seconds": round(end - start, 6)}
                              for start, end in merged],
                "intervals_count": len(merged),
                "seconds": round(sum(end - start for start, end in merged), 6)}

    def with_gameplay(rows):
        for row in rows:
            inside = [start for start, end in game_spans
                      if start < row["to"] - 1e-9 and end > row["from"] + 1e-9]
            row["gameplay_seen_in_saved_evidence"] = bool(inside)
            if inside:
                row["gameplay_open_at"] = round(min(inside), 6)
                row["what_it_is"] = ("saved game evidence reaches into this stretch, so play is known to "
                                     "be here and no clip claims it")
        return rows

    report = {"duration": duration, "duration_known": duration is not None,
              "candidates": account(claimed), "exportable_jobs": account(exportable),
              "held_intervals_not_rendered": account([(clip.get("start"), clip.get("end"))
                                                      for clip in clips if clip.get("held")]),
              "game_spans_in_saved_evidence": account(games),
              "uncovered_by_candidates": with_gameplay(uncovered_intervals(claimed, duration)),
              "uncovered_by_exportable_jobs": with_gameplay(uncovered_intervals(exportable, duration)),
              "invalid_bounds": invalid,
              "note": ("A stretch is 'unclaimed', not 'missed', until evidence says play was there. The "
                       "two accounts differ by whatever is on hold, and only the second one predicts the "
                       "folder an export writes."),
              "inference_sent": False}
    if duration is None:
        report["note"] = ("No recording length is stored, so the leading and trailing stretches cannot be "
                          "accounted for at all; every 'uncovered' figure below is between clips only.")
    report["seconds_between_the_two_accounts"] = round(
        report["candidates"]["seconds"] - report["exportable_jobs"]["seconds"], 6)
    return report


def _earlier_context_label(candidates: Sequence[Dict[str, Any]]) -> str:
    """How to name context the rows put *before* this boundary, in a sentence that must not oversay it."""
    parts = []
    for item in candidates[:3]:
        at = _num(item.get("at"))
        parts.append("%s at %s" % (item.get("level") or "unnamed level",
                                   "%.3f" % at if at is not None else "an unnamed frame"))
    return ", ".join(parts) if parts else "none"


def _governing_answer(decisions: Sequence[Dict[str, Any]], kinds: Sequence[str]) -> Optional[str]:
    """The answer a person last gave to one of these questions, from the journal that still governs.

    Read from the decision records rather than only from the fields :meth:`ReviewStore.recompute` projects,
    because the projection is rebuilt by whoever runs the store: a project loaded straight from disk, or a
    call made through a helper that annotates ``review`` without recompiling it, carries answers the
    projection has never seen. The journal is where the answer is actually written down.
    """
    said = None
    for record in decisions:
        if isinstance(record, dict) and record.get("kind") in kinds:
            said = record.get("answer")
    return said


def _machine_bound_at(segment: Dict[str, Any], which: str,
                      at: Optional[float]) -> Optional[Dict[str, Any]]:
    """The pipeline's own record of moving this boundary, when the row now sits where it put it.

    Matched by instant, not by order: an operator end, a hand move or a second machine pass can leave older
    records on the row, and the one that governs is the one whose destination is the bound in force.
    """
    if at is None:
        return None
    for record in reversed(list((segment.get("review") or {}).get("machine_bounds") or [])):
        if not isinstance(record, dict) or record.get("kind") != which:
            continue
        if _num(record.get("to")) is not None and abs(float(record["to"]) - float(at)) <= INSTANT_EPSILON:
            return record
    return None


def boundary_positions(segment: Dict[str, Any], clip: Dict[str, Any],
                       opening: Dict[str, Any], *, sequence_available: bool = False,
                       decisions: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """What is actually claimed about this boundary, in four separate fields.

    ``boundary_accepted`` used to be computed as ``not held and not flagged``, so an unreviewed candidate
    with no start evidence at all came out accepted: absence of contradiction was read as agreement, and
    "exportable" quietly became "correct". Those are four different questions and the report has to answer
    them separately, because the reviewer's next action differs for each:

    * **exportable** -- may a clip be cut here now. A useful partial with unknown names ships;
    * **machine support** -- does the recovered evidence place an opening here, contradict it, or say
      nothing. ``unknown`` is the answer whenever the evidence is absent, and absence stays unknown: it is
      never promoted to supported by the simple expedient of nobody disagreeing. Evidence only counts about
      the instant it was taken at, and an earlier context screen is somewhere to look rather than a claim
      about this frame;
    * **operator review** -- has a person recorded a decision about this row, and did they accept *this
      boundary*. Acceptance comes from what the governing answers say, never from the mere fact that both
      questions carry a fingerprint: approving a partial is a person accepting lost footage, and that is
      the third thing, not the second;
    * **independent validation** -- is there a reference outside this pipeline and this operator that
      settles it. Nothing on this machine produces one yet, so this is a constant ``False`` with the reason
      attached rather than something a heuristic can turn on.
    """
    review = segment.get("review") or {}
    recovery = segment.get("recovery") or {}
    edge = (recovery.get("edges") or {}).get("start") or {}
    subject_at = _num(opening.get("candidate_at"))
    governing = [str(value) for value in (review.get("decisions") or [])]
    start_answered = any("new_match" in value or "clip_start" in value for value in governing)
    end_answered = any("clip_end" in value for value in governing)
    candidates = ((opening.get("recovered_opening") or {}).get("candidates") or [])
    # Context the rows name *behind* this boundary. A screen that opens somewhere earlier is a place to go
    # and look; counting it as support for the instant under review is how a pre-pairing prompt ten seconds
    # early got reported as a supported set start.
    context_candidates = [item for item in candidates
                          if _num(item.get("at")) is None
                          or subject_at is None or abs(item["at"] - subject_at) > INSTANT_TOLERANCE_SECONDS]
    # Everything below is about the boundary as it stands now. ``start_opening`` was handed the current
    # instant, so a reading taken elsewhere has already been blanked out of ``settled_by`` and
    # ``station_cue_at``, and a contradiction here can only come from a clock read at this frame.
    late = opening.get("late_by_at_least_seconds")

    if late:
        start_support = "contradicted"
        start_why = ("the round clock already reads %d at the candidate, so the round was running %.1f s "
                     "before it" % (opening.get("clock_at_boundary"), late))
    elif opening.get("settled_by") or opening.get("station_cue_at"):
        start_support = "supported"
        start_why = ("the walk settled on a cue at %s"
                     % ((" %.3f" % (opening.get("settled_by") or {}).get("at"))
                        if (opening.get("settled_by") or {}).get("at") is not None else " an earlier row"))
    elif opening.get("opening_at_candidate"):
        here = opening["opening_at_candidate"]
        start_support = "supported"
        start_why = ("the saved rows put %s at this instant (%.3f), which is where a clip would open"
                     % (here.get("state"), here.get("at")))
    elif opening.get("edge_describes_candidate") is False:
        start_support = "unknown"
        start_why = ("the start evidence was read %.3f, where this clip used to open, and nothing here "
                     "names an opening at the instant it opens now (%.3f): the saved rows say %s. That is "
                     "a place to look, not a reading of this frame"
                     % (opening.get("at"), subject_at, _earlier_context_label(context_candidates))
                     if context_candidates else
                     "the start evidence was read %.3f, where this clip used to open, and no saved row "
                     "places an opening at the instant it opens now (%.3f): needs review at this instant"
                     % (opening.get("at"), subject_at))
    elif context_candidates:
        start_support = "unknown"
        start_why = ("the rows name earlier context (%s), which is a place to look, not a reading of this "
                     "instant" % _earlier_context_label(context_candidates))
    elif recovery:
        start_support = "unknown"
        start_why = "the recovery carries no start-edge evidence for this candidate"
    else:
        start_support = "unknown"
        start_why = "this row carries no recovery evidence at all"

    # One label has to carry the case where the pipeline's own readings do not agree, and a bare "supported"
    # would settle nothing by saying it. The label stays what the settlement implies -- withdrawing a
    # machine's own reading because another of its readings disagrees with it would be inventing a stricter
    # state machine than the detector runs -- but the sentence names both frames, and the card offers the
    # footage between them. Which one wins is not a question about algorithms, and the reviewer is not asked
    # to answer it as one.
    # A bound the pipeline moved itself is a fourth state, and folding it into either existing one would lie
    # in the opposite direction. It is not "supported": every reading above was taken where the clip used to
    # open, and no cue was settled at this frame. It is not "unknown" either, because unlike a hand move with
    # nothing behind it, this instant was chosen from recorded evidence, with a basis, a sampling width and a
    # fallback flag still on the row. Reporting ten machine-applied starts as "0 supported / 10 unknown" made
    # the artifact look like it had no idea what it had done.
    machine_bound = _machine_bound_at(segment, "opening", subject_at)
    if machine_bound is not None and start_support != "contradicted":
        start_support = "machine_applied"
        width = machine_bound.get("uncertainty_seconds")
        start_why = ("the pipeline moved this start from %.3f to %.3f on %s evidence, +/- %s; the readings "
                     "quoted above belong to the instant it replaced"
                     % (machine_bound.get("from") or 0.0, machine_bound.get("to") or 0.0,
                        "policy-preferred stage-select"
                        if not machine_bound.get("fallback")
                        else "a flagged load-in fallback (no clean stage select in this match's own run)",
                        "unknown width" if width is None else "%.3f s" % width))
    elif machine_bound is not None:
        machine_bound = dict(machine_bound, contradicted_by_current_reading=True)

    conflict = bool(opening.get("readings_conflict"))
    if conflict:
        dispute = [item for item in opening.get("readings") or [] if item.get("about") == "opening"]
        start_why = ("%s, but the saved evidence does not agree with itself about where that opening is: %s. "
                     "Watch the frames between them; neither cue decides the other"
                     % (start_why, "; ".join("a row of %s at %s" % (item["sees"], instant_text(item["at"]))
                                             for item in dispute)))

    end = (recovery.get("clip_end_basis")
           or ("padded conclusion end at %.3f" % recovery["clip_end_at"]
               if _num(recovery.get("clip_end_at")) is not None else ""))
    if clip.get("human_governs"):
        end = "an operator end recorded at review"
    if clip.get("held"):
        end_support = "contradicted"
        end_why = "; ".join(clip.get("reasons") or ["the clip does not contain the evidence it cites"])
    elif end:
        end_support = "supported"
        end_why = end
    else:
        end_support = "unknown"
        end_why = "no conclusion evidence and no recorded end"

    # The answers' *meaning* is a different fact from the answers' existence, and it is the one acceptance
    # has to follow. The journal is read first and :meth:`ReviewStore.recompute`'s projection only stands
    # in for a project that carries no decision records, so a row cannot be certified by the bare presence
    # of a fingerprint -- and a partial stays a partial however the two questions were ordered.
    start_says = _governing_answer(decisions, ("new_match", "clip_start")) or str(review.get("grouping")
                                                                                 or "")
    end_says = _governing_answer(decisions, ("clip_end",)) or str(
        (review.get("ends") or {}).get("answer") or "")
    start_answered = start_answered or bool(_governing_answer(decisions, ("new_match", "clip_start")))
    end_answered = end_answered or bool(_governing_answer(decisions, ("clip_end",)))
    start_confirms = start_says in START_CONFIRMING_ANSWERS
    end_confirms = end_says in END_CONFIRMING_ANSWERS
    opening_partial = (start_says == "accept_partial"
                       or bool((review.get("openings") or {}).get("approved_partial")))
    lost_footage = bool(clip.get("excluded_gameplay")) and bool(clip.get("approved"))
    partial_approval = opening_partial or lost_footage

    reviewed = bool(governing or decisions)
    accepted = bool((start_confirms and end_confirms and not partial_approval)
                    or review.get("boundaries_accepted"))
    return {
        "exportable": not bool(clip.get("held")),
        "machine_supports_start": start_support, "start_why": start_why,
        "machine_bound": machine_bound,
        "start_readings": opening.get("readings") or [],
        "start_readings_conflict": conflict,
        "start_inspect_window": opening.get("inspect_window"),
        "machine_supports_end": end_support, "end_why": end_why,
        "operator_reviewed": reviewed,
        "operator_answered": {"start": start_answered, "end": end_answered},
        "operator_confirms": {"start": start_confirms, "end": end_confirms},
        "partial_approval": {"opening_approved_partial": opening_partial,
                             "end_excludes_gameplay": lost_footage},
        "operator_accepted_boundary": accepted,
        # Proposed versus current, side by side, so a reviewer can see what a machine is asking for without
        # being shown a boundary that has already moved.
        "opening_proposal": pending_proposal(segment, "opening"),
        "proposed_start": _num((pending_proposal(segment, "opening") or {}).get("at")),
        "end_proposal": pending_proposal(segment, "end"),
        "proposed_end": _num((pending_proposal(segment, "end") or {}).get("at")),
        "plan_basis": ("operator boundary; a machine proposal is still pending on it"
                       if pending_proposal(segment, "opening") or pending_proposal(segment, "end")
                       else "operator boundary" if accepted else "current boundary, unaccepted"),
        "machine_proposal_pending": bool(pending_proposal(segment, "opening")
                                         or pending_proposal(segment, "end")),
        "partial_override": partial_approval,
        "independently_validated": False,
        "validation_why": ("no reference outside this pipeline and this operator has been built for this "
                           "recording, so no boundary here is independently validated; a person accepting "
                           "a boundary is an authority about their decision, not a second measurement"),
        "sequence_considered": bool(sequence_available),
        "claim_this_row_may_make": (
            "held for review" if clip.get("held") else
            "exportable; operator-accepted, not independently validated" if accepted else
            "exportable; operator approved a partial here, not a complete accepted boundary"
            if partial_approval else
            "exportable; boundary not accepted by anyone"),
    }


def export_plan(project: Dict[str, Any]) -> Dict[str, Any]:
    """What an export would write, what it must not, and which question releases each hold.

    A held clip stays in the plan with its reasons instead of vanishing: silently skipping a clip is how a
    review produces a folder of ten videos that quietly omits the one nobody answered for. Unknown player
    and winner labels do *not* hold a clip -- a nameless match is still a match the operator can publish --
    but an unresolved boundary, a claimed interval that crosses a neighbour, or a clip that loses the
    reading it cites does, until a person either fixes it or approves it in writing.
    """
    segments = project.get("matches") or []
    duration = _num(project.get("duration"))
    sequence_rows, sequence_provenance = project_sequence(project)
    # The journal is read here rather than through a store, because an export plan must describe the
    # project exactly as it is on disk: what a person decided, not what some caller last recomputed.
    journal = [d for d in ((project.get("review") or {}).get("decisions") or []) if _governs(d)]
    clips: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments):
        following = segments[index + 1] if index + 1 < len(segments) else None
        clip = resolve_clip_end(segment, following=following, duration=duration)
        start = _num(segment.get("start"))
        end = clip.get("end")
        basis = clip["basis"]
        reasons = list(clip.get("warnings") or [])
        held = bool(clip.get("held")) or bool(clip.get("skipped_reason"))
        if end is None and not (segment.get("recovery") or {}):
            # A hand-marked row with no end is the operator's own unfinished mark. The app has always
            # closed it at the next mark, and at the end of the tape when there is none. A recovered row
            # is a different claim and never takes either fallback.
            next_start = _num((following or {}).get("start"))
            if next_start is not None and start is not None and next_start > start:
                end, basis, held = next_start, "hand-marked row closed at the next mark", False
                reasons.append("closed at the next match's opening %.3f; this project carries no recovery "
                               "evidence, so that is your convention, not a detected boundary" % next_start)
            elif duration is not None and start is not None and duration > start:
                end, basis, held = duration, "hand-marked row closed at the end of the recording", False
                reasons.append("closed at the end of the recording %.3f" % duration)
        entry = {"index": index, "start": start, "end": end, "basis": basis, "held": held,
                 "reasons": reasons, "severity": clip.get("severity"),
                 "human_governs": clip.get("human_governs"), "approved": clip.get("approved"),
                 "excluded_gameplay": clip.get("excluded_gameplay") or [],
                 "included_past_recorded_end": clip.get("included_past_recorded_end") or [],
                 "review_kind": clip.get("review_kind"),
                 "player1": segment.get("player1", ""), "player2": segment.get("player2", ""),
                 "orientation": (segment.get("review") or {}).get("orientation"),
                 "names_known": bool(segment.get("player1") and segment.get("player2"))}
        if end is not None and start is not None and end <= start:
            entry["held"] = held = True
            entry["reasons"] = reasons + ["end %.3f is not after start %.3f" % (end, start)]
        clips.append(entry)
    for clip in clips:
        segment = segments[clip["index"]]
        opening = start_opening(segment.get("recovery") or {}, rows=sequence_rows,
                                candidate_at=_num(segment.get("start")))
        approved = bool(((segment.get("review") or {}).get("openings") or {}).get("approved_partial"))
        clip["opening"] = {"flagged": opening["flagged"],
                           "late_by_at_least_seconds": opening["late_by_at_least_seconds"],
                           "earliest_supported_opening": opening["earliest_supported_opening"],
                           "why": opening["contradicts"], "approved_partial": approved,
                           "recovered_opening_at": (opening.get("recovered_opening") or {}).get(
                               "earliest_candidate"),
                           # The round's own recovered opening, kept apart from the context candidate above:
                           # "a controller screen 45 s back" and "this round was announced at 785.785" are
                           # different claims about different frames, and a reader comparing the two has to
                           # be able to see which is which. The uncertainty travels with the instant so no
                           # consumer can render the interval as a timestamp.
                           "round_opening": (opening.get("recovered_opening") or {}).get("round_opening"),
                           "recovered_opening_level": (opening.get("recovered_opening") or {}).get(
                               "earliest_candidate_level"),
                           "recovered_opening_status": (opening.get("recovered_opening") or {}).get(
                               "status"),
                           "set_membership_decided": ((opening.get("recovered_opening") or {}).get(
                               "set_membership") or {}).get("decided"),
                           "needs_reading": opening["needs_reading"]}
        clip["opening_review_required"] = bool(opening["flagged"] and not approved)
        clip["boundary"] = boundary_positions(
            segment, clip, opening, sequence_available=bool(sequence_rows),
            decisions=[d for d in journal
                       if _num(d.get("segment_start")) == _num(segment.get("start"))])
        orphan = (segment.get("review") or {}).get("no_candidate")
        if orphan and not ((segment.get("review") or {}).get("decisions")):
            # A retained row whose evidence vanished is not evidence of anything on its own, so it must not
            # quietly become an exportable clip just because the arithmetic found bounds for it.
            clip["held"] = True
            clip["reasons"] = list(clip["reasons"]) + [
                "the newest import has no candidate at this opening (kept at %.3f); the row is held until "
                "a person re-claims it or deletes it" % (clip["start"] or 0.0)]
            clip["severity"] = "blocking"
            clip["review_kind"] = clip["review_kind"] or "new_match"

    coverage = coverage_accounting(project, clips)
    unplaced = coverage["uncovered_by_exportable_jobs"]
    held_clips = [clip for clip in clips if clip["held"]]
    return {"clips": clips, "held": held_clips, "exportable": [c for c in clips if not c["held"]],
            "unplaced_play": unplaced, "coverage": coverage,
            "opening_review": [clip for clip in clips if clip["opening_review_required"]],
            "counts": {"segments": len(clips), "exportable": len(clips) - len(held_clips),
                       "held": len(held_clips), "unplaced_intervals": len(unplaced),
                       "unplaced_seconds": round(sum(row["seconds"] for row in unplaced),
                                                INSTANT_PRECISION),
                       "openings_needing_review": len([c for c in clips
                                                       if c["opening_review_required"]]),
                       "operator_accepted": len([c for c in clips
                                                 if c["boundary"]["operator_accepted_boundary"]]),
                       # Counted apart from every one of those, because a proposal is none of them: no row
                       # moved, no question closed, no boundary accepted. It is work still offered.
                       "machine_proposals_pending": len([c for c in clips
                                                         if c["boundary"].get("machine_proposal_pending")]),
                       # Counted apart from acceptance, because a partial approved as a partial is neither
                       # an accepted boundary nor an unreviewed row: it is the third thing, and a total that
                       # folded it into either of the other two would be a claim about the wrong one.
                       "operator_approved_partials": len([c for c in clips
                                                          if c["boundary"]["partial_override"]]),
                       "machine_supported_starts": len([c for c in clips
                                                        if c["boundary"]["machine_supports_start"]
                                                        == "supported"]),
                       "unknown_starts": len([c for c in clips
                                              if c["boundary"]["machine_supports_start"] == "unknown"]),
                       # Reported apart from supported and apart from unknown, and the fallback half named,
                       # because "0 openings need review" must never be read as "the machine is sure".
                       "machine_applied_starts": len([c for c in clips
                                                      if c["boundary"].get("machine_supports_start")
                                                      == "machine_applied"]),
                       "machine_applied_starts_fallback": len([
                           c for c in clips
                           if c["boundary"].get("machine_supports_start") == "machine_applied"
                           and (c["boundary"].get("machine_bound") or {}).get("fallback")]),
                       "independently_validated": 0,
                       "covered_seconds_by_candidates": coverage["candidates"]["seconds"],
                       "covered_seconds_by_jobs": coverage["exportable_jobs"]["seconds"],
                       "uncovered_seconds_by_jobs": round(sum(row["seconds"] for row in unplaced),
                                                           INSTANT_PRECISION)},
            "exportable_is_not_accepted": bool([c for c in clips if c["opening_review_required"]]),
            "sequence": sequence_provenance, "inference_sent": False, "media_rendered": False}


# -------------------------------------------------------------------------------- re-import and identity

def reconcile_import(project: Dict[str, Any], incoming: Sequence[Dict[str, Any]], *,
                     tolerance: float = IDENTITY_TOLERANCE_SECONDS) -> Dict[str, Any]:
    """Fold a fresh recovery into a project by identity, not by string equality of one field.

    Keying only on ``start`` made a 1-second move of a boundary into two rows for the same footage, and
    made a same-start replacement throw away the names an operator had typed. So matching is explicit and
    every outcome is recorded where a reviewer can read it:

    * exact start, or a start within ``tolerance`` with the interval still overlapping -> the same set.
      The human answers stay attached, and the ones whose evidence moved are invalidated, not kept;
    * an interval that overlaps without matching a start -> both rows are kept and a split/merge question
      is opened, because either reading is a claim about footage nobody has watched yet;
    * a previous row a person decided something about but no candidate matches -> kept and flagged,
      because discarding a decision silently is worse than a stale row.
    """
    store = ReviewStore(project)
    existing = list(project.get("matches") or [])
    log: List[Dict[str, Any]] = []
    used: set = set()

    def overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
        a_start, a_end = _num(a.get("start")), _num(a.get("end"))
        b_start, b_end = _num(b.get("start")), _num(b.get("end"))
        if None in (a_start, b_start):
            return 0.0
        a_end = _num(a_end) if a_end is not None else a_start
        b_end = _num(b_end) if b_end is not None else b_start
        shared = min(a_end, b_end) - max(a_start, b_start)
        return max(0.0, shared)

    for candidate in incoming:
        start = _num(candidate.get("start"))
        match = None
        how = ""
        for position, previous in enumerate(existing):
            if position in used:
                continue
            previous_start = _num(previous.get("start"))
            if previous_start is None or start is None:
                continue
            if abs(previous_start - start) <= 1e-6:
                match, how, matched_index = previous, "exact_start", position
                break
        if match is None:
            near = [(abs(_num(previous.get("start")) - start), position, previous)
                    for position, previous in enumerate(existing)
                    if position not in used and _num(previous.get("start")) is not None
                    and abs(_num(previous.get("start")) - start) <= tolerance
                    and overlap(previous, candidate) > 0]
            if near:
                _, matched_index, match = min(near, key=lambda row: row[0])
                how = "moved_start"
        if match is None:
            for position, previous in enumerate(existing):
                if position in used:
                    continue
                shared = overlap(previous, candidate)
                shorter = min(_duration_of(previous), _duration_of(candidate))
                if shared > 0 and shorter > 0 and shared / shorter >= 0.5:
                    match, how, matched_index = previous, "overlap", position
                    break
        if match is None:
            existing.append(candidate)
            used.add(len(existing) - 1)
            log.append({"imported_start": start, "outcome": "added"})
            continue
        used.add(matched_index)
        previous_start = _num(match.get("start"))
        changed = _changed_evidence(match, candidate)
        if how == "overlap":
            existing.append(candidate)
            used.add(len(existing) - 1)
            match.setdefault("review", {})["contending"] = _num(candidate.get("start"))
            log.append({"imported_start": start, "matched_start": previous_start, "outcome": "overlap",
                        "note": "both rows kept; a split/merge question is open"})
            continue
        reasons = []
        if changed:
            reasons.append("evidence changed on re-import: " + ", ".join(
                "%s %s -> %s" % (key, _shown(before), _shown(after)) for key, before, after in changed))
        if how == "moved_start":
            reasons.append("the boundary moved from %.3f to %.3f on re-import" % (previous_start, start))
        if reasons:
            # Withdrawn while the row still carries the old evidence, so the recompute inside sees the
            # same baseline the answer was originally given against.
            store.invalidate(previous_start, "; ".join(reasons))
        preserved = _preserve_human_fields(match, candidate, how)
        match["recovery"] = candidate.get("recovery") or {}
        match["start"] = _num(candidate.get("start"))
        match["end"] = _num(candidate.get("end"))
        for field in ("player1", "player2", "character1", "character2", "round_name", "notes"):
            match[field] = candidate.get(field, match.get(field))
        if how == "moved_start":
            for decision in store.decisions_for(previous_start):
                decision["segment_start"] = start
                decision["identity"] = {"start_was": previous_start, "now": start, "matched_by": how}
        store.recompute(match)
        log.append({"imported_start": start, "matched_start": previous_start, "outcome": how,
                    "preserved_fields": sorted(preserved),
                    "invalidated_because": [key for key, _before, _after in changed]
                    + (["boundary"] if how == "moved_start" else [])})
    for position, previous in enumerate(existing):
        if position in used:
            continue
        decided = [d for d in store.decisions_for(previous.get("start")) if _governs(d)]
        # The row stays either way, and the log now says what actually happens to it. Deleting it here used
        # to be reported as "dropped_machine_row" while the row went straight back out in the returned
        # project -- a reconciliation nobody could trust. Dropping claimed footage is also the wrong
        # default: the newest import not naming a match at 30 s is an absence of evidence, not evidence
        # that nothing was played, and a silent delete takes that stretch out of the coverage report
        # exactly when it most wants looking at. So the row is kept, marked, and held unless a person has
        # already decided something about it -- which is what makes the retention observable in an export.
        previous.setdefault("review", {})["no_candidate"] = {
            "note": "the newest import has no candidate at this opening; the row is kept, not dropped",
            "kept_because": ("a person decided something about it" if decided
                             else "deleting it would silently take claimed footage out of the coverage"),
            "decisions": [d["fingerprint"] for d in decided]}
        log.append({"imported_start": None, "matched_start": _num(previous.get("start")),
                    "outcome": "retained_without_candidate", "decisions": len(decided),
                    "export_effect": ("unchanged: a governing decision already claims this row" if decided
                                      else "held: no job will render it, and its interval counts as "
                                           "uncovered footage")})
    project["matches"] = sorted(existing, key=lambda s: (_num(s.get("start")) is None,
                                                          _num(s.get("start")) or 0.0))
    store.state.setdefault("reconciliations", []).append(
        {"outcomes": log, "inference_sent": False})
    return project


def _duration_of(segment: Dict[str, Any]) -> float:
    start, end = _num(segment.get("start")), _num(segment.get("end"))
    if start is None:
        return 0.0
    return max(0.0, (end if end is not None else start) - start)


def _shown(value: Any) -> str:
    return "none" if value is None else ("%.3f" % value if isinstance(value, float) else str(value))


def _changed_evidence(previous: Dict[str, Any], candidate: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    old, new = previous.get("recovery") or {}, candidate.get("recovery") or {}
    changed = []
    for key in ("clip_end_at", "game_wins", "membership", "footage_complete"):
        before, after = old.get(key), new.get(key)
        if _num(before) is not None and _num(after) is not None and key == "clip_end_at":
            if abs(_num(before) - _num(after)) > 1e-9:
                changed.append((key, before, after))
        elif before != after and after is not None:
            changed.append((key, before, after))
    return changed


def _preserve_human_fields(previous: Dict[str, Any], candidate: Dict[str, Any],
                           how: str) -> Dict[str, Any]:
    """Never lose a name an operator typed, and never launder the machine's new guess past it."""
    preserved: Dict[str, Any] = {}
    review = previous.setdefault("review", {})
    edits = review.get("field_edits") or {}
    for field in ("player1", "player2", "character1", "character2", "round_name", "notes"):
        was, now = previous.get(field), candidate.get(field)
        if was == now:
            continue
        if edits.get(field, {}).get("source") in PRESERVE_EDIT_SOURCES:
            candidate[field] = was
            preserved[field] = "kept human value against the new suggestion"
        elif was and not now:
            candidate[field] = was
            preserved[field] = "carried forward: the new import has no value for it"
        elif was and now:
            candidate.setdefault("field_history", []).append({"was": was, "now": now, "matched_by": how})
    return preserved


def apply_opening_proposals(project: Dict[str, Any], *, rows: Optional[Sequence[Dict[str, Any]]] = None,
                            generated_by: str = "opening_sequence") -> Dict[str, Any]:
    """File the opening improvements the saved sequence itself supports, on every recovered row.

    Generic by construction: the instant, its level, its sampling width and the first frame showing play all
    come from :func:`_adjust_affordance`, which is the same reading the review card already shows. Nothing
    here names a recording, a timestamp, a stage or a person, and nothing here decides a boundary -- a
    proposal moves no row and answers no question.

    Only a **round-opening** claim is filed automatically. The levels below that (`game_opening_context`,
    `pre_pairing_context`) are context the walk reached, and the recovered block carries its own
    ``does_not_prove`` line for them: a fresh game list is consistent with a new set and with a stage pick
    between two games of one set, so context cannot say where a set starts. Filing those as proposals would
    launder a look-back window into a suggested cut. They stay what they are on the card: evidence an
    operator may act on, with the reason it does not settle the question.

    Returns counts plus one line per row, so a caller can report what was automatic without a reviewer
    having to infer it from the file.
    """
    if rows is None:
        rows, _provenance = project_sequence(project)
    store = ReviewStore(project)
    filed: List[Dict[str, Any]] = []
    ordered = sorted((s for s in store._segments() if _num(s.get("start")) is not None),
                     key=lambda s: float(s["start"]))
    boundaries = [None] + [_num(s.get("end")) for s in ordered[:-1]]
    for segment, previous_end in zip(ordered, boundaries):
        start = _num(segment.get("start"))
        recovery = segment.get("recovery") or {}
        if start is None:
            continue
        opening = start_opening(recovery, rows=rows, candidate_at=start)
        adjust = _adjust_affordance(start, opening)
        at, level = adjust.get("jump_to"), adjust.get("jump_to_level")
        row_here = next((r for r in rows or []
                         if abs(float(r.get("timestamp") or 0.0) - start) <= INSTANT_EPSILON), None)
        policy = policy_clip_start(
            rows, candidate_at=start,
            pairing=((row_here or {}).get("player1"), (row_here or {}).get("player2")),
            opening_at=(float(adjust["jump_to"]) if level == "round_opening" and adjust.get("jump_to")
                        is not None else None))
        # Which instant the clip should open on is the cutting policy's question, and the policy has an answer
        # of last resort too: with no clean stage-select occurrence, offer the earliest frame of this match's
        # own load-in and label it a fallback flagged for review. That is still a machine result the reviewer
        # can accept or refuse, and it beats leaving two known clipped openings for somebody to notice.
        # What it must never do is reach back across the set before this one.
        walk_at, walk_basis = (float(at) if at is not None else None), level
        cue = float(at) if level == "round_opening" and at is not None else None
        preferred = float(policy["at"]) if policy.get("at") is not None and not policy["fallback"] else None
        chosen = None
        # Precedence, and what each step refuses to do:
        #   1. a policy-preferred stage-select occurrence, the earliest point the policy endorses;
        #   2. otherwise the round's own recovered opening cue;
        #   3. otherwise this match's own load-in chain, filed as a flagged fallback for review;
        #   4. never the walk's bare context (`pre_pairing_context`, `game_opening_context`), which is the
        #      edge of a look-back window and not a claim about where this set opens.
        if preferred is not None:
            chosen = (preferred, policy["basis"])
        elif cue is not None and (preferred is None and policy.get("at") is not None
                                  and cue <= float(policy["at"]) + INSTANT_EPSILON):
            chosen = (cue, level)
        elif policy.get("at") is not None:
            chosen = (float(policy["at"]), policy["basis"])
        elif cue is not None:
            chosen = (cue, level)
        at, level = chosen if chosen else (None, None)
        if at is None:
            filed.append({"start": start, "outcome": "no-evidence",
                          "why": policy.get("review_reason")
                                 or (opening.get("recovered_opening") or {}).get("basis")
                                 or "no saved sequence row reaches this opening"})
            continue
        if previous_end is not None and float(at) <= float(previous_end) + INSTANT_EPSILON:
            filed.append({"start": start, "outcome": "refused-crossing", "at": float(at),
                          "why": "the only earlier anchor found (%.3f) is at or before the end of the set "
                                 "before this one (%.3f), and a proposal may not reach into the previous "
                                 "match" % (float(at), float(previous_end))})
            continue
        if float(at) >= start - INSTANT_EPSILON:
            filed.append({"start": start, "outcome": "already-supported", "at": float(at), "level": level,
                          "why": "the row is already at the earliest instant the sequence supports"})
            continue
        first_active = adjust.get("first_active_gameplay_at")
        preserves = "play" if first_active is not None and float(first_active) <= start + INSTANT_EPSILON \
            else "presentation"
        offered_row = next((row for row in rows or []
                            if abs(float(row.get("timestamp") or 0.0) - float(at)) <= INSTANT_EPSILON), None)
        frame = (bg.parse_round_facts(dict(offered_row, timestamp=float(offered_row.get("timestamp") or 0.0)))
                 or {}).get("frame") if offered_row else None
        width = (policy.get("uncertainty_seconds") if walk_basis != "round_opening"
                 or float(at) < (walk_at or 0.0) - INSTANT_EPSILON
                 else adjust.get("jump_to_uncertainty_seconds"))
        if policy.get("at") is not None and abs(float(at) - float(policy["at"])) <= INSTANT_EPSILON \
                and policy.get("fallback"):
            width = policy.get("uncertainty_seconds")
            why = ("no clean stage-select occurrence exists in this match's own load-in, so the earliest "
                   "frame of that load-in at %.3f s is offered as a flagged fallback; the clip starts at "
                   "%.3f s%s" % (float(at), start,
                                 # A one-row chain has no step to measure, and saying "0.000 s" would turn an
                                 # absence of evidence into a precision the tape never had.
                                 " and the sampling does not bound where that run begins"
                                 if width is None else
                                 " with %.3f s of sampling uncertainty about where the run begins" % width))
        elif policy.get("at") is not None and abs(float(at) - float(policy["at"])) <= INSTANT_EPSILON:
            why = ("the cutting policy prefers the final confident stage-select occurrence before the set: "
                   "%.3f s, where the clip currently opens at %.3f s" % (float(at), start))
        else:
            why = ("the saved sequence recovers this round's own opening at %.3f s; the clip starts at %.3f s"
                   % (float(at), start))
        record = proposal_record("opening", float(at), why=why,
            evidence={"level": level, "preserves": preserves, "frame": frame,
                  "policy_basis": policy.get("basis"), "policy_fallback": policy.get("fallback"),
                  "policy_review_reason": policy.get("review_reason"),
                  "policy_chain_rows": len(policy.get("chain") or []),
                  "policy_chain": (policy.get("chain") or [])[:12],
                  "walk_instant": walk_at, "walk_level": walk_basis,
                  "seconds_before_candidate": round(start - float(at), INSTANT_PRECISION),
                      "candidate_state": (opening.get("recovered_opening") or {}).get("candidate_state"),
                      "first_active_at": first_active,
                      "first_active_status": adjust.get("first_active_status"),
                      "seconds_into_running_round": (opening.get("recovered_opening") or {})
                      .get("candidate_is_seconds_into_a_running_round"),
                      "active_onset_uncertainty_seconds": adjust.get("first_active_uncertainty_seconds")},
            uncertainty_seconds=width,
            generated_by=generated_by)
        try:
            result = store.propose({"start": start}, record)
        except InvalidAnswer as exc:
            filed.append({"start": start, "outcome": "refused", "at": float(at), "why": str(exc)})
            continue
        filed.append({"start": start, "outcome": "unchanged" if result.get("unchanged") else "filed",
                      "at": float(at), "level": level, "preserves": preserves,
                      "proposal_id": result.get("proposal_id"),
                      "uncertainty_seconds": result.get("uncertainty_seconds"),
                      "policy_fallback": bool(policy.get("fallback")),
                      "seconds_before_candidate": round(start - float(at), INSTANT_PRECISION),
                      "previously_answered": result.get("already_answered")})
    counts = {name: sum(1 for item in filed if item["outcome"] == name)
              for name in ("filed", "unchanged", "already-supported", "editorial-only", "no-evidence",
                           "refused")}
    return {"counts": counts, "rows": filed, "generated_by": generated_by,
            "moves_no_boundary": True, "counts_as_decision": False}


def apply_machine_bounds(project: Dict[str, Any]) -> Dict[str, Any]:
    """Move clip bounds to the pipeline's own supported instants, and record it as machine work.

    This is deliberately **not** a reviewer action. It does not create a decision, does not write
    ``review["ends"]`` (the field whose existence means "a human chose this end"), does not touch
    ``acceptance``, and refuses any boundary a person has already answered. What it does is let the recovery
    finish its own arithmetic: where the sequence and the declared cutting policy support an earlier opening,
    the plan should say so without waiting for ten routine clicks that add no information.

    Everything the reviewer would need in order to disagree stays on the row: the instant the pipeline used
    before, the one it uses now, the basis, the sampling width, the fallback flag and the evidence behind it.
    Owner answers still outrank it, and a later pass may not overwrite what a person set.
    """
    store = ReviewStore(project)
    applied, skipped = [], []
    for segment in store._segments():
        start = _num(segment.get("start"))
        if start is None:
            continue
        review = segment.setdefault("review", {}) or {}
        for kind, question in (("opening", "new_match"), ("end", "clip_end")):
            offered = pending_proposal(segment, kind)
            if offered is None:
                continue
            governed = any(d.get("kind") == question and _governs(d)
                           and _num(d.get("segment_start")) == start for d in store.state["decisions"])
            if governed:
                skipped.append({"start": start, "kind": kind, "why": "a person has answered this boundary"})
                continue
            target = float(offered["at"])
            if kind == "opening":
                previous_end = None
                ordered = sorted((s for s in store._segments() if _num(s.get("start")) is not None),
                                 key=lambda s: float(s["start"]))
                before = [s for s in ordered if float(s["start"]) < target]
                previous_end = _num(before[-1].get("end")) if before else None
                if previous_end is not None and target <= previous_end + INSTANT_EPSILON:
                    skipped.append({"start": start, "kind": kind,
                                    "why": "the machine instant %.6f would reach into the set ending at %.6f"
                                           % (target, previous_end)})
                    continue
                record, note = {"from": start, "to": target}, "start"
                segment["start"] = target
                if _num(segment.get("end")) is not None and segment["end"] <= target:
                    segment["start"] = start
                    skipped.append({"start": start, "kind": kind, "why": "the move would invert the clip"})
                    continue
            else:
                record, note = {"from": _num(segment.get("end")), "to": target}, "end"
                segment["end"] = target
            record.update({"kind": kind, "basis": offered.get("evidence", {}).get("policy_basis")
                                     or offered.get("level"),
                           "fallback": bool(offered.get("evidence", {}).get("policy_fallback")),
                           "uncertainty_seconds": offered.get("uncertainty_seconds"),
                           "evidence": offered.get("evidence") or {}, "why": offered.get("why"),
                           "proposal_id": offered.get("proposal_id"), "applied_by": "pipeline",
                           "authoritative": False, "operator_decision": False})
            machine = review.setdefault("machine_bounds", [])
            machine.append(record)
            segment["review"] = review
            offered["status"] = PROPOSAL_APPLIED
            offered["applied_by"] = "pipeline"
            offered["operator_decision"] = False
            applied.append({"start": start, **note_and(record, note)})
    store.state["log"].append({"action": "machine_bounds", "applied": len(applied)})
    # Refreshed here, at the end, because the sentence it writes quotes how many bounds there were, and a
    # disclosure generated before the numbers exist is a disclosure about a different file.
    project.setdefault("review", {})["reading_the_summary"] = reading_the_summary(project)
    return {"applied": applied, "skipped": skipped, "counts_as_decision": False,
            "operator_acceptance": False,
            "note": ("machine bounds: the pipeline's own best supported instants. No decision was recorded, "
                     "no end was marked human, and a person's answer still outranks every entry here")}


def note_and(record, which):
    return {"kind": record["kind"], which: [record["from"], record["to"]]}


def reading_the_summary(project: Dict[str, Any]) -> str:
    """How to read this project's own counters, written from the counters that are actually in the file.

    This text used to exist only because a hand-run build step pasted it into the delivered copy. A
    pipeline-only rebuild lost it, and the number it was there to qualify -- ``openings_needing_review: 0``
    -- then read as "the machine is sure", which is the opposite of what a zero of that kind means. So it is
    generated here, from the same plan the summary line prints, and travels with every project.

    It is a disclosure, not a verdict: it counts what is in the file and says nothing was validated.
    """
    counts = export_plan(project)["counts"]

    def value(key):
        return counts.get(key) or 0

    parts = [
        "machine_applied_starts counts bounds the pipeline moved on recorded evidence (of these, "
        "machine_applied_starts_fallback were chosen where no clean stage-select occurrence existed). They "
        "are neither operator-accepted nor cue-supported at the new instant: the readings quoted beside a "
        "row belong to the instant it replaced.",
        "machine_supported_starts and unknown_starts are both %d here, and that is not the same statement "
        "as openings_needing_review being %d." % (value("machine_supported_starts"),
                                                  value("openings_needing_review")),
    ]
    if value("machine_proposals_pending"):
        parts.append("%d machine proposal(s) are pending: nothing was accepted by applying one, and no row "
                     "was moved by a person." % value("machine_proposals_pending"))
    segments = list(project.get("matches") or [])
    tiers = {}
    decisive = 0
    for segment in segments:
        report = (segment.get("recovery") or {}).get("attribution_confidence") or {}
        if report:
            tiers[report.get("tier")] = tiers.get(report.get("tier"), 0) + 1
            estimate = report.get("winner_estimate") or {}
            if estimate.get("name") and estimate.get("status") == "decisive":
                decisive += 1
    if tiers:
        parts.append(
            "attribution_confidence on a set is heuristic confidence in a *name*, not a probability and not "
            "a verdict: %s. A winner_estimate is published beside the authoritative set_winner and replaces "
            "nothing -- %d set(s) carry a name whose arithmetic closes every target they admit, and no tier "
            "reaches high because every read behind them is the one broadcast graphic, which cannot "
            "corroborate itself."
            % (", ".join("%d %s" % (count, tier) for tier, count in sorted(tiers.items())), decisive))
    parts.append("independently_validated is %d, and no count in this file establishes otherwise."
                 % value("independently_validated"))
    return " ".join(parts)


def merge_into_project(project: Dict[str, Any], segments: Sequence[Dict[str, Any]], *,
                       event: str = "", duration: Optional[float] = None,
                       sequence_source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fold imported recovery into a project without discarding anything a person already decided.

    Re-importing after a new pass is the normal case, and the dangerous one: the machine's newest
    suggestion must update the evidence while a human decision, once made, stays until a person changes it
    -- marked invalidated if the evidence it cited has moved. The identity work itself is in
    :func:`reconcile_import`; this keeps the surrounding project defaults so a CLI caller still gets a
    project the app can open.
    """
    out = project if isinstance(project, dict) else {}
    out.setdefault("matches", [])
    out.setdefault("review", {"version": REVIEW_VERSION, "decisions": [], "log": []})
    reconcile_import(out, segments)
    if sequence_source:
        # A re-import may restate where the rows are; it never silently drops a source the reviewer's
        # existing decisions were made against.
        out["sequence_source"] = sequence_source
    # The same evidence pass the card reads, filed as proposals. Done here rather than in the CLI so every
    # normal entry point -- command line, app import, a test building a project by hand -- offers a reviewer
    # the openings the recording actually supports instead of leaving that to whoever wrote a script.
    # Inside ``review``, not beside it: ProjectState.to_dict writes an explicit list of top-level fields, so
    # a key added at the top of the document survives this import and vanishes the first time the operator
    # saves. review travels as one dict and is kept.
    out.setdefault("review", {"version": REVIEW_VERSION, "decisions": [], "log": []})
    out["review"]["automatic_proposals"] = apply_opening_proposals(out)
    out["review"]["reading_the_summary"] = reading_the_summary(out)
    if event:
        out["event_name"] = event
    if duration is not None:
        out["duration"] = float(duration)
    out.setdefault("video_path", "")
    out.setdefault("players", [])
    out.setdefault("characters", [])
    for segment in out["matches"]:
        for field in ("player1", "player2"):
            value = segment.get(field)
            if value and value not in out["players"]:
                out["players"].append(value)
        for field in ("character1", "character2"):
            value = segment.get(field)
            if value and value not in out["characters"]:
                out["characters"].append(value)
    return out


def load_project(path: str | Path) -> Dict[str, Any]:
    project = json.loads(Path(path).read_text(encoding="utf-8"))
    ReviewStore(project).recompute_all()
    return project
