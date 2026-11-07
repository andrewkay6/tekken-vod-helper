import json
import math
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tekken_vod_helper import boundary_reasoning
from tekken_vod_helper.boundary_workflow import detect
from tekken_vod_helper.boundary_cli import build_parser
from tekken_vod_helper.boundary_media import VisionClient


class BoundaryProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")
        self.policy = os.path.join(self.tmp.name, "cutting-policy.md")
        with open(self.policy, "w") as f:
            f.write("POLICY TEXT")

    def _mock_vision(self, calls=None):
        captured = {"init": None}
        class MockVisionClient:
            def __init__(self, **kwargs):
                captured["init"] = kwargs
                self.calls = 0
                self.max_requests = kwargs.get("max_requests", 300)
                self.usage = {"frames": 0, "requests": 0}
                self.endpoint = kwargs.get("endpoint", "")
                self.model = kwargs.get("model", "")
                self.output_dir = kwargs.get("output_dir", "")

            def classify(self, frames):
                self.calls += 1
                self.usage["requests"] += 1
                self.usage["frames"] += len(frames)
                return [
                    {"timestamp": float(f.get("timestamp", i)), "state": "stage_select", "confidence": 0.9, "player1": "", "player2": "", "character1": "", "character2": ""}
                    for i, f in enumerate(frames)
                ]
        return MockVisionClient, captured

    def _mock_sample(self, interval=1.0, max_frames=100):
        def fake_sample(source, prefix, start=0, end=None, interval=1, max_frames=None, ffmpeg_path=""):
            dur = float(source.get("duration", 0.0))
            if end is None:
                end = dur
            n = int(math.ceil((end - start) / float(interval)))
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
        return fake_sample

    def _mock_adjudicator(self):
        adj_calls = {"init": None, "propose": None}
        class MockAdjudicator:
            def __init__(self, **kwargs):
                adj_calls["init"] = kwargs
                self.calls = 0
            def propose_windowed(self, samples, policy_text):
                adj_calls["propose"] = (len(samples), policy_text)
                self.calls += 1
                return [
                    {"start": 0.0, "end": 10.0, "player1": "", "player2": "", "character1": "", "character2": ""}
                ], [], {"algorithm": "temporal-mock", "policy_hash": "mock", "model_calls": self.calls}
        return MockAdjudicator, adj_calls

    def test_recognition_options_forwarded_to_visionclient(self):
        src = {"source": "yt:fake", "uri": "unused", "duration": 20.0}
        MockVision, captured = self._mock_vision()
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=self._mock_sample(interval=1.0)), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=MockVision), \
             patch("tekken_vod_helper.boundary_workflow._POLICY", Path(self.policy)):
            detect(
                "yt:fake", self.out, interval=2, fine_step=1, max_frames=100, max_requests=100, cache_fine=True, max_cache_frames=50, recognition_crops=True, vision_thinking=False
            )
        init = captured["init"]
        self.assertEqual(init.get("crop_regions"), True)
        self.assertEqual(init.get("enable_thinking"), False)

    def test_budget_sum_and_reservation(self):
        src = {"source": "yt:fake", "uri": "unused", "duration": 20.0}
        MockVision, captured = self._mock_vision()
        MockAdj, adj_calls = self._mock_adjudicator()
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=self._mock_sample(interval=1.0)), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=MockVision), \
             patch("tekken_vod_helper.boundary_reasoning.TemporalAdjudicator", new=MockAdj), \
             patch("tekken_vod_helper.boundary_workflow._POLICY", Path(self.policy)):
            res = detect(
                "yt:fake", self.out, interval=2, fine_step=1, max_frames=100, max_requests=100,
                cache_fine=True, max_cache_frames=50, temporal=True, adjudication_requests=1
            )
        self.assertEqual(res["vision_model_calls"], 2)
        self.assertEqual(res["adjudication_model_calls"], 1)
        self.assertEqual(res["model_calls"], 3)
        self.assertEqual(captured["init"]["max_requests"], 99)
        self.assertEqual(adj_calls["init"]["max_requests"], 1)

    def test_adjudication_allowance_is_reserved_from_the_request_budget(self):
        """The temporal allowance is taken out of --max-requests instead of assumed free."""
        src = {"source": "yt:fake", "uri": "unused", "duration": 20.0}
        MockVision, captured = self._mock_vision()
        MockAdj, adj_calls = self._mock_adjudicator()
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=self._mock_sample(interval=1.0)), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=MockVision), \
             patch("tekken_vod_helper.boundary_reasoning.TemporalAdjudicator", new=MockAdj), \
             patch("tekken_vod_helper.boundary_workflow._POLICY", Path(self.policy)):
            detect("yt:fake", self.out, interval=2, fine_step=1, max_frames=100, max_requests=100,
                   cache_fine=True, max_cache_frames=50, temporal=True)
        default = boundary_reasoning.DEFAULT_ADJUDICATION_REQUESTS
        self.assertEqual(captured["init"]["max_requests"], 100 - default)
        self.assertEqual(adj_calls["init"]["max_requests"], default)

    def test_temporal_rejected_when_max_requests_less_than_two(self):
        src = {"source": "yt:fake", "uri": "unused", "duration": 20.0}
        MockVision, captured = self._mock_vision()
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=self._mock_sample(interval=1.0)), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=MockVision), \
             patch("tekken_vod_helper.boundary_workflow._POLICY", Path(self.policy)):
            with self.assertRaises(ValueError) as ctx:
                detect(
                    "yt:fake", self.out, interval=2, fine_step=1, max_frames=100, max_requests=1, cache_fine=True, max_cache_frames=50, temporal=True
                )
        self.assertIn("max_requests", str(ctx.exception))
        self.assertIsNone(captured["init"])

    def test_policy_record_stability(self):
        src = {"source": "yt:fake", "uri": "unused", "duration": 20.0}
        MockVision, captured = self._mock_vision()
        MockAdj, adj_calls = self._mock_adjudicator()
        policy_obj = Path(self.policy)
        original_classify = MockVision.classify
        def mutate_policy(client, frames):
            Path(self.policy).write_text("EDITED DURING RUN")
            return original_classify(client, frames)
        MockVision.classify = mutate_policy
        with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=src), \
             patch("tekken_vod_helper.boundary_workflow.sample_frames", side_effect=self._mock_sample(interval=1.0)), \
             patch("tekken_vod_helper.boundary_workflow.VisionClient", new=MockVision), \
             patch("tekken_vod_helper.boundary_reasoning.TemporalAdjudicator", new=MockAdj), \
             patch("tekken_vod_helper.boundary_workflow._POLICY", policy_obj):
            res = detect(
                "yt:fake", self.out, interval=2, fine_step=1, max_frames=100, max_requests=100, cache_fine=True, max_cache_frames=50, temporal=True
            )
            expected_hash = __import__("hashlib").sha256(b"POLICY TEXT").hexdigest()[:16]
            self.assertEqual(res["policy_hash"], expected_hash)
            self.assertEqual(adj_calls["propose"][1], "POLICY TEXT")

    def test_cli_detect_flags(self):
        parser = build_parser()
        args = parser.parse_args(["detect", "--recognition-crops", "--no-vision-thinking", "--temporal", "src", "--output-dir", "/tmp"])
        self.assertTrue(args.recognition_crops)
        self.assertFalse(args.vision_thinking)
        self.assertTrue(args.temporal)

    def test_cli_backtest_flags(self):
        parser = build_parser()
        args = parser.parse_args(["backtest-boundaries", "--recognition-crops", "--no-vision-thinking", "--temporal", "src", "--truth", "/tmp/t.json", "--output-dir", "/tmp"])
        self.assertTrue(args.recognition_crops)
        self.assertFalse(args.vision_thinking)
        self.assertTrue(args.temporal)


if __name__ == "__main__":
    unittest.main()
