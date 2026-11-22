"""In-process fake ASCOM Alpaca network for the Alpaca camera tests.

The camera itself is ``scripts/dev/alpaca_sim``'s ``FakeAlpacaCamera`` — the
same pure state machine the real-HTTP simulator serves — so a test here sees
exactly the wire bytes (JSON envelopes, ImageBytes) a live server sends.
This module adds what a socket would: a ``requests.Session`` stand-in
(``FakeAlpacaNetwork.session``, pass it as ``session_factory``) and transport
faults, plus a fake UDP socket for discovery.

    net, cam, clock = make_fake(sensor_type=2, bayer_offset_x=1)
    client = AlpacaHttpClient(net.host, net.port, 0, session_factory=net.session)

Transport faults (``net.faults``): ``connection_errors`` / ``timeouts`` (next N
requests raise ``requests.ConnectionError`` / ``ReadTimeout``),
``download_timeouts`` (next N image downloads stall mid-body),
``refused_hosts``. Device faults are on the camera: ``cam.faults``
(0x407 for N requests, Idle-after-start blip, failed exposure, ImageArray
error), ``cam.drop_connection()``, ``cam.renumber(n)``. Every request is
recorded in ``net.requests``; a request without a timeout fails the test.
"""
import importlib.util
import json
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlencode, urlsplit, parse_qsl

import requests
from requests.structures import CaseInsensitiveDict

_SIM_DIR = Path(__file__).resolve().parent.parent / "scripts" / "dev" / "alpaca_sim"


def load_sim_package():
    """Import ``scripts/dev/alpaca_sim`` as ``alpaca_sim`` (no sys.path change)."""
    if "alpaca_sim" in sys.modules:
        return sys.modules["alpaca_sim"]
    spec = importlib.util.spec_from_file_location(
        "alpaca_sim", _SIM_DIR / "__init__.py", submodule_search_locations=[str(_SIM_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["alpaca_sim"] = module
    spec.loader.exec_module(module)
    return module


_sim = load_sim_package()
FakeAlpacaCamera = _sim.FakeAlpacaCamera
SimCameraSpec = _sim.SimCameraSpec
wire = sys.modules["alpaca_sim.wire"]


class FakeClock:
    """Monotonic seconds under test control. ``auto_advance`` ticks per request."""

    def __init__(self, start: float = 1000.0, auto_advance: float = 0.0):
        self.now = float(start)
        self.auto_advance = float(auto_advance)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)

    def sleep(self, seconds: float) -> None:
        """Drop-in for ``time.sleep`` that advances instead of waiting."""
        self.advance(max(0.0, seconds))


class FakeResponse:
    def __init__(self, status: int, content_type: str, body: bytes, url: str,
                 stall_after: Optional[int] = None, on_chunk=None):
        self.status_code = status
        self.headers = CaseInsensitiveDict({"Content-Type": content_type,
                                            "Content-Length": str(len(body))})
        self.content = body
        self.url = url
        self.reason = "OK" if status == 200 else "Error"
        self._stall_after = stall_after
        self._on_chunk = on_chunk
        self.closed = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)

    def iter_content(self, chunk_size: int = 1):
        body = self.content
        if self._stall_after is not None:
            body = body[:self._stall_after]
        sent = 0
        for i in range(0, len(body), chunk_size):
            chunk = body[i:i + chunk_size]
            sent += len(chunk)
            if self._on_chunk is not None:
                self._on_chunk(sent)
            yield chunk
        if self._stall_after is not None:
            raise requests.exceptions.ReadTimeout("fake: download stalled")

    def close(self) -> None:
        self.closed = True


@dataclass
class TransportFaults:
    connection_errors: int = 0
    timeouts: int = 0
    download_timeouts: int = 0
    refused_hosts: set = field(default_factory=set)


@dataclass
class RecordedRequest:
    method: str
    url: str
    member: str
    params: Dict[str, str]
    headers: Dict[str, str]
    timeout: object
    stream: bool


class FakeAlpacaNetwork:
    """Alpaca servers keyed by ``(host, port)``; ``session()`` is a Session stand-in."""

    def __init__(self, clock: Optional[FakeClock] = None):
        self.clock = clock or FakeClock()
        self.servers: Dict[tuple, FakeAlpacaCamera] = {}
        self.faults = TransportFaults()
        self.requests: List[RecordedRequest] = []
        self.on_chunk = None          # callable(bytes_so_far) during image bodies
        self.chunk_size_seen = None
        self.sessions: List["FakeSession"] = []
        self.host, self.port = "192.0.2.10", 11111

    def add_camera(self, camera: FakeAlpacaCamera, host: str = None, port: int = None):
        self.servers[(host or self.host, int(port or self.port))] = camera
        return camera

    def session(self) -> "FakeSession":
        s = FakeSession(self)
        self.sessions.append(s)
        return s

    def member_requests(self, member: str, method: Optional[str] = None):
        return [r for r in self.requests
                if r.member == member and (method is None or r.method == method)]

    def respond(self, method, url, params=None, data=None, headers=None, timeout=None,
                stream=False):
        if timeout is None:
            raise AssertionError(f"Alpaca request without a timeout: {method} {url}")
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port or 80
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({k: str(v) for k, v in (params or {}).items()})
        form = {k: str(v) for k, v in (data or {}).items()}
        member = parts.path.rstrip("/").rsplit("/", 1)[-1].lower()
        self.requests.append(RecordedRequest(method.upper(), url, member,
                                             form if method.upper() == "PUT" else query,
                                             dict(headers or {}), timeout, stream))
        self.clock.advance(self.clock.auto_advance)
        if host in self.faults.refused_hosts:
            raise requests.exceptions.ConnectionError(f"fake: {host} refused")
        if self.faults.connection_errors > 0:
            self.faults.connection_errors -= 1
            raise requests.exceptions.ConnectionError("fake: connection refused")
        if self.faults.timeouts > 0:
            self.faults.timeouts -= 1
            raise requests.exceptions.ReadTimeout("fake: read timed out")
        camera = self.servers.get((host, port))
        if camera is None:
            raise requests.exceptions.ConnectionError(f"fake: nothing listens on {host}:{port}")
        full_url = url + ("?" + urlencode(query) if query else "")
        resp = camera.handle(method, parts.path, query, form, dict(headers or {}))
        stall = None
        if member == "imagearray" and self.faults.download_timeouts > 0:
            self.faults.download_timeouts -= 1
            stall = max(1, len(resp.body) // 2)
        return FakeResponse(resp.status, resp.content_type, resp.body, full_url,
                            stall_after=stall, on_chunk=self.on_chunk if stream else None)


class FakeSession:
    """The ``requests.Session`` surface ``AlpacaHttpClient`` uses."""

    def __init__(self, network: FakeAlpacaNetwork):
        self._net = network
        self.closed = False

    def request(self, method, url, **kwargs):
        return self._net.respond(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self._net.respond("GET", url, **kwargs)

    def put(self, url, **kwargs):
        return self._net.respond("PUT", url, **kwargs)

    def close(self):
        self.closed = True


def make_fake(clock: Optional[FakeClock] = None, **spec_overrides):
    """``(network, camera, clock)``: one camera at ``network.host:network.port``."""
    clock = clock or FakeClock()
    net = FakeAlpacaNetwork(clock)
    spec_overrides.setdefault("width", 16)
    spec_overrides.setdefault("height", 12)
    cam = FakeAlpacaCamera(SimCameraSpec(**spec_overrides), clock=clock)
    net.add_camera(cam)
    return net, cam, clock


# -- discovery ---------------------------------------------------------------
class FakeDiscoverySocket:
    """UDP socket stand-in: replies queue up after a matching ``sendto``.

    ``responders`` maps a target address ("255.255.255.255", "127.0.0.1", …) to
    ``[(source_ip, payload_bytes), …]`` answered each time that target is
    queried. ``recvfrom`` on an empty queue advances ``clock`` by the socket
    timeout and raises ``socket.timeout``, so a discovery run finishes at once.
    """

    def __init__(self, responders: Dict[str, list], clock: FakeClock):
        self.responders = responders
        self.clock = clock
        self.sent: List[tuple] = []
        self._queue: List[tuple] = []
        self._timeout = None
        self.closed = False

    def sendto(self, data: bytes, address):
        self.sent.append((data, address))
        for source, payload in self.responders.get(address[0], []):
            self._queue.append((payload, (source, 32227)))
        return len(data)

    def settimeout(self, value):
        self._timeout = value

    def recvfrom(self, bufsize: int):
        if self._queue:
            return self._queue.pop(0)
        self.clock.advance(self._timeout or 0.01)
        raise socket.timeout("fake: no reply")

    def close(self):
        self.closed = True


def discovery_reply(port: int) -> bytes:
    return json.dumps({"AlpacaPort": port}).encode()
