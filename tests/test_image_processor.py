"""
Worker-integration tests for ui/controllers/image_processor.py.

The roof-safety confirmation FSM itself is unit-tested in test_ascom_safety.py
(RoofSafetyFSM). Here we assert the WIRING through the image-processing worker:
- ML enabled + safety enabled routes confirmed results through the FSM,
- the fail-safe still runs when ML is DISABLED but the safety file is enabled,
- a hard inference failure routes an UNSAFE verdict,
- a genuine write failure escalates via the worker's operator-visible channels.
"""
import pytest

# A QApplication is needed before constructing the QThread-derived worker.
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PIL import Image
from services.notifications import ERROR
from ui.controllers.image_processor import ImageProcessorWorker, ImageProcessingTask


@pytest.fixture(scope="module")
def _qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def worker(_qapp):
    w = ImageProcessorWorker()
    yield w
    # Never .start()ed here (tests call _process_task directly), so this is
    # just deterministic disposal of the QThread-derived QObject itself.
    from PySide6.QtCore import QEvent
    w.deleteLater()
    _qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _qapp.processEvents()


class _NotifierStub:
    """Captures NotificationEvents instead of delivering them."""

    def __init__(self):
        self.events = []

    def notify(self, event):
        self.events.append(event)


def _ascom(tmp_path, **over):
    cfg = {'enabled': True, 'file_path': str(tmp_path / 'roof.txt'),
           'min_confidence': 0.7, 'heartbeat_seconds': 0}
    cfg.update(over)
    return cfg


def _base_config(tmp_path, ml_models):
    return {
        'output_dir': str(tmp_path),
        'output_format': 'PNG',
        'resize_percent': 100,
        'auto_stretch': {'enabled': False},
        'overlays': [],
        'dev_mode': {'enabled': False},
        'ml_contribution': {'enabled': False},
        'meteor': {},
        'sharpening': {},
        'allsky_overlay': {},
        'weather': {},
        'ml_models': ml_models,
    }


def test_ml_disabled_but_ascom_enabled_writes_unsafe(worker, tmp_path):
    # Item 2: even with ML disabled, an enabled safety file must not freeze at a
    # stale OPEN — the fail-safe routes UNSAFE through the FSM.
    writes = []
    worker._safety_fsm._writer = lambda ml, cfg: (writes.append(dict(ml)), True)[1]
    worker._main_window = None

    cfg = _base_config(tmp_path, {
        'enabled': False,
        'ascom_safety_file': _ascom(tmp_path),
    })
    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (32, 32), (40, 40, 40)), {'FILENAME': 'x.png'}, cfg))

    assert len(writes) == 1
    assert writes[0]['roof_status'] == 'N/A'  # UNSAFE baseline


def test_inference_exception_writes_unsafe(worker, monkeypatch, tmp_path):
    # A hard inference failure (model blind) must route an UNSAFE verdict.
    monkeypatch.setattr('ui.controllers.image_processor.get_ml_service',
                        lambda: (_ for _ in ()).throw(RuntimeError("blind")))
    writes = []
    worker._safety_fsm._writer = lambda ml, cfg: (writes.append(dict(ml)), True)[1]
    worker._main_window = None

    cfg = _base_config(tmp_path, {
        'enabled': True,
        'ascom_safety_file': _ascom(tmp_path),
    })
    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (32, 32), (40, 40, 40)), {'FILENAME': 'x.png'}, cfg))

    assert len(writes) == 1
    assert writes[0]['roof_status'] == 'N/A'


def test_confident_open_via_worker_routes_through_fsm(worker, tmp_path, monkeypatch):
    # ML produces a confident Open: it must go through the FSM (which, per
    # Policy A, writes UNSAFE first and does NOT certify SAFE on one frame).
    writes = []
    worker._safety_fsm._writer = lambda ml, cfg: (writes.append(dict(ml)), True)[1]
    worker._main_window = None

    class _Svc:
        def is_available(self):
            return True

        def initialize(self):
            return True

        def get_last_results(self):
            return {'roof_status': 'Open', 'roof_confidence': 0.95}

    monkeypatch.setattr('ui.controllers.image_processor.get_ml_service', lambda: _Svc())
    monkeypatch.setattr('ui.controllers.image_processor.analyze_image_for_tokens',
                        lambda arr, config=None: {'ROOF_STATUS': 'Open'})

    cfg = _base_config(tmp_path, {
        'enabled': True,
        'ascom_safety_file': _ascom(tmp_path),
    })
    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (32, 32), (40, 40, 40)), {'FILENAME': 'x.png'}, cfg))

    # First confident-Open frame writes the UNSAFE baseline, NOT SAFE/OPEN.
    assert len(writes) == 1
    assert writes[0]['roof_status'] != 'Open'
    assert worker._safety_fsm._confirmed_safe is False


def test_genuine_write_failure_escalates_via_worker(worker, monkeypatch, tmp_path):
    captured = []
    notifier = _NotifierStub()
    monkeypatch.setattr('services.posthog_service.capture_error',
                        lambda exc, context=None: captured.append(context))
    worker._main_window = type('MW', (), {'config': {}, 'notifier': notifier})()
    worker._safety_fsm._writer = lambda ml, cfg: False  # genuine failure

    worker._safety_fsm.update({'roof_status': 'Closed', 'roof_confidence': 0.9},
                              _ascom(tmp_path))

    assert len(notifier.events) == 1
    assert notifier.events[0].type == ERROR
    assert 'stale' in notifier.events[0].body
    assert captured == ['ascom_safety_write']


# ---------------------------------------------------------------------------
# Deferred frame construction (reprocess rebuilds on the worker thread)
# ---------------------------------------------------------------------------

def test_frame_factory_runs_on_worker_and_result_is_processed(worker, tmp_path):
    worker._main_window = None
    calls = []

    def factory():
        calls.append(1)
        return Image.new('RGB', (16, 12), (40, 50, 60)), {'FILENAME': 'rebuilt.png'}

    task = ImageProcessingTask(None, {'FILENAME': 'stale.png'}, _base_config(tmp_path, {'enabled': False}),
                               frame_factory=factory)
    saved = []
    worker.processing_complete.connect(lambda p, o, m, path, d: saved.append((m, path)))

    worker._process_task(task)

    assert calls == [1]
    assert task.frame_factory is None  # consumed, not retained
    assert saved and saved[0][0]['FILENAME'] == 'rebuilt.png'
    assert saved[0][1].endswith('rebuilt.png')


def test_frame_factory_returning_nothing_skips_the_frame(worker, tmp_path):
    worker._main_window = None
    task = ImageProcessingTask(None, {}, _base_config(tmp_path, {'enabled': False}),
                               frame_factory=lambda: (None, None))
    errors, done = [], []
    worker.error_occurred.connect(errors.append)
    worker.processing_complete.connect(lambda *a: done.append(a))

    worker._process_task(task)

    assert errors == [] and done == []


def test_task_takes_ownership_of_the_image_without_copying():
    img = Image.new('RGB', (4, 4))
    assert ImageProcessingTask(img, {}, {}).img is img


# ---------------------------------------------------------------------------
# Preview downscale — the LANCZOS resize belongs on the worker, not the GUI
# thread. Only the GUI preview slot shrinks; every full-res consumer keeps its
# pixels.
# ---------------------------------------------------------------------------

def test_gui_preview_is_capped_while_output_and_dispatch_stay_full_res(worker, tmp_path):
    from services.preview_scaling import PREVIEW_MAX_PX

    worker._main_window = None
    big = Image.new('RGB', (2628, 2628), (30, 40, 50))
    results = []
    worker.processing_complete.connect(
        lambda preview, out, meta, path, dispatch: results.append((preview, out, dispatch)))

    worker._process_task(ImageProcessingTask(
        big, {'FILENAME': 'big.png'}, _base_config(tmp_path, {'enabled': False})))

    assert results, "processing_complete did not fire"
    preview, output, dispatch = results[0]
    assert max(preview.size) == PREVIEW_MAX_PX
    assert output.size == (2628, 2628), "output image must keep full resolution"
    assert dispatch.size == (2628, 2628), "web/Library dispatch must keep full resolution"


def test_timelapse_and_detection_frames_are_not_downscaled_by_the_preview_cap(worker, tmp_path):
    worker._main_window = None
    big = Image.new('RGB', (2628, 2628), (30, 40, 50))
    cfg = _base_config(tmp_path, {'enabled': False})
    cfg['meteor'] = {'enabled': True, 'detection_long_side': 1280}

    timelapse, detection = [], []
    worker.timelapse_ready.connect(lambda clean, overlaid: timelapse.append((clean, overlaid)))
    worker.detection_frame_ready.connect(lambda det, full: detection.append((det, full)))

    worker._process_task(ImageProcessingTask(big, {'FILENAME': 'big.png'}, cfg))

    assert timelapse and detection
    clean, overlaid = timelapse[0]
    assert clean.size == (2628, 2628) and overlaid.size == (2628, 2628)
    det_frame, full_clean = detection[0]
    assert max(det_frame.size) == 1280  # its own detection scale, untouched
    assert full_clean.size == (2628, 2628)


def test_saved_file_keeps_full_resolution(worker, tmp_path):
    worker._main_window = None
    big = Image.new('RGB', (2628, 2628), (30, 40, 50))
    paths = []
    worker.processing_complete.connect(lambda p, o, m, path, d: paths.append(path))

    worker._process_task(ImageProcessingTask(
        big, {'FILENAME': 'big.png'}, _base_config(tmp_path, {'enabled': False})))

    assert Image.open(paths[0]).size == (2628, 2628)


def test_small_frame_reaches_the_preview_untouched(worker, tmp_path):
    worker._main_window = None
    small = Image.new('RGB', (640, 480), (30, 40, 50))
    results = []
    worker.processing_complete.connect(lambda p, o, m, path, d: results.append((p, o)))

    worker._process_task(ImageProcessingTask(
        small, {'FILENAME': 'small.png'}, _base_config(tmp_path, {'enabled': False})))

    preview, output = results[0]
    assert preview.size == (640, 480) and output.size == (640, 480)


# ---------------------------------------------------------------------------
# Output framing (issue #12) — the crop is an OUTPUT-stage cut: every analysis
# frame stays full size, only the rendered destinations shrink.
# ---------------------------------------------------------------------------

def _crop_config(tmp_path, **over):
    cfg = _base_config(tmp_path, {'enabled': False})
    crop = {'enabled': True, 'x': 40, 'y': 100, 'width': 200, 'height': 200,
            'ref_width': 400, 'ref_height': 400}
    crop.update(over)
    cfg['output_crop'] = crop
    return cfg


def test_crop_applies_to_every_output_destination(worker, tmp_path):
    worker._main_window = None
    results, timelapse = [], []
    worker.processing_complete.connect(
        lambda p, o, m, path, d: results.append((o, m, path, d)))
    worker.timelapse_ready.connect(lambda clean, out: timelapse.append((clean, out)))

    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (400, 400), (30, 40, 50)), {'FILENAME': 'f.png'},
        _crop_config(tmp_path)))

    assert results, "processing_complete did not fire"
    output_img, metadata, path, dispatch_img = results[0]
    assert output_img.size == (200, 200)
    assert dispatch_img.size == (200, 200)
    assert Image.open(path).size == (200, 200)
    assert timelapse and timelapse[0][1].size == (200, 200)
    assert metadata['OUTPUT_CROP'] == {
        'x': 40, 'y': 100, 'width': 200, 'height': 200,
        'frame_width': 400, 'frame_height': 400,
    }


def test_crop_leaves_the_analysis_frames_full_size(worker, tmp_path):
    worker._main_window = None
    cfg = _crop_config(tmp_path)
    cfg['meteor'] = {'enabled': True, 'detection_long_side': 128}

    timelapse, detection = [], []
    worker.timelapse_ready.connect(lambda clean, out: timelapse.append(clean))
    worker.detection_frame_ready.connect(lambda det, full: detection.append((det, full)))

    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (400, 400), (30, 40, 50)), {'FILENAME': 'f.png'}, cfg))

    # The timelapse is the point of issue #12: its clean frame is cut to the
    # box even with include_overlays off. Meteor detection stays full frame.
    assert timelapse and timelapse[0].size == (200, 200)
    assert detection
    det_frame, full_clean = detection[0]
    assert max(det_frame.size) == 128
    assert full_clean.size == (400, 400)


def test_crop_box_is_scaled_when_the_frame_differs_from_the_reference(worker, tmp_path):
    # resize_percent halves the frame before the crop; the reference box must
    # follow, and the metadata must describe the frame it was actually cut from.
    worker._main_window = None
    cfg = _crop_config(tmp_path)
    cfg['resize_percent'] = 50
    results = []
    worker.processing_complete.connect(lambda p, o, m, path, d: results.append((o, m)))

    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (400, 400), (30, 40, 50)), {'FILENAME': 'f.png'}, cfg))

    output_img, metadata = results[0]
    assert output_img.size == (100, 100)
    assert metadata['OUTPUT_CROP'] == {
        'x': 20, 'y': 50, 'width': 100, 'height': 100,
        'frame_width': 200, 'frame_height': 200,
    }


@pytest.mark.parametrize("over", [{'enabled': False}, {'width': 0, 'height': 0}])
def test_disabled_or_zero_crop_changes_nothing_and_clears_stale_metadata(
        worker, tmp_path, over):
    worker._main_window = None
    results = []
    worker.processing_complete.connect(lambda p, o, m, path, d: results.append((o, m, path)))
    metadata = {'FILENAME': 'f.png', 'OUTPUT_CROP': {'x': 1, 'y': 2, 'width': 3,
                                                     'height': 4, 'frame_width': 5,
                                                     'frame_height': 6}}

    task = ImageProcessingTask(Image.new('RGB', (400, 400), (30, 40, 50)),
                               metadata, _crop_config(tmp_path, **over))
    worker._process_task(task)

    output_img, emitted, path = results[0]
    assert output_img.size == (400, 400)
    assert Image.open(path).size == (400, 400)
    assert 'OUTPUT_CROP' not in emitted


# ---------------------------------------------------------------------------
# reprocess=True (issue #12 review fix) — a reprocess is the same capture
# again, not a new frame in the time series: timelapse/meteor must not see it,
# but everything else (save, processing_complete, preview_ready) still fires.
# ---------------------------------------------------------------------------

def test_reprocess_suppresses_timelapse_and_detection_but_not_other_signals(worker, tmp_path):
    worker._main_window = None
    cfg = _base_config(tmp_path, {'enabled': False})
    cfg['meteor'] = {'enabled': True, 'detection_long_side': 128}

    timelapse, detection, complete, preview = [], [], [], []
    worker.timelapse_ready.connect(lambda *a: timelapse.append(a))
    worker.detection_frame_ready.connect(lambda *a: detection.append(a))
    worker.processing_complete.connect(lambda *a: complete.append(a))
    worker.preview_ready.connect(lambda *a: preview.append(a))

    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (64, 64), (30, 40, 50)), {'FILENAME': 'f.png'}, cfg,
        reprocess=True))

    assert timelapse == [], "reprocess must not feed the timelapse a duplicate frame"
    assert detection == [], "reprocess must not feed the meteor stack a duplicate frame"
    assert complete, "processing_complete must still fire on reprocess"
    assert preview, "preview_ready must still fire on reprocess"


def test_non_reprocess_frame_still_emits_timelapse_and_detection(worker, tmp_path):
    # Control for the test above — the suppression is reprocess-specific, not
    # a general regression in the meteor/timelapse wiring.
    worker._main_window = None
    cfg = _base_config(tmp_path, {'enabled': False})
    cfg['meteor'] = {'enabled': True, 'detection_long_side': 128}

    timelapse, detection = [], []
    worker.timelapse_ready.connect(lambda *a: timelapse.append(a))
    worker.detection_frame_ready.connect(lambda *a: detection.append(a))

    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (64, 64), (30, 40, 50)), {'FILENAME': 'f.png'}, cfg,
        reprocess=False))

    assert timelapse and detection


def test_task_reprocess_flag_defaults_false_and_is_stored():
    assert ImageProcessingTask(None, {}, {}).reprocess is False
    assert ImageProcessingTask(None, {}, {}, reprocess=True).reprocess is True


def test_preview_ready_native_size_is_captured_before_resize(worker, tmp_path):
    worker._main_window = None
    cfg = _base_config(tmp_path, {'enabled': False})
    cfg['resize_percent'] = 50

    preview = []
    worker.preview_ready.connect(lambda img, hist: preview.append((img, hist)))

    worker._process_task(ImageProcessingTask(
        Image.new('RGB', (400, 300), (30, 40, 50)), {'FILENAME': 'f.png'}, cfg))

    assert preview
    img, hist_data = preview[0]
    assert hist_data['native_size'] == (400, 300)
    assert img.size == (200, 150), "the emitted preview image is still the resized frame"


def test_process_and_save_forwards_reprocess_flag_to_the_task(_qapp, tmp_path):
    from PySide6.QtCore import QEvent

    from ui.controllers.image_processor import ImageProcessor

    proc = ImageProcessor()
    try:
        proc._main_window = type('MW', (), {'config': {'output_directory': str(tmp_path)}})()
        queued = []
        proc._worker.queue_task = queued.append

        proc.process_and_save(Image.new('RGB', (4, 4)), {'FILENAME': 'x.png'}, reprocess=True)
        proc.process_and_save(Image.new('RGB', (4, 4)), {'FILENAME': 'y.png'})

        assert len(queued) == 2
        assert queued[0].reprocess is True
        assert queued[1].reprocess is False
    finally:
        proc._worker.deleteLater()
        proc.deleteLater()
        _qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _qapp.processEvents()


def test_worker_reuses_one_overlay_image_cache_across_frames(worker, tmp_path):
    """The worker must hand the SAME cache dict to add_overlays every frame —
    a per-frame dict would re-decode the user's logo on every capture."""
    seen = []
    import ui.controllers.image_processor as ip

    def _spy(img, overlays, metadata, image_cache=None, weather_service=None):
        seen.append(image_cache)
        return img

    original = ip.add_overlays
    ip.add_overlays = _spy
    try:
        worker._main_window = None
        cfg = _base_config(tmp_path, {'enabled': False})
        for _ in range(2):
            worker._process_task(ImageProcessingTask(
                Image.new('RGB', (32, 32)), {'FILENAME': 'x.png'}, cfg))
    finally:
        ip.add_overlays = original

    assert len(seen) == 2
    assert seen[0] is not None and seen[0] is seen[1]
    assert seen[0] is worker._overlay_image_cache


def test_reprocess_marks_the_capture_so_per_capture_state_is_not_advanced(worker, tmp_path, monkeypatch):
    """The roof gate counts consecutive Closed frames. A reprocess re-runs the
    same capture from fresh metadata, so the processor has to say so — or one
    misread frame confirms itself the moment a setting is nudged."""
    from services import observing_window
    from services.observing_window import SAME_CAPTURE_KEY

    worker._main_window = None
    cfg = _base_config(tmp_path, {'enabled': False})
    seen = []
    real = observing_window.is_observing_window

    def spy(config, metadata, feature="feature"):
        seen.append(bool(metadata.get(SAME_CAPTURE_KEY)))
        return real(config, metadata, feature=feature)

    monkeypatch.setattr(observing_window, 'is_observing_window', spy)

    for reprocess in (False, True):
        seen.clear()
        worker._process_task(ImageProcessingTask(
            Image.new('RGB', (64, 64), (30, 40, 50)), {'FILENAME': 'f.png'}, cfg,
            reprocess=reprocess))
        assert seen, "the gate was never consulted"
        assert all(flag is reprocess for flag in seen)
