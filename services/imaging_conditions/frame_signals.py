"""
What one processed frame already says about the sky, read off its metadata.

Nothing here touches pixels. Camera mode carries the ML results as
``_ML_RESULTS``; Directory Watch mode only has the overlay tokens
(``ROOF_STATUS`` = "Open (95%)", ``SKY_CONDITION`` = "Clear (87%)",
``STARS_VISIBLE`` = Yes/No/N/A), so both are read. The star count is
``sky_evidence``'s fixed 900 px plane count; the FWHM is the one
``star_detection.analyze_stars`` measured on the full frame for the
``{FWHM}`` token, so the conditions feed costs no second image pass.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from services.sky_evidence import EVIDENCE_KEY, ml_star_signals

_TOKEN_RE = re.compile(r'^\s*([A-Za-z][A-Za-z ]*?)\s*(?:\((\d+(?:\.\d+)?)\s*%\))?\s*$')


@dataclass(frozen=True)
class FrameSignals:
    roof_state: str                    # 'Open' | 'Closed' | 'Unknown'
    roof_confidence: Optional[float]
    sky_condition: Optional[str]
    sky_confidence: Optional[float]
    stars_visible: Optional[bool]
    frame_is_static: bool
    star_count: Optional[int]          # sky_evidence plane count
    detected_stars: Optional[int]      # analyze_stars detections behind the FWHM
    fwhm_px: Optional[float]           # full-frame pixels


def _confidence(value) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None


def _parse_token(text):
    """``"Open (95%)"`` -> ``('Open', 0.95)``; ``"N/A"`` -> ``(None, None)``."""
    if not isinstance(text, str):
        return None, None
    m = _TOKEN_RE.match(text)
    if not m:
        return None, None
    pct = m.group(2)
    return m.group(1), (_confidence(float(pct) / 100.0) if pct else None)


def _roof_state(status) -> str:
    if isinstance(status, str):
        if status.startswith('Open'):
            return 'Open'
        if status.startswith('Closed'):
            return 'Closed'
    return 'Unknown'


def _int_or_none(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _positive_float(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def read_frame_signals(metadata: dict) -> FrameSignals:
    ml = metadata.get('_ML_RESULTS')
    if isinstance(ml, dict):
        roof_state = _roof_state(ml.get('roof_status'))
        roof_conf = _confidence(ml.get('roof_confidence'))
        sky = ml.get('sky_condition')
        sky = sky if isinstance(sky, str) and sky not in ('', 'N/A') else None
        sky_conf = _confidence(ml.get('sky_confidence')) if sky else None
    else:
        roof_name, roof_conf = _parse_token(metadata.get('ROOF_STATUS'))
        roof_state = _roof_state(roof_name)
        sky, sky_conf = _parse_token(metadata.get('SKY_CONDITION'))
    ml_signals = ml_star_signals(metadata)
    stars_visible = ml_signals['stars_visible']
    evidence = metadata.get(EVIDENCE_KEY)
    star_count = _int_or_none(evidence.get('star_count')) if isinstance(evidence, dict) else None
    return FrameSignals(
        roof_state=roof_state,
        roof_confidence=roof_conf if roof_state != 'Unknown' else None,
        sky_condition=sky,
        sky_confidence=sky_conf,
        stars_visible=stars_visible if isinstance(stars_visible, bool) else None,
        frame_is_static=bool(ml_signals['frame_is_static']),
        star_count=star_count,
        detected_stars=_int_or_none(metadata.get('STAR_COUNT')),
        fwhm_px=_positive_float(metadata.get('FWHM')),
    )
