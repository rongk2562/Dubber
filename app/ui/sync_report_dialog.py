"""
Time-Sync Report Dialog
SRS Section 4: 'Provide a sync report that flags segments exceeding their slot so the user can fix them'
"""

from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QGroupBox, QRadioButton, QButtonGroup
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from app.sync_engine import SyncReport, SyncReportItem

class SyncReportDialog(QDialog):
    jump_to_segment = Signal(int) # segment_id

    def __init__(self, report: SyncReport, lang: str = "km", parent=None):
        super().__init__(parent)
        self.report = report
        self.lang = lang
        self.setWindowTitle("របាយការណ៍តម្រឹមពេលវេលា (Time-Sync Report)" if lang == "km" else "Time-Sync Report")
        self.resize(800, 500)
        self._init_ui()
        self._populate("all")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Summary Metrics Cards
        metrics_box = QGroupBox("សង្ខេបលទ្ធផលតម្រឹមពេលវេលា (4-Stage Sync Summary)" if self.lang == "km" else "4-Stage Time-Sync Summary")
        m_layout = QHBoxLayout(metrics_box)
        m_layout.setSpacing(12)

        def make_stat_card(title: str, val: int, color_hex: str):
            box = QVBoxLayout()
            lbl_val = QLabel(str(val))
            lbl_val.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {color_hex};")
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_title = QLabel(title)
            lbl_title.setStyleSheet("font-size: 11px; color: #94a3b8;")
            lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(lbl_val)
            box.addWidget(lbl_title)
            return box

        m_layout.addLayout(make_stat_card("សរុបវគ្គ (Total)", self.report.total_segments, "#38bdf8"))
        m_layout.addLayout(make_stat_card("សមរម្យ (Fits OK)", self.report.ok_count, "#34d399"))
        m_layout.addLayout(make_stat_card("ពន្លឿនល្បឿន (Speed)", self.report.speed_adjusted_count, "#60a5fa"))
        m_layout.addLayout(make_stat_card("សង្ខេបអត្ថបទ (Shortened)", self.report.shortened_count, "#c084fc"))
        m_layout.addLayout(make_stat_card("ខ្ចីចន្លោះ (Borrowed)", self.report.gap_borrowed_count, "#fde047"))
        m_layout.addLayout(make_stat_card("បង្រួម (Atempo)", self.report.time_stretched_count, "#fdba74"))
        m_layout.addLayout(make_stat_card("ហួសកម្រិត (Overflow)", self.report.overflow_count, "#f43f5e"))

        layout.addWidget(metrics_box)

        # Filter Bar
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("បង្ហាញ (Filter):" if self.lang == "km" else "Filter:"))

        self.btn_filter_all = QRadioButton("ទាំងអស់ (All)" if self.lang == "km" else "All")
        self.btn_filter_all.setChecked(True)
        self.btn_filter_all.toggled.connect(lambda: self._populate("all") if self.btn_filter_all.isChecked() else None)
        filter_bar.addWidget(self.btn_filter_all)

        self.btn_filter_overflow = QRadioButton("ហួសកម្រិតតែប៉ុណ្ណោះ (Overflow Only)" if self.lang == "km" else "Overflow Only")
        self.btn_filter_overflow.setStyleSheet("color: #f87171; font-weight: bold;")
        self.btn_filter_overflow.toggled.connect(lambda: self._populate("overflow") if self.btn_filter_overflow.isChecked() else None)
        filter_bar.addWidget(self.btn_filter_overflow)

        self.btn_filter_adjusted = QRadioButton("វគ្គមានការកែសម្រួល (Adjusted Only)" if self.lang == "km" else "Adjusted Only")
        self.btn_filter_adjusted.toggled.connect(lambda: self._populate("adjusted") if self.btn_filter_adjusted.isChecked() else None)
        filter_bar.addWidget(self.btn_filter_adjusted)

        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        headers = [
            "ល.រ (#)", "ថិរវេលា Slot (s)", "សំឡេងដើម (s)", "សំឡេងចុងក្រោយ (s)",
            "ចន្លោះខ្ចី (s)", "ល្បឿន", "ស្ថានភាព", "សកម្មភាព"
        ] if self.lang == "km" else [
            "ID", "Slot (s)", "Orig TTS (s)", "Final TTS (s)",
            "Borrowed (s)", "Speed", "Status", "Action"
        ]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Bottom Bar
        b_bar = QHBoxLayout()
        b_bar.addStretch()
        btn_close = QPushButton("បិទ (Close)" if self.lang == "km" else "Close")
        btn_close.clicked.connect(self.accept)
        b_bar.addWidget(btn_close)
        layout.addLayout(b_bar)

    def _populate(self, filter_mode: str):
        self.table.setRowCount(0)

        items = self.report.items
        if filter_mode == "overflow":
            items = [it for it in items if it.is_overflow]
        elif filter_mode == "adjusted":
            items = [it for it in items if it.sync_status != "OK"]

        self.table.setRowCount(len(items))

        for row, it in enumerate(items):
            # ID
            item_id = QTableWidgetItem(str(it.segment_id))
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_id)

            # Slot
            item_slot = QTableWidgetItem(f"{it.slot_sec:.2f}")
            item_slot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item_slot)

            # Orig TTS
            item_orig = QTableWidgetItem(f"{it.original_tts_sec:.2f}")
            item_orig.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, item_orig)

            # Final TTS
            item_fin = QTableWidgetItem(f"{it.final_tts_sec:.2f}")
            item_fin.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if it.is_overflow:
                item_fin.setForeground(QColor("#f43f5e"))
            self.table.setItem(row, 3, item_fin)

            # Borrowed
            item_bor = QTableWidgetItem(f"{it.borrowed_gap_sec:.2f}" if it.borrowed_gap_sec > 0 else "-")
            item_bor.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, item_bor)

            # Speed
            item_spd = QTableWidgetItem(f"{it.speed_factor:.2f}x")
            item_spd.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, item_spd)

            # Status
            item_stat = QTableWidgetItem(it.sync_status)
            item_stat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if it.is_overflow:
                item_stat.setBackground(QColor("#881337"))
                item_stat.setForeground(QColor("#f43f5e"))
            elif it.sync_status == "OK":
                item_stat.setForeground(QColor("#34d399"))
            else:
                item_stat.setForeground(QColor("#fbbf24"))
            self.table.setItem(row, 6, item_stat)

            # Action: Jump to Segment
            btn_jump = QPushButton("🔍 ពិនិត្យ (Jump)" if self.lang == "km" else "Jump")
            btn_jump.clicked.connect(lambda checked, sid=it.segment_id: self._on_jump(sid))
            self.table.setCellWidget(row, 7, btn_jump)

    def _on_jump(self, segment_id: int):
        self.jump_to_segment.emit(segment_id)
        self.accept()
