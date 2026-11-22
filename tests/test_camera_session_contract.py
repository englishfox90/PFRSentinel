"""The capture-session contract a camera backend must meet (issue #43).

``ZWOCamera`` is the reference session: the controller, the shared capture
loop, the UI readers of ``controller.zwo_camera`` and the status API all reach
these members. A second backend has to carry the same ones. The handle side
is the zwoasi ``Camera`` subset the loop and ``CameraCalibration`` call.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest

SESSION_ATTRIBUTES = (
    'backend', 'capabilities', 'camera_name', 'camera_serial',
    'is_capturing', 'capture_thread', 'camera', 'asi', '_connection',
    'exposure_seconds', 'exposure_start_time', 'exposure_remaining', 'gain',
    'offset', 'flip', 'bayer_pattern', 'wb_config', 'wb_mode', 'use_raw16',
    'auto_exposure', 'max_exposure', 'target_brightness', 'capture_interval',
    'calibration_manager', 'calibration_mode', 'calibration_complete',
    'auto_recovery_enabled', 'scheduled_capture_mode', 'scheduled_capture_enabled',
    'scheduled_start_time', 'scheduled_end_time', 'scheduled_window_interval',
    'schedule_gate', 'supports_raw16', 'sensor_bit_depth', 'current_bit_depth',
    'camera_info', '_recovery_requested',
    'on_frame_callback', 'on_log_callback', 'on_error_callback',
    'on_calibration_callback', 'status_callback',
    'effective_capture_interval',
)

SESSION_METHODS = (
    'initialize_sdk', 'detect_cameras', 'connect_camera', 'reconnect_camera_safe',
    'disconnect_camera', 'start_capture', 'stop_capture',
    'wait_for_capture_thread_exit', 'capture_single_frame', 'capture_loop',
    'run_calibration', 'adjust_exposure_auto', 'is_within_scheduled_window',
    'is_in_time_window', 'scheduled_window_label', 'request_immediate_capture',
    'consume_immediate_capture', 'set_exposure', 'set_gain',
    'set_capture_interval', 'set_offset_live', 'set_flip_live',
    'set_white_balance_live', 'set_raw16_mode', 'log',
)

HANDLE_METHODS = (
    'start_exposure', 'stop_exposure', 'get_exposure_status',
    'get_data_after_exposure', 'get_camera_property', 'set_control_value',
    'get_control_value', 'get_roi_format', 'get_dropped_frames', 'close',
)

SDK_CONSTANTS = {
    'ASI_EXP_IDLE': 0, 'ASI_EXP_WORKING': 1, 'ASI_EXP_SUCCESS': 2,
    'ASI_EXP_FAILED': 3, 'ASI_GAIN': 0, 'ASI_EXPOSURE': 1,
    'ASI_BRIGHTNESS': 5, 'ASI_TEMPERATURE': 8, 'ASI_FLIP': 9,
    'ASI_IMG_RAW8': 0, 'ASI_IMG_RAW16': 2,
}

ZWO_TROUBLESHOOTING_LINES = (
    "  1. Check USB cable connection",
    "  2. Check camera power supply",
    "  3. Try: Physically disconnect USB, wait 5 seconds, reconnect",
    "  4. Check Windows Device Manager for USB errors",
    "  5. If persistent: Update ZWO drivers from astronomy-imaging-camera.com",
)


def _zwo_session():
    from services.camera.zwo_camera import ZWOCamera
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(asi=None, camera=None, sdk_lock=MagicMock())
        return ZWOCamera(sdk_path=None)


@pytest.mark.parametrize('name', SESSION_ATTRIBUTES)
def test_zwo_session_has_attribute(name):
    assert hasattr(_zwo_session(), name)


@pytest.mark.parametrize('name', SESSION_METHODS)
def test_zwo_session_has_method(name):
    assert callable(getattr(_zwo_session(), name))


@pytest.mark.parametrize('name', HANDLE_METHODS)
def test_zwoasi_camera_covers_the_handle(name):
    zwoasi = pytest.importorskip('zwoasi')
    assert callable(getattr(zwoasi.Camera, name))


def test_zwoasi_constants_are_the_values_the_handle_contract_names():
    zwoasi = pytest.importorskip('zwoasi')
    assert {k: getattr(zwoasi, k) for k in SDK_CONSTANTS} == SDK_CONSTANTS


# Wherever the loop body lives, its `time` is the one to fake.
_LOOP_MODULES = ('services.camera.zwo_capture_worker', 'services.camera.capture_loop')


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _DeadCamera:
    """Every frame and reconnect fails until the loop reaches long-retry mode."""

    def __init__(self, **extra):
        self.lines = []
        self.auto_recovery_enabled = True
        self.is_capturing = True
        self.scheduled_capture_mode = 'always'
        self.auto_exposure = False
        self.calibration_complete = True
        self.calibration_manager = None
        self.camera = MagicMock()
        self._connection = MagicMock()
        self.status_callback = None
        self.on_frame_callback = None
        self.on_error_callback = None
        self.effective_capture_interval = 1.0
        self.__dict__.update(extra)

    def log(self, msg):
        self.lines.append(msg)
        if msg.startswith("⏳ Entering long-interval retry mode"):
            self.is_capturing = False

    def is_within_scheduled_window(self):
        return True

    def consume_immediate_capture(self):
        return False

    def capture_single_frame(self):
        raise Exception("Camera closed")

    def reconnect_camera_safe(self):
        return False


@pytest.fixture
def run_loop(monkeypatch):
    for name in _LOOP_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, 'time'):
            monkeypatch.setattr(module, 'time', _Clock())
        if hasattr(module, 'capture_error'):
            monkeypatch.setattr(module, 'capture_error', lambda *a, **k: None)
    from services.camera import zwo_capture_worker
    return zwo_capture_worker.capture_loop


def _troubleshooting_block(lines):
    start = lines.index("Troubleshooting steps:")
    return tuple(lines[start + 1:start + 6])


def test_loop_prints_the_zwo_troubleshooting_steps(run_loop):
    cam = _DeadCamera()
    run_loop(cam)
    assert _troubleshooting_block(cam.lines) == ZWO_TROUBLESHOOTING_LINES


def test_session_protocol_names_every_contract_member():
    from services.camera.session import CaptureSession
    declared = set(CaptureSession.__annotations__) | {
        name for name in vars(CaptureSession) if not name.startswith('__')}
    assert set(SESSION_ATTRIBUTES) | set(SESSION_METHODS) <= declared


def test_zwo_session_satisfies_the_protocol():
    from services.camera.session import CaptureSession
    assert isinstance(_zwo_session(), CaptureSession)


def test_loop_owned_attributes_are_left_to_the_loop():
    from services.camera.session import LOOP_OWNED_ATTRIBUTES
    cam = _zwo_session()
    for name in LOOP_OWNED_ATTRIBUTES:
        assert not hasattr(cam, name)


def test_driver_contract_names_match():
    from services.camera.driver import HANDLE_METHOD_NAMES, SDK_CONSTANT_NAMES, ExposureHandle
    assert HANDLE_METHOD_NAMES == HANDLE_METHODS
    assert set(SDK_CONSTANT_NAMES) == set(SDK_CONSTANTS)
    assert set(HANDLE_METHODS) <= set(vars(ExposureHandle))


def test_zwoasi_camera_is_an_exposure_handle():
    zwoasi = pytest.importorskip('zwoasi')
    from services.camera.driver import ExposureHandle
    assert issubclass(zwoasi.Camera, ExposureHandle)


def test_zwo_session_is_a_capture_session_base():
    from services.camera.capture_session_base import CaptureSessionBase
    from services.camera.zwo_camera import ZWOCamera
    assert issubclass(ZWOCamera, CaptureSessionBase)
    assert ZWOCamera.backend == 'zwo'
    for name in ('__del__', '__enter__', '__exit__'):
        assert name in vars(ZWOCamera), f"{name} must stay on ZWOCamera"


def test_stop_capture_takes_the_connections_sdk_lock():
    cam = _zwo_session()
    lock = MagicMock()
    lock.acquire.return_value = True
    cam._connection.sdk_lock = lock
    cam._connection.camera = MagicMock()
    cam.is_capturing = True
    cam.exposure_start_time = 1.0
    cam.stop_capture()
    lock.acquire.assert_called_once_with(timeout=2.0)
    cam._connection.camera.stop_exposure.assert_called_once()
    lock.release.assert_called_once()


def test_set_gain_clamps_to_the_session_limits():
    cam = _zwo_session()
    cam.set_gain(900)
    assert cam.gain == 600
    cam.set_gain(-5)
    assert cam.gain == 0


def test_factory_applies_the_post_construction_settings():
    from services.camera.session_factory import build_session
    params = {k: 1000 for k in ('exposure_ms', 'max_exposure_ms')}
    params.update(
        sdk_path='', camera_name='ZWO ASI676MC (Index: 0)', clean_camera_name='ZWO ASI676MC',
        camera_serial='ABC', gain=120, wb_r=50, wb_b=60, offset=10, flip=1,
        bayer_pattern='RGGB', auto_exposure=True, wb_config={'mode': 'manual'},
        scheduled_capture_mode='variable', scheduled_start_time='18:00',
        scheduled_end_time='06:00', scheduled_window_interval=7.0,
        target_brightness=90, capture_interval=12.0, use_raw16=True, auto_recovery=False)
    gate, on_error, on_cal = MagicMock(), MagicMock(), MagicMock()
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(asi=None, camera=None, sdk_lock=MagicMock())
        cam = build_session(params, gate, 3, on_error, on_cal)
    assert cam.backend == 'zwo'
    assert (cam.camera_index, cam.camera_name, cam.camera_serial) == (3, 'ZWO ASI676MC', 'ABC')
    assert (cam.exposure_seconds, cam.max_exposure, cam.wb_mode) == (1.0, 1.0, 'manual')
    assert cam.schedule_gate is gate
    assert (cam.target_brightness, cam.capture_interval, cam.use_raw16) == (90, 12.0, True)
    assert cam._connection.auto_recovery_enabled is False
    assert cam.on_error_callback is on_error and cam.on_calibration_callback is on_cal


def test_read_capture_params_reads_the_saved_camera_profile():
    from services.camera.session_params import read_capture_params
    values = {'zwo_selected_camera_name': 'ZWO ASI676MC (Index: 1)',
              'zwo_selected_camera_serial': 'ABC', 'zwo_selected_camera': 1,
              'white_balance': {'manual_red_gain': 1.0}, 'dev_mode': {'use_raw16': True}}
    config = MagicMock()
    config.get = MagicMock(side_effect=lambda key, default=None: values.get(key, default))
    config.get_camera_profile.return_value = {'gain': 222}
    params = read_capture_params(config, auto_recovery=True)
    config.get_camera_profile.assert_called_once_with('ZWO ASI676MC', 'ABC')
    assert params['clean_camera_name'] == 'ZWO ASI676MC'
    assert params['gain'] == 222
    assert params['wb_config'] == {'manual_red_gain': 1.0, 'mode': 'asi_auto'}
    assert values['white_balance'] == {'manual_red_gain': 1.0}
    assert params['use_raw16'] is True and params['auto_recovery'] is True


def test_loop_uses_a_sessions_own_troubleshooting_steps(run_loop):
    cam = _DeadCamera(troubleshooting_hints=("Check the server", "Check the port"))
    run_loop(cam)
    start = cam.lines.index("Troubleshooting steps:")
    assert cam.lines[start + 1:start + 3] == ["  1. Check the server", "  2. Check the port"]
    assert not cam.lines[start + 3].startswith("  3.")
