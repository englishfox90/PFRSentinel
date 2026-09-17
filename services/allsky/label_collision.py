"""
Label collision avoidance for all-sky overlay rendering.

Labels are tried in candidate slots around each marker (right, left, below,
above, then the four diagonals). Every slot is anchored on the *edge* of the
text box nearest the marker, so the text never overlaps the star it names.
Higher-priority labels (bright stars, Messier) are placed first; lower-priority
labels are dropped if they overlap an already-placed label or a reserved marker.

The placed set is small (bounded by top_n), so exact rectangle tests are used
rather than a cell grid — a coarse grid rounded a marker's own cell into its
label's slot and rejected labels that were actually clear.
"""
from typing import Dict, List, Optional, Tuple

_MIN_GAP_PX = 4.0

# Gap between marker and text-box edge, as a fraction of the label height.
# Scales with the font so the spacing looks the same at 750 px and 1920 px.
GAP_RATIO = 0.6


def default_gap(label_h: float) -> float:
    """Marker-to-text gap for a label of the given pixel height."""
    return max(_MIN_GAP_PX, label_h * GAP_RATIO)


def candidate_slots(
    marker_x: float, marker_y: float,
    label_w: float, label_h: float,
    gap: float,
) -> List[Tuple[float, float]]:
    """Top-left corners for each candidate text box, nearest-edge `gap` px away."""
    d = gap * 0.7  # diagonal slots: same straight-line distance from the marker
    return [
        (marker_x + gap,           marker_y - label_h / 2.0),   # right
        (marker_x - gap - label_w, marker_y - label_h / 2.0),   # left
        (marker_x - label_w / 2.0, marker_y + gap),             # below
        (marker_x - label_w / 2.0, marker_y - gap - label_h),   # above
        (marker_x + d,             marker_y + d),               # below-right
        (marker_x + d,             marker_y - d - label_h),     # above-right
        (marker_x - d - label_w,   marker_y + d),               # below-left
        (marker_x - d - label_w,   marker_y - d - label_h),     # above-left
    ]


class LabelGrid:
    """
    Occupancy tracker for label placement.

    Records placed label boxes and reserved marker points. `cell_size` is kept
    for API compatibility and now sets the minimum clearance between labels.

    `slot_memory` maps a label key to the candidate-slot index it last landed
    in. It outlives the grid (which is rebuilt every frame) so a label keeps
    its side of the star from one frame to the next instead of flipping the
    moment a neighbour appears.
    """

    def __init__(self, img_width: int, img_height: int, cell_size: int = 12,
                 slot_memory: Optional[Dict[str, int]] = None):
        self._w = img_width
        self._h = img_height
        self._pad = cell_size / 2.0
        self._rects: List[Tuple[float, float, float, float]] = []
        self._markers: List[Tuple[float, float, float]] = []
        self._slot_memory = slot_memory

    def is_free(self, x: float, y: float, w: float, h: float) -> bool:
        """True if the box does not touch any placed label or reserved marker."""
        x0, y0, x1, y1 = x - self._pad, y - self._pad, x + w + self._pad, y + h + self._pad
        for rx0, ry0, rx1, ry1 in self._rects:
            if x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0:
                return False
        for mx, my, r in self._markers:
            # Distance from the marker to the nearest point of the unpadded box
            nx = min(max(mx, x), x + w)
            ny = min(max(my, y), y + h)
            if (nx - mx) ** 2 + (ny - my) ** 2 < r * r:
                return False
        return True

    def occupy(self, x: float, y: float, w: float, h: float) -> None:
        """Record a label box as placed."""
        self._rects.append((x, y, x + w, y + h))

    def reserve_marker(self, x: float, y: float, radius: float) -> None:
        """Keep labels off a marker (a star, planet, or object position)."""
        self._markers.append((x, y, radius))

    def try_place(
        self,
        marker_x: float,
        marker_y: float,
        label_w: float,
        label_h: float,
        gap: Optional[float] = None,
        key: Optional[str] = None,
    ) -> Optional[Tuple[float, float]]:
        """
        Try to place a label near (marker_x, marker_y).

        Tests candidate slots in order; returns (label_x, label_y) — the text
        box's top-left — for the first collision-free slot inside the image, or
        None if every slot is taken. The chosen box is recorded as occupied.

        With `key` and a slot memory, the slot this key used last time is
        tried before the default order and the winning slot is remembered.
        """
        if gap is None:
            gap = default_gap(label_h)

        slots = candidate_slots(marker_x, marker_y, label_w, label_h, gap)
        order = list(range(len(slots)))
        remembered = None
        if key is not None and self._slot_memory is not None:
            remembered = self._slot_memory.get(key)
            if remembered is not None and 0 <= remembered < len(slots):
                order.remove(remembered)
                order.insert(0, remembered)

        for i in order:
            lx, ly = slots[i]
            if lx < 0 or ly < 0 or lx + label_w > self._w or ly + label_h > self._h:
                continue
            if self.is_free(lx, ly, label_w, label_h):
                self.occupy(lx, ly, label_w, label_h)
                if key is not None and self._slot_memory is not None:
                    self._slot_memory[key] = i
                return lx, ly

        return None


def estimate_text_size(text: str, font_size: int) -> Tuple[float, float]:
    """
    Rough estimate of rendered text bounding box in pixels.
    Assumes ~0.6× monospace ratio.
    """
    w = len(text) * font_size * 0.6
    h = float(font_size) * 1.2
    return w, h
