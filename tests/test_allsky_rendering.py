"""
Tests for the all-sky overlay rendering pipeline.

Verifies that layers render without crashing, that individual toggles work,
and that the output is a valid PIL Image of the correct size.
No hardware, network, or calibrated data required.
"""
import numpy as np
import pytest
from datetime import datetime, timezone
from PIL import Image

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid, default_gap, estimate_text_size
from services.allsky.label_stability import reset_label_stability


@pytest.fixture(autouse=True)
def _fresh_label_state():
    """The renderer keeps frame-to-frame label state; tests start clean."""
    reset_label_stability()
    yield
    reset_label_stability()


# ===================================================================
# Helpers
# ===================================================================

def _default_model() -> FisheyeModel:
    """Create a test fisheye model pointing at zenith (no rotation)."""
    return FisheyeModel(
        cx=960.0, cy=540.0, a1=600.0, a3=0.0, a5=0.0,
        roll=0.0, axis_alt=90.0, axis_az=0.0,
        rms_residual=1.0, n_matches=50,
        calibrated_at="2024-01-01T00:00:00+00:00",
    )


def _test_image(w=1920, h=1080) -> Image.Image:
    """Return a dark RGBA test image."""
    return Image.new('RGBA', (w, h), (10, 10, 30, 255))


DT = datetime(2024, 6, 21, 22, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1  # London


# ===================================================================
# LabelGrid
# ===================================================================

class TestLabelGrid:
    def test_empty_grid_is_free(self):
        grid = LabelGrid(1920, 1080)
        assert grid.is_free(100, 100, 80, 20)

    def test_occupied_cell_blocked(self):
        grid = LabelGrid(1920, 1080, cell_size=12)
        grid.occupy(100.0, 100.0, 80.0, 20.0)
        # Same region should now be blocked
        assert not grid.is_free(100.0, 100.0, 80.0, 20.0)

    def test_different_region_still_free(self):
        grid = LabelGrid(1920, 1080, cell_size=12)
        grid.occupy(100.0, 100.0, 80.0, 20.0)
        # Far-away region should still be free
        assert grid.is_free(800.0, 600.0, 80.0, 20.0)

    def test_try_place_returns_position(self):
        grid = LabelGrid(1920, 1080)
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert pos is not None
        assert 0 <= pos[0] < 1920
        assert 0 <= pos[1] < 1080

    def test_try_place_none_when_surrounded(self):
        """Every slot blocked -> None, never a label on top of something."""
        grid = LabelGrid(1920, 1080)
        grid.occupy(300.0, 300.0, 400.0, 400.0)
        assert grid.try_place(500.0, 500.0, 60.0, 14.0) is None

    def test_right_slot_clears_marker_by_gap(self):
        grid = LabelGrid(1920, 1080)
        gap = default_gap(14.0)
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert pos == (500.0 + gap, 500.0 - 7.0)
        assert pos[0] - 500.0 >= 8.0  # visibly separated at the 750 px base size

    def test_left_slot_ends_before_marker(self):
        """Text in the left slot must end short of the star, not run across it."""
        grid = LabelGrid(1920, 1080)
        gap = default_gap(14.0)
        grid.occupy(500.0 + gap, 480.0, 60.0, 40.0)  # block the right slot
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert pos is not None
        assert pos[0] + 60.0 == 500.0 - gap
        assert pos[1] == 493.0

    def test_below_slot_is_centred_and_clear_of_marker(self):
        grid = LabelGrid(1920, 1080)
        gap = default_gap(14.0)
        grid.occupy(530.0, 470.0, 30.0, 20.0)  # clips the right slot
        grid.occupy(420.0, 470.0, 40.0, 20.0)  # clips the left slot
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert pos is not None
        assert pos[0] == 500.0 - 30.0
        assert pos[1] == 500.0 + gap  # text top sits below the star

    def test_gap_scales_with_label_height(self):
        assert default_gap(28.0) > default_gap(14.0)
        assert default_gap(1.0) >= 4.0

    def test_reserved_marker_blocks_label(self):
        """A label may not be placed over another star's position."""
        grid = LabelGrid(1920, 1080)
        gap = default_gap(14.0)
        grid.reserve_marker(500.0 + gap + 20.0, 500.0, 5.0)  # star inside the right slot
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert pos is not None
        assert pos[0] + 60.0 <= 500.0 - gap  # fell through to the left slot

    def test_own_marker_does_not_block_label(self):
        grid = LabelGrid(1920, 1080)
        gap = default_gap(14.0)
        grid.reserve_marker(500.0, 500.0, gap * 0.75)
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert pos == (500.0 + gap, 493.0)

    def test_labels_keep_clearance(self):
        grid = LabelGrid(1920, 1080, cell_size=12)
        grid.occupy(100.0, 100.0, 80.0, 20.0)
        assert not grid.is_free(184.0, 100.0, 80.0, 20.0)  # 4 px apart: too close
        assert grid.is_free(200.0, 100.0, 80.0, 20.0)      # 20 px apart: fine

    def test_estimate_text_size(self):
        w, h = estimate_text_size("M42", 12)
        assert w > 0 and h > 0
        w2, _ = estimate_text_size("Andromeda", 12)
        assert w2 > w  # Longer text → wider


# ===================================================================
# Render Grid
# ===================================================================

class TestRenderGrid:
    def test_renders_without_error(self):
        from services.allsky.render_grid import render_grid
        img = _test_image()
        model = _default_model()
        result = render_grid(img, model, {'enabled': True})
        assert result.size == img.size
        assert result.mode == 'RGBA'

    def test_disabled_returns_unchanged(self):
        from services.allsky.render_grid import render_grid
        img = _test_image()
        original = img.copy()
        model = _default_model()
        result = render_grid(img, model, {'enabled': False})
        assert list(result.getdata()) == list(original.getdata())

    def test_grid_modifies_image(self):
        from services.allsky.render_grid import render_grid
        img = _test_image()
        model = _default_model()
        result = render_grid(img, model, {'enabled': True, 'horizon': True,
                                          'altitude_rings': True, 'opacity': 200})
        # The image should change (some pixels different from background)
        arr_before = np.array(img)
        arr_after  = np.array(result)
        diff = np.abs(arr_before.astype(int) - arr_after.astype(int))
        assert diff.sum() > 0, "Grid render should change at least some pixels"


# ===================================================================
# Render Constellations
# ===================================================================

class TestRenderConstellations:
    def test_renders_without_error(self):
        from services.allsky.render_constellations import render_constellations
        img = _test_image()
        model = _default_model()
        config = {'enabled': True, 'lines': True, 'labels': True,
                  'color': '#4488FF', 'opacity': 180, 'line_width': 1, 'label_size': 12}
        result = render_constellations(img, model, config, LAT, LON, DT)
        assert result.size == img.size

    def test_disabled_returns_unchanged(self):
        from services.allsky.render_constellations import render_constellations
        img = _test_image()
        model = _default_model()
        original = img.copy()
        result = render_constellations(img, model, {'enabled': False}, LAT, LON, DT)
        assert list(result.getdata()) == list(original.getdata())


# ===================================================================
# Render Objects (Messier / NGC / Planets)
# ===================================================================

class TestRenderObjects:
    def test_planets_renders_without_error(self):
        from services.allsky.render_objects import render_planets
        from services.allsky.label_collision import LabelGrid
        img = _test_image()
        model = _default_model()
        grid = LabelGrid(1920, 1080)
        config = {'enabled': True, 'label_size': 14, 'marker_size': 10, 'opacity': 255,
                  'colors': {'Mars': '#FF6644', 'Jupiter': '#FFCC88'}}
        result = render_planets(img, model, config, LAT, LON, DT, grid)
        assert result.size == img.size

    def test_messier_renders_without_error(self):
        from services.allsky.render_objects import render_messier
        from services.allsky.label_collision import LabelGrid
        img = _test_image()
        model = _default_model()
        grid = LabelGrid(1920, 1080)
        config = {'enabled': True, 'color': '#FF8844', 'marker_size': 8,
                  'label_size': 10, 'opacity': 200}
        result = render_messier(img, model, config, LAT, LON, DT, grid)
        assert result.size == img.size

    def test_ngc_disabled_returns_unchanged(self):
        from services.allsky.render_objects import render_ngc
        from services.allsky.label_collision import LabelGrid
        img = _test_image()
        model = _default_model()
        grid = LabelGrid(1920, 1080)
        original = img.copy()
        result = render_ngc(img, model, {'enabled': False}, LAT, LON, DT, grid)
        assert list(result.getdata()) == list(original.getdata())


# ===================================================================
# Full overlay_renderer pipeline
# ===================================================================

class TestOverlayRenderer:
    def _make_config(self, calibration_path: str) -> dict:
        return {
            'enabled': True,
            'calibration_file': calibration_path,
            '_lat': LAT, '_lon': LON,
            'grid': {'enabled': True},
            'constellations': {'enabled': True, 'lines': True, 'labels': False},
            'messier': {'enabled': True},
            'ngc': {'enabled': False},
            'planets': {'enabled': True, 'opacity': 255, 'marker_size': 10,
                        'label_size': 12, 'colors': {}},
        }

    def test_uncalibrated_returns_original(self):
        """With no calibration file, render_allsky_overlay should be a no-op."""
        from services.allsky.overlay_renderer import render_allsky_overlay
        img = _test_image()
        original = img.copy()
        config = {'enabled': True, 'calibration_file': '', '_lat': LAT, '_lon': LON}
        result = render_allsky_overlay(img, config, {})
        assert list(result.getdata()) == list(original.getdata())

    def test_disabled_returns_original(self):
        from services.allsky.overlay_renderer import render_allsky_overlay
        img = _test_image()
        original = img.copy()
        result = render_allsky_overlay(img, {'enabled': False}, {})
        assert list(result.getdata()) == list(original.getdata())

    def test_with_valid_calibration(self, tmp_path):
        """With a valid calibration file, render should modify the image."""
        from services.allsky.overlay_renderer import render_allsky_overlay
        model = _default_model()
        cal_path = str(tmp_path / "cal.json")
        model.save(cal_path)

        img = _test_image()
        config = self._make_config(cal_path)
        result = render_allsky_overlay(img, config, {'DATETIME': '2024-06-21 22:00:00'})

        assert result.size == img.size
        # Image should have changed
        arr_before = np.array(img)
        arr_after  = np.array(result.convert('RGBA'))
        diff = np.abs(arr_before.astype(int) - arr_after.astype(int))
        assert diff.sum() > 0, "Overlay should modify the image"

    def test_preserves_rgb_mode(self, tmp_path):
        """Input RGB images should be returned as RGB (not RGBA)."""
        from services.allsky.overlay_renderer import render_allsky_overlay
        model = _default_model()
        cal_path = str(tmp_path / "cal.json")
        model.save(cal_path)

        img_rgb = Image.new('RGB', (1920, 1080), (10, 10, 30))
        config = self._make_config(cal_path)
        result = render_allsky_overlay(img_rgb, config, {})
        assert result.mode == 'RGB', f"Expected RGB, got {result.mode}"


# ===================================================================
# Output crop (issue #12) — the model must be TRANSLATED into output
# pixels, never scaled. A square→square crop has the same aspect ratio
# as the full frame, so the resize path cannot tell them apart alone.
# ===================================================================

class TestOutputCropTranslation:
    def _sized_model(self, w=1000, h=1000) -> FisheyeModel:
        return FisheyeModel(
            cx=500.0, cy=500.0, a1=400.0, a3=0.0, a5=0.0,
            roll=0.0, axis_alt=90.0, axis_az=0.0,
            rms_residual=1.0, n_matches=50,
            calibrated_at="2024-01-01T00:00:00+00:00",
            image_width=w, image_height=h,
        )

    def _spy_model(self, monkeypatch):
        """Capture the model handed to the first render layer."""
        seen = []

        def _spy(img, model, config):
            seen.append(model)
            return img

        monkeypatch.setattr('services.allsky.overlay_renderer.render_grid', _spy)
        return seen

    def _render(self, tmp_path, monkeypatch, model, img, metadata):
        from services.allsky.overlay_renderer import render_allsky_overlay
        cal_path = str(tmp_path / "cal.json")
        model.save(cal_path)
        seen = self._spy_model(monkeypatch)
        config = {'enabled': True, 'calibration_file': cal_path,
                  '_lat': LAT, '_lon': LON, 'grid': {'enabled': True},
                  'constellations': {'enabled': False}, 'messier': {'enabled': False},
                  'ngc': {'enabled': False}, 'planets': {'enabled': False}}
        render_allsky_overlay(img, config, metadata)
        assert seen, "grid layer never ran"
        return seen[0]

    def test_crop_translates_the_optical_centre(self, tmp_path, monkeypatch):
        crop = {'x': 200, 'y': 100, 'width': 600, 'height': 600,
                'frame_width': 1000, 'frame_height': 1000}
        used = self._render(tmp_path, monkeypatch, self._sized_model(),
                            Image.new('RGBA', (600, 600), (10, 10, 30, 255)),
                            {'OUTPUT_CROP': crop})

        assert (used.cx, used.cy) == (300.0, 400.0)
        assert used.a1 == 400.0, "plate scale must NOT change on a crop"
        assert (used.image_width, used.image_height) == (600, 600)

    def test_resize_then_crop_scales_before_translating(self, tmp_path, monkeypatch):
        # resize_percent 50 first (1000 -> 500), then a 300x300 cut at (100, 50).
        crop = {'x': 100, 'y': 50, 'width': 300, 'height': 300,
                'frame_width': 500, 'frame_height': 500}
        used = self._render(tmp_path, monkeypatch, self._sized_model(),
                            Image.new('RGBA', (300, 300), (10, 10, 30, 255)),
                            {'OUTPUT_CROP': crop})

        assert used.a1 == 200.0, "resize must still scale the plate scale"
        assert (used.cx, used.cy) == (150.0, 200.0)
        assert (used.image_width, used.image_height) == (300, 300)

    def test_legacy_model_without_a_size_is_only_translated(self, tmp_path, monkeypatch):
        crop = {'x': 200, 'y': 100, 'width': 600, 'height': 600,
                'frame_width': 1000, 'frame_height': 1000}
        used = self._render(tmp_path, monkeypatch, self._sized_model(w=0, h=0),
                            Image.new('RGBA', (600, 600), (10, 10, 30, 255)),
                            {'OUTPUT_CROP': crop})

        assert (used.cx, used.cy) == (300.0, 400.0)
        assert used.a1 == 400.0

    def test_without_crop_metadata_a_smaller_frame_still_scales(self, tmp_path, monkeypatch):
        used = self._render(tmp_path, monkeypatch, self._sized_model(),
                            Image.new('RGBA', (500, 500), (10, 10, 30, 255)), {})

        assert (used.cx, used.cy) == (250.0, 250.0)
        assert used.a1 == 200.0

    def test_crop_size_mismatch_skips_the_render(self, tmp_path, monkeypatch):
        # OUTPUT_CROP claims a 600x600 box but the frame we were actually
        # handed is 500x500 (stale/mismatched metadata) — rather than guess,
        # the renderer must log a warning and hand the image back untouched.
        from services.allsky.overlay_renderer import render_allsky_overlay

        cal_path = str(tmp_path / "cal.json")
        self._sized_model().save(cal_path)
        seen = self._spy_model(monkeypatch)
        warnings = []
        monkeypatch.setattr('services.allsky.overlay_renderer.log.warning',
                            lambda msg: warnings.append(msg))

        crop = {'x': 200, 'y': 100, 'width': 600, 'height': 600,
                'frame_width': 1000, 'frame_height': 1000}
        img = Image.new('RGBA', (500, 500), (10, 10, 30, 255))
        config = {'enabled': True, 'calibration_file': cal_path,
                  '_lat': LAT, '_lon': LON, 'grid': {'enabled': True},
                  'constellations': {'enabled': False}, 'messier': {'enabled': False},
                  'ngc': {'enabled': False}, 'planets': {'enabled': False}}

        result = render_allsky_overlay(img, config, {'OUTPUT_CROP': crop})

        assert result is img, "must return the image unchanged, not attempt a render"
        assert seen == [], "no render layer should have run"
        assert warnings and 'OUTPUT_CROP' in warnings[0]
