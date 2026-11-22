"""Find ASCOM Alpaca cameras: UDP discovery, then each server's Management API.

``discover_servers`` broadcasts ``alpacadiscovery1`` to UDP 32227 and collects
``{"AlpacaPort": n}`` replies (the same exchange ``alpyca.discovery`` uses).
``discover_cameras`` asks each server for ``/management/v1/configureddevices``
and keeps ``DeviceType == "Camera"`` only — PFR's own SafetyMonitor answers
discovery on the same port and must never be offered as a camera.

Every wait is bounded: the UDP listen by ``timeout``, each Management call by
the HTTP client's timeouts. Socket and client factories are injectable so the
tests never open a real socket. IPv6 multicast discovery is out of scope.
"""
import json
import socket
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from services.logger import app_logger

from .alpaca_http import AlpacaHttpClient, ci_get
from .alpaca_protocol import DISCOVERY_MESSAGE, DISCOVERY_PORT, AlpacaError
from ..errors import CameraError

DEFAULT_TARGETS = ("255.255.255.255", "127.0.0.1")
_RECV_BYTES = 1024


@dataclass(frozen=True)
class AlpacaDeviceInfo:
    host: str
    port: int
    device_number: int
    name: str
    unique_id: str
    device_type: str = "Camera"

    def label(self) -> str:
        """Display text: ``Name — host:port #n``."""
        return f"{self.name or 'Alpaca camera'} — {self.host}:{self.port} #{self.device_number}"


def _default_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    return sock


def parse_discovery_reply(data: bytes) -> Optional[int]:
    """The AlpacaPort from a discovery reply, or None when it is not one."""
    try:
        obj = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    port = ci_get(obj, "AlpacaPort")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    return port


def discover_servers(timeout: float = 2.0, port: int = DISCOVERY_PORT, *,
                     targets: Iterable[str] = DEFAULT_TARGETS, repeats: int = 2,
                     socket_factory: Callable = _default_socket,
                     clock: Callable[[], float] = time.monotonic) -> List[Tuple[str, int]]:
    """``[(ip, alpaca_port), …]`` of servers that answered, in reply order, deduped.

    The query goes to the broadcast address and to loopback (a server on this
    machine, e.g. ASCOM Remote beside NINA), and is re-sent halfway through
    because UDP may drop it.
    """
    targets = tuple(targets)
    try:
        sock = socket_factory()
    except OSError as exc:
        app_logger.warning(f"Alpaca discovery: cannot open a UDP socket: {exc}")
        return []

    found: List[Tuple[str, int]] = []
    seen = set()
    start = clock()
    deadline = start + max(0.0, float(timeout))
    sends_left = max(1, int(repeats))
    next_send = start
    try:
        while True:
            now = clock()
            if sends_left and now >= next_send:
                for target in targets:
                    try:
                        sock.sendto(DISCOVERY_MESSAGE, (target, port))
                    except OSError as exc:
                        app_logger.debug(f"Alpaca discovery: send to {target} failed: {exc}")
                sends_left -= 1
                next_send = start + (deadline - start) / 2.0
            if now >= deadline:
                break
            wait = deadline - now
            if sends_left:
                wait = min(wait, max(0.01, next_send - now))
            sock.settimeout(max(0.01, wait))
            try:
                data, addr = sock.recvfrom(_RECV_BYTES)
            except socket.timeout:
                continue
            except OSError as exc:
                app_logger.debug(f"Alpaca discovery: receive failed: {exc}")
                break
            alpaca_port = parse_discovery_reply(data)
            if alpaca_port is None:
                continue
            key = (addr[0], alpaca_port)
            if key not in seen:
                seen.add(key)
                found.append(key)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return found


def cameras_on_server(host: str, port: int, *, client_factory: Callable = AlpacaHttpClient,
                      timeout: float = 3.0) -> List[AlpacaDeviceInfo]:
    """The Camera devices one server lists in ``configureddevices``.

    Raises the client's ``CameraError`` subclasses when the server cannot be
    asked; an empty list means it answered with no camera.
    """
    client = client_factory(host, port, 0, connect_timeout=timeout, request_timeout=timeout)
    try:
        devices = client.management_configured_devices()
    finally:
        try:
            client.close()
        except Exception:
            pass
    cameras = []
    for device in devices:
        if str(ci_get(device, "DeviceType", "")).strip().lower() != "camera":
            continue
        try:
            number = int(ci_get(device, "DeviceNumber"))
        except (TypeError, ValueError):
            continue
        cameras.append(AlpacaDeviceInfo(
            host=host, port=int(port), device_number=number,
            name=str(ci_get(device, "DeviceName", "") or ""),
            unique_id=str(ci_get(device, "UniqueID", "") or ""),
            device_type=str(ci_get(device, "DeviceType", "Camera"))))
    return cameras


def discover_cameras(timeout: float = 2.0, *, port: int = DISCOVERY_PORT,
                     socket_factory: Callable = _default_socket,
                     client_factory: Callable = AlpacaHttpClient,
                     http_timeout: float = 3.0,
                     clock: Callable[[], float] = time.monotonic) -> List[AlpacaDeviceInfo]:
    """Every Alpaca camera that answered discovery, one entry per device.

    A server that fails its Management call is logged and skipped. A device
    seen through two addresses (loopback and LAN) is listed once, under the
    address that answered first.
    """
    cameras: List[AlpacaDeviceInfo] = []
    seen_ids = set()
    seen_addr = set()
    for host, alpaca_port in discover_servers(timeout, port, socket_factory=socket_factory,
                                              clock=clock):
        try:
            found = cameras_on_server(host, alpaca_port, client_factory=client_factory,
                                      timeout=http_timeout)
        except (CameraError, AlpacaError) as exc:
            app_logger.warning(f"Alpaca discovery: {host}:{alpaca_port} did not list its "
                               f"devices: {exc}")
            continue
        for cam in found:
            addr = (cam.host, cam.port, cam.device_number)
            if addr in seen_addr or (cam.unique_id and cam.unique_id in seen_ids):
                continue
            seen_addr.add(addr)
            if cam.unique_id:
                seen_ids.add(cam.unique_id)
            cameras.append(cam)
    return cameras
