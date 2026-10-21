from tekken_vod_helper.boundary_reasoning import TemporalAdjudicator
import pytest


@pytest.mark.parametrize('scores,expected', [
    ([(0, 0), (0, 0)], 'uncertain'),
    ([(0, 0), (None, None)], 'candidate'),
    ([(None, None), (None, None)], 'candidate'),
    ([(0, 0), (1, None)], 'candidate'),
    ([(False, 0), (0, False)], 'candidate'),
    ([(-1, 0), (0, 0)], 'candidate'),
    ([(0.0, 0), (0, 0)], 'candidate'),
])
def test_zero_score_evidence(scores, expected):
    obs = [dict(observation_id='o0', timestamp=0, state='stage_select', confidence=.9),
           dict(observation_id='o1', timestamp=5, state='gameplay', confidence=.9),
           dict(observation_id='o2', timestamp=10, state='controller_prompt', confidence=.9),
           dict(observation_id='o3', timestamp=15, state='gameplay', confidence=.9,
                score1=2, score2=1)]  # Evidence outside proposal cannot rescue it.
    for row, pair in zip(obs, scores):
        row.update(score1=pair[0], score2=pair[1])
    proposal = dict(start_id='o0', end_id='o2', evidence_ids=['o0','o1','o2'],
                    disposition='candidate', reason='original evidence')
    adj = TemporalAdjudicator('http://unused', 'unused')
    result = adj._validate_proposals([proposal], {r['observation_id']:r for r in obs},
                                     {r['observation_id']:i for i,r in enumerate(obs)}, obs)
    assert result[0]['disposition'] == expected
    assert result[0]['reason'].startswith('original evidence')
    if expected == 'uncertain':
        assert 'bracket status uncertain' in result[0]['reason']


@pytest.mark.parametrize('content', ['', 'invalid proposal JSON'])
def test_received_invalid_output_is_saved(tmp_path, content):
    import json
    from unittest.mock import MagicMock, patch
    from tekken_vod_helper.boundary_reasoning import TemporalAdjudicationError
    payload = {'choices': [{'message': {'content': content}}],
               'usage': {'total_tokens': 23}}
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode()
    adj = TemporalAdjudicator('http://unused', 'unused', output_dir=str(tmp_path))
    with patch('urllib.request.urlopen', return_value=response):
        with pytest.raises(TemporalAdjudicationError):
            adj.propose([dict(timestamp=0, state='unknown', confidence=0)], '')
    assert json.loads((tmp_path / 'adjudication-response.json').read_text()) == payload


@pytest.mark.parametrize('score', [False, -1, 0.1, '0'])
def test_propose_does_not_coerce_invalid_scores(score):
    import json
    from unittest.mock import patch
    rows = [dict(timestamp=t, state=s, confidence=.9, score1=score, score2=0)
            for t, s in [(0, 'stage_select'), (5, 'gameplay'), (10, 'controller_prompt')]]
    proposal = dict(start_id='obs_0000', end_id='obs_0002', evidence_ids=['obs_0001'],
                    disposition='candidate', reason='observed boundaries')
    adj = TemporalAdjudicator('http://unused', 'unused')
    with patch.object(adj, '_call_model', return_value=json.dumps([proposal])) as call:
        matches, _, _ = adj.propose(rows, '')
    assert len(matches) == 1
    assert all('score1' not in r for r in json.loads(call.call_args[0][0])['observations'])
