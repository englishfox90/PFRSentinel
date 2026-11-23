"""Imaging conditions on Live Monitoring: the verdict in one sentence, the star
index over the last hours with the not-OK stretches shaded, the recent pauses
and resumes, how far the clear-sky baseline has been learned, and when NINA
last asked.

Layout only. ``ui/controllers/imaging_conditions_controller.py`` pushes
everything in through ``set_view`` on its GUI-thread timer; the two links leave
through ``settings_requested`` and ``collapse_requested``.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, getFont, CaptionLabel, CardWidget, ProgressBar,
                            StrongBodyLabel, TransparentPushButton, TransparentToolButton)

from ..components.conditions_chart import ConditionsChart
from ..components.conditions_live_text import (
    BASELINE_HELP, FWHM_HELP, STAR_INDEX_HELP, event_line, learning_progress,
    measurements_line, nina_checked_text, verdict_sentence)
from ..components.imaging_conditions_text import detail_lines
from ..theme.icons import mdi, qicon
from ..theme.tokens import Colors, Spacing

PANEL_WIDTH = 340
MAX_EVENTS = 4

_TONES = {
    'ok': Colors.success_text,
    'warn': Colors.warning_text,
    'info': Colors.info_text,
    'muted': Colors.text_secondary,
}


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet(f"color: {Colors.text_muted}; font-size: 10px; font-weight: 600; "
                        "letter-spacing: 0.07em;")
    return label


class _ElidedCaption(QLabel):
    """One line in the caption font, cut with an ellipsis to the width it gets.
    (A plain QLabel: CaptionLabel's overloaded __init__ can't be subclassed.)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(getFont(12))
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text):
        self._full = text or ""
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        width = max(10, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, width))


class ConditionsLivePanel(CardWidget):
    settings_requested = Signal()
    collapse_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(PANEL_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Spacing.base, Spacing.md, Spacing.md, Spacing.base)
        lay.setSpacing(Spacing.sm)

        head = QHBoxLayout()
        head.setSpacing(Spacing.sm)
        icon = QLabel()
        icon.setPixmap(mdi('telescope').pixmap(18, 18))
        head.addWidget(icon)
        title = StrongBodyLabel("Imaging conditions")
        head.addWidget(title, 1)
        self.collapse_button = TransparentToolButton(mdi('chevron-right'))
        self.collapse_button.setToolTip("Hide the conditions panel")
        self.collapse_button.setFixedSize(28, 28)
        self.collapse_button.clicked.connect(self.collapse_requested.emit)
        head.addWidget(self.collapse_button)
        lay.addLayout(head)

        verdict_row = QHBoxLayout()
        verdict_row.setSpacing(Spacing.sm)
        self.verdict_dot = QLabel()
        self.verdict_dot.setFixedSize(12, 20)
        verdict_row.addWidget(self.verdict_dot, 0, Qt.AlignTop)
        self.verdict_label = BodyLabel("")
        self.verdict_label.setWordWrap(True)
        verdict_row.addWidget(self.verdict_label, 1)
        lay.addLayout(verdict_row)
        self.measurements_label = CaptionLabel("")
        self.measurements_label.setWordWrap(True)
        self.measurements_label.setStyleSheet(f"color: {Colors.text_secondary};")
        self.measurements_label.setContentsMargins(20, 0, 0, 0)
        lay.addWidget(self.measurements_label)
        foot = QHBoxLayout()
        foot.setSpacing(Spacing.sm)
        foot.setContentsMargins(0, 0, 0, 0)
        self.nina_icon = QLabel()
        self.nina_icon.setFixedSize(16, 16)
        foot.addWidget(self.nina_icon)
        self.nina_label = CaptionLabel("")
        self.nina_label.setWordWrap(True)
        foot.addWidget(self.nina_label, 1)
        lay.addLayout(foot)

        lay.addSpacing(Spacing.xs)
        lay.addWidget(_section("Star index · last hours"))
        self.chart = ConditionsChart()
        self.chart.setToolTip(f"{STAR_INDEX_HELP}\n\n{BASELINE_HELP}")
        lay.addWidget(self.chart)
        self.legend_label = CaptionLabel(
            "Dashed: clear sky · shaded: not OK to image")
        self.legend_label.setWordWrap(True)
        self.legend_label.setStyleSheet(f"color: {Colors.text_muted};")
        lay.addWidget(self.legend_label)

        lay.addSpacing(Spacing.xs)
        lay.addWidget(_section("Recent"))
        self.events_box = QVBoxLayout()
        self.events_box.setSpacing(Spacing.xs)
        self.event_rows = []
        for _ in range(MAX_EVENTS):
            row = QWidget()
            row.setObjectName("conditionsEventRow")
            row.setStyleSheet("#conditionsEventRow { background: transparent; }")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(Spacing.sm)
            when = CaptionLabel("")
            when.setFixedWidth(when.fontMetrics().horizontalAdvance('00:00') + 6)
            when.setStyleSheet(f"color: {Colors.text_muted};")
            row_lay.addWidget(when)
            text = _ElidedCaption()
            text.setMinimumWidth(40)
            row_lay.addWidget(text, 1)
            length = CaptionLabel("")
            length.setStyleSheet(f"color: {Colors.text_muted};")
            length.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_lay.addWidget(length)
            row.hide()
            self.events_box.addWidget(row)
            self.event_rows.append((row, when, text, length))
        self.no_events_label = CaptionLabel("No pauses or resumes yet")
        self.no_events_label.setStyleSheet(f"color: {Colors.text_muted};")
        self.events_box.addWidget(self.no_events_label)
        lay.addLayout(self.events_box)

        lay.addSpacing(Spacing.xs)
        self.learning_title = _section("Clear-sky baseline")
        lay.addWidget(self.learning_title)
        self.learning_label = CaptionLabel("")
        self.learning_label.setWordWrap(True)
        self.learning_label.setToolTip(BASELINE_HELP)
        lay.addWidget(self.learning_label)
        self.learning_bar = ProgressBar()
        self.learning_bar.setFixedHeight(4)
        lay.addWidget(self.learning_bar)

        lay.addStretch(1)
        self.settings_link = TransparentPushButton(mdi('cog-outline'), "Settings in NINA && ASCOM")
        self.settings_link.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self.settings_link, 0, Qt.AlignLeft)

    def set_view(self, snapshot, *, capturing=None, good_needed=None, samples=None,
                 events=None, checkins=None, now=None):
        text, tone = verdict_sentence(snapshot, capturing, good_needed, events)
        color = _TONES.get(tone, Colors.text_primary)
        self.verdict_label.setText(text)
        self.verdict_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.verdict_dot.setPixmap(qicon('circle', color).pixmap(10, 10))

        line = measurements_line(snapshot)
        self.measurements_label.setText(line)
        self.measurements_label.setVisible(bool(line))
        self.measurements_label.setToolTip(
            "\n".join(filter(None, (detail_lines(snapshot), "", STAR_INDEX_HELP, FWHM_HELP))))

        self.chart.set_samples(samples or [], now)

        events = list(events or [])[:MAX_EVENTS]
        for i, (row, when, label, length) in enumerate(self.event_rows):
            if i < len(events):
                t, line, dur = event_line(events[i], now)
                when.setText(t)
                label.setText(line)
                label.setToolTip(f"{t}  {line} · {dur}")
                length.setText(dur)
                tone = 'warn' if events[i].get('kind') == 'paused' else 'ok'
                label.setStyleSheet(f"color: {_TONES[tone] if i == 0 else Colors.text_secondary};")
                row.show()
            else:
                row.hide()
        self.no_events_label.setVisible(not events)

        progress = learning_progress(snapshot)
        for w in (self.learning_title, self.learning_label):
            w.setVisible(progress is not None)
        self.learning_bar.setVisible(progress is not None and progress[1] < progress[2])
        if progress is not None:
            caption, done, needed = progress
            self.learning_label.setText(caption)
            if self.learning_bar.maximum() != max(1, needed):
                self.learning_bar.setRange(0, max(1, needed))
            if self.learning_bar.value() != done:
                self.learning_bar.setValue(done)

        nina_text, nina_tone = nina_checked_text(checkins, capturing)
        nina_color = _TONES.get(nina_tone, Colors.text_secondary)
        self.nina_label.setText(nina_text)
        self.nina_label.setStyleSheet(f"color: {nina_color};")
        self.nina_icon.setPixmap(qicon('puzzle-outline', nina_color).pixmap(16, 16))
