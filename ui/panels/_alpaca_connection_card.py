"""ASCOM Alpaca camera connection card: server address, discovery, Test Connection.

Layout and signals only (issue #43). Discovery and the test run in
`ui/controllers/alpaca_discovery_controller.py`; the main window hands the results
back through `set_devices` / `set_test_result`. The settings widget writes the
`alpaca_camera` config from `connection_edited` / `device_picked`.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout
from PySide6.QtCore import Signal
from qfluentwidgets import PushButton, ComboBox, LineEdit, CaptionLabel

from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from ..components.cards import SettingsCard
from ..components.scroll_safe_spinbox import SpinBox

_NOT_TESTED = "Not tested yet. Test Connection checks the camera without starting capture."
_NO_DEVICES = "Not searched yet"
_STATUS_CHARS = 140


def describe_probe(details: dict) -> str:
    """One line for a passed test: name, frame size, bit depth, colour or mono.

    The Bayer name is left to the Bayer Pattern row, which uses this app's naming
    (the physical tile turned 180°, see the plan's Decisions).
    """
    parts = []
    width, height = details.get('width'), details.get('height')
    if width and height:
        parts.append(f"{width} × {height}")
    if details.get('bit_depth'):
        parts.append(f"{details['bit_depth']}-bit")
    pattern = str(details.get('bayer_pattern') or '').upper()
    if pattern:
        parts.append("mono" if pattern == 'MONO' else "colour")
    name = details.get('name') or "Camera"
    return f"{name}: {', '.join(parts)}" if parts else str(name)


class AlpacaConnectionCard(SettingsCard):
    connection_edited = Signal(dict)  # host, port, device_number typed by the user
    device_picked = Signal(dict)      # the same plus unique_id and name, from discovery
    discover_clicked = Signal()
    test_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__("Camera Connection",
                         "Connect to a camera on an ASCOM Alpaca server, such as ASCOM Remote",
                         parent)
        self._build()

    def _build(self):
        found_row = QHBoxLayout()
        found_row.setSpacing(Spacing.sm)
        self.device_combo = ComboBox()
        self.device_combo.setPlaceholderText(_NO_DEVICES)
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        found_row.addWidget(self.device_combo, 1)
        self.discover_btn = PushButton("Discover")
        self.discover_btn.setIcon(mdi('access-point-network'))
        self.discover_btn.clicked.connect(self.discover_clicked)
        found_row.addWidget(self.discover_btn)
        found_widget = QWidget()
        found_widget.setLayout(found_row)
        self.add_row("Found", found_widget,
                     "Cameras that answered on this network. Or enter the address below.")

        self.host_input = LineEdit()
        self.host_input.setPlaceholderText("e.g. 192.168.1.20 or observatory-pc")
        self.host_input.textChanged.connect(self._on_edited)
        self.add_row("Host", self.host_input, "Address of the computer running the Alpaca server")

        self.port_spin = SpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(11111)
        self.port_spin.valueChanged.connect(self._on_edited)
        self.add_row("Port", self.port_spin, "11111 is the ASCOM default")

        self.device_spin = SpinBox()
        self.device_spin.setRange(0, 99)
        self.device_spin.valueChanged.connect(self._on_edited)
        self.add_row("Device", self.device_spin, "Camera number on that server, usually 0")

        self.test_btn = PushButton("Test Connection")
        self.test_btn.setIcon(mdi('lan-connect'))
        self.test_btn.clicked.connect(self.test_clicked)
        self.test_status = CaptionLabel(_NOT_TESTED)
        self.test_status.setWordWrap(True)
        self.test_status.setStyleSheet(f"color: {Colors.text_muted};")
        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.addWidget(self.test_btn)
        test_row.addStretch(1)
        test_widget = QWidget()
        test_widget.setLayout(test_row)
        self.add_row("", test_widget)
        self.status_row = self.add_row("", self.test_status)

    def values(self) -> dict:
        return {
            'host': self.host_input.text().strip(),
            'port': self.port_spin.value(),
            'device_number': self.device_spin.value(),
        }

    def load(self, settings: dict):
        """Show `alpaca_camera` without emitting anything."""
        for w in (self.host_input, self.port_spin, self.device_spin):
            w.blockSignals(True)
        try:
            host = str(settings.get('host', ''))
            if self.host_input.text().strip() != host:  # keep the cursor while typing
                self.host_input.setText(host)
            self.port_spin.setValue(int(settings.get('port', 11111)))
            self.device_spin.setValue(int(settings.get('device_number', 0)))
        finally:
            for w in (self.host_input, self.port_spin, self.device_spin):
                w.blockSignals(False)

    def _on_edited(self, *_):
        self._set_status(_NOT_TESTED, Colors.text_muted)
        self.connection_edited.emit(self.values())

    def _on_device_selected(self, _index):
        device = self.device_combo.currentData()
        if device is None:
            return
        self.load({'host': device.host, 'port': device.port,
                   'device_number': device.device_number})
        self._set_status(_NOT_TESTED, Colors.text_muted)
        self.device_picked.emit({**self.values(), 'unique_id': device.unique_id,
                                 'name': device.name})

    def set_discovering(self, active: bool):
        self.discover_btn.setEnabled(not active)
        self.discover_btn.setText("Searching..." if active else "Discover")

    def set_devices(self, devices: list, error: str = ""):
        """Fill the results list; nothing is picked until the user chooses one."""
        self.device_combo.blockSignals(True)
        try:
            self.device_combo.clear()
            for device in devices:
                self.device_combo.addItem(device.label(), userData=device)
            self.device_combo.setCurrentIndex(-1)
            if error:
                placeholder = "Search failed"
            elif devices:
                placeholder = f"{len(devices)} found"
            else:
                placeholder = "None found"
            self.device_combo.setPlaceholderText(placeholder)
        finally:
            self.device_combo.blockSignals(False)

    def set_testing(self, active: bool):
        self.test_btn.setEnabled(not active)
        if active:
            self._set_status("Testing...", Colors.text_muted)

    def set_test_result(self, ok: bool, message: str, details: dict):
        if ok:
            self._set_status(f"✓ {describe_probe(details or {})}", Colors.success_text)
        else:
            # An HTTP error can run to several lines; the whole text is in the
            # tooltip and the log.
            short = message if len(message) <= _STATUS_CHARS else message[:_STATUS_CHARS - 1] + "…"
            self._set_status(f"✗ {short}", Colors.error_text)
        self.test_status.setToolTip(message if not ok else "")

    def set_capture_active(self, active: bool):
        for w in (self.device_combo, self.discover_btn, self.host_input,
                  self.port_spin, self.device_spin, self.test_btn):
            w.setEnabled(not active)
        self.setToolTip("Stop capture to change cameras" if active else "")

    def _set_status(self, text: str, colour: str):
        self.test_status.setText(text)
        self.test_status.setStyleSheet(f"color: {colour};")
