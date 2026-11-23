"""
``GET /conditions`` — the imaging-conditions contract and its OpenAPI entry.

Pure: no sockets. The payload itself is built by
``services.imaging_conditions.conditions_store.ConditionsStore.snapshot``;
this module is the catalog the docs are generated from and the golden list
of field names the NINA plugin depends on (fields may be added, never
renamed or removed — tests/test_api_conditions.py pins the set).
"""
from __future__ import annotations

CONDITIONS_PATH = "/conditions"

REASON_CODES = [
    ("roof_not_open", "The roof is not read Open with enough confidence."),
    ("daylight", "The sun is above the configured altitude (-18° astronomical by default)."),
    ("frame_static", "The frame is sensor noise only; nothing in it can be judged."),
    ("sky_unknown", "Neither a star count nor an ML sky reading is available."),
    ("cloud_cover_high", "Estimated cloud cover is above the threshold."),
    ("stars_low", "Star count is below min_star_ratio of this rig's learned clear-sky baseline "
                  "(never fires on a count while the baseline is still learning)."),
    ("stars_dropping", "Star count fell more than star_drop_pct % below the median of the last "
                       "window_frames frames."),
    ("fwhm_high", "Star FWHM is more than fwhm_rise_pct % above tonight's median."),
    ("stale", "No frame has been evaluated for longer than stale_after_s."),
    ("warming_up", "Not enough good frames yet since start-up (or a restart after a gap)."),
    ("recovering", "Frames are good again; waiting for good_frames in a row."),
    ("disabled", "Imaging conditions are switched off in Sentinel's settings."),
]

_NUM = {"type": "number", "nullable": True}
_STR = {"type": "string", "nullable": True}
_BOOL = {"type": "boolean", "nullable": True}
_INT = {"type": "integer", "nullable": True}


def _obj(props: dict, description: str) -> dict:
    return {"type": "object", "description": description, "properties": props}


# (name, schema). Order is the order of the payload.
CONDITIONS_FIELDS = [
    ("enabled", {"type": "boolean", "description": "Imaging conditions switched on in Sentinel."}),
    ("updated_at", dict(_STR, format="date-time",
                        description="UTC time the last frame was evaluated.")),
    ("age_s", dict(_NUM, description="Seconds since the last evaluated frame.")),
    ("stale", {"type": "boolean",
               "description": "True when age_s exceeds stale_after_s (imaging_ok is then false)."}),
    ("stale_after_s", {"type": "number",
                       "description": "Staleness limit in use: configured, or 3x the measured "
                                      "frame interval (at least 120 s)."}),
    ("imaging_ok", {"type": "boolean",
                    "description": "Recommendation, with hysteresis: false after bad_frames bad "
                                   "frames in a row, true after good_frames good ones."}),
    ("reasons", {"type": "array", "items": {"type": "string"},
                 "description": "Machine codes for the latest frame (see the reason table)."}),
    ("changed_at", dict(_STR, format="date-time",
                        description="UTC time imaging_ok last changed.")),
    ("frames_considered", {"type": "integer",
                           "description": "Frames evaluated since start-up or the last restart."}),
    ("frame_ok", dict(_BOOL, description="Whether the latest frame alone passed every rule.")),
    ("bad_streak", {"type": "integer", "description": "Consecutive bad frames."}),
    ("good_streak", {"type": "integer", "description": "Consecutive good frames."}),
    ("roof", _obj({
        "state": {"type": "string", "enum": ["Open", "Closed", "Unknown"]},
        "confidence": dict(_NUM, description="0-1, from the roof classifier."),
    }, "ML roof verdict for the latest frame.")),
    ("sky", _obj({
        "condition": dict(_STR, description="ML sky class (Clear / Partly Cloudy / Overcast)."),
        "confidence": dict(_NUM, description="0-1."),
        "stars_visible": dict(_BOOL, description="ML stars-visible verdict."),
    }, "ML sky verdict for the latest frame.")),
    ("stars", _obj({
        "count": dict(_INT, description="Star index: star-like sources on the sky disc "
                                         "resampled to a fixed 900 px plane, so it compares "
                                         "between frames and with the baseline. Not the "
                                         "full-frame count the FWHM is measured on."),
        "baseline": dict(_NUM, description="This rig's clear-sky count for the Moon's state; "
                                           "null until learned."),
        "ratio": dict(_NUM, description="count / baseline."),
        "trend": dict(_NUM, description="Fractional change vs the median of the recent frames."),
        "change_pct": dict(_NUM, description="Signed % change vs the median of the last "
                                             "window_frames frames (e.g. -45.0)."),
        "window_frames": dict(_INT, description="N, the frames the change is taken against."),
    }, "Measured star counts.")),
    ("cloud_cover_pct", dict(_NUM, description="Estimated cloud cover, 0-100. Never invented: "
                                               "null with neither a baseline nor an ML class.")),
    ("cloud_cover_source", dict(_STR, enum=["star_baseline", "ml_class", None],
                                description="star_baseline = 1 - ratio; ml_class = coarse "
                                            "figure from the ML sky class.")),
    ("fwhm", _obj({
        "px": dict(_NUM, description="Median star FWHM on the full frame, pixels."),
        "arcsec": dict(_NUM, description="Zenith plate scale from the all-sky calibration; "
                                         "null without one."),
        "night_median_px": dict(_NUM, description="Median FWHM of tonight's good frames."),
        "change_pct": dict(_NUM, description="Signed % change vs night_median_px."),
    }, "Star sharpness.")),
    ("sun_altitude_deg", dict(_NUM, description="Sun altitude at evaluation; null without a site.")),
    ("moon", _obj({
        "up": dict(_BOOL, description="Moon above the horizon; null without a site."),
        "illumination_pct": dict(_NUM, description="Illuminated fraction, 0-100."),
    }, "Moon state the baseline bin was chosen from.")),
    ("baseline_bin", dict(_STR, description="Baseline bin in use (moon_down, moon_up_0_25, ...).")),
    ("baseline_status", _obj({
        "state": {"type": "string", "enum": ["learning", "ready"]},
        "samples": {"type": "integer", "description": "Clear frames learned for this bin."},
        "needed": {"type": "integer", "description": "Clear frames the bin needs to be ready."},
        "bin": dict(_STR, description="Moon bin being learned (same values as baseline_bin)."),
    }, "The clear-sky baseline is learned on each installation from its own frames; "
       "until the current Moon bin is ready, stars.baseline and stars.ratio are null "
       "and the star-ratio rule does not fire.")),
    ("events", {"type": "array", "description":
                "Recent changes of imaging_ok, newest first (at most 10, from the last "
                "24 h; kept across a Sentinel restart). Empty while imaging conditions "
                "are off.",
                "items": {"type": "object", "properties": {
                    "at": {"type": "string", "format": "date-time",
                           "description": "UTC time of the change."},
                    "kind": {"type": "string", "enum": ["paused", "resumed"]},
                    "reasons": {"type": "array", "items": {"type": "string"},
                                "description": "Reason codes for a pause (the reason "
                                               "table, plus 'restarted' when Sentinel "
                                               "stopped while imaging was OK); empty "
                                               "for a resume."},
                    "duration_s": dict(_NUM, description="How long the state lasted; "
                                                         "null while it still holds."),
                }}}),
]

CONTRACT_KEYS = [name for name, _ in CONDITIONS_FIELDS]

DESCRIPTION = (
    "Live imaging conditions and a recommendation, for pausing a NINA sequence "
    "when the sky turns bad and resuming when it recovers. Evaluated on every "
    "frame Sentinel processes (camera and Directory Watch modes) from the ML "
    "roof and sky verdicts, the measured star count against this rig's own "
    "clear-sky baseline, the star-count trend and the FWHM. imaging_ok has "
    "hysteresis and starts false; it reads false with reason 'stale' when "
    "frames stop arriving. Same bearer token and Host allow-list as the "
    "control routes."
)


def _type_text(schema: dict) -> str:
    text = schema.get("type", "")
    if text == "array":
        text = f"array of {schema.get('items', {}).get('type', 'value')}"
    return f"{text} or null" if schema.get("nullable") else text


def field_rows() -> list:
    """``(name, type, description)`` per field, nested objects flattened to
    ``stars.count`` (array items as ``events[].kind``) and so on — the /docs
    field table."""
    rows = []
    for name, schema in CONDITIONS_FIELDS:
        rows.append((name, _type_text(schema), schema.get("description", "")))
        props, prefix = schema.get("properties"), name
        if props is None:
            props, prefix = (schema.get("items") or {}).get("properties"), f"{name}[]"
        for sub, sub_schema in (props or {}).items():
            desc = sub_schema.get("description", "")
            if sub_schema.get("enum"):
                values = ", ".join(str(v) for v in sub_schema["enum"])
                desc = f"{desc} One of: {values}.".strip()
            rows.append((f"{prefix}.{sub}", _type_text(sub_schema), desc))
    return rows


def openapi_paths() -> dict:
    return {CONDITIONS_PATH: {"get": {
        "summary": "Imaging conditions and recommendation",
        "description": DESCRIPTION,
        "security": [{"bearerAuth": []}],
        "responses": {
            "200": {"description": "Current conditions",
                    "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/ImagingConditions"}}}},
            "401": {"description": "Missing or invalid bearer token"},
            "403": {"description": "Host header not allowed"},
            "503": {"description": "Control API not enabled (code control_disabled)"},
        },
    }}}


def openapi_schemas() -> dict:
    return {"ImagingConditions": {
        "type": "object",
        "required": CONTRACT_KEYS,
        "properties": dict(CONDITIONS_FIELDS),
    }}
