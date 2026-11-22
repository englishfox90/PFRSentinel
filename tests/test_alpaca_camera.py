"""AlpacaCamera + alpaca_capture_frame — metadata, units, clamps, stop/recovery, colour."""
import threading
import time as real_time

import numpy as np
import pytest

from services.camera.alpaca import alpaca_http
from services.camera.alpaca.alpaca_camera import AlpacaCamera, effective_wb_config
from services.camera.capabilities import ALPACA_STATIC
from services.camera.errors import CameraError, CameraTimeoutError
from services.camera.frame_metadata import FRAME_METADATA_KEYS
from tests.alpaca_fake import make_fake
from tests.alpaca_rig import ALPACA_TIME_MODULES, FakeTime, alpaca_settings, make_rig, patch_time
from tests.test_frame_metadata import GOLDEN_KEYS


def _connected(monkeypatch, session_kwargs=None, settings=None, **spec):
    rig = make_rig(monkeypatch, session_kwargs=session_kwargs, settings=settings, **spec)
    assert rig.session.connect_camera(), rig.session._connection.last_error
    rig.session.is_capturing = True
    return rig


def _halves(img):
    a = np.asarray(img).astype(float)
    h = a.shape[0]
    return a[1:h // 2 - 1].reshape(-1, 3).mean(0), a[h // 2 + 1:-1].reshape(-1, 3).mean(0)


def test_metadata_keys_and_order_match_the_zwo_frame(monkeypatch):
    rig = _connected(monkeypatch)
    _img, meta = rig.session.capture_single_frame()
    assert tuple(meta) == FRAME_METADATA_KEYS == GOLDEN_KEYS
    assert meta["CAMERA"] == "PFR Alpaca Simulator"
    assert meta["RES"] == "16x12" and meta["EXPOSURE"] == "0.5s"
    assert meta["TEMP"] == "21.5 C" and meta["CAMERA_BIT_DEPTH"] == 12
    assert meta["IMAGE_BIT_DEPTH"] == 16 and meta["RAW_GEOMETRY"] == (16, 12, 16, "BGGR")
    assert meta["PIXEL_SIZE"] == 2.9 and meta["ELEC_PER_ADU"] == 0.8
    assert meta["EXPOSURE_START_UTC"].startswith("2026-")


def test_duration_sent_is_profile_ms_over_1000(monkeypatch, tmp_path):
    from services.camera.session_factory import build_session
    from services.camera.session_params import read_capture_params
    from services.config import Config
    net, cam, clock = make_fake()
    patch_time(monkeypatch, FakeTime(clock), *ALPACA_TIME_MODULES)
    config = Config(str(tmp_path / "config.json"))
    config.set("camera_source", "alpaca")
    config.set("alpaca_camera", alpaca_settings(net))
    config.get_camera_profile("", f"alpaca:{net.host}:{net.port}:0")["exposure_ms"] = 1250
    params = read_capture_params(config, auto_recovery=True)
    assert params["camera_source"] == "alpaca" and params["exposure_ms"] == 1250
    session = build_session(params, None, 0, None, None)
    assert isinstance(session, AlpacaCamera)
    session._connection._session_factory = net.session
    assert session.connect_camera()
    session.is_capturing = True
    _img, meta = session.capture_single_frame()
    assert cam.puts("startexposure")[-1]["Duration"] == "1.25"
    assert meta["EXPOSURE"] == "1.25s"


def test_auto_exposure_is_clamped_to_max_exposure_with_the_zwo_message(monkeypatch):
    lines = []
    rig = _connected(monkeypatch, session_kwargs={"auto_exposure": True, "max_exposure_sec": 2.0,
                                                  "exposure_sec": 9.0})
    rig.session.on_log_callback = lines.append
    rig.session.capture_single_frame()
    assert "⚠ Exposure 9000ms exceeds max 2000ms — clamping" in lines
    assert rig.session.exposure_seconds == 2.0
    assert rig.puts("startexposure")[-1]["Duration"] == "2.0"


def test_exposure_is_clamped_to_the_device_range(monkeypatch):
    rig = _connected(monkeypatch, exposure_min=0.01, exposure_max=5.0,
                     session_kwargs={"exposure_sec": 0.000001})
    _img, meta = rig.session.capture_single_frame()
    assert rig.puts("startexposure")[-1]["Duration"] == "0.01"
    assert meta["EXPOSURE"] == "0.01s"
    rig.session.exposure_seconds = 60.0
    rig.session.capture_single_frame()
    assert rig.puts("startexposure")[-1]["Duration"] == "5.0"


@pytest.mark.parametrize("offset", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_red_top_blue_bottom_under_every_bayer_offset(monkeypatch, offset):
    rig = _connected(monkeypatch, scene="red_top_blue_bottom", width=32, height=24,
                     bayer_offset_x=offset[0], bayer_offset_y=offset[1])
    img, meta = rig.session.capture_single_frame()
    top, bottom = _halves(img)
    assert top[0] > 4 * top[2] and bottom[2] > 4 * bottom[0]
    assert meta["BAYER_PATTERN"] == rig.session.bayer_pattern


@pytest.mark.parametrize("flip,top_is_red", [(0, True), (1, True), (2, False), (3, False)])
def test_software_flip_keeps_colour_and_moves_the_scene(monkeypatch, flip, top_is_red):
    rig = _connected(monkeypatch, scene="red_top_blue_bottom", width=32, height=24,
                     bayer_offset_x=1, session_kwargs={"flip": flip})
    img, _meta = rig.session.capture_single_frame()
    top, bottom = _halves(img)
    red, blue = (top, bottom) if top_is_red else (bottom, top)
    assert red[0] > 4 * red[2] and blue[2] > 4 * blue[0]


def test_mono_and_rgb_sensors(monkeypatch):
    mono = _connected(monkeypatch, sensor_type=0, scene="red_top_blue_bottom", width=32, height=24)
    img, meta = mono.session.capture_single_frame()
    a = np.asarray(img)
    assert meta["BAYER_PATTERN"] == "MONO"
    assert np.array_equal(a[..., 0], a[..., 1]) and np.array_equal(a[..., 1], a[..., 2])
    rgb = _connected(monkeypatch, sensor_type=1, scene="red_top_blue_bottom", width=32, height=24)
    img, meta = rgb.session.capture_single_frame()
    top, bottom = _halves(img)
    assert meta["BAYER_PATTERN"] == "RGB" and meta["RAW_GEOMETRY"][3] == "RGB"
    assert top[0] > 4 * top[2] and bottom[2] > 4 * bottom[0]


def test_recovery_request_before_and_during_the_exposure(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"exposure_sec": 10.0})
    s = rig.session
    s._recovery_requested = True
    with pytest.raises(Exception, match="Recovery requested by watchdog"):
        s.capture_single_frame()
    assert s._recovery_requested is False and not rig.puts("startexposure")

    original = rig.time.sleep

    def sleep(seconds):
        original(seconds)
        if rig.clock.now > 1003.0:
            s._recovery_requested = True
    monkeypatch.setattr(rig.time, "sleep", sleep)
    with pytest.raises(Exception, match="mid-exposure"):
        s.capture_single_frame()
    assert rig.puts("abortexposure") and s.exposure_start_time is None


def test_stop_during_the_wait_aborts_the_exposure(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"exposure_sec": 30.0})
    original = rig.time.sleep

    def sleep(seconds):
        original(seconds)
        if rig.clock.now > 1005.0:
            rig.session.is_capturing = False
    monkeypatch.setattr(rig.time, "sleep", sleep)
    with pytest.raises(Exception, match="Capture stopped during exposure"):
        rig.session.capture_single_frame()
    assert rig.puts("abortexposure") and rig.cam.state == 0


def test_idle_blip_after_start_is_tolerated(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"exposure_sec": 6.0})
    rig.cam.faults.idle_blip_polls = 4
    _img, meta = rig.session.capture_single_frame()
    assert meta["EXPOSURE"] == "6.0s"


def _fail_polls_after_start(rig, monkeypatch, count):
    device = rig.session.camera
    original = device.start

    def start(*args, **kwargs):
        value = original(*args, **kwargs)
        rig.net.faults.connection_errors = count
        return value
    monkeypatch.setattr(device, "start", start)


def test_three_transient_poll_failures_are_tolerated(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"exposure_sec": 0.5})
    _fail_polls_after_start(rig, monkeypatch, 3)
    _img, meta = rig.session.capture_single_frame()
    assert meta["EXPOSURE"] == "0.5s"


def test_a_fourth_consecutive_poll_failure_raises(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"exposure_sec": 0.5})
    _fail_polls_after_start(rig, monkeypatch, 4)
    with pytest.raises(CameraError, match="Connection refused by"):
        rig.session.capture_single_frame()


def test_exposure_timeout_is_bounded(monkeypatch):
    rig = _connected(monkeypatch, readout_s=1e6, session_kwargs={"exposure_sec": 1.0})
    with pytest.raises(CameraTimeoutError, match="within 31.0s"):
        rig.session.capture_single_frame()
    assert rig.clock.now < 1000 + 40


def test_camera_error_state_fails_the_frame(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"exposure_sec": 5.0})
    rig.cam.faults.fail_next_exposure = True
    with pytest.raises(CameraError, match="CameraState Error"):
        rig.session.capture_single_frame()


def test_download_diagnostics_and_json_fallback(monkeypatch):
    rig = _connected(monkeypatch, supports_imagebytes=False)
    rig.session.capture_single_frame()
    assert rig.session.last_image_format == "json"
    assert rig.session.last_download_bytes > 0 and rig.session.last_download_s is not None
    ib = _connected(monkeypatch)
    ib.session.capture_single_frame()
    assert ib.session.last_image_format == "imagebytes"
    assert ib.session.camera.imagebytes_used is True


def test_gain_is_sent_only_when_it_changes(monkeypatch):
    rig = _connected(monkeypatch, session_kwargs={"gain": 150})
    for _ in range(3):
        rig.session.capture_single_frame()
    rig.session.set_gain(200)
    rig.session.capture_single_frame()
    assert [p["Gain"] for p in rig.puts("gain")] == ["150", "200"]


def test_capabilities_follow_the_device(monkeypatch):
    rig = make_rig(monkeypatch, gain_mode="none", offset_mode="none", ccd_temperature=None,
                   bayer_offset_x=1, bayer_offset_y=1)
    assert rig.session.capabilities is ALPACA_STATIC
    rig.session.connect_camera()
    caps = rig.session.capabilities
    assert caps.backend == "alpaca" and caps.bayer_pattern == "RGGB" and caps.bit_depth == 12
    assert caps.gain is False and caps.offset is False and caps.temperature is False
    assert caps.exposure_range_s == (0.000032, 2000.0)
    assert caps.usb_reset is False and caps.hardware_white_balance is False
    full = _connected(monkeypatch)
    assert full.session.capabilities.gain_range == (0, 570)
    assert full.session.capabilities.offset_range == (0, 255)
    full.session.set_gain(9999)
    assert full.session.gain == 570


def test_profile_bayer_pattern_cannot_clobber_the_device_pattern(monkeypatch):
    rig = _connected(monkeypatch)
    rig.session.bayer_pattern = "GRBG"
    assert rig.session.bayer_pattern == "BGGR"
    rig.session.set_flip_live(1)
    assert rig.session.bayer_pattern == "GBRG"


def test_white_balance_maps_without_rewriting_the_stored_config(monkeypatch):
    stored = {"mode": "asi_auto", "gray_world_low_pct": 5}
    rig = make_rig(monkeypatch, session_kwargs={"wb_config": stored})
    assert rig.session.wb_config["mode"] == "gray_world"
    assert stored == {"mode": "asi_auto", "gray_world_low_pct": 5}
    rig.session.wb_config = {"mode": "manual", "manual_red_gain": 1.2}
    assert rig.session.wb_config["apply_software_gains"] is True
    assert effective_wb_config({"mode": "gray_world"}) == {"mode": "gray_world"}
    rig.session.set_white_balance_live("manual", 50, 60)
    assert (rig.session.wb_mode, rig.session.white_balance_r) == ("manual", 50)


def test_raw16_toggle_is_refused_and_offset_goes_live(monkeypatch):
    rig = _connected(monkeypatch)
    assert rig.session.set_raw16_mode(True) is False
    rig.session.set_offset_live(33)
    assert rig.cam.offset == 33


def test_identity_is_learned_but_not_persisted(monkeypatch):
    rig = _connected(monkeypatch)
    uid = rig.cam.spec.unique_id
    assert rig.session.learned_identity() == (uid, "PFR Alpaca Simulator")
    assert rig.session.camera_serial == f"alpaca:{uid}"
    assert rig.session.initialize_sdk() is True
    assert rig.session.detect_cameras() == [
        f"PFR Alpaca Simulator (Alpaca {rig.net.host}:{rig.net.port} #0)"]


def test_stop_capture_returns_within_three_seconds_during_a_long_download(monkeypatch):
    net, cam, _clock = make_fake(clock=None)
    cam.clock = real_time.monotonic
    monkeypatch.setattr(alpaca_http, "DOWNLOAD_CHUNK", 64)
    downloading = threading.Event()

    def slow_chunk(_sent):
        downloading.set()
        real_time.sleep(0.4)
    net.on_chunk = slow_chunk
    session = AlpacaCamera(alpaca_settings(net), exposure_sec=0.05, session_factory=net.session)
    assert session.connect_camera()
    assert session.start_capture(lambda *_a: None)
    assert downloading.wait(5.0)
    started = real_time.monotonic()
    session.stop_capture()
    assert real_time.monotonic() - started < 3.0
    assert session.capture_thread is None
    session.disconnect_camera()
    assert session.camera is None and cam.connected is True


def test_manual_software_red_gain_scales_the_red_channel(monkeypatch):
    def frame(red_gain):
        stored = {"mode": "manual", "manual_red_gain": red_gain, "manual_blue_gain": 1.0}
        rig = _connected(monkeypatch, scene="red_top_blue_bottom", width=32, height=24,
                         session_kwargs={"wb_config": stored})
        img, meta = rig.session.capture_single_frame()
        assert stored == {"mode": "manual", "manual_red_gain": red_gain, "manual_blue_gain": 1.0}
        assert meta["WB_CONFIG"]["apply_software_gains"] is True
        return _halves(img)[0]
    plain, boosted = frame(1.0), frame(1.5)
    assert boosted[0] == pytest.approx(plain[0] * 1.5, rel=0.05)
    assert boosted[2] == pytest.approx(plain[2], abs=1.0)


def test_zwo_session_wb_config_is_left_as_given():
    from unittest.mock import MagicMock, patch
    from services.camera.zwo_camera import ZWOCamera
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(asi=None, camera=None, sdk_lock=MagicMock())
        cam = ZWOCamera(sdk_path=None, wb_config={"mode": "manual", "manual_red_gain": 1.5})
    assert cam.wb_config == {"mode": "manual", "manual_red_gain": 1.5}


def test_alpaca_params_read_the_shared_zwo_mirror_keys(tmp_path):
    from services.camera.session_params import read_capture_params
    from services.config import Config
    config = Config(str(tmp_path / "config.json"))
    config.set("camera_source", "alpaca")
    config.set("alpaca_camera", {"host": "192.0.2.10", "port": 11111, "device_number": 0})
    config.set("zwo_auto_exposure", True)
    config.set("zwo_interval", 42.0)
    params = read_capture_params(config, auto_recovery=True)
    assert params["auto_exposure"] is True and params["capture_interval"] == 42.0
