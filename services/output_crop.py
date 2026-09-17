"""
Output framing — crop the *output* image to a sub-rectangle of the frame.

Issue #12: an all-sky lens rarely fills the sensor, so users want the saved
files / web frame / timelapse to carry the sky disc and not the black margins.
This is deliberately an output-stage crop, not a sensor ROI: every analysis
stage (auto-exposure, auto-stretch, ML roof/sky classifiers, all-sky
calibration, meteor detection) keeps seeing the full frame — their statistics
and pixel geometry are anchored on the dark corners and on the full-frame
optical centre — and only the rendered outputs are cut down.

The box is stored in pixels of a *reference* frame (``ref_width`` x
``ref_height``: the frame the user drew it on). When a frame of a different
size arrives (resize_percent, a different camera, watch-mode files) the box is
scaled proportionally, so the same region of the sky is kept.

Pure geometry + one PIL crop; no config or Qt imports.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

DEFAULT_OUTPUT_CROP = {
    "enabled": False,
    "x": 0,
    "y": 0,
    "width": 0,
    "height": 0,
    "ref_width": 0,
    "ref_height": 0,
    "keep_square": True,   # UI-only: lock the editor box to a square
}

# Smallest useful output edge. Also keeps a stray drag from producing a
# 2-pixel "image" that downstream encoders reject.
MIN_SIZE = 64

# Key under which the applied crop travels in the frame metadata dict so the
# all-sky renderer can translate its calibration model into output pixels.
METADATA_KEY = 'OUTPUT_CROP'

Box = Tuple[int, int, int, int]  # x, y, width, height


@dataclass(frozen=True)
class CropBox:
    """A crop resolved against a concrete frame — the box and the frame it cuts."""
    x: int
    y: int
    width: int
    height: int
    frame_width: int
    frame_height: int

    @property
    def pil_box(self) -> Tuple[int, int, int, int]:
        """PIL ``Image.crop`` tuple (left, upper, right, lower)."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    def as_metadata(self) -> dict:
        return {
            'x': self.x, 'y': self.y,
            'width': self.width, 'height': self.height,
            'frame_width': self.frame_width, 'frame_height': self.frame_height,
        }

    @classmethod
    def from_metadata(cls, data) -> Optional['CropBox']:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                int(data['x']), int(data['y']),
                int(data['width']), int(data['height']),
                int(data['frame_width']), int(data['frame_height']),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _even(value: int) -> int:
    """Round down to an even number (video encoders need even dimensions)."""
    return int(value) - (int(value) % 2)


def normalise_box(x, y, width, height, ref_width: int, ref_height: int,
                  keep_square: bool = False) -> Box:
    """Clamp a box into a ``ref_width`` x ``ref_height`` frame.

    Guarantees: even width/height, at least ``MIN_SIZE`` on each axis (or the
    whole frame if it is smaller than that), fully inside the frame. With
    ``keep_square`` the shorter edge wins. Degenerate reference dimensions
    return a zero box, which ``resolve_crop_box`` treats as "no crop".
    """
    ref_width, ref_height = int(ref_width), int(ref_height)
    if ref_width <= 0 or ref_height <= 0:
        return (0, 0, 0, 0)

    width = int(round(width))
    height = int(round(height))
    if keep_square:
        width = height = min(width, height)

    min_w = min(MIN_SIZE, ref_width)
    min_h = min(MIN_SIZE, ref_height)
    width = max(min_w, min(width, ref_width))
    height = max(min_h, min(height, ref_height))
    if keep_square:
        width = height = min(width, height, ref_width, ref_height)
    width = max(_even(width), min(2, ref_width))
    height = max(_even(height), min(2, ref_height))

    x = max(0, min(int(round(x)), ref_width - width))
    y = max(0, min(int(round(y)), ref_height - height))
    return (x, y, width, height)


def centred_box(ref_width: int, ref_height: int, width: int, height: int,
                keep_square: bool = False) -> Box:
    """A ``width`` x ``height`` box centred in the reference frame."""
    if keep_square:
        width = height = min(int(width), int(height))
    x = (int(ref_width) - int(width)) / 2
    y = (int(ref_height) - int(height)) / 2
    return normalise_box(x, y, width, height, ref_width, ref_height, keep_square)


def square_around_circle(cx: float, cy: float, radius: float,
                         ref_width: int, ref_height: int,
                         margin_fraction: float = 0.02) -> Box:
    """The square that just contains a circle (plus a small margin).

    Used by "Fit to sky": the all-sky sky-circle estimate becomes a crop that
    keeps the whole disc and drops the dark corners.
    """
    edge = 2.0 * float(radius) * (1.0 + max(0.0, margin_fraction))
    edge = min(edge, ref_width, ref_height)
    return normalise_box(cx - edge / 2, cy - edge / 2, edge, edge,
                         ref_width, ref_height, keep_square=True)


def is_full_frame(box: Box, ref_width: int, ref_height: int) -> bool:
    x, y, w, h = box
    return x == 0 and y == 0 and w >= ref_width and h >= ref_height


def resolve_crop_box(cfg, frame_width: int, frame_height: int) -> Optional[CropBox]:
    """Turn the stored config into a crop for a concrete frame.

    Returns ``None`` when the crop is disabled, unset, degenerate, or would
    keep the whole frame anyway. A box drawn on a differently sized reference
    frame is scaled proportionally on each axis before clamping.
    """
    if not isinstance(cfg, dict) or not cfg.get('enabled', False):
        return None
    frame_width, frame_height = int(frame_width), int(frame_height)
    if frame_width <= 0 or frame_height <= 0:
        return None
    try:
        x, y = float(cfg.get('x', 0)), float(cfg.get('y', 0))
        width, height = float(cfg.get('width', 0)), float(cfg.get('height', 0))
        ref_w, ref_h = int(cfg.get('ref_width', 0) or 0), int(cfg.get('ref_height', 0) or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None

    if ref_w > 0 and ref_h > 0 and (ref_w != frame_width or ref_h != frame_height):
        sx, sy = frame_width / ref_w, frame_height / ref_h
        x, y, width, height = x * sx, y * sy, width * sx, height * sy

    box = normalise_box(x, y, width, height, frame_width, frame_height)
    if box[2] <= 0 or box[3] <= 0 or is_full_frame(box, frame_width, frame_height):
        return None
    return CropBox(*box, frame_width=frame_width, frame_height=frame_height)


def apply_output_crop(img, cfg):
    """Crop a PIL image per the config. Returns ``(image, CropBox | None)``.

    The input object is returned untouched (same object) when no crop applies,
    so callers must treat the result as read-only in that case.
    """
    if img is None:
        return img, None
    box = resolve_crop_box(cfg, img.width, img.height)
    if box is None:
        return img, None
    return img.crop(box.pil_box), box


def describe(cfg) -> str:
    """Short human-readable summary for status text, e.g. '2880×2880 at (80, 320)'."""
    if not isinstance(cfg, dict) or not cfg.get('enabled', False):
        return "Full frame"
    try:
        w, h = int(cfg.get('width', 0)), int(cfg.get('height', 0))
        x, y = int(cfg.get('x', 0)), int(cfg.get('y', 0))
    except (TypeError, ValueError):
        return "Full frame"
    if w <= 0 or h <= 0:
        return "Full frame"
    return f"{w}×{h} at ({x}, {y})"
