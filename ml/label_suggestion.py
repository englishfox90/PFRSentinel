#!/usr/bin/env python3
"""Decide the labels to pre-fill for an unlabeled frame — pure, Qt-free.

Source priority is set by how each source scored against ~4,000 human labels
(Sep 2026): the NINA roof state agreed 99.4 % of the time, the AI vision model
94.9 %, and NINA + AI together 99.9 %. So NINA wins the roof call; the AI is the
tie-breaker when NINA is absent, and the local CNN after that. The AI only ever
judges roof and cloud; stars / moon come from the CNN, then from sky context.
"""


def to_bool(value) -> bool:
    """Calibration JSONs carry booleans as real bools or as 'True'/'False' strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes')
    return bool(value)


CLOUDY = ("Partly Cloudy", "Overcast")


def roof_votes(cal: dict, roof_pred=None) -> dict:
    """Every available roof opinion, keyed by source name."""
    votes = {}
    rs = cal.get('roof_state') or {}
    if rs.get('available') and rs.get('source') == 'nina_api' and rs.get('roof_open') is not None:
        votes['NINA'] = to_bool(rs.get('roof_open'))
    ai = cal.get('ai_suggestion')
    if ai:
        votes['AI'] = bool(ai.get('roof_open'))
    if roof_pred is not None:
        votes['ML'] = bool(roof_pred.roof_open)
    return votes


def suggest_labels(cal: dict, roof_pred=None, sky_pred=None) -> dict:
    """Best-guess labels for a frame, plus where each came from.

    `agreed` is True when at least two roof sources exist and none dissents —
    the bar for confirming a frame in bulk rather than one at a time.
    """
    votes = roof_votes(cal, roof_pred)
    if votes:
        roof_source = next(s for s in ('NINA', 'AI', 'ML') if s in votes)
        roof_open = votes[roof_source]
    else:
        roof_source = 'corner ratio'
        ratio = (cal.get('corner_analysis') or {}).get('corner_to_center_ratio', 1.0)
        roof_open = ratio < 0.95
    agreed = len(votes) >= 2 and len(set(votes.values())) == 1

    ai = cal.get('ai_suggestion') or {}
    wc = cal.get('weather_context') or {}
    sky_condition, sky_source = '', ''
    if roof_open:
        if ai.get('roof_open') and ai.get('sky_condition'):
            sky_condition, sky_source = ai['sky_condition'], 'AI'
        elif sky_pred is not None:
            sky_condition, sky_source = sky_pred.sky_condition, 'ML'
        elif wc.get('available'):
            pct = wc.get('cloud_coverage_pct') or 0
            sky_condition = "Clear" if pct <= 25 else "Partly Cloudy" if pct <= 75 else "Overcast"
            sky_source = 'weather'

    if not roof_open:
        stars, density, moon = False, 0.0, False
    elif sky_pred is not None:
        stars = bool(sky_pred.stars_visible)
        density = float(sky_pred.star_density) if stars else 0.0
        moon = bool(sky_pred.moon_visible)
    else:
        tc = cal.get('time_context') or {}
        mc = cal.get('moon_context') or {}
        stars = bool(tc.get('is_astronomical_night')) and sky_condition != "Overcast"
        density = 0.5 if stars else 0.0
        moon = bool(mc.get('moon_is_up')) if mc.get('available') else False

    return {
        'roof_open': roof_open,
        'sky_condition': sky_condition,
        'clouds_visible': sky_condition in CLOUDY,
        'stars_visible': stars,
        'star_density': density,
        'moon_visible': moon,
        'roof_source': roof_source,
        'sky_source': sky_source,
        'roof_votes': votes,
        'agreed': agreed,
    }


def describe_sources(suggestion: dict) -> str:
    """Short provenance line for the form, e.g. 'roof: NINA ✓AI ✓ML · sky: AI'."""
    votes = suggestion['roof_votes']
    lead = suggestion['roof_source']
    others = [f"{'✓' if v == suggestion['roof_open'] else '✗'}{s}"
              for s, v in votes.items() if s != lead]
    text = f"roof: {lead}" + (" " + " ".join(others) if others else "")
    if suggestion['sky_source']:
        text += f" · sky: {suggestion['sky_source']}"
    return text


def labels_from_suggestion(suggestion: dict, labeled_at: str, source: str) -> dict:
    """The `labels` block to store when a suggestion is accepted as-is."""
    labels = {
        'roof_open': suggestion['roof_open'],
        'stars_visible': suggestion['stars_visible'],
        'star_density': suggestion['star_density'],
        'moon_visible': suggestion['moon_visible'],
        'labeled_at': labeled_at,
        'label_source': source,
    }
    if suggestion['roof_open']:
        # Sky condition is only meaningful when the pier camera can see the sky.
        labels['clouds_visible'] = suggestion['clouds_visible']
        if suggestion['sky_condition']:
            labels['sky_condition'] = suggestion['sky_condition']
    return labels
