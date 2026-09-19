"""
Apply the saved accent + special theme to the main window and refresh the
widgets that bake token colours into inline stylesheets.
"""
from ..theme import get_stylesheet
from ..theme.special_themes import apply_appearance

# Components with a refresh_styles() hook. Panels that baked token colours at
# construction keep them until restart (the window applies the saved theme
# before building them) — the Settings card says so.
_REFRESHABLE = ('app_bar', 'nav_rail', 'status_strip')


def apply_window_appearance(window) -> None:
    apply_appearance(
        window.config.get('ui_accent', 'iris'),
        window.config.get('ui_special_theme', ''),
    )
    window.setStyleSheet(get_stylesheet())
    for name in _REFRESHABLE:
        widget = getattr(window, name, None)
        if widget is not None:
            widget.refresh_styles()
