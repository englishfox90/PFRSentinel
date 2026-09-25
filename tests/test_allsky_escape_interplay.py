"""What rule 3 does, and what the chance gate does with the #33 candidate.

`model_replacement` rule 3 lets a basin escape replace an incumbent that missed
the bright stars, with no RMS comparison at all. Read alone, its rationale can
sound like a claim that issue #33's 726-match joint fit should have been
installed once the five-star incumbent's 2.43 px veto was lifted. It should
not: on that rig the fit sits exactly at chance, and `multi_calibrate`'s chance
gate rejects it before `should_replace` is ever asked. These tests pin both
halves of that — the #33 candidate never reaches replacement, and a candidate
that IS informative reaches it and wins.

The rig is #33's: 3552x3552 px, trimmed sky radius 1345 px, 53 frames, 200
detections per frame (so the matcher's 400-candidate cap binds) and a final
re-match tolerance of 15.5 px. Chance there is ~10.35 matches per frame,
~549 over the buffer, so 726 matches is 1.32x chance against CHANCE_MARGIN 2.
"""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.calibration import CalibrationError
from services.allsky.chance_matches import CHANCE_MARGIN, estimate_chance
from services.allsky.fisheye import FisheyeModel
from services.allsky.model_replacement import should_replace

# Issue #33's rig.
SKY_R = 1345.0
FRAME_PX = 3552
CX = CY = FRAME_PX / 2.0
N_FRAMES = 53
FINAL_TOL_PX = 15.5
N_MATCHES_33 = 726
RMS_33 = 10.96          # == 15.5 / sqrt(2): the median residual of a chance pair
# A residual a real fit reports at that tolerance (0.45 of it; the credibility
# line in calibration_fit_merit is 0.55). Rule 3 waives the incumbent's RMS
# veto only for a candidate that is itself credible.
RMS_CREDIBLE = 7.0

# Detections per frame. 200 is the reporter's rig (400-candidate pool, the
# matcher's cap); 30 is a sparse rig whose pool is 5 * 30 = 150, putting chance
# an order of magnitude lower for the same tolerance.
RIG_33_DETECTIONS = 200
SPARSE_DETECTIONS = 30


def _rig_frame(n_det):
    """One #33-geometry frame. Every catalogue entry projects inside the sky
    disc under `_candidate_model`, so `frame_pool` sees the full pool."""
    return {
        'detected': [(0.0, 0.0, 1.0)] * n_det,
        'above_horizon': [({'name': 'x', 'vmag': 3.0}, 45.0, 10.0)] * 2000,
        'sky_cx': CX, 'sky_cy': CY, 'sky_r': SKY_R,
        'image_width': FRAME_PX, 'image_height': FRAME_PX,
    }


def _rig_frames(n_det):
    return [_rig_frame(n_det) for _ in range(N_FRAMES)]


def _candidate_model(n_matches=N_MATCHES_33, rms=RMS_33):
    m = FisheyeModel(cx=CX, cy=CY, a1=SKY_R / (np.pi / 2.0), a3=0.0, a5=0.0,
                     roll=0.0, axis_alt=90.0, axis_az=0.0, east_left=True,
                     rms_residual=rms, n_matches=n_matches, n_images=N_FRAMES,
                     span_minutes=300.0)
    m.final_tol_px = FINAL_TOL_PX
    return m


def _five_star_incumbent():
    """#33's incumbent: 8 parameters fitted to 5 matches in one image, so its
    2.43 px is what the arithmetic must produce, not a measurement."""
    return FisheyeModel(cx=CX, cy=CY, a1=SKY_R / (np.pi / 2.0),
                        rms_residual=2.43, n_matches=5, n_images=1,
                        span_minutes=0.0)


def _run_fit_and_validate(monkeypatch, frames, model):
    """Drive `_fit_and_validate` with the joint fit replaced by `model`, so the
    gates — and only the gates — decide. The lens-physics checks that follow
    the chance gate are stubbed: this exercises the chance gate, not them."""
    from services.allsky import multi_calibrate as MC

    matches = [[(0.0, 0.0, 45.0, 10.0)] * 20 for _ in frames]
    monkeypatch.setattr(MC, '_build_all_matches', lambda *a, **kw: matches)
    monkeypatch.setattr(MC, '_joint_iterative_fit',
                        lambda *a, **kw: (model, model.rms_residual))
    monkeypatch.setattr(MC, 'validate_lens_polynomial',
                        lambda *a, **kw: (True, 'polynomial ok'))
    monkeypatch.setattr(MC, 'validate_a1_scale',
                        lambda *a, **kw: (True, 'scale ok'))
    monkeypatch.setattr(MC, 'validate_bright_anchors',
                        lambda *a, **kw: (True, 'anchors ok'))
    return MC._fit_and_validate(frames, model, 4, 20, 12.0)


def _expected_in_message(msg):
    m = re.search(r'vs ([\d.]+) expected by chance', msg)
    assert m, msg
    return float(m.group(1))


class TestChanceGateGuardsRuleThree:
    """Rule 3 removes the thin incumbent's RMS veto. It does not hand the win
    to whatever the escape produced — the chance gate still has to be cleared
    first, and on the #33 rig itself nothing clears it."""

    def test_the_33_726_match_fit_is_at_chance_and_never_reaches_replacement(
            self, monkeypatch):
        frames = _rig_frames(RIG_33_DETECTIONS)
        model = _candidate_model()

        with pytest.raises(CalibrationError) as exc:
            _run_fit_and_validate(monkeypatch, frames, model)

        msg = str(exc.value)
        assert 'at chance level' in msg
        expected = _expected_in_message(msg)
        assert expected == pytest.approx(549.0, rel=0.10), msg
        assert N_MATCHES_33 < CHANCE_MARGIN * expected
        assert model.rms_residual == pytest.approx(FINAL_TOL_PX / np.sqrt(2.0),
                                                   abs=0.01)

    def test_a_candidate_above_chance_replaces_an_incumbent_that_missed_the_anchors(
            self, monkeypatch):
        """The rig rule 3 is for: the same 726 matches, but over frames where
        chance supplies only ~31, so the count is informative (23x), at a
        residual a real fit reports. It clears the gate, and the five-star
        incumbent's 2.43 px does not veto it."""
        frames = _rig_frames(SPARSE_DETECTIONS)
        fitted = _run_fit_and_validate(monkeypatch, frames,
                                       _candidate_model(rms=RMS_CREDIBLE))

        assert fitted.chance_expected == pytest.approx(31.4, rel=0.05)
        assert fitted.n_matches >= CHANCE_MARGIN * fitted.chance_expected
        assert fitted.chance_ratio > 20.0

        ok, why = should_replace(_five_star_incumbent(), 'none', fitted, 'good',
                                 escape=True, evidence=False,
                                 incumbent_failed_anchors=True)
        assert ok, why
        assert 'bright-anchor' in why and 'does not get an RMS veto' in why

    def test_a_candidate_at_chance_scatter_does_not_get_the_rule_3_bypass(
            self, monkeypatch):
        """Same frames, same 726 informative matches, but the residual is the
        chance pair's tol / sqrt(2): the count says sky, the residual says
        tolerance. The discredited incumbent loses its veto, yet what replaces
        it must be sky-worthy itself, so this one is held to the normal
        comparison — which it loses to the (incomparable) five-star model only
        on rank, and here on nothing: the reason names the candidate's merit."""
        frames = _rig_frames(SPARSE_DETECTIONS)
        fitted = _run_fit_and_validate(monkeypatch, frames, _candidate_model())
        assert fitted.rms_residual == pytest.approx(FINAL_TOL_PX / np.sqrt(2.0),
                                                    abs=0.01)

        ok, why = should_replace(_five_star_incumbent(), 'good', fitted, 'good',
                                 escape=True, evidence=False,
                                 incumbent_failed_anchors=True)
        assert not ok, why
        assert 'no merit of its own' in why and 'match tolerance' in why
        assert 'does not get an RMS veto' not in why

    def test_the_same_candidate_wins_on_rank_when_the_incumbent_kept_its_anchors(
            self, monkeypatch):
        """Rule 3 is not the only route past a five-star incumbent: its RMS is
        below COMPARABLE_MIN_MATCHES either way, so with anchor health unknown
        or fine the rank upgrade still carries the candidate."""
        frames = _rig_frames(SPARSE_DETECTIONS)
        fitted = _run_fit_and_validate(monkeypatch, frames, _candidate_model())

        ok, why = should_replace(_five_star_incumbent(), 'none', fitted, 'good',
                                 escape=True, evidence=False,
                                 incumbent_failed_anchors=False)
        assert ok, why
        assert 'not comparable' in why and 'rank upgrade' in why

    def test_chance_expectation_scales_with_the_detection_pool(self):
        """The two scenarios differ only in detections per frame: 200 binds the
        matcher's 400-candidate cap (~549 expected), 30 gives a 150-entry pool
        (~31). Same tolerance, same sky radius, same 53 frames."""
        model = _candidate_model()
        dense = estimate_chance(_rig_frames(RIG_33_DETECTIONS), model,
                                FINAL_TOL_PX)
        sparse = estimate_chance(_rig_frames(SPARSE_DETECTIONS), model,
                                 FINAL_TOL_PX)

        assert dense.n_frames == sparse.n_frames == N_FRAMES
        assert dense.expected == pytest.approx(548.4, rel=0.01)
        assert sparse.expected == pytest.approx(31.4, rel=0.01)
        assert dense.ratio(N_MATCHES_33) == pytest.approx(1.32, abs=0.01)
        assert sparse.ratio(N_MATCHES_33) > 20.0
