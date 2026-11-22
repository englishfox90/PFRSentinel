"""CameraControllerQt teardown of a camera it is about to drop.

ZWOCamera has no ``disconnect()``; the controller used to call it, swallow the
AttributeError, and drop its reference while the capture loop kept running
with the USB handle open (issue #43 plan, §1.11 item 1).
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from services.camera.zwo_camera import ZWOCamera


@pytest.fixture
def qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    yield QApplication.instance() or QApplication([])


def _controller():
    from ui.controllers.camera_controller import CameraControllerQt
    main_window = MagicMock()
    main_window.config.get = MagicMock(side_effect=lambda key, default=None: default)
    return CameraControllerQt(main_window)


def _session():
    cam = MagicMock(spec=ZWOCamera)
    cam.capture_thread = None
    return cam


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_stop_pressed_while_connecting_stops_the_running_loop(qt_app):
    ctrl = _controller()
    cam = _session()
    ctrl.zwo_camera = cam
    ctrl._user_requested_stop = True

    ctrl._on_capture_start_done(True, "")

    assert _wait_for(lambda: cam.disconnect_camera.called)
    cam.stop_capture.assert_called_once()
    assert cam.method_calls.index(('stop_capture', (), {})) < \
        cam.method_calls.index(('disconnect_camera', (), {}))
    assert ctrl.zwo_camera is None
    assert ctrl.is_connected is False
    assert ctrl.is_capturing is False


def test_late_stop_keeps_a_wedged_session_for_the_next_start(qt_app):
    ctrl = _controller()
    cam = _session()
    alive = threading.Event()
    worker = threading.Thread(target=alive.wait, daemon=True)
    worker.start()
    cam.capture_thread = worker
    ctrl.zwo_camera = cam
    ctrl._user_requested_stop = True

    try:
        ctrl._on_capture_start_done(True, "")
        assert _wait_for(lambda: cam.disconnect_camera.called)
        assert ctrl._dying_camera is cam
    finally:
        alive.set()


def test_start_tears_down_a_leftover_session_before_connecting(qt_app):
    ctrl = _controller()
    stale = _session()
    ctrl.zwo_camera = stale
    ctrl.is_connected = True
    order = []
    stale.disconnect_camera.side_effect = lambda: order.append('disconnect')

    def resolve(*_args):
        order.append('resolve')
        raise Exception("no camera")

    ctrl._resolve_camera_index = resolve

    ctrl.start_capture()

    assert ctrl.zwo_camera is None
    assert _wait_for(lambda: 'resolve' in order)
    stale.stop_capture.assert_called_once()
    assert order == ['disconnect', 'resolve']
