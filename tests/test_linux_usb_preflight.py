"""Tests for services/camera/linux_usb_preflight.py — udev rule + usbfs buffer checks (issue #39)."""
import os

import pytest

from services.camera import linux_usb_preflight as preflight

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _reset_once_flag(monkeypatch):
    monkeypatch.setattr(preflight, '_already_logged', False)


def _host(monkeypatch, *, usbfs_mb, has_rule, euid=1000):
    monkeypatch.setattr(preflight, 'read_usbfs_memory_mb', lambda: usbfs_mb)
    monkeypatch.setattr(preflight, 'has_zwo_udev_rule', lambda: has_rule)
    monkeypatch.setattr(preflight.os, 'geteuid', lambda: euid, raising=False)


class TestReadUsbfsMemory:
    def test_reads_the_kernel_parameter(self, tmp_path):
        param = tmp_path / 'usbfs_memory_mb'
        param.write_text('16\n')
        assert preflight.read_usbfs_memory_mb(str(param)) == 16

    def test_missing_or_garbled_parameter_is_none(self, tmp_path):
        assert preflight.read_usbfs_memory_mb(str(tmp_path / 'absent')) is None
        garbled = tmp_path / 'garbled'
        garbled.write_text('n/a')
        assert preflight.read_usbfs_memory_mb(str(garbled)) is None


class TestHasZwoUdevRule:
    def test_finds_the_vendor_id_in_any_rules_file(self, tmp_path):
        (tmp_path / '10-other.rules').write_text('ATTR{idVendor}=="1234"\n')
        (tmp_path / '99-asi.rules').write_text('ATTR{idVendor}=="03C3", MODE="0666"\n')
        assert preflight.has_zwo_udev_rule([str(tmp_path)]) is True

    def test_false_when_no_rule_mentions_zwo(self, tmp_path):
        (tmp_path / '10-other.rules').write_text('ATTR{idVendor}=="1234"\n')
        assert preflight.has_zwo_udev_rule([str(tmp_path), str(tmp_path / 'absent')]) is False

    def test_shipped_rule_file_satisfies_the_check(self):
        assert preflight.has_zwo_udev_rule([os.path.join(REPO_ROOT, 'installer', 'linux')]) is True

    def test_shipped_rule_sets_the_recommended_buffer(self):
        with open(os.path.join(REPO_ROOT, 'installer', 'linux', 'asi.rules'), encoding='utf-8') as handle:
            rule = handle.read()
        assert f'echo {preflight.RECOMMENDED_USBFS_MB} >' in rule
        assert 'MODE="0666"' in rule


class TestLinuxUsbWarnings:
    def test_healthy_host_has_no_warnings(self, monkeypatch):
        _host(monkeypatch, usbfs_mb=200, has_rule=True)
        assert preflight.linux_usb_warnings() == []

    def test_small_buffer_warns_with_the_fix(self, monkeypatch):
        _host(monkeypatch, usbfs_mb=16, has_rule=True)
        warnings = preflight.linux_usb_warnings()
        assert '16 MB' in warnings[0]
        assert 'asi.rules' in warnings[-1]

    @pytest.mark.parametrize('usbfs_mb', [0, None])
    def test_unlimited_or_unreadable_buffer_is_not_a_warning(self, monkeypatch, usbfs_mb):
        _host(monkeypatch, usbfs_mb=usbfs_mb, has_rule=True)
        assert preflight.linux_usb_warnings() == []

    def test_missing_rule_warns_for_a_normal_user(self, monkeypatch):
        _host(monkeypatch, usbfs_mb=200, has_rule=False)
        assert any('udev' in line for line in preflight.linux_usb_warnings())

    def test_missing_rule_is_fine_for_root(self, monkeypatch):
        _host(monkeypatch, usbfs_mb=200, has_rule=False, euid=0)
        assert preflight.linux_usb_warnings() == []


class TestLogLinuxUsbWarnings:
    def test_noop_off_linux(self, monkeypatch):
        monkeypatch.setattr(preflight.sys, 'platform', 'win32')
        _host(monkeypatch, usbfs_mb=16, has_rule=False)
        lines = []
        preflight.log_linux_usb_warnings(lines.append)
        assert lines == []

    def test_logs_once_per_process_on_linux(self, monkeypatch):
        monkeypatch.setattr(preflight.sys, 'platform', 'linux')
        _host(monkeypatch, usbfs_mb=16, has_rule=True)
        lines = []
        preflight.log_linux_usb_warnings(lines.append)
        first = len(lines)
        preflight.log_linux_usb_warnings(lines.append)
        assert first > 0 and len(lines) == first

    def test_a_failing_check_never_raises(self, monkeypatch):
        monkeypatch.setattr(preflight.sys, 'platform', 'linux')

        def boom():
            raise RuntimeError('sysfs went away')
        monkeypatch.setattr(preflight, 'linux_usb_warnings', boom)
        lines = []
        preflight.log_linux_usb_warnings(lines.append)
        assert 'skipped' in lines[0]
