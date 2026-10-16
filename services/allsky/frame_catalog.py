"""
Catalogue stars above the horizon for one frame.

The calibration buffer stores, per frame, the bright-star catalogue entries
that were above the horizon at that frame's instant, each with its alt/az.
That list is a pure function of (dt, lat, lon) and the bundled catalogue,
so it is computed here — once, vectorised — for the live service
(`CalibrationService._detect_frame`), the buffer dump loader and the
image-library loader, which must all build the identical shape:
``[(catalogue_star_dict, alt_deg, az_deg), ...]`` sorted by magnitude.
"""
from datetime import datetime
from typing import List, Tuple

import numpy as np

from .catalogs import get_bright_stars
from .coords import radec_to_altaz

# Faintest catalogue star the joint fit is allowed to match. 6.5 is the
# naked-eye limit, which is 8404 of the bundled BSC5 entries: the fit's
# greedy matcher already caps candidates per frame, so a deeper list adds
# chance matches without adding anchors.
CATALOG_MAX_VMAG = 6.5

# Stars below this altitude are dropped before matching. At 3° refraction is
# ~15' and changing fast with pressure, and every all-sky rig has horizon
# obstruction there anyway; nothing usable is lost.
MIN_ALTITUDE_DEG = 3.0


def above_horizon_stars(
    dt: datetime,
    lat: float,
    lon: float,
    max_vmag: float = CATALOG_MAX_VMAG,
    min_alt_deg: float = MIN_ALTITUDE_DEG,
) -> List[Tuple[dict, float, float]]:
    """Catalogue stars above ``min_alt_deg`` at ``dt`` from (``lat``, ``lon``).

    Returns ``[(star, alt_deg, az_deg), ...]`` sorted by ``vmag`` (brightest
    first), where ``star`` is the shared catalogue dict. One vectorised
    `radec_to_altaz` call per frame: bit-identical to the per-star loop it
    replaced and tens of times faster on the 8404-entry list.
    """
    catalog = get_bright_stars(max_mag=max_vmag)
    if not catalog:
        return []
    ra = np.array([s['ra_deg'] for s in catalog], dtype=float)
    dec = np.array([s['dec_deg'] for s in catalog], dtype=float)
    alt, az = radec_to_altaz(ra, dec, lat, lon, dt)
    alt = np.atleast_1d(alt)
    az = np.atleast_1d(az)
    above = [
        (catalog[i], float(alt[i]), float(az[i]))
        for i in np.flatnonzero(alt > min_alt_deg)
    ]
    above.sort(key=lambda x: x[0]['vmag'])
    return above
