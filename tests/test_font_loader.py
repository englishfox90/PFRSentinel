"""
Test the shared font loader — resolution order, caching, and the wiring
into the overlay renderer.
"""
import os
import sys

import pytest
from PIL import Image, ImageFont

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services import font_loader
from services.font_loader import bundled_font_path, load_font
from services.overlay_renderer import add_text_overlay


@pytest.fixture(autouse=True)
def clear_font_cache():
    """Font resolutions are cached process-wide — start each test clean."""
    font_loader.clear_cache()
    yield
    font_loader.clear_cache()


def test_bundled_font_path_exists():
    path = bundled_font_path()
    assert path is not None
    assert os.path.isfile(path)


def test_display_family_resolves_to_space_grotesk(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    font = load_font(24, 'display')
    assert isinstance(font, ImageFont.FreeTypeFont)
    assert font.getname()[0] == 'Space Grotesk'


def test_text_family_off_windows_skips_arial(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    attempted = []
    real_truetype = ImageFont.truetype

    def recording_truetype(path, size):
        attempted.append(path)
        return real_truetype(path, size)

    monkeypatch.setattr(ImageFont, 'truetype', recording_truetype)

    load_font(20, 'text')

    assert attempted, "expected at least one truetype attempt"
    assert attempted[0] == bundled_font_path()
    assert not any('arial' in p.lower() for p in attempted)


def test_load_font_falls_back_to_default_and_caches(monkeypatch):
    real_truetype = ImageFont.truetype

    def raise_for_paths(font, *args, **kwargs):
        # Only fail actual filesystem/name lookups (a str path) — Pillow's own
        # load_default() fallback calls truetype() on an in-memory resource,
        # which must keep working so the last-resort font stays reachable.
        if isinstance(font, str):
            raise OSError("no fonts here")
        return real_truetype(font, *args, **kwargs)

    monkeypatch.setattr(ImageFont, 'truetype', raise_for_paths)

    font_a = load_font(16, 'text')
    font_b = load_font(16, 'text')

    assert font_a is not None
    assert font_a is font_b


def test_add_text_overlay_wiring():
    """Exercise the real overlay renderer end-to-end (no mocking the font)."""
    img = Image.new('RGB', (200, 100), color=(0, 0, 0))
    overlay = {
        'type': 'text',
        'text': 'Hello',
        'anchor': 'Top-Left',
        'offset_x': 5,
        'offset_y': 5,
        'font_size': 18,
        'color': 'white',
    }

    result = add_text_overlay(img, overlay, {})

    assert result is not None
    assert result.size == img.size
