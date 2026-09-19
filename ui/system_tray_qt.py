"""System tray integration for PFR Sentinel.

Built on Qt's own QSystemTrayIcon so it runs on the GUI event loop with no
extra thread and no third-party backend — the previous implementation needed
the process main thread on macOS and AppIndicator/GTK on Linux.
"""
import os

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from services.logger import app_logger
from services.utils_paths import resource_path


class TrayUnavailableError(RuntimeError):
    """Raised when the desktop session offers no system tray."""


class SystemTrayQt(QObject):
    """Tray icon + menu for the main window.

    Everything here runs on the GUI thread, so the slots touch the window
    directly.
    """

    def __init__(self, window, app, auto_start=False, auto_stop=None, start_hidden=True):
        """
        Args:
            window: MainWindow instance
            app: QApplication instance
            auto_start: Start capture automatically
            auto_stop: Stop after N seconds
            start_hidden: Hide the window on init (startup-to-tray). When False,
                the window stays visible — used when tray mode is toggled on
                from Settings, where yanking the window away is jarring UX.

        Raises:
            TrayUnavailableError: no system tray on this desktop. Raised before
                anything is hidden, so the caller can fall back to a plain
                visible window rather than leaving the app unreachable.
        """
        super().__init__()

        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise TrayUnavailableError("No system tray is available on this desktop")

        self.window = window
        self.app = app
        self.auto_start = auto_start
        self.auto_stop = auto_stop

        self.menu = QMenu()
        self._show_hide_action = self.menu.addAction("Hide Window")
        self._show_hide_action.triggered.connect(self._toggle_window)
        self.menu.setDefaultAction(self._show_hide_action)
        self.menu.addSeparator()
        self._start_action = self.menu.addAction("Start Capture")
        self._start_action.triggered.connect(self._do_start_capture)
        self._stop_action = self.menu.addAction("Stop Capture")
        self._stop_action.triggered.connect(self._do_stop_capture)
        self.menu.addSeparator()
        self._exit_action = self.menu.addAction("Exit")
        self._exit_action.triggered.connect(self._do_exit_app)
        self.menu.aboutToShow.connect(self._refresh_menu)

        self.tray_icon = QSystemTrayIcon(self._load_icon(), self)
        self.tray_icon.setToolTip("PFR Sentinel")
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_activated)
        self._refresh_menu()
        self.tray_icon.show()

        # Without this, hiding the only top-level window quits the app.
        self.app.setQuitOnLastWindowClosed(False)

        if start_hidden:
            self.window.hide()

        app_logger.info("System tray initialized")

        if auto_start:
            QTimer.singleShot(3000, self._auto_start_capture)

    def _load_icon(self) -> QIcon:
        for name in ('assets/app_icon.png', 'assets/app_icon.ico'):
            try:
                path = resource_path(name)
                if os.path.exists(path):
                    return QIcon(path)
            except Exception as e:
                app_logger.warning(f"Could not load tray icon {name}: {e}")
        app_logger.warning("No tray icon asset found - using the platform default")
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    @property
    def _is_visible(self) -> bool:
        # Derived, not tracked: closeEvent hides to the tray and the update
        # dialog re-shows the window without going through this class.
        return self.window.isVisible()

    def _refresh_menu(self):
        self._show_hide_action.setText("Hide Window" if self._is_visible else "Show Window")
        capturing = bool(getattr(self.window, 'is_capturing', False))
        self._start_action.setEnabled(not capturing)
        self._stop_action.setEnabled(capturing)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._do_show_window()

    def _toggle_window(self):
        if self._is_visible:
            self._do_hide_window()
        else:
            self._do_show_window()

    def _do_show_window(self):
        self.window.show()
        self.window.activateWindow()
        self.window.raise_()
        self._refresh_menu()

    def _do_hide_window(self):
        self.window.hide()
        self._refresh_menu()
        app_logger.debug("Window minimized to tray")

    def _do_start_capture(self):
        try:
            if not self.window.is_capturing:
                self.window.start_capture()
                app_logger.info("Capture started from tray menu")
        except Exception as e:
            app_logger.error(f"Error starting capture from tray: {e}")
        self._refresh_menu()

    def _do_stop_capture(self):
        try:
            if self.window.is_capturing:
                self.window.stop_capture()
                app_logger.info("Capture stopped from tray menu")
        except Exception as e:
            app_logger.error(f"Error stopping capture from tray: {e}")
        self._refresh_menu()

    def _auto_start_capture(self):
        try:
            self.window.start_capture()
            app_logger.info("Auto-started capture from tray")

            if self.auto_stop and self.auto_stop > 0:
                QTimer.singleShot(self.auto_stop * 1000, self._auto_stop_capture)
        except Exception as e:
            app_logger.error(f"Error auto-starting capture: {e}")

    def _auto_stop_capture(self):
        try:
            self.window.stop_capture()
            app_logger.info(f"Auto-stopped capture after {self.auto_stop}s")
        except Exception as e:
            app_logger.error(f"Error auto-stopping capture: {e}")

    def _do_exit_app(self):
        app_logger.info("Exiting from system tray")
        self.window.quit_application()

    def shutdown(self):
        """Tear the tray down and let a last window close quit the app again."""
        try:
            if self.tray_icon is not None:
                self.tray_icon.hide()
                self.tray_icon.setContextMenu(None)
            if self.menu is not None:
                self.menu.deleteLater()
                self.menu = None
        except Exception as e:
            app_logger.debug(f"Tray shutdown: {e}")
        try:
            self.app.setQuitOnLastWindowClosed(True)
        except Exception:
            pass
