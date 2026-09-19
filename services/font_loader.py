"""Single font resolution path for every overlay renderer.

One `arial.ttf` / Space-Grotesk hunt used to be copy-pasted into six modules.
Off Windows there is no Arial, and a minimal Linux box may not even have
DejaVu, so those hunts silently bottomed out at Pillow's tiny bitmap default.
This module is the one place that knows the candidate order, so a platform
gap gets fixed once.
"""
import os
import sys
import threading

from PIL import ImageFont

from services.logger import app_logger
from services.utils_paths import resource_path

BUNDLED_FONT = 'SpaceGrotesk-Medium.ttf'

# Resolved fonts, keyed by (family, size). A transient truetype failure must
# not make labels change size between frames of a 24/7 capture loop, so the
# outcome — including a fallback — is cached and never re-resolved mid-session.
# Per thread: Pillow does not promise a FreeTypeFont is safe to draw with from
# two threads at once, and the processor worker and the all-sky preview can
# both be rendering.
_THREAD_CACHES = threading.local()


def _cache() -> dict:
    cache = getattr(_THREAD_CACHES, 'fonts', None)
    if cache is None:
        cache = _THREAD_CACHES.fonts = {}
    return cache


class _CacheProxy:
    """`_FONT_CACHE` keeps working as a dict view of the calling thread's cache."""

    def __getitem__(self, key):
        return _cache()[key]

    def __setitem__(self, key, value):
        _cache()[key] = value

    def __contains__(self, key):
        return key in _cache()

    def get(self, key, default=None):
        return _cache().get(key, default)

    def pop(self, key, default=None):
        return _cache().pop(key, default)

    def clear(self):
        _cache().clear()


_FONT_CACHE = _CacheProxy()


def clear_cache():
    """Drop the calling thread's cached font resolutions. Test-only."""
    _cache().clear()


def bundled_font_path(name: str = BUNDLED_FONT):
    """Return the absolute path to a bundled font, or None if it's missing."""
    path = resource_path(f'assets/fonts/{name}')
    return path if os.path.exists(path) else None


def _user_space_grotesk_candidates():
    if sys.platform != 'win32':
        return []
    user_fonts = os.path.join(
        os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Fonts'
    )
    return [
        os.path.join(user_fonts, name)
        for name in (
            'SpaceGrotesk-Medium.ttf',
            'SpaceGrotesk-Regular.ttf',
            'SpaceGrotesk-VariableFont_wght.ttf',
        )
    ]


def _candidates_for(family: str):
    bundled = bundled_font_path()
    if family == 'display':
        candidates = list(_user_space_grotesk_candidates())
        if bundled:
            candidates.append(bundled)
        if sys.platform == 'win32':
            candidates.append('arial.ttf')
        candidates.append('DejaVuSans.ttf')
        return candidates

    # 'text' — Windows users' existing overlays must keep their current look.
    candidates = []
    if sys.platform == 'win32':
        candidates += ['arial.ttf', 'Arial.ttf']
    if bundled:
        candidates.append(bundled)
    candidates.append('DejaVuSans.ttf')
    return candidates


def load_font(size: int, family: str = 'text'):
    """Resolve a font for `family` ('text' or 'display') at `size`.

    Never raises: falls back to Pillow's built-in default, and returns None
    only if even that fails — callers then draw without a font or skip labels,
    so a font problem never stops an unattended capture.
    """
    key = (family, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    font = None
    chosen = None
    for path in _candidates_for(family):
        try:
            font = ImageFont.truetype(path, size)
            chosen = path
            break
        except (OSError, IOError):
            continue

    if font is None:
        try:
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:
                font = ImageFont.load_default()
            chosen = 'PIL default'
        except Exception as e:
            app_logger.warning(f"font_loader: no font could be loaded ({e})")
            chosen = None

    app_logger.debug(f"font_loader: family={family} size={size} -> {chosen}")
    _FONT_CACHE[key] = font
    return font
