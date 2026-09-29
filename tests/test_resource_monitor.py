"""Tests for services.resource_monitor and services.performance.get_process_memory.

The monitor is clock- and counter-injected, so every rule is exercised on a
fake timeline: first sample always logs, a quiet app logs only on the
heartbeat, a private-bytes step earns a line, a reason forces one, CPU% is
process time over wall time over cores, and the growth warning fires once
for a sustained rise, never for a spike or for the expected step into
capturing, and re-arms once the window's baseline catches up.
"""
import json
import sys
from datetime import datetime

import pytest

from services import performance
from services.resource_monitor import (
    MemoryTrace, ResourceMonitor, ResourceSample, _format_gauge)


class _Fake:
    """Injected clock, process time and memory counters."""

    def __init__(self):
        self.t = 1000.0
        self.cpu = 0.0
        self.mem = {'working_set_mb': 900.0, 'peak_working_set_mb': 1400.0,
                    'private_mb': 700.0, 'page_faults': 10}

    def monitor(self, **kw):
        kw.setdefault('heartbeat_s', 1800)
        kw.setdefault('step_mb', 64)
        kw.setdefault('growth_warn_mb', 256)
        kw.setdefault('growth_window_s', 3600)
        return ResourceMonitor(
            read_memory=lambda: dict(self.mem),
            process_time=lambda: self.cpu,
            clock=lambda: self.t,
            now=lambda: datetime(2026, 9, 28, 22, 0, 0),
            cpu_count=4,
            thread_count=lambda: 17,
            **kw,
        )

    def advance(self, seconds, cpu_seconds=0.0, private=None):
        self.t += seconds
        self.cpu += cpu_seconds
        if private is not None:
            self.mem['private_mb'] = private


@pytest.fixture
def fake():
    return _Fake()


# --------------------------------------------------------------------------- #
#  Line policy                                                                 #
# --------------------------------------------------------------------------- #

def test_first_sample_always_logs(fake):
    report = fake.monitor().sample({'allsky_buffer': 12})
    assert report.line is not None
    assert report.line.startswith("Resources: ")
    assert "working set 900 MB (peak 1,400)" in report.line
    assert "private 700 MB" in report.line
    assert "17 threads" in report.line
    assert "allsky_buffer=12" in report.line
    assert report.sample.cpu_percent is None
    assert report.warning is None


def test_quiet_app_logs_only_on_heartbeat(fake):
    mon = fake.monitor()
    mon.sample()
    lines = []
    for _ in range(5):
        fake.advance(300)
        lines.append(mon.sample().line)
    assert lines == [None] * 5
    fake.advance(300)              # 1800 s since the first line
    assert mon.sample().line is not None


def test_private_step_earns_a_line_with_delta(fake):
    mon = fake.monitor()
    mon.sample()
    fake.advance(300, private=740)     # +40: under the 64 MB step
    assert mon.sample().line is None
    fake.advance(300, private=770)     # +70 since the last LINE
    line = mon.sample().line
    assert line is not None
    assert "private 770 MB (+70 since last report)" in line


def test_reason_forces_a_line(fake):
    mon = fake.monitor()
    mon.sample()
    fake.advance(10)
    line = mon.sample(reason='capture started').line
    assert line is not None
    assert line.startswith("Resources [capture started]: ")


def test_cpu_percent_is_process_time_over_wall_over_cores(fake):
    mon = fake.monitor()
    mon.sample()
    fake.advance(300, cpu_seconds=120)   # 120 s of CPU in 300 s on 4 cores
    report = mon.sample(reason='tick')
    assert report.sample.cpu_percent == pytest.approx(10.0)
    assert "CPU 10.0%" in report.line


def test_missing_counters_do_not_break_the_line(fake):
    fake.mem = {}
    report = fake.monitor().sample({'x': 1})
    assert report.sample.working_set_mb == -1.0
    assert "working set" not in report.line
    assert "private" not in report.line
    assert "x=1" in report.line


# --------------------------------------------------------------------------- #
#  Growth warning                                                              #
# --------------------------------------------------------------------------- #

def _climb(fake, mon, steps, per_step_mb, interval=300):
    warnings = []
    for _ in range(steps):
        fake.advance(interval, private=fake.mem['private_mb'] + per_step_mb)
        warnings.append(mon.sample().warning)
    return warnings


def test_sustained_rise_warns_once(fake):
    mon = fake.monitor()
    mon.sample()
    warnings = _climb(fake, mon, steps=12, per_step_mb=40)   # +480 MB over an hour
    fired = [w for w in warnings if w]
    assert len(fired) == 1
    assert "retention" in fired[0]
    assert "still rising" in fired[0]


def test_spike_that_falls_back_does_not_warn(fake):
    mon = fake.monitor()
    mon.sample()
    _climb(fake, mon, steps=3, per_step_mb=150)            # +450 in 15 min
    assert mon.sample().warning is None                    # span too short
    fake.advance(300, private=700)                         # fell back
    for _ in range(8):
        fake.advance(300)
        assert mon.sample().warning is None


def test_step_into_capturing_is_not_a_leak(fake):
    mon = fake.monitor()
    mon.sample()
    fake.advance(300)
    mon.sample()
    fake.advance(300, private=1300)                        # +600 at capture start
    assert mon.sample(reason='capture started', new_regime=True).warning is None
    for _ in range(12):                                    # then flat for an hour
        fake.advance(300)
        assert mon.sample().warning is None


def test_warning_rearms_after_the_baseline_catches_up(fake):
    mon = fake.monitor()
    mon.sample()
    first = [w for w in _climb(fake, mon, 12, 40) if w]
    assert len(first) == 1
    # Keep climbing: the window slides, the baseline follows, and once the
    # rise vs the window's minimum drops under half the threshold the guard
    # re-arms; a second hour of growth warns again.
    second = [w for w in _climb(fake, mon, 24, 40) if w]
    assert len(second) >= 1


# --------------------------------------------------------------------------- #
#  Formatting and snapshot                                                     #
# --------------------------------------------------------------------------- #

def test_gauge_formatting():
    assert _format_gauge('meteor_stack_mb', 31.4) == "31"
    assert _format_gauge('cpu_ratio', 0.25) == "0.2"
    assert _format_gauge('allsky_buffer', 1200) == "1,200"
    assert _format_gauge('flag', True) == "True"
    assert _format_gauge('mode', 'camera') == "camera"


def test_snapshot_is_plain_json_data(fake):
    mon = fake.monitor()
    assert mon.snapshot() == {}
    mon.sample({'allsky_buffer': 60, 'meteor_stack_mb': 31.0}, reason='startup')
    snap = mon.snapshot()
    json.dumps(snap)
    assert snap['reason'] == 'startup'
    assert snap['gauges'] == {'allsky_buffer': 60, 'meteor_stack_mb': 31.0}
    assert snap['taken_at'] == '2026-09-28T22:00:00'
    assert isinstance(mon.last_sample, ResourceSample)


# --------------------------------------------------------------------------- #
#  Real counters and the opt-in trace                                          #
# --------------------------------------------------------------------------- #

def test_get_process_memory_reads_real_counters():
    info = performance.get_process_memory()
    if sys.platform not in ('win32', 'linux'):
        pytest.skip("no cheap counter source on this platform")
    assert info is not None
    assert set(info) == {'working_set_mb', 'peak_working_set_mb', 'private_mb', 'page_faults'}
    assert info['working_set_mb'] > 10
    assert info['peak_working_set_mb'] >= info['working_set_mb'] * 0.5
    assert info['private_mb'] > 10
    assert performance.get_memory_usage_mb() == pytest.approx(info['working_set_mb'], rel=0.5)


def test_get_memory_usage_mb_is_minus_one_when_counters_fail(monkeypatch):
    monkeypatch.setattr(performance, 'get_process_memory', lambda: None)
    assert performance.get_memory_usage_mb() == -1.0


def test_memory_trace_reports_hotspots_and_growth():
    import tracemalloc
    was_tracing = tracemalloc.is_tracing()
    trace = MemoryTrace(top_n=3)
    assert trace.report() == [] or was_tracing
    assert trace.start()
    try:
        held = [bytearray(2 * 1024 * 1024) for _ in range(3)]
        first = trace.report()
        assert first and first[0].startswith("Python heap (tracemalloc):")
        assert "MB traced" in first[0]
        assert any("blocks" in line for line in first[1:])
        held.extend(bytearray(4 * 1024 * 1024) for _ in range(2))
        second = trace.report()
        assert any("grown since last report" in line for line in second)
        del held
    finally:
        if not was_tracing:
            trace.stop()
            assert not tracemalloc.is_tracing()
