"""A capture-loop error reaches the log once (QA, issue #43): the camera
controller logs it and the main window's handler no longer repeats it. ZWO keeps
"Camera error: …" at ERROR; an Alpaca loop still retrying a network outage logs
it at WARNING; errors the controller never logged (SDK missing, unrecoverable
mode) are still logged by the window."""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.config import Config


@pytest.fixture
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _controller(tmp_path, session_backend):
    from ui.controllers.camera_controller import CameraControllerQt
    main_window = MagicMock()
    main_window.config = Config(str(tmp_path / "config.json"))
    ctrl = CameraControllerQt(main_window)
    ctrl.zwo_camera = SimpleNamespace(backend=session_backend)
    return ctrl


def _window(ctrl):
    from ui.main_window.capture import _MainWindowCaptureMixin

    class _Window(_MainWindowCaptureMixin):
        pass

    w = _Window()
    w.__dict__.update(camera_controller=ctrl, app_bar=MagicMock(), _notify=MagicMock(),
                      _set_capture_error=MagicMock(), push_capture_status=MagicMock(),
                      _send_discord_error=MagicMock())
    ctrl.error.connect(w._on_camera_error)
    return w


def _lines(caplog, text):
    return [(r.levelno, r.getMessage()) for r in caplog.records if text in r.getMessage()]


@pytest.mark.parametrize("backend, fatal, level", [
    ("zwo", False, logging.ERROR),
    ("zwo", True, logging.ERROR),
    ("alpaca", False, logging.WARNING),
    ("alpaca", True, logging.ERROR),
])
def test_one_line_per_loop_error(qt_app, tmp_path, caplog, backend, fatal, level):
    caplog.set_level(logging.DEBUG)
    ctrl = _controller(tmp_path, backend)
    ctrl._schedule_auto_recovery = MagicMock()
    window = _window(ctrl)  # noqa: F841 — keeps the connected handler alive
    msg = "Capture error: Connection refused by 127.0.0.1:40481"
    ctrl._on_camera_error(msg, is_fatal=fatal)
    lines = _lines(caplog, msg)
    assert window._notify.called, "the main window's handler ran"
    assert lines == [(level, f"Camera error: {msg}")]


def test_errors_the_controller_never_logged_are_still_logged(qt_app, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    ctrl = _controller(tmp_path, "zwo")
    window = _window(ctrl)  # noqa: F841 — keeps the connected handler alive
    ctrl.error.emit("ZWO SDK not found (libASICamera2.so)")
    assert _lines(caplog, "SDK not found") == [
        (logging.ERROR, "Camera error received: ZWO SDK not found (libASICamera2.so)")]
