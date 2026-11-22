"""Main-window wiring for the Alpaca camera card: Discover and Test Connection.

Capture-panel signals go to `AlpacaDiscoveryController`, which works off the GUI
thread; its results come back here and go to the panel setters (issue #43).
"""
from services.logger import app_logger
from services.camera.camera_source import alpaca_settings, is_alpaca

ALPACA_NOT_CONNECTED_LABEL = "Alpaca · not connected"


def alpaca_target(settings: dict) -> tuple:
    return settings['host'], settings['port'], settings['device_number']


class _AlpacaCameraMixin:

    # The (host, port, device) that last answered a Test Connection or ran a
    # session; until one has, a configured host is not reported as connected.
    _alpaca_verified_target = None

    def _camera_idle_label(self):
        """The app bar's idle pill text, or None for the usual "Connected · idle"."""
        if not is_alpaca(self.config):
            return None
        target = alpaca_target(alpaca_settings(self.config))
        controller = getattr(self, 'camera_controller', None)
        if controller is not None and getattr(controller, 'is_connected', False):
            self._alpaca_verified_target = target
        return None if target == self._alpaca_verified_target else ALPACA_NOT_CONNECTED_LABEL

    def _setup_alpaca_connections(self):
        from ..controllers.alpaca_discovery_controller import AlpacaDiscoveryController
        self.alpaca_discovery = AlpacaDiscoveryController(self)
        self.alpaca_discovery.devices_found.connect(self._on_alpaca_devices_found)
        self.alpaca_discovery.test_result.connect(self._on_alpaca_test_result)
        self.capture_panel.alpaca_discover_clicked.connect(self._on_alpaca_discover)
        self.capture_panel.alpaca_test_clicked.connect(self._on_alpaca_test)

    def _on_alpaca_discover(self):
        if self.alpaca_discovery.discover():
            self.capture_panel.set_alpaca_discovering(True)

    def _on_alpaca_devices_found(self, devices: list, error: str):
        self.capture_panel.set_alpaca_discovering(False)
        self.capture_panel.set_alpaca_devices(devices, error)
        if error:
            self._notify(f"Alpaca discovery failed: {error}", "error")
        elif not devices:
            self._notify("No Alpaca cameras answered. Enter the server address instead.", "warning")
        else:
            self._notify(f"{len(devices)} Alpaca camera(s) found")

    def _on_alpaca_test(self):
        settings = alpaca_settings(self.config)
        if not settings['host']:
            self.capture_panel.set_alpaca_test_result(False, "Enter the server's host first", {})
            return
        if self.alpaca_discovery.test_connection(settings):
            self.capture_panel.set_alpaca_testing(True)

    def _on_alpaca_test_result(self, ok: bool, message: str, details: dict):
        self.capture_panel.set_alpaca_testing(False)
        self.capture_panel.set_alpaca_test_result(ok, message, details)
        tested = alpaca_target(alpaca_settings(self.config))
        if ok:
            self._alpaca_verified_target = tested
        elif self._alpaca_verified_target == tested:
            self._alpaca_verified_target = None
        if ok and not self.is_capturing:
            from ..controllers.alpaca_discovery_controller import capabilities_from_probe
            try:
                self.capture_panel.apply_camera_capabilities(capabilities_from_probe(details))
            except Exception as e:
                app_logger.debug(f"Could not show the tested camera's capabilities: {e}")
