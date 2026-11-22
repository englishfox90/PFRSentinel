"""The shared capture loop driving a real AlpacaCamera on the fake network (fake clock)."""
import pytest

from services.camera import capture_loop
from services.camera.capabilities import ALPACA_TROUBLESHOOTING_HINTS
from tests.alpaca_rig import make_rig


@pytest.fixture
def loop_rig(monkeypatch):
    def build(session_kwargs=None, settings=None, **spec):
        rig = make_rig(monkeypatch, session_kwargs=session_kwargs, settings=settings,
                       extra_time_modules=("services.camera.capture_loop",), **spec)
        monkeypatch.setattr(capture_loop, "capture_error", lambda *a, **k: None)
        rig.lines = []
        rig.errors = []
        rig.frames = []
        s = rig.session
        s.on_log_callback = rig.lines.append
        s.on_error_callback = lambda msg, is_fatal=False: rig.errors.append((msg, is_fatal))
        s.set_capture_interval(5.0)
        assert s.connect_camera(), s._connection.last_error
        s.is_capturing = True
        return rig
    return build


def _run(rig, stop_after_frames=None, on_frame=None):
    def frame(img, meta):
        rig.frames.append(meta)
        if on_frame:
            on_frame(len(rig.frames))
        if stop_after_frames and len(rig.frames) >= stop_after_frames:
            rig.session.is_capturing = False
    rig.session.on_frame_callback = frame
    rig.session.capture_loop()


def test_outage_reconnect_resume(loop_rig):
    rig = loop_rig()

    def outage(n):
        if n == 2:
            rig.cam.drop_connection()
            rig.net.faults.connection_errors = 3
    _run(rig, stop_after_frames=4, on_frame=outage)
    assert len(rig.frames) == 4
    assert "✓ Camera reconnected successfully" in rig.lines
    assert rig.cam.connected is True
    assert not any(fatal for _m, fatal in rig.errors)
    assert [p["Connected"] for p in rig.cam.puts("connected")] == ["True", "True"]


def test_transport_outage_that_outlasts_one_reconnect_still_recovers(loop_rig):
    rig = loop_rig()

    def outage(n):
        if n == 1:
            rig.net.faults.refused_hosts.add(rig.net.host)
            rig.clock_outage_until = rig.clock.now + 20.0

    original = rig.time.sleep

    def sleep(seconds):
        original(seconds)
        if getattr(rig, "clock_outage_until", None) and rig.clock.now > rig.clock_outage_until:
            rig.net.faults.refused_hosts.discard(rig.net.host)
    rig.time.sleep = sleep
    _run(rig, stop_after_frames=2, on_frame=outage)
    assert len(rig.frames) == 2
    assert any(line.startswith("✗ Reconnection attempt failed") for line in rig.lines)
    assert "✓ Camera reconnected successfully" in rig.lines


def test_recovery_off_gets_one_reconnect_then_stops_and_releases(loop_rig):
    rig = loop_rig()
    rig.session.auto_recovery_enabled = False

    def outage(n):
        rig.net.faults.refused_hosts.add(rig.net.host)
    _run(rig, on_frame=outage)
    assert len(rig.frames) == 1
    assert rig.session.is_capturing is False
    msg, fatal = rig.errors[-1]
    assert fatal is True and "automatic recovery is off" in msg
    assert rig.session.camera is None
    assert sum(line.startswith("Initiating reconnection attempt") for line in rig.lines) == 1


def test_gated_off_peak_never_releases_the_device_by_default(loop_rig):
    rig = loop_rig(session_kwargs={"scheduled_capture_mode": "gated"})
    rig.session.schedule_gate = lambda: False
    rig.session.status_callback = lambda _text: setattr(rig.session, "is_capturing", False)
    _run(rig)
    assert "✓ Camera disconnected for off-peak hours (reducing hardware load)" in rig.lines
    assert rig.session.camera is None
    assert rig.cam.connected is True
    assert [p["Connected"] for p in rig.cam.puts("connected")] == ["True"]


def test_gated_off_peak_releases_when_configured(loop_rig):
    rig = loop_rig(session_kwargs={"scheduled_capture_mode": "gated"},
                   settings={"release_on_disconnect": True})
    rig.session.schedule_gate = lambda: False
    rig.session.status_callback = lambda _text: setattr(rig.session, "is_capturing", False)
    _run(rig)
    assert rig.cam.connected is False
    assert [p["Connected"] for p in rig.cam.puts("connected")] == ["True", "False"]


def test_window_reopen_after_off_peak(loop_rig):
    rig = loop_rig(session_kwargs={"scheduled_capture_mode": "gated"})
    open_after = rig.clock.now + 60.0
    rig.session.schedule_gate = lambda: rig.clock.now >= open_after
    _run(rig, stop_after_frames=1)
    assert "✓ Camera reconnected successfully for scheduled captures" in rig.lines
    assert len(rig.frames) == 1


def test_unreachable_camera_prints_the_alpaca_hints(loop_rig):
    rig = loop_rig()
    rig.net.faults.refused_hosts.add(rig.net.host)

    def log(line):
        rig.lines.append(line)
        if line.startswith("⏳ Entering long-interval retry mode"):
            rig.session.is_capturing = False
    rig.session.on_log_callback = log
    _run(rig)
    start = rig.lines.index("Troubleshooting steps:")
    shown = rig.lines[start + 1:start + 1 + len(ALPACA_TROUBLESHOOTING_HINTS)]
    assert shown == [f"  {i}. {h}" for i, h in enumerate(ALPACA_TROUBLESHOOTING_HINTS, 1)]
    assert not any("USB" in line for line in shown)
