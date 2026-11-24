"""conditions_history: the bounded ring of per-frame verdicts, the paused /
resumed events derived from it (a frames gap while OK is a pause), the
events file that survives a restart, thread safety, and the feed recording
into it; nina_checkin: the /conditions request record behind "NINA last
checked", through the real route handler."""
import json
import threading
from datetime import datetime, timedelta, timezone

from services.imaging_conditions import ImagingConditionsFeed, conditions_history
from services.imaging_conditions.conditions_history import (
    ConditionsHistory, get_conditions_history, load_events)
from services.imaging_conditions.nina_checkin import (
    NINA, NinaCheckins, client_label, get_nina_checkins)
from services.imaging_conditions.star_baseline import StarBaseline

T0 = datetime(2026, 11, 23, 22, 0, tzinfo=timezone.utc)


def rec(ok, reasons=(), count=300, baseline=315.0):
    return {'imaging_ok': ok, 'frame_ok': ok, 'reasons': list(reasons),
            'stars': {'count': count, 'baseline': baseline}}


def history(tmp_path, **kw):
    kw.setdefault('wall', lambda: T0 + timedelta(hours=1))
    return ConditionsHistory(str(tmp_path / 'events.json'), **kw)


def test_events_follow_imaging_ok_changes(tmp_path):
    h = history(tmp_path)
    at = T0
    for ok, reasons in [(False, ['warming_up']), (True, []), (True, []),
                        (False, ['cloud_cover_high', 'stars_dropping']), (False, ['recovering']),
                        (True, [])]:
        h.record(rec(ok, reasons), at=at, stale_after_s=120)
        at += timedelta(seconds=30)
    events = h.events()
    assert [e['kind'] for e in events] == ['resumed', 'paused', 'resumed']
    assert events[1]['reasons'] == ['cloud_cover_high', 'stars_dropping']
    assert events[1]['duration_s'] == 60.0 and events[0]['duration_s'] is None
    assert events[2]['at'] == T0 + timedelta(seconds=30)
    payload = h.events_payload()
    assert payload[0] == {'at': '2026-11-23T22:02:30Z', 'kind': 'resumed', 'reasons': [],
                          'duration_s': None}
    json.dumps(payload)


def test_a_gap_while_ok_is_a_pause_at_the_stale_limit(tmp_path):
    h = history(tmp_path)
    h.record(rec(True), at=T0, stale_after_s=120)
    h.record(rec(False, ['warming_up']), at=T0 + timedelta(minutes=10), stale_after_s=120)
    events = h.events()
    assert [e['kind'] for e in events] == ['paused', 'resumed']
    assert events[0]['reasons'] == ['stale']
    assert events[0]['at'] == T0 + timedelta(seconds=120)


def test_ring_is_bounded_by_count_and_age(tmp_path):
    h = history(tmp_path, max_samples=50, max_age_s=600, wall=lambda: T0 + timedelta(hours=2))
    for i in range(200):
        h.record(rec(True), at=T0 + timedelta(hours=2) - timedelta(seconds=10 * (199 - i)),
                 stale_after_s=120)
    samples = h.samples()
    assert len(samples) <= 50
    assert samples[0].at >= T0 + timedelta(hours=2) - timedelta(seconds=600)


def test_events_survive_a_restart_and_close_an_open_ok(tmp_path):
    h = history(tmp_path)
    h.record(rec(True), at=T0, stale_after_s=120)
    h.record(rec(False, ['cloud_cover_high']), at=T0 + timedelta(minutes=1), stale_after_s=120)
    h.record(rec(True), at=T0 + timedelta(minutes=2), stale_after_s=120)
    reopened = history(tmp_path)
    events = reopened.events()
    assert [e['kind'] for e in events] == ['paused', 'resumed', 'paused', 'resumed']
    assert events[0]['reasons'] == ['restarted']
    assert events[2]['reasons'] == ['cloud_cover_high']


def test_old_garbled_or_oversized_event_files_load_as_nothing(tmp_path):
    path = tmp_path / 'events.json'
    path.write_text('{"events": [{"at": "2020-01-01T00:00:00Z", "kind": "paused"}]}')
    assert load_events(str(path), T0) == []
    path.write_text('not json')
    assert load_events(str(path), T0) == []
    path.write_text('{"events": [{"at": "2026-11-23T21:00:00Z", "kind": "rm -rf"}, 7]}')
    assert load_events(str(path), T0) == []
    path.write_text(' ' * (conditions_history.MAX_FILE_BYTES + 1))
    assert load_events(str(path), T0) == []
    assert load_events(str(tmp_path / 'missing.json'), T0) == []


def test_concurrent_writers_and_readers(tmp_path):
    h = history(tmp_path, wall=lambda: T0 + timedelta(hours=1), max_samples=500)
    errors = []

    def write(offset):
        try:
            for i in range(300):
                h.record(rec(i % 7 != 0, ['stars_low']),
                         at=T0 + timedelta(seconds=offset + i * 4), stale_after_s=1e9)
        except Exception as e:
            errors.append(e)

    def read():
        try:
            for _ in range(300):
                h.samples()
                h.events_payload()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write, args=(k,)) for k in range(4)]
    threads += [threading.Thread(target=read) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(h.samples()) == 500
    assert len(h.events()) <= conditions_history.MAX_EVENTS


def test_the_feed_records_every_judged_frame(tmp_path):
    clock = {'mono': 0.0, 'wall': T0}

    def tick():
        clock['mono'] += 30
        clock['wall'] += timedelta(seconds=30)

    h = history(tmp_path, wall=lambda: clock['wall'])
    feed = ImagingConditionsFeed(baseline=StarBaseline(str(tmp_path / 'b.json')), history=h,
                                 sun_alt_fn=lambda c: -30.0,
                                 moon_fn=lambda: {'moon_is_up': False},
                                 clock=lambda: clock['mono'], wall=lambda: clock['wall'])
    md = {'_ML_RESULTS': {'roof_status': 'Open', 'roof_confidence': 0.9,
                          'sky_condition': 'Clear', 'sky_confidence': 0.9},
          'STAR_COUNT': '300'}
    for _ in range(4):
        tick()
        feed.observe(md, {}, (100, 100))
    assert len(h.samples()) == 4 and h.samples()[-1].star_index == 300
    assert [e['kind'] for e in h.events()] == ['resumed']


def test_store_snapshot_carries_the_history_events():
    from services.imaging_conditions import get_conditions_store
    get_conditions_history().record(rec(True), at=datetime.now(timezone.utc), stale_after_s=120)
    snap = get_conditions_store().snapshot()
    assert [e['kind'] for e in snap['events']] == ['resumed']
    get_conditions_store().set_enabled(False)
    assert get_conditions_store().snapshot()['events'] == []


# --- NINA check-ins ------------------------------------------------------

def test_client_labels():
    assert client_label('PFRSentinel-NINA/1.0') == NINA
    assert client_label('curl/8.5.0') == 'curl'
    assert client_label('Mozilla/5.0 (WindowsPowerShell/5.1)') == 'PowerShell'
    assert client_label(None) == 'another client'


def test_checkins_keep_the_last_nina_and_the_last_anyone():
    now = {'t': 100.0}
    c = NinaCheckins(clock=lambda: now['t'])
    assert c.last() == {'nina': None, 'any': None}
    c.record('PFRSentinel-NINA/1.0', accepted=True)
    now['t'] = 104.0
    c.record('curl/8', accepted=True)
    last = c.last()
    assert last['nina']['age_s'] == 4.0 and last['nina']['client'] == NINA
    assert last['any']['client'] == 'curl' and last['any']['age_s'] == 0.0


def test_the_real_route_records_the_request():
    from services import web_conditions
    from tests.test_api_conditions import _FakeHandler, AUTH
    handler = _FakeHandler(headers=dict(AUTH, **{'User-Agent': 'PFRSentinel-NINA/1.0'}))
    web_conditions.serve_conditions(handler)
    assert handler.status == 200
    last = get_nina_checkins().last()
    assert last['nina']['accepted'] is True and last['nina']['age_s'] < 5
    refused = _FakeHandler(headers={'Host': '127.0.0.1', 'User-Agent': 'PFRSentinel-NINA/1.0',
                                    'Authorization': 'Bearer wrong'})
    web_conditions.serve_conditions(refused)
    assert refused.status == 401
    assert get_nina_checkins().last()['nina']['accepted'] is False
    assert 'wrong' not in json.dumps(get_nina_checkins().last(), default=str)


def test_events_path_stays_in_the_test_folder():
    assert 'imaging_conditions_events' in conditions_history.default_events_path()
