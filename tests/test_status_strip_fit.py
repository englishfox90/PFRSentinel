"""Status strip on a narrow window — low-priority tiles fold to their icon,
values elide at a word boundary with the whole text in the tooltip, and the
wide layout is untouched. IMAGING, the strip's one link, stays readable."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from ui.components.status_strip import StatusStrip
from ui.components.status_strip_fit import (
    COMPACT, ELIDED, FULL, FitItem, elide_at_word, plan_fit, word_steps,
)

PAUSED = "Paused · Cloud cover high"


def _measure(text):
    return 10 * len(text)  # one character, ten pixels


# --- Word-boundary elision (pure) -------------------------------------------
def test_text_that_fits_is_unchanged():
    assert elide_at_word(PAUSED, 1000, _measure) == PAUSED


def test_elides_at_a_word_and_drops_a_dangling_separator():
    assert elide_at_word(PAUSED, 160, _measure) == "Paused · Cloud…"
    assert elide_at_word(PAUSED, 100, _measure) == "Paused…"


def test_never_cuts_inside_a_word_while_a_whole_word_fits():
    for width in range(70, 260, 5):
        shown = elide_at_word(PAUSED, width, _measure)
        assert shown == PAUSED or shown.endswith("…")
        head = shown.rstrip("…")
        assert PAUSED.startswith(head)
        rest = PAUSED[len(head):]
        assert rest == "" or rest.startswith(" ")


def test_first_word_too_wide_uses_the_fallback():
    shown = elide_at_word(PAUSED, 30, _measure, fallback=lambda t, w: "P…")
    assert shown == "P…"


def test_word_steps_are_whole_word_heads_widest_first():
    steps = word_steps(PAUSED, _measure)
    assert steps == tuple(_measure(h) for h in
                          ("Paused · Cloud cover…", "Paused · Cloud…", "Paused…"))
    assert word_steps("Open", _measure) == ()


# --- plan_fit (pure) --------------------------------------------------------
def _item(key, value_w, **kw):
    return FitItem(key=key, chrome_w=50, label_w=30, value_w=value_w,
                   compact_w=50, **kw)


def test_everything_full_when_it_fits():
    items = [_item('a', 100, collapse_rank=0), _item('b', 100, elide_rank=0)]
    assert plan_fit(items, 300) == {'a': (FULL, None), 'b': (FULL, None)}


def test_tiles_fold_in_rank_order_and_only_as_far_as_needed():
    items = [_item('web', 100, collapse_rank=1), _item('weather', 100, collapse_rank=0),
             _item('imaging', 100)]
    plan = plan_fit(items, 400)
    assert plan['weather'] == (COMPACT, None)
    assert plan['web'] == (FULL, None)
    assert plan['imaging'] == (FULL, None)


def test_folding_comes_before_any_elision():
    items = [_item('web', 100, collapse_rank=0),
             _item('imaging', 200, elide_rank=0, steps=(150, 90))]
    plan = plan_fit(items, 300)
    assert plan == {'web': (COMPACT, None), 'imaging': (FULL, None)}


def test_lower_elide_rank_gives_up_words_before_a_higher_one():
    items = [_item('sky', 200, elide_rank=0, steps=(150, 90)),
             _item('imaging', 200, elide_rank=1, steps=(150, 90))]
    plan = plan_fit(items, 450)
    assert plan['imaging'] == (FULL, None)
    assert plan['sky'] == (ELIDED, 150)


def test_a_tier_takes_turns_so_no_value_is_cut_to_a_stub_first():
    items = [_item('pill', 300, elide_rank=0, steps=(250, 200, 150)),
             _item('sky', 150, elide_rank=0, steps=(100, 60))]
    plan = plan_fit(items, 520)
    assert plan['pill'] == (ELIDED, 250)
    assert plan['sky'] == (FULL, None)


# --- The widget -------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_strip(qapp):
    made = []

    def build(width, fill):
        strip = StatusStrip()
        fill(strip)
        strip.setFixedWidth(width)
        strip.show()
        qapp.processEvents()
        made.append(strip)
        return strip

    yield build
    for strip in made:
        strip.close()
        strip.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _paused(strip):
    strip.set_web(True, True)
    strip.set_discord(True)
    strip.set_roof("Open", 'ok')
    strip.set_sky("Partly Cloudy", 'warn')
    strip.set_weather("8.4°C · Clouds · 75%", 'primary')
    strip.set_seeing("142 stars · FWHM 3.21", 'primary')
    strip.set_imaging(PAUSED, 'warn')
    strip.set_capture_state('capturing', "Capturing · frame 1234")


def _not_configured(strip):
    strip.set_web(False, False)
    strip.set_discord(False)
    strip.set_roof("Not configured", 'muted')
    strip.set_sky("Not configured", 'muted')
    strip.set_weather("Not configured", 'muted')
    strip.set_seeing("—", 'muted')
    strip.set_imaging(PAUSED, 'warn')
    strip.set_capture_state('idle', "Alpaca · not connected")


def _natural_width(strip):
    return sum(t.fit_item('x').width() for t in strip._tiles) + \
        strip.pill.fit_item('x').width()


def _roomy(make_strip, fill):
    """A width with room to spare on any platform's fonts (Windows' are wider)."""
    return max(1500, _natural_width(make_strip(1500, fill)) + 80)


def _assert_readable(tile, full):
    shown = tile.value.text()
    if shown == full:
        return
    assert shown.endswith("…")
    head = shown[:-1]
    assert full.startswith(head) and full[len(head)] == " "
    assert full in tile.toolTip()


def test_wide_strip_is_unchanged(make_strip):
    strip = make_strip(_roomy(make_strip, _paused), _paused)
    assert all(t.presentation == FULL for t in strip._tiles)
    assert strip.imaging_tile.value.text() == PAUSED
    assert strip.pill.value.text() == "Capturing · frame 1234"
    assert strip.imaging_tile.toolTip() == "Show imaging conditions on Live Monitoring"
    assert strip.web_tile.eyebrow.isVisible()


@pytest.mark.parametrize('fill', [_paused, _not_configured])
@pytest.mark.parametrize('width', [1100, 960, 900])
def test_narrow_strip_keeps_imaging_readable_and_fits(make_strip, fill, width):
    strip = make_strip(width, fill)
    _assert_readable(strip.imaging_tile, PAUSED)
    assert strip.imaging_tile.presentation != COMPACT
    for tile in (strip.roof_tile, strip.sky_tile):
        if not tile.is_not_configured():
            assert tile.presentation != COMPACT
    used = sum(t.sizeHint().width() for t in strip._tiles) + strip.pill.sizeHint().width()
    assert used <= width


def test_low_priority_tiles_fold_first(make_strip, qapp):
    probe = make_strip(1500, _paused)
    width = _natural_width(probe) - 40   # just short of room for everything
    strip = make_strip(width, _paused)
    assert strip.web_tile.presentation == COMPACT
    for tile in (strip.roof_tile, strip.sky_tile, strip.seeing_tile,
                 strip.weather_tile, strip.imaging_tile):
        assert tile.presentation == FULL
    assert strip.imaging_tile.value.text() == PAUSED


def test_not_configured_tiles_fold_before_the_publish_toggles(make_strip):
    probe = make_strip(1500, _not_configured)
    strip = make_strip(_natural_width(probe) - 40, _not_configured)
    assert strip.roof_tile.presentation == COMPACT
    assert strip.web_tile.presentation == FULL
    assert strip.discord_tile.presentation == FULL


def test_folded_tile_names_its_value_in_the_tooltip(make_strip):
    strip = make_strip(960, _not_configured)
    tile = strip.weather_tile
    assert tile.presentation == COMPACT
    assert not tile.value.isVisible()
    assert tile.toolTip() == "Weather: Not configured"


def test_folded_tile_keeps_its_own_tooltip_too(make_strip):
    strip = make_strip(960, _not_configured)
    strip.roof_tile.setToolTip("Frame is noise")
    assert strip.roof_tile.toolTip() == "Roof: Not configured\nFrame is noise"


def test_widening_restores_the_full_layout(make_strip, qapp):
    strip = make_strip(960, _paused)
    assert strip.web_tile.presentation == COMPACT
    strip.setFixedWidth(_roomy(make_strip, _paused))
    qapp.processEvents()
    assert all(t.presentation == FULL for t in strip._tiles)
    assert strip.imaging_tile.toolTip() == "Show imaging conditions on Live Monitoring"


def test_a_longer_value_refits_without_a_resize(make_strip, qapp):
    strip = make_strip(1500, _paused)
    probe_width = _natural_width(strip)
    strip.setFixedWidth(probe_width)
    qapp.processEvents()
    assert all(t.presentation == FULL for t in strip._tiles)
    strip.set_weather("8.4°C · Light rain and drizzle · 100%", 'primary')
    assert strip.web_tile.presentation == COMPACT


def test_worst_case_at_the_window_minimum_elides_at_words(make_strip):
    def stress(strip):
        _paused(strip)
        strip.set_roof("Open · unreliable", 'warn', 'alert-outline')
        strip.set_sky("Too much static", 'warn', 'alert-outline')
        strip.set_imaging("Paused · Stars soft (FWHM)", 'warn')
        strip.set_capture_state('reconnecting', "Reconnected · waiting for a frame")

    strip = make_strip(760, stress)
    _assert_readable(strip.roof_tile, "Open · unreliable")
    _assert_readable(strip.sky_tile, "Too much static")
    _assert_readable(strip.imaging_tile, "Paused · Stars soft (FWHM)")
    pill = strip.pill.value.text()
    assert pill == "Reconnected · waiting for a frame" or (
        pill.endswith("…") and strip.pill.toolTip() == "Reconnected · waiting for a frame")


def test_an_unshown_strip_is_never_trimmed(qapp):
    strip = StatusStrip()
    _paused(strip)
    assert all(t.presentation == FULL for t in strip._tiles)
    assert strip.imaging_tile.value.text() == PAUSED
    strip.deleteLater()
