"""Tests for special theme packs — token patching, reset, pack integrity, UI wiring."""
import json
import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from ui.theme import special_themes
from ui.theme.accent_themes import ACCENT_PRESETS
from ui.theme.special_themes import SPECIAL_THEMES, apply_appearance
from ui.theme.styles import get_stylesheet
from ui.theme.tokens import Colors

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS = sorted(SPECIAL_THEMES)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def restore_default_appearance(qapp):
    yield
    apply_appearance('iris', '')


def _flush(qapp, *widgets):
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _luminance(hex_value: str) -> float:
    def channel(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    c = QColor(hex_value)
    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


# --- token patching -------------------------------------------------------

def test_alias_map_matches_the_token_defaults():
    defaults = special_themes._COLOR_DEFAULTS
    for alias, source in special_themes._ALIASES.items():
        assert defaults[alias] == defaults[source], f"{alias} no longer aliases {source}"


def test_pack_overrides_accent_and_neutrals_and_rederives_aliases():
    apply_appearance('nebula', 'halloween')
    colors = SPECIAL_THEMES['halloween']['colors']

    assert special_themes.active_special_theme_key() == 'halloween'
    assert Colors.accent_default == colors['iris_9']
    assert Colors.bg_app == colors['gray_1']
    assert Colors.bg_card == colors['gray_3']
    assert Colors.border_subtle == colors['gray_6']
    assert Colors.text_on_accent == colors['text_on_accent']


def test_switching_a_pack_off_leaves_nothing_behind():
    apply_appearance('nebula', 'halloween')
    apply_appearance('nebula', '')

    assert special_themes.active_special_theme() is None
    nebula = ACCENT_PRESETS['nebula']['colors']
    assert Colors.accent_default == nebula['iris_9']
    accent_driven = set(nebula) | {
        alias for alias, source in special_themes._ALIASES.items() if source.startswith('iris_')
    }
    for attr, default in special_themes._COLOR_DEFAULTS.items():
        if attr not in accent_driven:
            assert getattr(Colors, attr) == default, attr
    assert 'SubtitleLabel' not in get_stylesheet()


def test_unknown_pack_is_treated_as_off():
    apply_appearance('iris', 'easter-2031')

    assert special_themes.active_special_theme_key() == ''
    assert Colors.bg_app == special_themes._COLOR_DEFAULTS['bg_app']


def test_heading_rule_is_emitted_only_while_a_display_font_is_active():
    apply_appearance('iris', 'halloween')
    sheet = get_stylesheet()

    if special_themes.display_font_active():
        assert 'SubtitleLabel' in sheet and 'Creepster' in sheet
        # Same specificity as the global QWidget rule, so it must come after it.
        assert sheet.index('SubtitleLabel') > sheet.index('QWidget {')
    else:
        assert 'SubtitleLabel' not in sheet


# --- pack integrity -------------------------------------------------------

@pytest.mark.parametrize("key", PACKS)
def test_pack_never_touches_status_colours(key):
    for attr in SPECIAL_THEMES[key]['colors']:
        assert not attr.startswith(('success', 'warning', 'error', 'info', 'status'))
        assert attr in special_themes._COLOR_DEFAULTS, f"{attr} is not a Colors token"


@pytest.mark.parametrize("key", PACKS)
def test_pack_neutrals_keep_dark_site_luminance(key):
    for attr, value in SPECIAL_THEMES[key]['colors'].items():
        if attr.startswith('gray_'):
            default = special_themes._COLOR_DEFAULTS[attr]
            assert _luminance(value) <= _luminance(default) * 1.10, f"{attr} is brighter than Sand"


@pytest.mark.parametrize("key", PACKS)
def test_pack_icons_exist_in_the_bundled_mdi_font(key):
    import qtawesome
    fonts = os.path.join(os.path.dirname(qtawesome.__file__), 'fonts')
    charmap_file = next(f for f in os.listdir(fonts)
                        if f.startswith('materialdesignicons6') and f.endswith('.json'))
    with open(os.path.join(fonts, charmap_file), encoding='utf-8') as fh:
        charmap = json.load(fh)

    pack = SPECIAL_THEMES[key]
    for name in [pack['icon'], *pack.get('nav_icons', {}).values()]:
        assert name in charmap, f"mdi6.{name} is not in the bundled icon font"


@pytest.mark.parametrize("key", PACKS)
def test_pack_font_is_bundled_with_its_licence(key):
    font_file = SPECIAL_THEMES[key].get('display_font_file')
    if not font_file:
        pytest.skip("pack has no display font")

    assert os.path.isfile(os.path.join(REPO, 'assets', 'fonts', font_file))
    with open(os.path.join(REPO, 'PFRSentinel.spec'), encoding='utf-8') as fh:
        assert f"assets/fonts/{font_file}" in fh.read()


# --- widgets --------------------------------------------------------------

@pytest.mark.parametrize("key", PACKS)
def test_nav_rail_swaps_icons_for_real_sections_and_restores_them(qapp, monkeypatch, key):
    from ui.components import nav_rail as nav_module

    requested = []
    real_mdi = nav_module.mdi
    monkeypatch.setattr(nav_module, 'mdi', lambda name, *a: requested.append(name) or real_mdi(name, *a))
    nav = nav_module.NavRail()
    try:
        pack_icons = SPECIAL_THEMES[key]['nav_icons']
        assert set(pack_icons) <= set(nav._buttons), "pack names a nav section that does not exist"

        apply_appearance('iris', key)
        requested.clear()
        nav.refresh_styles()
        assert set(pack_icons.values()) <= set(requested)

        apply_appearance('iris', '')
        requested.clear()
        nav.refresh_styles()
        assert not set(pack_icons.values()) & set(requested)
        assert 'camera-plus-outline' in requested
    finally:
        _flush(qapp, nav)


@pytest.mark.parametrize("special", ['', *PACKS])
def test_every_sprite_state_paints(qapp, special):
    from ui.components.status_sprite import StatusSpriteWidget

    apply_appearance('iris', special)
    sprite = StatusSpriteWidget()
    sprite.setFixedSize(44, 44)
    try:
        for state in StatusSpriteWidget.STATE_TOOLTIPS:
            sprite.set_state(state)
            for frame in (0, 30, 100, 260):
                sprite._frame = frame
                canvas = QPixmap(44, 44)
                canvas.fill(QColor(0, 0, 0, 0))
                sprite.render(canvas)
                image = canvas.toImage()
                painted = any(image.pixelColor(x, y).alpha() for x in range(0, 44, 2) for y in range(0, 44, 2))
                assert painted, f"{special or 'standard'}/{state} drew nothing at frame {frame}"
        sprite.set_state(None)
    finally:
        _flush(qapp, sprite)


def test_waiting_words_follow_the_active_pack(qapp):
    from ui.components.status_sprite import StatusSpriteWidget

    sprite = StatusSpriteWidget()
    try:
        pack = SPECIAL_THEMES['halloween']
        assert sprite._next_waiting_word(pack) in pack['waiting_words']
        assert sprite._next_waiting_word(None) in StatusSpriteWidget.WAITING_WORDS
    finally:
        _flush(qapp, sprite)


def test_appearance_card_chip_toggles_and_a_swatch_leaves_the_pack(qapp):
    from ui.panels.appearance_card import AppearanceCard

    card = AppearanceCard()
    events = []
    card.accent_selected.connect(lambda k: events.append(('accent', k)))
    card.special_theme_selected.connect(lambda k: events.append(('special', k)))
    try:
        card.set_selection('nebula', '')
        assert events == []

        card._chips['halloween'].click()
        assert events == [('special', 'halloween')]
        assert card._swatches['nebula'].isChecked(), "the saved accent stays selected underneath"

        card._chips['halloween'].click()
        assert events[-1] == ('special', '')

        card._chips['halloween'].click()
        events.clear()
        card._swatches['aurora'].click()
        assert events == [('special', ''), ('accent', 'aurora')]
        assert not card._chips['halloween'].isChecked()
    finally:
        _flush(qapp, card)


def test_saved_pack_styles_its_chip_on_load_without_a_click(qapp):
    from ui.panels.appearance_card import AppearanceCard

    card = AppearanceCard()
    try:
        apply_appearance('nebula', 'halloween')
        card.set_selection('nebula', 'halloween')

        assert Colors.accent_default in card._chips['halloween'].styleSheet()
    finally:
        _flush(qapp, card)


def _wait(qapp, ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_sending_hold_ends_in_the_next_status_read_at_that_moment(qapp):
    from ui.components.app_bar import AppBar

    bar = AppBar()
    capturing = {'on': True}
    try:
        bar.show_sending(lambda: 'waiting' if capturing['on'] else None, hold_ms=20)
        assert bar.status_sprite._state == 'sending'
        capturing['on'] = False
        _wait(qapp, 80)

        assert bar.status_sprite._state is None
    finally:
        _flush(qapp, bar)


def test_a_newer_frame_cancels_a_stale_sending_hold(qapp):
    from ui.components.app_bar import AppBar

    bar = AppBar()
    try:
        bar.show_sending(lambda: 'waiting', hold_ms=20)
        bar.set_status('capturing')
        _wait(qapp, 80)

        assert bar.status_sprite._state == 'capturing'
    finally:
        _flush(qapp, bar)
