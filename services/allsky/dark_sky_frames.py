"""
Which buffer frames are dark enough to judge a calibration model by.

The observing-window gate opens as soon as a frame looks like an observable
sky (sun below civil twilight, exposure over the floor, stars detected), and
the calibration buffer fills from that moment. Through nautical twilight
those frames still detect the 200-star cap per frame — sky gradient, noise
and equipment edges — but only a handful of the detections are stars, so the
chance expectation (driven by the detection count) swamps the real matches
and every model, right or wrong, scores at chance.

Measured on the reporter's 2026-09-30 dusk buffer (discussion #105, 48 frames
from the moment the gate opened, ASI676MC at 36.6 N), per-frame chance ratio
of two models — the saved joint fit, and the freshly guided model that went
on to score 6-8x chance on the same night's dark frames:

    sun altitude    saved fit    fresh guided fit
    -8 to -11 deg   0.8 - 1.4    0.5 - 1.4
    -11 to -13      1.4 - 2.1    1.2 - 1.7
    -13 to -14      1.9 - 2.4    1.6 - 2.2
    -14 to -15      2.3 - 3.0    2.2 - 3.9
    -15.2           3.7          4.8

Scored over the whole buffer the good model read 1.42x and the saved one
1.56x; two such runs at dusk discredited the saved model, withheld the
overlay and started a seedless escape, and the reporter read the vanished
labels as a lost calibration. Only frames taken with the sun at or below
SCORE_MAX_SUN_ALT_DEG are scored, and a run with fewer than DARK_MIN_FRAMES of
them is not scored at all — which is not a strike (incumbent_chance).

Fails open: without a site or without astral, every frame is returned, which
is what scoring did before this module.

Pure apart from the astral call: no Qt, no I/O.
"""
from typing import List, Optional

from services.logger import app_logger as log

# The table above: by -15 deg the good model is clear of the 2x margin on a
# single frame, and below it the ratio keeps climbing into the 6-8x the night
# delivered. Astronomical darkness (-18 deg) would also do, but never arrives
# in a mid-latitude summer, where this would then never score at all.
SCORE_MAX_SUN_ALT_DEG = -15.0

# The cooldown after a buffer reset scores on ~10 frames; on the 2026-09-30
# night 10 dark frames already read 6.7x. Five keeps a run that straddles the
# end of twilight from being judged on two or three frames.
DARK_MIN_FRAMES = 5


def sun_altitude_deg(dt, lat: float, lon: float) -> Optional[float]:
    """Sun altitude (degrees, no refraction) at aware datetime `dt`, or None."""
    if dt is None:      # astral reads None as "now"
        return None
    try:
        from astral import Observer
        from astral.sun import elevation
        return float(elevation(Observer(latitude=float(lat), longitude=float(lon)),
                               dateandtime=dt, with_refraction=False))
    except Exception as e:
        log.debug(f"Sun altitude unavailable for frame scoring: {e}")
        return None


def dark_sky_frames(frames: List[dict], lat: Optional[float],
                    lon: Optional[float],
                    max_sun_alt: float = SCORE_MAX_SUN_ALT_DEG,
                    min_frames: int = DARK_MIN_FRAMES) -> List[dict]:
    """The frames of `frames` taken with the sun at or below `max_sun_alt`.

    [] when fewer than `min_frames` qualify — the caller scores nothing.
    Every frame, unchanged, when the site is unknown or a sun altitude can't
    be computed (fail open).
    """
    if not frames or lat is None or lon is None:
        return frames if frames else []
    dark = []
    for frame in frames:
        alt = sun_altitude_deg(frame.get('dt'), lat, lon)
        if alt is None:
            return frames
        if alt <= max_sun_alt:
            dark.append(frame)
    if len(dark) < min_frames:
        log.debug(f"Incumbent not scored: {len(dark)} of {len(frames)} frame(s) "
                  f"taken with the sun at or below {max_sun_alt:.0f} deg "
                  f"(need {min_frames}) — twilight frames score any model at chance")
        return []
    return frames if len(dark) == len(frames) else dark
