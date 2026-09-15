"""Tests for the overlay settings panel split (fixes 1-5)."""
import os
import time

import pytest


# ---------------------------------------------------------------------------
# Fix 1: every token in the UI catalogue has a sample substitution value
# ---------------------------------------------------------------------------

class TestTokenCatalogue:
    def test_all_tokens_have_sample_values(self):
        from ui.panels.overlay_preview import TOKENS, substitute_tokens
        missing = [
            token
            for label, token in TOKENS
            if token and substitute_tokens(token) == token
        ]
        assert not missing, f"Tokens with no sample value in substitute_tokens: {missing}"

    def test_no_header_rows_have_a_token_value(self):
        from ui.panels.overlay_preview import TOKENS
        bad = [label for label, token in TOKENS if token is None and not label.startswith("━")]
        assert not bad, f"Non-separator rows with None token: {bad}"


# ---------------------------------------------------------------------------
# Fix 2: _anchor_xy free function — correct coordinates for all anchors
# ---------------------------------------------------------------------------

class TestAnchorXY:
    @pytest.fixture
    def xy(self):
        from ui.panels.overlay_preview import _anchor_xy
        return _anchor_xy

    def test_top_left(self, xy):
        x, y = xy("Top-Left", 10, 5, 800, 600, 100, 50, margin=8)
        assert x == 8 + 10
        assert y == 8 + 5

    def test_top_right(self, xy):
        x, y = xy("Top-Right", 10, 5, 800, 600, 100, 50, margin=8)
        assert x == 800 - 100 - 8 - 10
        assert y == 8 + 5

    def test_bottom_left(self, xy):
        x, y = xy("Bottom-Left", 10, 5, 800, 600, 100, 50, margin=8)
        assert x == 8 + 10
        assert y == 600 - 50 - 8 - 5

    def test_bottom_right(self, xy):
        x, y = xy("Bottom-Right", 10, 5, 800, 600, 100, 50, margin=8)
        assert x == 800 - 100 - 8 - 10
        assert y == 600 - 50 - 8 - 5

    def test_top_center(self, xy):
        x, y = xy("Top-Center", 3, 5, 800, 600, 100, 50, margin=8)
        assert x == (800 - 100) // 2 + 3
        assert y == 8 + 5

    def test_bottom_center(self, xy):
        x, y = xy("Bottom-Center", 0, 5, 800, 600, 100, 50, margin=8)
        assert x == (800 - 100) // 2
        assert y == 600 - 50 - 8 - 5

    def test_zero_margin_and_offset(self, xy):
        x, y = xy("Top-Left", 0, 0, 400, 300, 50, 20, margin=0)
        assert x == 0
        assert y == 0

    def test_positive_offset_moves_away_from_edge(self, xy):
        _, y_bottom = xy("Bottom-Left", 0, 20, 400, 300, 50, 20, margin=0)
        _, y_bottom_no_offset = xy("Bottom-Left", 0, 0, 400, 300, 50, 20, margin=0)
        assert y_bottom < y_bottom_no_offset  # offset pulls element away from bottom


# ---------------------------------------------------------------------------
# Fix 3: compass geometry constants come from the service module
# ---------------------------------------------------------------------------

class TestCompassConstants:
    def test_constants_exported_from_service(self):
        from services.compass_overlay import (
            COMPASS_CIRCLE_R, COMPASS_CARDINAL_LEN, COMPASS_ORDINAL_LEN,
            COMPASS_HALF_BASE, COMPASS_INNER_R, COMPASS_LABEL_R,
        )
        assert COMPASS_CIRCLE_R == 0.72
        assert COMPASS_CARDINAL_LEN == 0.68
        assert COMPASS_ORDINAL_LEN == 0.45
        assert COMPASS_HALF_BASE == 0.12
        assert COMPASS_INNER_R == 0.07
        assert COMPASS_LABEL_R == 0.88

    def test_preview_imports_service_constants(self):
        from services import compass_overlay as svc
        from ui.panels import overlay_preview as preview
        assert preview.COMPASS_CIRCLE_R is svc.COMPASS_CIRCLE_R
        assert preview.COMPASS_CARDINAL_LEN is svc.COMPASS_CARDINAL_LEN
        assert preview.COMPASS_ORDINAL_LEN is svc.COMPASS_ORDINAL_LEN
        assert preview.COMPASS_HALF_BASE is svc.COMPASS_HALF_BASE
        assert preview.COMPASS_INNER_R is svc.COMPASS_INNER_R
        assert preview.COMPASS_LABEL_R is svc.COMPASS_LABEL_R


# ---------------------------------------------------------------------------
# Qt-dependent fixtures and tests (fixes 4 & 5)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _dispose(widget, app):
    """Deterministic teardown for a top-level widget.

    OverlayPreviewCard.resizeEvent schedules QTimer.singleShot(100, ...)
    bound to the instance. If the widget is deleted before that fires, the
    timer later calls into a dead C++ object from inside an unrelated
    test's event loop pump — the exact "node down" crash pattern this repo
    hit under xdist. Pump events past the 100ms window before closing.
    """
    from PySide6.QtCore import QEvent
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        app.processEvents()
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _make_panel(qt_app):
    from ui.panels.overlay_settings import OverlaySettingsPanel
    panel = OverlaySettingsPanel()
    panel.load_from_config({
        "overlays": [
            {"name": "Alpha", "type": "text", "text": "hello",
             "anchor": "Bottom-Left", "offset_x": 15, "offset_y": 15,
             "font_size": 24, "font_style": "normal", "color": "white",
             "alignment": "left", "bg_enabled": False, "bg_color": "transparent"},
            {"name": "Beta", "type": "text", "text": "world",
             "anchor": "Top-Right", "offset_x": 10, "offset_y": 10,
             "font_size": 24, "font_style": "normal", "color": "white",
             "alignment": "left", "bg_enabled": False, "bg_color": "transparent"},
        ]
    })
    return panel


@pytest.fixture
def panel(qt_app):
    p = _make_panel(qt_app)
    yield p
    _dispose(p, qt_app)


@pytest.fixture
def preview_card(qt_app):
    from ui.panels.overlay_preview import OverlayPreviewCard
    card = OverlayPreviewCard()
    yield card
    _dispose(card, qt_app)


# ---------------------------------------------------------------------------
# Fix 4: name / text edits update only the relevant row, not the whole table
# ---------------------------------------------------------------------------

class TestTargetedListUpdate:
    def test_name_change_updates_only_changed_row(self, panel):
        panel.overlay_table.selectRow(0)
        panel.overlay_table.blockSignals(False)
        row_count_before = panel.overlay_table.rowCount()

        panel._overlays[0]["name"] = "Renamed"
        panel._update_list_row(0)

        assert panel.overlay_table.rowCount() == row_count_before
        assert panel.overlay_table.item(0, 0).text() == "Renamed"
        assert panel.overlay_table.item(1, 0).text() == "Beta"

    def test_text_change_updates_summary_cell(self, panel):
        panel.overlay_table.selectRow(1)
        panel._overlays[1]["text"] = "changed text"
        panel._update_list_row(1)

        assert panel.overlay_table.item(1, 2).text() == "changed text"
        assert panel.overlay_table.item(0, 2).text() == "hello"

    def test_out_of_range_index_is_a_no_op(self, panel):
        panel._update_list_row(-1)
        panel._update_list_row(999)


# ---------------------------------------------------------------------------
# Fix 5: starfield background is cached between same-size repaints
# ---------------------------------------------------------------------------

class TestStarfieldCache:
    def test_background_cached_on_same_size(self, preview_card):
        card = preview_card
        card.resize(300, 200)
        card._update_preview()
        first_bg = card._background_pixmap
        assert first_bg is not None
        card._update_preview()
        assert card._background_pixmap is first_bg

    def test_background_regenerated_on_size_change(self, preview_card):
        card = preview_card
        card.resize(300, 200)
        card._update_preview()
        first_bg = card._background_pixmap
        card._background_size = (0, 0)  # force regeneration
        card._update_preview()
        assert card._background_pixmap is not first_bg
