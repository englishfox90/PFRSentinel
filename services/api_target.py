"""
Pure logic for ``POST /nina/target`` — the target NINA is imaging.

The NINA plugin pushes the running sequencer target (name, J2000 coordinates,
the imaging camera's field of view and rotation) so the all-sky overlay can
mark where the main scope is pointed.  This module validates that payload into
a :class:`~services.nina_target_store.NinaTarget`, shapes the response, and
carries the OpenAPI description that ``services/api_docs.py`` merges in.  The
socket-facing half is ``services/web_target.py``.

Like ``services/api_control.py`` it is ignorant of Qt and sockets: plain values
in, plain values out, errors as ``(http_status, message)`` with no HTTP error
codes — those are assigned by the route.

**Rotation convention.** ``rotation_deg`` is the sky position angle, degrees
east of north, of the camera's "up" (its height axis).  At 0 the width runs
east-west and the height north-south.  Only J2000 coordinates are accepted:
the renderer precesses them to the observation date like every catalogue layer.
"""
from __future__ import annotations

import json
import math
import unicodedata

from .api_control import MAX_BODY_BYTES
from .nina_target_store import NinaTarget

TARGET_PATH = "/nina/target"

MAX_NAME_CHARS = 64
MAX_SOURCE_CHARS = 32
# A wider field than this is not a telescope; it is a client bug or a lens the
# overlay could not draw usefully on a fisheye anyway.
MAX_FOV_DEG = 60

ROTATION_CONVENTION = (
    "rotation_deg is the sky position angle, in degrees east of north, of the "
    "camera's 'up' (its height axis): at 0 the width runs east-west and the "
    "height north-south. Only J2000 coordinates are accepted."
)

TARGET_ROUTES = [
    {
        "path": TARGET_PATH,
        "method": "post",
        "command": None,
        "summary": "Report the target NINA is imaging",
        "description": (
            "Store the current imaging target so the all-sky overlay can mark it "
            "and label it with its name: the imaging camera's field of view as a "
            "box with a small centre cross, or a reticle when no box can be drawn "
            "(field of view unknown, part of it below the horizon, or too small "
            "at the output resolution). "
            "Send {\"target\": null} to clear it. The overlay stops drawing a "
            "target that has not been re-sent for the configured staleness "
            "period, so a client should repeat it as a heartbeat. "
            + ROTATION_CONVENTION + " Requires a bearer token."
        ),
    },
]

TARGET_RESULT_FIELDS = [
    ("accepted", "boolean", "Always true on a 200: the request was valid and stored."),
    ("changed", "boolean",
     "Whether the stored target differs from the previous one; false for a heartbeat."),
    ("target", "object", "The stored target after normalisation, or null when cleared."),
    ("age_s", "number", "Seconds since the stored value was received; 0 on this response."),
    ("message", "string",
     "'Target stored.', 'Target cleared.' or 'Target unchanged.'"),
]


def _is_number(value) -> bool:
    # bool is an int subclass; `true` for a coordinate is a client bug.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_number(body: dict, key: str):
    """``(float, None)`` or ``(None, message)`` for a required finite number."""
    value = body.get(key)
    message = f"'{key}' must be a finite number of degrees."
    if not _is_number(value):
        return None, message
    # json.loads accepts NaN and Infinity, and an integer literal of any length,
    # which math.isfinite and float() refuse with OverflowError.
    try:
        value = float(value)
    except OverflowError:
        return None, message
    if not math.isfinite(value):
        return None, message
    return value, None


def clean_name(raw: str) -> str:
    """Strip control characters, collapse whitespace, cap at MAX_NAME_CHARS."""
    visible = "".join(" " if c.isspace() else c for c in raw
                      if c.isspace() or unicodedata.category(c)[0] != "C")
    return " ".join(visible.split())[:MAX_NAME_CHARS].rstrip()


def _parse_target(body: dict):
    """Validate the ``target`` object into ``(NinaTarget, None)`` or ``(None, message)``."""
    name = body.get("name")
    if not isinstance(name, str):
        return None, "'name' is required and must be a string."
    name = clean_name(name)
    if not name:
        return None, "'name' must contain visible characters."

    ra, err = _finite_number(body, "ra_deg")
    if err:
        return None, err
    if not 0.0 <= ra < 360.0:
        return None, "'ra_deg' must be at least 0 and below 360."

    dec, err = _finite_number(body, "dec_deg")
    if err:
        return None, err
    if not -90.0 <= dec <= 90.0:
        return None, "'dec_deg' must be between -90 and 90."

    epoch = body.get("epoch")
    if epoch is not None and (not isinstance(epoch, str) or epoch.strip().upper() != "J2000"):
        return None, "Only J2000 coordinates are accepted ('epoch' must be 'J2000')."

    has_w = body.get("fov_w_deg") is not None
    has_h = body.get("fov_h_deg") is not None
    fov_w = fov_h = None
    if has_w != has_h:
        return None, "'fov_w_deg' and 'fov_h_deg' must be sent together."
    if has_w:
        for key in ("fov_w_deg", "fov_h_deg"):
            value, err = _finite_number(body, key)
            if err:
                return None, err
            if not 0.0 < value <= MAX_FOV_DEG:
                return None, f"'{key}' must be above 0 and at most {MAX_FOV_DEG} degrees."
            if key == "fov_w_deg":
                fov_w = value
            else:
                fov_h = value

    rotation = None
    if body.get("rotation_deg") is not None:
        rotation, err = _finite_number(body, "rotation_deg")
        if err:
            return None, err
        rotation = rotation % 360.0

    source = body.get("source")
    if source is None:
        source = "nina"
    elif not isinstance(source, str):
        return None, "'source' must be a string."
    else:
        source = clean_name(source)[:MAX_SOURCE_CHARS].rstrip() or "nina"

    return NinaTarget(name=name, ra_deg=ra, dec_deg=dec, fov_w_deg=fov_w,
                      fov_h_deg=fov_h, rotation_deg=rotation, source=source), None


def parse_target_request(raw_body):
    """Validate a ``POST /nina/target`` body into ``(params, error)``.

    ``params`` is ``{"target": NinaTarget | None}``; ``None`` means clear.
    Exactly one of ``params`` / ``error`` is non-``None``; ``error`` is
    ``(http_status, message)``.  Unknown keys are ignored so a newer plugin can
    send fields this server does not use yet.
    """
    missing = (400, "'target' is required (null clears the target).")
    if raw_body is None:
        return None, missing
    if isinstance(raw_body, (bytes, bytearray)):
        if len(raw_body) > MAX_BODY_BYTES:
            return None, (413, "Request body too large.")
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            return None, (400, "Request body must be UTF-8 JSON.")
    if isinstance(raw_body, str):
        if not raw_body.strip():
            return None, missing
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            return None, (400, "Request body must be valid JSON.")
    else:
        body = raw_body

    if not isinstance(body, dict):
        return None, (400, "Request body must be a JSON object.")
    if "target" not in body:
        return None, missing

    target = body["target"]
    if target is None:
        return {"target": None}, None
    if not isinstance(target, dict):
        return None, (400, "'target' must be a JSON object or null.")

    parsed, message = _parse_target(target)
    if message:
        return None, (400, message)
    return {"target": parsed}, None


def build_target_result(snapshot, changed: bool) -> dict:
    """Shape the 200 response from a store snapshot. Plain values in, dict out."""
    snapshot = snapshot or {}
    target = snapshot.get("target")
    if target is None:
        message = "Target cleared."
    elif changed:
        message = "Target stored."
    else:
        message = "Target unchanged."
    return {
        "accepted": True,
        "changed": bool(changed),
        "target": target,
        "age_s": float(snapshot.get("age_s") or 0.0),
        "message": message,
    }


def _target_schema() -> dict:
    return {
        "type": "object",
        "required": ["name", "ra_deg", "dec_deg"],
        "description": "An imaging target. " + ROTATION_CONVENTION,
        "properties": {
            "name": {"type": "string", "maxLength": MAX_NAME_CHARS,
                     "description": "Target name. Control characters are removed, "
                                    "whitespace collapsed, and the result capped at "
                                    f"{MAX_NAME_CHARS} characters."},
            "ra_deg": {"type": "number", "minimum": 0, "maximum": 360,
                       "exclusiveMaximum": True,
                       "description": "Right ascension, J2000, decimal degrees."},
            "dec_deg": {"type": "number", "minimum": -90, "maximum": 90,
                        "description": "Declination, J2000, decimal degrees."},
            "epoch": {"type": "string", "enum": ["J2000"],
                      "description": "Optional. Only J2000 is accepted (case-insensitive)."},
            "fov_w_deg": {"type": "number", "nullable": True, "minimum": 0,
                          "exclusiveMinimum": True, "maximum": MAX_FOV_DEG,
                          "description": "Field of view along the camera's width, "
                                         "degrees. Send with fov_h_deg or not at all."},
            "fov_h_deg": {"type": "number", "nullable": True, "minimum": 0,
                          "exclusiveMinimum": True, "maximum": MAX_FOV_DEG,
                          "description": "Field of view along the camera's height, "
                                         "degrees. Send with fov_w_deg or not at all."},
            "rotation_deg": {"type": "number", "nullable": True,
                             "description": "Position angle of the camera's 'up', "
                                            "degrees east of north; normalised to "
                                            "[0, 360)."},
            "source": {"type": "string", "maxLength": MAX_SOURCE_CHARS, "default": "nina",
                       "description": "Who reported the target."},
        },
    }


def openapi_paths() -> dict:
    """OpenAPI ``paths`` entries for the target route, from TARGET_ROUTES."""
    paths = {}
    for route in TARGET_ROUTES:
        paths.setdefault(route["path"], {})[route["method"]] = {
            "summary": route["summary"],
            "description": route["description"],
            "security": [{"bearerAuth": []}],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["target"],
                    "properties": {
                        "target": {
                            "allOf": [{"$ref": "#/components/schemas/NinaTarget"}],
                            "nullable": True,
                            "description": "The target, or null to clear it.",
                        },
                    },
                }}},
            },
            "responses": {
                "200": {
                    "description": "Target stored, unchanged (heartbeat) or cleared",
                    "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/TargetResult"}}},
                },
                "400": {"description": "Malformed body or a field out of range"},
                "401": {"description": "Missing or invalid bearer token"},
                "403": {"description": "Host header not allowed"},
                "413": {"description": f"Body larger than {MAX_BODY_BYTES} bytes"},
                "503": {"description": "Control API not enabled"},
            },
        }
    return paths


def openapi_schemas() -> dict:
    """OpenAPI component schemas for the target payload and response."""
    result_props = {}
    for name, typ, desc in TARGET_RESULT_FIELDS:
        if name == "target":
            result_props[name] = {"allOf": [{"$ref": "#/components/schemas/NinaTarget"}],
                                  "nullable": True, "description": desc}
        else:
            result_props[name] = {"type": typ, "description": desc}
    return {
        "NinaTarget": _target_schema(),
        "TargetResult": {"type": "object", "properties": result_props},
    }
