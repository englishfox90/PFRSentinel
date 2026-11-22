"""Synthetic frames for the Alpaca camera simulator.

Renders the full sensor as a CFA mosaic (or mono / three planes), in ADU,
for a given exposure, gain and offset:

- ``sky``: an all-sky disc of night sky with a few hundred fixed stars,
  background and star signal both scaling with ``exposure_s * gain_factor``
  (so an auto-exposure loop has something to converge on), shot and read
  noise, and a dark corner outside the lens circle.
- ``red_top_blue_bottom``: the top half red, the bottom half blue, at fixed
  levels — the colour-orientation check for every Bayer phase.
- ``coords``: raw value ``(y * width + x) % (max_adu + 1)``, no CFA and no
  noise — the array-orientation check.

Independent of ``services/``: the simulator is a cross-check on the client.
"""
import numpy as np

SCENES = ("sky", "red_top_blue_bottom", "coords")
_RGGB = (("R", "G"), ("G", "B"))
_CHANNEL = {"R": 0, "G": 1, "B": 2}

# Fractions of full scale per second at gain factor 1.
SKY_RATE = (0.010, 0.014, 0.022)   # dim blue-ish night sky
STAR_SIGMA_PX = 1.3


def gain_factor(gain_value: float) -> float:
    """ZWO-style gain in 0.1 dB steps: gain 200 → 10x."""
    return float(10.0 ** (float(gain_value) / 200.0))


def cfa_channel_map(height: int, width: int, offset_x: int, offset_y: int) -> np.ndarray:
    """(H, W) channel index (0 R, 1 G, 2 B) of each sensor pixel for SensorType RGGB."""
    ys = (np.arange(height)[:, None] + offset_y) % 2
    xs = (np.arange(width)[None, :] + offset_x) % 2
    lut = np.array([[_CHANNEL[_RGGB[r][c]] for c in (0, 1)] for r in (0, 1)])
    return lut[ys, xs]


class SceneGenerator:
    """Deterministic star field over a full sensor of ``width`` x ``height``."""

    def __init__(self, width: int, height: int, kind: str = "sky", seed: int = 43,
                 n_stars=None):
        if kind not in SCENES:
            raise ValueError(f"Unknown scene {kind!r}; choose from {SCENES}")
        self.width, self.height, self.kind = int(width), int(height), kind
        self._noise = np.random.default_rng(seed + 1)
        rng = np.random.default_rng(seed)
        self.cx, self.cy = self.width / 2.0, self.height / 2.0
        self.radius = 0.48 * min(self.width, self.height)
        if n_stars is None:
            n_stars = int(np.clip(self.width * self.height / 8000, 20, 600))
        r = self.radius * np.sqrt(rng.uniform(0, 0.97, n_stars))
        theta = rng.uniform(0, 2 * np.pi, n_stars)
        self.star_x = self.cx + r * np.cos(theta)
        self.star_y = self.cy + r * np.sin(theta)
        # Power-law brightness: many faint stars, a handful that saturate.
        self.star_rate = 0.02 * (1.0 / rng.uniform(0.002, 1.0, n_stars)) ** 0.9
        warmth = rng.uniform(-0.3, 0.3, n_stars)
        self.star_rgb = np.stack([1 + warmth, np.ones(n_stars), 1 - warmth], axis=1)

    # -- public ----------------------------------------------------------
    def render(self, *, exposure_s: float, gain: float, offset: int, max_adu: int,
               sensor_type: int, bayer_offset_x: int = 0, bayer_offset_y: int = 0):
        """Full-sensor frame in ADU: (H, W) int32, or (H, W, 3) for SensorType 1."""
        h, w = self.height, self.width
        if self.kind == "coords":
            idx = np.arange(h * w, dtype=np.int64).reshape(h, w)
            frame = idx % (int(max_adu) + 1)
            if sensor_type == 1:
                frame = np.repeat(frame[:, :, None], 3, axis=2)
            return frame.astype(np.int32)

        rgb_rates = self._rgb_signal(exposure_s, gain, max_adu)  # (H, W, 3) ADU
        if sensor_type == 1:
            signal = rgb_rates
        elif sensor_type == 2:
            ch = cfa_channel_map(h, w, bayer_offset_x, bayer_offset_y)
            signal = np.take_along_axis(rgb_rates, ch[:, :, None], axis=2)[:, :, 0]
        else:
            signal = rgb_rates.mean(axis=2)
        pedestal = float(offset) * (int(max_adu) + 1) / 1024.0
        if self.kind == "sky":
            read_noise = 0.002 * max_adu
            sigma = np.sqrt(read_noise ** 2 + np.maximum(signal, 0) * max(1.0, max_adu / 4095))
            signal = signal + self._noise.standard_normal(signal.shape, dtype=np.float32) * sigma
        frame = np.clip(np.rint(signal + pedestal), 0, int(max_adu))
        return frame.astype(np.int32)

    # -- internals -------------------------------------------------------
    def _rgb_signal(self, exposure_s, gain, max_adu) -> np.ndarray:
        h, w = self.height, self.width
        if self.kind == "red_top_blue_bottom":
            rgb = np.full((h, w, 3), 0.05 * max_adu, dtype=np.float32)
            rgb[: h // 2, :, 0] = 0.6 * max_adu
            rgb[h // 2:, :, 2] = 0.6 * max_adu
            return rgb
        scale = float(exposure_s) * gain_factor(gain) * float(max_adu)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        rr = np.hypot(xx - self.cx, yy - self.cy) / self.radius
        disc = np.clip((1.0 - rr) * 12.0, 0.0, 1.0)          # soft lens edge
        horizon = 1.0 + 1.5 * np.clip(rr, 0, 1) ** 4          # light dome low down
        sky = (disc * horizon)[:, :, None] * np.asarray(SKY_RATE, np.float32)[None, None, :]
        rgb = sky * scale
        self._add_stars(rgb, scale)
        return rgb

    def _add_stars(self, rgb: np.ndarray, scale: float) -> None:
        h, w = rgb.shape[:2]
        half = 4
        offs = np.arange(-half, half + 1)
        for x, y, rate, colour in zip(self.star_x, self.star_y, self.star_rate, self.star_rgb):
            ix, iy = int(round(x)), int(round(y))
            x0, x1 = max(ix - half, 0), min(ix + half + 1, w)
            y0, y1 = max(iy - half, 0), min(iy + half + 1, h)
            if x0 >= x1 or y0 >= y1:
                continue
            gx = np.exp(-((offs + ix - x) ** 2) / (2 * STAR_SIGMA_PX ** 2))
            gy = np.exp(-((offs + iy - y) ** 2) / (2 * STAR_SIGMA_PX ** 2))
            stamp = np.outer(gy, gx)[y0 - (iy - half):y1 - (iy - half),
                                     x0 - (ix - half):x1 - (ix - half)]
            rgb[y0:y1, x0:x1, :] += (stamp[:, :, None] * (rate * scale)
                                     * colour[None, None, :]).astype(np.float32)
