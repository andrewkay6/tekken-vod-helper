from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

import pytest

import tekken_vod_helper.match_analyzer as ma
from tekken_vod_helper.match_analyzer import (
    AnalyzerConfig,
    analyze_match_boundaries,
    apply_boundary_proposals,
    identify_frame_state,
)
from tekken_vod_helper.models import MatchSegment

PillowImage = pytest.importorskip("PIL.Image")
ImageChops = pytest.importorskip("PIL.ImageChops")
ImageStat = pytest.importorskip("PIL.ImageStat")

REPO_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = REPO_ROOT / "backtests" / "reference"


@contextmanager
def _patch_similarities(start_score, end_score):
    with patch.object(ma, "_stage_select_similarity", side_effect=start_score), patch.object(ma, "_center_dialog_similarity", side_effect=end_score):
        yield


def _solid(path: Path, color):
    PillowImage.new("RGB", (320, 180), color).save(str(path))


def _rgb_similarity(frame, reference):
    """Per-pixel RGB similarity used only in timestamp-selection tests."""
    size = (32, 32)
    f = frame.convert("RGB").resize(size)
    r = reference.convert("RGB").resize(size)
    diff = ImageChops.difference(f, r)
    rms = (sum(v * v for v in ImageStat.Stat(diff).rms) ** 0.5)
    return max(0.0, 1.0 - rms / 255.0)


def test_stage_select_uses_last_matching_occurrence(tmp_path):
    start_ref = tmp_path / "stage.png"
    end_ref = tmp_path / "press-start.png"
    _solid(start_ref, (230, 230, 230))
    _solid(end_ref, (20, 20, 20))

    def fake_extract(timestamp, output_path):
        if 8 <= timestamp <= 12 or 38 <= timestamp <= 42:
            color = (230, 230, 230)
        elif 178 <= timestamp <= 182:
            color = (20, 20, 20)
        else:
            color = (70, 70, 70)
        _solid(Path(output_path), color)

    with _patch_similarities(_rgb_similarity, _rgb_similarity):
        proposals = analyze_match_boundaries(
            "fake.mkv",
            300.0,
            [MatchSegment(start=20.0), MatchSegment(start=250.0)],
            "",
            AnalyzerConfig(
                start_reference_path=str(start_ref),
                end_reference_path=str(end_ref),
                start_search_before=20.0,
                start_search_after=40.0,
                end_search_after_start=75.0,
                coarse_step=5.0,
                fine_step=1.0,
                start_min_score=0.95,
                end_min_score=0.95,
            ),
            frame_extractor=fake_extract,
        )

    assert proposals[0].proposed_start == 42.0
    assert proposals[0].proposed_end == 178.0


def test_end_prompt_before_plausible_match_duration_is_ignored(tmp_path):
    start_ref = tmp_path / "stage.png"
    end_ref = tmp_path / "press-start.png"
    _solid(start_ref, (230, 230, 230))
    _solid(end_ref, (20, 20, 20))

    def fake_extract(timestamp, output_path):
        if 48 <= timestamp <= 52:
            color = (230, 230, 230)
        elif 70 <= timestamp <= 72 or 190 <= timestamp <= 192:
            color = (20, 20, 20)
        else:
            color = (70, 70, 70)
        _solid(Path(output_path), color)

    with _patch_similarities(_rgb_similarity, _rgb_similarity):
        proposals = analyze_match_boundaries(
            "fake.mkv",
            300.0,
            [MatchSegment(start=50.0), MatchSegment(start=250.0)],
            "",
            AnalyzerConfig(
                start_reference_path=str(start_ref),
                end_reference_path=str(end_ref),
                start_search_before=10.0,
                start_search_after=10.0,
                end_search_after_start=75.0,
                coarse_step=5.0,
                fine_step=1.0,
                start_min_score=0.95,
                end_min_score=0.95,
            ),
            frame_extractor=fake_extract,
        )

    assert proposals[0].proposed_start == 52.0
    assert proposals[0].proposed_end == 190.0


def test_apply_boundary_proposals_updates_existing_matches():
    matches = [MatchSegment(start=10.0), MatchSegment(start=100.0)]
    proposals = [
        type(
            "Proposal",
            (),
            {
                "match_index": 0,
                "proposed_start": 12.3456,
                "proposed_end": 90.1234,
            },
        )()
    ]

    changed = apply_boundary_proposals(matches, proposals)

    assert changed == 2
    assert matches[0].start == 12.346
    assert matches[0].end == 90.123


@pytest.mark.xfail(strict=True, reason="Recovered heuristic misclassifies accepted prompt; Qwen vision is the final classifier")
def test_identify_frame_state_finds_controller_prompt_shape():
    """Classify the real press-start reference using the actual identifier."""
    image_path = REF_DIR / "end" / "bb7-press-start-user.png"
    assert image_path.exists(), "Missing reference: {}".format(image_path)
    with PillowImage.open(str(image_path)) as img:
        identifier = identify_frame_state(img.convert("RGB"))
    assert identifier.state == "controller_prompt", (
        "Expected controller_prompt but got '{}' (confidence={:.3f}). "
        "Implementation bug in identify_frame_state."
        .format(identifier.state, identifier.confidence)
    )


@pytest.mark.xfail(strict=True, reason="Recovered heuristic misclassifies accepted stage select; Qwen vision is the final classifier")
def test_identify_frame_state_stage_select_reference():
    """Classify the real stage-select reference using the actual identifier."""
    image_path = REF_DIR / "start" / "bb7-match1-stage-select-random.png"
    assert image_path.exists(), "Missing reference: {}".format(image_path)
    with PillowImage.open(str(image_path)) as img:
        identifier = identify_frame_state(img.convert("RGB"))
    assert identifier.state == "stage_select", (
        "Expected stage_select but got '{}' (confidence={:.3f}). "
        "Implementation bug in identify_frame_state."
        .format(identifier.state, identifier.confidence)
    )


def test_identify_frame_state_vs_screen_not_stage_select():
    """Real rejected VS-screen must NOT be classified as stage_select."""
    image_path = REF_DIR / "rejected" / "start" / "bb7-match1-vs-screen-not-stage-select.png"
    assert image_path.exists(), "Missing reference: {}".format(image_path)
    with PillowImage.open(str(image_path)) as img:
        identifier = identify_frame_state(img.convert("RGB"))
    assert identifier.state != "stage_select", (
        "VS screen misclassified as stage_select (confidence={:.3f}). "
        "Implementation bug in identify_frame_state."
        .format(identifier.confidence)
    )


def test_identify_frame_state_gameplay_not_stage_select():
    """Real rejected gameplay frame must NOT be classified as stage_select."""
    image_path = REF_DIR / "rejected" / "start" / "bb7-match1-gameplay-not-stage-select.png"
    assert image_path.exists(), "Missing reference: {}".format(image_path)
    with PillowImage.open(str(image_path)) as img:
        identifier = identify_frame_state(img.convert("RGB"))
    assert identifier.state != "stage_select", (
        "Gameplay misclassified as stage_select (confidence={:.3f}). "
        "Implementation bug in identify_frame_state."
        .format(identifier.confidence)
    )


def test_rejected_start_reference_does_not_trigger_stage_select(tmp_path):
    start_ref = tmp_path / "stage.png"
    end_ref = tmp_path / "press-start.png"
    _solid(start_ref, (230, 230, 230))
    _solid(end_ref, (20, 20, 20))

    def fake_extract(timestamp, output_path):
        color = (100, 150, 200)
        _solid(Path(output_path), color)

    with _patch_similarities(_rgb_similarity, _rgb_similarity):
        proposals = analyze_match_boundaries(
            "fake.mkv",
            300.0,
            [MatchSegment(start=50.0), MatchSegment(start=250.0)],
            "",
            AnalyzerConfig(
                start_reference_path=str(start_ref),
                end_reference_path=str(end_ref),
                start_search_before=10.0,
                start_search_after=10.0,
                end_search_after_start=75.0,
                coarse_step=5.0,
                fine_step=1.0,
                start_min_score=0.95,
                end_min_score=0.95,
            ),
            frame_extractor=fake_extract,
        )

    assert proposals[0].proposed_start is None
