"""The fake Alpaca camera (scripts/dev/alpaca_sim) through our client — spec behaviour and faults.

The later Alpaca device/connection/session tests build on this fake, so its
spec-faithfulness is pinned here: state machine on an injected clock, error
numbers, gain/offset modes, InterfaceVersion 4 Connect, and every fault switch.
"""
import pytest

from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_protocol import (
    AlpacaInvalidOperationError,
    AlpacaInvalidValueError,
    AlpacaNotConnectedError,
    AlpacaNotImplementedError,
    CameraState,
)
from services.camera.errors import CameraError
from tests.alpaca_fake import make_fake


def _rig(**spec):
    net, cam, clock = make_fake(**spec)
    client = AlpacaHttpClient(net.host, net.port, cam.device_number, session_factory=net.session)
    return net, cam, clock, client


def test_disconnected_camera_members_answer_0x407_but_common_members_work():
    net, cam, clock, client = _rig()
    for member in ("name", "description", "driverinfo", "driverversion", "interfaceversion",
                   "supportedactions", "connected"):
        client.get(member)
    for member in ("cameraxsize", "sensortype", "imageready", "camerastate", "gain"):
        with pytest.raises(AlpacaNotConnectedError):
            client.get(member)
    with pytest.raises(AlpacaNotConnectedError):
        client.put("startexposure", Duration=1.0, Light=True)


def test_exposure_state_machine_on_the_injected_clock():
    net, cam, clock, client = _rig(readout_s=0.5)
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=2.0, Light=True)
    assert client.get("camerastate") == CameraState.EXPOSING
    clock.advance(1.0)
    assert client.get("percentcompleted") == 50
    assert client.get("imageready") is False
    clock.advance(1.2)
    assert client.get("camerastate") == CameraState.READING
    clock.advance(0.5)
    assert client.get("camerastate") == CameraState.IDLE and client.get("imageready") is True
    assert client.get("lastexposureduration") == 2.0
    assert client.get("lastexposurestarttime").count(":") == 2
    assert client.get_image(timeout=5).shape == (12, 16)


def test_wrong_moment_and_bad_values():
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaInvalidOperationError):
        client.get("lastexposureduration")
    with pytest.raises(AlpacaInvalidOperationError):
        client.get("percentcompleted")
    with pytest.raises(AlpacaInvalidValueError):
        client.put("startexposure", Duration=5000.0, Light=True)
    client.put("numx", NumX=99)
    with pytest.raises(AlpacaInvalidValueError):
        client.put("startexposure", Duration=1.0, Light=True)
    client.put("numx", NumX=16)
    client.put("binx", BinX=2)
    with pytest.raises(AlpacaInvalidOperationError):         # asymmetric bin
        client.put("startexposure", Duration=1.0, Light=True)
    with pytest.raises(AlpacaInvalidValueError, match="HTTP 400"):
        client.put("startexposure", Duration="abc", Light=True)


def test_abort_discards_and_stop_keeps_the_image():
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=10.0, Light=True)
    clock.advance(1.0)
    client.put("abortexposure")
    assert client.get("imageready") is False and client.get("camerastate") == 0
    client.put("startexposure", Duration=10.0, Light=True)
    clock.advance(3.0)
    client.put("stopexposure")
    assert client.get("imageready") is True and client.get("lastexposureduration") == 3.0


def test_cannot_abort_or_stop_is_not_implemented():
    net, cam, clock, client = _rig(can_abort=False, can_stop=False)
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaNotImplementedError):
        client.put("abortexposure")
    with pytest.raises(AlpacaNotImplementedError):
        client.put("stopexposure")


def test_mono_has_no_bayer_offsets_and_colour_is_rank3():
    net, cam, clock, client = _rig(sensor_type=0)
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaNotImplementedError):
        client.get("bayeroffsetx")
    net, cam, clock, client = _rig(sensor_type=1)
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaNotImplementedError):
        client.get("bayeroffsety")
    client.put("startexposure", Duration=0.1, Light=True)
    clock.advance(0.2)
    assert client.get_image(timeout=5).shape == (12, 16, 3)


def test_gain_and_offset_modes():
    net, cam, clock, client = _rig(gain_mode="list", offset_mode="none", ccd_temperature=None)
    client.put("connected", Connected=True)
    assert client.get("gains") == ["Low", "Medium", "High"]
    for member in ("gainmin", "gainmax", "offset", "offsetmin", "offsets", "ccdtemperature"):
        with pytest.raises(AlpacaNotImplementedError):
            client.get(member)
    client.put("gain", Gain=2)
    assert client.get("gain") == 2
    with pytest.raises(AlpacaInvalidValueError):
        client.put("gain", Gain=3)
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    assert (client.get("gainmin"), client.get("gainmax")) == (0, 570)
    with pytest.raises(AlpacaNotImplementedError):
        client.get("gains")
    assert (client.get("offsetmin"), client.get("offsetmax")) == (0, 255)


def test_interface_version_4_connect_is_asynchronous():
    net, cam, clock, client = _rig(interface_version=4, connect_delay_s=1.0)
    client.put("connect")
    assert client.get("connecting") is True and client.get("connected") is False
    clock.advance(1.0)
    assert client.get("connecting") is False and client.get("connected") is True
    assert client.get("devicestate")[0]["Name"] == "CameraState"
    net, cam, clock, client = _rig(interface_version=3)
    with pytest.raises(AlpacaNotImplementedError):
        client.get("connecting")


def test_fault_not_connected_for_n_requests_then_dropped_connection():
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    cam.faults.not_connected_requests = 2
    for _ in range(2):
        with pytest.raises(AlpacaNotConnectedError):
            client.get("cameraxsize")
    assert client.get("cameraxsize") == 16
    cam.drop_connection()
    with pytest.raises(AlpacaNotConnectedError):
        client.get("cameraxsize")
    client.put("connected", Connected=True)
    assert client.get("cameraxsize") == 16


def test_fault_idle_blip_after_start():
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    cam.faults.idle_blip_polls = 2
    client.put("startexposure", Duration=1.0, Light=True)
    assert client.get("camerastate") == CameraState.IDLE
    assert client.get("imageready") is False
    assert client.get("camerastate") == CameraState.IDLE
    assert client.get("camerastate") == CameraState.EXPOSING
    clock.advance(1.0)
    assert client.get("imageready") is True


def test_fault_failed_exposure_reads_error_state():
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    cam.faults.fail_next_exposure = True
    client.put("startexposure", Duration=1.0, Light=True)
    clock.advance(1.0)
    assert client.get("camerastate") == CameraState.ERROR and client.get("imageready") is False
    client.put("startexposure", Duration=1.0, Light=True)    # a new exposure clears it
    clock.advance(1.0)
    assert client.get("imageready") is True


def test_fault_renumbered_device_and_transport_faults():
    net, cam, clock, client = _rig()
    cam.renumber(4)
    with pytest.raises(AlpacaInvalidValueError, match="HTTP 400"):
        client.get("name")
    assert client.management_configured_devices()[0]["DeviceNumber"] == 4
    client.device_number = 4
    assert client.get("name")
    net.faults.connection_errors = 3
    for _ in range(3):
        with pytest.raises(CameraError):
            client.get("name")
    assert client.get("name")


def test_request_log_records_put_parameters():
    net, cam, clock, client = _rig()
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=0.25, Light=True)
    assert cam.puts("startexposure")[0]["Duration"] == "0.25"
    assert cam.puts("connected")[0]["Connected"] == "True"
