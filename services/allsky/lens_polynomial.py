"""
Radial lens polynomial geometry: r(θ) = a1·θ + a3·θ³ + a5·θ⁵.

Pure maths on the three radial coefficients of a FisheyeModel — no observer,
no detections, no catalog, no I/O. `calibration_validate.validate_lens_polynomial`
uses it to reject fits whose radial curve turns over (folds two sky altitudes
onto one pixel radius) inside the field the camera actually images.
"""
import math
from typing import List, Optional, Tuple


# Where the monotonicity requirement stops, in degrees from the optical axis.
#
# It deliberately stops short of the horizon. On every real rig measured here
# the illuminated disc ends well above θ=90° — the reference rig's edge sits at
# ~17° altitude (θ≈73°) and the issue-#10 rig's at ~23° (θ≈67°) — so the last
# stretch of the polynomial is unconstrained extrapolation beyond any star the
# fit ever saw (validate_bright_anchors calls it extrapolation already below 40°
# altitude). Both known-good production models turn over there: the reference
# multi-image model (a1=1277.18, a3=-47.57, a5=-58.92) at 78.0° and the #10
# guided solve at 77.8°. Requiring monotonicity all the way to 90° would reject
# both, and with them every incumbent they vouch for (model_admission).
#
# 70° (20° altitude) keeps ~8° of margin under those two while still failing any
# fit that folds sky the camera genuinely images.
MONOTONIC_MAX_THETA_DEG = 70.0


def radial_slope(model, theta_rad: float) -> float:
    """dr/dθ = a1 + 3·a3·θ² + 5·a5·θ⁴ at `theta_rad`."""
    a1, a3, a5 = _coefficients(model)
    t2 = theta_rad * theta_rad
    return a1 + 3.0 * a3 * t2 + 5.0 * a5 * t2 * t2


def radial_turnover_deg(model, limit_deg: float = 90.0) -> Optional[float]:
    """First θ in (0, limit_deg] where dr/dθ reaches zero, or None.

    dr/dθ is a quadratic in u = θ² with g(0) = a1 > 0, so the smallest positive
    root of `5·a5·u² + 3·a3·u + a1` is the turnover — solved, not sampled.
    """
    a1, a3, a5 = _coefficients(model)
    if a1 <= 0.0:
        return 0.0
    u_limit = math.radians(limit_deg) ** 2
    for u in _positive_roots(5.0 * a5, 3.0 * a3, a1):
        if u <= u_limit:
            return math.degrees(math.sqrt(u))
    return None


def radial_monotonic(
    model,
    max_theta_deg: float = MONOTONIC_MAX_THETA_DEG,
) -> Tuple[bool, Optional[float]]:
    """(ok, turnover_theta_deg) for the model's radial polynomial.

    `ok` is False when r(θ) stops increasing at or before `max_theta_deg`.
    The turnover angle is reported whenever one exists anywhere below the
    horizon, including the out-of-field case that passes — callers put it in
    their message rather than discarding it.
    """
    turnover = radial_turnover_deg(model, 90.0)
    return (turnover is None or turnover > max_theta_deg), turnover


def _coefficients(model) -> Tuple[float, float, float]:
    return (float(getattr(model, 'a1', 0.0)),
            float(getattr(model, 'a3', 0.0)),
            float(getattr(model, 'a5', 0.0)))


def _positive_roots(a: float, b: float, c: float) -> List[float]:
    """Real roots of a·u² + b·u + c in ascending order, u > 0 only."""
    if a == 0.0:
        if b == 0.0:
            return []
        u = -c / b
        return [u] if u > 0.0 else []
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return []
    root = math.sqrt(disc)
    candidates = ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a))
    return sorted(u for u in candidates if u > 0.0)
