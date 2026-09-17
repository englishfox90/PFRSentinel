"""GUI-thread garbage collection (services/gc_scheduler.py).

The production fault pinned here: with automatic GC on, a cyclic shiboken
wrapper can be finalized by whichever thread happens to trip the allocation
threshold. Running a Qt object's C++ destructor off its owning thread is
undefined behaviour and crashed the observatory box overnight (issue #31).
These tests assert the collection actually moves to the GUI thread, and that
the module never leaves the interpreter with collection disabled and nothing
driving it.
"""
import gc
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from services import gc_scheduler


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def scheduler(qapp):
    """A scheduler with a short tick; always uninstalled so other test modules
    never inherit a disabled collector."""
    sched = gc_scheduler.GcScheduler(interval_ms=10)
    try:
        yield sched
    finally:
        sched.uninstall()
        assert gc.isenabled()


class CycleMarker:
    """Self-referential object that records the thread that finalized it."""

    def __init__(self, sink):
        self.sink = sink
        self.self_ref = self

    def __del__(self):
        self.sink.append(threading.current_thread())


def _spin(ms=400):
    """Run the GUI event loop for a bounded time so QTimer ticks are delivered."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _make_cycle(sink):
    marker = CycleMarker(sink)
    del marker


def _make_cycle_on_worker(sink):
    worker = threading.Thread(target=_make_cycle, args=(sink,), name="gc-test-worker")
    worker.start()
    worker.join()
    return worker


def test_install_disables_automatic_gc(scheduler):
    assert gc.isenabled()

    assert scheduler.install() is True

    assert scheduler.installed
    assert not gc.isenabled()


def test_uninstall_restores_previous_state(scheduler):
    scheduler.install()
    scheduler.uninstall()

    assert gc.isenabled()
    assert not scheduler.installed


def test_uninstall_leaves_gc_disabled_when_it_was_disabled_before(scheduler):
    gc.disable()
    try:
        scheduler.install()
        scheduler.uninstall()
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_install_is_idempotent(scheduler):
    assert scheduler.install() is True
    assert scheduler.install() is True
    assert not gc.isenabled()


def test_cycle_from_worker_thread_is_finalized_on_the_gui_thread(scheduler):
    scheduler.install()
    gc.collect()

    finalized_on = []
    worker = _make_cycle_on_worker(finalized_on)

    assert finalized_on == [], "cycle must not be collected on the worker thread"

    _spin()

    assert finalized_on, "cycle was never collected by the scheduler tick"
    assert finalized_on[0] is threading.main_thread()
    assert finalized_on[0] is not worker


def test_request_full_collect_from_worker_thread_collects_on_next_tick(scheduler):
    scheduler.install()
    gc.collect()

    collected = []

    def _collect(generation=2):
        collected.append(generation)
        return 0

    original = gc.collect
    gc.collect = _collect
    try:
        requester = threading.Thread(target=scheduler.request_full_collect)
        requester.start()
        requester.join()

        _spin(100)
    finally:
        gc.collect = original

    assert collected, "no collection ran after request_full_collect()"
    assert collected[0] == 2, "the requested collection must be a full one"


def test_generation_schedule_escalates_to_gen1_and_gen2(scheduler):
    scheduler.install()

    generations = []
    original = gc.collect
    gc.collect = lambda generation=2: generations.append(generation) or 0
    try:
        for _ in range(gc_scheduler.GEN2_EVERY):
            scheduler._tick()
    finally:
        gc.collect = original

    assert generations[0] == 0
    assert generations[gc_scheduler.GEN1_EVERY - 1] == 1
    assert generations[gc_scheduler.GEN2_EVERY - 1] == 2


def test_install_without_timer_leaves_gc_enabled(scheduler, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("no timer available")

    monkeypatch.setattr(gc_scheduler, "QTimer", _boom)

    assert scheduler.install() is False
    assert gc.isenabled(), "a failed install must never disable automatic GC"
    assert not scheduler.installed


def test_install_without_qapplication_leaves_gc_enabled(scheduler, monkeypatch):
    monkeypatch.setattr(QCoreApplication, "instance", staticmethod(lambda: None))

    assert scheduler.install() is False
    assert gc.isenabled()


def test_request_full_collect_while_uninstalled_does_not_collect_inline(scheduler):
    collected = []
    original = gc.collect
    gc.collect = lambda generation=2: collected.append(generation) or 0
    try:
        scheduler.request_full_collect()
    finally:
        gc.collect = original

    assert collected == []


def test_module_level_helpers_share_one_scheduler(qapp):
    try:
        assert gc_scheduler.install(interval_ms=10) is True
        assert gc_scheduler.is_installed()
        assert gc_scheduler.get_scheduler().interval_ms == 10
        assert not gc.isenabled()
    finally:
        gc_scheduler.uninstall()

    assert not gc_scheduler.is_installed()
    assert gc.isenabled()
