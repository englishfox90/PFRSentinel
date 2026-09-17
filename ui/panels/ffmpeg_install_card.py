"""
ffmpeg install prompt for the Timelapse page.

Shown in place of the timelapse settings while ffmpeg is missing; offers a
winget install or a link to download it manually.
"""
import subprocess
import sys
import webbrowser
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal, QThread
from qfluentwidgets import (
    CardWidget, SubtitleLabel, BodyLabel, CaptionLabel,
    PushButton, PrimaryPushButton, IndeterminateProgressBar
)

from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from services.ffmpeg_utils import is_ffmpeg_available, is_winget_available


# ------------------------------------------------------------------ #
#  winget install worker                                               #
# ------------------------------------------------------------------ #

class WingetInstallWorker(QThread):
    """Runs winget install ffmpeg in a background thread."""

    finished = Signal(bool, str)  # (success, message)

    def run(self):
        try:
            # Hide console window on Windows
            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(
                [
                    'winget', 'install',
                    '--id', 'Gyan.FFmpeg',
                    '--source', 'winget',
                    '--accept-package-agreements',
                    '--accept-source-agreements',
                    '--silent',
                ],
                capture_output=True,
                text=True,
                timeout=180,
                **kwargs,
            )
            if result.returncode == 0:
                self.finished.emit(True, "ffmpeg installed successfully.")
            else:
                err = (result.stderr or result.stdout or "Unknown error").strip()
                self.finished.emit(False, f"winget exited with code {result.returncode}: {err[:200]}")
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "Installation timed out after 3 minutes.")
        except FileNotFoundError:
            self.finished.emit(False, "winget not found on this system.")
        except Exception as e:
            self.finished.emit(False, str(e))


# ------------------------------------------------------------------ #
#  ffmpeg install card                                                 #
# ------------------------------------------------------------------ #

class FfmpegInstallCard(CardWidget):
    """Shown when ffmpeg is not installed. Offers winget or manual install."""

    install_succeeded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: WingetInstallWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.card_padding, Spacing.card_padding,
                                   Spacing.card_padding, Spacing.card_padding)
        layout.setSpacing(Spacing.element_gap)

        title = SubtitleLabel("ffmpeg Required")
        title.setStyleSheet(f"color: {Colors.text_primary};")
        layout.addWidget(title)

        desc = BodyLabel(
            "Timelapse recording requires ffmpeg — a free, open-source video encoder."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {Colors.text_secondary};")
        layout.addWidget(desc)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(Spacing.sm)

        self._winget_btn = PrimaryPushButton("Install via winget")
        self._winget_btn.setIcon(mdi('download'))
        self._winget_btn.clicked.connect(self._start_winget_install)
        btn_row.addWidget(self._winget_btn)

        manual_btn = PushButton("Download manually")
        manual_btn.setIcon(mdi('open-in-new'))
        manual_btn.clicked.connect(lambda: webbrowser.open("https://ffmpeg.org/download.html"))
        btn_row.addWidget(manual_btn)
        btn_row.addStretch()

        layout.addLayout(btn_row)

        # Progress / status
        self._progress = IndeterminateProgressBar(self)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_label = CaptionLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # Hide winget button if not available
        if not is_winget_available():
            self._winget_btn.hide()
            note = CaptionLabel(
                "winget (Windows Package Manager) is not available on this system. "
                "Please install ffmpeg manually and add it to PATH."
            )
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {Colors.text_muted};")
            layout.addWidget(note)

    def _start_winget_install(self):
        self._winget_btn.setEnabled(False)
        self._progress.show()
        self._status_label.setText("Installing ffmpeg via winget…")
        self._status_label.setStyleSheet(f"color: {Colors.text_secondary};")

        self._worker = WingetInstallWorker()
        self._worker.finished.connect(self._on_install_finished)
        self._worker.start()

    def _on_install_finished(self, success: bool, message: str):
        self._progress.hide()

        # Always re-probe after winget exits — it may have installed ffmpeg
        # to the winget packages folder even when the exit code is non-zero
        # (e.g. "already installed, upgrade not applicable" = exit 2316632107).
        ffmpeg_found = is_ffmpeg_available()

        if ffmpeg_found:
            self._winget_btn.setEnabled(False)
            self._status_label.setText("✓ ffmpeg is ready.")
            self._status_label.setStyleSheet(f"color: {Colors.status_success};")
            self.install_succeeded.emit()
        elif success:
            # winget exited cleanly but ffmpeg isn't findable yet (PATH not updated)
            self._winget_btn.setEnabled(False)
            self._status_label.setText(
                "✓ ffmpeg installed. Please restart PFRSentinel to activate timelapse."
            )
            self._status_label.setStyleSheet(f"color: {Colors.status_success};")
        else:
            self._winget_btn.setEnabled(True)
            self._status_label.setText("✗ " + message)
            self._status_label.setStyleSheet(f"color: {Colors.status_error};")
