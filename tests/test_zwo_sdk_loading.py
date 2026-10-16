"""The SDK locator as its callers use it: SDK init, and config repair (issue #39)."""
import json
import sys
import types

import pytest

from services import zwo_sdk_library as sdk
from services.camera import linux_usb_preflight
from services.camera.camera_connection import CameraConnection
from services.config import Config


@pytest.fixture
def fake_zwoasi(monkeypatch):
    """Stand-in zwoasi module that records what init() was handed."""
    module = types.ModuleType('zwoasi')
    module.init_calls = []
    module.init = module.init_calls.append
    monkeypatch.setitem(sys.modules, 'zwoasi', module)
    monkeypatch.setattr(linux_usb_preflight, '_already_logged', True)
    return module


def _connection(sdk_path, lines):
    return CameraConnection(sdk_path=sdk_path, logger=lines.append)


class TestInitializeSdk:
    def test_loads_the_configured_library_by_absolute_path(self, fake_zwoasi, tmp_path):
        library = tmp_path / 'libASICamera2.so'
        library.write_bytes(b'')
        assert _connection(str(library), []).initialize_sdk() is True
        assert fake_zwoasi.init_calls == [str(library)]

    def test_stale_configured_path_falls_back_to_a_found_library(self, fake_zwoasi, monkeypatch, tmp_path):
        found = tmp_path / 'found.lib'
        found.write_bytes(b'')
        monkeypatch.setattr(sdk, 'find_library', lambda: str(found))
        lines = []
        assert _connection(str(tmp_path / 'gone' / 'ASICamera2.dll'), lines).initialize_sdk() is True
        assert fake_zwoasi.init_calls == [str(found)]
        assert any('not found' in line and str(found) in line for line in lines)

    def test_no_library_anywhere_fails_without_calling_init(self, fake_zwoasi, monkeypatch):
        monkeypatch.setattr(sdk, 'find_library', lambda: None)
        lines = []
        assert _connection(None, lines).initialize_sdk() is False
        assert fake_zwoasi.init_calls == []
        assert any(sdk.library_name() in line for line in lines)

    def test_never_hands_zwoasi_a_bare_filename(self, fake_zwoasi, monkeypatch, tmp_path):
        # A bare name only resolves through the OS loader's search path, which
        # off Windows never includes the app folder.
        monkeypatch.chdir(tmp_path)
        (tmp_path / sdk.library_name()).write_bytes(b'')
        assert _connection(sdk.library_name(), []).initialize_sdk() is True
        assert fake_zwoasi.init_calls == [str(tmp_path / sdk.library_name())]


class TestCameraIndexResolver:
    """Start Capture enumerates through this before CameraConnection ever runs."""

    def test_stale_configured_path_falls_back_to_a_found_library(self, fake_zwoasi, monkeypatch, tmp_path):
        from services.camera import camera_index_resolver as resolver
        found = tmp_path / 'found.lib'
        found.write_bytes(b'')
        monkeypatch.setattr(sdk, 'find_library', lambda: str(found))
        fake_zwoasi.get_num_cameras = lambda: 1
        fake_zwoasi.list_cameras = lambda: ['ZWO ASI676MC']
        names = resolver._list_camera_names(str(tmp_path / 'gone' / sdk.library_name()))
        assert names == [(0, 'ZWO ASI676MC')]
        assert fake_zwoasi.init_calls == [str(found)]

    def test_no_library_anywhere_names_the_library(self, fake_zwoasi, monkeypatch):
        from services.camera import camera_index_resolver as resolver
        monkeypatch.setattr(sdk, 'find_library', lambda: None)
        with pytest.raises(Exception, match='ASICamera2'):
            resolver._list_camera_names('')
        assert fake_zwoasi.init_calls == []


class TestConfigRepairsForeignSdkPath:
    def _config_with(self, temp_config, zwo_sdk_path):
        with open(temp_config, 'w') as handle:
            json.dump({'zwo_sdk_path': zwo_sdk_path}, handle)
        return Config(temp_config)

    def test_another_platforms_library_is_replaced_by_a_found_one(self, temp_config, monkeypatch):
        monkeypatch.setattr(sdk, 'names_another_platforms_library', lambda path: True)
        monkeypatch.setattr(sdk, 'find_library', lambda: '/found/library')
        config = self._config_with(temp_config, '/old/ASICamera2.dll')
        assert config.get('zwo_sdk_path') == '/found/library'
        with open(temp_config) as handle:
            assert json.load(handle)['zwo_sdk_path'] == '/found/library'

    def test_left_alone_when_nothing_is_found(self, temp_config, monkeypatch):
        monkeypatch.setattr(sdk, 'names_another_platforms_library', lambda path: True)
        monkeypatch.setattr(sdk, 'find_library', lambda: None)
        config = self._config_with(temp_config, '/old/ASICamera2.dll')
        assert config.get('zwo_sdk_path') == '/old/ASICamera2.dll'

    def test_missing_path_with_this_platforms_name_is_never_rewritten(self, temp_config, monkeypatch):
        monkeypatch.setattr(sdk, 'find_library', lambda: '/found/library')
        unmounted = f'/mnt/not-mounted-yet/{sdk.library_name()}'
        config = self._config_with(temp_config, unmounted)
        assert config.get('zwo_sdk_path') == unmounted
