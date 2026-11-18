"""
Main Studio Window for AI Movie Translator & Auto Dubbing
Integrates all SRS requirements F1 - F11 with Khmer/English localization
"""

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QFileDialog, QMessageBox, QProgressBar,
    QMenuBar, QMenu, QToolBar, QStatusBar, QGroupBox, QSlider,
    QCheckBox, QDialog, QTabWidget, QComboBox, QLineEdit
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon, QAction, QFont

from app.models import Project, Segment, ProjectSettings
from app.database import DatabaseManager
from app.config import (
    APP_ROOT, get_resource_path, STRINGS, KHMER_VOICES,
    TARGET_LANGUAGES, TARGET_LANG_MAP, get_voice_pair_for_lang, is_female_voice,
    APP_VERSION, APP_BUILD_DATE
)
from app.audio_engine import AudioEngine
from app.stt_engine import STTEngine
from app.translation_engine import TranslationEngine
from app.tts_engine import TTSEngine
from app.sync_engine import SyncEngine, SyncReport
from app.export_engine import ExportEngine
from app.utils import is_ffmpeg_available

from app.ui.video_player import VideoPlayerWidget
from app.ui.segment_table import SegmentTableWidget
from app.ui.speaker_manager import SpeakerManagerDialog
from app.ui.sync_report_dialog import SyncReportDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.batch_dialog import BatchQueueDialog


# --- Background Worker Thread for Long-Running Tasks ---
class PipelineWorker(QThread):
    progress = Signal(int, str)
    finished_success = Signal(object)
    finished_error = Signal(str)

    def __init__(self, task_name: str, task_fn, *args, **kwargs):
        super().__init__()
        self.task_name = task_name
        self.task_fn = task_fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            res = self.task_fn(*self.args, progress_cb=self.progress.emit, **self.kwargs)
            self.finished_success.emit(res)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.project = Project()
        self.current_report: Optional[SyncReport] = None
        self.lang = "km" # Default Khmer per SRS
        
        # Engines
        self.audio_engine = AudioEngine()
        self.tts_engine = TTSEngine()
        self.translation_engine = TranslationEngine(self.project.settings)
        self.sync_engine = SyncEngine(self.audio_engine, self.tts_engine, self.translation_engine)
        self.export_engine = ExportEngine(self.audio_engine)

        self._init_ui()
        self._setup_autosave()
        self._check_resume_session()
        if getattr(self.project.settings, "auto_check_updates", True):
            QTimer.singleShot(3500, self._silent_startup_update_check)

    def _init_ui(self):
        self.resize(1540, 880)
        self.setMinimumSize(1120, 660)
        self.setWindowTitle(f"{STRINGS[self.lang]['app_title']} v{APP_VERSION}")
        for icon_rel in ("assets/logo.ico", "assets/logo.png", "assets/logo.svg"):
            icon_file = get_resource_path(icon_rel)
            if icon_file.exists():
                self.setWindowIcon(QIcon(str(icon_file)))
                break

        # Central Widget & Main Splitter
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(6)

        # Splitter: Left (Video + Mixing) | Right (Segment Editor)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(8)

        # --- Left Panel ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.video_player = VideoPlayerWidget()
        self.video_player.position_changed.connect(self._on_player_position_changed)
        self.video_player.preview_requested.connect(self._run_preview_result)
        left_layout.addWidget(self.video_player, stretch=1)

        # Lower Tools Tabs (Audio Mixing & Watermark Logo Tool)
        self.tool_tabs = QTabWidget()
        self.mixing_panel = self._create_mixing_panel()
        self.logo_panel = self._create_logo_panel()

        s = STRINGS[self.lang]
        self.tool_tabs.addTab(self.mixing_panel, s.get("audio_mixing_tab", "🎛️ សំឡេង (Audio)"))
        self.tool_tabs.addTab(self.logo_panel, s.get("logo_tool_tab", "🎨 Logo"))
        left_layout.addWidget(self.tool_tabs, stretch=0)

        # Prominent Fast Preview Button (Always accessible)
        self.btn_main_preview = QPushButton("👁️ " + s.get("btn_preview_dubbed", "មើលលទ្ធផលសាកល្បង") + " (Ctrl+P)")
        self.btn_main_preview.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #8b5cf6);
                color: white;
                font-weight: bold;
                font-size: 13px;
                padding: 7px 14px;
                border-radius: 6px;
                border: 1px solid #a855f7;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
            }
            QPushButton:pressed {
                background-color: #4338ca;
            }
        """)
        self.btn_main_preview.clicked.connect(self._run_preview_result)
        left_layout.addWidget(self.btn_main_preview)

        self.splitter.addWidget(left_panel)

        # --- Right Panel ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.segment_table = SegmentTableWidget()
        self.segment_table.play_segment_requested.connect(self.video_player.play_segment)
        self.segment_table.preview_tts_requested.connect(self._preview_segment_tts)
        self.segment_table.segment_speed_changed.connect(self._on_segment_speed_changed)
        self.segment_table.data_changed.connect(self._on_data_changed)
        right_layout.addWidget(self.segment_table)

        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([410, 1110])

        main_layout.addWidget(self.splitter)

        # Build Menus & ToolBar after widgets exist
        self._create_menu_bar()
        self._create_tool_bar()

        # Status Bar with Progress Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

        self.lbl_stats = QLabel("0 Segments")
        self.lbl_stats.setStyleSheet("color: #94a3b8; font-size: 11px; margin-right: 12px;")
        self.status_bar.addPermanentWidget(self.lbl_stats)

        # Version Badge in Status Bar
        self.btn_status_ver = QPushButton(f"v{APP_VERSION}")
        self.btn_status_ver.setFlat(True)
        self.btn_status_ver.setToolTip("ចុចដើម្បីពិនិត្យមើលកំណែថ្មី (Click to check for updates)" if self.lang == "km" else "Click to check for updates")
        self.btn_status_ver.setStyleSheet("""
            QPushButton {
                color: #38bdf8;
                font-size: 11px;
                font-weight: bold;
                background: transparent;
                border: 1px solid #1e293b;
                border-radius: 4px;
                padding: 1px 6px;
                margin-right: 8px;
            }
            QPushButton:hover {
                background-color: #0c4a6e;
                color: #7dd3fc;
            }
        """)
        self.btn_status_ver.clicked.connect(self._check_updates_dialog)
        self.status_bar.addPermanentWidget(self.btn_status_ver)

        self.status_bar.showMessage(STRINGS[self.lang]["status_ready"])

    def _create_menu_bar(self):
        menubar = self.menuBar()
        menubar.clear()
        s = STRINGS[self.lang]

        # File Menu
        menu_file = menubar.addMenu(s["file"])
        menu_file.addAction(s["new_project"], self._new_project, "Ctrl+N")
        menu_file.addAction(s["open_project"], self._open_project, "Ctrl+O")
        menu_file.addAction(s["save_project"], self._save_project, "Ctrl+S")
        menu_file.addAction(s["save_project_as"], self._save_project_as, "Ctrl+Shift+S")
        menu_file.addSeparator()
        menu_file.addAction(s["import_video"], self._import_video, "Ctrl+I")
        menu_file.addSeparator()
        menu_file.addAction(s["export_dubbed"], self._export_dubbed, "Ctrl+E")
        menu_file.addAction(s["export_srt"], self._export_srt)
        menu_file.addAction(s["export_wav"], self._export_wav)
        menu_file.addSeparator()
        menu_file.addAction(s["exit"], self.close, "Ctrl+Q")

        # Edit Menu
        menu_edit = menubar.addMenu(s["edit"])
        menu_edit.addAction(s["btn_add_segment"], self.segment_table._add_segment)
        menu_edit.addAction(s["btn_split_segment"], self.segment_table._split_selected)
        menu_edit.addAction(s["btn_merge_segments"], self.segment_table._merge_selected)
        menu_edit.addAction(s["btn_delete_segment"], self.segment_table._delete_selected)
        menu_edit.addAction("🧹 សម្អាត (Clear)..." if self.lang == "km" else "🧹 Clear...", self.segment_table._show_clear_menu)

        # Tools Menu
        menu_tools = menubar.addMenu(s["tools"])
        menu_tools.addAction(s["btn_auto_sync"], self._run_time_sync)
        menu_tools.addAction(s["tab_sync_report"], self._show_sync_report)
        menu_tools.addSeparator()
        menu_tools.addAction("👁️ " + s.get("btn_preview_dubbed", "មើលលទ្ធផលសាកល្បង (Preview)"), self._run_preview_result, "Ctrl+P")
        menu_tools.addSeparator()
        menu_tools.addAction(
            "⚙️ សារ៉េទីតាំងបិទបាំងអក្សរចិន (Subtitle Mask)..." if self.lang == "km" else "⚙️ Subtitle Mask & Position...",
            self._open_subtitle_mask_dialog
        )
        menu_tools.addAction(s["batch_queue"], self._open_batch_dialog)
        menu_tools.addAction(s["settings"], self._open_settings)

        # Help Menu
        menu_help = menubar.addMenu(s["help"])
        menu_help.addAction("🔄 ពិនិត្យមើលកំណែថ្មី (Check for Updates)..." if self.lang == "km" else "🔄 Check for Updates...", self._check_updates_dialog)
        menu_help.addSeparator()
        menu_help.addAction("តម្រូវការប្រព័ន្ធ (SRS Specification)", self._show_srs_help)
        menu_help.addAction("អំពីកម្មវិធី (About)", self._show_about)

    def _create_tool_bar(self):
        toolbar = QToolBar("Main Workflow Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        s = STRINGS[self.lang]

        # Step 0: Import Video
        self.act_import = toolbar.addAction("🎬 នាំចូល" if self.lang == "km" else "🎬 Import", self._import_video)
        self.act_import.setToolTip(s["btn_import_video"])
        toolbar.addSeparator()

        # Step 1: Transcribe
        self.act_transcribe = toolbar.addAction("🎙️ ១. Whisper" if self.lang == "km" else "🎙️ 1. Whisper", self._run_transcription)
        self.act_transcribe.setToolTip(s["btn_transcribe"])

        # Target Language Selector (Multi-Language Translate & TTS Voice)
        self.cb_target_lang = QComboBox()
        self.cb_target_lang.setMinimumWidth(185)
        self.cb_target_lang.setToolTip("ជ្រើសរើសភាសាបកប្រែ និងសំឡេង AI (Select Target Translation & AI Voice Language)")
        self.cb_target_lang.setStyleSheet("""
            QComboBox {
                background-color: #0f172a;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 6px;
                padding: 5px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #7dd3fc;
            }
        """)
        for t_code, t_label, _, _, _ in TARGET_LANGUAGES:
            self.cb_target_lang.addItem(t_label, t_code)
        cur_tlang = getattr(self.project.settings, "target_language", "km") or "km"
        t_idx = self.cb_target_lang.findData(cur_tlang)
        if t_idx >= 0:
            self.cb_target_lang.setCurrentIndex(t_idx)
        self.cb_target_lang.currentIndexChanged.connect(self._on_target_lang_changed)
        toolbar.addWidget(self.cb_target_lang)

        # Translation Engine Selector (Google Translate vs Gemini 1.5 Flash)
        self.cb_trans_provider = QComboBox()
        self.cb_trans_provider.setMinimumWidth(165)
        self.cb_trans_provider.setToolTip("ជ្រើសរើស Engine បកប្រែ៖ Google Translate (ចាស់/ឥតគិតថ្លៃ) ឬ Gemini 1.5 Flash (AI ភាពយន្ត)")
        self.cb_trans_provider.setStyleSheet("""
            QComboBox {
                background-color: #0f172a;
                color: #c084fc;
                border: 1px solid #7e22ce;
                border-radius: 6px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QComboBox:hover {
                border-color: #d8b4fe;
            }
        """)
        self._populate_trans_providers()
        self.cb_trans_provider.currentIndexChanged.connect(self._on_trans_provider_changed)
        toolbar.addWidget(self.cb_trans_provider)

        # Step 2: Translate
        self.act_translate = toolbar.addAction("🌐 ២. បកប្រែ" if self.lang == "km" else "🌐 2. Translate", self._run_translation)
        self.act_translate.setToolTip(s["btn_translate_all"])

        # Step 3: Generate Voices
        self.act_tts = toolbar.addAction("🗣️ ៣. សំឡេង AI" if self.lang == "km" else "🗣️ 3. Voices", self._run_tts_generation)
        self.act_tts.setToolTip(s["btn_generate_tts"])

        # Step 4: Auto Time-Sync
        self.act_sync = toolbar.addAction("⏱️ ៤. តម្រឹម" if self.lang == "km" else "⏱️ 4. Sync", self._run_time_sync)
        self.act_sync.setToolTip(s["btn_auto_sync"])

        # Step 5: Fast Preview Result (BEFORE EXPORT)
        self.act_preview = toolbar.addAction("👁️ Preview", self._run_preview_result)
        self.act_preview.setToolTip(s.get("btn_preview_dubbed", "មើលលទ្ធផលសាកល្បង") + " (Ctrl+P)")

        # Step 6: Export
        self.act_export = toolbar.addAction("🚀 ៥. នាំចេញ" if self.lang == "km" else "🚀 5. Export", self._export_dubbed)
        self.act_export.setToolTip(s["btn_export_all"])

        toolbar.addSeparator()

        # Sync Report, Batch & Settings
        act_rep = toolbar.addAction("📊 របាយការណ៍" if self.lang == "km" else "📊 Report", self._show_sync_report)
        act_rep.setToolTip(s["tab_sync_report"])

        act_bat = toolbar.addAction("📦 Batch", self._open_batch_dialog)
        act_bat.setToolTip(s["batch_queue"])

        act_set = toolbar.addAction("⚙️ កំណត់" if self.lang == "km" else "⚙️ Settings", self._open_settings)
        act_set.setToolTip(s["settings"])

        toolbar.addSeparator()

        # Bilingual Toggle Button
        self.btn_lang = QPushButton(s["language_toggle"])
        self.btn_lang.setMinimumWidth(105)
        self.btn_lang.setStyleSheet("background-color: #0369a1; color: white; font-weight: bold; padding: 6px 14px; border-radius: 6px;")
        self.btn_lang.clicked.connect(self._toggle_language)
        toolbar.addWidget(self.btn_lang)

    def _create_mixing_panel(self) -> QWidget:
        s = STRINGS[self.lang]
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # 1. Dedicated Original Voice (Vocals) Volume
        vbox_orig = QVBoxLayout()
        vbox_orig.setSpacing(2)
        row_orig_hdr = QHBoxLayout()
        self.lbl_orig_title = QLabel(s.get("original_voice_volume", "Original Voice Volume"))
        self.lbl_orig_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 11px;")
        orig_val_pct = int(self.project.settings.original_voice_volume * 100)
        self.lbl_orig_val = QLabel(f"{orig_val_pct}%" if orig_val_pct > 0 and not self.project.settings.mute_original else "0% (OFF)")
        self.lbl_orig_val.setStyleSheet("background: #1e293b; color: #38bdf8; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 11px;")
        row_orig_hdr.addWidget(self.lbl_orig_title)
        row_orig_hdr.addStretch()
        row_orig_hdr.addWidget(self.lbl_orig_val)
        vbox_orig.addLayout(row_orig_hdr)

        self.slider_orig_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_orig_vol.setRange(0, 150)
        self.slider_orig_vol.setValue(0 if self.project.settings.mute_original else orig_val_pct)
        self.slider_orig_vol.setEnabled(not self.project.settings.mute_original)
        self.slider_orig_vol.valueChanged.connect(self._on_orig_vol_changed)
        vbox_orig.addWidget(self.slider_orig_vol)
        layout.addLayout(vbox_orig)

        # 2. Dedicated Background (Music & Sound Effects) Volume
        vbox_bg = QVBoxLayout()
        vbox_bg.setSpacing(2)
        row_bg_hdr = QHBoxLayout()
        self.lbl_bg_title = QLabel(s.get("background_volume", "Background (Music/SFX) Volume"))
        self.lbl_bg_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 11px;")
        bg_val_pct = int(self.project.settings.background_volume * 100)
        bg_active = (not self.project.settings.mute_original or self.project.settings.keep_background) and bg_val_pct > 0
        self.lbl_bg_val = QLabel(f"{bg_val_pct}%" if bg_active else "0% (OFF)")
        self.lbl_bg_val.setStyleSheet("background: #1e293b; color: #f59e0b; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 11px;")
        row_bg_hdr.addWidget(self.lbl_bg_title)
        row_bg_hdr.addStretch()
        row_bg_hdr.addWidget(self.lbl_bg_val)
        vbox_bg.addLayout(row_bg_hdr)

        self.slider_bg_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_bg_vol.setRange(0, 150)
        self.slider_bg_vol.setValue(bg_val_pct if bg_active else 0)
        self.slider_bg_vol.setEnabled(bg_active or self.project.settings.keep_background)
        self.slider_bg_vol.valueChanged.connect(self._on_bg_vol_changed)
        vbox_bg.addWidget(self.slider_bg_vol)
        layout.addLayout(vbox_bg)

        # 3. AI Dubbed Voice Volume
        vbox_dub = QVBoxLayout()
        vbox_dub.setSpacing(2)
        row_dub_hdr = QHBoxLayout()
        self.lbl_dub_title = QLabel(s.get("ai_voice_volume", "AI Voice Volume"))
        self.lbl_dub_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 11px;")
        self.lbl_dub_val = QLabel(f"{int(self.project.settings.dubbed_volume * 100)}%")
        self.lbl_dub_val.setStyleSheet("background: #1e293b; color: #a855f7; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 11px;")
        row_dub_hdr.addWidget(self.lbl_dub_title)
        row_dub_hdr.addStretch()
        row_dub_hdr.addWidget(self.lbl_dub_val)
        vbox_dub.addLayout(row_dub_hdr)

        self.slider_dub_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_dub_vol.setRange(0, 200)
        self.slider_dub_vol.setValue(int(self.project.settings.dubbed_volume * 100))
        self.slider_dub_vol.valueChanged.connect(self._on_dub_vol_changed)
        vbox_dub.addWidget(self.slider_dub_vol)
        layout.addLayout(vbox_dub)

        # 4. AI Voice Speed (ល្បឿនសំឡេង AI និយាយ)
        vbox_spd = QVBoxLayout()
        vbox_spd.setSpacing(2)
        row_spd_hdr = QHBoxLayout()
        self.lbl_spd_title = QLabel(s.get("ai_voice_speed", "AI Voice Speed"))
        self.lbl_spd_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 11px;")
        
        init_spd = getattr(self.project.settings, "ai_voice_speed", 1.05)
        self.lbl_spd_val = QLabel(f"{init_spd:.2f}x")
        self.lbl_spd_val.setStyleSheet("background: #064e3b; color: #34d399; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 11px;")
        
        self.btn_apply_spd_all = QPushButton(s.get("btn_apply_speed_all", "⚡ អនុវត្តគ្រប់វគ្គ"))
        self.btn_apply_spd_all.setStyleSheet("background-color: #047857; color: white; font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: bold;")
        self.btn_apply_spd_all.setToolTip("កំណត់ល្បឿននេះទៅគ្រប់វគ្គទាំងអស់ (Apply this speed to all segments)")
        self.btn_apply_spd_all.clicked.connect(self._apply_global_speed_to_all)
        
        row_spd_hdr.addWidget(self.lbl_spd_title)
        row_spd_hdr.addStretch()
        row_spd_hdr.addWidget(self.lbl_spd_val)
        row_spd_hdr.addWidget(self.btn_apply_spd_all)
        vbox_spd.addLayout(row_spd_hdr)

        row_spd_controls = QHBoxLayout()
        self.slider_ai_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_ai_speed.setRange(75, 150)
        self.slider_ai_speed.setValue(int(init_spd * 100))
        self.slider_ai_speed.valueChanged.connect(self._on_ai_speed_changed)
        row_spd_controls.addWidget(self.slider_ai_speed)

        # Quick preset buttons for instant 1-click selection
        for spd_preset in [0.90, 1.00, 1.05, 1.15, 1.25]:
            btn_p = QPushButton(f"{spd_preset:.2f}x" if spd_preset != 1.0 else "1.0x")
            btn_p.setFixedWidth(36)
            btn_p.setStyleSheet("background: #1e293b; color: #94a3b8; font-size: 10px; padding: 2px; border-radius: 3px;")
            btn_p.clicked.connect(lambda checked, val=spd_preset: self.slider_ai_speed.setValue(int(val * 100)))
            row_spd_controls.addWidget(btn_p)

        vbox_spd.addLayout(row_spd_controls)
        layout.addLayout(vbox_spd)

        # 5. Checkboxes Row 1: Mute & Keep Background Controls & Single Status Badge
        row_checks_1 = QHBoxLayout()
        row_checks_1.setSpacing(8)
        self.chk_mute_orig = QCheckBox(s.get("mute_original", "Mute Original Entirely"))
        self.chk_mute_orig.setStyleSheet("color: #f87171; font-weight: bold; font-size: 11px;")
        self.chk_mute_orig.setChecked(self.project.settings.mute_original)
        self.chk_mute_orig.toggled.connect(self._on_mute_original_toggled)
        row_checks_1.addWidget(self.chk_mute_orig)

        self.chk_keep_bg = QCheckBox(s.get("keep_background", "Keep Background"))
        self.chk_keep_bg.setStyleSheet("color: #38bdf8; font-size: 11px;")
        self.chk_keep_bg.setChecked(self.project.settings.keep_background)
        self.chk_keep_bg.toggled.connect(self._on_keep_bg_toggled)
        row_checks_1.addWidget(self.chk_keep_bg)

        self.lbl_mute_summary = QLabel()
        self.lbl_mute_summary.setStyleSheet("background: #0f172a; color: #94a3b8; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 10px;")
        row_checks_1.addStretch()
        row_checks_1.addWidget(self.lbl_mute_summary)
        layout.addLayout(row_checks_1)

        # 6. Checkboxes Row 2: Subtitle Burn & Chinese Subtitle Masking
        row_checks_2 = QHBoxLayout()
        row_checks_2.setSpacing(8)
        self.chk_burn_sub = QCheckBox(s.get("burn_subtitles", "Burn Subtitles"))
        self.chk_burn_sub.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.chk_burn_sub.setChecked(self.project.settings.burn_subtitles)
        self.chk_burn_sub.toggled.connect(lambda val: setattr(self.project.settings, "burn_subtitles", val))
        row_checks_2.addWidget(self.chk_burn_sub)

        self.chk_mask_sub = QCheckBox(s.get("mask_subtitles", "Mask Original Subtitles"))
        self.chk_mask_sub.setStyleSheet("color: #c084fc; font-size: 11px;")
        self.chk_mask_sub.setChecked(getattr(self.project.settings, "mask_subtitles", True))
        self.chk_mask_sub.toggled.connect(lambda val: setattr(self.project.settings, "mask_subtitles", val))
        row_checks_2.addWidget(self.chk_mask_sub)

        self.btn_mask_settings = QPushButton("⚙️ សារ៉េទីតាំង..." if self.lang == "km" else "⚙️ Position...")
        self.btn_mask_settings.setStyleSheet("background-color: #1e293b; color: #38bdf8; font-size: 11px; padding: 2px 8px; border-radius: 4px; border: 1px solid #0284c7; font-weight: bold;")
        self.btn_mask_settings.setToolTip("បើកផ្ទាំងសារ៉េទីតាំង កម្ពស់ និងកម្រិតបិទបាំងអក្សរចិនដើម (Adjust Subtitle Mask & Positioning)")
        self.btn_mask_settings.clicked.connect(self._open_subtitle_mask_dialog)
        row_checks_2.addWidget(self.btn_mask_settings)
        row_checks_2.addStretch()
        layout.addLayout(row_checks_2)

        self._update_mute_status_display()
        layout.addStretch()
        return panel

    def _create_logo_panel(self) -> QWidget:
        s = STRINGS[self.lang]
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # 1. Enable Logo Checkbox
        self.chk_logo = QCheckBox(s.get("logo_enable", "Overlay Custom Logo on Video"))
        self.chk_logo.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 12px;")
        self.chk_logo.setChecked(self.project.settings.logo_enabled)
        self.chk_logo.toggled.connect(self._on_logo_enabled_toggled)
        layout.addWidget(self.chk_logo)

        # 2. Choose Logo File Row
        row_file = QHBoxLayout()
        row_file.setSpacing(6)
        self.btn_browse_logo = QPushButton(s.get("logo_browse", "🖼️ Choose Logo..."))
        self.btn_browse_logo.setStyleSheet("background-color: #334155; padding: 3px 8px; border-radius: 4px; font-size: 11px;")
        self.btn_browse_logo.clicked.connect(self._on_browse_logo)
        row_file.addWidget(self.btn_browse_logo)

        self.btn_default_logo = QPushButton("✨ Studio Logo")
        self.btn_default_logo.setToolTip("ប្រើប្រាស់ Logo ផ្លូវការរបស់កម្មវិធី (Use built-in AI Dub Studio watermark logo)")
        self.btn_default_logo.setStyleSheet("background-color: #0369a1; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;")
        self.btn_default_logo.clicked.connect(self._on_use_studio_logo)
        row_file.addWidget(self.btn_default_logo)

        logo_name = Path(self.project.settings.logo_path).name if self.project.settings.logo_path else "គ្មាន (None)"
        self.lbl_logo_path = QLabel(logo_name)
        self.lbl_logo_path.setStyleSheet("background: #1e293b; color: #94a3b8; font-size: 11px; padding: 2px 6px; border-radius: 3px;")
        row_file.addWidget(self.lbl_logo_path, stretch=1)

        self.btn_clear_logo = QPushButton("❌")
        self.btn_clear_logo.setFixedSize(24, 24)
        self.btn_clear_logo.clicked.connect(self._on_clear_logo)
        row_file.addWidget(self.btn_clear_logo)
        layout.addLayout(row_file)

        # 3. Logo Position Row
        row_pos = QHBoxLayout()
        row_pos.setSpacing(6)
        self.lbl_logo_pos = QLabel(s.get("logo_position", "Position") + ":")
        self.lbl_logo_pos.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 12px;")
        row_pos.addWidget(self.lbl_logo_pos)

        self.combo_logo_pos = QComboBox()
        self._populate_logo_positions()
        self.combo_logo_pos.currentIndexChanged.connect(self._on_logo_pos_changed)
        row_pos.addWidget(self.combo_logo_pos, stretch=1)
        layout.addLayout(row_pos)

        # 4. Logo Size Slider
        vbox_size = QVBoxLayout()
        vbox_size.setSpacing(2)
        row_size_hdr = QHBoxLayout()
        self.lbl_size_title = QLabel(s.get("logo_size", "Logo Size"))
        self.lbl_size_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 12px;")
        self.lbl_logo_size = QLabel(f"{self.project.settings.logo_width}px")
        self.lbl_logo_size.setStyleSheet("background: #1e293b; color: #38bdf8; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 11px;")
        row_size_hdr.addWidget(self.lbl_size_title)
        row_size_hdr.addStretch()
        row_size_hdr.addWidget(self.lbl_logo_size)
        vbox_size.addLayout(row_size_hdr)

        self.slider_logo_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_size.setRange(40, 320)
        self.slider_logo_size.setValue(self.project.settings.logo_width)
        self.slider_logo_size.valueChanged.connect(self._on_logo_size_changed)
        vbox_size.addWidget(self.slider_logo_size)
        layout.addLayout(vbox_size)

        # 5. Logo Opacity Slider
        vbox_op = QVBoxLayout()
        vbox_op.setSpacing(2)
        row_op_hdr = QHBoxLayout()
        self.lbl_op_title = QLabel(s.get("logo_opacity", "Opacity"))
        self.lbl_op_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 12px;")
        self.lbl_logo_opacity = QLabel(f"{int(self.project.settings.logo_opacity * 100)}%")
        self.lbl_logo_opacity.setStyleSheet("background: #1e293b; color: #c084fc; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 11px;")
        row_op_hdr.addWidget(self.lbl_op_title)
        row_op_hdr.addStretch()
        row_op_hdr.addWidget(self.lbl_logo_opacity)
        vbox_op.addLayout(row_op_hdr)

        self.slider_logo_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_opacity.setRange(20, 100)
        self.slider_logo_opacity.setValue(int(self.project.settings.logo_opacity * 100))
        self.slider_logo_opacity.valueChanged.connect(self._on_logo_opacity_changed)
        vbox_op.addWidget(self.slider_logo_opacity)
        layout.addLayout(vbox_op)

        layout.addStretch()
        return panel

    def _populate_logo_positions(self):
        s = STRINGS[self.lang]
        self.combo_logo_pos.blockSignals(True)
        self.combo_logo_pos.clear()
        self.combo_logo_pos.addItem(s.get("pos_top_right", "Top-Right"), "top_right")
        self.combo_logo_pos.addItem(s.get("pos_top_left", "Top-Left"), "top_left")
        self.combo_logo_pos.addItem(s.get("pos_bottom_right", "Bottom-Right"), "bottom_right")
        self.combo_logo_pos.addItem(s.get("pos_bottom_left", "Bottom-Left"), "bottom_left")
        self.combo_logo_pos.addItem(s.get("pos_center", "Center"), "center")
        idx = self.combo_logo_pos.findData(self.project.settings.logo_position)
        if idx >= 0:
            self.combo_logo_pos.setCurrentIndex(idx)
        self.combo_logo_pos.blockSignals(False)

    def _on_logo_enabled_toggled(self, checked: bool):
        self.project.settings.logo_enabled = checked
        self._sync_logo_to_player()
        self._on_data_changed()

    def _on_browse_logo(self):
        assets_dir = get_resource_path("assets")
        default_dir = str(assets_dir) if assets_dir.exists() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Logo Image", default_dir, "Image Files (*.png *.jpg *.jpeg *.webp *.bmp *.svg)"
        )
        if path:
            self.project.settings.logo_path = path
            self.project.settings.logo_enabled = True
            self.chk_logo.setChecked(True)
            self.lbl_logo_path.setText(Path(path).name)
            self._sync_logo_to_player()
            self._on_data_changed()

    def _on_use_studio_logo(self):
        for rel in ("assets/watermark_logo.png", "assets/logo.png"):
            candidate = get_resource_path(rel)
            if candidate.exists():
                self.project.settings.logo_path = str(candidate)
                self.project.settings.logo_enabled = True
                self.chk_logo.setChecked(True)
                self.lbl_logo_path.setText(candidate.name)
                self._sync_logo_to_player()
                self._on_data_changed()
                break

    def _on_clear_logo(self):
        self.project.settings.logo_path = ""
        self.project.settings.logo_enabled = False
        self.chk_logo.setChecked(False)
        self.lbl_logo_path.setText("គ្មាន (None)")
        self._sync_logo_to_player()
        self._on_data_changed()

    def _on_logo_pos_changed(self):
        pos = self.combo_logo_pos.currentData()
        if pos:
            self.project.settings.logo_position = pos
            self._sync_logo_to_player()
            self._on_data_changed()

    def _on_logo_size_changed(self, val: int):
        self.project.settings.logo_width = val
        if hasattr(self, "lbl_logo_size"):
            self.lbl_logo_size.setText(f"{val}px")
        self._sync_logo_to_player()
        self._on_data_changed()

    def _on_logo_opacity_changed(self, val: int):
        op = val / 100.0
        self.project.settings.logo_opacity = op
        if hasattr(self, "lbl_logo_opacity"):
            self.lbl_logo_opacity.setText(f"{val}%")
        self._sync_logo_to_player()
        self._on_data_changed()

    def _sync_logo_to_player(self):
        st = self.project.settings
        self.video_player.set_logo(
            enabled=st.logo_enabled,
            path=st.logo_path,
            position=st.logo_position,
            width=st.logo_width,
            opacity=st.logo_opacity,
            margin_x=st.logo_margin_x,
            margin_y=st.logo_margin_y
        )

    def _toggle_language(self):
        self.lang = "en" if self.lang == "km" else "km"
        self.project.settings.ui_language = self.lang
        self.setWindowTitle(f"{STRINGS[self.lang]['app_title']} v{APP_VERSION}")
        self._create_menu_bar()
        self.removeToolBar(self.findChild(QToolBar))
        self._create_tool_bar()
        self.segment_table.set_language(self.lang)
        self._update_mixing_labels()
        self.status_bar.showMessage(STRINGS[self.lang]["status_ready"])

    def _on_target_lang_changed(self):
        if not hasattr(self, "cb_target_lang"):
            return
        new_lang = self.cb_target_lang.currentData() or "km"
        self.project.settings.target_language = new_lang
        male_v, female_v = get_voice_pair_for_lang(new_lang)

        # Automatically switch segment voices to the new target language while preserving Male/Female gender
        if self.project.segments:
            for seg in self.project.segments:
                was_female = is_female_voice(seg.voice) or "ស្រី" in seg.speaker or "female" in seg.speaker.lower()
                new_voice = female_v if was_female else male_v
                if seg.voice != new_voice:
                    seg.voice = new_voice
                    seg.tts_file = ""
                    seg.tts_duration = 0.0

        self.segment_table.populate(self.project)
        self._on_data_changed()
        lang_lbl = TARGET_LANG_MAP.get(new_lang, {}).get("label", new_lang)
        self.status_bar.showMessage(f"បានប្ដូរភាសាបកប្រែ និងសំឡេង AI ទៅជា៖ {lang_lbl}", 4500)

    def _populate_trans_providers(self):
        if not hasattr(self, "cb_trans_provider"):
            return
        self.cb_trans_provider.blockSignals(True)
        self.cb_trans_provider.clear()
        if self.lang == "km":
            self.cb_trans_provider.addItem("🌐 Google (ចាស់)", "google")
            self.cb_trans_provider.addItem("✨ Gemini 1.5 Flash", "gemini")
        else:
            self.cb_trans_provider.addItem("🌐 Google (Classic)", "google")
            self.cb_trans_provider.addItem("✨ Gemini 1.5 Flash", "gemini")

        cur_prov = getattr(self.project.settings, "translation_provider", "google") or "google"
        idx = self.cb_trans_provider.findData(cur_prov)
        if idx >= 0:
            self.cb_trans_provider.setCurrentIndex(idx)
        else:
            self.cb_trans_provider.setCurrentIndex(0)
        self.cb_trans_provider.blockSignals(False)

    def _on_trans_provider_changed(self):
        if not hasattr(self, "cb_trans_provider"):
            return
        prov = self.cb_trans_provider.currentData() or "google"
        if prov == "gemini":
            if not self.project.settings.gemini_api_key:
                from PySide6.QtWidgets import QInputDialog
                key, ok = QInputDialog.getText(
                    self,
                    "Gemini API Key",
                    "សូមបញ្ចូល Google Gemini API Key (ឥតគិតថ្លៃពី https://aistudio.google.com/):" if self.lang == "km" else "Enter Google Gemini API Key (Free from https://aistudio.google.com/):",
                    QLineEdit.EchoMode.Normal
                )
                if ok and key.strip():
                    self.project.settings.gemini_api_key = key.strip()
                    self.project.settings.translation_provider = "gemini"
                    self.translation_engine.settings = self.project.settings
                    self.status_bar.showMessage("បានរក្សាទុក Gemini API Key និងបើកប្រើ Gemini 1.5 Flash!", 4500)
                else:
                    # Revert to google
                    self.cb_trans_provider.blockSignals(True)
                    idx = self.cb_trans_provider.findData("google")
                    if idx >= 0:
                        self.cb_trans_provider.setCurrentIndex(idx)
                    self.cb_trans_provider.blockSignals(False)
                    self.project.settings.translation_provider = "google"
                    self.translation_engine.settings = self.project.settings
                    return
            else:
                self.project.settings.translation_provider = "gemini"
                self.translation_engine.settings = self.project.settings
                self.status_bar.showMessage("បានជ្រើសរើស Engine: Google Gemini 1.5 Flash (AI ភាពយន្ត)", 4000)
        else:
            self.project.settings.translation_provider = "google"
            self.translation_engine.settings = self.project.settings
            self.status_bar.showMessage("បានជ្រើសរើស Engine: Google Translate (ចាស់/ឥតគិតថ្លៃ)", 4000)
        self._on_data_changed()

    def _update_mixing_labels(self):
        s = STRINGS[self.lang]
        if hasattr(self, "lbl_orig_title"):
            self.lbl_orig_title.setText(s.get("original_voice_volume", "Original Voice Volume"))
        if hasattr(self, "lbl_bg_title"):
            self.lbl_bg_title.setText(s.get("background_volume", "Background (Music/SFX) Volume"))
        if hasattr(self, "lbl_dub_title"):
            self.lbl_dub_title.setText(s.get("ai_voice_volume", "AI Voice Volume"))
        if hasattr(self, "lbl_spd_title"):
            self.lbl_spd_title.setText(s.get("ai_voice_speed", "AI Voice Speed"))
        if hasattr(self, "btn_apply_spd_all"):
            self.btn_apply_spd_all.setText(s.get("btn_apply_speed_all", "⚡ អនុវត្តគ្រប់វគ្គ"))
        if hasattr(self, "chk_mute_orig"):
            self.chk_mute_orig.setText(s.get("mute_original", "Mute Original Audio"))
        if hasattr(self, "chk_keep_bg"):
            self.chk_keep_bg.setText(s.get("keep_background", "Keep Background"))
        if hasattr(self, "chk_burn_sub"):
            self.chk_burn_sub.setText(s.get("burn_subtitles", "Burn Subtitles"))
        if hasattr(self, "chk_mask_sub"):
            self.chk_mask_sub.setText(s.get("mask_subtitles", "Mask Original Subtitles"))
        if hasattr(self, "btn_mask_settings"):
            self.btn_mask_settings.setText("⚙️ សារ៉េទីតាំង..." if self.lang == "km" else "⚙️ Position...")
        self._update_mute_status_display()

        # Update tabs & logo panel labels
        if hasattr(self, "tool_tabs"):
            self.tool_tabs.setTabText(0, s.get("audio_mixing_tab", "🎛️ សំឡេង (Audio)"))
            self.tool_tabs.setTabText(1, s.get("logo_tool_tab", "🎨 Logo"))
        if hasattr(self, "chk_logo"):
            self.chk_logo.setText(s.get("logo_enable", "Overlay Custom Logo on Video"))
        if hasattr(self, "btn_browse_logo"):
            self.btn_browse_logo.setText(s.get("logo_browse", "🖼️ Choose Logo..."))
        if hasattr(self, "lbl_logo_pos"):
            self.lbl_logo_pos.setText(s.get("logo_position", "Position") + ":")
        self._populate_logo_positions()
        if hasattr(self, "lbl_size_title"):
            self.lbl_size_title.setText(s.get("logo_size", "Logo Size"))
        if hasattr(self, "lbl_op_title"):
            self.lbl_op_title.setText(s.get("logo_opacity", "Opacity"))
        if hasattr(self, "btn_main_preview"):
            self.btn_main_preview.setText("👁️ " + s.get("btn_preview_dubbed", "មើលលទ្ធផលសាកល្បង") + " (Ctrl+P)")

    def _update_mute_status_display(self):
        """Display consolidated 1-phrase mute status and keep checkbox & sliders synced."""
        st = self.project.settings
        orig_on = st.original_voice_volume > 0.001 and not st.mute_original
        bg_on = st.background_volume > 0.001 and (st.keep_background or not st.mute_original)

        orig_str = f"{int(st.original_voice_volume * 100)}%" if orig_on else "OFF"
        bg_str = f"{int(st.background_volume * 100)}%" if bg_on else "OFF"

        status_text = f"Original: {orig_str}, BG: {bg_str}"
        if hasattr(self, "lbl_mute_summary"):
            self.lbl_mute_summary.setText(f"[ {status_text} ]")
            if not orig_on and not bg_on:
                self.lbl_mute_summary.setStyleSheet("background: #7f1d1d; color: #fecaca; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 10px;")
            elif not orig_on and bg_on:
                self.lbl_mute_summary.setStyleSheet("background: #854d0e; color: #fde047; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 10px;")
            else:
                self.lbl_mute_summary.setStyleSheet("background: #064e3b; color: #34d399; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 10px;")

    def _on_mute_original_toggled(self, checked: bool):
        self.project.settings.mute_original = checked
        if checked:
            self.slider_orig_vol.setValue(0)
            self.slider_orig_vol.setEnabled(False)
            self.project.settings.original_voice_volume = 0.0
            self.project.settings.original_volume = 0.0
            if hasattr(self, "lbl_orig_val"):
                self.lbl_orig_val.setText("0% (OFF)")
            if not self.project.settings.keep_background:
                self.slider_bg_vol.setValue(0)
                self.slider_bg_vol.setEnabled(False)
                self.project.settings.background_volume = 0.0
                if hasattr(self, "lbl_bg_val"):
                    self.lbl_bg_val.setText("0% (OFF)")
        else:
            self.slider_orig_vol.setEnabled(True)
            self.slider_orig_vol.setValue(30)
            self.project.settings.original_voice_volume = 0.3
            self.project.settings.original_volume = 0.3
            if hasattr(self, "lbl_orig_val"):
                self.lbl_orig_val.setText("30%")
            self.slider_bg_vol.setEnabled(True)
            if self.slider_bg_vol.value() == 0:
                self.slider_bg_vol.setValue(30)
                self.project.settings.background_volume = 0.3
            if hasattr(self, "lbl_bg_val"):
                self.lbl_bg_val.setText(f"{self.slider_bg_vol.value()}%")
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_keep_bg_toggled(self, checked: bool):
        self.project.settings.keep_background = checked
        if checked:
            self.slider_bg_vol.setEnabled(True)
            if self.slider_bg_vol.value() == 0:
                self.slider_bg_vol.setValue(30)
                self.project.settings.background_volume = 0.3
            if hasattr(self, "lbl_bg_val"):
                self.lbl_bg_val.setText(f"{self.slider_bg_vol.value()}%")
        elif self.project.settings.mute_original:
            self.slider_bg_vol.setValue(0)
            self.slider_bg_vol.setEnabled(False)
            self.project.settings.background_volume = 0.0
            if hasattr(self, "lbl_bg_val"):
                self.lbl_bg_val.setText("0% (OFF)")
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_orig_vol_changed(self, val: int):
        vol = val / 100.0
        self.project.settings.original_voice_volume = vol
        self.project.settings.original_volume = vol
        if hasattr(self, "lbl_orig_val"):
            self.lbl_orig_val.setText(f"{val}%" if val > 0 else "0% (OFF)")
        if val == 0:
            if not self.chk_mute_orig.isChecked() and (self.slider_bg_vol.value() == 0 or not self.project.settings.keep_background):
                self.chk_mute_orig.blockSignals(True)
                self.chk_mute_orig.setChecked(True)
                self.chk_mute_orig.blockSignals(False)
                self.project.settings.mute_original = True
        else:
            if self.chk_mute_orig.isChecked():
                self.chk_mute_orig.blockSignals(True)
                self.chk_mute_orig.setChecked(False)
                self.chk_mute_orig.blockSignals(False)
                self.project.settings.mute_original = False
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_bg_vol_changed(self, val: int):
        vol = val / 100.0
        self.project.settings.background_volume = vol
        if hasattr(self, "lbl_bg_val"):
            self.lbl_bg_val.setText(f"{val}%" if val > 0 else "0% (OFF)")
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_dub_vol_changed(self, val: int):
        self.project.settings.dubbed_volume = val / 100.0
        if hasattr(self, "lbl_dub_val"):
            self.lbl_dub_val.setText(f"{val}%")
        self._on_data_changed()

    def _on_duck_changed(self, val: int):
        self.project.settings.ducking_db = float(val)
        if hasattr(self, "lbl_duck_val"):
            self.lbl_duck_val.setText(f"{val} dB")
        self._on_data_changed()

    def _on_ai_speed_changed(self, val: int):
        spd = round(val / 100.0, 2)
        self.project.settings.ai_voice_speed = spd
        if hasattr(self, "lbl_spd_val"):
            self.lbl_spd_val.setText(f"{spd:.2f}x")
        self._on_data_changed()

    def _apply_global_speed_to_all(self):
        spd = getattr(self.project.settings, "ai_voice_speed", 1.05)
        for seg in self.project.segments:
            seg.speed = spd
            seg.is_modified = True
        self.segment_table.populate(self.project)
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        self._on_data_changed()
        count = len(self.project.segments)
        msg = f"បានកំណត់ល្បឿន {spd:.2f}x ទៅកាន់ {count} វគ្គទាំងអស់!" if self.lang == "km" else f"Applied speed {spd:.2f}x to all {count} segments!"
        self.status_bar.showMessage(msg, 5000)
        QMessageBox.information(self, "Voice Speed Applied", msg)

    def _on_segment_speed_changed(self, seg: Segment, new_speed: float):
        seg.is_modified = True
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        self._on_data_changed()
        self.status_bar.showMessage(f"វគ្គ {seg.id}: បានប្ដូរល្បឿនទៅ {new_speed:.2f}x (ចុច 🔊 ដើម្បីស្ដាប់សាកល្បង)", 3500)

    def _on_player_position_changed(self, curr_sec: float):
        """Update live subtitle overlay on the video player in real-time."""
        if not self.project or not self.project.segments:
            self.video_player.set_subtitle_text("")
            return

        active_text = ""
        for seg in self.project.segments:
            if seg.speech_start <= curr_sec <= seg.speech_end:
                active_text = seg.text_km or seg.text_src
                break

        self.video_player.set_subtitle_text(active_text)

    # --- Project Actions ---
    def _new_project(self):
        self.project = Project()
        self.video_player.stop()
        self.segment_table.populate(self.project)
        self.lbl_stats.setText("0 Segments")
        self.status_bar.showMessage("New project created.")

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "AI Dub Projects (*.aidub *.json);;All Files (*.*)"
        )
        if not path:
            return
        proj = self.db.load_project_from_file(path)
        if proj:
            self.project = proj
            self.segment_table.populate(self.project)
            if self.project.video_path and os.path.exists(self.project.video_path):
                self.video_player.load_video(self.project.video_path)
            if self.project.settings.result_video_path and os.path.exists(self.project.settings.result_video_path):
                self.video_player.set_result_video(self.project.settings.result_video_path)
            self._sync_logo_to_player()
            self._update_stats_display()
            self.status_bar.showMessage(STRINGS[self.lang]["status_saved"])

    def _save_project(self):
        if not self.project.project_file_path:
            self._save_project_as()
        else:
            self.db.save_project_to_file(self.project, self.project.project_file_path)
            self.status_bar.showMessage(STRINGS[self.lang]["status_saved"])

    def _save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", "", "AI Dub Project (*.aidub);;JSON (*.json)"
        )
        if path:
            self.db.save_project_to_file(self.project, path)
            self.status_bar.showMessage(STRINGS[self.lang]["status_saved"])

    def _import_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Video", "", "Video Files (*.mp4 *.mkv *.avi *.mov);;All Files (*.*)"
        )
        if not path:
            return
        self.project.video_path = path
        self.project.name = Path(path).stem
        self.video_player.load_video(path)
        self._sync_logo_to_player()
        self.status_bar.showMessage(f"Loaded video: {Path(path).name}")

    # --- Pipeline Workflow Execution (Async) ---
    def _run_transcription(self):
        if not self.project.video_path or not os.path.exists(self.project.video_path):
            QMessageBox.warning(self, "No Video", "សូមនាំចូលវីដេអូជាមុនសិន! (Please import a video first)")
            return

        self._start_worker(
            "Transcription",
            self._worker_transcribe,
            self._on_transcribe_finished
        )

    def _worker_transcribe(self, progress_cb):
        progress_cb(5, "Extracting audio with FFmpeg...")
        audio_path = self.audio_engine.extract_audio(self.project.video_path)
        self.project.audio_path = audio_path
        
        progress_cb(15, f"Transcribing with faster-whisper ({self.project.settings.whisper_model})...")
        stt = STTEngine(model_size=self.project.settings.whisper_model)
        segments = stt.transcribe(
            audio_path=audio_path,
            language=self.project.settings.source_language,
            progress_cb=progress_cb
        )
        return segments

    def _on_transcribe_finished(self, segments):
        t_lang = getattr(self.project.settings, "target_language", "km") or "km"
        male_v, female_v = get_voice_pair_for_lang(t_lang)
        for seg in segments:
            seg.voice = female_v if is_female_voice(seg.voice) else male_v
        self.project.segments = segments
        self.segment_table.populate(self.project)
        self._update_stats_display()
        self.status_bar.showMessage(f"Transcribed {len(segments)} segments.")
        QMessageBox.information(self, "Transcribe Complete", f"បានស្រង់យក {len(segments)} វគ្គដោយជោគជ័យ!")

    def _run_translation(self):
        if not self.project.segments:
            QMessageBox.warning(self, "No Segments", "សូមដំណើរការស្ដាប់សំឡេង (Transcribe) ជាមុនសិន!")
            return

        self._start_worker(
            "Translation",
            self._worker_translate,
            self._on_translate_finished
        )

    def _worker_translate(self, progress_cb):
        total = len(self.project.segments)
        t_lang = getattr(self.project.settings, "target_language", "km") or "km"
        lang_lbl = TARGET_LANG_MAP.get(t_lang, {}).get("label", t_lang)
        male_v, female_v = get_voice_pair_for_lang(t_lang)
        for i, s in enumerate(self.project.segments):
            pct = int((i / max(1, total)) * 100)
            progress_cb(pct, f"កំពុងបកប្រែវគ្គ {i+1}/{total} ទៅជាសម្ដីនិយាយធម្មជាតិ ({lang_lbl})...")
            prev_txt = self.project.segments[i - 1].text_src if i > 0 else ""
            next_txt = self.project.segments[i + 1].text_src if i + 1 < total else ""
            s.text_km = self.translation_engine.translate_segment(
                src_text=s.text_src,
                glossary=self.project.glossary,
                context_before=prev_txt,
                context_after=next_txt,
                target_lang=t_lang,
                slot_duration=s.slot
            )
            # Ensure segment voice matches target language & invalidate old TTS audio
            s.voice = male_v
            s.tts_file = ""
            s.tts_duration = 0.0
        return self.project.segments

    def _on_translate_finished(self, _):
        self.segment_table.populate(self.project)
        t_lang = getattr(self.project.settings, "target_language", "km") or "km"
        lang_lbl = TARGET_LANG_MAP.get(t_lang, {}).get("label", t_lang)
        self.status_bar.showMessage(f"Translation into {lang_lbl} complete.")
        QMessageBox.information(self, "Translation Complete", f"ការបកប្រែទៅជា {lang_lbl} បានរួចរាល់!")

    def _run_tts_generation(self):
        if not self.project.segments:
            QMessageBox.warning(
                self,
                "មិនទាន់មានទិន្នន័យ",
                "មិនទាន់មានវគ្គសន្ទនា (Segments) នៅឡើយទេ។ សូមដំណើរការ '១. ស្ដាប់សំឡេង (Transcribe)' ឬនាំចូលអក្សររត់ជាមុនសិន!"
            )
            return

        has_khmer = any(bool(s.text_km and s.text_km.strip()) for s in self.project.segments)
        has_src = any(bool(s.text_src and s.text_src.strip()) for s in self.project.segments)
        if not has_khmer and not has_src:
            QMessageBox.warning(
                self,
                "មិនទាន់មានអត្ថបទ",
                "មិនទាន់មានអត្ថបទសន្ទនានៅឡើយទេ។ សូមដំណើរការ '១. ស្ដាប់សំឡេង' ជាមុនសិន!"
            )
            return

        self._start_worker(
            "TTS Generation",
            self._worker_tts,
            self._on_tts_finished
        )

    def _worker_tts(self, progress_cb):
        import time
        import re
        total = len(self.project.segments)
        global_speed = getattr(self.project.settings, "ai_voice_speed", 1.05)
        t_lang = getattr(self.project.settings, "target_language", "km") or "km"

        # Check for segments missing translation (or still containing untranslated foreign script when target is Khmer)
        missing_km = [
            s for s in self.project.segments
            if s.text_src.strip() and (
                not s.text_km.strip() or (
                    t_lang == "km" and not re.search(r'[\u1780-\u17d3\u17d5-\u17ff]', s.text_km)
                )
            )
        ]
        if missing_km:
            progress_cb(2, f"កំពុងបកប្រែវគ្គខ្វះ {len(missing_km)} វគ្គ...")
            try:
                self.translation_engine.translate_segments(missing_km, target_lang=t_lang)
            except Exception as e:
                print(f"[MainWindow] Auto-translate notice: {e}")

        for i, s in enumerate(self.project.segments):
            pct = int((i / max(1, total)) * 85)
            target_speed = s.speed if (s.is_modified and s.speed != 1.0) else global_speed

            progress_cb(pct, f"កំពុងបង្កើតសំឡេង AI ប្រុស វគ្គ {i+1}/{total} (ID: {s.id})...")
            self.tts_engine.generate_segment_tts(s, speed_factor=target_speed)
            s.is_modified = False
            time.sleep(0.08)

        # Automatically run 4-stage time-sync & lip-sync alignment right after generating voices!
        progress_cb(90, "កំពុងតម្រឹមសំឡេងឱ្យស៊ីគ្នាជាមួយមាត់តួអង្គ (Auto Lip-Sync)...")
        self.current_report = self.sync_engine.synchronize_segments(self.project)
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        progress_cb(100, "បានបង្កើត និងតម្រឹមសំឡេងរួចរាល់ ១០០%!")
        return self.project.segments

    def _on_tts_finished(self, _):
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        self.segment_table.populate(self.project)
        self.status_bar.showMessage("Khmer AI voice generation complete.")
        QMessageBox.information(self, "TTS Complete", "បានបង្កើតឯកសារសំឡេង AI ខ្មែរគ្រប់វគ្គ!")

    def _run_time_sync(self):
        if not self.project.segments:
            return

        self._start_worker(
            "Time Sync",
            self._worker_sync,
            self._on_sync_finished
        )

    def _worker_sync(self, progress_cb):
        report = self.sync_engine.synchronize_segments(self.project, progress_cb=progress_cb)
        return report

    def _on_sync_finished(self, report: SyncReport):
        self.current_report = report
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        self.segment_table.populate(self.project)
        self._update_stats_display()
        self.status_bar.showMessage("Time synchronization complete.")
        
        # Show sync report dialog automatically
        self._show_sync_report()

    def _run_preview_result(self):
        """Generate ultra-fast pre-export preview video and start playing in Preview player."""
        if not self.project.video_path or not os.path.exists(self.project.video_path):
            QMessageBox.warning(self, "No Video", "សូមនាំចូលវីដេអូដើមជាមុនសិន!")
            return

        has_any_tts = any(bool(s.tts_file and os.path.exists(s.tts_file)) for s in self.project.segments)
        if not has_any_tts:
            QMessageBox.warning(
                self,
                "No Audio Clips",
                "មិនទាន់មានសំឡេង AI ខ្មែរនៅឡើយទេ។ សូមចុច '🗣️ ៣. បង្កើតសំឡេង AI ខ្មែរ' ជាមុនសិន ទើបអាចស្ដាប់សាកល្បងបាន!"
            )
            return

        def _on_preview_done(preview_mp4):
            self.video_player.set_preview_video(preview_mp4)
            self.status_bar.showMessage("វីដេអូ Preview រួចរាល់ហើយ! កំពុងចាក់សាកល្បងលទ្ធផល...", 6000)

        self._start_worker(
            "Generating Preview",
            lambda progress_cb=None, *args, **kwargs: self.export_engine.generate_fast_preview(
                project=self.project,
                progress_cb=progress_cb
            ),
            _on_preview_done
        )

    def _export_dubbed(self):
        if not self.project.video_path:
            QMessageBox.warning(self, "No Video", "សូមនាំចូលវីដេអូជាមុនសិន!")
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export Dubbed Video", "", "MP4 Video (*.mp4)"
        )
        if not out_path:
            return

        def _on_export_done(_):
            self.project.settings.result_video_path = out_path
            self.video_player.set_result_video(out_path)
            QMessageBox.information(
                self,
                "Export Complete",
                f"វីដេអូ Result ត្រូវបាននាំចេញ និងកំពុងចាក់បង្ហាញលើផ្ទាំង Preview ភ្លាមៗ!\n{out_path}"
            )

        self._start_worker(
            "Exporting Video",
            lambda progress_cb=None, *args, **kwargs: self.export_engine.export_all(
                project=self.project,
                output_mp4_path=out_path,
                progress_cb=progress_cb
            ),
            _on_export_done
        )

    def _export_srt(self):
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Subtitles", "", "SRT Subtitle (*.srt)")
        if not out_path:
            return
        from app.utils import generate_srt
        content = generate_srt(self.project.segments, language="km")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        QMessageBox.information(self, "Exported", f"បានរក្សាទុកឯកសារអក្សររត់ក្រោម៖\n{out_path}")

    def _export_wav(self):
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Audio", "", "WAV Audio (*.wav)")
        if not out_path:
            return
        total_dur = self.audio_engine.get_media_duration(self.project.video_path) if self.project.video_path else 60.0
        self.audio_engine.build_dubbed_audio_track(total_dur, self.project.segments, output_wav=out_path)
        QMessageBox.information(self, "Exported", f"បាននាំចេញឯកសារសំឡេងបញ្ចូល៖\n{out_path}")

    # --- Auxiliary Dialogs ---
    def _preview_segment_tts(self, seg: Segment):
        """Preview Khmer voice clip for segment."""
        # If missing Khmer translation, try to translate on the fly
        if not seg.text_km.strip() and seg.text_src.strip():
            self.status_bar.showMessage(f"កំពុងបកប្រែវគ្គ {seg.id} ជាភាសាខ្មែរ...")
            try:
                self.translation_engine.translate_segments([seg])
                self.segment_table.populate(self.project)
            except Exception as e:
                print(f"[MainWindow] Translation error: {e}")

        if not seg.text_km.strip():
            QMessageBox.warning(
                self,
                "គ្មានអត្ថបទខ្មែរ",
                f"វគ្គ {seg.id} មិនទាន់មានអត្ថបទជាភាសាខ្មែរនៅឡើយទេ។ សូមបញ្ចូល ឬបកប្រែជាភាសាខ្មែរជាមុនសិន!"
            )
            return

        self.status_bar.showMessage(f"កំពុងបង្កើត/ទាញយកសំឡេង AI វគ្គ {seg.id}...")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        if not seg.tts_file or not os.path.exists(seg.tts_file) or seg.is_modified:
            self.tts_engine.generate_segment_tts(seg, speed_factor=seg.speed)
            seg.is_modified = False
            self.segment_table.populate(self.project)

        if seg.tts_file and os.path.exists(seg.tts_file):
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtCore import QUrl
            if not hasattr(self, "_preview_player") or self._preview_player is None:
                self._preview_player = QMediaPlayer(self)
                self._preview_audio = QAudioOutput(self)
                self._preview_player.setAudioOutput(self._preview_audio)

            self._preview_audio.setVolume(1.0)
            self._preview_audio.setMuted(False)
            self._preview_player.stop()
            self._preview_player.setSource(QUrl.fromLocalFile(seg.tts_file))
            self._preview_player.play()
            self.status_bar.showMessage(f"កំពុងចាក់សំឡេង AI វគ្គ {seg.id} ({seg.tts_duration:.2f}s, {seg.speed:.2f}x)...", 4000)

    def _open_speaker_manager(self):
        dlg = SpeakerManagerDialog(self.project, lang=self.lang, parent=self)
        dlg.speakers_updated.connect(lambda: self.segment_table.populate(self.project))
        dlg.exec()

    def _show_sync_report(self):
        if not self.current_report:
            # Build report on the fly
            self.current_report = SyncReport(total_segments=len(self.project.segments))
            for s in self.project.segments:
                self.current_report.items.append(
                    from_seg := type("Item", (), {
                        "segment_id": s.id,
                        "slot_sec": s.slot,
                        "original_tts_sec": s.tts_duration,
                        "final_tts_sec": s.effective_duration,
                        "sync_status": s.sync_status,
                        "borrowed_gap_sec": s.borrowed_gap,
                        "speed_factor": s.speed,
                        "notes": s.sync_notes,
                        "is_overflow": (s.sync_status == "OVERFLOW")
                    })()
                )
                if s.sync_status == "OK": self.current_report.ok_count += 1
                elif s.sync_status == "OVERFLOW": self.current_report.overflow_count += 1
                else: self.current_report.speed_adjusted_count += 1

        dlg = SyncReportDialog(self.current_report, lang=self.lang, parent=self)
        dlg.jump_to_segment.connect(self._jump_to_segment_row)
        dlg.exec()

    def _jump_to_segment_row(self, segment_id: int):
        for r in range(self.segment_table.table.rowCount()):
            item = self.segment_table.table.item(r, self.segment_table.COL_ID)
            if item and item.text() == str(segment_id):
                self.segment_table.table.selectRow(r)
                self.segment_table.table.scrollToItem(item)
                break

    def _open_batch_dialog(self):
        dlg = BatchQueueDialog(lang=self.lang, parent=self)
        dlg.exec()

    def _open_settings(self):
        dlg = SettingsDialog(self.project.settings, lang=self.lang, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.translation_engine.settings = self.project.settings
            if hasattr(self, "cb_trans_provider"):
                self._populate_trans_providers()
            if hasattr(self, "cb_target_lang"):
                t_idx = self.cb_target_lang.findData(getattr(self.project.settings, "target_language", "km") or "km")
                if t_idx >= 0 and t_idx != self.cb_target_lang.currentIndex():
                    self.cb_target_lang.setCurrentIndex(t_idx)
                else:
                    self._on_target_lang_changed()
            self._update_mixing_labels()
            self._on_data_changed()

    def _open_subtitle_mask_dialog(self):
        from app.ui.subtitle_mask_dialog import SubtitleMaskDialog
        cur_time = 2.0
        if hasattr(self, "video_player") and hasattr(self.video_player, "get_current_time"):
            try:
                cur_time = self.video_player.get_current_time()
            except Exception:
                cur_time = 2.0

        dlg = SubtitleMaskDialog(
            settings=self.project.settings,
            video_path=self.project.video_path,
            current_time=cur_time,
            lang=self.lang,
            parent=self
        )
        if dlg.exec():
            if hasattr(self, "chk_mask_sub"):
                self.chk_mask_sub.setChecked(self.project.settings.mask_subtitles)
            self.status_bar.showMessage(
                "បានរក្សាទុកទីតាំងផ្ទាំងបិទបាំងអក្សរចិន (Subtitle Mask) រួចរាល់!"
                if self.lang == "km" else
                "Subtitle mask and positioning saved successfully!",
                4000
            )
            self._on_data_changed()

    def _show_srs_help(self):
        srs_path = get_resource_path("SRS_KHMER.md")
        content = "ឯកសារតម្រូវការប្រព័ន្ធ (SRS)"
        if srs_path.exists():
            with open(srs_path, "r", encoding="utf-8") as f:
                content = f.read()
        
        dlg = QDialog(self)
        dlg.setWindowTitle("ឯកសារតម្រូវការប្រព័ន្ធ (Software Requirements Specification)")
        dlg.resize(800, 600)
        v = QVBoxLayout(dlg)
        from PySide6.QtWidgets import QTextBrowser
        tb = QTextBrowser()
        tb.setMarkdown(content)
        v.addWidget(tb)
        dlg.exec()

    def _show_about(self):
        from PySide6.QtGui import QPixmap
        box = QMessageBox(self)
        box.setWindowTitle("About AI Movie Dubbing Studio")
        logo_file = get_resource_path("assets/logo.png")
        if logo_file.exists():
            pix = QPixmap(str(logo_file)).scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            box.setIconPixmap(pix)
        box.setText(
            f"<b>AI Movie Translator & Auto Dubbing Studio</b><br>"
            f"កម្មវិធីបកប្រែខ្សែភាពយន្ត និងបញ្ចូលសំឡេងស្វ័យប្រវត្តិតាម AI<br><br>"
            f"Version <b>{APP_VERSION}</b> ({APP_BUILD_DATE})<br><br>"
            f"Designed and built strictly to SRS specifications.<br>"
            f"Features faster-whisper, Microsoft Edge Khmer Neural TTS, "
            f"4-Stage Cascade Time-Synchronization, and FFmpeg audio ducking."
        )
        box.exec()

    def _check_updates_dialog(self):
        from app.ui.update_dialog import UpdateDialog
        url = getattr(self.project.settings, "update_check_url", None) or None
        dlg = UpdateDialog(manifest_url=url, lang=self.lang, parent=self)
        dlg.exec()

    def _silent_startup_update_check(self):
        from app.updater import CheckUpdateWorker, UpdateInfo
        url = getattr(self.project.settings, "update_check_url", None) or None
        self._startup_update_worker = CheckUpdateWorker(manifest_url=url)
        def _on_found(info: UpdateInfo):
            self.btn_status_ver.setText(f"🎉 v{info.latest_version} Update!")
            self.btn_status_ver.setStyleSheet("""
                QPushButton {
                    background-color: #0284c7;
                    color: white;
                    font-size: 11px;
                    font-weight: bold;
                    border-radius: 4px;
                    padding: 2px 8px;
                    margin-right: 8px;
                }
                QPushButton:hover {
                    background-color: #0369a1;
                }
            """)
            self.status_bar.showMessage(f"🎉 មានកំណែថ្មី v{info.latest_version} រួចរាល់! ចុចលើប៊ូតុង Update ដើម្បីដំឡើង។", 8000)
        self._startup_update_worker.update_found.connect(_on_found)
        self._startup_update_worker.start()

    # --- Worker Thread Helper ---
    def _start_worker(self, name: str, task_fn, on_success):
        if hasattr(self, "_current_worker") and self._current_worker and self._current_worker.isRunning():
            QMessageBox.warning(
                self,
                "កំពុងដំណើរការ",
                f"កំពុងដំណើរការ '{self._current_worker.task_name}' នៅឡើយទេ។ សូមរង់ចាំឱ្យចប់ជាមុនសិន!"
            )
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage(f"Starting {name}...")

        worker = PipelineWorker(name, task_fn)
        worker.progress.connect(self._on_worker_progress)
        worker.finished_success.connect(lambda res: self._on_worker_success(res, on_success))
        worker.finished_error.connect(self._on_worker_error)
        self._current_worker = worker
        worker.start()

    def closeEvent(self, event):
        if hasattr(self, "_current_worker") and self._current_worker and self._current_worker.isRunning():
            self.status_bar.showMessage("កំពុងរង់ចាំដំណើរការបញ្ចប់...")
            self._current_worker.wait(2000)
        event.accept()

    def _on_worker_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_bar.showMessage(msg)

    def _on_worker_success(self, res, on_success_callback):
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage("Ready")
        on_success_callback(res)
        self._on_data_changed()

    def _on_worker_error(self, err_msg: str):
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage(f"Error: {err_msg[:40]}")
        QMessageBox.critical(self, "Processing Error", f"ដំណើរការមានបញ្ហា៖\n{err_msg}")

    # --- Auto-Save & Recovery ---
    def _setup_autosave(self):
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30000) # Every 30 seconds
        self.autosave_timer.timeout.connect(self._do_autosave)
        self.autosave_timer.start()

    def _do_autosave(self):
        if self.project and self.project.segments:
            self.db.auto_save_project(self.project)

    def _check_resume_session(self):
        if self.db.has_autosave():
            box = QMessageBox(self)
            box.setWindowTitle("ស្តារឡើងវិញ (Resume Session)")
            box.setText("បានរកឃើញគម្រោងមុនដែលមិនទាន់បានបិទត្រឹមត្រូវ។ តើអ្នកចង់ស្តារឡើងវិញទេ?")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Yes:
                recovered = self.db.load_autosave()
                if recovered:
                    self.project = recovered
                    self.segment_table.populate(self.project)
                    if self.project.video_path and os.path.exists(self.project.video_path):
                        self.video_player.load_video(self.project.video_path)
                    self._update_stats_display()
                    self.status_bar.showMessage("បានស្តារគម្រោងឡើងវិញ។")

    def _on_data_changed(self):
        self._update_stats_display()

    def _update_stats_display(self):
        total = len(self.project.segments)
        overflows = sum(1 for s in self.project.segments if s.sync_status == "OVERFLOW")
        if overflows > 0:
            self.lbl_stats.setText(f"{total} Segments | ⚠️ {overflows} Overflows")
            self.lbl_stats.setStyleSheet("color: #f43f5e; font-weight: bold;")
        else:
            self.lbl_stats.setText(f"{total} Segments")
            self.lbl_stats.setStyleSheet("color: #94a3b8; font-size: 11px;")
