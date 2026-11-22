"""An ASCOM Alpaca camera (ICameraV3 / V4) as a pure request → response state machine.

``FakeAlpacaCamera.handle(method, path, query, form, headers)`` answers one
HTTP request with a ``SimResponse`` (status, content type, body bytes). It
opens no socket: ``scripts/dev/alpaca_camera_sim.py`` puts it behind a real
``http.server`` and ``tests/alpaca_fake.py`` behind a ``requests.Session``
stand-in, so both exercise the same wire bytes.

Time comes from an injectable ``clock`` (monotonic seconds) and ``wallclock``
(aware UTC datetime); the exposure state machine advances lazily on each
request. Device-level faults are switches on the instance (``faults``);
transport faults (refused connections, timeouts) belong to the transport.

Errors follow the spec: 0x400 NotImplemented for an optional member the device
lacks, 0x401 InvalidValue for an out-of-range value, 0x407 NotConnected for a
camera member while disconnected, 0x40B InvalidOperation for the wrong moment
(ImageArray before an image, StartExposure while exposing); HTTP 400 for a
missing or malformed parameter or an unknown device number, 404 for an
unknown member. The common members (Name, Description, DriverInfo,
DriverVersion, InterfaceVersion, SupportedActions, Connected, Connecting) work
while disconnected, as ASCOM requires.
"""
import itertools
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import unquote

import numpy as np

from . import wire
from .members import GETTERS, SETTERS, SimError, parse_uint
from .scene import SceneGenerator

NOT_IMPLEMENTED, INVALID_VALUE, VALUE_NOT_SET = 0x400, 0x401, 0x402
NOT_CONNECTED, INVALID_OPERATION = 0x407, 0x40B
IDLE, WAITING, EXPOSING, READING, DOWNLOAD, ERROR = range(6)

COMMON_MEMBERS = {"connected", "connecting", "connect", "disconnect", "name", "description",
                  "driverinfo", "driverversion", "interfaceversion", "supportedactions"}


@dataclass
class SimCameraSpec:
    """What the simulated camera reports. Defaults: a 12-bit RGGB colour body."""
    name: str = "PFR Alpaca Simulator"
    description: str = "Simulated all-sky camera (PFR Sentinel dev tool)"
    driver_info: str = "PFR Sentinel alpaca_camera_sim"
    driver_version: str = "1.0"
    interface_version: int = 3
    sensor_name: str = "SIM662"
    sensor_type: int = 2
    bayer_offset_x: int = 0
    bayer_offset_y: int = 0
    width: int = 1920
    height: int = 1080
    pixel_size_x: float = 2.9
    pixel_size_y: float = 2.9
    electrons_per_adu: float = 0.8
    max_adu: int = 4095
    exposure_min: float = 0.000032
    exposure_max: float = 2000.0
    exposure_resolution: float = 0.000001
    can_abort: bool = True
    can_stop: bool = True
    can_asymmetric_bin: bool = False
    max_bin: int = 2
    gain_mode: str = "range"            # "range" | "list" | "none"
    gain_min: int = 0
    gain_max: int = 570
    gains: List[str] = field(default_factory=lambda: ["Low", "Medium", "High"])
    gain: int = 100
    offset_mode: str = "range"          # "range" | "list" | "none"
    offset_min: int = 0
    offset_max: int = 255
    offsets: List[str] = field(default_factory=lambda: ["Low", "High"])
    offset: int = 10
    ccd_temperature: Optional[float] = 21.5   # None → NotImplemented
    can_set_ccd_temperature: bool = False
    readout_s: float = 0.0
    supports_imagebytes: bool = True
    scene: str = "sky"
    seed: int = 43
    device_number: int = 0
    unique_id: str = "3b2f6a1e-43a1-4f5e-9c1d-5e1a7c0a0043"
    connect_delay_s: float = 0.0        # InterfaceVersion 4 Connect() completion
    strict_param_case: bool = True      # reject PUT names not in canonical casing
    roi_locked_while_exposing: bool = False   # Bin/Start/Num PUT mid-exposure → 0x40B
    extra_devices: List[dict] = field(default_factory=list)
    readout_modes: List[str] = field(default_factory=list)   # empty → ReadoutModes NotImplemented
    readout_mode: int = 0
    # INDIGO's bridge: MaxADU stays the sensor's, but a "RAW 8" mode sends the
    # top 8 bits and a "RAW 16" mode sends the data MSB-aligned to 16 bits.
    readout_depth_from_name: bool = False
    # INDIGO 2.0 sizes an ImageBytes reply as W*H*4 and drops the 44-byte header's
    # worth from the end of the pixels; 44 reproduces it.
    imagebytes_short_by: int = 0


def indigo_readout_modes(width: int, height: int) -> List[str]:
    """INDIGO's names for an ASI camera's full-resolution modes, in its order."""
    return [f"RAW 8 {width}x{height}", f"RGB 24 {width}x{height}",
            f"RAW 16 {width}x{height}", f"Y 8 {width}x{height}"]


@dataclass
class SimFaults:
    """Device-level fault switches; each counter counts down as it fires."""
    not_connected_requests: int = 0     # next N camera members answer 0x407
    idle_blip_polls: int = 0            # first N CameraState/ImageReady reads after a start: Idle
    fail_next_exposure: bool = False    # the next exposure ends in CameraState Error
    imagearray_errors: int = 0          # next N ImageArray reads fail with 0x500
    driver_error_members: set = field(default_factory=set)   # GET → 0x500 (old V2 drivers)
    invalid_operation_reads: Dict[str, int] = field(default_factory=dict)  # next N GETs → 0x40B


@dataclass
class SimResponse:
    status: int
    content_type: str
    body: bytes


def _text(status: int, message: str) -> SimResponse:
    return SimResponse(status, "text/plain; charset=utf-8", message.encode("utf-8"))


class FakeAlpacaCamera:
    def __init__(self, spec: Optional[SimCameraSpec] = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.spec = spec or SimCameraSpec()
        self.clock = clock
        self.wallclock = wallclock
        self.faults = SimFaults()
        self.lock = threading.RLock()
        self._server_tx = itertools.count(1)
        self.device_number = self.spec.device_number
        self.request_log: List[tuple] = []
        self.scene = SceneGenerator(self.spec.width, self.spec.height, self.spec.scene,
                                    seed=self.spec.seed)
        self.reset_state()

    def reset_state(self) -> None:
        s = self.spec
        self.connected = False
        self.connecting_until = None
        self.state = IDLE
        self.bin_x = self.bin_y = 1
        self.readout_mode = s.readout_mode
        self.start_x = self.start_y = 0
        self.num_x, self.num_y = s.width, s.height
        self.gain = s.gain
        self.offset = s.offset
        self.cooler_on = False
        self.exposure_start = None
        self.exposure_start_utc = None
        self.duration = 0.0
        self.light = True
        self.reading_since = None
        self.image = None
        self.image_ready = False
        self.last_duration = None
        self.last_start_utc = None
        self.exposure_count = 0

    # -- public helpers for tests / the CLI ------------------------------
    def drop_connection(self) -> None:
        """The driver lost the camera: every camera member answers 0x407."""
        with self.lock:
            self.connected = False

    def renumber(self, device_number: int) -> None:
        with self.lock:
            self.device_number = int(device_number)

    def configured_devices(self) -> List[dict]:
        own = {"DeviceName": self.spec.name, "DeviceType": "Camera",
               "DeviceNumber": self.device_number, "UniqueID": self.spec.unique_id}
        return [own] + [dict(d) for d in self.spec.extra_devices]

    def puts(self, member: str) -> List[dict]:
        """Parameters of every PUT to ``member`` so far (oldest first)."""
        return [p for m, name, p in self.request_log if m == "PUT" and name == member]

    # -- request entry point ---------------------------------------------
    def handle(self, method: str, path: str, query: Dict[str, str], form: Dict[str, str],
               headers: Dict[str, str]) -> SimResponse:
        method = method.upper()
        parts = [unquote(p) for p in path.split("?")[0].split("/") if p]
        params = dict(query if method == "GET" else form)
        lower = {k.lower(): v for k, v in params.items()}
        client_tx = parse_uint(lower.get("clienttransactionid"))
        with self.lock:
            low_parts = [p.lower() for p in parts]
            if low_parts[:1] == ["management"]:
                return self._management(method, low_parts[1:], client_tx)
            if len(parts) != 5 or low_parts[:2] != ["api", "v1"]:
                return _text(404, f"Unknown path {path}")
            if low_parts[2] != "camera":
                return _text(404, f"No {parts[2]} devices on this server")
            try:
                number = int(parts[3])
            except ValueError:
                return _text(400, f"Device number {parts[3]!r} is not an integer")
            if number != self.device_number:
                return _text(400, f"No camera at device number {number}")
            member = low_parts[4]
            self.request_log.append((method, member, params))
            return self._device(method, member, params, lower, headers, client_tx)

    def _management(self, method, parts, client_tx) -> SimResponse:
        if method != "GET":
            return _text(405, "The Management API is GET only")
        if parts == ["apiversions"]:
            value = [1]
        elif parts == ["v1", "description"]:
            value = {"ServerName": "PFR Alpaca Camera Simulator", "Manufacturer": "PFR Sentinel",
                     "ManufacturerVersion": self.spec.driver_version, "Location": "Dev bench"}
        elif parts == ["v1", "configureddevices"]:
            value = self.configured_devices()
        else:
            return _text(404, f"Unknown management path /{'/'.join(parts)}")
        return self._json(wire.envelope(value, client_tx, next(self._server_tx)))

    def _json(self, obj) -> SimResponse:
        return SimResponse(200, wire.JSON_MIME, wire.json_bytes(obj))

    def _device(self, method, member, params, lower, headers, client_tx) -> SimResponse:
        table = GETTERS if method == "GET" else SETTERS if method == "PUT" else None
        if table is None:
            return _text(405, f"Method {method} not allowed")
        if member not in table:
            other = SETTERS if method == "GET" else GETTERS
            if member in other:
                return _text(405, f"{member} does not support {method}")
            return _text(404, f"Unknown camera member {member}")
        handler = table[member]
        server_tx = next(self._server_tx)
        try:
            self._advance()
            if member not in COMMON_MEMBERS:
                if self.faults.not_connected_requests > 0:
                    self.faults.not_connected_requests -= 1
                    raise SimError(NOT_CONNECTED, "Camera is not connected (injected)")
                if not self.connected:
                    raise SimError(NOT_CONNECTED, "Camera is not connected")
            if method == "GET" and member in self.faults.driver_error_members:
                raise SimError(0x500, f"Driver threw reading {member} (injected)")
            if method == "GET" and self.faults.invalid_operation_reads.get(member, 0) > 0:
                self.faults.invalid_operation_reads[member] -= 1
                raise SimError(INVALID_OPERATION, f"{member} is not available right now")
            if member == "imagearray":
                return self._imagearray(headers, client_tx, server_tx)
            if method == "PUT":
                handler(self, _PutParams(params, self.spec.strict_param_case))
                return self._json(wire.envelope(None, client_tx, server_tx, has_value=False))
            return self._json(wire.envelope(handler(self), client_tx, server_tx))
        except _BadRequest as exc:
            return _text(400, str(exc))
        except SimError as exc:
            return self._json(wire.envelope(None, client_tx, server_tx, exc.number, exc.message,
                                            has_value=method == "GET"))

    # -- exposure state machine ------------------------------------------
    def _advance(self) -> None:
        now = self.clock()
        if self.connecting_until is not None and now >= self.connecting_until:
            self.connecting_until = None
            self.connected = True
        if self.state == EXPOSING and now >= self.exposure_start + self.duration:
            self.state = READING
            self.reading_since = self.exposure_start + self.duration
        if self.state == READING and now >= self.reading_since + self.spec.readout_s:
            self._finish_exposure(self.duration)

    def start_exposure(self, duration: float, light: bool) -> None:
        s = self.spec
        if self.state not in (IDLE, ERROR):
            raise SimError(INVALID_OPERATION, "An exposure is already in progress")
        if not s.exposure_min <= duration <= s.exposure_max and not (duration == 0 and not light):
            raise SimError(INVALID_VALUE, f"Duration {duration} outside "
                                          f"[{s.exposure_min}, {s.exposure_max}]")
        if self.bin_x != self.bin_y and not s.can_asymmetric_bin:
            raise SimError(INVALID_OPERATION, "BinX must equal BinY on this camera")
        max_x, max_y = s.width // self.bin_x, s.height // self.bin_y
        if (self.start_x + self.num_x > max_x or self.start_y + self.num_y > max_y):
            raise SimError(INVALID_VALUE, f"Subframe {self.start_x},{self.start_y} "
                                          f"{self.num_x}x{self.num_y} exceeds {max_x}x{max_y}")
        self.duration, self.light = float(duration), bool(light)
        self.exposure_start = self.clock()
        self.exposure_start_utc = self.wallclock()
        self.state = EXPOSING
        self.image_ready = False
        self.image = None
        self.exposure_count += 1

    def stop_exposure(self, abort: bool) -> None:
        if self.state in (EXPOSING, WAITING, READING):
            if abort:
                self.state = IDLE
            else:
                self._finish_exposure(min(self.duration, self.clock() - self.exposure_start))

    def _finish_exposure(self, actual: float) -> None:
        if self.faults.fail_next_exposure:
            self.faults.fail_next_exposure = False
            self.state = ERROR
            return
        self.image = self._render(actual)
        self.image_ready = True
        self.last_duration = actual
        self.last_start_utc = self.exposure_start_utc
        self.state = IDLE

    def _render(self, actual: float) -> np.ndarray:
        s = self.spec
        full = self.scene.render(exposure_s=actual if self.light else 0.0, gain=self._gain_db(),
                                 offset=self._offset_value(), max_adu=s.max_adu,
                                 sensor_type=s.sensor_type, bayer_offset_x=s.bayer_offset_x,
                                 bayer_offset_y=s.bayer_offset_y)
        bx, by = self.bin_x, self.bin_y
        y0, x0 = self.start_y * by, self.start_x * bx
        crop = full[y0:y0 + self.num_y * by, x0:x0 + self.num_x * bx]
        if bx > 1 or by > 1:
            shape = (self.num_y, by, self.num_x, bx) + crop.shape[2:]
            crop = np.rint(crop.reshape(shape).mean(axis=(1, 3))).astype(np.int32)
        return np.ascontiguousarray(self._readout_depth(crop))

    def _readout_depth(self, frame: np.ndarray) -> np.ndarray:
        s = self.spec
        if not (s.readout_depth_from_name and s.readout_modes):
            return frame
        name = s.readout_modes[self.readout_mode]
        bits = int(s.max_adu).bit_length()
        frame = frame.astype(np.int32)
        if re.search(r"(?i)\b(?:raw|y)\s*8\b", name) and bits > 8:
            return frame >> (bits - 8)
        if re.search(r"(?i)\b(?:raw|y)\s*16\b", name) and bits < 16:
            return frame << (16 - bits)
        return frame

    def _gain_db(self) -> float:
        s = self.spec
        if s.gain_mode == "list":
            return 100.0 * self.gain          # index 0, 1, 2 → 0, 100, 200
        return float(self.gain) if s.gain_mode == "range" else 0.0

    def _offset_value(self) -> int:
        s = self.spec
        if s.offset_mode == "list":
            return 10 * self.offset
        return int(self.offset) if s.offset_mode == "range" else 0

    def camera_state_read(self) -> int:
        if self.state == EXPOSING and self.faults.idle_blip_polls > 0:
            self.faults.idle_blip_polls -= 1
            return IDLE
        return self.state

    def _imagearray(self, headers, client_tx, server_tx) -> SimResponse:
        if self.faults.imagearray_errors > 0:
            self.faults.imagearray_errors -= 1
            raise SimError(0x500, "Simulated driver failure reading the image")
        if not self.image_ready or self.image is None:
            raise SimError(INVALID_OPERATION, "No image is available")
        accept = next((v for k, v in headers.items() if k.lower() == "accept"), "")
        if self.spec.supports_imagebytes and wire.IMAGEBYTES_MIME in accept.lower():
            body = wire.encode_imagebytes(self.image, client_tx=client_tx, server_tx=server_tx)
            if self.spec.imagebytes_short_by:
                body = body[:-self.spec.imagebytes_short_by]
            return SimResponse(200, wire.IMAGEBYTES_MIME, body)
        return self._json(wire.imagearray_json(self.image, client_tx, server_tx))

    def iso_utc(self, when: Optional[datetime]) -> str:
        when = (when or self.wallclock()).astimezone(timezone.utc)
        return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}"

    def advance_clock_hint(self) -> float:
        """Seconds until the current exposure (and readout) completes; 0 when idle."""
        if self.state not in (EXPOSING, READING):
            return 0.0
        end = self.exposure_start + self.duration + self.spec.readout_s
        return max(0.0, end - self.clock())


class _BadRequest(Exception):
    """A malformed or missing parameter: HTTP 400."""


class _PutParams:
    """PUT form fields; names matched exactly unless the spec is lenient."""

    def __init__(self, params: Dict[str, str], strict: bool):
        self._params, self._strict = params, strict
        self._lower = {k.lower(): v for k, v in params.items()}

    def raw(self, name: str) -> str:
        if name in self._params:
            return self._params[name]
        if not self._strict and name.lower() in self._lower:
            return self._lower[name.lower()]
        raise _BadRequest(f"Missing parameter {name}")

    def int(self, name: str) -> int:
        text = self.raw(name)
        try:
            return int(text)
        except ValueError:
            raise _BadRequest(f"Parameter {name}={text!r} is not an integer") from None

    def float(self, name: str) -> float:
        text = self.raw(name)
        try:
            return float(text)
        except ValueError:
            raise _BadRequest(f"Parameter {name}={text!r} is not a number") from None

    def bool(self, name: str) -> bool:
        text = self.raw(name).strip().lower()
        if text not in ("true", "false"):
            raise _BadRequest(f"Parameter {name}={text!r} is not True/False")
        return text == "true"


__all__ = ["FakeAlpacaCamera", "SimCameraSpec", "SimFaults", "SimResponse", "SimError"]
