"""How long one Alpaca frame may take: the download deadline and the watchdog budget.

A dead link is caught by the per-recv stall timeout, so the total download
deadline only has to stop a link that crawls. It is therefore generous: three
times the last measured transfer time plus one stall, or — before anything has
been measured — the frame's worst-case size at a slow-VPN floor of
``FLOOR_BYTES_PER_S``. Never less than ``MIN_DOWNLOAD_TIMEOUT_S``.

``frame_budget_s`` is exposure + readout grace + that download deadline: what
the UI watchdog must wait before it calls an Alpaca capture wedged, so a long
exposure over a slow link is not "recovered" (and its download cancelled)
every frame.
"""
from .alpaca_sensor import RGB

EXPOSURE_GRACE_S = 30.0
MIN_DOWNLOAD_TIMEOUT_S = 30.0
MAX_DOWNLOAD_TIMEOUT_S = 1800.0
FLOOR_BYTES_PER_S = 250_000
MEASURED_MARGIN = 3.0
WATCHDOG_MARGIN_S = 60.0


def expected_bytes(info, use_imagebytes: bool) -> int:
    """Worst-case body size: Int32 ImageBytes, or ~12 bytes per pixel of JSON."""
    planes = 3 if info.bayer_pattern == RGB else 1
    per_pixel = 4 if use_imagebytes else 12
    return int(info.width) * int(info.height) * planes * per_pixel


def download_timeout_s(info, use_imagebytes: bool, *, last_bytes=None, last_s=None,
                       stall_s: float = 10.0) -> float:
    """The total deadline for the next download (see the module docstring)."""
    try:
        measured = float(last_s) > 0 and int(last_bytes) > 0
    except (TypeError, ValueError):
        measured = False
    if measured:
        budget = MEASURED_MARGIN * float(last_s) + stall_s
    else:
        budget = expected_bytes(info, use_imagebytes) / FLOOR_BYTES_PER_S + stall_s
    return min(MAX_DOWNLOAD_TIMEOUT_S, max(MIN_DOWNLOAD_TIMEOUT_S, budget))


def session_download_timeout(session, info, device) -> float:
    client = getattr(session._connection, "client", None)
    stall = getattr(client, "request_timeout", 10.0) or 10.0
    return download_timeout_s(info, device.use_imagebytes,
                              last_bytes=getattr(session, "last_download_bytes", None),
                              last_s=getattr(session, "last_download_s", None),
                              stall_s=float(stall))


def frame_budget_s(session) -> float:
    """Seconds one frame may legitimately take, for the capture watchdog."""
    exposure = float(getattr(session, "exposure_seconds", 0.0) or 0.0)
    conn = session._connection
    info = conn.static_info
    device = conn.camera
    if info is None or device is None:
        download = MIN_DOWNLOAD_TIMEOUT_S
    else:
        download = session_download_timeout(session, info, device)
    return exposure + EXPOSURE_GRACE_S + download + WATCHDOG_MARGIN_S


__all__ = ["download_timeout_s", "expected_bytes", "frame_budget_s",
           "session_download_timeout", "EXPOSURE_GRACE_S", "MIN_DOWNLOAD_TIMEOUT_S"]
