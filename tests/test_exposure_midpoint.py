"""Tests for services.exposure_midpoint — the instant a frame's stars belong to."""
from datetime import datetime, timedelta, timezone

import pytest

from services.exposure_midpoint import exposure_midpoint, exposure_seconds

NOW = datetime(2026, 9, 18, 5, 0, 30, tzinfo=timezone.utc)


class TestExposureSeconds:
    @pytest.mark.parametrize('raw, expected', [
        ('20.66s', 20.66), ('13.0s', 13.0), ('500ms', 0.5), ('0.5', 0.5),
        (13, 13.0), (2.5, 2.5), ('  30 s ', 30.0),
    ])
    def test_parses_the_capture_worker_and_sidecar_forms(self, raw, expected):
        assert exposure_seconds({'EXPOSURE': raw}) == pytest.approx(expected)

    def test_fits_exptime_is_a_fallback(self):
        assert exposure_seconds({'EXPTIME': 12.0}) == 12.0

    def test_missing_or_garbage_is_none(self):
        assert exposure_seconds(None) is None
        assert exposure_seconds({}) is None
        assert exposure_seconds({'EXPOSURE': 'N/A'}) is None
        assert exposure_seconds({'EXPOSURE': '-3s'}) is None


class TestMidpoint:
    def test_camera_path_uses_the_sdk_start_plus_half(self):
        start = NOW - timedelta(seconds=40)
        md = {'EXPOSURE': '20s', 'EXPOSURE_START_UTC': start.isoformat()}
        assert exposure_midpoint(md, NOW) == start + timedelta(seconds=10)

    def test_fits_date_obs_is_the_exposure_start(self):
        md = {'EXPTIME': 30.0, 'DATE-OBS': '2026-09-18T04:59:00'}   # naive = UTC
        assert exposure_midpoint(md, NOW) == datetime(
            2026, 9, 18, 4, 59, 15, tzinfo=timezone.utc)

    def test_without_a_start_the_receipt_time_is_moved_back_half_an_exposure(self):
        assert exposure_midpoint({'EXPOSURE': '20.0s'}, NOW) == NOW - timedelta(seconds=10)

    def test_no_exposure_at_all_is_the_receipt_time(self):
        assert exposure_midpoint({}, NOW) == NOW
        assert exposure_midpoint(None, NOW) == NOW

    def test_unparsable_start_falls_back_rather_than_raising(self):
        md = {'EXPOSURE': '10s', 'EXPOSURE_START_UTC': 'yesterday'}
        assert exposure_midpoint(md, NOW) == NOW - timedelta(seconds=5)

    def test_result_is_always_aware_utc(self):
        naive_now = NOW.replace(tzinfo=None)
        out = exposure_midpoint({'EXPOSURE': '4s'}, naive_now)
        assert out.tzinfo is timezone.utc
        assert out == NOW - timedelta(seconds=2)
