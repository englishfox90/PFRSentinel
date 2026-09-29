"""
"Is this sensor pixel on equipment?" for a frame the equipment map was not
learned on.

The equipment map (obstruction_map) is learned on the OUTPUT frame: after
resize_percent and the output crop, on the label vote's reduced grid, and it
is deliberately never rescaled. The guided-calibration dialog works on the
cached raw frame at sensor resolution, so a question such as "does this
predicted star sit on the mount?" needs the sensor pixel carried into the
map's own frame first: scale by the resize, translate by the crop, index the
grid. That translation is all this module does.

The answer is a callable bound to a snapshot of the map, so a worker thread
can ask it thousands of times without touching the map's lock, or None when
the map cannot answer for this frame: it has learned nothing yet, or its
stamp does not derive from a frame of this shape by a uniform resize. A
pixel the output frame never contained (cropped away) reads as sky, as does
every pixel the map has no evidence about — the map removes places it has
watched, it never invents an obstruction.
"""
from typing import Callable, Optional

import numpy as np

from .label_stability import vote_grid

# Resize is uniform in both axes; a stamp whose x and y scales differ by more
# than this came from something other than a resize of this frame.
_ASPECT_TOLERANCE = 0.01

SkyLookup = Callable[[float, float], bool]


def sky_lookup_for_frame(obs_map, frame_width: int, frame_height: int,
                         ) -> Optional[SkyLookup]:
    """An ``is_sky(x, y)`` for sensor-resolution pixels, or None."""
    try:
        w, h = int(frame_width), int(frame_height)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or obs_map is None:
        return None
    stamp = obs_map.stamp
    if stamp is None:
        return None
    out_w, out_h, crop = stamp
    small = obs_map.small_sky_mask(out_w, out_h, crop)
    if small is None:
        return None

    if crop:
        # (x, y, width, height, frame_width, frame_height): the crop box in
        # the RESIZED frame, whose size is the last pair.
        if len(crop) != 6:
            return None
        ox, oy, _cw, _ch, resized_w, resized_h = (float(v) for v in crop)
    else:
        ox = oy = 0.0
        resized_w, resized_h = float(out_w), float(out_h)
    sx, sy = resized_w / w, resized_h / h
    if sx <= 0 or sy <= 0 or abs(sx - sy) > _ASPECT_TOLERANCE * max(sx, sy):
        return None

    step, (rows, cols) = vote_grid((out_h, out_w))
    plane = np.ascontiguousarray(small, dtype=bool)
    if plane.shape != (rows, cols):
        return None

    def is_sky(x: float, y: float) -> bool:
        u = x * sx - ox
        v = y * sy - oy
        if not (0.0 <= u < out_w and 0.0 <= v < out_h):
            return True
        return bool(plane[int(v) // step, int(u) // step])

    return is_sky
