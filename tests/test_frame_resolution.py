"""Resolving an instant to a cached frame by manifest lookup, not by filename arithmetic.

The bug these tests exist for: a blinded edge sheet spelled its filenames as ``frame-%04d.jpg %
(int(t) + 1)``, which is only true when the cache step is exactly 1.000 s. BB9's cache is the source's
1000/1001 frame interval, so the claimed frame drifted later than the label as the recording ran -- 5.005 s
at 5495.490 and 6.006 s at 6486.480 -- and every clock reading taken from those tiles described a different
instant than the one it was credited with. Everything here is offline: manifests are written into
``tmp_path``, and the one real-recording test reads only the saved manifest and JPEG digests.
"""

import hashlib
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_source as bs

BB9_MANIFEST = Path("build/work/bb9/detect-1440p-v1/frames/sample-manifest.json")
#: The 1000/1001 s step a 59.94 Hz source produces when sampled at "one second".
RATE = 1001.0 / 1000.0


def write_cache(tmp_path, frames):
    """A cache manifest over real files, so path existence is part of what is under test."""
    entries = []
    for frame in frames:
        path = tmp_path / frame["name"]
        path.write_bytes(frame.get("bytes", b"\xff\xd8fakejpeg"))
        entries.append({"id": frame["id"], "timestamp": frame["timestamp"], "path": str(path)})
    manifest = tmp_path / "sample-manifest.json"
    manifest.write_text(json.dumps({"source": str(tmp_path / "master.mkv"), "start": 0.0,
                                    "end": entries[-1]["timestamp"] if entries else 0.0,
                                    "interval": 1.0, "frames": entries}), encoding="utf-8")
    return manifest


def secondly_cache(tmp_path, count=100):
    """A long recording sampled at a fractional rate: id ``n`` sits at ``(n-1) x 1.001`` seconds."""
    return write_cache(tmp_path, [{"id": index + 1, "name": "frame-%04d.jpg" % (index + 1),
                                   "timestamp": round(index * RATE, 6)}
                                  for index in range(count)])


def naive_frame_name(at_seconds):
    """The mapping that produced the wrong sheet, kept here so the test names its own enemy."""
    return "frame-%04d.jpg" % (int(at_seconds) + 1)


def test_a_fractional_step_makes_the_naive_filename_a_later_frame(tmp_path):
    """The regression itself: BB9's fifth set opens at 5495.490, where the naive name is 5.005 s late."""
    cache = secondly_cache(tmp_path, count=5600)
    wanted = round(5490 * RATE, 6)
    resolved = bs.resolve_frame(cache, wanted)
    assert resolved["id"] == 5491 and resolved["frame"] == "frame-5491.jpg"
    assert resolved["frame"] != naive_frame_name(wanted)
    assert resolved["offset_seconds"] == pytest.approx(0.0, abs=1e-6)
    assert resolved["within_tolerance"] is True


def test_the_naive_error_is_small_early_and_unbounded_late(tmp_path):
    """No constant offset fixes this, which is why it survived a spot check near the start of the tape."""
    cache = secondly_cache(tmp_path, count=6600)
    by_id = {int(entry["id"]): float(entry["timestamp"])
             for entry in json.loads(Path(cache).read_text(encoding="utf-8"))["frames"]}
    errors = []
    for at in (53.053, 790.79, 1588.587, 3723.72, 5495.49, 6486.48):
        naive_id = int(at) + 1
        errors.append((at, round(by_id[naive_id] - at, 3)))
    assert abs(errors[0][1]) < 0.06, "near the start the naive name is nearly right, which is why it '                                      'survived a spot check"
    assert errors[-1][1] > 6.0, "the drift is the recording's length, not a per-instant mistake"
    assert [error for _at, error in errors] == sorted(error for _at, error in errors)
    for at, error in errors:
        resolved = bs.resolve_frame(cache, at, digest=False)
        assert abs(resolved["offset_seconds"]) <= RATE / 2.0 + 1e-9
        assert resolved["frame"] != naive_frame_name(at) or error < 0.5, \
            "the sheet kept a frame up to %.3f s away from the instant it labelled" % error


def test_tolerance_is_half_the_cache_step_and_is_published(tmp_path):
    cache = secondly_cache(tmp_path, count=100)
    resolved = bs.resolve_frame(cache, 12.4, digest=False)
    assert resolved["tolerance_seconds"] == pytest.approx(RATE / 2.0, abs=1e-6)
    assert resolved["modal_step_seconds"] == pytest.approx(RATE, abs=1e-6)
    assert resolved["actual_at"] - 12.4 <= resolved["tolerance_seconds"] + 1e-9


def test_an_instant_the_cache_does_not_reach_is_refused_not_rounded(tmp_path):
    cache = write_cache(tmp_path, [{"id": 1, "name": "a.jpg", "timestamp": 0.0},
                                   {"id": 2, "name": "b.jpg", "timestamp": 1.0}])
    with pytest.raises(bs.FrameNotFound) as refused:
        bs.resolve_frame(cache, 1.9, digest=False)
    assert "outside the tolerance" in str(refused.value)
    disclosed = bs.resolve_frame(cache, 1.9, digest=False, disclose=True)
    assert disclosed["within_tolerance"] is False and disclosed["refused_because"]
    assert disclosed["frame"] == "b.jpg", "disclosure still names what it fell back to"
    assert disclosed["offset_seconds"] == pytest.approx(-0.9, abs=1e-9)


def test_a_manifest_entry_whose_file_is_gone_is_a_refusal_not_a_match(tmp_path):
    cache = write_cache(tmp_path, [{"id": 1, "name": "a.jpg", "timestamp": 0.0},
                                   {"id": 2, "name": "b.jpg", "timestamp": 1.0}])
    (tmp_path / "b.jpg").unlink()
    with pytest.raises(bs.FrameNotFound) as refused:
        bs.resolve_frame(cache, 1.0, digest=False)
    assert "not on disk" in str(refused.value)
    disclosed = bs.resolve_frame(cache, 1.0, digest=False, disclose=True)
    assert disclosed["within_tolerance"] is False
    assert disclosed["nearest_alternatives"], "the reviewer must see which frames do exist"


def test_two_equally_distant_frames_are_called_a_tie(tmp_path):
    cache = write_cache(tmp_path, [{"id": 1, "name": "a.jpg", "timestamp": 10.0},
                                   {"id": 2, "name": "b.jpg", "timestamp": 12.0},
                                   {"id": 3, "name": "c.jpg", "timestamp": 20.0}])
    resolved = bs.resolve_frame(cache, 11.0, digest=False, tolerance=1.5)
    assert resolved["ambiguous_tie"] is True
    assert not bs.resolve_frame(cache, 11.6, digest=False, tolerance=1.5)["ambiguous_tie"]


def test_a_frame_beside_a_hole_is_flagged_sparse(tmp_path):
    cache = write_cache(tmp_path, [{"id": 1, "name": "a.jpg", "timestamp": 0.0},
                                   {"id": 2, "name": "b.jpg", "timestamp": 1.0},
                                   {"id": 3, "name": "c.jpg", "timestamp": 2.0},
                                   {"id": 9, "name": "d.jpg", "timestamp": 21.0}])
    assert bs.resolve_frame(cache, 2.0, digest=False)["sparse_region"] is True
    assert bs.resolve_frame(cache, 1.0, digest=False)["sparse_region"] is False
    with pytest.raises(bs.FrameNotFound):
        bs.resolve_frame(cache, 12.0, digest=False), "nothing inside the hole may be borrowed"


def test_a_two_frame_cache_loads_at_all(tmp_path):
    """``load_cache_index`` took its median from the wrong list and died on any cache of two frames."""
    cache = write_cache(tmp_path, [{"id": 1, "name": "a.jpg", "timestamp": 0.0},
                                   {"id": 2, "name": "b.jpg", "timestamp": 1.0}])
    index = bs.load_cache_index(cache)
    assert index["irregular"]["step_median"] == 1.0 and index["irregular"]["uneven_step_count"] == 0
    assert bs.resolve_frame(cache, 0.4)["frame"] == "a.jpg"


def test_a_fractional_step_is_not_reported_as_uneven_steps(tmp_path):
    """The old median rounded whole seconds, so a 1.001 s cache looked like it jumped every other frame."""
    index = bs.load_cache_index(secondly_cache(tmp_path, count=400))
    assert index["irregular"]["uneven_step_count"] == 0
    assert index["irregular"]["step_median"] == pytest.approx(RATE, abs=1e-6)


def test_an_empty_index_is_a_refusal_rather_than_an_index_error(tmp_path):
    cache = tmp_path / "sample-manifest.json"
    cache.write_text(json.dumps({"source": "x", "frames": []}), encoding="utf-8")
    with pytest.raises(bs.FrameNotFound):
        bs.resolve_frame(cache, 5.0)


def test_resolve_frames_reports_every_instant_instead_of_stopping_at_the_first_miss(tmp_path):
    cache = secondly_cache(tmp_path, count=50)
    rows = bs.resolve_frames(cache, [round(10 * RATE, 3), 9999.0])
    assert [row["within_tolerance"] for row in rows] == [True, False]
    assert rows[0]["sha256"] == hashlib.sha256((tmp_path / "frame-0011.jpg").read_bytes()).hexdigest()


def test_the_digest_travels_with_the_frame_so_a_swapped_file_cannot_pass_unnoticed(tmp_path):
    cache = secondly_cache(tmp_path, count=10)
    first = bs.resolve_frame(cache, 3.0)
    (tmp_path / "frame-0004.jpg").write_bytes(b"different pixels")
    second = bs.resolve_frame(cache, 3.0)
    assert first["sha256"] != second["sha256"]
    assert first["path"] == second["path"], "identity is the manifest path; the digest is the proof"


def test_a_manifest_listed_out_of_order_still_resolves_to_the_right_frame(tmp_path):
    frames = [{"id": index + 1, "name": "frame-%04d.jpg" % (index + 1),
               "timestamp": round(index * RATE, 6)} for index in range(6)]
    frames = frames[::-1]
    manifest = write_cache(tmp_path, frames)
    assert bs.resolve_frame(manifest, round(4 * RATE, 6), digest=False)["id"] == 5


# ------------------------------------------------------------------ the real recording's own arithmetic

@pytest.mark.skipif(not BB9_MANIFEST.is_file(), reason="BB9 frame cache is not on this machine")
def test_bb9s_manifest_puts_the_disputed_labels_where_the_correction_says():
    """Pinned to the artifact that caused the error, not to a synthetic stand-in.

    ``blinded-edge-key.json`` claimed frame-5496.jpg for 5495.490 and frame-6487.jpg for 6486.480. The
    manifest puts those ids 5.005 s and 6.006 s later than the instants they were said to show, and the
    frames that actually match are 5491 and 6481.
    """
    cache = bs.load_cache_index(BB9_MANIFEST)
    assert cache["count"] == 6922
    for claimed_at, claimed_frame, correct_id in ((5495.490, "frame-5496.jpg", 5491),
                                                  (6486.480, "frame-6487.jpg", 6481),
                                                  (3723.720, "frame-3724.jpg", 3721),
                                                  (4464.460, "frame-4465.jpg", 4461)):
        resolved = bs.resolve_frame(cache, claimed_at)
        assert resolved["id"] == correct_id, "%s belongs to %s, not %s" % (
            claimed_at, resolved["frame"], claimed_frame)
        assert abs(resolved["offset_seconds"]) <= 0.001
        wrong = cache["by_id"][int(claimed_frame.split("-")[1].split(".")[0])]
        assert round(float(wrong["timestamp"]) - claimed_at, 3) >= 2.0, \
            "if the old id were only marginally wrong this would not be worth a regression test"


@pytest.mark.skipif(not BB9_MANIFEST.is_file(), reason="BB9 frame cache is not on this machine")
def test_every_predicted_boundary_on_bb9_has_a_frame_within_tolerance():
    """The resolver has to cover the recording it is used on, edge to edge, not just the disputed four."""
    cache = bs.load_cache_index(BB9_MANIFEST)
    project = json.loads(Path("build/work/bb9/review-ui-audit-2026-11-06/bb9-import.tvh.json")
                         .read_text(encoding="utf-8"))
    instants = [value for row in project["matches"] for value in (row["start"], row["end"])]
    instants += [0.0, float(project["duration"]) - 0.001]
    rows = bs.resolve_frames(cache, instants)
    assert all(row["within_tolerance"] for row in rows), \
        [row for row in rows if not row["within_tolerance"]][:3]
    assert len({row["frame"] for row in rows}) == len(rows), "two boundaries never share one frame here"
    assert all(row["modal_step_seconds"] == pytest.approx(RATE, abs=1e-4) for row in rows)
