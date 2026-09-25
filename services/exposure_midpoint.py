"""
Mid-exposure timestamp of a captured frame.

The calibration buffer used to be stamped with the time the processor got
round to the frame (reliability plan F7, deferred since June): for a 30 s
exposure that is the exposure end plus readout and queueing, 20–30 s after
the instant the star positions actually correspond to, i.e. ~0.12° of
rotation ≈ 2 px at 1000 px from the pole — a systematic error the joint
fit and the rotation-pole fit both pay for on every frame. Mid-exposure is
the instant a trailed star's centroid measures.

Sources, in order of trust:
  1. 'EXPOSURE_START_UTC' (ISO 8601) — the capture worker records the SDK
     exposure start; midpoint = start + exposure/2.
  2. 'DATE-OBS' (ISO 8601, FITS convention: exposure start, UTC) — a watch
     -mode frame whose header reached the metadata.
  3. Otherwise `received_at` minus half the exposure: the frame reached the
     processor after the exposure ended, so this is closer than the receipt
     time itself and never later than it.
An unparsable exposure yields `received_at` unchanged. Never raises.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

_NUMBER = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def exposure_seconds(metadata: Optional[dict]) -> Optional[float]:
    """Exposure length in seconds from 'EXPOSURE' ("20.66s", "20.66",
    "500ms") or the FITS 'EXPTIME'; None when neither parses."""
    if not metadata:
        return None
    for key in ('EXPOSURE', 'EXPTIME', 'Exposure'):
        raw = metadata.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return float(raw) if raw >= 0 else None
        text = str(raw).strip()
        m = _NUMBER.search(text)
        if not m:
            continue
        value = float(m.group(0))
        unit = text[m.end():].strip().lower()
        if unit.startswith('ms'):
            value /= 1000.0
        elif unit.startswith('us') or unit.startswith('µs'):
            value /= 1e6
        return value if value >= 0 else None
    return None


def exposure_midpoint(metadata: Optional[dict], received_at: datetime) -> datetime:
    """UTC instant the frame's star positions correspond to (module doc)."""
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    exp_s = exposure_seconds(metadata)
    half = timedelta(seconds=exp_s / 2.0) if exp_s else timedelta(0)
    start = _parse_start(metadata)
    if start is not None:
        return start + half
    return received_at - half


def _parse_start(metadata: Optional[dict]) -> Optional[datetime]:
    if not metadata:
        return None
    for key in ('EXPOSURE_START_UTC', 'DATE-OBS'):
        raw = metadata.get(key)
        if not raw or not isinstance(raw, str):
            continue
        try:
            dt = datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None
