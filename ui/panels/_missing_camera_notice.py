"""Missing/stuck-camera notice shown under the camera picker.

Extracted from `_camera_settings_widget.py` to stay under the file-size cap.
Owns its own Revive-vs-Detect-Again decision: USB re-enumeration
(`CM_Reenumerate_DevNode`) is a Win32 API, so Revive only ever makes sense on
Windows (tracking issue #1). Elsewhere, a stuck phantom device still gets a
clear "stuck" message — just with unplug/replug guidance instead of a button
that would fail everywhere but Windows.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal
from qfluentwidgets import BodyLabel, PushButton

from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from services.host_platform import IS_WINDOWS
from services.logger import app_logger


class MissingCameraNotice(QWidget):
    """Hidden by default; shown via `set_warning()` when the saved camera isn't detected."""

    revive_clicked = Signal(str)  # saved camera name to revive via USB reset

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("missingCameraWidget")
        self._saved_name = ''
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(Spacing.xs)

        self._label = BodyLabel("")
        self._label.setWordWrap(True)

        self._detect_btn = PushButton("Detect Again")
        self._detect_btn.setIcon(mdi('refresh'))

        self._revive_btn = PushButton("Revive (USB Reset)")
        self._revive_btn.setIcon(mdi('restart'))
        self._revive_btn.setToolTip(
            "Toggle the USB device off and on in Windows Device Manager. "
            "Can recover a camera that the driver sees but can't open. "
            "Requires Administrator privileges."
        )
        self._revive_btn.clicked.connect(self._on_revive_clicked)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Spacing.sm)
        btn_row.addWidget(self._detect_btn)
        btn_row.addWidget(self._revive_btn)
        btn_row.addStretch()

        layout.addWidget(self._label)
        layout.addLayout(btn_row)
        self.hide()

    def connect_detect(self, slot):
        self._detect_btn.clicked.connect(slot)

    def set_warning(self, saved_name: str, phantom_count: int = 0):
        """Pass empty saved_name to hide."""
        if not saved_name:
            self.hide()
            self._saved_name = ''
            self.reset_revive_button()
            return

        self._saved_name = saved_name
        if phantom_count > 0 and IS_WINDOWS:
            msg = (
                f"'{saved_name}' is stuck — the SDK detects {phantom_count} "
                "device(s) it can't open. Try Revive to perform a USB reset."
            )
            self._detect_btn.hide()
            self._revive_btn.show()
            bg, fg = Colors.error_bg, Colors.error_text
        elif phantom_count > 0:
            msg = (
                f"'{saved_name}' is stuck — the SDK detects {phantom_count} "
                "device(s) it can't open. Unplug the camera, plug it back in, "
                "then click Detect Again."
            )
            self._revive_btn.hide()
            self._detect_btn.show()
            bg, fg = Colors.error_bg, Colors.error_text
        else:
            msg = (
                f"'{saved_name}' is not connected. "
                "Check the USB cable, then click Detect Again to scan for cameras."
            )
            self._revive_btn.hide()
            self._detect_btn.show()
            bg, fg = Colors.warning_bg, Colors.warning_text

        self._label.setText(msg)
        self._label.setStyleSheet(f"color: {fg}; font-weight: 500;")
        self.setStyleSheet(f"""
            QWidget#missingCameraWidget {{
                background-color: {bg};
                border-radius: 6px;
                border: 1px solid {fg}40;
            }}
        """)
        self.show()

    def _on_revive_clicked(self):
        if not self._saved_name:
            return
        app_logger.info(f"User requested Revive for camera '{self._saved_name}'")
        self._revive_btn.setEnabled(False)
        self._revive_btn.setText("Resetting USB…")
        self.revive_clicked.emit(self._saved_name)

    def reset_revive_button(self):
        """Restore the Revive button after an async reset finishes."""
        self._revive_btn.setEnabled(True)
        self._revive_btn.setText("Revive (USB Reset)")
