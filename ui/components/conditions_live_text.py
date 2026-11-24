"""Plain-words wording for the Live Monitoring conditions panel: the one
sentence verdict, the event list, the learning line and "NINA last checked".

Formatting only — every input is a ``GET /conditions`` snapshot, an event
from ``services.imaging_conditions.conditions_history`` or a check-in from
``nina_checkin``. Shares the reason wording with the status tile
(``imaging_conditions_text``) but says it as a person would.
"""
from datetime import datetime

from .imaging_conditions_text import age_text, local_clock, moon_bin_text

# What a pause reason means, in the words of "Imaging paused at 23:14 — …".
CAUSE_TEXT = {
    'cloud_cover_high': "cloud moving in",
    'stars_low': "too few stars showing",
    'stars_dropping': "stars fading fast",
    'fwhm_high': "stars getting soft",
    'roof_not_open': "the roof is not open",
    'daylight': "the sun is too high",
    'frame_static': "frames show only sensor noise",
    'sky_unknown': "no sky reading",
    'stale': "no new frames",
    'restarted': "Sentinel restarted",
    'warming_up': "no frame judged yet",
    'recovering': "waiting for steady clear frames",
}

NINA_STALE_S = 15 * 60

STAR_COUNT_HELP = (
    "Stars: how many stars Sentinel counts on the sky in each frame — the same "
    "count as the SEEING tile, the {STAR_COUNT} overlay token and the image "
    "library. More stars means a clearer sky.")
BASELINE_HELP = (
    "Clear-sky baseline: the star count this camera sees on a clear night, "
    "learned from its own clear nights (separately for each Moon phase, and for a bright Moon low or high in the sky). It "
    "is taken from the better of those nights, so one hazy night barely moves it.")
FWHM_HELP = (
    "FWHM: how wide a star looks, in pixels. Rising FWHM means softer, "
    "blurrier stars — haze, dew or poor seeing.")


def cause_text(code) -> str:
    return CAUSE_TEXT.get(code, str(code).replace('_', ' '))


def _clock(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone().strftime('%H:%M')
    return local_clock(value)


def _good_needed_text(snapshot: dict, good_needed) -> str:
    have = int(snapshot.get('good_streak') or 0)
    if good_needed:
        return f"{have} of {int(good_needed)} good frames"
    return f"{have} good frame{'' if have == 1 else 's'}"


def _last_pause(events):
    for event in events or ():
        if event.get('kind') == 'paused':
            return event
    return None


def verdict_sentence(snapshot: dict, capturing=None, good_needed=None, events=None):
    """``(sentence, tone)``. ``events`` newest first, as the history gives them."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if not snapshot.get('enabled', False):
        return ("Off — Sentinel is not judging the sky, so NINA gets no verdict. "
                "Turn Imaging Conditions on in NINA & ASCOM."), 'muted'
    never = not snapshot.get('updated_at') and not snapshot.get('frames_considered')
    reasons = list(snapshot.get('reasons') or [])
    since = local_clock(snapshot.get('changed_at'))
    if snapshot.get('imaging_ok'):
        return (f"OK to image since {since}." if since else "OK to image."), 'ok'
    if capturing is False and never:
        return "Idle — start capture to judge the sky.", 'muted'
    if snapshot.get('stale'):
        last = local_clock(snapshot.get('updated_at'))
        at = f" since {last}" if last else ""
        if capturing is False:
            return f"Capture stopped — no frames{at}, so NINA gets no verdict.", 'muted'
        return f"No new frames{at} — NINA gets no verdict until they resume.", 'warn'
    head = reasons[0] if reasons else ''
    if head == 'warming_up':
        # No frame has been judged since the start: nothing has said the sky
        # is bad, and the first good frame is enough (verdict_hysteresis).
        if capturing is False:
            return "Idle — start capture to judge the sky.", 'muted'
        return ("Waiting for the first frame — nothing judged yet, so NINA is "
                "not held back."), 'info'
    if head == 'daylight':
        sun = snapshot.get('sun_altitude_deg')
        where = f" (sun at {sun:.0f}°)" if isinstance(sun, (int, float)) else ""
        return f"Waiting for dark{where} — imaging starts once the sun is low enough.", 'muted'
    pause = _last_pause(events)
    if head == 'recovering':
        when = f" at {_clock(pause['at'])}" if pause else ""
        why = f" — {cause_text(pause['reasons'][0])}" if pause and pause.get('reasons') else ""
        return (f"Imaging paused{when}{why}. {_good_needed_text(snapshot, good_needed)} "
                "to resume."), 'warn'
    why = cause_text(head) if head else "conditions not good enough"
    if since:
        return f"Imaging paused at {since} — {why}.", 'warn'
    return f"Not OK to image yet — {why}.", 'warn'


def duration_text(seconds) -> str:
    if seconds is None:
        return ""
    minutes = int(round(float(seconds) / 60))
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def event_line(event: dict, now=None):
    """``(time, text, duration)`` for one event row: ``("23:14", "Paused —
    cloud moving in", "42 min")``. A state that still holds says "so far"
    (or nothing under a minute)."""
    when = _clock(event.get('at'))
    if event.get('kind') == 'paused':
        reasons = event.get('reasons') or []
        text = "Paused — " + (cause_text(reasons[0]) if reasons else "conditions turned bad")
    else:
        text = "OK to image"
    seconds = event.get('duration_s')
    if seconds is not None:
        return when, text, duration_text(seconds)
    if now is not None and isinstance(event.get('at'), datetime):
        seconds = max(0.0, (now - event['at']).total_seconds())
        if seconds >= 60:
            return when, text, f"{duration_text(seconds)} so far"
    return when, text, "now"


def learning_progress(snapshot: dict):
    """``(caption, done, needed)`` for the learning bar, or None when there is
    nothing to show (off, never judged)."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if not snapshot.get('enabled') or (
            not snapshot.get('updated_at') and not snapshot.get('frames_considered')):
        return None
    status = snapshot.get('baseline_status') or {}
    moon = moon_bin_text(status.get('bin') or snapshot.get('baseline_bin')).lower()
    moon = f" with the {moon[0].upper()}{moon[1:]}" if moon.startswith('moon') else (
        f" ({moon})" if moon else "")
    needed = int(status.get('needed') or 0)
    done = min(int(status.get('samples') or 0), needed) if needed else 0
    if status.get('state') == 'ready':
        base = (snapshot.get('stars') or {}).get('baseline')
        tail = f": {base:.0f} stars" if isinstance(base, (int, float)) else ""
        nights = status.get('nights')
        if tail and isinstance(nights, int) and nights > 0:
            tail += f" from {nights} clear night{'' if nights == 1 else 's'}"
        return f"Clear-sky baseline learned{moon}{tail}", needed, needed
    return f"Learning your clear sky{moon} — {done} of {needed} clear frames", done, needed


def nina_checked_text(checkins: dict, capturing=None):
    """``(text, tone)`` for the footer line, from ``NinaCheckins.last()``."""
    checkins = checkins or {}
    nina = checkins.get('nina')
    other = checkins.get('any')
    if nina is None:
        if other is not None:
            return (f"NINA hasn't checked in · {other['client']} read it "
                    f"{age_text(other['age_s'])}"), 'muted'
        return "NINA hasn't checked in yet", 'muted'
    age = age_text(nina['age_s'])
    if not nina.get('accepted', True):
        return f"NINA tried {age} but was refused — check the control API", 'warn'
    if nina['age_s'] > NINA_STALE_S:
        return f"NINA last checked {age} — not lately", 'warn' if capturing else 'muted'
    return f"NINA last checked {age}", 'ok'


def measurements_line(snapshot: dict) -> str:
    """The latest frame's numbers in one short line: ``Stars 131/315 ·
    Cloud 58% · FWHM 2.9 px`` (star count / clear-sky baseline). Empty before the first judged frame."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if not snapshot.get('enabled') or (
            not snapshot.get('updated_at') and not snapshot.get('frames_considered')):
        return ""
    parts = []
    stars = snapshot.get('stars') or {}
    if stars.get('count') is not None:
        base = stars.get('baseline')
        parts.append(f"Stars {stars['count']}" + (
            f"/{base:.0f}" if isinstance(base, (int, float)) else ""))
    cloud = snapshot.get('cloud_cover_pct')
    if isinstance(cloud, (int, float)):
        parts.append(f"Cloud {cloud:.0f}%")
    fwhm = (snapshot.get('fwhm') or {}).get('px')
    if isinstance(fwhm, (int, float)):
        parts.append(f"FWHM {fwhm:.1f} px")
    if not parts:
        roof = (snapshot.get('roof') or {}).get('state')
        if roof and roof != 'Unknown':
            parts.append(f"Roof {roof.lower()}")
    return " · ".join(parts)
