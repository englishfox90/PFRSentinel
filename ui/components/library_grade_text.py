"""Wording for the imaging-conditions grade stored with a library frame.

Formatting only. The reason and Moon wording is the live views' own
(``imaging_conditions_text``), so a frame read back from the library says
what the IMAGING tile said when it was taken.
"""
from services.imaging_conditions.frame_grade import is_graded, split_reasons

from .imaging_conditions_text import moon_bin_text, reason_text
from .target_moon_text import stored_separation_text

_HOLDING = ('warming_up', 'recovering')
NOT_GRADED = "—"
# Rows stored before the first good frame was enough at a start can carry
# warming_up on a judged frame; they keep the wording they were saved under.
_STORED_TEXT = {'warming_up': "Warming up"}


def _reason(code) -> str:
    return _STORED_TEXT.get(code) or reason_text(code)


def frame_grade_headline(row):
    """``(text, tone)`` for one library row: 'OK', 'Not OK · Cloud cover
    high', or a dash for a frame that was never graded. Tones: 'ok',
    'warn', 'info' (waiting for good frames), 'muted'."""
    if not is_graded(row):
        return NOT_GRADED, 'muted'
    if row.get('imaging_ok'):
        return "OK", 'ok'
    reasons = split_reasons(row.get('imaging_reasons'))
    if not reasons:
        return "Not OK", 'warn'
    tone = 'info' if reasons[0] in _HOLDING else 'warn'
    return f"Not OK · {_reason(reasons[0])}", tone


def frame_grade_details(row) -> list:
    """The lines under the headline; empty for an ungraded frame."""
    if not is_graded(row):
        return []
    lines = []
    reasons = split_reasons(row.get('imaging_reasons'))
    ok, frame_ok = bool(row.get('imaging_ok')), row.get('frame_ok')
    if ok:
        if frame_ok is not None and not frame_ok:
            # The verdict holds through a short run of bad frames.
            why = ", ".join(_reason(r) for r in reasons)
            lines.append("This frame alone was not OK" + (f": {why}" if why else ""))
    else:
        rest = [_reason(r) for r in reasons[1:]]
        if rest:
            lines.append("Also: " + ", ".join(rest))
        if frame_ok:
            lines.append("This frame alone was OK")
    ratio = clear_sky_text(row.get('clear_sky_ratio'))
    moon = moon_bin_text(row.get('moon_bin'))
    tail = " · ".join(p for p in (ratio, moon) if p)
    if tail:
        lines.append(tail)
    target = stored_separation_text(row.get('moon_separation_deg'), row.get('moon_bin'))
    if target:
        lines.append(target)
    return lines


def clear_sky_text(ratio) -> str:
    """'Stars at 62% of clear sky', or empty while the baseline was learning."""
    if ratio is None:
        return ""
    try:
        return f"Stars at {round(float(ratio) * 100)}% of clear sky"
    except (TypeError, ValueError):
        return ""


def night_grade_text(summary, short=False) -> str:
    """'Imaging OK on 82% of 140 graded frames · mostly Cloud cover high';
    empty for a night with no graded frames. ``short`` drops the count."""
    summary = summary or {}
    graded = summary.get('graded_count') or 0
    pct = summary.get('ok_pct')
    if not graded or pct is None:
        return ""
    if short:
        text = f"imaging OK {pct}%"
    else:
        text = (f"Imaging OK on {pct}% of {graded:,} graded "
                f"frame{'' if graded == 1 else 's'}")
    top = summary.get('top_reason')
    if top:
        text += f" · mostly {_reason(top)}"
    return text
