"""
Unit tests for services/api_status.py — the pure capture-status / health logic
behind the richer /status endpoint. No network, no Qt; fast.
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services import api_status
from services.api_status import (
    build_capture_snapshot, derive_health, derive_status_view,
    HEALTH_OK, HEALTH_IDLE, HEALTH_DEGRADED, HEALTH_RECOVERING, HEALTH_ERROR,
)

NOW = 1_000_000.0


def _camera(**overrides):
    """A healthy running-camera snapshot, overridable per test."""
    base = dict(
        mode="camera", enabled=True, running=True, state="capturing",
        interval_seconds=5.0, effective_interval_seconds=5.0,
        schedule={"mode": "always", "start_time": "17:00", "end_time": "09:00",
                  "in_window": True, "window_interval_seconds": None},
        last_capture_epoch=NOW - 3,
        last_error=None,
        recovery={"in_progress": False, "attempts": 0, "unrecoverable": False},
    )
    base.update(overrides)
    return build_capture_snapshot(**base)


class TestDeriveHealth:
    def test_disabled_is_idle(self):
        snap = build_capture_snapshot(enabled=False)
        h = derive_health(snap, image_age=99999, now=NOW)
        assert h["status"] == HEALTH_IDLE
        assert h["reasons"]

    def test_running_fresh_is_ok(self):
        h = derive_health(_camera(), image_age=3, now=NOW)
        assert h["status"] == HEALTH_OK
        assert h["reasons"] == []

    def test_running_but_stalled_is_degraded(self):
        # interval 5s → stall threshold max(300, 15) = 300; age beyond that.
        h = derive_health(_camera(), image_age=400, now=NOW)
        assert h["status"] == HEALTH_DEGRADED
        assert "stalled" in h["reasons"][0]

    def test_outside_gated_window_is_idle_not_error(self):
        snap = _camera(schedule={"mode": "gated", "start_time": "17:00",
                                 "end_time": "09:00", "in_window": False,
                                 "window_interval_seconds": None})
        h = derive_health(snap, image_age=99999, now=NOW)
        assert h["status"] == HEALTH_IDLE
        assert "window" in h["reasons"][0]

    def test_recovery_in_progress_is_recovering(self):
        snap = _camera(running=False, state="recovering",
                       recovery={"in_progress": True, "attempts": 2, "unrecoverable": False})
        h = derive_health(snap, image_age=120, now=NOW)
        assert h["status"] == HEALTH_RECOVERING
        assert "2" in h["reasons"][0]

    def test_unrecoverable_is_error(self):
        snap = _camera(running=False,
                       recovery={"in_progress": False, "attempts": 5, "unrecoverable": True})
        h = derive_health(snap, image_age=120, now=NOW)
        assert h["status"] == HEALTH_ERROR

    def test_last_error_while_stopped_is_error(self):
        snap = _camera(running=False, state="stopped", last_error="boom")
        h = derive_health(snap, image_age=10, now=NOW)
        assert h["status"] == HEALTH_ERROR
        assert h["reasons"][0] == "boom"

    def test_watch_mode_not_marked_degraded_on_age(self):
        # Watch mode has no fixed cadence — staleness alone isn't a fault.
        snap = build_capture_snapshot(mode="watch", enabled=True, running=True,
                                      state="capturing")
        h = derive_health(snap, image_age=99999, now=NOW)
        assert h["status"] == HEALTH_OK


class TestDeriveStatusView:
    def test_next_capture_countdown_for_running_camera(self):
        view = derive_status_view(_camera(last_capture_epoch=NOW - 2),
                                  image_age=2, now=NOW)
        cap = view["capture"]
        assert cap["last_capture_age_seconds"] == 2
        # interval 5s, last frame 2s ago → ~3s to next.
        assert cap["next_capture_in_seconds"] == 3
        assert "last_capture_epoch" not in cap  # internal field stripped

    def test_no_next_capture_in_watch_mode(self):
        snap = build_capture_snapshot(mode="watch", enabled=True, running=True)
        view = derive_status_view(snap, image_age=5, now=NOW)
        assert view["capture"]["next_capture_in_seconds"] is None

    def test_empty_snapshot_is_idle(self):
        view = derive_status_view({}, image_age=None, now=NOW)
        assert view["health"]["status"] == HEALTH_IDLE
        assert view["capture"]["enabled"] is False

    def test_capture_block_matches_field_catalog(self):
        # Every documented capture field is present in the derived view.
        view = derive_status_view(_camera(), image_age=3, now=NOW)
        cap = view["capture"]
        for name, _type, _desc in api_status.CAPTURE_FIELDS:
            assert name in cap, f"missing documented field: {name}"


class TestCameraSource:
    """Issue #43: the backend rides along additively; ``mode`` keeps its enum."""

    def test_camera_mode_reports_its_source(self):
        snap = build_capture_snapshot(mode="camera", camera_source="alpaca", enabled=True)
        assert snap["mode"] == "camera" and snap["camera_source"] == "alpaca"
        view = derive_status_view(snap, image_age=None, now=NOW)
        assert view["capture"]["camera_source"] == "alpaca"

    def test_watch_and_idle_report_no_source(self):
        for mode in ("watch", "idle"):
            assert build_capture_snapshot(mode=mode, camera_source="zwo")["camera_source"] is None
        assert derive_status_view({}, image_age=None, now=NOW)["capture"]["camera_source"] is None

    def test_old_feeders_without_the_argument_still_work(self):
        assert build_capture_snapshot(mode="camera")["camera_source"] is None

    def test_field_is_documented_and_in_the_openapi_schema(self):
        names = [name for name, _t, _d in api_status.CAPTURE_FIELDS]
        assert names.index("camera_source") == names.index("mode") + 1
        from services.api_docs import build_openapi_spec
        assert "camera_source" in str(build_openapi_spec())
