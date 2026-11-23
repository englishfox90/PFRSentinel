"""
Sensitivity presets for the imaging-conditions thresholds.

One choice — Relaxed / Balanced / Strict — sets every threshold that decides
how readily imaging pauses. The individual keys in the ``imaging_conditions``
config block stay authoritative (``settings.read_settings`` never looks at
the preset name); ``sensitivity`` only records which preset the user picked,
and ``preset_for`` reads the block back to the preset its numbers match, or
``custom``.

Balanced is today's defaults exactly, so an existing config behaves as
before.

Cloud cover and the star floor are one decision, not two. With a learned
baseline the cloud figure *is* ``(1 - ratio) x 100``, so ``max_cloud_cover_pct``
50 already pauses at ratio 0.5 and ``min_star_ratio`` 0.4 can never be the
first to fire. Every preset therefore sets the star floor 10 points of
clear sky below the cloud line (``star_floor_for``): cloud is the pause line,
and "too few stars" is a second, deeper reason that only shows when the sky
is clearly worse than that. The cloud figure still matters on its own before
the baseline is learned, when it comes from the ML sky class (Clear 5 %,
Partly Cloudy 60 %, Overcast 95 %): Relaxed (70) keeps imaging through
"Partly Cloudy", Balanced and Strict pause for it.
"""
from __future__ import annotations

import math

from services.config_defaults import DEFAULT_CONFIG

SENSITIVITY_KEY = 'sensitivity'
RELAXED = 'relaxed'
BALANCED = 'balanced'
STRICT = 'strict'
CUSTOM = 'custom'

PRESET_ORDER = (RELAXED, BALANCED, STRICT)

# The keys a preset sets. Everything else in the block (sun limit, roof,
# trend window, staleness) is a separate choice.
PRESET_KEYS = ('bad_frames', 'good_frames', 'max_cloud_cover_pct', 'min_star_ratio',
               'star_drop_pct', 'fwhm_rise_pct')

# Clear-sky points between the cloud line and the star floor.
STAR_FLOOR_GAP = 0.10
STAR_FLOOR_MIN = 0.05

PRESETS = {
    # Slow to pause, quick to resume: rides out passing cloud.
    RELAXED: {'bad_frames': 5, 'good_frames': 2, 'max_cloud_cover_pct': 70.0,
              'min_star_ratio': 0.2, 'star_drop_pct': 60.0, 'fwhm_rise_pct': 80.0},
    BALANCED: {key: DEFAULT_CONFIG['imaging_conditions'][key] for key in PRESET_KEYS},
    # Quick to pause, slow to resume: protects every sub-exposure.
    STRICT: {'bad_frames': 2, 'good_frames': 5, 'max_cloud_cover_pct': 30.0,
             'min_star_ratio': 0.6, 'star_drop_pct': 25.0, 'fwhm_rise_pct': 30.0},
}

PRESET_LABELS = {RELAXED: "Relaxed", BALANCED: "Balanced", STRICT: "Strict", CUSTOM: "Custom"}


def star_floor_for(max_cloud_cover_pct) -> float:
    """``min_star_ratio`` that goes with a cloud limit: 10 points of clear
    sky below the cloud line, never under 0.05."""
    try:
        cloud = float(max_cloud_cover_pct)
    except (TypeError, ValueError):
        cloud = float(DEFAULT_CONFIG['imaging_conditions']['max_cloud_cover_pct'])
    if not math.isfinite(cloud):
        cloud = float(DEFAULT_CONFIG['imaging_conditions']['max_cloud_cover_pct'])
    cloud = max(0.0, min(100.0, cloud))
    return round(max(STAR_FLOOR_MIN, 1.0 - cloud / 100.0 - STAR_FLOOR_GAP), 2)


def preset_values(name: str) -> dict:
    """The threshold keys for a preset (a copy). Unknown names read Balanced."""
    return dict(PRESETS.get(name, PRESETS[BALANCED]))


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def preset_for(block) -> str:
    """The preset whose numbers ``block`` holds, else ``custom``. A key the
    block lacks reads its default, as ``read_settings`` would."""
    block = block if isinstance(block, dict) else {}
    defaults = DEFAULT_CONFIG['imaging_conditions']
    for name in PRESET_ORDER:
        if all(_same(block.get(key, defaults[key]), value)
               for key, value in PRESETS[name].items()):
            return name
    return CUSTOM
