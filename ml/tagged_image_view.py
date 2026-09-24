#!/usr/bin/env python3
"""Image panel whose frame colour shows the frame's current roof tag.

Green = open, red = closed, so the tag is readable where the eye already is
instead of across the window in the label form. A solid frame is a label that
is saved on disk; a dashed frame is a suggestion or an unsaved edit.
"""
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

OPEN_COLOUR = "#10b981"
CLOSED_COLOUR = "#ef4444"


def tag_text(roof_open: bool, sky: str) -> str:
    if not roof_open:
        return "ROOF CLOSED"
    return f"ROOF OPEN · {sky}" if sky else "ROOF OPEN · sky not set"


class TaggedImageView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QPixmap()
        self.setObjectName("taggedImageView")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.badge = QLabel("")
        self.badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.badge)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(400, 400)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image.setStyleSheet("background: #101010; border: none;")
        layout.addWidget(self.image, 1)

        self.set_tag(None, "", saved=False)

    def set_pixmap(self, pixmap: QPixmap):
        self._source = pixmap
        self._rescale()

    def set_tag(self, roof_open, sky: str, saved: bool):
        """roof_open None = nothing to show (no sample loaded)."""
        if roof_open is None:
            colour, text, style = "#444", "", "solid"
        else:
            colour = OPEN_COLOUR if roof_open else CLOSED_COLOUR
            text = tag_text(roof_open, sky) + ("" if saved else "   (not saved)")
            style = "solid" if saved else "dashed"
        self.setStyleSheet(
            f"#taggedImageView {{ border: 6px {style} {colour}; border-radius: 6px; background: #1a1a1a; }}")
        self.badge.setText(text)
        self.badge.setStyleSheet(
            f"background: {colour}; color: white; font-weight: bold; font-size: 16px; "
            f"padding: 6px; border-radius: 4px; border: none;")
        self.badge.setVisible(bool(text))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        if self._source.isNull():
            return
        side = max(64, min(self.image.width(), self.image.height()))
        self.image.setPixmap(self._source.scaled(
            side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation))
