"""AlpacaDiscoveryController: worker threads, GUI-thread delivery, re-entrancy (offscreen Qt)."""
import threading
import time
from dataclasses import dataclass, field

import pytest

from services.camera.alpaca.alpaca_discovery import AlpacaDeviceInfo


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@dataclass(frozen=True)
class _Probe:
    ok: bool
    message: str
    details: dict = field(default_factory=dict)


def _wait(qapp, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    return predicate()


def _controller(**kw):
    from ui.controllers.alpaca_discovery_controller import AlpacaDiscoveryController
    return AlpacaDiscoveryController(**kw)


def test_discovery_result_arrives_on_the_gui_thread(qapp):
    device = AlpacaDeviceInfo('10.0.0.2', 11111, 0, 'Sim', 'uid')
    worker_threads = []

    def discover(timeout):
        worker_threads.append(threading.current_thread())
        return [device]

    ctrl = _controller(discover_fn=discover)
    got = []
    ctrl.devices_found.connect(lambda d, e: got.append((d, e, threading.current_thread())))
    assert ctrl.discover() is True
    assert _wait(qapp, lambda: got)
    devices, error, thread = got[0]
    assert devices == [device] and error == ""
    assert thread is threading.main_thread()
    assert worker_threads and worker_threads[0] is not threading.main_thread()
    assert ctrl.discovering is False


def test_discovery_failure_reports_the_error(qapp):
    def discover(timeout):
        raise OSError("network unreachable")

    ctrl = _controller(discover_fn=discover)
    got = []
    ctrl.devices_found.connect(lambda d, e: got.append((d, e)))
    ctrl.discover()
    assert _wait(qapp, lambda: got)
    assert got[0] == ([], "network unreachable")


def test_a_second_discovery_while_one_runs_is_ignored(qapp):
    release = threading.Event()
    calls = []

    def discover(timeout):
        calls.append(timeout)
        release.wait(5)
        return []

    ctrl = _controller(discover_fn=discover, timeout=1.5)
    got = []
    ctrl.devices_found.connect(lambda d, e: got.append(e))
    assert ctrl.discover() is True
    assert ctrl.discover() is False
    release.set()
    assert _wait(qapp, lambda: got)
    assert calls == [1.5]
    assert ctrl.discover() is True, "free again once the result is delivered"
    assert _wait(qapp, lambda: len(got) == 2)


def test_test_connection_passes_settings_and_delivers_details(qapp):
    seen = []

    def probe(settings):
        seen.append(settings)
        return _Probe(True, "Connected", {'name': 'Sim', 'gain_range': (0, 100)})

    ctrl = _controller(probe_fn=probe)
    got = []
    ctrl.test_result.connect(lambda ok, msg, det: got.append((ok, msg, det, threading.current_thread())))
    settings = {'host': '10.0.0.2', 'port': 11111, 'device_number': 0}
    assert ctrl.test_connection(settings) is True
    assert ctrl.test_connection(settings) is False
    assert _wait(qapp, lambda: got)
    ok, msg, details, thread = got[0]
    assert ok is True and msg == "Connected"
    assert details['name'] == 'Sim' and tuple(details['gain_range']) == (0, 100)
    assert thread is threading.main_thread()
    assert seen == [settings]
    assert ctrl.testing is False


def test_probe_that_raises_is_a_failed_test(qapp):
    def probe(settings):
        raise RuntimeError("boom")

    ctrl = _controller(probe_fn=probe)
    got = []
    ctrl.test_result.connect(lambda ok, msg, det: got.append((ok, msg, det)))
    ctrl.test_connection({'host': 'x'})
    assert _wait(qapp, lambda: got)
    assert got[0] == (False, "boom", {})


def test_missing_probe_module_is_a_failed_test(qapp):
    def probe(settings):
        raise ImportError("no module")

    ctrl = _controller(probe_fn=probe)
    got = []
    ctrl.test_result.connect(lambda ok, msg, det: got.append(ok))
    ctrl.test_connection({'host': 'x'})
    assert _wait(qapp, lambda: got)
    assert got == [False]


def test_capabilities_from_probe():
    from ui.controllers.alpaca_discovery_controller import capabilities_from_probe
    caps = capabilities_from_probe({'bayer_pattern': 'BGGR', 'bit_depth': 12,
                                    'gain_range': (0, 300), 'offset_range': None,
                                    'exposure_range_s': (0.0001, 1800.0)})
    assert caps.backend == 'alpaca'
    assert caps.bayer_pattern == 'BGGR' and caps.bit_depth == 12
    assert caps.gain_range == (0, 300) and caps.gain is True
    assert caps.offset is False, "the probe read the camera and found no offset"
    assert caps.exposure_range_s == (0.0001, 1800.0)
    assert capabilities_from_probe({}).offset is True
