"""NINA Target card on the All-Sky settings page (issue #137).

Layout only: the controls for ``allsky_overlay.nina_target``. The target
itself arrives from the NINA plugin over the control API; this card only
decides whether and how it is drawn.
"""
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from qfluentwidgets import CaptionLabel

from services.config_defaults import DEFAULT_CONFIG
from ..components.cards import CollapsibleCard
from ..components.scroll_safe_spinbox import SpinBox
from ..theme.icons import mdi
from .allsky_settings_rows import LayerToggleRow, ColorPaletteRow

_DEFAULTS = DEFAULT_CONFIG['allsky_overlay']['nina_target']

STALE_MIN_S = 30
STALE_MAX_S = 3600
STALE_STEP_S = 10


class NinaTargetCard(CollapsibleCard):
    """Show/hide the NINA target and its field of view, colour, staleness."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("NINA Target", mdi('crosshairs-gps'), parent)
        caption = CaptionLabel(
            "Marks the target the NINA plugin reports and the field of view of "
            "NINA's imaging camera. Hidden when NINA stops reporting for longer "
            "than the time below."
        )
        caption.setWordWrap(True)
        self.add_widget(caption)

        self._enabled = LayerToggleRow("Show NINA target", default=bool(_DEFAULTS['enabled']))
        self._show_fov = LayerToggleRow("Show field of view", default=bool(_DEFAULTS['show_fov']))
        self._color = ColorPaletteRow(_DEFAULTS['color'])
        self._stale = SpinBox()
        self._stale.setRange(STALE_MIN_S, STALE_MAX_S)
        self._stale.setSingleStep(STALE_STEP_S)
        self._stale.setValue(int(_DEFAULTS['stale_after_s']))

        for row in (self._enabled, self._show_fov):
            row.toggled.connect(self._emit_changed)
            self.add_widget(row)
        self._color.color_changed.connect(self._emit_changed)
        self.add_widget(self._color)
        self._stale.valueChanged.connect(self._emit_changed)
        self.add_row("Hide after (s)", self._stale)

    def load(self, layer_cfg: dict) -> None:
        """Set every control from ``layer_cfg`` without emitting ``changed``."""
        cfg = layer_cfg if isinstance(layer_cfg, dict) else {}
        widgets = (self, self._enabled, self._show_fov, self._color, self._stale)
        previous = [w.blockSignals(True) for w in widgets]
        try:
            self._enabled.set_checked(bool(cfg.get('enabled', _DEFAULTS['enabled'])))
            self._show_fov.set_checked(bool(cfg.get('show_fov', _DEFAULTS['show_fov'])))
            self._color.set_color(_valid_color(cfg.get('color')))
            self._stale.setValue(_stale_seconds(cfg.get('stale_after_s')))
        finally:
            for w, was_blocked in zip(widgets, previous):
                w.blockSignals(was_blocked)

    def values(self) -> dict:
        return {
            'enabled': self._enabled.is_checked(),
            'show_fov': self._show_fov.is_checked(),
            'color': self._color.selected_color(),
            'stale_after_s': int(self._stale.value()),
        }

    def _emit_changed(self, *_):
        self.changed.emit()


def _valid_color(value) -> str:
    # A hand-edited config can carry anything; the swatch stylesheet and the
    # renderer both need a colour Qt and PIL can parse.
    if isinstance(value, str) and QColor.isValidColorName(value):
        return value
    return _DEFAULTS['color']


def _stale_seconds(value) -> int:
    try:
        seconds = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return int(_DEFAULTS['stale_after_s'])
    return max(STALE_MIN_S, min(STALE_MAX_S, seconds))
