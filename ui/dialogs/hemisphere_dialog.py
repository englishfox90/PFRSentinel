"""Ask which hemisphere a coordinate saved without one was meant to be in.

Shown once at startup when the config migration found a degrees-minutes-
seconds latitude or longitude with no N/S/E/W letter and no minus sign. The
value is already usable (read as north / east); this only settles the sign.
Layout only — the defaults and the write-back live in
``services.coordinate_hemisphere``.
"""
from typing import Dict, Optional

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, MessageBoxBase, RadioButton, StrongBodyLabel, SubtitleLabel

from services.coordinate_hemisphere import HEMISPHERES

_FIELD_TITLES = {"latitude": "Latitude", "longitude": "Longitude"}
_OPTION_TEXT = {
    "N": "North of the equator",
    "S": "South of the equator",
    "E": "East of Greenwich (Europe, Africa, Asia, Australia)",
    "W": "West of Greenwich (the Americas)",
}


class HemisphereDialog(MessageBoxBase):
    """One radio pair per unresolved coordinate; Confirm or Ask me later."""

    def __init__(self, parent, pending: Dict[str, str], defaults: Dict[str, str]):
        super().__init__(parent)
        self._groups: Dict[str, QButtonGroup] = {}

        self.viewLayout.addWidget(SubtitleLabel("Confirm your observatory's hemisphere", self))
        intro = BodyLabel(
            "These coordinates were saved without a hemisphere letter, so the app "
            "has assumed one. Pick the right side so weather, sun windows, the "
            "night-time check and the all-sky overlay use the correct location.", self)
        intro.setWordWrap(True)
        self.viewLayout.addWidget(intro)

        for field in ("latitude", "longitude"):
            if field not in pending:
                continue
            self.viewLayout.addWidget(StrongBodyLabel(
                f"{_FIELD_TITLES[field]} — entered as “{pending[field]}”", self))
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            group = QButtonGroup(row)
            for letter in HEMISPHERES[field]:
                button = RadioButton(_OPTION_TEXT[letter], row)
                button.setProperty("hemisphere", letter)
                button.setChecked(letter == defaults.get(field))
                group.addButton(button)
                row_layout.addWidget(button)
            row_layout.addStretch(1)
            self._groups[field] = group
            self.viewLayout.addWidget(row)

        self.yesButton.setText("Confirm")
        self.cancelButton.setText("Ask me later")
        self.widget.setMinimumWidth(560)

    def choices(self) -> Dict[str, str]:
        """``{field: letter}`` as currently selected."""
        out = {}
        for field, group in self._groups.items():
            checked = group.checkedButton()
            if checked is not None:
                out[field] = checked.property("hemisphere")
        return out


def show_hemisphere_dialog(parent, pending: Dict[str, str],
                           defaults: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Run the dialog. Returns ``{field: letter}`` on Confirm, None on Ask me later."""
    dialog = HemisphereDialog(parent, pending, defaults)
    try:
        if dialog.exec():
            return dialog.choices()
        return None
    finally:
        dialog.deleteLater()
