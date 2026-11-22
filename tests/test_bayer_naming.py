"""Tests for services/camera/bayer_naming.py — pipeline (OpenCV) vs physical Bayer names."""
import numpy as np
import pytest

from services.camera.bayer_naming import PATTERNS, physical_bayer_pattern, pipeline_pattern_name

cv2 = pytest.importorskip("cv2")


def red_scene_mosaic(physical, size=8):
    """A red-only scene sampled through ``physical`` (the top-left tile, row-major)."""
    mosaic = np.zeros((size, size), np.uint8)
    for i, colour in enumerate(physical):
        if colour == 'R':
            mosaic[i // 2::2, i % 2::2] = 200
    return mosaic


@pytest.mark.parametrize("physical", PATTERNS)
def test_opencv_decodes_a_physical_tile_only_through_its_reversed_name(physical):
    mosaic = red_scene_mosaic(physical)
    pipeline_maps = {  # the same table camera_utils.debayer_raw_image uses
        'RGGB': cv2.COLOR_BayerRG2RGB, 'BGGR': cv2.COLOR_BayerBG2RGB,
        'GRBG': cv2.COLOR_BayerGR2RGB, 'GBRG': cv2.COLOR_BayerGB2RGB,
    }
    red = [name for name, code in pipeline_maps.items()
           if np.array_equal(cv2.cvtColor(mosaic, code)[3:5, 3:5].reshape(-1, 3).mean(axis=0),
                             [200, 0, 0])]
    assert red == [pipeline_pattern_name(physical)]


def test_pipeline_bggr_is_physical_rggb():
    assert physical_bayer_pattern('BGGR') == 'RGGB'
    assert physical_bayer_pattern('RGGB') == 'BGGR'
    assert physical_bayer_pattern('GRBG') == 'GBRG'
    assert physical_bayer_pattern('GBRG') == 'GRBG'


@pytest.mark.parametrize("name", PATTERNS)
def test_round_trip(name):
    assert pipeline_pattern_name(physical_bayer_pattern(name)) == name


def test_case_and_whitespace_tolerated():
    assert physical_bayer_pattern(' bggr ') == 'RGGB'


@pytest.mark.parametrize("bad", [None, '', 'RGB', 'XGGB', 42])
def test_unknown_pattern_raises(bad):
    with pytest.raises(ValueError):
        physical_bayer_pattern(bad)
