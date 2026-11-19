"""
Video Player Component using PySide6 QtMultimedia (QGraphicsView + QGraphicsVideoItem)
F2: Preview, seek, play by segment
Features:
- Hardware-accelerated QGraphicsVideoItem in real video pixel coordinates (16:9 and 9:16)
- Live Logo / Watermark QGraphicsPixmapItem overlay with instant setScale/setPos/setOpacity (<5ms)
- Direct mouse drag + corner/center snapping + guide lines for Logo positioning
- Live Mask Band & Khmer Subtitle overlays in exact video pixel coordinates
- Live (Pre-render) vs Result (Exported) mode switcher + Stale Result badge
"""

import os
import subprocess
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QLabel, QStyle, QSizePolicy,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsLineItem, QFrame
)
from PySide6.QtCore import Qt, QUrl, Signal, QSizeF, QRectF
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from app.utils import format_timestamp
from app.dubbing.subtitle_renderer import logo_geometry


class DraggableLogoItem(QGraphicsPixmapItem):
    """QGraphicsPixmapItem supporting smooth mouse drag + snap-to-corner/center with guide lines."""

    def __init__(self, owner_widget):
        super().__init__()
        self._owner = owner_widget
        self._dragging = False
        self._grab_offset_x = 0.0
        self._grab_offset_y = 0.0
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            sp = event.scenePos()
            self._grab_offset_x = sp.x() - self.pos().x()
            self._grab_offset_y = sp.y() - self.pos().y()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            sp = event.scenePos()
            raw_x = sp.x() - self._grab_offset_x
            raw_y = sp.y() - self._grab_offset_y
            self._owner._handle_logo_drag(raw_x, raw_y, finished=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            sp = event.scenePos()
            raw_x = sp.x() - self._grab_offset_x
            raw_y = sp.y() - self._grab_offset_y
            self._owner._handle_logo_drag(raw_x, raw_y, finished=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VideoPreviewGraphicsView(QGraphicsView):
    """Responsive QGraphicsView keeping video aspect ratio (16:9, 9:16, etc.) on every resize."""

    def __init__(self, scene: QGraphicsScene, owner_widget, parent=None):
        super().__init__(scene, parent)
        self._owner = owner_widget
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setStyleSheet("background-color: #080C16; border-radius: 8px;")

    def aspectRatioMode(self):
        return self._owner.video_item.aspectRatioMode()

    def fit_video(self):
        rect = self.sceneRect()
        if rect.isValid() and rect.width() > 0 and rect.height() > 0:
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_video()
        self._owner._position_drop_zone()


class VideoPlayerWidget(QWidget):
    position_changed = Signal(float)      # Current time in seconds
    preview_requested = Signal()          # Request generating fast preview
    rerender_requested = Signal()         # Request re-rendering final export when stale
    video_dropped = Signal(str)           # Emitted when user drags & drops a video file
    browse_requested = Signal()           # Emitted when user clicks empty drop zone
    logo_dragged = Signal(str, float, float)  # (pos_name_or_custom, custom_x_pct, custom_y_pct)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lang = "km"
        self.setAcceptDrops(True)
        self.segment_end_time = None
        self.original_video_path = ""
        self.result_video_path = ""
        self.preview_video_path = ""
        self.is_final_export = False
        self._result_stale = False
        self.current_mode = "live"  # "live" (or alias "original") vs "result"

        self._video_w = 1280
        self._video_h = 720
        self._cached_logo_path = ""
        self._cached_logo_pixmap: Optional[QPixmap] = None
        self._current_sub_text = ""

        self.logo_config = {
            "enabled": False,
            "path": "",
            "position": "top_right",
            "width_pct": 12.0,
            "margin_pct": 2.5,
            "opacity": 0.90,
            "custom_x_pct": -1.0,
            "custom_y_pct": -1.0,
        }
        self.mask_config = {
            "enabled": False,
            "x_pct": 0.0,
            "y_pct": 83.0,
            "w_pct": 100.0,
            "h_pct": 12.0,
            "opacity": 0.95,
            "color": "black",
            "style": "solid",
        }
        self.sub_config = {
            "enabled": True,
            "font_size": 20,
            "margin_v": 25,
        }

        self._init_ui()
        self._setup_player()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Mode Switcher Bar: Live (Pre-render) vs Result + Stale Re-render badge
        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(6)

        self.btn_mode_original = QPushButton("🎬 Live (មុន render)")
        self.btn_mode_original.setToolTip("ចាក់វីដេអូដើមជាមួយ Overlay ផ្ទាល់ (Logo / Mask / Subtitle)")
        self.btn_mode_original.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt;"
        )
        self.btn_mode_original.clicked.connect(lambda: self.switch_mode("live"))
        mode_bar.addWidget(self.btn_mode_original)

        self.btn_mode_result = QPushButton("✨ Result")
        self.btn_mode_result.setToolTip("ចាក់ឯកសារ Result ដែលបាន render រួច")
        self.btn_mode_result.setStyleSheet(
            "background-color: #334155; color: #94a3b8; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt;"
        )
        self.btn_mode_result.clicked.connect(lambda: self.switch_mode("result"))
        mode_bar.addWidget(self.btn_mode_result)

        self.btn_refresh_preview = QPushButton("🔄")
        self.btn_refresh_preview.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #0284c7;"
        )
        self.btn_refresh_preview.setToolTip("បង្កើត និងផ្ទុកវីដេអូ Preview ឡើងវិញ (Regenerate Fast Preview)")
        self.btn_refresh_preview.clicked.connect(self.preview_requested.emit)
        mode_bar.addWidget(self.btn_refresh_preview)

        self.btn_rerender_stale = QPushButton("⚠️ Result ហួសសម័យ • Render ឡើងវិញ")
        self.btn_rerender_stale.setToolTip("ការកំណត់ Logo/អក្សរបានផ្លាស់ប្តូរក្រោយ Export — ចុចដើម្បី render ឡើងវិញ")
        self.btn_rerender_stale.setStyleSheet(
            "background-color: #78350F; color: #FDE68A; border: 1px solid #F59E0B; font-weight: bold; padding: 3px 8px; border-radius: 4px; font-size: 8pt;"
        )
        self.btn_rerender_stale.clicked.connect(self.rerender_requested.emit)
        self.btn_rerender_stale.setVisible(False)
        mode_bar.addWidget(self.btn_rerender_stale)

        mode_bar.addStretch()

        self.lbl_mode_status = QLabel("● Live")
        self.lbl_mode_status.setStyleSheet("color: #38bdf8; font-size: 8.5pt; font-weight: bold;")
        mode_bar.addWidget(self.lbl_mode_status)

        layout.addLayout(mode_bar)

        # QGraphicsView + QGraphicsVideoItem Scene (Real video pixels)
        self.scene = QGraphicsScene(0, 0, self._video_w, self._video_h, self)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video_item.setSize(QSizeF(self._video_w, self._video_h))
        self.video_item.setZValue(0)
        self.scene.addItem(self.video_item)

        # Overlay 1: Mask Band preview (optional)
        self.mask_rect_item = QGraphicsRectItem()
        self.mask_rect_item.setZValue(10)
        self.mask_rect_item.setPen(QPen(Qt.PenStyle.NoPen))
        self.mask_rect_item.hide()
        self.scene.addItem(self.mask_rect_item)

        # Overlay 2: Live Khmer Subtitle item
        self.subtitle_item = QGraphicsTextItem()
        self.subtitle_item.setZValue(20)
        self.subtitle_item.setDefaultTextColor(QColor("#FEF08A"))
        self.subtitle_item.hide()
        self.scene.addItem(self.subtitle_item)

        # Overlay 3: Interactive Draggable Logo item
        self.logo_item = DraggableLogoItem(self)
        self.logo_item.setZValue(30)
        self.logo_item.hide()
        self.scene.addItem(self.logo_item)

        # Overlay 4: Snap Alignment Guide lines (shown while dragging logo near snap targets)
        guide_pen = QPen(QColor("#38BDF8"), 2.0, Qt.PenStyle.DashLine)
        guide_pen.setCosmetic(True)
        self.snap_vline = QGraphicsLineItem()
        self.snap_vline.setPen(guide_pen)
        self.snap_vline.setZValue(40)
        self.snap_vline.hide()
        self.scene.addItem(self.snap_vline)

        self.snap_hline = QGraphicsLineItem()
        self.snap_hline.setPen(guide_pen)
        self.snap_hline.setZValue(40)
        self.snap_hline.hide()
        self.scene.addItem(self.snap_hline)

        self.video_view = VideoPreviewGraphicsView(self.scene, self)
        self.video_view.setMinimumSize(260, 160)
        self.video_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_view.fit_video()
        # Compatibility alias for layout tests/references
        self.video_widget = self.video_view
        layout.addWidget(self.video_view, stretch=1)

        # Empty State Drag & Drop Zone over video surface
        self.btn_drop_zone = QPushButton(
            "🎬 ទម្លាក់វីដេអូនៅទីនេះ ឬចុចដើម្បីជ្រើស\n(.mp4, .mkv, .avi, .mov)"
            if self.lang == "km" else
            "🎬 Drop video here or click to browse\n(.mp4, .mkv, .avi, .mov)",
            self.video_view
        )
        self.btn_drop_zone.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drop_zone.setStyleSheet("""
            QPushButton {
                background-color: rgba(22, 32, 54, 0.85);
                color: #9AA8C4;
                border: 2px dashed #243350;
                border-radius: 12px;
                font-size: 10pt;
                font-weight: 600;
                padding: 16px;
            }
            QPushButton:hover {
                border-color: #2D8CFF;
                color: #E6ECF8;
                background-color: rgba(45, 140, 255, 0.12);
            }
        """)
        self.btn_drop_zone.clicked.connect(self.browse_requested.emit)
        self.btn_drop_zone.show()

        # Controls Container (Play/Stop/Seek/Volume)
        controls_layout = QVBoxLayout()
        controls_layout.setSpacing(4)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_seek)
        controls_layout.addWidget(self.slider)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setMinimumWidth(34)
        self.btn_play.clicked.connect(self.toggle_play)
        btn_row.addWidget(self.btn_play)

        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.setMinimumWidth(34)
        self.btn_stop.clicked.connect(self.stop)
        btn_row.addWidget(self.btn_stop)

        self.lbl_time = QLabel("00:00:00 / 00:00:00")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 9pt; color: #94a3b8;")
        btn_row.addWidget(self.lbl_time)

        btn_row.addStretch()

        lbl_vol = QLabel("🔊")
        btn_row.addWidget(lbl_vol)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setMinimumWidth(55)
        self.vol_slider.setMaximumWidth(100)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        btn_row.addWidget(self.vol_slider)

        controls_layout.addLayout(btn_row)
        layout.addLayout(controls_layout)

    def _setup_player(self):
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_item)
        self.audio_output.setVolume(0.8)

        self.video_item.nativeSizeChanged.connect(self._on_native_size_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def _probe_video_dimensions(self, video_path: str) -> tuple[int, int]:
        try:
            cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0", video_path,
            ]
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            txt = (res.stdout or "").strip()
            if "x" in txt:
                parts = txt.split("x")
                w, h = int(parts[0]), int(parts[1])
                if w > 0 and h > 0:
                    return (w, h)
        except Exception:
            pass
        return (self._video_w, self._video_h)

    def _apply_scene_video_size(self, vw: int, vh: int):
        vw = max(16, int(vw))
        vh = max(16, int(vh))
        self._video_w = vw
        self._video_h = vh
        self.scene.setSceneRect(0, 0, vw, vh)
        self.video_item.setSize(QSizeF(vw, vh))
        self.video_view.fit_video()
        self._update_mask_display()
        self._update_subtitle_display()
        self._update_logo_display()

    def _on_native_size_changed(self, size: QSizeF):
        if size.isValid() and size.width() > 1 and size.height() > 1:
            self._apply_scene_video_size(int(round(size.width())), int(round(size.height())))

    def load_video(self, video_path: str):
        """Load video file for playback and set exact scene pixel dimensions."""
        if self.current_mode in ("live", "original"):
            self.original_video_path = video_path
        if hasattr(self, "btn_drop_zone") and video_path:
            self.btn_drop_zone.hide()

        if video_path and os.path.exists(video_path):
            vw, vh = self._probe_video_dimensions(video_path)
            self._apply_scene_video_size(vw, vh)

        url = QUrl.fromLocalFile(video_path)
        self.player.setSource(url)
        self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.btn_mode_result.setEnabled(True)
        self.btn_refresh_preview.setEnabled(True)

    def clear_video(self):
        self.stop()
        self.original_video_path = ""
        self.result_video_path = ""
        self.preview_video_path = ""
        self.is_final_export = False
        self._result_stale = False
        self.btn_rerender_stale.setVisible(False)
        self.player.setSource(QUrl())
        if hasattr(self, "btn_drop_zone"):
            self.btn_drop_zone.show()
            self._position_drop_zone()

    def set_language(self, lang: str):
        self.lang = lang
        if hasattr(self, "btn_drop_zone"):
            self.btn_drop_zone.setText(
                "🎬 ទម្លាក់វីដេអូនៅទីនេះ ឬចុចដើម្បីជ្រើស\n(.mp4, .mkv, .avi, .mov)"
                if lang == "km" else
                "🎬 Drop video here or click to browse\n(.mp4, .mkv, .avi, .mov)"
            )
        if hasattr(self, "btn_mode_original"):
            self.btn_mode_original.setText("🎬 Live (មុន render)" if lang == "km" else "🎬 Live (Preview)")
        if hasattr(self, "btn_mode_result"):
            self.btn_mode_result.setText("✨ Result")
        if hasattr(self, "btn_rerender_stale"):
            self.btn_rerender_stale.setText(
                "⚠️ Result ហួសសម័យ • Render ឡើងវិញ" if lang == "km" else "⚠️ Result outdated • Re-render"
            )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile() and u.toLocalFile().lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm")):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile() and u.toLocalFile().lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm")):
                    self.video_dropped.emit(u.toLocalFile())
                    event.acceptProposedAction()
                    return

    def set_preview_video(self, preview_path: str):
        """Set fast-muxed preview video (before full export) and start preview playback."""
        self.preview_video_path = preview_path
        self.result_video_path = preview_path
        self.is_final_export = False
        self.mark_result_stale(False)
        if preview_path and os.path.exists(preview_path):
            self.btn_mode_result.setEnabled(True)
            self.switch_mode("result")

    def set_result_video(self, result_path: str):
        """Set final exported video (with burned-in elements)."""
        self.result_video_path = result_path
        self.is_final_export = True
        self.mark_result_stale(False)
        if result_path and os.path.exists(result_path):
            self.btn_mode_result.setEnabled(True)
            self.switch_mode("result")

    def mark_result_stale(self, stale: bool = True):
        """Show/hide stale badge when settings change after a final export."""
        has_exported = bool(self.is_final_export and self.result_video_path and os.path.exists(self.result_video_path))
        self._result_stale = bool(stale and has_exported)
        if hasattr(self, "btn_rerender_stale"):
            self.btn_rerender_stale.setVisible(self._result_stale)

    def ensure_live_mode(self):
        """Automatically switch back to Live preview mode so overlays respond visibly."""
        if self.current_mode == "result" and self.original_video_path and os.path.exists(self.original_video_path):
            self.switch_mode("live")

    def switch_mode(self, mode: str):
        """Switch preview between Live (original + scene overlays) and Result (rendered MP4)."""
        norm_mode = "live" if mode in ("live", "original") else "result"
        pos_ms = self.player.position()
        was_playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

        if norm_mode == "result":
            target_path = self.result_video_path or self.preview_video_path
            if not target_path or not os.path.exists(target_path):
                self.preview_requested.emit()
                return

            self.current_mode = "result"
            self.load_video(target_path)
            self.btn_mode_result.setStyleSheet(
                "background-color: #7c3aed; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt;"
            )
            self.btn_mode_original.setStyleSheet(
                "background-color: #334155; color: #94a3b8; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt;"
            )

            if self.is_final_export:
                self.lbl_mode_status.setText("● Result (Rendered)")
                self.lbl_mode_status.setStyleSheet("color: #34d399; font-size: 8.5pt; font-weight: bold;")
            else:
                self.lbl_mode_status.setText("● Preview 🎧")
                self.lbl_mode_status.setStyleSheet("color: #c084fc; font-size: 8.5pt; font-weight: bold;")
        else:
            self.current_mode = "live"
            if self.original_video_path and os.path.exists(self.original_video_path):
                self.load_video(self.original_video_path)
            self.btn_mode_original.setStyleSheet(
                "background-color: #0284c7; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt;"
            )
            self.btn_mode_result.setStyleSheet(
                "background-color: #334155; color: #94a3b8; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt;"
            )
            self.lbl_mode_status.setText("● Live")
            self.lbl_mode_status.setStyleSheet("color: #38bdf8; font-size: 8.5pt; font-weight: bold;")

        self._update_mask_display()
        self._update_subtitle_display()
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
        self.player.setPosition(int(seconds * 1000))

    def play_segment(self, start_sec: float, end_sec: float):
        self.seek_to_second(start_sec)
        self.segment_end_time = end_sec
        self.player.play()

    def get_current_time(self) -> float:
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

    def _overlays_burned_in(self) -> bool:
        return bool(self.current_mode == "result" and self.is_final_export)

    # --- Live Mask Overlay ---
    def set_mask_config(
        self,
        enabled: bool,
        x_pct: float = 0.0,
        y_pct: float = 83.0,
        w_pct: float = 100.0,
        h_pct: float = 12.0,
        opacity: float = 0.95,
        color: str = "black",
        style: str = "solid",
    ):
        self.mask_config = {
            "enabled": enabled,
            "x_pct": x_pct,
            "y_pct": y_pct,
            "w_pct": w_pct,
            "h_pct": h_pct,
            "opacity": opacity,
            "color": color,
            "style": style,
        }
        self._update_mask_display()

    def _update_mask_display(self):
        if self._overlays_burned_in() or not self.mask_config.get("enabled", False):
            self.mask_rect_item.hide()
            return
        mc = self.mask_config
        vw, vh = self._video_w, self._video_h
        x = vw * (mc["x_pct"] / 100.0)
        y = vh * (mc["y_pct"] / 100.0)
        w = vw * (mc["w_pct"] / 100.0)
        h = vh * (mc["h_pct"] / 100.0)
        self.mask_rect_item.setRect(x, y, w, h)
        if mc.get("style", "solid") == "blur":
            self.mask_rect_item.setBrush(QBrush(QColor(15, 23, 42, 145)))
            self.mask_rect_item.setPen(QPen(QColor(56, 189, 248, 120), 2.0, Qt.PenStyle.DashLine))
        else:
            col = QColor(mc.get("color", "black"))
            if not col.isValid():
                col = QColor(0, 0, 0)
            col.setAlphaF(max(0.15, min(1.0, float(mc.get("opacity", 0.95)))))
            self.mask_rect_item.setBrush(QBrush(col))
            self.mask_rect_item.setPen(QPen(Qt.PenStyle.NoPen))
        self.mask_rect_item.show()
        self._update_subtitle_display()

    # --- Live Subtitle Overlay ---
    def set_subtitle_config(self, enabled: bool = True, font_size: int = 20, margin_v: int = 25):
        self.sub_config = {
            "enabled": enabled,
            "font_size": font_size,
            "margin_v": margin_v,
        }
        self._update_subtitle_display()

    def set_subtitle_text(self, text: str):
        self._current_sub_text = (text or "").strip()
        self._update_subtitle_display()

    def _update_subtitle_display(self):
        if self._overlays_burned_in() or not self.sub_config.get("enabled", True) or not self._current_sub_text:
            self.subtitle_item.hide()
            return

        vw, vh = self._video_w, self._video_h
        fs_px = max(10, int(round(self.sub_config.get("font_size", 20) * (vh / 720.0) * 0.85)))
        mv_px = int(round(self.sub_config.get("margin_v", 25) * (vh / 720.0)))

        font = QFont("Noto Sans Khmer", fs_px)
        font.setPixelSize(fs_px)
        font.setBold(True)
        self.subtitle_item.setFont(font)
        self.subtitle_item.setPlainText(self._current_sub_text)

        mc = self.mask_config
        if mc.get("enabled", False) and mc.get("w_pct", 100.0) < 99.9:
            box_x = vw * (mc.get("x_pct", 0.0) / 100.0)
            box_w = vw * (mc.get("w_pct", 100.0) / 100.0)
            self.subtitle_item.setTextWidth(max(80.0, box_w - 16.0))
            br = self.subtitle_item.boundingRect()
            x = box_x + (box_w - br.width()) / 2.0
        else:
            self.subtitle_item.setTextWidth(max(120, vw - 40))
            br = self.subtitle_item.boundingRect()
            x = (vw - br.width()) / 2.0

        y = max(10.0, vh - br.height() * 0.82 - mv_px)
        self.subtitle_item.setPos(x, y)
        self.subtitle_item.show()

    # --- Live Logo Overlay (setScale / setPos / setOpacity only, < 50ms) ---
    def set_logo(
        self,
        enabled: bool,
        path: str,
        position: str = "top_right",
        width_pct: float = 12.0,
        opacity: float = 0.90,
        margin_pct: float = 2.5,
        custom_x_pct: float = -1.0,
        custom_y_pct: float = -1.0,
        **_legacy_kwargs,
    ):
        """Configure live logo overlay using video-relative percentages and fast item transforms."""
        self.logo_config = {
            "enabled": enabled,
            "path": path or "",
            "position": position or "top_right",
            "width_pct": float(width_pct),
            "opacity": float(opacity),
            "margin_pct": float(margin_pct),
            "custom_x_pct": float(custom_x_pct),
            "custom_y_pct": float(custom_y_pct),
        }
        self._update_logo_display()

    def _get_cached_logo_pixmap(self, path: str) -> Optional[QPixmap]:
        if not path or not os.path.exists(path):
            self._cached_logo_path = ""
            self._cached_logo_pixmap = None
            return None
        if path != self._cached_logo_path or self._cached_logo_pixmap is None:
            pix = QPixmap(path)
            if pix.isNull():
                return None
            self._cached_logo_path = path
            self._cached_logo_pixmap = pix
        return self._cached_logo_pixmap

    def _update_logo_display(self):
        """Instant live transform update using logo_geometry() without re-scaling pixmap."""
        if self._overlays_burned_in():
            self.logo_item.hide()
            return

        cfg = self.logo_config
        if not cfg["enabled"] or not cfg["path"]:
            self.logo_item.hide()
            return

        pix = self._get_cached_logo_pixmap(cfg["path"])
        if pix is None or pix.isNull() or pix.width() <= 0 or pix.height() <= 0:
            self.logo_item.hide()
            return

        if self.logo_item.pixmap().cacheKey() != pix.cacheKey():
            self.logo_item.setPixmap(pix)

        x, y, w, h = logo_geometry(
            self._video_w,
            self._video_h,
            pix.width(),
            pix.height(),
            pos=cfg["position"],
            width_pct=cfg["width_pct"],
            margin_pct=cfg["margin_pct"],
            custom_x_pct=cfg.get("custom_x_pct", -1.0),
            custom_y_pct=cfg.get("custom_y_pct", -1.0),
        )
        scale_factor = w / float(pix.width())
        self.logo_item.setScale(scale_factor)
        self.logo_item.setPos(float(x), float(y))
        self.logo_item.setOpacity(max(0.05, min(1.0, cfg["opacity"])))
        self.logo_item.show()

    def _handle_logo_drag(self, raw_x: float, raw_y: float, finished: bool):
        """Drag logo inside scene pixels, snap to corners/center, show alignment guides."""
        pix = self._cached_logo_pixmap
        if pix is None or pix.isNull():
            return

        vw, vh = self._video_w, self._video_h
        _, _, w, h = logo_geometry(
            vw, vh, pix.width(), pix.height(),
            pos="top_left",
            width_pct=self.logo_config["width_pct"],
            margin_pct=self.logo_config["margin_pct"],
        )
        m = int(round(min(vw, vh) * (max(0.0, min(10.0, self.logo_config["margin_pct"])) / 100.0)))
        snap_tol = max(14.0, min(vw, vh) * 0.03)

        cx = max(0.0, min(float(vw - w), raw_x))
        cy = max(0.0, min(float(vh - h), raw_y))

        x_targets = [("left", float(m)), ("center_x", (vw - w) / 2.0), ("right", float(vw - w - m))]
        y_targets = [("top", float(m)), ("center_y", (vh - h) / 2.0), ("bottom", float(vh - h - m))]

        snap_x_tag = None
        for tag, tx in x_targets:
            if abs(cx - tx) <= snap_tol:
                cx = tx
                snap_x_tag = tag
                break

        snap_y_tag = None
        for tag, ty in y_targets:
            if abs(cy - ty) <= snap_tol:
                cy = ty
                snap_y_tag = tag
                break

        # Show / hide guide lines while dragging
        if not finished and snap_x_tag is not None:
            gx = (cx + w / 2.0) if snap_x_tag == "center_x" else (cx if snap_x_tag == "left" else cx + w)
            self.snap_vline.setLine(gx, 0, gx, vh)
            self.snap_vline.show()
        else:
            self.snap_vline.hide()

        if not finished and snap_y_tag is not None:
            gy = (cy + h / 2.0) if snap_y_tag == "center_y" else (cy if snap_y_tag == "top" else cy + h)
            self.snap_hline.setLine(0, gy, vw, gy)
            self.snap_hline.show()
        else:
            self.snap_hline.hide()

        self.logo_item.setPos(cx, cy)

        # Map snapped corners/center back to named presets
        corner_map = {
            ("left", "top"): "top_left",
            ("right", "top"): "top_right",
            ("left", "bottom"): "bottom_left",
            ("right", "bottom"): "bottom_right",
            ("center_x", "center_y"): "center",
        }
        named_pos = corner_map.get((snap_x_tag, snap_y_tag))
        if named_pos:
            self.logo_config["position"] = named_pos
            self.logo_config["custom_x_pct"] = -1.0
            self.logo_config["custom_y_pct"] = -1.0
            if finished:
                self.logo_dragged.emit(named_pos, -1.0, -1.0)
        else:
            xp = (cx / max(1, vw)) * 100.0
            yp = (cy / max(1, vh)) * 100.0
            self.logo_config["position"] = "custom"
            self.logo_config["custom_x_pct"] = xp
            self.logo_config["custom_y_pct"] = yp
            if finished:
                self.logo_dragged.emit("custom", xp, yp)

    def _position_drop_zone(self):
        if hasattr(self, "btn_drop_zone") and self.btn_drop_zone.isVisible():
            vw = max(200, self.video_view.width())
            vh = max(140, self.video_view.height())
            dw = min(vw - 32, 420)
            dh = min(vh - 32, 160)
            self.btn_drop_zone.resize(dw, dh)
            self.btn_drop_zone.move((vw - dw) // 2, (vh - dh) // 2)
            self.btn_drop_zone.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_drop_zone()
        if hasattr(self, "video_view"):
            self.video_view.fit_video()
