"""Smoke tests against a real ZWO camera, on whichever OS the SDK loads.

Run on the rig after an SDK or OS change:
    pytest -m requires_camera tests/test_camera_hardware.py
"""
import pytest

from services.host_platform import zwo_sdk_library_name
from services.zwo_sdk_library import find_sdk_library

pytestmark = pytest.mark.requires_camera


@pytest.fixture
def zwoasi_module():
    zwoasi = pytest.importorskip("zwoasi")
    library = find_sdk_library()
    if not library:
        pytest.skip(f"{zwo_sdk_library_name()} not found on this machine")
    # A library that is found but won't load is a failure, not a skip:
    # that is exactly what this file exists to catch on a new OS.
    zwoasi.init(library)
    if zwoasi.get_num_cameras() == 0:
        pytest.skip("No camera connected")
    return zwoasi


def test_camera_detection(zwoasi_module):
    assert zwoasi_module.get_num_cameras() > 0


def test_camera_connection(zwoasi_module):
    camera = zwoasi_module.Camera(0)
    try:
        info = camera.get_camera_property()
        assert 'Name' in info
        assert 'MaxWidth' in info
        assert 'MaxHeight' in info
    finally:
        camera.close()


def test_camera_serial_readable(zwoasi_module):
    from services.camera.camera_identity import read_serial
    camera = zwoasi_module.Camera(0)
    try:
        serial = read_serial(camera)
        assert serial, "ASIGetSerialNumber returned nothing"
    finally:
        camera.close()


def test_capture_single_frame(zwoasi_module):
    camera = zwoasi_module.Camera(0)
    try:
        camera.set_control_value(zwoasi_module.ASI_EXPOSURE, 1000)  # µs, i.e. 1 ms
        camera.set_control_value(zwoasi_module.ASI_GAIN, 0)
        camera.set_image_type(zwoasi_module.ASI_IMG_RAW8)
        data = camera.capture()
        assert data is not None
        assert len(data) > 0
    finally:
        camera.close()
