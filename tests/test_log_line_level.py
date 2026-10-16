"""Tests for services.log_line_level — the Logs tab's level hierarchy."""
import pytest

from services.log_line_level import at_or_above, line_level, normalise_threshold


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARN", "ERROR"])
def test_level_read_from_prefix(level):
    assert line_level(f"[19:45:09] {level}: something") == level


def test_level_word_in_the_message_is_not_the_level():
    assert line_level("[19:45:09] INFO: DEBUG mode enabled") == "INFO"
    assert line_level("[19:45:09] DEBUG: ERROR count 0") == "DEBUG"


def test_line_without_prefix_reads_as_info():
    assert line_level("plain message") == "INFO"


@pytest.mark.parametrize("threshold, shown", [
    ("DEBUG", {"DEBUG", "INFO", "WARN", "ERROR"}),
    ("INFO", {"INFO", "WARN", "ERROR"}),
    ("WARN", {"WARN", "ERROR"}),
    ("ERROR", {"ERROR"}),
])
def test_threshold_shows_its_level_and_everything_more_severe(threshold, shown):
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        assert at_or_above(f"[00:00:00] {level}: x", threshold) is (level in shown)


@pytest.mark.parametrize("saved, expected", [
    ("All", "DEBUG"), ("Info+", "INFO"),
    ("DEBUG", "DEBUG"), ("WARN", "WARN"), ("ERROR", "ERROR"),
    ("nonsense", "INFO"), (None, "INFO"),
])
def test_saved_setting_maps_onto_the_hierarchy(saved, expected):
    assert normalise_threshold(saved) == expected
