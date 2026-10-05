"""Title and description templates for YouTube uploads (issue #145)."""
from datetime import datetime

from services.timelapse_publishers import make_timelapse_metadata
from services.youtube_config import (
    DEFAULT_YOUTUBE_CONFIG,
    NIGHT_PLACEHOLDERS,
    render_template,
    squeeze_blank_lines,
    unknown_template_fields,
)


def test_unknown_template_fields_accepts_night_placeholders():
    template = "{date} {night} {min_temp} {night_summary} {weather_summary} {bogus}"

    assert unknown_template_fields(template) == {"bogus"}


def test_night_placeholders_render_empty_without_stats(tmp_path):
    video = tmp_path / "timelapse_20260619.mp4"
    video.write_bytes(b"fake")
    metadata = make_timelapse_metadata(str(video), frame_count=42, elapsed_seconds=65)

    rendered = render_template("[{night}|{roof}|{night_summary}] {missing}", metadata)

    assert rendered == "[||] {missing}"


def test_night_stats_reach_the_rendered_text(tmp_path):
    video = tmp_path / "timelapse_20260619.mp4"
    video.write_bytes(b"fake")
    metadata = make_timelapse_metadata(str(video), frame_count=42, elapsed_seconds=65)

    rendered = render_template("{night} {max_temp}", metadata, {"night": "2026-06-18", "max_temp": "51.8°F"})

    assert rendered == "2026-06-18 51.8°F"


def test_default_description_matches_config_defaults_and_drops_empty_night_line(tmp_path):
    from services.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["youtube"]["description_template"] == DEFAULT_YOUTUBE_CONFIG["description_template"]
    assert "{night_summary}" in DEFAULT_YOUTUBE_CONFIG["description_template"]
    assert set(NIGHT_PLACEHOLDERS) >= {"night", "min_temp", "max_temp", "roof", "gaps"}
    video = tmp_path / "timelapse_20260619.mp4"
    video.write_bytes(b"fake")
    metadata = make_timelapse_metadata(str(video), frame_count=42, elapsed_seconds=65)

    rendered = render_template(DEFAULT_YOUTUBE_CONFIG["description_template"], metadata).strip()

    assert rendered.endswith("\n42 frames, 00:01:05 of recording")


def test_recording_placeholder_for_automatic_and_manual_uploads(tmp_path):
    video = tmp_path / "timelapse_20260619.mp4"
    video.write_bytes(b"fake")
    automatic = make_timelapse_metadata(str(video), frame_count=42, elapsed_seconds=3725)
    manual = make_timelapse_metadata(str(video), frame_count=0, elapsed_seconds=0)

    assert render_template("[{recording}]", automatic) == "[42 frames, 01:02:05 of recording]"
    assert render_template("[{recording}]", manual) == "[]"
    # The raw values stay available on their own.
    assert render_template("{frame_count} {duration}", manual) == "0 00:00:00"
    assert unknown_template_fields("{recording} {frame_count} {duration}") == set()


def test_default_description_for_manual_upload_with_empty_night_is_one_line(tmp_path):
    video = tmp_path / "timelapse_20260619.mp4"
    video.write_bytes(b"fake")
    metadata = make_timelapse_metadata(str(video), frame_count=0, elapsed_seconds=0)
    metadata = metadata.__class__(
        path=metadata.path, frame_count=0, elapsed_seconds=0,
        file_size_bytes=metadata.file_size_bytes, queued_at=datetime(2026, 6, 19, 9, 0, 0),
    )

    rendered = render_template(DEFAULT_YOUTUBE_CONFIG["description_template"], metadata).strip()

    assert rendered == "All-sky timelapse recorded by PFR Sentinel on 2026-06-19."


def test_default_description_for_manual_upload_with_night_has_one_blank_line(tmp_path):
    video = tmp_path / "timelapse_20260619.mp4"
    video.write_bytes(b"fake")
    metadata = make_timelapse_metadata(str(video), frame_count=0, elapsed_seconds=0)
    metadata = metadata.__class__(
        path=metadata.path, frame_count=0, elapsed_seconds=0,
        file_size_bytes=metadata.file_size_bytes, queued_at=datetime(2026, 6, 19, 9, 0, 0),
    )
    night = {"night_summary": "Night of 2026-06-18 · 100% clear"}

    rendered = squeeze_blank_lines(
        render_template(DEFAULT_YOUTUBE_CONFIG["description_template"], metadata, night)
    ).strip()

    assert rendered == (
        "All-sky timelapse recorded by PFR Sentinel on 2026-06-19.\n"
        "\n"
        "Night of 2026-06-18 · 100% clear"
    )
    # Single blank lines and other whitespace are left as written.
    assert squeeze_blank_lines("a\n\nb  \n\tc\n\n\n\nd") == "a\n\nb  \n\tc\n\nd"
