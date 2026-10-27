"""
Is the fisheye's sky circle cropped by the frame?

The all-sky stack reads the measured sky circle as the horizon: a1 is seeded
and gated from its radius (calibration_validate) and star detection is masked
to it. That holds on a square sensor, where the whole circle is in frame. On a
wide sensor it does not: an ASI662MC (1920x1080) behind a ~2.2 mm lens has its
horizon ~1200 px from the centre, the circle scan can only report an edge
inside half the frame height (r=448), and a guided solve whose eight anchors
fit to 0-1 px was rejected as 2.28x "too large" for that circle every time
(2026-10-26, Mac mini rig).

When this says the circle is cropped, the circle is no horizon: callers drop
the a1-vs-circle gate and search the whole frame for stars. The circle radius
is still a usable pixel unit for tolerances (tol_scale), so it is kept.
"""
import math
from typing import Tuple

# Frames wider/taller than this ratio almost certainly crop the fisheye circle.
# Square-ish sensors stay on the circle-is-horizon path unchanged.
MAX_SQUARE_ASPECT = 1.2


def circle_cropped(width: int, height: int) -> bool:
    """True when the frame is too far from square to hold the whole sky circle."""
    w, h = int(width or 0), int(height or 0)
    if w <= 0 or h <= 0:
        return False
    return max(w, h) / min(w, h) > MAX_SQUARE_ASPECT


def whole_frame_radius(width: int, height: int, cx: float, cy: float) -> float:
    """Radius about (cx, cy) that reaches every corner of the frame."""
    return max(math.hypot(x - cx, y - cy)
               for x in (0.0, float(width)) for y in (0.0, float(height)))
