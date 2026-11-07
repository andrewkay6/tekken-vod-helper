"""Source framing executed through the public command, with only the transport faked.

``tests/test_pip_crop_execution.py`` froze the rule that a probe is only tested by driving
``scripts/run_pip_crop_probe.py`` with the real client and ``_post`` replaced, because the first version
of that probe shipped broken exactly where a fake client could not reach. These tests hold to that: every
case below calls the script's ``main()`` for ``--prepare`` and ``--execute``, against a synthetic master
whose frames are decoded to PNG and annotated in the runtime shape that
``boundary_source.build_execution_set`` writes.

What is under test is the integration the review asked for, not the renderer: that a master-framed crop
runs through the same budget ledger, group persistence and scoring as a cache crop; that a control which
disagrees with the answer paired to **its own instant** stops the batch; and that two framings of one
instant are two requests rather than one duplicate.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_media as media
from tekken_vod_helper import boundary_pips as bp
from tekken_vod_helper import boundary_source as bs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_pip_crop_probe.py"
MODEL = "test-model"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")

# Three cache frames, answered three different ways by the run they came from. The pairing rules treat
# them differently, and that difference is the thing under test.
FRAME_IDS = [10, 11, 12]
SAVED = {                            # cache id -> (drawn, LEFT, RIGHT, confidence)
    10: (True, 1, 3, 0.90),          # both sides drawn and confident -> may carry an expectation
    11: (False, None, None, 0.90),   # an abstention                  -> no expectation
    12: (True, 2, 2, 0.62),          # counted, but not confidently   -> no expectation
}
CONTROL_EXPECTATION = {"LEFT": 1, "RIGHT": 3}


def run_script(monkeypatch, argv, transport=None, capsys=None):
    """Invoke the public script's main() in-process, with only the transport replaced."""
    spec = importlib.util.spec_from_file_location("source_probe_script", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "source_probe_script", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + argv)
    if transport is not None:
        # A callable stored on the class is not a descriptor, so client._post(body) reaches __call__
        # with the body and no stray self: the transport is the only thing faked here.
        monkeypatch.setattr(bp.PipReadClient, "_post", transport, raising=True)
    module.main()
    return module, (capsys.readouterr().out if capsys else "")


class ScriptedTransport:
    """One answer for every request in the run, chosen by the test, and a log of what left."""

    def __init__(self, drawn=True, left=1, right=3, confidence=0.9):
        self.answer = (drawn, left, right, confidence)
        self.bodies = []
        self.fail_after = None

    def __call__(self, body):
        self.bodies.append(body)
        if self.fail_after is not None and len(self.bodies) > self.fail_after:
            raise media.TransportUncertain("connection dropped after the request was sent")
        blob = json.dumps(body)
        asked = [(int(m[0]), m[1]) for m in re.findall(r"item (\d+) label=(\w+)", blob)]
        drawn, left, right, confidence = self.answer
        payload = []
        for item, label in asked:
            lit = {"LEFT": left, "RIGHT": right}.get(label, left)
            payload.append({"item": item, "label": label, "drawn": drawn,
                            "lit": lit if drawn else None,
                            # A drawn row with a count has to name the slots, or validation rejects the
                            # reply -- as it should -- and the test measures nothing.
                            "positions": list(range(1, (lit or 0) + 1)) if drawn else [],
                            "confidence": confidence})
        return {"choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 1400, "completion_tokens": 900}}


def make_master(path, seconds=12.0, rate=30):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate={}:duration={}".format(rate, seconds),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_master(tmp_path_factory.mktemp("source-exec") / "master.mp4")


def cache_at(video, stamp, out_dir, tag):
    """The master frame at ``stamp``, as both a decoded PNG and the cache JPEG made from it."""
    from PIL import Image

    facts = bs.probe_master(video)
    decoded = bs.decode_frames(video, stamp, stamp + 1.0 / 30, out_dir / "decoded", facts, tag=tag)
    frame = decoded[0]
    jpeg = out_dir / "frame-{}.jpg".format(tag)
    jpeg.parent.mkdir(parents=True, exist_ok=True)
    Image.open(frame["png"]).convert("RGB").save(jpeg, format="JPEG", quality=92)
    return facts, frame, jpeg


def ledger(out_dir, requested=3, cap=None):
    """A real temporary ledger, because --execute now refuses to spend attempts nothing counts.

    The cap is above the allowance on purpose: these tests are about master-frame identity, controls and
    abstentions, and one of them is about the guard, which brings its own budget arguments.
    """
    return ["--budget-file", str(Path(str(out_dir) + "/../attempts.json") if Path(str(out_dir)).name ==
                                 "run" else Path(out_dir) / "attempts.json"),
            "--budget-cap", str(cap if cap is not None else int(requested) + 32),
            "--budget-minutes", "60"]


@pytest.fixture
def source_run(tmp_path, video, monkeypatch, capsys):
    """A prepared, master-framed run of three requests: control, abstention probe, shaky probe."""
    work = tmp_path / "cache"
    groups = work / "groups"
    groups.mkdir(parents=True)
    cache_frames, facts = [], None
    for cid in FRAME_IDS:
        stamp = (cid * 20) / 30.0                    # 6.667, 7.333, 8.000 s
        facts, frame, jpeg = cache_at(video, stamp, work, str(cid))
        cache_frames.append({"id": cid, "timestamp": round(frame["pts_time"], 6), "path": str(jpeg)})
        # The cache framing this run already spent attempts on, crops included: a pairing is only
        # reviewable if it can digest the images that were actually sent.
        bp.write_crops(jpeg, work, scale=bp.CROP_SCALE, timestamp=frame["pts_time"])
        drawn, left, right, confidence = SAVED[cid]
        (groups / "crops-{:016x}.json".format(cid)).write_text(json.dumps({
            "arm": "crops", "fingerprint": "{:016x}".format(cid), "frame": str(jpeg),
            "model": MODEL, "timestamp": round(frame["pts_time"], 6),
            "observations": [{"label": "LEFT", "drawn": drawn, "lit": left, "confidence": confidence},
                             {"label": "RIGHT", "drawn": drawn, "lit": right, "confidence": confidence}],
            "errors": []}), encoding="utf-8")
    manifest = work / "sample-manifest.json"
    manifest.write_text(json.dumps({"source": str(video), "interval": 1.0, "frames": cache_frames}),
                        encoding="utf-8")
    cache = bs.load_cache_index(manifest)
    alignment = {"rule": "index = round(t * {})".format(facts["avg_frame_rate"]),
                 "uncertainty_seconds": round(1.0 / facts["fps"], 6), "master": facts,
                 "per_frame": [{"cache_id": f["id"], "cache_timestamp": f["timestamp"],
                                "matched_index": round(f["timestamp"] * facts["fps"]),
                                "matched_pts_time": f["timestamp"], "mad": 1.0, "mad_margin": 9.0,
                                "ambiguity_frames": 0} for f in cache_frames]}
    out = tmp_path / "source-set"
    built = bs.build_execution_set(
        [{"cache_id": 10, "role": "control", "reason": "confident at its own instant"},
         {"cache_id": 11, "role": "probe", "reason": "abstained at its own instant"},
         {"cache_id": 12, "role": "probe", "reason": "counted without confidence"}],
        alignment, cache, groups, out, purpose="test")
    assert built["control"] == 10 and built["refused"] == []
    args = ["--annotations", str(out / "annotations.json"), "--output-dir", str(tmp_path / "run"),
            "--arms", "crops", "--scale", "1", "--model", MODEL,
            "--endpoint", "http://endpoint.test", "--max-requests", "3"] + ledger(tmp_path, 3)
    run_script(monkeypatch, args + ["--prepare"], capsys=capsys)
    return {"args": args, "set": out, "built": built, "run": tmp_path / "run", "groups": groups,
            "cache": cache, "alignment": alignment, "facts": facts, "tmp": tmp_path}


def pairing_of(source_run, cache_id):
    document = json.loads((source_run["set"] / "pairing.json").read_text(encoding="utf-8"))
    return next(p for p in document["planned"] if p["cache"]["id"] == cache_id)


def execute(source_run, monkeypatch, capsys, transport=None, extra=None):
    _, out = run_script(monkeypatch, source_run["args"] + list(extra or []) + ["--execute"],
                        transport=transport, capsys=capsys)
    return json.loads(out), transport


def groups_written(source_run):
    return sorted((source_run["run"] / "crops" / "groups").glob("*.json"))


# ---------------------------------------------------------------- pairing, before any request


def test_only_an_answer_at_the_same_instant_can_be_an_expectation(source_run):
    """The exact mistake this replaces: borrowing a reading from a neighbouring second.

    Cache frame 10 was answered confidently at its own timestamp, 11 abstained and 12 counted at 0.62.
    The first may carry an expectation; the other two may not, whatever a nearby frame happened to show.
    """
    control = pairing_of(source_run, 10)
    assert control["expected"] == CONTROL_EXPECTATION
    assert control["is_control"] is True and control["role"] == "control"
    abstained = pairing_of(source_run, 11)
    assert abstained["expected"] is None and abstained["is_control"] is False
    assert "abstention" in abstained["expectation_basis"]
    shaky = pairing_of(source_run, 12)
    assert shaky["expected"] is None and shaky["is_control"] is False
    assert "not confident" in shaky["expectation_basis"]


def test_pairing_names_the_group_file_and_digests_both_framings(source_run):
    """A candidate is reviewable only if the images on both sides are identified, not described."""
    control = pairing_of(source_run, 10)
    cache, master = control["cache"], control["master"]
    assert Path(cache["group_file"]).is_file() and cache["fingerprint"]
    assert Path(cache["path"]).is_file() and len(cache["jpeg_sha256"]) == 64
    assert [c["label"] for c in cache["crop_images"]] == ["LEFT", "RIGHT"]
    for crop in cache["crop_images"]:
        assert crop["sha256"] and Path(crop["path"]).is_file(), crop
    assert cache["saved_observations"][0]["confidence"] == 0.90
    assert master["content_matched"] is True and master["uncertainty_seconds"] > 0
    assert Path(master["master_png"]).is_file() and len(master["master_png_sha256"]) == 64
    assert master["drift_seconds"] < 1.0 / 30

    # Evaluator-side record stays out of the runtime input.
    annotations = json.loads((source_run["set"] / "annotations.json").read_text(encoding="utf-8"))
    assert annotations["version"] == bs.SOURCE_ANNOTATION_VERSION and annotations["scale"] == 1
    assert all(f["frame"].endswith(".png") for f in annotations["frames"])
    assert "group_file" not in json.dumps(annotations)
    assert "pairing" in annotations["provenance"]


def test_a_candidate_with_nothing_saved_at_its_instant_gets_no_expectation(tmp_path, video):
    """Silence at an instant is a fact about the candidate list, not a gap to fill from a neighbour."""
    facts, frame, jpeg = cache_at(video, 6.666667, tmp_path, "40")
    cache = {"frames": [{"id": 40, "timestamp": frame["pts_time"], "path": jpeg}],
             "by_id": {40: {"id": 40, "timestamp": frame["pts_time"], "path": str(jpeg)}},
             "path": "m.json"}
    alignment = {"rule": "x", "uncertainty_seconds": 0.03, "master": facts,
                 "per_frame": [{"cache_id": 40, "cache_timestamp": frame["pts_time"],
                                "matched_index": 200, "matched_pts_time": frame["pts_time"],
                                "mad": 1.0, "mad_margin": 9.0, "ambiguity_frames": 0}]}
    pair = bs.pair_candidate(40, alignment, cache, tmp_path / "no-such-groups", "control", "because")
    assert pair["expected"] is None and pair["is_control"] is False
    assert pair["cache"]["group_file"] is None
    assert "cannot be a control" in pair["expectation_basis"]


def test_an_unmatched_master_identity_is_refused_rather_than_rounded(tmp_path, video):
    """A candidate with no content match has no proven master frame, so nothing is decoded for it."""
    facts, frame, jpeg = cache_at(video, 6.666667, tmp_path, "41")
    cache = {"frames": [{"id": 41, "timestamp": frame["pts_time"], "path": str(jpeg)}],
             "by_id": {41: {"id": 41, "timestamp": frame["pts_time"], "path": str(jpeg)}},
             "path": "m.json"}
    alignment = {"rule": "x", "uncertainty_seconds": 0.03, "master": facts, "per_frame": []}
    built = bs.build_execution_set([{"cache_id": 41, "role": "probe", "reason": "unmatched"}],
                                   alignment, cache, tmp_path / "groups", tmp_path / "set")
    assert built["planned"] == [] and [r["cache_id"] for r in built["refused"]] == [41]
    assert "no content match" in built["refused"][0]["because"]


# ---------------------------------------------------------------- the public command, for real


def test_prepare_builds_master_crops_and_execute_answers_them(tmp_path, video, monkeypatch, capsys):
    """The advertised command has to run on a decoded master frame with nothing faked but the socket."""
    facts, frame, _jpeg = cache_at(video, 6.666667, tmp_path, "200")
    from PIL import Image

    size = Image.open(frame["png"]).size
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({
        "kind": "pip-row-annotations", "version": bs.SOURCE_ANNOTATION_VERSION, "scale": 1,
        "frames": [{"frame": frame["png"], "timestamp": frame["pts_time"],
                    "pips": CONTROL_EXPECTATION, "row_drawn": True, "role": "control"}]}),
        encoding="utf-8")
    out = tmp_path / "run"
    args = ["--annotations", str(annotations), "--output-dir", str(out), "--arms", "crops",
            "--scale", "1", "--model", MODEL, "--endpoint", "http://endpoint.test",
            "--max-requests", "1"] + ledger(tmp_path, 1)
    _, prepared = run_script(monkeypatch, args + ["--prepare"], capsys=capsys)
    summary = json.loads(prepared)
    assert summary["arms"]["crops"]["requests_planned"] == 1
    assert summary["arms"]["crops"]["images_total"] == 2

    _, executed = run_script(monkeypatch, args + ["--execute"], transport=ScriptedTransport(),
                             capsys=capsys)
    report = json.loads(executed)
    assert report["arms"]["crops"]["requests_sent"] == 1
    assert report["arms"]["crops"]["groups_answered"] == 1
    assert report["arms"]["crops"]["stopped_reason"] is None
    written = list((out / "crops" / "groups").glob("*.json"))
    assert len(written) == 1
    group = json.loads(written[0].read_text(encoding="utf-8"))
    assert group["frame"] == frame["png"] and group["scale"] == 1
    assert group["frame_size"] == [size[0], size[1]]
    assert group["role"] == "control"
    # The digests are the same truncated ones the request fingerprint is built from, so a reviewer can
    # tie this answer to these pixels with the same function the reuse rule used.
    assert group["source_sha256"] == bp._sha(Path(frame["png"]))
    assert {c["label"] for c in group["crop_images"]} == {"LEFT", "RIGHT"}
    # A 640x360 master frame at scale 1 delivers exactly the reference geometry: the framing changed,
    # the box did not. On the real 2560x1440 master the same box is 352x88 of native detail, which is
    # what makes it the same delivered picture as the cache arm's 4x upscale -- pinned in
    # tests/test_boundary_pips.py::test_the_same_geometry_is_valid_at_master_resolution.
    assert [c["size"] for c in group["crop_images"] if c["label"] == "LEFT"] == [[88, 22]]
    for crop in group["crop_images"]:
        assert Path(crop["path"]).is_file()
        assert crop["sha256"] == bp._sha(Path(crop["path"]))


def test_master_and_cache_framings_of_one_instant_are_two_requests_not_one(tmp_path, video):
    """Distinct framing at the same instant is deliberate; a resume must not confuse the two.

    Both framings ask the same question of the same box at the same timestamp, so a reuse rule keyed on
    the timestamp or the frame name would skip the master request and report the cache answer as if the
    master pixels had been sent. They are keyed on the image, and the images differ.
    """
    facts, frame, jpeg = cache_at(video, 6.666667, tmp_path, "201")
    stamp = frame["pts_time"]
    master = bp.build_records([{"frame": frame["png"], "timestamp": stamp,
                                "pips": CONTROL_EXPECTATION, "row_drawn": True}],
                              tmp_path / "crops-master", scale=1)
    cached = bp.build_records([{"frame": jpeg, "timestamp": stamp,
                                "pips": CONTROL_EXPECTATION, "row_drawn": True}],
                              tmp_path / "crops-cache", scale=bp.CROP_SCALE)
    assert master[0]["timestamp"] == cached[0]["timestamp"], "the fixture stopped being one instant"
    assert bp.group_fingerprint(master, "crops", MODEL) != bp.group_fingerprint(cached, "crops", MODEL)
    assert master[0]["source_sha256"] != cached[0]["source_sha256"]
    # Same box in the picture; this synthetic master is the cache's own size, so the two framings differ
    # by the scale factor. On the 2560x1440 master the box itself is already 4x, and scale 1 delivers the
    # same pixels the cache arm delivers at 4x -- pinned in tests/test_boundary_pips.py.
    for side in ("LEFT", "RIGHT"):
        assert master[0]["crops"][side]["box"] == cached[0]["crops"][side]["box"]
        assert [v * bp.CROP_SCALE for v in master[0]["crops"][side]["size"]] == \
            cached[0]["crops"][side]["size"]
    # A resumed cache-arm run must not mistake the master answer for its own, or the reuse rule would
    # spend nothing and report the cache crop's reading as if the master pixels had been sent.
    assert bp.group_filename("crops", bp.group_fingerprint(master, "crops", MODEL)) != \
        bp.group_filename("crops", bp.group_fingerprint(cached, "crops", MODEL))


# ---------------------------------------------------------------- control-failure stopping


def test_a_control_that_disagrees_stops_the_batch_before_the_next_send(source_run, monkeypatch,
                                                                       capsys):
    """The control is the run's only claim that this framing shows what the paired answer shows."""
    transport = ScriptedTransport(drawn=False, left=None, right=None)
    report, transport = execute(source_run, monkeypatch, capsys, transport)
    assert report["arms"]["crops"]["stopped_reason"] == "control_mismatch"
    assert len(transport.bodies) == 1, "the batch spent {} request(s) once the control had failed".format(
        len(transport.bodies))
    assert report["arms"]["crops"]["requests_sent"] == 1
    assert report["arms"]["crops"]["control"]["outcome"] == "failed"
    assert report["arms"]["crops"]["control"]["expected"] == CONTROL_EXPECTATION
    # The failed answer is kept: what the model said about these pixels is evidence about the framing.
    assert len(groups_written(source_run)) == 1
    assert not (source_run["run"] / "comparison.json").exists()


def test_a_control_has_to_be_planned_first(tmp_path, video, monkeypatch, capsys):
    """Ordering is enforced, not advised: a control asked third has already bought two requests."""
    first = cache_at(video, 6.666667, tmp_path, "a0")[1]
    second = cache_at(video, 7.333333, tmp_path, "a1")[1]
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({
        "kind": "pip-row-annotations", "version": bs.SOURCE_ANNOTATION_VERSION, "scale": 1,
        "frames": [{"frame": first["png"], "timestamp": first["pts_time"],
                    "pips": None, "row_drawn": None, "role": "probe"},
                   {"frame": second["png"], "timestamp": second["pts_time"],
                    "pips": CONTROL_EXPECTATION, "row_drawn": True, "role": "control"}]}),
        encoding="utf-8")
    out = tmp_path / "run"
    args = ["--annotations", str(annotations), "--output-dir", str(out), "--arms", "crops",
            "--scale", "1", "--model", MODEL, "--endpoint", "http://endpoint.test",
            "--max-requests", "2"] + ledger(tmp_path, 2)
    run_script(monkeypatch, args + ["--prepare"], capsys=capsys)
    transport = ScriptedTransport()
    # A ledger is supplied so this reaches the ordering refusal rather than tripping the guard above it.
    with pytest.raises(ValueError, match="control frame has to be planned first"):
        run_script(monkeypatch, args + ["--execute"], transport=transport, capsys=capsys)
    assert transport.bodies == [], "the mis-ordered plan sent a request before reaching the control"


def test_a_resumed_run_rechecks_the_control_before_spending_anything(source_run, monkeypatch, capsys):
    """A resume's cheapest property -- the control is already answered -- is also its trap."""
    _, spent = execute(source_run, monkeypatch, capsys,
                       ScriptedTransport(drawn=False, left=None, right=None))
    assert len(spent.bodies) == 1 and len(groups_written(source_run)) == 1

    # The endpoint is healthy now. The control's group is on disk and still disagrees with the answer
    # paired to its instant, so a correct transport must send nothing: re-asking would spend an attempt
    # to re-derive a judgement the run already has, and continuing would spend two more on a framing
    # that has already failed its only test.
    right = ScriptedTransport()
    report, right = execute(source_run, monkeypatch, capsys, right)
    assert right.bodies == [], "the resume sent {} request(s) past a control it already had".format(
        len(right.bodies))
    assert report["arms"]["crops"]["stopped_reason"] == "control_mismatch"
    assert report["arms"]["crops"]["groups_reused"] >= 1


def test_a_passing_control_lets_the_rest_of_the_plan_run(source_run, monkeypatch, capsys):
    """The gate has to be able to open, or it is a refusal with extra steps."""
    report, transport = execute(source_run, monkeypatch, capsys, ScriptedTransport())
    assert report["arms"]["crops"]["stopped_reason"] is None
    assert len(transport.bodies) == 3
    assert report["arms"]["crops"]["groups_answered"] == 3
    assert report["arms"]["crops"]["control"]["outcome"] == "passed"
    # The two probes carry no truth, so they are reported as ungraded rather than as hits or errors.
    score = report["arms"]["crops"]["score"]
    assert score["frames"] == 1 and score["frames_correct"] == 1, \
        "the score counted frames that have no paired truth: %s" % score
    assert score["ungraded_frames"] == 2
    assert {round(float(u["timestamp"]), 3) for u in report["arms"]["crops"]["ungraded"]} == \
        {7.333, 8.0}


# ---------------------------------------------------------------- budget


def test_the_shared_ledger_refuses_a_run_it_cannot_finish(source_run, monkeypatch, capsys):
    """Two attempts against three requests is not a partial run, it is a refusal before any send.

    The ledger governs sends and the plan keeps its shape: rewinding ``--max-requests`` to what is left
    would change the run's identity and make the resume impossible to authorise, so the whole command is
    turned away instead, having spent nothing.
    """
    ledger = source_run["run"] / "attempts.json"
    args = ["--budget-file", str(ledger), "--budget-cap", "2", "--budget-minutes", "15",
            "--purpose", "source-smoke"]
    transport = ScriptedTransport()
    with pytest.raises(SystemExit) as refused:
        run_script(monkeypatch, source_run["args"] + args + ["--execute"], transport=transport,
                   capsys=capsys)
    assert "3 groups are still owed" in str(refused.value)
    assert transport.bodies == [], "the refused command sent a request anyway"
    assert not groups_written(source_run), "a refused run must leave no answers behind"
    assert not ledger.is_file() or json.loads(ledger.read_text(encoding="utf-8")
                                              )["attempts_charged"] == 0


def test_the_ledger_charges_every_send_and_a_second_command_cannot_respend_it(source_run, monkeypatch,
                                                                              capsys):
    """A fresh ledger is what a smoke test needs, and it is the only thing that keeps it honest."""
    ledger = source_run["run"] / "attempts.json"
    args = ["--budget-file", str(ledger), "--budget-cap", "3", "--budget-minutes", "15",
            "--purpose", "source-smoke"]
    report, transport = execute(source_run, monkeypatch, capsys, ScriptedTransport(), args)
    assert len(transport.bodies) == 3
    assert report["budget"]["attempts_charged"] == 3
    assert report["budget"]["attempts_remaining"] == 0
    assert report["arms"]["crops"]["requests_sent"] == 3
    assert report["arms"]["crops"]["control"]["outcome"] == "passed"
    spent = json.loads(ledger.read_text(encoding="utf-8"))
    assert spent["cap"] == 3 and spent["minutes"] == 15.0 and spent["deadline_at"], \
        "the fresh deadline was not stamped at the first send"
    assert spent["label"].endswith("source-smoke"), spent.get("label")

    # A third command into the same ledger owes nothing, so it is allowed to run and must still send
    # nothing: the answers it would produce are already on disk.
    after = ScriptedTransport()
    _, again = run_script(monkeypatch, source_run["args"] + args + ["--execute"], transport=after,
                          capsys=capsys)
    report = json.loads(again)
    assert after.bodies == [], "the second command re-spent attempts the first already charged"
    assert report["arms"]["crops"]["groups_reused"] == 3
    assert json.loads(ledger.read_text(encoding="utf-8"))["attempts_charged"] == 3
