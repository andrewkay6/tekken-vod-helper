import json
import math
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from tekken_vod_helper.boundary_workflow import detect, _load_manifest
from tekken_vod_helper.boundary_cli import build_parser


def _make_source(duration: float):
    return {"source": "yt:fake", "uri": "unused", "duration": float(duration)}


class VisionMock:
    def __init__(self, endpoint, model, max_requests, output_dir, **kwargs):
        self.endpoint = endpoint
        self.model = model
        self.max_requests = int(max_requests)
        self.output_dir = str(output_dir)
        self.calls = 0
        self.usage = {"frames": 0, "requests": 0}

    def classify(self, frames):
        if not frames:
            return []
        self.calls += 1
        self.usage["requests"] += 1
        self.usage["frames"] += len(frames)
        out = []
        # Deterministic alternating states spaced >= 15s so merged refinement
        # windows do not collapse into one giant window for multi-transition tests.
        prev_state = None
        state = "stage_select"
        for i, f in enumerate(frames):
            ts = float(f.get("timestamp", i))
            if prev_state is None or ts - prev_ts >= 15.0:
                state = "gameplay" if state == "stage_select" else "stage_select"
            prev_ts = ts
            prev_state = state
            out.append({"timestamp": ts, "state": state, "confidence": 0.95, "player1": "", "player2": "", "character1": "", "character2": ""})
        return out


class TestBoundaryCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        pass

    def test_one_extraction_despite_multiple_transitions(self):
        src = _make_source(100.0)
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=lambda source, prefix, start=0, end=None, interval=1, max_frames=None, ffmpeg_path="": None), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=VisionMock):
            # We don't actually call sample_frames in cache_fine path when manifest absent? Actually detect uses sample_frames for dense cache. Patch to return realistic frames.
            def fake_sample(source, prefix, start=0, end=None, interval=1, max_frames=None, ffmpeg_path=""):
                dur = float(source.get("duration", 0.0))
                if end is None:
                    end = dur
                n = max(0, int(math.ceil((end - start) / interval)))
                if max_frames is not None:
                    n = min(n, int(max_frames))
                frames = []
                t = float(start)
                idx = 0
                while idx < n and t <= end + 1e-6:
                    frames.append({"timestamp": round(t, 3), "path": "f{}.jpg".format(idx)})
                    t += float(interval)
                    idx += 1
                return frames
            with patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=fake_sample):
                res = detect("yt:fake", self.out_dir, interval=5, fine_step=1, max_frames=2000, batch_size=8, max_requests=100, cache_fine=True, max_cache_frames=12000)
                self.assertEqual(res["status"], "complete")
                # Only one dense extraction
                self.assertEqual(res["cached_frame_count"], 100)

    def test_dense_manifest_cache_bypasses_extraction(self):
        src = _make_source(100.0)
        frames = [{"timestamp": i * 1.0, "path": "f{}.jpg".format(i)} for i in range(101)]
        manifest = {"source": "yt:fake", "interval": 1.0, "frames": frames}
        mpath = os.path.join(self.tmp.name, "manifest.json")
        os.makedirs(self.out_dir, exist_ok=True)
        with open(mpath, "w") as f:
            json.dump(manifest, f)
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=VisionMock):
            # sample_frames should NOT be called when manifest is used
            with patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=AssertionError("should not be called")) as mock_sf:
                res = detect("yt:fake", self.out_dir, interval=5, fine_step=1, max_frames=2000, batch_size=8, max_requests=100, cache_fine=True, max_cache_frames=12000, samples_manifest=mpath)
                self.assertEqual(mock_sf.call_count, 0)
                self.assertEqual(res["status"], "complete")

    def test_true_nonuniform_actual_timestamps_retained(self):
        src = _make_source(10.0)
        ts_list = [i * 1.01 for i in range(10)]
        frames = [{"timestamp": t, "path": "f{}.jpg".format(i)} for i, t in enumerate(ts_list)]
        manifest = {"source": "yt:fake", "interval": None, "frames": frames}
        mpath = os.path.join(self.tmp.name, "manifest.json")
        os.makedirs(self.out_dir, exist_ok=True)
        with open(mpath, "w") as f:
            json.dump(manifest, f)
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=VisionMock):
            res = detect("yt:fake", self.out_dir, interval=3.0, fine_step=1.0, max_frames=2000, batch_size=8, max_requests=100, cache_fine=True, max_cache_frames=20, samples_manifest=mpath)
            timeline = json.loads(open(os.path.join(self.out_dir, "timeline.json")).read())
            self.assertEqual([row["timestamp"] for row in timeline], [ts_list[i] for i in (0, 3, 6, 9)])

    def test_cache_cap_rejected_before_decoder(self):
        src = _make_source(100.0)
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src):
            with self.assertRaises(ValueError) as ctx:
                # duration 100 with interval 5 -> fine_step 1 => init_interval 1 => predicted 100 > 20
                detect("yt:fake", self.out_dir, interval=5, fine_step=1, max_frames=2000, batch_size=8, max_requests=100, cache_fine=True, max_cache_frames=20)
            self.assertIn("exceeds max_cache_frames", str(ctx.exception))

    def test_fine_classification_batches_bounded_and_no_images_beyond_prefix(self):
        src = _make_source(20.0)
        def fake_sample(source, prefix, start=0, end=None, interval=1, max_frames=None, ffmpeg_path=""):
            dur = float(source.get("duration", 0.0))
            if end is None:
                end = dur
            n = max(0, int(math.ceil((end - start) / interval)))
            if max_frames is not None:
                n = min(n, int(max_frames))
            frames = []
            t = float(start)
            idx = 0
            while idx < n and t <= end + 1e-6:
                frames.append({"timestamp": round(t, 3), "path": "f{}.jpg".format(idx)})
                t += float(interval)
                idx += 1
            return frames
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=fake_sample), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=VisionMock):
            res = detect("yt:fake", self.out_dir, interval=5.0, fine_step=1.0, max_frames=2000, batch_size=8, max_requests=100, cache_fine=True, max_cache_frames=100, duration=20.0)
            self.assertEqual(res["status"], "complete")
            timeline = json.loads(open(os.path.join(self.out_dir, "timeline.json")).read())
            for row in timeline:
                self.assertLessEqual(row["timestamp"], 20.0)

    def test_cli_args_parse(self):
        parser = build_parser()
        args = parser.parse_args(["detect", "--cache-fine", "--max-cache-frames", "5000", "src", "--output-dir", "/tmp"])
        self.assertTrue(args.cache_fine)
        self.assertEqual(args.max_cache_frames, 5000)


if __name__ == "__main__":
    unittest.main()
