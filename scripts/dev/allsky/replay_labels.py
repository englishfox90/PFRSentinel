"""
Replay a calibration buffer dump through the label pipeline and count flips.

Loads a dump written by `services.allsky.buffer_dump` (the app's "Dump
calibration buffer" button, an escape / exhaustion dump, or
`library_to_buffer.py`) and runs, frame by frame, the part of the overlay
that decides which labels are on screen: the sky-mask vote from the frame's
detections, the visibility plane (`sky_region`), star sightings, the
candidate ranking (`label_candidates`), the sticky top-N budget and the
drawn-label persistence (`label_persistence`). It prints, per label, how
often it switched on or off, and the totals, twice:

  * before — the budget's 3-frame absent hold and no drawn-label
    persistence: a label is drawn on exactly the frames it is eligible;
  * after  — what the app does now (issue #144).

Both passes use the current shared horizon cutoff, so the comparison
isolates the persistence; the old ranking's lower cutoffs also wasted
budget slots, which this does not reproduce.

The dump carries detections, not images, so this is the geometric part of
the pipeline only:

  * disc radii are not weighted by the local image brightness (every
    detection is treated as open sky, `sky_region.evidence_from_points`);
  * the detections are the calibration service's, not the overlay's own
    detection on the output frame — close, not identical;
  * the Moon's glare uses the default fraction of the sky radius instead of
    the measured saturated core, and the Moon is never "seen";
  * label placement (collisions with other labels) is not run, so a label
    the renderer would drop for lack of room still counts as drawn;
  * frames the service skipped (too few stars) are not in the dump, so the
    replay's frames are not always consecutive captures;
  * no equipment map unless one is passed with --map.

Pure reporting: nothing is written. Dev-only, never imported by the app.

Run from the repo root:
    python scripts/dev/allsky/replay_labels.py <dump.json> [--model path]
        [--config config.json] [--map allsky_obstruction.npz] [--top-n N]
        [--stars] [--all]
"""
import argparse
import json
import os
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

import numpy as np  # noqa: E402

from services.allsky.buffer_dump import load_buffer, model_from_dict  # noqa: E402
from services.allsky.calibration_validate import (  # noqa: E402
    median_frame_resolution, model_in_frame)
from services.allsky.coords import radec_to_altaz  # noqa: E402
from services.allsky.fisheye import FisheyeModel  # noqa: E402
from services.allsky.label_candidates import (  # noqa: E402
    eligible_labels, rank_label_candidates)
from services.allsky.label_stability import (  # noqa: E402
    LabelStabilizer, StickySelection, upsample)
from services.allsky.obstruction_map import ObstructionMap  # noqa: E402
from services.allsky.planets import moon_radec_topocentric  # noqa: E402
from services.allsky.sky_region import (  # noqa: E402
    FULL_MASK_MIN_DETECTIONS, MOON_GLARE_FRACTION, _select, evidence_from_points,
    grid_discs, model_sky_disc, model_sky_radius)
from services.allsky.star_sightings import (  # noqa: E402
    apply_sightings, label_targets, sighting_tolerance)
from services.config_defaults import DEFAULT_CONFIG  # noqa: E402

LEGACY_ABSENT_HOLD = 3


def _moon_exclude(model, dt, lat, lon, full_shape):
    ra, dec = moon_radec_topocentric(dt, lat, lon)
    alt, az = radec_to_altaz(ra, dec, lat, lon, dt, refraction=True)
    if float(alt) < 0.0:
        return None
    xy = model.altaz_to_pixel(float(alt), float(az))
    if xy is None:
        return None
    return grid_discs(full_shape, [xy], [MOON_GLARE_FRACTION * model_sky_radius(model)]) > 0


def _plane(frame, model, lat, lon, stab, obs_map, full_shape):
    """The visibility plane for one frame, advancing the vote and sightings
    as `sky_region.visibility_plane` and the renderer do."""
    h, w = full_shape
    det = np.array([(d[0], d[1]) for d in frame['detected']], dtype=float).reshape(-1, 2)
    evidence = evidence_from_points(full_shape, det)
    stab.fold_mask(evidence.mask, full_shape,
                   exclude=_moon_exclude(model, frame['dt'], lat, lon, full_shape),
                   partial=(evidence.mask is not None
                            and evidence.n_detections < FULL_MASK_MIN_DETECTIONS))
    vote, stale = stab.current_small_vote()
    map_sky = obs_map.small_sky_mask(w, h, None) if obs_map is not None else None
    small, _source = _select(vote, stale, map_sky, model_sky_disc(model, full_shape))
    return upsample(small, full_shape), evidence.points, map_sky


def replay(frames, model, scale, lat, lon, config, obs_map, legacy: bool):
    """Per-frame drawn sets ({uid: alpha}) for one pass."""
    stab = LabelStabilizer()
    if legacy:
        stab.selection = StickySelection(absent_hold=LEGACY_ABSENT_HOLD)
    top_n = int(config.get('top_n', 0))
    tol = sighting_tolerance(model.rms_residual, scale)
    out = []
    for frame in frames:
        full_shape = (int(frame.get('image_height') or model.image_height),
                      int(frame.get('image_width') or model.image_width))
        dt = frame['dt']
        gray, points, map_sky = _plane(frame, model, lat, lon, stab, obs_map, full_shape)
        gray = apply_sightings(gray, stab.sightings,
                               label_targets(model, config, lat, lon, dt),
                               points, tol, map_sky=map_sky)
        ranked = rank_label_candidates(config, model, lat, lon, dt, gray)
        allowed = stab.select(ranked, top_n) if top_n > 0 else None
        eligible = eligible_labels(ranked, allowed)
        out.append({u: 1.0 for u in eligible} if legacy
                   else stab.drawn_labels(eligible, budget=allowed))
    return out


def flips(drawn_frames):
    """{uid: on/off switches after the first frame}."""
    per = defaultdict(int)
    uids = set().union(*drawn_frames) if drawn_frames else set()
    for uid in uids:
        states = [uid in d for d in drawn_frames]
        per[uid] = sum(1 for a, b in zip(states, states[1:]) if a != b)
    return per


def _report(name, drawn_frames, top_n, show_all):
    per = flips(drawn_frames)
    counts = [len(d) for d in drawn_frames]
    faded = sum(1 for d in drawn_frames for a in d.values() if a < 1.0)
    total = sum(per.values())
    print(f"\n=== {name} ===")
    print(f"labels ever drawn: {len(per)}; flips: {total}; "
          f"labels flipping 3+ times: {sum(1 for v in per.values() if v >= 3)}")
    print(f"drawn per frame: mean {np.mean(counts):.1f}, max {max(counts)}"
          + (f" (budget {top_n}, over it on {sum(1 for c in counts if c > top_n)} frames)"
             if top_n > 0 else ""))
    if faded:
        print(f"label-frames drawn faded: {faded}")
    rows = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))
    for uid, n in rows if show_all else [r for r in rows if r[1] > 0]:
        trace = ''.join('#' if d.get(uid, 0.0) >= 1.0 else ('+' if uid in d else '.')
                        for d in drawn_frames)
        print(f"  {n:3d}  {uid:<24} {trace}")
    return total


def _config(path, top_n, stars):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG['allsky_overlay']))
    if path:
        with open(path, encoding='utf-8') as fh:
            loaded = json.load(fh).get('allsky_overlay', {})
        for key, value in loaded.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    if top_n is not None:
        cfg['top_n'] = top_n
    if stars:
        cfg['bright_stars']['enabled'] = True
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dump", help="buffer_*.json written by the app or library_to_buffer.py")
    ap.add_argument("--model", help="allsky_calibration.json instead of the dump's model")
    ap.add_argument("--config", help="config.json whose allsky_overlay layers to use "
                                     "(default: the app defaults)")
    ap.add_argument("--map", help="allsky_obstruction.npz to veto labels with")
    ap.add_argument("--top-n", type=int, help="override Max objects visible")
    ap.add_argument("--stars", action="store_true", help="turn the bright-star layer on")
    ap.add_argument("--all", action="store_true", help="list labels that never flip too")
    args = ap.parse_args(argv)

    frames, model_dict, lat, lon = load_buffer(args.dump)
    if not frames:
        print("no frames in dump")
        return 1
    frames = sorted(frames, key=lambda f: f['dt'])
    model = FisheyeModel.load(args.model) if args.model else model_from_dict(model_dict)
    if model is None:
        print("the dump holds no model: pass --model")
        return 1
    w, h = median_frame_resolution(frames)
    scaled = model_in_frame(model, w, h)
    scale = float(scaled.a1) / float(model.a1) if model.a1 else 1.0
    obs_map = None
    if args.map:
        obs_map = ObstructionMap()
        if not obs_map.load(args.map):
            print(f"could not load the equipment map {args.map}")
            return 1
    config = _config(args.config, args.top_n, args.stars)

    dts = [f['dt'] for f in frames]
    steps = [(b - a).total_seconds() for a, b in zip(dts, dts[1:])]
    print(f"dump {os.path.basename(args.dump)}: {len(frames)} frames at {w}x{h}, "
          f"{dts[0].isoformat()} -> {dts[-1].isoformat()}")
    if steps:
        median = float(np.median(steps))
        gaps = sum(1 for s in steps if s > 3 * median)
        print(f"frame interval: median {median:.0f} s; gaps over 3x: {gaps}")
    print(f"layers: stars={config['bright_stars'].get('enabled')} "
          f"planets={config['planets'].get('enabled')} "
          f"messier={config['messier'].get('enabled')} ngc={config['ngc'].get('enabled')} "
          f"top_n={config.get('top_n')}")
    print("trace: # drawn, + drawn fading, . not drawn")

    top_n = int(config.get('top_n', 0))
    before = _report(f"before (absent hold {LEGACY_ABSENT_HOLD}, no persistence)",
                     replay(frames, scaled, scale, lat, lon, config, obs_map, True),
                     top_n, args.all)
    after = _report("after (drawn-label persistence)",
                    replay(frames, scaled, scale, lat, lon, config, obs_map, False),
                    top_n, args.all)
    print(f"\nflips: {before} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
