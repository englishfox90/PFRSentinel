"""
Tests for the capture-schedule gate wired into ZWOCamera.

The camera is constructed without hardware (no SDK init, no connect), which is
enough to exercise the pure schedule decisions.
"""
from datetime import datetime, timedelta

import pytest

from services.camera.zwo_camera import ZWOCamera
from services.capture_schedule_window import CaptureWindowGate, gate_for_config


def _camera(mode="gated", start="17:00", end="09:00"):
    return ZWOCamera(
        scheduled_capture_mode=mode,
        scheduled_start_time=start,
        scheduled_end_time=end,
    )


def _times_around_now(inside: bool):
    """(start, end) HH:MM strings that bracket — or miss — the current clock."""
    now = datetime.now()
    if inside:
        return ((now - timedelta(hours=1)).strftime('%H:%M'),
                (now + timedelta(hours=1)).strftime('%H:%M'))
    return ((now + timedelta(hours=1)).strftime('%H:%M'),
            (now + timedelta(hours=2)).strftime('%H:%M'))


class _StubGate:
    """Callable gate with a describe(), like CaptureWindowGate."""

    def __init__(self, allowed=True, label="stub window", raises=False):
        self.allowed = allowed
        self.label = label
        self.raises = raises
        self.calls = 0

    def __call__(self, now=None):
        self.calls += 1
        if self.raises:
            raise RuntimeError("gate exploded")
        return self.allowed

    def describe(self, now=None):
        if self.raises:
            raise RuntimeError("describe exploded")
        return self.label


# --------------------------------------------------------------------------- #
#  default state                                                                #
# --------------------------------------------------------------------------- #

def test_schedule_gate_defaults_to_none():
    assert _camera().schedule_gate is None


# --------------------------------------------------------------------------- #
#  is_within_scheduled_window                                                   #
# --------------------------------------------------------------------------- #

def test_gated_mode_uses_gate_when_set():
    start, end = _times_around_now(inside=True)
    cam = _camera(start=start, end=end)
    cam.schedule_gate = _StubGate(allowed=False)

    assert cam.is_within_scheduled_window() is False
    assert cam.schedule_gate.calls == 1


def test_gated_mode_falls_back_to_legacy_window_without_gate():
    inside_start, inside_end = _times_around_now(inside=True)
    assert _camera(start=inside_start, end=inside_end).is_within_scheduled_window() is True

    out_start, out_end = _times_around_now(inside=False)
    assert _camera(start=out_start, end=out_end).is_within_scheduled_window() is False


def test_non_gated_modes_ignore_the_gate():
    for mode in ("always", "variable"):
        cam = _camera(mode=mode)
        cam.schedule_gate = _StubGate(allowed=False)

        assert cam.is_within_scheduled_window() is True
        assert cam.schedule_gate.calls == 0


def test_gate_exception_fails_open():
    out_start, out_end = _times_around_now(inside=False)
    cam = _camera(start=out_start, end=out_end)
    cam.schedule_gate = _StubGate(raises=True)

    assert cam.is_within_scheduled_window() is True


# --------------------------------------------------------------------------- #
#  is_in_time_window / effective interval                                       #
# --------------------------------------------------------------------------- #

def test_is_in_time_window_uses_gate_regardless_of_mode():
    start, end = _times_around_now(inside=True)
    cam = _camera(mode="variable", start=start, end=end)
    cam.schedule_gate = _StubGate(allowed=False)

    assert cam.is_in_time_window() is False


def test_is_in_time_window_falls_back_to_legacy_without_gate():
    start, end = _times_around_now(inside=True)
    assert _camera(mode="variable", start=start, end=end).is_in_time_window() is True


def test_variable_interval_follows_the_gate():
    start, end = _times_around_now(inside=False)
    cam = _camera(mode="variable", start=start, end=end)
    cam.set_capture_interval(60.0)
    cam.scheduled_window_interval = 5.0
    cam.schedule_gate = _StubGate(allowed=True)

    assert cam.effective_capture_interval == 5.0


# --------------------------------------------------------------------------- #
#  scheduled_window_label                                                       #
# --------------------------------------------------------------------------- #

def test_label_without_gate_is_the_configured_span():
    assert _camera(start="17:00", end="09:00").scheduled_window_label() == "17:00 - 09:00"


def test_label_with_gate_uses_describe():
    cam = _camera()
    cam.schedule_gate = _StubGate(label="timelapse window ±15 min (19:36 - 05:47)")

    assert cam.scheduled_window_label() == "timelapse window ±15 min (19:36 - 05:47)"


def test_label_falls_back_when_describe_raises():
    cam = _camera(start="17:00", end="09:00")
    cam.schedule_gate = _StubGate(raises=True)

    assert cam.scheduled_window_label() == "17:00 - 09:00"


# --------------------------------------------------------------------------- #
#  gate_for_config                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cfg", [
    {},
    {'scheduled_window_source': 'fixed'},
    {'scheduled_window_source': 'something-else'},
])
def test_gate_for_config_returns_none_for_non_timelapse_sources(cfg):
    assert gate_for_config(cfg) is None


def test_gate_for_config_returns_gate_for_timelapse_source():
    cfg = {
        'scheduled_window_source': 'timelapse',
        'scheduled_window_margin_min': 15,
        'timelapse': {'window_mode': 'fixed', 'fixed_start': '18:00', 'fixed_end': '06:00'},
        'weather': {},
    }

    gate = gate_for_config(cfg)

    assert isinstance(gate, CaptureWindowGate)
    assert isinstance(gate(), bool)
    assert "timelapse window" in gate.describe()


def test_gate_for_config_reads_the_live_config_object():
    """The gate must follow later edits — it holds the config, not a snapshot."""
    cfg = {
        'scheduled_window_source': 'timelapse',
        'scheduled_window_margin_min': 15,
        'timelapse': {'window_mode': 'always'},
        'weather': {},
    }
    gate = gate_for_config(cfg)
    assert gate.describe() == "timelapse window (always on)"

    cfg['timelapse'] = {'window_mode': 'fixed', 'fixed_start': '18:00', 'fixed_end': '06:00'}

    assert "17:45 - 06:15" in gate.describe()
