"""apply_camera_settings on an Alpaca session (issue #43): the profile is found by
the ``alpaca:<UniqueID>`` key, a different configured camera is refused, the
device's Bayer pattern survives a settings change, offset is left alone when
the camera has none, and a live manual WB change reaches the delivered pixels."""
import numpy as np
import pytest

from services.camera.camera_source import alpaca_profile_key
from services.config import Config
from tests.alpaca_rig import make_rig
from ui.controllers.camera_settings import apply_camera_settings


def _connected(monkeypatch, **spec):
    rig = make_rig(monkeypatch, **spec)
    assert rig.session.connect_camera(), rig.session._connection.last_error
    rig.session.is_capturing = True
    return rig


def _config(tmp_path, rig, unique_id=None, source="alpaca", **profile):
    config = Config(str(tmp_path / "config.json"))
    config.set("camera_source", source)
    config.set("zwo_auto_exposure", False)
    alpaca = {"host": rig.net.host, "port": rig.net.port, "device_number": 0,
              "unique_id": rig.cam.spec.unique_id if unique_id is None else unique_id,
              "name": "PFR Alpaca Simulator"}
    config.set("alpaca_camera", alpaca)
    stored = {"exposure_ms": 2000, "gain": 123, "offset": 17, "bayer_pattern": "GRBG",
              "flip": 0, "max_exposure_ms": 30000}
    stored.update(profile)
    config.save_camera_profile("PFR Alpaca Simulator", stored, serial=alpaca_profile_key(alpaca))
    return config


def test_profile_reached_by_the_alpaca_key_is_pushed_live(monkeypatch, tmp_path):
    rig = _connected(monkeypatch)
    config = _config(tmp_path, rig)
    apply_camera_settings(rig.session, config)
    assert rig.session.gain == 123
    assert rig.session.exposure_seconds == pytest.approx(2.0)
    assert rig.cam.offset == 17


def test_device_bayer_pattern_is_not_clobbered_by_the_profile(monkeypatch, tmp_path):
    rig = _connected(monkeypatch)
    before = rig.session.bayer_pattern
    apply_camera_settings(rig.session, _config(tmp_path, rig, bayer_pattern="GRBG"))
    assert rig.session.bayer_pattern == before != "GRBG"


def test_a_different_configured_camera_is_refused(monkeypatch, tmp_path):
    rig = _connected(monkeypatch)
    gain_before = rig.session.gain
    apply_camera_settings(rig.session, _config(tmp_path, rig, unique_id="another-camera"))
    assert rig.session.gain == gain_before
    assert rig.cam.offset != 17


def test_switching_source_mid_capture_pushes_nothing(monkeypatch, tmp_path):
    rig = _connected(monkeypatch)
    gain_before = rig.session.gain
    apply_camera_settings(rig.session, _config(tmp_path, rig, source="zwo"))
    assert rig.session.gain == gain_before


def test_offset_is_skipped_when_the_camera_has_none(monkeypatch, tmp_path):
    rig = _connected(monkeypatch, offset_mode="none")
    assert rig.session.capabilities.offset is False
    calls = []
    rig.session.set_offset_live = calls.append
    apply_camera_settings(rig.session, _config(tmp_path, rig))
    assert calls == []
    assert rig.session.gain == 123


def test_white_balance_change_is_stored_without_a_device_write(monkeypatch, tmp_path):
    rig = _connected(monkeypatch)
    config = _config(tmp_path, rig, wb_r=40, wb_b=60)
    config.set("white_balance", {"mode": "asi_auto"})
    puts_before = len(rig.net.requests)
    apply_camera_settings(rig.session, config)
    members = {r.member for r in rig.net.requests[puts_before:] if r.method == "PUT"}
    assert members <= {"gain", "offset"}
    assert rig.session.wb_config["mode"] == "gray_world"
    assert config.get("white_balance") == {"mode": "asi_auto"}


def _red_mean(img):
    a = np.asarray(img).astype(float)
    return a[1:a.shape[0] // 2 - 1, :, 0].mean()


def test_live_manual_red_gain_scales_red_in_the_delivered_frame(monkeypatch, tmp_path):
    rig = _connected(monkeypatch, scene="red_top_blue_bottom", width=32, height=24)
    config = _config(tmp_path, rig)
    reds = []
    for gain in (1.0, 1.5):
        config.set("white_balance", {"mode": "manual", "manual_red_gain": gain,
                                     "manual_blue_gain": 1.0})
        apply_camera_settings(rig.session, config)
        img, _meta = rig.session.capture_single_frame()
        reds.append(_red_mean(img))
    assert reds[1] == pytest.approx(reds[0] * 1.5, rel=0.05)
