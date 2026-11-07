"""Tests for temporal boundary reasoning (boundary_reasoning module)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import urllib.request

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_contract
from tekken_vod_helper.boundary_reasoning import (
    TemporalAdjudicationError,
    TemporalAdjudicator,
)

try:
    from tekken_vod_helper import agent_workflow  # noqa: F401
    _HAS_AGENT_WORKFLOW = True
except ImportError:
    _HAS_AGENT_WORKFLOW = False


def _make_response(content, usage=None):
    """Build a mock HTTP response object."""
    mock_resp = MagicMock()
    payload = {"choices": [{"message": {"content": content}}]}
    if usage:
        payload["usage"] = usage
    mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_timeline(n=10, base_ts=0.0, interval=5.0):
    """Create a minimal timeline of n observations."""
    timeline = []
    for i in range(n):
        timeline.append({
            "timestamp": base_ts + i * interval,
            "state": "unknown",
            "confidence": 0.5,
            "player1": "",
            "player2": "",
            "evidence": "test frame",
        })
    return timeline


def _make_valid_two_set_timeline():
    """Timeline with two sets: stage_select -> gameplay -> controller_prompt repeated."""
    timeline = [
        {"timestamp": 0.0, "state": "controller_prompt", "confidence": 0.8, "player1": "", "player2": "", "evidence": "menu"},
        {"timestamp": 5.0, "state": "stage_select", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "stage grid"},
        {"timestamp": 10.0, "state": "character_select", "confidence": 0.9, "player1": "A", "player2": "B", "evidence": "chars"},
        {"timestamp": 15.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "fighting"},
        {"timestamp": 20.0, "state": "gameplay", "confidence": 0.93, "player1": "A", "player2": "B", "evidence": "fighting"},
        {"timestamp": 25.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "evidence": "press start"},
        # Second set
        {"timestamp": 30.0, "state": "stage_select", "confidence": 0.92, "player1": "C", "player2": "D", "evidence": "stage grid"},
        {"timestamp": 35.0, "state": "character_select", "confidence": 0.9, "player1": "C", "player2": "D", "evidence": "chars"},
        {"timestamp": 40.0, "state": "gameplay", "confidence": 0.95, "player1": "C", "player2": "D", "evidence": "fighting"},
        {"timestamp": 45.0, "state": "controller_prompt", "confidence": 0.85, "player1": "C", "player2": "D", "evidence": "press start"},
    ]
    return timeline


def _make_aborted_timeline():
    """Timeline with stage_select but no gameplay (aborted attempt)."""
    timeline = [
        {"timestamp": 0.0, "state": "stage_select", "confidence": 0.9, "player1": "A", "player2": "B", "evidence": "stage grid"},
        {"timestamp": 5.0, "state": "character_select", "confidence": 0.85, "player1": "A", "player2": "B", "evidence": "chars"},
        {"timestamp": 10.0, "state": "controller_prompt", "confidence": 0.8, "player1": "A", "player2": "B", "evidence": "press start, no gameplay"},
    ]
    return timeline


class TestTemporalAdjudicatorValidation(unittest.TestCase):
    """Unit tests for validation logic with mocked HTTP."""

    def setUp(self):
        self.endpoint = "http://mock:8010/v1/chat/completions"
        self.model = "test-model"

    def _adjudicator(self, output_dir=None):
        return TemporalAdjudicator(
            endpoint=self.endpoint,
            model=self.model,
            max_requests=1,
            timeout=30,
            output_dir=output_dir,
        )

    def test_connection_accepts_base_or_completion_url(self):
        for suffix in ("", "/v1", "/v1/chat/completions"):
            with self.subTest(suffix=suffix), patch("urllib.request.urlopen", return_value=_make_response("[]")) as send:
                adj = TemporalAdjudicator("http://localhost:8010" + suffix, "local-model")
                adj.propose(_make_valid_two_set_timeline(), "")
                request = send.call_args[0][0]
                self.assertEqual(request.full_url, "http://localhost:8010/v1/chat/completions")
                self.assertTrue(request.get_header("Authorization").startswith("Bearer "))

    @unittest.skipIf(not _HAS_AGENT_WORKFLOW, "agent_workflow API module is not present on this branch")
    def test_candidates_are_accepted_by_match_application_api(self):
        from tekken_vod_helper.agent_workflow import _validate_match_list
        proposal = [{"start_id": "obs_0001", "end_id": "obs_0005",
                     "evidence_ids": ["obs_0001", "obs_0003", "obs_0005"],
                     "disposition": "candidate", "reason": "Observed stage, play and prompt."}]
        with patch("urllib.request.urlopen", return_value=_make_response(json.dumps(proposal))):
            matches, review, provenance = self._adjudicator().propose(_make_valid_two_set_timeline(), "")
        self.assertEqual(len(_validate_match_list(matches, 1000)), 1)

    def test_repeated_games_two_sets(self):
        """Two sets with different players should produce two candidate matches."""
        timeline = _make_valid_two_set_timeline()
        # obs_0001 is first stage_select, obs_0005 is first controller_prompt after gameplay
        # obs_0006 is second stage_select, obs_0009 is second controller_prompt
        model_response = json.dumps([
            {
                "start_id": "obs_0001", "end_id": "obs_0005",
                "evidence_ids": ["obs_0001", "obs_0003", "obs_0005"],
                "disposition": "candidate",
                "reason": "Stage select to controller prompt with observed gameplay."
            },
            {
                "start_id": "obs_0006", "end_id": "obs_0009",
                "evidence_ids": ["obs_0006", "obs_0008", "obs_0009"],
                "disposition": "candidate",
                "reason": "Second set with different players, stage select to controller prompt."
            },
        ])
        mock_resp = _make_response(model_response, {"total_tokens": 100})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            matches, review, prov = adj.propose(timeline, "test policy")

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["start"], 5.0)
        self.assertEqual(matches[0]["end"], 25.0)
        self.assertEqual(matches[1]["start"], 30.0)
        self.assertEqual(matches[1]["end"], 45.0)
        self.assertEqual(matches[0]["player1"], "A")
        self.assertEqual(matches[1]["player1"], "C")
        self.assertEqual(prov["algorithm"], "temporal-qwen-v1")
        self.assertEqual(prov["candidate_count"], 2)
        # bracket_validity_unverified should be in review
        bvu = [r for r in review if r["type"] == "bracket_validity_unverified"]
        self.assertEqual(len(bvu), 2)

    def test_same_player_reset_evidence(self):
        """Same players with score reset should be treated as separate sets."""
        timeline = [
            {"timestamp": 0.0, "state": "stage_select", "confidence": 0.95, "player1": "A", "player2": "B", "score1": 0, "score2": 0, "evidence": "stage grid, score 0-0"},
            {"timestamp": 5.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "score1": 0, "score2": 0, "evidence": "fighting"},
            {"timestamp": 10.0, "state": "gameplay", "confidence": 0.93, "player1": "A", "player2": "B", "score1": 1, "score2": 0, "evidence": "fighting, A wins round"},
            {"timestamp": 15.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "score1": 2, "score2": 0, "evidence": "set complete, press start"},
            # Grand final reset: same players, score back to 0-0
            {"timestamp": 20.0, "state": "stage_select", "confidence": 0.93, "player1": "A", "player2": "B", "score1": 0, "score2": 0, "evidence": "stage grid, score reset to 0-0, grand final"},
            {"timestamp": 25.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "score1": 0, "score2": 0, "evidence": "fighting again"},
            {"timestamp": 30.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "score1": 1, "score2": 0, "evidence": "press start after grand final game"},
        ]
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0003",
                "evidence_ids": ["obs_0000", "obs_0001", "obs_0002", "obs_0003"],
                "disposition": "candidate",
                "reason": "Set 1: stage select to controller prompt, score reached 2-0."
            },
            {
                "start_id": "obs_0004", "end_id": "obs_0006",
                "evidence_ids": ["obs_0004", "obs_0005", "obs_0006"],
                "disposition": "candidate",
                "reason": "Grand final reset: same players, score reset to 0-0, separate set."
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            matches, review, prov = adj.propose(timeline, "")

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["start"], 0.0)
        self.assertEqual(matches[0]["end"], 15.0)
        self.assertEqual(matches[1]["start"], 20.0)
        self.assertEqual(matches[1]["end"], 30.0)
        # Same players in both
        self.assertEqual(matches[0]["player1"], "A")
        self.assertEqual(matches[1]["player1"], "A")

    def test_aborted_attempt_preserved(self):
        """A stage_select with no gameplay should be marked aborted and go to review."""
        timeline = _make_aborted_timeline()
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0002",
                "evidence_ids": ["obs_0000", "obs_0002"],
                "disposition": "aborted",
                "reason": "Stage select followed by controller prompt with no observed gameplay; aborted attempt."
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            matches, review, prov = adj.propose(timeline, "")

        self.assertEqual(len(matches), 0)
        self.assertEqual(prov["candidate_count"], 0)
        # Should be in review as temporal_review
        temporal_reviews = [r for r in review if r["type"] == "temporal_review"]
        self.assertEqual(len(temporal_reviews), 1)
        self.assertEqual(temporal_reviews[0]["disposition"], "aborted")

    def test_unknown_id_rejection(self):
        """Model returning an observation_id not in the input should raise an error."""
        timeline = _make_aborted_timeline()
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0099",
                "evidence_ids": ["obs_0000"],
                "disposition": "candidate",
                "reason": "fake end"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("unknown end_id", str(ctx.exception))

    def test_fake_timestamp_cannot_be_introduced(self):
        """A model response referencing a timestamp-like string as an ID should be rejected."""
        timeline = _make_aborted_timeline()
        model_response = json.dumps([
            {
                "start_id": "123.456", "end_id": "789.012",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "invented timestamps"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("unknown start_id", str(ctx.exception))

    def test_overlap_rejection(self):
        """Two candidate proposals that overlap should be rejected."""
        timeline = [
            {"timestamp": 0.0, "state": "stage_select", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "s1"},
            {"timestamp": 5.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "g1"},
            {"timestamp": 10.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "evidence": "c1"},
            {"timestamp": 15.0, "state": "stage_select", "confidence": 0.9, "player1": "A", "player2": "B", "evidence": "s2"},
            {"timestamp": 20.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "g2"},
            {"timestamp": 25.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "evidence": "c2"},
        ]
        # Overlapping: first ends at obs_0005, second starts at obs_0003 (which is before obs_0005)
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0005",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "first"
            },
            {
                "start_id": "obs_0003", "end_id": "obs_0005",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "second overlapping"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("overlap", str(ctx.exception).lower())

    def test_malformed_response_not_json(self):
        """Non-JSON model response should raise an error."""
        timeline = _make_aborted_timeline()
        mock_resp = _make_response("This is not JSON at all.")

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_malformed_response_wrong_disposition(self):
        """Invalid disposition value should be rejected."""
        timeline = _make_aborted_timeline()
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0002",
                "evidence_ids": [],
                "disposition": "confirmed",
                "reason": "bad disposition"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("invalid disposition", str(ctx.exception))

    def test_malformed_response_missing_fields(self):
        """Missing required fields should be rejected."""
        timeline = _make_aborted_timeline()
        model_response = json.dumps([
            {
                "start_id": "obs_0000",
                "disposition": "candidate",
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("missing required fields", str(ctx.exception))

    def test_a_start_without_a_stage_grid_is_partial_not_rejected(self):
        """A set whose opening grid was never broadcast is still a set.

        The old validator rejected this outright, and the rejection was the bug: on BB9 it deleted two
        labelled matches and published nothing in their place, while the model had found the play. The
        span is now accepted with its start edge marked partial, so a reviewer is sent to the seconds
        of tape that decide it.
        """
        timeline = [
            {"timestamp": 0.0, "state": "character_select", "confidence": 0.9, "player1": "A", "player2": "B", "evidence": "chars"},
            {"timestamp": 5.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "fight"},
            {"timestamp": 10.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "evidence": "prompt"},
        ]
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0002",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "started at char select"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            matches, review, provenance = adj.propose(timeline, "")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["partial"], ["start"])
        self.assertEqual(matches[0]["start_completeness"], "supporting")
        self.assertEqual(matches[0]["end_completeness"], "strong")
        self.assertEqual(matches[0]["edge_contract"], boundary_contract.CONTRACT_VERSION)

    def test_a_disconnect_ending_is_partial_not_rejected(self):
        """A set that ends because the player left is real footage and stays in the output."""
        timeline = [
            {"timestamp": 0.0, "state": "stage_select", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "stage"},
            {"timestamp": 5.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "fight"},
            {"timestamp": 10.0, "state": "controller_disconnected", "confidence": 0.8, "player1": "A", "player2": "B", "evidence": "disconnected"},
        ]
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0002",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "ended at disconnected"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            matches, review, provenance = adj.propose(timeline, "")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["partial"], ["end"])
        self.assertEqual(matches[0]["start_completeness"], "strong")
        self.assertEqual(matches[0]["end_completeness"], "supporting")

    def test_both_edges_inferred_is_still_published_as_partial(self):
        """Play on tape is never dropped because a screen is missing at both ends."""
        timeline = [
            {"timestamp": 0.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "fight"},
            {"timestamp": 5.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "fight"},
            {"timestamp": 10.0, "state": "gameplay", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "fight"},
        ]
        model_response = json.dumps([
            {"start_id": "obs_0000", "end_id": "obs_0002", "evidence_ids": [],
             "disposition": "candidate", "reason": "continuous play, no screens observed"},
        ])
        with patch("urllib.request.urlopen", return_value=_make_response(model_response)):
            adj = self._adjudicator()
            matches, review, provenance = adj.propose(timeline, "")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["partial"], ["start", "end"])
        self.assertIn("no screen", matches[0]["notes"])

    def test_candidate_requires_gameplay(self):
        """Candidate with no gameplay between start and end should be rejected."""
        timeline = [
            {"timestamp": 0.0, "state": "stage_select", "confidence": 0.95, "player1": "A", "player2": "B", "evidence": "stage"},
            {"timestamp": 5.0, "state": "character_select", "confidence": 0.9, "player1": "A", "player2": "B", "evidence": "chars"},
            {"timestamp": 10.0, "state": "controller_prompt", "confidence": 0.85, "player1": "A", "player2": "B", "evidence": "prompt"},
        ]
        model_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0002",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "no gameplay in between"
            },
        ])
        mock_resp = _make_response(model_response)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adj = self._adjudicator()
            with self.assertRaises(TemporalAdjudicationError) as ctx:
                adj.propose(timeline, "")
        self.assertIn("no observed gameplay", str(ctx.exception))

    def test_empty_timeline_rejected(self):
        """Empty timeline should raise an error."""
        adj = self._adjudicator()
        with self.assertRaises(TemporalAdjudicationError) as ctx:
            adj.propose([], "")
        self.assertIn("Empty timeline", str(ctx.exception))

    def test_non_finite_timestamp_rejected(self):
        """Non-finite timestamp in input should raise an error."""
        timeline = [
            {"timestamp": float("inf"), "state": "stage_select", "confidence": 0.9, "evidence": ""},
            {"timestamp": 5.0, "state": "gameplay", "confidence": 0.9, "evidence": ""},
        ]
        adj = self._adjudicator()
        with self.assertRaises(TemporalAdjudicationError) as ctx:
            adj.propose(timeline, "")
        self.assertIn("Non-finite timestamp", str(ctx.exception))

    def test_oversized_input_rejected(self):
        """Input exceeding character limit should raise an explicit error."""
        # Create a timeline with very long evidence strings to exceed limit
        timeline = []
        for i in range(10000):
            timeline.append({
                "timestamp": float(i),
                "state": "unknown",
                "confidence": 0.5,
                "player1": "A",
                "player2": "B",
                "evidence": "x" * 100,
            })
        adj = self._adjudicator()
        with self.assertRaises(TemporalAdjudicationError) as ctx:
            adj.propose(timeline, "policy")
        self.assertIn("too large", str(ctx.exception))

    def test_output_dir_artifacts(self):
        """When output_dir is set, adjudication artifacts should be saved."""
        timeline = _make_valid_two_set_timeline()
        model_response = json.dumps([
            {
                "start_id": "obs_0001", "end_id": "obs_0005",
                "evidence_ids": [],
                "disposition": "candidate",
                "reason": "valid set"
            },
        ])
        mock_resp = _make_response(model_response, {"total_tokens": 50})

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("urllib.request.urlopen", return_value=mock_resp):
                adj = self._adjudicator(output_dir=tmpdir)
                matches, review, prov = adj.propose(timeline, "")

            artifact_path = Path(tmpdir) / "adjudication.json"
            self.assertTrue(artifact_path.exists())
            data = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(data["algorithm"], "temporal-qwen-v1")
            self.assertEqual(len(data["proposals"]), 1)
            self.assertIn("prompt_fingerprint", data)
            self.assertIn("raw_response", data)
            # No base64 or signed URLs
            self.assertNotIn("base64", artifact_path.read_text(encoding="utf-8"))


class TestTemporalDetectIntegration(unittest.TestCase):
    """Integration test for detect() with temporal=True (mocked models)."""

    def test_temporal_detect_integration(self):
        """detect(temporal=True) should use temporal adjudication and record provenance."""
        from tekken_vod_helper.boundary_workflow import detect

        # Create a minimal timeline that the mocked vision client will return
        timeline = [
            {"timestamp": 0.0, "state": "stage_select", "confidence": 0.95, "player1": "P1", "player2": "P2", "evidence": "stage grid visible"},
            {"timestamp": 5.0, "state": "character_select", "confidence": 0.9, "player1": "P1", "player2": "P2", "evidence": "character grid"},
            {"timestamp": 10.0, "state": "gameplay", "confidence": 0.95, "player1": "P1", "player2": "P2", "evidence": "active combat"},
            {"timestamp": 15.0, "state": "gameplay", "confidence": 0.93, "player1": "P1", "player2": "P2", "evidence": "active combat"},
            {"timestamp": 20.0, "state": "controller_prompt", "confidence": 0.85, "player1": "P1", "player2": "P2", "evidence": "press start prompt"},
        ]

        temporal_response = json.dumps([
            {
                "start_id": "obs_0000", "end_id": "obs_0004",
                "evidence_ids": ["obs_0000", "obs_0002", "obs_0004"],
                "disposition": "candidate",
                "reason": "Stage select to controller prompt with observed gameplay."
            },
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock resolve_source to avoid actual file probing
            mock_src = {
                "uri": "/fake/video.mp4",
                "duration": 25.0,
                "source": "local",
            }

            # Mock sample_frames to return empty (we'll use samples_manifest)
            # Create a minimal manifest
            manifest_data = {
                "frames": [{"path": "/fake/frame_{}.jpg".format(i), "timestamp": float(i * 5)} for i in range(5)],
                "source": "local",
                "interval": 5.0,
            }
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w") as f:
                json.dump(manifest_data, f)

            # We need to mock the VisionClient.classify to return our timeline
            # and also mock urllib for the temporal adjudicator
            with patch("tekken_vod_helper.boundary_workflow.resolve_source", return_value=mock_src), \
                 patch("tekken_vod_helper.boundary_workflow._load_manifest", return_value=manifest_data["frames"]), \
                 patch("tekken_vod_helper.boundary_workflow.VisionClient") as MockVision, \
                 patch("tekken_vod_helper.boundary_workflow._refine", return_value=timeline), \
                 patch("urllib.request.urlopen") as mock_urlopen:

                mock_client = MockVision.return_value
                mock_client.calls = 0
                mock_client.max_requests = 100
                mock_client.usage = {"total_calls": 0, "total_tokens": 0}
                mock_client.classify.return_value = timeline

                # Set up the temporal adjudicator mock response
                mock_resp = _make_response(temporal_response, {"total_tokens": 80})
                mock_urlopen.return_value = mock_resp

                result = detect(
                    "/fake/video.mp4",
                    os.path.join(tmpdir, "out"),
                    interval=5.0,
                    samples_manifest=manifest_path,
                    temporal=True,
                    endpoint="http://mock:8010/v1/chat/completions",
                    model="test-model",
                )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["match_count"], 1)
            self.assertEqual(result["proposal_algorithm"], "temporal-qwen-v2")
            # A timeline that fits one window must still cost exactly one adjudication call.
            adjudication = result["adjudication"]
            self.assertEqual(adjudication["window_count"], 1)
            self.assertEqual(adjudication["model_calls"], 1)
            self.assertEqual(result["adjudication_model_calls"], 1)

            # Verify predictions were written
            pred_path = Path(result["paths"]["predictions"])
            self.assertTrue(pred_path.exists())
            preds = json.loads(pred_path.read_text(encoding="utf-8"))
            self.assertEqual(len(preds), 1)
            self.assertEqual(preds[0]["start"], 0.0)
            self.assertEqual(preds[0]["end"], 20.0)

            # Verify review has bracket_validity_unverified
            review_path = Path(result["paths"]["review"])
            self.assertTrue(review_path.exists())
            review_data = json.loads(review_path.read_text(encoding="utf-8"))
            bvu = [i for i in review_data["items"] if i.get("type") == "bracket_validity_unverified"]
            self.assertEqual(len(bvu), 1)

            # Verify adjudication artifact exists
            adj_path = Path(result["output_dir"]) / "adjudication.json"
            self.assertTrue(adj_path.exists())


if __name__ == "__main__":
    unittest.main()
