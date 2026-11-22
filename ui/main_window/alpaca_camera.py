"""Main-window wiring for the Alpaca camera card: Discover and Test Connection.

Capture-panel signals go to `AlpacaDiscoveryController`, which works off the GUI
thread; its results come back here and go to the panel setters (issue #43).
"""
from services.logger import app_logger
from services.camera.camera_source import alpaca_settings, camera_ready, is_alpaca

ALPACA_NOT_CONNECTED_LABEL = "Alpaca · not connected"


def alpaca_target(settings: dict) -> tuple:
    return settings['host'], settings['port'], settings['device_number']


class _AlpacaCameraMixin:

    # The (host, port, device) that last answered a Test Connection or ran a
    # session; until one has, a configured host is not reported as connected.
    _alpaca_verified_target = None
    _alpaca_test_target = None

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
        from ..controllers.alpaca_identity import AlpacaCapabilityMemory
        self.alpaca_discovery = AlpacaDiscoveryController(self)
        self.alpaca_capabilities = AlpacaCapabilityMemory()
        self.alpaca_discovery.devices_found.connect(self._on_alpaca_devices_found)
        self.alpaca_discovery.test_result.connect(self._on_alpaca_test_result)
        self.capture_panel.alpaca_discover_clicked.connect(self._on_alpaca_discover)
        self.capture_panel.alpaca_test_clicked.connect(self._on_alpaca_test)
        self.capture_panel.camera_source_selected.connect(self._on_camera_source_selected)

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
            self._alpaca_test_target = alpaca_target(settings)
            self.capture_panel.set_alpaca_testing(True)

    def _on_alpaca_test_result(self, ok: bool, message: str, details: dict):
        self.capture_panel.set_alpaca_testing(False)
        settings = alpaca_settings(self.config)
        current = alpaca_target(settings)
        tested = getattr(self, '_alpaca_test_target', None) or current
        self._alpaca_test_target = None
        if tested != current:
            # The address was edited while the test ran: the card already reads
            # "Not tested", and this answer is about another address.
            app_logger.info("Alpaca test finished for an address that has since been edited; "
                            "result not applied")
            return
        self.capture_panel.set_alpaca_test_result(ok, message, details)
        if ok:
            self._alpaca_verified_target = tested
        elif self._alpaca_verified_target == tested:
            self._alpaca_verified_target = None
        if ok and not self.is_capturing:
            self._learn_alpaca_test(details or {})
        self._refresh_camera_pill()

    def _learn_alpaca_test(self, details: dict):
        """Persist the tested camera's identity and show its profile and ranges."""
        from services.camera.camera_source import persist_alpaca_identity
        from ..controllers.alpaca_discovery_controller import capabilities_from_probe
        try:
            if persist_alpaca_identity(self.config, details.get('unique_id'), details.get('name')):
                app_logger.info(f"Alpaca camera identity learned by Test Connection: "
                                f"{details.get('name') or '?'}")
                self.capture_panel.reload_camera_settings(self.config)
        except Exception as e:
            app_logger.warning(f"Could not save the tested camera's identity: {e}")
        try:
            caps = capabilities_from_probe(details)
            names = gain_names_from(details)
            self._alpaca_capability_memory().remember(alpaca_settings(self.config), caps, names)
            self.capture_panel.apply_camera_capabilities(caps, names)
        except Exception as e:
            app_logger.debug(f"Could not show the tested camera's capabilities: {e}")

    def _alpaca_capability_memory(self):
        memory = getattr(self, 'alpaca_capabilities', None)
        if memory is None:
            from ..controllers.alpaca_identity import AlpacaCapabilityMemory
            memory = self.alpaca_capabilities = AlpacaCapabilityMemory()
        return memory

    def _remember_alpaca_session_capabilities(self, session):
        """Keep a running Alpaca session's capabilities for its address."""
        caps = getattr(session, 'capabilities', None)
        if caps is None:
            return (), None
        names = session_gain_names(session)
        self._alpaca_capability_memory().remember(alpaca_settings(self.config), caps, names)
        return names, caps

    def _reapply_known_alpaca_capabilities(self):
        """Show what was last read from the configured address, if anything."""
        if not is_alpaca(self.config) or not hasattr(self, 'capture_panel'):
            return
        known = self._alpaca_capability_memory().for_config(self.config)
        if known is not None:
            self.capture_panel.apply_camera_capabilities(*known)

    def _on_camera_source_selected(self, source: str):
        self._reapply_known_alpaca_capabilities()
        self._refresh_camera_pill()

    def _refresh_camera_pill(self):
        """Update the app bar now rather than on the next 1 s status tick."""
        try:
            self.app_bar.set_camera_connected(camera_ready(self.config, self.camera_controller),
                                              self._camera_idle_label())
            self.app_bar.refresh_camera_pill()
        except Exception as e:
            app_logger.debug(f"Camera pill refresh skipped: {e}")


def gain_names_from(details: dict) -> tuple:
    """The camera's named gains from a probe (``gains``), when it offers a list."""
    names = (details or {}).get('gains') or ()
    return tuple(str(n) for n in names) if isinstance(names, (list, tuple)) else ()


def session_gain_names(session) -> tuple:
    """Named gains of a running Alpaca session (``static_info.gains`` in list mode)."""
    info = getattr(session, 'static_info', None)
    if info is None or getattr(info, 'gain_mode', None) != 'list':
        return ()
    return tuple(str(n) for n in (getattr(info, 'gains', None) or ()))
