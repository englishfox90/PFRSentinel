"""Human wording for Alpaca transport failures (issue #43): one sentence naming
the server and the likely cause, never urllib3's retry text or the request's
ClientID query string; the raw text goes to DEBUG only. Also: Alpaca traffic
ignores a system proxy by default."""
import errno
import inspect
import logging
import socket

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NameResolutionError, NewConnectionError

from services.camera.alpaca import alpaca_discovery
from services.camera.alpaca.alpaca_connection import AlpacaConnection
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_probe import probe_alpaca_camera
from services.camera.alpaca.alpaca_protocol import AlpacaTimeoutError, AlpacaTransportError
from services.camera.alpaca.alpaca_transport_text import describe_transport_failure
from tests.alpaca_fake import FakeResponse
from tests.alpaca_rig import alpaca_settings

HOST, PORT = "127.0.0.1", 11111
URL = (f"/management/v1/configureddevices?ClientID=1714636915&ClientTransactionID=1")


def _wrapped(reason):
    """``requests.ConnectionError`` the way requests raises it for a dead port."""
    retry = MaxRetryError(None, URL, reason=reason)
    return requests.ConnectionError(retry)


def _refused():
    inner = NewConnectionError(
        None, f"Failed to establish a new connection: [Errno {errno.ECONNREFUSED}] "
              "Connection refused")
    inner.__cause__ = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
    return _wrapped(inner)


def _not_found():
    gai = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    inner = NameResolutionError("foo", None, gai)
    inner.__cause__ = gai
    return _wrapped(inner)


def _no_route():
    inner = NewConnectionError(None, "Failed to establish a new connection: "
                                     "[Errno 113] No route to host")
    inner.__cause__ = OSError(errno.EHOSTUNREACH, "No route to host")
    return _wrapped(inner)


@pytest.mark.parametrize("exc,host,expected", [
    (_refused(), HOST, "Connection refused by 127.0.0.1:11111 — is the Alpaca server running?"),
    (_not_found(), "foo", "Host name 'foo' not found — check the server address"),
    (_no_route(), "10.0.0.5", "No route to 10.0.0.5 — check the address and that it is on "
                              "this network"),
    (requests.ConnectTimeout("Connection to 10.0.0.5 timed out. (connect timeout=3)"),
     "10.0.0.5", "No answer from 10.0.0.5:11111 within 3 s"),
    (requests.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed "
                              "connection without response'))"),
     HOST, "127.0.0.1:11111 closed the connection — is it an Alpaca server?"),
    (requests.ConnectionError("something odd"), HOST,
     "Cannot reach the Alpaca server at 127.0.0.1:11111"),
])
def test_failures_read_as_one_short_sentence(exc, host, expected):
    text = describe_transport_failure(exc, host, PORT, 3.0)
    assert text == expected
    assert "ClientID" not in text and "HTTPConnectionPool" not in text


class _RaisingSession:
    def __init__(self, exc):
        self.exc = exc

    def request(self, *a, **k):
        raise self.exc

    get = request

    def close(self):
        pass


def test_client_raises_the_plain_sentence_and_logs_the_raw_text(caplog):
    client = AlpacaHttpClient(HOST, PORT, session_factory=lambda: _RaisingSession(_refused()))
    with caplog.at_level(logging.DEBUG), pytest.raises(AlpacaTransportError) as raised:
        client.management_configured_devices()
    assert str(raised.value) == ("Connection refused by 127.0.0.1:11111 — is the Alpaca "
                                 "server running?")
    assert "ClientID" in caplog.text


def test_read_timeout_names_the_read_wait():
    client = AlpacaHttpClient(HOST, PORT, request_timeout=7.0,
                              session_factory=lambda: _RaisingSession(requests.ReadTimeout("x")))
    with pytest.raises(AlpacaTimeoutError, match=r"^No answer from 127.0.0.1:11111 within 7 s$"):
        client.get("name")


def test_probe_carries_the_sentence():
    settings = alpaca_settings(type("N", (), {"host": HOST, "port": PORT})())
    result = probe_alpaca_camera(settings, session_factory=lambda: _RaisingSession(_refused()))
    assert result.ok is False
    assert result.message == "Connection refused by 127.0.0.1:11111 — is the Alpaca server running?"


class _NotFoundSession:
    """Some other web server on the port: 404 for every path."""

    def request(self, method, url, **_k):
        return FakeResponse(404, "text/html", b"<html><body>Not Found</body></html>", url)

    get = lambda self, url, **k: self.request("GET", url, **k)  # noqa: E731

    def close(self):
        pass


def test_a_web_server_that_is_not_alpaca_is_named_as_such():
    conn = AlpacaConnection({"host": HOST, "port": 8080}, session_factory=_NotFoundSession)
    assert conn.connect() is False
    assert conn.last_error == ("Server at 127.0.0.1:8080 is not an Alpaca server "
                               "(HTTP 404 on /management) — check the port")


def test_alpaca_traffic_ignores_the_system_proxy_by_default():
    client = AlpacaHttpClient(HOST, PORT)
    assert client._session.trust_env is False
    assert client._image_session_get().trust_env is False
    default = inspect.signature(alpaca_discovery.cameras_on_server).parameters["client_factory"]
    assert default.default is AlpacaHttpClient
    client.close()
