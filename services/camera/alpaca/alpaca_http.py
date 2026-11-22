"""HTTP client for one ASCOM Alpaca device (and its server's Management API).

Every request carries ``ClientID`` (random per process) and an incrementing
``ClientTransactionID``. GET sends them as query parameters; PUT sends them
with the member's parameters as an ``application/x-www-form-urlencoded`` body
in canonical casing (``Connected``, ``Duration``, ``Light`` …), which is how the
official ``alpyca`` client sends them too.

Every call is time-bounded: ``(connect_timeout, read timeout)`` on the socket,
plus a wall-clock deadline on image downloads, which stream in 1 MiB chunks on a
second session (no lock) so ``abort_event`` can cut a long download short while
short calls keep flowing. Errors map to ``alpaca_protocol``: a non-zero
ErrorNumber to its Alpaca class, HTTP 400 to InvalidValue, 404 to
NotImplemented, and anything that means "unreachable or unusable" (socket error,
timeout, 5xx, bad JSON) to ``AlpacaTransportError``.
"""
import itertools
import json
import random
import threading
import time
from typing import Callable, Optional

import numpy as np
import requests

from services.logger import app_logger

from .alpaca_transport_text import describe_transport_failure
from .alpaca_imagebytes import IMAGEBYTES_MIME, parse_imagearray_json, parse_imagebytes
from .alpaca_protocol import (
    API_VERSION,
    DEFAULT_PORT,
    INVALID_VALUE,
    NOT_IMPLEMENTED,
    OPERATION_CANCELLED,
    AlpacaCancelledError,
    AlpacaError,
    AlpacaInvalidValueError,
    AlpacaNotImplementedError,
    AlpacaProtocolError,
    AlpacaTimeoutError,
    AlpacaTransportError,
    raise_for_error,
)

DOWNLOAD_CHUNK = 1 << 20
MAX_IMAGE_BYTES = 1 << 30
_ERROR_TEXT_LIMIT = 200

CLIENT_ID = random.SystemRandom().randint(1, 2 ** 31 - 1)
_transaction_ids = itertools.count(1)
_transaction_lock = threading.Lock()


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
    """Alpaca wire text for a parameter value (invariant culture)."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return repr(value)
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


def _url_host(host: str) -> str:
    host = (host or "").strip()
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _http_error(response, what: str) -> Exception:
    status = response.status_code
    text = (getattr(response, "text", "") or "").strip()[:_ERROR_TEXT_LIMIT]
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
        self._lock = threading.Lock()
        self._image_lock = threading.Lock()
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

    def _transport_error(self, exc, what: str, read_timeout: float):
        """The Alpaca exception for a failed request: plain words, raw text at DEBUG."""
        app_logger.debug(f"Alpaca {what} to {self.describe()} failed: {exc!r}")
        wait = self.connect_timeout if isinstance(exc, requests.ConnectTimeout) else read_timeout
        message = describe_transport_failure(exc, self.host, self.port, wait)
        if isinstance(exc, requests.Timeout):
            return AlpacaTimeoutError(message)
        return AlpacaTransportError(message)

    def _send(self, method: str, url: str, what: str, timeout, **kwargs):
        timeouts = self._timeout(timeout)
        try:
            return self._session.request(method, url, timeout=timeouts, **kwargs)
        except requests.RequestException as exc:
            raise self._transport_error(exc, what, timeouts[1]) from exc

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
            self._image_session = self._session_factory()
        return self._image_session

    def get_image(self, timeout: float = 60.0, use_imagebytes: bool = True,
                  abort_event: Optional[threading.Event] = None) -> np.ndarray:
        """Download ``imagearray`` as ``(H, W)`` / ``(H, W, P)``.

        ``timeout`` bounds the whole download (and each socket read). Sends
        ``Accept: application/imagebytes`` when ``use_imagebytes``; the reply's
        Content-Type picks the decoder, so a server that ignores the header and
        answers JSON still works. ``abort_event`` set mid-download raises
        ``AlpacaCancelledError`` within one chunk.
        """
        timeout = float(timeout)
        headers = {"Accept": f"{IMAGEBYTES_MIME}, application/json"} if use_imagebytes \
            else {"Accept": "application/json"}
        started = time.monotonic()
        deadline = started + timeout
        with self._image_lock:
            session = self._image_session_get()
            try:
                response = session.get(self.member_url("imagearray"), params=self._ids(),
                                       headers=headers, stream=True,
                                       timeout=(self.connect_timeout, timeout))
            except requests.RequestException as exc:
                raise self._transport_error(exc, "image download", timeout) from exc
            try:
                payload, content_type = self._read_body(response, deadline, abort_event,
                                                        timeout)
            finally:
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

    def _read_body(self, response, deadline, abort_event, timeout):
        if response.status_code != 200:
            raise _http_error(response, "GET imagearray")
        content_type = (response.headers.get("Content-Type") or "").lower()
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                if int(length) > MAX_IMAGE_BYTES:
                    raise AlpacaProtocolError(f"Image of {length} bytes is implausibly large")
            except ValueError:
                pass
        buf = bytearray()
        try:
            for chunk in response.iter_content(DOWNLOAD_CHUNK):
                if abort_event is not None and abort_event.is_set():
                    raise AlpacaCancelledError(OPERATION_CANCELLED, "Image download aborted")
                if time.monotonic() > deadline:
                    raise AlpacaTimeoutError(
                        f"Image download exceeded {timeout:.0f} s ({self.describe()})")
                if chunk:
                    buf += chunk
                    if len(buf) > MAX_IMAGE_BYTES:
                        raise AlpacaProtocolError("Image download is implausibly large")
        except requests.Timeout as exc:
            app_logger.debug(f"Alpaca image download stalled after {len(buf)} bytes: {exc!r}")
            raise AlpacaTimeoutError(
                f"Image download from {self.host}:{self.port} stalled") from exc
        except requests.RequestException as exc:
            app_logger.debug(f"Alpaca image download failed after {len(buf)} bytes: {exc!r}")
            raise AlpacaTransportError(
                f"Image download from {self.host}:{self.port} was interrupted") from exc
        if abort_event is not None and abort_event.is_set():
            raise AlpacaCancelledError(OPERATION_CANCELLED, "Image download aborted")
        return buf, content_type

    def close(self) -> None:
        for session in (self._session, self._image_session):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
