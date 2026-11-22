"""CameraControllerQt on an Alpaca source: no ZWO index, USB or DLL recovery;
identity persisted on the main thread; a start that fails before the first
frame is retried under auto-recovery (issue #43). ZWO behaviour is pinned
alongside so the gate cannot drift into it."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from services.camera.alpaca import alpaca_connection
from services.camera.alpaca.alpaca_camera import AlpacaCamera
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.session_params import read_capture_params
from services.config import Config
from tests.alpaca_fake import make_fake

ACCESS_VIOLATION = "exception: access violation writing 0x0000000000000024"


@pytest.fixture
def qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def fake_net(monkeypatch):
    """Route every AlpacaHttpClient through the in-process fake server."""
    net, cam, _clock = make_fake()

    def client(*args, **kwargs):
        kwargs["session_factory"] = net.session
        return AlpacaHttpClient(*args, **kwargs)

    monkeypatch.setattr(alpaca_connection, "AlpacaHttpClient", client)
    monkeypatch.setattr(AlpacaCamera, "start_capture", lambda self, **_kw: True)
    return net, cam


def _config(tmp_path, source="alpaca", auto_recovery=True, host="192.0.2.10"):
    config = Config(str(tmp_path / "config.json"))
    config.set("camera_source", source)
    config.set("camera_auto_recovery", auto_recovery)
    config.set("alpaca_camera", {"host": host, "port": 11111, "device_number": 0})
    return config


def _controller(config):
    from ui.controllers.camera_controller import CameraControllerQt
    main_window = MagicMock()
    main_window.config = config
    ctrl = CameraControllerQt(main_window)
    ctrl._usb_reset.run_async = MagicMock()
    return ctrl


def _pump(qt_app, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        qt_app.processEvents()
        time.sleep(0.01)
    return predicate()


def test_alpaca_start_never_resolves_a_zwo_index(qt_app, tmp_path, fake_net):
    ctrl = _controller(_config(tmp_path))
    ctrl._resolve_camera_index = MagicMock(side_effect=AssertionError("ZWO path"))
    params = read_capture_params(ctrl.config, True)
    ctrl._start_capture_worker(params)
    ctrl._resolve_camera_index.assert_not_called()
    assert ctrl.is_capturing and ctrl.zwo_camera.backend == "alpaca"
    ctrl._usb_reset.run_async.assert_not_called()


def test_learned_identity_is_persisted_on_the_main_thread(qt_app, tmp_path, fake_net,
                                                          monkeypatch):
    import services.camera.camera_source as camera_source
    _net, cam = fake_net
    ctrl = _controller(_config(tmp_path))
    real_persist = camera_source.persist_alpaca_identity
    threads = []

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real_persist(*args, **kwargs)
    monkeypatch.setattr(camera_source, "persist_alpaca_identity", spy)

    params = read_capture_params(ctrl.config, True)
    worker = threading.Thread(target=ctrl._start_capture_worker, args=(params,))
    worker.start()
    worker.join(10.0)
    assert _pump(qt_app, lambda: ctrl.is_capturing)
    assert threads == [threading.get_ident()]
    saved = ctrl.config.get("alpaca_camera")
    assert saved["unique_id"] == cam.spec.unique_id
    assert saved["name"] == "PFR Alpaca Simulator"
    assert ctrl.config.get("zwo_selected_camera_serial", "") == ""
    assert Config(ctrl.config.config_path).get("alpaca_camera")["unique_id"] == cam.spec.unique_id


def test_connect_failure_reports_the_reason_not_a_generic_line(qt_app, tmp_path, fake_net):
    net, _cam = fake_net
    net.faults.refused_hosts.add(net.host)
    ctrl = _controller(_config(tmp_path, auto_recovery=False))
    errors = []
    ctrl.error.connect(errors.append)
    ctrl._start_capture_worker(read_capture_params(ctrl.config, False))
    assert errors and errors[0] != "Failed to connect to camera"
    assert "192.0.2.10:11111" in errors[0] and "ClientID" not in errors[0]


def test_unrecoverable_patterns_are_ignored_for_alpaca(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path))
    ctrl._start_usb_reset_worker = MagicMock()
    ctrl._on_capture_start_done(False, ACCESS_VIOLATION)
    ctrl._start_usb_reset_worker.assert_not_called()
    assert ctrl._unrecoverable_mode is False
    assert ctrl._auto_recovery_timer is not None
    ctrl._cancel_auto_recovery_timer()


def test_zwo_still_resets_usb_on_an_unrecoverable_start_error(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path, source="zwo"))
    ctrl._start_usb_reset_worker = MagicMock()
    ctrl._on_capture_start_done(False, ACCESS_VIOLATION)
    ctrl._start_usb_reset_worker.assert_called_once()


@pytest.mark.parametrize("source,auto_recovery,retries", [
    ("alpaca", True, True),
    ("alpaca", False, False),
    ("zwo", True, False),
])
def test_start_failure_before_the_first_frame(qt_app, tmp_path, source, auto_recovery, retries):
    ctrl = _controller(_config(tmp_path, source=source, auto_recovery=auto_recovery))
    assert ctrl._last_successful_frame_ts == 0
    ctrl._on_capture_start_done(False, "Connection refused by 192.0.2.10:11111")
    assert (ctrl._auto_recovery_timer is not None) is retries
    assert ctrl._auto_recovery_attempts == (1 if retries else 0)
    ctrl._cancel_auto_recovery_timer()


def test_repeated_alpaca_start_failures_climb_the_ladder(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path))
    for _ in range(3):
        ctrl._on_capture_start_done(False, "No answer from 192.0.2.10:11111 within 3 s")
    assert ctrl._auto_recovery_attempts == 3
    assert ctrl._auto_recovery_timer.interval() == ctrl._auto_recovery_schedule[2] * 1000
    ctrl._cancel_auto_recovery_timer()


def _wedged(backend):
    dying = MagicMock()
    dying.backend = backend
    dying.wait_for_capture_thread_exit = MagicMock(return_value=False)
    return dying


def test_a_wedged_alpaca_thread_reschedules_then_escalates_without_usb(qt_app, tmp_path):
    from ui.controllers.camera_controller import _MAX_WEDGED_SKIPS
    ctrl = _controller(_config(tmp_path))
    ctrl._dying_camera = _wedged("alpaca")
    ctrl.start_capture = MagicMock()
    ctrl._start_usb_reset_worker = MagicMock()
    ctrl._schedule_auto_recovery = MagicMock()
    ctrl._escalate_to_restart_or_alert = MagicMock()
    for _ in range(_MAX_WEDGED_SKIPS):
        ctrl._on_auto_recovery_fire()
    ctrl._start_usb_reset_worker.assert_not_called()
    ctrl.start_capture.assert_not_called()
    assert ctrl._schedule_auto_recovery.call_count == _MAX_WEDGED_SKIPS - 1
    reason = ctrl._escalate_to_restart_or_alert.call_args.args[0]
    assert "Alpaca" in reason and "ZWO" not in reason


def test_a_wedged_zwo_thread_still_tries_usb_first(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path, source="zwo"))
    ctrl._dying_camera = _wedged("zwo")
    ctrl.start_capture = MagicMock()
    ctrl._start_usb_reset_worker = MagicMock()
    ctrl._on_auto_recovery_fire()
    ctrl._start_usb_reset_worker.assert_called_once()


def test_usb_reset_worker_and_revive_do_nothing_for_alpaca(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path))
    ctrl._start_usb_reset_worker()
    revived = []
    ctrl.camera_revive_done.connect(lambda ok, name: revived.append((ok, name)))
    ctrl.revive_missing_camera("PFR Alpaca Simulator")
    ctrl._usb_reset.run_async.assert_not_called()
    assert revived == [(False, "PFR Alpaca Simulator")]


def test_unrecoverable_text_names_no_zwo_sdk_for_alpaca(qt_app, tmp_path):
    messages = {}
    for source in ("alpaca", "zwo"):
        ctrl = _controller(_config(tmp_path, source=source))
        seen = []
        ctrl.error.connect(seen.append)
        ctrl._enter_unrecoverable_mode("boom")
        messages[source] = seen[0]
    assert "ZWO" not in messages["alpaca"] and "restart" in messages["alpaca"]
    assert messages["zwo"] == ("Camera unrecoverable — ZWO SDK state is corrupted. "
                               "Please restart the application. (last error: boom)")


def test_a_wedged_zwo_session_is_reset_even_after_switching_to_alpaca(qt_app, tmp_path):
    """Recovery follows the stuck session's backend; config was switched since."""
    ctrl = _controller(_config(tmp_path, source="alpaca"))
    ctrl._dying_camera = _wedged("zwo")
    ctrl.start_capture = MagicMock()
    ctrl._on_auto_recovery_fire()
    ctrl._usb_reset.run_async.assert_called_once()
    ctrl.start_capture.assert_not_called()


def test_a_skipped_usb_reset_keeps_recovery_alive(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path, source="zwo"))
    ctrl._start_usb_reset_worker(alpaca=True)
    ctrl._usb_reset.run_async.assert_not_called()
    assert ctrl._auto_recovery_timer is not None, "skipping must reschedule"
    ctrl._cancel_auto_recovery_timer()


def test_start_failure_follows_the_backend_that_started(qt_app, tmp_path):
    from services.camera.session_params import read_capture_params
    config = _config(tmp_path, source="zwo")
    ctrl = _controller(config)
    ctrl._starting_source = read_capture_params(config, True).get('camera_source')
    config.set("camera_source", "alpaca")
    ctrl._start_usb_reset_worker = MagicMock()
    ctrl._on_capture_start_done(False, ACCESS_VIOLATION)
    ctrl._start_usb_reset_worker.assert_called_once_with(alpaca=False)


def test_unrecoverable_text_follows_the_stuck_session(qt_app, tmp_path):
    ctrl = _controller(_config(tmp_path, source="alpaca"))
    ctrl._dying_camera = _wedged("zwo")
    seen = []
    ctrl.error.connect(seen.append)
    ctrl._enter_unrecoverable_mode("boom")
    assert seen[0] == ("Camera unrecoverable — ZWO SDK state is corrupted. "
                       "Please restart the application. (last error: boom)")
