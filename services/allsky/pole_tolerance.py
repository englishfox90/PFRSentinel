"""
How far a model may put the celestial pole from where it was measured.

Two consumers share one set of numbers (issue #93, package 5):

- the admission gate (`calibration_validate.validate_pole`) needs a pass/
  fail distance — `pole_tolerance_px`; so does the orientation search's
  pole filter;
- the joint fit (`joint_fit`) needs a 1σ scale for its pole pseudo-
  observation, so a solve that wants to sit far from a well-measured pole
  pays for it — `pole_sigma_px`, packaged with the pole's sky position as
  a `PoleConstraint`.

Both are derived from the estimate's own `sigma_px` (pole_from_rotation:
the fit's scatter, honest about jitter but blind to the lens model's
regional error) plus an allowance for that regional error that grows
with the pole's distance from the optical centre, and never tighter than
a floor. The issue asked for the gate to be *tightened*; the reference
night measured that a good model's pole moves by a tenth of that radius
between two admissible fits, so the gate is calibrated, not tightened.
In numbers, at a 3–10 px sigma: on the reference rig at full resolution
(a1 ≈ 1277, r_p ≈ 1230–1300 px) the gate is 145–175 px against the flat
gate's 112–124 px; on the reporter's rig (a1 1097, r_p ≈ 1124 px)
142–172 px against 125 px. It is tighter than the flat gate only for a
pole within ~1000 px of the centre. Consequences: issue #93's model,
100 px off the pole, passes both gates — package 3's chance gate is
what catches it, not this one; a correct model with the 50–70 px of
regional error the pole-anchor plan measured passes both. An estimate
with no sigma — the Polaris path, or a history entry from before sigma
existed — keeps the flat tolerance the gate always used.

The model-vs-model basin veto (`model_admission._basin_veto`) is NOT
built on these: it compares two models, not a measurement, and keeps
POLE_TOL_REF_PX.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# Flat tolerance when the estimate carries no sigma (Polaris path, legacy
# history entries). Generous by design: even a good model can carry 50-70px
# of regional error at the pole (measured on the reference rig), while
# wrong-basin fits miss it by 400-1400px. The measurement itself is good to
# ~15px. Reference resolution; scaled by tol_scale.
POLE_TOL_REF_PX = 140.0

# The gate passes a model within POLE_SIGMA_MULTIPLE sigmas of the measured
# pole plus the model-error allowance. Three: the rotation fit's sigma is a
# radial 1σ-like scale held to covering the true error in ≥ 90 % of seeded
# runs (test_pole_from_rotation), and 3σ of that is the outer bound the
# pole finder itself uses when the Polaris and rotation paths must agree.
POLE_SIGMA_MULTIPLE = 3.0

# Regional lens-model error at the pole, as a fraction of the pole's
# distance from the optical centre. The pole is where the model is worst
# (on the reporter's rig no star near it is ever detected, plan §0.3),
# and what moves it between two good models of one rig is plate scale:
# a scale error e shifts the pole by e·r_p. Measured on the reference
# rig's 2026-09-18 night (750 px, r_p = 290 px, plan §0.7): every joint
# fit that passed the anchor gate on all three recent frames — seeded
# from the guided model or found from cold — solved a1 at 0.945–0.955×
# the guided model's and sat 21–23 px (0.07–0.08·r_p) from its pole and
# 29–32 px (0.10–0.11·r_p) from the rotation pole; the guided model
# itself sat 11.5 px (0.04·r_p) from it. A fixed allowance cannot cover
# that at one resolution without being meaningless at another. 0.10 is
# also the plate-scale continuity band model_admission admits as the
# same rig: the pole gate must not veto at the pole what the scale gate
# accepts. sigma_px measures the fit's scatter, not this, so it is added
# rather than folded into the multiple.
POLE_TOL_RADIAL_FRACTION = 0.10

# The tolerance never drops below this (reference resolution) however tight
# the sigma: a 3 px sigma on a good night would otherwise gate at 39 px
# full-res, under the 50–70 px the known-good reference model has always
# projected the NCP from Polaris (pole-anchor plan) — and a gate the true
# model fails is worse than a loose one.
POLE_TOL_FLOOR_REF_PX = 40.0


def _known(sigma_px: Optional[float]) -> bool:
    return sigma_px is not None and float(sigma_px) > 0.0


def pole_tolerance_px(sigma_px: Optional[float], scale: float,
                      pole_radius_px: float) -> float:
    """Admission distance for a pole with this sigma, in frame pixels.

    `scale` is calibration_validate.tol_scale(sky_r) — the caller's, so this
    module stays free of that import; `pole_radius_px` is the measured
    pole's distance from the optical centre in the same frame. An unknown
    sigma (None or 0) means the flat POLE_TOL_REF_PX.
    """
    if not _known(sigma_px):
        return POLE_TOL_REF_PX * scale
    return max(POLE_SIGMA_MULTIPLE * float(sigma_px)
               + POLE_TOL_RADIAL_FRACTION * float(pole_radius_px),
               POLE_TOL_FLOOR_REF_PX * scale)


def pole_sigma_px(sigma_px: Optional[float], scale: float,
                  pole_radius_px: float) -> float:
    """1σ scale for the joint fit's pole pseudo-observation, in frame pixels.

    The measurement scatter and the regional model error add in quadrature
    — the fit should not be dragged across the lens's own error at the
    pole by a 3 px sigma (measured: forcing the reference night's fit onto
    the rotation pole cost it 80 % of its matches and every anchor frame,
    plan §0.7). With no sigma the flat tolerance is treated as the
    POLE_SIGMA_MULTIPLE bound, so the pull is consistent with the gate.
    """
    if not _known(sigma_px):
        return POLE_TOL_REF_PX * scale / POLE_SIGMA_MULTIPLE
    return float(np.hypot(float(sigma_px),
                          POLE_TOL_RADIAL_FRACTION * float(pole_radius_px)))


@dataclass(frozen=True)
class PoleConstraint:
    """A measured pole as the joint fit sees it: where it is on the sky
    (alt = |lat|, az north or south), where it was measured in the frame,
    and the 1σ the fit's pull is scaled by."""
    x: float
    y: float
    sigma_px: float
    alt_deg: float
    az_deg: float

    @classmethod
    def from_estimate(cls, pole, lat_deg: float, scale: float,
                      centre: Tuple[float, float]) -> Optional['PoleConstraint']:
        """None when there is no pole. `pole` is a PoleEstimate (duck-typed:
        .x, .y, .sigma_px) in the frames' own resolution; `centre` the
        optical centre the fit starts from, for the pole's radius."""
        if pole is None:
            return None
        r_p = float(np.hypot(float(pole.x) - centre[0], float(pole.y) - centre[1]))
        return cls(x=float(pole.x), y=float(pole.y),
                   sigma_px=pole_sigma_px(getattr(pole, 'sigma_px', 0.0), scale, r_p),
                   alt_deg=abs(float(lat_deg)),
                   az_deg=0.0 if float(lat_deg) >= 0 else 180.0)
