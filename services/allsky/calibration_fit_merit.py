"""
Is a fit's own arithmetic worth believing?

model_quality rates a model from n_matches, n_images, span and RMS — the
numbers the fit reports about itself. On a high-resolution rig those numbers
are exactly what a WRONG model reports too: issue #93's saved model matched
606 stars over 60 frames at RMS 10.9 px and rated Good, and every one of
those matches was a coincidence. 606 is ~1.0x what chance_matches expects at
the fit's 16.5 px final tolerance, and 10.9 px is that tolerance / sqrt(2),
the median residual of a uniformly scattered chance partner. The fit was
describing its tolerance, not the sky.

Two numbers the fit already knows, now persisted on the model
(FisheyeModel.final_tol_px / chance_ratio), let this be told after the fact:

  * RMS as a fraction of the final match tolerance — chance sits at 0.707,
    real fits well under it;
  * the match count as a multiple of the chance expectation.

Either one failing makes the fit non-credible, and model_quality then caps it
at PRELIMINARY however many frames it spans. Unknown fields (0.0 — a file
written by an earlier version) never fail a model: a legacy file keeps its
rating until it is re-fitted, and a guided solve is exempt outright because
its residual is over a handful of anchors a person identified, not a match
process that chance can imitate.

Pure: no I/O, no Qt.
"""
from typing import Tuple

from .chance_matches import CHANCE_MARGIN, chance_median_residual
from .model_admission import is_guided

# RMS / final match tolerance above which a fit is describing its tolerance
# rather than the sky. Measured on every log to hand (ALLSKY_HOSTING_SITE_PLAN
# decision 5):
#
#   Fit                                                        RMS / final tol
#   Reference rig guided model, 7 anchors (13 px limit)                  0.32
#   Last genuine automatic fit, #10 rig, 2026-09-05 (n = 4561)           0.48
#   Chance-band fits, reference rig, Sep 17-20 2026 (35 fits)       0.64-0.83
#   Chance-band fits, reporter's rig (#93), Sep 23 2026 (62 fits)   0.65-0.72
#
# Chance itself is 1 / sqrt(2) = 0.707 (chance_matches.chance_median_residual).
# 0.55 sits midway between the last genuine fit and the first chance fit; a
# false "credible" is the expensive error (it lets a chance fit bypass the RMS
# guard in model_replacement), so the line leans toward the genuine side
# rather than hugging the chance band at 0.6.
CREDIBLE_RMS_FRACTION = 0.55

# Match count as a multiple of chance below which the count carries no
# orientation information — the same margin multi_calibrate's chance gate
# applies to a fresh fit (chance_matches.CHANCE_MARGIN), so a model that was
# admitted under that gate cannot fail this half; it is here for files whose
# ratio was recorded by a gate with a different margin, and for fits judged
# after the fact by incumbent_chance.
CHANCE_RATIO_FLOOR = CHANCE_MARGIN


def fit_is_credible(model) -> Tuple[bool, str]:
    """(credible?, reason) for the fit statistics `model` carries.

    False only on recorded evidence: an RMS above CREDIBLE_RMS_FRACTION of
    the recorded final tolerance, or a recorded chance ratio under
    CHANCE_RATIO_FLOOR. A model with neither recorded is credible by
    default, with a reason that says so; a guided model is credible outright.
    """
    if model is None:
        return False, "no model"
    if is_guided(model):
        return True, "guided solve — anchors identified by the user"
    tol = float(getattr(model, 'final_tol_px', 0.0) or 0.0)
    ratio = float(getattr(model, 'chance_ratio', 0.0) or 0.0)
    if tol <= 0.0 and ratio <= 0.0:
        return True, ("fit tolerance and chance ratio not recorded "
                      "(calibrated by an earlier version)")

    problems = []
    notes = []
    if tol > 0.0:
        fraction = float(model.rms_residual) / tol
        line = (f"RMS {model.rms_residual:.1f} px is {fraction:.2f} of the "
                f"{tol:.1f} px match tolerance")
        if fraction > CREDIBLE_RMS_FRACTION:
            problems.append(
                line + f" — chance scatter sits at "
                f"{chance_median_residual(1.0):.2f}, a real fit under "
                f"{CREDIBLE_RMS_FRACTION:.2f}")
        else:
            notes.append(line)
    if ratio > 0.0:
        line = f"{ratio:.2f}x the matches chance would supply"
        if ratio < CHANCE_RATIO_FLOOR:
            problems.append(line + f" (a real fit clears {CHANCE_RATIO_FLOOR:.1f}x)")
        else:
            notes.append(line)
    if problems:
        return False, "Matches no better than chance: " + "; ".join(problems)
    return True, "; ".join(notes)


def credibility_note(model) -> str:
    """The reason a model's rating is capped, or '' when it is not.

    What the quality badge shows in its tooltip: silence for a credible
    model, the measurement for a chance fit.
    """
    credible, reason = fit_is_credible(model)
    return '' if credible else reason
