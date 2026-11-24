"""A QLabel whose colour, size and weight are stated outright.

The app stylesheet's ``QWidget { font-size: 14px }`` outranks
``QWidget.setFont()``, and qfluentwidgets re-applies its own label
stylesheet whenever the accent changes, so a ``CaptionLabel`` given a colour
with ``setStyleSheet`` ends up body-sized and white. Text that has to stay
small or muted says so in its own stylesheet.
"""
from PySide6.QtWidgets import QLabel

from ..theme.tokens import Colors, Typography


def label_qss(color: str = None, size: int = None, weight: int = None) -> str:
    return (f"color: {color or Colors.text_secondary}; "
            f"font-size: {size or Typography.size_caption}px; "
            f"font-weight: {weight or Typography.weight_regular}; "
            "background: transparent;")


def plain_label(text: str = "", *, color: str = None, size: int = None,
                weight: int = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    label.setStyleSheet(label_qss(color, size, weight))
    return label


def section_label(text: str) -> QLabel:
    """A small upper-case heading inside a card, as the nav rail's groups."""
    label = QLabel(text.upper())
    label.setStyleSheet(
        f"color: {Colors.gray_10}; font-size: {Typography.size_small}px; "
        f"font-weight: {Typography.weight_semibold}; letter-spacing: 0.07em; "
        "background: transparent;")
    return label
