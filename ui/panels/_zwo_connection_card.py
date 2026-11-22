"""ZWO camera connection card: SDK path, camera picker, Detect, missing-camera notice.

Extracted from `_camera_settings_widget.py` (issue #43) so the Alpaca card can sit
beside it. Layout and signals only; the settings widget writes the config.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QFileDialog
from PySide6.QtCore import Signal
from qfluentwidgets import PushButton, ComboBox, LineEdit

from ..theme.tokens import Spacing
from ..theme.icons import mdi
from ..components.cards import SettingsCard
from ._missing_camera_notice import MissingCameraNotice
from services.host_platform import zwo_sdk_library_name, zwo_sdk_dialog_filter


def clean_camera_name(name: str) -> str:
    return name.split('(Index:')[0].strip() if '(Index:' in name else name


class ZwoConnectionCard(SettingsCard):
    sdk_path_changed = Signal(str)
    camera_picked = Signal(int, str)  # SDK index hint, model name
    detect_clicked = Signal()
    revive_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Camera Connection", "Connect to your ZWO ASI camera", parent)
        self._build()

    def _build(self):
        sdk_row = QHBoxLayout()
        sdk_row.setSpacing(Spacing.sm)
        self.sdk_path_input = LineEdit()
        self.sdk_path_input.setPlaceholderText(f"Path to {zwo_sdk_library_name()}")
        self.sdk_path_input.textChanged.connect(self.sdk_path_changed)
        sdk_row.addWidget(self.sdk_path_input, 1)
        sdk_browse = PushButton("Browse")
        sdk_browse.setIcon(mdi('folder-outline'))
        sdk_browse.clicked.connect(self._browse_sdk)
        sdk_row.addWidget(sdk_browse)
        sdk_widget = QWidget()
        sdk_widget.setLayout(sdk_row)
        self.sdk_row = self.add_row("SDK Path", sdk_widget)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(Spacing.sm)
        self.camera_combo = ComboBox()
        self.camera_combo.setPlaceholderText("No cameras detected")
        self.camera_combo.currentIndexChanged.connect(self._on_camera_selected)
        camera_row.addWidget(self.camera_combo, 1)
        self.detect_btn = PushButton("Detect")
        self.detect_btn.setIcon(mdi('refresh'))
        self.detect_btn.clicked.connect(self.detect_clicked)
        camera_row.addWidget(self.detect_btn)
        camera_widget = QWidget()
        camera_widget.setLayout(camera_row)
        self.camera_row = self.add_row("Camera", camera_widget)

        self.missing_notice = MissingCameraNotice()
        self.missing_notice.connect_detect(self.detect_clicked.emit)
        self.missing_notice.revive_clicked.connect(self.revive_clicked)
        self.add_widget(self.missing_notice)

    def _browse_sdk(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select ASI SDK", "", zwo_sdk_dialog_filter())
        if file_path:
            self.sdk_path_input.setText(file_path)

    def _on_camera_selected(self, index):
        # The SDK index rides as hidden item data (set in set_cameras), so it is
        # never parsed out of the visible label.
        data = self.camera_combo.currentData()
        actual_index = data if isinstance(data, int) else index
        self.camera_picked.emit(actual_index, clean_camera_name(self.camera_combo.currentText()))

    def set_cameras(self, camera_list: list):
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        if camera_list:
            # Display the model name only — the hardware serial is the identity
            # now. The SDK index is kept as hidden item data (a starting hint for
            # connect); it shifts on hot-plug, so showing it is misleading.
            # NB: pass the index as userData, NOT positionally — ComboBox.addItem
            # is (text, icon, userData), so a bare int lands in the icon slot and
            # crashes the dropdown ('int' object has no attribute 'icon') when the
            # menu renders. currentData() also reads userData, so this is the slot
            # _on_camera_selected expects.
            for pos, entry in enumerate(camera_list):
                idx = pos
                if '(Index: ' in entry:
                    try:
                        idx = int(entry.split('(Index: ')[1].rstrip(')'))
                    except (IndexError, ValueError):
                        idx = pos
                self.camera_combo.addItem(clean_camera_name(entry), userData=idx)
        else:
            self.camera_combo.setPlaceholderText("No cameras detected")
        self.camera_combo.blockSignals(False)

    def set_detecting(self, is_detecting: bool):
        self.detect_btn.setEnabled(not is_detecting)
        self.detect_btn.setText("Detecting..." if is_detecting else "Detect")

    def set_capture_active(self, active: bool):
        """Changing the selected camera does not switch the live hardware — it
        only pushes the picked camera's profile onto the running one. Locking the
        picker during capture enforces the real workflow: Stop → pick → Start."""
        self.camera_combo.setEnabled(not active)
        self.detect_btn.setEnabled(not active)
        self.camera_combo.setToolTip("Stop capture to change cameras" if active else "")
