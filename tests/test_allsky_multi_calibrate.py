"""Chance-match gating and cold-start winner selection in multi_calibrate.

Issue #33: on a high-resolution rig the joint fit returns essentially the same
match count and RMS for every orientation, so the `min_total_matches` floor
passes wrong basins and ranking bootstrap candidates by raw match count is a
coin flip. These tests pin the two places that now consult `chance_matches`.
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.bootstrap_selection import (
    CLOSE_EXCESS_FRAC, RIVAL_MAX_STAR_OVERLAP, RIVAL_MIN_EXCESS_FRACTION,
    chance_excess, find_rival, matched_star_ids, recent_anchor_hits,
    select_bootstrap_winner, star_overlap)
from services.allsky.calibration import CalibrationError
from services.allsky.chance_matches import ChanceEstimate
from services.allsky.fisheye import FisheyeModel

LAT, LON = 31.33, -100.46


def _true_model():
    return FisheyeModel(
        cx=1137.44, cy=1306.0, a1=643.39, a3=1.30, a5=-7.73,
        roll=-0.321, axis_alt=82.55, axis_az=16.90, east_left=True)


def _obstructed(x, y):
    if 1150 < x < 1900 and 1150 < y < 2100:
        return True
    return math.hypot(x - 1137, y - 1306) > 980


def _frame(true_model, dt):
    from services.allsky.catalogs import get_bright_stars
    from services.allsky.coords import radec_to_altaz
    rng = np.random.default_rng(7)
    above, detected = [], []
    for s in get_bright_stars(max_mag=4.0):
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, dt)
        alt, az = float(alt), float(az)
        if alt <= 3.0:
            continue
        above.append((s, alt, az))
        if alt < 12:
            continue
        xy = true_model.altaz_to_pixel(alt, az)
        if xy is None or _obstructed(*xy):
            continue
        detected.append((xy[0] + rng.normal(), xy[1] + rng.normal(), 1000.0))
    above.sort(key=lambda t: t[0]['vmag'])
    return {'dt': dt, 'detected': detected, 'above_horizon': above,
            'sky_cx': 1137.0, 'sky_cy': 1306.0, 'sky_r': 968.0,
            'image_width': 2628, 'image_height': 2628}


@pytest.fixture(scope='module')
def synthetic_frames():
    base = datetime(2026, 6, 22, 5, 30, tzinfo=timezone.utc)
    tm = _true_model()
    return [_frame(tm, base + timedelta(minutes=20 * i)) for i in range(5)]


class TestChanceGate:
    """_fit_and_validate must reject a fit whose matches are at chance level."""

    def test_healthy_fit_is_far_above_chance(self, synthetic_frames):
        """Seeded with truth the fit converges at iteration 0, so the gate is
        judged at the loosest tolerance (31px here) — the worst case for the
        estimate, and still ~4.7x chance against a CHANCE_MARGIN of 2."""
        pytest.importorskip('scipy')
        from services.allsky import multi_calibrate as MC
        model = MC._fit_and_validate(synthetic_frames, _true_model(), 4, 20, 20.0)
        assert model.chance_expected > 0
        assert model.n_matches > 4.0 * model.chance_expected, (
            f"n_matches={model.n_matches} chance={model.chance_expected:.1f}")

    def test_fit_at_chance_level_is_rejected(self, synthetic_frames, monkeypatch):
        pytest.importorskip('scipy')
        from services.allsky import multi_calibrate as MC

        def _at_chance(n_matches, frames, model, tol_px, **kw):
            # The reporter's ratio: 686 matches against 619 expected.
            est = ChanceEstimate(expected=n_matches / 1.108, tol_px=tol_px,
                                 n_frames=len(frames),
                                 median_residual_px=tol_px / math.sqrt(2))
            return False, est.describe(n_matches), est

        monkeypatch.setattr(MC, 'check_above_chance', _at_chance)
        with pytest.raises(CalibrationError) as exc:
            MC._fit_and_validate(synthetic_frames, _true_model(), 4, 20, 20.0)
        assert 'chance' in str(exc.value).lower()

    def test_gate_is_judged_at_the_tolerance_the_matches_used(self,
                                                              synthetic_frames):
        """The joint fit can stop early, so the expectation must be taken at the
        tolerance the surviving matches were built at, not the schedule's end."""
        pytest.importorskip('scipy')
        from services.allsky import multi_calibrate as MC
        from services.allsky.calibration_validate import tol_scale
        from services.allsky.chance_matches import expected_chance_matches

        model = MC._fit_and_validate(synthetic_frames, _true_model(), 4, 20, 20.0)
        ts = tol_scale(MC.median_sky_r(synthetic_frames))
        schedule = {ts * max(18.0, 50.0 - 5.0 * i) for i in range(10)}
        assert any(abs(model.final_tol_px - t) < 1e-9 for t in schedule)
        assert model.chance_expected == pytest.approx(
            expected_chance_matches(synthetic_frames, model,
                                    model.final_tol_px))


class TestBootstrapSelection:
    """The cold-start winner is the candidate with the most excess over chance,
    not the most raw matches."""

    def _candidate(self, n_matches, chance_expected, model=None):
        m = model or _true_model()
        m.n_matches = n_matches
        m.chance_expected = chance_expected
        m.rms_residual = 10.0
        return m

    def test_higher_excess_beats_higher_raw_count(self, synthetic_frames):
        chancey = self._candidate(700, 650.0)      # excess 50, 1.08x chance
        real = self._candidate(400, 40.0)          # excess 360, 10x chance
        best, why = select_bootstrap_winner([chancey, real], synthetic_frames)
        assert best is real, why
        assert 'excess=360' in why

    def test_refine_from_detections_picks_the_higher_excess_candidate(
            self, synthetic_frames, monkeypatch):
        from services.allsky import multi_calibrate as MC

        chancey = self._candidate(700, 650.0)
        real = self._candidate(400, 40.0)
        seeds = [object(), object()]
        prepared = {id(seeds[0]): chancey, id(seeds[1]): real}

        monkeypatch.setattr(MC, '_coarse_orientation_candidates',
                            lambda frames, **kw: seeds)
        monkeypatch.setattr(MC, '_fit_and_validate',
                            lambda frames, seed, *a, **kw: prepared[id(seed)])

        best = MC.refine_from_detections(synthetic_frames, None)
        assert best is real
        assert best.n_matches < chancey.n_matches

    def test_anchor_hits_break_a_tie_on_excess(self, synthetic_frames):
        """Candidates within CLOSE_EXCESS_FRAC of the best excess are
        indistinguishable there, so the anchor gate decides."""
        good = self._candidate(400, 40.0)
        wrong = _true_model()
        wrong.roll += math.radians(35.0)
        wrong.axis_az += 30.0
        wrong = self._candidate(410, 40.0, wrong)

        assert chance_excess(wrong) > chance_excess(good)  # wrong leads on excess
        assert (chance_excess(good)
                >= CLOSE_EXCESS_FRAC * chance_excess(wrong))  # ...but is tied
        assert (recent_anchor_hits(good, synthetic_frames)
                > recent_anchor_hits(wrong, synthetic_frames))

        best, _why = select_bootstrap_winner([wrong, good], synthetic_frames)
        assert best is good

    def test_empty_candidate_list(self):
        assert select_bootstrap_winner([], []) == (None, '')

    def test_missing_chance_expected_falls_back_to_raw_count(self):
        legacy = _true_model()
        legacy.n_matches = 120
        assert chance_excess(legacy) == 120.0

    def test_close_frac_threshold_floors_negative_best_excess(self):
        """chance_excess(m) >= close_frac * best_excess inverts when
        best_excess is negative — 0.75 * -10 == -7.5, a HIGHER bar than -10
        itself, so even the best candidate would fail its own cut. Flooring
        best_excess at 0 before scaling keeps the threshold sane (unreachable
        via the chance gate today, which guarantees excess >= 0)."""
        assert CLOSE_EXCESS_FRAC * max(-10.0, 0.0) == 0.0

    def test_all_below_chance_returns_the_least_bad_candidate(self,
                                                              synthetic_frames):
        """The 0.0 floor makes the shortlist cut unsatisfiable when every
        candidate is below chance — nothing clears 0.0. The fallback must rank
        the whole list by excess rather than indexing an empty shortlist."""
        worst = self._candidate(300, 900.0)        # excess -600
        least_bad = self._candidate(500, 560.0)    # excess -60
        middle = self._candidate(400, 700.0)       # excess -300

        best, why = select_bootstrap_winner([worst, least_bad, middle],
                                            synthetic_frames)
        assert best is least_bad, why
        assert 'excess=-60' in why
        assert 'tied=3' in why


class TestJointFitSeedIsolation:
    """A seeded refinement's seed_model can be the live
    CalibrationService._model the GUI thread renders from — _joint_iterative_fit
    must never write onto it."""

    def test_least_squares_failure_does_not_mutate_seed(
            self, synthetic_frames, monkeypatch):
        pytest.importorskip('scipy')
        import copy
        from services.allsky import multi_calibrate as MC
        from services.allsky.calibration_validate import tol_scale

        seed = copy.deepcopy(_true_model())
        seed.n_matches = 321
        seed.rms_residual = 4.5

        ts = tol_scale(MC.median_sky_r(synthetic_frames))
        matches = MC._build_all_matches(
            synthetic_frames, seed, tol_px=50.0 * ts, min_per_image=4)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated least_squares failure")
        from services.allsky import joint_fit as JF
        monkeypatch.setattr(JF, '_least_squares', _boom)

        model, _rms = MC._joint_iterative_fit(
            matches, synthetic_frames, seed, 4, 20, 20.0, tol_scale_factor=ts)

        assert model is not seed
        assert seed.n_matches == 321
        assert seed.rms_residual == 4.5


class TestRivalTest:
    """Issue #93, package 5e: a second survivor that explains the frames with
    a mostly different star set at a comparable excess means the night
    cannot tell two solutions apart, and the escape yields nothing."""

    def _fit(self, n_matches, chance, stars):
        m = _true_model()
        m.n_matches = n_matches
        m.chance_expected = chance
        m.rms_residual = 3.0
        m.matched_stars = [{'hr': str(h), 'name': ''} for h in stars]
        return m

    def test_star_ids_prefer_hr_and_skip_blanks(self):
        m = _true_model()
        m.matched_stars = [{'hr': '7001', 'name': 'Vega'}, {'hr': '', 'name': 'Deneb'},
                           {'hr': '', 'name': ''}, {'hr': '7001', 'name': 'Vega'}]
        assert matched_star_ids(m) == {'7001', 'Deneb'}

    def test_same_basin_is_not_a_rival(self):
        winner = self._fit(400, 40.0, range(100))
        same = self._fit(380, 40.0, range(10, 110))          # 90 % overlap
        assert star_overlap(same, winner) == pytest.approx(0.9)
        assert find_rival([winner, same], winner) is None

    def test_different_star_set_at_half_the_excess_is_a_rival(self):
        winner = self._fit(400, 40.0, range(100))
        rival = self._fit(240, 40.0, range(200, 300))        # 0 % overlap, excess 200
        assert chance_excess(rival) >= RIVAL_MIN_EXCESS_FRACTION * chance_excess(winner)
        assert find_rival([winner, rival], winner) is rival

    def test_weak_rival_is_ignored(self):
        winner = self._fit(400, 40.0, range(100))
        weak = self._fit(150, 40.0, range(200, 300))         # excess 110 < 180
        assert find_rival([winner, weak], winner) is None

    def test_overlap_threshold_is_the_boundary(self):
        winner = self._fit(400, 40.0, range(100))
        half = self._fit(300, 40.0, list(range(50)) + list(range(200, 250)))
        assert star_overlap(half, winner) == pytest.approx(RIVAL_MAX_STAR_OVERLAP)
        assert find_rival([winner, half], winner) is None
        under = self._fit(300, 40.0, list(range(49)) + list(range(200, 251)))
        assert find_rival([winner, under], winner) is under

    def test_no_diagnostics_means_no_verdict(self):
        winner = self._fit(400, 40.0, range(100))
        blank = self._fit(300, 40.0, [])
        assert find_rival([winner, blank], winner) is None

    def test_refine_from_detections_withholds_on_a_rival(self, synthetic_frames,
                                                        monkeypatch):
        from services.allsky import multi_calibrate as MC
        winner = self._fit(400, 40.0, range(100))
        rival = self._fit(240, 40.0, range(200, 300))
        seeds = [object(), object()]
        prepared = {id(seeds[0]): winner, id(seeds[1]): rival}
        monkeypatch.setattr(MC, '_coarse_orientation_candidates',
                            lambda frames, **kw: seeds)
        monkeypatch.setattr(MC, 'search_frames', lambda frames, ring=None: [])
        monkeypatch.setattr(MC, '_fit_and_validate',
                            lambda frames, seed, *a, **kw: prepared[id(seed)])
        with pytest.raises(CalibrationError) as exc:
            MC.refine_from_detections(synthetic_frames, None)
        assert 'two incompatible solutions' in str(exc.value)

    def test_refine_from_detections_returns_the_winner_without_a_rival(
            self, synthetic_frames, monkeypatch):
        from services.allsky import multi_calibrate as MC
        winner = self._fit(400, 40.0, range(100))
        same = self._fit(380, 40.0, range(10, 110))
        seeds = [object(), object()]
        prepared = {id(seeds[0]): winner, id(seeds[1]): same}
        monkeypatch.setattr(MC, '_coarse_orientation_candidates',
                            lambda frames, **kw: seeds)
        monkeypatch.setattr(MC, 'search_frames', lambda frames, ring=None: [])
        monkeypatch.setattr(MC, '_fit_and_validate',
                            lambda frames, seed, *a, **kw: prepared[id(seed)])
        assert MC.refine_from_detections(synthetic_frames, None) is winner
