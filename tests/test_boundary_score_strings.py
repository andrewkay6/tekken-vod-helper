import json
import pytest
from tekken_vod_helper.boundary_media import VisionClient


@pytest.mark.parametrize('score,expected', [('0', 0), ('2', 2), ('12', 12), (None, None)])
def test_canonical_decimal_scores(score, expected):
    rows = [dict(id=1, state='gameplay', confidence=.9, score1=score)]
    result = VisionClient()._parse_output(json.dumps(rows), [dict(id=1, timestamp=0, path='f.jpg')])
    assert result[0]['score1'] == expected


@pytest.mark.parametrize('score', [True, -1, 0.1, '01', ' 1', '+1', '1.0', 'unknown', '100', '\u0661'])
def test_other_scores_remain_invalid(score):
    rows = [dict(id=1, state='gameplay', confidence=.9, score1=score)]
    with pytest.raises(ValueError, match='Score'):
        VisionClient()._parse_output(json.dumps(rows), [dict(id=1, timestamp=0, path='f.jpg')])


def test_failed_batch_keeps_requested_ids(tmp_path):
    from unittest.mock import MagicMock, patch
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({'choices': [{'message': {
        'content': '[{"id":7,"state":"gameplay","confidence":0.9,"score1":true}]'}}]}).encode()
    client = VisionClient(output_dir=str(tmp_path))
    frames = [dict(id=7, timestamp=12, path='missing.jpg')]
    with patch.object(client, '_build_content', return_value=[]), \
         patch('urllib.request.urlopen', return_value=response):
        with pytest.raises(ValueError, match='Score'):
            client.classify(frames)
    # Provenance is named after the request and its attempt number, so every try keeps its own record.
    # When the name was a bare call counter all three of these overwrote each other and only the last
    # failure was left on disk -- which is how a run could spend three attempts and show one.
    files = sorted((tmp_path / 'batches').glob('batch-*.json'))
    assert len(files) == 3, [p.name for p in files]
    for path in files:
        saved = json.loads(path.read_text())
        assert saved['frame_ids'] == [7]
        assert saved['results'] == []
