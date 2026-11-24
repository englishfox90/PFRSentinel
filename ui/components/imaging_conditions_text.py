"""Display wording for the imaging-conditions payload (GET /conditions).

Formatting only, shared by the Imaging Conditions card and the status
strip's IMAGING tile so both say the same thing.
"""
from datetime import datetime

REASON_TEXT = {
    'roof_not_open': "Roof not open",
    'daylight': "Sun too high",
    'frame_static': "Frame is sensor noise",
    'sky_unknown': "No sky reading",
    'cloud_cover_high': "Cloud cover high",
    'stars_low': "Too few stars",
    'stars_dropping': "Stars dropping",
    'fwhm_high': "Stars soft (FWHM)",
    'stale': "No recent frames",
    'warming_up': "Warming up",
    'recovering': "Recovering",
    'disabled': "Off",
}

MOON_BIN_TEXT = {
    'moon_down': "Moon down",
    'moon_up_0_25': "Moon up, 0–25% lit",
    'moon_up_25_50': "Moon up, 25–50% lit",
    'moon_up_50_75': "Moon up, 50–75% lit",
    'moon_up_75_100': "Moon up, 75–100% lit",
    'moon_unknown': "Moon position unknown (no site set)",
}

STAR_INDEX_TOOLTIP = (
    "Star index: star-like points counted on the sky disc after it is resampled "
    "to a fixed 900 px plane, so the figure compares between frames, exposures "
    "and frame sizes, and against this rig's learned clear-sky index.\n"
    "It is not the SEEING tile's star count, which is measured on the full "
    "frame for FWHM and changes with resolution and detection threshold."
)

_HOLDING = ('warming_up', 'recovering')


def reason_text(code: str) -> str:
    return REASON_TEXT.get(code, str(code).replace('_', ' '))


def signed_pct(value) -> str:
    """``+12%`` / ``-45%``; anything that rounds to zero reads ``±0%``
    (never ``-0%``)."""
    rounded = round(float(value))
    return "±0%" if rounded == 0 else f"{rounded:+d}%"


def _never_judged(snapshot: dict) -> bool:
    return not snapshot.get('updated_at') and not snapshot.get('frames_considered')


def summarize_conditions(snapshot: dict, capturing=None):
    """``(text, tone)`` for a one-line verdict; tones are the status strip's.

    ``capturing`` is whether capture is running (None = not known). With
    capture stopped there is nothing to warm up, so a store that never saw
    a frame reads as idle, and a stale one says capture stopped rather than
    implying frames went missing.
    """
    if not isinstance(snapshot, dict) or not snapshot.get('enabled', False):
        return "Off", 'muted'
    reasons = list(snapshot.get('reasons') or [])
    if snapshot.get('imaging_ok'):
        cloud = snapshot.get('cloud_cover_pct')
        return ("OK" if cloud is None else f"OK · {cloud:.0f}% cloud"), 'ok'
    if capturing is False:
        if _never_judged(snapshot):
            return "Idle", 'muted'
        if snapshot.get('stale'):
            return "Paused · Capture stopped", 'muted'
    if snapshot.get('stale'):
        return f"Paused · {reason_text('stale')}", 'warn'
    if reasons and reasons[0] in _HOLDING:
        return reason_text(reasons[0]), 'info'
    if not reasons:
        return "Not OK", 'warn'
    return f"Paused · {reason_text(reasons[0])}", 'warn'


def secondary_text(snapshot: dict, capturing=None, good_needed=None) -> str:
    """The line under the verdict: what the headline does not already say.
    Empty when there is nothing more to add. ``good_needed`` is the
    configured good_frames, when the caller knows it."""
    if not isinstance(snapshot, dict) or not snapshot.get('enabled', False):
        return ""
    if snapshot.get('imaging_ok'):
        return ""
    if snapshot.get('stale'):
        return "Reported to NINA as not OK until frames arrive again"
    reasons = list(snapshot.get('reasons') or [])
    if not reasons:
        return ""
    if reasons[0] == 'warming_up':
        if capturing is False and _never_judged(snapshot):
            return ""
        return f"{_good_so_far(snapshot, good_needed)} before imaging is OK"
    if reasons[0] == 'recovering':
        return f"{_good_so_far(snapshot, good_needed)} to resume"
    rest = [reason_text(r) for r in reasons[1:]]
    return ("Also: " + ", ".join(rest)) if rest else ""


def _good_so_far(snapshot: dict, needed=None) -> str:
    n = int(snapshot.get('good_streak') or 0)
    if needed:
        return f"{n} of {int(needed)} good frames in a row"
    return f"{n} good frame{'' if n == 1 else 's'} in a row so far"


def age_text(age_s) -> str:
    seconds = max(0, int(round(float(age_s))))
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    return f"{seconds / 3600:.1f} h ago"


def local_clock(iso_utc) -> str:
    """``HH:MM`` local time of an ISO-8601 UTC stamp; empty when unreadable."""
    try:
        stamp = datetime.fromisoformat(str(iso_utc).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return ""
    return stamp.astimezone().strftime('%H:%M')


def as_of_text(snapshot: dict) -> str:
    """``Last frame at 02:24 (2 min ago)`` for a stale snapshot, else empty."""
    if not isinstance(snapshot, dict) or not snapshot.get('stale'):
        return ""
    clock = local_clock(snapshot.get('updated_at'))
    age = snapshot.get('age_s')
    when = " ".join(p for p in (
        f"at {clock}" if clock else "",
        f"({age_text(age)})" if age is not None else "") if p)
    return f"From the last frame{' ' + when if when else ''}; not current:"


def detail_lines(snapshot: dict) -> str:
    """The measurements behind the verdict, one short line each."""
    if not isinstance(snapshot, dict) or _never_judged(snapshot):
        return ""
    lines = []
    as_of = as_of_text(snapshot)
    if as_of:
        lines.append(as_of)
    stars = snapshot.get('stars') or {}
    if stars.get('count') is not None:
        text = f"Star index: {stars['count']}"
        if stars.get('baseline') is not None:
            text += f" of a clear-sky {stars['baseline']:.0f}"
        if stars.get('change_pct') is not None:
            text += f", {signed_pct(stars['change_pct'])} vs recent frames"
        lines.append(text)
    learning = baseline_learning_text(snapshot)
    if learning:
        lines.append(learning)
    cloud = snapshot.get('cloud_cover_pct')
    if cloud is not None:
        source = "estimated from the ML sky class" \
            if snapshot.get('cloud_cover_source') == 'ml_class' else "from the star index"
        lines.append(f"Cloud cover: {cloud:.0f}% ({source})")
    fwhm = snapshot.get('fwhm') or {}
    if fwhm.get('px') is not None:
        text = f"FWHM: {fwhm['px']:.1f} px"
        if fwhm.get('arcsec') is not None:
            text += f" ({fwhm['arcsec']:.0f}\")"
        if fwhm.get('night_median_px') is not None:
            text += f", tonight's median {fwhm['night_median_px']:.1f} px"
        if fwhm.get('change_pct') is not None:
            text += f" ({signed_pct(fwhm['change_pct'])})"
        if fwhm.get('reference_px') is not None:
            text += f"; your sharp nights {fwhm['reference_px']:.1f} px"
        lines.append(text)
    roof = snapshot.get('roof') or {}
    sky = snapshot.get('sky') or {}
    parts = [f"Roof: {roof.get('state', 'Unknown')}"]
    if sky.get('condition'):
        parts.append(f"Sky: {sky['condition']}")
    if snapshot.get('sun_altitude_deg') is not None:
        parts.append(f"Sun {snapshot['sun_altitude_deg']:.0f}°")
    lines.append(" · ".join(parts))
    return "\n".join(lines)


def moon_bin_text(bin_name) -> str:
    return MOON_BIN_TEXT.get(bin_name, "") if bin_name else ""


def baseline_learning_text(snapshot: dict) -> str:
    """"Learning your sky: 12/30 clear frames with the Moon down" while the
    current Moon bin's baseline is not ready. Empty once it is, and empty
    whenever the latest frame could not have taught it anything (sun up, or
    no star index), where a learning count is only noise."""
    snapshot = snapshot or {}
    status = snapshot.get('baseline_status') or {}
    if status.get('state') != 'learning':
        return ""
    if 'daylight' in (snapshot.get('reasons') or []):
        return ""
    if (snapshot.get('stars') or {}).get('count') is None:
        return ""
    text = (f"Learning your sky: {status.get('samples', 0)}/{status.get('needed', '?')} "
            "clear frames")
    moon = moon_bin_text(status.get('bin') or snapshot.get('baseline_bin'))
    if moon:
        text += f" ({moon})"
    return text
