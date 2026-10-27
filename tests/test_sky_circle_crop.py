"""
Tests for services/allsky/sky_circle_crop.py and the guided path on a frame
that crops the sky circle.

Reproduces the 2026-10-26 Mac mini rig: an ASI662MC (1920x1080) whose lens
puts the horizon ~1200 px out. The circle scan reports r=448, and a guided
solve that fit every anchor to under a pixel was rejected as 2.28x the
circle-implied plate scale. Synthetic sky: a known FisheyeModel projects the
bright catalogue, so every expectation is exact.
"""
from datetime import datetime, timezone

import numpy as np
import pytest

pytest.importorskip('scipy')

from services.allsky.calibration import CalibrationError
from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.fisheye import FisheyeModel
from services.allsky.guided_calibration import calibrate_from_anchors
from services.allsky.guided_hints import suggest_stars
from services.allsky.sky_circle_crop import circle_cropped, whole_frame_radius

LAT, LON = 31.5, -99.4
DT = datetime(2026, 10, 26, 5, 42, tzinfo=timezone.utc)
W, H = 1920, 1080
# What the circle scan measured on the rig's frame: an edge inside half the
# frame height, not the horizon.
SKY = (964.0, 534.0, 448.0)


def _true_model():
    # The rig's solved pose: a1 765, axis tilted 16 deg from the zenith.
    return FisheyeModel(
        cx=975.0, cy=549.0, a1=765.0, a3=-10.0, a5=0.0,
        roll=0.0, axis_alt=74.0, axis_az=265.0, east_left=True)


@pytest.fixture(scope='module')
def in_frame():
    """Bright stars the true model puts inside the frame, brightest first."""
    model = _true_model()
    seen, out = set(), []
    for s in get_bright_stars(max_mag=3.0):
        name = s.get('name')
        if not name or name in seen:
            continue
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, DT)
        if float(alt) <= 15.0:
            continue
        xy = model.altaz_to_pixel(float(alt), float(az))
        if xy is None or not (40 <= xy[0] < W - 40 and 40 <= xy[1] < H - 40):
            continue
        seen.add(name)
        out.append({'name': name, 'ra_deg': s['ra_deg'], 'dec_deg': s['dec_deg'],
                    'alt': float(alt), 'az': float(az),
                    'vmag': float(s.get('vmag', 0.0)),
                    'x': float(xy[0]), 'y': float(xy[1])})
    out.sort(key=lambda c: c['vmag'])
    return out


def _anchors(stars, n):
    """n anchors spread over the frame: the farthest-point pick from the list."""
    chosen = [stars[0]]
    while len(chosen) < n:
        chosen.append(max(
            (s for s in stars if s not in chosen),
            key=lambda s: min(np.hypot(s['x'] - c['x'], s['y'] - c['y'])
                              for c in chosen)))
    return [(c['x'], c['y'], c['ra_deg'], c['dec_deg'], c['name'])
            for c in chosen]


class TestCircleCropped:
    def test_square_and_near_square_frames_hold_the_circle(self):
        assert not circle_cropped(3552, 3552)
        assert not circle_cropped(3552, 3079)    # 1.15

    def test_wide_and_tall_frames_crop_it(self):
        assert circle_cropped(1920, 1080)
        assert circle_cropped(1080, 1920)

    def test_unknown_size_is_not_cropped(self):
        assert not circle_cropped(0, 0)
        assert not circle_cropped(None, 1080)

    def test_whole_frame_radius_reaches_the_farthest_corner(self):
        r = whole_frame_radius(W, H, 900.0, 500.0)
        assert r == pytest.approx(np.hypot(W - 900.0, H - 500.0))


class TestGuidedSolveOnCroppedCircle:
    def test_scene_puts_stars_outside_the_measured_circle(self, in_frame):
        outside = [s for s in in_frame
                   if np.hypot(s['x'] - SKY[0], s['y'] - SKY[1]) > SKY[2]]
        assert len(in_frame) >= 8 and len(outside) >= 3

    def test_circle_read_as_horizon_rejects_the_true_scale(self, in_frame):
        with pytest.raises(CalibrationError, match='sky-circle-implied scale'):
            calibrate_from_anchors(_anchors(in_frame, 8), LAT, LON, DT,
                                   *SKY, W, H)

    def test_cropped_circle_lets_the_anchors_set_the_scale(self, in_frame):
        model = calibrate_from_anchors(_anchors(in_frame, 8), LAT, LON, DT,
                                       *SKY, W, H, circle_cropped=True)
        assert model.a1 == pytest.approx(765.0, rel=0.02)
        assert model.rms_residual < 1.0
        assert model.provenance == 'guided'

    def test_lens_polynomial_gate_still_applies(self, in_frame, monkeypatch):
        import services.allsky.guided_calibration as gc
        monkeypatch.setattr(gc, 'validate_lens_polynomial',
                            lambda m: (False, 'a3 pinned'))
        with pytest.raises(CalibrationError, match='a3 pinned'):
            calibrate_from_anchors(_anchors(in_frame, 8), LAT, LON, DT,
                                   *SKY, W, H, circle_cropped=True)


class TestHintsOnCroppedCircle:
    def _hints(self, in_frame, frame_size):
        anchors = _anchors(in_frame, 4)
        detections = [(s['x'], s['y'], 1000.0 - i)
                      for i, s in enumerate(in_frame)]
        return suggest_stars(anchors, in_frame, detections, LAT, LON, DT,
                             *SKY, frame_size=frame_size)

    def test_hints_outside_the_circle_are_kept(self, in_frame):
        result = self._hints(in_frame, (W, H))
        assert result is not None and result.trusted
        assert any(np.hypot(h.x - SKY[0], h.y - SKY[1]) > SKY[2]
                   for h in result.hints)
        assert all(0 <= h.x < W and 0 <= h.y < H for h in result.hints)

    def test_without_frame_size_hints_stay_inside_the_circle(self, in_frame):
        result = self._hints(in_frame, None)
        assert result is not None
        assert all(np.hypot(h.x - SKY[0], h.y - SKY[1]) <= SKY[2]
                   for h in result.hints)


class TestDetectionRegion:
    @staticmethod
    def _frame(points):
        img = np.full((H, W), 20, dtype=np.uint8)
        yy, xx = np.mgrid[-4:5, -4:5]
        blob = (200 * np.exp(-(xx ** 2 + yy ** 2) / 4.0)).astype(np.uint8)
        for x, y in points:
            img[y - 4:y + 5, x - 4:x + 5] = np.maximum(
                img[y - 4:y + 5, x - 4:x + 5], blob)
        return img

    def test_region_radius_finds_stars_outside_the_circle(self):
        from services.allsky.star_centroid import detect_stars
        inside, outside, edge = (964, 534), (1700, 950), (1912, 540)
        img = self._frame([inside, outside, edge])
        cx, cy, r = SKY

        default = detect_stars(img, sky_cx=cx, sky_cy=cy, sky_radius=r)
        assert len(default) == 1

        wide = detect_stars(img, sky_cx=cx, sky_cy=cy, sky_radius=r,
                            region_radius=whole_frame_radius(W, H, cx, cy))
        found = sorted((round(x), round(y)) for x, y, _f in wide)
        assert found == sorted([inside, outside])   # the frame-edge one is not
