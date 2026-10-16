"""
The multi-image joint fit: pooled matching and the iterative least-squares
solve that multi_calibrate's public entry points drive.

One FisheyeModel is fitted to N frames at once; each frame differs only by
its observation time. Extracted from multi_calibrate.py (file-size cap);
multi_calibrate re-exports the underscored names for existing callers.

Dependencies: scipy (same as single-image calibration).
"""
import dataclasses
from typing import List, Optional, Tuple

import numpy as np

from services.logger import app_logger as log

# Pre-import scipy at module level so background threads never trigger
# a first-time import (causes segfault in PyInstaller builds).
try:
    from scipy.optimize import least_squares as _least_squares
except Exception as _e:
    _least_squares = None
    log.error(f"scipy.optimize import failed in joint_fit: {type(_e).__name__}: {_e}")

from .calibration import CalibrationError, _brightness_match, _params_to_model
from .calibration_validate import (
    A3_MAX, A3_MIN, REG_A3, REG_A5, median_frame_resolution, median_sky_r, tol_scale,
    validate_pole)
from .fisheye import FisheyeModel
from .joint_fit_diagnostics import collect_diagnostics as _collect_diagnostics
from .model_admission import is_guided
from .pole_tolerance import PoleConstraint

# Weight of the measured-pole pseudo-observation relative to the star
# residuals (issue #93, 5c). Like the ridge prior it is multiplied by
# sqrt(N residuals) so its strength tracks the data volume; the offset it
# penalises is in units of the pole's sigma (pole_tolerance.pole_sigma_px,
# which carries the model-error allowance and is ~110 px at full
# resolution on both real rigs, 0.10·r_p dominating), so at 1.0 a model
# 1σ from the pole costs as much as every match moving 1 px. At that
# sigma the term is a no-harm prior: a correct seed's RMS and pole are
# unchanged (synthetic presets; 0.6 px at the pole on the reference
# night, plan §0.7), a seed rolled 6° off ends no farther from the pole
# and no worse in RMS than the free fit, and a seed in a wrong basin is
# not rescued — the wrong matches resist, not the weight. A tighter sigma
# does pull (test_joint_fit's 1 px case), but forcing the reference
# night's fit onto the measured pole that way cost it 80 % of its
# matches and every anchor frame, which is why the sigma is what it is.
POLE_WEIGHT = 1.0
# Residual charged, per component, when the model projects the pole off the
# image altogether — the same "far" value the star terms use.
_POLE_OFF_IMAGE_RES = 30.0


# ---------------------------------------------------------------------------
# Radial-polynomial regularisation (ridge prior toward the seed)
# ---------------------------------------------------------------------------
#
# On obstructed installs (pier cameras with the telescope OTA/mount in frame)
# the matched stars cluster in the unblocked sky region, leaving the radial
# polynomial (a3, a5) under-constrained. With even a slightly stale seed the
# optimiser bends a3 toward its bound to absorb an *orientation* error — landing
# in a low-RMS false minimum where roll/axis are wrong and the bright anchors
# are 50-90 px off (observed in production as the recurring "a3=25.0 outside
# plausible range" refinement failure). A soft ridge penalty pulling a3/a5 back
# toward the seed values forces the orientation parameters to take the
# correction instead. The penalty is weighted by sqrt(N residuals) so its
# strength relative to the data is independent of how many stars matched: when
# coverage genuinely constrains the polynomial the data still dominates; when it
# doesn't, the physical prior wins instead of the bound.
# Ridge weights REG_A3 / REG_A5 are shared with the guided fit (calibration_validate).


def pole_constraint_for(pole, lat_deg: Optional[float], frames,
                        seed_model=None) -> Optional[PoleConstraint]:
    """The pseudo-observation a fit over `frames` should carry, or None.

    None without a trusted pole or a site latitude. A guided seed outranks
    the measured pole (model_admission): when the seed already contradicts
    the pole beyond the gate tolerance the pole is advisory and must not
    drag a human-anchored basin toward a light — the constraint is dropped
    and the disagreement logged once per fit.
    """
    if pole is None or lat_deg is None:
        return None
    sky_r = median_sky_r(frames)
    if is_guided(seed_model):
        w, h = median_frame_resolution(frames)
        ok, msg = validate_pole(seed_model, lat_deg, pole, sky_r=sky_r,
                                pole_image_width=w, pole_image_height=h)
        if not ok:
            log.info(f"Joint fit: measured pole not used as a constraint — it "
                     f"disagrees with the guided seed ({msg}); user anchors outrank it")
            return None
    if seed_model is not None:
        centre = (float(seed_model.cx), float(seed_model.cy))
    else:
        centre = (float(np.median([f.get('sky_cx', 0.0) for f in frames])),
                  float(np.median([f.get('sky_cy', 0.0) for f in frames])))
    return PoleConstraint.from_estimate(pole, lat_deg, tol_scale(sky_r), centre)


def build_all_matches(frames, model, tol_px: float,
                      min_per_image: int,
                      max_vmag: Optional[float] = None,
                      _log: bool = True) -> List[list]:
    """Match each frame's detections to catalog using the current model."""
    all_matches = []
    discarded = 0
    total_matched = 0
    for f in frames:
        horizon = f['above_horizon']
        if max_vmag is not None:
            horizon = [(s, a, z) for s, a, z in horizon if s.get('vmag', 9.0) <= max_vmag]
        matches = _brightness_match(f['detected'], horizon, model, tol_px=tol_px)
        if len(matches) >= min_per_image:
            all_matches.append(matches)
            total_matched += len(matches)
        else:
            discarded += 1
    # One summary line per call rather than one per frame: at ~60 frames over
    # 10 tightening iterations the per-frame form emitted ~600 DEBUG lines per
    # refinement cycle (every couple of minutes). The per-iteration INFO summary
    # in joint_iterative_fit covers the convergence story.
    if _log:
        log.debug(f"  tol={tol_px:.0f}px: kept {len(all_matches)} frames "
                  f"({total_matched} matches), discarded {discarded} "
                  f"(min={min_per_image}/frame)")
    return all_matches


def joint_iterative_fit(
    all_matches, frames, seed_model, min_per_image, min_total, max_residual,
    cx_range: float = 100.0,
    cy_range: float = 100.0,
    tol_scale_factor: float = 1.0,
    pole: Optional[PoleConstraint] = None,
) -> Tuple[FisheyeModel, float]:
    """
    Iterative joint optimisation over all matched frames.

    Each iteration:
      1. Run scipy least_squares on the pooled residuals.
      2. Re-match every frame at a tightening tolerance.
      3. Discard frames that fall below min_per_image matches.

    cx_range / cy_range: maximum allowed drift of the optical centre from
    the seed value (pixels).  Keeps the optimizer from drifting to a
    degenerate local minimum — the sky-circle centre is a hard physical
    constraint that orientation/polynomial parameters cannot compensate for.

    pole: a trusted measured pole (pole_tolerance.PoleConstraint) appended
    to every residual vector as a pseudo-observation weighted by
    POLE_WEIGHT·sqrt(N)/sigma, so the fit cannot walk away from it during
    the tolerance schedule. None leaves the residual vector exactly as it
    was without a pole.
    """
    if _least_squares is None:
        raise CalibrationError("scipy is required for calibration.")

    # Work on a copy from here on. For a seeded refinement, seed_model can be
    # the live CalibrationService._model the GUI thread renders from — if
    # least_squares throws on iteration 0 the loop below breaks with
    # `model is seed_model`, and writing n_matches/rms_residual/etc onto that
    # shared object (and should_replace comparing it with itself) would
    # corrupt the incumbent in place instead of just failing the refinement.
    model = dataclasses.replace(seed_model)

    # Anchor cx/cy within cx_range/cy_range of the seed model.
    # east_left is discrete — fixed from seed, not part of continuous optimisation.
    seed_cx, seed_cy = model.cx, model.cy
    east_left = model.east_left

    # Polynomial regularisation toward the seed (physical prior). See the
    # ridge-prior note above and REG_A3/REG_A5 rationale.
    seed_a3, seed_a5 = model.a3, model.a5

    # Bright anchor matches: kept at a fixed large tolerance throughout all iterations
    # so that easily-identified bright stars (vmag < 3.5) always participate in the
    # loss even when the tightening regular tolerance would exclude them.  Without
    # this, the optimizer can settle into a false minimum where hundreds of dim stars
    # match well but Arcturus/Vega/Antares/Deneb are 50-90 px off.
    _ANCH_TOL = 100.0 * tol_scale_factor
    _ANCH_MAX_VMAG = 3.5
    _ANCH_WEIGHT = 4.0  # multiply residuals → 16x contribution to squared loss
    anchor_matches = build_all_matches(
        frames, model, tol_px=_ANCH_TOL, min_per_image=0,
        max_vmag=_ANCH_MAX_VMAG, _log=False,
    )

    # Tolerance `all_matches` currently reflects. Tracked rather than recomputed
    # by the caller because the loop can break early (converged, or a failed
    # least_squares), and the chance-match gate must be judged at the tolerance
    # the surviving matches were actually built at.
    final_tol = 50.0 * tol_scale_factor

    for iteration in range(10):
        params = np.array([
            model.cx, model.cy, model.a1, model.a3, model.a5,
            model.roll, model.axis_alt, model.axis_az,
        ])

        # Capture by name; both are reassigned after _least_squares returns.
        current_matches = all_matches
        current_anchor = anchor_matches

        def residuals(p):
            m = _params_to_model(p, east_left)
            res = []
            for img_matches in current_matches:
                for (dx, dy), _star, (alt, az) in img_matches:
                    xy = m.altaz_to_pixel(alt, az)
                    if xy is None:
                        res.extend([30.0, 30.0])
                    else:
                        res.extend([dx - xy[0], dy - xy[1]])
            # Bright anchor terms: fixed large tolerance, high weight.
            # These prevent the false minimum where dim-star density produces low RMS
            # but the easily-identified bright anchors are far from any detection.
            for img_anchors in current_anchor:
                for (dx, dy), _star, (alt, az) in img_anchors:
                    xy = m.altaz_to_pixel(alt, az)
                    if xy is None:
                        res.extend([30.0 * _ANCH_WEIGHT, 30.0 * _ANCH_WEIGHT])
                    else:
                        res.extend([(dx - xy[0]) * _ANCH_WEIGHT,
                                    (dy - xy[1]) * _ANCH_WEIGHT])
            # Ridge prior on the radial polynomial (see note above). Weighted by
            # sqrt(N) so the prior's strength tracks the data volume.
            if res and (REG_A3 > 0 or REG_A5 > 0):
                w = float(np.sqrt(len(res)))
                res.append(REG_A3 * w * (m.a3 - seed_a3))
                res.append(REG_A5 * w * (m.a5 - seed_a5))
            # Measured-pole pseudo-observation (see POLE_WEIGHT).
            if res and pole is not None:
                w = POLE_WEIGHT * float(np.sqrt(len(res)))
                xy = m.altaz_to_pixel(pole.alt_deg, pole.az_deg)
                if xy is None:
                    res.extend([w * _POLE_OFF_IMAGE_RES, w * _POLE_OFF_IMAGE_RES])
                else:
                    res.extend([w * (xy[0] - pole.x) / pole.sigma_px,
                                w * (xy[1] - pole.y) / pole.sigma_px])
            return res

        try:
            # a3/a5 bounds match single-image fit: physical fisheye range.
            # axis_alt lower bound is 60° (not 45°) to match the grid-search floor —
            # allowing lower values lets the optimizer drift to a zenith-magnified false
            # minimum where dim stars cluster densely but bright anchors are missed.
            result = _least_squares(
                residuals, params,
                bounds=(
                    [seed_cx - cx_range, seed_cy - cy_range,
                     50,  A3_MIN, -1500.0, -np.pi, 60.0, -180.0],
                    [seed_cx + cx_range, seed_cy + cy_range,
                     2000,  A3_MAX,   500.0,  np.pi, 90.0,  540.0],
                ),
                method='trf',
                max_nfev=12000,
                ftol=1e-5,
            )
            raw = result.x.copy()
            raw[7] = raw[7] % 360.0   # normalise axis_az to [0, 360)
            model = _params_to_model(raw, east_left)
        except Exception as e:
            log.warning(f"Joint fit iteration {iteration} failed: {e}")
            break

        # Re-match every frame at tightening tolerance (schedule shape fixed;
        # endpoints scaled to the sky radius for resolution-independence, F10).
        # Floor at 18px (≈11px at this sky radius): on real frames a 5-8px floor
        # over-prunes — centroid noise + residual model error drop good matches
        # below min_per_image, the frames get discarded, and a genuinely correct
        # fit collapses to a handful of matches (observed: a correct orientation
        # held 79 matches at 9px then fell to 4 at 5px). A moderate floor keeps
        # the match set populated so correct fits survive and degenerate ones
        # are still distinguished by far lower counts.
        tol = tol_scale_factor * max(18.0, 50.0 - iteration * 5.0)
        final_tol = tol
        all_matches = build_all_matches(frames, model, tol_px=tol,
                                        min_per_image=min_per_image)
        # Rebuild anchor matches with updated model (fixed large tolerance)
        anchor_matches = build_all_matches(
            frames, model, tol_px=_ANCH_TOL, min_per_image=0,
            max_vmag=_ANCH_MAX_VMAG, _log=False,
        )

        total   = sum(len(m) for m in all_matches)
        rms     = joint_rms(all_matches, model)
        n_imgs  = len(all_matches)
        log.info(f"  Iter {iteration}: {total} matches / {n_imgs} frames, "
                 f"RMS={rms:.2f}px, tol={tol:.0f}px, "
                 f"axis_alt={model.axis_alt:.3f}")

        if rms < 2.5 and total >= min_total:
            break

    total = sum(len(m) for m in all_matches)
    rms   = joint_rms(all_matches, model)

    model.n_matches    = total
    model.rms_residual = float(rms)
    model.final_tol_px = float(final_tol)
    model.matched_stars = _collect_diagnostics(all_matches, model, frames)
    return model, rms


def joint_rms(all_matches, model) -> float:
    """Median pixel residual across all matches in all frames."""
    residuals = []
    for img_matches in all_matches:
        for (dx, dy), _star, (alt, az) in img_matches:
            xy = model.altaz_to_pixel(alt, az)
            if xy is not None:
                residuals.append(float(np.hypot(dx - xy[0], dy - xy[1])))
    return float(np.median(residuals)) if residuals else 999.0


# Underscored names kept for callers that imported them from multi_calibrate.
_build_all_matches = build_all_matches
_joint_iterative_fit = joint_iterative_fit
_joint_rms = joint_rms
