"""Open the configured Alpaca camera for the headless runner (issue #43).

The GUI builds its session on the controller's start worker; headless has no
Qt loop, so this does the same steps inline: parameters from config, the
session factory, ``connect_camera()``, then the learned UniqueID / Name written
back (headless has a single thread, so that write is safe here). A camera that
drops mid-run is reopened with ``reconnect_after_error`` — one bounded
re-handshake per failed frame, never the ZWO USB ladder.
"""
from typing import Callable, Optional

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
    cam.on_log_callback = lambda msg: app_logger.info(msg)
    if not cam.connect_camera():
        log(f"ERROR: Failed to connect to the Alpaca camera at "
            f"{settings['host']}:{settings['port']} #{settings['device_number']}: "
            f"{cam.last_connect_error or 'unknown error'}")
        return None
    # Headless drives capture_single_frame itself, with no capture thread; the
    # frame path aborts an exposure whenever the session is not capturing.
    cam.is_capturing = True
    unique_id, name = cam.learned_identity()
    if persist_alpaca_identity(config, unique_id, name):
        log(f"Persisted Alpaca camera identity: {name or '?'} ({unique_id or 'no UniqueID'})")
    log(f"✓ Connected to Alpaca camera: {name or '?'} "
        f"({settings['host']}:{settings['port']} #{settings['device_number']})")
    return cam


def reconnect_after_error(cam, log: Callable[[str], None]) -> Optional[bool]:
    """Reopen an Alpaca session after a failed frame; None for any other backend."""
    if getattr(cam, 'backend', None) != 'alpaca':
        return None
    try:
        ok = bool(cam.reconnect_camera_safe())
    except Exception as e:
        app_logger.debug(f"Alpaca reconnect raised: {e}")
        ok = False
    log("✓ Alpaca camera reconnected" if ok
        else "Alpaca camera still unreachable; retrying on the next frame")
    return ok
