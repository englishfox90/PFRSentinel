"""
YouTube upload configuration helpers.

Kept separate from the API client so validation and template rendering stay
testable without Google dependencies installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from string import Formatter
from typing import Any


UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
VALID_PRIVACY_STATUSES = {"private", "unlisted", "public"}

# The night line is last so that, when the library has nothing for the night,
# the empty placeholder only leaves trailing whitespace, which the upload strips.
DEFAULT_DESCRIPTION_TEMPLATE = (
    "All-sky timelapse recorded by PFR Sentinel on {date}.\n"
    "{frame_count} frames, {duration} of recording.\n"
    "\n"
    "{night_summary}"
)

BASE_PLACEHOLDERS = ("date", "filename", "frame_count", "duration", "size_mb")

# Filled from the image library by services.youtube_night_stats; every one
# renders as an empty string when the night has no library data.
NIGHT_PLACEHOLDERS = (
    "night", "start_time", "end_time", "min_temp", "max_temp", "clear_pct",
    "max_stars", "best_seeing", "roof", "gaps", "weather_summary", "night_summary",
)

SUPPORTED_PLACEHOLDERS = frozenset(BASE_PLACEHOLDERS + NIGHT_PLACEHOLDERS)

# Shown to the user beside the title and description fields.
PLACEHOLDER_DESCRIPTIONS = (
    ("date", "Date the upload was queued (YYYY-MM-DD)"),
    ("filename", "Video file name"),
    ("frame_count", "Frames in the timelapse"),
    ("duration", "Length of the recording session (HH:MM:SS)"),
    ("size_mb", "File size in MB"),
    ("night", "Night the video covers (YYYY-MM-DD, the evening's date)"),
    ("start_time", "First library frame in the video's span (HH:MM)"),
    ("end_time", "Last library frame in the video's span (HH:MM)"),
    ("min_temp", "Lowest camera sensor temperature, in your weather units"),
    ("max_temp", "Highest camera sensor temperature, in your weather units"),
    ("clear_pct", "Percent of frames with a clear sky (number only)"),
    ("max_stars", "Most stars detected in one frame"),
    ("best_seeing", "Best seeing, e.g. Good (FWHM 2.3 px)"),
    ("roof", "Roof state: Open, Closed, or Open 80% of the night"),
    ("gaps", "Capture gaps over an hour: none, or 2 (longest 1h 12m)"),
    ("weather_summary", "Cloud cover from the weather service across the night"),
    ("night_summary", "One line combining the night statistics"),
)

DEFAULT_YOUTUBE_CONFIG = {
    "enabled": False,
    "client_secrets_path": "",
    "privacy_status": "private",
    "title_template": "PFR Sentinel Timelapse {date}",
    "description_template": DEFAULT_DESCRIPTION_TEMPLATE,
    "tags": "astronomy, allsky, timelapse",
    "category_id": "22",
}


@dataclass(frozen=True)
class TimelapseUploadMetadata:
    """Immutable metadata captured when an upload is queued."""

    path: str
    frame_count: int
    elapsed_seconds: int
    file_size_bytes: int
    queued_at: datetime

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def size_mb(self) -> str:
        return f"{self.file_size_bytes / (1024 * 1024):.1f}"

    @property
    def duration(self) -> str:
        h, rem = divmod(max(0, int(self.elapsed_seconds)), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def date(self) -> str:
        return self.queued_at.strftime("%Y-%m-%d")


def normalize_youtube_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return a flat YouTube config dict with safe defaults."""
    normalized = dict(DEFAULT_YOUTUBE_CONFIG)
    if isinstance(config, dict):
        for key in DEFAULT_YOUTUBE_CONFIG:
            if key in config:
                normalized[key] = config[key]

    normalized["enabled"] = bool(normalized.get("enabled", False))
    normalized["client_secrets_path"] = str(normalized.get("client_secrets_path") or "")
    normalized["privacy_status"] = str(normalized.get("privacy_status") or "private").lower()
    normalized["title_template"] = str(normalized.get("title_template") or DEFAULT_YOUTUBE_CONFIG["title_template"])
    normalized["description_template"] = str(
        normalized.get("description_template") or DEFAULT_YOUTUBE_CONFIG["description_template"]
    )
    normalized["category_id"] = str(normalized.get("category_id") or "22")
    normalized["tags"] = _normalize_tags(normalized.get("tags"))
    return normalized


def _normalize_tags(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "")


def parse_tags(value: Any) -> list[str]:
    """Parse tags from config into YouTube API list format."""
    if isinstance(value, list):
        raw_tags = value
    else:
        raw_tags = str(value or "").split(",")
    tags = []
    seen = set()
    for raw in raw_tags:
        tag = str(raw).strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def validate_youtube_config(
    config: dict[str, Any],
    *,
    require_client_file: bool = True,
) -> list[str]:
    """Return user-readable validation errors."""
    cfg = normalize_youtube_config(config)
    errors = []
    if cfg["privacy_status"] not in VALID_PRIVACY_STATUSES:
        errors.append("Privacy must be Private, Unlisted, or Public.")
    if not cfg["title_template"].strip():
        errors.append("Video title is required.")
    if require_client_file:
        path = cfg.get("client_secrets_path", "")
        if not path:
            errors.append("OAuth client JSON is required.")
        elif not os.path.isfile(path):
            errors.append("OAuth client JSON was not found.")
    if not str(cfg.get("category_id", "")).strip():
        errors.append("YouTube category is required.")
    return errors


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def build_template_context(
    metadata: TimelapseUploadMetadata,
    night_stats: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Placeholder values for one upload.

    ``night_stats`` comes from ``services.youtube_night_stats``; a night
    placeholder it does not carry renders as an empty string, never as the
    literal ``{name}``, so a missing library never leaks braces into YouTube.
    """
    context: dict[str, Any] = {
        "date": metadata.date,
        "filename": metadata.filename,
        "frame_count": metadata.frame_count,
        "duration": metadata.duration,
        "size_mb": metadata.size_mb,
    }
    stats = night_stats or {}
    for name in NIGHT_PLACEHOLDERS:
        context[name] = str(stats.get(name) or "")
    return context


def render_template(
    template: str,
    metadata: TimelapseUploadMetadata,
    night_stats: dict[str, str] | None = None,
) -> str:
    """Render supported placeholders, leaving unknown placeholders intact."""
    context = _SafeFormatDict(build_template_context(metadata, night_stats))
    try:
        return str(template).format_map(context)
    except Exception:
        return str(template)


def unknown_template_fields(template: str) -> set[str]:
    """Return placeholder names not supported by the template context."""
    supported = SUPPORTED_PLACEHOLDERS
    unknown = set()
    try:
        for _, field_name, _, _ in Formatter().parse(str(template)):
            if field_name and field_name.split(".", 1)[0] not in supported:
                unknown.add(field_name)
    except Exception:
        return set()
    return unknown
