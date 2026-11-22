"""Find ASCOM Alpaca cameras: UDP discovery, then each server's Management API.

``discover_servers`` broadcasts ``alpacadiscovery1`` to UDP 32227 and collects
``{"AlpacaPort": n}`` replies (the same exchange ``alpyca.discovery`` uses).
On a multi-homed host 255.255.255.255 leaves through one interface only, so the
query also goes to every IPv4 interface's directed broadcast (``ifaddr``) — a
camera on a ZeroTier / VPN subnet or a second NIC is found too. If interfaces
cannot be listed, the limited broadcast and loopback still go out.
``discover_cameras`` asks each server for ``/management/v1/configureddevices``
and keeps ``DeviceType == "Camera"`` only — PFR's own SafetyMonitor answers
discovery on the same port and must never be offered as a camera.

Every wait is bounded: the UDP listen by ``timeout``, each Management call by
the HTTP client's timeouts. Socket and client factories are injectable so the
tests never open a real socket. IPv6 multicast discovery is out of scope.
"""
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from services.logger import app_logger

try:
    import ifaddr
except ImportError:  # pragma: no cover - a pinned runtime dependency
    ifaddr = None

from .alpaca_http import AlpacaHttpClient, ci_get
from .alpaca_protocol import DISCOVERY_MESSAGE, DISCOVERY_PORT, AlpacaError
from ..errors import CameraError

DEFAULT_TARGETS = ("255.255.255.255", "127.0.0.1")
_MAX_INTERFACE_TARGETS = 32
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
    # No explicit bind: the first sendto binds an ephemeral port, which is all
    # a discovery client needs, and an all-interfaces bind would be a listening
    # socket in CodeQL's eyes (py/bind-socket-all-network-interfaces). If every
    # send fails, recvfrom on the never-bound socket raises and the receive
    # loop ends with nothing found, which is the right answer.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return sock


def _adapters():
    if ifaddr is None:
        return []
    return ifaddr.get_adapters()


def interface_broadcasts(adapters_fn: Optional[Callable] = None) -> List[str]:
    """Directed broadcast of every IPv4 interface (loopback and /31-/32 skipped)."""
    out: List[str] = []
    try:
        adapters = list((adapters_fn or _adapters)())
    except Exception as exc:
        app_logger.debug(f"Alpaca discovery: cannot list network interfaces: {exc}")
        return out
    for adapter in adapters:
        for ip in getattr(adapter, "ips", ()) or ():
            address = getattr(ip, "ip", None)
            if not isinstance(address, str):
                continue  # IPv6 addresses come as tuples
            try:
                net = ipaddress.IPv4Interface(f"{address}/{int(ip.network_prefix)}").network
            except (ValueError, TypeError):
                continue
            if net.is_loopback or net.prefixlen >= 31:
                continue
            target = str(net.broadcast_address)
            if target not in out:
                out.append(target)
    return out[:_MAX_INTERFACE_TARGETS]


def discovery_targets(adapters_fn: Optional[Callable] = None) -> tuple:
    """Limited broadcast, loopback, then each interface's directed broadcast."""
    targets = list(DEFAULT_TARGETS)
    for target in interface_broadcasts(adapters_fn):
        if target not in targets:
            targets.append(target)
    return tuple(targets)


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
                     targets: Optional[Iterable[str]] = None, repeats: int = 2,
                     socket_factory: Callable = _default_socket,
                     clock: Callable[[], float] = time.monotonic) -> List[Tuple[str, int]]:
    """``[(ip, alpaca_port), …]`` of servers that answered, in reply order, deduped.

    The query goes to the broadcast address, to loopback (a server on this
    machine, e.g. ASCOM Remote beside NINA) and to each interface's directed
    broadcast (``discovery_targets``), and is re-sent halfway through because
    UDP may drop it.
    """
    targets = discovery_targets() if targets is None else tuple(targets)
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

    A server that fails its Management call is skipped (DEBUG; one INFO line
    sums the run up). A device seen through two addresses (loopback and LAN)
    is listed once, under the address that answered first.
    """
    cameras: List[AlpacaDeviceInfo] = []
    seen_ids = set()
    seen_addr = set()
    servers = discover_servers(timeout, port, socket_factory=socket_factory, clock=clock)
    unlisted = []
    for host, alpaca_port in servers:
        try:
            found = cameras_on_server(host, alpaca_port, client_factory=client_factory,
                                      timeout=http_timeout)
        except (CameraError, AlpacaError) as exc:
            unlisted.append(f"{host}:{alpaca_port}")
            app_logger.debug(f"Alpaca discovery: {host}:{alpaca_port} did not list its "
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
    summary = (f"Alpaca discovery: {len(servers)} server(s) answered, "
               f"{len(cameras)} camera(s) found")
    if unlisted:
        summary += f"; {', '.join(unlisted)} did not list devices (details at DEBUG)"
    app_logger.info(summary)
    return cameras
