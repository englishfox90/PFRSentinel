"""alpaca_device — native API, optional members, V3/V4 connect, and the zwoasi facade."""
import numpy as np
import pytest

from services.camera.alpaca.alpaca_device import AlpacaCameraDevice
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_protocol import (
    ALPACA_SDK,
    AlpacaNotConnectedError,
    AlpacaNotImplementedError,
)
from tests.alpaca_fake import make_fake
from tests.alpaca_rig import FakeTime, patch_time


@pytest.fixture
def make_device(monkeypatch):
    def build(release=False, **spec):
        spec.setdefault("width", 16)
        spec.setdefault("height", 12)
        net, cam, clock = make_fake(**spec)
        patch_time(monkeypatch, FakeTime(clock), "services.camera.alpaca.alpaca_device")
        client = AlpacaHttpClient(net.host, net.port, 0, session_factory=net.session)
        device = AlpacaCameraDevice(client, release_on_disconnect=release)
        return net, cam, clock, device
    return build


def _connected(make_device, **spec):
    net, cam, clock, device = make_device(**spec)
    device.connect()
    device.read_static()
    return net, cam, clock, device


def test_v3_connect_puts_connected_true(make_device):
    net, cam, _clock, device = make_device(interface_version=3)
    device.connect()
    assert cam.connected is True
    assert [r.params["Connected"] for r in net.member_requests("connected", "PUT")] == ["True"]
    assert not net.member_requests("connect", "PUT")


def test_v4_connect_uses_connect_and_waits_for_connecting(make_device):
    net, cam, _clock, device = make_device(interface_version=4, connect_delay_s=1.0)
    device.connect()
    assert cam.connected is True
    assert net.member_requests("connect", "PUT")
    assert len(net.member_requests("connecting", "GET")) >= 2
    assert not net.member_requests("connected", "PUT")


def test_v4_connect_that_never_finishes_is_bounded(make_device):
    _net, _cam, _clock, device = make_device(interface_version=4, connect_delay_s=1e9)
    with pytest.raises(AlpacaNotConnectedError):
        device.connect(timeout=5.0)


def test_already_connected_device_is_left_alone(make_device):
    net, cam, _clock, device = make_device()
    cam.connected = True
    device.connect()
    assert not net.member_requests("connected", "PUT")


@pytest.mark.parametrize("version,member", [(3, "connected"), (4, "disconnect")])
def test_disconnect_releases_only_when_configured(make_device, version, member):
    net, cam, _clock, device = make_device(interface_version=version)
    device.connect()
    assert device.disconnect() is False
    assert cam.connected is True
    net2, cam2, _c, released = make_device(interface_version=version, release=True)
    released.connect()
    assert released.disconnect() is True
    assert cam2.connected is False
    assert net2.member_requests(member, "PUT")


def test_static_info_reads_sensor_facts(make_device):
    _net, _cam, _clock, device = _connected(make_device, bayer_offset_x=1, bayer_offset_y=0,
                                            max_adu=4095)
    info = device.info
    assert (info.width, info.height, info.max_adu) == (16, 12, 4095)
    assert info.physical_bayer_pattern == "GRBG"
    assert info.bayer_pattern == "GBRG"
    assert (info.sensor_bit_depth, info.transfer_bit_depth) == (12, 16)
    assert info.gain_range == (0, 570) and info.offset_range == (0, 255)
    assert info.exposure_range_s == (0.000032, 2000.0)
    assert info.has_temperature is True and info.interface_version == 3


def test_optional_members_not_implemented_switch_capabilities_off(make_device):
    _net, _cam, _clock, device = _connected(make_device, gain_mode="none", offset_mode="none",
                                            ccd_temperature=None, sensor_type=0)
    info = device.info
    assert info.gain_mode is None and info.gain_range is None
    assert info.offset_mode is None and info.offset_range is None
    assert info.has_temperature is False
    assert info.bayer_pattern == "MONO"
    assert device.ccd_temperature() is None
    assert device.set_gain(100) is None


def test_gain_list_mode_is_an_index_range(make_device):
    net, _cam, _clock, device = _connected(make_device, gain_mode="list")
    assert device.info.gain_mode == "list"
    assert device.info.gains == ("Low", "Medium", "High")
    assert device.info.gain_range == (0, 2)
    device.set_gain(7)
    assert net.member_requests("gain", "PUT")[-1].params["Gain"] == "2"


def test_gain_is_clamped_and_only_sent_when_it_changes(make_device):
    net, cam, _clock, device = _connected(make_device)
    assert device.set_gain(9999) is True
    assert cam.gain == 570
    device.set_gain(9999)
    device.set_gain(570)
    assert len(net.member_requests("gain", "PUT")) == 1
    device.set_gain(120)
    assert cam.gain == 120


def test_full_frame_roi_uses_canonical_casing(make_device):
    net, cam, _clock, device = _connected(make_device)
    cam.num_x, cam.start_x, cam.bin_x = 4, 2, 2
    device.set_full_frame_roi()
    assert (cam.bin_x, cam.bin_y, cam.start_x, cam.start_y) == (1, 1, 0, 0)
    assert (cam.num_x, cam.num_y) == (16, 12)
    assert net.member_requests("numx", "PUT")[-1].params["NumX"] == "16"


def test_set_control_value_exposure_is_microseconds_to_seconds(make_device):
    net, _cam, _clock, device = _connected(make_device)
    device.set_control_value(ALPACA_SDK.ASI_EXPOSURE, 250_000)
    assert device.pending_exposure_s == pytest.approx(0.25)
    assert device.get_control_value(ALPACA_SDK.ASI_EXPOSURE) == (250_000, False)
    device.start_exposure()
    assert net.member_requests("startexposure", "PUT")[-1].params["Duration"] == "0.25"


def test_exposure_is_clamped_to_the_device_range(make_device):
    _net, _cam, _clock, device = _connected(make_device, exposure_min=0.001, exposure_max=60.0)
    device.set_control_value(ALPACA_SDK.ASI_EXPOSURE, 1)
    assert device.pending_exposure_s == 0.001
    device.set_control_value(ALPACA_SDK.ASI_EXPOSURE, 3_600_000_000)
    assert device.pending_exposure_s == 60.0


def test_exposure_status_follows_the_device(make_device):
    _net, cam, clock, device = _connected(make_device)
    device.set_pending_exposure(1.0)
    device.start_exposure()
    assert device.get_exposure_status() == ALPACA_SDK.ASI_EXP_WORKING
    clock.advance(1.1)
    assert device.get_exposure_status() == ALPACA_SDK.ASI_EXP_SUCCESS
    cam.faults.fail_next_exposure = True
    device.start_exposure()
    clock.advance(1.1)
    assert device.get_exposure_status() == ALPACA_SDK.ASI_EXP_FAILED


def test_idle_blip_after_start_reads_as_working_then_idle_after_grace(make_device):
    _net, cam, clock, device = _connected(make_device)
    device.set_pending_exposure(1.0)
    cam.faults.idle_blip_polls = 2
    device.start_exposure()
    assert device.get_exposure_status() == ALPACA_SDK.ASI_EXP_WORKING
    device.abort()
    cam.state = 0
    clock.advance(5.0)
    assert device.get_exposure_status() == ALPACA_SDK.ASI_EXP_IDLE


def test_data_after_exposure_is_msb_aligned_row_major(make_device):
    _net, _cam, clock, device = _connected(make_device, scene="coords", max_adu=4095)
    device.set_pending_exposure(0.1)
    device.start_exposure()
    clock.advance(0.2)
    data = device.get_data_after_exposure()
    arr = np.frombuffer(data, dtype="<u2").reshape(12, 16)
    expected = (np.arange(16 * 12).reshape(12, 16) % 4096) << 4
    assert np.array_equal(arr, expected)


def test_property_roi_and_dropped_frames(make_device):
    _net, _cam, _clock, device = _connected(make_device, max_adu=255)
    prop = device.get_camera_property()
    assert (prop["MaxWidth"], prop["MaxHeight"], prop["BitDepth"]) == (16, 12, 8)
    assert prop["IsColorCam"] is True and prop["Name"] == "PFR Alpaca Simulator"
    assert device.get_roi_format() == (16, 12, 1, ALPACA_SDK.ASI_IMG_RAW8)
    assert device.get_dropped_frames() == 0


def test_temperature_through_the_facade_is_tenths(make_device):
    _net, _cam, _clock, device = _connected(make_device, ccd_temperature=-4.26)
    assert device.get_control_value(ALPACA_SDK.ASI_TEMPERATURE) == (-43, False)
    _net, _cam, _clock, cold = _connected(make_device, ccd_temperature=None)
    with pytest.raises(AlpacaNotImplementedError):
        cold.get_control_value(ALPACA_SDK.ASI_TEMPERATURE)


def test_abort_falls_back_to_stop_and_only_for_our_exposure(make_device):
    net, _cam, _clock, device = _connected(make_device, can_abort=False, can_stop=True)
    device.stop_exposure()
    assert not net.member_requests("stopexposure", "PUT")
    device.start(1.0)
    device.stop_exposure()
    assert net.member_requests("stopexposure", "PUT")
    assert not net.member_requests("abortexposure", "PUT")


def test_close_never_releases_by_default(make_device):
    net, cam, _clock, device = _connected(make_device)
    device.close()
    assert cam.connected is True
    assert not [r for r in net.member_requests("connected", "PUT")
                if r.params["Connected"] == "False"]
