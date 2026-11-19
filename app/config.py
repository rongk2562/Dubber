"""
Application Configuration and Internationalization (Khmer & English)
AI Movie Translator & Auto Dubbing App
"""

import os
import sys
from pathlib import Path

# Determine Frozen (.exe) vs Development Paths
IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    EXE_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", EXE_DIR))
    APP_ROOT = EXE_DIR
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    EXE_DIR = APP_ROOT
    BUNDLE_DIR = APP_ROOT


def get_resource_path(relative_path: str) -> Path:
    """Resolve bundled or workspace resource file path safely in both dev and .exe modes."""
    for base in (APP_ROOT, BUNDLE_DIR, EXE_DIR):
        candidate = base / relative_path
        if candidate.exists():
            return candidate
    return APP_ROOT / relative_path


def _setup_bundled_ffmpeg():
    """Ensure bundled ffmpeg.exe & ffprobe.exe are on PATH when running on another PC."""
    candidate_dirs = [
        EXE_DIR / "ffmpeg_bin",
        BUNDLE_DIR / "ffmpeg_bin",
        EXE_DIR,
        BUNDLE_DIR,
        Path(r"C:\ffmpeg\bin"),
    ]
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    for d in reversed(candidate_dirs):
        if d.exists() and (d / "ffmpeg.exe").exists():
            d_str = str(d)
            if d_str not in path_parts:
                path_parts.insert(0, d_str)
    os.environ["PATH"] = os.pathsep.join(path_parts)


_setup_bundled_ffmpeg()

# Writable user/project directories (fallback to LOCALAPPDATA if EXE_DIR is read-only)
def _init_writable_dirs(base_dir: Path):
    data_d = base_dir / "data"
    temp_d = base_dir / "temp"
    proj_d = base_dir / "projects"
    for d in (data_d, temp_d, proj_d):
        d.mkdir(parents=True, exist_ok=True)
    test_file = temp_d / ".write_test"
    test_file.write_text("ok", encoding="utf-8")
    test_file.unlink(missing_ok=True)
    return data_d, temp_d, proj_d


try:
    DATA_DIR, TEMP_DIR, PROJECTS_DIR = _init_writable_dirs(APP_ROOT)
except Exception:
    fallback_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KhmerAIDubStudio"
    DATA_DIR, TEMP_DIR, PROJECTS_DIR = _init_writable_dirs(fallback_root)

DEFAULT_DB_PATH = DATA_DIR / "app_data.db"

# Supported Target Languages for Multi-Language Translation & Single Male AI Voice Dubbing
TARGET_LANGUAGES = [
    ("km", "🇰🇭 ខ្មែរ (Khmer)", "Khmer (ភាសាខ្មែរ)", "km-KH-PisethNeural", "km-KH-PisethNeural"),
    ("en", "🇺🇸 អង់គ្លេស (English)", "English", "en-US-AndrewMultilingualNeural", "en-US-AndrewMultilingualNeural"),
    ("zh-CN", "🇨🇳 ចិន (Chinese)", "Simplified Chinese (中文)", "zh-CN-YunxiNeural", "zh-CN-YunxiNeural"),
    ("th", "🇹🇭 ថៃ (Thai)", "Thai (ภาษาไทย)", "th-TH-NiwatNeural", "th-TH-NiwatNeural"),
    ("vi", "🇻🇳 វៀតណាម (Vietnamese)", "Vietnamese (Tiếng Việt)", "vi-VN-NamMinhNeural", "vi-VN-NamMinhNeural"),
    ("ja", "🇯🇵 ជប៉ុន (Japanese)", "Japanese (日本語)", "ja-JP-KeitaNeural", "ja-JP-KeitaNeural"),
    ("ko", "🇰🇷 កូរ៉េ (Korean)", "Korean (한국어)", "ko-KR-InJoonNeural", "ko-KR-InJoonNeural"),
    ("fr", "🇫🇷 បារាំង (French)", "French (Français)", "fr-FR-HenriNeural", "fr-FR-HenriNeural"),
    ("es", "🇪🇸 អេស្ប៉ាញ (Spanish)", "Spanish (Español)", "es-ES-AlvaroNeural", "es-ES-AlvaroNeural"),
]

TARGET_LANG_MAP = {
    code: {
        "code": code,
        "label": label,
        "prompt_name": prompt_name,
        "male_voice": male_v,
        "female_voice": male_v,
    }
    for code, label, prompt_name, male_v, _ in TARGET_LANGUAGES
}

# Single Male Neural Voice Per Supported Language (Microsoft Edge TTS)
KHMER_VOICES = {
    # Khmer (km-KH) — Single Male Voice: Piseth
    "km-KH-PisethNeural": {
        "name": "Piseth (ខ្មែរ - ប្រុស / Male)",
        "actor": "Piseth",
        "gender": "Male",
        "short_name": "km-KH-PisethNeural",
        "locale": "km-KH",
        "lang": "km",
    },
    # English (en-US) — Single Male Voice: Andrew
    "en-US-AndrewMultilingualNeural": {
        "name": "Andrew (English - Male)",
        "actor": "Andrew",
        "gender": "Male",
        "short_name": "en-US-AndrewMultilingualNeural",
        "locale": "en-US",
        "lang": "en",
    },
    # Chinese Mandarin (zh-CN) — Single Male Voice: Yunxi
    "zh-CN-YunxiNeural": {
        "name": "Yunxi (Chinese - Male)",
        "actor": "Yunxi",
        "gender": "Male",
        "short_name": "zh-CN-YunxiNeural",
        "locale": "zh-CN",
        "lang": "zh-CN",
    },
    # Thai (th-TH) — Single Male Voice: Niwat
    "th-TH-NiwatNeural": {
        "name": "Niwat (Thai - Male)",
        "actor": "Niwat",
        "gender": "Male",
        "short_name": "th-TH-NiwatNeural",
        "locale": "th-TH",
        "lang": "th",
    },
    # Vietnamese (vi-VN) — Single Male Voice: NamMinh
    "vi-VN-NamMinhNeural": {
        "name": "NamMinh (Vietnamese - Male)",
        "actor": "NamMinh",
        "gender": "Male",
        "short_name": "vi-VN-NamMinhNeural",
        "locale": "vi-VN",
        "lang": "vi",
    },
    # Japanese (ja-JP) — Single Male Voice: Keita
    "ja-JP-KeitaNeural": {
        "name": "Keita (Japanese - Male)",
        "actor": "Keita",
        "gender": "Male",
        "short_name": "ja-JP-KeitaNeural",
        "locale": "ja-JP",
        "lang": "ja",
    },
    # Korean (ko-KR) — Single Male Voice: InJoon
    "ko-KR-InJoonNeural": {
        "name": "InJoon (Korean - Male)",
        "actor": "InJoon",
        "gender": "Male",
        "short_name": "ko-KR-InJoonNeural",
        "locale": "ko-KR",
        "lang": "ko",
    },
    # French (fr-FR) — Single Male Voice: Henri
    "fr-FR-HenriNeural": {
        "name": "Henri (French - Male)",
        "actor": "Henri",
        "gender": "Male",
        "short_name": "fr-FR-HenriNeural",
        "locale": "fr-FR",
        "lang": "fr",
    },
    # Spanish (es-ES) — Single Male Voice: Alvaro
    "es-ES-AlvaroNeural": {
        "name": "Alvaro (Spanish - Male)",
        "actor": "Alvaro",
        "gender": "Male",
        "short_name": "es-ES-AlvaroNeural",
        "locale": "es-ES",
        "lang": "es",
    },
}


def get_voice_pair_for_lang(lang_code: str = "km"):
    """Return (male_voice_id, male_voice_id) — strictly locked to Single Male Voice."""
    info = TARGET_LANG_MAP.get(lang_code) or TARGET_LANG_MAP["km"]
    v = info["male_voice"]
    return v, v


def normalize_to_male_voice(voice_id: str, fallback_lang: str = "km") -> str:
    """Convert any legacy or female voice_id to the corresponding language's Single Male Voice."""
    if voice_id in KHMER_VOICES:
        return voice_id
    if voice_id:
        prefix = voice_id[:5].lower()
        prefix_map = {
            "km-kh": "km-KH-PisethNeural",
            "en-us": "en-US-AndrewMultilingualNeural",
            "zh-cn": "zh-CN-YunxiNeural",
            "th-th": "th-TH-NiwatNeural",
            "vi-vn": "vi-VN-NamMinhNeural",
            "ja-jp": "ja-JP-KeitaNeural",
            "ko-kr": "ko-KR-InJoonNeural",
            "fr-fr": "fr-FR-HenriNeural",
            "es-es": "es-ES-AlvaroNeural",
        }
        if prefix in prefix_map:
            return prefix_map[prefix]
    male_v, _ = get_voice_pair_for_lang(fallback_lang)
    return male_v


def is_female_voice(voice_id: str) -> bool:
    """Always False — Single Male Voice mode only."""
    return False


def get_voice_actor_name(voice_id: str) -> str:
    """Return short Male actor name (e.g. Piseth, Andrew, Yunxi) for UI display."""
    norm_id = normalize_to_male_voice(voice_id)
    v_meta = KHMER_VOICES.get(norm_id)
    if v_meta and "actor" in v_meta:
        return v_meta["actor"]
    return "Piseth"

# Translation Providers
TRANSLATION_PROVIDERS = [
    ("google", "Google Translate (Free / Web - លំនាំដើមចាស់ មិនបាច់ប្រើ Key)"),
    ("gemini", "Google Gemini 1.5 Flash (AI Studio - ណែនាំសម្រាប់ភាពយន្ត)"),
    ("claude", "Anthropic Claude (API)"),
    ("openai", "OpenAI GPT-4o (API)"),
    ("offline", "Manual / Offline Mode"),
]

# Whisper Models
WHISPER_MODELS = [
    ("tiny", "Tiny (Fastest, Low Memory ~1GB)"),
    ("base", "Base (Balanced, Fast ~1GB)"),
    ("small", "Small (Good Accuracy ~2GB)"),
    ("medium", "Medium (High Accuracy ~5GB)"),
    ("large-v3", "Large v3 (Best Accuracy ~10GB)"),
]

# Time-Sync Constraints (From SRS Specification)
MAX_TTS_SPEED = 1.35          # Maximum TTS speed multiplier
MAX_ATEMPO_SPEED = 1.65       # Maximum FFmpeg atempo time-stretch multiplier for exact lip-sync
DEFAULT_DUCKING_ATTENUATION = -14.0 # dB reduction for original audio during speech
DEFAULT_DUCKING_FADE_MS = 250 # ms fade in / fade out for ducking

# Application Version & Update Configuration
APP_NAME = "AI Movie Translator & Auto Dubbing Studio"
APP_VERSION = "1.3.1"
APP_BUILD_DATE = "2026-11-19"
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/rongk2562/Dubber/main/version.json"
LOCAL_UPDATE_URL = "http://localhost:5000/api/version"
GITHUB_RELEASES_URL = "https://github.com/rongk2562/Dubber/releases"

# Bilingual Localization Strings (English & Khmer)
STRINGS = {
    "en": {
        "app_title": "AI Movie Translator & Auto Dubbing Studio",
        "subtitle": "Automatic Speech Recognition, Khmer Translation, Neural Voice Dubbing & Time Sync",
        "file": "File",
        "edit": "Edit",
        "tools": "Tools",
        "help": "Help",
        "new_project": "New Project",
        "open_project": "Open Project...",
        "save_project": "Save Project",
        "save_project_as": "Save Project As...",
        "import_video": "Import Video",
        "export_dubbed": "Export Dubbed Video (MP4)...",
        "export_srt": "Export Subtitles (SRT)...",
        "export_wav": "Export Dubbed Audio (WAV)...",
        "batch_queue": "Batch Processing Queue",
        "settings": "Settings",
        "exit": "Exit",
        "language_toggle": "ភាសាខ្មែរ (Khmer)",
        
        # Tabs
        "tab_editor": "Dubbing & Subtitle Editor",
        "tab_speakers": "Speaker Voice Assignment",
        "tab_glossary": "Glossary & Character Names",
        "tab_sync_report": "Time-Sync Report",
        "tab_batch": "Batch Queue",
        
        # Actions
        "btn_import_video": "Import Video (MP4/MKV/AVI)",
        "btn_transcribe": "1. Transcribe (Whisper)",
        "btn_translate_all": "2. Translate to Khmer",
        "btn_generate_tts": "3. Generate AI Voices",
        "btn_auto_sync": "4. Auto Time-Sync",
        "btn_export_all": "5. Export Final Video",
        "btn_play_segment": "Play Segment",
        "btn_preview_tts": "Preview Voice",
        "btn_add_segment": "Add Segment",
        "btn_delete_segment": "Delete Segment",
        "btn_split_segment": "Split Segment",
        "btn_merge_segments": "Merge Segments",
        
        # Table Headers
        "col_id": "#",
        "col_start": "Start",
        "col_end": "End",
        "col_slot": "Slot (s)",
        "col_speaker": "Speaker",
        "col_source_text": "Original Speech",
        "col_khmer_text": "Khmer Translation",
        "col_voice": "Voice",
        "col_tts_dur": "TTS (s)",
        "col_speed": "Speed",
        "col_sync_status": "Sync Status",
        "col_actions": "Actions",
        
        # Audio Mixing
        "audio_mixing_title": "Audio Mixing & Ducking",
        "audio_mixing_tab": "🎛️ Audio",
        "original_volume": "Original Audio Volume",
        "original_voice_volume": "Original Voice Volume",
        "background_volume": "Background (Music/SFX) Volume",
        "ai_voice_volume": "AI Voice Volume",
        "dubbed_volume": "Dubbed Voice Volume",
        "ducking_level": "Ducking Reduction",
        "mute_original": "Mute Original Audio (Pure Dubbing Only)",
        "keep_background": "Keep Background (Music & Sound Effects)",
        "mask_subtitles": "Mask Original Chinese Subtitles",
        "burn_subtitles": "Burn-in Subtitles into Exported Video",
        "btn_detect_genders": "🎭 Auto-Assign Character Voices",
        "ai_voice_speed": "AI Voice Speed",
        "btn_apply_speed_all": "⚡ Apply to All",
        "batch_speed": "Speed:",
        "btn_set_speed": "Set Speed",
        
        # Logo Tool
        "logo_tool_title": "Watermark / Logo Tool",
        "logo_tool_tab": "🎨 Logo",
        "logo_enable": "Overlay Custom Logo on Video",
        "logo_browse": "🖼️ Choose Logo...",
        "logo_position": "Position",
        "logo_size": "Logo Size",
        "logo_opacity": "Opacity",
        "pos_top_right": "Top-Right",
        "pos_top_left": "Top-Left",
        "pos_bottom_right": "Bottom-Right",
        "pos_bottom_left": "Bottom-Left",
        "pos_center": "Center",
        "preview_original": "📺 Original Video",
        "preview_result": "✨ Preview Dubbed Result",
        "btn_preview_dubbed": "Preview Dubbed Result",
        "btn_refresh_preview": "Refresh Preview",
        "preview_loading": "Generating fast dubbed preview...",
        
        # Statuses
        "status_ready": "Ready",
        "status_extracting": "Extracting audio from video...",
        "status_transcribing": "Transcribing speech with faster-whisper...",
        "status_translating": "Translating segments to Khmer...",
        "status_generating_tts": "Generating Khmer neural speech clips...",
        "status_syncing": "Performing 4-stage time synchronization...",
        "status_exporting": "Muxing and exporting final video...",
        "status_saved": "Project saved successfully.",
        
        # Time-sync states
        "sync_ok": "OK (Fits)",
        "sync_speed_adj": "Speed Adjusted",
        "sync_shortened": "Text Shortened",
        "sync_borrowed": "Gap Borrowed",
        "sync_time_stretched": "Time Stretched",
        "sync_overflow": "OVERFLOW (Needs Review)",
    },
    
    "km": {
        "app_title": "កម្មវិធីបកប្រែខ្សែភាពយន្ត និងបញ្ចូលសំឡេងស្វ័យប្រវត្តិតាម AI",
        "subtitle": "ស្ដាប់សំឡេងដើម បកប្រែជាភាសាខ្មែរ បញ្ចូលសំឡេង AI និងតម្រឹមពេលវេលាជាក់លាក់",
        "file": "ឯកសារ (File)",
        "edit": "កែសម្រួល (Edit)",
        "tools": "ឧបករណ៍ (Tools)",
        "help": "ជំនួយ (Help)",
        "new_project": "គម្រោងថ្មី (New Project)",
        "open_project": "បើកគម្រោង... (Open)",
        "save_project": "រក្សាទុក (Save)",
        "save_project_as": "រក្សាទុកជា... (Save As)",
        "import_video": "នាំចូលវីដេអូ (Import Video)",
        "export_dubbed": "នាំចេញវីដេអូបញ្ចូលសំឡេង (MP4)...",
        "export_srt": "នាំចេញអក្សររត់ក្រោម (SRT)...",
        "export_wav": "នាំចេញឯកសារសំឡេងបញ្ចូល (WAV)...",
        "batch_queue": "ជួរដំណើរការវីដេអូច្រើន (Batch Queue)",
        "settings": "ការកំណត់ (Settings)",
        "exit": "ចាកចេញ (Exit)",
        "language_toggle": "English",
        
        # Tabs
        "tab_editor": "កែសម្រួលការបញ្ចូលសំឡេង និងអត្ថបទ",
        "tab_speakers": "កំណត់សំឡេងតាមតួអង្គ (Speakers)",
        "tab_glossary": "វចនានុក្រមឈ្មោះតួអង្គ (Glossary)",
        "tab_sync_report": "របាយការណ៍តម្រឹមពេលវេលា (Sync Report)",
        "tab_batch": "ដំណើរការជាបាច់ (Batch Queue)",
        
        # Actions
        "btn_import_video": "នាំចូលវីដេអូ (MP4/MKV/AVI)",
        "btn_transcribe": "១. ស្ដាប់បំប្លែងជាអក្សរ (Whisper)",
        "btn_translate_all": "២. បកប្រែជាភាសាខ្មែរ",
        "btn_generate_tts": "៣. បង្កើតសំឡេង AI ខ្មែរ",
        "btn_auto_sync": "៤. តម្រឹមពេលវេលាស្វ័យប្រវត្តិ",
        "btn_export_all": "៥. នាំចេញវីដេអូសម្រេច",
        "btn_play_segment": "ចាក់វគ្គនេះ",
        "btn_preview_tts": "ស្ដាប់សំឡេង AI",
        "btn_add_segment": "បន្ថែមវគ្គថ្មី",
        "btn_delete_segment": "លុបវគ្គនេះ",
        "btn_split_segment": "ពុះវគ្គ",
        "btn_merge_segments": "បញ្ចូលគ្នាវគ្គ",
        
        # Table Headers
        "col_id": "ល.រ",
        "col_start": "ចាប់ផ្ដើម",
        "col_end": "បញ្ចប់",
        "col_slot": "ថិរវេលា",
        "col_speaker": "តួអង្គ",
        "col_source_text": "អត្ថបទដើម",
        "col_khmer_text": "អត្ថបទខ្មែរ",
        "col_voice": "សំឡេង AI",
        "col_tts_dur": "ថិរវេលា AI",
        "col_speed": "ល្បឿន",
        "col_sync_status": "ស្ថានភាព",
        "col_actions": "សកម្មភាព",
        
        # Audio Mixing
        "audio_mixing_title": "ការលាយសំឡេង និងបញ្ចុះសំឡេងដើម (Audio Mixing & Ducking)",
        "audio_mixing_tab": "🎛️ សំឡេង (Audio)",
        "original_volume": "កម្រិតសំឡេងដើម / ផ្ទៃខាងក្រោយ",
        "original_voice_volume": "កម្រិតសំឡេងមនុស្សដើម (Original Voice)",
        "background_volume": "កម្រិតតន្ត្រីផ្ទៃក្រោយ (Music/SFX)",
        "ai_voice_volume": "កម្រិតសំឡេងបញ្ចូល AI ខ្មែរ (AI Voice)",
        "dubbed_volume": "កម្រិតសំឡេងបញ្ចូល AI ខ្មែរ",
        "ducking_level": "កម្រិតបញ្ចុះសំឡេងដើមពេលមានការនិយាយ (Ducking)",
        "mute_original": "🔇 បិទសំឡេងដើមទាំងស្រុង (លឺតែសំឡេង AI ខ្មែរ)",
        "keep_background": "រក្សាតន្ត្រីផ្ទៃក្រោយ (Music & SFX)",
        "mask_subtitles": "បិទបាំងអក្សរចិនដើម (Subtitle Mask)",
        "burn_subtitles": "ដុតអក្សររត់ក្រោម (Subtitle) ជាប់លើវីដេអូនាំចេញ",
        "btn_detect_genders": "🎭 ស្មានសំឡេងតួអង្គ (ប្រុស/ស្រី) ស្វ័យប្រវត្តិ",
        "ai_voice_speed": "ល្បឿនសំឡេង AI និយាយ (Voice Speed)",
        "btn_apply_speed_all": "⚡ អនុវត្តគ្រប់វគ្គ",
        "batch_speed": "ល្បឿន:",
        "btn_set_speed": "កំណត់ល្បឿន",
        
        # Logo Tool
        "logo_tool_title": "ឧបករណ៍ដាក់ Logo / សញ្ញាសម្គាល់ (Watermark Tool)",
        "logo_tool_tab": "🎨 Logo / Watermark",
        "logo_enable": "បង្ហាញ Logo លើវីដេអូ Result",
        "logo_browse": "🖼️ ជ្រើសរើស Logo...",
        "logo_position": "ទីតាំង Logo",
        "logo_size": "ទំហំ Logo",
        "logo_opacity": "កម្រិតថ្លា/ច្បាស់",
        "pos_top_right": "លើ - ស្ដាំ",
        "pos_top_left": "លើ - ឆ្វេង",
        "pos_bottom_right": "ក្រោម - ស្ដាំ",
        "pos_bottom_left": "ក្រោម - ឆ្វេង",
        "pos_center": "កណ្ដាល",
        "preview_original": "📺 វីដេអូដើម",
        "preview_result": "✨ មើលលទ្ធផល (Preview Dubbed)",
        "btn_preview_dubbed": "មើលលទ្ធផលសាកល្បង (Preview)",
        "btn_refresh_preview": "ផ្ទុកឡើងវិញ",
        "preview_loading": "កំពុងរៀបចំវីដេអូសម្រាប់ចាក់សាកល្បង...",
        
        # Statuses
        "status_ready": "រួចរាល់សម្រាប់ដំណើរការ",
        "status_extracting": "កំពុងស្រង់យកសំឡេងពីវីដេអូតាម FFmpeg...",
        "status_transcribing": "កំពុងស្ដាប់បំប្លែងជាអក្សរតាម faster-whisper...",
        "status_translating": "កំពុងបកប្រែអត្ថបទទៅជាភាសាខ្មែរ...",
        "status_generating_tts": "កំពុងបង្កើតឯកសារសំឡេង AI ខ្មែរ (edge-tts)...",
        "status_syncing": "កំពុងតម្រឹមពេលវេលា ៤ ដំណាក់កាលតាមវិធាន...",
        "status_exporting": "កំពុងលាយសំឡេង និងនាំចេញវីដេអូចុងក្រោយ...",
        "status_saved": "បានរក្សាទុកគម្រោងដោយជោគជ័យ។",
        
        # Time-sync states
        "sync_ok": "សមរម្យ (ល្មម)",
        "sync_speed_adj": "បានពន្លឿនសំឡេង",
        "sync_shortened": "បានសង្ខេបអត្ថបទ",
        "sync_borrowed": "បានខ្ចីចន្លោះទំនេរ",
        "sync_time_stretched": "បានបង្រួមពេលវេលា",
        "sync_overflow": "ហួសកំណត់ (ត្រូវការពិនិត្យ)",
    }
}
