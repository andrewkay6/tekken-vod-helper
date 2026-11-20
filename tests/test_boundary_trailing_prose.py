import json
import pytest
from tekken_vod_helper.boundary_media import VisionClient

FRAMES = [dict(id=4241, timestamp=0, path='f.jpg')]
ROWS = [dict(id=4241, state='controller_prompt', confidence=.9, score1=1, score2=2)]


def test_fenced_array_followed_by_prose_with_brackets():
    # Shape of the BB11 batch that failed three times on 2026-11-20.
    text = '```json\n' + json.dumps(ROWS, indent=2) + '\n```\n\nCentral overlay reads "Press [stick] on the controller"; scores [1-2].'
    result = VisionClient()._parse_output(text, FRAMES)
    assert result[0]['state'] == 'controller_prompt'


def test_prose_before_array_still_parses():
    text = 'Here are the results:\n' + json.dumps(ROWS)
    assert VisionClient()._parse_output(text, FRAMES)[0]['score2'] == 2


def test_no_array_is_still_rejected():
    with pytest.raises(ValueError):
        VisionClient()._parse_output('I could not classify these frames.', FRAMES)


def test_broken_array_is_still_rejected():
    with pytest.raises(ValueError):
        VisionClient()._parse_output('```json\n[{"id": 4241, "state": \n```', FRAMES)


def test_prose_with_brackets_before_array():
    text = 'All eight frames show **"Player 1 / Press [⊕] on the controller."**\n\n```json\n' + json.dumps(ROWS) + '\n```'
    assert VisionClient()._parse_output(text, FRAMES)[0]['id'] == 4241
