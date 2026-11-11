"""
Hardware white balance (ASI_WB_R / ASI_WB_B) for ZWO colour cameras.

The camera applies these gains to the Bayer data before Sentinel debayers it,
so a bad value turns every frame one colour no matter what the software does
afterwards. The scale is 1-99 with roughly 50 neutral, not a 1.0x multiplier:
R=1, B=1 removes almost all red and blue and the frame comes out green
(reported 2026-11-09 on an ASI662MC).

Every mode writes both gains on every connect. 'ASI Auto' used to write nothing
(it set ASI_AUTO_MAX_BRIGHTNESS, the auto-exposure target), so the camera kept
whatever gains another program, or an earlier manual write, had left.
"""
from typing import Any, Dict, Optional

# Below this a channel is all but switched off. Not a hard floor (the SDK
# accepts 1), only the point where the log warns.
WB_LOW_WARNING = 10

_NEUTRAL = 50


def _control(controls: Optional[Dict], control_type) -> Optional[Dict]:
    if not controls:
        return None
    return next((c for c in controls.values() if c.get('ControlType') == control_type), None)


def _clamp(ctrl: Optional[Dict], value: int) -> int:
    if ctrl is None:
        return int(value)
    return int(max(ctrl['MinValue'], min(ctrl['MaxValue'], value)))


def _default(ctrl: Optional[Dict]) -> int:
    if ctrl is None or ctrl.get('DefaultValue') is None:
        return _NEUTRAL
    return int(ctrl['DefaultValue'])


def low_gain_warning(wb_r: int, wb_b: int) -> Optional[str]:
    """Plain-language warning for gains that will tint the frame, or None."""
    low = [name for name, v in (("red", wb_r), ("blue", wb_b)) if v < WB_LOW_WARNING]
    if not low:
        return None
    return (
        f"Manual white balance {' and '.join(low)} below {WB_LOW_WARNING} "
        f"(R={wb_r}, B={wb_b}) — the camera scale is 1-99 with about 50 neutral, "
        f"so frames will look {'green' if len(low) == 2 else 'tinted'}"
    )


def configure_white_balance(camera, asi, settings: Dict[str, Any], log,
                            controls: Optional[Dict] = None) -> None:
    """Write ASI_WB_R / ASI_WB_B for the configured white-balance mode."""
    wb_mode = settings.get('wb_mode', 'asi_auto')
    ctrl_r = _control(controls, asi.ASI_WB_R)
    ctrl_b = _control(controls, asi.ASI_WB_B)

    if wb_mode == 'manual':
        wb_r = _clamp(ctrl_r, settings.get('wb_r', 75))
        wb_b = _clamp(ctrl_b, settings.get('wb_b', 99))
        camera.set_control_value(asi.ASI_WB_R, wb_r, auto=False)
        camera.set_control_value(asi.ASI_WB_B, wb_b, auto=False)
        log(f"  White balance: Manual (R={wb_r}, B={wb_b})")
        warning = low_gain_warning(wb_r, wb_b)
        if warning:
            log(f"  ⚠ {warning}")
    elif wb_mode == 'gray_world':
        camera.set_control_value(asi.ASI_WB_R, _NEUTRAL, auto=False)
        camera.set_control_value(asi.ASI_WB_B, _NEUTRAL, auto=False)
        log("  White balance: Gray World (software)")
    else:
        # Start from the camera's own defaults and hand the gains to the SDK's
        # auto loop. Where the SDK doesn't run auto WB for single exposures,
        # the defaults are what stays — never a stale value from elsewhere.
        wb_r, wb_b = _default(ctrl_r), _default(ctrl_b)
        try:
            camera.set_control_value(asi.ASI_WB_R, wb_r, auto=True)
            camera.set_control_value(asi.ASI_WB_B, wb_b, auto=True)
            log(f"  White balance: ASI Auto (starting from R={wb_r}, B={wb_b})")
        except Exception as e:
            log(f"  White balance: ASI Auto not accepted ({e}); using R={wb_r}, B={wb_b}")
            camera.set_control_value(asi.ASI_WB_R, wb_r, auto=False)
            camera.set_control_value(asi.ASI_WB_B, wb_b, auto=False)
