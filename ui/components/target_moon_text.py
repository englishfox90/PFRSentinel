"""Wording for "how close is NINA's target to the Moon".

Formatting only. The numbers come from the conditions record's ``target``
and ``moon`` blocks (``services.imaging_conditions.target_moon``), and the
caution threshold is that module's — this is information for the operator,
never a verdict: Sentinel does not pause for it.
"""
from services.imaging_conditions.target_moon import is_close_to_moon

CLOSE_HINT = ("Close to a bright Moon: expect a brighter background. Sentinel "
              "does not pause for this; the NINA plugin can, with its "
              "\"Min Moon separation\" limit.")


def _article(number: int) -> str:
    """'an' before a number said with a vowel sound (8, 11, 18, 80–89)."""
    return "an" if number in (8, 11, 18) or 80 <= number <= 89 else "a"


def target_moon_text(snapshot):
    """``(text, close)``: 'Target M 15 is 18° from an 87% Moon', and whether
    that is inside the caution distance. Empty when there is no fresh
    target, or the Moon is known to be down (it then does not matter)."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    target = snapshot.get('target')
    if not snapshot.get('enabled', True) or not isinstance(target, dict):
        return "", False
    separation = target.get('moon_separation_deg')
    if isinstance(separation, bool) or not isinstance(separation, (int, float)):
        return "", False
    moon = snapshot.get('moon') or {}
    if moon.get('up') is False:
        return "", False
    name = str(target.get('name') or '').strip()
    subject = f"Target {name}" if name else "The target"
    illum = moon.get('illumination_pct')
    if isinstance(illum, (int, float)) and not isinstance(illum, bool):
        pct = round(illum)
        what = f"{_article(pct)} {pct}% Moon"
    else:
        what = "the Moon"
    text = f"{subject} is {round(separation)}° from {what}"
    return text, is_close_to_moon(separation, illum, moon.get('up'))


def target_moon_detail(snapshot) -> str:
    """The same line for a plain-text list, with the altitudes when known."""
    text, close = target_moon_text(snapshot)
    if not text:
        return ""
    snapshot = snapshot or {}
    parts = []
    target_alt = (snapshot.get('target') or {}).get('altitude_deg')
    moon_alt = (snapshot.get('moon') or {}).get('altitude_deg')
    if isinstance(target_alt, (int, float)):
        parts.append(f"target at {target_alt:.0f}°")
    if isinstance(moon_alt, (int, float)):
        parts.append(f"Moon at {moon_alt:.0f}°")
    if parts:
        text += f" ({', '.join(parts)} altitude)"
    return text + (" — close" if close else "")


def stored_separation_text(separation_deg, moon_bin=None) -> str:
    """'Target 18° from the Moon' for a library frame; empty when no target
    was reported or the Moon was down."""
    if moon_bin == 'moon_down':
        return ""
    if isinstance(separation_deg, bool) or not isinstance(separation_deg, (int, float)):
        return ""
    return f"Target {round(separation_deg)}° from the Moon"
