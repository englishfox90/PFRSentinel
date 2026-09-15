"""Tests for services/weather.py error redaction.

OpenWeatherMap takes its credential as the ``appid`` query parameter, so every
``requests`` exception message carries the operator's API key in clear text.
These tests pin that it never reaches the logger.
"""
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

requests = pytest.importorskip("requests")


API_KEY = "0123456789abcdef0123456789abcdef"

# The two shapes requests uses: a full URL for an HTTP status error, and a
# scheme-less bare path for a connection error.
FULL_URL_MSG = (
    "401 Client Error: Unauthorized for url: https://api.openweathermap.org"
    f"/data/2.5/weather?lat=51.5&lon=-0.1&appid={API_KEY}&units=metric"
)
BARE_PATH_MSG = (
    "HTTPSConnectionPool(host='api.openweathermap.org', port=443): Max retries "
    f"exceeded with url: /data/2.5/weather?lat=51.5&lon=-0.1&appid={API_KEY}"
    "&units=metric (Caused by NewConnectionError('failed to resolve'))"
)


class TestWeatherErrorRedaction:
    """redact_weather_error() strips the appid value from any error text."""

    def test_strips_appid_from_bare_path(self):
        from services.weather import redact_weather_error

        out = redact_weather_error(requests.exceptions.ConnectionError(BARE_PATH_MSG))

        assert API_KEY not in out
        assert "appid=[REDACTED]" in out

    def test_strips_appid_from_full_url(self):
        from services.weather import redact_weather_error

        out = redact_weather_error(requests.exceptions.HTTPError(FULL_URL_MSG))

        assert API_KEY not in out

    def test_keeps_exception_type_and_harmless_text(self):
        from services.weather import redact_weather_error

        out = redact_weather_error(ValueError("no coord in response"))

        assert out.startswith("ValueError: ")
        assert "no coord in response" in out

    def test_accepts_a_plain_string(self):
        from services.weather import redact_weather_error

        out = redact_weather_error(RuntimeError(f"appid={API_KEY}"))

        assert API_KEY not in out


class TestWeatherServiceLogging:
    """The API key must not survive into a log line on any failure path."""

    def _service(self, **kwargs):
        from services.weather import WeatherService

        return WeatherService(API_KEY, "London", "metric", **kwargs)

    @staticmethod
    def _fail_requests(monkeypatch, message):
        from services import weather as weather_mod

        def boom(*args, **kwargs):
            raise requests.exceptions.ConnectionError(message)

        monkeypatch.setattr(weather_mod.requests, "get", boom)
        logged = []
        monkeypatch.setattr(weather_mod.app_logger, "error",
                            lambda msg: logged.append(str(msg)))
        return logged

    def test_fetch_failure_does_not_log_api_key(self, monkeypatch):
        logged = self._fail_requests(monkeypatch, BARE_PATH_MSG)

        service = self._service(latitude=51.5, longitude=-0.1)
        assert service.fetch_weather() is None

        assert logged
        assert all(API_KEY not in line for line in logged), logged

    def test_location_resolve_failure_does_not_log_api_key(self, monkeypatch):
        logged = self._fail_requests(monkeypatch, FULL_URL_MSG)

        service = self._service()
        assert service.resolve_location() is False

        assert logged
        assert all(API_KEY not in line for line in logged), logged
        assert any("London" in line for line in logged), logged
