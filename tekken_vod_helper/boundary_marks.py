"""Recover complete sets from a classified timeline and untrusted live markings.

Live markings are approximate. A stream operator marks a match when they notice it starting, which
can be seconds early or minutes late; marks get duplicated, skipped entirely, or land in the middle of
play. The recoverable unit here is therefore the **play itself**: sets are grouped from what the
timeline shows, markings are used only as anchors to record against and to corroborate, and a set the
marking never mentions is still found.

Boundaries are decided by evidence, not by convention. A stage grid or a controller prompt is strong
evidence of where a set begins or ends, but its absence is not evidence that no set is there: a
broadcast can cut into a match already in progress. When the evidence stops short, the set is
**preserved as partial** at the furthest point the timeline supports and labelled as such, never
dropped and never extended into the next marking's boundary.
"""
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .boundary_names import canonicalise_rows, pair_of, same_pair

# Consecutive confident gameplay rows closer together than this belong to one block: the round resets,
# stage loading and character menus between games of a set routinely run a minute or two.
_BLOCK_GAP_SECONDS = 240.0
# Candidate cuts closer together than this describe one transition, so only the strongest survives.
_CUT_CLUSTER_SECONDS = 120.0
# A set's games are this far apart at most before the gap itself implies the match ended.
_SET_GAP_SECONDS = 900.0
# How far to look outside a play block for boundary evidence.
_SEARCH_SECONDS = 420.0
# Play on both sides of a *visible* scene change within this window is a recording cut: the game
# itself changed with no transition, so neither match has an edge on tape. Names are excluded on
# purpose -- measured on BB9 the broadcast header changed three times mid-round while the round
# clock kept counting (43 -> 42, 52 -> 47, 49 -> 45), so a name change here proves nothing.
_HARD_CUT_SECONDS = 20.0
# Tekken 8 bracket sets are normally first to three games.
_SET_TARGET = 3
# A mark this far from any play is not an anchor for anything; it is reported, never guessed with.
_ANCHOR_SEARCH_SECONDS = 900.0

_SET_STATES = ("gameplay",)
_END_EVIDENCE = ("controller_prompt", "post_match_menu", "controller_disconnected")
_START_EVIDENCE = ("stage_select", "character_select", "vs_screen", "stage_intro")


def _finite(value):
    # type: (Any) -> bool
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _confident(row, min_confidence=0.5):
    # type: (Dict[str, Any], float) -> bool
    return _finite(row.get("confidence")) and row["confidence"] >= min_confidence


def _names(row):
    # type: (Dict[str, Any]) -> Optional[Tuple[str, str]]
    return pair_of(row)


def play_blocks(rows, min_confidence=0.5, gap=_BLOCK_GAP_SECONDS):
    # type: (Sequence[Dict[str, Any]], float, float) -> List[Dict[str, Any]]
    """Contiguous stretches of confident gameplay: the units that must never be silently lost.

    Only elapsed time breaks a block. Splitting on a pairing change instead would let a name reading
    decide where play ends, and names are an estimate: that is how one tag seen three ways became
    three matches. A pairing change inside a block is recorded, not acted on.
    """
    indices = [i for i, r in enumerate(rows)
               if r.get("state") in _SET_STATES and _confident(r, min_confidence)]
    blocks = []  # type: List[List[int]]
    for i in indices:
        if blocks and rows[i]["timestamp"] - rows[blocks[-1][-1]]["timestamp"] <= gap:
            blocks[-1].append(i)
        else:
            blocks.append([i])
    out = []
    for group in blocks:
        pairings = []
        for i in group:
            name = _names(rows[i])
            if name and not any(same_pair(name, seen) for seen in pairings):
                pairings.append(name)
        games = sum(1 for i in group if _score(rows[i]) and _is_new_game_start(rows, group, i))
        out.append({"start": rows[group[0]]["timestamp"], "end": rows[group[-1]]["timestamp"],
                    "first_index": group[0], "last_index": group[-1],
                    "rows": len(group), "pairings": pairings,
                    "pairing_uncertain": len(pairings) > 1,
                    "observed_games": max(games, 1)})
    return out


def possible_play_blocks(rows, min_confidence=0.5, gap=_BLOCK_GAP_SECONDS):
    # type: (Sequence[Dict[str, Any]], float, float) -> List[Dict[str, Any]]
    """Gameplay the classifier was not sure about: not evidence, and not nothing either.

    Dropping these rows from the denominator is what let a timeline of five 0.4-confidence gameplay
    readings report ``fraction_accounted: 1.0`` with no sets at all. Possible play has to stay on the
    books as something to go and look at, reported separately from the confident coverage that was
    actually verified -- never merged into it, which would trade a false 100% for a false set.
    """
    indices = [i for i, r in enumerate(rows)
               if r.get("state") in _SET_STATES and _finite(r.get("confidence"))
               and r["confidence"] < min_confidence]
    groups = []  # type: List[List[int]]
    for i in indices:
        if groups and rows[i]["timestamp"] - rows[groups[-1][-1]]["timestamp"] <= gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [{"start": rows[g[0]]["timestamp"], "end": rows[g[-1]]["timestamp"], "rows": len(g),
             "max_confidence": max(rows[i]["confidence"] for i in g)} for g in groups]


def _score(row):
    # type: (Dict[str, Any]) -> Optional[Tuple[int, int]]
    a, b = row.get("score1"), row.get("score2")
    if isinstance(a, int) and not isinstance(a, bool) and isinstance(b, int) and not isinstance(b, bool):
        if a >= 0 and b >= 0:
            return (a, b)
    return None


def _is_new_game_start(rows, group, i):
    # type: (Sequence[Dict[str, Any]], List[int], int) -> bool
    """A game begins where the broadcast score returns to 0-0 after play, or at the block's first row."""
    position = group.index(i)
    if position == 0:
        return True
    current, previous = _score(rows[i]), _score(rows[group[position - 1]])
    return bool(current and previous and current == (0, 0) and previous != (0, 0))


def _decided(score, target):
    # type: (Optional[Tuple[int, int]], int) -> bool
    return bool(score and max(score) >= target)


def _characters(row):
    # type: (Dict[str, Any]) -> Optional[Tuple[str, str]]
    """The two fighters the game itself is drawing, or None when the frame did not say."""
    left, right = row.get("character1"), row.get("character2")
    if not isinstance(left, str) or not isinstance(right, str):
        return None
    left, right = left.strip().casefold(), right.strip().casefold()
    if not left or not right:
        return None
    return (left, right)


def hard_cuts(rows, blocks=None, window=_HARD_CUT_SECONDS):
    # type: (Sequence[Dict[str, Any]], Optional[List[Dict[str, Any]]], float) -> List[Dict[str, Any]]
    """Places where the *game on screen* changed between adjacent confident play, with no transition.

    This is the signature of a truncated recording: the previous set's conclusion was never broadcast
    and the next was already underway when the camera arrived, so neither side has an edge on tape and
    both are reported truncated instead of a clean boundary being invented.

    Only a visible scene change qualifies. The pairing never does, alone or corroborated: the
    broadcast header changes players and score boxes in the middle of a live round, measured three
    times on BB9 (round clock 43 -> 42, 52 -> 47, 49 -> 45 with the same stage, the same characters
    and health already spent), so cutting there cuts the overlay, not the recording.
    """
    out = []
    play = [i for i, row in enumerate(rows)
            if row.get("state") in _SET_STATES and _confident(row)]
    for earlier, later in zip(play, play[1:]):
        gap = rows[later]["timestamp"] - rows[earlier]["timestamp"]
        if gap > window:
            continue
        before, after = _characters(rows[earlier]), _characters(rows[later])
        if before and after and before != after:
            out.append({"at": rows[earlier]["timestamp"], "before": list(before),
                        "after": list(after), "gap_seconds": round(gap, 3),
                        "basis": "characters_changed_without_transition"})
    return out


def _best_start(rows, lower, upper, index, target=_SET_TARGET):
    # type: (Sequence[Dict[str, Any]], float, float, int, int) -> Tuple[float, str, bool]
    """Latest boundary evidence at or before the play, earliest if there is none."""
    candidates = []
    for i in range(max(0, index - 400), index + 1):
        t = rows[i]["timestamp"]
        if t < lower - 1e-6 or t > upper:
            continue
        state = rows[i].get("state")
        if state == "stage_select" and _confident(rows[i]):
            if i == 0 or rows[i - 1].get("state") != "stage_select":
                candidates.append((t, "stage_select", True))
        elif state in _START_EVIDENCE and _confident(rows[i]) and i and rows[i - 1].get("state") in _END_EVIDENCE:
            candidates.append((t, str(state), False))
    if not candidates:
        return upper, "play_observed", False
    strong = [c for c in candidates if c[2]]
    chosen = max(strong or candidates, key=lambda c: c[0])
    return chosen[0], chosen[1], chosen[2]


def _next_play_pairing(rows, after_index, limit):
    # type: (Sequence[Dict[str, Any]], int, float) -> Optional[Tuple[str, str]]
    """The pairing of the next confident gameplay at or after this point, if the video shows any."""
    for i in range(after_index, min(len(rows), after_index + 400)):
        row = rows[i]
        if row["timestamp"] > limit:
            return None
        if row.get("state") in _SET_STATES and _confident(row):
            return _names(row)
    return None


def _best_end(rows, lower, upper, index, block_end, target=_SET_TARGET, own_pairing=None):
    # type: (Sequence[Dict[str, Any]], float, float, int, float, int, Optional[Tuple[str, str]]) -> Tuple[float, str, bool]
    """Where the set concluded: a prompt or post-match screen that nothing of the same match follows.

    Completion is read structurally, not from the scoreboard. On BB9 the classified ``score1``/``score2``
    fields hold round wins (1-0, 1-1) rather than games won, so requiring a set-score target marks every
    set partial while proving nothing. What does show a match ending is the broadcast moving on: the next
    confident play belongs to a different pairing, or no further play appears inside the search window.
    A legible decided score is taken as corroboration when it happens to be there.
    """
    candidates = []
    for i in range(index, min(len(rows), index + 400)):
        t = rows[i]["timestamp"]
        if t < block_end - 1e-6:
            continue
        if t > upper:
            break
        state = rows[i].get("state")
        if state not in _END_EVIDENCE or not _confident(rows[i]):
            continue
        later = _next_play_pairing(rows, i + 1, upper)
        # A prompt is firm when the broadcast demonstrably moved on, or when it showed a decided score.
        # Finding *no* later play is not the same as finding a different pairing: an unclassified tail,
        # a search limit or a disconnect into unknown footage is missing evidence, not a concluded set,
        # and calling it complete is how a dropped recording became a clean ending.
        moved_on = later is not None and not same_pair(later, own_pairing)
        candidates.append((t, str(state), moved_on or _decided(_score(rows[i]), target)))
    if not candidates:
        return block_end, "play_observed", False
    firm = [c for c in candidates if c[2]]
    chosen = min(firm or candidates, key=lambda c: c[0])
    return chosen[0], chosen[1] if firm else chosen[1] + "_undetermined", bool(firm)


def play_stretches(rows, min_confidence=0.5, gap=_BLOCK_GAP_SECONDS):
    # type: (Sequence[Dict[str, Any]], float, float) -> Tuple[List[List[int]], Dict[int, int]]
    """Maximal runs of confident play, broken by any screen or by a pause in the broadcast.

    A stretch is the unit a pairing belongs to: one continuous view of one fight. Asking what the play
    "near" a row reported instead reaches across a menu into the next set, which is how a match with
    fewer play rows than the look-ahead reported its *opponent's* names and got split in two.
    """
    stretches = []  # type: List[List[int]]
    current = []  # type: List[int]
    for i, row in enumerate(rows):
        if row.get("state") in _SET_STATES and _confident(row, min_confidence):
            previous = current[-1] if current else None
            # A recording cut leaves no menu behind, so the scene itself is what ends a stretch: the
            # fight on screen changed between two adjacent readings of play.
            scene_changed = (previous is not None
                             and bool(_characters(rows[previous])) and bool(_characters(row))
                             and _characters(rows[previous]) != _characters(row))
            if current and (scene_changed or
                            rows[i]["timestamp"] - rows[previous]["timestamp"] > gap):
                stretches.append(current)
                current = []
            current.append(i)
        elif current:
            stretches.append(current)
            current = []
    if current:
        stretches.append(current)
    index_of = {}
    for identifier, group in enumerate(stretches):
        for i in group:
            index_of[i] = identifier
    return stretches, index_of


def _stretch_pairing(rows, group):
    # type: (Sequence[Dict[str, Any]], Sequence[int]) -> Optional[Tuple[str, str]]
    """The pairing that a stretch of play reports by majority, so one frame cannot rename the fight.

    Ties fall to the reading that appears first, because a stretch belongs to one pairing and an exactly
    half-and-half reading is an overlay change inside it -- reported as a warning, never a boundary.
    """
    tally = {}  # type: Dict[Tuple[str, str], int]
    order = []  # type: List[Tuple[str, str]]
    for i in group:
        name = _names(rows[i])
        if not name:
            continue
        for seen in tally:
            if same_pair(seen, name):
                tally[seen] += 1
                break
        else:
            tally[name] = 1
            order.append(name)
    if not tally:
        return None
    return max(order, key=lambda key: (tally[key], -order.index(key)))


def _screens_between(rows, start_index, end_index):
    # type: (Sequence[Dict[str, Any]], int, int) -> List[Tuple[float, str]]
    """Confident boundary screens inside a stretch of non-play, in the order the tape shows them."""
    found = []
    for i in range(start_index + 1, end_index):
        row = rows[i]
        if not _confident(row):
            continue
        state = row.get("state")
        if state == "controller_assignment":
            found.append((row["timestamp"], "controller_assignment"))
        elif state in _END_EVIDENCE:
            found.append((row["timestamp"], "concluded_prompt"))
        elif state in _START_EVIDENCE:
            found.append((row["timestamp"], "episode_opener"))
    return found


def _incoming_opener(screens):
    # type: (Sequence[Tuple[float, str]]) -> Optional[float]
    """The screen that opens the episode which follows, or None when the stretch does not show one.

    The screens between two stretches of play belong to two different sets: the grids of the set that
    was ending come before its concluding prompt, and the grids of the set that is beginning come after
    it. Anchoring on the first screen of the stretch therefore opens the new set at the previous set's
    own between-game grid, and anchoring on the last opens it at the VS screen instead of its stage
    grid, which is the boundary the reviewer has to be able to trust.
    """
    prompts = [position for position, (_, kind) in enumerate(screens)
               if kind == "concluded_prompt"]
    if prompts:
        following = [when for when, kind in screens[prompts[-1] + 1:] if kind == "episode_opener"]
        return following[0] if following else screens[prompts[-1]][0]
    # No prompt at all: the incoming episode is the unbroken run of screens right up to the play.
    run = [when for when, kind in reversed(screens) if kind == "episode_opener"]
    return run[-1] if len(run) == len(screens) and run else None


def _reset_after(rows, opener_time, later_index, target):
    # type: (Sequence[Dict[str, Any]], float, int, int) -> bool
    """Whether the play following a concluded prompt starts from a reset score.

    This is the only way a same-pairing reset -- a Grand Final restarted with both players unchanged --
    is visible at all, and it needs three things together: a prompt, a score that had reached the
    target, and play afterwards reporting a fresh count. A score box on its own proves nothing, because
    on BB9 one was observed going 3-0 back to 0-0 inside a single live round.
    """
    if opener_time is None:
        return False
    for i in range(later_index, min(len(rows), later_index + 40)):
        row = rows[i]
        if row.get("state") in _SET_STATES and _confident(row):
            score = _score(row)
            return bool(score) and max(score) == 0
    return False


def _cut_moments(rows, set_gap=_SET_GAP_SECONDS, cluster=_CUT_CLUSTER_SECONDS, target=_SET_TARGET):
    # type: (Sequence[Dict[str, Any]], float, float, int) -> List[Tuple[float, str]]
    """Moments where the recording itself says a set ended, judged so no single reading can cut.

    Every candidate here is structural: a controller being handed over, a screen between two stretches
    of play, a break long enough to be an intermission, the game on screen changing with no transition,
    or a concluded prompt followed by a reset score under the same pairing. The broadcast pairing is
    used only to *corroborate* a screen that is already there, never to open one, because the header
    was measured changing mid-round -- and a boundary is exactly the thing that must not move when it
    does. A pairing change with no screen beside it is returned separately as a warning so the
    uncertainty reaches a reviewer instead of being resolved by guesswork.
    """
    play = [i for i, row in enumerate(rows)
            if row.get("state") in _SET_STATES and _confident(row)]
    stretches, stretch_of = play_stretches(rows)
    moments = []  # type: List[Tuple[float, str]]

    for earlier, later in zip(play, play[1:]):
        earlier_t, later_t = rows[earlier]["timestamp"], rows[later]["timestamp"]
        gap = later_t - earlier_t
        boundary = (earlier_t + later_t) / 2.0
        if stretch_of.get(earlier) == stretch_of.get(later):
            continue  # one continuous view of one fight: nothing here ended it
        if gap > set_gap:
            moments.append((boundary, "play_gap"))
            continue
        screens = _screens_between(rows, earlier, later)
        assignment = [t for t, kind in screens if kind == "controller_assignment"]
        if assignment:
            moments.append((assignment[0], "controller_assignment"))
            continue
        before = _stretch_pairing(rows, stretches[stretch_of[earlier]])
        after = _stretch_pairing(rows, stretches[stretch_of[later]])
        pairing_changed = bool(before and after and not same_pair(before, after))
        opener = _incoming_opener(screens)
        if screens and pairing_changed and opener is not None:
            moments.append((opener, "new_pairing_after_screen"))
            continue
        concluded = [t for t, kind in screens if kind == "concluded_prompt"]
        decided_before = any(_decided(_score(rows[i]), target)
                             for i in range(earlier, later + 1) if rows[i].get("state") in _END_EVIDENCE)
        if (concluded and opener is not None and decided_before
                and _reset_after(rows, opener, later, target)):
            moments.append((opener, "same_pairing_reset"))
            continue
        before_chars, after_chars = _characters(rows[earlier]), _characters(rows[later])
        if gap <= _HARD_CUT_SECONDS and before_chars and after_chars and before_chars != after_chars:
            # Live play of two different scenes back to back: the recording cut, so neither set has
            # an edge on tape and both sides get reported truncated.
            moments.append((earlier_t, "hard_cut"))

    # Keep the strongest reason in any cluster of candidates: one match transition should produce one
    # boundary, not a dozen adjacent ones fighting over the same rows.
    kept = []  # type: List[Tuple[float, str]]
    for when, kind in sorted(moments):
        if kept and when - kept[-1][0] < cluster:
            if _CUT_STRENGTH[kind] > _CUT_STRENGTH[kept[-1][1]]:
                kept[-1] = (when, kind)
            continue
        kept.append((when, kind))
    return kept


_CUT_STRENGTH = {"pairing_change_unconfirmed": 0, "pairing_change_persisted": 1,
                 "new_pairing_after_screen": 2, "same_pairing_reset": 2,
                 "controller_assignment": 3, "hard_cut": 4, "play_gap": 5}


def overlay_changes(rows):
    # type: (Sequence[Dict[str, Any]]) -> List[Tuple[float, str]]
    """Where the broadcast header reported different players without the play agreeing.

    These are warnings, never boundaries: a header corrected mid-round and a header announcing a new
    set look identical in the name field alone, and the mid-round kind was confirmed three times on BB9
    against the round clock. Reporting them keeps the uncertainty in the output, where a reviewer or the
    investigation pass can act on it, instead of the segmenter guessing which one it was.
    """
    play = [i for i, row in enumerate(rows)
            if row.get("state") in _SET_STATES and _confident(row)]
    stretches, stretch_of = play_stretches(rows)
    out = []
    for earlier, later in zip(play, play[1:]):
        before, after = _names(rows[earlier]), _names(rows[later])
        if not before or not after or same_pair(before, after):
            continue
        same_view = stretch_of.get(earlier) == stretch_of.get(later)
        if not same_view:
            one = _stretch_pairing(rows, stretches[stretch_of[earlier]])
            two = _stretch_pairing(rows, stretches[stretch_of[later]])
            if one and two and not same_pair(one, two):
                continue  # the play itself changed pairing: a boundary candidate, not a warning
        out.append(((rows[earlier]["timestamp"] + rows[later]["timestamp"]) / 2.0,
                    "overlay_change_inside_play" if same_view
                    else "overlay_change_across_screen"))
    return out


def recover_sets(rows, marks=(), pad_before=0.0, pad_after=0.0, set_target=_SET_TARGET,
                 min_confidence=0.5, duration=None, split_on_names="corroborate", name_mode="cluster"):
    # type: (Sequence[Dict[str, Any]], Iterable[Any], float, float, int, float, Optional[float], str) -> Dict[str, Any]
    """Recover every set the timeline supports, using markings only as approximate anchors.

    Args:
        rows: Classified timeline rows in chronological order.
        marks: Live markings -- timestamps, or dicts with a ``start``/``timestamp`` key. May be empty,
            duplicated, offset, or missing whole matches; none of them can hide or invent a set.
        pad_before: Seconds of lead-in to keep before the evidence boundary, for clip cuts.
        pad_after: Seconds of tail to keep after the evidence boundary, for clip cuts.
        set_target: Games needed to decide a set. Corroboration only: the classified score fields hold
            round wins on real footage, so nothing here requires them to be readable or right.
        min_confidence: Classification confidence needed to count a row as evidence at all.
        duration: Recording length, used only to clamp clip padding.
        split_on_names: How much weight the broadcast pairing carries. ``corroborate`` (default) opens
            a new set only when names change *and* structural evidence agrees; ``always`` also splits
            on a name change alone, which is how a spelling flicker invents matches; ``never`` ignores
            names for grouping entirely.

    Returns:
        ``{"sets": [...], "residuals": [...], "marks": [...], "coverage": {...}}`` where every set
        records its boundary evidence and whether it is partial or truncated, every mark records the
        offset between the anchor and the evidence, and no confident gameplay row is left unaccounted.
    """
    for i, row in enumerate(rows):
        if not _finite(row.get("timestamp")):
            raise ValueError("row {} has a non-finite timestamp".format(i))
    ordered = sorted(range(len(rows)), key=lambda i: rows[i]["timestamp"])
    rows = [rows[i] for i in ordered]
    # Cluster the broadcast names before comparing anything. Names are an estimate, so a spelling
    # change must never look like a player change: without this step BB9's `Spire2929`/`Spitfire2929`
    # flicker alone turned 9 matches into 301.
    if name_mode != "raw":
        rows = canonicalise_rows(rows)

    anchors = _normalise_marks(marks)
    blocks = play_blocks(rows, min_confidence)
    cuts = hard_cuts(rows, blocks)
    cut_times = {c["at"] for c in cuts}
    edge = (rows[0]["timestamp"] if rows else 0.0,
            duration if _finite(duration) else (rows[-1]["timestamp"] if rows else 0.0))

    # Cut moments tile the recording, so every confident play row lands in exactly one segment by
    # construction. Coverage is a property of the partition, not something to re-check afterwards.
    moments_all = _cut_moments(rows, target=set_target)
    moments = moments_all
    warnings = overlay_changes(rows)
    if split_on_names == "always":
        # Kept only to demonstrate the cost of trusting the header: this is the mode that turned one
        # tag read three ways into 301 matches, so the comparison has to stay measurable.
        moments = sorted(moments + [(when, "pairing_change_persisted") for when, _ in warnings])
    elif split_on_names == "never":
        moments = [(when, kind) for when, kind in moments if kind != "new_pairing_after_screen"]

    sets = []
    unconfirmed = [when for when, _ in warnings]
    bounds = [edge[0]] + [when for when, _ in moments] + [edge[1]]
    # A window's basis is the cut that *opened* it: `play_gap` on a set means "this set began after
    # a break in the broadcast", which is what a reviewer needs, not why the previous one ended.
    openings = ["first_play"] + [kind for _, kind in moments]
    for position in range(len(bounds) - 1):
        lower, upper = bounds[position], bounds[position + 1]
        closed = position == len(bounds) - 2
        block = _window_play(rows, lower, upper, min_confidence, closed)
        if block is None:
            continue
        span = _recover_one(rows, block, anchors, cut_times, edge, set_target,
                            pad_before, pad_after, lower, upper)
        span["split_basis"] = openings[position]
        # A header change the footage did not corroborate is kept as a warning on the set it sits in,
        # so a human can look there instead of the recovery silently choosing to merge or split.
        span["unconfirmed_pairing_change"] = [round(w, 3) for w in unconfirmed if lower <= w <= upper]
        sets.append(span)
    sets.sort(key=lambda s: s["start"])
    _clip_to_neighbours(sets, edge, pad_before, pad_after)
    _assign_marks(sets, anchors)

    assigned = [(s["play_start"], s["play_end"]) for s in sets]
    residuals = []
    for block in blocks:
        if any(not (block["end"] < a or block["start"] > b) for a, b in assigned):
            continue
        residuals.append({
            "start": block["start"], "end": block["end"],
            "gameplay_rows": block["rows"],
            "pairings": [list(p) for p in block["pairings"][:4]],
            "reason": _residual_reason(block, rows, cut_times, edge),
        })
    # Possible play never becomes a set, but it never disappears either: it is retained as an
    # unresolved span with the investigation that would settle it.
    investigation = []
    for block in possible_play_blocks(rows, min_confidence):
        if any(not (block["end"] < a or block["start"] > b) for a, b in assigned):
            continue
        investigation.append({
            "start": block["start"], "end": block["end"], "gameplay_rows": block["rows"],
            "max_confidence": block["max_confidence"], "reason": "possible_play_below_confidence",
            "would_resolve": "denser frames across this span, reclassified at the confidence floor",
        })
    residuals.extend({"start": item["start"], "end": item["end"],
                      "gameplay_rows": item["gameplay_rows"], "pairings": [],
                      "reason": item["reason"], "confidence_floor": min_confidence,
                      "max_confidence": item["max_confidence"]} for item in investigation)
    total_rows = sum(b["rows"] for b in blocks)
    inside = sum(s["gameplay_rows"] for s in sets)
    possible_rows = sum(b["rows"] for b in possible_play_blocks(rows, min_confidence))
    overlaps = [(round(a["end"], 3), round(b["start"], 3)) for a, b in zip(sets, sets[1:])
                if a["end"] > b["start"]]
    return {
        "sets": sets,
        "overlaps": overlaps,
        "residuals": residuals,
        "investigation": investigation,
        "overlay_changes": [{"at": round(when, 3), "kind": kind} for when, kind in warnings],
        "marks": anchors,
        "hard_cuts": cuts,
        "coverage": {
            "play_blocks": len(blocks),
            "gameplay_rows": total_rows,
            "rows_inside_sets": inside,
            "rows_outside_sets": total_rows - inside,
            # Confident rows only. Possible play is counted beside it, never inside it, so a run that
            # looked at nothing cannot report full coverage of what it did not look at.
            "fraction_accounted": 1.0 if not total_rows else (
                inside + sum(r["gameplay_rows"] for r in residuals
                             if r["reason"] != "possible_play_below_confidence")) / float(total_rows),
            "possible_play_rows": possible_rows,
            "possible_play_retained": sum(i["gameplay_rows"] for i in investigation),
        },
    }


def _clip_to_neighbours(sets, edge, pad_before, pad_after):
    # type: (List[Dict[str, Any]], Tuple[float, float], float, float) -> None
    """Pad each clip from its evidence boundary, without ever reaching into a neighbour's clip.

    Padding is what turns a set boundary into a cuttable clip, and it is deliberately configurable
    rather than derived from the next marking: markings are approximate, the evidence is not. The
    recording floor is time zero, not the first classified row, because a VOD whose classification
    starts mid-broadcast still has picture before it.
    """
    floor, ceiling = 0.0, max(edge[1], edge[0])
    for position, span in enumerate(sets):
        earliest = floor if position == 0 else sets[position - 1]["clip_end"]
        span["clip_start"] = min(span["start"], max(earliest, span["start"] - pad_before))
    for position in range(len(sets) - 1, -1, -1):
        span = sets[position]
        latest = ceiling if position == len(sets) - 1 else sets[position + 1]["clip_start"]
        span["clip_end"] = max(span["end"], min(latest, span["end"] + pad_after))
        span["clip_start"] = min(span["clip_start"], span["clip_end"])


def _normalise_marks(marks):
    # type: (Iterable[Any]) -> List[Dict[str, Any]]
    out = []
    for n, mark in enumerate(list(marks or [])):
        value = mark
        if isinstance(mark, dict):
            value = mark.get("start", mark.get("timestamp", mark.get("start_time")))
        if not _finite(value):
            out.append({"index": n, "timestamp": None, "usable": False,
                        "reason": "mark has no finite timestamp"})
            continue
        out.append({"index": n, "timestamp": float(value), "usable": True})
    return out


def _window_play(rows, lower, upper, min_confidence=0.5, closed=False):
    # type: (Sequence[Dict[str, Any]], float, float, float, bool) -> Optional[Dict[str, Any]]
    """The confident play inside one window between two cuts.

    Taken from the rows themselves rather than from pre-split blocks: a block may straddle a cut, and
    letting two windows claim it would double-count play and make overlapping sets look legitimate.
    Windows are half-open for the same reason -- a row landing exactly on a boundary belongs to one
    match, not both -- except the last, which closes at the end of the recording.
    """
    indices = [i for i, row in enumerate(rows)
               if lower - 1e-6 <= row["timestamp"] and (row["timestamp"] < upper or closed)
               and row.get("state") in _SET_STATES and _confident(row, min_confidence)]
    if not indices:
        return None
    pairings = []
    for i in indices:
        name = _names(rows[i])
        if name and not any(same_pair(name, seen) for seen in pairings):
            pairings.append(name)
    group = sorted(indices)
    games = sum(1 for i in group if _score(rows[i]) and _is_new_game_start(rows, group, i))
    return {"start": rows[group[0]]["timestamp"], "end": rows[group[-1]]["timestamp"],
            "first_index": group[0], "last_index": group[-1], "rows": len(group),
            "pairings": pairings, "pairing_uncertain": len(pairings) > 1,
            "observed_games": max(games, 1)}


def _recover_one(rows, block, anchors, cut_times, edge, target, pad_before, pad_after,
                 previous_play_end, next_play_start):
    # type: (Sequence[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], set, Tuple[float, float], int, float, float, float, float) -> Dict[str, Any]
    first, last = block, block
    play_start, play_end = first["start"], last["end"]
    # Each set is confined to the stretch of recording between its two cuts, so sets tile the
    # broadcast and cannot overlap: a prompt found one window further on belongs to a different match,
    # and reaching for it manufactured two sets ending at the same second. Evidence just outside the
    # window is not lost -- `clip_start`/`clip_end` padding exists precisely to widen the cut there.
    lower, upper = previous_play_end, next_play_start
    start_at, start_kind, start_firm = _best_start(rows, lower, play_start, first["first_index"], target)
    own = block["pairings"][0] if block["pairings"] else None
    end_at, end_kind, end_firm = _best_end(rows, play_start, upper, last["last_index"], play_end,
                                           target, own)
    start_at = min(max(start_at, lower), play_start)
    end_at = max(min(end_at, upper), play_end)

    # A cut truncates an edge only when it is *at* that edge. One-sided proximity let a cut at 560 s
    # declare a set that ended at 160 s truncated, which turns a missing boundary into a claim about
    # the recording; missing evidence and lost footage are different statements and stay separate.
    def _at_edge(value):
        # type: (float) -> bool
        return any(abs(value - c) <= _HARD_CUT_SECONDS for c in cut_times)

    truncated_start = start_at == play_start and _at_edge(play_start)
    truncated_end = end_at == play_end and _at_edge(play_end)
    at_edge_end = abs(play_end - edge[1]) <= _HARD_CUT_SECONDS

    partial = []
    if not start_firm:
        partial.append("start")
    if not end_firm:
        partial.append("end")

    return {
        "start": start_at, "end": end_at,
        "clip_start": start_at, "clip_end": end_at,
        "play_start": play_start, "play_end": play_end,
        "gameplay_rows": block["rows"],
        "observed_games": block["observed_games"],
        "pairings": [list(p) for p in block["pairings"][:4]],
        "pairing_uncertain": bool(block["pairing_uncertain"]),
        "score_at_end": list(_score(rows[block["last_index"]]) or []),
        "evidence_is_estimate": {"names": True, "scores": True},
        "start_evidence": start_kind, "end_evidence": end_kind,
        "partial": partial,
        "truncated": {"start": bool(truncated_start),
                      "end": bool(truncated_end or at_edge_end)},
        "marks_inside": [], "mark_offsets": [],
    }


def _residual_reason(block, rows, cut_times, edge):
    # type: (Dict[str, Any], Sequence[Dict[str, Any]], set, Tuple[float, float]) -> str
    near_cut = any(abs(block["start"] - c) <= _HARD_CUT_SECONDS or abs(block["end"] - c) <= _HARD_CUT_SECONDS
                   for c in cut_times)
    if near_cut:
        return "truncated_recording"
    if abs(block["end"] - edge[1]) <= _HARD_CUT_SECONDS or abs(block["start"] - edge[0]) <= _HARD_CUT_SECONDS:
        return "recording_edge"
    if len(block["pairings"]) > 1:
        return "pairing_changed_without_boundary_evidence"
    return "no_boundary_evidence"


def _assign_marks(sets, anchors):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> None
    """Record each mark against the play it actually falls in, as an offset -- never a boundary.

    The offset is measured to the play, not to the evidence boundary, because that is the distance the
    marking error has to explain: how late the operator was relative to what the video shows.
    """
    for mark in anchors:
        if not mark["usable"]:
            continue
        if not sets:
            mark["status"] = "no_sets_recovered"
            continue
        inside = [s for s in sets if s["play_start"] - 1e-6 <= mark["timestamp"] <= s["play_end"] + 1e-6]
        target = inside[0] if inside else min(
            sets, key=lambda s: min(abs(mark["timestamp"] - s["play_start"]),
                                    abs(mark["timestamp"] - s["play_end"])))
        distance = min(abs(mark["timestamp"] - target["play_start"]),
                       abs(mark["timestamp"] - target["play_end"]))
        if not inside and distance > _ANCHOR_SEARCH_SECONDS:
            mark["status"] = "unanchored"
            continue
        mark.update(status="anchored" if inside else "nearest_set",
                    set_start=target["start"], set_end=target["end"],
                    offset_seconds=round(distance, 3),
                    # A mark outside the play is late or early; inside it, the operator simply marked
                    # during the match, which is normal and says nothing about where the set ended.
                    position="inside" if inside else ("late" if mark["timestamp"] > target["play_end"] else "early"))
        if target is not None and mark not in target["marks_inside"]:
            target["marks_inside"].append(mark)
            target["mark_offsets"].append(mark["offset_seconds"])
