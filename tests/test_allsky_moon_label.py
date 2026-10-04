"""The Moon's label is set outside its glare and reads on a bright sky.

The Moon used to be labelled like a planet: near-white text a few pixels
from its predicted centre. On a moonlit frame that put the name inside the
bloom — on the SFRO rig's 2026-09-28 00:06 frame only "oon" could be read.
"""
import math
import time
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

from services.allsky import overlay_renderer
from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid
from services.allsky.label_stability import reset_label_stability
from services.allsky.moon_label import MoonGlare, glare_at, label_gap, text_halo
from services.allsky.render_objects import (
    planet_targets, render_planets, reserve_moon_glare,
)

# Moon at 61 deg altitude over London.
DT = datetime(2024, 12, 15, 23, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1
SKY_R = 444.0          # the rig's sky radius on a 750 px output frame
SKY_LEVEL = 60.0


@pytest.fixture(autouse=True)
def _fresh_label_state():
    reset_label_stability()
    yield
    reset_label_stability()


def _moonlit(size=750, centre=(300.0, 380.0), core=40.0, halo=35.0, scale=1.0):
    """A sky at SKY_LEVEL with a saturated Moon core and a glare halo that
    falls off over ``halo`` px past the core edge, like the rig's frame."""
    size = int(size * scale)
    cx, cy = centre[0] * scale, centre[1] * scale
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - cx, yy - cy) / scale
    glow = np.clip(1.0 - (r - core) / halo, 0.0, 1.0) ** 1.5
    plane = SKY_LEVEL + (255.0 - SKY_LEVEL) * glow
    plane[r <= core] = 255.0
    return Image.fromarray(plane.astype(np.uint8)).convert('RGB')


def _model(**kw):
    base = dict(cx=375.0, cy=375.0, a1=230.0, a3=0.0, a5=0.0, roll=0.0,
                axis_alt=90.0, axis_az=0.0, rms_residual=1.0, n_matches=50,
                calibrated_at='2024-01-01T00:00:00+00:00',
                image_width=750, image_height=750)
    base.update(kw)
    return FisheyeModel(**base)


# --- measurement -----------------------------------------------------------

def test_glare_is_centred_on_the_core_not_on_the_prediction():
    # The model puts the Moon 30 px to the right of where it is (the rig's
    # frame was ~33 px out), more than half the search radius.
    glare = glare_at(_moonlit(), 330.0, 380.0, SKY_R)
    assert glare is not None
    assert abs(glare.x - 300.0) < 1.5 and abs(glare.y - 380.0) < 1.5
    assert glare.core_r == pytest.approx(40.0, rel=0.1)


def test_glare_radius_is_past_the_core_and_inside_the_halo():
    glare = glare_at(_moonlit(), 310.0, 380.0, SKY_R)
    # Text could not be read over the inner part of the fall-off.
    assert 40.0 < glare.radius < 40.0 + 35.0


def test_the_measurement_scales_with_the_frame():
    small = glare_at(_moonlit(), 320.0, 380.0, SKY_R)
    big = glare_at(_moonlit(scale=4.0), 320.0 * 4, 380.0 * 4, SKY_R * 4)
    assert big.x / 4 == pytest.approx(small.x, abs=1.5)
    assert big.radius / 4 == pytest.approx(small.radius, rel=0.12)
    assert big.core_r / 4 == pytest.approx(small.core_r, rel=0.12)


def test_no_saturated_core_is_no_glare():
    # Moon in cloud or behind the pier: nothing saturates near it.
    img = Image.new('RGB', (750, 750), (120, 120, 120))
    assert glare_at(img, 300.0, 380.0, SKY_R) is None


def test_a_bright_star_is_not_the_moon():
    plane = np.full((750, 750), SKY_LEVEL, dtype=np.uint8)
    plane[378:382, 298:302] = 255          # 16 saturated px
    assert glare_at(Image.fromarray(plane).convert('RGB'), 300.0, 380.0, SKY_R) is None


def test_a_core_outside_the_search_radius_is_ignored():
    # 0.10 of the sky radius is 44 px; a light 120 px away is not the Moon.
    assert glare_at(_moonlit(), 420.0, 380.0, SKY_R) is None


def test_a_prediction_off_the_frame_is_no_glare():
    assert glare_at(_moonlit(), -5.0, 380.0, SKY_R) is None
    assert glare_at(_moonlit(), 300.0, 800.0, SKY_R) is None


def test_glare_never_exceeds_a_quarter_of_the_sky_radius():
    # A washed-out sky never falls back to a dark level within reach.
    glare = glare_at(_moonlit(halo=400.0), 300.0, 380.0, SKY_R)
    assert glare.radius <= 0.25 * SKY_R + 1e-6


def test_measurement_budget_at_full_resolution():
    img = _moonlit(scale=3552 / 750)
    k = 3552 / 750
    glare_at(img, 320 * k, 380 * k, SKY_R * k)
    t0 = time.perf_counter()
    glare_at(img, 320 * k, 380 * k, SKY_R * k)
    # Measured ~4 ms; a 25x ceiling only catches a lost box-reduction.
    assert time.perf_counter() - t0 < 0.1


# --- placement -------------------------------------------------------------

GLARE = MoonGlare(x=300.0, y=380.0, core_r=40.0, radius=60.0)


@pytest.mark.parametrize('slot', range(8))
def test_every_label_slot_clears_the_glare(slot):
    from services.allsky.label_collision import candidate_slots
    tw, th = 60.0, 16.8
    lx, ly = candidate_slots(GLARE.x, GLARE.y, tw, th, label_gap(GLARE, th))[slot]
    nx = min(max(GLARE.x, lx), lx + tw)
    ny = min(max(GLARE.y, ly), ly + th)
    assert math.hypot(nx - GLARE.x, ny - GLARE.y) >= GLARE.radius


def test_the_moon_label_lands_outside_the_glare_on_the_right():
    memory = {}
    grid = LabelGrid(750, 750, slot_memory=memory)
    reserve_moon_glare(grid, GLARE)
    targets = [('Moon', GLARE.x, GLARE.y, 'planet:Moon')]
    render_planets(_moonlit().convert('RGBA'), None, {'enabled': True, 'label_size': 14},
                   LAT, LON, DT, grid, targets=targets, moon_glare=GLARE)
    assert memory['planet:Moon'] == 0
    x0, y0, x1, y1 = grid._rects[-1]
    assert x0 - GLARE.x >= GLARE.radius


def test_other_labels_are_kept_out_of_the_glare():
    grid = LabelGrid(750, 750)
    reserve_moon_glare(grid, GLARE)
    # A box anywhere in the bloom is refused; one past it is not.
    assert not grid.is_free(320.0, 375.0, 20.0, 10.0)
    assert grid.is_free(GLARE.x + GLARE.radius + 2.0, 375.0, 20.0, 10.0)


def test_the_moon_name_has_a_dark_outline():
    halo = text_halo(14, 255)
    assert halo['stroke_width'] >= 1
    assert halo['stroke_fill'][:3] == (0, 0, 0)

    # Drawn on the bright sky by the glare, the outline puts dark pixels
    # around the near-white text.
    img = Image.new('RGBA', (750, 750), (230, 230, 230, 255))
    grid = LabelGrid(750, 750)
    targets = [('Moon', GLARE.x, GLARE.y, 'planet:Moon')]
    out = render_planets(img, None, {'enabled': True, 'label_size': 14}, LAT, LON,
                         DT, grid, targets=targets, moon_glare=GLARE)
    x0, y0, x1, y1 = (int(v) for v in grid._rects[-1])
    box = np.asarray(out.convert('L'))[y0:y1 + 4, x0:x1 + 4]
    assert box.min() < 60


def test_a_planet_label_has_no_outline():
    img = Image.new('RGBA', (750, 750), (230, 230, 230, 255))
    grid = LabelGrid(750, 750)
    targets = [('Saturn', 300.0, 380.0, 'planet:Saturn')]
    out = render_planets(img, None, {'enabled': True, 'label_size': 14}, LAT, LON,
                         DT, grid, targets=targets)
    x0, y0, x1, y1 = (int(v) for v in grid._rects[-1])
    box = np.asarray(out.convert('L'))[y0:y1 + 4, x0:x1 + 4]
    assert box.min() > 150


# --- visibility --------------------------------------------------------------

def _moon_above(model):
    from services.allsky.coords import radec_to_altaz
    from services.allsky.planets import get_all_positions
    ra, dec = get_all_positions(DT, LAT, LON)['Moon']
    alt, az = radec_to_altaz(ra, dec, LAT, LON, DT, refraction=True)
    return float(alt), model.altaz_to_pixel(float(alt), float(az))


def test_a_seen_moon_needs_no_visibility_test():
    model = _model()
    alt, xy = _moon_above(model)
    if alt < 10.0 or xy is None:
        pytest.skip('Moon not usable at the fixed test instant')
    dark = np.zeros((750, 750), dtype=np.uint8)   # the plane calls it all obstructed
    cfg = {'enabled': True}
    assert all(n != 'Moon' for n, *_ in planet_targets(model, cfg, LAT, LON, DT, dark))
    glare = MoonGlare(float(xy[0]) + 7.0, float(xy[1]) - 4.0, 30.0, 50.0)
    moon = [t for t in planet_targets(model, cfg, LAT, LON, DT, dark, moon_glare=glare)
            if t[0] == 'Moon']
    assert moon == [('Moon', glare.x, glare.y, 'planet:Moon')]


def test_a_seen_moon_keeps_its_top_n_slot_on_an_obstructed_plane():
    model = _model()
    alt, xy = _moon_above(model)
    if alt < 0.0 or xy is None:
        pytest.skip('Moon not usable at the fixed test instant')
    dark = np.zeros((750, 750), dtype=np.uint8)
    cfg = {'top_n': 5, 'planets': {'enabled': True}}
    unseen = overlay_renderer._compute_allowed_ids(cfg, model, LAT, LON, DT, dark)
    reset_label_stability()
    seen = overlay_renderer._compute_allowed_ids(cfg, model, LAT, LON, DT, dark,
                                                 moon_seen=True)
    assert 'planet:Moon' not in unseen
    assert 'planet:Moon' in seen
