"""Tests for the master-resolution crop path: identity proved, geometry stated, digests written.

The synthetic master is 640x360 at 30 fps with a burned-in frame counter, so a "cached" frame can be made
by writing one master frame out as a JPEG and the right answer to "which master frame is this?" is known
independently of the code under test. The real recording is only touched by the arithmetic test, which
needs no decoder.
"""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_pips as bp
from tekken_vod_helper import boundary_source as bs

FPS = 30
SECONDS = 5.0


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not installed")


@pytest.fixture(scope="module")
def master(tmp_path_factory):
    """A short clip whose every frame is numbered, so a frame number is checkable by eye."""
    root = tmp_path_factory.mktemp("source-master")
    path = root / "master.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate={}:duration={}".format(FPS, SECONDS),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture
def cache(master, tmp_path):
    """A cache manifest over chosen master frames, including one missing file and one uneven step."""
    from PIL import Image

    wanted = [0, 30, 90, 147]                          # ids 1..4 at 0.0, 1.0, 3.0, 4.9 s
    frames = []
    for cid, index in enumerate(wanted, start=1):
        stamp = index / float(FPS)
        entry = {"id": cid, "timestamp": round(stamp, 6),
                 "path": str(tmp_path / "frame-{:04d}.jpg".format(cid))}
        decoded = bs.decode_frames(master, stamp, stamp + 1.0 / FPS, tmp_path / "scratch",
                                   BS_FACTS, tag="c{}".format(cid))
        Image.open(decoded[0]["png"]).convert("RGB").save(entry["path"], format="JPEG", quality=90)
        frames.append(entry)
    shutil.rmtree(tmp_path / "scratch")
    (Path(frames[2]["path"])).unlink()                 # the manifest still lists it
    manifest = tmp_path / "sample-manifest.json"
    manifest.write_text(json.dumps({"source": str(master), "start": 0.0, "end": SECONDS,
                                    "interval": 1.0, "frames": frames}), encoding="utf-8")
    return {"path": manifest, "wanted": wanted, "frames": frames}


BS_FACTS = {"fps": float(FPS), "avg_frame_rate": "{}:1".format(FPS), "time_base": "1:30",
            "time_base_seconds": 1.0 / FPS, "width": 640, "height": 360, "fps_fraction": (FPS, 1)}


def test_master_index_arithmetic_uses_the_streams_own_rational_rate():
    """A drop-frame rate is not 60, and the difference is several frames by the end of a VOD.

    This is the arithmetic the cache-to-master mapping depends on. It is pinned here on the real BB9
    numbers because the earlier 1.000986 constant drifted 5.8 master frames by frame 6922, and a mapping
    that silently rounds through that error moves a label across a HUD transition.
    """
    facts = {"fps": 19001.0 / 317.0, "avg_frame_rate": "19001:317", "time_base_seconds": 1.0 / 19001.0}
    step = 317.0 / 19001.0                              # one master frame, in seconds
    assert bs.master_index_at(facts, 0.0) == 0
    assert bs.master_index_at(facts, step * 60) == 60
    assert bs.master_index_at(facts, step * 6921) == 6921
    # A whole second of cache steps: 1.001 s is 60.0006 master frames, so the cache is not on 1 Hz.
    assert bs.master_index_at(facts, 1.000999947) == 60
    assert abs(bs.seconds_of_index(facts, 415290) - 6928.4211355) < 1e-5
    assert abs(bs.seconds_of_index(facts, 415289) - 6928.4044524) < 1e-5
    assert math.isclose(facts["fps"] * step, 1.0, rel_tol=1e-12)
    # Why a constant copied out of a comment is a bug waiting to be paid for: the codebase's 1.000986 s
    # cache step is not the stream's 60 master frames, and the difference is five and three quarter master
    # frames by the end of this recording -- enough to land a label on the wrong side of a HUD change.
    drift_seconds = 6921 * abs(60 * step - 1.000986)
    assert 0.09 < drift_seconds < 0.11 and 5 < drift_seconds / step < 6.5


def test_probe_reports_the_stream_rather_than_a_guessed_constant(master):
    facts = bs.probe_master(master)
    assert (facts["width"], facts["height"]) == (640, 360)
    assert facts["avg_frame_rate"] == "30:1"
    assert facts["time_base"].count(":") == 1
    assert facts["declared_frames"] in (None, int(FPS * SECONDS))
    assert facts["bytes"] == Path(master).stat().st_size


def test_alignment_is_proved_by_content_and_names_the_rule_it_tested(master, cache, tmp_path):
    loaded = bs.load_cache_index(cache["path"])
    facts = bs.probe_master(master)
    out = bs.align(loaded, facts, master, tmp_path / "align")
    assert out["samples_matched"] == 3, "one cached file is missing, so exactly one sample must refuse"
    assert out["samples_refused"] and "not on disk" in out["samples_refused"][0]["because"]
    assert out["cache"]["missing_count"] == 1 and out["cache"]["missing_files"]
    assert out["rule_disagreed"] == 0, [r["fitted_rule_residual_seconds"] for r in out["per_frame"]]
    assert out["rule_agreed"] == 3
    for row in out["per_frame"]:
        expected = cache["wanted"][row["cache_id"] - 1]
        assert row["matched_index"] == expected, \
            "the content match picked frame {} for a cache frame made from {}".format(
                row["matched_index"], expected)
        assert abs(row["residual_seconds_vs_cache_timestamp"]) < 1.0 / FPS
        assert row["mad"] < 8.0, "a frame written from the master should match near-exactly"
        # The margin, not the absolute distance, is what entitles the render to claim one frame. On this
        # generator the neighbouring frame is only 0.35 gray levels away, so the honest answer is "+/-1
        # frame" -- and a thin margin that silently produced a tight claim is the failure mode here.
        expected_ambiguity = 0 if (row["mad_margin"] or 0) > bs.AMBIGUOUS_MARGIN_MAD else 1
        assert row["ambiguity_frames"] == expected_ambiguity, \
            "ambiguity did not follow the content margin: %s" % row
    assert out["uncertainty_seconds"] == pytest.approx(1.5 / FPS), \
        "a thin margin must widen the reported uncertainty to a frame plus the rounding half-frame"
    assert 0 < out["uncertainty_seconds"] <= 1.5 / FPS, \
        "the uncertainty must include the half-frame rounding, nothing wider"
    assert "60*(N-1)" not in json.dumps(out)


def test_a_sharper_stream_earns_a_tighter_uncertainty(tmp_path):
    """Same code, opposite evidence: when every frame is different, one frame is excludable.

    This is the control that keeps the previous test honest. The uncertainty figure is not a constant
    this module likes, it is a consequence of the measured margin -- so a generator whose frames differ
    wildly must come back with ambiguity 0 and half a frame, and if it did, the slow testsrc clip above
    would be the only reason to widen.
    """
    from PIL import Image

    path = tmp_path / "noise.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "nullsrc=s=640x360:d=2:r={},"
                    "geq=lum='random(1)*255':cb=128:cr=128".format(FPS),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    facts = bs.probe_master(path)
    entry_path = tmp_path / "cache-45.jpg"
    stamp = 45 / float(FPS)
    decoded = bs.decode_frames(path, stamp, stamp + 1.0 / FPS, tmp_path / "scratch", facts, tag="x")
    Image.open(decoded[0]["png"]).convert("RGB").save(entry_path, format="JPEG", quality=95)
    manifest = tmp_path / "sample-manifest.json"
    manifest.write_text(json.dumps({"source": str(path), "start": 0.0, "end": 2.0, "interval": 1.0,
                                    "frames": [{"id": 1, "timestamp": round(stamp, 6),
                                                "path": str(entry_path)}]}), encoding="utf-8")
    out = bs.align(bs.load_cache_index(manifest), facts, path, tmp_path / "align")
    row = out["per_frame"][0]
    assert row["matched_index"] == 45
    assert row["mad_margin"] > bs.AMBIGUOUS_MARGIN_MAD, "the fixture stopped discriminating"
    assert row["ambiguity_frames"] == 0
    assert abs(out["uncertainty_seconds"] - 0.5 / FPS) < 1e-6


def test_a_static_shot_reports_a_tie_instead_of_choosing_arbitrarily(tmp_path):
    """Every candidate identical is a real case: the broadcast holds a menu for minutes.

    Sorting (mad, frame) tuples crashes the moment two frames tie, and the honest answer to "which of
    these identical frames is the cached one?" is *none of them* -- so the row has to come back with a
    zero margin, an ambiguity of one frame, and a count of the candidates that tied with the winner.
    """
    from PIL import Image

    path = tmp_path / "static.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=#3a3a3a:s=640x360:d=2:r={}".format(FPS),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    facts = bs.probe_master(path)
    entry_path = tmp_path / "cache-static.jpg"
    stamp = 1.0
    decoded = bs.decode_frames(path, stamp, stamp + 1.0 / FPS, tmp_path / "scratch", facts, tag="s")
    Image.open(decoded[0]["png"]).convert("RGB").save(entry_path, format="JPEG", quality=95)
    manifest = tmp_path / "sample-manifest.json"
    manifest.write_text(json.dumps({"source": str(path), "start": 0.0, "end": 2.0, "interval": 1.0,
                                    "frames": [{"id": 1, "timestamp": stamp,
                                                "path": str(entry_path)}]}), encoding="utf-8")
    out = bs.align(bs.load_cache_index(manifest), facts, path, tmp_path / "align")
    row = out["per_frame"][0]
    assert row["tied_candidates"] > 10, "a static clip should tie most candidates: %s" % row
    assert row["mad_margin"] == pytest.approx(0.0, abs=1e-6)
    assert row["ambiguity_frames"] == 1, "a tie cannot entitle the render to claim one frame"
    assert abs(out["uncertainty_seconds"] - 1.5 / FPS) < 1e-6


def test_render_writes_both_groups_with_digests_and_refuses_what_it_cannot_place(master, cache,
                                                                                tmp_path):
    loaded = bs.load_cache_index(cache["path"])
    facts = bs.probe_master(master)
    alignment = bs.align(loaded, facts, master, tmp_path / "align2")
    out = tmp_path / "render"
    before = {p: p.stat().st_mtime_ns for p in Path(cache["path"]).parent.glob("*.jpg")}
    targets = [{"cache_id": 2, "role": "control"},
               {"at_seconds": 2.0333, "role": "sub-second instant the cache never held"},
               {"role": "asked for nothing"}]
    rendered = bs.render(targets, alignment, out)
    planned = [r for r in rendered["records"] if r.get("crops")]
    assert len(planned) == 2
    assert len(rendered["refusals"]) == 1 and "no at_seconds" in rendered["refusals"][0]["because"]
    assert planned[0]["content_matched"] is True, "a sampled cache frame is a proved identity"
    assert planned[0]["master_index"] == cache["wanted"][1]
    # The 2.0333 s target is one master frame after 2.0 s: the point of the whole path.
    assert planned[1]["master_index"] == 61 and planned[1]["content_matched"] is False
    for record in planned:
        assert sorted(record["crops"]) == ["LEFT", "RIGHT"]
        for name, part in record["crops"].items():
            box = part["box"]
            assert part["size"] == [box[2] - box[0], box[3] - box[1]]
            assert Path(part["path"]).is_file()
            assert len(part["sha256"]) == 64
            assert part["source_pixels"] == part["sent_pixels"], "scale 1 must not invent pixels"
            assert abs(part["box_fractions"][0] - dict(bp.GROUPS)[name][0]) < 1e-6, \
                "the source crop drifted off the geometry the cache arm validated"
        assert record["source_sha256"] != planned[0]["source_sha256"] or record is planned[0]
    assert (out / "src-{}-LEFT.png".format(planned[0]["master_index"])).is_file()
    assert (out / "src-{}-RIGHT.png".format(planned[1]["master_index"])).is_file()
    assert not (out / "frames").exists(), "the decoded full frames are a temp file, not an artifact"
    written = bs.write_artifacts(out, alignment, rendered, purpose="test")
    plan = json.loads(Path(written["plan"]).read_text(encoding="utf-8"))
    assert plan["inference_sent"] is False and plan["requests_planned"] == 2
    assert plan["images_planned"] == 4 and plan["arm_identity"]["version"] == bp.CROP_VERSION
    assert plan["requests"][0]["image_sha256"][0] == bs._sha256(plan["requests"][0]["image_paths"][0]), \
        "the plan's digest is not the digest of the image it names"
    assert {p: p.stat().st_mtime_ns for p in before} == before, "a cache file was touched"


def test_rendered_records_feed_the_existing_crop_request_plan_without_reusing_its_answers(master, cache,
                                                                                         tmp_path):
    """A source crop must plan as its own request, never inherit a cached frame's answer.

    Both arms ask the same question of the same box, so they share the arm and the prompt. What keeps them
    from sharing an *answer* is the request fingerprint, which hashes the frame identity, the source
    digest and the rendered size -- all three of which differ between a 640x360 cache JPEG and a native
    master frame.
    """
    loaded = bs.load_cache_index(cache["path"])
    facts = bs.probe_master(master)
    alignment = bs.align(loaded, facts, master, tmp_path / "align3")
    rendered = bs.render([{"cache_id": 2, "role": "control"}], alignment, tmp_path / "render3")
    source_records = bs.records_for_requests(rendered)
    cache_records = bp.build_records([{"frame": cache["frames"][1]["path"], "timestamp": 1.0}],
                                     tmp_path / "crops")
    from_cache = bp.group_fingerprint(cache_records, "crops")
    from_master = bp.group_fingerprint(source_records, "crops")
    assert from_cache != from_master, "a master crop would be accepted as the cache crop's answer"
    plan = bp.plan_requests(source_records, "crops", max_requests=8)
    assert plan["requests_planned"] == 1 and plan["images_total"] == 2
    assert plan["arm_identity"]["groups"]["LEFT"][0] == pytest.approx(bp.LEFT_BOX[0])
