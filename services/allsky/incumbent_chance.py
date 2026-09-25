"""
Re-judge the model on disk against the live detection buffer.

Every automatic refinement is judged against chance (chance_matches) before
it can be saved. The model already on disk never was: it kept the rating it
earned when it was saved, and the only later check on it was the
bright-anchor gate (incumbent_evidence), which reports None on the moonlit,
obstructed buffers a hosting-site rig produces most nights. Issue #93's log
shows the result — 62 consecutive refinements rejected at 1.0-1.5x chance
while the incumbent, with the same arithmetic, sat unscored under a green
badge.

score_incumbent runs the incumbent through the same matcher the candidate
uses, on the same frames, at the joint fit's final tolerance, and compares
the count with the chance expectation. IncumbentChanceStreak turns those
scores into a verdict the service can act on: two consecutive chance-level
runs discredit the model (the "two strikes" of the independent all-sky
solver's nightly self-check, ALLSKY_HOSTING_SITE_PLAN section 6), one
credible run clears it, and a buffer too thin to judge by (cloud, a
moon-washed sky) is skipped rather than counted either way. The guided
solve itself is exempt throughout (model_admission.is_user_anchored): a solve
over a handful of user-named anchors is not expected to meet the joint fit's
final tolerance across the whole sky, and its basin outranks every automatic
measurement (.claude/rules/allsky.md). A joint fit that inherited the guided
stamp is not — it is exactly the fit this module exists to judge.

The verdict changes what the UI shows and what model_replacement may do; it
never rewrites the calibration file. The file's rating is "from when it was
saved" and the badge already says so.

Pure apart from logging: no Qt, no I/O.
"""
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from services.logger import app_logger as log

from .calibration_quality import CalibrationQuality
from .calibration_validate import (
    median_frame_resolution, model_in_frame, tol_scale)
from .chance_matches import CHANCE_MARGIN, estimate_chance
from .model_admission import is_user_anchored
from .multi_calibrate import _build_all_matches, _joint_rms

# The joint fit's final re-match tolerance at reference resolution
# (multi_calibrate._joint_iterative_fit: tol_scale * max(18, 50 - 5 * i)).
# The incumbent is judged at the same tolerance a candidate's final match
# count is, so its ratio is comparable with the gate every candidate passed.
SCORE_TOL_REF_PX = 18.0

# Cloudy guard: below this median detection count per frame the buffer says
# nothing about the model, so the run is skipped rather than scored. A clear
# night on either #93 rig detects the 200-star cap per frame; the reporter's
# Sep 23 buffer, judged unusable by every other gate, still carried enough
# stars to fit. Cloud and moon glare thin the pool toward the 5-star floor
# _detect_frame accepts, where chance expects a fraction of a match per frame
# and one coincidence flips the ratio either way. 40 keeps the expectation
# in whole matches over a buffer (the matcher's 5-per-detection pool at the
# #93 rig's 15.5 px scaled tolerance on a 1345 px disc gives ~1 chance match
# per frame, ~60 over a full 60-frame buffer) without waiting for a perfect
# sky.
SCORE_MIN_DETECTIONS = 40

# Consecutive chance-level scores that discredit the incumbent. One is a
# passing cloud or a buffer straddling a day gap; two runs are at least a
# refinement cooldown apart on different frames. Cleared by a single credible
# score — the same asymmetry as the escape trigger, where one completed run
# resets the failure count.
CHANCE_STRIKES = 2


@dataclass(frozen=True)
class IncumbentScore:
    """One run's verdict on the model on disk."""

    n_matches: int
    expected: float     # chance expectation over the same frames
    ratio: float        # n_matches / max(expected, 1)
    rms: float          # median residual of those matches (px)
    tol_px: float       # tolerance both were taken at
    n_frames: int       # frames that contributed to `expected`

    @property
    def chance_level(self) -> bool:
        return self.ratio < CHANCE_MARGIN

    def describe(self) -> str:
        return (f"{self.n_matches} matches vs {self.expected:.0f} expected by "
                f"chance at tol={self.tol_px:.1f}px over {self.n_frames} "
                f"frame(s) ({self.ratio:.2f}x chance), RMS={self.rms:.2f}px")


def score_tolerance_px(sky_r: Optional[float]) -> float:
    """The tolerance to score at for a buffer of median sky radius `sky_r`."""
    return SCORE_TOL_REF_PX * tol_scale(sky_r)


def score_incumbent(model, frames: List[dict], tol_px: float
                    ) -> Optional[IncumbentScore]:
    """Match `model` against `frames` at `tol_px` and compare with chance.

    None when there is nothing to judge: no valid model, no frames, a buffer
    under the cloudy guard (SCORE_MIN_DETECTIONS), or a matcher failure. None
    is not a strike. The model is rescaled into the buffer's resolution first
    (model_in_frame), as incumbent_anchor_health does — the buffer holds
    preview-resolution frames while a manual calibration is at raw
    resolution.
    """
    if model is None or not frames or not model.is_valid():
        return None
    counts = [len(f.get('detected') or []) for f in frames]
    median_det = float(np.median(counts))
    if median_det < SCORE_MIN_DETECTIONS:
        log.debug(f"Incumbent not scored: median {median_det:.0f} detections "
                  f"per frame is under the {SCORE_MIN_DETECTIONS} floor "
                  "(cloud or glare — nothing to judge by)")
        return None
    try:
        width, height = median_frame_resolution(frames)
        proj = model_in_frame(model, width, height)
        matches = _build_all_matches(frames, proj, tol_px=tol_px,
                                     min_per_image=0, _log=False)
        n_matches = sum(len(m) for m in matches)
        est = estimate_chance(frames, proj, tol_px)
        rms = _joint_rms(matches, proj)
    except Exception as e:
        log.debug(f"Incumbent scoring failed (non-fatal): {e}")
        return None
    score = IncumbentScore(
        n_matches=n_matches, expected=float(est.expected),
        ratio=n_matches / max(float(est.expected), 1.0),
        rms=float(rms), tol_px=float(tol_px), n_frames=est.n_frames)
    log.info(f"Incumbent scored against the live buffer: {score.describe()}")
    return score


class IncumbentChanceStreak:
    """Consecutive chance-level scores on the model on disk.

    `record` returns True when the verdict changed, so the owner emits on
    transitions only. `reset` is for a new model: strikes belong to the model
    they were scored against.
    """

    def __init__(self) -> None:
        self._strikes = 0
        self._discredited = False
        self._last: Optional[IncumbentScore] = None

    @property
    def discredited(self) -> bool:
        """CHANCE_STRIKES consecutive chance-level scores, not yet cleared."""
        return self._discredited

    @property
    def strikes(self) -> int:
        return self._strikes

    def record(self, score: Optional[IncumbentScore], model=None) -> bool:
        """`model` is the incumbent the score describes; the guided solve
        itself is never discredited by chance (second guard behind
        _RefineWorker's skip — its authority is the user's, not the joint
        fit's). A joint fit descended from it is judged like any other."""
        if score is None or (model is not None and is_user_anchored(model)):
            return False
        self._last = score
        before = self._discredited
        if score.chance_level:
            self._strikes += 1
            self._discredited = self._strikes >= CHANCE_STRIKES
        else:
            self._strikes = 0
            self._discredited = False
        if self._discredited and not before:
            log.warning(
                f"The saved calibration matched at chance level in "
                f"{self._strikes} consecutive runs ({score.describe()}) — "
                "shown as preliminary until a run clears chance; the file is "
                "unchanged. Run Guided Calibration if the overlay is off.")
        elif before and not self._discredited:
            log.info("The saved calibration cleared chance again "
                     f"({score.describe()}) — rating restored")
        return before != self._discredited

    def reset(self) -> None:
        self._strikes = 0
        self._discredited = False
        self._last = None

    def note(self, file_quality: str) -> str:
        """The badge tooltip's reason while discredited: the measurement that
        capped it, and the rating the file still carries. '' otherwise —
        this is the live verdict, not calibration_fit_merit's reading of the
        file's own fields (which are unknown on a legacy file)."""
        if not self._discredited or self._last is None:
            return ''
        last = self._last
        return (f"Matched {last.n_matches} stars vs {last.expected:.0f} "
                f"expected by chance at {last.tol_px:.1f} px in "
                f"{self._strikes} consecutive runs (rating from when it was "
                f"saved: {file_quality})")

    def cap(self, quality: str) -> str:
        """`quality` as the UI should show it: capped at PRELIMINARY while
        discredited. The file's own rating is left alone."""
        if (self._discredited and CalibrationQuality.rank(quality)
                > CalibrationQuality.rank(CalibrationQuality.PRELIMINARY)):
            return CalibrationQuality.PRELIMINARY
        return quality


def calibrated_status(model, quality: str, chance_level: bool) -> str:
    """The status line for a model that is staying in place."""
    label = ("preliminary — matches at chance level" if chance_level
             else quality)
    return (f"Calibrated: {model.n_matches} stars, "
            f"RMS={model.rms_residual:.1f}px ({label})")
