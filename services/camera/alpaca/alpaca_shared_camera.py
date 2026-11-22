"""Frame checks for an Alpaca camera that another client may be using too.

NINA (through ASCOM Remote) can change gain, offset, binning or the subframe
between two of Sentinel's frames, and can start its own exposure at any time.
``prepare_frame`` runs before each exposure: it reads gain, offset and the ROI
back and re-asserts what this session wants. ``check_delivered`` runs on each
downloaded image: an image that is not this frame (wrong size, or a duration
that is not the one requested) is dropped with a rate-limited warning and a
``FrameSkipped`` — the loop takes the next frame instead of reconnecting.
"""
import numpy as np

from ..errors import CameraConfigurationError, FrameSkipped
from .alpaca_protocol import AlpacaInvalidOperationError
from .alpaca_sensor import RGB, effective_max_adu

DURATION_TOLERANCE_S = 0.5
DURATION_TOLERANCE_FRACTION = 0.05
DURATION_MISMATCHES_BEFORE_IGNORING = 3


def prepare_frame(device, session, warnings) -> None:
    """Gain, offset and full-frame ROI as this session wants them, or ``FrameSkipped``."""
    try:
        device.reassert_control("gain", session.gain)
        offset = getattr(session, "offset", None)
        if offset is not None:
            device.reassert_control("offset", offset)
        wrong = device.roi_mismatches()
        if not wrong:
            return
        described = ", ".join(f"{m}={actual} (want {want})" for m, want, actual in wrong)
        warnings.limited("roi-changed", f"Alpaca camera is not set to full frame ({described}) "
                                        f"— another client changed it; restoring")
        if not device.set_full_frame_roi([(m, want) for m, want, _ in wrong]):
            raise FrameSkipped("camera busy — the full-frame ROI cannot be restored while "
                               "another client is exposing")
    except AlpacaInvalidOperationError as exc:
        raise FrameSkipped(f"camera busy — another client is exposing ({exc})") from exc


def _duration_mismatch(device, duration, warnings) -> bool:
    state = warnings.state
    if state.get("ignore_last_duration"):
        return False
    last = device.last_exposure_duration()
    if last is None or duration <= 0:
        return False
    tolerance = max(DURATION_TOLERANCE_S, DURATION_TOLERANCE_FRACTION * duration)
    if abs(last - duration) <= tolerance:
        state["duration_mismatches"] = 0
        return False
    count = state.get("duration_mismatches", 0) + 1
    state["duration_mismatches"] = count
    if count >= DURATION_MISMATCHES_BEFORE_IGNORING:
        # Every frame disagreeing the same way is a driver reporting another
        # unit, not another client: stop checking rather than drop every frame.
        state["ignore_last_duration"] = True
        warnings.once("last-duration-ignored",
                      f"Alpaca camera's LastExposureDuration ({last:g} s) never matches the "
                      f"requested exposure ({duration:g} s); no longer checking it")
        return False
    warnings.limited("foreign-image",
                     f"Alpaca image is from a {last:g} s exposure, not this frame's {duration:g} s "
                     f"— another client's image? Frame dropped")
    return True


def check_delivered(device, info, image: np.ndarray, duration: float, warnings) -> None:
    """Raise unless ``image`` is the full-frame image of the exposure just taken."""
    want_rgb = info.bayer_pattern == RGB
    rank_ok = (image.ndim == 3 and image.shape[2] == 3) if want_rgb else image.ndim == 2
    if not rank_ok:
        raise CameraConfigurationError(
            f"Alpaca camera delivered an image of shape {image.shape} for SensorType "
            f"{info.sensor_type}")
    height, width = image.shape[:2]
    if (width, height) != (info.width, info.height):
        warnings.limited("frame-shape",
                         f"Alpaca camera delivered a {width}x{height} image, expected "
                         f"{info.width}x{info.height} (binned or subframed by another client?) "
                         f"— frame dropped, full frame restored")
        device.set_full_frame_roi()
        raise FrameSkipped(f"delivered image is {width}x{height}, not full frame")
    if _duration_mismatch(device, duration, warnings):
        raise FrameSkipped("delivered image is not this frame's exposure")
    top = effective_max_adu(info.max_adu)
    if image.size and image.max() > top:
        warnings.once("max-adu", f"Alpaca camera delivered pixels above its MaxADU {info.max_adu} "
                                 f"(max {image.max()}): the driver's MaxADU is wrong or its data "
                                 f"is not right-aligned; values above it are clipped")


__all__ = ["prepare_frame", "check_delivered"]
