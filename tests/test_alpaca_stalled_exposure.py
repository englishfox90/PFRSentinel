"""Issue #177: an Alpaca camera that answers but never finishes an exposure.

ZWO's ASCOM driver behind ASCOM Remote, after NINA opened another ZWO camera,
kept reading Connected and Exposing while no exposure completed. The shared
loop went timeout → reconnect → timeout at "1/5" forever, the pill read
"Reconnecting…" although every reconnect succeeded, and only Stop + Start got
frames again. The simulator's ``stall_exposures`` fault reproduces it: it
clears only when the driver lets go of the camera (Connected=false).
"""
import logging

import pytest

from services.camera import capture_loop
from services.camera.alpaca import alpaca_stall_recovery
from services.camera.errors import ExposureStalledError
from tests.alpaca_rig import make_rig

LONG_S = 30.0


@pytest.fixture
def stall_rig(monkeypatch):
    def build(time_limit=4000.0, settings=None, **spec):
        rig = make_rig(monkeypatch, session_kwargs={"exposure_sec": LONG_S},
                       settings=settings,
                       extra_time_modules=("services.camera.capture_loop",), **spec)
        monkeypatch.setattr(capture_loop, "capture_error", lambda *a, **k: None)
        rig.lines, rig.errors, rig.frames, rig.links = [], [], [], []
        s = rig.session
        s.set_capture_interval(15.0)
        start = rig.clock.now

        def log(line):
            rig.lines.append(line)
            rig.links.append(s.link_state)
            if rig.clock.now - start > time_limit:
                s.is_capturing = False
        s.on_log_callback = log
        s.on_error_callback = lambda msg, is_fatal=False: rig.errors.append((msg, is_fatal))
        assert s.connect_camera(), s._connection.last_error
        s.is_capturing = True
        return rig
    return build


def _run(rig, stop_after_frames, on_frame=None):
    def frame(img, meta):
        rig.frames.append(meta)
        if on_frame:
            on_frame(len(rig.frames))
        if len(rig.frames) >= stop_after_frames:
            rig.session.is_capturing = False
    rig.session.on_frame_callback = frame
    rig.session.capture_loop()


def _stall_after_first_frame(rig):
    def stall(n):
        if n == 1:
            rig.cam.faults.stall_exposures = True
    return stall


def test_stalled_camera_is_reopened_and_capture_resumes(stall_rig, caplog):
    rig = stall_rig()
    caplog.set_level(logging.DEBUG)
    _run(rig, stop_after_frames=3, on_frame=_stall_after_first_frame(rig))
    assert len(rig.frames) == 3
    # The driver was made to let go of the camera exactly once, then reopened.
    assert [p["Connected"] for p in rig.cam.puts("connected")] == ["True", "False", "True"]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert sum("reopening it in the driver" in m for m in warnings) == 1
    assert any("may see it disconnect" in m for m in warnings)
    # A short test exposure proved the camera before 30 s ones resumed.
    assert any(line.startswith("✓ Alpaca camera finished a 1s test exposure") for line in rig.lines)
    assert rig.session.link_state == 'ok'
    assert not any(fatal for _m, fatal in rig.errors)


def test_timeout_names_what_the_camera_reported(stall_rig, caplog):
    rig = stall_rig()
    caplog.set_level(logging.DEBUG)
    _run(rig, stop_after_frames=2, on_frame=_stall_after_first_frame(rig))
    first = next(r.getMessage() for r in caplog.records
                 if "ERROR in capture loop" in r.getMessage())
    assert "did not complete 30.0s exposure within 60.0s" in first
    assert "camera still reports Exposing" in first and "after abort it reads Idle" in first
    assert any("overdue: CameraState Exposing" in r.getMessage() for r in caplog.records
               if r.levelname == "DEBUG")
    # Expected weather on a network camera: no traceback at INFO.
    assert not any(line.startswith("Stack trace") for line in rig.lines)


def test_pill_says_stalled_not_reconnecting_after_a_reconnect_that_answered(stall_rig):
    rig = stall_rig()
    _run(rig, stop_after_frames=3, on_frame=_stall_after_first_frame(rig))
    after = rig.lines.index("✓ Camera reconnected successfully")
    assert rig.links[after] == 'stalled'


def test_long_exposures_that_never_finish_are_never_looped_at_one_of_five(stall_rig):
    """A driver no reopen can fix: the count climbs to long-retry, it never resets to 1/5."""
    rig = stall_rig(time_limit=3000.0)

    def stall(n):
        if n == 1:
            rig.cam.faults.stall_exposures = True
            rig.cam.driver_reopened = lambda: None
    _run(rig, stop_after_frames=99, on_frame=stall)
    counts = [line for line in rig.lines if line.startswith("Consecutive errors")]
    assert counts[:2] == ["Consecutive errors: 1/5", "Consecutive errors: 2/5"]
    assert any(line.startswith("⏳ Entering long-interval retry mode") for line in rig.lines)
    # Errors are reported once per episode, not once per reconnect cycle.
    assert sum(msg.startswith("Capture error:") for msg, _f in rig.errors) == 1


def test_short_exposures_work_long_ones_stall_still_reaches_the_reopen(stall_rig, monkeypatch):
    """The probe passing must not clear the stall count, or the reopen never comes."""
    rig = stall_rig()
    cam = rig.cam
    original = cam._advance

    def advance():
        cam.faults.stall_exposures = cam.duration > 5.0 and cam.stall_long
        original()
    cam.stall_long = False
    monkeypatch.setattr(cam, "_advance", advance)
    monkeypatch.setattr(cam, "driver_reopened", lambda: setattr(cam, "stall_long", False))

    def stall(n):
        if n == 1:
            cam.stall_long = True
    _run(rig, stop_after_frames=3, on_frame=stall)
    assert len(rig.frames) == 3
    assert [p["Connected"] for p in cam.puts("connected")] == ["True", "False", "True"]


def test_transport_outage_never_reopens_the_driver(stall_rig):
    rig = stall_rig()

    def outage(n):
        if n == 1:
            rig.net.faults.connection_errors = 6
    _run(rig, stop_after_frames=2, on_frame=outage)
    assert len(rig.frames) == 2
    assert [p["Connected"] for p in rig.cam.puts("connected")] == ["True"]
    assert rig.session.stalled_exposures == 0


def test_reopen_is_rate_limited(stall_rig):
    rig = stall_rig()
    s = rig.session
    s.stalled_exposures = alpaca_stall_recovery.REOPEN_AFTER_STALLS
    assert alpaca_stall_recovery.should_reopen(s) is True
    assert alpaca_stall_recovery.should_reopen(s) is False
    rig.clock.advance(alpaca_stall_recovery.REOPEN_MIN_INTERVAL_S + 1)
    assert alpaca_stall_recovery.should_reopen(s) is True


def test_one_stall_does_not_reopen(stall_rig):
    s = stall_rig().session
    s.stalled_exposures = 1
    assert alpaca_stall_recovery.should_reopen(s) is False


def test_v4_reopen_uses_disconnect_then_connect(stall_rig):
    rig = stall_rig(interface_version=4)
    rig.cam.faults.stall_exposures = True
    s = rig.session
    s.stalled_exposures = 2
    s.is_capturing = True
    assert s.reconnect_camera_safe()
    assert len(rig.cam.puts("disconnect")) == 1 and len(rig.cam.puts("connect")) >= 1
    assert rig.cam.faults.stall_exposures is False


def test_probe_counts_another_clients_exposure_as_a_working_camera(stall_rig):
    rig = stall_rig()
    s = rig.session
    s.stalled_exposures = 1
    rig.cam.start_exposure(5.0, True)      # NINA's exposure is running
    assert alpaca_stall_recovery.probe_exposure(s) is True
    assert rig.cam.puts("abortexposure") == []


def test_stall_error_is_a_camera_timeout():
    from services.camera.errors import CameraTimeoutError
    assert issubclass(ExposureStalledError, CameraTimeoutError)
