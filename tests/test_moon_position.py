"""moon_position — the Moon's place in the sky from the all-sky overlay's
ephemeris, and the great-circle distance used for target / Moon separation."""
from datetime import datetime, timezone

import pytest

import services.moon as moon
from services import moon_position as mp

DT = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
LAT, LON = 31.55, -100.46


def test_altitude_agrees_with_astral():
    from astral import Observer
    from astral.moon import azimuth, elevation
    pos = mp.moon_position(LAT, LON, DT)
    obs = Observer(LAT, LON)
    # astral is geocentric; ours carries the Moon's parallax (up to ~1°).
    assert pos['altitude_deg'] == pytest.approx(elevation(obs, DT), abs=1.5)
    assert pos['azimuth_deg'] == pytest.approx(azimuth(obs, DT), abs=1.5)


def test_without_a_site_there_is_a_position_but_no_altitude():
    pos = mp.moon_position(None, None, DT)
    assert pos['altitude_deg'] is None and pos['azimuth_deg'] is None
    topo = mp.moon_position(LAT, LON, DT)
    assert mp.angular_separation_deg(pos['ra_deg'], pos['dec_deg'],
                                     topo['ra_deg'], topo['dec_deg']) < 1.2


@pytest.mark.parametrize("a, b, expected", [
    ((0, 0), (90, 0), 90.0), ((10, 89), (190, 89), 2.0), ((5, 5), (5, 5), 0.0),
    ((0, 0), (180, 0), 180.0), ((359, 0), (1, 0), 2.0), ((0, 90), (123, -90), 180.0),
])
def test_angular_separation(a, b, expected):
    assert mp.angular_separation_deg(*a, *b) == pytest.approx(expected, abs=1e-6)
    assert mp.angular_separation_deg(*b, *a) == pytest.approx(expected, abs=1e-6)


def test_j2000_is_precessed_to_the_date():
    ra, dec = mp.j2000_of_date(10.0, 41.0, DT)
    moved = mp.angular_separation_deg(10.0, 41.0, ra, dec)
    assert 0.2 < moved < 0.6    # ~27 years of precession


def test_moon_context_carries_the_altitude_with_a_site(monkeypatch):
    monkeypatch.setattr(moon, 'get_configured_location', lambda: (LAT, LON, 'Obs'))
    ctx = moon.compute_moon_context()
    assert -90.0 <= ctx['altitude_deg'] <= 90.0
    assert 0.0 <= ctx['azimuth_deg'] <= 360.0


def test_moon_context_without_a_site_has_no_altitude(monkeypatch):
    monkeypatch.setattr(moon, 'get_configured_location', lambda: (None, None, None))
    assert 'altitude_deg' not in moon.compute_moon_context()


def test_a_failing_ephemeris_reads_as_unknown_altitude(monkeypatch):
    monkeypatch.setattr(moon, 'get_configured_location', lambda: (LAT, LON, 'Obs'))

    def boom(*a, **k):
        raise RuntimeError("no ephemeris")
    monkeypatch.setattr(mp, 'moon_position', boom)
    ctx = moon.compute_moon_context()
    assert ctx['available'] is True and ctx['altitude_deg'] is None
