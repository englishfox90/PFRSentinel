"""Control API card on the NINA & ASCOM page: the switch and bearer token that
let NINA read ``/conditions`` and start or stop capture. Moved from Output →
Web Server; the config keys are unchanged (``output.webserver_control_enabled``
and ``output.api_token``).

Layout only: the switch and Regenerate leave through signals; the token is
minted and pushed to the server by the window (``ensure_control_token``),
driven from ``ui/controllers/nina_ascom_controller.py``.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, LineEdit, PushButton, ToolButton

from ..components.cards import CollapsibleCard, SwitchRow
from ..theme.icons import mdi
from ..theme.tokens import Colors, Spacing


class ControlApiCard(CollapsibleCard):
    enabled_toggled = Signal(bool)
    regenerate_requested = Signal()
    notify = Signal(str, str)  # message, category

    def __init__(self, parent=None):
        super().__init__("Control API", mdi('key-outline'), parent)
        intro = CaptionLabel(
            "The NINA plugin and the bundled NINA scripts use this to read imaging "
            "conditions and to start or stop capture. It runs on the web server "
            "(Output → Web Server).")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Colors.text_secondary};")
        self.add_widget(intro)
        self.web_off_label = CaptionLabel(
            "The web server is off, so NINA cannot reach Sentinel — turn it on in "
            "Output → Web Server.")
        self.web_off_label.setWordWrap(True)
        self.web_off_label.setStyleSheet(f"color: {Colors.warning_text};")
        self.web_off_label.hide()
        self.add_widget(self.web_off_label)

        self.control_enabled_switch = SwitchRow(
            "Capture control API",
            "Let NINA and other local tools read conditions and start/stop capture")
        self.control_enabled_switch.toggled.connect(self.enabled_toggled.emit)
        self.add_widget(self.control_enabled_switch)

        token_row = QHBoxLayout()
        token_row.setContentsMargins(0, 0, 0, 0)
        token_row.setSpacing(Spacing.sm)
        # Read-only: the token is generated, never typed. Masked by default so
        # it doesn't leak into screenshots.
        self.control_token_input = LineEdit()
        self.control_token_input.setReadOnly(True)
        self.control_token_input.setEchoMode(LineEdit.Password)
        self.control_token_input.setPlaceholderText("Turn the control API on to make a token")
        token_row.addWidget(self.control_token_input, 1)
        self.show_token_btn = PushButton("Show")
        self.show_token_btn.setCheckable(True)
        self.show_token_btn.clicked.connect(self._toggle_token_visibility)
        token_row.addWidget(self.show_token_btn)
        # Icon buttons keep the row narrow enough for the inspector beside the
        # preview on a small window.
        self.copy_token_btn = ToolButton(mdi('content-copy'))
        self.copy_token_btn.setToolTip("Copy the token")
        self.copy_token_btn.clicked.connect(self._copy_token)
        token_row.addWidget(self.copy_token_btn)
        self.regen_token_btn = ToolButton(mdi('refresh'))
        self.regen_token_btn.setToolTip("Make a new token (the old one stops working)")
        self.regen_token_btn.clicked.connect(self.regenerate_requested.emit)
        token_row.addWidget(self.regen_token_btn)
        token_widget = QWidget()
        token_widget.setLayout(token_row)
        self.add_row("API token", token_widget,
                     "The NINA plugin and scripts read this automatically — copy it "
                     "only for other tools")

    def load(self, output: dict) -> None:
        output = output if isinstance(output, dict) else {}
        control_on = bool(output.get('webserver_control_enabled', False))
        sw = self.control_enabled_switch
        was = (sw.blockSignals(True), sw.switch.blockSignals(True))
        try:
            sw.set_checked(control_on)
        finally:
            sw.blockSignals(was[0])
            sw.switch.blockSignals(was[1])
        # Only a live credential is shown: "" while the API is off.
        self.set_token((output.get('api_token', '') or '') if control_on else '')
        self.set_web_server_on(bool(output.get('webserver_enabled', False)))

    def set_token(self, token: str) -> None:
        self.control_token_input.setText(token or '')

    def set_web_server_on(self, on: bool) -> None:
        self.web_off_label.setVisible(not on)

    def _toggle_token_visibility(self):
        shown = self.show_token_btn.isChecked()
        self.control_token_input.setEchoMode(LineEdit.Normal if shown else LineEdit.Password)
        self.show_token_btn.setText("Hide" if shown else "Show")

    def _copy_token(self):
        token = self.control_token_input.text()
        if not token:
            self.notify.emit("No token yet — turn the capture control API on first.", 'warning')
            return
        QApplication.clipboard().setText(token)
        self.notify.emit("API token copied to clipboard", 'info')
