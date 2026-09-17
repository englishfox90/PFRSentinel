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


@pytest.fixture
def fake_app_data_root(monkeypatch, tmp_path):
    """Redirect the shared resolver into tmp_path.

    Without this the icon cache is created in the developer's real app-data
    root every time the suite runs. The resolver's own platform behaviour is
    covered by tests/test_utils_paths.py; what belongs here is only that
    weather.py builds on top of it instead of reading the environment itself.
    """
    root = tmp_path / 'app_data_root'
    root.mkdir()
    # raising=False so that against code which does NOT delegate to the resolver
    # the patch is simply inert and the test fails on the real assertion below,
    # rather than erroring here about a missing attribute.
    monkeypatch.setattr(weather_mod, 'get_app_data_dir', lambda: str(root),
                        raising=False)
    return root


def test_icon_cache_dir_resolves_without_localappdata(
    offline_service, fake_app_data_root, monkeypatch, tmp_path
):
    """With LOCALAPPDATA unset the icon cache directory must still be created,
    under whatever the shared resolver returns — not raise a TypeError that the
    surrounding except swallows, and not fall back to the working directory.

    makedirs is spied rather than only checking the directory afterwards: the
    attempt is the thing that distinguishes fixed from unfixed code.
    """
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    created = []
    real_makedirs = os.makedirs

    def _spy(path, *args, **kwargs):
        created.append(path)
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(os, 'makedirs', _spy)

    assert offline_service.get_weather_icon_path() is None   # no weather data

    expected = fake_app_data_root / 'weather_icons'
    assert str(expected) in created, \
        "icon cache directory was never created — resolution raised"
    assert os.path.isabs(str(expected))
    assert expected.is_dir()
    assert not list(cwd.iterdir()), "nothing may be written to the cwd"
