"""
Tests for api_status.build_schedule — the single builder behind the ``schedule``
block of /status, shared by the GUI feeder and the headless runner.

Config is a plain dict: the builder's contract is "anything with .get()".
"""
from datetime import datetime, timedelta

from services import api_status


class _StubCamera:
    def __init__(self, in_window=True, label="17:00 - 09:00"):
        self._in_window = in_window
        self._label = label

    def is_in_time_window(self):
        return self._in_window

    def scheduled_window_label(self):
        return self._label


def _fixed_config(**overrides):
    cfg = {
        'scheduled_capture_mode': 'gated',
        'scheduled_window_source': 'fixed',
        'scheduled_start_time': '17:00',
        'scheduled_end_time': '09:00',
        'scheduled_window_interval': 5.0,
    }
    cfg.update(overrides)
    return cfg


def _timelapse_config(**overrides):
    cfg = _fixed_config(
        scheduled_window_source='timelapse',
        scheduled_window_margin_min=15,
    )
    cfg['timelapse'] = {'window_mode': 'fixed', 'fixed_start': '18:00', 'fixed_end': '06:00'}
    cfg['weather'] = {}
    cfg.update(overrides)
    return cfg


# --------------------------------------------------------------------------- #
#  fixed source                                                                 #
# --------------------------------------------------------------------------- #

def test_fixed_source_reports_configured_times():
    schedule = api_status.build_schedule(_fixed_config(), _StubCamera(in_window=False))

    assert schedule["mode"] == "gated"
    assert schedule["start_time"] == "17:00"
    assert schedule["end_time"] == "09:00"
    assert schedule["in_window"] is False
    assert schedule["window_interval_seconds"] is None
    assert schedule["source"] == "fixed"
    assert schedule["window"] == "17:00 - 09:00"


def test_always_mode_is_always_in_window():
    schedule = api_status.build_schedule(
        _fixed_config(scheduled_capture_mode='always'), _StubCamera(in_window=False)
    )

    assert schedule["mode"] == "always"
    assert schedule["in_window"] is True


def test_variable_mode_reports_the_window_interval():
    schedule = api_status.build_schedule(
        _fixed_config(scheduled_capture_mode='variable', scheduled_window_interval=2.5),
        _StubCamera(),
    )

    assert schedule["window_interval_seconds"] == 2.5


def test_camera_error_leaves_in_window_true():
    class _Broken(_StubCamera):
        def is_in_time_window(self):
            raise RuntimeError("camera gone")

    assert api_status.build_schedule(_fixed_config(), _Broken())["in_window"] is True


# --------------------------------------------------------------------------- #
#  timelapse source                                                             #
# --------------------------------------------------------------------------- #

def test_timelapse_source_reports_the_margined_window_times():
    schedule = api_status.build_schedule(_timelapse_config(), _StubCamera())

    assert schedule["source"] == "timelapse"
    assert schedule["start_time"] == "17:45"
    assert schedule["end_time"] == "06:15"


def test_timelapse_window_label_without_camera():
    schedule = api_status.build_schedule(_timelapse_config(), None)

    assert "timelapse window ±15 min" in schedule["window"]
    assert "17:45 - 06:15" in schedule["window"]


def test_timelapse_always_on_keeps_configured_times():
    cfg = _timelapse_config()
    cfg['timelapse'] = {'window_mode': 'always'}

    schedule = api_status.build_schedule(cfg, None)

    assert schedule["start_time"] == "17:00"
    assert schedule["end_time"] == "09:00"
    assert schedule["window"] == "timelapse window (always on)"
    assert schedule["in_window"] is True


def test_camera_label_wins_over_the_config_description():
    schedule = api_status.build_schedule(_timelapse_config(), _StubCamera(label="live label"))

    assert schedule["window"] == "live label"


# --------------------------------------------------------------------------- #
#  no camera                                                                    #
# --------------------------------------------------------------------------- #

def test_no_camera_still_reports_the_schedule():
    now = datetime.now()
    cfg = _fixed_config(
        scheduled_start_time=(now - timedelta(hours=1)).strftime('%H:%M'),
        scheduled_end_time=(now + timedelta(hours=1)).strftime('%H:%M'),
    )

    schedule = api_status.build_schedule(cfg, None)

    assert schedule["mode"] == "gated"
    assert schedule["in_window"] is True
    assert schedule["window"] == f"{cfg['scheduled_start_time']} - {cfg['scheduled_end_time']}"


def test_no_camera_outside_the_fixed_window():
    now = datetime.now()
    cfg = _fixed_config(
        scheduled_start_time=(now + timedelta(hours=1)).strftime('%H:%M'),
        scheduled_end_time=(now + timedelta(hours=2)).strftime('%H:%M'),
    )

    assert api_status.build_schedule(cfg, None)["in_window"] is False


def test_keys_are_exactly_the_documented_set():
    schedule = api_status.build_schedule(_fixed_config(), _StubCamera())

    assert set(schedule) == {
        "mode", "start_time", "end_time", "in_window",
        "window_interval_seconds", "source", "window",
    }
