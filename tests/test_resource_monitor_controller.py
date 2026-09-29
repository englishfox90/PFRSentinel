"""Tests for ui.controllers.resource_monitor_controller (offscreen Qt).

A stand-in main window carries only the attributes the gauges read; the
controller must report what is there, skip what is not, never raise, and
route the monitor's line / warning to the logger. The timer never fires in
these tests — cadence is the monitor's business and is tested there.
"""
import queue

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

import ui.controllers.resource_monitor_controller as rmc
from services.meteor.frame_stack import FrameStack
from services.resource_monitor import ResourceMonitor, ResourceReport, ResourceSample
from ui.controllers.resource_monitor_controller import (
    ResourceMonitorController, buffer_mb, pil_image_mb)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Config:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)


class _CalService:
    frame_count = 42
    _ring = [object()] * 7


class _Pump:
    queued = 1


class _Writer:
    _pump = _Pump()


class _Obj:
    pass


class _MainWindow(QObject):
    def __init__(self, config=None):
        super().__init__()
        self.config = config or _Config()
        self.allsky_controller = _Obj()
        self.allsky_controller._cal_service = _CalService()
        self.meteor_controller = _Obj()
        stack = FrameStack(maxlen=3)
        for _ in range(2):
            stack.push(np.zeros((100, 200), dtype=np.uint8))
        self.meteor_controller._stack = stack
        self.image_processor = _Obj()
        self.image_processor._queue = queue.Queue(maxsize=10)
        self.image_processor._queue.put('frame')
        self.image_processor._overlay_image_cache = {'logo': object()}
        self.timelapse_controller = _Obj()
        self.timelapse_controller._writer = _Writer()
        self._cached_raw_image = Image.new('RGB', (100, 50))
        self._cached_raw_metadata = {'RAW_BAYER': np.zeros(1024 * 1024, dtype=np.uint8)}


@pytest.fixture
def window(qapp):
    mw = _MainWindow()
    yield mw
    mw.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def logged(monkeypatch):
    lines = {'info': [], 'warning': [], 'debug': []}
    for level in lines:
        monkeypatch.setattr(rmc.app_logger, level,
                            lambda msg, _l=level: lines[_l].append(msg))
    return lines


def test_gauges_read_every_subsystem(window, monkeypatch):
    from services.web_output import ImageHTTPHandler
    monkeypatch.setattr(ImageHTTPHandler, 'latest_image_snapshot',
                        (b'x' * (2 * 1024 * 1024), 'image/jpeg', 'etag', 0.0))
    ctrl = ResourceMonitorController(window)
    g = ctrl.gauges()
    assert g['allsky_buffer'] == 42
    assert g['allsky_ring'] == 7
    assert g['meteor_stack'] == 2
    # two uint8 frames + one float64 running sum
    assert g['meteor_stack_mb'] == pytest.approx((2 * 20000 + 8 * 20000) / 1048576)
    assert g['processor_queue'] == 1
    assert g['overlay_cache'] == 1
    assert g['timelapse_queue'] == 1
    assert g['cached_frame_mb'] == pytest.approx(100 * 50 * 4 / 1048576 + 1.0)
    assert g['web_latest_mb'] == pytest.approx(2.0)


def test_missing_subsystems_are_left_out_not_raised(qapp, monkeypatch):
    from services.web_output import ImageHTTPHandler
    monkeypatch.setattr(ImageHTTPHandler, 'latest_image_snapshot', None)
    bare = QObject()
    bare.config = _Config()
    bare.meteor_controller = None
    try:
        ctrl = ResourceMonitorController(bare)
        assert ctrl.gauges() == {}
    finally:
        bare.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_a_reader_that_raises_is_skipped(window):
    class _Broken:
        @property
        def frame_count(self):
            raise RuntimeError("boom")
    window.allsky_controller._cal_service = _Broken()
    g = ResourceMonitorController(window).gauges()
    assert 'allsky_buffer' not in g
    assert g['meteor_stack'] == 2


def test_mark_logs_line_and_warning(window, logged):
    class _Mon(ResourceMonitor):
        def sample(self, gauges=None, *, reason=None, new_regime=False):
            s = ResourceSample('t', 0.0, 1.0, 2.0, 3.0, 0, None, 1, dict(gauges or {}), reason)
            return ResourceReport(s, line=f"LINE {reason} {new_regime}", warning="WARN")
    ctrl = ResourceMonitorController(window, monitor=_Mon())
    ctrl.mark('capture started', new_regime=True)
    assert logged['info'] == ["LINE capture started True"]
    assert logged['warning'] == ["WARN"]


def test_quiet_sample_logs_nothing(window, logged):
    ctrl = ResourceMonitorController(window)
    ctrl._report()                      # first sample: a line
    ctrl._report()                      # nothing moved: no line
    assert len(logged['info']) == 1
    assert logged['warning'] == []


def test_sampling_failure_is_debug_only(window, logged):
    class _Mon(ResourceMonitor):
        def sample(self, *a, **k):
            raise RuntimeError("counters gone")
    ResourceMonitorController(window, monitor=_Mon())._report()
    assert logged['info'] == [] and logged['warning'] == []
    assert logged['debug'] and "counters gone" in logged['debug'][0]


def test_interval_and_trace_follow_config(window, logged, monkeypatch):
    import services.dev_mode_config as dmc
    monkeypatch.setattr(dmc, 'is_dev_mode_available', lambda: True)
    window.config = _Config({'diagnostics': {'resource_log_interval_s': 42,
                                             'memory_trace': True}})
    ctrl = ResourceMonitorController(window)
    assert ctrl._timer.interval() == 42_000
    assert ctrl._trace is not None
    ctrl.start()
    try:
        assert ctrl._timer.isActive()
        assert any(l.startswith("Memory trace on") for l in logged['info'])
        assert any(l.startswith("Resources [startup]") for l in logged['info'])
        assert any(l.startswith("Python heap (tracemalloc)") for l in logged['info'])
    finally:
        ctrl.stop()
    assert not ctrl._timer.isActive()
    assert not ctrl._trace.active


def test_default_interval_without_config_section(window):
    ctrl = ResourceMonitorController(window)
    assert ctrl._timer.interval() == 300_000
    assert ctrl._trace is None


def test_snapshot_round_trips_through_the_monitor(window):
    ctrl = ResourceMonitorController(window)
    assert ctrl.snapshot() == {}
    ctrl.mark('startup')
    snap = ctrl.snapshot()
    assert snap['reason'] == 'startup'
    assert snap['gauges']['allsky_buffer'] == 42


def test_memory_trace_is_refused_in_production_builds(window, logged, monkeypatch):
    import services.dev_mode_config as dmc
    monkeypatch.setattr(dmc, 'is_dev_mode_available', lambda: False)
    window.config = _Config({'diagnostics': {'memory_trace': True}})
    ctrl = ResourceMonitorController(window)
    assert ctrl._trace is None
    ctrl.start()
    try:
        assert any("only dev builds honour it" in l for l in logged['info'])
        assert any(l.startswith("Resources [startup]") for l in logged['info'])
        assert not any(l.startswith("Python heap") for l in logged['info'])
    finally:
        ctrl.stop()


@pytest.mark.parametrize("dev_build", [True, False])
def test_window_starts_the_monitor_in_every_build(qapp, monkeypatch, dev_build):
    import services.dev_mode_config as dmc
    from ui.main_window.lifecycle import _MainWindowLifecycleMixin
    monkeypatch.setattr(dmc, 'is_dev_mode_available', lambda: dev_build)

    class _Window(QObject, _MainWindowLifecycleMixin):
        def __init__(self):
            super().__init__()
            self.config = _Config()
            self.meteor_controller = None

    win = _Window()
    try:
        win._start_resource_monitor()
        assert win.resource_monitor is not None
        win._mark_resources('capture started', new_regime=True)
        assert win.resource_monitor.snapshot()['reason'] == 'capture started'
        win.resource_monitor.stop()
    finally:
        win.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_size_helpers():
    assert pil_image_mb(None) == 0.0
    assert pil_image_mb(Image.new('L', (1024, 1024))) == pytest.approx(1.0)
    assert pil_image_mb(Image.new('RGB', (1024, 1024))) == pytest.approx(4.0)
    assert pil_image_mb(Image.new('I;16', (1024, 1024))) == pytest.approx(2.0)
    assert buffer_mb(b'x' * 1048576) == pytest.approx(1.0)
    assert buffer_mb(np.zeros(1048576, dtype=np.uint8)) == pytest.approx(1.0)
    assert buffer_mb(None) == 0.0
    assert buffer_mb("not a buffer") == 0.0
