"""
Replay a calibration buffer dump through the automatic-calibration path.

Loads a dump written by `services.allsky.buffer_dump` (the app's escape /
exhaustion / "Dump calibration buffer" output, or `library_to_buffer.py`)
and runs, in the order `_RefineWorker.run` does, what the service would have
run on it: the pole finder, the incumbent's bright-anchor health, the joint
fit seeded from the dump's model (or --model, or nothing with --cold-start),
the chance gate on the result, admission against the incumbent and the
replacement decision. Everything the service would log is printed too —
`app_logger` echoes to stdout — so the output reads like the rig's log.

Pure reporting: nothing is written. Dev-only, never imported by the app.

Run from the repo root:
    python scripts/dev/allsky/replay_buffer.py <dump.json> [--model path]
                                               [--cold-start]
"""
import argparse
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from services.allsky.buffer_dump import load_buffer, model_from_dict  # noqa: E402
from services.allsky.calibration import CalibrationError  # noqa: E402
from services.allsky.calibration_quality import model_quality  # noqa: E402
from services.allsky.calibration_validate import (  # noqa: E402
    median_frame_resolution, model_in_frame, tol_scale)
from services.allsky.calibration_workers import MAX_RESIDUAL_PX  # noqa: E402
from services.allsky.chance_matches import check_above_chance  # noqa: E402
from services.allsky.fisheye import FisheyeModel  # noqa: E402
from services.allsky.incumbent_evidence import incumbent_anchor_health  # noqa: E402
from services.allsky.model_admission import (  # noqa: E402
    admission_evidence, admit_candidate, east_left_hint)
from services.allsky.model_replacement import should_replace  # noqa: E402
from services.allsky.multi_calibrate import (  # noqa: E402
    median_sky_r, refine_from_detections)
from services.allsky.pole_consensus import PoleHistory  # noqa: E402
from services.allsky.pole_finder import find_pole  # noqa: E402
from services.allsky.static_lights import clean_pools  # noqa: E402


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _describe_frames(frames, lat, lon) -> None:
    dts = sorted(f['dt'] for f in frames)
    span_min = (dts[-1] - dts[0]).total_seconds() / 60.0 if len(dts) > 1 else 0.0
    w, h = median_frame_resolution(frames)
    n_det = sorted(len(f['detected']) for f in frames)
    n_cat = sorted(len(f['above_horizon']) for f in frames)
    exposures = [f['exposure_s'] for f in frames if f.get('exposure_s') is not None]
    print(f"site: lat={lat:.2f} lon={lon:.2f}")
    print(f"frames: {len(frames)}, {dts[0].isoformat()} -> {dts[-1].isoformat()} "
          f"({span_min:.1f} min span)")
    print(f"frame: {w}x{h}, median sky_r={median_sky_r(frames):.1f}px")
    print(f"detections/frame: min {n_det[0]}, median {n_det[len(n_det) // 2]}, "
          f"max {n_det[-1]}; catalogue stars above horizon: "
          f"{n_cat[0]}..{n_cat[-1]}")
    if exposures:
        print(f"exposure: {min(exposures):.2f}..{max(exposures):.2f} s")


def _describe_model(model, label: str) -> None:
    if model is None:
        print(f"{label}: none")
        return
    q = model_quality(model, model.n_images, model.span_minutes)
    tol = getattr(model, 'final_tol_px', None)
    print(f"{label}: {model} provenance={model.provenance!r} "
          f"n_images={model.n_images} span={model.span_minutes:.1f}min "
          f"quality={q} final_tol_px={tol}")


def replay(frames, seed, incumbent, lat, ring=None, use_pole: bool = True) -> int:
    _section("Pool hygiene")
    raw_n = sum(len(f['detected']) for f in frames)
    frames, ring, lights = clean_pools(frames, ring, None)
    print(f"static lights stripped: {len(lights)}; detections {raw_n} -> "
          f"{sum(len(f['detected']) for f in frames)}")
    sky_r = median_sky_r(frames)
    pole_w, pole_h = median_frame_resolution(frames)

    _section("Pole finder")
    history = PoleHistory()
    t0 = time.perf_counter()
    pole = history.record(find_pole(frames, lat, ring=ring) if use_pole else None, sky_r)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    if pole is None:
        print(f"pole: withheld / not trusted ({dt_ms:.0f} ms)")
    else:
        print(f"pole: ({pole.x:.1f}, {pole.y:.1f}) ± {pole.sigma_px:.1f}px "
              f"source={pole.source} east_left={pole.east_left} "
              f"a1={pole.a1_px_per_rad:.0f} over {pole.span_minutes:.0f} min "
              f"({dt_ms:.0f} ms)")
    drought = history.runs_since_trusted

    if incumbent is not None:
        _section("Incumbent on these frames")
        health = incumbent_anchor_health(incumbent, frames)
        verdict = {True: "passes the bright-anchor gate (an escape would be "
                         "cancelled)",
                   False: "definitely misses the bright stars (rule 3 would "
                          "apply)",
                   None: "cannot be judged (tie / obstructed / too few "
                         "testable frames)"}[health]
        print(f"anchor health: {health} — {verdict}")
        tol = getattr(incumbent, 'final_tol_px', None) or 18.0 * tol_scale(sky_r)
        _ok, msg, _est = check_above_chance(
            incumbent.n_matches, frames, incumbent, tol)
        print(f"chance (its own n_matches at tol={tol:.1f}px): {msg}")

    _section("Joint fit" + (" (cold start / basin escape)" if seed is None
                            else " (refinement)"))
    hint = east_left_hint(incumbent, pole, drought)
    print(f"east_left hint: {hint}")
    t0 = time.perf_counter()
    try:
        model = refine_from_detections(
            frames, seed, max_residual_px=MAX_RESIDUAL_PX, east_left_hint=hint,
            pole=pole, lat_deg=lat, ring=ring)
    except CalibrationError as e:
        print(f"REJECTED after {time.perf_counter() - t0:.0f} s: {e}")
        return 1
    print(f"fit took {time.perf_counter() - t0:.0f} s")
    _describe_model(model, "result")

    _section("Chance gate on the result")
    tol = getattr(model, 'final_tol_px', None) or 18.0 * tol_scale(sky_r)
    ok, msg, est = check_above_chance(model.n_matches, frames, model, tol)
    print(f"{'above' if ok else 'AT'} chance: {msg}; a chance fit would show "
          f"RMS ~{est.median_residual_px:.1f}px, this one {model.rms_residual:.2f}px")

    _section("Admission and replacement")
    ok, msg = admit_candidate(
        model, incumbent, lat, pole, sky_r,
        pole_image_width=pole_w, pole_image_height=pole_h,
        runs_without_pole=drought)
    print(f"admission: {'admitted' if ok else 'REFUSED'} — {msg}")
    if not ok:
        return 1
    evidence = admission_evidence(incumbent, pole, drought)
    model.n_images = len(frames)
    dts = sorted(f['dt'] for f in frames)
    model.span_minutes = (dts[-1] - dts[0]).total_seconds() / 60.0
    new_q = model_quality(model, model.n_images, model.span_minutes)
    inc_q = (model_quality(incumbent, incumbent.n_images, incumbent.span_minutes)
             if incumbent is not None else 'none')
    improved, why = should_replace(
        incumbent, inc_q, model, new_q, escape=seed is None, evidence=evidence,
        incumbent_failed_anchors=(seed is None and incumbent is not None
                                  and incumbent_anchor_health(incumbent, frames) is False))
    print(f"replacement: {'REPLACE' if improved else 'keep incumbent'} — {why}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dump", help="buffer_*.json written by the app or library_to_buffer.py")
    ap.add_argument("--model", help="allsky_calibration.json to seed from instead of "
                                    "the model recorded in the dump")
    ap.add_argument("--cold-start", action="store_true",
                    help="ignore any model: run the seedless bootstrap the basin "
                         "escape runs (slow: ~4 min on a 60-frame buffer)")
    ap.add_argument("--no-pole", action="store_true",
                    help="run the joint fit without the measured pole (before/"
                         "after comparisons)")
    args = ap.parse_args(argv)

    frames, model_dict, lat, lon = load_buffer(args.dump)
    _section(f"Dump {os.path.basename(args.dump)}")
    if not frames:
        print("no frames in dump")
        return 1
    _describe_frames(frames, lat, lon)

    incumbent = FisheyeModel.load(args.model) if args.model else model_from_dict(model_dict)
    if incumbent is not None:
        w, h = median_frame_resolution(frames)
        scaled = model_in_frame(incumbent, w, h)
        if scaled is not incumbent:
            print(f"model rescaled from {incumbent.image_width}x{incumbent.image_height} "
                  f"to the buffer's {w}x{h}")
            incumbent = scaled
    _describe_model(incumbent, "incumbent")
    seed = None if args.cold_start else incumbent
    return replay(frames, seed, incumbent, lat, use_pole=not args.no_pole)


if __name__ == "__main__":
    sys.exit(main())
