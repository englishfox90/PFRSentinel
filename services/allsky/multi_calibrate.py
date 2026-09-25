"""
Multi-image all-sky fisheye calibration.

Jointly fits ONE FisheyeModel to N exposures from the same fixed camera.
All optical/mount parameters (cx, cy, a1, a3, a5, roll, axis_alt, axis_az)
are shared; each image only differs by observation time, which changes the
AltAz of every catalog star.

Benefits over single-image calibration
---------------------------------------
- Many more matched stars → better-constrained polynomial (a3, a5)
- Stars at different altitudes/azimuths in each frame → axis_alt/az
  is no longer degenerate and rarely hits the 90° upper bound
- Robust to a few bad frames (they contribute only a small fraction of
  the total residual)

Algorithm
---------
1.  Detect stars in every frame.
2.  For each frame, build initial matches against the BSC5 catalog using
    a seed FisheyeModel (derived from single-image calibration on the
    frame with the most detections, or supplied by the caller).
3.  Iteratively run scipy least_squares on the pooled residuals, then
    re-match each frame at successively tighter tolerances.
4.  Accept if total median residual < max_residual_px and total matches
    >= min_total_matches.

Dependencies: scipy (same as single-image calibration).
"""
import dataclasses
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from services.logger import app_logger as log

from .star_centroid import detect_stars, estimate_sky_circle
from .fisheye import FisheyeModel
from .catalogs import get_bright_stars
from .calibration import (
    CalibrationError,
    _get_image_size,
    _catalog_altaz,
    calibrate,
)
from .calibration_validate import (
    median_sky_r,
    validate_a1_scale,
    validate_bright_anchors,
    validate_lens_polynomial,
    tol_scale,
)
from .chance_matches import check_above_chance
from .bootstrap_selection import (
    chance_excess, describe_orientation, find_rival, select_bootstrap_winner)
# Extracted to joint_fit.py / orientation_search.py (file-size cap); the
# underscored names are re-exported here for existing callers and tests.
from .joint_fit import (  # noqa: F401
    _build_all_matches, _joint_iterative_fit, _joint_rms, pole_constraint_for)
from .orientation_search import (  # noqa: F401
    _coarse_orientation_candidates, centre_offset_vote, search_frames)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def refine_from_detections(
    frames: List[dict],
    seed_model: Optional[FisheyeModel] = None,
    min_matches_per_image: int = 4,
    min_total_matches: int = 20,
    max_residual_px: float = 20.0,
    east_left_hint: Optional[bool] = None,
    pole=None,
    lat_deg: Optional[float] = None,
    ring: Optional[List[dict]] = None,
) -> FisheyeModel:
    """
    Joint calibration from pre-processed frame data.

    Unlike multi_calibrate(), this skips star detection (already done).
    Designed for the background calibration service which detects stars on
    arrival and stores only the detections.

    Args:
        frames: list of dicts with keys: dt, detected, above_horizon.
        seed_model: starting FisheyeModel. When None, a coarse seed is derived
            from scratch via a cross-frame orientation search (cold-start
            bootstrap, no prior model and any site).
        min_matches_per_image: discard frames with fewer matches.
        min_total_matches: fail if total matches below this.
        max_residual_px: maximum accepted median residual (pixels).
        east_left_hint: measured mirror convention (pole_finder); restricts
            the cold-start orientation search to the correct mirror.
        pole: the trusted measured pole (pole_consensus) in the frames'
            resolution, or None. With `lat_deg` it seeds and filters the
            cold-start orientation search and is a pseudo-observation in
            the joint fit (joint_fit.POLE_WEIGHT) — never a requirement.
        ring: the long-baseline frame ring (frame_ring) the orientation
            search prefers to `frames`.

    Returns:
        Refined FisheyeModel.

    Raises:
        CalibrationError on insufficient data or poor fit.
    """
    if len(frames) < 1:
        raise CalibrationError("No frames provided for refinement.")

    constraint = pole_constraint_for(pole, lat_deg, frames, seed_model)

    # --- Warm path: refine an existing model ---
    if seed_model is not None:
        log.info(f"Refining from {len(frames)} pre-processed frame(s)"
                 + (" with the measured pole as a constraint" if constraint else ""))
        return _fit_and_validate(
            frames, seed_model, min_matches_per_image,
            min_total_matches, max_residual_px, pole=constraint,
        )

    # --- Cold start: fit EVERY coarse orientation candidate through the joint
    # fit + gates, then pick a winner. Raw match count is NOT the
    # discriminator: on a high-resolution rig the greedy matcher returns
    # near-identical counts for every orientation (issue #33), so ranking by it
    # chooses between contradictory basins at random. See bootstrap_selection
    # for the ordering that replaced it. ---
    candidates = _coarse_orientation_candidates(
        frames, east_left_hint=east_left_hint, pole=pole, lat_deg=lat_deg, ring=ring)
    vote_frames = search_frames(frames, ring)
    passed = []
    last_err = None
    for i, seed in enumerate(candidates):
        try:
            model = _fit_and_validate(
                frames, seed, min_matches_per_image,
                min_total_matches, max_residual_px,
                pole=constraint, centre_vote_frames=vote_frames,
            )
            passed.append(model)
            log.info(f"Bootstrap candidate {i + 1}/{len(candidates)} passed: "
                     f"n_matches={model.n_matches}, rms={model.rms_residual:.1f}px, "
                     f"excess over chance={chance_excess(model):.0f}")
        except CalibrationError as e:
            last_err = e
            log.info(f"Bootstrap candidate {i + 1}/{len(candidates)} rejected: {e}")

    if not passed:
        raise CalibrationError(
            f"Cold-start bootstrap failed: no candidate orientation passed "
            f"({last_err})."
        )

    best, why = select_bootstrap_winner(passed, frames)
    rival = find_rival(passed, best)
    if rival is not None:
        raise CalibrationError(
            "Cold-start bootstrap withheld: two incompatible solutions explain "
            f"the frames — {describe_orientation(best)} and "
            f"{describe_orientation(rival)} share under half their matched "
            "stars; more sky is needed before either can be trusted.")
    log.info(f"Cold-start bootstrap: selected best of {len(passed)} passing "
             f"candidate(s) by excess over chance, then bright-anchor hits "
             f"({why})")
    return best


def _fit_and_validate(
    frames: List[dict],
    seed_model: FisheyeModel,
    min_matches_per_image: int,
    min_total_matches: int,
    max_residual_px: float,
    pole=None,
    centre_vote_frames: Optional[List[dict]] = None,
) -> FisheyeModel:
    """Joint-fit from a seed and enforce the residual + sanity gates.

    `pole` is a joint_fit PoleConstraint (or None). `centre_vote_frames`
    turns on the centre-offset vote for a coarse cold-start seed: the
    bright stars correct cx/cy before the first match, which the fit's own
    ±100 px centre bound could not (orientation_search).
    """
    _ts = tol_scale(median_sky_r(frames))
    if centre_vote_frames:
        dx, dy, votes = centre_offset_vote(centre_vote_frames, seed_model,
                                           median_sky_r(frames))
        if votes:
            log.info(f"Centre offset vote: ({dx:+.0f}, {dy:+.0f}) px on {votes} votes")
            seed_model = dataclasses.replace(seed_model, cx=seed_model.cx + dx,
                                             cy=seed_model.cy + dy)
    all_matches = _build_all_matches(frames, seed_model, tol_px=50.0 * _ts,
                                     min_per_image=min_matches_per_image)
    total = sum(len(m) for m in all_matches)
    log.info(f"Initial matches: {total} across {len(all_matches)} frame(s)")

    if total < max(3, min_total_matches // 4):
        raise CalibrationError(
            f"Only {total} initial matches across all frames — cannot refine."
        )

    model, rms = _joint_iterative_fit(
        all_matches, frames, seed_model,
        min_matches_per_image, min_total_matches, max_residual_px,
        tol_scale_factor=_ts, pole=pole,
    )

    if rms > max_residual_px:
        raise CalibrationError(
            f"Refinement residual {rms:.1f}px exceeds limit {max_residual_px}px."
        )

    # Minimum total matches. A wrong/degenerate orientation (e.g. a 180° roll
    # flip) loses almost all matches as the tolerance tightens — it survives on
    # a handful of coincidental alignments that can still include enough bright
    # anchors to fool the anchor gate (observed: a rotated fit saved with only
    # 13 matches, placing Polaris on the wrong side). A correct fit keeps tens
    # to hundreds of matches across frames, so this cleanly rejects the
    # degenerate basins the joint fit can otherwise reach.
    if model.n_matches < min_total_matches:
        raise CalibrationError(
            f"Refinement matched only {model.n_matches} stars "
            f"(need >= {min_total_matches}) — likely a degenerate or wrong "
            "orientation."
        )

    # The floor above is absolute; this one is relative to what the greedy
    # matcher hands out for free. On a high-resolution rig the tolerance disc
    # covers enough of the sky that a wrong basin clears any fixed floor on
    # coincidence alone (issue #33: ~686 matches at RMS 10.86px for every
    # orientation between 60 and 90°, against 619 expected by chance).
    chance_ok, chance_msg, est = check_above_chance(
        model.n_matches, frames, model,
        model.final_tol_px or 18.0 * _ts,
    )
    model.chance_expected = est.expected
    model.chance_ratio = model.n_matches / max(est.expected, 1.0)
    if not chance_ok:
        raise CalibrationError(
            f"Refinement is at chance level: {chance_msg} — the match count "
            "carries no orientation information at this resolution."
        )

    # Sanity checks — multi-image fits over a short span (minutes) can
    # converge to non-physical basins where roll + polynomial + axis_alt
    # collude to satisfy all frames simultaneously without matching the
    # real sky. Guard with lens-polynomial physics + plate-scale consistency
    # + bright-anchor hit rate on the most recent frames.
    poly_ok, poly_msg = validate_lens_polynomial(model)
    scale_ok, scale_msg = validate_a1_scale(model, median_sky_r(frames))

    # Validate bright anchors on the 3 most recent frames — require majority
    # (at least 2 of 3) to pass. Single-frame validation was fragile: one
    # partially-cloudy frame could silently gate the entire calibration path.
    recent = frames[-3:]
    anch_results = [
        validate_bright_anchors(model, f['above_horizon'], f['detected'],
                                sky_r=f.get('sky_r'))
        for f in recent
    ]
    n_ok = sum(1 for ok, _ in anch_results if ok)
    min_ok = max(1, len(recent) - 1)  # 2/3 frames, 1/2 frames, or 1/1 frame
    anch_ok = n_ok >= min_ok
    if anch_ok:
        anch_msg = f"{n_ok}/{len(recent)} recent frames pass anchor check"
    else:
        fail_msgs = [m for ok, m in anch_results if not ok]
        anch_msg = (f"only {n_ok}/{len(recent)} recent frames pass anchor check: "
                    + "; ".join(fail_msgs[:2]))

    if not (poly_ok and scale_ok and anch_ok):
        reason = "; ".join(
            m for ok, m in ((poly_ok, poly_msg), (scale_ok, scale_msg),
                            (anch_ok, anch_msg)) if not ok
        )
        raise CalibrationError(f"Refinement failed sanity check: {reason}")

    latest = frames[-1]
    model.image_width = latest.get('image_width', 0)
    model.image_height = latest.get('image_height', 0)
    model.calibrated_at = datetime.now(timezone.utc).isoformat()
    log.info(f"Refinement succeeded: {model} ({poly_msg}; {anch_msg})")
    return model


def multi_calibrate(
    images_and_times: List[Tuple],
    lat_deg: float,
    lon_deg: float,
    seed_model: Optional[FisheyeModel] = None,
    max_stars: int = 200,
    min_matches_per_image: int = 4,
    min_total_matches: int = 20,
    max_residual_px: float = 10.0,
) -> FisheyeModel:
    """
    Calibrate a fisheye lens model from multiple exposures.

    Args:
        images_and_times: List of (image, utc_datetime) where image is a
                          PIL Image or numpy array.
        lat_deg: Observer latitude (degrees, north positive).
        lon_deg: Observer longitude (degrees, east positive).
        seed_model: Optional starting model.  If None, the best single-frame
                    calibration is used as the seed.
        max_stars: Max detected stars per image (passed to detect_stars).
        min_matches_per_image: Images with fewer matches are discarded.
        min_total_matches: Minimum total matches required across all images.
        max_residual_px: Maximum accepted median residual (pixels).

    Returns:
        Calibrated FisheyeModel.

    Raises:
        CalibrationError on insufficient data or poor fit.
    """
    if len(images_and_times) < 1:
        raise CalibrationError("No images provided for multi-image calibration.")

    log.info(f"Multi-calibrate: {len(images_and_times)} image(s)")

    # ------------------------------------------------------------------
    # Step 1: Star detection for every frame
    # ------------------------------------------------------------------
    frames = []                         # [{image, dt, detections, catalog_altaz}, ...]
    img_cx0, img_cy0 = None, None       # shared optical-centre seed

    for image, dt in images_and_times:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        sky_cx, sky_cy, sky_r = estimate_sky_circle(image)
        if img_cx0 is None:
            img_cx0, img_cy0 = sky_cx, sky_cy

        detected = detect_stars(
            image, max_stars=max_stars,
            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r,
        )
        log.info(f"  {dt.isoformat()}: {len(detected)} candidate stars")

        catalog     = get_bright_stars(max_mag=6.5)
        cat_altaz   = _catalog_altaz(catalog, lat_deg, lon_deg, dt)
        above_horiz = [(s, a, z) for s, a, z in cat_altaz if a > 3.0]
        above_horiz.sort(key=lambda x: x[0]['vmag'])

        frames.append({
            'image': image,
            'dt':    dt,
            'detected':     detected,
            'above_horizon': above_horiz,
            'sky_cx': sky_cx, 'sky_cy': sky_cy, 'sky_r': sky_r,
        })

    # ------------------------------------------------------------------
    # Step 2: Seed model
    # ------------------------------------------------------------------
    if seed_model is None:
        seed_model = _best_single_frame_model(
            frames, lat_deg, lon_deg, img_cx0, img_cy0
        )
        log.info(f"Seed model: a1={seed_model.a1:.1f}, "
                 f"axis_alt={seed_model.axis_alt:.2f}, rms={seed_model.rms_residual:.2f}px")
    else:
        log.info("Using supplied seed model.")

    # ------------------------------------------------------------------
    # Step 3: Initial matching for all frames using the seed model
    # ------------------------------------------------------------------
    _ts = tol_scale(median_sky_r(frames))
    all_matches = _build_all_matches(frames, seed_model, tol_px=50.0 * _ts,
                                     min_per_image=min_matches_per_image)
    total = sum(len(m) for m in all_matches)
    log.info(f"Initial total matches: {total} across {len(all_matches)} frame(s)")

    if total < max(3, min_total_matches // 4):
        raise CalibrationError(
            f"Only {total} initial matches across all frames — cannot proceed.\n"
            "  Ensure images show a clear night sky and lat/lon/time are correct."
        )

    # ------------------------------------------------------------------
    # Step 4: Joint iterative fit
    # ------------------------------------------------------------------
    model, rms = _joint_iterative_fit(
        all_matches, frames, seed_model,
        min_matches_per_image, min_total_matches, max_residual_px,
        tol_scale_factor=_ts,
    )

    if rms > max_residual_px:
        raise CalibrationError(
            f"Multi-calibration residual {rms:.1f}px exceeds limit {max_residual_px}px."
        )

    log.info(f"Multi-calibration succeeded: {model}, RMS={rms:.2f}px")
    return model


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _best_single_frame_model(frames, lat, lon, cx0, cy0) -> FisheyeModel:
    """Run single-image calibration on the frame with the most detections."""
    best_frame = max(frames, key=lambda f: len(f['detected']))
    try:
        model = calibrate(
            best_frame['image'], lat, lon, dt=best_frame['dt'],
            image_cx=cx0, image_cy=cy0,
        )
        return model
    except CalibrationError as e:
        log.warning(f"Single-frame seed failed: {e}; using default model.")
        img_h, img_w = _get_image_size(best_frame['image'])
        a1_guess = min(img_w, img_h) * 0.5 * 0.75 / (np.pi / 2.0)
        return FisheyeModel(cx=cx0, cy=cy0, a1=a1_guess)

