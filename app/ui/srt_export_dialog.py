"""
Export Chinese SRT Dialog for External Translation (ChatGPT / Claude / Gemini).
Includes chunked export option (40-60 lines/file) and customizable ChatGPT prompt copy.
"""

from pathlib import Path
from typing import List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QSpinBox, QTextEdit, QFileDialog, QMessageBox,
    QApplication, QGroupBox
)
from PySide6.QtCore import Qt

from app.models import Project
from app.dubbing.srt_io import export_chinese_srt_for_chatgpt, build_chatgpt_prompt


class SrtExportForChatGPTDialog(QDialog):
    """
    Dialog to export Chinese SRT files matching app timestamps/indices,
    optionally split into chunks, and copy a ready-to-use ChatGPT dubbing prompt.
    """

    def __init__(self, project: Project, lang: str = "km", parent=None):
        super().__init__(parent)
        self.project = project
        self.lang = lang
        self.exported_files: List[str] = []

        self.setWindowTitle(
            "📤 Export SRT ចិន (សម្រាប់បកប្រែក្នុង ChatGPT)"
            if lang == "km" else
            "📤 Export Chinese SRT for ChatGPT Translation"
        )
        self.resize(740, 560)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        lbl_info = QLabel(
            f"សរុបមាន {len(self.project.segments)} ជួរ។ លេខរៀង និងពេលវេលាត្រូវបានរក្សាទុកដូចគ្នានឹងក្នុងកម្មវិធី ដើម្បីងាយស្រួលនាំចូល SRT ខ្មែរមកវិញ។"
            if self.lang == "km" else
            f"Total {len(self.project.segments)} segments. Indices and timestamps match the app for clean re-import."
        )
        lbl_info.setStyleSheet("color: #94a3b8; font-size: 12px;")
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        # Group 1: Chunking Options
        grp_chunk = QGroupBox("១. ជម្រើសបែងចែកឯកសារ (Chunking)" if self.lang == "km" else "1. Export Options")
        lay_chunk = QHBoxLayout(grp_chunk)

        self.chk_chunk = QCheckBox(
            "Export ជាបំណែកៗ (ការពារកុំឱ្យលើសកម្រិត ChatGPT)"
            if self.lang == "km" else
            "Export in chunks (avoid ChatGPT length limits)"
        )
        self.chk_chunk.setChecked(len(self.project.segments) > 60)
        self.chk_chunk.toggled.connect(lambda on: self.spin_chunk.setEnabled(on))
        lay_chunk.addWidget(self.chk_chunk)

        lay_chunk.addStretch()
        lay_chunk.addWidget(QLabel("ចំនួនបន្ទាត់/ឯកសារ:" if self.lang == "km" else "Lines/file:"))

        self.spin_chunk = QSpinBox()
        self.spin_chunk.setRange(20, 200)
        self.spin_chunk.setValue(50)
        self.spin_chunk.setEnabled(self.chk_chunk.isChecked())
        lay_chunk.addWidget(self.spin_chunk)
        layout.addWidget(grp_chunk)

        # Group 2: ChatGPT Prompt Template
        grp_prompt = QGroupBox(
            "២. Prompt សម្រាប់ ChatGPT (អាចកែសម្រួលបាន)"
            if self.lang == "km" else
            "2. ChatGPT Prompt Template (Editable)"
        )
        lay_prompt = QVBoxLayout(grp_prompt)

        row_ctx = QHBoxLayout()
        row_ctx.addWidget(QLabel("Context រឿង:" if self.lang == "km" else "Story Context:"))
        self.txt_summary = QTextEdit()
        self.txt_summary.setMaximumHeight(48)
        self.txt_summary.setPlaceholderText("ឧ. រឿងភាគគ្រួសារ តួឯកឈ្មោះ គាំងហុង និង ប៉ោសូវ...")
        row_ctx.addWidget(self.txt_summary, stretch=1)

        btn_refresh = QPushButton("🔄 ធ្វើបច្ចុប្បន្នភាព Prompt")
        btn_refresh.clicked.connect(self._refresh_prompt)
        row_ctx.addWidget(btn_refresh)
        lay_prompt.addLayout(row_ctx)

        self.txt_prompt = QTextEdit()
        self.txt_prompt.setStyleSheet("font-family: 'Khmer OS Siemreap', 'Noto Sans Khmer', sans-serif; font-size: 12px;")
        lay_prompt.addWidget(self.txt_prompt, stretch=1)

        btn_copy_prompt = QPushButton("📋 ចម្លង prompt សម្រាប់ ChatGPT" if self.lang == "km" else "📋 Copy ChatGPT Prompt")
        btn_copy_prompt.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 6px 12px;")
        btn_copy_prompt.clicked.connect(self._copy_prompt)
        lay_prompt.addWidget(btn_copy_prompt)

        layout.addWidget(grp_prompt, stretch=1)

        self._refresh_prompt()

        # Bottom Actions
        row_btns = QHBoxLayout()
        row_btns.addStretch()

        btn_close = QPushButton("បិទ" if self.lang == "km" else "Close")
        btn_close.clicked.connect(self.reject)
        row_btns.addWidget(btn_close)

        btn_export = QPushButton("💾 Export SRT ចិន (UTF-8)" if self.lang == "km" else "💾 Export Chinese SRT")
        btn_export.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 7px 16px;")
        btn_export.clicked.connect(self._do_export)
        row_btns.addWidget(btn_export)

        layout.addLayout(row_btns)

    def _refresh_prompt(self):
        summary = self.txt_summary.toPlainText().strip()
        prompt = build_chatgpt_prompt(self.project.glossary, summary=summary)
        self.txt_prompt.setPlainText(prompt)

    def _copy_prompt(self):
        cb = QApplication.clipboard()
        cb.setText(self.txt_prompt.toPlainText())
        QMessageBox.information(
            self,
            "បានចម្លង (Copied)",
            "បានចម្លង Prompt សម្រាប់ ChatGPT រួចរាល់! សូម Paste ទៅក្នុង ChatGPT រួមជាមួយឯកសារ SRT ចិន។"
        )

    def _do_export(self):
        if not self.project.segments:
            QMessageBox.warning(self, "គ្មានទិន្នន័យ", "មិនទាន់មានជួរអត្ថបទនៅឡើយទេ!")
            return

        default_name = f"{Path(self.project.name or 'chinese_source').stem}_zh.srt"
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "រក្សាទុកឯកសារ SRT ចិន",
            default_name,
            "SubRip Subtitle (*.srt)"
        )
        if not out_path:
            return

        chunk = self.spin_chunk.value() if self.chk_chunk.isChecked() else 0
        files = export_chinese_srt_for_chatgpt(self.project.segments, out_path, chunk_size=chunk)
        self.exported_files = files
        msg = "\n".join(f"• {f}" for f in files)
        QMessageBox.information(
            self,
            "Export ជោគជ័យ",
            f"បានរក្សាទុកឯកសារ SRT ចិន ({len(files)} ឯកសារ) ដោយជោគជ័យ៖\n\n{msg}"
        )
        self.accept()
