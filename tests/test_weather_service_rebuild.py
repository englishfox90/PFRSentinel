"""A rebuilt WeatherService reaches the live holders (issue #125).

`_init_weather_service` builds a fresh instance on every settings edit, but
the processing worker copied the start-up instance and `FileWatcher` (and the
handler it schedules) copied the one current when the watch started, so a
units change never reached the overlays until the app was restarted. The
worker now reads the main window's service per frame, and the rebuild hands
the new instance to a live watcher.
"""
import types

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PIL import Image

from services.config import Config
from services.watcher import FileWatcher, ImageFileHandler
from ui.main_window.settings import _MainWindowSettingsMixin


@pytest.fixture
def no_network(monkeypatch):
    import requests

    def _boom(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(requests, "get", _boom)


def _window(tmp_path, watch_controller=None, **weather):
    config = Config(str(tmp_path / "config.json"))
    config.set('weather', {**config.get('weather', {}), 'api_key': 'k', 'location': 'Austin, US',
                           'units': 'imperial', **weather})
    win = types.SimpleNamespace(config=config, weather_service=None, watch_controller=watch_controller)
    win._init_weather_service = types.MethodType(_MainWindowSettingsMixin._init_weather_service, win)
    return win


def test_set_weather_service_reaches_the_scheduled_handler():
    watcher = FileWatcher(config=None)
    watcher.handler = ImageFileHandler(config=None)
    replacement = object()

    watcher.set_weather_service(replacement)

    assert watcher.weather_service is replacement
    assert watcher.handler.weather_service is replacement


def test_rebuild_hands_the_new_service_to_a_running_watcher(tmp_path, no_network):
    watcher = FileWatcher(config=None, weather_service=object())
    win = _window(tmp_path, watch_controller=types.SimpleNamespace(watcher=watcher))

    win._init_weather_service()

    assert win.weather_service is not None
    assert watcher.weather_service is win.weather_service
    assert watcher.weather_service.units == 'imperial'


def test_rebuild_clears_a_running_watchers_service_when_weather_is_unconfigured(tmp_path, no_network):
    watcher = FileWatcher(config=None, weather_service=object())
    win = _window(tmp_path, watch_controller=types.SimpleNamespace(watcher=watcher), api_key='')

    win._init_weather_service()

    assert win.weather_service is None
    assert watcher.weather_service is None


@pytest.mark.parametrize("watch_controller", [None, types.SimpleNamespace(watcher=None)])
def test_rebuild_without_a_running_watcher_is_a_plain_rebuild(tmp_path, no_network, watch_controller):
    win = _window(tmp_path, watch_controller=watch_controller)

    win._init_weather_service()

    assert win.weather_service.units == 'imperial'


def test_worker_reads_the_main_windows_current_service_each_frame(tmp_path):
    from PySide6.QtCore import QEvent
    import ui.controllers.image_processor as ip

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    worker = ip.ImageProcessorWorker()
    seen = []

    def _spy(img, overlays, metadata, image_cache=None, weather_service=None):
        seen.append(weather_service)
        return img

    first, second = object(), object()
    worker._main_window = types.SimpleNamespace(weather_service=first)
    cfg = {'output_dir': str(tmp_path), 'output_format': 'PNG', 'resize_percent': 100,
           'auto_stretch': {'enabled': False}, 'overlays': [], 'dev_mode': {'enabled': False},
           'ml_contribution': {'enabled': False}, 'meteor': {}, 'sharpening': {},
           'allsky_overlay': {}, 'weather': {}, 'ml_models': {'enabled': False}}
    original = ip.add_overlays
    ip.add_overlays = _spy
    try:
        for service in (first, second):
            worker._main_window.weather_service = service
            worker._process_task(ip.ImageProcessingTask(
                Image.new('RGB', (32, 32)), {'FILENAME': 'x.png'}, cfg))
    finally:
        ip.add_overlays = original
        worker.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    assert seen == [first, second]
