"""session_state + the feed's restart behaviour: the streaks, star counts,
FWHM readings and frame intervals saved by one run are adopted by the next
while they are younger than the staleness limit, and ignored after it."""
import json
from datetime import datetime, timedelta, timezone

from services.imaging_conditions import ConditionsStore, ImagingConditionsFeed
from services.imaging_conditions.plate_scale import PlateScale
from services.imaging_conditions.session_state import SessionState
from services.imaging_conditions.star_baseline import StarBaseline
from services.imaging_conditions.verdict_hysteresis import VerdictHysteresis

T0 = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
CFG = {'imaging_conditions': {}}
FRAME = (3552, 3552)


class NoCalibration(PlateScale):
    def lens_a1(self, w, h):
        return None


class Run:
    """One app run: its own feed and clocks, sharing the files on disk."""

    def __init__(self, tmp_path, start: datetime):
        self.mono = 50.0
        self.now = start
        self.store = ConditionsStore(clock=lambda: self.mono)
        self.session = SessionState(str(tmp_path / 'session_state.json'))
        self.feed = ImagingConditionsFeed(
            store=self.store, plate_scale=NoCalibration(), session_state=self.session,
            baseline=StarBaseline(str(tmp_path / 'b.json'), clock=lambda: self.mono),
            sun_alt_fn=lambda c: -30.0,
            moon_fn=lambda: {'moon_is_up': False, 'illumination_pct': 2.0},
            clock=lambda: self.mono, wall=lambda: self.now)

    def frames(self, n, *, step=30.0, count=300, roof='Open', fwhm='3.0', cfg=CFG):
        for _ in range(n):
            self.mono += step
            self.now += timedelta(seconds=step)
            self.feed.observe({
                '_ML_RESULTS': {'roof_status': roof, 'roof_confidence': 0.95,
                                'sky_condition': 'Clear', 'sky_confidence': 0.9,
                                'stars_visible': True},
                'FWHM': fwhm, 'STAR_COUNT': str(count),
                'CAMERA': 'ZWO ASI676MC'}, cfg, FRAME)
        return self.store.snapshot()


def test_quick_restart_carries_on_where_it_stopped(tmp_path):
    first = Run(tmp_path, T0)
    snap = first.frames(12)
    assert snap['imaging_ok'] is True and snap['fwhm']['night_median_px'] == 3.0
    changed_at = snap['changed_at']

    second = Run(tmp_path, first.now + timedelta(seconds=40))
    snap = second.frames(1, step=0.0, count=150)
    assert snap['imaging_ok'] is True and snap['changed_at'] == changed_at
    assert snap['frames_considered'] == 13
    assert snap['stars']['change_pct'] == -50.0          # against the saved counts
    assert snap['fwhm']['night_median_px'] == 3.0        # the saved night's readings
    assert snap['reasons'] == ['stars_dropping'] and snap['bad_streak'] == 1


def test_restart_during_a_pause_stays_paused(tmp_path):
    first = Run(tmp_path, T0)
    first.frames(1)
    assert first.frames(3, roof='Closed')['imaging_ok'] is False

    second = Run(tmp_path, first.now + timedelta(seconds=30))
    snap = second.frames(1, step=0.0)
    assert snap['imaging_ok'] is False and snap['reasons'] == ['recovering']
    assert second.frames(2)['imaging_ok'] is True


def test_state_older_than_the_stale_limit_is_ignored(tmp_path):
    first = Run(tmp_path, T0)
    first.frames(1)
    first.frames(3, roof='Closed')

    second = Run(tmp_path, first.now + timedelta(seconds=121))   # auto limit: 120 s
    snap = second.frames(1, step=0.0)
    assert snap['imaging_ok'] is True and snap['frames_considered'] == 1

    third = Run(tmp_path, T0 + timedelta(hours=1))
    first.session.save(at=T0, verdict=first.feed._verdict.export(), counts=[], fwhm=[],
                       intervals=[], force=True)
    cfg = {'imaging_conditions': {'stale_after_s': 7200}}
    assert third.frames(1, step=0.0, cfg=cfg)['imaging_ok'] is False   # within the configured limit


def test_unusable_files_load_as_nothing(tmp_path):
    path = tmp_path / 'session_state.json'
    state = SessionState(str(path))
    kw = dict(now=T0, counts_max=60, fwhm_max=600, intervals_max=10)
    assert state.load(**kw) is None
    for text in ('', '{"version": 1', '[]', '{"version": 99}',
                 json.dumps({'version': 1, 'saved_at': 'yesterday', 'verdict': {}}),
                 json.dumps({'version': 1, 'saved_at': (T0 + timedelta(hours=1)).isoformat(),
                             'verdict': {}}),
                 json.dumps({'version': 1, 'saved_at': T0.isoformat(), 'verdict': None})):
        path.write_text(text, encoding='utf-8')
        assert state.load(**kw) is None
    path.write_text('x' * 70_000, encoding='utf-8')
    assert state.load(**kw) is None

    path.write_text(json.dumps({
        'version': 1, 'saved_at': (T0 - timedelta(seconds=10)).isoformat(),
        'verdict': {'imaging_ok': 'yes'}, 'counts': [5, -1, True, 'x', 10 ** 9, 7],
        'fwhm': [2.5, 0, float('nan'), 900, '3'], 'intervals': 'none'}), encoding='utf-8')
    loaded = state.load(**kw)
    assert loaded['age_s'] == 10 and loaded['counts'] == [5, 7] and loaded['fwhm'] == [2.5]
    assert loaded['intervals'] == []
    assert VerdictHysteresis().restore(loaded['verdict']) is False

    run = Run(tmp_path, T0)                      # a bad verdict block changes nothing
    assert run.frames(1)['frames_considered'] == 1


def test_saves_are_throttled_but_a_verdict_change_is_written_at_once(tmp_path):
    state = SessionState(str(tmp_path / 's.json'))
    verdict = VerdictHysteresis().export()
    kw = dict(verdict=verdict, counts=[1], fwhm=[], intervals=[])
    assert state.save(at=T0, **kw) is True
    assert state.save(at=T0 + timedelta(seconds=5), **kw) is False
    assert state.save(at=T0 + timedelta(seconds=5), force=True, **kw) is True
    assert state.save(at=T0 + timedelta(seconds=26), **kw) is True
    assert [p.name for p in tmp_path.iterdir()] == ['s.json']


def test_hysteresis_export_round_trip():
    a = VerdictHysteresis()
    a.observe(True, bad_frames=3, good_frames=3, now=T0)
    a.observe(False, bad_frames=3, good_frames=3, now=T0)
    b = VerdictHysteresis()
    assert b.restore(json.loads(json.dumps(a.export()))) is True
    assert b.state() == a.state()
    assert b.restore({'imaging_ok': True}) is False and b.state() == a.state()
