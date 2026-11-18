"""
Settings Dialog: STT models, Translation API keys, TTS voices, Ducking, Font
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QSlider, QPushButton, QGroupBox, QSpinBox,
    QDoubleSpinBox
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from app.models import ProjectSettings
from app.config import WHISPER_MODELS, TRANSLATION_PROVIDERS, KHMER_VOICES, TARGET_LANGUAGES

class SettingsDialog(QDialog):
    def __init__(self, settings: ProjectSettings, lang: str = "km", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.lang = lang
        self.setWindowTitle("ការកំណត់ប្រព័ន្ធ (System Settings)" if lang == "km" else "System Settings")
        self.resize(520, 560)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 1. Speech-to-Text (STT) Group
        stt_box = QGroupBox("១. ស្ដាប់បំប្លែងសំឡេងជាអក្សរ (faster-whisper)" if self.lang == "km" else "1. Speech-to-Text (faster-whisper)")
        stt_layout = QVBoxLayout(stt_box)

        row_wh = QHBoxLayout()
        row_wh.addWidget(QLabel("ម៉ូដែល Whisper (Model):" if self.lang == "km" else "Whisper Model:"))
        self.cb_whisper = QComboBox()
        for m_id, m_label in WHISPER_MODELS:
            self.cb_whisper.addItem(m_label, m_id)
        w_idx = self.cb_whisper.findData(self.settings.whisper_model)
        if w_idx >= 0:
            self.cb_whisper.setCurrentIndex(w_idx)
        row_wh.addWidget(self.cb_whisper)
        stt_layout.addLayout(row_wh)
        layout.addWidget(stt_box)

        # 2. Translation & LLM APIs
        tr_box = QGroupBox("២. សេវាបកប្រែច្រើនភាសា (Multi-Language Translation / LLM)" if self.lang == "km" else "2. Multi-Language Translation / LLM APIs")
        tr_layout = QVBoxLayout(tr_box)

        row_tlang = QHBoxLayout()
        row_tlang.addWidget(QLabel("ភាសាគោលដៅ (Target Language):" if self.lang == "km" else "Target Language:"))
        self.cb_target_lang = QComboBox()
        for t_code, t_label, _, _, _ in TARGET_LANGUAGES:
            self.cb_target_lang.addItem(t_label, t_code)
        tl_idx = self.cb_target_lang.findData(getattr(self.settings, "target_language", "km") or "km")
        if tl_idx >= 0:
            self.cb_target_lang.setCurrentIndex(tl_idx)
        row_tlang.addWidget(self.cb_target_lang)
        tr_layout.addLayout(row_tlang)

        row_prov = QHBoxLayout()
        row_prov.addWidget(QLabel("អ្នកផ្គត់ផ្គង់ (Provider):" if self.lang == "km" else "Provider:"))
        self.cb_provider = QComboBox()
        for p_id, p_label in TRANSLATION_PROVIDERS:
            self.cb_provider.addItem(p_label, p_id)
        p_idx = self.cb_provider.findData(self.settings.translation_provider)
        if p_idx >= 0:
            self.cb_provider.setCurrentIndex(p_idx)
        row_prov.addWidget(self.cb_provider)
        tr_layout.addLayout(row_prov)

        # API Keys
        row_gemini = QHBoxLayout()
        row_gemini.addWidget(QLabel("Gemini API Key:"))
        self.txt_gemini = QLineEdit(self.settings.gemini_api_key)
        self.txt_gemini.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_gemini.setPlaceholderText("AQ... ឬ AIzaSy... (ឥតគិតថ្លៃ / Free)")
        row_gemini.addWidget(self.txt_gemini)

        btn_get_gemini = QPushButton("🔗 យក Key ឥតគិតថ្លៃ" if self.lang == "km" else "🔗 Get Free Key")
        btn_get_gemini.setStyleSheet("background-color: #047857; color: white; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold;")
        btn_get_gemini.setToolTip("បើកគេហទំព័រ Google AI Studio ដើម្បីយក Gemini API Key ឥតគិតថ្លៃ")
        btn_get_gemini.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://aistudio.google.com/app/apikey")))
        row_gemini.addWidget(btn_get_gemini)
        tr_layout.addLayout(row_gemini)

        row_claude = QHBoxLayout()
        row_claude.addWidget(QLabel("Claude API Key:"))
        self.txt_claude = QLineEdit(self.settings.claude_api_key)
        self.txt_claude.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_claude.setPlaceholderText("sk-ant-...")
        row_claude.addWidget(self.txt_claude)
        tr_layout.addLayout(row_claude)

        row_openai = QHBoxLayout()
        row_openai.addWidget(QLabel("OpenAI API Key:"))
        self.txt_openai = QLineEdit(self.settings.openai_api_key)
        self.txt_openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_openai.setPlaceholderText("sk-proj-...")
        row_openai.addWidget(self.txt_openai)
        tr_layout.addLayout(row_openai)

        layout.addWidget(tr_box)

        # 3. Audio Mixing & Ducking
        mix_box = QGroupBox("៣. ការលាយសំឡេង និង Ducking (Mixing & Ducking)" if self.lang == "km" else "3. Audio Mixing & Ducking")
        mix_layout = QVBoxLayout(mix_box)

        # Ducking dB
        row_duck = QHBoxLayout()
        row_duck.addWidget(QLabel("កម្រិតបញ្ចុះសំឡេងដើម (Ducking dB):" if self.lang == "km" else "Ducking Reduction (dB):"))
        self.spin_ducking = QSpinBox()
        self.spin_ducking.setRange(-40, 0)
        self.spin_ducking.setValue(int(self.settings.ducking_db))
        self.spin_ducking.setSuffix(" dB")
        row_duck.addWidget(self.spin_ducking)
        mix_layout.addLayout(row_duck)

        # AI Voice Speed
        row_speed = QHBoxLayout()
        row_speed.addWidget(QLabel("ល្បឿនសំឡេង AI (Voice Speed):" if self.lang == "km" else "AI Voice Speed:"))
        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.50, 2.00)
        self.spin_speed.setSingleStep(0.05)
        self.spin_speed.setDecimals(2)
        self.spin_speed.setSuffix("x")
        self.spin_speed.setValue(getattr(self.settings, "ai_voice_speed", 1.05))
        row_speed.addWidget(self.spin_speed)
        mix_layout.addLayout(row_speed)

        # Burn-in Subtitles
        self.chk_burn = QCheckBox("ដុតអក្សររត់ក្រោម (Subtitle) ជាប់លើវីដេអូនាំចេញ" if self.lang == "km" else "Burn-in Khmer subtitles into exported MP4")
        self.chk_burn.setChecked(self.settings.burn_subtitles)
        mix_layout.addWidget(self.chk_burn)

        # Font
        row_font = QHBoxLayout()
        row_font.addWidget(QLabel("ពុម្ពអក្សរ (Font):" if self.lang == "km" else "Subtitle Font:"))
        self.txt_font = QLineEdit(self.settings.subtitle_font)
        row_font.addWidget(self.txt_font)
        mix_layout.addLayout(row_font)

        layout.addWidget(mix_box)

        # 4. Version & Updates Group
        from app.config import APP_VERSION, APP_BUILD_DATE, DEFAULT_UPDATE_URL
        ver_box = QGroupBox("៤. កំណែ និងការអាប់ដេត (Version & Updates)" if self.lang == "km" else "4. Version & Updates")
        ver_layout = QVBoxLayout(ver_box)

        row_ver = QHBoxLayout()
        lbl_v_info = QLabel(f"កំណែបច្ចុប្បន្ន (Current Version): <b>v{APP_VERSION}</b> ({APP_BUILD_DATE})")
        lbl_v_info.setStyleSheet("color: #38bdf8;")
        row_ver.addWidget(lbl_v_info)
        row_ver.addStretch()

        btn_check_now = QPushButton("🔄 ពិនិត្យមើលឥឡូវនេះ" if self.lang == "km" else "Check Now")
        btn_check_now.setStyleSheet("background-color: #0d9488; color: white; font-weight: bold; padding: 4px 10px;")
        btn_check_now.clicked.connect(self._check_updates_now)
        row_ver.addWidget(btn_check_now)
        ver_layout.addLayout(row_ver)

        self.chk_auto_update = QCheckBox("ពិនិត្យមើលកំណែថ្មីស្វ័យប្រវត្តិតាមអ៊ីនធឺណិតពេលបើកកម្មវិធី" if self.lang == "km" else "Automatically check for updates on startup")
        self.chk_auto_update.setChecked(getattr(self.settings, "auto_check_updates", True))
        ver_layout.addWidget(self.chk_auto_update)

        row_url = QHBoxLayout()
        row_url.addWidget(QLabel("Update URL (Optional):"))
        self.txt_update_url = QLineEdit(getattr(self.settings, "update_check_url", "") or "")
        self.txt_update_url.setPlaceholderText(DEFAULT_UPDATE_URL)
        row_url.addWidget(self.txt_update_url)
        ver_layout.addLayout(row_url)

        layout.addWidget(ver_box)

        # Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        btn_save = QPushButton("រក្សាទុក (Save)" if self.lang == "km" else "Save")
        btn_save.setStyleSheet("background-color: #0284c7; color: white;")
        btn_save.clicked.connect(self._save_settings)
        btn_bar.addWidget(btn_save)

        btn_cancel = QPushButton("បោះបង់ (Cancel)" if self.lang == "km" else "Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(btn_cancel)

        layout.addLayout(btn_bar)

    def _check_updates_now(self):
        from app.ui.update_dialog import UpdateDialog
        url = self.txt_update_url.text().strip() or None
        dlg = UpdateDialog(manifest_url=url, lang=self.lang, parent=self)
        dlg.exec()

    def _save_settings(self):
        self.settings.whisper_model = self.cb_whisper.currentData()
        self.settings.target_language = self.cb_target_lang.currentData() or "km"
        self.settings.translation_provider = self.cb_provider.currentData()
        self.settings.gemini_api_key = self.txt_gemini.text().strip()
        self.settings.claude_api_key = self.txt_claude.text().strip()
        self.settings.openai_api_key = self.txt_openai.text().strip()
        self.settings.ducking_db = float(self.spin_ducking.value())
        self.settings.ai_voice_speed = float(self.spin_speed.value())
        self.settings.burn_subtitles = self.chk_burn.isChecked()
        self.settings.subtitle_font = self.txt_font.text().strip() or "Kantumruy Pro"
        self.settings.auto_check_updates = self.chk_auto_update.isChecked()
        self.settings.update_check_url = self.txt_update_url.text().strip()
        self.accept()
