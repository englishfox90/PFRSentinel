"""
Platform and privilege detection at startup.

Covers `main._check_admin_privileges`, `main._platform_name`, and
`services.camera.camera_connection.CameraConnection._is_running_as_admin`.
All run during startup and must never raise on any platform.
"""
import pytest
from unittest.mock import MagicMock, patch


def _windll_returning(value):
    """A stand-in for ctypes.windll whose IsUserAnAdmin() returns `value`."""
    windll = MagicMock()
    windll.shell32.IsUserAnAdmin.return_value = value
    return windll


try:
    from services.camera.camera_connection import CameraConnection
    _CONN_SKIP = None
except ImportError as e:  # optional runtime deps absent
    CameraConnection = None
    _CONN_SKIP = f"camera_connection not importable: {e}"

try:
    import main as _main
    _MAIN_SKIP = None
except ImportError as e:  # optional runtime deps absent
    _main = None
    _MAIN_SKIP = f"main.py not importable: {e}"


def _load_camera_connection():
    if CameraConnection is None:
        pytest.skip(_CONN_SKIP)
    return CameraConnection


def _load_main():
    if _main is None:
        pytest.skip(_MAIN_SKIP)
    return _main


class TestCameraConnectionAdminCheck:
    """CameraConnection._is_running_as_admin across platforms."""

    def test_windows_reports_admin_from_shell32(self):
        conn = _load_camera_connection()
        with patch('sys.platform', 'win32'), \
                patch('ctypes.windll', _windll_returning(1), create=True):
            assert conn._is_running_as_admin() is True

    def test_windows_reports_non_admin_from_shell32(self):
        conn = _load_camera_connection()
        with patch('sys.platform', 'win32'), \
                patch('ctypes.windll', _windll_returning(0), create=True):
            assert conn._is_running_as_admin() is False

    def test_posix_root_uses_geteuid(self):
        conn = _load_camera_connection()
        with patch('sys.platform', 'linux'), \
                patch('os.geteuid', return_value=0, create=True):
            assert conn._is_running_as_admin() is True

    def test_posix_normal_user_uses_geteuid(self):
        conn = _load_camera_connection()
        with patch('sys.platform', 'darwin'), \
                patch('os.geteuid', return_value=501, create=True):
            assert conn._is_running_as_admin() is False

    def test_posix_never_calls_shell32(self):
        conn = _load_camera_connection()
        windll = _windll_returning(1)
        with patch('sys.platform', 'linux'), \
                patch('ctypes.windll', windll, create=True), \
                patch('os.geteuid', return_value=1000, create=True):
            assert conn._is_running_as_admin() is False
        assert windll.shell32.IsUserAnAdmin.call_count == 0

    def test_returns_false_when_windows_api_missing(self):
        conn = _load_camera_connection()
        windll = MagicMock()
        windll.shell32.IsUserAnAdmin.side_effect = AttributeError("no shell32")
        with patch('sys.platform', 'win32'), \
                patch('ctypes.windll', windll, create=True):
            assert conn._is_running_as_admin() is False

    def test_returns_false_when_geteuid_unavailable(self):
        conn = _load_camera_connection()
        with patch('sys.platform', 'linux'), \
                patch('os.geteuid', side_effect=OSError("unsupported"), create=True):
            assert conn._is_running_as_admin() is False


class TestMainAdminPrivilegeCheck:
    """main._check_admin_privileges result and platform-appropriate wording."""

    def test_windows_admin_logs_unchanged_message(self):
        main = _load_main()
        logger = MagicMock()
        with patch('sys.platform', 'win32'), \
                patch.object(main, 'app_logger', logger), \
                patch('ctypes.windll', _windll_returning(1), create=True):
            assert main._check_admin_privileges() is True
        logger.info.assert_called_once_with("Running with Administrator privileges")
        assert logger.warning.call_count == 0

    def test_windows_non_admin_warns_about_administrator(self):
        main = _load_main()
        logger = MagicMock()
        with patch('sys.platform', 'win32'), \
                patch.object(main, 'app_logger', logger), \
                patch('ctypes.windll', _windll_returning(0), create=True):
            assert main._check_admin_privileges() is False
        message = logger.warning.call_args[0][0]
        assert "Not running as Administrator" in message
        assert "run as Administrator" in message

    def test_posix_normal_user_gives_no_administrator_advice(self):
        main = _load_main()
        logger = MagicMock()
        with patch('sys.platform', 'linux'), \
                patch.object(main, 'app_logger', logger), \
                patch('os.geteuid', return_value=1000, create=True):
            assert main._check_admin_privileges() is False
        assert logger.warning.call_count == 0
        message = logger.info.call_args[0][0]
        assert "Administrator" not in message
        assert "Windows-only" in message

    def test_posix_root_does_not_advise_elevating(self):
        main = _load_main()
        logger = MagicMock()
        with patch('sys.platform', 'darwin'), \
                patch.object(main, 'app_logger', logger), \
                patch('os.geteuid', return_value=0, create=True):
            assert main._check_admin_privileges() is True
        message = logger.warning.call_args[0][0]
        assert "not recommended" in message
        assert "Run as administrator" not in message

    def test_posix_never_calls_shell32(self):
        main = _load_main()
        windll = _windll_returning(1)
        with patch('sys.platform', 'linux'), \
                patch.object(main, 'app_logger', MagicMock()), \
                patch('ctypes.windll', windll, create=True), \
                patch('os.geteuid', return_value=1000, create=True):
            assert main._check_admin_privileges() is False
        assert windll.shell32.IsUserAnAdmin.call_count == 0

    def test_returns_false_when_privilege_api_unavailable(self):
        main = _load_main()
        logger = MagicMock()
        with patch.object(main, 'app_logger', logger):
            with patch('sys.platform', 'win32'), \
                    patch('ctypes.windll', MagicMock(
                        **{'shell32.IsUserAnAdmin.side_effect': AttributeError}),
                    create=True):
                assert main._check_admin_privileges() is False
            with patch('sys.platform', 'linux'), \
                    patch('os.geteuid', side_effect=OSError, create=True):
                assert main._check_admin_privileges() is False


class TestPlatformNameProperty:
    """main._platform_name feeds the PostHog `os` person property."""

    def test_windows_value_is_unchanged_literal(self):
        main = _load_main()
        with patch('sys.platform', 'win32'), \
                patch('platform.system', return_value='Windows'):
            assert main._platform_name() == 'Windows'

    def test_windows_ignores_platform_system(self):
        main = _load_main()
        with patch('sys.platform', 'win32'), \
                patch('platform.system', side_effect=RuntimeError):
            assert main._platform_name() == 'Windows'

    def test_macos_reported_as_macos(self):
        main = _load_main()
        with patch('sys.platform', 'darwin'), \
                patch('platform.system', return_value='Darwin'):
            assert main._platform_name() == 'macOS'

    def test_linux_reported_as_linux(self):
        main = _load_main()
        with patch('sys.platform', 'linux'), \
                patch('platform.system', return_value='Linux'):
            assert main._platform_name() == 'Linux'

    def test_unknown_platform_never_raises(self):
        main = _load_main()
        with patch('sys.platform', 'linux'), \
                patch('platform.system', side_effect=OSError):
            assert main._platform_name() == 'Unknown'
        with patch('sys.platform', 'linux'), \
                patch('platform.system', return_value=''):
            assert main._platform_name() == 'Unknown'
