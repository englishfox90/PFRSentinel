"""
Tests for the persisted all-sky equipment map (services/allsky/obstruction_map.py).

Synthetic frames only: a sky disc with a silhouette of "equipment" cut out of
it. Positive evidence is the sky part of the disc, negative evidence is the
rest of the disc, exactly as the renderer feeds the map.
"""
import time

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.label_stability import vote_grid
from services.allsky.obstruction_map import (
    HEAL_FRAMES, NEGATIVE_MIN_DETECTIONS, POSITIVE_MIN_DETECTIONS, SAVE_INTERVAL_S,
    ObstructionMap, get_obstruction_map,
)

SIZE = 200          # frame edge; grid step 1 so the assertions are exact


@pytest.fixture(autouse=True)
def _fresh_singleton():
    get_obstruction_map().reset()
    yield
    get_obstruction_map().reset()


def _disc(cx, cy, r, size=SIZE):
    yy, xx = np.mgrid[0:size, 0:size]
    return ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r


def _scene(silhouette, size=SIZE):
    """(sky_mask, reach, sky_region) for a disc with ``silhouette`` cut out."""
    region = _disc(size / 2, size / 2, size * 0.45, size)
    sky = region & ~silhouette
    return np.where(sky, 255, 0).astype(np.uint8), sky, region


def _feed(m, silhouette, frames, n=60, exclude=None, observable=True, crop=None):
    mask, reach, region = _scene(silhouette)
    for _ in range(frames):
        m.update(mask, n_detections=n, frame_is_observable=observable,
                 reach_mask=reach, sky_region=region, exclude=exclude, crop=crop)


def _left_block():
    s = np.zeros((SIZE, SIZE), dtype=bool)
    s[:, :70] = True
    return s


def _right_block():
    s = np.zeros((SIZE, SIZE), dtype=bool)
    s[:, 130:] = True
    return s


class TestLearning:
    def test_unknown_map_is_none(self):
        m = ObstructionMap()
        assert m.sky_mask_for(SIZE, SIZE) is None
        assert not m.is_known

    def test_converges_to_the_silhouette_within_heal_frames(self):
        m = ObstructionMap()
        silhouette = _left_block()
        _feed(m, silhouette, HEAL_FRAMES)
        sky = m.sky_mask_for(SIZE, SIZE)
        _, _, region = _scene(silhouette)
        assert not sky[region & silhouette].any(), "equipment inside the disc"
        assert sky[region & ~silhouette].all(), "sky inside the disc"
        assert sky[~region].all(), "outside the disc nothing was watched: sky"

    def test_forgets_a_moved_scope_in_about_twice_heal_frames(self):
        m = ObstructionMap()
        _feed(m, _left_block(), HEAL_FRAMES)
        _feed(m, _right_block(), 2 * HEAL_FRAMES)
        sky = m.sky_mask_for(SIZE, SIZE)
        _, _, region = _scene(_left_block())
        assert sky[region & _left_block()].all(), "the old place is sky again"
        assert not sky[region & _right_block()].any(), "the new place is equipment"

    def test_a_moved_scope_is_not_forgotten_in_a_few_frames(self):
        """The map is a prior: one cloudy-edge evening must not erase it."""
        m = ObstructionMap()
        _feed(m, _left_block(), HEAL_FRAMES)
        _feed(m, _right_block(), HEAL_FRAMES // 10)
        sky = m.sky_mask_for(SIZE, SIZE)
        _, _, region = _scene(_left_block())
        assert not sky[region & _left_block()].any()

    def test_no_detection_frames_leave_the_map_untouched(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 5)
        before = m.sky_probability()
        mask, reach, region = _scene(_left_block())
        m.update(None, n_detections=0, frame_is_observable=True)
        m.update(mask, n_detections=POSITIVE_MIN_DETECTIONS - 1, frame_is_observable=True,
                 reach_mask=reach, sky_region=region)
        assert m.frames_seen == 5
        assert np.array_equal(m.sky_probability(), before)

    def test_unobservable_frames_teach_nothing(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 5, observable=False)
        assert m.frames_seen == 0 and m.sky_mask_for(SIZE, SIZE) is None

    def test_sparse_frames_add_but_never_remove(self):
        """Under NEGATIVE_MIN_DETECTIONS a frame says where the sky is, not
        where it is not: a cloudy frame must not teach equipment."""
        m = ObstructionMap()
        _feed(m, _left_block(), HEAL_FRAMES, n=NEGATIVE_MIN_DETECTIONS - 1)
        sky = m.sky_mask_for(SIZE, SIZE)
        assert sky.all()

    def test_moon_glare_disc_is_excluded_from_negative_evidence(self):
        m = ObstructionMap()
        silhouette = _left_block()
        moon = _disc(130, 100, 15)
        # the glare blanks the discs there: it reads as "no star" in the mask
        mask, reach, region = _scene(silhouette | moon)
        for _ in range(HEAL_FRAMES):
            m.update(mask, n_detections=60, frame_is_observable=True,
                     reach_mask=reach, sky_region=region, exclude=moon)
        sky = m.sky_mask_for(SIZE, SIZE)
        assert sky[moon].all(), "the Moon's patch of sky is not equipment"
        assert not sky[region & silhouette].any()

    def test_reach_keeps_the_gap_between_stars_out_of_the_negative(self):
        """A pixel within two disc radii of a detection was not watched."""
        m = ObstructionMap()
        region = _disc(100, 100, 90)
        star = _disc(100, 100, 10)
        reach = _disc(100, 100, 20)
        mask = np.where(star, 255, 0).astype(np.uint8)
        for _ in range(10):
            m.update(mask, n_detections=60, frame_is_observable=True,
                     reach_mask=reach, sky_region=region)
        sky = m.sky_mask_for(SIZE, SIZE)
        assert sky[100, 115], "inside the reach: unknown, so sky"
        assert not sky[100, 140], "beyond the reach, inside the disc: equipment"

    def test_reset_forgets_everything(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 3)
        m.reset()
        assert m.sky_mask_for(SIZE, SIZE) is None and m.frames_seen == 0


class TestStamps:
    def test_a_different_frame_size_reads_none(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 3)
        assert m.sky_mask_for(SIZE + 10, SIZE) is None

    def test_a_different_crop_reads_none(self):
        """A crop moved 10 px at the same size puts the equipment elsewhere;
        the read API must not serve the old map for it."""
        m = ObstructionMap()
        crop = (0, 0, SIZE, SIZE, 400, 400)
        _feed(m, _left_block(), 3, crop=crop)
        assert m.sky_mask_for(SIZE, SIZE, crop) is not None
        assert m.sky_mask_for(SIZE, SIZE, (10, 0, SIZE, SIZE, 400, 400)) is None
        assert m.sky_mask_for(SIZE, SIZE) is None, "None means uncropped"
        assert m.small_sky_mask(SIZE, SIZE) is None

    def test_full_resolution_mask_is_read_only(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 3)
        sky = m.sky_mask_for(SIZE, SIZE)
        with pytest.raises(ValueError):
            sky[0, 0] = True

    def test_a_different_crop_starts_a_new_map(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 3, crop=(0, 0, SIZE, SIZE, 400, 400))
        _feed(m, _left_block(), 1, crop=(10, 0, SIZE, SIZE, 400, 400))
        assert m.frames_seen == 1

    def test_a_different_size_starts_a_new_map(self):
        m = ObstructionMap()
        _feed(m, _left_block(), 3)
        big = np.zeros((SIZE + 10, SIZE + 10), dtype=np.uint8)
        big[:, 100:] = 255
        m.update(big, n_detections=20, frame_is_observable=True)
        assert m.frames_seen == 1 and m.sky_mask_for(SIZE, SIZE) is None

    def test_large_frames_live_on_the_grid(self):
        m = ObstructionMap()
        h, w = 3552, 3552
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[:, w // 2:] = 255
        m.update(mask, n_detections=20, frame_is_observable=True)
        _, grid = vote_grid((h, w))
        assert m.sky_probability().shape == grid
        assert m.sky_probability().nbytes <= 512 * 512 * 4
        sky = m.sky_mask_for(w, h)
        assert sky.shape == (h, w) and sky[100, 3000] and sky[100, 100]

    def test_grid_planes_are_accepted_as_is(self):
        m = ObstructionMap()
        full_shape = (3552, 3552)
        _, grid = vote_grid(full_shape)
        small = np.zeros(grid, dtype=np.uint8)
        small[:, grid[1] // 2:] = 255
        region = np.ones(grid, dtype=bool)
        m.update(small, n_detections=60, frame_is_observable=True,
                 full_shape=full_shape, reach_mask=small > 0, sky_region=region)
        sky = m.sky_mask_for(3552, 3552)
        assert sky[100, 3000] and not sky[100, 100]


class TestPersistence:
    def test_round_trip(self, tmp_path):
        m = ObstructionMap()
        _feed(m, _left_block(), 7, crop=(5, 6, SIZE, SIZE, 300, 300))
        path = str(tmp_path / 'map.npz')
        assert m.save(path)
        other = ObstructionMap()
        assert other.load(path)
        assert other.frames_seen == 7
        crop = (5, 6, SIZE, SIZE, 300, 300)
        assert other.stamp == (SIZE, SIZE, crop)
        assert np.array_equal(other.sky_mask_for(SIZE, SIZE, crop),
                              m.sky_mask_for(SIZE, SIZE, crop))

    def test_loaded_map_for_another_frame_size_is_discarded_on_use(self, tmp_path):
        m = ObstructionMap()
        _feed(m, _left_block(), 7)
        path = str(tmp_path / 'map.npz')
        m.save(path)
        other = ObstructionMap()
        other.load(path)
        assert other.sky_mask_for(SIZE + 2, SIZE + 2) is None
        big = np.zeros((SIZE + 2, SIZE + 2), dtype=np.uint8)
        big[:, 100:] = 255
        other.update(big, n_detections=20, frame_is_observable=True)
        assert other.frames_seen == 1 and other.stamp[0] == SIZE + 2

    def test_truncated_file_loads_as_unknown_without_raising(self, tmp_path):
        """np.load raises zipfile.BadZipFile on a cut-off npz; load() runs
        unguarded at startup, so it must swallow anything."""
        m = ObstructionMap()
        _feed(m, _left_block(), 3)
        path = tmp_path / 'map.npz'
        m.save(str(path))
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        other = ObstructionMap()
        assert not other.load(str(path))
        assert other.sky_mask_for(SIZE, SIZE) is None

    def test_concurrent_saves_leave_one_valid_file(self, tmp_path):
        import threading
        m = ObstructionMap()
        _feed(m, _left_block(), 3)
        path = str(tmp_path / 'map.npz')
        errors = []

        def writer():
            try:
                for _ in range(15):
                    _feed(m, _left_block(), 1)
                    assert m.save(path)
            except Exception as e:      # pragma: no cover - reported below
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert sorted(os.listdir(tmp_path)) == ['map.npz'], "no temp files left"
        other = ObstructionMap()
        assert other.load(path) and other.frames_seen >= 3

    def test_an_update_during_a_write_is_not_marked_saved(self, tmp_path, monkeypatch):
        m = ObstructionMap()
        _feed(m, _left_block(), 1)
        path = str(tmp_path / 'map.npz')
        real = m._write_atomic

        def slow_write(*args):
            real(*args)
            _feed(m, _left_block(), 1)          # lands while the file is being written

        monkeypatch.setattr(m, '_write_atomic', slow_write)
        assert m.save(path)
        monkeypatch.setattr(m, '_write_atomic', real)
        assert m.save_if_dirty(path), "the late update still needs saving"
        assert not m.save_if_dirty(path)

    def test_missing_and_corrupt_files_are_ignored(self, tmp_path):
        m = ObstructionMap()
        assert not m.load(str(tmp_path / 'absent.npz'))
        bad = tmp_path / 'bad.npz'
        bad.write_bytes(b'not an npz')
        assert not m.load(str(bad))
        np.savez(str(tmp_path / 'foreign.npz'), version=99, plane=np.zeros((2, 2)))
        assert not m.load(str(tmp_path / 'foreign.npz'))
        assert m.sky_mask_for(SIZE, SIZE) is None

    def test_save_is_throttled_to_the_interval(self, tmp_path):
        m = ObstructionMap()
        path = str(tmp_path / 'map.npz')
        _feed(m, _left_block(), 1)
        assert m.maybe_save(path, now=0.0)
        _feed(m, _left_block(), 1)
        assert not m.maybe_save(path, now=SAVE_INTERVAL_S - 1)
        assert m.maybe_save(path, now=SAVE_INTERVAL_S)
        assert not m.maybe_save(path, now=SAVE_INTERVAL_S * 3), "nothing changed"

    def test_save_if_dirty_after_reset_removes_the_file(self, tmp_path):
        m = ObstructionMap()
        path = str(tmp_path / 'map.npz')
        _feed(m, _left_block(), 1)
        m.save(path)
        m.reset()
        assert m.save_if_dirty(path)
        assert not os.path.exists(path)


class TestBudget:
    def test_full_resolution_mask_is_cached_until_the_map_changes(self):
        m = ObstructionMap()
        _feed(m, _left_block(), HEAL_FRAMES)
        first = m.sky_mask_for(SIZE, SIZE)
        assert m.sky_mask_for(SIZE, SIZE) is first
        _feed(m, _left_block(), 1)            # same verdict, tiny EMA move
        assert m.sky_mask_for(SIZE, SIZE) is first
        _feed(m, _right_block(), 2 * HEAL_FRAMES)
        assert m.sky_mask_for(SIZE, SIZE) is not first

    @pytest.mark.slow
    def test_update_at_full_sensor_resolution_stays_cheap(self):
        """Plan §8: the map update runs on the capture path with a 10 ms
        budget at 3552 px. Measured idle on the development container:
        p50 2.15 ms, p95 6.8 ms (grid planes as the renderer builds them).
        The assertion is a loose 5x ceiling so scheduler noise on a loaded
        xdist runner never fails it and a real regression still does; the
        budget itself is checked from the logged numbers."""
        from services.logger import app_logger
        m = ObstructionMap()
        full_shape = (3552, 3552)
        _, grid = vote_grid(full_shape)
        rng = np.random.default_rng(1)
        mask = np.where(rng.random(grid) > 0.5, 255, 0).astype(np.uint8)
        reach = mask > 0
        region = np.ones(grid, dtype=bool)
        moon = np.zeros(grid, dtype=bool)
        moon[200:260, 200:260] = True
        kw = dict(n_detections=60, frame_is_observable=True, full_shape=full_shape,
                  reach_mask=reach, sky_region=region, exclude=moon)
        m.update(mask, **kw)                     # allocates the planes
        samples = []
        for _ in range(50):
            t0 = time.perf_counter()
            m.update(mask, **kw)
            samples.append((time.perf_counter() - t0) * 1000.0)
        p50, p95 = np.percentile(samples, 50), np.percentile(samples, 95)
        app_logger.info(f"ObstructionMap.update at 3552 px: p50={p50:.2f} ms p95={p95:.2f} ms")
        assert p50 < 50.0, f"p50 {p50:.2f} ms is over 5x the 10 ms budget"
