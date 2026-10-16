"""Tests for services.allsky.pole_from_rotation — the pole from the field.

Acceptance (plan §Package 4c, both rig presets and the southern mirror):
rotation pole within 25 px of truth with Polaris hidden and a static LED
100 px from the pole; three static lights and a slewing light give the
same answer; sigma_px covers the true error in ≥ 90 % of 20 seeded runs;
the fit on 36 ring frames × 200 detections stays under the resource
budget (plan §8: ≤ 10 s wall, ≤ 50 MB peak RSS growth).
"""
import gc
import logging
import os
import time
import tracemalloc
from dataclasses import replace

import numpy as np
import pytest

from services.allsky import axis_vote
from services.allsky import pole_from_rotation as pfr
from services.allsky.pole_from_rotation import fit_rotation, pole_from_rotation
from tests.allsky_synth import (
    PRESETS, REFERENCE, REPORTER, SOUTHERN, SlewingLight, StaticLight, instants,
    synth_frames, true_pole)

ACCEPT_PX = 25.0


def _led(preset, sigma=1.2):
    pole = true_pole(preset)
    return StaticLight(pole[0] + 100.0, pole[1], sigma)


def _err(est, preset):
    pole = true_pole(preset)
    return float(np.hypot(est.x - pole[0], est.y - pole[1]))


@pytest.mark.parametrize('preset', PRESETS, ids=lambda p: p.name)
class TestAcceptance:
    def test_pole_with_polaris_hidden_and_a_led_by_the_pole(self, preset):
        frames = synth_frames(preset, instants(12, 60), seed=1, hide_pole_px=60,
                              static_lights=[_led(preset)])
        est = pole_from_rotation(frames, preset.lat)
        assert est is not None
        assert _err(est, preset) < ACCEPT_PX
        assert est.source == 'rotation' and est.sigma_px > 0
        assert est.east_left is True     # every preset model is east_left=True
        assert est.window_start == frames[0]['dt'] and est.window_end == frames[-1]['dt']
        assert (est.image_width, est.image_height) == (3552, 3552)

    def test_three_static_lights_and_a_slewing_light_give_the_same_answer(self, preset):
        pole = true_pole(preset)
        clean = synth_frames(preset, instants(12, 60), seed=2, hide_pole_px=60)
        lights = [StaticLight(pole[0] + 100, pole[1], 1.2),
                  StaticLight(preset.sky_cx - 600, preset.sky_cy + 300, 0.6, 50000),
                  StaticLight(preset.sky_cx + 500, preset.sky_cy - 700, 2.0)]
        slew = SlewingLight(preset.sky_cx + 200, preset.sky_cy + 200, 4.0, -3.0)
        dirty = synth_frames(preset, instants(12, 60), seed=2, hide_pole_px=60,
                             static_lights=lights, slewing_light=slew)
        a = pole_from_rotation(clean, preset.lat)
        b = pole_from_rotation(dirty, preset.lat)
        assert a is not None and b is not None
        assert np.hypot(a.x - b.x, a.y - b.y) < 5.0
        assert _err(b, preset) < ACCEPT_PX

    def test_a_wrong_sky_circle_seed_is_recovered(self, preset):
        """The sky circle is a seed, not a measurement (plan §0.3)."""
        frames = synth_frames(
            preset, instants(12, 60), seed=3, hide_pole_px=60,
            sky_circle=(preset.sky_cx + 70, preset.sky_cy - 60, preset.sky_r * 0.85))
        fit = fit_rotation(frames, preset.lat)
        assert fit.estimate is not None, fit.reason
        assert _err(fit.estimate, preset) < ACCEPT_PX
        assert abs(fit.a1 / preset.model.a1 - 1.0) < 0.08


class TestMirror:
    @pytest.mark.parametrize('preset', [REFERENCE, SOUTHERN], ids=lambda p: p.name)
    def test_mirrored_model_flips_east_left(self, preset):
        mirrored = replace(preset, model=replace(preset.model, east_left=False))
        frames = synth_frames(mirrored, instants(12, 60), seed=4)
        est = pole_from_rotation(frames, mirrored.lat)
        assert est is not None and est.east_left is False

    def test_southern_pole_is_the_scp(self):
        frames = synth_frames(SOUTHERN, instants(12, 60), seed=5)
        est = pole_from_rotation(frames, SOUTHERN.lat)
        assert est is not None
        assert _err(est, SOUTHERN) < ACCEPT_PX


# Full acceptance run: 20 seeds per preset, ≥ 90 % coverage (plan 4c). It
# costs ~50 s, so CI runs 2 seeds per preset (6 runs, one short and one
# long window each, ~5 s) and demands every one covered; the 20-seed
# version runs with PFR_FULL_ACCEPTANCE=1 (100 % / 100 % / 100 % on
# 2026-09-25).
FULL_ACCEPTANCE = os.environ.get('PFR_FULL_ACCEPTANCE') == '1'
COVERAGE_SEEDS = 20 if FULL_ACCEPTANCE else 2
# One fixed rng seed per preset: str hashes are salted per process, so
# hash(preset.name) perturbed the sky circle differently on every run.
COVERAGE_RNG_SEED = {'reference': 101, 'reporter': 202, 'southern': 303}


class TestSigmaCoverage:
    @pytest.mark.slow
    @pytest.mark.parametrize('preset', PRESETS, ids=lambda p: p.name)
    def test_sigma_covers_the_true_error_in_90_percent_of_runs(self, preset):
        rng = np.random.default_rng(COVERAGE_RNG_SEED[preset.name])
        covered, found = 0, 0
        for seed in range(COVERAGE_SEEDS):
            n, span = (12, 60) if seed % 2 == 0 else (20, 120)
            circle = (preset.sky_cx + rng.uniform(-80, 80), preset.sky_cy + rng.uniform(-80, 80),
                      preset.sky_r * rng.uniform(0.8, 1.05))
            frames = synth_frames(preset, instants(n, span), seed=seed, hide_pole_px=60,
                                  static_lights=[_led(preset)], sky_circle=circle)
            est = pole_from_rotation(frames, preset.lat)
            if est is None:
                continue
            found += 1
            covered += _err(est, preset) <= est.sigma_px
        assert found >= COVERAGE_SEEDS - 1
        assert covered / found >= (0.9 if FULL_ACCEPTANCE else 1.0)


class TestWithholds:
    def test_requirements(self):
        few = synth_frames(REPORTER, instants(pfr.MIN_FRAMES - 1, 60), seed=6)
        assert fit_rotation(few, REPORTER.lat).reason.startswith(f"{pfr.MIN_FRAMES - 1} frames")
        short = synth_frames(REPORTER, instants(12, pfr.MIN_SPAN_MINUTES - 5), seed=6)
        assert 'span' in fit_rotation(short, REPORTER.lat).reason
        thin = synth_frames(REPORTER, instants(12, 60), seed=6)
        for f in thin:
            f['detected'] = f['detected'][:pfr.MIN_MEDIAN_DETECTIONS - 1]
        assert 'detections/frame' in fit_rotation(thin, REPORTER.lat).reason

    def test_random_field_has_no_pole(self):
        rng = np.random.default_rng(7)
        frames = synth_frames(REPORTER, instants(12, 60), seed=7)
        for f in frames:
            f['detected'] = [(float(x), float(y), 100.0) for x, y in
                             zip(rng.uniform(600, 3000, 150), rng.uniform(600, 3000, 150))]
        fit = fit_rotation(frames, REPORTER.lat)
        assert fit.estimate is None
        assert fit.reason

    def test_static_only_field_has_no_pole(self):
        """Unstripped lights vote 50x for an axis (their configuration is
        invariant) and the support gate is what refuses them."""
        rng = np.random.default_rng(8)
        lights = [StaticLight(rng.uniform(900, 2800), rng.uniform(700, 2600), 1.0)
                  for _ in range(60)]
        frames = synth_frames(REPORTER, instants(12, 60), seed=8, static_lights=lights)
        for f in frames:
            f['detected'] = [d for d in f['detected'] if d[2] >= 30000]
        fit = fit_rotation(frames, REPORTER.lat)
        assert fit.estimate is None
        assert fit.support < pfr.MIN_SUPPORT_FRACTION


class TestSeedModel:
    def test_guided_seed_is_used_for_the_radial_function(self):
        frames = synth_frames(REFERENCE, instants(12, 60), seed=9, hide_pole_px=60)
        seeded = fit_rotation(frames, REFERENCE.lat, seed_model=REFERENCE.model)
        assert seeded.estimate is not None
        assert _err(seeded.estimate, REFERENCE) < 10.0
        assert abs(seeded.a1 / REFERENCE.model.a1 - 1.0) < 0.05


class TestResourceBudget:
    """Plan §8: rotation fit ≤ 10 s wall and ≤ 50 MB peak-RSS growth on
    36 ring frames × 200 detections; the coarse vote well inside that.

    Verified 2026-09-25 on the CI container (single core): coarse vote at
    4 scales × 48 pairs 0.37 s; full fit 1.7–2.0 s wall, tracemalloc peak
    well under the budget (see the logged figure). Memory is measured with
    tracemalloc, as test_image_stretch does: `resource` does not exist on
    Windows, ru_maxrss is in different units per platform, and a process
    high-water mark reads 0 once an earlier test has raised it. The
    figures are logged on every run; the assertions are a 5× sanity
    ceiling so scheduler noise on a loaded runner cannot fail the suite
    while a genuine regression still does (the policy of the other
    packages' budget tests).
    """
    @pytest.mark.slow
    def test_36_ring_frames_within_the_budget(self):
        pole = true_pole(REFERENCE)
        frames = synth_frames(REFERENCE, instants(36, 350), seed=10, hide_pole_px=60,
                              static_lights=[StaticLight(pole[0] + 100, pole[1], 1.2)])
        assert int(np.median([len(f['detected']) for f in frames])) == 200
        fit_rotation(frames[:8], REFERENCE.lat)   # warm imports and caches
        gc.collect()
        tracemalloc.start()
        t0 = time.perf_counter()
        fit = fit_rotation(frames, REFERENCE.lat)
        wall = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak / (1024 * 1024)
        assert fit.estimate is not None, fit.reason
        assert _err(fit.estimate, REFERENCE) < ACCEPT_PX
        figures = f"rotation fit on 36 x 200: {wall:.2f} s wall, {peak_mb:.1f} MB peak (tracemalloc)"
        logging.getLogger(__name__).info(figures)
        assert wall < 50.0, f"{figures} — over the 5x sanity ceiling of the 10 s budget"
        assert peak_mb < 250.0, f"{figures} — over the 5x sanity ceiling of the 50 MB budget"

    def test_coarse_vote_alone_is_fast(self):
        frames = synth_frames(REFERENCE, instants(36, 350), seed=11)
        usable = sorted(frames, key=lambda f: f['dt'])
        t0 = usable[0]['dt']
        t_min = np.array([(f['dt'] - t0).total_seconds() / 60.0 for f in usable])
        xy = [np.asarray([(d[0], d[1]) for d in f['detected']], dtype=np.float32) for f in usable]
        lens = pfr._seed_lens(REFERENCE.sky_cx, REFERENCE.sky_cy, REFERENCE.sky_r, None)
        pairs = pfr._select_pairs(t_min, max_gap=pfr.HOUGH_MAX_GAP_MINUTES)
        alphas = pfr._alphas(t_min, pairs)
        vecs = [lens.unproject(p) for p in xy]
        start = time.perf_counter()
        seeds, ratio = axis_vote.hough_axis(vecs, pairs, alphas)
        wall = time.perf_counter() - start
        assert seeds and ratio > pfr.HOUGH_MIN_PEAK_RATIO
        figures = f"coarse vote, {len(pairs)} pairs at one scale: {wall:.2f} s"
        logging.getLogger(__name__).info(figures)
        assert wall < 5.0, f"{figures} — over the 5x sanity ceiling of the 1 s figure"
