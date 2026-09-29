"""services/allsky/label_size — one preset shifts every layer's label size.

'large' is what the app always drew, so an existing config renders the
same; each step is 2 base px on every layer; nothing goes under the floor;
the renderer applies it before any layer reads its ``label_size``.
"""
import copy

import pytest

from services.allsky import overlay_renderer
from services.allsky.label_size import (
    CONFIG_KEY, DEFAULT_PRESET, MIN_LABEL_PX, PRESETS, STEP_PX,
    apply_label_size_preset, preset_keys, preset_names, preset_offset_px, sized_label_px,
)
from services.config_defaults import DEFAULT_CONFIG

LAYERS = ('constellations', 'bright_stars', 'messier', 'ngc', 'planets', 'grid')


def _allsky():
    return copy.deepcopy(DEFAULT_CONFIG['allsky_overlay'])


class TestPresets:
    def test_five_presets_in_size_order_with_large_as_the_default(self):
        assert preset_keys() == ('too_small', 'small', 'normal', 'large', 'why_so_large')
        assert preset_names() == ('Too small', 'Small', 'Normal', 'Large', 'Why so large')
        assert DEFAULT_PRESET == 'large' and DEFAULT_CONFIG['allsky_overlay'][CONFIG_KEY] == 'large'
        offsets = [preset_offset_px(k) for k in preset_keys()]
        assert offsets == sorted(offsets)

    def test_two_pixels_per_step_around_large(self):
        assert STEP_PX == 2
        assert preset_offset_px('large') == 0
        assert preset_offset_px('normal') == -2
        assert preset_offset_px('small') == -4
        assert preset_offset_px('too_small') == -6
        assert preset_offset_px('why_so_large') == 2

    def test_unknown_or_missing_preset_reads_as_large(self):
        assert preset_offset_px('enormous') == 0
        assert preset_offset_px(None) == 0
        assert preset_offset_px('') == 0

    def test_display_names_match_the_config_values(self):
        assert len(PRESETS) == len(preset_keys()) == len(preset_names())


class TestSizedLabel:
    def test_offset_is_added_to_the_configured_size(self):
        assert sized_label_px(11, 'why_so_large') == 13
        assert sized_label_px(11, 'normal') == 9
        assert sized_label_px(11, 'large') == 11

    def test_never_under_the_floor(self):
        assert sized_label_px(9, 'too_small') == MIN_LABEL_PX
        assert sized_label_px(2, 'large') == MIN_LABEL_PX

    def test_garbage_configured_size_falls_to_the_floor_not_an_exception(self):
        assert sized_label_px('big', 'large') == MIN_LABEL_PX
        assert sized_label_px(None, 'why_so_large') == MIN_LABEL_PX + STEP_PX


class TestApplyPreset:
    def test_large_returns_the_config_untouched(self):
        cfg = _allsky()
        assert apply_label_size_preset(cfg) is cfg

    @pytest.mark.parametrize("preset", ['too_small', 'small', 'normal', 'why_so_large'])
    def test_every_layer_moves_by_the_same_offset(self, preset):
        cfg = _allsky()
        cfg[CONFIG_KEY] = preset
        out = apply_label_size_preset(cfg)
        for layer in LAYERS:
            expected = max(MIN_LABEL_PX, cfg[layer]['label_size'] + preset_offset_px(preset))
            assert out[layer]['label_size'] == expected, layer

    def test_layers_keep_their_relative_order_where_the_floor_allows(self):
        cfg = _allsky()
        cfg[CONFIG_KEY] = 'why_so_large'
        out = apply_label_size_preset(cfg)
        assert out['planets']['label_size'] > out['bright_stars']['label_size'] \
            > out['ngc']['label_size']

    def test_the_callers_dict_is_never_mutated(self):
        cfg = _allsky()
        cfg[CONFIG_KEY] = 'small'
        before = copy.deepcopy(cfg)
        out = apply_label_size_preset(cfg)
        assert cfg == before
        assert out is not cfg and out['bright_stars'] is not cfg['bright_stars']
        assert out['burn_into_output'] is cfg['burn_into_output']

    def test_a_layer_without_a_label_size_is_left_alone(self):
        cfg = _allsky()
        cfg[CONFIG_KEY] = 'small'
        del cfg['grid']['label_size']
        cfg['messier'] = 'not a dict'
        out = apply_label_size_preset(cfg)
        assert 'label_size' not in out['grid'] and out['messier'] == 'not a dict'


class TestRendererAppliesIt:
    def test_the_overlay_entry_point_resizes_before_the_layers_see_the_config(self, monkeypatch):
        """Past the enabled check, render_allsky_overlay hands the layers a
        preset-adjusted config; the model load is the first thing that reads
        it, so a spy there sees what every layer will see."""
        seen = {}

        def spy(config):
            seen['config'] = config
            return None  # "not calibrated": the render stops here

        monkeypatch.setattr(overlay_renderer, '_load_model', spy)
        from PIL import Image

        cfg = _allsky()
        cfg.update(enabled=True, calibration_file='x.json')
        cfg[CONFIG_KEY] = 'why_so_large'
        img = Image.new('RGB', (64, 64))
        assert overlay_renderer.render_allsky_overlay(img, cfg, {}) is img
        assert seen['config']['bright_stars']['label_size'] == \
            cfg['bright_stars']['label_size'] + STEP_PX
        assert cfg['bright_stars']['label_size'] == DEFAULT_CONFIG['allsky_overlay'][
            'bright_stars']['label_size']
