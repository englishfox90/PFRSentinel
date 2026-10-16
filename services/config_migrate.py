"""Legacy config-key migration.

Historically, per-camera settings lived as flat top-level keys (`zwo_exposure_ms`,
`zwo_gain`, `zwo_wb_r`, …). The current model is `camera_profiles[clean_name]`,
which avoids cross-contamination when the user swaps cameras.

`migrate_legacy_camera_keys` folds any legacy flat keys into the active camera's
profile and strips them from the dict. Idempotent — safe to call on already-migrated
configs.
"""
from __future__ import annotations

from typing import Any


LEGACY_TO_PROFILE = {
    "zwo_exposure_ms": "exposure_ms",
    "zwo_gain": "gain",
    "zwo_max_exposure_ms": "max_exposure_ms",
    "zwo_target_brightness": "target_brightness",
    "zwo_wb_r": "wb_r",
    "zwo_wb_b": "wb_b",
    "zwo_offset": "offset",
    "zwo_flip": "flip",
    "zwo_bayer_pattern": "bayer_pattern",
}

# Legacy global-ish key with no new home — replaced by `white_balance.mode`.
DEAD_KEYS = ("zwo_auto_wb",)


def migrate_legacy_camera_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Fold legacy `zwo_*` per-camera keys into `camera_profiles[active_camera]`.

    Mutates and returns `data`. No-op when no legacy keys are present.

    Rules:
    - Active camera name is taken from `zwo_selected_camera_name` (falls back to
      `zwo_camera_name`). If neither is set, a synthetic `"__unassigned__"` slot
      is used so the values aren't silently lost.
    - If the profile already has a value for a migrated key, the existing profile
      value wins — the legacy global value is dropped. This matches how the app
      actually used the keys: profile first, flat global as fallback.
    - `zwo_auto_wb` is dropped entirely — it duplicates `white_balance.mode`.
    """
    if not isinstance(data, dict):
        return data

    has_legacy_per_camera = any(k in data for k in LEGACY_TO_PROFILE)
    has_dead_keys = any(k in data for k in DEAD_KEYS)
    if not has_legacy_per_camera and not has_dead_keys:
        return data

    for dead in DEAD_KEYS:
        data.pop(dead, None)

    if not has_legacy_per_camera:
        return data

    active = data.get("zwo_selected_camera_name") or data.get("zwo_camera_name") or "__unassigned__"
    profiles = data.setdefault("camera_profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["camera_profiles"] = profiles

    profile = profiles.setdefault(active, {})
    if not isinstance(profile, dict):
        profile = {}
        profiles[active] = profile

    for legacy_key, profile_key in LEGACY_TO_PROFILE.items():
        if legacy_key not in data:
            continue
        legacy_value = data.pop(legacy_key)
        profile.setdefault(profile_key, legacy_value)

    return data


# ---------------------------------------------------------------------------
# Weather coordinates
# ---------------------------------------------------------------------------

# (config key, is_longitude) — the range check differs.
COORDINATE_FIELDS = (("latitude", False), ("longitude", True))


def normalise_weather_coordinates(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Rewrite ``weather.latitude`` / ``weather.longitude`` as canonical signed
    decimal-degree strings. Mutates ``data``; returns ``(level, message)``
    notices for the log, empty when nothing changed.

    Configs saved before 3.6.6 (and any edited by hand) can hold the text the
    user typed, e.g. ``"31 32 51"``. The Settings panel now canonicalises on
    save, but everything that reads the config with a plain ``float()`` —
    weather, the timelapse sun window, the capture schedule gate — silently
    fell back on such a value, and the lenient readers (moon, night flag) read
    it with whatever sign they guessed. One representation on disk ends that.

    What this cannot do is invent a hemisphere: a DMS value with no letter and
    no minus is read as north / east, exactly as the Settings panel would read
    it, a warning says so, and the original text is kept under
    ``weather.hemisphere_unconfirmed`` so the GUI can ask
    (``services.coordinate_hemisphere``). An unparseable value is left alone (with a
    warning) rather than blanked — that is the panel's job, where the user can
    see it. Messages never carry the coordinate itself (issue #65).
    """
    weather = data.get("weather") if isinstance(data, dict) else None
    if not isinstance(weather, dict):
        return []

    from .coordinate_hemisphere import UNCONFIRMED_KEY
    from .coordinates import is_unsigned_dms, parse_coordinate, to_decimal_string

    notices: list[tuple[str, str]] = []
    for key, is_longitude in COORDINATE_FIELDS:
        raw = weather.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        value = parse_coordinate(raw, is_longitude)
        if value is None:
            notices.append(("warning", (
                f"Config: weather.{key} could not be read as a coordinate and was "
                f"left unchanged - re-enter it under Settings > Weather API")))
            continue
        canonical = to_decimal_string(value)
        if raw == canonical:
            continue
        weather[key] = canonical
        notices.append(("info", f"Config: weather.{key} rewritten as decimal degrees"))
        if is_unsigned_dms(raw):
            # Remember what was typed so the GUI can ask which hemisphere was
            # meant; headless runs only get the warning below.
            pending = weather.get(UNCONFIRMED_KEY)
            if not isinstance(pending, dict):
                pending = weather[UNCONFIRMED_KEY] = {}
            pending[key] = str(raw).strip()
            hemisphere = "EAST" if is_longitude else "NORTH"
            opposite = "west of Greenwich" if is_longitude else "in the southern hemisphere"
            letter = "W" if is_longitude else "S"
            notices.append(("warning", (
                f"Config: weather.{key} was stored in degrees-minutes-seconds with no "
                f"hemisphere letter and has been read as {hemisphere}. If the observatory "
                f"is {opposite}, re-enter it under Settings > Weather API with a "
                f"trailing {letter} or a leading minus")))
    return notices
