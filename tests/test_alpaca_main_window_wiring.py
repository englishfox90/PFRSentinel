"""Main-window wiring for the Alpaca source: Start gating, no SDK auto-detect,
Discover / Test routed to the controller and back to the panel."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.camera.capabilities import ALPACA_STATIC
from services.config import Config


@pytest.fixture
def config(tmp_path):
    c = Config(str(tmp_path / "config.json"))
    c.set('capture_mode', 'camera')
    c.set('camera_source', 'alpaca')
    return c


def _window(config, **kw):
    """A stand-in main window: the real mixin methods over mocked widgets."""
    from ui.main_window.capture import _MainWindowCaptureMixin

    class _Window(_MainWindowCaptureMixin):
        pass

    w = _Window()
    w.__dict__.update(config=config, is_capturing=False, camera_controller=None,
                      app_bar=MagicMock(), capture_panel=MagicMock(), _notify=MagicMock(),
                      alpaca_discovery=MagicMock())
    w.__dict__.update(kw)
    return w


def test_start_needs_an_alpaca_host_not_detected_cameras(config):
    from ui.main_window.capture import _MainWindowCaptureMixin
    w = _window(config)
    _MainWindowCaptureMixin._update_start_button(w)
    enabled, tip = w.app_bar.set_start_enabled.call_args.args
    assert enabled is False and 'Alpaca' in tip

    config.set('alpaca_camera', {**config.get('alpaca_camera'), 'host': '10.0.0.2'})
    _MainWindowCaptureMixin._update_start_button(w)
    assert w.app_bar.set_start_enabled.call_args.args == (True,)


def test_zwo_start_gate_is_unchanged(config):
    from ui.main_window.capture import _MainWindowCaptureMixin
    config.set('camera_source', 'zwo')
    config.set('alpaca_camera', {**config.get('alpaca_camera'), 'host': '10.0.0.2'})
    w = _window(config)
    _MainWindowCaptureMixin._update_start_button(w)
    enabled, tip = w.app_bar.set_start_enabled.call_args.args
    assert enabled is False and 'ZWO' in tip


def test_startup_auto_detect_skips_the_sdk_for_alpaca(config):
    from ui.main_window.camera_detect import _CameraDetectMixin
    w = _window(config, _on_detect_cameras=MagicMock())
    _CameraDetectMixin._auto_detect_cameras(w)
    w._on_detect_cameras.assert_not_called()


def test_connect_camera_on_an_alpaca_rig_discovers(config):
    from ui.main_window.camera_detect import _CameraDetectMixin
    w = _window(config, _on_alpaca_discover=MagicMock())
    _CameraDetectMixin._on_detect_cameras(w)
    w._on_alpaca_discover.assert_called_once()
    w.capture_panel.set_detecting.assert_not_called()


def test_discover_and_results_reach_the_panel(config):
    from ui.main_window.alpaca_camera import _AlpacaCameraMixin
    w = _window(config)
    w.alpaca_discovery.discover.return_value = True
    _AlpacaCameraMixin._on_alpaca_discover(w)
    w.capture_panel.set_alpaca_discovering.assert_called_with(True)
    _AlpacaCameraMixin._on_alpaca_devices_found(w, ['dev'], '')
    w.capture_panel.set_alpaca_discovering.assert_called_with(False)
    w.capture_panel.set_alpaca_devices.assert_called_with(['dev'], '')


def test_test_without_a_host_never_reaches_the_controller(config):
    from ui.main_window.alpaca_camera import _AlpacaCameraMixin
    w = _window(config)
    _AlpacaCameraMixin._on_alpaca_test(w)
    w.alpaca_discovery.test_connection.assert_not_called()
    ok = w.capture_panel.set_alpaca_test_result.call_args.args[0]
    assert ok is False


def test_passed_test_shows_the_camera_capabilities(config):
    from ui.main_window.alpaca_camera import _AlpacaCameraMixin
    config.set('alpaca_camera', {**config.get('alpaca_camera'), 'host': '10.0.0.2'})
    w = _window(config)
    w.alpaca_discovery.test_connection.return_value = True
    _AlpacaCameraMixin._on_alpaca_test(w)
    assert w.alpaca_discovery.test_connection.call_args.args[0]['host'] == '10.0.0.2'
    w.capture_panel.set_alpaca_testing.assert_called_with(True)
    _AlpacaCameraMixin._on_alpaca_test_result(w, True, 'ok', {'bayer_pattern': 'RGGB'})
    caps = w.capture_panel.apply_camera_capabilities.call_args.args[0]
    assert caps.bayer_pattern == 'RGGB'


def test_live_alpaca_session_capabilities_go_to_the_panel(config):
    from ui.main_window.capture import _MainWindowCaptureMixin
    caps = replace(ALPACA_STATIC, bayer_pattern='GRBG', bit_depth=16)
    session = SimpleNamespace(capabilities=caps)
    w = _window(config, camera_controller=SimpleNamespace(zwo_camera=session))
    _MainWindowCaptureMixin._update_camera_capabilities(w)
    w.capture_panel.apply_camera_capabilities.assert_called_once_with(caps, ())
    w.capture_panel.update_camera_capabilities.assert_not_called()
