"""CameraCapabilities: the static set per camera source and ZWO's connected refinement (issue #43)."""
import dataclasses
from types import SimpleNamespace

import pytest

from services.camera.capabilities import (
    ALPACA_STATIC,
    ZWO_STATIC,
    ZWO_TROUBLESHOOTING_HINTS,
    CameraCapabilities,
    static_capabilities,
    zwo_capabilities,
)
from services.host_platform import IS_WINDOWS


def test_capabilities_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ZWO_STATIC.gain = False


@pytest.mark.parametrize('source, expected', [
    ('zwo', ZWO_STATIC), ('alpaca', ALPACA_STATIC), ('bogus', ZWO_STATIC), ('', ZWO_STATIC),
])
def test_static_capabilities_by_source(source, expected):
    assert static_capabilities(source) is expected


def test_zwo_static_shows_the_usb_controls():
    caps = ZWO_STATIC
    assert caps.backend == 'zwo'
    assert caps.needs_sdk_path and caps.index_selection and not caps.network_connection
    assert caps.hardware_white_balance and caps.flip and caps.offset and caps.gain
    assert caps.usb_reset is IS_WINDOWS
    assert not caps.bayer_from_device and caps.bayer_pattern is None
    assert caps.gain_range == (0, 600) and caps.offset_range == (0, 255)


def test_alpaca_static_hides_what_only_usb_has():
    caps = ALPACA_STATIC
    assert caps.backend == 'alpaca'
    assert caps.network_connection and caps.bayer_from_device
    assert not (caps.needs_sdk_path or caps.index_selection or caps.usb_reset
                or caps.hardware_white_balance or caps.raw16_toggle)
    assert caps.flip and caps.troubleshooting_hints
    assert caps.troubleshooting_hints != ZWO_TROUBLESHOOTING_HINTS


def test_zwo_hints_are_the_ones_the_loop_always_printed():
    from tests.test_camera_session_contract import ZWO_TROUBLESHOOTING_LINES
    printed = tuple(f"  {n}. {hint}" for n, hint in enumerate(ZWO_STATIC.troubleshooting_hints, 1))
    assert printed == ZWO_TROUBLESHOOTING_LINES


def test_zwo_capabilities_follow_the_open_connection():
    caps = zwo_capabilities(SimpleNamespace(supports_raw16=True, bit_depth=12))
    assert caps.raw16_toggle is True and caps.bit_depth == 12
    assert dataclasses.replace(caps, raw16_toggle=False, bit_depth=None) == ZWO_STATIC


def test_zwo_capabilities_before_connect():
    caps = zwo_capabilities(SimpleNamespace())
    assert caps.raw16_toggle is False and caps.bit_depth is None


def test_zwo_session_reports_its_capabilities():
    from unittest.mock import MagicMock, patch
    from services.camera.zwo_camera import ZWOCamera
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(supports_raw16=True, bit_depth=14)
        cam = ZWOCamera(sdk_path=None)
    assert isinstance(cam.capabilities, CameraCapabilities)
    assert cam.capabilities.raw16_toggle is True and cam.capabilities.bit_depth == 14


def test_every_field_is_set_on_both_static_sets():
    names = {f.name for f in dataclasses.fields(CameraCapabilities)}
    assert names == set(dataclasses.asdict(ZWO_STATIC)) == set(dataclasses.asdict(ALPACA_STATIC))
