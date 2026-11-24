"""How close the telescope's target is to the Moon.

The NINA plugin reports the sequencer's running target to ``/nina/target``
(``services.nina_target_store``). For each judged frame the feed adds a
``target`` block to the conditions record: the target's name, its angular
distance from the Moon and its altitude.

This is reported, never judged. ``imaging_ok`` stays a statement about the
sky; a target near the Moon is the operator's choice — narrowband imagers
do it on purpose — and pausing on it would stall a whole night. The views
show a gentle caution inside ``caution_separation_deg``, and the NINA
plugin has its own optional "min Moon separation" limit.

The target counts as fresh for as long as the all-sky overlay would draw it
(``render_target.stale_after_seconds``), and both positions are compared in
the equinox of date, like the overlay's (``services.moon_position``).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from services.logger import app_logger

# Starting guesses, not measurements. The usual rule of thumb is to keep
# further from the Moon the brighter it is: this line runs from 20° at new
# to 60° at full (about 55° at 87 % lit). It only colours a line of text.
CAUTION_SEPARATION_NEW_DEG = 20.0
CAUTION_SEPARATION_FULL_DEG = 60.0

_MAX_NAME = 64


def caution_separation_deg(illumination_pct) -> Optional[float]:
    """The separation under which a target reads as close to the Moon, for
    this illumination; None when the illumination is unknown."""
    try:
        illum = float(illumination_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(illum):
        return None
    fraction = min(1.0, max(0.0, illum / 100.0))
    return CAUTION_SEPARATION_NEW_DEG + fraction * (
        CAUTION_SEPARATION_FULL_DEG - CAUTION_SEPARATION_NEW_DEG)


def is_close_to_moon(separation_deg, illumination_pct, moon_up) -> bool:
    """Whether to caution: only with the Moon known to be up."""
    if moon_up is not True or not isinstance(separation_deg, (int, float)):
        return False
    limit = caution_separation_deg(illumination_pct)
    return limit is not None and separation_deg < limit


def fresh_target(config) -> Optional[dict]:
    """The target NINA last reported, while the overlay would still draw
    it; None when there is none, it went stale, or anything fails."""
    try:
        from services.allsky.render_target import stale_after_seconds
        from services.nina_target_store import get_nina_target_store
        allsky_cfg = config.get('allsky_overlay', {}) or {}
        target = get_nina_target_store().current(
            stale_after_seconds(allsky_cfg.get('nina_target')))
        return target.as_dict() if target is not None else None
    except Exception as e:
        app_logger.debug(f"Imaging conditions: NINA target unavailable: {e}")
        return None


def site_from_config(config) -> tuple:
    """``(lat, lon)`` from the weather settings, or ``(None, None)``."""
    try:
        from services.coordinates import parse_coordinate
        weather = config.get('weather', {}) or {}
        lat = parse_coordinate(weather.get('latitude', ''), is_longitude=False)
        lon = parse_coordinate(weather.get('longitude', ''), is_longitude=True)
    except Exception:
        return None, None
    return (lat, lon) if lat is not None and lon is not None else (None, None)


def target_block(target, now: datetime, lat=None, lon=None) -> Optional[dict]:
    """The record's ``target`` block for a J2000 target dict, or None.

    Without a site the separation is still given (against the geocentric
    Moon, within the Moon's one degree of parallax) and the altitude is
    None. Never raises.
    """
    if not isinstance(target, dict):
        return None
    try:
        from services import moon_position as mp
        ra, dec = mp.j2000_of_date(target['ra_deg'], target['dec_deg'], now)
        moon = mp.moon_position(lat, lon, now)
        separation = mp.angular_separation_deg(ra, dec, moon['ra_deg'], moon['dec_deg'])
        altitude = None
        if lat is not None and lon is not None:
            altitude = mp.altitude_deg(ra, dec, lat, lon, now)
        if not math.isfinite(separation):
            return None
        return {
            'name': str(target.get('name') or '')[:_MAX_NAME],
            'moon_separation_deg': round(separation, 1),
            'altitude_deg': None if altitude is None or not math.isfinite(altitude)
            else round(altitude, 1),
        }
    except Exception as e:
        app_logger.debug(f"Imaging conditions: target / Moon separation skipped: {e}")
        return None
