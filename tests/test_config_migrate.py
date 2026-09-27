"""Tests for services.config_migrate — legacy zwo_* → camera_profiles migration."""
import json

import pytest

from services.config import Config
from services.config_migrate import (
    DEAD_KEYS,
    LEGACY_TO_PROFILE,
    migrate_legacy_camera_keys,
)


class TestMigrateLegacyCameraKeys:
    def test_empty_config_is_noop(self):
        data = {}
        assert migrate_legacy_camera_keys(data) == {}

    def test_config_without_legacy_keys_is_noop(self):
        data = {"capture_mode": "camera", "camera_profiles": {"X": {"exposure_ms": 50}}}
        before = json.dumps(data, sort_keys=True)
        after = migrate_legacy_camera_keys(data)
        assert json.dumps(after, sort_keys=True) == before

    def test_legacy_keys_fold_into_active_profile(self):
        data = {
            "zwo_selected_camera_name": "ZWO ASI676MC",
            "zwo_exposure_ms": 250.0,
            "zwo_gain": 150,
            "zwo_wb_r": 80,
            "zwo_wb_b": 90,
            "zwo_offset": 22,
            "zwo_flip": 1,
            "zwo_bayer_pattern": "RGGB",
            "zwo_max_exposure_ms": 20000.0,
            "zwo_target_brightness": 110,
        }
        result = migrate_legacy_camera_keys(data)

        assert "camera_profiles" in result
        profile = result["camera_profiles"]["ZWO ASI676MC"]
        assert profile == {
            "exposure_ms": 250.0,
            "gain": 150,
            "wb_r": 80,
            "wb_b": 90,
            "offset": 22,
            "flip": 1,
            "bayer_pattern": "RGGB",
            "max_exposure_ms": 20000.0,
            "target_brightness": 110,
        }
        # All legacy per-camera keys stripped
        for legacy_key in LEGACY_TO_PROFILE:
            assert legacy_key not in result

    def test_falls_back_to_zwo_camera_name(self):
        data = {"zwo_camera_name": "Old Camera", "zwo_exposure_ms": 333.0}
        result = migrate_legacy_camera_keys(data)
        assert result["camera_profiles"]["Old Camera"]["exposure_ms"] == 333.0

    def test_unassigned_slot_when_no_camera_name(self):
        """Legacy values aren't silently lost when no camera is selected."""
        data = {"zwo_exposure_ms": 999.0}
        result = migrate_legacy_camera_keys(data)
        assert "__unassigned__" in result["camera_profiles"]
        assert result["camera_profiles"]["__unassigned__"]["exposure_ms"] == 999.0

    def test_existing_profile_value_wins(self):
        """When profile already has a value, the legacy global is dropped (profile wins)."""
        data = {
            "zwo_selected_camera_name": "Cam",
            "zwo_exposure_ms": 100.0,  # legacy
            "camera_profiles": {"Cam": {"exposure_ms": 500.0}},  # profile already set
        }
        result = migrate_legacy_camera_keys(data)
        assert result["camera_profiles"]["Cam"]["exposure_ms"] == 500.0
        assert "zwo_exposure_ms" not in result

    def test_is_idempotent(self):
        data = {
            "zwo_selected_camera_name": "Cam",
            "zwo_gain": 200,
        }
        once = migrate_legacy_camera_keys(dict(data))
        twice = migrate_legacy_camera_keys(dict(once))
        assert once == twice

    def test_dead_keys_are_stripped(self):
        data = {"zwo_auto_wb": True, "capture_mode": "camera"}
        result = migrate_legacy_camera_keys(data)
        for dead in DEAD_KEYS:
            assert dead not in result
        # Non-dead keys preserved
        assert result["capture_mode"] == "camera"

    def test_preserves_global_zwo_keys(self):
        """zwo_interval, zwo_auto_exposure, zwo_sdk_path are globals — not migrated."""
        data = {
            "zwo_selected_camera_name": "Cam",
            "zwo_interval": 10.0,
            "zwo_auto_exposure": True,
            "zwo_sdk_path": "C:/foo/ASICamera2.dll",
            "zwo_exposure_ms": 200.0,  # this one DOES migrate
        }
        result = migrate_legacy_camera_keys(data)
        assert result["zwo_interval"] == 10.0
        assert result["zwo_auto_exposure"] is True
        assert result["zwo_sdk_path"] == "C:/foo/ASICamera2.dll"
        assert "zwo_exposure_ms" not in result


class TestMigrationAtLoadTime:
    """End-to-end: writing a legacy config to disk, then loading via Config, should migrate."""

    def test_legacy_config_migrates_on_load(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "zwo_selected_camera_name": "ZWO ASI676MC",
            "zwo_exposure_ms": 250.0,
            "zwo_gain": 180,
            "zwo_bayer_pattern": "BGGR",
            "zwo_auto_wb": False,
        }))

        cfg = Config(str(cfg_path))

        # Legacy keys gone
        assert cfg.get("zwo_exposure_ms") is None
        assert cfg.get("zwo_auto_wb") is None
        # Profile populated
        profile = cfg.data["camera_profiles"]["ZWO ASI676MC"]
        assert profile["exposure_ms"] == 250.0
        assert profile["gain"] == 180
        assert profile["bayer_pattern"] == "BGGR"


# ---------------------------------------------------------------------------
# Weather coordinate normalisation
# ---------------------------------------------------------------------------

from services.config_migrate import normalise_weather_coordinates  # noqa: E402


def _weather(**fields):
    return {"weather": {"api_key": "", **fields}}


class TestNormaliseWeatherCoordinates:
    def test_dms_with_hemisphere_becomes_signed_decimal(self):
        data = _weather(latitude="31 19 49 N", longitude="100 27 25 W")
        notices = normalise_weather_coordinates(data)
        assert data["weather"]["latitude"] == "31.3302778"
        assert data["weather"]["longitude"] == "-100.4569444"
        assert [level for level, _ in notices] == ["info", "info"]

    def test_leading_minus_carries_the_sign(self):
        data = _weather(longitude="-100 27 25")
        normalise_weather_coordinates(data)
        assert data["weather"]["longitude"].startswith("-100.45")

    def test_canonical_decimal_is_untouched_and_silent(self):
        data = _weather(latitude="31.3303162", longitude="-100.4570705")
        before = json.dumps(data, sort_keys=True)
        assert normalise_weather_coordinates(data) == []
        assert json.dumps(data, sort_keys=True) == before

    def test_unsigned_dms_reads_east_and_north_with_a_warning(self):
        data = _weather(latitude="31 19 49", longitude="100 27 25")
        notices = normalise_weather_coordinates(data)
        assert data["weather"]["longitude"] == "100.4569444"
        assert data["weather"]["latitude"] == "31.3302778"
        warnings = [m for level, m in notices if level == "warning"]
        assert len(warnings) == 2
        assert "EAST" in warnings[1] and "trailing W" in warnings[1]
        assert "NORTH" in warnings[0] and "trailing S" in warnings[0]

    def test_unsigned_decimal_gets_no_hemisphere_warning(self):
        # A bare positive decimal is a deliberate east/north value, not a
        # sign the parser had to guess.
        data = _weather(longitude="100.457")
        notices = normalise_weather_coordinates(data)
        assert all(level == "info" for level, _ in notices)

    def test_notices_never_carry_the_coordinate(self):
        data = _weather(latitude="31 19 49", longitude="100 27 25 W")
        for _, message in normalise_weather_coordinates(data):
            assert "31" not in message and "100" not in message

    def test_blank_and_missing_are_noops(self):
        assert normalise_weather_coordinates(_weather(latitude="", longitude="  ")) == []
        assert normalise_weather_coordinates({}) == []
        assert normalise_weather_coordinates({"weather": "not a dict"}) == []
        assert normalise_weather_coordinates(_weather()) == []

    def test_garbage_is_left_alone_with_a_warning(self):
        data = _weather(latitude="somewhere", longitude="-100.46")
        notices = normalise_weather_coordinates(data)
        assert data["weather"]["latitude"] == "somewhere"
        assert data["weather"]["longitude"] == "-100.46"
        assert notices == [("warning", notices[0][1])]
        assert "left unchanged" in notices[0][1]

    def test_out_of_range_is_left_alone(self):
        data = _weather(latitude="100 0 0")
        normalise_weather_coordinates(data)
        assert data["weather"]["latitude"] == "100 0 0"

    def test_numeric_json_value_becomes_canonical_string(self):
        data = _weather(latitude=31.5, longitude=-100.25)
        normalise_weather_coordinates(data)
        assert data["weather"] == {"api_key": "", "latitude": "31.5", "longitude": "-100.25"}

    def test_is_idempotent(self):
        data = _weather(latitude="31 19 49 N", longitude="100 27 25 W")
        normalise_weather_coordinates(data)
        once = json.dumps(data, sort_keys=True)
        assert normalise_weather_coordinates(data) == []
        assert json.dumps(data, sort_keys=True) == once


class TestCoordinateNormalisationAtLoadTime:
    def test_dms_config_is_canonical_in_memory_and_on_disk(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "weather": {"latitude": "31 19 49 N", "longitude": "100 27 25 W", "api_key": "k"},
        }))

        cfg = Config(str(cfg_path))

        assert cfg.get("weather")["longitude"] == "-100.4569444"
        assert cfg.get("weather")["latitude"] == "31.3302778"
        on_disk = json.loads(cfg_path.read_text())["weather"]
        assert on_disk["longitude"] == "-100.4569444"
        assert on_disk["api_key"] == "k"

    def test_canonical_config_is_not_rewritten_on_load(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"weather": {"latitude": "31.33", "longitude": "-100.46"}}))
        stamp = cfg_path.stat().st_mtime_ns

        Config(str(cfg_path))

        assert cfg_path.stat().st_mtime_ns == stamp

    def test_unparseable_coordinate_survives_load_unchanged(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"weather": {"latitude": "somewhere", "longitude": "-100.46"}}))

        cfg = Config(str(cfg_path))

        assert cfg.get("weather")["latitude"] == "somewhere"
        assert json.loads(cfg_path.read_text())["weather"]["latitude"] == "somewhere"
