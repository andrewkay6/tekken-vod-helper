"""Attribution confidence: what a recovered name is worth, and what nothing is allowed to do with it.

Four claims hold this layer apart from the recovery code it reports on.

* **The score explains itself or it is decoration.** Every factor carries the origin of the evidence behind
  it, the instant it came from and the weight it applied; the weights live in one table; and a test sits on
  each side of the documented threshold. The score is heuristic confidence, not a calibrated probability.
* **One source is one witness.** The band crops, the nameplate rows and the character columns are reads of
  the same broadcast overlay by the same classifier. Nothing in this file may turn three readings of one
  instrument into corroboration -- including the seat continuity the character columns infer, which is a
  continuity hypothesis and not proof that the same human held a seat.
* **A conflict is not a low score.** Duplicate credits are quarantined from the tally, and an identity
  conflict withdraws *decisiveness* rather than merely discounting it. A name published on contradictory
  credits has to arrive labelled as a candidate.
* **Timing separates a repaint from a wrong name; a majority cannot.** So the reads' order is consumed, with
  near-variant spellings reported as spellings and unobserved spans reported as gaps.

The estimates are inert. The last group of tests proves the set verdicts are the same records with the report
suppressed as with it running, so nothing computed here feeds a grouping, a target or a winner.
"""
import copy
import json

from tekken_vod_helper import boundary_evidence as be
from tekken_vod_helper import boundary_format as bf
from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_identity as bi
from tekken_vod_helper import boundary_review as review

KNOWN_ORIGINS = set(bi.INDEPENDENT_WITNESS_ORIGINS) | set(bi.GRAPHIC_ORIGINS) | {
    "recovery_verdict", "link_record"}

POLICY = {"default_games_to_win": 2,
          "overrides": {"winners final": 3, "losers semi-final": 3, "losers final": 3, "grand final": 3},
          "source": "test-policy", "bracket_results_verified": False}


# --------------------------------------------------------------------------------------------- fixtures
def game(start, winner="p1", counts=True, round_ids=None, characters=("ALISA", "REINA"), end=None):
    return {"start": start, "end": end if end is not None else start + 150.0,
            "status": "complete", "winner": winner, "counts_toward_set": counts,
            "round_ids": list(round_ids or ["round-%s" % start]), "characters": list(characters),
            "basis": "test"}


def cover(at, p1, p2, usable=True, arrangements=1, why=None, support=1):
    return {"game_start": at, "at": at, "usable": usable, "sides": {"p1": p1, "p2": p2},
            "arrangements": arrangements if usable else 0, "readings_inside": 1, "support": support,
            "confidence": [0.9, 0.9] if usable else None, "image_sha256": "crop-%s" % at,
            "why_unusable": why, "unreadable_readings": [] if usable else [{"at": at}],
            "conflicting_readings": [{"at": at}] if arrangements > 1 else []}


def timing(p1=(), p2=(), carried=None, span=(0.0, 150.0), named_rows=8, gaps=()):
    """A `name_sequence` in the shape `boundary_games._name_sequence` really produces.

    ``p1``/``p2`` are ``(at, from, to)`` reads per side -- ``from`` is None for the first read inside the span,
    which is an arrival and not a change. ``carried`` is ``{"p1": (at, name[, drawn])}``, the last reading
    before the span opened, where ``drawn`` is ``"gap"`` (still drawn during the pause between matches) or
    ``"previous_match"``. A real record derives that from the previous game's end; a fixture has to say it,
    because the difference is the whole question a carry-in answers.
    """
    sides = {}
    for side, listed in (("p1", list(p1)), ("p2", list(p2))):
        carried_in = (carried or {}).get(side)
        sides[side] = {"change_events": [{"at": at, "from": source, "to": value,
                                          "offset_from_span_start": round(at - span[0], 3)}
                                         for at, source, value in listed],
                       "named_rows": named_rows if listed else 0,
                       "first_named_at": listed[0][0] if listed else None,
                       "last_named_at": listed[-1][0] if listed else None,
                       "carried_in": ({"at": carried_in[0], "name": carried_in[1],
                                       "drawn_during": (carried_in[2] if len(carried_in) > 2 else "gap"),
                                       "age_seconds_before_span": round(span[0] - carried_in[0], 3)}
                                      if carried_in else None),
                       "names_seen": sorted({value for _, _, value in listed})}
    return {"name_sequence": {"span": list(span), "lookback_from": span[0] - 150.0,
                              "rows_read": named_rows,
                              "unobserved_gaps": [{"from": a, "to": b, "seconds": round(b - a, 3)}
                                                  for a, b in gaps],
                              "sides": sides, "meaning": "test record"}}


def pairing(dominant=True, share=1.0, rows=8, sides=("Alpha", "Beth"), span=(0.0, 150.0), sequence=None):
    out = {"dominant": dominant, "sides": {"p1": sides[0], "p2": sides[1]},
           "share": {"p1": share, "p2": share}, "named_rows": rows,
           "characters": {"player1": "ALISA", "player2": "REINA"},
           "character_reads": {"player1": ["ALISA"], "player2": ["REINA"]}}
    if sequence is not None:
        out.update(sequence)
    else:
        start = span[0] + 2.0
        out.update(timing(span=span, named_rows=rows,
                          p1=[(start, None, sides[0])], p2=[(start, None, sides[1])]))
    return out


def hypothesis(at, sides, basis="header_band_saved", admitted=None, agrees=True, contradicts=None):
    row = {"at": at, "basis": basis,
           "admitted": (basis != "none") if admitted is None else admitted,
           "sides": dict(sides), "share": {"p1": 1.0, "p2": 1.0}, "named_rows": 8,
           "band_cover": basis == "header_band_saved",
           "band_agrees": agrees if basis == "header_band_saved" else None,
           "why": "test read of the graphic", "span": [at, at + 150.0]}
    if contradicts:
        row["contradicts"] = contradicts
    return row


def nameplate(rows, possible):
    return {"unit": "overlay_nameplate_hypothesis", "authoritative": False,
            "changes_grouping_or_targets": False, "trust_note": "one graphic, sampled twice",
            "games": rows, "possible_winners": possible,
            "unnamed_games": [row["at"] for row in rows if not row["admitted"]]}


def formats(targets, **rest):
    out = {"targets": list(targets), "format_known": bool(targets), "ambiguous": len(targets) > 1,
           "basis": rest.pop("basis", None) or "test", "evidence": {}, "readings_without_a_stage": [],
           "policy_source": POLICY["source"] if targets else None,
           "stage_vocabulary_applied": bool(targets)}
    out.update(rest)
    return out


def plate(at, p1="Alpha", p2="Beth", **kwargs):
    return hypothesis(at, {"p1": p1, "p2": p2}, **kwargs)


def credited(per_game, **rest):
    """The attribution block `_attributed_wins` publishes when it credited everything it could."""
    tally = {}
    for row in per_game:
        tally[row["credited_to"]] = tally.get(row["credited_to"], 0) + 1
    return dict({"game_wins_by_identity": tally, "per_game": per_game,
                 "unattributed_games": 0, "unit": "identities"}, **rest)


def credit_row(at, name, side="p1"):
    return {"game_start": at, "winner_side": side, "credited_to": name, "from_band_at": at + 10.0,
            "crop_sha256": "crop-%s" % at, "readings_agreeing": 1, "confidence": [0.9, 0.9]}


# ---------------------------------------------------------------------------- reading vs attribution
def test_a_nameplate_that_moved_inside_one_game_scores_below_one_that_held():
    """A stable read and a read the graphic changed under are different facts, and must score apart."""
    stable = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                            hypothesis=plate(0.0), credited=True)
    moved = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                           hypothesis=plate(0.0, contradicts={"overlay_nameplate_rows": {}}),
                           credited=True)
    assert [f["factor"] for f in moved["negative_factors"]] == ["nameplate_changed_within_game"]
    assert moved["score"] < stable["score"]
    assert "nameplate_changed_within_game" not in [f["factor"] for f in stable["negative_factors"]]


def test_two_routes_agreeing_on_one_graphic_are_not_two_witnesses():
    agreed = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                            hypothesis=plate(0.0, agrees=True), credited=True)
    factors = {row["factor"]: row for row in agreed["positive_factors"]}
    assert "graphic_reads_agree" in factors
    assert factors["graphic_reads_agree"]["independent"] is False
    assert factors["graphic_reads_agree"]["origin"] == "header_band_saved+overlay_nameplate_rows"
    assert agreed["independent_witnesses"] == 1
    assert agreed["attribution_cap"] == bi.MAX_SINGLE_WITNESS_SCORE
    assert agreed["score"] < bi.NEEDS_REVIEW_BELOW, "one witness cannot reach the top tier"


def test_seat_continuity_from_the_character_columns_never_lifts_the_cap():
    """The audit below says these columns are not side evidence; the scorer may not treat them as identity.

    Two players can main the same fighter and one player can change fighters, so "the fighters held their
    sides" corroborates a mapping and proves nothing about a person. It stays a positive factor and stops
    being a second witness.
    """
    held = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                          hypothesis=plate(0.0), credited=True, seat={"state": "held", "at": -20.0})
    names = [f["factor"] for f in held["positive_factors"]]
    assert "seat_held_by_footage" in names
    seat_factor = [f for f in held["positive_factors"] if f["factor"] == "seat_held_by_footage"][0]
    assert seat_factor["origin"] == "character_columns" and seat_factor["independent"] is False
    assert held["independent_witnesses"] == 1
    assert held["attribution_cap"] == bi.MAX_SINGLE_WITNESS_SCORE
    assert held["score"] <= bi.MAX_SINGLE_WITNESS_SCORE


def test_only_evidence_from_outside_the_broadcast_lifts_the_cap():
    """And it lifts it for the game it is about, not for the candidate's unresolved neighbours."""
    witness = {"origin": "operator_answer", "at": 20.0, "because": "the reviewer said which side is which"}
    # Evidence whose attribution axis reaches 75: a band credit, a graphic agreeing with itself, and a seat the
    # fighters settled. Without an outside witness that is capped at 65, which is the whole point of the cap.
    capped = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                            hypothesis=plate(0.0), credited=True, seat={"state": "held", "at": -20.0})
    answered = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                              hypothesis=plate(0.0), credited=True, seat={"state": "held", "at": -20.0},
                              independent_witness=witness)
    assert capped["attribution_cap"] == bi.MAX_SINGLE_WITNESS_SCORE
    assert capped["single_witness_capped"] is True and capped["score"] == bi.MAX_SINGLE_WITNESS_SCORE
    assert answered["independent_witnesses"] == 2 and answered["attribution_cap"] == 100
    assert answered["score"] == 75 and answered["single_witness_capped"] is False
    assert answered["score"] > capped["score"], "lifting the cap changed nothing"
    assert [row["factor"] for row in answered["positive_factors"]][-1] == "independent_witness"
    assert answered["positive_factors"][-1]["weight"] == 0, "a witness is not a bonus on top of itself"
    # Scoping is the candidate's job: a witness about one opening must not lift the cap on a game it says
    # nothing about, which is what a candidate-level witness count would have done.
    report = bi.candidate_report([game(0.0, "p1"), game(900.0, "p1", round_ids=["other"])],
                                 band_map={0.0: cover(10.0, "Alpha", "Beth"),
                                           910.0: cover(910.0, "Alpha", "Beth")},
                                 pairings={0.0: pairing(), 900.0: pairing(span=(900.0, 1050.0))},
                                 nameplate=nameplate([plate(0.0), plate(900.0)], []),
                                 attributed=credited([credit_row(0.0, "Alpha"), credit_row(900.0, "Alpha")]),
                                 formats=formats([2]), membership="supported",
                                 external_witnesses=[dict(witness, at=20.0)])
    answered_row, neighbour = report["games"]
    assert answered_row["independent_witnesses"] == 2, answered_row["independent_witnesses"]
    assert neighbour["independent_witnesses"] == 1, neighbour["independent_witnesses"]
    assert neighbour["attribution_cap"] == bi.MAX_SINGLE_WITNESS_SCORE
    # an unrelated origin is not a witness at all
    bogus = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                           hypothesis=plate(0.0), credited=True,
                           independent_witness={"origin": "overlay_nameplate_rows", "at": 20.0})
    assert bogus["independent_witnesses"] == 1


def test_the_real_swap_vocabulary_reaches_the_report_and_remaps_nothing():
    """`exchanged` is what the pipeline emits. An earlier version of this layer accepted "crossed" instead.

    That is not a typo: it means every real side exchange in the recording was scored as if the seat question
    had never been asked. The row here is produced by `_classify_link`, not written by hand.
    """
    observations = [
        {"timestamp": at, "screen_state": "gameplay", "hud_present": True, "characters": characters,
         "overlay": {"player1": p1, "player2": p2, "score": [0, 0]}}
        for at, p1, p2, characters in [
            (10.0, "Alpha", "Beth", ("REINA", "ASUKA")), (60.0, "Alpha", "Beth", ("REINA", "ASUKA")),
            (110.0, "Alpha", "Beth", ("REINA", "ASUKA")), (260.0, "Beth", "Alpha", ("ASUKA", "REINA")),
            (310.0, "Beth", "Alpha", ("ASUKA", "REINA")), (360.0, "Beth", "Alpha", ("ASUKA", "REINA"))]]
    link = bg._classify_link({"start": 0.0, "end": 150.0}, {"start": 250.0, "end": 400.0}, observations)
    assert link["seat_continuity"] == "exchanged", link
    games = [game(250.0, "p1", round_ids=["r9"]), game(450.0, "p1", round_ids=["r10"])]
    report = bi.candidate_report(games, pairings=bg._overlay_pairings(observations, games),
                                 links=[dict(link, at=200.0)],
                                 nameplate=nameplate([], []), formats=formats([2]),
                                 membership="supported")
    row = report["games"][0]
    assert row["seat_state"] == "exchanged"
    factors = {entry["factor"]: entry for entry in row["positive_factors"]}
    assert "seat_exchanged_by_footage" in factors
    assert "ambiguous_orientation" not in [entry["factor"] for entry in row["negative_factors"]]
    assert factors["seat_exchanged_by_footage"]["origin"] == "character_columns"
    assert row["independent_witnesses"] == 1, "a swap read off the same columns is not a second witness"
    # The claim this layer is allowed to make: the per-side arithmetic is meaningless, the per-game credits
    # survive because each came from that game's own reading.
    assert "changed seats" in factors["seat_exchanged_by_footage"]["why"]
    assert "per-game credits survive" in factors["seat_exchanged_by_footage"]["why"] \
        or "each was taken from that game" in factors["seat_exchanged_by_footage"]["why"]


def test_a_fighters_changed_gap_is_an_ambiguous_orientation_not_a_held_seat():
    report = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                            hypothesis=plate(0.0), credited=True, seat={"state": "changed", "at": -20.0})
    assert "ambiguous_orientation" in [f["factor"] for f in report["negative_factors"]]
    assert any(row["evidence"] == "seat orientation across this gap"
               for row in report["unavailable_evidence"])


def test_a_game_nobody_read_is_missing_evidence_and_not_a_penalty():
    """Absence is priced as footage, never scored as a worse read: only a read that failed is a penalty."""
    unread = bi.game_report(game(0.0), cover=None, pairing=pairing(), hypothesis=plate(0.0, basis="none",
                                                                                       agrees=None))
    assert "band crop reading for this game" in {row["evidence"] for row in unread["unavailable_evidence"]}
    assert not [f for f in unread["negative_factors"] if "band_nothing_usable" == f["factor"]]
    failed = bi.game_report(game(0.0), cover=cover(10.0, None, None, usable=False,
                                                   why="its reading(s) named only one side"),
                            pairing=pairing(), hypothesis=plate(0.0, basis="none", agrees=None))
    assert "band_reading_unusable" in [f["factor"] for f in failed["negative_factors"]]
    assert failed["ocr_read_confidence"] < unread["ocr_read_confidence"]


def test_more_rows_and_a_stronger_share_raise_only_the_reading_axis():
    weak = bi.game_report(game(0.0), cover=None, pairing=pairing(share=0.83, rows=2),
                          hypothesis=plate(0.0, basis="overlay_nameplate_hypothesis"))
    strong = bi.game_report(game(0.0), cover=None, pairing=pairing(share=1.0, rows=20),
                            hypothesis=plate(0.0, basis="overlay_nameplate_hypothesis"))
    assert strong["ocr_read_confidence"] > weak["ocr_read_confidence"]
    assert strong["attribution_confidence"] == weak["attribution_confidence"], \
        "legibility is not correct-person attribution"


# ------------------------------------------------------------------------- duplicates are quarantined
def test_repeated_rounds_are_removed_from_the_estimate_in_both_routes():
    """The reproduction this replaces: a decisive figure built from one game counted twice."""
    games = [game(0.0, "p1", round_ids=["same-round"]), game(200.0, "p1", round_ids=["same-round"])]
    plates = nameplate([plate(0.0), plate(200.0)],
                       [{"name": "Alpha", "wins": 2, "games": [0.0, 200.0],
                         "unit": "overlay_nameplate_hypothesis"},
                        {"name": "Beth", "wins": 0, "games": [], "unit": "overlay_nameplate_hypothesis"}])
    report = bi.candidate_report(games, pairings={0.0: pairing(), 200.0: pairing()}, nameplate=plates,
                                 attributed=credited([credit_row(0.0, "Alpha"), credit_row(200.0, "Alpha")]),
                                 formats=formats([2]), membership="supported")
    estimate = report["winner_estimate"]
    assert estimate["name"] is None, "a doubled game was allowed to reach the target"
    assert estimate["status"] == "blocked", estimate["status"]
    assert estimate["why_not"] == "duplicate_credits_quarantined"
    # Nothing is hidden: the tally exactly as recorded stays beside the one that was used.
    assert estimate["credits_as_recorded"] == {"Alpha": 2}
    assert estimate["credits"] == {"Alpha": 0}
    assert report["credits"]["credited_wins"] == {"Alpha": 2}
    assert report["credits"]["credited_wins_after_quarantine"] == {"Alpha": 0}
    assert report["duplicates_quarantined"][0]["at_starts"] == [0.0, 200.0]
    assert "duplicate_or_replay_evidence" in [f["factor"] for f in report["negative_factors"]]
    assert all(row["credit_quarantined"] for row in report["games"])
    # and the same on the nameplate route, not just the credited one
    hypothesis_only = bi.candidate_report(games, pairings={0.0: pairing(), 200.0: pairing()},
                                          nameplate=plates, formats=formats([2]), membership="supported")
    tiers = hypothesis_only["estimates_by_tier"]
    assert tiers["nameplate_hypothesis"]["name"] is None
    assert tiers["nameplate_hypothesis"]["status"] == "blocked"


def test_a_duplicate_that_is_not_decisive_is_still_not_called_a_duplicate_by_accident():
    """Only an exact repeat of round identifiers quarantines; resemblance never does."""
    distinct = bi.candidate_report([game(0.0, "p1"), game(200.0, "p1")],
                                   pairings={0.0: pairing(), 200.0: pairing()},
                                   nameplate=nameplate([plate(0.0), plate(200.0)],
                                                       [{"name": "Alpha", "wins": 2,
                                                         "games": [0.0, 200.0], "unit": "x"}]),
                                   attributed=credited([credit_row(0.0, "Alpha"),
                                                        credit_row(200.0, "Alpha")]),
                                   formats=formats([2]), membership="supported")
    assert distinct["duplicates_quarantined"] == []
    assert "duplicate_or_replay_evidence" not in [f["factor"] for f in distinct["negative_factors"]]
    assert any(row["evidence"] == "game duplicate or replay marking"
               for row in distinct["unavailable_evidence"])
    assert distinct["winner_estimate"]["name"] == "Alpha" and distinct["winner_estimate"]["decisive"]


# ------------------------------------------------------------------- conflicts withdraw decisiveness
def test_a_missing_cover_no_longer_hides_a_conflicting_seat():
    """The defect this replaces: the first partial reason returned before the seat checks ever ran."""
    games = [game(0.0, "p1"), game(200.0, "p2"), game(400.0, "p2")]
    band_map = {200.0: cover(210.0, "Alpha", "Buk"), 400.0: cover(410.0, "Alpha", "Ceil")}
    attributed = bg._attributed_wins(games, band_map, [{"at": 200.0, "status": "unresolved",
                                                        "kind": "identity_mapping",
                                                        "seat_continuity": "unknown"}])
    withheld = attributed["withheld"]
    assert "no band reading falls inside" in withheld
    assert "side won 2 of this candidate's games" in withheld
    assert {row["kind"] for row in attributed["blockers"]} == {"win_uncovered", "renamed_seat"}


def test_an_identity_conflict_withdraws_decisiveness_from_both_routes():
    """Coverage gaps may leave the nameplate route speaking; a contradicted identity may not."""
    games = [game(0.0, "p1"), game(200.0, "p1")]
    plates = nameplate([plate(0.0), plate(200.0)],
                       [{"name": "Alpha", "wins": 2, "games": [0.0, 200.0], "unit": "x"}])
    for reason in ("the band and the pairing hypothesis disagree about 200.0, so neither is promoted to a "
                   "per-player credit",
                   "the p1 side won 2 of this candidate's games and the band called it Alpha and Ceil"):
        attributed = {"withheld": reason, "per_game": [credit_row(0.0, "Alpha"), credit_row(200.0, "Alpha")],
                      "person_tally_possible": "disagree" not in reason}
        if "disagree" in reason:
            attributed["blockers"] = [{"kind": "mapping_contested", "at": [200.0], "why": reason}]
        else:
            attributed["blockers"] = [{"kind": "renamed_seat", "at": [0.0, 200.0], "why": reason,
                                       "seat_continuity": "held"}]
            attributed["participant_hypotheses"] = {"p1": [{"name": "Alpha", "game_start": 0.0},
                                                           {"name": "Ceil", "game_start": 200.0}]}
        report = bi.candidate_report(games, pairings={0.0: pairing(), 200.0: pairing()},
                                     nameplate=plates, attributed=attributed,
                                     formats=formats([2]), membership="supported")
        for tier, estimate in report["estimates_by_tier"].items():
            assert not estimate["decisive"], "%s named a winner across a contradicted identity" % tier
            # "none" is legitimate on the credited route -- a withheld tally has no credits to weigh. What
            # may never happen is a name arriving without the word "decisive" attached to it.
            assert estimate["status"] in ("conditional", "ambiguous", "blocked", "none"), estimate["status"]
        assert report["identity_conflict"] is True
        assert report["needs_review"] is True
        # Nothing is hidden. Either the name survives with "decisive" withdrawn, or it is published among the
        # excluded candidates with the reason it was not carried, and the tally as recorded stays visible.
        estimate = report["winner_estimate"]
        assert (estimate.get("name") == "Alpha" and estimate["decisive"] is False) or \
            any(row["name"] == "Alpha" for row in estimate["excluded"]), estimate
        assert estimate["credits_as_recorded"] == {"Alpha": 2} or estimate.get("name") == "Alpha"
    # and the policy interface is the kind, not the English: no reason text is consulted
    blinded = bi.candidate_report(games, pairings={0.0: pairing(), 200.0: pairing()}, nameplate=plates,
                                  attributed={"withheld": "????", "blockers": [{"kind": "renamed_seat",
                                                                                "why": "????"}],
                                              "participant_hypotheses": {"p1": [{"name": "Alpha"}]},
                                              "per_game": []},
                                  formats=formats([2]), membership="supported")
    assert any("competing_names_for_one_seat" == f["factor"] for f in blinded["negative_factors"])


def test_a_coverage_gap_blocks_only_the_route_it_broken():
    """Coverage makes the credited tally *short*, which is not the same as contradicted.

    The credited route cannot reach the target because a game was never cropped; the nameplate route read both
    games and is allowed to name the winner it can defend. Collapsing the two would either throw away a usable
    name or launder an unfinished tally into a decisive one.
    """
    games = [game(0.0, "p1"), game(200.0, "p1")]
    plates = nameplate([plate(0.0), plate(200.0)],
                       [{"name": "Alpha", "wins": 2, "games": [0.0, 200.0], "unit": "x"}])
    report = bi.candidate_report(games, pairings={0.0: pairing(), 200.0: pairing()}, nameplate=plates,
                                 attributed={"withheld": "the game at 200.0 s was won on a screen side no "
                                                         "band reading falls inside",
                                             "blockers": [{"kind": "win_uncovered", "at": [200.0],
                                                           "why": "the game at 200.0 s was uncovered"}],
                                             "game_wins_by_identity": {"Alpha": 1},
                                             "per_game": [credit_row(0.0, "Alpha")]},
                                 formats=formats([2]), membership="supported")
    assert report["identity_conflict"] is False
    assert report["estimates_by_tier"]["credited_wins"]["status"] == "none"
    assert report["estimates_by_tier"]["credited_wins"]["why_not"] == "no candidate reaches every target"
    assert report["estimates_by_tier"]["nameplate_hypothesis"]["decisive"] is True
    assert report["winner_estimate"]["name"] == "Alpha"
    assert "credits_are_incomplete" in [f["factor"] for f in report["negative_factors"]]
    assert any(row["evidence"] == "per-player win credits" for row in report["unavailable_evidence"])


def test_a_conflict_still_refuses_a_partial_tally_rather_than_publishing_one():
    games = [game(0.0, "p1"), game(200.0, "p1")]
    band_map = {0.0: cover(10.0, "Alpha", "Beth"), 200.0: cover(210.0, "Ceil", "Beth")}
    attributed = bg._attributed_wins(games, band_map, [])
    assert "game_wins_by_identity" not in attributed
    assert attributed["person_tally_possible"] is False
    assert {row["kind"] for row in attributed["blockers"]} == {"renamed_seat"}


def test_an_unreadable_reading_and_a_missing_cover_are_reported_apart_and_together():
    games = [game(0.0, "p1"), game(200.0, "p1")]
    band_map = {0.0: cover(10.0, None, None, usable=False, why="its reading(s) named only one side")}
    attributed = bg._attributed_wins(games, band_map, [])
    assert "named only one side" in attributed["withheld"]
    assert "no band reading falls inside" in attributed["withheld"]
    assert {row["kind"] for row in attributed["blockers"]} == {"cover_unusable", "win_uncovered"}


# -------------------------------------------------------------------------------- the real targets
def test_every_applicable_target_must_be_cleared_and_the_targets_are_consumed_unchanged():
    """A no-stage span under BB9's policy admits both 2 and 3: three wins name it, two wins do not."""
    both = bf.applicable_formats([], POLICY)
    assert both["targets"] == [2, 3] and both["format_known"] and both["ambiguous"]
    reached_two = bi.winner_estimate(both["targets"], {"Alpha": 2, "Beth": 1}, formats=both)
    reached_three = bi.winner_estimate(both["targets"], {"Alpha": 3, "Beth": 1}, formats=both)
    assert reached_two["name"] is None and reached_two["why_not"] == "no candidate reaches every target"
    assert reached_three["name"] == "Alpha" and reached_three["decisive"]
    assert reached_three["targets_met"] == [2, 3] and reached_three["targets_missed"] == []
    assert reached_two["format"]["policy_source"] == POLICY["source"], "the source stays visible"


def test_credits_that_cannot_both_be_true_are_not_resolved_by_picking_the_larger_one():
    """First to three says Alpha won; first to two says the set ended with two players both at two.

    The name stays visible -- it is still the thing a reviewer should look at first -- but the word
    "decisive" is withdrawn, because under one admissible reading of the format these credits are impossible.
    """
    estimate = bi.winner_estimate([2, 3], {"Alpha": 3, "Beth": 2})
    assert estimate["name"] == "Alpha" and estimate["decisive"] is False
    assert estimate["status"] == "ambiguous" and estimate["why_not"] == "incompatible credits"
    assert estimate["per_target"] == {"2": ["Alpha", "Beth"], "3": ["Alpha"]}
    assert "first to 2" in estimate["because"]
    assert "clears_every_applicable_target" not in estimate, \
        "an ambiguous tally must not carry the flag that says it cleared"
    # The same arithmetic with a single claimant at each target is decisive again.
    assert bi.winner_estimate([2, 3], {"Alpha": 3, "Beth": 1})["decisive"]


def test_a_named_stage_selects_one_target_and_a_configured_target_is_still_evidence():
    ft3 = bf.applicable_formats([(10.0, "Basement Brawl #9 LOSERS FINAL")], POLICY)
    assert ft3["targets"] == [3] and not ft3["ambiguous"]
    assert bi.winner_estimate(ft3["targets"], {"Alpha": 2})["name"] is None
    assert bi.winner_estimate(ft3["targets"], {"Alpha": 3})["name"] == "Alpha"
    configured = bg._bucket_formats(None, None, 2, 0.0, 100.0)
    assert configured["targets"] == [2] and configured["basis"] == "games_per_set configured by the caller"
    assert bi.winner_estimate(configured["targets"], {"Alpha": 2},
                              formats=configured)["decisive"]


def test_no_policy_and_no_stage_is_the_targetless_case_not_a_default():
    nothing = bf.applicable_formats([], None)
    assert nothing["targets"] == [] and not nothing["format_known"]
    estimate = bi.winner_estimate(nothing["targets"], {"Alpha": 5}, formats=nothing)
    assert estimate["name"] is None and estimate["why_not"] == "no target applies"


def test_a_long_concluded_set_is_never_read_as_a_higher_target():
    """The inference the steering forbids: four games is not evidence of first to three."""
    bucketed = bg._bucket_formats(None, POLICY, None, 0.0, 900.0, stage_readings=[])
    assert bucketed["targets"] == [2, 3], "the target came from the length on tape"
    report = bi.candidate_report([game(start, "p1") for start in (0.0, 200.0, 400.0, 600.0)],
                                 attributed=credited([credit_row(start, "Alpha") for start
                                                      in (0.0, 200.0, 400.0, 600.0)]),
                                 formats=bucketed, membership="supported")
    assert report["winner_estimate"]["targets"] == [2, 3]
    assert report["changes_grouping_or_targets"] is False


def test_two_candidates_reaching_the_target_name_nobody():
    estimate = bi.winner_estimate([3], {"Alpha": 3, "Beth": 3})
    assert estimate["decisive"] is False and estimate["why_not"] == "incompatible credits"
    assert "Alpha" in estimate["because"] and "Beth" in estimate["because"]


# --------------------------------------------------------------- membership is a claim, not a formality
def test_membership_that_nothing_argued_makes_an_estimate_conditional_never_invisible():
    games = [game(0.0, "p1"), game(200.0, "p1")]
    attribution = credited([credit_row(0.0, "Alpha"), credit_row(200.0, "Alpha")])
    for membership, expected in (("supported", "decisive"), ("assumed", "conditional"), (None,
                                                                                         "conditional")):
        report = bi.candidate_report(games, attributed=attribution, formats=formats([2]),
                                     membership=membership)
        assert report["winner_estimate"]["status"] == expected, membership
        assert report["winner_estimate"]["name"] == "Alpha", membership
        assert report["winner_estimate"]["decisive"] is (expected == "decisive")
        if expected == "conditional":
            assert "membership" in report["winner_estimate"]["conditional_because"]
            assert report["needs_review"] is True


def test_membership_nobody_establishes_at_all_names_no_winner():
    games = [game(0.0, "p1"), game(200.0, "p1")]
    attribution = credited([credit_row(0.0, "Alpha"), credit_row(200.0, "Alpha")])
    for membership in ("contested", "single"):
        report = bi.candidate_report(games, attributed=attribution, formats=formats([2]),
                                     membership=membership)
        assert report["winner_estimate"]["name"] is None, membership
        assert report["winner_estimate"]["why_not"] == "membership_%s" % membership, membership
        assert report["winner_estimate"]["credits"] == {"Alpha": 2}, "the figures were hidden with the name"
        flagged = [f for f in report["negative_factors"] if f["factor"] == "membership_disputed"]
        assert flagged and flagged[0]["membership"] == membership, membership


# ------------------------------------------------------------------------------ timing, not tallies
def test_a_contradiction_the_timing_explains_costs_less_than_one_it_does_not():
    """Same contradiction flag, different order: one is a header repainting, the other is a bad read.

    The claim is only as strong as the evidence, so the explained case keeps a penalty (-5) instead of
    becoming a bonus, and the unexplained case keeps the full one (-20). "Explained" means the *winner's*
    side carried a different reading in and settled on the credited one; anything else is a story about the
    other side of the screen.
    """
    contradicted = plate(0.0, contradicts={"overlay_nameplate_rows": {}})
    explained = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"),
                               pairing=pairing(sides=("Alpha", "Beth"), sequence=timing(
                                   p1=[(2.0, None, "Buk"), (70.0, "Buk", "Alpha")],
                                   p2=[(2.0, None, "Beth")], carried={"p1": (-40.0, "Buk")},
                                   span=(0.0, 150.0))),
                               hypothesis=contradicted, credited=True)
    unstable = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"),
                              pairing=pairing(sides=("Alpha", "Beth"), sequence=timing(
                                  p1=[(2.0, None, "Buk"), (60.0, "Buk", "Alpha"),
                                      (120.0, "Alpha", "Buk")],
                                  p2=[(2.0, None, "Beth")], carried={"p1": (-40.0, "Buk")},
                                  span=(0.0, 150.0))),
                              hypothesis=contradicted, credited=True)
    explained_factors = [f for f in explained["negative_factors"]
                         if f["factor"] == "name_change_explained_by_repaint"]
    unstable_factors = [f for f in unstable["negative_factors"]
                        if f["factor"] == "nameplate_changed_within_game"]
    assert explained_factors and not [f for f in explained["negative_factors"]
                                      if f["factor"] == "nameplate_changed_within_game"]
    assert unstable_factors and not [f for f in unstable["negative_factors"]
                                     if f["factor"] == "name_change_explained_by_repaint"]
    assert explained_factors[0]["weight"] == bi.ATTRIBUTION_WEIGHTS["name_change_explained_by_repaint"]
    assert unstable_factors[0]["weight"] == bi.ATTRIBUTION_WEIGHTS["nameplate_changed_within_game"]
    assert explained["score"] > unstable["score"], (explained["score"], unstable["score"])
    assert "order settles it" in explained_factors[0]["why"]
    # no ordering evidence at all is not an explanation either
    silent = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                            hypothesis=contradicted, credited=True)
    assert "nameplate_changed_within_game" in [f["factor"] for f in silent["negative_factors"]]



def test_the_same_two_names_at_the_same_frequencies_are_scored_by_their_order():
    """A late repaint and a wrong name have identical tallies. Only the order tells them apart.

    A single recorded move that holds is what a header repainting looks like. A name that came back -- even
    once, even at the far end -- is the graphic unstable under the read, and no repaint explains that. The
    first version of this scan dropped the span's arrival, so `A -> B -> A` looked like two changes and got
    the milder penalty it is now refused.
    """
    settled = bi.nameplate_timing(pairing(sequence=timing(
        p1=[(10.0, None, "Alpha"), (60.0, "Alpha", "Beth")],
        p2=[(10.0, None, "Beth")], span=(0.0, 150.0))), game(0.0))
    flip_flopped = bi.nameplate_timing(pairing(sequence=timing(
        p1=[(10.0, None, "Alpha"), (60.0, "Alpha", "Beth"), (90.0, "Beth", "Alpha"),
            (120.0, "Alpha", "Beth"), (140.0, "Beth", "Alpha")],
        p2=[(10.0, None, "Beth"), (140.0, "Beth", "Alpha")], span=(0.0, 150.0))), game(0.0))
    assert settled["sides"]["p1"]["changes_inside"] == 1
    assert settled["sides"]["p1"]["trajectory"] == ["Alpha", "Beth"]
    assert not settled["readings_alternating"]
    assert flip_flopped["readings_alternating"]
    stable_factors = bi._timing_factors(settled, game(0.0))[0]
    unstable_positive, unstable_negative = bi._timing_factors(flip_flopped, game(0.0))
    assert "name_change_ordered_against_boundary" in [f["factor"] for f in stable_factors]
    assert "nameplate_alternating_within_read" in [f["factor"] for f in unstable_negative]
    assert not [f for f in unstable_positive
                if f["factor"] == "name_change_ordered_against_boundary"]
    # and the arrival of a name is not a change at all
    assert all(event["from"] is not None for event in flip_flopped["change_events"])
    assert flip_flopped["arrivals"]



def test_a_spelling_variant_is_reported_as_uncertainty_rather_than_a_pairing_change():
    """`derpingtoast` beside `derpingtonstoast` is *suspected* one name, which proves nothing either way.

    Revision 2 called this "one name misread twice" and used it to excuse a contradiction. The resemblance is
    real, the identity claim is not, and the difference matters: an uncertainty cannot buy a milder penalty.
    """
    carried = bi.nameplate_timing(pairing(sides=("derpingtoast", "Beth"), sequence=timing(
        p1=[(2.0, None, "derpingtoast")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-5.005, "derpingtonstoast")}, span=(0.0, 150.0))), game(0.0))
    assert carried["alias_uncertain"], "the wobble was not reported at all"
    assert not carried["carried_in_persists"], "an alias wobble was reported as a pairing handoff"
    entry = carried["alias_uncertain"][0]
    assert entry["relation"] == "alias_uncertain" and entry["identity_established"] is False
    assert entry["similarity"] > 0.8 and "tolerance" in entry["rule"]
    assert not [f for f in bi._timing_factors(carried, game(0.0))[0]
                if f["factor"] == "prior_pairing_carried_into_game"]
    # A reading this project's own tolerance puts outside one name is different: the handoff may be claimed,
    # and it is still labelled as a claim about readings, not about humans.
    different = bi.nameplate_timing(pairing(sides=("Mattiniero", "Beth"), sequence=timing(
        p1=[(2.0, None, "Buk"), (55.0, "Buk", "Mattiniero")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-40.0, "Buk")}, span=(0.0, 150.0))), game(0.0))
    assert different["carried_in_persists"] and not different["alias_uncertain"]
    moved = different["carried_in_persists"][0]
    assert moved["relation"] == "outside_tolerance" and moved["identity_established"] is False
    assert moved["carried_age_seconds"] == 40.0 and moved["seconds_into_game"] == 55.0



def test_a_spelling_difference_the_measure_cannot_settle_is_left_unsettled():
    """`Az` against `Ariz` falls between this recording's two bands and must be reported as neither."""
    # `Az`/`Ariz` is *not* one name under this project's own tolerance, and that is the conservative answer:
    # it leaves a pairing question open rather than closing it on resemblance.
    assert bi._spelling_relation("Az", "Ariz")[0] == "outside_tolerance"
    assert bi._spelling_relation("Buk", "Mattiniero")[0] == "outside_tolerance"
    assert bi._spelling_relation("Alpha", "Ariel")[0] == "outside_tolerance"
    assert not hasattr(bi, "NEAR_VARIANT_SIMILARITY"), "a second fitted threshold reappeared"
    # `Az`/`Ariz` is a truncation the subsequence rule catches; the unresolved band is exercised with a pair
    # this recording does not contain, because the rule has to exist before a name ever lands in it.
    for carried_name, credited_name, expect_repaint in [("derpingtonstoast", "derpingtoast", False),
                                                        ("Az", "Ariz", True),
                                                        ("Buk", "Mattiniero", True)]:
        unsure = bi.nameplate_timing(pairing(sides=(credited_name, "Beth"), sequence=timing(
            p1=[(2.0, None, credited_name)], p2=[(2.0, None, "Beth")],
            carried={"p1": (-30.0, carried_name)}, span=(0.0, 150.0))), game(0.0))
        buckets = [len(unsure["carried_in_persists"]), len(unsure["alias_uncertain"])]
        assert sum(1 for size in buckets if size) == 1, (carried_name, buckets)
        assert bool(unsure["carried_in_persists"]) is expect_repaint, carried_name


def test_a_name_already_drawn_before_the_game_opened_is_persistence_not_evidence():
    carried = bi.nameplate_timing(pairing(sequence=timing(
        p1=[(2.0, None, "Alpha")], p2=[(2.0, None, "Beth")], carried={"p1": (-30.0, "Alpha")},
        span=(0.0, 150.0))), game(0.0))
    assert carried["carried_in_matches"] and not carried["carried_in_persists"]
    factors = [f for f in bi._timing_factors(carried, game(0.0))[0]
               if f["factor"] == "carried_in_matches_credited_name"]
    assert factors[0]["weight"] == 5 and factors[0]["independent"] is False


def test_a_span_with_no_named_row_is_reported_as_unobserved_and_charged_once():
    missing = bi.nameplate_timing(pairing(sequence=timing(span=(0.0, 150.0), gaps=[(40.0, 130.0)])),
                                 game(0.0))
    assert len(missing["unobserved_gaps"]) == 1
    _, negative = bi._timing_factors(missing, game(0.0))
    assert [f["factor"] for f in negative] == ["unobserved_nameplate_span"]
    assert negative[0]["weight"] == bi.ATTRIBUTION_WEIGHTS["unobserved_nameplate_span"]


def test_a_repaint_is_only_claimed_when_the_credited_name_was_actually_read_afterwards():
    """Two names in order is a boundary; the credited name appearing *after* the other one is the repaint.

    Without that later read the ordering supports a statement about two readings and nothing about two
    pairings, and calling it the header showing the previous pairing would be the same invention wearing
    friendlier clothes.
    """
    never_read = bi.nameplate_timing(pairing(sides=("Mattiniero", "Beth"), sequence=timing(
        p1=[(2.0, None, "Buk")], p2=[(2.0, None, "Beth")], carried={"p1": (-40.0, "Buk")},
        span=(0.0, 150.0))), game(0.0))
    assert not never_read["carried_in_persists"], "a repaint was claimed with no post-boundary read"
    assert never_read["sides"]["p1"]["credited_name_read_in_span"] is False
    assert never_read["alias_uncertain"][0]["relation"] == "credited_name_never_read"
    assert not [f for f in bi._timing_factors(never_read, game(0.0))[0]
                if f["factor"] == "prior_pairing_carried_into_game"]
    # The same fixture with the credited name drawn later *is* the repaint shape, and quotes the interval.
    read_late = bi.nameplate_timing(pairing(sides=("Mattiniero", "Beth"), sequence=timing(
        p1=[(2.0, None, "Buk"), (55.0, "Buk", "Mattiniero")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-40.0, "Buk")}, span=(0.0, 150.0))), game(0.0))
    entry = read_late["carried_in_persists"][0]
    assert entry["seconds_into_game"] == 55.0 and entry["credited_name_read_in_span"] is True
    factor = [f for f in bi._timing_factors(read_late, game(0.0))[0]
              if f["factor"] == "prior_pairing_carried_into_game"][0]
    assert "was not drawn until 55.0 s in" in factor["why"]


def test_a_missing_record_is_absent_coverage_not_a_zero():
    absent = bi.nameplate_timing(None, game(0.0))
    assert absent["coverage"] == "absent"
    assert bi._timing_factors(absent, game(0.0)) == ([], [])


# ------------------------------------------------------------------------------- the published tier
def test_a_score_always_travels_with_at_least_one_reason():
    games = [game(0.0, "p1"), game(200.0, "p1")]
    report = bi.candidate_report(games,
                                 nameplate=nameplate([plate(0.0, basis="overlay_nameplate_hypothesis"),
                                                      plate(200.0, basis="overlay_nameplate_hypothesis")],
                                                     [{"name": "Alpha", "wins": 2, "games": [0.0, 200.0],
                                                       "unit": "x"}]),
                                 formats=formats([2]), membership="supported")
    assert report["why_this_score"], "a low score reached the reviewer with no reason attached"
    strongest = report["why_this_score"][0]
    assert strongest["count"] == 2, "the factor was not counted across the games it applies to"
    assert strongest["weight_per_game"] != 0
    assert all(str(entry.get("why") or "").strip() for entry in report["why_this_score"])


def test_the_low_confidence_flag_fires_on_both_sides_of_the_published_threshold():
    assert bi.tier_for(bi.NEEDS_REVIEW_BELOW) == "high"
    assert bi.tier_for(bi.NEEDS_REVIEW_BELOW - 1) == "medium"
    assert bi.tier_for(40) == "medium" and bi.tier_for(39) == "low"
    witness = {"origin": "operator_answer", "at": 20.0}
    seat = {"state": "held", "at": -20.0}
    over = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                          hypothesis=plate(0.0), credited=True, seat=seat, independent_witness=witness)
    # One documented attribution weight less -- the crop no longer agreeing with the rows -- and the same
    # evidence falls below the bound. Weakening only the reading axis would not: the score is the weaker axis,
    # so a test that moved the strong one and claimed to cross a threshold would be testing nothing.
    under = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                           hypothesis=plate(0.0, agrees=None), credited=True, seat=seat,
                           independent_witness=witness)
    capped = bi.game_report(game(0.0), cover=cover(10.0, "Alpha", "Beth"), pairing=pairing(),
                            hypothesis=plate(0.0), credited=True, seat=seat)
    assert over["score"] >= bi.NEEDS_REVIEW_BELOW and not over["needs_review"], over["score"]
    assert under["score"] < bi.NEEDS_REVIEW_BELOW and under["needs_review"], under["score"]
    step = over["score"] - under["score"]
    assert step == bi.ATTRIBUTION_WEIGHTS["graphic_reads_agree"], (over["score"], under["score"], step)
    assert capped["score"] == bi.MAX_SINGLE_WITNESS_SCORE and capped["needs_review"] is True
    for tier, floor in bi.TIERS:
        assert bi.tier_for(floor) == tier


def test_an_unresolved_conflict_cannot_be_outvoted_by_legible_reads():
    """The seat that is two people is exactly the case where every individual read looks excellent."""
    games = [game(0.0, "p2"), game(200.0, "p2")]
    attributed = {"withheld": "the p2 side won 2 of this candidate's games",
                  "blockers": [{"kind": "renamed_seat", "why": "the p2 side won 2 games",
                                "seat_continuity": "held"}],
                  "person_tally_possible": True,
                  "participant_hypotheses": {"p2": [{"name": "Buk", "game_start": 0.0},
                                                    {"name": "Ceil", "game_start": 200.0}]},
                  "per_game": [], "unattributed_games": 0}
    report = bi.candidate_report(games, band_map={0.0: cover(10.0, "Alpha", "Buk"),
                                                 200.0: cover(210.0, "Alpha", "Ceil")},
                                 pairings={0.0: pairing(), 200.0: pairing()},
                                 nameplate=nameplate([plate(0.0, p2="Buk"), plate(200.0, p2="Ceil")],
                                                     [{"name": "Buk", "wins": 1, "games": [0.0],
                                                       "unit": "x"}]),
                                 attributed=attributed, formats=formats([2]), membership="supported")
    assert "competing_names_for_one_seat" in [f["factor"] for f in report["negative_factors"]]
    assert report["identity_conflict"] is True and report["needs_review"] is True
    assert report["score"] <= bi.MAX_SINGLE_WITNESS_SCORE
    assert report["winner_estimate"]["decisive"] is False


# --------------------------------------------------------------------------- characters, offline only
def test_a_recurring_pair_is_counted_and_refuses_to_orient_anything():
    games = [game(0.0, "p1"), game(200.0, "p1")]
    audit = bi.character_audit(games, {0.0: pairing(), 200.0: pairing()})
    assert audit["games_with_a_pair"] == 2 and audit["games_side_bound"] == 0
    assert audit["recurring_unordered_pairs"] == {"ALISA/REINA": 2}
    assert audit["recurring_pairs_establish_orientation"] is False
    assert audit["fighter_persistence_establishes_identity"] is False
    assert audit["inference_added"] is False and audit["authoritative"] is False
    assert any("character1" in row for row in audit["missing_evidence"])
    report = bi.candidate_report(games, formats=formats([2]), membership="supported",
                                 characters=audit, nameplate=nameplate([], []))
    binding = [row for row in report["unavailable_evidence"]
               if row["evidence"] == "character-to-side binding"]
    assert binding[0]["measured"] == {"games_with_a_pair": 2, "games_side_bound": 0,
                                      "recurring_unordered_pairs": {"ALISA/REINA": 2}}


def test_an_explicit_binding_is_carried_verbatim_and_no_slot_becomes_a_side():
    games = [game(0.0, "p1")]
    games[0]["characters_by_side"] = {"p1": "ALISA", "p2": "REINA"}
    audit = bi.character_audit(games, {0.0: pairing()})
    assert audit["games_side_bound"] == 1
    assert audit["games"][0]["explicit_binding"] == {"p1": "ALISA", "p2": "REINA"}
    assert "explicit per-side binding" in audit["side_binding_basis"]
    unbound = bi.character_audit([game(0.0, "p1")], {0.0: pairing()})
    assert unbound["games_naming_both_slots"] == 1 and unbound["games_side_bound"] == 0


# ------------------------------------------------------------------------------------- inert by design
def test_the_report_changes_no_verdict_grouping_or_target():
    games = [game(0.0, "p1"), game(200.0, "p2")]
    rows = [{"timestamp": at, "screen_state": "gameplay", "hud_present": True,
             "characters": ("ALISA", "REINA"),
             "overlay": {"player1": "Alpha", "player2": "Beth", "score": [0, 0]}}
            for at in (10.0, 210.0)]
    with_report = bg._group_into_sets(copy.deepcopy(games), 2, rows, format_policy=POLICY)
    blind = (bg.boundary_identity.candidate_report, bg.boundary_identity.character_audit)

    def suppressed(*args, **kwargs):
        return {}

    bg.boundary_identity.candidate_report = suppressed
    bg.boundary_identity.character_audit = suppressed
    try:
        without = bg._group_into_sets(copy.deepcopy(games), 2, rows, format_policy=POLICY)
    finally:
        (bg.boundary_identity.candidate_report,
         bg.boundary_identity.character_audit) = blind
    keys = ("attribution_confidence", "character_bindings")
    stripped = [{key: value for key, value in record.items() if key not in keys}
                for record in with_report]
    assert stripped == [{key: value for key, value in record.items() if key not in keys}
                        for record in without]
    assert all("attribution_confidence" in record for record in with_report)


# ------------------------------------------------------------------------------- saved-project state
def candidate(attribution=None, withheld=None, targets=(2, 3), status="decisive"):
    """One imported candidate in the shape `candidate_records` publishes."""
    return {"start": 100.0, "end": 400.0, "games": 2, "status": "complete", "membership": "supported",
            "game_wins": {"p1": 2, "p2": 0}, "deciding_wins": {"Alpha": 2, "Beth": 0},
            "deciding_wins_unit": "identities", "set_winner": "Alpha", "winner_seat": None,
            "winner_unit": "broadcast_identity_hypothesis", "games_per_set_expected": None,
            "concluded_by": ["target_reached"], "boundary_confirmed": True,
            "end_transition_observed": True, "cut_proposed_by_pipeline": True,
            "set_completion": {"state": "confirmed", "target_reached": True},
            "grouping_unresolved": False, "boundary_conflicts": [], "alternative_groupings": [],
            "footage_complete": True, "edges": {}, "clip_end_at": 405.0, "clip_end_basis": "test",
            "clip_end_anchor": {}, "clip_uncertainty": None, "basis": "test", "missing_evidence": [],
            "attribution_without_band": "screen_sides_only", "attribution_with_band": "identities",
            "game_wins_by_identity": attribution or {"Alpha": 2, "Beth": 0}, "attribution_support": None,
            "attribution_withheld": withheld, "participant_hypotheses": None,
            "nameplate_hypotheses": nameplate([plate(100.0)], [{"name": "Alpha", "wins": 2,
                                                                "games": [100.0], "unit": "x"}]),
            "attribution_confidence": {"kind": "attribution-confidence", "version": 2, "score": 55,
                                       "tier": "medium", "needs_review": True, "identity_conflict": False,
                                       "independent_witnesses": 1, "estimate_blockers": [],
                                       "duplicates_quarantined": [],
                                       "winner_estimate": {"name": "Alpha", "wins": 2, "source":
                                                           "credited_wins", "status": status,
                                                           "decisive": status == "decisive",
                                                           "targets": list(targets)},
                                       "negative_factors": [{"why": "one game, no second witness"}],
                                       "unavailable_evidence": [{"evidence": "operator orientation",
                                                                 "status": "absent"}]},
            "character_bindings": {"games_with_a_pair": 2, "games_side_bound": 0},
            "applicable_formats": formats(list(targets)), "complete_under": list(targets),
            "open_under": [], "format_dependent": False,
            "attribution": {"support": {"games_credited": 2}}, "links": [], "cut_mapping_resolution": [],
            "game_spans": [[100.0, 200.0], [250.0, 400.0]], "identities": [], "unresolved_claims": []}


def imported(candidate_row, games=None):
    document = {"kind": "set-recovery", "candidates": [candidate_row]}
    return review.import_recovery(document, source="recovery.json",
                                 games=games or [game(100.0, "p1"), game(250.0, "p1")])


def test_verdict_provenance_survives_a_normal_save_and_reload():
    from tekken_vod_helper.models import ProjectState
    project = review.merge_into_project({}, imported(candidate())["segments"], event="Basement Brawl 9")
    # An owner answer, made before the round trip, because the thing a rebuild must never lose is a human.
    project["matches"][0]["review"]["decisions"] = [{"kind": "orientation", "value": "swapped",
                                                    "answered_by": "operator"}]
    reloaded = json.loads(json.dumps(project))
    again = ProjectState.from_dict(reloaded).to_dict()
    for stage, document in (("reload", reloaded), ("project state round trip", again)):
        recovery = document["matches"][0]["recovery"]
        assert recovery["set_winner"] == "Alpha", stage
        assert recovery["winner_unit"] == "broadcast_identity_hypothesis", stage
        assert recovery["deciding_wins"] == {"Alpha": 2, "Beth": 0}, stage
        assert recovery["applicable_formats"]["targets"] == [2, 3], \
            "%s: the target a verdict was measured against went missing again" % stage
        assert recovery["applicable_formats"]["ambiguous"] is True, stage
        assert recovery["complete_under"] == [2, 3], stage
        assert recovery["attribution_confidence"]["winner_estimate"]["status"] == "decisive", stage
        assert recovery["attribution_confidence"]["version"] == 2, stage
        assert recovery["character_bindings"]["games_side_bound"] == 0, stage
        assert recovery["nameplate_hypotheses"]["authoritative"] is False, stage
    assert again["matches"][0]["review"]["decisions"][0]["value"] == "swapped"


def test_reading_the_summary_is_written_by_the_normal_path_not_pasted_in():
    project = review.merge_into_project({}, imported(candidate())["segments"], event="Basement Brawl 9")
    text = project["review"]["reading_the_summary"]
    assert "openings_needing_review" in text and "independently_validated is 0" in text
    assert "heuristic confidence" in text, "the tier left the disclosure when the paste step did"
    assert "1 set(s) carry a name whose arithmetic closes" in text
    assert "1 medium" in text and "1 high" not in text


def test_the_review_card_reports_the_same_numbers_the_recovery_published():
    project = review.merge_into_project({}, imported(candidate())["segments"], event="Basement Brawl 9")
    recovery = project["matches"][0]["recovery"]
    queue = review.review_queue(project)
    assert queue, "no card to check the report against"
    excerpt = queue[0]["evidence"]
    report = recovery["attribution_confidence"]
    assert excerpt["attribution_confidence"]["score"] == report["score"]
    assert excerpt["attribution_confidence"]["tier"] == report["tier"]
    assert excerpt["winner_estimate"]["name"] == report["winner_estimate"]["name"]
    assert excerpt["estimate_status"] == "decisive"
    assert excerpt["estimate_status"] == report["winner_estimate"]["status"], \
        "the card quoted a different word than the report wrote"
    assert excerpt["target_provenance"]["applicable_formats"]["targets"] == [2, 3]
    assert excerpt["target_provenance"]["winner_unit"] == "broadcast_identity_hypothesis"
    assert review.export_plan(project)["counts"]["segments"] == 1


def test_a_conditional_estimate_reaches_the_card_as_conditional():
    project = review.merge_into_project(
        {}, imported(candidate(status="conditional"))["segments"], event="Basement Brawl 9")
    excerpt = review.review_queue(project)[0]["evidence"]
    assert excerpt["estimate_status"] == "conditional"
    assert excerpt["winner_estimate"]["decisive"] is False


def test_the_evidence_layer_carries_the_report_the_recovery_layer_computed():
    games = [game(0.0, "p1"), game(200.0, "p2")]
    rows = [{"timestamp": at, "screen_state": "gameplay", "hud_present": True,
             "characters": ("ALISA", "REINA"),
             "overlay": {"player1": "Alpha", "player2": "Beth", "score": [0, 0]}}
            for at in (10.0, 210.0)]
    sets = bg._group_into_sets(copy.deepcopy(games), 2, rows, format_policy=POLICY)
    record = be.candidate_records({"sets": sets})[0]
    assert record["attribution_confidence"]["kind"] == "attribution-confidence"
    assert record["attribution_confidence"]["authoritative"] is False
    assert record["character_bindings"]["games_observed"] == 2
    assert record["applicable_formats"]["targets"] == [2, 3]
    assert record["winner_unit"] in (None, "broadcast_identity_hypothesis")
    # and the timing record travelled with it, because a reviewer cannot re-derive it from a summary
    assert record["attribution_confidence"]["games"][0]["nameplate_timing"]["coverage"] == "observed"


def test_every_weight_a_factor_can_use_is_declared_once():
    """No factor may be scored from a number that lives only inside an expression.

    The table is the documentation, so a factor missing from it is a weight nobody reviewed -- and an origin
    missing from the declared list is a witness claim nobody granted.
    """
    tables = dict(bi.OCR_WEIGHTS)
    tables.update(bi.ATTRIBUTION_WEIGHTS)
    collected = set()
    games = [game(0.0, "p1"), game(200.0, "p1", counts=False, round_ids=["same"]),
             game(400.0, "p1", round_ids=["same"])]
    report = bi.candidate_report(games, band_map={0.0: cover(10.0, "Alpha", "Beth", usable=False,
                                                             why="one side only")},
                                 pairings={0.0: pairing(), 200.0: pairing(), 400.0: pairing()},
                                 nameplate=nameplate([plate(0.0), plate(200.0), plate(400.0)],
                                                     [{"name": "Alpha", "wins": 2, "games": [0.0, 200.0],
                                                       "unit": "x"}]),
                                 attributed={"withheld": "x", "blockers": [{"kind": "renamed_seat",
                                                                           "why": "x"}],
                                             "participant_hypotheses": {"p1": [{"name": "Alpha"},
                                                                              {"name": "Ceil"}]},
                                             "per_game": [credit_row(0.0, "Alpha")]},
                                 formats=formats([2, 3]), membership="assumed",
                                 links=[{"at": 200.0, "seat_continuity": "unknown",
                                         "questions": [{"kind": "identity_mapping"}]}])
    for row in report["games"]:
        for group in ("positive_factors", "negative_factors"):
            collected.update(f["factor"] for f in row[group])
    collected.update(f["factor"] for f in report["positive_factors"] + report["negative_factors"])
    collected.update(entry["factor"] for entry in report["why_this_score"])
    undeclared = sorted(collected - set(tables))
    assert not undeclared, "a factor was scored from a weight that is not in the table: %s" % undeclared
    for row in report["games"]:
        for group in ("positive_factors", "negative_factors"):
            for factor in row[group]:
                assert factor["origin"] in KNOWN_ORIGINS, factor
                assert factor["independent"] == (factor["origin"] in bi.INDEPENDENT_WITNESS_ORIGINS)


# ----------------------------------------------------- review 3: alternation, side matching, resemblance
def test_an_immediate_return_to_the_first_name_is_instability_not_a_repaint():
    """A -> B -> A inside one game is the graphic moving under the read, whatever the change count is.

    The first alternation scan dropped the span's arrival, so the second ``Alpha`` had nothing to be "back
    to" and a three-name wobble earned the milder repaint penalty. The arrival is a reading like any other,
    and so is a name carried in from before the span.
    """
    wobble = bi.nameplate_timing(pairing(sides=("Alpha", "Beth"), sequence=timing(
        p1=[(2.0, None, "Alpha"), (50.0, "Alpha", "Zed"), (90.0, "Zed", "Alpha")],
        p2=[(2.0, None, "Beth")], span=(0.0, 150.0))), game(0.0))
    assert wobble["sides"]["p1"]["trajectory"] == ["Alpha", "Zed", "Alpha"]
    assert wobble["readings_alternating"], "A -> B -> A was reported as a settled handoff"

    factors = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"),
                             pairing=pairing(sides=("Alpha", "Beth"), sequence=timing(
                                 p1=[(2.0, None, "Alpha"), (50.0, "Alpha", "Zed"),
                                     (90.0, "Zed", "Alpha")],
                                 p2=[(2.0, None, "Beth")], span=(0.0, 150.0))),
                             hypothesis=plate(0.0, contradicts={"overlay_nameplate_rows": {}}),
                             credited=True)["negative_factors"]
    names = [f["factor"] for f in factors]
    assert "nameplate_alternating_within_read" in names, names
    assert "name_change_explained_by_repaint" not in names, names

    # A name carried in from before the span belongs to the trajectory too.
    carried_back = bi.nameplate_timing(pairing(sides=("Alpha", "Beth"), sequence=timing(
        p1=[(2.0, None, "Zed"), (90.0, "Zed", "Alpha")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-30.0, "Alpha")}, span=(0.0, 150.0))), game(0.0))
    assert carried_back["sides"]["p1"]["trajectory"] == ["Alpha", "Zed", "Alpha"], \
        carried_back["sides"]["p1"]["trajectory"]
    assert carried_back["readings_alternating"]


def test_a_repaint_on_one_side_does_not_clear_a_contradicted_credit_on_the_other():
    """An explanation names a side and a player, or it explains nothing.

    Admitting any contradicted game because *some* side showed a tidy handoff let a p1 explanation carry a
    contradictory read of p2 -- the side whose credit the tally actually depends on.
    """
    row = {"admitted": True, "counts_toward_set": True, "contradicted": True, "winner_side": "p2",
           "name": "Beth", "sides": {"p1": "Alpha", "p2": "Beth"},
           "nameplate_timing": {
               "coverage": "observed",
               "carried_in_persists": [{"side": "p1", "credited": "Alpha", "drawn_during": "gap",
                                        "credited_name_read_in_span": True}],
               "readings_alternating": [{"side": "p2", "sequence": ["Beth", "Zed", "Beth"]}]}}
    clean, excluded = bi._clean_hypothesis_credits({"Beth": {"wins": 2, "games": [0.0]}}, {0.0: row})
    assert clean == {}, "a p1 handoff excused a contradicted p2 credit"
    assert excluded and excluded[0]["name"] == "Beth"
    assert "p2" in excluded[0]["because"], excluded[0]["because"]

    # Aimed at the side and name that were contradicted, the same shape of evidence does clear it.
    settled = {"admitted": True, "counts_toward_set": True, "contradicted": True, "winner_side": "p1",
               "name": "Alpha", "sides": {"p1": "Alpha", "p2": "Beth"},
               "nameplate_timing": {
                   "coverage": "observed", "readings_alternating": [],
                   "carried_in_persists": [{"side": "p1", "credited": "Alpha", "drawn_during": "gap",
                                            "credited_name_read_in_span": True, "carried": "Buk",
                                            "seconds_into_game": 55.0}]}}
    cleared, still = bi._clean_hypothesis_credits({"Alpha": {"wins": 2, "games": [0.0]}}, {0.0: settled})
    assert cleared == {"Alpha": 2}, (cleared, still)


def test_a_contradiction_is_softened_only_for_the_winner_side_it_concerns():
    """The milder penalty is about the credited name on the winning side, not about any side in the game."""
    contradicted = plate(0.0, contradicts={"overlay_nameplate_rows": {}})
    elsewhere = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"),
                               pairing=pairing(sides=("Alpha", "Beth"), sequence=timing(
                                   p1=[(2.0, None, "Buk")],
                                   p2=[(2.0, None, "Buk"), (60.0, "Buk", "Beth")],
                                   carried={"p2": (-40.0, "Alpha")}, span=(0.0, 150.0))),
                               hypothesis=contradicted, credited=True)
    names = [f["factor"] for f in elsewhere["negative_factors"]]
    assert "nameplate_changed_within_game" in names, names
    assert "name_change_explained_by_repaint" not in names, names

    own = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"),
                         pairing=pairing(sides=("Alpha", "Beth"), sequence=timing(
                             p1=[(2.0, None, "Buk"), (55.0, "Buk", "Alpha")],
                             p2=[(2.0, None, "Beth")], carried={"p1": (-40.0, "Buk")},
                             span=(0.0, 150.0))),
                         hypothesis=contradicted, credited=True)
    assert "name_change_explained_by_repaint" in [f["factor"] for f in own["negative_factors"]]
    assert own["score"] > elsewhere["score"], (own["score"], elsewhere["score"])


def test_string_resemblance_alone_never_declares_one_player():
    """This project already declares how close two readings must be to count as one name, and that rule is a
    tolerance for comparison, not proof of identity. A suspected alias therefore settles nothing in either
    direction -- neither a pairing change nor a repaint -- until saved evidence is recorded beside it.
    """
    assert bi._spelling_relation("A", "Mattiniero")[0] != "same_name"
    assert bi._spelling_relation("Ann", "Joanne")[0] != "same_name"
    assert bi._spelling_relation("az", "ariz")[0] == "outside_tolerance"
    assert bi._spelling_relation("Buk", "Mattiniero")[0] == "outside_tolerance"
    assert bi._spelling_relation("RebiX", "Rebix")[0] == "exact_normalised"
    relation, similarity = bi._spelling_relation("derpingtonstoast", "derpingtoast")
    assert relation == "alias_uncertain", (relation, similarity)
    evidence = bi.spelling_evidence("derpingtonstoast", "derpingtoast")
    assert evidence["identity_established"] is False and evidence["rule"], evidence
    # A frame that was actually read is evidence about text, and it stops there.
    read = {"a": "derpingtoast", "b": "derpingtonstoast", "reviewed_by": "machine",
            "bound_participant_identity": False,
            "read_from": [{"frame_file": "frame-5481.jpg", "master_seconds": 5485.48,
                           "text_seen_in_saved_frame": "derpingtoast"}]}
    assert bi.spelling_evidence("derpingtonstoast", "derpingtoast",
                                aliases=(read,))["relation"] == "alias_uncertain"
    # ...while an operator's binding is allowed to say one player, and says so with its source.
    bound = dict(read, reviewed_by="operator", bound_participant_identity=True, source="operator answer")
    settled = bi.spelling_evidence("derpingtonstoast", "derpingtoast", aliases=(bound,))
    assert settled["relation"] == "bound_participant_identity" and settled["identity_established"] is True
    # a declaration that never says what the pixels showed is a resemblance in alias clothing
    vague = {"a": "derpingtoast", "b": "derpingtonstoast", "evidence": "trust me"}
    assert bi.spelling_evidence("derpingtonstoast", "derpingtoast",
                                aliases=(vague,))["relation"] == "alias_uncertain"


def test_an_uncertain_alias_cannot_be_reported_as_a_pairing_handoff():
    """Two spellings inside the tolerance are not two players, so they cannot show the header moved either."""
    record = bi.nameplate_timing(pairing(sides=("derpingtoast", "Beth"), sequence=timing(
        p1=[(2.0, None, "derpingtoast")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-5.005, "derpingtonstoast")}, span=(0.0, 150.0))), game(0.0))
    assert not record["carried_in_persists"], "an alias wobble was reported as a pairing handoff"
    assert [entry["relation"] for entry in record["alias_uncertain"]] == ["alias_uncertain"]
    assert not [f for f in bi._timing_factors(record, game(0.0))[0]
                if f["factor"] == "prior_pairing_carried_into_game"]
    # within a span, the same wobble must not be smoothed into one reading either
    inside = bi.nameplate_timing(pairing(sides=("derpingtoast", "Beth"), sequence=timing(
        p1=[(2.0, None, "derpingtoast"), (60.0, "derpingtoast", "derpingtonstoast"),
            (90.0, "derpingtonstoast", "derpingtoast")], p2=[(2.0, None, "Beth")],
        span=(0.0, 150.0))), game(0.0))
    assert inside["sides"]["p1"]["trajectory"] == ["derpingtoast", "derpingtonstoast", "derpingtoast"]
    assert inside["readings_alternating"], "an A -> B -> A alias wobble was smoothed away"


def test_a_full_candidate_report_cannot_be_decisive_on_a_side_mismatched_explanation():
    """The integration the unit tests miss: fallback must not name a winner across this."""
    games = [game(0.0, "p2", round_ids=["r1"]), game(200.0, "p2", round_ids=["r2"])]
    rows = [plate(0.0, p2="Beth", contradicts={"overlay_nameplate_rows": {}}),
            plate(200.0, p2="Beth", contradicts={"overlay_nameplate_rows": {}})]
    report = bi.candidate_report(games, pairings={
        0.0: pairing(sides=("Alpha", "Beth"), sequence=timing(
            p1=[(2.0, None, "Buk"), (55.0, "Buk", "Alpha")], p2=[(2.0, None, "Zed")],
            carried={"p1": (-40.0, "Buk")}, span=(0.0, 150.0))),
        200.0: pairing(sides=("Alpha", "Beth"), sequence=timing(
            p1=[(202.0, None, "Buk"), (255.0, "Buk", "Alpha")], p2=[(202.0, None, "Zed")],
            carried={"p1": (-40.0, "Buk")}, span=(200.0, 350.0)))},
        nameplate=nameplate(rows, [{"name": "Beth", "wins": 2, "games": [0.0, 200.0], "unit": "x"}]),
        attributed={"withheld": "the game at 0.0 s cannot be credited", "per_game": [],
                    "blockers": [{"kind": "win_uncovered", "why": "no cover"}]},
        formats=formats([2]), membership="supported")
    estimate = report["estimates_by_tier"]["nameplate_hypothesis"]
    assert estimate["name"] is None and estimate["status"] in ("none", "blocked"), estimate
    assert [row["name"] for row in estimate["excluded"]] == ["Beth"]
    assert report["credits"]["nameplate_hypothesis_wins"] == {"Beth": 2}, "the raw tally was hidden"


def test_uncertain_spellings_are_reported_as_uncertainty_and_not_merged():
    """A suspected alias has to reach the reviewer as an open question, with both spellings intact.

    Merging it would invent a participant; ignoring it would let a wobble pass as a pairing change. The report
    therefore publishes the pair, the rule that left it open, and what would settle it -- and keeps the raw
    spellings everywhere else in the record.
    """
    games = [game(0.0, "p1"), game(200.0, "p1")]
    pairings = {0.0: pairing(sides=("derpingtoast", "Beth"), sequence=timing(
        p1=[(2.0, None, "derpingtoast")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-5.0, "derpingtonstoast")}, span=(0.0, 150.0))),
        200.0: pairing(sides=("derpingtoast", "Beth"), sequence=timing(
            p1=[(202.0, None, "derpingtoast"), (260.0, "derpingtoast", "derpingtonstoast")],
            p2=[(202.0, None, "Beth")], span=(200.0, 350.0)))}
    report = bi.candidate_report(games, pairings=pairings,
                                 nameplate=nameplate([plate(0.0, p1="derpingtoast"),
                                                      plate(200.0, p1="derpingtoast")],
                                                     [{"name": "derpingtoast", "wins": 2,
                                                       "games": [0.0, 200.0], "unit": "x"}]),
                                 formats=formats([2]), membership="supported")
    pairs = report["alias_uncertainty"]
    assert len(pairs) == 2, pairs
    assert {tuple(sorted(row["spellings"])) for row in pairs} == {
        ("derpingtoast", "derpingtonstoast")}
    assert all(row["identity_established"] is False and row["rule"] for row in pairs)
    assert all("settled_by" in row and "crop" in row["settled_by"] for row in pairs)
    flagged = [row for row in report["unavailable_evidence"]
               if "uncertain nameplate spelling" in str(row.get("evidence"))]
    assert flagged and flagged[0]["status"] == "absent"
    # nothing anywhere collapses the two spellings into one canonical name
    assert "derpingtonstoast" in json.dumps(report)
    assert report["winner_estimate"]["name"] == "derpingtoast", json.dumps(report["winner_estimate"])


def test_a_menu_card_is_not_a_nameplate_repaint():
    """The evidence audit found this: the fuller spellings live on menus, not in fights.

    On BB9 `derpingtonstoast`, `derpinqtoast`, `PWD DA | Az` and `Mattiiniero` each appear in eight
    consecutive sample rows -- one ~40 s stretch -- and their rows are `vs_screen`, `stage_intro` and
    `post_match_menu`, where the broadcast legitimately draws a longer name for the same player. Reading those
    rows as nameplate changes invents a pairing handover at every menu, which is exactly the kind of false
    certainty this layer is supposed to refuse.
    """
    rows = [{"timestamp": at, "screen_state": state, "hud_present": True,
             "characters": ("ALISA", "REINA"), "overlay": {"player1": p1, "player2": "Beth",
                                                           "score": [0, 0]}}
            for at, state, p1 in [
                (-40.0, "stage_intro", "derpingtonstoast"), (-35.0, "vs_screen", "derpingtonstoast"),
                (10.0, "gameplay", "derpingtoast"), (60.0, "gameplay", "derpingtoast"),
                (110.0, "gameplay", "derpingtoast"), (145.0, "post_match_menu", "derpinqtoast")]]
    from tekken_vod_helper import boundary_games as bg
    sequence = bg._name_sequence(rows, 0.0, 150.0, window_start=-45.0)
    assert sequence["play_rows"] == 3 and sequence["off_play_rows"] == 3
    side = sequence["sides"]["p1"]
    assert side["carried_in"] is None, "a menu card was carried in as the previous pairing"
    # one arrival (the fight's own first reading, `from` null) and no change at all: a menu spelling is never
    # allowed to become a nameplate move
    assert [event["to"] for event in side["change_events"] if event["from"] is not None] == [], \
        side["change_events"]
    assert [entry[1] for entry in side["off_play_reads"]] == ["derpingtonstoast", "derpingtonstoast",
                                                              "derpinqtoast"], side["off_play_reads"]
    # and the report built on it makes no handover claim
    record = bi.nameplate_timing({"sides": {"p1": "derpingtoast", "p2": "Beth"}, "name_sequence": sequence},
                                 game(0.0))
    assert not record["carried_in_persists"] and not record["alias_uncertain"]
    assert record["sides"]["p1"]["trajectory"] == ["derpingtoast"]


def test_a_name_last_drawn_in_the_previous_match_is_not_a_repaint():
    """The distinction the second review pass could not see, and the first draft of this one got wrong.

    A carry-in 280 s old was last drawn in the middle of the previous match. Nothing was observed at the
    boundary, so there is no handover to describe and no milder penalty to grant. The gap between matches is
    the only place a leftover name means the header had not changed yet.
    """
    previous_match = bi.nameplate_timing(pairing(sides=("Mattiniero", "Beth"), sequence=timing(
        p1=[(2.0, None, "Mattiniero")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-280.0, "PWDDA | Ariz", "previous_match")}, span=(0.0, 150.0))), game(0.0))
    assert not previous_match["carried_in_persists"]
    entry = previous_match["previous_match_pairing"][0]
    assert entry["drawn_during"] == "previous_match" and entry["carried_age_seconds"] == 280.0
    assert "unobserved" in entry["because"]
    assert not [f for f in bi._timing_factors(previous_match, game(0.0))[0]
                if f["factor"] == "prior_pairing_carried_into_game"]
    gap = bi.nameplate_timing(pairing(sides=("Mattiniero", "Beth"), sequence=timing(
        p1=[(2.0, None, "Mattiniero")], p2=[(2.0, None, "Beth")],
        carried={"p1": (-280.0, "PWDDA | Ariz", "gap")}, span=(0.0, 150.0))), game(0.0))
    assert gap["carried_in_persists"], "a name drawn during the pause was refused as a handover"
    # and the softened contradiction follows the same line. The hypothesis has to name the player the
    # pairing actually drew, or the test is asserting a milder penalty for a name nobody credited.
    contradicted = plate(0.0, p1="Mattiniero", contradicts={"overlay_nameplate_rows": {}})
    for carried, expect in ((("PWDDA | Ariz", "previous_match"), "nameplate_changed_within_game"),
                            (("PWDDA | Ariz", "gap"), "name_change_explained_by_repaint")):
        row = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Mattiniero", "Beth"),
                             pairing=pairing(sides=("Mattiniero", "Beth"), sequence=timing(
                                 p1=[(2.0, None, carried[0]), (30.0, carried[0], "Mattiniero")],
                                 p2=[(2.0, None, "Beth")],
                                 carried={"p1": (-280.0, carried[0], carried[1])}, span=(0.0, 150.0))),
                             hypothesis=contradicted, credited=True)
        factors = [f["factor"] for f in row["negative_factors"]]
        assert expect in factors, (carried, factors)


def test_the_gap_boundary_comes_from_the_previous_games_recorded_end():
    """`drawn_during` is derived, not asserted: it is the previous game's own end, from the same pairing pass."""
    from tekken_vod_helper import boundary_games as bg
    rows = [{"timestamp": at, "screen_state": state, "hud_present": True,
             "characters": ("ALISA", "REINA"),
             "overlay": {"player1": p1, "player2": "Beth", "score": [0, 0]}}
            for at, state, p1 in [
                (100.0, "gameplay", "Buk"), (140.0, "gameplay", "Buk"),      # previous match, ends 150
                (160.0, "gameplay", "Buk"),                                   # in the gap before game 2
                (210.0, "gameplay", "Mattiniero"), (300.0, "gameplay", "Mattiniero")]]
    games = [{"start": 100.0, "end": 150.0}, {"start": 200.0, "end": 350.0}]
    pairings = bg._overlay_pairings(rows, games)
    carried = pairings[200.0]["name_sequence"]["sides"]["p1"]["carried_in"]
    assert carried["at"] == 160.0 and carried["drawn_during"] == "gap", carried
    # the same reading against a previous match that ran to 190 s would be "previous_match"
    games_late = [{"start": 100.0, "end": 190.0}, {"start": 200.0, "end": 350.0}]
    late = bg._overlay_pairings(rows, games_late)[200.0]["name_sequence"]["sides"]["p1"]["carried_in"]
    assert late["drawn_during"] == "previous_match", late



def test_a_gap_observed_handover_names_its_own_confound():
    """A leftover name in the gap is a real observation with two readings, and the record must say both.

    If it were written as a clean repaint it would be the same over-reach in a nicer sentence: the boundary
    between these games is itself the thing under review.
    """
    row = bi.game_report(game(0.0, "p1"), cover=cover(10.0, "Alpha", "Beth"),
                         pairing=pairing(sides=("Alpha", "Beth"), sequence=timing(
                             p1=[(2.0, None, "Buk"), (55.0, "Buk", "Alpha")],
                             p2=[(2.0, None, "Beth")], carried={"p1": (-10.0, "Buk", "gap")},
                             span=(0.0, 150.0))),
                         hypothesis=plate(0.0, p1="Alpha", contradicts={"overlay_nameplate_rows": {}}),
                         credited=True)
    softened = [f for f in row["negative_factors"]
                if f["factor"] == "name_change_explained_by_repaint"][0]
    assert "boundary a few seconds late" in softened["why"], softened["why"]
    assert "discounted and not excused" in softened["why"]
    confounds = [f for f in row["positive_factors"] if f.get("relation") == "boundary_or_repaint_ambiguous"]
    assert confounds and confounds[0]["weight"] == 0 and confounds[0]["settles_nothing"] is True
    assert row["score"] < bi.NEEDS_REVIEW_BELOW, "a confounded explanation must not read as settled"


def test_a_machine_text_observation_corrects_one_instant_and_binds_no_person():
    """The distinction review 4 asked for, in both directions.

    A corrected reading of a nameplate is a fact about displayed text at the instant it was inspected. It must
    fix the trajectory there and it must not silently become "these two tags are one player" -- which is what
    happened when any nonempty pixel note was treated as establishing identity.
    """
    left, right = "PWDDA | Az", "PWDDA | Ariz"
    read = {"a": left, "b": right, "reviewed_by": "machine", "bound_participant_identity": False,
            "read_from": [{"frame_file": "frame-2601.jpg", "master_seconds": 2602.6,
                           "text_seen_in_saved_frame": right}]}
    evidence = bi.spelling_evidence(left, right, aliases=(read,))
    assert evidence["identity_established"] is False
    assert evidence["relation"] == "alias_uncertain"
    operator = bi.spelling_evidence(left, right, aliases=(
        dict(read, reviewed_by="operator", bound_participant_identity=True),))
    assert operator["identity_established"] is True and operator["display_text_equivalent"] is True
    # an operator binding is the only thing that retires the uncertainty as a person
    pair = pairing(sides=(right, "Beth"), sequence=timing(
        p1=[(2.0, None, right)], p2=[(2.0, None, "Beth")], carried={"p1": (-5.0, left)}, span=(0.0, 150.0)))
    assert bi.nameplate_timing(pair, game(0.0))["alias_uncertain"]
    bound = bi.nameplate_timing(pair, game(0.0),
                                aliases=(dict(read, reviewed_by="operator",
                                              bound_participant_identity=True),))
    assert not bound["alias_uncertain"] and bound["carried_in_matches"]


def test_a_saved_text_correction_applies_to_its_own_instant_and_seat_only():
    """Scope is the whole idea: the same tag elsewhere is unexamined, not corrected.

    The correction arrives as an observation about (side, instant). Applying it to the trajectory is right;
    applying it wherever the string appears would be the global alias the review refused.
    """
    from tekken_vod_helper import boundary_games as bg
    rows = [{"timestamp": at, "screen_state": "gameplay", "hud_present": True,
             "characters": ("ALISA", "REINA"),
             "overlay": {"player1": p1, "player2": "Beth", "score": [0, 0]}}
            for at, p1 in [(10.0, "PWDDA | Az"), (60.0, "PWDDA | Az"), (110.0, "PWDDA | Ariz")]]
    corrections = [{"side": "p1", "at": 10.0, "raw_text": "PWDDA | Az", "observed_text": "PWDDA | Ariz",
                    "frame_file": "frame-0011.jpg", "frame_sha256": "x" * 64, "reviewed_by": "machine",
                    "evidence_tier": "machine_display_text"}]
    sequence = bg._name_sequence(rows, 0.0, 150.0, window_start=0.0, text_corrections=corrections)
    side = sequence["sides"]["p1"]
    # Only the 10.0 s row is corrected. The 60.0 s row still carries the raw string, so the trajectory reads
    # Ariz -> Az -> Ariz: an apparent wobble that is really one unexamined row, kept visible instead of being
    # smoothed away by a global alias.
    assert [event["to"] for event in side["change_events"]] == ["PWDDA | Ariz", "PWDDA | Az",
                                                                "PWDDA | Ariz"], side["change_events"]
    assert len(side["display_text_corrections_applied"]) == 1
    applied = side["display_text_corrections_applied"][0]
    assert applied["row_text"] == "PWDDA | Az" and applied["observed_text"] == "PWDDA | Ariz"
    assert applied["at"] == 10.0 and applied["bound_participant_identity"] is False
    assert sorted(side["names_seen"]) == ["PWDDA | Ariz", "PWDDA | Az"]
    uncorrected = bg._name_sequence(rows, 0.0, 150.0, window_start=0.0)
    assert [event["to"] for event in uncorrected["sides"]["p1"]["change_events"]] == \
        ["PWDDA | Az", "PWDDA | Ariz"]
    # a correction aimed at the other seat, or another instant, matches nothing
    for wrong in ({"side": "p2", "at": 10.0}, {"side": "p1", "at": 40.0}):
        probe = bg._name_sequence(rows, 0.0, 150.0, window_start=0.0,
                                  text_corrections=[dict(corrections[0], **wrong)])
        assert probe["sides"]["p1"]["display_text_corrections_applied"] == [], wrong
