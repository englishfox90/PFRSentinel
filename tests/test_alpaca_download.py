"""alpaca_download — the image download's total deadline and abort are hard guarantees.

The default-suite tests drive the logic through the fake transport (a dripping
body, headers that never arrive, an abort mid-drip, a bounded error body).
The ``requires_network`` tests do the same against a real local socket: a server
that drips one byte every half second, chunked or not, or drips headers forever
— the reviewers' ``drip.py``, which used to hang ``get_image`` indefinitely.
"""
import socket
import threading
import time

import numpy as np
import pytest
import requests

from services.camera.alpaca.alpaca_download import (
    MAX_JSON_BYTES,
    TransferGuard,
    guarded_session,
    printable,
    read_body,
)
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_imagebytes import HEADER
from services.camera.alpaca.alpaca_protocol import (
    AlpacaCancelledError,
    AlpacaError,
    AlpacaProtocolError,
    AlpacaTimeoutError,
    AlpacaTransportError,
)
from tests.alpaca_fake import FakeResponse, make_fake


@pytest.fixture
def rig():
    net, cam, clock = make_fake(width=10, height=6, scene="coords")
    client = AlpacaHttpClient(net.host, net.port, 0, session_factory=net.session,
                              connect_timeout=1.0, request_timeout=2.0)
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=0.5, Light=True)
    clock.advance(0.6)
    return net, cam, client


def test_dripping_body_is_cut_at_the_total_deadline(rig):
    net, cam, client = rig
    net.faults.trickle_downloads = 1
    net.faults.trickle_s = 0.02
    started = time.monotonic()
    with pytest.raises(AlpacaTimeoutError, match="exceeded"):
        client.get_image(timeout=0.3)
    assert time.monotonic() - started < 1.5
    np.testing.assert_array_equal(client.get_image(timeout=5), np.arange(60).reshape(6, 10))


def test_abort_mid_drip_returns_within_the_watch_interval(rig):
    net, cam, client = rig
    net.faults.trickle_downloads = 1
    abort = threading.Event()
    threading.Timer(0.2, abort.set).start()
    started = time.monotonic()
    with pytest.raises(AlpacaCancelledError):
        client.get_image(timeout=60, abort_event=abort)
    assert time.monotonic() - started < 1.0


def test_headers_that_never_arrive_are_broken_by_the_deadline_and_by_abort(rig):
    net, cam, client = rig
    net.faults.hang_downloads = 1
    started = time.monotonic()
    with pytest.raises(AlpacaTimeoutError, match="exceeded"):
        client.get_image(timeout=0.3)
    assert time.monotonic() - started < 1.5
    assert net.breaks == 1  # only the watchdog can end a request that never answers
    net.faults.hang_downloads = 1
    abort = threading.Event()
    threading.Timer(0.1, abort.set).start()
    with pytest.raises(AlpacaCancelledError):
        client.get_image(timeout=60, abort_event=abort)


def test_session_abort_predicate_without_wait_is_honoured(rig):
    net, cam, client = rig

    class Flag:
        def __init__(self):
            self.at = time.monotonic() + 0.15

        def is_set(self):
            return time.monotonic() >= self.at

    net.faults.trickle_downloads = 1
    with pytest.raises(AlpacaCancelledError):
        client.get_image(timeout=60, abort_event=Flag())


def test_error_body_is_bounded_and_printable(rig, monkeypatch):
    net, cam, client = rig
    body = b"\x00\x01driver says no\xff\xfe" + b"x" * 100_000
    monkeypatch.setattr(net, "respond", lambda *a, **k: FakeResponse(
        500, "application/octet-stream", body, "u"))
    with pytest.raises(AlpacaTransportError) as err:
        client.get_image(timeout=5)
    text = str(err.value)
    assert "driver says no" in text and "\x00" not in text and "�" not in text
    assert len(text) < 300


def test_short_body_is_a_transport_error_not_a_hang():
    class Raw:
        def __init__(self):
            self.chunks = [b"abc"]

        def read1(self, n):
            return self.chunks.pop() if self.chunks else b""

    response = FakeResponse(200, "application/imagebytes", b"", "u", content_length=10)
    response.raw = Raw()
    with TransferGuard(time.monotonic() + 5, None, None, "x", 5) as guard:
        with pytest.raises(AlpacaTransportError, match="3 of 10"):
            read_body(response, guard, where="x")


def test_body_lands_in_a_buffer_sized_from_content_length():
    payload = bytes(range(256)) * 4000
    response = FakeResponse(200, "application/imagebytes", payload, "u")
    with TransferGuard(time.monotonic() + 5, None, None, "x", 5) as guard:
        buf = read_body(response, guard, where="x")
    assert bytes(buf) == payload and len(buf) == len(payload)
    too_long = FakeResponse(200, "application/imagebytes", payload, "u", content_length=10)
    with TransferGuard(time.monotonic() + 5, None, None, "x", 5) as guard:
        with pytest.raises(AlpacaProtocolError, match="longer than its Content-Length"):
            read_body(too_long, guard, where="x")


def test_oversize_json_image_is_refused_with_a_reason(rig, monkeypatch):
    net, cam, client = rig
    monkeypatch.setattr(net, "respond", lambda *a, **k: FakeResponse(
        200, "application/json", b"{}", "u", content_length=MAX_JSON_BYTES + 1))
    with pytest.raises(AlpacaProtocolError, match="does not support ImageBytes"):
        client.get_image(timeout=5)


def test_imagebytes_error_with_bad_datastart_reports_the_device_error(rig, monkeypatch):
    net, cam, client = rig
    payload = HEADER.pack(1, 0x500, 0, 0, 9999, 2, 2, 2, 0, 0, 0) + b"Camera fell over\x00\x07"
    monkeypatch.setattr(net, "respond", lambda *a, **k: FakeResponse(
        200, "application/imagebytes", payload, "u"))
    with pytest.raises(AlpacaError) as err:
        client.get_image(timeout=5)
    assert err.value.number == 0x500 and "Camera fell over" in str(err.value)
    assert "\x00" not in str(err.value) and "\x07" not in str(err.value)


def test_printable_strips_control_characters():
    assert printable("ok\x00\x1b[31mred\n") == "ok??[31mred?".strip()
    assert len(printable("y" * 1000, 50)) == 50


def test_guarded_session_only_wraps_real_sessions():
    real = guarded_session(requests.Session())
    assert callable(real.break_transfer)
    real.break_transfer()  # nothing in flight: a no-op
    real.close()
    net, _, _ = make_fake()
    fake = net.session()
    assert guarded_session(fake) is fake


# -- real sockets ----------------------------------------------------------------
def _serve(mode):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    stop = threading.Event()
    conns = []

    def handle(conn):
        try:
            conn.recv(65536)
            if mode == "drip":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/imagebytes\r\n"
                             b"Content-Length: 100000000\r\n\r\n")
                while not stop.is_set():
                    conn.sendall(b"x")
                    time.sleep(0.5)
            elif mode == "chunked_drip":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/imagebytes\r\n"
                             b"Transfer-Encoding: chunked\r\n\r\n")
                while not stop.is_set():
                    conn.sendall(b"1\r\nx\r\n")
                    time.sleep(0.5)
            elif mode == "hdrdrip":
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                while not stop.is_set():
                    conn.sendall(b"X-A: b\r\n")
                    time.sleep(0.5)
            elif mode == "error_drip":
                conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 100000\r\n\r\n")
                while not stop.is_set():
                    conn.sendall(b"e")
                    time.sleep(0.5)
        except OSError:
            pass

    def accept():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conns.append(conn)
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()

    def close():
        stop.set()
        listener.close()
        for c in conns:
            c.close()
    return listener.getsockname()[1], close


@pytest.mark.requires_network
@pytest.mark.parametrize("mode", ["drip", "chunked_drip", "hdrdrip", "error_drip"])
def test_real_dripping_server_is_cut_at_the_deadline(mode):
    port, close = _serve(mode)
    try:
        client = AlpacaHttpClient("127.0.0.1", port, 0, request_timeout=2.0)
        started = time.monotonic()
        with pytest.raises(AlpacaTimeoutError, match="exceeded"):
            client.get_image(timeout=1.5)
        assert time.monotonic() - started < 2.5
        client.close()
    finally:
        close()


@pytest.mark.requires_network
@pytest.mark.parametrize("mode", ["drip", "hdrdrip"])
def test_real_dripping_server_honours_abort(mode):
    port, close = _serve(mode)
    try:
        client = AlpacaHttpClient("127.0.0.1", port, 0, request_timeout=2.0)
        abort = threading.Event()
        threading.Timer(0.5, abort.set).start()
        started = time.monotonic()
        with pytest.raises(AlpacaCancelledError):
            client.get_image(timeout=60, abort_event=abort)
        assert time.monotonic() - started < 1.5
        client.close()
    finally:
        close()


@pytest.mark.requires_network
def test_real_silent_server_is_a_stall_after_the_request_timeout():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    held = []
    threading.Thread(target=lambda: held.append(listener.accept()), daemon=True).start()
    try:
        client = AlpacaHttpClient("127.0.0.1", listener.getsockname()[1], 0,
                                  request_timeout=0.5)
        started = time.monotonic()
        with pytest.raises(AlpacaTimeoutError):
            client.get_image(timeout=30)
        assert time.monotonic() - started < 2.0
        client.close()
    finally:
        listener.close()
