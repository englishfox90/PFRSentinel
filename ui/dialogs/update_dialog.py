"""
Update Dialog
Shows update available notification with download/skip options.
"""

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTextBrowser
)
from PySide6.QtCore import Qt, Signal, QThread
from qfluentwidgets import (
    MessageBoxBase, SubtitleLabel, BodyLabel, CaptionLabel,
    PrimaryPushButton, PushButton
)

from services.host_platform import IS_WINDOWS, platform_label
from services.logger import app_logger
from services.reveal_in_file_manager import reveal_path
from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi


class DownloadThread(QThread):
    """Background thread for downloading installer."""

    progress = Signal(int, int)  # downloaded, total
    download_finished = Signal(object)  # Path or None  (don't shadow QThread.finished)

    def __init__(self, update_checker, update_info):
        super().__init__()
        self.update_checker = update_checker
        self.update_info = update_info
        self._last_percent = -1

    def run(self):
        def throttled_progress(downloaded, total):
            """Only emit progress every 1% to reduce UI overhead."""
            if total > 0:
                percent = int(downloaded * 100 / total)
                if percent != self._last_percent:
                    self._last_percent = percent
                    self.progress.emit(downloaded, total)
            else:
                self.progress.emit(downloaded, total)

        result = self.update_checker.download_installer(
            self.update_info,
            progress_callback=throttled_progress
        )
        self.download_finished.emit(result)


class UpdateDialog(MessageBoxBase):
    """Dialog showing update available with download option."""

    def __init__(self, parent, update_info):
        super().__init__(parent)
        self.update_info = update_info
        self._download_thread = None
        self._downloaded_path = None  # Path to downloaded installer

        # Hide default OK/Cancel buttons - we have our own
        self.yesButton.hide()
        self.cancelButton.hide()

        self._setup_ui()

    def _setup_ui(self):
        # Title
        title = SubtitleLabel(f"Update Available: v{self.update_info.latest_version}")
        title.setStyleSheet(f"color: {Colors.text_primary};")
        self.viewLayout.addWidget(title)

        # Current vs new version
        version_text = BodyLabel(
            f"You're running v{self.update_info.current_version}  \u2192  "
            f"v{self.update_info.latest_version} is available"
        )
        version_text.setStyleSheet(f"color: {Colors.text_secondary};")
        self.viewLayout.addWidget(version_text)

        self.viewLayout.addSpacing(Spacing.sm)

        # Release notes (scrollable, rendered as markdown)
        notes = self.update_info.release_notes or ""
        notes_browser = QTextBrowser()
        notes_browser.setMarkdown(notes)
        notes_browser.setOpenExternalLinks(True)
        notes_browser.setReadOnly(True)
        # Size the notes area from the host window rather than a fixed cap, so
        # long release notes get the room the screen actually has. The dialog is
        # an overlay on its parent, so it can never grow past that window.
        notes_browser.setFixedHeight(self._notes_height())
        notes_browser.setStyleSheet(f"""
            QTextBrowser {{
                color: {Colors.text_secondary};
                background: {Colors.bg_card};
                border: 1px solid {Colors.border_subtle};
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }}
        """)
        self.viewLayout.addWidget(notes_browser)

        self.viewLayout.addSpacing(Spacing.sm)

        # File info
        if self.update_info.installer_name:
            size_info = CaptionLabel(
                f"{self.update_info.installer_name} "
                f"({self.update_info.installer_size_mb:.1f} MB)"
            )
            size_info.setStyleSheet(f"color: {Colors.text_muted};")
            self.viewLayout.addWidget(size_info)

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background: {Colors.bg_card};
                height: 20px;
            }}
            QProgressBar::chunk {{
                background: {Colors.iris_9};
                border-radius: 4px;
            }}
        """)
        self.viewLayout.addWidget(self.progress_bar)

        # Status label
        self.status_label = CaptionLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.iris_9};")
        self.status_label.setVisible(False)
        self.viewLayout.addWidget(self.status_label)

        # Buttons. Off Windows there is no installer asset to download (Phase 5,
        # #41), so "View on GitHub" takes the primary slot and download is hidden.
        self.download_btn = PrimaryPushButton("Download Update")
        self.download_btn.setIcon(mdi('download'))
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.clicked.connect(self._on_download)

        view_btn_cls = PushButton if IS_WINDOWS else PrimaryPushButton
        self.view_btn = view_btn_cls("View on GitHub")
        self.view_btn.setIcon(mdi('open-in-new'))
        self.view_btn.setCursor(Qt.PointingHandCursor)
        self.view_btn.clicked.connect(self._on_view_github)

        self.skip_btn = PushButton("Skip This Version")
        self.skip_btn.setCursor(Qt.PointingHandCursor)
        self.skip_btn.clicked.connect(self.reject)

        self.buttonLayout.addWidget(self.download_btn)
        self.buttonLayout.addWidget(self.view_btn)
        self.buttonLayout.addWidget(self.skip_btn)

        if not IS_WINDOWS:
            self.download_btn.hide()
            no_installer_note = CaptionLabel(
                f"Installers for {platform_label()} arrive in a later release — "
                "until then, pull the new version from GitHub."
            )
            no_installer_note.setStyleSheet(f"color: {Colors.text_muted};")
            no_installer_note.setWordWrap(True)
            self.viewLayout.addWidget(no_installer_note)

        self.widget.setMinimumWidth(self._dialog_width())

    # Fraction of the parent window the notes area may occupy; leaves room for
    # the title, version line, file info, progress bar and button row.
    _NOTES_HEIGHT_FRACTION = 0.55
    _NOTES_MIN_HEIGHT = 200
    _NOTES_MAX_HEIGHT = 640
    _DIALOG_MIN_WIDTH = 480
    _DIALOG_MAX_WIDTH = 760

    def _parent_size(self):
        parent = self.parent()
        if parent is not None and parent.height() > 0 and parent.width() > 0:
            return parent.width(), parent.height()
        return None

    def _notes_height(self):
        size = self._parent_size()
        if size is None:
            return self._NOTES_MIN_HEIGHT
        available = int(size[1] * self._NOTES_HEIGHT_FRACTION)
        return max(self._NOTES_MIN_HEIGHT, min(self._NOTES_MAX_HEIGHT, available))

    def _dialog_width(self):
        size = self._parent_size()
        if size is None:
            return self._DIALOG_MIN_WIDTH
        available = int(size[0] * 0.7)
        return max(self._DIALOG_MIN_WIDTH, min(self._DIALOG_MAX_WIDTH, available))

    def _on_download(self):
        """Start download in background thread."""
        from services.update_checker import get_update_checker

        app_logger.info(
            f"Update download started: {self.update_info.installer_name} "
            f"({self.update_info.installer_size_mb:.1f} MB)"
        )

        self.download_btn.setEnabled(False)
        self.download_btn.setText("Downloading...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setVisible(True)
        self.status_label.setText("Starting download...")

        checker = get_update_checker()
        self._download_thread = DownloadThread(checker, self.update_info)
        self._download_thread.progress.connect(self._on_progress)
        self._download_thread.download_finished.connect(self._on_download_finished)
        self._download_thread.start()

    def _on_progress(self, downloaded: int, total: int):
        """Update progress bar."""
        if total > 0:
            percent = int(downloaded / total * 100)
            self.progress_bar.setValue(percent)
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.status_label.setText(f"Downloading: {mb_done:.1f} / {mb_total:.1f} MB")

    def _on_download_finished(self, result):
        """Handle download completion."""
        self._download_thread = None

        if result:
            self._downloaded_path = result
            self.progress_bar.setValue(100)
            self.status_label.setText(f"Downloaded to: {result}")
            self.status_label.setStyleSheet(f"color: {Colors.status_success};")
            app_logger.info(f"Update ready to install: {result}")

            # Disconnect old handler first
            try:
                self.download_btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass

            self._set_run_installer_button()
        else:
            self.progress_bar.setVisible(False)
            self.status_label.setText("Download failed — try View on GitHub")
            self.status_label.setStyleSheet(f"color: {Colors.status_error};")
            app_logger.warning(
                f"Update download failed: {self.update_info.installer_name}"
            )
            self._reset_download_button("Retry Download")

    def _set_run_installer_button(self):
        """Update download button to 'Run Installer' state."""
        self.download_btn.setText("Run Installer")
        self.download_btn.setIcon(mdi('play'))
        self.download_btn.setEnabled(True)
        self.download_btn.clicked.connect(self._on_run_installer)
        self.download_btn.repaint()

    def _reset_download_button(self, text: str):
        """Reset download button with given text."""
        self.download_btn.setText(text)
        self.download_btn.setEnabled(True)
        self.download_btn.repaint()

    def _on_run_installer(self):
        """Launch the downloaded installer and close the app."""
        import os
        import subprocess

        if not IS_WINDOWS:
            # Belt and braces: the download button that leads here is hidden
            # off Windows, since no non-Windows installer asset exists yet.
            app_logger.warning("Run Installer requested off Windows; ignoring")
            return

        if not self._downloaded_path or not os.path.exists(self._downloaded_path):
            self.status_label.setText("Installer file not found")
            self.status_label.setStyleSheet(f"color: {Colors.status_error};")
            return

        app_logger.info(f"Launching installer: {self._downloaded_path}")

        try:
            # Launch installer detached from this process
            subprocess.Popen(
                [str(self._downloaded_path)],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )

            # Close the dialog and quit the application so installer can proceed
            self.accept()
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.quit()
        except Exception as e:
            app_logger.error(f"Failed to launch installer: {e}")
            self.status_label.setText(f"Failed to launch: {e}")
            self.status_label.setStyleSheet(f"color: {Colors.status_error};")
            # Fallback: open the containing folder
            reveal_path(self._downloaded_path)

    def _on_view_github(self):
        """Open releases page in browser."""
        from services.update_checker import get_update_checker
        checker = get_update_checker()
        checker.open_releases_page(self.update_info)


def show_update_dialog(parent, update_info) -> bool:
    """
    Show update dialog and return True if user wants to download.

    Args:
        parent: Parent widget
        update_info: UpdateInfo dataclass

    Returns:
        True if dialog was accepted (download started)
    """
    dialog = UpdateDialog(parent, update_info)
    return dialog.exec()
