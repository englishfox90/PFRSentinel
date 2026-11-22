"""ASCOM Alpaca camera simulator: a real HTTP Alpaca server plus UDP discovery.

Dev tool, not shipped. Serves one simulated camera (``alpaca_sim.device``)
at ``/api/v1/camera/<n>/<member>`` and the Management API, answers
``alpacadiscovery1`` on UDP 32227, renders a night-sky scene whose brightness
follows Duration x Gain, and speaks both ImageBytes and JSON ImageArray. It
is accepted by ASCOM's ``alpyca`` client and by PFR Sentinel's own.

    python scripts/dev/alpaca_camera_sim.py --port 11112
    python scripts/dev/alpaca_camera_sim.py --sensor-type 2 --bayer-offset-x 1 \
        --bayer-offset-y 1 --width 1920 --height 1080 --max-adu 4095

Port 11111 (the Alpaca default) is also PFR's own SafetyMonitor default;
pick another when both run on this machine. ``--port 0`` takes a free port and
logs it as ``ALPACA_SIM_PORT=<n>``.
"""
import argparse
import json
import logging
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from alpaca_sim import SCENES, FakeAlpacaCamera, SimCameraSpec

LOG = logging.getLogger("alpaca_camera_sim")
DISCOVERY_PORT = 32227
DISCOVERY_MESSAGE = b"alpacadiscovery"   # followed by the protocol version digit
MAX_BODY = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PFRAlpacaSim/1.0"

    def do_GET(self):
        self._dispatch("GET")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_POST(self):
        self._send(405, "text/plain", b"Alpaca uses GET and PUT")

    def _dispatch(self, method: str):
        url = urlsplit(self.path)
        query = dict(parse_qsl(url.query, keep_blank_values=True))
        form = {}
        if method == "PUT":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                self._send(400, "text/plain", b"Bad Content-Length")
                return
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            form = dict(parse_qsl(body, keep_blank_values=True))
        headers = {k: v for k, v in self.headers.items()}
        try:
            resp = self.server.camera.handle(method, url.path, query, form, headers)
        except Exception as exc:  # a simulator bug must not kill the server
            LOG.exception("Simulator failed on %s %s", method, self.path)
            self._send(500, "text/plain", f"Simulator error: {exc}".encode())
            return
        self._send(resp.status, resp.content_type, resp.body)

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        LOG.debug("%s - %s", self.address_string(), format % args)


class SimServer:
    """The HTTP server and discovery responder for one ``FakeAlpacaCamera``."""

    def __init__(self, camera: FakeAlpacaCamera, host: str = "127.0.0.1", port: int = 0,
                 discovery_port: Optional[int] = DISCOVERY_PORT,
                 discovery_bind: str = ""):
        self.camera = camera
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.camera = camera
        self._stop = threading.Event()
        self._threads = []
        self._udp = None
        if discovery_port is not None:
            self._udp = self._open_discovery(discovery_bind, discovery_port)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def discovery_port(self) -> Optional[int]:
        return self._udp.getsockname()[1] if self._udp else None

    def _open_discovery(self, bind: str, port: int):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind, port))
        except OSError as exc:
            LOG.warning("Discovery disabled: cannot bind UDP %s: %s", port, exc)
            sock.close()
            return None
        sock.settimeout(0.5)
        return sock

    def start(self) -> "SimServer":
        self._spawn(self._httpd.serve_forever, "alpaca-sim-http")
        if self._udp:
            self._spawn(self._discovery_loop, "alpaca-sim-discovery")
        return self

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _discovery_loop(self):
        reply = json.dumps({"AlpacaPort": self.port}).encode()
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.startswith(DISCOVERY_MESSAGE):
                try:
                    self._udp.sendto(reply, addr)
                except OSError as exc:
                    LOG.debug("Discovery reply to %s failed: %s", addr, exc)

    def stop(self) -> None:
        self._stop.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._udp:
            self._udp.close()
        for t in self._threads:
            t.join(timeout=2.0)


def build_parser() -> argparse.ArgumentParser:
    d = SimCameraSpec()
    p = argparse.ArgumentParser(description="ASCOM Alpaca camera simulator (PFR Sentinel dev tool)")
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind address (0.0.0.0 for the LAN)")
    p.add_argument("--port", type=int, default=11112, help="HTTP port; 0 picks a free one")
    p.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT)
    p.add_argument("--no-discovery", action="store_true", help="Do not answer UDP discovery")
    p.add_argument("--device-number", type=int, default=d.device_number)
    p.add_argument("--name", default=d.name)
    p.add_argument("--unique-id", default=d.unique_id)
    p.add_argument("--interface-version", type=int, choices=(3, 4), default=d.interface_version)
    p.add_argument("--sensor-type", type=int, choices=range(6), default=d.sensor_type,
                   help="0 mono, 1 colour (RGB planes), 2 RGGB Bayer, 3-5 CMYG/CMYG2/LRGB")
    p.add_argument("--bayer-offset-x", type=int, choices=(0, 1), default=d.bayer_offset_x)
    p.add_argument("--bayer-offset-y", type=int, choices=(0, 1), default=d.bayer_offset_y)
    p.add_argument("--width", type=int, default=d.width)
    p.add_argument("--height", type=int, default=d.height)
    p.add_argument("--max-adu", type=int, default=d.max_adu)
    p.add_argument("--gain-mode", choices=("range", "list", "none"), default=d.gain_mode)
    p.add_argument("--offset-mode", choices=("range", "list", "none"), default=d.offset_mode)
    p.add_argument("--no-temperature", action="store_true", help="CCDTemperature NotImplemented")
    p.add_argument("--readout-s", type=float, default=d.readout_s)
    p.add_argument("--scene", choices=SCENES, default=d.scene)
    p.add_argument("--no-imagebytes", action="store_true", help="Serve JSON ImageArray only")
    p.add_argument("--lenient-params", action="store_true",
                   help="Accept PUT parameter names in any case")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def spec_from_args(args) -> SimCameraSpec:
    return SimCameraSpec(
        name=args.name, unique_id=args.unique_id, device_number=args.device_number,
        interface_version=args.interface_version, sensor_type=args.sensor_type,
        bayer_offset_x=args.bayer_offset_x, bayer_offset_y=args.bayer_offset_y,
        width=args.width, height=args.height, max_adu=args.max_adu,
        gain_mode=args.gain_mode, offset_mode=args.offset_mode,
        ccd_temperature=None if args.no_temperature else SimCameraSpec().ccd_temperature,
        readout_s=args.readout_s, scene=args.scene,
        supports_imagebytes=not args.no_imagebytes,
        strict_param_case=not args.lenient_params)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    server = SimServer(FakeAlpacaCamera(spec_from_args(args)), host=args.host, port=args.port,
                       discovery_port=None if args.no_discovery else args.discovery_port)
    server.start()
    LOG.info("ALPACA_SIM_PORT=%d", server.port)
    LOG.info("Alpaca camera simulator on http://%s:%d/api/v1/camera/%d/ (discovery %s)",
             args.host, server.port, args.device_number,
             f"UDP {server.discovery_port}" if server.discovery_port else "off")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        LOG.info("Stopping")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
