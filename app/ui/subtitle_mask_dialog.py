"""
Interactive Subtitle Masking & Positioning Dialog.
Provides a live video frame preview where the user can visually adjust, drag, and fine-tune:
- Masking band position: Both Vertical (Y %) and Horizontal (X % / ឆ្វេង-ស្ដាំ), height (%), width (%), opacity
- 2D Drag & Drop on the preview canvas (free movement in any direction: up, down, left, right)
- Khmer subtitle font size and vertical margin (MarginV)
- Presets for horizontal (16:9) and vertical Douyin/TikTok (9:16) video formats.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, List

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QPushButton, QCheckBox, QGroupBox,
    QSpinBox, QScrollArea, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QFontDatabase,
    QTextLayout, QTextOption, QPixmap, QImage, QPainterPath
)

from app.models import ProjectSettings, Segment
from app.config import TEMP_DIR, get_resource_path


def _load_bundled_khmer_font() -> str:
    """Load bundled Noto Sans Khmer TTF fonts via QFontDatabase so preview matches export."""
    family = "Noto Sans Khmer"
    try:
        for rel_path in ("assets/fonts/NotoSansKhmer-Regular.ttf", "assets/fonts/NotoSansKhmer-Bold.ttf"):
            fpath = get_resource_path(rel_path)
            if fpath.exists():
                fid = QFontDatabase.addApplicationFont(str(fpath))
                if fid >= 0:
                    fams = QFontDatabase.applicationFontFamilies(fid)
                    if fams:
                        family = fams[0]
    except Exception as e:
        print(f"[SubtitleMaskDialog] Font load warning: {e}")
    return family


class SubtitleMaskCanvas(QWidget):
    """
    Interactive canvas displaying the video frame with the overlaid mask band
    and sample Khmer subtitles. Supports 2D dragging (X & Y) directly on the frame.
    """
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 320)
        self.setStyleSheet("background-color: #0b0f19; border-radius: 8px;")

        self.frame_pixmap: Optional[QPixmap] = None
        self.mask_enabled: bool = True
        self.mask_y_pct: float = 83.0
        self.mask_x_pct: float = 0.0
        self.mask_h_pct: float = 12.0
        self.mask_w_pct: float = 100.0
        self.mask_opacity: float = 0.95
        self.mask_color: str = "black"
        self.font_size: int = 20
        self.margin_v: int = 25

        self.dragging: bool = False
        self.drag_offset_x: int = 0
        self.drag_offset_y: int = 0

        self.sample_khmer_text: str = "«គាំងហុង និង ប៉ោសូវ បានមកដល់ហើយ»"
        self.sample_chinese_text: str = "这是一条原版中文字幕 (Sample Chinese Subtitles)"
        self.font_family: str = _load_bundled_khmer_font()
        self.video_height: int = 720
        self.last_wrapped_lines: int = 1

    def set_frame(self, pixmap: Optional[QPixmap]):
        self.frame_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            self.video_height = max(1, pixmap.height())
        self.update()

    def update_params(
        self,
        enabled: bool,
        y_pct: float,
        h_pct: float,
        w_pct: float,
        x_pct: float,
        opacity: float,
        font_size: int,
        margin_v: int,
        color: str = "black"
    ):
        self.mask_enabled = enabled
        self.mask_y_pct = max(30.0, min(98.0, y_pct))
        self.mask_h_pct = max(3.0, min(35.0, h_pct))
        self.mask_w_pct = max(20.0, min(100.0, w_pct))
        max_x = max(0.0, 100.0 - self.mask_w_pct)
        self.mask_x_pct = max(0.0, min(max_x, x_pct))
        self.mask_opacity = max(0.1, min(1.0, opacity))
        self.font_size = max(10, min(48, font_size))
        self.margin_v = max(0, min(250, margin_v))
        self.mask_color = color
        self.update()

    def _get_video_rect(self) -> QRect:
        """Calculate the aspect-fitted target rect inside this widget."""
        w = self.width()
        h = self.height()
        if not self.frame_pixmap or self.frame_pixmap.isNull():
            target_aspect = 16.0 / 9.0
        else:
            target_aspect = self.frame_pixmap.width() / max(1, self.frame_pixmap.height())

        widget_aspect = w / max(1, h)
        if widget_aspect > target_aspect:
            # Letterbox left & right
            vh = h - 20
            vw = int(vh * target_aspect)
        else:
            # Letterbox top & bottom
            vw = w - 20
            vh = int(vw / target_aspect)

        vx = (w - vw) // 2
        vy = (h - vh) // 2
        return QRect(vx, vy, vw, vh)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        v_rect = self._get_video_rect()

        # 1. Draw Video Frame or Simulated Video Canvas
        if self.frame_pixmap and not self.frame_pixmap.isNull():
            scaled = self.frame_pixmap.scaled(
                v_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            painter.drawPixmap(v_rect.topLeft(), scaled)
        else:
            # Simulated dark video background
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1e293b"))
            painter.drawRoundedRect(v_rect, 6, 6)

            painter.setPen(QPen(QColor("#334155"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(v_rect.left(), v_rect.center().y(), v_rect.right(), v_rect.center().y())
            painter.drawLine(v_rect.center().x(), v_rect.top(), v_rect.center().x(), v_rect.bottom())

            painter.setPen(QColor("#64748b"))
            f_hint = QFont("Inter", 11)
            painter.setFont(f_hint)
            painter.drawText(v_rect, Qt.AlignmentFlag.AlignCenter, "🎥 ផ្ទាំងវីដេអូគំរូ (Video Frame)")

            # Draw sample Chinese subtitle near bottom
            zh_y = v_rect.top() + int(v_rect.height() * 0.84)
            painter.setPen(QColor("#ffffff"))
            f_zh = QFont("SimHei, Microsoft YaHei", 12)
            f_zh.setBold(True)
            painter.setFont(f_zh)
            zh_rect = QRect(v_rect.left(), zh_y, v_rect.width(), 26)
            painter.drawText(zh_rect, Qt.AlignmentFlag.AlignCenter, self.sample_chinese_text)

        # 2. Draw Masking Band (if enabled)
        if self.mask_enabled:
            band_y = v_rect.top() + int(v_rect.height() * (self.mask_y_pct / 100.0))
            band_h = max(6, int(v_rect.height() * (self.mask_h_pct / 100.0)))
            band_w = int(v_rect.width() * (self.mask_w_pct / 100.0))
            band_x = v_rect.left() + int(v_rect.width() * (self.mask_x_pct / 100.0))

            band_rect = QRect(band_x, band_y, band_w, band_h)

            # Opacity
            alpha = int(self.mask_opacity * 255)
            mask_col = QColor(0, 0, 0, alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(mask_col))
            painter.drawRect(band_rect)

            # Draw cyan adjustment border with grab handles
            painter.setPen(QPen(QColor("#38bdf8"), 1.8, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(band_rect)

            # Corner handles to indicate it's draggable
            handle_col = QColor("#0284c7")
            painter.setBrush(QBrush(handle_col))
            painter.setPen(QPen(QColor("#ffffff"), 1))
            hw = 6
            painter.drawRect(band_rect.left() - 2, band_rect.top() - 2, hw, hw)
            painter.drawRect(band_rect.right() - 4, band_rect.top() - 2, hw, hw)
            painter.drawRect(band_rect.left() - 2, band_rect.bottom() - 4, hw, hw)
            painter.drawRect(band_rect.right() - 4, band_rect.bottom() - 4, hw, hw)

            # Coordinates Badge
            badge_w = 170
            badge_h = 17
            badge_x = min(band_rect.left() + 4, v_rect.right() - badge_w)
            badge_y = max(v_rect.top() + 2, band_rect.top() - 19)
            badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#0284c7"))
            painter.drawRoundedRect(badge_rect, 3, 3)

            painter.setPen(QColor("#ffffff"))
            f_badge = QFont("Inter", 8)
            f_badge.setBold(True)
            painter.setFont(f_badge)
            painter.drawText(
                badge_rect,
                Qt.AlignmentFlag.AlignCenter,
                f"X: {self.mask_x_pct:.1f}% | Y: {self.mask_y_pct:.1f}%"
            )

        # 3. Draw Sample Khmer Subtitle (WYSIWYG 1:1 matching with FFmpeg/libass PlayRes & word-wrapping)
        vid_h = self.video_height if self.video_height > 0 else 720
        scale = v_rect.height() / float(vid_h)
        actual_fs = max(12, int(self.font_size * (vid_h / 720.0)))
        actual_mv = int(self.margin_v * (vid_h / 720.0))

        preview_font_px = max(4, round(actual_fs * scale))
        preview_margin_v = actual_mv * scale

        f_sub = QFont(self.font_family)
        f_sub.setPixelSize(preview_font_px)
        f_sub.setBold(True)
        painter.setFont(f_sub)
        fm = QFontMetrics(f_sub)

        # Horizontal alignment & wrap width matching MarginL / MarginR inside mask box
        if self.mask_enabled and self.mask_w_pct < 99.9:
            band_x = v_rect.left() + int(v_rect.width() * (self.mask_x_pct / 100.0))
            band_w = int(v_rect.width() * (self.mask_w_pct / 100.0))
            center_x = band_x + (band_w / 2.0)
            wrap_width = max(40.0, float(band_w - 8))
        else:
            center_x = float(v_rect.center().x())
            wrap_width = max(40.0, float(v_rect.width() - 20))

        text = self.sample_khmer_text or "«គាំងហុង និង ប៉ោសូវ បានមកដល់ហើយ»"
        text_layout = QTextLayout(text, f_sub)
        t_opt = QTextOption(Qt.AlignmentFlag.AlignHCenter)
        t_opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        text_layout.setTextOption(t_opt)
        text_layout.beginLayout()
        wrapped_lines = []
        while True:
            ln = text_layout.createLine()
            if not ln.isValid():
                break
            ln.setLineWidth(wrap_width)
            wrapped_lines.append(ln)
        text_layout.endLayout()

        self.last_wrapped_lines = max(1, len(wrapped_lines))
        total_h = sum(ln.height() for ln in wrapped_lines)
        block_top_y = v_rect.bottom() - preview_margin_v - total_h

        path = QPainterPath()
        cur_y = 0.0
        for ln in wrapped_lines:
            ln_txt = text[ln.textStart():ln.textStart() + ln.textLength()].strip()
            if ln_txt:
                ln_w = fm.horizontalAdvance(ln_txt)
                ln_x = center_x - (ln_w / 2.0)
                baseline_y = block_top_y + cur_y + ln.ascent()
                path.addText(ln_x, baseline_y, f_sub, ln_txt)
            cur_y += ln.height()

        # Shadow (matching ASS Shadow=1.0)
        shadow_offset = max(0.8, 1.0 * (vid_h / 720.0) * scale)
        shadow_path = QPainterPath(path)
        shadow_path.translate(shadow_offset, shadow_offset)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawPath(shadow_path)

        # Black outline (matching ASS Outline=2.5)
        outline_w = max(1.2, 2.5 * (vid_h / 720.0) * scale * 1.4)
        painter.setPen(QPen(QColor(0, 0, 0, 240), outline_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # White foreground text
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawPath(path)

    # Full 2D Mouse drag interactions: Move both X (left-right) and Y (up-down)
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.mask_enabled:
            v_rect = self._get_video_rect()
            if v_rect.contains(event.pos()):
                self.dragging = True
                band_y = v_rect.top() + int(v_rect.height() * (self.mask_y_pct / 100.0))
                band_h = max(6, int(v_rect.height() * (self.mask_h_pct / 100.0)))
                band_w = int(v_rect.width() * (self.mask_w_pct / 100.0))
                band_x = v_rect.left() + int(v_rect.width() * (self.mask_x_pct / 100.0))
                band_rect = QRect(band_x, band_y, band_w, band_h)

                if band_rect.contains(event.pos()):
                    self.drag_offset_x = event.pos().x() - band_x
                    self.drag_offset_y = event.pos().y() - band_y
                else:
                    # Clicked outside band: center the band on click
                    self.drag_offset_x = band_w // 2
                    self.drag_offset_y = band_h // 2
                    self._update_xy_from_pos(event.pos().x(), event.pos().y(), v_rect)

    def mouseMoveEvent(self, event):
        if self.dragging and self.mask_enabled:
            v_rect = self._get_video_rect()
            self._update_xy_from_pos(event.pos().x(), event.pos().y(), v_rect)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False

    def _update_xy_from_pos(self, mouse_x: int, mouse_y: int, v_rect: QRect):
        # 1. Update X (left-right)
        band_w = int(v_rect.width() * (self.mask_w_pct / 100.0))
        new_x = mouse_x - getattr(self, "drag_offset_x", band_w // 2)
        rel_x = new_x - v_rect.left()
        x_pct = (rel_x / max(1, v_rect.width())) * 100.0
        max_x_pct = max(0.0, 100.0 - self.mask_w_pct)
        self.mask_x_pct = max(0.0, min(max_x_pct, x_pct))

        # 2. Update Y (up-down)
        band_h = max(6, int(v_rect.height() * (self.mask_h_pct / 100.0)))
        new_y = mouse_y - getattr(self, "drag_offset_y", band_h // 2)
        rel_y = new_y - v_rect.top()
        y_pct = (rel_y / max(1, v_rect.height())) * 100.0
        max_y_pct = max(35.0, 100.0 - self.mask_h_pct)
        self.mask_y_pct = max(35.0, min(max_y_pct, y_pct))

        self.update()
        self.settings_changed.emit()


class SubtitleMaskDialog(QDialog):
    """
    Pop-up dialog allowing the user to tune, reposition (X & Y), and preview
    the subtitle masking band and Khmer subtitle font & margin.
    """

    def __init__(
        self,
        settings: ProjectSettings,
        video_path: str = "",
        current_time: float = 2.0,
        segments: Optional[List[Segment]] = None,
        lang: str = "km",
        parent=None
    ):
        super().__init__(parent)
        self.settings = settings
        self.video_path = video_path
        self.current_time = max(0.5, current_time)
        self.segments = segments or []
        self.lang = lang

        # 16ms QTimer throttle for smooth 60fps preview updates without FFmpeg re-render
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(16)
        self._preview_timer.timeout.connect(self._flush_preview_update)
        self._pending_preview_params: Optional[dict] = None

        title = "⚙️ សារ៉េទីតាំងផ្ទាំងបិទបាំងអក្សរចិនដើម (Subtitle Mask & Position)" if lang == "km" else "⚙️ Subtitle Mask & Positioning"
        self.setWindowTitle(title)
        self.resize(920, 670)
        self.setStyleSheet("""
            QDialog {
                background-color: #0f172a;
                color: #f8fafc;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 10px;
                font-weight: bold;
                color: #38bdf8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QLabel {
                color: #cbd5e1;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #334155;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #0284c7;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #38bdf8;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QPushButton {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid #475569;
                padding: 6px 12px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38bdf8;
            }
        """)

        self._init_ui()
        self._update_sample_text_for_time()
        self._load_current_frame()
        self._sync_ui_from_settings()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        # Header description
        lbl_info = QLabel(
            "លោកអ្នកអាចទាញ Mouse ផ្ទាល់លើផ្ទាំងវីដេអូ ឬរំកិល Slider ដើម្បីសារ៉េទីតាំង (ឆ្វេង-ស្ដាំ លើ-ក្រោម) កម្ពស់ និងទទឹងផ្ទាំងបាំង។"
            if self.lang == "km" else
            "Drag directly on the video canvas or use sliders to adjust position (X & Y), height, width, and opacity."
        )
        lbl_info.setStyleSheet("color: #94a3b8; font-size: 12px;")
        main_layout.addWidget(lbl_info)

        # Split: Left (Canvas) + Right (Controls)
        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # --- Left Column: Canvas Preview ---
        left_box = QVBoxLayout()
        left_box.setSpacing(8)

        self.canvas = SubtitleMaskCanvas(self)
        self.canvas.settings_changed.connect(self._sync_sliders_from_canvas)
        left_box.addWidget(self.canvas, stretch=1)

        # Canvas toolbar (Capture frame & Presets)
        bar_capture = QHBoxLayout()
        self.btn_capture = QPushButton("📸 ថតរូបពីវីដេអូបច្ចុប្បន្ន" if self.lang == "km" else "📸 Grab Video Frame")
        self.btn_capture.clicked.connect(self._load_current_frame)
        bar_capture.addWidget(self.btn_capture)

        self.btn_time_prev = QPushButton("⏪ -5s")
        self.btn_time_prev.clicked.connect(lambda: self._seek_frame(-5.0))
        bar_capture.addWidget(self.btn_time_prev)

        self.btn_time_next = QPushButton("⏩ +5s")
        self.btn_time_next.clicked.connect(lambda: self._seek_frame(5.0))
        bar_capture.addWidget(self.btn_time_next)

        bar_capture.addStretch()
        left_box.addLayout(bar_capture)

        content_layout.addLayout(left_box, stretch=3)

        # --- Right Column: Sliders & Settings ---
        right_box = QVBoxLayout()
        right_box.setSpacing(10)

        # Group 1: Subtitle Masking Band
        grp_mask = QGroupBox("១. ផ្ទាំងបិទបាំងអក្សរចិន (Subtitle Mask Band)" if self.lang == "km" else "1. Subtitle Mask Band")
        lay_mask = QVBoxLayout(grp_mask)
        lay_mask.setSpacing(7)

        self.chk_mask = QCheckBox("☑️ បើកដំណើរការផ្ទាំងបាំង (Enable Mask Band)" if self.lang == "km" else "Enable Mask Band")
        self.chk_mask.setStyleSheet("color: #38bdf8; font-weight: bold;")
        self.chk_mask.toggled.connect(self._on_values_changed)
        lay_mask.addWidget(self.chk_mask)

        # 1. Y Position Slider (Up - Down)
        row_y = QHBoxLayout()
        row_y.addWidget(QLabel("ទីតាំងបញ្ឈរ (Y លើ-ក្រោម):" if self.lang == "km" else "Vertical Y (Up-Down):"))
        self.lbl_y_val = QLabel("83.0%")
        self.lbl_y_val.setStyleSheet("color: #38bdf8; font-weight: bold; width: 55px;")
        row_y.addWidget(self.lbl_y_val)
        lay_mask.addLayout(row_y)

        self.slider_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_y.setRange(350, 980)  # 35.0% to 98.0%
        self.slider_y.setValue(830)
        self.slider_y.valueChanged.connect(self._on_values_changed)
        lay_mask.addWidget(self.slider_y)

        # 2. X Position Slider (Left - Right)
        row_x = QHBoxLayout()
        row_x.addWidget(QLabel("ទីតាំងផ្ដេក (X ឆ្វេង-ស្ដាំ):" if self.lang == "km" else "Horizontal X (Left-Right):"))
        self.lbl_x_val = QLabel("0.0%")
        self.lbl_x_val.setStyleSheet("color: #38bdf8; font-weight: bold; width: 55px;")
        row_x.addWidget(self.lbl_x_val)
        lay_mask.addLayout(row_x)

        self.slider_x = QSlider(Qt.Orientation.Horizontal)
        self.slider_x.setRange(0, 1000)  # 0.0% to 100.0%
        self.slider_x.setValue(0)
        self.slider_x.valueChanged.connect(self._on_values_changed)
        lay_mask.addWidget(self.slider_x)

        # Quick Center Horizontally Button
        btn_center_x = QPushButton("🎯 ដាក់ផ្ទាំងបាំងចំកណ្ដាល (Center Horizontally)" if self.lang == "km" else "🎯 Center Mask Horizontally")
        btn_center_x.setStyleSheet("background-color: #0c4a6e; color: #38bdf8; padding: 4px; font-size: 11px;")
        btn_center_x.clicked.connect(self._center_mask_horizontally)
        lay_mask.addWidget(btn_center_x)

        # 3. Height Slider
        row_h = QHBoxLayout()
        row_h.addWidget(QLabel("កម្ពស់ផ្ទាំងបាំង (Height):" if self.lang == "km" else "Band Height:"))
        self.lbl_h_val = QLabel("12.0%")
        self.lbl_h_val.setStyleSheet("color: #38bdf8; font-weight: bold; width: 55px;")
        row_h.addWidget(self.lbl_h_val)
        lay_mask.addLayout(row_h)

        self.slider_h = QSlider(Qt.Orientation.Horizontal)
        self.slider_h.setRange(30, 300)  # 3.0% to 30.0%
        self.slider_h.setValue(120)
        self.slider_h.valueChanged.connect(self._on_values_changed)
        lay_mask.addWidget(self.slider_h)

        # 4. Width Slider
        row_w = QHBoxLayout()
        row_w.addWidget(QLabel("ទទឹង (Width):" if self.lang == "km" else "Band Width:"))
        self.lbl_w_val = QLabel("100%")
        self.lbl_w_val.setStyleSheet("color: #38bdf8; font-weight: bold; width: 55px;")
        row_w.addWidget(self.lbl_w_val)
        lay_mask.addLayout(row_w)

        self.slider_w = QSlider(Qt.Orientation.Horizontal)
        self.slider_w.setRange(20, 100)
        self.slider_w.setValue(100)
        self.slider_w.valueChanged.connect(self._on_width_slider_changed)
        lay_mask.addWidget(self.slider_w)

        # 5. Opacity Slider
        row_op = QHBoxLayout()
        row_op.addWidget(QLabel("កម្រិតងងឹត (Opacity):" if self.lang == "km" else "Mask Opacity:"))
        self.lbl_op_val = QLabel("95%")
        self.lbl_op_val.setStyleSheet("color: #38bdf8; font-weight: bold; width: 55px;")
        row_op.addWidget(self.lbl_op_val)
        lay_mask.addLayout(row_op)

        self.slider_op = QSlider(Qt.Orientation.Horizontal)
        self.slider_op.setRange(30, 100)
        self.slider_op.setValue(95)
        self.slider_op.valueChanged.connect(self._on_values_changed)
        lay_mask.addWidget(self.slider_op)

        right_box.addWidget(grp_mask)

        # Group 2: Khmer Subtitle Styling & Margin
        grp_sub = QGroupBox("២. អក្សរខ្មែរ (Khmer Subtitle)" if self.lang == "km" else "2. Khmer Subtitle")
        lay_sub = QVBoxLayout(grp_sub)
        lay_sub.setSpacing(7)

        # Font Size
        row_fs = QHBoxLayout()
        row_fs.addWidget(QLabel("ទំហំអក្សរ (Font Size):" if self.lang == "km" else "Font Size:"))
        self.lbl_fs_val = QLabel("20 px")
        self.lbl_fs_val.setStyleSheet("color: #fbbf24; font-weight: bold;")
        row_fs.addWidget(self.lbl_fs_val)
        lay_sub.addLayout(row_fs)

        self.slider_fs = QSlider(Qt.Orientation.Horizontal)
        self.slider_fs.setRange(12, 36)
        self.slider_fs.setValue(20)
        self.slider_fs.valueChanged.connect(self._on_values_changed)
        lay_sub.addWidget(self.slider_fs)

        # Margin Bottom
        row_mb = QHBoxLayout()
        row_mb.addWidget(QLabel("គម្លាតពីបាត (Margin V):" if self.lang == "km" else "Bottom Margin:"))
        self.lbl_mb_val = QLabel("25 px")
        self.lbl_mb_val.setStyleSheet("color: #fbbf24; font-weight: bold;")
        row_mb.addWidget(self.lbl_mb_val)
        lay_sub.addLayout(row_mb)

        self.slider_mb = QSlider(Qt.Orientation.Horizontal)
        self.slider_mb.setRange(5, 250)
        self.slider_mb.setValue(25)
        self.slider_mb.valueChanged.connect(self._on_margin_slider_changed)
        lay_sub.addWidget(self.slider_mb)

        # Auto-lock Center Checkbox
        self.chk_lock_center = QCheckBox("🔒 តម្រឹមចំកណ្ដាលប្រអប់ស្វ័យប្រវត្តិ (Lock in Center)" if self.lang == "km" else "🔒 Auto-lock Subtitle in Center")
        self.chk_lock_center.setStyleSheet("color: #38bdf8; font-weight: bold;")
        self.chk_lock_center.setChecked(True)
        self.chk_lock_center.toggled.connect(self._on_lock_center_toggled)
        lay_sub.addWidget(self.chk_lock_center)

        btn_auto_center = QPushButton("🎯 ដាក់អក្សរចំកណ្ដាលផ្ទាំងបាំង" if self.lang == "km" else "🎯 Center Text in Mask")
        btn_auto_center.setStyleSheet("background-color: #0369a1; color: white; padding: 4px; font-size: 11px;")
        btn_auto_center.clicked.connect(self._on_center_btn_clicked)
        lay_sub.addWidget(btn_auto_center)

        right_box.addWidget(grp_sub)

        # Group 3: Quick Presets
        grp_presets = QGroupBox("៣. ជម្រើសរហ័ស (Quick Presets)" if self.lang == "km" else "3. Quick Presets")
        lay_pres = QGridLayout(grp_presets)
        lay_pres.setSpacing(6)

        btn_pre_169 = QPushButton("🎬 ស្ដង់ដារ (16:9)")
        btn_pre_169.clicked.connect(lambda: self._apply_preset(83.0, 12.0, 100.0, 0.95, 25, 0.0))
        lay_pres.addWidget(btn_pre_169, 0, 0)

        btn_pre_916 = QPushButton("📱 បញ្ឈរ Douyin (9:16)")
        btn_pre_916.clicked.connect(lambda: self._apply_preset(76.0, 14.0, 100.0, 0.95, 80, 0.0))
        lay_pres.addWidget(btn_pre_916, 0, 1)

        btn_pre_solid = QPushButton("⬛ ខ្មៅជិតឈឹង (100%)")
        btn_pre_solid.clicked.connect(lambda: self.slider_op.setValue(100))
        lay_pres.addWidget(btn_pre_solid, 1, 0)

        btn_pre_reset = QPushButton("🔄 កំណត់ឡើងវិញ")
        btn_pre_reset.clicked.connect(lambda: self._apply_preset(83.0, 12.0, 100.0, 0.95, 25, 0.0))
        lay_pres.addWidget(btn_pre_reset, 1, 1)

        right_box.addWidget(grp_presets)
        right_box.addStretch()

        content_layout.addLayout(right_box, stretch=2)
        main_layout.addLayout(content_layout)

        # --- Bottom Action Buttons ---
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self.btn_cancel = QPushButton("បោះបង់" if self.lang == "km" else "Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("💾 រក្សាទុក និងអនុវត្ត" if self.lang == "km" else "💾 Save & Apply")
        self.btn_save.setStyleSheet("background-color: #059669; color: white; padding: 7px 18px; font-size: 13px;")
        self.btn_save.clicked.connect(self._save_and_apply)
        btn_bar.addWidget(self.btn_save)

        main_layout.addLayout(btn_bar)

    def _sync_ui_from_settings(self):
        s = self.settings
        self.chk_mask.setChecked(getattr(s, "mask_subtitles", True))

        y_val = int(getattr(s, "mask_y_percent", 83.0) * 10)
        self.slider_y.setValue(y_val)

        h_val = int(getattr(s, "mask_height_percent", 12.0) * 10)
        self.slider_h.setValue(h_val)

        w_val = int(getattr(s, "mask_width_percent", 100.0))
        self.slider_w.setValue(w_val)

        max_x = max(0.0, 100.0 - w_val)
        self.slider_x.setRange(0, int(max_x * 10))

        x_val = getattr(s, "mask_x_percent", 0.0)
        if x_val <= 0.01 and w_val < 99:
            x_val = max_x / 2.0
        self.slider_x.setValue(int(x_val * 10))

        op_val = int(getattr(s, "mask_opacity", 0.95) * 100)
        self.slider_op.setValue(op_val)

        fs_val = int(getattr(s, "subtitle_font_size", 20))
        self.slider_fs.setValue(fs_val)

        mb_val = int(getattr(s, "subtitle_margin_v", 25))
        self.slider_mb.setValue(mb_val)

        self._on_values_changed()

    def _on_width_slider_changed(self):
        """When width changes, ensure X slider range is updated and stay centered if needed."""
        w = float(self.slider_w.value())
        max_x = max(0.0, 100.0 - w)
        self.slider_x.blockSignals(True)
        self.slider_x.setRange(0, int(max_x * 10))
        cur_x = self.slider_x.value() / 10.0
        # If cur_x was at 0, or exceeds max_x, auto center it
        if cur_x <= 0.01 or cur_x > max_x:
            new_x = max_x / 2.0
            self.slider_x.setValue(int(new_x * 10))
        self.slider_x.blockSignals(False)
        self._on_values_changed()

    def _center_mask_horizontally(self):
        """Center the mask band horizontally."""
        w = float(self.slider_w.value())
        center_x = max(0.0, (100.0 - w) / 2.0)
        self.slider_x.setValue(int(center_x * 10))

    def _on_margin_slider_changed(self):
        # If user drags margin manually, uncheck lock center so custom offset is preserved
        if not getattr(self, "_auto_centering", False):
            if hasattr(self, "chk_lock_center") and self.chk_lock_center.isChecked():
                self.chk_lock_center.blockSignals(True)
                self.chk_lock_center.setChecked(False)
                self.chk_lock_center.blockSignals(False)
        self._on_values_changed()

    def _on_lock_center_toggled(self, checked: bool):
        if checked:
            self._auto_center_subtitle()
            self._on_values_changed()

    def _on_center_btn_clicked(self):
        if hasattr(self, "chk_lock_center"):
            self.chk_lock_center.blockSignals(True)
            self.chk_lock_center.setChecked(True)
            self.chk_lock_center.blockSignals(False)
        self._auto_center_subtitle()
        self._on_values_changed()

    def _on_values_changed(self):
        enabled = self.chk_mask.isChecked()
        y_pct = self.slider_y.value() / 10.0
        x_pct = self.slider_x.value() / 10.0
        h_pct = self.slider_h.value() / 10.0
        w_pct = float(self.slider_w.value())
        op = self.slider_op.value() / 100.0
        fs = self.slider_fs.value()

        if getattr(self, "chk_lock_center", None) and self.chk_lock_center.isChecked() and not getattr(self, "_auto_centering", False):
            self._auto_center_subtitle()

        mb = self.slider_mb.value()

        self.lbl_y_val.setText(f"{y_pct:.1f}%")
        self.lbl_x_val.setText(f"{x_pct:.1f}%")
        self.lbl_h_val.setText(f"{h_pct:.1f}%")
        self.lbl_w_val.setText(f"{int(w_pct)}%")
        self.lbl_op_val.setText(f"{int(op * 100)}%")
        self.lbl_fs_val.setText(f"{fs} px")
        self.lbl_mb_val.setText(f"{mb} px")

        self._pending_preview_params = dict(
            enabled=enabled,
            y_pct=y_pct,
            h_pct=h_pct,
            w_pct=w_pct,
            x_pct=x_pct,
            opacity=op,
            font_size=fs,
            margin_v=mb
        )
        # Throttle canvas repaint at ~16ms (60fps) without calling FFmpeg
        if not self._preview_timer.isActive():
            self._flush_preview_update()
            self._preview_timer.start()

    def _flush_preview_update(self):
        if self._pending_preview_params:
            params = self._pending_preview_params
            self._pending_preview_params = None
            self.canvas.update_params(**params)

    def _sync_sliders_from_canvas(self):
        self.slider_y.blockSignals(True)
        self.slider_y.setValue(int(self.canvas.mask_y_pct * 10))
        self.lbl_y_val.setText(f"{self.canvas.mask_y_pct:.1f}%")
        self.slider_y.blockSignals(False)

        self.slider_x.blockSignals(True)
        self.slider_x.setValue(int(self.canvas.mask_x_pct * 10))
        self.lbl_x_val.setText(f"{self.canvas.mask_x_pct:.1f}%")
        self.slider_x.blockSignals(False)

        if getattr(self, "chk_lock_center", None) and self.chk_lock_center.isChecked():
            self._auto_center_subtitle()

        self._on_values_changed()

    def _auto_center_subtitle(self):
        """Calculate optimal MarginV to center the wrapped Khmer text directly inside the mask band."""
        self._auto_centering = True
        try:
            y_pct = self.slider_y.value() / 10.0
            h_pct = self.slider_h.value() / 10.0
            fs = self.slider_fs.value()
            lines = max(1, getattr(self.canvas, "last_wrapped_lines", 1))
            text_block_h = fs * 1.2 * lines
            center_from_bottom_pct = 100.0 - (y_pct + (h_pct / 2.0))
            computed_margin = max(5, int((center_from_bottom_pct / 100.0) * 720.0 - (text_block_h / 2.0)))
            self.slider_mb.setValue(min(250, max(5, computed_margin)))
        finally:
            self._auto_centering = False

    def _apply_preset(self, y: float, h: float, w: float, op: float, mb: int, x: float = 0.0):
        self.slider_w.setValue(int(w))
        max_x = max(0.0, 100.0 - w)
        self.slider_x.setRange(0, int(max_x * 10))
        if x <= 0.01 and w < 99:
            x = max_x / 2.0
        self.slider_x.setValue(int(x * 10))
        self.slider_y.setValue(int(y * 10))
        self.slider_h.setValue(int(h * 10))
        self.slider_op.setValue(int(op * 100))
        if hasattr(self, "chk_lock_center"):
            self.chk_lock_center.blockSignals(True)
            self.chk_lock_center.setChecked(True)
            self.chk_lock_center.blockSignals(False)
        self._auto_center_subtitle()
        self._on_values_changed()

    def _update_sample_text_for_time(self):
        """Use real Khmer text of segment at current_time, or longest segment text."""
        chosen = ""
        if self.segments:
            for seg in self.segments:
                txt = (getattr(seg, "text_km", "") or "").strip()
                if txt and seg.start <= self.current_time <= seg.end:
                    chosen = txt
                    break
            if not chosen:
                km_texts = [(getattr(seg, "text_km", "") or "").strip() for seg in self.segments]
                km_texts = [t for t in km_texts if t]
                if km_texts:
                    chosen = max(km_texts, key=len)
        if chosen:
            self.canvas.sample_khmer_text = chosen
        else:
            self.canvas.sample_khmer_text = "«គាំងហុង និង ប៉ោសូវ បានមកដល់ហើយ»"

    def _seek_frame(self, delta: float):
        self.current_time = max(0.5, self.current_time + delta)
        self._update_sample_text_for_time()
        self._load_current_frame()

    def _load_current_frame(self):
        """Extract a single frame from the video using FFmpeg and load into canvas."""
        if not self.video_path or not os.path.exists(self.video_path):
            self.canvas.set_frame(None)
            return

        out_frame = TEMP_DIR / "mask_preview_frame.jpg"
        out_frame.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{self.current_time:.2f}",
            "-i", self.video_path,
            "-vframes", "1",
            "-q:v", "2",
            str(out_frame)
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4.0)
            if res.returncode == 0 and out_frame.exists():
                pix = QPixmap(str(out_frame))
                if not pix.isNull():
                    self.canvas.set_frame(pix)
                    return
        except Exception as e:
            print(f"[SubtitleMaskDialog] Note frame capture: {e}")

        self.canvas.set_frame(None)

    def _save_and_apply(self):
        """Save settings to ProjectSettings and close."""
        self.settings.mask_subtitles = self.chk_mask.isChecked()
        self.settings.mask_y_percent = self.slider_y.value() / 10.0
        self.settings.mask_x_percent = self.slider_x.value() / 10.0
        self.settings.mask_height_percent = self.slider_h.value() / 10.0
        self.settings.mask_width_percent = float(self.slider_w.value())
        self.settings.mask_opacity = self.slider_op.value() / 100.0
        self.settings.subtitle_font_size = self.slider_fs.value()
        self.settings.subtitle_margin_v = self.slider_mb.value()
        self.accept()
