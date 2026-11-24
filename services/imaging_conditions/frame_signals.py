"""
What one processed frame already says about the sky, read off its metadata.

Nothing here touches pixels. Camera mode carries the ML results as
``_ML_RESULTS``; Directory Watch mode only has the overlay tokens
(``ROOF_STATUS`` = "Open (95%)", ``SKY_CONDITION`` = "Clear (87%)",
``STARS_VISIBLE`` = Yes/No/N/A), so both are read. The star count is the
frame's ``STAR_COUNT`` and the FWHM its ``FWHM`` — the numbers
``star_detection.analyze_stars`` measured at the frame's own resolution
for the image stats, the overlay tokens and the library — so the
conditions views show the same count as everything else and the feed
costs no second image pass.

``STAR_COUNT`` is "N/A" whenever the observable-sky gate
(``observing_window``) kept star detection from running; the count is then
None. Daylight, a closed roof and a static frame carry their own reason
codes. The one case that would otherwise be lost is the gate's own
"roof reads Open but the frame shows no stars" rule, which is exactly an
overcast sky: ``no_star_evidence`` carries it to ``frame_rules``. The
gate's internal 900 px plane count is never read here.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from services.observing_window import REASON_KEY
from services.sky_evidence import ml_star_signals

_TOKEN_RE = re.compile(r'^\s*([A-Za-z][A-Za-z ]*?)\s*(?:\((\d+(?:\.\d+)?)\s*%\))?\s*$')


@dataclass(frozen=True)
class FrameSignals:
    roof_state: str                    # 'Open' | 'Closed' | 'Unknown'
    roof_confidence: Optional[float]
    sky_condition: Optional[str]
    sky_confidence: Optional[float]
    stars_visible: Optional[bool]
    frame_is_static: bool
    star_count: Optional[int]          # the frame's STAR_COUNT; None when withheld
    detected_stars: Optional[int]      # same number: the stars behind the FWHM
    fwhm_px: Optional[float]           # full-frame pixels
    no_star_evidence: bool = False     # the gate withheld the count: no stars in frame


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
    star_count = _int_or_none(metadata.get('STAR_COUNT'))
    return FrameSignals(
        roof_state=roof_state,
        roof_confidence=roof_conf if roof_state != 'Unknown' else None,
        sky_condition=sky,
        sky_confidence=sky_conf,
        stars_visible=stars_visible if isinstance(stars_visible, bool) else None,
        frame_is_static=bool(ml_signals['frame_is_static']),
        star_count=star_count,
        detected_stars=star_count,
        fwhm_px=_positive_float(metadata.get('FWHM')),
        no_star_evidence=star_count is None and metadata.get(REASON_KEY) == 'no_stars',
    )
