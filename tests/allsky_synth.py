"""Synthetic all-sky detection buffers for the pole and calibration tests.

Given a FisheyeModel, a site and a list of UTC instants, project the real
bright-star catalogue through the model and return frames in the
CalibrationService buffer format (calibration_service._detect_frame):

    {'dt', 'detected': [(x, y, flux), ...], 'above_horizon', 'sky_cx',
     'sky_cy', 'sky_r', 'image_width', 'image_height'}

Nuisances a hosting-site rig adds are opt-in: centroid jitter, static
lights (a pier LED that jitters but never moves), a slewing light, a hidden
pole region (equipment over Polaris), an obstruction mask and detection
dropout. Everything is seeded, so a test that fails reproduces exactly.

Two rig presets:

  REFERENCE — the maintainer's rig, the known-good multi-image model of
    docs/ALLSKY_POLE_ANCHOR_PLAN.md (a1 ≈ 1277 px/rad at 3552 px, east_left
    True, lat 38.97). Its a3/a5 terms are large and negative: the radial
    function turns over near 78° from the axis, so stars are only generated
    inside lens_polynomial.MONOTONIC_MAX_THETA_DEG. Polaris is visible.
  REPORTER — issue #93's rig (a1 1097, cx 1858, cy 1669, axis_alt 84.16,
    axis_az 48.40, lat 36.5, lon −116.9). The roll and mirror are not in the
    issue; the values here are placeholders, which matters to nothing that
    is measured from the rotating field.
  SOUTHERN — the reference model mirrored to lat −38.97. No southern buffer
    dump exists yet, so this preset is the only evidence for the southern
    path (plan §5 decision 6).

`sky_r` on a preset is the trimmed radius the sky-circle estimator would
report, not the model's horizon radius: on both real rigs the lit disc ends
at equipment, so the seed the solvers get from it is deliberately wrong by
the same factor it is wrong in the field (reference: ~1386 px measured vs
a1·π/2 = 2006; the a1 seed comes out at 0.8× truth).
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.fisheye import FisheyeModel
from services.allsky.lens_polynomial import MONOTONIC_MAX_THETA_DEG

FRAME_PX = 3552
# detect_stars caps the buffer at the 200 brightest detections per frame.
MAX_DETECTIONS = 200
# Sub-pixel centroid jitter of a real star on a stretched frame (plan §4).
DEFAULT_JITTER_PX = 0.4
# A 20 s frame detects to roughly this magnitude on the reference rig; the
# cap at MAX_DETECTIONS is what binds in practice.
DEFAULT_LIMITING_MAG = 6.5
# Detections closer than this to the sky-circle edge are dropped, as
# detect_stars(border_px=20) does.
EDGE_BORDER_PX = 20.0


@dataclass(frozen=True)
class RigPreset:
    name: str
    model: FisheyeModel
    lat: float
    lon: float
    sky_r: float          # trimmed radius the estimator reads on this rig
    sky_cx: float
    sky_cy: float


@dataclass(frozen=True)
class StaticLight:
    """A light that never moves; its centroid jitters like a saturated blob."""
    x: float
    y: float
    sigma_px: float = 1.2
    flux: float = 30000.0


@dataclass(frozen=True)
class SlewingLight:
    """A light on a moving mount: starts at (x, y), moves at a steady rate."""
    x: float
    y: float
    vx_px_per_min: float
    vy_px_per_min: float
    flux: float = 30000.0
    sigma_px: float = 0.6


REFERENCE = RigPreset(
    name='reference',
    model=FisheyeModel(
        cx=1532.277480022239, cy=1748.2333782530266,
        a1=1277.1768448604173, a3=-47.565326396085396, a5=-58.919100634659344,
        roll=1.2213944731235367, axis_alt=84.49275734968984,
        axis_az=280.49549589886345, east_left=True,
        rms_residual=7.8, n_matches=4561, n_images=40, span_minutes=80.0,
        image_width=FRAME_PX, image_height=FRAME_PX,
    ),
    lat=38.9717, lon=-76.9,   # longitude only picks which stars are up
    sky_r=1386.0, sky_cx=1532.0, sky_cy=1748.0,
)

REPORTER = RigPreset(
    name='reporter',
    model=FisheyeModel(
        cx=1858.0, cy=1669.0, a1=1097.0, a3=0.0, a5=0.0,
        roll=-2.0, axis_alt=84.16, axis_az=48.40, east_left=True,
        rms_residual=10.9, n_matches=606, n_images=60, span_minutes=50.0,
        image_width=FRAME_PX, image_height=FRAME_PX,
    ),
    lat=36.5, lon=-116.9,
    sky_r=1390.0, sky_cx=1858.0, sky_cy=1669.0,
)

SOUTHERN = replace(REFERENCE, name='southern', lat=-38.9717, lon=145.0)

PRESETS = (REFERENCE, REPORTER, SOUTHERN)

T0 = datetime(2026, 9, 23, 4, 0, 0, tzinfo=timezone.utc)


def instants(n: int, span_min: float, start: datetime = T0) -> List[datetime]:
    """`n` instants evenly spaced over `span_min` minutes from `start`."""
    if n == 1:
        return [start]
    return [start + timedelta(minutes=span_min * k / (n - 1)) for k in range(n)]


def true_pole(preset: RigPreset) -> Tuple[float, float]:
    """Pixel of the celestial pole this site can see, through the true model."""
    xy = preset.model.altaz_to_pixel(abs(preset.lat), 0.0 if preset.lat >= 0 else 180.0)
    assert xy is not None, f"{preset.name}: pole projects off-model"
    return float(xy[0]), float(xy[1])


def sky_pixels(preset: RigPreset, dt: datetime, max_mag: float = DEFAULT_LIMITING_MAG
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[dict]]:
    """(x, y, flux, star records) of catalogue stars visible in the frame at `dt`.

    Visible = above the horizon, inside the monotonic part of the lens and
    inside the trimmed sky circle. No jitter, no cap — the raw truth.
    """
    stars = get_bright_stars(max_mag=max_mag)
    ra = np.array([s['ra_deg'] for s in stars])
    dec = np.array([s['dec_deg'] for s in stars])
    vmag = np.array([s['vmag'] for s in stars])
    alt, az = radec_to_altaz(ra, dec, preset.lat, preset.lon, dt)
    px, py, visible = preset.model.altaz_array_to_pixels(alt, az)
    theta = _theta_from_axis(preset.model, alt, az)
    r_sky = np.hypot(px - preset.sky_cx, py - preset.sky_cy)
    keep = (visible & (alt > 3.0)
            & (theta <= np.radians(MONOTONIC_MAX_THETA_DEG))
            & (r_sky <= preset.sky_r - EDGE_BORDER_PX))
    flux = 3000.0 * 10.0 ** (-0.4 * (vmag - 2.0))
    idx = np.nonzero(keep)[0]
    return px[idx], py[idx], flux[idx], [stars[i] for i in idx]


def synth_frames(
    preset: RigPreset,
    times: Sequence[datetime],
    seed: int = 0,
    jitter_px: float = DEFAULT_JITTER_PX,
    static_lights: Sequence[StaticLight] = (),
    slewing_light: Optional[SlewingLight] = None,
    hide_pole_px: float = 0.0,
    obstruction: Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]] = None,
    dropout: float = 0.0,
    max_mag: float = DEFAULT_LIMITING_MAG,
    with_catalog: bool = False,
    sky_circle: Optional[Tuple[float, float, float]] = None,
) -> List[dict]:
    """Buffer frames for `times`, brightest MAX_DETECTIONS per frame.

    hide_pole_px: drop every star within this radius of the true pole (the
        pier covering Polaris on the reporter's rig).
    obstruction: (x, y) -> bool array, True where equipment hides the sky.
    dropout: fraction of star detections dropped at random per frame.
    with_catalog: also fill 'above_horizon' as _detect_frame does (slow,
        ~8k catalogue rows per frame; only the joint fit needs it).
    sky_circle: (cx, cy, r) stamped on the frames instead of the preset's,
        to hand a solver a deliberately wrong seed.
    """
    rng = np.random.default_rng(seed)
    pole = true_pole(preset)
    cx, cy, r = sky_circle if sky_circle is not None else (
        preset.sky_cx, preset.sky_cy, preset.sky_r)
    t_first = times[0]
    frames = []
    for dt in times:
        x, y, flux, _stars = sky_pixels(preset, dt, max_mag)
        keep = np.ones(len(x), dtype=bool)
        if hide_pole_px > 0:
            keep &= np.hypot(x - pole[0], y - pole[1]) > hide_pole_px
        if obstruction is not None:
            keep &= ~np.asarray(obstruction(x, y), dtype=bool)
        if dropout > 0:
            keep &= rng.random(len(x)) >= dropout
        x, y, flux = x[keep], y[keep], flux[keep]
        x = x + rng.normal(0.0, jitter_px, len(x))
        y = y + rng.normal(0.0, jitter_px, len(y))
        det = list(zip(x.tolist(), y.tolist(), flux.tolist()))

        for light in static_lights:
            det.append((light.x + rng.normal(0.0, light.sigma_px),
                        light.y + rng.normal(0.0, light.sigma_px), light.flux))
        if slewing_light is not None:
            t_min = (dt - t_first).total_seconds() / 60.0
            det.append((slewing_light.x + slewing_light.vx_px_per_min * t_min
                        + rng.normal(0.0, slewing_light.sigma_px),
                        slewing_light.y + slewing_light.vy_px_per_min * t_min
                        + rng.normal(0.0, slewing_light.sigma_px),
                        slewing_light.flux))
        det.sort(key=lambda d: -d[2])
        det = det[:MAX_DETECTIONS]

        frame = {
            'dt': dt, 'detected': det, 'above_horizon': [],
            'sky_cx': float(cx), 'sky_cy': float(cy), 'sky_r': float(r),
            'image_width': preset.model.image_width or FRAME_PX,
            'image_height': preset.model.image_height or FRAME_PX,
        }
        if with_catalog:
            frame['above_horizon'] = _above_horizon(preset, dt)
        frames.append(frame)
    return frames


def disc_obstruction(cx: float, cy: float, radius: float
                     ) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """An obstruction mask: True inside the disc (a scope or pier head)."""
    def mask(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.hypot(x - cx, y - cy) <= radius
    return mask


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _theta_from_axis(model: FisheyeModel, alt_deg: np.ndarray, az_deg: np.ndarray
                     ) -> np.ndarray:
    """Angle from the optical axis for each (alt, az), radians."""
    alt_r, az_r = np.radians(alt_deg), np.radians(az_deg)
    v = np.column_stack([np.cos(alt_r) * np.sin(az_r),
                         np.cos(alt_r) * np.cos(az_r),
                         np.sin(alt_r)])
    aa, ab = np.radians(model.axis_alt), np.radians(model.axis_az)
    axis = np.array([np.cos(aa) * np.sin(ab), np.cos(aa) * np.cos(ab), np.sin(aa)])
    return np.arccos(np.clip(v @ axis, -1.0, 1.0))


def _above_horizon(preset: RigPreset, dt: datetime) -> List[tuple]:
    catalog = get_bright_stars(max_mag=6.5)
    ra = np.array([s['ra_deg'] for s in catalog])
    dec = np.array([s['dec_deg'] for s in catalog])
    alt, az = radec_to_altaz(ra, dec, preset.lat, preset.lon, dt)
    out = [(s, float(a), float(z)) for s, a, z in zip(catalog, alt, az) if a > 3.0]
    out.sort(key=lambda t: t[0]['vmag'])
    return out
