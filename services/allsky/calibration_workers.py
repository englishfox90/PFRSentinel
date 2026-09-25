"""
Background QThread workers for CalibrationService.

Extracted from calibration_service.py (which re-exports both classes and
MAX_RESIDUAL_PX for existing callers).

Ownership contract — both classes are created UNPARENTED and retired by the
service on QThread.finished:

  * Neither exposes a signal named ``finished``. A same-named Signal on a
    QThread subclass shadows QThread.finished in Python, and QThread.finished
    is the only hook that tells the owner a run is over. Shadowing it is what
    made every worker leak. Results arrive on ``result_ready`` / ``failed``.
  * ``release()`` drops the payload the worker pinned for the duration of the
    run (a frame buffer, a full-resolution image copy).
"""
from PySide6.QtCore import Signal, QThread

from services.logger import app_logger as log

from .calibration import calibrate, CalibrationError
from .calibration_validate import median_frame_resolution, model_in_frame
from .incumbent_chance import score_incumbent, score_tolerance_px
from .incumbent_evidence import corroborate_incumbent
from .model_admission import (
    admission_evidence, admit_candidate, east_left_hint, is_guided, is_user_anchored)
from .multi_calibrate import median_sky_r, refine_from_detections
from .pole_consensus import PoleHistory
from .pole_finder import find_pole
from .static_lights import clean_pools

MAX_RESIDUAL_PX = 20.0      # max accepted median residual (pixels)


def _rotation_seed(incumbent, frames):
    """The incumbent, in the frames' resolution, when it may seed the
    rotation-pole fit's radial function: only a guided model — a human-
    anchored basin whose scale is locked (pole-anchor plan P7: never a
    distrusted model; a 'pole'-stamped one may have been vouched for by
    the very light the fit is meant to see past, issue #93)."""
    if not is_guided(incumbent):
        return None
    w, h = median_frame_resolution(frames)
    return model_in_frame(incumbent, w, h)


class _RefineWorker(QThread):
    """Run multi-image joint calibration in a background thread."""

    # (FisheyeModel, n_images, span_min, admitted on evidence?) — the last
    # is model_admission.admission_evidence, which _on_refine_done needs to
    # decide whether a basin-escape result may bypass the RMS guard.
    result_ready = Signal(object, int, float, bool)
    failed = Signal(str)                   # error message
    # The incumbent was stamped pole-corroborated in place this run
    # (incumbent_evidence); the service persists the stamp.
    incumbent_corroborated = Signal(str)   # message
    # The incumbent's chance score on this run's frames (incumbent_chance
    # .IncumbentScore, or None when the buffer can't judge it). Its own
    # signal, not a field of result_ready: most runs on a rig that needs it
    # end in `failed` (#93: 62 of 62), and the score has to reach the
    # service on those too.
    incumbent_scored = Signal(object)

    def __init__(self, frames, seed_model, n_images: int, span_min: float,
                 lat: float = 0.0, incumbent=None, pole_history=None,
                 parent=None, ring=None, obstruction_map=None):
        super().__init__(parent)
        self._frames = frames
        self._seed = seed_model            # None = seedless (cold start / escape)
        self._incumbent = incumbent        # model the result would replace
        self._pole_history = pole_history or PoleHistory()
        self._n_images = n_images
        self._span_min = span_min
        self._lat = lat
        # Long-baseline frame ring (frame_ring) for the rotation-pole fit,
        # and the equipment map (obstruction_map.ObstructionMap, the
        # service's singleton; None only when the service has none).
        self._ring = ring
        self._obstruction_map = obstruction_map

    def release(self) -> None:
        """Drop the payload once the run is over."""
        self._frames = None
        self._seed = None
        self._incumbent = None
        self._pole_history = None
        self._ring = None
        self._obstruction_map = None

    def run(self):
        try:
            # Pool hygiene first (static_lights): pier lights and detections
            # on equipment leave both pools before anything measures them,
            # so the pole finder cannot adopt a light and the chance
            # expectation counts only sky.
            try:
                frames, ring, _lights = clean_pools(
                    self._frames, self._ring, self._obstruction_map)
            except Exception as e:
                log.warning(f"Calibration pool hygiene skipped (non-fatal): {e}")
                frames, ring = self._frames, list(self._ring or [])
            # Model-free ground truth from the same buffer: the measured
            # celestial pole, filtered through the cross-run consensus. None
            # is normal (short span, cloudy or hidden pole, contaminated
            # field) and simply skips the pole check. This is the ONLY place
            # a measurement is recorded — one entry per physical run.
            sky_r = median_sky_r(frames)
            pole = None
            try:
                pole = self._pole_history.record(
                    find_pole(frames, self._lat, ring=ring,
                              seed_model=_rotation_seed(self._incumbent, frames)),
                    sky_r)
            except Exception as e:
                log.debug(f"Pole estimation failed (non-fatal): {e}")
            # Runs without a trusted pole age the incumbent's 'pole' rung
            # (model_admission); read after record() so this run counts.
            drought = self._pole_history.runs_since_trusted
            pole_w, pole_h = median_frame_resolution(frames)

            # Let a trusted pole vouch for the model on disk before it is
            # asked to constrain this run's candidate — otherwise a correct
            # legacy model stays uncorroborated until some candidate is
            # admitted, and an escape can displace it on fit numbers alone.
            stamped, why = corroborate_incumbent(
                self._incumbent, self._lat, pole, sky_r,
                pole_image_width=pole_w, pole_image_height=pole_h)
            if stamped:
                self.incumbent_corroborated.emit(why)
            # The guided solve itself is never scored: a 7-anchor solve is
            # not expected to meet the joint fit's final tolerance across the
            # whole sky, its authority is the user's, and the anchor-health
            # caution already covers a moved camera. A joint fit descended
            # from it (same provenance stamp) is scored like any other.
            if not is_user_anchored(self._incumbent):
                self.incumbent_scored.emit(score_incumbent(
                    self._incumbent, frames, score_tolerance_px(sky_r)))

            model = refine_from_detections(
                frames,
                self._seed,
                max_residual_px=MAX_RESIDUAL_PX,
                east_left_hint=east_left_hint(self._incumbent, pole, drought),
                pole=pole, lat_deg=self._lat, ring=ring,
            )
            ok, msg = admit_candidate(
                model, self._incumbent, self._lat, pole, sky_r,
                pole_image_width=pole_w, pole_image_height=pole_h,
                runs_without_pole=drought)
            if not ok:
                raise CalibrationError(f"admission check failed: {msg}")
            log.info(f"Refinement admitted: {msg}")
            evidence = admission_evidence(self._incumbent, pole, drought)
            self.result_ready.emit(
                model, self._n_images, self._span_min, evidence)
        except Exception as e:
            self.failed.emit(str(e))


class _InitialCalWorker(QThread):
    """Run single-image calibration when no model exists yet."""

    result_ready = Signal(object)   # FisheyeModel
    failed = Signal(str)

    def __init__(self, image, lat, lon, dt, parent=None):
        super().__init__(parent)
        self._image = image
        self._lat = lat
        self._lon = lon
        self._dt = dt

    def release(self) -> None:
        """Drop the full-resolution image copy."""
        self._image = None

    def run(self):
        try:
            # No min_matches override: the automatic path must not accept a
            # thinner fit than Calibrate Now does. The old 6 let the grid
            # search proceed on 3 matches and the fit finish on 5 (issue #33).
            model = calibrate(
                self._image, self._lat, self._lon, dt=self._dt,
            )
            self.result_ready.emit(model)
        except Exception as e:
            self.failed.emit(str(e))
