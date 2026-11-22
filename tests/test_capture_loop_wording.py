"""capture_loop — per-backend recovery wording and log levels, ``link_state``, and
``FrameSkipped``. A ZWO session (no ``recovery_wording``) must log exactly the
lines it always did; an Alpaca one uses network wording, logs its failures as
WARNING once each and keeps tracebacks of an outage at DEBUG."""
import logging
from unittest.mock import MagicMock

import pytest

from services.camera import capture_loop
from services.camera.alpaca.alpaca_protocol import AlpacaTransportError
from services.camera.errors import FrameSkipped
from services.camera.recovery_wording import NETWORK_WORDING, ZWO_WORDING, wording_for


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Session:
    def __init__(self, frames, reconnects, wording=None, max_reconnects=10):
        if wording is not None:
            self.recovery_wording = wording
        self.auto_recovery_enabled = True
        self._frames = list(frames)
        self._reconnects = list(reconnects)
        self._max_reconnects = max_reconnects
        self.reconnect_calls = 0
        self.lines = []
        self.errors = []
        self.states = []
        self.is_capturing = True
        self.scheduled_capture_mode = 'always'
        self.auto_exposure = False
        self.calibration_complete = True
        self.calibration_manager = None
        self.camera = MagicMock()
        self.camera.get_dropped_frames.return_value = 0
        self._connection = MagicMock()
        self.status_callback = None
        self.on_frame_callback = None
        self.effective_capture_interval = 1.0
        self.frames = 0

    def log(self, msg):
        self.lines.append(msg)

    def on_error_callback(self, msg, is_fatal=False):
        self.errors.append((msg, is_fatal))

    def is_within_scheduled_window(self):
        return True

    def consume_immediate_capture(self):
        return False

    def capture_single_frame(self):
        self.states.append(getattr(self, 'link_state', None))
        outcome = self._frames.pop(0) if self._frames else None
        if outcome is None:
            self.is_capturing = False
            raise Exception("script over")
        if isinstance(outcome, Exception):
            raise outcome
        self.frames += 1
        return MagicMock(), {'FILENAME': 'capture.png'}

    def reconnect_camera_safe(self):
        self.reconnect_calls += 1
        if self.reconnect_calls >= self._max_reconnects:
            self.is_capturing = False
        return self._reconnects.pop(0) if self._reconnects else False


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(capture_loop, 'time', _Clock())
    monkeypatch.setattr(capture_loop, 'capture_error', lambda *a, **k: None)
    return capture_loop.capture_loop


def test_zwo_wording_is_the_original_text():
    assert wording_for(object()) is ZWO_WORDING
    assert ZWO_WORDING.cold_open_absorb.format(error="Camera closed") == (
        "First frame after reconnect failed (Camera closed) — reopening silently "
        "(known ZWO cold-open quirk, no fault raised)")
    assert ZWO_WORDING.pre_reconnect_wait.format(seconds=8.0) == (
        "Waiting 8s before reconnection attempt (USB bus settle)...")
    assert ZWO_WORDING.post_reconnect_settle == "Waiting 3s for USB bus to stabilise..."
    assert ZWO_WORDING.levelled is False and ZWO_WORDING.quiet_traceback_types == ()


def test_zwo_session_logs_the_same_lines_through_its_callback(run):
    cam = _Session(frames=[True, Exception("USB gone"), Exception("Camera closed"), True],
                   reconnects=[True, True])
    run(cam)
    lines = cam.lines
    assert "✗ ERROR in capture loop: USB gone" in lines
    assert "Waiting 8s before reconnection attempt (USB bus settle)..." in lines
    assert "Waiting 3s for USB bus to stabilise..." in lines
    assert ("First frame after reconnect failed (Camera closed) — reopening silently "
            "(known ZWO cold-open quirk, no fault raised)") in lines
    assert any(line.startswith("Stack trace: Traceback") for line in lines)
    assert cam.errors[0] == ("Capture error: USB gone - attempting recovery...", False)


def test_alpaca_outage_logs_network_wording_at_warning_without_tracebacks(run, caplog):
    outage = AlpacaTransportError("Cannot reach the Alpaca server at 10.0.0.5:11111")
    cam = _Session(frames=[True, outage, Exception("Camera closed"), True],
                   reconnects=[False, True, True], wording=NETWORK_WORDING)
    with caplog.at_level(logging.DEBUG, logger="PFRSentinel"):
        run(cam)
    joined = "\n".join(cam.lines)
    assert "USB" not in joined and "ZWO" not in joined
    assert "Waiting 8s before reconnecting to the Alpaca server..." in cam.lines
    assert not any(line.startswith("Stack trace") for line in cam.lines)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings.count(f"✗ ERROR in capture loop: {outage}") == 1
    assert "✗ Reconnection attempt failed: Failed to reconnect camera" in warnings
    traces = [r for r in caplog.records if r.getMessage().startswith("Stack trace")]
    assert traces and all(r.levelno == logging.DEBUG for r in traces)


def test_a_bug_keeps_its_traceback_even_on_a_network_session(run):
    cam = _Session(frames=[KeyError("RES"), True], reconnects=[True],
                   wording=NETWORK_WORDING)
    run(cam)
    assert any(line.startswith("Stack trace: Traceback") for line in cam.lines)


def test_link_state_follows_the_loop(run):
    cam = _Session(frames=[True, Exception("gone"), True, True], reconnects=[False, True],
                   wording=NETWORK_WORDING)
    seen = []
    original = cam.reconnect_camera_safe

    def reconnect():
        seen.append(cam.link_state)
        return original()
    cam.reconnect_camera_safe = reconnect
    run(cam)
    assert cam.states[0] == 'ok'
    assert seen and set(seen) == {'reconnecting'}
    assert cam.states[2] == 'reconnecting'   # first frame on the new connection
    assert cam.states[3] == 'ok'


def test_long_retry_reads_as_waiting(run, monkeypatch):
    cam = _Session(frames=[Exception("gone")] * 6, reconnects=[False] * 6,
                   wording=NETWORK_WORDING)
    states = []
    clock = capture_loop.time
    original_sleep = clock.sleep

    def sleep(seconds):
        states.append(cam.link_state)
        if cam.link_state == 'waiting':
            cam.is_capturing = False
        original_sleep(seconds)
    clock.sleep = sleep
    run(cam)
    assert 'waiting' in states and cam.long_retry_mode_public is True


def test_initial_link_state_is_ok_on_every_session():
    from services.camera.capture_session_base import CaptureSessionBase
    assert CaptureSessionBase().link_state == 'ok'


def test_frame_skipped_is_not_an_error_and_never_reconnects(run):
    cam = _Session(frames=[FrameSkipped("camera busy — another client is exposing"),
                           FrameSkipped("camera busy — another client is exposing"), True],
                   reconnects=[], wording=NETWORK_WORDING)
    run(cam)
    assert cam.reconnect_calls == 0 and cam.frames == 1
    assert cam.lines.count("⏭ Frame skipped: camera busy — another client is exposing") == 1
    assert not [e for e in cam.errors if "busy" in e[0]]
