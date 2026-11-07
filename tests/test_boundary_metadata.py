"""Set metadata must bind each player to the character read beside them, never to a screen slot."""
from tekken_vod_helper.boundary_reasoning import TemporalAdjudicator, _gameplay_metadata


def row(i, state='gameplay', pair=('Old A','Old B'), chars=('Dragunov','Eddy')):
    return dict(observation_id='o'+str(i), timestamp=i, state=state, confidence=.9,
                player1=pair[0], player2=pair[1], character1=chars[0], character2=chars[1])


def test_late_stable_header_and_played_characters_replace_stale_setup_metadata():
    rows = [row(0,'stage_select',chars=('Armor King','Reina')),row(1),row(2),
            row(3,pair=('Actual A','Actual B')),row(4,pair=('Actual A','Actual B')),
            row(5,'controller_prompt',pair=('Next A','Next B')),
            row(6,pair=('Later A','Later B')),row(7,pair=('Later A','Later B'))]
    proposal = dict(start_id='o0',end_id='o5',reason='observed set')
    match = TemporalAdjudicator('http://unused','unused')._build_match(proposal,rows)
    assert (match['player1'],match['player2']) == ('Actual A','Actual B')
    assert (match['character1'],match['character2']) == ('Dragunov','Eddy')


def test_repeated_timestamp_does_not_verify_a_changed_header():
    rows = [row(0),row(1),row(2,pair=('Unverified A','Unverified B')),
            row(2,pair=('Unverified A','Unverified B'))]
    assert _gameplay_metadata(rows)['player1'] == 'Old A'


def test_low_confidence_header_does_not_replace_supported_names():
    rows = [row(0),row(1),dict(row(2,pair=('Noise A','Noise B')),confidence=.2),
            dict(row(3,pair=('Noise A','Noise B')),confidence=.2)]
    assert _gameplay_metadata(rows)['player1'] == 'Old A'


def test_late_unverified_pair_cannot_reorient_the_winning_header():
    # Winner is verified at ts2/ts3; the trailing single-sighting ts4 pair must not
    # leak into how the winning pair is written to player1/player2.
    rows = [row(0,pair=('Bb one','Bb two')),row(1,pair=('Bb one','Bb two')),
            row(2,pair=('Aa one','Aa two')),row(3,pair=('Aa one','Aa two')),
            row(4,pair=('Zz one','Zz two'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['player2']) == ('Aa one','Aa two')


def test_latest_verified_pair_wins_over_an_earlier_one():
    rows = [row(0,pair=('Early A','Early B')),row(1,pair=('Early A','Early B')),
            row(6,pair=('Late A','Late B')),row(7,pair=('Late A','Late B'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['player2']) == ('Late A','Late B')


def test_slot_orientation_is_independent_of_row_order():
    # Reversing the reported header reverses the characters with it: the pairing, not the reading
    # order, is what player1/character1 and player2/character2 mean.
    rows_a = [row(0,pair=('Ann','Zoe'),chars=('Jin','Nina')),
              row(1,pair=('Ann','Zoe'),chars=('Jin','Nina'))]
    rows_b = [row(0,pair=('Zoe','Ann'),chars=('Nina','Jin')),
              row(1,pair=('Zoe','Ann'),chars=('Nina','Jin'))]
    assert _gameplay_metadata(rows_a) == _gameplay_metadata(rows_b)
    meta = _gameplay_metadata(rows_a)
    assert (meta['player1'],meta['character1']) == ('Ann','Jin')
    assert (meta['player2'],meta['character2']) == ('Zoe','Nina')


def test_a_name_never_inherits_the_character_opposite_it_on_screen():
    # Zoe played Jin and Ann played Nina. Alphabetical orientation puts Ann in slot 1, so slot 1's
    # character must travel with Ann; leaving it as the left-hand Jin pairs a player with the
    # opponent's character.
    rows = [row(1,'gameplay',pair=('Zoe','Ann'),chars=('Jin','Nina')),
            row(2,'gameplay',pair=('Zoe','Ann'),chars=('Jin','Nina'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['character1']) == ('Ann','Nina')
    assert (meta['player2'],meta['character2']) == ('Zoe','Jin')


def test_a_side_swap_between_games_keeps_each_player_with_their_own_character():
    rows = [row(1,pair=('Ann','Zoe'),chars=('Jin','Nina')),
            row(2,pair=('Zoe','Ann'),chars=('Nina','Jin')),
            row(3,pair=('Ann','Zoe'),chars=('Jin','Nina'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['character1']) == ('Ann','Jin')
    assert (meta['player2'],meta['character2']) == ('Zoe','Nina')


def test_non_gameplay_overlays_never_supply_set_metadata():
    # A setup grid and the next player's controller overlay name different pairings than the set
    # that was played, so they may not contribute a single field.
    rows = [row(0,'stage_select',pair=('Setup A','Setup B'),chars=('Armor King','Reina')),
            row(1,'character_select',pair=('Setup A','Setup B'),chars=('Armor King','Reina')),
            row(2,'gameplay',pair=('Played A','Played B'),chars=('Jin','Nina')),
            row(3,'gameplay',pair=('Played A','Played B'),chars=('Jin','Nina')),
            row(4,'controller_prompt',pair=('Next A','Next B'),chars=('King','Kuma')),
            row(5,'post_match_menu',pair=('Next A','Next B'),chars=('King','Kuma'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['player2']) == ('Played A','Played B')
    assert (meta['character1'],meta['character2']) == ('Jin','Nina')
    assert meta['association']['basis'] == 'co_observed_slots'


def test_a_changed_character_mid_set_is_not_treated_as_a_new_player():
    rows = [row(1,pair=('Ann','Zoe'),chars=('Jin','Nina')),
            row(2,pair=('Ann','Zoe'),chars=('Jin','Nina')),
            row(3,pair=('Ann','Zoe'),chars=('Jin','Nina')),
            row(4,pair=('Ann','Zoe'),chars=('Jin','Steve')),
            row(5,pair=('Ann','Zoe'),chars=('Jin','Steve'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['player2']) == ('Ann','Zoe')
    assert (meta['character1'],meta['character2']) == ('Jin','Nina')
    latest = [row(6,pair=('Ann','Zoe'),chars=('Jin','Steve')),
              row(7,pair=('Ann','Zoe'),chars=('Jin','Steve'))]
    assert _gameplay_metadata(rows + latest)['character2'] == 'Steve'


def test_names_never_read_beside_a_character_stay_unbound_instead_of_guessed():
    rows = [row(1,pair=('Ann','Zoe'),chars=('','')),
            row(2,pair=('Ann','Zoe'),chars=('',''))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['player2']) == ('Ann','Zoe')
    assert 'character1' not in meta and 'character2' not in meta
    assert meta['association']['basis'] == 'names_never_co_observed_with_characters'
    assert meta['association']['verified'] is True


def test_one_sided_header_keeps_the_bound_slot_and_marks_the_other_unbound():
    rows = [row(1,pair=('Ann','Zoe'),chars=('Jin','')),
            row(2,pair=('Ann','Zoe'),chars=('Jin',''))]
    meta = _gameplay_metadata(rows)
    assert (meta['character1'],meta['character2']) == ('Jin','')
    assert meta['association']['basis'] == 'partially_co_observed'


def test_build_match_flags_a_span_with_no_confident_gameplay_header():
    rows = [row(0,'stage_select',pair=('Setup A','Setup B'),chars=('Armor King','Reina')),
            row(1,'vs_screen'),row(2,'controller_prompt',pair=('Next A','Next B'))]
    proposal = dict(start_id='o0',end_id='o2',reason='no play observed')
    match = TemporalAdjudicator('http://unused','unused')._build_match(proposal,rows)
    assert match['metadata_association'] == {'basis':'span_fallback','verified':False}


def test_characters_stay_bound_to_their_own_slots():
    rows = [row(0,chars=('Armor King','Reina')),row(1,chars=('Armor King','Reina'))]
    meta = _gameplay_metadata(rows)
    assert (meta['player1'],meta['player2']) == ('Old A','Old B')
    assert (meta['character1'],meta['character2']) == ('Armor King','Reina')


def test_anchor_uses_first_confident_grid_in_final_stage_episode():
    rows = [row(0,'stage_select'),row(1,'gameplay'),row(2,'character_select'),
            row(3,'stage_select'),row(4,'stage_select'),row(5,'stage_select'),
            row(6),row(7,'controller_prompt')]
    proposal = dict(start_id='o5',end_id='o7',evidence_ids=['o5','o6','o7'],
                    disposition='candidate',reason='selected final grid frame')
    result = TemporalAdjudicator('http://unused','unused')._validate_proposals(
        [proposal],{r['observation_id']:r for r in rows},
        {r['observation_id']:i for i,r in enumerate(rows)},rows)
    assert result[0]['start_id'] == 'o3'
