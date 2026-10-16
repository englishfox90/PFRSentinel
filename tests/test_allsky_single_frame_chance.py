"""
Tests for services/allsky/single_frame_chance.py and its two call sites.

Issue #93 follow-up: on 2026-09-28 the triangle-hash fallback filled the
empty calibration slot with a 10-match fit at RMS = tol/sqrt(2) — a
coincidence fit — because neither single-frame path recorded the final
tolerance or a chance ratio, so `fit_is_credible` never saw it. The
single-frame paths must now be judged the way the joint fit is, and the
guided solve must stay exempt.
"""
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel
from services.allsky.single_frame_chance import (
    judge_single_frame_fit, single_frame,
)

LAT, LON = 31.33, -100.46
DT = datetime(2026, 6, 22, 6, 0, tzinfo=timezone.utc)
SKY = dict(sky_cx=1137.0, sky_cy=1306.0, sky_r=968.0)


def _true_model():
    return FisheyeModel(cx=1137.0, cy=1306.0, a1=643.0, a3=1.3, a5=-7.7,
                        roll=-0.321, axis_alt=82.5, axis_az=16.9,
                        east_left=True)


def _sky(true_model, max_mag=4.0, noise=0.5, seed=7):
    """(detected, above_horizon) for one synthetic frame of the real catalog."""
    from services.allsky.catalogs import get_bright_stars
    from services.allsky.coords import radec_to_altaz
    rng = np.random.default_rng(seed)
    above, detected = [], []
    for s in get_bright_stars(max_mag=max_mag):
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, DT)
        alt, az = float(alt), float(az)
        if alt <= 3.0:
            continue
        above.append((s, alt, az))
        if alt < 12:
            continue
        xy = true_model.altaz_to_pixel(alt, az)
        if xy is None:
            continue
        detected.append((xy[0] + rng.normal() * noise,
                         xy[1] + rng.normal() * noise, 1000.0))
    above.sort(key=lambda t: t[0]['vmag'])
    return detected, above


def _random_detections(n, seed=3):
    """Detections scattered uniformly inside the sky circle — no sky at all."""
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        x = rng.uniform(SKY['sky_cx'] - SKY['sky_r'], SKY['sky_cx'] + SKY['sky_r'])
        y = rng.uniform(SKY['sky_cy'] - SKY['sky_r'], SKY['sky_cy'] + SKY['sky_r'])
        if np.hypot(x - SKY['sky_cx'], y - SKY['sky_cy']) <= SKY['sky_r']:
            out.append((x, y, 500.0))
    return out


class TestJudgeSingleFrameFit:
    def test_chance_shaped_fit_is_rejected_and_stamped(self):
        """The 2026-09-28 shape: matches ≈ chance expectation, RMS = tol/√2."""
        from services.allsky.chance_matches import estimate_chance
        _det, above = _sky(_true_model())
        detected = _random_detections(200)
        model = _true_model()
        model.final_tol_px = 7.9
        frame = single_frame(detected, above, **SKY)
        expected = estimate_chance([frame], model, 7.9).expected
        assert expected > 1.0, "fixture must produce a real chance pool"
        model.n_matches = int(round(expected))
        model.rms_residual = 7.9 / np.sqrt(2.0)

        ok, msg, est = judge_single_frame_fit(model, frame)

        assert ok is False
        assert 'chance' in msg.lower()
        assert f"{model.n_matches} matches vs {expected:.0f} expected" in msg
        assert est is not None and est.tol_px == 7.9
        assert 0.5 < model.chance_ratio < 2.0

    def test_high_match_count_still_fails_on_rms_fraction(self):
        """Ten matches on a small pool can read 2x chance (the log's case:
        10 vs ~4 expected) — the RMS = tol/√2 signature must still fail it."""
        _det, above = _sky(_true_model())
        detected = _random_detections(200)
        model = _true_model()
        model.final_tol_px = 7.9
        model.n_matches = 10
        model.rms_residual = 5.77
        ok, msg, _ = judge_single_frame_fit(model, single_frame(detected, above, **SKY))
        assert ok is False
        assert 'match tolerance' in msg

    def test_genuine_fit_passes_and_records_ratio(self):
        detected, above = _sky(_true_model())
        model = _true_model()
        model.final_tol_px = 10.0
        model.n_matches = len(detected)
        model.rms_residual = 1.0
        ok, msg, est = judge_single_frame_fit(model, single_frame(detected, above, **SKY))
        assert ok is True, msg
        assert model.chance_ratio >= 2.0
        assert est is not None and est.n_frames == 1

    def test_no_final_tolerance_is_not_judged(self):
        detected, above = _sky(_true_model())
        model = _true_model()
        model.n_matches = 10
        model.rms_residual = 7.0
        ok, msg, est = judge_single_frame_fit(model, single_frame(detected, above, **SKY))
        assert ok is True
        assert est is None
        assert model.chance_ratio == 0.0

    def test_no_geometry_is_not_judged(self):
        detected, above = _sky(_true_model())
        model = _true_model()
        model.final_tol_px = 10.0
        model.n_matches = 10
        model.rms_residual = 7.0
        frame = single_frame(detected, above, None, None, None)
        ok, _msg, _est = judge_single_frame_fit(model, frame)
        assert ok is True
        assert model.chance_ratio == 0.0

    def test_fallback_circle_is_not_attached(self):
        frame = single_frame([], [], 10.0, 10.0, None, image_width=100, image_height=80)
        assert 'sky_r' not in frame
        assert frame['image_width'] == 100


class TestIterativeFitRecordsTolerance:
    def test_final_tol_px_is_the_last_rematch_tolerance(self):
        pytest.importorskip('scipy')
        from services.allsky.calibration import _iterative_fit
        true = _true_model()
        detected, above = _sky(true, noise=0.3)
        by_px = {(round(x, 3), round(y, 3)) for x, y, _ in detected}
        matches = []
        for s, alt, az in above:
            xy = true.altaz_to_pixel(alt, az)
            if xy is None:
                continue
            near = [d for d in detected if np.hypot(d[0] - xy[0], d[1] - xy[1]) < 2.0]
            if near:
                matches.append(((near[0][0], near[0][1]), s, (alt, az)))
        assert len(matches) >= 8 and by_px
        seed = FisheyeModel(cx=1137.0, cy=1306.0, a1=630.0, a3=0.0, a5=0.0,
                            roll=-0.3, axis_alt=82.0, axis_az=17.5, east_left=True)
        model, rms = _iterative_fit(matches, seed, LAT, LON, DT, above, detected,
                                    8, 15.0, tol_scale=968.0 / 1563.0)
        assert model.final_tol_px > 0.0
        # The schedule runs 50 -> 10 px scaled; the fit converges (rms < 2) so
        # it stops early, at one of the schedule's tolerances.
        schedule = {(968.0 / 1563.0) * max(10.0, 50.0 - i * 6.0) for i in range(8)}
        assert any(abs(model.final_tol_px - t) < 1e-6 for t in schedule)
        assert rms < 2.0
        ok, msg, _ = judge_single_frame_fit(
            model, single_frame(detected, above, **SKY, image_width=2628,
                                image_height=2628))
        assert ok is True, msg
        assert model.chance_ratio >= 2.0


class TestTriangleCalibrateGate:
    def _patch_validators(self, monkeypatch, tm):
        for name in ('validate_lens_polynomial', 'validate_a1_scale'):
            monkeypatch.setattr(tm, name, lambda *a, **k: (True, 'ok'))
        monkeypatch.setattr(tm, 'validate_bright_anchors',
                            lambda *a, **k: (True, '5/12 bright anchors matched'))

    def _run(self, monkeypatch, fit_model, rms):
        from services.allsky import triangle_match as tm
        _det, above = _sky(_true_model())
        detected = _random_detections(200)
        seed = _true_model()
        seed_matches = [((d[0], d[1]), above[i][0], (above[i][1], above[i][2]))
                        for i, d in enumerate(detected[:10])]
        monkeypatch.setattr(
            tm, '_generate_and_score',
            lambda *a, **kw: (seed, len(seed_matches), list(seed_matches), 1e-6))
        monkeypatch.setattr(tm, '_iterative_fit', lambda *a, **kw: (fit_model, rms))
        self._patch_validators(monkeypatch, tm)
        return tm.triangle_calibrate(
            image=None, lat_deg=LAT, lon_deg=LON, dt=DT,
            detected=detected, above_horizon=above,
            sky_cx=SKY['sky_cx'], sky_cy=SKY['sky_cy'], sky_radius=SKY['sky_r'],
            min_matches=8, max_residual_px=15.0)

    def test_chance_level_fit_raises_with_the_numbers(self, monkeypatch):
        pytest.importorskip('scipy')
        from services.allsky.calibration import CalibrationError
        fit = _true_model()
        fit.n_matches = 10
        fit.rms_residual = 5.77
        fit.final_tol_px = 7.9
        with pytest.raises(CalibrationError, match=r'chance level.*10 matches vs'):
            self._run(monkeypatch, fit, 5.77)

    def test_credible_fit_is_returned_with_ratio(self, monkeypatch):
        pytest.importorskip('scipy')
        fit = _true_model()
        fit.n_matches = 60
        fit.rms_residual = 2.0
        fit.final_tol_px = 7.9
        model = self._run(monkeypatch, fit, 2.0)
        assert model is fit
        assert model.chance_ratio >= 2.0
        assert model.final_tol_px == 7.9


class TestGridPathGate:
    def test_chance_level_grid_fit_falls_through_to_triangle(self, monkeypatch):
        """Guard (e) joins (a)-(d): a chance-shaped grid fit is not returned,
        it gets the triangle fallback like every other sanity failure."""
        pytest.importorskip('scipy')
        from PIL import Image as PILImage
        from services.allsky import calibration as cal
        from services.allsky import triangle_match as tm

        _det, above = _sky(_true_model())
        detected = _random_detections(200)
        image = PILImage.new('L', (2628, 2628), color=0)
        seed = _true_model()
        matches = [((d[0], d[1]), above[i][0], (above[i][1], above[i][2]))
                   for i, d in enumerate(detected[:10])]
        monkeypatch.setattr(cal, 'measure_sky_circle',
                            lambda *a, **k: (SKY['sky_cx'], SKY['sky_cy'], SKY['sky_r']))
        monkeypatch.setattr(cal, 'detect_stars', lambda *a, **k: list(detected))
        monkeypatch.setattr(cal, '_catalog_altaz',
                            lambda *a, **k: [(s, alt, az) for s, alt, az in above])
        monkeypatch.setattr(cal, '_find_best_initial_model',
                            lambda *a, **kw: (seed, list(matches)))
        fit = _true_model()
        fit.n_matches = 10
        fit.rms_residual = 5.77
        fit.final_tol_px = 7.9
        monkeypatch.setattr(cal, '_iterative_fit', lambda *a, **kw: (fit, 5.77))
        for name in ('validate_lens_polynomial', 'validate_a1_scale',
                     'validate_bright_anchors'):
            monkeypatch.setattr(cal, name, lambda *a, **k: (True, 'ok'))

        sentinel = FisheyeModel(cx=1.0, cy=2.0, a1=3.0)
        calls = []

        def fake_triangle(*args, **kwargs):
            calls.append(kwargs)
            return sentinel
        monkeypatch.setattr(tm, 'triangle_calibrate', fake_triangle)

        result = cal.calibrate(image, LAT, LON, DT, min_matches=8,
                               max_residual_px=15.0)
        assert result is sentinel
        assert len(calls) == 1


class TestGuidedSolveIsExempt:
    def test_guided_model_is_credible_without_a_chance_ratio(self):
        from services.allsky.calibration_fit_merit import fit_is_credible
        from services.allsky.model_admission import is_user_anchored
        m = FisheyeModel(n_matches=5, rms_residual=3.0, final_tol_px=13.5,
                         chance_ratio=0.0, n_images=1, provenance='guided')
        assert is_user_anchored(m)
        ok, reason = fit_is_credible(m)
        assert ok and 'guided' in reason

    def test_calibrate_from_anchors_records_no_chance_ratio(self):
        """The guided path never runs the single-frame judge: its limit is
        the anchor RMS, and it must keep chance_ratio at 0 so
        is_user_anchored still recognises it."""
        pytest.importorskip('scipy')
        from services.allsky.catalogs import get_bright_stars
        from services.allsky.coords import radec_to_altaz
        from services.allsky.guided_calibration import calibrate_from_anchors
        true = _true_model()
        anchors = []
        for s in get_bright_stars(max_mag=2.5):
            alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, DT)
            if float(alt) < 25:
                continue
            xy = true.altaz_to_pixel(float(alt), float(az))
            if xy is not None:
                anchors.append((xy[0], xy[1], s['ra_deg'], s['dec_deg'], float(az)))
        anchors.sort(key=lambda t: t[4])
        idx = np.linspace(0, len(anchors) - 1, 6).round().astype(int)
        anchors = [anchors[i][:4] for i in dict.fromkeys(idx.tolist())]
        m = calibrate_from_anchors(anchors, LAT, LON, DT, 1137.0, 1306.0, 968.0,
                                   image_width=2628, image_height=2628)
        assert m.provenance == 'guided'
        assert m.chance_ratio == 0.0
        assert m.final_tol_px > 0.0
