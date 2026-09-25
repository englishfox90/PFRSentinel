"""Choosing between cold-start orientation candidates that all passed the gates.

The cold-start bootstrap joint-fits every coarse orientation seed and keeps the
ones that clear the residual / polynomial / scale / anchor gates. Several
usually survive, and they can disagree violently: on the rig in issue #33
eleven candidates passed in one night with `a1` from 968 to 1250 and implied
poles 346-1748 px apart.

Ranking them by raw match count — the original rule — is a coin flip there,
because the greedy matcher returns essentially the same count for *any*
orientation once the tolerance disc is a large enough share of the sky disc
(see `chance_matches`). What does separate them:

1. **Excess over chance** (`n_matches` minus the chance expectation at the
   tolerance the matches were built at). A correct basin matches stars the
   model actually places, so its count is set by sky coverage, not by tolerance
   area, and its excess dwarfs a wrong basin's.
2. **Bright-anchor hits on the most recent frames**, for candidates whose
   excess is too close to call. The anchor gate was the only statistic that
   stayed informative on the reporter's rig, so it settles ties rather than
   leading — it is a coarse integer count over at most 12 anchors and would
   throw away the finer excess signal if it went first.

3. **Rival test** (issue #93, 5e). After the winner is chosen, a second
   survivor that explains the frames with a mostly *different* star set and
   at least half the winner's excess means two incompatible solutions fit
   the same night — the frames cannot tell them apart, and picking one is
   the wrong-basin install this module exists to prevent. The escape then
   yields no candidate at all (`find_rival`).

Pure: numpy/statistics only, no I/O, no Qt.
"""
from typing import List, Optional, Set, Tuple

import numpy as np

from .calibration_validate import count_anchor_hits, tol_scale
from .chance_matches import excess_over_chance

# Candidates within this fraction of the best excess are treated as tied and
# go to the anchor-hit tie-break. Wide on purpose: the chance estimate carries
# several percent of model error and the fits themselves scatter, so a 10-20%
# lead in excess is not real evidence of a better basin.
CLOSE_EXCESS_FRAC = 0.75

# Anchor pool miss tolerance at the reference resolution, matching
# validate_bright_anchors' default; scaled per frame by its sky radius.
ANCHOR_MISS_PX = 40.0

# Frames the anchor tie-break looks at — the same recent slice the anchor gate
# validates on, so a candidate cannot win the tie-break on frames the gate
# never saw.
ANCHOR_FRAMES = 3

# Rival test. A rival shares fewer than this fraction of its matched stars
# with the winner (two fits of one basin share nearly all of theirs — the
# same stars land on the same detections — while a different basin lands
# other catalogue stars on those detections) and carries at least this
# fraction of the winner's excess over chance (the plan's "≥ 0.5×": a
# rival at a tenth of the winner's excess is a weak fit the ranking
# already settled, one at half is a solution the frames support).
RIVAL_MAX_STAR_OVERLAP = 0.5
RIVAL_MIN_EXCESS_FRACTION = 0.5


def chance_excess(model) -> float:
    """Matches beyond the chance expectation `_fit_and_validate` stamped on."""
    return excess_over_chance(model.n_matches,
                              float(getattr(model, 'chance_expected', 0.0)))


def recent_anchor_hits(model, frames: List[dict],
                       n_frames: int = ANCHOR_FRAMES) -> int:
    """Bright-anchor hits summed over the most recent `n_frames` frames."""
    return sum(
        count_anchor_hits(
            model, f.get('above_horizon') or [], f.get('detected') or [],
            max_miss_px=ANCHOR_MISS_PX * tol_scale(f.get('sky_r')),
        )[0]
        for f in (frames or [])[-n_frames:]
    )


def select_bootstrap_winner(passed: List, frames: List[dict],
                            close_frac: float = CLOSE_EXCESS_FRAC
                            ) -> Tuple[Optional[object], str]:
    """(winner, description) — best candidate by excess over chance, ties on
    bright-anchor hits. Returns (None, '') for an empty candidate list."""
    if not passed:
        return None, ''
    best_excess = max(chance_excess(m) for m in passed)
    # A negative best_excess would flip the inequality below (>= a more
    # negative threshold is easier to clear, not harder) and admit every
    # candidate into the shortlist instead of just the close ones. Floor it
    # at 0 — unreachable via the normal path since the chance gate already
    # guarantees excess >= 0, but a caller bypassing that gate should still
    # get a real "close to best" cut, not an inverted one.
    shortlist = [m for m in passed
                 if chance_excess(m) >= close_frac * max(best_excess, 0.0)]
    if shortlist:
        shortlist.sort(key=lambda m: (-recent_anchor_hits(m, frames),
                                      -chance_excess(m)))
    else:
        # The floor makes the cut unsatisfiable when every candidate is below
        # chance: nothing clears 0.0. Same bypassing caller, so rank the whole
        # list by excess and return the best of a bad lot — an anchor tie-break
        # over candidates that are all at or under chance would be noise.
        shortlist = sorted(passed, key=chance_excess, reverse=True)
    best = shortlist[0]
    return best, (f"n_matches={best.n_matches}, "
                  f"chance={float(getattr(best, 'chance_expected', 0.0)):.0f}, "
                  f"excess={chance_excess(best):.0f}, "
                  f"anchors={recent_anchor_hits(best, frames)}, "
                  f"tied={len(shortlist)}")


def matched_star_ids(model) -> Set[str]:
    """Identities of the stars a fit matched (joint_fit_diagnostics): the HR
    number when recorded, else the name; unnamed, unnumbered entries are
    left out rather than collapsed into one."""
    ids = set()
    for rec in getattr(model, 'matched_stars', None) or ():
        key = str(rec.get('hr') or '') or str(rec.get('name') or '')
        if key:
            ids.add(key)
    return ids


def star_overlap(model, other) -> Optional[float]:
    """Fraction of `model`'s matched stars also matched by `other`, or None
    when either recorded no identifiable stars."""
    a, b = matched_star_ids(model), matched_star_ids(other)
    if not a or not b:
        return None
    return len(a & b) / len(a)


def find_rival(passed: List, winner) -> Optional[object]:
    """The strongest survivor that explains the frames with a different star
    set at a comparable excess, or None (see module doc, rule 3)."""
    best_excess = chance_excess(winner)
    rivals = []
    for m in passed:
        if m is winner:
            continue
        overlap = star_overlap(m, winner)
        if overlap is None or overlap >= RIVAL_MAX_STAR_OVERLAP:
            continue
        if chance_excess(m) >= RIVAL_MIN_EXCESS_FRACTION * max(best_excess, 0.0):
            rivals.append(m)
    if not rivals:
        return None
    return max(rivals, key=chance_excess)


def describe_orientation(model) -> str:
    return (f"east_left={model.east_left} axis_alt={model.axis_alt:.1f} "
            f"axis_az={model.axis_az:.1f} roll={np.degrees(model.roll):.1f}° "
            f"a1={model.a1:.0f} n_matches={model.n_matches} "
            f"excess={chance_excess(model):.0f}")
