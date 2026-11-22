"""CameraCalibration on the Alpaca facade: converges on the simulator's exposure x gain scene.

The simulator runs on the real clock with millisecond exposures, so the
calibration's own (unpatched) waits stay short.
"""
import time

import numpy as np
import pytest

from services import posthog_service
from services.camera.alpaca.alpaca_camera import AlpacaCamera
from services.camera.camera_calibration import CameraCalibration
from tests.alpaca_fake import make_fake
from tests.alpaca_rig import alpaca_settings


@pytest.fixture(autouse=True)
def _no_analytics(monkeypatch):
    monkeypatch.setattr(posthog_service, "capture_event", lambda *a, **k: None)


def _session(max_adu, **kwargs):
    net, cam, _clock = make_fake(width=64, height=64, max_adu=max_adu, seed=7)
    cam.clock = time.monotonic
    session = AlpacaCamera(alpaca_settings(net), exposure_sec=0.002, gain=500,
                           auto_exposure=True, max_exposure_sec=2.0,
                           session_factory=net.session, **kwargs)
    assert session.connect_camera(), session._connection.last_error
    return session, cam


@pytest.mark.parametrize("max_adu", [255, 4095])
def test_calibration_converges_to_target_brightness(max_adu):
    session, cam = _session(max_adu)
    assert session.calibration_manager.bit_depth == (8 if max_adu == 255 else 16)
    session.run_calibration()
    history = session.calibration_manager.calibration_history
    final_exposure, final_brightness = history[-1]
    assert abs(final_brightness - 100) < 20
    assert session.calibration_complete is True
    assert session.exposure_seconds == pytest.approx(final_exposure)
    durations = [float(p["Duration"]) for p in cam.puts("startexposure")]
    assert durations == pytest.approx([float(e) for e, _b in history])


def test_microseconds_become_seconds_only_in_the_facade():
    session, cam = _session(255)
    manager = CameraCalibration(session.camera, session.asi, lambda _m: None, bit_depth=8)
    manager.update_settings(exposure_seconds=0.002, gain=500, target_brightness=100,
                            max_exposure_sec=2.0)
    session.camera.set_control_value(session.asi.ASI_EXPOSURE, 37_500)
    session.camera.start_exposure()
    assert cam.puts("startexposure")[-1]["Duration"] == "0.0375"


def test_adjust_exposure_auto_writes_through_the_facade():
    session, cam = _session(255)
    session.calibration_complete = True
    dark = np.full((64, 64, 3), 20, dtype=np.uint8)
    before = session.exposure_seconds
    session.adjust_exposure_auto(dark)
    assert session.exposure_seconds > before
    assert session.camera.pending_exposure_s == pytest.approx(session.exposure_seconds)
