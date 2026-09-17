"""
Test services/output_crop.py — pure geometry for the output-stage crop
(issue #12) plus its DEFAULT_CONFIG integration.
"""
import json

from PIL import Image

from services.output_crop import (
    MIN_SIZE,
    CropBox,
    apply_output_crop,
    centred_box,
    describe,
    is_full_frame,
    normalise_box,
    resolve_crop_box,
    square_around_circle,
)


class TestNormaliseBox:
    def test_clamps_negative_and_oversized_into_frame(self):
        assert normalise_box(-100, -100, 5000, 5000, 1000, 800) == (0, 0, 1000, 800)

    def test_oversized_box_alone_clamps_to_frame(self):
        assert normalise_box(0, 0, 5000, 5000, 1000, 800) == (0, 0, 1000, 800)

    def test_enforces_minimum_size_on_small_request(self):
        # Requested 10x10 is below MIN_SIZE; position is unaffected, size is bumped up.
        assert normalise_box(10, 10, 10, 10, 1000, 800) == (10, 10, MIN_SIZE, MIN_SIZE)

    def test_negative_width_and_height_floor_to_min_size(self):
        assert normalise_box(0, 0, -10, -10, 1000, 800) == (0, 0, MIN_SIZE, MIN_SIZE)

    def test_whole_frame_when_frame_smaller_than_min_size_and_even(self):
        # 40x30 is below MIN_SIZE (64) on both axes and already even, so the
        # whole-frame guarantee lands exactly on the frame size.
        assert normalise_box(0, 0, 1000, 1000, 40, 30) == (0, 0, 40, 30)

    def test_odd_frame_smaller_than_min_size_loses_one_pixel_to_evenness(self):
        # The even-dimension guarantee takes priority over exactly matching an
        # odd frame size, so a 41x31 frame yields 40x30, not 41x31.
        assert normalise_box(0, 0, 1000, 1000, 41, 31) == (0, 0, 40, 30)

    def test_enforces_even_width_and_height(self):
        # 101x201 requested on a frame with no size constraint bites.
        assert normalise_box(0, 0, 101, 201, 1000, 800) == (0, 0, 100, 200)

    def test_keep_square_uses_shorter_edge(self):
        # 300x150 requested with keep_square: the box collapses to the
        # shorter edge (150), well above MIN_SIZE so it isn't bumped further.
        box = normalise_box(0, 0, 300, 150, 1000, 800, keep_square=True)
        assert box == (0, 0, 150, 150)

    def test_keep_square_also_respects_min_size(self):
        box = normalise_box(0, 0, 100, 50, 1000, 800, keep_square=True)
        assert box == (0, 0, MIN_SIZE, MIN_SIZE)

    def test_float_inputs_round_before_evening(self):
        box = normalise_box(10.7, 20.3, 100.4, 200.6, 1000, 800)
        # width 100.4 -> round 100 (already even); height 200.6 -> round 201 -> even 200
        assert box == (11, 20, 100, 200)

    def test_float_size_below_min_size_is_clamped_up(self):
        box = normalise_box(10.7, 20.3, 100.4, 50.6, 1000, 800)
        # height 50.6 rounds to 51, below MIN_SIZE, so it is bumped to 64.
        assert box == (11, 20, 100, MIN_SIZE)

    def test_zero_ref_width_returns_zero_box(self):
        assert normalise_box(0, 0, 100, 100, 0, 800) == (0, 0, 0, 0)

    def test_zero_ref_height_returns_zero_box(self):
        assert normalise_box(0, 0, 100, 100, 1000, 0) == (0, 0, 0, 0)

    def test_negative_ref_dims_return_zero_box(self):
        assert normalise_box(0, 0, 100, 100, -100, 800) == (0, 0, 0, 0)
        assert normalise_box(0, 0, 100, 100, 1000, -1) == (0, 0, 0, 0)

    def test_result_always_fully_inside_frame(self):
        x, y, w, h = normalise_box(900, 700, 500, 500, 1000, 800)
        assert x >= 0 and y >= 0
        assert x + w <= 1000
        assert y + h <= 800


class TestCentredBox:
    def test_centres_box_in_frame(self):
        box = centred_box(1000, 800, 200, 100)
        x, y, w, h = box
        assert (w, h) == (200, 100)
        assert x + w / 2 == 1000 / 2
        assert y + h / 2 == 800 / 2

    def test_square_variant_with_equal_dims_is_centred(self):
        box = centred_box(1000, 800, 150, 150, keep_square=True)
        x, y, w, h = box
        assert w == h == 150
        assert x + w / 2 == 1000 / 2
        assert y + h / 2 == 800 / 2

    def test_square_variant_is_always_square_and_even(self):
        box = centred_box(1000, 800, 301, 151, keep_square=True)
        _, _, w, h = box
        assert w == h
        assert w % 2 == 0


class TestSquareAroundCircle:
    def test_result_contains_the_circle(self):
        cx, cy, r = 500, 400, 100
        x, y, w, h = square_around_circle(cx, cy, r, 1000, 800)
        assert x <= cx - r
        assert y <= cy - r
        assert x + w >= cx + r
        assert y + h >= cy + r

    def test_margin_widens_the_box_beyond_bare_diameter(self):
        cx, cy, r = 500, 400, 100
        tight = square_around_circle(cx, cy, r, 1000, 800, margin_fraction=0.0)
        wide = square_around_circle(cx, cy, r, 1000, 800, margin_fraction=0.5)
        assert wide[2] > tight[2]

    def test_always_square_and_even(self):
        box = square_around_circle(123, 77, 51, 1000, 800, margin_fraction=0.02)
        _, _, w, h = box
        assert w == h
        assert w % 2 == 0
        assert h % 2 == 0

    def test_circle_near_corner_shifts_inward_without_shrinking(self):
        # Circle centred near (20, 20) with radius 50 would need negative
        # coordinates to stay centred; the box keeps its full edge length and
        # is shifted to (0, 0) instead of shrinking below the circle.
        edge = 2.0 * 50 * 1.02
        box = square_around_circle(20, 20, 50, 1000, 800, margin_fraction=0.02)
        x, y, w, h = box
        assert (x, y) == (0, 0)
        assert w == h
        assert abs(w - edge) <= 2  # only the even-rounding may move it slightly

    def test_circle_larger_than_frame_clamps_to_min_dimension(self):
        box = square_around_circle(500, 400, 1000, 1000, 800)
        _, _, w, h = box
        assert w == h == 800  # clamped to the shorter frame edge


class TestIsFullFrame:
    def test_true_when_box_covers_whole_frame(self):
        assert is_full_frame((0, 0, 1000, 800), 1000, 800) is True

    def test_false_when_box_is_smaller(self):
        assert is_full_frame((0, 0, 999, 800), 1000, 800) is False

    def test_evenness_shave_on_an_odd_frame_still_counts_as_full(self):
        assert is_full_frame((0, 0, 1000, 800), 1001, 801) is True
        assert is_full_frame((0, 0, 998, 800), 1001, 801) is False

    def test_false_when_offset_even_if_full_size(self):
        assert is_full_frame((10, 0, 1000, 800), 1000, 800) is False


class TestResolveCropBox:
    def test_none_when_disabled(self):
        assert resolve_crop_box({'enabled': False, 'width': 100, 'height': 100}, 1000, 800) is None

    def test_none_when_cfg_not_a_dict(self):
        assert resolve_crop_box(None, 1000, 800) is None
        assert resolve_crop_box("not a dict", 1000, 800) is None
        assert resolve_crop_box([1, 2, 3], 1000, 800) is None

    def test_none_when_width_zero(self):
        cfg = {'enabled': True, 'x': 0, 'y': 0, 'width': 0, 'height': 800}
        assert resolve_crop_box(cfg, 1000, 800) is None

    def test_none_when_height_zero(self):
        cfg = {'enabled': True, 'x': 0, 'y': 0, 'width': 100, 'height': 0}
        assert resolve_crop_box(cfg, 1000, 800) is None

    def test_none_when_box_is_full_frame(self):
        cfg = {'enabled': True, 'x': 0, 'y': 0, 'width': 1000, 'height': 800}
        assert resolve_crop_box(cfg, 1000, 800) is None

    def test_none_when_frame_dims_degenerate(self):
        cfg = {'enabled': True, 'x': 0, 'y': 0, 'width': 100, 'height': 100}
        assert resolve_crop_box(cfg, 0, 800) is None
        assert resolve_crop_box(cfg, 1000, 0) is None
        assert resolve_crop_box(cfg, -100, 800) is None

    def test_none_when_values_non_numeric(self):
        cfg = {'enabled': True, 'x': 'oops', 'y': 0, 'width': 100, 'height': 100}
        assert resolve_crop_box(cfg, 1000, 800) is None

    def test_scales_proportionally_when_ref_dims_differ(self):
        # Box drawn as 2880x2880 at (80, 320) on a 3552x3552 reference frame,
        # applied to a 1776x1776 frame (exactly half) should halve everything.
        cfg = {
            'enabled': True, 'x': 80, 'y': 320,
            'width': 2880, 'height': 2880,
            'ref_width': 3552, 'ref_height': 3552,
        }
        box = resolve_crop_box(cfg, 1776, 1776)
        assert box is not None
        assert (box.x, box.y, box.width, box.height) == (40, 160, 1440, 1440)
        assert (box.frame_width, box.frame_height) == (1776, 1776)

    def test_ref_dims_zero_means_same_as_frame(self):
        cfg = {
            'enabled': True, 'x': 10, 'y': 10, 'width': 200, 'height': 200,
            'ref_width': 0, 'ref_height': 0,
        }
        box = resolve_crop_box(cfg, 1000, 800)
        assert (box.x, box.y, box.width, box.height) == (10, 10, 200, 200)

    def test_result_clamped_inside_frame(self):
        cfg = {'enabled': True, 'x': 950, 'y': 750, 'width': 200, 'height': 200}
        box = resolve_crop_box(cfg, 1000, 800)
        assert box is not None
        assert box.x + box.width <= 1000
        assert box.y + box.height <= 800

    def test_carries_frame_dims_on_the_returned_box(self):
        cfg = {'enabled': True, 'x': 0, 'y': 0, 'width': 100, 'height': 100}
        box = resolve_crop_box(cfg, 1000, 800)
        assert (box.frame_width, box.frame_height) == (1000, 800)


class TestApplyOutputCrop:
    def test_returns_same_object_and_none_when_disabled(self):
        img = Image.new('RGB', (100, 100))
        result_img, box = apply_output_crop(img, {'enabled': False})
        assert result_img is img
        assert box is None

    def test_returns_same_object_and_none_when_cfg_missing_crop(self):
        img = Image.new('RGB', (100, 100))
        result_img, box = apply_output_crop(img, {})
        assert result_img is img
        assert box is None

    def test_none_image_passes_through(self):
        result_img, box = apply_output_crop(None, {'enabled': True, 'width': 100, 'height': 100})
        assert result_img is None
        assert box is None

    def test_crops_to_expected_size_and_content(self):
        # 200x200 image; a distinguishable red pixel at (150, 20) should land
        # inside a 64x64 crop taken at offset (100, 0) — proving both the size
        # and the offset are applied, not just the size.
        img = Image.new('RGB', (200, 200), color=(0, 0, 0))
        img.putpixel((150, 20), (255, 0, 0))

        cfg = {'enabled': True, 'x': 100, 'y': 0, 'width': 64, 'height': 64}
        cropped, box = apply_output_crop(img, cfg)

        assert cropped is not img
        assert cropped.size == (64, 64)
        assert box.pil_box == (100, 0, 164, 64)
        # (150, 20) in the source is (50, 20) in the cropped image.
        assert cropped.getpixel((50, 20)) == (255, 0, 0)
        # A corner far from the marked pixel should stay black.
        assert cropped.getpixel((0, 0)) == (0, 0, 0)

    def test_no_crop_applied_when_box_resolves_to_full_frame(self):
        img = Image.new('RGB', (100, 100))
        cfg = {'enabled': True, 'x': 0, 'y': 0, 'width': 100, 'height': 100}
        result_img, box = apply_output_crop(img, cfg)
        assert result_img is img
        assert box is None


class TestCropBox:
    def test_pil_box_is_left_upper_right_lower(self):
        box = CropBox(10, 20, 100, 200, 1000, 800)
        assert box.pil_box == (10, 20, 110, 220)

    def test_as_metadata_contains_all_fields(self):
        box = CropBox(10, 20, 100, 200, 1000, 800)
        assert box.as_metadata() == {
            'x': 10, 'y': 20, 'width': 100, 'height': 200,
            'frame_width': 1000, 'frame_height': 800,
        }

    def test_from_metadata_round_trip(self):
        box = CropBox(10, 20, 100, 200, 1000, 800)
        restored = CropBox.from_metadata(box.as_metadata())
        assert restored == box

    def test_from_metadata_coerces_numeric_strings(self):
        # JSON round trips sometimes leave numbers as strings; int() handles it.
        data = {'x': '10', 'y': '20', 'width': '100', 'height': '200',
                'frame_width': '1000', 'frame_height': '800'}
        restored = CropBox.from_metadata(data)
        assert restored == CropBox(10, 20, 100, 200, 1000, 800)

    def test_from_metadata_returns_none_for_non_dict(self):
        assert CropBox.from_metadata(None) is None
        assert CropBox.from_metadata("junk") is None
        assert CropBox.from_metadata([1, 2, 3]) is None

    def test_from_metadata_returns_none_for_missing_keys(self):
        assert CropBox.from_metadata({'x': 10}) is None
        assert CropBox.from_metadata({}) is None

    def test_from_metadata_returns_none_for_non_numeric_values(self):
        data = {'x': 'oops', 'y': 20, 'width': 100, 'height': 200,
                'frame_width': 1000, 'frame_height': 800}
        assert CropBox.from_metadata(data) is None


class TestDescribe:
    def test_full_frame_when_disabled(self):
        assert describe({'enabled': False, 'width': 100, 'height': 100}) == "Full frame"

    def test_full_frame_when_cfg_not_a_dict(self):
        assert describe(None) == "Full frame"
        assert describe("junk") == "Full frame"

    def test_full_frame_when_width_or_height_zero(self):
        assert describe({'enabled': True, 'width': 0, 'height': 100, 'x': 0, 'y': 0}) == "Full frame"
        assert describe({'enabled': True, 'width': 100, 'height': 0, 'x': 0, 'y': 0}) == "Full frame"

    def test_full_frame_when_values_non_numeric(self):
        assert describe({'enabled': True, 'width': 'oops', 'height': 100, 'x': 0, 'y': 0}) == "Full frame"

    def test_formats_enabled_crop(self):
        result = describe({'enabled': True, 'width': 2880, 'height': 2880, 'x': 80, 'y': 320})
        assert result == "2880×2880 at (80, 320)"


class TestConfigDefaultIntegration:
    def test_default_config_output_crop_matches_module_default(self):
        from services.config_defaults import DEFAULT_CONFIG
        from services.output_crop import DEFAULT_OUTPUT_CROP
        assert DEFAULT_CONFIG['output_crop'] == DEFAULT_OUTPUT_CROP

    def test_default_config_output_crop_is_an_independent_copy(self):
        from services.config_defaults import DEFAULT_CONFIG
        from services.output_crop import DEFAULT_OUTPUT_CROP
        DEFAULT_CONFIG['output_crop']['enabled'] = True
        try:
            assert DEFAULT_OUTPUT_CROP['enabled'] is False
        finally:
            DEFAULT_CONFIG['output_crop']['enabled'] = False

    def test_old_config_json_without_key_still_gets_the_default(self, temp_config):
        from services.config import Config
        from services.output_crop import DEFAULT_OUTPUT_CROP

        old_config = {'capture_mode': 'camera'}  # predates output_crop entirely
        with open(temp_config, 'w') as f:
            json.dump(old_config, f)

        config = Config(temp_config)
        assert config.data['output_crop'] == DEFAULT_OUTPUT_CROP

    def test_partial_output_crop_block_is_backfilled(self, temp_config):
        from services.config import Config

        partial_config = {'output_crop': {'enabled': True}}
        with open(temp_config, 'w') as f:
            json.dump(partial_config, f)

        config = Config(temp_config)
        crop = config.data['output_crop']
        assert crop['enabled'] is True
        assert 'width' in crop and 'height' in crop
        assert 'ref_width' in crop and 'ref_height' in crop
