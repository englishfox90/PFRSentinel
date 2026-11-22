"""camera_source — backend choice, Alpaca settings, profile key, identity, labels, readiness."""
import json

import pytest

from services.camera import camera_source as cs
from services.config import Config
from services.config_defaults import DEFAULT_CAMERA_PROFILE, DEFAULT_CONFIG


@pytest.fixture
def config(tmp_path):
    return Config(str(tmp_path / "config.json"))


def _alpaca(config, **fields):
    cfg = dict(config.get("alpaca_camera"))
    cfg.update(fields)
    config.set("alpaca_camera", cfg)
    config.set("camera_source", "alpaca")
    config.set("capture_mode", "camera")


def test_defaults_land_on_new_and_old_configs(tmp_path):
    assert DEFAULT_CONFIG["camera_source"] == "zwo"
    assert DEFAULT_CONFIG["alpaca_camera"]["port"] == 11111
    assert DEFAULT_CONFIG["alpaca_camera"]["release_on_disconnect"] is False
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"capture_mode": "camera", "alpaca_camera": {"host": "10.0.0.5"}}))
    old = Config(str(path))
    assert cs.camera_source(old) == "zwo"
    assert old.get("alpaca_camera")["host"] == "10.0.0.5"
    assert old.get("alpaca_camera")["use_imagebytes"] is True   # merged one level deep


@pytest.mark.parametrize("value,expected", [
    ("zwo", "zwo"), ("alpaca", "alpaca"), (" ALPACA ", "alpaca"), ("indi", "zwo"),
    (None, "zwo"), (3, "zwo"), ("", "zwo"),
])
def test_camera_source_validated(config, value, expected):
    config.set("camera_source", value)
    assert cs.camera_source(config) == expected


def test_alpaca_settings_typed_and_bounded(config):
    _alpaca(config, host="  allsky.local ", port="80", device_number=-3,
            connect_timeout_s=0, request_timeout_s="abc", use_imagebytes="yes",
            release_on_disconnect=True)
    s = cs.alpaca_settings(config)
    assert s["host"] == "allsky.local" and s["port"] == 80
    assert s["device_number"] == 0
    assert s["connect_timeout_s"] == 0.5 and s["request_timeout_s"] == 10.0
    assert s["use_imagebytes"] is True and s["release_on_disconnect"] is True
    config.set("alpaca_camera", "garbage")
    assert cs.alpaca_settings(config)["port"] == 11111
    config.set("alpaca_camera", {"port": 70000})
    assert cs.alpaca_settings(config)["port"] == 11111


def test_profile_key_prefers_unique_id():
    assert cs.alpaca_profile_key({"unique_id": " abc ", "host": "h"}) == "alpaca:abc"
    assert cs.alpaca_profile_key({"host": "10.0.0.5", "port": 11112, "device_number": 1}) \
        == "alpaca:10.0.0.5:11112:1"
    assert cs.alpaca_profile_key({"host": ""}) == ""
    assert cs.alpaca_profile_key(None) == ""


def test_zwo_identity_is_exactly_the_old_lookup(config):
    config.set("zwo_selected_camera_name", "ZWO ASI676MC")
    config.set("zwo_selected_camera_serial", "1a2b3c")
    assert cs.active_camera_identity(config) == ("ZWO ASI676MC", "1a2b3c")
    config.set("zwo_selected_camera_name", "")
    config.set("zwo_camera_name", "ZWO ASI120MM")      # legacy fallback
    config.set("zwo_selected_camera_serial", None)
    assert cs.active_camera_identity(config) == ("ZWO ASI120MM", "")


def test_alpaca_identity_uses_prefixed_key(config):
    _alpaca(config, host="10.0.0.5", name="Camera 1")
    assert cs.active_camera_identity(config) == ("Camera 1", "alpaca:10.0.0.5:11111:0")
    _alpaca(config, unique_id="uid-9")
    assert cs.active_camera_identity(config) == ("Camera 1", "alpaca:uid-9")


def test_alpaca_profile_survives_bogus_profile_pruning(config, tmp_path):
    # Config.load prunes "Camera <n>" keys; a device named "Camera 1" must keep its profile.
    _alpaca(config, host="10.0.0.5", unique_id="uid-9", name="Camera 1")
    profile = cs.active_camera_profile(config)
    profile["gain"] = 321
    config.save_camera_profile("Camera 1", profile, serial="alpaca:uid-9")
    config.save()
    reloaded = Config(str(tmp_path / "config.json"))
    assert "Camera 1" not in reloaded.list_camera_profiles()
    assert reloaded.get_camera_profile("Camera 1", "alpaca:uid-9")["gain"] == 321


def test_active_camera_profile_defaults_without_a_camera(config):
    assert cs.active_camera_profile(config) == dict(DEFAULT_CAMERA_PROFILE)
    _alpaca(config, host="")
    assert cs.active_camera_profile(config) == dict(DEFAULT_CAMERA_PROFILE)
    assert config.list_camera_profiles() == []


def test_alpaca_profile_seeded_from_same_named_legacy_profile(config):
    config.save_camera_profile("ZWO ASI676MC", {**DEFAULT_CAMERA_PROFILE, "gain": 222})
    _alpaca(config, host="10.0.0.5", unique_id="uid-1", name="ZWO ASI676MC")
    assert cs.active_camera_profile(config)["gain"] == 222
    assert "alpaca:uid-1" in config.list_camera_profiles()


def test_capture_source_label(config):
    config.set("capture_mode", "camera")
    assert cs.capture_source_label(config) == "ZWO Camera Capture"
    config.set("camera_source", "alpaca")
    assert cs.capture_source_label(config) == "Alpaca Camera Capture"
    config.set("capture_mode", "watch")
    assert cs.capture_source_label(config) == "Directory Watch"


class _Controller:
    def __init__(self, connected):
        self.is_connected = connected


def test_camera_ready(config):
    config.set("capture_mode", "watch")
    assert cs.camera_ready(config, None) is True
    config.set("capture_mode", "camera")
    config.set("available_cameras", [])
    assert cs.camera_ready(config, None) is False
    assert cs.camera_ready(config, _Controller(True)) is True
    config.set("available_cameras", ["ZWO ASI676MC (Index: 0)"])
    assert cs.camera_ready(config, _Controller(False)) is True
    _alpaca(config, host="")
    assert cs.camera_ready(config, None) is False
    _alpaca(config, host="10.0.0.5")
    assert cs.camera_ready(config, None) is True


def test_persist_alpaca_identity_writes_once_and_saves(config, tmp_path):
    _alpaca(config, host="10.0.0.5")
    assert cs.persist_alpaca_identity(config, "uid-7", "AllSky") is True
    assert config.get("alpaca_camera")["unique_id"] == "uid-7"
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["alpaca_camera"]["name"] == "AllSky"
    assert cs.persist_alpaca_identity(config, "uid-7", "AllSky") is False
    assert cs.persist_alpaca_identity(config, "", "") is False
    assert config.get("zwo_selected_camera_serial", "") == ""   # never touches zwo_* keys


def test_persist_moves_a_host_keyed_profile_to_the_unique_id(config):
    _alpaca(config, host="10.0.0.5", name="AllSky")
    profile = cs.active_camera_profile(config)
    profile["gain"] = 150
    config.save_camera_profile("AllSky", profile, serial="alpaca:10.0.0.5:11111:0")
    cs.persist_alpaca_identity(config, "uid-7", "AllSky")
    assert cs.active_camera_identity(config)[1] == "alpaca:uid-7"
    assert cs.active_camera_profile(config)["gain"] == 150
    assert "alpaca:10.0.0.5:11111:0" in config.list_camera_profiles()   # copied, not moved


def test_persist_never_overwrites_an_existing_unique_id_profile(config):
    config.save_camera_profile("AllSky", {**DEFAULT_CAMERA_PROFILE, "gain": 400},
                               serial="alpaca:uid-7")
    _alpaca(config, host="10.0.0.5", name="AllSky")
    config.save_camera_profile("AllSky", {**DEFAULT_CAMERA_PROFILE, "gain": 10},
                               serial="alpaca:10.0.0.5:11111:0")
    cs.persist_alpaca_identity(config, "uid-7", "AllSky")
    assert config.get_camera_profile("AllSky", "alpaca:uid-7")["gain"] == 400
