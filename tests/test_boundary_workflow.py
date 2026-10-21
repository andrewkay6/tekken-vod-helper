"""Tests for boundary_workflow."""
import json
import os
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tekken_vod_helper.boundary_workflow import (
    propose_from_timeline, detect, backtest, score_saved_predictions, _validate_match_list, _refine,
)


def _s(t, state, conf=0.9, p1="", p2="", c1="", c2="", ev=""):
    return {"timestamp": t, "state": state, "confidence": conf,
            "player1": p1, "player2": p2, "character1": c1, "character2": c2, "evidence": ev}


class TestPropose(unittest.TestCase):
    def test_grouping_across_games(self):
        """Repeated stage_select within a set does NOT split."""
        samples = [_s(0, "stage_select"), _s(5, "gameplay"), _s(30, "post_match_menu"),
                   _s(35, "stage_select"), _s(40, "gameplay"), _s(70, "post_match_menu"),
                   _s(80, "controller_prompt"), _s(85, "controller_prompt")]
        m, r = propose_from_timeline(samples)
        self.assertEqual(len(m), 1)
        self.assertAlmostEqual(m[0]["start"], 0.0)
        self.assertAlmostEqual(m[0]["end"], 80.0)

    def test_stable_endpoint_not_generic_menu(self):
        """post_match_menu alone does not close a set."""
        samples = [_s(0, "stage_select"), _s(5, "gameplay"), _s(40, "post_match_menu"),
                   _s(50, "post_match_menu")]
        m, r = propose_from_timeline(samples)
        self.assertEqual(len(m), 0)
        self.assertTrue(any(x["type"] == "unterminated" for x in r))

    def test_partial_abstention(self):
        """Unknown at tail becomes review, no invented end."""
        samples = [_s(0, "stage_select"), _s(5, "gameplay"),
                   _s(40, "unknown", conf=0.9), _s(50, "unknown", conf=0.9)]
        m, r = propose_from_timeline(samples)
        self.assertEqual(len(m), 0)

    def test_reset_same_players(self):
        """Two sets with same players are both detected."""
        s = [_s(0, "stage_select", p1="A", p2="B"), _s(5, "gameplay", p1="A", p2="B"),
             _s(60, "controller_prompt"), _s(65, "controller_prompt"),
             _s(70, "stage_select", p1="A", p2="B"), _s(75, "gameplay", p1="A", p2="B"),
             _s(140, "controller_prompt"), _s(145, "controller_prompt")]
        m, r = propose_from_timeline(s)
        self.assertEqual(len(m), 2)
        self.assertAlmostEqual(m[0]["start"], 0.0)
        self.assertAlmostEqual(m[1]["start"], 70.0)

    def test_setup_only_30s_no_match(self):
        """Setup-only >=30 seconds emits possible_false_start, no match."""
        samples = [_s(0, "character_select"), _s(5, "stage_select"), _s(10, "vs_screen"),
                   _s(15, "stage_intro"), _s(20, "post_match_menu"), _s(30, "post_match_menu"),
                   _s(35, "controller_prompt"), _s(40, "controller_prompt")]
        m, r = propose_from_timeline(samples)
        self.assertEqual(len(m), 0)
        self.assertTrue(any(x["type"] == "possible_false_start" for x in r))

    def test_intro_vs_then_gameplay_still_matches(self):
        """Intro/VS in potential state does not break matching when gameplay follows."""
        samples = [_s(0, "stage_select"), _s(5, "stage_intro"), _s(10, "vs_screen"),
                   _s(15, "gameplay"), _s(45, "post_match_menu"), _s(60, "controller_prompt"),
                   _s(65, "controller_prompt")]
        m, r = propose_from_timeline(samples)
        self.assertEqual(len(m), 1)
        self.assertAlmostEqual(m[0]["start"], 0.0)

    def test_ordinary_gameplay_match_has_validity_review(self):
        """Ordinary gameplay match includes bracket_validity_unverified review."""
        samples = [_s(0, "stage_select"), _s(5, "gameplay"),
                   _s(40, "post_match_menu"), _s(50, "controller_prompt"), _s(55, "controller_prompt")]
        m, r = propose_from_timeline(samples)
        self.assertEqual(len(m), 1)
        self.assertTrue(any(x["type"] == "bracket_validity_unverified" for x in r))
        vr = [x for x in r if x["type"] == "bracket_validity_unverified"][0]
        self.assertEqual(vr["start"], 0.0)
        self.assertEqual(vr["end"], 50.0)


class TestDetect(unittest.TestCase):
    @patch("tekken_vod_helper.boundary_workflow.VisionClient")
    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    @patch("tekken_vod_helper.boundary_workflow.resolve_source")
    def test_model_budget(self, mock_res, mock_sf, mock_vc):
        """Exceeding max_requests raises."""
        mock_res.return_value = {"uri": "/tmp/v.mp4", "duration": 100.0, "source": "/tmp/v.mp4"}
        mock_sf.return_value = [{"id": i, "timestamp": float(i * 5), "path": "/tmp/f.jpg"}
                                for i in range(20)]
        client = MagicMock()
        client.calls = 0
        client.max_requests = 1
        client.usage = []
        def _cls(frames):
            client.calls += 1
            if client.calls > client.max_requests:
                raise ValueError("Budget exhausted")
            return [{"id": f["id"], "timestamp": f["timestamp"], "path": f["path"],
                     "state": "unknown", "confidence": 0.5, "player1": "",
                     "player2": "", "character1": "", "character2": "", "evidence": ""}
                    for f in frames]
        client.classify.side_effect = _cls
        mock_vc.return_value = client
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                detect("/tmp/v.mp4", td, max_frames=200, batch_size=5, max_requests=1)

    @patch("tekken_vod_helper.boundary_workflow.VisionClient")
    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    @patch("tekken_vod_helper.boundary_workflow.resolve_source")
    def test_no_network_mocked(self, mock_res, mock_sf, mock_vc):
        """Full detect with mocked deps produces valid finite matches."""
        mock_res.return_value = {"uri": "/tmp/v.mp4", "duration": 200.0, "source": "/tmp/v.mp4"}
        mock_sf.return_value = [{"id": i, "timestamp": float(i * 5), "path": "/tmp/f.jpg"}
                                for i in range(40)]
        client = MagicMock()
        client.calls = 0
        client.max_requests = 300
        client.usage = []
        def _cls(frames):
            client.calls += 1
            out = []
            for f in frames:
                t = f["timestamp"]
                st = "unknown"
                if t < 10: st = "stage_select"
                elif t < 80: st = "gameplay"
                elif t < 90: st = "post_match_menu"
                elif t < 100: st = "controller_prompt"
                elif t < 150: st = "gameplay"
                else: st = "controller_prompt"
                out.append({"id": f["id"], "timestamp": t, "path": f["path"],
                            "state": st, "confidence": 0.95, "player1": "X",
                            "player2": "Y", "character1": "", "character2": "", "evidence": ""})
            return out
        client.classify.side_effect = _cls
        mock_vc.return_value = client
        with tempfile.TemporaryDirectory() as td:
            result = detect("/tmp/v.mp4", td, max_frames=50, fine_step=2)
            self.assertEqual(result["status"], "complete")
            self.assertGreaterEqual(result["match_count"], 1)
            for p, v in result["paths"].items():
                self.assertTrue(os.path.isfile(v), p)
            preds = json.loads(Path(result["paths"]["predictions"]).read_text())
            for m in preds:
                self.assertLess(m["start"], m["end"])
                self.assertTrue(math.isfinite(m["start"]))
                self.assertTrue(math.isfinite(m["end"]))

    @patch("tekken_vod_helper.boundary_workflow.VisionClient")
    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    @patch("tekken_vod_helper.boundary_workflow.resolve_source")
    def test_declared_range(self, mock_res, mock_sf, mock_vc):
        """duration param clamps the processed range."""
        mock_res.return_value = {"uri": "/tmp/v.mp4", "duration": 500.0, "source": "/tmp/v.mp4"}
        mock_sf.return_value = [{"id": 1, "timestamp": 0.0, "path": "/tmp/f.jpg"}]
        client = MagicMock(); client.calls = 0; client.max_requests = 300; client.usage = []
        client.classify.return_value = [{"id": 1, "timestamp": 0.0, "path": "/tmp/f.jpg",
                                         "state": "unknown", "confidence": 0.9, "player1": "",
                                         "player2": "", "character1": "", "character2": "", "evidence": ""}]
        mock_vc.return_value = client
        with tempfile.TemporaryDirectory() as td:
            r = detect("/tmp/v.mp4", td, duration=120.0)
            self.assertAlmostEqual(r["range"][1], 120.0)


class TestBacktest(unittest.TestCase):
    @patch("tekken_vod_helper.boundary_workflow.detect")
    def test_labels_loaded_after_detect(self, mock_detect):
        """Truth file is not read until detect returns."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir)
            pred_path = os.path.join(out_dir, "predictions.json")
            with open(pred_path, "w") as f:
                json.dump([], f)
            truth_path = os.path.join(td, "truth.json")
            with open(truth_path, "w") as f:
                json.dump([{"start": 0, "end": 60}], f)
            mock_detect.return_value = {"output_dir": out_dir, "match_count": 0,
                "review_count": 0, "model_calls": 0, "status": "complete",
                "range": [0.0, 100.0],
                "paths": {"predictions": pred_path}}
            with patch("tekken_vod_helper.boundary_scoring.score_matches",
                       return_value={"matched": 0, "total": 1}) as sm:
                result = backtest("/tmp/v.mp4", truth_path, out_dir)
            sm.assert_called_once()
            self.assertEqual(result["calibration"], "heldout")


class TestScoreSavedPredictions(unittest.TestCase):
    def test_omitted_final_end_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir)
            Path(out_dir, "project.tvh.json").write_text(json.dumps({"duration": 200.0, "matches": []}))
            Path(out_dir, "predictions.json").write_text(json.dumps([{"start": 0, "end": 200}]))
            tp = os.path.join(td, "truth.json")
            Path(tp).write_bytes(json.dumps({"duration": 200.0, "matches": [{"start": 0, "end": None}]}).encode("utf-8"))

            result = {"output_dir": out_dir, "match_count": 1, "review_count": 0,
                      "model_calls": 0, "status": "complete", "source_id": "",
                      "range": [0.0, 200.0], "paths": {"predictions": os.path.join(out_dir, "predictions.json"),
                                                        "project": os.path.join(out_dir, "project.tvh.json")}}

            report = score_saved_predictions(result, tp, out_dir)
            self.assertIn(0, report["derived_truth_ends"])
            self.assertEqual(report["cut_metrics"]["within_tolerance_start"], 1)
            self.assertEqual(report["cut_metrics"]["within_tolerance_end"], 1)

    def test_truth_bytes_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir)
            Path(out_dir, "project.tvh.json").write_text(json.dumps({"duration": 100.0, "matches": []}))
            Path(out_dir, "predictions.json").write_text(json.dumps([]))
            tp = os.path.join(td, "truth.json")
            raw_bytes = json.dumps({"duration": 100.0, "matches": [{"start": 10, "end": None}]}).encode("utf-8")
            Path(tp).write_bytes(raw_bytes)

            result = {"output_dir": out_dir, "match_count": 0, "review_count": 0,
                      "model_calls": 0, "status": "complete", "source_id": "",
                      "range": [0.0, 100.0], "paths": {"predictions": os.path.join(out_dir, "predictions.json"),
                                                        "project": os.path.join(out_dir, "project.tvh.json")}}

            score_saved_predictions(result, tp, out_dir)
            self.assertEqual(Path(tp).read_bytes(), raw_bytes)

    def test_calibration_source_vs_filename(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir)
            Path(out_dir, "project.tvh.json").write_text(json.dumps({"duration": 100.0, "matches": []}))
            Path(out_dir, "predictions.json").write_text(json.dumps([]))
            tp = os.path.join(td, "truth.json")
            Path(tp).write_text(json.dumps([]))

            r1 = {"output_dir": out_dir, "status": "complete", "source_id": "cal_test",
                  "range": [0.0, 100.0], "paths": {"predictions": os.path.join(out_dir, "predictions.json"),
                                                    "project": os.path.join(out_dir, "project.tvh.json")}}
            rep1 = score_saved_predictions(r1, tp, out_dir, source="W_rDl7ZWvuQ")
            self.assertEqual(rep1["calibration"], "calibration")

            r2 = {"output_dir": out_dir, "status": "complete", "source_id": "BB5",
                  "range": [0.0, 100.0], "paths": {"predictions": os.path.join(out_dir, "predictions.json"),
                                                    "project": os.path.join(out_dir, "project.tvh.json")}}
            rep2 = score_saved_predictions(r2, tp, out_dir, source="BB5_source")
            self.assertEqual(rep2["calibration"], "heldout")

    @patch("tekken_vod_helper.boundary_workflow.detect")
    def test_scoring_existing_predictions_no_detector(self, mock_detect):
        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir)
            Path(out_dir, "project.tvh.json").write_text(json.dumps({"duration": 100.0, "matches": []}))
            Path(out_dir, "predictions.json").write_text(json.dumps([]))
            tp = os.path.join(td, "truth.json")
            Path(tp).write_text(json.dumps([]))

            result = {"output_dir": out_dir, "status": "complete", "source_id": "",
                      "range": [0.0, 100.0], "paths": {"predictions": os.path.join(out_dir, "predictions.json"),
                                                        "project": os.path.join(out_dir, "project.tvh.json")}}
            score_saved_predictions(result, tp, out_dir)
            self.assertEqual(mock_detect.call_count, 0)


    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    def test_extraction_failures_stop_direct(self, mock_sf):
        samples = [{"timestamp": t, "state": "A" if t % 60 == 0 else "B", "confidence": 0.9} for t in range(0, 300, 30)]
        src = {"uri": "x", "duration": 300.0, "source": "x"}
        out = Path(tempfile.mkdtemp())
        client = MagicMock(max_requests=300, calls=0, usage=[])
        issues = []
        call_count = [0]
        def _sf(src, path, start, end, interval, max_frames, ffmpeg_path):
            call_count[0] += 1
            raise ValueError("fail")
        mock_sf.side_effect = _sf
        _refine(samples, src, out, 5, 1, 2000, client, "", issues=issues)
        self.assertEqual(call_count[0], 3)
        self.assertTrue(any(i["type"] == "refinement_failed" for i in issues))
        out.rmdir()

    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    def test_success_resets_failures_direct(self, mock_sf):
        samples = [{"timestamp": t, "state": "A" if t % 60 == 0 else "B", "confidence": 0.9} for t in range(0, 300, 30)]
        src = {"uri": "x", "duration": 300.0, "source": "x"}
        out = Path(tempfile.mkdtemp())
        client = MagicMock(max_requests=300, calls=0, usage=[])
        issues = []
        call_count = [0]
        def _sf(src, path, start, end, interval, max_frames, ffmpeg_path):
            call_count[0] += 1
            if call_count[0] in (1, 2, 4, 5):
                raise ValueError("fail")
            return [{"timestamp": 0, "state": "gameplay"}]
        mock_sf.side_effect = _sf
        _refine(samples, src, out, 5, 1, 2000, client, "", issues=issues)
        self.assertEqual(call_count[0], 9)
        self.assertFalse(any(i["type"] == "refinement_stopped" for i in issues))
        self.assertEqual(len([i for i in issues if i["type"] == "refinement_failed"]), 4)
        out.rmdir()

    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    def test_budget_exhausted_no_extraction_direct(self, mock_sf):
        samples = [{"timestamp": t, "state": "A" if t % 60 == 0 else "B", "confidence": 0.9} for t in range(0, 300, 30)]
        src = {"uri": "x", "duration": 300.0, "source": "x"}
        out = Path(tempfile.mkdtemp())
        client = MagicMock(max_requests=300, calls=0, usage=[])
        client.calls = client.max_requests
        issues = []
        call_count = [0]
        def _sf(src, path, start, end, interval, max_frames, ffmpeg_path):
            call_count[0] += 1
            raise ValueError("fail")
        mock_sf.side_effect = _sf
        _refine(samples, src, out, 5, 1, 2000, client, "", issues=issues)
        self.assertEqual(call_count[0], 0)
        self.assertTrue(any(i["type"] == "refinement_stopped" for i in issues))
        out.rmdir()

    @patch("tekken_vod_helper.boundary_workflow.VisionClient")
    @patch("tekken_vod_helper.boundary_workflow.sample_frames")
    @patch("tekken_vod_helper.boundary_workflow.resolve_source")
    @patch("tekken_vod_helper.boundary_workflow.propose_from_timeline")
    def test_persists_review_issues_and_degraded_status(self, mock_propose, mock_res, mock_sf, mock_vc):
        mock_res.return_value = {"uri": "x", "duration": 200.0, "source": "x"}
        mock_sf.return_value = [{"id": i, "timestamp": float(i * 5), "path": "f.jpg", "state": "gameplay", "confidence": 0.9} for i in range(10)]
        client = MagicMock()
        client.calls = 0
        client.max_requests = 300
        client.usage = []
        client.classify.return_value = [{"id": i, "timestamp": 0.0, "state": "gameplay", "confidence": 0.9} for i in range(10)]
        mock_vc.return_value = client
        mock_propose.return_value = ([], [])

        def mock_refine(results, src, out, coarse, fine, max_frames, client, ff, issues=None, **kwargs):
            if issues is not None:
                issues.append({"type": "test_issue"})
            return results

        import tekken_vod_helper.boundary_workflow as bw
        old_refine = bw._refine
        bw._refine = mock_refine
        try:
            with tempfile.TemporaryDirectory() as td:
                result = detect("/tmp/v.mp4", td, max_frames=200, batch_size=5)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["refinement_status"], "degraded")
                review = json.loads((Path(td) / "review.json").read_text())
                self.assertTrue(any(i["type"] == "test_issue" for i in review["items"]))
        finally:
            bw._refine = old_refine

if __name__ == "__main__":
    unittest.main()
