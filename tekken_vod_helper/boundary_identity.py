"""How much to believe a recovered *name*, and what nothing is allowed to do with it.

Two different questions kept apart, because a recording answers them differently.

* **Did the machine read the broadcast graphic?** (`ocr_read_confidence`) A crop that named both sides at a
  confidence floor and 40 rows that agreed is a good read. A close-up that drew no nameplate is a bad one.
* **Is that the right person?** (`attribution_confidence`) A perfectly legible nameplate can name the wrong
  human: the header advertises the *next* pairing mid-round, a set changes ends so the same person moves from
  the left seat to the right, and a broadcast renames a seat without changing who sits in it.

Collapsing them is how "we read it clearly" becomes "we know who won". Each axis is scored from its own
factors and the published score is the **weaker** of the two -- a legible read on an unestablished seat is not
a confident attribution, whatever the OCR looked like.

Everything here is *heuristic confidence*, not a calibrated probability. The weights below are a documented
ranking of evidence, not frequencies measured against a ground truth this recording does not have: there is no
verified bracket (``bracket_results_verified: false``) and no operator orientation pass, so nothing on disk
would let anyone say "a score of 70 has been right 70% of the time". A score sorts which names to look at
first, and it travels with the factors that produced it so a person can disagree.

Six rules the numbers are built to obey:

1. **Missing evidence never raises a score** -- and never lowers one. Absence is not agreement, so it lands in
   ``unavailable_evidence`` named with what would supply it; but charging every game nobody cropped for that
   would measure the size of the budget instead of the quality of the name. A read that was taken and refused
   *is* a penalty, and that is the difference.
2. **One source is one witness, counted by origin and not by agreement.** The band crops, the per-frame
   nameplate rows and the character columns are reads of the same broadcast overlay by the same classifier.
   They earn a small credit marked ``independent: False`` and the attribution axis stays capped below the top
   tier (:data:`MAX_SINGLE_WITNESS_SCORE`) until something from outside that source -- an operator answer, a
   verified result sheet -- says the same thing. Character persistence in particular is a continuity
   hypothesis, not proof the same human held a seat: two players can main the same fighter and one player can
   change fighters.
3. **A conflict is not a low score.** Games that repeat each other's rounds are quarantined from the tally
   rather than priced down, and a contradicted identity withdraws *decisiveness* from both estimate routes. A
   name published on contradictory credits arrives labelled a candidate. Nothing is hidden -- the raw figures
   stay in the record beside the reason they were not used.
4. **Every applicable target is tested separately.** A span whose stage was never read admits both first to
   two and first to three, so three wins clear everything the format admits and two do not; and where two
   names reach the same target the credits contradict each other under that reading, which no amount of
   arithmetic resolves by taking the bigger number.
5. **An observed swap is not an error; an ambiguous orientation is.** Where the footage says the players
   changed seats, that is evidence and it earns credit, while an unanswered seat question costs. Neither one
   remaps anything here: each game's credit already comes from that game's own reading, which is exactly why a
   side exchange moves the *side arithmetic* and leaves the per-game person credits standing.
6. **Timing separates a repaint from a wrong name; a majority cannot.** The header advertising the next
   pairing and a name that is simply wrong both look like two names on one side in aggregate, so the reads'
   order against the boundary is consumed here -- with near-variant spellings reported as spellings,
   unobserved spans reported as gaps, and nothing inferred where the strings do not decide it.

Nothing in this module decides a grouping, a target, a boundary or an authoritative winner. It reads the
verdicts the recovery layer already reached and reports how much the names inside them deserve to be trusted.
Estimates it publishes are inert by construction: they are consumed by nothing that computes.
"""
import difflib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .boundary_names import SAME_NAME_RATIO, names_close

# ------------------------------------------------------------------------------------------ the numbers
# The only place any weight, bound or tier appears. Heuristic confidence on a 0-100 scale, NOT a calibrated
# probability: these are rankings of evidence, and no ground truth exists on this recording to fit them to.
SCORE_FLOOR = 0
SCORE_CEILING = 100

#: Both axes start at nothing. A base above zero would be the same lie as counting missing evidence as
#: reassuring: it would mean every game inherits confidence for free before anyone read a pixel.
SCORE_BASE_OCR = 0
SCORE_BASE_ATTRIBUTION = 0

#: How close two readings must be to count as one name is NOT decided here. `boundary_names` already declares
#: that tolerance for the whole pipeline (`SAME_NAME_RATIO` plus a containment rule), and a second,
#: separately-fitted threshold would be a second opinion about identity rather than a second reading of the
#: same evidence. See :func:`spelling_evidence` for what each relation licenses.

#: Nameplate legibility at or above this share earns the extra credit; between here and the dominance floor it
#: earns the base credit. Both are read off the same `NAME_DOMINANT_SHARE` discipline the recovery layer uses,
#: so a factor cannot disagree with the rule that admitted the read in the first place.
STRONG_SHARE = 0.95

#: While every identity claim comes from the one broadcast graphic, the attribution axis stops here -- below
#: the "high" floor, so no amount of a nameplate agreeing with itself reaches the top tier. Lifted only by a
#: witness whose origin is in :data:`INDEPENDENT_WITNESS_ORIGINS`, of which this recording has none.
MAX_SINGLE_WITNESS_SCORE = 65

#: Tiers, highest first. Deliberately coarse -- the difference between 71 and 78 is not a fact about this
#: recording, and a finer scale would invite reading precision into it.
TIERS = (("high", 70), ("medium", 40), ("low", 0))

#: Below this the row is flagged for a person. It is the top tier's bound on purpose: anything short of
#: "high" is something a reviewer should look at, and a heuristic that flags nothing is decoration.
NEEDS_REVIEW_BELOW = 70

#: How far the set-level factors may move the score away from its games. Capped because a candidate-wide fact
#: is a correction to the reading of its games, not a second tally of them: uncapped, one withheld credit
#: drove every imperfect set to the floor and the ranking stopped meaning anything.
SET_ADJUSTMENT_LIMIT = 25

#: Did we read the graphic?
OCR_WEIGHTS = {
    "band_reading_usable": 40,
    "band_confidence_floor": 10,
    "overlay_names_both_sides": 30,
    "overlay_share_very_high": 10,
    "overlay_rows_5_or_more": 10,
    "band_readings_disagree": -30,
    "band_reading_unusable": -40,
    "overlay_not_dominant": -20,
    "no_named_rows": -15,
    # Reported, never scored: a fact worth seeing that settles nothing by itself.
    "near_variant_readings": 0,
    # Set level, charged per game the graphic never named at all.
    "games_the_graphic_never_named": -8,
}

#: Is that the right person?
ATTRIBUTION_WEIGHTS = {
    "credited_by_band": 45,
    "named_by_band_not_credited": 35,
    "credited_by_overlay_hypothesis": 25,
    "seat_held_by_footage": 20,
    "seat_exchanged_by_footage": 15,
    "name_change_ordered_against_boundary": 10,
    "graphic_reads_agree": 10,
    "carried_in_matches_credited_name": 5,
    "prior_pairing_carried_into_game": 5,
    "previous_match_pairing_observed": 0,
    # Reported, never scored: a witness changes the cap, not the arithmetic under it.
    "independent_witness": 0,
    "name_change_explained_by_repaint": -5,
    "unobserved_nameplate_span": -10,
    "game_not_counted": -30,
    "duplicate_or_replay_evidence": -30,
    "renamed_seat_unresolved": -30,
    "contested_mapping": -30,
    "nameplate_alternating_within_read": -25,
    "nameplate_changed_within_game": -20,
    "membership_disputed": -20,
    "competing_names_for_one_seat": -35,
    "ambiguous_orientation": -35,
    "no_identity_read_at_all": -40,
    # Set level. Deliberately few: what a candidate got wrong about a game is already inside that game's
    # score, and restating it here is how every imperfect set ends up pinned at zero with nothing left to
    # rank them by. These describe the candidate as a whole.
    "band_crop_readings": 15,
    "seat_continuity_observed": 10,
    "credits_are_incomplete": -12,
    "membership_assumed": -10,
    "games_not_counted": -10,
}

#: Which origins count as evidence *independent of the broadcast graphic*. Everything the classifier read off
#: the overlay is one instrument however many fields it filled in, so only an answer from outside the
#: recording's own reads can lift the cap.
INDEPENDENT_WITNESS_ORIGINS = ("operator_answer", "verified_result_sheet", "external_bracket_confirmed")

#: Everything the recovery layer can see that is *not* independent evidence: three routes to the one
#: broadcast overlay, plus the verdicts and link records derived from them. Declared so a test can insist no
#: factor claims an origin that was never granted.
GRAPHIC_ORIGINS = ("header_band_saved", "overlay_nameplate_rows", "character_columns",
                   "header_band_saved+overlay_nameplate_rows")
DERIVED_ORIGINS = ("recovery_verdict", "link_record")

#: The evidence each factor came from. Origin travels with every factor because that is what lets a reader see
#: that a crop, a row and a fighter column are three readings of one thing -- and what stops "several factors
#: agreed" from reading as "several witnesses agreed".
EVIDENCE_ORIGIN = {
    "band_reading_usable": "header_band_saved",
    "band_confidence_floor": "header_band_saved",
    "band_readings_disagree": "header_band_saved",
    "credited_by_band": "header_band_saved",
    "named_by_band_not_credited": "header_band_saved",
    "band_crop_readings": "header_band_saved",
    "graphic_reads_agree": "header_band_saved+overlay_nameplate_rows",
    "overlay_names_both_sides": "overlay_nameplate_rows",
    "overlay_share_very_high": "overlay_nameplate_rows",
    "overlay_rows_5_or_more": "overlay_nameplate_rows",
    "overlay_not_dominant": "overlay_nameplate_rows",
    "no_named_rows": "overlay_nameplate_rows",
    "credited_by_overlay_hypothesis": "overlay_nameplate_rows",
    "name_change_ordered_against_boundary": "overlay_nameplate_rows",
    "name_change_explained_by_repaint": "overlay_nameplate_rows",
    "nameplate_changed_within_game": "overlay_nameplate_rows",
    "nameplate_alternating_within_read": "overlay_nameplate_rows",
    "carried_in_matches_credited_name": "overlay_nameplate_rows",
    "prior_pairing_carried_into_game": "overlay_nameplate_rows",
    "previous_match_pairing_observed": "overlay_nameplate_rows",
    "unobserved_nameplate_span": "overlay_nameplate_rows",
    "near_variant_readings": "overlay_nameplate_rows",
    "alias_uncertain_readings": "overlay_nameplate_rows",
    "games_the_graphic_never_named": "overlay_nameplate_rows",
    # Character continuity is inferred from the same classifier columns the audit below refuses to call side
    # evidence, so it is recorded as that origin and never as a witness.
    "seat_held_by_footage": "character_columns",
    "seat_exchanged_by_footage": "character_columns",
    "seat_continuity_observed": "character_columns",
    "ambiguous_orientation": "link_record",
    "no_identity_read_at_all": "recovery_verdict",
    "competing_names_for_one_seat": "recovery_verdict",
    "renamed_seat_unresolved": "recovery_verdict",
    "contested_mapping": "recovery_verdict",
    "credits_are_incomplete": "recovery_verdict",
    "duplicate_or_replay_evidence": "recovery_verdict",
    "game_not_counted": "recovery_verdict",
    "games_not_counted": "recovery_verdict",
    "membership_disputed": "recovery_verdict",
    "membership_assumed": "recovery_verdict",
}

#: Seat-continuity values the recovery layer actually emits (`boundary_games._seat_continuity`): held,
#: exchanged, changed, unknown. The first version of this layer accepted "crossed", a word the pipeline has
#: never produced, so every real side exchange in the recording was scored as though the seat question had not
#: been asked. `changed` establishes nothing about the person -- a re-pick and a new match look the same.
SEAT_STATE_MEANING = {
    "held": ("seat_held_by_footage",
             "the fighters held their sides across this gap, so wins on one seat are one person's wins -- "
             "inferred from the character columns, which is continuity and not identity"),
    "exchanged": ("seat_exchanged_by_footage",
                  "the players changed seats here, so the side arithmetic of this candidate is meaningless "
                  "while the per-game credits survive: each was taken from that game's own reading, which is "
                  "why a swap moves the seats and not the people"),
    "changed": ("ambiguous_orientation",
                "the fighters on this seat differ across the gap, which a re-pick and a new match both "
                "explain, so nobody knows whether the person did"),
    "unknown": ("ambiguous_orientation",
                "nothing on screen says whether this seat kept its player, which is the ambiguous case that "
                "must not be resolved by arithmetic"),
}

#: Membership values that may not carry an estimate at all: a candidate nobody has established is one set
#: (``contested``) and a single game at the edge of what was watched (``single``) cannot name a winner.
HARD_MEMBERSHIP_VETO = ("contested", "single")

#: Membership values that leave an estimate *conditional*: nothing contradicted these games being one set and
#: nothing argued it either (``assumed``), or the question was never answered (``unset``). The recovery layer
#: always names a membership, so a missing one is treated as the weaker case and not as a free pass.
SOFT_MEMBERSHIP_VETO = ("assumed", "unset")

#: Blocker kinds published by `boundary_games._attributed_wins`. The distinction is the whole policy: a
#: *conflict* means the identity is contradicted, so no route through this candidate may call a tally
#: decisive; a *coverage* gap means the credited tally is short, which leaves the nameplate route free to
#: speak for the games it did read.
CONFLICT_BLOCKERS = ("mapping_contested", "renamed_seat")
COVERAGE_BLOCKERS = ("win_uncovered", "cover_unusable")

#: Estimate outcomes. `decisive` is only ever a claim about arithmetic against the admitted targets; the word
#: that prices the name underneath it is the tier, and the two travel together or not at all.
ESTIMATE_STATUSES = ("decisive", "conditional", "ambiguous", "blocked", "none")

#: Evidence this layer would use if it had it, and does not. `axis` is the score it would move; the wording is
#: what a reviewer reads, so it names the act and not the abstraction.
UNAVAILABLE_EVIDENCE = (
    {"evidence": "operator orientation answer", "axis": "attribution_confidence",
     "would_resolve": "which screen side is which player, so a seat's wins belong to a person"},
    {"evidence": "verified bracket or event sheet", "axis": "attribution_confidence",
     "would_resolve": "the roster and the round, so a nameplate tag can be matched to a human"},
    {"evidence": "character-to-side binding", "axis": "attribution_confidence",
     "would_resolve": "which fighter sits on which side, the second reading that would let a seat be "
                      "followed across a rename"},
    {"evidence": "game duplicate or replay marking", "axis": "attribution_confidence",
     "would_resolve": "whether two games on tape are the same game played twice, which would change how many "
                      "wins may be counted"},
    {"evidence": "per-read OCR confidence from the frame rows", "axis": "ocr_read_confidence",
     "would_resolve": "how legible the nameplate was on each row, not only how often it agreed with itself"},
)


def tier_for(score):
    # type: (int) -> str
    for name, floor in TIERS:
        if score >= floor:
            return name
    return TIERS[-1][0]


def _clamp(value):
    # type: (float) -> int
    return int(max(SCORE_FLOOR, min(SCORE_CEILING, round(value))))


def _origin(factor, axis):
    # type: (str, str) -> str
    return EVIDENCE_ORIGIN.get(factor) or ("overlay_nameplate_rows" if axis == "ocr"
                                           else "recovery_verdict")


def _side(name, weight, axis, why, **provenance):
    # type: (str, int, str, str, **Any) -> Dict[str, Any]
    """One factor: its weight, the axis it moved, the claim it supports and where the evidence came from."""
    origin = provenance.pop("origin", None) or _origin(name, axis)
    row = {"factor": name, "weight": weight, "axis": axis, "why": why, "origin": origin,
           "independent": origin in INDEPENDENT_WITNESS_ORIGINS}
    row.update({key: value for key, value in provenance.items() if value is not None})
    return row


def _share_value(share):
    # type: (Any) -> Optional[float]
    """The lowest per-side share, which is the side a reader should worry about first."""
    if not isinstance(share, dict):
        return None
    values = [float(v) for v in share.values() if isinstance(v, (int, float))]
    return min(values) if values else None


def _normalised(value):
    # type: (Any) -> str
    """A reading with case and team tag removed: `RebiX` and `PWDDA | Rebix` both become "rebix"."""
    return str(value or "").split("|")[-1].strip().lower()


def _agree(left, right):
    # type: (Any, Any) -> bool
    """Do these two readings name the same player *as far as normalisation can tell*?

    Case and team tag only: those are one frame of one graphic written two ways, and counting them as a
    change would invent the ambiguity this module exists to price. How close two genuinely different
    spellings must be before this project calls them one name is `boundary_names`' decision, not a second
    opinion invented here.
    """
    return bool(left) and bool(right) and _normalised(left) == _normalised(right)


def _similarity(left, right):
    # type: (Any, Any) -> float
    """How much two nameplate spellings look alike, 0 to 1, once case, spacing and team tags are gone.

    Reported as evidence about the *strings*. It decides nothing on its own; see
    :func:`spelling_evidence` for what a given resemblance does and does not license.
    """
    a, b = str(left or ""), str(right or "")
    for value in ("|", " ", "-", "_"):
        a, b = a.replace(value, ""), b.replace(value, "")
    return round(difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio(), 3)


def spelling_evidence(left, right, aliases=()):
    # type: (Any, Any, Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """What two spellings do and do not establish, quoting the rule that decided it.

    Four relations, and only one of them is a claim about a person:

    * ``exact_normalised`` -- one reading written two ways (case, team tag). Certain, and about the text.
    * ``bound_participant_identity`` -- an operator answer or a roster binds the two tags to one human. The
      only relation here that says *one player*, and it carries its source so a reader can go and look.
      A corrected nameplate reading is **not** this: it says two strings were the same displayed text at one
      instant, which is what :func:`display_text_observations` reports separately.
    * ``alias_uncertain`` -- inside `boundary_names`' declared same-name tolerance, so plausibly one player
      misread. It settles nothing either way: not a second pairing, and not one pairing.
    * ``outside_tolerance`` -- further apart than this project allows for one name, which is the same
      judgement the grouping layer makes when a pairing change raises a mapping question. It licenses
      treating the two as different *readings*; it is still not proof that two humans exist.

    Reusing that rule instead of inventing one is the point: no threshold fitted to this recording's roster
    would be evidence of identity, and a suspicion must never be merged into a canonical name. The raw
    spellings always travel in the record, and every relation short of certainty leaves behind an entry
    naming the observation that would settle it.
    """
    similarity = _similarity(left, right)
    pair = {str(left or "").casefold(), str(right or "").casefold()}
    for alias in aliases or ():
        declared = {str(alias.get("a") or "").casefold(), str(alias.get("b") or "").casefold()}
        if len(declared) != 2 or declared != pair:
            continue
        # An alias counts as evidence only if it says what the pixels showed. The check lives here rather
        # than in the loader so a caller cannot bypass it by passing a dict it built itself -- which is the
        # difference between "someone looked at frame 2601" and "these strings look alike".
        if not alias.get("bound_participant_identity") or alias.get("reviewed_by") != "operator":
            # A reading of a frame, however careful, is evidence about text. Passing it here must not buy a
            # person: the same tag can be worn by a different human at another instant.
            continue
        return {"left": str(left or ""), "right": str(right or ""),
                "relation": "bound_participant_identity", "similarity": similarity,
                "identity_established": True, "display_text_equivalent": True,
                "rule": "one participant, bound by an operator answer or roster", "evidence": dict(alias)}
    if _normalised(left) == _normalised(right):
        return {"left": str(left or ""), "right": str(right or ""), "relation": "exact_normalised",
                "similarity": similarity, "identity_established": True,
                "rule": "identical after case and team-tag normalisation -- a fact about the text, not "
                        "about a player"}
    if names_close(str(left or ""), str(right or "")):
        return {"left": str(left or ""), "right": str(right or ""), "relation": "alias_uncertain",
                "similarity": similarity, "identity_established": False,
                "rule": "within boundary_names' same-name tolerance (ratio %.2f, or a containment of it), "
                        "which is this project's rule for *comparing* names and is not evidence that one "
                        "person exists" % SAME_NAME_RATIO}
    return {"left": str(left or ""), "right": str(right or ""), "relation": "outside_tolerance",
            "similarity": similarity, "identity_established": False,
            "rule": "further apart than boundary_names' same-name tolerance (ratio %.2f, no containment): "
                    "different readings, which is not the same as proving different players" % SAME_NAME_RATIO}


#: What each ``role`` on a saved reading means, in the words a reviewer reads. The distinction is the point:
#: an applied gameplay correction, the same correction repeating as the next game's lookback, and a reading
#: taken on a vs screen are one observation doing three different jobs, and a count that added them up would
#: be a count of sentences rather than of things looked at.
READING_ROLE_MEANINGS = {
    "within_game": "corrected text on a gameplay row inside this candidate's own span",
    "carry_in_context": "the same observation, quoted as the name carried in from before this span -- "
                        "lookback context, not a second corrected row",
    "off_play": "a valid reading of a menu or versus screen: exposed as off-play evidence and deliberately "
                "kept out of the gameplay trajectory, so it supports no winner",
    "not_applied_row_text_mismatch": "admitted by the loader but refused here: the row at this seat and "
                                     "instant carries a different string than the one observed, so neither "
                                     "string is used and the uncertainty stays",
}

#: Which role a mention earns when the record itself does not say, keyed by the list it was found in.
_ROLE_BY_FIELD = {"display_text_corrections_applied": "within_game",
                  "off_play_text_observations": "off_play",
                  "display_text_correction_mismatches": "not_applied_row_text_mismatch"}


def _reading_key(entry):
    # type: (Dict[str, Any]) -> str
    """Which observation this mention is of, independent of how many rows or candidates repeat it."""
    if entry.get("observation_id"):
        return str(entry["observation_id"])
    return "%s|%s|%s|%s" % (entry.get("side"), entry.get("at"), entry.get("row_text")
                            or entry.get("declared_text"), entry.get("observed_text"))


#: Strongest role first. One reading can do several jobs across the games of one candidate -- correct a row
#: inside this span, then be quoted as the next span's carry-in -- and the entry that represents it is one
#: observation whose *primary* role is the strongest thing it did, with the rest listed beside it.
_ROLE_STRENGTH = ("within_game", "off_play", "carry_in_context", "not_applied_row_text_mismatch")


def _strongest_role(roles):
    # type: (Sequence[str]) -> str
    for role in _ROLE_STRENGTH:
        if role in roles:
            return role
    return str(roles[0]) if roles else "unknown"


def _collapsed_readings(rows, field):
    # type: (Sequence[Dict[str, Any]], str) -> List[Dict[str, Any]]
    """One record per observation inside this candidate, with every mention and role it earned.

    Collapsing happens here rather than at the report level because the repetition is real and has to stay
    visible: a nameplate read at one instant is one observation, and the two candidates that mention it -- the
    one it sits inside and the next one, whose lookback reaches back to it -- are two references. Summing the
    references instead is what made a previous revision report two corrections from one file with two entries.
    """
    order, by_key = [], {}
    for row in rows or []:
        timing = row.get("nameplate_timing") or {}
        for side in ("p1", "p2"):
            block = (timing.get("sides") or {}).get(side) or {}
            for entry in block.get(field) or []:
                role = str(entry.get("role") or _ROLE_BY_FIELD.get(field, "within_game"))
                record = dict(entry, side=entry.get("side") or side, role=role,
                              reviewed_by=entry.get("reviewed_by") or "machine",
                              evidence_tier=entry.get("evidence_tier") or "machine_display_text",
                              accepted_by=None, bound_participant_identity=False)
                key = _reading_key(record)
                if key not in by_key:
                    by_key[key] = dict(record, observation_id=key, roles=[], reference_count=0,
                                       mentioned_in=[])
                    order.append(key)
                hit = by_key[key]
                hit["reference_count"] += 1
                if role not in hit["roles"]:
                    hit["roles"].append(role)
                hit["role"] = _strongest_role(hit["roles"])
                where = (row.get("start") if row.get("start") is not None else row.get("game"))
                if where is not None and where not in hit["mentioned_in"]:
                    hit["mentioned_in"].append(where)
    return [by_key[key] for key in order]


def display_text_observations(rows):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Every saved-frame reading of displayed text inside these game rows, with its scope attached.

    Consumers that might otherwise over-claim read this list instead of the corrected strings: each entry
    names the instant, the seat, the frame and its digest, who looked, what job it actually did (``role``)
    and how many rows repeat it (``reference_count``), and says in two fields that no identity was bound and
    nobody accepted it. Readings that were refused -- the row did not carry the string the observation claims
    to correct -- are *not* here; they are in :func:`display_text_correction_refusals`, because a refusal
    listed among the applied would be the exact over-claim this seam exists to prevent.
    """
    out = []
    for record in _collapsed_readings(rows, "display_text_corrections_applied") + \
            _collapsed_readings(rows, "off_play_text_observations"):
        out.append(dict(record,
                        identity_established=False,
                        covers=("this instant and this seat only: the same tag at another instant is "
                                "another observation or it stays uncertain"),
                        role_meaning=READING_ROLE_MEANINGS.get(record.get("role"),
                                                               "what this reference was, in one line")))
    return sorted(out, key=lambda row: (float(row.get("at") or 0.0), str(row.get("side")),
                                        str(row.get("role"))))


def display_text_correction_refusals(rows):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Admitted observations that named this seat and instant but not this string, so nothing was applied."""
    return sorted(_collapsed_readings(rows, "display_text_correction_mismatches"),
                  key=lambda row: (float(row.get("at") or 0.0), str(row.get("side"))))


def _spelling_relation(left, right, aliases=()):
    # type: (Any, Any, Sequence[Dict[str, Any]]) -> Tuple[str, float]
    """(relation, similarity) -- the two values most callers need. See :func:`spelling_evidence`."""
    evidence = spelling_evidence(left, right, aliases=aliases)
    return evidence["relation"], evidence["similarity"]


def aliases_from_saved_evidence(path):
    # type: (Any) -> List[Dict[str, Any]]
    """Load declared aliases from a file, keeping each one's provenance attached.

    This is the seam, not the decision: a row-resemblance guess and a crop that was actually looked at are
    different kinds of claim, so an entry that does not say it was settled by pixels is loaded and labelled
    as unresolved rather than discarded or trusted. Nothing in the normal pipeline calls this; the operator
    promotes a file here deliberately, and until then the report keeps saying the spellings are uncertain.
    """
    payload = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    rows = payload.get("aliases") if isinstance(payload, dict) else payload
    out = []
    for entry in rows or []:
        if not (entry.get("a") and entry.get("b")):
            continue
        declared = dict(entry)
        declared["settled_by_pixels"] = bool(entry.get("settled_by_pixels",
                                                       entry.get("text_seen") is not None
                                                       or any(read.get("text_seen_in_saved_frame")
                                                              for read in (entry.get("read_from") or []))))
        if not declared["settled_by_pixels"]:
            # An alias nobody verified with pixels is not evidence of one participant, so it must not close
            # the uncertainty -- it is recorded as a candidate reading and nothing more.
            continue
        out.append(declared)
    return out


def repaint_explanation(timing, side, name):
    # type: (Optional[Dict[str, Any]], Optional[str], Optional[str]) -> Dict[str, Any]
    """Does this game's ordered reading explain a contradicted name *on the side and player concerned*?

    Three things have to hold, and the first version of this function required none of them. The carried name
    and the credited name must be on the side whose credit is in question, and must name the player that
    credit names; the credited name must actually have been drawn later in the span, or the ordering shows two
    readings rather than a handover; and that side must not have gone back to a name it already carried,
    because a wobble is not a repaint.

    A pair still inside the alias tolerance is refused even when the rest holds. Two spellings that
    `boundary_names` would call one name cannot demonstrate that the header moved from one pairing to
    another, and spending that uncertainty as if it were a finding is the exact mistake this layer is here to
    stop making.
    """
    side = side if side in ("p1", "p2") else None
    if not timing or timing.get("coverage") != "observed" or side is None or not name:
        return {"supported": False, "because": "no ordered reading of this game's nameplate was supplied"}
    alternating = [entry for entry in (timing.get("readings_alternating") or [])
                   if entry.get("side") == side]
    if alternating:
        return {"supported": False, "side": side, "entry": alternating[0],
                "because": "the readings on %s went back to a name they had already carried (%s), which no "
                           "repaint explains" % (side, " -> ".join(str(v) for v in alternating[0]["sequence"]))}
    for entry in timing.get("carried_in_persists") or []:
        if entry.get("side") != side or not _agree(entry.get("credited"), name):
            continue
        if entry.get("relation") == "alias_uncertain" or not entry.get("credited_name_read_in_span"):
            continue
        if entry.get("drawn_during") != "gap":
            # Nothing was observed at the boundary itself, so there is no handover to describe.
            continue
        return {"supported": True, "side": side, "entry": entry,
                # Two things would look exactly like this: a header that had not repainted yet, and a game
                # boundary placed a few seconds late. Both are consistent with a gameplay-classified row
                # sitting in the gap, so the explanation softens the penalty and does not remove it.
                "because": "the side carried %s into the gap %.1f s before this game and drew %s from %.1f s "
                           "in, on the side whose credit is at issue -- which is what a header that had not "
                           "repainted yet looks like, and also what a boundary a few seconds late looks like, "
                           "so the contradiction is discounted and not excused"
                           % (entry.get("carried"), entry.get("carried_age_seconds") or 0.0,
                              entry.get("credited"), entry.get("seconds_into_game") or 0.0),
                "confounded_with": "a game boundary placed late by a few seconds"}
    return {"supported": False, "side": side, "name": name,
            "because": "no carried-in reading on %s names %s, so nothing here separates a late repaint from "
                       "a wrong name" % (side, name)}



# ------------------------------------------------------------------------- the ordered nameplate record
def nameplate_timing(pairing, game, aliases=()):
    # type: (Optional[Dict[str, Any]], Dict[str, Any], Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """When the graphic actually drew each name across one game, against that game's own boundaries.

    Aggregate agreement cannot answer "is this the player who won, or the pairing the header was advertising
    at the time?" -- a late repaint and a wrong name produce the same tally. The saved rows carry timestamps,
    so this reports the order: what was being drawn before the game opened, what changed inside it and when,
    and which stretches were never drawn at all.

    Four distinctions it exists to keep. An **arrival** (the first read inside the span, ``from`` null) is a
    reading but not a change, so it belongs to the trajectory and not to the change list. The **trajectory** --
    carried-in name, then every reading in order -- is what instability is measured on, because `A -> B -> A`
    is the graphic moving under the read however few "changes" the strict definition leaves. An
    **alias-suspect pair** (`derpingtoast` beside `derpingtonstoast`, inside this project's own name tolerance)
    is neither a second pairing nor a settled one. And a **span nobody read** is published as a gap, which is
    not the same as agreement.
    """
    sequence = (pairing or {}).get("name_sequence") or {}
    sides = sequence.get("sides") or {}
    if not sequence:
        return {"coverage": "absent", "because": "no nameplate record was supplied for this game",
                "sides": {}, "arrivals": [], "change_events": [], "unobserved_gaps": [],
                "names_per_side": {}, "readings_alternating": [], "carried_in_persists": [],
                "carried_in_matches": [], "alias_uncertain": [], "previous_match_pairing": [],
                "outside_tolerance_pairs": [],
                "would_need": "the classification rows for this span (they exist for every game in BB9)"}
    span = sequence.get("span") or [game.get("start"), game.get("end")]
    out = {"coverage": "observed", "span": span, "lookback_from": sequence.get("lookback_from"),
           "play_rows": sequence.get("play_rows"), "off_play_rows": sequence.get("off_play_rows"),
           "unobserved_gaps": sequence.get("unobserved_gaps") or [], "sides": {}, "arrivals": [],
           "change_events": [], "names_per_side": {}, "readings_alternating": [],
           "carried_in_persists": [], "carried_in_matches": [], "alias_uncertain": [],
           "reading_relations": [], "outside_tolerance_pairs": [], "previous_match_pairing": []}
    for side in ("p1", "p2"):
        block = sides.get(side) or {}
        events = block.get("change_events") or []
        carried, credited = block.get("carried_in"), ((pairing.get("sides") or {}).get(side))
        changes, arrivals = [], []
        for event in events:
            stamped = dict(event, side=side, spelling_variant_only=_agree(event.get("from"),
                                                                           event.get("to")))
            # ``from`` is null on the first read inside a span: nothing had changed, something simply arrived.
            (arrivals if event.get("from") is None else changes).append(stamped)
        out["arrivals"].extend(arrivals)
        out["change_events"].extend(changes)
        out["sides"][side] = {"named_rows": block.get("named_rows"),
                              "first_named_at": block.get("first_named_at"),
                              "last_named_at": block.get("last_named_at"),
                              "changes_inside": len(changes), "arrivals": len(arrivals),
                              "spelling_flips_inside": sum(1 for event in changes
                                                           if event["spelling_variant_only"]),
                              "carried_in": carried, "credited_name": credited,
                              # Passed through from the reading source, because this is where a reader can see
                              # that a string in the trajectory is not the string the classifier wrote, and
                              # which frame the difference was settled against.
                              "display_text_corrections_applied": list(
                                  block.get("display_text_corrections_applied") or []),
                              # Both of these exist so an admitted observation cannot disappear between the
                              # tracker and the report: a reading taken on a menu, and a reading refused
                              # because the row it names carried a different string, are each a third
                              # disposition that the applied list would misreport as "used".
                              "off_play_text_observations": list(
                                  block.get("off_play_text_observations") or []),
                              "display_text_correction_mismatches": list(
                                  block.get("display_text_correction_mismatches") or []),
                              # An arrival is still a reading: the credited name being drawn at the instant the
                              # span opened counts, even though it is not a change.
                              "credited_name_read_in_span": any(_agree(event["to"], credited)
                                                                for event in events)}
        trajectory, marks = [], []
        if carried:
            trajectory.append(carried.get("name"))
        for event in events:
            value = event.get("to")
            if not value:
                continue
            if trajectory and _agree(trajectory[-1], value):
                continue
            trajectory.append(value)
        out["sides"][side]["trajectory"] = [str(v) for v in trajectory]
        out["sides"][side]["trajectory_changes"] = len(trajectory) - (1 if carried else 0)
        # Distinct readings on this side, with the relation between each adjacent pair named. This is where a
        # spelling wobble and a pairing change part company -- or stay together as unresolved, which is the
        # honest answer more often than the first version of this function admitted.
        pairs = []
        for earlier, later in zip(trajectory, trajectory[1:]):
            evidence = spelling_evidence(earlier, later, aliases=aliases)
            pairs.append({"from": evidence["left"], "to": evidence["right"],
                          "relation": evidence["relation"], "similarity": evidence["similarity"],
                          "identity_established": evidence["identity_established"],
                          "rule": evidence["rule"]})
        out["sides"][side]["reading_relations"] = pairs
        out["reading_relations"].extend(dict(pair, side=side) for pair in pairs)
        if carried and credited:
            # "The header was still showing the previous pairing" is only supported when this span went on to
            # draw the credited name. Two names being drawn says a boundary passed; only the order in which
            # the credited one appears says the earlier name was left behind rather than wrong.
            credited_reads = [event["at"] for event in events if _agree(event.get("to"), credited)]
            first_credited = min(credited_reads) if credited_reads else None
            measured = {"side": side, "carried": carried.get("name"), "credited": credited,
                        "carried_age_seconds": carried.get("age_seconds_before_span"),
                        "credited_name_read_in_span": first_credited is not None,
                        "first_read_of_credited_name_at": first_credited,
                        "seconds_into_game": (round(float(first_credited) - float(span[0]), 3)
                                              if first_credited is not None else None)}
            evidence = spelling_evidence(carried.get("name"), credited, aliases=aliases)
            measured = dict(measured, similarity=evidence["similarity"],
                            relation=evidence["relation"], rule=evidence["rule"],
                            drawn_during=carried.get("drawn_during"),
                            identity_established=evidence["identity_established"])
            if evidence["relation"] in ("exact_normalised", "bound_participant_identity"):
                # The same participant on both sides of the boundary: persistence, not a handover. A declared
                # alias belongs with the case-insensitive match because that is exactly what it asserts, and
                # routing it to the handover bucket would turn "one player, two spellings" into evidence that
                # the pairing changed.
                out["carried_in_matches"].append(dict(
                    measured, similarity=(1.0 if evidence["relation"] == "exact_normalised"
                                          else evidence["similarity"]),
                    alias_evidence=evidence.get("evidence"),
                    because=("the seat kept its player across the boundary -- the same reading either side"
                             if evidence["relation"] == "exact_normalised" else
                             "the two spellings are one participant by declared evidence, so this seat kept "
                             "its player across the boundary")))
            elif evidence["relation"] == "alias_uncertain":
                measured["because"] = ("the two spellings are inside this project's own same-name tolerance, "
                                       "which makes one player suspected and nothing settled: it is neither a "
                                       "pairing change nor a repaint")
                out["alias_uncertain"].append(measured)
            elif evidence["relation"] == "outside_tolerance" and first_credited is None:
                measured["because"] = ("a different reading was drawn before this game and the credited name "
                                       "was never drawn inside it, so the ordering shows two readings and not "
                                       "two pairings")
                out["alias_uncertain"].append(dict(measured, relation="credited_name_never_read"))
            elif carried.get("drawn_during") != "gap":
                # The old name was last drawn in the middle of the *previous match*, not in the pause before
                # this one. Then nothing was left on screen at the boundary: this is simply who played before,
                # and calling it a repaint would date the header's change to a moment nobody observed.
                measured["because"] = ("the earlier reading belongs to the previous match rather than to the "
                                       "gap before this game, so the handover at this boundary is unobserved")
                out["previous_match_pairing"].append(measured)
            else:
                out["carried_in_persists"].append(measured)
            if evidence["relation"] in ("bound_participant_identity", "outside_tolerance"):
                out["outside_tolerance_pairs"].append(
                    {"side": side, "a": evidence["left"], "b": evidence["right"],
                     "relation": evidence["relation"], "similarity": evidence["similarity"],
                     "identity_established": evidence["identity_established"]})
        # Instability is read off the trajectory, so the arrival and the carried-in name count: dropping the
        # first reading made A -> B -> A look like a settled handoff, which is the difference between a
        # repaint and the graphic moving under the read.
        for index, value in enumerate(trajectory):
            back = [other for other in trajectory[index + 2:] if _agree(value, other)]
            if back:
                out["readings_alternating"].append({
                    "side": side, "sequence": [str(v) for v in trajectory],
                    "because": "the readings on this side went back to a name they had already carried, so the "
                               "graphic moved while it was being read and no repaint explains that"})
                break
        out["names_per_side"][side] = sorted({str(v) for v in trajectory} | ({credited} if credited else set()))
    out["meaning"] = ("when each name was drawn, in order, against this span -- the only thing that separates "
                      "a header that repaints late from a name that is simply wrong")
    return out


def _timing_factors(timing, game):
    # type: (Dict[str, Any], Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]
    """Score what the ordered reads said. A change explained by timing is not a contradiction."""
    positive, negative = [], []
    if timing.get("coverage") != "observed":
        return positive, negative
    meaningful = [event for event in (timing.get("change_events") or [])
                  if not event.get("spelling_variant_only")]
    if meaningful and not timing.get("readings_alternating"):
        positive.append(_side("name_change_ordered_against_boundary",
                              ATTRIBUTION_WEIGHTS["name_change_ordered_against_boundary"], "attribution",
                              "the names changed %d time(s) in a recorded order (%s), so a repaint and a "
                              "wrong name can be told apart here"
                              % (len(meaningful), "; ".join("%s at %.1f s"
                                                            % (event.get("to"),
                                                               event.get("offset_from_span_start", 0.0))
                                                            for event in meaningful[:3])),
                              at=[event.get("at") for event in meaningful], events=meaningful[:6],
                              at_span=timing.get("span")))
    flips = sum(1 for event in (timing.get("change_events") or []) if event.get("spelling_variant_only"))
    if flips:
        positive.append(_side("near_variant_readings", 0, "ocr",
                              "%d change(s) inside this span were one name spelled differently, which is a "
                              "read artifact and not a pairing moving" % flips,
                              at=game.get("start"), count=flips, settles_nothing=True))
    for entry in list(timing.get("near_variant_reads") or []) + list(timing.get("unresolved_spellings") or []):
        positive.append(_side("near_variant_readings", 0, "ocr", entry.get("because") or "",
                              at=entry.get("first_read_of_credited_name_at"),
                              similarity=entry.get("similarity"), settles_nothing=True))
    for entry in timing.get("readings_alternating") or []:
        negative.append(_side("nameplate_alternating_within_read",
                              ATTRIBUTION_WEIGHTS["nameplate_alternating_within_read"], "attribution",
                              "on %s the rows went back to a name they had already carried (%s), which no "
                              "repaint explains: the graphic itself was unstable while it was being read"
                              % (entry["side"], " -> ".join(str(value) for value in entry["sequence"])),
                              at=game.get("start"), sequence=entry["sequence"]))
    for entry in timing.get("carried_in_matches") or []:
        positive.append(_side("carried_in_matches_credited_name",
                              ATTRIBUTION_WEIGHTS["carried_in_matches_credited_name"], "attribution",
                              "the name credited on %s was already being drawn %.1f s before this game opened: "
                              "one pairing across the boundary, which is what a set in progress looks like"
                              % (entry["side"], entry.get("carried_age_seconds") or 0.0),
                              at=entry.get("first_read_of_credited_name_at"),
                              carried_age_seconds=entry.get("carried_age_seconds")))
    for entry in timing.get("carried_in_persists") or []:
        positive.append(_side("prior_pairing_carried_into_game",
                              ATTRIBUTION_WEIGHTS["prior_pairing_carried_into_game"], "attribution",
                              "the side opened on %s, drawn %.1f s before the game, and %s was not drawn "
                              "until %.1f s in: the previous pairing was still on screen as this one began, "
                              "so a competing read at the start of this game is the header moving rather than "
                              "necessarily a wrong name -- though a boundary placed a few seconds late would "
                              "look the same, and for those first %s s the two cannot be separated"
                              % (entry["carried"], entry.get("carried_age_seconds") or 0.0,
                                 entry["credited"], entry.get("seconds_into_game") or 0.0,
                                 entry.get("seconds_into_game")),
                              at=entry.get("first_read_of_credited_name_at"),
                              similarity=entry.get("similarity"),
                              carried_age_seconds=entry.get("carried_age_seconds"),
                              seconds_into_game=entry.get("seconds_into_game")))
    for entry in timing.get("previous_match_pairing") or []:
        positive.append(_side("previous_match_pairing_observed", 0, "attribution",
                              "the previous reading on %s was last drawn inside the match before this one "
                              "(%.1f s before it opened), so the boundary itself is unobserved and nothing is "
                              "explained either way" % (entry["side"], entry.get("carried_age_seconds") or 0),
                              at=entry.get("first_read_of_credited_name_at"),
                              drawn_during=entry.get("drawn_during"), settles_nothing=True))
    for entry in timing.get("carried_in_persists") or []:
        positive.append(_side("alias_uncertain_readings", 0, "attribution",
                              "the handover on %s is observed only in the gap between matches, so a late "
                              "boundary and a slow header are not separable here" % entry.get("side"),
                              at=entry.get("first_read_of_credited_name_at"),
                              relation="boundary_or_repaint_ambiguous", settles_nothing=True))
    if timing.get("unobserved_gaps"):
        negative.append(_side("unobserved_nameplate_span",
                              ATTRIBUTION_WEIGHTS["unobserved_nameplate_span"]
                              * len(timing["unobserved_gaps"]), "attribution",
                              "%d span(s) inside this game drew no name at all, so continuity across them is "
                              "unobserved rather than agreed" % len(timing["unobserved_gaps"]),
                              at=game.get("start"), gaps=timing["unobserved_gaps"],
                              count=len(timing["unobserved_gaps"])))
    return positive, negative


# --------------------------------------------------------------------------------------------- per game
def game_report(game, cover=None, pairing=None, hypothesis=None, credited=False, seat=None,
                duplicate=False, timing=None, independent_witness=None, aliases=()):
    # type: (Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool, Optional[Dict[str, Any]], bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Score one game's identity evidence, factor by factor, with the instant each factor came from.

    ``cover`` is the merged band reading inside this game's span, ``pairing`` the frame-row majority with its
    name sequence, ``hypothesis`` the nameplate record, ``credited`` whether the recovery layer used this
    game's win as a per-player credit, and ``seat`` the seat record at this game's opening gap in the
    recovery layer's own vocabulary (``{"state": "held"|"exchanged"|"changed"|"unknown", "at": ...}``).

    ``independent_witness`` is the only thing that lifts the single-witness cap: evidence whose origin is not a
    read of the broadcast overlay. Character persistence is not one, and neither is a second route to the same
    nameplate.
    """
    cover, pairing = dict(cover or {}), dict(pairing or {})
    hypothesis, seat = dict(hypothesis or {}), dict(seat or {})
    if timing is None:
        timing = nameplate_timing(pairing, game, aliases=aliases)
    positive, negative, absent = [], [], []
    ocr = float(SCORE_BASE_OCR)
    attribution = float(SCORE_BASE_ATTRIBUTION)

    if cover.get("usable"):
        ocr += OCR_WEIGHTS["band_reading_usable"]
        positive.append(_side("band_reading_usable", OCR_WEIGHTS["band_reading_usable"], "ocr",
                              "a saved band reading inside this game's span names both sides at its own "
                              "confidence floor", at=cover.get("at"),
                              crop_sha256=cover.get("image_sha256"), readings=cover.get("readings_inside")))
        if cover.get("confidence"):
            ocr += OCR_WEIGHTS["band_confidence_floor"]
            positive.append(_side("band_confidence_floor", OCR_WEIGHTS["band_confidence_floor"], "ocr",
                                  "the answer carried a per-side confidence, which is the floor the band route "
                                  "itself applies before naming anyone", at=cover.get("at"),
                                  confidence=cover.get("confidence")))
    elif cover:
        if cover.get("arrangements", 0) > 1:
            ocr += OCR_WEIGHTS["band_readings_disagree"]
            negative.append(_side("band_readings_disagree", OCR_WEIGHTS["band_readings_disagree"], "ocr",
                                  "%d readings inside this span name different pairings, so the graphic was "
                                  "moving while it was being read" % cover.get("arrangements"),
                                  at=cover.get("at"),
                                  instants=[row.get("at") for row in
                                            cover.get("conflicting_readings") or []],
                                  why_unusable=cover.get("why_unusable")))
        else:
            ocr += OCR_WEIGHTS["band_reading_unusable"]
            negative.append(_side("band_reading_unusable", OCR_WEIGHTS["band_reading_unusable"], "ocr",
                                  "a reading falls inside this game but the answer refuses to carry an "
                                  "identity", why_unusable=cover.get("why_unusable"),
                                  at=(cover.get("unreadable_readings") or [{}])[0].get("at")))
    else:
        absent.append({"evidence": "band crop reading for this game", "axis": "ocr_read_confidence",
                       "status": "absent", "because": "no saved band reading falls inside this game's span",
                       "span": [game.get("start"), game.get("end")]})

    share, rows = _share_value(pairing.get("share")), pairing.get("named_rows")
    if pairing.get("dominant"):
        ocr += OCR_WEIGHTS["overlay_names_both_sides"]
        positive.append(_side("overlay_names_both_sides", OCR_WEIGHTS["overlay_names_both_sides"], "ocr",
                              "this game's own rows named both sides on a dominant reading",
                              share=pairing.get("share"), named_rows=rows,
                              span=[game.get("start"), game.get("end")]))
        if share is not None and share >= STRONG_SHARE:
            ocr += OCR_WEIGHTS["overlay_share_very_high"]
            positive.append(_side("overlay_share_very_high", OCR_WEIGHTS["overlay_share_very_high"], "ocr",
                                  "the weakest side agreed with itself on %s%% of named rows"
                                  % round(share * 100, 1), share=share))
        if isinstance(rows, int) and rows >= 5:
            ocr += OCR_WEIGHTS["overlay_rows_5_or_more"]
            positive.append(_side("overlay_rows_5_or_more", OCR_WEIGHTS["overlay_rows_5_or_more"], "ocr",
                                  "%d rows inside the span carried a name, so the read is not one frame"
                                  % rows, named_rows=rows))
    else:
        ocr += OCR_WEIGHTS["overlay_not_dominant"]
        negative.append(_side("overlay_not_dominant", OCR_WEIGHTS["overlay_not_dominant"], "ocr",
                              "the rows inside this game do not dominantly name both sides (share %s over %s "
                              "rows)" % (pairing.get("share"), rows), at=game.get("start")))
        if not rows:
            ocr += OCR_WEIGHTS["no_named_rows"]
            negative.append(_side("no_named_rows", OCR_WEIGHTS["no_named_rows"], "ocr",
                                  "no row inside this game carried a name at all", at=game.get("start")))

    # ---- attribution axis
    basis = hypothesis.get("basis")
    if credited and basis == "header_band_saved":
        attribution += ATTRIBUTION_WEIGHTS["credited_by_band"]
        positive.append(_side("credited_by_band", ATTRIBUTION_WEIGHTS["credited_by_band"], "attribution",
                              "the recovery layer credited this game's win from a usable band reading",
                              at=cover.get("at"), independent=False))
    elif hypothesis.get("admitted") and basis in ("header_band_saved", "overlay_nameplate_hypothesis"):
        name = ("named_by_band_not_credited" if basis == "header_band_saved"
                else "credited_by_overlay_hypothesis")
        attribution += ATTRIBUTION_WEIGHTS[name]
        positive.append(_side(name, ATTRIBUTION_WEIGHTS[name], "attribution", hypothesis.get("why") or "",
                              at=hypothesis.get("at"), share=hypothesis.get("share"),
                              named_rows=hypothesis.get("named_rows"), independent=False))
    else:
        attribution += ATTRIBUTION_WEIGHTS["no_identity_read_at_all"]
        negative.append(_side("no_identity_read_at_all", ATTRIBUTION_WEIGHTS["no_identity_read_at_all"],
                              "attribution", "neither route named both sides of this game, so a win here "
                                              "belongs to a screen side and not to a person",
                              at=game.get("start")))

    if hypothesis.get("band_agrees") is True:
        attribution += ATTRIBUTION_WEIGHTS["graphic_reads_agree"]
        positive.append(_side("graphic_reads_agree", ATTRIBUTION_WEIGHTS["graphic_reads_agree"], "attribution",
                              "the crop and the rows read the same arrangement -- which is one graphic "
                              "agreeing with itself, not a second witness", at=hypothesis.get("at"),
                              independent=False, crop_sha256=hypothesis.get("band_crop_sha256")))
    if hypothesis.get("contradicts"):
        # Not `elif`: these are different evidence. A crop can match the majority of rows while a minority says
        # the name moved under it, and swallowing the second fact because the first was good news is how a
        # contradiction stops being visible.
        #
        # Whether the timing explains it is decided on the winner's own side and the player the credit names: a
        # tidy handoff on the other side says nothing about the credit this tally depends on, an alias wobble is
        # not a pairing change, and a name that came back is the graphic unstable under the read.
        winner_side = game.get("winner") if game.get("winner") in ("p1", "p2") else None
        explanation = repaint_explanation(timing, winner_side,
                                          (hypothesis.get("sides") or {}).get(winner_side))
        explained = bool(explanation.get("supported"))
        factor = "name_change_explained_by_repaint" if explained else "nameplate_changed_within_game"
        attribution += ATTRIBUTION_WEIGHTS[factor]
        negative.append(_side(factor, ATTRIBUTION_WEIGHTS[factor], "attribution",
                              (("the rows inside this game read the same graphic differently, and the order "
                                "settles it: %s") % explanation.get("because")) if explained else
                              ("the rows inside this game read the same graphic differently, so the name moved "
                               "while the game was being played (%s)" % explanation.get("because")),
                              at=hypothesis.get("at"), contradicts=hypothesis.get("contradicts"),
                              winner_side=winner_side, side=explanation.get("side"),
                              explanation=explanation.get("because"), entry=explanation.get("entry"),
                              alternating=bool(timing.get("readings_alternating"))))

    state = seat.get("state")
    if state in SEAT_STATE_MEANING:
        factor, why = SEAT_STATE_MEANING[state]
        attribution += ATTRIBUTION_WEIGHTS[factor]
        record = _side(factor, ATTRIBUTION_WEIGHTS[factor], "attribution", why, at=seat.get("at"),
                       scope="the gap before this game", evidence=state)
        (positive if ATTRIBUTION_WEIGHTS[factor] > 0 else negative).append(record)
        if state in ("changed", "unknown"):
            absent.append({"evidence": "seat orientation across this gap", "axis": "attribution_confidence",
                           "status": "absent", "because": "the fighters %s and nothing else settles who sat "
                                                          "there" % state, "at": seat.get("at")})
    else:
        absent.append({"evidence": "seat orientation for this game", "axis": "attribution_confidence",
                       "status": "absent", "because": "no mapping question was raised at this gap, so the "
                                                      "seat was never asked about"})

    timing_positive, timing_negative = _timing_factors(timing, game)
    positive.extend(timing_positive)
    negative.extend(timing_negative)

    if not game.get("counts_toward_set"):
        attribution += ATTRIBUTION_WEIGHTS["game_not_counted"]
        negative.append(_side("game_not_counted", ATTRIBUTION_WEIGHTS["game_not_counted"], "attribution",
                              "this game is not counted toward the set, so a win here credits nobody",
                              at=game.get("start"), basis=game.get("basis")))
    if duplicate:
        attribution += ATTRIBUTION_WEIGHTS["duplicate_or_replay_evidence"]
        negative.append(_side("duplicate_or_replay_evidence",
                              ATTRIBUTION_WEIGHTS["duplicate_or_replay_evidence"], "attribution",
                              "this game repeats another game's round ids, so the same win may be counted "
                              "twice -- and its credits are quarantined below, not merely priced down",
                              at=game.get("start"), round_ids=game.get("round_ids"), quarantined=True))

    origin = str((independent_witness or {}).get("origin") or "")
    independent = origin in INDEPENDENT_WITNESS_ORIGINS
    if independent:
        positive.append(_side("independent_witness", 0, "attribution",
                              "something that is not a read of this broadcast says the same thing: %s"
                              % ((independent_witness or {}).get("because") or origin),
                              at=(independent_witness or {}).get("at"), origin=origin,
                              scope=(independent_witness or {}).get("scope", "game")))
    witnesses = 1 + (1 if independent else 0)
    cap = SCORE_CEILING if witnesses > 1 else MAX_SINGLE_WITNESS_SCORE
    attribution_capped = attribution > cap
    attribution = min(attribution, cap)
    score = _clamp(min(ocr, attribution))
    return {"at": game.get("start"), "span": [game.get("start"), game.get("end")],
            "winner_side": game.get("winner"),
            "counts_toward_set": bool(game.get("counts_toward_set")),
            "credited": bool(credited), "duplicate": bool(duplicate),
            "name": ((hypothesis.get("sides") or {}).get(game.get("winner"))
                     if game.get("winner") in ("p1", "p2") else None),
            "sides": hypothesis.get("sides") or {}, "basis": basis,
            "admitted": bool(hypothesis.get("admitted")),
            "contradicted": bool(hypothesis.get("contradicts")),
            "ocr_read_confidence": _clamp(ocr),
            "attribution_confidence": _clamp(attribution),
            "attribution_cap": cap,
            "score": score, "tier": tier_for(score),
            "needs_review": score < NEEDS_REVIEW_BELOW,
            "single_witness_capped": attribution_capped,
            "positive_factors": positive, "negative_factors": negative,
            "unavailable_evidence": absent,
            "independent_witnesses": witnesses,
            "evidence_origins": sorted({row["origin"] for row in positive + negative}),
            "seat_state": state, "seat_at": seat.get("at"), "nameplate_timing": timing}


# -------------------------------------------------------------------------------------------- estimate
def winner_estimate(targets, credits, blockers=(), source=None, excluded=(), formats=None,
                    raw_credits=None, membership=None, duplicates=()):
    # type: (Sequence[int], Dict[str, int], Sequence[str], Optional[str], Sequence[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, int]], Optional[str], Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """Name a winner only where one candidate clears every applicable target and nothing contradicts it.

    ``targets`` is the candidate's own ``applicable_formats["targets"]`` and is consumed unchanged. The test is
    **per target**: a name must clear each one, and a second name reaching any of them means the credits
    contradict each other under that reading of the format -- `Alpha 3, Beth 2` cannot be a set anyone played,
    because first to two would have ended it. Nothing here infers a target from how many games happen to be on
    tape: a concluded five-game set is not evidence of first to three, and a four-game strict lead is not a
    target either.

    Outcomes are graded rather than binary, and ``decisive`` is only ever a claim about the arithmetic -- the
    name underneath it is priced by the tier, not by this word.
    """
    formats = dict(formats or {})
    targets = sorted(int(t) for t in targets or ())
    credits = dict(credits or {})
    raw_credits = dict(raw_credits) if raw_credits is not None else None
    top = max(targets) if targets else None
    per_target = {str(t): sorted(name for name, wins in credits.items() if wins >= t) for t in targets}
    contradictions = {key: value for key, value in per_target.items() if len(value) > 1}
    reaching_all = sorted(name for name, wins in credits.items() if top is not None and wins >= top)
    residual = {"targets": list(targets), "top_target": top, "per_target": per_target,
                "format": {key: formats.get(key) for key in
                           ("basis", "format_known", "ambiguous", "policy_source", "default_games_to_win",
                            "stage_vocabulary_applied", "readings_without_a_stage") if key in formats},
                "credits": dict(sorted(credits.items(), key=lambda kv: (-kv[1], str(kv[0])))),
                # The tally exactly as recorded, kept beside the smaller one used whenever a quarantine
                # removed anything: a reader has to see what was taken out to reach the figure.
                "credits_as_recorded": (dict(sorted(raw_credits.items(), key=lambda kv: (-kv[1], str(kv[0]))))
                                        if raw_credits and raw_credits != credits else None),
                "excluded": list(excluded), "duplicates_quarantined": list(duplicates or []),
                "leader": (max(credits, key=lambda k: credits[k]) if credits else None),
                "leader_wins": (max(credits.values()) if credits else None)}
    blockers = [str(row) for row in blockers if str(row).strip()]

    if not targets:
        return dict(residual, name=None, status="none", decisive=False, source=source,
                    why_not="no target applies",
                    because="the format is unknown for this span: nothing may be measured against nothing, "
                            "and a winner is never read off the game count that happened to be watched")
    if contradictions:
        return dict(residual, name=(reaching_all[0] if len(reaching_all) == 1 else None), status="ambiguous",
                    decisive=False, source=source, why_not="incompatible credits",
                    because="under %s more than one name reaches the target (%s), so these credits cannot all "
                            "be true at once and no reading of the format is safe to call decisive"
                            % (" and ".join("first to %s" % key for key in sorted(contradictions)),
                               "; ".join("%s: %s" % (key, ", ".join(value))
                                         for key, value in sorted(contradictions.items()))),
                    contradictions=contradictions, blockers=blockers)
    if not reaching_all:
        would_have = sorted(name for name, wins in (raw_credits or {}).items()
                            if top is not None and wins >= top)
        if duplicates and would_have:
            # The tally that reached the target did so on games that repeat each other's rounds. That is not a
            # shortfall to price down; it is a figure that cannot be used, and the status says which.
            return dict(residual, name=None, status="blocked", decisive=False, source=source,
                        why_not="duplicate_credits_quarantined",
                        because="%s reached the target only on games carrying the same rounds as each other; "
                                "those credits are quarantined, what remains (%s) does not reach first to %s, "
                                "and the figure exactly as recorded is published above"
                                % (", ".join(would_have),
                                   ", ".join("%s %d" % (name, wins) for name, wins in
                                             sorted(credits.items(), key=lambda kv: -kv[1])) or "nobody",
                                   " or ".join(str(t) for t in targets)),
                        blockers=blockers)
        return dict(residual, name=None, status="none", decisive=False, source=source,
                    why_not="no candidate reaches every target",
                    because=("%s holds the most credited wins (%s) and the applicable targets are %s, so the "
                             "arithmetic still leaves the set open under at least one reading of the format"
                             % (residual["leader"], residual["leader_wins"],
                                " or ".join("first to %d" % t for t in targets)))
                    if residual["leader"] else "nobody holds a credited win", blockers=blockers)
    name = reaching_all[0]
    estimate = dict(residual, name=name, wins=credits[name], source=source,
                    targets_met=[t for t in targets if credits[name] >= t],
                    targets_missed=[t for t in targets if credits[name] < t],
                    clears_every_applicable_target=True, blockers=blockers,
                    status_describes="the arithmetic against the targets this candidate admits, not the "
                                     "trustworthiness of the name: read it beside the tier and the score")
    if membership in HARD_MEMBERSHIP_VETO:
        return dict(estimate, name=None, status="blocked", decisive=False,
                    why_not="membership_%s" % membership,
                    because="membership is %s, so these games are not established as one set and no tally "
                            "across them names a winner -- the leader and the figures stay published above"
                            % membership)
    reasons = list(blockers)
    if membership in SOFT_MEMBERSHIP_VETO:
        reasons.append("membership is %s: nothing argued these games are one set, so a tally across them is a "
                       "candidate and not a score" % membership)
    if reasons:
        return dict(estimate, status="conditional", decisive=False, conditional_because="; ".join(reasons),
                    because="the arithmetic reaches every target, but %s -- so this name is published as a "
                            "candidate to check, not as a result" % "; ".join(reasons))
    return dict(estimate, status="decisive", decisive=True)


# ------------------------------------------------------------------------------ structured internals
def _blocker_kinds(attributed):
    # type: (Dict[str, Any]) -> List[str]
    """Which kinds of reason the recovery layer gave for withholding per-player credits, read by field.

    ``_attributed_wins`` publishes typed blockers and those are the interface. The fallback derives a kind from
    the structured fields it has always emitted -- a seat holding two participants is a `renamed_seat` conflict
    whoever wrote the sentence -- because a report that lost the conflict whenever wording arrived without a
    kind would be the same bypass in a new coat. Nothing here matches English to decide a policy.
    """
    rows = list(attributed.get("blockers") or [])
    kinds = sorted({str(row.get("kind")) for row in rows if row.get("kind")})
    if kinds:
        return kinds
    if attributed.get("participant_hypotheses"):
        return ["renamed_seat"]
    return []


def _seat_state_by_game(games, links):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> Dict[float, Dict[str, Any]]
    """The seat answer attached to each game's opening gap, in the recovery layer's own vocabulary.

    Only a gap that produced a seat record counts. A gap that raised no question and drew no answer leaves the
    game out of this map entirely, which scores nothing: the machine did not fail to answer a question nobody
    put, and defaulting every game to "unknown" would price 27 unanswered questions as 27 mistakes.
    """
    states = {}
    ordered = sorted((g for g in games if g.get("start") is not None), key=lambda g: float(g["start"]))
    for link in links or []:
        at, seat = link.get("at"), link.get("seat_continuity")
        if at is None or seat not in SEAT_STATE_MEANING:
            continue
        following = next((g for g in ordered if float(g["start"]) >= float(at) - 1e-6), None)
        if following is not None:
            states.setdefault(round(float(following["start"]), 6),
                              {"state": seat, "at": round(float(at), 3),
                               "link_identity": link.get("identity"),
                               "questions": [str(question.get("kind")) for question
                                             in (link.get("questions") or [])]})
    return states


def _witness_for(witnesses, game):
    # type: (Sequence[Dict[str, Any]], Dict[str, Any]) -> Optional[Dict[str, Any]]
    """The independent evidence about this game, if anything outside the broadcast graphic speaks to it.

    A witness is scoped: an operator answer about one opening belongs to that game, a verified result sheet
    about the set speaks for all of it. One game's witness must not quietly lift the cap on the games nobody
    answered for, which is what a candidate-level count would have done.
    """
    for witness in witnesses or []:
        if witness.get("scope") == "candidate":
            return dict(witness, scope="candidate")
        at = witness.get("at")
        if at is None:
            continue
        if float(game.get("start") or 0.0) - 1e-6 <= float(at) <= float(game.get("end") or 0.0) + 1e-6:
            return dict(witness, scope="game")
    return None


def _duplicate_groups(games):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Games that repeat another game's round ids -- the one duplicate signal this metadata really has.

    A replay, a re-cut boundary and a genuinely identical round sequence all look the same to a nameplate, so
    nothing is *claimed* about games that merely resemble each other: only an exact repeat of the round
    identifiers quarantines a credit. Everything else stays in ``unavailable_evidence`` as the marking that was
    never made.
    """
    groups = {}  # type: Dict[Tuple[str, ...], List[float]]
    for entry in games:
        ids = tuple(entry.get("round_ids") or ())
        if not ids:
            continue
        groups.setdefault(tuple(sorted(ids)), []).append(round(float(entry["start"]), 6))
    return [{"round_ids": list(ids), "at_starts": sorted(starts),
             "because": "these games carry the same rounds, so one win may be counted %d times and nothing on "
                        "tape says which start is the real one" % len(starts)}
            for ids, starts in sorted(groups.items()) if len(starts) > 1]


def _without_duplicates(credits_by_name, games_by_name, blocked):
    # type: (Dict[str, int], Dict[str, Sequence[Any]], set) -> Dict[str, int]
    """Recount every name's wins ignoring the games whose credits were quarantined."""
    if not blocked:
        return dict(credits_by_name or {})
    out = {}
    for name, wins in (credits_by_name or {}).items():
        held = list(games_by_name.get(name) or [])
        removed = sum(1 for at in held if round(float(at), 6) in blocked) if held else 0
        out[name] = max(0, int(wins) - removed)
    return out


def _clean_hypothesis_credits(tally_rows, rows_by_at):
    # type: (Dict[str, Dict[str, Any]], Dict[float, Dict[str, Any]]) -> Tuple[Dict[str, int], List[Dict[str, Any]]]
    """Split the nameplate tally into names whose games hold together and names that do not.

    A name is dropped from the estimate when a game behind it never had an admitted read, was not counted
    toward the set, or read the graphic differently inside the game in a way the timing could not explain.
    Dropping is not silent: the reason travels with the name into ``excluded``.
    """
    clean, excluded = {}, []
    for name, data in tally_rows.items():
        problems = []
        for at in data.get("games") or []:
            row = rows_by_at.get(round(float(at), 6)) or {}
            if not row:
                problems.append("the game at %s carries no identity read in this report" % at)
            elif not row.get("admitted"):
                problems.append("the game at %s has no admitted nameplate read" % at)
            elif not row.get("counts_toward_set"):
                problems.append("the game at %s does not count toward the set" % at)
            elif row.get("contradicted"):
                # Matched on side and name: an explanation about the other side's header cannot rescue the
                # credit this tally is about to take.
                explanation = repaint_explanation(row.get("nameplate_timing"), row.get("winner_side"),
                                                  row.get("name"))
                if not explanation.get("supported"):
                    problems.append("the rows inside the game at %s read %s on %s differently and nothing in "
                                    "their timing explains it (%s)"
                                    % (at, row.get("name"), row.get("winner_side"),
                                       explanation.get("because")))
        if problems:
            excluded.append({"name": name, "wins": data.get("wins"), "because": "; ".join(problems)})
        else:
            clean[name] = int(data.get("wins") or 0)
    return clean, excluded


# ---------------------------------------------------------------------------- one candidate's report
def candidate_report(games, band_map=None, pairings=None, nameplate=None, attributed=None, formats=None,
                      membership=None, links=None, status=None, authoritative_winner=None, deciding=None,
                      deciding_unit=None, characters=None, external_witnesses=(), aliases=()):
    # type: (Sequence[Dict[str, Any]], Optional[Dict[float, Dict[str, Any]]], Optional[Dict[float, Dict[str, Any]]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str], Optional[Sequence[Dict[str, Any]]], Optional[str], Optional[Dict[str, int]], Optional[str], Optional[Dict[str, Any]], Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """The explainable confidence report for one candidate set: game rows, a set score, a graded estimate.

    Inert by construction. Nothing here is read by grouping, target closure or ``set_winner``; the
    authoritative verdict is echoed beside the estimate so a reader can see whether they agree, never so one
    becomes the other. ``external_witnesses`` is the only thing that can lift the single-witness cap, and the
    recovery layer passes none for a recording whose results were never verified.
    """
    band_map, pairings = dict(band_map or {}), dict(pairings or {})
    nameplate, attributed = dict(nameplate or {}), dict(attributed or {})
    formats, links = dict(formats or {}), list(links or [])
    membership = membership or "unset"
    hypothesis_rows = {round(float(r.get("at")), 6): r
                       for r in (nameplate.get("games") or []) if r.get("at") is not None}
    credited_by = {round(float(row.get("game_start")), 6)
                   for row in (attributed.get("per_game") or []) if row.get("game_start") is not None}
    seat_states = _seat_state_by_game(games, links)
    external = [dict(row) for row in (external_witnesses or []) if isinstance(row, dict)
                and str(row.get("origin") or "") in INDEPENDENT_WITNESS_ORIGINS]

    groups = _duplicate_groups(games)
    duplicate_starts = {start for group in groups for start in group["at_starts"]}
    rows = []
    for entry in games:
        key = round(float(entry["start"]), 6)
        rows.append(game_report(entry, cover=band_map.get(key), pairing=pairings.get(key),
                                hypothesis=hypothesis_rows.get(key) or {}, credited=key in credited_by,
                                seat=seat_states.get(key) or {}, duplicate=key in duplicate_starts,
                                independent_witness=_witness_for(external, entry),
                                timing=nameplate_timing(pairings.get(key), entry, aliases=aliases),
                                aliases=aliases))
    for row in rows:
        if row["at"] is not None and round(float(row["at"]), 6) in duplicate_starts:
            row["credit_quarantined"] = True
    rows_by_at = {round(float(row["at"]), 6): row for row in rows if row.get("at") is not None}

    positive, negative = [], []

    def credit(factor, axis, why, count=1, **provenance):
        table = OCR_WEIGHTS if axis == "ocr" else ATTRIBUTION_WEIGHTS
        unit = table[factor]
        row = _side(factor, unit * count, axis, why, **provenance)
        if count != 1:
            row["weight_per_game"] = unit
            row["count"] = count
        (positive if row["weight"] > 0 else negative).append(row)

    withheld = str(attributed.get("withheld") or "")
    kinds = _blocker_kinds(attributed)
    conflict_kinds = [kind for kind in kinds if kind in CONFLICT_BLOCKERS]
    coverage_kinds = [kind for kind in kinds if kind in COVERAGE_BLOCKERS]
    blockers, credit_blockers, hypothesis_blockers, absent_at_set = [], [], [], None
    for kind in conflict_kinds:
        rows_for = [row for row in (attributed.get("blockers") or []) if row.get("kind") == kind]
        text = "; ".join(str(row.get("why") or "") for row in rows_for).strip() or withheld
        label = "competing_names_for_one_seat" if kind == "renamed_seat" else "contested_mapping"
        if not text:
            # A typed conflict with no wording still blocks: the kind is the policy, the sentence is not.
            text = "the recovery layer published a %s conflict for this candidate" % kind
        credit(label, "attribution", text, at=[row.get("at") for row in rows_for])
        blockers.append(text)
        credit_blockers.append(text)
        hypothesis_blockers.append(text)
    if coverage_kinds and not conflict_kinds:
        # A win nobody could credit, with no contradiction behind it: coverage, not conflict. The credited
        # tally is short, so that route says so, and the nameplate route keeps its voice.
        credit("credits_are_incomplete", "attribution",
               "the credited tally is short of the games this candidate contains: %s" % withheld,
               at=[row.get("at") for row in (attributed.get("blockers") or [])])
        credit_blockers.append(withheld or "the credited tally is short of this candidate's games")
        absent_at_set = {"evidence": "per-player win credits", "axis": "attribution_confidence",
                         "status": "absent", "because": withheld or "some counted wins were not credited"}
    if attributed.get("person_tally_possible") is False and "renamed_seat" not in conflict_kinds:
        text = ("a screen side is named as two participants and the fighters never said whether the seat "
                "moved, so side-wins are not one person's wins")
        credit("renamed_seat_unresolved", "attribution", text, at=withheld)
        blockers.append(text)
        hypothesis_blockers.append(text)
    if duplicate_starts:
        text = ("credits from %d game(s) were quarantined because they repeat another game's rounds"
                % len(duplicate_starts))
        credit("duplicate_or_replay_evidence", "attribution",
               "%d game(s) repeat another game's round ids, so the same win may be counted twice; their "
               "credits are quarantined from the estimate rather than discounted" % len(duplicate_starts),
               at=sorted(duplicate_starts))
        blockers.append(text)
    if membership in HARD_MEMBERSHIP_VETO:
        credit("membership_disputed", "attribution",
               "membership is %s, so these games may not all belong to one set" % membership,
               membership=membership)
    elif membership in SOFT_MEMBERSHIP_VETO:
        credit("membership_assumed", "attribution",
               "nothing contradicted these games being one set and nothing supported it either"
               if membership == "assumed" else "membership was never decided for this candidate",
               membership=membership)
    uncounted = [row for row in rows if not row["counts_toward_set"]]
    if uncounted:
        credit("games_not_counted", "attribution",
               "%d game(s) in this candidate do not count toward the set, so their wins cannot carry a person "
               "to a target" % len(uncounted), count=len(uncounted), at=[row["at"] for row in uncounted])
    if any(row["basis"] == "header_band_saved" for row in rows):
        credit("band_crop_readings", "attribution",
               "at least one game's sides came from a crop read at a declared instant, which is the stronger "
               "route to the same one graphic", independent=False,
               at=[row["at"] for row in rows if row["basis"] == "header_band_saved"])
    if any(row["seat_state"] in ("held", "exchanged") for row in rows):
        credit("seat_continuity_observed", "attribution",
               "the fighters settled at least one neighbouring gap, which corroborates a mapping and verifies "
               "nobody: the read comes from the same character columns the audit below refuses to call side "
               "evidence", independent=False,
               at=[row["at"] for row in rows if row["seat_state"] in ("held", "exchanged")])
    if nameplate.get("unnamed_games"):
        credit("games_the_graphic_never_named", "ocr",
               "%d game(s) carry no admitted nameplate read at all" % len(nameplate["unnamed_games"]),
               count=len(nameplate["unnamed_games"]), at=nameplate["unnamed_games"])

    # A set score with no sentence attached is not an explainable number: when the candidate added nothing of
    # its own, the games' factors are summarised here -- counted, and marked reported rather than scored,
    # because pricing them again would charge the same evidence twice.
    by_factor = {}
    for game_row in rows:
        for factor in game_row["negative_factors"] + game_row["positive_factors"]:
            entry = by_factor.setdefault(factor["factor"],
                                         {"factor": factor["factor"], "count": 0, "axis": factor["axis"],
                                          "why": factor["why"], "origin": factor.get("origin"),
                                          "independent": factor.get("independent"),
                                          "weight_per_game": factor["weight"]})
            entry["count"] += 1
    why_this_score = sorted(by_factor.values(),
                            key=lambda entry: (-abs(entry["weight_per_game"]) * entry["count"],
                                               entry["factor"]))[:4]

    mean_ocr = _clamp(sum(row["ocr_read_confidence"] for row in rows) / len(rows)) if rows else SCORE_FLOOR
    counted_rows = [row for row in rows if row["counts_toward_set"] and not row.get("credit_quarantined")] \
        or rows
    base = (sum(row["score"] for row in counted_rows) / len(counted_rows)) if counted_rows else SCORE_FLOOR
    adjustment = sum(row["weight"] for row in positive + negative)
    clamped = max(-SET_ADJUSTMENT_LIMIT, min(SET_ADJUSTMENT_LIMIT, adjustment))
    score = _clamp(base + clamped)
    witnesses = 1 + (1 if any(row["independent_witnesses"] > 1 for row in rows) else 0)
    cap = SCORE_CEILING if witnesses > 1 else MAX_SINGLE_WITNESS_SCORE
    capped = score > cap
    if capped:
        score = cap
    mean_attribution = (_clamp(sum(row["attribution_confidence"] for row in rows) / len(rows))
                        if rows else SCORE_FLOOR)
    conflict = bool(conflict_kinds) or attributed.get("person_tally_possible") is False

    # Both estimate routes are computed on the tally that survives quarantine, with the figure as recorded
    # kept beside it: a duplicate is removed from the arithmetic, and its removal is what a reader can check.
    targets = [int(t) for t in (formats.get("targets") or [])]
    credited_raw = {name: int(wins) for name, wins in (attributed.get("game_wins_by_identity") or {}).items()}
    credited_games = {}
    for row in attributed.get("per_game") or []:
        if row.get("game_start") is not None and row.get("credited_to"):
            credited_games.setdefault(row["credited_to"], []).append(row["game_start"])
    credited_usable = _without_duplicates(credited_raw, credited_games, duplicate_starts)
    tally_rows = {row["name"]: {"wins": int(row["wins"]), "games": list(row.get("games") or [])}
                  for row in (nameplate.get("possible_winners") or []) if row.get("name")}
    clean, excluded = _clean_hypothesis_credits(tally_rows, rows_by_at)
    hypothesis_raw = {name: data["wins"] for name, data in tally_rows.items()}
    hypothesis_usable = _without_duplicates(clean, {name: data["games"] for name, data in tally_rows.items()},
                                            duplicate_starts)
    estimate_credits = winner_estimate(targets, credited_usable, blockers=credit_blockers,
                                       source="credited_wins", formats=formats, membership=membership,
                                       duplicates=groups, raw_credits=credited_raw)
    estimate_hypothesis = winner_estimate(targets, hypothesis_usable, blockers=hypothesis_blockers,
                                          source="nameplate_hypothesis", excluded=excluded, formats=formats,
                                          membership=membership, duplicates=groups,
                                          raw_credits=hypothesis_raw)
    if estimate_credits["name"]:
        final = estimate_credits
    elif estimate_hypothesis["name"]:
        final = estimate_hypothesis
    else:
        # Neither route names anyone: publish whichever residual carries more of what was seen, because a
        # reviewer deciding where to look next needs the leader and the targets, not an empty dict.
        final = estimate_hypothesis if not credited_raw else estimate_credits
    final = dict(final, authoritative_winner=authoritative_winner,
                 agrees_with_authoritative=(authoritative_winner is not None and bool(authoritative_winner)
                                            and authoritative_winner == final.get("name")))

    out = {
        "kind": "attribution-confidence", "version": 2,
        "score": score, "tier": tier_for(score),
        "needs_review": bool(score < NEEDS_REVIEW_BELOW or conflict
                             or final.get("status") != "decisive"),
        "thresholds": {"tiers": [{"tier": name, "min_score": floor} for name, floor in TIERS],
                       "needs_review_below": NEEDS_REVIEW_BELOW,
                       "single_witness_cap": MAX_SINGLE_WITNESS_SCORE,
                       "set_adjustment_limit": SET_ADJUSTMENT_LIMIT},
        "axes": {"ocr_read_confidence": mean_ocr, "attribution_confidence": mean_attribution,
                 "set_score_base": _clamp(base), "set_score_adjustment": clamped,
                 "set_score_adjustment_uncapped": adjustment,
                 "set_score_adjustment_limit": SET_ADJUSTMENT_LIMIT,
                 "why_the_lower_axis_decides": ("the score is the weaker of the two axes: a legible nameplate "
                                                "on an unestablished seat is not a confident attribution")},
        "single_witness_capped": capped,
        "independent_witnesses": witnesses,
        "independent_witness_origins": sorted({str(row.get("origin")) for row in external}),
        "witness_origins_seen": sorted({row.get("origin") for row in positive + negative
                                        if row.get("origin")}),
        "witness_note": nameplate.get("trust_note") or (
            "the band crops, the nameplate rows and the character columns are reads of one broadcast overlay "
            "by one classifier, so the report never counts them as corroborating sources"),
        "named_candidates": sorted({row["name"] for row in rows if row.get("name")} | set(credited_raw)
                                   | set(hypothesis_raw)),
        "credits": {"credited_wins": credited_raw, "credited_wins_after_quarantine": credited_usable,
                    "nameplate_hypothesis_wins": hypothesis_raw,
                    "nameplate_hypothesis_wins_after_quarantine": hypothesis_usable},
        "duplicates_quarantined": groups,
        "identity_conflict": conflict,
        "estimate_blockers": blockers,
        "winner_estimate": final,
        "estimates_by_tier": {"credited_wins": estimate_credits, "nameplate_hypothesis": estimate_hypothesis},
        "leading_candidate": (None if final.get("name") else final.get("leader")),
        "positive_factors": positive, "negative_factors": negative,
        "why_this_score": why_this_score,
        "unavailable_evidence": ([dict(row, status="absent") for row in UNAVAILABLE_EVIDENCE]
                                 + [row for game_row in rows for row in game_row["unavailable_evidence"]]
                                 + ([absent_at_set] if absent_at_set else [])),
        "games": rows,
        "authoritative": False, "changes_grouping_or_targets": False,
        "did_not_move": {"boundaries": True, "grouping": True, "targets": True, "set_winner": True},
        "score_meaning": ("heuristic confidence, not a calibrated probability: the weights are a ranking of "
                          "evidence and there is no verified result to calibrate them against"),
    }
    # Which readings this project's own tolerance cannot place, per candidate. Reported as uncertainty in
    # both directions: it is not evidence of a second pairing, and merging it would be evidence of one that
    # nobody has. `settled_by` names the observation that would close it, and nothing here goes and gets it.
    unsettled, asked = [], set()

    def key_for(at, side, one, other, **_ignored):
        # The carried-in name is also the first step of the trajectory, so the same open question would be
        # stated twice; one entry per (game, side, pair of spellings), keeping the record that has intervals.
        return (at, side, tuple(sorted([str(one), str(other)])))

    for game_row in rows:
        timing = game_row.get("nameplate_timing") or {}
        for entry in timing.get("alias_uncertain") or []:
            key = key_for(game_row["at"], entry.get("side"), entry.get("carried"), entry.get("credited"))
            if key in asked:
                continue
            asked.add(key)
            unsettled.append({"at": game_row["at"], "side": entry.get("side"),
                              "spellings": [entry.get("carried"), entry.get("credited")],
                              "relation": entry.get("relation"), "similarity": entry.get("similarity"),
                              "identity_established": bool(entry.get("identity_established")),
                              "rule": entry.get("rule"),
                              "carried_age_seconds": entry.get("carried_age_seconds"),
                              "seconds_into_game": entry.get("seconds_into_game"),
                              "settled_by": "a crop of the nameplate at this instant, a roster that binds "
                                            "the tag to a player, or an operator answer"})
        for event in timing.get("reading_relations") or []:
            if event.get("relation") != "alias_uncertain":
                continue
            key = key_for(game_row["at"], event.get("side"), event.get("from"), event.get("to"))
            if key in asked:
                continue
            asked.add(key)
            unsettled.append({"at": game_row["at"], "side": event.get("side"),
                              "spellings": [event.get("from"), event.get("to")],
                              "relation": "alias_within_span_uncertain",
                              "similarity": event.get("similarity"),
                              "identity_established": False, "rule": event.get("rule"),
                              "settled_by": "a crop of the nameplate at each of those instants, or a roster: "
                                            "the span shows two spellings and no way to tell a rename from a "
                                            "misread"})
    if unsettled:
        out["unavailable_evidence"].append({
            "evidence": "participant identity for %d uncertain nameplate spelling pair(s)" % len(unsettled),
            "axis": "attribution_confidence", "status": "absent",
            "because": "the spellings fall inside boundary_names' same-name tolerance, which is a rule for "
                       "comparing readings and not a finding that one person exists",
            "pairs": unsettled})
        out["alias_uncertainty"] = unsettled
    seen_text = display_text_observations(rows)
    refused_text = display_text_correction_refusals(rows)
    if refused_text:
        # Kept out of the list above on purpose. A reader who sees only admitted entries would conclude that
        # every scoped reading was used somewhere, which is the claim that would be false here.
        out["display_text_correction_refusals"] = refused_text
    if seen_text:
        out["display_text_observations"] = seen_text
        out["display_text_note"] = (
            "each entry is a saved frame that was read at one instant on one seat; it corrects displayed text, "
            "binds no participant identity, and is not an acceptance")
        out["unavailable_evidence"] = [
            row for row in out["unavailable_evidence"]
            if not str(row.get("evidence", "")).startswith("participant identity for")]
        remaining = [row for row in (out.get("alias_uncertainty") or [])
                     if row.get("relation") != "bound_participant_identity"]
        if remaining:
            out["unavailable_evidence"].append({
                "evidence": "participant identity for %d uncertain nameplate spelling pair(s)"
                            % len(remaining),
                "axis": "attribution_confidence", "status": "absent",
                "because": "the spellings fall inside boundary_names' same-name tolerance, which is a rule "
                           "for comparing readings and not a finding that one person exists",
                "pairs": remaining})
    if characters:
        # The measured gap replaces the generic one, with its counts: "0 of 30 games are side-bound" is a
        # different thing to act on than "side binding is absent", and neither is a reason to lower a score
        # that never depended on the fighters.
        out["unavailable_evidence"] = [
            row for row in out["unavailable_evidence"] if row["evidence"] != "character-to-side binding"]
        out["unavailable_evidence"].append({
            "evidence": "character-to-side binding", "axis": "attribution_confidence", "status": "absent",
            "measured": {"games_with_a_pair": characters.get("games_with_a_pair"),
                         "games_side_bound": characters.get("games_side_bound"),
                         "recurring_unordered_pairs": characters.get("recurring_unordered_pairs")},
            "would_resolve": "which fighter sits on which side, so a seat can be followed across a rename",
            "because": characters.get("side_binding_basis")})
        out["characters"] = {"games_with_a_pair": characters.get("games_with_a_pair"),
                             "games_side_bound": characters.get("games_side_bound"),
                             "recurring_unordered_pairs": characters.get("recurring_unordered_pairs"),
                             "recurring_pairs_establish_orientation": False}
    if status is not None:
        out["set_status"] = status
    if deciding is not None:
        out["deciding_wins"] = dict(deciding)
        out["deciding_wins_unit"] = deciding_unit
    return out


# ---------------------------------------------------------------------------- characters, offline only
def character_audit(games, pairings=None):
    # type: (Sequence[Dict[str, Any]], Optional[Dict[float, Dict[str, Any]]]) -> Dict[str, Any]
    """What the recording says about *which fighter*, and exactly what it does not say about which side.

    The audit is offline and adds no inference. Every game in the delivered BB9 evidence carries a fighter
    pair and none of them is side-bound: the classification schema names its columns ``character1`` and
    ``character2`` and never declares which screen side either belongs to, so the row's slot is a reading of a
    column, not a fact about the left or right of the screen. That matters beyond tidiness -- the seat
    continuity the confidence layer is offered is read from these same columns, so a pair cannot corroborate a
    mapping it is already assumed inside, and persistence of a *fighter* is not persistence of a *person*.

    A pair that recurs across a set is published as a count and is not evidence of orientation: two players who
    keep their characters for a whole set produce the same recurring pair whichever one of them is on the left.
    What would bind a side is an operator answer or a roster, and the absence is reported as the missing
    evidence it is.
    """
    pairings = dict(pairings or {})
    rows, pairs = [], {}
    for entry in games:
        key = round(float(entry["start"]), 6)
        pairing = pairings.get(key) or {}
        observed = [str(value) for value in (entry.get("characters") or []) if value]
        slots = pairing.get("characters") or {}
        explicit = entry.get("characters_by_side") or {}
        bound = bool(explicit.get("p1") and explicit.get("p2"))
        unordered = tuple(sorted(value.upper() for value in observed))
        if unordered:
            pairs[unordered] = pairs.get(unordered, 0) + 1
        rows.append({"at": entry.get("start"), "observed": observed,
                     "slot_reads": {side: slots.get("player1" if side == "p1" else "player2")
                                    for side in ("p1", "p2")},
                     "every_slot_named": bool(slots.get("player1") and slots.get("player2")),
                     "reads_per_slot": pairing.get("character_reads"), "side_bound": bound,
                     "explicit_binding": dict(explicit) if explicit else None,
                     "winner_side": entry.get("winner"),
                     "counts_toward_set": bool(entry.get("counts_toward_set"))})
    recurring = {"/".join(pair): count for pair, count in pairs.items() if count > 1}
    return {
        "games": rows,
        "games_observed": len(rows),
        "games_with_a_pair": sum(1 for row in rows if len(row["observed"]) >= 2),
        "games_side_bound": sum(1 for row in rows if row["side_bound"]),
        "games_naming_both_slots": sum(1 for row in rows if row["every_slot_named"]),
        "recurring_unordered_pairs": recurring,
        "recurring_pairs_establish_orientation": False,
        "fighter_persistence_establishes_identity": False,
        "why_not": ("a recurring unordered pair is what two players who keep their characters look like from "
                    "either end of the screen, and two players who main the same fighter look the same from "
                    "both -- so neither the pair nor its persistence across a gap says which side is which, "
                    "still less who the human is"),
        "side_binding_basis": (
            "the classification columns are read positionally by the seat-continuity code as well, so a pair "
            "is not independent evidence about the mapping it is already assumed inside"
            if not any(row["side_bound"] for row in rows) else
            "an explicit per-side binding was supplied for at least one game and is carried verbatim"),
        "missing_evidence": [
            "which screen side the columns character1 and character2 describe: the classification prompt asks "
            "for both names and never says which is player 1's",
            "an operator orientation answer for this set, or a roster that binds a name to a character in a "
            "named seat",
            "a second independent read of the fighter positions that does not come from the same two columns "
            "(the score box and the win-row crops name no character at all)"],
        "authoritative": False,
        "changes_grouping_or_targets": False,
        "inference_added": False,
    }
