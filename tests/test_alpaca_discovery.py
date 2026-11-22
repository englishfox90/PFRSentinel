"""alpaca_discovery — UDP query/reply on a fake socket, Camera filter, dedupe, bounded waits."""
import functools
import logging
from types import SimpleNamespace

from services.camera.alpaca import alpaca_discovery
from services.camera.alpaca.alpaca_discovery import (
    AlpacaDeviceInfo,
    cameras_on_server,
    discover_cameras,
    discover_servers,
    parse_discovery_reply,
)
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_protocol import DISCOVERY_MESSAGE
from tests.alpaca_fake import (
    FakeAlpacaCamera,
    FakeAlpacaNetwork,
    FakeClock,
    FakeDiscoverySocket,
    SimCameraSpec,
    discovery_reply,
)

SAFETY_MONITOR = {"DeviceName": "PFR Sentinel Roof", "DeviceType": "SafetyMonitor",
                  "DeviceNumber": 0, "UniqueID": "safety-1"}


def _servers(responders, clock=None, **kw):
    clock = clock or FakeClock()
    sock = FakeDiscoverySocket(responders, clock)
    found = discover_servers(2.0, socket_factory=lambda: sock, clock=clock, **kw)
    return found, sock, clock


def test_query_is_alpacadiscovery1_to_broadcast_and_loopback_on_32227(monkeypatch):
    monkeypatch.setattr(alpaca_discovery, "_adapters", lambda: [])
    found, sock, clock = _servers({})
    assert found == []
    targets = [addr for _, addr in sock.sent]
    assert ("255.255.255.255", 32227) in targets and ("127.0.0.1", 32227) in targets
    assert all(data == b"alpacadiscovery1" == DISCOVERY_MESSAGE for data, _ in sock.sent)
    assert len(sock.sent) == 4            # sent twice: UDP may drop the first
    assert sock.closed
    assert clock.now - 1000.0 <= 2.1      # bounded by the timeout


def test_replies_parsed_and_deduped_in_reply_order():
    found, _, _ = _servers({
        "255.255.255.255": [("192.168.1.20", discovery_reply(11111)),
                            ("192.168.1.30", discovery_reply(32323))],
        "127.0.0.1": [("127.0.0.1", discovery_reply(11112))],
    })
    assert found == [("192.168.1.20", 11111), ("192.168.1.30", 32323), ("127.0.0.1", 11112)]


def test_garbage_replies_are_ignored():
    found, _, _ = _servers({"255.255.255.255": [
        ("10.0.0.1", b"not json"), ("10.0.0.2", b'{"AlpacaPort": "x"}'),
        ("10.0.0.3", b'{"AlpacaPort": 70000}'), ("10.0.0.4", b"[1]"),
        ("10.0.0.5", b'{"alpacaport": 4567}'), ("10.0.0.6", b'{"AlpacaPort": true}'),
    ]})
    assert found == [("10.0.0.5", 4567)]


def test_parse_discovery_reply():
    assert parse_discovery_reply(b'{"AlpacaPort": 11111}') == 11111
    assert parse_discovery_reply(b"\xff\xfe") is None
    assert parse_discovery_reply(b'{"Port": 1}') is None


def test_socket_open_failure_returns_empty():
    def boom():
        raise OSError("no network")
    assert discover_servers(1.0, socket_factory=boom) == []


def test_send_failure_on_one_target_keeps_the_rest():
    clock = FakeClock()
    sock = FakeDiscoverySocket({"127.0.0.1": [("127.0.0.1", discovery_reply(11112))]}, clock)
    original = sock.sendto

    def sendto(data, addr):
        if addr[0] == "255.255.255.255":
            raise OSError("Network is unreachable")
        return original(data, addr)
    sock.sendto = sendto
    assert discover_servers(1.0, socket_factory=lambda: sock, clock=clock) == [("127.0.0.1", 11112)]


def _network():
    clock = FakeClock()
    net = FakeAlpacaNetwork(clock)
    cam_a = FakeAlpacaCamera(SimCameraSpec(name="AllSky A", unique_id="uid-a", width=8,
                                           height=6, extra_devices=[SAFETY_MONITOR]), clock=clock)
    cam_b = FakeAlpacaCamera(SimCameraSpec(name="AllSky B", unique_id="uid-b", device_number=2,
                                           width=8, height=6), clock=clock)
    net.add_camera(cam_a, "192.168.1.20", 11111)
    net.add_camera(cam_b, "192.168.1.30", 32323)
    net.add_camera(cam_a, "127.0.0.1", 11111)       # the same server seen over loopback
    factory = functools.partial(AlpacaHttpClient, session_factory=net.session)
    return net, clock, factory


def test_cameras_on_server_filters_out_the_safety_monitor():
    net, clock, factory = _network()
    cams = cameras_on_server("192.168.1.20", 11111, client_factory=factory)
    assert cams == [AlpacaDeviceInfo("192.168.1.20", 11111, 0, "AllSky A", "uid-a", "Camera")]
    assert all(r.timeout == (3.0, 3.0) for r in net.requests)


def test_discover_cameras_end_to_end_with_dedupe_and_a_dead_server():
    net, clock, factory = _network()
    sock = FakeDiscoverySocket({"255.255.255.255": [
        ("192.168.1.20", discovery_reply(11111)),
        ("192.168.1.30", discovery_reply(32323)),
        ("192.168.1.99", discovery_reply(11111)),           # answers UDP, HTTP is down
    ], "127.0.0.1": [("127.0.0.1", discovery_reply(11111))]}, clock)
    cams = discover_cameras(2.0, socket_factory=lambda: sock, client_factory=factory, clock=clock)
    assert [(c.host, c.port, c.device_number, c.name) for c in cams] == [
        ("192.168.1.20", 11111, 0, "AllSky A"),
        ("192.168.1.30", 32323, 2, "AllSky B"),
    ]
    assert cams[0].label() == "AllSky A — 192.168.1.20:11111 #0"


def test_renumbered_device_is_listed_under_its_new_number():
    net, clock, factory = _network()
    net.servers[("192.168.1.30", 32323)].renumber(5)
    cams = cameras_on_server("192.168.1.30", 32323, client_factory=factory)
    assert [(c.device_number, c.unique_id) for c in cams] == [(5, "uid-b")]


def _adapter(*ips):
    return SimpleNamespace(ips=[SimpleNamespace(ip=ip, network_prefix=prefix) for ip, prefix in ips])


def test_each_interface_gets_its_directed_broadcast():
    adapters = [_adapter(("192.168.1.20", 24), (("fe80::1", 0, 3), 64)),
                _adapter(("10.147.17.5", 16)),          # ZeroTier
                _adapter(("127.0.0.1", 8)),             # loopback: covered already
                _adapter(("100.64.0.9", 32)),           # point-to-point: no broadcast
                _adapter(("192.168.1.21", 24))]         # same subnet twice
    assert alpaca_discovery.discovery_targets(lambda: adapters) == (
        "255.255.255.255", "127.0.0.1", "192.168.1.255", "10.147.255.255")


def test_interface_enumeration_failure_falls_back_to_broadcast_and_loopback():
    def broken():
        raise OSError("no permission")
    assert alpaca_discovery.discovery_targets(broken) == ("255.255.255.255", "127.0.0.1")


def test_a_camera_on_a_second_subnet_is_found(monkeypatch):
    monkeypatch.setattr(alpaca_discovery, "_adapters",
                        lambda: [_adapter(("10.147.17.5", 16))])
    found, sock, _ = _servers({"10.147.255.255": [("10.147.17.80", discovery_reply(11111))]})
    assert found == [("10.147.17.80", 11111)]
    assert ("10.147.255.255", 32227) in [addr for _, addr in sock.sent]


def test_listing_failures_are_debug_with_one_info_summary(caplog):
    clock = FakeClock()
    net = FakeAlpacaNetwork(clock)
    net.add_camera(FakeAlpacaCamera(SimCameraSpec(), clock=clock), host="192.168.1.20",
                   port=11111)
    sock = FakeDiscoverySocket({"255.255.255.255": [
        ("192.168.1.20", discovery_reply(11111)), ("192.168.1.99", discovery_reply(11111))]},
        clock)
    factory = functools.partial(AlpacaHttpClient, session_factory=net.session)
    with caplog.at_level(logging.DEBUG, logger="PFRSentinel"):
        cams = discover_cameras(2.0, socket_factory=lambda: sock, client_factory=factory,
                                clock=clock)
    assert len(cams) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO
             and r.getMessage().startswith("Alpaca discovery:")]
    assert infos == ["Alpaca discovery: 2 server(s) answered, 1 camera(s) found; "
                     "192.168.1.99:11111 did not list devices (details at DEBUG)"]
