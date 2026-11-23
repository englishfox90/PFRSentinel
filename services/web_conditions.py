"""
HTTP route for ``GET /conditions`` — the imaging-conditions payload.

Gated exactly like the control routes (``web_control.authorize``): Host
allow-list first, then the bearer token, failing closed with 503
``control_disabled`` when no token is configured, and no CORS header. The
request thread only reads the thread-safe conditions store, and notes the
request (client label from the User-Agent, accepted or refused) for Live
Monitoring's "NINA last checked" line.
"""
from __future__ import annotations

from . import api_conditions, web_control
from .imaging_conditions import get_conditions_store
from .imaging_conditions.nina_checkin import get_nina_checkins


def serve_conditions(handler) -> None:
    accepted = web_control.authorize(handler)
    try:
        agent = handler.headers.get("User-Agent") if handler.headers else None
        get_nina_checkins().record(agent, accepted=accepted)
    except Exception:
        pass
    if not accepted:
        return
    web_control._send_json(handler, 200, get_conditions_store().snapshot())


def route_get(handler) -> bool:
    """Serve ``GET /conditions``. Returns False if the path isn't ours."""
    path = handler.path.split("?", 1)[0].rstrip("/") or "/"
    if path != api_conditions.CONDITIONS_PATH:
        return False
    serve_conditions(handler)
    return True
