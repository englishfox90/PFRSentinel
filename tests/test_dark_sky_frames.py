"""
dark_sky_frames — only frames taken with the sun at or below -15 deg judge the
model on disk.

The times and site are the reporter's 2026-09-30 dusk (discussion #105,
36.58 N 116.60 W, the site as the buffer dump rounds it): the observing gate
opened at 02:06:55 UTC with the sun at -8 deg, and two incumbent scores over
those twilight frames (1.10x and 1.13x chance) discredited a model the same
night's dark frames could not fault.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky import dark_sky_frames as dsf

LAT, LON = 36.58, -116.6
GATE_OPENED = datetime(2026, 10, 1, 2, 6, 55, tzinfo=timezone.utc)


def _frames(start, n, step_s=45):
    return [{'dt': start + timedelta(seconds=i * step_s), 'detected': []}
            for i in range(n)]


class TestSunAltitude:

    def test_the_reporters_gate_opened_in_nautical_twilight(self):
        alt = dsf.sun_altitude_deg(GATE_OPENED, LAT, LON)
        assert -9.0 < alt < -7.0

    def test_unreadable_time_is_none(self):
        assert dsf.sun_altitude_deg(None, LAT, LON) is None


class TestDarkSkyFrames:

    def test_the_reporters_dusk_buffer_is_not_scored(self):
        """48 frames from the gate opening (sun -8 deg) to the 19:43 local
        escape (sun -15.2 deg): at most one dark frame, so nothing to score."""
        dusk = _frames(GATE_OPENED, 48)
        assert dsf.sun_altitude_deg(dusk[-1]['dt'], LAT, LON) > -15.5
        assert dsf.dark_sky_frames(dusk, LAT, LON) == []

    def test_the_first_two_runs_that_discredited_the_model_are_not_scored(self):
        """19:12 local (10 frames) and 19:16 local (17 frames)."""
        assert dsf.dark_sky_frames(_frames(GATE_OPENED, 10, 30), LAT, LON) == []
        assert dsf.dark_sky_frames(_frames(GATE_OPENED, 17, 30), LAT, LON) == []

    def test_a_dark_buffer_is_scored_whole_and_unchanged(self):
        dark = _frames(datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc), 60, 60)
        assert dsf.dark_sky_frames(dark, LAT, LON) is dark

    def test_a_buffer_straddling_the_end_of_twilight_keeps_its_dark_part(self):
        frames = _frames(datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc), 30, 60)
        kept = dsf.dark_sky_frames(frames, LAT, LON)
        assert 0 < len(kept) < len(frames)
        assert all(dsf.sun_altitude_deg(f['dt'], LAT, LON)
                   <= dsf.SCORE_MAX_SUN_ALT_DEG for f in kept)
        assert kept == frames[-len(kept):]

    def test_too_few_dark_frames_score_nothing(self):
        frames = _frames(datetime(2026, 10, 1, 2, 36, tzinfo=timezone.utc), 20, 20)
        n_dark = sum(dsf.sun_altitude_deg(f['dt'], LAT, LON)
                     <= dsf.SCORE_MAX_SUN_ALT_DEG for f in frames)
        assert 0 < n_dark < dsf.DARK_MIN_FRAMES
        assert dsf.dark_sky_frames(frames, LAT, LON) == []

    @pytest.mark.parametrize('lat, lon', [(None, LON), (LAT, None), (None, None)])
    def test_unknown_site_fails_open(self, lat, lon):
        dusk = _frames(GATE_OPENED, 10)
        assert dsf.dark_sky_frames(dusk, lat, lon) is dusk

    def test_no_sun_altitude_fails_open(self, monkeypatch):
        monkeypatch.setattr(dsf, 'sun_altitude_deg', lambda *a: None)
        dusk = _frames(GATE_OPENED, 10)
        assert dsf.dark_sky_frames(dusk, LAT, LON) is dusk

    def test_empty(self):
        assert dsf.dark_sky_frames([], LAT, LON) == []
        assert dsf.dark_sky_frames(None, LAT, LON) == []
