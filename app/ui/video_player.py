"""
Video Player Component using PySide6 QtMultimedia
F2: Preview, seek, play by segment
Features:
- Live Subtitle Overlay on Video Surface
- Live Custom Logo / Watermark Overlay on Video Surface with position & opacity control
- Original Video vs Result Video Switcher
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QLabel, QStyle, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from app.utils import format_timestamp

class VideoPlayerWidget(QWidget):
    position_changed = Signal(float) # Current time in seconds
    preview_requested = Signal()     # Request generating dubbed preview

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segment_end_time = None
        self.original_video_path = ""
        self.result_video_path = ""
        self.preview_video_path = ""
        self.is_final_export = False
        self.current_mode = "original" # "original" or "result"
        self.logo_config = {
            "enabled": False,
            "path": "",
            "position": "top_right",
            "width": 120,
            "opacity": 0.90,
            "margin_x": 20,
            "margin_y": 20
        }
        self._init_ui()
        self._setup_player()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Mode Switcher Bar (Original Video vs Result/Preview Video)
        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(6)

        self.btn_mode_original = QPushButton("📺 វីដេអូដើម")
        self.btn_mode_original.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 12px;")
        self.btn_mode_original.clicked.connect(lambda: self.switch_mode("original"))
        mode_bar.addWidget(self.btn_mode_original)

        self.btn_mode_result = QPushButton("✨ លទ្ធផល (Result)")
        self.btn_mode_result.setStyleSheet("background-color: #334155; color: #94a3b8; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 12px;")
        self.btn_mode_result.clicked.connect(lambda: self.switch_mode("result"))
        mode_bar.addWidget(self.btn_mode_result)

        self.btn_refresh_preview = QPushButton("🔄")
        self.btn_refresh_preview.setFixedWidth(30)
        self.btn_refresh_preview.setStyleSheet("background-color: #1e293b; color: #38bdf8; font-weight: bold; padding: 4px; border-radius: 4px; border: 1px solid #0284c7;")
        self.btn_refresh_preview.setToolTip("បង្កើត និងផ្ទុកវីដេអូ Preview ឡើងវិញ (Regenerate Fast Preview)")
        self.btn_refresh_preview.clicked.connect(self.preview_requested.emit)
        mode_bar.addWidget(self.btn_refresh_preview)

        mode_bar.addStretch()

        self.lbl_mode_status = QLabel("● ដើម")
        self.lbl_mode_status.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")
        mode_bar.addWidget(self.lbl_mode_status)

        layout.addLayout(mode_bar)

        # Video Surface Widget
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(420, 240)
        self.video_widget.setStyleSheet("background-color: #000000; border-radius: 8px;")
        layout.addWidget(self.video_widget, stretch=1)

        # Real-time Subtitle Overlay on Video Surface
        self.lbl_subtitle = QLabel(self.video_widget)
        self.lbl_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_subtitle.setWordWrap(True)
        self.lbl_subtitle.setStyleSheet("""
            background-color: rgba(15, 23, 42, 0.88);
            color: #fef08a;
            font-family: 'Kantumruy Pro', 'Inter', sans-serif;
            font-size: 15px;
            font-weight: bold;
            padding: 6px 14px;
            border-radius: 6px;
            border: 1px solid rgba(254, 240, 138, 0.35);
        """)
        self.lbl_subtitle.hide()

        # Real-time Logo / Watermark Overlay on Video Surface
        self.lbl_logo = QLabel(self.video_widget)
        self.lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_logo.setStyleSheet("background: transparent;")
        self.lbl_logo.hide()

        # Controls Container
        controls_layout = QVBoxLayout()
        controls_layout.setSpacing(4)

        # Progress / Seek Slider
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_seek)
        controls_layout.addWidget(self.slider)

        # Buttons and Time readout
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setFixedWidth(40)
        self.btn_play.clicked.connect(self.toggle_play)
        btn_row.addWidget(self.btn_play)

        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.setFixedWidth(40)
        self.btn_stop.clicked.connect(self.stop)
        btn_row.addWidget(self.btn_stop)

        self.lbl_time = QLabel("00:00:00 / 00:00:00")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 12px; color: #94a3b8;")
        btn_row.addWidget(self.lbl_time)

        btn_row.addStretch()

        # Volume icon + slider
        lbl_vol = QLabel("🔊")
        btn_row.addWidget(lbl_vol)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        btn_row.addWidget(self.vol_slider)

        controls_layout.addLayout(btn_row)
        layout.addLayout(controls_layout)

    def _setup_player(self):
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.audio_output.setVolume(0.8)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def load_video(self, video_path: str):
        """Load video file for playback."""
        if self.current_mode == "original":
            self.original_video_path = video_path

        url = QUrl.fromLocalFile(video_path)
        self.player.setSource(url)
        self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.btn_mode_result.setEnabled(True)
        self.btn_refresh_preview.setEnabled(True)

    def set_preview_video(self, preview_path: str):
        """Set fast-muxed preview video (before full export) and start preview playback."""
        self.preview_video_path = preview_path
        self.result_video_path = preview_path
        self.is_final_export = False
        if preview_path and os.path.exists(preview_path):
            self.btn_mode_result.setEnabled(True)
            self.switch_mode("result")

    def set_result_video(self, result_path: str):
        """Set final exported video (with burned-in elements)."""
        self.result_video_path = result_path
        self.is_final_export = True
        if result_path and os.path.exists(result_path):
            self.btn_mode_result.setEnabled(True)
            self.switch_mode("result")

    def switch_mode(self, mode: str):
        """Switch video preview between Original and Result / Preview Video."""
        pos_ms = self.player.position()
        was_playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.current_mode = mode

        if mode == "result":
            target_path = self.result_video_path or self.preview_video_path
            if not target_path or not os.path.exists(target_path):
                # Auto-request fast preview generation if result doesn't exist yet!
                self.preview_requested.emit()
                return

            self.load_video(target_path)
            self.btn_mode_result.setStyleSheet("background-color: #7c3aed; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 12px;")
            self.btn_mode_original.setStyleSheet("background-color: #334155; color: #94a3b8; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 12px;")

            if self.is_final_export:
                self.lbl_mode_status.setText("● Exported 🎬")
                self.lbl_mode_status.setStyleSheet("color: #34d399; font-size: 11px; font-weight: bold;")
                self.lbl_subtitle.hide()
            else:
                self.lbl_mode_status.setText("● Preview 🎧")
                self.lbl_mode_status.setStyleSheet("color: #c084fc; font-size: 11px; font-weight: bold;")

            self._update_logo_display()
            self.player.setPosition(pos_ms)
            if was_playing:
                self.play()
        else:
            if not self.original_video_path or not os.path.exists(self.original_video_path):
                return

            self.load_video(self.original_video_path)
            self.btn_mode_original.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 12px;")
            self.btn_mode_result.setStyleSheet("background-color: #334155; color: #94a3b8; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 12px;")
            self.lbl_mode_status.setText("● ដើម 📺")
            self.lbl_mode_status.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")
            self._update_logo_display()
            self.player.setPosition(pos_ms)
            if was_playing:
                self.play()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.segment_end_time = None
            self.player.play()

    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()
        self.segment_end_time = None

    def seek_to_second(self, seconds: float):
        """Seek to timestamp in seconds."""
        pos_ms = int(seconds * 1000)
        self.player.setPosition(pos_ms)

    def play_segment(self, start_sec: float, end_sec: float):
        """Play specific segment from start_sec to end_sec and then pause."""
        self.seek_to_second(start_sec)
        self.segment_end_time = end_sec
        self.player.play()

    def get_current_time(self) -> float:
        """Return current video playback position in seconds."""
        if hasattr(self, "player") and self.player:
            return max(0.0, self.player.position() / 1000.0)
        return 0.0

    def _on_seek(self, position_ms: int):
        self.segment_end_time = None
        self.player.setPosition(position_ms)

    def _on_position_changed(self, position_ms: int):
        self.slider.setValue(position_ms)
        curr_sec = position_ms / 1000.0
        dur_sec = self.player.duration() / 1000.0
        
        self.lbl_time.setText(f"{format_timestamp(curr_sec, False)} / {format_timestamp(dur_sec, False)}")
        self.position_changed.emit(curr_sec)

        # Segment playback check
        if self.segment_end_time is not None and curr_sec >= self.segment_end_time:
            self.player.pause()
            self.segment_end_time = None

    def _on_duration_changed(self, duration_ms: int):
        self.slider.setRange(0, duration_ms)

    def _on_volume_changed(self, val: int):
        self.audio_output.setVolume(val / 100.0)

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def set_subtitle_text(self, text: str):
        """Display live subtitle text overlay on top of video."""
        if self.current_mode == "result" and self.is_final_export:
            # Final exported video already has burned subtitles
            self.lbl_subtitle.hide()
            return

        cleaned = text.strip() if text else ""
        if not cleaned:
            self.lbl_subtitle.hide()
            return

        self.lbl_subtitle.setText(cleaned)
        self.lbl_subtitle.adjustSize()
        vid_w = max(100, self.video_widget.width())
        vid_h = max(100, self.video_widget.height())
        sub_w = min(vid_w - 40, max(200, self.lbl_subtitle.width() + 24))
        sub_h = max(34, self.lbl_subtitle.height() + 8)
        self.lbl_subtitle.resize(sub_w, sub_h)
        self.lbl_subtitle.move((vid_w - sub_w) // 2, max(10, vid_h - sub_h - 20))
        self.lbl_subtitle.show()
        self.lbl_subtitle.raise_()

    def set_logo(
        self,
        enabled: bool,
        path: str,
        position: str = "top_right",
        width: int = 120,
        opacity: float = 0.90,
        margin_x: int = 20,
        margin_y: int = 20
    ):
        """Configure live logo / watermark overlay."""
        self.logo_config = {
            "enabled": enabled,
            "path": path,
            "position": position,
            "width": width,
            "opacity": opacity,
            "margin_x": margin_x,
            "margin_y": margin_y
        }
        self._update_logo_display()

    def _update_logo_display(self):
        """Update logo overlay size, opacity, and coordinates on video widget."""
        if self.current_mode == "result" and self.is_final_export:
            # Final exported video already has burned-in logo
            self.lbl_logo.hide()
            return

        cfg = self.logo_config
        if not cfg["enabled"] or not cfg["path"] or not os.path.exists(cfg["path"]):
            self.lbl_logo.hide()
            return

        pix = QPixmap(cfg["path"])
        if pix.isNull():
            self.lbl_logo.hide()
            return

        vid_w = max(100, self.video_widget.width())
        vid_h = max(100, self.video_widget.height())

        # Scale logo relative to video surface size
        target_w = max(30, min(cfg["width"], vid_w - 20))
        scaled_pix = pix.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
        self.lbl_logo.setPixmap(scaled_pix)
        self.lbl_logo.adjustSize()

        # Opacity effect
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(max(0.1, min(cfg["opacity"], 1.0)))
        self.lbl_logo.setGraphicsEffect(effect)

        lw = self.lbl_logo.width()
        lh = self.lbl_logo.height()
        mx = cfg["margin_x"]
        my = cfg["margin_y"]

        pos = cfg["position"]
        if pos == "top_left":
            x, y = mx, my
        elif pos == "bottom_left":
            x, y = mx, max(0, vid_h - lh - my)
        elif pos == "bottom_right":
            x, y = max(0, vid_w - lw - mx), max(0, vid_h - lh - my)
        elif pos == "center":
            x, y = max(0, (vid_w - lw) // 2), max(0, (vid_h - lh) // 2)
        else: # top_right
            x, y = max(0, vid_w - lw - mx), my

        self.lbl_logo.move(x, y)
        self.lbl_logo.show()
        self.lbl_logo.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Reposition subtitle overlay
        if hasattr(self, "lbl_subtitle") and self.lbl_subtitle.isVisible():
            vid_w = max(100, self.video_widget.width())
            vid_h = max(100, self.video_widget.height())
            sub_w = self.lbl_subtitle.width()
            sub_h = self.lbl_subtitle.height()
            self.lbl_subtitle.move((vid_w - sub_w) // 2, max(10, vid_h - sub_h - 20))

        # Reposition logo overlay
        if hasattr(self, "lbl_logo") and self.lbl_logo.isVisible():
            self._update_logo_display()
