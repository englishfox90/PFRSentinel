"""Render the imaging-conditions views IN CONTEXT to PNG files (dev only).

The real main window, with the app theme and stylesheet, at 2x: the
conditions panel beside the real preview card on Live Monitoring, expanded
and collapsed, at a normal and a narrow window width, and the Imaging
Conditions card on the NINA & ASCOM page, collapsed and expanded.

    live_cold_expanded.png      capture running, no frame judged yet
    live_cold_pointer_on_panel.png  the same with the pointer resting on the panel
    live_ok_expanded.png        OK to image, baseline learned, NINA checking in
    live_paused_expanded.png    paused under cloud, recovering
    live_ok_collapsed.png       the same night with the panel collapsed
    live_paused_collapsed.png
    live_narrow_expanded.png    the narrow window (1000 px)
    live_narrow_collapsed.png
    nina_card_collapsed.png     NINA & ASCOM page, card collapsed
    nina_card_expanded.png      NINA & ASCOM page, card expanded
    output_cards.png            Output page: other collapsible cards, one open

Synthetic data only (``render_conditions_views``' night), fed through the
real ``ImagingConditionsController`` from stand-in store / history /
check-ins. Runs offscreen, starts no capture, and never touches the real
app-data directory.

    python scripts/dev/render_live_monitoring.py --out /tmp/live_monitoring
"""
import argparse
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_conditions_views as views  # noqa: E402

WIDE = (1440, 900)
NARROW = (1000, 720)


class _Store:
    def __init__(self):
        self.value = {}

    def snapshot(self):
        return dict(self.value)

    def set_enabled(self, _enabled):
        pass


class _History:
    def __init__(self):
        self.s, self.e = [], []

    def samples(self):
        return list(self.s)

    def events(self):
        return list(self.e)


class _Checkins:
    def __init__(self):
        self.value = {}

    def last(self):
        return dict(self.value)


def cold_snapshot():
    return {'enabled': True, 'imaging_ok': False, 'reasons': ['warming_up'], 'stale': False,
            'updated_at': None, 'age_s': None, 'changed_at': None, 'frames_considered': 0,
            'good_streak': 0, 'bad_streak': 0, 'stars': {}, 'fwhm': {}, 'events': []}


def paused_snapshot():
    snap = views.snapshot()
    snap.update(imaging_ok=False, reasons=['recovering'], good_streak=1, frame_ok=True,
                changed_at=views._iso(views.NOW - timedelta(minutes=9)))
    snap['stars'].update(count=96, ratio=0.68, change_pct=14.0)
    snap['cloud_cover_pct'] = 32.0
    return snap


def paused_history():
    samples, events = views.history()
    paused = views.NOW - timedelta(minutes=9)
    events = [{'at': paused, 'kind': 'paused', 'reasons': ['cloud_cover_high'],
               'ended_at': None, 'duration_s': None}] + [
        dict(e, ended_at=paused, duration_s=(paused - e['at']).total_seconds())
        if e['ended_at'] is None else e for e in events]
    return samples, events


def sky_image(size=900):
    """A drawn all-sky frame for the preview, so the card is not empty."""
    import random
    from PIL import Image, ImageDraw, ImageFilter
    rng = random.Random(7)
    img = Image.new('RGB', (size, size), (4, 5, 9))
    draw = ImageDraw.Draw(img)
    c, r = size / 2, size * 0.47
    draw.ellipse((c - r, c - r, c + r, c + r), fill=(13, 17, 30))
    for _ in range(420):
        a, d = rng.uniform(0, 6.283), r * rng.random() ** 0.5
        import math
        x, y = c + d * math.cos(a), c + d * math.sin(a)
        b = rng.randint(90, 255)
        s = rng.choice((0.6, 0.8, 1.0, 1.6))
        draw.ellipse((x - s, y - s, x + s, y + s), fill=(b, b, min(255, b + 10)))
    return img.filter(ImageFilter.GaussianBlur(0.5))


def render(out_dir):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent, QFont
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Segoe UI", 10))
    from services import posthog_service
    posthog_service.posthog.disabled = True
    posthog_service.capture_event = lambda *a, **k: None
    posthog_service.capture_error = lambda *a, **k: None
    from ui.main_window import MainWindow

    window = MainWindow()
    for name in ('_auto_detect_cameras', '_send_discord_startup',
                 '_ensure_output_servers_started', '_confirm_coordinate_hemispheres'):
        setattr(window, name, lambda *a, **k: None)
    ctrl = window.imaging_conditions_controller
    ctrl._timer.stop()
    store, history, checkins = _Store(), _History(), _Checkins()
    ctrl._store, ctrl._history, ctrl._checkins = store, history, checkins
    capturing = {'on': True}
    ctrl._capturing = lambda: capturing['on']
    views_now = views.NOW
    real_refresh_live = ctrl._refresh_live

    def refresh_live(snapshot, is_capturing):
        ctrl._live.set_view(snapshot, capturing=is_capturing, good_needed=3,
                            samples=history.samples(), events=history.events(),
                            checkins=checkins.last(), now=views_now)
    ctrl._refresh_live = refresh_live
    del real_refresh_live

    try:
        window.live_panel.update_preview(sky_image(), {})
    except Exception as e:  # the preview API is not this script's subject
        print(f"(preview left empty: {e})")

    saved = []

    def settle():
        for _ in range(30):
            app.processEvents()

    def shoot(name, size):
        window.resize(*size)
        window.show()
        settle()
        ctrl.refresh()
        settle()
        path = os.path.join(out_dir, name)
        if not window.grab().save(path):
            raise RuntimeError(f"could not write {path}")
        saved.append(path)

    def state(snapshot, samples_events, nina=True):
        store.value = snapshot
        history.s, history.e = samples_events
        checkins.value = ({'nina': {'client': 'NINA', 'age_s': 6.0, 'accepted': True},
                           'any': {'client': 'NINA', 'age_s': 6.0, 'accepted': True}}
                          if nina else {})

    window.go_to_section('monitoring')
    window.live_panel.set_conditions_open(True)
    state(cold_snapshot(), ([], []), nina=False)
    shoot("live_cold_expanded.png", WIDE)
    # The pointer resting on the panel: the state the owner's screenshot shows.
    panel = window.live_panel.conditions_panel
    centre = QPointF(panel.width() / 2, panel.height() / 2)
    app.sendEvent(panel, QEnterEvent(centre, centre, QPointF(panel.mapToGlobal(centre.toPoint()))))
    QTest.qWait(300)
    shoot("live_cold_pointer_on_panel.png", WIDE)
    app.sendEvent(panel, QEvent(QEvent.Leave))
    QTest.qWait(300)
    state(views.snapshot(), views.history())
    shoot("live_ok_expanded.png", WIDE)
    shoot("live_narrow_expanded.png", NARROW)
    state(paused_snapshot(), paused_history())
    shoot("live_paused_expanded.png", WIDE)
    window.live_panel.set_conditions_open(False)
    shoot("live_paused_collapsed.png", WIDE)
    state(views.snapshot(), views.history())
    shoot("live_ok_collapsed.png", WIDE)
    shoot("live_narrow_collapsed.png", NARROW)

    window.go_to_section('nina')
    card = window.nina_panel.conditions_card
    if card.content.isVisible():
        card._toggle_expanded(None)
    shoot("nina_card_collapsed.png", WIDE)
    card._toggle_expanded(None)
    shoot("nina_card_expanded.png", WIDE)

    # Other users of CollapsibleCard, one open and the rest closed.
    window.go_to_section('output')
    from ui.components.cards import CollapsibleCard
    cards = window.output_panel.findChildren(CollapsibleCard)
    for i, other in enumerate(cards):
        other.set_expanded(i == 0)
    shoot("output_cards.png", WIDE)

    window._force_quit = True
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="directory the PNG files are written to")
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pfr_live_monitoring_") as root:
        views.isolate_app_data(root)
        for path in render(out_dir):
            print(path)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
