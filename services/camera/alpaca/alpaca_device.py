"""One Alpaca camera: its native API plus the zwoasi-shaped ``ExposureHandle`` facade.

The native half speaks seconds and numpy arrays and is what the Alpaca capture
path calls. The facade half (``start_exposure``, ``get_exposure_status``,
``set_control_value`` …) exists so ``CameraCalibration``, the shared capture
loop and ``CaptureSessionBase.stop_capture`` drive this device exactly as they
drive a ``zwoasi.Camera``. The one unit conversion between the two lives here:
``set_control_value(ASI_EXPOSURE, µs)`` / ``get_control_value(ASI_EXPOSURE)``.

Optional ICamera members a driver does not implement read as ``None`` and
switch the matching capability off. Interface V4's ``Connect`` / ``Connecting``
is used when the device reports InterfaceVersion >= 4, otherwise the V3
``Connected`` PUT. ``disconnect`` / ``close`` release the device only when
``release_on_disconnect`` is set: on ASCOM Remote another client (NINA) may
share the connection, and ``Connected=false`` would drop it for them too.
"""
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from services.logger import app_logger

from .alpaca_protocol import (
    ALPACA_SDK,
    VALUE_NOT_SET,
    AlpacaError,
    AlpacaInvalidOperationError,
    AlpacaInvalidValueError,
    AlpacaNotConnectedError,
    AlpacaNotImplementedError,
    CameraState,
    SensorType,
)
from .alpaca_sensor import (
    bayer_pattern_for,
    physical_bayer_pattern,
    sensor_bit_depth,
    to_raw_bytes,
    transfer_bit_depth,
)

ABORT_TIMEOUT_S = 2.0
CONNECT_POLL_S = 0.25
IDLE_GRACE_S = 2.0

_ABSENT = (AlpacaNotImplementedError, AlpacaInvalidOperationError)


@dataclass(frozen=True)
class AlpacaStaticInfo:
    """What a connected camera reports about itself; ``None`` = not implemented."""
    name: str
    description: Optional[str]
    driver_info: Optional[str]
    driver_version: Optional[str]
    interface_version: int
    sensor_name: Optional[str]
    sensor_type: int
    bayer_offset_x: int
    bayer_offset_y: int
    width: int
    height: int
    pixel_size_x: Optional[float]
    electrons_per_adu: Optional[float]
    max_adu: int
    exposure_min: Optional[float]
    exposure_max: Optional[float]
    can_abort: bool
    can_stop: bool
    gain_mode: Optional[str]            # 'range' | 'list' | None
    gain_min: Optional[int]
    gain_max: Optional[int]
    gains: tuple
    offset_mode: Optional[str]
    offset_min: Optional[int]
    offset_max: Optional[int]
    offsets: tuple
    has_temperature: bool
    max_bin_x: Optional[int]

    @property
    def physical_bayer_pattern(self) -> str:
        return physical_bayer_pattern(self.sensor_type, self.bayer_offset_x, self.bayer_offset_y)

    @property
    def bayer_pattern(self) -> str:
        """The pipeline name (``debayer_raw_image`` / ``BAYER_PATTERN``)."""
        return bayer_pattern_for(self.sensor_type, self.bayer_offset_x, self.bayer_offset_y)

    @property
    def sensor_bit_depth(self) -> int:
        return sensor_bit_depth(self.max_adu)

    @property
    def transfer_bit_depth(self) -> int:
        return transfer_bit_depth(self.max_adu)

    @property
    def gain_range(self):
        if self.gain_mode == 'range' and self.gain_max is not None:
            return (self.gain_min, self.gain_max)
        if self.gain_mode == 'list':
            return (0, max(0, len(self.gains) - 1))
        return None

    @property
    def offset_range(self):
        if self.offset_mode == 'range' and self.offset_max is not None:
            return (self.offset_min, self.offset_max)
        if self.offset_mode == 'list':
            return (0, max(0, len(self.offsets) - 1))
        return None

    @property
    def exposure_range_s(self):
        if self.exposure_min is None and self.exposure_max is None:
            return None
        return (self.exposure_min if self.exposure_min is not None else 0.0,
                self.exposure_max if self.exposure_max is not None else 3600.0)

    def clamp_exposure(self, seconds: float) -> float:
        low, high = self.exposure_range_s or (0.0, None)
        seconds = max(float(seconds), low or 0.0)
        return min(seconds, high) if high is not None else seconds


def _clamp(value, bounds):
    low, high = bounds
    return max(low, min(high, int(value)))


class AlpacaCameraDevice:
    """A camera behind an ``AlpacaHttpClient``; also an ``ExposureHandle``."""

    def __init__(self, client, *, use_imagebytes: bool = True,
                 release_on_disconnect: bool = False):
        self.client = client
        self.use_imagebytes = bool(use_imagebytes)
        self.release_on_disconnect = bool(release_on_disconnect)
        self.info: Optional[AlpacaStaticInfo] = None
        self.flip = 0
        self.interface_version = None
        self.imagebytes_used = None
        self._state_lock = threading.Lock()
        self._pending_s = 1.0
        self._started_at = None
        self._exposing = False
        self._gain = None
        self._offset = None
        self._temperature_ok = True
        self._warned = set()

    # -- helpers ----------------------------------------------------------
    def _optional(self, member, default=None):
        try:
            return self.client.get(member)
        except _ABSENT:
            return default
        except AlpacaError as exc:
            if exc.number == VALUE_NOT_SET:
                return default
            raise

    def _warn_once(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            app_logger.warning(message)

    # -- connection -------------------------------------------------------
    def is_connected(self) -> bool:
        return bool(self.client.get("connected"))

    def connect(self, timeout: float = 30.0) -> None:
        """Connect (V4 ``Connect`` + ``Connecting`` poll, else V3 ``Connected``)."""
        version = self._optional("interfaceversion", 1)
        try:
            self.interface_version = int(version)
        except (TypeError, ValueError):
            self.interface_version = 1
        if self.is_connected():
            return
        if self.interface_version >= 4 and self._connect_v4(timeout):
            return
        self.client.put("connected", Connected=True)
        if not self.is_connected():
            raise AlpacaNotConnectedError(None, "Device did not report Connected after connect")

    def _connect_v4(self, timeout: float) -> bool:
        try:
            self.client.put("connect")
        except AlpacaNotImplementedError:
            return False
        deadline = time.monotonic() + max(1.0, float(timeout))
        while True:
            try:
                busy = bool(self.client.get("connecting"))
            except AlpacaNotImplementedError:
                busy = False
            if not busy:
                break
            if time.monotonic() >= deadline:
                raise AlpacaNotConnectedError(
                    None, f"Device still connecting after {timeout:.0f} s")
            time.sleep(CONNECT_POLL_S)
        if not self.is_connected():
            raise AlpacaNotConnectedError(None, "Device did not report Connected after Connect")
        return True

    def disconnect(self) -> bool:
        """Release the device when ``release_on_disconnect``; True if a PUT was sent."""
        if not self.release_on_disconnect:
            return False
        try:
            if (self.interface_version or 0) >= 4:
                try:
                    self.client.put("disconnect", timeout=ABORT_TIMEOUT_S)
                    return True
                except AlpacaNotImplementedError:
                    pass
            self.client.put("connected", timeout=ABORT_TIMEOUT_S, Connected=False)
            return True
        except Exception as exc:
            app_logger.debug(f"Alpaca disconnect failed: {exc}")
            return False

    # -- static info ------------------------------------------------------
    def read_static(self) -> AlpacaStaticInfo:
        get, opt = self.client.get, self._optional
        sensor_type = opt("sensortype")
        if sensor_type is None:
            self._warn_once("sensortype", "Alpaca camera does not report SensorType; "
                                          "treating frames as monochrome")
            sensor_type = SensorType.MONOCHROME
        sensor_type = int(sensor_type)
        bayer_x = bayer_y = 0
        if sensor_type >= SensorType.RGGB:
            bayer_x = int(opt("bayeroffsetx", 0) or 0)
            bayer_y = int(opt("bayeroffsety", 0) or 0)

        gain_mode, gain_min, gain_max, gains = self._range_or_list("gain")
        offset_mode, offset_min, offset_max, offsets = self._range_or_list("offset")
        has_temperature = opt("ccdtemperature") is not None
        self._temperature_ok = has_temperature

        info = AlpacaStaticInfo(
            name=str(opt("name", "") or "Alpaca camera"),
            description=opt("description"),
            driver_info=opt("driverinfo"),
            driver_version=opt("driverversion"),
            interface_version=int(self.interface_version or opt("interfaceversion", 1) or 1),
            sensor_name=opt("sensorname"),
            sensor_type=sensor_type,
            bayer_offset_x=bayer_x,
            bayer_offset_y=bayer_y,
            width=int(get("cameraxsize")),
            height=int(get("cameraysize")),
            pixel_size_x=opt("pixelsizex"),
            electrons_per_adu=opt("electronsperadu"),
            max_adu=int(opt("maxadu", 65535) or 65535),
            exposure_min=opt("exposuremin"),
            exposure_max=opt("exposuremax"),
            can_abort=bool(opt("canabortexposure", False)),
            can_stop=bool(opt("canstopexposure", False)),
            gain_mode=gain_mode, gain_min=gain_min, gain_max=gain_max, gains=gains,
            offset_mode=offset_mode, offset_min=offset_min, offset_max=offset_max,
            offsets=offsets,
            has_temperature=has_temperature,
            max_bin_x=opt("maxbinx"),
        )
        self.info = info
        if sensor_type == SensorType.COLOR:
            app_logger.info("Alpaca camera delivers full-colour (RGB) frames: no debayer")
        return info

    def _range_or_list(self, kind):
        low, high = self._optional(f"{kind}min"), self._optional(f"{kind}max")
        if low is not None and high is not None:
            return 'range', int(low), int(high), ()
        names = self._optional(f"{kind}s")
        if isinstance(names, list) and names:
            return 'list', None, None, tuple(str(n) for n in names)
        if self._optional(kind) is not None:
            return 'range', 0, None, ()
        return None, None, None, ()

    # -- native exposure API (seconds) -------------------------------------
    @property
    def pending_exposure_s(self) -> float:
        with self._state_lock:
            return self._pending_s

    def set_pending_exposure(self, seconds: float) -> float:
        value = self.info.clamp_exposure(seconds) if self.info else float(seconds)
        with self._state_lock:
            self._pending_s = value
        return value

    def start(self, duration_s: float, light: bool = True) -> float:
        duration = self.set_pending_exposure(duration_s)
        self.client.put("startexposure", Duration=float(duration), Light=bool(light))
        with self._state_lock:
            self._started_at = time.monotonic()
            self._exposing = True
        return duration

    def image_ready(self) -> bool:
        return bool(self.client.get("imageready"))

    def camera_state(self) -> int:
        return int(self.client.get("camerastate"))

    def abort(self) -> None:
        """Abort (or stop) an exposure this client started; never raises."""
        with self._state_lock:
            exposing, self._exposing = self._exposing, False
        if not exposing:
            return
        info = self.info
        member = "abortexposure"
        if info is not None and not info.can_abort:
            member = "stopexposure" if info.can_stop else None
        if member is None:
            return
        try:
            self.client.put(member, timeout=ABORT_TIMEOUT_S)
        except Exception as exc:
            app_logger.debug(f"Alpaca {member} failed: {exc}")

    def download(self, abort_event=None, timeout: float = 60.0) -> np.ndarray:
        image = self.client.get_image(timeout=timeout, use_imagebytes=self.use_imagebytes,
                                      abort_event=abort_event)
        with self._state_lock:
            self._exposing = False
        self.imagebytes_used = self.client.last_image_format == "imagebytes"
        return image

    def ccd_temperature(self) -> Optional[float]:
        if not self._temperature_ok:
            return None
        try:
            return float(self.client.get("ccdtemperature"))
        except _ABSENT:
            self._temperature_ok = False
            return None

    def _control_bounds(self, kind):
        info = self.info
        if info is None:
            return (0, None)
        mode = getattr(info, f"{kind}_mode")
        if mode is None:
            return None
        if mode == 'list':
            return getattr(info, f"{kind}_range")
        return (getattr(info, f"{kind}_min") or 0, getattr(info, f"{kind}_max"))

    def _set_control(self, kind, value) -> Optional[bool]:
        bounds = self._control_bounds(kind)
        if bounds is None:
            return None
        low, high = bounds
        value = int(value)
        clamped = max(low, value) if high is None else _clamp(value, bounds)
        if clamped != value:
            self._warn_once(f"{kind}-clamp", f"Alpaca camera {kind} {value} outside "
                                             f"[{low}, {high}]; using {clamped}")
        if clamped == getattr(self, f"_{kind}"):
            return True
        try:
            self.client.put(kind, **{kind.title(): clamped})
        except AlpacaInvalidValueError as exc:
            self._warn_once(f"{kind}-invalid", f"Alpaca camera rejected {kind} {clamped}: {exc}")
            return False
        except AlpacaNotImplementedError:
            return None
        setattr(self, f"_{kind}", clamped)
        return True

    def set_gain(self, value) -> Optional[bool]:
        """PUT Gain (clamped; only when it changed). None when unsupported."""
        return self._set_control("gain", value)

    def set_offset(self, value) -> Optional[bool]:
        """PUT Offset (clamped; only when it changed). None when unsupported."""
        return self._set_control("offset", value)

    def set_full_frame_roi(self) -> None:
        info = self.info
        for member, value in (("binx", 1), ("biny", 1), ("startx", 0), ("starty", 0),
                              ("numx", info.width), ("numy", info.height)):
            try:
                self.client.put(member, **{member[:-1].title() + member[-1].upper(): value})
            except (AlpacaNotImplementedError, AlpacaInvalidValueError) as exc:
                self._warn_once(f"roi-{member}", f"Alpaca camera refused {member}={value}: {exc}")

    # -- zwoasi facade (ExposureHandle) -----------------------------------
    def set_control_value(self, control_type, value, auto=False) -> None:
        if control_type == ALPACA_SDK.ASI_EXPOSURE:
            self.set_pending_exposure(float(value) / 1e6)
        elif control_type == ALPACA_SDK.ASI_GAIN:
            self.set_gain(value)
        elif control_type == ALPACA_SDK.ASI_BRIGHTNESS:
            self.set_offset(value)
        elif control_type == ALPACA_SDK.ASI_FLIP:
            self.flip = int(value)

    def get_control_value(self, control_type):
        if control_type == ALPACA_SDK.ASI_TEMPERATURE:
            celsius = self.ccd_temperature()
            if celsius is None:
                raise AlpacaNotImplementedError(None, "CCDTemperature is not implemented")
            return int(round(celsius * 10)), False
        if control_type == ALPACA_SDK.ASI_EXPOSURE:
            return int(round(self.pending_exposure_s * 1e6)), False
        if control_type == ALPACA_SDK.ASI_GAIN:
            return int(self._gain or 0), False
        if control_type == ALPACA_SDK.ASI_BRIGHTNESS:
            return int(self._offset or 0), False
        if control_type == ALPACA_SDK.ASI_FLIP:
            return int(self.flip), False
        return 0, False

    def start_exposure(self, is_dark: bool = False) -> None:
        self.start(self.pending_exposure_s, light=not is_dark)

    def stop_exposure(self) -> None:
        self.abort()

    def get_exposure_status(self) -> int:
        """ASI_EXP_* for the exposure in flight.

        Some drivers read Idle for a moment after StartExposure, so Idle with
        no image counts as WORKING until the exposure plus a grace has passed.
        """
        if self.image_ready():
            return ALPACA_SDK.ASI_EXP_SUCCESS
        state = self.camera_state()
        if state == CameraState.ERROR:
            return ALPACA_SDK.ASI_EXP_FAILED
        if state != CameraState.IDLE:
            return ALPACA_SDK.ASI_EXP_WORKING
        with self._state_lock:
            started = self._started_at
            pending = self._pending_s
        if started is not None and time.monotonic() - started <= pending + IDLE_GRACE_S:
            return ALPACA_SDK.ASI_EXP_WORKING
        return ALPACA_SDK.ASI_EXP_IDLE

    def get_data_after_exposure(self, buffer_=None):
        """Row-major H*W pixels at the transfer depth (an RGB frame as its plane mean)."""
        image = self.download()
        if image.ndim == 3:
            image = image.mean(axis=2)
        max_adu = self.info.max_adu if self.info else 65535
        data, _depth = to_raw_bytes(image, max_adu)
        return data

    def get_camera_property(self) -> dict:
        info = self.info
        if info is None:
            return {'Name': 'Alpaca camera', 'MaxWidth': 0, 'MaxHeight': 0}
        return {
            'Name': info.name,
            'MaxWidth': info.width,
            'MaxHeight': info.height,
            'BitDepth': info.sensor_bit_depth,
            'PixelSize': info.pixel_size_x or 0,
            'ElecPerADU': info.electrons_per_adu or 1.0,
            'IsColorCam': info.bayer_pattern != 'MONO',
            'SupportedVideoFormat': [ALPACA_SDK.ASI_IMG_RAW8 if info.transfer_bit_depth == 8
                                     else ALPACA_SDK.ASI_IMG_RAW16],
        }

    def get_roi_format(self):
        info = self.info
        if info is None:
            return 0, 0, 1, ALPACA_SDK.ASI_IMG_RAW8
        image_type = (ALPACA_SDK.ASI_IMG_RAW16 if info.transfer_bit_depth == 16
                      else ALPACA_SDK.ASI_IMG_RAW8)
        return info.width, info.height, 1, image_type

    def get_dropped_frames(self) -> int:
        return 0

    def close(self) -> None:
        self.abort()
        self.disconnect()


__all__ = ["AlpacaCameraDevice", "AlpacaStaticInfo"]
