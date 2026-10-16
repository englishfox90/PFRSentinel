"""
Frame static score — is a frame scene, or only sensor noise?

A closed roof at night with the exposure pinned at its ceiling produces a frame
that is nothing but read noise. The roof and sky classifiers have no "no
information" output, so on such a frame they return a near-coin-flip "Open" and
a confident "Clear" (support bundle 2026-09-20, ASI662MC).

The measure rests on one property of noise: it is independent from pixel to
pixel, so averaging N pixels shrinks its spread by sqrt(N). Going from fine
blocks to coarse blocks 4x wider averages 16x the pixels, so pure noise drops to
0.25 of its fine-block spread. Scene structure is correlated across blocks and
barely drops, so its ratio stays near 1. The ratio does not depend on
brightness, gain or bit depth, so it needs no per-camera tuning.

REPORTING ONLY. Nothing may gate a roof identification on this until it has been
validated on real closed-roof and underexposed open-roof frames: a rig whose
open-roof raw sits at ~4/255 (issue #13) is exactly where a wrong "static"
verdict would cost real readings.
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Pure noise sits at 0.25; the dimmest real scene measured sat at ~0.7.
STATIC_RATIO_THRESHOLD = 0.5

_COARSE_FACTOR = 4
_FINE_BLOCKS_ACROSS = 64   # fine block = short side / this
_MIN_FINE_BLOCK = 4
_MIN_COARSE_BLOCKS = 64    # fewer than this and the robust sigma is itself noise
_TREND_DEGREE = 2


@dataclass(frozen=True)
class StaticScore:
    ratio: float       # 0.25 = pure noise, ~1 = scene structure
    is_static: bool


def _block_means(plane: np.ndarray, n: int) -> np.ndarray:
    """Mean of each n x n block, without copying the frame.

    Rows are summed one strip at a time: a strip sum with dtype= casts through
    numpy's small buffer, where reduceat(dtype=float64) on the whole frame casts
    all of it first (101 MB at 3552x3552), and the reshape trick copies the
    frame whenever the width is not a multiple of n. float64 so an integer frame
    is never rounded (cv2.resize INTER_AREA would round uint8 block means to
    whole ADU, which is coarser than the noise being measured).
    """
    h = (plane.shape[0] // n) * n
    w = (plane.shape[1] // n) * n
    rows = np.stack([plane[top:top + n, :w].sum(axis=0, dtype=np.float64)
                     for top in range(0, h, n)])
    blocks = np.add.reduceat(rows, np.arange(0, w, n), axis=1)
    blocks /= float(n * n)
    if blocks.ndim == 3:
        blocks = blocks.mean(axis=2)
    return blocks


def _remove_smooth_trend(blocks: np.ndarray) -> np.ndarray:
    """Subtract a least-squares quadratic surface.

    Amp glow or a light leak under a closed roof is a smooth gradient, which is
    "structure" to the ratio and would read ~1 on a frame that is otherwise pure
    noise. A quadratic takes that out and leaves anything with edges.
    """
    h, w = blocks.shape
    yy, xx = np.mgrid[0:h, 0:w]
    x = (xx / max(w - 1, 1) - 0.5).ravel()
    y = (yy / max(h - 1, 1) - 0.5).ravel()
    basis = np.stack([x ** i * y ** j
                      for i in range(_TREND_DEGREE + 1)
                      for j in range(_TREND_DEGREE + 1 - i)], axis=1)
    coeffs, *_ = np.linalg.lstsq(basis, blocks.ravel(), rcond=None)
    return blocks - (basis @ coeffs).reshape(h, w)


def _robust_sigma(values: np.ndarray) -> float:
    # MAD, not std: a handful of LEDs or hot blocks under a closed roof must not
    # count as scene structure.
    return 1.4826 * float(np.median(np.abs(values - np.median(values))))


def measure_static(image: np.ndarray) -> Optional[StaticScore]:
    """Score a frame (2D plane or HxWxC, any dtype). None when it is too small
    to measure; never raises on a well-formed array."""
    if image is None or image.ndim not in (2, 3):
        return None

    fine_n = max(_MIN_FINE_BLOCK, min(image.shape[:2]) // _FINE_BLOCKS_ACROSS)
    coarse_n = fine_n * _COARSE_FACTOR
    if (image.shape[0] // coarse_n) * (image.shape[1] // coarse_n) < _MIN_COARSE_BLOCKS:
        return None

    fine = _block_means(image, fine_n)
    level = float(np.abs(fine).max())
    fine = _remove_smooth_trend(fine)
    coarse = _block_means(fine, _COARSE_FACTOR)

    fine_sigma = _robust_sigma(fine)
    if fine_sigma <= 1e-9 * max(level, 1e-12):
        # No variation at all (a saturated or synthetic flat frame): there is no
        # noise to measure, and it is not what this module is looking for.
        return None

    ratio = _robust_sigma(coarse) / fine_sigma
    return StaticScore(ratio=ratio, is_static=ratio < STATIC_RATIO_THRESHOLD)
