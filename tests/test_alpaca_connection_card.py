"""AlpacaConnectionCard and its config wiring in the settings widget (offscreen Qt)."""
import pytest

from services.camera.alpaca.alpaca_discovery import AlpacaDeviceInfo
from services.config import Config


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def card(qapp):
    from ui.panels._alpaca_connection_card import AlpacaConnectionCard
    c = AlpacaConnectionCard()
    yield c
    c.deleteLater()


@pytest.fixture
def widget_and_config(qapp, tmp_path):
    from PySide6.QtWidgets import QWidget
    from ui.panels._camera_settings_widget import CameraSettingsWidget
    config = Config(str(tmp_path / "config.json"))
    config.set('camera_source', 'alpaca')
    config.set('alpaca_camera', {**config.get('alpaca_camera'), 'host': '10.0.0.5',
                                 'unique_id': 'abc', 'name': 'Old camera'})
    host = QWidget()
    host.config = config
    widget = CameraSettingsWidget(host)
    widget.load_from_config(config)
    yield widget, config
    host.deleteLater()


DEVICES = [AlpacaDeviceInfo('192.168.1.20', 11111, 0, 'ZWO ASI676MC', 'uid-1'),
           AlpacaDeviceInfo('192.168.1.21', 32323, 2, 'Simulator', 'uid-2')]


def test_ranges_and_defaults(card):
    assert (card.port_spin.minimum(), card.port_spin.maximum()) == (1, 65535)
    assert (card.device_spin.minimum(), card.device_spin.maximum()) == (0, 99)
    assert card.values() == {'host': '', 'port': 11111, 'device_number': 0}


def test_load_shows_settings_without_emitting(card):
    seen = []
    card.connection_edited.connect(seen.append)
    card.load({'host': 'obs-pc', 'port': 4567, 'device_number': 3})
    assert card.values() == {'host': 'obs-pc', 'port': 4567, 'device_number': 3}
    assert seen == []


def test_editing_emits_the_typed_address(card):
    seen = []
    card.connection_edited.connect(seen.append)
    card.host_input.setText('obs-pc')
    card.port_spin.setValue(5555)
    card.device_spin.setValue(1)
    assert seen[-1] == {'host': 'obs-pc', 'port': 5555, 'device_number': 1}


def test_discovered_devices_are_listed_by_label_and_none_picked(card):
    picked = []
    card.device_picked.connect(picked.append)
    card.set_devices(DEVICES, "")
    combo = card.device_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [d.label() for d in DEVICES]
    assert combo.currentIndex() == -1
    assert picked == []


def test_picking_a_device_fills_the_address_and_brings_its_identity(card):
    picked, edited = [], []
    card.device_picked.connect(picked.append)
    card.connection_edited.connect(edited.append)
    card.set_devices(DEVICES, "")
    card.device_combo.setCurrentIndex(1)
    assert card.values() == {'host': '192.168.1.21', 'port': 32323, 'device_number': 2}
    assert picked == [{'host': '192.168.1.21', 'port': 32323, 'device_number': 2,
                       'unique_id': 'uid-2', 'name': 'Simulator'}]
    assert edited == [], "a pick is one signal, not three edits"


def test_discovery_error_and_empty_result_say_so(card):
    card.set_devices([], "socket refused")
    assert card.device_combo.count() == 0
    assert 'failed' in card.device_combo.text().lower()
    card.set_devices([], "")
    assert 'none' in card.device_combo.text().lower()


def test_discover_and_test_buttons_emit_and_show_progress(card):
    clicks = []
    card.discover_clicked.connect(lambda: clicks.append('discover'))
    card.test_clicked.connect(lambda: clicks.append('test'))
    card.discover_btn.click()
    card.test_btn.click()
    assert clicks == ['discover', 'test']
    card.set_discovering(True)
    assert not card.discover_btn.isEnabled()
    card.set_discovering(False)
    assert card.discover_btn.isEnabled()
    card.set_testing(True)
    assert not card.test_btn.isEnabled()


def test_test_result_caption(card):
    card.set_test_result(True, "ok", {'name': 'Sim', 'width': 800, 'height': 600,
                                      'bit_depth': 16, 'bayer_pattern': 'BGGR'})
    assert card.test_status.text().startswith('✓ Sim')
    assert '800 × 600' in card.test_status.text() and 'colour' in card.test_status.text()
    card.set_test_result(False, "No Camera device #0 on that server", {})
    assert card.test_status.text() == '✗ No Camera device #0 on that server'


def test_capture_locks_the_card(card):
    card.set_capture_active(True)
    assert not card.host_input.isEnabled() and not card.test_btn.isEnabled()
    card.set_capture_active(False)
    assert card.host_input.isEnabled()


def test_typed_address_clears_the_learned_identity(widget_and_config):
    w, config = widget_and_config
    w.alpaca_card.host_input.setText('10.0.0.6')
    cfg = config.get('alpaca_camera')
    assert cfg['host'] == '10.0.0.6'
    assert cfg['unique_id'] == '' and cfg['name'] == ''
    assert cfg['use_imagebytes'] is True, "other alpaca_camera keys are kept"


def test_typed_address_creates_no_profile_per_keystroke(widget_and_config):
    w, config = widget_and_config
    before = set(config.list_camera_profiles())
    for text in ('1', '19', '192', '192.168.1.9'):
        w.alpaca_card.host_input.setText(text)
    assert set(config.list_camera_profiles()) == before


def test_picked_device_writes_its_identity(widget_and_config):
    w, config = widget_and_config
    w.alpaca_card.set_devices(DEVICES, "")
    w.alpaca_card.device_combo.setCurrentIndex(0)
    cfg = config.get('alpaca_camera')
    assert (cfg['host'], cfg['port'], cfg['device_number']) == ('192.168.1.20', 11111, 0)
    assert (cfg['unique_id'], cfg['name']) == ('uid-1', 'ZWO ASI676MC')


def test_loading_never_writes_alpaca_camera(widget_and_config):
    w, config = widget_and_config
    before = dict(config.get('alpaca_camera'))
    w.load_from_config(config)
    assert config.get('alpaca_camera') == before


def test_long_failure_is_shortened_with_the_whole_text_in_the_tooltip(card):
    message = "Cannot reach Alpaca server " + "x" * 400
    card.set_test_result(False, message, {})
    assert len(card.test_status.text()) <= 142
    assert card.test_status.toolTip() == message
