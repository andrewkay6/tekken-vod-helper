"""The review panel's behaviour, with no widgets and no model calls in it.

``app.py`` cannot be imported on a box without tkinter, and this repo's whole verification story rests on
that being survivable. So everything a reviewer does -- answer a question, defer it, nudge a boundary,
fold a candidate into its neighbour, pick a bracket match, undo, save, ask what an export would write --
lives here against real :class:`~tekken_vod_helper.models.ProjectState` objects, and is exercised by
tests. What is left in ``app.py`` is a tree, some buttons and a seek call: presentation.

Three rules hold the layer together.

* **The journal is the truth and the segment is its projection.** Every write goes through
  :class:`~tekken_vod_helper.boundary_review.ReviewStore` and ends in a recompute, so undo and
  invalidation cannot leave a consequence behind.
* **A view is not a copy.** ``ProjectState`` keeps ``recovery`` and ``review`` as plain dicts, and the
  flow hands those same objects to the store. Only the flat fields need flushing, and :meth:`ReviewFlow.flush`
  is the one place that does it, at the end of every mutating method.
* **Nothing here reaches for the source.** Playing a boundary seeks in whatever the app already has open
  and reads saved evidence. An answer must never cost an inference, so no path in this module can spend one.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import boundary_review as review
from .boundary_review import _num as number
from .models import MatchSegment, ProjectState

#: The panel's own shape, so a saved project can say which panel wrote it.
PANEL_VERSION = "bb9-review-panel-v1"
DEFAULT_CONTEXT_SECONDS = 12.0

FLAT_FIELDS = ("player1", "player2", "character1", "character2", "round_name", "notes")


def _candidate_label(adjust):
    # type: (Dict[str, Any]) -> str
    """The text on the button that offers an earlier instant to look at.

    It names the level, because jumping to a round's opening is not jumping to a set start. It also names the
    width when the sampling only bounds the instant, because an interval shown as a point is how a proposal
    gets read as a measurement -- and the label is the one place a reviewer meets that number before deciding
    whether to move the playhead at all. Rounding is presentation only: ``jump_to`` keeps full precision, so
    no disagreement between two frames can be created by the sentence describing them.
    """
    level = adjust.get("jump_to_level") or "none"
    width = adjust.get("jump_to_uncertainty_seconds")
    if level == "round_opening" and isinstance(width, (int, float)) and not isinstance(width, bool):
        return "Look just before the candidate (%s, ±%s s)" % (level, review.span_text(width))
    return "Look just before the candidate (%s)" % level


class ReviewFlow:
    """Every decision a reviewer can make about a project, callable without a display."""

    def __init__(self, state: ProjectState, *, context_seconds: float = DEFAULT_CONTEXT_SECONDS,
                 include_confident: bool = True) -> None:
        self.state = state
        self.context_seconds = float(context_seconds)
        self.include_confident = bool(include_confident)
        if not isinstance(self.state.review, dict) or not self.state.review:
            self.state.review = {"version": review.REVIEW_VERSION, "decisions": [], "log": []}
        self.store().recompute_all()

    # ------------------------------------------------------------------------------------ the view

    def rows(self) -> List[Tuple[MatchSegment, Dict[str, Any]]]:
        """One row per segment: the object and a dict view that shares its ``recovery``/``review``.

        The nested blocks go over by reference on purpose -- the store's recompute writes into them and the
        segments see it without a second copy drifting. The flat fields are copies, so a mutation of a view
        only reaches the segment through :meth:`flush`.
        """
        pairs = []
        for match in self.state.matches:
            view = {"start": match.start, "end": match.end, "player1": match.player1,
                    "player2": match.player2, "character1": match.character1,
                    "character2": match.character2, "round_name": match.round_name,
                    "notes": match.notes, "recovery": match.recovery, "review": match.review}
            pairs.append((match, view))
        return pairs

    def project(self) -> Dict[str, Any]:
        return {"duration": self.state.duration, "video_path": self.state.video_path,
                "event_name": self.state.event_name, "event": self.state.event_name,
                "matches": [view for _match, view in self.rows()],
                "review": self.state.review,
                # The same evidence the raw helper is given, or the queue a reviewer sees and the queue a
                # test exercised are two different things -- which is exactly how the sequence path shipped
                # working in one and empty in the app.
                "sequence_source": self.state.sequence_source,
                "sequence_rows": self.state.sequence_rows}

    def sequence_provenance(self) -> Dict[str, Any]:
        """What the saved sequence can support right now, in the reviewer's terms.

        Whether openings were considered at all is a fact about this project's evidence, and a reviewer
        told "no opening problems found" while no sequence is on disk would be misled twice: once by the
        absence, once by the wording.
        """
        _rows, provenance = review.project_sequence(self.project())
        return provenance

    def store(self) -> review.ReviewStore:
        return review.ReviewStore(self.project())

    def flush(self, pairs: Sequence[Tuple[MatchSegment, Dict[str, Any]]]) -> None:
        self.state.matches = [self._bind(match, view) for match, view in pairs]

    @staticmethod
    def _bind(match: MatchSegment, view: Dict[str, Any]) -> MatchSegment:
        match.start = view["start"]
        match.end = view["end"]
        for field in FLAT_FIELDS:
            setattr(match, field, view.get(field, "") or "")
        match.recovery = view.get("recovery") or {}
        match.review = view.get("review") or {}
        return match

    def reflow(self) -> None:
        """Rebuild every projection: the journal first, then the flat fields it implies, then the order."""
        self.state.matches = sorted(self.state.matches, key=lambda match: match.start)
        store = self.store()
        pairs = self.rows()
        store.recompute_all()
        self.rebind(pairs)
        self.flush(pairs)

    # ----------------------------------------------------------------------------------- questions

    def queue(self, view: str = "open") -> List[Dict[str, Any]]:
        return review.review_queue(self.project(), context_seconds=self.context_seconds,
                                   include_confident=self.include_confident, view=view)

    def item(self, fingerprint: str, view: str = "all") -> Optional[Dict[str, Any]]:
        return next((candidate for candidate in self.queue(view)
                     if candidate["fingerprint"] == fingerprint), None)

    def start_fingerprint(self, segment_start: float) -> str:
        """The opening question for a row, so an edit made outside the panel becomes a real decision."""
        for candidate in self.queue("all"):
            if candidate["kind"] == "new_match" \
                    and number(candidate["segment"]["start"]) == number(segment_start):
                return candidate["fingerprint"]
        raise review.InvalidAnswer("no opening question for %s" % (segment_start,))

    def answer(self, fingerprint: str, value: str, *, note: str = "",
               end_at: Optional[float] = None,
               delta_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Answer one question by fingerprint and write its consequence onto the project.

        Grouping and split answers are dispatched to the structural path, because a grouping answer that
        only annotates metadata is a note rather than a decision: it has to change which rows exist, and
        therefore which clips an export would write.
        """
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no question %s in this project" % fingerprint)
        if item["kind"] == "new_match" and value in ("new_match", "same_match_next_game"):
            return self.regroup(fingerprint, value)
        if item["kind"] == "membership" and value == "no_split":
            return self.split_at(fingerprint, end_at)
        store = self.store()
        decision = store.decide(item, value, note=note, end_at=end_at, delta_seconds=delta_seconds)
        pairs = self.rows()
        store.recompute_all()
        self.flush(pairs)
        return decision

    def undo(self, fingerprint: str) -> bool:
        store = self.store()
        if (store.state.get("snapshots") or {}).get(fingerprint):
            return self._restore(fingerprint)
        changed = store.undo(fingerprint)
        if changed:
            pairs = self.rows()
            store.recompute_all()
            self.flush(pairs)
        return changed

    def undo_last(self) -> Optional[str]:
        """The most recent answer that can still be withdrawn.

        A plain undo deletes the decision record but leaves its log entry behind, and a structural undo
        truncates the log to where it stood before the change. Scanning the log alone therefore names an
        answer that is already gone: the panel calls ``undo`` on it, gets False, and reports "nothing to
        undo" while every earlier decision sits unreachable behind it. Only an entry with a snapshot to
        restore or a decision still in force is offered.
        """
        state = self.store().state
        snapshots = state.get("snapshots") or {}
        decided = {entry.get("fingerprint") for entry in (state.get("decisions") or [])}
        for entry in reversed(list(self.state.review.get("log") or [])):
            # A claim is the most structural thing on the list: it put a row where there was none. Leaving it
            # out would make "Undo last" step back over the end answer it recorded and leave the row behind,
            # which is not the thing the reviewer just did.
            if entry.get("action") not in ("decide", "regroup", "split", "claim"):
                continue
            fingerprint = entry.get("fingerprint")
            if fingerprint and (fingerprint in snapshots or fingerprint in decided):
                return fingerprint
        return None

    # ---------------------------------------------------------------------------- boundary editing

    def opening_candidate(self, fingerprint: str) -> Dict[str, Any]:
        """The earlier instant the saved sequence supports for a start question, with its level.

        Looking is not moving. This says where to put the playhead so a person can judge the proposal
        against the boundary the pipeline predicted; the boundary itself changes only when they answer, so a
        recovered candidate can never become a runtime hint by way of being displayed.
        """
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no question %s in this project" % fingerprint)
        adjust = item.get("adjust") or {}
        at = adjust.get("jump_to")
        return {"at": at, "level": adjust.get("jump_to_level"),
                # The width travels with the instant. A recovered opening is an interval, and an API that
                # returns the midpoint alone hands a frontend a precision the sampling never supported -- the
                # state-word error one layer up, and the second opinion's job is to catch exactly that.
                "uncertainty_seconds": adjust.get("jump_to_uncertainty_seconds"),
                "first_active_at": adjust.get("first_active_gameplay_at"),
                "first_active_uncertainty_seconds": adjust.get("first_active_uncertainty_seconds"),
                "first_active_status": adjust.get("first_active_status"),
                "current": (item.get("boundary") or {}).get("at"), "playable": at is not None,
                "sequence": self.sequence_provenance(), "inference_sent": False}

    # ---------------------------------------------------------------------------------- proposals

    def propose_opening(self, segment_start: float, *, at: Optional[float] = None,
                        why: Optional[str] = None, evidence: Optional[Dict[str, Any]] = None,
                        uncertainty_seconds: Optional[float] = None,
                        generated_by: str = "opening_sequence") -> Dict[str, Any]:
        """Ask the reviewer to move an opening, without moving it.

        Given no instant, the suggestion is the one the saved sequence already supports -- the very
        ``jump_to``, level and width :meth:`opening_candidate` publishes, so no second idea about where a set
        starts is invented here. Given one, the caller states its own evidence, which is how a proposal comes
        to rest on character and stage continuity rather than on a cue the walk recognises.

        Either way the row does not move, the owner's question stays open, and nothing about the export
        changes. A machine may put a number in front of a person; it may not become the person.
        """
        fingerprint = self.start_fingerprint(segment_start)
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no opening question for %.6f" % segment_start)
        current = float((item.get("boundary") or {}).get("at") or segment_start)
        candidate = self.opening_candidate(fingerprint)
        if at is None:
            at = candidate.get("at")
            if uncertainty_seconds is None:
                uncertainty_seconds = candidate.get("uncertainty_seconds")
            if why is None:
                why = ("the saved sequence supports this opening at level %s"
                       % (candidate.get("level") or "unknown"))
            if at is None:
                raise review.InvalidAnswer(
                    "the sequence offers no earlier instant for %.6f, so a proposal here would have to "
                    "invent one" % segment_start)
        if float(at) >= current - 1e-9:
            raise review.InvalidAnswer(
                "an opening proposal has to be earlier than the boundary it questions (%.6f is not before "
                "%.6f); the row is already at the instant the evidence supports" % (float(at), current))
        record = review.proposal_record(
            "opening", float(at), why=why or "", evidence=evidence or {},
            uncertainty_seconds=uncertainty_seconds, generated_by=generated_by)
        record["current_start"] = current
        record["seconds_saved"] = round(current - float(at), 6)
        record["play_cut_at_current"] = (candidate.get("first_active_at") is not None
                                         and float(candidate["first_active_at"]) <= current + 0.001)
        return self.store().propose({"start": segment_start}, record)

    def propose_end(self, segment_start: float, at: float, *, why: str,
                    evidence: Optional[Dict[str, Any]] = None,
                    uncertainty_seconds: Optional[float] = None, purpose: str = "presentation",
                    generated_by: str = "opening_sequence") -> Dict[str, Any]:
        """Ask for a different clip end, and say plainly whether the ask is evidence or presentation.

        ``purpose`` is not decoration. Proposing an extension because a read *confirms the winner* and
        proposing one because the victory camera is nicer to watch are different claims, and the second does
        not become the first by being recorded in the same field.
        """
        record = review.proposal_record("end", float(at), why=why, evidence=evidence or {},
                                        uncertainty_seconds=uncertainty_seconds, purpose=purpose,
                                        generated_by=generated_by)
        row = next((view for _match, view in self.rows()
                    if number(view.get("start")) == number(segment_start)), None)
        if row is None:
            raise review.InvalidAnswer("no row starting at %.6f" % segment_start)
        clip = review.resolve_clip_end(dict(row), following=None,
                                       duration=self.state.duration or None)
        record["current_end"] = clip.get("end")
        record["current_end_basis"] = clip.get("basis")
        return self.store().propose({"start": segment_start}, record)

    def proposals(self, segment_start: Optional[float] = None) -> List[Dict[str, Any]]:
        return self.store().proposals(segment_start)

    def apply_proposal(self, segment_start: float, kind: str, *,
                       proposal_id: Optional[str] = None, note: str = "") -> Dict[str, Any]:
        """The operator's own act of taking a suggestion: it becomes their decision, and stops being pending.

        The boundary moves through the same controller a person driving the tab would use, so the decision
        carries their authorship and none of the proposal's -- and undo withdraws the boundary, not a label.
        """
        store = self.store()
        record = store.find_proposal(segment_start, kind, proposal_id=proposal_id)
        origin = float(record["at"])
        if kind == "opening":
            decision = self.move_start(self.start_fingerprint(segment_start), origin)
        else:
            decision = self.set_human_end(segment_start, origin)
        # Stamped by identity: an applied opening proposal no longer lives on a row that starts where the
        # caller named, because applying it is precisely what moved the row.
        store.set_proposal_status(None, kind, review.PROPOSAL_APPLIED,
                                  note=note or ("applied the machine proposal at %.3f s" % origin),
                                  record=record)
        return decision

    def reject_proposal(self, segment_start: float, kind: str, *,
                        proposal_id: Optional[str] = None, note: str = "") -> Dict[str, Any]:
        """Refuse a suggestion. The boundary is left exactly where it was, and the refusal is the record.

        The refused record stays on the row -- history the next pass has to see -- and a fresh proposal of the
        same kind may be filed beside it, which is why resolution goes by id or by "the live one" and never by
        "the first of this kind".
        """
        return self.store().set_proposal_status(segment_start, kind, review.PROPOSAL_REFUSED,
                                                note=note or "refused by the reviewer",
                                                proposal_id=proposal_id)

    def nudge_boundary(self, fingerprint: str, seconds: float) -> Dict[str, Any]:
        """Move the boundary a question is about by a signed number of seconds.

        A start moves the row; an end is answered with the question's own extend/shorten value. Either way
        it lands in the journal, so undo has something to withdraw and the export plan has a basis to show.
        """
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no question %s in this project" % fingerprint)
        if item["kind"] == "clip_end":
            return self.answer(fingerprint, "extend" if seconds >= 0 else "shorten",
                               delta_seconds=seconds)
        if item["kind"] != "new_match":
            raise review.InvalidAnswer("%s is not a boundary question" % item["kind"])
        return self.move_start(fingerprint, (item["boundary"]["at"] or 0.0) + float(seconds))

    def set_boundary_to(self, fingerprint: str, value: float) -> Dict[str, Any]:
        """Put a question's boundary at an exact instant, choosing the honest answer for the direction."""
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no question %s in this project" % fingerprint)
        if item["kind"] == "clip_end":
            current = item["boundary"]["at"]
            answer = "extend" if (current is None or value >= current) else "shorten"
            return self.answer(fingerprint, answer, end_at=value)
        if item["kind"] == "new_match":
            return self.move_start(fingerprint, value)
        raise review.InvalidAnswer("%s has no boundary to set" % item["kind"])

    def move_start(self, fingerprint: str, value: float) -> Dict[str, Any]:
        """Put a candidate's opening elsewhere, keeping the row's identity and withdrawing stale answers.

        Anything already answered about the old opening stops governing: it was about a different frame.
        That is what keeps a nudge from carrying an approval across a boundary it never covered.
        """
        item = self.item(fingerprint, view="all")
        if item is None or item["kind"] != "new_match":
            raise review.InvalidAnswer("only a start question can move a match opening")
        pairs = self.rows()
        index = int(item["index"])
        _match, view = pairs[index]
        following = pairs[index + 1][1] if index + 1 < len(pairs) else None
        start, clamp_note = review.validate_boundary_value(
            start=None, value=value, duration=self.state.duration or None,
            following_start=number((following or {}).get("start")))
        self._refuse_inside_previous(view, start)
        previous_start = number(view.get("start"))
        reason = "the opening moved from %.3f to %.3f by hand" % (previous_start, start)
        snapshot = self._snapshot()          # before anything moves, so undo is exact
        store = self.store()
        store.invalidate(previous_start, reason)
        for decision in store.state["decisions"]:
            if number(decision.get("segment_start")) == previous_start:
                decision["segment_start"] = start
                decision["identity"] = {"start_was": previous_start, "now": start,
                                        "matched_by": "human_move"}
        view["start"] = start
        self.flush(pairs)
        self.reflow()
        question = next((candidate for candidate in self.queue("all")
                         if candidate["kind"] == "new_match"
                         and number(candidate["segment"]["start"]) == start), None)
        if question is None:
            raise review.InvalidAnswer("no start question for %.3f after the move" % start)
        decision = self.store().decide(question, "other_adjust", note=clamp_note or reason)
        decision["start_at"] = start
        decision["moved_from"] = previous_start
        self._store_snapshot(fingerprint, snapshot, "move",
                             aliases=[question["fingerprint"]])
        return decision

    def _refuse_inside_previous(self, view: Dict[str, Any], start: float) -> None:
        """An opening that lands inside the clip before it is a merge, and the rows have to say so."""
        earlier = [row for _match, row in self.rows()
                   if row is not view and (number(row.get("start")) or 0.0) < start]
        if not earlier:
            return
        last = max(earlier, key=lambda row: number(row.get("start")) or 0.0)
        clip = review.resolve_clip_end(last, following=view, duration=self.state.duration or None)
        if clip["end"] is not None and start < clip["end"] - 1e-9:
            raise review.InvalidAnswer(
                "the opening %.3f falls inside the clip before it, which runs to %.3f; folding the two "
                "rows together is the answer that changes the export" % (start, clip["end"]))

    # ------------------------------------------------------------------------------------ structural

    def regroup(self, fingerprint: str, value: str) -> Dict[str, Any]:
        """Answer "does a new match start here?" by changing the rows, not by annotating them.

        ``same_match_next_game`` folds this candidate into the clip before it: one export instead of two,
        both rows' games under one set, and a snapshot an undo can come back from. ``new_match`` keeps the
        split and is recorded so the queue stops asking.
        """
        item = self.item(fingerprint, view="all")
        if item is None or item["kind"] != "new_match":
            raise review.InvalidAnswer("regrouping needs a start question")
        if value == "new_match":
            return self._answer_plain(item, "new_match")
        if value != "same_match_next_game":
            raise review.InvalidAnswer("a start question takes new_match, same_match_next_game, "
                                       "other_adjust or unsure")
        index = int(item["index"])
        if index == 0:
            raise review.InvalidAnswer("the first candidate has no earlier match to fold into")
        snapshot = self._snapshot()
        # Recorded while both rows still exist, so the answer is attached to the boundary it was about.
        decision = self._answer_plain(item, "same_match_next_game")
        pairs = self.rows()
        _previous_match, previous = pairs[index - 1]
        _current_match, current = pairs[index]
        recovery = previous.setdefault("recovery", {})
        moved_games = list((current.get("recovery") or {}).get("games") or [])
        recovery["games"] = list(recovery.get("games") or []) + moved_games
        recovery["published_game_count"] = len(recovery["games"])
        # The folded row has to reach the end of the later footage, and its padded clip end moves with it;
        # taking the max of each keeps a hand-marked blank from pretending it had an end.
        current_end = number(current.get("end"))
        if current_end is not None and (number(previous.get("end")) is None
                                        or current_end > previous["end"]):
            previous["end"] = current_end
        current_clip = number((current.get("recovery") or {}).get("clip_end_at"))
        if current_clip is not None and (number(recovery.get("clip_end_at")) is None
                                         or current_clip > recovery["clip_end_at"]):
            recovery["clip_end_at"] = current_clip
        previous.setdefault("review", {}).setdefault("merged_from", []).append(
            {"start": current.get("start"), "games": len(moved_games), "fingerprint": fingerprint})
        pairs.pop(index)
        self.flush(pairs)
        self.reflow()
        recorded = self.store().decision(fingerprint) or decision
        self._store_snapshot(fingerprint, snapshot, "merge")
        self.store().state["log"].append({"action": "regroup", "fingerprint": fingerprint,
                                          "note": "folded %.3f into the clip before it"
                                                  % (current.get("start") or 0.0)})
        return recorded

    def split_at(self, fingerprint: str, at: Optional[float]) -> Dict[str, Any]:
        """Cut a row in two at an instant a person chose, giving each half its own games and clip end."""
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no question %s in this project" % fingerprint)
        if item["kind"] not in ("membership", "new_match"):
            raise review.InvalidAnswer("a %s question cannot cut a row in two" % item["kind"])
        if at is None:
            raise review.InvalidAnswer("splitting needs the instant to cut at")
        pairs = self.rows()
        index = int(item["index"])
        _match, view = pairs[index]
        start, end = number(view.get("start")), number(view.get("end"))
        cut = review.validate_boundary_value(start=start, value=at,
                                             duration=self.state.duration or None,
                                             following_start=end)[0]
        snapshot = self._snapshot()
        head = [game for game in (view.get("recovery") or {}).get("games") or []
                if number(game.get("start")) is not None and number(game.get("start")) < cut]
        tail = [game for game in (view.get("recovery") or {}).get("games") or []
                if number(game.get("start")) is not None and number(game.get("start")) >= cut]
        if not tail:
            raise review.InvalidAnswer("nothing in this row opens at or after %.3f, so there is nothing "
                                       "to split off" % cut)
        second = copy.deepcopy(view)
        second["start"] = cut
        second["end"] = end
        second["recovery"] = copy.deepcopy(view.get("recovery") or {})
        second["recovery"]["games"] = tail
        second["recovery"]["published_game_count"] = len(tail)
        second["recovery"].pop("clip_end_at", None)
        second["review"] = {"decisions": [], "orientation": review.machine_sides(second),
                            "suggested_by": review.REVIEW_VERSION}
        second["notes"] = ""
        view["end"] = number(head[-1].get("end")) if head else start
        (view.setdefault("recovery") or {})["games"] = head
        (view.get("recovery") or {})["published_game_count"] = len(head)
        (view.get("recovery") or {}).pop("clip_end_at", None)
        view.setdefault("review", {}).setdefault("split_from", {"at": cut})
        pairs.insert(index + 1, (MatchSegment.from_dict(second), second))
        self.flush(pairs)
        self.reflow()
        store = self.store()
        decision = store.decide(item, "no_split", end_at=cut)
        self._store_snapshot(fingerprint, snapshot, "split")
        store.state["log"].append({"action": "split", "fingerprint": fingerprint,
                                   "note": "cut at %.3f" % cut})
        return decision

    def claim_footage(self, start: float, end: Optional[float] = None, *,
                      note: str = "") -> Dict[str, Any]:
        """Make footage that no candidate covered into a row a reviewer can answer questions about.

        Before this, an operator who played an unclaimed stretch and recognised a match in it had nowhere
        to say so: the row could be played, and it could not be claimed, so the recording exported without
        it and the coverage report went on calling the footage unplaced. A new row is the only thing that
        puts a stretch into the plan.

        What it arrives with is the honest minimum: no machine evidence, so both boundary questions are open
        and every claim about it starts at "not accepted by anyone". What it must not arrive with is a
        boundary inside another clip -- that is a split or a merge, and each has its own answer.

        Rejection is a no-op, in both senses. Every question a claim raises is asked and answered *before*
        the project is touched, and the mutation itself runs inside a capture-and-restore, because a row
        appended and then abandoned by a later validation is worse than no row: the count changed, the
        coverage changed, an export job appeared for footage nobody approved, and there is no undo entry to
        take it back, since undo records answers and this was never one.
        """
        placed, placed_end, notes = self._checked_claim(start, end)
        captured = self._capture()
        try:
            snapshot = self._snapshot()
            segment = MatchSegment(start=placed, end=placed_end,
                                   notes=note or "operator claimed footage no candidate covered",
                                   recovery={},
                                   # Where the row came from, in the row. It is not machine evidence and it
                                   # is not a decision about a boundary: it says a person watched this tape
                                   # and asked for it to count.
                                   review={"claim": {"note": note, "end_claimed": placed_end is not None,
                                                     "clamped": notes}})
            self.state.matches.append(segment)
            self.reflow()
            store = self.store()
            if placed_end is not None:
                self.set_human_end(placed, placed_end)
            store.recompute_all()
            question = next((candidate for candidate in self.queue("all")
                             if candidate["kind"] == "new_match"
                             and number(candidate["segment"]["start"]) == placed), None)
            # The log entry names the question it can be stepped back over, because that is what "Undo
            # last" reads.
            store.state.setdefault("log", []).append({"action": "claim", "start": placed,
                                                      "end": placed_end, "note": note,
                                                      "fingerprint": (question["fingerprint"]
                                                                      if question else None)})
            if question is not None:
                self._store_snapshot(question["fingerprint"], snapshot, "claim")
        except Exception:
            self._rollback(captured)
            raise
        return {"claimed_from": placed, "claimed_to": placed_end, "question": (
            question["fingerprint"] if question else None)}

    def _checked_claim(self, start, end):
        """Every reason a claim can be refused, found before anything is refused *after*.

        The order is the point. An opening is checked against the row that follows it and an end against the
        opening it must stop before, and both need the neighbours as they stand -- which is why neither can
        wait until the new row is in the list, where it becomes its own neighbour and a legitimate claim
        turns into a collision with itself.
        """
        duration = self.state.duration or None
        rows = self.rows()
        existing = sorted(value for value in (number(row.get("start")) for _match, row in rows)
                          if value is not None)
        # Validate the raw start before trusting it as a position: an infinite or missing opening has no
        # neighbours to check against, and comparing against NaN says "yes" to everything.
        wanted = number(start)
        nearest = next((value for value in existing
                        if wanted is not None and value > wanted + review.INSTANT_EPSILON), None)
        placed, clamp_note = review.validate_boundary_value(
            start=None, value=start, duration=duration, following_start=nearest)
        for _match, row in rows:
            claimed_from = number(row.get("start"))
            if claimed_from is None:
                continue
            if abs(placed - claimed_from) <= review.INSTANT_EPSILON:
                raise review.InvalidAnswer(
                    "a row already opens at %s. Two matches cannot start at one instant on one recording: "
                    "if this footage belongs to that row, answer that row's questions; if it holds another "
                    "match, cut the row above at the opening you saw and claim the rest"
                    % review.instant_text(claimed_from))
            if placed <= claimed_from + review.INSTANT_EPSILON:
                continue
            claimed_to = number(review.resolve_clip_end(row, duration=duration).get("end"))
            if claimed_to is not None and placed < claimed_to - review.INSTANT_EPSILON:
                raise review.InvalidAnswer(
                    "%s is already inside the clip that opens at %s and stops at %s. Footage two clips "
                    "claim is a split or a merge: cut the row above, or fold this one into it, rather than "
                    "starting a second match on top of the first"
                    % (review.instant_text(placed), review.instant_text(claimed_from),
                       review.instant_text(claimed_to)))
        if end is None:
            return placed, None, clamp_note
        # The end is asked the same question the exporter will ask it later: does this clip stop before
        # whatever opens next. Answering it here is what makes a refusal leave nothing behind.
        after = next((value for value in existing if value > placed + review.INSTANT_EPSILON), None)
        placed_end, end_note = review.validate_boundary_value(
            start=placed, value=end, duration=duration, following_start=after)
        note = " ".join(text for text in (clamp_note, end_note) if text) or None
        return placed, placed_end, note

    def _capture(self) -> Dict[str, Any]:
        """The whole mutable project, deep-copied, so a half-finished action can be un-done exactly."""
        return {"matches": copy.deepcopy([match.to_dict() for match in self.state.matches]),
                "review": copy.deepcopy(self.state.review)}

    def _rollback(self, captured: Dict[str, Any]) -> None:
        self.state.matches = [MatchSegment.from_dict(view) for view in captured["matches"]]
        self.state.review = captured["review"]
        self.reflow()

    def annotate_footage(self, kind: str, start: float, end: Optional[float] = None, *,
                         boundary: Optional[float] = None, view: str = "", note: str = "",
                         observer: str = "") -> Dict[str, Any]:
        """Write down what a person saw in a stretch of tape.

        It moves nothing, accepts nothing and validates nothing.

        The disposition path exists because footage the pipeline never proposed has nowhere else to go: an
        operator can watch a gap and know it is a match, and the only record available was a free-text note
        that no comparison could read. A record carries the exact instant somebody says a match opened, what
        was on screen there, and their words about it, so a later pass has something to line up against --
        and so "somebody watched this" is a fact on the page instead of something inferred from silence.
        """
        record = review.annotation_record(kind, start, end, boundary=boundary, view=view, note=note,
                                         observer=observer,
                                         project={"duration": self.state.duration or None})
        block = self.state.review if isinstance(self.state.review, dict) else {}
        existing = [item for item in (block.get("annotations") or []) if isinstance(item, dict)]
        record["sequence"] = len(existing) + 1
        block["annotations"] = existing + [record]
        self.state.review = block
        block.setdefault("log", []).append({"action": "annotate", "kind": kind, "from": record["from"],
                                           "to": record["to"], "boundary_at": record["boundary_at"]})
        return record

    def footage_coverage(self) -> Dict[str, Any]:
        """What has been said about the tape, and how much of it nobody has looked at yet."""
        return review.footage_coverage({"duration": self.state.duration or None},
                                       annotations=(self.state.review or {}).get("annotations"))

    def _snapshot(self) -> Dict[str, Any]:
        """Everything an undo has to put back: the rows, and the journal as it stood beforehand.

        Snapshots live in ``review["snapshots"]`` keyed by question, never inside the decision record. The
        obvious place would have been the decision itself, and it is wrong: a decision holding a copy of the
        journal would copy every earlier snapshot too, so a saved project would grow superlinearly with the
        number of things a reviewer fixed.
        """
        return {"matches": copy.deepcopy([view for _match, view in self.rows()]),
                "decisions": copy.deepcopy(list(self.state.review.get("decisions") or [])),
                "log_len": len(self.state.review.get("log") or [])}

    def _store_snapshot(self, fingerprint: str, snapshot: Dict[str, Any], kind: str,
                        aliases: Sequence[str] = ()) -> None:
        """Attach a taken-before-the-change snapshot to the question whose undo it serves.

        A move renames its own question -- the fingerprint carries the boundary -- so it is filed under both
        the question that was answered and the one the answer created. Undo is pressed on whichever the
        panel is still standing on, and a reviewer should never be told "nothing to undo" for the last
        thing they did.
        """
        store = self.store()
        stored = dict(snapshot, kind=kind, aliases=[fingerprint] + list(aliases))
        for key in stored["aliases"]:
            store.state.setdefault("snapshots", {})[key] = stored
            decision = store.decision(key)
            if decision is not None:
                decision["restorable"] = kind

    def _restore(self, fingerprint: str) -> bool:
        """Put the rows and the journal back exactly as they stood before the structural answer."""
        store = self.store()
        snapshot = (store.state.get("snapshots") or {}).get(fingerprint)
        if not snapshot:
            return False
        for key in snapshot.get("aliases") or [fingerprint]:
            (store.state.get("snapshots") or {}).pop(key, None)
        self.state.matches = [MatchSegment.from_dict(view) for view in snapshot["matches"]]
        self.state.review["decisions"] = copy.deepcopy(snapshot.get("decisions") or [])
        del self.state.review.setdefault("log", [])[snapshot.get("log_len", 0):]
        restored = self.store()
        restored.state["log"].append({"action": "undo", "fingerprint": fingerprint,
                                      "restored": snapshot.get("kind", "state")})
        self.reflow()
        return True

    def _answer_plain(self, item: Dict[str, Any], value: str) -> Dict[str, Any]:
        store = self.store()
        decision = store.decide(item, value)
        pairs = self.rows()
        store.recompute_all()
        self.flush(pairs)
        return decision

    # ------------------------------------------------------------------------- identity and names

    def set_orientation(self, segment_start: float, orientation: str) -> Dict[str, Any]:
        """Say which way round the two names sit, and let that bind them to the sides.

        Orientation is only meaningful against a pairing, so a row with no bracket match chosen yet gets a
        refusal naming the missing step rather than a silent swap of whatever strings were in the fields.
        ``unsure`` is always available, and defers the question instead of answering it.
        """
        if orientation not in ("as-listed", "swapped", "unsure"):
            raise review.InvalidAnswer("orientation is as-listed, swapped or unsure, not %r" % orientation)
        pairs = self.rows()
        pair = next((row for row in pairs if number(row[1].get("start")) == number(segment_start)), None)
        if pair is None:
            raise review.InvalidAnswer("no segment at %s" % segment_start)
        _match, view = pair
        question = next((candidate for candidate in self.queue("all")
                         if candidate["kind"] == "orientation"
                         and number(candidate["segment"]["start"]) == number(segment_start)), None)
        if orientation == "unsure":
            if question is None:
                raise review.InvalidAnswer("this row is not being asked about its sides")
            return self.answer(question["fingerprint"], "unsure")
        chosen = (view.get("review") or {}).get("bracket") or self.state.review.get("bracket") or {}
        players = list(chosen.get("players") or [])
        if len(players) != 2:
            raise review.InvalidAnswer("choose the bracket match for this set first: an orientation says "
                                       "which way round two names go, and this row has %d" % len(players))
        return self.assign_bracket_set(segment_start, players, label=chosen.get("label", ""),
                                       orientation=orientation)

    def assign_bracket_set(self, segment_start: float, players: Sequence[str], *, label: str = "",
                           orientation: Optional[str] = None,
                           remembered: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Attach a bracket match to a row and say which side of the screen is which.

        Choosing a bracket match settles *identity*, never a boundary: it names the two players and the
        round, and the pairing is recorded with the source it came from. A bracket's character pair is
        unordered, so until a person states the orientation the flat character fields stay empty and the
        orientation question stays open -- binding names to sides by list order would be a guess wearing
        a name, and an anonymous player cannot be resolved by matching strings either.
        """
        pairs = self.rows()
        pair = next((row for row in pairs if number(row[1].get("start")) == number(segment_start)), None)
        if pair is None:
            raise review.InvalidAnswer("no segment at %s" % segment_start)
        _match, view = pair
        resolved = review.resolve_characters(view, list(players), orientation=orientation,
                                             remembered=remembered or dict(self.state.player_characters))
        store = self.store()
        start = number(view.get("start"))
        store.decide(self._statement(start, "bracket", view), "bracket",
                     context={"players": list(players), "label": label, "orientation": orientation,
                              "source": "start.gg"})
        # A character the listing can place is recorded per side, with the provenance that says whether it
        # came from the tape, from a person, or from the operator's usual pick.
        for side in review.SIDES:
            entry = resolved["sides"][side]
            if not entry["character"]:
                continue
            existing = ((view.get("review") or {}).get("characters") or {}).get(side) or {}
            if existing.get("character") == entry["character"]:
                continue
            store.decide(self._statement(start, "character", view, side), entry["character"],
                         side=side, bracket_player=entry["player"],
                         character_source=entry["character_source"] or "remembered")
        if orientation in ("as-listed", "swapped"):
            question = next((candidate for candidate in self.queue("all")
                             if candidate["kind"] == "orientation"
                             and number(candidate["segment"]["start"]) == start), None)
            store.decide(question or self._statement(start, "orientation", view), orientation)
        self.reflow()
        return {"orientation": orientation if orientation in ("as-listed", "swapped") else None,
                "export_ready": resolved["export_ready"],
                "questions": resolved["questions"], "sides": resolved["sides"]}

    @staticmethod
    def _statement(start: float, kind: str, view: Dict[str, Any], side: str = "") -> Dict[str, Any]:
        """A question-shaped record for something the operator stated rather than was asked.

        The subject carries the side so two characters on one row are two records: fingerprints collide
        otherwise, and the second answer silently replaced the first.
        """
        subject = "%s..%s" % (round(start or 0, 3), round(number(view.get("end")) or 0, 3))
        return {"kind": kind, "segment": {"start": start},
                "subject": "%s|%s" % (subject, side) if side else subject, "options": []}

    def rebind(self, pairs: Sequence[Tuple[MatchSegment, Dict[str, Any]]]) -> None:
        """Re-derive the flat name and character fields from the journal, never the other way round.

        ``resolve_characters`` is the one place that decides precedence -- a person's word, then what the
        tape showed, then the operator's usual pick, then nothing -- so this calls it instead of repeating
        the order and drifting from it. Player fields follow an orientation statement even over older
        typing, because saying which side is which *is* the statement about who is in ``player1``.
        """
        for _match, view in pairs:
            review_block = view.get("review") or {}
            bracket = review_block.get("bracket") or {}
            players = list(bracket.get("players") or [])
            orientation = review_block.get("orientation")
            resolved = review.resolve_characters(view, players, orientation=orientation,
                                                 remembered=dict(self.state.player_characters))
            if len(players) == 2 and orientation in ("as-listed", "swapped"):
                for side, field in (("p1", "player1"), ("p2", "player2")):
                    view[field] = resolved["sides"][side]["player"] or ""
            if players or (review_block.get("characters") or {}):
                for side, field in (("p1", "character1"), ("p2", "character2")):
                    view[field] = resolved["sides"][side]["character"] or ""
            for field in FLAT_FIELDS:
                entry = (review_block.get("field_edits") or {}).get(field) or {}
                if entry.get("source") in review.PRESERVE_EDIT_SOURCES \
                        and field not in ("player1", "player2"):
                    view[field] = entry.get("value", view.get(field))
            for field in ("player1", "player2", "character1", "character2"):
                value = view.get(field)
                if not value:
                    continue
                bucket = self.state.players if field.startswith("player") else self.state.characters
                if value not in bucket:
                    bucket.append(value)

    def set_character(self, segment_start: float, side: str, character: str) -> None:
        """One side's character, from a person, as an undoable record with its provenance attached."""
        if side not in review.SIDES:
            raise review.InvalidAnswer("side must be one of %s" % ", ".join(review.SIDES))
        pairs = self.rows()
        pair = next((row for row in pairs if number(row[1].get("start")) == number(segment_start)), None)
        if pair is None:
            raise review.InvalidAnswer("no segment at %s" % segment_start)
        _match, view = pair
        store = self.store()
        store.decide(self._statement(number(view.get("start")), "character", view, side), character,
                     side=side, bracket_player=view.get("player1" if side == "p1" else "player2", ""),
                     character_source="human")
        if character and character not in self.state.characters:
            self.state.characters.append(character)
        self.reflow()

    def set_field(self, segment_start: float, field: str, value: str, *,
                  source: str = "human") -> None:
        """A typed field is a human statement, so it is marked as one and survives the next import.

        ``source`` distinguishes the operator's keyboard ("human") from a record they chose to attach
        ("start.gg"). Both outrank a machine suggestion on re-import; neither may be mistaken for footage.
        """
        if field not in FLAT_FIELDS:
            raise review.InvalidAnswer("%s is not an editable match field" % field)
        if source not in review.PRESERVE_EDIT_SOURCES:
            raise review.InvalidAnswer("%s is not a provenance this field accepts: %s"
                                       % (source, ", ".join(review.PRESERVE_EDIT_SOURCES)))
        pairs = self.rows()
        pair = next((row for row in pairs if number(row[1].get("start")) == number(segment_start)), None)
        if pair is None:
            raise review.InvalidAnswer("no segment at %s" % segment_start)
        _match, view = pair
        view[field] = value
        review_block = view.setdefault("review", {})
        review_block.setdefault("field_edits", {})[field] = {"source": source, "value": value}
        self.flush(pairs)

    def set_human_end(self, segment_start: float, value: Optional[float]) -> Dict[str, Any]:
        """Where the operator says the clip stops, as the decision that outranks the machine.

        The existing controls wrote ``segment.end``, which sits *below* ``recovery.clip_end_at`` in the
        authority order, so a manual end on a recovered row changed nothing an export obeyed. This writes
        the governing journal entry and leaves ``end`` as the recorded set end it always was.
        """
        pairs = self.rows()
        pair = next((row for row in pairs if number(row[1].get("start")) == number(segment_start)), None)
        if pair is None:
            raise review.InvalidAnswer("no segment at %s" % segment_start)
        _match, view = pair
        fingerprint = self.end_fingerprint(view)
        if value is None:
            existing = self.store().decision(fingerprint)
            if existing is None:
                return {"end": None, "cleared": False}
            self.undo(fingerprint)
            return {"end": None, "cleared": True}
        item = self.item(fingerprint, view="all")
        if item is None:
            raise review.InvalidAnswer("no clip-end question for %s to answer" % segment_start)
        current = review.resolve_clip_end(view, following=self._following(view),
                                          duration=self.state.duration or None)
        base = current["end"]
        answer = "extend" if (base is None or value >= base) else "shorten"
        return self.answer(fingerprint, answer, end_at=value)

    @staticmethod
    def end_fingerprint(view: Dict[str, Any]) -> str:
        start, end = number(view.get("start")), number(view.get("end"))
        return "clip_end|%s|%s..%s" % (start, round(start or 0, 3), round(end or 0, 3))

    def _following(self, view: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rows = [row for _match, row in self.rows()]
        position = next((i for i, row in enumerate(rows) if row is view), None)
        if position is None or position + 1 >= len(rows):
            return None
        return rows[position + 1]

    # ------------------------------------------------------------------------------ consequences

    def export_plan(self) -> Dict[str, Any]:
        return review.export_plan(self.project())

    def summary(self) -> Dict[str, Any]:
        project = self.project()
        store = self.store()
        plan = review.export_plan(project)
        return {"open": len(review.review_queue(project, context_seconds=self.context_seconds,
                                                include_confident=self.include_confident, view="open")),
                "deferred": len(store.deferred()),
                "answered": len(store.governing()),
                "invalidated": len([d for d in store.state["decisions"]
                                    if d.get("status") == review.INVALIDATED]),
                "held": len(plan["held"]),
                "exportable": len(plan["exportable"]),
                "unplaced_seconds": plan["counts"]["unplaced_seconds"],
                "openings_needing_review": plan["counts"]["openings_needing_review"],
                # A different count from the one above, and the tab has to be able to say which is which:
                # an opening needing a reading includes the ones where the rows simply stop short, and on
                # BB9 that is five rows against three the saved clock actually contradicts.
                "machine_contradicted_starts": sum(1 for clip in plan["clips"]
                                                   if clip["boundary"]["machine_supports_start"]
                                                   == "contradicted"),
                # Also apart, because it answers a different question: not what the evidence says about the
                # boundary, but that the pipeline's own readings of it say different things.
                "start_readings_conflict": sum(1 for clip in plan["clips"]
                                               if clip["boundary"]["start_readings_conflict"]),
                "operator_accepted": plan["counts"]["operator_accepted"],
                "operator_approved_partials": plan["counts"]["operator_approved_partials"],
                # Its own number, and deliberately not folded into any of the counts above: a proposal has
                # answered nothing, moved nothing and been accepted by nobody. Without it on the summary line
                # a reviewer cannot see that suggestions are waiting without opening rows one by one.
                "machine_proposals_pending": plan["counts"]["machine_proposals_pending"],
                "machine_supported_starts": plan["counts"]["machine_supported_starts"],
                "unknown_starts": plan["counts"]["unknown_starts"],
                # Its own line, and its own fallback half, because a machine-applied bound is neither a cue
                # settled at this frame nor a bound with nothing behind it -- and a reviewer skimming for
                # "how much of this did the pipeline decide for itself, and how much of that was a fallback"
                # cannot get at it from either neighbour.
                "machine_applied_starts": plan["counts"]["machine_applied_starts"],
                "machine_applied_starts_fallback": plan["counts"]["machine_applied_starts_fallback"],
                # Always zero until an independent reference exists, and printed even when it is: the
                # number a report must never be allowed to imply is "validated".
                "independently_validated": plan["counts"]["independently_validated"],
                "inference_sent": False}

    def unresolved_gameplay(self) -> List[Dict[str, Any]]:
        """Every interval no clip claims, plus every clip on hold, in tape order.

        Each row keeps the three things that are easy to collapse into one: what the stretch *is* (an
        interval no candidate claims), what the saved evidence says about it (play reaches it, or it does
        not, or nothing was recorded either way), and how it would be known if anybody wanted to know. The
        last two are the difference between "no play here" and "nobody looked", and a list that shows only
        the first invites the wrong one.
        """
        plan = self.export_plan()
        rows = [{"from": row["from"], "to": row["to"], "seconds": row["seconds"],
                 "kind": "gap", "edge": row["edge"], "why": row["why"],
                 "what_it_is": row.get("what_it_is") or "",
                 "how_it_would_be_known": row.get("how_it_would_be_known") or "",
                 "gameplay_seen_in_saved_evidence": row["gameplay_seen_in_saved_evidence"],
                 "playable": True} for row in plan["unplaced_play"]]
        for clip in plan["held"]:
            rows.append({"from": clip["start"], "to": clip["end"],
                         "seconds": None if clip["start"] is None or clip["end"] is None
                         else round(clip["end"] - clip["start"], 6),
                         "kind": "held", "edge": "candidate",
                         "why": "; ".join(clip["reasons"]) or "held",
                         "what_it_is": "a clip the project proposes and the plan refuses to render",
                         "how_it_would_be_known": "answer the question it is held for, or watch the "
                                                  "boundary it is argued about",
                         "gameplay_seen_in_saved_evidence": None,
                         # A held clip is the most watchable thing on the list: the reviewer has to see the
                         # boundary that is being argued about before they can answer the question about it.
                         "playable": True, "review_kind": clip.get("review_kind"),
                         "clip_index": clip["index"]})
        return sorted(rows, key=lambda row: (row["from"] is None, row["from"] or 0.0))


class ReviewPanel:
    """What the review tab shows and which buttons exist, as data a test can assert on.

    ``app.py`` renders this and maps a button press back through :meth:`press`. Splitting the two is what
    lets the flow be verified where tkinter cannot be imported: the widget layout is the only part of this
    feature that has never been executed on this box, and this class is the part that has.
    """

    def __init__(self, state: ProjectState, *, context_seconds: float = DEFAULT_CONTEXT_SECONDS) -> None:
        self.flow = ReviewFlow(state, context_seconds=context_seconds)
        self.selected: Optional[str] = None
        self.view = "open"
        self.message = ""
        self.current_time = 0.0
        self.context_wide: Dict[str, float] = {}
        self.playback: Optional[Dict[str, Any]] = None
        self.preferred_index: Optional[int] = None
        # Which proposal the card currently on screen is offering, remembered at render time. A button that
        # resolves "the live proposal" when it is pressed will happily apply a suggestion that appeared after
        # the reviewer looked at it; remembering what was drawn is what lets a stale press refuse instead.
        self.rendered: Optional[Dict[str, Any]] = None

    def state_view(self) -> Dict[str, Any]:
        items = self.flow.queue(self.view)
        if self.selected is None or not any(i["fingerprint"] == self.selected for i in items):
            # Answering a question removes it from the list, and jumping back to the top on every click
            # would make a reviewer re-find their place each time. So stay with the clip just answered
            # until its remaining questions are done, and only then move to the next clip.
            same_clip = [i for i in items if i["index"] == self.preferred_index]
            self.selected = (same_clip[0]["fingerprint"] if same_clip else
                             items[0]["fingerprint"] if items else None)
        selected = next((item for item in items if item["fingerprint"] == self.selected), None)
        plan = self.flow.export_plan()
        # Recorded as part of drawing the card, not consulted to change what the card says: this is the
        # identity the apply/reject buttons under it were rendered for.
        self.rendered = ({"fingerprint": self.selected, "kind": (selected.get("proposal") or {}).get("kind"),
                          "proposal_id": (selected.get("proposal") or {}).get("proposal_id")}
                         if selected and selected.get("proposal") else None)
        return {"version": PANEL_VERSION,
                "source": {"video_path": self.flow.state.video_path,
                           "has_source": bool(self.flow.state.video_path),
                           "duration": self.flow.state.duration,
                           "event_name": self.flow.state.event_name},
                "summary": self.flow.summary(),
                "views": ["open", "deferred", "answered"],
                "active_view": self.view,
                "rows": [{"fingerprint": item["fingerprint"], "kind": item["kind"],
                          "state": item["state"], "index": item["index"],
                          "clip": item["index"] + 1, "start": item["segment"]["start"],
                          "end": item["segment"]["end"], "why": item["why_flagged"],
                          "held": item["export_consequence"]["held"]} for item in items],
                "question": self._question_view(selected),
                "held_clips": [{"index": clip["index"], "start": clip["start"], "end": clip["end"],
                                "reasons": clip["reasons"], "review_kind": clip["review_kind"]}
                               for clip in plan["held"]],
                "unresolved": self.flow.unresolved_gameplay(),
                "footage_actions": self._footage_actions(self.flow.unresolved_gameplay()),
                "coverage": self._coverage_view(plan),
                # What has been said about the tape and what nobody has looked at. A stretch with no record
                # is unwatched, and it stays listed as unwatched however many of its neighbours were noted:
                # an annotation pass that stops halfway is not a reviewed recording.
                "footage_coverage": self.flow.footage_coverage(),
                "message": self.message,
                "costs_inference": False}

    def _footage_actions(self, unresolved: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """What a watcher can do with a stretch of tape, per stretch, in tape order.

        Two kinds of action and nothing else. A gap can become a row -- the only way a match nobody marked
        reaches an export -- or it can be described and left alone. Neither marks the footage reviewed: the
        row a claim creates arrives with its opening still open, and a description leaves every boundary
        exactly where it was.
        """
        actions = []
        for row in unresolved:
            if row.get("kind") != "gap" or row.get("from") is None:
                continue
            span = "%s_%s" % (row["from"], row.get("to") if row.get("to") is not None else row["from"])
            actions.append({"from": row["from"], "to": row.get("to"), "seconds": row.get("seconds"),
                            "controls": [{"action": "claim-interval:" + span,
                                          "label": "Claim it as a match"}] +
                                         [{"action": "dispose:%s:%s" % (kind, span), "label": label}
                                          for kind, label in sorted(review.FOOTAGE_DISPOSITIONS.items())
                                          if kind != "play_new_match"]})
        return actions

    @staticmethod
    def _coverage_view(plan: Dict[str, Any]) -> Dict[str, Any]:
        """The two coverage accounts side by side, because one number invites the wrong conclusion.

        "1676.7 s unplaced" read as though the gaps between clips were the whole story; on BB9 the tape's
        own first 53 s and last 57 s were outside the arithmetic entirely, and a held clip counted as
        covered although no job would render it.
        """
        coverage = plan.get("coverage") or {}
        candidates, jobs = coverage.get("candidates") or {}, coverage.get("exportable_jobs") or {}
        # The sequence provenance travels with the coverage view because it qualifies every number next to
        # it: "no opening candidate" means something different when no rows were available at all, and the
        # panel line that renders it reads from here.
        sequence = plan.get("sequence") or {}
        uncovered = coverage.get("uncovered_by_exportable_jobs") or []
        return {"duration": coverage.get("duration"),
                "candidates_seconds": candidates.get("seconds", 0.0),
                "candidates_intervals": candidates.get("intervals_count", 0),
                "jobs_seconds": jobs.get("seconds", 0.0),
                "held_seconds": (coverage.get("held_intervals_not_rendered") or {}).get("seconds", 0.0),
                "uncovered_seconds": round(sum(row["seconds"] for row in uncovered), 6),
                "uncovered_intervals": len(uncovered),
                "uncovered_by_edge": {row["edge"]: row["seconds"] for row in uncovered},
                "known_gameplay_unclaimed": [row for row in uncovered
                                             if row.get("gameplay_seen_in_saved_evidence")],
                "unknown_coverage": [row for row in uncovered
                                     if row.get("gameplay_seen_in_saved_evidence") is False],
                "openings_needing_review": len(plan.get("opening_review") or []),
                "exportable_is_not_accepted": plan.get("exportable_is_not_accepted"),
                "boundary_summary": ReviewPanel._boundary_summary(plan),
                "invalid_bounds": coverage.get("invalid_bounds") or [],
                "sequence": sequence}

    @staticmethod
    def _boundary_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
        """Four counts, because one "accepted" number is what got this wrong in the first place."""
        clips = plan.get("clips") or []
        return {
            "exportable": sum(1 for clip in clips if clip["boundary"]["exportable"]),
            "held": sum(1 for clip in clips if not clip["boundary"]["exportable"]),
            "start_supported": sum(1 for clip in clips
                                   if clip["boundary"]["machine_supports_start"] == "supported"),
            "start_contradicted": sum(1 for clip in clips
                                      if clip["boundary"]["machine_supports_start"] == "contradicted"),
            "start_unknown": sum(1 for clip in clips
                                 if clip["boundary"]["machine_supports_start"] == "unknown"),
            # Reported apart from all three, because it is a different fact: not what the evidence says about
            # the boundary, but that the pipeline's own readings of it do not agree with each other. A row
            # can be counted here and "supported" at the same time, and that is not a contradiction to
            # resolve by arithmetic -- it is the frames to go and look at.
            "start_readings_conflict": sum(1 for clip in clips
                                           if clip["boundary"]["start_readings_conflict"]),
            "operator_accepted": sum(1 for clip in clips
                                     if clip["boundary"]["operator_accepted_boundary"]),
            # Counted apart from every one of those: a proposal has answered nothing, moved nothing and been
            # accepted by nobody. Without its own number on the line a reviewer cannot see that work is
            # waiting on them without selecting rows one by one.
            "machine_proposals_pending": sum(1 for clip in clips
                                             if clip["boundary"].get("machine_proposal_pending")),
            # Its own line, because "a person approved shipping this" and "a person accepted this boundary"
            # are different claims and the first one used to be counted as the second.
            "operator_approved_partial": sum(1 for clip in clips
                                             if clip["boundary"]["partial_override"]),
            "independently_validated": 0,
            "note": ("exportable is permission to cut a file; supported is what the machine evidence says "
                     "about the instant the boundary is on now; operator_accepted is a person's decision "
                     "that this is the boundary; an approved partial is a person agreeing to ship footage "
                     "that loses its opening or its ending, which does not certify the boundary; "
                     "machine proposals awaiting a decision are a machine's suggestion about where a bound "
                     "could move: no boundary moved, no question closed, nobody accepted anything; "
                     "start_readings_conflict counts rows whose saved readings do not agree with each other "
                     "about where the opening is, which is neither support nor contradiction and is settled "
                     "by watching the frames, not by ranking the cues; validated needs a reference outside "
                     "both, and no boundary on this recording has one")}

    def _question_view(self, item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if item is None:
            return None
        context = self._context(item)
        buttons = [{"action": "answer:" + value, "label": label,
                    "needs_note": value == "accept_partial", "enabled": True}
                   for value, label in zip(item["options"], item["labels"])]
        log = (self.flow.state.review or {}).get("log") or []
        # Two undos on purpose. "Undo this" withdraws the answer under the question being read and is
        # disabled when there is none; "Undo last" steps back through the journal whatever the reviewer was
        # standing on -- without it, undoing a boundary move looked dead, because the move had already
        # renumbered the question the button was pointing at.
        buttons.append({"action": "undo", "label": "Undo this",
                        "enabled": item.get("decision") is not None})
        buttons.append({"action": "undo-last", "label": "Undo last", "enabled": bool(log)})
        # The queue builds the proposed-versus-current block; the tab shows it and offers the two answers a
        # person can give it. Nothing here recomputes an instant, so the card and the queue cannot drift.
        proposal = item.get("proposal")
        proposal_buttons = ([{"action": "proposal:apply", "label": "Apply this proposal",
                              "enabled": True, "needs_note": False},
                             {"action": "proposal:reject", "label": "Reject this proposal",
                              "enabled": True, "needs_note": False}]
                            if proposal else [])
        return {"fingerprint": item["fingerprint"], "kind": item["kind"], "state": item["state"],
                "segment": item["segment"],
                "proposal": proposal,
                "ask": item["ask"], "why": item["why_flagged"], "boundary": item["boundary"],
                "context": context, "evidence": item["evidence"],
                "consequence": item["export_consequence"], "decision": item.get("decision"),
                "adjust": item["adjust"],
                "controls": buttons + proposal_buttons + [
                    # An earlier instant the saved rows support looking at, if there is one, labelled with
                    # the level it is supported at and the width the sampling can claim for it: "Look just
                    # before the candidate (round_opening)" must not read like an offer to jump to a set
                    # start, and an instant offered without its interval reads like a measurement.
                    {"action": "play:candidate", "label": _candidate_label(item.get("adjust") or {}),
                     "enabled": item.get("adjust", {}).get("jump_to") is not None},
                    {"action": "play:context", "label": "Play this boundary", "enabled": True},
                    # Only offered when the saved readings of this opening name different frames. It plays
                    # the span that contains them both, because that is the only evidence that can say which
                    # one the footage agrees with -- and the reviewer is not being asked to rank cues.
                    {"action": "play:dispute",
                     "label": "Play the frames in dispute (%.0f s)"
                              % (item["boundary"].get("dispute_window") or {}).get("seconds", 0.0),
                     "enabled": bool(item["boundary"].get("readings_conflict"))},
                    {"action": "play:back", "label": "More footage backward", "enabled": True},
                    {"action": "play:forward", "label": "More footage forward", "enabled": True},
                    {"action": "start:now", "label": "Set opening to current time",
                     "enabled": item["kind"] == "new_match"},
                    {"action": "end:now", "label": "Set end to current time",
                     "enabled": item["kind"] == "clip_end"}]
                + self._identity_controls(item),
                "nudge_buttons": [{"action": "nudge:%s" % delta, "label": "%+.1f s" % delta}
                                  for delta in (-5.0, -1.0, -0.1, 0.1, 1.0, 5.0)]}

    def _context(self, item: Dict[str, Any]) -> Dict[str, Any]:
        context = dict(item["context"])
        wide = self.context_wide.get(item["fingerprint"])
        if wide is not None:
            context["seconds_each_side"] = wide
            duration = self.flow.state.duration or None
            context["play_from"] = max(0.0, context["boundary_at"] - wide)
            context["play_to"] = context["boundary_at"] + wide
            if duration is not None:
                context["play_to"] = min(duration, context["play_to"])
        return context

    def set_current_time(self, seconds: float) -> None:
        self.current_time = float(seconds)

    def play_interval(self, start: float, end: float) -> Dict[str, Any]:
        """Point the player at a stretch of tape no question is about -- an uncovered gap, a held clip.

        The uncovered list is where a missed match would show up, so it has to be as watchable as a
        boundary question. Nothing is rendered: this is the same seek-and-pause window the questions use.
        """
        duration = self.flow.state.duration or 0.0
        window = {"play_from": float(start), "play_to": float(min(end, duration) if duration else end),
                  "boundary_at": float(start), "seconds_each_side": 0.0}
        self.playback = {"from": window["play_from"], "to": window["play_to"],
                         "boundary_at": window["boundary_at"]}
        self.message = "playing %.3f-%.3f: %s" % (window["play_from"], window["play_to"],
                                                  "no clip claims this footage")
        return window

    def choose_bracket(self, players: Sequence[str], label: str = "") -> Dict[str, Any]:
        """The bracket picker's callback: identity for this row, orientation still a separate statement."""
        result = self.flow.assign_bracket_set(self._segment_start(), players, label=label)
        self.message = "bracket match %s attached; say which side is which" % (label or "chosen")
        return result

    def _identity_controls(self, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Who is on the screen, and which way round. Identity first, orientation second, always separate."""
        row = next((row for row in self.flow.rows()
                    if number(row[1].get("start")) == number(item["segment"]["start"])), (None, None))[1]
        chosen = ((row or {}).get("review") or {}).get("bracket") or {}
        controls = [{"action": "bracket:pick", "label": "Choose bracket match", "enabled": True}]
        if item["kind"] == "orientation":
            for value, label in (("as-listed", "As listed"), ("swapped", "Swapped"),
                                 ("unsure", "Unsure")):
                controls.append({"action": "orientation:" + value, "label": label,
                                 "enabled": len(chosen.get("players") or []) == 2 or value == "unsure"})
        return controls

    def press(self, action: str, note: str = "") -> str:
        """One button, one effect, one line of feedback. Everything the panel offers is reachable here."""
        verb, _, argument = action.partition(":")
        try:
            if verb == "view":
                self.view = argument or "open"
                self.selected = None
            elif verb == "select":
                self.selected = argument
            elif verb == "answer":
                answered = self.flow.item(self.selected, view="all")
                decision = self.flow.answer(self.selected, argument, note=note)
                self.preferred_index = None if answered is None else answered["index"]
                self.message = "%s recorded as %s" % (decision["kind"], decision["status"])
            elif verb == "proposal":
                # Applying is the reviewer's decision, taken through the same controller a boundary move
                # always goes through; rejecting changes no boundary at all. Both leave the proposal on the
                # row as the record it is, and an undo of the applied move puts the suggestion back to pending
                # because the snapshot predates it.
                #
                # The card is the instruction: this acts on the proposal that was *drawn*, by the id recorded
                # when it was drawn. Re-resolving the live proposal at click time once moved a row to 770 s
                # while the reviewer was looking at a card that said 785.785 -- the number on the screen and
                # the number applied were different suggestions of the same kind. If the row has been
                # refreshed since, the controller refuses by id rather than substituting, and the card is
                # redrawn so the next press sees what is actually on offer. A row starting at 0.0 also has to
                # count, so presence is tested against None and never by truthiness.
                shown = self.rendered or {}
                start = number(((self.flow.item(self.selected, view="all") or {}).get("segment") or {})
                               .get("start")) if self.selected else None
                kind, proposal_id = shown.get("kind"), shown.get("proposal_id")
                if kind is None or proposal_id is None or start is None:
                    self.message = "this card shows no proposal to act on"
                else:
                    try:
                        if argument == "apply":
                            decision = self.flow.apply_proposal(start, kind, proposal_id=proposal_id,
                                                                note=note)
                            self.message = "applied the %s proposal: recorded as %s under your answer" % (
                                kind, decision["status"])
                        else:
                            self.flow.reject_proposal(start, kind, proposal_id=proposal_id, note=note)
                            self.message = "rejected the %s proposal; the boundary did not move" % kind
                    except review.InvalidAnswer as exc:
                        self.message = "%s - the card has been refreshed" % exc
                self.state_view()
            elif verb == "undo":
                self.message = self._undo()
            elif verb == "undo-last":
                fingerprint = self.flow.undo_last()
                if not fingerprint or not self.flow.undo(fingerprint):
                    self.message = "nothing to undo"
                else:
                    self.message = "stepped back over %s" % fingerprint
                    self.selected = fingerprint if self.flow.item(fingerprint, "all") else self.selected
            elif verb == "nudge":
                decision = self.flow.nudge_boundary(self.selected, float(argument))
                moved = decision.get("end_at") if decision.get("end_at") is not None \
                    else decision.get("start_at")
                self.message = "boundary now %s" % ("%.3f" % moved if moved is not None
                                                    else decision.get("answer"))
            elif verb == "end":
                self.flow.set_human_end(self._segment_start(), self.current_time)
                self.message = "end set to %.3f as a human decision" % self.current_time
            elif verb == "start":
                decision = self.flow.move_start(self.selected, self.current_time)
                self.message = "opening moved to %.3f" % decision["start_at"]
            elif verb == "play":
                self.message = self._play(argument or "context")
            elif verb == "orientation":
                result = self.flow.set_orientation(self._segment_start(), argument)
                self.message = "sides bound as %s" % argument if result.get("export_ready") \
                    else "orientation %s recorded" % argument
            elif verb == "play-interval":
                first, _sep, second = (argument or "").partition("_")
                self.play_interval(float(first), float(second))
                self.message = "playing %.3f-%.3f of footage no clip claims" % (
                    self.playback["from"], self.playback["to"])
            elif verb == "claim-interval":
                # "I watched this stretch and there is a match in it" has to be sayable. Before it was, the
                # unclaimed list was a list of things the operator could look at but never act on, so a
                # completely unmarked match stayed outside the workflow forever.
                first, _sep, second = (argument or "").partition("_")
                end = float(second) if second and second != "None" else None
                claimed = self.flow.claim_footage(float(first), end, note=note)
                self.selected = next((item["fingerprint"] for item in self.flow.queue("open")
                                      if item["kind"] == "new_match"
                                      and item["segment"]["start"] == claimed["claimed_from"]),
                                     self.selected)
                self.message = ("claimed %.3f%s as a match: its opening is open and nothing about it is "
                                "accepted yet%s"
                                % (claimed["claimed_from"],
                                   "" if end is None else "-%.3f" % end,
                                   "" if end is None else " (you set that end, so that answer is yours)")
                                )
            elif verb == "claim-here":
                claimed = self.flow.claim_footage(self.current_time, None, note=note)
                self.message = "claimed the footage from %.3f as a match; its end is still open" \
                    % claimed["claimed_from"]
            elif verb == "dispose":
                # A disposition says what a stretch of tape is. It is not an answer to a review question:
                # it moves no boundary, and it cannot make any part of the recording look reviewed.
                kind, _sep, interval = argument.partition(":")
                first, _sep2, second = interval.partition("_")
                record = self.flow.annotate_footage(
                    kind, float(first), float(second) if second and second != "None" else None,
                    boundary=self.current_time if kind == "play_new_match" else None,
                    note=note, observer="operator")
                self.message = ("noted %.3f-%s as %s; nothing about this project moved"
                                % (record["from"],
                                   "%.3f" % record["to"] if record["to"] is not None else "…",
                                   record["kind"]))
            elif verb == "bracket":
                self.message = "choose a bracket match, then say which side is which"
            else:
                self.message = "the panel has no action %s" % action
        except review.InvalidAnswer as exc:
            self.message = "refused: %s" % exc
        return self.message

    def _undo(self) -> str:
        fingerprint = self.selected if self.selected and self.flow.store().decision(self.selected) \
            else self.flow.undo_last()
        if not fingerprint or not self.flow.undo(fingerprint):
            return "nothing to undo"
        return "undid %s and rebuilt its consequences" % fingerprint

    def _play(self, which: str) -> str:
        item = self.flow.item(self.selected, view="all")
        if item is None:
            raise review.InvalidAnswer("no question selected")
        context = self._context(item)
        step = float(context["seconds_each_side"])
        if which == "candidate":
            # Look at the instant the saved sequence proposes, without moving the boundary: the reviewer
            # decides whether to bring the clip start there, and only their answer is recorded.
            candidate = self.flow.opening_candidate(item["fingerprint"])
            if candidate["at"] is None:
                raise review.InvalidAnswer(str(candidate["sequence"].get("reason")
                                               or "no earlier opening candidate is available"))
            self.playback = {"from": max(0.0, candidate["at"] - step),
                             "to": candidate["at"] + step, "boundary_at": candidate["at"]}
            return ("playing the %s candidate at %.3f; the clip still opens at %.3f until you move it"
                    % (candidate["level"], candidate["at"], item["boundary"]["at"] or 0.0))
        if which == "dispute":
            # The two readings are seconds to a minute apart, and the ordinary ±12 s window around the
            # boundary would show one of them and hide the other. Play the span that holds both, and say
            # plainly that the question is what is on screen there -- not which cue to trust.
            window = (item["boundary"] or {}).get("dispute_window")
            if not window:
                raise review.InvalidAnswer("nothing about this opening is in dispute between the saved "
                                           "readings, so there is no wider span to inspect")
            self.playback = {"from": window["from"], "to": window["to"],
                             "boundary_at": item["boundary"]["at"]}
            return ("playing %s-%s, the frames the saved evidence disagrees about (%d readings of this "
                    "opening); the question is what is on screen there, not which cue to believe"
                    % (review.instant_text(window["from"]), review.instant_text(window["to"]),
                       window["readings"]))
        if which == "back":
            context = self._grow(item["fingerprint"], context, earlier=step)
        elif which == "forward":
            context = self._grow(item["fingerprint"], context, earlier=-step)
        self.playback = {"from": context["play_from"], "to": context["play_to"],
                         "boundary_at": context["boundary_at"]}
        return "playing %.3f-%.3f from saved evidence, nothing new was analysed" % (
            self.playback["from"], self.playback["to"])

    def _grow(self, fingerprint: str, context: Dict[str, Any], *, earlier: float) -> Dict[str, Any]:
        """Ask for more of the same tape around the boundary: widen the window, never render a clip."""
        seconds = float(context["seconds_each_side"]) + abs(earlier)
        self.context_wide[fingerprint] = seconds
        boundary = context["boundary_at"] or 0.0
        duration = self.flow.state.duration or 0.0
        return {"boundary_at": boundary, "seconds_each_side": seconds,
                "play_from": max(0.0, boundary - seconds),
                "play_to": min(duration, boundary + seconds) if duration else boundary + seconds}

    def _segment_start(self) -> float:
        item = self.flow.item(self.selected, view="all") if self.selected else None
        if item is None:
            raise review.InvalidAnswer("no question selected")
        return number(item["segment"]["start"])
