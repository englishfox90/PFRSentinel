"""Night statistics for the YouTube description (issue #145, Part 1)."""
import os
from datetime import datetime, timedelta

import pytest

from services.library.index import LibraryIndex
from services.youtube_config import NIGHT_PLACEHOLDERS, TimelapseUploadMetadata, render_template
from services.youtube_night_stats import (
    build_night_context,
    find_night_session,
    format_temp,
    video_time_span,
)

EVENING = datetime(2026, 6, 18, 20, 0, 0)
MORNING_END = datetime(2026, 6, 19, 4, 30, 0)


@pytest.fixture
def library_index(tmp_path):
    index = LibraryIndex(str(tmp_path / "library.db"))
    yield index
    index.close()


def add_frame(index, when, **fields):
    record = {
        "captured_at": int(when.timestamp()),
        "path": f"{when:%Y%m%d_%H%M%S}.jpg",
        "bytes": 1000,
        "created_at": int(when.timestamp()),
    }
    record.update(fields)
    index.insert(record)


def fill_night(index, start=EVENING, end=datetime(2026, 6, 19, 5, 0, 0), step_min=10, **fields):
    """One frame every ``step_min`` minutes; temperature falls 0.1 °C per frame."""
    when, n = start, 0
    while when <= end:
        frame = {"temp": f"{10.0 - 0.1 * n:.1f}°C", "roof": "Open", "condition": "Clear",
                 "clouds": 10, "star_count": 100 + n, "seeing": "Good", "fwhm": "2.5"}
        frame.update(fields)
        add_frame(index, when, **frame)
        when += timedelta(minutes=step_min)
        n += 1


def auto_metadata(tmp_path, end=MORNING_END, elapsed=7 * 3600):
    video = tmp_path / "timelapse_20260618.mp4"
    video.write_bytes(b"fake")
    return TimelapseUploadMetadata(str(video), 300, elapsed, 4, end)


def manual_metadata(tmp_path, mtime=MORNING_END):
    video = tmp_path / "timelapse_20260618.mp4"
    video.write_bytes(b"fake")
    os.utime(video, (mtime.timestamp(), mtime.timestamp()))
    return TimelapseUploadMetadata(str(video), 0, 0, 4, datetime(2026, 6, 25, 9, 0, 0))


def test_automatic_upload_uses_only_frames_in_the_recording_window(tmp_path, library_index):
    fill_night(library_index)
    ctx = build_night_context(auto_metadata(tmp_path), library_index)

    # Window 21:30 -> 04:30; the library runs 20:00 -> 05:00.
    assert ctx["night"] == "2026-06-18"
    assert (ctx["start_time"], ctx["end_time"]) == ("21:30", "04:30")
    # Frame n=9 (21:30) is 9.1 °C, frame n=51 (04:30) is 4.9 °C.
    assert (ctx["min_temp"], ctx["max_temp"]) == ("4.9°C", "9.1°C")
    assert ctx["clear_pct"] == "100"
    assert ctx["max_stars"] == "151"
    assert ctx["best_seeing"] == "Good (FWHM 2.5 px)"
    assert ctx["roof"] == "Open"
    assert ctx["gaps"] == "none"
    assert ctx["weather_summary"] == "Cloud cover 10%"


def test_manual_upload_takes_the_night_up_to_the_file_time(tmp_path, library_index):
    fill_night(library_index)
    metadata = manual_metadata(tmp_path)

    assert video_time_span(metadata) == (None, int(MORNING_END.timestamp()))
    ctx = build_night_context(metadata, library_index)

    assert ctx["night"] == "2026-06-18"
    assert (ctx["start_time"], ctx["end_time"]) == ("20:00", "04:30")


@pytest.mark.parametrize("units, low, high", [
    ("metric", "4.9°C", "9.1°C"),
    ("imperial", "40.8°F", "48.4°F"),
    ("standard", "278.0K", "282.2K"),
    ("bogus", "4.9°C", "9.1°C"),
])
def test_temperatures_follow_the_saved_weather_units(tmp_path, library_index, units, low, high):
    fill_night(library_index)
    ctx = build_night_context(auto_metadata(tmp_path), library_index, units=units)

    assert (ctx["min_temp"], ctx["max_temp"]) == (low, high)
    assert f"sensor {low} to {high}" in ctx["night_summary"]


def test_format_temp_handles_missing_reading():
    assert format_temp(None, "imperial") == ""
    assert format_temp(0.0, "imperial") == "32.0°F"


def test_mixed_roof_gaps_and_cloud_range(tmp_path, library_index):
    fill_night(library_index, start=EVENING, end=datetime(2026, 6, 18, 22, 0), clouds=0)
    fill_night(library_index, start=datetime(2026, 6, 19, 0, 0), end=datetime(2026, 6, 19, 0, 50),
               roof="Closed (97%)", condition=None, clouds=60)
    ctx = build_night_context(manual_metadata(tmp_path, mtime=datetime(2026, 6, 19, 1, 0)), library_index)

    # 13 open frames, 6 closed; a 2 h hole between 22:00 and 00:00.
    assert ctx["roof"] == "Open 68% of the night"
    assert ctx["gaps"] == "1 (longest 2h 00m)"
    assert ctx["clear_pct"] == "68"
    assert ctx["weather_summary"] == "Cloud cover 0 to 60% (average 19%)"
    assert "roof open 68% of the night" in ctx["night_summary"]
    assert "1 capture gap" in ctx["night_summary"]


def test_night_without_any_sky_signal_leaves_those_fields_empty(tmp_path, library_index):
    fill_night(library_index, roof=None, condition=None, clouds=None, star_count=None,
               seeing=None, fwhm=None)
    ctx = build_night_context(auto_metadata(tmp_path), library_index)

    assert ctx["night"] == "2026-06-18"
    for name in ("clear_pct", "roof", "max_stars", "best_seeing", "weather_summary"):
        assert ctx[name] == "", name
    assert ctx["night_summary"] == "Night of 2026-06-18, 21:30 to 04:30 · sensor 4.9°C to 9.1°C"


def test_window_across_noon_picks_the_night_with_most_frames(tmp_path, library_index):
    fill_night(library_index, start=datetime(2026, 6, 19, 11, 0), end=datetime(2026, 6, 19, 11, 50))
    fill_night(library_index, start=datetime(2026, 6, 19, 12, 0), end=datetime(2026, 6, 19, 15, 0))
    metadata = auto_metadata(tmp_path, end=datetime(2026, 6, 19, 15, 0), elapsed=4 * 3600)

    session, rows = find_night_session(metadata, library_index)

    assert session["key"] == "2026-06-19"
    assert len(rows) == session["frame_count"] == 19


def test_empty_library_renders_every_placeholder_empty(tmp_path, library_index):
    ctx = build_night_context(auto_metadata(tmp_path), library_index)

    assert ctx == {name: "" for name in NIGHT_PLACEHOLDERS}


def test_frames_from_another_night_are_not_borrowed(tmp_path, library_index):
    fill_night(library_index, start=EVENING - timedelta(days=2), end=MORNING_END - timedelta(days=2))
    ctx = build_night_context(auto_metadata(tmp_path), library_index)

    assert ctx["night"] == ""


def test_missing_library_renders_empty(tmp_path):
    assert build_night_context(auto_metadata(tmp_path), None) == {name: "" for name in NIGHT_PLACEHOLDERS}


def test_library_error_never_escapes(tmp_path):
    class BrokenIndex:
        def brief_rows(self, since=None, until=None):
            raise RuntimeError("database is locked")

    ctx = build_night_context(auto_metadata(tmp_path), BrokenIndex())

    assert ctx == {name: "" for name in NIGHT_PLACEHOLDERS}


def test_manual_upload_of_a_vanished_file_renders_empty(tmp_path, library_index):
    fill_night(library_index)
    metadata = TimelapseUploadMetadata(str(tmp_path / "gone.mp4"), 0, 0, 0, MORNING_END)

    assert video_time_span(metadata) == (None, None)
    assert build_night_context(metadata, library_index)["night"] == ""


def test_rendered_description_carries_the_night_line(tmp_path, library_index):
    fill_night(library_index)
    metadata = auto_metadata(tmp_path)
    ctx = build_night_context(metadata, library_index, units="imperial")

    text = render_template("{night_summary}", metadata, ctx)

    assert text == (
        "Night of 2026-06-18, 21:30 to 04:30 · sensor 40.8°F to 48.4°F · 100% clear"
        " · roof open · up to 151 stars · best seeing Good (FWHM 2.5 px) · cloud cover 10%"
    )


class _Config:
    def __init__(self, youtube, weather):
        self._values = {"youtube": youtube, "weather": weather, "discord": {}}

    def get(self, key, default=None):
        return self._values.get(key, default)


class _Auth:
    def has_token(self):
        return True


class _Uploader:
    def __init__(self):
        self.night_stats = []

    def upload_video(self, config, metadata, *, resumable_uri="", progress_callback=None, night_stats=None):
        from services.youtube_upload import YouTubeUploadResult
        self.night_stats.append(night_stats)
        return YouTubeUploadResult(True, "uploaded", "Uploaded", video_id="v1")


def _publish(tmp_path, provider, units="imperial"):
    from services.timelapse_publishers import TimelapsePublishers
    from services.youtube_upload_state import YouTubeUploadStateStore

    client_json = tmp_path / "client.json"
    client_json.write_text("{}", encoding="utf-8")
    uploader = _Uploader()
    publisher = TimelapsePublishers(
        _Config({"enabled": True, "client_secrets_path": str(client_json)}, {"units": units}),
        youtube_auth_manager=_Auth(),
        youtube_uploader=uploader,
        youtube_state_store=YouTubeUploadStateStore(storage_dir=str(tmp_path)),
        library_index_provider=provider,
    )
    result = publisher.enqueue_youtube_upload(auto_metadata(tmp_path), manual=False)
    publisher._queue.join()
    publisher.shutdown(timeout=1)
    assert result.status == "queued"
    return uploader.night_stats


def test_publisher_hands_the_uploader_the_night_in_saved_units(tmp_path, library_index):
    fill_night(library_index)

    stats = _publish(tmp_path, lambda: library_index)

    assert stats[0]["night"] == "2026-06-18"
    assert stats[0]["min_temp"] == "40.8°F"


def test_publisher_survives_a_failing_library_provider(tmp_path):
    def provider():
        raise RuntimeError("library stopped")

    stats = _publish(tmp_path, provider)

    assert stats == [{name: "" for name in NIGHT_PLACEHOLDERS}]


def test_timelapse_controller_reads_the_index_from_the_main_window():
    from ui.controllers.timelapse_controller import TimelapseController

    class Window:
        image_library = None

    controller = TimelapseController.__new__(TimelapseController)
    controller._main_window = Window()
    assert controller._library_index() is None

    Window.image_library = type("Library", (), {"index": "idx"})()
    assert controller._library_index() == "idx"
