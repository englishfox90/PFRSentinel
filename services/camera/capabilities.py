"""What a camera backend can do, for the UI and the controller to gate on.

A static set per source is known before connecting (which widgets to show);
a session refines it once the camera is open (RAW16 support, bit depth, and for
Alpaca the reported Bayer pattern and control ranges). Pure data.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..host_platform import IS_WINDOWS

# Printed by the capture loop, numbered, when a camera stays unreachable.
ZWO_TROUBLESHOOTING_HINTS = (
    "Check USB cable connection",
    "Check camera power supply",
    "Try: Physically disconnect USB, wait 5 seconds, reconnect",
    "Check Windows Device Manager for USB errors",
    "If persistent: Update ZWO drivers from astronomy-imaging-camera.com",
)

ALPACA_TROUBLESHOOTING_HINTS = (
    "Check the Alpaca server is running and reachable from this computer",
    "Check the camera is connected in the server (e.g. ASCOM Remote)",
    "Check a firewall is not blocking the server's port",
    "Check the device number matches the camera on that server",
)


@dataclass(frozen=True)
class CameraCapabilities:
    backend: str
    display_name: str
    needs_sdk_path: bool
    index_selection: bool
    network_connection: bool
    usb_reset: bool
    hardware_white_balance: bool
    raw16_toggle: bool
    flip: bool
    offset: bool
    gain: bool
    bayer_from_device: bool
    bayer_pattern: str | None
    bit_depth: int | None
    gain_range: tuple[int, int] | None
    offset_range: tuple[int, int] | None
    exposure_range_s: tuple[float, float] | None
    temperature: bool
    troubleshooting_hints: tuple[str, ...]


ZWO_STATIC = CameraCapabilities(
    backend='zwo',
    display_name='ZWO ASI (USB)',
    needs_sdk_path=True,
    index_selection=True,
    network_connection=False,
    usb_reset=IS_WINDOWS,
    hardware_white_balance=True,
    raw16_toggle=False,
    flip=True,
    offset=True,
    gain=True,
    bayer_from_device=False,
    bayer_pattern=None,
    bit_depth=None,
    gain_range=(0, 600),
    offset_range=(0, 255),
    exposure_range_s=(0.000001, 3600.0),
    temperature=True,
    troubleshooting_hints=ZWO_TROUBLESHOOTING_HINTS,
)

ALPACA_STATIC = CameraCapabilities(
    backend='alpaca',
    display_name='ASCOM Alpaca (network)',
    needs_sdk_path=False,
    index_selection=False,
    network_connection=True,
    usb_reset=False,
    hardware_white_balance=False,
    raw16_toggle=False,
    flip=True,
    offset=True,
    gain=True,
    bayer_from_device=True,
    bayer_pattern=None,
    bit_depth=None,
    gain_range=None,
    offset_range=None,
    exposure_range_s=None,
    temperature=True,
    troubleshooting_hints=ALPACA_TROUBLESHOOTING_HINTS,
)

_STATIC = {'zwo': ZWO_STATIC, 'alpaca': ALPACA_STATIC}


def static_capabilities(source: str) -> CameraCapabilities:
    """The before-connect capabilities for ``source``; an unknown source reads as ZWO."""
    return _STATIC.get(source, ZWO_STATIC)


def zwo_capabilities(connection) -> CameraCapabilities:
    """``ZWO_STATIC`` refined by an open ``CameraConnection``."""
    return replace(
        ZWO_STATIC,
        raw16_toggle=bool(getattr(connection, 'supports_raw16', False)),
        bit_depth=getattr(connection, 'bit_depth', None),
    )
