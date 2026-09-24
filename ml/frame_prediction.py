#!/usr/bin/env python3
"""Run the local roof / sky classifiers on one sample, and describe the result.

Qt-free: the labeling tab shows the text, the batch-confirm worker only needs
the predictions.
"""
from pathlib import Path

from services.logger import app_logger
from .label_suggestion import to_bool, suggest_labels

MODELS_DIR = Path(__file__).parent / 'models'


def load_classifiers():
    """(roof_classifier, sky_classifier); either is None when it cannot be loaded."""
    roof = sky = None
    try:
        from .roof_classifier import RoofClassifier
        path = MODELS_DIR / 'roof_classifier_v1.pth'
        if path.exists():
            roof = RoofClassifier.load(str(path), image_size=128)
            app_logger.info(f"Loaded roof classifier from {path}")
    except Exception as e:
        app_logger.warning(f"Failed to load roof model: {e}")
    try:
        from .sky_classifier import SkyClassifier
        path = MODELS_DIR / 'sky_classifier_v1.pth'
        if path.exists():
            sky = SkyClassifier.load(str(path), image_size=256)
            app_logger.info(f"Loaded sky classifier from {path}")
    except Exception as e:
        app_logger.warning(f"Failed to load sky model: {e}")
    return roof, sky


def sky_metadata(cal: dict):
    if not cal:
        return None
    tc = cal.get('time_context', {})
    ca = cal.get('corner_analysis', {})
    mc = cal.get('moon_context', {})
    st = cal.get('stretch', {})
    return {
        'corner_to_center_ratio': ca.get('corner_to_center_ratio', 1.0),
        'median_lum': st.get('median_lum', 0.0),
        'is_astronomical_night': tc.get('is_astronomical_night', False),
        'hour': tc.get('hour', 12),
        'moon_illumination': mc.get('illumination_pct', 0.0),
        'moon_is_up': mc.get('moon_is_up', False),
    }


def predict_frame(roof_clf, sky_clf, sample: dict, cal: dict) -> dict:
    """{'roof', 'sky', 'roof_error', 'sky_error'} — predictions are None when unavailable.

    The sky model runs when the best roof call (NINA, then AI, then the roof
    model) is open — not on the roof model alone, which is the weakest of the three.
    """
    out = {'roof': None, 'sky': None, 'roof_error': None, 'sky_error': None}
    if 'lum' not in sample:
        return out
    if roof_clf:
        try:
            out['roof'] = roof_clf.predict_from_fits(sample['lum'])
        except Exception as e:
            out['roof_error'] = str(e)
    if sky_clf and suggest_labels(cal or {}, out['roof'])['roof_open']:
        try:
            out['sky'] = sky_clf.predict_from_fits(sample['lum'], sky_metadata(cal))
        except Exception as e:
            out['sky_error'] = str(e)
    return out


def _bar(fraction: float, filled: str = "█", empty: str = "░", width: int = 10) -> str:
    n = int(fraction * width)
    return filled * n + empty * (width - n)


def _tick(match: bool) -> str:
    return '✓' if match else '✗'


def describe_prediction(roof_clf, sky_clf, sample: dict, cal: dict, pred: dict) -> str:
    if not roof_clf and not sky_clf:
        return ("⚠️ No ML models loaded\n\nTo train models:\n"
                "  python ml/train_roof_classifier.py\n"
                "  python ml/train_sky_classifier.py")
    if 'lum' not in sample:
        return "⚠️ No FITS image available for prediction"

    cal = cal or {}
    labels = cal.get('labels', {})
    labeled = bool(labels.get('labeled_at'))
    lines = []

    if roof_clf:
        lines.append("━━━ ROOF CLASSIFIER ━━━")
        roof = pred['roof']
        if roof is None:
            lines.append(f"⚠️ Error: {pred['roof_error']}")
        else:
            lines.append(f"State:      {'🟢 OPEN' if roof.roof_open else '🔴 CLOSED'}")
            lines.append(f"Confidence: [{_bar(roof.confidence)}] {roof.confidence:.1%}")
            rs = cal.get('roof_state', {})
            if rs.get('available'):
                lines.append(f"vs API:     {_tick(to_bool(rs.get('roof_open', False)) == roof.roof_open)}")
            if labeled:
                lines.append(f"vs Label:   {_tick(to_bool(labels.get('roof_open', False)) == roof.roof_open)}")

    if sky_clf:
        lines += ["", "━━━ SKY CLASSIFIER ━━━"]
        sky = pred['sky']
        if pred['sky_error']:
            lines.append(f"⚠️ Error: {pred['sky_error']}")
        elif sky is None:
            lines += ["⛔ N/A - Roof is CLOSED", "   (Pier camera cannot see sky)"]
        else:
            lines.append(f"Sky:     {sky.sky_condition} ({sky.sky_confidence:.0%})")
            for cond, prob in sorted(sky.sky_probabilities.items(), key=lambda x: -x[1])[:3]:
                marker = "◄" if cond == sky.sky_condition else " "
                lines.append(f"  [{_bar(prob)}] {prob:5.1%} {cond[:12]:<12}{marker}")
            lines.append(f"Stars:   {'⭐' if sky.stars_visible else '  '} "
                         f"{'Yes' if sky.stars_visible else 'No'} ({sky.stars_confidence:.0%})")
            if sky.stars_visible:
                lines.append(f"         Density: [{_bar(sky.star_density, '★', '☆', 5)}] {sky.star_density:.2f}")
            lines.append(f"Moon:    {'🌙' if sky.moon_visible else '  '} "
                         f"{'Yes' if sky.moon_visible else 'No'} ({sky.moon_confidence:.0%})")
            if labeled:
                lines += ["", "vs Manual Labels:"]
                lbl_sky = labels.get('sky_condition', '')
                if lbl_sky:
                    lines.append(f"  Sky:   {_tick(lbl_sky == sky.sky_condition)} (label: {lbl_sky})")
                lines.append(f"  Stars: {_tick(to_bool(labels.get('stars_visible', False)) == sky.stars_visible)}")
                lines.append(f"  Moon:  {_tick(to_bool(labels.get('moon_visible', False)) == sky.moon_visible)}")

    return "\n".join(lines)
