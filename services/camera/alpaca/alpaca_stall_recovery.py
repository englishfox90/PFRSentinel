"""Recovery for an Alpaca camera that answers but never finishes an exposure (issue #177).

Seen with ZWO's ASCOM driver behind ASCOM Remote once NINA opened another ZWO
camera on the same PC: the server kept answering, the driver kept reporting
Connected and Exposing, and no exposure ever completed. Our ordinary reconnect
leaves a driver that reads Connected alone (on a shared server another client
may be using it), so it changed nothing, and the loop went timeout → reconnect
→ timeout for as long as capture ran; only Stop + Start, whose calibration
starts with short exposures, got frames again.

Two steps, both only after ``ExposureStalledError`` (never on an ordinary
transport outage):

- **Probe** — after any reconnect that follows a stalled exposure, one short
  exposure (``PROBE_EXPOSURE_S``, or the session's own when shorter) must finish
  within ``PROBE_GRACE_S`` before long exposures resume. A probe that stalls
  fails the reconnect, so the loop's error count climbs instead of resetting.
  A probe that finishes does not clear the stall count; a full frame does.
- **Reopen** — once ``REOPEN_AFTER_STALLS`` exposures in a row have stalled, the
  reconnect makes the driver close and reopen this camera
  (``AlpacaCameraDevice.reopen``), at most once per ``REOPEN_MIN_INTERVAL_S``,
  with one WARNING saying why and what another client of the same camera sees.
"""
import time

from services.logger import app_logger

from ..errors import ExposureStalledError, FrameSkipped

PROBE_EXPOSURE_S = 1.0
PROBE_GRACE_S = 15.0
REOPEN_AFTER_STALLS = 2
REOPEN_MIN_INTERVAL_S = 600.0


def should_reopen(session) -> bool:
    """True when this reconnect should reopen the camera in the driver; records it."""
    stalls = int(getattr(session, 'stalled_exposures', 0) or 0)
    if stalls < REOPEN_AFTER_STALLS:
        return False
    last = getattr(session, '_last_driver_reopen', None)
    now = time.monotonic()
    if last is not None and now - last < REOPEN_MIN_INTERVAL_S:
        app_logger.debug(f"Alpaca: {stalls} stalled exposures; driver reopened "
                         f"{now - last:.0f}s ago, not again yet")
        return False
    session._last_driver_reopen = now
    app_logger.warning(
        f"Alpaca camera answers but has not finished an exposure ({stalls} in a row): "
        f"reopening it in the driver (Connected=false, then true). Another program "
        f"connected to this same camera through the server may see it disconnect briefly.")
    return True


def probe_exposure(session) -> bool:
    """One short exposure that must finish; False (logged) when it does not.

    Another client exposing (``FrameSkipped``) counts as a camera that works.
    The image is not downloaded: the next StartExposure discards it.
    """
    from .alpaca_capture_frame import wait_for_image
    device = session.camera
    info = session._connection.static_info
    if device is None or info is None:
        return False
    seconds = min(PROBE_EXPOSURE_S, float(session.exposure_seconds or PROBE_EXPOSURE_S))
    try:
        duration = device.start(info.clamp_exposure(seconds))
        wait_for_image(session, device, duration, time.monotonic(), grace_s=PROBE_GRACE_S)
    except FrameSkipped:
        return True
    except ExposureStalledError as exc:
        session.stalled_exposures = int(getattr(session, 'stalled_exposures', 0) or 0) + 1
        app_logger.warning(f"Alpaca camera reconnected but did not finish a {seconds:g}s "
                           f"test exposure: {exc}")
        return False
    except Exception as exc:
        app_logger.warning(f"Alpaca test exposure after reconnect failed: {exc}")
        return False
    # stalled_exposures is left as it is: only a full-length exposure clears it,
    # so a camera that finishes 1 s but not 30 s still reaches the reopen.
    session.log(f"✓ Alpaca camera finished a {duration:g}s test exposure; resuming capture")
    return True


__all__ = ["probe_exposure", "should_reopen", "PROBE_EXPOSURE_S", "PROBE_GRACE_S",
           "REOPEN_AFTER_STALLS", "REOPEN_MIN_INTERVAL_S"]
