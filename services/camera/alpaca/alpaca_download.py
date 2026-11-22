"""A bounded ImageArray download: total deadline and abort are hard guarantees.

``iter_content`` blocks until a whole chunk has arrived, so a server that drips
a byte every half second never lets a deadline or abort check run, and a
dripped header block never returns from ``Session.get`` at all. Three layers
close that:

- the body is read with ``raw.read1``, which returns after each ``recv``, so the
  deadline and abort are checked between socket reads;
- every ``recv`` is bounded by a short stall timeout (the client's request
  timeout), separate from the total deadline;
- ``TransferGuard`` runs a watchdog thread that, at the deadline or on abort,
  shuts down the socket under the transfer (``GuardedAdapter`` tracks it) — so
  a blocked header read or ``recv`` returns at once whatever the server does.

The body lands in a buffer preallocated from Content-Length (no doubling
copies), and a JSON ImageArray over ``MAX_JSON_BYTES`` is refused: decoding it
would take gigabytes and minutes.
"""
import socket
import threading
import time
from typing import Callable, Optional

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection
from urllib3.connectionpool import HTTPConnectionPool

from services.logger import app_logger

from .alpaca_protocol import (
    OPERATION_CANCELLED,
    AlpacaCancelledError,
    AlpacaProtocolError,
    AlpacaTimeoutError,
    AlpacaTransportError,
)

DOWNLOAD_CHUNK = 1 << 20
MAX_IMAGE_BYTES = 1 << 30
MAX_JSON_BYTES = 64 << 20
ERROR_BODY_LIMIT = 4096
WATCH_EVERY_S = 0.05

_TIMEOUT_ERRORS = (requests.Timeout, urllib3.exceptions.TimeoutError, socket.timeout)
_READ_ERRORS = (requests.RequestException, urllib3.exceptions.HTTPError, OSError, ValueError,
                AttributeError)


def printable(text: str, limit: int = 200) -> str:
    """``text`` with control / binary characters replaced, cut to ``limit``."""
    cleaned = "".join(ch if ch.isprintable() and ch != "\ufffd" else "?"
                      for ch in (text or "")[:limit])
    return cleaned.strip()


class GuardedAdapter(HTTPAdapter):
    """An adapter that remembers the connection of the request in flight, so a
    watchdog on another thread can shut its socket down (``break_transfer``)."""

    def __init__(self):
        self._active = None
        self._active_lock = threading.Lock()
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        adapter = self

        class _TrackedConnection(HTTPConnection):
            def getresponse(self, *a, **k):
                adapter._track(self)
                return super().getresponse(*a, **k)

        class _TrackedPool(HTTPConnectionPool):
            ConnectionCls = _TrackedConnection

        schemes = dict(self.poolmanager.pool_classes_by_scheme)
        schemes["http"] = _TrackedPool
        self.poolmanager.pool_classes_by_scheme = schemes

    def _track(self, connection) -> None:
        with self._active_lock:
            self._active = connection

    def break_transfer(self) -> None:
        with self._active_lock:
            connection, self._active = self._active, None
        sock = getattr(connection, "sock", None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def guarded_session(session):
    """``session`` with the guarded adapter on ``http://`` (a real Session only)."""
    if isinstance(session, requests.Session) and not hasattr(session, "break_transfer"):
        adapter = GuardedAdapter()
        session.mount("http://", adapter)
        session.break_transfer = adapter.break_transfer
    return session


class TransferGuard:
    """Watchdog for one download: at ``deadline`` or once ``abort_event`` is set,
    records why and calls ``breaker`` (which shuts the socket down)."""

    def __init__(self, deadline: float, abort_event, breaker: Optional[Callable[[], None]],
                 describe: str, timeout_s: float, clock: Callable[[], float] = time.monotonic):
        self.deadline = deadline
        self.abort_event = abort_event
        self.breaker = breaker
        self.describe = describe
        self.timeout_s = timeout_s
        self.clock = clock
        self.reason = None
        self._stop = threading.Event()
        self._thread = None

    def _due(self) -> Optional[str]:
        event = self.abort_event
        if event is not None and event.is_set():
            return "cancelled"
        if self.clock() >= self.deadline:
            return "timeout"
        return None

    def _watch(self) -> None:
        while not self._stop.wait(WATCH_EVERY_S):
            reason = self._due()
            if reason:
                self.reason = reason
                if self.breaker is not None:
                    try:
                        self.breaker()
                    except Exception as exc:
                        app_logger.debug(f"Alpaca download: breaking the transfer failed: {exc}")
                return

    def __enter__(self) -> "TransferGuard":
        self._thread = threading.Thread(target=self._watch, name="alpaca-download-guard",
                                        daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False

    def error(self) -> Optional[Exception]:
        reason = self.reason or self._due()
        if reason == "cancelled":
            return AlpacaCancelledError(OPERATION_CANCELLED, "Image download aborted")
        if reason == "timeout":
            return AlpacaTimeoutError(
                f"Image download exceeded {self.timeout_s:.0f} s ({self.describe})")
        return None

    def check(self) -> None:
        error = self.error()
        if error is not None:
            raise error


def _content_length(response) -> Optional[int]:
    headers = response.headers
    encoding = (headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding not in ("", "identity"):
        return None
    try:
        length = int(headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def _chunks(response, size: int):
    """``read1`` per ``recv`` when the response has it, else ``iter_content``."""
    read1 = getattr(getattr(response, "raw", None), "read1", None)
    if read1 is None:
        yield from response.iter_content(size)
        return
    while True:
        chunk = read1(size)
        if not chunk:
            return
        yield chunk


def _json_refused(size: int) -> AlpacaProtocolError:
    return AlpacaProtocolError(
        f"The Alpaca server sent the image as JSON ({size / 1e6:.0f} MB or more). JSON "
        f"images over {MAX_JSON_BYTES >> 20} MB are refused: decoding one takes gigabytes "
        f"of memory. The server does not support ImageBytes (or it is switched off in "
        f"PFR's Alpaca settings) — use a server that supports it, e.g. ASCOM Remote 6.6+.")


def read_body(response, guard: TransferGuard, *, limit: int = MAX_IMAGE_BYTES,
              json_limit: Optional[int] = None, where: str = "") -> bytearray:
    """The response body, read under ``guard``; raises the guard's error first."""
    length = _content_length(response)
    if json_limit is not None and length is not None and length > json_limit:
        raise _json_refused(length)
    if length is not None and length > limit:
        raise AlpacaProtocolError(f"Image of {length} bytes is implausibly large")
    buf = bytearray(length) if length is not None else bytearray()
    view = memoryview(buf) if length is not None else None
    filled = 0
    cap = limit if json_limit is None else min(limit, json_limit)
    try:
        for chunk in _chunks(response, DOWNLOAD_CHUNK):
            guard.check()
            n = len(chunk)
            if view is not None:
                if filled + n > length:
                    raise AlpacaProtocolError("Image download is longer than its Content-Length")
                view[filled:filled + n] = chunk
            else:
                buf += chunk
            filled += n
            if filled > cap:
                if json_limit is not None and filled > json_limit:
                    raise _json_refused(filled)
                raise AlpacaProtocolError("Image download is implausibly large")
    except (AlpacaCancelledError, AlpacaTimeoutError, AlpacaProtocolError):
        raise
    except _READ_ERRORS as exc:
        guard.check()
        app_logger.debug(f"Alpaca image download failed after {filled} bytes: {exc!r}")
        if isinstance(exc, _TIMEOUT_ERRORS) or "timed out" in str(exc).lower():
            raise AlpacaTimeoutError(f"Image download from {where} stalled") from None
        raise AlpacaTransportError(f"Image download from {where} was interrupted") from None
    finally:
        if view is not None:
            view.release()
    guard.check()
    if length is not None and filled < length:
        raise AlpacaTransportError(
            f"Image download from {where} ended after {filled} of {length} bytes")
    return buf


def read_error_text(response, guard: TransferGuard) -> str:
    """At most ``ERROR_BODY_LIMIT`` bytes of an error reply, as printable text."""
    out = bytearray()
    try:
        for chunk in _chunks(response, ERROR_BODY_LIMIT):
            guard.check()
            out += chunk
            if len(out) >= ERROR_BODY_LIMIT:
                break
    except (AlpacaCancelledError, AlpacaTimeoutError):
        raise
    except _READ_ERRORS as exc:
        guard.check()
        app_logger.debug(f"Alpaca error body unreadable: {exc!r}")
    return printable(bytes(out[:ERROR_BODY_LIMIT]).decode("utf-8", errors="replace"))


__all__ = ["DOWNLOAD_CHUNK", "MAX_IMAGE_BYTES", "MAX_JSON_BYTES", "GuardedAdapter",
           "TransferGuard", "guarded_session", "printable", "read_body", "read_error_text"]
