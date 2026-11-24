"""Live Monitoring's imaging-conditions panel and the NINA & ASCOM page:
the one-sentence verdict, event / learning / NINA-check-in wording, the
chart's not-OK spans, the panel shown only on Live Monitoring and
collapsible, the IMAGING tile as a link that opens it, the page in the nav
rail, and the moved Roof Safety and Control API cards saving the same
config keys (offscreen Qt)."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from services.imaging_conditions.conditions_history import Sample
from ui.components.conditions_live_text import (
    event_line, learning_progress, measurements_line, nina_checked_text, verdict_sentence)

T0 = datetime(2026, 11, 23, 22, 0, tzinfo=timezone.utc)


def local(dt):
    return dt.astimezone().strftime('%H:%M')


def snap(**kw):
    base = {'enabled': True, 'imaging_ok': False, 'reasons': [], 'stale': False,
            'updated_at': '2026-11-23T22:30:00Z', 'changed_at': '2026-11-23T22:14:00Z',
            'frames_considered': 40, 'good_streak': 0,
            'stars': {'count': 131, 'baseline': 315.0}, 'cloud_cover_pct': 58.0,
            'fwhm': {'px': 2.94}, 'baseline_status': {'state': 'ready', 'samples': 30,
                                                      'needed': 30, 'bin': 'moon_down'}}
    base.update(kw)
    return base


# --- wording -------------------------------------------------------------

def test_verdict_sentences():
    at = local(T0 + timedelta(minutes=14))
    assert verdict_sentence(snap(reasons=['cloud_cover_high'])) == \
        (f"Imaging paused at {at} — cloud moving in.", 'warn')
    pause = {'kind': 'paused', 'at': T0 + timedelta(minutes=14),
             'reasons': ['cloud_cover_high'], 'ended_at': None}
    assert verdict_sentence(snap(reasons=['recovering'], good_streak=2), True, 3, [pause]) == \
        (f"Imaging paused at {at} — cloud moving in. 2 of 3 good frames to resume.", 'warn')
    assert verdict_sentence(snap(imaging_ok=True), True) == (f"OK to image since {at}.", 'ok')
    cold = {'enabled': True, 'reasons': ['warming_up'], 'updated_at': None,
            'frames_considered': 0}
    assert verdict_sentence(cold, capturing=False) == \
        ("Idle — start capture to judge the sky.", 'muted')
    waiting = ("Waiting for the first frame — nothing judged yet, so NINA is not held back.",
               'info')
    assert verdict_sentence(dict(cold), capturing=True, good_needed=3) == waiting
    assert verdict_sentence(dict(cold), good_needed=3) == waiting
    assert "good frame" not in waiting[0]
    assert verdict_sentence(snap(reasons=['recovering'], good_streak=0), True, 3)[0].endswith(
        "0 of 3 good frames to resume.")
    assert "no verdict" in verdict_sentence({'enabled': False})[0]
    for capturing in (True, False):
        text = verdict_sentence(snap(stale=True, reasons=['stale']), capturing)[0]
        assert "no verdict" in text and "told" not in text
    stale = verdict_sentence(snap(stale=True, reasons=['stale']), True)
    assert stale[0].startswith("No new frames since ") and stale[1] == 'warn'
    assert verdict_sentence(snap(stale=True, reasons=['stale']), False)[1] == 'muted'
    assert verdict_sentence({'enabled': False})[0].startswith("Off")
    assert verdict_sentence(snap(reasons=['daylight'], sun_altitude_deg=4.2))[0] == \
        "Waiting for dark (sun at 4°) — imaging starts once the sun is low enough."
    assert verdict_sentence(snap(reasons=['stars_low'], changed_at=None))[0] == \
        "Not OK to image yet — too few stars showing."


def test_no_jargon_in_any_pause_reason():
    from ui.components.conditions_live_text import CAUSE_TEXT
    for code, text in CAUSE_TEXT.items():
        assert '_' not in text and 'ratio' not in text and 'FWHM' not in text, code


def test_event_lines():
    now = T0 + timedelta(minutes=30)
    paused = {'kind': 'paused', 'at': T0, 'reasons': ['stale'], 'duration_s': 2520.0}
    assert event_line(paused, now) == (local(T0), "Paused — no new frames", "42 min")
    ongoing = {'kind': 'resumed', 'at': T0, 'reasons': [], 'duration_s': None}
    assert event_line(ongoing, now) == (local(T0), "OK to image", "30 min so far")
    assert event_line(ongoing, T0 + timedelta(seconds=20))[2] == "now"
    long = dict(paused, duration_s=3 * 3600 + 5 * 60)
    assert event_line(long, now)[2] == "3 h 5 min"


def test_learning_and_measurements():
    learning = snap(baseline_status={'state': 'learning', 'samples': 12, 'needed': 30,
                                     'bin': 'moon_up_25_50'})
    assert learning_progress(learning) == (
        "Learning your clear sky with the Moon up, 25–50% lit — 12 of 30 clear frames", 12, 30)
    assert learning_progress(snap())[0] == \
        "Clear-sky baseline learned with the Moon down: 315 stars"
    nights = snap(baseline_status={'state': 'ready', 'samples': 900, 'needed': 30,
                                   'bin': 'moon_down', 'nights': 12})
    assert learning_progress(nights) == (
        "Clear-sky baseline learned with the Moon down: 315 stars from 12 clear nights",
        30, 30)
    nights['baseline_status']['nights'] = 1
    assert learning_progress(nights)[0].endswith("from 1 clear night")
    assert learning_progress({'enabled': True, 'frames_considered': 0}) is None
    assert measurements_line(snap()) == "Stars 131/315 · Cloud 58% · FWHM 2.9 px"
    assert measurements_line({'enabled': True}) == ""


def test_nina_check_in_wording():
    assert nina_checked_text({}) == ("NINA hasn't checked in yet", 'muted')
    other = {'client': 'curl', 'age_s': 12.0, 'accepted': True, 'at': T0}
    assert nina_checked_text({'any': other})[0] == "NINA hasn't checked in · curl read it 12 s ago"
    nina = dict(other, client='NINA plugin', age_s=4.0)
    assert nina_checked_text({'nina': nina}) == ("NINA last checked 4 s ago", 'ok')
    assert nina_checked_text({'nina': dict(nina, age_s=1800)}, capturing=True)[1] == 'warn'
    assert "refused" in nina_checked_text({'nina': dict(nina, accepted=False)})[0]


# --- widgets -------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _dispose(qapp, *widgets):
    for w in widgets:
        w.close()
        w.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _samples():
    rows = []
    for i in range(60):
        ok = not 20 <= i < 30
        rows.append(Sample(T0 + timedelta(minutes=2 * i), 300 if ok else 120, 315.0, ok,
                           ok, () if ok else ('cloud_cover_high',)))
    return rows


def test_chart_shades_not_ok_stretches_and_gaps(qapp):
    from ui.components.conditions_chart import ConditionsChart
    chart = ConditionsChart()
    rows = _samples()
    shifted = [Sample(r.at + timedelta(minutes=20), r.star_index, r.baseline, r.imaging_ok,
                      r.frame_ok, r.reasons) for r in rows[45:]]
    rows = rows[:45] + shifted
    end = rows[-1].at + timedelta(seconds=30)
    chart.set_samples(rows, end)
    spans = chart.not_ok_spans()
    assert spans[0] == (T0 + timedelta(minutes=40), T0 + timedelta(minutes=60))
    assert spans[1] == (rows[44].at, rows[45].at)  # the 22-minute gap
    assert len(spans) == 2
    chart.resize(300, 132)
    chart.grab()  # paints without raising
    _dispose(qapp, chart)


def test_live_panel_fills_every_part(qapp):
    from ui.panels.conditions_live_panel import ConditionsLivePanel
    panel = ConditionsLivePanel()
    events = [{'kind': 'paused', 'at': T0 + timedelta(minutes=40), 'reasons': ['cloud_cover_high'],
               'ended_at': None, 'duration_s': None},
              {'kind': 'resumed', 'at': T0, 'reasons': [], 'ended_at': T0 + timedelta(minutes=40),
               'duration_s': 2400.0}]
    panel.set_view(snap(reasons=['cloud_cover_high']), capturing=True, good_needed=3,
                   samples=_samples(), events=events,
                   checkins={'nina': {'client': 'NINA plugin', 'age_s': 4.0, 'accepted': True,
                                      'at': T0}},
                   now=T0 + timedelta(hours=2))
    assert panel.verdict_label.text().startswith("Imaging paused at")
    assert panel.nina_label.text() == "NINA last checked 4 s ago"
    shown = [r for r in panel.event_rows if not r[0].isHidden()]
    assert len(shown) == 2 and shown[0][2].full_text() == "Paused — cloud moving in"
    assert shown[1][3].text() == "40 min"
    assert panel.no_events_label.isHidden()
    tip = panel.measurements_label.toolTip()
    assert "Stars: " in tip and "SEEING tile" in tip and "index" not in tip.lower()
    _dispose(qapp, panel)


def test_panel_only_on_live_monitoring_and_collapsible(qapp):
    from ui.panels.conditions_live_panel import PANEL_WIDTH
    from ui.components.conditions_rail import RAIL_WIDTH
    from ui.panels.live_monitoring import LiveMonitoringPanel
    live = LiveMonitoringPanel()
    live.resize(1300, 800)
    live.show()
    qapp.processEvents()
    panel = live.conditions_panel
    toggles = []
    live.conditions_toggled.connect(toggles.append)
    live.set_preview_only(False)
    assert panel.isHidden()
    live.set_preview_only(True)
    qapp.processEvents()
    assert not panel.isHidden() and not panel.is_collapsed()
    assert panel.rail.isHidden() and panel.width() == PANEL_WIDTH

    # The whole header row collapses it, not only the chevron: a click on
    # the title, far from the chevron, is enough.
    header = panel.collapse_button
    assert header is panel.header and header.width() > PANEL_WIDTH - 20
    assert header.cursor().shape() == Qt.PointingHandCursor and header.toolTip()
    assert header.focusPolicy() & Qt.TabFocus
    assert header.chevron_name() == 'chevron-right'
    QTest.mouseClick(header, Qt.LeftButton, Qt.NoModifier, QPoint(60, header.height() // 2))
    qapp.processEvents()

    # Collapsed: still there as a rail (not a button in the preview header),
    # anchored beside the preview at the panel's height, and it opens again.
    assert not panel.isHidden() and panel.is_collapsed()
    assert panel.body.isHidden() and not panel.rail.isHidden()
    assert panel.width() == RAIL_WIDTH and panel.rail.height() > 300
    assert not hasattr(live, 'conditions_button')
    rail = panel.rail
    assert rail.cursor().shape() == Qt.PointingHandCursor
    assert rail.toolTip().startswith("Show the imaging conditions panel")
    QTest.mouseClick(rail, Qt.LeftButton, Qt.NoModifier, QPoint(10, rail.height() - 30))
    qapp.processEvents()
    assert not panel.is_collapsed() and panel.rail.isHidden() and not panel.body.isHidden()
    assert toggles == [False, True]

    # Keyboard: Space on the header, Enter on the rail; focus follows the
    # control so the same key toggles back.
    header.setFocus(Qt.TabFocusReason)
    QTest.keyClick(header, Qt.Key_Space)
    qapp.processEvents()
    assert panel.is_collapsed() and rail.hasFocus()
    QTest.keyClick(rail, Qt.Key_Return)
    qapp.processEvents()
    assert not panel.is_collapsed() and header.hasFocus()
    assert toggles == [False, True, False, True]
    _dispose(qapp, live)


def test_collapsed_rail_keeps_the_verdict_in_sight(qapp):
    from ui.panels.conditions_live_panel import ConditionsLivePanel
    from ui.theme.tokens import Colors
    panel = ConditionsLivePanel()
    panel.set_collapsed(True)
    panel.resize(panel.width(), 600)
    panel.show()
    panel.set_view(snap(reasons=['cloud_cover_high']), capturing=True, good_needed=3)
    rail = panel.rail
    assert rail.status_text() == "Paused · Cloud cover high"
    assert rail.status_color() == Colors.warning_text
    assert "Imaging paused at" in rail.toolTip()
    panel.set_view(snap(imaging_ok=True, cloud_cover_pct=8.0), capturing=True)
    assert rail.status_text() == "OK · 8% cloud" and rail.status_color() == Colors.success_text
    # The dot is painted in the verdict's colour.
    qapp.processEvents()
    image = rail.grab().toImage()
    want = QColor(Colors.success_text)
    greens = sum(
        1 for y in range(0, min(140, image.height())) for x in range(image.width())
        if abs(image.pixelColor(x, y).green() - want.green()) < 30
        and abs(image.pixelColor(x, y).red() - want.red()) < 30)
    assert greens > 10
    _dispose(qapp, panel)


def test_live_panel_narrows_beside_a_small_preview(qapp):
    from ui.panels.conditions_live_panel import PANEL_MIN_WIDTH, PANEL_WIDTH
    from ui.panels.live_monitoring import LiveMonitoringPanel
    live = LiveMonitoringPanel()
    live.set_preview_only(True)
    live.resize(1300, 800)
    live.show()
    qapp.processEvents()
    assert live.conditions_panel.width() == PANEL_WIDTH
    live.resize(660, 800)
    qapp.processEvents()
    assert PANEL_MIN_WIDTH <= live.conditions_panel.width() < PANEL_WIDTH
    live.resize(300, 800)
    qapp.processEvents()
    assert live.conditions_panel.width() == PANEL_MIN_WIDTH
    _dispose(qapp, live)


def test_live_cards_are_one_family_with_or_without_the_pointer(qapp):
    """The owner's screenshot: the conditions panel had a lighter fill and a
    pale border because qfluentwidgets' CardWidget lights up under the
    pointer. Both cards are surfaces now, painted the same in every state."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    from ui.components.cards import StaticCard
    from ui.panels.conditions_live_panel import ConditionsLivePanel
    from ui.panels.live_monitoring import LiveMonitoringPanel
    from ui.theme.tokens import Spacing
    live = LiveMonitoringPanel()
    live.set_preview_only(True)
    live.resize(1300, 800)
    live.show()
    qapp.processEvents()
    panel = live.conditions_panel
    preview_card = live.preview.parentWidget()
    assert isinstance(panel, StaticCard) and isinstance(preview_card, StaticCard)
    assert isinstance(panel, ConditionsLivePanel)
    assert panel.borderRadius == preview_card.borderRadius

    def fill_and_edge(card):
        image = card.grab().toImage()
        y = card.height() - 6
        return image.pixelColor(card.width() // 2, y).name(), image.pixelColor(1, y).name()

    rest = fill_and_edge(panel)
    assert rest == fill_and_edge(preview_card)
    centre = QPointF(panel.width() / 2, panel.height() - 6)
    qapp.sendEvent(panel, QEnterEvent(centre, centre, centre))
    QTest.qWait(250)
    assert fill_and_edge(panel) == rest

    # Same padding and the same header type as the card beside it.
    margins = preview_card.layout().contentsMargins()
    assert margins.left() == margins.top() == Spacing.card_padding
    assert panel.header.layout().contentsMargins().left() == Spacing.card_padding
    preview_title = next(w for w in preview_card.findChildren(type(panel.header.title_label))
                         if w.text() == "Preview")
    assert type(preview_title) is type(panel.header.title_label)
    title_y = panel.header.title_label.mapTo(live, QPoint(0, 0)).y()
    assert abs(title_y - preview_title.mapTo(live, QPoint(0, 0)).y()) <= 3
    _dispose(qapp, live)


def test_imaging_tile_is_a_link_and_other_tiles_are_not(qapp):
    from ui.components.status_strip import StatusStrip
    strip = StatusStrip()
    strip.resize(1400, 52)
    strip.show()
    hits = []
    strip.imaging_clicked.connect(lambda: hits.append(1))
    tile = strip.imaging_tile
    assert tile.cursor().shape() == Qt.PointingHandCursor
    assert strip.roof_tile.cursor().shape() != Qt.PointingHandCursor
    QTest.mouseClick(tile.value, Qt.LeftButton, Qt.NoModifier, QPoint(2, 2))
    QTest.mouseClick(strip.roof_tile, Qt.LeftButton)
    assert hits == [1]
    _dispose(qapp, strip)


# --- the page, navigation and the moved cards -----------------------------

class _Config:
    def __init__(self, data):
        self.data = data
        self.saves = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1


def _host(qapp, config):
    from ui.components.nav_rail import NavRail
    from ui.components.status_strip import StatusStrip
    from ui.main_window.navigation import _MainWindowNavigationMixin
    from ui.panels.live_monitoring import LiveMonitoringPanel

    class Host(_MainWindowNavigationMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.config = config
            self.notes = []
            self.nav_rail = NavRail(self)
            self.status_strip = StatusStrip(self)
            self.live_panel = LiveMonitoringPanel(self)
            self.inspector_stack = QStackedWidget(self)
            self.output_panel = _Signals()
            self.processing_panel = _Processing()
            for _ in range(10):
                self.inspector_stack.addWidget(QWidget())
            self._build_nina_page()
            self.nav_rail.section_changed.connect(self._on_nav_changed)
            self._wire_nina_page()

        def save_config(self):
            self.config.save()

        def _on_settings_changed(self):
            self.config.save()

        def _notify(self, message, category='info'):
            self.notes.append((category, message))

    return Host()


class _Signals(QWidget):
    from PySide6.QtCore import Signal
    open_nina_requested = Signal()
    settings_changed = Signal()


class _Processing:
    def __init__(self):
        self.ml_section = _Signals()
        self.settings_changed = self.ml_section.settings_changed


def test_page_is_in_the_nav_and_the_tile_opens_the_live_panel(qapp, monkeypatch):
    import services.posthog_service as ph
    monkeypatch.setattr(ph, 'capture_event', lambda *a, **k: None)
    config = _Config({'output': {}, 'ml_models': {}, 'imaging_conditions': {}})
    host = _host(qapp, config)
    try:
        assert host.nav_rail._buttons['nina'].text() == "NINA && ASCOM"
        host.nav_rail.set_section('nina')
        assert host.inspector_stack.currentWidget() is host.nina_panel
        host.live_panel.set_conditions_open(False)
        host.status_strip.imaging_clicked.emit()
        assert host.nav_rail._current_section == 'monitoring'
        assert host.inspector_stack.isHidden()
        assert not host.live_panel.conditions_panel.isHidden()
        assert config.data['live_conditions_panel_open'] is True
        host.live_panel.conditions_panel.settings_requested.emit()
        assert host.inspector_stack.currentWidget() is host.nina_panel
        host.output_panel.open_nina_requested.emit()
        host.processing_panel.ml_section.open_nina_requested.emit()
        assert host.nav_rail._current_section == 'nina'
    finally:
        _dispose(qapp, host)


def ml_block(config):
    return config.data['ml_models']['alpaca_safety_monitor']


def test_moved_cards_load_and_save_the_same_keys(qapp, monkeypatch):
    import services.posthog_service as ph
    monkeypatch.setattr(ph, 'capture_event', lambda *a, **k: None)
    config = _Config({
        'output': {'webserver_enabled': False, 'webserver_control_enabled': True,
                   'api_token': 'tok'},
        'ml_models': {'enabled': True, 'alpaca_safety_monitor': {'enabled': True, 'port': 11112},
                      'ascom_safety_file': {'enabled': False, 'file_path': 'C:/r.txt'}},
        'imaging_conditions': {'bad_frames': 2, 'good_frames': 5, 'max_cloud_cover_pct': 30,
                               'min_star_ratio': 0.6, 'star_drop_pct': 25, 'fwhm_rise_pct': 30},
    })
    host = _host(qapp, config)
    try:
        page = host.nina_panel
        page.load_from_config(config)
        safety = page.safety_card
        assert safety.alpaca_safety_switch.is_checked() is True
        assert "11112" in safety.alpaca_safety_info.text()
        assert safety.ascom_file_path.text() == 'C:/r.txt'
        assert safety.ml_off_label.isHidden()
        assert page.control_card.control_token_input.text() == 'tok'
        assert not page.control_card.web_off_label.isHidden()
        assert page.conditions_card.sensitivity() == 'strict'

        safety.ascom_safety_switch.set_checked(True)
        safety.alpaca_safety_switch.set_checked(False)
        safety.ascom_file_path.setText('D:/roof.txt')
        safety.ascom_file_path.editingFinished.emit()
        ml = config.data['ml_models']
        assert ml['ascom_safety_file'] == {'enabled': True, 'file_path': 'D:/roof.txt'}
        assert ml['alpaca_safety_monitor'] == {'enabled': False, 'port': 11112}
        assert ml['enabled'] is True

        assert safety.alpaca_source() == 'roof'
        assert "two confident Open frames" in safety.alpaca_safety_info.text()
        safety.alpaca_source_combo.setCurrentIndex(1)
        assert ml_block(config)['verdict_source'] == 'imaging_conditions'
        assert "Imaging Conditions reads OK" in safety.alpaca_safety_info.text()
        page.load_from_config(config)
        assert safety.alpaca_source() == 'imaging_conditions'
    finally:
        _dispose(qapp, host)


def test_image_processing_no_longer_holds_the_moved_cards(qapp):
    from ui.panels.image_processing_ml import ImageProcessingMLSection
    section = ImageProcessingMLSection(None)
    for name in ('conditions_card', 'alpaca_safety_switch', 'ascom_safety_switch'):
        assert not hasattr(section, name), name
    hits = []
    section.open_nina_requested.connect(lambda: hits.append(1))
    section.open_nina_btn.click()
    assert hits == [1]
    _dispose(qapp, section)
