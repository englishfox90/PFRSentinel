"""
Turn a captured frame into a calibration buffer entry.

Extracted from CalibrationService (which keeps `_detect_frame` as an alias
for existing callers). Two pieces, both run on the image-processor worker
thread that feeds the service, never on the GUI thread:

  * detect_calibration_frame — measure the sky circle, detect stars inside
    it, and attach the catalogue stars above the horizon at the frame's time
    and site (frame_catalog). The result is the frame dict every calibration consumer reads
    (refine_from_detections, find_pole, incumbent_evidence,
    incumbent_chance); only detections are stored, never the image.
  * SkippedFrameSummary — the accounting behind the "frames skipped" warning
    (F9): per-frame skips stay at DEBUG because a cloudy night produces one
    every capture, and a WARNING summary goes out at most once per interval
    so the user can still see that calibration is running but starving.
"""
from datetime import datetime
from typing import Optional

from services.logger import app_logger as log

from .frame_catalog import above_horizon_stars
from .star_centroid import detect_stars, measure_sky_circle


def detect_calibration_frame(image, dt: datetime, lat: float, lon: float
                             ) -> Optional[dict]:
    """Detect stars and compute catalog AltAz for one frame."""
    try:
        circle = measure_sky_circle(image)
        if circle is None:
            log.debug("CalibrationService: no measurable sky circle "
                      "(no illuminated disc) — skipping frame")
            return None
        sky_cx, sky_cy, sky_r = circle
        detected = detect_stars(
            image, max_stars=200,
            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r,
        )
        if len(detected) < 5:
            log.debug(f"CalibrationService: {len(detected)} stars — "
                      "too few, skipping frame")
            return None

        above_horizon = above_horizon_stars(dt, lat, lon)

        img_w = image.width if hasattr(image, 'width') else 0
        img_h = image.height if hasattr(image, 'height') else 0
        return {
            'dt': dt,
            'detected': detected,
            'above_horizon': above_horizon,
            'sky_cx': sky_cx,
            'sky_cy': sky_cy,
            'sky_r': sky_r,
            'image_width': img_w,
            'image_height': img_h,
        }
    except Exception as e:
        log.warning(f"CalibrationService frame detection failed: {e}")
        return None


class SkippedFrameSummary:
    """Count skipped frames and warn about them at most once per interval.

    The window opens silently on the first skip; the summary is emitted the
    first time a skip lands `interval_s` or more after the window opened,
    then the count and the window restart.
    """

    def __init__(self, interval_s: float) -> None:
        self._interval_s = float(interval_s)
        self.skipped = 0
        self._window_start = 0.0

    def note_skip(self, now: float) -> None:
        self.skipped += 1
        if self._window_start == 0.0:
            self._window_start = now
        elif now - self._window_start >= self._interval_s:
            log.warning(
                f"Calibration skipped {self.skipped} frame(s) in the "
                "last cycle (too few stars / detection failed) — sky may be "
                "cloudy or the lens obstructed."
            )
            self.skipped = 0
            self._window_start = now
