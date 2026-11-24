"""The imaging-conditions grade a frame got at capture time, kept in the
image library: the pipelines' hook leaves a compact summary on the frame's
metadata, the library stores it in nullable columns (old databases migrate,
old rows read as not graded), a reprocess never changes a grade, and the
Library shows it per frame and per night in the live views' wording."""
import json
import os
import sqlite3
from datetime import datetime

import pytest
from PIL import Image

from services.imaging_conditions import feed as feed_module
from services.imaging_conditions import frame_grade as G
from services.imaging_conditions.feed import observe_imaging_conditions
from services.library import ImageLibrary, sessions as S
from services.library.grade_summary import summarize_grades
from services.library.index import LibraryIndex, _FIELDS
from services.observing_window import SAME_CAPTURE_KEY
from services.sky_evidence import EVIDENCE_KEY
from ui.components import library_grade_text as T

FRAME = (3552, 3552)
CFG = {'imaging_conditions': {}, 'weather': {}}
NIGHT = int(datetime(2026, 7, 3, 22, 0).timestamp())


def sky_frame(count=300, roof='Open', sky='Clear', name='capture_1.png'):
    return {
        '_ML_RESULTS': {'roof_status': roof, 'roof_confidence': 0.95,
                        'sky_condition': sky, 'sky_confidence': 0.9,
                        'stars_visible': bool(count)},
        EVIDENCE_KEY: {'star_count': count}, 'FWHM': '3.0', 'STAR_COUNT': '150',
        'CAMERA': 'ZWO ASI676MC', 'FILENAME': name, 'DATETIME': '2026-07-03 22:00:00',
    }


def record(ok=True, frame_ok=True, reasons=(), ratio=0.62, moon='moon_down'):
    return {'imaging_ok': ok, 'frame_ok': frame_ok, 'reasons': list(reasons),
            'stars': {'count': 186, 'baseline': 300.0, 'ratio': ratio},
            'cloud_cover_pct': 38.0, 'baseline_bin': moon,
            'fwhm': {'px': 3.0, 'change_pct': 4.0}, 'roof': {'state': 'Open'}}


class FakeFeed:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def observe(self, metadata, config, frame_size=None):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def _no_remembered_capture():
    G.forget_last_grade()
    yield
    G.forget_last_grade()


@pytest.fixture
def fake_feed(monkeypatch):
    def install(result):
        fake = FakeFeed(result)
        monkeypatch.setattr(feed_module, 'get_imaging_conditions_feed', lambda: fake)
        return fake
    return install


def row(id_, at=0, **grade):
    base = {'id': id_, 'captured_at': NIGHT + at, 'path': f'p/{id_}.jpg', 'bytes': 10,
            'created_at': NIGHT + at, 'roof': 'Open', 'condition': 'Clear', 'clouds': 0}
    base.update(grade)
    return base


def graded(id_, at=0, ok=True, frame_ok=None, reasons='', ratio=0.9, moon='moon_down'):
    return row(id_, at, imaging_ok=int(ok),
               frame_ok=int(ok if frame_ok is None else frame_ok),
               imaging_reasons=reasons, clear_sky_ratio=ratio, moon_bin=moon)


# -- the summary and the hook ------------------------------------------------

def test_summary_is_small_and_json_safe():
    summary = G.summarize_record(record(ok=False, frame_ok=False,
                                        reasons=['cloud_cover_high', 'stars_low']))
    assert summary == {'imaging_ok': False, 'frame_ok': False,
                       'reasons': ['cloud_cover_high', 'stars_low'],
                       'clear_sky_ratio': 0.62, 'moon_bin': 'moon_down',
                       'moon_separation_deg': None}
    assert json.loads(json.dumps(summary)) == summary


def test_summary_of_a_learning_baseline_has_no_ratio():
    assert G.summarize_record(record(ratio=None))['clear_sky_ratio'] is None
    assert G.summarize_record(record(ratio=float('nan')))['clear_sky_ratio'] is None


def test_no_record_is_no_summary():
    assert G.summarize_record(None) is None
    assert G.summarize_record({'reasons': []}) is None


def test_hook_leaves_the_real_feeds_grade_on_the_metadata():
    meta = sky_frame()
    observe_imaging_conditions(meta, CFG, FRAME)
    grade = meta[G.GRADE_KEY]
    assert grade['imaging_ok'] is True and grade['frame_ok'] is True
    assert grade['reasons'] == []
    assert grade['moon_bin']
    assert grade['clear_sky_ratio'] is None  # baseline still learning


def test_hook_records_why_a_frame_was_not_ok():
    meta = sky_frame(roof='Closed')
    observe_imaging_conditions(meta, CFG, FRAME)
    grade = meta[G.GRADE_KEY]
    assert grade['imaging_ok'] is False and grade['frame_ok'] is False
    assert grade['reasons'][0] == 'roof_not_open'


def test_disabled_conditions_leave_the_frame_ungraded():
    meta = sky_frame()
    meta[G.GRADE_KEY] = {'imaging_ok': True}  # stale, from an earlier pass
    observe_imaging_conditions(meta, {'imaging_conditions': {'enabled': False}}, FRAME)
    assert G.GRADE_KEY not in meta
    assert G.grade_columns(meta) == dict.fromkeys(G.GRADE_COLUMNS)


def test_a_failing_feed_never_raises_and_leaves_no_grade(fake_feed):
    fake_feed(RuntimeError("boom"))
    meta = sky_frame()
    observe_imaging_conditions(meta, CFG, FRAME)
    assert G.GRADE_KEY not in meta


def test_grade_key_can_never_be_an_overlay_token():
    from services.overlay_renderer import replace_tokens
    meta = sky_frame()
    observe_imaging_conditions(meta, CFG, FRAME)
    assert G.GRADE_KEY.startswith('_') and G.GRADE_KEY != G.GRADE_KEY.upper()
    assert replace_tokens('{_imaging_grade} {_IMAGING_GRADE}', meta) == '? ?'


# -- reprocess ----------------------------------------------------------------

def test_reprocess_keeps_the_grade_already_on_the_metadata(fake_feed):
    fake_feed(record(ok=False, frame_ok=False, reasons=['stars_low']))
    meta = sky_frame()
    observe_imaging_conditions(meta, CFG, FRAME)
    before = dict(meta[G.GRADE_KEY])
    meta[SAME_CAPTURE_KEY] = True
    fake_feed(None)  # what the feed returns for a reprocess
    observe_imaging_conditions(meta, CFG, FRAME)
    assert meta[G.GRADE_KEY] == before


def test_reprocess_rebuilt_from_the_cached_capture_gets_its_grade_back(fake_feed):
    fake_feed(record(ok=False, frame_ok=False, reasons=['cloud_cover_high']))
    capture = sky_frame(name='capture_7.png')
    cached = dict(capture)  # camera mode caches the metadata before processing
    observe_imaging_conditions(capture, CFG, FRAME)
    fake_feed(None)
    cached[SAME_CAPTURE_KEY] = True
    observe_imaging_conditions(cached, CFG, FRAME)
    assert cached[G.GRADE_KEY] == capture[G.GRADE_KEY]
    assert cached[G.GRADE_KEY] is not capture[G.GRADE_KEY]


def test_reprocess_of_another_capture_is_not_given_a_strangers_grade(fake_feed):
    fake_feed(record())
    observe_imaging_conditions(sky_frame(name='capture_7.png'), CFG, FRAME)
    fake_feed(None)
    other = sky_frame(name='capture_3.png')
    other[SAME_CAPTURE_KEY] = True
    observe_imaging_conditions(other, CFG, FRAME)
    assert G.GRADE_KEY not in other


def test_reprocess_after_conditions_were_switched_off_gets_nothing(fake_feed):
    fake_feed(record())
    observe_imaging_conditions(sky_frame(), CFG, FRAME)
    fake_feed(None)
    observe_imaging_conditions(sky_frame(name='capture_2.png'), CFG, FRAME)  # ungraded capture
    again = sky_frame(name='capture_2.png')
    again[SAME_CAPTURE_KEY] = True
    observe_imaging_conditions(again, CFG, FRAME)
    assert G.GRADE_KEY not in again


def test_reprocess_adds_a_row_and_leaves_the_stored_grade_alone(tmp_path, fake_feed):
    index = LibraryIndex(str(tmp_path / 'lib.db'))
    fake_feed(record(ok=False, frame_ok=False, reasons=['cloud_cover_high'], ratio=0.31))
    capture = sky_frame()
    cached = dict(capture)
    observe_imaging_conditions(capture, CFG, FRAME)
    first = index.insert(row(None, **ImageLibrary._extract_meta(capture)))
    stored = index.get(first)

    fake_feed(record(ok=True))  # the sky has since cleared; a reprocess must not see it
    cached[SAME_CAPTURE_KEY] = True
    observe_imaging_conditions(cached, CFG, FRAME)
    second = index.insert(row(None, 5, **ImageLibrary._extract_meta(cached)))

    assert index.get(first) == stored
    for column in G.GRADE_COLUMNS:
        assert index.get(second)[column] == stored[column]
    assert stored['imaging_ok'] == 0 and stored['imaging_reasons'] == 'cloud_cover_high'
    index.close()


# -- both pipelines carry it to the dict the library sees ----------------------

def test_directory_watch_metadata_carries_the_grade(tmp_path):
    from services.config import Config
    from services.processor import process_image
    cfg = Config(str(tmp_path / 'config.json'))
    cfg.set('output_directory', str(tmp_path / 'out'))
    src = tmp_path / 'frame.png'
    Image.new('RGB', (64, 48), (5, 5, 5)).save(src)
    extras = {}
    process_image(str(src), cfg, extras=extras)
    grade = extras['metadata'][G.GRADE_KEY]
    assert isinstance(grade['imaging_ok'], bool)
    assert ImageLibrary._extract_meta(extras['metadata'])['imaging_ok'] in (0, 1)


def test_camera_pipeline_metadata_carries_the_grade(tmp_path):
    from PySide6 import QtWidgets
    from PySide6.QtCore import QEvent
    from ui.controllers.image_processor import ImageProcessingTask, ImageProcessorWorker
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    worker = ImageProcessorWorker()
    worker._main_window = None
    seen = []
    worker.processing_complete.connect(
        lambda preview, out, meta, path, dispatch: seen.append(meta))
    config = {'output_dir': str(tmp_path), 'output_format': 'PNG', 'resize_percent': 100,
              'auto_stretch': {'enabled': False}, 'overlays': [], 'dev_mode': {'enabled': False},
              'ml_contribution': {'enabled': False}, 'meteor': {}, 'sharpening': {},
              'allsky_overlay': {}, 'weather': {}, 'ml_models': {'enabled': False}}
    try:
        worker._process_task(ImageProcessingTask(
            Image.new('RGB', (96, 64), (20, 20, 20)), {'FILENAME': 'a.png'}, config))
        assert seen, "processing_complete did not fire"
        assert isinstance(seen[0][G.GRADE_KEY]['imaging_ok'], bool)
        assert ImageLibrary._extract_meta(seen[0])['imaging_ok'] in (0, 1)
    finally:
        worker.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


# -- the index ------------------------------------------------------------------

_OLD_SCHEMA = """
CREATE TABLE images (
    id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at INTEGER NOT NULL,
    path TEXT NOT NULL, width INTEGER, height INTEGER, bytes INTEGER NOT NULL,
    session TEXT, exposure TEXT, gain TEXT, temp TEXT, camera TEXT, weather TEXT,
    roof TEXT, condition TEXT, clouds INTEGER, star_count INTEGER, seeing TEXT,
    fwhm TEXT, created_at INTEGER NOT NULL
);
"""


def test_old_database_opens_and_old_rows_read_as_not_graded(tmp_path):
    path = str(tmp_path / 'library.db')
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SCHEMA)
    conn.execute("INSERT INTO images (captured_at, path, bytes, roof, star_count, created_at) "
                 "VALUES (?, 'old/1.jpg', 100, 'Open', 412, ?)", (NIGHT, NIGHT))
    conn.commit()
    conn.close()

    index = LibraryIndex(path)
    old = index.query()[0]
    assert old['star_count'] == 412 and old['roof'] == 'Open'
    assert all(old[c] is None for c in G.GRADE_COLUMNS)
    assert not G.is_graded(old)
    new_id = index.insert(graded(None, 60, ok=False, reasons='stars_low'))
    assert index.get(new_id)['imaging_reasons'] == 'stars_low'
    index.close()
    LibraryIndex(path).close()  # a second open has nothing left to migrate


def test_grade_round_trips_through_the_library_columns(tmp_path):
    meta = sky_frame()
    G.stamp_frame_grade(meta, record(ok=False, frame_ok=True,
                                     reasons=['recovering'], ratio=0.874, moon='moon_up_25_50'))
    index = LibraryIndex(str(tmp_path / 'lib.db'))
    rid = index.insert(row(None, **ImageLibrary._extract_meta(meta)))
    for got in (index.get(rid), index.query()[0], index.range_rows()[0]):
        assert got['imaging_ok'] == 0 and got['frame_ok'] == 1
        assert G.split_reasons(got['imaging_reasons']) == ['recovering']
        assert got['clear_sky_ratio'] == pytest.approx(0.874)
        assert got['moon_bin'] == 'moon_up_25_50'
        assert got['star_count'] == 150  # still the STAR_COUNT token, not a second count
    brief = index.brief_rows()[0]
    assert brief['imaging_ok'] == 0 and brief['imaging_reasons'] == 'recovering'
    index.close()


def test_ungraded_frame_stores_nulls(tmp_path):
    index = LibraryIndex(str(tmp_path / 'lib.db'))
    rid = index.insert(row(None, **ImageLibrary._extract_meta(sky_frame())))
    assert all(index.get(rid)[c] is None for c in G.GRADE_COLUMNS)
    index.close()


def test_every_grade_column_is_a_migrated_index_field():
    from services.library.index import _MIGRATIONS
    migrated = {name for name, _ in _MIGRATIONS}
    assert set(G.GRADE_COLUMNS) <= migrated
    assert set(G.GRADE_COLUMNS) <= set(_FIELDS)


def test_web_manifest_and_openapi_carry_the_grade(tmp_path):
    from io import BytesIO
    from services import web_library
    from services.api_docs import _library_schemas

    index = LibraryIndex(str(tmp_path / 'lib.db'))
    index.insert(graded(None, ok=False, reasons='cloud_cover_high,stars_low', ratio=0.4))
    index.insert(row(None, 60))

    class Library:
        MAX_PAGE = 500

        def list_images(self, **kw):
            return index.count(), index.query(**kw)

    class Handler:
        def __init__(self):
            self.wfile = BytesIO()

        def send_response(self, code): self.code = code
        def send_header(self, *a): pass
        def end_headers(self): pass

    handler = Handler()
    web_library.serve_list(handler, Library(), '/library', {})
    images = json.loads(handler.wfile.getvalue())['images']
    assert images[0]['imaging_ok'] is None and images[0]['imaging_reasons'] is None
    assert images[1]['imaging_ok'] == 0
    assert images[1]['imaging_reasons'] == 'cloud_cover_high,stars_low'
    assert images[1]['clear_sky_ratio'] == pytest.approx(0.4)
    documented = _library_schemas()['LibraryImage']['properties']
    assert set(G.GRADE_COLUMNS) <= set(documented)
    index.close()


# -- the night summary ------------------------------------------------------------

def test_night_summary_counts_only_graded_frames():
    rows = [graded(1, 0), graded(2, 60), graded(3, 120),
            graded(4, 180, ok=False, reasons='cloud_cover_high,stars_low'),
            graded(5, 240, ok=False, reasons='cloud_cover_high'),
            graded(6, 300, ok=False, reasons='stars_dropping'),
            graded(7, 360, ok=False, frame_ok=True, reasons='recovering'),
            graded(8, 420, ok=False, frame_ok=True, reasons='recovering'),
            row(9, 480), row(10, 540)]
    summary = summarize_grades(rows)
    assert summary == {'graded_count': 8, 'ok_count': 3, 'ok_pct': 38,
                       'top_reason': 'cloud_cover_high', 'top_reason_count': 2}


def test_night_summary_names_a_holding_reason_only_without_a_cause():
    rows = [graded(1, ok=False, frame_ok=True, reasons='warming_up'),
            graded(2, 60, ok=False, frame_ok=True, reasons='warming_up'), graded(3, 120)]
    assert summarize_grades(rows)['top_reason'] == 'warming_up'


def test_night_summary_of_ungraded_frames_is_empty():
    summary = summarize_grades([row(1), row(2, 60)])
    assert summary['graded_count'] == 0 and summary['ok_pct'] is None
    assert summary['top_reason'] is None
    assert T.night_grade_text(summary) == ""


def test_all_ok_night_has_no_reason():
    summary = summarize_grades([graded(1), graded(2, 60)])
    assert summary['ok_pct'] == 100 and summary['top_reason'] is None


def test_session_summaries_carry_the_grade_figures(tmp_path):
    index = LibraryIndex(str(tmp_path / 'lib.db'))
    index.insert(graded(None, 0))
    index.insert(graded(None, 60, ok=False, reasons='fwhm_high'))
    index.insert(row(None, 120))
    session = S.summarize_sessions(index)[0]
    assert session['frame_count'] == 3
    assert session['graded_count'] == 2 and session['ok_pct'] == 50
    assert session['top_reason'] == 'fwhm_high'
    index.close()


# -- wording -------------------------------------------------------------------------

def test_headline_uses_the_live_reason_wording():
    from ui.components.imaging_conditions_text import REASON_TEXT
    assert T.frame_grade_headline(graded(1)) == ("OK", 'ok')
    for code, words in REASON_TEXT.items():
        text, _ = T.frame_grade_headline(graded(1, ok=False, reasons=code))
        if code == 'warming_up':
            # Only rows stored before the first good frame was enough carry
            # it on a judged frame; "waiting for the first frame" would be
            # nonsense beside a frame, so they keep the old words.
            assert text == "Not OK · Warming up"
            continue
        assert text == f"Not OK · {words}"
    assert T.frame_grade_headline(graded(1, ok=False, reasons='recovering'))[1] == 'info'
    assert T.frame_grade_headline(graded(1, ok=False, reasons='stars_low'))[1] == 'warn'


def test_ungraded_frame_is_a_dash_never_not_ok():
    for ungraded in (row(1), {}, None, row(1, imaging_ok=None, imaging_reasons='stars_low')):
        text, tone = T.frame_grade_headline(ungraded)
        assert text == "—" and tone == 'muted'
        assert T.frame_grade_details(ungraded) == []


def test_details_say_what_the_headline_does_not():
    lines = T.frame_grade_details(
        graded(1, ok=False, reasons='cloud_cover_high,stars_low', ratio=0.31))
    assert lines == ["Also: Too few stars", "Stars at 31% of clear sky · Moon down"]
    held = T.frame_grade_details(graded(1, ok=True, frame_ok=False, reasons='stars_low'))
    assert held[0] == "This frame alone was not OK: Too few stars"
    recovering = T.frame_grade_details(
        graded(1, ok=False, frame_ok=True, reasons='recovering', ratio=None, moon=None))
    assert recovering == ["This frame alone was OK"]
    assert T.frame_grade_details(graded(1, ratio=None)) == ["Moon down"]


def test_night_text():
    summary = {'graded_count': 1400, 'ok_pct': 82, 'top_reason': 'cloud_cover_high'}
    assert T.night_grade_text(summary) == \
        "Imaging OK on 82% of 1,400 graded frames · mostly Cloud cover high"
    assert T.night_grade_text(summary, short=True) == "imaging OK 82% · mostly Cloud cover high"
    assert T.night_grade_text({'graded_count': 1, 'ok_pct': 100, 'top_reason': None}) == \
        "Imaging OK on 100% of 1 graded frame"


# -- widgets (offscreen Qt) --------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_widget(qt_app):
    created = []

    def _make(ctor):
        widget = ctor()
        created.append(widget)
        return widget

    yield _make
    from PySide6.QtCore import QEvent
    for widget in created:
        timer = getattr(widget, "_timer", None)
        if timer is not None:
            timer.stop()
        widget.close()
        widget.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


def _open_night(make_widget, frames):
    from ui.panels.library_night_view import NightView
    view = make_widget(NightView)
    session = {'key': S.night_key(NIGHT), 'gaps': [], 'max_gap_seconds': 0,
               **summarize_grades(frames)}
    view.begin_load(session)
    view.set_frames({'session': session, 'frames': frames, 'filmstrip': []})
    return view


def test_night_view_shows_each_frames_grade(qt_app, make_widget):
    frames = [graded(1, 0), graded(2, 60, ok=False, reasons='cloud_cover_high,stars_low',
                                   ratio=0.31),
              row(3, 120)]
    view = _open_night(make_widget, frames)
    block = view.grade_block

    view._seek_to_id(1)
    assert block.headline.text() == "OK"
    assert "Stars at 90% of clear sky" in block.details.text()

    view._seek_to_id(2)
    assert block.headline.text() == "Not OK · Cloud cover high"
    assert "Also: Too few stars" in block.details.text()
    assert "31% of clear sky" in block.details.text()
    assert not block.details.isHidden()

    view._seek_to_id(3)  # archived before grades existed, or with conditions off
    assert block.headline.text() == "—"
    assert block.details.isHidden() and block.details.text() == ""
    assert view._meta_values['stars'].text() == "—"


def test_night_view_header_summarises_the_night_and_follows_live_frames(qt_app, make_widget):
    view = _open_night(make_widget, [graded(1, 0), graded(2, 60, ok=False, reasons='stars_low')])
    assert view.grade_label.text() == "Imaging OK on 50% of 2 graded frames · mostly Too few stars"
    view.append_frame(graded(3, 120))
    assert view.grade_label.text().startswith("Imaging OK on 67% of 3 graded frames")


def test_night_view_header_is_empty_for_an_ungraded_night(qt_app, make_widget):
    view = _open_night(make_widget, [row(1), row(2, 60)])
    assert view.grade_label.text() == ""


def test_session_card_states_the_share_only_when_frames_were_graded(qt_app, make_widget):
    from qfluentwidgets import CaptionLabel
    from ui.panels.library_session_list import _SessionCard

    def stats_text(session):
        card = make_widget(lambda: _SessionCard(session))
        return " | ".join(label.text() for label in card.findChildren(CaptionLabel))

    base = {'key': '2026-07-03', 'start_epoch': NIGHT, 'end_epoch': NIGHT + 3600,
            'frame_count': 120, 'band': [(0.0, 1.0, S.STATUS_CLEAR)], 'max_gap_seconds': 0}
    with_grades = stats_text({**base, 'graded_count': 100, 'ok_pct': 82,
                              'top_reason': 'cloud_cover_high'})
    assert "imaging OK 82% · mostly Cloud cover high" in with_grades
    assert "imaging" not in stats_text({**base, 'graded_count': 0, 'ok_pct': None,
                                        'top_reason': None}).lower()
    assert "imaging" not in stats_text(base).lower()
    # The two shares are named for what they measure, so 82% beside 60%
    # does not read as two verdicts that disagree.
    both = stats_text({**base, 'graded_count': 100, 'ok_pct': 82, 'top_reason': None,
                       'clear_pct': 60})
    assert "imaging OK 82%" in both and "sky clear 60%" in both and "60% clear" not in both
