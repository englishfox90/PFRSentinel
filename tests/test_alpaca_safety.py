"""
Alpaca SafetyMonitor — confirmed roof verdict served over ASCOM Alpaca.

State staleness and the feed's Policy A confirmation run with injected clocks;
the protocol is exercised as a pure handler; one socket test checks the real
HTTP server and discovery reply end to end.
"""
import json
import socket
import urllib.parse
import urllib.request

import pytest

from services.alpaca_safety.feed import AlpacaSafetyFeed
from services.alpaca_safety.protocol import SafetyMonitorDevice, UNIQUE_ID, NOT_IMPLEMENTED
from services.alpaca_safety.server import AlpacaSafetyServer
from services.alpaca_safety.service import AlpacaSafetyService
from services.alpaca_safety.state import SafetyState
from services.config_defaults import DEFAULT_CONFIG

OPEN = {'roof_status': 'Open', 'roof_confidence': 0.95}
CLOSED = {'roof_status': 'Closed', 'roof_confidence': 0.95}
UNSURE_OPEN = {'roof_status': 'Open', 'roof_confidence': 0.4}


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def state(clock):
    return SafetyState(clock=clock)


def _ml_config(**over):
    cfg = dict(DEFAULT_CONFIG['ml_models']['alpaca_safety_monitor'], enabled=True)
    cfg.update(over)
    return {'alpaca_safety_monitor': cfg}


# --- state -------------------------------------------------------------------

def test_state_starts_unsafe_and_stale(state):
    assert state.is_stale()
    assert state.is_safe() is False


def test_state_goes_unsafe_when_frames_stop(state, clock):
    state.publish(True)
    state.note_frame()
    assert state.is_safe() is True
    clock.t += state.stale_after_seconds + 1
    assert state.is_safe() is False


def test_publish_reports_only_changes(state):
    assert state.publish(True) is True
    assert state.publish(True) is False
    assert state.publish(False) is True


# --- feed --------------------------------------------------------------------

def test_feed_needs_two_confident_open_frames(state):
    feed = AlpacaSafetyFeed(state)
    feed.update(OPEN, _ml_config())
    assert state.is_safe() is False
    feed.update(OPEN, _ml_config())
    assert state.is_safe() is True


def test_feed_needs_two_closed_frames_to_go_unsafe(state):
    feed = AlpacaSafetyFeed(state)
    for _ in range(2):
        feed.update(OPEN, _ml_config())
    feed.update(CLOSED, _ml_config())
    assert state.is_safe() is True
    feed.update(CLOSED, _ml_config())
    assert state.is_safe() is False


def test_feed_treats_low_confidence_and_missing_results_as_unsafe(state):
    feed = AlpacaSafetyFeed(state)
    for _ in range(2):
        feed.update(OPEN, _ml_config())
    feed.update(UNSURE_OPEN, _ml_config())
    feed.update(None, _ml_config())
    assert state.is_safe() is False


def test_feed_does_nothing_while_disabled(state):
    feed = AlpacaSafetyFeed(state)
    for _ in range(3):
        feed.update(OPEN, _ml_config(enabled=False))
    assert state.is_stale()
    assert state.is_safe() is False


def test_feed_starts_over_after_being_switched_off(state):
    feed = AlpacaSafetyFeed(state)
    for _ in range(2):
        feed.update(OPEN, _ml_config())
    feed.update(OPEN, _ml_config(enabled=False))
    feed.update(OPEN, _ml_config())
    assert state.is_safe() is False
    feed.update(OPEN, _ml_config())
    assert state.is_safe() is True


def test_feed_respects_configured_min_confidence(state):
    feed = AlpacaSafetyFeed(state)
    for _ in range(2):
        feed.update(UNSURE_OPEN, _ml_config(min_confidence=0.3))
    assert state.is_safe() is True


# --- protocol ----------------------------------------------------------------

def _call(device, method, path, **params):
    status, ctype, body = device.handle(method, path, {k: str(v) for k, v in params.items()})
    return status, (json.loads(body) if ctype == 'application/json' else body.decode())


@pytest.fixture
def safe_device(state):
    state.publish(True)
    state.note_frame()
    return SafetyMonitorDevice(state)


def test_management_lists_one_safety_monitor(safe_device):
    status, body = _call(safe_device, 'GET', '/management/v1/configureddevices')
    assert status == 200
    assert body['Value'] == [{'DeviceName': 'PFR Sentinel Roof', 'DeviceType': 'SafetyMonitor',
                              'DeviceNumber': 0, 'UniqueID': UNIQUE_ID}]
    assert _call(safe_device, 'GET', '/management/apiversions')[1]['Value'] == [1]


def test_issafe_is_false_until_connected(safe_device):
    assert _call(safe_device, 'GET', '/api/v1/safetymonitor/0/issafe')[1]['Value'] is False
    _call(safe_device, 'PUT', '/api/v1/safetymonitor/0/connected', Connected='True')
    assert _call(safe_device, 'GET', '/api/v1/safetymonitor/0/issafe')[1]['Value'] is True
    _call(safe_device, 'PUT', '/api/v1/safetymonitor/0/disconnect')
    assert _call(safe_device, 'GET', '/api/v1/safetymonitor/0/connected')[1]['Value'] is False


def test_issafe_follows_staleness(safe_device, clock, state):
    _call(safe_device, 'PUT', '/api/v1/safetymonitor/0/connect')
    clock.t += state.stale_after_seconds + 1
    assert _call(safe_device, 'GET', '/api/v1/safetymonitor/0/issafe')[1]['Value'] is False


def test_parameter_names_are_case_insensitive_and_transaction_ids_echo(safe_device):
    _, body = _call(safe_device, 'PUT', '/API/V1/SafetyMonitor/0/Connected',
                    connected='true', ClientTransactionID=42)
    assert body['ErrorNumber'] == 0
    assert body['ClientTransactionID'] == 42
    assert 'Value' not in body
    assert safe_device.connected


def test_bad_requests_are_http_errors(safe_device):
    assert _call(safe_device, 'GET', '/api/v1/safetymonitor/1/issafe')[0] == 400
    assert _call(safe_device, 'PUT', '/api/v1/safetymonitor/0/connected', Connected='maybe')[0] == 400
    assert _call(safe_device, 'GET', '/api/v1/camera/0/connected')[0] == 404


def test_unknown_members_and_commands_are_not_implemented(safe_device):
    for method, member in (('GET', 'nosuchthing'), ('PUT', 'commandblind'), ('PUT', 'action')):
        status, body = _call(safe_device, method, f'/api/v1/safetymonitor/0/{member}')
        assert status == 200
        assert body['ErrorNumber'] == NOT_IMPLEMENTED


def test_garbage_transaction_id_reads_as_zero(safe_device):
    _, body = _call(safe_device, 'GET', '/api/v1/safetymonitor/0/name', ClientTransactionID='x')
    assert body['ClientTransactionID'] == 0
    assert body['Value'] == 'PFR Sentinel Roof'


def test_device_state_carries_issafe(safe_device):
    _call(safe_device, 'PUT', '/api/v1/safetymonitor/0/connect')
    _, body = _call(safe_device, 'GET', '/api/v1/safetymonitor/0/devicestate')
    assert {'Name': 'IsSafe', 'Value': True} in body['Value']


def test_interface_version_is_v3(safe_device):
    assert _call(safe_device, 'GET', '/api/v1/safetymonitor/0/interfaceversion')[1]['Value'] == 3


# --- service -----------------------------------------------------------------

class _FakeServer:
    started = []

    def __init__(self, device, bind, port, discovery):
        self.bind, self.requested_port, self.discovery = bind, port, discovery
        self.stopped = False

    def start(self):
        _FakeServer.started.append(self)
        return True

    def stop(self):
        self.stopped = True


def test_service_follows_config(state):
    _FakeServer.started = []
    service = AlpacaSafetyService(state, server_factory=_FakeServer)
    service.reconcile(_ml_config(enabled=False))
    assert not service.running

    service.reconcile(_ml_config())
    service.reconcile(_ml_config())
    assert len(_FakeServer.started) == 1

    service.reconcile(_ml_config(port=11112))
    assert _FakeServer.started[0].stopped
    assert _FakeServer.started[1].requested_port == 11112

    service.reconcile(_ml_config(enabled=False))
    assert _FakeServer.started[1].stopped
    assert not service.running


def test_service_survives_a_server_that_cannot_start(state):
    class _Refuses(_FakeServer):
        def start(self):
            return False

    service = AlpacaSafetyService(state, server_factory=_Refuses)
    service.reconcile(_ml_config())
    assert not service.running


# --- sockets -----------------------------------------------------------------

def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.requires_network
def test_real_server_answers_http_and_discovery(state):
    state.publish(True)
    state.note_frame()
    discovery_port = _free_udp_port()
    server = AlpacaSafetyServer(SafetyMonitorDevice(state), bind='127.0.0.1', port=0,
                                discovery=True, discovery_port=discovery_port)
    assert server.start()
    try:
        base = f'http://127.0.0.1:{server.port}/api/v1/safetymonitor/0'
        put = urllib.request.Request(f'{base}/connected', method='PUT',
                                     data=urllib.parse.urlencode({'Connected': 'True'}).encode())
        with urllib.request.urlopen(put, timeout=5) as r:
            assert json.loads(r.read())['ErrorNumber'] == 0
        with urllib.request.urlopen(f'{base}/issafe?ClientTransactionID=7', timeout=5) as r:
            body = json.loads(r.read())
        assert body['Value'] is True
        assert body['ClientTransactionID'] == 7

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.settimeout(5)
        udp.sendto(b'alpacadiscovery1', ('127.0.0.1', discovery_port))
        reply, _ = udp.recvfrom(1024)
        udp.close()
        assert json.loads(reply) == {'AlpacaPort': server.port}
    finally:
        server.stop()
