"""
Tests for services/time_context.py — the ``is_astronomical_night`` feature
shared by the calibration JSON (training) and MLService (inference), issue #86.

The event-time version this replaces read "night" for most of the afternoon
at a western-hemisphere site (astral clamps sunset to the calendar date of the
zone asked in, so a Texas sunset in UTC came before that date's sunrise) and
handed ``twilight()`` ints it compares against ``SunDirection`` members, so it
never computed astronomical twilight at all. These tests pin the flag to the
sun's actual elevation, at absolute instants, whatever the host zone is.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import services.time_context as time_context
from services.time_context import compute_time_context

astral = pytest.importorskip("astral")

TEXAS = (31.55, -100.46, "West Texas")
CHICAGO = ZoneInfo("America/Chicago")

# 2026-06-21 at (31.55, -100.46): astronomical dusk 22:29 CDT, dawn 04:57 CDT.
SUMMER_DUSK = datetime(2026, 6, 21, 22, 29, tzinfo=CHICAGO)
SUMMER_DAWN = datetime(2026, 6, 21, 4, 57, tzinfo=CHICAGO)


@pytest.fixture(autouse=True)
def _clear_sun_times_cache():
    time_context._SUN_TIMES_CACHE.clear()
    yield
    time_context._SUN_TIMES_CACHE.clear()


def _night(now, location=TEXAS):
    return compute_time_context(now, location)['is_astronomical_night']


# --- the flag follows real astronomical twilight ----------------------------

def test_flag_flips_at_astronomical_dusk():
    assert _night(SUMMER_DUSK - timedelta(minutes=10)) is False
    assert _night(SUMMER_DUSK + timedelta(minutes=10)) is True


def test_flag_flips_at_astronomical_dawn():
    assert _night(SUMMER_DAWN - timedelta(minutes=10)) is True
    assert _night(SUMMER_DAWN + timedelta(minutes=10)) is False


def test_afternoon_is_never_night_at_a_western_site():
    # The regression: the event-time computation called 15:00 in Texas "night".
    for month in (3, 6, 9, 12):
        assert _night(datetime(2026, month, 21, 15, 0, tzinfo=CHICAGO)) is False


def test_night_hours_follow_the_season():
    def night_hours(month):
        return sum(
            _night(datetime(2026, month, 21, h, 30, tzinfo=CHICAGO)) for h in range(24))
    # ~7 dark hours in June, ~11 in December at 31.5° N.
    assert 6 <= night_hours(6) <= 8
    assert 10 <= night_hours(12) <= 12


def test_clock_night_is_not_astronomical_night():
    # 21:00 CDT in June: the old inference clock (hour >= 20) said night; the
    # sun is still only ~13° down.
    assert _night(datetime(2026, 6, 21, 21, 0, tzinfo=CHICAGO)) is False


# --- absolute instants, not host wall-clock ---------------------------------

def test_same_instant_in_any_zone_gives_the_same_flag():
    instant = SUMMER_DUSK + timedelta(minutes=10)
    for zone in ("UTC", "Europe/London", "Asia/Tokyo"):
        assert _night(instant.astimezone(ZoneInfo(zone))) is True


def test_naive_input_is_read_as_host_local_time():
    instant = SUMMER_DUSK + timedelta(minutes=10)
    host_local = instant.astimezone().replace(tzinfo=None)
    assert _night(host_local) is True
    assert compute_time_context(host_local, TEXAS)['hour'] == host_local.hour


def test_sun_times_land_on_the_site_day_in_order():
    ctx = compute_time_context(datetime(2026, 6, 21, 12, 0, tzinfo=CHICAGO), TEXAS)
    times = {k: datetime.fromisoformat(v) for k, v in ctx['sun_times'].items()}
    assert times['dawn'] < times['sunrise'] < times['noon'] < times['sunset'] < times['dusk']
    assert times['sunset'].replace(microsecond=0).isoformat() == '2026-06-22T01:49:43+00:00'


# --- period classification --------------------------------------------------

@pytest.mark.parametrize("hour, minute, period, detailed", [
    (3, 0, 'night', 'night'),
    (6, 20, 'twilight', 'dawn'),   # civil dawn 06:09, sunrise 06:37 CDT
    (9, 0, 'day', 'morning'),
    (15, 0, 'day', 'afternoon'),
    (20, 0, 'day', 'evening'),     # sunset 20:49 CDT: within two hours
    (21, 0, 'twilight', 'dusk'),   # civil dusk 21:18 CDT
    (23, 0, 'night', 'night'),
])
def test_periods_across_a_summer_day(hour, minute, period, detailed):
    ctx = compute_time_context(datetime(2026, 6, 21, hour, minute, tzinfo=CHICAGO), TEXAS)
    assert (ctx['period'], ctx['detailed_period']) == (period, detailed)
    assert ctx['is_daylight'] is (period == 'day')
    assert ctx['calculation_method'] == 'astral'


# --- edge cases -------------------------------------------------------------

def test_high_latitude_summer_never_reaches_astronomical_night():
    oslo = (59.91, 10.75, "Oslo")
    for hour in range(24):
        ctx = compute_time_context(
            datetime(2026, 6, 21, hour, 0, tzinfo=ZoneInfo("Europe/Oslo")), oslo)
        assert ctx['is_astronomical_night'] is False
        assert ctx['calculation_method'] == 'astral'


def test_polar_day_degrades_without_sun_times():
    svalbard = (78.2, 15.6, "Longyearbyen")
    ctx = compute_time_context(datetime(2026, 6, 21, 12, 0, tzinfo=ZoneInfo("UTC")), svalbard)
    assert ctx['calculation_method'] == 'astral'
    assert ctx['period'] == 'day'
    assert ctx['is_astronomical_night'] is False
    # Only solar noon exists; the sun never crosses the horizon or twilight.
    assert {k for k, v in ctx['sun_times'].items() if v} == {'noon'}
    assert set(ctx['sun_times']) == {'dawn', 'sunrise', 'noon', 'sunset', 'dusk'}


def test_one_missing_event_keeps_the_others():
    # Tampere in June: the sun sets and rises but never reaches civil dusk.
    tampere = (61.5, 23.8, "Tampere")
    ctx = compute_time_context(datetime(2026, 6, 21, 9, 0, tzinfo=ZoneInfo("Europe/Helsinki")), tampere)
    assert ctx['sun_times']['dawn'] is None and ctx['sun_times']['dusk'] is None
    assert ctx['sun_times']['sunrise'] and ctx['sun_times']['noon'] and ctx['sun_times']['sunset']
    assert ctx['detailed_period'] == 'morning'


def test_period_edges_agree_with_astral_event_times():
    ctx = compute_time_context(datetime(2026, 6, 21, 12, 0, tzinfo=CHICAGO), TEXAS)
    rise = datetime.fromisoformat(ctx['sun_times']['sunrise'])
    assert compute_time_context(rise - timedelta(seconds=30), TEXAS)['period'] == 'twilight'
    assert compute_time_context(rise + timedelta(seconds=30), TEXAS)['period'] == 'day'


def test_no_location_falls_back_to_the_training_clock_band():
    def flag(hour):
        return compute_time_context(datetime(2026, 6, 21, hour, 0), (None, None, None))
    assert flag(23)['is_astronomical_night'] is True
    assert flag(4)['is_astronomical_night'] is True
    assert flag(5)['is_astronomical_night'] is False
    assert flag(21)['is_astronomical_night'] is False
    assert flag(12)['calculation_method'] == 'simple_hour_based'


def test_location_read_from_config_when_not_given(monkeypatch):
    monkeypatch.setattr(time_context, 'get_configured_location', lambda: TEXAS)
    ctx = compute_time_context(SUMMER_DUSK + timedelta(minutes=10))
    assert ctx['is_astronomical_night'] is True
    assert ctx['location']['name'] == "West Texas"


def test_astral_failure_falls_back_to_clock(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no ephemeris")
    monkeypatch.setattr(time_context, 'elevation', boom)
    ctx = compute_time_context(datetime(2026, 6, 21, 23, 0, tzinfo=CHICAGO), TEXAS)
    assert ctx['calculation_method'] == 'simple_hour_based'
    assert ctx['is_astronomical_night'] is True


# --- caching ----------------------------------------------------------------

def test_sun_times_computed_once_per_site_day(monkeypatch):
    calls = []
    real_noon = time_context.noon

    def counting_noon(observer, day, **k):
        calls.append(day)
        return real_noon(observer, day, **k)
    monkeypatch.setattr(time_context, 'noon', counting_noon)

    # The site day is the mean-solar day (UTC-7 at this longitude), so 03:00
    # CDT is already the 21st while 01:00 CDT would still be the 20th.
    for hour in (3, 12, 23):
        compute_time_context(datetime(2026, 6, 21, hour, 0, tzinfo=CHICAGO), TEXAS)
    compute_time_context(datetime(2026, 6, 22, 12, 0, tzinfo=CHICAGO), TEXAS)
    assert len(calls) == 2


def test_ui_controller_shim_re_exports_the_service():
    from ui.controllers import time_context as shim
    assert shim.compute_time_context is compute_time_context
