"""target_moon + the feed — NINA's target and the Moon: the separation in
the equinox of date, the target's altitude only with a site, the same
staleness as the all-sky overlay, a caution distance that grows with the
Moon's illumination, the record's ``target`` block and ``moon.altitude_deg``,
the baseline bin following the Moon's altitude with hysteresis — and
``imaging_ok`` never depending on any of it."""
from datetime import datetime, timezone

import pytest

from services import moon_position as mp
from services import nina_target_store as nts
from services.imaging_conditions import ConditionsStore, ImagingConditionsFeed
from services.imaging_conditions import target_moon as tm
from services.imaging_conditions.frame_grade import summarize_record
from services.imaging_conditions.plate_scale import PlateScale
from services.imaging_conditions.star_baseline import StarBaseline

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
LAT, LON = 31.55, -100.46
SITE = {'latitude': str(LAT), 'longitude': str(LON)}
FRAME = (1000, 1000)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class NoCalibration(PlateScale):
    def lens_a1(self, w, h):
        return None


def target_near_moon(offset_deg=18.0, name='M 15'):
    """A J2000-ish target ``offset_deg`` north of the Moon (precession is
    well under the tolerances used here)."""
    moon = mp.moon_position(LAT, LON, NOW)
    return {'name': name, 'ra_deg': moon['ra_deg'], 'dec_deg': moon['dec_deg'] + offset_deg}


def good_frame():
    return {'_ML_RESULTS': {'roof_status': 'Open', 'roof_confidence': 0.95,
                            'sky_condition': 'Clear', 'sky_confidence': 0.9,
                            'stars_visible': True},
            'STAR_COUNT': '300', 'FWHM': '3.0', 'CAMERA': 'cam'}


def make_feed(tmp_path, moon, target=None):
    clock = Clock()
    store = ConditionsStore(clock=clock)
    box = {'moon': moon, 'target': target}
    feed = ImagingConditionsFeed(
        store=store, baseline=StarBaseline(str(tmp_path / 'b.json'), clock=clock),
        plate_scale=NoCalibration(), sun_alt_fn=lambda c: -30.0,
        moon_fn=lambda: box['moon'], target_fn=lambda c: box['target'],
        clock=clock, wall=lambda: NOW)
    return feed, store, clock, box


# -- geometry ----------------------------------------------------------------

def test_block_gives_separation_and_altitude():
    block = tm.target_block(target_near_moon(18.0), NOW, LAT, LON)
    assert block['name'] == 'M 15'
    assert block['moon_separation_deg'] == pytest.approx(18.0, abs=0.6)
    assert -90.0 <= block['altitude_deg'] <= 90.0


def test_without_a_site_the_separation_stands_and_the_altitude_is_unknown():
    block = tm.target_block(target_near_moon(40.0), NOW)
    assert block['moon_separation_deg'] == pytest.approx(40.0, abs=1.6)  # Moon's parallax
    assert block['altitude_deg'] is None


@pytest.mark.parametrize("target", [None, 'M 15', {}, {'name': 'x'},
                                    {'name': 'x', 'ra_deg': 'a', 'dec_deg': 1},
                                    {'name': 'x', 'ra_deg': float('nan'), 'dec_deg': 1}])
def test_unusable_target_is_no_block_and_never_raises(target):
    assert tm.target_block(target, NOW, LAT, LON) is None


def test_name_is_bounded():
    block = tm.target_block(dict(target_near_moon(), name='n' * 500), NOW, LAT, LON)
    assert len(block['name']) == 64


# -- caution distance ----------------------------------------------------------

def test_caution_distance_grows_with_the_moon():
    assert tm.caution_separation_deg(0) == tm.CAUTION_SEPARATION_NEW_DEG
    assert tm.caution_separation_deg(100) == tm.CAUTION_SEPARATION_FULL_DEG
    assert tm.caution_separation_deg(20) < tm.caution_separation_deg(87)
    assert tm.caution_separation_deg(250) == tm.CAUTION_SEPARATION_FULL_DEG
    assert tm.caution_separation_deg(None) is None
    assert tm.caution_separation_deg(float('nan')) is None


def test_close_only_with_the_moon_known_to_be_up():
    assert tm.is_close_to_moon(18.0, 87.0, True) is True
    assert tm.is_close_to_moon(70.0, 87.0, True) is False
    assert tm.is_close_to_moon(18.0, 5.0, True) is True     # under the thin-Moon line too
    assert tm.is_close_to_moon(25.0, 5.0, True) is False
    assert tm.is_close_to_moon(18.0, 87.0, False) is False  # Moon down
    assert tm.is_close_to_moon(18.0, 87.0, None) is False   # no site
    assert tm.is_close_to_moon(None, 87.0, True) is False


# -- freshness: the overlay's own rule ---------------------------------------

@pytest.fixture
def target_store(monkeypatch):
    clock = Clock()
    store = nts.NinaTargetStore(monotonic=clock)
    monkeypatch.setattr(nts, 'get_nina_target_store', lambda: store)
    return store, clock


def test_fresh_target_follows_the_overlays_stale_setting(target_store):
    store, clock = target_store
    assert tm.fresh_target({}) is None
    store.set(nts.NinaTarget(name='M 15', ra_deg=322.5, dec_deg=12.2))
    assert tm.fresh_target({})['name'] == 'M 15'
    clock.t += 119
    assert tm.fresh_target({}) is not None
    clock.t += 2                                    # past the default 120 s
    assert tm.fresh_target({}) is None
    cfg = {'allsky_overlay': {'nina_target': {'stale_after_s': 600}}}
    assert tm.fresh_target(cfg)['name'] == 'M 15'
    store.set(None)
    assert tm.fresh_target(cfg) is None


def test_a_failing_store_is_no_target(monkeypatch):
    def boom():
        raise RuntimeError("store gone")
    monkeypatch.setattr(nts, 'get_nina_target_store', boom)
    assert tm.fresh_target({}) is None


def test_site_from_config():
    assert tm.site_from_config({'weather': SITE}) == (LAT, LON)
    assert tm.site_from_config({'weather': {'latitude': '31.5'}}) == (None, None)
    assert tm.site_from_config({}) == (None, None)


# -- the feed ------------------------------------------------------------------

BRIGHT_HIGH = {'moon_is_up': True, 'illumination_pct': 87.0, 'altitude_deg': 62.3}


def test_record_carries_the_target_and_the_moons_altitude(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path, BRIGHT_HIGH, target_near_moon(18.0))
    record = feed.observe(good_frame(), {'weather': SITE}, FRAME)
    assert record['moon'] == {'up': True, 'illumination_pct': 87.0, 'altitude_deg': 62.3}
    assert record['baseline_bin'] == 'moon_up_75_100_high'
    assert record['target']['name'] == 'M 15'
    assert record['target']['moon_separation_deg'] == pytest.approx(18.0, abs=0.6)
    assert record['target']['altitude_deg'] is not None
    assert store.snapshot()['target'] == record['target']
    assert summarize_record(record)['moon_separation_deg'] == \
        record['target']['moon_separation_deg']


def test_a_target_on_top_of_a_bright_moon_never_changes_the_verdict(tmp_path):
    feed, store, clock, box = make_feed(tmp_path, BRIGHT_HIGH, target_near_moon(1.0))
    record = feed.observe(good_frame(), {'weather': SITE}, FRAME)
    assert record['target']['moon_separation_deg'] < 2.0
    assert record['imaging_ok'] is True and record['frame_ok'] is True
    assert record['reasons'] == []


def test_no_target_is_a_null_block(tmp_path):
    feed, store, clock, _ = make_feed(tmp_path, BRIGHT_HIGH, None)
    record = feed.observe(good_frame(), {'weather': SITE}, FRAME)
    assert record['target'] is None
    assert summarize_record(record)['moon_separation_deg'] is None


def test_a_failing_target_source_costs_only_the_block(tmp_path, monkeypatch):
    feed, store, clock, box = make_feed(tmp_path, BRIGHT_HIGH, {'name': 'x'})
    record = feed.observe(good_frame(), {'weather': SITE}, FRAME)
    assert record['target'] is None and record['imaging_ok'] is True


def test_no_site_keeps_working(tmp_path):
    moon = {'illumination_pct': 87.0}            # no site: no up / down, no altitude
    feed, store, clock, _ = make_feed(tmp_path, moon, target_near_moon(30.0))
    record = feed.observe(good_frame(), {}, FRAME)
    assert record['baseline_bin'] == 'moon_unknown'
    assert record['moon'] == {'up': None, 'illumination_pct': 87.0, 'altitude_deg': None}
    assert record['target']['altitude_deg'] is None
    assert record['target']['moon_separation_deg'] == pytest.approx(30.0, abs=1.6)


def test_bright_moon_without_an_altitude_reads_the_unsplit_bin(tmp_path):
    feed, *_ = make_feed(tmp_path, {'moon_is_up': True, 'illumination_pct': 87.0})
    assert feed.observe(good_frame(), {}, FRAME)['baseline_bin'] == 'moon_up_75_100'


def test_bin_follows_the_altitude_with_hysteresis(tmp_path):
    moon = {'moon_is_up': True, 'illumination_pct': 87.0, 'altitude_deg': 28.0}
    feed, store, clock, box = make_feed(tmp_path, moon)
    seen = []
    for altitude in (28.0, 30.5, 31.5, 33.5, 31.0, 28.0, 26.0):
        box['moon'] = dict(moon, altitude_deg=altitude)
        clock.t += 30
        seen.append(feed.observe(good_frame(), {}, FRAME)['baseline_bin'])
    low, high = 'moon_up_75_100_low', 'moon_up_75_100_high'
    assert seen == [low, low, low, high, high, high, low]


def test_low_and_high_moons_learn_separate_baselines(tmp_path):
    cfg = {'imaging_conditions': {'baseline_min_samples': 3}}
    moon = {'moon_is_up': True, 'illumination_pct': 90.0, 'altitude_deg': 12.0}
    feed, store, clock, box = make_feed(tmp_path, moon)
    for _ in range(4):
        clock.t += 30
        low = feed.observe(dict(good_frame(), STAR_COUNT='260'), cfg, FRAME)
    assert low['baseline_status']['state'] == 'ready' and low['stars']['baseline'] == 260
    box['moon'] = dict(moon, altitude_deg=65.0)
    clock.t += 30
    high = feed.observe(dict(good_frame(), STAR_COUNT='120'), cfg, FRAME)
    # The washed-out high-Moon count is not judged against the low-Moon sky.
    assert high['baseline_bin'] == 'moon_up_75_100_high'
    assert high['stars']['baseline'] is None and high['stars']['ratio'] is None
    assert not {'stars_low', 'cloud_cover_high'} & set(high['reasons'])
    assert high['baseline_status']['state'] == 'learning'


def test_a_thin_moon_is_one_bin_at_any_altitude(tmp_path):
    moon = {'moon_is_up': True, 'illumination_pct': 20.0, 'altitude_deg': 10.0}
    feed, store, clock, box = make_feed(tmp_path, moon)
    first = feed.observe(good_frame(), {}, FRAME)['baseline_bin']
    box['moon'] = dict(moon, altitude_deg=70.0)
    clock.t += 30
    assert feed.observe(good_frame(), {}, FRAME)['baseline_bin'] == first == 'moon_up_0_25'
