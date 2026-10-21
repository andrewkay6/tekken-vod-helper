"""Prevent wasted inference or truth leakage in the resumable evaluator."""
import json
import pytest
from scripts import evaluate_boundary_window as runner


def args(tmp_path, *extra):
    return ['--source', 'https://www.youtube.com/watch?v=untouched',
            '--truth', str(tmp_path / 'unread-truth.json'),
            '--output-dir', str(tmp_path / 'run')] + list(extra)


@pytest.mark.parametrize('option,value', [('--duration', '0'), ('--duration', 'nan'), ('--duration', 'inf'),
    ('--interval', '0'), ('--fine-step', 'inf'), ('--max-requests', '1'),
    ('--batch-size', '0'), ('--max-frames', '-1')])
def test_invalid_budgets_do_not_resolve_source(tmp_path, monkeypatch, option, value):
    def forbidden(*a, **kw):
        pytest.fail('Invalid configuration reached source resolution')
    monkeypatch.setattr(runner, 'resolve_source', forbidden)
    with pytest.raises((ValueError, SystemExit)):
        runner.main(args(tmp_path, option, value))


def test_resume_mismatch_stops_before_network(tmp_path, monkeypatch):
    calls = []
    def stopped(*a, **kw):
        calls.append(True)
        raise RuntimeError('source stopped for test')
    monkeypatch.setattr(runner, 'resolve_source', stopped)
    with pytest.raises(RuntimeError, match='source stopped'):
        runner.main(args(tmp_path, '--duration', '30'))
    assert (tmp_path / 'run' / 'protocol.json').exists()
    with pytest.raises((ValueError, SystemExit)):
        runner.main(args(tmp_path, '--duration', '35'))
    assert len(calls) == 1


def test_orphan_predictions_never_trigger_inference(tmp_path, monkeypatch):
    def stopped(*a, **kw):
        raise RuntimeError('source stopped for test')
    monkeypatch.setattr(runner, 'resolve_source', stopped)
    with pytest.raises(RuntimeError):
        runner.main(args(tmp_path, '--duration', '30'))
    (tmp_path / 'run' / 'predictions.json').write_text('[]')
    monkeypatch.setattr(runner, 'resolve_source', lambda *a, **kw: pytest.fail('Repeated inference'))
    with pytest.raises((ValueError, SystemExit)):
        runner.main(args(tmp_path, '--duration', '30'))


@pytest.mark.parametrize('interrupt', [False, True])
def test_predictions_persist_before_scoring(tmp_path, monkeypatch, interrupt):
    from tekken_vod_helper.boundary_scoring import score_matches
    frames = [dict(id=i + 1, timestamp=float(i), path='frame.jpg') for i in range(30)]
    source = dict(duration=30, source='yt:untouched', uri='unused')
    monkeypatch.setattr(runner, 'resolve_source', lambda *a, **kw: source)
    samples, classifications, proposals = [], [], []
    def sample(*a, **kw):
        samples.append(True)
        manifest = tmp_path / 'run' / 'frames' / 'sample-manifest.json'
        manifest.parent.mkdir()
        manifest.write_text('{}')
        return frames
    monkeypatch.setattr(runner, 'sample_frames', sample)
    monkeypatch.setattr(runner, '_load_manifest', lambda *a, **kw: frames)
    def classify(self, batch):
        self.calls += 1
        classifications.append(True)
        self.usage.append({'tokens': {'total_tokens': 10}})
        rows = [dict(f, state='gameplay', confidence=.9) for f in batch]
        self._save_provenance('test', {}, {'usage': {'total_tokens': 10}}, rows, batch)
        return rows
    monkeypatch.setattr(runner.VisionClient, 'classify', classify)
    refinements = []
    def refine(rows, *a, **kw):
        refinements.append(True)
        return rows
    monkeypatch.setattr(runner, '_refine', refine)
    matches = [dict(start=0, end=20, player1='A', player2='B')]
    def propose(self, rows, policy):
        assert not (tmp_path / 'run' / 'predictions.json').exists()
        proposals.append(True)
        if interrupt and len(proposals) == 1:
            raise RuntimeError('interrupted after saved vision work')
        return matches, [{'type': 'bracket_validity_unverified'}], {'usage': {'total_tokens': 5}}
    monkeypatch.setattr(runner.TemporalAdjudicator, 'propose', propose)
    scored = []
    def score(result, truth, output_dir, **kwargs):
        assert json.loads((tmp_path / 'run' / 'predictions.json').read_text()) == matches
        assert not (tmp_path / 'unread-truth.json').exists()
        scored.append(True)
        return {'score': score_matches(matches, matches, end=30)}
    monkeypatch.setattr(runner, 'score_saved_predictions', score)
    run_args = args(tmp_path, '--duration', '30', '--max-frames', '20')
    if interrupt:
        with pytest.raises(RuntimeError, match='interrupted'):
            runner.main(run_args)
    runner.main(run_args)
    assert scored == [True]
    assert samples == [True]
    assert classifications == [True]
    assert refinements == [True]
    monkeypatch.setattr(runner, 'resolve_source', lambda *a, **kw: pytest.fail('Inference during rescore'))
    runner.main(run_args)
    assert scored == [True, True]
