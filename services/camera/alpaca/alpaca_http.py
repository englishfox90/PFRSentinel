"""HTTP client for one ASCOM Alpaca device (and its server's Management API).

Every request carries ``ClientID`` (random per process) and an incrementing
``ClientTransactionID``. GET sends them as query parameters; PUT sends them
with the member's parameters as an ``application/x-www-form-urlencoded`` body
in canonical casing (``Connected``, ``Duration``, ``Light`` …), which is how the
official ``alpyca`` client sends them too.

Every call is time-bounded: ``(connect_timeout, read timeout)`` on the socket,
and the first name lookup of a host by ``connect_timeout`` too (the OS resolver
has no timeout of its own). Three sessions, so a slow call never holds up a
fast one: ordinary calls share one (serialised); ``put_control`` — abort, stop,
live settings — has its own, so AbortExposure never queues behind the capture
thread's poll; and image downloads have a third (``alpaca_download``: hard
total deadline and abort). Errors map to ``alpaca_protocol``: a non-zero
ErrorNumber to its Alpaca class, HTTP 400 to InvalidValue, 404 to
NotImplemented, and anything that means "unreachable or unusable" (socket error,
timeout, 5xx, bad JSON) to ``AlpacaTransportError``.
"""
import ipaddress
import itertools
import json
import random
import socket
import threading
import time
from typing import Callable, Optional

import numpy as np
import requests

from services.logger import app_logger

from .alpaca_download import (
    DOWNLOAD_CHUNK,  # noqa: F401 — re-exported
    MAX_IMAGE_BYTES,  # noqa: F401 — re-exported
    MAX_JSON_BYTES,
    TransferGuard,
    guarded_session,
    printable,
    read_body,
    read_error_text,
)
from .alpaca_transport_text import describe_transport_failure
from .alpaca_imagebytes import IMAGEBYTES_MIME, parse_imagearray_json, parse_imagebytes
from .alpaca_protocol import (
    API_VERSION,
    DEFAULT_PORT,
    INVALID_VALUE,
    NOT_IMPLEMENTED,
    AlpacaError,
    AlpacaInvalidValueError,
    AlpacaNotImplementedError,
    AlpacaProtocolError,
    AlpacaTimeoutError,
    AlpacaTransportError,
    raise_for_error,
)

_ERROR_TEXT_LIMIT = 200
DNS_CACHE_S = 300.0

CLIENT_ID = random.SystemRandom().randint(1, 2 ** 31 - 1)
_transaction_ids = itertools.count(1)
_transaction_lock = threading.Lock()
_dns_ok = {}
_dns_lock = threading.Lock()


def lan_session() -> requests.Session:
    """A Session that ignores proxy settings: an Alpaca server is on the local
    network, and Windows' registry proxy bypass does not cover bare LAN IPs."""
    session = requests.Session()
    session.trust_env = False
    return session


def next_transaction_id() -> int:
    with _transaction_lock:
        return next(_transaction_ids)


def format_param(value) -> str:
    """Alpaca wire text for a parameter value (invariant culture, never ``1e-05``)."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        text = repr(value)
        if "e" in text or "E" in text:
            text = f"{value:.12f}".rstrip("0")
            text = text + "0" if text.endswith(".") else text
        return text
    return str(value)


def ci_get(obj: dict, key: str, default=None):
    """Case-insensitive dict lookup (servers vary in response-key casing)."""
    if key in obj:
        return obj[key]
    lowered = key.lower()
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() == lowered:
            return v
    return default


def split_host_port(host: str, port=None):
    """``(host, port)`` from a host field that may carry a port.

    ``"obs-pc:11111"`` / ``"10.0.0.5:32323"`` / ``"[fe80::1]:11111"`` → host and
    that port; a bare IPv6 literal (``"fe80::1%eth0"``) keeps ``port``. Raises
    ``ValueError`` with a plain message for a port that is not a number.
    """
    text = (host or "").strip()
    if text.startswith("["):
        close = text.find("]")
        if close < 0:
            raise ValueError(f"Host {text!r} has an unclosed '['")
        inner, rest = text[1:close], text[close + 1:]
        if rest.startswith(":") and rest[1:]:
            return inner, _port_number(rest[1:], text)
        return inner, port
    if text.count(":") == 1:
        name, _, tail = text.partition(":")
        return name.strip(), _port_number(tail, text) if tail.strip() else port
    return text, port


def _port_number(text: str, field: str) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise ValueError(f"Host {field!r}: '{text}' is not a port number") from None
    if not 0 < value < 65536:
        raise ValueError(f"Host {field!r}: port {value} is out of range")
    return value


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def _url_host(host: str) -> str:
    host = (host or "").strip()
    if ":" in host and not host.startswith("["):
        return "[" + host.replace("%", "%25") + "]"
    return host


def _http_error(response, what: str, text: Optional[str] = None) -> Exception:
    status = response.status_code
    if text is None:
        text = getattr(response, "text", "") or ""
    text = printable(text, _ERROR_TEXT_LIMIT)
    detail = f"HTTP {status} for {what}" + (f": {text}" if text else "")
    if status == 400:
        error = AlpacaInvalidValueError(INVALID_VALUE, detail)
    elif status == 404:
        error = AlpacaNotImplementedError(NOT_IMPLEMENTED, detail)
    elif status >= 500:
        error = AlpacaTransportError(detail)
    else:
        error = AlpacaError(None, detail)
    error.http_status = status
    return error


def bound_dns(host: str, port: int, timeout: float,
              resolver: Callable = socket.getaddrinfo) -> None:
    """Resolve ``host`` once on a worker thread, waiting at most ``timeout``.

    The OS resolver has no timeout, so a dead DNS server on a VPN would hold
    the first request of every reconnect for 20-40 s. A literal IP skips this;
    a name that resolved is trusted for ``DNS_CACHE_S``. A lookup that fails
    outright is left for the request to report in its own words.
    """
    if not host or _is_ip_literal(host):
        return
    now = time.monotonic()
    with _dns_lock:
        if _dns_ok.get(host, 0.0) > now:
            return
    box = {}

    def lookup():
        try:
            resolver(host, port, 0, socket.SOCK_STREAM)
            box["ok"] = True
        except OSError:
            box["ok"] = False

    worker = threading.Thread(target=lookup, name="alpaca-dns", daemon=True)
    worker.start()
    worker.join(max(0.1, float(timeout)))
    if worker.is_alive():
        raise AlpacaTimeoutError(
            f"Looking up the address of {host} took longer than {timeout:.0f} s. "
            f"Check the network's DNS (or VPN), or enter the server's IP address.")
    if box.get("ok"):
        with _dns_lock:
            _dns_ok[host] = now + DNS_CACHE_S


class AlpacaHttpClient:
    """One Alpaca device at ``http://host:port/api/v1/<device_type>/<n>``."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, device_number: int = 0, *,
                 device_type: str = "camera", connect_timeout: float = 3.0,
                 request_timeout: float = 10.0,
                 session_factory: Callable[[], requests.Session] = lan_session):
        self.host = (host or "").strip()
        self.port = int(port)
        self._device_number = int(device_number)
        self.device_type = device_type.lower()
        self.connect_timeout = float(connect_timeout)
        self.request_timeout = float(request_timeout)
        self._session_factory = session_factory
        self._session = session_factory()
        self._image_session = None
        self._control_session = None
        self._lock = threading.Lock()
        self._image_lock = threading.Lock()
        self._control_lock = threading.Lock()
        self.last_image_format = None
        self.last_image_bytes = 0
        self.last_download_s = None

    # -- addressing -------------------------------------------------------
    @property
    def device_number(self) -> int:
        return self._device_number

    @device_number.setter
    def device_number(self, value: int) -> None:
        self._device_number = int(value)

    @property
    def server_url(self) -> str:
        return f"http://{_url_host(self.host)}:{self.port}"

    def member_url(self, member: str) -> str:
        return (f"{self.server_url}/api/v{API_VERSION}/{self.device_type}/"
                f"{self._device_number}/{member.lower()}")

    def describe(self) -> str:
        return f"{self.host}:{self.port} #{self._device_number}"

    # -- plumbing ---------------------------------------------------------
    def _ids(self) -> dict:
        return {"ClientID": str(CLIENT_ID),
                "ClientTransactionID": str(next_transaction_id())}

    def _timeout(self, timeout: Optional[float]):
        read = self.request_timeout if timeout is None else float(timeout)
        return (self.connect_timeout, read)

    def _bound_dns(self) -> None:
        if isinstance(self._session, requests.Session):
            bound_dns(self.host, self.port, self.connect_timeout)

    def _transport_error(self, exc, what: str, read_timeout: float):
        """The Alpaca exception for a failed request: plain words, raw text at DEBUG."""
        app_logger.debug(f"Alpaca {what} to {self.describe()} failed: {exc!r}")
        wait = self.connect_timeout if isinstance(exc, requests.ConnectTimeout) else read_timeout
        message = describe_transport_failure(exc, self.host, self.port, wait)
        if isinstance(exc, requests.Timeout):
            return AlpacaTimeoutError(message)
        return AlpacaTransportError(message)

    def _send(self, method: str, url: str, what: str, timeout, session=None, **kwargs):
        timeouts = self._timeout(timeout)
        self._bound_dns()
        try:
            response = (session or self._session).request(method, url, timeout=timeouts,
                                                          **kwargs)
        except requests.RequestException as exc:
            raise self._transport_error(exc, what, timeouts[1]) from exc
        if not hasattr(response, "status_code"):
            raise AlpacaProtocolError(f"No usable reply to {what} from {self.describe()}")
        return response

    @staticmethod
    def _decode_envelope(response, what: str):
        if response.status_code != 200:
            raise _http_error(response, what)
        try:
            body = response.json()
        except ValueError:
            raise AlpacaProtocolError(f"Reply to {what} is not JSON") from None
        if not isinstance(body, dict):
            raise AlpacaProtocolError(f"Reply to {what} is not a JSON object")
        raise_for_error(ci_get(body, "ErrorNumber", 0), ci_get(body, "ErrorMessage", ""))
        return ci_get(body, "Value")

    # -- device members ---------------------------------------------------
    def get(self, member: str, timeout: Optional[float] = None, **params):
        """GET a member and return its ``Value``."""
        query = self._ids()
        query.update({k: format_param(v) for k, v in params.items()})
        with self._lock:
            response = self._send("GET", self.member_url(member), f"GET {member}",
                                  timeout, params=query)
        return self._decode_envelope(response, f"GET {member}")

    def put(self, member: str, timeout: Optional[float] = None, **params):
        """PUT a member (form-encoded, canonical casing) and return ``Value``."""
        form = {k: format_param(v) for k, v in params.items()}
        form.update(self._ids())
        with self._lock:
            response = self._send("PUT", self.member_url(member), f"PUT {member}",
                                  timeout, data=form)
        return self._decode_envelope(response, f"PUT {member}")

    def put_control(self, member: str, timeout: Optional[float] = None, **params):
        """``put`` on the control session: never waits behind ``get`` / ``put``.

        For AbortExposure / StopExposure and live settings, which must not queue
        behind the capture thread's in-flight poll or a blocking StartExposure.
        """
        form = {k: format_param(v) for k, v in params.items()}
        form.update(self._ids())
        with self._control_lock:
            if self._control_session is None:
                self._control_session = self._session_factory()
            response = self._send("PUT", self.member_url(member), f"PUT {member}", timeout,
                                  session=self._control_session, data=form)
        return self._decode_envelope(response, f"PUT {member}")

    def _management(self, path: str, timeout: Optional[float]):
        url = f"{self.server_url}/management/{path}"
        with self._lock:
            response = self._send("GET", url, f"management {path}", timeout,
                                  params=self._ids())
        return self._decode_envelope(response, f"management {path}")

    def management_api_versions(self, timeout: Optional[float] = None) -> list:
        value = self._management("apiversions", timeout)
        return list(value) if isinstance(value, list) else []

    def management_description(self, timeout: Optional[float] = None) -> dict:
        value = self._management(f"v{API_VERSION}/description", timeout)
        return dict(value) if isinstance(value, dict) else {}

    def management_configured_devices(self, timeout: Optional[float] = None) -> list:
        """``[{DeviceName, DeviceType, DeviceNumber, UniqueID}, …]`` (dicts kept as sent)."""
        value = self._management(f"v{API_VERSION}/configureddevices", timeout)
        if not isinstance(value, list):
            raise AlpacaProtocolError("configureddevices Value is not a list")
        return [d for d in value if isinstance(d, dict)]

    # -- image download ---------------------------------------------------
    def _image_session_get(self):
        if self._image_session is None:
            self._image_session = guarded_session(self._session_factory())
        return self._image_session

    def get_image(self, timeout: float = 60.0, use_imagebytes: bool = True,
                  abort_event: Optional[threading.Event] = None) -> np.ndarray:
        """Download ``imagearray`` as ``(H, W)`` / ``(H, W, P)``.

        ``timeout`` bounds the whole download, headers included; each socket
        read is bounded by the request timeout (a stall), and ``abort_event``
        set at any moment raises ``AlpacaCancelledError`` within
        ``alpaca_download.WATCH_EVERY_S`` — the watchdog shuts the socket down,
        so neither a dripped header nor a dripped body can hold the call.
        Sends ``Accept: application/imagebytes`` when ``use_imagebytes``; the
        reply's Content-Type picks the decoder, so a server that ignores the
        header and answers JSON still works (up to ``MAX_JSON_BYTES``).
        """
        timeout = max(0.1, float(timeout))
        stall = min(self.request_timeout, timeout)
        headers = {"Accept": f"{IMAGEBYTES_MIME}, application/json"} if use_imagebytes \
            else {"Accept": "application/json"}
        started = time.monotonic()
        where = f"{self.host}:{self.port}"
        with self._image_lock:
            session = self._image_session_get()
            self._bound_dns()
            breaker = getattr(session, "break_transfer", None)
            with TransferGuard(started + timeout, abort_event, breaker, self.describe(),
                               timeout) as guard:
                response = None
                try:
                    try:
                        response = session.get(self.member_url("imagearray"),
                                               params=self._ids(), headers=headers,
                                               stream=True, timeout=(self.connect_timeout, stall))
                    except requests.RequestException as exc:
                        guard.check()
                        raise self._transport_error(exc, "image download", stall) from exc
                    guard.check()
                    payload, content_type = self._read_image_body(response, guard, where)
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass

        if IMAGEBYTES_MIME in content_type:
            image = parse_imagebytes(payload)
            self.last_image_format = "imagebytes"
        else:
            try:
                obj = json.loads(payload)
            except ValueError:
                raise AlpacaProtocolError("ImageArray reply is neither ImageBytes nor JSON") \
                    from None
            image = parse_imagearray_json(obj)
            self.last_image_format = "json"
        self.last_image_bytes = len(payload)
        self.last_download_s = time.monotonic() - started
        return image

    def _read_image_body(self, response, guard, where):
        if not hasattr(response, "status_code"):
            raise AlpacaProtocolError(f"No usable reply to GET imagearray from {where}")
        if response.status_code != 200:
            raise _http_error(response, "GET imagearray", read_error_text(response, guard))
        content_type = (response.headers.get("Content-Type") or "").lower()
        json_limit = None if IMAGEBYTES_MIME in content_type else MAX_JSON_BYTES
        return read_body(response, guard, json_limit=json_limit, where=where), content_type

    def close(self) -> None:
        for session in (self._session, self._image_session, self._control_session):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
