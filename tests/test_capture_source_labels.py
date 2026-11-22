"""Source-aware wording outside the panels (issue #43): Discord start messages,
the main window's startup / periodic text, the capture watchdog and the app
bar's idle pill. The ZWO text is pinned byte for byte."""
from unittest.mock import MagicMock, Mock, patch

import pytest

from services.camera.camera_source import capture_source_label


def _cfg(**values):
    base = {'discord': {'enabled': True, 'webhook_url': 'https://discord.com/api/webhooks/t/t',
                        'post_startup_shutdown': True},
            'capture_mode': 'camera', 'output_directory': '/out'}
    base.update(values)
    config = Mock()
    config.get = lambda key, default=None: base.get(key, default)
    return config


@pytest.mark.parametrize("values,label", [
    ({}, "ZWO Camera Capture"),
    ({'camera_source': 'zwo'}, "ZWO Camera Capture"),
    ({'camera_source': 'alpaca'}, "Alpaca Camera Capture"),
    ({'capture_mode': 'watch', 'camera_source': 'alpaca'}, "Directory Watch"),
])
@pytest.mark.parametrize("method", ["send_startup_message", "send_capture_started_message"])
@patch('services.discord_alerts.requests.post')
def test_discord_mode_line(mock_post, method, values, label):
    from services.discord_alerts import DiscordAlerts
    mock_post.return_value = Mock(status_code=204)
    assert getattr(DiscordAlerts(_cfg(**values)), method)() is True
    description = mock_post.call_args[1]['json']['embeds'][0]['description']
    assert description.startswith(f"**Mode:** {label}\n")


def test_discord_mode_defaults_to_watch_when_the_key_is_absent():
    from services.discord_alerts import DiscordAlerts
    config = Mock()
    config.get = lambda key, default=None: {'discord': {'post_startup_shutdown': True,
                                                         'enabled': True,
                                                         'webhook_url': 'x'}}.get(key, default)
    with patch.object(DiscordAlerts, 'send_discord_message', return_value=True) as send:
        DiscordAlerts(config).send_startup_message()
    assert send.call_args.args[1].startswith("**Mode:** Directory Watch\n")


def test_short_label_keeps_the_periodic_zwo_text():
    assert capture_source_label({'camera_source': 'zwo'}, short=True) == "ZWO Camera"
    assert capture_source_label({'camera_source': 'alpaca'}, short=True) == "Alpaca Camera"
    assert capture_source_label({'capture_mode': 'watch'}, short=True) == "Directory Watch"


class _OutputHost:
    def __init__(self, **values):
        from ui.main_window.output import _MainWindowOutputMixin
        self.__class__ = type("Host", (_OutputHost, _MainWindowOutputMixin), {})
        self.config = _cfg(**values)
        self.is_capturing = True
        self.image_count = 3
        self.camera_controller = None
        self.notifier = MagicMock()


@pytest.mark.parametrize("source,mode,startup", [
    ("zwo", "ZWO Camera", "ZWO Camera Capture"),
    ("alpaca", "Alpaca Camera", "Alpaca Camera Capture"),
])
def test_main_window_startup_and_periodic_text(source, mode, startup):
    host = _OutputHost(camera_source=source)
    assert f"**Mode:** {mode}\n" in host._build_periodic_body()
    host._send_discord_startup()
    event = host.notifier.notify.call_args.args[0]
    assert event.body == f"Mode: {startup}\nOutput Path: /out"
    assert event.data['mode'] == 'camera'


def test_main_window_capture_snapshot_reports_the_source():
    host = _OutputHost(camera_source='alpaca')
    host._last_capture_error = host._last_capture_error_epoch = None
    host.watch_controller = None
    host.camera_controller = MagicMock(zwo_camera=None, is_capturing=True)
    host.camera_controller.recovery_state.return_value = None
    host.camera_controller.last_successful_frame_epoch.return_value = None
    snapshot = host._build_capture_status()
    assert snapshot['mode'] == 'camera' and snapshot['camera_source'] == 'alpaca'


def _watchdog_fatal_text(backend):
    from ui.main_window.capture_watchdog import _CaptureWatchdogMixin
    host = _CaptureWatchdogMixin()
    host.is_capturing = True
    cam = MagicMock(backend=backend, is_capturing=True, _last_frame_time=0.0,
                    capture_interval=5.0, exposure_seconds=1.0, long_retry_mode_public=False)
    host.camera_controller = MagicMock(zwo_camera=cam)
    host._watchdog_first_fire_ts = 1.0
    host._watchdog_ui_fatal_sent = False
    host._check_capture_watchdog()
    return host.camera_controller._on_camera_error.call_args.args[0]


def test_watchdog_fatal_text_follows_the_source():
    assert _watchdog_fatal_text('zwo').startswith(
        "Capture thread appears permanently wedged inside the ZWO SDK. ")
    alpaca = _watchdog_fatal_text('alpaca')
    assert "Alpaca server" in alpaca and "ZWO" not in alpaca


# -- app bar idle pill ------------------------------------------------------------

@pytest.fixture
def qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    yield QApplication.instance() or QApplication([])


def _bar(qt_app):
    from ui.components.app_bar import AppBar
    bar = AppBar()
    bar._status_strip = MagicMock()
    return bar


def test_app_bar_idle_label_override_and_default(qt_app):
    bar = _bar(qt_app)
    bar.set_camera_connected(True, "Alpaca · not connected")
    bar.update_status(False, 0)
    bar._status_strip.set_capture_state.assert_called_with('idle', "Alpaca · not connected")
    bar.set_camera_connected(True)
    bar.update_status(False, 0)
    bar._status_strip.set_capture_state.assert_called_with('idle', "Connected · idle")


class _AlpacaHost:
    def __init__(self, source='alpaca', host='192.0.2.10'):
        from ui.main_window.alpaca_camera import _AlpacaCameraMixin
        self.__class__ = type("Host", (_AlpacaHost, _AlpacaCameraMixin), {})
        self.config = _cfg(camera_source=source,
                           alpaca_camera={'host': host, 'port': 11111, 'device_number': 0})
        self.camera_controller = None
        self.is_capturing = False
        self.capture_panel = MagicMock()


def test_alpaca_reads_not_connected_until_a_test_passes():
    from ui.main_window.alpaca_camera import ALPACA_NOT_CONNECTED_LABEL
    host = _AlpacaHost()
    assert host._camera_idle_label() == ALPACA_NOT_CONNECTED_LABEL
    host._on_alpaca_test_result(False, "Connection refused by 192.0.2.10:11111", {})
    assert host._camera_idle_label() == ALPACA_NOT_CONNECTED_LABEL
    with patch('ui.controllers.alpaca_discovery_controller.capabilities_from_probe'):
        host._on_alpaca_test_result(True, "Connected: Sim 16×12, RGGB, 12-bit", {})
    assert host._camera_idle_label() is None
    host.config = _cfg(camera_source='alpaca',
                       alpaca_camera={'host': '192.0.2.99', 'port': 11111, 'device_number': 0})
    assert host._camera_idle_label() == ALPACA_NOT_CONNECTED_LABEL


def test_a_live_alpaca_session_counts_as_connected_after_it_stops():
    host = _AlpacaHost()
    host.camera_controller = MagicMock(is_connected=True)
    assert host._camera_idle_label() is None
    host.camera_controller.is_connected = False
    assert host._camera_idle_label() is None


def test_zwo_keeps_the_default_label():
    assert _AlpacaHost(source='zwo')._camera_idle_label() is None
