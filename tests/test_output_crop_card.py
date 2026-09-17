"""
Tests for the Output Framing UI (issue #12): CropBoxEditor, OutputCropCard,
and OutputCropController.

Offscreen-Qt pattern follows tests/test_timelapse_status_card.py — module
scoped QApplication, per-test widget teardown (close/deleteLater/flush
DeferredDelete) so xdist workers don't inherit leaked Qt objects.
"""
import time

import pytest

pytest.importorskip("PySide6")

from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from services.output_crop import resolve_crop_box
from ui.controllers.output_crop_controller import OutputCropController
from ui.panels.crop_box_editor import CropBoxEditor
from ui.panels.output_crop_card import OutputCropCard

DEADLINE_S = 5.0
POLL_S = 0.02


def _pump_until(qapp, predicate, deadline=DEADLINE_S):
    """Poll processEvents for a worker-thread signal result, never a bare sleep."""
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(POLL_S)
    return False


def _send_mouse(widget, event_type, pos, buttons=Qt.LeftButton):
    ev = QMouseEvent(event_type, pos, pos, Qt.LeftButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def _drag(editor, start: QPointF, end: QPointF):
    _send_mouse(editor, QEvent.MouseButtonPress, start)
    _send_mouse(editor, QEvent.MouseMove, end)
    _send_mouse(editor, QEvent.MouseButtonRelease, end, buttons=Qt.NoButton)


class FakeConfig:
    """Dict-backed stand-in for services.config.Config — never the real thing."""

    def __init__(self, initial=None):
        self.d = dict(initial or {})

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


class FakeMainWindow:
    def __init__(self, config=None):
        self.config = config or FakeConfig()


def _solid_frame(size=512, color=(2, 2, 2)):
    return Image.new("RGB", (size, size), color)


def _disc_frame(size=3552, cx=None, cy=None, radius=1400, bg=(2, 2, 2), fg=(90, 90, 120)):
    cx = size // 2 + 100 if cx is None else cx
    cy = size // 2 - 200 if cy is None else cy
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fg)
    return img, cx, cy, radius


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _close_widget(qapp, widget):
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def card(qapp):
    mw = FakeMainWindow()
    widget = OutputCropCard(mw)
    # CollapsibleCard starts collapsed; force content visible + give the
    # editor real geometry so drag/resize coordinate math has something to
    # letterbox into (mirrors the ui_smoke.py driver).
    widget.content.setVisible(True)
    widget.resize(700, 600)
    widget.show()
    qapp.processEvents()
    yield widget, mw
    _close_widget(qapp, widget)


@pytest.fixture
def editor(qapp):
    widget = CropBoxEditor()
    widget.resize(700, 500)
    widget.show()
    qapp.processEvents()
    yield widget
    _close_widget(qapp, widget)


@pytest.fixture
def controller(qapp):
    ctrl = OutputCropController()
    yield ctrl
    ctrl.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 1. No frame, empty config
# ---------------------------------------------------------------------------

class TestNoReferenceFrame:
    def test_status_and_spins_before_any_frame(self, card):
        widget, mw = card
        widget.load_from_config(mw.config)

        assert "No reference frame" in widget.status_label.text()
        assert not widget.x_spin.isEnabled()
        assert not widget.y_spin.isEnabled()
        assert not widget.w_spin.isEnabled()
        assert not widget.h_spin.isEnabled()
        assert widget.editor.box() == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# 2. load_from_config restores state without a frame
# ---------------------------------------------------------------------------

class TestLoadFromConfig:
    def test_restores_reference_box_and_switches(self, card):
        widget, mw = card
        mw.config.set('output_crop', {
            'enabled': True, 'x': 100, 'y': 200, 'width': 1000, 'height': 1000,
            'ref_width': 3552, 'ref_height': 3552, 'keep_square': False,
        })

        widget.load_from_config(mw.config)

        assert widget.editor.reference() == (3552, 3552)
        assert widget.editor.box() == (100, 200, 1000, 1000)
        assert widget.enable_switch.is_checked() is True
        assert widget.square_switch.isChecked() is False
        assert "1000" in widget.status_label.text() or "1000×1000" in widget.status_label.text()
        assert "(100, 200)" in widget.status_label.text()

    def test_disabled_crop_reports_full_frame_status(self, card):
        widget, mw = card
        mw.config.set('output_crop', {
            'enabled': False, 'x': 0, 'y': 0, 'width': 800, 'height': 800,
            'ref_width': 1600, 'ref_height': 1600, 'keep_square': True,
        })

        widget.load_from_config(mw.config)

        assert widget.enable_switch.is_checked() is False
        assert "1600" in widget.status_label.text()
        assert "Crop off" in widget.status_label.text()


# ---------------------------------------------------------------------------
# 3. First set_frame seeds a centred default, doesn't save; enabling saves
# ---------------------------------------------------------------------------

class TestFirstFrameDefaultBox:
    def test_first_frame_seeds_centred_80pct_box_without_saving(self, card):
        widget, mw = card
        widget.load_from_config(mw.config)
        thumb = _solid_frame(512)

        widget.set_frame(thumb, 1000, 1000)

        x, y, w, h = widget.editor.box()
        assert w == 800 and h == 800          # 80% of 1000, square by default
        assert x == 100 and y == 100           # centred
        assert mw.config.get('output_crop') is None   # not saved yet

    def test_enabling_after_first_frame_saves_and_emits(self, card):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 1000, 1000)

        received = []
        widget.settings_changed.connect(lambda: received.append(True))
        widget.enable_switch.set_checked(True)

        assert received == [True]
        saved = mw.config.get('output_crop')
        assert saved is not None
        assert saved['enabled'] is True
        assert saved['width'] == 800 and saved['height'] == 800


# ---------------------------------------------------------------------------
# 4. Dragging inside the box moves it
# ---------------------------------------------------------------------------

class TestDragMove:
    def test_drag_moves_box_updates_spins_and_saves_once(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.enable_switch.set_checked(True)
        qapp.processEvents()

        ed = widget.editor
        s = ed._scale()
        box = ed._box_rect()
        start = QPointF(box.center())
        end = QPointF(box.center().x() + 200 * s, box.center().y())

        changed_count = []
        committed_count = []
        settings_count = []
        ed.box_changed.connect(lambda *a: changed_count.append(a))
        ed.box_committed.connect(lambda *a: committed_count.append(a))
        widget.settings_changed.connect(lambda: settings_count.append(True))

        before_x = ed.box()[0]
        _send_mouse(ed, QEvent.MouseButtonPress, start)
        _send_mouse(ed, QEvent.MouseMove, end)
        qapp.processEvents()

        # Mid-drag: box_changed fired, box_committed and settings_changed did not.
        assert len(changed_count) >= 1
        assert committed_count == []
        assert settings_count == []
        assert widget.x_spin.value() == ed.box()[0]

        _send_mouse(ed, QEvent.MouseButtonRelease, end, buttons=Qt.NoButton)
        qapp.processEvents()

        # Release: exactly one commit, exactly one save.
        assert len(committed_count) == 1
        assert len(settings_count) == 1
        after_x = ed.box()[0]
        assert after_x > before_x
        assert widget.x_spin.value() == after_x
        saved = mw.config.get('output_crop')
        assert saved['x'] == after_x


# ---------------------------------------------------------------------------
# 5. Corner resize keep_square vs not
# ---------------------------------------------------------------------------

class TestCornerResize:
    def test_square_resize_keeps_box_square_and_even(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.enable_switch.set_checked(True)
        assert widget.square_switch.isChecked() is True
        qapp.processEvents()

        ed = widget.editor
        s = ed._scale()
        box = ed._box_rect()
        se = QPointF(box.right(), box.bottom())
        target = QPointF(se.x() - 300 * s, se.y() - 100 * s)
        _drag(ed, se, target)
        qapp.processEvents()

        x, y, w, h = ed.box()
        assert w == h
        assert w % 2 == 0

    def test_edge_resize_without_keep_square_changes_one_dimension(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.square_switch.setChecked(False)
        widget.enable_switch.set_checked(True)
        qapp.processEvents()

        ed = widget.editor
        before_w, before_h = ed.box()[2], ed.box()[3]
        s = ed._scale()
        box = ed._box_rect()
        east = QPointF(box.right(), box.center().y())
        target = QPointF(east.x() - 300 * s, east.y())
        _drag(ed, east, target)
        qapp.processEvents()

        after_w, after_h = ed.box()[2], ed.box()[3]
        assert after_h == before_h
        assert after_w != before_w


# ---------------------------------------------------------------------------
# 6. Spin clamping
# ---------------------------------------------------------------------------

class TestSpinClamping:
    def test_x_spin_beyond_frame_is_clamped_and_saved(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.enable_switch.set_checked(True)
        widget.square_switch.setChecked(True)
        qapp.processEvents()
        # Fix width/height to a known value via a direct box set so the
        # clamp arithmetic below is exact.
        widget._set_box(0, 0, 2540, 2540, save=True)
        qapp.processEvents()

        widget.x_spin.setRange(0, 4000)   # spin's own range would otherwise clip at ref_w
        widget.x_spin.setValue(4000)
        qapp.processEvents()

        x, y, w, h = widget.editor.box()
        assert w == 2540
        assert x == 3552 - 2540  # == 1012
        saved = mw.config.get('output_crop')
        assert saved['x'] == 1012


# ---------------------------------------------------------------------------
# 7. Centre / Full frame buttons
# ---------------------------------------------------------------------------

class TestButtons:
    def test_centre_button_centres_current_box_size(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.enable_switch.set_checked(True)
        widget._set_box(0, 0, 1000, 1000, save=True)
        qapp.processEvents()

        widget.centre_btn.click()
        qapp.processEvents()

        x, y, w, h = widget.editor.box()
        assert (w, h) == (1000, 1000)
        assert x == (3552 - 1000) // 2 or abs(x - (3552 - 1000) / 2) <= 1
        assert y == (3552 - 1000) // 2 or abs(y - (3552 - 1000) / 2) <= 1
        assert mw.config.get('output_crop')['x'] == x

    def test_full_frame_button_resets_to_whole_frame(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.enable_switch.set_checked(True)
        widget._set_box(500, 500, 1000, 1000, save=True)
        qapp.processEvents()

        widget.reset_btn.click()
        qapp.processEvents()

        x, y, w, h = widget.editor.box()
        assert (x, y) == (0, 0)
        assert w >= 3550 and h >= 3550   # evenness may shave one px off 3552
        saved = mw.config.get('output_crop')
        assert saved['width'] == w and saved['height'] == h


# ---------------------------------------------------------------------------
# 8. set_reference scales an existing box proportionally
# ---------------------------------------------------------------------------

class TestSetReferenceScaling:
    def test_changing_reference_size_scales_box_proportionally(self, editor):
        editor.set_reference(3552, 3552)
        editor.set_box(100, 200, 1000, 1000)
        assert editor.box() == (100, 200, 1000, 1000)

        editor.set_reference(1776, 1776)   # half the linear size

        x, y, w, h = editor.box()
        assert w == 500 and h == 500
        assert x == 50 and y == 100


# ---------------------------------------------------------------------------
# 9. Controller thumbnail generation + active gating
# ---------------------------------------------------------------------------

class TestControllerThumbnail:
    def test_on_preview_ready_emits_thumbnail_within_max_side(self, qapp, controller):
        received = {}
        controller.thumbnail_ready.connect(
            lambda thumb, w, h: received.update(thumb=thumb, w=w, h=h)
        )

        # 1440 = 2 * THUMBNAIL_MAX_PX(720) so the reduce() factor divides
        frame = _solid_frame(1440)
        controller.on_preview_ready(frame)

        assert _pump_until(qapp, lambda: bool(received))
        assert received['w'] == 1440 and received['h'] == 1440
        assert max(received['thumb'].size) <= 720

    def test_thumbnail_honours_the_cap_on_the_real_sensor_size(self, qapp, controller):
        # 3552 / 720 is not an integer: a floor factor (4) gave 888 px, above
        # the documented cap; the ceiling factor (5) gives 710 px.
        received = {}
        controller.thumbnail_ready.connect(
            lambda thumb, w, h: received.update(thumb=thumb, w=w, h=h)
        )

        controller.on_preview_ready(_solid_frame(3552))

        assert _pump_until(qapp, lambda: bool(received))
        assert max(received['thumb'].size) <= 720
        assert received['thumb'].size == (711, 711)   # PIL reduce() rounds up the remainder

    def test_native_size_is_used_as_the_emitted_reference_dims(self, qapp, controller):
        # hist_data['native_size'] is the pre-resize sensor size; the thumbnail
        # is built from the (possibly already-resized) image handed in, but the
        # reference dims the editor keeps the box in must be the native ones.
        received = {}
        controller.thumbnail_ready.connect(
            lambda thumb, w, h: received.update(thumb=thumb, w=w, h=h)
        )

        frame = _solid_frame(512)  # e.g. a resized preview frame
        controller.on_preview_ready(frame, {'native_size': (1024, 1024)})

        assert _pump_until(qapp, lambda: bool(received))
        assert (received['w'], received['h']) == (1024, 1024)
        assert max(received['thumb'].size) <= 720

    def test_junk_native_size_falls_back_to_the_image_size(self, qapp, controller):
        received = {}
        controller.thumbnail_ready.connect(
            lambda thumb, w, h: received.update(thumb=thumb, w=w, h=h)
        )

        frame = _solid_frame(256)
        controller.on_preview_ready(frame, {'native_size': 'nonsense'})

        assert _pump_until(qapp, lambda: bool(received))
        assert (received['w'], received['h']) == (256, 256)

    def test_second_frame_suppressed_while_inactive_then_resumes(self, qapp, controller):
        received = []
        controller.thumbnail_ready.connect(lambda thumb, w, h: received.append((w, h)))

        # First call always builds a thumbnail (so a freshly opened page has
        # something to show), even with set_active(False) from the start.
        controller.set_active(False)
        controller.on_preview_ready(_solid_frame(256))
        assert _pump_until(qapp, lambda: len(received) == 1)

        # Second frame while inactive: suppressed.
        controller.on_preview_ready(_solid_frame(300))
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()
        assert len(received) == 1

        # Reactivate: next frame produces a thumbnail again.
        controller.set_active(True)
        controller.on_preview_ready(_solid_frame(300))
        assert _pump_until(qapp, lambda: len(received) == 2)
        assert received[1] == (300, 300)


# ---------------------------------------------------------------------------
# 10. Controller fit-to-sky
# ---------------------------------------------------------------------------

class TestControllerFitToSky:
    def test_fit_to_sky_with_no_frame_fails(self, qapp, controller):
        failures = []
        controller.fit_failed.connect(lambda msg: failures.append(msg))

        controller.fit_to_sky()
        qapp.processEvents()

        assert len(failures) == 1
        assert "No frame yet" in failures[0]

    def test_fit_to_sky_finds_disc_and_apply_box_saves(self, qapp, controller):
        cv2 = pytest.importorskip("cv2")
        del cv2  # only used to confirm the optional dependency is present

        size = 1024
        img, cx, cy, radius = _disc_frame(size=size, cx=560, cy=430, radius=380)

        thumb_received = {}
        controller.thumbnail_ready.connect(
            lambda thumb, w, h: thumb_received.update(thumb=thumb, w=w, h=h)
        )
        controller.on_preview_ready(img)
        assert _pump_until(qapp, lambda: bool(thumb_received))

        fit_results = []
        fail_results = []
        controller.fit_ready.connect(lambda x, y, w, h: fit_results.append((x, y, w, h)))
        controller.fit_failed.connect(lambda msg: fail_results.append(msg))

        controller.fit_to_sky()
        assert _pump_until(qapp, lambda: bool(fit_results) or bool(fail_results), deadline=10.0)

        assert fail_results == []
        assert len(fit_results) == 1
        x, y, w, h = fit_results[0]
        assert w == h   # square_around_circle always returns a square

        # bbox check with tolerance: the fitted square must contain the disc.
        tol = 40
        assert x <= (cx - radius) + tol
        assert y <= (cy - radius) + tol
        assert x + w >= (cx + radius) - tol
        assert y + h >= (cy + radius) - tol

        mw = FakeMainWindow()
        widget = OutputCropCard(mw)
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(256), size, size)
        widget.apply_box(x, y, w, h)

        assert widget.enable_switch.is_checked() is True
        assert widget.editor.box() == (x, y, w, h)
        saved = mw.config.get('output_crop')
        assert saved['enabled'] is True
        assert (saved['x'], saved['y'], saved['width'], saved['height']) == (x, y, w, h)
        _close_widget(qapp, widget)


# ---------------------------------------------------------------------------
# 11. show_fit_error
# ---------------------------------------------------------------------------

class TestShowFitError:
    def test_re_enables_button_and_shows_message(self, card):
        widget, mw = card
        widget.fit_btn.setEnabled(False)
        widget.fit_btn.setText("Measuring…")

        widget.show_fit_error("Could not find the sky circle in the last frame.")

        assert widget.fit_btn.isEnabled() is True
        assert widget.fit_btn.text() == "Fit to sky"
        assert widget.status_label.text() == "Could not find the sky circle in the last frame."


# ---------------------------------------------------------------------------
# Integration sanity: resolve_crop_box agrees with what the card saved.
# ---------------------------------------------------------------------------

class TestResolveAgreesWithCard:
    def test_saved_crop_resolves_on_same_size_frame(self, card, qapp):
        widget, mw = card
        widget.load_from_config(mw.config)
        widget.set_frame(_solid_frame(512), 3552, 3552)
        widget.enable_switch.set_checked(True)
        qapp.processEvents()

        saved = mw.config.get('output_crop')
        resolved = resolve_crop_box(saved, 3552, 3552)

        assert resolved is not None
        assert (resolved.x, resolved.y, resolved.width, resolved.height) == (
            saved['x'], saved['y'], saved['width'], saved['height'],
        )
