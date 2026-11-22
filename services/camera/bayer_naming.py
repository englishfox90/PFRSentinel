"""Bayer pattern names: the pipeline's (OpenCV) name vs the physical tile.

The pipeline's ``bayer_pattern`` setting is handed straight to OpenCV as
``cv2.COLOR_Bayer<XY>2RGB`` (``camera_utils.debayer_raw_image``). OpenCV names
a mosaic by the 2nd and 3rd pixels of its *second* row, which is the physical
top-left 2x2 tile rotated 180 degrees: a sensor with red at pixel (0, 0) —
physically RGGB — decodes correctly only through ``COLOR_BayerBG2RGB``, so the
pipeline calls it "BGGR". Measured for all four layouts in
``tests/test_bayer_naming.py``.

FITS readers (Siril, PixInsight, astropy debayers) read ``BAYERPAT`` as the
physical tile from the top-left pixel, so any file leaving the app must carry
the physical name, never the pipeline one.
"""

PATTERNS = ('RGGB', 'BGGR', 'GRBG', 'GBRG')


def _normalise(name) -> str:
    pattern = str(name or '').strip().upper()
    if pattern not in PATTERNS:
        raise ValueError(f"Unknown Bayer pattern {name!r}; expected one of {PATTERNS}")
    return pattern


def physical_bayer_pattern(pipeline_name) -> str:
    """The physical top-left tile (FITS ``BAYERPAT``) for a pipeline name."""
    return _normalise(pipeline_name)[::-1]


def pipeline_pattern_name(physical_name) -> str:
    """The pipeline / OpenCV name for a physical top-left tile."""
    return _normalise(physical_name)[::-1]
