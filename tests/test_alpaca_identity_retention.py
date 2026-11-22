"""Alpaca camera identity and capabilities survive address edits (issue #43, QA).

Retyping the port — even back to the same value, one keystroke at a time —
used to clear the learned UniqueID, so the UI and the next capture fell back to
an address-keyed profile with default settings. A passed Test Connection now
learns the identity, a result for an address edited mid-test is dropped, and
the last-read ranges come back after a ZWO → Alpaca switch (offscreen Qt).
"""
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from services.camera.capabilities import ALPACA_STATIC
from services.camera.camera_source import alpaca_profile_key
from services.config import Config

UID = 'uid-sim-1'
NAME = 'PFR Alpaca Simulator'


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config(tmp_path):
    c = Config(str(tmp_path / "config.json"))
    c.set('capture_mode', 'camera')
    c.set('camera_source', 'alpaca')
    c.set('alpaca_camera', {**c.get('alpaca_camera'), 'host': '127.0.0.1', 'port': 35725,
                            'device_number': 0, 'unique_id': UID, 'name': NAME})
    c.save_camera_profile(NAME, {'gain': 250, 'max_exposure_ms': 2000, 'exposure_ms': 200},
                          serial=f'alpaca:{UID}')
    return c


@pytest.fixture
def panel(qapp, config):
    from PySide6.QtWidgets import QWidget
    from ui.panels.capture_settings import CaptureSettingsPanel
    host = QWidget()
    host.config = config
    p = CaptureSettingsPanel(host)
    p.load_from_config(config)
    yield p
    host.deleteLater()


def _type_port(card, text):
    """Select all and type, one keystroke at a time, as a user does."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    edit = card.port_spin.lineEdit()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, text)


def test_retyping_the_same_port_keeps_the_identity_and_the_tuned_profile(panel, config):
    w = panel.camera_widget
    assert w.gain_spin.value() == 250
    _type_port(w.alpaca_card, '35725')
    cfg = config.get('alpaca_camera')
    assert (cfg['port'], cfg['unique_id'], cfg['name']) == (35725, UID, NAME)
    assert w.gain_spin.value() == 250
    assert w.max_exposure_spin.value() == pytest.approx(2.0)


def test_a_different_address_clears_it_and_going_back_restores_it(panel, config):
    w = panel.camera_widget
    w.alpaca_card.port_spin.setValue(1)
    assert config.get('alpaca_camera')['unique_id'] == ''
    assert w.gain_spin.value() == 100, "an unknown address shows the defaults"
    w.alpaca_card.port_spin.setValue(35725)
    assert config.get('alpaca_camera')['unique_id'] == UID
    assert w.gain_spin.value() == 250


def test_a_discovered_device_is_remembered_at_its_address(panel, config):
    from services.camera.alpaca.alpaca_discovery import AlpacaDeviceInfo
    card = panel.camera_widget.alpaca_card
    card.set_devices([AlpacaDeviceInfo('10.0.0.9', 11111, 1, 'Other', 'uid-other')], "")
    card.device_combo.setCurrentIndex(0)
    card.host_input.setText('127.0.0.1')
    card.port_spin.setValue(35725)
    card.device_spin.setValue(0)
    assert config.get('alpaca_camera')['unique_id'] == UID
    card.host_input.setText('10.0.0.9')
    card.port_spin.setValue(11111)
    card.device_spin.setValue(1)
    assert config.get('alpaca_camera')['unique_id'] == 'uid-other'


def _window(config, panel):
    from ui.main_window.capture import _MainWindowCaptureMixin

    class _Window(_MainWindowCaptureMixin):
        pass

    w = _Window()
    w.__dict__.update(config=config, is_capturing=False, camera_controller=None,
                      app_bar=MagicMock(), capture_panel=panel, _notify=MagicMock(),
                      alpaca_discovery=MagicMock())
    w.alpaca_discovery.test_connection.return_value = True
    return w


PROBE = {'unique_id': UID, 'name': NAME, 'gain_range': (0, 400), 'offset_range': (0, 255),
         'exposure_range_s': (0.0001, 2000.0), 'bayer_pattern': 'RGGB', 'bit_depth': 12}


def test_a_passed_test_learns_the_identity_and_moves_the_address_profile(panel, config):
    w = panel.camera_widget
    w.alpaca_card.port_spin.setValue(1)
    w.alpaca_card.port_spin.setValue(4000)   # never seen: address-keyed
    w.gain_spin.setValue(180)                # creates alpaca:127.0.0.1:4000:0
    address_key = alpaca_profile_key({'host': '127.0.0.1', 'port': 4000, 'device_number': 0})
    assert address_key in config.list_camera_profiles()
    win = _window(config, panel)
    win._on_alpaca_test()
    win._on_alpaca_test_result(True, 'Connected', {**PROBE, 'unique_id': 'uid-new'})
    assert config.get('alpaca_camera')['unique_id'] == 'uid-new'
    assert config.get_camera_profile(NAME, 'alpaca:uid-new')['gain'] == 180
    assert w.gain_spin.value() == 180
    assert w.alpaca_card.test_status.text().startswith('✓')
    win.app_bar.refresh_camera_pill.assert_called()


def test_a_result_for_an_address_edited_mid_test_is_not_applied(panel, config):
    win = _window(config, panel)
    win._on_alpaca_test()
    panel.camera_widget.alpaca_card.host_input.setText('10.0.0.77')
    win._on_alpaca_test_result(True, 'Connected', {**PROBE, 'unique_id': 'uid-wrong'})
    assert config.get('alpaca_camera')['unique_id'] == ''
    assert win._alpaca_verified_target is None
    assert not panel.camera_widget.alpaca_card.test_status.text().startswith('✓')


def test_test_ranges_come_back_after_a_zwo_round_trip(panel, config):
    win = _window(config, panel)
    panel.camera_source_selected.connect(win._on_camera_source_selected)
    win._on_alpaca_test()
    win._on_alpaca_test_result(True, 'Connected', PROBE)
    w = panel.camera_widget
    assert (w.gain_spin.minimum(), w.gain_spin.maximum()) == (0, 400)
    panel.mode_selector.items['zwo'].itemClicked.emit(True)
    assert w.gain_spin.maximum() == 600
    panel.mode_selector.items['alpaca'].itemClicked.emit(True)
    assert (w.gain_spin.minimum(), w.gain_spin.maximum()) == (0, 400)
    assert w.max_exposure_spin.maximum() == pytest.approx(2000.0)


def test_exposure_hint_follows_the_camera_limit(panel):
    w = panel.camera_widget
    from qfluentwidgets import CaptionLabel
    hint = w.exposure_row.findChildren(CaptionLabel)[0]
    w.apply_capabilities(replace(ALPACA_STATIC, exposure_range_s=(0.0001, 2000.0)))
    assert hint.text() == "0.001s to 2000s"
    assert "2000s" in w.exposure_spin.toolTip()
    panel.mode_selector.items['zwo'].itemClicked.emit(True)
    assert hint.text() == "0.001s to 3600s", "ZWO's hint is unchanged"


def test_named_gains_are_shown_by_name(panel):
    from qfluentwidgets import CaptionLabel
    w = panel.camera_widget
    names = ('Low', 'Medium', 'High')
    w.apply_capabilities(replace(ALPACA_STATIC, gain_range=(0, 2)), names)
    w.gain_spin.setValue(1)
    hint = w.gain_row.findChildren(CaptionLabel)[0]
    assert hint.text().startswith("Medium")
    assert "2: High" in w.gain_spin.toolTip()
    w.apply_capabilities(replace(ALPACA_STATIC, gain_range=(0, 600)))
    assert hint.text() == "0 to 600"


def test_session_gain_names_only_in_list_mode():
    from types import SimpleNamespace
    from ui.main_window.alpaca_camera import gain_names_from, session_gain_names
    listed = SimpleNamespace(static_info=SimpleNamespace(gain_mode='list', gains=('a', 'b')))
    ranged = SimpleNamespace(static_info=SimpleNamespace(gain_mode='range', gains=()))
    assert session_gain_names(listed) == ('a', 'b')
    assert session_gain_names(ranged) == ()
    assert session_gain_names(object()) == ()
    assert gain_names_from({'gains': ['x', 'y']}) == ('x', 'y')
    assert gain_names_from({}) == ()


def test_alpaca_edits_write_no_legacy_zwo_mirror(panel, config):
    w = panel.camera_widget
    w.gain_spin.setValue(300)
    w.offset_spin.setValue(30)
    w.auto_exp_switch.set_checked(False)
    from services.config_migrate import LEGACY_TO_PROFILE
    assert not [k for k in LEGACY_TO_PROFILE if config.get(k) is not None]
    assert config.get("zwo_auto_exposure") is False, "capture still reads this one"


def test_a_long_found_label_never_widens_the_card(qapp):
    from PySide6.QtWidgets import QSizePolicy
    from services.camera.alpaca.alpaca_discovery import AlpacaDeviceInfo
    from ui.panels._alpaca_connection_card import AlpacaConnectionCard
    card = AlpacaConnectionCard()
    long_name = "A very long Alpaca camera name from a server " * 3
    device = AlpacaDeviceInfo('192.168.100.200', 41983, 0, long_name, 'u')
    card.set_devices([device], "")
    combo = card.device_combo
    assert combo.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored
    card.resize(500, card.sizeHint().height())
    card.show()
    combo.setCurrentIndex(0)
    qapp.processEvents()
    assert card.minimumSizeHint().width() < 500
    assert combo.itemText(0) == device.label() and combo.currentText() == device.label()
    assert '…' in combo.text() and combo.text().endswith('#0'), "elided in the middle"
    assert device.label() in combo.toolTip()
    card.hide()
    card.deleteLater()
