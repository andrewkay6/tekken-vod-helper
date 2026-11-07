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

import copy
import json
import re
from pathlib import Path
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
                "alternative_groupings": candidate.get("alternative_groupings") or [],
                "ambiguous_links_cut": candidate.get("ambiguous_links_cut", 0),
                "boundary_conflicts": candidate.get("boundary_conflicts") or [],
                "missing_evidence": candidate.get("missing_evidence") or [],
                "unresolved_claims": candidate.get("unresolved_claims") or [],
                "published_game_count": published if isinstance(published, int) else None,
                "edges": candidate.get("edges") or {},
                "attribution_withheld": candidate.get("attribution_withheld"),
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
    if human_end is not None:
        end, basis = human_end, "human decision"
    elif machine_end is not None:
        end, basis = machine_end, str(recovery.get("clip_end_basis") or "recovery padded end")
    elif recorded_end is not None:
        end, basis = recorded_end, "recorded set end (no padded conclusion end was published)"
    else:
        end, basis = None, "no end evidence"
    if end is not None and confirmed is not None and end < confirmed - 1e-9:
        # The claim and the number disagree. Saying so is the fix; extending through the next opening
        # without being asked would be the same mistake in the other direction.
        warnings.append(
            "clip ends at %.3f but the winner confirmation is at %.3f: the exported clip does not contain "
            "the reading that names the winner, and the pipeline's own basis text says it does"
            % (end, confirmed))
        excluded.append({"from": end, "to": confirmed, "what": "winner confirmation"})
    if following is not None and end is not None:
        next_start = _num(following.get("start"))
        if next_start is not None and end > next_start + 1e-9:
            warnings.append("clip end %.3f runs past the next candidate's start %.3f" % (end, next_start))
    if end is None:
        next_start = _num((following or {}).get("start"))
        return {"end": None, "basis": basis, "warnings": warnings + [
            "no conclusion evidence and no human end; refusing to default to the next mark at %s"
            % ("%.3f" % next_start if next_start is not None else "an unknown position")],
                "excluded_gameplay": excluded, "included_past_recorded_end": [], "severity": "blocking",
                "skipped_reason": "unresolved end", "held": True, "human_governs": False,
                "approved": False, "review_kind": "clip_end",
                "suggested_end": next_start, "suggestion_needs_approval": True}
    clamped = False
    if duration is not None and end > float(duration) + 1e-9:
        warnings.append("clip end %.3f exceeds the recording length %.3f; clamped" % (end, float(duration)))
        end = float(duration)
        clamped = True
    if recorded_end is not None and end > recorded_end + 1e-9:
        # Padding walks forward on purpose, so this is not a loss: it is extra tape that the clip now
        # claims, and it may contain the opening of something else. Listing it under excluded gameplay
        # would be the opposite of what happened.
        extra.append({"from": recorded_end, "to": end,
                      "what": "footage past the recorded set end that this clip now includes"})
        warnings.append("clip runs %.3f s past the recorded set end; confirm nothing else's footage is "
                        "being claimed" % (end - recorded_end))
    next_start = _num((following or {}).get("start"))
    crosses = next_start is not None and end > next_start + 1e-9
    if crosses:
        warnings.append("clip end %.3f passes the next candidate's start %.3f" % (end, next_start))
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
DERIVED_REVIEW_KEYS = ("grouping", "ends", "orientation", "characters", "membership", "bracket")

#: How far a re-import may move a candidate and still be the same set rather than a second row.
IDENTITY_TOLERANCE_SECONDS = EDGE_TOLERANCE_SECONDS

#: The value a Tekken round clock is drawn at when a round opens. Not an offset: the clock only ever falls
#: inside a round, so a reading below this at a candidate instant is proof play was already running.
ROUND_CLOCK_OPENING = 60


class InvalidAnswer(ValueError):
    """An answer a question never offered, or a boundary value the footage cannot hold."""


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
                    decision["moved_seconds"] = round(value - base, 3)
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
    """
    number = _num(value)
    if number is None:
        raise InvalidAnswer("a boundary has to be a number of seconds, not %r" % (value,))
    if start is not None and number <= start + 1e-9:
        raise InvalidAnswer("boundary %.3f is not after the match start %.3f" % (number, start))
    if following_start is not None and number > following_start + 1e-9:
        raise InvalidAnswer("boundary %.3f passes the next candidate's opening at %.3f, which would put "
                            "another match's footage in this clip" % (number, following_start))
    note = None
    if duration is not None and number > duration + 1e-9:
        note = ("clamped to the recording length %.3f: the value asked for (%.3f) is past the end of the "
                "tape" % (duration, number))
        number = duration
    return round(number, 3), note


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


#: Screen states that only appear between games: somebody went to the machine, picked a stage or a
#: character, and the VS screen ran. A run of these behind a candidate is a *new game* starting, which is
#: why they recover an opening the clock alone cannot see (the clock is simply not drawn there).
PRE_ROUND_STATES = ("stage_select", "character_select", "controller_prompt", "controller_assignment",
                    "vs_screen", "stage_intro", "round_call", "loading", "menu")
#: States that mean a fight was on the screen: play behind the candidate belongs to something earlier.
FIGHT_STATES = ("gameplay", "ko", "knockout", "match_end", "replay", "result")
#: A row whose state nobody recognised. It contradicts nothing and proves nothing, so it may sit inside a
#: run but never carry one.
UNKNOWN_STATE = "unknown"


def _state_pairs(rows: Sequence[Any]) -> List[Tuple[float, str]]:
    """(timestamp, screen state) for every saved row, in tape order, unreadable states kept as unknown.

    Rows arrive with the state under several keys (``screen_state`` from the tracker, ``state`` from the
    classifier), and a missing state is a fact about the record, not about the tape: it is kept as unknown
    so a walk can say "these frames prove nothing" instead of stepping over them as if they were play.
    """
    pairs: List[Tuple[float, str]] = []
    for row in rows or []:
        at = _num(row.get("timestamp", row.get("at")))
        if at is None:
            continue
        state = str(row.get("screen_state") or row.get("state") or UNKNOWN_STATE).strip().lower()
        pairs.append((float(at), state or UNKNOWN_STATE))
    return sorted(pairs)


def _clock_at(clocks: Dict[float, int], at: float, slop: float) -> Optional[int]:
    for key, value in clocks.items():
        if abs(key - at) <= slop:
            return value
    return None


def recover_opening(rows: Sequence[Any], at: float, *, clock_start: int = ROUND_CLOCK_OPENING,
                    scan_seconds: float = OPENING_SCAN_SECONDS,
                    slop: float = OPENING_CLOCK_SLOP, cues: Sequence[Any] = ()) -> Dict[str, Any]:
    """Where the saved sequence says this candidate's game opened, and what it cannot say at all.

    :func:`start_opening` restates the pipeline's own edge note, which is mostly a warning. This is the
    recovery step, and it does not lean on the round clock: the clock is drawn only while a round is live,
    so behind a set opening the saved rows read ``controller_prompt`` -> ``character_select`` ->
    ``vs_screen`` -> ``stage_intro`` -> ``gameplay``. That run is a new game starting -- there is no other
    way onto a stage select -- so the earliest instant the sequence supports is the first row of the run,
    and the last fight row behind it bounds how far back the claim reaches.

    The clock is used only as a bound on the candidate itself: a reading below ``clock_start`` means play
    was already running there, because inside a round the clock only ever falls. Nothing subtracts it from a
    timestamp to invent an opening, and nothing here rewrites a boundary -- a resolution is a place for the
    reviewer to look.

    Four outcomes, kept distinct because candidate-local review and full-recording discovery are different
    jobs: ``resolved`` names an instant and cites the rows; ``contradicted`` says play continues across the
    candidate with no select screen between, so it is not an opening; ``consistent`` says the candidate is
    already at the front of its sequence; ``unresolved`` says the rows run out, and
    ``missing_evidence_window`` names the seconds that have none.

    ``rows`` and ``cues`` are saved sequence evidence, never an answer key: a boundary found from last
    year's corrected timestamps is not a boundary that was found.
    """
    wanted = float(at)
    states = _state_pairs(rows)
    clocks = _clock_pairs(rows)
    clock_by_time = {point: value for point, value in clocks}
    window = [pair for pair in states if wanted - scan_seconds - 1e-9 <= pair[0] <= wanted + slop]
    at_candidate = max((pair for pair in window if pair[0] <= wanted + slop),
                       key=lambda pair: pair[0], default=None)
    result: Dict[str, Any] = {"at": wanted, "status": "unresolved", "opening_at": None,
                              "opening_uncertainty_seconds": None, "basis": "",
                              "candidate_state": at_candidate[1] if at_candidate else None,
                              "clock_at_candidate": _clock_at(clock_by_time, wanted, slop),
                              "rows_in_window": len(window),
                              "run": [(round(point, 6), state) for point, state in window][-14:],
                              "pre_run_states": [], "last_fight_before_run": None,
                              "unknown_rows_in_run": 0, "scan_seconds": scan_seconds,
                              "what_would_settle_it": "", "inference_sent": False}
    if not window:
        result["missing_evidence_window"] = [round(wanted - scan_seconds, 6), round(wanted, 6)]
        result["what_would_settle_it"] = ("no saved row at all lies within %.1f s behind this candidate, "
                                          "so the sequence cannot say where the game opened" % scan_seconds)
        return result

    behind = [pair for pair in window if pair[0] < wanted - 1e-9]
    run: List[Tuple[float, str]] = []
    unknown = 0
    if at_candidate is not None and at_candidate[1] in FIGHT_STATES:
        for at_row, state in reversed(behind):
            if state in PRE_ROUND_STATES:
                run.append((at_row, state))
            elif state == UNKNOWN_STATE and run:
                run.append((at_row, state))
                unknown += 1
            elif state in FIGHT_STATES:
                if run:
                    result["last_fight_before_run"] = round(at_row, 6)
                break
            elif run:
                break
    result["unknown_rows_in_run"] = unknown
    result["pre_run_states"] = sorted({state for _t, state in run if state != UNKNOWN_STATE})

    if run:
        earliest = min(at_row for at_row, _state in run)
        newest = max(at_row for at_row, _state in run)
        result["status"] = "resolved"
        result["opening_at"] = round(earliest, 6)
        result["basis"] = ("the saved sequence runs %s into this candidate, and the only way onto a stage "
                           "select or a VS screen is a new game starting. The candidate sits %.3f s after "
                           "the first row of that run."
                           % (" -> ".join(reversed(result["pre_run_states"])), newest - earliest))
        if result["last_fight_before_run"] is not None:
            result["opening_uncertainty_seconds"] = round(earliest - result["last_fight_before_run"], 6)
            result["basis"] += (" The last row showing a fight is %.3f, so the opening lies between it and "
                                "%.3f rather than exactly on either."
                                % (result["last_fight_before_run"], earliest))
        else:
            result["opening_uncertainty_seconds"] = round(earliest - (wanted - scan_seconds), 6)
            result["what_would_settle_it"] = ("no fight row sits behind the run inside the window, so the "
                                              "run's first frame is the edge of the evidence, not a bound")
        clock = result["clock_at_candidate"]
        if clock is not None and clock < clock_start:
            result["candidate_is_seconds_into_a_running_round"] = round(float(clock_start - clock), 3)
            result["basis"] += (" The round clock already reads %d at the candidate, so this clip starts "
                                "%.1f s into a round that was running." % (clock, clock_start - clock))
        return result

    if at_candidate is not None and at_candidate[1] in PRE_ROUND_STATES:
        # The candidate is already standing on the select screen: the sequence supports this instant, so
        # the reviewer's "jump to the opening" goes where they already are rather than nowhere.
        result["status"] = "consistent"
        result["opening_at"] = round(wanted, 6)
        result["basis"] = ("the candidate row is itself on a pre-round screen, so the clip already opens as "
                          "early as this sequence can support")
        return result
    recent_fight = [pair for pair in behind[-3:] if pair[1] in FIGHT_STATES]
    if recent_fight:
        result["status"] = "contradicted"
        result["basis"] = ("the rows immediately behind this candidate already show play, with no select or "
                           "VS screen between them, so the candidate is inside a running game rather than "
                           "at its opening")
        return result
    if not behind:
        result["status"] = "unresolved"
        result["missing_evidence_window"] = [round(wanted - scan_seconds, 6), round(wanted, 6)]
        result["what_would_settle_it"] = ("the candidate is the oldest row in the window; settle it with "
                                          "the %.1f s of sequence before it" % scan_seconds)
        return result
    result["status"] = "unresolved"
    result["what_would_settle_it"] = ("the window holds %d rows of play and unclassified frames with no "
                                      "select/VS sequence in them, so the opening is neither found nor "
                                      "excluded" % len(window))
    return result


def start_opening(recovery: Dict[str, Any], *, clock_opening: int = ROUND_CLOCK_OPENING,
                  rows: Sequence[Any] = (), cues: Sequence[Any] = ()
                  ) -> Dict[str, Any]:
    """How far back the saved sequence evidence supports moving a candidate's opening, as a general rule.

    Inside a round the clock only ever falls. So a clock reading below ``clock_opening`` at the candidate
    instant proves play was already running, and the round opened at least that many seconds earlier; and
    a live-play row the backward walk had to pass says the same thing whatever the clock read. This never
    subtracts a constant from a timestamp to move a boundary -- it reports what the saved rows entail,
    keeps the cue the walk settled on as the earliest *named* frame, and says plainly when the evidence
    stops before either, which is a reading to be bought, not a guess to be made.
    """
    edge = ((recovery or {}).get("edges") or {}).get("start") or {}
    at = _num(edge.get("at"))
    clock = edge.get("clock_at_boundary")
    clock = int(clock) if isinstance(clock, (int, float)) and not isinstance(clock, bool) else None
    scanned = edge.get("scanned") or []
    reaches = _num(scanned[0]) if len(scanned) == 2 else None
    walked = [row for row in (edge.get("walked_past") or []) if isinstance(row, dict)]
    live_walked = [row for row in walked if row.get("state") == "gameplay"]
    cue_at = _num((edge.get("settled_by") or {}).get("at")) if isinstance(edge.get("settled_by"), dict) else None
    if cue_at is None:
        cue_at = _num(edge.get("station_cue_at"))
    late_by = None if clock is None else max(0.0, float(clock_opening - clock))
    reasons: List[str] = []
    if late_by and at is not None:
        reasons.append("the round clock already reads %d at the candidate instant %.3f. It only ever falls "
                       "inside a round, so this round opened at least %.1f s earlier and the clip would "
                       "start on play already in progress" % (clock, at, late_by))
    if live_walked:
        reasons.append("the backward walk found a gameplay row at %s ahead of the candidate and could not "
                       "settle the bound there (%s); that is on-screen play before this start, though a "
                       "round-intro animation can also draw as gameplay, so it bounds the opening without "
                       "naming it"
                       % (", ".join("%.3f" % _num(row.get("at")) for row in live_walked),
                          live_walked[0].get("because") or "clock unread"))
    earliest = None
    supported_by: List[str] = []
    if cue_at is not None and (at is None or cue_at <= at + 1e-9):
        settled = edge.get("settled_by") if isinstance(edge.get("settled_by"), dict) else {}
        earliest = cue_at
        supported_by = ["station cue %s at %.3f, the nearest frame the saved rows name where the players "
                        "stopped" % (settled.get("state") or "unnamed", cue_at)]
    if late_by and at is not None:
        supported_by.append("clock-bound opening no later than %.3f" % (at - late_by))
    needs_reading = bool(reasons) and earliest is None and late_by is None
    recovered = recover_opening(rows, at, clock_start=clock_opening) if (rows and at is not None) else None
    if recovered is not None:
        # The sequence outranks the edge note when it has something to say, because it is the raw
        # observation and the edge block is somebody's summary of it. A recovered opening is a proposal the
        # reviewer acts on; it is never written into the boundary here.
        supported_by = list(supported_by) + ([
            "saved clock series: %s (opening %s, %d row(s) behind the candidate)"
            % (recovered["basis"] or recovered.get("why", ""),
               "%.3f" % recovered["opening_at"] if recovered["opening_at"] is not None else "unnamed",
               recovered["rows_in_window"])]) if recovered["status"] in ("resolved", "contradicted",
                                                                     "consistent") else []
        if recovered["status"] == "resolved" and recovered["opening_at"] is not None:
            earliest = min(value for value in (earliest, recovered["opening_at"])
                           if value is not None)
            needs_reading = False
        if recovered["status"] == "contradicted":
            reasons.append(recovered["why"])
    return {"at": at, "clock_at_boundary": clock, "clock_opening": clock_opening,
            "late_by_at_least_seconds": late_by,
            "earliest_supported_opening": earliest,
            "clock_bound_opening": None if (at is None or not late_by) else at - late_by,
            "supported_by": supported_by,
            "contradicts": reasons,
            "evidence_reaches_to": reaches,
            "settled_by": edge.get("settled_by"),
            "walked_past": walked, "recovered_opening": recovered,
            "flagged": bool(reasons),
            "needs_reading": needs_reading,
            "reading_note": ("saved rows stop at %s, which is not far enough back to name an opening"
                             % ("%.3f" % reaches if reaches is not None else "the start of the window"))
            if needs_reading else ""}


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
    queue: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments):
        recovery = segment.get("recovery") or {}
        start, end = _num(segment.get("start")), _num(segment.get("end"))
        clip = resolve_clip_end(segment, following=segments[index + 1]
                                if index + 1 < len(segments) else None,
                                duration=_num(project.get("duration")))
        opening = start_opening(recovery, rows=project.get("sequence_rows") or ())
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
                                 if kind == "new_match" else None},
                    "why_flagged": why,
                    "context": _context(boundary_at, context_seconds, project, clip),
                    "adjust": {"steps_seconds": list(ADJUST_STEPS_SECONDS),
                               "current": boundary_at, "to_current_time": True,
                               # What the saved sequence supports, so the reviewer can jump there and look
                               # before deciding. A proposal with an instant attached, never an edit.
                               "jump_to": opening.get("recovered_opening", {}).get("opening_at")
                               if opening.get("recovered_opening") else None},
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


def _questions_for(recovery: Dict[str, Any], clip: Dict[str, Any], flags: Sequence[str],
                   opening: Dict[str, Any]) -> List[Tuple[str, str]]:
    """What to ask at one segment: its opening and its ending always, plus whatever is still contested.

    Both boundary questions are asked even when nothing is flagged, because a flag list cannot prove the
    absence of a wrong boundary -- five of ten BB9 starts were late and none of them were flagged by
    anything. What changes with confidence is the *why*, never whether the reviewer is shown the frame.
    """
    start_reasons = list(opening["contradicts"])
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
        "start_clock_at_boundary": opening["clock_at_boundary"],
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
        opening = start_opening(segment.get("recovery") or {},
                                rows=project.get("sequence_rows") or ())
        approved = bool(((segment.get("review") or {}).get("openings") or {}).get("approved_partial"))
        clip["opening"] = {"flagged": opening["flagged"],
                           "late_by_at_least_seconds": opening["late_by_at_least_seconds"],
                           "earliest_supported_opening": opening["earliest_supported_opening"],
                           "why": opening["contradicts"], "approved_partial": approved,
                           "recovered_opening_at": (opening.get("recovered_opening") or {}).get(
                               "opening_at"),
                           "recovered_opening_status": (opening.get("recovered_opening") or {}).get(
                               "status"),
                           "needs_reading": opening["needs_reading"]}
        clip["opening_review_required"] = bool(opening["flagged"] and not approved)
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
        # Two different questions, and conflating them is how "exportable" came to read as "correct". An
        # approved partial says a person agreed to ship the loss; it does not say the opening is right, so
        # acceptance stays false while the evidence is known to contradict the boundary.
        clip["boundary_accepted"] = not (clip["held"] or opening["flagged"])
    coverage = coverage_accounting(project, clips)
    unplaced = coverage["uncovered_by_exportable_jobs"]
    held_clips = [clip for clip in clips if clip["held"]]
    return {"clips": clips, "held": held_clips, "exportable": [c for c in clips if not c["held"]],
            "unplaced_play": unplaced, "coverage": coverage,
            "opening_review": [clip for clip in clips if clip["opening_review_required"]],
            "counts": {"segments": len(clips), "exportable": len(clips) - len(held_clips),
                       "held": len(held_clips), "unplaced_intervals": len(unplaced),
                       "unplaced_seconds": round(sum(row["seconds"] for row in unplaced), 3),
                       "openings_needing_review": len([c for c in clips
                                                       if c["opening_review_required"]]),
                       "boundary_accepted": len([c for c in clips if c["boundary_accepted"]]),
                       "covered_seconds_by_candidates": coverage["candidates"]["seconds"],
                       "covered_seconds_by_jobs": coverage["exportable_jobs"]["seconds"],
                       "uncovered_seconds_by_jobs": round(sum(row["seconds"] for row in unplaced), 3)},
            "exportable_is_not_accepted": bool([c for c in clips if c["opening_review_required"]]),
            "inference_sent": False, "media_rendered": False}


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


def merge_into_project(project: Dict[str, Any], segments: Sequence[Dict[str, Any]], *,
                       event: str = "", duration: Optional[float] = None) -> Dict[str, Any]:
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
