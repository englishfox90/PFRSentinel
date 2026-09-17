"""
Tests for services.timelapse_window — recording-window schedule maths.

Covers:
- T1: the sun window must be naive LOCAL wall-clock, not UTC with tzinfo
  merely stripped (which shifted the window by the host's UTC offset).
- Issue #14: astral clamps sunset/sunrise/dusk/dawn to the calendar date in
  ``tzinfo``, so asking for a UTC date returns the wrong night's events off the
  prime meridian. Real astral with an injected fixed-offset zone pins this down
  without depending on the CI host's own timezone.
- WindowCache: astral's solar geometry ran twice per captured frame; the cache
  must collapse that to one computation per day AND recompute when the window
  config changes.
"""
from datetime import date, datetime, timedelta, timezone

import astral.sun as astral_sun
import pytest

from services.timelapse_window import WindowCache, fixed_window, sun_window
from services.timelapse_writer import TimelapseWriter


_SUN_CONFIG = {
    'enabled': True, 'window_mode': 'sun', 'sun_mode': 'sunset_sunrise',
    'sun_latitude': 51.5, 'sun_longitude': 0.0,
}

_PLUS5 = timezone(timedelta(hours=5))
_FAKE_SUNSET = datetime(2026, 1, 1, 18, 0, tzinfo=_PLUS5)    # 13:00 UTC
_FAKE_SUNRISE = datetime(2026, 1, 2, 6, 0, tzinfo=_PLUS5)    # 01:00 UTC


def _patch_sun(monkeypatch, calls=None):
    """Stub astral's clamping event functions for the sunset_sunrise mode."""
    def fake_sunset(observer, date=None, tzinfo=None):
        if calls is not None:
            calls.append(date)
        return _FAKE_SUNSET

    def fake_sunrise(observer, date=None, tzinfo=None):
        if calls is not None:
            calls.append(date)
        return _FAKE_SUNRISE

    monkeypatch.setattr(astral_sun, 'sunset', fake_sunset)
    monkeypatch.setattr(astral_sun, 'sunrise', fake_sunrise)


def test_sun_window_converts_utc_to_local_naive(monkeypatch):
    """astral returns tz-aware datetimes; the window must be naive LOCAL
    wall-clock, not the instant with tzinfo merely stripped."""
    _patch_sun(monkeypatch)

    ws, we = sun_window(_SUN_CONFIG, date(2026, 1, 1))

    assert ws == _FAKE_SUNSET.astimezone().replace(tzinfo=None)
    assert we == _FAKE_SUNRISE.astimezone().replace(tzinfo=None)
    # On any host whose local offset isn't +5h, the buggy .replace(tzinfo=None)
    # (→ 18:00 naive) differs from the correct local instant.
    if datetime.now().astimezone().utcoffset() != timedelta(hours=5):
        assert ws != _FAKE_SUNSET.replace(tzinfo=None)


def test_sun_window_returns_naive_wall_clock_in_the_injected_zone(monkeypatch):
    """With an explicit tzinfo the result is naive wall-clock in THAT zone."""
    _patch_sun(monkeypatch)

    ws, we = sun_window(_SUN_CONFIG, date(2026, 1, 1), tzinfo=_PLUS5)

    assert ws == datetime(2026, 1, 1, 18, 0)
    assert we == datetime(2026, 1, 2, 6, 0)


def test_writer_delegates_sun_window(monkeypatch):
    """The writer's _sun_window delegate must keep working for existing callers."""
    _patch_sun(monkeypatch)
    writer = TimelapseWriter()
    writer.configure(dict(_SUN_CONFIG))

    assert writer._sun_window(date(2026, 1, 1)) == sun_window(_SUN_CONFIG, date(2026, 1, 1))


def test_cache_computes_the_sun_window_once_per_day(monkeypatch):
    """The per-frame window check ran astral twice per captured frame; the
    cache must reduce repeat checks for the same day to zero recomputation."""
    calls = []
    _patch_sun(monkeypatch, calls)
    cache = WindowCache()

    first = cache.window_for_day(_SUN_CONFIG, date(2026, 1, 1))
    n_after_first = len(calls)
    assert n_after_first > 0

    for _ in range(50):
        assert cache.window_for_day(_SUN_CONFIG, date(2026, 1, 1)) == first
    assert len(calls) == n_after_first          # no recomputation

    cache.window_for_day(_SUN_CONFIG, date(2026, 1, 2))
    assert len(calls) > n_after_first           # a new day does recompute


def test_cache_recomputes_when_window_config_changes(monkeypatch):
    """A settings edit (mode, sun mode, coordinates, fixed times) must not be
    served a stale window."""
    calls = []
    _patch_sun(monkeypatch, calls)
    cache = WindowCache()
    day = date(2026, 1, 1)

    fixed = dict(_SUN_CONFIG, window_mode='fixed', fixed_start='18:00', fixed_end='06:00')
    assert cache.window_for_day(fixed, day) == (
        datetime(2026, 1, 1, 18, 0), datetime(2026, 1, 2, 6, 0))

    edited = dict(fixed, fixed_start='20:00')
    assert cache.window_for_day(edited, day) == (
        datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 6, 0))

    cache.window_for_day(_SUN_CONFIG, day)
    n = len(calls)
    moved = dict(_SUN_CONFIG, sun_latitude=-33.9, sun_longitude=151.2)
    cache.window_for_day(moved, day)
    assert len(calls) > n            # relocating the site recomputes the window


# --- Issue #14: "date" must mean the LOCAL calendar date ---------------------

_DAY = date(2026, 9, 14)

# (id, latitude, longitude, utc_offset_hours, sun_mode)
_SITES = [
    ('amargosa_civil', 36.58, -116.60, -7, 'civil'),
    ('amargosa_sunset_sunrise', 36.58, -116.60, -7, 'sunset_sunrise'),
    ('honolulu_civil', 21.3, -157.8, -10, 'civil'),
    ('sydney_nautical', -33.9, 151.2, 10, 'nautical'),
    ('sydney_astronomical', -33.9, 151.2, 10, 'astronomical'),
    ('tokyo_sunset_sunrise', 35.7, 139.7, 9, 'sunset_sunrise'),
    ('london_astronomical', 51.5, -0.1, 0, 'astronomical'),
]


@pytest.mark.parametrize(
    'lat,lon,offset,sun_mode',
    [case[1:] for case in _SITES],
    ids=[case[0] for case in _SITES],
)
def test_sun_window_uses_local_calendar_date_west_of_utc(lat, lon, offset, sun_mode):
    """The window for `day` must be that day's local night: it starts on `day`
    after noon and ends before noon on `day + 1`, in every zone. Asking astral
    for a UTC date returned the previous evening's dusk west of UTC and a
    next-day end east of about UTC+8."""
    tz = timezone(timedelta(hours=offset))
    config = {
        'window_mode': 'sun', 'sun_mode': sun_mode,
        'sun_latitude': lat, 'sun_longitude': lon,
        'fixed_start': '18:00', 'fixed_end': '06:00',
    }

    start, end = sun_window(config, _DAY, tzinfo=tz)

    assert start.date() == _DAY
    assert start.hour >= 12
    assert end.date() == _DAY + timedelta(days=1)
    assert end.hour < 12
    assert timedelta(hours=6) <= end - start <= timedelta(hours=16)


def test_sun_window_falls_back_to_fixed_when_sun_never_reaches_depression():
    """High-latitude summer: astral raises rather than returning a time. The
    window must degrade to the configured fixed one, not propagate."""
    tz = timezone(timedelta(hours=2))
    config = {
        'window_mode': 'sun', 'sun_mode': 'astronomical',
        'sun_latitude': 69.0, 'sun_longitude': 18.9,     # Tromsø
        'fixed_start': '22:00', 'fixed_end': '04:00',
    }
    midsummer = date(2026, 6, 21)

    assert sun_window(config, midsummer, tzinfo=tz) == fixed_window(config, midsummer)
