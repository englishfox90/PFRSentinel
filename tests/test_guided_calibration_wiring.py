"""
Tests for the main window's guided-calibration wiring
(_MainWindowSettingsMixin._open_guided_calibration).

The dialog holds a full-resolution pixmap and the session holds two
full-resolution frames. Both must be gone once the dialog closes: on a 24/7
process, anything that survives a close is a leak per click of the button.
"""
import gc
import weakref

import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from ui.controllers.guided_calibration_session import GuidedCalibrationSession
from ui.main_window.settings import _MainWindowSettingsMixin
from ui.panels.allsky_guided_dialog import GuidedCalibrationDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Controller:
    def __init__(self, prep):
        self._prep = prep
        self.committed = []

    def prepare_guided_calibration(self):
        prep, self._prep = self._prep, None     # hand over, keep no reference
        return prep

    def begin_guided_session(self, prep):
        return GuidedCalibrationSession(prep, commit=self._commit)

    def _commit(self, model):
        self.committed.append(model)
        return True, ""


class _Window(_MainWindowSettingsMixin, QWidget):
    def __init__(self, controller):
        super().__init__()
        self.allsky_controller = controller
        self.notes = []

    def _notify(self, message, category='info'):
        self.notes.append(message)


def _prep():
    image = Image.new('RGB', (1200, 1200), (3, 3, 3))
    return {'image': image, 'display_image': image, 'detections': [],
            'candidates': [], 'sky_cx': 600.0, 'sky_cy': 600.0, 'sky_r': 550.0,
            'lat': 31.0, 'lon': -100.0, 'dt': None,
            'image_width': 1200, 'image_height': 1200}


def _open_dialog():
    for w in QApplication.topLevelWidgets():
        if isinstance(w, GuidedCalibrationDialog) and w.isVisible():
            return w
    return None


def _flush(qapp):
    for _ in range(3):
        gc.collect()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


@pytest.fixture
def window(qapp):
    prep = _prep()
    image_ref = weakref.ref(prep['image'])
    win = _Window(_Controller(prep))
    del prep
    yield win, image_ref
    win.close()
    win.deleteLater()
    _flush(qapp)


def test_cancelled_dialog_releases_the_frame_and_itself(window, qapp):
    win, image_ref = window
    seen = []

    def cancel():
        dlg = _open_dialog()
        seen.append(weakref.ref(dlg))
        dlg.reject()

    QTimer.singleShot(0, cancel)
    win._open_guided_calibration()
    _flush(qapp)

    assert seen, "the dialog never opened"
    assert image_ref() is None, "prep frames survived the dialog"
    assert not [w for w in win.findChildren(GuidedCalibrationDialog)]
    assert win.notes == []


def test_saved_dialog_notifies_and_releases_everything(window, qapp):
    win, image_ref = window

    def save():
        dlg = _open_dialog()
        # As if a solve had passed and the user is looking at the review.
        dlg._state = 'review'
        dlg._on_primary()

    # The session's held model is what save() commits; give it one.
    original = win.allsky_controller.begin_guided_session

    def begin(prep):
        s = original(prep)
        s._pending_model = object()
        return s

    win.allsky_controller.begin_guided_session = begin
    QTimer.singleShot(0, save)
    win._open_guided_calibration()
    _flush(qapp)

    assert len(win.allsky_controller.committed) == 1
    assert win.notes and 'saved' in win.notes[0]
    assert image_ref() is None
    assert not [w for w in win.findChildren(GuidedCalibrationDialog)]
