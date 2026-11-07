"""Optional bracket context: strong, fallible, and never the only witness.

The review's finding was that `separated` required a controller screen on every branch, which is a
mandatory cue wearing the word "preferred". These tests hold the replacement to its two halves at once: a
second evidence family is admitted, and that family alone decides nothing. The bracket may agree with a
boundary the recording already argues for; it may not create one, may not vouch for itself, and may not
turn a short recovery into a long one.

The second half is the recording-only path. Every case here is also run with no bracket supplied, and the
verdict has to be the one this branch produced before the input existed -- otherwise "optional" would mean
"off by accident" rather than "a route you can decline".

No bracket in this project has been fetched or verified. Everything below is caller-supplied data, and the
provenance fields are asserted precisely because nothing here has checked them against an event API.
"""
import pytest

from tekken_vod_helper import boundary_bracket as bb
from tekken_vod_helper import boundary_games as bg

TWO_MATCHES = {
    "source": "startgg",
    "provenance": {"event": "basement-brawl-9", "fetched_at": "2026-10-30T12:00:00Z",
                   "verified": False, "note": "caller-supplied; nothing fetched by this repo"},
    "matches": [
        {"id": "wqf1", "label": "Winners Quarter-Final", "section": "winners",
         "players": ["Alpha", "Beta"], "games_per_set": 3, "reported_score": [3, 1]},
        {"id": "wqf2", "label": "Winners Quarter-Final", "section": "winners",
         "players": ["Gamma", "Delta"], "games_per_set": 3, "reported_score": [3, 2]},
    ],
}


def context(matches, source="startgg", **provenance):
    prov = {"event": "basement-brawl-9", "fetched_at": "2026-10-30T12:00:00Z", "verified": False}
    prov.update(provenance)
    return bb.normalize({"source": source, "provenance": prov, "matches": matches})


def game(index, winner="p1", start=None, end=None):
    item = {"start": (index * 200.0 if start is None else start),
            "end": (index * 200.0 + 150.0 if end is None else end),
            "status": "complete", "winner": winner, "counts_toward_set": True}
    item["conclusion_event_at"] = item["end"]
    return item


def pairing_rows(first, second, scores=([0, 0], [0, 0])):
    """Two games, each named dominantly on every row, with nothing in the gap but the dwell floor."""
    rows = []
    for item, (left, right), score in ((first, ("Alpha", "Beta"), scores[0]),
                                       (second, ("Gamma", "Delta"), scores[1])):
        for at in (item["start"] + 10, item["start"] + 80, item["end"] - 10):
            rows.append({"timestamp": at, "screen_state": "gameplay",
                         "overlay": {"player1": left, "player2": right, "score": list(score)}})
    return rows


def link(rows, bracket=None, first_start=0.0, second_start=200.0):
    games = [game(0, start=first_start), game(1, start=second_start)]
    return bg._classify_link(games[0], games[1], rows, bracket=bracket)


def _two_match_bracket(**overrides):
    matches = [{"id": "wqf1", "label": "Winners Quarter-Final", "section": "winners",
                "players": ["Alpha", "Beta"], "games_per_set": 3, "reported_score": [3, 1]},
               {"id": "wqf2", "label": "Winners Quarter-Final", "section": "winners",
                "players": ["Gamma", "Delta"], "games_per_set": 3, "reported_score": [3, 2]}]
    matches[0].update(overrides.get("first", {}))
    matches[1].update(overrides.get("second", {}))
    return context(matches)


# ---------------------------------------------------------------- normalising an optional input


def test_nothing_supplied_is_the_normal_case_and_not_an_error():
    assert bb.normalize(None) is None
    assert bb.provenance_block(None) == {"supplied": False, "route": "recording_only"}
    assert bb.link_signal(None, ["Alpha", "Beta"], ["Gamma", "Delta"]) is None
    assert bb.format_for(None) is None
    assert bb.expectation(None, ["Alpha", "Beta"]) is None


def test_unattributed_context_is_refused_rather_than_used_silently():
    """Context with no source cannot be discounted later, which is the only reason to keep it."""
    with pytest.raises(bb.BracketError, match="names no source"):
        bb.normalize({"matches": [{"players": ["A", "B"]}]})
    with pytest.raises(bb.BracketError, match="unknown bracket source"):
        bb.normalize({"source": "a_vibes_based_spreadsheet", "matches": [{"players": ["A", "B"]}]})
    with pytest.raises(bb.BracketError, match="no matches"):
        bb.normalize({"source": "startgg", "matches": []})
    with pytest.raises(bb.BracketError, match="exactly two players"):
        bb.normalize({"source": "startgg", "matches": [{"players": ["A"]}]}),


def test_normalising_twice_changes_nothing_and_never_re_labels_provenance():
    once = _two_match_bracket()
    assert bb.normalize(once) is once
    again = bb.normalize(bb.normalize({"source": "startgg", "matches": once["matches"]}))
    assert again["provenance"]["source"] == "startgg"
    assert again["may_decide_a_boundary_alone"] is False
    assert again["independent_of_recording"] is False
    assert "independent proof of the score it reported" in " ".join(again["never_supplies"])


def test_a_reported_score_carries_its_provenance_and_its_limits():
    once = _two_match_bracket()
    stated = bb.expectation(once, ["Gamma", "Delta"])
    assert stated["reported_score"] == [3, 2] and stated["expected_games"] == 5
    assert stated["use"] == "account for this many games, or name the gap"
    assert "cannot" in stated and "independent proof" in stated["cannot"]
    # An ambiguity in the pairing must not become an expectation.
    assert bb.expectation(once, ["Nobody", "Notlisted"]) is None


def test_format_is_only_stated_when_the_candidates_agree():
    agreed = context([{"players": ["A", "B"], "games_per_set": 3, "section": "losers"},
                      {"players": ["C", "D"], "games_per_set": 3, "section": "losers"}])
    assert bb.format_for(agreed)["games_per_set"] == 3
    disputed = context([{"players": ["A", "B"], "games_per_set": 3},
                        {"players": ["C", "D"], "games_per_set": 5}])
    assert bb.format_for(disputed) is None, "a target two matches disagree on was reported as a format"


def test_grand_finals_reset_is_a_stated_format_never_an_inference():
    plain = context([{"players": ["A", "B"], "section": "grand_finals", "games_per_set": 3}])
    assert bb.format_for(plain)["grand_finals_reset"] is None, "a reset was assumed from the label"
    reset = context([{"players": ["A", "B"], "section": "grand_finals", "games_per_set": 3,
                      "grand_finals_reset": True}])
    assert bb.format_for(reset)["grand_finals_reset"] is True


# ---------------------------------------------------------------- the link signal


def test_the_bracket_names_a_different_match_only_when_the_pairings_resolve_uniquely():
    once = _two_match_bracket()
    signal = bb.link_signal(once, ["Alpha", "Beta"], ["Gamma", "Delta"])
    assert signal["state"] == "different_match"
    assert signal["before"] == "wqf1" and signal["after"] == "wqf2"
    assert signal["independent_of_recording"] is False

    # A rematch, or a grand-final reset, leaves the same two names in two listed matches: the bracket
    # declines to answer, and declining is not agreement.
    rematch = context([{"id": "m1", "players": ["Alpha", "Beta"]},
                       {"id": "m2", "players": ["Beta", "Alpha"]}])
    silent = bb.link_signal(rematch, ["Alpha", "Beta"], ["Alpha", "Beta"])
    assert silent["state"] == "unknown" and "more than one listed match" in silent["because"]
    assert bb.link_signal(once, ["Nobody", "Alphabet"], ["Gamma", "Delta"])["state"] == "unknown"


def test_spelling_drift_in_a_gamer_tag_does_not_become_a_match_change():
    """BB9 read one tag as both "Spitfire2929" and "Spire2929"; a bracket is joined by names, carefully."""
    once = context([{"id": "m1", "players": ["Spitfire2929", "Rebix"]},
                    {"id": "m2", "players": ["Buk", "Ariz"]}])
    signal = bb.link_signal(once, ["Spire2929", "Rebix"], ["Buk", "Ariz"])
    assert signal["state"] == "different_match"
    same = bb.link_signal(once, ["Spitfire2929", "Rebix"], ["Spire2929", "RebiX"])
    assert same["state"] == "same_match", "one player's two spellings became two matches"


# ---------------------------------------------------------------- what it may now do


def test_a_listed_match_change_replaces_the_controller_screen_as_the_event_signal():
    """The point of the clarification: a recording with no controller handover is not evidence-free.

    Two families still, and both are required -- the bracket's word that these are different matches, and
    the footage showing a different pairing on a dominant reading. Neither alone is enough, which is the
    next test.
    """
    rows = pairing_rows(game(0), game(1))
    without = link(rows)
    assert without["verdict"] == "ambiguous", "the recording-only verdict changed"
    assert without["basis"] == "recording_only"
    assisted = link(rows, bracket=_two_match_bracket())
    assert assisted["verdict"] == "separated"
    assert assisted["basis"] == "bracket_assisted"
    assert set(assisted["evidence_for"]) == {"bracket_match_change", "pairing_change"}
    assert assisted["evidence_against"] == []
    assert "two independent signals agree" in assisted["reasons"][0]


def test_the_bracket_on_its_own_does_not_cut():
    """A pairing that resolves to two listed matches but shows no change on screen stays uncertain.

    This is the case Andy's clarification warns about in reverse: the bracket is usually faithful, and
    "usually" is doing work here, because an event record that disagrees with a steady pairing is either a
    late update or a mis-set -- and the footage cannot tell which.
    """
    same_pairing = []
    first, second = game(0), game(1)
    for item in (first, second):
        for at in (item["start"] + 10, item["end"] - 10):
            same_pairing.append({"timestamp": at, "screen_state": "gameplay",
                                 "overlay": {"player1": "Alpha", "player2": "Beta",
                                             "score": [1, 0]}})
    one_match = context([{"id": "m1", "players": ["Alpha", "Beta"], "reported_score": [3, 1]}])
    verdict = link(same_pairing, bracket=one_match)
    assert verdict["verdict"] == "joined" and verdict["bracket"]["state"] == "same_match"
    two_matches = context([{"id": "m1", "players": ["Alpha", "Beta"]},
                           {"id": "m2", "players": ["Beta", "Alpha"]}])
    verdict = link(same_pairing, bracket=two_matches)
    assert verdict["verdict"] == "joined", "a pairing in two listed matches was read as a new match"
    assert verdict["bracket"]["state"] == "unknown"


def test_a_bracket_that_disagrees_with_the_footage_keeps_the_doubt_instead_of_choosing():
    """Contradictory information stays contradictory.

    Here the recording would have separated on its own -- a controller prompt plus a tally reset with the
    same pairing named. The event record says one match and states no reset, so the verdict is demoted to
    ambiguous and the disagreement is routed to the band, not resolved by whichever source is newer.
    """
    rows = pairing_rows(game(0), game(1), scores=([2, 0], [0, 0]))
    rows = [{"timestamp": g["start"] + 10, "screen_state": "gameplay",
             "overlay": {"player1": "Alpha", "player2": "Beta", "score": score}}
            for g, score in ((game(0), [2, 0]), (game(1), [0, 0]))]
    rows += [{"timestamp": game(0)["start"] + 40, "screen_state": "gameplay",
              "overlay": {"player1": "Alpha", "player2": "Beta", "score": [2, 0]}},
             {"timestamp": game(0)["end"] + 30, "screen_state": "controller_prompt", "overlay": {}}]
    alone = link(rows)
    assert alone["verdict"] == "separated", "the recording-only route stopped separating on cue + reset"
    against = context([{"id": "m1", "players": ["Alpha", "Beta"], "grand_finals_reset": False}])
    verdict = link(rows, bracket=against)
    assert verdict["verdict"] == "ambiguous"
    assert verdict["evidence_against"] == ["bracket_same_match"]
    assert verdict["questions"] and verdict["questions"][0]["route"] == "header"
    assert "point different ways" in " ".join(verdict["reasons"])


def test_a_stated_grand_final_reset_is_not_treated_as_a_contradiction():
    reset = context([{"id": "gf", "players": ["Alpha", "Beta"], "section": "grand_finals",
                      "grand_finals_reset": True}])
    rows = [{"timestamp": g["start"] + 10, "screen_state": "gameplay",
             "overlay": {"player1": "Alpha", "player2": "Beta", "score": score}}
            for g, score in ((game(0), [2, 0]), (game(1), [0, 0]))]
    rows += [{"timestamp": game(0)["end"] + 30, "screen_state": "controller_prompt", "overlay": {}}]
    verdict = link(rows, bracket=reset)
    assert verdict["evidence_against"] == [], "a format that allows a reset was read as an objection"
    assert verdict["verdict"] == "separated"


# ---------------------------------------------------------------- what it may never do


def test_a_candidate_short_of_the_expected_count_reports_a_gap_and_keeps_its_games():
    """A reported 3-2 means five games to account for. It does not mean five games were recovered."""
    games = [game(0, "p1"), game(1, "p2")]
    rows = pairing_rows(game(0), game(1))
    record = {"games": 2, "identities": [{"before": ["Gamma", "Delta"], "after": ["Gamma", "Delta"]}]}
    stated = bg._bracket_expectation(_two_match_bracket(), record)
    assert stated["expected_games"] == 5 and stated["games_seen"] == 2
    assert stated["games_unaccounted"] == 3 and stated["gap_is_a_question"] is True
    assert stated["independent_of_recording"] is False
    assert len(games) == 2, "the expectation reached into the game list"


def test_no_configured_target_is_adopted_from_the_bracket_by_default():
    """Adopting a target is a caller decision: ``games_per_set`` stays a configured input."""
    rows = pairing_rows(game(0), game(1))
    observations = bg.build_observations(rows)
    plain = bg.track_games(observations, games_per_set=None)
    assisted = bg.track_games(observations, games_per_set=None, bracket=_two_match_bracket())
    assert plain["format"]["games_per_set"] is None
    assert assisted["format"]["games_per_set"] is None
    assert assisted["format"]["bracket_states"]["games_per_set"] == 3
    assert assisted["format"]["bracket_states"]["configured_by_a_human"] is False
    assert assisted["evidence_routes"]["bracket"]["supplied"] is True
    assert plain["evidence_routes"]["bracket"] == {"supplied": False, "route": "recording_only"}


@pytest.mark.parametrize("screen", ["controller_assignment", "controller_prompt", None])
def test_the_recording_only_path_is_unchanged_by_the_presence_of_the_code(screen):
    """The same footage, bracket or no bracket: if no signal from the bracket resolves, nothing moves."""
    rows = pairing_rows(game(0), game(1))
    if screen:
        rows = rows + [{"timestamp": game(0)["end"] + 30, "screen_state": screen, "overlay": {}}]
    silent = context([{"id": "m9", "players": ["Unrelated", "Names"]}])
    assert link(rows, bracket=silent)["verdict"] == link(rows)["verdict"]
