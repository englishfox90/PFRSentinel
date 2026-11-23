"""imaging_conditions.frame_rules / frame_signals / settings — one frame's
verdict from its metadata: every reason code, cloud cover never invented
without a baseline or an ML class, trend against the recent median, FWHM
against the night's median, and a hand-edited config clamped, not trusted."""
import pytest

from services.config_defaults import DEFAULT_CONFIG
from services.imaging_conditions import frame_rules as rules
from services.imaging_conditions.frame_signals import read_frame_signals
from services.imaging_conditions.settings import auto_stale_after_s, read_settings
from services.sky_evidence import EVIDENCE_KEY

SETTINGS = read_settings({'imaging_conditions': {}})


def clear_meta(**over):
    md = {
        '_ML_RESULTS': {'roof_status': 'Open', 'roof_confidence': 0.95,
                        'sky_condition': 'Clear', 'sky_confidence': 0.9,
                        'stars_visible': True, 'frame_is_static': False},
        EVIDENCE_KEY: {'star_count': 300},
        'FWHM': '3.0', 'STAR_COUNT': '150',
    }
    md.update(over)
    return md


def evaluate(md, *, sun=-30.0, baseline=None, recent=(), fwhm_median=None,
             settings=SETTINGS):
    return rules.evaluate_frame(read_frame_signals(md), settings, sun_alt_deg=sun,
                                baseline=baseline, recent_counts=list(recent),
                                night_fwhm_median=fwhm_median)


def test_clear_dark_open_frame_is_good():
    ev = evaluate(clear_meta(), baseline=300.0, recent=[300] * 10, fwhm_median=3.0)
    assert ev.ok and ev.reasons == []
    assert ev.ratio == 1.0 and ev.trend == 0.0 and ev.cloud_cover_pct == 0.0
    assert ev.cloud_cover_source == rules.SOURCE_STAR_BASELINE


@pytest.mark.parametrize("sun, limit, bad", [(-17.0, -18.0, True), (-18.5, -18.0, False),
                                             (-13.0, -12.0, False), (-11.0, -12.0, True)])
def test_daylight_follows_the_configured_sun_limit(sun, limit, bad):
    settings = read_settings({'imaging_conditions': {'max_sun_alt_deg': limit}})
    assert (rules.DAYLIGHT in evaluate(clear_meta(), sun=sun, settings=settings).reasons) is bad


def test_unknown_sun_never_blocks():
    assert rules.DAYLIGHT not in evaluate(clear_meta(), sun=None).reasons


@pytest.mark.parametrize("roof, conf", [('Closed', 0.99), ('Open', 0.5), ('N/A', None)])
def test_roof_not_confidently_open_is_bad(roof, conf):
    md = clear_meta()
    md['_ML_RESULTS'].update(roof_status=roof, roof_confidence=conf)
    assert rules.ROOF_NOT_OPEN in evaluate(md).reasons


def test_roof_rule_can_be_switched_off_for_a_roofless_rig():
    md = clear_meta(_ML_RESULTS={'roof_status': 'N/A'})
    settings = read_settings({'imaging_conditions': {'require_roof_open': False}})
    assert rules.ROOF_NOT_OPEN not in evaluate(md, settings=settings).reasons


def test_watch_mode_tokens_read_like_ml_results():
    md = {'ROOF_STATUS': 'Open (95%)', 'SKY_CONDITION': 'Partly Cloudy (72%)',
          'STARS_VISIBLE': 'Yes', EVIDENCE_KEY: {'star_count': 120}, 'FWHM': 'N/A'}
    sig = read_frame_signals(md)
    assert (sig.roof_state, sig.roof_confidence) == ('Open', 0.95)
    assert (sig.sky_condition, sig.sky_confidence) == ('Partly Cloudy', 0.72)
    assert sig.stars_visible is True and sig.fwhm_px is None
    ev = evaluate(md)
    assert ev.cloud_cover_source == rules.SOURCE_ML_CLASS
    assert rules.CLOUD_COVER_HIGH in ev.reasons


def test_cloud_cover_is_null_without_baseline_or_ml_class():
    md = clear_meta(_ML_RESULTS={'roof_status': 'Open', 'roof_confidence': 0.9})
    ev = evaluate(md)
    assert ev.cloud_cover_pct is None and ev.cloud_cover_source is None and ev.ratio is None


def test_low_confidence_ml_class_is_not_used_for_cloud_cover():
    md = clear_meta()
    md['_ML_RESULTS']['sky_confidence'] = 0.3
    assert evaluate(md).cloud_cover_pct is None


def test_baseline_ratio_sets_cloud_cover_and_stars_low():
    ev = evaluate(clear_meta(**{EVIDENCE_KEY: {'star_count': 90}}), baseline=300.0)
    assert ev.ratio == 0.3 and ev.cloud_cover_pct == 70.0
    assert {rules.CLOUD_COVER_HIGH, rules.STARS_LOW} <= set(ev.reasons)


def test_ratio_above_one_reads_zero_cloud():
    assert evaluate(clear_meta(**{EVIDENCE_KEY: {'star_count': 450}}),
                    baseline=300.0).cloud_cover_pct == 0.0


@pytest.mark.parametrize("count", [0, 5, 40, 3000])
def test_cold_start_never_judges_a_count_on_its_own(count):
    # No shipped star numbers: until this rig's baseline is learned, any count
    # (even 0) passes the star-ratio rule; only relative rules may fire.
    ev = evaluate(clear_meta(**{EVIDENCE_KEY: {'star_count': count}}))
    assert rules.STARS_LOW not in ev.reasons
    assert ev.ratio is None and ev.cloud_cover_source == rules.SOURCE_ML_CLASS


def test_ml_no_stars_without_a_count_is_stars_low():
    md = clear_meta(**{EVIDENCE_KEY: {'star_count': None}})
    md['_ML_RESULTS']['stars_visible'] = False
    assert rules.STARS_LOW in evaluate(md).reasons


def test_nothing_to_judge_the_sky_by_is_sky_unknown():
    md = {'_ML_RESULTS': {'roof_status': 'Open', 'roof_confidence': 0.9}}
    assert evaluate(md).reasons == [rules.SKY_UNKNOWN]


def test_static_frame_is_bad_and_not_sky_unknown():
    md = {'_ML_RESULTS': {'roof_status': 'Open', 'roof_confidence': 0.9,
                          'frame_is_static': True}}
    assert evaluate(md).reasons == [rules.FRAME_STATIC]


def test_trend_against_the_recent_median():
    recent = [300, 310, 290, 305, 295]
    ev = evaluate(clear_meta(**{EVIDENCE_KEY: {'star_count': 120}}), recent=recent)
    assert ev.trend == pytest.approx((120 - 300) / 300, abs=1e-3)
    assert ev.change_pct == -60.0
    assert rules.STARS_DROPPING in ev.reasons
    # 200 of 300 is a 33 % drop: under the default 40 %.
    assert rules.STARS_DROPPING not in evaluate(
        clear_meta(**{EVIDENCE_KEY: {'star_count': 200}}), recent=recent).reasons


def test_star_drop_threshold_is_a_configurable_percent():
    settings = read_settings({'imaging_conditions': {'star_drop_pct': 25}})
    ev = evaluate(clear_meta(**{EVIDENCE_KEY: {'star_count': 200}}), recent=[300] * 5,
                  settings=settings)
    assert ev.change_pct == pytest.approx(-33.3) and rules.STARS_DROPPING in ev.reasons


def test_trend_needs_a_few_earlier_frames():
    assert evaluate(clear_meta(), recent=[300, 300]).trend is None


def test_trend_uses_only_the_last_trend_frames():
    settings = read_settings({'imaging_conditions': {'trend_frames': 3}})
    ev = evaluate(clear_meta(**{EVIDENCE_KEY: {'star_count': 100}}),
                  recent=[1000] * 20 + [100, 100, 100], settings=settings)
    assert ev.trend == 0.0


def test_fwhm_high_against_the_night_median():
    md = clear_meta(FWHM='5.0')
    ev = evaluate(md, fwhm_median=3.0)
    assert ev.fwhm_change_pct == pytest.approx(66.7) and rules.FWHM_HIGH in ev.reasons
    assert rules.FWHM_HIGH not in evaluate(md, fwhm_median=4.0).reasons   # +25 %
    assert evaluate(md, fwhm_median=None).fwhm_change_pct is None
    settings = read_settings({'imaging_conditions': {'fwhm_rise_pct': 20}})
    assert rules.FWHM_HIGH in evaluate(md, fwhm_median=4.0, settings=settings).reasons


def test_fwhm_skipped_with_too_few_detections():
    assert evaluate(clear_meta(STAR_COUNT='4')).fwhm_px is None
    assert evaluate(clear_meta(FWHM='N/A')).fwhm_px is None


def test_baseline_frames_need_dark_open_clear_and_a_count():
    sig = read_frame_signals(clear_meta())
    assert rules.is_baseline_frame(sig, SETTINGS, -20.0)
    assert not rules.is_baseline_frame(sig, SETTINGS, -15.0)   # nautical: never
    assert not rules.is_baseline_frame(sig, SETTINGS, None)
    cloudy = clear_meta()
    cloudy['_ML_RESULTS']['sky_condition'] = 'Partly Cloudy'
    assert not rules.is_baseline_frame(read_frame_signals(cloudy), SETTINGS, -30.0)
    static = clear_meta()
    static['_ML_RESULTS']['frame_is_static'] = True
    assert not rules.is_baseline_frame(read_frame_signals(static), SETTINGS, -30.0)


def test_settings_defaults_match_config_defaults():
    block = DEFAULT_CONFIG['imaging_conditions']
    s = read_settings({})
    assert s.enabled is True and s.bad_frames == block['bad_frames'] == 3
    assert s.good_frames == 3 and s.max_sun_alt_deg == -18.0 and s.stale_after_s == 0


@pytest.mark.parametrize("raw, expected", [('garbage', 3), (None, 3), (True, 3),
                                           (-5, 1), (999, 20), (float('nan'), 3)])
def test_settings_clamp_hand_edits(raw, expected):
    assert read_settings({'imaging_conditions': {'bad_frames': raw}}).bad_frames == expected


def test_settings_survive_a_non_dict_block():
    assert read_settings({'imaging_conditions': 'oops'}).enabled is True


def test_auto_staleness_is_three_intervals_at_least_120s():
    assert auto_stale_after_s(None) == 120
    assert auto_stale_after_s(10) == 120
    assert auto_stale_after_s(60) == 180
    assert auto_stale_after_s(10_000) == 3600


def test_old_config_without_the_block_gets_the_defaults(tmp_path):
    import json
    from services.config import Config
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'output_directory': str(tmp_path),
                                'imaging_conditions': {'bad_frames': 5}}))
    block = Config(str(path)).get('imaging_conditions')
    assert block['bad_frames'] == 5
    assert block['good_frames'] == DEFAULT_CONFIG['imaging_conditions']['good_frames']
    assert read_settings(Config(str(path))).bad_frames == 5


def test_ml_class_cloud_map_matches_the_sky_model_classes():
    from ml.sky_classifier import SKY_CONDITIONS
    assert set(rules.ML_CLASS_CLOUD_PCT) == set(SKY_CONDITIONS)
    assert set(rules.CLEAR_CLASSES) <= set(SKY_CONDITIONS)
