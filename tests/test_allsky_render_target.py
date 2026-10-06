"""services/allsky/render_target — the NINA target on the all-sky overlay.

Issue #137: a reticle at the target, its name, and the imaging camera's
field of view projected through the fisheye. DT is the J2000 epoch, so the
precession the renderer applies is the identity and a target at the local
sidereal time and the site latitude sits at the zenith, the image centre.
"""
import copy
import math
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

from services.allsky import overlay_renderer
from services.allsky.coords import (
    altaz_to_radec, julian_date, lst_degrees, precess_from_j2000, radec_to_altaz,
)
from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid, estimate_text_size
from services.allsky.label_size import CONFIG_KEY, apply_label_size_preset
from services.allsky.label_stability import get_label_stabilizer, reset_label_stability
from services.allsky.render_stars import render_bright_stars
from services.allsky import render_target as render_target_module
from services.allsky.moon_label import MoonGlare
from services.allsky.render_target import (
    MIN_FOV_PX, TARGET_UID, TargetPlacement, fov_corners_radec,
    layer_config, locate_target, marker_reach, place_target, render_target, reserve_target,
    stale_after_seconds, target_label_px,
)
from services.config_defaults import DEFAULT_CONFIG

DT = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1
IMG_SIZE = (750, 750)
ZENITH_RA = float(lst_degrees(julian_date(DT), LON))
LAYER = {'enabled': True, 'show_fov': True, 'show_label': True, 'color': '#FF66AA',
         'marker_size': 10, 'line_width': 2, 'label_size': 13, 'opacity': 255,
         'stale_after_s': 120}


@pytest.fixture(autouse=True)
def _fresh_label_state():
    reset_label_stability()
    yield
    reset_label_stability()


def _model(**kw):
    base = dict(cx=375.0, cy=375.0, a1=230.0, a3=0.0, a5=0.0, roll=0.0,
                axis_alt=90.0, axis_az=0.0, rms_residual=1.0, n_matches=50,
                calibrated_at='2024-01-01T00:00:00+00:00',
                image_width=750, image_height=750)
    base.update(kw)
    return FisheyeModel(**base)


def _img():
    return Image.new('RGBA', IMG_SIZE, (10, 10, 30, 255))


def _target(**kw):
    t = {'name': 'M31', 'ra_deg': ZENITH_RA, 'dec_deg': LAT,
         'fov_w_deg': 10.0, 'fov_h_deg': 5.0, 'rotation_deg': 0.0, 'source': 'nina'}
    t.update(kw)
    return t


def _place(target=None, layer=None, model=None):
    return place_target(IMG_SIZE, model or _model(), layer or LAYER,
                        _target() if target is None else target, LAT, LON, DT)


def _sep(ra1, dec1, ra2, dec2):
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _bbox(poly):
    xs, ys = [x for x, _ in poly], [y for _, y in poly]
    return max(xs) - min(xs), max(ys) - min(ys)


def _is_target_colour(px):
    r, g, b = px[:3]
    return r > 180 and b > 150 and g < 130


def _count_target_pixels(img):
    a = np.asarray(img.convert('RGB')).astype(int)
    return int(((a[..., 0] > 180) & (a[..., 2] > 150) & (a[..., 1] < 130)).sum())


# ---------------------------------------------------------------------------
# fov_corners_radec
# ---------------------------------------------------------------------------

class TestFovCorners:
    def test_corners_sit_on_the_tangent_plane_diagonal(self):
        ra, dec, w, h = 83.8, 22.0, 10.0, 5.0
        pts = fov_corners_radec(ra, dec, w, h, 30.0, samples_per_edge=1)
        assert len(pts) == 4
        expected = math.degrees(math.atan(math.hypot(math.tan(math.radians(w / 2)),
                                                     math.tan(math.radians(h / 2)))))
        for p in pts:
            assert _sep(ra, dec, *p) == pytest.approx(expected, abs=1e-6)
            # The flat-sky estimate a small field reduces to.
            assert _sep(ra, dec, *p) == pytest.approx(math.hypot(w / 2, h / 2), abs=0.01)

    def test_edge_midpoints_at_half_width_and_half_height(self):
        ra, dec, w, h = 10.68, 41.27, 3.0, 2.0
        pts = fov_corners_radec(ra, dec, w, h, 0.0, samples_per_edge=2)
        # Order: top-left, top-mid, top-right, right-mid, ..., left-mid.
        top, right, bottom, left = pts[1], pts[3], pts[5], pts[7]
        assert _sep(ra, dec, *top) == pytest.approx(h / 2, abs=1e-6)
        assert _sep(ra, dec, *bottom) == pytest.approx(h / 2, abs=1e-6)
        assert _sep(ra, dec, *right) == pytest.approx(w / 2, abs=1e-6)
        assert _sep(ra, dec, *left) == pytest.approx(w / 2, abs=1e-6)

    def test_at_zero_the_height_runs_north_south(self):
        pts = fov_corners_radec(120.0, 10.0, 4.0, 2.0, 0.0, samples_per_edge=2)
        top = pts[1]
        assert top[0] == pytest.approx(120.0, abs=1e-6)
        assert top[1] == pytest.approx(11.0, abs=1e-6)

    def test_a_quarter_turn_swaps_the_axes(self):
        a = fov_corners_radec(200.0, -20.0, 6.0, 2.0, 0.0, samples_per_edge=2)
        b = fov_corners_radec(200.0, -20.0, 2.0, 6.0, 90.0, samples_per_edge=2)
        for p in a:
            assert min(_sep(*p, *q) for q in b) < 1e-6

    def test_only_the_angle_mod_180_changes_the_outline(self):
        a = fov_corners_radec(50.0, 30.0, 6.0, 2.0, 25.0)
        b = fov_corners_radec(50.0, 30.0, 6.0, 2.0, 205.0)
        for p in a:
            assert min(_sep(*p, *q) for q in b) < 1e-6

    def test_samples_per_edge_sets_the_point_count(self):
        assert len(fov_corners_radec(0.0, 0.0, 2.0, 1.0, 0.0)) == 24
        assert len(fov_corners_radec(0.0, 0.0, 2.0, 1.0, 0.0, samples_per_edge=3)) == 12


# ---------------------------------------------------------------------------
# place_target
# ---------------------------------------------------------------------------

class TestPlaceTarget:
    def test_a_zenith_target_lands_on_the_optical_centre(self):
        p = _place()
        assert p.x == pytest.approx(375.0, abs=1.0)
        assert p.y == pytest.approx(375.0, abs=1.0)
        assert p.name == 'M31'
        assert p.label_px == target_label_px(IMG_SIZE, LAYER) == 13
        assert p.marker_r == pytest.approx(10.0)

    def test_a_10_by_5_degree_field_is_40_by_20_px_at_a1_230(self):
        w, h = _bbox(_place().fov_polygon)
        # Equidistant fisheye: 230 px/rad is ~4.01 px per degree.
        assert w == pytest.approx(40.0, abs=3.0)
        assert h == pytest.approx(20.0, abs=3.0)

    def test_a_quarter_turn_stands_the_field_on_end(self):
        w, h = _bbox(_place(_target(rotation_deg=90.0)).fov_polygon)
        assert w == pytest.approx(20.0, abs=3.0)
        assert h == pytest.approx(40.0, abs=3.0)

    def test_the_angle_is_measured_east_of_north(self):
        """North is up and east is left on this model, so a camera 'up'
        turned 45 deg east of north points up and to the left."""
        for rot, sign in ((45.0, -1), (315.0, 1)):
            layer = dict(LAYER)
            target = _target(fov_w_deg=2.0, fov_h_deg=8.0, rotation_deg=rot)
            ra, dec = fov_corners_radec(target['ra_deg'], target['dec_deg'], 2.0, 8.0, rot,
                                        samples_per_edge=2)[1]
            top = place_target(IMG_SIZE, _model(), layer,
                               _target(ra_deg=ra, dec_deg=dec, fov_w_deg=None,
                                       fov_h_deg=None), LAT, LON, DT)
            assert top.y < 375.0 - 5
            assert (top.x - 375.0) * sign > 5

    def test_below_the_horizon_is_not_drawn(self):
        assert _place(_target(dec_deg=-60.0)) is None

    def test_a_field_reaching_below_the_horizon_keeps_the_reticle_alone(self):
        ra, dec = altaz_to_radec(3.0, 180.0, LAT, LON, DT, refraction=True)
        p = _place(_target(ra_deg=float(ra), dec_deg=float(dec),
                           fov_w_deg=10.0, fov_h_deg=10.0))
        assert p is not None
        assert p.y > 375.0 + 300
        assert p.fov_polygon is None

    def test_a_target_off_the_image_is_not_drawn(self):
        model = _model(cx=375.0, cy=-200.0)
        assert _place(model=model) is None

    def test_a_field_under_min_fov_px_draws_only_the_reticle(self):
        p = _place(_target(fov_w_deg=0.5, fov_h_deg=0.5))
        assert p is not None and p.fov_polygon is None
        assert 0.5 * 230 * math.pi / 180 < MIN_FOV_PX

    def test_show_fov_off_drops_the_outline(self):
        assert _place(layer=dict(LAYER, show_fov=False)).fov_polygon is None

    def test_no_fov_in_the_target_draws_only_the_reticle(self):
        p = _place(_target(fov_w_deg=None, fov_h_deg=None, rotation_deg=None))
        assert p is not None and p.fov_polygon is None

    def test_no_target_or_layer_off_places_nothing(self):
        assert place_target(IMG_SIZE, _model(), LAYER, None, LAT, LON, DT) is None
        assert _place(layer=dict(LAYER, enabled=False)) is None

    @pytest.mark.parametrize('kw, expected', [
        (dict(target=_target(dec_deg=-60.0)), "it is below the horizon"),
        (dict(model=_model(cx=375.0, cy=-200.0)), "it falls outside the image"),
        (dict(layer=dict(LAYER, enabled=False)), "the NINA target layer is switched off"),
    ])
    def test_locate_says_why_nothing_was_placed(self, kw, expected):
        placement, why = locate_target(IMG_SIZE, kw.get('model', _model()),
                                       kw.get('layer', LAYER), kw.get('target', _target()),
                                       LAT, LON, DT)
        assert placement is None and why == expected

    @pytest.mark.parametrize('target, layer, expected', [
        (_target(fov_w_deg=None, fov_h_deg=None), LAYER, "NINA sent no field of view"),
        (_target(), dict(LAYER, show_fov=False), "the field-of-view box is switched off"),
        (_target(fov_w_deg=0.5, fov_h_deg=0.5), LAYER, "the field is under 4 px across"),
    ])
    def test_a_reticle_placement_says_why_there_is_no_box(self, target, layer, expected):
        placement, why = locate_target(IMG_SIZE, _model(), layer, target, LAT, LON, DT)
        assert why == '' and placement.fov_polygon is None
        assert placement.reticle_reason == expected

    def test_a_box_placement_has_no_reason(self):
        placement, why = locate_target(IMG_SIZE, _model(), LAYER, _target(), LAT, LON, DT)
        assert why == '' and placement.fov_polygon and placement.reticle_reason == ''

    def test_coordinates_are_precessed_to_the_date(self):
        """The catalogue layers precess J2000 to the date; without the same
        step the target sat ~0.37 deg off every label by 2026."""
        dt = datetime(2026, 10, 5, 22, 0, 0, tzinfo=timezone.utc)
        ra0, dec0 = 10.6847, 41.2687
        alt0, az0 = radec_to_altaz(ra0, dec0, LAT, LON, dt)
        ra, dec = precess_from_j2000(ra0, dec0, dt)
        alt, az = radec_to_altaz(float(ra), float(dec), LAT, LON, dt)
        big = (7500, 7500)
        model = _model(cx=3750.0, cy=3750.0, a1=2300.0, image_width=7500, image_height=7500)
        expected = model.altaz_to_pixel(float(alt), float(az))
        unprecessed = model.altaz_to_pixel(float(alt0), float(az0))
        p = place_target(big, model, LAYER, _target(ra_deg=ra0, dec_deg=dec0), LAT, LON, dt)
        assert (p.x, p.y) == pytest.approx(expected, abs=0.01)
        assert math.hypot(p.x - unprecessed[0], p.y - unprecessed[1]) > 0.2 * 2300 * math.pi / 180


# ---------------------------------------------------------------------------
# reserve_target / render_target
# ---------------------------------------------------------------------------

class TestReserveAndRender:
    def _placement(self, **kw):
        base = dict(name='M31', x=300.0, y=300.0, marker_r=10.0, label_px=13,
                    fov_polygon=None)
        base.update(kw)
        return TargetPlacement(**base)

    def test_the_target_name_takes_the_right_slot_and_a_nearby_star_moves(self):
        memory = {}
        grid = LabelGrid(*IMG_SIZE, slot_memory=memory)
        placed = reserve_target(grid, self._placement())
        assert memory[TARGET_UID] == 0
        assert placed.label_pos[0] > 300.0
        star = [('Vega', 320.0, 300.0, 'star:HR7001')]
        render_bright_stars(_img(), None, {'enabled': True, 'label_size': 11},
                            LAT, LON, DT, grid, targets=star)
        assert memory.get('star:HR7001') != 0

    def test_the_name_clears_the_reticle_ticks(self):
        grid = LabelGrid(*IMG_SIZE)
        placed = reserve_target(grid, self._placement())
        assert placed.label_pos[0] >= 300.0 + 10.0 * 2.2

    def test_later_labels_stay_off_the_reticle(self):
        grid = LabelGrid(*IMG_SIZE)
        reserve_target(grid, self._placement(name=''))
        assert not grid.is_free(310.0, 295.0, 8, 8)

    def test_show_label_off_reserves_the_reticle_and_places_no_name(self):
        grid = LabelGrid(*IMG_SIZE)
        placed = reserve_target(grid, self._placement(), show_label=False)
        assert placed.label_pos is None
        assert not grid.is_free(305.0, 298.0, 4, 4)

    def test_with_no_box_render_draws_the_ring_ticks_and_name(self):
        placement = reserve_target(LabelGrid(*IMG_SIZE), self._placement())
        out = render_target(_img(), LAYER, placement)
        assert _is_target_colour(out.getpixel((310, 300)))      # ring
        assert _is_target_colour(out.getpixel((300, 282)))      # top tick
        lx, ly = placement.label_pos
        box = out.crop((int(lx), int(ly), int(lx) + 40, int(ly) + 16))
        assert _count_target_pixels(box) > 10                   # name
        assert not _is_target_colour(out.getpixel((300, 300)))  # open centre

    def test_with_a_box_render_draws_the_box_and_nothing_inside_it(self):
        """A screen-aligned cross read as a slanted X on a turned box."""
        poly = ((250.0, 270.0), (350.0, 270.0), (350.0, 330.0), (250.0, 330.0))
        placement = reserve_target(LabelGrid(*IMG_SIZE), self._placement(fov_polygon=poly))
        out = render_target(_img(), LAYER, placement)
        assert _is_target_colour(out.getpixel((300, 270)))      # outline
        inside = out.crop((253, 273, 348, 328))
        assert _count_target_pixels(inside) == 0                # no cross, no ring
        lx, ly = placement.label_pos
        box = out.crop((int(lx), int(ly), int(lx) + 40, int(ly) + 16))
        assert _count_target_pixels(box) > 10

    def test_a_small_box_has_no_ring_round_it(self):
        """At full resolution a field is smaller than the ring and ticks;
        those would swallow the box, so only the box is drawn."""
        poly = ((295.0, 296.0), (305.0, 296.0), (305.0, 304.0), (295.0, 304.0))
        placement = reserve_target(LabelGrid(*IMG_SIZE), self._placement(fov_polygon=poly))
        out = render_target(_img(), LAYER, placement)
        for px in ((290, 300), (300, 290), (300, 310), (307, 307), (293, 307), (300, 278),
                   (300, 300)):
            assert not _is_target_colour(out.getpixel(px)), px
        assert _is_target_colour(out.getpixel((295, 300)))      # box edge

    @pytest.mark.parametrize('poly', [
        ((280.0, 290.0), (320.0, 290.0), (320.0, 310.0), (280.0, 310.0)),
        ((260.0, 297.0), (340.0, 297.0), (340.0, 303.0), (260.0, 303.0)),
        ((300.0, 270.0), (330.0, 300.0), (300.0, 330.0), (270.0, 300.0)),
        ((285.0, 280.0), (330.0, 280.0), (330.0, 312.0), (285.0, 312.0)),
    ])
    def test_the_name_clears_the_box_bounding_rect(self, poly):
        xs, ys = [x for x, _ in poly], [y for _, y in poly]
        tw, th = estimate_text_size('M31', 13)
        # Slot memory steers the name into each of the eight slots in turn.
        for slot in range(8):
            memory = {TARGET_UID: slot}
            placed = reserve_target(LabelGrid(*IMG_SIZE, slot_memory=memory),
                                    self._placement(fov_polygon=poly))
            assert memory[TARGET_UID] == slot
            lx, ly = placed.label_pos
            overlaps = (lx < max(xs) and lx + tw > min(xs)
                        and ly < max(ys) and ly + th > min(ys))
            assert not overlaps, (slot, placed.label_pos)

    def test_later_labels_stay_off_the_box(self):
        poly = ((260.0, 285.0), (340.0, 285.0), (340.0, 315.0), (260.0, 315.0))
        grid = LabelGrid(*IMG_SIZE)
        placement = self._placement(name='', fov_polygon=poly)
        reserve_target(grid, placement)
        assert marker_reach(placement) == pytest.approx(math.hypot(40.0, 15.0))
        assert not grid.is_free(330.0, 305.0, 8, 8)

    def test_render_without_a_placement_returns_the_image(self):
        img = _img()
        assert render_target(img, LAYER, None) is img

    def test_render_with_the_layer_off_draws_nothing(self):
        img = _img()
        assert render_target(img, dict(LAYER, enabled=False), self._placement()) is img

    def test_render_keeps_the_image_mode(self):
        out = render_target(Image.new('RGB', IMG_SIZE), LAYER, self._placement())
        assert out.mode == 'RGB'
        assert _is_target_colour(out.getpixel((310, 300)))


# ---------------------------------------------------------------------------
# render_allsky_overlay end to end
# ---------------------------------------------------------------------------

def _overlay_config(cal_path, target):
    return {
        'enabled': True, 'calibration_file': cal_path,
        '_lat': LAT, '_lon': LON, '_obs_utc': DT.isoformat(),
        'grid': {'enabled': False}, 'constellations': {'enabled': False},
        'bright_stars': {'enabled': True, 'max_magnitude': 2.5},
        'messier': {'enabled': False}, 'ngc': {'enabled': False},
        'planets': {'enabled': False},
        'nina_target': dict(LAYER),
        '_nina_target': target,
    }


@pytest.fixture
def cal_path(tmp_path):
    path = str(tmp_path / 'cal.json')
    _model().save(path)
    return path


class TestOverlayRenderer:
    def test_the_target_is_drawn_and_its_name_takes_the_first_slot(self, cal_path):
        out = overlay_renderer.render_allsky_overlay(
            _img(), _overlay_config(cal_path, _target()), {})
        assert _count_target_pixels(out) > 100
        assert _is_target_colour(out.getpixel((395, 375)))      # box edge, 20 px out
        assert not _is_target_colour(out.getpixel((379, 375)))  # no centre cross
        assert not _is_target_colour(out.getpixel((385, 375)))  # no ring
        assert get_label_stabilizer().slot_memory.get(TARGET_UID) == 0

    def test_nothing_is_drawn_without_a_target(self, cal_path):
        out = overlay_renderer.render_allsky_overlay(
            _img(), _overlay_config(cal_path, None), {})
        assert _count_target_pixels(out) == 0

    def test_drawn_even_where_the_plane_says_equipment(self, cal_path, monkeypatch):
        monkeypatch.setattr(overlay_renderer, 'visibility_plane',
                            lambda img, *a, **kw: np.zeros((img.height, img.width), np.uint8))
        out = overlay_renderer.render_allsky_overlay(
            _img(), _overlay_config(cal_path, _target()), {})
        assert _is_target_colour(out.getpixel((395, 375)))      # box edge

    def test_a_failing_placement_leaves_the_other_layers_alone(self, cal_path, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError('boom')

        monkeypatch.setattr(overlay_renderer, 'locate_target', boom)
        _one_star(monkeypatch, 420.0, 375.0)
        out = overlay_renderer.render_allsky_overlay(
            _img(), _overlay_config(cal_path, _target()), {})
        assert _count_target_pixels(out) == 0
        assert get_label_stabilizer().slot_memory.get(STAR_UID) is not None
        assert _count_star_pixels(out) > 10

    def test_a_star_in_the_targets_slot_yields_to_the_target(self, cal_path, monkeypatch):
        """A bright star below-right of the target, whose own right-hand
        name would cover the target's: the target is named first and keeps
        the right; the star's name moves to another side."""
        _one_star(monkeypatch, 400.0, 392.0)
        out = overlay_renderer.render_allsky_overlay(
            _img(), _overlay_config(cal_path, _target()), {})
        memory = get_label_stabilizer().slot_memory
        assert memory[TARGET_UID] == 0
        assert memory.get(STAR_UID) not in (None, 0)
        assert _count_star_pixels(out) > 10

    def test_without_the_target_that_star_is_named_on_the_right(self, cal_path, monkeypatch):
        _one_star(monkeypatch, 400.0, 392.0)
        overlay_renderer.render_allsky_overlay(_img(), _overlay_config(cal_path, None), {})
        assert get_label_stabilizer().slot_memory.get(STAR_UID) == 0

    def test_the_targets_name_stays_out_of_the_moon_glare(self, cal_path, monkeypatch):
        glare = MoonGlare(x=410.0, y=375.0, core_r=6.0, radius=25.0)
        monkeypatch.setattr(overlay_renderer, 'measure_moon_glare', lambda *a, **kw: glare)
        placed = {}
        real = overlay_renderer.render_target

        def spy(img, cfg, placement):
            placed['p'] = placement
            return real(img, cfg, placement)

        monkeypatch.setattr(overlay_renderer, 'render_target', spy)
        cfg = _overlay_config(cal_path, _target())
        cfg['planets'] = {'enabled': True, 'colors': {}}
        overlay_renderer.render_allsky_overlay(_img(), cfg, {})
        lx, ly = placed['p'].label_pos
        tw, th = estimate_text_size('M31', placed['p'].label_px)
        nx, ny = min(max(glare.x, lx), lx + tw), min(max(glare.y, ly), ly + th)
        assert math.hypot(nx - glare.x, ny - glare.y) >= glare.radius
        assert get_label_stabilizer().slot_memory[TARGET_UID] != 0


STAR_UID = 'star:HR7001'


def _one_star(monkeypatch, x, y):
    monkeypatch.setattr(overlay_renderer, 'bright_star_targets',
                        lambda *a, **kw: [('Vega', x, y, STAR_UID)])


def _count_star_pixels(img):
    """The bright-star layer's default #FFEEAA: green high, unlike the target."""
    a = np.asarray(img.convert('RGB')).astype(int)
    return int(((a[..., 0] > 180) & (a[..., 1] > 180) & (a[..., 2] > 100)).sum())


# ---------------------------------------------------------------------------
# Hand-edited settings
# ---------------------------------------------------------------------------

class TestSettings:
    @pytest.mark.parametrize('value, expected', [
        (120, 120.0), (600, 600.0), (None, 120.0), ('2m', 120.0), ('300', 300.0),
        (float('nan'), 120.0), (float('inf'), 120.0), (0, 30.0), (-5, 30.0),
        (1e9, 3600.0),
    ])
    def test_stale_after_seconds_is_clamped_and_defaulted(self, value, expected):
        assert stale_after_seconds({'stale_after_s': value}) == expected

    @pytest.mark.parametrize('layer', [None, 'off', [], {}])
    def test_a_missing_or_garbage_block_reads_as_the_default(self, layer):
        assert stale_after_seconds(layer) == 120.0
        assert layer_config(layer) == (layer if isinstance(layer, dict) else {})

    @pytest.mark.parametrize('color', ['red', '#abc', '#GGGGGG', None, 123, 'FF66AA'])
    def test_an_unusable_colour_draws_in_the_default(self, color, monkeypatch):
        monkeypatch.setattr(render_target_module, '_bad_colors_logged', set())
        out = render_target(_img(), dict(LAYER, color=color), TestReserveAndRender()._placement())
        assert out.getpixel((310, 300))[:3] == (0xFF, 0x66, 0xAA)

    def test_a_bad_colour_is_logged_once_not_every_frame(self, monkeypatch):
        calls = []
        monkeypatch.setattr(render_target_module, '_bad_colors_logged', set())
        monkeypatch.setattr(render_target_module.log, 'debug', calls.append)
        monkeypatch.setattr(render_target_module.log, 'warning', calls.append)
        for _ in range(3):
            render_target(_img(), dict(LAYER, color='red'), TestReserveAndRender()._placement())
        assert len(calls) == 1

    def test_garbage_sizes_fall_back_to_the_defaults(self):
        layer = dict(LAYER, marker_size='big', label_size=None, line_width='x',
                     opacity=float('nan'))
        p = _place(layer=layer)
        assert p.marker_r == pytest.approx(10.0) and p.label_px == 13
        assert _is_target_colour(render_target(_img(), layer, p).getpixel((395, 375)))

    def test_a_null_layer_block_draws_with_the_defaults(self):
        p = place_target(IMG_SIZE, _model(), None, _target(), LAT, LON, DT)
        assert p is not None and p.fov_polygon is not None
        assert _is_target_colour(render_target(_img(), None, p).getpixel((395, 375)))


def test_the_label_size_preset_moves_the_target_label():
    layer = DEFAULT_CONFIG.get('allsky_overlay', {}).get('nina_target') or LAYER
    cfg = {CONFIG_KEY: 'why_so_large', 'nina_target': copy.deepcopy(layer)}
    out = apply_label_size_preset(cfg)
    assert out['nina_target']['label_size'] == layer['label_size'] + 2
    assert cfg['nina_target']['label_size'] == layer['label_size']
