"""
Periodic process resource report: memory, CPU and what the app is holding.

PFR Sentinel runs unattended for months and the operator's only health
check is Task Manager, which shows one number — the private working set —
and says nothing about whether it is real retention or pages Windows simply
hasn't reclaimed. This module turns a periodic sample into a log line that
distinguishes the two and names the subsystems holding frames, so a
"memory keeps climbing" report can be read from the log instead of guessed
at from a screenshot.

Pure and clock-injectable: nothing here touches Qt, and the caller decides
the cadence and supplies the per-subsystem gauges (``ResourceMonitorController``
does both). ``sample()`` returns a ``ResourceReport``; the caller logs
``report.line`` when it is not None.

Two numbers matter, and they diverge on purpose:

* ``working_set_mb`` is Task Manager's column. ``working_set.trim_working_set``
  lowers it without freeing a byte, and Windows never trims it on its own
  while there is no memory pressure — so between trims it sits near the
  high-water mark of everything a frame touched.
* ``private_mb`` is the process's committed private bytes. A trim cannot
  touch it, so a private figure that rises sample after sample is retention;
  a high working set over a flat private figure is cosmetic.

Lines are rate-limited: one goes out when private bytes move by
``step_mb`` since the last line, on a heartbeat otherwise, and always when
the caller passes a ``reason`` (capture start/stop, a trim). A sustained rise
of ``growth_warn_mb`` within ``growth_window_s`` raises a WARNING once, and
re-arms once the window's baseline catches up — so a genuine leak warns
about once an hour and a one-off step warns once.
"""
import os
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .performance import get_process_memory

DEFAULT_SAMPLE_INTERVAL_S = 300      # the controller's timer
DEFAULT_HEARTBEAT_S = 1800           # a line even when nothing moved
DEFAULT_STEP_MB = 64                 # private-bytes change that earns a line
DEFAULT_GROWTH_WARN_MB = 256         # sustained rise that earns a WARNING
DEFAULT_GROWTH_WINDOW_S = 3600

_MB = 1024 * 1024


@dataclass
class ResourceSample:
    taken_at: str
    monotonic: float
    working_set_mb: float
    peak_working_set_mb: float
    private_mb: float
    page_faults: int
    cpu_percent: Optional[float]     # of the whole machine, since the previous sample
    threads: int
    gauges: Dict[str, object]
    reason: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResourceReport:
    sample: ResourceSample
    line: Optional[str]              # INFO line, or None when the sample earns no line
    warning: Optional[str]           # WARNING about sustained growth, or None


class ResourceMonitor:
    """Sampling policy and formatting; owns no timer and no Qt object."""

    def __init__(self, *, heartbeat_s: float = DEFAULT_HEARTBEAT_S,
                 step_mb: float = DEFAULT_STEP_MB,
                 growth_warn_mb: float = DEFAULT_GROWTH_WARN_MB,
                 growth_window_s: float = DEFAULT_GROWTH_WINDOW_S,
                 read_memory: Callable[[], Optional[dict]] = get_process_memory,
                 process_time: Callable[[], float] = time.process_time,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = datetime.now,
                 cpu_count: Optional[int] = None,
                 thread_count: Callable[[], int] = threading.active_count):
        self._heartbeat_s = float(heartbeat_s)
        self._step_mb = float(step_mb)
        self._growth_warn_mb = float(growth_warn_mb)
        self._growth_window_s = float(growth_window_s)
        self._read_memory = read_memory
        self._process_time = process_time
        self._clock = clock
        self._now = now
        self._cpu_count = int(cpu_count or os.cpu_count() or 1)
        self._thread_count = thread_count

        self._lock = threading.Lock()
        self._last: Optional[ResourceSample] = None
        self._last_logged: Optional[ResourceSample] = None
        self._prev_cpu: Optional[tuple] = None          # (process_time, clock)
        self._history: List[tuple] = []                 # (monotonic, private_mb)
        self._growth_armed = True
        self._warned_at_mb: Optional[float] = None

    # ------------------------------------------------------------------ #
    #  Sampling                                                            #
    # ------------------------------------------------------------------ #

    def sample(self, gauges: Optional[Dict[str, object]] = None, *,
               reason: Optional[str] = None,
               new_regime: bool = False) -> ResourceReport:
        """Take one sample. ``reason`` forces a line; ``new_regime`` (capture
        started or stopped) also restarts the growth window, because the
        step up into capturing is expected and must not read as a leak."""
        t = self._clock()
        mem = self._read_memory() or {}
        cpu_now = self._process_time()
        cpu_percent = None
        if self._prev_cpu is not None:
            prev_cpu, prev_t = self._prev_cpu
            wall = t - prev_t
            if wall > 0:
                cpu_percent = max(0.0, (cpu_now - prev_cpu) / wall / self._cpu_count * 100.0)
        self._prev_cpu = (cpu_now, t)

        sample = ResourceSample(
            taken_at=self._now().isoformat(timespec='seconds'),
            monotonic=t,
            working_set_mb=float(mem.get('working_set_mb', -1.0)),
            peak_working_set_mb=float(mem.get('peak_working_set_mb', -1.0)),
            private_mb=float(mem.get('private_mb', -1.0)),
            page_faults=int(mem.get('page_faults', -1)),
            cpu_percent=cpu_percent,
            threads=int(self._thread_count()),
            gauges=dict(gauges or {}),
            reason=reason,
        )

        with self._lock:
            if new_regime:
                self._history.clear()
                self._growth_armed = True
                self._warned_at_mb = None
            warning = self._check_growth(sample)
            line = None
            if self._earns_line(sample):
                line = self.format_line(sample, self._last_logged)
                self._last_logged = sample
            self._last = sample
        return ResourceReport(sample=sample, line=line, warning=warning)

    def _earns_line(self, sample: ResourceSample) -> bool:
        if sample.reason or self._last_logged is None:
            return True
        if sample.monotonic - self._last_logged.monotonic >= self._heartbeat_s:
            return True
        if sample.private_mb < 0 or self._last_logged.private_mb < 0:
            return False
        return abs(sample.private_mb - self._last_logged.private_mb) >= self._step_mb

    def _check_growth(self, sample: ResourceSample) -> Optional[str]:
        if sample.private_mb < 0:
            return None
        self._history.append((sample.monotonic, sample.private_mb))
        cutoff = sample.monotonic - self._growth_window_s
        self._history = [h for h in self._history if h[0] >= cutoff]
        if len(self._history) < 3:
            return None
        span = sample.monotonic - self._history[0][0]
        baseline = min(p for _, p in self._history)
        top = max(p for _, p in self._history)
        rise = sample.private_mb - baseline
        if rise < self._growth_warn_mb / 2:
            self._growth_armed = True
            self._warned_at_mb = None
            return None
        if not self._growth_armed:
            # A climb that never pauses would otherwise warn exactly once:
            # re-arm after another full threshold of growth past the last warning.
            if (self._warned_at_mb is None
                    or sample.private_mb - self._warned_at_mb < self._growth_warn_mb):
                return None
            self._growth_armed = True
        # Warn only for a rise that has lasted and is still at its top — a
        # spike that already fell back is not retention.
        if span < self._growth_window_s / 2 or sample.private_mb < top - self._step_mb / 2:
            return None
        self._growth_armed = False
        self._warned_at_mb = sample.private_mb
        return (f"Memory growth: private bytes up {rise:,.0f} MB over the last "
                f"{span / 60:.0f} min ({baseline:,.0f} → {sample.private_mb:,.0f} MB) "
                f"and still rising — this is retention, not working-set noise. "
                f"Export Diagnostics captures the current resource snapshot.")

    # ------------------------------------------------------------------ #
    #  Output                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def format_line(sample: ResourceSample,
                    previous: Optional[ResourceSample] = None) -> str:
        head = "Resources"
        if sample.reason:
            head += f" [{sample.reason}]"
        parts = []
        ws = sample.working_set_mb
        if ws >= 0:
            peak = sample.peak_working_set_mb
            peak_txt = f" (peak {peak:,.0f})" if peak >= 0 else ""
            parts.append(f"working set {ws:,.0f} MB{peak_txt}")
        if sample.private_mb >= 0:
            delta = ""
            if previous is not None and previous.private_mb >= 0:
                d = sample.private_mb - previous.private_mb
                delta = f" ({d:+,.0f} since last report)"
            parts.append(f"private {sample.private_mb:,.0f} MB{delta}")
        if sample.cpu_percent is not None:
            parts.append(f"CPU {sample.cpu_percent:.1f}%")
        parts.append(f"{sample.threads} threads")
        for key, value in sample.gauges.items():
            parts.append(f"{key}={_format_gauge(key, value)}")
        return f"{head}: " + " · ".join(parts)

    @property
    def last_sample(self) -> Optional[ResourceSample]:
        with self._lock:
            return self._last

    def snapshot(self) -> dict:
        """The latest sample as plain data (for the diagnostics bundle)."""
        with self._lock:
            return self._last.as_dict() if self._last is not None else {}


def _format_gauge(key: str, value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:,.0f}" if key.endswith('_mb') else f"{value:.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


# ---------------------------------------------------------------------- #
#  Opt-in Python-heap hotspots                                             #
# ---------------------------------------------------------------------- #

class MemoryTrace:
    """tracemalloc top-N by allocation site, with growth since the last report.

    Opt-in (config ``diagnostics.memory_trace``): tracing costs CPU on every
    allocation and the retained snapshot is not free. It sees Python-side
    allocations, numpy arrays included; Pillow's own image buffers are
    invisible to it, so a PIL-only holder shows up in ``private_mb`` but not
    here — that absence is itself a finding.
    """

    def __init__(self, top_n: int = 10, nframes: int = 1):
        self._top_n = int(top_n)
        self._nframes = int(nframes)
        self._prev: Optional[tracemalloc.Snapshot] = None

    @property
    def active(self) -> bool:
        return tracemalloc.is_tracing()

    def start(self) -> bool:
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(self._nframes)
            return True
        except Exception:
            return False

    def stop(self) -> None:
        self._prev = None
        try:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
        except Exception:
            pass

    def report(self) -> List[str]:
        if not tracemalloc.is_tracing():
            return []
        try:
            snap = tracemalloc.take_snapshot().filter_traces((
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, '<frozen importlib._bootstrap>'),
                tracemalloc.Filter(False, '<unknown>'),
            ))
            current, peak = tracemalloc.get_traced_memory()
        except Exception as e:
            return [f"Python heap: tracemalloc snapshot failed: {e}"]

        lines = [f"Python heap (tracemalloc): {current / _MB:,.0f} MB traced, "
                 f"peak {peak / _MB:,.0f} MB"]
        for stat in snap.statistics('lineno')[:self._top_n]:
            lines.append(f"  {stat.size / _MB:7.1f} MB  {stat.count:>8,} blocks  "
                         f"{_short_site(stat.traceback)}")
        if self._prev is not None:
            grown = [d for d in snap.compare_to(self._prev, 'lineno')
                     if d.size_diff > 0][:self._top_n]
            if grown:
                lines.append("  grown since last report:")
                for d in grown:
                    lines.append(f"  {d.size_diff / _MB:+7.1f} MB  {d.count_diff:+8,} blocks  "
                                 f"{_short_site(d.traceback)}")
        self._prev = snap
        return lines


def _short_site(traceback) -> str:
    frame = traceback[0]
    parts = Path(frame.filename).parts
    return f"{'/'.join(parts[-2:])}:{frame.lineno}"
