"""How the status strip fits a narrow window.

Width arithmetic kept apart from the widgets so it can be tested
without a display. When every tile fits at its natural width nothing changes;
otherwise low-priority tiles fold to their icon, in rank order, and only if
that is still not enough do the remaining values elide, at a word boundary.
"""
from dataclasses import dataclass

from PySide6.QtCore import Qt

ELLIPSIS = "…"
_TRAILING_PUNCTUATION = " ·,:;-–—"

FULL = 'full'
COMPACT = 'compact'
ELIDED = 'elided'


def _word_heads(text: str):
    """Word-boundary elisions of ``text``, longest first."""
    words = text.split(' ')
    for n in range(len(words) - 1, 0, -1):
        head = ' '.join(words[:n]).rstrip(_TRAILING_PUNCTUATION)
        if head:
            yield head + ELLIPSIS


def word_steps(text: str, measure, pad: int = 0) -> tuple:
    """Widths ``text`` can elide to at a word boundary, widest first, each
    narrower than the whole text."""
    full = measure(text)
    widths = sorted({measure(h) + pad for h in _word_heads(text)}, reverse=True)
    return tuple(w for w in widths if w < full + pad)


def elide_at_word(text: str, width: int, measure, fallback=None) -> str:
    """``text`` cut back to whole words plus an ellipsis so it measures at
    most ``width``. A dangling separator ("Paused ·") is dropped with the word
    after it. When not even the first word fits, ``fallback(text, width)``
    decides (a character elision), else the first word is returned elided."""
    if measure(text) <= width:
        return text
    for head in _word_heads(text):
        if measure(head) <= width:
            return head
    if fallback is not None:
        return fallback(text, width)
    return text.split(' ')[0] + ELLIPSIS


@dataclass
class FitItem:
    """One tile's widths. ``chrome_w`` is everything but the text column, which
    is as wide as the wider of the eyebrow (``label_w``) and the value.
    ``steps`` are the widths the value can elide to at a word boundary
    (``word_steps``), widest first, so a value is never cut inside a word."""
    key: str
    chrome_w: int
    label_w: int
    value_w: int
    compact_w: int = 0
    collapse_rank: int = None   # None: never folds to its icon
    elide_rank: int = None      # None: never elides; equal ranks share the cut
    steps: tuple = ()

    def width(self, mode: str = FULL, value_w: int = None) -> int:
        if mode == COMPACT:
            return self.compact_w
        shown = self.value_w if value_w is None else value_w
        return self.chrome_w + max(self.label_w, shown)


def plan_fit(items, available: int) -> dict:
    """``{key: (mode, value_width)}`` for the strip at ``available`` px.
    ``value_width`` is only set for an elided value."""
    plan = {item.key: (FULL, None) for item in items}

    def total():
        return sum(item.width(*plan[item.key]) for item in items)

    if total() <= available:
        return plan
    for item in sorted((i for i in items if i.collapse_rank is not None),
                       key=lambda i: i.collapse_rank):
        plan[item.key] = (COMPACT, None)
        if total() <= available:
            return plan
    # Items that share an elide rank take turns giving up a word, the one
    # with the most words left first (then the widest), so no one value is
    # cut to a stub while another is whole.
    ranks = sorted({i.elide_rank for i in items if i.elide_rank is not None})
    step = {i.key: 0 for i in items}
    for rank in ranks:
        tier = [i for i in items if i.elide_rank == rank and plan[i.key][0] == FULL]
        while total() > available:
            # A step that leaves the value under its eyebrow saves nothing.
            open_items = [i for i in tier if step[i.key] < len(i.steps)
                          and i.width(ELIDED, i.steps[step[i.key]]) < i.width(*plan[i.key])]
            if not open_items:
                break
            item = max(open_items, key=lambda i: (len(i.steps) - step[i.key],
                                                  i.width(*plan[i.key])))
            plan[item.key] = (ELIDED, item.steps[step[item.key]])
            step[item.key] += 1
        if total() <= available:
            break
    return plan


# --- Measuring a polished QLabel (its style sheet sets the font) ---------
def elide_label_text(label, text: str, width: int) -> str:
    label.ensurePolished()
    fm = label.fontMetrics()
    return elide_at_word(text, width, fm.horizontalAdvance,
                         lambda t, w: fm.elidedText(t, Qt.ElideRight, w))


def label_text_width(label, text: str) -> int:
    label.ensurePolished()
    return label.fontMetrics().horizontalAdvance(text) + 2


def label_elide_steps(label, text: str) -> tuple:
    label.ensurePolished()
    return word_steps(text, label.fontMetrics().horizontalAdvance, pad=2)


def chrome_width(layout, icon_w: int) -> int:
    m = layout.contentsMargins()
    return m.left() + m.right() + icon_w + layout.spacing()
