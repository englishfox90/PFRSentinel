"""ZWOCamera.request_immediate_capture() — the one-shot wake the diagnostics
export uses to skip the rest of a long inter-frame wait."""
from unittest.mock import MagicMock, patch


def _make_camera():
    from services.camera import ZWOCamera
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(asi=None, camera=None, sdk_lock=MagicMock())
        return ZWOCamera(sdk_path=None)


def test_consume_is_false_until_requested():
    cam = _make_camera()
    assert cam.consume_immediate_capture() is False


def test_request_is_consumed_exactly_once():
    cam = _make_camera()
    cam.request_immediate_capture()
    cam.request_immediate_capture()  # repeated requests coalesce into one
    assert cam.consume_immediate_capture() is True
    assert cam.consume_immediate_capture() is False
