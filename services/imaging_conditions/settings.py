"""
The ``imaging_conditions`` config block, read once per frame into typed,
bounded values.

A hand-edited config.json can hold anything; every value is clamped to a
range the rules can work with, and a value that cannot be read falls back to
``DEFAULT_CONFIG``, so a bad edit degrades a threshold rather than the feed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from services.config_defaults import DEFAULT_CONFIG

CONFIG_KEY = 'imaging_conditions'
_DEFAULTS = DEFAULT_CONFIG[CONFIG_KEY]

# The automatic staleness: three frame intervals, never under two minutes
# (a 30 s cadence with one slow download must not flicker stale) and never
# over an hour (a slow daytime cadence must still go stale overnight).
AUTO_STALE_FACTOR = 3.0
AUTO_STALE_MIN_S = 120.0
STALE_MAX_S = 3600.0
STALE_MIN_S = 30.0


@dataclass(frozen=True)
class ConditionsSettings:
    enabled: bool
    bad_frames: int
    good_frames: int
    max_sun_alt_deg: float
    require_roof_open: bool
    min_roof_confidence: float
    min_sky_confidence: float
    max_cloud_cover_pct: float
    min_star_ratio: float
    star_drop_pct: float
    trend_frames: int
    fwhm_rise_pct: float
    min_stars_for_fwhm: int
    baseline_min_samples: int
    stale_after_s: float   # 0 = automatic


def _number(cfg, key, lo, hi):
    value = cfg.get(key, _DEFAULTS[key])
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        value = float(_DEFAULTS[key])
    if not math.isfinite(value) or isinstance(cfg.get(key), bool):
        value = float(_DEFAULTS[key])
    return max(lo, min(hi, value))


def _flag(cfg, key):
    value = cfg.get(key, _DEFAULTS[key])
    return value if isinstance(value, bool) else bool(_DEFAULTS[key])


def read_settings(config) -> ConditionsSettings:
    """``config`` is a plain dict (camera mode) or a ``Config`` (Watch mode)."""
    try:
        cfg = config.get(CONFIG_KEY, {}) if config is not None else {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    stale = _number(cfg, 'stale_after_s', 0.0, STALE_MAX_S)
    if 0 < stale < STALE_MIN_S:
        stale = STALE_MIN_S
    return ConditionsSettings(
        enabled=_flag(cfg, 'enabled'),
        bad_frames=int(_number(cfg, 'bad_frames', 1, 20)),
        good_frames=int(_number(cfg, 'good_frames', 1, 20)),
        max_sun_alt_deg=_number(cfg, 'max_sun_alt_deg', -24.0, 0.0),
        require_roof_open=_flag(cfg, 'require_roof_open'),
        min_roof_confidence=_number(cfg, 'min_roof_confidence', 0.0, 1.0),
        min_sky_confidence=_number(cfg, 'min_sky_confidence', 0.0, 1.0),
        max_cloud_cover_pct=_number(cfg, 'max_cloud_cover_pct', 0.0, 100.0),
        min_star_ratio=_number(cfg, 'min_star_ratio', 0.0, 2.0),
        star_drop_pct=_number(cfg, 'star_drop_pct', 5.0, 100.0),
        trend_frames=int(_number(cfg, 'trend_frames', 3, 60)),
        fwhm_rise_pct=_number(cfg, 'fwhm_rise_pct', 5.0, 400.0),
        min_stars_for_fwhm=int(_number(cfg, 'min_stars_for_fwhm', 1, 1000)),
        baseline_min_samples=int(_number(cfg, 'baseline_min_samples', 3, 500)),
        stale_after_s=stale,
    )


def auto_stale_after_s(frame_interval_s) -> float:
    """Staleness for a measured median frame interval (None = not measured)."""
    if frame_interval_s is None or not math.isfinite(frame_interval_s) or frame_interval_s <= 0:
        return AUTO_STALE_MIN_S
    return max(AUTO_STALE_MIN_S, min(STALE_MAX_S, AUTO_STALE_FACTOR * frame_interval_s))
