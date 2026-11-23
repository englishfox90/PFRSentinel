"""
Arcseconds per pixel at the optical centre, from the all-sky calibration.

The fisheye model's radial term is ``r = a1·θ + a3·θ³ + a5·θ⁵``, so near
the centre one pixel spans ``1 / a1`` radians. ``a1`` is in the pixels of
the frame the model was fitted on (``image_width`` x ``image_height``), so
it is scaled to the frame the FWHM was measured on — the full sensor frame,
while the calibration usually runs on the resized one. With no calibration,
an invalid one, or one whose frame size is unknown or of another shape (a
crop, which no single factor relates), there is no plate scale: the feed
reports the FWHM in pixels only, never a guessed arcsecond figure.

The figure is a zenith plate scale; towards the horizon the fisheye packs
more sky into a pixel, so an off-centre star's arcsecond FWHM is understated.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

ARCSEC_PER_RADIAN = 206264.806
_ASPECT_TOLERANCE = 0.02


def _default_path() -> str:
    from services.app_config import get_calibration_path
    return get_calibration_path()


def _default_loader(path):
    from services.allsky.fisheye import FisheyeModel
    return FisheyeModel.try_load(path)


class PlateScale:
    """Cached by the calibration file's path and mtime; re-read on change."""

    def __init__(self, path_fn: Callable[[], str] = _default_path,
                 loader: Callable[[str], object] = _default_loader):
        self._path_fn = path_fn
        self._loader = loader
        self._lock = threading.Lock()
        self._key = None
        self._model = None

    def arcsec_per_px(self, frame_width, frame_height) -> Optional[float]:
        a1 = self.lens_a1(frame_width, frame_height)
        return None if a1 is None else ARCSEC_PER_RADIAN / a1

    def lens_a1(self, frame_width, frame_height) -> Optional[float]:
        """The calibration's ``a1`` (px/rad) in this frame's pixels, or None."""
        model = self._current_model()
        if model is None:
            return None
        try:
            a1 = float(model.a1)
            m_w, m_h = int(model.image_width or 0), int(model.image_height or 0)
            w, h = int(frame_width or 0), int(frame_height or 0)
        except (TypeError, ValueError, AttributeError):
            return None
        if a1 <= 0 or m_w <= 0 or m_h <= 0 or w <= 0 or h <= 0:
            return None
        if abs(m_w / m_h - w / h) / max(m_w / m_h, w / h) > _ASPECT_TOLERANCE:
            return None
        return a1 * w / m_w

    def _current_model(self):
        try:
            path = self._path_fn()
            mtime = os.path.getmtime(path) if os.path.isfile(path) else None
        except Exception:
            return None
        with self._lock:
            if self._key == (path, mtime):
                return self._model
        model = None
        if mtime is not None:
            model = self._loader(path)
            try:
                if model is not None and not model.is_valid():
                    model = None
            except Exception:
                model = None
        with self._lock:
            self._key, self._model = (path, mtime), model
        return model
