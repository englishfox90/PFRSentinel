"""
What happens to an automatic calibration model once it is discredited.

incumbent_chance judges the model on disk against the live buffer and, after
two consecutive chance-level runs, says it is not describing tonight's sky.
Until now that verdict changed only the badge and the caution: the overlay
kept drawing the model's labels (2026-09-28: a wrong-basin single-image fit
put Polaris at the south edge of the frame for 17 hours), every refinement
was still seeded from it, and nothing told the user. This module owns the
three consequences of the verdict, as one *episode* that begins when the
streak discredits the model and ends when a run clears it or another model
takes its place:

  * the overlay is withheld — the renderer treats the model as absent;
  * CalibrationService runs a seedless bootstrap instead of seeded
    refinement (the escape path, without the anchor-health veto: the chance
    verdict is a direct measurement over the whole buffer and, like the
    'misaligned' caution, outranks a healthy anchor reading on three frames);
  * one notification goes out, at the start of the episode only.

The calibration file is never rewritten by any of this: the model stays on
disk, and a bootstrap that passes admission replaces it through
model_replacement (rule 3) like any other. The guided solve itself is never
discredited (model_admission.is_user_anchored), so it never has an episode.

The withhold flag is a module-level store, like obstruction_map's and
label_stability's: the service flips it on the GUI thread, the renderer reads
it on the image-processor thread, and a single attribute write is atomic.
"""
from typing import Optional, Tuple

from .model_admission import is_user_anchored

_withheld = {'active': False, 'reason': ''}


def withhold_overlay(reason: str) -> None:
    _withheld['reason'] = reason
    _withheld['active'] = True


def release_overlay() -> None:
    _withheld['active'] = False
    _withheld['reason'] = ''


def overlay_withheld() -> bool:
    """True while the renderer must not draw the saved model."""
    return bool(_withheld['active'])


def withhold_reason() -> str:
    return str(_withheld['reason'])


class DiscreditEpisode:
    """One stretch during which the model on disk is discredited.

    `update` reconciles the episode with the streak's current verdict and
    reports transitions only: True when an episode begins (the owner
    notifies), False when it ends, None when nothing changed. Idempotent, so
    the owner can call it after every event that may have changed the
    verdict or the model.
    """

    def __init__(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def update(self, discredited: bool, model) -> Optional[bool]:
        if model is None or is_user_anchored(model):
            discredited = False
        if discredited and not self._active:
            self._active = True
            withhold_overlay("the saved calibration matched the stars in "
                             "recent frames no better than chance")
            return True
        if not discredited and self._active:
            self._active = False
            release_overlay()
            return False
        return None

    def end(self) -> Optional[bool]:
        return self.update(False, None)


def notification_text(model, note: str) -> Tuple[str, str]:
    """(title, body) for the calibration_discredited event."""
    n = int(getattr(model, 'n_matches', 0) or 0)
    rms = float(getattr(model, 'rms_residual', 0.0) or 0.0)
    measured = f" {note}." if note else ""
    body = (
        f"The saved all-sky calibration ({n} stars, RMS {rms:.1f} px) matched "
        "the stars in recent frames no better than chance would, in two "
        f"automatic runs in a row.{measured} The overlay is not drawn until a "
        "run clears it or a fresh automatic calibration replaces it; the "
        "calibration file is unchanged. Cloud causes this too. If the sky is "
        "clear, run Guided Calibration.")
    return "All-Sky Calibration Needs Attention", body
