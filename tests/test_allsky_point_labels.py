"""Planet labels placed like bright-star labels, and labels behind equipment.

Discussion #105: Saturn's name sat on the left of the planet while every
star's sat on the right. Planets were labelled last and never reserved, so a
star's label could take the slot right of a planet (and cover the planet).
Now every star and planet is reserved first and planets are named first.

The same thread asked for labels to stay on behind the pier and scopes; that
is ``allsky_overlay.labels_behind_equipment``, off by default.
"""
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

from services.allsky import overlay_renderer
from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid, marker_radius, reserve_targets
from services.allsky.label_stability import LabelStabilizer, reset_label_stability
from services.allsky.obstruction_map import ObstructionMap
from services.allsky.render_objects import render_planets
from services.allsky.render_stars import render_bright_stars
from services.allsky.sky_region import SkyEvidence, visibility_plane

DT = datetime(2024, 6, 21, 22, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1
CFG = {'enabled': True, 'label_size': 14}
IMG_SIZE = (750, 750)  # label_size is in 750 px units: 14 px labels here

# A star just left of Saturn: its own right-hand label covers Saturn and the
# slot right of Saturn.
STAR = [('Vega', 300.0, 300.0, 'star:HR7001')]
PLANET = [('Saturn', 330.0, 300.0, 'planet:Saturn')]


@pytest.fixture(autouse=True)
def _fresh_label_state():
    reset_label_stability()
    yield
    reset_label_stability()


def _img():
    return Image.new('RGBA', IMG_SIZE, (10, 10, 30, 255))


def _model(**kw):
    base = dict(cx=375.0, cy=375.0, a1=230.0, a3=0.0, a5=0.0, roll=0.0,
                axis_alt=90.0, axis_az=0.0, rms_residual=1.0, n_matches=50,
                calibrated_at='2024-01-01T00:00:00+00:00',
                image_width=750, image_height=750)
    base.update(kw)
    return FisheyeModel(**base)


def _render_in_renderer_order(memory):
    grid = LabelGrid(*IMG_SIZE, slot_memory=memory)
    reserve_targets(grid, STAR, 14)
    reserve_targets(grid, PLANET, 14)
    img = render_planets(_img(), None, CFG, LAT, LON, DT, grid, targets=PLANET)
    render_bright_stars(img, None, CFG, LAT, LON, DT, grid, targets=STAR)
    return grid


def test_the_old_order_pushed_the_planet_label_off_the_right():
    memory = {}
    grid = LabelGrid(*IMG_SIZE, slot_memory=memory)
    img = render_bright_stars(_img(), None, CFG, LAT, LON, DT, grid, targets=STAR)
    render_planets(img, None, CFG, LAT, LON, DT, grid, targets=PLANET)
    assert memory['star:HR7001'] == 0
    # Moved off the right, or with every slot crowded by the star's name, lost.
    assert memory.get('planet:Saturn') != 0


def test_a_planet_label_sits_right_of_the_planet_like_a_star_label():
    memory = {}
    _render_in_renderer_order(memory)
    assert memory['planet:Saturn'] == 0


def test_a_star_label_never_covers_a_planet():
    memory = {}
    _render_in_renderer_order(memory)
    # Right of the star would cover Saturn at x=330; it goes elsewhere.
    assert memory['star:HR7001'] != 0


def test_planets_reserve_themselves_when_rendered_alone():
    grid = LabelGrid(*IMG_SIZE)
    render_planets(_img(), None, CFG, LAT, LON, DT, grid, targets=PLANET)
    r = marker_radius(14)
    assert not grid.is_free(330.0 - r / 2, 300.0 - r / 2, r, r)


def test_the_renderer_names_planets_before_stars_with_both_reserved(tmp_path, monkeypatch):
    cal = tmp_path / 'cal.json'
    _model().save(str(cal))
    calls = []
    real_planets, real_stars = overlay_renderer.render_planets, overlay_renderer.render_bright_stars

    def planets(img, *a, **kw):
        calls.append(('planets', kw.get('targets')))
        return real_planets(img, *a, **kw)

    def stars(img, *a, **kw):
        calls.append(('stars', kw.get('targets')))
        return real_stars(img, *a, **kw)

    monkeypatch.setattr(overlay_renderer, 'render_planets', planets)
    monkeypatch.setattr(overlay_renderer, 'render_bright_stars', stars)
    cfg = {'enabled': True, 'calibration_file': str(cal), '_lat': LAT, '_lon': LON,
           'bright_stars': {'enabled': True}, 'planets': {'enabled': True}}
    overlay_renderer.render_allsky_overlay(_img(), cfg, {'DATETIME': '2024-06-21 22:00:00'})

    assert [name for name, _ in calls] == ['planets', 'stars']
    assert all(targets is not None for _, targets in calls)


# ---------------------------------------------------------------------------
# labels_behind_equipment
# ---------------------------------------------------------------------------

def _obstructed_plane(behind_equipment):
    """A plane from a fresh vote that saw sky only in the left half."""
    model = _model()
    stabilizer = LabelStabilizer()
    h, w = IMG_SIZE[1], IMG_SIZE[0]
    from services.allsky.label_stability import vote_grid
    _, (rows, cols) = vote_grid((h, w))
    mask = np.zeros((rows, cols), np.uint8)
    mask[:, : cols // 2] = 255
    evidence = SkyEvidence(mask, mask > 0, 50, (h, w))
    return visibility_plane(
        _img(), model, DT, LAT, LON, stabilizer, ObstructionMap(),
        evidence=evidence, behind_equipment=behind_equipment)


def test_off_labels_stay_off_what_the_vote_saw_as_equipment():
    plane = _obstructed_plane(False)
    assert plane[375, 200] == 255
    assert plane[375, 550] == 0


def test_on_labels_cover_the_whole_sky_disc():
    plane = _obstructed_plane(True)
    assert plane[375, 200] == 255
    assert plane[375, 550] == 255


def test_on_never_extends_past_the_sky_disc():
    plane = _obstructed_plane(True)
    assert plane[2, 2] == 0


def test_on_still_feeds_the_vote():
    model = _model()
    stabilizer = LabelStabilizer()
    from services.allsky.label_stability import vote_grid
    _, (rows, cols) = vote_grid((750, 750))
    mask = np.full((rows, cols), 255, np.uint8)
    visibility_plane(_img(), model, DT, LAT, LON, stabilizer, ObstructionMap(),
                     evidence=SkyEvidence(mask, mask > 0, 50, (750, 750)),
                     behind_equipment=True)
    vote, _ = stabilizer.current_small_vote()
    assert vote is not None
