"""``AlpacaCamera``: an ASCOM Alpaca camera as a capture session.

Everything backend-neutral — schedule gate, capture thread, stop/join, the
auto-exposure wrappers and the shared capture loop — comes from
``CaptureSessionBase``. This class adds the network half: connect / reconnect
through ``AlpacaConnection``, one frame through ``alpaca_capture_frame``, and
capabilities read from the device once it is open.

The device decides the Bayer pattern (SensorType + BayerOffset, flip-adjusted);
a profile's ``bayer_pattern`` is stored but never used for decoding. White
balance has no hardware path: a stored ``asi_auto`` runs as ``gray_world`` and
``manual`` always applies the software gains — config is never rewritten.
"""
import dataclasses

from ..capabilities import ALPACA_STATIC, CameraCapabilities
from ..capture_session_base import CaptureSessionBase
from . import alpaca_capture_frame
from .alpaca_connection import AlpacaConnection, lan_session
from .alpaca_discovery import cameras_on_server
from .alpaca_http import AlpacaHttpClient
from .alpaca_sensor import flip_bayer_pattern

LIVE_LOCK_TIMEOUT_S = 2.0
_DEFAULT_GAIN_LIMITS = (0, 600)


def effective_wb_config(wb_config) -> dict:
    """The white balance an Alpaca frame actually gets (no hardware WB)."""
    effective = dict(wb_config or {})
    mode = effective.get('mode', 'asi_auto')
    if mode == 'asi_auto':
        effective['mode'] = 'gray_world'
    elif mode == 'manual':
        effective['apply_software_gains'] = True
    return effective


class AlpacaCamera(CaptureSessionBase):
    """A capture session on an ASCOM Alpaca camera."""

    backend = 'alpaca'

    def __init__(self, alpaca_settings=None, exposure_sec=1.0, gain=100, offset=20, flip=0,
                 auto_exposure=False, max_exposure_sec=30.0, wb_mode='asi_auto',
                 wb_config=None, bayer_pattern='BGGR', white_balance_r=75,
                 white_balance_b=99, scheduled_capture_enabled=False,
                 scheduled_start_time="17:00", scheduled_end_time="09:00",
                 scheduled_capture_mode=None, scheduled_window_interval=5.0,
                 status_callback=None, camera_name=None, camera_serial=None,
                 session_factory=lan_session):
        super().__init__(
            exposure_sec=exposure_sec, gain=gain, auto_exposure=auto_exposure,
            max_exposure_sec=max_exposure_sec, wb_mode=wb_mode, wb_config=wb_config,
            bayer_pattern=bayer_pattern,
            scheduled_capture_enabled=scheduled_capture_enabled,
            scheduled_start_time=scheduled_start_time,
            scheduled_end_time=scheduled_end_time,
            scheduled_capture_mode=scheduled_capture_mode,
            scheduled_window_interval=scheduled_window_interval,
            status_callback=status_callback,
        )
        self._connection = AlpacaConnection(alpaca_settings or {}, logger=self.log,
                                            session_factory=session_factory)
        self.camera_name = camera_name or self._connection.camera_name
        self.camera_serial = camera_serial or self._connection.camera_serial
        self.camera_index = 0
        self.wb_mode = wb_mode
        self.white_balance_r = white_balance_r
        self.white_balance_b = white_balance_b
        self.offset = offset
        self.flip = flip
        self.use_raw16 = False
        self.last_download_s = None
        self.last_download_bytes = 0
        self.last_image_format = None

    # -- delegated state ----------------------------------------------------
    @property
    def camera(self):
        return self._connection.camera

    @camera.setter
    def camera(self, value):
        self._connection.camera = value

    @property
    def asi(self):
        return self._connection.asi

    @property
    def static_info(self):
        return self._connection.static_info

    @property
    def unique_id(self) -> str:
        return self._connection.unique_id

    @property
    def supports_raw16(self) -> bool:
        return False

    @property
    def sensor_bit_depth(self) -> int:
        return self._connection.bit_depth

    @property
    def current_bit_depth(self) -> int:
        return self._connection.current_bit_depth

    @property
    def camera_info(self) -> dict:
        return self._connection.camera_info

    @property
    def auto_recovery_enabled(self) -> bool:
        return self._connection.auto_recovery_enabled

    @auto_recovery_enabled.setter
    def auto_recovery_enabled(self, value):
        self._connection.auto_recovery_enabled = bool(value)

    @property
    def wb_config(self) -> dict:
        return effective_wb_config(self._stored_wb_config)

    @wb_config.setter
    def wb_config(self, value):
        self._stored_wb_config = dict(value or {})

    @property
    def bayer_pattern(self) -> str:
        """The pattern frames are decoded with: the device's, after the flip."""
        info = self._connection.static_info
        if info is None:
            return self._stored_bayer_pattern
        return flip_bayer_pattern(info.bayer_pattern, self.flip, info.width, info.height)

    @bayer_pattern.setter
    def bayer_pattern(self, value):
        self._stored_bayer_pattern = value

    @property
    def _GAIN_LIMITS(self):
        info = self._connection.static_info
        limits = info.gain_range if info is not None else None
        return limits or _DEFAULT_GAIN_LIMITS

    @property
    def troubleshooting_hints(self) -> tuple:
        return ALPACA_STATIC.troubleshooting_hints

    @property
    def capabilities(self) -> CameraCapabilities:
        info = self._connection.static_info
        if info is None:
            return ALPACA_STATIC
        return dataclasses.replace(
            ALPACA_STATIC,
            bayer_pattern=info.bayer_pattern,
            bit_depth=info.sensor_bit_depth,
            gain=info.gain_mode is not None,
            gain_range=info.gain_range,
            offset=info.offset_mode is not None,
            offset_range=info.offset_range,
            exposure_range_s=info.exposure_range_s,
            temperature=info.has_temperature,
        )

    def learned_identity(self) -> tuple:
        """``(unique_id, name)`` learned on connect, for the main thread to persist."""
        return self._connection.unique_id, self._connection.camera_name or ''

    # -- cleanup safety net -------------------------------------------------
    def __del__(self):
        try:
            self.disconnect_camera()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect_camera()
        return False

    # -- connection ---------------------------------------------------------
    def initialize_sdk(self):
        return True

    def detect_cameras(self):
        conn = self._connection
        if not conn.host:
            return []
        factory = self._connection._session_factory

        def client_factory(*args, **kwargs):
            return AlpacaHttpClient(*args, session_factory=factory, **kwargs)

        try:
            found = cameras_on_server(conn.host, conn.port, client_factory=client_factory)
        except Exception as e:
            self.log(f"Alpaca detect failed ({conn.host}:{conn.port}): {e}")
            return []
        return [f"{c.name} (Alpaca {c.host}:{c.port} #{c.device_number})" for c in found]

    def _settings(self) -> dict:
        return {'gain': self.gain, 'offset': self.offset, 'flip': self.flip,
                'exposure_sec': self.exposure_seconds}

    def _after_connect(self):
        conn = self._connection
        self.camera_name = conn.camera_name
        self.camera_serial = conn.camera_serial
        self._init_calibration_manager()

    def connect_camera(self, camera_index=0):
        """Connect to the configured Alpaca camera (``camera_index`` is unused)."""
        if not self._connection.connect(self._settings()):
            return False
        self._after_connect()
        self.log("✓ Camera connection successful")
        if self.scheduled_capture_enabled:
            self.log(f"Scheduled capture enabled: {self.scheduled_window_label()}")
        return True

    def reconnect_camera_safe(self):
        if not self._connection.reconnect_safe(self._settings()):
            return False
        self._after_connect()
        return True

    def disconnect_camera(self):
        if self.is_capturing:
            self.log("Stopping active capture before disconnect...")
            self.stop_capture()

        def stop_exposure_callback():
            self.exposure_start_time = None
            self.exposure_remaining = 0.0

        self._connection.disconnect(stop_exposure_callback=stop_exposure_callback)
        self.exposure_start_time = None
        self.exposure_remaining = 0.0

    def capture_single_frame(self):
        return alpaca_capture_frame.capture_single_frame(self)

    # -- live settings ------------------------------------------------------
    def _run_live(self, write, success_msg=None) -> bool:
        """Run ``write(device)`` under this session's own lock (2 s timed)."""
        if not self.camera:
            return False
        lock = self._connection.sdk_lock
        if not lock.acquire(timeout=LIVE_LOCK_TIMEOUT_S):
            self.log("⚠ Live setting skipped — camera busy (capture worker holds lock)")
            return False
        try:
            if not self.camera:
                return False
            write(self.camera)
            if success_msg:
                self.log(success_msg)
            return True
        except Exception as e:
            self.log(f"Failed to apply live setting: {e}")
            return False
        finally:
            lock.release()

    def set_offset_live(self, offset):
        self.offset = offset
        self._run_live(lambda device: device.set_offset(offset))

    def set_flip_live(self, flip):
        """Software flip: the next frame is flipped and its Bayer pattern follows."""
        self.flip = int(flip or 0)

    def set_white_balance_live(self, mode, wb_r, wb_b):
        """Stored only: there is no hardware white balance on an Alpaca camera."""
        self.wb_mode, self.white_balance_r, self.white_balance_b = mode, wb_r, wb_b

    def set_raw16_mode(self, enabled: bool) -> bool:
        """No RAW8/RAW16 switch: the transfer depth follows the device's MaxADU."""
        return False
