"""Diagnostics summary on an Alpaca rig (issue #43): ``camera_source`` always,
and for a live Alpaca session the driver, sensor, pattern and transfer facts —
without the host or UniqueID the config redaction masks."""
import json
from unittest.mock import MagicMock

import pytest

from services.camera.alpaca.alpaca_diagnostics import session_diagnostics
from tests.alpaca_rig import make_rig
from ui.controllers.diagnostics_controller import DiagnosticsController


def _connected(monkeypatch, **spec):
    rig = make_rig(monkeypatch, **spec)
    assert rig.session.connect_camera()
    rig.session.is_capturing = True
    return rig


def test_session_facts_after_a_frame(monkeypatch):
    rig = _connected(monkeypatch, max_adu=4095)
    rig.session.capture_single_frame()
    facts = session_diagnostics(rig.session)
    assert facts["backend"] == "alpaca" and facts["connected"] is True
    assert facts["max_adu"] == 4095 and facts["sensor_bit_depth"] == 12
    assert facts["sensor_type"] == rig.cam.spec.sensor_type
    assert facts["interface_version"] == rig.cam.spec.interface_version
    assert facts["driver_info"] == rig.cam.spec.driver_info
    assert facts["bayer_pattern"] == rig.session.bayer_pattern
    assert facts["image_format"] == "imagebytes" and facts["imagebytes"] is True
    assert facts["last_download_bytes"] > 0 and facts["last_download_s"] is not None
    text = json.dumps(facts)
    assert rig.net.host not in text and rig.cam.spec.unique_id not in text


def test_facts_before_connect_or_a_frame_never_raise():
    session = MagicMock(spec=["backend"])
    session.backend = "alpaca"
    facts = session_diagnostics(session)
    assert facts["connected"] is False and facts["download_mb_s"] is None


@pytest.mark.parametrize("n_bytes,seconds,rate", [(2_000_000, 0.5, 4.0), (0, 1.0, None),
                                                 (100, 0, None)])
def test_transfer_rate(n_bytes, seconds, rate):
    session = MagicMock(backend="alpaca", last_download_bytes=n_bytes, last_download_s=seconds,
                        last_image_format="json", _connection=MagicMock(static_info=None))
    assert session_diagnostics(session)["download_mb_s"] == rate


def _controller(session, config):
    ctrl = DiagnosticsController.__new__(DiagnosticsController)
    ctrl._mw = MagicMock(camera_controller=MagicMock(zwo_camera=session, is_capturing=True),
                         watch_controller=None, resource_monitor=None)
    return ctrl


def test_summary_names_the_source_and_carries_the_alpaca_block(monkeypatch):
    rig = _connected(monkeypatch)
    cfg = {"capture_mode": "camera", "camera_source": "alpaca",
           "alpaca_camera": {"host": rig.net.host, "name": "PFR Alpaca Simulator"}}
    summary = _controller(rig.session, cfg)._summary(cfg, [])
    assert summary["camera_source"] == "alpaca"
    assert summary["selected_camera"] == "PFR Alpaca Simulator"
    assert summary["live_camera"]["alpaca"]["backend"] == "alpaca"


def test_zwo_summary_is_unchanged_apart_from_the_source():
    zwo = MagicMock(backend="zwo", exposure_seconds=1.0, wb_config={})
    cfg = {"capture_mode": "camera", "zwo_selected_camera_name": "ZWO ASI676MC"}
    summary = _controller(zwo, cfg)._summary(cfg, [])
    assert summary["camera_source"] == "zwo"
    assert summary["selected_camera"] == "ZWO ASI676MC"
    assert "alpaca" not in summary["live_camera"]
