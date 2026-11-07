"""One global budget for an experiment: attempts charged before sending, one wall-time deadline.

A bounded run has two limits that neither command nor process can enforce on its own. A per-command
``--max-requests`` resets every time the operator reruns it, so a resumed run can spend the allowance
twice; a ``timeout`` wrapper covers one process, so a second command starts a fresh clock. Both limits
have to live *outside* the process that spends them.

The ledger is one small JSON file, written with ``os.replace`` so a crash mid-write cannot leave a
half-charged budget:

* an attempt is charged **before** the request leaves, so a run killed mid-flight has already paid for
  it and the next process refuses to spend it again;
* while it is in flight the entry is ``in_flight``, which is the state the transport-uncertain rule
  exists for: the answer is unknown, and an unknown answer is never re-sent silently -- the ledger
  records it and the operator decides;
* ``deadline_at`` is stamped once, on the first send, and every later command obeys the same instant.

This module decides nothing about vision or games. It only answers "am I still allowed to ask", and it
makes the refusal name the cap that produced it.
"""
import json
import hashlib
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CAP = 25
DEFAULT_MINUTES = 25.0
KIND = "attempt-ledger"


def _finite_number(value):
    # type: (Any) -> bool
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class BudgetStopped(RuntimeError):
    """Raised instead of sending. Carries the limit that produced it, so the report can name it."""

    def __init__(self, reason, message=None, **detail):
        # type: (str, Optional[str], **Any) -> None
        self.reason = reason
        self.detail = detail
        super().__init__(message or "not sending: {}".format(reason))


def request_tag(body):
    # type: (Any) -> str
    """A stable name for what a request asks about, taken from the frame it sends.

    Derived from the body rather than passed in, so an attempt cannot be charged to the ledger without
    the request that caused it: the count is a property of what left the machine.
    """
    blob = json.dumps(body, sort_keys=True)
    found = sorted({m.group(0) for m in re.finditer(r"frame-\d{4}", blob)})
    if not found:
        return "body:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    return "+".join(found[:3]) + ("+%d more" % (len(found) - 3) if len(found) > 3 else "")


def attach(client, ledger, purpose="primary", tag=None):
    # type: (Any, AttemptLedger, str, Optional[Any]) -> Any
    """Charge every attempt this client makes to the shared ledger, before it is sent.

    Wrapping the transport call, not the classify loop, is the whole point: the retry-on-unusable-reply
    loop inside the client makes more than one attempt per group, and a cap that counts groups is not a
    cap on what is spent. The client's own request identity, provenance and transport-uncertain rule are
    untouched -- this only refuses to send once the ledger says stop.

    The entry is named by :func:`request_tag` (a hash of the body, or its frame tokens) unless the caller
    has set ``client.ledger_request_tag`` -- a route whose body is base64 frames cannot recover that hash
    from what it wrote to disk, and an in-flight reconciliation needs a name it *can* recompute, or a
    killed process leaves an attempt that the next one dares neither re-send nor close.

    ``tag`` overrides both, and exists because frame tokens are not unique across routes: one frame can
    be asked twice in a run, once for its pip row and once for its nameplate. Two attempts, two answers,
    two questions -- and a reconciliation that keys on the bare frame would close the band's hanging
    attempt with the crop's answer file. A caller that runs several routes against one ledger has to name
    them apart, which is what a route-qualified tag does.
    """
    inner = client._post

    def charged(body):
        label = getattr(client, "ledger_request_tag", None)
        named = tag(body) if tag else (label or request_tag(body))
        entry = ledger.reserve(request=named, purpose=purpose)
        try:
            reply = inner(body)
        except BaseException as exc:
            # BaseException, not Exception: an operator's Ctrl-C lands here too, and the attempt it
            # leaves behind is a spend whose outcome nobody knows. Closing the entry at the moment the
            # send fails is what keeps the *attempt's* outcome separate from the *request's* -- a later
            # retry that answers has nothing to do with this one, and an entry left ``in_flight`` for a
            # process that is already dead only invites a later reconciliation to guess.
            from tekken_vod_helper import boundary_media
            uncertain = isinstance(exc, (boundary_media.TransportUncertain, KeyboardInterrupt))
            reason = getattr(exc, "reason", None)
            if isinstance(exc, KeyboardInterrupt):
                reason = reason or "interrupted_before_reply"
            ledger.settle(entry, "uncertain" if uncertain else "failed",
                          error=str(exc)[:200] or type(exc).__name__, reason=reason)
            raise
        ledger.settle(entry, "answered", tokens=(reply.get("usage") or {}))
        return reply

    client._post = charged
    client.ledger = ledger
    return client


class AttemptLedger:
    """A shared cap on attempts and a single wall-time deadline across every command that uses it."""

    def __init__(self, path, cap=DEFAULT_CAP, minutes=DEFAULT_MINUTES, label="trial", clock=None,
                 created_at=None, deadline_at=None, entries=None, dry_run=False):
        # type: (Any, int, float, str, Any, Optional[float], Optional[float], Optional[List[Dict[str, Any]]], bool) -> None
        self.path = Path(path)
        self.cap = int(cap)
        self.minutes = float(minutes)
        self.label = str(label)
        self.clock = clock or time.time
        self.created_at = created_at
        self.deadline_at = deadline_at
        self.entries = list(entries or [])
        self.dry_run = bool(dry_run)

    # ------------------------------------------------------------------ construction

    @classmethod
    def open(cls, path, cap=DEFAULT_CAP, minutes=DEFAULT_MINUTES, label="trial", dry_run=False,
             clock=None):
        # type: (Any, int, float, str, bool, Any) -> "AttemptLedger"
        """Load an existing ledger, or create one. Creating never overwrites a spent one."""
        path = Path(path)
        cap = int(cap)
        minutes = float(minutes)
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            raise ValueError("cap must be a positive integer")
        if not _finite_number(minutes) or minutes <= 0:
            raise ValueError("minutes must be a positive number of minutes")
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("kind") != KIND:
                raise ValueError("{} is not an attempt ledger".format(path))
            if int(data["cap"]) > cap:
                # A ledger written under a wider budget cannot be reinterpreted under a narrower one
                # without inventing spending; and one written narrower cannot silently widen.
                raise ValueError("ledger at {} was opened with cap {}; refusing to run at {}"
                                 .format(path, data["cap"], cap))
            if float(data["minutes"]) != minutes:
                raise ValueError("ledger at {} was opened with a {} minute deadline; this command asks "
                                 "for {} -- the deadline is one clock across every command".format(
                                     path, data["minutes"], minutes))
            return cls(path, cap=cap, minutes=minutes, label=data.get("label", label),
                       created_at=data.get("created_at"), deadline_at=data.get("deadline_at"),
                       entries=data.get("entries"), dry_run=data.get("dry_run", dry_run),
                       clock=clock)
        ledger = cls(path, cap=cap, minutes=minutes, label=label, dry_run=dry_run, clock=clock)
        ledger._write()
        return ledger

    # ------------------------------------------------------------------ state

    @property
    def used(self):
        # type: () -> int
        return len(self.entries)

    @property
    def remaining(self):
        # type: () -> int
        return max(0, self.cap - self.used)

    @property
    def in_flight(self):
        # type: () -> List[Dict[str, Any]]
        return [e for e in self.entries if e.get("state") == "in_flight"]

    @property
    def uncertain(self):
        # type: () -> List[Dict[str, Any]]
        """Spent attempts whose own outcome no reply explains. Charged, and never silently re-sent."""
        return [e for e in self.entries if e.get("state") == "uncertain"]

    def states(self):
        # type: () -> Dict[str, int]
        """How many attempts ended each way. Counted per attempt, never per request."""
        counts = {"answered": 0, "in_flight": 0, "uncertain": 0, "failed": 0}
        for entry in self.entries:
            state = str(entry.get("state"))
            counts[state] = counts.get(state, 0) + 1
        return counts

    def seconds_left(self):
        # type: () -> Optional[float]
        if self.deadline_at is None:
            return None
        return self.deadline_at - self.clock()

    def summary(self):
        # type: () -> Dict[str, Any]
        left = self.seconds_left()
        states = self.states()
        # Two questions, and one number cannot answer both: "did every attempt get a reply" is about an
        # attempt, "is every request answered" is about a request, and a run that retried a frame it had
        # already paid for answers them differently. They are reported apart so neither can be read as
        # the other.
        return {"kind": KIND, "path": str(self.path), "label": self.label, "cap": self.cap,
                "attempts_charged": self.used, "attempts_remaining": self.remaining,
                "attempts_by_state": states,
                "minutes": self.minutes, "created_at": self.deadline_at and self.created_at,
                "deadline_at": self.deadline_at,
                "seconds_until_deadline": None if left is None else round(left, 1),
                "in_flight": len(self.in_flight),
                "in_flight_requests": [e.get("request") for e in self.in_flight],
                "uncertain_attempts": states.get("uncertain", 0),
                "uncertain_requests": [{"request": e.get("request"), "n": e.get("n"),
                                        "reason": e.get("outcome_reason") or e.get("reason"),
                                        "request_satisfied": bool(e.get("request_satisfied"))}
                                       for e in self.uncertain],
                "requests_satisfied_by_an_answer_file": sorted(
                    {e.get("request") for e in self.entries if e.get("request_satisfied")}),
                "dry_run": self.dry_run,
                "stops_when": ["attempt_cap_reached", "wall_time_deadline_reached"]}

    # ------------------------------------------------------------------ spending

    def reserve(self, request, purpose="primary"):
        # type: (str, str) -> Dict[str, Any]
        """Charge one attempt before the request goes out. Raises BudgetStopped rather than sending."""
        if not request:
            raise ValueError("an attempt must name the request it charges")
        if self.dry_run:
            raise BudgetStopped("dry_run", "this ledger is marked dry-run: nothing may be sent")
        if self.deadline_at is not None and self.clock() >= self.deadline_at:
            self._write()
            raise BudgetStopped("wall_time_deadline_reached",
                                "the run's single {} minute deadline passed at {:.0f}".format(
                                    self.minutes, self.deadline_at),
                                deadline_at=self.deadline_at)
        if self.used >= self.cap:
            self._write()
            raise BudgetStopped("attempt_cap_reached",
                                "{} of {} attempts spent; nothing further is sent".format(
                                    self.used, self.cap),
                                cap=self.cap)
        if self.deadline_at is None:
            self.created_at = self.clock()
            self.deadline_at = self.created_at + self.minutes * 60.0
        entry = {"n": self.used + 1, "request": str(request), "purpose": str(purpose),
                 "charged_at": round(self.clock(), 3), "state": "in_flight",
                 "deadline_at": self.deadline_at}
        self.entries.append(entry)
        self._write()          # durable before the socket opens
        return entry

    def settle(self, entry, state, **detail):
        # type: (Dict[str, Any], str, **Any) -> Dict[str, Any]
        """Close an attempt. Charged either way -- an unknown answer is still a spent attempt."""
        if state not in ("answered", "failed", "uncertain"):
            raise ValueError("unknown settle state {!r}".format(state))
        entry["state"] = state
        entry["settled_at"] = round(self.clock(), 3)
        entry.update({k: v for k, v in detail.items() if k in ("frames", "reason", "error", "tokens")})
        self._write()
        return entry

    def reconcile_in_flight(self, answered_requests, reason="answer on disk"):
        # type: (Any, str) -> Dict[str, Any]
        """Attribute an answer found on disk to one attempt, and never to more than one.

        ``answered_requests`` is the set of request names with an answer file, or a mapping of name to the
        answer's modification time. An answer proves the **request** was satisfied; it does not prove that
        every attempt which carried that request got a reply, and the two facts used to be written into the
        same field. A killed process leaves an attempt whose outcome is genuinely unknown, and a resume
        that re-sends the frame and gets an answer has explained *its own* attempt -- the earlier spend
        stays an unknown outcome, still charged, so a reader can see that the run paid twice for one
        request. Closing it as answered would erase the only evidence of that.

        So an attempt is only closed when the answer can be tied to it: no other attempt for the same
        request already explains the file (one that was settled answered by the transport, which is the
        exact link), and no rival hanging attempt competes for the same single file. What cannot be
        attributed is settled ``uncertain`` with the reason, and both cases carry
        ``request_satisfied`` because that much the file does prove.

        Nothing is refunded and nothing is re-sent: the entry count, and so ``attempts_charged``, only
        ever grows.
        """
        times = dict(answered_requests.items()) if hasattr(answered_requests, "items") else {}
        known = set(times) if times else set(answered_requests)
        report = {"closed": [], "unattributable": [], "requests_satisfied": sorted(known)}
        # Satisfaction is stamped on every attempt that carried an answered request, whatever its own
        # outcome, because it is a fact about the request. Whether *this* attempt got a reply is the
        # separate question the state field answers, and only an in-flight entry still owes an answer to
        # it. The comparison below is by identity: two attempts for one request differ only by ``n``, and
        # equal-valued dicts would otherwise be confused for each other.
        by_id = {id(entry): entry for entry in self.entries}
        touched = set()
        for entry in self.entries:
            if entry.get("request") in known:
                if not entry.get("request_satisfied"):
                    entry["request_satisfied"] = True
                    touched.add(id(entry))
                if entry.get("state") == "in_flight":
                    touched.add(id(entry))
        hanging = [entry for entry in self.entries if id(entry) in touched
                   and entry.get("state") == "in_flight"]
        # Attribution is decided against a snapshot. If closing one entry could change what another
        # hanging attempt sees as its rivals, the first of them is attributed by the order this loop
        # happens to walk in -- which is a guess with the look of a rule.
        for entry in hanging:
            same = [by_id[id(other)] for other in self.entries
                    if other is not entry and other.get("request") == entry.get("request")]
            explained = [other for other in same
                         if other.get("state") == "answered" and other.get("settled_at") is not None]
            rivals = [other for other in hanging if other is not entry
                      and other.get("request") == entry.get("request")]
            when = times.get(entry.get("request"))
            if explained:
                self._close_unknown(entry, "the reply on disk is linked to attempt {} of this request, "
                                           "not this one".format(explained[0].get("n")), report,
                                    "reply_belongs_to_another_attempt")
            elif rivals:
                self._close_unknown(entry, "one answer file cannot be attributed between {} attempts "
                                           "left hanging for this request".format(len(rivals) + 1),
                                    report, "reply_not_attributable")
            elif when is not None and entry.get("charged_at") is not None and when < entry["charged_at"]:
                self._close_unknown(entry, "the reply on disk predates this attempt", report,
                                    "reply_predates_attempt")
            else:
                entry["state"] = "answered"
                entry["settled_at"] = round(self.clock(), 3)
                entry["reason"] = reason
                report["closed"].append(entry.get("request"))
        if touched:
            self._write()
        return report

    def _close_unknown(self, entry, detail, report, reason):
        # type: (Dict[str, Any], str, Dict[str, Any], str) -> None
        """Keep a spent attempt whose own outcome no reply explains, and say why."""
        entry["state"] = "uncertain"
        entry["settled_at"] = round(self.clock(), 3)
        entry["outcome_reason"] = reason
        entry["reason"] = detail
        report["unattributable"].append(entry.get("request"))

    # ------------------------------------------------------------------ persistence

    def _write(self):
        # type: () -> None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.summary()
        payload["entries"] = self.entries
        text = json.dumps(payload, indent=2, sort_keys=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(str(temp), str(self.path))
