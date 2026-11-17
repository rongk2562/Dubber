"""
Batch Processing Queue Dialog
F11: Process multiple movies, progress bar, cancel
"""

import os
from typing import List, Dict, Optional
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QProgressBar, QFileDialog, QHeaderView, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal
from app.models import Project
from app.audio_engine import AudioEngine
from app.stt_engine import STTEngine
from app.translation_engine import TranslationEngine
from app.tts_engine import TTSEngine
from app.sync_engine import SyncEngine
from app.export_engine import ExportEngine

class BatchWorker(QThread):
    item_progress = Signal(int, int, str) # row, pct, status
    batch_finished = Signal()

    def __init__(self, items: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.items = items
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        audio_engine = AudioEngine()
        tts_engine = TTSEngine()
        trans_engine = TranslationEngine()
        sync_engine = SyncEngine(audio_engine, tts_engine, trans_engine)
        export_engine = ExportEngine(audio_engine)

        for row, item in enumerate(self.items):
            if self.is_cancelled:
                self.item_progress.emit(row, 0, "Cancelled")
                break

            video_path = item["video_path"]
            output_mp4 = item["output_path"]

            try:
                # 1. Audio Extract
                self.item_progress.emit(row, 10, "Extracting audio...")
                extracted_wav = audio_engine.extract_audio(video_path)

                if self.is_cancelled: break

                # 2. STT
                self.item_progress.emit(row, 30, "Transcribing...")
                stt = STTEngine(model_size="base")
                segments = stt.transcribe(extracted_wav)

                if self.is_cancelled: break

                # 3. Translate
                self.item_progress.emit(row, 50, "Translating to Khmer...")
                for s in segments:
                    if self.is_cancelled: break
                    s.text_km = trans_engine.translate_segment(s.text_src)

                if self.is_cancelled: break

                # 4. TTS
                self.item_progress.emit(row, 70, "Generating Khmer AI voices...")
                for s in segments:
                    if self.is_cancelled: break
                    tts_engine.generate_segment_tts(s)

                if self.is_cancelled: break

                # 5. Time Sync
                self.item_progress.emit(row, 85, "Time-syncing...")
                proj = Project(video_path=video_path, segments=segments)
                sync_engine.synchronize_segments(proj)

                if self.is_cancelled: break

                # 6. Export
                self.item_progress.emit(row, 95, "Exporting dubbed MP4...")
                export_engine.export_all(proj, output_mp4)

                self.item_progress.emit(row, 100, "Completed ✅")

            except Exception as e:
                self.item_progress.emit(row, 0, f"Error: {str(e)[:30]}")

        self.batch_finished.emit()


class BatchQueueDialog(QDialog):
    def __init__(self, lang: str = "km", parent=None):
        super().__init__(parent)
        self.lang = lang
        self.worker: Optional[BatchWorker] = None
        self.setWindowTitle("ជួរដំណើរការវីដេអូច្រើន (Batch Movie Queue)" if lang == "km" else "Batch Processing Queue")
        self.resize(750, 480)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Top Buttons
        top_bar = QHBoxLayout()
        self.btn_add = QPushButton("➕ បន្ថែមវីដេអូច្រើន (Add Movies)" if self.lang == "km" else "➕ Add Movies")
        self.btn_add.clicked.connect(self._add_files)
        top_bar.addWidget(self.btn_add)

        self.btn_clear = QPushButton("សម្អាត (Clear List)" if self.lang == "km" else "Clear List")
        self.btn_clear.clicked.connect(self._clear_list)
        top_bar.addWidget(self.btn_clear)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        headers = ["ឈ្មោះវីដេអូ", "វីដេអូនាំចេញ", "វឌ្ឍនភាព", "ស្ថានភាព"] if self.lang == "km" else ["Movie Title", "Output Path", "Progress", "Status"]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Bottom Bar
        b_bar = QHBoxLayout()

        self.btn_start = QPushButton("🚀 ចាប់ផ្ដើមដំណើរការ (Start Queue)" if self.lang == "km" else "🚀 Start Queue")
        self.btn_start.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 8px 16px;")
        self.btn_start.clicked.connect(self._start_batch)
        b_bar.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("⏹️ បោះបង់ (Cancel)" if self.lang == "km" else "⏹️ Cancel")
        self.btn_cancel.setStyleSheet("background-color: #dc2626; color: white;")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_batch)
        b_bar.addWidget(self.btn_cancel)

        b_bar.addStretch()

        btn_close = QPushButton("បិទ (Close)" if self.lang == "km" else "Close")
        btn_close.clicked.connect(self.accept)
        b_bar.addWidget(btn_close)

        layout.addLayout(b_bar)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "ជ្រើសរើសវីដេអូ (Select Videos)",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.mov);;All Files (*.*)"
        )
        if not files:
            return

        for f in files:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Name
            self.table.setItem(row, 0, QTableWidgetItem(Path(f).name))

            # Out
            out_file = str(Path(f).parent / f"{Path(f).stem}_dubbed_khmer.mp4")
            self.table.setItem(row, 1, QTableWidgetItem(out_file))

            # Progress
            pbar = QProgressBar()
            pbar.setValue(0)
            self.table.setCellWidget(row, 2, pbar)

            # Status
            self.table.setItem(row, 3, QTableWidgetItem("រង់ចាំ (Queued)" if self.lang == "km" else "Queued"))

    def _clear_list(self):
        if not self.worker or not self.worker.isRunning():
            self.table.setRowCount(0)

    def _start_batch(self):
        if self.table.rowCount() == 0:
            return

        items = []
        for r in range(self.table.rowCount()):
            video_path = self.table.item(r, 0).text()
            out_path = self.table.item(r, 1).text()
            items.append({"video_path": video_path, "output_path": out_path})

        self.btn_start.setEnabled(False)
        self.btn_add.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self.worker = BatchWorker(items)
        self.worker.item_progress.connect(self._on_item_progress)
        self.worker.batch_finished.connect(self._on_batch_finished)
        self.worker.start()

    def _on_item_progress(self, row: int, pct: int, status: str):
        pbar = self.table.cellWidget(row, 2)
        if pbar:
            pbar.setValue(pct)
        item_status = self.table.item(row, 3)
        if item_status:
            item_status.setText(status)

    def _cancel_batch(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)

    def _on_batch_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        QMessageBox.information(self, "Completed", "ដំណើរការជាបាច់បានបញ្ចប់!" if self.lang == "km" else "Batch processing finished!")
