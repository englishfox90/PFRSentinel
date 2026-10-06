"""target_status_log — one INFO line per change in what the overlay does with
the NINA target.

PR #143 follow-up: a received target that was not drawn (below the horizon,
off the image, no field of view, gone stale) left nothing in Sentinel's log,
so "received but not drawn" read the same as "never received".
"""
import pytest
from PIL import Image

from services.allsky import overlay_renderer, target_status_log
from services.allsky.render_target import TargetPlacement
from services.logger import app_logger
from services.nina_target_store import NinaTarget, NinaTargetStore

BOX = ((10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0))


def _placement(polygon=BOX, why=''):
    return TargetPlacement(name='M31', x=15.0, y=15.0, marker_r=10.0, label_px=13,
                           fov_polygon=polygon, reticle_reason=why)


@pytest.fixture
def lines(monkeypatch):
    target_status_log.forget()
    out = []
    monkeypatch.setattr(app_logger, 'info', lambda msg, *a, **k: out.append(str(msg)))
    yield out
    target_status_log.forget()


def test_a_drawn_box_is_logged_once_not_every_frame(lines):
    for _ in range(5):
        target_status_log.report_placement('M31', _placement(), '')
    assert lines == ["NINA target 'M31' drawn on the all-sky overlay with its field-of-view box"]


def test_a_reticle_names_why_there_is_no_box(lines):
    target_status_log.report_placement('M31', _placement(None, 'NINA sent no field of view'), '')
    assert lines == ["NINA target 'M31' drawn on the all-sky overlay as a reticle, "
                     "without a box: NINA sent no field of view"]


def test_not_drawn_names_why(lines):
    target_status_log.report_placement('M31', None, 'it is below the horizon')
    assert lines == ["NINA target 'M31' not drawn on the all-sky overlay: it is below the horizon"]


def test_each_change_is_logged_and_a_return_is_logged_again(lines):
    target_status_log.report_placement('M31', _placement(), '')
    target_status_log.report_placement('M31', None, 'it is below the horizon')
    target_status_log.report_placement('M31', None, 'it is below the horizon')
    target_status_log.report_placement('M31', _placement(), '')
    assert len(lines) == 3


def test_a_new_target_is_logged_even_in_the_same_state(lines):
    target_status_log.report_placement('M31', _placement(), '')
    target_status_log.report_placement('M42', _placement(), '')
    assert [line.split("'")[1] for line in lines] == ['M31', 'M42']


def test_stale_is_logged_once_while_the_age_grows(lines):
    for age in (125.0, 140.0, 300.0):
        target_status_log.report_stale('M31', age, 120.0)
    assert lines == ["NINA target 'M31' hidden: NINA has not reported it for over 120 s "
                     "(125 s since the last one)"]


def test_forget_lets_the_same_outcome_be_logged_again(lines):
    target_status_log.report_placement('M31', _placement(), '')
    target_status_log.forget()
    target_status_log.report_placement('M31', _placement(), '')
    assert len(lines) == 2


# --- wiring -----------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def preview(monkeypatch, tmp_path):
    """The preview path up to the render, over a store with a settable clock."""
    import services.app_config
    import services.observing_window
    clock = _Clock()
    store = NinaTargetStore(monotonic=clock)
    monkeypatch.setattr(overlay_renderer, 'get_nina_target_store', lambda: store)
    monkeypatch.setattr(overlay_renderer, 'render_allsky_overlay', lambda img, cfg, md: img)
    monkeypatch.setattr(overlay_renderer, 'overlay_withheld', lambda: False)
    monkeypatch.setattr(services.observing_window, 'is_observing_window',
                        lambda *a, **kw: True)
    monkeypatch.setattr(services.app_config, 'get_obstruction_map_path',
                        lambda: str(tmp_path / 'map.npz'))

    def run():
        overlay_renderer.render_allsky_for_preview(
            Image.new('RGB', (32, 32)), {'enabled': True, 'calibration_file': 'c.json'},
            {'weather': {}}, {})
    return store, clock, run


def test_the_preview_path_logs_a_target_going_stale_once(lines, preview):
    store, clock, run = preview
    store.set(NinaTarget(name='M31', ra_deg=10.68, dec_deg=41.27))
    run()
    assert lines == []
    clock.t += 121.0
    run()
    clock.t += 30.0
    run()
    assert len(lines) == 1 and lines[0].startswith("NINA target 'M31' hidden:")


def test_a_cleared_target_logs_nothing_here(lines, preview):
    """web_target already logs the clear at INFO."""
    store, clock, run = preview
    store.set(NinaTarget(name='M31', ra_deg=10.68, dec_deg=41.27))
    store.set(None)
    run()
    assert lines == []


def test_the_renderer_reports_the_placement(lines, monkeypatch, tmp_path):
    from tests.test_allsky_render_target import _model, _overlay_config, _target
    cal = str(tmp_path / 'cal.json')
    _model().save(cal)
    overlay_renderer.render_allsky_overlay(
        Image.new('RGBA', (750, 750)), _overlay_config(cal, _target()), {})
    overlay_renderer.render_allsky_overlay(
        Image.new('RGBA', (750, 750)), _overlay_config(cal, _target(dec_deg=-60.0)), {})
    assert [line for line in lines if 'NINA target' in line] == [
        "NINA target 'M31' drawn on the all-sky overlay with its field-of-view box",
        "NINA target 'M31' not drawn on the all-sky overlay: it is below the horizon",
    ]
