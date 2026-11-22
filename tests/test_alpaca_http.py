"""alpaca_http — ids on every request, canonical PUT casing, error mapping, bounded timeouts, downloads."""
import threading

import numpy as np
import pytest
import requests

from services.camera.alpaca.alpaca_http import CLIENT_ID, AlpacaHttpClient, format_param
from services.camera.alpaca.alpaca_protocol import (
    AlpacaCancelledError,
    AlpacaError,
    AlpacaInvalidOperationError,
    AlpacaInvalidValueError,
    AlpacaNotConnectedError,
    AlpacaNotImplementedError,
    AlpacaProtocolError,
    AlpacaTimeoutError,
    AlpacaTransportError,
)
from services.camera.errors import CameraDisconnectedError, CameraTimeoutError
from tests.alpaca_fake import FakeResponse, make_fake


@pytest.fixture
def rig():
    net, cam, clock = make_fake(width=10, height=6, scene="coords")
    client = AlpacaHttpClient(net.host, net.port, 0, session_factory=net.session,
                              connect_timeout=2.5, request_timeout=7.0)
    return net, cam, client


def _expose(client, cam, duration=0.5):
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=duration, Light=True)
    cam.clock.advance(duration + 0.01)
    assert client.get("imageready") is True


def test_every_request_carries_client_id_and_increasing_transaction_id(rig):
    net, cam, client = rig
    client.get("name")
    client.put("connected", Connected=True)
    client.get("connected")
    client.management_configured_devices()
    ids = [int(r.params["ClientTransactionID"]) for r in net.requests]
    assert all(r.params["ClientID"] == str(CLIENT_ID) for r in net.requests)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert 1 <= CLIENT_ID < 2 ** 31


def test_get_uses_query_and_put_uses_canonical_form_fields(rig):
    net, cam, client = rig
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=1.5, Light=False)
    put = net.member_requests("startexposure", "PUT")[0]
    assert put.params["Duration"] == "1.5" and put.params["Light"] == "False"
    assert "?" not in put.url
    client.get("cameraxsize")
    get = net.member_requests("cameraxsize", "GET")[0]
    assert {"ClientID", "ClientTransactionID"} <= set(get.params)
    assert net.requests[0].url.startswith("http://192.0.2.10:11111/api/v1/camera/0/")


def test_wrong_put_casing_is_rejected_by_the_strict_fake(rig):
    net, cam, client = rig
    with pytest.raises(AlpacaInvalidValueError, match="HTTP 400"):
        client.put("connected", connected=True)


def test_values_and_case_insensitive_response_keys(rig, monkeypatch):
    net, cam, client = rig
    assert client.get("name") == "PFR Alpaca Simulator"
    original = net.respond

    def lower_keys(*a, **k):
        resp = original(*a, **k)
        resp.content = resp.content.replace(b'"Value"', b'"value"') \
            .replace(b'"ErrorNumber"', b'"errornumber"')
        return resp
    monkeypatch.setattr(net, "respond", lower_keys)
    assert client.get("interfaceversion") == 3


def test_error_numbers_map_to_exception_classes(rig):
    net, cam, client = rig
    with pytest.raises(AlpacaNotConnectedError) as exc:
        client.get("cameraxsize")
    assert exc.value.number == 0x407 and isinstance(exc.value, CameraDisconnectedError)
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaNotImplementedError):
        client.get("cooleron")
    with pytest.raises(AlpacaInvalidValueError):
        client.put("gain", Gain=10_000)
    with pytest.raises(AlpacaInvalidOperationError):
        client.get("lastexposureduration")
    cam.faults.imagearray_errors = 1
    _expose(client, cam)
    with pytest.raises(AlpacaError) as err:
        client.get_image(timeout=5)
    assert err.value.number == 0x500


def test_http_status_mapping(rig):
    net, cam, client = rig
    with pytest.raises(AlpacaNotImplementedError, match="HTTP 404"):
        client.get("nosuchmember")
    client.device_number = 3
    with pytest.raises(AlpacaInvalidValueError, match="HTTP 400"):
        client.get("name")


def test_5xx_and_bad_json_are_transport_errors(rig, monkeypatch):
    net, cam, client = rig
    monkeypatch.setattr(net, "respond", lambda *a, **k: FakeResponse(
        503, "text/plain", b"busy", "u"))
    with pytest.raises(AlpacaTransportError, match="503"):
        client.get("name")
    monkeypatch.setattr(net, "respond", lambda *a, **k: FakeResponse(
        200, "application/json", b"<html>", "u"))
    with pytest.raises(AlpacaProtocolError):
        client.get("name")
    monkeypatch.setattr(net, "respond", lambda *a, **k: FakeResponse(
        200, "application/json", b"[1, 2]", "u"))
    with pytest.raises(AlpacaProtocolError):
        client.get("name")


def test_connection_error_and_timeout_become_transport_errors(rig):
    net, cam, client = rig
    net.faults.connection_errors = 1
    with pytest.raises(AlpacaTransportError) as exc:
        client.get("name")
    assert isinstance(exc.value, CameraDisconnectedError)
    net.faults.timeouts = 1
    with pytest.raises(AlpacaTimeoutError) as exc:
        client.get("name")
    assert isinstance(exc.value, CameraTimeoutError)
    assert isinstance(exc.value, CameraDisconnectedError)
    assert client.get("name")  # recovers


def test_every_call_is_time_bounded(rig):
    net, cam, client = rig
    client.get("name")
    client.get("name", timeout=1.25)
    client.management_api_versions()
    _expose(client, cam)
    client.get_image(timeout=42)
    timeouts = [r.timeout for r in net.requests]
    assert all(isinstance(t, tuple) and None not in t for t in timeouts)
    assert timeouts[0] == (2.5, 7.0) and timeouts[1] == (2.5, 1.25)
    assert net.member_requests("imagearray")[0].timeout == (2.5, 42.0)


def test_image_download_imagebytes_and_json_agree(rig):
    net, cam, client = rig
    _expose(client, cam)
    via_bytes = client.get_image(timeout=5, use_imagebytes=True)
    assert client.last_image_format == "imagebytes"
    req = net.member_requests("imagearray")[-1]
    assert "application/imagebytes" in req.headers["Accept"] and req.stream
    via_json = client.get_image(timeout=5, use_imagebytes=False)
    assert client.last_image_format == "json"
    np.testing.assert_array_equal(via_bytes, via_json)
    assert via_bytes.shape == (6, 10)
    np.testing.assert_array_equal(via_bytes, np.arange(60).reshape(6, 10))
    assert client.last_image_bytes > 0 and client.last_download_s is not None


def test_server_without_imagebytes_falls_back_to_json():
    net, cam, _ = make_fake(width=8, height=4, supports_imagebytes=False, scene="coords")
    client = AlpacaHttpClient(net.host, net.port, 0, session_factory=net.session)
    _expose(client, cam)
    image = client.get_image(timeout=5, use_imagebytes=True)
    assert client.last_image_format == "json"
    np.testing.assert_array_equal(image, np.arange(32).reshape(4, 8))


def test_imagearray_before_an_image_is_invalid_operation(rig):
    net, cam, client = rig
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaInvalidOperationError):
        client.get_image(timeout=5)


def test_download_uses_its_own_session_and_streams_in_mib_chunks(rig):
    net, cam, client = rig
    _expose(client, cam)
    client.get_image(timeout=5)
    assert len(net.sessions) == 2


def test_abort_event_cuts_a_download_short():
    net, cam, _ = make_fake(width=1200, height=1000, scene="coords", max_adu=65535)
    client = AlpacaHttpClient(net.host, net.port, 0, session_factory=net.session)
    _expose(client, cam)
    abort = threading.Event()
    chunks = []

    def on_chunk(sent):
        chunks.append(sent)
        abort.set()
    net.on_chunk = on_chunk
    with pytest.raises(AlpacaCancelledError):
        client.get_image(timeout=5, abort_event=abort)
    assert len(chunks) == 1  # 2.4 MB body, stopped after the first 1 MiB chunk


def test_stalled_download_is_a_timeout(rig):
    net, cam, client = rig
    _expose(client, cam)
    net.faults.download_timeouts = 1
    with pytest.raises(AlpacaTimeoutError):
        client.get_image(timeout=5)
    assert client.get_image(timeout=5).shape == (6, 10)


def test_wall_clock_deadline_bounds_a_trickling_download(rig, monkeypatch):
    net, cam, client = rig
    _expose(client, cam)
    import services.camera.alpaca.alpaca_http as mod
    ticks = iter([0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks))
    with pytest.raises(AlpacaTimeoutError, match="exceeded"):
        client.get_image(timeout=5)


def test_management_configured_devices_and_description(rig):
    net, cam, client = rig
    devices = client.management_configured_devices()
    assert devices == [{"DeviceName": "PFR Alpaca Simulator", "DeviceType": "Camera",
                        "DeviceNumber": 0, "UniqueID": cam.spec.unique_id}]
    assert client.management_api_versions() == [1]
    assert client.management_description()["ServerName"]


def test_format_param_invariant_text():
    assert format_param(True) == "True" and format_param(False) == "False"
    assert format_param(0.001) == "0.001" and format_param(7) == "7"


def test_ipv6_host_is_bracketed():
    client = AlpacaHttpClient("fe80::1", 11111, 2, session_factory=requests.Session)
    assert client.member_url("Name") == "http://[fe80::1]:11111/api/v1/camera/2/name"
    client.close()
