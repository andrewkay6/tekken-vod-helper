"""Tests for boundary detect/backtest CLI dispatch and legacy script agentic mode."""
import io
import importlib.util
import json
import sys
from contextlib import redirect_stdout

import pytest
from pathlib import Path
from unittest.mock import patch

from tekken_vod_helper.boundary_cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_legacy():
    spec = importlib.util.spec_from_file_location(
        "bt_script", REPO_ROOT / "scripts" / "backtest_youtube_boundaries.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_detect_dispatch():
    with patch("tekken_vod_helper.boundary_workflow.detect") as mock_detect:
        mock_detect.return_value = {"status": "complete", "match_count": 3,
                                    "paths": {}, "range": [0, 100],
                                    "review_count": 1, "model_calls": 10,
                                    "policy_hash": "abc", "source_id": "test",
                                    "output_dir": "/tmp/out"}
        from tekken_vod_helper.boundary_cli import main
        f = io.StringIO()
        with redirect_stdout(f):
            main(["detect", "http://example.com/v", "--output-dir", "/tmp/out",
                  "--duration", "600", "--interval", "3", "--fine-step", "0.5",
                  "--max-frames", "100", "--batch-size", "4", "--max-requests", "50",
                  "--yt-dlp", "/usr/bin/yt-dlp", "--endpoint", "http://ep",
                  "--model", "test-model", "--samples-manifest", "/tmp/m.json",
                  "--ffmpeg", "/usr/bin/ffmpeg", "--ffprobe", "/usr/bin/ffprobe"])
        kwargs = mock_detect.call_args[1]
        assert kwargs["duration"] == 600
        assert kwargs["interval"] == 3
        assert kwargs["fine_step"] == 0.5
        assert kwargs["max_frames"] == 100
        assert kwargs["batch_size"] == 4
        assert kwargs["max_requests"] == 50
        assert kwargs["yt_dlp"] == "/usr/bin/yt-dlp"
        assert kwargs["endpoint"] == "http://ep"
        assert kwargs["model"] == "test-model"
        assert kwargs["samples_manifest"] == "/tmp/m.json"
        assert kwargs["ffmpeg_path"] == "/usr/bin/ffmpeg"
        assert kwargs["ffprobe_path"] == "/usr/bin/ffprobe"
        assert mock_detect.call_args[0][0] == "http://example.com/v"
        assert mock_detect.call_args[0][1] == "/tmp/out"


def test_backtest_boundaries_dispatch():
    with patch("tekken_vod_helper.boundary_workflow.backtest") as mock_bt:
        mock_bt.return_value = {"score": {"f1": 0.9}, "detect": {}, "calibration": "heldout"}
        from tekken_vod_helper.boundary_cli import main
        f = io.StringIO()
        with redirect_stdout(f):
            main(["backtest-boundaries", "http://example.com/v",
                  "--truth", "/tmp/truth.json", "--output-dir", "/tmp/bt",
                  "--interval", "4", "--ffmpeg", "/usr/bin/ffmpeg",
                  "--ffprobe", "/usr/bin/ffprobe"])
        kwargs = mock_bt.call_args[1]
        assert kwargs["interval"] == 4
        assert kwargs["ffmpeg_path"] == "/usr/bin/ffmpeg"
        assert kwargs["ffprobe_path"] == "/usr/bin/ffprobe"
        assert mock_bt.call_args[0][0] == "http://example.com/v"
        assert mock_bt.call_args[0][1] == "/tmp/truth.json"
        assert mock_bt.call_args[0][2] == "/tmp/bt"


def test_legacy_parser_flags():
    script = _load_legacy()
    with patch("sys.argv", ["prog", "--agentic", "--truth", "/tmp/t.json",
                            "--url", "http://x", "--output-dir", "/tmp/o"]):
        args = script._parse_args()
        assert args.agentic is True
        assert args.truth == "/tmp/t.json"


def test_legacy_agentic_dispatch(tmp_path):
    out = str(tmp_path / "out")
    script = _load_legacy()
    with patch("sys.argv", ["prog", "--agentic", "--truth", "/tmp/t.json",
                            "--url", "http://x", "--output-dir", out,
                            "--ffmpeg", "/usr/bin/ffmpeg", "--ffprobe", "/usr/bin/ffprobe"]):
        with patch("tekken_vod_helper.boundary_workflow.backtest") as mock_bt:
            mock_bt.return_value = {"score": {}, "detect": {}}
            rc = script.main()
            assert rc == 0
            assert mock_bt.called
            assert mock_bt.call_args[0][1] == "/tmp/t.json"


def test_annotate_footage_validates_a_blinded_pass_and_reports_what_nobody_described(tmp_path):
    """A reference pass is recorded from the tape, kept in its own file, and never a decision.

    The command has no project argument on purpose: a pass that could read the boundaries it will later be
    compared against is not independent, and a record that could carry one would turn a second opinion into
    an authority. What it can say is how much of the recording somebody described and which stretches nobody
    has, which is the only honest way to report a pass that stopped halfway.
    """
    records = tmp_path / "pass.jsonl"
    records.write_text("\n".join(json.dumps(row) for row in [
        {"kind": "play_new_match", "from": 100.0, "to": 700.0, "boundary_at": 104.5,
         "view": "the pair names and the station frame", "note": "second set of the pool"},
        {"kind": "not_play", "from": 700.0, "to": 760.0},
        {"kind": "watched it", "from": 800.0},                       # no such disposition
        {"kind": "play_new_match", "from": 900.0, "to": 1000.0},      # no instant named
        {"kind": "unsure", "from": 1200.0, "to": 5000.0},             # past the end of the tape
    ]) + "\n", encoding="utf-8")
    out = tmp_path / "annotations.json"
    main(["annotate-footage", "--recording", "/recordings/bb9.mkv", "--duration", "2000",
          "--input", str(records), "--output", str(out), "--observer", "andy",
          "--pass-label", "first-look"])
    document = json.loads(out.read_text(encoding="utf-8"))
    assert [row["kind"] for row in document["records"]] == ["play_new_match", "not_play"]
    assert document["records"][0]["boundary_at"] == 104.5
    assert document["records"][0]["observer"] == "andy"
    assert document["records"][0]["is_not_a_boundary_decision"] is True
    assert [row["sequence"] for row in document["records"]] == [1, 2]
    assert len(document["rejected"]) == 3
    assert "exact instant" in document["rejected"][1]["error"]
    assert document["coverage"]["seconds_annotated"] == 660.0
    assert document["coverage"]["seconds_unwatched"] == 1340.0
    assert document["coverage"]["whole_recording_annotated"] is False
    assert [row["from"] for row in document["coverage"]["unwatched_intervals"]] == [0.0, 760.0]
    assert [row["edge"] for row in document["coverage"]["unwatched_intervals"]] == ["leading", "trailing"]
    assert document["independently_validated"] is False
    assert document["inference_sent"] is False and document["media_rendered"] is False
    assert not (tmp_path / "project.tvh.json").exists()
    printed = io.StringIO()
    with redirect_stdout(printed):
        main(["annotate-footage", "--recording", "/recordings/bb9.mkv", "--duration", "2000",
              "--input", str(records), "--output", str(out)])
    assert "1340.0" in printed.getvalue()


def test_annotate_footage_reads_a_wrapped_document_too(tmp_path):
    source = tmp_path / "pass.json"
    source.write_text(json.dumps({"records": [{"kind": "unsure", "from": 0.0, "to": 10.0}]}),
                      encoding="utf-8")
    out = tmp_path / "annotations.json"
    main(["annotate-footage", "--recording", "/r.mkv", "--duration", "10", "--input", str(source),
          "--output", str(out)])
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["coverage"]["whole_recording_annotated"] is True
    assert document["coverage"]["seconds_unwatched"] == 0.0


def test_a_reference_pass_records_who_how_and_what_they_had_already_seen(tmp_path):
    """A CLI with no project argument stops a pass reading answers; it does not make the pass independent.

    That distinction used to be implied by the wording around the command, and an implication is not a
    record. The three fields are what a later comparison has to go on, and an empty one is written as
    "not recorded" with its consequence named, rather than left blank to be read as fine.
    """
    source = tmp_path / "pass.jsonl"
    source.write_text(json.dumps({"kind": "play_new_match", "from": 100.0, "to": 700.0,
                                  "boundary_at": 104.5, "view": "the pair names"}) + "\n",
                      encoding="utf-8")
    out = tmp_path / "reference.json"
    main(["annotate-footage", "--recording", "/r.mkv", "--duration", "2000", "--input", str(source),
          "--output", str(out), "--observer", "andy", "--procedure", "one continuous pass at 1x",
          "--prior-exposure", "none: I had not opened the project or seen any boundary list"])
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["method"] == {"observer": "andy",
                                  "procedure": "one continuous pass at 1x",
                                  "prior_exposure": "none: I had not opened the project or seen any "
                                                    "boundary list"}
    assert document["independence_claimable"] is True
    assert document["independence_unclaimed_because"] is None
    assert document["no_expected_count_from_the_detector"] is True
    assert document["independently_validated"] is False
    # No detector number crossed into the file: only what the watcher wrote, plus the tape's own length.
    assert document["duration"] == 2000.0
    assert "what_this_is_not" in document


def test_an_undocumented_exposure_leaves_the_pass_unevaluated_and_says_which_field(tmp_path):
    source = tmp_path / "pass.jsonl"
    source.write_text(json.dumps({"kind": "unsure", "from": 0.0, "to": 10.0}) + "\n", encoding="utf-8")
    out = tmp_path / "reference.json"
    captured = io.StringIO()
    with redirect_stdout(captured):
        main(["annotate-footage", "--recording", "/r.mkv", "--duration", "100", "--input", str(source),
              "--output", str(out)])
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["independence_claimable"] is False
    because = document["independence_unclaimed_because"]
    assert "who watched" in because and "how they watched" in because \
        and "what they had already seen" in because
    assert "unevaluated, not wrong" in because
    assert not document["method"]["observer"].startswith("andy")
    printed = json.loads(captured.getvalue())
    assert printed["independence_claimable"] is False


def test_compare_reference_measures_agreement_and_refuses_to_call_it_validation(tmp_path):
    """The comparison exists so an honest disagreement can be seen; it must not manufacture agreement.

    The tolerance is the operator's, required, and never defaulted from the project -- and whatever the
    numbers line up to, ``validation`` comes back false with its reason. A tool that could turn a match
    into a validated boundary would only be a way of grading one's own homework with better formatting.
    """
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({"kind": "footage-annotations",
                                     "method": {"observer": "andy", "procedure": "1x",
                                                "prior_exposure": "none"},
                                     "independence_claimable": True,
                                     "records": [{"kind": "play_new_match", "from": 100.0,
                                                  "boundary_at": 100.0},
                                                 {"kind": "play_new_match", "from": 300.0,
                                                  "boundary_at": 300.0},
                                                 {"kind": "play_new_match", "from": 900.0,
                                                  "boundary_at": 900.0}]}), encoding="utf-8")
    project = tmp_path / "project.tvh.json"
    project.write_text(json.dumps({"duration": 1000.0, "video_path": "/r.mkv", "matches": [
        {"start": 100.0, "end": 200.0, "recovery": {"clip_end_at": 202.0,
                                                    "clip_end_anchor": {"winner_confirmed_at": 201.0}}},
        {"start": 300.4, "end": 400.0, "recovery": {"clip_end_at": 402.0,
                                                    "clip_end_anchor": {"winner_confirmed_at": 401.0}}}]}),
        encoding="utf-8")

    loose = json.loads(_run_capture(main, ["compare-reference", "--reference", str(reference),
                                           "--project", str(project), "--tolerance", "1.0"]))
    tight = json.loads(_run_capture(main, ["compare-reference", "--reference", str(reference),
                                           "--project", str(project), "--tolerance", "0.2"]))
    # The reference names three openings and the project has two, so a roomy tolerance still leaves the
    # third unmatched: agreement is a fraction of what the reference claimed, never a percentage of the
    # answer the project already gives.
    assert [item["project_at"] for item in loose["matched"]] == [100.0, 300.4]
    assert loose["agreement_fraction"] == pytest.approx(0.666667, abs=1e-6), loose
    assert loose["in_reference_not_project"] == [900.0]
    assert loose["in_project_not_reference"] == []
    assert loose["validation"] is False and "not a validation" in loose["validation_reason"]
    assert loose["reference_method"]["prior_exposure"] == "none"
    assert loose["reference_independence_claimable"] is True
    # The same pair at a fifth of a second: the 0.4 s difference is now a miss on both sides, and the
    # report says so instead of rounding the disagreement away.
    assert tight["matched"] == [{"reference_at": 100.0, "project_at": 100.0, "difference_seconds": 0.0}]
    assert tight["in_reference_not_project"] == [300.0, 900.0]
    assert tight["in_project_not_reference"] == [300.4]
    assert tight["agreement_fraction"] == pytest.approx(0.333333, abs=1e-6)
    assert tight["validation"] is False


def _run_capture(entry, argv):
    captured = io.StringIO()
    with redirect_stdout(captured):
        entry(argv)
    return captured.getvalue()


def test_compare_reference_demands_its_tolerance_from_the_caller(tmp_path):
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({"records": []}), encoding="utf-8")
    project = tmp_path / "project.tvh.json"
    project.write_text(json.dumps({"duration": 10.0, "matches": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["compare-reference", "--reference", str(reference), "--project", str(project)])


def test_a_single_record_pass_is_read_as_one_record_not_as_a_malformed_document(tmp_path):
    """One line of JSON is both a valid JSONL file and a valid JSON document. Both readings are wrong.

    A watcher who describes exactly one stretch of tape writes one line. Read as a document, it has no
    'records' key, and the command used to report that a file containing an annotation contained no
    annotations -- the failure lands on the person who wrote the least, which is the worst place for it.
    """
    single = tmp_path / "one.jsonl"
    single.write_text(json.dumps({"kind": "not_play", "from": 0.0, "to": 30.0,
                                  "note": "lobby"}), encoding="utf-8")
    out = tmp_path / "reference.json"
    result = json.loads(_run_capture(main, ["annotate-footage", "--recording", "/r.mkv",
                                            "--duration", "600", "--input", str(single),
                                            "--output", str(out)]))
    document = json.loads(out.read_text(encoding="utf-8"))
    assert result["records"] == 1 and result["rejected"] == 0
    assert document["records"][0]["kind"] == "not_play"
    assert document["coverage"]["seconds_annotated"] == 30.0


def test_an_empty_input_is_refused_rather_than_written_as_a_clean_pass(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    captured = io.StringIO()
    with redirect_stdout(captured), pytest.raises(SystemExit):
        main(["annotate-footage", "--recording", "/r.mkv", "--duration", "600",
              "--input", str(empty), "--output", str(tmp_path / "out.json")])
    assert "annotations have to be" in captured.getvalue()
    assert not (tmp_path / "out.json").exists(), "a refused pass writes no file"
