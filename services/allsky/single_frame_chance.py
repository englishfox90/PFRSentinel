"""
Chance judgement for a fit made from ONE frame.

The joint fit records the tolerance it was judged at and its match count as a
multiple of what a wrong model gets for free (`chance_matches`), and refuses
a fit that is no better than chance. The single-frame paths — `calibrate()`'s
grid search and the triangle-hash fallback — recorded neither, so on the
reference rig on 2026-09-28 a 10-match fit at RMS 5.8 px on a 7.9 px final
tolerance (RMS = tol/√2, the signature of uniform scatter inside the
tolerance) landed in the empty calibration slot as "preliminary" and drew
Polaris at the south edge of the frame for seventeen hours. The incumbent
re-judging then discredited it on the next run, but by design that never
touches the file or the overlay: the slot-filling path is the one place a
chance fit must be stopped, because nothing sits behind it.

This module builds the one-frame pool the chance estimator expects, stamps
`chance_ratio` on the model beside the `final_tol_px` that `_iterative_fit`
records, and asks `calibration_fit_merit.fit_is_credible` — the same two
tests the joint fit and the badge use. The guided solve never comes here:
`guided_calibration` has its own anchor-based limit and `is_user_anchored`
exempts it wherever the merit rule runs.

Pure: no I/O, no Qt.
"""
from typing import Optional, Sequence, Tuple

from .calibration_fit_merit import fit_is_credible
from .chance_matches import ChanceEstimate, estimate_chance


def single_frame(detected: Sequence, above_horizon: Sequence,
                 sky_cx: Optional[float], sky_cy: Optional[float],
                 sky_r: Optional[float],
                 image_width: int = 0, image_height: int = 0) -> dict:
    """The frame dict `chance_matches.frame_pool` reads, for one image.

    `sky_*` are attached only when the sky circle was measured — a fallback
    circle would describe a search area the detections were not confined to.
    Without a circle the estimator falls back to the image bounds, and with
    neither it reports no geometry, which the judge treats as "not judged".
    """
    frame = {'detected': list(detected), 'above_horizon': list(above_horizon),
             'image_width': int(image_width or 0),
             'image_height': int(image_height or 0)}
    if sky_r and sky_cx is not None and sky_cy is not None:
        frame.update(sky_cx=float(sky_cx), sky_cy=float(sky_cy),
                     sky_r=float(sky_r))
    return frame


def judge_single_frame_fit(model, frame: dict
                           ) -> Tuple[bool, str, Optional[ChanceEstimate]]:
    """Stamp `model.chance_ratio` from `frame` and say whether the fit stands.

    Returns (ok, message, estimate). `ok` is False only on recorded evidence
    — an RMS above the credible fraction of the final tolerance, or a match
    count under the chance margin — exactly as `fit_is_credible` judges a
    saved model. Fails open (ok, reason, None) when the fit carries no final
    tolerance (an older caller) or the frame has no geometry to estimate
    against; the ratio is then left at 0.0 = unknown rather than invented.
    """
    tol = float(getattr(model, 'final_tol_px', 0.0) or 0.0)
    if tol <= 0.0:
        return True, "final match tolerance not recorded — chance not judged", None
    est = estimate_chance([frame], model, tol)
    if est.n_frames == 0 or est.expected <= 0.0:
        model.chance_ratio = 0.0
        return True, "no frame geometry for a chance estimate — not judged", est
    # Same convention as the joint fit (multi_calibrate): a pool so thin that
    # chance expects under one match is floored at one, so the ratio reads as
    # the match count itself rather than blowing up.
    model.chance_ratio = float(model.n_matches) / max(est.expected, 1.0)
    model.chance_expected = est.expected   # transient, as the joint fit sets it
    credible, reason = fit_is_credible(model)
    described = est.describe(int(model.n_matches))
    if credible:
        return True, f"{described}; {reason}", est
    return False, f"{reason} ({described})", est
