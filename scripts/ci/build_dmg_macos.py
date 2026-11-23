"""Wrap PFR Sentinel.app in a drag-to-Applications .dmg with install steps.

    python scripts/ci/build_dmg_macos.py "dist/PFR Sentinel.app" dist/PFRSentinel-X.Y.Z-macos-arm64.dmg

Run by build_sentinel_macos.sh. dmgbuild writes the Finder window layout
(.DS_Store, background, volume icon) directly, so no Finder or AppleScript
is involved and it works the same on a CI runner as on a desk.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dmg_background as layout  # noqa: E402

VOLUME_NAME = "PFR Sentinel"
VOLUME_ICON = layout.REPO / "assets" / "app_icon.icns"


def dmg_settings(app: Path, readme: Path, background: Path) -> dict:
    return {
        "format": "UDZO",
        "files": [(str(app), layout.APP_NAME), (str(readme), layout.README_NAME)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(VOLUME_ICON),
        "background": str(background),
        "window_rect": layout.WINDOW_RECT,
        "default_view": "icon-view",
        "icon_size": float(layout.ICON_SIZE),
        "text_size": float(layout.TEXT_SIZE),
        "icon_locations": dict(layout.ICON_POSITIONS),
        "hide_extensions": [layout.APP_NAME, layout.README_NAME],
        "show_status_bar": False,
        "show_tab_view": False,
        "show_toolbar": False,
        "show_pathbar": False,
        "show_sidebar": False,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: build_dmg_macos.py <app> <dmg>", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("FAIL: disk images can only be built on macOS", file=sys.stderr)
        return 1
    import dmgbuild

    app, dmg = Path(argv[1]), Path(argv[2])
    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        readme = work_dir / layout.README_NAME
        readme.write_text(layout.README_TEXT, encoding="utf-8")
        background = layout.write_backgrounds(work_dir)
        dmgbuild.build_dmg(str(dmg), VOLUME_NAME,
                           settings=dmg_settings(app, readme, background))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
