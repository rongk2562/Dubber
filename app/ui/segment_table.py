"""
Segment Subtitle & Dubbing Editor Table Widget
F5: Subtitle editor (start / end / source text / Khmer text, editable)
F6: Voice selection
F7: Visual Sync status badges
"""

import os
from typing import List, Optional, Callable
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QComboBox, QLineEdit, QLabel, QMessageBox,
    QAbstractItemView, QDoubleSpinBox, QMenu, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from app.models import Segment, Project
from app.config import (
    KHMER_VOICES, STRINGS, TARGET_LANG_MAP,
    get_voice_pair_for_lang, is_female_voice, get_voice_actor_name, normalize_to_male_voice
)
from app.utils import format_timestamp

class SegmentTableWidget(QWidget):
    segment_selected = Signal(Segment)
    play_segment_requested = Signal(float, float) # start_sec, end_sec
    preview_tts_requested = Signal(Segment)
    segment_speed_changed = Signal(object, float) # seg, new_speed
    data_changed = Signal()

    COL_ID = 0
    COL_START = 1
    COL_END = 2
    COL_SLOT = 3
    COL_SPEAKER = 4
    COL_SRC = 5
    COL_KM = 6
    COL_VOICE = 7
    COL_TTS_DUR = 8
    COL_SPEED = 9
    COL_STATUS = 10
    COL_ACTIONS = 11

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.lang = "km"
        self._is_populating = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Toolbar above table — Equal-sized boxes with zero overlap
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)

        btn_base_qss = """
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #64748b;
            }
        """

        self.btn_add = QPushButton("➕ បន្ថែម")
        self.btn_add.setToolTip("បន្ថែមវគ្គថ្មី (Add Segment)")
        self.btn_add.setStyleSheet(btn_base_qss)
        self.btn_add.clicked.connect(self._add_segment)
        top_bar.addWidget(self.btn_add, 1)

        self.btn_split = QPushButton("✂️ ពុះ")
        self.btn_split.setToolTip("ពុះវគ្គដែលបានជ្រើស (Split Selected)")
        self.btn_split.setStyleSheet(btn_base_qss)
        self.btn_split.clicked.connect(self._split_selected)
        top_bar.addWidget(self.btn_split, 1)

        self.btn_merge = QPushButton("🔗 បញ្ចូល")
        self.btn_merge.setToolTip("បញ្ចូលវគ្គដែលបានជ្រើស (Merge Selected)")
        self.btn_merge.setStyleSheet(btn_base_qss)
        self.btn_merge.clicked.connect(self._merge_selected)
        top_bar.addWidget(self.btn_merge, 1)

        self.btn_delete = QPushButton("🗑️ លុប")
        self.btn_delete.setToolTip("លុបវគ្គដែលបានជ្រើស (Delete Selected)")
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #f87171;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #ef4444;
            }
        """)
        self.btn_delete.clicked.connect(self._delete_selected)
        top_bar.addWidget(self.btn_delete, 1)

        self.btn_clear = QPushButton("🧹 សម្អាត")
        self.btn_clear.setToolTip("សម្អាតអត្ថបទបកប្រែ សំឡេង AI ឬសម្អាតតារាងទាំងមូល (Clear Translations / Voices / All)")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #7f1d1d;
                color: #fecaca;
                border: 1px solid #ef4444;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #991b1b;
            }
        """)
        self.btn_clear.clicked.connect(self._show_clear_menu)
        top_bar.addWidget(self.btn_clear, 1)

        self.btn_auto_gender = QPushButton("🎭 ស្មានសំឡេង")
        self.btn_auto_gender.setVisible(False) # Single Male Voice mode only

        self.btn_humanize = QPushButton("✨ សម្ដីរស់រវើក")
        self.btn_humanize.setStyleSheet("""
            QPushButton {
                background-color: #0d9488;
                color: white;
                border: 1px solid #14b8a6;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0f766e;
            }
        """)
        self.btn_humanize.setToolTip("កែសម្រួលអត្ថបទឱ្យក្លាយជាសម្ដីនិយាយបែបភាពយន្តរស់រវើក និងបន្ថែមសញ្ញាអារម្មណ៍ (? / !)")
        self.btn_humanize.clicked.connect(self._humanize_dialogue)
        top_bar.addWidget(self.btn_humanize, 1)

        # Quick Batch Speed Controls
        self.lbl_batch_spd = QLabel("⚡ ល្បឿន:")
        self.lbl_batch_spd.setVisible(False) # Keep attribute for compatibility while saving horizontal space

        self.cb_batch_speed = QComboBox()
        self.cb_batch_speed.setToolTip("ជ្រើសរើសល្បឿនសំឡេង AI (Select AI Voice Speed)")
        self.cb_batch_speed.setStyleSheet("""
            QComboBox {
                background-color: #1e293b;
                color: #38bdf8;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #38bdf8;
            }
        """)
        for spd_val in [0.80, 0.90, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.40, 1.50]:
            self.cb_batch_speed.addItem(f"⚡ {spd_val:.2f}x", spd_val)
        self.cb_batch_speed.setCurrentIndex(3) # Default 1.05x
        top_bar.addWidget(self.cb_batch_speed, 1)

        self.btn_batch_speed = QPushButton("⚡ " + ("ដាក់ល្បឿន" if self.lang == "km" else "Set Speed"))
        self.btn_batch_speed.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: white;
                border: 1px solid #38bdf8;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_batch_speed.setToolTip("កំណត់ល្បឿននេះទៅកាន់វគ្គដែលបានជ្រើសរើស (ឬគ្រប់វគ្គទាំងអស់បើមិនបានជ្រើស) (Set speed for selected or all segments)")
        self.btn_batch_speed.clicked.connect(self._apply_batch_speed)
        top_bar.addWidget(self.btn_batch_speed, 1)

        # Search / filter (Hidden per user preference)
        self.txt_filter = QLineEdit()
        self.txt_filter.setVisible(False)

        # Enforce uniform 34px height and equal expanding policy across all 8 active boxes
        for w in [
            self.btn_add, self.btn_split, self.btn_merge, self.btn_delete,
            self.btn_clear, self.btn_humanize, self.cb_batch_speed, self.btn_batch_speed
        ]:
            w.setFixedHeight(34)
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout.addLayout(top_bar)

        # Main Table
        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)

        # Set Column Headers
        self.update_headers()

        # Resize modes & Scrollbars
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setHighlightSections(False)

        # Balanced column widths so all 12 columns fit 100% inside the table viewport without clipping 'សកម្មភាព'
        self._col_widths = {
            self.COL_ID: 32,
            self.COL_START: 58,
            self.COL_END: 52,
            self.COL_SLOT: 48,
            self.COL_SPEAKER: 48,
            self.COL_SRC: 130,
            self.COL_KM: 130,
            self.COL_VOICE: 86,
            self.COL_TTS_DUR: 58,
            self.COL_SPEED: 58,
            self.COL_STATUS: 70,
            self.COL_ACTIONS: 74,
        }
        for col, width in self._col_widths.items():
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)

        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self.table)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_column_widths()

    def _adjust_column_widths(self):
        """Dynamically distribute extra viewport space to text columns while preserving full visibility of 'សកម្មភាព'."""
        try:
            total_w = self.table.viewport().width()
            if total_w <= 100:
                return
            fixed_sum = sum(w for col, w in self._col_widths.items() if col not in (self.COL_SRC, self.COL_KM))
            avail_for_text = max(160, total_w - fixed_sum - 4)
            half_w = avail_for_text // 2
            for col, width in self._col_widths.items():
                if col == self.COL_SRC:
                    self.table.setColumnWidth(col, half_w)
                elif col == self.COL_KM:
                    self.table.setColumnWidth(col, avail_for_text - half_w)
                else:
                    self.table.setColumnWidth(col, width)
        except Exception:
            pass

    def set_language(self, lang: str):
        self.lang = lang
        self.update_headers()
        self.update_button_texts()

    def update_headers(self):
        s = STRINGS.get(self.lang, STRINGS["km"])
        t_lang = getattr(self.project.settings, "target_language", "km") if self.project else "km"
        if t_lang == "km":
            target_col_title = s["col_khmer_text"]
        else:
            lang_info = TARGET_LANG_MAP.get(t_lang, {})
            short_lbl = lang_info.get("label", t_lang)
            target_col_title = f"បកប្រែ ({short_lbl})" if self.lang == "km" else f"Translation ({short_lbl})"

        headers = [
            s["col_id"],
            s["col_start"],
            s["col_end"],
            s["col_slot"],
            s["col_speaker"],
            s["col_source_text"],
            target_col_title,
            s["col_voice"],
            s["col_tts_dur"],
            s["col_speed"],
            s["col_sync_status"],
            s["col_actions"],
        ]
        self.table.setHorizontalHeaderLabels(headers)

    def update_button_texts(self):
        if self.lang == "km":
            self.btn_add.setText("➕ បន្ថែម")
            self.btn_split.setText("✂️ ពុះ")
            self.btn_merge.setText("🔗 បញ្ចូល")
            self.btn_delete.setText("🗑️ លុប")
            self.btn_clear.setText("🧹 សម្អាត")
            self.btn_auto_gender.setText("🎭 ស្មានសំឡេង")
            self.btn_humanize.setText("✨ សម្ដីរស់រវើក")
            self.lbl_batch_spd.setText("⚡ ល្បឿន:")
            self.btn_batch_speed.setText("⚡ ដាក់ល្បឿន")
            self.txt_filter.setPlaceholderText("🔍 ស្វែងរក...")
        else:
            self.btn_add.setText("➕ Add")
            self.btn_split.setText("✂️ Split")
            self.btn_merge.setText("🔗 Merge")
            self.btn_delete.setText("🗑️ Delete")
            self.btn_clear.setText("🧹 Clear")
            self.btn_auto_gender.setText("🎭 Auto Voices")
            self.btn_humanize.setText("✨ Humanize")
            self.lbl_batch_spd.setText("⚡ Speed:")
            self.btn_batch_speed.setText("⚡ Set Speed")
            self.txt_filter.setPlaceholderText("🔍 Search...")

    def populate(self, project: Project):
        self._is_populating = True
        self.project = project
        self.update_headers()
        self.table.setRowCount(0)

        for row, seg in enumerate(project.segments):
            self.table.insertRow(row)

            # 0: ID
            item_id = QTableWidgetItem(str(seg.id))
            item_id.setFlags(item_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_ID, item_id)

            # 1: Start (MM:SS.ms matching original video clock)
            from app.utils import format_timestamp
            start_disp = format_timestamp(seg.start, srt_format=False)
            item_start = QTableWidgetItem(start_disp)
            item_start.setToolTip(f"{start_disp} ({seg.start:.2f}s)")
            item_start.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_START, item_start)

            # 2: End (MM:SS.ms matching original video clock)
            end_disp = format_timestamp(seg.end, srt_format=False)
            item_end = QTableWidgetItem(end_disp)
            item_end.setToolTip(f"{end_disp} ({seg.end:.2f}s)")
            item_end.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_END, item_end)

            # 3: Slot (Computed)
            item_slot = QTableWidgetItem(f"{seg.slot:.2f}")
            item_slot.setFlags(item_slot.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_slot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_slot.setForeground(QColor("#38bdf8"))
            self.table.setItem(row, self.COL_SLOT, item_slot)

            # 4: Speaker (Single Male Voice mode)
            seg.speaker = "ប្រុស (Male)"
            item_spk = QTableWidgetItem("👨 ប្រុស" if self.lang == "km" else "👨 Male")
            item_spk.setFlags(item_spk.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_spk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_spk.setForeground(QColor("#38bdf8"))
            self.table.setItem(row, self.COL_SPEAKER, item_spk)

            # 5: Original Speech
            item_src = QTableWidgetItem(seg.text_src)
            self.table.setItem(row, self.COL_SRC, item_src)

            # 6: Khmer Translation
            item_km = QTableWidgetItem(seg.text_km)
            km_font = QFont("Kantumruy Pro", 11)
            item_km.setFont(km_font)
            item_km.setForeground(QColor("#fef08a") if seg.text_km else QColor("#94a3b8"))
            self.table.setItem(row, self.COL_KM, item_km)

            # 7: Voice (Locked to Single Male Voice of active Target Language)
            t_lang = getattr(self.project.settings, "target_language", "km") if self.project else "km"
            seg.voice = normalize_to_male_voice(seg.voice, t_lang)
            actor_name = get_voice_actor_name(seg.voice)
            btn_lbl = f"👨 ប្រុស ({actor_name})" if self.lang == "km" else f"👨 Male ({actor_name})"
            lbl_voice = QLabel(btn_lbl)
            lbl_voice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_voice.setStyleSheet("background-color: #0c4a6e; color: #38bdf8; font-weight: bold; font-size: 11px; border-radius: 4px; padding: 2px 3px;")
            self.table.setCellWidget(row, self.COL_VOICE, lbl_voice)

            # 8: TTS Duration
            item_dur = QTableWidgetItem(f"{seg.tts_duration:.2f}" if seg.tts_duration > 0 else "-")
            item_dur.setFlags(item_dur.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_TTS_DUR, item_dur)

            # 9: Interactive Speed SpinBox
            spin_speed = QDoubleSpinBox()
            spin_speed.setRange(0.50, 2.00)
            spin_speed.setSingleStep(0.05)
            spin_speed.setDecimals(2)
            spin_speed.setSuffix("x")
            spin_speed.setValue(seg.speed)
            spin_speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin_speed.setStyleSheet("""
                QDoubleSpinBox {
                    background-color: #0f172a;
                    color: #38bdf8;
                    font-weight: bold;
                    border: 1px solid #334155;
                    border-radius: 4px;
                    padding: 1px 0px;
                    font-size: 11px;
                }
                QDoubleSpinBox:hover {
                    border-color: #0284c7;
                    color: #7dd3fc;
                }
                QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                    width: 13px;
                }
            """)
            spin_speed.valueChanged.connect(lambda val, s=seg: self._on_seg_speed_spin_changed(s, val))
            self.table.setCellWidget(row, self.COL_SPEED, spin_speed)

            # 10: Sync Status Badge
            self._update_status_cell(row, seg)

            # 11: Action Buttons Container
            action_widget = QWidget()
            act_layout = QHBoxLayout(action_widget)
            act_layout.setContentsMargins(2, 2, 2, 2)
            act_layout.setSpacing(4)

            btn_style = """
                QPushButton {
                    padding: 0px;
                    font-size: 11px;
                    border-radius: 4px;
                    background-color: #1e293b;
                    border: 1px solid #334155;
                }
                QPushButton:hover {
                    background-color: #0284c7;
                    border-color: #38bdf8;
                }
            """

            btn_play = QPushButton("▶")
            btn_play.setToolTip("Play video segment" if self.lang == "en" else "ចាក់វគ្គនេះក្នុងវីដេអូ")
            btn_play.setFixedSize(28, 24)
            btn_play.setStyleSheet(btn_style)
            btn_play.clicked.connect(
                lambda checked, s=seg: self.play_segment_requested.emit(
                    s.speech_start,
                    s.speech_end
                )
            )
            act_layout.addWidget(btn_play)

            btn_tts = QPushButton("🔊")
            btn_tts.setToolTip("Preview Khmer voice" if self.lang == "en" else "ស្ដាប់សំឡេង AI ខ្មែរ")
            btn_tts.setFixedSize(28, 24)
            btn_tts.setStyleSheet(btn_style)
            btn_tts.clicked.connect(lambda checked, s=seg: self.preview_tts_requested.emit(s))
            act_layout.addWidget(btn_tts)

            self.table.setCellWidget(row, self.COL_ACTIONS, action_widget)

        self._is_populating = False
        self._adjust_text_columns()

    def _update_status_cell(self, row: int, seg: Segment):
        """Render colored badge based on SRS Time-sync rules & speech-rate QA gate."""
        s = STRINGS.get(self.lang, STRINGS["km"])
        status = seg.sync_status
        label_text = s.get("sync_ok", "OK")
        bg_color = "#065f46" # Green
        fg_color = "#34d399"

        # Calculate actual speech rate
        from app.dubbing.speech_rate import khmer_syllable_count
        syl = khmer_syllable_count(seg.text_km or seg.text_src)
        dur = max(0.2, seg.tts_duration or seg.slot)
        syl_rate = round(syl / dur, 1)

        if syl_rate > 7.5:
            label_text = f"⚡ {syl_rate}/s"
            bg_color = "#881337" # Red highlight
            fg_color = "#fda4af"
        elif status in ("SPEED_ADJUSTED", "SPED_UP"):
            label_text = f"{seg.speed:.2f}x"
            bg_color = "#1e3a8a" # Blue
            fg_color = "#60a5fa"
        elif status == "SHORTENED":
            label_text = s.get("sync_shortened", "Shortened")
            bg_color = "#581c87" # Purple
            fg_color = "#c084fc"
        elif status == "GAP_BORROWED":
            label_text = s.get("sync_borrowed", "Gap Borrowed")
            bg_color = "#854d0e" # Amber
            fg_color = "#fde047"
        elif status == "TIME_STRETCHED":
            label_text = s.get("sync_time_stretched", "Stretched")
            bg_color = "#9a3412" # Orange
            fg_color = "#fdba74"
        elif status == "OVERFLOW":
            label_text = s.get("sync_overflow", "OVERFLOW")
            bg_color = "#881337" # Red
            fg_color = "#f43f5e"

        src_tag = getattr(seg, "src_source", "")
        if src_tag == "Manual":
            src_badge = "កែដោយដៃ"
        elif src_tag in ("OCR", "Whisper"):
            src_badge = src_tag
        else:
            src_badge = ""

        if getattr(seg, "needs_review", False):
            label_text = f"⚠️ {label_text}"
            if bg_color == "#065f46":
                bg_color = "#854d0e"
                fg_color = "#fde047"

        display_badge = f"{src_badge} | {label_text}" if src_badge else label_text
        item_status = QTableWidgetItem(display_badge)
        item_status.setFlags(item_status.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item_status.setBackground(QColor(bg_color))
        item_status.setForeground(QColor(fg_color))
        cer_info = f" | CER vs Whisper: {getattr(seg, 'whisper_cer', 0.0)*100:.0f}%" if getattr(seg, "whisper_cer", 0.0) > 0 else ""
        item_status.setToolTip(f"Source: {src_badge or 'Auto'} | {syl} syl / {dur:.2f}s = {syl_rate}/s | Speed: {seg.speed:.2f}x{cer_info}")
        self.table.setItem(row, self.COL_STATUS, item_status)

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._is_populating or not self.project:
            return
        row = item.row()
        col = item.column()
        if row >= len(self.project.segments):
            return

        seg = self.project.segments[row]

        try:
            from app.utils import parse_timestamp
            if col == self.COL_START:
                seg.start = round(parse_timestamp(item.text()), 2)
                seg.actual_start = seg.start
                slot_item = self.table.item(row, self.COL_SLOT)
                if slot_item:
                    slot_item.setText(f"{seg.slot:.2f}")
            elif col == self.COL_END:
                seg.end = round(parse_timestamp(item.text()), 2)
                seg.actual_end = seg.end
                slot_item = self.table.item(row, self.COL_SLOT)
                if slot_item:
                    slot_item.setText(f"{seg.slot:.2f}")
            elif col == self.COL_SRC:
                seg.text_src = item.text()
                seg.src_source = "Manual"
            elif col == self.COL_KM:
                seg.text_km = item.text()
                item.setForeground(QColor("#fef08a") if seg.text_km else QColor("#94a3b8"))
            elif col == self.COL_SPEED:
                seg.speed = float(item.text().replace("x", ""))
            
            seg.is_modified = True
            self.data_changed.emit()
        except ValueError:
            pass

    def _on_speaker_changed(self, row: int, speaker: str):
        if self._is_populating or not self.project:
            return
        if row < len(self.project.segments):
            self.project.segments[row].speaker = speaker
            self.data_changed.emit()

    def _on_voice_changed(self, row: int, voice_id: str):
        if self._is_populating or not self.project:
            return
        if row < len(self.project.segments):
            self.project.segments[row].voice = voice_id
            self.data_changed.emit()

    def _on_seg_speed_spin_changed(self, seg: Segment, val: float):
        if self._is_populating or not self.project:
            return
        new_speed = round(val, 2)
        if abs(seg.speed - new_speed) > 0.01:
            seg.speed = new_speed
            seg.is_modified = True
            self.segment_speed_changed.emit(seg, new_speed)
            self.data_changed.emit()

    def _apply_batch_speed(self):
        if not self.project or not self.project.segments:
            return
        target_speed = float(self.cb_batch_speed.currentData())
        sel_rows = [r.row() for r in self.table.selectionModel().selectedRows()]
        
        target_segs = [self.project.segments[r] for r in sel_rows if r < len(self.project.segments)]
        if not target_segs:
            target_segs = self.project.segments
            
        for s in target_segs:
            s.speed = target_speed
            s.is_modified = True
            
        self.populate(self.project)
        self.data_changed.emit()
        count = len(target_segs)
        msg = f"បានកំណត់ល្បឿន {target_speed:.2f}x ទៅលើ {count} វគ្គ!" if self.lang == "km" else f"Updated speed to {target_speed:.2f}x for {count} segments!"
        QMessageBox.information(self, "Speed Updated", msg)

    def _on_selection_changed(self):
        sel_rows = self.table.selectionModel().selectedRows()
        if sel_rows and self.project:
            row = sel_rows[0].row()
            if row < len(self.project.segments):
                self.segment_selected.emit(self.project.segments[row])

    def _add_segment(self):
        if not self.project:
            return
        new_id = len(self.project.segments) + 1
        last_end = self.project.segments[-1].end if self.project.segments else 0.0
        new_seg = Segment(id=new_id, start=last_end + 0.5, end=last_end + 3.5, speaker="S1")
        self.project.segments.append(new_seg)
        self.populate(self.project)
        self.table.selectRow(self.table.rowCount() - 1)
        self.data_changed.emit()

    def _delete_selected(self):
        if not self.project:
            return
        sel_rows = sorted([r.row() for r in self.table.selectionModel().selectedRows()], reverse=True)
        if not sel_rows:
            return
        
        for r in sel_rows:
            if r < len(self.project.segments):
                self.project.segments.pop(r)

        # Re-index
        for idx, s in enumerate(self.project.segments):
            s.id = idx + 1

        self.populate(self.project)
        self.data_changed.emit()

    def _show_clear_menu(self):
        """Show popup menu with 3 clear options: Translations Only, Voices Only, or All Segments."""
        if not self.project or not self.project.segments:
            msg = "មិនមានទិន្នន័យក្នុងតារាងដើម្បីសម្អាតទេ!" if self.lang == "km" else "No segments in table to clear!"
            QMessageBox.information(self, "Clear", msg)
            return

        menu = QMenu(self)
        if self.lang == "km":
            act_clear_trans = menu.addAction("🌐 ១. សម្អាតតែអត្ថបទបកប្រែ (Clear Translations Only)")
            act_clear_tts = menu.addAction("🔇 ២. សម្អាតតែសំឡេង AI ចាស់ (Clear AI Voices Only)")
            menu.addSeparator()
            act_clear_all = menu.addAction("🗑️ ៣. សម្អាតវគ្គទាំងអស់ចេញពីតារាង (Clear All Segments)")
        else:
            act_clear_trans = menu.addAction("🌐 1. Clear Translations Only")
            act_clear_tts = menu.addAction("🔇 2. Clear Generated AI Voices Only")
            menu.addSeparator()
            act_clear_all = menu.addAction("🗑️ 3. Clear All Segments")

        chosen = menu.exec(self.btn_clear.mapToGlobal(self.btn_clear.rect().bottomLeft()))
        if chosen == act_clear_trans:
            self._clear_translations_only()
        elif chosen == act_clear_tts:
            self._clear_voices_only()
        elif chosen == act_clear_all:
            self._clear_all_segments()

    def _clear_translations_only(self):
        if not self.project or not self.project.segments:
            return
        for seg in self.project.segments:
            seg.text_km = ""
            seg.tts_file = ""
            seg.tts_duration = 0.0
            seg.sync_status = "OK"
            seg.is_modified = True
        self.populate(self.project)
        self.data_changed.emit()
        msg = (
            "បានសម្អាតអត្ថបទបកប្រែទាំងអស់រួចរាល់! ឥឡូវអ្នកអាចជ្រើសរើសភាសាថ្មី ហើយចុច '២. បកប្រែ' ម្តងទៀត។"
            if self.lang == "km" else
            "Cleared all translations! You can now select a new target language and click '2. Translate'."
        )
        QMessageBox.information(self, "Cleared Translations", msg)

    def _clear_voices_only(self):
        if not self.project or not self.project.segments:
            return
        for seg in self.project.segments:
            seg.tts_file = ""
            seg.tts_duration = 0.0
            seg.sync_status = "OK"
            seg.is_modified = True
        self.populate(self.project)
        self.data_changed.emit()
        msg = (
            "បានសម្អាតឯកសារសំឡេង AI ចាស់រួចរាល់! សូមចុច '៣. សំឡេង AI' ដើម្បីបង្កើតសំឡេងថ្មី។"
            if self.lang == "km" else
            "Cleared generated AI voices! Click '3. Voices' to synthesize new voices."
        )
        QMessageBox.information(self, "Cleared Voices", msg)

    def _clear_all_segments(self):
        if not self.project or not self.project.segments:
            return
        prompt = (
            f"តើអ្នកពិតជាចង់សម្អាតវគ្គទាំងអស់ ({len(self.project.segments)} វគ្គ) ចេញពីតារាងមែនទេ?"
            if self.lang == "km" else
            f"Are you sure you want to clear all {len(self.project.segments)} segments from the table?"
        )
        reply = QMessageBox.question(
            self,
            "បញ្ជាក់ការសម្អាត (Confirm Clear All)" if self.lang == "km" else "Confirm Clear All",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.project.segments.clear()
            self.populate(self.project)
            self.data_changed.emit()

    def _split_selected(self):
        """Split chosen segment in half"""
        if not self.project:
            return
        sel_rows = self.table.selectionModel().selectedRows()
        if not sel_rows:
            return
        row = sel_rows[0].row()
        seg = self.project.segments[row]

        mid = round(seg.start + (seg.end - seg.start) / 2.0, 2)
        old_end = seg.end
        seg.end = mid

        new_seg = Segment(
            id=seg.id + 1,
            start=mid,
            end=old_end,
            speaker=seg.speaker,
            voice=seg.voice
        )
        self.project.segments.insert(row + 1, new_seg)

        for idx, s in enumerate(self.project.segments):
            s.id = idx + 1

        self.populate(self.project)
        self.data_changed.emit()

    def _merge_selected(self):
        """Merge two consecutive selected segments"""
        if not self.project:
            return
        sel_rows = sorted([r.row() for r in self.table.selectionModel().selectedRows()])
        if len(sel_rows) < 2:
            return
        
        first = self.project.segments[sel_rows[0]]
        last = self.project.segments[sel_rows[-1]]
        
        # Merge text
        all_src = " ".join([self.project.segments[r].text_src for r in sel_rows if self.project.segments[r].text_src])
        all_km = " ".join([self.project.segments[r].text_km for r in sel_rows if self.project.segments[r].text_km])
        
        first.end = last.end
        first.text_src = all_src
        first.text_km = all_km
        
        for r in reversed(sel_rows[1:]):
            self.project.segments.pop(r)
            
        for idx, s in enumerate(self.project.segments):
            s.id = idx + 1

        self.populate(self.project)
        self.data_changed.emit()

    def _filter_table(self, query: str):
        query = query.strip().lower()
        for r in range(self.table.rowCount()):
            item_src = self.table.item(r, self.COL_SRC)
            item_km = self.table.item(r, self.COL_KM)
            src_match = query in (item_src.text().lower() if item_src else "")
            km_match = query in (item_km.text().lower() if item_km else "")
            self.table.setRowHidden(r, not (src_match or km_match))

    def _toggle_seg_voice(self, seg: Segment):
        """Toggle voice between Male and Female of the active target language with 1 click."""
        t_lang = getattr(self.project.settings, "target_language", "km") if self.project else "km"
        male_v, female_v = get_voice_pair_for_lang(t_lang)
        if is_female_voice(seg.voice):
            seg.voice = male_v
            seg.speaker = "ប្រុស (Male)"
        else:
            seg.voice = female_v
            seg.speaker = "ស្រី (Female)"

        # Invalidate old TTS clip so new voice will synthesize
        seg.tts_file = ""
        seg.tts_duration = 0.0

        self.populate(self.project)
        self.data_changed.emit()

    def _auto_detect_genders(self):
        """
        Scan actual speech audio pitch (F0 frequency) and dialogue context
        to automatically assign Male or Female voices of the active target language.
        """
        if not self.project or not self.project.segments:
            return

        # Check if project audio file exists for acoustic voice detection
        audio_path = self.project.audio_path
        if not audio_path and self.project.video_path:
            from pathlib import Path
            v_name = Path(self.project.video_path).stem
            pot_audio = Path("d:/summray_mo/temp") / f"{v_name}_extracted_16k.wav"
            if pot_audio.exists():
                audio_path = str(pot_audio)

        filtered_audio = None
        sr = 16000
        stt_eng = None
        if audio_path and os.path.exists(audio_path):
            try:
                from app.stt_engine import STTEngine
                import numpy as np
                import scipy.signal
                from pydub import AudioSegment
                stt_eng = STTEngine()
                audio_seg = AudioSegment.from_file(audio_path).set_channels(1).set_frame_rate(sr)
                raw_samples = np.array(audio_seg.get_array_of_samples(), dtype=np.float32)
                b, a = scipy.signal.butter(3, 70 / (sr / 2), btype='high')
                filtered_audio = scipy.signal.filtfilt(b, a, raw_samples)
            except Exception as e:
                print(f"[SegmentTable] Acoustic load note: {e}")

        female_keywords = [
            "ស្ត្រី", "នាង", "កូនក្រមុំ", "បងស្រី", "ប្អូនស្រី", "អូន", "ចាស",
            "ម្ចាស់ក្សត្រី", "ម៉ាក់", "អ្នកម្តាយ", "កញ្ញា", "ជំទាវ", "យាយ", "មីង",
            "នារី", "ស្រី", "ម៉ែ", "មេម៉ាយ", "កូនស្រី", "ចៅស្រី"
        ]
        male_keywords = [
            "បងប្រុស", "កូនកំលោះ", "លោក", "បាទ", "លោកឪពុក", "ប៉ា", "តា", "ពូ",
            "បុរស", "ប្រុស", "លោកម្ចាស់", "បង", "មេបញ្ជាការ", "កូនប្រុស", "ចៅប្រុស", "ព្រះអង្គ"
        ]

        t_lang = getattr(self.project.settings, "target_language", "km") if self.project else "km"
        male_voice, female_voice = get_voice_pair_for_lang(t_lang)

        updated = 0
        for seg in self.project.segments:
            # 1. Acoustic voice pitch detection
            gender = None
            if filtered_audio is not None and stt_eng is not None:
                gender = stt_eng.detect_slice_gender(filtered_audio, sr, seg.start, seg.end)

            # 2. Linguistic context clues
            text = (seg.text_km + " " + seg.text_src).lower()
            has_female = any(kw in text for kw in female_keywords)
            has_male = any(kw in text for kw in male_keywords)

            if has_female and not has_male:
                target_voice = female_voice
            elif has_male and not has_female:
                target_voice = male_voice
            elif gender == "Female":
                target_voice = female_voice
            elif gender == "Male":
                target_voice = male_voice
            else:
                continue

            speaker_tag = "ស្រី (Female)" if target_voice == female_voice else "ប្រុស (Male)"
            seg.speaker = speaker_tag

            if seg.voice != target_voice:
                seg.voice = target_voice
                seg.tts_file = "" # invalidate clip
                seg.tts_duration = 0.0
                updated += 1

        self.populate(self.project)
        self.data_changed.emit()

        msg = (
            f"បានស្ដាប់វិភាគរលកសំឡេង (Pitch Frequency) និងកំណត់សំឡេងតួអង្គ (ប្រុស/ស្រី) ដោយស្វ័យប្រវត្តិចំនួន {updated} វគ្គ!"
            if self.lang == "km" else
            f"Successfully auto-detected and assigned character voices (Male / Female) for {updated} segments!"
        )
        QMessageBox.information(self, "Auto Voices", msg)

    def _humanize_dialogue(self):
        """Transform selected or all segments into natural spoken Khmer dialogue."""
        if not self.project or not self.project.segments:
            return
        from app.translation_engine import TranslationEngine
        tr_engine = TranslationEngine(self.project.settings)
        
        selected_rows = sorted(list(set(index.row() for index in self.table.selectedIndexes())))
        target_segs = [self.project.segments[r] for r in selected_rows] if selected_rows else self.project.segments
        
        count = 0
        for seg in target_segs:
            old_km = seg.text_km
            new_km = tr_engine.humanize_khmer_dialogue(seg.text_km, seg.text_src, slot_duration=seg.slot)
            if new_km != old_km:
                seg.text_km = new_km
                seg.tts_file = "" # invalidate clip so user regenerates with emotional prosody
                seg.tts_duration = 0.0
                count += 1
                
        if count > 0:
            self.populate(self.project)
            self.data_changed.emit()
            msg = (
                f"បានកែសម្រួលអត្ថបទឱ្យក្លាយជាសម្ដីនិយាយរស់រវើកបែបភាពយន្តចំនួន {count} វគ្គរួចរាល់!\n"
                f"សូមចុច '៣. បង្កើតសំឡេង AI ខ្មែរ' ដើម្បីបង្កើតសំឡេងថ្មីប្រកបដោយអារម្មណ៍ធម្មជាតិ។"
                if self.lang == "km" else
                f"Successfully humanized dialogue for {count} segments into movie-dubbing phrasing!\n"
                f"Click '3. Generate AI Voices' to synthesize natural emotional speech."
            )
            QMessageBox.information(self, "Humanize Dialogue", msg)
        else:
            msg = "អត្ថបទទាំងអស់មានទម្រង់និយាយបែបធម្មជាតិរួចរាល់ហើយ!" if self.lang == "km" else "All dialogue is already in natural conversational phrasing!"
            QMessageBox.information(self, "Humanize Dialogue", msg)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_text_columns()

    def showEvent(self, event):
        super().showEvent(event)
        self._adjust_text_columns()

    def _adjust_text_columns(self):
        """Dynamically allocate remaining table width to text columns so all 12 columns fit flush inside the viewport."""
        if not hasattr(self, "table") or not hasattr(self, "_col_widths"):
            return
        fixed_cols = [
            self.COL_ID, self.COL_START, self.COL_END, self.COL_SLOT,
            self.COL_SPEAKER, self.COL_VOICE, self.COL_TTS_DUR,
            self.COL_SPEED, self.COL_STATUS, self.COL_ACTIONS
        ]
        fixed_total = sum(self.table.columnWidth(c) for c in fixed_cols if not self.table.isColumnHidden(c))
        viewport_w = self.table.viewport().width()
        available = viewport_w - fixed_total - 2

        if available >= 160:
            src_w = available // 2
            km_w = available - src_w
            self.table.setColumnWidth(self.COL_SRC, src_w)
            self.table.setColumnWidth(self.COL_KM, km_w)
        else:
            min_src = self._col_widths.get(self.COL_SRC, 120)
            min_km = self._col_widths.get(self.COL_KM, 120)
            self.table.setColumnWidth(self.COL_SRC, min_src)
            self.table.setColumnWidth(self.COL_KM, min_km)

