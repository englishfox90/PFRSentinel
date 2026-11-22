"""alpaca_probe — Test Connection summary, failure reasons, no configuration, bounded time."""
import threading
import time

import pytest
import requests

from services.camera.alpaca.alpaca_probe import ProbeResult, probe_alpaca_camera
from tests.alpaca_fake import make_fake
from tests.alpaca_rig import alpaca_settings

DETAIL_KEYS = {
    "name", "unique_id", "device_number", "sensor_type", "bayer_pattern",
    "physical_bayer_pattern", "width", "height", "max_adu", "bit_depth", "gain_range",
    "offset_range", "exposure_range_s", "interface_version", "driver_info", "imagebytes",
}


def test_probe_summarises_the_camera():
    net, cam, _clock = make_fake(width=1920, height=1080, max_adu=4095)
    result = probe_alpaca_camera(alpaca_settings(net), session_factory=net.session)
    assert isinstance(result, ProbeResult) and result.ok is True
    assert result.message == "Connected: PFR Alpaca Simulator 1920×1080, RGGB, 12-bit"
    d = result.details
    assert DETAIL_KEYS <= set(d)
    assert (d["bayer_pattern"], d["physical_bayer_pattern"]) == ("BGGR", "RGGB")
    assert d["unique_id"] == cam.spec.unique_id and d["device_number"] == 0
    assert d["gain_range"] == (0, 570) and d["interface_version"] == 3
    assert d["imagebytes"] is None


def test_probe_never_configures_and_releases_only_when_asked():
    net, cam, _clock = make_fake()
    probe_alpaca_camera(alpaca_settings(net), session_factory=net.session)
    assert not [r for r in net.requests if r.method == "PUT" and r.member != "connected"]
    assert cam.connected is True
    net2, cam2, _c = make_fake()
    probe_alpaca_camera(alpaca_settings(net2, release_on_disconnect=True),
                        session_factory=net2.session)
    assert cam2.connected is False


@pytest.mark.parametrize("sensor_type,label", [(0, "mono"), (1, "RGB")])
def test_probe_labels_non_bayer_sensors(sensor_type, label):
    net, _cam, _clock = make_fake(sensor_type=sensor_type, max_adu=255)
    result = probe_alpaca_camera(alpaca_settings(net), session_factory=net.session)
    assert result.message.endswith(f", {label}, 8-bit")


def test_probe_reports_why_it_failed():
    net, cam, _clock = make_fake()
    net.faults.refused_hosts.add(net.host)
    result = probe_alpaca_camera(alpaca_settings(net), session_factory=net.session)
    assert result.ok is False and "Cannot reach" in result.message
    assert result.details["host"] == net.host

    net2, cam2, _c = make_fake()
    cam2.configured_devices = lambda: [{"DeviceName": "Roof", "DeviceType": "SafetyMonitor",
                                        "DeviceNumber": 0, "UniqueID": "r"}]
    result = probe_alpaca_camera(alpaca_settings(net2), session_factory=net2.session)
    assert result.ok is False and "has no Camera device" in result.message


@pytest.mark.parametrize("settings", [None, {}, {"host": ""}, {"host": "x", "port": "nope"}])
def test_probe_never_raises(settings):
    result = probe_alpaca_camera(settings)
    assert isinstance(result, ProbeResult) and result.ok is False and result.message


def test_probe_is_bounded_when_the_server_stalls():
    # The stalled request is released (as a refused connection) before the test
    # ends: a worker left asleep used to wake 10 s later, in some other test, and
    # log "'NoneType' object has no attribute 'status_code'".
    release = threading.Event()
    calls = []

    class Stalled:
        def request(self, *a, **k):
            calls.append(1)
            release.wait(10)
            raise requests.ConnectionError("stalled server released")

        def close(self):
            pass

    started = time.monotonic()
    try:
        result = probe_alpaca_camera({"host": "192.0.2.1", "port": 11111},
                                     session_factory=Stalled, deadline_s=0.3)
        assert time.monotonic() - started < 2.0
        assert result.ok is False and "No answer" in result.message
    finally:
        release.set()
    for worker in [t for t in threading.enumerate() if t.name == "alpaca-probe"]:
        worker.join(5.0)
        assert not worker.is_alive()
    assert calls


def test_a_session_that_returns_no_response_is_a_protocol_error_not_a_crash():
    from services.camera.alpaca.alpaca_http import AlpacaHttpClient
    from services.camera.alpaca.alpaca_protocol import AlpacaProtocolError

    class Empty:
        def request(self, *a, **k):
            return None

        def close(self):
            pass

    client = AlpacaHttpClient("192.0.2.1", 11111, 0, session_factory=Empty)
    with pytest.raises(AlpacaProtocolError, match="No usable reply"):
        client.get("name")
