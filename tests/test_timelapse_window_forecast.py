"""
Tests for services.timelapse_window_forecast — the projected recording window
shown on the Timelapse Status card (GitHub issue #14).

Sun-mode cases run real astral with an injected fixed-offset zone, as
test_timelapse_window does, so they don't depend on the CI host's timezone.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from services import timelapse_window_forecast as fc
from services.timelapse_window_forecast import describe_forecast, forecast_window

_UTC_MINUS_7 = timezone(timedelta(hours=-7))
_UTC_PLUS_2 = timezone(timedelta(hours=2))


def _fixed(start='18:00', end='06:00'):
    return {'window_mode': 'fixed', 'fixed_start': start, 'fixed_end': end}


def _sun(sun_mode, lat, lon):
    return {
        'window_mode': 'sun', 'sun_mode': sun_mode,
        'sun_latitude': lat, 'sun_longitude': lon,
        'fixed_start': '18:00', 'fixed_end': '06:00',
    }


# --------------------------------------------------------------------------- #
#  forecast_window
# --------------------------------------------------------------------------- #

def test_fixed_window_before_start_is_upcoming_today():
    now = datetime(2026, 9, 16, 14, 0)
    forecast = forecast_window(_fixed(), now)

    assert forecast.state == fc.UPCOMING
    assert forecast.start == datetime(2026, 9, 16, 18, 0)
    assert forecast.end == datetime(2026, 9, 17, 6, 0)
    assert forecast.note == ""


def test_fixed_overnight_window_after_midnight_is_yesterdays_active_window():
    now = datetime(2026, 9, 17, 2, 30)
    forecast = forecast_window(_fixed(), now)

    assert forecast.state == fc.ACTIVE
    assert forecast.start == datetime(2026, 9, 16, 18, 0)
    assert forecast.end == datetime(2026, 9, 17, 6, 0)


@pytest.mark.parametrize("now", [datetime(2026, 9, 16, 18, 0), datetime(2026, 9, 17, 6, 0)])
def test_window_edges_are_inclusive_like_the_writer(now):
    assert forecast_window(_fixed(), now).state == fc.ACTIVE


def test_window_already_over_today_projects_tomorrows():
    now = datetime(2026, 9, 16, 7, 0)
    forecast = forecast_window(_fixed('01:00', '05:00'), now)

    assert forecast.state == fc.UPCOMING
    assert forecast.start == datetime(2026, 9, 17, 1, 0)
    assert forecast.end == datetime(2026, 9, 17, 5, 0)


@pytest.mark.parametrize("mode, state", [("always", fc.ALWAYS), ("roof", fc.ROOF)])
def test_unschedulable_modes_have_no_times(mode, state):
    forecast = forecast_window({'window_mode': mode}, datetime(2026, 9, 16, 12, 0))

    assert forecast.state == state
    assert forecast.start is None and forecast.end is None


def test_sun_window_is_on_the_local_night_west_of_greenwich():
    """Amargosa Valley, UTC-7 — the reporter's site and zone in #14."""
    now = datetime(2026, 9, 14, 15, 0)
    forecast = forecast_window(_sun('civil', 36.64, -116.40), now, tzinfo=_UTC_MINUS_7)

    assert forecast.state == fc.UPCOMING
    assert forecast.note == ""
    assert forecast.start.date() == date(2026, 9, 14)
    assert forecast.end.date() == date(2026, 9, 15)
    assert 18 <= forecast.start.hour <= 19
    assert 5 <= forecast.end.hour <= 6


def test_deeper_twilight_gives_a_shorter_window():
    now = datetime(2026, 9, 14, 15, 0)
    spans = {
        mode: forecast_window(_sun(mode, 36.64, -116.40), now, tzinfo=_UTC_MINUS_7)
        for mode in ('sunset_sunrise', 'civil', 'nautical', 'astronomical')
    }

    durations = [f.end - f.start for f in spans.values()]
    assert durations == sorted(durations, reverse=True)
    assert spans['civil'].start < spans['nautical'].start < spans['astronomical'].start


def test_sun_mode_without_location_uses_fixed_times_and_says_so():
    cfg = _sun('nautical', None, None)
    forecast = forecast_window(cfg, datetime(2026, 9, 16, 12, 0))

    assert forecast.start == datetime(2026, 9, 16, 18, 0)
    assert "No location" in forecast.note


def test_sun_mode_when_twilight_never_happens_uses_fixed_times_and_says_so():
    """Tromsø in midsummer: the sun never gets 18° below the horizon."""
    cfg = _sun('astronomical', 69.65, 18.96)
    forecast = forecast_window(cfg, datetime(2026, 6, 21, 12, 0), tzinfo=_UTC_PLUS_2)

    assert forecast.start == datetime(2026, 6, 21, 18, 0)
    assert "Astronomical twilight doesn't happen" in forecast.note


def test_sun_forecast_does_not_log(monkeypatch):
    """Polled every 5 s — the writer's fallback warning must not fire from here."""
    logged = []
    monkeypatch.setattr(fc.timelapse_window.app_logger, 'warning', logged.append)

    forecast_window(_sun('astronomical', None, None), datetime(2026, 9, 16, 12, 0))
    forecast_window(_sun('astronomical', 69.65, 18.96), datetime(2026, 6, 21, 12, 0), tzinfo=_UTC_PLUS_2)

    assert logged == []


@pytest.mark.parametrize("lat, lon", [(95, 0), ("abc", 0), (51.5, 200)])
def test_sun_mode_with_a_bad_coordinate_is_not_blamed_on_the_night(lat, lon):
    """A hand-edited latitude of 95 is a config error, not a polar night."""
    cfg = _sun('astronomical', lat, lon)
    forecast = forecast_window(cfg, datetime(2026, 9, 16, 12, 0), tzinfo=_UTC_PLUS_2)

    assert forecast.start == datetime(2026, 9, 16, 18, 0)
    assert "doesn't happen" not in forecast.note
    assert "isn't a valid latitude/longitude" in forecast.note


def test_24_hour_fixed_window_picks_todays_anchor_like_the_writer():
    now = datetime(2026, 9, 16, 18, 0)
    forecast = forecast_window(_fixed('18:00', '18:00'), now)

    assert forecast.state == fc.ACTIVE
    assert forecast.start == now


# --------------------------------------------------------------------------- #
#  describe_forecast
# --------------------------------------------------------------------------- #

def test_describe_upcoming_window():
    now = datetime(2026, 9, 16, 14, 0)
    text = describe_forecast(forecast_window(_fixed(), now), now)

    assert text == "Next window: today 18:00 → 06:00 (12h 00m)  ·  opens in 4h 00m"


def test_describe_upcoming_window_tomorrow():
    now = datetime(2026, 9, 16, 7, 0)
    text = describe_forecast(forecast_window(_fixed('01:00', '05:00'), now), now)

    assert text.startswith("Next window: tomorrow 01:00 → 05:00 (4h 00m)")
    assert text.endswith("opens in 18h 00m")


def test_describe_open_window_while_recording():
    now = datetime(2026, 9, 17, 5, 15)
    text = describe_forecast(forecast_window(_fixed(), now), now, recording=True)

    assert text == "Window open: 18:00 → 06:00 (12h 00m)  ·  closes in 45m"


def test_describe_open_window_not_recording_explains_capture_is_needed():
    now = datetime(2026, 9, 17, 2, 0)
    text = describe_forecast(forecast_window(_fixed(), now), now, enabled=True, recording=False)

    assert "capture must be running" in text


def test_describe_open_window_disabled_has_no_capture_hint():
    now = datetime(2026, 9, 17, 2, 0)
    text = describe_forecast(forecast_window(_fixed(), now), now, enabled=False, recording=False)

    assert "capture" not in text


def test_describe_appends_fallback_note():
    now = datetime(2026, 9, 16, 12, 0)
    text = describe_forecast(forecast_window(_sun('civil', None, None), now), now)

    assert text.splitlines()[1].startswith("No location in Weather settings")


@pytest.mark.parametrize("state, fragment", [
    (fc.ALWAYS, "always on"),
    (fc.ROOF, "roof state"),
    (fc.UNAVAILABLE, "couldn't be worked out"),
])
def test_describe_states_without_times(state, fragment):
    assert fragment in describe_forecast(fc.WindowForecast(state))


def test_describe_counts_the_extra_hour_on_a_clock_change_night():
    """US clocks go back at 02:00 on 1 Nov 2026: 20:00 → 06:00 lasts 11 h, not 10."""
    try:
        chicago = ZoneInfo("America/Chicago")
    except ZoneInfoNotFoundError:
        pytest.skip("no IANA tz database on this host")
    now = datetime(2026, 10, 31, 19, 0)
    forecast = forecast_window(_fixed('20:00', '06:00'), now)

    text = describe_forecast(forecast, now, tzinfo=chicago)

    assert "20:00 → 06:00 (11h 00m)" in text
    assert text.endswith("opens in 1h 00m")
