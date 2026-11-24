"""Imaging conditions on Live Monitoring: the verdict in one sentence, the star
index over the last hours with the not-OK stretches shaded, the recent pauses
and resumes, how far the clear-sky baseline has been learned, and when NINA
last asked.

One card in two forms. Expanded, the whole header row collapses it; collapsed
it is a narrow rail (``ConditionsRail``) that keeps the verdict in sight and
opens it again. The card and its padding are the preview card's beside it
(``StaticCard``, ``Spacing.card_padding``, a ``SubtitleLabel`` title).

Layout only. ``ui/controllers/imaging_conditions_controller.py`` pushes
everything in through ``set_view`` on its GUI-thread timer; the links leave
through ``settings_requested``, ``collapse_requested`` and ``expand_requested``.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ProgressBar, TransparentPushButton

from ..components.cards import StaticCard
from ..components.collapse_header import CollapseHeader
from ..components.conditions_chart import ConditionsChart
from ..components.conditions_live_text import (
    BASELINE_HELP, FWHM_HELP, STAR_COUNT_HELP, event_line, learning_progress,
    measurements_line, nina_checked_text, verdict_sentence)
from ..components.conditions_rail import RAIL_WIDTH, ConditionsRail
from ..components.imaging_conditions_text import detail_lines, summarize_conditions
from ..components.plain_label import label_qss, plain_label, section_label
from ..components.target_moon_text import CLOSE_HINT, target_moon_text
from ..theme.icons import mdi, qicon
from ..theme.tokens import Colors, Spacing, Typography

PANEL_WIDTH = 340
PANEL_MIN_WIDTH = 280
MAX_EVENTS = 4
# Text under the verdict lines up with the sentence, right of its dot.
_INDENT = 12 + Spacing.sm

_TONES = {
    'ok': Colors.success_text,
    'warn': Colors.warning_text,
    'info': Colors.info_text,
    'muted': Colors.text_secondary,
}


class _ElidedCaption(QLabel):
    """One caption line, cut with an ellipsis to the width it gets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setStyleSheet(label_qss())

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


class ConditionsLivePanel(StaticCard):
    settings_requested = Signal()
    collapse_requested = Signal()
    expand_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._expanded_width = PANEL_WIDTH
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.rail = ConditionsRail()
        self.rail.clicked.connect(self.expand_requested.emit)
        self.rail.hide()
        outer.addWidget(self.rail)

        self.body = QWidget()
        self.body.setObjectName("conditionsBody")
        self.body.setStyleSheet("#conditionsBody, #conditionsContent, #conditionsEventRow "
                                "{ background: transparent; }")
        outer.addWidget(self.body, 1)
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.header = CollapseHeader(
            "Imaging conditions", mdi('telescope'), expanded=True,
            expanded_icon='chevron-right', collapsed_icon='chevron-left',
            expanded_tip="Hide the conditions panel (the verdict stays on the rail)",
            collapsed_tip="Show the imaging conditions panel")
        # The title sits where the preview card's does: card_padding from the
        # card's top and left edges.
        self.header.layout().setContentsMargins(
            Spacing.card_padding, Spacing.card_padding - 2, Spacing.md, Spacing.sm)
        self.header.clicked.connect(self.collapse_requested.emit)
        self.collapse_button = self.header
        body_lay.addWidget(self.header)

        content = QWidget()
        content.setObjectName("conditionsContent")
        body_lay.addWidget(content, 1)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(Spacing.card_padding, Spacing.xs,
                               Spacing.card_padding, Spacing.card_padding)
        lay.setSpacing(Spacing.element_gap)

        verdict_row = QHBoxLayout()
        verdict_row.setSpacing(Spacing.sm)
        self.verdict_dot = QLabel()
        self.verdict_dot.setFixedSize(12, 20)
        verdict_row.addWidget(self.verdict_dot, 0, Qt.AlignTop)
        self.verdict_label = BodyLabel("")
        self.verdict_label.setWordWrap(True)
        verdict_row.addWidget(self.verdict_label, 1)
        lay.addLayout(verdict_row)
        self.measurements_label = plain_label(wrap=True)
        self.measurements_label.setContentsMargins(_INDENT, 0, 0, 0)
        lay.addWidget(self.measurements_label)
        self.target_label = plain_label(wrap=True)
        self.target_label.setContentsMargins(_INDENT, 0, 0, 0)
        self.target_label.hide()
        lay.addWidget(self.target_label)
        foot = QHBoxLayout()
        foot.setSpacing(Spacing.sm - 2)
        foot.setContentsMargins(_INDENT, 0, 0, 0)
        self.nina_icon = QLabel()
        self.nina_icon.setFixedSize(14, 14)
        foot.addWidget(self.nina_icon)
        self.nina_label = plain_label(wrap=True)
        foot.addWidget(self.nina_label, 1)
        lay.addLayout(foot)

        lay.addSpacing(Spacing.sm)
        lay.addWidget(section_label("Stars · last hours"))
        self.chart = ConditionsChart()
        self.chart.setToolTip(f"{STAR_COUNT_HELP}\n\n{BASELINE_HELP}")
        lay.addWidget(self.chart)
        self.legend_label = plain_label(
            "Dashed: clear sky · shaded: not OK to image",
            color=Colors.gray_10, size=Typography.size_small, wrap=True)
        lay.addWidget(self.legend_label)

        lay.addSpacing(Spacing.sm)
        lay.addWidget(section_label("Recent"))
        self.events_box = QVBoxLayout()
        self.events_box.setSpacing(Spacing.xs)
        self.event_rows = []
        for _ in range(MAX_EVENTS):
            row = QWidget()
            row.setObjectName("conditionsEventRow")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(Spacing.sm)
            when = plain_label(color=Colors.gray_10)
            when.setFixedWidth(when.fontMetrics().horizontalAdvance('00:00') + 6)
            row_lay.addWidget(when)
            text = _ElidedCaption()
            text.setMinimumWidth(40)
            row_lay.addWidget(text, 1)
            length = plain_label(color=Colors.gray_10)
            length.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_lay.addWidget(length)
            row.hide()
            self.events_box.addWidget(row)
            self.event_rows.append((row, when, text, length))
        self.no_events_label = plain_label("No pauses or resumes yet", color=Colors.gray_10)
        self.events_box.addWidget(self.no_events_label)
        lay.addLayout(self.events_box)

        self.learning_gap = QWidget()
        self.learning_gap.setObjectName("conditionsContent")
        self.learning_gap.setFixedHeight(Spacing.sm)
        lay.addWidget(self.learning_gap)
        self.learning_title = section_label("Clear-sky baseline")
        lay.addWidget(self.learning_title)
        self.learning_label = plain_label(wrap=True)
        self.learning_label.setToolTip(BASELINE_HELP)
        lay.addWidget(self.learning_label)
        self.learning_bar = ProgressBar()
        self.learning_bar.setFixedHeight(4)
        lay.addWidget(self.learning_bar)

        lay.addStretch(1)
        self.settings_link = TransparentPushButton(mdi('cog-outline'), "Settings in NINA && ASCOM")
        self.settings_link.setToolTip("Open the Imaging Conditions card on the NINA & ASCOM page")
        self.settings_link.clicked.connect(self.settings_requested.emit)
        # The button pads its own text; pull it left so its icon lines up
        # with the card's content edge.
        link_row = QHBoxLayout()
        link_row.setContentsMargins(-Spacing.sm, 0, 0, 0)
        link_row.addWidget(self.settings_link)
        link_row.addStretch(1)
        lay.addLayout(link_row)
        self._apply_width()

    # -- collapsed / expanded ----------------------------------------------

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """The rail instead of the panel. Keyboard focus follows the control
        that was used, so Space toggles back."""
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        had_focus = self.header.hasFocus() or self.rail.hasFocus()
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)
        self.rail.setVisible(collapsed)
        self._apply_width()
        if had_focus:
            (self.rail if collapsed else self.header).setFocus(Qt.TabFocusReason)

    def set_expanded_width(self, width: int) -> None:
        """Narrower beside a small preview (``PANEL_MIN_WIDTH`` .. ``PANEL_WIDTH``)."""
        width = max(PANEL_MIN_WIDTH, min(PANEL_WIDTH, int(width)))
        if width != self._expanded_width:
            self._expanded_width = width
            self._apply_width()

    def _apply_width(self):
        self.setFixedWidth(RAIL_WIDTH if self._collapsed else self._expanded_width)

    def set_view(self, snapshot, *, capturing=None, good_needed=None, samples=None,
                 events=None, checkins=None, now=None):
        text, tone = verdict_sentence(snapshot, capturing, good_needed, events)
        color = _TONES.get(tone, Colors.text_primary)
        self.verdict_label.setText(text)
        self.verdict_label.setStyleSheet(
            f"color: {color}; font-size: {Typography.size_body}px; font-weight: 600;")
        self.verdict_dot.setPixmap(qicon('circle', color).pixmap(10, 10))
        short, short_tone = summarize_conditions(snapshot, capturing)
        self.rail.set_status(short, _TONES.get(short_tone, Colors.text_secondary), text)

        line = measurements_line(snapshot)
        self.measurements_label.setText(line)
        self.measurements_label.setVisible(bool(line))
        self.measurements_label.setToolTip(
            "\n".join(filter(None, (detail_lines(snapshot), "", STAR_COUNT_HELP, FWHM_HELP))))

        target_text, close = target_moon_text(snapshot)
        self.target_label.setText(target_text)
        self.target_label.setVisible(bool(target_text))
        self.target_label.setStyleSheet(
            label_qss(_TONES['warn'] if close else Colors.text_secondary))
        self.target_label.setToolTip(CLOSE_HINT if close else "")

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
                label.setStyleSheet(label_qss(_TONES[tone] if i == 0 else Colors.text_secondary))
                row.show()
            else:
                row.hide()
        self.no_events_label.setVisible(not events)

        progress = learning_progress(snapshot)
        for w in (self.learning_gap, self.learning_title, self.learning_label):
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
        self.nina_label.setStyleSheet(label_qss(nina_color))
        self.nina_icon.setPixmap(qicon('puzzle-outline', nina_color).pixmap(14, 14))
