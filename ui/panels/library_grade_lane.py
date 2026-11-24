"""
Library — the imaging-conditions lane of the night scrubber (painting only).

Drawn by ``Scrubber`` under its condition band, on the same frame-index
axis: a strip along the bottom coloured OK / not OK / not graded, and the
clear-sky ratio as a line above it where it was known. The area behind the
line is left neutral: the condition band directly above uses the same green
and amber for the sky class, and a second tinted block under it read as one
doubled band. The lane is rendered once into a pixmap per night and width; moving
the playhead only blits it and draws the marker.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

from services.library import grade_timeline as GT
from ..components.library_grade_text import clear_sky_text, frame_grade_headline
from ..theme.tokens import Colors
from .library_format import fmt_clock

LANE_H = 40
_STRIP_H = 5
_TRACE_PAD = 4


def lane_colors():
    """State → colour, read at paint time so a theme pack is followed.
    Not OK is the warning colour, never the error red: it is a pause."""
    return {
        GT.GRADE_OK: Colors.success_default,
        GT.GRADE_NOT_OK: Colors.warning_default,
        GT.GRADE_UNGRADED: Colors.gray_6,
    }


def lane_legend_html() -> str:
    """One caption line naming the lane's colours and its line."""
    colors = lane_colors()

    def chip(state, label):
        return f'<span style="color:{colors[state]};">■</span> {label}'

    return "  ".join((
        chip(GT.GRADE_OK, "imaging OK"), chip(GT.GRADE_NOT_OK, "not OK"),
        chip(GT.GRADE_UNGRADED, "not graded"),
        f'<span style="color:{Colors.gray_12};">─</span> stars vs clear sky',
    )).replace("  ", "&nbsp;&nbsp;&nbsp;")


def lane_tooltip(row) -> str:
    """'02:10 · Not OK · Cloud cover high' plus the clear-sky line."""
    clock = fmt_clock(row.get('captured_at'))
    if row.get('imaging_ok') is None:
        return f"{clock} · not graded"
    text = f"{clock} · {frame_grade_headline(row)[0]}"
    ratio = clear_sky_text(row.get('clear_sky_ratio'))
    return f"{text}\n{ratio}" if ratio else text


class GradeLane:
    """Timeline data + a cached rendering of it. Not a widget: the scrubber
    owns the geometry, the playhead and the mouse."""

    def __init__(self):
        self._timeline = GT.build_grade_timeline([])
        self._count = 0
        self._cache = None
        self._cache_key = None
        self.renders = 0  # full lane renderings (tests / timing)

    def set_rows(self, rows):
        self._timeline = GT.build_grade_timeline(rows)
        self._count = len(rows or [])
        self._cache = None

    def has_grades(self) -> bool:
        return bool(self._timeline['graded'])

    def state_at(self, index):
        states = self._timeline['states']
        return states[index] if 0 <= index < len(states) else None

    def segments(self):
        return list(self._timeline['segments'])

    def paint(self, painter, x, y, w, dpr=1.0):
        """Blit the lane at (x, y), rendering it first if the night, the
        width or the theme colours changed."""
        colors = lane_colors()
        key = (int(w), round(dpr, 2), tuple(sorted(colors.items())), Colors.gray_2)
        if self._cache is None or self._cache_key != key:
            self._cache = self._render(int(w), dpr, colors)
            self._cache_key = key
        painter.drawPixmap(QPointF(x, y), self._cache)

    def marker_y(self, index):
        """Lane-relative y of the trace at ``index``, or None where the
        clear-sky ratio was unknown."""
        ratios = self._timeline['ratios']
        if not (0 <= index < len(ratios)) or ratios[index] is None:
            return None
        return self._ratio_y(ratios[index])

    def _ratio_y(self, ratio):
        top, bottom = _TRACE_PAD, LANE_H - _STRIP_H - _TRACE_PAD
        return bottom - (ratio / self._timeline['scale_max']) * (bottom - top)

    def _frac(self, index):
        return index / (self._count - 1) if self._count > 1 else 0.0

    def _render(self, w, dpr, colors):
        self.renders += 1
        pix = QPixmap(max(1, int(w * dpr)), int(LANE_H * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(QColor(Colors.gray_2))
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        for frac0, frac1, state in self._timeline['segments']:
            x0, x1 = frac0 * w, frac1 * w
            rect_w = max(1.0, x1 - x0)
            p.fillRect(QRectF(x0, LANE_H - _STRIP_H, rect_w, _STRIP_H),
                       QColor(colors[state]))

        # 100 % of clear sky, as a guide for the trace.
        guide = QPen(QColor(Colors.gray_8), 1, Qt.DotLine)
        p.setPen(guide)
        y100 = self._ratio_y(1.0)
        p.drawLine(QPointF(0, y100), QPointF(w, y100))

        p.setPen(QPen(QColor(Colors.gray_12), 1.4))
        p.setBrush(QColor(Colors.gray_12))
        for run in self._timeline['runs']:
            if len(run) == 1:
                i, ratio = run[0]
                p.drawEllipse(QPointF(self._frac(i) * w, self._ratio_y(ratio)), 1.2, 1.2)
            else:
                p.drawPolyline(QPolygonF(
                    [QPointF(self._frac(i) * w, self._ratio_y(r)) for i, r in run]))

        p.end()
        return pix
