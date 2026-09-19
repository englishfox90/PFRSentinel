"""Config 'camera_auto_recovery' — the opt-out from the camera recovery ladder.

On (default): behaviour is unchanged. Off: a fault gets one plain reconnect with
no USB reset, SDK reset, retry loop, restart timer or app relaunch.
"""
from unittest.mock import MagicMock, patch

import pytest

from services.camera import camera_reconnect, zwo_capture_worker
from services.camera.camera_connection import CameraConnection
from services.config import DEFAULT_CONFIG


def test_default_config_keeps_recovery_on():
    assert DEFAULT_CONFIG['camera_auto_recovery'] is True


# --------------------------------------------------------------------------
# Connection layer
# --------------------------------------------------------------------------

def _connection(auto_recovery: bool):
    conn = MagicMock()
    conn.auto_recovery_enabled = auto_recovery
    conn.camera_name = 'ZWO ASI676MC'
    conn.camera_serial = None
    conn.detect_cameras.return_value = [{'index': 0, 'name': 'ZWO ASI676MC'}]
    conn._find_camera_index_by_name.return_value = 0
    return conn


class TestReconnectWithoutRecovery:
    def test_failed_open_is_not_escalated(self):
        conn = _connection(auto_recovery=False)
        conn.connect.return_value = False
        with patch.object(camera_reconnect, 'run_recovery_ladder') as ladder:
            assert camera_reconnect.reconnect_safe(conn, settings={}) is False
        ladder.assert_not_called()
        conn.reset_sdk_completely.assert_not_called()
        conn._usb_reset_func.assert_not_called()
        conn._usb_disable_enable_func.assert_not_called()
        assert conn.connect.call_count == 1

    def test_missing_camera_fails_without_the_ladder(self):
        conn = _connection(auto_recovery=False)
        conn._find_camera_index_by_name.return_value = None
        with patch.object(camera_reconnect, 'run_recovery_ladder') as ladder:
            assert camera_reconnect.reconnect_safe(conn, settings={}) is False
        ladder.assert_not_called()
        conn.connect.assert_not_called()

    def test_plain_reconnect_still_succeeds(self):
        conn = _connection(auto_recovery=False)
        conn.connect.return_value = True
        assert camera_reconnect.reconnect_safe(conn, settings={'gain': 1}) is True
        args, kwargs = conn.connect.call_args
        assert args == (0, {'gain': 1})
        assert kwargs['_skip_roi_usb_recovery'] is True

    def test_enabled_still_escalates_a_failed_open(self):
        conn = _connection(auto_recovery=True)
        conn.connect.return_value = False
        with patch.object(camera_reconnect, 'run_recovery_ladder',
                          return_value=(False, None, False)) as ladder:
            assert camera_reconnect.reconnect_safe(conn, settings={}) is False
        ladder.assert_called_once()
        assert ladder.call_args.kwargs['force_disable_enable'] is True


def test_connection_defaults_to_recovery_on():
    assert CameraConnection(sdk_path='').auto_recovery_enabled is True


def test_zwo_camera_flag_reaches_the_connection():
    from services.camera.zwo_camera import ZWOCamera
    cam = ZWOCamera(sdk_path='')
    assert cam.auto_recovery_enabled is True
    cam.auto_recovery_enabled = False
    assert cam._connection.auto_recovery_enabled is False


# --------------------------------------------------------------------------
# Capture loop
# --------------------------------------------------------------------------

class _Clock:
    """Stands in for the worker's `time` module so settle waits cost nothing."""

    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _FakeCamera:
    """Scripted camera: `frames` are per-call outcomes of capture_single_frame
    (an Exception to raise, or True for a good frame); `reconnects` likewise."""

    def __init__(self, auto_recovery, frames, reconnects, max_reconnects=10):
        self.auto_recovery_enabled = auto_recovery
        self._frames = list(frames)
        self._reconnects = list(reconnects)
        self._max_reconnects = max_reconnects
        self.reconnect_calls = 0
        self.errors = []
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

    def log(self, _msg):
        pass

    def on_error_callback(self, msg, is_fatal=False):
        self.errors.append((msg, is_fatal))

    def is_within_scheduled_window(self):
        return True

    def consume_immediate_capture(self):
        return False

    def capture_single_frame(self):
        outcome = self._frames.pop(0) if self._frames else Exception("Camera closed")
        if isinstance(outcome, Exception):
            raise outcome
        return MagicMock(), {'FILENAME': 'capture.png'}

    def reconnect_camera_safe(self):
        self.reconnect_calls += 1
        if self.reconnect_calls >= self._max_reconnects:
            self.is_capturing = False  # end an otherwise endless recovery-on run
        return self._reconnects.pop(0) if self._reconnects else False


@pytest.fixture
def run_loop(monkeypatch):
    monkeypatch.setattr(zwo_capture_worker, 'time', _Clock())
    monkeypatch.setattr(zwo_capture_worker, 'capture_error', lambda *a, **k: None)
    return zwo_capture_worker.capture_loop


class TestCaptureLoopWithRecoveryOff:
    def test_failed_reconnect_stops_capture_after_one_attempt(self, run_loop):
        cam = _FakeCamera(False, frames=[Exception("USB gone")], reconnects=[False])
        run_loop(cam)
        assert cam.reconnect_calls == 1
        assert cam.is_capturing is False
        msg, is_fatal = cam.errors[-1]
        assert is_fatal is True
        assert "automatic recovery is off" in msg

    def test_second_fault_after_a_good_reconnect_stops_capture(self, run_loop):
        cam = _FakeCamera(False, frames=[Exception("a"), Exception("b")],
                          reconnects=[True])
        run_loop(cam)
        assert cam.reconnect_calls == 1
        assert cam.errors[-1][1] is True

    def test_a_good_frame_renews_the_single_reconnect(self, run_loop):
        cam = _FakeCamera(
            False,
            frames=[Exception("a"), True, Exception("b"), Exception("c")],
            reconnects=[True, True],
        )
        run_loop(cam)
        assert cam.reconnect_calls == 2
        assert cam.errors[-1][1] is True


def test_capture_loop_keeps_retrying_with_recovery_on(run_loop):
    cam = _FakeCamera(True, frames=[], reconnects=[], max_reconnects=4)
    run_loop(cam)
    assert cam.reconnect_calls == 4
    assert not any(is_fatal for _msg, is_fatal in cam.errors)


# --------------------------------------------------------------------------
# Controller
# --------------------------------------------------------------------------

@pytest.fixture
def qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    yield QApplication.instance() or QApplication([])


def _controller(auto_recovery):
    from ui.controllers.camera_controller import CameraControllerQt
    values = {'camera_auto_recovery': auto_recovery}
    main_window = MagicMock()
    main_window.config.get = MagicMock(
        side_effect=lambda key, default=None: values.get(key, default))
    ctrl = CameraControllerQt(main_window)
    ctrl._start_usb_reset_worker = MagicMock()
    return ctrl


class TestControllerWithRecoveryOff:
    def test_fatal_error_schedules_no_restart(self, qt_app):
        ctrl = _controller(False)
        ctrl.zwo_camera = MagicMock()
        ctrl.is_capturing = True
        ctrl._on_camera_error("Camera reconnect failed", is_fatal=True)
        assert ctrl._auto_recovery_timer is None
        assert ctrl._auto_recovery_attempts == 0
        assert ctrl.recovery_state()['in_progress'] is False

    def test_corrupt_sdk_start_failure_triggers_no_usb_reset(self, qt_app):
        from ui.controllers.camera_controller import _UNRECOVERABLE_ERROR_PATTERNS
        ctrl = _controller(False)
        ctrl._last_successful_frame_ts = 123.0
        with patch('services.posthog_service.capture_error'):
            ctrl._on_capture_start_done(False, _UNRECOVERABLE_ERROR_PATTERNS[0])
        ctrl._start_usb_reset_worker.assert_not_called()
        assert ctrl._unrecoverable_mode is False
        assert ctrl._auto_recovery_timer is None

    def test_turning_it_off_cancels_a_pending_restart(self, qt_app):
        ctrl = _controller(True)
        ctrl.zwo_camera = MagicMock()
        ctrl._schedule_auto_recovery()
        assert ctrl._auto_recovery_timer is not None
        ctrl.set_auto_recovery_enabled(False)
        assert ctrl._auto_recovery_timer is None
        assert ctrl.zwo_camera.auto_recovery_enabled is False
        assert ctrl.recovery_state()['in_progress'] is False


def test_controller_still_schedules_restart_with_recovery_on(qt_app):
    ctrl = _controller(True)
    ctrl._schedule_auto_recovery()
    assert ctrl._auto_recovery_timer is not None
    ctrl._cancel_auto_recovery_timer()
