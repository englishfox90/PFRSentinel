"""
Special (seasonal) theme packs.

An accent preset only swaps the iris colour scale. A special theme is a pack
that may also tint the neutrals, put a display font on headings, swap the nav
rail icons and re-skin the status sprite. It is stored separately from the
accent (`ui_special_theme` vs `ui_accent`) so switching it off restores the
user's own accent untouched.

Status colours, body text, mono telemetry and every published output are
deliberately outside a pack's reach.
"""
from PySide6.QtGui import QColor, QFontDatabase
from qfluentwidgets import setThemeColor

from services.logger import app_logger
from services.utils_paths import resource_path
from .tokens import Colors, Typography

SPECIAL_THEMES = {
    'halloween': {
        'label': 'Halloween',
        'icon': 'pumpkin',
        'colors': {
            # Pumpkin accent scale
            'iris_3':  '#2E1605', 'iris_4':  '#401E06', 'iris_5':  '#552808',
            'iris_6':  '#70340A', 'iris_7':  '#93440D', 'iris_8':  '#C25A10',
            'iris_9':  '#FF6A00', 'iris_10': '#FF8124', 'iris_11': '#FFA35C',
            'iris_12': '#FFE1C7',
            # Sand greys pushed toward aubergine at the same luminance, so
            # dark-site brightness is unchanged. Text greys are left alone.
            'gray_1': '#120F16', 'gray_2': '#19161E', 'gray_3': '#221E28',
            'gray_4': '#2A2531', 'gray_5': '#312C39', 'gray_6': '#3B3544',
            'gray_7': '#4A4355',
            # White on #FF6A00 is only ~2.9:1
            'text_on_accent': '#1A0D00',
        },
        'display_font_file': 'Creepster-Regular.ttf',
        'display_font_family': 'Creepster',
        # Creepster runs small; card headers otherwise inherit the 14px body size.
        'display_size': 22,
        # Keyed by nav rail section, not icon name, so nothing outside the rail changes.
        'nav_icons': {
            'monitoring': 'eye-outline',
            'capture': 'ghost-outline',
            'processing': 'bottle-tonic-skull-outline',
            'overlays': 'script-text-outline',
            'allsky': 'moon-waning-crescent',
            'output': 'bat',
            'timelapse': 'timer-sand',
            'library': 'grave-stone',
        },
        'sprite': 'halloween',
        'waiting_words': [
            "Boo!", "Witching hour", "It's alive!", "Full moon!", "Bats in dome",
            "Ghost photons", "Who's there?", "Trick or treat", "Seeing: eerie",
            "Very dark frames", "Spooky seeing", "Cobwebs!", "Hex: locked",
            "Moonlit!", "Dead pixels", "Haunted flats", "Creak...", "Fog rolls in",
            "Owl spotted", "Midnight!", "Bone cold", "Potion ready", "Eerie glow",
            "Ghoul's night", "No garlic", "Phantom star", "Black cat!", "Broom parked",
        ],
    },
}

# Semantic aliases in tokens.Colors are copied by value at class creation, so
# patching gray_1 does not move bg_app. Re-derive them after every patch.
_ALIASES = {
    'bg_app': 'gray_1', 'bg_surface': 'gray_2', 'bg_card': 'gray_3',
    'bg_input': 'gray_4', 'bg_hover': 'gray_5', 'bg_elevated': 'gray_3',
    'text_primary': 'gray_12', 'text_secondary': 'gray_11',
    'text_muted': 'gray_8', 'text_disabled': 'gray_9',
    'border_subtle': 'gray_6', 'border_default': 'gray_7', 'border_focus': 'iris_6',
    'accent_subtle': 'iris_3', 'accent_default': 'iris_9', 'accent_hover': 'iris_10',
    'accent_active': 'iris_4', 'accent_text': 'iris_11',
    'status_idle': 'gray_9', 'status_offline': 'gray_8',
}

_COLOR_DEFAULTS = {
    k: v for k, v in vars(Colors).items() if not k.startswith('_') and isinstance(v, str)
}
_DISPLAY_DEFAULT = Typography.family_display

_active_key = ''
_loaded_fonts = {}


def active_special_theme_key() -> str:
    return _active_key


def active_special_theme():
    """The active pack dict, or None when no special theme is on."""
    return SPECIAL_THEMES.get(_active_key)


def display_font_active() -> bool:
    return Typography.family_display != _DISPLAY_DEFAULT


def display_size() -> int:
    pack = active_special_theme()
    return pack.get('display_size', Typography.size_title) if pack else Typography.size_title


def _load_display_font(pack) -> str:
    """Register the pack's bundled font with Qt. Returns the family, '' on failure."""
    file_name = pack.get('display_font_file')
    if not file_name:
        return ''
    if file_name in _loaded_fonts:
        return _loaded_fonts[file_name]
    family = ''
    try:
        font_id = QFontDatabase.addApplicationFont(resource_path(f'assets/fonts/{file_name}'))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        family = families[0] if families else ''
    except Exception as e:
        app_logger.warning(f"Special theme font {file_name} failed to load: {e}")
    if not family:
        app_logger.warning(f"Special theme font {file_name} unavailable; headings keep the normal font")
    _loaded_fonts[file_name] = family
    return family


def apply_appearance(accent: str, special: str = '') -> None:
    """
    Apply the accent preset, then the special theme pack on top (if any).

    Always starts from the token defaults, so switching a pack off — or from
    one pack to another — leaves nothing behind. The caller re-applies
    get_stylesheet() and refreshes inline-styled widgets, as for an accent.
    """
    global _active_key
    from .styles import apply_accent_theme

    for attr, value in _COLOR_DEFAULTS.items():
        setattr(Colors, attr, value)
    Typography.family_display = _DISPLAY_DEFAULT

    apply_accent_theme(accent)

    pack = SPECIAL_THEMES.get(special or '')
    _active_key = special if pack else ''
    if pack:
        for attr, value in pack.get('colors', {}).items():
            setattr(Colors, attr, value)
        family = _load_display_font(pack)
        if family:
            Typography.family_display = f"'{family}', {_DISPLAY_DEFAULT}"

    for alias, source in _ALIASES.items():
        setattr(Colors, alias, getattr(Colors, source))
    setThemeColor(QColor(Colors.iris_9))
