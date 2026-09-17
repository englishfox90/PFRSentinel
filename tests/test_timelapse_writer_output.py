"""
Tests for the output target services.timelapse_writer.TimelapseWriter computes:
which file the video is written to, and at what pixel dimensions.

All of this is decided before any ffmpeg process exists, so these tests need no
subprocess fakes, no clock and no threading — they are pure resolution of config
into a destination path and a -vf filter chain. The ffmpeg process lifecycle
(crash-loop guard, backoff, orphan cleanup, stop races, status snapshots) is a
separate concern and lives in test_timelapse_writer.py.

Covers:
- _build_ffmpeg_cmd even-dimension handling (libx264 + yuv420p require even
  width AND height — odd dims used to crash ffmpeg on every frame).
- The shipped output_max_dim default, which feeds that filter chain.
- _build_output_path's fallback directory, which must resolve to the shared
  app-data root rather than a relative path under the working directory.
"""
import os
from datetime import datetime
from pathlib import Path

from services.timelapse_writer import TimelapseWriter


# --------------------------------------------------------------------------- #
#  _build_ffmpeg_cmd — even-dimension handling                                 #
# --------------------------------------------------------------------------- #

def _vf(writer, frame_size, max_dim):
    writer._config = {'output_max_dim': max_dim}
    cmd = writer._build_ffmpeg_cmd(frame_size, 'out.mp4')
    return cmd[cmd.index('-vf') + 1] if '-vf' in cmd else None


def test_even_native_no_filter():
    """Already-even frames need no -vf — avoid pointless scaling overhead."""
    assert _vf(TimelapseWriter(), (1920, 1080), 0) is None


def test_odd_native_is_cropped_even():
    """Odd source dims must be forced even or x264/yuv420p aborts."""
    vf = _vf(TimelapseWriter(), (1937, 1097), 0)
    assert vf == 'crop=trunc(iw/2)*2:trunc(ih/2)*2'


def test_downscale_also_forces_even():
    """The aspect-preserving downscale can land on odd dims, so crop follows it."""
    vf = _vf(TimelapseWriter(), (4144, 2822), 1920)
    assert vf == (
        'scale=1920:1920:force_original_aspect_ratio=decrease,'
        'crop=trunc(iw/2)*2:trunc(ih/2)*2'
    )


def test_downscale_skipped_when_smaller_than_max():
    """No downscale and even source → no filter chain at all."""
    assert _vf(TimelapseWriter(), (1280, 720), 1920) is None


# --------------------------------------------------------------------------- #
#  Shipped defaults and the fallback output directory                          #
# --------------------------------------------------------------------------- #

def test_default_output_max_dim_downscales_to_1920():
    """Without a default, a 2628x2628 sensor encoded at native resolution —
    ~20MB per piped frame. The shipped default must cap the longest side, and
    must be one of the panel dropdown's values."""
    from services.config_defaults import DEFAULT_CONFIG

    max_dim = DEFAULT_CONFIG['timelapse']['output_max_dim']
    assert max_dim == 1920
    assert max_dim in {0, 1920, 1440, 1280, 720}   # ui/panels/timelapse_panel _res_map

    vf = _vf(TimelapseWriter(), (2628, 2628), max_dim)
    assert vf == (
        'scale=1920:1920:force_original_aspect_ratio=decrease,'
        'crop=trunc(iw/2)*2:trunc(ih/2)*2'
    )


def test_default_output_dir_is_absolute_without_localappdata(monkeypatch, tmp_path):
    """With no output_dir configured, the fallback timelapse directory used to
    be built from os.getenv('LOCALAPPDATA', '') — off Windows that is a
    relative 'PFRSentinel/timelapse' written under the process working
    directory. It must resolve to the shared app-data root instead."""
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.chdir(tmp_path)

    writer = TimelapseWriter()
    writer._config = {}
    path = writer._build_output_path(datetime(2026, 9, 17, 22, 0))

    assert os.path.isabs(path)
    assert Path(path).parent.name == 'timelapse'
    assert tmp_path not in Path(path).parents
    assert not list(tmp_path.iterdir()), "nothing may be written to the cwd"
