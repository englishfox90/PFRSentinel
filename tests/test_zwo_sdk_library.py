"""Tests for services/zwo_sdk_library.py — per-platform ZWO SDK discovery (issue #39)."""
import os

import pytest

from services import host_platform, zwo_sdk_library as sdk

LIBRARY_BYTES = b'x' * sdk.MIN_LIBRARY_BYTES


def _set_platform(monkeypatch, *, windows=False, macos=False):
    for module in (host_platform, sdk):
        monkeypatch.setattr(module, 'IS_WINDOWS', windows)
        monkeypatch.setattr(module, 'IS_MACOS', macos)
    monkeypatch.setattr(host_platform, 'IS_LINUX', not windows and not macos)


@pytest.fixture
def isolated_search(monkeypatch, tmp_path):
    """Search only three tmp folders: bundled, per-user and one system dir."""
    bundled, user, system = (tmp_path / n for n in ('bundled', 'user', 'system'))
    for folder in (bundled, user, system):
        folder.mkdir()
    monkeypatch.setattr(sdk, 'resource_path', lambda rel: str(bundled / rel))
    monkeypatch.setattr(sdk, 'get_app_data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(sdk, 'USER_SDK_SUBFOLDER', 'user')
    monkeypatch.setattr(sdk, '_system_library_dirs', lambda: [str(system)])
    monkeypatch.delattr(sdk.sys, 'frozen', raising=False)
    return bundled, user, system


class TestLibraryName:
    @pytest.mark.parametrize('flags, expected', [
        ({'windows': True}, 'ASICamera2.dll'),
        ({'macos': True}, 'libASICamera2.dylib'),
        ({}, 'libASICamera2.so'),
    ])
    def test_name_per_platform(self, monkeypatch, flags, expected):
        _set_platform(monkeypatch, **flags)
        assert sdk.library_name() == expected
        assert expected in sdk.ALL_LIBRARY_NAMES


class TestSearchDirs:
    def test_order_is_bundled_then_user_then_system(self, monkeypatch, isolated_search):
        bundled, user, system = isolated_search
        assert sdk.search_dirs() == [str(bundled), str(user), str(system)]

    def test_frozen_build_also_searches_beside_the_executable(self, monkeypatch, isolated_search, tmp_path):
        exe = tmp_path / 'app' / 'PFRSentinel'
        monkeypatch.setattr(sdk.sys, 'frozen', True, raising=False)
        monkeypatch.setattr(sdk.sys, 'executable', str(exe))
        dirs = sdk.search_dirs()
        assert str(exe.parent) in dirs
        assert os.path.join(str(exe.parent), '_internal') in dirs

    def test_windows_system_dirs_are_the_install_folders(self, monkeypatch):
        _set_platform(monkeypatch, windows=True)
        monkeypatch.setenv('PROGRAMFILES', r'C:\PF')
        monkeypatch.setenv('PROGRAMFILES(X86)', r'C:\PF86')
        assert sdk._system_library_dirs() == [
            os.path.join(r'C:\PF86', 'PFRSentinel', '_internal'),
            os.path.join(r'C:\PF', 'PFRSentinel', '_internal'),
        ]

    def test_windows_without_program_files_env_adds_no_relative_dirs(self, monkeypatch):
        _set_platform(monkeypatch, windows=True)
        monkeypatch.delenv('PROGRAMFILES', raising=False)
        monkeypatch.delenv('PROGRAMFILES(X86)', raising=False)
        assert sdk._system_library_dirs() == []

    def test_macos_system_dirs_cover_both_homebrew_prefixes(self, monkeypatch):
        _set_platform(monkeypatch, macos=True)
        dirs = sdk._system_library_dirs()
        assert '/usr/local/lib' in dirs and '/opt/homebrew/lib' in dirs

    def test_linux_system_dirs_put_the_multiarch_folder_first(self, monkeypatch):
        _set_platform(monkeypatch)
        monkeypatch.setattr(sdk.sysconfig, 'get_config_var',
                            lambda name: 'aarch64-linux-gnu' if name == 'MULTIARCH' else None)
        dirs = sdk._system_library_dirs()
        assert dirs.index(os.path.join('/usr/lib', 'aarch64-linux-gnu')) < dirs.index('/usr/lib')

    def test_linux_without_multiarch_still_lists_plain_dirs(self, monkeypatch):
        _set_platform(monkeypatch)
        monkeypatch.setattr(sdk.sysconfig, 'get_config_var', lambda name: None)
        assert sdk._system_library_dirs() == list(sdk.LINUX_LIBRARY_DIRS)


class TestFindLibrary:
    def test_none_when_nothing_is_installed(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        assert sdk.find_library() is None

    def test_bundled_copy_wins_over_system_copy(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch, macos=True)
        bundled, _user, system = isolated_search
        (bundled / 'libASICamera2.dylib').write_bytes(LIBRARY_BYTES)
        (system / 'libASICamera2.dylib').write_bytes(LIBRARY_BYTES)
        assert sdk.find_library() == str(bundled / 'libASICamera2.dylib')

    def test_user_folder_copy_is_found(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        _bundled, user, _system = isolated_search
        (user / 'libASICamera2.so').write_bytes(LIBRARY_BYTES)
        assert sdk.find_library() == str(user / 'libASICamera2.so')

    def test_result_is_absolute(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        (isolated_search[2] / 'libASICamera2.so').write_bytes(LIBRARY_BYTES)
        assert os.path.isabs(sdk.find_library())

    def test_linux_accepts_a_versioned_soname(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        system = isolated_search[2]
        (system / 'libASICamera2.so.1.36').write_bytes(LIBRARY_BYTES)
        (system / 'libASICamera2.so.1.37').write_bytes(LIBRARY_BYTES)
        assert sdk.find_library() == str(system / 'libASICamera2.so.1.37')

    def test_macos_accepts_the_versioned_dylib(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch, macos=True)
        user = isolated_search[1]
        (user / 'libASICamera2.dylib.1.37').write_bytes(LIBRARY_BYTES)
        assert sdk.find_library() == str(user / 'libASICamera2.dylib.1.37')

    def test_symlink_stub_is_skipped_for_the_real_file_beside_it(self, monkeypatch, isolated_search):
        # What a git host or a Windows unzip makes of ZWO's libASICamera2.so link.
        _set_platform(monkeypatch)
        user = isolated_search[1]
        (user / 'libASICamera2.so').write_bytes(b'libASICamera2.so.1.37')
        (user / 'libASICamera2.so.1.37').write_bytes(LIBRARY_BYTES)
        assert sdk.find_library() == str(user / 'libASICamera2.so.1.37')

    def test_another_platforms_library_is_not_picked_up(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        (isolated_search[0] / 'ASICamera2.dll').write_bytes(LIBRARY_BYTES)
        assert sdk.find_library() is None

    def test_a_directory_with_the_library_name_is_ignored(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        (isolated_search[0] / 'libASICamera2.so').mkdir()
        assert sdk.find_library() is None


class TestResolveLibraryPath:
    def test_existing_configured_path_wins(self, monkeypatch, isolated_search, tmp_path):
        _set_platform(monkeypatch)
        (isolated_search[0] / 'libASICamera2.so').write_bytes(LIBRARY_BYTES)
        custom = tmp_path / 'custom.so'
        custom.write_bytes(LIBRARY_BYTES)
        assert sdk.resolve_library_path(str(custom)) == str(custom)

    def test_stale_configured_path_falls_back_to_the_search(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        found = isolated_search[0] / 'libASICamera2.so'
        found.write_bytes(LIBRARY_BYTES)
        assert sdk.resolve_library_path(r'C:\gone\ASICamera2.dll') == str(found)

    @pytest.mark.parametrize('configured', [None, ''])
    def test_unset_configured_path_falls_back_to_the_search(self, monkeypatch, isolated_search, configured):
        _set_platform(monkeypatch)
        assert sdk.resolve_library_path(configured) is None

    def test_default_is_the_bundled_spot_when_nothing_is_installed(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        assert sdk.default_library_path() == str(isolated_search[0] / 'libASICamera2.so')


class TestNamesAnotherPlatformsLibrary:
    @pytest.mark.parametrize('path', [
        r'C:\Program Files (x86)\PFRSentinel\_internal\ASICamera2.dll',
        '/Users/someone/PFRSentinel/ASICamera2.dll',
        '/usr/local/lib/libASICamera2.dylib',
    ])
    def test_foreign_library_on_linux(self, monkeypatch, path):
        _set_platform(monkeypatch)
        assert sdk.names_another_platforms_library(path) is True

    @pytest.mark.parametrize('path', [
        '/usr/lib/libASICamera2.so',
        '/usr/lib/x86_64-linux-gnu/libASICamera2.so.1.37',
        '/mnt/usb/my-sdk-build.so',
    ])
    def test_own_or_unrecognised_name_is_left_alone(self, monkeypatch, path):
        _set_platform(monkeypatch)
        assert sdk.names_another_platforms_library(path) is False

    def test_linux_library_is_foreign_on_windows(self, monkeypatch):
        _set_platform(monkeypatch, windows=True)
        assert sdk.names_another_platforms_library('/usr/lib/libASICamera2.so') is True
        assert sdk.names_another_platforms_library(r'D:\sdk\ASICamera2.dll') is False


class TestMissingLibraryHelp:
    def test_names_the_library_and_every_searched_folder(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        text = '\n'.join(sdk.missing_library_help())
        assert 'libASICamera2.so' in text
        for folder in isolated_search:
            assert str(folder) in text

    def test_linux_mentions_the_udev_rule_and_macos_does_not(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch)
        assert 'asi.rules' in '\n'.join(sdk.missing_library_help())
        _set_platform(monkeypatch, macos=True)
        assert 'asi.rules' not in '\n'.join(sdk.missing_library_help())

    def test_windows_does_not_send_the_user_to_a_download(self, monkeypatch, isolated_search):
        _set_platform(monkeypatch, windows=True)
        text = '\n'.join(sdk.missing_library_help())
        assert 'Download' not in text and 'ASICamera2.dll' in text


class TestDefaultConfig:
    def test_default_sdk_path_names_this_platforms_library(self):
        from services.config import DEFAULT_CONFIG
        assert host_platform.zwo_sdk_library_name() in DEFAULT_CONFIG['zwo_sdk_path']
        assert os.path.isabs(DEFAULT_CONFIG['zwo_sdk_path'])
