"""Window layout and background artwork for the macOS .dmg.

The disk image opens as a Finder window that shows the install steps: drag
the app onto Applications, then run the one-time quarantine command (the app
is not notarized, see docs/wiki/macOS.md). Everything here is pure Pillow so
the artwork can be rendered and checked on any OS; build_dmg_macos.py hands
the layout to dmgbuild on a Mac.

    python scripts/ci/dmg_background.py <out-dir>    # writes both PNGs to look at

Coordinates are Finder points. The background is rendered at 1x and 2x;
dmgbuild combines `name.png` and `name@2x.png` into one HiDPI TIFF.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]

APP_NAME = "PFR Sentinel.app"
README_NAME = "Read me first.txt"
QUARANTINE_COMMAND = f'xattr -dr com.apple.quarantine "/Applications/{APP_NAME}"'

WINDOW_SIZE = (660, 480)
# Whether Finder's saved window bounds include the ~28 pt title bar varies
# with the macOS release, so the window asks for the content height plus a
# title bar and the art runs on past it: no empty strip at the bottom either way.
TITLE_BAR = 28
WINDOW_RECT = ((200, 120), (WINDOW_SIZE[0], WINDOW_SIZE[1] + TITLE_BAR))
BACKGROUND_SIZE = (WINDOW_SIZE[0], WINDOW_SIZE[1] + 2 * TITLE_BAR)
ICON_SIZE = 88
TEXT_SIZE = 13
ICON_POSITIONS = {
    APP_NAME: (165, 150),
    "Applications": (495, 150),
    README_NAME: (562, 318),
}

# Finder draws icon labels in black in Light mode and white in Dark mode,
# whatever the background. A mid-grey plate under each label keeps both
# readable, which no single dark or light background colour can.
LABEL_PLATE = "#8B8B85"
LABEL_PLATE_SIZE = (124, 22)
LABEL_OFFSET_Y = ICON_SIZE // 2 + 14

BG_TOP = "#191926"
BG_BOTTOM = "#111110"
PANEL = "#222221"
PANEL_BORDER = "#303374"
CODE_BG = "#111110"
TEXT = "#EEEEEC"
TEXT_MUTED = "#A1A09A"
ACCENT = "#5B5BD6"
ACCENT_TEXT = "#B1A9FF"

PANEL_BOX = (24, 250, 636, 460)
CODE_BOX = (44, 406, 616, 442)

README_TEXT = f"""PFR Sentinel for macOS (Apple silicon)

1. Drag "PFR Sentinel" onto the "Applications" folder in this window.

2. The first time only: PFR Sentinel is open source and isn't notarized
   by Apple yet, so macOS will say it "cannot be opened" or "is damaged".
   It isn't damaged. Open Terminal (Applications > Utilities > Terminal),
   paste this line and press Return:

   {QUARANTINE_COMMAND}

   Then open PFR Sentinel from Applications as usual.

The command removes the "downloaded from the internet" flag from this one
app. It doesn't turn off Gatekeeper or change any other security setting.
Run it again after installing an update.

More help: https://github.com/englishfox90/PFRSentinel/wiki/macOS
"""

_TEXT_FONT = REPO / "assets" / "fonts" / "SpaceGrotesk-Medium.ttf"
_MONO_FONTS = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "DejaVuSansMono.ttf",
)


def _font(size: float, mono: bool = False) -> ImageFont.FreeTypeFont:
    candidates = _MONO_FONTS if mono else (str(_TEXT_FONT),)
    for path in candidates:
        try:
            return ImageFont.truetype(path, round(size))
        except OSError:
            continue
    return ImageFont.load_default(round(size))


def _fit_mono(text: str, max_width: float, size: float) -> ImageFont.FreeTypeFont:
    while size > 8:
        font = _font(size, mono=True)
        if font.getlength(text) <= max_width:
            return font
        size -= 0.5
    return _font(size, mono=True)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and font.getlength(candidate) > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _hex(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _gradient(size: tuple[int, int]) -> Image.Image:
    top, bottom = _hex(BG_TOP), _hex(BG_BOTTOM)
    width, height = size
    column = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return column.resize(size)


def _arrow(draw: ImageDraw.ImageDraw, s: float) -> None:
    (ax, ay), (bx, _) = ICON_POSITIONS[APP_NAME], ICON_POSITIONS["Applications"]
    start = ax + ICON_SIZE / 2 + 22
    tip = bx - ICON_SIZE / 2 - 22
    head = 26
    shaft = 10
    draw.rounded_rectangle(
        ((start) * s, (ay - shaft / 2) * s, (tip - head + 4) * s, (ay + shaft / 2) * s),
        radius=shaft / 2 * s, fill=ACCENT,
    )
    draw.polygon(
        [((tip - head) * s, (ay - head * 0.8) * s), (tip * s, ay * s),
         ((tip - head) * s, (ay + head * 0.8) * s)],
        fill=ACCENT,
    )
    label = "drag"
    font = _font(14 * s)
    draw.text(((start + tip) / 2 * s, (ay - 22) * s), label, font=font,
              fill=ACCENT_TEXT, anchor="ms")


def _step_badge(draw: ImageDraw.ImageDraw, s: float, x: float, y: float, n: int) -> None:
    r = 12
    draw.ellipse(((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s), fill=ACCENT)
    draw.text((x * s, y * s), str(n), font=_font(14 * s), fill=TEXT, anchor="mm")


def _label_plates(draw: ImageDraw.ImageDraw, s: float) -> None:
    w, h = LABEL_PLATE_SIZE
    for x, y in ICON_POSITIONS.values():
        cy = y + LABEL_OFFSET_Y
        draw.rounded_rectangle(
            ((x - w / 2) * s, (cy - h / 2) * s, (x + w / 2) * s, (cy + h / 2) * s),
            radius=h / 2 * s, fill=LABEL_PLATE,
        )


def render(scale: int = 1) -> Image.Image:
    s = scale
    width, height = BACKGROUND_SIZE
    image = _gradient((width * s, height * s))
    draw = ImageDraw.Draw(image)

    _step_badge(draw, s, 40, 36, 1)
    draw.text((60 * s, 36 * s), "Drag PFR Sentinel onto Applications to install it",
              font=_font(19 * s), fill=TEXT, anchor="lm")
    _arrow(draw, s)

    x0, y0, x1, y1 = PANEL_BOX
    draw.rounded_rectangle((x0 * s, y0 * s, x1 * s, y1 * s), radius=14 * s,
                           fill=PANEL, outline=PANEL_BORDER, width=max(1, s))
    _step_badge(draw, s, 56, 280, 2)
    draw.text((76 * s, 280 * s), "First launch only", font=_font(17 * s),
              fill=ACCENT_TEXT, anchor="lm")

    body = ("PFR Sentinel isn't notarized by Apple yet, so macOS will say it "
            "\u201ccannot be opened\u201d or \u201cis damaged\u201d. It isn't. Open "
            "Terminal, paste this line and press Return, then open the app as usual. "
            "\u201cRead me first\u201d has it to copy.")
    body_font = _font(13.5 * s)
    line_h = 19
    for i, line in enumerate(_wrap(body, body_font, 430 * s)):
        draw.text((44 * s, (304 + i * line_h) * s), line, font=body_font,
                  fill=TEXT_MUTED, anchor="la")

    cx0, cy0, cx1, cy1 = CODE_BOX
    draw.rounded_rectangle((cx0 * s, cy0 * s, cx1 * s, cy1 * s), radius=8 * s,
                           fill=CODE_BG, outline=PANEL_BORDER, width=max(1, s))
    mono = _fit_mono(QUARANTINE_COMMAND, (cx1 - cx0 - 28) * s, 13 * s)
    draw.text(((cx0 + 14) * s, (cy0 + cy1) / 2 * s), QUARANTINE_COMMAND,
              font=mono, fill=TEXT, anchor="lm")

    _label_plates(draw, s)
    return image


def write_backgrounds(out_dir: Path, stem: str = "background") -> Path:
    """Write `<stem>.png` and `<stem>@2x.png`; return the 1x path dmgbuild takes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    one_x = out_dir / f"{stem}.png"
    render(1).save(one_x, dpi=(72, 72))
    render(2).save(out_dir / f"{stem}@2x.png", dpi=(144, 144))
    return one_x


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: dmg_background.py <out-dir>")
    print(write_backgrounds(Path(sys.argv[1])))
