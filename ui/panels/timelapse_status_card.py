"""
Status card for the Timelapse page: the live session, the projected recording
window, and a button to open (or reveal) the video.

Layout and display formatting only — the window text arrives ready-made in the
status dict from TimelapseController (``window_forecast``).
"""
import os
import subprocess

from qfluentwidgets import BodyLabel, CaptionLabel, PushButton

from ..theme.tokens import Colors
from ..theme.icons import mdi
from ..components.cards import SettingsCard


class TimelapseStatusCard(SettingsCard):
    """'Current timelapse session' card, fed by update_status()."""

    def __init__(self, parent=None):
        super().__init__("Status", "Current timelapse session", parent)
        self._current_video_path = ''
        self._recording_active = False

        self.status_label = BodyLabel("Not recording")
        self.status_label.setStyleSheet(f"color: {Colors.text_muted};")
        self.add_widget(self.status_label)

        self.window_label = CaptionLabel("")
        self.window_label.setWordWrap(True)
        self.window_label.setStyleSheet(f"color: {Colors.text_secondary};")
        self.window_label.hide()
        self.add_widget(self.window_label)

        self.open_video_btn = PushButton("Open video")
        self.open_video_btn.setIcon(mdi('play'))
        self.open_video_btn.setEnabled(False)
        self.open_video_btn.clicked.connect(self._open_current_video)
        self.add_widget(self.open_video_btn)

    def update_status(self, status: dict):
        """Render a TimelapseController.get_status() dict."""
        session_path = status.get('session_path', '')

        if status.get('recording'):
            elapsed = status.get('elapsed_seconds', 0)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            filename = os.path.basename(session_path)
            text = (
                f"● Recording  ·  {status.get('frame_count', 0)} frames  ·  "
                f"{h:02d}:{m:02d}:{s:02d} elapsed  ·  {filename}"
            )
            self.status_label.setText(text)
            self.status_label.setStyleSheet(f"color: {Colors.status_success};")
        else:
            self.status_label.setText("Not recording")
            self.status_label.setStyleSheet(f"color: {Colors.text_muted};")

        forecast = status.get('window_forecast', '')
        self.window_label.setText(forecast)
        self.window_label.setVisible(bool(forecast))

        self._current_video_path = session_path
        self._recording_active = status.get('recording', False)
        file_exists = bool(session_path and os.path.isfile(session_path))

        if self._recording_active and file_exists:
            # File is locked by ffmpeg — show folder instead so user can drag to VLC
            self.open_video_btn.setText("Show in folder")
            self.open_video_btn.setIcon(mdi('folder-outline'))
            self.open_video_btn.setEnabled(True)
        elif file_exists:
            # Recording stopped — file is finalized and safe to open directly
            self.open_video_btn.setText("Open video")
            self.open_video_btn.setIcon(mdi('play'))
            self.open_video_btn.setEnabled(True)
        else:
            self.open_video_btn.setText("Open video")
            self.open_video_btn.setIcon(mdi('play'))
            self.open_video_btn.setEnabled(False)

    def _open_current_video(self):
        """Open video or reveal in Explorer depending on recording state."""
        path = self._current_video_path
        if not path or not os.path.isfile(path):
            return
        if self._recording_active:
            # Reveal in Explorer with the file selected
            subprocess.run(['explorer', f'/select,{path}'])
        else:
            os.startfile(path)
