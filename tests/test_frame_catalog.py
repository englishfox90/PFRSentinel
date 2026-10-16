"""
`frame_catalog.above_horizon_stars` must be a drop-in for the per-star loop
`CalibrationService._detect_frame` used to run inline: the same catalogue
objects, the same alt/az to the bit, the same magnitude order and the same
``(star, alt, az)`` tuple shape — on either hemisphere and at a site where
most of the catalogue never rises.
"""
from datetime import datetime, timezone

import pytest

from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.frame_catalog import above_horizon_stars


def _scalar_loop(dt, lat, lon):
    """The loop as it stood in calibration_service.py before the extraction."""
    catalog = get_bright_stars(max_mag=6.5)
    above_horizon = []
    for s in catalog:
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], lat, lon, dt)
        if float(alt) > 3.0:
            above_horizon.append((s, float(alt), float(az)))
    above_horizon.sort(key=lambda x: x[0]['vmag'])
    return above_horizon


@pytest.mark.parametrize('dt, lat, lon', [
    (datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc), 36.42, -116.87),   # reporter's site
    (datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc), -33.87, 151.21),  # Sydney, southern
    (datetime(2026, 12, 21, 0, 0, tzinfo=timezone.utc), 78.22, 15.63),     # Svalbard, polar night
], ids=['mid-latitude', 'southern', 'polar'])
def test_matches_the_scalar_loop_exactly(dt, lat, lon):
    expected = _scalar_loop(dt, lat, lon)
    got = above_horizon_stars(dt, lat, lon)
    assert len(got) == len(expected) > 100
    for (s_got, alt_got, az_got), (s_exp, alt_exp, az_exp) in zip(got, expected):
        assert s_got is s_exp
        assert alt_got == alt_exp and az_got == az_exp
        assert isinstance(alt_got, float) and isinstance(az_got, float)
    assert all(isinstance(t, tuple) and len(t) == 3 for t in got)
