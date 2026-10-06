"""
HTTP route for ``POST /nina/target`` — the NINA plugin's target push.

Reached from ``web_control.route_post`` and gated exactly like the capture
routes: Host allow-list, bearer token, fail closed with no token, no CORS
header, 4096-byte body cap.  The path is fixed rather than living under the
configurable control path because it is not a capture command.

Unlike a capture command nothing here touches Qt: the request thread only
writes the thread-safe :mod:`services.nina_target_store`, which the overlay
renderer reads on its own thread.
"""
from __future__ import annotations

from . import api_target, web_control
from .logger import app_logger
from .nina_target_store import get_nina_target_store


def _describe(target) -> str:
    fov = ""
    if target.fov_w_deg is not None:
        fov = f", FOV {target.fov_w_deg:.2f}x{target.fov_h_deg:.2f} deg"
    rotation = "" if target.rotation_deg is None else f", PA {target.rotation_deg:.1f} deg"
    return (f"'{target.name}' RA {target.ra_deg:.4f} Dec {target.dec_deg:+.4f}"
            f"{fov}{rotation} from {target.source}")


def serve_target(handler):
    """Validate and store a pushed target, answering with the stored value."""
    if not web_control.authorize(handler):
        return

    raw_body, error = web_control._read_body(handler)
    if error:
        app_logger.warning(f"NINA target refused (HTTP {error[0]}): {error[1]}")
        web_control._send_error(handler, *error)
        return

    params, error = api_target.parse_target_request(raw_body)
    if error:
        status, message = error
        # The plugin backs off and logs its own warning, but only in NINA's
        # log; without this line Sentinel's log shows a bare 400.
        app_logger.warning(f"NINA target refused (HTTP {status}): {message}")
        web_control._send_error(handler, status, message,
                                web_control.ERR_BODY_TOO_LARGE if status == 413
                                else web_control.ERR_BAD_REQUEST)
        return

    store = get_nina_target_store()
    target = params["target"]
    changed = store.set(target)
    if target is None:
        if changed:
            app_logger.info("NINA target cleared via HTTP API")
        else:
            app_logger.debug("NINA target clear repeated (no target stored)")
    elif changed:
        app_logger.info(f"NINA target set via HTTP API: {_describe(target)}")
    else:
        app_logger.debug(f"NINA target heartbeat: '{target.name}'")

    web_control._send_json(handler, 200,
                           api_target.build_target_result(store.snapshot(), changed))


def route_post(handler) -> bool:
    """Serve ``POST /nina/target``. Returns False if the path isn't ours."""
    path = handler.path.split("?", 1)[0].rstrip("/") or "/"
    if path != api_target.TARGET_PATH:
        return False
    serve_target(handler)
    return True
