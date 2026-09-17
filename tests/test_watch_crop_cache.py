"""
ui/main_window/capture.py `_on_watch_image_processed` + ui/main_window/output.py
`_on_image_processed` (issue #12 review fix).

A code review found watch mode caching the CROPPED frame for "Calibrate Now"
(and re-cropping it on every reprocess). The fix threads the pre-crop
`clean_frame` from process_image's `extras` through to the output-crop editor
and the raw-frame cache. This pins that the cache stays full-frame even when
the dispatched output is cropped.

Follows the MagicMock + types.MethodType bind pattern used throughout
tests/test_output_dispatch.py and tests/test_frame_builder.py — the real
mixin methods run against a lightweight host, no Qt MainWindow required.
"""
import types

from unittest.mock import MagicMock

from PIL import Image

from ui.main_window.capture import _MainWindowCaptureMixin
from ui.main_window.output import _MainWindowOutputMixin


def _bind(win, cls, name):
    setattr(win, name, types.MethodType(getattr(cls, name), win))


def _window():
    win = MagicMock()
    win.config = {'capture_mode': 'watch'}
    win.is_capturing = False
    win.image_library = None
    win._last_clean_full_frame = None
    _bind(win, _MainWindowCaptureMixin, '_on_watch_image_processed')
    _bind(win, _MainWindowOutputMixin, '_on_image_processed')
    return win


def test_calibrate_now_cache_stays_full_frame_when_output_is_cropped():
    win = _window()
    clean = Image.new('RGB', (640, 480), (5, 5, 5))
    cropped_out = Image.new('RGB', (200, 200), (9, 9, 9))
    metadata = {'FILENAME': 'f.png'}
    extras = {'clean_frame': clean, 'native_size': (640, 480), 'metadata': metadata}

    win._on_watch_image_processed(cropped_out, cropped_out, '/out/f.png', extras)

    # The raw-frame cache used by "Calibrate Now" must be the FULL frame, not
    # the cropped output — a wrong-sized cache is exactly the bug found in
    # review (a cropped frame cached, then re-cropped on every reprocess).
    assert win._cached_raw_image.size == clean.size
    assert win._cached_raw_image is not clean  # cached as a copy
    assert win._cached_raw_metadata is metadata
    # The transient handoff slot is cleared after being consumed once.
    assert win._last_clean_full_frame is None
    win.output_crop_controller.on_preview_ready.assert_called_once_with(
        clean, {'native_size': (640, 480)})


def test_no_crash_and_no_controller_call_when_extras_carry_no_clean_frame():
    win = _window()
    out = Image.new('RGB', (640, 480))
    extras = {'metadata': {'FILENAME': 'f.png'}}

    win._on_watch_image_processed(out, out, '/out/f.png', extras)

    win.output_crop_controller.on_preview_ready.assert_not_called()
    # Falls back to the (uncropped, in this case) output image.
    assert win._cached_raw_image.size == out.size
