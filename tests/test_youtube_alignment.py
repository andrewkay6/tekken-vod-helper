from PIL import Image
import pytest
from scripts.align_youtube_reference import best_matches, derive_interval


def test_pixel_matches_expose_distinct_competing_scenes():
    red = Image.new('RGB', (48, 27), (255, 0, 0))
    near = Image.new('RGB', (48, 27), (254, 0, 0))
    blue = Image.new('RGB', (48, 27), (0, 0, 255))
    source = [(20, blue, 'blue'), (5, red, 'nearby'), (6, near, 'different'), (0, red, 'exact')]
    result = best_matches(source, red)
    assert [r['timestamp'] for r in result] == [0, 6, 20]
    assert result[0]['path'] == 'exact'
    assert result[0]['distance'] == 0
    assert result[1]['distance'] == pytest.approx(1 / 3)
    assert result[2]['distance'] == 170


def test_empty_reference_is_not_a_match():
    with pytest.raises(ValueError):
        best_matches([], Image.new('RGB', (48, 27)))


def test_offset_consensus_rejects_unrelated_black_frame_matches():
    def anchor(offset, side):
        return {'offset':offset,'side':side,'matches':[{'distance':1}]}
    data = [anchor(100, 'start'), anchor(100.5, 'start'), anchor(100.2, 'end'),
            anchor(100.4, 'end'), anchor(900, 'start'), anchor(901, 'start')]
    interval, inliers = derive_interval(data, 500)
    assert interval == pytest.approx([100.25,600.3])
    assert len(inliers) == 4
    assert derive_interval(data[:2],500) == (None, [])
