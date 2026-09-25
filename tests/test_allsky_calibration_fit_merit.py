"""
Tests for services/allsky/calibration_fit_merit.py and the two FisheyeModel
fields it reads (final_tol_px, chance_ratio).

Issue #93: the reporter's saved model — 606 matches over 60 frames spanning
63 minutes at RMS 10.9 px — rated Good on frame count alone, while 606 was
1.04x what chance supplies at its 16.5 px final tolerance and 10.9 px is that
tolerance / sqrt(2). Nothing persisted either number, so nothing could tell.
"""
import dataclasses
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky import calibration_fit_merit as fm
from services.allsky.calibration_quality import CalibrationQuality, model_quality
from services.allsky.chance_matches import CHANCE_MARGIN, chance_median_residual
from services.allsky.fisheye import FisheyeModel


def _reporter_model(**over) -> FisheyeModel:
    """The model saved on the #93 rig, values from the issue."""
    m = FisheyeModel(cx=1858.0, cy=1669.0, a1=1097.0, a3=0.0, a5=0.0,
                     roll=0.0349, axis_alt=84.16, axis_az=48.40,
                     rms_residual=10.90, n_matches=606, n_images=60,
                     span_minutes=62.6, final_tol_px=16.5, chance_ratio=1.04,
                     image_width=2840, image_height=2840)
    for k, v in over.items():
        setattr(m, k, v)
    return m


def _rig10_model(**over) -> FisheyeModel:
    """The last genuine automatic fit: #10 rig, 2026-09-05."""
    m = FisheyeModel(cx=1843.7, cy=1670.7, a1=1269.1, a3=-10.1559,
                     a5=-56.231751, rms_residual=7.8, n_matches=4561,
                     n_images=40, span_minutes=80.0, final_tol_px=16.0,
                     chance_ratio=3.4)
    for k, v in over.items():
        setattr(m, k, v)
    return m


class TestFitIsCredible:

    def test_reporters_model_is_a_chance_fit(self):
        ok, why = fm.fit_is_credible(_reporter_model())
        assert not ok
        assert why.startswith('Matches no better than chance')
        assert '0.66 of the 16.5 px' in why
        assert '1.04x' in why

    def test_last_genuine_fit_is_credible(self):
        ok, why = fm.fit_is_credible(_rig10_model())
        assert ok, why
        assert '0.49 of the 16.0 px' in why

    def test_rms_fraction_alone_fails_it(self):
        """The reference rig's Sep 17-20 fits: 0.64-0.83 of tolerance while
        the chance gate had already been passed (ratio recorded >= 2)."""
        m = _rig10_model(rms_residual=0.64 * 16.0, chance_ratio=2.2)
        ok, why = fm.fit_is_credible(m)
        assert not ok and 'match tolerance' in why

    def test_chance_ratio_alone_fails_it(self):
        m = _rig10_model(rms_residual=5.0, chance_ratio=CHANCE_MARGIN - 0.05)
        ok, why = fm.fit_is_credible(m)
        assert not ok and 'chance would supply' in why

    def test_the_line_sits_between_the_genuine_and_the_chance_bands(self):
        """Decision 5's table: 0.48 genuine, 0.64 the first chance fit;
        chance itself is 1/sqrt(2)."""
        assert 0.48 < fm.CREDIBLE_RMS_FRACTION < 0.64
        assert fm.CREDIBLE_RMS_FRACTION < chance_median_residual(1.0)
        assert fm.CHANCE_RATIO_FLOOR == CHANCE_MARGIN

    def test_exactly_on_the_line_is_credible(self):
        m = _rig10_model(rms_residual=fm.CREDIBLE_RMS_FRACTION * 16.0)
        assert fm.fit_is_credible(m)[0]
        m = _rig10_model(chance_ratio=CHANCE_MARGIN)
        assert fm.fit_is_credible(m)[0]

    def test_unknown_fields_never_fail_a_model(self):
        """A file from an earlier version keeps its rating until re-fitted."""
        legacy = _reporter_model(final_tol_px=0.0, chance_ratio=0.0)
        ok, why = fm.fit_is_credible(legacy)
        assert ok and 'not recorded' in why

    def test_one_known_field_is_judged_on_its_own(self):
        only_tol = _reporter_model(chance_ratio=0.0)
        assert not fm.fit_is_credible(only_tol)[0]
        only_ratio = _reporter_model(final_tol_px=0.0)
        assert not fm.fit_is_credible(only_ratio)[0]
        fine_tol = _rig10_model(chance_ratio=0.0)
        assert fm.fit_is_credible(fine_tol)[0]

    def test_guided_solve_is_exempt(self):
        """Its RMS limit is a pass/fail threshold over user-named anchors, not
        a match tolerance chance can imitate; the guided limit can sit at
        the RMS itself and the fit still be right."""
        m = _reporter_model(provenance='guided', n_matches=7, rms_residual=12.0,
                            final_tol_px=13.0, chance_ratio=0.0)
        ok, why = fm.fit_is_credible(m)
        assert ok and 'guided' in why

    def test_no_model(self):
        assert fm.fit_is_credible(None) == (False, "no model")

    def test_note_is_silent_for_a_credible_model(self):
        assert fm.credibility_note(_rig10_model()) == ''
        assert fm.credibility_note(_reporter_model(final_tol_px=0.0,
                                                   chance_ratio=0.0)) == ''
        assert 'chance' in fm.credibility_note(_reporter_model())


class TestModelQualityCap:

    def test_reporters_model_rates_preliminary(self):
        m = _reporter_model()
        assert model_quality(m, m.n_images, m.span_minutes) == \
            CalibrationQuality.PRELIMINARY

    def test_rig10_model_stays_excellent(self):
        m = _rig10_model()
        assert model_quality(m, m.n_images, m.span_minutes) == \
            CalibrationQuality.EXCELLENT

    def test_legacy_file_keeps_its_tier(self):
        m = _reporter_model(final_tol_px=0.0, chance_ratio=0.0)
        assert model_quality(m, m.n_images, m.span_minutes) == \
            CalibrationQuality.GOOD

    def test_the_cap_never_lifts_a_rating(self):
        """'none' (too few matches) stays 'none' whatever the fields say."""
        m = _reporter_model(n_matches=5)
        assert model_quality(m, 60, 62.6) == CalibrationQuality.NONE

    def test_guided_model_is_not_capped(self):
        m = _reporter_model(provenance='guided', n_matches=7, rms_residual=12.0,
                            final_tol_px=13.0, chance_ratio=0.0)
        assert model_quality(m, 1, 0.0) == CalibrationQuality.PRELIMINARY


class TestPersistedFields:

    def test_defaults_are_unknown(self):
        m = FisheyeModel()
        assert m.final_tol_px == 0.0 and m.chance_ratio == 0.0

    def test_round_trip_through_save_and_load(self, tmp_path):
        p = tmp_path / "allsky_calibration.json"
        _reporter_model().save(str(p))
        back = FisheyeModel.load(str(p))
        assert back.final_tol_px == 16.5
        assert back.chance_ratio == 1.04
        assert model_quality(back, back.n_images, back.span_minutes) == \
            CalibrationQuality.PRELIMINARY

    def test_legacy_file_without_the_keys_loads_as_unknown(self, tmp_path):
        p = tmp_path / "allsky_calibration.json"
        _reporter_model().save(str(p))
        data = json.loads(p.read_text())
        del data['final_tol_px']
        del data['chance_ratio']
        p.write_text(json.dumps(data))
        back = FisheyeModel.load(str(p))
        assert back.final_tol_px == 0.0 and back.chance_ratio == 0.0
        assert model_quality(back, back.n_images, back.span_minutes) == \
            CalibrationQuality.GOOD

    def test_dataclasses_replace_keeps_them(self):
        """model_in_frame rescales with dataclasses.replace; the ad-hoc
        attributes it used to drop are what left a loaded model unjudgeable."""
        m = dataclasses.replace(_reporter_model(), cx=100.0)
        assert m.final_tol_px == 16.5 and m.chance_ratio == 1.04

    def test_joint_fit_records_the_ratio_it_was_gated_on(self, monkeypatch):
        """_fit_and_validate: chance_ratio = n_matches / max(expected, 1)."""
        from services.allsky import multi_calibrate as MC
        frames = [{
            'detected': [(0.0, 0.0, 1.0)] * 30,
            'above_horizon': [({'name': 'x', 'vmag': 3.0}, 45.0, 10.0)] * 2000,
            'sky_cx': 1776.0, 'sky_cy': 1776.0, 'sky_r': 1345.0,
            'image_width': 3552, 'image_height': 3552,
        } for _ in range(53)]
        fitted = FisheyeModel(cx=1776.0, cy=1776.0, a1=856.0, rms_residual=4.0,
                              n_matches=726, n_images=53, span_minutes=300.0,
                              final_tol_px=15.5)
        matches = [[(0.0, 0.0, 45.0, 10.0)] * 20 for _ in frames]
        monkeypatch.setattr(MC, '_build_all_matches', lambda *a, **kw: matches)
        monkeypatch.setattr(MC, '_joint_iterative_fit',
                            lambda *a, **kw: (fitted, fitted.rms_residual))
        for gate in ('validate_lens_polynomial', 'validate_a1_scale',
                     'validate_bright_anchors'):
            monkeypatch.setattr(MC, gate, lambda *a, **kw: (True, 'ok'))
        out = MC._fit_and_validate(frames, fitted, 4, 20, 12.0)
        assert out.chance_expected > 1.0
        assert out.chance_ratio == pytest.approx(
            out.n_matches / out.chance_expected)
        assert out.chance_ratio >= CHANCE_MARGIN
        assert out.final_tol_px == 15.5
