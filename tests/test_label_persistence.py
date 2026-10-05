"""
Tests for services/allsky/label_persistence.py and the shared horizon cutoff
(issue #144): the drawn labels are debounced (two eligible frames before a
first draw), held and faded after the last one, never advanced by a
reprocess, and the renderers draw a held label whatever the plane says. The
ranking and the layers agree on the horizon, so a low object no longer takes
a top-N slot it can never use.
"""
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

from services.allsky import label_candidates, overlay_renderer
from services.allsky.catalogs import get_messier_objects
from services.allsky.coords import radec_to_altaz
from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid
from services.allsky.label_persistence import (
    LABEL_FADE_IN_FRAMES, LABEL_FADE_OUT_FRAMES, LABEL_HOLD_FRAMES,
    LABEL_SHOW_FRAMES, LabelPersistence,
)
from services.allsky.label_stability import (
    ABSENT_HOLD_FRAMES, forget_drawn_labels, get_label_stabilizer,
    reset_label_stability,
)
from services.allsky.render_objects import LABEL_MIN_ALT_DEG, render_messier
from services.allsky.render_stars import bright_star_targets
from services.observing_window import SAME_CAPTURE_KEY

DT = datetime(2024, 6, 21, 22, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1


@pytest.fixture(autouse=True)
def _fresh_label_state():
    reset_label_stability()
    yield
    reset_label_stability()


def _started(**kw) -> LabelPersistence:
    """A persistence past its first frame, so the debounce applies."""
    p = LabelPersistence(**kw)
    p.update(['other'])
    return p


def _alphas(p, frames, uid='a'):
    """Alpha of ``uid`` after each frame (0.0 = not drawn)."""
    return [p.update(f).get(uid, 0.0) for f in frames]


# ===================================================================
# Debounce, hold and fade
# ===================================================================

class TestDebounce:
    def test_first_appearance_needs_two_eligible_frames(self):
        p = _started()
        assert LABEL_SHOW_FRAMES == 2
        assert 'a' not in p.update(['a'])
        assert 'a' in p.update(['a'])

    def test_a_one_frame_newcomer_is_never_drawn(self):
        p = _started()
        assert _alphas(p, [['a'], [], ['a'], [], ['a'], []]) == [0.0] * 6

    def test_the_first_frame_after_a_reset_draws_what_it_finds(self):
        """No history to debounce against: the first preview of a session
        is not left blank."""
        p = LabelPersistence()
        assert p.update(['a', 'b']) == {'a': 1.0, 'b': 1.0}
        assert 'c' not in p.update(['a', 'b', 'c']), "debounced from then on"

    def test_fade_in_is_monotonic_and_reaches_full(self):
        p = _started()
        alphas = _alphas(p, [['a']] * (LABEL_SHOW_FRAMES + LABEL_FADE_IN_FRAMES + 2))
        drawn = [a for a in alphas if a > 0.0]
        assert drawn == sorted(drawn)
        assert len([a for a in drawn if a < 1.0]) == LABEL_FADE_IN_FRAMES - 1
        assert alphas[-1] == 1.0


class TestHold:
    def _shown(self):
        p = _started()
        _alphas(p, [['a']] * (LABEL_SHOW_FRAMES + LABEL_FADE_IN_FRAMES))
        assert p.update(['a'])['a'] == 1.0
        return p

    def test_a_drawn_label_is_held_for_the_hold_then_dropped(self):
        p = self._shown()
        alphas = _alphas(p, [[]] * (LABEL_HOLD_FRAMES + 3))
        assert all(a > 0.0 for a in alphas[:LABEL_HOLD_FRAMES])
        assert alphas[LABEL_HOLD_FRAMES:] == [0.0, 0.0, 0.0]

    def test_hold_is_long_enough_for_a_timelapse(self):
        """Issue #144: at 24 fps a 3-frame hold was 1/8 s of video."""
        assert LABEL_HOLD_FRAMES >= 10
        assert ABSENT_HOLD_FRAMES == LABEL_HOLD_FRAMES

    def test_fade_out_is_monotonic_and_only_at_the_end_of_the_hold(self):
        p = self._shown()
        alphas = _alphas(p, [[]] * LABEL_HOLD_FRAMES)
        assert alphas == sorted(alphas, reverse=True)
        steady = LABEL_HOLD_FRAMES - LABEL_FADE_OUT_FRAMES
        assert alphas[:steady] == [1.0] * steady
        assert all(0.0 < a < 1.0 for a in alphas[steady:])

    def test_a_short_drop_is_drawn_at_full_strength(self):
        p = self._shown()
        assert _alphas(p, [[], [], ['a'], ['a']]) == [1.0, 1.0, 1.0, 1.0]

    def test_a_label_returning_late_in_the_hold_fades_back_without_a_debounce(self):
        p = self._shown()
        late = _alphas(p, [[]] * (LABEL_HOLD_FRAMES - 1))[-1]
        back = _alphas(p, [['a']] * LABEL_FADE_IN_FRAMES)
        assert 0.0 < late < back[0] and back[-1] == 1.0
        assert back == sorted(back)

    def test_a_lasting_drop_ends_after_the_hold_and_needs_the_debounce_again(self):
        p = self._shown()
        _alphas(p, [[]] * (LABEL_HOLD_FRAMES + 1))
        assert 'a' not in p.update(['a'])
        assert 'a' in p.update(['a'])


class TestBudget:
    def _shown(self):
        p = _started()
        _alphas(p, [['a']] * (LABEL_SHOW_FRAMES + LABEL_FADE_IN_FRAMES))
        return p

    def test_a_label_still_holding_its_slot_gets_the_full_hold(self):
        p = self._shown()
        alphas = [p.update([], budget={'a'}).get('a', 0.0)
                  for _ in range(LABEL_HOLD_FRAMES + 1)]
        assert sum(1 for a in alphas if a > 0.0) == LABEL_HOLD_FRAMES

    def test_a_label_whose_slot_went_to_another_crossfades_out(self):
        """A real swap is not held: the old label fades over the fade-out
        frames while the newcomer fades in, so the drawn count exceeds the
        budget for no longer than that."""
        p = self._shown()
        alphas = [p.update(['b'], budget={'b'}).get('a', 0.0)
                  for _ in range(LABEL_HOLD_FRAMES)]
        fading = [a for a in alphas if a > 0.0]
        assert len(fading) == LABEL_FADE_OUT_FRAMES
        assert fading == sorted(fading, reverse=True) and fading[0] < 1.0

    def test_no_budget_means_no_crossfade(self):
        p = self._shown()
        alphas = [p.update([]).get('a', 0.0) for _ in range(LABEL_HOLD_FRAMES)]
        assert all(a > 0.0 for a in alphas)

    def test_peek_reads_the_pick_without_counting_the_frame(self):
        stab = get_label_stabilizer()
        stab.select(['a', 'b', 'c'], 2)
        for _ in range(ABSENT_HOLD_FRAMES + 3):
            assert stab.select(['b', 'c'], 2, advance=False) == {'a', 'b'}
        assert stab.select(['b', 'c'], 2) == {'a', 'b'}, "the hold is still whole"


class TestReadOnlyAndReset:
    def test_current_never_advances(self):
        p = _started()
        p.update(['a'])
        p.update(['a'])
        before = p.current(['a'])
        for _ in range(LABEL_HOLD_FRAMES + 5):
            assert p.current([]) == before

    def test_current_before_any_frame_draws_what_is_eligible(self):
        assert LabelPersistence().current(['a']) == {'a': 1.0}

    def test_reset_clears(self):
        p = _started()
        p.update(['a'])
        p.update(['a'])
        p.reset()
        assert p.current([]) == {}

    def test_the_stabilizer_owns_it_and_reset_label_stability_clears_it(self):
        stab = get_label_stabilizer()
        stab.drawn_labels(['a'])
        assert stab.drawn_labels([], advance=False) == {'a': 1.0}
        reset_label_stability()
        assert stab.persistence.current([]) == {}

    def test_a_reprocess_reads_without_advancing(self):
        stab = get_label_stabilizer()
        stab.drawn_labels(['a'])
        for _ in range(LABEL_HOLD_FRAMES + 5):
            stab.drawn_labels([], advance=False)
        assert stab.drawn_labels([])['a'] == 1.0, "first missing frame, still full"

    def test_forget_drawn_labels_leaves_the_rest_of_the_state(self):
        stab = get_label_stabilizer()
        stab.select(['a', 'b'], 1)
        stab.drawn_labels(['a'])
        forget_drawn_labels()
        assert stab.persistence.current([]) == {}
        assert stab.selection.shown == {'a'}


# ===================================================================
# The renderers draw what is persisted, faded
# ===================================================================

def _model() -> FisheyeModel:
    return FisheyeModel(
        cx=375.0, cy=375.0, a1=215.0, a3=0.0, a5=0.0,
        roll=0.0, axis_alt=90.0, axis_az=0.0,
        rms_residual=1.0, n_matches=50,
        calibrated_at="2024-01-01T00:00:00+00:00",
        image_width=750, image_height=750,
    )


def _high_messier_uid() -> str:
    for obj in get_messier_objects():
        alt, _ = radec_to_altaz(obj['ra_deg'], obj['dec_deg'], LAT, LON, DT,
                                refraction=True)
        if obj.get('label') and float(alt) > 40.0:
            return f"messier:{obj['label']}"
    pytest.skip('no Messier object high up at the test instant')


def _messier_render(persisted):
    img = Image.new('RGBA', (750, 750), (0, 0, 0, 255))
    obstructed = np.zeros((750, 750), dtype=np.uint8)
    out = render_messier(img, _model(), {'enabled': True}, LAT, LON, DT,
                         LabelGrid(750, 750), sky_gray=obstructed, persisted=persisted)
    return np.asarray(out)[..., 0]


class TestRenderers:
    def test_a_held_label_is_drawn_on_an_obstructed_plane(self):
        uid = _high_messier_uid()
        assert _messier_render(None).max() == 0, "control: the plane hides it"
        assert _messier_render({uid: 1.0}).max() > 0

    def test_only_the_persisted_uids_are_drawn(self):
        assert _messier_render({}).max() == 0

    def test_alpha_fades_the_label(self):
        uid = _high_messier_uid()
        full = int(_messier_render({uid: 1.0}).max())
        faint = int(_messier_render({uid: 0.25}).max())
        assert 0 < faint < full

    def test_point_targets_skip_the_visibility_test_when_persisted(self):
        obstructed = np.zeros((750, 750), dtype=np.uint8)
        cfg = {'enabled': True, 'max_magnitude': 2.5}
        open_sky = np.full((750, 750), 255, dtype=np.uint8)
        everything = bright_star_targets(_model(), cfg, LAT, LON, DT, open_sky)
        assert everything, "control: bright stars above the horizon"
        assert bright_star_targets(_model(), cfg, LAT, LON, DT, obstructed) == []
        uid = everything[0][3]
        held = bright_star_targets(_model(), cfg, LAT, LON, DT, obstructed,
                                   persisted={uid: 0.5})
        assert [t[3] for t in held] == [uid]


# ===================================================================
# One horizon for ranking and drawing
# ===================================================================

def _fake_sky(monkeypatch, objects):
    """Messier catalogue of ``objects`` [(label, alt, vmag)], at az 0."""
    alts = {i: alt for i, (_, alt, _) in enumerate(objects)}
    cat = [{'label': label, 'ra_deg': float(i), 'dec_deg': 0.0, 'vmag': mag}
           for i, (label, _, mag) in enumerate(objects)]
    monkeypatch.setattr(label_candidates, 'get_messier_objects', lambda: cat)
    monkeypatch.setattr(label_candidates, 'radec_to_altaz',
                        lambda ra, dec, *a, **k: (alts[int(ra)], 0.0))


CFG = {'top_n': 1, 'planets': {'enabled': False}, 'messier': {'enabled': True}}


class TestHorizon:
    def test_an_object_at_seven_degrees_no_longer_takes_a_slot(self, monkeypatch):
        _fake_sky(monkeypatch, [('M1', 7.0, 1.0), ('M2', 40.0, 5.0)])
        ranked = label_candidates.rank_label_candidates(CFG, _model(), LAT, LON, DT)
        assert ranked == ['messier:M2']
        assert overlay_renderer._select_budget(ranked, CFG) == {'messier:M2'}

    def test_ranked_from_the_same_altitude_the_layers_draw_from(self, monkeypatch):
        _fake_sky(monkeypatch, [('M1', LABEL_MIN_ALT_DEG + 0.5, 1.0),
                                ('M2', LABEL_MIN_ALT_DEG - 0.5, 1.0)])
        ranked = label_candidates.rank_label_candidates(CFG, _model(), LAT, LON, DT)
        assert ranked == ['messier:M1']

    def test_a_layer_switched_off_takes_no_slot(self, monkeypatch):
        _fake_sky(monkeypatch, [('M1', 40.0, 1.0)])
        cfg = dict(CFG, messier={'enabled': False})
        assert label_candidates.rank_label_candidates(cfg, _model(), LAT, LON, DT) == []

    def test_eligible_is_ranked_and_inside_the_budget(self):
        ranked = ['a', 'b', 'c']
        assert label_candidates.eligible_labels(ranked, {'a', 'c', 'held'}) == ['a', 'c']
        assert label_candidates.eligible_labels(ranked, None) == ranked


# ===================================================================
# Renderer end to end
# ===================================================================

def _synthetic_sky(n_stars: int = 60, size: int = 750) -> Image.Image:
    rng = np.random.default_rng(0)
    arr = np.zeros((size, size), dtype=np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    c, r = size / 2.0, size * 0.45
    arr[((xx - c) ** 2 + (yy - c) ** 2) <= r * r] = 70
    arr += rng.normal(0, 2.0, arr.shape)
    for _ in range(n_stars):
        ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0, r * 0.9)
        x, y = c + rad * np.cos(ang), c + rad * np.sin(ang)
        arr += rng.uniform(80, 180) * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / 4.5))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGBA')


def _config(tmp_path) -> dict:
    path = str(tmp_path / 'cal.json')
    _model().save(path)
    return {
        'enabled': True, 'calibration_file': path,
        '_lat': LAT, '_lon': LON, '_obs_utc': DT.isoformat(),
        'top_n': 6, 'grid': {'enabled': False},
        'constellations': {'enabled': False},
        'bright_stars': {'enabled': True, 'max_magnitude': 2.5},
        'messier': {'enabled': True}, 'ngc': {'enabled': False},
        'planets': {'enabled': True, 'colors': {}},
    }


class TestRendererEndToEnd:
    @pytest.mark.slow
    def test_held_labels_are_drawn_until_the_hold_ends(self, tmp_path):
        """The equipment map turns the whole frame to equipment: nothing is
        eligible any more, yet the labels already on screen are drawn for
        the hold — the trade the reporter accepted — then go."""
        from services.allsky.obstruction_map import get_obstruction_map
        config = _config(tmp_path)
        frame = _synthetic_sky()
        base = np.asarray(frame)
        obs_map = get_obstruction_map()
        obs_map.reset()
        try:
            first = overlay_renderer.render_allsky_overlay(frame, config, {})
            assert not np.array_equal(np.asarray(first), base), "control: labels drawn"
            obs_map.update(np.zeros((750, 750), dtype=np.uint8), n_detections=60,
                           frame_is_observable=True,
                           sky_region=np.ones((750, 750), dtype=bool))
            drawn = [not np.array_equal(
                np.asarray(overlay_renderer.render_allsky_overlay(frame, config, {})), base)
                for _ in range(LABEL_HOLD_FRAMES + 1)]
        finally:
            obs_map.reset()
        assert drawn == [True] * LABEL_HOLD_FRAMES + [False]

    @pytest.mark.slow
    def test_a_reprocess_never_advances_the_drawn_labels(self, tmp_path):
        config = _config(tmp_path)
        frame = _synthetic_sky()
        overlay_renderer.render_allsky_overlay(frame, config, {})
        persistence = get_label_stabilizer().persistence
        before = persistence.current([])
        assert before
        again = overlay_renderer.render_allsky_overlay(frame, config, {SAME_CAPTURE_KEY: True})
        assert persistence.current([]) == before
        first = overlay_renderer.render_allsky_overlay(frame, config, {SAME_CAPTURE_KEY: True})
        assert np.array_equal(np.asarray(again), np.asarray(first))

    def test_a_frame_the_gate_withholds_forgets_the_drawn_labels(self, monkeypatch):
        import services.observing_window as observing_window
        stab = get_label_stabilizer()
        stab.drawn_labels(['a'])
        monkeypatch.setattr(observing_window, 'is_observing_window', lambda *a, **k: False)
        img = Image.new('RGB', (32, 32))
        out = overlay_renderer.render_allsky_for_preview(
            img, {'enabled': True, 'calibration_file': 'cal.json'}, {}, {})
        assert out is img
        assert stab.persistence.current([]) == {}
