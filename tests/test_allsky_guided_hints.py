"""
Tests for services/allsky/guided_hints.py — star suggestions during a guided
calibration.

Synthetic sky: a known FisheyeModel projects the bright catalogue to pixels,
and those pixels double as the "detections", so every expectation is exact.
"""
from datetime import datetime, timezone

import numpy as np
import pytest

pytest.importorskip('scipy')

from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.fisheye import FisheyeModel
from services.allsky.guided_hints import (
    MAX_HINTS, MIN_HINT_ANCHORS, suggest_stars)

LAT, LON = 31.33, -100.46
DT = datetime(2026, 6, 22, 6, 0, tzinfo=timezone.utc)
SKY = (1137.0, 1306.0, 968.0)


def _true_model():
    return FisheyeModel(
        cx=1137.0, cy=1306.0, a1=643.0, a3=1.3, a5=-7.7,
        roll=-0.321, axis_alt=82.5, axis_az=16.9, east_left=True)


@pytest.fixture(scope='module')
def sky():
    """(candidates, detections, truth) for the synthetic frame."""
    model = _true_model()
    candidates, truth = [], {}
    for i, s in enumerate(get_bright_stars(max_mag=3.5)):
        alt, az = radec_to_altaz(s['ra_deg'], s['dec_deg'], LAT, LON, DT)
        if float(alt) <= 15.0:
            continue
        xy = model.altaz_to_pixel(float(alt), float(az))
        if xy is None:
            continue
        name = s.get('name') or f"star-{i}"
        if name in truth:
            continue
        candidates.append({
            'name': name, 'ra_deg': s['ra_deg'], 'dec_deg': s['dec_deg'],
            'alt': float(alt), 'az': float(az),
            'vmag': float(s.get('vmag', 0.0))})
        truth[name] = (float(xy[0]), float(xy[1]))
    candidates.sort(key=lambda c: c['vmag'])
    detections = [(x, y, 1000.0 - i) for i, (x, y) in
                  enumerate(truth[c['name']] for c in candidates)]
    return candidates, detections, truth


def _anchors(sky, n, spread=True):
    """n bright, high anchors spread in azimuth: (px, py, ra, dec, name)."""
    candidates, _det, truth = sky
    pool = sorted((c for c in candidates if c['alt'] > 30 and c['vmag'] < 2.6),
                  key=lambda c: c['az'])
    idx = np.linspace(0, len(pool) - 1, n).round().astype(int)
    picked = [pool[i] for i in dict.fromkeys(idx.tolist())]
    return [(*truth[c['name']], c['ra_deg'], c['dec_deg'], c['name'])
            for c in picked]


def test_too_few_anchors_gives_no_suggestions(sky):
    candidates, det, _truth = sky
    anchors = _anchors(sky, MIN_HINT_ANCHORS)[:MIN_HINT_ANCHORS - 1]
    assert suggest_stars(anchors, candidates, det, LAT, LON, DT, *SKY) is None


def test_three_correct_anchors_place_the_other_bright_stars(sky):
    candidates, det, truth = sky
    anchors = _anchors(sky, 3)
    result = suggest_stars(anchors, candidates, det, LAT, LON, DT, *SKY)

    assert result is not None and result.trusted
    assert result.hints, "a trusted result with no hints helps nobody"
    errors = [np.hypot(h.x - truth[h.name][0], h.y - truth[h.name][1])
              for h in result.hints]
    # The point of a hint is that a click on it snaps to the right star.
    snap_radius = 0.035 * SKY[2]
    assert np.median(errors) < snap_radius, f"median miss {np.median(errors):.0f}px"


def test_identified_stars_are_not_suggested_again(sky):
    candidates, det, _truth = sky
    anchors = _anchors(sky, 4)
    result = suggest_stars(anchors, candidates, det, LAT, LON, DT, *SKY)
    named = {a[4] for a in anchors}
    assert named.isdisjoint(h.name for h in result.hints)


def test_suggestions_are_capped_to_the_brightest(sky):
    candidates, det, _truth = sky
    result = suggest_stars(_anchors(sky, 5), candidates, det, LAT, LON, DT, *SKY)
    assert len(result.hints) <= MAX_HINTS
    mags = [h.vmag for h in result.hints]
    assert mags == sorted(mags)


def test_misidentified_anchor_withholds_suggestions_with_a_reason(sky):
    """A wrong name must not produce confident labels on empty sky."""
    candidates, det, truth = sky
    anchors = _anchors(sky, 4)
    taken = {a[4] for a in anchors}
    # Name the first click as a bright star from a different part of the sky.
    x, y = anchors[0][0], anchors[0][1]
    far = max((c for c in candidates[:40] if c['name'] not in taken),
              key=lambda c: np.hypot(truth[c['name']][0] - x,
                                     truth[c['name']][1] - y))
    wrong = [(x, y, far['ra_deg'], far['dec_deg'], far['name'])] + anchors[1:]

    result = suggest_stars(wrong, candidates, det, LAT, LON, DT, *SKY)
    assert result is not None
    assert not result.trusted
    assert 'mis-identified' in result.message


def test_wrong_pose_that_fits_its_anchors_is_caught_by_support(sky):
    """Anchors rotated together about the centre agree with each other —
    the RMS check passes — but the sky they predict isn't in the frame."""
    candidates, det, _truth = sky
    cx, cy, _r = SKY
    a = np.radians(150.0)
    rotated = []
    for x, y, ra, dec, name in _anchors(sky, 3):
        dx, dy = x - cx, y - cy
        rotated.append((cx + dx * np.cos(a) - dy * np.sin(a),
                        cy + dx * np.sin(a) + dy * np.cos(a), ra, dec, name))
    result = suggest_stars(rotated, candidates, det, LAT, LON, DT, *SKY)
    assert result is not None
    assert not result.trusted


def test_frame_with_almost_no_detections_does_not_accuse_the_anchors(sky):
    """Hazy frame, detection finds nothing: correct anchors, no way to check
    them against the sky. Suggestions still help; a warning would be false."""
    candidates, _det, _truth = sky
    for detections in ([], [(10.0, 10.0, 1.0)] * 3):
        result = suggest_stars(_anchors(sky, 4), candidates, detections,
                               LAT, LON, DT, *SKY)
        assert result.support is None
        assert result.trusted and result.hints
        assert result.message == ""


def test_anchor_below_horizon_gives_no_suggestions(sky):
    candidates, det, _truth = sky
    anchors = _anchors(sky, 3)
    # Dec -89 never rises from latitude +31.
    anchors[0] = (anchors[0][0], anchors[0][1], 10.0, -89.0, 'nowhere')
    assert suggest_stars(anchors, candidates, det, LAT, LON, DT, *SKY) is None
