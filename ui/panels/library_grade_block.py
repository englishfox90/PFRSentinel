"""
Library — the imaging-conditions grade of the frame under the playhead
(layout only). Wording comes from ``ui/components/library_grade_text``.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import CaptionLabel, BodyLabel

from ..components.library_grade_text import frame_grade_details, frame_grade_headline
from ..theme.tokens import Colors, Spacing

_TONE_COLORS = {
    'ok': Colors.success_text,
    'warn': Colors.warning_text,
    'info': Colors.info_text,
    'muted': Colors.text_primary,
}


class FrameGradeBlock(QWidget):
    """'IMAGING CONDITIONS' caption, the verdict, and the lines behind it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(Spacing.xs)

        title = CaptionLabel("IMAGING CONDITIONS")
        title.setStyleSheet(f"color: {Colors.text_muted}; letter-spacing: 1px;")
        title.setToolTip(
            "What Imaging Conditions reported to NINA when this frame was "
            "taken. Stored with the frame; never recalculated.")
        v.addWidget(title)

        self.headline = BodyLabel("—")
        self.headline.setWordWrap(True)
        v.addWidget(self.headline)

        self.details = CaptionLabel("")
        self.details.setWordWrap(True)
        self.details.setStyleSheet(f"color: {Colors.text_secondary};")
        self.details.setVisible(False)
        v.addWidget(self.details)

    def set_frame(self, row):
        text, tone = frame_grade_headline(row)
        self.headline.setText(text)
        self.headline.setStyleSheet(f"color: {_TONE_COLORS.get(tone, Colors.text_primary)};")
        lines = frame_grade_details(row)
        self.details.setText("\n".join(lines))
        self.details.setVisible(bool(lines))
