"""Tests for services/diagnostics_bundle.py — redaction, log selection, ZIP assembly."""
import json
import os
import time
import zipfile

import pytest

from services.diagnostics_bundle import (
    REDACTED, build_bundle, default_bundle_path, environment_info,
    recent_log_files, redact_config,
)


SAMPLE_CONFIG = {
    "capture_mode": "camera",
    "control_api": {"api_token": "abc123", "enabled": True},
    "weather": {"api_key": "", "location": "Perth"},
    "discord": {"webhook_url": "https://discord.com/api/webhooks/1/x", "enabled": True},
    "hermes_legacy": {"secret": "s3cr3t", "subscriptions": [{"webhook_url": "https://h/1"}]},
    "posthog_distinct_id": "uuid-1234",
    "youtube": {"client_secrets_path": "C:/x/client_secret.json"},
    "camera_profiles": {"ZWO ASI676MC": {"gain": 180, "offset": 0}},
    "zwo_selected_camera_serial": "0123456789abcdef",
    "weather_site": {"latitude": "-31.95", "longitude": "115.86", "elevation": "20"},
    "hermes": {"url": "https://private.example/hook", "event_urls": {"roof": "https://p/r"}},
}


class TestRedactConfig:
    def test_masks_secrets_at_every_level(self):
        out = redact_config(SAMPLE_CONFIG)
        assert out["control_api"]["api_token"] == REDACTED
        assert out["discord"]["webhook_url"] == REDACTED
        assert out["hermes_legacy"]["secret"] == REDACTED
        assert out["hermes_legacy"]["subscriptions"][0]["webhook_url"] == REDACTED
        assert out["posthog_distinct_id"] == REDACTED
        assert out["youtube"]["client_secrets_path"] == REDACTED

    def test_masks_location_urls_and_serial(self):
        out = redact_config(SAMPLE_CONFIG)
        assert out["zwo_selected_camera_serial"] == REDACTED
        assert out["weather_site"]["latitude"] == REDACTED
        assert out["weather_site"]["longitude"] == REDACTED
        assert out["weather_site"]["elevation"] == REDACTED
        assert out["hermes"]["url"] == REDACTED
        assert out["hermes"]["event_urls"] == REDACTED

    def test_keeps_diagnostic_values(self):
        out = redact_config(SAMPLE_CONFIG)
        assert out["camera_profiles"]["ZWO ASI676MC"] == {"gain": 180, "offset": 0}
        assert out["weather"]["location"] == REDACTED
        assert out["control_api"]["enabled"] is True

    def test_empty_secret_stays_empty(self):
        # "not configured" must remain distinguishable from "hidden"
        assert redact_config(SAMPLE_CONFIG)["weather"]["api_key"] == ""

    def test_does_not_mutate_input(self):
        original = json.dumps(SAMPLE_CONFIG, sort_keys=True)
        redact_config(SAMPLE_CONFIG)
        assert json.dumps(SAMPLE_CONFIG, sort_keys=True) == original


class TestRecentLogFiles:
    def test_filters_by_mtime_and_sorts_oldest_first(self, tmp_path):
        now = time.time()
        old = tmp_path / "sentinel.log.2026-08-01"
        mid = tmp_path / "sentinel.log.2026-09-12"
        new = tmp_path / "sentinel.log"
        for p in (old, mid, new):
            p.write_text("x")
        os.utime(old, (now - 10 * 86400, now - 10 * 86400))
        os.utime(mid, (now - 2 * 86400, now - 2 * 86400))
        os.utime(new, (now, now))
        (tmp_path / "subdir").mkdir()

        files = recent_log_files(tmp_path, days=3, now=now)
        assert [p.name for p in files] == ["sentinel.log.2026-09-12", "sentinel.log"]

    def test_missing_dir_is_empty(self, tmp_path):
        assert recent_log_files(tmp_path / "nope") == []


class TestBuildBundle:
    def test_bundle_contents(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "sentinel.log").write_text("[2026-09-14] INFO - hello\n")
        frame = tmp_path / "raw_20260914_010203_bayer.fits"
        frame.write_bytes(b"SIMPLE")
        dest = tmp_path / "out" / "bundle.zip"

        result = build_bundle(
            dest,
            config_data=SAMPLE_CONFIG,
            log_dir=log_dir,
            summary={"app_version": "3.7.6", "notes": ["n1"]},
            extra_files={
                "frames/raw_bayer.fits": str(frame),
                "allsky/allsky_calibration.json": str(tmp_path / "missing.json"),
            },
        )

        assert result == dest and dest.is_file()
        with zipfile.ZipFile(dest) as zf:
            names = set(zf.namelist())
            assert {"logs/sentinel.log", "config.redacted.json",
                    "frames/raw_bayer.fits", "summary.json"} <= names
            assert "allsky/allsky_calibration.json" not in names

            config = json.loads(zf.read("config.redacted.json"))
            assert config["control_api"]["api_token"] == REDACTED
            assert "abc123" not in zf.read("config.redacted.json").decode()

            summary = json.loads(zf.read("summary.json"))
            assert summary["app_version"] == "3.7.6"
            assert summary["notes"] == ["n1"]
            assert "frames/raw_bayer.fits" in summary["contents"]
            assert summary["missing"] == ["allsky/allsky_calibration.json"]

    def test_default_bundle_path_and_environment(self, tmp_path):
        path = default_bundle_path(tmp_path)
        assert path.parent == tmp_path
        assert path.name.startswith("PFRSentinel_diagnostics_") and path.suffix == ".zip"
        env = environment_info("9.9.9")
        assert env["app_version"] == "9.9.9"
        assert env["python"] and env["platform"] and env["created_at"]
