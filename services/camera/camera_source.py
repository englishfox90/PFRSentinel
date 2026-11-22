"""Which camera backend is active, and who that camera is (issue #43).

``capture_mode`` stays ``"camera" | "watch"``; under ``"camera"`` the
``camera_source`` key picks the backend: ``"zwo"`` (USB, the ZWO SDK) or
``"alpaca"`` (ASCOM Alpaca over the network). Every reader that used to ask
for the ZWO name/serial asks ``active_camera_identity`` instead, so the
profile key follows the backend:

- ZWO: ``(selected model name, hardware serial)`` — exactly the previous
  ``get_selected_camera_name`` + ``zwo_selected_camera_serial``.
- Alpaca: ``(device Name, "alpaca:<UniqueID>")``, or
  ``"alpaca:<host>:<port>:<n>"`` before the UniqueID is learned. The prefix
  keeps a bare name such as "Camera 1" out of the key, so
  ``prune_bogus_profiles`` can never delete an Alpaca profile. A same-named
  legacy profile ("ZWO ASI676MC" seen through ZWO's ASCOM driver) is copied
  once as a template — the profile store's normal name fallback.

Profiles are reached only through ``Config``'s delegators.
"""
from services.config_defaults import DEFAULT_CAMERA_PROFILE, DEFAULT_CONFIG

from .camera_utils import get_selected_camera_name

ZWO = "zwo"
ALPACA = "alpaca"
CAMERA_SOURCES = (ZWO, ALPACA)
ALPACA_PROFILE_PREFIX = "alpaca:"

_SOURCE_LABELS = {ZWO: "ZWO Camera Capture", ALPACA: "Alpaca Camera Capture"}
_WATCH_LABEL = "Directory Watch"

_ALPACA_DEFAULTS = DEFAULT_CONFIG["alpaca_camera"]


def camera_source(config) -> str:
    """The configured backend; anything unknown reads as ``'zwo'``."""
    value = str(config.get("camera_source", ZWO) or ZWO).strip().lower()
    return value if value in CAMERA_SOURCES else ZWO


def is_alpaca(config) -> bool:
    """True when camera capture would run on the Alpaca backend."""
    return camera_source(config) == ALPACA


def _int(value, default, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if lo <= value <= hi else default


def _float(value, default, lo, hi):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(value, lo), hi)


def _bool(value, default):
    return value if isinstance(value, bool) else default


def alpaca_settings(config) -> dict:
    """``alpaca_camera`` with every field typed and bounded (a hand-edited
    config must not reach the client as a string port or a zero timeout)."""
    raw = config.get("alpaca_camera") or {}
    if not isinstance(raw, dict):
        raw = {}
    d = _ALPACA_DEFAULTS
    return {
        "host": str(raw.get("host") or "").strip(),
        "port": _int(raw.get("port"), d["port"], 1, 65535),
        "device_number": _int(raw.get("device_number"), d["device_number"], 0, 2 ** 31 - 1),
        "unique_id": str(raw.get("unique_id") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "use_imagebytes": _bool(raw.get("use_imagebytes"), d["use_imagebytes"]),
        "connect_timeout_s": _float(raw.get("connect_timeout_s"), d["connect_timeout_s"],
                                    0.5, 60.0),
        "request_timeout_s": _float(raw.get("request_timeout_s"), d["request_timeout_s"],
                                    1.0, 300.0),
        "release_on_disconnect": _bool(raw.get("release_on_disconnect"),
                                       d["release_on_disconnect"]),
    }


def alpaca_profile_key(alpaca_cfg: dict) -> str:
    """``"alpaca:<UniqueID>"``, else ``"alpaca:<host>:<port>:<n>"``; ``""`` with no host."""
    cfg = alpaca_cfg or {}
    unique_id = str(cfg.get("unique_id") or "").strip()
    if unique_id:
        return f"{ALPACA_PROFILE_PREFIX}{unique_id}"
    host = str(cfg.get("host") or "").strip()
    if not host:
        return ""
    port = cfg.get("port", _ALPACA_DEFAULTS["port"])
    number = cfg.get("device_number", 0)
    return f"{ALPACA_PROFILE_PREFIX}{host}:{port}:{number}"


def active_camera_identity(config) -> tuple:
    """``(name, profile_serial)`` of the camera the active backend points at."""
    if is_alpaca(config):
        settings = alpaca_settings(config)
        return settings["name"], alpaca_profile_key(settings)
    return (get_selected_camera_name(config),
            config.get("zwo_selected_camera_serial", "") or "")


def active_camera_profile(config) -> dict:
    """The active camera's profile dict, or a copy of the defaults with none."""
    name, serial = active_camera_identity(config)
    if not name and not serial:
        return dict(DEFAULT_CAMERA_PROFILE)
    return config.get_camera_profile(name, serial) or dict(DEFAULT_CAMERA_PROFILE)


def capture_source_label(config, short: bool = False) -> str:
    """Status text for the capture mode; ZWO keeps "ZWO Camera Capture".

    ``short`` drops the trailing "Capture" ("ZWO Camera"), the form the
    periodic Discord update has always used.
    """
    if config.get("capture_mode", "camera") == "watch":
        return _WATCH_LABEL
    label = _SOURCE_LABELS[camera_source(config)]
    return label[:-len(" Capture")] if short else label


def camera_ready(config, controller=None) -> bool:
    """Whether the primary action should offer Start rather than Connect Camera.

    Watch mode is always ready (start is gated on a valid directory elsewhere);
    camera mode is ready when a camera is connected, or — ZWO — at least one
    was detected, or — Alpaca — a server host is configured.
    """
    if config.get("capture_mode", "camera") == "watch":
        return True
    if controller is not None and getattr(controller, "is_connected", False):
        return True
    if is_alpaca(config):
        return bool(alpaca_settings(config)["host"])
    return bool(config.get("available_cameras"))


def persist_alpaca_identity(config, unique_id, name) -> bool:
    """Write a freshly learned UniqueID / Name to ``alpaca_camera``; True if changed.

    The first time the UniqueID is learned, a profile tuned under the
    host:port:n key moves to the UniqueID key (copied, not deleted), so the
    settings made before the first connect follow the device. Call on the
    main thread, like ``persist_camera_serial``.
    """
    current = config.get("alpaca_camera") or {}
    if not isinstance(current, dict):
        current = {}
    unique_id = str(unique_id or "").strip()
    name = str(name or "").strip()
    updated = dict(current)
    if unique_id:
        updated["unique_id"] = unique_id
    if name:
        updated["name"] = name
    if updated == current:
        return False

    old_key = alpaca_profile_key(current)
    new_key = alpaca_profile_key(updated)
    if old_key and new_key and old_key != new_key \
            and old_key in config.list_camera_profiles() \
            and new_key not in config.list_camera_profiles():
        profile = config.get_camera_profile(current.get("name") or name, old_key)
        if profile is not None:
            config.save_camera_profile(name or profile.get("name", ""), dict(profile),
                                       serial=new_key)
    config.set("alpaca_camera", updated)
    config.save()
    return True
