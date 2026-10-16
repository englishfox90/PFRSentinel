"""
A labelled Stretch slider that reports a display-stretch strength (0-1)
once the user has stopped moving it.

Whoever listens re-renders a frame of up to 12 MP on the GUI thread, so the
slider settles first: one `settled` after a short quiet spell, not one per
tick of a drag.
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from .cards import ClickSlider
from ..theme.tokens import Spacing

_SETTLE_MS = 40
_SLIDER_MAX = 100
_SLIDER_WIDTH = 160


class DisplayStretchSlider(QWidget):
    """Signals: settled(float) — the strength the slider came to rest on."""

    settled = Signal(float)

    def __init__(self, strength: float, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_SETTLE_MS)
        self._timer.timeout.connect(self.settle)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Spacing.xs)
        row.addWidget(CaptionLabel("Stretch"))
        self.slider = ClickSlider(Qt.Horizontal)
        self.slider.setRange(0, _SLIDER_MAX)
        self.slider.setValue(round(strength * _SLIDER_MAX))
        self.slider.setFixedWidth(_SLIDER_WIDTH)
        self.slider.setToolTip(
            "How hard the frame is brightened for display. Left is softer "
            "(fewer pixels saturate), right is harder. The middle is what the "
            "star detector sees. Display only — the solve is unaffected.")
        self.slider.valueChanged.connect(lambda _value: self._timer.start())
        row.addWidget(self.slider)

    @property
    def strength(self) -> float:
        return self.slider.value() / float(_SLIDER_MAX)

    @property
    def settling(self) -> bool:
        """True while a move is waiting for its quiet spell to end."""
        return self._timer.isActive()

    def settle(self) -> None:
        """Report the current strength now, as the timer would."""
        self._timer.stop()
        self.settled.emit(self.strength)
