"""
Tests for services.capture_schedule_window — capture gating that follows the
timelapse recording window.

Config is passed as plain dicts: the module's contract is "anything with
.get(key, default)", and the rules say not to mock services.config.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from services import capture_schedule_window as csw
from services import timelapse_window


def _config(**overrides):
    """A config shaped like DEFAULT_CONFIG's relevant slices."""
    cfg = {
        'scheduled_window_source': 'timelapse',
        'scheduled_window_margin_min': 15,
        'scheduled_start_time': '16:15',
        'scheduled_end_time': '07:30',
        'timelapse': {
            'window_mode': 'fixed',
            'sun_mode': 'astronomical',
            'fixed_start': '18:00',
            'fixed_end': '06:00',
        },
        'weather': {'latitude': '', 'longitude': ''},
    }
    for key, value in overrides.items():
        if key in ('timelapse', 'weather'):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    return cfg


DAY = date(2026, 3, 11)


# --------------------------------------------------------------------------- #
#  timelapse_window_config                                                      #
# --------------------------------------------------------------------------- #

def test_timelapse_window_config_injects_weather_coordinates():
    cfg = _config(weather={'latitude': 51.5, 'longitude': -0.12})

    tl = csw.timelapse_window_config(cfg)

    assert tl['window_mode'] == 'fixed'
    assert tl['sun_latitude'] == 51.5
    assert tl['sun_longitude'] == -0.12


def test_timelapse_window_config_maps_falsy_coordinates_to_none():
    """Empty strings / 0 must become None — sun_window tests `is None`."""
    tl = csw.timelapse_window_config(_config(weather={'latitude': '', 'longitude': 0}))

    assert tl['sun_latitude'] is None
    assert tl['sun_longitude'] is None


def test_timelapse_window_config_does_not_mutate_the_source_section():
    cfg = _config()

    csw.timelapse_window_config(cfg)

    assert 'sun_latitude' not in cfg['timelapse']


# --------------------------------------------------------------------------- #
#  capture_window_for_day                                                       #
# --------------------------------------------------------------------------- #

def test_fixed_timelapse_window_widened_by_margin():
    start, end = csw.capture_window_for_day(_config(), DAY)

    assert start == datetime(2026, 3, 11, 17, 45)
    assert end == datetime(2026, 3, 12, 6, 15)


def test_zero_margin_leaves_the_timelapse_window_unchanged():
    start, end = csw.capture_window_for_day(_config(scheduled_window_margin_min=0), DAY)

    assert start == datetime(2026, 3, 11, 18, 0)
    assert end == datetime(2026, 3, 12, 6, 0)


def test_always_mode_returns_none():
    cfg = _config(timelapse={'window_mode': 'always'})

    assert csw.capture_window_for_day(cfg, DAY) is None


def test_always_mode_gate_allows_capture_at_any_time():
    gate = csw.CaptureWindowGate(_config(timelapse={'window_mode': 'always'}))

    assert gate(datetime(2026, 3, 11, 3, 0)) is True
    assert gate(datetime(2026, 3, 11, 13, 0)) is True
    assert 'always on' in gate.describe(datetime(2026, 3, 11, 13, 0))


def test_fixed_source_ignores_the_timelapse_window():
    cfg = _config(scheduled_window_source='fixed')

    start, end = csw.capture_window_for_day(cfg, DAY)

    assert start == datetime(2026, 3, 11, 16, 15)
    assert end == datetime(2026, 3, 12, 7, 30)


# --------------------------------------------------------------------------- #
#  roof fallback                                                                #
# --------------------------------------------------------------------------- #

@pytest.fixture
def roof_warnings(monkeypatch):
    """Count app_logger.warning calls with the once-per-process latch reset."""
    calls = []
    monkeypatch.setattr(csw, '_roof_warning_logged', False, raising=False)
    monkeypatch.setattr(csw.app_logger, 'warning', lambda msg, *a, **k: calls.append(msg))
    return calls


def test_roof_mode_falls_back_to_the_fixed_scheduled_times(roof_warnings):
    cfg = _config(timelapse={'window_mode': 'roof'})

    start, end = csw.capture_window_for_day(cfg, DAY)

    # Fixed scheduled times, no margin applied.
    assert start == datetime(2026, 3, 11, 16, 15)
    assert end == datetime(2026, 3, 12, 7, 30)


def test_roof_mode_warns_only_once_per_process(roof_warnings):
    cfg = _config(timelapse={'window_mode': 'roof'})

    csw.capture_window_for_day(cfg, DAY)
    csw.capture_window_for_day(cfg, DAY + timedelta(days=1))

    assert len(roof_warnings) == 1
    assert 'roof' in roof_warnings[0].lower()


def test_roof_mode_describe_explains_the_fallback(roof_warnings):
    gate = csw.CaptureWindowGate(_config(timelapse={'window_mode': 'roof'}))

    label = gate.describe(datetime(2026, 3, 11, 20, 0))

    assert label == "fixed 16:15 - 07:30 (timelapse roof mode can't be scheduled)"


# --------------------------------------------------------------------------- #
#  sun mode delegation                                                          #
# --------------------------------------------------------------------------- #

def test_sun_mode_applies_margin_and_passes_tzinfo(monkeypatch):
    seen = {}

    def fake_window_for_day(config, day, tzinfo=None):
        seen['config'] = config
        seen['day'] = day
        seen['tzinfo'] = tzinfo
        return datetime(2026, 3, 11, 19, 36), datetime(2026, 3, 12, 5, 47)

    monkeypatch.setattr(timelapse_window, 'window_for_day', fake_window_for_day)
    cfg = _config(timelapse={'window_mode': 'sun', 'sun_mode': 'civil'},
                  weather={'latitude': 36.58, 'longitude': -116.60})
    tz = timezone(timedelta(hours=-7))

    start, end = csw.capture_window_for_day(cfg, DAY, tzinfo=tz)

    assert start == datetime(2026, 3, 11, 19, 21)
    assert end == datetime(2026, 3, 12, 6, 2)
    assert seen['tzinfo'] is tz
    assert seen['day'] == DAY
    assert seen['config']['sun_latitude'] == 36.58


def test_sun_mode_describe_labels_the_margin(monkeypatch):
    monkeypatch.setattr(
        timelapse_window, 'window_for_day',
        lambda config, day, tzinfo=None: (datetime.combine(day, datetime.min.time()) + timedelta(hours=19, minutes=36),
                                          datetime.combine(day, datetime.min.time()) + timedelta(days=1, hours=5, minutes=47)))
    gate = csw.CaptureWindowGate(_config(timelapse={'window_mode': 'sun'}))

    assert gate.describe(datetime(2026, 3, 11, 22, 0)) == 'timelapse window ±15 min (19:21 - 06:02)'


# --------------------------------------------------------------------------- #
#  CaptureWindowGate                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('moment, expected', [
    (datetime(2026, 3, 11, 17, 44), False),
    (datetime(2026, 3, 11, 17, 46), True),
    (datetime(2026, 3, 11, 2, 0), True),      # yesterday's overnight window
    (datetime(2026, 3, 11, 6, 14), True),
    (datetime(2026, 3, 11, 6, 16), False),
    (datetime(2026, 3, 11, 12, 0), False),
])
def test_gate_spans_two_days_for_overnight_windows(moment, expected):
    gate = csw.CaptureWindowGate(_config())

    assert gate(moment) is expected


@pytest.mark.parametrize('moment, expected', [
    (datetime(2026, 3, 11, 23, 44), False),
    (datetime(2026, 3, 11, 23, 46), True),     # tomorrow's window, pulled back by the margin
    (datetime(2026, 3, 12, 0, 5), True),
    (datetime(2026, 3, 12, 6, 14), True),
    (datetime(2026, 3, 12, 6, 16), False),
])
def test_gate_finds_a_window_whose_margin_crosses_back_over_midnight(moment, expected):
    """A timelapse window starting at 00:00 with a 15 min margin opens at
    23:45 the previous evening, anchored on TOMORROW's day — the camera must
    reconnect then, not at midnight with no settle time."""
    cfg = _config()
    cfg['timelapse']['fixed_start'] = '00:00'
    cfg['timelapse']['fixed_end'] = '06:00'
    gate = csw.CaptureWindowGate(cfg)

    assert gate(moment) is expected


def test_active_window_reports_the_margin_pulled_window_before_midnight():
    cfg = _config()
    cfg['timelapse']['fixed_start'] = '00:00'
    cfg['timelapse']['fixed_end'] = '06:00'
    gate = csw.CaptureWindowGate(cfg)

    assert gate.active_window(datetime(2026, 3, 11, 23, 50)) == (
        datetime(2026, 3, 11, 23, 45), datetime(2026, 3, 12, 6, 15)
    )


def test_gate_recomputes_when_the_timelapse_window_changes():
    cfg = _config()
    gate = csw.CaptureWindowGate(cfg)
    moment = datetime(2026, 3, 11, 16, 30)

    assert gate(moment) is False

    cfg['timelapse']['fixed_start'] = '16:00'

    assert gate(moment) is True


def test_gate_recomputes_when_the_margin_changes():
    cfg = _config(scheduled_window_margin_min=0)
    gate = csw.CaptureWindowGate(cfg)
    moment = datetime(2026, 3, 11, 17, 50)

    assert gate(moment) is False

    cfg['scheduled_window_margin_min'] = 15

    assert gate(moment) is True


def test_gate_cache_stays_bounded():
    gate = csw.CaptureWindowGate(_config())

    for offset in range(40):
        gate(datetime(2026, 3, 11, 20, 0) + timedelta(days=offset))

    assert len(gate._cache) <= 8


def test_gate_is_thread_safe_under_concurrent_calls():
    import threading

    gate = csw.CaptureWindowGate(_config())
    results = []

    def hammer():
        for offset in range(50):
            results.append(gate(datetime(2026, 3, 11, 20, 0) + timedelta(days=offset)))

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 200
    assert all(results)


# --------------------------------------------------------------------------- #
#  describe_capture_window                                                      #
# --------------------------------------------------------------------------- #

def test_describe_capture_window_for_fixed_source():
    cfg = _config(scheduled_window_source='fixed')

    assert csw.describe_capture_window(cfg, datetime(2026, 3, 11, 20, 0)) == '16:15 - 07:30'


def test_describe_capture_window_matches_the_gate():
    cfg = _config()
    now = datetime(2026, 3, 11, 20, 0)

    assert csw.describe_capture_window(cfg, now) == csw.CaptureWindowGate(cfg).describe(now)
    assert csw.describe_capture_window(cfg, now) == 'timelapse window ±15 min (17:45 - 06:15)'


def test_describe_uses_the_window_containing_now():
    """At 02:00 the active window is yesterday's, not the one starting tonight."""
    cfg = _config()
    cfg['timelapse']['fixed_start'] = '18:00'

    label = csw.describe_capture_window(cfg, datetime(2026, 3, 11, 2, 0))

    assert label == 'timelapse window ±15 min (17:45 - 06:15)'


def test_describe_omits_the_margin_when_it_is_zero():
    cfg = _config(scheduled_window_margin_min=0)

    assert csw.describe_capture_window(cfg, datetime(2026, 3, 11, 20, 0)) == \
        'timelapse window (18:00 - 06:00)'


# --------------------------------------------------------------------------- #
#  margin handling                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw, expected', [
    (15, 15),
    (0, 0),
    ('30', 30),
    (45.7, 45),
    (-5, 0),
    (999, 180),
    ('abc', csw.DEFAULT_MARGIN_MINUTES),
    (None, csw.DEFAULT_MARGIN_MINUTES),
])
def test_margin_minutes_clamps_and_defaults(raw, expected):
    assert csw.margin_minutes(_config(scheduled_window_margin_min=raw)) == expected


def test_missing_margin_key_uses_the_default():
    cfg = _config()
    del cfg['scheduled_window_margin_min']

    assert csw.margin_minutes(cfg) == csw.DEFAULT_MARGIN_MINUTES


def test_margin_clamp_is_applied_to_the_window():
    start, end = csw.capture_window_for_day(_config(scheduled_window_margin_min=999), DAY)

    assert start == datetime(2026, 3, 11, 15, 0)   # 18:00 - 180 min
    assert end == datetime(2026, 3, 12, 9, 0)      # 06:00 + 180 min


def test_source_constants():
    assert csw.SOURCE_FIXED == 'fixed'
    assert csw.SOURCE_TIMELAPSE == 'timelapse'
    assert csw.DEFAULT_MARGIN_MINUTES == 15


# --------------------------------------------------------------------------- #
#  real astral                                                                  #
# --------------------------------------------------------------------------- #

def test_real_sun_window_is_anchored_on_the_requested_day():
    """Death Valley, civil dusk→dawn, UTC-7: the window must start the evening
    OF 2026-09-14 and end the morning of the 15th — not slip a day back."""
    cfg = _config(
        timelapse={'window_mode': 'sun', 'sun_mode': 'civil'},
        weather={'latitude': 36.58, 'longitude': -116.60},
    )

    start, end = csw.capture_window_for_day(
        cfg, date(2026, 9, 14), tzinfo=timezone(timedelta(hours=-7)))

    assert start.date() == date(2026, 9, 14)
    assert start.hour >= 12
    assert end.date() == date(2026, 9, 15)
    assert end.hour < 12
