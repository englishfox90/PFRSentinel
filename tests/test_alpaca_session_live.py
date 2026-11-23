"""AlpacaCamera against the real-HTTP simulator on 127.0.0.1 (scripts/dev/alpaca_camera_sim.py).

Real sockets, real clock, the shared capture loop on its own thread, auto-exposure on.
"""
import importlib.util
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from services import posthog_service
from services.camera.alpaca.alpaca_camera import AlpacaCamera
from services.camera.alpaca.alpaca_probe import probe_alpaca_camera
from services.camera.frame_metadata import FRAME_METADATA_KEYS
from tests.alpaca_fake import load_sim_package

pytestmark = pytest.mark.requires_network

_SIM_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev" / "alpaca_camera_sim.py"


def _sim_module():
    load_sim_package()
    if "alpaca_camera_sim" in sys.modules:
        return sys.modules["alpaca_camera_sim"]
    spec = importlib.util.spec_from_file_location("alpaca_camera_sim", _SIM_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["alpaca_camera_sim"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sim_server():
    sim = _sim_module()
    servers = []

    def start(**spec):
        camera = sim.FakeAlpacaCamera(sim.SimCameraSpec(**spec))
        server = sim.SimServer(camera, host="127.0.0.1", port=0, discovery_port=None).start()
        servers.append(server)
        return server
    yield start
    for server in servers:
        server.stop()


@pytest.fixture(autouse=True)
def _no_analytics(monkeypatch):
    monkeypatch.setattr(posthog_service, "capture_event", lambda *a, **k: None)
    from services.camera import capture_loop
    monkeypatch.setattr(capture_loop, "capture_error", lambda *a, **k: None)


def _settings(server, **extra):
    settings = {"host": "127.0.0.1", "port": server.port, "device_number": 0, "unique_id": "",
                "name": "", "use_imagebytes": True, "connect_timeout_s": 3.0,
                "request_timeout_s": 10.0, "release_on_disconnect": False}
    settings.update(extra)
    return settings


def _capture(server, frames_wanted=3, timeout_s=60.0, **session_kwargs):
    frames, errors = [], []
    done = threading.Event()

    def on_frame(img, meta):
        frames.append((np.asarray(img).copy(), meta))
        if len(frames) >= frames_wanted:
            done.set()

    session = AlpacaCamera(_settings(server), **session_kwargs)
    session.on_error_callback = lambda msg, is_fatal=False: errors.append((msg, is_fatal))
    assert session.connect_camera(), session._connection.last_error
    session.set_capture_interval(1.0)
    assert session.start_capture(on_frame)
    try:
        assert done.wait(timeout_s), f"only {len(frames)} frames; errors {errors}"
    finally:
        session.stop_capture()
        session.disconnect_camera()
    assert not any(fatal for _m, fatal in errors)
    return session, frames


@pytest.mark.parametrize("offset", [(0, 0), (1, 1)])
def test_live_frames_have_full_metadata_and_correct_colour(sim_server, offset):
    server = sim_server(width=96, height=64, scene="red_top_blue_bottom",
                        bayer_offset_x=offset[0], bayer_offset_y=offset[1])
    session, frames = _capture(server, exposure_sec=0.01, auto_exposure=True,
                               max_exposure_sec=0.2, wb_config={"mode": "manual"})
    assert session.calibration_complete is True
    for img, meta in frames:
        assert tuple(meta) == FRAME_METADATA_KEYS
        assert all(meta[k] is not None for k in FRAME_METADATA_KEYS
                   if k not in ("RAW_RGB_NO_WB",))
        assert meta["RES"] == "96x64" and meta["CAMERA"] == "PFR Alpaca Simulator"
        top = img[2:28].reshape(-1, 3).astype(float).mean(0)
        bottom = img[36:62].reshape(-1, 3).astype(float).mean(0)
        assert top[0] > 4 * top[2] and bottom[2] > 4 * bottom[0]
    assert session.last_image_format == "imagebytes"


def test_live_auto_exposure_converges_on_the_sky_scene(sim_server):
    server = sim_server(width=128, height=128, scene="sky", gain=500)
    session, frames = _capture(server, exposure_sec=0.002, gain=500, auto_exposure=True,
                               max_exposure_sec=2.0, wb_config={"mode": "manual"})
    history = session.calibration_manager.calibration_history
    assert abs(history[-1][1] - 100) < 20
    assert all(float(meta["EXPOSURE"].rstrip("s")) <= 2.0 for _img, meta in frames)


def test_live_json_fallback_and_probe(sim_server):
    server = sim_server(width=64, height=48, supports_imagebytes=False)
    result = probe_alpaca_camera(_settings(server))
    assert result.ok is True and result.message.startswith("Connected: PFR Alpaca Simulator 64×48")
    session, frames = _capture(server, frames_wanted=1, exposure_sec=0.01)
    assert session.last_image_format == "json"


def test_live_issue_177_stalled_camera_recovers_without_stop_start(sim_server, monkeypatch):
    """Issue #177 end to end over real HTTP: after one good frame the camera keeps
    answering but never finishes an exposure (ZWO's ASCOM driver once NINA opened
    another ZWO camera). The session reconnects, has the driver reopen the camera,
    proves it with a short exposure and captures again — no Stop + Start."""
    from services.camera.alpaca import alpaca_capture_frame, alpaca_stall_recovery
    monkeypatch.setattr(alpaca_capture_frame, "EXPOSURE_GRACE_S", 1.0)
    monkeypatch.setattr(alpaca_stall_recovery, "PROBE_GRACE_S", 1.0)
    server = sim_server(width=64, height=48)
    server.camera.faults.stall_after_exposures = 1
    session, frames = _capture(server, frames_wanted=2, exposure_sec=0.2,
                               wb_config={"mode": "manual"}, timeout_s=120.0)
    connected = [p["Connected"] for m, name, p in server.camera.request_log
                 if m == "PUT" and name == "connected"]
    assert connected == ["True", "False", "True"]
    assert session.stalled_exposures == 0
