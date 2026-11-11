"""
Hardware white balance (ASI_WB_R / ASI_WB_B) — reported 2026-11-09.

An ASI662MC went green after an off-peak reconnect. Its profile held
wb_r=1, wb_b=1 (the camera scale is 1-99, ~50 neutral). A fresh start never
wrote them: the controller didn't pass ``wb_mode``, so the camera ran as
'asi_auto', which only set ASI_AUTO_MAX_BRIGHTNESS. A live settings change
then switched the instance to 'manual', and the next reconnect wrote R=1, B=1.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.camera.camera_white_balance import (
    WB_LOW_WARNING, configure_white_balance, low_gain_warning,
)

ASI_WB_R, ASI_WB_B, ASI_AUTO_MAX_BRIGHTNESS = 3, 4, 12


def _asi():
    return SimpleNamespace(ASI_WB_R=ASI_WB_R, ASI_WB_B=ASI_WB_B,
                           ASI_AUTO_MAX_BRIGHTNESS=ASI_AUTO_MAX_BRIGHTNESS)


def _controls(r_default=52, b_default=95):
    return {
        'WB_R': {'ControlType': ASI_WB_R, 'MinValue': 1, 'MaxValue': 99, 'DefaultValue': r_default},
        'WB_B': {'ControlType': ASI_WB_B, 'MinValue': 1, 'MaxValue': 99, 'DefaultValue': b_default},
    }


def _writes(camera):
    return {c.args[0]: (c.args[1], c.kwargs.get('auto')) for c in camera.set_control_value.call_args_list}


def test_manual_writes_both_gains_with_auto_off():
    cam, logs = MagicMock(), []
    configure_white_balance(cam, _asi(), {'wb_mode': 'manual', 'wb_r': 73, 'wb_b': 86},
                            logs.append, controls=_controls())
    assert _writes(cam) == {ASI_WB_R: (73, False), ASI_WB_B: (86, False)}
    assert not any('⚠' in line for line in logs)


def test_manual_gains_are_clamped_to_the_camera_range():
    cam = MagicMock()
    configure_white_balance(cam, _asi(), {'wb_mode': 'manual', 'wb_r': 0, 'wb_b': 150},
                            lambda _: None, controls=_controls())
    assert _writes(cam) == {ASI_WB_R: (1, False), ASI_WB_B: (99, False)}


def test_manual_low_gains_log_a_warning():
    cam, logs = MagicMock(), []
    configure_white_balance(cam, _asi(), {'wb_mode': 'manual', 'wb_r': 1, 'wb_b': 1},
                            logs.append, controls=_controls())
    assert any('⚠' in line and 'green' in line for line in logs)


def test_asi_auto_writes_camera_defaults_and_never_the_exposure_target():
    cam, logs = MagicMock(), []
    configure_white_balance(cam, _asi(), {'wb_mode': 'asi_auto', 'wb_r': 1, 'wb_b': 1},
                            logs.append, controls=_controls(52, 95))
    assert _writes(cam) == {ASI_WB_R: (52, True), ASI_WB_B: (95, True)}
    assert ASI_AUTO_MAX_BRIGHTNESS not in _writes(cam)


def test_asi_auto_without_control_info_starts_neutral():
    cam = MagicMock()
    configure_white_balance(cam, _asi(), {}, lambda _: None, controls={})
    assert _writes(cam) == {ASI_WB_R: (50, True), ASI_WB_B: (50, True)}


def test_asi_auto_rejected_still_leaves_known_gains():
    cam = MagicMock()

    def set_control_value(control, value, auto=False):
        if auto:
            raise RuntimeError("auto not supported")
    cam.set_control_value.side_effect = set_control_value
    configure_white_balance(cam, _asi(), {'wb_mode': 'asi_auto'}, lambda _: None,
                            controls=_controls(52, 95))
    final = {c.args[0]: c.args[1] for c in cam.set_control_value.call_args_list
             if not c.kwargs.get('auto')}
    assert final == {ASI_WB_R: 52, ASI_WB_B: 95}


def test_gray_world_sets_neutral_hardware_gains():
    cam = MagicMock()
    configure_white_balance(cam, _asi(), {'wb_mode': 'gray_world', 'wb_r': 1, 'wb_b': 1},
                            lambda _: None, controls=_controls())
    assert _writes(cam) == {ASI_WB_R: (50, False), ASI_WB_B: (50, False)}


@pytest.mark.parametrize("r, b, expect", [
    (50, 50, None), (WB_LOW_WARNING, WB_LOW_WARNING, None),
    (1, 50, 'red'), (50, 9, 'blue'), (1, 1, 'red and blue'),
])
def test_low_gain_warning(r, b, expect):
    warning = low_gain_warning(r, b)
    if expect is None:
        assert warning is None
    else:
        assert f"{expect} below" in warning


# --------------------------------------------------------------------------- #
#  ZWOCamera keeps what a reconnect will write in step with the profile        #
# --------------------------------------------------------------------------- #

def _live_camera(mode='manual', r=1, b=1):
    from services.camera.zwo_camera import ZWOCamera
    cam = ZWOCamera(white_balance_r=r, white_balance_b=b, wb_mode=mode)
    cam._connection.camera = MagicMock()
    cam._connection.camera.get_controls.return_value = _controls()
    cam._connection.asi = _asi()
    return cam


def test_live_white_balance_reaches_the_camera_and_the_reconnect_settings():
    cam = _live_camera()
    cam.set_white_balance_live('manual', 50, 60)
    assert _writes(cam.camera) == {ASI_WB_R: (50, False), ASI_WB_B: (60, False)}
    assert (cam.wb_mode, cam.white_balance_r, cam.white_balance_b) == ('manual', 50, 60)


def test_unchanged_live_white_balance_is_not_rewritten():
    cam = _live_camera(r=50, b=60)
    cam.set_white_balance_live('manual', 50, 60)
    cam.camera.set_control_value.assert_not_called()


def test_live_white_balance_waits_its_turn_on_the_sdk_lock():
    cam = _live_camera()
    lock = cam._connection.sdk_lock
    seen = []
    cam.camera.set_control_value.side_effect = lambda *a, **k: seen.append(lock.locked())
    cam.set_white_balance_live('manual', 50, 60)
    assert seen and all(seen)


def test_apply_camera_settings_pushes_the_profile_white_balance():
    from ui.controllers.camera_settings import apply_camera_settings
    zwo = MagicMock(camera_name='ZWO ASI662MC', exposure_seconds=1.0, flip=0,
                    calibration_manager=None)
    config = MagicMock()
    values = {
        'zwo_selected_camera_name': 'ZWO ASI662MC',
        'white_balance': {'mode': 'manual'},
    }
    config.get.side_effect = lambda k, d=None: values.get(k, d)
    config.get_camera_profile.return_value = {'wb_r': 50, 'wb_b': 60}
    apply_camera_settings(zwo, config)
    zwo.set_white_balance_live.assert_called_once_with('manual', 50, 60)


def test_capture_start_hands_the_configured_mode_to_the_camera(monkeypatch):
    import ui.controllers.camera_controller as cc
    seen = {}

    def fake_camera(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after construction")
    monkeypatch.setattr(cc, 'ZWOCamera', fake_camera)

    params = {k: 0 for k in (
        'exposure_ms', 'gain', 'max_exposure_ms', 'offset', 'flip', 'capture_interval',
        'scheduled_window_interval', 'target_brightness')}
    params.update(sdk_path='', saved_camera_index=0, camera_name='ZWO ASI662MC',
                  clean_camera_name='ZWO ASI662MC', camera_serial='', wb_r=50, wb_b=60,
                  bayer_pattern='BGGR', auto_exposure=True,
                  wb_config={'mode': 'manual'}, scheduled_capture_mode='always',
                  scheduled_start_time='17:00', scheduled_end_time='09:00')
    fake_self = SimpleNamespace(
        _dying_camera=None, _resolve_camera_index=lambda *a: 0,
        _capture_start_done=MagicMock(), config=MagicMock())
    cc.CameraControllerQt._start_capture_worker(fake_self, params)

    assert seen['wb_mode'] == 'manual'
    assert seen['wb_config'] == {'mode': 'manual'}


def test_manual_sliders_warn_about_low_gains():
    from PySide6.QtWidgets import QApplication
    from ui.panels._camera_settings_widget import CameraSettingsWidget
    QApplication.instance() or QApplication([])
    main_window = MagicMock()
    main_window.config.get.side_effect = lambda k, d=None: d
    widget = CameraSettingsWidget(main_window)

    widget.wb_r_slider.setValue(1)
    widget.wb_b_slider.setValue(1)
    assert not widget.wb_low_warning.isHidden()
    assert 'green' in widget.wb_low_warning.text()

    widget.wb_r_slider.setValue(50)
    widget.wb_b_slider.setValue(50)
    assert widget.wb_low_warning.isHidden()
