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
