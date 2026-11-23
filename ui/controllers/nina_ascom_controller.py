"""NINA & ASCOM page controller: saves the Control API and Roof Safety cards'
edits to the same config keys they always used, and keeps the cards' hints
(web server off, ML off) in step with the rest of the settings.

Token minting and the push to a running server stay on the window
(``ensure_control_token`` / ``regenerate_control_token`` in
``ui/main_window/settings.py``); the window's ``_on_settings_changed`` saves
the config and reconciles the web server and the Alpaca SafetyMonitor.
"""
from PySide6.QtCore import QObject

from services.logger import app_logger


class NinaAscomController(QObject):
    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self._main_window = main_window
        self._panel = None

    @property
    def config(self):
        return getattr(self._main_window, 'config', None)

    def bind(self, panel) -> None:
        self._panel = panel
        control = panel.control_card
        control.enabled_toggled.connect(self.set_control_enabled)
        control.regenerate_requested.connect(self.regenerate_token)
        control.notify.connect(self._notify)
        safety = panel.safety_card
        safety.alpaca_toggled.connect(self.set_alpaca_safety)
        safety.safety_file_toggled.connect(self.set_safety_file)
        safety.safety_file_path_changed.connect(self.set_safety_file_path)
        for name in ('output_panel', 'processing_panel'):
            other = getattr(self._main_window, name, None)
            signal = getattr(other, 'settings_changed', None)
            if hasattr(signal, 'connect'):
                signal.connect(self.refresh_hints)
        self.refresh_hints()

    def refresh_hints(self) -> None:
        if self._panel is None or self.config is None:
            return
        output = self.config.get('output', {}) or {}
        self._panel.control_card.set_web_server_on(bool(output.get('webserver_enabled', False)))
        ml = self.config.get('ml_models', {}) or {}
        self._panel.safety_card.ml_off_label.setVisible(not ml.get('enabled', False))

    # -- control API -------------------------------------------------------

    def set_control_enabled(self, checked: bool) -> None:
        if self.config is None:
            return
        output = dict(self.config.get('output', {}) or {})
        output['webserver_control_enabled'] = bool(checked)
        self.config.set('output', output)
        # Persist + reconcile first, then resolve the token: the resolve reads
        # the flag just written, and on disable it pushes the empty token that
        # actually disarms a server already running.
        self._settings_changed()
        self._sync_token()
        if checked and not output.get('webserver_enabled', False):
            self._notify("Capture control needs the web server — turn it on in "
                         "Output → Web Server.", 'warning')

    def regenerate_token(self) -> None:
        regenerate = getattr(self._main_window, 'regenerate_control_token', None)
        if regenerate is None:
            return
        regenerate()
        self._sync_token()
        self._notify("New API token made — any tool using the old one must be updated.")

    def _sync_token(self) -> None:
        ensure = getattr(self._main_window, 'ensure_control_token', None)
        token = (ensure() or '') if ensure is not None else ''
        if self._panel is not None:
            self._panel.control_card.set_token(token)

    # -- roof safety -------------------------------------------------------

    def _ml_block(self, key):
        ml = dict(self.config.get('ml_models', {}) or {})
        block = dict(ml.get(key, {}) or {})
        return ml, block

    def _save_ml_block(self, ml, key, block) -> None:
        ml[key] = block
        self.config.set('ml_models', ml)
        self._settings_changed()

    def set_alpaca_safety(self, checked: bool) -> None:
        if self.config is None:
            return
        ml, block = self._ml_block('alpaca_safety_monitor')
        block['enabled'] = bool(checked)
        self._save_ml_block(ml, 'alpaca_safety_monitor', block)
        if self._panel is not None:
            self._panel.safety_card.show_alpaca_info(block)

    def set_safety_file(self, checked: bool) -> None:
        if self.config is None:
            return
        ml, block = self._ml_block('ascom_safety_file')
        block['enabled'] = bool(checked)
        self._save_ml_block(ml, 'ascom_safety_file', block)
        if checked:
            app_logger.info("ASCOM Safety file output enabled: "
                            f"{block.get('file_path', '') or '(no path set)'}")
        else:
            app_logger.info("ASCOM Safety file output disabled")

    def set_safety_file_path(self, path: str) -> None:
        if self.config is None:
            return
        ml, block = self._ml_block('ascom_safety_file')
        if block.get('file_path', '') == path:
            return
        block['file_path'] = path
        self._save_ml_block(ml, 'ascom_safety_file', block)

    # -- plumbing ----------------------------------------------------------

    def _settings_changed(self) -> None:
        handler = getattr(self._main_window, '_on_settings_changed', None)
        if handler is not None:
            handler()
        elif self.config is not None:
            self.config.save()

    def _notify(self, message: str, category: str = 'info') -> None:
        notify = getattr(self._main_window, '_notify', None)
        if notify is not None:
            notify(message, category)
