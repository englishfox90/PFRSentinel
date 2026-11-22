"""Open the configured Alpaca camera for the headless runner (issue #43).

The GUI builds its session on the controller's start worker; headless has no
Qt loop, so this does the same steps inline: parameters from config, the
session factory, ``connect_camera()``, then the learned UniqueID / Name written
back (headless has a single thread, so that write is safe here). A camera that
drops mid-run is reopened with ``reconnect_after_error`` — one bounded
re-handshake per failed frame, never the ZWO USB ladder. With
``camera_auto_recovery`` off that is one reconnect, as in the GUI: a frame that
fails again before a good one pauses capture (``recovery_exhausted``).

The connection's own lines (``✓ Connected …`` / ``✗ Alpaca connect failed …``)
go to the log at DEBUG during the open; the runner logs one line either way.
"""
from typing import Callable, Optional

from .camera.errors import CameraError
from .capture_schedule_window import gate_for_config
from .camera.camera_source import alpaca_settings, persist_alpaca_identity
from .camera.session_factory import build_alpaca_session
from .camera.session_params import read_capture_params
from .logger import app_logger


def open_alpaca_camera(config, log: Callable[[str], None]):
    """A connected ``AlpacaCamera`` for ``config``, or None (reason logged)."""
    settings = alpaca_settings(config)
    if not settings['host']:
        log("ERROR: No Alpaca server host is configured (alpaca_camera.host)")
        return None
    auto_recovery = config.get('camera_auto_recovery', True) is not False
    params = read_capture_params(config, auto_recovery)
    cam = build_alpaca_session(params, gate_for_config(config), on_error=None,
                               on_calibration=None)
    cam.on_log_callback = None
    if not cam.connect_camera():
        log(f"ERROR: Failed to connect to the Alpaca camera at "
            f"{settings['host']}:{settings['port']} #{settings['device_number']}: "
            f"{cam.last_connect_error or 'unknown error'}")
        return None
    cam.on_log_callback = lambda msg: app_logger.info(msg)
    # Headless drives capture_single_frame itself, with no capture thread; the
    # frame path aborts an exposure whenever the session is not capturing.
    cam.is_capturing = True
    unique_id, name = cam.learned_identity()
    if persist_alpaca_identity(config, unique_id, name):
        log(f"Persisted Alpaca camera identity: {name or '?'} ({unique_id or 'no UniqueID'})")
    log(f"✓ Connected to Alpaca camera: {name or '?'} "
        f"({settings['host']}:{settings['port']} #{settings['device_number']})")
    return cam


def is_expected_failure(cam, error) -> bool:
    """A network camera's dropped link (or the code's own plain message), whose
    traceback belongs at DEBUG; anything else is a bug and keeps it."""
    return (getattr(cam, 'backend', None) == 'alpaca'
            and (isinstance(error, CameraError) or type(error) is Exception))


def note_good_frame(cam) -> None:
    """A frame came through: the single reconnect (recovery off) is earned back."""
    if getattr(cam, 'backend', None) == 'alpaca':
        cam._headless_reconnect_spent = False


def recovery_exhausted(cam) -> bool:
    """Recovery is off and the one reconnect since the last good frame is spent."""
    return (getattr(cam, 'backend', None) == 'alpaca'
            and getattr(cam, 'auto_recovery_enabled', True) is False
            and bool(getattr(cam, '_headless_reconnect_spent', False)))


def reconnect_after_error(cam, log: Callable[[str], None]) -> Optional[bool]:
    """Reopen an Alpaca session after a failed frame; None for any other backend.

    With automatic recovery off only the first failure since a good frame
    reconnects; after that it returns False without touching the server.
    """
    if getattr(cam, 'backend', None) != 'alpaca':
        return None
    if recovery_exhausted(cam):
        log("Alpaca camera failed again after its single reconnect; automatic recovery "
            "is off — not reconnecting")
        return False
    if getattr(cam, 'auto_recovery_enabled', True) is False:
        cam._headless_reconnect_spent = True
    try:
        ok = bool(cam.reconnect_camera_safe())
    except Exception as e:
        app_logger.debug(f"Alpaca reconnect raised: {e}")
        ok = False
    log("✓ Alpaca camera reconnected" if ok
        else "Alpaca camera still unreachable; retrying on the next frame")
    return ok


def log_failure_trace(cam, error, log: Callable[[str], None]) -> None:
    """The traceback of a failed frame: DEBUG for an expected network failure."""
    import traceback
    text = traceback.format_exc()
    if is_expected_failure(cam, error):
        app_logger.debug(text)
    else:
        log(text)


def after_failed_frame(cam, log: Callable[[str], None]) -> bool:
    """Recover from a failed frame; True when the runner should pause capture.

    Only an Alpaca session reconnects here (ZWO recovers inside its own frame
    path). With recovery off and the single reconnect spent, capture pauses
    instead, and a resume earns one fresh reconnect.
    """
    if recovery_exhausted(cam):
        log("Capture paused: the camera failed again after its single reconnect and "
            "automatic recovery is off. Resume through the control API or restart once "
            "the camera is back.")
        note_good_frame(cam)
        return True
    reconnect_after_error(cam, log)
    return False
