"""Main-window wiring for the Alpaca camera card: Discover and Test Connection.

Capture-panel signals go to `AlpacaDiscoveryController`, which works off the GUI
thread; its results come back here and go to the panel setters (issue #43).
"""
from services.logger import app_logger
from services.camera.camera_source import alpaca_settings


class _AlpacaCameraMixin:

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
        if ok and not self.is_capturing:
            from ..controllers.alpaca_discovery_controller import capabilities_from_probe
            try:
                self.capture_panel.apply_camera_capabilities(capabilities_from_probe(details))
            except Exception as e:
                app_logger.debug(f"Could not show the tested camera's capabilities: {e}")
