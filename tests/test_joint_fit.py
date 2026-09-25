"""joint_fit — the extracted multi-image joint fit and its measured-pole
pseudo-observation (issue #93, package 5a/5c)."""
import dataclasses
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.allsky_synth import REFERENCE, instants, synth_frames, true_pole
from services.allsky import joint_fit as JF
from services.allsky import multi_calibrate as MC
from services.allsky.calibration_validate import median_sky_r, tol_scale
from services.allsky.fisheye import FisheyeModel
from services.allsky.model_admission import projected_pole
from services.allsky.pole_estimate import PoleEstimate
from services.allsky.pole_tolerance import PoleConstraint, pole_sigma_px

pytest.importorskip('scipy')


@pytest.fixture(scope='module')
def night():
    """Three frames six minutes apart — the near-zenith regime where the
    fit can trade axis against roll, with heavy jitter and dropout."""
    return synth_frames(REFERENCE, instants(3, 6), seed=11, hide_pole_px=60,
                        with_catalog=True, dropout=0.3, jitter_px=1.5)


def _fit(frames, seed, pole):
    ts = tol_scale(median_sky_r(frames))
    matches = JF.build_all_matches(frames, seed, tol_px=50.0 * ts, min_per_image=4)
    model, rms = JF.joint_iterative_fit(matches, frames, seed, 4, 20, 20.0,
                                        tol_scale_factor=ts, pole=pole)
    return model, rms


def _pole_error(model, preset=REFERENCE):
    px, py = true_pole(preset)
    xy = projected_pole(model, preset.lat)
    return math.hypot(xy[0] - px, xy[1] - py)


def _constraint(sigma_px, preset=REFERENCE):
    px, py = true_pole(preset)
    return PoleConstraint(x=px + 3.0, y=py - 4.0, sigma_px=sigma_px,
                          alt_deg=abs(preset.lat), az_deg=0.0)


def _pole_radius(preset=REFERENCE):
    px, py = true_pole(preset)
    return math.hypot(px - preset.model.cx, py - preset.model.cy)


def _calibrated_sigma(frames, preset=REFERENCE):
    return pole_sigma_px(5.0, tol_scale(median_sky_r(frames)), _pole_radius(preset))


class TestExtraction:
    def test_multi_calibrate_re_exports_the_moved_names(self):
        assert MC._build_all_matches is JF.build_all_matches
        assert MC._joint_iterative_fit is JF.joint_iterative_fit
        assert MC._joint_rms is JF.joint_rms


class TestPoleTerm:
    def test_no_pole_leaves_the_fit_as_it_was(self, night):
        """With pole=None the residual vector carries no pole term at all,
        and the term is purely additive: a pole so wide its term vanishes
        gives the same model to numerical precision."""
        a, _ = _fit(night, dataclasses.replace(REFERENCE.model), None)
        b, _ = _fit(night, dataclasses.replace(REFERENCE.model), _constraint(1e12))
        for k in ('cx', 'cy', 'a1', 'a3', 'a5', 'roll', 'axis_alt', 'axis_az'):
            assert getattr(a, k) == pytest.approx(getattr(b, k), abs=1e-3), k
        assert a.n_matches == b.n_matches

    def test_a_correct_seed_is_not_disturbed(self, night):
        """The calibrated sigma carries the model-error allowance: a pole a
        few pixels from the truth must not distort a fit that is already
        right."""
        sigma = _calibrated_sigma(night)
        a, rms_a = _fit(night, dataclasses.replace(REFERENCE.model), None)
        b, rms_b = _fit(night, dataclasses.replace(REFERENCE.model), _constraint(sigma))
        assert rms_b == pytest.approx(rms_a, abs=0.1)
        assert _pole_error(b) < 10.0

    def test_deployed_sigma_never_leaves_a_rolled_seed_worse(self, night):
        """At the deployed sigma (pole_sigma_px, ~110 px at full resolution
        on the reference preset) the term is a no-harm prior: a seed rolled
        6° (pole 114 px off) ends no farther from the pole and within
        0.05 px RMS of the free fit. 3σ is ~330 px here, so "inside 3σ"
        would be no test at all."""
        seed = dataclasses.replace(REFERENCE.model,
                                   roll=REFERENCE.model.roll + math.radians(6.0))
        assert _pole_error(seed) > 100.0
        sigma = _calibrated_sigma(night)
        assert sigma > 90.0
        free, rms_free = _fit(night, seed, None)
        held, rms_held = _fit(night, seed, _constraint(sigma))
        assert _pole_error(held) <= _pole_error(free) + 1.0
        assert abs(rms_held - rms_free) <= 0.05

    def test_a_tight_pole_pins_the_basin(self, night):
        """A property of the term at a 1 px sigma — NOT the deployed
        behaviour (the deployed sigma is ~110 px, and forcing the real
        reference night onto its pole this way wrecked the fit, plan
        §0.7): the same 2.5°-tilted seed the free fit leaves in a wrong
        basin (RMS ~11 px, ~30 matches) is pulled into the true one. It
        proves the residual term is wired in and signed correctly."""
        m = REFERENCE.model
        seed = dataclasses.replace(m, axis_alt=m.axis_alt - 2.5,
                                   axis_az=m.axis_az + 3.75,
                                   roll=m.roll + math.radians(2.5))
        free, rms_free = _fit(night, seed, None)
        held, rms_held = _fit(night, seed, _constraint(1.0))
        assert _pole_error(free) > 100.0 and free.n_matches < 100
        assert _pole_error(held) < 15.0 and held.n_matches > 400
        assert rms_held < rms_free

    def test_off_image_pole_is_charged_not_crashed(self, night):
        con = PoleConstraint(x=100.0, y=100.0, sigma_px=5.0, alt_deg=-10.0, az_deg=0.0)
        model, rms = _fit(night, dataclasses.replace(REFERENCE.model), con)
        assert model.n_matches > 0


class TestPoleConstraintFor:
    def _pole(self, sigma=4.0):
        px, py = true_pole(REFERENCE)
        return PoleEstimate(x=px, y=py, east_left=True, sign=-1, n_frames=20,
                            span_minutes=200.0, drift_px=0.0, flux=0.0,
                            sign_votes=(0, 0), sigma_px=sigma, source='rotation')

    def test_none_without_pole_or_latitude(self, night):
        assert JF.pole_constraint_for(None, REFERENCE.lat, night) is None
        assert JF.pole_constraint_for(self._pole(), None, night) is None

    def test_constraint_from_a_trusted_pole(self, night):
        con = JF.pole_constraint_for(self._pole(), REFERENCE.lat, night)
        assert con.az_deg == 0.0 and con.alt_deg == pytest.approx(REFERENCE.lat)
        px, py = true_pole(REFERENCE)
        r_p = math.hypot(px - REFERENCE.sky_cx, py - REFERENCE.sky_cy)
        assert con.sigma_px == pytest.approx(
            pole_sigma_px(4.0, tol_scale(median_sky_r(night)), r_p))

    def test_guided_seed_that_agrees_keeps_the_constraint(self, night):
        seed = dataclasses.replace(REFERENCE.model, provenance='guided')
        assert JF.pole_constraint_for(self._pole(), REFERENCE.lat, night, seed) is not None

    def test_guided_seed_that_disagrees_drops_it(self, night):
        """User anchors outrank the measured pole (model_admission): a pole
        the guided seed contradicts must not drag the fit toward it."""
        seed = dataclasses.replace(REFERENCE.model, provenance='guided')
        px, py = true_pole(REFERENCE)
        far = dataclasses.replace(self._pole(), x=px + 400.0, y=py + 300.0)
        assert JF.pole_constraint_for(far, REFERENCE.lat, night, seed) is None
        plain = dataclasses.replace(seed, provenance='')
        assert JF.pole_constraint_for(far, REFERENCE.lat, night, plain) is not None


class TestSeedIsolation:
    def test_pole_kwarg_does_not_mutate_the_seed(self, night):
        seed = FisheyeModel(**dataclasses.asdict(REFERENCE.model))
        seed.n_matches = 321
        _fit(night, seed, _constraint(5.0))
        assert seed.n_matches == 321
