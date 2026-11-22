"""Headless runner on an Alpaca source (issue #43): ``_init_camera`` dispatches
on ``camera_source``, the Alpaca branch opens the camera through the session
factory and persists its identity, frames come off it, ``/status`` reports
``camera_source``, and a dropped server is reopened after a failed frame."""
from unittest.mock import MagicMock

import pytest

import services.headless_runner as headless_runner
from services.camera.alpaca import alpaca_connection
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.camera_source import alpaca_profile_key
from services.config import Config
from services.headless_alpaca import reconnect_after_error
from tests.alpaca_fake import make_fake
from tests.alpaca_rig import ALPACA_TIME_MODULES, FakeTime, patch_time


@pytest.fixture
def fake_net(monkeypatch):
    net, cam, clock = make_fake()

    def client(*args, **kwargs):
        kwargs["session_factory"] = net.session
        return AlpacaHttpClient(*args, **kwargs)

    monkeypatch.setattr(alpaca_connection, "AlpacaHttpClient", client)
    patch_time(monkeypatch, FakeTime(clock), *ALPACA_TIME_MODULES)
    return net, cam


def _runner(monkeypatch, tmp_path, source="alpaca", host="192.0.2.10"):
    path = str(tmp_path / "config.json")
    seed = Config(path)
    seed.set("camera_source", source)
    seed.set("zwo_auto_exposure", False)
    seed.set("alpaca_camera", {"host": host, "port": 11111, "device_number": 0})
    seed.save()
    monkeypatch.setattr(headless_runner, "Config", lambda: Config(path))
    monkeypatch.setattr(headless_runner.signal, "signal", lambda *a: None)
    runner = headless_runner.HeadlessRunner()
    runner._log = MagicMock()
    runner.config.load()
    return runner


def test_alpaca_init_opens_the_camera_and_persists_its_identity(monkeypatch, tmp_path,
                                                               fake_net):
    _net, cam = fake_net
    monkeypatch.setattr(headless_runner, "resolve_library_path",
                        MagicMock(side_effect=AssertionError("ZWO SDK path read")))
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera() is True
    assert runner.zwo_camera.backend == "alpaca"
    saved = Config(runner.config.config_path).get("alpaca_camera")
    assert saved["unique_id"] == cam.spec.unique_id
    img, meta = runner.zwo_camera.capture_single_frame()
    assert img.size == (cam.spec.width, cam.spec.height)
    assert meta["CAMERA"] == "PFR Alpaca Simulator"
    runner.zwo_camera.disconnect_camera()


def test_alpaca_profile_is_the_active_profile(monkeypatch, tmp_path, fake_net):
    runner = _runner(monkeypatch, tmp_path)
    key = alpaca_profile_key(runner.config.get("alpaca_camera"))
    runner.config.save_camera_profile("", {"exposure_ms": 1234, "gain": 77}, serial=key)
    assert runner._active_profile()["exposure_ms"] == 1234
    runner._load_config()
    logged = " ".join(str(c.args[0]) for c in runner._log.call_args_list)
    assert "Alpaca server: 192.0.2.10:11111 #0" in logged and "SDK Path" not in logged


def test_missing_host_or_unreachable_server_fails_cleanly(monkeypatch, tmp_path, fake_net):
    net, _cam = fake_net
    assert _runner(monkeypatch, tmp_path / "a", host="")._init_camera() is False
    net.faults.refused_hosts.add(net.host)
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera() is False
    logged = " ".join(str(c.args[0]) for c in runner._log.call_args_list)
    assert "192.0.2.10:11111" in logged and "ClientID" not in logged


def test_zwo_source_still_takes_the_sdk_branch(monkeypatch, tmp_path):
    resolve = MagicMock(return_value="")
    monkeypatch.setattr(headless_runner, "resolve_library_path", resolve)
    runner = _runner(monkeypatch, tmp_path, source="zwo")
    assert runner._init_camera() is False
    resolve.assert_called_once()


def test_status_snapshot_carries_the_camera_source(monkeypatch, tmp_path, fake_net):
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera()
    runner.web_server = MagicMock(running=True)
    runner._push_capture_status(running=True, state="capturing")
    snapshot = runner.web_server.update_capture_status.call_args.args[0]
    assert snapshot["mode"] == "camera" and snapshot["camera_source"] == "alpaca"
    runner.zwo_camera.disconnect_camera()


def test_dropped_server_is_reopened_after_a_failed_frame(monkeypatch, tmp_path, fake_net):
    net, _cam = fake_net
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera()
    net.faults.connection_errors = 50
    with pytest.raises(Exception):
        runner.zwo_camera.capture_single_frame()
    assert reconnect_after_error(runner.zwo_camera, runner._log) is False
    net.faults.connection_errors = 0
    assert reconnect_after_error(runner.zwo_camera, runner._log) is True
    runner.zwo_camera.capture_single_frame()
    assert reconnect_after_error(MagicMock(backend="zwo"), runner._log) is None
    runner.zwo_camera.disconnect_camera()


def test_recovery_off_spends_one_reconnect_until_a_good_frame(monkeypatch, tmp_path, fake_net):
    from services.headless_alpaca import note_good_frame, recovery_exhausted
    net, _cam = fake_net
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera()
    cam = runner.zwo_camera
    cam.auto_recovery_enabled = False
    attempts = []
    original = cam.reconnect_camera_safe
    cam.reconnect_camera_safe = lambda: attempts.append(1) or original()
    assert reconnect_after_error(cam, runner._log) is True
    assert recovery_exhausted(cam) is True
    assert reconnect_after_error(cam, runner._log) is False
    assert len(attempts) == 1
    note_good_frame(cam)
    assert recovery_exhausted(cam) is False
    assert reconnect_after_error(cam, runner._log) is True and len(attempts) == 2
    cam.disconnect_camera()


def test_recovery_on_always_reconnects(monkeypatch, tmp_path, fake_net):
    from services.headless_alpaca import recovery_exhausted
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera()
    cam = runner.zwo_camera
    for _ in range(3):
        assert reconnect_after_error(cam, runner._log) is True
    assert recovery_exhausted(cam) is False
    cam.disconnect_camera()


def test_open_logs_one_line_and_no_connection_duplicates(monkeypatch, tmp_path, fake_net, caplog):
    import logging
    net, _cam = fake_net
    net.faults.refused_hosts.add(net.host)
    runner = _runner(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="PFRSentinel"):
        assert runner._init_camera() is False
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert not [m for m in info if m.startswith("✗ Alpaca connect failed")]
    assert runner._log.call_count == 1
    assert "ERROR: Failed to connect" in runner._log.call_args.args[0]


def test_expected_failures_keep_their_traceback_at_debug():
    from services.camera.alpaca.alpaca_protocol import AlpacaTransportError
    from services.headless_alpaca import is_expected_failure
    alpaca = MagicMock(backend="alpaca")
    assert is_expected_failure(alpaca, AlpacaTransportError("gone"))
    assert is_expected_failure(alpaca, Exception("Camera not connected"))
    assert not is_expected_failure(alpaca, KeyError("RES"))
    assert not is_expected_failure(MagicMock(backend="zwo"), Exception("x"))


def test_after_failed_frame_pauses_once_the_single_reconnect_is_spent(monkeypatch, tmp_path,
                                                                     fake_net):
    from services.headless_alpaca import after_failed_frame, recovery_exhausted
    runner = _runner(monkeypatch, tmp_path)
    assert runner._init_camera()
    cam = runner.zwo_camera
    cam.auto_recovery_enabled = False
    assert after_failed_frame(cam, runner._log) is False      # the one reconnect
    assert after_failed_frame(cam, runner._log) is True       # pause
    assert "Capture paused" in runner._log.call_args.args[0]
    assert recovery_exhausted(cam) is False                   # a resume may reconnect again
    assert after_failed_frame(MagicMock(backend="zwo"), runner._log) is False
    cam.disconnect_camera()
