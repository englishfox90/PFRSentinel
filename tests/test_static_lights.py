"""Tests for services.allsky.static_lights — pool hygiene on synthetic buffers."""
from datetime import timedelta

import numpy as np
import pytest

from services.allsky.static_lights import (
    STATIC_CLUSTER_TOL_PX, STATIC_MIN_SPAN_MINUTES, clean_pools, find_static_lights,
    strip_obstructed, strip_static)
from tests.allsky_synth import (
    REPORTER, SlewingLight, StaticLight, instants, synth_frames, true_pole)

POLE = true_pole(REPORTER)
LIGHTS = [StaticLight(POLE[0] + 100, POLE[1], 1.2),
          StaticLight(1200.0, 2400.0, 0.6, flux=50000.0),
          StaticLight(2500.0, 2600.0, 2.0)]


def _has_light(frames, light, tol=STATIC_CLUSTER_TOL_PX):
    return any(np.hypot(x - light.x, y - light.y) <= tol
               for f in frames for x, y, _ in f['detected'])


class TestFindStaticLights:
    def test_finds_every_light_and_nothing_else(self):
        frames = synth_frames(REPORTER, instants(20, 120), seed=1, static_lights=LIGHTS)
        found = find_static_lights(frames)
        assert len(found) == 3
        for light in LIGHTS:
            assert min(np.hypot(x - light.x, y - light.y) for x, y in found) < 3.0

    def test_polaris_is_not_a_static_light(self):
        # 2–3 px of real motion over the window: the coherence test, not a
        # pixel tolerance, keeps it off the list (plan 4b).
        frames = synth_frames(REPORTER, instants(20, 120), seed=2, static_lights=LIGHTS)
        found = find_static_lights(frames)
        assert all(np.hypot(x - POLE[0], y - POLE[1]) > 20.0 for x, y in found)
        stripped = strip_static(frames, found)
        d = np.array(stripped[0]['detected'])
        assert np.min(np.hypot(d[:, 0] - POLE[0], d[:, 1] - POLE[1])) < 20.0

    def test_slewing_light_is_not_static(self):
        slew = SlewingLight(1500.0, 1500.0, 3.0, -2.0)
        frames = synth_frames(REPORTER, instants(20, 120), seed=3, slewing_light=slew)
        assert find_static_lights(frames) == []

    def test_needs_the_span(self):
        frames = synth_frames(REPORTER, instants(12, STATIC_MIN_SPAN_MINUTES - 5),
                              seed=4, static_lights=LIGHTS)
        assert find_static_lights(frames) == []

    def test_a_light_that_switches_on_mid_night_is_found(self):
        frames = synth_frames(REPORTER, instants(24, 230), seed=5, static_lights=LIGHTS)
        # Drop the first light from the first half of the night.
        for f in frames[:12]:
            f['detected'] = [d for d in f['detected']
                             if np.hypot(d[0] - LIGHTS[0].x, d[1] - LIGHTS[0].y) > 5]
        found = find_static_lights(frames)
        assert min(np.hypot(x - LIGHTS[0].x, y - LIGHTS[0].y) for x, y in found) < 3.0

    def test_chance_hits_on_a_dense_pool_are_not_lights(self):
        """The presence rule: random hits over a night fit no line but must
        not be called static (the 750 px replay stripped 85 % of a pool)."""
        rng = np.random.default_rng(6)
        frames = synth_frames(REPORTER, instants(30, 235), seed=6)
        for f in frames:   # crowd the pool: 2000 random detections a frame
            f['detected'] = [(float(x), float(y), 100.0) for x, y in
                             zip(rng.uniform(900, 2800, 2000), rng.uniform(700, 2600, 2000))]
        assert len(find_static_lights(frames)) < 3


class TestStrip:
    def test_strip_static_returns_new_dicts_and_keeps_the_rest(self):
        frames = synth_frames(REPORTER, instants(8, 60), seed=7, static_lights=LIGHTS)
        before = [len(f['detected']) for f in frames]
        out = strip_static(frames, [(light.x, light.y) for light in LIGHTS])
        assert all(o is not f for o, f in zip(out, frames))
        assert [len(f['detected']) for f in frames] == before   # originals untouched
        assert all(len(o['detected']) == b - 3 for o, b in zip(out, before))
        assert not any(_has_light(out, light) for light in LIGHTS)
        assert out[0]['sky_r'] == frames[0]['sky_r']

    def test_strip_static_with_no_lights_is_identity(self):
        frames = synth_frames(REPORTER, instants(3, 10), seed=8)
        assert strip_static(frames, []) == frames

    def test_strip_obstructed_none_map_is_identity(self):
        frames = synth_frames(REPORTER, instants(3, 10), seed=9)
        assert strip_obstructed(frames, None) == frames

    def test_strip_obstructed_drops_detections_where_the_map_says_equipment(self):
        frames = synth_frames(REPORTER, instants(3, 10), seed=10)

        class Map:
            calls = 0

            def sky_mask_for(self, w, h):
                Map.calls += 1
                m = np.ones((h, w), dtype=bool)
                m[:, :1800] = False       # left half is equipment
                return m
        out = strip_obstructed(frames, Map())
        assert Map.calls == 1                       # one mask per resolution
        assert all(x >= 1800 for f in out for x, _, _ in f['detected'])
        assert all(len(o['detected']) < len(f['detected']) for o, f in zip(out, frames))

    def test_strip_obstructed_tolerates_a_map_that_fails_or_mismatches(self):
        frames = synth_frames(REPORTER, instants(2, 10), seed=11)

        class Broken:
            def sky_mask_for(self, w, h):
                raise RuntimeError("no map yet")

        class Wrong:
            def sky_mask_for(self, w, h):
                return np.ones((10, 10), dtype=bool)
        assert strip_obstructed(frames, Broken()) == frames
        assert strip_obstructed(frames, Wrong()) == frames


class TestCleanPools:
    def test_lights_found_on_the_union_are_stripped_from_both(self):
        # Buffer: 30 min at 30 s (too short for the static rule alone);
        # ring: one frame per 10 min over 3 h.
        buffer = synth_frames(REPORTER, instants(60, 30), seed=12, static_lights=LIGHTS)
        ring_times = [buffer[-1]['dt'] - timedelta(minutes=10 * k) for k in range(18, -1, -1)]
        ring = synth_frames(REPORTER, ring_times, seed=13, static_lights=LIGHTS)
        assert find_static_lights(buffer) == []
        b2, r2, lights = clean_pools(buffer, ring, None)
        assert len(lights) == 3
        assert not any(_has_light(b2, light) for light in LIGHTS)
        assert not any(_has_light(r2, light) for light in LIGHTS)
        assert len(b2) == 60 and len(r2) == 19

    def test_no_ring_no_map_is_a_cheap_pass(self):
        buffer = synth_frames(REPORTER, instants(6, 30), seed=14)
        b2, r2, lights = clean_pools(buffer, None, None)
        assert lights == [] and r2 == [] and b2 == buffer


@pytest.mark.parametrize('n', [0, 1])
def test_empty_or_tiny_input(n):
    frames = synth_frames(REPORTER, instants(max(n, 1), 1), seed=0)[:n]
    assert find_static_lights(frames) == []
