"""Render the imaging-conditions views to PNG files (dev only).

Synthetic data only: a night with a bright Moon high in the sky and NINA's
target close to it. Each widget is the real one, with the app theme and
stylesheet, saved at 2x:

    live_panel_learned.png    Live Monitoring's conditions panel, baseline
                              learned, target near a bright high Moon
    live_panel_learning.png   the same while this Moon bin is still learning
    live_panel_far.png        the same with the target well away from the Moon
    conditions_card.png       the NINA & ASCOM Imaging Conditions card,
                              expanded with Advanced open

Runs offscreen and never touches the real app-data directory: every path
the app resolves is pointed at a temporary directory before any app module
is imported.

    python scripts/dev/render_conditions_views.py --out /tmp/conditions_views
"""
import argparse
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NOW = datetime(2026, 11, 24, 3, 12, 0, tzinfo=timezone.utc)
BIN = 'moon_up_75_100_high'
BASELINE = 142.0


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


def _iso(when):
    return when.isoformat(timespec='seconds').replace('+00:00', 'Z')


def snapshot(*, learned=True, separation=18.0):
    """A ``GET /conditions`` payload: OK to image under an 87 % Moon at 62°."""
    status = ({'state': 'ready', 'samples': 412, 'needed': 30, 'bin': BIN, 'nights': 4}
              if learned else
              {'state': 'learning', 'samples': 12, 'needed': 30, 'bin': BIN, 'nights': 0})
    baseline = BASELINE if learned else None
    count = 131
    return {
        'enabled': True, 'updated_at': _iso(NOW - timedelta(seconds=20)), 'age_s': 20.0,
        'stale': False, 'stale_after_s': 120.0, 'imaging_ok': True, 'reasons': [],
        'changed_at': _iso(NOW - timedelta(minutes=64)), 'frames_considered': 310,
        'frame_ok': True, 'bad_streak': 0, 'good_streak': 118,
        'roof': {'state': 'Open', 'confidence': 0.99},
        'sky': {'condition': 'Clear', 'confidence': 0.93, 'stars_visible': True},
        'stars': {'count': count, 'baseline': baseline,
                  'ratio': round(count / baseline, 3) if baseline else None,
                  'trend': -0.02, 'change_pct': -2.0, 'window_frames': 10},
        'cloud_cover_pct': round(100 * (1 - count / baseline), 1) if baseline else 10.0,
        'cloud_cover_source': 'star_baseline' if baseline else 'ml_class',
        'fwhm': {'px': 2.9, 'arcsec': None, 'night_median_px': 2.8, 'change_pct': 3.6,
                 'reference_px': 2.6, 'reference_nights': 4},
        'sun_altitude_deg': -41.2,
        'moon': {'up': True, 'illumination_pct': 87.0, 'altitude_deg': 62.3},
        'baseline_bin': BIN, 'baseline_status': status,
        'target': {'name': 'M 15', 'moon_separation_deg': separation, 'altitude_deg': 71.4},
        'events': [],
    }


def history(learned=True):
    """Chart samples and events: a cloud bank an hour ago, clear since."""
    from services.imaging_conditions.conditions_history import Sample
    samples = []
    start = NOW - timedelta(hours=4)
    for i in range(480):
        at = start + timedelta(seconds=30 * i)
        minutes = i / 2
        cloud = max(0.0, 1.2 * math.exp(-((minutes - 150) / 14) ** 2) - 0.05)
        count = int(BASELINE * (0.93 - 0.85 * cloud) + 5 * math.sin(i * 0.9))
        ok = not 136 <= minutes < 176
        samples.append(Sample(at=at, star_index=max(0, count),
                              baseline=BASELINE if learned else None, imaging_ok=ok,
                              frame_ok=cloud < 0.3,
                              reasons=() if ok else ('cloud_cover_high',)))
    paused = start + timedelta(minutes=136)
    resumed = start + timedelta(minutes=176)
    events = [
        {'at': resumed, 'kind': 'resumed', 'reasons': [], 'ended_at': None,
         'duration_s': None},
        {'at': paused, 'kind': 'paused', 'reasons': ['cloud_cover_high'],
         'ended_at': resumed, 'duration_s': (resumed - paused).total_seconds()},
        {'at': start, 'kind': 'resumed', 'reasons': [], 'ended_at': paused,
         'duration_s': (paused - start).total_seconds()},
    ]
    return samples, events


def render(out_dir):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Segoe UI", 10))
    from ui.theme import apply_theme, get_stylesheet
    from ui.theme.tokens import Colors
    apply_theme()

    from ui.panels.conditions_live_panel import ConditionsLivePanel
    from ui.panels.imaging_conditions_card import ImagingConditionsCard
    from services.config_defaults import DEFAULT_CONFIG

    saved = []

    def shoot(widget, name, width=None, height=None):
        host = QWidget()
        host.setStyleSheet(get_stylesheet() + f"\nQWidget#shot {{ background: {Colors.bg_app}; }}")
        host.setObjectName("shot")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(widget)
        if width:
            host.setFixedWidth(width)
        host.show()
        for _ in range(20):
            app.processEvents()
        host.resize(host.width(), height or host.sizeHint().height())
        for _ in range(20):
            app.processEvents()
        path = os.path.join(out_dir, name)
        if not host.grab().save(path):
            raise RuntimeError(f"could not write {path}")
        saved.append(path)
        host.close()

    checkins = {'nina': {'client': 'NINA', 'age_s': 6.0, 'accepted': True},
                'any': {'client': 'NINA', 'age_s': 6.0, 'accepted': True}}
    for name, learned, separation in (("live_panel_learned.png", True, 18.0),
                                      ("live_panel_learning.png", False, 18.0),
                                      ("live_panel_far.png", True, 96.0)):
        panel = ConditionsLivePanel()
        samples, events = history(learned)
        panel.set_view(snapshot(learned=learned, separation=separation), capturing=True,
                       good_needed=3, samples=samples, events=events, checkins=checkins,
                       now=NOW)
        shoot(panel, name, height=640)

    card = ImagingConditionsCard()
    card.load(dict(DEFAULT_CONFIG['imaging_conditions'], enabled=True))
    card._toggle_expanded(None)
    card.set_advanced_open(True)
    card.show_conditions(snapshot(), capturing=True)
    shoot(card, "conditions_card.png", width=900)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="directory the PNG files are written to")
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pfr_conditions_views_") as root:
        isolate_app_data(root)
        for path in render(out_dir):
            print(path)


if __name__ == "__main__":
    main()
