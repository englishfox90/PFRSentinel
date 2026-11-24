"""
Alpaca SafetyMonitor ``verdict_source``: IsSafe from the roof (default) or from
the imaging-conditions grade, switched by config, fail-safe either way.
"""
import json
from datetime import datetime, timezone

import pytest

from services.alpaca_safety.protocol import (
    DEVICE_DESCRIPTION, DEVICE_DESCRIPTION_CONDITIONS, SafetyMonitorDevice)
from services.alpaca_safety.service import AlpacaSafetyService
from services.alpaca_safety.state import SafetyState
from services.alpaca_safety.verdict_source import (
    IMAGING_CONDITIONS, ROOF, SafetyVerdict, read_source)
from services.config_defaults import DEFAULT_CONFIG
from services.imaging_conditions.conditions_store import ConditionsStore, empty_record

T0 = datetime(2026, 11, 24, 22, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def roof_safe(clock):
    state = SafetyState(clock=clock)
    state.publish(True)
    state.note_frame()
    return state


@pytest.fixture
def store(clock):
    return ConditionsStore(clock=clock, events_fn=lambda: [])


def _publish(store, ok, stale_after_s=300.0):
    record = empty_record()
    record.update(imaging_ok=ok, reasons=[] if ok else ['cloud'], changed_at=T0)
    store.publish(record, updated_at=T0, stale_after_s=stale_after_s)


def _issafe(device):
    _, _, body = device.handle('GET', '/api/v1/safetymonitor/0/issafe', {})
    return json.loads(body)['Value']


def _connected_device(state, store, source):
    verdict = SafetyVerdict(state, conditions_fn=store.verdict)
    verdict.set_source(source)
    device = SafetyMonitorDevice(state, verdict)
    device.handle('PUT', '/api/v1/safetymonitor/0/connect', {})
    return device


def test_default_source_is_roof_and_unknown_reads_roof():
    assert DEFAULT_CONFIG['ml_models']['alpaca_safety_monitor']['verdict_source'] == ROOF
    assert read_source({}) == ROOF
    assert read_source({'verdict_source': 'Imaging_Conditions'}) == IMAGING_CONDITIONS
    assert read_source({'verdict_source': 'weather'}) == ROOF
    assert read_source(None) == ROOF


def test_roof_source_ignores_the_conditions_grade(roof_safe, store):
    _publish(store, False)
    device = _connected_device(roof_safe, store, ROOF)
    assert _issafe(device) is True


def test_conditions_source_follows_imaging_ok_with_roof_open(roof_safe, store):
    device = _connected_device(roof_safe, store, IMAGING_CONDITIONS)
    assert _issafe(device) is False  # nothing judged yet
    _publish(store, True)
    assert _issafe(device) is True
    _publish(store, False)
    assert _issafe(device) is False


def test_conditions_source_reads_unsafe_when_stale_or_off(roof_safe, store, clock):
    device = _connected_device(roof_safe, store, IMAGING_CONDITIONS)
    _publish(store, True, stale_after_s=120)
    clock.t += 121
    assert _issafe(device) is False
    _publish(store, True)
    store.set_enabled(False)
    assert _issafe(device) is False


def test_conditions_source_failure_reads_unsafe(roof_safe):
    def boom():
        raise RuntimeError("store broke")
    verdict = SafetyVerdict(roof_safe, conditions_fn=boom)
    verdict.set_source(IMAGING_CONDITIONS)
    assert verdict.is_safe() is False


def test_store_verdict_matches_snapshot(store, clock):
    assert store.verdict() == (False, None)
    _publish(store, True, stale_after_s=200)
    assert store.verdict() == (True, T0)
    assert store.snapshot()['imaging_ok'] is True
    clock.t += 201
    ok, changed_at = store.verdict()
    assert ok is False and store.snapshot()['imaging_ok'] is False
    assert changed_at is not None and changed_at > T0


def test_device_state_and_description_follow_the_source(roof_safe, store):
    _publish(store, True)
    device = _connected_device(roof_safe, store, IMAGING_CONDITIONS)
    _, _, body = device.handle('GET', '/api/v1/safetymonitor/0/devicestate', {})
    state = {row['Name']: row['Value'] for row in json.loads(body)['Value']}
    assert state['IsSafe'] is True
    assert state['TimeStamp'].startswith('2026-11-24T22:00:00')
    _, _, body = device.handle('GET', '/api/v1/safetymonitor/0/description', {})
    assert json.loads(body)['Value'] == DEVICE_DESCRIPTION_CONDITIONS
    device.verdict.set_source(ROOF)
    _, _, body = device.handle('GET', '/api/v1/safetymonitor/0/description', {})
    assert json.loads(body)['Value'] == DEVICE_DESCRIPTION


class _FakeServer:
    def __init__(self, device, bind, port, discovery):
        self.device, self.bind, self.requested_port, self.discovery = device, bind, port, discovery

    def start(self):
        return True

    def stop(self):
        pass


def test_reconcile_switches_source_without_restarting_the_server(roof_safe):
    service = AlpacaSafetyService(state=roof_safe, server_factory=_FakeServer)
    cfg = dict(DEFAULT_CONFIG['ml_models']['alpaca_safety_monitor'], enabled=True)
    service.reconcile({'alpaca_safety_monitor': cfg})
    server = service._server
    assert server.device.verdict.source == ROOF
    service.reconcile({'alpaca_safety_monitor': dict(cfg, verdict_source=IMAGING_CONDITIONS)})
    assert service._server is server
    assert server.device.verdict.source == IMAGING_CONDITIONS
