"""Render the Library's imaging-conditions views to PNG files (dev only).

Builds a synthetic night in a temporary library (clear -> cloudy -> clear,
a few ungraded frames at the end, plus an older night with no grades),
opens the real Library panel on it with the app theme and stylesheet, and
saves screenshots at 2x:

    night_clear.png      night view on a clear, "OK" frame (with the
                         target's distance from the Moon in the grade block)
    night_cloudy.png     night view on a cloudy, "Not OK" frame
    night_ungraded.png   night view on a frame that was never graded
    session_list.png     the session list with both nights' cards

Runs offscreen and never touches the real app-data directory: every path
the app resolves is pointed at the temporary directory before any app
module is imported.

    python scripts/dev/render_library_grade.py --out /tmp/library_grade
"""
import argparse
import math
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta
from io import BytesIO

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRAMES = 200
STEP_S = 150
NIGHT_START = datetime(2026, 7, 3, 21, 0, 0)
THUMB_PX = 750


def isolate_app_data(root):
    """Point every app-data lookup at ``root``. Must run before app imports."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "2"
    os.environ["LOCALAPPDATA"] = root
    os.environ["XDG_DATA_HOME"] = root
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from services import utils_paths
    data_dir = os.path.join(root, "PFRSentinel")
    os.makedirs(data_dir, exist_ok=True)
    utils_paths.get_app_data_dir = lambda: data_dir
    return data_dir


def cloud_fraction(i):
    """0 clear .. 1 overcast: clear, cloud through the middle, clear again."""
    t = i / (FRAMES - 1)
    return max(0.0, min(1.0, 1.35 * math.exp(-((t - 0.5) / 0.17) ** 2) - 0.08))


def sky_jpeg(i, when, rng_stars):
    """A drawn all-sky frame: stars thinned and veiled by the cloud fraction."""
    from PIL import Image, ImageDraw, ImageFilter
    cloud = cloud_fraction(i)
    size = THUMB_PX
    img = Image.new("RGB", (size, size), (2, 2, 4))
    draw = ImageDraw.Draw(img)
    c, r = size / 2, size * 0.47
    for k in range(40, 0, -1):  # sky glow, brighter toward the horizon
        rr = r * k / 40
        shade = int(8 + 22 * (k / 40) ** 3)
        draw.ellipse((c - rr, c - rr, c + rr, c + rr), fill=(shade // 2, shade // 2 + 2, shade + 6))
    drift = i * 0.004  # the sky turns through the night
    for ang, rad, mag in rng_stars:
        if mag < cloud * 1.15:
            continue  # faint stars go first
        a = ang + drift
        x, y = c + rad * r * math.cos(a), c + rad * r * math.sin(a)
        b = int(110 + 145 * mag * (1 - 0.6 * cloud))
        s = 0.7 + 1.6 * mag
        draw.ellipse((x - s, y - s, x + s, y + s), fill=(b, b, min(255, b + 10)))
    if cloud > 0.02:
        veil = Image.new("L", (size, size), 0)
        vd = ImageDraw.Draw(veil)
        rng = random.Random(1000 + i // 4)
        for _ in range(int(4 + 26 * cloud)):
            w = rng.uniform(90, 260)
            x = (rng.uniform(-0.2, 1.0) * size + i * 3.0) % (size * 1.2) - size * 0.1
            y = rng.uniform(0.05, 0.9) * size
            vd.ellipse((x, y, x + w, y + w * 0.55), fill=int(70 + 150 * cloud))
        veil = veil.filter(ImageFilter.GaussianBlur(38))
        img = Image.composite(Image.new("RGB", (size, size), (74, 70, 66)), img, veil)
        draw = ImageDraw.Draw(img)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((c - r, c - r, c + r, c + r), fill=255)
    img = Image.composite(img, Image.new("RGB", (size, size), (0, 0, 0)), mask)
    ImageDraw.Draw(img).text((14, size - 26), when.strftime("%Y-%m-%d %H:%M:%S"),
                             fill=(200, 200, 200))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def grade_for(i):
    """The grade columns a frame of the synthetic night would have stored."""
    if i >= FRAMES - 8:
        return {}  # conditions were switched off for the last frames
    cloud = cloud_fraction(i)
    wobble = 0.03 * math.sin(i * 1.3)
    ratio = round(max(0.03, 1.0 - 0.92 * cloud + wobble), 3)
    frame_ok = ratio >= 0.6
    clearing = i > FRAMES / 2
    if frame_ok and clearing and ratio < 0.68:
        ok, reasons = False, "recovering"      # good frames, verdict still waiting
    elif frame_ok:
        ok, reasons = True, ""
    elif ratio < 0.4:
        ok, reasons = False, "cloud_cover_high,stars_low"
    else:
        ok, reasons = False, "cloud_cover_high"
    return {"imaging_ok": int(ok), "frame_ok": int(frame_ok), "imaging_reasons": reasons,
            "clear_sky_ratio": ratio, "moon_bin": "moon_up_75_100_high",
            # NINA's target drifting away from a bright, high Moon.
            "moon_separation_deg": round(18.0 + i * 0.03, 1)}


def build_library(root):
    """A temp library with the synthetic night and an older, ungraded night."""
    from services.library import ImageLibrary
    from services.library.index import LibraryIndex
    from services.library.store import LibraryStore

    library = ImageLibrary(lambda: {})
    library.store = LibraryStore(os.path.join(root, "Library"))
    library.index = LibraryIndex(library.store.db_path)
    rng = random.Random(7)
    stars = [(rng.uniform(0, 2 * math.pi), math.sqrt(rng.random()), rng.random() ** 2.2)
             for _ in range(650)]

    def add(i, when, grade, cloud):
        data = sky_jpeg(i, when, stars)
        rel_path, size = library.store.write(data, when)
        count = int(520 * (1 - 0.93 * cloud) + 12 * math.sin(i))
        fwhm = 2.6 + 2.2 * cloud + 0.1 * math.sin(i * 0.7)
        library.index.insert({
            "captured_at": int(when.timestamp()), "path": rel_path,
            "width": THUMB_PX, "height": THUMB_PX, "bytes": size,
            "created_at": int(when.timestamp()), "session": when.strftime("%Y-%m-%d"),
            "exposure": "13.0s", "gain": "300", "temp": f"{14.5 - i * 0.012:.1f}°C",
            "camera": "ZWO ASI676MC", "weather": None, "roof": "Open",
            "condition": ("Clear" if cloud < 0.25 else
                          "Partly Cloudy" if cloud < 0.6 else "Overcast"),
            "clouds": int(round(cloud * 100)), "star_count": max(0, count),
            "seeing": "Good" if fwhm < 3.2 else "Fair" if fwhm < 4.2 else "Poor",
            "fwhm": f"{fwhm:.1f}", **grade,
        })

    for i in range(FRAMES):
        add(i, NIGHT_START + timedelta(seconds=i * STEP_S), grade_for(i), cloud_fraction(i))
    earlier = NIGHT_START - timedelta(days=1)
    for k in range(40):  # archived before grades existed: nothing to show
        i = 6 + k // 8
        add(i, earlier + timedelta(seconds=k * 600), {}, cloud_fraction(i))
    return library


def pump(app, until, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if until():
            for _ in range(5):
                app.processEvents()
            return True
        time.sleep(0.01)
    return False


def render(out_dir, root):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Segoe UI", 10))
    from ui.theme import apply_theme, get_stylesheet
    apply_theme()

    from ui.controllers.library_controller import LibraryController
    from ui.panels.library_panel import LibraryPanel

    class Host(QWidget):
        """Stands in for the main window: what the panel and controller read."""

        def __init__(self, library):
            super().__init__()
            self.image_library = library
            self.config = {"meteor": {}}
            self.library_controller = None

    host = Host(build_library(root))
    host.setStyleSheet(get_stylesheet())
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = LibraryPanel(host)
    layout.addWidget(panel)
    controller = LibraryController(host)
    host.library_controller = controller
    controller.sessions_ready.connect(panel.set_sessions)
    controller.frames_ready.connect(panel.set_frames)
    controller.frame_ready.connect(panel.on_frame_ready)
    controller.load_failed.connect(panel.on_load_failed)

    sessions = []
    controller.sessions_ready.connect(sessions.extend)
    saved = []

    def save(name):
        path = os.path.join(out_dir, name)
        if not host.grab().save(path):
            raise RuntimeError(f"could not write {path}")
        saved.append(path)

    try:
        host.resize(1280, 330)
        host.show()
        if not pump(app, lambda: bool(sessions)):
            raise RuntimeError("the session list did not load")
        save("session_list.png")

        host.resize(1280, 860)
        night = max(sessions, key=lambda s: s.get("graded_count") or 0)
        view = panel.night_view
        panel.open_session(night)
        if not pump(app, lambda: view.scrubber.frame_count() == FRAMES):
            raise RuntimeError("the night did not load")

        heroes = []
        controller.frame_ready.connect(lambda image_id, _data: heroes.append(image_id))
        for name, index in (("night_clear.png", 22), ("night_cloudy.png", FRAMES // 2),
                            ("night_ungraded.png", FRAMES - 4)):
            frame_id = view._frames[index]["id"]
            view._seek_to_id(frame_id)
            if not pump(app, lambda: frame_id in heroes):
                raise RuntimeError(f"frame {frame_id} did not load")
            save(name)
    finally:
        controller.shutdown()
        host.close()
        host.image_library.index.close()
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="directory the PNG files are written to")
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pfr_library_grade_") as root:
        isolate_app_data(root)
        for path in render(out_dir, root):
            print(path)


if __name__ == "__main__":
    main()
