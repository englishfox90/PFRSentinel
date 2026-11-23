"""Display wording for the imaging-conditions payload (GET /conditions).

Formatting only, shared by the Imaging Conditions card and the status
strip's IMAGING tile so both say the same thing.
"""

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


def reason_text(code: str) -> str:
    return REASON_TEXT.get(code, str(code).replace('_', ' '))


def summarize_conditions(snapshot: dict):
    """``(text, tone)`` for a one-line verdict; tones are the status strip's."""
    if not isinstance(snapshot, dict) or not snapshot.get('enabled', False):
        return "Off", 'muted'
    reasons = list(snapshot.get('reasons') or [])
    if snapshot.get('imaging_ok'):
        cloud = snapshot.get('cloud_cover_pct')
        return ("OK" if cloud is None else f"OK · {cloud:.0f}% cloud"), 'ok'
    if snapshot.get('stale'):
        return "No recent frames", 'muted'
    if reasons and reasons[0] in ('warming_up', 'recovering'):
        return reason_text(reasons[0]), 'info'
    if not reasons:
        return "Not OK", 'warn'
    return f"Paused · {reason_text(reasons[0])}", 'warn'


def detail_lines(snapshot: dict) -> str:
    """The measurements behind the verdict, one short line each."""
    if not isinstance(snapshot, dict):
        return ""
    lines = []
    stars = snapshot.get('stars') or {}
    if stars.get('count') is not None:
        text = f"Stars: {stars['count']}"
        if stars.get('baseline') is not None:
            text += f" of a clear-sky {stars['baseline']:.0f}"
        if stars.get('change_pct') is not None:
            text += f", {stars['change_pct']:+.0f}% vs recent frames"
        lines.append(text)
    learning = baseline_learning_text(snapshot)
    if learning:
        lines.append(learning)
    cloud = snapshot.get('cloud_cover_pct')
    if cloud is not None:
        source = "estimated from the ML sky class" \
            if snapshot.get('cloud_cover_source') == 'ml_class' else "from the star count"
        lines.append(f"Cloud cover: {cloud:.0f}% ({source})")
    fwhm = snapshot.get('fwhm') or {}
    if fwhm.get('px') is not None:
        text = f"FWHM: {fwhm['px']:.1f} px"
        if fwhm.get('arcsec') is not None:
            text += f" ({fwhm['arcsec']:.0f}\")"
        if fwhm.get('night_median_px') is not None:
            text += f", tonight's median {fwhm['night_median_px']:.1f} px"
        if fwhm.get('change_pct') is not None:
            text += f" ({fwhm['change_pct']:+.0f}%)"
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


def baseline_learning_text(snapshot: dict) -> str:
    """"Learning your sky (12/30 clear frames)" while the current Moon bin's
    baseline is not ready; empty once it is."""
    status = (snapshot or {}).get('baseline_status') or {}
    if status.get('state') != 'learning':
        return ""
    return (f"Learning your sky ({status.get('samples', 0)}/{status.get('needed', '?')} "
            "clear frames for this Moon phase)")
