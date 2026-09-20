"""
Star suggestions for guided calibration.

Identifying the first few stars by eye is the hard part of a guided
calibration; after that the geometry is already pinned well enough to say
where every other bright star must be. Three anchors fix the six pose
parameters (the centre comes from the sky circle), and on synthetic rigs a
three-anchor provisional solve lands the remaining bright stars within about
one click-snap radius — enough to label them for the user, so the rest of the
session is confirming names rather than star-hopping from memory.

The provisional model never leaves this module: it is not gated, not saved and
not a calibration. Its only consumers are the suggestions below and two early
mis-identification warnings: a wrong name among the anchors usually leaves the
provisional fit unable to reproduce them (the centre is fixed and the lens
terms carry a prior, so even three anchors are not a free fit), and one that
does fit predicts stars that sit on empty sky — the `support` figure.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import numpy as np

from services.logger import app_logger as log

from .calibration_validate import a1_from_sky_radius, tol_scale
from .coords import radec_to_altaz
from .guided_calibration import SolveCancelled, solve_anchor_set

# Fewer than three anchors leave the pose underdetermined.
MIN_HINT_ANCHORS = 3

# A predicted star "has a detection" when one lies within this fraction of
# the sky radius — the dialog's click-snap scale, so a supported hint is one
# the user's click would land on.
_SUPPORT_RADIUS_FRACTION = 0.035
_SUPPORT_MIN_PX = 30.0

# Only the brighter predictions vote on support: a mag 3.5 star is routinely
# lost to haze or the horizon murk, and its absence says nothing about the
# anchors. Low-altitude stars are excluded for the same reason.
_SUPPORT_MAX_MAG = 2.5
_SUPPORT_MIN_ALT = 25.0
_SUPPORT_MIN_VOTERS = 4

# The predicted stars are mag 2.5 or brighter, so they belong among the
# brightest detections. Judging support against every detection lets faint
# field stars vouch for a wrong pose by coincidence.
_SUPPORT_MAX_DETECTIONS = 80

# With fewer detections than this the frame cannot vouch for anything: on a
# hazy frame where detection finds almost nothing, every predicted star is
# "unsupported" and correct anchors would be accused of being wrong — on
# exactly the kind of rig guided calibration exists for.
_SUPPORT_MIN_DETECTIONS = 15

# Below this fraction the provisional pose does not describe the sky in the
# frame. Measured on the reference frame (lum_20260116_021511, 3552 px): every
# correct three-anchor subset scored 0.47-0.53 and six deliberately rotated
# poses scored 0.00-0.18 against the 80 brightest detections. Against all 235
# detections the wrong poses reached 0.29 — hence the cap above. Obstructions
# and cloud hide real stars, so a correct pose never scores near 1.
_SUPPORT_TRUSTED = 0.3

# Suggestions shown. The pick list runs to ~100 stars at mag 3.5; labelling
# them all buries the frame. The brightest are the ones a person recognises.
MAX_HINTS = 30

# The provisional fit must at least reproduce its own anchors.
_PROVISIONAL_RMS_LIMIT_PX = 25.0


@dataclass
class StarHint:
    """Where one not-yet-identified candidate star should be in the frame."""
    name: str
    x: float
    y: float
    vmag: float
    supported: bool      # a detected star sits within the support radius


@dataclass
class HintResult:
    hints: List[StarHint] = field(default_factory=list)
    rms: float = 0.0
    support: Optional[float] = None   # None = too few bright stars to judge
    trusted: bool = False
    message: str = ""


def suggest_stars(
    anchors: Sequence[tuple],
    candidates: Sequence[dict],
    detections: Sequence[tuple],
    lat_deg: float,
    lon_deg: float,
    dt: Optional[datetime],
    sky_cx: float,
    sky_cy: float,
    sky_radius: float,
    should_cancel=None,
) -> Optional[HintResult]:
    """Predict pixel positions for the candidates the user has not named yet.

    anchors:    (pixel_x, pixel_y, ra_deg, dec_deg, name) per identified star.
    candidates: the dialog's pick list — dicts with name/alt/az/vmag.
    detections: detected star centroids, (x, y, ...) tuples, brightest first.

    should_cancel: optional callable polled during the solve; returning True
        abandons it (the result is None).

    Returns None when there are too few anchors or the solve cannot run;
    otherwise a HintResult whose `trusted` flag says whether the hints should
    be shown as guidance or withheld in favour of `message`.
    """
    if len(anchors) < MIN_HINT_ANCHORS or not sky_radius:
        return None
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    px = np.array([(a[0], a[1]) for a in anchors], dtype=float)
    alts = np.empty(len(anchors))
    azs = np.empty(len(anchors))
    for i, a in enumerate(anchors):
        alt, az = radec_to_altaz(float(a[2]), float(a[3]), lat_deg, lon_deg, dt)
        alts[i], azs[i] = float(alt), float(az)
    if np.any(alts <= 0):
        return None

    scale = tol_scale(sky_radius)
    rms_limit = _PROVISIONAL_RMS_LIMIT_PX * scale
    try:
        model, rms = solve_anchor_set(
            alts, azs, px, float(sky_cx), float(sky_cy),
            a1_from_sky_radius(sky_radius), sky_radius, rms_limit,
            should_cancel=should_cancel)
    except SolveCancelled:
        return None
    except Exception as e:
        log.debug(f"Guided hints: provisional solve failed: {e}")
        return None
    if model is None:
        return None

    if rms > rms_limit:
        return HintResult(
            rms=float(rms), trusted=False,
            message=("The stars identified so far don't agree with each "
                     "other — one of them is probably mis-identified."))

    named = {str(a[4]) for a in anchors if len(a) > 4}
    radius = max(_SUPPORT_MIN_PX, _SUPPORT_RADIUS_FRACTION * float(sky_radius))
    det = (np.array([(d[0], d[1])
                     for d in detections[:_SUPPORT_MAX_DETECTIONS]], dtype=float)
           if len(detections) else np.empty((0, 2)))

    hints: List[StarHint] = []
    voters = supported_voters = 0
    for c in candidates:
        if c['name'] in named:
            continue
        xy = model.altaz_to_pixel(float(c['alt']), float(c['az']))
        if xy is None:
            continue
        x, y = float(xy[0]), float(xy[1])
        # Predictions outside the sky circle are horizon clutter, not help.
        if math.hypot(x - sky_cx, y - sky_cy) > float(sky_radius):
            continue
        supported = bool(len(det)) and bool(
            np.min(np.hypot(det[:, 0] - x, det[:, 1] - y)) <= radius)
        hints.append(StarHint(c['name'], x, y, float(c.get('vmag', 0.0)),
                              supported))
        if (float(c.get('vmag', 9.0)) <= _SUPPORT_MAX_MAG
                and float(c['alt']) >= _SUPPORT_MIN_ALT):
            voters += 1
            supported_voters += int(supported)

    judgeable = (voters >= _SUPPORT_MIN_VOTERS
                 and len(det) >= _SUPPORT_MIN_DETECTIONS)
    support = (supported_voters / voters) if judgeable else None
    trusted = support is None or support >= _SUPPORT_TRUSTED
    message = ""
    if not trusted:
        message = (f"Only {supported_voters} of the {voters} bright stars these "
                   "identifications predict line up with a star in the frame "
                   "— one of them is probably mis-identified.")
    hints.sort(key=lambda h: h.vmag)
    hints = hints[:MAX_HINTS]
    return HintResult(hints=hints, rms=float(rms), support=support,
                      trusted=trusted, message=message)
