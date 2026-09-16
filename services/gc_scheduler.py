"""Cyclic garbage collection driven from the Qt GUI thread.

CPython's automatic cyclic collector runs on whichever thread happens to trip
the allocation threshold, and it finalizes everything it finds there — not just
what that thread allocated. In a Qt process that means a background worker (the
all-sky refine worker allocating a numpy scalar, say) can finalize a cyclic
shiboken wrapper for a Python-owned Qt object and run that object's C++
destructor on a thread that does not own it. Deleting a Qt object from the
wrong thread is undefined behaviour; it surfaces as an access violation,
typically while the GUI thread is inside a Qt C++ call with the GIL released
(issue #31).

The mitigation is calibre's: switch automatic collection off and drive it from
a QTimer owned by the GUI thread, so every cyclic finalization happens on the
thread where Qt objects are safe to destroy.
"""
import gc
import threading

from PySide6.QtCore import QCoreApplication, QThread, QTimer

from .logger import app_logger

# One second keeps each gen-0 pass small enough to be invisible between frames
# while still bounding how long cyclic garbage can pile up; the 1/10/100 ratio
# is calibre's, so a full gen-2 sweep lands roughly every 100 s.
DEFAULT_INTERVAL_MS = 1000
GEN1_EVERY = 10
GEN2_EVERY = 100

# Debug-log threshold. This box runs unattended for months — logging every
# tick would bury the log in noise for no diagnostic gain.
NOTABLE_FREED = 500


class GcScheduler:
    """Owns the GUI-thread collection timer and the automatic-GC override."""

    def __init__(self, interval_ms: int = DEFAULT_INTERVAL_MS):
        self._interval_ms = int(interval_ms)
        self._timer = None
        self._ticks = 0
        self._full_requested = threading.Event()
        self._was_enabled = None

    @property
    def installed(self) -> bool:
        return self._timer is not None

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def install(self, interval_ms: int = None) -> bool:
        """Take over cyclic collection.

        Must be called on the GUI thread, after the QApplication exists — the
        timer collects on whichever thread creates it, which is the whole point.
        Returns False and leaves automatic GC untouched when that is not
        possible: running with collection disabled and nothing driving it would
        leak every cycle the process makes.
        """
        if self._timer is not None:
            return True
        if interval_ms is not None:
            self._interval_ms = int(interval_ms)

        app = QCoreApplication.instance()
        if app is None:
            app_logger.warning(
                "GC scheduler not installed: no QApplication — automatic GC left enabled"
            )
            return False
        if app.thread() is not QThread.currentThread():
            app_logger.warning(
                "GC scheduler not installed: install() must run on the GUI thread — "
                "automatic GC left enabled"
            )
            return False

        try:
            timer = QTimer()
            timer.setInterval(self._interval_ms)
            timer.timeout.connect(self._tick)
            timer.start()
        except Exception as e:
            app_logger.warning(
                f"GC scheduler not installed ({e}) — automatic GC left enabled"
            )
            return False

        self._timer = timer
        self._ticks = 0
        self._full_requested.clear()
        self._was_enabled = gc.isenabled()
        gc.disable()
        app_logger.info(
            f"GC scheduler installed: collecting on the GUI thread every {self._interval_ms} ms"
        )
        return True

    def uninstall(self) -> None:
        """Stop the timer and hand cyclic collection back to the interpreter."""
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.stop()
                timer.timeout.disconnect(self._tick)
            except (RuntimeError, TypeError):
                pass
            app_logger.debug("GC scheduler uninstalled")

        if self._was_enabled is None or self._was_enabled:
            gc.enable()
        self._was_enabled = None
        self._full_requested.clear()

    def request_full_collect(self) -> None:
        """Ask for a full ``gc.collect()`` on the next tick. Safe from any thread.

        When the scheduler is not installed this only arms the flag: automatic
        collection is still on in that case, so nothing needs forcing, and
        collecting inline would reintroduce the wrong-thread finalization this
        module exists to prevent.
        """
        self._full_requested.set()

    def _tick(self) -> None:
        self._ticks += 1

        if self._full_requested.is_set():
            self._full_requested.clear()
            freed = gc.collect()
            app_logger.debug(f"GC (full, requested): {freed} objects")
            return

        if self._ticks % GEN2_EVERY == 0:
            generation = 2
        elif self._ticks % GEN1_EVERY == 0:
            generation = 1
        else:
            generation = 0

        freed = gc.collect(generation)
        if freed >= NOTABLE_FREED:
            app_logger.debug(f"GC (gen {generation}): {freed} objects")


_scheduler = GcScheduler()


def get_scheduler() -> GcScheduler:
    return _scheduler


def install(interval_ms: int = DEFAULT_INTERVAL_MS) -> bool:
    return _scheduler.install(interval_ms=interval_ms)


def uninstall() -> None:
    _scheduler.uninstall()


def request_full_collect() -> None:
    _scheduler.request_full_collect()


def is_installed() -> bool:
    return _scheduler.installed
