"""
One FITS writer for every FITS file the app produces.

astropy is excluded from the PyInstaller bundle to save ~30 MB
(``PFRSentinel.spec``), yet ``from astropy.io import fits`` can still succeed
inside the frozen app: a hollow ``astropy.io.fits`` package gets pulled in by
another hook and imports cleanly with none of its classes on it. Testing the
import therefore proves nothing — the dev-mode export logged
``module 'astropy.io.fits' has no attribute 'PrimaryHDU'`` on every frame of
the 2026-09-29 dev build. ``astropy_fits()`` tests for the class instead,
and ``write_fits`` falls back to writing the file itself: a single primary
HDU, 2-D or 3-D, integer or float, with the caller's header cards.

Header cards: ``{keyword: value}`` or ``{keyword: (value, comment)}``.
``COMMENT`` / ``HISTORY`` are commentary cards. Structural keywords the
writer owns (SIMPLE, BITPIX, NAXIS*, BZERO, BSCALE, EXTEND, END) are skipped
from the caller's dict.
"""
import numpy as np

from services.logger import app_logger

_CARD_LEN = 80
_BLOCK = 2880
_CARDS_PER_BLOCK = _BLOCK // _CARD_LEN
_OWNED_KEYWORDS = frozenset(
    {'SIMPLE', 'BITPIX', 'NAXIS', 'NAXIS1', 'NAXIS2', 'NAXIS3', 'BZERO', 'BSCALE',
     'EXTEND', 'END'})
_COMMENTARY = frozenset({'COMMENT', 'HISTORY'})

# dtype -> (BITPIX, on-disk big-endian dtype, BZERO). Unsigned integers are
# stored as the signed type of the same width with a BZERO offset, as the
# standard prescribes and astropy does.
_ENCODINGS = {
    np.dtype(np.uint8): (8, '>u1', None),
    np.dtype(np.int16): (16, '>i2', None),
    np.dtype(np.uint16): (16, '>i2', 32768),
    np.dtype(np.int32): (32, '>i4', None),
    np.dtype(np.uint32): (32, '>i4', 2147483648),
    np.dtype(np.float32): (-32, '>f4', None),
    np.dtype(np.float64): (-64, '>f8', None),
}


def astropy_fits():
    """The ``astropy.io.fits`` module when it is genuinely usable, else None."""
    try:
        from astropy.io import fits
        fits.PrimaryHDU  # noqa: B018 — hollow bundle module lacks it
        return fits
    except Exception:
        return None


def write_fits(path, data, header=None) -> None:
    """Write ``data`` as the primary HDU of ``path`` with ``header`` cards.

    Uses astropy when it is available and complete; otherwise the built-in
    writer below produces the same file for the array types the app writes.
    """
    header = header or {}
    fits = astropy_fits()
    if fits is not None:
        hdu = fits.PrimaryHDU(np.asarray(data))
        for key, value in header.items():
            hdu.header[key] = value
        hdu.writeto(path, overwrite=True)
        return
    _write_builtin(path, np.asarray(data), header)


def _write_builtin(path, data, header) -> None:
    if data.ndim not in (2, 3):
        raise ValueError(f"FITS image data must be 2-D or 3-D, got {data.ndim}-D")
    encoding = _ENCODINGS.get(data.dtype)
    if encoding is None:
        app_logger.debug(f"fits_writer: {data.dtype} stored as float32")
        data = data.astype(np.float32)
        encoding = _ENCODINGS[data.dtype]
    bitpix, disk_dtype, bzero = encoding

    cards = [
        _card('SIMPLE', True, 'conforms to FITS standard'),
        _card('BITPIX', bitpix, 'array data type'),
        _card('NAXIS', data.ndim, 'number of array dimensions'),
    ]
    for i, n in enumerate(reversed(data.shape), start=1):
        cards.append(_card(f'NAXIS{i}', int(n)))
    if bzero is not None:
        cards.append(_card('BZERO', bzero, 'offset data range to that of unsigned integer'))
        cards.append(_card('BSCALE', 1, 'default scaling factor'))
    for key, value in header.items():
        keyword = str(key).upper().strip()[:8]
        if keyword in _OWNED_KEYWORDS or not keyword:
            continue
        if keyword in _COMMENTARY:
            cards.append(_commentary_card(keyword, value))
            continue
        comment = ''
        if isinstance(value, tuple) and len(value) == 2:
            value, comment = value
        cards.append(_card(keyword, value, str(comment or '')))
    cards.append(b'END'.ljust(_CARD_LEN))
    while len(cards) % _CARDS_PER_BLOCK:
        cards.append(b' ' * _CARD_LEN)

    if bzero is not None:
        payload = (data.astype(np.int64) - bzero).astype(disk_dtype).tobytes()
    else:
        payload = data.astype(disk_dtype).tobytes()
    payload += b'\x00' * ((-len(payload)) % _BLOCK)

    with open(path, 'wb') as f:
        f.write(b''.join(cards))
        f.write(payload)


def _card(keyword: str, value, comment: str = '') -> bytes:
    """One 80-byte header card in the fixed format (value right-justified to
    column 30 for numbers and logicals, quoted strings from column 11)."""
    if isinstance(value, (bool, np.bool_)):
        field = f"= {'T' if value else 'F':>20}"
    elif isinstance(value, (int, np.integer)):
        field = f"= {int(value):>20d}"
    elif isinstance(value, (float, np.floating)):
        field = f"= {_format_float(float(value)):>20}"
    else:
        text = str(value).replace("'", "''")[:68]
        field = f"= '{text.ljust(8)}'"
    card = f"{keyword.ljust(8)}{field}"
    if comment:
        room = _CARD_LEN - len(card) - 3
        if room > 0:
            card += f" / {comment[:room]}"
    return card.ljust(_CARD_LEN)[:_CARD_LEN].encode('ascii', errors='replace')


def _commentary_card(keyword: str, value) -> bytes:
    text = str(value[0] if isinstance(value, tuple) else value)[:72]
    return f"{keyword.ljust(8)}{text}".ljust(_CARD_LEN).encode('ascii', errors='replace')


def _format_float(value: float) -> str:
    """Shortest exact representation that still reads as a float (the
    standard requires a decimal point or exponent)."""
    if value != value or value in (float('inf'), float('-inf')):
        raise ValueError("FITS header cards cannot hold NaN or infinity")
    text = repr(value).upper()
    if '.' not in text and 'E' not in text:
        text += '.0'
    return text
