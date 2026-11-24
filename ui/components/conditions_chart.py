"""Star count over the last few hours, with the not-OK stretches shaded and the
clear-sky baseline dashed — the chart on Live Monitoring's conditions panel.

Painted with QPainter like the histogram beside it; no charting library.
Display only: ``set_samples`` takes ``conditions_history.Sample`` rows, the
caller decides the window.
"""
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QSizePolicy

from ..theme.tokens import Colors, Layout

WINDOW = timedelta(hours=3)
MIN_WINDOW = timedelta(minutes=30)
# A gap longer than this between two frames is drawn as a break in the line
# and shaded: nothing was judged, so NINA was told not to image.
GAP = timedelta(minutes=5)

_PAD_L, _PAD_R, _PAD_T, _PAD_B = 34, 8, 8, 18


def _alpha(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


class ConditionsChart(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples = []
        self._now = None
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(132)
        self.setStyleSheet(f"""
            ConditionsChart {{
                background-color: {Colors.bg_surface};
                border: 1px solid {Colors.border_subtle};
                border-radius: {Layout.radius_md}px;
            }}
        """)

    def set_samples(self, samples, now: datetime = None) -> None:
        self._samples = list(samples or [])
        self._now = now or datetime.now(timezone.utc)
        self.update()

    def time_range(self):
        now = self._now or datetime.now(timezone.utc)
        start = now - WINDOW
        if self._samples:
            first = self._samples[0].at
            start = max(start, first - timedelta(minutes=2))
        if now - start < MIN_WINDOW:
            start = now - MIN_WINDOW
        return start, now

    def not_ok_spans(self):
        """``[(start, end), ...]`` stretches where imaging was not OK: the
        state a frame left in force until the next frame, and any gap longer
        than ``GAP``. Before the first frame nothing is known, so nothing is
        shaded."""
        start, end = self.time_range()
        rows = [s for s in self._samples if s.at <= end]
        spans = []

        def add(a, b):
            a, b = max(a, start), min(b, end)
            if b <= a:
                return
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], b))
            else:
                spans.append((a, b))

        for cur, nxt in zip(rows, rows[1:]):
            if not cur.imaging_ok or nxt.at - cur.at > GAP:
                add(cur.at, nxt.at)
        if rows and (not rows[-1].imaging_ok or end - rows[-1].at > GAP):
            add(rows[-1].at, end)
        return spans

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plot = QRectF(_PAD_L, _PAD_T, self.width() - _PAD_L - _PAD_R,
                      self.height() - _PAD_T - _PAD_B)
        small = QFont()
        small.setPixelSize(10)
        p.setFont(small)
        start, end = self.time_range()
        span_s = max(1.0, (end - start).total_seconds())

        def x_of(t):
            return plot.left() + plot.width() * (t - start).total_seconds() / span_s

        counts = [s.star_index for s in self._samples if s.star_index is not None and s.at >= start]
        bases = [s.baseline for s in self._samples if s.baseline and s.at >= start]
        top = max(counts + bases + [10])
        top = top * 1.15

        def y_of(v):
            return plot.bottom() - plot.height() * min(1.0, v / top)

        for a, b in self.not_ok_spans():
            p.fillRect(QRectF(QPointF(x_of(max(a, start)), plot.top()),
                              QPointF(x_of(min(b, end)), plot.bottom())),
                       _alpha(Colors.warning_default, 46))

        p.setPen(QPen(QColor(Colors.border_subtle), 1))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        self._draw_time_ticks(p, plot, start, end, x_of)
        p.setPen(QColor(Colors.text_muted))
        p.drawText(QRectF(0, plot.top() - 4, _PAD_L - 6, 14), Qt.AlignRight | Qt.AlignVCenter,
                   f"{top / 1.15:.0f}")
        p.drawText(QRectF(0, plot.bottom() - 10, _PAD_L - 6, 14),
                   Qt.AlignRight | Qt.AlignVCenter, "0")

        if not counts:
            p.drawText(plot, Qt.AlignCenter, "No star count yet")
            p.end()
            return

        if bases:
            base = bases[-1]
            pen = QPen(QColor(Colors.text_secondary), 1, Qt.DashLine)
            p.setPen(pen)
            y = y_of(base)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        path = QPainterPath()
        prev = None
        for s in self._samples:
            if s.star_index is None or s.at < start:
                continue
            pt = QPointF(x_of(s.at), y_of(s.star_index))
            if prev is None or s.at - prev > GAP:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
            prev = s.at
        p.setPen(QPen(QColor(Colors.accent_text), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()

    @staticmethod
    def _draw_time_ticks(p, plot, start, end, x_of):
        span = end - start
        step = timedelta(minutes=30) if span <= timedelta(hours=2) else timedelta(hours=1)
        local = start.astimezone()
        tick = local.replace(minute=0, second=0, microsecond=0)
        while tick < local:
            tick += step
        p.setPen(QColor(Colors.text_muted))
        last_right = -1e9
        while tick <= end.astimezone():
            x = x_of(tick)
            label = tick.strftime('%H:%M')
            rect = QRectF(x - 20, plot.bottom() + 3, 40, 12)
            if rect.left() > last_right + 4 and rect.right() <= plot.right() + 14:
                p.drawText(rect, Qt.AlignCenter, label)
                last_right = rect.right()
            p.setPen(QPen(_alpha(Colors.border_subtle, 160), 1, Qt.DotLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(QColor(Colors.text_muted))
            tick += step

