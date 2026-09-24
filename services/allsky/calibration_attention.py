"""
Does the calibration badge still deserve to be believed?

The quality badge is computed from the model's own fit statistics at the
moment it was saved, and nothing ever revisited it: issue #79 shows a green
"Good" sitting over a log of back-to-back refinement rejections, on a rig
whose overlay was wrong enough that the user was in the middle of a guided
calibration. The rating was never a claim about tonight's sky, but a green
badge reads as one.

This module turns what the service already knows into a caution shown beside
the badge. It never changes the rating and never touches the model — the
replacement rules (model_replacement, incumbent_evidence) are unaffected.

Both inputs are needed before anything is shown:

  * consecutive refinement rejections — on their own they mean little (the
    2026-09-05 incumbent drew a correct overlay through 26 of them);
  * the saved model's bright-anchor health on the recent frames — on its own
    it is never consulted, because a passing cloud fails it.

A model that still hits its bright anchors is healthy whatever the
refinements are doing, so that combination stays silent.
"""
from typing import List, Optional, Tuple

from .incumbent_evidence import RECENT_FRAMES, incumbent_anchor_health

# Same count that makes the service suspect its seed (BASIN_ESCAPE_FAILURES).
ATTENTION_MIN_FAILURES = 3

LEVEL_NONE = ''
LEVEL_UNCONFIRMED = 'unconfirmed'
LEVEL_MISALIGNED = 'misaligned'

_ADVICE = (
    "Cloud causes this too. On a clear night, look at the overlay: if the "
    "constellation lines sit on their stars, nothing is needed. If they "
    "don't, run Guided Calibration.")


def calibration_attention(
    model,
    consecutive_failures: int,
    frames: List[dict],
    escape_paused: bool = False,
) -> Tuple[str, str]:
    """Return (level, message); ('', '') when the badge can stand as it is."""
    if model is None or consecutive_failures < ATTENTION_MIN_FAILURES:
        return LEVEL_NONE, ''
    health: Optional[bool] = incumbent_anchor_health(model, frames)
    if health is True:
        return LEVEL_NONE, ''

    paused = (" Automatic re-calibration has paused itself for now."
              if escape_paused else "")
    if health is False:
        return LEVEL_MISALIGNED, (
            f"The saved calibration missed the bright stars in the last "
            f"{RECENT_FRAMES} frames, and the last {consecutive_failures} "
            f"automatic refinements were rejected.{paused} The rating shown "
            "describes the fit when it was saved, not how it lines up now. "
            + _ADVICE)
    return LEVEL_UNCONFIRMED, (
        f"The last {consecutive_failures} automatic refinements were "
        f"rejected, and recent frames couldn't confirm the saved calibration "
        f"either way.{paused} The saved calibration is unchanged. " + _ADVICE)
