"""Single-instance guard command-channel tests.

Covers the quit/activate protocol the installer relies on: --shutdown sends
"quit" (clean teardown before an upgrade), while a second app launch sends
anything else and just surfaces the running window.
"""
import sys
import threading
import time

import pytest


@pytest.fixture
def qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump_until_quit(app, timeout_sec=5.0):
    """Run the event loop until something calls app.quit() or the timeout.

    The timeout timer is owned and stopped here. A fire-and-forget singleShot
    outlives a fast test and quits the *next* test's loop early.
    """
    from PySide6.QtCore import QTimer
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(app.quit)
    timer.start(int(timeout_sec * 1000))
    try:
        app.exec()
    finally:
        timer.stop()


def test_quit_command_emits_quit_requested(qt_app):
    from services.single_instance import SingleInstanceGuard, request_shutdown
    name = "PFRSentinel-test-quit"
    guard = SingleInstanceGuard(name)
    seen = {"quit": 0, "activate": 0}
    guard.quit_requested.connect(lambda: seen.__setitem__("quit", seen["quit"] + 1))
    guard.activate_requested.connect(lambda: seen.__setitem__("activate", seen["activate"] + 1))
    guard.quit_requested.connect(qt_app.quit)

    try:
        assert guard.already_running() is False  # this process owns the lock

        result = {}

        def client():
            time.sleep(0.2)  # let the server's event loop start
            result["ok"] = request_shutdown(name, timeout_ms=2000)

        worker = threading.Thread(target=client, daemon=True)
        worker.start()
        _pump_until_quit(qt_app)
        worker.join(3)

        assert result.get("ok") is True
        assert seen == {"quit": 1, "activate": 0}
    finally:
        guard.deleteLater()


def test_non_quit_payload_emits_activate(qt_app):
    from PySide6.QtNetwork import QLocalSocket
    from services.single_instance import SingleInstanceGuard
    name = "PFRSentinel-test-activate"
    guard = SingleInstanceGuard(name)
    seen = {"quit": 0, "activate": 0}
    guard.quit_requested.connect(lambda: seen.__setitem__("quit", seen["quit"] + 1))
    guard.activate_requested.connect(lambda: seen.__setitem__("activate", seen["activate"] + 1))
    guard.activate_requested.connect(qt_app.quit)

    try:
        assert guard.already_running() is False

        def client():
            time.sleep(0.2)
            sock = QLocalSocket()
            sock.connectToServer(name)
            if sock.waitForConnected(2000):
                sock.write(b"activate")  # what a second app launch sends
                sock.flush()
                sock.waitForBytesWritten(2000)
                sock.disconnectFromServer()

        worker = threading.Thread(target=client, daemon=True)
        worker.start()
        _pump_until_quit(qt_app)
        worker.join(3)

        assert seen == {"quit": 0, "activate": 1}
    finally:
        guard.deleteLater()


def test_request_shutdown_false_when_nothing_running(qt_app):
    from services.single_instance import request_shutdown
    # No server listening on this name → no instance to signal.
    assert request_shutdown("PFRSentinel-test-absent", timeout_ms=300) is False


def test_receiver_waits_as_long_as_the_sender():
    """The receive window must not be shorter than request_shutdown's timeout.

    They were 200ms and 1500ms. A GUI thread busy with a frame could miss the
    payload, and the handler then treats a 'quit' as an 'activate' — so an
    installer upgrade proceeds while the app still holds its files and camera.
    """
    import inspect
    from services import single_instance

    sig = inspect.signature(single_instance.request_shutdown)
    sender_ms = sig.parameters["timeout_ms"].default
    assert single_instance._READ_TIMEOUT_MS >= sender_ms


def test_late_payload_is_still_honoured(qt_app):
    """The bytes may arrive well after the connection is accepted.

    This is the exact shape of the flake the blocking reader had on Windows:
    the handler ran at connect time, the client wrote "quit" a moment later,
    and the payload was never seen. The reader must stay armed until the
    payload, the peer's close, or the timeout — whichever comes first.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QLocalSocket
    from services.single_instance import SingleInstanceGuard
    name = "PFRSentinel-test-late"
    guard = SingleInstanceGuard(name)
    seen = {"quit": 0, "activate": 0}
    guard.quit_requested.connect(lambda: seen.__setitem__("quit", seen["quit"] + 1))
    guard.activate_requested.connect(lambda: seen.__setitem__("activate", seen["activate"] + 1))
    guard.quit_requested.connect(qt_app.quit)
    guard.activate_requested.connect(qt_app.quit)
    try:
        assert guard.already_running() is False
        sock = QLocalSocket()
        sock.connectToServer(name)
        assert sock.waitForConnected(2000)

        def write_late():
            sock.write(b"quit")
            sock.flush()

        QTimer.singleShot(400, write_late)   # well after newConnection fired
        _pump_until_quit(qt_app)
        assert seen == {"quit": 1, "activate": 0}
    finally:
        guard.deleteLater()


def test_silent_connection_falls_back_to_activate(qt_app):
    """A client that connects and sends nothing must not wedge the reader."""
    from PySide6.QtNetwork import QLocalSocket
    from services import single_instance
    name = "PFRSentinel-test-silent"
    guard = single_instance.SingleInstanceGuard(name)
    seen = {"quit": 0, "activate": 0}
    guard.quit_requested.connect(lambda: seen.__setitem__("quit", seen["quit"] + 1))
    guard.activate_requested.connect(lambda: seen.__setitem__("activate", seen["activate"] + 1))
    guard.activate_requested.connect(qt_app.quit)
    try:
        assert guard.already_running() is False
        sock = QLocalSocket()
        sock.connectToServer(name)
        assert sock.waitForConnected(2000)
        _pump_until_quit(qt_app, timeout_sec=single_instance._READ_TIMEOUT_MS / 1000.0 + 3.0)
        assert seen == {"quit": 0, "activate": 1}
    finally:
        guard.deleteLater()
