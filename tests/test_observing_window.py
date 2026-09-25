"""
Test the sky-observation gate in services/observing_window.py.

Covers the ml_models.roof_gates_sky_features opt-out (GitHub issue #10):
misfiring roof classifiers on roofless all-sky rigs should not have to
disable ML entirely to keep star detection / all-sky calibration alive.
"""
import os
import sys

import pytest

# Ensure project root is in path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.config import DEFAULT_CONFIG
from services.observing_window import (
    ROOF_CLOSED_CONFIRM_FRAMES, SAME_CAPTURE_KEY, is_observing_window,
    reset_roof_gate)


@pytest.fixture(autouse=True)
def _fresh_roof_gate():
    reset_roof_gate()
    yield
    reset_roof_gate()


def _confirmed(config, status='Closed (98%)'):
    """Feed enough consecutive frames with `status` to confirm it; return the
    gate's verdict on the last one. Each frame carries its own metadata."""
    verdict = None
    for _ in range(ROOF_CLOSED_CONFIRM_FRAMES):
        verdict = is_observing_window(config, {'ROOF_STATUS': status}, feature="test")
    return verdict


def _config(ml_models=None, weather=None):
    """Minimal config dict. No lat/lon by default so the sun gate falls
    through and only the roof gate under test is exercised."""
    cfg = {'weather': weather or {}}
    if ml_models is not None:
        cfg['ml_models'] = ml_models
    return cfg


class TestRoofGateDefaultBehaviour:
    """Default (roof_gates_sky_features unset / True) matches today's behaviour."""

    def test_suppresses_when_roof_closed(self):
        config = _config(ml_models={'enabled': True})

        assert _confirmed(config) is False

    def test_allows_when_roof_open(self):
        config = _config(ml_models={'enabled': True})
        metadata = {'ROOF_STATUS': 'Open (95%)'}

        assert is_observing_window(config, metadata, feature="test") is True

    def test_explicit_true_matches_default(self):
        config = _config(ml_models={'enabled': True, 'roof_gates_sky_features': True})

        assert _confirmed(config) is False


class TestRoofGateOptOut:
    """roof_gates_sky_features: False skips the roof gate for roofless rigs."""

    def test_does_not_suppress_when_flag_disabled(self):
        config = _config(ml_models={'enabled': True, 'roof_gates_sky_features': False})
        metadata = {'ROOF_STATUS': 'Closed (98%)'}

        assert is_observing_window(config, metadata, feature="test") is True

    def test_still_allows_when_roof_open_and_flag_disabled(self):
        config = _config(ml_models={'enabled': True, 'roof_gates_sky_features': False})
        metadata = {'ROOF_STATUS': 'Open (95%)'}

        assert is_observing_window(config, metadata, feature="test") is True

    def test_ml_disabled_also_skips_roof_gate_regardless_of_flag(self):
        config = _config(ml_models={'enabled': False, 'roof_gates_sky_features': True})
        metadata = {'ROOF_STATUS': 'Closed (98%)'}

        assert is_observing_window(config, metadata, feature="test") is True


class TestTwilightGateIndependentOfRoofFlag:
    """The sun/twilight gate must apply regardless of roof_gates_sky_features."""

    def test_twilight_gate_suppresses_with_flag_disabled(self, monkeypatch):
        monkeypatch.setattr('astral.sun.elevation', lambda *a, **kw: 10.0)
        config = _config(
            ml_models={'enabled': True, 'roof_gates_sky_features': False},
            weather={'latitude': '51.5074', 'longitude': '-0.1278'},
        )
        metadata = {'ROOF_STATUS': 'Open (95%)'}

        assert is_observing_window(config, metadata, feature="test") is False

    def test_twilight_gate_suppresses_with_ml_disabled(self, monkeypatch):
        monkeypatch.setattr('astral.sun.elevation', lambda *a, **kw: 10.0)
        config = _config(weather={'latitude': '51.5074', 'longitude': '-0.1278'})
        metadata = {}

        assert is_observing_window(config, metadata, feature="test") is False

    def test_twilight_gate_allows_below_civil_twilight(self, monkeypatch):
        monkeypatch.setattr('astral.sun.elevation', lambda *a, **kw: -20.0)
        config = _config(
            ml_models={'enabled': True, 'roof_gates_sky_features': False},
            weather={'latitude': '51.5074', 'longitude': '-0.1278'},
        )
        metadata = {'ROOF_STATUS': 'Open (95%)'}

        assert is_observing_window(config, metadata, feature="test") is True


class TestResultCaching:
    def test_result_is_cached_on_metadata(self):
        config = _config(ml_models={'enabled': True})
        is_observing_window(config, {'ROOF_STATUS': 'Closed (98%)'}, feature="test")
        metadata = {'ROOF_STATUS': 'Closed (98%)'}   # second frame: confirmed

        first = is_observing_window(config, metadata, feature="test")
        # Flip the underlying status; cached result must not change.
        metadata['ROOF_STATUS'] = 'Open (95%)'
        second = is_observing_window(config, metadata, feature="test")

        assert first is False
        assert second is False


class TestDefaultConfigPreservesBehaviour:
    """DEFAULT_CONFIG must ship roof_gates_sky_features=True so existing
    installs keep suppressing sky features on a Closed roof prediction."""

    def test_default_config_has_flag_enabled(self):
        ml_defaults = DEFAULT_CONFIG.get('ml_models', {})
        assert ml_defaults.get('roof_gates_sky_features') is True

    def test_default_config_roof_closed_suppresses(self):
        config = {
            'weather': dict(DEFAULT_CONFIG['weather']),
            'ml_models': dict(DEFAULT_CONFIG['ml_models']),
        }
        config['ml_models']['enabled'] = True

        assert _confirmed(config) is False


class TestRoofClosedConfirmation:
    """One Closed frame is not a closed roof. The classifier's known failure
    is the unusual frame an exposure change produces, and acting on it blanked
    the whole all-sky overlay for that frame (discussion #76)."""

    CONFIG = {'weather': {}, 'ml_models': {'enabled': True}}

    def _frame(self, status):
        return is_observing_window(self.CONFIG, {'ROOF_STATUS': status}, feature="test")

    def test_a_single_closed_frame_does_not_suppress(self):
        assert self._frame('Open (95%)') is True
        assert self._frame('Closed (71%)') is True
        assert self._frame('Open (96%)') is True

    def test_consecutive_closed_frames_suppress(self):
        verdicts = [self._frame('Closed (98%)')
                    for _ in range(ROOF_CLOSED_CONFIRM_FRAMES + 2)]
        assert verdicts[:ROOF_CLOSED_CONFIRM_FRAMES - 1] == [True] * (ROOF_CLOSED_CONFIRM_FRAMES - 1)
        assert all(v is False for v in verdicts[ROOF_CLOSED_CONFIRM_FRAMES - 1:])

    def test_an_open_frame_restarts_the_count(self):
        for status in ('Closed (98%)', 'Open (90%)', 'Closed (98%)'):
            assert self._frame(status) is True

    def test_reopening_lifts_the_suppression_at_once(self):
        _confirmed(self.CONFIG)
        assert self._frame('Open (95%)') is True

    def test_several_callers_in_one_frame_count_as_one_frame(self):
        """Star detection, the calibration feed and the overlay all ask about
        the same frame; three questions must not look like three frames."""
        metadata = {'ROOF_STATUS': 'Closed (98%)'}
        answers = [is_observing_window(self.CONFIG, metadata, feature=f)
                   for f in ("Star detection", "All-sky calibration", "All-sky overlay")]
        assert answers == [True, True, True]

    def test_frames_with_the_gate_switched_off_do_not_count(self):
        off = {'weather': {}, 'ml_models': {'enabled': True,
                                            'roof_gates_sky_features': False}}
        for _ in range(ROOF_CLOSED_CONFIRM_FRAMES + 1):
            assert is_observing_window(off, {'ROOF_STATUS': 'Closed (98%)'},
                                       feature="test") is True
        assert self._frame('Closed (98%)') is True

    def test_reprocessing_a_capture_does_not_count_it_twice(self):
        """Nudging a setting re-runs the same capture with fresh metadata. One
        misread frame must not be able to confirm itself."""
        assert self._frame('Closed (98%)') is True
        for _ in range(3):
            again = {'ROOF_STATUS': 'Closed (98%)', SAME_CAPTURE_KEY: True}
            assert is_observing_window(self.CONFIG, again, feature="test") is True
        assert self._frame('Open (95%)') is True          # streak never reached 2

    def test_reprocessing_under_a_confirmed_closure_stays_suppressed(self):
        _confirmed(self.CONFIG)
        again = {'ROOF_STATUS': 'Closed (98%)', SAME_CAPTURE_KEY: True}
        assert is_observing_window(self.CONFIG, again, feature="test") is False

    def test_a_reprocess_cannot_clear_the_streak_either(self):
        """Whatever the re-run reads, the count belongs to real captures."""
        assert self._frame('Closed (98%)') is True
        again = {'ROOF_STATUS': 'Open (95%)', SAME_CAPTURE_KEY: True}
        assert is_observing_window(self.CONFIG, again, feature="test") is True
        assert self._frame('Closed (98%)') is False       # second real Closed frame

    def test_a_caller_with_no_roof_verdict_neither_counts_nor_resets(self):
        """Watch mode asks twice per frame: the processor with the ML verdict,
        then the overlay renderer with a fresh dict that never saw ML. The
        second question is not an 'Open' reading."""
        blind = lambda: is_observing_window(self.CONFIG, {}, feature="All-sky overlay")

        assert self._frame('Closed (98%)') is True     # frame 1, processor
        assert blind() is True                          # frame 1, overlay
        assert self._frame('Closed (98%)') is False    # frame 2: confirmed...
        assert blind() is False                         # ...and the overlay follows

    def test_an_explicit_na_from_ml_still_clears_the_count(self):
        """ML ran and had no answer: that is a reading, and it is not Closed."""
        assert self._frame('Closed (98%)') is True
        assert self._frame('N/A') is True
        assert self._frame('Closed (98%)') is True

    def test_no_verdict_and_nothing_confirmed_allows(self):
        assert is_observing_window(self.CONFIG, {}, feature="test") is True


# ---------------------------------------------------------------------------
# Issue #93: the roof verdict corroborated by what the frame contains.
# ---------------------------------------------------------------------------

from services.ascom_safety_fsm import RoofSafetyFSM  # noqa: E402
from services.observing_window import (  # noqa: E402
    NO_STARS_CONFIRM_FRAMES, RECOVERY_CONFIRM_FRAMES, RECOVERY_STAR_FACTOR,
    REASON_KEY)
from services.sky_evidence import EVIDENCE_KEY  # noqa: E402

FLOOR = 15
GATE = {'weather': {}, 'allsky_overlay': {'min_exposure_s': 0.5, 'min_star_detections': FLOOR}}
GATE_ML = {**GATE, 'ml_models': {'enabled': True}}


def _judge(config, star_count=None, exposure=None, roof=None, stars_visible=None,
           static=False, reprocess=False, evidence=True):
    """One frame through the gate; returns (verdict, reason, metadata)."""
    metadata = {}
    if exposure is not None:
        metadata['EXPOSURE'] = exposure
    if roof is not None:
        metadata['ROOF_STATUS'] = roof
    ml = {}
    if stars_visible is not None:
        ml['stars_visible'] = stars_visible
    if static:
        ml['frame_is_static'] = True
    if ml:
        metadata['_ML_RESULTS'] = ml
    if evidence:
        metadata[EVIDENCE_KEY] = {'star_count': star_count, 'sky_circle': None,
                                  'frame_size': (100, 100)}
    if reprocess:
        metadata[SAME_CAPTURE_KEY] = True
    verdict = is_observing_window(config, metadata, feature="test")
    return verdict, metadata[REASON_KEY], metadata


class TestExposureFloor:
    def test_a_lit_roof_at_60ms_is_not_a_sky(self):
        verdict, reason, _ = _judge(GATE, star_count=40, exposure='0.06s')
        assert verdict is False and reason == 'exposure'

    def test_ten_seconds_passes(self):
        assert _judge(GATE, star_count=40, exposure='10.0s')[0] is True

    def test_no_exposure_passes(self):
        verdict, reason, _ = _judge(GATE, star_count=40)
        assert verdict is True and reason == ''

    def test_zero_disables_the_floor(self):
        off = {**GATE, 'allsky_overlay': {'min_exposure_s': 0, 'min_star_detections': FLOOR}}
        assert _judge(off, star_count=40, exposure='0.06s')[0] is True

    def test_exposure_read_from_the_evidence_when_present(self):
        metadata = {'EXPOSURE': '10s', EVIDENCE_KEY: {'star_count': 40, 'exposure_s': 0.008}}
        assert is_observing_window(GATE, metadata, feature="test") is False
        assert metadata[REASON_KEY] == 'exposure'

    def test_short_frames_leave_the_roof_streak_alone(self):
        for _ in range(3):
            assert _judge(GATE_ML, exposure='0.06s', roof='Closed (98%)')[1] == 'exposure'
        # The streak never counted those: this is the first Closed frame.
        assert _judge(GATE_ML, star_count=40, exposure='10s', roof='Closed (98%)')[0] is True


class TestStaticFrame:
    def test_sensor_noise_only_is_not_a_sky_whatever_the_count(self):
        verdict, reason, _ = _judge(GATE_ML, star_count=200, exposure='10s',
                                    roof='Open (95%)', static=True)
        assert verdict is False and reason == 'static'

    def test_static_comes_before_the_exposure_floor(self):
        assert _judge(GATE, exposure='0.06s', static=True)[1] == 'static'


class TestNoStarsRule:
    def _no_stars(self, config, n, **kw):
        return [_judge(config, star_count=3, exposure='10s', **kw) for _ in range(n)]

    def test_open_roof_with_no_stars_on_three_frames_blocks_two_does_not(self):
        frames = self._no_stars(GATE_ML, NO_STARS_CONFIRM_FRAMES, roof='Open (95%)')
        assert [f[0] for f in frames[:-1]] == [True] * (NO_STARS_CONFIRM_FRAMES - 1)
        assert frames[-1][0] is False and frames[-1][1] == 'no_stars'

    def test_ml_off_with_no_stars_blocks(self):
        frames = self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES)
        assert frames[-1][0] is False and frames[-1][1] == 'no_stars'

    def test_a_starry_frame_restarts_the_count(self):
        self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES - 1)
        assert _judge(GATE, star_count=FLOOR, exposure='10s')[0] is True
        assert self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES - 1)[-1][0] is True

    def test_ml_saying_stars_are_visible_vetoes_a_low_count(self):
        frames = self._no_stars(GATE_ML, NO_STARS_CONFIRM_FRAMES + 1,
                                roof='Open (95%)', stars_visible=True)
        assert all(f[0] is True for f in frames)

    def test_recovery_needs_two_frames_at_twice_the_floor(self):
        self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES)
        strong = RECOVERY_STAR_FACTOR * FLOOR
        assert _judge(GATE, star_count=strong - 1, exposure='10s')[0] is False   # not strong
        assert _judge(GATE, star_count=strong, exposure='10s')[0] is False       # 1 of 2
        assert _judge(GATE, star_count=strong - 1, exposure='10s')[0] is False   # resets
        for i in range(RECOVERY_CONFIRM_FRAMES):
            verdict, reason, _ = _judge(GATE, star_count=strong, exposure='10s')
        assert verdict is True and reason == ''

    def test_a_closed_roof_still_blocks_with_plenty_of_stars(self):
        for _ in range(ROOF_CLOSED_CONFIRM_FRAMES):
            verdict, reason, _ = _judge(GATE_ML, star_count=200, exposure='10s',
                                        roof='Closed (98%)', stars_visible=True)
        assert verdict is False and reason == 'roof'

    def test_a_zero_floor_never_blocks(self):
        off = {**GATE, 'allsky_overlay': {'min_exposure_s': 0.5, 'min_star_detections': 0}}
        frames = [_judge(off, star_count=0, exposure='10s') for _ in range(5)]
        assert all(f[0] is True for f in frames)

    def test_a_caller_without_evidence_neither_counts_nor_resets(self):
        self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES - 1)
        assert _judge(GATE, evidence=False)[0] is True                    # blind call
        assert _judge(GATE, star_count=None, exposure='10s')[0] is True   # unmeasured
        assert self._no_stars(GATE, 1)[-1][0] is False                    # third real miss

    def test_a_reprocess_neither_counts_nor_clears(self):
        self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES - 1)
        for _ in range(3):
            assert _judge(GATE, star_count=3, exposure='10s', reprocess=True)[0] is True
        assert _judge(GATE, star_count=200, exposure='10s', reprocess=True)[0] is True
        assert self._no_stars(GATE, 1)[-1][0] is False

    def test_under_a_block_the_overlay_and_a_reprocess_follow_it(self):
        self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES)
        assert _judge(GATE, evidence=False)[0] is False
        verdict, reason, _ = _judge(GATE, star_count=200, exposure='10s', reprocess=True)
        assert verdict is False and reason == 'no_stars'

    def test_the_verdict_and_reason_are_cached_together(self):
        self._no_stars(GATE, NO_STARS_CONFIRM_FRAMES)
        _, _, metadata = _judge(GATE, star_count=3, exposure='10s')
        metadata[EVIDENCE_KEY]['star_count'] = 500
        assert is_observing_window(GATE, metadata, feature="overlay") is False
        assert metadata[REASON_KEY] == 'no_stars'


class TestTransitionLogging:
    def test_one_info_line_per_transition_never_per_frame(self, monkeypatch):
        from services import observing_window
        lines = []
        monkeypatch.setattr(observing_window.app_logger, 'info', lambda msg: lines.append(msg))
        for _ in range(NO_STARS_CONFIRM_FRAMES + 3):
            _judge(GATE, star_count=3, exposure='10s')
        assert len(lines) == 1 and 'no stars' in lines[0]
        for _ in range(RECOVERY_CONFIRM_FRAMES + 2):
            _judge(GATE, star_count=200, exposure='10s')
        assert len(lines) == 2 and 'resumed' in lines[1]
        _judge(GATE, star_count=200, exposure='0.06s')
        assert len(lines) == 3 and 'exposure' in lines[2]


class TestConsumersUnaffected:
    """Rule 5 governs the overlay, calibration feed and star analysis only.
    The roof verdict the ASCOM safety file and the meteor gate read is left
    exactly as ML wrote it."""

    def test_no_stars_leaves_the_ml_results_untouched(self):
        ml = {'roof_status': 'Open', 'roof_confidence': 0.95, 'stars_visible': False}
        snapshot = dict(ml)
        for _ in range(NO_STARS_CONFIRM_FRAMES):
            metadata = {'ROOF_STATUS': 'Open (95%)', 'EXPOSURE': '10s', '_ML_RESULTS': ml,
                        EVIDENCE_KEY: {'star_count': 0}}
            verdict = is_observing_window(GATE_ML, metadata, feature="test")
        assert verdict is False and metadata[REASON_KEY] == 'no_stars'
        assert metadata['_ML_RESULTS'] is ml and ml == snapshot
        assert metadata['ROOF_STATUS'] == 'Open (95%)'

    def test_the_ascom_safety_file_still_certifies_safe_from_the_same_verdict(self):
        writes = []
        fsm = RoofSafetyFSM(writer=lambda ml, cfg: (writes.append(dict(ml)), True)[1])
        ml = {'roof_status': 'Open', 'roof_confidence': 0.95}
        cfg = {'enabled': True, 'min_confidence': 0.7, 'heartbeat_seconds': 0}
        for _ in range(NO_STARS_CONFIRM_FRAMES):
            metadata = {'ROOF_STATUS': 'Open (95%)', '_ML_RESULTS': ml,
                        EVIDENCE_KEY: {'star_count': 0}}
            assert is_observing_window(GATE_ML, metadata, feature="test") in (True, False)
            fsm.update(metadata['_ML_RESULTS'], cfg)
        assert metadata[REASON_KEY] == 'no_stars'
        assert fsm._confirmed_safe is True
        assert writes[-1]['roof_status'] == 'Open'

    def test_the_meteor_gate_reads_the_roof_not_the_stars(self):
        # ui/controllers/meteor_controller.py: suspended only when the roof
        # is known and not Open. A no-stars block never changes that reading.
        ml = {'roof_status': 'Open'}
        for _ in range(NO_STARS_CONFIRM_FRAMES):
            metadata = {'ROOF_STATUS': 'Open (95%)', '_ML_RESULTS': ml,
                        EVIDENCE_KEY: {'star_count': 0}}
            is_observing_window(GATE_ML, metadata, feature="test")
        assert metadata[REASON_KEY] == 'no_stars'
        roof_status = metadata['_ML_RESULTS'].get('roof_status')
        assert not (roof_status is not None and roof_status != 'Open')
