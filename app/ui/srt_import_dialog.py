"""
Robust SRT Import & Diff Preview Dialog.
Supports UTF-8/BOM/UTF-16 auto-detection, index/time-overlap/order matching,
merge/split detection, speech-rate validation, optional AI condensation,
locked-row protection, and side-by-side diff preview before applying.
"""

from pathlib import Path
from typing import List, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QRadioButton, QButtonGroup, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QGroupBox,
    QLineEdit, QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont

from app.models import Project
from app.config import DATA_DIR
from app.dubbing.srt_io import (
    SrtParseResult, DiffItem, ImportSnapshot,
    parse_srt_file, match_srt_to_segments, apply_srt_diff,
    STATUS_OK, STATUS_NEW, STATUS_UNMATCHED, STATUS_TIME_INVALID,
    STATUS_TOO_LONG, STATUS_REVIEW, STATUS_EMPTY, STATUS_LOCKED,
)
from app.dubbing.speech_rate import get_speech_budget


class SrtImportDialog(QDialog):
    """
    Interactive SRT Import Dialog with Preview Diff table and validation gates.
    """

    def __init__(self, project: Project, translation_engine=None, lang: str = "km", parent=None):
        super().__init__(parent)
        self.project = project
        self.translation_engine = translation_engine
        self.lang = lang

        self.parse_result: Optional[SrtParseResult] = None
        self.diffs: List[DiffItem] = []
        self.undo_snapshot: Optional[ImportSnapshot] = None
        self.changed_count: int = 0
        self.backup_path: Optional[str] = None

        self.setWindowTitle(
            "📥 នាំចូល SRT និងពិនិត្យភាពខុសគ្នា (Import SRT & Preview Diff)"
            if lang == "km" else
            "📥 Import SRT & Preview Diff"
        )
        from app.ui.theme import fit_dialog_to_screen
        fit_dialog_to_screen(self, 1000, 680)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # --- 1. File Selection ---
        row_file = QHBoxLayout()
        row_file.addWidget(QLabel("ឯកសារ SRT:" if self.lang == "km" else "SRT File:"))
        self.txt_file = QLineEdit()
        self.txt_file.setReadOnly(True)
        self.txt_file.setPlaceholderText("ជ្រើសរើសឯកសារ .srt (គាំទ្រ UTF-8 / UTF-8-BOM / UTF-16)...")
        row_file.addWidget(self.txt_file, stretch=1)

        btn_browse = QPushButton("📂 ជ្រើសឯកសារ .srt..." if self.lang == "km" else "📂 Browse .srt...")
        btn_browse.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 6px 12px;")
        btn_browse.clicked.connect(self._browse_srt)
        row_file.addWidget(btn_browse)
        layout.addLayout(row_file)

        # Encoding & Warnings Banner
        self.lbl_parse_info = QLabel("")
        self.lbl_parse_info.setWordWrap(True)
        self.lbl_parse_info.setVisible(False)
        layout.addWidget(self.lbl_parse_info)

        # --- 2. Import Options ---
        grp_opts = QGroupBox("ការកំណត់ការនាំចូល (Import Settings)" if self.lang == "km" else "Import Settings")
        grid = QGridLayout(grp_opts)
        grid.setSpacing(8)

        # Replace Target Mode
        grid.addWidget(QLabel("ជំនួសអត្ថបទក្នុងជួរ:" if self.lang == "km" else "Replace Target:"), 0, 0)
        row_target = QHBoxLayout()
        self.rb_target_km = QRadioButton("(ក) អត្ថបទខ្មែរ [Default]" if self.lang == "km" else "(a) Khmer Text [Default]")
        self.rb_target_src = QRadioButton("(ខ) អត្ថបទចិនដើម" if self.lang == "km" else "(b) Original Chinese Text")
        self.rb_target_km.setChecked(True)
        self.bg_target = QButtonGroup(self)
        self.bg_target.addButton(self.rb_target_km)
        self.bg_target.addButton(self.rb_target_src)
        self.rb_target_km.toggled.connect(self._recompute_diff)
        row_target.addWidget(self.rb_target_km)
        row_target.addWidget(self.rb_target_src)
        row_target.addStretch()
        grid.addLayout(row_target, 0, 1)

        # Match Mode
        grid.addWidget(QLabel("ការផ្គូផ្គង:" if self.lang == "km" else "Matching Mode:"), 1, 0)
        self.cb_match_mode = QComboBox()
        self.cb_match_mode.addItem("ស្វ័យប្រវត្តិ (Index បើស្មើ / Time បើខុសគ្នា)" if self.lang == "km" else "Auto (Index if equal else Time)", "auto")
        self.cb_match_mode.addItem("១) តាមលេខរៀង (Index)" if self.lang == "km" else "1) By Index", "index")
        self.cb_match_mode.addItem("២) តាមពេលវេលា (Overlap ≥ 50%)" if self.lang == "km" else "2) By Time Overlap (>= 50%)", "time")
        self.cb_match_mode.addItem("៣) តាមលំដាប់ ចាប់ពីបន្ទាត់ទី 1" if self.lang == "km" else "3) Sequential Order (from row 1)", "order")
        self.cb_match_mode.currentIndexChanged.connect(self._recompute_diff)
        grid.addWidget(self.cb_match_mode, 1, 1)

        # Checkboxes
        row_chks = QHBoxLayout()
        self.chk_use_times = QCheckBox("ប្រើពេលវេលា (start/end) ពី SRT ផងដែរ" if self.lang == "km" else "Also use timestamps (start/end) from SRT")
        self.chk_use_times.setChecked(False)
        self.chk_use_times.toggled.connect(self._recompute_diff)
        row_chks.addWidget(self.chk_use_times)

        self.chk_clear_tts = QCheckBox("លុបសំឡេង AI ចាស់ដែលត្រូវនឹងជួរដែលប្តូរ" if self.lang == "km" else "Clear old AI voice for changed rows")
        self.chk_clear_tts.setChecked(True)
        row_chks.addWidget(self.chk_clear_tts)
        row_chks.addStretch()
        grid.addLayout(row_chks, 2, 0, 1, 2)

        layout.addWidget(grp_opts)

        # --- 3. Diff Summary & Optional AI Condense ---
        row_summary = QHBoxLayout()
        self.lbl_diff_summary = QLabel("សូមជ្រើសរើសឯកសារ .srt ដើម្បីពិនិត្យភាពខុសគ្នា។" if self.lang == "km" else "Select an .srt file to preview diff.")
        self.lbl_diff_summary.setStyleSheet("font-weight: bold; color: #cbd5e1;")
        row_summary.addWidget(self.lbl_diff_summary, stretch=1)

        self.btn_condense_ai = QPushButton("✨ សង្ខេបជួរវែងពេកដោយ AI (ជម្រើស)" if self.lang == "km" else "✨ AI Condense Too-Long Rows")
        self.btn_condense_ai.setStyleSheet("background-color: #7e22ce; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_condense_ai.setEnabled(False)
        self.btn_condense_ai.clicked.connect(self._condense_too_long_rows)
        row_summary.addWidget(self.btn_condense_ai)
        layout.addLayout(row_summary)

        # --- 4. Preview Diff Table ---
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["ជួរ", "ចិនដើម", "ខ្មែរចាស់", "ខ្មែរថ្មី", "ស្ថានភាព"]
            if self.lang == "km" else
            ["Row", "Original", "Old Text", "New Text", "Status"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, stretch=1)

        # --- 5. Bottom Actions ---
        row_btns = QHBoxLayout()
        row_btns.addStretch()

        self.btn_cancel = QPushButton("បោះបង់" if self.lang == "km" else "Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        row_btns.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("✅ អនុវត្ត" if self.lang == "km" else "✅ Apply")
        self.btn_apply.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 7px 20px;")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply_import)
        row_btns.addWidget(self.btn_apply)

        layout.addLayout(row_btns)

    def _browse_srt(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "ជ្រើសរើសឯកសារ SRT ខ្មែរ",
            "",
            "SubRip Subtitle (*.srt);;All Files (*.*)"
        )
        if not file_path:
            return
        self.load_srt_file(file_path)

    def load_srt_file(self, file_path: str):
        res = parse_srt_file(file_path)
        if not res.is_valid:
            err_msg = "\n".join(res.errors + res.warnings) or "ឯកសារ SRT ខូច ឬមិនត្រឹមត្រូវ។"
            QMessageBox.critical(self, "កំហុសឯកសារ SRT", err_msg)
            return

        self.txt_file.setText(file_path)
        self.parse_result = res

        info_parts = [f"Encoding: {res.encoding_used} | ចំនួនជួរ SRT: {len(res.entries)} | ជួរក្នុង App: {len(self.project.segments)}"]
        if res.warnings:
            info_parts.append("⚠️ ការព្រមាន:\n" + "\n".join(res.warnings[:5]))
            if len(res.warnings) > 5:
                info_parts.append(f"... និង {len(res.warnings) - 5} ទៀត")
            self.lbl_parse_info.setStyleSheet("background-color: #422006; color: #fde047; padding: 6px; border-radius: 4px;")
        else:
            self.lbl_parse_info.setStyleSheet("background-color: #0f172a; color: #38bdf8; padding: 6px; border-radius: 4px;")
        self.lbl_parse_info.setText("\n".join(info_parts))
        self.lbl_parse_info.setVisible(True)

        self._recompute_diff()

    def _recompute_diff(self):
        if not self.parse_result or not self.parse_result.is_valid:
            return
        target = "km" if self.rb_target_km.isChecked() else "src"
        mode = self.cb_match_mode.currentData() or "auto"
        use_times = self.chk_use_times.isChecked()

        self.diffs = match_srt_to_segments(
            self.project.segments,
            self.parse_result.entries,
            mode=mode,
            replace_target=target,
            use_srt_times=use_times,
        )
        self._populate_diff_table()

    def _populate_diff_table(self):
        self.table.setRowCount(len(self.diffs))

        cnt_ok = cnt_new = cnt_rev = cnt_long = cnt_err = cnt_lock = 0

        for r, item in enumerate(self.diffs):
            row_label = f"#{item.seg_id}" if item.seg_index >= 0 else f"SRT #{item.seg_id}"
            it_row = QTableWidgetItem(row_label)
            it_row.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            it_src = QTableWidgetItem(item.src_text)
            it_old = QTableWidgetItem(item.old_text)
            it_new = QTableWidgetItem(item.new_text)

            status_txt = item.status_label
            if item.syllables > 0:
                status_txt += f" ({item.syll_per_sec:.1f}/វិ)"
            it_status = QTableWidgetItem(status_txt)
            if item.notes:
                it_status.setToolTip(item.notes)

            # Color coding
            bg_col = None
            fg_col = QColor("#e2e8f0")
            if item.status == STATUS_OK:
                cnt_ok += 1
                fg_col = QColor("#4ade80")
            elif item.status == STATUS_NEW:
                cnt_new += 1
                fg_col = QColor("#38bdf8")
            elif item.status in (STATUS_REVIEW, STATUS_EMPTY):
                cnt_rev += 1
                bg_col = QColor(113, 63, 18, 90)   # Yellow tint
                fg_col = QColor("#facc15")
            elif item.status == STATUS_TOO_LONG:
                cnt_long += 1
                bg_col = QColor(127, 29, 29, 100)  # Red tint
                fg_col = QColor("#f87171")
            elif item.status in (STATUS_UNMATCHED, STATUS_TIME_INVALID):
                cnt_err += 1
                bg_col = QColor(124, 45, 18, 90)
                fg_col = QColor("#fb923c")
            elif item.status == STATUS_LOCKED:
                cnt_lock += 1
                bg_col = QColor(30, 58, 138, 90)
                fg_col = QColor("#93c5fd")

            it_status.setForeground(fg_col)
            f_bold = QFont()
            f_bold.setBold(True)
            it_status.setFont(f_bold)

            for col_idx, cell in enumerate((it_row, it_src, it_old, it_new, it_status)):
                if bg_col:
                    cell.setBackground(bg_col)
                self.table.setItem(r, col_idx, cell)

        self.lbl_diff_summary.setText(
            f"សរុប {len(self.diffs)} | OK: {cnt_ok} | ថ្មី: {cnt_new} | ត្រូវពិនិត្យ: {cnt_rev} | វែងពេក: {cnt_long} | ជាប់សោ: {cnt_lock} | មិនផ្គូផ្គង/ខុស: {cnt_err}"
        )
        self.btn_apply.setEnabled(len(self.diffs) > 0)
        self.btn_condense_ai.setEnabled(cnt_long > 0 and self.translation_engine is not None)

    def _condense_too_long_rows(self):
        """Optional user-triggered AI condensation for TOO_LONG rows."""
        if not self.translation_engine:
            return
        too_long_items = [d for d in self.diffs if d.status == STATUS_TOO_LONG and d.seg_index >= 0]
        if not too_long_items:
            return

        condensed = 0
        for item in too_long_items:
            slot = max(0.3, (item.srt_end - item.srt_start) if (self.chk_use_times.isChecked() and item.srt_start is not None and item.srt_end is not None) else (item.end - item.start))
            _, max_syll = get_speech_budget(slot)
            try:
                shorter = self.translation_engine.condense_khmer_text(
                    khmer_text=item.new_text,
                    src_text=item.src_text,
                    max_syllables=max_syll,
                    target_lang="km",
                )
                if shorter and shorter.strip():
                    item.new_text = shorter.strip()
                    from app.dubbing.srt_io import _evaluate_speech_rate
                    sylls, rate, req_spd, still_long = _evaluate_speech_rate(item.new_text, slot)
                    item.syllables = sylls
                    item.syll_per_sec = rate
                    item.required_speed = req_spd
                    item.status = STATUS_TOO_LONG if still_long else STATUS_OK
                    item.notes = "បានសង្ខេបដោយ AI"
                    condensed += 1
            except Exception as e:
                print(f"[SRT Import] AI condense error on #{item.seg_id}: {e}")

        self._populate_diff_table()
        QMessageBox.information(self, "AI សង្ខេបរួចរាល់", f"បានសង្ខេបជួរវែងពេកចំនួន {condensed} ជួរ។")

    def _apply_import(self):
        if not self.diffs:
            return

        # Ask confirmation if any locked/manually edited rows will be overwritten
        locked_items = [d for d in self.diffs if d.status == STATUS_LOCKED]
        if locked_items:
            ids_str = ", ".join(f"#{d.seg_id}" for d in locked_items[:10])
            ans = QMessageBox.question(
                self,
                "បញ្ជាក់ការសរសេរជាន់ជួរជាប់សោ",
                f"មានជួរចំនួន {len(locked_items)} ({ids_str}) ដែលអ្នកបានកែ/ចាក់សោដោយដៃ។\n"
                "តើអ្នកចង់សរសេរជាន់ជួរទាំងនោះដែរឬទេ?\n(Yes = សរសេរជាន់ | No = រំលងជួរជាប់សោ)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            approve = (ans == QMessageBox.StandardButton.Yes)
            for d in locked_items:
                d.overwrite_approved = approve

        target = "km" if self.rb_target_km.isChecked() else "src"
        use_times = self.chk_use_times.isChecked()
        clear_tts = self.chk_clear_tts.isChecked()

        snap, changed, backup_path = apply_srt_diff(
            project=self.project,
            diffs=self.diffs,
            replace_target=target,
            use_srt_times=use_times,
            clear_changed_tts=clear_tts,
            backup_dir=DATA_DIR / "backups",
        )
        self.undo_snapshot = snap
        self.changed_count = changed
        self.backup_path = backup_path
        self.accept()
