"""Severity of a formatted log line and the threshold filter the Logs tab uses.

Lines come from `services.logger` as `[HH:MM:SS] LEVEL: message`. The level is
read from that prefix only: matching the word anywhere put "INFO: DEBUG mode
enabled" in the DEBUG bucket.
"""
import re

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
DEFAULT_LEVEL = "INFO"

_RANK = {name: i for i, name in enumerate(LEVELS)}
_PREFIX = re.compile(r"^\[[^\]]*\]\s+(DEBUG|INFO|WARN|ERROR):")

# Values saved by versions whose dropdown matched one level exactly.
_LEGACY = {"All": "DEBUG", "Info+": "INFO"}


def line_level(message: str) -> str:
    """The line's level; a line without the logger's prefix reads as INFO."""
    match = _PREFIX.match(message)
    return match.group(1) if match else DEFAULT_LEVEL


def normalise_threshold(value) -> str:
    """Map a saved `ui_log_level` (old or new) onto one of LEVELS."""
    value = _LEGACY.get(value, value)
    return value if value in _RANK else DEFAULT_LEVEL


def at_or_above(message: str, threshold: str) -> bool:
    return _RANK[line_level(message)] >= _RANK[normalise_threshold(threshold)]
