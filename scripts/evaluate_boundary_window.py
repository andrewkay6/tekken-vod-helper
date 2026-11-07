"""Resumable label-blind prefix evaluation; truth loads only after predictions persist."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tekken_vod_helper import local_model
from tekken_vod_helper.boundary_media import PROMPT_VERSION, resolve_source, sample_frames, VisionClient
from tekken_vod_helper.boundary_reasoning import TemporalAdjudicator
from tekken_vod_helper.boundary_workflow import _refine, _build_project, _load_manifest, score_saved_predictions


def write_protocol(out, args):
    import math
    import hashlib
    import json
    from pathlib import Path
    d = vars(args).copy()
    for k in ("duration", "interval", "fine_step"):
        v = d.get(k)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{k} must be finite > 0")
    if d["fine_step"] > d["interval"]:
        raise ValueError("fine_step must be <= interval")
    for k in ("max_frames", "batch_size", "max_requests"):
        v = d.get(k)
        if not isinstance(v, int) or v <= 0:
            raise ValueError(f"{k} must be positive int")
    if d["max_requests"] < 2:
        raise ValueError("max_requests must be >= 2")
    if math.ceil(d["duration"] / d["interval"]) > d["max_frames"]:
        raise ValueError("too many frames")
    if math.ceil(d["duration"] / d["fine_step"]) > 12000:
        raise ValueError("too many fine steps")
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    policy = ROOT / "docs" / "cutting-policy.md"
    b1 = ROOT / "tekken_vod_helper" / "boundary_media.py"
    b2 = ROOT / "tekken_vod_helper" / "boundary_reasoning.py"
    d["policy_sha256"] = sha(policy)
    d["profile_hashes"] = {"boundary_media": sha(b1), "boundary_reasoning": sha(b2)}
    p = Path(out) / "protocol.json"
    if p.exists():
        existing = json.loads(p.read_text(encoding="utf-8"))
        if existing != d:
            raise ValueError("config mismatch")
    else:
        if any(out.iterdir()):
            raise ValueError("output directory not empty")
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source','truth','output-dir']:
        parser.add_argument('--'+name, required=True)
    for name,default in [('duration',3600),('interval',5),('fine-step',1)]:
        parser.add_argument('--'+name,type=float,default=default)
    for name,default in [('max-frames',1000),('max-requests',120),('batch-size',12)]:
        parser.add_argument('--'+name,type=int,default=default)
    parser.add_argument('--yt-dlp',default='C:/tmp/tekken-vod-helper-tools/yt-dlp.exe')
    parser.add_argument('--ffmpeg',default='C:/ProgramData/chocolatey/lib/ffmpeg-full/tools/ffmpeg/bin/ffmpeg.exe')
    parser.add_argument('--endpoint',default=local_model.default_endpoint(),
                        help='Default: $QWEN_BASE_URL or {}.'.format(local_model.DEFAULT_ENDPOINT))
    parser.add_argument('--model',default=local_model.default_model(),
                        help='Historical cached batches record the model id, so replaying an older '
                             'run directory needs its original model passed here. Default: $QWEN_MODEL '
                             'or {}.'.format(local_model.DEFAULT_MODEL))
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    def save(name, data):
        (out / name).write_text(json.dumps(data, indent=2), encoding='utf-8')

    write_protocol(out, args)
    if (out / 'predictions.json').exists():
        if (out / 'backtest-report.json').exists():
            return json.loads((out / 'backtest-report.json').read_text())
        if not (out / 'run.json').exists():
            raise ValueError('Predictions exist but run.json is missing; repair metadata without inference')
        return score_saved_predictions(json.loads((out / 'run.json').read_text()), args.truth, str(out))
    src = resolve_source(args.source,
                         yt_dlp=args.yt_dlp)
    dur = min(args.duration, src['duration'])
    ff = args.ffmpeg
    manifest = out / 'frames' / 'sample-manifest.json'
    frames = (_load_manifest(manifest, src, dur, args.fine_step, is_cache_fine=True) if manifest.exists() else
              sample_frames(src, str(out / 'frames'), start=0, end=dur,
                            interval=args.fine_step, width=640, max_frames=12000, ffmpeg_path=ff))
    print(json.dumps({'phase':'sampled','frames':len(frames),'duration':dur}), flush=True)
    coarse = []
    for frame in frames:
        if not coarse or frame['timestamp'] - coarse[-1]['timestamp'] >= args.interval:
            coarse.append(frame)
    known = {}
    class ResumeClient(VisionClient):
        def classify(self, batch):
            missing = [f for f in batch if f['timestamp'] not in known]
            if missing:
                for row in super().classify(missing):
                    known[row['timestamp']] = row
            return [known[f['timestamp']] for f in batch]

    client = ResumeClient(endpoint=args.endpoint, model=args.model,
                          max_requests=args.max_requests - 1, output_dir=str(out), crop_regions=True,
                          enable_thinking=True,
                          policy_path=str(ROOT / 'docs' / 'cutting-policy.md'))
    by_id = {f['id']: f for f in frames}
    recovered = []
    for path in sorted((out / 'batches').glob('batch-*.json')):
        batch = json.loads(path.read_text())
        if (batch['policy_hash'] != client._policy_hash or batch['model'] != client.model
                or batch.get('prompt_version') != PROMPT_VERSION):
            raise ValueError(
                'Saved batch profile mismatch: {}: saved {} / {}, policy {} / {}'.format(
                    path.name, batch.get('prompt_version'), PROMPT_VERSION,
                    batch['policy_hash'], client._policy_hash))
        client.calls = max(client.calls, batch['call'])
        results = batch['results']
        if not results:
            ids = batch['frame_ids'] or [int(i) for i in re.findall(
                r'id=(\d+) t=', batch['request']['messages'][0]['content'][0]['text'])]
            results = client._parse_output(batch['response']['choices'][0]['message']['content'],
                                           [by_id[i] for i in ids])
            recovered.append({'original_batch': str(path), 'results': results})
        known.update({r['timestamp']: r for r in results})
        client.usage.append({'call': batch['call'], 'tokens': batch['response'].get('usage', {})})
    if recovered:
        save('recovered-parsing.json', recovered)
    rows = []
    for i in range(0, len(coarse), args.batch_size):
        rows.extend(client.classify(coarse[i:i+args.batch_size]))
        save('timeline.json', rows)
        print(json.dumps({'phase':'coarse','observations':len(rows),'calls':client.calls}), flush=True)
    issues = []
    if (out / 'timeline-refined.json').exists():
        rows = json.loads((out / 'timeline-refined.json').read_text())
        issues = json.loads((out / 'refinement-issues.json').read_text())
    else:
        rows = _refine(rows, src, out, args.interval, args.fine_step, args.max_frames, client, ff, issues=issues,
                       cached_frames=frames, coarse_timestamps=[f['timestamp'] for f in coarse],
                       dur=dur, batch_size=args.batch_size)
        # Preserve every saved observation even if an interrupted attempt reached the request cap.
        merged = {r['timestamp']:r for r in rows}
        merged.update(known)
        rows = sorted(merged.values(), key=lambda r:r['timestamp'])
        save('refinement-issues.json', issues)
        save('timeline-refined.json', rows)
    print(json.dumps({'phase':'refined','observations':len(rows),'calls':client.calls}), flush=True)
    policy = (ROOT / 'docs' / 'cutting-policy.md').read_text(encoding='utf-8')
    saved_response = None
    if (out / 'adjudication.json').exists():
        saved_response = json.loads((out / 'adjudication.json').read_text())['raw_response']
    elif (out / 'adjudication-response.json').exists():
        saved_response = json.loads((out / 'adjudication-response.json').read_text())['choices'][0]['message']['content']
    adj = TemporalAdjudicator(endpoint=args.endpoint, model=args.model,
                              timeout=1800, output_dir=None if saved_response is not None else str(out))
    if saved_response is not None:
        adj._call_model = lambda content: saved_response
    matches, review, provenance = adj.propose(rows, policy)
    if saved_response is not None:
        provenance.update(mode='saved-response-replay', new_model_calls=0)
    save('predictions.json', matches)
    save('project.tvh.json', _build_project(matches, src, dur, 'Held-out evaluation', str(out)).to_dict())
    save('review.json', {'items': review + issues, 'adjudication': provenance})
    run = {'status': 'complete', 'range': [0, dur], 'source_id': src['source'],
           'output_dir': str(out), 'refinement_status': 'degraded' if issues else 'complete',
           'vision_model_calls': client.calls, 'adjudication_model_calls': adj.calls,
           'model_calls': client.calls + adj.calls, 'usage': client.usage,
           'adjudication': provenance, 'paths': {k: str(out / v) for k,v in
           {'predictions':'predictions.json','review':'review.json','project':'project.tvh.json','timeline':'timeline.json'}.items()}}
    save('run.json', run)
    report = score_saved_predictions(run, args.truth, str(out))
    score = report['score']
    print(json.dumps({'phase':'complete','tp':score['true_positives'], 'fp':score['false_positives'],
        'fn':score['false_negatives'], 'eligible_references':score['reference_count']-len(score['excluded_references']),
        'cut_errors':score['matches'], 'start_mae':score['start_mae'],'end_mae':score['end_mae'],
        'within5_both':score['within_tolerance_both'],'review_count':len(review)}), flush=True)
    return report


if __name__ == '__main__':
    main()
