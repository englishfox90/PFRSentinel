"""
The measured celestial pole: the value the two finders share.

pole_finder (Polaris path) and pole_from_rotation (rotating-field path)
both produce a PoleEstimate, and pole_finder.find_pole orchestrates the
two, so the dataclass and the sidereal constants live here rather than in
either finder. pole_finder re-exports them for existing callers.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import numpy as np

from .calibration_validate import SKY_TRIM_FRACTION

# Sidereal rotation rate.
SIDEREAL_DEG_PER_MIN = 360.0 / (23.9345 * 60.0)

# Polaris's angular distance from the NCP (~0.65° epoch-2026; shrinks slowly).
POLARIS_POLAR_DEG = 0.65


@dataclass
class PoleEstimate:
    """Measured celestial-pole pixel position + field-rotation direction."""
    x: float
    y: float
    east_left: Optional[bool]   # None when the sign vote was inconclusive
    sign: int                   # -1 = clockwise in array coords (y down)
    n_frames: int
    span_minutes: float
    drift_px: float             # measured drift of the pole-star track
    flux: float                 # median flux of the pole-star track
    sign_votes: Tuple[int, int]  # (matches for +1, matches for -1)
    # The buffer window the estimate was measured over. Two estimates are
    # independent evidence only when their windows do not overlap
    # (pole_consensus); consecutive refine runs share most of one rolling
    # buffer. None = unknown (an estimate not produced by find_pole).
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    # Resolution of the frames x/y were measured on; 0 = unknown. The
    # history compares and rescales positions across resize changes.
    image_width: int = 0
    image_height: int = 0
    # Radial uncertainty of (x, y) in pixels: the true pole is expected
    # within sigma_px of the estimate and 3·sigma_px is the outer bound the
    # solver and the gate use (pole_from_rotation documents how it is
    # measured). 0.0 = unknown: a Polaris-path estimate, or one from before
    # this field existed — the gate then falls back to its flat tolerance.
    sigma_px: float = 0.0
    # 'polaris' — the brightest coherent near-stationary track;
    # 'rotation' — the axis the whole field rotates about.
    source: str = 'polaris'
    # Plate scale (px/rad, linear term) the rotation fit solved alongside
    # the axis, in the same frame as x/y; the orientation search scans
    # scale around it (issue #93 package 5b) because on an obstructed
    # aperture the sky circle is not a measurement. 0.0 = none (Polaris
    # path, or an estimate from before this field existed).
    a1_px_per_rad: float = 0.0


def predicted_polaris_arc_px(sky_r: float, span_minutes: float) -> float:
    """Pixel arc Polaris sweeps around the pole over the window.

    px/deg is approximated from the untrimmed sky radius spanning ~90° of
    altitude — measured to match the reference rig within a few percent.
    """
    px_per_deg = (float(sky_r) / (1.0 - SKY_TRIM_FRACTION)) / 90.0
    rot_rad = np.radians(SIDEREAL_DEG_PER_MIN * float(span_minutes))
    return POLARIS_POLAR_DEG * px_per_deg * rot_rad
