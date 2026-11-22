"""One Alpaca camera: its native API plus the zwoasi-shaped ``ExposureHandle`` facade.

The native half speaks seconds and numpy arrays and is what the Alpaca capture
path calls. The facade half (``start_exposure``, ``get_exposure_status``,
``set_control_value`` …) exists so ``CameraCalibration``, the shared capture
loop and ``CaptureSessionBase.stop_capture`` drive this device exactly as they
drive a ``zwoasi.Camera``. The one unit conversion between the two lives here:
``set_control_value(ASI_EXPOSURE, µs)`` / ``get_control_value(ASI_EXPOSURE)``.

Optional ICamera members a driver does not implement — or that throw any device
error, as an uncooled camera or an old V2 driver behind ASCOM Remote does for
CCDTemperature / Offset* / SensorName — read as ``None`` and switch the
matching capability off; only a transport failure (or NotConnected) fails the
connect. Interface V4's ``Connect`` / ``Connecting``
is used when the device reports InterfaceVersion >= 4, otherwise the V3
``Connected`` PUT. ``disconnect`` / ``close`` release the device only when
``release_on_disconnect`` is set: on ASCOM Remote another client (NINA) may
share the connection, and ``Connected=false`` would drop it for them too.

The camera may be shared with another client (NINA through ASCOM Remote), so
``abort`` only ever aborts an exposure this device started and has not yet
seen finish, and it goes through the client's control session so it never
waits behind the capture thread's poll.
"""
import threading
import time
from typing import Optional

import numpy as np

from services.logger import app_logger

from ..errors import FrameSkipped
from .alpaca_protocol import (
    ALPACA_SDK,
    AlpacaError,
    AlpacaImageBytesError,
    AlpacaInvalidOperationError,
    AlpacaInvalidValueError,
    AlpacaNotConnectedError,
    AlpacaNotImplementedError,
    CameraState,
    SensorType,
)
from .alpaca_warnings import SessionWarnings
from .alpaca_readout import pixel_ceiling, preferred_readout_mode
from .alpaca_sensor import to_raw_bytes
from .alpaca_static_info import AlpacaStaticInfo

ABORT_TIMEOUT_S = 2.0
CONNECT_POLL_S = 0.25
IDLE_GRACE_S = 2.0
START_GRACE_S = 10.0
IMAGEBYTES_OFF = "imagebytes_off"

_ABSENT = (AlpacaNotImplementedError, AlpacaInvalidOperationError)
_STARTING = "starting"


def _clamp(value, bounds):
    low, high = bounds
    return max(low, min(high, int(value)))


class AlpacaCameraDevice:
    """A camera behind an ``AlpacaHttpClient``; also an ``ExposureHandle``."""

    def __init__(self, client, *, use_imagebytes: bool = True,
                 release_on_disconnect: bool = False,
                 warnings: Optional[SessionWarnings] = None):
        self.client = client
        self.warnings = warnings if warnings is not None else SessionWarnings()
        self.use_imagebytes = bool(use_imagebytes) and not self.warnings.state.get(IMAGEBYTES_OFF)
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

    # -- helpers ----------------------------------------------------------
    def _optional(self, member, default=None):
        """An optional member's value, or ``default`` on any device error.

        NotImplemented, InvalidOperation, ValueNotSet, a driver error
        (0x500-0xFFF) or an HTTP 400 all mean "this camera does not offer it".
        A transport failure — and NotConnected — still raise.
        """
        try:
            return self.client.get(member)
        except AlpacaNotConnectedError:
            raise
        except AlpacaError as exc:
            if not isinstance(exc, _ABSENT):
                self.warnings.debug_once(f"optional-{member}",
                                         f"Alpaca camera: optional {member} unavailable ({exc})")
            return default

    def _warn_once(self, key, message):
        self.warnings.once(key, message)

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
        # A V3 driver opens the camera inside this PUT: 10-20 s is normal.
        self.client.put("connected", timeout=max(self.client.request_timeout, float(timeout)),
                        Connected=True)
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

    def reopen(self, timeout: float = 30.0) -> None:
        """Make the driver let go of the camera and open it again.

        V4 ``Disconnect`` + ``Connect``, else V3 ``Connected=false`` then
        ``true``, whatever ``release_on_disconnect`` says. Only for a camera
        that answers but no longer finishes exposures (``alpaca_stall_recovery``):
        another client of the same camera on the server may see it drop.
        """
        if (self.interface_version or 0) >= 4:
            try:
                self.client.put("disconnect", timeout=max(self.client.request_timeout,
                                                          float(timeout)))
            except AlpacaNotImplementedError:
                pass
            else:
                if self._connect_v4(timeout):
                    return
        self.client.put("connected", timeout=max(self.client.request_timeout, float(timeout)),
                        Connected=False)
        self.client.put("connected", timeout=max(self.client.request_timeout, float(timeout)),
                        Connected=True)
        if not self.is_connected():
            raise AlpacaNotConnectedError(None, "Device did not report Connected after reopening")

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

    def select_readout_mode(self) -> Optional[str]:
        """Move an 8-bit readout mode to its 16-bit twin (``alpaca_readout``).

        Returns the name of the mode in use, or None when the camera names no
        modes. A refused write keeps the current mode and is logged once.
        """
        names = self._optional("readoutmodes")
        current = self._optional("readoutmode")
        if not isinstance(names, list) or not names:
            return None
        target = preferred_readout_mode(names, current)
        if target is not None:
            try:
                self.client.put("readoutmode", ReadoutMode=int(target))
                app_logger.info(f"Alpaca camera readout mode: '{names[target]}' "
                                f"(was '{names[int(current)]}', 8-bit)")
                current = target
            except AlpacaNotConnectedError:
                raise
            except AlpacaError as exc:
                self._warn_once("readout-mode",
                                f"Alpaca camera refused readout mode '{names[target]}' ({exc}); "
                                f"frames stay 8-bit")
        try:
            return str(names[int(current)])
        except (TypeError, ValueError, IndexError):
            return None

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
        """StartExposure; ``FrameSkipped`` when another client is exposing.

        Some legacy drivers block the PUT for the whole exposure, so it gets
        the exposure plus a grace. The exposure counts as ours from the moment
        the PUT is sent (so Stop can abort a blocking one) until ImageReady is
        seen or the start fails.
        """
        duration = self.set_pending_exposure(duration_s)
        with self._state_lock:
            self._exposing = _STARTING
        try:
            self.client.put("startexposure",
                            timeout=max(self.client.request_timeout, duration + START_GRACE_S),
                            Duration=float(duration), Light=bool(light))
        except AlpacaInvalidOperationError as exc:
            self._finished()
            raise FrameSkipped(f"camera busy — another client is exposing ({exc})") from exc
        except BaseException:
            self._finished()
            raise
        with self._state_lock:
            self._started_at = time.monotonic()
            if self._exposing == _STARTING:
                self._exposing = True
        return duration

    def _finished(self) -> None:
        with self._state_lock:
            self._exposing = False

    @property
    def exposure_in_flight(self) -> bool:
        """True while an exposure this device started has not been seen to end."""
        with self._state_lock:
            return bool(self._exposing)

    def image_ready(self) -> bool:
        ready = bool(self.client.get("imageready"))
        if ready:
            self._finished()
        return ready

    def camera_state(self) -> int:
        return int(self.client.get("camerastate"))

    def abort(self) -> None:
        """Abort (or stop) an exposure this client started; never raises.

        Never sent once ImageReady has been seen, nor after a failed start: on
        a shared camera the exposure running then may be another client's.
        """
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
            self.client.put_control(member, timeout=ABORT_TIMEOUT_S)
        except Exception as exc:
            app_logger.debug(f"Alpaca {member} failed: {exc}")

    def mark_finished(self) -> None:
        """The exposure is known to have ended (Idle / Error): nothing to abort."""
        self._finished()

    def download(self, abort_event=None, timeout: float = 60.0) -> np.ndarray:
        try:
            try:
                image = self.client.get_image(timeout=timeout, use_imagebytes=self.use_imagebytes,
                                              abort_event=abort_event)
            except AlpacaImageBytesError as exc:
                # The image is still on the server until the next exposure: take
                # it as JSON, and stay on JSON for the session (reconnects too).
                self.use_imagebytes = False
                self.warnings.state[IMAGEBYTES_OFF] = True
                self.warnings.once("imagebytes-broken",
                                   f"Alpaca server sent an ImageBytes image that cannot be "
                                   f"decoded ({exc}); downloading as JSON for this session")
                image = self.client.get_image(timeout=timeout, use_imagebytes=False,
                                              abort_event=abort_event)
        finally:
            self._finished()
        fmt = self.client.last_image_format
        self.imagebytes_used = fmt == "imagebytes"
        note = getattr(self.client, "last_image_note", "")
        if note:
            self.warnings.once("imagebytes-short", note)
        if self.use_imagebytes and fmt == "json":
            self.warnings.once("imagebytes-ignored",
                               "Alpaca server ignored the ImageBytes request and sent JSON: "
                               "downloads are several times larger and slower. ASCOM Remote "
                               "6.6+ and most current drivers support ImageBytes.")
        return image

    def ccd_temperature(self) -> Optional[float]:
        """The sensor temperature, or None. Only NotImplemented switches it off:
        an InvalidOperation during readout (or a passing driver error) is one
        missing reading, not a camera without a sensor."""
        if not self._temperature_ok:
            return None
        try:
            return float(self.client.get("ccdtemperature"))
        except AlpacaNotImplementedError:
            self._temperature_ok = False
            return None
        except AlpacaNotConnectedError:
            raise
        except AlpacaError as exc:
            self.warnings.debug_once("temperature-read", f"Alpaca CCDTemperature read failed: {exc}")
            return None
        except (TypeError, ValueError):
            return None

    def percent_completed(self) -> Optional[int]:
        """PercentCompleted, or None when the driver does not offer it (never raises)."""
        try:
            value = self._optional("percentcompleted")
            return None if value is None else int(value)
        except Exception:
            return None

    def last_exposure_duration(self) -> Optional[float]:
        """LastExposureDuration (s), or None when the driver does not offer it."""
        value = self._optional("lastexposureduration")
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
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

    def _set_control(self, kind, value, live: bool = False) -> Optional[bool]:
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
        put = self.client.put_control if live else self.client.put
        try:
            put(kind, **{kind.title(): clamped})
        except AlpacaInvalidValueError as exc:
            self._warn_once(f"{kind}-invalid", f"Alpaca camera rejected {kind} {clamped}: {exc}")
            return False
        except AlpacaNotImplementedError:
            return None
        setattr(self, f"_{kind}", clamped)
        return True

    def set_gain(self, value, live: bool = False) -> Optional[bool]:
        """PUT Gain (clamped; only when it changed). None when unsupported."""
        return self._set_control("gain", value, live)

    def set_offset(self, value, live: bool = False) -> Optional[bool]:
        """PUT Offset (clamped; only when it changed). None when unsupported."""
        return self._set_control("offset", value, live)

    def reassert_control(self, kind, value) -> Optional[bool]:
        """Make the camera's ``kind`` equal ``value`` now: another client may
        have changed it since our last PUT, so read it back and re-send."""
        if self._control_bounds(kind) is None or value is None:
            return None
        try:
            current = int(self.client.get(kind))
        except AlpacaNotConnectedError:
            raise
        except (AlpacaError, TypeError, ValueError):
            return self._set_control(kind, value)
        setattr(self, f"_{kind}", current)
        return self._set_control(kind, value)

    def full_frame_roi(self) -> tuple:
        info = self.info
        return (("binx", 1), ("biny", 1), ("startx", 0), ("starty", 0),
                ("numx", info.width), ("numy", info.height))

    def set_full_frame_roi(self, members=None) -> bool:
        """Bin 1x1 full frame; True when every write was accepted.

        InvalidOperation (another client is exposing) is not a failure of the
        connect: the frame path re-checks the ROI before each exposure.
        """
        ok = True
        for member, value in (members or self.full_frame_roi()):
            try:
                self.client.put(member, **{member[:-1].title() + member[-1].upper(): value})
            except AlpacaNotConnectedError:
                raise
            except AlpacaInvalidOperationError as exc:
                ok = False
                self.warnings.limited(f"roi-busy-{member}",
                                      f"Alpaca camera refused {member}={value} for now "
                                      f"(another client exposing?): {exc}")
            except AlpacaError as exc:
                ok = False
                self._warn_once(f"roi-{member}", f"Alpaca camera refused {member}={value}: {exc}")
        return ok

    def roi_mismatches(self) -> list:
        """``[(member, wanted, actual), …]`` where the camera is not full frame."""
        wrong = []
        for member, value in self.full_frame_roi():
            try:
                actual = int(self.client.get(member))
            except AlpacaNotConnectedError:
                raise
            except (AlpacaError, TypeError, ValueError):
                continue
            if actual != value:
                wrong.append((member, value, actual))
        return wrong

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
        A driver may also flip CameraState to Idle a moment before ImageReady,
        so Idle past the grace re-reads ImageReady once before giving up.
        """
        if self.image_ready():
            return ALPACA_SDK.ASI_EXP_SUCCESS
        state = self.camera_state()
        if state == CameraState.ERROR:
            self._finished()
            return ALPACA_SDK.ASI_EXP_FAILED
        if state != CameraState.IDLE:
            return ALPACA_SDK.ASI_EXP_WORKING
        with self._state_lock:
            started = self._started_at
            pending = self._pending_s
        if started is not None and time.monotonic() - started <= pending + IDLE_GRACE_S:
            return ALPACA_SDK.ASI_EXP_WORKING
        if self.image_ready():
            return ALPACA_SDK.ASI_EXP_SUCCESS
        self._finished()
        return ALPACA_SDK.ASI_EXP_IDLE

    def get_data_after_exposure(self, buffer_=None):
        """Row-major H*W pixels at the transfer depth (an RGB frame as its plane mean)."""
        image = self.download()
        if image.ndim == 3:
            image = image.mean(axis=2)
        max_adu = self.info.max_adu if self.info else 65535
        data, _depth = to_raw_bytes(image, pixel_ceiling(max_adu, self.warnings.state))
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
