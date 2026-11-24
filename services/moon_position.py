"""Where the Moon is in the sky, and how far a target is from it.

One Moon for the whole app: the position is the all-sky overlay's own
(``services.allsky.planets``, Meeus ch. 47, about 5 arcminutes), so the
altitude the imaging-conditions baseline is binned by and the Moon label on
the overlay can never disagree. Everything is in the equinox of date —
J2000 targets are precessed (``coords.precess_from_j2000``) before they are
compared with it. Pure maths: no config, no I/O.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional


def moon_position(lat_deg=None, lon_deg=None, dt: Optional[datetime] = None) -> dict:
    """``{ra_deg, dec_deg, altitude_deg, azimuth_deg}`` for the Moon at
    ``dt`` (UTC now by default).

    With a site the position is topocentric (the Moon's parallax is about a
    degree) and the altitude carries refraction. Without one the RA/Dec is
    geocentric and altitude / azimuth are None.
    """
    from services.allsky.coords import radec_to_altaz
    from services.allsky.planets import moon_radec, moon_radec_topocentric

    dt = dt or datetime.now(timezone.utc)
    if lat_deg is None or lon_deg is None:
        ra, dec = moon_radec(dt)
        return {'ra_deg': float(ra), 'dec_deg': float(dec),
                'altitude_deg': None, 'azimuth_deg': None}
    ra, dec = moon_radec_topocentric(dt, float(lat_deg), float(lon_deg))
    alt, az = radec_to_altaz(ra, dec, float(lat_deg), float(lon_deg), dt, refraction=True)
    return {'ra_deg': float(ra), 'dec_deg': float(dec),
            'altitude_deg': float(alt), 'azimuth_deg': float(az)}


def j2000_of_date(ra_deg: float, dec_deg: float, dt: datetime) -> tuple:
    """A J2000 position in the equinox of ``dt``, as plain floats."""
    from services.allsky.coords import precess_from_j2000
    ra, dec = precess_from_j2000(float(ra_deg), float(dec_deg), dt)
    return float(ra), float(dec)


def altitude_deg(ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float,
                 dt: datetime) -> float:
    """Altitude (with refraction) of an of-date position from a site."""
    from services.allsky.coords import radec_to_altaz
    return float(radec_to_altaz(ra_deg, dec_deg, float(lat_deg), float(lon_deg), dt,
                                refraction=True)[0])


def angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance between two positions in the same frame."""
    r1, d1, r2, d2 = (math.radians(float(v)) for v in (ra1, dec1, ra2, dec2))
    # Vincenty: accurate at 0° and 180°, where the plain cosine rule is not.
    dr = r2 - r1
    y = math.hypot(math.cos(d2) * math.sin(dr),
                   math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(dr))
    x = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dr)
    return math.degrees(math.atan2(y, x))
