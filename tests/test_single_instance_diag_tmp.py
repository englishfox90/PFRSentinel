"""TEMPORARY diagnostic for the single-instance quit flake on the CI runner.

Runs several client-side write strategies against the real guard and reports
what the server received. Deliberately fails at the end so the summary shows
in the CI log. Remove before merge.
"""
import sys
import threading
import time

import pytest


def _server_rounds(label, client_fn, n=10):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from services import single_instance as si

    app = QApplication.instance() or QApplication(sys.argv)
    rows = []
    for i in range(n):
        name = f"PFRSentinel-diag-{label}-{i}"
        guard = si.SingleInstanceGuard(name)
        assert guard.already_running() is False
        loop = QEventLoop()
        got = {}

        def dispatch(cmd, _got=got, _loop=loop):
            _got["cmd"] = cmd
            _loop.quit()

        guard._dispatch = dispatch
        info = {}
        th = threading.Thread(target=client_fn, args=(name, info), daemon=True)
        th.start()
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(loop.quit)
        t.start(5000)
        t0 = time.monotonic()
        loop.exec()
        t.stop()
        th.join(3)
        rows.append((got.get("cmd"), round(time.monotonic() - t0, 3), dict(info)))
        if guard._server is not None:
            guard._server.close()
        guard.deleteLater()
        app.processEvents()
    return rows


def _client_baseline(name, info):
    """Exactly what request_shutdown does, with every return value recorded."""
    from PySide6.QtNetwork import QLocalSocket
    time.sleep(0.2)
    t0 = time.monotonic()
    sock = QLocalSocket()
    sock.connectToServer(name)
    info["connected"] = sock.waitForConnected(2000)
    info["write"] = sock.write(b"quit")
    info["flush"] = sock.flush()
    info["wfbw"] = sock.waitForBytesWritten(2000)
    info["btw_after"] = sock.bytesToWrite()
    info["state"] = sock.state().name
    info["err"] = sock.errorString()
    info["wfd"] = sock.waitForDisconnected(2000)
    info["btw_end"] = sock.bytesToWrite()
    info["dt"] = round(time.monotonic() - t0, 3)


def _client_loop_until_flushed(name, info):
    from PySide6.QtNetwork import QLocalSocket
    time.sleep(0.2)
    sock = QLocalSocket()
    sock.connectToServer(name)
    info["connected"] = sock.waitForConnected(2000)
    sock.write(b"quit")
    deadline = time.monotonic() + 2.0
    loops = 0
    while sock.bytesToWrite() > 0 and time.monotonic() < deadline:
        loops += 1
        sock.waitForBytesWritten(200)
    info["loops"] = loops
    info["btw_after"] = sock.bytesToWrite()
    info["wfd"] = sock.waitForDisconnected(2000)
    info["btw_end"] = sock.bytesToWrite()


def _client_write_then_sleep(name, info):
    from PySide6.QtNetwork import QLocalSocket
    time.sleep(0.2)
    sock = QLocalSocket()
    sock.connectToServer(name)
    info["connected"] = sock.waitForConnected(2000)
    sock.write(b"quit")
    time.sleep(0.5)
    info["btw_after_sleep"] = sock.bytesToWrite()
    info["wfd"] = sock.waitForDisconnected(2000)
    info["btw_end"] = sock.bytesToWrite()


def _client_event_loop_in_thread(name, info):
    """Client with its own event loop: connect, write, and let Qt drive it."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtNetwork import QLocalSocket
    time.sleep(0.2)
    sock = QLocalSocket()
    loop = QEventLoop()
    sock.connected.connect(lambda: (sock.write(b"quit"), info.__setitem__("wrote", True)))
    sock.disconnected.connect(loop.quit)
    sock.errorOccurred.connect(lambda e: (info.__setitem__("err", sock.errorString()), loop.quit()))
    QTimer.singleShot(3000, loop.quit)
    sock.connectToServer(name)
    loop.exec()
    info["btw_end"] = sock.bytesToWrite()
    info["state"] = sock.state().name


def test_report_client_strategies():
    variants = [
        ("baseline", _client_baseline),
        ("loopflush", _client_loop_until_flushed),
        ("writesleep", _client_write_then_sleep),
        ("evloop", _client_event_loop_in_thread),
    ]
    lines = [f"platform={sys.platform} python={sys.version.split()[0]}"]
    try:
        import PySide6
        lines.append(f"PySide6={PySide6.__version__}")
    except Exception:
        pass
    for label, fn in variants:
        rows = _server_rounds(label, fn)
        ok = sum(1 for cmd, _, _ in rows if cmd and cmd.strip().lower().startswith(b"quit"))
        lines.append(f"\n=== {label}: quit seen {ok}/{len(rows)}")
        for cmd, dt, info in rows:
            tag = "OK  " if cmd and cmd.startswith(b"quit") else "MISS"
            lines.append(f"  {tag} server_got={cmd!r} after={dt}s client={info}")
    pytest.fail("\n".join(lines))
