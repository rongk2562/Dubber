"""
Refactored System Settings Dialog (Sidebar + Stacked Pages):
Pages: General | OCR / ASR | Translation | Audio | Notifications | Version & Advanced
Includes Password show/hide toggles, API key test buttons, Whisper trade-offs, and hidden Advanced URL.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QGroupBox, QSpinBox,
    QDoubleSpinBox, QListWidget, QStackedWidget, QWidget,
    QFormLayout, QMessageBox, QScrollArea, QFrame, QPlainTextEdit
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from app.models import ProjectSettings
from app.config import (
    WHISPER_MODELS, TRANSLATION_PROVIDERS, TARGET_LANGUAGES,
    APP_VERSION, APP_BUILD_DATE, DEFAULT_UPDATE_URL
)
from app.ui.theme import ToggleSwitch, fit_dialog_to_screen


def _has_cuda_gpu() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


class SettingsDialog(QDialog):
    def __init__(self, settings: ProjectSettings, lang: str = "km", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.lang = lang
        self.setWindowTitle("ការកំណត់ប្រព័ន្ធ" if lang == "km" else "System Settings")
        fit_dialog_to_screen(self, 780, 560)
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(14)

        # Sidebar Navigation
        self.sidebar = QListWidget()
        self.sidebar.setMinimumWidth(145)
        self.sidebar.setMaximumWidth(205)
        tabs_km = [
            "⚙️ ទូទៅ",
            "👁️ OCR / ASR",
            "🌐 បកប្រែ & API",
            "💬 OpenAI / ChatGPT",
            "🎛️ សំឡេង & អក្សរ",
            "🔔 ការជូនដំណឹង",
            "🔄 កំណែ & Advanced",
        ]
        tabs_en = [
            "⚙️ General",
            "👁️ OCR / ASR",
            "🌐 Translation & API",
            "💬 OpenAI / ChatGPT",
            "🎛️ Audio & Subtitles",
            "🔔 Notifications",
            "🔄 Version & Advanced",
        ]
        self.sidebar.addItems(tabs_km if self.lang == "km" else tabs_en)
        body.addWidget(self.sidebar)

        # Stacked Pages inside QScrollArea so all pages scroll smoothly on small screens
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_page_general())
        self.stack.addWidget(self._build_page_asr_ocr())
        self.stack.addWidget(self._build_page_translation())
        self.stack.addWidget(self._build_page_openai())
        self.stack.addWidget(self._build_page_audio())
        self.stack.addWidget(self._build_page_notifications())
        self.stack.addWidget(self._build_page_version_advanced())

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        stack_scroll = QScrollArea()
        stack_scroll.setWidgetResizable(True)
        stack_scroll.setFrameShape(QFrame.Shape.NoFrame)
        stack_scroll.setWidget(self.stack)
        body.addWidget(stack_scroll, stretch=1)
        root.addLayout(body, stretch=1)

        # Bottom Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        btn_cancel = QPushButton("បោះបង់" if self.lang == "km" else "Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(btn_cancel)

        btn_save = QPushButton("💾 រក្សាទុក" if self.lang == "km" else "💾 Save")
        btn_save.setProperty("variant", "primary")
        btn_save.clicked.connect(self._save_settings)
        btn_bar.addWidget(btn_save)
        root.addLayout(btn_bar)

    def _build_page_general(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        grp = QGroupBox("ភាសា និងទិដ្ឋភាពទូទៅ" if self.lang == "km" else "Language & Appearance")
        form = QFormLayout(grp)

        self.cb_ui_lang = QComboBox()
        self.cb_ui_lang.addItem("🇰🇭 ភាសាខ្មែរ", "km")
        self.cb_ui_lang.addItem("🇺🇸 English", "en")
        idx = self.cb_ui_lang.findData(getattr(self.settings, "ui_language", self.lang))
        if idx >= 0:
            self.cb_ui_lang.setCurrentIndex(idx)
        form.addRow("ភាសាកម្មវិធី:" if self.lang == "km" else "Interface Language:", self.cb_ui_lang)

        self.txt_font = QLineEdit(self.settings.subtitle_font)
        form.addRow("ពុម្ពអក្សរ Subtitle:" if self.lang == "km" else "Subtitle Font:", self.txt_font)

        lay.addWidget(grp)
        lay.addStretch()
        return page

    def _build_page_asr_ocr(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        # Whisper Section with Hardware Recommendation & Trade-off table
        grp_wh = QGroupBox("ម៉ាស៊ីនស្ដាប់សំឡេង (Whisper)" if self.lang == "km" else "Speech Recognition (Whisper)")
        v_wh = QVBoxLayout(grp_wh)

        has_gpu = _has_cuda_gpu()
        rec_model = "medium / small (CUDA GPU)" if has_gpu else "base (CPU)"
        lbl_hw = QLabel(
            f"💡 ណែនាំសម្រាប់ម៉ាស៊ីននេះ៖ <b>{rec_model}</b>"
            if self.lang == "km" else
            f"💡 Recommended for this PC: <b>{rec_model}</b>"
        )
        lbl_hw.setStyleSheet("color: #22C55E; font-size: 9pt;")
        v_wh.addWidget(lbl_hw)

        row_wh = QHBoxLayout()
        row_wh.addWidget(QLabel("ម៉ូដែល:" if self.lang == "km" else "Model:"))
        self.cb_whisper = QComboBox()
        tradeoffs = {
            "tiny": "Tiny — លឿនបំផុត (~400MB RAM) | ភាពត្រឹមត្រូវទាប",
            "base": "Base — លឿន & សមរម្យ (~1GB RAM) [ណែនាំ CPU]",
            "small": "Small — ត្រឹមត្រូវល្អ (~2GB RAM) | យឺតមធ្យម",
            "medium": "Medium — ត្រឹមត្រូវខ្ពស់ (~5GB VRAM) [ណែនាំ GPU]",
            "large-v3": "Large-v3 — ត្រឹមត្រូវខ្ពស់បំផុត (~8GB VRAM) | យឺត",
        }
        for m_id, m_label in WHISPER_MODELS:
            self.cb_whisper.addItem(tradeoffs.get(m_id, m_label), m_id)
        w_idx = self.cb_whisper.findData(self.settings.whisper_model)
        if w_idx >= 0:
            self.cb_whisper.setCurrentIndex(w_idx)
        row_wh.addWidget(self.cb_whisper, stretch=1)
        v_wh.addLayout(row_wh)
        lay.addWidget(grp_wh)

        # Video OCR Section
        grp_ocr = QGroupBox("អានអក្សរចិនក្នុងវីដេអូ (Video OCR)" if self.lang == "km" else "Video Hardsub OCR")
        form_ocr = QFormLayout(grp_ocr)

        self.cb_src_mode = QComboBox()
        self.cb_src_mode.addItem("Auto (OCR បើមានអក្សរក្នុងវីដេអូ, មិនអញ្ចឹង Whisper)" if self.lang == "km" else "Auto (OCR if hardsubs, else Whisper)", "auto")
        self.cb_src_mode.addItem("OCR អក្សរក្នុងវីដេអូ" if self.lang == "km" else "Video OCR Only", "ocr")
        self.cb_src_mode.addItem("Whisper (ស្ដាប់សំឡេង)" if self.lang == "km" else "Whisper Audio Only", "whisper")
        self.cb_src_mode.addItem("💬 ChatGPT / OpenAI (Transcription)", "openai")
        sm_idx = self.cb_src_mode.findData(getattr(self.settings, "src_mode", "auto"))
        if sm_idx >= 0:
            self.cb_src_mode.setCurrentIndex(sm_idx)
        form_ocr.addRow("ប្រភពអត្ថបទដើម:" if self.lang == "km" else "Source Mode:", self.cb_src_mode)

        self.cb_ocr_eng = QComboBox()
        self.cb_ocr_eng.addItem("RapidOCR (ONNX — លឿន & ត្រឹមត្រូវ)", "rapidocr")
        self.cb_ocr_eng.addItem("PaddleOCR (ជម្រើស)", "paddleocr")
        oe_idx = self.cb_ocr_eng.findData(getattr(self.settings, "ocr_engine", "rapidocr"))
        if oe_idx >= 0:
            self.cb_ocr_eng.setCurrentIndex(oe_idx)
        form_ocr.addRow("OCR Engine:", self.cb_ocr_eng)

        self.spin_ocr_fps = QDoubleSpinBox()
        self.spin_ocr_fps.setRange(2.0, 10.0)
        self.spin_ocr_fps.setSingleStep(1.0)
        self.spin_ocr_fps.setValue(float(getattr(self.settings, "ocr_fps", 5.0)))
        form_ocr.addRow("ល្បឿនស្កេន (FPS):" if self.lang == "km" else "Scan FPS:", self.spin_ocr_fps)

        self.chk_ocr_ts = ToggleSwitch("ទទួលពេលវេលា OCR" if self.lang == "km" else "Use OCR timestamps")
        self.chk_ocr_ts.setChecked(bool(getattr(self.settings, "use_ocr_timestamps", True)))
        form_ocr.addRow("", self.chk_ocr_ts)

        lay.addWidget(grp_ocr)
        lay.addStretch()
        return page

    def _make_api_key_row(self, value: str, placeholder: str, provider_tag: str):
        row = QHBoxLayout()
        txt = QLineEdit(value)
        txt.setEchoMode(QLineEdit.EchoMode.Password)
        txt.setPlaceholderText(placeholder)
        row.addWidget(txt, stretch=1)

        btn_eye = QPushButton("👁")
        btn_eye.setMinimumWidth(32)
        btn_eye.setCheckable(True)
        btn_eye.setToolTip("បង្ហាញ / លាក់ Key" if self.lang == "km" else "Show / Hide Key")
        btn_eye.toggled.connect(lambda checked, le=txt: le.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        row.addWidget(btn_eye)

        btn_test = QPushButton("សាកល្បង Key" if self.lang == "km" else "Test Key")
        btn_test.clicked.connect(lambda checked=False, le=txt, p=provider_tag: self._test_api_key(p, le.text().strip()))
        row.addWidget(btn_test)
        return row, txt

    def _test_api_key(self, provider: str, key: str):
        if not key:
            QMessageBox.warning(self, "API Key", "សូមបញ្ចូល API Key ជាមុនសិន!" if self.lang == "km" else "Please enter an API key first.")
            return
        try:
            import httpx
            if provider == "gemini":
                r = httpx.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
                    timeout=6.0
                )
                ok = (r.status_code == 200)
            elif provider == "openai":
                r = httpx.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=6.0
                )
                ok = (r.status_code == 200)
            elif provider == "claude":
                r = httpx.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    timeout=6.0
                )
                ok = (r.status_code == 200)
            else:
                ok = False

            if ok:
                QMessageBox.information(self, "ជោគជ័យ", f"✅ {provider.upper()} API Key ដំណើរការត្រឹមត្រូវ!")
            else:
                QMessageBox.warning(self, "បរាជ័យ", f"❌ {provider.upper()} API Key មិនត្រឹមត្រូវ ឬអស់ quota (HTTP {r.status_code})។")
        except Exception as e:
            QMessageBox.warning(self, "បរាជ័យ", f"❌ មិនអាចតភ្ជាប់សាកល្បង Key បានទេ៖\n{e}")

    def _build_page_translation(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        grp = QGroupBox("ការបកប្រែ និង API Keys" if self.lang == "km" else "Translation Engine & API Keys")
        form = QFormLayout(grp)

        self.cb_target_lang = QComboBox()
        for t_code, t_label, _, _, _ in TARGET_LANGUAGES:
            self.cb_target_lang.addItem(t_label, t_code)
        tl_idx = self.cb_target_lang.findData(getattr(self.settings, "target_language", "km") or "km")
        if tl_idx >= 0:
            self.cb_target_lang.setCurrentIndex(tl_idx)
        form.addRow("ភាសាគោលដៅ:" if self.lang == "km" else "Target Language:", self.cb_target_lang)

        self.cb_provider = QComboBox()
        prov_labels = {
            "gemini": "Google Gemini AI — [ណែនាំ: គុណភាពខ្ពស់]",
            "claude": "Anthropic Claude — [ណែនាំ: គុណភាពខ្ពស់]",
            "openai": "OpenAI GPT — [ណែនាំ: គុណភាពខ្ពស់]",
            "google": "Google Translate Free — [លឿន តែគុណភាពទាប]",
            "local_ai": "Local AI Context Translator",
        }
        for p_id, p_label in TRANSLATION_PROVIDERS:
            self.cb_provider.addItem(prov_labels.get(p_id, p_label), p_id)
        p_idx = self.cb_provider.findData(self.settings.translation_provider)
        if p_idx >= 0:
            self.cb_provider.setCurrentIndex(p_idx)
        form.addRow("ម៉ាស៊ីនបកប្រែ:" if self.lang == "km" else "Provider:", self.cb_provider)

        row_gem, self.txt_gemini = self._make_api_key_row(self.settings.gemini_api_key, "AIzaSy... / AQ...", "gemini")
        btn_get_gem = QPushButton("🔗 យក Key")
        btn_get_gem.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://aistudio.google.com/app/apikey")))
        row_gem.addWidget(btn_get_gem)
        form.addRow("Gemini API Key:", row_gem)

        row_cl, self.txt_claude = self._make_api_key_row(self.settings.claude_api_key, "sk-ant-...", "claude")
        form.addRow("Claude API Key:", row_cl)

        from app.secrets import get_openai_api_key
        saved_oa_key = (self.settings.openai_api_key or "").strip() or get_openai_api_key()
        row_oa, self.txt_openai = self._make_api_key_row(saved_oa_key, "sk-proj-...", "openai")
        form.addRow("OpenAI API Key:", row_oa)

        lay.addWidget(grp)
        lay.addStretch()
        return page

    def _build_page_openai(self) -> QWidget:
        from app.secrets import get_openai_api_key
        from app.openai_client import DEFAULT_OPENAI_BASE_URL, DEFAULT_STT_MODELS, DEFAULT_CHAT_MODELS

        page = QWidget()
        lay = QVBoxLayout(page)
        grp = QGroupBox("OpenAI / ChatGPT (សំឡេងជាអក្សរ & បកប្រែ SRT)" if self.lang == "km" else "OpenAI / ChatGPT")
        form = QFormLayout(grp)

        saved_key = (self.settings.openai_api_key or "").strip() or get_openai_api_key()
        row_key = QHBoxLayout()
        self.txt_oa_sec_key = QLineEdit(saved_key)
        self.txt_oa_sec_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_oa_sec_key.setPlaceholderText("sk-proj-... (រក្សាទុកក្នុង OS Credential Store)")
        # Keep in sync with Translation page field
        if hasattr(self, "txt_openai"):
            self.txt_oa_sec_key.textChanged.connect(lambda t: self.txt_openai.setText(t) if self.txt_openai.text() != t else None)
            self.txt_openai.textChanged.connect(lambda t: self.txt_oa_sec_key.setText(t) if self.txt_oa_sec_key.text() != t else None)
        row_key.addWidget(self.txt_oa_sec_key, stretch=1)

        btn_eye_oa = QPushButton("👁")
        btn_eye_oa.setMinimumWidth(32)
        btn_eye_oa.setCheckable(True)
        btn_eye_oa.setToolTip("បង្ហាញ / លាក់ Key" if self.lang == "km" else "Show / Hide Key")
        btn_eye_oa.toggled.connect(
            lambda checked: self.txt_oa_sec_key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        )
        row_key.addWidget(btn_eye_oa)

        btn_conn = QPushButton("🔗 សាកល្បងការតភ្ជាប់" if self.lang == "km" else "🔗 Test Connection")
        btn_conn.setProperty("variant", "primary")
        btn_conn.clicked.connect(self._test_openai_and_refresh_models)
        row_key.addWidget(btn_conn)
        form.addRow("OpenAI API Key:", row_key)

        self.txt_oa_base_url = QLineEdit(getattr(self.settings, "openai_base_url", DEFAULT_OPENAI_BASE_URL) or DEFAULT_OPENAI_BASE_URL)
        self.txt_oa_base_url.setPlaceholderText(DEFAULT_OPENAI_BASE_URL)
        form.addRow("Base URL (ជម្រើស Proxy):" if self.lang == "km" else "Base URL:", self.txt_oa_base_url)

        self.cb_oa_stt_model = QComboBox()
        self.cb_oa_stt_model.setEditable(True)
        for m in DEFAULT_STT_MODELS:
            self.cb_oa_stt_model.addItem(m)
        self.cb_oa_stt_model.setCurrentText(getattr(self.settings, "openai_stt_model", "whisper-1") or "whisper-1")
        form.addRow("Model ដកស្រង់សំឡេង:" if self.lang == "km" else "Transcription Model:", self.cb_oa_stt_model)

        self.cb_oa_trans_model = QComboBox()
        self.cb_oa_trans_model.setEditable(True)
        for m in DEFAULT_CHAT_MODELS:
            self.cb_oa_trans_model.addItem(m)
        self.cb_oa_trans_model.setCurrentText(getattr(self.settings, "openai_trans_model", "gpt-4o-mini") or "gpt-4o-mini")
        form.addRow("Model បកប្រែ:" if self.lang == "km" else "Translation Model:", self.cb_oa_trans_model)

        self.spin_oa_conc = QSpinBox()
        self.spin_oa_conc.setRange(1, 6)
        self.spin_oa_conc.setValue(int(getattr(self.settings, "openai_concurrency", 2) or 2))
        form.addRow("Concurrency (Batch ព្រមគ្នា):" if self.lang == "km" else "Concurrency:", self.spin_oa_conc)

        self.chk_oa_fallback = ToggleSwitch(
            "Fallback ទៅ Google Translate បើ ChatGPT បរាជ័យ" if self.lang == "km" else "Fallback to Google Translate on failure"
        )
        self.chk_oa_fallback.setChecked(bool(getattr(self.settings, "openai_fallback_google", True)))
        form.addRow("", self.chk_oa_fallback)

        self.txt_oa_prompt = QPlainTextEdit(getattr(self.settings, "openai_custom_prompt", "") or "")
        self.txt_oa_prompt.setPlaceholderText(
            "បន្ថែមរចនាប័ទ្មបកប្រែផ្ទាល់ខ្លួន (ឧ. ប្រើពាក្យសុភាពក្នុងរឿងបុរាណ ឬពាក្យយុវវ័យ)..."
            if self.lang == "km" else "Optional custom translation prompt instructions..."
        )
        self.txt_oa_prompt.setMaximumHeight(90)
        form.addRow("Prompt បន្ថែម (ជម្រើស):" if self.lang == "km" else "Custom Prompt:", self.txt_oa_prompt)

        lbl_sec = QLabel(
            "🔒 API Key ត្រូវបានរក្សាទុកដោយសុវត្ថិភាពតាម OS Credential Store (Keyring / Windows DPAPI) មិនរក្សាទុកជាអក្សរធម្មតាទេ។"
            if self.lang == "km" else
            "🔒 Stored securely via OS Credential Store (never saved in plaintext)."
        )
        lbl_sec.setWordWrap(True)
        lbl_sec.setStyleSheet("color: #34D399; font-size: 8.5pt;")
        form.addRow("", lbl_sec)

        lay.addWidget(grp)
        lay.addStretch()
        return page

    def _test_openai_and_refresh_models(self):
        from app.openai_client import OpenAIClient
        key = self.txt_oa_sec_key.text().strip()
        base_url = self.txt_oa_base_url.text().strip()
        client = OpenAIClient(self.settings)
        ok, msg, ids = client.test_connection(api_key=key, base_url=base_url)
        if ok:
            stt_list, chat_list = OpenAIClient.categorize_models(ids)
            cur_stt = self.cb_oa_stt_model.currentText().strip()
            cur_chat = self.cb_oa_trans_model.currentText().strip()
            self.cb_oa_stt_model.clear()
            self.cb_oa_stt_model.addItems(stt_list)
            self.cb_oa_stt_model.setCurrentText(cur_stt or stt_list[0])
            self.cb_oa_trans_model.clear()
            self.cb_oa_trans_model.addItems(chat_list)
            self.cb_oa_trans_model.setCurrentText(cur_chat or chat_list[0])
            QMessageBox.information(self, "ជោគជ័យ (OpenAI)", f"✅ {msg}")
        else:
            QMessageBox.warning(self, "បរាជ័យ (OpenAI)", f"❌ {msg}")

    def _build_page_audio(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        grp = QGroupBox("ការលាយសំឡេង និងការបង្ហាញអក្សរ" if self.lang == "km" else "Audio Mixing & Subtitles")
        form = QFormLayout(grp)

        self.spin_ducking = QSpinBox()
        self.spin_ducking.setRange(-40, 0)
        self.spin_ducking.setValue(int(self.settings.ducking_db))
        self.spin_ducking.setSuffix(" dB")
        self.spin_ducking.setToolTip("បន្ថយសំឡេងដើមពេល AI កំពុងនិយាយ (សមកាលកម្មជាមួយ Panel សំឡេង)" if self.lang == "km" else "Ducking attenuation during AI speech")
        form.addRow("បន្ថយសំឡេងដើម (Ducking):" if self.lang == "km" else "Ducking dB:", self.spin_ducking)

        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.50, 2.00)
        self.spin_speed.setSingleStep(0.05)
        self.spin_speed.setDecimals(2)
        self.spin_speed.setSuffix("x")
        self.spin_speed.setValue(getattr(self.settings, "ai_voice_speed", 1.05))
        self.spin_speed.setToolTip("សមកាលកម្មជាមួយប៊ូតុងល្បឿនលើ Panel សំឡេង" if self.lang == "km" else "Synced with Audio Panel speed control")
        form.addRow("ល្បឿនសំឡេង AI:" if self.lang == "km" else "AI Voice Speed:", self.spin_speed)

        self.chk_burn = ToggleSwitch("ដុតអក្សរខ្មែរលើវីដេអូ Export" if self.lang == "km" else "Burn-in subtitles on export")
        self.chk_burn.setChecked(self.settings.burn_subtitles)
        form.addRow("", self.chk_burn)

        lay.addWidget(grp)
        lay.addStretch()
        return page

    def _build_page_notifications(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        grp = QGroupBox("ការជូនដំណឹង" if self.lang == "km" else "Notifications")
        v = QVBoxLayout(grp)

        self.chk_notify_enabled = ToggleSwitch("ទទួលដំណឹងពី developer" if self.lang == "km" else "Receive developer announcements")
        self.chk_notify_enabled.setChecked(getattr(self.settings, "notify_enabled", True))
        v.addWidget(self.chk_notify_enabled)

        self.chk_notify_toast = ToggleSwitch("បង្ហាញ Toast ក្នុងកម្មវិធី" if self.lang == "km" else "Show in-app toast")
        self.chk_notify_toast.setChecked(getattr(self.settings, "notify_show_toast", True))
        v.addWidget(self.chk_notify_toast)

        self.chk_notify_tray = ToggleSwitch("Windows tray notification")
        self.chk_notify_tray.setChecked(getattr(self.settings, "notify_windows_tray", True))
        v.addWidget(self.chk_notify_tray)

        self.chk_notify_sound = ToggleSwitch("សំឡេងជូនដំណឹងពេលចប់" if self.lang == "km" else "Play completion sound")
        self.chk_notify_sound.setChecked(getattr(self.settings, "notify_sound", True))
        v.addWidget(self.chk_notify_sound)

        row_poll = QHBoxLayout()
        row_poll.addWidget(QLabel("ចន្លោះពេលពិនិត្យ:" if self.lang == "km" else "Check interval:"))
        self.cb_notify_interval = QComboBox()
        for hrs in (1, 6, 24):
            self.cb_notify_interval.addItem(f"{hrs} ម៉ោង" if self.lang == "km" else f"{hrs} hours", hrs)
        cur_hrs = int(getattr(self.settings, "notify_interval_hours", 6))
        h_idx = self.cb_notify_interval.findData(cur_hrs)
        if h_idx >= 0:
            self.cb_notify_interval.setCurrentIndex(h_idx)
        row_poll.addWidget(self.cb_notify_interval)
        row_poll.addStretch()

        btn_check_notices = QPushButton("🔔 ពិនិត្យឥឡូវនេះ" if self.lang == "km" else "🔔 Check Now")
        btn_check_notices.setProperty("variant", "primary")
        btn_check_notices.clicked.connect(self._check_notices_now)
        row_poll.addWidget(btn_check_notices)
        v.addLayout(row_poll)

        lay.addWidget(grp)
        lay.addStretch()
        return page

    def _build_page_version_advanced(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        grp_ver = QGroupBox("កំណែកម្មវិធី" if self.lang == "km" else "Application Version")
        v = QVBoxLayout(grp_ver)

        row_v = QHBoxLayout()
        lbl_v = QLabel(f"កំណែបច្ចុប្បន្ន៖ <b>v{APP_VERSION}</b> ({APP_BUILD_DATE})" if self.lang == "km" else f"Current Version: <b>v{APP_VERSION}</b> ({APP_BUILD_DATE})")
        row_v.addWidget(lbl_v)
        row_v.addStretch()
        btn_upd = QPushButton("🔄 ពិនិត្យកំណែថ្មី" if self.lang == "km" else "🔄 Check Updates")
        btn_upd.clicked.connect(self._check_updates_now)
        row_v.addWidget(btn_upd)
        v.addLayout(row_v)

        self.chk_auto_update = ToggleSwitch("ពិនិត្យកំណែថ្មីស្វ័យប្រវត្តិពេលបើកកម្មវិធី" if self.lang == "km" else "Auto-check updates on startup")
        self.chk_auto_update.setChecked(getattr(self.settings, "auto_check_updates", True))
        v.addWidget(self.chk_auto_update)
        lay.addWidget(grp_ver)

        # Hidden-by-default Advanced section for Update URL
        self.btn_toggle_adv = QPushButton("▸ Advanced" if self.lang == "en" else "▸ កំណត់កម្រិតខ្ពស់ (Advanced)")
        self.btn_toggle_adv.setCheckable(True)
        self.btn_toggle_adv.setFlat(True)
        self.btn_toggle_adv.setStyleSheet("text-align: left; color: #9AA8C4; font-weight: 600;")
        lay.addWidget(self.btn_toggle_adv)

        self.box_advanced = QGroupBox("Advanced")
        adv_form = QFormLayout(self.box_advanced)
        self.txt_update_url = QLineEdit(getattr(self.settings, "update_check_url", "") or "")
        self.txt_update_url.setPlaceholderText(DEFAULT_UPDATE_URL)
        adv_form.addRow("Custom Update URL:", self.txt_update_url)
        self.box_advanced.setVisible(False)
        self.btn_toggle_adv.toggled.connect(self.box_advanced.setVisible)
        lay.addWidget(self.box_advanced)

        lay.addStretch()
        return page

    def _check_updates_now(self):
        from app.ui.update_dialog import UpdateDialog
        url = self.txt_update_url.text().strip() or None
        dlg = UpdateDialog(manifest_url=url, lang=self.lang, parent=self)
        dlg.exec()

    def _check_notices_now(self):
        from app.notifications import NotificationManager
        NotificationManager.instance().poll_remote_async(force=True)
        QMessageBox.information(self, "ពិនិត្យដំណឹង", "កំពុងពិនិត្យមើលដំណឹងថ្មីពី Developer ក្នុង background!")

    def _save_settings(self):
        self.settings.ui_language = self.cb_ui_lang.currentData() or self.lang
        self.settings.whisper_model = self.cb_whisper.currentData()
        self.settings.src_mode = self.cb_src_mode.currentData() or "auto"
        self.settings.ocr_engine = self.cb_ocr_eng.currentData() or "rapidocr"
        self.settings.ocr_fps = float(self.spin_ocr_fps.value())
        self.settings.use_ocr_timestamps = self.chk_ocr_ts.isChecked()

        self.settings.target_language = self.cb_target_lang.currentData() or "km"
        self.settings.translation_provider = self.cb_provider.currentData()
        self.settings.gemini_api_key = self.txt_gemini.text().strip()
        self.settings.claude_api_key = self.txt_claude.text().strip()
        oa_key = self.txt_oa_sec_key.text().strip() if hasattr(self, "txt_oa_sec_key") else self.txt_openai.text().strip()
        self.settings.openai_api_key = oa_key
        from app.secrets import set_openai_api_key
        set_openai_api_key(oa_key)
        if hasattr(self, "txt_oa_base_url"):
            self.settings.openai_base_url = self.txt_oa_base_url.text().strip() or "https://api.openai.com/v1"
        if hasattr(self, "cb_oa_stt_model"):
            self.settings.openai_stt_model = self.cb_oa_stt_model.currentText().strip() or "whisper-1"
        if hasattr(self, "cb_oa_trans_model"):
            self.settings.openai_trans_model = self.cb_oa_trans_model.currentText().strip() or "gpt-4o-mini"
        if hasattr(self, "spin_oa_conc"):
            self.settings.openai_concurrency = int(self.spin_oa_conc.value())
        if hasattr(self, "chk_oa_fallback"):
            self.settings.openai_fallback_google = self.chk_oa_fallback.isChecked()
        if hasattr(self, "txt_oa_prompt"):
            self.settings.openai_custom_prompt = self.txt_oa_prompt.toPlainText().strip()

        self.settings.ducking_db = float(self.spin_ducking.value())
        self.settings.ai_voice_speed = float(self.spin_speed.value())
        self.settings.burn_subtitles = self.chk_burn.isChecked()
        self.settings.subtitle_font = self.txt_font.text().strip() or "Noto Sans Khmer"

        self.settings.auto_check_updates = self.chk_auto_update.isChecked()
        self.settings.update_check_url = self.txt_update_url.text().strip()

        self.settings.notify_enabled = self.chk_notify_enabled.isChecked()
        self.settings.notify_show_toast = self.chk_notify_toast.isChecked()
        self.settings.notify_windows_tray = self.chk_notify_tray.isChecked()
        self.settings.notify_sound = self.chk_notify_sound.isChecked()
        self.settings.notify_interval_hours = int(self.cb_notify_interval.currentData() or 6)

        from app.notifications import NotificationManager
        NotificationManager.instance().configure(
            enabled=self.settings.notify_enabled,
            show_toast=self.settings.notify_show_toast,
            tray_enabled=self.settings.notify_windows_tray,
            interval_hours=self.settings.notify_interval_hours,
            sound_enabled=self.settings.notify_sound,
            lang=self.settings.ui_language,
        )
        self.accept()
