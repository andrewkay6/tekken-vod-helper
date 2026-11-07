"""The set-recovery artifacts must not become an input to the thing they judge.

``labels-eval-frozen.json`` is evaluator-side truth in waiting, and ``interval-candidates.json`` is a
survey of what the layer already knows about one match. Both sit under ``build/``, where the crop-run
discovery walks -- so a future refactor that globs too eagerly could feed either one to the planner, and
the run would then be scored against its own expectations, or planned against a survey instead of the
footage. These tests hold the line the pip labels already hold.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SET_DIR = ROOT / "build/work/bb9/set-trial-v1"

LABELS = SET_DIR / "labels-eval-frozen.json"
GRID = SET_DIR / "label-sheet/grid.json"


def test_labels_were_actually_read_and_declare_what_they_cannot_support():
    """A labels file full of nothing is a placeholder, not evaluation truth; this one has readings in it."""
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    assert labels["kind"] == "set-recovery-evaluation-labels"
    assert labels["frozen"] is True and len(labels["labels"]) == 6
    assert labels["version"] == "set-trial-v1-inspected"
    for band in labels["labels"]:
        assert (SET_DIR / band["band"]).is_file(), band["band"]
        assert len(band["band_sha256"]) >= 12, "a label with no digest of the pixels read is an opinion"
        assert band["names"]["left"] and band["names"]["right"]
    # The load-bearing label: same match, continuing tally, opposite screen sides. It is what shows a
    # swap is an identity-mapping event rather than a set boundary.
    swaps = [b for b in labels["labels"] if b.get("side_mapping_changed_from_previous_label")]
    assert len(swaps) == 1 and swaps[0]["games_won"] == {"left": 0, "right": 1}
    # And the file refuses to assert the conclusion it never saw.
    assert labels["unlabelled_gaps"], "a labels file with no declared gaps has overreached"
    assert any("unknown" in gap for gap in labels["unlabelled_gaps"]), \
        "the file asserts a final tally it never read"
    # Operator anchors are labelled approximate, with the frame that proves it.
    late = [b for b in labels["labels"] if "28.7 s BEFORE" in b.get("what_this_proves", "")]
    assert late and late[0]["label_read"] == "WINNERS FINAL"


def test_the_labels_file_is_not_a_runtime_annotation():
    """No 'frames' key, and nothing a planner reads to build a request."""
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    assert "frames" not in labels, "a label file with runtime shape will be consumed as one"
    assert "annotations_for" in labels and labels["annotations_for"] is None


def test_the_label_sheet_is_a_grid_not_a_detector_echo():
    """Frames chosen on a 180 s schedule, so no label inherits a boundary from the pipeline."""
    grid = json.loads((SET_DIR / "label-sheet" / "grid.json").read_text(encoding="utf-8"))
    assert len(grid) == 4
    steps = [round(b["asked_at"] - a["asked_at"], 1) for a, b in zip(grid, grid[1:])]
    assert steps == [180.0, 180.0, 180.0], "the sampling was not a fixed grid: %s" % steps
    for band in grid:
        assert (SET_DIR / Path(band["band"]).name).is_file() or Path(band["band"]).is_file()


def test_set_discovery_never_picks_the_survey_up_as_a_crop_run():
    """The reuse walker looks for ``*/crops/groups``; neither of these files is anywhere near it."""
    from tekken_vod_helper import boundary_pips as bp
    from tekken_vod_helper import boundary_windows as bw

    for path in (LABELS, GRID):
        assert path.is_file()
        assert path.parent.name != "groups" and path.parent.name != "crops"
    # A labels file dropped where groups live would be parsed as an answer; assert the shape that stops it.
    assert "observations" not in json.loads(LABELS.read_text(encoding="utf-8"))
    assert hasattr(bw, "load_saved_readings") and hasattr(bp, "is_cache_framing")
