"""alpaca_frame_budget + the capture watchdog — a long exposure over a slow link is
not called wedged (and its download not cancelled) every frame; the download
deadline follows the measured throughput; ZWO's threshold is unchanged; a
watchdog cancel of a download is consumed once."""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.camera.alpaca import alpaca_frame_budget as budget
from services.camera.alpaca.alpaca_static_info import AlpacaStaticInfo
from tests.alpaca_rig import make_rig


def _info(width=6248, height=4176, sensor_type=2):
    return AlpacaStaticInfo(
        name="cam", description=None, driver_info=None, driver_version=None,
        interface_version=3, sensor_name=None, sensor_type=sensor_type, bayer_offset_x=0,
        bayer_offset_y=0, width=width, height=height, pixel_size_x=None,
        electrons_per_adu=None, max_adu=65535, exposure_min=None, exposure_max=None,
        can_abort=True, can_stop=True, gain_mode=None, gain_min=None, gain_max=None, gains=(),
        offset_mode=None, offset_min=None, offset_max=None, offsets=(), has_temperature=False,
        max_bin_x=None)


def test_unmeasured_download_budget_assumes_a_slow_vpn():
    info = _info()
    seconds = budget.download_timeout_s(info, True, stall_s=10.0)
    assert seconds == pytest.approx(6248 * 4176 * 4 / budget.FLOOR_BYTES_PER_S + 10.0)


def test_measured_throughput_sets_the_budget_with_a_floor_and_a_cap():
    info = _info()
    assert budget.download_timeout_s(info, True, last_bytes=52e6, last_s=40.0,
                                     stall_s=10.0) == pytest.approx(130.0)
    assert budget.download_timeout_s(info, True, last_bytes=52e6, last_s=0.5,
                                     stall_s=10.0) == budget.MIN_DOWNLOAD_TIMEOUT_S
    assert budget.download_timeout_s(info, True, last_bytes=52e6, last_s=5000,
                                     stall_s=10.0) == budget.MAX_DOWNLOAD_TIMEOUT_S
    assert budget.download_timeout_s(_info(16, 12), True) == budget.MIN_DOWNLOAD_TIMEOUT_S


def test_frame_budget_is_exposure_plus_grace_plus_download(monkeypatch):
    rig = make_rig(monkeypatch, width=16, height=12)
    s = rig.session
    assert s.frame_budget_s == pytest.approx(
        s.exposure_seconds + budget.EXPOSURE_GRACE_S + budget.MIN_DOWNLOAD_TIMEOUT_S
        + budget.WATCHDOG_MARGIN_S)
    assert s.connect_camera()
    s.exposure_seconds = 240.0
    s.last_download_s, s.last_download_bytes = 120.0, 52_000_000
    assert s.frame_budget_s == pytest.approx(240 + 30 + 3 * 120 + 10 + 60)


def _watchdog(cam, stale_for):
    from ui.main_window.capture_watchdog import _CaptureWatchdogMixin
    host = _CaptureWatchdogMixin()
    host.is_capturing = True
    host.camera_controller = MagicMock(zwo_camera=cam)
    host._watchdog_first_fire_ts = None
    host._watchdog_ui_fatal_sent = False
    cam._last_frame_time = time.time() - stale_for
    host._check_capture_watchdog()
    return host


def test_watchdog_waits_out_an_alpaca_frame_budget():
    cam = SimpleNamespace(backend='alpaca', is_capturing=True, capture_interval=5.0,
                          exposure_seconds=240.0, long_retry_mode_public=False,
                          _recovery_requested=False, frame_budget_s=700.0)
    _watchdog(cam, stale_for=400.0)
    assert cam._recovery_requested is False      # old threshold (300 s) fired here
    _watchdog(cam, stale_for=710.0)
    assert cam._recovery_requested is True


def test_zwo_watchdog_threshold_is_unchanged():
    cam = SimpleNamespace(backend='zwo', is_capturing=True, capture_interval=5.0,
                          exposure_seconds=240.0, long_retry_mode_public=False,
                          _recovery_requested=False, frame_budget_s=10_000.0)
    _watchdog(cam, stale_for=290.0)
    assert cam._recovery_requested is False
    _watchdog(cam, stale_for=301.0)
    assert cam._recovery_requested is True


def test_watchdog_cancel_of_a_download_is_consumed_once(monkeypatch):
    rig = make_rig(monkeypatch, width=16, height=12)
    s = rig.session
    assert s.connect_camera()
    s.is_capturing = True

    def request_recovery(request):
        if request.member == "imagearray":
            s._recovery_requested = True
    rig.net.request_hook = request_recovery
    with pytest.raises(Exception, match="mid-download"):
        s.capture_single_frame()
    assert s._recovery_requested is False
    rig.net.request_hook = None
    s.capture_single_frame()
