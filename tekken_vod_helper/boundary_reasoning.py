"""Temporal reasoning for boundary proposals using a vision-language model."""
import hashlib
import http.client
import json
import math
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import boundary_contract, local_model
from .boundary_names import names_close as _names_close, normalise_name as _normalise_name, same_pair as _same_pair

MAX_INPUT_CHARS = 500_000
_ALGORITHM = "temporal-qwen-v1"
# v2 keeps v1's single-window prompt byte-for-byte and adds size-bounded windows, global
# observation ids and explicit cross-window reconciliation for timelines that cannot fit one call.
WINDOW_ALGORITHM = "temporal-qwen-v2"
_DEFAULT_WINDOW_RATIO = 0.8
DEFAULT_WINDOW_OVERLAP = 120
_DEFAULT_WINDOW_ATTEMPTS = 2
DEFAULT_ADJUDICATION_REQUESTS = 4
# A reasoning model on a 2,000-row timeline spent 12,000 tokens thinking and produced no content at
# all (measured 2026-10-28), so the completion budget has to cover the analysis and the answer.
DEFAULT_COMPLETION_TOKENS = 48_000
_HEADER_CHAR_KEYS = ("character1", "character2")

# The rules live in boundary_contract, which is also what the validator applies. Writing them twice is
# how the model ended up instructed to reject the two BB9 sets whose edges were never broadcast, while
# a wrong boundary elsewhere still passed as a completed candidate.
_SYSTEM_PROMPT = (
    "You are a temporal boundary adjudicator for Tekken tournament VODs.\n"
    "Given a chronological sequence of classified observations, infer tournament SETS.\n"
    "A SET is a completed bracket match that may contain multiple games.\n\n"
    "Rules:\n"
    + boundary_contract.prompt_rules()
    + "\nOutput: a strict JSON array of proposal objects with exactly these fields:\n"
    '[{"start_id": "<observation_id>", "end_id": "<observation_id>", '
    '"evidence_ids": ["<id>", ...], "disposition": "candidate|uncertain|aborted", '
    '"reason": "<brief explanation>"}]\n\n'
    "Use only observation_ids from the input. Do not reference historical data, window counts, "
    "or expected match counts.\n"
    "Respond with a JSON array only. No markdown fences, no text outside the JSON."
)


class TemporalAdjudicationError(Exception):
    """Raised when temporal adjudication fails; must not look successful."""
    pass


def _header_sightings(segment, min_confidence):
    # type: (List[Dict[str, Any]], float) -> List[Tuple[Any, str, str, str, str]]
    """Collect (timestamp, name, name, character, character) header readings made during play.

    Only gameplay rows qualify: stage-select grids and the next player's controller overlay report
    different pairings, so accepting every confident state lets unrelated overlays stand in for the
    metadata of the set that was actually played. Each entry keeps a row's own slot association, so
    a name can never be separated from the character reported beside it.
    """
    sightings = []
    for row in segment:
        if row.get("state") != "gameplay":
            continue
        conf = row.get("confidence", 0)
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or conf < min_confidence:
            continue
        n1, n2 = row.get("player1"), row.get("player2")
        if not isinstance(n1, str) or not isinstance(n2, str):
            continue
        n1, n2 = n1.strip(), n2.strip()
        if not n1 or not n2:
            continue

        def _text(value):
            return value.strip() if isinstance(value, str) else ""

        chars = [_text(row.get(key)) for key in _HEADER_CHAR_KEYS]
        sightings.append((row.get("timestamp"), n1, n2, chars[0], chars[1]))
    return sightings


def _gameplay_metadata(segment, min_confidence=0.5):
    # type: (List[Dict[str, Any]], float) -> Dict[str, Any]
    """Resolve the set's broadcast header, keeping every name bound to its own character.

    The winning pair is the one most recently reported during play at two distinct timestamps; a
    single sighting can be an overlay caught mid-transition. Names are written in a canonical
    (case-folded) slot order so the result cannot depend on row order, and the characters are
    permuted with the names they were observed beside. When no row reports a name together with a
    character, the association is reported as unverified instead of being guessed from screen slots.
    """
    sightings = _header_sightings(segment, min_confidence)
    if not sightings:
        return {"association": {"basis": "no_gameplay_header_rows", "verified": False}}

    groups = {}  # type: Dict[Tuple[str, str], List[Tuple[Any, str, str, str, str]]]
    for s in sightings:
        groups.setdefault(tuple(sorted((s[1].casefold(), s[2].casefold()))), []).append(s)
    stamps = {k: {s[0] for s in v if isinstance(s[0], (int, float)) and not isinstance(s[0], bool)}
              for k, v in groups.items()}
    verified = {k for k, v in stamps.items() if len(v) >= 2}
    pool = verified or set(groups)
    winner = max(pool, key=lambda k: max(stamps[k]) if stamps[k] else -1)
    rows = sorted(groups[winner], key=lambda s: (not isinstance(s[0], (int, float)) or isinstance(s[0], bool), s[0]))
    latest = rows[-1]
    ordered_names = sorted((latest[1], latest[2]), key=str.casefold)

    # votes[char] = [count, latest_timestamp, display_spelling] per observed name
    bindings = {}  # type: Dict[str, Dict[str, List[Any]]]
    for ts, n1, n2, c1, c2 in rows:
        for name, char in ((n1, c1), (n2, c2)):
            if not char:
                continue
            votes = bindings.setdefault(name.casefold(), {})
            entry = votes.setdefault(char.casefold(), [0, None, char])
            entry[0] += 1
            numeric_ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
            if numeric_ts is not None and (entry[1] is None or numeric_ts > entry[1]):
                entry[1], entry[2] = numeric_ts, char

    characters = []
    for name in ordered_names:
        votes = bindings.get(name.casefold())
        if not votes:
            characters.append("")
            continue
        # A player may legally change characters between games, so the character played most of
        # the set wins and a tie falls back to the most recent one.
        characters.append(max(votes.values(), key=lambda e: (e[0], e[1] if e[1] is not None else -1))[2])

    if all(characters):
        basis = "co_observed_slots"
    elif any(characters):
        basis = "partially_co_observed"
    else:
        basis = "names_never_co_observed_with_characters"
    out = {"player1": ordered_names[0], "player2": ordered_names[1],
           "association": {"basis": basis, "verified": bool(verified),
                           "sightings": len(rows),
                           "timestamps": len(stamps[winner])}}
    if any(characters):
        out["character1"], out["character2"] = characters[0], characters[1]
    return out


def _association_review(match):
    # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
    """A review item whenever a set's name/character pairing rests on anything but co-observation."""
    association = match.get("metadata_association") or {}
    if association.get("basis") == "co_observed_slots" and association.get("verified"):
        return None
    return {
        "type": "metadata_association_uncertain",
        "start": match["start"],
        "end": match["end"],
        "player1": match.get("player1", ""),
        "player2": match.get("player2", ""),
        "character1": match.get("character1", ""),
        "character2": match.get("character2", ""),
        "association": association,
        "disposition": "candidate",
        "reason": "Player/character pairing was not established from confident gameplay rows that "
                  "report the name beside the character; the pairing may be stale, swapped or blank.",
    }


def build_observations(timeline):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Assign one global observation id per timeline row, in the saved chronological order.

    Ids are derived from position alone, so every window of the same timeline refers to the same
    observation and cross-window proposals can be compared, deduplicated and reconciled.
    """
    obs = []  # type: List[Dict[str, Any]]
    for i, row in enumerate(timeline):
        entry = {
            "observation_id": "obs_{:04d}".format(i),
            "timestamp": row.get("timestamp"),
            "state": row.get("state", "unknown"),
            "confidence": row.get("confidence", 0.0),
            "player1": row.get("player1", ""),
            "player2": row.get("player2", ""),
            "character1": row.get("character1", ""),
            "character2": row.get("character2", ""),
            "evidence": row.get("evidence", ""),
        }
        for key in ('score1', 'score2'):
            value = row.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                entry[key] = value
        obs.append(entry)
    for o in obs:
        ts = o["timestamp"]
        if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts):
            raise TemporalAdjudicationError(
                "Non-finite timestamp at observation {}".format(o["observation_id"])
            )
    return obs


def _serialized_observation(obs):
    # type: (Dict[str, Any]) -> int
    # Two extra characters, not one: json.dumps separates array items with ", ", so sizing on a bare
    # comma would understate every prompt by roughly one character per observation.
    return len(json.dumps(obs, ensure_ascii=False)) + 2


def user_content(obs, policy_text, window_note=""):
    # type: (List[Dict[str, Any]], str, str) -> str
    """Serialize one adjudication prompt. An empty window note keeps the v1 bytes exactly."""
    obs_json = json.dumps({"observations": obs}, ensure_ascii=False)
    blocks = []
    if policy_text:
        blocks.append("Policy context:\n{}".format(policy_text))
    if window_note:
        blocks.append("Window context:\n{}".format(window_note))
    if not blocks:
        return obs_json
    blocks.append("Data:\n{}".format(obs_json))
    return "\n\n".join(blocks)


def _prompt_scaffold(policy_text, window_note):
    # type: (str, str) -> int
    """Characters a prompt costs before any real observation is added."""
    return len(_SYSTEM_PROMPT) + len(user_content([], policy_text, window_note))


def _window_data_budget(max_input_chars, ratio, policy_text, window_note=""):
    # type: (int, float, str, str) -> int
    """Characters available for observations after system, policy and JSON framing are charged."""
    scaffold = _prompt_scaffold(policy_text, window_note)
    budget = int(max_input_chars * ratio) - scaffold - 64
    if budget <= 0:
        # A small cap can put the ratio target below the prompt's own framing. The hard limit is
        # what actually has to hold, so fall back to it instead of refusing to run at all.
        budget = max_input_chars - scaffold - 64
    if budget <= 0:
        raise TemporalAdjudicationError(
            "Prompt overhead of {} chars leaves no room for observations under the {} char limit "
            "of an adjudication window; raise --max-input-chars".format(scaffold, max_input_chars))
    return budget


def _confident(row):
    # type: (Dict[str, Any]) -> bool
    value = row.get("confidence", 0)
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0.5


_BREAK_START = frozenset(["stage_select"])
# Uncovered play closer together than this is reported as one stretch: the menus, reloads and round
# breaks between games of a set routinely span a couple of minutes, and sampling density varies
# across the timeline, so a row-count tolerance fragments one missed set into several flags.
_PLAY_GAP_SECONDS = 180.0
# Broadcast names are OCR'd from a 640-wide frame, so one spelling per set is not guaranteed. On the
# BB9 timeline the same player appeared as "Spitfire2929" and "Spire2929" (0.857 similar) and treating
# that as a new pairing invented a set boundary; distinct opponents in the same data never exceeded
# 0.5. Names at or above this ratio count as the same player.
def _header_pair(row):
    # type: (Dict[str, Any]) -> Optional[frozenset]
    """The two names a header reports, normalised and order-insensitive, or None if unreadable."""
    names = []
    for key in ("player1", "player2"):
        value = row.get(key)
        if not isinstance(value, str):
            return None
        names.append("".join(c for c in value.casefold() if c.isalnum()))
    if not names[0] or not names[1]:
        return None
    return frozenset(names)


def _previous_pair(obs, index, reach=240):
    # type: (List[Dict[str, Any]], int, int) -> Optional[frozenset]
    """The pairing of the play immediately before this index, if any header recorded it."""
    for j in range(index - 1, max(-1, index - reach), -1):
        row = obs[j]
        if row.get("state") == "gameplay" and _confident(row):
            pair = _header_pair(row)
            if pair:
                return pair
    return None


def _clean_break(obs, index, lookback=6):
    # type: (List[Dict[str, Any]], int, int) -> Optional[str]
    """Name the set boundary at this index, or None when cutting here could split a set.

    A stage grid alone does not open a set. Measured on the BB9 timeline, 13 of the 13 boundaries a
    "closing prompt then a grid" rule chose were between-game stage votes inside one set -- 12 kept
    the identical player pairing, which is exactly how the adjudicating model described them. What
    distinguishes a real set boundary in this data is that the pairing changes, so that is the rule:
    a confident grid that opens an episode and reports two names the preceding play did not.

    A changed pairing can also be an OCR variant of the same name; that costs only where a prompt is
    cut, and the shared context plus reconciliation still see the set, so it is accepted knowingly.
    """
    if index <= 0 or index >= len(obs):
        return None
    after = obs[index]
    if after.get("state") not in _BREAK_START or not _confident(after):
        return None
    if obs[index - 1].get("state") == "stage_select":
        return None  # only the grid that opens an episode can be a boundary
    pair = _header_pair(after)
    previous = _previous_pair(obs, index)
    if pair is None or previous is None:
        return None
    if _same_pair(pair, previous):
        return None
    return "new_pair"


def plan_windows(obs, policy_text, max_input_chars=MAX_INPUT_CHARS,
                 ratio=_DEFAULT_WINDOW_RATIO, overlap=DEFAULT_WINDOW_OVERLAP, window_note=""):
    # type: (List[Dict[str, Any]], str, int, float, int, str) -> List[Dict[str, Any]]
    """Split observations into size-bounded, overlapping windows.

    Each window has a core range -- the observations only it owns -- plus leading and trailing
    context taken from its neighbours. Context is what lets a set that crosses a split be proposed
    once with a real endpoint instead of being clamped to a window edge, so every window records how
    much context was wanted and how much actually fit: shortfalls are reported, never hidden.
    """
    if not obs:
        raise TemporalAdjudicationError("Empty timeline")
    budget = _window_data_budget(max_input_chars, ratio, policy_text, window_note)
    sizes = [_serialized_observation(o) for o in obs]

    # A single observation that cannot fit its own window is unadjudicable, not silently dropped.
    if max(sizes) > budget:
        worst = max(range(len(obs)), key=lambda i: sizes[i])
        raise TemporalAdjudicationError(
            "Observation {} serializes to {} chars, over the {} char window budget; "
            "raise --max-input-chars or shorten the recorded evidence".format(
                obs[worst]["observation_id"], sizes[worst], budget))

    prefix = [0]  # type: List[int]
    for size in sizes:
        prefix.append(prefix[-1] + size)

    cores = []  # type: List[List[Any]]
    start = 0
    while start < len(obs):
        end = start
        while end < len(obs) and prefix[end + 1] - prefix[start] <= budget:
            end += 1
        if end >= len(obs):
            cores.append([start, len(obs), "end_of_data"])
            break
        # Cut between sets where that is possible at all: a boundary in the middle of play is the
        # one thing no amount of overlap reliably repairs.
        reach = max(overlap, 1)
        low, high = max(start + 1, end - reach), min(len(obs) - 1, end + reach)
        nearby = [c for c in range(low, high + 1)
                  if prefix[c] - prefix[start] <= budget and _clean_break(obs, c)]
        kind = None
        if nearby:
            end = min(nearby, key=lambda c: (abs(c - end), -c))
            kind = _clean_break(obs, end)
        cores.append([start, end, kind])
        start = end

    scaffold = _prompt_scaffold(policy_text, window_note)
    windows = []  # type: List[Dict[str, Any]]
    for i, (cs, ce, kind) in enumerate(cores):
        wanted_before = min(overlap, cs) if i > 0 else 0
        wanted_after = min(overlap, len(obs) - ce) if i + 1 < len(cores) else 0
        core_chars = sum(sizes[cs:ce])
        before, after = wanted_before, wanted_after
        # Context is a budget luxury: when it cannot all fit, shrink it (reporting the shortfall)
        # rather than letting a prompt exceed the cap or dropping core coverage.
        while before + after > 0:
            total = scaffold + core_chars + sum(sizes[cs - before:cs]) + sum(sizes[ce:ce + after])
            if total <= max_input_chars:
                break
            if before >= after and before > 0:
                before -= 1
            elif after > 0:
                after -= 1
            else:
                break
        ws, we = cs - before, ce + after
        prompt_chars = scaffold + sum(sizes[ws:we])
        if prompt_chars > max_input_chars:
            raise TemporalAdjudicationError(
                "Window {} needs {} chars, over the {} char limit".format(i, prompt_chars, max_input_chars))
        windows.append({
            "index": i,
            "obs_start": ws,
            "obs_end": we,
            "core_start": cs,
            "core_end": ce,
            "observation_count": we - ws,
            "core_count": ce - cs,
            "context_before": before,
            "context_after": after,
            "wanted_context_before": wanted_before,
            "wanted_context_after": wanted_after,
            "context_shortfall": (wanted_before - before) + (wanted_after - after),
            "break_kind": kind,
            "break_snapped": bool(kind),
            "prompt_chars": prompt_chars,
            "first_observation_id": obs[ws]["observation_id"],
            "last_observation_id": obs[we - 1]["observation_id"],
            "start_timestamp": obs[ws]["timestamp"],
            "end_timestamp": obs[we - 1]["timestamp"],
            "is_first": i == 0,
            "is_last": i == len(cores) - 1,
        })
    if sum(w["core_count"] for w in windows) != len(obs):
        raise TemporalAdjudicationError("Window cores do not cover the timeline exactly once")
    if windows and (windows[0]["obs_start"] != 0 or windows[-1]["obs_end"] != len(obs)):
        raise TemporalAdjudicationError("Window coverage does not span the timeline")
    return windows


def _proposal_errors(proposal, index, obs_by_id, obs_order, obs, allowed_ids=None):
    # type: (Dict[str, Any], int, Dict[str, Dict], Dict[str, int], List[Dict[str, Any]], Optional[set]) -> List[str]
    """Every reason one proposal is unusable, in the wording both adjudication paths report."""
    errors = []  # type: List[str]
    sid, eid = proposal["start_id"], proposal["end_id"]
    known = set(obs_by_id.keys()) if allowed_ids is None else set(allowed_ids)

    for field, value in (("start_id", sid), ("end_id", eid)):
        if value not in known:
            if value in obs_by_id:
                errors.append("Proposal index {} references {} '{}' outside this window".format(
                    index, field, value))
            else:
                errors.append("Proposal index {} references unknown {} '{}'".format(index, field, value))
    for ev_id in proposal["evidence_ids"]:
        if ev_id not in known:
            errors.append("Proposal index {} references unknown evidence_id '{}'".format(index, ev_id))
    if errors:
        return errors

    if obs_order[sid] >= obs_order[eid]:
        errors.append(
            "Proposal index {}: start_id '{}' (order {}) is not before end_id '{}' (order {})".format(
                index, sid, obs_order[sid], eid, obs_order[eid]))
        return errors

    s_ts = obs_by_id[sid]["timestamp"]
    e_ts = obs_by_id[eid]["timestamp"]
    if not math.isfinite(s_ts) or not math.isfinite(e_ts):
        errors.append("Proposal index {} has non-finite timestamps".format(index))
    elif s_ts >= e_ts:
        errors.append("Proposal index {}: start timestamp {} >= end timestamp {}".format(index, s_ts, e_ts))

    if proposal["disposition"] == "candidate":
        # The contract, not a fixed screen: a set whose opening grid or closing prompt was never
        # broadcast is still a set. Rejecting it deleted two labelled BB9 matches and returned nothing
        # in their place, which is worse than a partial with a flagged edge.
        verdict = boundary_contract.span_verdict(
            obs_by_id[sid], obs_by_id[eid],
            any(obs[j]["state"] == "gameplay"
                for j in range(obs_order[sid], obs_order[eid] + 1)))
        errors.extend("Proposal index {}: {}".format(index, problem)
                      for problem in verdict["errors"])
        proposal["contract"] = verdict["contract"]
        proposal["start_completeness"] = verdict["start_completeness"]
        proposal["end_completeness"] = verdict["end_completeness"]
        proposal["partial"] = verdict["partial"]
        proposal["edge_notes"] = verdict["notes"]
        if verdict["partial"] and verdict["notes"]:
            proposal["reason"] = ((proposal.get("reason") or "").strip()
                                  + " " + verdict["notes"][0]).strip()
    return errors


def _anchor_to_episode_start(proposal, obs_by_id, obs_order, obs):
    # type: (Dict[str, Any], Dict[str, Dict], Dict[str, int], List[Dict[str, Any]]) -> None
    """Anchor a candidate at the beginning of the observed stage-selection episode.

    The walk uses the whole timeline, not just the window that proposed the cut, which is what lets
    two windows that saw different halves of one set agree on the same global start id.
    """
    start = obs_order[proposal['start_id']]
    while start > 0:
        previous = obs[start - 1]
        if previous['state'] != 'stage_select' or previous['confidence'] < 0.5:
            break
        start -= 1
        proposal['start_id'] = previous['observation_id']


def _downgrade_repeated_zero_scores(proposal, obs_by_id, obs_order, obs):
    # type: (Dict[str, Any], Dict[str, Dict], Dict[str, int], List[Dict[str, Any]]) -> None
    """Mark a candidate uncertain when the broadcast score never left 0-0."""
    segment = obs[obs_order[proposal['start_id']]:obs_order[proposal['end_id']] + 1]
    zero_pair_count = 0
    has_positive = False
    seen_timestamps = set()
    for o in segment:
        ts = o.get('timestamp')
        s1 = o.get('score1')
        s2 = o.get('score2')
        valid1 = isinstance(s1, int) and not isinstance(s1, bool) and s1 >= 0
        valid2 = isinstance(s2, int) and not isinstance(s2, bool) and s2 >= 0
        if valid1 and valid2 and s1 == 0 and s2 == 0 and ts not in seen_timestamps:
            zero_pair_count += 1
            seen_timestamps.add(ts)
        if (valid1 and s1 > 0) or (valid2 and s2 > 0):
            has_positive = True
    if zero_pair_count >= 2 and not has_positive:
        proposal['disposition'] = 'uncertain'
        prev_reason = proposal.get('reason', '')
        new_reason = 'Repeated 0-0 broadcast scores without observed set-score progression; bracket status uncertain.'
        proposal['reason'] = (prev_reason + ' ' + new_reason).strip() if prev_reason else new_reason


class NonRetryableAdjudicationError(TemporalAdjudicationError):
    """The endpoint answered and the same request would fail the same way at temperature 0.

    Re-asking cannot fix a configuration problem, and on a single-purpose box each attempt is minutes
    of the machine's whole attention, so these stop the window after one call and say what to change.
    """


class TemporalAdjudicationTimeout(NonRetryableAdjudicationError):
    """The socket gave up before an answer arrived, so the endpoint may still be working.

    A reply that never arrived is not evidence the request failed: on a single-purpose inference box
    a blind retry can stack a second job onto the first. Callers retry malformed replies freely and
    stop here, leaving the decision to a human resuming the run.
    """


class TemporalAdjudicationTruncated(NonRetryableAdjudicationError):
    """The model spent the whole completion budget thinking and emitted no content.

    Measured on BB9 2026-10-28: a 269,929-token prompt with `max_tokens` 12000 returned
    `finish_reason: length`, `reasoning_tokens: 12000` and an empty `content` twice in a row, because
    the retry loop re-issued a deterministic request. The fix is a larger budget, not another call.
    """


def request_may_still_be_running(exc):
    # type: (BaseException) -> bool
    """Whether this connection error leaves server-side work possibly in flight.

    A refused or unroutable endpoint proved nothing started, so re-asking is safe. A timeout or a
    connection that died after the request went out did not, and neither does a reply that stops
    mid-body.
    """
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return True
        return isinstance(reason, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError))
    return isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                            http.client.RemoteDisconnected, http.client.IncompleteRead,
                            http.client.BadStatusLine))


class TemporalAdjudicationIncomplete(TemporalAdjudicationError):
    """Some adjudication windows produced nothing usable; partial results were still persisted."""

    def __init__(self, message, provenance=None, review_items=None):
        # type: (str, Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]) -> None
        super().__init__(message)
        self.provenance = provenance or {}
        self.review_items = review_items or []


class TemporalAdjudicator:
    """Temporal boundary adjudicator using a local vision-language model."""

    def __init__(self, endpoint=None, model=None, max_requests=1, timeout=300, output_dir=None,
                 max_input_chars=MAX_INPUT_CHARS, window_ratio=_DEFAULT_WINDOW_RATIO,
                 overlap_observations=DEFAULT_WINDOW_OVERLAP,
                 window_attempts=_DEFAULT_WINDOW_ATTEMPTS, chunk_dir=None,
                 max_completion_tokens=DEFAULT_COMPLETION_TOKENS):
        self._endpoint = local_model.chat_completions_url(endpoint or local_model.default_endpoint())
        self._model = model or local_model.default_model()
        self._max_requests = max(1, int(max_requests))
        self._timeout = timeout
        self._output_dir = Path(output_dir) if output_dir else None
        self._max_input_chars = int(max_input_chars)
        self._window_ratio = float(window_ratio)
        self._overlap = int(overlap_observations)
        self._window_attempts = max(1, int(window_attempts))
        self._max_completion_tokens = max(1, int(max_completion_tokens))
        self._chunk_dir = Path(chunk_dir) if chunk_dir else None
        self._calls = 0
        self._usage = {"total_calls": 0, "total_tokens": 0}
        self._attempts = []  # type: List[Dict[str, Any]]

    @property
    def calls(self):
        # type: () -> int
        return self._calls

    @property
    def budget_exhausted(self):
        # type: () -> bool
        return self._calls >= self._max_requests

    @property
    def usage(self):
        # type: () -> Dict[str, Any]
        return dict(self._usage)

    def _user_content(self, obs, policy_text, window_note=""):
        # type: (List[Dict[str, Any]], str, str) -> str
        return user_content(obs, policy_text, window_note)

    def propose(self, timeline, policy_text):
        # type: (List[Dict[str, Any]], str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]
        """Propose temporal boundaries from a chronological classified timeline in one request.

        Args:
            timeline: List of dicts with at least 'timestamp', 'state', 'confidence'.
            policy_text: Cutting policy text included in the prompt context.

        Returns:
            (matches, review_items, provenance)

        Raises:
            TemporalAdjudicationError: when the timeline does not fit one bounded prompt.
                propose_windowed() is the path for timelines that cannot.
        """
        if not timeline:
            raise TemporalAdjudicationError("Empty timeline")

        obs = build_observations(timeline)
        prompt = self._user_content(obs, policy_text)

        # Bound input characters
        total_chars = len(_SYSTEM_PROMPT) + len(prompt)
        if total_chars > MAX_INPUT_CHARS:
            raise TemporalAdjudicationError(
                "Input too large: {} chars exceeds limit of {}".format(total_chars, MAX_INPUT_CHARS)
            )

        # Call model
        raw_response = self._call_model(prompt)

        # Parse
        proposals = self._parse_response(raw_response)

        # Validate
        obs_by_id = {o["observation_id"]: o for o in obs}
        obs_order = {o["observation_id"]: i for i, o in enumerate(obs)}
        validated = self._validate_proposals(proposals, obs_by_id, obs_order, obs)

        # Separate into matches and review
        matches = []  # type: List[Dict[str, Any]]
        review_items = []  # type: List[Dict[str, Any]]
        for p in validated:
            if p["disposition"] == "candidate":
                m = self._build_match(p, obs)
                matches.append(m)
                review_items.append({
                    "type": "bracket_validity_unverified",
                    "start": m["start"],
                    "end": m["end"],
                    "reason": "Temporal adjudication proposes this as a candidate set; "
                              "screen-state evidence does not verify completed bracket status.",
                    "disposition": "candidate",
                })
                association = _association_review(m)
                if association is not None:
                    review_items.append(association)
            else:
                review_items.append({
                    "type": "temporal_review",
                    "disposition": p["disposition"],
                    "start_id": p["start_id"],
                    "end_id": p["end_id"],
                    "evidence_ids": p.get("evidence_ids", []),
                    "reason": p.get("reason", ""),
                })

        # Validate no overlaps in final matches
        self._validate_no_overlap(matches)

        # Provenance
        prompt_fingerprint = hashlib.sha256(
            (_SYSTEM_PROMPT + "\n" + prompt).encode("utf-8")
        ).hexdigest()[:32]

        provenance = {
            "algorithm": _ALGORITHM,
            "prompt_fingerprint": prompt_fingerprint,
            "usage": dict(self._usage),
            "model": self._model,
            "endpoint": self._endpoint,
            "raw_response_length": len(raw_response) if raw_response else 0,
            "observations_count": len(obs),
            "proposals_count": len(validated),
            "candidate_count": len(matches),
        }

        # Save adjudication artifacts (no signed URLs, no base64)
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "algorithm": _ALGORITHM,
                "prompt_fingerprint": prompt_fingerprint,
                "usage": dict(self._usage),
                "model": self._model,
                "observations_count": len(obs),
                "proposals": validated,
                "raw_response": raw_response,
            }
            artifact_path = self._output_dir / "adjudication.json"
            artifact_path.write_text(
                json.dumps(artifacts, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8"
            )

        return matches, review_items, provenance

    def _window_note(self, window, total, first_ts, last_ts):
        # type: (int, int, float, float) -> str
        return (
            "This is adjudication window {} of {} of a longer recording, covering {:.6f} s to "
            "{:.6f} s. Observation ids are global to the recording. The first and last observation "
            "of this window may fall inside a set: a window edge is not evidence that a set starts "
            "or ends, so do not name an endpoint at the final observation unless that observation "
            "closes the set."
        ).format(window, total, first_ts, last_ts)

    def plan(self, timeline, policy_text):
        # type: (List[Dict[str, Any]], str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]
        """Return (observations, windows) sized to the prompt cap. Makes no requests.

        A timeline that already fits one window keeps the single-shot prompt bytes exactly, so
        ordinary detection behaves as it always did; only a split earns a window note.
        """
        obs = build_observations(timeline)
        worst = self._window_note(9999, 9999, 999999.999999, 999999.999999)
        windows = plan_windows(obs, policy_text, self._max_input_chars, self._window_ratio,
                               self._overlap, worst)
        for w in windows:
            w["note"] = "" if len(windows) == 1 else self._window_note(
                w["index"] + 1, len(windows), w["start_timestamp"], w["end_timestamp"])
            w["prompt_chars"] = _prompt_scaffold(policy_text, w["note"]) + sum(
                _serialized_observation(o) for o in obs[w["obs_start"]:w["obs_end"]])
            if w["prompt_chars"] > self._max_input_chars:
                raise TemporalAdjudicationError(
                    "Window {} needs {} chars, over the {} char limit".format(
                        w["index"], w["prompt_chars"], self._max_input_chars))
        return obs, windows

    @staticmethod
    def _fingerprint(model, algorithm, prompt):
        # type: (str, str, str) -> str
        return hashlib.sha256("\0".join([algorithm, model, prompt]).encode("utf-8")).hexdigest()[:32]

    def _chunk_path(self, index):
        # type: (int) -> Optional[Path]
        directory = self._chunk_dir
        if directory is None and self._output_dir is not None:
            directory = self._output_dir / "adjudication"
        if directory is None:
            return None
        return Path(directory) / "chunk-{:04d}.json".format(index)

    @staticmethod
    def _load_chunk(path, fingerprint):
        # type: (Optional[Path], str) -> Optional[Dict[str, Any]]
        """Reuse a saved chunk only when it is byte-identical in model, algorithm and prompt."""
        if path is None or not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None
        if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
            return None
        if record.get("status") != "ok" or not isinstance(record.get("proposals"), list):
            return None
        return record

    def _save_chunk(self, path, window, fingerprint, raw, proposals, status, error):
        # type: (Optional[Path], Dict[str, Any], str, str, List[Dict[str, Any]], str, Optional[str]) -> None
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "fingerprint": fingerprint,
            "algorithm": WINDOW_ALGORITHM,
            "model": self._model,
            "endpoint": self._endpoint,
            "status": status,
            "error": error,
            "window": {key: window.get(key) for key in (
                "index", "obs_start", "obs_end", "core_start", "core_end", "context_before",
                "context_after", "context_shortfall", "break_kind", "prompt_chars",
                "start_timestamp", "end_timestamp", "first_observation_id",
                "last_observation_id")},
            "calls": window.get("calls", 0),
            "attempts": [a for a in self._attempts if a.get("window") == window.get("index")],
            "raw_response": raw,
            "proposals": proposals,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(str(tmp), str(path))

    def _request_window(self, prompt, window, obs_by_id, obs_order, obs):
        # type: (str, Dict[str, Any], Dict[str, Dict], Dict[str, int], List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]], Optional[str], bool, bool]
        """Ask for one window, retrying unusable replies while the allowance lasts.

        Returns (raw, proposals, error, attempted, timed_out). A window whose reply arrived but was
        unusable is retried; one whose reply never arrived is not, because the endpoint may still be
        working on it and a second request would queue behind work nobody is waiting for.
        """
        allowed = {o["observation_id"] for o in obs[window["obs_start"]:window["obs_end"]]}
        raw = ""
        error = None
        attempted = False
        for _ in range(self._window_attempts):
            if self.budget_exhausted:
                error = "request allowance exhausted after {} of {} calls".format(
                    self._calls, self._max_requests)
                break
            attempted = True
            try:
                raw = self._call_model(prompt, window=window["index"])
                proposals = self._parse_response(raw)
            except NonRetryableAdjudicationError as exc:
                # A retry of a deterministic request buys nothing except another wait on a
                # single-purpose machine, so stop and name what has to change.
                status = "unresolved_timeout" if isinstance(exc, TemporalAdjudicationTimeout) \
                    else "unresolved_truncation"
                return raw, [], str(exc), attempted, status
            except TemporalAdjudicationError as exc:
                error = str(exc)
                continue
            self._validate_proposals(proposals, obs_by_id, obs_order, obs,
                                     allowed_ids=allowed, strict=False)
            return raw, proposals, None, attempted, None
        return raw, [], error or "no usable response", attempted, None

    def _window_calls(self, index):
        # type: (int) -> int
        return sum(1 for a in self._attempts if a.get("window") == index)

    def propose_windowed(self, timeline, policy_text):
        # type: (List[Dict[str, Any]], str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]
        """Adjudicate a timeline too large for one prompt as size-bounded, overlapping windows.

        Every window is validated against the global observation ids, so proposals from different
        windows can be compared and merged. A window that yields nothing usable is recorded as a
        failure and raises `TemporalAdjudicationIncomplete` after its partial results are saved: the
        run stays resumable instead of looking complete or discarding the calls already paid for.
        """
        obs, windows = self.plan(timeline, policy_text)
        obs_by_id = {o["observation_id"]: o for o in obs}
        obs_order = {o["observation_id"]: i for i, o in enumerate(obs)}

        results = []  # type: List[Dict[str, Any]]
        for w in windows:
            window_obs = obs[w["obs_start"]:w["obs_end"]]
            prompt = self._user_content(window_obs, policy_text, w["note"])
            w["prompt_chars"] = len(_SYSTEM_PROMPT) + len(prompt)
            fingerprint = self._fingerprint(self._model, WINDOW_ALGORITHM, prompt)
            w["fingerprint"] = fingerprint
            path = self._chunk_path(w["index"])
            cached = self._load_chunk(path, fingerprint)
            if cached is not None:
                # Reusing saved work: this run charges it no calls and invents no usage for it.
                w.update(status="resumed", calls=0, attempts=[], error=None,
                         saved_calls=cached.get("calls", 0))
                results.append({"window": w, "proposals": cached["proposals"], "failed": False})
                continue
            first_attempt = len(self._attempts)
            raw, proposals, error, attempted, unresolved = self._request_window(
                prompt, w, obs_by_id, obs_order, obs)
            w["attempts"] = self._attempts[first_attempt:]
            w["calls"] = self._window_calls(w["index"])
            if error is not None:
                w["status"] = unresolved or ("failed" if attempted else "not_attempted")
                w["error"] = error
                self._save_chunk(path, w, fingerprint, raw, proposals, w["status"], error)
                results.append({"window": w, "proposals": [], "failed": True})
                continue
            w.update(status="ok", error=None)
            self._save_chunk(path, w, fingerprint, raw, proposals, "ok", None)
            results.append({"window": w, "proposals": proposals, "failed": False})

        matches, review, reconciliation = self._reconcile(results, obs, obs_by_id, obs_order)
        review.extend(self._coverage_reviews(windows))
        review.extend(self._final_play_review(obs, matches))

        unfinished = ("failed", "not_attempted", "unresolved_timeout", "unresolved_truncation")
        failed = [w["index"] for w in windows if w["status"] in unfinished]
        timed_out = [w["index"] for w in windows if w["status"] == "unresolved_timeout"]
        truncated = [w["index"] for w in windows if w["status"] == "unresolved_truncation"]
        provenance = {
            "algorithm": WINDOW_ALGORITHM,
            "model": self._model,
            "endpoint": self._endpoint,
            "observations_count": len(obs),
            "window_count": len(windows),
            "max_input_chars": self._max_input_chars,
            "window_ratio": self._window_ratio,
            "overlap_observations": self._overlap,
            "request_allowance": self._max_requests,
            "model_calls": self._calls,
            "budget_exhausted": self.budget_exhausted,
            "resumed_windows": [w["index"] for w in windows if w["status"] == "resumed"],
            "windows": [{k: v for k, v in w.items() if k != "note"} for w in windows],
            "reconciliation": reconciliation,
            "usage": dict(self._usage),
            "attempts": list(self._attempts),
            "proposals_count": sum(len(r["proposals"]) for r in results),
            "candidate_count": len(matches),
            "failed_windows": failed,
            "timed_out_windows": timed_out,
            "truncated_windows": truncated,
            "complete": not failed,
            "status": "complete" if not failed else "incomplete",
        }
        self._write_windowed_artifact(provenance, matches, review)

        if failed:
            unattempted = [w["index"] for w in windows if w["status"] == "not_attempted"]
            detail = ""
            if unattempted:
                detail = "; {} window(s) were never attempted because the request allowance " \
                         "ran out".format(len(unattempted))
            if timed_out:
                detail += "; {} window(s) lost their connection, so the endpoint may still be " \
                          "running them — check the server before resuming".format(len(timed_out))
            if truncated:
                detail += "; {} window(s) spent the whole completion budget reasoning and emitted " \
                          "no content — change the configuration, do not just re-run".format(
                              len(truncated))
            raise TemporalAdjudicationIncomplete(
                "Adjudication incomplete: windows {} produced no usable proposals{}".format(
                    ", ".join(str(i) for i in failed), detail),
                provenance=provenance, review_items=review)
        return matches, review, provenance

    def _write_windowed_artifact(self, provenance, matches, review):
        # type: (Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]) -> None
        if self._output_dir is None:
            return
        self._output_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(provenance)
        payload["matches"] = matches
        payload["review_items"] = review
        (self._output_dir / "adjudication.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def _coverage_reviews(self, windows):
        # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
        """Report where the split itself could have cost evidence, so thin proof is never hidden."""
        items = []
        for w in windows:
            if not w["break_snapped"] and not w["is_last"]:
                items.append({
                    "type": "set_may_cross_window_boundary",
                    "window": w["index"],
                    "start": w["end_timestamp"],
                    "end": windows[w["index"] + 1]["start_timestamp"]
                    if w["index"] + 1 < len(windows) else w["end_timestamp"],
                    "context_after": w["context_after"],
                    "context_before_next": windows[w["index"] + 1]["context_before"]
                    if w["index"] + 1 < len(windows) else 0,
                    "disposition": "uncertain",
                    "reason": "No visible set boundary fell near this window split, so a set running "
                              "across it may have been seen whole by neither window. Compare the two "
                              "windows' proposals before trusting play on either side of this time.",
                })
            if w["context_shortfall"]:
                items.append({
                    "type": "window_context_truncated",
                    "window": w["index"],
                    "start": w["start_timestamp"],
                    "end": w["end_timestamp"],
                    "context_before": w["context_before"],
                    "context_after": w["context_after"],
                    "wanted_context_before": w["wanted_context_before"],
                    "wanted_context_after": w["wanted_context_after"],
                    "disposition": "uncertain",
                    "reason": "The prompt cap left {} of {} wanted context observations out of this "
                              "window; a set crossing its edges may be reported uncertain.".format(
                                  w["context_shortfall"],
                                  w["wanted_context_before"] + w["wanted_context_after"]),
                })
        return items

    def _unproposed_play(self, obs, spans, obs_order):
        # type: (List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]) -> List[Dict[str, Any]]
        """Confident play that no proposal mentioned at all, so the run cannot call it adjudicated.

        A model that returns five sets and two uncertain spans can still skip a set outright, and on
        the BB9 recovery it did exactly that: the Winners Final at 3,923.9-4,334.3 s (Mattiniero 3-0
        Spitfire2929, confirmed on cached frames) appeared in no proposal while the run reported
        ``complete``. Absence of a flag is not evidence of absence of play, so unmentioned play is
        flagged here rather than silently dropped.
        """
        covered = [(obs_order[s["start_id"]], obs_order[s["end_id"]]) for s in spans]
        # Uncovered confident play, grouped into stretches. Grouping by adjacency rather than by
        # "runs of gameplay" matters: menus and round breaks sit inside sets too, and a run-based
        # scan happily merges an omitted set with the play that proposed around it.
        outside = [i for i, row in enumerate(obs)
                   if row["state"] == "gameplay" and _confident(row)
                   and not any(a <= i <= b for a, b in covered)]
        runs = []  # type: List[List[int]]
        for i in outside:
            if runs and obs[i]["timestamp"] - obs[runs[-1][-1]]["timestamp"] <= _PLAY_GAP_SECONDS:
                runs[-1].append(i)
            else:
                runs.append([i])

        items = []
        for indices in runs:
            first, last = indices[0], indices[-1]
            pairings = []
            for i in indices:
                pair = tuple(sorted(filter(None, (_normalise_name(obs[i].get("player1")),
                                                  _normalise_name(obs[i].get("player2"))))))
                if len(pair) == 2 and pair not in pairings:
                    pairings.append(pair)
            items.append({
                "type": "unproposed_play",
                "start": obs[first]["timestamp"],
                "end": obs[last]["timestamp"],
                "observation_ids": [obs[first]["observation_id"], obs[last]["observation_id"]],
                "gameplay_observations": len(indices),
                "pairings": [list(p) for p in pairings[:4]],
                "disposition": "uncertain",
                "reason": "Confident gameplay here appears in no proposal at all, so nothing decided "
                          "whether it is a set. A set can begin with play when the stage vote was "
                          "never captured, which is how the missed BB9 Winners Final started; "
                          "inspect the cached frames here before treating the run as covering the "
                          "recording.",
            })
        return items

    def _final_play_review(self, obs, matches):
        # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> List[Dict[str, Any]]
        """Play observed after the last accepted endpoint stays unbounded rather than assumed over."""
        if not obs:
            return []
        last = obs[-1]
        tail_start = matches[-1]["end"] if matches else None
        gameplay = [o for o in obs if o["state"] == "gameplay"]
        if not gameplay:
            return []
        latest = gameplay[-1]
        if tail_start is not None and latest["timestamp"] <= tail_start:
            return []
        closed = any(o["state"] == "controller_prompt" and o["timestamp"] >= latest["timestamp"]
                     for o in obs)
        if closed:
            return []
        return [{
            "type": "unterminated_final_play",
            "start": tail_start if tail_start is not None else latest["timestamp"],
            "end": last["timestamp"],
            "disposition": "uncertain",
            "reason": "Classified gameplay continues to the end of the saved timeline without a "
                      "controller prompt, so the last set has no observed endpoint. The recording "
                      "may be truncated or the set may still be running; no end is invented.",
        }]

    def _reconcile(self, results, obs, obs_by_id, obs_order):
        # type: (List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict], Dict[str, int]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]
        """Merge per-window proposals into one chronology, recording what it cannot settle.

        Ownership is the anchored global start id: at most one endpoint is adopted per start, so an
        agreement found in two windows is counted once and a disagreement is never resolved by
        picking a winner without evidence. Endpoints proposed at the last observation a window could
        see are treated as unverified, because the true end may simply have been out of view.
        """
        windows_by_index = {r["window"]["index"]: r["window"] for r in results}
        review = []  # type: List[Dict[str, Any]]
        spans = {}   # type: Dict[Tuple[str, str, str], Dict[str, Any]]
        rejected = 0
        for r in results:
            w = r["window"]
            if r["failed"]:
                review.append({
                    "type": "adjudication_window_failed",
                    "window": w["index"],
                    "start": w["start_timestamp"],
                    "end": w["end_timestamp"],
                    "core_observation_ids": [w["first_observation_id"], w["last_observation_id"]],
                    "disposition": "uncertain",
                    "reason": "Adjudication window {} ({}): {}. Its core observations were never "
                              "adjudicated.".format(w["index"], w.get("status", "failed"),
                                                     w.get("error", "")),
                })
                continue
            for p in r["proposals"]:
                if p.get("rejection"):
                    rejected += 1
                    review.append({
                        "type": "rejected_proposal",
                        "window": w["index"],
                        "disposition": "uncertain",
                        "start_id": p.get("start_id"),
                        "end_id": p.get("end_id"),
                        "reason": p["rejection"],
                    })
                    continue
                key = (p["start_id"], p["end_id"], p["disposition"])
                entry = spans.setdefault(key, {
                    "start_id": p["start_id"], "end_id": p["end_id"],
                    "disposition": p["disposition"], "windows": [], "evidence_ids": [], "reasons": []})
                entry["windows"].append(w["index"])
                merged = set(entry["evidence_ids"]) | set(p.get("evidence_ids", []))
                entry["evidence_ids"] = sorted(merged, key=lambda oid: obs_order[oid])
                entry["reasons"].append({"window": w["index"], "reason": p.get("reason", "")})

        for entry in spans.values():
            end_index = obs_order[entry["end_id"]]
            # Ending on the last observation a window could see is only suspicious when that window
            # stopped before the data did; a cut at the end of the timeline has nothing further to
            # hide behind.
            reached_end_of_data = any(windows_by_index[i]["obs_end"] >= len(obs)
                                      for i in entry["windows"])
            entry["truncated_at_edge"] = end_index < len(obs) - 1 and not reached_end_of_data and all(
                end_index >= windows_by_index[i]["obs_end"] - 1 for i in entry["windows"])

        groups = {}  # type: Dict[str, List[Dict[str, Any]]]
        for entry in spans.values():
            groups.setdefault(entry["start_id"], []).append(entry)

        adopted = []  # type: List[Dict[str, Any]]
        conflicts = 0
        unverified_edges = 0
        for start_id in sorted(groups, key=lambda oid: obs_order[oid]):
            entries = groups[start_id]
            candidates = [e for e in entries if e["disposition"] == "candidate"]
            settled = [e for e in candidates if not e["truncated_at_edge"]]
            pool = settled or candidates
            if candidates and not settled:
                unverified_edges += 1
                review.append({
                    "type": "window_edge_endpoint_unverified",
                    "start_id": start_id,
                    "start": obs_by_id[start_id]["timestamp"],
                    "disposition": "uncertain",
                    "windows": sorted({i for e in candidates for i in e["windows"]}),
                    "candidates": [{"end_id": e["end_id"],
                                    "end": obs_by_id[e["end_id"]]["timestamp"],
                                    "windows": e["windows"]} for e in candidates],
                    "reason": "Every proposal for this start ended at the last observation its "
                              "window could see, so the set may continue past it. No endpoint is "
                              "adopted; widen the window overlap or supply more context.",
                })
                others = [e for e in entries if e["disposition"] != "candidate"]
                adopted.extend(others)
                continue
            if not pool:
                adopted.extend(entries)
                continue
            # Two windows can look at one span and disagree about whether it is a set at all. The
            # optimistic reading must not silently win that argument.
            disputed = [o for o in entries if o["disposition"] != "candidate"
                        and any(o["end_id"] == c["end_id"] for c in pool)]
            if disputed:
                conflicts += 1
                review.append({
                    "type": "conflicting_dispositions",
                    "start_id": start_id,
                    "start": obs_by_id[start_id]["timestamp"],
                    "candidates": [{"end_id": e["end_id"], "end": obs_by_id[e["end_id"]]["timestamp"],
                                    "disposition": e["disposition"], "windows": e["windows"],
                                    "reasons": e["reasons"]}
                                   for e in entries if e["end_id"] in
                                   {d["end_id"] for d in disputed}],
                    "disposition": "uncertain",
                    "reason": "Adjudication windows gave the same span both a candidate and a "
                              "non-candidate disposition; it is kept for review instead of "
                              "publishing the optimistic reading.",
                })
                adopted.extend(disputed)
                continue
            support = max(len(e["windows"]) for e in pool)
            tied = [e for e in pool if len(e["windows"]) == support]
            if len({e["end_id"] for e in tied}) > 1:
                conflicts += 1
                review.append({
                    "type": "conflicting_boundaries",
                    "start_id": start_id,
                    "start": obs_by_id[start_id]["timestamp"],
                    "disposition": "uncertain",
                    "candidates": [{"end_id": e["end_id"],
                                    "end": obs_by_id[e["end_id"]]["timestamp"],
                                    "windows": e["windows"], "reasons": e["reasons"]}
                                   for e in sorted(tied, key=lambda x: obs_order[x["end_id"]])],
                    "reason": "Adjudication windows gave this start {} endpoints with equal support; "
                              "picking one would be a guess, so both are kept for review.".format(
                                  len({e["end_id"] for e in tied})),
                })
                continue
            chosen = tied[0]
            adopted.append(chosen)
            for other in pool:
                if other is not chosen:
                    review.append({
                        "type": "superseded_endpoint",
                        "start_id": start_id,
                        "end_id": other["end_id"],
                        "end": obs_by_id[other["end_id"]]["timestamp"],
                        "disposition": "uncertain",
                        "windows": other["windows"],
                        "adopted_end_id": chosen["end_id"],
                        "reason": "A window proposed this shorter endpoint with less support; the "
                                  "better-supported endpoint was adopted instead.",
                    })

        # Global revalidation: ids, states, chronology, observed play and non-overlap.
        usable = []  # type: List[Dict[str, Any]]
        for entry in adopted:
            errors = _proposal_errors(dict(entry, evidence_ids=entry["evidence_ids"]),
                                      len(usable), obs_by_id, obs_order, obs)
            if errors:
                review.append({"type": "rejected_proposal", "disposition": "uncertain",
                               "start_id": entry["start_id"], "end_id": entry["end_id"],
                               "reason": "; ".join(errors)})
                continue
            usable.append(entry)

        usable.sort(key=lambda e: obs_order[e["start_id"]])
        # Anything that was proposed, adopted or withheld still counts as examined play.
        examined = list(spans.values())
        dropped = set()
        for i in range(1, len(usable)):
            if obs_order[usable[i]["start_id"]] <= obs_order[usable[i - 1]["end_id"]]:
                conflicts += 1
                for j in (i, i - 1):
                    dropped.add(id(usable[j]))
                review.append({
                    "type": "conflicting_candidates",
                    "disposition": "uncertain",
                    "first": {"start_id": usable[i - 1]["start_id"], "end_id": usable[i - 1]["end_id"],
                              "windows": usable[i - 1]["windows"]},
                    "second": {"start_id": usable[i]["start_id"], "end_id": usable[i]["end_id"],
                               "windows": usable[i]["windows"]},
                    "reason": "Two adopted proposals overlap, so at least one window misread the "
                              "boundary; neither is published as a set.",
                })
        usable = [e for e in usable if id(e) not in dropped]

        matches = []  # type: List[Dict[str, Any]]
        for entry in usable:
            if entry["disposition"] != "candidate":
                review.append({"type": "temporal_review", "disposition": entry["disposition"],
                               "start_id": entry["start_id"], "end_id": entry["end_id"],
                               "start": obs_by_id[entry["start_id"]]["timestamp"],
                               "end": obs_by_id[entry["end_id"]]["timestamp"],
                               "evidence_ids": entry["evidence_ids"],
                               "windows": entry["windows"],
                               "reason": " | ".join(r["reason"] for r in entry["reasons"] if r["reason"])})
                continue
            match = self._build_match(entry, obs)
            match["adjudication_windows"] = entry["windows"]
            match["evidence_ids"] = entry["evidence_ids"]
            matches.append(match)
            review.append({
                "type": "bracket_validity_unverified",
                "start": match["start"], "end": match["end"],
                "reason": "Temporal adjudication proposes this as a candidate set; "
                          "screen-state evidence does not verify completed bracket status.",
                "disposition": "candidate",
            })
            association = _association_review(match)
            if association is not None:
                review.append(association)

        self._validate_no_overlap(matches)
        unproposed = self._unproposed_play(obs, examined, obs_order)
        review.extend(unproposed)
        return matches, review, {
            "spans": len(spans),
            "starts": len(groups),
            "adopted": len(matches),
            "conflicts": conflicts,
            "edge_unverified_starts": unverified_edges,
            "rejected_proposals": rejected,
            "unproposed_play_gaps": len(unproposed),
            "unproposed_play_rows": sum(i["gameplay_observations"] for i in unproposed),
        }

    def _call_model(self, prompt, window=None):
        # type: (str, Optional[int]) -> str
        if self._calls >= self._max_requests:
            raise TemporalAdjudicationError("Model request budget exhausted ({} max)".format(self._max_requests))
        self._calls += 1

        payload = {
            "model": self._model,
            "reasoning_effort": "low",
            "max_tokens": self._max_completion_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + os.environ.get("QWEN_API_KEY", "local")},
            method="POST",
        )
        attempt = {"call": self._calls, "window": window, "prompt_chars": len(prompt),
                   "ok": False, "tokens": 0, "error": None,
                   "completion_budget": self._max_completion_tokens}
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            attempt["error"] = "Model endpoint HTTP error {} {}".format(exc.code, exc.reason)
            self._record_attempt(attempt)
            raise TemporalAdjudicationError(attempt["error"])
        except urllib.error.URLError as exc:
            if request_may_still_be_running(exc):
                attempt["error"] = "Model connection lost after the request was sent: {}".format(
                    exc.reason)
                self._record_attempt(attempt)
                raise TemporalAdjudicationTimeout(
                    "{} — the endpoint may still be working on this request, so it is not retried "
                    "automatically; check the server before resuming.".format(attempt["error"]))
            attempt["error"] = "Model endpoint unreachable: {}".format(exc.reason)
            self._record_attempt(attempt)
            raise TemporalAdjudicationError(attempt["error"])
        except (json.JSONDecodeError, UnicodeDecodeError):
            attempt["error"] = "Model returned invalid JSON response"
            self._record_attempt(attempt)
            raise TemporalAdjudicationError(attempt["error"])
        except OSError as exc:
            if request_may_still_be_running(exc):
                attempt["error"] = "Model connection lost mid-request: {}".format(exc)
                self._record_attempt(attempt)
                raise TemporalAdjudicationTimeout(
                    "{} — the endpoint may still be working on this request, so it is not retried "
                    "automatically; check the server before resuming.".format(attempt["error"]))
            attempt["error"] = "Model endpoint connection error: {}".format(exc)
            self._record_attempt(attempt)
            raise TemporalAdjudicationError(attempt["error"])

        # Preserve received output even if content extraction or proposal parsing fails. The attempt
        # number is in the name: two calls on one window that overwrite each other erase the evidence
        # that the failure was deterministic.
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            if window is None:
                name = 'adjudication-response.json' if self._calls <= 1 \
                    else 'adjudication-response-{:02d}.json'.format(self._calls)
            else:
                name = 'adjudication-response-{:04d}-call-{:02d}.json'.format(window, self._calls)
            (self._output_dir / name).write_text(
                json.dumps(data, indent=2), encoding='utf-8')

        # Extract content from response
        content = ""
        finish_reason = None
        reasoning = ""
        if isinstance(data, dict):
            choices = data.get("choices", [])
            if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                choice = choices[0]
                msg = choice.get("message", {}) if isinstance(choice.get("message"), dict) else {}
                content = msg.get("content", "") if isinstance(msg.get("content"), str) else ""
                reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
                finish_reason = choice.get("finish_reason")
            if not content:
                content = data.get("content", data.get("text", ""))
        elif isinstance(data, list):
            content = str(data[0]) if data else ""
        else:
            content = str(data)

        # Track usage: every attempted request is counted and every reply's tokens are added,
        # so a multi-window run reports what it actually cost rather than its last response.
        tokens = 0
        completion_tokens = 0
        if isinstance(data, dict) and isinstance(data.get("usage"), dict):
            reported = data["usage"].get("total_tokens", 0)
            if isinstance(reported, int) and not isinstance(reported, bool) and reported >= 0:
                tokens = reported
            detail = data["usage"].get("completion_tokens_details")
            reported_completion = data["usage"].get("completion_tokens", 0)
            if isinstance(reported_completion, int) and not isinstance(reported_completion, bool):
                completion_tokens = reported_completion
            if isinstance(detail, dict) and isinstance(detail.get("reasoning_tokens"), int):
                attempt["reasoning_tokens"] = detail["reasoning_tokens"]
        attempt.update(ok=False, tokens=tokens, finish_reason=finish_reason,
                       completion_tokens=completion_tokens,
                       reasoning_chars=len(reasoning or ""), prompt_tokens=(
                           data.get("usage") or {}).get("prompt_tokens")
                           if isinstance(data, dict) else None)
        if not content or not content.strip():
            attempt["error"] = "Model returned empty response"
            self._record_attempt(attempt)
            if finish_reason == "length":
                raise TemporalAdjudicationTruncated(
                    "Model spent its whole completion budget reasoning "
                    "({reasoning} reasoning token(s) of {budget}, finish_reason=length) and emitted "
                    "no content. Repeating the request cannot change that at temperature 0 — raise "
                    "--adjudication-max-tokens or lower --max-input-chars to shrink each window."
                    .format(reasoning=attempt.get("reasoning_tokens", completion_tokens),
                            budget=self._max_completion_tokens))
            raise TemporalAdjudicationError(attempt["error"])

        attempt.update(ok=True, response_chars=len(content))
        self._record_attempt(attempt)
        return content

    def _record_attempt(self, attempt):
        # type: (Dict[str, Any]) -> None
        self._attempts.append(dict(attempt))
        self._usage["total_calls"] = self._calls
        self._usage["total_tokens"] = self._usage.get("total_tokens", 0) + attempt.get("tokens", 0)

    def _parse_response(self, raw):
        # type: (str) -> List[Dict[str, Any]]
        text = raw.strip()
        # Strip markdown code fences if the model added them despite instructions
        if text.startswith("```"):
            lines = text.split("\n")
            end_idx = len(lines) - 1
            for i in range(1, len(lines)):
                if lines[i].strip() == "```":
                    end_idx = i
                    break
            text = "\n".join(lines[1:end_idx])
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise TemporalAdjudicationError(
                "Model response is not valid JSON: {}...".format(text[:200])
            )

        if not isinstance(data, list):
            raise TemporalAdjudicationError(
                "Expected JSON array of proposals, got {}".format(type(data).__name__)
            )

        proposals = []  # type: List[Dict[str, Any]]
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise TemporalAdjudicationError(
                    "Proposal index {} is not a JSON object".format(i)
                )
            required = {"start_id", "end_id", "disposition", "reason"}
            missing = required - set(item.keys())
            if missing:
                raise TemporalAdjudicationError(
                    "Proposal index {} missing required fields: {}".format(
                        i, ", ".join(sorted(missing))
                    )
                )
            disp = item["disposition"]
            if disp not in ("candidate", "uncertain", "aborted"):
                raise TemporalAdjudicationError(
                    "Proposal index {} has invalid disposition '{}'".format(i, disp)
                )
            ev_ids = item.get("evidence_ids", [])
            if not isinstance(ev_ids, list):
                raise TemporalAdjudicationError(
                    "Proposal index {} evidence_ids must be a list".format(i)
                )
            proposals.append({
                "start_id": str(item["start_id"]),
                "end_id": str(item["end_id"]),
                "evidence_ids": [str(x) for x in ev_ids],
                "disposition": disp,
                "reason": str(item["reason"]),
            })

        return proposals

    def _validate_proposals(self, proposals, obs_by_id, obs_order, obs, allowed_ids=None, strict=True):
        # type: (List[Dict[str, Any]], Dict[str, Dict], Dict[str, int], List[Dict[str, Any]], Optional[set], bool) -> List[Dict[str, Any]]
        """Check, anchor and downgrade proposals.

        strict=True keeps the v1 contract: the first unusable proposal aborts the call. The windowed
        path passes strict=False so one bad reply cannot invalidate every other window; a rejected
        proposal stays in the list carrying a "rejection" reason, and its overlap decision is left to
        reconciliation instead of raising.
        """
        for i, p in enumerate(proposals):
            errors = _proposal_errors(p, i, obs_by_id, obs_order, obs, allowed_ids)
            if errors:
                if strict:
                    raise TemporalAdjudicationError(errors[0])
                p["rejection"] = errors[0]
                continue
            _anchor_to_episode_start(p, obs_by_id, obs_order, obs)
            _downgrade_repeated_zero_scores(p, obs_by_id, obs_order, obs)

        if strict:
            candidates = [p for p in proposals if p["disposition"] == "candidate"]
            candidates.sort(key=lambda p: obs_order[p["start_id"]])
            for i in range(1, len(candidates)):
                prev_end_order = obs_order[candidates[i - 1]["end_id"]]
                curr_start_order = obs_order[candidates[i]["start_id"]]
                if curr_start_order <= prev_end_order:
                    raise TemporalAdjudicationError(
                        "Candidate proposals overlap: end_id '{}' (order {}) vs start_id '{}' (order {})".format(
                            candidates[i - 1]["end_id"], prev_end_order,
                            candidates[i]["start_id"], curr_start_order
                        )
                    )
        return proposals

    def _build_match(self, proposal, obs):
        # type: (Dict[str, Any], List[Dict[str, Any]]) -> Dict[str, Any]
        start_idx = next(i for i, o in enumerate(obs) if o["observation_id"] == proposal["start_id"])
        end_idx = next(i for i, o in enumerate(obs) if o["observation_id"] == proposal["end_id"])

        segment = obs[start_idx:end_idx + 1]
        metadata = _gameplay_metadata(segment)
        association = dict(metadata.get("association") or {})
        p1, p2 = metadata.get("player1", ""), metadata.get("player2", "")
        c1, c2 = metadata.get("character1", ""), metadata.get("character2", "")

        if not (p1 and p2):
            # No confident gameplay header named both players for this span, so fall back to the
            # first value seen anywhere in it. That can be a setup grid or the next player's overlay,
            # and its slot order is not this set's pairing, so the record says it is unverified.
            p1 = p1 or next((o.get("player1") or "" for o in segment if o.get("player1")), "")
            p2 = p2 or next((o.get("player2") or "" for o in segment if o.get("player2")), "")
            c1 = c1 or next((o.get("character1") or "" for o in segment if o.get("character1")), "")
            c2 = c2 or next((o.get("character2") or "" for o in segment if o.get("character2")), "")
            association = {"basis": "span_fallback", "verified": False}
        elif not (c1 and c2):
            # A character the header was never read beside has no trustworthy slot either: leaving it
            # blank keeps a real name from being published with somebody else's character.
            association["unbound_slots"] = [
                slot for slot, value in (("character1", c1), ("character2", c2)) if not value]

        return {
            "start": obs[start_idx]["timestamp"],
            "end": obs[end_idx]["timestamp"],
            "player1": p1,
            "player2": p2,
            "character1": c1,
            "character2": c2,
            "round_name": "",
            "notes": proposal.get("reason", ""),
            "metadata_association": association,
            # Which edges rest on a preferred screen and which were inferred from play alone. Without
            # these a partial set and a fully evidenced one are indistinguishable downstream, which is
            # how a missing grid got read as a missing match.
            "partial": proposal.get("partial", []),
            "start_completeness": proposal.get("start_completeness", "inferred"),
            "end_completeness": proposal.get("end_completeness", "inferred"),
            "edge_contract": proposal.get("contract", boundary_contract.CONTRACT_VERSION),
        }

    def _validate_no_overlap(self, matches):
        # type: (List[Dict[str, Any]]) -> None
        if len(matches) <= 1:
            return
        sorted_matches = sorted(matches, key=lambda m: m["start"])
        for i in range(1, len(sorted_matches)):
            if sorted_matches[i]["start"] < sorted_matches[i - 1]["end"]:
                raise TemporalAdjudicationError(
                    "Match {} (start={}) overlaps with match {} (end={})".format(
                        i, sorted_matches[i]["start"], i - 1, sorted_matches[i - 1]["end"]
                    )
                )
