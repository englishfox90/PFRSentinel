"""`apply_capabilities` on the camera settings widget: ZWO vs Alpaca (offscreen Qt).

ZWO capabilities keep every ZWO widget as it always was; Alpaca hides the SDK,
camera picker, Revive and RAW16, shows the Alpaca card, filters the WB modes and
shows the camera's Bayer pattern read-only. Nothing here may write config.
"""
import json
from dataclasses import replace

import pytest

from services.camera.capabilities import ALPACA_STATIC, ZWO_STATIC
from services.config import Config


@pytest.fixture
def widget_and_config(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QWidget
    QApplication.instance() or QApplication([])
    from ui.panels._camera_settings_widget import CameraSettingsWidget
    config = Config(str(tmp_path / "config.json"))
    host = QWidget()
    host.config = config
    widget = CameraSettingsWidget(host)
    yield widget, config
    host.deleteLater()


def _wb_items(widget):
    return [widget.wb_mode_combo.itemText(i) for i in range(widget.wb_mode_combo.count())]


def _snapshot(config):
    return json.dumps(config.data, sort_keys=True, default=str)


def test_zwo_shows_sdk_picker_raw16_and_hardware_wb(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'zwo')
    w.load_from_config(config)
    assert not w.zwo_card.isHidden()
    assert not w.zwo_card.sdk_row.isHidden() and not w.zwo_card.camera_row.isHidden()
    assert w.alpaca_card.isHidden()
    assert not w.raw16_switch.isHidden()
    assert _wb_items(w) == ['asi_auto', 'manual', 'gray_world']
    assert not w.hw_wb_settings.isHidden() and w.sw_wb_settings.isHidden()
    assert w.bayer_combo.isEnabled()
    assert (w.gain_spin.minimum(), w.gain_spin.maximum()) == (0, 600)
    assert (w.offset_spin.minimum(), w.offset_spin.maximum()) == (0, 255)
    assert w.max_exposure_spin.maximum() == 3600.0


def test_missing_camera_notice_with_revive_is_zwo_only(widget_and_config):
    w, config = widget_and_config
    w.set_missing_camera_warning('ZWO ASI676MC', phantom_count=1)
    assert not w._missing_notice.isHidden()
    w.apply_capabilities(ALPACA_STATIC)
    assert w._missing_notice.isHidden()
    assert w.zwo_card.isHidden()


def test_alpaca_hides_zwo_widgets_and_shows_its_card(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'alpaca')
    w.load_from_config(config)
    assert w.zwo_card.isHidden()
    assert not w.alpaca_card.isHidden()
    assert w.raw16_switch.isHidden()
    assert 'connects' in w.raw16_status.text()
    assert w.hw_wb_settings.isHidden() and not w.sw_wb_settings.isHidden()
    assert not w.bayer_combo.isEnabled()


def test_stored_asi_auto_reads_as_gray_world_without_writing(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'alpaca')
    wb = dict(config.get('white_balance'))
    wb['mode'] = 'asi_auto'
    config.set('white_balance', wb)
    before = _snapshot(config)
    w.load_from_config(config)
    assert _wb_items(w) == ['manual', 'gray_world']
    assert w.wb_mode_combo.currentText() == 'gray_world'
    assert not w.gray_world_settings.isHidden()
    assert _snapshot(config) == before
    assert config.get('white_balance')['mode'] == 'asi_auto'


def test_switching_back_to_zwo_restores_asi_auto(widget_and_config):
    w, config = widget_and_config
    wb = dict(config.get('white_balance'))
    wb['mode'] = 'asi_auto'
    config.set('white_balance', wb)
    config.set('camera_source', 'alpaca')
    w.load_from_config(config)
    config.set('camera_source', 'zwo')
    w.load_from_config(config)
    assert _wb_items(w) == ['asi_auto', 'manual', 'gray_world']
    assert w.wb_mode_combo.currentText() == 'asi_auto'
    assert config.get('white_balance')['mode'] == 'asi_auto'


def test_connected_alpaca_shows_detected_bayer_read_only(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'alpaca')
    w.load_from_config(config)
    before = _snapshot(config)
    w.apply_capabilities(replace(ALPACA_STATIC, bayer_pattern='RGGB', bit_depth=12,
                                 gain_range=(0, 100), offset_range=(0, 50),
                                 exposure_range_s=(0.001, 600.0)))
    assert w.bayer_combo.currentText() == 'RGGB'
    assert not w.bayer_combo.isEnabled()
    assert 'Reported by the camera' in w.bayer_combo.toolTip()
    assert (w.gain_spin.minimum(), w.gain_spin.maximum()) == (0, 100)
    assert w.offset_spin.maximum() == 50
    assert w.max_exposure_spin.maximum() == 600.0
    assert '12-bit' in w.raw16_status.text()
    assert _snapshot(config) == before


def test_mono_alpaca_camera_shows_no_bayer_pattern(widget_and_config):
    w, _config = widget_and_config
    w.apply_capabilities(replace(ALPACA_STATIC, bayer_pattern='MONO', bit_depth=8))
    assert w.bayer_combo.currentIndex() == -1
    assert '8-bit' in w.raw16_status.text()


def test_unsupported_offset_and_gain_are_hidden_or_disabled(widget_and_config):
    w, _config = widget_and_config
    w.apply_capabilities(replace(ALPACA_STATIC, offset=False, gain=False))
    assert w.offset_row.isHidden()
    assert not w.gain_spin.isEnabled()


def test_switching_back_restores_zwo_exactly(widget_and_config):
    w, config = widget_and_config
    config.set('zwo_selected_camera_name', 'ZWO ASI676MC')
    config.update_camera_profile('ZWO ASI676MC', bayer_pattern='GBRG')
    w.load_from_config(config)
    gain_hint = w.gain_row.findChildren(type(w.raw16_status))[0].text()
    w.apply_capabilities(replace(ALPACA_STATIC, bayer_pattern='GRBG', bit_depth=16,
                                 gain_range=(0, 50), offset_range=(0, 10),
                                 exposure_range_s=(0.001, 10.0)))
    w.apply_capabilities(ZWO_STATIC)
    assert not w.zwo_card.isHidden() and w.alpaca_card.isHidden()
    assert not w.raw16_switch.isHidden()
    assert w.raw16_status.text() == "Connect camera to check RAW16 support"
    assert w.bayer_combo.isEnabled()
    assert w.bayer_combo.currentText() == 'GBRG'
    assert (w.gain_spin.minimum(), w.gain_spin.maximum()) == (0, 600)
    assert w.offset_spin.maximum() == 255
    assert w.max_exposure_spin.maximum() == 3600.0
    assert w.gain_row.findChildren(type(w.raw16_status))[0].text() == gain_hint == "0 to 600"
    assert not w.hw_wb_settings.isHidden()


def test_a_value_the_last_camera_clamped_reloads_in_full(widget_and_config):
    w, config = widget_and_config
    config.set('zwo_selected_camera_name', 'ZWO ASI676MC')
    config.update_camera_profile('ZWO ASI676MC', gain=450)
    w.apply_capabilities(replace(ALPACA_STATIC, gain_range=(0, 100)))
    config.set('camera_source', 'zwo')
    w.load_from_config(config)
    assert w.gain_spin.value() == 450


def test_alpaca_settings_save_to_the_alpaca_profile(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'alpaca')
    config.set('alpaca_camera', {**config.get('alpaca_camera'), 'host': '10.0.0.5',
                                 'unique_id': 'abc-123', 'name': 'Sim'})
    w.load_from_config(config)
    w.gain_spin.setValue(77)
    assert config.get_camera_profile('Sim', 'alpaca:abc-123')['gain'] == 77
    assert config.get('zwo_selected_camera_name', '') == ''


def test_software_wb_gains_write_white_balance(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'alpaca')
    w.load_from_config(config)
    w.sw_red_gain_spin.setValue(1.25)
    w.sw_blue_gain_spin.setValue(0.8)
    wb = config.get('white_balance')
    assert wb['manual_red_gain'] == 1.25 and wb['manual_blue_gain'] == 0.8


def test_stop_resets_alpaca_to_its_static_capabilities(widget_and_config):
    w, config = widget_and_config
    config.set('camera_source', 'alpaca')
    w.load_from_config(config)
    w.apply_capabilities(replace(ALPACA_STATIC, bayer_pattern='RGGB', bit_depth=12))
    w.reset_camera_capabilities()
    assert w._caps == ALPACA_STATIC
    assert w.raw16_switch.isHidden()
    assert 'connects' in w.raw16_status.text()
