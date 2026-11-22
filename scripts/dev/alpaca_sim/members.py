"""ICameraV3/V4 member table for the simulator: one GET and/or PUT handler per member.

Handlers take the ``FakeAlpacaCamera`` (``cam``) and, for PUT, its parsed form
(``p``). A GET returns the Value; any handler raises ``SimError`` for an
Alpaca device error. The camera has already been checked for connection.
"""


class SimError(Exception):
    def __init__(self, number: int, message: str):
        super().__init__(message)
        self.number, self.message = number, message


NOT_IMPLEMENTED, INVALID_VALUE, INVALID_OPERATION = 0x400, 0x401, 0x40B
IDLE, EXPOSING, READING = 0, 2, 3


def parse_uint(text) -> int:
    """ClientTransactionID / ClientID: a uint32, garbage reads 0 (as ASCOM asks)."""
    try:
        value = int(str(text))
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= 0xFFFFFFFF else 0


def _not_impl(what):
    raise SimError(NOT_IMPLEMENTED, f"{what} is not implemented by this camera")


def _require_v4(cam, member):
    if cam.spec.interface_version < 4:
        _not_impl(member)


# -- gain / offset in range or list mode -------------------------------------
def _range_or_list(cam, kind, part):
    mode = getattr(cam.spec, f"{kind}_mode")
    if part in ("min", "max"):
        if mode != "range":
            _not_impl(f"{kind.title()}{part.title()}")
        return getattr(cam.spec, f"{kind}_{part}")
    if part == "list":
        if mode != "list":
            _not_impl(f"{kind.title()}s")
        return list(getattr(cam.spec, f"{kind}s"))
    if mode == "none":
        _not_impl(kind.title())
    return getattr(cam, kind)


def _set_range_or_list(cam, kind, value):
    spec = cam.spec
    mode = getattr(spec, f"{kind}_mode")
    if mode == "none":
        _not_impl(kind.title())
    if mode == "range":
        lo, hi = getattr(spec, f"{kind}_min"), getattr(spec, f"{kind}_max")
    else:
        lo, hi = 0, len(getattr(spec, f"{kind}s")) - 1
    if not lo <= value <= hi:
        raise SimError(INVALID_VALUE, f"{kind.title()} {value} outside [{lo}, {hi}]")
    setattr(cam, kind, value)


# -- exposure-related reads ---------------------------------------------------
def _percent(cam):
    if cam.state == EXPOSING and cam.duration > 0:
        return int(min(100, max(0, 100 * (cam.clock() - cam.exposure_start) / cam.duration)))
    if cam.state == READING or cam.image_ready:
        return 100
    raise SimError(INVALID_OPERATION, "No exposure in progress")


def _last(cam, what):
    if cam.last_duration is None:
        raise SimError(INVALID_OPERATION, "No image has been taken yet")
    return cam.last_duration if what == "duration" else cam.iso_utc(cam.last_start_utc)


def _image_ready(cam):
    if cam.state == EXPOSING and cam.faults.idle_blip_polls > 0:
        return False
    return bool(cam.image_ready)


def _bayer(cam, axis):
    if cam.spec.sensor_type < 2:
        _not_impl(f"BayerOffset{axis.upper()}")
    return getattr(cam.spec, f"bayer_offset_{axis}")


def _ccd_temperature(cam):
    if cam.spec.ccd_temperature is None:
        _not_impl("CCDTemperature")
    return float(cam.spec.ccd_temperature)


def _cooler_on(cam):
    if not cam.spec.can_set_ccd_temperature:
        _not_impl("CoolerOn")
    return cam.cooler_on


def _connecting(cam):
    _require_v4(cam, "Connecting")
    return cam.connecting_until is not None


def _device_state(cam):
    _require_v4(cam, "DeviceState")
    items = [("CameraState", cam.camera_state_read()), ("ImageReady", _image_ready(cam))]
    if cam.spec.ccd_temperature is not None:
        items.append(("CCDTemperature", float(cam.spec.ccd_temperature)))
    return [{"Name": n, "Value": v} for n, v in items]


def _readout(cam, what):
    if not cam.spec.readout_modes:
        _not_impl("ReadoutModes" if what == "modes" else "ReadoutMode")
    return list(cam.spec.readout_modes) if what == "modes" else int(cam.readout_mode)


GETTERS = {
    "connected": lambda c: bool(c.connected),
    "connecting": _connecting,
    "devicestate": _device_state,
    "name": lambda c: c.spec.name,
    "description": lambda c: c.spec.description,
    "driverinfo": lambda c: c.spec.driver_info,
    "driverversion": lambda c: c.spec.driver_version,
    "interfaceversion": lambda c: int(c.spec.interface_version),
    "supportedactions": lambda c: [],
    "sensorname": lambda c: c.spec.sensor_name,
    "sensortype": lambda c: int(c.spec.sensor_type),
    "bayeroffsetx": lambda c: _bayer(c, "x"),
    "bayeroffsety": lambda c: _bayer(c, "y"),
    "cameraxsize": lambda c: c.spec.width,
    "cameraysize": lambda c: c.spec.height,
    "pixelsizex": lambda c: c.spec.pixel_size_x,
    "pixelsizey": lambda c: c.spec.pixel_size_y,
    "electronsperadu": lambda c: c.spec.electrons_per_adu,
    "maxadu": lambda c: int(c.spec.max_adu),
    "exposuremin": lambda c: c.spec.exposure_min,
    "exposuremax": lambda c: c.spec.exposure_max,
    "exposureresolution": lambda c: c.spec.exposure_resolution,
    "canabortexposure": lambda c: c.spec.can_abort,
    "canstopexposure": lambda c: c.spec.can_stop,
    "canasymmetricbin": lambda c: c.spec.can_asymmetric_bin,
    "canpulseguide": lambda c: False,
    "canfastreadout": lambda c: False,
    "cangetcoolerpower": lambda c: False,
    "cansetccdtemperature": lambda c: c.spec.can_set_ccd_temperature,
    "hasshutter": lambda c: False,
    "maxbinx": lambda c: c.spec.max_bin,
    "maxbiny": lambda c: c.spec.max_bin,
    "binx": lambda c: c.bin_x,
    "biny": lambda c: c.bin_y,
    "startx": lambda c: c.start_x,
    "starty": lambda c: c.start_y,
    "numx": lambda c: c.num_x,
    "numy": lambda c: c.num_y,
    "gain": lambda c: _range_or_list(c, "gain", "value"),
    "gainmin": lambda c: _range_or_list(c, "gain", "min"),
    "gainmax": lambda c: _range_or_list(c, "gain", "max"),
    "gains": lambda c: _range_or_list(c, "gain", "list"),
    "offset": lambda c: _range_or_list(c, "offset", "value"),
    "offsetmin": lambda c: _range_or_list(c, "offset", "min"),
    "offsetmax": lambda c: _range_or_list(c, "offset", "max"),
    "offsets": lambda c: _range_or_list(c, "offset", "list"),
    "ccdtemperature": _ccd_temperature,
    "cooleron": _cooler_on,
    "camerastate": lambda c: c.camera_state_read(),
    "imageready": _image_ready,
    "percentcompleted": _percent,
    "lastexposureduration": lambda c: _last(c, "duration"),
    "lastexposurestarttime": lambda c: _last(c, "start"),
    "readoutmodes": lambda c: _readout(c, "modes"),
    "readoutmode": lambda c: _readout(c, "mode"),
    "imagearray": None,   # served by FakeAlpacaCamera._imagearray (binary/JSON)
}


# -- PUT handlers -------------------------------------------------------------
def _put_connected(cam, p):
    value = p.bool("Connected")
    cam.connected = value
    cam.connecting_until = None
    if not value:
        cam.stop_exposure(abort=True)


def _put_connect(cam, p):
    _require_v4(cam, "Connect")
    if cam.connected:
        return
    if cam.spec.connect_delay_s > 0:
        cam.connecting_until = cam.clock() + cam.spec.connect_delay_s
    else:
        cam.connected = True


def _put_disconnect(cam, p):
    _require_v4(cam, "Disconnect")
    cam.connected = False
    cam.connecting_until = None
    cam.stop_exposure(abort=True)


def _roi_unlocked(cam):
    if cam.spec.roi_locked_while_exposing and cam.state in (EXPOSING, READING):
        raise SimError(INVALID_OPERATION, "The subframe cannot change during an exposure")


def _put_bin(cam, p, axis):
    value = p.int(f"Bin{axis.upper()}")
    _roi_unlocked(cam)
    if not 1 <= value <= cam.spec.max_bin:
        raise SimError(INVALID_VALUE, f"Bin{axis.upper()} {value} outside [1, {cam.spec.max_bin}]")
    setattr(cam, f"bin_{axis}", value)


def _put_nonneg(cam, p, name, attr, minimum):
    value = p.int(name)
    _roi_unlocked(cam)
    if value < minimum:
        raise SimError(INVALID_VALUE, f"{name} {value} must be at least {minimum}")
    setattr(cam, attr, value)


def _put_readout_mode(cam, p):
    _readout(cam, "mode")
    value = p.int("ReadoutMode")
    _roi_unlocked(cam)
    if not 0 <= value < len(cam.spec.readout_modes):
        raise SimError(INVALID_VALUE, f"ReadoutMode {value} outside "
                                      f"[0, {len(cam.spec.readout_modes) - 1}]")
    cam.readout_mode = value


def _put_start(cam, p):
    cam.start_exposure(p.float("Duration"), p.bool("Light"))


def _put_abort(cam, p):
    if not cam.spec.can_abort:
        _not_impl("AbortExposure")
    cam.stop_exposure(abort=True)


def _put_stop(cam, p):
    if not cam.spec.can_stop:
        _not_impl("StopExposure")
    cam.stop_exposure(abort=False)


def _put_cooler(cam, p):
    if not cam.spec.can_set_ccd_temperature:
        _not_impl("CoolerOn")
    cam.cooler_on = p.bool("CoolerOn")


SETTERS = {
    "connected": _put_connected,
    "connect": _put_connect,
    "disconnect": _put_disconnect,
    "binx": lambda c, p: _put_bin(c, p, "x"),
    "biny": lambda c, p: _put_bin(c, p, "y"),
    "startx": lambda c, p: _put_nonneg(c, p, "StartX", "start_x", 0),
    "starty": lambda c, p: _put_nonneg(c, p, "StartY", "start_y", 0),
    "numx": lambda c, p: _put_nonneg(c, p, "NumX", "num_x", 1),
    "numy": lambda c, p: _put_nonneg(c, p, "NumY", "num_y", 1),
    "gain": lambda c, p: _set_range_or_list(c, "gain", p.int("Gain")),
    "offset": lambda c, p: _set_range_or_list(c, "offset", p.int("Offset")),
    "cooleron": _put_cooler,
    "readoutmode": _put_readout_mode,
    "startexposure": _put_start,
    "abortexposure": _put_abort,
    "stopexposure": _put_stop,
}
