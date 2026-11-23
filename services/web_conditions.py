"""
HTTP route for ``GET /conditions`` — the imaging-conditions payload.

Gated exactly like the control routes (``web_control.authorize``): Host
allow-list first, then the bearer token, failing closed with 503
``control_disabled`` when no token is configured, and no CORS header. The
request thread only reads the thread-safe conditions store.
"""
from __future__ import annotations

from . import api_conditions, web_control
from .imaging_conditions import get_conditions_store


def serve_conditions(handler) -> None:
    if not web_control.authorize(handler):
        return
    web_control._send_json(handler, 200, get_conditions_store().snapshot())


def route_get(handler) -> bool:
    """Serve ``GET /conditions``. Returns False if the path isn't ours."""
    path = handler.path.split("?", 1)[0].rstrip("/") or "/"
    if path != api_conditions.CONDITIONS_PATH:
        return False
    serve_conditions(handler)
    return True
