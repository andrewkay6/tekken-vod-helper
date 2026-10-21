"""Real-media regression for the filter-log/output-duration boundary mismatch."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tekken_vod_helper.boundary_media import sample_frames


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required for media integration")
class TestFractionalSampling(unittest.TestCase):
    def test_muxer_omitted_tail_keeps_valid_actual_pts(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "fractional.mp4"
            subprocess.run([
                ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=160x90:rate=30000/1001", "-t", "12",
                "-c:v", "libx264", "-preset", "ultrafast", str(video),
            ], check=True, capture_output=True, timeout=30)
            frames = sample_frames(
                {"uri": str(video), "duration": 12, "source": "synthetic"},
                str(root / "frames"), end=10.01, interval=1,
                max_frames=12, ffmpeg_path=ffmpeg,
            )
            self.assertEqual(len(frames), 10)
            self.assertAlmostEqual(frames[-1]["timestamp"], 9.009, places=5)
            self.assertTrue(all(Path(row["path"]).is_file() for row in frames))
            manifest = json.loads((root / "frames/sample-manifest.json").read_text())
            self.assertEqual(manifest["frames"], frames)
