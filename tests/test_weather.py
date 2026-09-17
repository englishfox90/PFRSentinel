"""
Tests for services.weather path resolution.

The weather icon cache used to be built from a bare os.getenv('LOCALAPPDATA')
with no default: off Windows that passed None to os.path.join, raised
TypeError, and the surrounding try/except swallowed it — the icon cache
silently stopped working. It must now resolve through the shared app-data
resolver on every platform.

No network: fetch_weather is stubbed and requests.get is booby-trapped.
"""
import os
import sys
from pathlib import Path

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import services.weather as weather_mod
from services.utils_paths import get_app_data_dir
from services.weather import WeatherService


@pytest.fixture
def no_network(monkeypatch):
    """Any outbound HTTP from these tests is a bug — fail loudly."""
    def _boom(*args, **kwargs):
        raise AssertionError("weather tests must not touch the network")

    monkeypatch.setattr(weather_mod.requests, 'get', _boom)


@pytest.fixture
def offline_service(monkeypatch, no_network):
    service = WeatherService(api_key='test-key', location='London')
    monkeypatch.setattr(service, 'fetch_weather', lambda: None)
    return service


def test_icon_cache_dir_resolves_without_localappdata(
    offline_service, monkeypatch, tmp_path
):
    """With LOCALAPPDATA unset the icon cache directory must still be created,
    at an absolute path outside the process working directory — not raise a
    TypeError that the surrounding except swallows.

    makedirs is spied rather than asserting on the real directory: the app-data
    root survives between runs, so an isdir() check would pass on stale state.
    The resolver creates the root itself, so more than one call is expected.
    """
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.chdir(tmp_path)

    created = []
    real_makedirs = os.makedirs

    def _spy(path, *args, **kwargs):
        created.append(path)
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(os, 'makedirs', _spy)

    assert offline_service.get_weather_icon_path() is None   # no weather data

    icon_dirs = [p for p in created if os.path.basename(p) == 'weather_icons']
    assert icon_dirs, "icon cache directory was never created — resolution raised"
    icon_dir = icon_dirs[0]
    assert os.path.isabs(icon_dir)
    assert tmp_path not in Path(icon_dir).parents
    assert os.path.dirname(icon_dir) == get_app_data_dir()
    assert not list(tmp_path.iterdir()), "nothing may be written to the cwd"
