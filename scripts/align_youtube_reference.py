"""Independent pixel alignment of published clips against a cached source VOD."""
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageChops, ImageStat
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tekken_vod_helper.boundary_media import resolve_source, sample_frames


def best_matches(source_features, query, limit=3):
    if not source_features:
        raise ValueError
    dists = []
    for t, img, path in source_features:
        diff = ImageChops.difference(img, query)
        m = ImageStat.Stat(diff).mean
        dists.append((sum(m) / len(m), t, path))
    dists.sort(key=lambda x: (x[0], x[1]))
    res = []
    for d, t, p in dists:
        if len(res) >= limit:
            break
        if all(abs(t - r['timestamp']) > 5 for r in res):
            res.append({'timestamp': t, 'distance': d, 'path': p})
    return res


def feature(path):
    with Image.open(path) as im:
        return im.convert('RGB').resize((48, 27))


def derive_interval(anchors, duration):
    usable = [a for a in anchors if a['matches'][0]['distance'] < 12]
    candidates = []
    for anchor in usable:
        group = [a for a in usable if abs(a['offset']-anchor['offset']) <= 2]
        starts = [a['offset'] for a in group if a['side']=='start']
        ends = [a['offset'] for a in group if a['side']=='end']
        if len(starts)<2 or len(ends)<2:
            continue
        first, last = statistics.median(starts), statistics.median(ends)
        if abs(first-last)>2:
            continue
        candidates.append((len(group), -sum(a['matches'][0]['distance'] for a in group)/len(group),
                           [first, last+duration], group))
    if not candidates:
        return None, []
    best = max(candidates, key=lambda c:(c[0],c[1]))
    return best[2], best[3]


def main():
    out = ROOT / 'build/work/boundary-evaluation/bb8-youtube-reference-alignment-v1'
    out.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / 'build/work/qwen/youtube-channel-flat-2026-10-20.json').read_text())
    ids = ['0_lzrH5Nako', 'FnQ8M63Krbs', '0pAwbhGkzic', 'Q9RAnM5_lmQ']
    entries = {e['id']:e for e in catalog['entries'] if e['id'] in ids}
    def prepare(vid):
        entry = entries[vid]
        folder = out / vid
        folder.mkdir(exist_ok=True)
        samples = []
        src = None
        for label, start, end in [('start', 0, 5), ('opening', 8, 13), ('end', entry['duration']-5, entry['duration'])]:
            manifest = folder / label / 'sample-manifest.json'
            if manifest.exists():
                frames = json.loads(manifest.read_text())['frames']
            else:
                if src is None:
                    src = resolve_source('https://www.youtube.com/watch?v='+vid,
                        yt_dlp='C:/tmp/tekken-vod-helper-tools/yt-dlp.exe')
                frames = sample_frames(src, str(folder / label), start=start, end=end,
                    interval=1, max_frames=5, width=640,
                    ffmpeg_path='C:/ProgramData/chocolatey/lib/ffmpeg-full/tools/ffmpeg/bin/ffmpeg.exe')
            samples.extend([dict(f, side='end' if label=='end' else 'start') for f in frames])
        return vid, entry, samples
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepared = list(pool.map(prepare, ids))
    print('Published-clip boundary frames cached', flush=True)
    source = ROOT / 'build/work/boundary-evaluation/oct1-heldout-prefix-v7/frames/sample-manifest.json'
    frames = json.loads(source.read_text())['frames']
    source_features = [(f['timestamp'], feature(ROOT / f['path']), f['path']) for f in frames]
    results = []
    for vid, entry, samples in prepared:
        anchors = []
        for sample in samples:
            query = feature(sample['path'])
            if sum(ImageStat.Stat(query).stddev) < 10:
                continue
            matches = best_matches(source_features, query)
            anchors.append({'clip_time':sample['timestamp'], 'side':sample['side'],
                'query_path':sample['path'], 'matches':matches,
                'offset':matches[0]['timestamp']-sample['timestamp']})
        interval, inliers = derive_interval(anchors, entry['duration'])
        status = 'unresolved' if interval is None else 'pixel-aligned-needs-visual-verification'
        result = {'video_id':vid,'url':'https://www.youtube.com/watch?v='+vid,
                  'title':entry['title'],'published_duration':entry['duration'],
                  'status':status,'source_interval':interval,'anchors':anchors,
                  'inlier_count':len(inliers), 'inlier_anchors':inliers,
                  'reference_note':'Independent published-clip alignment; source cache1s grid gives approximately1s resolution. Original manual checkpoint unchanged.'}
        results.append(result)
        (out / 'alignment.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='anchors'}),flush=True)
    return results


if __name__ == '__main__':
    main()
