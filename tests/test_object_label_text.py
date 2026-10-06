"""
Tests for services/allsky/object_label_text.py — the Messier / NGC label
text style (issue #144): the common name alone by default, the old
"Andromeda Galaxy (M31)", or the catalogue number.
"""
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

from services.allsky.catalogs import get_messier_objects
from services.allsky.coords import radec_to_altaz
from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid
from services.allsky.object_label_text import (
    CONFIG_KEY, DEFAULT_STYLE, known_style, object_label_text, style_keys, style_names,
)
from services.allsky.render_objects import render_messier
from services.config_defaults import DEFAULT_CONFIG

DT = datetime(2024, 6, 21, 22, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1


class TestText:
    def test_the_common_name_alone(self):
        assert object_label_text('Andromeda Galaxy', 'M31', 'name') == 'Andromeda Galaxy'

    def test_name_and_number_is_the_old_form(self):
        assert (object_label_text('Andromeda Galaxy', 'M31', 'name_number')
                == 'Andromeda Galaxy (M31)')

    def test_the_number_alone(self):
        assert object_label_text('Owl Cluster', 'NGC0457', 'number') == 'NGC0457'

    @pytest.mark.parametrize('style', ['name', 'name_number', 'number'])
    def test_no_common_name_falls_back_to_the_number(self, style):
        assert object_label_text('', 'M2', style) == 'M2'
        assert object_label_text(None, 'M2', style) == 'M2'
        assert object_label_text('   ', 'M2', style) == 'M2'

    def test_the_name_is_trimmed(self):
        assert object_label_text(' Ring Nebula ', 'M57', 'name') == 'Ring Nebula'

    @pytest.mark.parametrize('style', ['name', 'name_number'])
    def test_several_names_show_the_first(self, style):
        text = object_label_text('Eagle Nebula,Star Queen', 'IC4703', style)
        assert text.startswith('Eagle Nebula') and 'Star Queen' not in text

    @pytest.mark.parametrize('style', [None, '', 'fancy', 3])
    def test_an_unknown_style_reads_as_the_default(self, style):
        assert known_style(style) == DEFAULT_STYLE == 'name'
        assert object_label_text('Ring Nebula', 'M57', style) == 'Ring Nebula'

    def test_every_style_has_a_display_name(self):
        assert len(style_keys()) == len(style_names()) == 3
        assert DEFAULT_STYLE in style_keys()

    def test_both_layers_default_to_the_common_name(self):
        overlay = DEFAULT_CONFIG['allsky_overlay']
        assert overlay['messier'][CONFIG_KEY] == 'name'
        assert overlay['ngc'][CONFIG_KEY] == 'name'


def _model() -> FisheyeModel:
    return FisheyeModel(
        cx=375.0, cy=375.0, a1=215.0, a3=0.0, a5=0.0,
        roll=0.0, axis_alt=90.0, axis_az=0.0,
        rms_residual=1.0, n_matches=50,
        calibrated_at="2024-01-01T00:00:00+00:00",
        image_width=750, image_height=750,
    )


def _drawn_width(style) -> int:
    """Width in pixels of everything one named Messier object's label draws."""
    named = None
    for obj in get_messier_objects():
        alt, _ = radec_to_altaz(obj['ra_deg'], obj['dec_deg'], LAT, LON, DT, refraction=True)
        if obj.get('label') and (obj.get('name') or '').strip() and float(alt) > 40.0:
            named = obj['label']
            break
    if named is None:
        pytest.skip('no named Messier object high up at the test instant')
    img = Image.new('RGBA', (750, 750), (0, 0, 0, 255))
    out = render_messier(img, _model(), {'enabled': True, CONFIG_KEY: style},
                         LAT, LON, DT, LabelGrid(750, 750),
                         allowed_ids={f'messier:{named}'},
                         sky_gray=np.full((750, 750), 255, dtype=np.uint8))
    cols = np.flatnonzero(np.asarray(out)[..., 0].max(axis=0))
    return int(cols[-1] - cols[0] + 1) if len(cols) else 0


def test_the_renderer_draws_the_chosen_style():
    number, name, both = (_drawn_width(s) for s in ('number', 'name', 'name_number'))
    assert 0 < number < name < both


def test_the_renderer_reads_a_missing_style_as_the_common_name():
    assert _drawn_width(None) == _drawn_width('name')
