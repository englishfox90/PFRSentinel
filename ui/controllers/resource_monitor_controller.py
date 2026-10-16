"""
Feeds ``services.resource_monitor`` from the live app on a GUI-thread timer.

Owns the cadence (a ``QTimer``), reads the per-subsystem gauges — how many
frames the all-sky calibration buffer and ring hold, the meteor stack's
size, the processor and timelapse queue depths, the reprocess cache and the
web server's latest encoded frame — and logs what the monitor decides is
worth a line. ``mark()`` forces a line at capture start/stop and after a
working-set trim, which is how the log shows the trim lowering the working
set while private bytes stay put.

Gauges are read on the GUI thread only. A few reach into another object's
private state (``_ring``, ``_stack``, ``_queue``, ``_pump``): those owners
sit at the file-size cap, and a read-only diagnostic does not justify a
public accessor on each. Every gauge is guarded — a missing attribute means
"not reported", never an exception.
"""
from PySide6.QtCore import QObject, QTimer

from services.logger import app_logger
from services.resource_monitor import (
    DEFAULT_SAMPLE_INTERVAL_S, MemoryTrace, ResourceMonitor)

_MB = 1024 * 1024

# Pillow's internal storage, not the on-disk size: RGB is 4 bytes/px.
_PIL_BYTES_PER_PX = {
    '1': 1, 'L': 1, 'P': 1, 'LA': 4, 'PA': 4,
    'I;16': 2, 'I;16L': 2, 'I;16B': 2, 'I;16N': 2,
    'I': 4, 'F': 4, 'RGB': 4, 'RGBA': 4, 'RGBX': 4, 'CMYK': 4, 'YCbCr': 4,
}


def pil_image_mb(image) -> float:
    """Approximate resident size of a PIL image in MB (0 for None)."""
    if image is None:
        return 0.0
    per_px = _PIL_BYTES_PER_PX.get(getattr(image, 'mode', ''), 4)
    return image.width * image.height * per_px / _MB


def buffer_mb(obj) -> float:
    """Size of a bytes / bytearray / numpy buffer in MB (0 for anything else)."""
    nbytes = getattr(obj, 'nbytes', None)
    if nbytes is None and isinstance(obj, (bytes, bytearray, memoryview)):
        nbytes = len(obj)
    return (nbytes or 0) / _MB


class ResourceMonitorController(QObject):

    def __init__(self, main_window, *, interval_s=None, monitor=None, trace=None):
        super().__init__(main_window)
        self._mw = main_window
        cfg = {}
        try:
            cfg = main_window.config.get('diagnostics', {}) or {}
        except Exception:
            pass
        self._interval_s = float(interval_s or cfg.get('resource_log_interval_s')
                                 or DEFAULT_SAMPLE_INTERVAL_S)
        self._monitor = monitor or ResourceMonitor()
        self._trace = trace
        self._trace_refused = False
        if self._trace is None and cfg.get('memory_trace'):
            # tracemalloc taxes every allocation: a dev-build tool, never
            # something a production user can switch on by editing config.json.
            from services.dev_mode_config import is_dev_mode_available
            if is_dev_mode_available():
                self._trace = MemoryTrace()
            else:
                self._trace_refused = True
        self._timer = QTimer(self)
        self._timer.setInterval(int(self._interval_s * 1000))
        self._timer.timeout.connect(self._tick)

    @property
    def monitor(self) -> ResourceMonitor:
        return self._monitor

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._trace_refused:
            app_logger.info("diagnostics.memory_trace is set but only dev builds "
                            "honour it — resource lines continue without hotspots")
        if self._trace is not None:
            if self._trace.start():
                app_logger.info("Memory trace on (diagnostics.memory_trace): Python "
                                "allocation hotspots follow each resource line")
            else:
                app_logger.warning("Memory trace requested but tracemalloc could not start")
        self._timer.start()
        self.mark('startup')

    def stop(self) -> None:
        self._timer.stop()
        if self._trace is not None:
            self._trace.stop()

    def mark(self, reason: str, *, new_regime: bool = False) -> None:
        """Force a line now. ``new_regime`` restarts the growth window (capture
        start/stop moves the baseline and must not read as a leak)."""
        self._report(reason=reason, new_regime=new_regime)

    def snapshot(self) -> dict:
        """Latest sample as plain data, for the diagnostics bundle. Thread-safe."""
        return self._monitor.snapshot()

    # ------------------------------------------------------------------ #
    #  Sampling                                                            #
    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        self._report()

    def _report(self, reason=None, new_regime=False) -> None:
        try:
            report = self._monitor.sample(self.gauges(), reason=reason,
                                          new_regime=new_regime)
        except Exception as e:
            app_logger.debug(f"Resource sample failed: {e}")
            return
        if report.line:
            app_logger.info(report.line)
            if self._trace is not None:
                for line in self._trace.report():
                    app_logger.info(line)
        if report.warning:
            app_logger.warning(report.warning)

    def gauges(self) -> dict:
        out = {}
        for name, read in (
            ('allsky_buffer', self._allsky_buffer),
            ('allsky_ring', self._allsky_ring),
            ('meteor_stack', self._meteor_stack),
            ('meteor_stack_mb', self._meteor_stack_mb),
            ('processor_queue', self._processor_queue),
            ('overlay_cache', self._overlay_cache),
            ('timelapse_queue', self._timelapse_queue),
            ('cached_frame_mb', self._cached_frame_mb),
            ('web_latest_mb', self._web_latest_mb),
        ):
            try:
                value = read()
            except Exception:
                value = None
            if value is not None:
                out[name] = value
        return out

    # Each reader returns None when its subsystem is absent.

    def _cal_service(self):
        return getattr(getattr(self._mw, 'allsky_controller', None), '_cal_service', None)

    def _allsky_buffer(self):
        svc = self._cal_service()
        return None if svc is None else int(svc.frame_count)

    def _allsky_ring(self):
        ring = getattr(self._cal_service(), '_ring', None)
        return None if ring is None else len(ring)

    def _stack(self):
        return getattr(getattr(self._mw, 'meteor_controller', None), '_stack', None)

    def _meteor_stack(self):
        stack = self._stack()
        return None if stack is None else int(stack.count)

    def _meteor_stack_mb(self):
        stack = self._stack()
        return None if stack is None else stack.nbytes / _MB

    def _processor_queue(self):
        q = getattr(getattr(self._mw, 'image_processor', None), '_queue', None)
        return None if q is None else int(q.qsize())

    def _overlay_cache(self):
        cache = getattr(getattr(self._mw, 'image_processor', None),
                        '_overlay_image_cache', None)
        return None if cache is None else len(cache)

    def _timelapse_queue(self):
        writer = getattr(getattr(self._mw, 'timelapse_controller', None), '_writer', None)
        pump = getattr(writer, '_pump', None)
        return None if pump is None else int(pump.queued)

    def _cached_frame_mb(self):
        image = getattr(self._mw, '_cached_raw_image', None)
        meta = getattr(self._mw, '_cached_raw_metadata', None) or {}
        total = pil_image_mb(image) + buffer_mb(meta.get('RAW_BAYER'))
        return total if (image is not None or meta) else None

    @staticmethod
    def _web_latest_mb():
        from services.web_output import ImageHTTPHandler
        snap = ImageHTTPHandler.latest_image_snapshot
        return None if not snap else buffer_mb(snap[0])
