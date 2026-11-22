"""Test Connection for an Alpaca camera: connect, read what it is, let go.

``probe_alpaca_camera(settings)`` takes ``camera_source.alpaca_settings(config)``
and never raises. It does not configure the camera (no gain, offset or ROI
writes: another client may be imaging with it), and it releases the device
afterwards only when ``release_on_disconnect`` is set — the same rule a
capture session follows. The whole probe runs on a worker thread joined with a
deadline, so a server that accepts connections and then stalls every reply
still answers the caller in bounded time.
"""
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from services.logger import app_logger

from .alpaca_connection import AlpacaConnection, lan_session
from .alpaca_sensor import MONO, RGB

PROBE_CONNECT_TIMEOUT_S = 10.0
MIN_PROBE_DEADLINE_S = 15.0
MAX_PROBE_DEADLINE_S = 60.0


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str
    details: dict = field(default_factory=dict)


def _probe_deadline(settings: dict) -> float:
    connect = float(settings.get("connect_timeout_s", 3.0) or 3.0)
    request = float(settings.get("request_timeout_s", 10.0) or 10.0)
    budget = PROBE_CONNECT_TIMEOUT_S + 2 * (connect + request)
    return min(MAX_PROBE_DEADLINE_S, max(MIN_PROBE_DEADLINE_S, budget))


def _pattern_label(physical: str) -> str:
    if physical == MONO:
        return "mono"
    if physical == RGB:
        return "RGB"
    return physical


def summary(details: dict) -> str:
    """``Connected: <Name> 1920×1080, RGGB, 12-bit``."""
    return (f"Connected: {details['name']} {details['width']}×{details['height']}, "
            f"{_pattern_label(details['physical_bayer_pattern'])}, "
            f"{details['bit_depth']}-bit")


def _details(conn: AlpacaConnection) -> dict:
    info = conn.static_info
    device = conn.camera
    return {
        "host": conn.host,
        "port": conn.port,
        "name": info.name,
        "unique_id": conn.unique_id,
        "device_number": conn.device_number,
        "sensor_type": info.sensor_type,
        "bayer_pattern": info.bayer_pattern,
        "physical_bayer_pattern": info.physical_bayer_pattern,
        "width": info.width,
        "height": info.height,
        "max_adu": info.max_adu,
        "bit_depth": info.sensor_bit_depth,
        "gain_range": info.gain_range,
        "offset_range": info.offset_range,
        "exposure_range_s": info.exposure_range_s,
        "interface_version": info.interface_version,
        "driver_info": info.driver_info,
        "imagebytes": getattr(device, "imagebytes_used", None),
    }


def _run(settings: dict, session_factory: Callable) -> ProbeResult:
    conn = AlpacaConnection(settings, logger=app_logger.debug, session_factory=session_factory,
                            connect_timeout_s=PROBE_CONNECT_TIMEOUT_S)
    base = {"host": conn.host, "port": conn.port, "device_number": conn.device_number}
    try:
        if not conn.connect(configure=False):
            return ProbeResult(False, conn.last_error or "Connection failed", base)
        details = _details(conn)
        return ProbeResult(True, summary(details), details)
    finally:
        conn.disconnect()


def probe_alpaca_camera(settings: dict, *, session_factory: Callable = lan_session,
                        deadline_s: Optional[float] = None) -> ProbeResult:
    """Connect to the configured camera, describe it, and disconnect. Never raises."""
    settings = dict(settings or {})
    deadline = _probe_deadline(settings) if deadline_s is None else float(deadline_s)
    box = {}

    def work():
        try:
            box["result"] = _run(settings, session_factory)
        except Exception as exc:
            box["result"] = ProbeResult(False, f"Probe failed: {exc}",
                                        {"host": settings.get("host", "")})

    worker = threading.Thread(target=work, name="alpaca-probe", daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        host = settings.get("host", "")
        return ProbeResult(False, f"No answer from {host}:{settings.get('port')} within "
                                  f"{deadline:.0f} s", {"host": host})
    return box.get("result") or ProbeResult(False, "Probe failed", {})
