"""
Test compass rose overlay
"""
import pytest
import os
import sys
import json
import numpy as np
from PIL import Image

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.compass_overlay import draw_compass


def _make_image(w=256, h=256):
    """Create a test RGBA image."""
    return Image.new('RGBA', (w, h), (0, 0, 0, 255))


def _label_widths(img, mirror, size=160, cx=128, cy=128):
    """Pixel width of the label on each side of `img`, as ``(west, east)``.

    'W' is wider than 'E' in every Latin face, so which side carries the wide
    glyph is a direct, font-independent reading of which label went where.

    Ink totals are not usable for this and neither is position. Cropping a
    fixed band clipped DejaVu's 'W' — 10px wider than Arial's — off Windows,
    and a band wide enough for any 'W' reaches the star arms, which rasterise
    a pixel further left than right; the labels' black shadow then darkens a
    different slice of arm on each side. Width survives all of it.

    The label-only ink comes from subtracting the same rose rendered with the
    labels suppressed, and columns below 5% of the peak are dropped so the
    shadow-over-arm residue does not extend the run.
    """
    from services import compass_overlay
    px = max(10, size // 6)
    sentinel = object()

    previous = compass_overlay._FONT_CACHE.get(px, sentinel)
    compass_overlay._FONT_CACHE[px] = None  # forces the "skip labels" path
    try:
        bare = draw_compass(_make_image(), size=size, cx=cx, cy=cy, mirror=mirror)
    finally:
        if previous is sentinel:
            compass_overlay._FONT_CACHE.pop(px, None)
        else:
            compass_overlay._FONT_CACHE[px] = previous

    glyphs = (np.array(img)[:, :, :3].astype(int)
              - np.array(bare)[:, :, :3].astype(int))
    columns = glyphs[cy - 20:cy + 20].sum(axis=(0, 2)).clip(min=0)
    floor = 0.05 * columns.max()
    assert floor > 0, "no label ink anywhere in the rendered compass"

    def _width(lo, hi):
        return int((columns[lo:hi] > floor).sum())

    return _width(cx - 100, cx - 50), _width(cx + 50, cx + 100)


class TestCompassRendering:
    """Test compass overlay rendering"""

    def test_compass_renders_without_error(self):
        """Test compass renders on image without error at default rotation"""
        img = _make_image()
        result = draw_compass(img, rotation=0)
        assert result is not None
        assert result.size == (256, 256)

    def test_compass_modifies_image(self):
        """Test compass actually draws on the image (pixels change)"""
        img = _make_image()
        original = np.array(img).copy()
        result = draw_compass(img, rotation=0)
        assert not np.array_equal(original, np.array(result))

    def test_rotations_produce_distinct_output(self):
        """Test compass at 0, 90, 180, 270 rotations produces distinct outputs"""
        images = []
        for angle in [0, 90, 180, 270]:
            img = _make_image()
            result = draw_compass(img, rotation=angle)
            images.append(np.array(result))

        # Each rotation should differ from at least one other
        all_same = True
        for i in range(len(images)):
            for j in range(i + 1, len(images)):
                if not np.array_equal(images[i], images[j]):
                    all_same = False
                    break
        assert not all_same, "Different rotations should produce distinct outputs"


class TestCompassMirror:
    """Test the E/W mirror option"""

    def test_mirror_changes_output(self):
        """Test mirroring produces a different image from the default"""
        normal = np.array(draw_compass(_make_image(), size=160, cx=128, cy=128))
        mirrored = np.array(draw_compass(_make_image(), size=160, cx=128, cy=128,
                                         mirror=True))
        assert not np.array_equal(normal, mirrored)

    def test_mirror_swaps_east_and_west_labels(self):
        """Test the E label moves to the W side (and vice versa) when mirrored"""
        size, cx, cy = 160, 128, 128
        normal = draw_compass(_make_image(), size=size, cx=cx, cy=cy)
        mirrored = draw_compass(_make_image(), size=size, cx=cx, cy=cy, mirror=True)

        normal_w, normal_e = _label_widths(normal, mirror=False)
        mirrored_w, mirrored_e = _label_widths(mirrored, mirror=True)

        # The label that was on the east is now on the west, and vice versa.
        assert normal_e == mirrored_w
        assert normal_w == mirrored_e
        # Guard the assertions above against E and W rendering identically
        assert normal_w > normal_e

    def test_mirror_keeps_north_up(self):
        """Test mirroring is left-right only — N stays where rotation puts it"""
        size, cx, cy = 160, 128, 128
        label_r = (size // 2) * 0.88
        box = 18

        def _north(img):
            return np.array(img.crop((cx - box, int(cy - label_r - box),
                                      cx + box, int(cy - label_r + box))))

        normal = draw_compass(_make_image(), size=size, cx=cx, cy=cy)
        mirrored = draw_compass(_make_image(), size=size, cx=cx, cy=cy, mirror=True)
        assert np.array_equal(_north(normal), _north(mirrored))


class TestCompassPosition:
    """Test compass position options"""

    def test_all_positions_render(self):
        """Test compass position is configurable (center, corners)"""
        positions = ['center', 'top-left', 'top-right', 'bottom-left', 'bottom-right']
        for pos in positions:
            img = _make_image()
            result = draw_compass(img, position=pos)
            assert result is not None, f"Failed to render at position {pos}"

    def test_positions_differ(self):
        """Test different positions produce different images"""
        img1 = draw_compass(_make_image(), position='top-left')
        img2 = draw_compass(_make_image(), position='bottom-right')
        assert not np.array_equal(np.array(img1), np.array(img2))


class TestCompassEdgeCases:
    """Test edge cases"""

    def test_small_image_no_crash(self):
        """Test compass on small image doesn't crash or overflow bounds"""
        img = _make_image(32, 32)
        # Small image should skip drawing (too small for compass)
        result = draw_compass(img, size=80)
        assert result is not None

    def test_rgb_input_converted(self):
        """Test RGB input is handled (converted to RGBA)"""
        img = Image.new('RGB', (256, 256), (0, 0, 0))
        result = draw_compass(img)
        assert result.mode == 'RGBA'

    def test_custom_size(self):
        """Test custom compass size"""
        img = _make_image(512, 512)
        result = draw_compass(img, size=120)
        assert result is not None


class TestCompassExplicitCoords:
    """Test compass with explicit cx/cy coordinates"""

    def test_explicit_coords(self):
        """Test compass renders at explicit cx/cy coordinates"""
        img = _make_image(256, 256)
        result = draw_compass(img, rotation=0, size=60, cx=128, cy=128)
        assert result is not None
        assert result.size == (256, 256)
        # Should have drawn something
        assert not np.array_equal(np.array(_make_image(256, 256)), np.array(result))

    def test_explicit_coords_override_position(self):
        """Test cx/cy take precedence over position string"""
        img1 = draw_compass(_make_image(), cx=50, cy=50, size=40)
        img2 = draw_compass(_make_image(), position='bottom-right', cx=50, cy=50, size=40)
        assert np.array_equal(np.array(img1), np.array(img2))


class TestCompassConfig:
    """Test compass overlay config round-trip via overlays list"""

    def test_config_round_trip(self, temp_config):
        """Test compass overlay config round-trips through save/load"""
        from services.config import Config
        config = Config(temp_config)

        overlays = config.get('overlays', [])
        overlays.append({
            'name': 'Compass Rose',
            'type': 'compass',
            'rotation': 45,
            'size': 100,
            'anchor': 'Top-Right',
            'offset_x': 20,
            'offset_y': 20,
        })
        config.set('overlays', overlays)
        config.save()

        config2 = Config(temp_config)
        loaded_overlays = config2.get('overlays', [])
        compass = [o for o in loaded_overlays if o.get('type') == 'compass']
        assert len(compass) == 1
        assert compass[0]['rotation'] == 45
        assert compass[0]['size'] == 100
        assert compass[0]['anchor'] == 'Top-Right'


class TestCompassFontCaching:
    """The label font is resolved once per size, not per draw_compass call.

    Re-resolving per call meant a transient ImageFont.truetype failure could
    render one frame in arial and the next in the default bitmap font — labels
    visibly jumping size between consecutive frames of a capture loop, and a
    flaky E/W mirror comparison.
    """

    def _clear_cache(self):
        from services import compass_overlay
        compass_overlay._FONT_CACHE.clear()

    def test_font_resolved_once_across_many_draws(self):
        from PIL import ImageFont
        from services import compass_overlay
        self._clear_cache()

        calls = []
        real = ImageFont.truetype

        def counting(font=None, *args, **kwargs):
            if isinstance(font, str):
                calls.append(font)
            return real(font, *args, **kwargs)

        ImageFont.truetype = counting
        try:
            draw_compass(_make_image(), size=160, cx=128, cy=128)
            after_first_draw = list(calls)
            for _ in range(4):
                draw_compass(_make_image(), size=160, cx=128, cy=128)
        finally:
            ImageFont.truetype = real
            self._clear_cache()

        # Count resolutions, not truetype calls: _label_font walks a candidate
        # list, and how far it gets is platform-dependent — Windows hits
        # arial.ttf first, Linux falls through to DejaVuSans.ttf. What must
        # hold everywhere is that the walk happens once, not once per frame.
        assert after_first_draw, "font was never resolved"
        assert calls == after_first_draw, (
            f"font re-resolved after the first draw: {calls[len(after_first_draw):]}")

    def test_consecutive_draws_use_the_same_font_despite_a_transient_failure(self):
        """The exact flake: font available for one call, unavailable for the next."""
        from PIL import ImageFont
        self._clear_cache()

        real = ImageFont.truetype
        state = {"n": 0}

        def flaky(font=None, *args, **kwargs):
            if isinstance(font, str) and font.lower().endswith('arial.ttf'):
                state["n"] += 1
                if state["n"] > 1:
                    raise OSError("cannot open resource")
            return real(font, *args, **kwargs)

        ImageFont.truetype = flaky
        try:
            normal = draw_compass(_make_image(), size=160, cx=128, cy=128)
            mirrored = draw_compass(_make_image(), size=160, cx=128, cy=128,
                                    mirror=True)
        finally:
            ImageFont.truetype = real
            self._clear_cache()

        normal_w, normal_e = _label_widths(normal, mirror=False)
        mirrored_w, mirrored_e = _label_widths(mirrored, mirror=True)

        assert normal_e == mirrored_w
        assert normal_w == mirrored_e

    def test_missing_font_skips_labels_instead_of_failing_the_frame(self):
        """An unattended capture must not stop over a font that won't load."""
        from PIL import ImageFont
        from services import compass_overlay
        self._clear_cache()

        real = ImageFont.truetype
        real_default = ImageFont.load_default

        def boom(*args, **kwargs):
            raise OSError("no fonts on this box")

        ImageFont.truetype = boom
        ImageFont.load_default = boom
        try:
            img = draw_compass(_make_image(), size=160, cx=128, cy=128)
        finally:
            ImageFont.truetype = real
            ImageFont.load_default = real_default
            self._clear_cache()

        assert img is not None
        assert img.size == (256, 256)
