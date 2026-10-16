"""
Label size presets for the all-sky overlay.

Every layer has its own ``label_size`` in config (constellations 12, bright
stars 11, Messier 10, NGC 9, planets 14, grid 14, NINA target 13), in the renderers' base
units — pixels on a 750 px frame, scaled up with the image. One preset on
the All-Sky page shifts all of them together, 2 base px per step, so the
layers keep their relative sizes. 'large' is the size the app has always
drawn, so an existing config looks the same.
"""
from typing import Tuple

CONFIG_KEY = 'label_size_preset'
DEFAULT_PRESET = 'large'
STEP_PX = 2
# No preset may take a layer below this, whatever its configured size.
MIN_LABEL_PX = 5

# (config value, display name, steps from 'large')
PRESETS: Tuple[Tuple[str, str, int], ...] = (
    ('too_small', 'Too small', -3),
    ('small', 'Small', -2),
    ('normal', 'Normal', -1),
    ('large', 'Large', 0),
    ('why_so_large', 'Why so large', 1),
)

_LAYERS = ('constellations', 'bright_stars', 'messier', 'ngc', 'planets', 'grid',
           'nina_target')
_OFFSETS = {key: steps * STEP_PX for key, _, steps in PRESETS}


def preset_keys() -> Tuple[str, ...]:
    return tuple(key for key, _, _ in PRESETS)


def preset_names() -> Tuple[str, ...]:
    return tuple(name for _, name, _ in PRESETS)


def preset_offset_px(preset) -> int:
    """Base pixels added to every layer's ``label_size``; unknown → 'large'."""
    return _OFFSETS.get(str(preset or DEFAULT_PRESET), 0)


def sized_label_px(configured, preset) -> int:
    """A layer's ``label_size`` after the preset, never under MIN_LABEL_PX."""
    try:
        base = int(round(float(configured)))
    except (TypeError, ValueError):
        base = MIN_LABEL_PX
    return max(MIN_LABEL_PX, base + preset_offset_px(preset))


def apply_label_size_preset(allsky_cfg: dict) -> dict:
    """The allsky_overlay config with each layer's ``label_size`` shifted by
    the preset. A shallow copy: only the layer dicts that carry a label
    size are replaced, so the caller's dict and its other sub-dicts are
    untouched. 'large' returns the config as it is."""
    preset = allsky_cfg.get(CONFIG_KEY, DEFAULT_PRESET)
    if preset_offset_px(preset) == 0:
        return allsky_cfg
    cfg = dict(allsky_cfg)
    for layer in _LAYERS:
        layer_cfg = cfg.get(layer)
        if isinstance(layer_cfg, dict) and 'label_size' in layer_cfg:
            cfg[layer] = dict(layer_cfg, label_size=sized_label_px(layer_cfg['label_size'], preset))
    return cfg
