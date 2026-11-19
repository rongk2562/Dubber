"""
Speaker Support and Voice Assignment Dialog / Panel
F8: Separate speakers (speaker 1, 2...) and assign different voices
"""

from typing import Dict, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QComboBox, QLineEdit, QLabel, QHeaderView, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from app.models import Project, Speaker
from app.config import KHMER_VOICES
from app.ui.theme import fit_dialog_to_screen

class SpeakerManagerDialog(QDialog):
    speakers_updated = Signal()

    def __init__(self, project: Project, lang: str = "km", parent=None):
        super().__init__(parent)
        self.project = project
        self.lang = lang
        self.setWindowTitle("ការកំណត់សំឡេងតាមតួអង្គ (Speaker Voice Assignment)" if lang == "km" else "Speaker Voice Assignment")
        fit_dialog_to_screen(self, 550, 380)
        self._init_ui()
        self._populate()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(
            "កំណត់សំឡេង AI ខ្មែរ (ប្រុស ឬ ស្រី) សម្រាប់តួអង្គនីមួយៗ (S1, S2...)៖"
            if self.lang == "km" else
            "Assign Khmer AI voices (Male or Female) to each detected speaker (S1, S2...):"
        )
        desc.setStyleSheet("color: #94a3b8;")
        layout.addWidget(desc)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        headers = ["Speaker ID", "Character Name", "Assigned Khmer Voice", "Gender"] if self.lang == "en" else ["សម្គាល់", "ឈ្មោះតួអង្គ", "សំឡេង AI ខ្មែរ", "ភេទ"]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Actions
        btn_bar = QHBoxLayout()
        self.btn_add = QPushButton("➕ បន្ថែមតួអង្គ (Add Speaker)" if self.lang == "km" else "➕ Add Speaker")
        self.btn_add.clicked.connect(self._add_speaker)
        btn_bar.addWidget(self.btn_add)

        self.btn_apply = QPushButton("អនុវត្តលើគ្រប់វគ្គ (Apply to Segments)" if self.lang == "km" else "Apply to All Segments")
        self.btn_apply.setStyleSheet("background-color: #0284c7; color: white;")
        self.btn_apply.clicked.connect(self._apply_to_segments)
        btn_bar.addWidget(self.btn_apply)

        btn_bar.addStretch()

        self.btn_close = QPushButton("បិទ (Close)" if self.lang == "km" else "Close")
        self.btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_close)

        layout.addLayout(btn_bar)

    def _populate(self):
        # Auto-detect speakers from segments if not yet in project.speakers
        found_speakers = set(s.speaker for s in self.project.segments if s.speaker)
        if not found_speakers:
            found_speakers = {"S1", "S2"}

        for spk_id in sorted(found_speakers):
            if spk_id not in self.project.speakers:
                # Alternate male/female defaults
                idx = len(self.project.speakers)
                default_voice = "km-KH-PisethNeural" if idx % 2 == 0 else "km-KH-SreymomNeural"
                self.project.speakers[spk_id] = Speaker(
                    id=spk_id,
                    name=f"Speaker {spk_id}",
                    voice=default_voice,
                    gender="Male" if "Piseth" in default_voice else "Female"
                )

        self.table.setRowCount(len(self.project.speakers))
        for row, (spk_id, spk) in enumerate(sorted(self.project.speakers.items())):
            # 0: ID
            item_id = QTableWidgetItem(spk.id)
            item_id.setFlags(item_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item_id)

            # 1: Name
            item_name = QTableWidgetItem(spk.name)
            self.table.setItem(row, 1, item_name)

            # 2: Voice
            cb_voice = QComboBox()
            for v_id, v_data in KHMER_VOICES.items():
                cb_voice.addItem(v_data["name"], v_id)
            v_idx = cb_voice.findData(spk.voice)
            if v_idx >= 0:
                cb_voice.setCurrentIndex(v_idx)
            cb_voice.currentIndexChanged.connect(lambda idx, s=spk, cb=cb_voice: self._update_spk_voice(s, cb.itemData(idx)))
            self.table.setCellWidget(row, 2, cb_voice)

            # 3: Gender
            item_gender = QTableWidgetItem(spk.gender)
            item_gender.setFlags(item_gender.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, item_gender)

    def _update_spk_voice(self, spk: Speaker, voice_id: str):
        spk.voice = voice_id
        spk.gender = "Male" if "Piseth" in voice_id else "Female"

    def _add_speaker(self):
        new_id = f"S{len(self.project.speakers) + 1}"
        self.project.speakers[new_id] = Speaker(
            id=new_id,
            name=f"Speaker {new_id}",
            voice="km-KH-PisethNeural"
        )
        self._populate()

    def _apply_to_segments(self):
        """Update voice on every segment matching each speaker."""
        # Save names first
        for row in range(self.table.rowCount()):
            spk_id = self.table.item(row, 0).text()
            name = self.table.item(row, 1).text()
            if spk_id in self.project.speakers:
                self.project.speakers[spk_id].name = name

        applied_count = 0
        for seg in self.project.segments:
            if seg.speaker in self.project.speakers:
                seg.voice = self.project.speakers[seg.speaker].voice
                applied_count += 1

        self.speakers_updated.emit()
        msg = f"បានអនុវត្តសំឡេងលើ {applied_count} វគ្គរួចរាល់!" if self.lang == "km" else f"Successfully assigned voices to {applied_count} segments!"
        QMessageBox.information(self, "Success", msg)
