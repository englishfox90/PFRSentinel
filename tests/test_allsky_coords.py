"""
Tests for services/allsky/coords.py — coordinate transforms and refraction.

All tests use known reference values from Meeus "Astronomical Algorithms" 2nd ed.
or verifiable astronomical facts (e.g. zenith star always at alt=90°).
"""
import math
import pytest
from datetime import datetime, timezone

# ------------------------------------------------------------------ imports
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.coords import (
    julian_date,
    gmst_degrees,
    lst_degrees,
    radec_to_altaz,
    altaz_to_radec,
    atmospheric_refraction,
    ecliptic_to_equatorial,
    geocentric_to_topocentric,
)


# ===================================================================
# Precession of the equinoxes (J2000 → epoch of date)
# ===================================================================

class TestPrecession:
    def test_identity_at_j2000(self):
        """At the J2000 epoch the shift is zero."""
        from services.allsky.coords import precess_from_j2000
        dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ra, dec = precess_from_j2000(201.298, -11.161, dt)
        assert float(ra) == pytest.approx(201.298, abs=1e-3)
        assert float(dec) == pytest.approx(-11.161, abs=1e-3)

    def test_spica_shift_2026(self):
        """Spica precesses ~0.37° over 26 years (RA increases, Dec decreases)."""
        from services.allsky.coords import precess_from_j2000
        dt = datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc)
        ra, dec = precess_from_j2000(201.298, -11.161, dt)
        # RA grows, Dec drops; total separation ~0.35-0.40°
        assert float(ra) > 201.298
        assert float(dec) < -11.161
        sep = math.hypot((float(ra) - 201.298) * math.cos(math.radians(-11.2)),
                         float(dec) - (-11.161))
        assert sep == pytest.approx(0.37, abs=0.05)

    def test_grows_with_time(self):
        """Precession is larger for a more distant epoch."""
        from services.allsky.coords import precess_from_j2000
        _, d2030 = precess_from_j2000(0.0, 0.0, datetime(2030, 1, 1, tzinfo=timezone.utc))
        _, d2100 = precess_from_j2000(0.0, 0.0, datetime(2100, 1, 1, tzinfo=timezone.utc))
        assert abs(float(d2100)) > abs(float(d2030))

    def test_accepts_arrays(self):
        from services.allsky.coords import precess_from_j2000
        import numpy as np
        ra = np.array([10.0, 200.0]); dec = np.array([20.0, -30.0])
        ra2, dec2 = precess_from_j2000(ra, dec, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert ra2.shape == (2,) and dec2.shape == (2,)

    def test_matches_meeus_worked_example_21b(self):
        """Published apparent position (plan #93 5d'): Meeus, Astronomical
        Algorithms 2nd ed., Example 21.b — θ Persei with proper motion applied
        (α 2h44m12.975s, δ +49°13'39.90") precessed to 2028 Nov 13.19 TD
        (JD 2462088.69) is α 2h46m11.331s, δ +49°20'54.54". The IAU 1976
        rotation lands within 0.01" of it; a wrong sign convention or a
        dropped term would miss by arcminutes."""
        from datetime import timedelta
        from services.allsky.coords import julian_date, precess_from_j2000
        dt = datetime(2028, 11, 13, tzinfo=timezone.utc) + timedelta(days=0.19)
        assert julian_date(dt) == pytest.approx(2462088.69, abs=1e-6)
        ra, dec = precess_from_j2000((2 + 44 / 60 + 12.975 / 3600) * 15.0,
                                     49 + 13 / 60 + 39.90 / 3600, dt)
        exp_ra = (2 + 46 / 60 + 11.331 / 3600) * 15.0
        exp_dec = 49 + 20 / 60 + 54.54 / 3600
        assert abs(float(ra) - exp_ra) * 3600 < 0.01
        assert abs(float(dec) - exp_dec) * 3600 < 0.01

    def test_calibration_catalogue_is_of_date(self):
        """The bright-star catalogue every calibration matches against is
        precessed to the epoch of the load, not left at J2000: Vega's stored
        position must equal its J2000 position precessed to now (~0.18° apart for Vega, up to 0.4° elsewhere
        in 2026 — 3–7 px at the reporter's plate scale, not a rigid rotation
        the fit could absorb)."""
        import math
        from services.allsky.catalogs import get_bright_stars
        from services.allsky.coords import precess_from_j2000
        vega = next(s for s in get_bright_stars(max_mag=1.0) if str(s['hr']) == '7001')
        ra_now, dec_now = precess_from_j2000(279.23473, 38.78369,
                                             datetime.now(timezone.utc))
        assert abs(vega['ra_deg'] - float(ra_now)) < 0.01
        assert abs(vega['dec_deg'] - float(dec_now)) < 0.01
        sep_j2000 = math.hypot((vega['ra_deg'] - 279.23473) * math.cos(math.radians(38.8)),
                               vega['dec_deg'] - 38.78369)
        assert sep_j2000 > 0.1


# ===================================================================
# Topocentric (diurnal) parallax — significant for the Moon (~1°)
# ===================================================================

class TestTopocentricParallax:
    LAT, LON = 31.3303162, -100.4570705
    DT = datetime(2026, 5, 26, 3, 50, 44, tzinfo=timezone.utc)
    MOON_PARALLAX = 0.927  # equatorial horizontal parallax (deg) at that time

    def test_parallax_lowers_moon_altitude(self):
        """Topocentric Moon is always at or below the geocentric altitude."""
        from services.allsky.planets import moon_radec
        ra, dec = moon_radec(self.DT)
        ra_t, dec_t = geocentric_to_topocentric(
            ra, dec, self.MOON_PARALLAX, self.LAT, self.LON, self.DT)
        g_alt, _ = radec_to_altaz(ra, dec, self.LAT, self.LON, self.DT, refraction=False)
        t_alt, _ = radec_to_altaz(ra_t, dec_t, self.LAT, self.LON, self.DT, refraction=False)
        assert float(t_alt) < float(g_alt)
        # ~0.6° lower at alt 50° (π·cos(alt))
        assert float(g_alt) - float(t_alt) == pytest.approx(0.6, abs=0.15)

    def test_zero_parallax_is_identity(self):
        ra_t, dec_t = geocentric_to_topocentric(
            150.0, 20.0, 0.0, self.LAT, self.LON, self.DT)
        assert ra_t == pytest.approx(150.0)
        assert dec_t == pytest.approx(20.0)

    def test_get_all_positions_moon_topocentric_differs(self):
        """Passing lat/lon shifts the Moon; omitting them returns geocentric."""
        from services.allsky.planets import get_all_positions
        geo = get_all_positions(self.DT)['Moon']
        topo = get_all_positions(self.DT, self.LAT, self.LON)['Moon']
        assert geo != topo


# ===================================================================
# Julian Date
# ===================================================================

class TestJulianDate:
    def test_j2000_epoch(self):
        """J2000.0 = 2000-01-01 12:00:00 UTC → JD 2451545.0"""
        dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert abs(julian_date(dt) - 2451545.0) < 1e-5

    def test_b1950_epoch(self):
        """B1950.0 ≈ JD 2433282.423"""
        dt = datetime(1950, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        jd = julian_date(dt)
        assert abs(jd - 2433282.5) < 0.1

    def test_gregorian_reform(self):
        """Meeus example: 1582-10-15 → JD 2299160.5"""
        dt = datetime(1582, 10, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert abs(julian_date(dt) - 2299160.5) < 0.01

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime should be treated as UTC (no timezone offset)."""
        dt_naive  = datetime(2024, 6, 15, 12, 0, 0)
        dt_aware  = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert abs(julian_date(dt_naive) - julian_date(dt_aware)) < 1e-8


# ===================================================================
# GMST
# ===================================================================

class TestGMST:
    def test_j2000_gmst(self):
        """
        At J2000.0 (2000-01-01 12:00:00 UTC), GMST ≈ 280.46061° (Meeus Eq. 12.4).
        """
        jd = 2451545.0
        g = gmst_degrees(jd) % 360.0
        assert abs(g - 280.46061) < 0.01

    def test_gmst_range(self):
        """GMST must be in [0, 360)."""
        for jd in [2451545.0, 2451545.5, 2400000.0, 2500000.0]:
            g = gmst_degrees(jd)
            assert 0.0 <= g < 360.0


# ===================================================================
# Coordinate round-trip
# ===================================================================

class TestAltAzRoundTrip:
    """Convert RA/Dec → AltAz → RA/Dec; verify round-trip to < 0.1°."""

    LAT = 51.5    # London
    LON = -0.1
    DT  = datetime(2024, 3, 20, 22, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("ra,dec", [
        (0.0,   0.0),    # Vernal equinox direction
        (90.0,  30.0),
        (180.0, -45.0),
        (270.0, 60.0),
        (45.0,  75.0),
    ])
    def test_round_trip(self, ra, dec):
        alt, az = radec_to_altaz(ra, dec, self.LAT, self.LON, self.DT,
                                 refraction=False)
        ra2, dec2 = altaz_to_radec(float(alt), float(az), self.LAT, self.LON, self.DT,
                                    refraction=False)
        assert abs(dec2 - dec) < 0.1, f"Dec round-trip failed: {dec} → {dec2}"
        # RA wraps; check modular difference
        d_ra = abs((ra2 - ra + 180) % 360 - 180)
        assert d_ra < 0.2, f"RA round-trip failed: {ra} → {ra2}"

    def test_zenith_is_90(self):
        """A star at the zenith should have alt = 90° (ignoring refraction)."""
        # The LST at this moment gives the zenith RA
        from services.allsky.coords import julian_date
        jd = julian_date(self.DT)
        ra_zenith = lst_degrees(jd, self.LON)
        dec_zenith = self.LAT
        alt, az = radec_to_altaz(ra_zenith, dec_zenith, self.LAT, self.LON, self.DT,
                                 refraction=False)
        assert abs(float(alt) - 90.0) < 0.5, f"Zenith alt = {alt} ≠ 90°"

    def test_northern_star_circumpolar(self):
        """Polaris (dec ≈ 89.3°) should always be above horizon at lat=51.5°."""
        ra_polaris, dec_polaris = 37.95, 89.26
        for hour in range(0, 24, 4):
            dt = datetime(2024, 3, 20, hour, 0, 0, tzinfo=timezone.utc)
            alt, _ = radec_to_altaz(ra_polaris, dec_polaris, self.LAT, self.LON, dt,
                                    refraction=False)
            assert float(alt) > 0.0, f"Polaris below horizon at hour={hour}"


# ===================================================================
# Atmospheric Refraction
# ===================================================================

class TestRefraction:
    def test_zenith_near_zero(self):
        """Refraction at zenith (alt=90°) should be ≈ 0."""
        r = float(atmospheric_refraction(90.0))
        assert r < 0.01

    def test_horizon_half_degree(self):
        """Refraction at horizon (alt=0°) should be ≈ 0.5°."""
        r = float(atmospheric_refraction(0.0))
        assert 0.4 < r < 0.6, f"Horizon refraction = {r}° (expected ≈0.5°)"

    def test_below_horizon_zero(self):
        """Refraction below -2° should return 0."""
        r = float(atmospheric_refraction(-5.0))
        assert r == 0.0

    def test_monotonic(self):
        """Refraction should decrease monotonically from horizon to zenith."""
        alts = [0, 5, 10, 20, 30, 45, 60, 80, 90]
        refractions = [float(atmospheric_refraction(a)) for a in alts]
        for i in range(len(refractions) - 1):
            assert refractions[i] >= refractions[i + 1], (
                f"Refraction non-monotonic at alt={alts[i]}"
            )

    def test_refraction_applied_increases_alt(self):
        """Adding refraction should increase the apparent altitude."""
        alt_true = 20.0
        refraction = float(atmospheric_refraction(alt_true))
        assert refraction > 0.0


# ===================================================================
# Ecliptic → Equatorial
# ===================================================================

class TestEclipticToEquatorial:
    def test_vernal_equinox(self):
        """Ecliptic lon=0°, lat=0° → RA=0°, Dec=0°."""
        jd = 2451545.0  # J2000.0
        ra, dec = ecliptic_to_equatorial(0.0, 0.0, jd)
        assert abs(float(ra)) < 0.5
        assert abs(float(dec)) < 0.5

    def test_summer_solstice(self):
        """Ecliptic lon=90°, lat=0° → RA≈90°, Dec≈+23.4°."""
        jd = 2451545.0
        ra, dec = ecliptic_to_equatorial(90.0, 0.0, jd)
        assert abs(float(ra) - 90.0) < 1.0
        assert abs(float(dec) - 23.4) < 0.5
