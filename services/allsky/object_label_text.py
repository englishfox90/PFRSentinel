"""
The text of a Messier or NGC/IC label (issue #144).

"Andromeda Galaxy (M31)" was the only form, and on a timelapse it was too
long: the reporter asked for the common name alone, the layer colour
already telling the catalogues apart. Shorter text also collides less, and
a label that loses its slot to a collision on one frame and wins it on the
next is a flicker of its own. ``allsky_overlay.messier.label_style`` and
``allsky_overlay.ngc.label_style`` pick one of three forms; an object with
no common name always shows its catalogue number. The NGC catalogue lists
some objects under several names ("Eagle Nebula,Star Queen"); the first
stands for the object.
"""
from typing import Tuple

CONFIG_KEY = 'label_style'
DEFAULT_STYLE = 'name'

# (config value, display name)
STYLES: Tuple[Tuple[str, str], ...] = (
    ('name', 'Common name'),
    ('name_number', 'Common name and number'),
    ('number', 'Catalogue number'),
)


def style_keys() -> Tuple[str, ...]:
    return tuple(key for key, _ in STYLES)


def style_names() -> Tuple[str, ...]:
    return tuple(name for _, name in STYLES)


def known_style(style) -> str:
    """``style`` when it is one of STYLES, else the default."""
    return style if style in style_keys() else DEFAULT_STYLE


def object_label_text(common_name, catalogue_id: str, style) -> str:
    """The label for an object with ``common_name`` (may be empty or None)
    and ``catalogue_id`` ('M31', 'NGC0457') in ``style``; an unknown style
    reads as the default."""
    common = (common_name or '').split(',')[0].strip()
    style = known_style(style)
    if not common or style == 'number':
        return catalogue_id
    if style == 'name_number':
        return f"{common} ({catalogue_id})"
    return common
