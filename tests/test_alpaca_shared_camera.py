"""An Alpaca camera shared with another client (NINA through ASCOM Remote), and the
driver quirks the review found: abort only our own exposure, re-assert gain /
offset / full-frame ROI before every frame, drop a frame that is not ours, the
Idle-before-ImageReady race, optional members that throw driver errors,
CCDTemperature during readout, MaxADU, and calibration on a slow readout."""
import threading
import time

import numpy as np
import pytest

from services import posthog_service
from services.camera.alpaca.alpaca_capture_frame import wait_for_image
from services.camera.alpaca.alpaca_device import AlpacaCameraDevice
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_protocol import ALPACA_SDK, CameraState
from services.camera.errors import FrameSkipped
from tests.alpaca_rig import make_rig


@pytest.fixture(autouse=True)
def _no_analytics(monkeypatch):
    monkeypatch.setattr(posthog_service, "capture_event", lambda *a, **k: None)


def _session(monkeypatch, session_kwargs=None, **spec):
    spec.setdefault("width", 32)
    spec.setdefault("height", 24)
    rig = make_rig(monkeypatch, session_kwargs=session_kwargs, **spec)
    assert rig.session.connect_camera(), rig.session._connection.last_error
    rig.session.is_capturing = True
    return rig


def _nina(rig):
    return AlpacaHttpClient(rig.net.host, rig.net.port, 0, session_factory=rig.net.session)


# -- abort only our own exposure ---------------------------------------------------
def test_failed_download_never_aborts_the_other_clients_exposure(monkeypatch):
    rig = _session(monkeypatch)
    rig.net.faults.download_timeouts = 1
    with pytest.raises(Exception):
        rig.session.capture_single_frame()
    nina = _nina(rig)
    nina.put("startexposure", Duration=300.0, Light=True)
    rig.clock.advance(1.0)
    rig.session._connection.disconnect()
    rig.session.stop_capture()
    assert not rig.net.member_requests("abortexposure", "PUT")
    assert nina.get("camerastate") == CameraState.EXPOSING


def test_our_running_exposure_is_still_aborted_on_stop(monkeypatch):
    rig = _session(monkeypatch)
    device = rig.session.camera
    device.start(30.0)
    assert device.exposure_in_flight
    rig.session.exposure_start_time = time.time()
    rig.session.stop_capture()
    assert len(rig.net.member_requests("abortexposure", "PUT")) == 1
    assert rig.cam.state == CameraState.IDLE and not device.exposure_in_flight


def test_image_ready_marks_our_exposure_finished(monkeypatch):
    rig = _session(monkeypatch)
    device = rig.session.camera
    device.start(1.0)
    rig.clock.advance(1.5)
    assert device.image_ready() is True and not device.exposure_in_flight
    device.abort()
    assert not rig.net.member_requests("abortexposure", "PUT")


def test_start_refused_because_another_client_is_exposing_skips_the_frame(monkeypatch):
    rig = _session(monkeypatch)
    _nina(rig).put("startexposure", Duration=300.0, Light=True)
    with pytest.raises(FrameSkipped, match="another client"):
        rig.session.capture_single_frame()
    assert not rig.session.camera.exposure_in_flight
    assert not rig.net.member_requests("abortexposure", "PUT")


def test_abort_goes_through_the_control_session(monkeypatch):
    rig = _session(monkeypatch)
    device = rig.session.camera
    device.start(30.0)
    client = rig.session._connection.client
    with client._lock:   # the capture thread's poll holds the ordinary session
        device.abort()
    assert len(rig.net.member_requests("abortexposure", "PUT")) == 1


# -- re-assert gain / offset / ROI ---------------------------------------------------
def test_gain_offset_and_roi_are_reasserted_before_every_frame(monkeypatch):
    rig = _session(monkeypatch, session_kwargs={"gain": 100, "offset": 20})
    rig.session.capture_single_frame()
    nina = _nina(rig)
    nina.put("gain", Gain=300)
    nina.put("offset", Offset=50)
    nina.put("binx", BinX=2)
    nina.put("biny", BinY=2)
    nina.put("numx", NumX=15)
    nina.put("numy", NumY=11)
    nina.put("startx", StartX=1)
    img, meta = rig.session.capture_single_frame()
    assert (rig.cam.gain, rig.cam.offset) == (100, 20)
    assert (rig.cam.bin_x, rig.cam.bin_y, rig.cam.start_x, rig.cam.num_x, rig.cam.num_y) == \
        (1, 1, 0, 32, 24)
    assert img.size == (32, 24) and meta["RES"] == "32x24"


def test_roi_refused_mid_exposure_does_not_fail_connect(monkeypatch):
    rig = make_rig(monkeypatch, width=32, height=24, roi_locked_while_exposing=True)
    nina = _nina(rig)
    nina.put("connected", Connected=True)
    nina.put("numx", NumX=10)
    nina.put("startexposure", Duration=300.0, Light=True)
    assert rig.session.connect_camera(), rig.session._connection.last_error
    rig.session.is_capturing = True
    with pytest.raises(FrameSkipped, match="busy"):
        rig.session.capture_single_frame()
    nina.put("abortexposure")
    img, _meta = rig.session.capture_single_frame()
    assert img.size == (32, 24)


def test_delivered_image_of_the_wrong_shape_is_dropped_and_roi_restored(monkeypatch):
    rig = _session(monkeypatch)
    device = rig.session.camera
    original = device.roi_mismatches
    device.roi_mismatches = lambda: []   # NINA changes it after our check
    nina = _nina(rig)

    def change_after_start(request):
        if request.member == "startexposure" and request.method == "PUT":
            rig.net.request_hook = None
            nina.put("binx", BinX=2)
            nina.put("biny", BinY=2)
            nina.put("numx", NumX=16)
            nina.put("numy", NumY=12)
    rig.net.request_hook = change_after_start
    with pytest.raises(FrameSkipped, match="16x12"):
        rig.session.capture_single_frame()
    device.roi_mismatches = original
    assert (rig.cam.bin_x, rig.cam.num_x, rig.cam.num_y) == (1, 32, 24)
    img, _meta = rig.session.capture_single_frame()
    assert img.size == (32, 24)


def test_an_image_from_another_exposure_is_dropped(monkeypatch):
    rig = _session(monkeypatch)
    real = rig.session.camera.last_exposure_duration
    rig.session.camera.last_exposure_duration = lambda: 120.0
    with pytest.raises(FrameSkipped, match="not this frame"):
        rig.session.capture_single_frame()
    rig.session.camera.last_exposure_duration = real
    rig.session.capture_single_frame()


def test_a_driver_that_always_reports_another_duration_is_ignored(monkeypatch):
    rig = _session(monkeypatch)
    rig.session.camera.last_exposure_duration = lambda: 500.0   # milliseconds, say
    for _ in range(2):
        with pytest.raises(FrameSkipped):
            rig.session.capture_single_frame()
    img, _meta = rig.session.capture_single_frame()
    assert img.size == (32, 24)


# -- Idle before ImageReady -------------------------------------------------------
class _Session:
    is_capturing = True
    camera = object()
    _recovery_requested = False
    exposure_remaining = 0.0

    def log(self, _m):
        pass


def test_idle_then_image_ready_is_not_a_lost_exposure():
    calls = []

    class Device:
        def image_ready(self):
            calls.append("ready")
            return len(calls) > 2

        def camera_state(self):
            calls.append("state")
            return CameraState.IDLE

        def abort(self):
            raise AssertionError("aborted")

        def mark_finished(self):
            pass

    wait_for_image(_Session(), Device(), 0.5, time.monotonic() - 10.0)
    assert calls == ["ready", "state", "ready"]


def test_facade_status_rereads_image_ready_on_idle(monkeypatch):
    rig = _session(monkeypatch)
    device = rig.session.camera
    device.set_pending_exposure(0.5)
    device.start_exposure()
    rig.clock.advance(10.0)
    reads = iter([False, True])
    device.image_ready = lambda: next(reads)
    device.camera_state = lambda: CameraState.IDLE
    assert device.get_exposure_status() == ALPACA_SDK.ASI_EXP_SUCCESS


# -- optional members and temperature --------------------------------------------
@pytest.mark.parametrize("member", ["ccdtemperature", "offset", "offsetmin", "sensorname",
                                    "electronsperadu", "lastexposureduration"])
def test_an_optional_member_that_throws_a_driver_error_reads_as_absent(monkeypatch, member):
    rig = make_rig(monkeypatch, width=32, height=24)
    rig.cam.faults.driver_error_members = {member}
    assert rig.session.connect_camera(), rig.session._connection.last_error
    rig.session.is_capturing = True
    img, _meta = rig.session.capture_single_frame()
    assert img.size == (32, 24)


def test_a_required_member_error_still_fails_connect(monkeypatch):
    rig = make_rig(monkeypatch, width=32, height=24)
    rig.cam.faults.driver_error_members = {"cameraxsize"}
    assert rig.session.connect_camera() is False
    assert "0x500" in rig.session.last_connect_error


def test_temperature_invalid_operation_during_readout_is_one_missing_reading(monkeypatch):
    rig = _session(monkeypatch)
    rig.cam.faults.invalid_operation_reads = {"ccdtemperature": 1}
    _img, meta = rig.session.capture_single_frame()
    assert meta["TEMP"] == "N/A"
    _img, meta = rig.session.capture_single_frame()
    assert meta["TEMP"] == "21.5 C"


# -- MaxADU -------------------------------------------------------------------------
def test_pixels_above_max_adu_warn_once_per_session(monkeypatch, caplog):
    rig = _session(monkeypatch, max_adu=4095, scene="coords")
    rig.session._connection.static_info = rig.session._connection.static_info.__class__(
        **{**rig.session._connection.static_info.__dict__, "max_adu": 255 + 256})
    for _ in range(2):
        rig.session.capture_single_frame()
    assert sum("above its MaxADU" in r.getMessage() for r in caplog.records) == 1
    rig.session.reconnect_camera_safe()
    rig.session._connection.static_info = rig.session._connection.static_info.__class__(
        **{**rig.session._connection.static_info.__dict__, "max_adu": 511})
    rig.session.capture_single_frame()
    assert sum("above its MaxADU" in r.getMessage() for r in caplog.records) == 1


def test_max_adu_65536_keeps_16_bit_values(monkeypatch):
    rig = _session(monkeypatch, max_adu=65536)
    assert rig.session._connection.static_info.sensor_bit_depth == 16
    _img, meta = rig.session.capture_single_frame()
    assert meta["CAMERA_BIT_DEPTH"] == 16


def test_session_warnings_survive_a_reconnect(monkeypatch, caplog):
    rig = _session(monkeypatch, sensor_type=4)
    device_before = rig.session.camera
    rig.session.reconnect_camera_safe()
    assert rig.session.camera is not device_before
    assert rig.session.camera.warnings is device_before.warnings


# -- calibration -------------------------------------------------------------------
def test_calibration_converges_with_a_slow_readout(monkeypatch):
    rig = _session(monkeypatch, session_kwargs={"exposure_sec": 0.002, "gain": 500,
                                                "auto_exposure": True,
                                                "max_exposure_sec": 2.0},
                   width=64, height=64, max_adu=4095, seed=7, readout_s=2.5)
    session = rig.session
    n_before = len(rig.net.requests)
    assert session.calibration_manager.run_calibration(max_attempts=6) is True
    starts = rig.net.member_requests("startexposure", "PUT")
    attempts = len(session.calibration_manager.calibration_history)
    assert len(starts) == attempts
    # Polls at >= 0.25 s through a 2.5 s readout: not 40 requests a second.
    per_attempt = (len(rig.net.requests) - n_before) / attempts
    assert per_attempt < 25
    assert not rig.net.member_requests("abortexposure", "PUT")


def test_calibration_abort_during_readout_aborts_our_exposure(monkeypatch):
    rig = _session(monkeypatch, session_kwargs={"exposure_sec": 0.002, "auto_exposure": True},
                   readout_s=60.0)
    manager = rig.session.calibration_manager
    original_sleep = rig.time.sleep

    def sleep(seconds):
        original_sleep(seconds)
        if rig.clock.now - start > 5.0:
            manager.abort()
    rig.time.sleep = sleep
    start = rig.clock.now
    assert manager.run_calibration(max_attempts=3) is False
    assert len(rig.net.member_requests("abortexposure", "PUT")) == 1


def test_calibration_hook_is_not_installed_on_zwo():
    from services.camera.camera_calibration import CameraCalibration
    assert CameraCalibration(object(), object()).exposure_hook is None


def test_live_setting_does_not_wait_behind_the_capture_threads_request(monkeypatch):
    rig = _session(monkeypatch)
    client = rig.session._connection.client
    done = threading.Event()
    with client._lock:
        threading.Thread(target=lambda: (rig.session.set_offset_live(33), done.set()),
                         daemon=True).start()
        assert done.wait(2.0)
    assert rig.cam.offset == 33


def test_device_without_session_warnings_gets_its_own():
    assert AlpacaCameraDevice(object()).warnings is not None


def test_frame_bytes_are_a_view_of_one_pixel_copy(monkeypatch):
    rig = _session(monkeypatch, max_adu=4095, scene="coords")
    _img, meta = rig.session.capture_single_frame()
    raw = meta["RAW_BAYER"]
    assert isinstance(raw, memoryview) and raw.nbytes == 32 * 24 * 2
    pixels = np.frombuffer(raw, dtype="<u2").reshape(24, 32)
    assert int(pixels[0, 1]) == 1 << 4


def test_stop_aborts_an_exposure_whose_start_put_is_still_blocking(monkeypatch):
    # Some legacy drivers hold the StartExposure PUT for the whole exposure.
    # Stop must still reach AbortExposure within its bound: the PUT is not
    # under the session lock, and abort has its own control session.
    rig = _session(monkeypatch)
    s = rig.session
    blocked, released = threading.Event(), threading.Event()

    def hook(request):
        if request.member == "startexposure":
            blocked.set()
            released.wait(5.0)
        elif request.member == "abortexposure":
            released.set()
    rig.net.request_hook = hook
    worker = threading.Thread(target=lambda: _swallow(s.capture_single_frame), daemon=True)
    worker.start()
    assert blocked.wait(2.0)
    started = time.monotonic()
    s.stop_capture()
    assert time.monotonic() - started < 2.5
    assert released.is_set() and len(rig.net.member_requests("abortexposure", "PUT")) == 1
    worker.join(2.0)
    assert not worker.is_alive()


def _swallow(fn):
    try:
        fn()
    except Exception:
        pass


def test_connect_and_start_puts_get_room_for_slow_drivers(monkeypatch):
    rig = make_rig(monkeypatch, width=32, height=24, interface_version=3)
    assert rig.session.connect_camera()
    connect = rig.net.member_requests("connected", "PUT")[0]
    assert connect.timeout[1] >= 30.0           # a V3 driver opens the camera in this PUT
    rig.session.is_capturing = True
    rig.session.exposure_seconds = 45.0
    rig.session.capture_single_frame()
    start = rig.net.member_requests("startexposure", "PUT")[-1]
    assert start.timeout[1] >= 45.0 + 10.0      # some drivers block for the whole exposure


def test_a_server_that_ignores_imagebytes_is_warned_about_once_per_session(monkeypatch, caplog):
    rig = _session(monkeypatch, supports_imagebytes=False)
    rig.session.capture_single_frame()
    rig.session.reconnect_camera_safe()
    rig.session.capture_single_frame()
    assert sum("ignored the ImageBytes request" in r.getMessage()
               for r in caplog.records) == 1
