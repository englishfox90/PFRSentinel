"""
Which objects may carry a label this frame, brightest first.

The ranking the top-N budget is chosen from, and the only place a label's
eligibility is decided: an object is a candidate when its layer is enabled,
it is at least ``LABEL_MIN_ALT_DEG`` above the horizon (the same cutoff the
layers draw from), the model projects it into the frame and the visibility
plane calls its pixel open sky. A candidate that is also inside the budget
is *eligible*, and the drawn-label persistence (label_persistence) decides
from that what is drawn.

Kept apart from the overlay renderer so the dev replay
(``scripts/dev/allsky/replay_labels.py``) runs exactly this ranking on a
buffer dump.
"""
from datetime import datetime
from typing import List, Optional, Set

import numpy as np

from .catalogs import get_bright_stars, get_messier_objects, get_ngc_objects
from .coords import radec_to_altaz
from .fisheye import FisheyeModel
from .planets import get_all_positions
from .render_objects import LABEL_MIN_ALT_DEG, _is_sky_visible
from .render_stars import star_display_name, star_uid

# Approximate visual magnitudes of the planets, for ranking only.
PLANET_RANK_MAG = {
    'Moon': -12.0, 'Venus': -4.0, 'Jupiter': -2.0, 'Mars': 0.5,
    'Mercury': 0.0, 'Saturn': 0.7, 'Uranus': 5.7, 'Neptune': 7.8,
}


def rank_label_candidates(
    config: dict,
    model: FisheyeModel,
    lat: float,
    lon: float,
    dt: datetime,
    gray: Optional[np.ndarray] = None,
    moon_seen: bool = False,
) -> List[str]:
    """UIDs of every object a layer would label on ``gray``, brightest
    first: ``'star:HR7001'``, ``'planet:Jupiter'``, ``'messier:M45'``,
    ``'ngc:NGC 2244'``. ``gray`` None skips the visibility test. A Moon
    whose glare was measured (``moon_seen``) needs none: the plane cannot
    see inside the glare."""
    def _projected(ra, dec):
        alt, az = radec_to_altaz(ra, dec, lat, lon, dt, refraction=True)
        if float(alt) < LABEL_MIN_ALT_DEG:
            return None
        return model.altaz_to_pixel(float(alt), float(az))

    def _visible(xy) -> bool:
        if xy is None:
            return False
        return gray is None or _is_sky_visible(gray, int(xy[0]), int(xy[1]))

    candidates = []  # (mag, uid)

    stars_cfg = config.get('bright_stars', {})
    if stars_cfg.get('enabled', False):
        use_bayer = bool(stars_cfg.get('bayer_fallback', False))
        for s in get_bright_stars(max_mag=float(stars_cfg.get('max_magnitude', 2.5))):
            if star_display_name(s, use_bayer) and _visible(_projected(s['ra_deg'], s['dec_deg'])):
                candidates.append((float(s['vmag']), star_uid(s)))

    if config.get('planets', {}).get('enabled', True):
        for name, (ra, dec) in get_all_positions(dt, lat, lon).items():
            if name == 'Sun':
                continue
            xy = _projected(ra, dec)
            if xy is None:
                continue
            if (name == 'Moon' and moon_seen) or _visible(xy):
                candidates.append((PLANET_RANK_MAG.get(name, 0.0), f'planet:{name}'))

    if config.get('messier', {}).get('enabled', True):
        for obj in get_messier_objects():
            label = obj.get('label', '')
            if label and _visible(_projected(obj['ra_deg'], obj['dec_deg'])):
                mag = float(obj.get('vmag') or obj.get('mag') or 10.0)
                candidates.append((mag, f'messier:{label}'))

    ngc_cfg = config.get('ngc', {})
    if ngc_cfg.get('enabled', False):
        for obj in get_ngc_objects(max_mag=float(ngc_cfg.get('min_magnitude', 12.0))):
            if obj.get('messier'):
                continue
            oid = obj.get('id', obj.get('name', ''))
            if oid and _visible(_projected(obj['ra_deg'], obj['dec_deg'])):
                mag = float(obj.get('vmag') or obj.get('mag') or 99.0)
                candidates.append((mag, f'ngc:{oid}'))

    candidates.sort(key=lambda c: c[0])
    return [uid for _, uid in candidates]


def eligible_labels(ranked: List[str], allowed_ids: Optional[Set[str]]) -> List[str]:
    """The candidates inside the budget: what this frame would label on its
    own. A held incumbent the budget keeps (label_stability) is in
    ``allowed_ids`` but not in ``ranked``, and is not eligible."""
    if allowed_ids is None:
        return list(ranked)
    return [uid for uid in ranked if uid in allowed_ids]
