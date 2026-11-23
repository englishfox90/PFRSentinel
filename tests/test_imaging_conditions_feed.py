"""ImagingConditionsFeed + ConditionsStore — the per-frame feed end to end on
an injected clock: hysteresis both ways, starts not-OK, stale after a gap
(and starts over), the local baseline learned per Moon bin (cold start
explicit, reset on a new camera / frame size / lens), change % fields, FWHM
in arcsec only with a calibration, reprocesses and disabled configs, the
pipeline hook never raising, and a per-frame bookkeeping budget."""
import time

import pytest

from services.imaging_conditions import ConditionsStore, ImagingConditionsFeed
from services.imaging_conditions.feed import observe_imaging_conditions
from services.imaging_conditions.plate_scale import ARCSEC_PER_RADIAN, PlateScale
from services.imaging_conditions.star_baseline import StarBaseline
from services.observing_window import SAME_CAPTURE_KEY
from services.sky_evidence import EVIDENCE_KEY

FRAME = (3552, 3552)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class NoCalibration(PlateScale):
    def lens_a1(self, w, h):
        return None


class FixedCalibration(PlateScale):
    def __init__(self, a1_full_frame):
        super().__init__()
        self._a1 = a1_full_frame

    def lens_a1(self, w, h):
        return self._a1


def make_feed(tmp_path, *, sun=-30.0, moon=None, plate=None, clock=None):
    clock = clock or Clock()
    store = ConditionsStore(clock=clock)
    sun_box = {'alt': sun}
    feed = ImagingConditionsFeed(
        store=store, baseline=StarBaseline(str(tmp_path / 'b.json'), clock=clock),
        plate_scale=plate or NoCalibration(), sun_alt_fn=lambda c: sun_box['alt'],
        moon_fn=lambda: moon if moon is not None else {'moon_is_up': False,
                                                         'illumination_pct': 2.0},
        clock=clock)
    return feed, store, clock, sun_box


def frame(count=300, roof='Open', sky='Clear', fwhm='3.0', detections='150', camera='ZWO ASI676MC'):
    return {
        '_ML_RESULTS': {'roof_status': roof, 'roof_confidence': 0.95,
                        'sky_condition': sky, 'sky_confidence': 0.9,
                        'stars_visible': count is None or count > 0},
        EVIDENCE_KEY: {'star_count': count}, 'FWHM': fwhm, 'STAR_COUNT': detections,
        'CAMERA': camera,
    }


CFG = {'imaging_conditions': {}}


def feed_frames(feed, clock, n, *, step=30.0, cfg=CFG, size=FRAME, **kw):
    for _ in range(n):
        clock.t += step
        feed.observe(frame(**kw), cfg, size)


def test_starts_not_ok_and_warms_up(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    snap = store.snapshot()
    assert snap['imaging_ok'] is False and snap['reasons'] == ['warming_up']
    assert snap['updated_at'] is None and snap['changed_at'] is None
    feed_frames(feed, clock, 2)
    snap = store.snapshot()
    assert snap['imaging_ok'] is False and snap['reasons'] == ['warming_up']
    assert snap['frame_ok'] is True and snap['good_streak'] == 2
    feed_frames(feed, clock, 1)
    snap = store.snapshot()
    assert snap['imaging_ok'] is True and snap['reasons'] == []
    assert snap['changed_at'] is not None and snap['frames_considered'] == 3


def test_hysteresis_rides_out_bad_frames_then_pauses_then_recovers(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 3)
    feed_frames(feed, clock, 2, roof='Closed')
    snap = store.snapshot()
    assert snap['imaging_ok'] is True and snap['reasons'] == ['roof_not_open']
    assert snap['bad_streak'] == 2
    feed_frames(feed, clock, 1)          # a good frame breaks the run
    feed_frames(feed, clock, 3, roof='Closed')
    assert store.snapshot()['imaging_ok'] is False
    feed_frames(feed, clock, 2)
    snap = store.snapshot()
    assert snap['imaging_ok'] is False and snap['reasons'] == ['recovering']
    feed_frames(feed, clock, 1)
    assert store.snapshot()['imaging_ok'] is True


def test_configured_frame_counts(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    cfg = {'imaging_conditions': {'good_frames': 1, 'bad_frames': 1}}
    feed_frames(feed, clock, 1, cfg=cfg)
    assert store.snapshot()['imaging_ok'] is True
    feed_frames(feed, clock, 1, cfg=cfg, sky='Overcast')
    snap = store.snapshot()
    assert snap['imaging_ok'] is False and 'cloud_cover_high' in snap['reasons']


def test_goes_stale_after_auto_limit_and_starts_over(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 5, step=60.0)
    snap = store.snapshot()
    assert snap['imaging_ok'] is True and snap['stale_after_s'] == 180.0
    clock.t += 179
    assert store.snapshot()['stale'] is False
    clock.t += 2
    snap = store.snapshot()
    assert snap['stale'] is True and snap['imaging_ok'] is False and snap['reasons'] == ['stale']
    assert snap['changed_at'] is not None
    assert store.compact() == {'imaging_ok': False, 'reasons': ['stale'],
                               'changed_at': snap['changed_at'], 'stale': True}
    feed_frames(feed, clock, 1, step=0.0)
    snap = store.snapshot()
    assert snap['stale'] is False and snap['imaging_ok'] is False
    assert snap['reasons'] == ['warming_up'] and snap['frames_considered'] == 1


def test_configured_stale_limit(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 3, cfg={'imaging_conditions': {'stale_after_s': 600}})
    clock.t += 500
    assert store.snapshot()['stale'] is False
    clock.t += 200
    assert store.snapshot()['stale'] is True


def test_disabled_reports_disabled_and_feeds_nothing(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 3)
    feed_frames(feed, clock, 1, cfg={'imaging_conditions': {'enabled': False}})
    snap = store.snapshot()
    assert snap['enabled'] is False and snap['imaging_ok'] is False
    assert snap['reasons'] == ['disabled'] and snap['updated_at'] is None


def test_reprocess_of_the_same_capture_is_not_counted(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    md = frame()
    md[SAME_CAPTURE_KEY] = True
    assert feed.observe(md, CFG, FRAME) is None
    assert store.snapshot()['frames_considered'] == 0


def test_daylight_and_twilight_setting(tmp_path):
    feed, store, clock, sun = make_feed(tmp_path, sun=-14.0)
    feed_frames(feed, clock, 3)
    assert store.snapshot()['reasons'] == ['daylight']
    feed_frames(feed, clock, 3, cfg={'imaging_conditions': {'max_sun_alt_deg': -12}})
    assert store.snapshot()['imaging_ok'] is True


def test_baseline_cold_start_then_ready_per_moon_bin(tmp_path):
    moon = {'moon_is_up': False, 'illumination_pct': 2.0}
    feed, store, clock, _ = make_feed(tmp_path, moon=moon)
    cfg = {'imaging_conditions': {'baseline_min_samples': 5}}
    feed_frames(feed, clock, 4, cfg=cfg, count=300)
    snap = store.snapshot()
    assert snap['stars']['baseline'] is None and snap['stars']['ratio'] is None
    assert snap['cloud_cover_source'] == 'ml_class'
    assert snap['baseline_status'] == {'state': 'learning', 'samples': 4, 'needed': 5,
                                       'bin': 'moon_down'}
    feed_frames(feed, clock, 1, cfg=cfg, count=300)
    feed_frames(feed, clock, 1, cfg=cfg, count=150, sky='Partly Cloudy')
    snap = store.snapshot()
    assert snap['baseline_status']['state'] == 'ready'
    assert snap['stars']['baseline'] == 300.0 and snap['stars']['ratio'] == 0.5
    assert snap['cloud_cover_pct'] == 50.0 and snap['cloud_cover_source'] == 'star_baseline'
    # A Moon-up night has its own bin and starts learning again.
    moon.update(moon_is_up=True, illumination_pct=80.0)
    feed_frames(feed, clock, 1, cfg=cfg, count=120)
    snap = store.snapshot()
    assert snap['stars']['baseline'] is None and snap['baseline_bin'] == 'moon_up_75_100'
    assert snap['baseline_status']['state'] == 'learning'


def test_cloudy_or_closed_frames_never_teach_the_baseline(tmp_path):
    feed, _, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 3, sky='Overcast')
    feed_frames(feed, clock, 3, roof='Closed')
    assert feed.baseline.sample_count('moon_down') == 0


def test_twilight_frames_never_teach_the_baseline(tmp_path):
    feed, _, clock, _ = make_feed(tmp_path, sun=-15.0)
    feed_frames(feed, clock, 3, cfg={'imaging_conditions': {'max_sun_alt_deg': -12}})
    assert feed.baseline.sample_count('moon_down') == 0


@pytest.mark.parametrize("change", [{'camera': 'Other camera'}, {'size': (1920, 1080)}])
def test_new_camera_or_frame_size_starts_the_baseline_over(tmp_path, change):
    feed, _, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 4)
    assert feed.baseline.sample_count('moon_down') == 4
    clock.t += 30
    md = frame(camera=change.get('camera', 'ZWO ASI676MC'))
    feed.observe(md, CFG, change.get('size', FRAME))
    assert feed.baseline.sample_count('moon_down') == 1


def test_lens_change_starts_over_but_a_refinement_or_first_calibration_does_not(tmp_path):
    plate = FixedCalibration(None)
    feed, _, clock, _ = make_feed(tmp_path, plate=plate)
    feed_frames(feed, clock, 3)
    plate._a1 = 1100.0          # first calibration: adopted, kept
    feed_frames(feed, clock, 1)
    assert feed.baseline.sample_count('moon_down') == 4
    plate._a1 = 1104.0          # a refinement
    feed_frames(feed, clock, 1)
    assert feed.baseline.sample_count('moon_down') == 5
    plate._a1 = 900.0           # a different lens
    feed_frames(feed, clock, 1)
    assert feed.baseline.sample_count('moon_down') == 1


def test_change_pct_and_window(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 10, count=300)
    feed_frames(feed, clock, 1, count=165)
    stars = store.snapshot()['stars']
    assert stars['change_pct'] == -45.0 and stars['trend'] == -0.45
    assert stars['window_frames'] == 10
    assert 'stars_dropping' in store.snapshot()['reasons']


def test_fwhm_change_against_the_night_median(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 10, fwhm='3.0')
    feed_frames(feed, clock, 1, fwhm='4.8')
    snap = store.snapshot()
    assert snap['fwhm']['night_median_px'] == 3.0 and snap['fwhm']['change_pct'] == 60.0
    assert 'fwhm_high' in snap['reasons']


def test_fwhm_arcsec_only_with_a_calibration(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path)
    feed_frames(feed, clock, 1, fwhm='3.0')
    assert store.snapshot()['fwhm'] == {'px': 3.0, 'arcsec': None, 'night_median_px': None,
                                        'change_pct': None}
    feed, store, clock, _ = make_feed(tmp_path, plate=FixedCalibration(1100.0))
    feed_frames(feed, clock, 1, fwhm='3.0')
    assert store.snapshot()['fwhm']['arcsec'] == round(3.0 * ARCSEC_PER_RADIAN / 1100.0, 1)


def test_plate_scale_from_a_real_calibration_file(tmp_path):
    from services.allsky.fisheye import FisheyeModel
    path = tmp_path / 'cal.json'
    FisheyeModel(a1=550.0, n_matches=50, image_width=1776, image_height=1776).save(str(path))
    plate = PlateScale(path_fn=lambda: str(path))
    # Calibrated on a 50 % resize: the full frame's a1 is twice as large.
    assert plate.lens_a1(3552, 3552) == pytest.approx(1100.0)
    assert plate.arcsec_per_px(3552, 3552) == pytest.approx(ARCSEC_PER_RADIAN / 1100.0)
    assert plate.arcsec_per_px(3552, 2000) is None          # another shape: a crop
    assert PlateScale(path_fn=lambda: str(tmp_path / 'none.json')).arcsec_per_px(10, 10) is None
    (tmp_path / 'bad.json').write_text('{not json')
    assert PlateScale(path_fn=lambda: str(tmp_path / 'bad.json')).arcsec_per_px(10, 10) is None
    FisheyeModel(a1=550.0, n_matches=50).save(str(tmp_path / 'nosize.json'))
    assert PlateScale(path_fn=lambda: str(tmp_path / 'nosize.json')).arcsec_per_px(10, 10) is None


def test_pipeline_hook_never_raises(monkeypatch):
    from services.imaging_conditions import feed as feed_mod

    class Boom:
        def observe(self, *a):
            raise RuntimeError("boom")

    monkeypatch.setattr(feed_mod, 'get_imaging_conditions_feed', lambda: Boom())
    observe_imaging_conditions({}, CFG, FRAME)


def test_pipeline_hook_publishes_to_the_shared_store():
    from services.imaging_conditions import get_conditions_store
    observe_imaging_conditions(frame(), {'imaging_conditions': {}, 'weather': {}}, FRAME)
    assert get_conditions_store().snapshot()['frames_considered'] == 1


def test_processor_watch_mode_feeds_conditions(tmp_path):
    from PIL import Image
    from services.config import Config
    from services.imaging_conditions import get_conditions_store
    from services.processor import process_image
    cfg = Config(str(tmp_path / 'config.json'))
    cfg.set('output_directory', str(tmp_path / 'out'))
    src = tmp_path / 'frame.png'
    Image.new('RGB', (64, 48), (5, 5, 5)).save(src)
    process_image(str(src), cfg)
    assert get_conditions_store().snapshot()['frames_considered'] == 1


@pytest.mark.slow
def test_per_frame_cost_is_bookkeeping_only(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path, plate=FixedCalibration(1100.0))
    durations = []
    for i in range(300):
        clock.t += 30
        md = frame(count=280 + i % 40, fwhm=f"{3.0 + (i % 5) * 0.1:.1f}")
        start = time.perf_counter()
        feed.observe(md, CFG, FRAME)
        durations.append(time.perf_counter() - start)
    durations.sort()
    p50, p95 = durations[150], durations[285]
    from services.logger import app_logger
    app_logger.info(f"imaging conditions per frame: p50 {p50 * 1e3:.2f} ms, p95 {p95 * 1e3:.2f} ms")
    # Measured ~0.1 ms; a 5 ms ceiling only catches an accidental image pass.
    assert p95 < 0.005
