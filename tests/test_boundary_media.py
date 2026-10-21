import json, math, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from tekken_vod_helper.boundary_media import (
    ALLOWED_STATES, VisionClient, _FMT, _parse_pts, resolve_source, sample_frames,
)


class TestParsePts(unittest.TestCase):
    def test_extracts_timestamps(self):
        stderr = (
            "[Parsed_showinfo_1] n: 0 pts: 0 pts_time:0.000000 fmt:yuv420p\n"
            "[Parsed_showinfo_1] n: 120 pts: 12000 pts_time:5.000000 fmt:yuv420p\n"
            "[Parsed_showinfo_1] n: 240 pts: 24000 pts_time:10.000000 fmt:yuv420p\n"
        )
        self.assertEqual(_parse_pts(stderr), [0.0, 5.0, 10.0])

    def test_ignores_non_showinfo(self):
        self.assertEqual(_parse_pts("random log line"), [])


class TestResolveSource(unittest.TestCase):
    @mock.patch("tekken_vod_helper.boundary_media.probe_duration")
    def test_local(self, mock_probe):
        mock_probe.return_value = 120.5
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            path = f.name
        try:
            r = resolve_source(path)
            self.assertEqual(r["duration"], 120.5)
            self.assertEqual(r["source"], path)
            self.assertEqual(r["http_headers"], {})
        finally:
            os.unlink(path)

    def test_rejects_nonfinite(self):
        with self.assertRaises(ValueError):
            resolve_source("/nonexistent_file_xyz.mp4")

    @mock.patch("tekken_vod_helper.boundary_media.Path.exists", return_value=True)
    @mock.patch("tekken_vod_helper.boundary_media.shutil.which", return_value="node")
    @mock.patch("tekken_vod_helper.boundary_media.subprocess.run")
    def test_remote_selector_metadata_and_sanitized_errors(self, mock_run, mock_node, mock_exists):
        mock_run.return_value = mock.Mock(
            returncode=0, stdout=json.dumps({
                "id": "BB7", "duration": 3660.0, "url": "https://hls.example/playlist.m3u8",
                "format_id": "230", "protocol": "m3u8_native", "vcodec": "avc1.4D401E", "height": 360,
                "http_headers": {},
            }), stderr="")
        r = resolve_source("https://www.youtube.com/watch?v=BB7", yt_dlp="/usr/bin/yt-dlp")
        self.assertEqual(r["format_id"], "230")
        self.assertEqual(r["protocol"], "m3u8_native")
        self.assertEqual(r["vcodec"], "avc1.4D401E")
        self.assertEqual(r["height"], 360)
        cmd = mock_run.call_args[0][0]
        self.assertIn("-f", cmd)
        self.assertEqual(cmd[cmd.index("-f") + 1], _FMT)
        self.assertIn("--no-cookies", cmd)
        self.assertIn("--socket-timeout", cmd)
        self.assertIn("--retries", cmd)
        self.assertIn("--js-runtimes", cmd)
        self.assertEqual(mock_run.call_count, 1)

        mock_run.return_value = mock.Mock(
            returncode=1, stdout="", stderr="fatal: https://signed.example/x.m3u8?token=SECRET123 aborted")
        with self.assertRaises(ValueError) as ctx:
            resolve_source("https://www.youtube.com/watch?v=BB7", yt_dlp="/usr/bin/yt-dlp")
        self.assertNotIn("signed.example", str(ctx.exception))
        self.assertNotIn("SECRET123", str(ctx.exception))
        self.assertEqual(mock_run.call_count, 2)


class TestSampleFrames(unittest.TestCase):
    @mock.patch("tekken_vod_helper.boundary_media.require_tool")
    @mock.patch("tekken_vod_helper.boundary_media.subprocess.run")
    def test_cap_before_extraction(self, mock_run, mock_tool):
        mock_tool.return_value = "/usr/bin/ffmpeg"
        src = {"uri": "/tmp/test.mp4", "duration": 10000.0, "source": "/tmp/test.mp4"}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as ctx:
                sample_frames(src, d, start=0, end=10000, interval=5, max_frames=10)
            self.assertIn("exceeds", str(ctx.exception))
            self.assertEqual(mock_run.call_count, 0)  # no ffmpeg call

    @mock.patch("tekken_vod_helper.boundary_media.require_tool")
    @mock.patch("tekken_vod_helper.boundary_media.subprocess.run")
    def test_timestamps_from_showinfo(self, mock_run, mock_tool):
        mock_tool.return_value = "/usr/bin/ffmpeg"
        stderr = ("[showinfo] pts_time:0.000000\n"
                  "[showinfo] pts_time:5.000000\n"
                  "[showinfo] pts_time:10.000000\n")
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr=stderr)
        # Simulate produced files
        with tempfile.TemporaryDirectory() as d:
            # Pre-create stage files via side effect
            def fake_run(cmd, **kw):
                # Find the output pattern dir
                out = cmd[-1]  # pattern like /path/.stage_xxx/frame-%04d.jpg
                stage_dir = Path(out).parent
                for i in range(3):
                    (stage_dir / "frame-{:04d}.jpg".format(i + 1)).write_bytes(b"\xff\xd8")
                return mock.Mock(returncode=0, stdout="", stderr=stderr)
            mock_run.side_effect = fake_run
            src = {"uri": "/tmp/v.mp4", "duration": 30.0, "source": "yt:abc"}
            frames = sample_frames(src, d, start=10.0, end=25.0, interval=5)
            self.assertEqual(len(frames), 3)
            self.assertAlmostEqual(frames[0]["timestamp"], 10.0)
            self.assertAlmostEqual(frames[1]["timestamp"], 15.0)
            self.assertAlmostEqual(frames[2]["timestamp"], 20.0)
            # Manifest written
            manifest = json.loads((Path(d) / "sample-manifest.json").read_text())
            self.assertEqual(manifest["source"], "yt:abc")
            self.assertNotIn("http", manifest["source"])


class TestVisionClient(unittest.TestCase):
    def _make_frames(self, n=2):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xd9")
            p = f.name
        try:
            return [{"id": i + 1, "timestamp": float(i * 5), "path": p} for i in range(n)]
        except Exception:
            os.unlink(p)
            raise

    @mock.patch("urllib.request.urlopen")
    def test_budget_before_http(self, mock_urlopen):
        vc = VisionClient(max_requests=1)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":0.9,'
                                           '"player1":"","player2":"","character1":"","character2":"","evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        vc.classify(frames)
        self.assertEqual(vc.calls, 1)
        # Second call should raise before HTTP
        with self.assertRaises(ValueError) as ctx:
            vc.classify(frames)
        self.assertIn("Budget", str(ctx.exception))
        self.assertEqual(mock_urlopen.call_count, 1)  # only first call hit HTTP

    @mock.patch("urllib.request.urlopen")
    def test_invalid_state_rejected(self, mock_urlopen):
        vc = VisionClient(max_requests=10)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"boss_fight","confidence":0.9,'
                                           '"player1":"","player2":"","character1":"","character2":"","evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with self.assertRaises(ValueError) as ctx:
            vc.classify(frames)
        self.assertIn("Invalid state", str(ctx.exception))

    @mock.patch("urllib.request.urlopen")
    def test_invalid_confidence_rejected(self, mock_urlopen):
        vc = VisionClient(max_requests=10)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":1.5,'
                                           '"player1":"","player2":"","character1":"","character2":"","evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with self.assertRaises(ValueError) as ctx:
            vc.classify(frames)
        self.assertIn("Confidence", str(ctx.exception))

    @mock.patch("urllib.request.urlopen")
    def test_hallucinated_id_rejected(self, mock_urlopen):
        vc = VisionClient(max_requests=10)
        frames = self._make_frames(1)  # id=1
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":99,"state":"gameplay","confidence":0.5,'
                                           '"player1":"","player2":"","character1":"","character2":"","evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with self.assertRaises(ValueError) as ctx:
            vc.classify(frames)
        self.assertIn("Unknown frame id", str(ctx.exception))

    @mock.patch("urllib.request.urlopen")
    def test_policy_context_no_historical_json(self, mock_urlopen):
        """Verify the policy text sent contains no historical timestamps or JSON."""
        vc = VisionClient(max_requests=10, policy_path="docs/cutting-policy.md")
        policy_text = vc.policy_path.read_text(encoding="utf-8") if vc.policy_path.exists() else ""
        # The policy should not contain timestamp patterns like HH:MM:SS or raw JSON arrays
        self.assertNotIn("[", policy_text)  # no JSON arrays in rules-only doc
        self.assertNotIn("]", policy_text)
        self.assertNotIn("\"timestamp\"", policy_text)
        # Allowed states are mentioned
        self.assertIn("stage-select", policy_text)

    def test_parse_scores_present(self):
        vc = VisionClient(max_requests=1)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":0.9,'
                                           '"player1":"A","player2":"B","character1":"Kazuya","character2":"Heihachi",'
                                           '"score1":2,"score2":1,"evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen = mock.Mock()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", mock_urlopen):
            res = vc.classify(frames)
        self.assertEqual(res[0]["score1"], 2)
        self.assertEqual(res[0]["score2"], 1)

    def test_parse_scores_omitted(self):
        vc = VisionClient(max_requests=1)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":0.9,'
                                           '"player1":"A","player2":"B","character1":"Kazuya","character2":"Heihachi",'
                                           '"evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen = mock.Mock()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", mock_urlopen):
            res = vc.classify(frames)
        self.assertIsNone(res[0]["score1"])
        self.assertIsNone(res[0]["score2"])

    def test_parse_scores_invalid(self):
        vc = VisionClient(max_requests=1)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":0.9,'
                                           '"player1":"A","player2":"B","character1":"Kazuya","character2":"Heihachi",'
                                           '"score1":false,"score2":-1,"evidence":""}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen = mock.Mock()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", mock_urlopen):
            with self.assertRaises(ValueError) as ctx:
                vc.classify(frames)
            self.assertIn("Score", str(ctx.exception))

    def test_new_state_controller_assignment(self):
        vc = VisionClient(max_requests=1)
        frames = self._make_frames(1)
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '[{"id":1,"state":"controller_assignment","confidence":0.8,'
                                           '"player1":"","player2":"","character1":"","character2":"","score1":null,"score2":null,"evidence":"Assign controller dialog"}]'}}],
            "usage": {},
        }).encode()
        mock_urlopen = mock.Mock()
        mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
        mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", mock_urlopen):
            res = vc.classify(frames)
        self.assertEqual(res[0]["state"], "controller_assignment")

    def test_policy_snapshot_stable(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
            tf.write("# Policy\nTest rules.\n")
            tf.flush()
            policy_p = tf.name
        try:
            vc = VisionClient(max_requests=1, policy_path=policy_p)
            h1 = vc._policy_hash
            # Simulate disk edit after init
            with open(policy_p, "w", encoding="utf-8") as f:
                f.write("# Policy\nTest rules edited.\n")
            # vc should retain old hash
            self.assertEqual(vc._policy_hash, h1)
            # New instance gets new hash
            vc2 = VisionClient(max_requests=1, policy_path=policy_p)
            self.assertNotEqual(vc2._policy_hash, h1)
        finally:
            os.unlink(policy_p)

    def test_provenance_fingerprint_changes(self):
        vc = VisionClient(max_requests=1)
        # Mock classify to avoid API call but populate usage
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.Mock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":0.9,'
                                               '"player1":"","player2":"","character1":"","character2":"","score1":null,"score2":null,"evidence":""}]'}}],
                "usage": {},
            }).encode()
            mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
            mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
            vc.classify(self._make_frames(1))
        
        self.assertIn("fingerprint", vc.usage[0])
        original = vc.usage[0]["fingerprint"]
        self.assertEqual(len(original), 16)
        with mock.patch.object(vc, "_build_content", return_value=[{"type": "text", "text": "changed instructions"}]):
            self.assertNotEqual(vc._prompt_fingerprint(), original)

    def _make_pil_image(self, width=200, height=200, color="red"):
        try:
            from PIL import Image
            import io as stdio
            img = Image.new("RGB", (width, height), color)
            img.paste("green", (int(width * 0.5), 0, int(width * 0.8), int(height * 0.5)))
            buf = stdio.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except ImportError:
            return b"\xff\xd8\xff\xd9"

    def test_crop_regions_basic(self):
        vc = VisionClient(max_requests=1, crop_regions=True)
        img_bytes = self._make_pil_image(200, 200)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(img_bytes)
            p = f.name
        try:
            frames = [{"id": 1, "timestamp": 1.0, "path": p}]
            mock_resp = mock.Mock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '[{"id":1,"state":"gameplay","confidence":0.9,'
                                               '"player1":"","player2":"","character1":"","character2":"","evidence":""}]'}}],
                "usage": {},
            }).encode()
            mock_urlopen = mock.Mock()
            mock_urlopen.return_value.__enter__ = mock.Mock(return_value=mock_resp)
            mock_urlopen.return_value.__exit__ = mock.Mock(return_value=False)
            with mock.patch("urllib.request.urlopen", mock_urlopen):
                res = vc.classify(frames)
            self.assertEqual(res[0]["state"], "gameplay")
            call_data = json.loads(mock_urlopen.call_args[0][0].data)
            content = call_data["messages"][0]["content"]
            text_contents = [c for c in content if c.get("type") == "text"]
            region_labels = [c["text"] for c in text_contents if "Region" in c["text"]]
            self.assertIn("Region [broadcast-scoreboard]", region_labels)
            self.assertIn("Region [controller-overlay]", region_labels)
            import base64
            import io
            from PIL import Image
            payloads = [c["image_url"]["url"] for c in content if c.get("type") == "image_url"]
            sizes = [Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size for url in payloads]
            self.assertEqual(sizes, [(200, 200), (600, 72), (420, 264)])
            self.assertEqual(Path(p).read_bytes(), img_bytes)
        finally:
            os.unlink(p)

    def test_crop_regions_small_image_error(self):
        vc = VisionClient(max_requests=1, crop_regions=True)
        try:
            from PIL import Image
            import io as stdio
            img = Image.new("RGB", (50, 50), "red")
            buf = stdio.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_bytes = buf.read()
        except ImportError:
            self.skipTest("Pillow not available")
            
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(img_bytes)
            p = f.name
        try:
            frames = [{"id": 1, "timestamp": 1.0, "path": p}]
            with self.assertRaises(ValueError) as ctx:
                vc._build_content("", frames)
            self.assertIn("too small", str(ctx.exception).lower())
        finally:
            os.unlink(p)

    def test_crop_regions_fingerprint_differs(self):
        vc_off = VisionClient(max_requests=1, crop_regions=False)
        vc_on = VisionClient(max_requests=1, crop_regions=True)
        fp_off = vc_off._prompt_fingerprint()
        fp_on = vc_on._prompt_fingerprint()
        self.assertNotEqual(fp_off, fp_on)


if __name__ == "__main__":
    unittest.main()
