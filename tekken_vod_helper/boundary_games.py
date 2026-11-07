"""Recover games from what the game itself draws, ignoring the broadcast overlay.

A Tekken 8 bracket set is a hierarchy: a **round** is one fight, a **game** is the first side to win
``rounds_per_game`` rounds, and a **set** is the first side to win ``games_per_set`` games. The word
"match" is avoided everywhere here because it names all three levels depending on who is speaking.

The recording's own overlay is useless as a boundary source. Measured on BB9, the broadcast header
changed the player names *and* the score box in the middle of a live round three times (round clock
43 -> 42, 52 -> 47, 49 -> 45 with the same stage, same characters and health already spent), so a name
update can neither open nor close anything, and neither can a score box. What the game draws cannot
lie in the same way, because it is part of the rendered fight:

* the **round clock** counts down once per second inside a round and jumps back to full when the next
  round starts, so a monotonic countdown proves continuity and a reset proves a round boundary;
* a **ROUND n / FINAL ROUND** callout opens a round, **K.O.** and **TIME UP** close one, and a victory
  pose or result transition closes a game;
* **win dots** fill as rounds are won, which is what distinguishes game three of a set from a rematch.

Observations (what was seen) are kept separate from events (what the sequence means), and both are
kept separate from the broadcast overlay, which is recorded only so a reviewer can see that it was
contradicted. Anything the frames in hand cannot settle stays an explicit unknown with an entry in the
investigation queue: an unobserved round end is *unresolved*, never silently rounded off to complete.
"""
import copy
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from . import boundary_bracket as bracket_context
from . import boundary_format
from . import boundary_names as names

SCHEMA_VERSION = "games-v1"

# Tekken 8 default: a round is 60 seconds of fight time. A reading is treated as a fresh round when
# it goes *up* by more than the clock can tick between two observations, so a 5 s sampling interval
# cannot invent a boundary out of rounding.
ROUND_CLOCK_START = 60
ROUND_TICK_TOLERANCE = 2
# A round's own clock is its ceiling, plus the K.O. freeze, the victory pose and any replay the
# broadcast shows before the next round starts. Longer than this and the span holds more than one
# round whether a reset was read or not.
ROUND_CAP_SLACK = 30
# How long a round must already have been open before a ROUND callout is taken as a new round
# starting. The announcement holds on screen for several one-second frames, and each of them saying
# the same thing is one event, not a round each.
ROUND_CALL_MIN_SECONDS = 10.0
# The floor for a span to be a round at all: the ROUND callout, the FIGHT banner and one exchange take
# longer than this together, so anything shorter is a transition read twice.
MIN_ROUND_SECONDS = 5.0
# How much unwatched tape after a game's last readable clock still counts as a bounded end. The clock is
# the finest evidence the recording offers for a conclusion, so an end resting on it is a bound rather
# than a timestamp; past this the boundary is reported uncertain instead of quiet.
END_BOUND_UNCERTAIN_SECONDS = 30.0
# A clip has to contain the read that names the winner. It does not have to reach the next menu: after a
# conclusion the broadcast holds the camera on the players for minutes, and padding to that would make
# every clip a third of a match.
CLIP_PAD_SECONDS = 2.0
# How far from a suspect win-dot reading its supporting readings have to sit before the suspect can be
# called a misread. Rounds are the unit because a round is bounded by its own clock, so an adjacent
# round also bounds how far in time a resolution may reach.
DOT_SUPPORT_ROUND_GAP = 1
# Readings one side may have resolved inside a single game before the disagreement stops looking like
# one bad frame. A row that contradicts itself twice has no local fact left to trust it by, so the
# second fall is reported and the game stays contested.
MAX_RESOLVED_READS_PER_SIDE = 1
#: How long a round's own result sequence runs, for the purpose of *finding* a trailing win-dot fall that
#: may deserve the HUD-departure exception. It bounds the candidate; it does not excuse it -- see
#: ``_hud_departure_corroboration``.
#: Measured on the executed unseen trial: the two readings that invented pips landed 2.002 s and
#: 4.004 s after the K.O. that closed their round, and the next screen the recording showed was a
#: controller prompt 6.006 s after it. Bounding the excuse is the whole difference between this rule and
#: the maximum-of-everything bug it must not become: a fall 40 s after a round closed is not a victory
#: pose, and the existing rule has to stay free to contest the game over it.
RESULT_SEQUENCE_SECONDS = 5.0
# A "FINAL ROUND" callout means both sides already have rounds_per_game - 1 wins; it is a start
# signal for a round and a certainty signal for the game ending right after it.
DEFAULT_ROUNDS_PER_GAME = 3
DEFAULT_GAMES_PER_SET = 3

_HUD_ABSENT_STATES = frozenset(["stage_select", "character_select", "controller_prompt",
                                "controller_assignment", "post_match_menu", "controller_disconnected",
                                "unknown"])
_GAME_OPENING_STATES = frozenset(["stage_select", "stage_intro", "character_select", "vs_screen"])
_GAME_CLOSING_STATES = frozenset(["post_match_menu", "controller_prompt", "controller_assignment"])

# What a screen between two games says about whether they are one set. The policy exists because both
# obvious rules are wrong: treating stage or character selection as a boundary cuts every set after its
# first game (stages are picked between games of a set), while ignoring the screens merges unrelated
# matches -- it fused ``derpingtoast``/``Ariz``'s game with three of ``Buk``/``Rebix``'s. Nothing here is
# sufficient or mandatory on its own: ``controller_prompt`` is *preferred* rather than categorically
# neutral, and a preferred cue still only counts when something else agrees with it.
SET_CUE_WEIGHT = {
    "controller_assignment": "preferred", "controller_disconnected": "preferred",
    "controller_prompt": "preferred",
    "post_match_menu": "corroborating", "character_select": "corroborating",
    "lobby": "corroborating", "rounds_select": "corroborating", "mode_select": "corroborating",
    # Chosen between games of the same set, so a cut on these would end every set at its first game.
    "stage_select": "neutral", "stage_intro": "neutral", "vs_screen": "neutral",
    "round_end": "neutral", "gameplay": "neutral", "unknown": "neutral",
}
# Nameplate agreement is not identity. Measured on this recording: **21 nameplate changes happen while the
# round clock keeps falling** -- mid-round -- including Rebix/RebiX and ``PWDDA | Ariz``/``PWDDA | Az``
# flicker, and inside Buk/Rebix's own match the header named Ariz/Mattiniero on 161 of 296 gameplay rows.
# So a name may corroborate a cut and may never cause one alone, and a higher vote share buys confidence
# rather than correctness: the threshold below only decides whether a pairing hypothesis is worth carrying
# forward as a mapping question.
NAME_DOMINANT_SHARE = 0.80

#: Mapping-question outcomes, named once because three different callers have to agree on which ones
#: count as "we know who sat where" and which ones are still a question about the footage.
SETTLED_MAPPING_STATUSES = ("swap_confirmed", "pairing_change_confirmed", "steady_confirmed")
OPEN_MAPPING_STATUSES = ("uncovered", "contested")
# Dwell length is an investigation trigger, never a boundary. Inside one set on this recording the
# between-game dwell is 20.0 s and 26.0 s; a dwell past a full round clock is worth asking about.
SET_GAME_GAP_LIMIT = ROUND_CLOCK_START + ROUND_CAP_SLACK
# The three verdicts a link can carry. ``separated`` needs a preferred cue plus corroboration; ``joined``
# is what the format predicts when nothing argues against it; ``ambiguous`` keeps BOTH readings alive as
# candidate groupings, which is the only honest output when the evidence points two ways.
LINK_VERDICTS = ("joined", "separated", "ambiguous")

_CLOCK_RE = re.compile(r"\b(?:timer|clock)\b[^.;,]{0,20}?\b(\d{1,2})\b", re.I)
# The same clock when the prose puts the number *ahead* of its word: "29s timer", "60-second timer",
# "11s round timer". Measured on BB9, 34 gameplay rows state a clock this way and the forward pattern reads
# none of them, which bounded one round 16 s early and left three rows of live play inside no game.
# Deliberately narrow, because the number has to be a seconds count: the unit is required, it must sit
# within a short span *before* the word timer/clock, and a value above the round's full time is refused by
# the caller as it always was. Without the unit this pattern would read "1-1" and "24 DAMAGE" as clocks.
# A negated clause is refused here too: "no 30s timer" is a row saying the clock was unreadable, not a 30.
_CLOCK_BACKWARDS_RE = re.compile(
    r"\b(\d{1,2})\s*-?\s*(?:s|secs?|seconds?)\b[^.;,]{0,14}?\b(?:round\s+|match\s+)?(?:timer|clock)\b", re.I)
# A game's own round number, not a fixed 1-3: first-to-three reaches a fourth round at 2-1 and a
# fifth at 2-2, and a schema that stops at 3 forces the reader to mislabel ROUND 4 as something else.
_ROUND_CALL_RE = re.compile(r"\bROUND\s*([1-9])\b|\bFINAL\s*ROUND\b", re.I)
# The banners Tekken draws as a round begins. Matched **case-sensitively** on purpose: the game draws
# them in capitals and the classifier quotes what is on screen, while the same words read
# case-insensitively describe a round already being fought. Measured over the 2,000 classified BB9
# rows, the capitals appear on 43 rows and every one of them sits within 6 s of a clock-driven round
# opening; case-insensitive matching finds 190 rows ("Active fight with timer 46", "Mid-fight frame"),
# which are prose about fighting, not a banner.
_ROUND_OPEN_RE = re.compile(r"\b(READY|FIGHT)\b")
#: Start bases that say this span *is* a round's opener: the game drew its own start signal and no clock
#: was ever read inside the span. That shape is not a very short round, it is the next round's opener
#: sampled in the second before its digits appeared -- see :func:`_is_callout_sliver`.
OPENING_SIGNATURE_BASES = ("round_callout", "round_open_cue")
_KO_RE = re.compile(r"\bK\.?\s?O\.?\b", re.I)
_TIME_UP_RE = re.compile(r"\bTIME\s*UP\b|\bTime\s*Up\b|\bTIME\s*OVER\b", re.I)
# What the game drew when it was not K.O. or TIME UP: a row that says the round ended. Measured across
# all 2019 classified BB9 rows this matches 52 times and every match describes a concluded round
# ("Round-ending PERFECT banner", "Post-KO round-end pose", "End-of-round victory close-up"). The banner
# word alone does **not** qualify: 6 of the 14 rows saying PERFECT are the hit-feedback tag ("'PERFECT'
# hit banner ... during active round"), which is why the pattern is anchored on the round-end phrase and
# not on the callout. Missing this family is what left the executed trial's first game with no end event
# at all -- its concluding row reads "Round-ending PERFECT banner over active stage, timer 35" at
# 1371.37 s, and the boundary fell back to a clock bound 216 s wide instead.
_ROUND_END_RE = re.compile(r"\bround[- ]?(?:end|ending|ended|over|won|victory)\b"
                           r"|\bend of (?:the )?round\b", re.I)
_RESULT_RE = re.compile(r"\bYOU WIN\b|\bVICTORY\b|\bVictory[- ]pose\b|\bwin banner\b|\bKO camera\b", re.I)
_WIN_DOT_RE = re.compile(r"\bWIN\s*(\d)\b|(\d)\s*(?:win\s*)?dots?\b", re.I)
_HUD_RE = re.compile(r"health bar|in-?fight|arena|fighters?\b", re.I)


def _finite(value):
    # type: (Any) -> bool
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _seconds_text(value):
    # type: (Optional[float]) -> str
    """A timestamp for prose. A reading with no timestamp says so rather than printing None."""
    return "%s s" % round(value, 3) if _finite(value) else "undated"


_NEGATION_CUE = re.compile(r"\b(?:no|not|never|without|absent|nor|neither|lacks?|"
                           r"isn'?t|aren'?t|cannot|can'?t|none of)\b", re.I)
# How far back a negating word can still govern the phrase it precedes. Prose is provisional
# evidence, so this only ever *removes* a claim rather than creating one: a cue within the window
# means the writer may have said the banner is absent, and an absent banner is not a round end.
_NEGATION_WINDOW = 32
# A negation stops at the end of its own clause. "Broadcast scoreboard still shows 0-0 (set **not**
# updated); **round-end** victory pose" asserts one thing and denies another, and a 32-character window
# that crosses the ")" and the ";" read the denial of the scoreboard as a denial of the round end --
# deleting the row that concluded the second accepted game and leaving its boundary to a clock bound.
# A period is deliberately **not** a boundary here: "No K.O. or TIME UP banner" is one denial of two
# banners and the abbreviation's own full stop sits inside it.
_CLAUSE_BOUNDARY = re.compile(r"[;)]")


def _negated(text, start):
    # type: (str, int) -> bool
    """Whether a negating word appears in this clause, close enough before the match to govern it."""
    window = text[max(0, start - _NEGATION_WINDOW):start]
    boundary = max((m.end() for m in _CLAUSE_BOUNDARY.finditer(window)), default=0)
    return bool(_NEGATION_CUE.search(window[boundary:]))


def _first_unnegated_match(evidence, pattern):
    # type: (str, "re.Pattern[str]") -> Optional["re.Match[str]"]
    """The first match of this pattern that no negating word reaches, or None."""
    for match in pattern.finditer(evidence):
        if not _negated(evidence, match.start()):
            return match
    return None


def _first_unnegated(evidence, pattern):
    # type: (str, "re.Pattern[str]") -> bool
    """Whether any match of this pattern sits outside the reach of a negating word."""
    return _first_unnegated_match(evidence, pattern) is not None


def _prose_events(evidence):
    # type: (str) -> Dict[str, Any]
    """Round events read out of prose, with negation honoured.

    ``No K.O. or TIME UP banner is visible; no victory pose.`` describes footage where none of those
    things happened. Reading it as a K.O. and a result transition invented a round end and a game
    conclusion out of the word "no". A negated phrase is recorded rather than dropped, because the
    sentence still says the banner was looked for and not seen.
    """
    events = {"negated": []}
    events["result_transition"] = _first_unnegated(evidence, _RESULT_RE)
    if _first_unnegated(evidence, _TIME_UP_RE):
        events["round_end"] = "time_up"
    elif _first_unnegated(evidence, _KO_RE):
        events["round_end"] = "ko"
    elif _first_unnegated(evidence, _ROUND_END_RE):
        # Named for what it is rather than pretending to a more specific banner: this says the round
        # ended, and the pixels said so, but not which callout was drawn.
        events["round_end"] = "callout"
    else:
        events["round_end"] = None
    for name, pattern in (("result_transition", _RESULT_RE), ("round_end", _TIME_UP_RE),
                          ("round_end", _KO_RE), ("round_end", _ROUND_END_RE),
                          # A row that says the opening banner is *not* on screen is evidence against
                          # calling this instant an observed start, and it has to survive as such: the
                          # full-clock rule below would otherwise talk straight past the denial.
                          ("round_open", _ROUND_OPEN_RE)):
        if any(_negated(evidence, m.start()) for m in pattern.finditer(evidence)):
            events["negated"].append(name)
    return events


#: A denial anywhere in the clock noun's own clause, before or after it. The tracker's existing negation
#: check looks *backwards* from the match within 32 characters, which is right for "no READY banner" and
#: wrong for "the 30s timer is not visible", where the denial comes after the digits it cancels.
_CLOCK_DENIAL_RE = re.compile(
    r"\b(?:no|not|never|without|absent|nor|neither|lacks?|unreadable|illegible|unclear|invisible|"
    r"hidden|obscured|gone|cleared|disappear\w*|unseen)\b", re.I)
_CLOCK_CLAUSE_END = re.compile(r"[.;,]")


def _clause_denies_clock(evidence, start, end):
    # type: (str, int, int) -> bool
    """Whether the clause holding a clock reading also denies that reading."""
    before = evidence[:start]
    left = max((match.end() for match in _CLOCK_CLAUSE_END.finditer(before)), default=0)
    tail = evidence[end:]
    right = next((match.start() for match in _CLOCK_CLAUSE_END.finditer(tail)), len(tail))
    return bool(_CLOCK_DENIAL_RE.search(evidence[left:end + right]))


def parse_round_facts(row):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """What one observation says about the *game*, not about the broadcast.

    Two kinds of row arrive here. A classified timeline row carries prose, and the facts are mined
    from it -- the cached BB9 timeline already names the round clock in most of its evidence strings,
    so reading them costs no inference. An observation produced by a vision group carries the facts as
    fields already, and those win. The overlay is per field, because a denser pass that genuinely
    could not read the clock should not erase what the prose said, and a prose row that never mentioned
    the win dots must not invent them. A field absent from both readings stays None, which is not the
    same as a round that did not tick.
    """
    evidence = row.get("evidence") or ""
    state = row.get("state") or "unknown"
    clock = None
    # Which of the two wordings produced the reading, so a reviewer can tell a clock the tracker has always
    # read from one it only started reading after the prose-order gap was closed. None means no reading.
    clock_wording = None
    match = _CLOCK_RE.search(evidence)
    if match:
        value = int(match.group(1))
        if value <= ROUND_CLOCK_START:
            clock, clock_wording = value, "after_word"
    if clock is None:
        # The number ahead of its word ("29s timer") is the same clock, and refusing it is what bounded a
        # round 16 s early and left live play outside every game. Only consulted when the wording above found
        # nothing, so a row stating both keeps the reading the pattern has always trusted. A denial anywhere
        # in the noun's own clause refuses it, before or after: "no 30s timer" and "the 30s timer is not
        # visible" both report a clock the frame could not be read for, and neither is a 30. Measured on the
        # 2,019 classified rows this clause check costs no real reading -- it fires on 0 of the 34.
        backwards = _first_unnegated_match(evidence, _CLOCK_BACKWARDS_RE)
        if backwards and not _clause_denies_clock(evidence, backwards.start(), backwards.end()):
            value = int(backwards.group(1))
            if value <= ROUND_CLOCK_START:
                clock, clock_wording = value, "before_word"
    call = None
    call_match = _ROUND_CALL_RE.search(evidence)
    if call_match:
        call = ("final" if call_match.group(0).upper().startswith("FINAL")
                else int(call_match.group(1)))
    # The game's own opening banner, kept apart from the round *number*: "READY" and "FIGHT" say a round
    # is beginning here and say nothing about which one, so a cue can settle a start and must never
    # become a "round 1".
    cue_match = _first_unnegated_match(evidence, _ROUND_OPEN_RE)
    open_cue = cue_match.group(1).lower() if cue_match else None
    dots = None
    dots_match = _WIN_DOT_RE.search(evidence)
    if dots_match:
        dots = int(dots_match.group(1) or dots_match.group(2))
    events = _prose_events(evidence)
    hud_present = state == "gameplay" or bool(
        state not in _HUD_ABSENT_STATES and _HUD_RE.search(evidence))
    facts = {
        "timestamp": row.get("timestamp"),
        "screen_state": state,
        "confidence": row.get("confidence"),
        "frame": row.get("path"),
        "observation_id": row.get("id"),
        "round_clock": clock,
        # Which wording produced the clock: "after_word" ("timer 29"), "before_word" ("29s timer") or None
        # when the row states no readable clock at all. Kept because a reading that only exists after the
        # prose-order fix must be traceable to that fix, and "unreadable" is a state of the frame, not a 0.
        "round_clock_wording": clock_wording,
        "round_call": call,
        "round_open_cue": open_cue,
        "round_end": events["round_end"],
        "result_transition": events["result_transition"],
        # Side-specific, always a pair. A number the prose never attributed to a player stays
        # unattributed instead of becoming "somebody's total": deciding a game or a set needs to know
        # *which* side reached the target, and reducing a pair to a maximum throws that away.
        "win_dots": [None, None],
        # Per-side row presence, travelling with the count so a number can be judged against the
        # reading that produced it. Absent means "nothing was said", which is not "the row was seen".
        "win_dots_drawn": [None, None],
        "win_dots_confidence": [None, None],
        "win_dots_unattributed": dots,
        "prose_negated": sorted(set(events["negated"])),
        "hud_present": hud_present,
        "characters": tuple(row.get(key) or "" for key in ("character1", "character2")),
        # Kept for the reviewer only. Nothing below reads these, because a header that changes
        # mid-round is exactly the failure this module exists to survive.
        "overlay": {"player1": row.get("player1"), "player2": row.get("player2"),
                    "score": [row.get("score1"), row.get("score2")],
                    "trust": "untrusted"},
        "fact_source": "classified_evidence_text",
        "conflicts": [],
    }

    # A vision group answers these fields directly, and answers are keyed by *presence*, not truth: an
    # explicit ``result_transition: false`` is a correction of the prose, and treating it as "nothing
    # was said" let an old wrong guess survive every later pass. Where the two disagree the stated
    # value wins and the disagreement is recorded, never silently overwritten.
    for key in ("round_clock", "round_call", "round_end", "result_transition", "round_open_cue",
                "win_dots", "win_dots_drawn", "win_dots_confidence"):
        if key not in row:
            continue
        previous = facts[key]
        if previous not in (None, False) and row[key] in (None, False):
            facts["conflicts"].append({"field": key, "from": previous, "to": row[key]})
        facts[key] = row[key]
        if key == "round_clock":
            # The wording says *how* a clock was known, so it has to follow the value that won. Telling a
            # reader "before_word" about a number the answer stated outright would credit the prose pattern
            # with a reading it never made, and a stated no-reading is "unreadable" -- a dark frame -- not a
            # parser gap. That distinction is the whole reason the field exists.
            facts["round_clock_wording"] = ("stated_field" if row["round_clock"] is not None
                                            else "stated_unreadable")
    if "win_dots" in row and isinstance(row["win_dots"], (list, tuple)):
        stated = list(row["win_dots"])[:2]
        while len(stated) < 2:
            stated.append(None)
        if any(facts["win_dots"]) and any(stated) and facts["win_dots"] != stated:
            facts["conflicts"].append({"field": "win_dots", "from": facts["win_dots"], "to": stated})
        facts["win_dots"] = stated
    if row.get("characters") and not any(facts["characters"]):
        facts["characters"] = tuple(list(row["characters"]) + ["", ""])[:2]
    if any(key in row for key in ("round_clock", "win_dots", "round_end", "result_transition")):
        facts["fact_source"] = "vision_group"
    return facts


def build_observations(rows):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Compact, stable-ID'd observations in time order, one per classified row."""
    out = []
    for i, row in enumerate(rows):
        if not _finite(row.get("timestamp")):
            raise ValueError("row {} has a non-finite timestamp".format(i))
        fact = parse_round_facts(row)
        fact["id"] = "gobs-{:05d}".format(i)
        out.append(fact)
    out.sort(key=lambda f: f["timestamp"])
    return out


def _banner_still_in_open_round(current, clock_value, quarantined, restarted, clock=ROUND_CLOCK_START):
    # type: (Dict[str, Any], Optional[int], bool, bool, int) -> bool
    """Whether a ROUND banner is being read inside a round the clock says is still being played.

    Only the game's own primitive decides this, and it decides it one way: a new round begins by taking
    the clock back up. So a banner cannot open a second round when

    * this row read the digits at all -- a banner with no clock is a banner on its own evidence and stays
      an opener, because merging that would read continuity into an absence,
    * the reading is trusted (not quarantined as an impossible fall) and is not a rise,
    * the round already open had a reading of its own to fall from, and
    * that round was demonstrably counting down -- its last digits sat below a fresh clock. A round
      stalled at 60 has no fall to continue, and a banner there is a new round being announced.

    A banner numbering a different round from the one open is rejected by the caller before this is
    consulted: two positive readings that disagree are reported as a conflict, not resolved by which
    one is more convenient.
    """
    if clock_value is None or quarantined or restarted:
        return False
    previous = current.get("last_clock_value")
    if previous is None or int(current.get("clock_readings") or 0) < 1:
        return False
    return previous < clock


def _clock_restarted(last_value, last_time, value, at, tolerance=ROUND_TICK_TOLERANCE):
    # type: (Optional[int], Optional[float], Optional[int], float, int) -> bool
    """Whether two clock readings can only belong to different rounds.

    Inside a round the clock only ever falls, so **any rise beyond reading jitter can only be the
    next round**. The tolerance absorbs a digit misread by the classifier; without it one bad reading
    invents a round. The inverse is deliberately *not* a boundary: a clock that falls more slowly than
    the recording advances is a round that paused (K.O. freeze, hit pause) or a stale reading, and
    treating it as a restart turned 129 real rounds into 169.
    """
    if value is None or last_value is None:
        return False
    return value > last_value + tolerance


def clock_contradiction(last_value, last_time, value, at, tolerance=ROUND_TICK_TOLERANCE):
    # type: (Optional[int], Optional[float], Optional[int], float, int) -> bool
    """True when a clock reading fell faster than the recording advanced: one of them is wrong.

    Reported, never acted on. A bad reading is evidence about the reading, not about the fight, and
    deciding a boundary from it would let a single misread frame end a round early.
    """
    if value is None or last_value is None or last_time is None:
        return False
    expected = last_value - max(0.0, at - last_time)
    return value < expected - tolerance


def track_games(observations, rounds_per_game=DEFAULT_ROUNDS_PER_GAME,
                games_per_set=DEFAULT_GAMES_PER_SET, clock=ROUND_CLOCK_START,
                clip_pad_seconds=CLIP_PAD_SECONDS,
                end_bound_uncertain_seconds=END_BOUND_UNCERTAIN_SECONDS,
                bracket=None, header=None, format_policy=None, stage_readings=None,
                analysis_window=None, source=None):
    # type: (Sequence[Dict[str, Any]], int, int, int, float, float, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Sequence[Dict[str, Any]]], Optional[Sequence[float]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Follow the round -> game hierarchy over ordered observations.

    Boundaries come from the game's own sequence only: a clock that restarts, a ROUND callout, a K.O.
    or TIME UP that ends a round, a result transition, and the menu screens between games. The
    broadcast pairing never opens or closes a round or a game, so a mid-round overlay correction
    cannot split anything.

    Returns rounds, games and sets-as-game-groups with the evidence behind each, plus the unresolved
    items. Nothing is dropped: a game whose end was never seen is reported incomplete and queued.

    ``bracket`` is the optional event record (see ``boundary_bracket``): normalised here, and only ever
    consulted at the **set** layer, where a listed match change may corroborate a boundary the footage
    already argues for. ``header`` is the optional read-only set of saved band readings (see
    ``boundary_header.saved_readings``), consulted only for *who sat on which side*, never for whether a
    set ended here. Left None -- the default, and the case every historical trial ran under -- the sets come
    out exactly as the recording alone describes them.

    ``format_policy`` is an event's confirmed set format (see ``boundary_format.load_policy``). When it is
    supplied the target stops being one number for the whole run: each candidate is measured against the
    target the stage the band names inside *its own span* selects, or against every target the format admits
    when no stage was named there. Format remains optional -- with no policy, recovery still runs and simply
    never closes a set on a guessed target.
    """
    bracket = bracket_context.normalize(bracket)
    rounds: List[Dict[str, Any]] = []
    games: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    contradictions: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    last_closed = None  # type: Optional[Dict[str, Any]]
    previous_gameplay = None  # type: Optional[Dict[str, Any]]
    last_clock = None  # type: Optional[int]
    last_clock_time = None  # type: Optional[float]
    # A clock reading that contradicts the trusted baseline. Held aside until the reading after it
    # says which of the pair was wrong, rather than being allowed to move the baseline.
    suspect = None  # type: Optional[Tuple[int, float]]
    restarted = False

    def close_round(kind, at=None, evidence_id=None):
        # type: (str, Optional[float], Optional[str]) -> None
        nonlocal current, last_closed
        if current is None:
            return
        current["end"] = at if at is not None else current.get("last_gameplay")
        current["end_basis"] = kind
        if evidence_id:
            current["end_evidence"].append(evidence_id)
        rounds.append(current)
        last_closed = current
        current = None

    def open_round(start_fact, basis, prior=None):
        # type: (Dict[str, Any], str, Optional[Dict[str, Any]]) -> None
        nonlocal current, last_closed
        last_closed = None
        current = {"number_observed": start_fact.get("round_call"),
                   "start": start_fact["timestamp"],
                   "start_evidence": [start_fact["id"]], "start_basis": basis,
                   "clock_at_start": start_fact.get("round_clock"),
                   "readings": 0, "characters": start_fact.get("characters"),
                   "result_transition": False, "last_gameplay": start_fact["timestamp"],
                   # The round's own last readable clock, which is the defensible bound on its end when
                   # no K.O. or TIME UP row was ever seen. `last_gameplay` cannot serve: after a
                   # conclusion the broadcast keeps drawing play-like frames over a menu for minutes, and
                   # an end taken from it inherits the dwell instead of the conclusion -- measured at
                   # 215.2 s late on the executed trial. Kept separate from `last_clock` in the loop,
                   # which is a sequence-wide baseline and may hold a reading from an earlier round.
                   "last_clock_at": start_fact["timestamp"]
                                        if start_fact.get("round_clock") is not None else None,
                   "last_clock_value": start_fact.get("round_clock"),
                   # The last instant this round was *demonstrably* being played. A clock that only
                   # repeats itself is not proof of life: at a K.O. the digits freeze and keep drawing
                   # for seconds, so the readings after the last fall are evidence about the overlay,
                   # not about the fight. A reading that fell from the previous one is proof, because
                   # only a running round decrements its own clock.
                   "last_live_at": start_fact["timestamp"]
                                   if start_fact.get("round_clock") is not None else None,
                   # Consecutive tail readings that repeated the value: the freeze, measured rather than
                   # assumed. Reported so a reviewer can see the difference between a round that was
                   # watched to its end and one whose digits simply would not move.
                   "clock_frozen_tail": None,
                   # Counted by the loop below, which is the only place that knows whether the reading
                   # survived quarantine. Seeding this at 1 double-counted the row that opened the round:
                   # the reported figure was 12 where 11 clock rows were read.
                   "clock_readings": 0,
                   "end": None, "end_basis": None, "end_evidence": []}
        if basis == "first_gameplay_seen":
            # Play already underway with no opener in front of it: the round began off-camera or between
            # samples, so its start is inferred and stays flagged instead of being invented -- unless
            # the tape shows the opening on one of the two rows either side of that instant, which the
            # settling pass below reads back from what is seeded here.
            current["start_inferred"] = True
            if "round_open" in (start_fact.get("prose_negated") or []) \
                    or "round_open" in ((prior or {}).get("prose_negated") or []):
                # The row said the banner was *not* on screen. That is evidence against an observed
                # opening, and it vetoes both signatures below rather than only the one it names: a
                # denial of the start signal on the opening rows is not neutral about the start.
                current["opening_denied"] = True
            elif prior is not None and prior.get("round_open_cue"):
                # The banner was drawn on the row straight before this round's first play row: the coarse
                # label kept that frame out of gameplay, the game still said a round begins here.
                _note_open_cue(current, prior, before_opening_row=True)
            elif (start_fact.get("round_clock") is not None and clock is not None
                  and start_fact["round_clock"] == clock
                  and prior is not None and prior["screen_state"] in _GAME_OPENING_STATES):
                # The other way the recording shows an opening: a full round clock arriving straight off a
                # pre-round screen. A candidate only, until the digits actually fall -- one clock=60 frame
                # is an overlay being drawn, which is not proof of a round being played.
                current["opening_candidate"] = {
                    "via": "full_clock_after_transition",
                    "clock_read": start_fact["round_clock"],
                    "at": start_fact["timestamp"],
                    "evidence": start_fact["id"],
                    "previous_screen": prior["screen_state"],
                    "waiting_on": "a clock reading below the round's full time inside this round"}

    def note(key, value):
        # type: (str, Any) -> None
        """Attach a fact to the round it belongs to, which may already be closed.

        A K.O. ends a round before the victory pose and the rematch menu arrive, so the evidence that
        decides whether a *game* ended always lands after the round's own end. Dropping it is what
        made every game look incomplete; keeping it on the round it followed is what the sequence
        actually shows.
        """
        box = current if current is not None else last_closed
        if box is not None and not box.get(key):
            box[key] = value

    def close_current(kind_if_unended):
        # type: (str) -> None
        """Close the open round, preferring the end the recording actually showed.

        A K.O. that was seen ends the round even when nothing after it was broadcast, so the recording
        edge is recorded separately as a fact about the *tape*: it says the sequence stops here, not
        that the round did.
        """
        if current is None:
            return
        if current.get("round_end_at"):
            close_round("round_end_" + current["round_end_kind"], current["round_end_at"],
                        (current.get("round_end_evidence") or [None])[0])
        elif current.get("last_live_at") is not None:
            # No end event was seen, so the round ends where its clock last *fell* -- the final instant
            # the recording proves it was still being played. `close_round`'s other fallback is the last
            # play row, and after a conclusion the camera keeps drawing play-like frames over a menu for
            # minutes: an end inherited from it reported game 1 of the executed trial as ending 215.2 s
            # after the row that decided it. A clock reading that only repeated its own value is the
            # milder version of the same mistake -- at a K.O. the digits freeze and stay on screen -- so
            # the frozen tail bounds the round from above instead of setting its end.
            close_round(kind_if_unended, current["last_live_at"])
        elif current.get("last_clock_at") is not None:
            close_round(kind_if_unended, current["last_clock_at"])
        else:
            close_round(kind_if_unended)
        if kind_if_unended == "recording_or_gap_edge":
            last_closed["tail"] = kind_if_unended

    previous_fact = None  # type: Optional[Dict[str, Any]]

    for fact in observations:
        state = fact["screen_state"]
        # The row straight before this one, whatever the coarse label called it. A round-opening banner
        # can land on a screen the classifier keeps out of gameplay, and a full clock arriving straight
        # off a pre-round screen is the other half of what an opening looks like.
        prior = previous_fact
        previous_fact = fact
        # The round HUD can appear before FIGHT while a coarse classifier still
        # calls the frame stage_intro. An explicitly read in-game clock is usable
        # round evidence; do not drop it solely because of that coarse label.
        if state == "stage_intro" and fact.get("round_clock") is not None:
            state = "gameplay"
        clock_value = fact["round_clock"] if state == "gameplay" else None
        restarted = False
        # True when this fact's clock reading was rejected as impossible. A rejected reading must not
        # become the bound for anything, but the flag has to travel to the bottom of the loop, where the
        # round that owns the anchor is finally known.
        quarantined = False

        if clock_value is not None:
            if clock_contradiction(last_clock, last_clock_time, clock_value, fact["timestamp"]):
                quarantined = True
                # The clock cannot fall faster than the recording advances, so one of the two readings
                # is wrong. Quarantine the newcomer and keep the trusted baseline: promoting a bad
                # reading to baseline made the *next* good one look like a round restart, which is how
                # 60,50,10,30,20 became two rounds. The suspect is kept so the reading after it can
                # say which of the two was real.
                contradictions.append({
                    "type": "round_clock_impossible_fall", "at": fact["timestamp"],
                    "expected_at_least": round(last_clock - (fact["timestamp"] - last_clock_time), 1),
                    "read": clock_value, "previous_reading_at": last_clock_time,
                    "would_resolve": "the cached frames across this interval"})
                if suspect is None:
                    suspect = (clock_value, fact["timestamp"])
                else:
                    # Contradictory twice over: the baseline is what is doubtful, so the suspect takes
                    # over, and the pair stays reported instead of quietly re-anchoring.
                    last_clock, last_clock_time = suspect
                    suspect = None
            else:
                suspect = None
                restarted = _clock_restarted(last_clock, last_clock_time, clock_value,
                                             fact["timestamp"])
                last_clock, last_clock_time = clock_value, fact["timestamp"]

        if fact["result_transition"]:
            note("result_transition", True)
            note("result_transition_at", fact["timestamp"])

        if state != "gameplay":
            if state in _GAME_OPENING_STATES:
                # A screen in front of the game view interrupts play. Whether it separates rounds or
                # games is decided in the game layer, which sees what came before and after.
                note("interrupted_by", state)
                note("interrupted_by_at", fact["timestamp"])
            if state in _GAME_CLOSING_STATES:
                note("closed_by", state)
                note("closed_by_at", fact["timestamp"])
            if state not in _GAME_CLOSING_STATES and current is not None \
                    and fact.get("round_open_cue"):
                # A banner the coarse label kept out of gameplay is still the game saying a round begins,
                # and it corroborates the round already open. It never opens one of its own.
                _note_open_cue(current, fact)
            continue

        previous_gameplay = fact
        if current is None:
            open_round(fact, "round_callout" if fact["round_call"] is not None
                       else "round_clock_reset" if restarted else "first_gameplay_seen", prior)
        elif restarted:
            # The clock can only be here again if a new round began, whatever else was or is visible.
            close_current("round_end_not_observed")
            open_round(fact, "round_clock_reset")
        elif fact["round_call"] is not None and \
                fact["timestamp"] - current["start"] >= ROUND_CALL_MIN_SECONDS and \
                not (fact["round_call"] != current.get("number_observed")
                     and current.get("number_observed") is not None) and \
                _banner_still_in_open_round(current, clock_value, quarantined, restarted, clock):
            # "ROUND n" stays drawn for several seconds after the round opened, so a row inside a
            # countdown that is still falling can only be showing the round already being played. The
            # measured case: 60, 58, 53 across 6,071.065-6,081.075 with the same number announced twice.
            # Cutting there made one round two, and that extra unit is what let the game layer's
            # round-count cap open a game boundary the win row then contradicted.
            current.setdefault("banners_seen_inside", []).append({
                "at": fact["timestamp"], "round_call": fact["round_call"], "clock": clock_value,
                "open_round_clock": current.get("last_clock_value"),
                "number_matches": fact["round_call"] == current.get("number_observed"),
                "means": "the announcement was still on screen while this round's own clock kept falling"})
        elif fact["round_call"] is not None and \
                fact["timestamp"] - current["start"] >= ROUND_CALL_MIN_SECONDS:
            # "ROUND n" is drawn once, as a round begins. A clock that stalled instead of counting down
            # (60 read again 45 seconds later) cannot prove a restart on its own, but the game saying
            # ROUND 2 at that instant can. The seconds rule stops one announcement, seen across
            # several frames, from opening a round per frame.
            if current.get("number_observed") is not None and \
                    fact["round_call"] != current["number_observed"] and \
                    clock_value is not None and not quarantined and not restarted and \
                    current.get("last_clock_value") is not None:
                # The digits say this round is still running and the banner names a different one. Both
                # are positive readings, so neither wins by silence: the banner keeps its traditional
                # power to open, and the disagreement is reported for the frames that could settle it.
                current.setdefault("round_numbering_conflicts", []).append({
                    "at": fact["timestamp"], "announced": fact["round_call"],
                    "open_round_number": current["number_observed"], "clock": clock_value,
                    "open_round_clock": current["last_clock_value"],
                    "would_resolve": "the cached frames across this interval"})
            close_current("round_end_not_observed")
            open_round(fact, "round_callout")
        _note_reading(current, fact)
        current["last_gameplay"] = fact["timestamp"]
        if fact.get("round_open_cue"):
            # The game drew its own opening banner on this row. Whether it corroborates *this* round's
            # start is settled below, against the instant the clock first fell.
            _note_open_cue(current, fact)
        if clock_value is not None and not quarantined:
            # The round's own bound for an end that was never seen. Recorded here, after any open or
            # close this fact caused, so the clock that opens a round belongs to the round it opens --
            # crediting it to the round being closed would push that round's end past its own start.
            previous_value = current.get("last_clock_value")
            had_reading = int(current.get("clock_readings") or 0) > 0
            # Only a *later* reading that failed to fall is a freeze. The row that opened the round has
            # nothing to repeat, and treating it as a stall invented a frozen tail on every round.
            stalled = had_reading and previous_value is not None and clock_value >= previous_value
            current["last_clock_at"] = fact["timestamp"]
            current["last_clock_value"] = clock_value
            current["clock_readings"] = int(current.get("clock_readings") or 0) + 1
            if not stalled:
                # The digits fell, so the round was running at this instant.
                current["last_live_at"] = fact["timestamp"]
                current["clock_frozen_tail"] = None
                if had_reading and previous_value is not None and clock_value < previous_value:
                    # The first *fall* is the boundary of the opening: a banner drawn after this instant
                    # belongs to a round already counting down, and cannot prove where it began. A row
                    # that repeats the full value is neither a fall nor an opening -- it is the freeze.
                    # Only the first one counts, so later falls cannot stretch that window open.
                    if current.get("first_fall_at") is None:
                        current["first_fall_at"] = fact["timestamp"]
                    current["clock_fell_after_opening"] = True
            else:
                tail = current.get("clock_frozen_tail") or {
                    "value": clock_value, "from": fact["timestamp"],
                    # Where the digits last fell: everything after this is unproven play.
                    "last_fall_at": current.get("last_live_at"), "readings": 0}
                tail["to"] = fact["timestamp"]
                tail["readings"] = int(tail.get("readings") or 0) + 1
                current["clock_frozen_tail"] = tail
        if fact["round_call"] is not None and current["number_observed"] is None:
            current["number_observed"] = fact["round_call"]
            current["start_evidence"].append(fact["id"])
        if fact["round_end"] and "round_end_at" not in current:
            # The first end signal after a round opens ends it; the K.O. splash and the victory pose
            # that follow are the same event seen for a few more seconds.
            current["round_end_at"] = fact["timestamp"]
            current["round_end_kind"] = fact["round_end"]
            current["round_end_evidence"] = [fact["id"]]

    close_current("recording_or_gap_edge")
    # Settled before the impossible spans are absorbed: an opener whose only content is a READY banner
    # has to be recognised as an opener first, or the pass below reads it as an ordinary short round.
    _confirm_round_openings(rounds)
    rounds = _drop_impossible_rounds(rounds)
    for index, rnd in enumerate(rounds):
        rnd["id"] = "round-{:05d}".format(index)

    games = _group_into_games(rounds, observations, rounds_per_game,
                              clip_pad_seconds=clip_pad_seconds,
                              end_bound_uncertain_seconds=end_bound_uncertain_seconds)
    sets_out = _group_into_sets(games, games_per_set, observations,
                                clip_pad_seconds=clip_pad_seconds, bracket=bracket, header=header,
                                format_policy=format_policy, stage_readings=stage_readings,
                                analysis_window=analysis_window, source=source)
    unresolved.extend(_unresolved(rounds, games, observations, clock, contradictions))
    for record in sets_out:
        # What the event record thinks this pairing's match score was, attached as something to account
        # for. It is never merged into `game_wins` and never compared against them to decide completion:
        # the number that generated an expectation cannot also be the proof that met it.
        record["bracket_expectation"] = (_bracket_expectation(bracket, record) if bracket else None)
        claims = _set_unresolved_claims(record)
        record["unresolved_claims"] = claims
        if not claims:
            # Nothing is owed: every game named a winner, the wins are mapped to people, and a supplied
            # target says the set closed. This is the only state that leaves the queue silently.
            continue
        # The question differs by what is missing. A candidate cut from a neighbour needs the *membership*
        # question answered before anyone counts its wins toward a set; one that merely stopped short of
        # the target needs games, and a lone game at the edge of what was watched may be all there is.
        cut = record.get("cut_before") or record.get("cut_after")
        if cut and cut["verdict"] == "separated":
            question = ("membership was cut here by %s (%s): decide whether a set ended before counting "
                        "either side of this boundary" % (cut["verdict"], (cut["reasons"] or [""])[0])[:220])
        elif record["membership"] == "unverified":
            question = ("nothing contradicted continuity but nothing showed it either -- a legible player "
                        "name on both sides of each gap, or a win-marker on the broadcast, would upgrade "
                        "this from assumed to shown")
        elif record["membership"] == "single":
            question = ("one game only: either the set continued outside what was watched, or this game "
                        "is the whole of a set the recording cut short")
        else:
            targets = [t for t in ((record.get("applicable_formats") or {}).get("targets")
                                   or [games_per_set]) if t]
            question = ("no side reached %s -- look for games between these that were not "
                        "detected, and for a conclusion past the last one watched"
                        % " or ".join("first to %d" % t for t in targets))
        unresolved.append({
            "type": "set_unresolved", "at": record["start"], "end": record["end"],
            "status": record["status"], "membership": record["membership"],
            # The claim list is the payload: which answers are missing, what each one needs, and whether
            # it needs the model at all. A queue entry that asks for inference must be closable by it.
            "unresolved_claims": claims,
            "needs_inference": [c["claim"] for c in claims if c["costs_inference"]],
            "needs_from_user": [c["claim"] for c in claims if not c["costs_inference"]],
            "games_seen": record["games"], "game_wins": record["game_wins"],
            "game_winners": record["game_winners"],
            "games_without_winner": record["games_without_winner"],
            "identities": record["identities"],
            "applicable_formats": record.get("applicable_formats"),
            "complete_under": record.get("complete_under"),
            "open_under": record.get("open_under"),
            "link_verdicts": [link["verdict"] for link in record["links"]],
            "questions": record.get("questions") or [],
            "alternative_groupings": record.get("alternative_groupings") or [],
            "attribution": record.get("attribution"),
            "cut_before": record.get("cut_before"),
            "cut_after": record.get("cut_after"),
            "reason": record["basis"],
            "routed_questions": [{"kind": q.get("kind"), "route": q.get("route"),
                                   "at": q.get("at"), "because": q.get("because"),
                                   "would_resolve": q.get("would_resolve")}
                                  for q in (record.get("questions") or [])],
            "would_resolve": question,
            "missing_evidence": record["missing_evidence"]})
    return {
        "schema": SCHEMA_VERSION,
        "format": {"rounds_per_game": rounds_per_game, "games_per_set": games_per_set,
                   "source": ("stage-specific from the event format, per candidate" if format_policy
                              else "configured, not inferred from labels"),
                   # Which targets each candidate was measured against is recorded on the candidate, not
                   # here: one run can hold a first-to-two quarter-final and a first-to-three final, and a
                   # single number in the header would be true of neither.
                   "policy": ({k: format_policy[k] for k in
                               ("default_games_to_win", "overrides", "source", "event", "confirmed_at",
                                "grand_final_reset", "bracket_results_verified")}
                              if format_policy else None),
                   # Reported, never adopted: a target that arrives from an event record becomes the run's
                   # target only when a caller passes it in as ``games_per_set`` and says where it came
                   # from. Silently closing a set because a bracket expected three games is how an
                   # external guess becomes an internal fact.
                   "bracket_states": bracket_context.format_for(bracket) if bracket else None},
        # Which routes were in the room. A bracket-assisted run, a band-assisted run and a
        # recording-only run do not make the same claim, so they must not print the same header.
        "evidence_routes": {"recording": "primary",
                            "bracket": bracket_context.provenance_block(bracket),
                            "header_band": _header_route(header)},
        # Named so a report can say which policy produced its clip ends. These are still module
        # defaults unless a caller passes them: the user-facing padding policy the final workflow
        # needs is not built yet, and a constant should not be presented as one.
        "clip_policy": {"clip_pad_seconds": clip_pad_seconds,
                        "end_bound_uncertain_seconds": end_bound_uncertain_seconds,
                        "source": "caller or module constant; not yet a user setting"},
        "rounds": rounds,
        "games": games,
        "sets": sets_out,
        "unresolved": unresolved,
        "accounting": _accounting(observations, rounds, games, sets_out),
    }


def _note_open_cue(rnd, fact, before_opening_row=False):
    # type: (Optional[Dict[str, Any]], Dict[str, Any], bool) -> None
    """Record that this row shows one of the game's own round-opening banners.

    Recorded, never acted on here: whether the banner belongs to the opening of this round is decided
    once the round's first clock fall is known, which is after the round has been watched.
    """
    if rnd is None or not fact.get("round_open_cue"):
        return
    rnd.setdefault("open_cues", []).append({
        "at": fact["timestamp"], "banner": fact["round_open_cue"], "evidence": fact["id"],
        "clock_read": fact.get("round_clock"), "before_opening_row": before_opening_row})


def _confirm_round_openings(rounds):
    # type: (List[Dict[str, Any]]) -> None
    """Settle each round's start from what the recording showed, and only from what it showed.

    Two saved signatures turn "play was already underway" into "the round began here", and both are
    positive evidence that the *game* drew its own opening:

    * a READY or FIGHT banner landing at or before the instant the round's clock first fell -- the same
      banner landing later belongs to a round already counting down, not to this one's start;
    * the round's own full clock, read on the first play row that follows a pre-round screen, *and*
      digits that then fell. The second half is the whole point: a clock that only repeats its value is
      an overlay being drawn, not a round being played, so a bare frame reading 60 is never an opener.

    Neither signature names a round, so neither is allowed to invent a number: "READY" is not "ROUND 1".
    A start that stays inferred keeps its disclosure -- a query that opens mid-round truncated the
    footage, and no amount of nearby 60s changes that.
    """
    for rnd in rounds:
        if rnd.get("start_basis") != "first_gameplay_seen":
            continue
        if rnd.get("opening_denied"):
            # The opening rows said the start signal was not on screen. Two saved signatures turn an
            # inferred start into an observed one and neither survives that denial, which is a claim
            # about the opening instant from the same frames.
            continue
        first_fall = rnd.get("first_fall_at")
        opening = next((cue for cue in (rnd.get("open_cues") or [])
                        if first_fall is None or cue["at"] <= first_fall), None)
        if opening is not None:
            # The banner row itself is part of the evidence for the boundary, and the reading it carried
            # -- which may be nothing at all, since the coarse label can call this frame stage_intro.
            rnd["start_basis"] = "round_open_cue"
        else:
            candidate = rnd.get("opening_candidate")
            if not candidate or not rnd.get("clock_fell_after_opening"):
                continue
            # What closed the gap between "a frame reads 60" and "the round began here" is the digits
            # moving: only a running round decrements its own clock.
            rnd["start_basis"] = "round_open_full_clock"
            opening = dict(candidate, corroborated_by={
                "clock_fell_at": first_fall,
                "readings_after_opening": rnd.get("clock_readings")})
        rnd.pop("start_inferred", None)
        rnd["round_start_observed"] = opening
        evidence = opening.get("evidence")
        if evidence and evidence not in rnd["start_evidence"]:
            rnd["start_evidence"].append(evidence)


def _frame_identity(path):
    # type: (Any) -> str
    """One spelling of one frame file. Mirrors :func:`boundary_evidence.frame_identity`.

    Kept as a local copy because this module sits *below* ``boundary_evidence`` in the import graph and
    cannot import it. Two readings of the same picture are one observation, not corroboration, and the
    corpus records the same frame both relatively and absolutely -- so the rule has to be the same one the
    merge uses, and ``test_the_support_identity_rule_matches_the_merges`` keeps the two from drifting.
    """
    text = str(path or "")
    if not text:
        return ""
    try:
        return os.path.realpath(text)
    except OSError:  # an unreadable or vanished path still needs one stable key
        return os.path.abspath(text)


def _note_reading(current, fact):
    # type: (Optional[Dict[str, Any]], Dict[str, Any]) -> None
    if current is None:
        return
    current["readings"] += 1
    left, right = (fact.get("win_dots") or [None, None])[:2]
    presence = (fact.get("win_dots_drawn") or [None, None])[:2]
    confidence = (fact.get("win_dots_confidence") or [None, None])[:2]
    # Two crops, two visibilities: a side being unseen while the other is seen is ordinary, not a
    # logical contradiction, so presence is recorded per side and judged per side. Each reading keeps
    # its own timestamp and its own crop confidence -- the full-frame timeline's confidence must not be
    # reported as the crop's, which is what an inherited 0.95 in place of 0.50/0.55 meant.
    for side, value, seen, conf, counterpart_seen in (
            ("p1", left, presence[0], confidence[0], presence[1]),
            ("p2", right, presence[1], confidence[1], presence[0])):
        if value is None:
            continue
        # Chronological order, compressing only *consecutive* repeats. Deduplicating by value anywhere
        # inside a round turned 2-3-2 into [2, 3] -- a monotonic series -- so the correction that proves
        # the reading untrustworthy was exactly what the rule deleted, and a game built on it went out
        # as won. Neighbouring identical readings show one unchanged pip row and carry no information;
        # a return to an earlier number is a contradiction to be resolved below, not deleted here.
        seen_values = current.setdefault("win_dots_seen", {}).setdefault(side, [])
        evidence = current.setdefault("win_dots_evidence", {}).setdefault(side, [])
        # ---- raw provenance, kept apart from the state transition -------------------------------
        # The compressed series above answers "what did the row do". It cannot answer "how many times
        # was the row looked at", because a second frame that reads the same number is deliberately
        # invisible here -- and that question is the one a winner's provenance turns on: measured
        # 2026-11-05, the flash frame at 4069.064786 and its confirmation at 4071.066786 both read LEFT 3,
        # and the compressed evidence carried one entry for two frames. So every *distinct* frame that
        # spoke is logged, in order, with the instant it spoke at; the same frame arriving twice (the
        # same answer ingested under two path spellings, or a resumed run carrying it again) is one
        # observation and does not become corroboration by being read twice.
        readings = current.setdefault("win_dots_readings", {}).setdefault(side, [])
        picture = _frame_identity(fact.get("frame") or fact.get("path"))
        # A reading that names no picture still happened at an instant of its own: keying them all on the
        # empty string would fold every anonymous reading into one look and understate support wholesale.
        # The fallback is recorded, so a provenance report can say the frames were not identified.
        anonymous = not picture
        identity = picture or ("instant:%r" % (fact.get("timestamp"),))
        already = next((entry for entry in readings if entry.get("identity") == identity), None)
        if already is None:
            readings.append({
                "value": value, "at": fact.get("timestamp"), "frame": fact.get("frame"),
                "identity": identity, "anonymous_identity": anonymous,
                "observation_id": fact.get("observation_id"),
                "row_drawn": seen, "confidence": conf, "counterpart_row_drawn": counterpart_seen,
                # Whether this frame changed what the series says. False is the compressed-away case: a
                # repeat of the row's state, still a second look at it.
                "changed_the_row": bool(not seen_values or seen_values[-1] != value),
                # Instants at which this same picture was offered again. Counting them as looks is how a
                # resumed run inflates a winner's provenance, and leaving them unrecorded makes "no
                # duplicates" and "duplicates quietly dropped" indistinguishable to a reader.
                "repeat_instants": []})
        elif value == already.get("value"):
            if fact.get("timestamp") not in (already.get("repeat_instants") or []):
                already.setdefault("repeat_instants", []).append(fact.get("timestamp"))
        else:
            # The same picture read two different numbers: a second framing, or an answer rewritten by a
            # later run. Neither is a corroborating look at the first, so the later claim replaces it and
            # the disagreement stays visible instead of becoming support.
            already.update({"value": value, "at": fact.get("timestamp"), "confidence": conf,
                            "row_drawn": seen, "observation_id": fact.get("observation_id"),
                            "counterpart_row_drawn": counterpart_seen,
                            "contradicted_earlier_answer_at": already.get("at")})

        if not seen_values or seen_values[-1] != value:
            seen_values.append(value)
            evidence.append({"value": value, "at": fact.get("timestamp"), "row_drawn": seen,
                             "confidence": conf, "observation_id": fact.get("observation_id"),
                             "frame": fact.get("frame"),
                             # The same answer's claim about the *other* row at the same instant. Both
                             # rows of a scoreboard are drawn or both are: a count asserted on a frame
                             # whose own answer says the counterpart row is not drawn is a claim about a
                             # HUD that is not there. None means the other side said nothing, which is
                             # absence of evidence and never evidence of departure.
                             "counterpart_row_drawn": counterpart_seen})
    unattributed = fact.get("win_dots_unattributed")
    if unattributed is not None:
        # A dot count the reading never placed on a side. It proves somebody has won rounds and
        # nothing about who, so it is kept apart rather than promoted into a per-side total.
        seen = current.setdefault("win_dots_unattributed", [])
        if unattributed not in seen:
            seen.append(unattributed)


def _end_is_observed(rnd):
    # type: (Dict[str, Any]) -> bool
    """Whether this span's end is something the recording showed, rather than a bound it implies."""
    basis = rnd.get("end_basis") or ""
    return basis.startswith("round_end_") and basis != "round_end_not_observed"


def _is_callout_sliver(rnd):
    # type: (Dict[str, Any]) -> bool
    """A span whose entire content is a ROUND banner, with no clock ever read inside it.

    A round that is actually fought has a running clock, so this shape is not a short round: it is the
    *next* round's opener sampled in the second before its digits appeared. Measured on the executed
    trial, the "ROUND 1" row at 1820.819 s was absorbed backwards into the game that had just been won,
    and its unobserved end -- the row itself -- replaced that game's observed K.O. at 1800.799 s,
    putting the boundary 20.02 s after the fight ended. An opener belongs in front of the round it
    opens, not behind the round it follows.
    """
    if int(rnd.get("clock_readings") or 0):
        return False
    return rnd.get("number_observed") is not None or \
        rnd.get("start_basis") in OPENING_SIGNATURE_BASES


def _merge_span_evidence(survivor, fragment):
    # type: (Dict[str, Any], Dict[str, Any]) -> None
    """Fold one absorbed span into the round that survives it, keeping every claim either held.

    Two rules, both learned from a defect. The neighbour survives with its own readings, because an
    earlier version compared a fragment's span with *itself* and let a zero-second frame replace a round
    holding the pip series that named a winner. And the surviving end is the **stronger** of the two:
    an observed K.O. is never displaced by a bound, which is what let an absorbed opener move a game's
    boundary 20 s past its own conclusion.
    """
    survivor["readings"] = int(survivor.get("readings") or 0) + int(fragment.get("readings") or 0)
    for side, values in (fragment.get("win_dots_seen") or {}).items():
        series = survivor.setdefault("win_dots_seen", {}).setdefault(side, [])
        for value in values:
            if not series or series[-1] != value:
                series.append(value)
    for side, entries in (fragment.get("win_dots_evidence") or {}).items():
        survivor.setdefault("win_dots_evidence", {}).setdefault(side, []).extend(entries)

    def _rank(rnd):
        # Observed beats bound; between two of a kind, the later one covers the fragment too.
        return (_end_is_observed(rnd), rnd.get("end") if rnd.get("end") is not None
                else rnd.get("start"))

    if _rank(fragment) > _rank(survivor):
        for key in ("end", "end_basis", "end_evidence"):
            if key in fragment:
                survivor[key] = fragment[key]
    for key in ("last_gameplay", "last_live_at", "last_clock_at"):
        keep, offer = survivor.get(key), fragment.get(key)
        survivor[key] = max((v for v in (keep, offer) if v is not None), default=None)
    # The clock value is a property of a reading, so it travels with whichever reading won.
    if (fragment.get("last_clock_at") or float("-inf")) > (survivor.get("last_clock_at")
                                                           or float("-inf")):
        survivor["last_clock_value"] = fragment.get("last_clock_value")
    if survivor.get("clock_frozen_tail") in (None, [], False):
        survivor["clock_frozen_tail"] = fragment.get("clock_frozen_tail")
    # Everything the sequence attached after the round closed -- the K.O. row, the victory pose, the
    # screen that ended the game -- has to cross the merge with its timestamp, or absorbing a fragment
    # silently deletes the evidence the boundary claims are made of.
    for group in ("round_end_at", "round_end_kind", "round_end_evidence", "number_observed",
                  "clock_at_start", "result_transition", "result_transition_at",
                  "closed_by", "closed_by_at", "interrupted_by", "interrupted_by_at"):
        if survivor.get(group) in (None, [], False) and fragment.get(group) not in (None, [], False):
            survivor[group] = fragment[group]
    survivor["start_evidence"] = (list(survivor.get("start_evidence") or [])
                                  + list(fragment.get("start_evidence") or []))


def _prepend_opener(rnd, sliver):
    # type: (Dict[str, Any], Dict[str, Any]) -> None
    """Attach an absorbed ROUND banner to the round it opens, and stretch that round back to it."""
    if sliver["start"] < rnd["start"]:
        rnd["start"] = sliver["start"]
        rnd["start_evidence"] = (list(sliver.get("start_evidence") or [])
                                 + list(rnd.get("start_evidence") or []))
        if rnd.get("clock_at_start") in (None, [], False):
            rnd["clock_at_start"] = sliver.get("clock_at_start")
    if rnd.get("number_observed") in (None, [], False):
        rnd["number_observed"] = sliver.get("number_observed")
    for side, entries in (sliver.get("win_dots_evidence") or {}).items():
        # Chronologically first, so the series still reads in time order after the move.
        series = rnd.setdefault("win_dots_evidence", {}).setdefault(side, [])
        series[:0] = entries
    for side, values in (sliver.get("win_dots_seen") or {}).items():
        series = rnd.setdefault("win_dots_seen", {}).setdefault(side, [])
        moved = list(values)
        if moved and series and moved[-1] == series[0]:
            moved.pop()                      # one unchanged row seen on both sides of the merge
        series[:0] = moved
    rnd["readings"] = int(rnd.get("readings") or 0) + int(sliver.get("readings") or 0)
    rnd.setdefault("absorbed_openers", []).append(
        {"start": sliver["start"], "end": sliver.get("end"),
         "round_call": sliver.get("number_observed"), "start_basis": sliver.get("start_basis"),
         "read_as": "the opener of this round, sampled before its clock was read"})


def _drop_impossible_rounds(rounds):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Absorb sub-minimum spans, but keep them visible as possible short rounds.

    A span shorter than the ROUND callout and FIGHT banner together is nearly always one transition
    read as two boundaries. "Nearly always" is not "always": a round can be decided in seconds, so
    the fragment is merged into a neighbour and recorded there, never deleted -- the merge is an
    interpretation, and the observation it consumed has to stay recoverable. Which neighbour is
    evidence, not habit: an opener with no clock inside it is absorbed *forward*, into the round whose
    banner it shows, and anything else falls back onto the round that preceded it.
    """
    kept = []  # type: List[Dict[str, Any]]
    index = 0
    while index < len(rounds):
        rnd = rounds[index]
        following = rounds[index + 1] if index + 1 < len(rounds) else None
        span = (rnd.get("end") or rnd["start"]) - rnd["start"]
        unwatched_tail = rnd.get("end_basis") == "recording_or_gap_edge"
        opener = (span < MIN_ROUND_SECONDS and not unwatched_tail and _is_callout_sliver(rnd)
                  and following is not None
                  and following["start"] - (rnd.get("end") or rnd["start"]) <= MIN_ROUND_SECONDS)
        if opener:
            _prepend_opener(following, rnd)
            index += 1
            continue
        if kept and span < MIN_ROUND_SECONDS and not unwatched_tail:
            previous = kept[-1]
            previous.setdefault("absorbed_spans", []).append(
                {"start": rnd["start"], "end": rnd.get("end"), "seconds": round(span, 3),
                 "start_basis": rnd.get("start_basis"), "end_basis": rnd.get("end_basis"),
                 "end_was_observed": _end_is_observed(rnd), "possible_short_round": True})
            _merge_span_evidence(previous, rnd)
            index += 1
            continue
        kept.append(rnd)
        index += 1
    return kept


def _compress(values):
    # type: (Iterable[Any]) -> List[Any]
    """Drop consecutive repeats, which are one unchanged row seen several times, never a movement."""
    out = []  # type: List[Any]
    for value in values:
        if not out or out[-1] != value:
            out.append(value)
    return out


def _isolated_misread(readings, position, previous):
    # type: (Sequence[Dict[str, Any]], int, int) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]
    """The neighbouring readings that would prove one low count is a bad read and not a reset.

    Returns the bracketing pair, or None to leave the fall as a contradiction. Every condition is
    *local* on purpose. A later reading that happens to reach the same height proves nothing about the
    dip: ``2, 0, 1, 2, 3`` is exactly what a new game looks like when it is rebuilt from zero, and the
    only way to tell that from one bad frame is to look at what the same side read immediately around
    it. So the low reading must stand alone between two readings that agree with the previous high, and
    must not be the more confident claim of the three -- a strong reading is never discarded on the
    word of weaker ones, because then the disagreement is real and belongs in the investigation queue.
    """
    following = readings[position + 1] if position + 1 < len(readings) else None
    before = readings[position - 1]
    dip = readings[position]
    if following is None or before["value"] < previous or following["value"] < previous:
        return None
    if abs(dip["round_key"] - before["round_key"]) > DOT_SUPPORT_ROUND_GAP or \
            abs(dip["round_key"] - following["round_key"]) > DOT_SUPPORT_ROUND_GAP:
        return None
    for neighbour in (before, following):
        neighbour_confidence, dip_confidence = neighbour.get("confidence"), dip.get("confidence")
        if None not in (neighbour_confidence, dip_confidence) and \
                dip_confidence > neighbour_confidence:
            return None
    return before, following


def _lands_after_round_end(reading, window_seconds=RESULT_SEQUENCE_SECONDS):
    # type: (Dict[str, Any], float) -> Optional[float]
    """The round's observed end event, if this pip count was read inside the sequence right after it.

    The ending row is the last legitimate word on a round's score. After the end event the row only ever
    gains the winning dot, so a *lower* number read immediately after cannot be a pip taken back -- it is
    the HUD sliding out of shot during the victory sequence. Measured on the executed unseen trial, both
    readings that failed were 2.002 s and 4.004 s past the K.O. that closed their round, on frames where
    the camera had already left the overlay. Raising after the end stays legitimate and is how a game's
    deciding pip usually arrives -- game 2 of that trial was won by a row read 2.002 s after its own end
    -- so only the direction that is impossible is bounded here.

    The window is what keeps this from becoming the maximum-of-everything bug again. A fall 40 s after a
    round closed is not a victory pose, it is either a genuine contradiction or a wrong boundary, and the
    existing rule has to be allowed to contest the game.

    Only an **observed** end bounds anything: a round whose end was inferred from the next round opening,
    a menu, or the edge of the window establishes no final score. An exact tie with `round_end_at` counts
    as inside it, because the row that names the winner and the end event share a timestamp by
    construction.

    This function only asks *where the reading falls in time*. It is not evidence that the count is wrong:
    a K.O. proves a round ended, and says nothing about whether the number read at that moment was right
    or about whether the overlay was still on screen. The excusing happens next, in
    :func:`_hud_departure_corroboration`, and a fall that cannot clear it stays a contradiction.
    """
    rnd = reading.get("round") or {}
    if not _end_is_observed(rnd):
        return None
    end, at = rnd.get("round_end_at"), reading.get("at")
    if end is None or at is None:
        return None
    return end if 0 < at - end <= window_seconds else None


def _hud_departure_corroboration(falling, standing):
    # type: (Dict[str, Any], Dict[str, Any]) -> Optional[str]
    """Why this post-end fall may *not* be dismissed as a leaving HUD, or ``None`` if it may.

    A trailing fall inside the result sequence has two explanations and the round end cannot tell them
    apart: the row slid out of shot, or the count read when the round closed was never correct and this
    reading is the correction. The first excuses the fall, the second makes it the strongest evidence in
    the game. So the exception asks the recording for a fact that only the first explanation fits.

    **One answer must contradict itself about the HUD.** Both rows of a scoreboard are drawn together, so
    a count asserted on a frame whose own answer reports the counterpart row as *not* drawn is a claim
    about pixels that are not there. Measured on the executed trial, both invented readings sit on exactly
    such a frame (3052.04884 s: LEFT 1 while RIGHT is drawn:false; 3054.050839 s: RIGHT 2 while LEFT is
    drawn:false), and a frame that simply says nothing about the other row is refused -- silence is not
    evidence of departure. This is a presence claim, not a timestamp, so it is not a whitelist for the two
    instances that motivated the rule.

    **The correction may not outrank the count it corrects.** Two readings of one row are compared only in
    order ("is the later one the bolder?"), never as probabilities: the model's scale is uncalibrated and
    both numbers are treated as ordinal. If the fall is claimed more confidently than the settled count,
    preferring the older number is a guess, and a game whose own evidence disagrees this loudly is
    contested rather than resolved in either direction. If either confidence is missing, the comparison
    cannot be made and the exception is refused for the same reason.
    """
    if falling.get("counterpart_row_drawn") is not False:
        return "no frame reports this count on a HUD whose counterpart row is absent"
    falling_conf, standing_conf = falling.get("confidence"), standing.get("confidence")
    if falling_conf is None or standing_conf is None:
        return "one of the two readings carries no confidence, so the pair cannot be ordered"
    if falling_conf > standing_conf:
        return ("the fall is claimed more confidently (%s) than the count it would set aside (%s): "
                "the later reading looks like the correction, not the row leaving"
                % (falling_conf, standing_conf))
    return None


def _dot_regressions(open_rounds, result_sequence_seconds=RESULT_SEQUENCE_SECONDS):
    # type: (Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """Sides whose win-dot count fell inside one game, after two narrow exceptions.

    A round-win pip lights and stays lit until the game ends and the row resets, so a fall is either a
    misread or two games spliced together. A reading is treated as a misread only when the *same side's
    own neighbours in time* say so -- see ``_isolated_misread`` -- and then that single reading is set
    aside with its provenance kept on the round; the raw series stays in ``win_dots_evidence``. A
    trailing fall that lands just after the round that owns it closed is offered a second, stricter
    exception -- see ``_hud_departure_corroboration`` -- and is excused only when the frame itself says
    the HUD was going away. Anything else is kept as a real contradiction: a run of low counts, however
    briefly it later recovers, an unrecovered fall, or a disagreement between equally firm readings. A
    reset-and-rebuild and a wrong game boundary look identical from those frames, so the game is named
    contested and queued rather than resolved by whichever hypothesis the arithmetic preferred. Never
    requiring both crops to be visible: each side is judged on its own series.
    """
    by_side = {}  # type: Dict[str, List[Dict[str, Any]]]
    for key, rnd in enumerate(open_rounds):
        for side, values in (rnd.get("win_dots_evidence") or {}).items():
            for reading in values:
                by_side.setdefault(side, []).append(dict(reading, round=rnd, round_key=key))
    if not by_side:
        by_side = {}
        for key, rnd in enumerate(open_rounds):
            for side, values in (rnd.get("win_dots_seen") or {}).items():
                for value in values:
                    by_side.setdefault(side, []).append({"value": value, "round": rnd,
                                                         "round_key": key})

    for readings in by_side.values():
        readings.sort(key=lambda r: (r.get("at") if r.get("at") is not None else 0.0))

    regressions = {}
    for side, readings in by_side.items():
        resolved = 0
        position = 1
        while position < len(readings):
            previous = max(r["value"] for r in readings[:position])
            value = readings[position]["value"]
            if value >= previous:
                position += 1
                continue
            # A count that lowers the standing total of the round it belongs to, read after that round's
            # own observed end, is not that round unlighting a pip: the row had already been settled when
            # the K.O. closed it. Either the crop is no longer showing the HUD at all, or the reading
            # belongs to a later game that had already reset. Neither contradicts the concluded game, so
            # the count is set aside here rather than becoming a regression -- and it is set aside with
            # its provenance kept, because "the pip row disappeared two seconds after the round ended" is
            # exactly the fact a presence rule needs later. Keyed on the reading's own round, so a genuine
            # reset read inside the *next* round stays a contradiction (see _lands_after_round_end).
            bounded_by = _lands_after_round_end(readings[position], result_sequence_seconds)
            if bounded_by is not None and position == len(readings) - 1:
                # A *trailing* fall whose count was already settled when the round closed, read inside the
                # result sequence that follows it, is the pip row leaving the frame -- and it is set aside
                # by refusing to call it a contradiction, never by rewriting the series. `win_dots_seen`
                # and `win_dots_evidence` keep every reading exactly as sent: the raw record is what a
                # reviewer needs in order to disagree with this rule, and the existing isolated-misread
                # path is the only place a reading is ever removed from the series.
                #
                # Bounded and trailing are both load-bearing. A fall that later readings climb back from
                # is the wrong-boundary signature this rule was built to keep -- 0-3-0-1 in one game is two
                # games spliced, and the 40 s gap in the test fixture is not a victory pose -- and dropping
                # it would restore the maximum-of-everything bug the fall rule replaced. So only the last
                # reading, inside the window, whose predecessor settled at or before the end event, is even
                # offered to the exception; everything else contests outright, and what is offered still has
                # to clear _hud_departure_corroboration before it is excused.
                settled = [r for r in readings[:position]
                           if r["value"] == previous and r.get("at") is not None
                           and r["at"] <= bounded_by]
                if settled:
                    stale = readings[position]
                    declined = _hud_departure_corroboration(stale, settled[0])
                    if declined is not None:
                        # Time and direction alone do not excuse anything: the fall stays a live
                        # contradiction and the game stays contested, but the near-miss is recorded so a
                        # reviewer can see what the pipeline weighed rather than reconstruct it.
                        stale["round"].setdefault("win_dots_after_round_end_declined", []).append(dict(
                            {k: v for k, v in stale.items() if k not in ("round", "round_key")},
                            side=side, round_end_at=bounded_by,
                            standing_count=previous, established_at=settled[0].get("at"),
                            standing_confidence=settled[0].get("confidence"),
                            counterpart_row_drawn=stale.get("counterpart_row_drawn"),
                            window_seconds=result_sequence_seconds, declined_because=declined))
                    else:
                        stale["round"].setdefault("win_dots_after_round_end", []).append(dict(
                            {k: v for k, v in stale.items() if k not in ("round", "round_key")},
                            side=side, round_end_at=bounded_by,
                            seconds_after_round_end=round(stale.get("at") - bounded_by, 3),
                            standing_count=previous, established_at=settled[0].get("at"),
                            # The pair the ordering was decided on, kept so a reviewer can re-run the
                            # judgement instead of taking it on trust.
                            standing_confidence=settled[0].get("confidence"),
                            counterpart_row_drawn=stale.get("counterpart_row_drawn"),
                            window_seconds=result_sequence_seconds,
                            reason="the series' last reading, %.3f s after this round closed with its "
                                   "count already at %d, on a frame whose own answer reports the "
                                   "counterpart row as not drawn: the HUD was leaving the shot, and the "
                                   "fall is no more confident than the count it contradicts. Kept in the "
                                   "raw series; not counted as a regression."
                                   % (stale.get("at") - bounded_by, previous)))
                        position += 1
                        continue
            support = None
            if resolved < MAX_RESOLVED_READS_PER_SIDE:
                support = _isolated_misread(readings, position, previous)
            if support is not None:
                resolved += 1
                rejected = readings.pop(position)
                before, following = support
                rejected["round"].setdefault("win_dots_rejected", []).append(dict(
                    {k: v for k, v in rejected.items() if k not in ("round", "round_key")},
                    side=side,
                    supported_by=[{k: n[k] for k in ("value", "at", "confidence") if k in n}
                                  for n in support],
                    reason="isolated fall: this side read %r at %s and %r at %s on either side of this "
                           "single %r at %s, and a round-win pip cannot unlight and re-light by itself "
                           "inside one game" % (
                               before["value"], _seconds_text(before.get("at")),
                               following["value"], _seconds_text(following.get("at")),
                               value, _seconds_text(rejected.get("at")))))
                for target in {r["round_key"] for r in readings} | {rejected["round_key"]}:
                    rnd = open_rounds[target]
                    rnd.setdefault("win_dots_seen", {})[side] = _compress(
                        r["value"] for r in readings if r["round_key"] == target)
                continue
            # The first fall is the one reported: it is where the sequence stopped being a game whose
            # pips only ever filled, and reporting the last of several let the earliest break vanish.
            regressions.setdefault(side, {"from": previous, "to": value,
                                          "at": readings[position].get("at")})
            position += 1
    return regressions


def _winner_support(rounds, side, rounds_per_game):
    # type: (Sequence[Dict[str, Any]], Optional[str], int) -> Optional[Dict[str, Any]]
    """How many distinct frames read this side at or above the target, on the path that credited the win.

    The question a published winner should be able to answer on its own: is the number that closed this game
    something the recording was looked at once, or twice? It has to be counted on the raw log
    (``win_dots_readings``) and not on ``win_dots_evidence``, because that series compresses a repeat of the
    same row away -- which is right for "what did the score do" and wrong for "how often was it seen".
    Measured on BB9's 3,958.955 game: the flash frame at 4069.064786 and its confirmation at 4071.066786 both
    read LEFT 3, and the compressed evidence carried one entry for both.

    Four exclusions, each one a way a count could inflate without any new looking happening:

    * a reading the temporal rules set aside (an isolated misread, a departing HUD) supports nothing;
    * the same frame arriving twice is one observation, so identity is the dedupe key;
    * the other side's row and this side's sub-target readings are not evidence of *this* win;
    * only the rounds of the game that was credited -- a later game's identical row is another claim.

    Two frames agreeing is corroboration and nothing more: it does not prove the count, and one frame has
    never been a reason to withhold a winner. This only says how much looking stands behind it.
    """
    if not side:
        return None
    set_aside = _set_aside_readings(rounds)
    frames, instants, dropped_aside, repeats, anonymous = [], [], 0, 0, 0
    for rnd in rounds:
        for entry in (rnd.get("win_dots_readings") or {}).get(side) or []:
            if (entry.get("value") or 0) < rounds_per_game:
                continue
            if (side, entry.get("at")) in set_aside:
                dropped_aside += 1
                continue
            identity = entry.get("identity") or _frame_identity(entry.get("frame"))
            if entry.get("anonymous_identity"):
                anonymous += 1
            if identity in frames:
                repeats += 1
                continue
            frames.append(identity)
            repeats += len(entry.get("repeat_instants") or [])
            if _finite(entry.get("at")):
                instants.append(float(entry["at"]))
    if not frames:
        return None
    ordered = sorted(instants)
    return {
        "side": side, "target": rounds_per_game,
        "deciding_at": ordered[0] if ordered else None,
        "frames": [Path(name).name for name in frames],
        # The full identities, not just the names: two framings of one instant are two pictures that share
        # a basename, and a provenance field that hides that is worse than no field.
        "frame_paths": list(frames),
        "frame_count": len(frames),
        "distinct_instants": len(set(ordered)),
        "first_at": ordered[0] if ordered else None,
        "last_at": ordered[-1] if ordered else None,
        "seconds_apart": (round(ordered[-1] - ordered[0], 6) if len(ordered) > 1 else 0.0),
        "set_aside_excluded": dropped_aside, "duplicate_identities_excluded": repeats,
        # Frames that named no picture at all: the count is still honest per instant, but a reader has to
        # know that "one frame" here might mean "one reading whose source was never identified".
        "frames_unidentified": anonymous,
        "single_reading": len(frames) == 1,
        "means": "how many distinct frames read this side at or above the target inside this game. One is "
                 "a claim nobody looked at twice: still a winner, and still worth a neighbour read.",
    }


def _game_outcome(open_rounds, reached, next_round, regressions):
    # type: (Sequence[Dict[str, Any]], List[str], Optional[Dict[str, Any]], Dict[str, Any]) -> Tuple[Optional[str], str, bool, List[str]]
    """Decide a game's winner and whether the evidence contradicts itself.

    A winner is claimed only when the readings agree. A contradictory game is still a real span of
    footage and stays reported, but it is contested: it names no winner and contributes nothing to a
    set, because a review flag that leaves ``winner`` set is a flag somebody can forget to read.
    """
    reasons = []
    if len(reached) > 1:
        reasons.append("both_sides_reached_target")
    if regressions:
        reasons.append("win_dots_regressed:" + ",".join(sorted(regressions)))
    if next_round is not None and next_round.get("number_observed") not in (None, 1):
        reasons.append("round_numbering_continues_at_{}".format(next_round["number_observed"]))
    contested = bool(reasons)
    if contested:
        return None, "contested:" + ("+".join(reasons) if reasons else "unknown"), True, reasons
    if len(reached) == 1:
        return reached[0], "win_dots", False, []
    return None, "not_observed", False, []


def _group_into_games(rounds, observations, rounds_per_game, clip_pad_seconds=CLIP_PAD_SECONDS,
                      end_bound_uncertain_seconds=END_BOUND_UNCERTAIN_SECONDS):
    # type: (List[Dict[str, Any]], Sequence[Dict[str, Any]], int, float, float) -> List[Dict[str, Any]]
    """Group rounds into games from the sequence, and name the side that took each game.

    A game is closed when the recording shows something a round end does not: a result transition
    followed by a between-game screen, or one side's win dots reaching the target. Five rounds in a
    row without either is not a completed game, it is a run whose conclusion was never seen, so the
    span is emitted incomplete and queued. A game closed only on a menu, or on readings that disagree
    with each other, has **no winner**: the sequence may prove a game ended and never prove who took
    it, and set reconstruction is only allowed to consume the winners it can actually stand behind.
    """
    games = []
    open_rounds = []  # type: List[Dict[str, Any]]
    cap_notes = []  # type: List[Dict[str, Any]]
    for position, rnd in enumerate(rounds):
        open_rounds.append(rnd)
        following = rounds[position + 1] if position + 1 < len(rounds) else None
        dots = rnd.get("win_dots_seen") or {}
        reached = [side for side, values in dots.items()
                   if values and max(values) >= rounds_per_game]
        # Stage/character intros also occur between rounds. They cannot turn a
        # round victory animation into a completed game.
        closes_on_menu = bool(rnd.get("result_transition") and rnd.get("closed_by"))
        capped = len(open_rounds) >= 2 * rounds_per_game - 1
        boundary_conflicts = []  # type: List[Dict[str, Any]]
        if capped and not closes_on_menu and len(reached) != 1:
            # The count rule is a stopping signal, not evidence of a boundary. Where the win row says the
            # opposite -- drawn, non-zero and identical on both sides of the candidate cut, nothing cleared
            # -- closing here manufactures a game end the recording contradicts. That is how 6,016.01 got
            # split from a game at 6,161.155 with a 2-2 row standing on both sides of it. So the group
            # stays open, the fact is recorded on whatever game eventually closes it, and the queue still
            # sees the run: an over-long group is `game_incomplete`, never a win.
            note = _cap_boundary_contradicted(open_rounds, following, rounds_per_game)
            if note:
                note["rounds_seen"] = len(open_rounds)
                callout = note.get("new_game_callout") or {}
                if callout.get("conflicts_with_pip_continuity"):
                    # Except when the recording *also* says a new game began. An explicit ROUND 1 and a win
                    # row that never cleared are two positive readings pointing opposite ways, and neither
                    # gets to erase the other quietly: the announced boundary stands, the disagreement is
                    # carried on both games it separates, and no winner is claimed from a game whose start
                    # is the thing in dispute.
                    note["resolution"] = ("the boundary the callout announced was kept, flagged unresolved; "
                                          "the alternative is one game containing both groups of rounds")
                    boundary_conflicts.append(note)
                else:
                    cap_notes.append(note)
                    continue
        if not (closes_on_menu or len(reached) == 1 or capped):
            continue
        # Only a cap-forced cut needs this question asked: every other game here closed on something the
        # screen showed, and filing those boundaries as "drawn by counting" would be a false disclaimer on
        # fourteen decided games. Inside a run the count chose the edge, so the row is consulted twice --
        # once for a reset *within* the run, which is where a game actually turned over, and once at the
        # cut itself, which is the boundary the next game will be measured by. An edge the row cannot
        # settle comes back in row_notes: the split is withheld, not reinterpreted as continuity.
        boundary_evidence, row_reset_at, row_notes = [], None, []
        if capped and not closes_on_menu and len(reached) != 1 and not boundary_conflicts:
            row_reset_at = _row_reset_within(open_rounds, rounds_per_game, row_notes)
            if row_reset_at:
                boundary_evidence.append(row_reset_at)
            else:
                boundary_evidence.extend(n for n in (
                    _cap_boundary_reset(open_rounds, following, rounds_per_game,
                                        rounds[position + 2:], row_notes)
                    or _cap_boundary_unread(open_rounds, following),) if n)
            boundary_evidence.extend(row_notes)
        regressions = _dot_regressions(open_rounds)
        winner, winner_basis, contested, reasons = _game_outcome(
            open_rounds, reached, following, regressions)
        unresolved_cuts = [note for note in row_notes if note.get("verdict") == "ambiguous"]
        if unresolved_cuts and not boundary_conflicts:
            contested = True
            reasons = list(reasons) + ["win_row_reset_unresolved_at_%s"
                                       % _seconds_text(note.get("at")) for note in unresolved_cuts]
        if boundary_conflicts:
            for conflict in boundary_conflicts:
                # Keep what this group would have claimed. Withholding a winner is not the same as
                # forgetting it, and the reviewer deciding the boundary needs both candidates visible.
                conflict["winner_if_this_side_won"] = winner or winner_basis
            contested = True
            reasons = list(reasons) + ["pip_continuity_contradicts_round_1_callout"]
            winner, winner_basis = None, "withheld:game_boundary_conflict"
        basis = ("result_transition_then_menu" if closes_on_menu else
                 "win_dots_reached_target" if len(reached) == 1 else
                 "round_count_exceeded_without_conclusion")
        if row_reset_at:
            # The run contains a game the recording can point to, and the rest is a run whose own
            # conclusion has not been seen. Emitting the first part separately is what makes the second
            # part's start evidence rather than an assumption, and it is the only case here where the row
            # -- not the count -- chose the boundary.
            index = row_reset_at["after_round_index"]
            head, tail = open_rounds[:index], open_rounds[index:]
            # What bounds this head is the round the row opened, not the round that happened to follow the
            # counted run. The rehearsal of game-reset-probe-v1 showed why: a ROUND 2 two rounds past the
            # boundary -- the second round of the game that legitimately opened there -- was read as a
            # contradiction of the boundary itself, contesting the one genuine reset the rule exists to
            # find. The neighbour also belongs on the record: if the round that opens the new game announces
            # ROUND 2, that is the announcement this cut must answer to.
            head_next = tail[0] if tail else following
            head_winner, head_basis, head_contested, head_reasons = _game_outcome(
                head, [], head_next, _dot_regressions(head))
            games.append(_game_record(head, "win_row_cleared_mid_run", rounds_per_game,
                                      complete=False, winner=head_winner,
                                      winner_basis=head_basis, next_round=head_next,
                                      contested=head_contested,
                                      contested_reasons=head_reasons,
                                      dot_regressions=_dot_regressions(head),
                                      clip_pad_seconds=clip_pad_seconds,
                                      boundary_evidence=[row_reset_at],
                                      end_bound_uncertain_seconds=end_bound_uncertain_seconds))
            open_rounds = tail
            continue
        games.append(_game_record(open_rounds, basis, rounds_per_game,
                                  complete=closes_on_menu or len(reached) == 1,
                                  winner=winner, winner_basis=winner_basis,
                                  next_round=following, contested=contested,
                                  contested_reasons=reasons, dot_regressions=regressions,
                                  clip_pad_seconds=clip_pad_seconds,
                                  round_count_beyond_target=cap_notes,
                                  boundary_evidence=boundary_evidence,
                                  boundary_conflicts=boundary_conflicts,
                                  end_bound_uncertain_seconds=end_bound_uncertain_seconds))
        open_rounds, cap_notes = [], []
    if open_rounds:
        regressions = _dot_regressions(open_rounds)
        winner, winner_basis, contested, reasons = _game_outcome(open_rounds, [], None, regressions)
        games.append(_game_record(open_rounds, "incomplete_no_closing_evidence",
                                  rounds_per_game, complete=False, winner=winner,
                                  winner_basis=winner_basis, contested=contested,
                                  contested_reasons=reasons, dot_regressions=regressions,
                                  clip_pad_seconds=clip_pad_seconds,
                                  round_count_beyond_target=cap_notes,
                                  end_bound_uncertain_seconds=end_bound_uncertain_seconds))
    _mark_disputed_game_starts(games)
    return games


def _candidate_boundary_conflicts(games):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    '''Every game boundary inside this candidate that the recording reads two ways.

    Built from the games rather than from the candidate's own tally: the disagreement lives on the
    boundary, and a candidate that reported only "no target reached" would hide the one fact a reviewer
    needs in order to go and look at it.
    '''
    out = []
    for game in games:
        for conflict in game.get("boundary_conflicts") or []:
            out.append({"at": conflict["at"],
                        "readings_persisted": conflict["readings"],
                        "round_number_announced": (conflict.get("new_game_callout") or {}).get(
                            "round_number_announced"),
                        "between": [game["start"], conflict["at"]],
                        "winners_withheld": True,
                        "would_resolve": conflict.get("would_resolve")})
    return out


def _boundary_alternatives(games):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    '''The merged reading of a disputed game boundary, in the schema `alternative_groupings` already uses.

    A link cut keeps its joined reading there because the two errors are not symmetric. This is the same
    argument one layer down: the split is primary because the game announced it, and the merge the win row
    supports is kept as the alternative. `at` and `joined_with` are present because the projection and the
    grouping diff read them directly, and a malformed entry here would be an AttributeError in a command
    that never touched this code.
    '''
    out = []
    for index, game in enumerate(games):
        for conflict in game.get("boundary_conflicts") or []:
            following = next((other for other in games[index + 1:]
                              if abs(other["start"] - conflict["at"]) < 0.01), None)
            announced = (conflict.get("new_game_callout") or {}).get("round_number_announced")
            out.append({"at": conflict["at"], "joined_with": game["start"],
                        "would_merge": [game["start"], following["start"]] if following
                        else [game["start"]],
                        "reading": "one game containing the rounds on both sides: the win row is read and "
                                   "unchanged across this boundary, so no game opened here",
                        "reason": "pip_continuity_contradicts_round_1_callout",
                        "questions": [], "identity": "unknown", "score_signal": "unknown",
                        "dwell_seconds": None, "evidence_for": ["win_row_persisted"],
                        "evidence_against": ["round_callout_announced_%s" % announced],
                        "basis": "recording_only", "kind": "game_boundary_conflict",
                        "conflicting_readings": {"win_row": conflict["readings"],
                                                 "round_callout": announced},
                        "why_not_primary": "the game itself announces a new game at this instant and a "
                                           "merge would erase that reading; the split stands and both "
                                           "games report the disagreement"})
    return out


def _mark_disputed_game_starts(games):
    # type: (List[Dict[str, Any]]) -> None
    """Give the game *after* a disputed boundary the same visibility the game before it got.

    A conflict recorded only on the game that closed reads as a problem behind the boundary, when the
    thing actually in doubt is whether the second game began there at all -- and its win row is the
    evidence for that. Both records carry the same conflict so neither side of the cut looks settled.
    """
    for game in games:
        for conflict in game.get("boundary_conflicts") or []:
            partner = next((other for other in games
                            if other is not game and abs(other["start"] - conflict["at"]) < 0.01), None)
            if partner is None:
                continue
            partner["start_is_disputed"] = {
                "at": conflict["at"],
                "readings_persisted": conflict["readings"],
                "round_number_announced": (conflict.get("new_game_callout") or {}).get(
                    "round_number_announced"),
                "alternative_grouping": {"start": game["start"], "end": partner.get("end"),
                                         "as": "one game containing the rounds on both sides"},
                "contested_reasons": ["pip_continuity_contradicts_round_1_callout"],
                "would_resolve": conflict.get("would_resolve")}
            if not partner.get("contested"):
                partner["contested"] = True
                partner["contested_reasons"] = list(partner.get("contested_reasons") or []) + \
                    ["inherited_game_boundary_conflict"]
                partner["winner_if_boundary_untouched"] = partner.get("winner")
                partner["winner"], partner["winner_basis"] = None, "withheld:game_boundary_conflict"
                partner["counts_toward_set"] = False


def _set_aside_readings(open_rounds):
    # type: (Sequence[Dict[str, Any]]) -> FrozenSet[Tuple[Any, Any]]
    """The win-dot readings the temporal rules would discard, without discarding any of them.

    The fall rule and the reset rule read the same pixels and used to answer independently: the single low
    reading :func:`_dot_regressions` sets aside as an isolated misread was still, to
    :func:`_row_reset_within`, a row that "reads empty on both sides", and the run was cut in two on it.
    One reading cannot be a misread and a game boundary at the same instant, so the reset paths ask the
    fall rule first and judge only the readings it would stand behind. Run on a copy, because this is a
    question and not a rewrite: ``win_dots_evidence`` keeps every reading exactly as sent.
    """
    probe = [copy.deepcopy(rnd) for rnd in open_rounds]
    _dot_regressions(probe)
    set_aside = set()
    for rnd in probe:
        for field in ("win_dots_rejected", "win_dots_after_round_end"):
            for entry in rnd.get(field) or []:
                set_aside.add((entry.get("side"), entry.get("at")))
    return frozenset(set_aside)


def _next_row_reading(rounds, side, after_at, set_aside=frozenset()):
    # type: (Sequence[Dict[str, Any]], str, Optional[float], FrozenSet) -> Optional[Dict[str, Any]]
    """The next live reading of one side strictly after ``after_at``, or ``None`` if the readings end."""
    for rnd in rounds:
        for entry in (rnd.get("win_dots_evidence") or {}).get(side) or []:
            if entry.get("value") is None or (side, entry.get("at")) in set_aside:
                continue
            if after_at is None or (entry.get("at") or 0.0) > after_at:
                return entry
    return None


def _win_row_cut(open_rounds, index, rounds_per_game, tail_rounds=(), set_aside=frozenset()):
    # type: (Sequence[Dict[str, Any]], int, int, Sequence[Dict[str, Any]], FrozenSet) -> Optional[Dict[str, Any]]
    """What the win row says about cutting a counted run between round ``index - 1`` and round ``index``.

    ``None`` when the row has no opinion at this edge -- a side nobody read, a group that already produced
    a winner, a row that only ever filled. Otherwise one of three verdicts:

    ``"reset"`` -- somebody had won a round and the row reads empty on **both** sides at this opening, and
    no side that held a pip is read back at that height on its next reading. A game's row is cleared only
    by a new game opening, so this is a boundary.

    ``"ambiguous"`` -- the same empty row, except the side that held the pip reads back at or above that
    height at the very next frame. A scoreboard that never went dark and a new game that immediately won a
    round are those same two frames: :func:`_dot_regressions` refuses to settle that pair by arithmetic, so
    this does not either. No split is drawn, the boundary stays contested, and both groupings are recorded.

    ``"declined"`` -- the edge looked like a reset only through a reading the temporal rules already set
    aside. The fall rule decided it; deciding it again here would only be a second and weaker opinion.
    """
    sides = ("p1", "p2")
    rest = list(tail_rounds or ())
    before_span = list(open_rounds)[:index]
    after_span = list(open_rounds)[index:] + rest
    cut_at = (open_rounds[index].get("start") if index < len(open_rounds)
              else (rest[0].get("start") if rest else None))

    def pair(skip):
        return ({side: _nearest_row(before_span, side, True, skip) for side in sides},
                {side: _nearest_row(after_span, side, False, skip) for side in sides})

    def clears_row(previous, nxt):
        return (all(previous.values()) and all(nxt.values())
                and not any((previous[side]["value"] or 0) >= rounds_per_game for side in sides)
                and any((previous[side]["value"] or 0) > 0 for side in sides)
                and all((nxt[side]["value"] or 0) == 0 for side in sides))

    raw_previous, raw_next = pair(frozenset())
    looked_cleared = clears_row(raw_previous, raw_next)
    previous, nxt = pair(set_aside)
    if not clears_row(previous, nxt):
        if not looked_cleared:
            return None
        return {"type": "win_row_reset_declined_by_temporal_adjudication", "verdict": "declined",
                "after_round_index": index, "at": cut_at,
                "readings": {side: [raw_previous[side]["value"], raw_next[side]["value"]]
                             for side in sides},
                "reading_frames": {side: [raw_previous[side].get("at"), raw_next[side].get("at")]
                                   for side in sides},
                "declined_readings": [side for side in sides
                                      if (side, (raw_next[side] or {}).get("at")) in set_aside],
                # What the discarded reading yielded to. The rejection provenance itself is written by the
                # fall rule when it adjudicates the group the reading belongs to, which is not this group;
                # the note has to carry the bracket on its own or a reviewer cannot check the call.
                "next_live_reading": {side: ({"value": nxt[side]["value"], "at": nxt[side].get("at")}
                                             if nxt[side] else None) for side in sides},
                "confirms": None,
                "means": "the row read empty on both sides at this opening, but the reading that emptied "
                         "it is one the fall rule already set aside against this side's own neighbours in "
                         "time: a row that was never dark does not open a game, so this stays inside one "
                         "game and the raw reading is kept on the round"}

    had_wins = [side for side in sides if (previous[side]["value"] or 0) > 0]
    rebuilt = {}
    for side in had_wins:
        later = _next_row_reading(after_span, side, nxt[side].get("at"), set_aside)
        if later is not None and (later["value"] or 0) >= (previous[side]["value"] or 0):
            rebuilt[side] = {"held_before": previous[side]["value"], "read_at": later.get("at"),
                             "value": later["value"]}
    readings = {side: [previous[side]["value"], nxt[side]["value"]] for side in sides}
    frames = {side: [previous[side].get("at"), nxt[side].get("at")] for side in sides}
    if rebuilt:
        return {"type": "win_row_reset_ambiguous", "verdict": "ambiguous",
                "after_round_index": index, "at": cut_at,
                "readings": readings, "reading_frames": frames,
                "rebuilt": rebuilt, "confirms": None, "contested": True,
                "why_not_confirmed": "the row went empty at this opening and %s was read back at the height "
                                     "it held on the very next reading: a row that never went dark and a "
                                     "new game that immediately won a round are the same two frames, so "
                                     "this boundary is contested rather than confirmed"
                                     % "/".join(sorted(rebuilt)),
                "alternative": {"as": "two games cut at this instant", "after_round_index": index,
                                "at": cut_at,
                                "reading": "the row read empty on both sides at this opening, which is "
                                           "what a new game's opening looks like"},
                "means": "an empty row was read on both sides after somebody had won, and the primary "
                         "reading stays the one the count drew: one game whose readings disagree. The "
                         "split is kept as the alternative because nothing here eliminates it either"}
    return {"type": "win_row_cleared_across_the_candidate_boundary", "verdict": "reset",
            "after_round_index": index, "at": cut_at,
            "readings": readings, "reading_frames": frames,
            "means": "a round win stood on the row when it was last read, and the row is read empty on "
                     "both sides at this round's opening: no round outcome clears a game's row, so a "
                     "new game opened here and the run counted is two games read",
            "confirms": "a game boundary inside the run, ahead of the one the round count suggested"}


def _row_reset_within(open_rounds, rounds_per_game, notes=None):
    # type: (Sequence[Dict[str, Any]], int, Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]
    """Where inside a counted run the win row says a new game actually opened, if anywhere.

    The boundary a cap draws is at the end of the run, but a run of five rounds is two games only if the
    turn happened somewhere inside it, and the row records exactly that: some side is credited with a
    round win, and at a later round opening the row reads empty on **both** sides. Nothing about the
    presentation is consulted. Between-round fades, versus splashes and stage intros appear inside
    ordinary round transitions on this recording (12 of 112), so a rule keyed to them would cut decided,
    accepted games; the cleared row cannot be produced by any round outcome, because round wins are only
    ever added to a game's row -- and only a row that really was dark says it, which is why the readings
    the temporal rules set aside never get to open a game (:func:`_set_aside_readings`).

    Returns the index of the round that opens the new game, so the caller can split the run there, or
    ``None`` when no pair of readings says it. ``None`` is the common case today: these runs have no pip
    readings at all inside them, which is why the boundary stays as the count left it and the missing
    observation is named instead. An edge where the row went dark but the frames cannot tell a reset from
    a misread is not merged away either: it comes back through ``notes`` as a contested alternative.
    """
    set_aside = _set_aside_readings(open_rounds)
    fallback = None
    for index in range(1, len(open_rounds)):
        cut = _win_row_cut(open_rounds, index, rounds_per_game, (), set_aside)
        if cut is None:
            continue
        if cut["verdict"] == "reset":
            return cut
        if fallback is None:
            fallback = cut
    if fallback is not None and notes is not None:
        notes.append(fallback)
    return None


def _cap_boundary_reset(open_rounds, following, rounds_per_game, tail_rounds=(), notes=None):
    # type: (Sequence[Dict[str, Any]], Optional[Dict[str, Any]], int, Sequence[Dict[str, Any]], Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]
    """Whether the win row says a cap-forced game boundary really happened.

    The counterpart to :func:`_cap_boundary_contradicted`, and deliberately narrower than "a row fell". A
    single side dropping one pip is what a misread frame looks like, and the recorder has two documented
    excuses for a fall (an isolated reading its neighbours contradict, and a trailing reading after an
    observed round end). A row that reads **empty on both sides** after the cut, when somebody had won a
    round before it, is not a misread of one slot: a game's score table is cleared only when a new game
    starts, and no round outcome takes a won pip away.

    Both of those are the same reading of the same frame, so they are settled by the same adjudicator, not
    twice and not independently: the cut is evaluated against the readings :func:`_dot_regressions` would
    stand behind. Where the row went dark and then came straight back to the height it held, the two rules
    cannot be told apart from these frames, and the answer is a contested alternative in ``notes`` rather
    than a boundary the count is allowed to call confirmed.

    Nothing here looks at stage intros, black frames or controller prompts. Those are how a broadcast
    *presents* a new game, and on this recording they appear between rounds as often as between games
    (an intro and a fade sit inside 12 of 112 round transitions, including inside games whose winner is
    accepted), so a rule keyed on them cuts games that should not be cut. The row is the only witness.

    Like the contradiction rule, this stands down when somebody already reached the target: that group is
    a concluded game on its own evidence, and the winner path handles it.
    """
    if following is None:
        return None
    # The adjudication spans the cut, not just the run above it: the reading that would empty the row lives
    # in the rounds *below*, and the fall rule judges a reading by the neighbours on either side of it.
    # Deciding the cut from one side of the fall rule's own bracket is the bypass this whole path exists to
    # close, and it errs the safe way -- a reading set aside can only ever remove a credit, never add one.
    set_aside = _set_aside_readings(list(open_rounds) + [following] + list(tail_rounds or ()))
    cut = _win_row_cut(open_rounds, len(open_rounds), rounds_per_game,
                       [following] + list(tail_rounds or ()), set_aside)
    if cut is None:
        return None
    if cut["verdict"] == "reset":
        cut["at"] = following["start"]
        cut["means"] = ("somebody had taken a round when the row was last read, and the row is read empty "
                        "on both sides at the round that follows: a game's win row is cleared only by a "
                        "new game opening, so this cut is a boundary and not a round transition")
        cut["confirms"] = "the game boundary the round count proposed"
        return cut
    if notes is not None:
        notes.append(cut)
    return None


def _cap_boundary_unread(open_rounds, following):
    # type: (Sequence[Dict[str, Any]], Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A cap-forced boundary that no win row was ever read against, with the two frames that would settle it.

    The round count is a stopping signal, so when nothing on screen says where a game ended the group is
    still cut -- an over-long run has to be reported as a run -- but the cut cannot be recorded as though
    it were verified. Naming the exact instants is the point: "one game or two" here is not a judgement
    about ambiguous footage, it is an observation nobody has taken, and these are the two rows to ask.
    """
    if following is None:
        return None
    missing = {}
    for side in ("p1", "p2"):
        before, after = _nearest_row(open_rounds, side, True), _nearest_row([following], side, False)
        missing[side] = {"last_read_before": (before or {}).get("at"),
                         "first_read_after": (after or {}).get("at"),
                         "read_before": bool(before), "read_after": bool(after)}
    if all(entry["read_before"] and entry["read_after"] for entry in missing.values()):
        return None                      # both ends read: reset or persist decides it
    return {"type": "cap_boundary_has_no_win_row_read",
            "at": following["start"],
            "would_confirm": "a win row that reads empty on both sides at this round's opening, after a "
                             "row that showed at least one side having won a round",
            "would_refute": "the same non-empty row on both sides of the cut, which is what "
                            "_cap_boundary_contradicted already refuses to split on",
            "readings_needed": missing,
            "means": "this boundary was drawn by the round count alone: no pip row on either side of it "
                     "has ever been read, so the grouping is preserved as the detector left it and the "
                     "missing observation is named instead of guessed through"}


def _nearest_row(rounds, side, from_end, set_aside=frozenset()):
    # type: (Sequence[Dict[str, Any]], str, bool, FrozenSet) -> Optional[Dict[str, Any]]
    """The nearest win-row reading for one side: last in the span, or first.

    ``set_aside`` names the readings :func:`_dot_regressions` would discard for a fall, which a reset rule
    may not count as a row that cleared. The raw entry stays on the round untouched -- this only declines
    to treat it as evidence of a boundary.
    """
    for rnd in (list(rounds)[::-1] if from_end else list(rounds)):
        entries = [e for e in ((rnd.get("win_dots_evidence") or {}).get(side) or [])
                   if e.get("value") is not None and (side, e.get("at")) not in set_aside]
        if entries:
            return dict(entries[-1] if from_end else entries[0], round=rnd.get("id"))
    return None


def _cap_boundary_contradicted(open_rounds, following, rounds_per_game):
    # type: (Sequence[Dict[str, Any]], Optional[Dict[str, Any]], int) -> Optional[Dict[str, Any]]:
    """Whether the win row says a cap-driven game boundary did not happen.

    Only a row that is **read and identical** on both sides of the cut contradicts it. A missing reading, a
    zeroed row or a changed count is not a contradiction: those are the genuine resets and the unresolved
    cases, and they keep the behaviour they had before this rule existed. Every side with evidence at both
    ends has to agree, because one side clearing while the other holds is exactly what a new game looks
    like. A group in which somebody already reached the target is a concluded game, not a contradiction,
    so this refuses to fire and the normal winner path takes it.
    """
    if following is None:
        return None
    compared, sides_seen = {}, 0
    for side in ("p1", "p2"):
        before = _nearest_row(open_rounds, side, True)
        after = _nearest_row([following], side, False)
        if not before or not after:
            continue
        sides_seen += 1
        persisted = before["value"] == after["value"] and bool(before["value"])
        compared[side] = {"before": before, "after": after, "persisted": persisted}
    if not sides_seen or not all(c["persisted"] for c in compared.values()):
        return None
    announced = following.get("number_observed")
    return {"type": "win_row_persisted_across_the_candidate_boundary",
            "at": following["start"],
            "readings": {side: [c["before"]["value"], c["after"]["value"]]
                         for side, c in compared.items()},
            # A round the game itself announces as ROUND 1 is positive evidence of a new game, and it
            # belongs in the note rather than being weighed by whoever reads the note next: the pip row
            # and the callout can both be right readings of the same frame sequence, and then the
            # boundary is unresolved, not decided in favour of the last rule added.
            "new_game_callout": (None if announced is None else {
                "at": following["start"], "round_number_announced": announced,
                "conflicts_with_pip_continuity": announced == 1}),
            "means": "the row that decides this game is read and unchanged on both sides of where the "
                     "round count wanted a new game to start, so no game opened there",
            "would_resolve": "the win row at the candidate round's own opening, and the ROUND callout "
                             "on that round's first rows"}


def _first_reaching(rounds, side, rounds_per_game):
    # type: (Sequence[Dict[str, Any]], Optional[str], int) -> Optional[float]:
    """The earliest reading in this game where `side`'s own row reached the target.

    Kept apart from the conclusion event on purpose. The K.O. stops the clock and the pip arrives with
    the victory pose a second or two later, so a game legitimately ends **before** the row that proves
    who took it: measured on the executed trial, games 3 and 4 name their winners 2.0 s after the K.O.
    row that closed them. Forcing the two together would move the boundary or throw away the
    confirmation, which is why both are recorded.
    """
    if not side:
        return None
    hits = [e.get("at") for rnd in rounds
            for e in ((rnd.get("win_dots_evidence") or {}).get(side) or [])
            if e.get("value") is not None and e["value"] >= rounds_per_game
            and e.get("at") is not None]
    return min(hits) if hits else None


def _game_record(rounds, basis, rounds_per_game, complete, winner=None, winner_basis=None,
                 next_round=None, contested=False, contested_reasons=(), dot_regressions=None,
                 clip_pad_seconds=CLIP_PAD_SECONDS, round_count_beyond_target=(),
                 boundary_evidence=(),
                 boundary_conflicts=(), end_bound_uncertain_seconds=END_BOUND_UNCERTAIN_SECONDS):
    # type: (List[Dict[str, Any]], str, int, bool, Optional[str], Optional[str], Optional[Dict[str, Any]], bool, Sequence[str], Optional[Dict[str, Any]], float, float) -> Dict[str, Any]
    last = rounds[-1] if rounds else {}
    # A game conclusion is three separable claims -- something ended, the game was the level that
    # ended, and which side won it -- and each rests on different pixels. Recording them apart is what
    # lets a reviewer see that "result transition + menu" proves the first two and never the third,
    # instead of one status field silently implying all three.
    signals = {
        "result_transition": bool(last.get("result_transition")),
        "closing_screen": last.get("closed_by") or last.get("interrupted_by"),
        "win_dots_reached_target": any(
            values and max(values) >= rounds_per_game
            for values in (last.get("win_dots_seen") or {}).values()),
        # A following round announcing ROUND 1 is the numbering's own confirmation that a new game
        # began here; ROUND 2 or 3 says the sequence continued and this was never a game end.
        "next_round_number": (next_round or {}).get("number_observed"),
        "round_numbering_confirms_new_game": (next_round or {}).get("number_observed") == 1,
        "round_numbering_contradicts": (next_round or {}).get("number_observed") not in (None, 1),
    }
    # ---- the five separable claims about where this game stops -------------------------------
    last_round = rounds[-1] if rounds else {}
    conclusion_at = last_round.get("end") if rounds else None
    anchored_on_event = _end_is_observed(last_round)
    confirmed_at = _first_reaching(rounds, winner, rounds_per_game)
    # How much looking stands behind the number that closed this game. Only a row-credited winner has a
    # support count to make: a game closed on a menu names no winner, and a contested one names none either.
    winner_support = (_winner_support(rounds, winner, rounds_per_game)
                      if winner and winner_basis == "win_dots" else None)
    # The first screen that says the broadcast moved on, read from the round the conclusion came from and
    # from no other. A victory pose over an earlier round of the same game says that round ended -- which
    # is what a round is -- and offering it as evidence about the *game's* conclusion turned every
    # legitimately transitioned mid-game round into a contradiction 60 s before the end.
    transition_at, transition_kind = None, None
    for key, label in (("result_transition_at", "result_transition"),
                       ("closed_by_at", None), ("interrupted_by_at", None)):
        if last_round.get(key) is not None:
            transition_at = last_round[key]
            transition_kind = label or last_round.get(key.replace("_at", ""))
            break
    # Every marker that says "by this instant the game had already concluded", in the order the
    # recording offers them. The next round opening used to be the *only* upper bound the record knew
    # about, which is what made an unwatched menu dwell look like an unbounded end: game 1 of the
    # executed trial reported 216.216 s of doubt while the row naming its winner and the victory pose
    # sat 0.0 s and 1.0 s after the clock it was bounded by. The tightest marker wins.
    next_opening = (next_round or {}).get("start")
    markers = [
        {"kind": "winner_confirmation", "at": confirmed_at,
         "means": "this side's pip reached the target, so the deciding round was already over"},
        {"kind": "result_transition", "at": transition_at,
         "means": "the game drew its result screen, so play had stopped"},
        {"kind": "next_round_opening", "at": next_opening,
         "means": "a new round began, so nothing after this instant is this game"},
    ]
    seen_markers = [m for m in markers if m.get("at") is not None]
    # What proved the round was still being played, and whether its clock froze before the end.
    frozen = last_round.get("clock_frozen_tail") or None
    live_bound = {
        "at": conclusion_at,
        "kind": ("observed_end_event" if anchored_on_event else
                 "clock_fell" if conclusion_at is not None else "no_live_evidence"),
        "frozen_readings_after": (frozen or {}).get("readings"),
        "frozen_from_at": (frozen or {}).get("from"),
        "frozen_to_at": (frozen or {}).get("to"),
    }
    earlier_than_play = [m for m in seen_markers
                         if conclusion_at is not None and m["at"] < conclusion_at - 0.5]
    bound_high = next_opening
    if not anchored_on_event:
        tightest = [m for m in seen_markers
                    if conclusion_at is not None and m["at"] >= conclusion_at - 0.5]
        if tightest:
            bound_high = min(tightest, key=lambda m: m["at"])["at"]
        if bound_high is None:
            tails = [r.get("last_gameplay") for r in rounds if r.get("last_gameplay") is not None]
            bound_high = max(tails) if tails else conclusion_at
    elif bound_high is None:
        bound_high = conclusion_at
    if anchored_on_event:
        # The conclusion was watched, so there is no bracket to quote: the tape behind it is the dwell
        # between games, reported under its own name below rather than widened into the end's uncertainty.
        bound_high = conclusion_at
    unwatched = (round(bound_high - conclusion_at, 3) if conclusion_at is not None
                 and bound_high is not None and bound_high >= conclusion_at else None)
    # The tape between the conclusion and the next opening, reported as what it is: a fact about how long
    # the broadcast held a menu, never a bound on when the game ended. Kept so shrinking the bracket
    # cannot be mistaken for hiding the dwell.
    dwell = (round(next_opening - conclusion_at, 3) if next_opening is not None
             and conclusion_at is not None and next_opening >= conclusion_at else None)
    end_conflict = ({"seconds_late_after_evidence": round(min(conclusion_at - m["at"]
                                                              for m in earlier_than_play), 3),
                     "evidence_earlier_than_clock": [
                         {"kind": m["kind"], "at": m["at"], "means": m["means"]}
                         for m in sorted(earlier_than_play, key=lambda m: m["at"])]}
                    if (earlier_than_play and not anchored_on_event and conclusion_at is not None)
                    else None)
    # Four states, because "the recording never showed it" comes in degrees and each needs different
    # frames. ``observed`` is an end event on screen. ``bounded`` is a conclusion the recording pins
    # between two of its own observations, close enough together to quote. ``conflicting`` is two
    # observations that cannot both be true -- play evidenced after the game was already won. ``open``
    # is the wide one: a bound whose far end is the next round, with nothing in between watched.
    if anchored_on_event:
        end_status = "observed"
    elif end_conflict:
        end_status = "conflicting"
    elif unwatched is not None and unwatched <= end_bound_uncertain_seconds:
        end_status = "bounded"
    else:
        end_status = "open"
    # The clip has to reach the read naming the winner -- that is the whole point of the recording --
    # but stops at the uncertainty bound instead of following the camera into a minutes-long menu.
    # Four separate claims about where this game stops, because the recording keeps them apart and a
    # single "end" field cannot hold all of them without lying about at least one:
    #   * ``last_live_at`` -- the last instant the digits were seen to fall, so play was unambiguously
    #     happening. The lower bound on any conclusion.
    #   * ``conclusion_event_at`` -- the estimate of when the game ended, which is that bound when no
    #     conclusion marker was read, or the observed K.O./TIME UP row when one was.
    #   * ``winner_confirmed_at`` -- the first reading that says *who* took it, which legitimately lands
    #     after the end, because the pip arrives with the victory pose.
    #   * footage past the estimate -- rows still labelled gameplay after the conclusion, which is the
    #     camera on the players, not the game still being played.
    # `last_live_at` is the lower bound on the conclusion, so it can never sit above the conclusion
    # estimate: when the K.O. row was watched, every falling digit after it is the victory camera or a
    # replay (measured: three games whose rounds keep reading digits for 1 s to 119 s past an observed
    # end, up to 2,722.72 against a conclusion at 2,603.60). The round keeps its own reading of that; the
    # game reports the bound the claim can actually stand on.
    last_live_at = (conclusion_at if anchored_on_event else
                    (last_round.get("last_live_at") or last_round.get("last_clock_at")))
    confirmation_readings = [
        {"at": e.get("at"), "value": e.get("value"), "confidence": e.get("confidence")}
        for rnd in rounds for e in ((rnd.get("win_dots_evidence") or {}).get(winner) or [])
        if winner and e.get("value") is not None and e["value"] >= rounds_per_game]
    confirmation_readings.sort(key=lambda e: e["at"] or 0.0)
    footage_after = (round(last_round.get("last_gameplay") - conclusion_at, 3)
                     if last_round.get("last_gameplay") is not None and conclusion_at is not None
                     and last_round["last_gameplay"] > conclusion_at + 0.001 else None)
    clip_end = None
    for candidate in (x for x in (conclusion_at, confirmed_at) if x is not None):
        clip_end = candidate if clip_end is None else max(clip_end, candidate)
    if clip_end is not None:
        # Capped at the next opening, not at the tightened bound: the bound says when the game stopped,
        # the cap says only how far the tape runs, and a 1 s bracket must not truncate a clip that has
        # the decisive row inside it.
        clip_cap = max(next_opening if next_opening is not None else clip_end + clip_pad_seconds,
                       clip_end)
        clip_end = round(min(clip_end + clip_pad_seconds, clip_cap), 6)
    return {
        "start": rounds[0]["start"],
        "end": rounds[-1]["end"] if rounds else None,
        # Five claims about where a game stops, which used to be one field quietly holding all of them.
        # `end` is the conclusion event: an observed K.O./TIME UP row when there was one, otherwise the
        # round's last readable clock. A K.O. two seconds before the pip update is therefore *correct*,
        # not an error to reconcile, and the confirmation is recorded after it rather than merged in.
        "conclusion_event_at": conclusion_at,
        "conclusion_evidence": ("observed_end_event" if anchored_on_event else
                                "last_readable_clock" if last_round.get("last_clock_at") is not None
                                else "last_play_row"),
        "winner_confirmed_at": confirmed_at,
        "winner_confirmation_lag_seconds": (round(confirmed_at - conclusion_at, 3)
                                            if confirmed_at is not None and conclusion_at is not None
                                            else None),
        "transition_at": transition_at,
        "transition_evidence": transition_kind,
        # Where the conclusion could actually be, when the recording never showed it. The upper bound is
        # the next round opening -- the last moment that could still belong to this game -- and the tape
        # between the two is a fact about what was *watched*, not about what happened. A missing
        # observation at an edge is not proof the recording was cut.
        "end_uncertainty": {"low": conclusion_at, "high": bound_high,
                            "seconds_wide": unwatched,
                            "seconds_unwatched": unwatched,
                            "next_opening_at": next_opening,
                            "tape_after_conclusion_seconds": dwell,
                            "clock_readings_in_round": last.get("clock_readings"),
                            # What the near bound rests on: "the pip reached three" is a different claim
                            # from "the next round opened 216 s later", and only one of them is evidence.
                            "bounded_by": (next((m["kind"] for m in seen_markers
                                                 if m["at"] == bound_high), None)
                                           if not anchored_on_event else "observed_end_event"),
                            "conclusion_markers": [dict(m, used=(not anchored_on_event and
                                                                  m["at"] == bound_high))
                                                   for m in sorted(seen_markers,
                                                                    key=lambda m: m["at"])],
                            "live_bound": live_bound,
                            "cause": (None if anchored_on_event else
                                      "the round's end was never observed; the conclusion lies between "
                                      "the last instant the recording proves it was played and the "
                                      "first thing it drew afterwards")},
        "end_status": end_status,
        # A boundary the recording never showed is a bound, and a bound is not an export timestamp.
        "provisional_end": end_status != "observed",
        "boundary_certain": end_status in ("observed", "bounded"),
        "end_conflict": end_conflict,
        # What a clip has to contain: the conclusion evidence and the read naming the winner, padded a
        # little, and never stretched to the next menu just because the dwell is long.
        "clip_end_at": clip_end,
        "clip_end_basis": ("conclusion plus winner confirmation, padded %.1f s and capped at the next "
                           "round opening; padding is a constant here, not yet the user-facing clip "
                           "policy the final workflow needs" % clip_pad_seconds)
        if clip_end is not None else None,
        "last_observed_play_at": last.get("last_gameplay"),
        # The same instant the line above reports is not the claim above it. `last_observed_play_at` is
        # any row still labelled gameplay, which includes the camera on the victory pose; `last_live_at`
        # is the last time the digits themselves fell, and is the only one that bounds a conclusion from
        # below. When footage runs past the estimate the gap is reported instead of being absorbed into
        # either number -- shrinking the bracket is not the same as settling it.
        "last_live_at": last_live_at,
        "footage_after_conclusion_seconds": footage_after,
        "footage_after_conclusion_kind": (
            "post_conclusion_footage: the row is labelled gameplay and sits past the conclusion estimate,"
            " which is the victory camera rather than play still being played" if footage_after else None),
        # Every reading that says the winner reached the target, with the confidence that came with it.
        # The earliest sets `winner_confirmed_at`; a reviewer needs to see that it was 0.55 on a flash
        # frame and 0.90 two seconds later, because that is the difference between a bound and a fact.
        "winner_confirmation_readings": confirmation_readings,
        # Times the round count wanted a game boundary and the win row refused to agree. Kept on the game
        # that eventually closed, because the alternative is a correct-looking run with no trace of the
        # split that was nearly manufactured inside it.
        "round_count_beyond_target": list(round_count_beyond_target or []),
        # What the win row said about this game's opening boundary: a cleared row that confirms the cut,
        # or the two frames nobody has read yet. Empty means the boundary was never in question -- the
        # game closed on a result transition or on the target, not on a count.
        "boundary_evidence": list(boundary_evidence or []),
        # A cap cut that a positive ROUND 1 announced and the win row denied. Both games the cut separates
        # carry it -- the one before shows the boundary it refused to claim, the one after shows the start
        # it cannot stand on -- and the winner is withheld from both until something settles it.
        "boundary_conflicts": [dict(c) for c in (boundary_conflicts or [])],
        "start_is_disputed": None,
        "rounds": len(rounds),
        "round_ids": [r.get("id") for r in rounds],
        "basis": basis,
        "conclusion_signals": signals,
        "status": "complete" if complete else "incomplete",
        # Who took the game, and on what. A game closed by a menu alone proves the game ended and
        # nothing about the winner, and that distinction is the whole difference between a set
        # reconstruction and arithmetic dressed as one.
        "winner": winner,
        "winner_basis": winner_basis if winner else (winner_basis or "not_observed"),
        # The winner's provenance: how many distinct frames read the credited side at or above the target
        # inside this game, and how far apart they are. None when the winner did not come from the row.
        "winner_support": winner_support,
        # Contested means the readings inside this game disagree with each other. It is not a note to
        # read later: the winner is already None and the set layer is already refusing to count it.
        "contested": bool(contested),
        "contested_reasons": list(contested_reasons),
        "dot_regressions": dict(dot_regressions or {}),
        # Counts read after the round that owns them had already closed, and therefore set aside rather
        # than counted as a fall. Named on the game because the alternative is a winner that appears with
        # no trace of the reading that almost cost it: a reviewer has to be able to see the discarded
        # contradiction, keep it, or reject the rule that discarded it.
        "dots_after_round_end": [dict(entry, round=rnd.get("id"))
                                 for rnd in rounds for entry in
                                 (rnd.get("win_dots_after_round_end") or [])],
        # The ones this rule was offered and refused. A fall that met the timing and direction but found
        # no HUD corroboration is still contesting its game, and without this line the refusal is
        # invisible: the report shows a contested game and no trace of the exception it nearly used.
        "dots_after_round_end_declined": [dict(entry, round=rnd.get("id"))
                                          for rnd in rounds for entry in
                                          (rnd.get("win_dots_after_round_end_declined") or [])],
        "counts_toward_set": bool(winner) and not contested,
        "rounds_per_game_expected": rounds_per_game,
        # Which level of the hierarchy actually ended here. A result transition over the KO camera
        # can close a round, a game or the set; only the screens after it say which, so an absent
        # screen is recorded as unknown rather than resolved by assumption.
        "concluded_level": "game" if complete else "unknown",
        "start_basis": rounds[0]["start_basis"] if rounds else None,
        "characters": rounds[0].get("characters") if rounds else None,
        "closing_screen": rounds[-1].get("closed_by") or rounds[-1].get("interrupted_by")
        if rounds else None,
        "evidence_ids": [r["end_evidence"][0] if r["end_evidence"] else r["start_evidence"][0]
                         for r in rounds],
    }


def _overlay_pair(observations, lo, hi):
    # type: (Sequence[Dict[str, Any]], float, float) -> Dict[str, Any]
    """Who the broadcast said was on each side during a span, and how much to trust that.

    Names are the only *persistent* participant identity the recording offers -- LEFT/RIGHT are screen
    sides, which change when a set changes ends or a bracket swaps sticks. They are also the least stable
    signal in the file: they go missing over close-ups (``player1`` arrives as None for whole seconds), they
    arrive truncated ("Suka" for "Asuka"), and they differ in case between reads of the same frame
    (RebiX/Rebix). So this returns a majority read per side plus how many rows supported it, and every
    caller treats an absent read as *unknown*, never as disagreement.
    """
    votes = {"player1": {}, "player2": {}}
    char_votes = {"player1": {}, "player2": {}}
    rows_with_names = 0
    score_seen = []
    for obs in observations:
        at = float(obs["timestamp"])
        if not (lo <= at <= hi):
            continue
        overlay = obs.get("overlay") or {}
        named = False
        for key in ("player1", "player2"):
            value = (overlay.get(key) or "").strip()
            if value:
                votes[key][value] = votes[key].get(value, 0) + 1
                named = True
        # The fighters, counted the same way as the names: a close-up that draws no HUD contributes
        # nothing rather than a vote for the empty string, which would outvote a real read.
        for key, value in zip(("player1", "player2"), obs.get("characters") or ()):
            value = str(value or "").strip().upper()
            if value and obs.get("hud_present", True):
                char_votes[key][value] = char_votes[key].get(value, 0) + 1
        if named:
            rows_with_names += 1
        score = overlay.get("score")
        if isinstance(score, (list, tuple)) and len(score) == 2 \
                and all(isinstance(v, int) for v in score):
            score_seen.append((at, [int(score[0]), int(score[1])]))
    winner_of = lambda side: (max(votes[side].items(), key=lambda kv: (kv[1], kv[0]))[0]
                              if votes[side] else None)
    spread = lambda side: sorted(votes[side].keys())
    # How much of the reading backs the majority name. This is not decoration: inside the Buk/Rebix match
    # (1577.8-3246.9 s) the gameplay rows name ``PWDDA | Ariz``/``Mattiniero`` 161 times and ``Buk``/``Rebix``
    # 135 times, because the broadcast header advertises the next pairing and changes mid-round. A name
    # agreeing with itself 135 times is arithmetically as "legible" as one agreeing 300 times over 300
    # rows, so it is the share, not the count, that separates a participant change from the header moving.
    def share_of(side):
        tally = votes[side]
        total = sum(tally.values())
        return round(max(tally.values()) / total, 3) if total else None

    def fighter_of(side):
        """The fighter only when one spelling dominates, mirroring how a name earns the same right."""
        tally = char_votes[side]
        total = sum(tally.values())
        if not total:
            return None
        top, count = max(tally.items(), key=lambda kv: (kv[1], kv[0]))
        return top if count / total >= NAME_DOMINANT_SHARE else None

    return {"player1": winner_of("player1"), "player2": winner_of("player2"),
            # Per-side fighters, kept in the same "unknown is not disagreement" discipline as the names.
            "characters": {side: fighter_of(side) for side in ("player1", "player2")},
            "character_reads": {side: sorted(char_votes[side]) for side in ("player1", "player2")},
            "all_reads": {side: spread(side) for side in ("player1", "player2")},
            "share": {side: share_of(side) for side in ("player1", "player2")},
            "named_rows": rows_with_names,
            "score_first": score_seen[0][1] if score_seen else None,
            "score_last": score_seen[-1][1] if score_seen else None,
            "score_times": [[at, value] for at, value in score_seen[:1]]}


def _gap_cues(observations, lo, hi):
    # type: (Sequence[Dict[str, Any]], float, float) -> Dict[str, Any]
    """The screens between two games, weighted rather than split into cut/no-cut.

    Every screen is reported in the bucket it falls in, including the neutral ones, because "no cue was
    neutral" has to be inspectable: the same post-match menu that ends a set also ends each game inside it,
    and the only way a reviewer can tell the two apart is by seeing what else was on screen.
    """
    seen, states = [], set()
    for obs in observations:
        at = float(obs["timestamp"])
        if lo < at < hi:
            state = obs.get("screen_state")
            if state:
                states.add(state)
                seen.append((at, state))
    buckets = {"preferred": [], "corroborating": [], "neutral": [], "unclassified": []}
    for state in sorted(states):
        buckets.setdefault(SET_CUE_WEIGHT.get(state, "unclassified"), []).append(state)
    # The gap's own shape, read off the footage rather than off a name. On this recording the two kinds of
    # gap look different and the difference is not in any single screen: between two games of one match the
    # broadcast shows the game-result screen and the next round's opening (21.0 s and 25.0 s at
    # 3,373.4->3,394.4 and 3,933.9->3,959.0), while between two matches it shows players putting controllers
    # down and picking them up behind a 182 s dwell (3,546.5->3,728.7). So a gap that holds the result
    # screen with **no** controller screen is positive evidence that the station was not cleared, which is
    # what "these games are one set" means when nobody's nameplate can be trusted.
    station_cleared = bool(buckets["preferred"])
    between_games = "post_match_menu" in states
    return {"states": sorted(states), "at": seen[0][0] if seen else None,
            "first_seen_at": seen[0][0] if seen else None,
            # Whether anything at all was sampled in the stretch. A gap with no rows is not a stretch in
            # which nothing happened, and an edge rule that cannot tell those apart turns a truncated query
            # into a claim about the recording.
            "observed": bool(seen),
            "unclassified": buckets["unclassified"],
            # Kept as separate keys rather than one "cues" list, so a rule can name which class it used.
            "preferred_cues": buckets["preferred"], "corroborating_cues": buckets["corroborating"],
            "neutral_cues": buckets["neutral"],
            "station_cleared": station_cleared,
            "within_match_transition": bool(between_games and not station_cleared)}


def _seat_continuity(earlier, later):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """Whether each screen side kept the same fighter between two games.

    Characters are the only participant continuity the recording offers that does not depend on a name:
    the seat keeps its fighter until the players change seats or change matches, and a nameplate is measured
    moving under a fighter that never moved. ``held`` therefore says the human on each side is the same
    person across the gap; ``exchanged`` says the two players switched sides; ``changed`` says the fighters
    themselves are different, which is what a new match looks like when the players re-pick; ``unknown``
    covers a side whose fighter was never read.

    This is not identity -- two players can both main Reina, and one player can change characters between
    games -- so it corroborates a mapping and never decides one. What it does settle is the contradiction a
    swap looks like: if the fighters held their sides while the names crossed, no sticks were exchanged and
    the two seat-wins belong to the same person, whoever the broadcast calls them.
    """
    def pair(side):
        value = (earlier.get("characters") or {}).get(side)
        other = (later.get("characters") or {}).get(side)
        return (str(value).strip().lower() if value else None,
                str(other).strip().lower() if other else None)
    one, two = pair("player1"), pair("player2")
    if not all(one) or not all(two):
        return "unknown"
    if one[0] == two[1] and two[0] == one[1] and one[0] != one[1]:
        return "exchanged"
    if one[0] == one[1] and two[0] == two[1]:
        return "held"
    if one[0] != one[1] and two[0] != two[1]:
        return "changed"
    return "unknown"


def _short_tag(value):
    # type: (str) -> str
    """The part of a nameplate after any team tag: "PWDDA | Ariz" and "Ariz" are one player."""
    return str(value or "").split("|")[-1].strip().lower()


def _name_identity(earlier, later):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """``same`` / ``swapped`` / ``different`` / ``unknown`` between two pairing hypotheses.

    Comparison is tolerant by team tag and by spelling distance, because the header is measured flickering
    Rebix/RebiX and truncating "PWDDA | Ariz" to "PWDDA | Az" **while the round clock keeps falling**. A
    side that was never read is unknown, never a disagreement.
    """
    def one(a, b):
        if not a or not b:
            return None
        if a == b or names.names_close(a, b):
            return "same"
        if _short_tag(a) and _short_tag(a) == _short_tag(b):
            return "same"
        return "different"

    left = one(earlier.get("player1"), later.get("player1"))
    right = one(earlier.get("player2"), later.get("player2"))
    if left == "different" or right == "different":
        crossed = one(earlier.get("player1"), later.get("player2"))
        back = one(earlier.get("player2"), later.get("player1"))
        if crossed == "same" and back == "same":
            return "swapped"
        return "different"
    if left == "same" and right == "same":
        return "same"
    return "unknown"


def _classify_link(previous, following, observations, bracket=None):
    # type: (Dict[str, Any], Dict[str, Any], Sequence[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """One adjacent game pair, judged by combining evidence -- never by obeying one signal.

    Three verdicts. **separated** needs a *match-ending event* plus something independent in the footage
    agreeing with it. **joined** is the format's own prediction where nothing argues against it.
    **ambiguous** is the answer whenever the signals point different ways, and it is not resolved here by
    picking: the caller keeps both groupings as candidates and the disagreement is routed to the specific
    evidence that would settle it.

    The event side used to admit one witness only, a controller screen -- which made a mandatory cue under
    another name, since a recording that never showed a player re-grabbing a stick could not separate at
    all. It admits two now, interchangeable in kind and not in standing: a controller screen is in the
    footage, an event bracket is somebody else's record of it. The footage side of the pair is unchanged --
    a dominant pairing change, or a tally that reset with the same two names on it.

    What this deliberately refuses to do:
    * treat a nameplate change as a boundary -- 21 changes on this recording land mid-round, so a change on
      its own becomes an identity-mapping question, not a cut;
    * treat a side swap as a new set -- it says the screen sides now map to different players, which is a
      question about the participant mapping, and wins still have to be attributed to a persistent
      hypothesis rather than to a side;
    * treat a long dwell as a boundary -- it is worth looking at, which is not the same as being true;
    * treat the score box as anything but a hint, which already split one real set when it was trusted;
    * let a bracket stand alone, or stand for itself: an external record of a score is not independent
      evidence of that score, so a listed match change still has to be agreed with by something on screen.
    """
    earlier = _overlay_pair(observations, previous["start"], previous["end"])
    later = _overlay_pair(observations, following["start"], following["end"])
    identity = _name_identity(earlier, later)
    gap = _gap_cues(observations, previous["end"], following["start"])
    seat = _seat_continuity(earlier, later)
    dwell = round(following["start"] - previous["end"], 3)
    preferred = bool(gap["preferred_cues"])
    corroborated_by_name = identity == "different" and _dominant(earlier) and _dominant(later)
    score = _score_signal(earlier, later)

    # None when no bracket was supplied. That case is not a special value to be handled later, it is the
    # recording-only path, and it has to produce exactly the verdicts it produced before brackets existed.
    shown = bracket_context.link_signal(bracket, [earlier.get("player1"), earlier.get("player2")],
                                        [later.get("player1"), later.get("player2")]) if bracket else None

    # Two families, because one has never been enough. An **ending** says the match could have closed here:
    # a controller screen in the footage, or the event record listing these two pairings as different
    # matches. An **agreement** is something independent on screen that fits that reading: a pairing change
    # on a dominant read, or a tally that reset while the same two names held. A verdict of ``separated``
    # needs one of each and no contradiction; a single family on its own is the ambiguity it always was.
    endings = []
    if preferred:
        endings.append(("controller_screen",
                        "a preferred screen (%s) sits in the gap" % ", ".join(gap["preferred_cues"])))
    if shown and shown["state"] == "different_match":
        endings.append(("bracket_match_change",
                        "the event record lists {} and {} as different matches".format(
                            shown.get("before"), shown.get("after"))))
    agreement = []
    if corroborated_by_name:
        agreement.append(("pairing_change",
                          "the pairing hypothesis changed on a dominant reading on both sides"))
    if identity == "same" and score == "reset":
        agreement.append(("tally_reset",
                          "a tally that reset, with the same pairing named on both sides: consistent with "
                          "a new set between the same two players"))
    contradictions = []
    if shown and shown["state"] == "same_match" and not shown.get("reset_possible"):
        contradictions.append(("bracket_same_match",
                               "the event record lists both pairings under {} and states no reset".format(
                                   shown.get("before"))))

    reasons, questions = [], []
    reasons.extend(text for _, text in endings + agreement)
    if endings and agreement and not contradictions:
        verdict = "separated"
        # The family names go in front of the reasons, so a reader can see which kind of evidence did the
        # work without reading the code: "controller_screen + pairing_change" and
        # "bracket_match_change + tally_reset" are different claims about the world, and when both endings
        # are present a summary that names one of them is not the whole story.
        reasons.insert(0, "two independent signals agree (%s + %s)"
                       % ("|".join(name for name, _ in endings),
                          "|".join(name for name, _ in agreement)))
    elif endings and agreement:
        verdict = "ambiguous"
        reasons.append("the footage and the event record point different ways, so neither is promoted to a "
                       "boundary: " + "; ".join(text for _, text in contradictions))
        questions.append({"kind": "transition_evidence", "at": [gap["first_seen_at"], following["start"]],
                          "route": "header",
                          "because": "the recording and the bracket disagree about whether this gap ends "
                                     "the match",
                          "would_resolve": "the nameplate and win-marker band at each end of the gap, read "
                                           "off the footage rather than from either claim"})
    elif endings:
        verdict = "ambiguous"
        reasons.append("%s sits here but nothing independent agrees: pairing verdict %s, tally %s"
                       % (" and ".join(name for name, _ in endings), identity, score))
        questions.append({"kind": "transition_evidence",
                          "at": [gap["first_seen_at"], following["start"]],
                          "route": "header",
                          "because": "an ending signal alone does not say whether the set ended or the "
                                     "players just picked a stage",
                          "would_resolve": "the nameplate and win-marker band at the transition: which "
                                           "pairing is credited, and whether the set tally moved"})
    elif identity == "different":
        # Names alone. Mid-round changes are measured, so this is a mapping question, not a boundary.
        if gap["within_match_transition"]:
            # The names moved, and the footage says the players did not: the gap shows the game-result
            # screen with no controller screen, and (where it can be read) the same fighters on the same
            # sides. That is continuity, so the link joins and the naming stays a question.
            verdict = "joined"
            reasons.append("the gap holds the game-result screen and no controller screen, so the station "
                           "was not cleared between these games"
                           + (", and each side kept its fighter" if seat == "held" else "")
                           + "; the pairing hypothesis changed (%s -> %s), which is a naming question about "
                             "that link and not evidence that a match ended here"
                             % ([earlier.get("player1"), earlier.get("player2")],
                                [later.get("player1"), later.get("player2")]))
            questions.append({"kind": "identity_mapping", "at": [previous["end"], following["start"]],
                              "route": "header", "seat_continuity": seat,
                              "because": "one side is named as two participants across this gap, so a win "
                                         "on that side cannot be credited to a person until the naming is "
                                         "settled",
                              "would_resolve": "the nameplate band at each game's first live frame, plus "
                                               "the event record's entry for this round"})
        else:
            verdict = "ambiguous"
            reasons.append("the pairing hypothesis changed (%s -> %s) with no match-ending signal between "
                           "the games, and this header is measured naming the next pairing mid-round"
                           % ([earlier.get("player1"), earlier.get("player2")],
                              [later.get("player1"), later.get("player2")]))
            questions.append({"kind": "identity_mapping", "at": [previous["end"], following["start"]],
                              "route": "header",
                              "because": "a nameplate difference is not a boundary; it says the screen "
                                         "sides may now map to different players",
                              "would_resolve": "the nameplate band at each game's first live frame, read "
                                               "at native resolution with the two reads shown side by side"})
    elif identity == "swapped":
        verdict = "ambiguous" if not gap["within_match_transition"] else "joined"
        if verdict == "ambiguous":
            reasons.append("the same two names appear on opposite sides, which is a question about how "
                           "screen sides map to players -- not evidence that a set ended")
            questions.append({"kind": "identity_mapping", "at": [previous["end"], following["start"]],
                              "route": "header", "swap": True,
                              "because": "wins are counted per side; if the players exchanged sticks, the "
                                         "tally has to be re-mapped before either grouping is correct",
                              "would_resolve": "the pairing at each side of the swap, plus whether the "
                                               "round-win pips restart at zero on both sides"})
        else:
            # A swap that the footage contradicts: the fighters stayed where they were, so no sticks were
            # exchanged and the names crossed over people who did not move.
            reasons.append("the two names crossed sides while each side kept its fighter%s, so this is a "
                           "reassignment of who is called what, not two players exchanging sticks; the "
                           "games join and the naming stays open"
                           % (" (" + seat + " seats)" if seat == "held" else ""))
            questions.append({"kind": "identity_mapping", "at": [previous["end"], following["start"]],
                              "route": "header", "swap": True, "seat_continuity": seat,
                              "because": "the same screen side is named as two participants inside one "
                                         "candidate, so the per-participant tally cannot be published even "
                                         "though the per-seat tally can",
                              "would_resolve": "which name the event record gives the player on each side "
                                               "of this station for this round"})
    elif dwell > SET_GAME_GAP_LIMIT:
        verdict = "ambiguous"
        reasons.append("%.1f s of non-play separates the games, longer than a round clock: worth asking "
                       "about, and on its own proving nothing" % dwell)
        questions.append({"kind": "dwell_content", "at": [previous["end"], following["start"]],
                          "route": "state",
                          "because": "a long dwell is usually a menu, an ad or a gap in what was watched, "
                                     "and only sometimes a set boundary",
                          "would_resolve": "whether the stretch holds a set-deciding screen, a pairing "
                                           "change, or dead air"})
    else:
        verdict = "joined"
        reasons.append("no match-ending signal, no dominant pairing change and no abnormal dwell: the "
                       "format predicts these are consecutive games of one set")

    joins = verdict != "separated"
    if joins and score == "reset":
        # Recorded, never acted on: the box disagreed and the disagreement belongs in the record even
        # when the pairing evidence outvotes it.
        reasons.append("the broadcast tally went backwards across the gap, which is a hint against this "
                       "link and nothing more: the box is read off an overlay measured changing mid-round")
    return {"verdict": verdict, "joins": joins, "reasons": reasons, "questions": questions,
            # Positive evidence *for* continuity, as distinct from the absence of evidence against.
            "positively_supported": bool(joins and (
                (identity == "same" and _dominant(earlier) and _dominant(later))
                or score == "advanced" or gap["within_match_transition"])),
            "dwell_seconds": dwell, "score_signal": score,
            "identity": identity, "identity_approximate": True,
            "seat_continuity": seat,
            "fighters": {"before": (earlier.get("characters") or {}),
                         "after": (later.get("characters") or {})},
            "transition": {"within_match": gap["within_match_transition"],
                           "station_cleared": gap["station_cleared"],
                           "states": gap["states"]},
            "identity_share": {"before": earlier.get("share"), "after": later.get("share")},
            "identities": {"before": [earlier.get("player1"), earlier.get("player2")],
                           "after": [later.get("player1"), later.get("player2")]},
            "named_rows": {"before": earlier.get("named_rows"), "after": later.get("named_rows")},
            "scores": {"before": earlier.get("score_last"), "after": later.get("score_first")},
            "gap": gap, "at": previous["end"], "before_game": previous["start"],
            "after_game": following["start"],
            # Which evidence families were consulted and which objected, by name, so a verdict can be
            # re-examined without reading the code that produced it.
            "evidence_for": [name for name, _ in endings + agreement],
            "evidence_against": [name for name, _ in contradictions],
            "bracket": shown,
            "basis": ("recording_only" if not bracket else
                      ("bracket_assisted" if shown and shown["state"] != "unknown"
                       else "bracket_supplied_but_silent"))}


def _dominant(read):
    # type: (Dict[str, Any]) -> bool
    return all((read.get("share") or {}).get(side) is not None
               and read["share"][side] >= NAME_DOMINANT_SHARE for side in ("player1", "player2"))


def _score_signal(earlier, later):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """What the broadcast tally appears to have done. A hint only: it was trusted once and split a set."""
    a, b = earlier.get("score_last"), later.get("score_first")
    if not a or not b:
        return "unknown"
    if b[0] <= a[0] and b[1] <= a[1] and (b[0] < a[0] or b[1] < a[1]):
        return "reset"
    if b[0] >= a[0] and b[1] >= a[1] and (b[0] > a[0] or b[1] > a[1]):
        return "advanced"
    return "unchanged"


def _header_route(header):
    # type: (Optional[Dict[str, Any]]) -> Dict[str, Any]
    """How the band took part: read-only reuse of answers that already exist, or not at all."""
    if not header:
        return {"supplied": False, "route": "no_band_evidence"}
    return {"supplied": True, "route": "saved_readings_reused",
            "readings": len(header.get("readings") or ()),
            "source_directory": header.get("source_directory"),
            "provenance": header.get("provenance"),
            "inference_sent": False, "attempts_charged_now": 0,
            "resolves": "which identity sat on which screen side, where a reading falls inside the game",
            "never_resolves": ["whether a set ended", "a game or round result",
                               "the number of games in a candidate"]}


def _bracket_expectation(bracket, record):
    # type: (Optional[Dict[str, Any]], Dict[str, Any]) -> Optional[Dict[str, Any]]
    """What the event record implies for this candidate, beside what the recording actually found.

    Reported so a short candidate reads as a named gap -- "three games seen, five expected" -- and never as
    a completed set or a missing one. The expectation cannot verify itself: a reported 3-2 is the source of
    the number of games it asks for, so agreement is consistency with an input, not independent proof.
    """
    if not bracket:
        return None
    seen = []
    for entry in record.get("identities") or ():
        for side in ("before", "after"):
            seen.extend([n for n in (entry.get(side) or []) if n])
    pairing = list(dict.fromkeys(seen))[:2] or None
    stated = bracket_context.expectation(bracket, pairing)
    if not stated:
        return {"supplied": True, "match": None, "games_expected": None,
                "because": "this candidate's pairing hypothesis does not resolve to exactly one listed "
                           "match, so the bracket is silent about its length"}
    games_seen = int(record.get("games") or 0)
    return dict(stated, supplied=True, games_seen=games_seen,
                games_unaccounted=max(0, int(stated["expected_games"]) - games_seen),
                gap_is_a_question=games_seen < int(stated["expected_games"]),
                independent_of_recording=False)


def _bucket_formats(header, policy, games_per_set, lo, hi, stage_readings=None):
    # type: (Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[int], float, float, Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]
    """Which target(s) apply to one candidate, from the band readings inside that candidate's own span.

    A stage is selected per candidate rather than per run because one interval crosses matches: the
    quarter-final that closes 2-0 and the Winners Final that needs three are different formats, and a
    single global target can only be wrong about one of them. With no policy the caller's configured
    target is used exactly as it always was, and with neither there is no target -- which is reported,
    never guessed.

    ``stage_readings`` exists because the stage text and the pairing claim arrive in the same answer but
    decide different things. A run that withholds the band's *names* to measure their effect must not
    thereby lose the *round* the band was naming, or the comparison would be between two formats rather
    than between two readings of one recording; callers that split the two pass the readings here.
    """
    readings = (stage_readings if stage_readings is not None
                else (header or {}).get("readings") or ())
    stages = [(r.get("at"), ((r.get("pairing") or {}).get("label")))
              for r in readings
              if r.get("at") is not None and float(lo) <= float(r["at"]) <= float(hi)]
    if policy is not None:
        formats = boundary_format.applicable_formats(stages, policy)
        if formats["targets"]:
            return formats
    if games_per_set:
        return {"targets": [int(games_per_set)], "ambiguous": False, "format_known": True,
                "basis": "games_per_set configured by the caller", "evidence": {
                    str(games_per_set): [{"at": None, "stage": "configured"}]},
                "readings_without_a_stage": [], "policy_source": None,
                "stage_vocabulary_applied": False}
    return {"targets": [], "ambiguous": False, "format_known": False,
            "basis": "no format supplied and no stage named inside this span",
            "evidence": {}, "readings_without_a_stage": [], "policy_source": None,
            "stage_vocabulary_applied": False}


def _reached_under(targets, deciding, attributed=None):
    # type: (Sequence[int], Dict[str, int], Optional[Dict[str, Any]]) -> Tuple[List[int], List[int]]
    """Split the applicable targets into the ones this tally satisfies and the ones it does not.

    A tally that cannot be read as any one person's wins satisfies nothing, however large its maximum.
    That is the case when a screen side is credited to two participants and the recording does not show the
    seat keeping its player: two side-wins across a seat exchange are 1-1, and letting the number 2 reach a
    target would turn the tracker's own unit confusion into a won set.
    """
    if not deciding:
        return [], list(targets)
    if (attributed or {}).get("person_tally_possible") is False:
        return [], list(targets)
    top = max(deciding.values())
    return ([t for t in targets if top >= t], [t for t in targets if top < t])


def _group_into_sets(games, games_per_set, observations=(), clip_pad_seconds=CLIP_PAD_SECONDS,
                     bracket=None, header=None, format_policy=None, stage_readings=None,
                     analysis_window=None, source=None):
    # type: (List[Dict[str, Any]], Optional[int], Sequence[Dict[str, Any]], float, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Sequence[Dict[str, Any]]], Optional[Sequence[float]], Optional[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Bundle games into candidate sets from combined evidence, and keep every reading the evidence allows.

    The failure this replaces was a silent fusion: wins accumulated across whatever games were adjacent,
    so one bracket match's game and the next match's three games became a single 2-2 "set" that could
    never close and never reported a fault. Adjacency is not membership.

    The failure this must not replace it with is inventing boundaries. So a candidate is split only where
    the evidence **separates** (a match-ending signal plus something independent agreeing); a link that is
    merely questionable is kept joined and recorded as an `alternative_grouping`, which is the other reading
    of the same games. Both readings cover the identical set of games, so no gameplay is ever dropped by a
    cut, and a reviewer sees the disagreement instead of a single confident choice.

    ``games_per_set=None`` means the format was never configured. That is reported, not defaulted: a set
    cannot be closed against a target nobody supplied, and inventing "3" is how a bracket match gets
    quietly reinterpreted as several sets. ``bracket`` is the optional event record; with no bracket the
    grouping is exactly the recording-only judgement it was before that input existed.
    """
    if not games:
        return []
    # Computed over every game, not per bucket: the mapping question sits on the link that split them, so
    # answering it needs the reading from the candidate on the other side of that cut.
    band_map = _band_map(header, games)
    pairings = _overlay_pairings(observations, games)
    if observations:
        links = [_classify_link(a, b, observations, bracket=bracket) for a, b in zip(games, games[1:])]
    else:
        links = [{"verdict": "ambiguous", "joins": True, "reasons": ["no observations were supplied, so "
                                                                     "membership was never checked"],
                  "questions": [], "identity": "unknown", "score_signal": "unknown", "dwell_seconds": None,
                  "evidence_for": [], "evidence_against": [], "bracket": None,
                  "basis": "recording_only", "gap": {}, "identities": {}, "at": a["end"]}
                 for a in games[:-1]]

    # Primary reading: games stay together only where the evidence positively joins them. An ambiguous
    # link is cut in the primary and its joined reading is kept as an alternative, because the two errors
    # are not symmetric: a false split yields two partial candidates and a named question, while a false
    # merge fabricates one entity that no longer reports the doubt -- the exact defect this function exists
    # to stop.
    buckets, current, inside = [], [games[0]], []
    cut_before = None  # type: Optional[Dict[str, Any]]
    for link, game in zip(links, games[1:]):
        # Measured on the deciding tally: a 3-0 taken while alternating sides is a won set, and closing
        # candidates against side arithmetic alone would leave it open forever. Only *supported*
        # identities change the figure -- see _deciding_wins.
        formats = _bucket_formats(header, format_policy, games_per_set,
                                  current[0]["start"], current[-1]["end"], stage_readings)
        deciding, _, attributed = _deciding_wins(current, band_map,
                                                 _resolve_mapping_questions(inside, band_map))
        closed, open_under = _reached_under(formats["targets"], deciding, attributed)
        if formats["targets"] and closed and not open_under:
            closure = {"verdict": "target_reached", "at": current[-1]["end"], "reasons":
                       ["every target this candidate's own stage readings admit (%s) is reached, so this "
                        "candidate closed and the next game begins a new one"
                        % ", ".join("first to %d" % t for t in closed)],
                       "questions": [], "gap": {},
                       "evidence_for": ["configured_target"], "evidence_against": [],
                       "bracket": None, "basis": "recording_only", "identities": {}}
            buckets.append((current, inside, cut_before, closure))
            # The link after a closed candidate is the boundary that closed it, not evidence about the
            # membership of what follows. Carrying it into the next bucket's own links used to mark that
            # candidate `contested` on the strength of a link it does not contain -- and a candidate can
            # only be joined or not by the gaps *inside* it.
            current, inside, cut_before = [game], [], closure
            continue
        elif link["verdict"] != "joined":
            buckets.append((current, inside, cut_before, link))
            current, inside, cut_before = [game], [], link
            continue
        current.append(game)
        inside.append(link)
    buckets.append((current, inside, cut_before, None))

    sets_out = []
    for bucket, inside, before, after in buckets:
        formats = _bucket_formats(header, format_policy, games_per_set,
                                  bucket[0]["start"], bucket[-1]["end"], stage_readings)
        # `after` is None for the candidate that runs to the end of what was read, and that is a different
        # claim from a bound the recording argues for: the edges function has to tell "nothing observed"
        # apart from "the players put the controllers down".
        #
        # The walk is also bounded by the games this bucket does *not* contain: an edge stops at the opening
        # of the next candidate and the close of the previous one, so a cue from somebody else's transition
        # can never be reached by walking far enough in the wrong direction.
        opening = games.index(bucket[0])
        closing = games.index(bucket[-1])
        edges = _match_edges(bucket, observations, cut_before=before, cut_after=after, bracket=bracket,
                             analysis_window=analysis_window, source=source,
                             previous_game=games[opening - 1] if opening else None,
                             next_game=games[closing + 1] if closing + 1 < len(games) else None)
        if not inside:
            membership = "single"
        elif any(link["verdict"] != "joined" for link in inside):
            membership = "contested"
        elif any(link.get("positively_supported") for link in inside):
            # Something argued *for* these being one set: a pairing hypothesis that held on a dominant
            # reading, or a tally that moved. Not merely the absence of a counter-argument.
            membership = "supported"
        else:
            # Every link joined because nothing contradicted it. That is the format's default, and it is
            # worth reporting as such: calling it support is how "we never saw evidence against" becomes
            # "we have evidence for", which is the move that fused the two matches in the first place.
            membership = "assumed"
        record = _set_record(bucket, games_per_set, membership=membership, links=inside,
                             single=len(bucket) == 1, clip_pad_seconds=clip_pad_seconds,
                             cut_before=before,
                             cut_after=None if (after or {}).get("verdict") == "target_reached"
                             else after,
                             header=header, band_map=band_map, pairings=pairings, formats=formats,
                             edges=edges)
        record["_before"], record["_after"] = before, after
        sets_out.append(record)
    # Each ambiguous link the primary cut is recorded as the reading it foregoes: the same games, joined.
    # Neither reading is discarded, and the primary is only the one that never invents an entity. The
    # alternative names the two games it would merge and the evidence that would settle it, which is what
    # a follow-up pass routes to.
    for record in sets_out:
        alternatives = []
        for link, side in ((record.get("_before"), "previous"), (record.get("_after"), "next")):
            if not link or link.get("verdict") != "ambiguous":
                continue
            entry = {
                "joined_with": side, "at": link["at"],
                "would_merge": [link.get("before_game"), link.get("after_game")],
                "reading": "these games are consecutive games of one set, and the boundary is elsewhere",
                "reason": (link["reasons"] or [""])[0],
                "questions": link.get("questions") or [],
                "why_not_primary": "joining here would assert the absence of a boundary the evidence does "
                                   "not show; splitting keeps the doubt visible and the games covered"}
            if header:
                # This is the reading where a side swap actually bites: two games under one tally, counted
                # on screen sides that may name different people. The band answers *who*; it is not asked
                # *whether*, and the verdict above is untouched either way.
                resolved_here = _resolve_mapping_questions([link], band_map)
                merged = [g for g in games if g["start"] in (link.get("before_game"),
                                                             link.get("after_game"))]
                attributed_merged = _attributed_wins(merged, band_map, resolved_here)
                entry["band_mapping"] = resolved_here[0] if resolved_here else None
                entry["band_attribution_if_joined"] = attributed_merged
                entry["band_mapping_is_approximate"] = True
            alternatives.append(entry)
        record["alternative_groupings"] = alternatives
        record["ambiguous_links_cut"] = len(alternatives)
        # A disputed game boundary is an alternative grouping of exactly the same kind -- a reading where
        # two games are one -- so it joins the list the projection and the budget already consume instead
        # of opening a parallel channel nobody reads.
        candidate_starts = {span[0] for span in record["game_spans"]}
        boundary_alternatives = _boundary_alternatives(
            [game for game in games if game["start"] in candidate_starts])
        if boundary_alternatives:
            record["alternative_groupings"] = alternatives + boundary_alternatives
            record["grouping_unresolved"] = True
        record.pop("_before", None)
        record.pop("_after", None)
    for record in sets_out:
        record["games_covered"] = record["games"]
        # Questions from inside the candidate and from the cuts that formed it, in time order: a cut is
        # where the disagreement lives, so it is the more important half of the list.
        cut_questions = [q for key in ("cut_before", "cut_after")
                         for q in ((record.get(key) or {}).get("questions") or [])]
        record["questions"] = sorted((record.get("questions") or []) + cut_questions,
                                     key=lambda q: (q.get("at") or [0.0])[0]
                                     if isinstance(q.get("at"), list) else 0.0)
        record["link_verdicts_all"] = (
            [(record.get("cut_before") or {}).get("verdict")]
            + [link["verdict"] for link in record["links"]]
            + [(record.get("cut_after") or {}).get("verdict")])
    return sets_out


def _wins_of(games):
    # type: (Sequence[Dict[str, Any]]) -> Dict[str, int]
    """Per-side uncontested game wins in a candidate.

    Sides are screen positions, so this is the *unattributed* tally: `_attribution` says whether it can be
    read as per-player wins at all.
    """
    wins = {"p1": 0, "p2": 0}
    for entry in games:
        # Only an uncontested winner counts. A game whose own readings disagree is evidence that a game
        # happened, never evidence that a particular side won it.
        if entry.get("counts_toward_set") and entry.get("winner") in wins:
            wins[entry["winner"]] += 1
    return wins


def _overlay_pairings(observations, games):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> Dict[float, Dict[str, Any]]
    """What each game's own classification rows say about who sat where.

    The candidate-level rule used to be "no link contradicted one pairing", which a candidate of unreadable
    nameplates satisfies by having nothing to contradict. This is the positive version: a game counts as
    identified by the overlay only when its rows dominantly name *both* sides.
    """
    if not observations:
        return {}
    out = {}
    for entry in games:
        pair = _overlay_pair(observations, entry["start"], entry["end"])
        out[round(float(entry["start"]), 6)] = {
            "sides": {"p1": pair.get("player1"), "p2": pair.get("player2")},
            "dominant": _dominant(pair),
            "share": pair.get("share"),
            "named_rows": pair.get("named_rows"),
        }
    return out


def _band_map(header, games):
    # type: (Optional[Dict[str, Any]], Sequence[Dict[str, Any]]) -> Dict[float, Dict[str, Any]]
    """Each game's screen-side identities, keyed by the moment the game opens.

    Keyed rather than positional: a candidate is a slice of the games, and an attribution rule that had to
    remember where the slice came from is a rule that will eventually read the wrong game's nameplate.
    """
    return {round(float(entry["start"]), 6): cover
            for entry, cover in zip(games, _band_side_map(header, games)) if cover}


def _band_side_map(header, games):
    # type: (Optional[Dict[str, Any]], Sequence[Dict[str, Any]]) -> List[Optional[Dict[str, Any]]]
    """Which identity the broadcast credited on which screen side, for each game in a candidate.

    A band reading is attributed to a game only when its instant falls inside that game's span; the
    nearest reading outside every span is *not* silently borrowed, because a reading taken after the
    players exchanged sticks describes the game behind it. The result is positional per game: ``None``
    where no reading covers it, which the caller must treat as unresolved rather than as agreement.
    """
    if not header:
        return [None for _ in games]
    readings = header.get("readings") or ()
    out = []
    for entry in games:
        span = [float(entry["start"]), float(entry["end"])]
        inside = [r for r in readings if r.get("at") is not None
                  and span[0] <= float(r["at"]) <= span[1]]
        if not inside:
            out.append(None)
            continue
        covers = [_band_cover(reading) for reading in inside]
        usable = [c for c in covers if c["usable"]]
        # Distinct arrangements among the usable readings, merged tolerantly so RebiX/Rebix is one
        # arrangement and not two.
        arrangements = []
        for cover in usable:
            if not any(_same_arrangement(a, cover) for a in arrangements):
                arrangements.append(cover)
        agree = [c for c in usable
                 if arrangements and _same_arrangement(arrangements[0], c)] if len(arrangements) <= 1 \
            else []
        cover = {
            "game_start": entry["start"],
            "readings_inside": len(inside),
            # The arrangement every agreeing reading reported, or None when they did not agree. A game
            # with two different nameplates inside it is not a game whose sides are known.
            "arrangements": len(arrangements),
            "sides": arrangements[0]["sides"] if len(arrangements) == 1 else None,
            "usable": len(arrangements) == 1,
            "at": arrangements[0]["at"] if len(arrangements) == 1 else None,
            "image_sha256": arrangements[0]["image_sha256"] if len(arrangements) == 1 else None,
            "support": len(agree) if len(arrangements) == 1 else 0,
            "games_displayed": arrangements[0]["games_displayed"] if len(arrangements) == 1 else None,
            "confidence": arrangements[0]["confidence"] if len(arrangements) == 1 else None,
            "conflicting_readings": [c for c in covers if c["usable"]] if len(arrangements) > 1 else [],
            "unreadable_readings": [c for c in covers if not c["usable"]],
            "why_unusable": None if len(arrangements) == 1 else
            _band_cover_reason(covers, arrangements, entry["start"]),
            "pips_unvalidated": (arrangements[0]["pips_unvalidated"] if len(arrangements) == 1
                                 else covers[0]["pips_unvalidated"]),
            "source": "header_band_saved",
        }
        out.append(cover)
    return out


def _band_cover(reading):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """One reading's claim about the two screen sides, with the answer's own usability respected.

    ``usable`` is not "two non-empty strings came back". A low-confidence name is exactly what this band
    route was built to refuse to treat as an identity, and the reply already says so through
    ``mapping_usable`` -- which is the field ``read_pairings`` derives from the per-side confidence floor.
    Ignoring it would let a 0.2 transcription credit a win to a person.
    """
    pairing = reading.get("pairing") or {}
    left, right = (pairing.get("left") or {}), (pairing.get("right") or {})
    named = [left.get("player"), right.get("player")]
    return {
        "at": reading.get("at"), "image_sha256": reading.get("image_sha256"),
        # p1 is the left nameplate and p2 the right, the same convention the win-dot rows use.
        "sides": {"p1": named[0], "p2": named[1]},
        "games_displayed": pairing.get("tally"),
        "confidence": [left.get("confidence"), right.get("confidence")],
        "usable": bool(pairing.get("mapping_usable")) and all(named),
        "mapping_usable": bool(pairing.get("mapping_usable")),
        "named_both_sides": all(named),
        "pips_unvalidated": pairing.get("pips_unvalidated"),
    }


def _same_arrangement(one, other):
    # type: (Dict[str, Any], Dict[str, Any]) -> bool
    return _names_agree(one["sides"]["p1"], other["sides"]["p1"]) \
        and _names_agree(one["sides"]["p2"], other["sides"]["p2"])


def _band_cover_reason(covers, arrangements, game_start):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]], float) -> str
    """Which of the three ways a watched game can still be unidentified, named separately.

    "Nobody read it", "nobody could read it" and "two readings disagree" need different follow-up
    observations, so they must not collapse into one reason string -- and each has to name the game it
    describes, because these clauses get joined across a pair of games.
    """
    at = "game at %s s" % round(float(game_start), 2)
    if not any(c["usable"] for c in covers):
        if not any(c["named_both_sides"] for c in covers):
            return "%s: its reading(s) named only one side, or none" % at
        return ("%s: it named both sides but not at a confidence that can carry an identity "
                "(mapping_usable was false)" % at)
    return "%s: its %d readings name different pairings" % (at, len(arrangements))


def _band_sides(band_map, game):
    # type: (Dict[float, Dict[str, Any]], Dict[str, Any]) -> Optional[Dict[str, str]]
    """The screen-side identities of a game, when one unconflicting usable reading states them."""
    cover = band_map.get(round(float(game["start"]), 6))
    if not cover or not cover["usable"]:
        return None
    return cover["sides"]


def _resolve_mapping_questions(links, band_map):
    # type: (Sequence[Dict[str, Any]], Dict[float, Dict[str, Any]]) -> List[Dict[str, Any]]
    """Answer each identity question from the band, or say which footage would answer it.

    Outcomes, and they are not interchangeable:

    * ``swap_confirmed`` - the same two identities, opposite sides. The mapping is established, so a win
      follows the person rather than the screen position, which is precisely what the side-swap case was
      left unresolved for;
    * ``pairing_change_confirmed`` - the band names a different pairing on the far side, so each game's
      sides are separately known. Still not a boundary: a nameplate change without an ending signal is a
      mapping fact, and the grouping verdict stands as it was made;
    * ``steady_confirmed`` - the same identities on the same sides, which retires the question without
      changing anything;
    * ``contested`` - the band and the overlay hypothesis disagree about who was on which side. The
      question stays open, and the band does not win by being newer;
    * ``uncovered`` - no band reading inside one of the two games. This is a missing observation, named as
      one, and it is what a priced trial is for.
    """
    resolved = []
    for link in links:
        questions = [q for q in (link.get("questions") or []) if q.get("kind") == "identity_mapping"]
        if not questions:
            # Recorded rather than skipped: a reader looking at a cut needs to know the band was asked
            # nothing here, not to find a hole where an answer might have been.
            if link.get("questions"):
                resolved.append({"at": link.get("at"), "status": "no_mapping_question",
                                 "because": "this link's open question is %s, not who sat on which side"
                                            % ", ".join(sorted({str(q.get("kind")) for q in
                                                                link.get("questions") or ()})),
                                 "identity_from_overlay": link.get("identity")})
            continue
        # The link names the two games it sits between, so the lookup survives bucketing: the question
        # lives on the *cut*, and the games it compares are in different candidates.
        def cover(key):
            # `is not None`, not truthiness: the first game of a recording opens at 0.0, and treating that
            # as "no game named" silently uncovered exactly the boundary a trial is most likely to ask about.
            value = link.get(key)
            return band_map.get(round(float(value), 6)) if value is not None else None
        before, after = cover("before_game"), cover("after_game")
        record = {"at": link.get("at"), "identity_from_overlay": link.get("identity"),
                  # Carried so the attribution layer can tell a renamed seat from an exchanged one: two
                  # wins on the same side are one person's two wins only while that side kept its player.
                  "seat_continuity": link.get("seat_continuity"),
                  "overlay_identities": link.get("identities")}
        if not before or not after:
            missing = [name for name, cover in (("earlier game", before), ("later game", after))
                       if not cover]
            record.update({"status": "uncovered",
                           "because": "no band reading falls inside the %s, so the side mapping across "
                                      "this gap was never observed" % " and the ".join(missing),
                           "needs_footage": {"from": link.get("before_game"), "to": link.get("after_game"),
                                             "route": "header"}})
            resolved.append(record)
            continue
        if not (before["usable"] and after["usable"]):
            # A game the band looked at and could not settle is not the same event as a game it never
            # saw, and it is certainly not a settled mapping: conflicting or unusable readings stay open.
            record.update({"status": "contested" if (before["conflicting_readings"]
                                                     or after["conflicting_readings"]) else "uncovered",
                           "because": "; ".join(filter(None, [(before.get("why_unusable")),
                                                              (after.get("why_unusable"))]))
                           or "a band reading covers both games but did not name both sides",
                           "band": {"before": _band_record(before), "after": _band_record(after)},
                           "needs_footage": {"from": link.get("before_game"),
                                             "to": link.get("after_game"), "route": "header"}})
            resolved.append(record)
            continue
        one, two = before["sides"], after["sides"]
        forward = _names_agree(one["p1"], two["p1"]) and _names_agree(one["p2"], two["p2"])
        crossed = _names_agree(one["p1"], two["p2"]) and _names_agree(one["p2"], two["p1"])
        overlay = link.get("identities") or {}
        overlay_before = overlay.get("before") or [None, None]
        overlay_after = overlay.get("after") or [None, None]
        agrees_with_overlay = all(_names_agree(band, shown) for band, shown in
                                  ((one["p1"], overlay_before[0]), (one["p2"], overlay_before[1]),
                                   (two["p1"], overlay_after[0]), (two["p2"], overlay_after[1])))
        record["band"] = {"before": _band_record(before), "after": _band_record(after),
                          "before_at": before["at"], "after_at": after["at"],
                          "images": [before.get("image_sha256"), after.get("image_sha256")],
                          # Agreement is countable, and a reader should be able to tell one reading from
                          # four that all said the same thing.
                          "support": [before.get("support"), after.get("support")]}
        if not agrees_with_overlay:
            # The band and the overlay hypothesis name different people for the same games. Neither is
            # promoted: the disagreement is the finding, and it is handed to the footage that could see
            # both.
            record.update({"status": "contested",
                           "because": "the band and the pairing hypothesis built from the classification "
                                      "rows name different players for these games",
                           "needs_footage": {"from": link.get("before_game"),
                                             "to": link.get("after_game"), "route": "header"}})
        elif crossed:
            record.update({"status": "swap_confirmed",
                           "because": "the same two identities are credited on opposite sides, so a win "
                                      "belongs to the player named on the winning side of each game, not "
                                      "to the side number",
                           "mapping_changed_at": link.get("at")})
        elif forward:
            status = "steady_confirmed" if link.get("identity") == "same" else "pairing_change_confirmed"
            record.update({"status": status,
                           "because": ("the band names the same pairing on the same sides on both sides "
                                       "of the gap" if status == "steady_confirmed" else
                                       "the band names a different pairing in the later game, so each "
                                       "game's sides are separately identified")})
        else:
            record.update({"status": "contested",
                           "because": "the two readings neither hold the sides nor exchange them, which "
                                      "is a fourth state nobody has evidence for",
                           "needs_footage": {"from": link.get("before_game"),
                                             "to": link.get("after_game"), "route": "header"}})
        resolved.append(record)
    return resolved


def _band_record(cover):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """What the band said about one game, minus the internal bookkeeping."""
    return {"sides": cover.get("sides"), "at": cover.get("at"),
            "usable": cover.get("usable"), "readings_inside": cover.get("readings_inside"),
            "arrangements": cover.get("arrangements"), "support": cover.get("support"),
            "confidence": cover.get("confidence"),
            "conflicting": [{"at": c.get("at"), "sides": c.get("sides"),
                             "image_sha256": c.get("image_sha256")}
                            for c in cover.get("conflicting_readings") or []]}


def _names_agree(one, other):
    # type: (Optional[str], Optional[str]) -> bool
    if not one or not other:
        return False
    return names.names_close(names.normalise_name(one), names.normalise_name(other))


def _attributed_wins(games, band_map, contested=()):
    # type: (Sequence[Dict[str, Any]], Dict[float, Dict[str, Any]], Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]
    """Per-identity game wins for one candidate, or the reason they cannot be published.

    Three answers, deliberately distinct. ``None`` means there is nothing to attribute (no band, or no
    counted winner). ``{"withheld": why}`` means wins exist and *cannot* be credited -- a side nobody
    identified, a reading that came back unusable, two readings inside one game naming different
    pairings, or a mapping question the band and the footage answered differently. A tally dict means
    every counted winner's side was named by a usable, unconflicting reading.

    A partial attribution is worse than none: two wins credited to a named player and one silently dropped
    is a scoreline that looks settled, and a settled-looking score is what stops anyone going to look.
    """
    if not band_map:
        return None
    blocked = [entry for entry in contested if entry.get("status") == "contested"]
    if blocked:
        return {"withheld": "the band and the pairing hypothesis disagree about %s, so neither is "
                            "promoted to a per-player credit"
                % ", ".join(str(round(float(entry["at"]), 2)) for entry in blocked)}
    # Everyone the band named on either side of any game in this candidate, so the figure is a score and
    # not a list of winners: a reader who sees {Alice: 3} is entitled to ask whether Beth played.
    tally = {}   # type: Dict[str, int]
    for entry in games:
        cover = band_map.get(round(float(entry["start"]), 6))
        if cover and cover["usable"]:
            for side in ("p1", "p2"):
                named = cover["sides"][side]
                if named:
                    key = next((k for k in tally if _names_agree(k, named)), named)
                    tally.setdefault(key, 0)
    unattributed = 0
    per_game = []
    for entry in games:
        cover = band_map.get(round(float(entry["start"]), 6))
        winner = entry.get("winner")
        if not (entry.get("counts_toward_set") and winner in ("p1", "p2")):
            unattributed += 1
            continue
        if not cover:
            return {"withheld": "the game at %s s was won on a screen side no band reading falls inside, "
                                "so nobody is known to have won it"
                    % round(float(entry["start"]), 2)}
        if not cover["usable"]:
            return {"withheld": "a counted win at %s s cannot be credited: %s"
                    % (round(float(entry["start"]), 2), cover.get("why_unusable"))}
        identity = cover["sides"][winner]
        key = next((k for k in tally if _names_agree(k, identity)), identity)
        tally[key] = tally.get(key, 0) + 1
        per_game.append({"game_start": entry["start"], "winner_side": winner, "credited_to": key,
                         "from_band_at": cover["at"], "crop_sha256": cover.get("image_sha256"),
                         "readings_agreeing": cover.get("support"),
                         "confidence": cover.get("confidence")})
    if not per_game:
        # Nothing was credited, so nothing may be *called* credited -- including a roster of names at zero
        # each. An empty identity tally printed as a per-player attribution is how "we found no winners"
        # starts being read as "the score is level", and the unit field would then claim a basis the
        # candidate never used.
        return None
    # One screen side credited to two different participants inside one candidate. Nothing separated those
    # games -- a match-ending signal would have cut this candidate before this point -- so the seat kept its
    # player while the broadcast renamed it. Publishing the identity figure regardless would score a side
    # that won both games as 1-1, which turns the overlay's naming error into a scoreboard: the exact
    # invention this function exists to refuse. So the names come back as hypotheses, and the per-seat
    # tally the caller falls through to stays the number of facts.
    by_side = {}
    for entry in per_game:
        by_side.setdefault(entry["winner_side"], []).append(entry)
    renamed = {side: entries for side, entries in by_side.items()
               if len({entry["credited_to"] for entry in entries}) > 1}
    if renamed:
        # Whether a seat's wins belong to one person depends on whether the seat kept its player, and the
        # recording says which it was: the fighters held their sides at 3,373->3,394 s, so two wins on one
        # side are two wins by one person whoever the nameplate called them. Had the fighters crossed sides,
        # the players changed seats, and the same two side-wins would belong to two people -- a 1-1, not a
        # set. Read from the link records the mapping questions carry; "unknown" never earns a closure.
        continuity = [entry.get("seat_continuity") for entry in contested
                      if entry.get("kind") != "no_mapping_question"] or ["unknown"]
        held = all(value == "held" for value in continuity)
        reasons = []
        for side, entries in sorted(renamed.items()):
            reasons.append(
                "the %s side won %d of this candidate's games and the band called it %s, naming it %s at "
                "%s s and %s at %s s: %s"
                % (side, len(entries), " and ".join(sorted({e["credited_to"] for e in entries})),
                   entries[0]["credited_to"], round(float(entries[0]["game_start"]), 2),
                   entries[-1]["credited_to"], round(float(entries[-1]["game_start"]), 2),
                   "one seat cannot be two people unless its player moved, and the fighters say it did not"
                   if held else
                   "and nothing on screen says whether that side kept its player, so the wins cannot be "
                   "added up as one person's"))
        return {
            "withheld": "; ".join(reasons),
            "participant_hypotheses": {
                side: [{"name": entry["credited_to"], "game_start": entry["game_start"],
                        "band_at": entry["from_band_at"], "crop_sha256": entry["crop_sha256"]}
                       for entry in entries] for side, entries in sorted(renamed.items())},
            "seat_continuity": "held" if held else (continuity[0] if continuity else "unknown"),
            # The per-seat tally counts one person's wins only while the seat held its player. Where that
            # is not established, 2-0 on sides is not 2 games for anybody and no target may be met on it.
            "person_tally_possible": held,
            "per_game": per_game, "unattributed_games": unattributed,
            "unit": "screen_side_with_participant_hypotheses", "approximate": True,
            "games_credited": len(per_game)}
    if not tally:
        return None
    return {"game_wins_by_identity": tally, "per_game": per_game, "unattributed_games": unattributed,
            # The name came off a screen, not out of a roster: it identifies a broadcast participant
            # hypothesis, and a mis-spelled tag is still the same hypothesis, not a new player.
            "unit": "broadcast_identity_hypotheses",
            "approximate": True,
            "support": {"games_credited": len(per_game),
                        "readings_used": sum(1 for g in per_game if g.get("from_band_at") is not None),
                        "games_with_agreeing_duplicate_readings":
                            sum(1 for g in per_game if (g.get("readings_agreeing") or 0) > 1)},
            "note": "wins are credited to the identity the scoreboard named on the winning side of each "
                    "game; the pip rows that decided the win are the crop arm's, never the band's"}


def _deciding_wins(games, band_map, contested=()):
    # type: (Sequence[Dict[str, Any]], Dict[float, Dict[str, Any]], Sequence[Dict[str, Any]]) -> Tuple[Dict[str, int], str, Optional[Dict[str, Any]]]
    """The tally a set is measured against, and what it is a tally *of*.

    Per identity whenever every counted winner's side is identified; per screen side otherwise. This is
    not a cosmetic preference. A player who takes a first-to-three set while trading sides reads 2-1 on
    sides and 3-0 on people, and a target tested against the side figure never closes -- which is the same
    class of error as the fusion this layer was built to stop, only in the direction that hides a won set
    instead of inventing one.
    """
    attributed = _attributed_wins(games, band_map, contested)
    if attributed and "game_wins_by_identity" in attributed:
        return dict(attributed["game_wins_by_identity"]), "identities", attributed
    return _wins_of(games), "sides", attributed


def _reached(games, games_per_set, band_map=None, contested=()):
    # type: (Sequence[Dict[str, Any]], Optional[int], Optional[Dict[float, Dict[str, Any]]], Sequence[Dict[str, Any]]) -> bool
    if not games_per_set:
        return False          # no configured target, so nothing has "reached" it
    deciding, _, _ = _deciding_wins(games, band_map or {}, contested)
    return bool(deciding) and max(deciding.values()) >= games_per_set


def _attribution(games, links, header=None, band_map=None, mapping_questions=None, attributed=None,
                 pairings=None):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[float, Dict[str, Any]]], Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]], Optional[Dict[float, Dict[str, Any]]]) -> Dict[str, Any]
    """Can wins be credited to participants, or only to screen sides?

    A swap or an unresolved pairing change means p1/p2 may name different humans in different games, and
    summing them into one tally is how two matches became a fictitious 2-2 set. When that happens the side
    tally stays -- it is the observation -- but it is labelled side-only, and no candidate may call itself
    won by a person on the strength of it.

    ``header`` is a read-only set of saved band readings (``boundary_header.saved_readings``), and it is
    consulted for **who**, never for **whether**: the verdicts in ``links`` are not a function of it. A
    reading has to be usable in the answer's own terms -- ``mapping_usable``, which is the confidence floor
    -- and a game holding two readings that name different pairings is contested, not a coin flip resolved
    by whichever row sorts first.
    """
    mapping = [link for link in links if link.get("identity") in ("swapped", "different")
               or any(q.get("kind") == "identity_mapping" for q in link.get("questions") or [])]
    band_map = dict(band_map or {})
    resolved = list(mapping_questions if mapping_questions is not None
                    else _resolve_mapping_questions(links, band_map))
    credited = [entry for entry in resolved if entry.get("status") in SETTLED_MAPPING_STATUSES]
    held_back = [entry for entry in resolved if entry.get("status") in OPEN_MAPPING_STATUSES]
    attributed = attributed if attributed is not None else _attributed_wins(games, band_map, resolved)
    # "Stable" used to mean "nothing contradicted one pairing", which a candidate of unreadable nameplates
    # satisfies. Persistent players now requires someone to have actually named them: either the overlay
    # hypothesis held on a dominant reading throughout, or a usable band reading says who was where.
    pairings = dict(pairings or {})
    # Every game in the candidate named on both sides by its own dominant rows, no link contradicting it,
    # and no link whose pairing hypothesis was simply unreadable.
    overlay_named = all(pairings.get(round(float(g["start"]), 6), {}).get("dominant") for g in games)
    overlay_held = (overlay_named and all(link.get("verdict") == "joined" for link in links)
                    and not mapping and all(link.get("identity") == "same" for link in links))
    tally = attributed if attributed and "game_wins_by_identity" in attributed else None
    stable = overlay_held or bool(tally)
    out = {"unit": "persistent_players" if overlay_held else "screen_sides_only",
           "safe_to_read_as_players": stable,
           "identity_support": {
               "overlay_named_every_game": bool(overlay_named),
               "band_creditable_games": sum(1 for g in games
                                            if (band_map.get(round(float(g["start"]), 6)) or {})
                                            .get("usable")),
               "games": len(games)},
           "mapping_questions": [{"at": link["at"], "identity": link.get("identity"),
                                  "identities": link.get("identities"),
                                  "approximate": True} for link in mapping],
           "note": ("wins are counted per screen side; the pairing hypothesis behind those sides is not "
                    "established, so this is not a per-player score" if not stable else
                    ("the same pairing hypothesis held on a dominant reading across every game"
                     if overlay_held else
                     "the band named both sides of every game, so each win is credited to a person"))}
    if tally and not overlay_held:
        out["unit"] = tally["unit"]
    if header:
        out["band_readings_used"] = bool(band_map)
        out["identity_evidence_class"] = "unvalidated_header_diagnostic"
        out["cannot_settle"] = ["whether a set ended here: a nameplate is not a boundary",
                                "a round or game result: the band's pip row is never the crop arm's "
                                "win-dot measurement"]
    if resolved:
        # Which uncertainty the band closed and which it did not, per question, so a reader can price the
        # remaining footage rather than trusting a summary word.
        out["mapping_resolution"] = resolved
        out["questions_resolved"] = len(credited)
        out["questions_needing_footage"] = held_back
    if attributed and not tally:
        out["attribution_withheld"] = attributed.get("withheld")
    elif header and not band_map:
        out["attribution_withheld"] = ("no band reading falls inside any game of this candidate, so its "
                                       "screen sides name nobody")
    if tally:
        out["overlay_only_unit"] = "persistent_players" if overlay_held else "screen_sides_only"
        out["unit"] = tally["unit"]
        out["safe_to_read_as_players"] = True
        out["mapping_source"] = "header_band_saved"
        out["game_wins_by_identity"] = tally["game_wins_by_identity"]
        out["per_game"] = tally["per_game"]
        out["support"] = tally["support"]
        out["approximate"] = True
        out["note"] = tally["note"]
        if held_back:
            out["partially_supported"] = True
    return out


def assign_marks(sets, marks):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Attach the operator's approximate marks to candidate sets without letting them decide anything.

    Marks are made from memory during a running bracket: they land late, get written twice, get skipped,
    and a set can be marked while the marker is watching the next one. So a mark is an *anchor inside* a
    candidate -- attributed to whichever candidate contains it, or reported as unattributed -- and never a
    boundary. The offset from the candidate's own evidence-bound edge is published per mark, which is what
    turns "the marks drift +40 s" into a measured claim instead of an assumption a later run inherits.
    """
    moments = []
    for mark in marks or ():
        at = mark.get("at") if isinstance(mark, dict) else mark
        if not isinstance(at, (int, float)):
            continue
        moments.append({"at": float(at),
                        "label": (mark.get("label") if isinstance(mark, dict) else None)})
    annotated = []
    for record in sets:
        inside = [m for m in moments if record["start"] <= m["at"] <= record["end"]]
        offsets = [round(m["at"] - record["start"], 3) for m in inside]
        spacing = sorted(round(b["at"] - a["at"], 3) for a, b in zip(inside, inside[1:]))
        annotated.append(dict(record, marks={
            "count": len(inside), "at": [m["at"] for m in inside],
            "labels": [m["label"] for m in inside if m["label"]],
            "offsets_from_start": offsets,
            "spread_seconds": round(max(offsets) - min(offsets), 3) if len(offsets) > 1 else 0.0,
            # Two marks seconds apart in one candidate is a duplicate; two marks a minute apart may be a
            # second set nobody counted. Only the spacing distinguishes them, so the spacing is reported
            # and the verdict is not -- guessing which it was is how a count becomes a conclusion.
            "spacing_seconds": spacing}))
    outside = [m for m in moments if not any(r["start"] <= m["at"] <= r["end"] for r in sets)]
    if outside:
        # Reported against the candidates on either side rather than dropped or guessed at: a mark with
        # nothing around it is the signal that a candidate is missing from the recovery, and naming one
        # neighbour "the owner" would invent a boundary to explain it.
        ordered = sorted(sets, key=lambda r: r["start"])
        for item in outside:
            before = [r for r in ordered if r["end"] <= item["at"]]
            after = [r for r in ordered if r["start"] >= item["at"]]
            item["previous_candidate"] = (None if not before else
                                          {"end": before[-1]["end"],
                                           "seconds_after_end": round(item["at"] - before[-1]["end"], 3)})
            item["next_candidate"] = (None if not after else
                                      {"start": after[0]["start"],
                                       "seconds_before_start": round(after[0]["start"] - item["at"], 3)})
        annotated.append({"kind": "unattributed_marks", "marks": outside, "status": "unattributed",
                          "meaning": "a mark with no candidate around it: either the play it names was "
                                     "not recovered, or the mark is approximate and lands off the play",
                          "would_resolve": "watch the span it names and report what is there"})
    return annotated


def _cut_summary(link):
    # type: (Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]
    if link is None:
        return None
    gap = link.get("gap") or {}
    return {"verdict": link["verdict"], "at": link["at"],
            "before_game": link.get("before_game"), "after_game": link.get("after_game"),
            "reasons": link["reasons"],
            "states_seen": (link.get("gap") or {}).get("states"),
            "unclassified_cues": (link.get("gap") or {}).get("unclassified"),
            "questions": link.get("questions") or [],
            "identity": link.get("identity"), "identity_approximate": link.get("identity_approximate"),
            "identity_share": link.get("identity_share"),
            "score_signal": link.get("score_signal"), "dwell_seconds": link.get("dwell_seconds"),
            "identities": link.get("identities"),
            "preferred_cues": gap.get("preferred_cues"),
            "corroborating_cues": gap.get("corroborating_cues"),
            "neutral_cues": gap.get("neutral_cues"),
            "evidence_for": link.get("evidence_for"), "evidence_against": link.get("evidence_against"),
            # The two footage-side facts a swap is judged against, published because they are what
            # separates "the players changed sides" from "the broadcast changed names": which fighter each
            # side was showing, and whether the station was cleared between the games.
            "seat_continuity": link.get("seat_continuity"), "fighters": link.get("fighters"),
            "transition": link.get("transition"),
            "positively_supported": link.get("positively_supported"),
            "basis": link.get("basis", "recording_only"), "bracket": link.get("bracket")}


EDGE_STATION_CLEARED = "station_cleared"
EDGE_PLAY_CONTINUES = "play_continues_past_this_bound"
EDGE_PLAY_CONTINUES_BEFORE = "play_continues_before_this_bound"
EDGE_NOT_OBSERVED = "nothing_observed_after"
EDGE_NOT_OBSERVED_BEFORE = "nothing_observed_before"
# Rows were sampled on this side of the bound and none of them could settle it: no station cue, and no two
# gameplay frames whose clocks differed. This is not "nothing was observed" and not "play continued" -- it
# is the evidence being silent, which is why it resolves to uncertainty rather than to either conclusion.
EDGE_UNSETTLED = "sampled_but_unsettled"
EDGE_WITHIN_GAME_SCREEN = "between_games_screen_no_station_event"
EDGE_WITHIN_GAME_SCREEN_BEFORE = "between_games_screen_before_this_bound"

# How far past a candidate's last game the recording is read for an end signal -- a ceiling on the walk, not
# a licence to use anything inside it. The two station clears this measures are 1.0 s and 4.0 s after the
# last round on this recording (3,547.5 s after 3,546.5 s; 2,139.1 s after 2,133.1 s), and a between-game
# transition is followed by the next round inside 30 s, so 120 s reaches both without walking into the match
# after next.
EDGE_LOOKAHEAD_SECONDS = 120.0


def _station_cue_states():
    # type: () -> frozenset
    """The station-level states, read from the cue policy rather than restated here."""
    return frozenset(state for state, weight in SET_CUE_WEIGHT.items() if weight == "preferred")


def _edge_events(observations, lo, hi, anchor_clock, boundary, direction="after"):
    # type: (Sequence[Dict[str, Any]], float, float, Optional[int], float, str) -> tuple
    """What could settle an edge, in the order a walk outward from the boundary meets it.

    Two kinds of event settle an edge, and the line between them and the rest is the whole rule:

    * ``cue`` -- a station-level screen (controller prompt/assignment/disconnected). It cannot be part of a
      round, so the one nearest the boundary *is* the players stopping there.
    * ``live`` -- an **observed change** in the round clock between two gameplay rows outside the candidate.
      The clock falls inside a round and jumps when the next opens, so a change is a round being fought on
      the far side of the bound.

    A single readable frame is not a change, and treating it as one would be exactly wrong: the recording
    draws the K.O. freeze and the victory pose for seconds after a round is decided -- 35, 35, 35 across
    2.0 s on this tape, and the row just past a game's end is very often such a frame -- so an isolated
    gameplay row, a repeated digit, an unreadable clock, a menu or a stage intro are all *walked past*,
    each named in ``walked_past`` so an unresolved edge can be read rather than trusted. "Nothing settled
    it" is only honest if the reader can see what did not.

    ``anchor_clock`` seeds the comparison with the candidate's last (or first) readable clock, so a frame
    outside the bound that continues falling from it is live play on its very first appearance -- and, on the
    leading edge, the walk runs backwards through time with the same rule.
    """
    rows = sorted(observations, key=lambda o: float(o.get("timestamp", 0.0)))
    rows = [obs for obs in rows if lo < float(obs.get("timestamp", 0.0)) < hi]
    if direction == "before":
        rows = rows[::-1]
    events, walked = [], []
    last = anchor_clock
    for obs in rows:
        at = float(obs.get("timestamp", 0.0))
        state = str(obs.get("screen_state") or obs.get("state") or "")
        if state in _station_cue_states():
            events.append({"kind": "cue", "at": at, "state": state})
            continue
        if state != "gameplay":
            continue
        value = obs.get("round_clock")
        if value is None:
            walked.append({"at": at, "state": state, "clock": None, "because": "clock unread"})
            continue
        if last is not None and int(value) != int(last):
            events.append({"kind": "live", "at": at, "state": state,
                           "because": "the round clock changed from %s to %s" % (last, value)})
            last = value
            continue
        walked.append({"at": at, "state": state, "clock": value,
                       "because": "clock reads %s, %s" % (
                           value, "unchanged from the row nearer the bound" if last is not None
                           else "the first readable frame past the bound, with nothing beside it to change "
                                "from -- a frozen result frame is indistinguishable from this")})
        last = value
    events.sort(key=lambda event: abs(event["at"] - boundary))
    walked.sort(key=lambda event: abs(event["at"] - boundary))
    return events, walked


#: How far past an edge the cache has to reach before "nothing is here" may be claimed at all.
BOUNDARY_TOLERANCE_FRAMES = 2


def source_bounds(manifest=None, cache=None, duration=None, recording_start=None, recording_end=None):
    # type: (Optional[str], Optional[Sequence[Dict[str, Any]]], Optional[float], Optional[float], Optional[float]) -> Dict[str, Any]
    """What the tape covers, as distinct from what the cache holds or one query looked at.

    Three different extents get confused constantly, so they are named separately here:

    * the **cache** -- which frames exist on disk. A sampling manifest's ``start`` and ``end`` are the
      bounds *requested of the encoder* (``boundary_media.sample_frames`` clamps ``end`` to the duration
      and writes back what it asked for), so they establish cache extent and nothing more. A cache sampled
      from 100-200 s of a 1000 s recording proves only that 0-100 s was never put in the cache; the first
      and last analysed observation say even less.
    * the **source** -- how long the media is and where its timeline starts. That comes from explicit
      metadata only: ``recording_start`` / ``recording_end``, a manifest's ``source_duration`` (what newer
      writers record), or a caller-supplied ``duration`` from probing the file itself.
    * the **query** -- the analysis window, which is a third thing again and belongs to the caller.

    Anything not supplied stays ``None`` and every claim over it comes back unknown. A missing bound is a
    gap in knowledge, and the honest answer is to say so, not to borrow the nearest number.
    """
    header, entries = {}, list(cache or [])
    if not entries and manifest and Path(str(manifest)).is_file():
        raw = json.loads(Path(str(manifest)).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            header, entries = raw, list(raw.get("frames") or [])
        elif isinstance(raw, list):
            entries = raw
    times = sorted(float(e["timestamp"]) for e in entries if e.get("timestamp") is not None)
    interval = header.get("interval")
    # Cache extent: the frames themselves if we have them, else the sampled span the header recorded.
    cache_start = times[0] if times else (float(header["start"]) if header.get("start") is not None else None)
    cache_end = times[-1] if times else (float(header["end"]) if header.get("end") is not None else None)
    # Source extent: explicit metadata only, never the sampling bounds.
    known_start = _first_number(recording_start, header.get("recording_start"), header.get("source_start"))
    known_end = _first_number(recording_end, header.get("recording_end"),
                              _end_from(header.get("source_duration"), known_start),
                              _end_from(duration, known_start))
    basis = []
    if times:
        basis.append("cache frame records (extent of the cache only)")
    elif header:
        basis.append("manifest sampling bounds (extent of the cache only)")
    for value, why in ((known_start, "explicit recording start"), (known_end, "probed media duration")):
        if value is not None:
            basis.append(why)
    source = header.get("source")
    return {
        "recording_start": known_start,
        "recording_end": known_end,
        "cache_start": cache_start,
        "cache_end": cache_end,
        "cache_frames": len(times),
        "cache_interval": float(interval) if interval else None,
        "source": str(source) if source else None,
        "source_available": bool(source and Path(str(source)).is_file()) if source else None,
        # What the *tape* is known to be. Cache bounds alone never make a truncation claim permissible.
        "proven": known_start is not None or known_end is not None,
        "cache_known": cache_start is not None or cache_end is not None,
        "basis": basis or ["no cache, no manifest and no source metadata: neither the tape nor the cache "
                           "has a known extent"],
    }


def _first_number(*values):
    # type: (*Any) -> Optional[float]
    for value in values:
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _end_from(length, start):
    # type: (Any, Optional[float]) -> Optional[float]
    """A duration plus a known origin is an end; a duration with no origin still ends at the duration,
    because a media file's timeline starts at 0 unless something says otherwise."""
    if length is None:
        return None
    return float(length) + float(start or 0.0)


def footage_limit(at, direction, source=None, beyond_window=False, window_edge=None,
                  tolerance=None):
    # type: (float, str, Optional[Dict[str, Any]], bool, Optional[float], Optional[float]) -> Dict[str, Any]
    """Why nothing settled this edge, when only the tape and the cache are allowed to answer.

    Five answers, because they have five different remedies and collapsing them is how a caller's narrow
    query gets reported as a damaged recording:

    ``recording_head`` / ``recording_tail``
        the edge sits at the proven start or end of the source. Nothing on tape can settle it.
    ``analysis_window``
        footage exists beyond the query's own edge and the query declined to read it. Widening the window
        costs no inference, and it is the only limit here that a caller can undo by asking again.
    ``footage_never_sampled``
        inside the queried span and present in the cache but never read -- a finer grid question.
    ``not_in_cache``
        the recording covers the instant and the cache does not, so recovery means sampling the source
        rather than re-asking a frame.
    ``source_bounds_unknown``
        nothing can be said. This is the honest default for a caller that supplied no manifest, no cache
        and no duration, and it must never silently become a truncation claim.
    """
    src = dict(source or {})
    at = float(at)
    tol = float(tolerance if tolerance is not None else
                (src.get("cache_interval") or 1.0) * BOUNDARY_TOLERANCE_FRAMES)
    if not src.get("proven") and not src.get("cache_known"):
        return {"limit": "source_bounds_unknown",
                "basis": "neither the tape nor the cache has a known extent: the first and last analysed "
                         "observation say where this query looked, not where the recording or the cache "
                         "begin or end"}
    head = direction in ("before", "start")
    rec = None
    if head and src.get("recording_start") is not None:
        rec = float(src["recording_start"])
    elif not head and src.get("recording_end") is not None:
        rec = float(src["recording_end"])
    cache_low, cache_high = src.get("cache_start"), src.get("cache_end")
    if rec is not None:
        if head and at <= rec + tol:
            return {"limit": "recording_head",
                    "basis": "the source starts at %.3f s, at or after this edge at %.3f s" % (rec, at)}
        if not head and at >= rec - tol:
            return {"limit": "recording_tail",
                    "basis": "the source ends at %.3f s, at or before this edge at %.3f s" % (rec, at)}
    if beyond_window and window_edge is not None:
        edge = float(window_edge)
        outside = (cache_low is not None and cache_low < edge - tol) if head else (
            cache_high is not None and cache_high > edge + tol)
        rec_outside = rec is not None and ((rec < edge - tol) if head else (rec > edge + tol))
        if outside or rec_outside:
            return {"limit": "analysis_window",
                    "basis": "footage exists beyond the query's own edge at %.3f s; this run declined to "
                             "read it" % edge}
    cached_here = (cache_low is not None and cache_low < at - tol) if head else (
        cache_high is not None and cache_high > at + tol)
    if cached_here:
        return {"limit": "footage_never_sampled",
                "basis": "the cache holds frames on this side of %.3f s and none was read" % at}
    # The cache stops here and the tape is not shown to stop with it. Without a proven source bound this is
    # as far as honesty goes: a sampling window of 100-200 s over a 1000 s recording is a gap in the cache,
    # not the end of the footage, and only sampling the source can tell the two apart.
    edge = cache_high if not head else cache_low
    further = "the source is known to run further" if rec is not None \
        else "nothing here says where the source ends"
    return {"limit": "not_in_cache",
            "basis": "the cache ends at %.3f s on this side of %.3f s and %s, so the footage here is "
                     "unexamined rather than absent" % (edge, at, further)}


#: Edge limits that mean the tape itself stops here: no further footage exists to ask a model about, so a
#: claim bounded by one is settled by what the recording is, not by a bigger allowance.
TAPE_END_LIMITS = frozenset({"recording_head", "recording_tail"})
#: Edge limits that mean footage may well exist but was never sampled into the cache. Neither is answerable
#: by a request -- only by extracting frames from the source -- so a claim bounded by one is deferred by
#: name rather than priced.
UNREADABLE_BY_A_REQUEST = frozenset({"footage_never_sampled", "not_in_cache", "source_bounds_unknown"})


def _set_unresolved_claims(record):
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    """Which claims about this set the evidence has not settled, each with the evidence that settles it.

    Deliberately not one boolean. A candidate whose games all concluded can still owe four different
    answers, and `set_winner is None` cannot tell them apart -- worse, collapsing them into the footage
    `status` let a successful read *remove* a set from the queue. `costs_inference` is the guard against
    the opposite failure: an unsupplied format is a question for the user or the event page, and anything
    that re-asked the model for it would spend on every run and never close.

    Each claim also names **where** it would be answered -- ``at`` is the list of instants the evidence has
    to cover, and ``windows`` the stretches the state route can inspect whole. Without them the claim is
    prose, and a planner holding prose either ignores it or invents frames to fit the sentence; with them
    it can be matched against what is already planned, reused from what is already saved, or deferred by
    name. Where the footage itself says there is nothing more to read, the claim carries that as
    ``beyond_footage`` rather than asking for a frame past the end of the tape.
    """
    claims = []
    edges = record.get("edges") or {}
    # The candidate's own game spans, not the game records: the queue carries what a set layer may read,
    # and these are the instants the crop route anchors a pip reading to, so a claim and a plan can be
    # matched against each other without inventing a second grid of decision points.
    spans = [list(entry) for entry in record.get("game_spans") or () if entry]
    winners = list(record.get("game_winners") or ())

    def _stamp(value):
        return None if value is None else round(float(value), 6)

    def _instants(values):
        return [_stamp(value) for value in values if value is not None]

    def _beyond_footage():
        """Where this candidate's own edges say there is nothing left to read, or nothing sampled."""
        return {"tape_ends": [key for key in ("start", "end")
                              if ((edges.get(key) or {}).get("limit") in TAPE_END_LIMITS)],
                "unsampled": [key for key in ("start", "end")
                              if ((edges.get(key) or {}).get("limit") in UNREADABLE_BY_A_REQUEST)]}

    unnamed = [entry for entry, won in zip(spans, winners) if not won]
    if record.get("games_without_winner"):
        claims.append({"claim": "game_results",
                       "missing": "%d game(s) in this span name no winner"
                                  % record["games_without_winner"],
                       "would_resolve": "the win row at those games' conclusion instants",
                       "route": "pip_crops", "costs_inference": True,
                       "at": _instants([entry[1] for entry in unnamed]),
                       "games": [[_stamp(entry[0]), _stamp(entry[1])] for entry in unnamed],
                       "beyond_footage": _beyond_footage()})
    if record.get("boundary_conflicts"):
        claims.append({"claim": "set_membership",
                       "missing": "an internal game boundary is disputed: the win row continues across a "
                                  "round the game announces as ROUND 1",
                       "would_resolve": "the win row at the disputed round's own opening",
                       "route": "pip_crops", "costs_inference": True,
                       "at": _instants([record.get("start"), record.get("end")])})
    if not record.get("boundary_confirmed") or record.get("membership") not in ("supported", "confirmed"):
        # Whole-frame questions, and the route that can see a whole frame. The top band of a frame cannot
        # show a controller being handed over or a stage change; what it *can* answer about this claim --
        # which pairing the nameplate credits at each edge -- is the `identity` claim below, and that stays
        # on the header route where it belongs.
        claims.append({"claim": "set_continuity",
                       "missing": "nothing on screen showed these games belong to one set, or the boundary "
                                  "with a neighbour was only proposed",
                       "would_resolve": "the frames around each game edge, read whole: which screen "
                                        "separates these games, whether the station was cleared, and "
                                        "whether live play continues on the same pairing",
                       "route": "state", "costs_inference": True,
                       "at": _instants([record.get("start"), record.get("end")]),
                       # One window per pair of consecutive games inside the candidate: the stretch between
                       # them is where a handover would be, and a route asked to read a whole frame needs
                       # the stretch, not one instant in the middle of it.
                       "windows": [[_stamp(a[1]), _stamp(b[0])] for a, b in zip(spans, spans[1:])],
                       "band_can_answer": ["which pairing the nameplate credits at each edge"],
                       "not_answerable_by_a_band_crop": ["controller handover", "stage identity",
                                                          "what fills a long dwell"],
                       "beyond_footage": _beyond_footage()})
    attribution = record.get("attribution") or {}
    if record.get("game_winners") and attribution.get("unit") != "persistent_players":
        # The points are the **games whose wins need a person**, as stretches, not the candidate's two
        # edges. A band reading is credited to whoever's span contains it, which is what
        # ``missing_header_observations`` prices -- so a claim that named the candidate's boundaries asked
        # for two instants the planner would never schedule, and a point-level ledger could only ever
        # report them unaccounted. Naming the games is what makes "carried" mean "someone will read this".
        credited = [entry for entry, won in zip(spans, winners) if won] or spans
        claims.append({"claim": "identity",
                       "missing": "the wins are credited to screen sides, so no participant is named as "
                                  "the set's winner",
                       "would_resolve": "a band reading inside each credited game's own span, mapping each "
                                        "side to a named player",
                       "route": "header", "costs_inference": True,
                       "at": _instants([record.get("start"), record.get("end")]),
                       "windows": [[_stamp(entry[0]), _stamp(entry[1])] for entry in credited],
                       "beyond_footage": _beyond_footage()})
    if not record.get("closed_by_target"):
        formats = record.get("applicable_formats") or {}
        if not formats.get("format_known"):
            claims.append({"claim": "format",
                           "missing": "no format was supplied and the span names no stage, so no target "
                                      "exists to compare %s against"
                                      % json.dumps(record.get("deciding_wins")),
                           "would_resolve": "the event format from the user or the event listing -- no frame "
                                            "of this recording can supply it",
                           "route": "user_policy", "costs_inference": False,
                           # One question for the recording, not one per candidate: ten candidates all
                           # missing the same fact do not need ten asks, and an ask is not inference.
                           "ask_once": True,
                           "applies_to": [[_stamp(record.get("start")), _stamp(record.get("end"))]]})
        else:
            claims.append({"claim": "completion",
                           "missing": "no side reached the supplied target, so either a game went undetected "
                                      "or the set is still being played",
                           "would_resolve": "a win row at each detected game's conclusion that nobody has "
                                            "read yet",
                           "route": "pip_crops", "costs_inference": True,
                           # Only the games' own conclusion instants. The candidate's two outer edges are
                           # where this set meets its neighbours: no win row is drawn there, the crop route
                           # has never scheduled a frame for them, and no deferral list names them -- so as
                           # a demand they could only ever come back unaccounted, which is a defect in this
                           # sentence rather than a property of the footage. A game nobody detected between
                           # two known ones is a whole-frame question about the stretch, and it is already
                           # asked by the dwell and continuity kinds in the queue.
                           "at": _instants([entry[1] for entry in spans]),
                           "gaps": [[_stamp(a[1]), _stamp(b[0])] for a, b in zip(spans, spans[1:])],
                           "answered_by_other_questions": ["dwell_after_observed_end",
                                                            "game_end_bounded"],
                           "beyond_footage": _beyond_footage()})
    return claims


def _match_edges(games, observations, cut_before=None, cut_after=None, bracket=None,
                 analysis_window=None, lookahead_seconds=EDGE_LOOKAHEAD_SECONDS,
                 previous_game=None, next_game=None, source=None):
    # type: (List[Dict[str, Any]], Sequence[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Sequence[float]], float, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Where the recording says a set began and ended, read off the tape rather than off a target.

    This exists because a numeric target is not the only way to know a set is over, and treating it as the
    only way made the format -- a fact about the event, supplied or not -- veto a conclusion the footage
    argued for on its own. Two players who put the controllers down have ended a match; that is an
    observation, independent of whether the round was first to two or first to three.

    **Locality is what makes it an observation rather than a coincidence.** An edge is settled by the row
    nearest its boundary: a station cue found before any live play *is* the finding; live play nearer the
    boundary than any cue *contradicts* one; and a cue reached only after play had resumed belongs to that
    later transition, not to this bound. Reading a fixed window instead lets a controller prompt 80 s late,
    sitting past a round that had already restarted, conclude a set that had plainly gone on -- borrowing an
    event from somebody else's boundary. The walk is also bounded by the adjacent game, so no cue behind the
    next candidate's opening can reach back over it, and the same rule runs backwards for the leading edge.

    Families accepted, and what is explicitly not one:

    * ``station_cleared`` -- a controller cue nearer this bound than any live play within it;
    * ``bracket_match_change`` -- the event record lists a different match on the far side;
    * the absence of either is not a finding. ``play_continues_*`` is evidence *against* a boundary and
      ``nothing_observed_*`` is no evidence at all, which is why a candidate running to the end of the
      analysis window stays unresolved rather than being read as finished -- and stays distinguishable from
      a recording that actually stopped there.

    ``concluded`` therefore means "the footage shows this set ending here", and ``footage_complete`` means
    "the footage also shows it beginning here", so every one of its games is inside the recording. They are
    separate claims because they are separate questions: a set whose last games are on tape and whose first
    game is not has a known end and an unknown score.
    """
    ordered = sorted(observations, key=lambda o: float(o.get("timestamp", 0.0)))

    def clock_inside(at, direction):
        """The candidate's own last (or first) readable clock, to seed the comparison outside it."""
        side = (lambda t: t <= at) if direction == "after" else (lambda t: t >= at)
        values = [obs.get("round_clock") for obs in ordered
                  if side(float(obs.get("timestamp", 0.0))) and obs.get("round_clock") is not None]
        if not values:
            return None
        return values[-1] if direction == "after" else values[0]

    def edge(at, direction, link, neighbour_at):
        at = float(at)
        if direction == "after":
            lo, hi = at, at + lookahead_seconds
            if neighbour_at is not None and float(neighbour_at) > at:
                hi = min(hi, float(neighbour_at))
        else:
            lo, hi = at - lookahead_seconds, at
            if neighbour_at is not None and float(neighbour_at) < at:
                lo = max(lo, float(neighbour_at))
        window_edge = analysis_window is not None and (
            hi > float(analysis_window[1]) + 0.001 if direction == "after"
            else lo < float(analysis_window[0]) - 0.001)
        rows = [obs for obs in ordered if lo < float(obs.get("timestamp", 0.0)) < hi]
        states = {str(o.get("screen_state") or o.get("state") or "unknown") for o in rows}
        anchor = clock_inside(at, direction)
        events, walked = _edge_events(ordered, lo, hi, anchor, at, direction)
        nearest = events[0] if events else None
        listed = (link or {}).get("bracket") or {}
        families, against = [], []
        if listed.get("match_change"):
            families.append("bracket_match_change")

        if nearest is None:
            # Nothing on this side settled the bound -- and *which kind* of nothing matters. A between-games
            # screen says the set went on; rows too degraded to settle anything say the evidence is absent;
            # and no rows at all usually means the caller's window stopped, not the recording. Collapsing
            # the middle case into the last one would report "nothing observed" beside a scan that saw
            # several frames, which is the same dishonesty in miniature as a borrowed cue.
            if "post_match_menu" in states:
                kind = (EDGE_WITHIN_GAME_SCREEN if direction == "after"
                        else EDGE_WITHIN_GAME_SCREEN_BEFORE)
                against.append("a between-games screen with no station change: the set went on")
            elif not rows:
                kind = EDGE_NOT_OBSERVED if direction == "after" else EDGE_NOT_OBSERVED_BEFORE
                against.append("analysis window edge" if window_edge else "no observation on this side")
            else:
                kind = EDGE_UNSETTLED
                against.append("%d row(s) were sampled on this side and none could settle the bound: %s"
                               % (len(rows), walked[0]["because"] if walked
                                  else "no station cue, and no round clock that changed"))
                if window_edge:
                    against.append("the walk stops at the analysis window's own edge, not at the end of the "
                                   "recording")
        elif nearest["kind"] == "cue":
            kind = EDGE_STATION_CLEARED
            families.insert(0, "station_cleared")
        else:
            kind = EDGE_PLAY_CONTINUES if direction == "after" else EDGE_PLAY_CONTINUES_BEFORE
            against.append("%s at %.3f s, %.3f s past the bound and nearer than any station cue"
                           % (nearest["because"], nearest["at"], abs(nearest["at"] - at)))
            later_cue = next((e for e in events if e["kind"] == "cue"), None)
            if later_cue is not None:
                against.append("the controller cue at %.3f s sits behind that live round and belongs to a "
                               "later transition, not to this boundary" % later_cue["at"])
        return {"at": round(at, 6), "direction": direction, "state": kind,
                "evidence_for": families, "evidence_against": against,
                "scanned": [round(lo, 6), round(hi, 6)],
                "bounded_by_adjacent_game": neighbour_at is not None,
                "settled_by": nearest,
                "walked_past": walked[:6],
                "station_cue_at": (round(nearest["at"], 6) if nearest and nearest["kind"] == "cue"
                                   else None),
                "live_play_at": (round(nearest["at"], 6) if nearest and nearest["kind"] == "live"
                                 else None),
                "clock_at_boundary": anchor,
                "cues": sorted(states), "observed": bool(rows),
                "unresolved_at": kind in (EDGE_NOT_OBSERVED, EDGE_NOT_OBSERVED_BEFORE, EDGE_UNSETTLED)
                                 and not window_edge,
                "beyond_analysis_window": bool(window_edge)}

    first, last = games[0], games[-1]
    start_at = first.get("start")
    if (start_at is None or math.isinf(float(start_at))) and first.get("first_round_watched_at") is not None:
        start_at = first["first_round_watched_at"]
    end_at = last.get("watched_end") or last.get("end") or last.get("conclusion_event_at")
    previous_at = (previous_game or {}).get("end") or (previous_game or {}).get("conclusion_event_at")
    next_at = (next_game or {}).get("start")
    start = edge(start_at, "before", cut_before, previous_at) if start_at is not None else None
    end = edge(end_at, "after", cut_after, next_at) if end_at is not None else None
    unsettled = (EDGE_NOT_OBSERVED, EDGE_NOT_OBSERVED_BEFORE, EDGE_UNSETTLED)
    for entry, direction in ((start, "before"), (end, "after")):
        if entry is None or entry["state"] not in unsettled:
            continue
        # *Why* this side has nothing on it, from the tape and the cache rather than from the rows that
        # happened to be handed over. Three remedies are in play -- widen the query, sample the source, or
        # accept that the footage stops here -- and the first and last analysed observation cannot tell
        # them apart. With no source bounds at all the answer is "unknown", never a truncation claim.
        edge_at = float(entry["at"])
        window_lo, window_hi = (analysis_window or (None, None))
        verdict = footage_limit(edge_at, direction, source=source,
                                beyond_window=bool(entry.get("beyond_analysis_window")),
                                window_edge=(float(window_lo) if direction == "before" and window_lo is not None
                                             else float(window_hi) if window_hi is not None else None))
        entry["limit"] = verdict["limit"]
        entry["limit_basis"] = verdict["basis"]
        entry["source_bounds"] = None if not (source or {}).get("proven") else {
            key: source.get(key) for key in ("recording_start", "recording_end", "cache_start",
                                             "cache_end", "cache_frames", "source", "source_available")}
    supported = lambda entry: bool(entry) and EDGE_STATION_CLEARED in entry["evidence_for"]
    start_here, end_here = supported(start), supported(end)
    return {"start": start, "end": end,
            "concluded": end_here,
            "footage_complete": bool(start_here and end_here),
            "start_observed": start_here, "end_observed": end_here,
            "families_accepted": [EDGE_STATION_CLEARED, "bracket_match_change"],
            "rule": "the row nearest the boundary settles it: a station cue found before any live play "
                    "concludes, live play nearer than any cue contradicts, and a cue reached only after "
                    "play resumed belongs to that later transition",
            "note": "a boundary is a finding only when a family of cues says the players stopped; the "
                    "format's target is a second, independent way to reach one, and neither is the other"}
def _unobserved_leading_footage(start_edge):
    # type: (Optional[Dict[str, Any]]) -> str
    """Why this candidate's opening games may be missing, in the one place that knows which it is.

    A reached target says a set ended; it says nothing about where the set began. Keying this disclosure to
    the *status* let the optional format silence the only line admitting that the first game was already in
    progress -- a 2-0 claim beside a scoreboard that was already at 1-1 when the footage opened. So the claim
    is keyed to the edge, and the wording keeps the three remedies apart: a window edge is a query the caller
    can widen offline, a recording head is a fact about the tape, and unsampled footage between analysed
    frames is a planner gap that a finer grid closes.
    """
    at = (start_edge or {}).get("at")
    where = ("%.3f s" % at) if at is not None else "its first game"
    limit = (start_edge or {}).get("limit")
    if limit == "analysis_window":
        return ("the start of this set: the analysis window opened after the set had, so its first readable "
                "frame is at %s with the round clock already running and games played earlier are not "
                "accounted for. This is a limit of the query, not of the recording -- widening the window "
                "reuses footage already on disk -- and until it is widened the tally cannot be quoted as "
                "the set's" % where)
    if limit == "recording_head":
        return ("the start of this set: the recording itself begins at %s with the round clock already "
                "running, so no earlier game of this set exists on tape and no wider window would find one; "
                "the tally cannot be quoted as the set's" % where)
    if limit == "footage_never_sampled":
        return ("the start of this set: footage exists before its first readable frame at %s but no frame "
                "there was ever sampled, so the earlier games are unaccounted for rather than absent -- a "
                "finer grid is what would settle it, not a wider recording" % where)
    if limit == "not_in_cache":
        return ("the start of this set: frames exist on the source before %s but not in the cache this run "
                "read, so the earlier games are unexamined rather than absent; recovering them means "
                "sampling the source" % where)
    if limit == "source_bounds_unknown":
        return ("the start of this set: the first frame this query read is at %s with the round clock "
                "already running, and no source bounds were supplied, so it is unknown whether the set "
                "began there, the recording began there, or footage in between was never looked at. The "
                "tally cannot be quoted as the set's until one of those is established" % where)
    return ("the start of this set: the recording shows it ending here, but no station change is observed "
            "before its first game at %s, so games played earlier are not accounted for and the score "
            "cannot be quoted as the set's" % where)


def _unobserved_trailing_footage(end_edge):
    # type: (Optional[Dict[str, Any]]) -> str
    """What is missing after the last game, named by the reason that is actually on record.

    The mirror of :func:`_unobserved_leading_footage`, and written separately because the two edges ask
    different questions: the head wants to know whether games were played before, the tail whether play
    continued after. A station cue settles the tail outright, so this only ever speaks where nothing did.
    """
    at = (end_edge or {}).get("at")
    where = ("%.3f s" % at) if at is not None else "its last game"
    limit = (end_edge or {}).get("limit")
    if limit == "analysis_window":
        return ("any observation after the last game: the analysis window closed at %s while footage "
                "continues beyond it, so whether play went on is unqueried rather than absent -- widening "
                "the window reuses rows already on disk" % where)
    if limit == "recording_tail":
        return ("any observation after the last game: the recording itself ends at %s, so nothing further "
                "can be seen on this tape and continuity past it is unknowable rather than unasked" % where)
    if limit == "footage_never_sampled":
        return ("any observation after the last game at %s: the cache holds frames beyond it and none was "
                "read, so play continuing is unexamined, not ruled out" % where)
    if limit == "not_in_cache":
        return ("any observation after the last game at %s: the cache stops there while the source is not "
                "shown to, so only sampling the source can say whether the set went on" % where)
    if limit == "source_bounds_unknown":
        return ("any observation after the last game at %s, and no source bounds were supplied: it is "
                "unknown whether the recording ended, the query ended, or the footage in between was "
                "never looked at" % where)
    return ("any observation after the last game at %s, so whether the set ended there is unknown rather "
            "than unanswered" % where)


def _set_record(games, games_per_set, membership, links, single, clip_pad_seconds=CLIP_PAD_SECONDS,
                cut_before=None, cut_after=None, header=None, band_map=None, pairings=None,
                formats=None, edges=None):
    # type: (List[Dict[str, Any]], Optional[int], str, List[Dict[str, Any]], bool, float, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[float, Dict[str, Any]]], Optional[Dict[float, Dict[str, Any]]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    wins = _wins_of(games)
    unnamed = sum(1 for entry in games
                  if not (entry.get("counts_toward_set") and entry.get("winner") in wins))
    band_map = dict(band_map or {})
    formats = dict(formats or {"targets": ([games_per_set] if games_per_set else []),
                               "ambiguous": False, "format_known": bool(games_per_set), "evidence": {},
                               "basis": "no format passed", "readings_without_a_stage": [],
                               "stage_vocabulary_applied": False, "policy_source": None})
    targets = [int(t) for t in formats["targets"]]
    # One participant reaches one target: this candidate is measured against every target its own stage
    # readings admit, and a set is complete only where all of them say it is. A 2-0 inside a span whose
    # band named both a quarter-final and a Winners Final is therefore reported as *closed under first to
    # two* and left open, because inventing the boundary the other reading denies is the error this layer
    # was built to stop.
    # One resolution pass per candidate, over its own links and the two cuts that formed it: the mapping
    # question a swap raises normally sits on the cut, not between two games that ended up together.
    mapping_questions = _resolve_mapping_questions(
        [l for l in (list(links) + [cut_before, cut_after]) if l], band_map) if header else []
    deciding, deciding_unit, attributed = _deciding_wins(games, band_map, mapping_questions)
    pairings = dict(pairings or {})
    complete_under, open_under = _reached_under(targets, deciding, attributed)
    reached = bool(targets) and not open_under and bool(complete_under)
    edges = dict(edges or {})
    # Two independent ways for a set to be over, and neither one is the other's fallback:
    #   * the tally reaches the target the stage admits (the format's answer), and
    #   * the recording shows the players stopping (the footage's answer, which needs no format at all).
    # A candidate satisfies the second when a station cue closes it; the first still counts when the target
    # is known and reached. What the second route does **not** buy is a winner: a controller handed over
    # says the set ended here, not that anybody won it, and reading a forfeit as a victory is the same
    # invention in the other direction.
    boundary = bool(edges.get("concluded"))
    end_state = (edges.get("end") or {}).get("state")
    contradicted_by_play = end_state == EDGE_PLAY_CONTINUES
    # The footage route needs both edges, not just one. A station clear after the last game says a set ended
    # there; without a clear (or a window edge that is not in question) before the first game, the candidate
    # is the tail of a set whose earlier games are not on tape, and calling it complete would report a
    # partial recording as a partial score. Both edges observed is what "we have the whole set" means.
    footage_whole = bool(edges.get("footage_complete"))
    # At least one game of this candidate has to have a winner before "the footage shows the set ending"
    # counts as recovering a set. Without that floor, a stretch of undeclosed games bounded by two station
    # clears would be reported as a completed set with no score -- the BB9 shape that used to prove nothing
    # was resolved, and a boundary observation is not on its own a recovery.
    has_a_counted_win = any(entry.get("counts_toward_set") and entry.get("winner") for entry in games)
    decided_by = []
    if reached:
        decided_by.append("target_reached")
    if boundary:
        decided_by.append("station_cleared")
    # Which target this candidate was measured against, published only where one target actually applied.
    # A boundary reached without a format leaves it null rather than reading one off the game count that
    # happened to be on tape, which is the inference this function is not allowed to make.
    games_per_set = targets[0] if len(targets) == 1 else None
    leader = [key for key, value in deciding.items()
              if complete_under and value >= min(complete_under)]
    if not targets:
        if boundary and footage_whole and has_a_counted_win:
            status = "complete"
            basis = "set_ended_on_the_recording_without_a_format_%s" % membership
        elif boundary:
            # The end is a fact and something else is missing -- here, that the footage does not show the
            # set beginning, because the caller's window opened mid-match. Saying "no observed end" would
            # contradict the `edges` block printed beside it.
            status, basis = "partial", "set_ended_on_the_recording_footage_incomplete"
        else:
            status, basis = "partial", "no_target_and_no_observed_end"
    elif reached and membership in ("supported", "assumed"):
        status = "complete"
        basis = ("participant_reached_target_and_membership_supported"
                 if deciding_unit == "identities" else
                 "side_reached_target_and_membership_supported")
        if deciding_unit == "identities" and membership == "assumed":
            basis = "participant_reached_target_with_membership_assumed"
        if boundary:
            basis += "_and_station_cleared"
    elif reached and membership != "supported":
        status, basis = "partial", "target_reached_but_membership_%s" % membership
    elif boundary and footage_whole and has_a_counted_win:
        # The tally fell short of the target and the tape says the players stopped anyway: a forfeit, a
        # disconnect, a set cut short, or a target that was never this event's. Reported as "ended here,
        # score unresolved", because choosing between those is a bracket question, not an inference.
        status, basis = "complete", "set_ended_on_the_recording_target_not_reached"
    elif boundary:
        # The end is on tape and the beginning is not, because the window opened mid-set. A boundary with
        # no score: some of this set's games were never in the footage, so however the tally reads it is
        # not the set's result -- and the configured target has no bearing on that either way.
        status, basis = "partial", "set_ended_on_the_recording_footage_incomplete"
    else:
        status = "partial"
        basis = ("membership_%s_target_not_reached" % membership)
    format_dependent = bool(complete_under) and bool(open_under)
    if format_dependent and not boundary:
        basis = ("target_reached_only_under_%s" % "_or_".join(str(t) for t in complete_under))
    elif format_dependent and boundary:
        basis = "set_ended_on_the_recording_format_disputed"
    elif (attributed or {}).get("person_tally_possible") is False and deciding and not boundary:
        # The number is large enough and the set still cannot be called closed *on the tally*. With a
        # station clear behind it the end is a fact and only the score is open, so the veto stays scoped to
        # the score -- which is the distinction this whole route exists to make.
        basis = "seat_wins_cannot_be_read_as_one_person"

    missing = []
    if status == "complete" and not targets:
        missing.append("the score and winner: the recording shows this set ending and no format was "
                       "supplied, so no target is claimed and none is needed for the boundary")
    if not targets and status != "complete":
        missing.append("the format: games_per_set was never configured and no stage was named inside this "
                       "span -- which is missing evidence about the rules, not a verdict that the footage "
                       "shows no set ending")
    if not (edges or {}).get("start_observed", True):
        missing.append(_unobserved_leading_footage((edges or {}).get("start")))
    if status == "complete" and basis == "set_ended_on_the_recording_target_not_reached":
        missing.append("why the set ended below its target -- a forfeit, a disconnect, a cut short, or a "
                       "target this event never used: the recording shows the players stopping and the "
                       "tally falling short, and only the event record can say which")
    if end_state == EDGE_NOT_OBSERVED:
        # Keyed to the edge, not to the status. A side reaching its target ends the set and says nothing
        # about whether the footage kept going past the last game it read -- and the old wording, "the
        # recording (or the caller's window) stops there", is the same conflation as the leading edge, in
        # the other direction: it offered the reader a choice between two remedies the evidence has already
        # decided between.
        missing.append(_unobserved_trailing_footage((edges or {}).get("end")))
    if contradicted_by_play:
        missing.append("the end is contradicted: play was still running past this candidate's last game, "
                       "so a closure here rests on the target alone")
    if basis == "seat_wins_cannot_be_read_as_one_person":
        names = sorted({entry["name"] for entries in
                        (attributed or {}).get("participant_hypotheses", {}).values()
                        for entry in entries})
        missing.append("whether the winning screen side kept the same player across these games: it is "
                       "named as %s and the fighters read %s, so %d side-wins cannot be added up as one "
                       "person's set"
                       % (", ".join(names) or "nobody", (attributed or {}).get("seat_continuity"),
                          max((deciding or {}).values() or [0])))
    if format_dependent:
        missing.append("which format applies: the band readings inside this span admit %s, and the tally "
                       "reaches %s but not %s"
                       % (" or ".join("first to %d" % t for t in targets),
                          ", ".join(str(t) for t in complete_under),
                          ", ".join(str(t) for t in open_under)))
    if membership == "assumed":
        missing.append("nothing contradicted these games being one set, and nothing supported it either: "
                       "a legible pairing on both sides of each gap, or a tally that moves, is what would "
                       "upgrade this from the format default to an observation")
    elif membership != "supported":
        missing.append("membership rests on cues that disagree; see `links`, `cut_before`, `cut_after` "
                       "and `alternative_groupings` for the specific disagreement")
    if targets and not reached:
        missing.append("per-%s game wins reaching %s (seen %s)"
                       % ("side" if deciding_unit == "sides" else "participant",
                          ("%d" % targets[0]) if len(targets) == 1
                          else "any applicable target (%s)" % " or ".join("first to %d" % t
                                                                          for t in targets),
                          dict(deciding)))
    if unnamed:
        missing.append("%d game(s) in this candidate name no winner" % unnamed)
    for conflict in _candidate_boundary_conflicts(games):
        missing.append("a game boundary inside this candidate is disputed at %.3f s: the win row is read "
                       "%s on both sides of it while the game announces ROUND %s there, so %d games are "
                       "reported where one is an equally supported reading. Neither reading was discarded: "
                       "both games carry it and their wins are withheld"
                       % (conflict["at"], conflict["readings_persisted"],
                          conflict["round_number_announced"], len(games)))
    if deciding_unit == "sides" and header:
        # The candidate is being measured on sides because nobody could establish who held them. Saying so
        # is the difference between an unresolved set and a set that looks like it went to game five.
        missing.append("which participant holds each counted win: %s"
                       % ((attributed or {}).get("withheld")
                          or "no band reading was usable for these games"))

    # A set's clip has to contain everything its last game's clip contains, and that includes the read
    # naming the winner: anchoring on `conclusion_event_at` alone cut the clip two seconds short of the
    # pip that decided the set. The game layer already applied the policy once -- max(conclusion,
    # confirmation) + configured padding, capped at that game's next opening -- so the set inherits that
    # number instead of padding it a second time, and neither the next marking nor the menu dwell behind
    # a conclusion is ever reached for.
    last = games[-1]
    # `end` is only the anchor when no conclusion instant was ever named for that game -- the same
    # precedence the policy had before, plus the confirmation the old version ignored.
    conclusion_at = last.get("conclusion_event_at")
    if conclusion_at is None:
        conclusion_at = last.get("end")
    justified = [x for x in (conclusion_at, last.get("winner_confirmed_at")) if x is not None]
    anchor = max(justified) if justified else None
    clip_end = last.get("clip_end_at")
    padded_already = clip_end is not None and anchor is not None and clip_end >= anchor - 1e-9
    if clip_end is None and anchor is not None:
        clip_end = round(anchor + clip_pad_seconds, 6)
    clip_end = round(clip_end, 6) if clip_end is not None else None

    return {
        "start": games[0]["start"],
        "end": last["end"],
        "games": len(games),
        "complete_games": sum(1 for g in games if g["status"] == "complete"),
        "basis": basis,
        "status": status,
        "membership": membership,
        "game_wins": dict(wins),
        # The figure the target was tested against, and what it counts. Identical to `game_wins` unless a
        # supported participant mapping says the sides were the same person all along.
        "deciding_wins": dict(deciding),
        "deciding_wins_unit": deciding_unit,
        # Populated only when a *participant* reached the target: an unresolved mapping must not publish a
        # persistent-player winner, whatever the side arithmetic says.
        "set_winner": (leader[0] if (status == "complete" and deciding_unit == "identities"
                                     and len(leader) == 1) else None),
        "winner_unit": ("broadcast_identity_hypothesis" if (status == "complete"
                                                           and deciding_unit == "identities"
                                                           and len(leader) == 1) else
                        ("screen_side" if status == "complete" else None)),
        "attribution_withheld": (attributed or {}).get("withheld"),
        # Who the broadcast named on the seat that holds the wins, when the seat is known and the name is
        # not. A closed 2-0 with two candidate names is a recovered set with an open naming question, and
        # collapsing that to one name would be inventing a roster entry; collapsing it to "unresolved"
        # would throw the set away.
        "participant_hypotheses": (attributed or {}).get("participant_hypotheses"),
        "winner_seat": (max(deciding, key=lambda k: deciding[k])
                        if (status == "complete" and deciding_unit == "sides" and deciding) else None),
        "applicable_formats": formats,
        "complete_under": complete_under, "open_under": open_under,
        "format_dependent": format_dependent,
        # The boundary and the score are different claims and are published separately. `concluded_by`
        # names which route or routes support "this set ended here"; `edges` carries what the tape showed on
        # each side, including the case where it showed nothing because the query stopped there.
        "concluded_by": decided_by,
        # Three claims, kept apart because the recording earns them in different amounts. A station clear
        # shows the players stopped (an observed end) and lets the pipeline propose a cut there; only a
        # tally reaching a target confirms a set completed. Collapsing the three into "complete" is how a
        # report comes to say a set ended where the structure evaluation says every grouping of these
        # games is still open -- and `status` keeps its footage meaning precisely so this block can
        # disagree with it in words instead of silently moving the boundary.
        "end_transition_observed": bool(boundary),
        "cut_proposed_by_pipeline": bool(boundary),
        "set_completion": {
            "state": "confirmed" if reached else (
                "proposed_cut_only" if boundary else "unresolved"),
            "target_supplied": bool(targets),
            "target_reached": bool(reached),
            "why": (None if reached else
                    "the tape shows the players stopping here and the pipeline cuts a set there; that is "
                    "not a confirmed completion, because no tally reached a target"
                    + ("" if targets else " and none was supplied")),
        },
        "boundary_confirmed": boundary,
        "footage_complete": bool(edges.get("footage_complete")),
        "edges": edges or None,
        "attribution": _attribution(games, links, header=header, band_map=band_map,
                                    mapping_questions=mapping_questions, attributed=attributed,
                                    pairings=pairings),
        "cut_mapping_resolution": [entry for entry in mapping_questions
                                   if entry.get("at") in {(cut_before or {}).get("at"),
                                                          (cut_after or {}).get("at")}],
        "games_without_winner": unnamed,
        "games_per_set_expected": games_per_set,
        "game_spans": [[g["start"], g["end"]] for g in games],
        "game_winners": [g.get("winner") for g in games],
        # Per link: who was named on each side before and after, and what the tally did. Kept beside the
        # verdict so a reviewer can disagree with the classification without re-running anything.
        "identities": [{"before": (l.get("identities") or {}).get("before"),
                        "after": (l.get("identities") or {}).get("after"),
                        "verdict": l.get("identity"), "approximate": True,
                        "share": l.get("identity_share"), "score_signal": l.get("score_signal")}
                       for l in links],
        "links": [{"verdict": l["verdict"], "at": l["at"], "reasons": l["reasons"],
                   "questions": l.get("questions") or [], "identity": l.get("identity"),
                   "identity_share": l.get("identity_share"), "dwell_seconds": l.get("dwell_seconds"),
                   "score_signal": l.get("score_signal"),
                   "states_seen": (l.get("gap") or {}).get("states"),
                   "unclassified_cues": (l.get("gap") or {}).get("unclassified"),
                   "positively_supported": l.get("positively_supported", False),
                   # Which fighter each side was showing, and whether the station was cleared: the two
                   # footage facts that tell a side swap from a broadcast renaming the same two seats.
                   "seat_continuity": l.get("seat_continuity"), "fighters": l.get("fighters"),
                   "transition": l.get("transition"),
                   "preferred_cues": (l.get("gap") or {}).get("preferred_cues"),
                   "corroborating_cues": (l.get("gap") or {}).get("corroborating_cues"),
                   "neutral_cues": (l.get("gap") or {}).get("neutral_cues"),
                   # Named so a reader can see what a verdict rested on without re-deriving it: which
                   # families agreed, which objected, and whether an event record was in the room at all.
                   "evidence_for": l.get("evidence_for"), "evidence_against": l.get("evidence_against"),
                   "basis": l.get("basis", "recording_only"),
                   "bracket": l.get("bracket")} for l in links],
        "questions": [q for l in links for q in (l.get("questions") or [])],
        "clip_end_at": clip_end,
        "clip_end_basis": (
            "last game's %s + %.1f s configured padding, applied once at the game layer and capped at "
            "that game's next opening; the set never re-pads a bound the game already padded and never "
            "reaches past it for a marking"
            % ("conclusion plus winner confirmation" if last.get("winner_confirmed_at")
               else "conclusion evidence", clip_pad_seconds)
            + (" (inherited already padded from the game clip)" if padded_already else ""))
        if clip_end is not None else None,
        # Kept beside the number so a reader can see which claim set it, and what is still unknown.
        # A game boundary the recording reads two ways is a property of the candidate, not only of the two
        # games it separates: set arithmetic happens here, so the record that reports a game count has to
        # say that one of its boundaries is disputed.
        "boundary_conflicts": _candidate_boundary_conflicts(games),
        "grouping_unresolved": bool(_candidate_boundary_conflicts(games)) or not reached,
        "clip_end_anchor": {"conclusion_event_at": last.get("conclusion_event_at"),
                            "winner_confirmed_at": last.get("winner_confirmed_at"),
                            "game_clip_end_at": last.get("clip_end_at"),
                            "set_end_at": last.get("end")},
        "clip_uncertainty": last.get("end_uncertainty"),
        # A one-game candidate may simply be all of the set that was watched, which is a different claim
        # from "these games are not one set".
        "single_game_candidate": bool(single),
        "edge_of_watched_span": bool(single),
        # Why a candidate stopped collecting: reaching the target is not the same reason as being cut by
        # evidence, and only the first is a won set.
        "closed_by_target": bool(reached),
        "cut_before": _cut_summary(cut_before),
        "cut_after": _cut_summary(cut_after),
        "missing_evidence": missing,
    }


def _unresolved(rounds, games, observations, clock, contradictions=()):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]], Sequence[Dict[str, Any]], int, Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Where the frames in hand do not settle it, and what would."""
    out = list(contradictions)
    for position, rnd in enumerate(rounds):
        # The round occupies the recording until the next one opens, which is where a reset that was
        # never read hides; measuring only to the observed end makes the span look safe. Once the end
        # itself was seen, though, the tape behind it is not this round's: a game that closed at a K.O.
        # and then held a menu for 216 s is a long *dwell*, not a round that outlived its clock, and
        # flagging it as one sent the investigation looking for a reset that had already been answered.
        observed_end = _end_is_observed(rnd) and rnd.get("end") is not None
        occupied = ((rnd.get("end") or rnd["start"]) if observed_end
                    else (rounds[position + 1]["start"] if position + 1 < len(rounds)
                          else (rnd.get("end") or rnd["start"]))) - rnd["start"]
        if occupied > clock + ROUND_CAP_SLACK:
            # A round cannot outlive its own clock, so a longer span means a reset was never read:
            # two rounds are in here, and the fix is frames, not a bigger threshold.
            out.append({"type": "round_longer_than_the_clock_allows", "at": rnd["start"],
                        "seconds": round(occupied, 1),
                        "would_resolve": "cached frames inside this span for the clock reset"})
        elif observed_end and position + 1 < len(rounds):
            # The dwell behind an observed end is not a bound on the round, but it is unaccounted tape
            # all the same, and an unreported gap is how "no rule was broken" got read as "the
            # boundaries are right". Named separately because the question differs: this span does not
            # need a reset found, it needs somebody to say what the 216 s of menu actually was.
            dwell = rounds[position + 1]["start"] - (rnd.get("end") or rnd["start"])
            if dwell > clock + ROUND_CAP_SLACK:
                out.append({"type": "dwell_after_observed_end", "at": rnd.get("end"),
                            "round_id": rnd.get("id"), "end_basis": rnd.get("end_basis"),
                            "seconds": round(dwell, 1), "next_round_start": rounds[position + 1]["start"],
                            "would_resolve": "the screens inside this span: post-match padding, or play "
                                             "that was classified as nothing"})
        if rnd.get("start_inferred"):
            out.append({"type": "round_start_not_observed", "at": rnd["start"],
                        "would_resolve": "the saved rows in front of this round read for a ROUND/READY/"
                                         "FIGHT banner, a full round clock off a pre-round screen or the "
                                         "transition into the HUD -- all free, and only then cached "
                                         "frames that were never sampled"})
        if not rnd.get("end_basis") or rnd.get("end_basis") == "recording_or_gap_edge":
            out.append({"type": "round_end_not_observed", "at": rnd.get("end"),
                        "would_resolve": "denser frames near the round end for K.O./TIME UP"})
        # Win dots are asked about at the round's **end**, where the pip for the round just taken is
        # drawn. Queuing the start sends the investigation to the frames that cannot answer it, which
        # is how a pilot spent 15 requests and never looked at a game conclusion.
        dots_at = rnd.get("round_end_at") or rnd.get("end") or rnd["start"]
        if not (rnd.get("win_dots_seen") or {}).values():
            out.append({"type": "win_dots_never_read", "at": dots_at, "round_start": rnd["start"],
                        "anchor": "round_end",
                        "would_resolve": "frames at the round end, whole and cropped to the "
                                         "pip row beside the clock"})
    for position, game in enumerate(games):
        # A game's winner and its end boundary are different claims and can disagree: the row naming the
        # winner can be two seconds after a legitimate K.O., or the conclusion can be a bound with
        # minutes of unwatched tape behind it. Both have to reach the queue, or `tracking_faults: []`
        # reads as "boundaries are right" when it only ever meant "no rule was broken".
        status = game.get("end_status") or ("observed" if game.get("boundary_certain") else "open")
        if status == "observed":
            continue
        following = games[position + 1]["start"] if position + 1 < len(games) else None
        conflict = game.get("end_conflict") or {}
        bound = game.get("end_uncertainty") or {}
        wide = (bound.get("seconds_wide") or 0.0)
        out.append({"type": "game_end_uncertain" if status != "bounded" else "game_end_bounded",
                    "at": game.get("conclusion_event_at"),
                    "end_status": status,
                    "end_bound_high": bound.get("high"),
                    "seconds_wide": bound.get("seconds_wide"),
                    "seconds_unwatched": bound.get("seconds_unwatched"),
                    "bounded_by": bound.get("bounded_by"),
                    "tape_after_conclusion_seconds": bound.get("tape_after_conclusion_seconds"),
                    "clock_frozen_after_end": bool((bound.get("live_bound") or {}).get(
                        "frozen_readings_after")),
                    # Three different reasons, because they need different frames: nothing was seen
                    # after the last falling clock, something was seen and it contradicts that clock, or
                    # the end is already pinned inside one sampling step.
                    "because": ("the clock kept being drawn %.3f s after the pip and the result screen "
                                "that concluded this round" % conflict["seconds_late_after_evidence"]
                                if conflict else
                                "%.3f s between the last instant this game was proved live and the %s "
                                "that says it had ended" % (wide, bound.get("bounded_by") or "opening")
                                if status == "bounded" else
                                "%.3f s of tape between the last readable clock and the next opening"
                                % wide),
                    "evidence_earlier_than_clock": conflict.get("evidence_earlier_than_clock") or [],
                    "winner": game.get("winner"), "next_game_start": following,
                    "claim_kept": "the winner stands on its own pip readings; only the boundary is open",
                    "would_resolve": ("nothing: the bracket is %.3f s wide and the tape inside it has "
                                      "already been watched -- quote it as a bound, or sample the "
                                      "master between the cached whole seconds" % wide
                                      if status == "bounded" else
                                      "clock or K.O. frames inside the unwatched span -- not a denser "
                                      "guess, and not the next game's start rebranded as this game's "
                                      "end")})
    for game in games:
        if game["status"] != "complete":
            out.append({"type": "game_incomplete", "at": game["start"], "rounds_seen": game["rounds"],
                        "basis": game["basis"],
                        "would_resolve": "result transition or post-match screen after the last round"})
        if game.get("winner") is None:
            # The game ended; who took it is what a set needs, and a menu cannot say it.
            out.append({"type": "game_winner_unobserved", "at": game["end"] or game["start"],
                        "anchor": "game_end", "rounds_seen": game["rounds"],
                        "basis": game["basis"],
                        "would_resolve": "side-specific win dots in the frames at the last round end"})
        support = game.get("winner_support") or {}
        if (game.get("winner") and not game.get("contested") and support.get("single_reading")
                and _finite(support.get("deciding_at"))):
            # The winner stands and still counts: one frame is a claim, not a contradiction, and the rules
            # that contest readings already exist. What this item says is that nothing was looked at twice,
            # and it names the instant the next look should start from. Measured 2026-11-05, this is the
            # shape the recording's winners usually have -- and the confirmation frame that settled the
            # 3,958.955 game sits 2.002 s after exactly such an anchor.
            out.append({"type": "game_winner_single_reading", "at": support["deciding_at"],
                        "anchor": "game_end", "side": support["side"],
                        "game_start": game["start"], "value": support["target"],
                        "frame": (support.get("frames") or [None])[0],
                        "frames_read_at_or_above_target": support.get("frame_count"),
                        "would_resolve": "the same pip row on the next cached frame after this one: a "
                                         "second look either carries the count or contradicts it, and "
                                         "either answer is worth more than one reading was"})
        signals = game.get("conclusion_signals") or {}
        for side, movement in (game.get("dot_regressions") or {}).items():
            # The pilot's own acceptance rule: a dot count that falls inside a game is a misread to
            # go and look at, not a round that was taken back. The queue item points at the reading
            # that fell, because that is where the frames are; the game start is kept as context only.
            # An undated fall has nothing to point at, so it falls back to the game start.
            fell_at = movement.get("at")
            out.append({"type": "win_dots_regressed",
                        "at": fell_at if _finite(fell_at) else game["start"],
                        "game_start": game["start"], "side": side,
                        "anchor": "game_rounds", "from": movement["from"], "to": movement["to"],
                        "would_resolve": "the frames where this side's pip row changed, whole and "
                                         "cropped to the row beside the clock"})
        for conflict in game.get("boundary_conflicts") or []:
            # Named on its own because it is a different ask from a contradicted conclusion: the frames to
            # look at are on the *boundary*, and what would settle it is the row at the next round's own
            # opening plus the callout on its first rows.
            out.append({"type": "game_boundary_conflict", "at": conflict["at"], "anchor": "game_start",
                        "readings_persisted": conflict["readings"],
                        "round_number_announced": (conflict.get("new_game_callout") or {}).get(
                            "round_number_announced"),
                        "alternative_grouping": {"start": game["start"], "end": game["end"]},
                        "would_resolve": conflict.get("would_resolve")})
        if game.get("contested"):
            out.append({"type": "game_conclusion_contradicted", "at": game["end"] or game["start"],
                        "anchor": "game_end", "reasons": game["contested_reasons"],
                        "next_round_number": signals.get("next_round_number"),
                        "would_resolve": "the frames across this boundary for the ROUND callout and "
                                         "the win-dot row"})
    # A clock reading is the cheapest continuity proof available, and only three quarters of the
    # cached gameplay rows carry one.
    gameplay = [o for o in observations if o["screen_state"] == "gameplay"]
    missing = [o for o in gameplay if o["round_clock"] is None]
    if gameplay and missing:
        out.append({"type": "round_clock_unread", "count": len(missing),
                    "of": len(gameplay),
                    "example_timestamps": [round(o["timestamp"], 1) for o in missing[:8]],
                    "would_resolve": "ask the vision pass for the clock digits explicitly"})
    return out


def _accounting(observations, rounds, games, sets=()):
    # type: (Sequence[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """Coverage of the footage by the tracker, separate from whether any of it is correct.

    Containment is asked of **one** game at a time. The test used to be two independent clauses -- some
    game starts at or before the row, and some game ends at or after it -- and a row sitting in the gap
    between two games satisfies both while being inside neither, so the coverage figure quietly counted
    the very gaps it exists to expose. That is 5 play rows of 200 on this trial's rehearsal and 3 of 59
    on the accepted stage-3 one: small, and in the direction of over-claiming. ``inside + between`` is
    now the whole play count by construction, and an incomplete game whose end was never seen covers
    from its start to the edge of what was watched, which is what it claims.
    """
    gameplay = [o for o in observations if o["screen_state"] == "gameplay"]
    inside = sum(1 for o in gameplay if _inside_a_game(o["timestamp"], games))
    return {
        "observations": len(observations),
        "gameplay_observations": len(gameplay),
        "rounds": len(rounds),
        "games": len(games),
        "games_incomplete": sum(1 for g in games if g["status"] != "complete"),
        # A bare count of sets cannot distinguish "one set recovered" from "one fictitious set merged
        # from two matches" from "no set could be attributed at all", so the statuses are what get kept.
        "sets": {"candidates": len(sets),
                 "complete": sum(1 for s in sets if s["status"] == "complete"),
                 "partial": sum(1 for s in sets if s["status"] == "partial"),
                 "membership_supported": sum(1 for s in sets if s["membership"] == "supported"),
                 "membership_assumed": sum(1 for s in sets if s["membership"] == "assumed"),
                 "membership_contested": sum(1 for s in sets if s["membership"] == "contested"),
                 "single_game": sum(1 for s in sets if s["membership"] == "single"),
                 "ambiguous_links_kept_as_alternatives": sum(len(s.get("alternative_groupings") or [])
                                                             for s in sets),
                 "questions": sum(len(s.get("questions") or []) for s in sets),
                 "games_covered": sum(s["games"] for s in sets),
                 "cuts": sum(1 for s in sets if s.get("cut_after"))},
        "gameplay_inside_games": inside,
        "gameplay_between_games": len(gameplay) - inside,
        "clock_readings": sum(1 for o in gameplay if o["round_clock"] is not None),
    }


def _inside_a_game(timestamp, games):
    # type: (float, Sequence[Dict[str, Any]]) -> bool
    """True when a single reported game spans this instant. A gap between two games is not a game."""
    return any(g["start"] <= timestamp
               and timestamp <= (float("inf") if g.get("end") is None else g["end"])
               for g in games)


def write_trace(result, path):
    # type: (Dict[str, Any], Any) -> None
    """Persist one compact trace so the chat never has to hold the whole recording."""
    with open(str(path), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=list)
