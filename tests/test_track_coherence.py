"""Tests for services.allsky.track_coherence — does time explain a track?

The measurement that set COHERENCE_MIN_RATIO is reproduced here on the
synthetic fixture (tests/allsky_synth.py): Polaris versus a jittering
static light, at the reporter's plate scale where the margin is smallest.
"""
import numpy as np
import pytest

from services.allsky.pole_finder import (
    DRIFT_BAND, STATIC_NOISE_FLOOR_PX, predicted_polaris_arc_px)
from services.allsky.track_coherence import (
    COHERENCE_MIN_RATIO, MIN_HITS, is_coherent, is_static, track_coherence)
from tests.allsky_synth import REPORTER, StaticLight, instants, synth_frames, true_pole


class TestRegression:
    def test_straight_line_is_pure_progression(self):
        t = np.arange(10.0)
        tr = track_coherence(t, 3.0 * t, -4.0 * t)
        assert tr.progression_px == pytest.approx(45.0)
        assert tr.scatter_px == pytest.approx(0.0, abs=1e-9)
        assert tr.n_hits == 10 and tr.span_minutes == 9.0

    def test_scatter_is_the_rms_about_the_line(self):
        rng = np.random.default_rng(1)
        t = np.linspace(0, 60, 60)
        x = 0.05 * t + rng.normal(0, 1.0, 60)
        y = rng.normal(0, 1.0, 60)
        tr = track_coherence(t, x, y)
        # sqrt(sum of both axes' squared residuals / (n-2)) ≈ sqrt(2)
        assert 1.2 < tr.scatter_px < 1.65

    def test_too_few_hits_or_zero_span_is_none(self):
        assert track_coherence([0, 1, 2], [0, 0, 0], [0, 0, 0]) is None
        assert track_coherence([5.0] * MIN_HITS, [0] * MIN_HITS, [0] * MIN_HITS) is None
        assert track_coherence([0, 1, 2, 3], [0, 1], [0, 1]) is None


class TestVerdicts:
    ARC = 3.0

    def test_progressing_track_inside_the_band_is_coherent(self):
        t = np.linspace(0, 60, 30)
        tr = track_coherence(t, t / 20.0, np.zeros(30))   # 3 px over the window
        assert is_coherent(tr, self.ARC, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)

    def test_jitter_without_progression_is_not(self):
        rng = np.random.default_rng(2)
        t = np.linspace(0, 60, 60)
        tr = track_coherence(t, rng.normal(0, 1.2, 60), rng.normal(0, 1.2, 60))
        assert not is_coherent(tr, self.ARC, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)
        assert is_static(tr, COHERENCE_MIN_RATIO)

    def test_progression_outside_the_band_is_not_coherent(self):
        t = np.linspace(0, 60, 30)
        fast = track_coherence(t, t, np.zeros(30))           # 60 px: a slewing light
        slow = track_coherence(t, t / 200.0, np.zeros(30))   # 0.3 px: under the floor
        assert not is_coherent(fast, self.ARC, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)
        assert not is_coherent(slow, self.ARC, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)

    def test_none_track_is_neither(self):
        assert not is_coherent(None, self.ARC, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)
        assert not is_static(None)

    def test_static_floor_catches_a_perfectly_steady_light(self):
        t = np.linspace(0, 60, 30)
        tr = track_coherence(t, 0.01 * t / 60.0, np.zeros(30))   # 0.01 px, no scatter
        assert is_static(tr, COHERENCE_MIN_RATIO, floor_px=0.5)


def _tracks(preset, n, span, seed, sigma):
    pole = true_pole(preset)
    led = StaticLight(pole[0] + 100, pole[1], sigma)
    frames = synth_frames(preset, instants(n, span), seed=seed, static_lights=[led])
    t, px, py, lx, ly = [], [], [], [], []
    for f in frames:
        d = np.array(f['detected'])
        r = np.hypot(d[:, 0] - pole[0], d[:, 1] - pole[1])
        j = int(np.argmin(r))
        assert r[j] < 30, "Polaris must be the nearest detection to the pole"
        t.append((f['dt'] - frames[0]['dt']).total_seconds() / 60.0)
        px.append(d[j, 0]); py.append(d[j, 1])
        k = int(np.argmin(np.hypot(d[:, 0] - led.x, d[:, 1] - led.y)))
        lx.append(d[k, 0]); ly.append(d[k, 1])
    return track_coherence(t, px, py), track_coherence(t, lx, ly)


class TestMeasuredSeparation:
    """The numbers behind COHERENCE_MIN_RATIO (reporter scale, 60 hits, 47 min).

    Full measurement, 100 seeds per cell (2026-09-25): Polaris ratio min
    3.28 (47 min) / 4.13 (60 min); a 1.2 px light max 1.21 / 0.94; a 2.0 px
    light max 3.31 / 2.74 — but a 2 px light's *progression* stays under
    2.1 px at the 95th percentile, below the 3 px separable arc, so the
    band catches what the ratio does not. With only 12 sampled hits the
    light reached 2.64 and Polaris fell to 1.96, which is why the finder
    gathers coherence hits from every frame.
    """
    @pytest.mark.slow
    def test_measured_separation(self):
        polaris, light = [], []
        for seed in range(12):
            a, b = _tracks(REPORTER, 60, 47.0, seed, 1.2)
            polaris.append(a.ratio)
            light.append(b.ratio)
        assert min(polaris) > COHERENCE_MIN_RATIO * 1.2
        assert max(light) < COHERENCE_MIN_RATIO / 1.2

    def test_polaris_is_coherent_and_the_light_is_static(self):
        arc = predicted_polaris_arc_px(REPORTER.sky_r, 47.0)
        assert arc < 3.0   # the reporter's regime: under the separable floor
        a, b = _tracks(REPORTER, 60, 60.0, 3, 1.2)
        arc60 = predicted_polaris_arc_px(REPORTER.sky_r, 60.0)
        assert is_coherent(a, arc60, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)
        assert is_static(b, COHERENCE_MIN_RATIO)
        assert not is_coherent(b, arc60, DRIFT_BAND, STATIC_NOISE_FLOOR_PX)
