"""scripts/ci/dmg_background.py + build_dmg_macos.py — the .dmg window that
shows the install steps. dmgbuild itself needs a Mac and runs in the macOS
build job; the layout, artwork and settings are checked here on every OS."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "ci"))
import build_dmg_macos  # noqa: E402
import dmg_background as layout  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _plate_box(name):
    x, y = layout.ICON_POSITIONS[name]
    w, h = layout.LABEL_PLATE_SIZE
    cy = y + layout.LABEL_OFFSET_Y
    return (x - w / 2, cy - h / 2, x + w / 2, cy + h / 2)


def _icon_box(name):
    x, y = layout.ICON_POSITIONS[name]
    half = layout.ICON_SIZE / 2
    return (x - half, y - half, x + half, y + half)


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_backgrounds_render_at_1x_and_2x(tmp_path):
    one_x = layout.write_backgrounds(tmp_path)
    from PIL import Image

    with Image.open(one_x) as small, Image.open(tmp_path / "background@2x.png") as big:
        assert small.size == layout.BACKGROUND_SIZE
        assert big.size == (layout.BACKGROUND_SIZE[0] * 2, layout.BACKGROUND_SIZE[1] * 2)
    # dmgbuild pairs `name.png` with `name@2x.png` by this exact naming.
    assert one_x.name == "background.png"


def test_background_covers_the_window_with_or_without_the_title_bar():
    (_, _), (w, h) = layout.WINDOW_RECT
    assert layout.BACKGROUND_SIZE[0] >= w
    assert layout.BACKGROUND_SIZE[1] >= h
    assert h - layout.TITLE_BAR == layout.WINDOW_SIZE[1]


def test_every_icon_and_label_sits_inside_the_visible_window():
    width, height = layout.WINDOW_SIZE
    for name in layout.ICON_POSITIONS:
        for box in (_icon_box(name), _plate_box(name)):
            assert box[0] >= 0 and box[1] >= 0
            assert box[2] <= width and box[3] <= height, name


def test_readme_icon_clears_the_command_box_and_the_body_text():
    readme = layout.README_NAME
    assert not _overlaps(_plate_box(readme), layout.CODE_BOX)
    assert not _overlaps(_icon_box(readme), layout.CODE_BOX)
    # The body text column ends at x = 44 + 430.
    assert _icon_box(readme)[0] > 44 + 430
    assert _plate_box(readme)[0] > 44 + 430


def test_the_command_shown_is_the_one_the_wiki_and_readme_give():
    wiki = (REPO / "docs" / "wiki" / "macOS.md").read_text(encoding="utf-8")
    assert layout.QUARANTINE_COMMAND in wiki
    assert layout.QUARANTINE_COMMAND in layout.README_TEXT
    assert f'"/Applications/{layout.APP_NAME}"' in layout.QUARANTINE_COMMAND


def test_command_fits_its_box_at_both_scales():
    x0, _, x1, _ = layout.CODE_BOX
    for scale in (1, 2):
        font = layout._fit_mono(layout.QUARANTINE_COMMAND, (x1 - x0 - 28) * scale, 13 * scale)
        assert font.getlength(layout.QUARANTINE_COMMAND) <= (x1 - x0 - 28) * scale


def test_dmg_settings_lay_out_the_window(tmp_path):
    app = tmp_path / layout.APP_NAME
    readme = tmp_path / layout.README_NAME
    background = tmp_path / "background.png"
    settings = build_dmg_macos.dmg_settings(app, readme, background)

    names_in_image = {name for _, name in settings["files"]}
    assert names_in_image == {layout.APP_NAME, layout.README_NAME}
    assert settings["symlinks"] == {"Applications": "/Applications"}
    assert set(settings["icon_locations"]) == names_in_image | {"Applications"}
    assert settings["window_rect"] == layout.WINDOW_RECT
    assert settings["icon_size"] == layout.ICON_SIZE
    assert settings["background"] == str(background)
    assert settings["format"] == "UDZO"
    assert Path(settings["icon"]).is_file()
    assert not settings["show_toolbar"] and not settings["show_sidebar"]


def test_building_refuses_off_macos(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    assert build_dmg_macos.main(["x", "a.app", "b.dmg"]) == 1
    assert "macOS" in capsys.readouterr().err
