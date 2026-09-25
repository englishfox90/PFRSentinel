"""
Tests for services/allsky/buffer_dump.py (issue #93, package 0) and the
image-library loader in scripts/dev/allsky/library_to_buffer.py.

A dump must give a replay exactly the frames the service saw (aware
datetimes, float detections, the catalogue list the fit matched against),
must never contain image data however the frame dict was decorated, must
keep only the newest MAX_DUMPS files, and must be written by the three
triggers — escape exhaustion (once), every basin escape in a dev build, and
the UI button — without ever raising into the service.
"""
import importlib.util
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from services import app_config
from services.allsky import buffer_dump as bd
from services.allsky.buffer_dump import (
    MAX_DUMPS, BufferDumpTrigger, dump_buffer, list_dumps, load_buffer,
    model_from_dict, newest_dump, write_dump,
)
from services.allsky.escape_policy import ESCAPE_EXHAUSTION_THRESHOLD
from services.allsky.fisheye import FisheyeModel
from services.allsky.frame_catalog import above_horizon_stars

# Two decimals so SITE_DECIMALS rounding is the identity and the recomputed
# catalogue list is bit-identical to the one dumped.
LAT, LON = 36.42, -116.87
T0 = datetime(2026, 9, 23, 5, 30, tzinfo=timezone.utc)
FRAME_PX = 750

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'dev' / 'allsky' / 'library_to_buffer.py'


def _frame(i: int, dt=None, **extra) -> dict:
    dt = dt or T0 + timedelta(minutes=3 * i)
    frame = {
        'dt': dt,
        'detected': [(np.float64(100.5 + i), np.float32(200.25), np.float64(1234.0)),
                     (10.0, 20.0, 5.5)],
        'above_horizon': above_horizon_stars(dt, LAT, LON),
        'sky_cx': 375.0, 'sky_cy': np.float64(376.5), 'sky_r': 340.0,
        'image_width': FRAME_PX, 'image_height': FRAME_PX,
    }
    frame.update(extra)
    return frame


def _model(**over) -> FisheyeModel:
    m = FisheyeModel(cx=375.0, cy=376.5, a1=216.4, a3=-1.7, a5=-6.1, roll=1.35,
                     axis_alt=72.6, axis_az=264.6, east_left=True,
                     rms_residual=4.1, n_matches=7, n_images=1,
                     calibrated_at='2026-09-15T02:20:11+00:00',
                     image_width=FRAME_PX, image_height=FRAME_PX,
                     provenance='guided')
    for k, v in over.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

class TestRoundTrip:

    def test_frames_come_back_in_the_service_shape(self, tmp_path):
        frames = [_frame(0), _frame(1, dt=T0.astimezone(timezone(timedelta(hours=2)))),
                  _frame(2)]
        path = dump_buffer(frames, None, LAT, LON, tmp_path, now=T0)

        loaded, model, lat, lon = load_buffer(path)
        assert model is None and (lat, lon) == (LAT, LON)
        assert len(loaded) == 3
        for orig, back in zip(frames, loaded):
            assert back['dt'] == orig['dt'] and back['dt'].tzinfo is not None
            assert back['dt'].utcoffset() == orig['dt'].utcoffset()
            assert back['detected'] == [tuple(float(v) for v in d) for d in orig['detected']]
            assert all(isinstance(v, float) for d in back['detected'] for v in d)
            for key in ('sky_cx', 'sky_cy', 'sky_r', 'image_width', 'image_height'):
                assert back[key] == orig[key]
            assert [s['hr'] for s, _, _ in back['above_horizon']] == \
                   [s['hr'] for s, _, _ in orig['above_horizon']]
            assert [(a, z) for _, a, z in back['above_horizon']] == \
                   [(a, z) for _, a, z in orig['above_horizon']]
            assert 'exposure_s' not in back

    def test_exposure_is_kept_when_the_frame_has_one(self, tmp_path):
        path = dump_buffer([_frame(0, exposure_s=np.float32(20.5)), _frame(1)],
                           None, LAT, LON, tmp_path, now=T0)
        loaded, *_ = load_buffer(path)
        assert loaded[0]['exposure_s'] == pytest.approx(20.5)
        assert 'exposure_s' not in loaded[1]

    def test_model_round_trip_carries_the_setattr_extras(self, tmp_path):
        model = _model(final_tol_px=15.5, chance_expected=549.2)
        path = dump_buffer([_frame(0)], model, LAT, LON, tmp_path, now=T0)
        _, model_dict, *_ = load_buffer(path)
        back = model_from_dict(model_dict)
        assert back == _model()
        assert back.provenance == 'guided'
        assert back.final_tol_px == 15.5 and back.chance_expected == 549.2
        assert model_from_dict(None) is None

    def test_site_is_rounded_to_about_a_kilometre(self, tmp_path):
        path = dump_buffer([_frame(0)], None, 36.42345, -116.87891, tmp_path, now=T0)
        payload = json.loads(path.read_text(encoding='utf-8'))
        assert (payload['lat'], payload['lon']) == (36.42, -116.88)

    def test_schema(self, tmp_path):
        path = dump_buffer([_frame(0)], _model(), LAT, LON, tmp_path, now=T0)
        payload = json.loads(path.read_text(encoding='utf-8'))
        assert payload['version'] == 1
        assert payload['created_at'] == T0.isoformat(timespec='seconds')
        assert payload['app_version']
        assert payload['catalog'] == {'max_vmag': 6.5, 'min_alt_deg': 3.0}
        assert payload['model']['a1'] == 216.4
        assert payload['frames'][0]['n_above_horizon'] == len(_frame(0)['above_horizon'])
        assert path.name == 'buffer_20260923_053000_000000.json'
        assert not list(tmp_path.glob('*.tmp'))

    def test_unknown_version_is_refused(self, tmp_path):
        bad = tmp_path / 'buffer_x.json'
        bad.write_text(json.dumps({'version': 99, 'lat': 0, 'lon': 0, 'frames': []}))
        with pytest.raises(ValueError, match='version'):
            load_buffer(bad)


class TestNoImageData:

    def test_extra_keys_never_reach_the_file(self, tmp_path):
        frame = _frame(0, image=np.zeros((600, 600, 3), np.uint8),
                       gray=np.ones((600, 600), np.float32), note='not a number')
        path = write_dump([frame], None, LAT, LON, tmp_path / 'd.json')
        record = json.loads(path.read_text(encoding='utf-8'))['frames'][0]
        assert set(record) == {'dt', 'detected', 'n_above_horizon', 'sky_cx',
                               'sky_cy', 'sky_r', 'image_width', 'image_height'}
        assert path.stat().st_size < 4096


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

class TestRotation:

    def test_keeps_only_the_newest_five(self, tmp_path):
        (tmp_path / 'notes.txt').write_text('keep me')
        decoy_dir = tmp_path / 'buffer_00000000_000000.json'
        decoy_dir.mkdir()
        for i in range(MAX_DUMPS + 2):
            dump_buffer([_frame(0)], None, LAT, LON, tmp_path,
                        now=T0 + timedelta(minutes=i))
        names = [p.name for p in list_dumps(tmp_path)]
        assert len(names) == MAX_DUMPS
        assert names[0] == 'buffer_20260923_053200_000000.json'
        assert names[-1] == 'buffer_20260923_053600_000000.json'
        assert newest_dump(tmp_path) == tmp_path / names[-1]
        assert (tmp_path / 'notes.txt').is_file()
        assert decoy_dir.is_dir()

    def test_two_dumps_in_the_same_second_stay_ordered(self, tmp_path):
        a = dump_buffer([_frame(0)], None, LAT, LON, tmp_path, now=T0)
        b = dump_buffer([_frame(1)], None, LAT, LON, tmp_path,
                        now=T0 + timedelta(microseconds=250))
        assert a != b and a.is_file() and b.is_file()
        assert newest_dump(tmp_path) == b

    def test_prune_sweeps_a_dead_write(self, tmp_path):
        dead = tmp_path / 'buffer_20260923_000000_000000.json.tmp'
        dead.write_text('half')
        (tmp_path / 'other.tmp').write_text('not ours')
        dump_buffer([_frame(0)], None, LAT, LON, tmp_path, now=T0)
        assert not dead.exists()
        assert (tmp_path / 'other.tmp').is_file()
        assert len(list_dumps(tmp_path)) == 1

    def test_empty_or_missing_dir(self, tmp_path):
        assert newest_dump(tmp_path) is None
        assert newest_dump(tmp_path / 'nope') is None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

class _Buffer:
    """Stand-in for the service state the trigger snapshots."""

    def __init__(self, frames):
        import threading
        self.lock = threading.Lock()
        self.frames = list(frames)
        self.model = None

    def snapshot(self):
        assert self.lock.locked(), "snapshot must run under the service lock"
        return self.frames, self.model, LAT, LON


class TestTrigger:

    def test_dump_now_on_an_empty_buffer_returns_none(self, tmp_path):
        buf = _Buffer([])
        trigger = BufferDumpTrigger(buf.lock, buf.snapshot, out_dir=tmp_path)
        assert trigger.dump_now() is None
        assert list_dumps(tmp_path) == []

    def test_dump_now_writes_and_returns_the_path(self, tmp_path):
        buf = _Buffer([_frame(0), _frame(1)])
        trigger = BufferDumpTrigger(buf.lock, buf.snapshot, out_dir=tmp_path)
        path = trigger.dump_now()
        assert path is not None and path.is_file()
        assert len(load_buffer(path)[0]) == 2

    @pytest.mark.parametrize('dev_mode, expected', [(False, 0), (True, 1)])
    def test_escape_dumps_only_in_a_dev_build(self, tmp_path, monkeypatch, dev_mode, expected):
        monkeypatch.setattr(bd, 'is_dev_mode_available', lambda: dev_mode)
        buf = _Buffer([_frame(0)])
        trigger = BufferDumpTrigger(buf.lock, buf.snapshot, out_dir=tmp_path)
        trigger.escape_started()
        trigger.wait()
        assert len(list_dumps(tmp_path)) == expected

    def test_exhaustion_dumps_off_the_calling_thread(self, tmp_path):
        import threading
        buf = _Buffer([_frame(0)])
        trigger = BufferDumpTrigger(buf.lock, buf.snapshot, out_dir=tmp_path)
        seen = []
        orig = bd.dump_buffer
        try:
            bd.dump_buffer = lambda *a, **k: seen.append(threading.current_thread()) or orig(*a, **k)
            trigger.exhaustion_reached()
            trigger.wait()
        finally:
            bd.dump_buffer = orig
        assert seen and seen[0] is not threading.main_thread()
        assert len(list_dumps(tmp_path)) == 1

    def test_write_failure_never_raises(self, tmp_path):
        blocker = tmp_path / 'file-not-a-dir'
        blocker.write_text('x')
        buf = _Buffer([_frame(0)])
        trigger = BufferDumpTrigger(buf.lock, buf.snapshot, out_dir=blocker)
        assert trigger.dump_now() is None
        trigger.exhaustion_reached()
        trigger.wait()


class TestServiceWiring:

    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        pytest.importorskip('PySide6')
        monkeypatch.setattr(app_config, 'get_allsky_buffer_dir', lambda create=True: str(tmp_path))
        from services.allsky.calibration_service import CalibrationService
        svc = CalibrationService()
        svc._save_model = lambda *a, **k: None
        svc._lat, svc._lon = LAT, LON
        return svc

    def test_dump_now(self, service, tmp_path):
        assert service.dump_now() is None
        service._frames = [_frame(0)]
        path = service.dump_now()
        assert path is not None and path.parent == tmp_path

    def test_exhaustion_dumps_once(self, service, tmp_path):
        service._frames = [_frame(0), _frame(1)]
        now = time.monotonic()
        for _ in range(ESCAPE_EXHAUSTION_THRESHOLD):
            service._escape_backoff.record_fruitless(now)
        service._warn_if_escape_exhausted()
        service._dump_trigger.wait()
        service._warn_if_escape_exhausted()
        service._dump_trigger.wait()
        assert len(list_dumps(tmp_path)) == 1

    def test_basin_escape_dumps_in_a_dev_build(self, service, tmp_path, monkeypatch):
        from services.allsky import calibration_service as cs

        class _Sig:
            def connect(self, *_):
                pass

        class _FakeWorker:
            result_ready = failed = incumbent_corroborated = finished = _Sig()

            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

        monkeypatch.setattr(cs, '_RefineWorker', _FakeWorker)
        monkeypatch.setattr(cs, 'incumbent_anchor_health', lambda *a, **k: None)
        monkeypatch.setattr(bd, 'is_dev_mode_available', lambda: True)
        service._model = _model()
        service._consecutive_refine_failures = cs.BASIN_ESCAPE_FAILURES
        service._frames = [_frame(i) for i in range(cs.MIN_FRAMES_BOOTSTRAP)]
        service._maybe_refine()
        assert service._escape_attempt is True
        service._dump_trigger.wait()
        assert len(list_dumps(tmp_path)) == 1
        service._refine_worker = None


# ---------------------------------------------------------------------------
# UI: the button and the controller status line
# ---------------------------------------------------------------------------

class TestUi:

    @pytest.fixture
    def qapp(self):
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_button_asks_main_window_to_dump(self, qapp):
        from ui.panels.allsky_settings import AllSkySettingsPanel
        panel = AllSkySettingsPanel()
        got = []
        panel.settings_changed.connect(got.append)
        panel._dump_btn.click()
        assert got == [{'_action': 'dump_buffer'}]
        assert panel._dump_btn.text() == 'Dump calibration buffer'
        panel.deleteLater()

    def test_button_icon_exists_in_the_bundled_font(self):
        import qtawesome
        fonts = os.path.join(os.path.dirname(qtawesome.__file__), 'fonts')
        charmap_file = next(f for f in os.listdir(fonts)
                            if f.startswith('materialdesignicons6') and f.endswith('.json'))
        with open(os.path.join(fonts, charmap_file), encoding='utf-8') as fh:
            assert 'database-export' in json.load(fh)

    def test_controller_reports_the_path_or_an_empty_buffer(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setattr(app_config, 'get_allsky_buffer_dir', lambda create=True: str(tmp_path))
        from ui.controllers.allsky_controller import AllSkyController

        class _Config:
            def get(self, key, default=None):
                return default

        class _MainWindow:
            config = _Config()

        ctrl = AllSkyController(_MainWindow())
        seen = []
        ctrl.status_changed.connect(seen.append)

        def wait_for(pred, timeout=5.0):
            deadline = time.monotonic() + timeout
            while not pred():
                assert time.monotonic() < deadline, seen
                qapp.processEvents()
                time.sleep(0.01)

        ctrl.dump_calibration_buffer()
        wait_for(lambda: any('empty' in m for m in seen))
        ctrl.calibration_service._frames = [_frame(0)]
        ctrl.dump_calibration_buffer()
        wait_for(lambda: any('saved: ' in m for m in seen))
        reported = next(m for m in seen if 'saved: ' in m).split('saved: ', 1)[1]
        assert Path(reported) == newest_dump(tmp_path)
        ctrl.shutdown()

    def test_double_click_spawns_one_writer(self, qapp, monkeypatch):
        import threading
        from ui.controllers.allsky_controller import AllSkyController

        class _Config:
            def get(self, key, default=None):
                return default

        class _MainWindow:
            config = _Config()

        ctrl = AllSkyController(_MainWindow())
        release = threading.Event()
        calls = []

        def slow_dump():
            calls.append(1)
            release.wait(5.0)
            return None

        monkeypatch.setattr(ctrl.calibration_service, 'dump_now', slow_dump)
        seen = []
        ctrl.status_changed.connect(seen.append)
        ctrl.dump_calibration_buffer()
        ctrl.dump_calibration_buffer()
        deadline = time.monotonic() + 5.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        release.set()
        deadline = time.monotonic() + 5.0
        while ctrl._dump_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == [1]
        assert sum('Saving' in m for m in seen) == 1
        ctrl.dump_calibration_buffer()
        while ctrl._dump_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == [1, 1], "a finished dump does not block the next"
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# Library loader
# ---------------------------------------------------------------------------

def _load_script():
    spec = importlib.util.spec_from_file_location('library_to_buffer', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sky_jpeg(path: Path, size: int = 240, seed: int = 0, text_box=None) -> None:
    """A fisheye-ish disc with ~40 stars; optional bright 'text box'."""
    rng = np.random.default_rng(seed)
    cx = cy = size / 2
    radius = size * 0.45
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    rr = np.hypot(xx - cx, yy - cy)
    img = np.where(rr <= radius, 70.0 * (1 - 0.4 * (rr / radius) ** 2), 0.0).astype(np.float32)
    for _ in range(40):
        theta = rng.uniform(0, 2 * np.pi)
        rad = rng.uniform(0, radius * 0.85)
        x, y = int(cx + rad * np.cos(theta)), int(cy + rad * np.sin(theta))
        img[y - 1:y + 2, x - 1:x + 2] += rng.uniform(80, 180)
    img += rng.normal(0, 2.0, img.shape)
    if text_box:
        x, y, w, h = text_box
        img[y:y + h, x:x + w] = 20.0
        img[y + 2:y + h - 2:3, x + 2:x + w - 2:3] = 255.0   # glyph-like specks
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert('RGB').save(
        path, 'JPEG', quality=85)


@pytest.fixture
def library(tmp_path):
    """Two open-roof frames and one closed, indexed in a real library.db."""
    from services.library.index import LibraryIndex
    night = tmp_path / 'Library' / '2026-09-17'
    night.mkdir(parents=True)
    base = int(datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc).timestamp())
    stamps = [('20260917_210000_aaaa0001', base, '12.5s', 'Open'),
              ('20260917_210500_aaaa0002', base + 300, '500ms', 'Closed'),
              ('20260917_211000_aaaa0003', base + 600, '20.66', 'Open (97%)')]
    idx = LibraryIndex(str(tmp_path / 'Library' / 'library.db'))
    for i, (name, epoch, exposure, roof) in enumerate(stamps):
        _sky_jpeg(night / f'{name}.jpg', seed=i)
        idx.insert({'captured_at': epoch, 'path': f'2026-09-17/{name}.jpg',
                    'width': 240, 'height': 240, 'bytes': 1, 'created_at': epoch,
                    'exposure': exposure, 'roof': roof})
    idx.close()
    return night


class TestLibraryLoader:

    def test_night_becomes_a_loadable_dump(self, library, tmp_path):
        mod = _load_script()
        out = tmp_path / 'buffer_night.json'
        model_path = tmp_path / 'cal.json'
        _model(image_width=3552, image_height=3552, a1=1259.2).save(str(model_path))
        assert mod.main([str(library), '--lat', str(LAT), '--lon', str(LON),
                         '--out', str(out), '--model', str(model_path)]) == 0

        frames, model_dict, lat, lon = load_buffer(out)
        assert (lat, lon) == (LAT, LON)
        assert len(frames) == 2, "the closed-roof frame is skipped"
        assert [f['dt'] for f in frames] == [
            datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 18, 4, 10, tzinfo=timezone.utc)]
        assert [f['exposure_s'] for f in frames] == [12.5, 20.66]
        for f in frames:
            assert len(f['detected']) >= mod.MIN_DETECTIONS
            assert (f['image_width'], f['image_height']) == (240, 240)
            assert 0 < f['sky_r'] < 120 and f['above_horizon']
        model = model_from_dict(model_dict)
        assert (model.image_width, model.image_height) == (240, 240)
        assert model.a1 == pytest.approx(1259.2 * 240 / 3552)

    def test_without_a_database_times_come_from_the_names(self, library, tmp_path):
        mod = _load_script()
        os.remove(library.parent / 'library.db')
        out = tmp_path / 'b.json'
        assert mod.main([str(library), '--lat', str(LAT), '--lon', str(LON),
                         '--tz', 'America/Los_Angeles', '--out', str(out)]) == 0
        frames, *_ = load_buffer(out)
        assert len(frames) == 3, "no roof column to skip on"
        assert frames[0]['dt'] == datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)
        assert 'exposure_s' not in frames[0]

    def test_ignore_rect_blanks_a_burned_in_box(self, tmp_path):
        mod = _load_script()
        path = tmp_path / '20260917_210000_aaaa0001.jpg'
        _sky_jpeg(path, text_box=(90, 100, 60, 24))
        with Image.open(path) as image:
            noisy = mod.detect_frame(image, T0, LAT, LON)
            clean = mod.detect_frame(image, T0, LAT, LON, rects=[(90, 100, 60, 24)])

        def in_box(frame):
            return sum(1 for x, y, _ in frame['detected'] if 90 <= x < 150 and 100 <= y < 124)

        assert in_box(noisy) > 0
        assert in_box(clean) == 0
        assert len(clean['detected']) >= mod.MIN_DETECTIONS

    @pytest.mark.parametrize('text, expected', [
        ('20.66s', 20.66), ('20.66', 20.66), ('500ms', 0.5), (' 1.5 s ', 1.5),
        (None, None), ('N/A', None),
    ])
    def test_parse_exposure(self, text, expected):
        assert _load_script().parse_exposure_s(text) == expected
