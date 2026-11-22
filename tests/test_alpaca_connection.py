"""alpaca_connection — handshake, UniqueID gate, renumbering, teardown and release semantics."""
import pytest

from services.camera.alpaca.alpaca_connection import AlpacaConnection
from services.camera.alpaca.alpaca_protocol import ALPACA_SDK
from services.camera.sdk_lock import SDK_LOCK
from tests.alpaca_fake import make_fake
from tests.alpaca_rig import alpaca_settings

UID = "3b2f6a1e-43a1-4f5e-9c1d-5e1a7c0a0043"


@pytest.fixture
def rig():
    net, cam, clock = make_fake()
    return net, cam


def _conn(net, **overrides):
    return AlpacaConnection(alpaca_settings(net, **overrides), session_factory=net.session)


def test_connect_handshakes_reads_static_and_configures(rig):
    net, cam = rig
    conn = _conn(net)
    assert conn.connect({"gain": 150, "offset": 12, "exposure_sec": 2.0}) is True
    assert cam.connected is True
    assert conn.camera is not None and conn.static_info.width == 16
    assert (cam.gain, cam.offset) == (150, 12)
    assert conn.camera.pending_exposure_s == 2.0
    assert conn.camera_name == "PFR Alpaca Simulator"
    assert conn.asi is ALPACA_SDK
    assert (conn.bit_depth, conn.current_bit_depth, conn.supports_raw16) == (12, 16, False)


def test_connect_learns_the_unique_id_and_profile_key(rig):
    net, _cam = rig
    conn = _conn(net)
    assert conn.camera_serial == f"alpaca:{net.host}:{net.port}:0"
    conn.connect()
    assert conn.unique_id == UID
    assert conn.camera_serial == f"alpaca:{UID}"


def test_renumbered_device_is_found_by_unique_id(rig):
    net, cam = rig
    cam.renumber(3)
    conn = _conn(net, unique_id=UID, device_number=0)
    assert conn.connect() is True
    assert conn.device_number == 3 and conn.renumbered_from == 0
    assert conn.client.device_number == 3
    assert net.requests[-1].url.split("/api/v1/camera/")[1].startswith("3/")


def test_unknown_unique_id_fails_the_identity_gate(rig):
    net, cam = rig
    conn = _conn(net, unique_id="someone-else")
    assert conn.connect() is False
    assert "no longer on" in conn.last_error
    assert conn.camera is None and cam.connected is False


def test_server_without_a_camera_gets_a_clear_error(rig):
    net, cam = rig
    cam.configured_devices = lambda: [{"DeviceName": "PFR Roof", "DeviceType": "SafetyMonitor",
                                       "DeviceNumber": 0, "UniqueID": "x"}]
    conn = _conn(net)
    assert conn.connect() is False
    assert "has no Camera device" in conn.last_error
    assert "SafetyMonitor" in conn.last_error


def test_missing_device_number_lists_what_is_there(rig):
    net, _cam = rig
    conn = _conn(net, device_number=5)
    assert conn.connect() is False
    assert "no camera #5" in conn.last_error and "#0 PFR Alpaca Simulator" in conn.last_error


def test_unreachable_server_and_missing_host_fail_without_raising(rig):
    net, _cam = rig
    net.faults.refused_hosts.add(net.host)
    conn = _conn(net)
    assert conn.connect() is False and "Cannot reach" in conn.last_error
    assert _conn(net, host="").connect() is False


def test_sdk_lock_is_per_instance_never_the_zwo_lock(rig):
    net, _cam = rig
    a, b = _conn(net), _conn(net)
    assert a.sdk_lock is not b.sdk_lock
    assert a.sdk_lock is not SDK_LOCK and b.sdk_lock is not SDK_LOCK


def test_disconnect_is_idempotent_and_keeps_the_device_connected(rig):
    net, cam = rig
    conn = _conn(net)
    conn.connect()
    calls = []
    conn.disconnect(stop_exposure_callback=lambda: calls.append(1), release_sdk=True)
    conn.disconnect()
    conn.disconnect(release_sdk=True)
    assert conn.camera is None and conn.client is None
    assert calls == [1]
    assert cam.connected is True
    assert [p["Connected"] for p in cam.puts("connected")] == ["True"]


def test_release_on_disconnect_sends_connected_false(rig):
    net, cam = rig
    conn = _conn(net, release_on_disconnect=True)
    conn.connect()
    conn.disconnect()
    assert cam.connected is False
    assert [p["Connected"] for p in cam.puts("connected")] == ["True", "False"]


def test_disconnect_aborts_an_exposure_in_flight(rig):
    net, cam = rig
    conn = _conn(net)
    conn.connect()
    conn.camera.start(30.0)
    conn.disconnect()
    assert cam.puts("abortexposure")
    assert cam.state == 0


def test_reconnect_after_the_driver_dropped_the_camera(rig):
    net, cam = rig
    conn = _conn(net)
    conn.connect()
    cam.drop_connection()
    assert conn.reconnect_safe({"gain": 90}) is True
    assert cam.connected is True and cam.gain == 90


def test_verify_identity_against_the_management_list(rig):
    net, cam = rig
    conn = _conn(net)
    conn.connect()
    assert conn.verify_identity() is True
    cam.spec.unique_id = "swapped"
    assert conn.verify_identity() is False


def test_connect_never_touches_config(rig, monkeypatch):
    net, _cam = rig
    import services.config as config_module
    monkeypatch.setattr(config_module.Config, "set",
                        lambda *a, **k: pytest.fail("connection wrote config"))
    monkeypatch.setattr(config_module.Config, "save",
                        lambda *a, **k: pytest.fail("connection saved config"))
    conn = _conn(net)
    assert conn.connect() is True
    conn.disconnect()
