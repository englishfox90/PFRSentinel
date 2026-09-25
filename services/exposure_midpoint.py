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
     exposure start; midpoint = start + exposure/2. This is the only source
     that reaches the calibration service today: camera mode is the only
     path that feeds it (ui/controllers/image_processor), and the capture
     worker stamps every frame.
  2. 'DATE-OBS' (ISO 8601, FITS convention: exposure start, UTC). Handled
     for a caller that has a FITS header in its metadata; nothing in
     services/watcher.py puts one there today, and Directory Watch mode
     does not feed the calibration service at all, so this source is
     currently unreachable in production.
  3. Otherwise `received_at` minus half the exposure: the frame reached the
     processor after the exposure ended, so this is closer than the receipt
     time itself and never later than it. The plan's watch-mode fallback
     (file mtime minus half the sidecar exposure) needs the watcher to
     carry the path or mtime into the metadata — a follow-up in the
     watcher, not done here.
An unparsable exposure yields `received_at` unchanged. Never raises.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from .sky_evidence import parse_exposure_seconds


def exposure_seconds(metadata: Optional[dict]) -> Optional[float]:
    """Exposure length in seconds from 'EXPOSURE' (the capture worker and
    sidecars) or the FITS 'EXPTIME'; None when neither is present or
    parses. The value itself is read by sky_evidence.parse_exposure_seconds,
    the one exposure parser (formats and units are its tests' business)."""
    if not metadata:
        return None
    for key in ('EXPOSURE', 'EXPTIME', 'Exposure'):
        if metadata.get(key) is not None:
            value = parse_exposure_seconds(metadata[key])
            if value is not None:
                return value
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
