"""
Tests for services/allsky/equipment_lookup.py — carrying a sensor-resolution
pixel into the equipment map's output-frame grid.

The map is learned on a 100 px output frame with a block of "equipment" on
its left; the sensor frame the guided dialog works on is larger, and may
have been cropped as well as resized on the way to that output frame.
"""
import numpy as np
import pytest

from services.allsky.equipment_lookup import sky_lookup_for_frame
from services.allsky.obstruction_map import ObstructionMap

OUT = 100          # output frame edge; grid step 1 so the assertions are exact


def _disc(cx, cy, r, size=OUT):
    yy, xx = np.mgrid[0:size, 0:size]
    return ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r


def _learned_map(crop=None):
    """A map that has watched a left-hand block stay dark on one frame."""
    silhouette = np.zeros((OUT, OUT), dtype=bool)
    silhouette[:, :35] = True
    region = _disc(OUT / 2, OUT / 2, OUT * 0.45)
    sky = region & ~silhouette
    m = ObstructionMap()
    m.update(np.where(sky, 255, 0).astype(np.uint8), n_detections=60,
             frame_is_observable=True, reach_mask=sky, sky_region=region,
             crop=crop)
    assert m.is_known
    return m


def test_unlearned_map_has_no_answer():
    assert sky_lookup_for_frame(ObstructionMap(), 400, 400) is None


def test_resized_frame_maps_through_the_scale():
    """Sensor 400 px, output 100 px: sensor (80, 200) is output (20, 50),
    inside the block; sensor (300, 200) is output (75, 50), open sky."""
    is_sky = sky_lookup_for_frame(_learned_map(), 400, 400)
    assert is_sky is not None
    assert is_sky(80.0, 200.0) is False
    assert is_sky(300.0, 200.0) is True


def test_cropped_frame_maps_through_scale_then_crop():
    """Sensor 400 px resized to 200 px, then cropped to 100 px at (50, 50)."""
    crop = (50, 50, OUT, OUT, 200, 200)
    is_sky = sky_lookup_for_frame(_learned_map(crop=crop), 400, 400)
    assert is_sky is not None
    # Sensor (120, 200) -> resized (60, 100) -> output (10, 50): the block.
    assert is_sky(120.0, 200.0) is False
    # Sensor (240, 200) -> output (70, 50): open sky.
    assert is_sky(240.0, 200.0) is True


def test_pixels_the_crop_removed_read_as_sky():
    """The map never saw them, so it cannot call them equipment."""
    crop = (50, 50, OUT, OUT, 200, 200)
    is_sky = sky_lookup_for_frame(_learned_map(crop=crop), 400, 400)
    assert is_sky(10.0, 10.0) is True        # output (-45, -45)
    assert is_sky(390.0, 390.0) is True      # output (145, 145)


def test_frame_of_another_shape_has_no_answer():
    """A 400x300 sensor frame cannot become a square output by a resize."""
    assert sky_lookup_for_frame(_learned_map(), 400, 300) is None


@pytest.mark.parametrize('bad', [(0, 400), (400, 0), ('x', 400)])
def test_nonsense_frame_size_has_no_answer(bad):
    assert sky_lookup_for_frame(_learned_map(), *bad) is None


def test_lookup_is_a_snapshot():
    """Later learning does not move the answer under a running worker."""
    m = _learned_map()
    is_sky = sky_lookup_for_frame(m, 400, 400)
    m.reset()
    assert is_sky(80.0, 200.0) is False
