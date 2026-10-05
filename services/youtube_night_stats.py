"""
Night statistics for the YouTube upload description (issue #145, Part 1).

Finds the image-library night a finished timelapse covers and turns its
summary into the night placeholders listed in
``youtube_config.NIGHT_PLACEHOLDERS``. Nothing here may fail an upload: a
missing library, a night with no frames or any error yields empty strings.

The span comes from the upload metadata. An automatic upload knows when the
session ended (``queued_at``) and how long it ran (``elapsed_seconds``), so only
library frames inside the recording window count. **Upload latest video** knows
neither, so the file's modification time stands in for the end and the night
is taken from local noon up to it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from .library.sessions import (
    NIGHT_CUTOFF_HOUR,
    STATUS_UNKNOWN,
    night_key,
    roof_state,
    row_status,
    summarize_sessions,
)
from .logger import app_logger
from .youtube_config import NIGHT_PLACEHOLDERS, TimelapseUploadMetadata

_UNIT_SYMBOLS = {"metric": "°C", "imperial": "°F", "standard": "K"}


def empty_night_context() -> dict[str, str]:
    return {name: "" for name in NIGHT_PLACEHOLDERS}


def video_time_span(metadata: TimelapseUploadMetadata) -> tuple[int | None, int | None]:
    """(start, end) epochs of the recording, either of which may be None."""
    if int(metadata.elapsed_seconds or 0) > 0:
        end = int(metadata.queued_at.timestamp())
        return end - int(metadata.elapsed_seconds), end
    try:
        return None, int(os.path.getmtime(metadata.path))
    except OSError:
        return None, None


def _night_start_epoch(key: str) -> int:
    # Naive local time on purpose: night_key splits nights at local noon.
    day = datetime.strptime(key, "%Y-%m-%d")
    return int((day + timedelta(hours=NIGHT_CUTOFF_HOUR)).timestamp())


class _WindowedIndex:
    """Index view capped at the video's end that keeps the rows it served."""

    def __init__(self, index, until: int):
        self._index = index
        self._until = until
        self.rows: list[dict] = []

    def brief_rows(self, since=None):
        self.rows = self._index.brief_rows(since=since, until=self._until)
        return self.rows


def find_night_session(metadata: TimelapseUploadMetadata, index) -> tuple[dict | None, list[dict]]:
    """The library session the video covers and its rows, or (None, [])."""
    start, end = video_time_span(metadata)
    if end is None:
        return None, []
    if start is None:
        start = _night_start_epoch(night_key(end))
    view = _WindowedIndex(index, until=end)
    sessions = summarize_sessions(view, since=start)
    if not sessions:
        return None, []
    # A window that crosses local noon spans two library nights; the video
    # belongs to the one it spent most of its frames in.
    session = max(sessions, key=lambda s: s["frame_count"])
    rows = [r for r in view.rows if night_key(r["captured_at"]) == session["key"]]
    return session, rows


def format_temp(celsius: float | None, units: str) -> str:
    """Sensor temperature (stored in °C) in the saved weather units."""
    if celsius is None:
        return ""
    units = units if units in _UNIT_SYMBOLS else "metric"
    if units == "imperial":
        value = celsius * 9 / 5 + 32
    elif units == "standard":
        value = celsius + 273.15
    else:
        value = celsius
    return f"{value:.1f}{_UNIT_SYMBOLS[units]}"


def _clock(epoch) -> str:
    # Local wall-clock time, like night_key; the viewer reads the site's clock.
    return datetime.fromtimestamp(int(epoch)).strftime("%H:%M")


def _duration(seconds: int) -> str:
    minutes = int(seconds) // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _clear_pct(session: dict, rows: list[dict]) -> str:
    # summarize_sessions reports 0% for a night with no roof, sky or cloud
    # signal at all; that is "unknown", not "overcast".
    if session.get("clear_pct") is None:
        return ""
    if rows and all(row_status(r) == STATUS_UNKNOWN for r in rows):
        return ""
    return str(session["clear_pct"])


def _roof(rows: list[dict]) -> str:
    states = [roof_state(r.get("roof")) for r in rows]
    n_open = states.count("open")
    n_closed = states.count("closed")
    if not n_open and not n_closed:
        return ""
    if not n_closed:
        return "Open"
    if not n_open:
        return "Closed"
    return f"Open {round(100 * n_open / (n_open + n_closed))}% of the night"


def _gaps(session: dict) -> str:
    gaps = session.get("gaps") or []
    if not gaps:
        return "none"
    return f"{len(gaps)} (longest {_duration(session.get('max_gap_seconds') or 0)})"


def _best_seeing(session: dict) -> str:
    label = session.get("best_seeing")
    fwhm = session.get("best_fwhm")
    fwhm_text = f"FWHM {fwhm:.1f} px" if fwhm is not None else ""
    if label and fwhm_text:
        return f"{label} ({fwhm_text})"
    return label or fwhm_text


def _weather_summary(rows: list[dict]) -> str:
    clouds = []
    for r in rows:
        try:
            if r.get("clouds") is not None:
                clouds.append(int(r["clouds"]))
        except (TypeError, ValueError):
            continue
    if not clouds:
        return ""
    low, high = min(clouds), max(clouds)
    average = round(sum(clouds) / len(clouds))
    if low == high:
        return f"Cloud cover {average}%"
    return f"Cloud cover {low} to {high}% (average {average}%)"


def _night_summary(ctx: dict[str, str], gap_count: int) -> str:
    head = f"Night of {ctx['night']}"
    if ctx["start_time"] and ctx["end_time"]:
        head += f", {ctx['start_time']} to {ctx['end_time']}"
    parts = [head]
    if ctx["min_temp"] and ctx["max_temp"]:
        if ctx["min_temp"] == ctx["max_temp"]:
            parts.append(f"sensor {ctx['min_temp']}")
        else:
            parts.append(f"sensor {ctx['min_temp']} to {ctx['max_temp']}")
    if ctx["clear_pct"]:
        parts.append(f"{ctx['clear_pct']}% clear")
    if ctx["roof"]:
        parts.append(f"roof {ctx['roof'][0].lower()}{ctx['roof'][1:]}")
    if ctx["max_stars"]:
        parts.append(f"up to {ctx['max_stars']} stars")
    if ctx["best_seeing"]:
        parts.append(f"best seeing {ctx['best_seeing']}")
    if gap_count:
        noun = "gap" if gap_count == 1 else "gaps"
        parts.append(f"{gap_count} capture {noun}")
    if ctx["weather_summary"]:
        parts.append(ctx["weather_summary"][0].lower() + ctx["weather_summary"][1:])
    return " · ".join(parts)


def format_night_context(session: dict, rows: list[dict], units: str = "metric") -> dict[str, str]:
    """Placeholder strings for one ``summarize_sessions`` night."""
    ctx = empty_night_context()
    ctx["night"] = str(session.get("key") or "")
    if session.get("start_epoch") is not None:
        ctx["start_time"] = _clock(session["start_epoch"])
    if session.get("end_epoch") is not None:
        ctx["end_time"] = _clock(session["end_epoch"])
    ctx["min_temp"] = format_temp(session.get("min_temp_c"), units)
    ctx["max_temp"] = format_temp(session.get("max_temp_c"), units)
    ctx["clear_pct"] = _clear_pct(session, rows)
    if session.get("max_stars") is not None:
        ctx["max_stars"] = str(session["max_stars"])
    ctx["best_seeing"] = _best_seeing(session)
    ctx["roof"] = _roof(rows)
    ctx["gaps"] = _gaps(session)
    ctx["weather_summary"] = _weather_summary(rows)
    ctx["night_summary"] = _night_summary(ctx, len(session.get("gaps") or []))
    return ctx


def build_night_context(
    metadata: TimelapseUploadMetadata,
    index: Any,
    *,
    units: str = "metric",
) -> dict[str, str]:
    """Night placeholders for an upload; all empty when nothing can be found."""
    if index is None:
        app_logger.debug("YouTube: no image library, night placeholders left empty")
        return empty_night_context()
    try:
        session, rows = find_night_session(metadata, index)
        if session is None:
            app_logger.info(
                f"YouTube: no library frames for {metadata.filename}, night placeholders left empty"
            )
            return empty_night_context()
        context = format_night_context(session, rows, units)
        app_logger.debug(
            f"YouTube: night {context['night']} matched for {metadata.filename} "
            f"({session.get('frame_count', 0)} library frames)"
        )
        return context
    except Exception as exc:
        app_logger.info(f"YouTube: night statistics unavailable ({type(exc).__name__}: {exc})")
        return empty_night_context()
