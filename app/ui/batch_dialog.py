"""
Batch Folder Processing Dialog (v1.4.0)
Processes an entire folder of episode videos sequentially in background worker:
- Natural sorting (EP1, EP2, EP10)
- Check/uncheck, reorder, remove items
- Selective steps, Whisper/OCR/OpenAI & Google/ChatGPT/Gemini engine selection
- Skip completed vs Overwrite policy
- Shared cross-episode Glossary editor
- Persistent JSON queue recovery (DATA_DIR/batch_queue.json)
- Start, Pause, Resume, Cancel, Retry Failed, Elapsed/ETA, Token/Cost estimate, Live Logs
"""

import copy
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from app.batch_engine import (
    BATCH_STATE_FILE, BatchItem, BatchJobConfig, BatchPipelineWorker,
    ST_DONE, ST_FAILED, ST_PENDING, ST_SKIPPED, apply_audio_settings_dict,
    clear_batch_state, detect_audio_separation_info, extract_audio_settings_dict,
    extract_mask_settings_dict, format_audio_summary, format_duration_hms,
    format_mask_summary, has_audio_stream, load_batch_state,
    render_batch_audio_preview, resolve_episode_settings, save_batch_state,
    scan_video_folder
)
from app.config import STRINGS
from app.models import GlossaryItem, ProjectSettings
from app.openai_client import OpenAIUsageStats, estimate_transcription_cost
from app.ui.subtitle_mask_dialog import SubtitleMaskDialog
from app.ui.theme import SegmentedControl, ToggleSwitch, fit_dialog_to_screen


class _FrameCaptureThread(QThread):
    """Non-blocking background FFmpeg single-frame reader for Batch mask preview."""
    frame_ready = Signal(int, QImage)

    def __init__(self, req_id: int, video_path: str, seek_sec: float, parent=None):
        super().__init__(parent)
        self.req_id = req_id
        self.video_path = video_path
        self.seek_sec = max(0.2, float(seek_sec))

    def run(self):
        img = QImage()
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="batch_mask_frame_", suffix=".jpg")
            os.close(fd)
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{self.seek_sec:.2f}",
                "-i", self.video_path,
                "-vframes", "1",
                "-q:v", "2",
                tmp_path,
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5.0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if res.returncode == 0 and os.path.exists(tmp_path):
                img.load(tmp_path)
        except Exception:
            img = QImage()
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        self.frame_ready.emit(self.req_id, img)


class BatchSubtitleMaskDialog(SubtitleMaskDialog):
    """
    Reuses existing SubtitleMaskDialog UI/logic inside Batch Mode without modifying
    the single-video dialog, while extracting real frames off the UI thread and
    respecting the selected batch video duration.
    """

    def __init__(
        self,
        settings: ProjectSettings,
        video_path: str = "",
        current_time: float = 2.0,
        duration_sec: float = 0.0,
        lang: str = "km",
        parent=None,
    ):
        self._video_duration_hint = max(0.0, float(duration_sec or 0.0))
        self._frame_req_id = 0
        self._frame_thread: Optional[_FrameCaptureThread] = None
        super().__init__(
            settings=settings,
            video_path=video_path,
            current_time=current_time,
            segments=None,
            lang=lang,
            parent=parent,
        )

    def _probe_duration(self) -> float:
        if getattr(self, "_video_duration_hint", 0.0) > 1.0:
            return float(self._video_duration_hint)
        return super()._probe_duration()

    def _load_current_frame(self):
        if not self.video_path or not os.path.exists(self.video_path):
            self.canvas.set_frame(None)
            return

        if not getattr(self, "_initial_frame_loaded", False):
            self._initial_frame_loaded = True
            if self.current_time <= 2.0:
                seek_init = min(8.0, max(2.0, self.video_duration * 0.15))
                self.current_time = round(seek_init, 2)
                if hasattr(self, "slider_seek"):
                    self.slider_seek.blockSignals(True)
                    self.slider_seek.setValue(int(self.current_time * 10))
                    self.slider_seek.blockSignals(False)
                    self.lbl_seek_time.setText(f"{self.current_time:.1f}s")

        self._frame_req_id += 1
        req_id = self._frame_req_id
        worker = _FrameCaptureThread(req_id, self.video_path, self.current_time, parent=self)
        worker.frame_ready.connect(self._on_async_frame_ready)
        self._frame_thread = worker
        worker.start()

    def _on_async_frame_ready(self, req_id: int, img: QImage):
        if req_id != self._frame_req_id:
            return
        if not img.isNull():
            pix = QPixmap.fromImage(img)
            if not pix.isNull():
                self.canvas.set_frame(pix)
                return
        self.canvas.set_frame(None)

    def closeEvent(self, event):
        if self._frame_thread and self._frame_thread.isRunning():
            self._frame_thread.wait(800)
        super().closeEvent(event)

    def done(self, result: int):
        if self._frame_thread and self._frame_thread.isRunning():
            self._frame_thread.wait(800)
        super().done(result)


class _AudioPreviewThread(QThread):
    """Background 10-15s audio mix preview builder so Batch UI never freezes."""
    preview_ready = Signal(str, str)   # wav_path, info_note
    preview_failed = Signal(str)

    def __init__(self, video_path: str, ep_settings: ProjectSettings, lang: str = "km", parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.ep_settings = copy.deepcopy(ep_settings)
        self.lang = lang

    def run(self):
        try:
            wav_path, note = render_batch_audio_preview(
                self.video_path, self.ep_settings, lang=self.lang, clip_seconds=12.0
            )
            self.preview_ready.emit(wav_path, note)
        except Exception as e:
            self.preview_failed.emit(str(e))


class BatchAudioMixWidget(QWidget):
    """Reusable Audio Mix control group shared by BatchQueueDialog and per-episode override dialog."""
    settings_changed = Signal()

    def __init__(self, lang: str = "km", parent=None):
        super().__init__(parent)
        self.lang = lang
        self._last_orig_pct = 30
        self._last_bg_pct = 30
        self._syncing = False
        self._init_ui()

    def _init_ui(self):
        s = STRINGS.get(self.lang, STRINGS["km"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Row 1: Original Voice toggle + slider (0-100%)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.sw_orig = ToggleSwitch(s["batch_audio_orig"])
        self.sw_orig.setMinimumWidth(120)
        self.sw_orig.toggled.connect(self._on_orig_toggled)
        r1.addWidget(self.sw_orig)

        self.slider_orig = QSlider(Qt.Orientation.Horizontal)
        self.slider_orig.setRange(0, 100)
        self.slider_orig.valueChanged.connect(self._on_orig_slider)
        r1.addWidget(self.slider_orig, stretch=1)

        self.lbl_orig = QLabel("OFF")
        self.lbl_orig.setMinimumWidth(42)
        self.lbl_orig.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_orig.setStyleSheet("color: #38bdf8; font-weight: bold;")
        r1.addWidget(self.lbl_orig)
        lay.addLayout(r1)

        # Row 2: Music / SFX toggle + slider (0-100%, default 30%)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.sw_bg = ToggleSwitch(s["batch_audio_bg"])
        self.sw_bg.setMinimumWidth(120)
        self.sw_bg.toggled.connect(self._on_bg_toggled)
        r2.addWidget(self.sw_bg)

        self.slider_bg = QSlider(Qt.Orientation.Horizontal)
        self.slider_bg.setRange(0, 100)
        self.slider_bg.setValue(30)
        self.slider_bg.valueChanged.connect(self._on_bg_slider)
        r2.addWidget(self.slider_bg, stretch=1)

        self.lbl_bg = QLabel("30%")
        self.lbl_bg.setMinimumWidth(42)
        self.lbl_bg.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_bg.setStyleSheet("color: #F59E0B; font-weight: bold;")
        r2.addWidget(self.lbl_bg)
        lay.addLayout(r2)

        # Separation backend indicator
        _, sep_info = detect_audio_separation_info(lang=self.lang)
        self.lbl_sep_info = QLabel(sep_info)
        self.lbl_sep_info.setStyleSheet("color: #9AA8C4; font-size: 8pt;")
        self.lbl_sep_info.setWordWrap(True)
        lay.addWidget(self.lbl_sep_info)

        # Row 3: AI Voice slider (0-150%, default 100%)
        r3 = QHBoxLayout()
        r3.setSpacing(8)
        lbl_ai_t = QLabel(s["batch_audio_ai"])
        lbl_ai_t.setMinimumWidth(120)
        lbl_ai_t.setStyleSheet("font-weight: 600;")
        r3.addWidget(lbl_ai_t)

        self.slider_ai = QSlider(Qt.Orientation.Horizontal)
        self.slider_ai.setRange(0, 150)
        self.slider_ai.setValue(100)
        self.slider_ai.valueChanged.connect(self._on_ai_slider)
        r3.addWidget(self.slider_ai, stretch=1)

        self.lbl_ai = QLabel("100%")
        self.lbl_ai.setMinimumWidth(42)
        self.lbl_ai.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_ai.setStyleSheet("color: #2D8CFF; font-weight: bold;")
        r3.addWidget(self.lbl_ai)
        lay.addLayout(r3)

        # Row 4: Speed presets + Auto-fit
        r4 = QHBoxLayout()
        r4.setSpacing(8)
        lbl_spd = QLabel(s["batch_audio_speed"])
        lbl_spd.setStyleSheet("color: #9AA8C4; font-weight: 600;")
        r4.addWidget(lbl_spd)

        self.seg_speed = SegmentedControl([
            ("0.9x", 0.90),
            ("1.0x", 1.00),
            ("1.05x", 1.05),
            ("1.15x", 1.15),
            ("1.25x", 1.25),
            ("Auto-fit", "auto_fit"),
        ])
        self.seg_speed.set_value(1.05, emit=False)
        self.seg_speed.selection_changed.connect(lambda _v: self._emit_changed())
        r4.addWidget(self.seg_speed, stretch=1)
        lay.addLayout(r4)

        # Row 5: Live Summary Badge + Both-Muted Warning
        r5 = QHBoxLayout()
        self.lbl_badge = QLabel()
        r5.addWidget(self.lbl_badge)
        self.lbl_warn = QLabel(f"⚠ {s['batch_audio_both_muted_warn']}")
        self.lbl_warn.setStyleSheet("color: #FCA5A5; font-size: 8.5pt;")
        self.lbl_warn.setVisible(False)
        r5.addWidget(self.lbl_warn)
        r5.addStretch()
        lay.addLayout(r5)

        self._refresh_badge()

    def _on_orig_toggled(self, checked: bool):
        if self._syncing:
            return
        self.slider_orig.blockSignals(True)
        self.slider_orig.setEnabled(checked)
        if checked:
            v = self._last_orig_pct if self._last_orig_pct > 0 else 30
            self.slider_orig.setValue(v)
            self.lbl_orig.setText(f"{v}%")
        else:
            if self.slider_orig.value() > 0:
                self._last_orig_pct = self.slider_orig.value()
            self.slider_orig.setValue(0)
            self.lbl_orig.setText("OFF")
        self.slider_orig.blockSignals(False)
        self._emit_changed()

    def _on_orig_slider(self, val: int):
        if self._syncing:
            return
        if val > 0:
            self._last_orig_pct = val
        self.lbl_orig.setText(f"{val}%" if val > 0 else "OFF")
        self.sw_orig.blockSignals(True)
        self.sw_orig.setChecked(val > 0)
        self.sw_orig.blockSignals(False)
        self._emit_changed()

    def _on_bg_toggled(self, checked: bool):
        if self._syncing:
            return
        self.slider_bg.blockSignals(True)
        self.slider_bg.setEnabled(checked)
        if checked:
            v = self._last_bg_pct if self._last_bg_pct > 0 else 30
            self.slider_bg.setValue(v)
            self.lbl_bg.setText(f"{v}%")
        else:
            if self.slider_bg.value() > 0:
                self._last_bg_pct = self.slider_bg.value()
            self.slider_bg.setValue(0)
            self.lbl_bg.setText("OFF")
        self.slider_bg.blockSignals(False)
        self._emit_changed()

    def _on_bg_slider(self, val: int):
        if self._syncing:
            return
        if val > 0:
            self._last_bg_pct = val
        self.lbl_bg.setText(f"{val}%" if val > 0 else "OFF")
        self.sw_bg.blockSignals(True)
        self.sw_bg.setChecked(val > 0)
        self.sw_bg.blockSignals(False)
        self._emit_changed()

    def _on_ai_slider(self, val: int):
        if self._syncing:
            return
        self.lbl_ai.setText(f"{val}%")
        self._emit_changed()

    def _emit_changed(self):
        self._refresh_badge()
        if not self._syncing:
            self.settings_changed.emit()

    def _refresh_badge(self):
        d = self.get_audio_dict()
        self.lbl_badge.setText(format_audio_summary(d))
        orig_on = d["orig_enabled"] and d["original_voice_volume"] > 0.001
        bg_on = d["bg_enabled"] and d["background_volume"] > 0.001
        both_off = (not orig_on) and (not bg_on)
        self.lbl_warn.setVisible(both_off)
        if both_off:
            self.lbl_badge.setStyleSheet("background: #3B1219; color: #FCA5A5; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 8pt;")
        elif not orig_on and bg_on:
            self.lbl_badge.setStyleSheet("background: #422006; color: #FDE047; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 8pt;")
        else:
            self.lbl_badge.setStyleSheet("background: #0F2E22; color: #34D399; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 8pt;")

    def get_audio_dict(self) -> Dict[str, Any]:
        orig_on = self.sw_orig.isChecked() and self.slider_orig.value() > 0
        bg_on = self.sw_bg.isChecked() and self.slider_bg.value() > 0
        spd_val = self.seg_speed.current_value()
        auto_fit = (spd_val == "auto_fit")
        spd = 1.05 if auto_fit else float(spd_val or 1.05)
        return {
            "orig_enabled": bool(orig_on),
            "original_voice_volume": round(self.slider_orig.value() / 100.0, 2) if orig_on else 0.0,
            "bg_enabled": bool(bg_on),
            "background_volume": round(self.slider_bg.value() / 100.0, 2) if bg_on else 0.0,
            "dubbed_volume": round(self.slider_ai.value() / 100.0, 2),
            "ai_voice_speed": spd,
            "auto_fit": auto_fit,
        }

    def set_from_audio_dict(self, data: Optional[Dict[str, Any]]):
        d = data or {}
        orig_v = int(round(min(1.0, max(0.0, float(d.get("original_voice_volume", 0.0)))) * 100))
        orig_on = bool(d.get("orig_enabled", orig_v > 0)) and orig_v > 0
        bg_v = int(round(min(1.0, max(0.0, float(d.get("background_volume", 0.30)))) * 100))
        bg_on = bool(d.get("bg_enabled", bg_v > 0)) and bg_v > 0
        ai_v = int(round(min(1.5, max(0.0, float(d.get("dubbed_volume", 1.0)))) * 100))
        auto_fit = bool(d.get("auto_fit", False))
        spd = float(d.get("ai_voice_speed", 1.05))

        self._syncing = True
        try:
            if orig_v > 0:
                self._last_orig_pct = orig_v
            if bg_v > 0:
                self._last_bg_pct = bg_v

            self.sw_orig.setChecked(orig_on)
            self.slider_orig.setEnabled(orig_on)
            self.slider_orig.setValue(orig_v if orig_on else 0)
            self.lbl_orig.setText(f"{orig_v}%" if orig_on else "OFF")

            self.sw_bg.setChecked(bg_on)
            self.slider_bg.setEnabled(bg_on)
            self.slider_bg.setValue(bg_v if bg_on else 0)
            self.lbl_bg.setText(f"{bg_v}%" if bg_on else "OFF")

            self.slider_ai.setValue(ai_v)
            self.lbl_ai.setText(f"{ai_v}%")

            if auto_fit:
                self.seg_speed.set_value("auto_fit", emit=False)
            else:
                nearest = min([0.90, 1.00, 1.05, 1.15, 1.25], key=lambda v: abs(v - spd))
                self.seg_speed.set_value(nearest, emit=False)
        finally:
            self._syncing = False
        self._refresh_badge()

    def set_from_settings(self, st: ProjectSettings, auto_fit: bool = False):
        self.set_from_audio_dict(extract_audio_settings_dict(st, auto_fit=auto_fit))


class BatchEpisodeAudioDialog(QDialog):
    """Per-episode Audio Mix override dialog launched from Batch table context menu."""

    def __init__(self, filename: str, audio_dict: Dict[str, Any], lang: str = "km", parent=None):
        super().__init__(parent)
        self.lang = lang
        self.setWindowTitle(f"🎛️ កំណត់សំឡេងផ្ទាល់៖ {filename}" if lang == "km" else f"Episode Audio Mix: {filename}")
        fit_dialog_to_screen(self, 620, 320)

        lay = QVBoxLayout(self)
        self.mix_widget = BatchAudioMixWidget(lang=lang, parent=self)
        self.mix_widget.set_from_audio_dict(audio_dict)
        lay.addWidget(self.mix_widget)
        lay.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        btn_c = QPushButton("បោះបង់" if lang == "km" else "Cancel")
        btn_c.clicked.connect(self.reject)
        btns.addWidget(btn_c)
        btn_ok = QPushButton("💾 រក្សាទុក" if lang == "km" else "💾 Save")
        btn_ok.setProperty("variant", "primary")
        btn_ok.clicked.connect(self.accept)
        btns.addWidget(btn_ok)
        lay.addLayout(btns)

    def result_audio_dict(self) -> Dict[str, Any]:
        return self.mix_widget.get_audio_dict()


class SharedGlossaryDialog(QDialog):
    """Lightweight editor for shared batch glossary terms (Chinese -> Khmer)."""

    def __init__(self, glossary_dicts: List[Dict[str, str]], lang: str = "km", parent=None):
        super().__init__(parent)
        self.lang = lang
        self.glossary_dicts = copy.deepcopy(glossary_dicts)
        self.setWindowTitle("📖 វចនានុក្រមរួម (Shared Batch Glossary)" if lang == "km" else "Shared Batch Glossary")
        fit_dialog_to_screen(self, 560, 420)
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        lbl = QLabel(
            "កំណត់ឈ្មោះតួអង្គ និងពាក្យពិសេស (ចិន → ខ្មែរ) ដើម្បីប្រើឱ្យដូចគ្នានៅគ្រប់ភាគទាំងអស់៖"
            if self.lang == "km" else
            "Define character names and special terms shared across all batch episodes:"
        )
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            ["ពាក្យចិន / ដើម (Source)", "ពាក្យខ្មែរ (Target)"] if self.lang == "km" else ["Source Term", "Khmer Term"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for item in self.glossary_dicts:
            self._append_row(item.get("src_term", ""), item.get("km_term", ""))
        root.addWidget(self.table, stretch=1)

        row_btns = QHBoxLayout()
        btn_add = QPushButton("➕ បន្ថែមជួរ" if self.lang == "km" else "➕ Add Term")
        btn_add.clicked.connect(lambda: self._append_row("", ""))
        row_btns.addWidget(btn_add)

        btn_del = QPushButton("🗑️ លុបជួរ" if self.lang == "km" else "🗑️ Remove Row")
        btn_del.clicked.connect(self._remove_selected)
        row_btns.addWidget(btn_del)
        row_btns.addStretch()

        btn_cancel = QPushButton("បោះបង់" if self.lang == "km" else "Cancel")
        btn_cancel.clicked.connect(self.reject)
        row_btns.addWidget(btn_cancel)

        btn_ok = QPushButton("💾 រក្សាទុក" if self.lang == "km" else "💾 Save")
        btn_ok.setProperty("variant", "primary")
        btn_ok.clicked.connect(self._save)
        row_btns.addWidget(btn_ok)
        root.addLayout(row_btns)

    def _append_row(self, src: str, km: str):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(src))
        self.table.setItem(r, 1, QTableWidgetItem(km))

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _save(self):
        out: List[Dict[str, str]] = []
        for r in range(self.table.rowCount()):
            s_item = self.table.item(r, 0)
            k_item = self.table.item(r, 1)
            src = (s_item.text() if s_item else "").strip()
            km = (k_item.text() if k_item else "").strip()
            if src and km:
                out.append({"src_term": src, "km_term": km})
        self.glossary_dicts = out
        self.accept()


class BatchQueueDialog(QDialog):
    def __init__(
        self,
        lang: str = "km",
        base_settings: Optional[ProjectSettings] = None,
        project_glossary: Optional[List[GlossaryItem]] = None,
        state_file: Optional[Path] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.lang = lang
        self._state_file = state_file or BATCH_STATE_FILE
        self.base_settings = copy.deepcopy(base_settings) if base_settings else ProjectSettings()
        self.config = BatchJobConfig(
            src_engine=getattr(self.base_settings, "src_mode", "auto") or "auto",
            trans_engine=getattr(self.base_settings, "translation_provider", "google") or "google",
            shared_glossary=[
                {"src_term": g.src_term, "km_term": g.km_term}
                for g in (project_glossary or [])
                if g.src_term and g.km_term
            ],
            mask_enabled=bool(getattr(self.base_settings, "mask_subtitles", True)),
            mask_mode="normal",
        )
        self.items: List[BatchItem] = []
        self.worker: Optional[BatchPipelineWorker] = None
        self._is_paused = False
        self._batch_start_ts: float = 0.0
        self._last_eta_sec: float = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed_timer)

        self._preview_worker: Optional[_AudioPreviewThread] = None
        self._audio_out: Optional[QAudioOutput] = None
        self._audio_player: Optional[QMediaPlayer] = None

        self.setWindowTitle("📦 ដំណើរការទាំង Folder (Batch Mode)" if lang == "km" else "Batch Folder Queue")
        fit_dialog_to_screen(self, 1080, 780)
        self._init_ui()
        self._restore_saved_queue()
        self._update_controls_state()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("scrollBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(8)

        # 1. Folder Selection Group
        grp_dir = QGroupBox("១. ជ្រើសរើស Folder ចូល និងចេញ" if self.lang == "km" else "1. Input & Output Folders")
        v_dir = QVBoxLayout(grp_dir)

        row_in = QHBoxLayout()
        row_in.addWidget(QLabel("ថតវីដេអូចូល:" if self.lang == "km" else "Input Folder:"))
        self.txt_in_dir = QLineEdit()
        self.txt_in_dir.setPlaceholderText("ជ្រើស folder ដែលមានភាគវីដេអូ (.mp4 .mkv .avi .mov)...")
        row_in.addWidget(self.txt_in_dir, stretch=1)
        self.chk_subfolders = QCheckBox("រួមទាំង subfolder" if self.lang == "km" else "Include subfolders")
        row_in.addWidget(self.chk_subfolders)
        btn_browse_in = QPushButton("📂 ជ្រើស..." if self.lang == "km" else "📂 Browse...")
        btn_browse_in.clicked.connect(self._browse_in_folder)
        row_in.addWidget(btn_browse_in)
        v_dir.addLayout(row_in)

        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("ថតលទ្ធផលចេញ:" if self.lang == "km" else "Output Folder:"))
        self.txt_out_dir = QLineEdit()
        self.txt_out_dir.setPlaceholderText("<output>/<ឈ្មោះ folder>/<ឈ្មោះវីដេអូ>...")
        row_out.addWidget(self.txt_out_dir, stretch=1)
        btn_browse_out = QPushButton("📂 ជ្រើស..." if self.lang == "km" else "📂 Browse...")
        btn_browse_out.clicked.connect(self._browse_out_folder)
        row_out.addWidget(btn_browse_out)

        self.btn_scan = QPushButton("🔍 ស្កេនវីដេអូ (Natural Sort)" if self.lang == "km" else "🔍 Scan Videos")
        self.btn_scan.setProperty("variant", "primary")
        self.btn_scan.clicked.connect(self._scan_folder)
        row_out.addWidget(self.btn_scan)
        v_dir.addLayout(row_out)
        layout.addWidget(grp_dir)

        # 2. Batch Options & Engines Group
        grp_opt = QGroupBox("២. ជម្រើសដំណើរការ និង Engine" if self.lang == "km" else "2. Pipeline Options & Engines")
        v_opt = QVBoxLayout(grp_opt)

        row_steps = QHBoxLayout()
        row_steps.addWidget(QLabel("ជំហាន:" if self.lang == "km" else "Steps:"))
        self.chk_step_extract = QCheckBox("ដកស្រង់អត្ថបទ")
        self.chk_step_extract.setChecked(True)
        self.chk_step_trans = QCheckBox("បកប្រែខ្មែរ")
        self.chk_step_trans.setChecked(True)
        self.chk_step_tts = QCheckBox("សំឡេង AI")
        self.chk_step_tts.setChecked(True)
        self.chk_step_render = QCheckBox("Render វីដេអូ")
        self.chk_step_render.setChecked(True)
        self.chk_step_srt = QCheckBox("Export SRT (.km / .zh)")
        self.chk_step_srt.setChecked(True)
        for cb in (self.chk_step_extract, self.chk_step_trans, self.chk_step_tts, self.chk_step_render, self.chk_step_srt):
            cb.toggled.connect(self._update_cost_estimate)
            row_steps.addWidget(cb)
        row_steps.addStretch()
        v_opt.addLayout(row_steps)

        row_eng = QHBoxLayout()
        row_eng.addWidget(QLabel("Engine ដកស្រង់:" if self.lang == "km" else "Transcription:"))
        self.cb_batch_src = QComboBox()
        self.cb_batch_src.addItem("🤖 Auto (OCR/Whisper)", "auto")
        self.cb_batch_src.addItem("👁️ Video OCR", "ocr")
        self.cb_batch_src.addItem("🎙️ Whisper", "whisper")
        self.cb_batch_src.addItem("💬 ChatGPT / OpenAI", "openai")
        idx_s = self.cb_batch_src.findData(self.config.src_engine)
        if idx_s >= 0:
            self.cb_batch_src.setCurrentIndex(idx_s)
        self.cb_batch_src.currentIndexChanged.connect(self._update_cost_estimate)
        row_eng.addWidget(self.cb_batch_src)

        row_eng.addWidget(QLabel("Engine បកប្រែ:" if self.lang == "km" else "Translation:"))
        self.cb_batch_trans = QComboBox()
        self.cb_batch_trans.addItem("🌐 Google Translate", "google")
        self.cb_batch_trans.addItem("💬 ChatGPT (OpenAI)", "openai")
        self.cb_batch_trans.addItem("✨ Gemini 1.5 Flash", "gemini")
        idx_t = self.cb_batch_trans.findData(self.config.trans_engine)
        if idx_t >= 0:
            self.cb_batch_trans.setCurrentIndex(idx_t)
        self.cb_batch_trans.currentIndexChanged.connect(self._update_cost_estimate)
        row_eng.addWidget(self.cb_batch_trans)

        row_eng.addWidget(QLabel("ឯកសារមានស្រាប់:" if self.lang == "km" else "Existing files:"))
        self.cb_conflict = QComboBox()
        self.cb_conflict.addItem("រំលងភាគដែលរួចរាល់ហើយ (Skip)", False)
        self.cb_conflict.addItem("សរសេរជាន់លើ (Overwrite)", True)
        row_eng.addWidget(self.cb_conflict)

        self.btn_glossary = QPushButton(f"📖 Glossary រួម ({len(self.config.shared_glossary)})")
        self.btn_glossary.clicked.connect(self._edit_shared_glossary)
        row_eng.addWidget(self.btn_glossary)
        row_eng.addStretch()
        v_opt.addLayout(row_eng)

        row_meta = QHBoxLayout()
        self.chk_sound_done = QCheckBox("សំឡេង & សារជូនដំណឹងពេល Batch ចប់" if self.lang == "km" else "Notify + sound on finish")
        self.chk_sound_done.setChecked(True)
        row_meta.addWidget(self.chk_sound_done)
        row_meta.addStretch()
        self.lbl_cost_est = QLabel("")
        self.lbl_cost_est.setStyleSheet("color: #38BDF8; font-weight: 600;")
        row_meta.addWidget(self.lbl_cost_est)
        v_opt.addLayout(row_meta)
        layout.addWidget(grp_opt)

        # 3. Original Chinese Subtitle Mask Group (Reusing SubtitleMaskDialog)
        s = STRINGS.get(self.lang, STRINGS["km"])
        grp_mask = QGroupBox(s["batch_mask_group"])
        v_mask = QVBoxLayout(grp_mask)
        v_mask.setSpacing(6)

        row_m1 = QHBoxLayout()
        row_m1.setSpacing(12)
        self.chk_batch_mask = ToggleSwitch(s["batch_mask_enable"])
        self.chk_batch_mask.setChecked(self.config.mask_enabled)
        self.chk_batch_mask.toggled.connect(self._on_batch_mask_toggled)
        row_m1.addWidget(self.chk_batch_mask)

        self.rb_mask_normal = QRadioButton(s["batch_mask_mode_normal"])
        self.rb_mask_normal.setChecked(True)
        self.rb_mask_custom = QRadioButton(s["batch_mask_mode_custom"])
        self.rb_mask_custom.setMinimumWidth(235)
        self._mask_btn_group = QButtonGroup(self)
        self._mask_btn_group.addButton(self.rb_mask_normal)
        self._mask_btn_group.addButton(self.rb_mask_custom)
        self.rb_mask_normal.toggled.connect(self._on_mask_mode_changed)
        row_m1.addWidget(self.rb_mask_normal)
        row_m1.addWidget(self.rb_mask_custom)
        row_m1.addStretch()
        v_mask.addLayout(row_m1)

        row_m2 = QHBoxLayout()
        self.btn_batch_mask_pos = QPushButton(s["batch_mask_btn"])
        self.btn_batch_mask_pos.clicked.connect(self._open_batch_mask_dialog)
        row_m2.addWidget(self.btn_batch_mask_pos)

        self.lbl_mask_summary = QLabel("")
        self.lbl_mask_summary.setStyleSheet("color: #93C5FD; font-weight: 600;")
        row_m2.addWidget(self.lbl_mask_summary, stretch=1)
        v_mask.addLayout(row_m2)
        layout.addWidget(grp_mask)
        self._refresh_mask_summary()

        # 4. Audio Mix Group (Reuses AudioEngine / BackgroundSeparator / TimelineMixer)
        grp_audio = QGroupBox(s["batch_audio_group"])
        v_aud = QVBoxLayout(grp_audio)
        v_aud.setSpacing(6)

        row_aud_mode = QHBoxLayout()
        row_aud_mode.setSpacing(12)
        self.rb_audio_normal = QRadioButton(s["batch_audio_mode_normal"])
        self.rb_audio_normal.setChecked(True)
        self.rb_audio_custom = QRadioButton(s["batch_audio_mode_custom"])
        self._audio_btn_group = QButtonGroup(self)
        self._audio_btn_group.addButton(self.rb_audio_normal)
        self._audio_btn_group.addButton(self.rb_audio_custom)
        self.rb_audio_normal.toggled.connect(self._on_audio_mode_changed)
        row_aud_mode.addWidget(self.rb_audio_normal)
        row_aud_mode.addWidget(self.rb_audio_custom)
        row_aud_mode.addStretch()

        self.btn_audio_preview = QPushButton(s["batch_audio_preview"])
        self.btn_audio_preview.setProperty("variant", "primary")
        self.btn_audio_preview.clicked.connect(self._toggle_audio_preview)
        row_aud_mode.addWidget(self.btn_audio_preview)
        v_aud.addLayout(row_aud_mode)

        self.audio_mix_widget = BatchAudioMixWidget(lang=self.lang, parent=self)
        self.audio_mix_widget.set_from_settings(self.base_settings, auto_fit=False)
        self.audio_mix_widget.settings_changed.connect(self._on_batch_audio_edited)
        v_aud.addWidget(self.audio_mix_widget)
        layout.addWidget(grp_audio)

        # 5. Queue Table & Row Manipulation Controls
        row_qtools = QHBoxLayout()
        self.btn_up = QPushButton("▲ ឡើង")
        self.btn_up.clicked.connect(lambda: self._move_selected(-1))
        row_qtools.addWidget(self.btn_up)
        self.btn_dn = QPushButton("▼ ចុះ")
        self.btn_dn.clicked.connect(lambda: self._move_selected(1))
        row_qtools.addWidget(self.btn_dn)
        self.btn_chk_all = QPushButton("☑ ជ្រើសទាំងអស់")
        self.btn_chk_all.clicked.connect(lambda: self._toggle_all_checks(True))
        row_qtools.addWidget(self.btn_chk_all)
        self.btn_unchk = QPushButton("☐ ដកជ្រើស")
        self.btn_unchk.clicked.connect(lambda: self._toggle_all_checks(False))
        row_qtools.addWidget(self.btn_unchk)
        self.btn_rm = QPushButton("🗑️ លុប")
        self.btn_rm.setProperty("variant", "danger")
        self.btn_rm.clicked.connect(self._remove_selected)
        row_qtools.addWidget(self.btn_rm)

        self.btn_clear_queue = QPushButton(s["batch_clear"])
        self.btn_clear_queue.setProperty("variant", "danger")
        self.btn_clear_queue.setToolTip(
            "សម្អាតបញ្ជីទាំងអស់ (មិនលុបឯកសារវីដេអូ ឬលទ្ធផលក្នុងថាសឡើយ)" if self.lang == "km"
            else "Clear all queue items (does not delete files on disk)"
        )
        self.btn_clear_queue.clicked.connect(self._clear_queue)
        row_qtools.addWidget(self.btn_clear_queue)
        row_qtools.addStretch()
        layout.addLayout(row_qtools)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ជ្រើស", "ល.រ", "ឈ្មោះឯកសារ", "រយៈពេល", "ស្ថានភាព", "វឌ្ឍនភាព", "សារកំហុស"]
            if self.lang == "km" else
            ["Use", "#", "Filename", "Duration", "Status", "Progress", "Error"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table, stretch=2)

        # 5. Overall Progress & Log View
        row_prog = QHBoxLayout()
        self.lbl_overall = QLabel("រួចរាល់សម្រាប់ចាប់ផ្ដើម")
        row_prog.addWidget(self.lbl_overall, stretch=1)
        self.lbl_timing = QLabel("កន្លងផុត: 00:00 | នៅសល់: --:--")
        self.lbl_timing.setStyleSheet("color: #9AA8C4;")
        row_prog.addWidget(self.lbl_timing)
        self.lbl_live_usage = QLabel("")
        self.lbl_live_usage.setStyleSheet("color: #34D399; font-weight: 600;")
        row_prog.addWidget(self.lbl_live_usage)
        layout.addLayout(row_prog)

        self.overall_pbar = QProgressBar()
        self.overall_pbar.setValue(0)
        layout.addWidget(self.overall_pbar)

        row_log_hdr = QHBoxLayout()
        row_log_hdr.addWidget(QLabel("កំណត់ហេតុដំណើរការ Batch (Logs):" if self.lang == "km" else "Batch Logs:"))
        row_log_hdr.addStretch()
        self.btn_clear_log = QPushButton(s["batch_clear_log"])
        self.btn_clear_log.setToolTip("សម្អាតតែអត្ថបទ Log ប៉ុណ្ណោះ" if self.lang == "km" else "Clear log text only")
        self.btn_clear_log.clicked.connect(lambda: self.txt_log.clear())
        row_log_hdr.addWidget(self.btn_clear_log)
        layout.addLayout(row_log_hdr)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(120)
        self.txt_log.setPlaceholderText("កំណត់ហេតុដំណើរការ Batch (Logs)...")
        layout.addWidget(self.txt_log)

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        # Bottom Pinned Controls (outside scroll)
        b_bar = QHBoxLayout()
        self.btn_start = QPushButton("▶ ចាប់ផ្ដើម" if self.lang == "km" else "▶ Start")
        self.btn_start.setProperty("variant", "success")
        self.btn_start.clicked.connect(lambda: self._start_batch(retry_failed_only=False))
        b_bar.addWidget(self.btn_start)

        self.btn_pause = QPushButton("⏸ ផ្អាក" if self.lang == "km" else "⏸ Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        b_bar.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton("⏹ បោះបង់" if self.lang == "km" else "⏹ Cancel")
        self.btn_cancel.setProperty("variant", "danger")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_batch)
        b_bar.addWidget(self.btn_cancel)

        self.btn_retry_failed = QPushButton("🔁 សាកល្បងឡើងវិញ (Failed)" if self.lang == "km" else "🔁 Retry Failed")
        self.btn_retry_failed.clicked.connect(lambda: self._start_batch(retry_failed_only=True))
        b_bar.addWidget(self.btn_retry_failed)

        b_bar.addStretch()
        btn_close = QPushButton("បិទ" if self.lang == "km" else "Close")
        btn_close.clicked.connect(self.accept)
        b_bar.addWidget(btn_close)
        outer.addLayout(b_bar)

        self._update_cost_estimate()

    def _browse_in_folder(self):
        d = QFileDialog.getExistingDirectory(self, "ជ្រើសរើស Folder វីដេអូចូល")
        if d:
            self.txt_in_dir.setText(d)
            if not self.txt_out_dir.text().strip():
                self.txt_out_dir.setText(str(Path(d).parent / "Batch_Output"))

    def _browse_out_folder(self):
        d = QFileDialog.getExistingDirectory(self, "ជ្រើសរើស Folder លទ្ធផលចេញ")
        if d:
            self.txt_out_dir.setText(d)

    def _scan_folder(self):
        in_dir = self.txt_in_dir.text().strip()
        if not in_dir or not os.path.isdir(in_dir):
            QMessageBox.warning(self, "Folder មិនត្រឹមត្រូវ", "សូមជ្រើសរើស Folder វីដេអូចូលឱ្យបានត្រឹមត្រូវ!")
            return
        out_dir = self.txt_out_dir.text().strip() or str(Path(in_dir).parent / "Batch_Output")
        self.txt_out_dir.setText(out_dir)

        self.items = scan_video_folder(
            input_folder=in_dir,
            output_folder=out_dir,
            include_subfolders=self.chk_subfolders.isChecked(),
            probe_durations=True,
        )
        self._sync_config_from_ui()
        self._populate_table()
        save_batch_state(self.config, self.items, state_file=self._state_file)
        self._append_log(f"🔍 រកឃើញវីដេអូចំនួន {len(self.items)} ឯកសារ (តម្រៀបតាមលេខភាគ natural sort)។")
        self._update_cost_estimate()
        self._update_controls_state()

    def _populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, it in enumerate(self.items):
            self.table.insertRow(row)
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk_item.setCheckState(Qt.CheckState.Checked if it.checked else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, chk_item)

            idx_item = QTableWidgetItem(str(row + 1))
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, idx_item)

            name_item = QTableWidgetItem(it.filename)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            tips = []
            if it.mask_override:
                tips.append("⚙ បិទបាំងផ្ទាល់ខ្លួន")
            if it.audio_override:
                tips.append("🎛️ សំឡេងផ្ទាល់ខ្លួន")
            if tips:
                name_item.setToolTip(" | ".join(tips))
            self.table.setItem(row, 2, name_item)

            dur_item = QTableWidgetItem(format_duration_hms(it.duration_sec))
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, dur_item)

            st_item = QTableWidgetItem(it.status)
            st_item.setFlags(st_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 4, st_item)

            pbar = QProgressBar()
            pbar.setValue(it.progress)
            self.table.setCellWidget(row, 5, pbar)

            badges = []
            if it.mask_override:
                badges.append("⚙ បិទបាំងផ្ទាល់")
            if it.audio_override:
                badges.append("🎛️ សំឡេងផ្ទាល់")
            note_txt = it.error_msg or (" · ".join(badges))
            err_item = QTableWidgetItem(note_txt)
            err_item.setFlags(err_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 6, err_item)
        self.table.blockSignals(False)

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if item.column() == 0 and 0 <= item.row() < len(self.items):
            self.items[item.row()].checked = (item.checkState() == Qt.CheckState.Checked)
            save_batch_state(self.config, self.items, state_file=self._state_file)
            self._update_cost_estimate()

    def _move_selected(self, delta: int):
        if self.worker and self.worker.isRunning():
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if len(rows) != 1:
            return
        r = rows[0]
        nr = r + delta
        if 0 <= nr < len(self.items):
            self.items[r], self.items[nr] = self.items[nr], self.items[r]
            self._populate_table()
            self.table.selectRow(nr)
            save_batch_state(self.config, self.items, state_file=self._state_file)

    def _toggle_all_checks(self, checked: bool):
        if self.worker and self.worker.isRunning():
            return
        for it in self.items:
            it.checked = checked
        self._populate_table()
        save_batch_state(self.config, self.items, state_file=self._state_file)
        self._update_cost_estimate()

    def _remove_selected(self):
        if self.worker and self.worker.isRunning():
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self.items):
                self.items.pop(r)
        self._populate_table()
        save_batch_state(self.config, self.items, state_file=self._state_file)
        self._update_cost_estimate()
        self._update_controls_state()

    def _clear_queue(self):
        """Clear all items from the Batch queue UI & persisted state without deleting disk files."""
        if self.worker and self.worker.isRunning():
            return
        if not self.items:
            return

        s = STRINGS.get(self.lang, STRINGS["km"])
        ans = QMessageBox.question(
            self,
            s["batch_clear_confirm_title"],
            s["batch_clear_confirm_msg"],
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.items.clear()
        self._populate_table()
        self.overall_pbar.setValue(0)
        self.lbl_overall.setText("រួចរាល់សម្រាប់ចាប់ផ្ដើម" if self.lang == "km" else "Ready to start")
        self.lbl_timing.setText(
            "កន្លងផុត: 00:00 | នៅសល់: --:--" if self.lang == "km" else "Elapsed: 00:00 | ETA: --:--"
        )
        self.lbl_live_usage.setText("")
        self._update_cost_estimate()
        self._sync_config_from_ui()
        clear_batch_state(self.config, state_file=self._state_file)
        self._update_controls_state()
        self._append_log("🧹 បានសម្អាតបញ្ជី Batch ទាំងអស់រួចរាល់។")

    # --- Batch Subtitle Masking & Preview Helpers ---
    def _effective_batch_settings_copy(self) -> ProjectSettings:
        return resolve_episode_settings(self.base_settings, self.config, item=None)

    def _refresh_mask_summary(self):
        if not hasattr(self, "lbl_mask_summary"):
            return
        eff = self._effective_batch_settings_copy()
        summary = format_mask_summary(eff, enabled=self.config.mask_enabled, lang=self.lang)
        mode_tag = (
            "[ផ្ទាល់ Batch]" if self.config.mask_mode == "custom" else "[ដូចរបៀបធម្មតា]"
            if self.lang == "km" else
            "[Custom Batch]" if self.config.mask_mode == "custom" else "[Normal Settings]"
        )
        self.lbl_mask_summary.setText(f"{mode_tag} {summary}")

    def _on_batch_mask_toggled(self, checked: bool):
        self.config.mask_enabled = bool(checked)
        self._refresh_mask_summary()
        save_batch_state(self.config, self.items, state_file=self._state_file)

    def _on_mask_mode_changed(self):
        is_normal = self.rb_mask_normal.isChecked()
        self.config.mask_mode = "normal" if is_normal else "custom"
        if is_normal:
            norm_on = bool(getattr(self.base_settings, "mask_subtitles", True))
            self.config.mask_enabled = norm_on
            self.chk_batch_mask.blockSignals(True)
            self.chk_batch_mask.setChecked(norm_on)
            self.chk_batch_mask.blockSignals(False)
        elif not self.config.batch_mask_settings:
            self.config.batch_mask_settings = extract_mask_settings_dict(self.base_settings)
        self._refresh_mask_summary()
        save_batch_state(self.config, self.items, state_file=self._state_file)

    def _pick_preview_video_item(self) -> Optional[BatchItem]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if rows and 0 <= rows[0] < len(self.items):
            return self.items[rows[0]]
        for it in self.items:
            if it.checked:
                return it
        return self.items[0] if self.items else None

    def _open_batch_mask_dialog(self):
        preview_item = self._pick_preview_video_item()
        temp_settings = self._effective_batch_settings_copy()
        vid_path = preview_item.video_path if preview_item else ""
        vid_dur = preview_item.duration_sec if preview_item else 0.0

        dlg = BatchSubtitleMaskDialog(
            settings=temp_settings,
            video_path=vid_path,
            current_time=2.0,
            duration_sec=vid_dur,
            lang=self.lang,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config.batch_mask_settings = extract_mask_settings_dict(temp_settings)
            self.config.mask_mode = "custom"
            self.config.mask_enabled = bool(temp_settings.mask_subtitles)

            self.rb_mask_custom.blockSignals(True)
            self.rb_mask_normal.blockSignals(True)
            self.rb_mask_custom.setChecked(True)
            self.rb_mask_normal.setChecked(False)
            self.rb_mask_custom.blockSignals(False)
            self.rb_mask_normal.blockSignals(False)

            self.chk_batch_mask.blockSignals(True)
            self.chk_batch_mask.setChecked(self.config.mask_enabled)
            self.chk_batch_mask.blockSignals(False)

            self._refresh_mask_summary()
            save_batch_state(self.config, self.items, state_file=self._state_file)
            self._append_log("⚙ បានកំណត់ទីតាំងបិទបាំងអក្សរចិនផ្ទាល់សម្រាប់ Batch នេះ។")

    # --- Batch Audio Mix & Non-blocking Preview Helpers ---
    def _on_batch_audio_edited(self):
        self.config.batch_audio_settings = self.audio_mix_widget.get_audio_dict()
        self.config.audio_mode = "custom"
        self.rb_audio_custom.blockSignals(True)
        self.rb_audio_normal.blockSignals(True)
        self.rb_audio_custom.setChecked(True)
        self.rb_audio_normal.setChecked(False)
        self.rb_audio_custom.blockSignals(False)
        self.rb_audio_normal.blockSignals(False)
        save_batch_state(self.config, self.items, state_file=self._state_file)

    def _on_audio_mode_changed(self):
        is_normal = self.rb_audio_normal.isChecked()
        self.config.audio_mode = "normal" if is_normal else "custom"
        if is_normal:
            self.audio_mix_widget.set_from_settings(self.base_settings, auto_fit=False)
        else:
            if not self.config.batch_audio_settings:
                self.config.batch_audio_settings = self.audio_mix_widget.get_audio_dict()
            else:
                self.audio_mix_widget.set_from_audio_dict(self.config.batch_audio_settings)
        save_batch_state(self.config, self.items, state_file=self._state_file)

    def _stop_audio_preview(self):
        if self._audio_player:
            self._audio_player.stop()
        s = STRINGS.get(self.lang, STRINGS["km"])
        if hasattr(self, "btn_audio_preview"):
            self.btn_audio_preview.setText(s["batch_audio_preview"])
            self.btn_audio_preview.setEnabled(True)

    def _toggle_audio_preview(self):
        s = STRINGS.get(self.lang, STRINGS["km"])
        if self._audio_player and self._audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_audio_preview()
            return
        if self._preview_worker and self._preview_worker.isRunning():
            return

        target_item = self._pick_preview_video_item()
        if not target_item or not os.path.exists(target_item.video_path):
            QMessageBox.information(
                self,
                "គ្មានវីដេអូសម្រាប់សាកល្បង" if self.lang == "km" else "No Video Selected",
                "សូមស្កេន Folder ឬជ្រើសវីដេអូមួយក្នុងតារាងជាមុនសិន!" if self.lang == "km" else "Scan or select a video in the queue first!",
            )
            return

        self._sync_config_from_ui()
        eff = resolve_episode_settings(self.base_settings, self.config, item=target_item)
        if not has_audio_stream(target_item.video_path):
            self._append_log(f"⚠️ Preview: {target_item.filename} — {s['batch_audio_no_track']}")

        self.btn_audio_preview.setEnabled(False)
        self.btn_audio_preview.setText("⏳ កំពុងលាយសំឡេង..." if self.lang == "km" else "⏳ Mixing...")
        self._append_log(f"🔊 កំពុងបង្កើត Audio Preview (12s) ពី {target_item.filename}...")

        self._preview_worker = _AudioPreviewThread(target_item.video_path, eff, lang=self.lang, parent=self)
        self._preview_worker.preview_ready.connect(self._on_audio_preview_ready)
        self._preview_worker.preview_failed.connect(self._on_audio_preview_failed)
        self._preview_worker.start()

    def _on_audio_preview_ready(self, wav_path: str, info_note: str):
        s = STRINGS.get(self.lang, STRINGS["km"])
        self._append_log(f"▶️ Preview រួចរាល់ ({info_note})")
        if self._audio_player is None:
            self._audio_out = QAudioOutput(self)
            self._audio_out.setVolume(1.0)
            self._audio_player = QMediaPlayer(self)
            self._audio_player.setAudioOutput(self._audio_out)
            self._audio_player.playbackStateChanged.connect(
                lambda st: self._stop_audio_preview() if st == QMediaPlayer.PlaybackState.StoppedState else None
            )
        self._audio_player.setSource(QUrl.fromLocalFile(wav_path))
        self._audio_player.play()
        self.btn_audio_preview.setEnabled(True)
        self.btn_audio_preview.setText(s["batch_audio_preview_stop"])

    def _on_audio_preview_failed(self, err: str):
        s = STRINGS.get(self.lang, STRINGS["km"])
        self.btn_audio_preview.setEnabled(True)
        self.btn_audio_preview.setText(s["batch_audio_preview"])
        self._append_log(f"❌ បរាជ័យការបង្កើត Audio Preview: {err}")
        QMessageBox.warning(self, "Audio Preview Error", f"មិនអាចលាយសំឡេងសាកល្បងបានទេ៖\n{err}")

    def _on_table_context_menu(self, pos):
        if self.worker and self.worker.isRunning():
            return
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        if not (0 <= row < len(self.items)):
            return

        s = STRINGS.get(self.lang, STRINGS["km"])
        item = self.items[row]
        menu = QMenu(self)
        act_override = menu.addAction(s["batch_mask_episode_override"])
        act_reset = menu.addAction(s["batch_mask_episode_clear"]) if item.mask_override else None
        menu.addSeparator()
        act_aud_override = menu.addAction(s["batch_audio_episode_override"])
        act_aud_reset = menu.addAction(s["batch_audio_episode_clear"]) if item.audio_override else None

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act_override:
            ep_settings = resolve_episode_settings(self.base_settings, self.config, item=item)
            dlg = BatchSubtitleMaskDialog(
                settings=ep_settings,
                video_path=item.video_path,
                current_time=2.0,
                duration_sec=item.duration_sec,
                lang=self.lang,
                parent=self,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted:
                item.mask_override = extract_mask_settings_dict(ep_settings)
                self._populate_table()
                self.table.selectRow(row)
                save_batch_state(self.config, self.items, state_file=self._state_file)
                self._append_log(f"⚙ បានកំណត់ទីតាំងបិទបាំងផ្ទាល់សម្រាប់ភាគ៖ {item.filename}")
        elif act_reset is not None and chosen == act_reset:
            item.mask_override = None
            self._populate_table()
            self.table.selectRow(row)
            save_batch_state(self.config, self.items, state_file=self._state_file)
            self._append_log(f"🔄 ភាគ {item.filename} ត្រឡប់ទៅប្រើ settings បិទបាំងរួមវិញ។")
        elif chosen == act_aud_override:
            ep_settings = resolve_episode_settings(self.base_settings, self.config, item=item)
            cur_aud = item.audio_override or extract_audio_settings_dict(
                ep_settings, auto_fit=bool(getattr(ep_settings, "_batch_audio_auto_fit", False))
            )
            dlg_a = BatchEpisodeAudioDialog(item.filename, cur_aud, lang=self.lang, parent=self)
            if dlg_a.exec() == QDialog.DialogCode.Accepted:
                item.audio_override = dlg_a.result_audio_dict()
                self._populate_table()
                self.table.selectRow(row)
                save_batch_state(self.config, self.items, state_file=self._state_file)
                self._append_log(f"🎛️ បានកំណត់សំឡេងផ្ទាល់សម្រាប់ភាគ៖ {item.filename}")
        elif act_aud_reset is not None and chosen == act_aud_reset:
            item.audio_override = None
            self._populate_table()
            self.table.selectRow(row)
            save_batch_state(self.config, self.items, state_file=self._state_file)
            self._append_log(f"🔄 ភាគ {item.filename} ត្រឡប់ទៅប្រើ settings សំឡេងរួមវិញ។")

    def _edit_shared_glossary(self):
        dlg = SharedGlossaryDialog(self.config.shared_glossary, lang=self.lang, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config.shared_glossary = dlg.glossary_dicts
            self.btn_glossary.setText(f"📖 Glossary រួម ({len(self.config.shared_glossary)})")
            save_batch_state(self.config, self.items, state_file=self._state_file)

    def _sync_config_from_ui(self):
        self.config.input_folder = self.txt_in_dir.text().strip()
        self.config.output_folder = self.txt_out_dir.text().strip()
        self.config.include_subfolders = self.chk_subfolders.isChecked()
        self.config.overwrite_existing = bool(self.cb_conflict.currentData())
        self.config.src_engine = self.cb_batch_src.currentData() or "auto"
        self.config.trans_engine = self.cb_batch_trans.currentData() or "google"
        self.config.run_extract = self.chk_step_extract.isChecked()
        self.config.run_translate = self.chk_step_trans.isChecked()
        self.config.run_tts = self.chk_step_tts.isChecked()
        self.config.run_render = self.chk_step_render.isChecked()
        self.config.run_export_srt = self.chk_step_srt.isChecked()
        self.config.notify_on_complete = self.chk_sound_done.isChecked()
        if hasattr(self, "chk_batch_mask"):
            self.config.mask_enabled = self.chk_batch_mask.isChecked()
        if hasattr(self, "rb_mask_custom"):
            self.config.mask_mode = "custom" if self.rb_mask_custom.isChecked() else "normal"
        if hasattr(self, "rb_audio_custom"):
            self.config.audio_mode = "custom" if self.rb_audio_custom.isChecked() else "normal"
        if hasattr(self, "audio_mix_widget") and self.config.audio_mode == "custom":
            self.config.batch_audio_settings = self.audio_mix_widget.get_audio_dict()

    def _restore_saved_queue(self):
        loaded = load_batch_state(self._state_file)
        if not loaded:
            return
        cfg, items = loaded
        if cfg.input_folder:
            self.txt_in_dir.setText(cfg.input_folder)
        if cfg.output_folder:
            self.txt_out_dir.setText(cfg.output_folder)
        self.chk_subfolders.setChecked(cfg.include_subfolders)
        self.chk_step_extract.setChecked(cfg.run_extract)
        self.chk_step_trans.setChecked(cfg.run_translate)
        self.chk_step_tts.setChecked(cfg.run_tts)
        self.chk_step_render.setChecked(cfg.run_render)
        self.chk_step_srt.setChecked(cfg.run_export_srt)
        self.chk_sound_done.setChecked(cfg.notify_on_complete)

        for cb, val in ((self.cb_batch_src, cfg.src_engine), (self.cb_batch_trans, cfg.trans_engine), (self.cb_conflict, cfg.overwrite_existing)):
            idx = cb.findData(val)
            if idx >= 0:
                cb.setCurrentIndex(idx)

        if cfg.shared_glossary:
            self.config.shared_glossary = cfg.shared_glossary
            self.btn_glossary.setText(f"📖 Glossary រួម ({len(cfg.shared_glossary)})")

        # Restore persisted Batch mask configuration cleanly (independent from normal settings)
        self.config.mask_enabled = bool(cfg.mask_enabled)
        self.config.mask_mode = cfg.mask_mode if cfg.mask_mode in ("normal", "custom") else "normal"
        self.config.batch_mask_settings = copy.deepcopy(cfg.batch_mask_settings or {})

        self.rb_mask_normal.blockSignals(True)
        self.rb_mask_custom.blockSignals(True)
        self.rb_mask_custom.setChecked(self.config.mask_mode == "custom")
        self.rb_mask_normal.setChecked(self.config.mask_mode != "custom")
        self.rb_mask_normal.blockSignals(False)
        self.rb_mask_custom.blockSignals(False)

        self.chk_batch_mask.blockSignals(True)
        self.chk_batch_mask.setChecked(self.config.mask_enabled)
        self.chk_batch_mask.blockSignals(False)
        self._refresh_mask_summary()

        # Restore persisted Batch audio mix configuration cleanly (independent from normal settings)
        self.config.audio_mode = cfg.audio_mode if cfg.audio_mode in ("normal", "custom") else "normal"
        self.config.batch_audio_settings = copy.deepcopy(cfg.batch_audio_settings or {})

        self.rb_audio_normal.blockSignals(True)
        self.rb_audio_custom.blockSignals(True)
        self.rb_audio_custom.setChecked(self.config.audio_mode == "custom")
        self.rb_audio_normal.setChecked(self.config.audio_mode != "custom")
        self.rb_audio_normal.blockSignals(False)
        self.rb_audio_custom.blockSignals(False)
        if self.config.audio_mode == "custom" and self.config.batch_audio_settings:
            self.audio_mix_widget.set_from_audio_dict(self.config.batch_audio_settings)
        else:
            self.audio_mix_widget.set_from_settings(self.base_settings, auto_fit=False)

        if items:
            self.items = items
            self._populate_table()
            self._append_log(f"♻️ បានស្ដារជួរ Batch មុនចំនួន {len(items)} ភាគ។")
            self._update_cost_estimate()

    def _update_controls_state(self):
        running = bool(self.worker and self.worker.isRunning())
        has_items = bool(self.items)

        self.btn_start.setEnabled((not running) and has_items)
        self.btn_scan.setEnabled(not running)
        self.btn_retry_failed.setEnabled((not running) and has_items)
        self.btn_clear_queue.setEnabled((not running) and has_items)
        self.btn_up.setEnabled((not running) and has_items)
        self.btn_dn.setEnabled((not running) and has_items)
        self.btn_chk_all.setEnabled((not running) and has_items)
        self.btn_unchk.setEnabled((not running) and has_items)
        self.btn_rm.setEnabled((not running) and has_items)

        self.chk_batch_mask.setEnabled(not running)
        self.rb_mask_normal.setEnabled(not running)
        self.rb_mask_custom.setEnabled(not running)
        self.btn_batch_mask_pos.setEnabled(not running)

        self.rb_audio_normal.setEnabled(not running)
        self.rb_audio_custom.setEnabled(not running)
        self.audio_mix_widget.setEnabled(not running)
        self.btn_audio_preview.setEnabled((not running) and has_items)

        self.btn_pause.setEnabled(running)
        self.btn_cancel.setEnabled(running)

    def _update_cost_estimate(self):
        checked_items = [it for it in self.items if it.checked]
        total_sec = sum(it.duration_sec for it in checked_items)
        use_oa_stt = self.chk_step_extract.isChecked() and self.cb_batch_src.currentData() == "openai"
        use_oa_tr = self.chk_step_trans.isChecked() and self.cb_batch_trans.currentData() == "openai"

        if not (use_oa_stt or use_oa_tr) or not checked_items:
            self.lbl_cost_est.setText("")
            return

        est_usd = 0.0
        parts = []
        if use_oa_stt:
            u = estimate_transcription_cost(total_sec, getattr(self.base_settings, "openai_stt_model", "whisper-1"))
            est_usd += u.estimated_usd
            parts.append(f"STT ~{u.audio_minutes:.1f}m")
        if use_oa_tr:
            # Rough dialogue density ~12 lines/minute -> ~450 tokens/minute
            est_toks = int(max(1, (total_sec / 60.0) * 450)) if total_sec > 0 else len(checked_items) * 1200
            tr_usd = (est_toks / 1_000_000.0) * 0.75
            est_usd += tr_usd
            parts.append(f"Trans ~{est_toks:,} tok")
        self.lbl_cost_est.setText(f"💰 ប៉ាន់ស្មាន OpenAI ({' + '.join(parts)}): ~${est_usd:.4f}")

    def _start_batch(self, retry_failed_only: bool = False):
        if not self.items:
            QMessageBox.information(self, "គ្មានវីដេអូ", "សូមស្កេន Folder វីដេអូជាមុនសិន!")
            return
        if retry_failed_only and not any(it.status == ST_FAILED and it.checked for it in self.items):
            QMessageBox.information(self, "គ្មានភាគបរាជ័យ", "មិនមានភាគណាដែលបរាជ័យសម្រាប់សាកល្បងម្ដងទៀតទេ។")
            return

        self._sync_config_from_ui()
        # Warn politely if BOTH Original Voice and Music/SFX are OFF during Render
        eff_chk = resolve_episode_settings(self.base_settings, self.config, item=None)
        if self.config.run_render and bool(getattr(eff_chk, "mute_original", False)):
            s = STRINGS.get(self.lang, STRINGS["km"])
            ans = QMessageBox.warning(
                self,
                "សំឡេង AI តែមួយ" if self.lang == "km" else "AI Voice Only",
                f"{s['batch_audio_both_muted_warn']}\n\nតើអ្នកចង់បន្តដែរឬទេ?" if self.lang == "km"
                else f"{s['batch_audio_both_muted_warn']}\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._stop_audio_preview()
        save_batch_state(self.config, self.items, state_file=self._state_file)

        self._is_paused = False
        self._batch_start_ts = time.time()
        self._last_eta_sec = 0.0
        self.btn_pause.setText("⏸ ផ្អាក" if self.lang == "km" else "⏸ Pause")

        self.worker = BatchPipelineWorker(
            config=self.config,
            items=self.items,
            base_settings=self.base_settings,
            retry_failed_only=retry_failed_only,
            state_file=self._state_file,
            parent=self,
        )
        self.worker.item_updated.connect(self._on_item_updated)
        self.worker.overall_progress.connect(self._on_overall_progress)
        self.worker.log_message.connect(self._append_log)
        self.worker.usage_updated.connect(self._on_usage_updated)
        self.worker.batch_finished.connect(self._on_batch_finished)
        self.worker.start()
        self._elapsed_timer.start()
        self._update_controls_state()

    def _toggle_pause(self):
        if not self.worker or not self.worker.isRunning():
            return
        if self._is_paused:
            self._is_paused = False
            self.worker.resume()
            self.btn_pause.setText("⏸ ផ្អាក" if self.lang == "km" else "⏸ Pause")
        else:
            self._is_paused = True
            self.worker.pause()
            self.btn_pause.setText("▶ បន្ត" if self.lang == "km" else "▶ Resume")

    def _cancel_batch(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)

    def _on_item_updated(self, row: int, status: str, pct: int, err: str):
        if 0 <= row < self.table.rowCount():
            self.table.blockSignals(True)
            st_item = self.table.item(row, 4)
            if st_item:
                st_item.setText(status)
            pbar = self.table.cellWidget(row, 5)
            if isinstance(pbar, QProgressBar):
                pbar.setValue(pct)
            err_item = self.table.item(row, 6)
            if err_item:
                err_item.setText(err)
            self.table.blockSignals(False)

    def _tick_elapsed_timer(self):
        if self._batch_start_ts <= 0:
            return
        elapsed = max(0.0, time.time() - self._batch_start_ts)
        eta_txt = format_duration_hms(self._last_eta_sec) if self._last_eta_sec > 0 else "--:--"
        self.lbl_timing.setText(f"កន្លងផុត: {format_duration_hms(elapsed)} | នៅសល់: {eta_txt}")

    def _on_overall_progress(self, pct: int, active_name: str, elapsed: float, eta: float):
        self.overall_pbar.setValue(pct)
        self._last_eta_sec = float(eta or 0.0)
        if active_name:
            self.lbl_overall.setText(f"កំពុងដំណើរការ៖ {active_name}")
        self.lbl_timing.setText(
            f"កន្លងផុត: {format_duration_hms(elapsed)} | នៅសល់: {format_duration_hms(eta) if eta > 0 else '--:--'}"
        )

    def _on_usage_updated(self, usage: OpenAIUsageStats):
        toks = usage.prompt_tokens + usage.completion_tokens
        self.lbl_live_usage.setText(f"📊 OpenAI: {toks:,} tok (~${usage.estimated_usd:.4f})")

    def _append_log(self, msg: str):
        self.txt_log.appendPlainText(msg)

    def _on_batch_finished(self, done_cnt: int, failed_cnt: int, skipped_cnt: int):
        self._elapsed_timer.stop()
        if self.worker:
            self.worker.wait(500)
            self.worker = None
        self._update_controls_state()
        self.lbl_overall.setText(f"បានបញ្ចប់៖ រួចរាល់ {done_cnt} | រំលង {skipped_cnt} | បរាជ័យ {failed_cnt}")

        if self.chk_sound_done.isChecked():
            QApplication.beep()
            try:
                from app.notifications import NotificationManager
                out_dir = self.config.output_folder
                NotificationManager.instance().notify(
                    "success" if failed_cnt == 0 else "warning",
                    "Batch ចប់រួចរាល់",
                    f"រួចរាល់ {done_cnt}, រំលង {skipped_cnt}, បរាជ័យ {failed_cnt}",
                    action_label="📂 បើកថត" if out_dir else "",
                    action_path=out_dir,
                    persist=True,
                    tray=True,
                )
            except Exception:
                pass

    def closeEvent(self, event):
        self._stop_audio_preview()
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.wait(800)
        super().closeEvent(event)

    def done(self, result: int):
        self._stop_audio_preview()
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.wait(800)
        super().done(result)
