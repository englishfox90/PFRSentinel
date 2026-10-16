"""
Display stretch for the guided-calibration frame.

The dialog shows the linear frame the solver fits in, brightened so the stars
can be seen at all (a correctly exposed all-sky frame has a median near 2/255,
issue #10). One fixed stretch — the detector's own 1st–99th percentile mapping
— suited the reference rig, but on a moonlit hosting-site sky it reads as a
washed-out frame far brighter than the app's own output, with the suggestion
labels lost on it (discussion #105). The user now picks how hard to stretch.

Display only. The frame the solver fits and the detections a click snaps to
never pass through here; ``DisplayStretch.render`` returns a new image and
leaves the linear frame untouched, so anchor pixels stay in the solver's own
pixel space whatever the slider says.
"""
import numpy as np
from PIL import Image

# Slider strength giving the detector's own p1–p99 stretch, the pre-#105
# behaviour and the middle of the slider.
DEFAULT_STRENGTH = 0.5

_BLACK_PERCENTILE = 1.0
# The two ends of the slider, as the fraction of pixels mapped to white:
# 0.01 % saturates only the brightest star cores, 10 % turns the sky itself
# white. The detector's 1 % sits at strength 0.5.
_CLIP_SOFT = 1e-4
_CLIP_DETECTOR = 1e-2
_CLIP_HARD = 1e-1


def clip_fraction(strength: float) -> float:
    """Fraction of pixels mapped to white for a slider strength in [0, 1].

    Log-linear on each side of the detector's 1 % at 0.5, so equal slider
    steps look like equal steps in brightness rather than a dead zone at one
    end and a cliff at the other.
    """
    s = min(max(float(strength), 0.0), 1.0)
    if s <= 0.5:
        lo, hi, t = _CLIP_SOFT, _CLIP_DETECTOR, s / 0.5
    else:
        lo, hi, t = _CLIP_DETECTOR, _CLIP_HARD, (s - 0.5) / 0.5
    return float(10.0 ** (np.log10(lo) + (np.log10(hi) - np.log10(lo)) * t))


def white_percentile(strength: float) -> float:
    """Percentile mapped to full white at this strength (99.0 at 0.5)."""
    return 100.0 * (1.0 - clip_fraction(strength))


class DisplayStretch:
    """A frame's brightness histogram, ready to render at any strength.

    The histogram is taken once; each ``render`` is then one 256-entry lookup
    over the frame (tens of ms on a 12 MP frame), fast enough to follow a
    slider on the GUI thread.
    """

    def __init__(self, image):
        self._rgb = image if getattr(image, 'mode', None) == 'RGB' \
            else image.convert('RGB')
        counts = np.asarray(self._rgb.histogram(), dtype=np.int64)
        self._cdf = np.cumsum(counts.reshape(3, 256).sum(axis=0))

    @property
    def size(self):
        return self._rgb.size

    def level_at(self, percentile: float) -> float:
        """The 8-bit level below which `percentile` % of samples fall."""
        total = int(self._cdf[-1])
        if total <= 0:
            return 0.0
        target = min(max(percentile, 0.0), 100.0) / 100.0 * total
        return float(min(int(np.searchsorted(self._cdf, target)), 255))

    def levels(self, strength: float):
        """(black, white) 8-bit levels the stretch maps to 0 and 255."""
        return (self.level_at(_BLACK_PERCENTILE),
                self.level_at(white_percentile(strength)))

    def render(self, strength: float = DEFAULT_STRENGTH) -> Image.Image:
        """A new RGB image stretched for display; the source is untouched."""
        lo, hi = self.levels(strength)
        if hi <= lo:
            # A flat frame has nothing to stretch; black, like the detector's
            # percentile_stretch, rather than noise blown up to full range.
            return Image.new('RGB', self._rgb.size)
        table = ((np.arange(256, dtype=np.float32) - lo) / (hi - lo) * 255.0)
        lut = table.clip(0.0, 255.0).astype(np.uint8).tolist()
        return self._rgb.point(lut * 3)
