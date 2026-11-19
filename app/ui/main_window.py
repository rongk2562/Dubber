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
    QCheckBox, QDialog, QTabWidget, QComboBox, QLineEdit,
    QSystemTrayIcon, QSizePolicy, QApplication, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings, QRect
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

from app.ui.theme import (
    ToggleSwitch, SegmentedControl,
    get_active_screen, is_geometry_on_any_screen, clamp_widget_to_screen
)
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
        self._srt_undo_stack = []
        self._srt_redo_stack = []
        self._skip_imported_on_translate = False
        
        # Engines
        self.audio_engine = AudioEngine()
        self.tts_engine = TTSEngine()
        self.translation_engine = TranslationEngine(self.project.settings)
        self.sync_engine = SyncEngine(self.audio_engine, self.tts_engine, self.translation_engine)
        self.export_engine = ExportEngine(self.audio_engine)

        # Notification Manager (Singleton) & System Tray
        from app.notifications import NotificationManager
        self.notif_manager = NotificationManager.instance()
        self.notif_manager.configure(
            enabled=getattr(self.project.settings, "notify_enabled", True),
            show_toast=getattr(self.project.settings, "notify_show_toast", True),
            tray_enabled=getattr(self.project.settings, "notify_windows_tray", True),
            interval_hours=int(getattr(self.project.settings, "notify_interval_hours", 6)),
            sound_enabled=getattr(self.project.settings, "notify_sound", True),
            lang=self.lang,
        )
        self._active_toast = None
        self.tray_icon = None

        self._init_ui()
        self._setup_notifications_ui()
        self._setup_autosave()
        if "--self-test" not in sys.argv and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self._check_resume_session()
        if getattr(self.project.settings, "auto_check_updates", True):
            QTimer.singleShot(3500, self._silent_startup_update_check)
        # Non-blocking async remote announcements check (adds ~0ms startup time)
        QTimer.singleShot(800, lambda: self.notif_manager.poll_remote_async(force=False))

    def _apply_screen_constraints(self, screen=None):
        scr = screen or get_active_screen(self)
        if scr is None:
            return
        avail = scr.availableGeometry()
        min_w = min(1024, max(640, avail.width() - 20))
        min_h = min(600, max(480, avail.height() - 20))
        self.setMinimumSize(min_w, min_h)
        clamp_widget_to_screen(self, scr, max_ratio=1.0)

    def _restore_window_geometry(self):
        scr = get_active_screen(self)
        avail = scr.availableGeometry() if scr else QRect(0, 0, 1366, 768)
        default_w = min(avail.width(), max(self.minimumWidth(), int(avail.width() * 0.92)))
        default_h = min(avail.height(), max(self.minimumHeight(), int(avail.height() * 0.92)))

        restored = False
        if "--self-test" not in sys.argv and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            qs = QSettings("KhmerAIDubStudio", "AIMovieDubbing")
            saved_rect = qs.value("main_window/rect")
            if isinstance(saved_rect, QRect) and is_geometry_on_any_screen(saved_rect):
                self.setGeometry(saved_rect)
                restored = True
            saved_split = qs.value("main_window/splitter")
            if saved_split:
                try:
                    self.splitter.restoreState(saved_split)
                except Exception:
                    pass

        if not restored:
            self.resize(default_w, default_h)
            x = avail.left() + max(0, (avail.width() - default_w) // 2)
            y = avail.top() + max(0, (avail.height() - default_h) // 2)
            self.move(x, y)
            self.splitter.setSizes([int(default_w * 0.34), int(default_w * 0.66)])

        clamp_widget_to_screen(self, scr, max_ratio=1.0)

    def _init_ui(self):
        self._screen_hook_connected = False
        self._apply_screen_constraints()
        self.setWindowTitle(f"{STRINGS[self.lang]['app_title']} v{APP_VERSION}")
        for icon_rel in ("assets/logo.ico", "assets/logo.png", "assets/logo.svg"):
            icon_file = get_resource_path(icon_rel)
            if icon_file.exists():
                self.setWindowIcon(QIcon(str(icon_file)))
                break

        # Central Widget & Main Layout (QVBoxLayout -> Header Stepper + Update Banner + Splitter)
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # Header Stepper & Top-Right Corner Controls
        self.header_bar = self._create_header_stepper()
        main_layout.addWidget(self.header_bar)

        from app.ui.notification_ui import UpdateBannerWidget
        self.update_banner = UpdateBannerWidget(self.notif_manager, self)
        main_layout.addWidget(self.update_banner)

        # Splitter: Left (Scrollable Video + Mixing, Pinned Bottom Preview) | Right (Segment Editor)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(5)

        # --- Left Panel ---
        left_panel = QWidget()
        left_panel.setMinimumWidth(300)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(6)

        self.left_scroll = QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        left_scroll_body = QWidget()
        left_scroll_body.setObjectName("scrollBody")
        lsb_layout = QVBoxLayout(left_scroll_body)
        lsb_layout.setContentsMargins(0, 0, 2, 0)
        lsb_layout.setSpacing(8)

        self.video_player = VideoPlayerWidget()
        self.video_player.position_changed.connect(self._on_player_position_changed)
        self.video_player.preview_requested.connect(self._run_preview_result)
        self.video_player.rerender_requested.connect(self._export_dubbed)
        self.video_player.video_dropped.connect(self._load_video_path)
        self.video_player.browse_requested.connect(self._import_video)
        self.video_player.logo_dragged.connect(self._on_logo_dragged_in_preview)
        lsb_layout.addWidget(self.video_player, stretch=1)

        # Lower Tools Tabs (Audio Mixing & Watermark Logo Tool)
        self.tool_tabs = QTabWidget()
        self.mixing_panel = self._create_mixing_panel()
        self.logo_panel = self._create_logo_panel()

        s = STRINGS[self.lang]
        self.tool_tabs.addTab(self.mixing_panel, "🎛️ សំឡេង" if self.lang == "km" else "🎛️ Audio")
        self.tool_tabs.addTab(self.logo_panel, "🎨 Logo")
        self.tool_tabs.currentChanged.connect(self._on_tool_tab_changed)
        lsb_layout.addWidget(self.tool_tabs, stretch=0)
        self._sync_overlays_to_player()

        self.left_scroll.setWidget(left_scroll_body)
        left_layout.addWidget(self.left_scroll, stretch=1)

        # Secondary Preview Button pinned OUTSIDE scroll area at bottom
        self.btn_main_preview = QPushButton("👁️ មើលលទ្ធផលសាកល្បង (Ctrl+P)" if self.lang == "km" else "👁️ Preview Result (Ctrl+P)")
        self.btn_main_preview.setToolTip("បង្កើត និងចាក់មើលលទ្ធផលសាកល្បងលឿនមុនពេល Export (Ctrl+P)")
        self.btn_main_preview.clicked.connect(self._run_preview_result)
        left_layout.addWidget(self.btn_main_preview, stretch=0)

        self.splitter.addWidget(left_panel)

        # --- Right Panel ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(6)

        self.segment_table = SegmentTableWidget()
        self.segment_table.play_segment_requested.connect(self.video_player.play_segment)
        self.segment_table.preview_tts_requested.connect(self._preview_segment_tts)
        self.segment_table.segment_speed_changed.connect(self._on_segment_speed_changed)
        self.segment_table.data_changed.connect(self._on_data_changed)
        self.segment_table.generate_tts_requested.connect(self._run_tts_generation)
        self._populate_engine_bar(self.segment_table.engine_bar)
        right_layout.addWidget(self.segment_table)

        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(lambda _p, _i: self._reflow_engine_bar())

        main_layout.addWidget(self.splitter, stretch=1)

        # Build MenuBar
        self._create_menu_bar()

        # Status Bar: Status + Progress + Task Name + Cancel + Segment Count + Version
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_task_name = QLabel("")
        self.lbl_task_name.setStyleSheet("color: #2D8CFF; font-weight: 600; font-size: 8.5pt; margin-right: 6px;")
        self.lbl_task_name.setVisible(False)
        self.status_bar.addPermanentWidget(self.lbl_task_name)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumWidth(90)
        self.progress_bar.setMaximumWidth(180)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

        self.btn_cancel_task = QPushButton("✖ បោះបង់" if self.lang == "km" else "✖ Cancel")
        self.btn_cancel_task.setProperty("variant", "danger")
        self.btn_cancel_task.setVisible(False)
        self.btn_cancel_task.clicked.connect(self._cancel_current_worker)
        self.status_bar.addPermanentWidget(self.btn_cancel_task)

        self.lbl_stats = QLabel("0 ឃ្លា" if self.lang == "km" else "0 Segments")
        self.lbl_stats.setStyleSheet("color: #9AA8C4; font-size: 8.5pt; margin-right: 10px;")
        self.status_bar.addPermanentWidget(self.lbl_stats)

        self.btn_status_ver = QPushButton(f"v{APP_VERSION}")
        self.btn_status_ver.setFlat(True)
        self.btn_status_ver.setToolTip("ពិនិត្យកំណែថ្មី" if self.lang == "km" else "Check for updates")
        self.btn_status_ver.clicked.connect(self._check_updates_dialog)
        self.status_bar.addPermanentWidget(self.btn_status_ver)

        self.status_bar.showMessage(STRINGS[self.lang]["status_ready"])
        self._restore_window_geometry()
        self._update_stepper_states()
        self._update_responsive_header()

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
        menu_file.addAction("📦 Batch (ដំណើរការទាំង Folder)..." if self.lang == "km" else "📦 Batch (Folder Queue)...", self._open_batch_dialog, "Ctrl+Shift+B")
        menu_file.addAction("📥 នាំចូល SRT (Import SRT)..." if self.lang == "km" else "📥 Import SRT...", self._import_srt_external)
        menu_file.addAction("📤 Export SRT ចិន (សម្រាប់បកប្រែ)..." if self.lang == "km" else "📤 Export Chinese SRT (for Translation)...", self._export_zh_srt_chatgpt)
        menu_file.addSeparator()
        menu_file.addAction(s["export_dubbed"], self._export_dubbed, "Ctrl+E")
        menu_file.addAction(s["export_srt"], self._export_srt)
        menu_file.addAction(s["export_wav"], self._export_wav)
        menu_file.addSeparator()
        menu_file.addAction(s["exit"], self.close, "Ctrl+Q")

        # Edit Menu
        menu_edit = menubar.addMenu(s["edit"])
        self.act_undo_srt = menu_edit.addAction("↩️ Undo នាំចូល SRT" if self.lang == "km" else "↩️ Undo SRT Import", self._undo_srt_import, "Ctrl+Z")
        self.act_redo_srt = menu_edit.addAction("↪️ Redo នាំចូល SRT" if self.lang == "km" else "↪️ Redo SRT Import", self._redo_srt_import, "Ctrl+Y")
        self.act_undo_srt.setEnabled(False)
        self.act_redo_srt.setEnabled(False)
        menu_edit.addSeparator()
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

    def _create_header_stepper(self) -> QWidget:
        """Create responsive 6-step horizontal stepper header + compact/overflow '⋯' + top-right utilities."""
        header = QWidget()
        header.setObjectName("headerStepper")
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(10, 4, 10, 4)
        h_layout.setSpacing(6)

        self.step_buttons = []
        self.step_seps = []
        self._step_meta = [
            ("① នាំចូល", "① Import", "🎬", "នាំចូលវីដេអូ (Ctrl+I)", "Import video (Ctrl+I)"),
            ("② អត្ថបទចិន", "② Chinese Text", "🎙️", "ស្រង់អក្សរចិនពីសំឡេង/OCR", "Extract Chinese text (ASR/OCR)"),
            ("③ បកប្រែ", "③ Translate", "🌐", "បកប្រែអត្ថបទទៅជាភាសាគោលដៅ", "Translate segments"),
            ("④ សំឡេង AI", "④ AI Voice", "🗣️", "បង្កើតសំឡេង AI និងតម្រឹមពេលវេលា", "Generate AI voices & auto-sync"),
            ("⑤ មើលជាមុន", "⑤ Preview", "👁️", "មើលលទ្ធផលសាកល្បង (Ctrl+P)", "Preview dubbed video (Ctrl+P)"),
            ("⑥ នាំចេញ", "⑥ Export", "🚀", "នាំចេញវីដេអូ MP4 (Ctrl+E)", "Export final MP4 video (Ctrl+E)"),
        ]

        for idx, (km_lbl, en_lbl, icon_ch, km_tip, en_tip) in enumerate(self._step_meta):
            btn = QPushButton(km_lbl if self.lang == "km" else en_lbl)
            btn.setObjectName("stepPill")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(km_tip if self.lang == "km" else en_tip)
            btn.clicked.connect(lambda checked=False, s_idx=idx: self._on_stepper_clicked(s_idx))
            self.step_buttons.append(btn)
            h_layout.addWidget(btn)
            if idx < len(self._step_meta) - 1:
                sep = QLabel("›")
                sep.setStyleSheet("color: #475569; font-weight: bold; font-size: 10.5pt;")
                self.step_seps.append(sep)
                h_layout.addWidget(sep)

        # Overflow menu button ("⋯") visible in compact/narrow modes so nothing ever hides silently
        self.btn_header_more = QPushButton("⋯")
        self.btn_header_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_header_more.setToolTip("ជំហាន & ឧបករណ៍បន្ថែម" if self.lang == "km" else "More workflow actions")
        self.btn_header_more.clicked.connect(self._open_header_overflow_menu)
        self.btn_header_more.setVisible(False)
        h_layout.addWidget(self.btn_header_more)

        h_layout.addStretch(1)

        # Batch Folder Mode button
        self.btn_header_batch = QPushButton("📦 Batch" if self.lang == "km" else "📦 Batch")
        self.btn_header_batch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_header_batch.setToolTip(
            "ដំណើរការវីដេអូទាំង Folder ដោយស្វ័យប្រវត្តិ (Ctrl+Shift+B)" if self.lang == "km" else "Batch process full video folder (Ctrl+Shift+B)"
        )
        self.btn_header_batch.setStyleSheet("""
            QPushButton {
                background-color: #162036;
                color: #93C5FD;
                border: 1px solid #243350;
                border-radius: 8px;
                padding: 4px 10px;
                font-weight: 600;
                font-size: 9pt;
            }
            QPushButton:hover { background-color: #1E2D4D; border-color: #3B82F6; color: #FFFFFF; }
        """)
        self.btn_header_batch.clicked.connect(self._open_batch_dialog)
        h_layout.addWidget(self.btn_header_batch)

        # Notification Bell (anchored popover trigger)
        self.btn_notifications = QPushButton("🔔")
        self.btn_notifications.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_notifications.setToolTip("មជ្ឈមណ្ឌលដំណឹង" if self.lang == "km" else "Notifications")
        self.btn_notifications.clicked.connect(self._open_notification_center)
        h_layout.addWidget(self.btn_notifications)

        # Settings gear button
        self.btn_header_settings = QPushButton("⚙")
        self.btn_header_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_header_settings.setToolTip("ការកំណត់កម្មវិធី" if self.lang == "km" else "Settings")
        self.btn_header_settings.clicked.connect(self._open_settings)
        h_layout.addWidget(self.btn_header_settings)

        # Clean language selector dropdown
        self.cb_ui_lang = QComboBox()
        self.cb_ui_lang.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_ui_lang.addItem("ខ្មែរ", "km")
        self.cb_ui_lang.addItem("English", "en")
        lang_idx = self.cb_ui_lang.findData(self.lang)
        if lang_idx >= 0:
            self.cb_ui_lang.setCurrentIndex(lang_idx)
        self.cb_ui_lang.setToolTip("ភាសាកម្មវិធី / App Language")
        self.cb_ui_lang.currentIndexChanged.connect(self._on_ui_lang_combo_changed)
        h_layout.addWidget(self.cb_ui_lang)

        self._update_stepper_states()
        return header

    def _open_header_overflow_menu(self):
        menu = QMenu(self)
        for idx, (km_lbl, en_lbl, icon_ch, km_tip, en_tip) in enumerate(self._step_meta):
            lbl = km_lbl if self.lang == "km" else en_lbl
            menu.addAction(f"{icon_ch} {lbl}", lambda i=idx: self._on_stepper_clicked(i))
        menu.addSeparator()
        menu.addAction("📤 " + ("Export SRT ចិន" if self.lang == "km" else "Export Chinese SRT"), self._export_zh_srt_chatgpt)
        menu.addAction("📥 " + ("នាំចូល SRT" if self.lang == "km" else "Import SRT"), self._import_srt_external)
        menu.addSeparator()
        menu.addAction("📊 " + ("របាយការណ៍" if self.lang == "km" else "Sync Report"), self._show_sync_report)
        menu.addAction("📦 Batch", self._open_batch_dialog)
        menu.addAction("⚙ " + ("ការកំណត់" if self.lang == "km" else "Settings"), self._open_settings)
        menu.exec(self.btn_header_more.mapToGlobal(self.btn_header_more.rect().bottomLeft()))

    def _update_responsive_header(self):
        """Switch header pills to compact icon mode <1280px and move extra pills to '⋯' on very narrow screens."""
        if not hasattr(self, "step_buttons") or not self.step_buttons:
            return
        w = self.width()
        compact = w < 1280
        very_narrow = w < 900

        for idx, btn in enumerate(self.step_buttons):
            km_lbl, en_lbl, icon_ch, km_tip, en_tip = self._step_meta[idx]
            full_lbl = km_lbl if self.lang == "km" else en_lbl
            btn.setToolTip(f"{full_lbl} — {km_tip if self.lang == 'km' else en_tip}")
            if compact:
                num = ["①", "②", "③", "④", "⑤", "⑥"][idx]
                btn.setText(f"{num} {icon_ch}")
            else:
                btn.setText(full_lbl)
            btn.setVisible(not (very_narrow and idx >= 4))

        for idx, sep in enumerate(getattr(self, "step_seps", [])):
            sep.setVisible(not (very_narrow and idx >= 3))

        if hasattr(self, "btn_header_more"):
            self.btn_header_more.setVisible(compact)

        if hasattr(self, "btn_export_zh_srt"):
            self.btn_export_zh_srt.setText(
                ("📤 SRT" if compact else ("📤 Export SRT ចិន" if self.lang == "km" else "📤 Export ZH SRT"))
            )
        if hasattr(self, "btn_import_srt"):
            self.btn_import_srt.setText(
                ("📥 SRT" if compact else ("📥 នាំចូល SRT" if self.lang == "km" else "📥 Import SRT"))
            )
        self._reflow_engine_bar()

    def _on_stepper_clicked(self, idx: int):
        actions = [
            self._import_video,
            self._run_transcription,
            self._run_translation,
            self._run_tts_generation,
            self._run_preview_result,
            self._export_dubbed,
        ]
        if 0 <= idx < len(actions):
            actions[idx]()

    def _update_stepper_states(self):
        """Update visual step state: done / active / locked based on project progress."""
        if not hasattr(self, "step_buttons") or len(self.step_buttons) < 6:
            return
        has_video = bool(self.project.video_path and os.path.exists(self.project.video_path))
        segs = self.project.segments or []
        has_src = any(bool(s.text_src and s.text_src.strip()) for s in segs)
        has_trans = any(bool(s.text_km and s.text_km.strip()) for s in segs)
        has_tts = any(bool(s.tts_file and os.path.exists(s.tts_file)) for s in segs)
        has_preview = bool(getattr(self.project.settings, "result_video_path", "") and os.path.exists(self.project.settings.result_video_path))

        done_flags = [has_video, has_src, has_trans, has_tts, has_preview, has_preview]
        # First incomplete step becomes active
        active_idx = 5
        for i in range(6):
            if not done_flags[i]:
                active_idx = i
                break

        styles = {
            "done": "QPushButton#stepPill { background-color: #132A23; color: #34D399; border: 1px solid #22C55E; border-radius: 14px; padding: 4px 10px; font-weight: 600; font-size: 8.5pt; }",
            "active": "QPushButton#stepPill { background-color: #2D8CFF; color: #FFFFFF; border: 1px solid #60A5FA; border-radius: 14px; padding: 4px 11px; font-weight: bold; font-size: 8.5pt; }",
            "locked": "QPushButton#stepPill { background-color: #162036; color: #9AA8C4; border: 1px solid #243350; border-radius: 14px; padding: 4px 10px; font-weight: 500; font-size: 8.5pt; } QPushButton#stepPill:hover { border-color: #2D8CFF; color: #E6ECF8; }",
        }
        for i, btn in enumerate(self.step_buttons):
            if i == active_idx:
                state = "active"
            elif done_flags[i]:
                state = "done"
            else:
                state = "locked"
            btn.setProperty("stepState", state)
            btn.setStyleSheet(styles[state])

    def _refresh_header_labels(self):
        self._update_responsive_header()
        if hasattr(self, "lbl_src_mode"):
            self.lbl_src_mode.setText("ប្រភព:" if self.lang == "km" else "Source:")
        if hasattr(self, "lbl_target_lang"):
            self.lbl_target_lang.setText("ភាសា:" if self.lang == "km" else "Target:")
        if hasattr(self, "lbl_trans_prov"):
            self.lbl_trans_prov.setText("បកប្រែ:" if self.lang == "km" else "Engine:")
        if hasattr(self, "btn_cancel_task"):
            self.btn_cancel_task.setText("✖ បោះបង់" if self.lang == "km" else "✖ Cancel")
        self._populate_trans_providers()

    def _populate_engine_bar(self, bar):
        """Populate engine selectors & SRT tools cleanly with responsive reflow so combos never clip."""
        from PySide6.QtWidgets import QGridLayout
        outer = bar if isinstance(bar, QVBoxLayout) else bar.layout()
        if outer is None:
            outer = QVBoxLayout(bar)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(6)

        self._engine_grid = QGridLayout()
        self._engine_grid.setContentsMargins(0, 0, 0, 0)
        self._engine_grid.setSpacing(6)

        # Group A: Source mode + OCR opts
        self._eng_grp_src = QWidget()
        l_src = QHBoxLayout(self._eng_grp_src)
        l_src.setContentsMargins(0, 0, 0, 0)
        l_src.setSpacing(6)
        self.lbl_src_mode = QLabel("ប្រភព:" if self.lang == "km" else "Source:")
        self.lbl_src_mode.setStyleSheet("color: #9AA8C4; font-size: 9pt; font-weight: 600;")
        l_src.addWidget(self.lbl_src_mode)

        self.cb_src_mode = QComboBox()
        self.cb_src_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_src_mode.setToolTip("ប្រភពអត្ថបទចិន៖ Auto / OCR / Whisper")
        self.cb_src_mode.addItem("🤖 Auto (OCR/Whisper)", "auto")
        self.cb_src_mode.addItem("👁️ Video OCR", "ocr")
        self.cb_src_mode.addItem("🎙️ Whisper", "whisper")
        self.cb_src_mode.addItem("💬 ChatGPT / OpenAI", "openai")
        cur_src_mode = getattr(self.project.settings, "src_mode", "auto") or "auto"
        sm_idx = self.cb_src_mode.findData(cur_src_mode)
        if sm_idx >= 0:
            self.cb_src_mode.setCurrentIndex(sm_idx)
        self.cb_src_mode.currentIndexChanged.connect(
            lambda idx: setattr(self.project.settings, "src_mode", self.cb_src_mode.itemData(idx))
        )
        l_src.addWidget(self.cb_src_mode)

        self.btn_ocr_opts = QPushButton("⚙ OCR")
        self.btn_ocr_opts.setToolTip("កំណត់ Engine OCR (RapidOCR/PaddleOCR), FPS និងតំបន់ ROI")
        self.btn_ocr_opts.clicked.connect(self._open_ocr_options_menu)
        l_src.addWidget(self.btn_ocr_opts)

        # Group B: Target language
        self._eng_grp_lang = QWidget()
        l_lang = QHBoxLayout(self._eng_grp_lang)
        l_lang.setContentsMargins(0, 0, 0, 0)
        l_lang.setSpacing(6)
        self.lbl_target_lang = QLabel("ភាសា:" if self.lang == "km" else "Target:")
        self.lbl_target_lang.setStyleSheet("color: #9AA8C4; font-size: 9pt; font-weight: 600;")
        l_lang.addWidget(self.lbl_target_lang)

        self.cb_target_lang = QComboBox()
        self.cb_target_lang.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_target_lang.setToolTip("ជ្រើសរើសភាសាបកប្រែ និងសំឡេង AI")
        for t_code, t_label, _, _, _ in TARGET_LANGUAGES:
            self.cb_target_lang.addItem(t_label, t_code)
        cur_tlang = getattr(self.project.settings, "target_language", "km") or "km"
        t_idx = self.cb_target_lang.findData(cur_tlang)
        if t_idx >= 0:
            self.cb_target_lang.setCurrentIndex(t_idx)
        self.cb_target_lang.currentIndexChanged.connect(self._on_target_lang_changed)
        l_lang.addWidget(self.cb_target_lang)

        # Group C: Translation provider
        self._eng_grp_trans = QWidget()
        l_tr = QHBoxLayout(self._eng_grp_trans)
        l_tr.setContentsMargins(0, 0, 0, 0)
        l_tr.setSpacing(6)
        self.lbl_trans_prov = QLabel("បកប្រែ:" if self.lang == "km" else "Engine:")
        self.lbl_trans_prov.setStyleSheet("color: #9AA8C4; font-size: 9pt; font-weight: 600;")
        l_tr.addWidget(self.lbl_trans_prov)

        self.cb_trans_provider = QComboBox()
        self.cb_trans_provider.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_trans_provider.setToolTip("ជ្រើសរើស Engine បកប្រែ៖ Google ឬ Gemini 1.5 Flash")
        self._populate_trans_providers()
        self.cb_trans_provider.currentIndexChanged.connect(self._on_trans_provider_changed)
        l_tr.addWidget(self.cb_trans_provider)

        # Group D: SRT buttons
        self._eng_grp_srt = QWidget()
        l_srt = QHBoxLayout(self._eng_grp_srt)
        l_srt.setContentsMargins(0, 0, 0, 0)
        l_srt.setSpacing(6)
        l_srt.addStretch(1)
        self.btn_export_zh_srt = QPushButton("📤 Export SRT ចិន" if self.lang == "km" else "📤 Export ZH SRT")
        self.btn_export_zh_srt.setToolTip("Export SRT ចិន សម្រាប់បកប្រែក្នុង ChatGPT/ឧបករណ៍ផ្សេង")
        self.btn_export_zh_srt.clicked.connect(self._export_zh_srt_chatgpt)
        l_srt.addWidget(self.btn_export_zh_srt)

        self.btn_import_srt = QPushButton("📥 នាំចូល SRT" if self.lang == "km" else "📥 Import SRT")
        self.btn_import_srt.setToolTip("នាំចូល SRT ដែលបានបកប្រែរួចមកជំនួសក្នុងតារាង")
        self.btn_import_srt.clicked.connect(self._import_srt_external)
        l_srt.addWidget(self.btn_import_srt)

        outer.addLayout(self._engine_grid)
        self._reflow_engine_bar()

    def _reflow_engine_bar(self):
        """Place engine groups in 2 rows when wide, or reflow neatly onto 3-4 rows when narrow so zero text truncates."""
        if not hasattr(self, "_engine_grid"):
            return
        while self._engine_grid.count():
            self._engine_grid.takeAt(0)

        avail_w = self.segment_table.width() if hasattr(self, "segment_table") and self.segment_table.width() > 100 else max(640, int(self.width() * 0.62))
        w_a = self._eng_grp_src.sizeHint().width()
        w_b = self._eng_grp_lang.sizeHint().width()
        w_c = self._eng_grp_trans.sizeHint().width()
        w_d = self._eng_grp_srt.sizeHint().width()

        if w_a + w_b + 12 <= avail_w and w_c + w_d + 12 <= avail_w:
            # 2-row balanced desktop layout
            self._engine_grid.addWidget(self._eng_grp_src, 0, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_lang, 0, 1, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_trans, 1, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_srt, 1, 1, Qt.AlignmentFlag.AlignRight)
            self._engine_grid.setColumnStretch(0, 0)
            self._engine_grid.setColumnStretch(1, 1)
        elif w_c + w_d + 12 <= avail_w:
            # 3-row medium width
            self._engine_grid.addWidget(self._eng_grp_src, 0, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_lang, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_trans, 2, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_srt, 2, 1, Qt.AlignmentFlag.AlignRight)
            self._engine_grid.setColumnStretch(0, 0)
            self._engine_grid.setColumnStretch(1, 1)
        else:
            # Stacked compact rows
            self._engine_grid.addWidget(self._eng_grp_src, 0, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_lang, 1, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_trans, 2, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.addWidget(self._eng_grp_srt, 3, 0, Qt.AlignmentFlag.AlignLeft)
            self._engine_grid.setColumnStretch(0, 1)
            self._engine_grid.setColumnStretch(1, 0)

    def _create_mixing_panel(self) -> QWidget:
        s = STRINGS[self.lang]
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        orig_val_pct = int(self.project.settings.original_voice_volume * 100)
        orig_on = (not self.project.settings.mute_original) and orig_val_pct > 0
        bg_val_pct = int(self.project.settings.background_volume * 100)
        bg_on = (not self.project.settings.mute_original or self.project.settings.keep_background) and bg_val_pct > 0

        # Row 1: [switch] Original Voice + slider
        row_orig = QHBoxLayout()
        row_orig.setSpacing(6)
        self.sw_orig_voice = ToggleSwitch("សំឡេងដើម" if self.lang == "km" else "Original Voice")
        self.sw_orig_voice.setMinimumWidth(110)
        self.sw_orig_voice.setChecked(orig_on)
        self.sw_orig_voice.toggled.connect(self._on_orig_voice_switch_toggled)
        row_orig.addWidget(self.sw_orig_voice)

        self.slider_orig_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_orig_vol.setRange(0, 150)
        self.slider_orig_vol.setValue(orig_val_pct if orig_on else 0)
        self.slider_orig_vol.setEnabled(orig_on)
        self.slider_orig_vol.valueChanged.connect(self._on_orig_vol_changed)
        row_orig.addWidget(self.slider_orig_vol, 1)

        self.lbl_orig_val = QLabel(f"{orig_val_pct}%" if orig_on else "OFF")
        self.lbl_orig_val.setMinimumWidth(38)
        self.lbl_orig_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_orig_val.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 8.5pt;")
        row_orig.addWidget(self.lbl_orig_val)
        layout.addLayout(row_orig)

        # Row 2: [switch] Music / SFX + slider
        row_bg = QHBoxLayout()
        row_bg.setSpacing(6)
        self.sw_bg_music = ToggleSwitch("ភ្លេង / SFX" if self.lang == "km" else "Music / SFX")
        self.sw_bg_music.setMinimumWidth(110)
        self.sw_bg_music.setChecked(bg_on)
        self.sw_bg_music.toggled.connect(self._on_bg_music_switch_toggled)
        row_bg.addWidget(self.sw_bg_music)

        self.slider_bg_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_bg_vol.setRange(0, 150)
        self.slider_bg_vol.setValue(bg_val_pct if bg_on else 0)
        self.slider_bg_vol.setEnabled(bg_on)
        self.slider_bg_vol.valueChanged.connect(self._on_bg_vol_changed)
        row_bg.addWidget(self.slider_bg_vol, 1)

        self.lbl_bg_val = QLabel(f"{bg_val_pct}%" if bg_on else "OFF")
        self.lbl_bg_val.setMinimumWidth(38)
        self.lbl_bg_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_bg_val.setStyleSheet("color: #F59E0B; font-weight: bold; font-size: 8.5pt;")
        row_bg.addWidget(self.lbl_bg_val)
        layout.addLayout(row_bg)

        # Row 3: AI Voice slider
        row_dub = QHBoxLayout()
        row_dub.setSpacing(6)
        self.lbl_dub_title = QLabel("សំឡេង AI" if self.lang == "km" else "AI Voice")
        self.lbl_dub_title.setMinimumWidth(110)
        self.lbl_dub_title.setStyleSheet("color: #E6ECF8; font-weight: 600; font-size: 9pt;")
        row_dub.addWidget(self.lbl_dub_title)

        self.slider_dub_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_dub_vol.setRange(0, 200)
        self.slider_dub_vol.setValue(int(self.project.settings.dubbed_volume * 100))
        self.slider_dub_vol.valueChanged.connect(self._on_dub_vol_changed)
        row_dub.addWidget(self.slider_dub_vol, 1)

        self.lbl_dub_val = QLabel(f"{int(self.project.settings.dubbed_volume * 100)}%")
        self.lbl_dub_val.setMinimumWidth(38)
        self.lbl_dub_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_dub_val.setStyleSheet("color: #2D8CFF; font-weight: bold; font-size: 8.5pt;")
        row_dub.addWidget(self.lbl_dub_val)
        layout.addLayout(row_dub)

        # Concise status badge row
        row_status = QHBoxLayout()
        self.lbl_mute_summary = QLabel()
        row_status.addWidget(self.lbl_mute_summary)
        row_status.addStretch(1)
        layout.addLayout(row_status)

        # Speed SegmentedControl + Auto-fit
        row_spd = QHBoxLayout()
        row_spd.setSpacing(6)
        self.lbl_spd_title = QLabel("ល្បឿន:" if self.lang == "km" else "Speed:")
        self.lbl_spd_title.setStyleSheet("color: #9AA8C4; font-size: 8.5pt; font-weight: 600;")
        row_spd.addWidget(self.lbl_spd_title)

        init_spd = getattr(self.project.settings, "ai_voice_speed", 1.05)
        self.seg_ai_speed = SegmentedControl([
            ("0.9x", 0.90),
            ("1.0x", 1.00),
            ("1.05x", 1.05),
            ("1.15x", 1.15),
            ("1.25x", 1.25),
        ])
        nearest = min([0.90, 1.00, 1.05, 1.15, 1.25], key=lambda v: abs(v - init_spd))
        self.seg_ai_speed.set_value(nearest, emit=False)
        self.seg_ai_speed.selection_changed.connect(self._on_speed_segment_changed)
        row_spd.addWidget(self.seg_ai_speed, 1)

        self.btn_apply_spd_all = QPushButton("⚡ Auto-fit" if self.lang == "km" else "⚡ Auto-fit")
        self.btn_apply_spd_all.setToolTip("អនុវត្តល្បឿននេះទៅគ្រប់វគ្គទាំងអស់" if self.lang == "km" else "Apply selected speed to all segments")
        self.btn_apply_spd_all.clicked.connect(self._apply_global_speed_to_all)
        row_spd.addWidget(self.btn_apply_spd_all)
        layout.addLayout(row_spd)

        # Output Switches + កំណត់...
        row_out = QHBoxLayout()
        row_out.setSpacing(8)
        self.chk_burn_sub = ToggleSwitch("ដាក់អក្សរខ្មែរ" if self.lang == "km" else "Burn Subtitles")
        self.chk_burn_sub.setChecked(self.project.settings.burn_subtitles)
        self.chk_burn_sub.toggled.connect(self._on_burn_sub_toggled)
        row_out.addWidget(self.chk_burn_sub)

        self.chk_mask_sub = ToggleSwitch("បិទបាំងអក្សរចិន" if self.lang == "km" else "Mask Subtitles")
        self.chk_mask_sub.setChecked(getattr(self.project.settings, "mask_subtitles", True))
        self.chk_mask_sub.toggled.connect(self._on_mask_sub_toggled)
        row_out.addWidget(self.chk_mask_sub)

        row_out.addStretch(1)
        self.btn_mask_settings = QPushButton("⚙ កំណត់…" if self.lang == "km" else "⚙ Position…")
        self.btn_mask_settings.setToolTip("បើកផ្ទាំងកំណត់ទីតាំង និងរបាំងបិទបាំងអក្សរចិន" if self.lang == "km" else "Configure Subtitle Mask & Position")
        self.btn_mask_settings.clicked.connect(self._open_subtitle_mask_dialog)
        row_out.addWidget(self.btn_mask_settings)
        layout.addLayout(row_out)

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
        self.chk_logo.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 9pt;")
        self.chk_logo.setChecked(self.project.settings.logo_enabled)
        self.chk_logo.toggled.connect(self._on_logo_enabled_toggled)
        layout.addWidget(self.chk_logo)

        # 2. Choose Logo File Row
        row_file = QHBoxLayout()
        row_file.setSpacing(6)
        self.btn_browse_logo = QPushButton(s.get("logo_browse", "🖼️ Choose Logo..."))
        self.btn_browse_logo.setStyleSheet("background-color: #334155; padding: 3px 8px; border-radius: 4px; font-size: 8.5pt;")
        self.btn_browse_logo.clicked.connect(self._on_browse_logo)
        row_file.addWidget(self.btn_browse_logo)

        self.btn_default_logo = QPushButton("✨ Studio Logo")
        self.btn_default_logo.setToolTip("ប្រើប្រាស់ Logo ផ្លូវការរបស់កម្មវិធី (Use built-in AI Dub Studio watermark logo)")
        self.btn_default_logo.setStyleSheet("background-color: #0369a1; color: white; padding: 3px 8px; border-radius: 4px; font-size: 8.5pt; font-weight: bold;")
        self.btn_default_logo.clicked.connect(self._on_use_studio_logo)
        row_file.addWidget(self.btn_default_logo)

        logo_name = Path(self.project.settings.logo_path).name if self.project.settings.logo_path else "គ្មាន (None)"
        self.lbl_logo_path = QLabel(logo_name)
        self.lbl_logo_path.setStyleSheet("background: #1e293b; color: #94a3b8; font-size: 8.5pt; padding: 2px 6px; border-radius: 3px;")
        row_file.addWidget(self.lbl_logo_path, stretch=1)

        self.btn_clear_logo = QPushButton("❌")
        self.btn_clear_logo.clicked.connect(self._on_clear_logo)
        row_file.addWidget(self.btn_clear_logo)
        layout.addLayout(row_file)

        # 3. Logo Position Row (4 corners + center + dragged custom)
        row_pos = QHBoxLayout()
        row_pos.setSpacing(6)
        self.lbl_logo_pos = QLabel(s.get("logo_position", "Position") + ":")
        self.lbl_logo_pos.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 9pt;")
        row_pos.addWidget(self.lbl_logo_pos)

        self.combo_logo_pos = QComboBox()
        self._populate_logo_positions()
        self.combo_logo_pos.currentIndexChanged.connect(self._on_logo_pos_changed)
        row_pos.addWidget(self.combo_logo_pos, stretch=1)
        layout.addLayout(row_pos)

        # 4. Logo Size Slider (% of video width: 5% - 40%)
        vbox_size = QVBoxLayout()
        vbox_size.setSpacing(2)
        row_size_hdr = QHBoxLayout()
        self.lbl_size_title = QLabel("ទំហំ Logo (% ទទឹងវីដេអូ)" if self.lang == "km" else "Logo Size (% video width)")
        self.lbl_size_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 9pt;")
        init_wpct = int(round(getattr(self.project.settings, "logo_width_pct", 12.0)))
        self.lbl_logo_size = QLabel(f"{init_wpct}%")
        self.lbl_logo_size.setStyleSheet("background: #1e293b; color: #38bdf8; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 8.5pt;")
        row_size_hdr.addWidget(self.lbl_size_title)
        row_size_hdr.addStretch()
        row_size_hdr.addWidget(self.lbl_logo_size)
        vbox_size.addLayout(row_size_hdr)

        self.slider_logo_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_size.setRange(5, 40)
        self.slider_logo_size.setValue(max(5, min(40, init_wpct)))
        self.slider_logo_size.valueChanged.connect(self._on_logo_size_changed)
        vbox_size.addWidget(self.slider_logo_size)
        layout.addLayout(vbox_size)

        # 5. Logo Margin Slider (0.0% - 10.0%)
        vbox_margin = QVBoxLayout()
        vbox_margin.setSpacing(2)
        row_margin_hdr = QHBoxLayout()
        self.lbl_margin_title = QLabel("គម្លាតពីគែម (Margin)" if self.lang == "km" else "Edge Margin")
        self.lbl_margin_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 9pt;")
        init_mpct = float(getattr(self.project.settings, "logo_margin_pct", 2.5))
        self.lbl_logo_margin = QLabel(f"{init_mpct:.1f}%")
        self.lbl_logo_margin.setStyleSheet("background: #1e293b; color: #34d399; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 8.5pt;")
        row_margin_hdr.addWidget(self.lbl_margin_title)
        row_margin_hdr.addStretch()
        row_margin_hdr.addWidget(self.lbl_logo_margin)
        vbox_margin.addLayout(row_margin_hdr)

        self.slider_logo_margin = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_margin.setRange(0, 100)  # 0.0% .. 10.0% in 0.1% steps
        self.slider_logo_margin.setValue(max(0, min(100, int(round(init_mpct * 10)))))
        self.slider_logo_margin.valueChanged.connect(self._on_logo_margin_changed)
        vbox_margin.addWidget(self.slider_logo_margin)
        layout.addLayout(vbox_margin)

        # 6. Logo Opacity Slider
        vbox_op = QVBoxLayout()
        vbox_op.setSpacing(2)
        row_op_hdr = QHBoxLayout()
        self.lbl_op_title = QLabel(s.get("logo_opacity", "Opacity"))
        self.lbl_op_title.setStyleSheet("font-weight: 600; color: #cbd5e1; font-size: 9pt;")
        self.lbl_logo_opacity = QLabel(f"{int(self.project.settings.logo_opacity * 100)}%")
        self.lbl_logo_opacity.setStyleSheet("background: #1e293b; color: #c084fc; font-weight: bold; padding: 1px 6px; border-radius: 3px; font-size: 8.5pt;")
        row_op_hdr.addWidget(self.lbl_op_title)
        row_op_hdr.addStretch()
        row_op_hdr.addWidget(self.lbl_logo_opacity)
        vbox_op.addLayout(row_op_hdr)

        self.slider_logo_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_opacity.setRange(10, 100)
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
        self.combo_logo_pos.addItem("✋ ទីតាំងផ្ទាល់ខ្លួន (Dragged)" if self.lang == "km" else "✋ Custom (Dragged)", "custom")
        idx = self.combo_logo_pos.findData(self.project.settings.logo_position)
        if idx >= 0:
            self.combo_logo_pos.setCurrentIndex(idx)
        self.combo_logo_pos.blockSignals(False)

    def _on_tool_tab_changed(self, tab_idx: int):
        # Automatically switch to Live preview when user opens Logo tab
        if tab_idx == 1 and hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self._sync_overlays_to_player()

    def _on_burn_sub_toggled(self, val: bool):
        self.project.settings.burn_subtitles = val
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
            self._sync_overlays_to_player()
        self._on_data_changed()

    def _on_mask_sub_toggled(self, val: bool):
        self.project.settings.mask_subtitles = val
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
            self._sync_overlays_to_player()
        self._on_data_changed()

    def _on_logo_enabled_toggled(self, checked: bool):
        self.project.settings.logo_enabled = checked
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
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
            self.chk_logo.blockSignals(True)
            self.chk_logo.setChecked(True)
            self.chk_logo.blockSignals(False)
            self.lbl_logo_path.setText(Path(path).name)
            if hasattr(self, "video_player"):
                self.video_player.ensure_live_mode()
                self.video_player.mark_result_stale(True)
            self._sync_logo_to_player()
            self._on_data_changed()

    def _on_use_studio_logo(self):
        for rel in ("assets/watermark_logo.png", "assets/logo.png"):
            candidate = get_resource_path(rel)
            if candidate.exists():
                self.project.settings.logo_path = str(candidate)
                self.project.settings.logo_enabled = True
                self.chk_logo.blockSignals(True)
                self.chk_logo.setChecked(True)
                self.chk_logo.blockSignals(False)
                self.lbl_logo_path.setText(candidate.name)
                if hasattr(self, "video_player"):
                    self.video_player.ensure_live_mode()
                    self.video_player.mark_result_stale(True)
                self._sync_logo_to_player()
                self._on_data_changed()
                break

    def _on_clear_logo(self):
        self.project.settings.logo_path = ""
        self.project.settings.logo_enabled = False
        self.chk_logo.blockSignals(True)
        self.chk_logo.setChecked(False)
        self.chk_logo.blockSignals(False)
        self.lbl_logo_path.setText("គ្មាន (None)")
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
        self._sync_logo_to_player()
        self._on_data_changed()

    def _on_logo_pos_changed(self):
        pos = self.combo_logo_pos.currentData()
        if pos:
            self.project.settings.logo_position = pos
            if pos != "custom":
                self.project.settings.logo_custom_x_pct = -1.0
                self.project.settings.logo_custom_y_pct = -1.0
            if hasattr(self, "video_player"):
                self.video_player.ensure_live_mode()
                self.video_player.mark_result_stale(True)
            self._sync_logo_to_player()
            self._on_data_changed()

    def _on_logo_dragged_in_preview(self, pos_name: str, custom_x_pct: float, custom_y_pct: float):
        self.project.settings.logo_position = pos_name
        self.project.settings.logo_custom_x_pct = custom_x_pct
        self.project.settings.logo_custom_y_pct = custom_y_pct
        if hasattr(self, "combo_logo_pos"):
            self.combo_logo_pos.blockSignals(True)
            idx = self.combo_logo_pos.findData(pos_name)
            if idx >= 0:
                self.combo_logo_pos.setCurrentIndex(idx)
            self.combo_logo_pos.blockSignals(False)
        if hasattr(self, "video_player"):
            self.video_player.mark_result_stale(True)
        self._on_data_changed()

    def _on_logo_size_changed(self, val: int):
        self.project.settings.logo_width_pct = float(val)
        if hasattr(self, "lbl_logo_size"):
            self.lbl_logo_size.setText(f"{val}%")
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
        self._sync_logo_to_player()
        self._on_data_changed()

    def _on_logo_margin_changed(self, val: int):
        mpct = round(val / 10.0, 1)
        self.project.settings.logo_margin_pct = mpct
        if hasattr(self, "lbl_logo_margin"):
            self.lbl_logo_margin.setText(f"{mpct:.1f}%")
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
        self._sync_logo_to_player()
        self._on_data_changed()

    def _on_logo_opacity_changed(self, val: int):
        op = val / 100.0
        self.project.settings.logo_opacity = op
        if hasattr(self, "lbl_logo_opacity"):
            self.lbl_logo_opacity.setText(f"{val}%")
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
            self.video_player.mark_result_stale(True)
        self._sync_logo_to_player()
        self._on_data_changed()

    def _sync_logo_widgets_from_settings(self):
        st = self.project.settings
        if hasattr(self, "chk_logo"):
            self.chk_logo.blockSignals(True)
            self.chk_logo.setChecked(st.logo_enabled)
            self.chk_logo.blockSignals(False)
        if hasattr(self, "lbl_logo_path"):
            self.lbl_logo_path.setText(Path(st.logo_path).name if st.logo_path else "គ្មាន (None)")
        if hasattr(self, "combo_logo_pos"):
            self._populate_logo_positions()
        if hasattr(self, "slider_logo_size"):
            wpct = int(round(getattr(st, "logo_width_pct", 12.0)))
            self.slider_logo_size.blockSignals(True)
            self.slider_logo_size.setValue(max(5, min(40, wpct)))
            self.slider_logo_size.blockSignals(False)
            self.lbl_logo_size.setText(f"{wpct}%")
        if hasattr(self, "slider_logo_margin"):
            mpct = float(getattr(st, "logo_margin_pct", 2.5))
            self.slider_logo_margin.blockSignals(True)
            self.slider_logo_margin.setValue(max(0, min(100, int(round(mpct * 10)))))
            self.slider_logo_margin.blockSignals(False)
            self.lbl_logo_margin.setText(f"{mpct:.1f}%")
        if hasattr(self, "slider_logo_opacity"):
            opct = int(round(st.logo_opacity * 100))
            self.slider_logo_opacity.blockSignals(True)
            self.slider_logo_opacity.setValue(max(10, min(100, opct)))
            self.slider_logo_opacity.blockSignals(False)
            self.lbl_logo_opacity.setText(f"{opct}%")

    def _sync_logo_to_player(self):
        st = self.project.settings
        self.video_player.set_logo(
            enabled=st.logo_enabled,
            path=st.logo_path,
            position=st.logo_position,
            width_pct=getattr(st, "logo_width_pct", 12.0),
            opacity=st.logo_opacity,
            margin_pct=getattr(st, "logo_margin_pct", 2.5),
            custom_x_pct=getattr(st, "logo_custom_x_pct", -1.0),
            custom_y_pct=getattr(st, "logo_custom_y_pct", -1.0),
        )

    def _sync_overlays_to_player(self):
        st = self.project.settings
        if hasattr(self.video_player, "set_mask_config"):
            self.video_player.set_mask_config(
                enabled=getattr(st, "mask_subtitles", True),
                x_pct=getattr(st, "mask_x_percent", 0.0),
                y_pct=getattr(st, "mask_y_percent", 83.0),
                w_pct=getattr(st, "mask_width_percent", 100.0),
                h_pct=getattr(st, "mask_height_percent", 12.0),
                opacity=getattr(st, "mask_opacity", 0.95),
                color=getattr(st, "mask_color", "black"),
                style=getattr(st, "mask_style", "solid"),
            )
        if hasattr(self.video_player, "set_subtitle_config"):
            self.video_player.set_subtitle_config(
                enabled=getattr(st, "burn_subtitles", True),
                font_size=getattr(st, "subtitle_font_size", 20),
                margin_v=getattr(st, "subtitle_margin_v", 25),
            )
        self._sync_logo_to_player()

    def _on_ui_lang_combo_changed(self):
        if not hasattr(self, "cb_ui_lang"):
            return
        new_lang = self.cb_ui_lang.currentData() or "km"
        if new_lang == self.lang:
            return
        self._apply_language(new_lang)

    def _toggle_language(self):
        new_lang = "en" if self.lang == "km" else "km"
        self._apply_language(new_lang)
        if hasattr(self, "cb_ui_lang"):
            self.cb_ui_lang.blockSignals(True)
            idx = self.cb_ui_lang.findData(new_lang)
            if idx >= 0:
                self.cb_ui_lang.setCurrentIndex(idx)
            self.cb_ui_lang.blockSignals(False)

    def _apply_language(self, new_lang: str):
        self.lang = new_lang
        self.project.settings.ui_language = self.lang
        self.setWindowTitle(f"{STRINGS[self.lang]['app_title']} v{APP_VERSION}")
        self._create_menu_bar()
        self._refresh_header_labels()
        self.segment_table.set_language(self.lang)
        if hasattr(self.video_player, "set_language"):
            self.video_player.set_language(self.lang)
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
            self.cb_trans_provider.addItem("💬 ChatGPT (OpenAI)", "openai")
        else:
            self.cb_trans_provider.addItem("🌐 Google (Classic)", "google")
            self.cb_trans_provider.addItem("✨ Gemini 1.5 Flash", "gemini")
            self.cb_trans_provider.addItem("💬 ChatGPT (OpenAI)", "openai")

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
        if prov == "openai":
            from app.secrets import get_openai_api_key, set_openai_api_key
            if not get_openai_api_key():
                from PySide6.QtWidgets import QInputDialog
                key, ok = QInputDialog.getText(
                    self,
                    "OpenAI / ChatGPT API Key",
                    "សូមបញ្ចូល OpenAI API Key (រក្សាទុកដោយសុវត្ថិភាពក្នុង OS Credential Store):" if self.lang == "km"
                    else "Enter OpenAI API Key (saved securely in OS Credential Store):",
                    QLineEdit.EchoMode.Password
                )
                if ok and key.strip():
                    set_openai_api_key(key.strip())
                    self.project.settings.translation_provider = "openai"
                    self.translation_engine.settings = self.project.settings
                    self.status_bar.showMessage("បានរក្សាទុក OpenAI API Key ដោយសុវត្ថិភាព និងបើកប្រើ ChatGPT!", 4500)
                else:
                    self.cb_trans_provider.blockSignals(True)
                    idx = self.cb_trans_provider.findData("google")
                    if idx >= 0:
                        self.cb_trans_provider.setCurrentIndex(idx)
                    self.cb_trans_provider.blockSignals(False)
                    self.project.settings.translation_provider = "google"
                    self.translation_engine.settings = self.project.settings
                    return
            else:
                self.project.settings.translation_provider = "openai"
                self.translation_engine.settings = self.project.settings
                model_name = getattr(self.project.settings, "openai_trans_model", "gpt-4o-mini")
                self.status_bar.showMessage(f"បានជ្រើសរើស Engine: ChatGPT / OpenAI ({model_name})", 4000)
        elif prov == "gemini":
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
        if hasattr(self, "sw_orig_voice"):
            self.sw_orig_voice.setText("សំឡេងដើម" if self.lang == "km" else "Original Voice")
        if hasattr(self, "sw_bg_music"):
            self.sw_bg_music.setText("ភ្លេង / SFX" if self.lang == "km" else "Music / SFX")
        if hasattr(self, "lbl_dub_title"):
            self.lbl_dub_title.setText("សំឡេង AI" if self.lang == "km" else "AI Voice")
        if hasattr(self, "lbl_spd_title"):
            self.lbl_spd_title.setText("ល្បឿន:" if self.lang == "km" else "Speed:")
        if hasattr(self, "chk_burn_sub"):
            self.chk_burn_sub.setText("ដាក់អក្សរខ្មែរ" if self.lang == "km" else "Burn Subtitles")
        if hasattr(self, "chk_mask_sub"):
            self.chk_mask_sub.setText("បិទបាំងអក្សរចិន" if self.lang == "km" else "Mask Subtitles")
        if hasattr(self, "btn_mask_settings"):
            self.btn_mask_settings.setText("⚙ កំណត់…" if self.lang == "km" else "⚙ Position…")
        self._update_mute_status_display()

        # Update tabs & logo panel labels
        if hasattr(self, "tool_tabs"):
            self.tool_tabs.setTabText(0, "🎛️ សំឡេង" if self.lang == "km" else "🎛️ Audio")
            self.tool_tabs.setTabText(1, "🎨 Logo")
        if hasattr(self, "chk_logo"):
            self.chk_logo.setText("បង្ហាញ Logo លើវីដេអូ" if self.lang == "km" else "Overlay Custom Logo")
        if hasattr(self, "btn_browse_logo"):
            self.btn_browse_logo.setText("🖼️ ជ្រើស Logo…" if self.lang == "km" else "🖼️ Choose Logo…")
        if hasattr(self, "lbl_logo_pos"):
            self.lbl_logo_pos.setText("ទីតាំង:" if self.lang == "km" else "Position:")
        self._populate_logo_positions()
        if hasattr(self, "lbl_size_title"):
            self.lbl_size_title.setText("ទំហំ Logo (% ទទឹងវីដេអូ)" if self.lang == "km" else "Logo Size (% video width)")
        if hasattr(self, "lbl_margin_title"):
            self.lbl_margin_title.setText("គម្លាតពីគែម (Margin)" if self.lang == "km" else "Edge Margin")
        if hasattr(self, "lbl_op_title"):
            self.lbl_op_title.setText("កម្រិតច្បាស់" if self.lang == "km" else "Opacity")
        if hasattr(self, "btn_main_preview"):
            self.btn_main_preview.setText(("👁️ មើលលទ្ធផលសាកល្បង (Ctrl+P)" if self.lang == "km" else "👁️ Preview Result (Ctrl+P)"))

    def _update_mute_status_display(self):
        """Display consolidated 1-phrase mute status and keep switches & sliders synced."""
        st = self.project.settings
        orig_on = st.original_voice_volume > 0.001 and not st.mute_original
        bg_on = st.background_volume > 0.001 and (st.keep_background or not st.mute_original)

        orig_str = f"{int(st.original_voice_volume * 100)}%" if orig_on else "OFF"
        bg_str = f"{int(st.background_volume * 100)}%" if bg_on else "OFF"

        status_text = f"Original: {orig_str} | BG: {bg_str}"
        if hasattr(self, "lbl_mute_summary"):
            self.lbl_mute_summary.setText(status_text)
            if not orig_on and not bg_on:
                self.lbl_mute_summary.setStyleSheet("background: #3B1219; color: #FCA5A5; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 7.5pt;")
            elif not orig_on and bg_on:
                self.lbl_mute_summary.setStyleSheet("background: #422006; color: #FDE047; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 7.5pt;")
            else:
                self.lbl_mute_summary.setStyleSheet("background: #0F2E22; color: #34D399; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 7.5pt;")

    def _on_orig_voice_switch_toggled(self, checked: bool):
        if checked:
            self.project.settings.mute_original = False
            self.slider_orig_vol.setEnabled(True)
            if self.slider_orig_vol.value() == 0:
                self.slider_orig_vol.blockSignals(True)
                self.slider_orig_vol.setValue(30)
                self.slider_orig_vol.blockSignals(False)
                self.project.settings.original_voice_volume = 0.3
                self.project.settings.original_volume = 0.3
            if hasattr(self, "lbl_orig_val"):
                self.lbl_orig_val.setText(f"{self.slider_orig_vol.value()}%")
        else:
            self.project.settings.mute_original = True
            self.project.settings.original_voice_volume = 0.0
            self.project.settings.original_volume = 0.0
            self.slider_orig_vol.blockSignals(True)
            self.slider_orig_vol.setValue(0)
            self.slider_orig_vol.blockSignals(False)
            self.slider_orig_vol.setEnabled(False)
            if hasattr(self, "lbl_orig_val"):
                self.lbl_orig_val.setText("OFF")
            # Preserve background switch intent
            if hasattr(self, "sw_bg_music"):
                self.project.settings.keep_background = self.sw_bg_music.isChecked()
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_bg_music_switch_toggled(self, checked: bool):
        self.project.settings.keep_background = checked
        if checked:
            self.slider_bg_vol.setEnabled(True)
            if self.slider_bg_vol.value() == 0:
                self.slider_bg_vol.blockSignals(True)
                self.slider_bg_vol.setValue(30)
                self.slider_bg_vol.blockSignals(False)
                self.project.settings.background_volume = 0.3
            if hasattr(self, "lbl_bg_val"):
                self.lbl_bg_val.setText(f"{self.slider_bg_vol.value()}%")
        else:
            self.project.settings.background_volume = 0.0
            self.slider_bg_vol.blockSignals(True)
            self.slider_bg_vol.setValue(0)
            self.slider_bg_vol.blockSignals(False)
            self.slider_bg_vol.setEnabled(False)
            if hasattr(self, "lbl_bg_val"):
                self.lbl_bg_val.setText("OFF")
            if hasattr(self, "sw_orig_voice") and not self.sw_orig_voice.isChecked():
                self.project.settings.mute_original = True
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_orig_vol_changed(self, val: int):
        vol = val / 100.0
        self.project.settings.original_voice_volume = vol
        self.project.settings.original_volume = vol
        if hasattr(self, "lbl_orig_val"):
            self.lbl_orig_val.setText(f"{val}%" if val > 0 else "OFF")
        if hasattr(self, "sw_orig_voice"):
            self.sw_orig_voice.blockSignals(True)
            self.sw_orig_voice.setChecked(val > 0)
            self.sw_orig_voice.blockSignals(False)
        self.project.settings.mute_original = (val == 0)
        if val == 0 and hasattr(self, "sw_bg_music"):
            self.project.settings.keep_background = self.sw_bg_music.isChecked()
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_bg_vol_changed(self, val: int):
        vol = val / 100.0
        self.project.settings.background_volume = vol
        if hasattr(self, "lbl_bg_val"):
            self.lbl_bg_val.setText(f"{val}%" if val > 0 else "OFF")
        if hasattr(self, "sw_bg_music"):
            self.sw_bg_music.blockSignals(True)
            self.sw_bg_music.setChecked(val > 0)
            self.sw_bg_music.blockSignals(False)
        self.project.settings.keep_background = (val > 0)
        self._update_mute_status_display()
        self._on_data_changed()

    def _on_dub_vol_changed(self, val: int):
        self.project.settings.dubbed_volume = val / 100.0
        if hasattr(self, "lbl_dub_val"):
            self.lbl_dub_val.setText(f"{val}%")
        self._on_data_changed()

    def _on_duck_changed(self, val: int):
        self.project.settings.ducking_db = float(val)
        self._on_data_changed()

    def _on_speed_segment_changed(self, spd: float):
        self.project.settings.ai_voice_speed = float(spd)
        self._on_data_changed()

    def _on_ai_speed_changed(self, val: int):
        spd = round(val / 100.0, 2)
        self.project.settings.ai_voice_speed = spd
        if hasattr(self, "seg_ai_speed"):
            nearest = min([0.90, 1.00, 1.05, 1.15, 1.25], key=lambda v: abs(v - spd))
            self.seg_ai_speed.set_value(nearest, emit=False)
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
        if hasattr(self.video_player, "clear_video"):
            self.video_player.clear_video()
        else:
            self.video_player.stop()
        self.segment_table.populate(self.project)
        self.lbl_stats.setText("0 ឃ្លា" if self.lang == "km" else "0 Segments")
        self.status_bar.showMessage("New project created.")
        self._update_stepper_states()

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
            self._sync_logo_widgets_from_settings()
            self._sync_overlays_to_player()
            self._update_stats_display()
            self._update_stepper_states()
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
        self._load_video_path(path)

    def _load_video_path(self, path: str):
        if not path or not os.path.exists(path):
            return
        self.project.video_path = path
        self.project.name = Path(path).stem
        self.video_player.load_video(path)
        self._sync_overlays_to_player()
        self._update_stepper_states()
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

    def _open_ocr_options_menu(self):
        from PySide6.QtWidgets import QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QCheckBox, QDialogButtonBox
        from app.ui.theme import fit_dialog_to_screen
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙️ ការកំណត់ OCR អក្សរក្នុងវិដេអូ (OCR Settings)")
        fit_dialog_to_screen(dlg, 420, 280)
        form = QFormLayout(dlg)

        cb_eng = QComboBox()
        cb_eng.addItem("RapidOCR (ONNX - Default / លឿន)", "rapidocr")
        cb_eng.addItem("PaddleOCR (ជម្រើស)", "paddleocr")
        cur_eng = getattr(self.project.settings, "ocr_engine", "rapidocr")
        idx = cb_eng.findData(cur_eng)
        if idx >= 0:
            cb_eng.setCurrentIndex(idx)
        form.addRow("OCR Engine:", cb_eng)

        spin_fps = QDoubleSpinBox()
        spin_fps.setRange(2.0, 10.0)
        spin_fps.setSingleStep(1.0)
        spin_fps.setValue(float(getattr(self.project.settings, "ocr_fps", 5.0)))
        form.addRow("ល្បឿនស្កេន (FPS, 4–8):", spin_fps)

        chk_ocr_ts = QCheckBox("ទទួលពេលវេលា OCR (Use OCR start/end timestamps)")
        chk_ocr_ts.setChecked(bool(getattr(self.project.settings, "use_ocr_timestamps", True)))
        form.addRow("", chk_ocr_ts)

        btn_roi = QPushButton("📐 កែតំបន់ ROI (Subtitle Mask & Position)...")
        btn_roi.clicked.connect(self._open_subtitle_mask_dialog)
        form.addRow("តំបន់ស្កេន (ROI):", btn_roi)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.project.settings.ocr_engine = cb_eng.currentData()
            self.project.settings.ocr_fps = spin_fps.value()
            self.project.settings.use_ocr_timestamps = chk_ocr_ts.isChecked()
            self._on_data_changed()

    def _worker_transcribe(self, progress_cb):
        self._last_openai_stt_usage = None
        src_mode = getattr(self.project.settings, "src_mode", "auto") or "auto"
        if src_mode == "openai":
            from app.openai_client import OpenAIClient
            client = OpenAIClient(self.project.settings)
            segments, usage = client.transcribe_video(
                video_path=self.project.video_path,
                source_lang=getattr(self.project.settings, "source_language", "zh") or "zh",
                progress_cb=progress_cb,
                cancel_cb=lambda: getattr(self, "_cancel_requested", False),
            )
            self._last_openai_stt_usage = usage
            return segments, None

        ocr_engine_name = getattr(self.project.settings, "ocr_engine", "rapidocr") or "rapidocr"
        ocr_fps = float(getattr(self.project.settings, "ocr_fps", 5.0))
        use_ocr_ts = bool(getattr(self.project.settings, "use_ocr_timestamps", True))

        use_ocr = False
        if src_mode == "ocr":
            use_ocr = True
        elif src_mode == "auto":
            progress_cb(3, "កំពុងពិនិត្យមើលអក្សរចិនក្នុងវីដេអូ (Auto hardsub probe)...")
            from app.dubbing.ocr_engine import SubtitleOcrEngine
            probe_eng = SubtitleOcrEngine(engine_name=ocr_engine_name)
            use_ocr = probe_eng.detect_hardcoded_subtitles(
                self.project.video_path,
                settings=self.project.settings
            )

        if use_ocr:
            from app.dubbing.ocr_engine import SubtitleOcrEngine
            ocr_eng = SubtitleOcrEngine(engine_name=ocr_engine_name)
            existing_whisper = [
                s for s in self.project.segments
                if getattr(s, "src_source", "") in ("", "Whisper")
            ]

            segments, ocr_report = ocr_eng.scan_video(
                video_path=self.project.video_path,
                settings=self.project.settings,
                fps=ocr_fps,
                min_conf=0.6,
                min_chars=2,
                min_duration=0.3,
                vad_segments=existing_whisper if existing_whisper else None,
                use_ocr_timestamps=use_ocr_ts,
                progress_cb=progress_cb,
                cancel_cb=lambda: getattr(self, "_cancel_requested", False),
            )

            # Run AI repair ONLY on low-confidence OCR rows
            low_conf_segs = [s for s in segments if getattr(s, "ocr_confidence", 1.0) < 0.80]
            if low_conf_segs:
                progress_cb(95, f"កំពុងជួសជុលជួរ OCR confidence ទាប ({len(low_conf_segs)} ជួរ)...")
                try:
                    from app.dubbing.asr_repair import ASRRepairEngine
                    repair_eng = ASRRepairEngine(self.translation_engine)
                    repair_eng.repair_segments(low_conf_segs)
                except Exception as e:
                    print(f"[OCR] Low-conf AI repair note: {e}")

            return segments, ocr_report

        # Standard Whisper Audio Transcription
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
        for seg in segments:
            seg.src_source = "Whisper"
        return segments, None

    def _on_transcribe_finished(self, result):
        if isinstance(result, tuple) and len(result) == 2:
            segments, ocr_report = result
        else:
            segments, ocr_report = result, None

        t_lang = getattr(self.project.settings, "target_language", "km") or "km"
        male_v, female_v = get_voice_pair_for_lang(t_lang)
        for seg in segments:
            seg.voice = female_v if is_female_voice(seg.voice) else male_v
        self.project.segments = segments
        self.segment_table.populate(self.project)
        self._update_stats_display()
        self._update_stepper_states()

        if ocr_report is not None:
            self.status_bar.showMessage(f"OCR complete: {ocr_report.total_segments} phrases extracted.")
            report_msg = (
                f"ស្កេន OCR ({ocr_report.engine_used}) បានបញ្ចប់ដោយជោគជ័យ!\n\n"
                f"• ចំនួនឃ្លាសរុប: {ocr_report.total_segments}\n"
                f"• ជួរ confidence ទាប (< 0.80): {ocr_report.low_conf_count}\n"
                f"• ជួរខុសគ្នាខ្លាំងពី Whisper (CER > 30%): {ocr_report.whisper_mismatch_count}\n"
                f"• Watermark/Logo ដែលបានច្រោះចេញ: {ocr_report.watermarks_filtered}"
            )
            if ocr_report.details:
                report_msg += "\n\nជួរដែលគួរពិនិត្យ:\n" + "\n".join(ocr_report.details[:5])
            QMessageBox.information(self, "របាយការណ៍ស្កេន OCR (OCR Scan Report)", report_msg)
        else:
            stt_usage = getattr(self, "_last_openai_stt_usage", None)
            if stt_usage is not None and getattr(stt_usage, "audio_minutes", 0.0) > 0:
                self.status_bar.showMessage(
                    f"ស្រង់អត្ថបទ OpenAI រួចរាល់: {len(segments)} វគ្គ | {stt_usage.summary_km()}"
                )
                QMessageBox.information(
                    self,
                    "Transcribe Complete",
                    f"បានស្រង់យក {len(segments)} វគ្គដោយជោគជ័យ (OpenAI Audio API)!\n{stt_usage.summary_km()}"
                )
            else:
                self.status_bar.showMessage(f"Transcribed {len(segments)} segments.")
                QMessageBox.information(self, "Transcribe Complete", f"បានស្រង់យក {len(segments)} វគ្គដោយជោគជ័យ!")

    def _run_translation(self):
        if not self.project.segments:
            QMessageBox.warning(self, "No Segments", "សូមដំណើរការស្ដាប់សំឡេង (Transcribe) ជាមុនសិន!")
            return

        imported_cnt = sum(1 for s in self.project.segments if getattr(s, "translation_source", "") == "imported")
        self._skip_imported_on_translate = False
        if imported_cnt > 0:
            ans = QMessageBox.question(
                self,
                "ការពារជួរនាំចូលពី SRT",
                f"មានជួរចំនួន {imported_cnt} ដែលត្រូវបាននាំចូលពី SRT (Imported)។\n"
                "តើអ្នកចង់សរសេរជាន់ជួរទាំងនោះដោយការបកប្រែស្វ័យប្រវត្តិឬទេ?\n"
                "(Yes = សរសេរជាន់ទាំងអស់ | No = រំលងជួរ Imported | Cancel = បោះបង់)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            self._skip_imported_on_translate = (ans == QMessageBox.StandardButton.No)

        self._last_openai_trans_usage = None
        provider = getattr(self.project.settings, "translation_provider", "google") or "google"
        if provider == "openai":
            from app.secrets import get_openai_api_key
            from app.openai_client import estimate_translation_cost
            if not get_openai_api_key():
                QMessageBox.warning(
                    self,
                    "OpenAI API Key",
                    "មិនទាន់មាន OpenAI API Key ទេ! សូមបញ្ចូលក្នុង Settings → OpenAI / ChatGPT ជាមុនសិន។"
                )
                return
            pending = [
                s for s in self.project.segments
                if not (self._skip_imported_on_translate and getattr(s, "translation_source", "") == "imported")
                and (s.text_src or "").strip()
            ]
            oai_model = getattr(self.project.settings, "openai_trans_model", "gpt-4o-mini") or "gpt-4o-mini"
            est = estimate_translation_cost(pending, model=oai_model)
            ans_est = QMessageBox.question(
                self,
                "ប៉ាន់ស្មានតម្លៃ OpenAI Batch",
                f"ម៉ូដែល: {oai_model}\n"
                f"ចំនួនជួរត្រូវបកប្រែ: {len(pending)} ជួរ (Batch 30 ជួរ/ដង)\n"
                f"ប៉ាន់ស្មាន Token: ~{est.total_tokens:,} tokens\n"
                f"ប៉ាន់ស្មានតម្លៃ: ~${est.estimated_usd:.4f} USD\n\n"
                "តើអ្នកចង់ចាប់ផ្ដើមបកប្រែឥឡូវនេះទេ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans_est != QMessageBox.StandardButton.Yes:
                return

        self._start_worker(
            "Translation",
            self._worker_translate,
            self._on_translate_finished
        )

    def _worker_translate(self, progress_cb):
        t_lang = getattr(self.project.settings, "target_language", "km") or "km"
        male_v, female_v = get_voice_pair_for_lang(t_lang)
        skip_imp = getattr(self, "_skip_imported_on_translate", False)
        provider = getattr(self.project.settings, "translation_provider", "google") or "google"

        def _on_usage(stats):
            self._last_openai_trans_usage = stats

        self.translation_engine.settings = self.project.settings
        self.translation_engine.translate_segments(
            segments=self.project.segments,
            glossary=self.project.glossary,
            target_lang=t_lang,
            progress_cb=progress_cb,
            usage_cb=_on_usage if provider == "openai" else None,
            cancel_cb=lambda: getattr(self, "_cancel_requested", False),
            skip_imported=skip_imp,
        )
        for s in self.project.segments:
            if skip_imp and getattr(s, "translation_source", "") == "imported":
                continue
            s.translation_source = "auto"
            s.tts_outdated = True
            s.voice = female_v if is_female_voice(s.voice) else male_v
            s.tts_file = ""
            s.tts_duration = 0.0
        return self.project.segments

    def _on_translate_finished(self, _):
        self.segment_table.populate(self.project)
        self._update_stepper_states()
        t_lang = getattr(self.project.settings, "target_language", "km") or "km"
        lang_lbl = TARGET_LANG_MAP.get(t_lang, {}).get("label", t_lang)
        tr_usage = getattr(self, "_last_openai_trans_usage", None)
        if tr_usage is not None and (getattr(tr_usage, "total_tokens", 0) > 0 or getattr(tr_usage, "estimated_usd", 0.0) > 0):
            summary = tr_usage.summary_km()
            self.status_bar.showMessage(f"បកប្រែទៅ {lang_lbl} រួចរាល់ | {summary}")
            QMessageBox.information(
                self,
                "Translation Complete",
                f"ការបកប្រែទៅជា {lang_lbl} បានរួចរាល់!\n{summary}"
            )
        else:
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

        # Check for segments missing translation (skip segments explicitly imported from external SRT)
        missing_km = [
            s for s in self.project.segments
            if getattr(s, "translation_source", "") != "imported" and s.text_src.strip() and (
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

        has_any_valid_tts = any(
            bool(s.tts_file and os.path.exists(s.tts_file) and not getattr(s, "tts_outdated", False))
            for s in self.project.segments
        )
        has_any_outdated = any(
            getattr(s, "tts_outdated", False) or not s.tts_file or not os.path.exists(s.tts_file)
            for s in self.project.segments
        )
        only_regen_outdated = has_any_valid_tts and has_any_outdated

        to_generate = []
        for s in self.project.segments:
            if only_regen_outdated and s.tts_file and os.path.exists(s.tts_file) and not getattr(s, "tts_outdated", False):
                continue
            to_generate.append(s)

        if to_generate:
            progress_cb(5, f"កំពុងបង្កើតសំឡេង AI ប្រុស {len(to_generate)} វគ្គ (Parallel 5x)...")
            self.tts_engine.generate_segments_batch(
                to_generate,
                speed_fn=lambda s: s.speed if (s.is_modified and s.speed != 1.0) else global_speed,
                max_workers=5,
                progress_cb=lambda pct, msg: progress_cb(int(pct * 0.85), msg),
                cancel_cb=lambda: getattr(self, "_cancel_requested", False),
            )
            for s in to_generate:
                s.is_modified = False
                s.tts_outdated = False

        # Automatically run 4-stage time-sync & lip-sync alignment right after generating voices!
        progress_cb(90, "កំពុងតម្រឹមសំឡេងឱ្យស៊ីគ្នាជាមួយមាត់តួអង្គ (Auto Lip-Sync)...")
        self.current_report = self.sync_engine.synchronize_segments(self.project)
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        progress_cb(100, "បានបង្កើត និងតម្រឹមសំឡេងរួចរាល់ ១០០%!")
        return self.project.segments

    def _on_tts_finished(self, _):
        self.audio_engine.align_speech_and_subtitles(self.project.segments)
        self.segment_table.populate(self.project)
        self._update_stepper_states()
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
        self._update_stepper_states()
        self.status_bar.showMessage("Time synchronization complete.")

        overflow_cnt = sum(
            1 for s in self.project.segments
            if s.sync_status == "OVERFLOW" or getattr(s, "speed", 1.0) > 1.15
        )
        if overflow_cnt > 0:
            self.notif_manager.notify(
                "warning",
                "ការព្រមានល្បឿននិយាយ (Overflow / > 1.15x)",
                f"មានវគ្គចំនួន {overflow_cnt} ដែលវែងជាង slot ឬត្រូវពន្លឿន > 1.15x។",
                persist=True,
            )

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
            self._update_stepper_states()
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
            self._update_stepper_states()
            if getattr(self.project.settings, "notify_sound", True):
                QApplication.beep()
            self.notif_manager.notify(
                "success",
                "Export រួចរាល់",
                f"វីដេអូត្រូវបាននាំចេញដោយជោគជ័យ៖ {Path(out_path).name}",
                action_label="📂 បើកថត",
                action_path=out_path,
                persist=True,
                tray=True,
            )
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

    def _export_zh_srt_chatgpt(self):
        if not self.project.segments:
            QMessageBox.warning(self, "គ្មានទិន្នន័យ", "សូមដំណើរការស្ដាប់សំឡេង (Whisper) ជាមុនសិន!")
            return
        from app.ui.srt_export_dialog import SrtExportForChatGPTDialog
        dlg = SrtExportForChatGPTDialog(self.project, lang=self.lang, parent=self)
        dlg.exec()

    def _import_srt_external(self):
        if not self.project.segments:
            QMessageBox.warning(self, "គ្មានទិន្នន័យ", "សូមដំណើរការស្ដាប់សំឡេង (Whisper) ជាមុនសិន ដើម្បីឱ្យមានជួរពេលវេលាសម្រាប់ផ្គូផ្គង!")
            return
        from app.ui.srt_import_dialog import SrtImportDialog
        dlg = SrtImportDialog(
            project=self.project,
            translation_engine=self.translation_engine,
            lang=self.lang,
            parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.undo_snapshot is not None:
            self._srt_undo_stack.append(dlg.undo_snapshot)
            self._srt_redo_stack.clear()
            if hasattr(self, "act_undo_srt"):
                self.act_undo_srt.setEnabled(True)
            if hasattr(self, "act_redo_srt"):
                self.act_redo_srt.setEnabled(False)
            self.segment_table.populate(self.project)
            self._update_stepper_states()
            self._do_autosave()
            backup_msg = f"\nBackup: {dlg.backup_path}" if dlg.backup_path else ""
            QMessageBox.information(
                self,
                "នាំចូល SRT ជោគជ័យ",
                f"បានអនុវត្តការជំនួសអត្ថបទពី SRT ចំនួន {dlg.changed_count} ជួរដោយជោគជ័យ!{backup_msg}\n"
                "ជួរដែលបានផ្លាស់ប្តូរត្រូវបានសម្គាល់ដើម្បីបង្កើតសំឡេង AI ថ្មី (Regenerate)។"
            )

    def _undo_srt_import(self):
        if not self._srt_undo_stack:
            return
        from app.dubbing.srt_io import create_import_snapshot, restore_import_snapshot
        redo_snap = create_import_snapshot(self.project.segments, description="Redo SRT Import")
        undo_snap = self._srt_undo_stack.pop()
        restore_import_snapshot(self.project, undo_snap)
        self._srt_redo_stack.append(redo_snap)
        if hasattr(self, "act_undo_srt"):
            self.act_undo_srt.setEnabled(len(self._srt_undo_stack) > 0)
        if hasattr(self, "act_redo_srt"):
            self.act_redo_srt.setEnabled(True)
        self.segment_table.populate(self.project)
        self._do_autosave()
        self.status_bar.showMessage("↩️ បានស្ដារអត្ថបទ និងសំឡេងដើមមុនពេលនាំចូល SRT វិញហើយ!", 5000)

    def _redo_srt_import(self):
        if not self._srt_redo_stack:
            return
        from app.dubbing.srt_io import create_import_snapshot, restore_import_snapshot
        undo_snap = create_import_snapshot(self.project.segments, description="Before Redo SRT Import")
        redo_snap = self._srt_redo_stack.pop()
        restore_import_snapshot(self.project, redo_snap)
        self._srt_undo_stack.append(undo_snap)
        if hasattr(self, "act_undo_srt"):
            self.act_undo_srt.setEnabled(True)
        if hasattr(self, "act_redo_srt"):
            self.act_redo_srt.setEnabled(len(self._srt_redo_stack) > 0)
        self.segment_table.populate(self.project)
        self._do_autosave()
        self.status_bar.showMessage("↪️ បានអនុវត្តការនាំចូល SRT ឡើងវិញហើយ!", 5000)

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
        dlg = BatchQueueDialog(
            lang=self.lang,
            base_settings=self.project.settings,
            project_glossary=self.project.glossary,
            parent=self,
        )
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
            if hasattr(self, "seg_ai_speed"):
                spd = getattr(self.project.settings, "ai_voice_speed", 1.05)
                nearest = min([0.90, 1.00, 1.05, 1.15, 1.25], key=lambda v: abs(v - spd))
                self.seg_ai_speed.set_value(nearest, emit=False)
            self._update_mixing_labels()
            self._on_data_changed()

    def _open_subtitle_mask_dialog(self):
        from app.ui.subtitle_mask_dialog import SubtitleMaskDialog
        if hasattr(self, "video_player"):
            self.video_player.ensure_live_mode()
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
            segments=self.project.segments,
            lang=self.lang,
            parent=self
        )
        if dlg.exec():
            if hasattr(self, "chk_mask_sub"):
                self.chk_mask_sub.setChecked(self.project.settings.mask_subtitles)
            if hasattr(self, "video_player"):
                self._sync_overlays_to_player()
                self.video_player.mark_result_stale(True)
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
        
        from app.ui.theme import fit_dialog_to_screen
        dlg = QDialog(self)
        dlg.setWindowTitle("ឯកសារតម្រូវការប្រព័ន្ធ (Software Requirements Specification)")
        fit_dialog_to_screen(dlg, 800, 600)
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
                    font-size: 8.5pt;
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

        self._cancel_requested = False
        if hasattr(self, "lbl_task_name"):
            self.lbl_task_name.setText(name)
            self.lbl_task_name.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        if hasattr(self, "btn_cancel_task"):
            self.btn_cancel_task.setVisible(True)
        self.status_bar.showMessage(f"Starting {name}...")

        worker = PipelineWorker(name, task_fn)
        worker.progress.connect(self._on_worker_progress)
        worker.finished_success.connect(lambda res: self._on_worker_success(res, on_success))
        worker.finished_error.connect(self._on_worker_error)
        self._current_worker = worker
        worker.start()

    def _cancel_current_worker(self):
        self._cancel_requested = True
        if hasattr(self, "btn_cancel_task"):
            self.btn_cancel_task.setVisible(False)
        self.status_bar.showMessage("កំពុងបោះបង់ដំណើរការ...", 4000)

    def closeEvent(self, event):
        try:
            if "--self-test" not in sys.argv and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                qs = QSettings("KhmerAIDubStudio", "AIMovieDubbing")
                if not self.isMaximized() and not self.isMinimized():
                    qs.setValue("main_window/rect", self.geometry())
                if hasattr(self, "splitter"):
                    qs.setValue("main_window/splitter", self.splitter.saveState())
        except Exception:
            pass
        if hasattr(self, "_current_worker") and self._current_worker and self._current_worker.isRunning():
            self._cancel_requested = True
            self.status_bar.showMessage("កំពុងរង់ចាំដំណើរការបញ្ចប់...")
            self._current_worker.wait(2000)
        event.accept()

    def _on_screen_changed(self, screen):
        self._apply_screen_constraints(screen)
        self._update_responsive_header()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_screen_hook_connected", False):
            wh = self.windowHandle()
            if wh is not None:
                wh.screenChanged.connect(self._on_screen_changed)
                self._screen_hook_connected = True
        self._update_responsive_header()

    def _on_worker_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_bar.showMessage(msg)

    def _on_worker_success(self, res, on_success_callback):
        self.progress_bar.setVisible(False)
        if hasattr(self, "lbl_task_name"):
            self.lbl_task_name.setVisible(False)
        if hasattr(self, "btn_cancel_task"):
            self.btn_cancel_task.setVisible(False)
        self.status_bar.showMessage("Ready")
        on_success_callback(res)
        self._on_data_changed()

    def _on_worker_error(self, err_msg: str):
        self.progress_bar.setVisible(False)
        if hasattr(self, "lbl_task_name"):
            self.lbl_task_name.setVisible(False)
        if hasattr(self, "btn_cancel_task"):
            self.btn_cancel_task.setVisible(False)
        if "cancelled" in err_msg.lower():
            self.status_bar.showMessage("បានបោះបង់ដំណើរការ។", 5000)
            return
        self.status_bar.showMessage(f"Error: {err_msg[:40]}")
        self.notif_manager.notify(
            "error",
            "ដំណើរការមានបញ្ហា",
            err_msg[:220],
            action_label="👁️ មើលលម្អិត",
            persist=True,
            action_cb=lambda m=err_msg: QMessageBox.critical(self, "Processing Error Details", m),
        )
        QMessageBox.critical(self, "Processing Error", f"ដំណើរការមានបញ្ហា៖\n{err_msg}")

    # --- Notification System UI Wiring ---
    def _setup_notifications_ui(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(self.windowIcon(), self)
            self.tray_icon.setToolTip(STRINGS[self.lang]["app_title"])
            self.tray_icon.show()

        self.notif_manager.items_changed.connect(self._refresh_bell_badge)
        self.notif_manager.toast_requested.connect(self._show_toast_item)
        self.notif_manager.critical_requested.connect(self._show_critical_modal)
        self.notif_manager.update_banner_requested.connect(self.update_banner.show_update)
        self.notif_manager.tray_requested.connect(self._show_tray_alert)
        self._refresh_bell_badge()

    def _refresh_bell_badge(self):
        if not hasattr(self, "btn_notifications"):
            return
        unread = self.notif_manager.unread_count()
        if unread > 0:
            self.btn_notifications.setText(f"🔔 {unread}")
            self.btn_notifications.setStyleSheet("""
                QPushButton {
                    background-color: #7F1D1D;
                    color: #FEF08A;
                    border: 1px solid #EF4444;
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-weight: bold;
                    font-size: 9pt;
                }
                QPushButton:hover { background-color: #991B1B; }
            """)
        else:
            self.btn_notifications.setText("🔔")
            self.btn_notifications.setStyleSheet("""
                QPushButton {
                    background-color: #162036;
                    color: #E6ECF8;
                    border: 1px solid #243350;
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-weight: bold;
                    font-size: 9pt;
                }
                QPushButton:hover { border-color: #2D8CFF; }
            """)

    def _show_toast_item(self, item):
        from app.ui.notification_ui import NotificationToastWidget
        toast = NotificationToastWidget(item, self)
        toast.clicked.connect(lambda _it: self._open_notification_center())
        toast.closed.connect(self._on_toast_closed)
        self._active_toast = toast
        toast.show()
        toast.raise_()

    def _on_toast_closed(self):
        self._active_toast = None
        self.notif_manager.notify_toast_finished()

    def _show_critical_modal(self, item):
        QMessageBox.critical(
            self,
            f"‼️ {item.title}",
            f"{item.body}\n\n(សូមចុច OK ដើម្បីទទួលស្គាល់)"
        )
        self.notif_manager.mark_read(item.id)

    def _show_tray_alert(self, title: str, message: str):
        if self.tray_icon and getattr(self.project.settings, "notify_windows_tray", True):
            # Show tray notification especially when app is minimized or backgrounded
            if self.isMinimized() or not self.isActiveWindow() or "Export" in title or "Batch" in title:
                self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    def _open_notification_center(self):
        from app.ui.notification_ui import NotificationCenterDialog
        anchor = getattr(self, "btn_notifications", None)
        dlg = NotificationCenterDialog(self.notif_manager, lang=self.lang, anchor_widget=anchor, parent=self)
        dlg.exec()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_header()
        if getattr(self, "_active_toast", None) is not None:
            try:
                self._active_toast._reposition()
            except Exception:
                pass

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
            self.lbl_stats.setStyleSheet("color: #94a3b8; font-size: 8.5pt;")
