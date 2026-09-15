"""Single-instance guard command-channel tests.

Covers the quit/activate protocol the installer relies on: --shutdown sends
"quit" (clean teardown before an upgrade), while a second app launch sends
anything else and just surfaces the running window.

Clients here never run in a Python *thread*. PySide6's blocking waitFor*()
calls hold the GIL, so a thread-based client sitting in waitForDisconnected()
starves the main thread of the Python slots that accept the connection and
read the payload; Qt's local pipes have a zero-length buffer, so the write
cannot complete until that read happens, and both sides stall until the client
times out. That is exactly how it failed on the CI runner. Production is
cross-process, so the quit test drives the real request_shutdown() from a
subprocess, and the other clients live on the main thread inside the event loop.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _make_guard(qt_app, name, quit_ends_loop=True, activate_ends_loop=True):
    from services.single_instance import SingleInstanceGuard
    guard = SingleInstanceGuard(name)
    seen = {"quit": 0, "activate": 0}
    guard.quit_requested.connect(lambda: seen.__setitem__("quit", seen["quit"] + 1))
    guard.activate_requested.connect(lambda: seen.__setitem__("activate", seen["activate"] + 1))
    if quit_ends_loop:
        guard.quit_requested.connect(qt_app.quit)
    if activate_ends_loop:
        guard.activate_requested.connect(qt_app.quit)
    return guard, seen


def _spawn_shutdown_client(name):
    """Run request_shutdown() in a separate process, as the installer does."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from PySide6.QtCore import QCoreApplication;"
        "app = QCoreApplication([]);"
        "from services.single_instance import request_shutdown;"
        "print('RESULT', request_shutdown(sys.argv[2], timeout_ms=5000))"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.Popen(
        [sys.executable, "-c", code, _REPO_ROOT, name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def test_quit_command_emits_quit_requested(qt_app):
    name = "PFRSentinel-test-quit"
    guard, seen = _make_guard(qt_app, name)
    try:
        assert guard.already_running() is False  # this process owns the lock
        proc = _spawn_shutdown_client(name)
        # Generous: the child has to import PySide6 before it can connect.
        _pump_until_quit(qt_app, timeout_sec=30.0)
        out, err = proc.communicate(timeout=30)

        assert proc.returncode == 0, err
        assert "RESULT True" in out, (out, err)
        assert seen == {"quit": 1, "activate": 0}
    finally:
        guard.deleteLater()


def test_non_quit_payload_emits_activate(qt_app):
    from PySide6.QtNetwork import QLocalSocket
    name = "PFRSentinel-test-activate"
    guard, seen = _make_guard(qt_app, name)
    try:
        assert guard.already_running() is False
        sock = QLocalSocket()

        def on_connected():
            sock.write(b"activate")  # what a second app launch sends
            sock.disconnectFromServer()

        sock.connected.connect(on_connected)
        sock.connectToServer(name)
        _pump_until_quit(qt_app)
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

    The reader must stay armed until the payload, the peer's close, or the
    timeout — whichever comes first — rather than deciding at connect time.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QLocalSocket
    name = "PFRSentinel-test-late"
    guard, seen = _make_guard(qt_app, name)
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
    guard, seen = _make_guard(qt_app, name)
    try:
        assert guard.already_running() is False
        sock = QLocalSocket()
        sock.connectToServer(name)
        assert sock.waitForConnected(2000)
        _pump_until_quit(qt_app, timeout_sec=single_instance._READ_TIMEOUT_MS / 1000.0 + 3.0)
        assert seen == {"quit": 0, "activate": 1}
    finally:
        guard.deleteLater()
