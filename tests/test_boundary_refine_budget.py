"""Regression checks for real cached refinement windows and attempted-frame caps."""
import pytest
from tekken_vod_helper.boundary_workflow import _refine


class Client:
    def __init__(self, fail=False, max_requests=10):
        self.calls = 0
        self.max_requests = max_requests
        self.batches = []
        self.fail = fail

    def classify(self, frames):
        self.calls += 1
        self.batches.append([f['timestamp'] for f in frames])
        if self.fail:
            raise ValueError('classification failed')
        return [dict(f, state='gameplay', confidence=0.9) for f in frames]


def refine(budget, client):
    rows = [dict(timestamp=t, state=s) for t, s in
            [(0, 'stage_select'), (10, 'gameplay'), (20, 'controller_prompt')]]
    issues = []
    result = _refine(rows, {'duration': 30}, None, 5, 1, 3 + budget,
                     client, '', issues=issues,
                     cached_frames=[dict(timestamp=t, path='frame.jpg')
                                    for t in range(1, 20) if t != 10],
                     coarse_timestamps=[0, 10, 20], dur=30, batch_size=4)
    return result, issues


@pytest.mark.parametrize('budget,expected', [
    (0, []), (4, [[1, 2, 3, 4]]),
    (6, [[1, 2, 3, 4], [5, 6]]),
])
def test_cached_frame_budget(budget, expected):
    client = Client()
    result, issues = refine(budget, client)
    assert client.batches == expected
    assert client.calls == len(expected)
    assert len(result) == 3 + budget
    assert [r['timestamp'] for r in result] == sorted(r['timestamp'] for r in result)
    assert any(i.get('reason') == 'frame budget exhausted' for i in issues)


def test_failed_attempts_consume_frames():
    client = Client(fail=True)
    result, issues = refine(6, client)
    assert client.batches == [[1, 2, 3, 4], [5, 6]]
    assert client.calls == 2
    assert len(result) == 3
    assert sum(i['type'] == 'refinement_failed' for i in issues) == 2


def test_request_cap_stops_further_batches():
    client = Client(max_requests=1)
    result, issues = refine(10, client)
    assert client.batches == [[1, 2, 3, 4]]
    assert len(result) == 7
    assert any(i.get('reason') == 'model budget exhausted' for i in issues)
