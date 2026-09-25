"""
Tests for services/allsky/incumbent_chance.py — the model on disk re-judged
against the live detection buffer (issue #93: a 606-match chance fit sat
under a green badge through 62 rejected refinements, never scored itself).

The synthetic rig is #93's geometry: a 3552 px frame, a 1345 px trimmed sky
disc, 200 detections per frame (the matcher's 400-candidate pool binds) and
60 frames. Under a model whose projections land at random on that disc the
greedy matcher's count is chance_matches' expectation, so the score's ratio
must read ~1 and its RMS ~tol / sqrt(2); under the true model the same
frames score far above chance.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky import incumbent_chance as ic
from services.allsky.calibration_quality import CalibrationQuality
from services.allsky.calibration_validate import tol_scale
from services.allsky.chance_matches import CHANCE_MARGIN
from services.allsky.fisheye import FisheyeModel

FRAME_PX = 3552
SKY_R = 1345.0
CX = CY = FRAME_PX / 2.0
N_FRAMES = 60
N_DET = 200
N_CAT = 400
# Catalogue projections fill 95 % of the disc so every entry is inside it.
CAT_FILL = 0.95


def _model(**over) -> FisheyeModel:
    m = FisheyeModel(cx=CX, cy=CY, a1=SKY_R / CAT_FILL / (np.pi / 2.0),
                     a3=0.0, a5=0.0, roll=0.0, axis_alt=90.0, axis_az=0.0,
                     east_left=True, rms_residual=6.0, n_matches=500,
                     n_images=40, span_minutes=80.0,
                     image_width=FRAME_PX, image_height=FRAME_PX)
    for k, v in over.items():
        setattr(m, k, v)
    return m


def _catalogue(rng):
    """N_CAT stars whose projections under _model() are uniform over the
    inner CAT_FILL of the sky disc, sorted brightest first."""
    r = SKY_R * CAT_FILL * np.sqrt(rng.random(N_CAT))
    theta = r / _model().a1
    alt = 90.0 - np.degrees(theta)
    az = rng.random(N_CAT) * 360.0
    vmag = np.sort(rng.random(N_CAT) * 4.0 + 1.0)
    return [({'name': f's{i}', 'vmag': float(vmag[i])}, float(alt[i]),
             float(az[i])) for i in range(N_CAT)]


def _random_detections(rng, n=N_DET):
    r = SKY_R * np.sqrt(rng.random(n))
    phi = rng.random(n) * 2.0 * np.pi
    return [(float(CX + r[i] * np.cos(phi[i])),
             float(CY + r[i] * np.sin(phi[i])), 100.0) for i in range(n)]


def _true_detections(rng, model, catalogue, n=N_DET):
    alts = np.array([a for _s, a, _z in catalogue[:n]])
    azs = np.array([z for _s, _a, z in catalogue[:n]])
    px, py, _vis = model.altaz_array_to_pixels(alts, azs)
    noise = rng.normal(0.0, 1.0, size=(n, 2))
    return [(float(px[i] + noise[i, 0]), float(py[i] + noise[i, 1]), 100.0)
            for i in range(n)]


def _frames(detections_for, n_frames=N_FRAMES, seed=93):
    rng = np.random.default_rng(seed)
    catalogue = _catalogue(rng)
    t0 = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)
    return [{
        'dt': t0 + timedelta(minutes=i),
        'detected': detections_for(rng, catalogue),
        'above_horizon': catalogue,
        'sky_cx': CX, 'sky_cy': CY, 'sky_r': SKY_R,
        'image_width': FRAME_PX, 'image_height': FRAME_PX,
    } for i in range(n_frames)]


@pytest.fixture(scope='module')
def random_buffer():
    return _frames(lambda rng, cat: _random_detections(rng))


@pytest.fixture(scope='module')
def true_buffer():
    model = _model()
    return _frames(lambda rng, cat: _true_detections(rng, model, cat))


def _tol():
    return ic.score_tolerance_px(SKY_R)


class TestScoreIncumbent:

    def test_tolerance_is_the_joint_fits_final_one(self):
        assert _tol() == pytest.approx(18.0 * tol_scale(SKY_R))

    def test_random_projections_score_at_chance(self, random_buffer):
        """The #93 shape: the count chance_matches predicts, an RMS of
        tol / sqrt(2). This is the score the reporter's model would get."""
        score = ic.score_incumbent(_model(), random_buffer, _tol())
        assert score is not None
        assert score.n_frames == N_FRAMES
        assert 0.85 < score.ratio < 1.15, score.describe()
        assert score.rms == pytest.approx(_tol() / np.sqrt(2.0), rel=0.10)
        assert score.chance_level

    def test_the_true_model_scores_far_above_chance(self, true_buffer):
        score = ic.score_incumbent(_model(), true_buffer, _tol())
        assert score is not None
        assert score.ratio > 3.0 * CHANCE_MARGIN, score.describe()
        assert score.rms < 3.0
        assert not score.chance_level

    def test_one_match_pass_is_well_inside_the_budget(self, random_buffer):
        """ALLSKY_HOSTING_SITE_PLAN section 8: at most 1 s per refinement run
        on a 60-frame buffer — one match pass at the final tolerance, no
        re-fit, no image access."""
        ic.score_incumbent(_model(), random_buffer, _tol())   # warm caches
        # CPU time of this process, best of three: the budget is what the
        # call burns on the observatory PC (0.17-0.20 s wall clock measured
        # alone), not how the CI runner schedules a four-worker suite —
        # wall clock reached 1.6 s there while the call itself was unchanged.
        elapsed = []
        for _ in range(3):
            t0 = time.process_time()
            ic.score_incumbent(_model(), random_buffer, _tol())
            elapsed.append(time.process_time() - t0)
        assert min(elapsed) < 1.0, [f"{t:.3f} s CPU" for t in elapsed]

    def test_cloudy_buffer_is_not_scored(self, random_buffer):
        """Under SCORE_MIN_DETECTIONS median detections the buffer says
        nothing about the model: None, and never a strike."""
        thin = [dict(f, detected=f['detected'][:ic.SCORE_MIN_DETECTIONS - 1])
                for f in random_buffer]
        assert ic.score_incumbent(_model(), thin, _tol()) is None

    def test_median_not_mean_decides_the_guard(self, random_buffer):
        """A few clear frames in a cloudy buffer don't lift the median."""
        mixed = [dict(f, detected=f['detected'][:10]) for f in random_buffer]
        for f in mixed[:5]:
            f['detected'] = random_buffer[0]['detected']
        assert ic.score_incumbent(_model(), mixed, _tol()) is None

    def test_nothing_to_judge(self, random_buffer):
        assert ic.score_incumbent(None, random_buffer, _tol()) is None
        assert ic.score_incumbent(_model(), [], _tol()) is None
        assert ic.score_incumbent(_model(n_matches=0), random_buffer, _tol()) is None

    def test_model_is_rescaled_into_the_buffers_resolution(self, true_buffer):
        """The buffer holds preview-resolution frames; a manual calibration
        is at raw resolution. Scored unscaled, the true model would read as
        chance."""
        m = _model()
        raw = FisheyeModel(
            cx=m.cx * 2, cy=m.cy * 2, a1=m.a1 * 2, a3=0.0, a5=0.0,
            roll=0.0, axis_alt=90.0, axis_az=0.0, east_left=True,
            rms_residual=6.0, n_matches=500, n_images=40, span_minutes=80.0,
            image_width=FRAME_PX * 2, image_height=FRAME_PX * 2)
        score = ic.score_incumbent(raw, true_buffer, _tol())
        assert score is not None and not score.chance_level, score.describe()

    def test_a_matcher_failure_is_not_a_verdict(self, random_buffer, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("no scipy today")
        monkeypatch.setattr(ic, '_build_all_matches', boom)
        assert ic.score_incumbent(_model(), random_buffer, _tol()) is None


def _score(ratio):
    return ic.IncumbentScore(n_matches=int(600 * ratio), expected=600.0,
                             ratio=ratio, rms=10.9, tol_px=15.5, n_frames=60)


class TestIncumbentChanceStreak:

    def test_two_chance_level_runs_discredit(self):
        s = ic.IncumbentChanceStreak()
        assert s.record(_score(1.04)) is False
        assert not s.discredited
        assert s.record(_score(1.10)) is True
        assert s.discredited

    def test_one_run_is_a_passing_cloud(self):
        s = ic.IncumbentChanceStreak()
        s.record(_score(0.9))
        assert not s.discredited and s.strikes == 1

    def test_one_credible_run_clears(self):
        s = ic.IncumbentChanceStreak()
        s.record(_score(1.0))
        s.record(_score(1.0))
        assert s.record(_score(4.0)) is True
        assert not s.discredited and s.strikes == 0

    def test_unscored_run_is_neither_strike_nor_clearance(self):
        s = ic.IncumbentChanceStreak()
        s.record(_score(1.0))
        assert s.record(None) is False
        assert s.strikes == 1
        s.record(_score(1.0))
        assert s.discredited
        assert s.record(None) is False
        assert s.discredited

    def test_no_repeat_emission_while_it_stays_discredited(self):
        s = ic.IncumbentChanceStreak()
        s.record(_score(1.0))
        s.record(_score(1.0))
        assert s.record(_score(1.2)) is False

    def test_the_margin_is_the_chance_gates(self):
        s = ic.IncumbentChanceStreak()
        s.record(_score(CHANCE_MARGIN))
        assert s.strikes == 0
        s.record(_score(CHANCE_MARGIN - 0.01))
        assert s.strikes == 1

    def test_guided_model_is_never_discredited(self):
        s = ic.IncumbentChanceStreak()
        guided = _model(provenance='guided', n_matches=7, rms_residual=4.1)
        for _ in range(3):
            assert s.record(_score(1.0), guided) is False
        assert s.strikes == 0 and not s.discredited
        assert s.note('preliminary') == ''
        # The same scores against an automatic model do count.
        s.record(_score(1.0), _model())
        s.record(_score(1.0), _model())
        assert s.discredited

    def test_reset_for_a_new_model(self):
        s = ic.IncumbentChanceStreak()
        s.record(_score(1.0))
        s.record(_score(1.0))
        s.reset()
        assert not s.discredited and s.strikes == 0

    def test_note_is_the_measurement_and_the_saved_rating(self):
        s = ic.IncumbentChanceStreak()
        assert s.note('good') == ''
        s.record(_score(1.04))
        assert s.note('good') == ''
        s.record(_score(1.04))
        assert s.note('good') == ("Matched 624 stars vs 600 expected by chance "
                                  "at 15.5 px in 2 consecutive runs (rating "
                                  "from when it was saved: good)")
        s.record(_score(3.0))
        assert s.note('good') == ''

    def test_cap_holds_the_badge_at_preliminary_while_discredited(self):
        s = ic.IncumbentChanceStreak()
        assert s.cap('excellent') == 'excellent'
        s.record(_score(1.0))
        s.record(_score(1.0))
        assert s.cap('excellent') == CalibrationQuality.PRELIMINARY
        assert s.cap('good') == CalibrationQuality.PRELIMINARY
        assert s.cap('preliminary') == 'preliminary'
        assert s.cap('none') == 'none'


class TestCalibratedStatus:

    def test_reporters_model_reads_preliminary_at_chance(self):
        m = _model(rms_residual=10.90, n_matches=606)
        assert ic.calibrated_status(m, 'good', chance_level=True) == (
            "Calibrated: 606 stars, RMS=10.9px "
            "(preliminary — matches at chance level)")

    def test_credible_model_keeps_its_rating(self):
        m = _model(rms_residual=7.8, n_matches=4561)
        assert ic.calibrated_status(m, 'excellent', chance_level=False) == (
            "Calibrated: 4561 stars, RMS=7.8px (excellent)")
