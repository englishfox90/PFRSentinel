"""
Tests for services.weather: icon-cache path resolution and log redaction.

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


# --- Log redaction -----------------------------------------------------------
#
# str() of a requests HTTPError / ConnectionError carries the request URL, and
# every OpenWeatherMap query has the API key (appid) plus the observer's exact
# position (lat/lon) or city (q) in the query string. Logs go into support
# bundles whose config copy redacts exactly those keys, so a raw exception in
# the log would hand back what the bundle just removed.

SECRET_KEY = 'sk0123456789abcdef'
LEAKY_URL = (
    'https://api.openweathermap.org/data/2.5/weather'
    f'?lat=51.5074&lon=-0.1278&q=London%2CGB&appid={SECRET_KEY}&units=metric'
)
LEAKY_POOL_MSG = (
    "HTTPSConnectionPool(host='api.openweathermap.org', port=443): "
    "Max retries exceeded with url: /data/2.5/weather"
    f"?lat=51.5074&lon=-0.1278&q=London%2CGB&appid={SECRET_KEY}&units=metric "
    "(Caused by NewConnectionError)"
)
SECRETS = [SECRET_KEY, '51.5074', '-0.1278', 'London']


def _http_error(status, reason):
    response = weather_mod.requests.Response()
    response.status_code = status
    response.reason = reason
    return weather_mod.requests.HTTPError(
        f"{status} Client Error: {reason} for url: {LEAKY_URL}", response=response)


@pytest.fixture
def logged_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(weather_mod.app_logger, 'error', errors.append)
    monkeypatch.setattr(weather_mod.app_logger, 'info', lambda msg: None)
    return errors


def test_describe_weather_error_reports_http_status_not_url():
    out = weather_mod.describe_weather_error(_http_error(401, 'Unauthorized'))
    assert out == 'HTTP 401 Unauthorized'
    for secret in SECRETS:
        assert secret not in out


def test_describe_weather_error_strips_query_secrets_from_connection_errors():
    out = weather_mod.describe_weather_error(
        weather_mod.requests.ConnectionError(LEAKY_POOL_MSG))
    for secret in SECRETS:
        assert secret not in out
    assert out.startswith('ConnectionError: ')
    assert 'Max retries exceeded' in out
    assert 'units=metric' in out


def test_describe_weather_error_leaves_lookalike_params_alone():
    out = weather_mod.describe_weather_error(
        weather_mod.requests.ConnectionError('flat=1&freq=2&format=json'))
    assert out == 'ConnectionError: flat=1&freq=2&format=json'


def test_resolve_location_failure_never_logs_key_or_city(monkeypatch, logged_errors):
    def _raise(*args, **kwargs):
        raise _http_error(401, 'Unauthorized')
    monkeypatch.setattr(weather_mod.requests, 'get', _raise)

    service = WeatherService(api_key=SECRET_KEY, location='London,GB')
    assert service.resolve_location() is False

    assert len(logged_errors) == 1
    assert 'HTTP 401 Unauthorized' in logged_errors[0]
    for secret in SECRETS:
        assert secret not in logged_errors[0]


def test_fetch_weather_failure_never_logs_coordinates(monkeypatch, logged_errors):
    def _raise(*args, **kwargs):
        raise weather_mod.requests.ConnectionError(LEAKY_POOL_MSG)
    monkeypatch.setattr(weather_mod.requests, 'get', _raise)

    service = WeatherService(api_key=SECRET_KEY, location='',
                             latitude=51.5074, longitude=-0.1278)
    assert service.fetch_weather() is None

    joined = ' '.join(logged_errors)
    assert 'ConnectionError' in joined
    for secret in SECRETS:
        assert secret not in joined
