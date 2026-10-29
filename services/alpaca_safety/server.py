"""
HTTP + UDP discovery transport for the Alpaca SafetyMonitor.

The HTTP side hands every request to SafetyMonitorDevice; the UDP side answers
Alpaca discovery ("alpacadiscovery1" on port 32227) with the HTTP port.
"""
import json
import socket
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse, parse_qsl

from services.logger import app_logger

from .protocol import SafetyMonitorDevice

DISCOVERY_PORT = 32227
DISCOVERY_MESSAGE = b"alpacadiscovery1"
_MAX_BODY_BYTES = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    device: SafetyMonitorDevice = None  # set on the per-server subclass

    def do_GET(self):
        url = urlparse(self.path)
        self._respond(*self.device.handle('GET', url.path, dict(parse_qsl(url.query))))

    def do_PUT(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY_BYTES:
            self._respond(400, 'text/plain; charset=utf-8', b"Bad Content-Length")
            return
        body = self.rfile.read(length).decode('utf-8', errors='replace') if length else ''
        params = dict(parse_qsl(url.query))
        params.update(parse_qsl(body))
        self._respond(*self.device.handle('PUT', url.path, params))

    def _respond(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        app_logger.debug(f"Alpaca SafetyMonitor HTTP: {format % args}")


class AlpacaSafetyServer:
    def __init__(self, device: SafetyMonitorDevice, bind: str = '0.0.0.0', port: int = 11111,
                 discovery: bool = True, discovery_port: int = DISCOVERY_PORT):
        self._device = device
        self.bind = bind
        self.requested_port = port
        self.discovery = discovery
        self._discovery_port = discovery_port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._udp: Optional[socket.socket] = None
        self._threads = []
        self._stop = threading.Event()

    @property
    def port(self) -> Optional[int]:
        return self._httpd.server_address[1] if self._httpd else None

    def start(self) -> bool:
        handler = type('SafetyMonitorHandler', (_Handler,), {'device': self._device})
        try:
            self._httpd = ThreadingHTTPServer((self.bind, self.requested_port), handler)
        except OSError as e:
            app_logger.error(f"Alpaca SafetyMonitor: can't listen on {self.bind}:{self.requested_port}: {e}")
            self._httpd = None
            return False
        self._httpd.daemon_threads = True
        self._stop.clear()
        self._spawn(self._httpd.serve_forever, 'AlpacaSafetyHTTP')

        if self.discovery:
            self._udp = self._open_discovery_socket()
            if self._udp is not None:
                self._spawn(self._discovery_loop, 'AlpacaSafetyDiscovery')

        app_logger.info(f"Alpaca SafetyMonitor listening on {self.bind}:{self.port}"
                        f"{' (discoverable)' if self._udp else ''}")
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._udp is not None:
            self._udp.close()
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []
        self._httpd = None
        self._udp = None

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _open_discovery_socket(self) -> Optional[socket.socket]:
        # Other Alpaca servers on this host (ASCOM Remote, INDIGO's bridge) listen on
        # the same port; REUSEPORT lets each of them see the broadcast too.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            s.bind(('', self._discovery_port))
            s.settimeout(0.5)
            return s
        except OSError as e:
            s.close()
            app_logger.warning(f"Alpaca SafetyMonitor: discovery off, can't bind UDP {self._discovery_port}: {e}")
            return None

    def _discovery_loop(self):
        reply = json.dumps({"AlpacaPort": self.port}).encode('utf-8')
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.startswith(DISCOVERY_MESSAGE):
                try:
                    self._udp.sendto(reply, addr)
                except OSError as e:
                    app_logger.debug(f"Alpaca SafetyMonitor: discovery reply to {addr} failed: {e}")
