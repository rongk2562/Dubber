"""
Batch Folder Processing Engine for Multi-Episode Video Dubbing.
Features:
- Recursive / flat folder scanning (.mp4, .mkv, .avi, .mov) with natural sorting (EP1, EP2, EP10)
- Persistent JSON queue state (DATA_DIR/batch_queue.json) for crash/restart recovery
- Sequential background worker with Start, Pause, Resume, Cancel, and Retry Failed
- Selective pipeline steps (Extract, Translate, AI Voice TTS, Render Video, Export SRT)
- Skip already completed episodes (default) or Overwrite existing outputs
- Shared character/term glossary across all episodes
- Supports Whisper / Auto OCR / OpenAI STT and Google / ChatGPT / Gemini translation
"""

import copy
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from app.audio_engine import AudioEngine
from app.config import DATA_DIR, TARGET_LANG_MAP, get_voice_pair_for_lang, is_female_voice
from app.dubbing.subtitle_renderer import SubtitleRenderer
from app.export_engine import ExportEngine
from app.logger import get_logger, redact_secrets
from app.models import GlossaryItem, Project, ProjectSettings, Segment
from app.openai_client import OpenAIClient, OpenAIUsageStats
from app.stt_engine import STTEngine
from app.sync_engine import SyncEngine
from app.translation_engine import TranslationEngine
from app.tts_engine import TTSEngine

log = get_logger("batch_engine")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov"}
BATCH_STATE_FILE = DATA_DIR / "batch_queue.json"

# Status labels in Khmer (stored & displayed uniformly)
ST_PENDING = "រង់ចាំ"
ST_EXTRACTING = "កំពុងដកស្រង់"
ST_TRANSLATING = "កំពុងបកប្រែ"
ST_TTS = "កំពុងបញ្ចូលសំឡេង"
ST_RENDERING = "កំពុង Render"
ST_DONE = "រួចរាល់"
ST_FAILED = "បរាជ័យ"
ST_SKIPPED = "រំលង"


def natural_sort_key(name: str) -> List[Any]:
    """Natural sort key so EP1, EP2, EP10 order numerically instead of 1, 10, 2."""
    parts = re.split(r"(\d+)", (name or "").lower())
    return [int(p) if p.isdigit() else p for p in parts]


def probe_video_duration(video_path: str) -> float:
    """Fast ffprobe duration lookup (seconds)."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return max(0.0, float((r.stdout or "0").strip()))
    except Exception:
        return 0.0


def format_duration_hms(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class BatchItem:
    index: int
    video_path: str
    filename: str
    duration_sec: float = 0.0
    checked: bool = True
    status: str = ST_PENDING
    progress: int = 0
    error_msg: str = ""
    out_mp4: str = ""
    out_km_srt: str = ""
    out_zh_srt: str = ""
    mask_override: Optional[Dict[str, Any]] = None
    audio_override: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchItem":
        valid = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid})


MASK_SETTINGS_FIELDS = (
    "mask_subtitles",
    "mask_y_percent",
    "mask_height_percent",
    "mask_width_percent",
    "mask_x_percent",
    "mask_opacity",
    "mask_color",
    "mask_style",
    "mask_blur_strength",
    "mask_blur_dim",
    "mask_feather",
    "subtitle_font",
    "subtitle_font_size",
    "subtitle_margin_v",
)


def extract_mask_settings_dict(settings: ProjectSettings) -> Dict[str, Any]:
    """Extract only subtitle mask & Khmer subtitle styling fields without touching unrelated settings."""
    return {k: copy.deepcopy(getattr(settings, k)) for k in MASK_SETTINGS_FIELDS if hasattr(settings, k)}


def apply_mask_settings_dict(settings: ProjectSettings, mask_dict: Optional[Dict[str, Any]]) -> ProjectSettings:
    """Apply saved mask & Khmer subtitle fields onto a ProjectSettings copy."""
    if not mask_dict:
        return settings
    for k in MASK_SETTINGS_FIELDS:
        if k in mask_dict and mask_dict[k] is not None:
            setattr(settings, k, copy.deepcopy(mask_dict[k]))
    return settings


def format_mask_summary(settings_like: Any, enabled: Optional[bool] = None, lang: str = "km") -> str:
    """Build compact human-readable summary of mask + Khmer subtitle settings."""
    is_on = bool(getattr(settings_like, "mask_subtitles", True)) if enabled is None else bool(enabled)
    if not is_on:
        return "បិទការបិទបាំង (Disabled)" if lang == "km" else "Mask Disabled"

    y_pct = float(getattr(settings_like, "mask_y_percent", 83.0))
    h_pct = float(getattr(settings_like, "mask_height_percent", 12.0))
    w_pct = float(getattr(settings_like, "mask_width_percent", 100.0))
    x_pct = float(getattr(settings_like, "mask_x_percent", 0.0))
    op = float(getattr(settings_like, "mask_opacity", 0.95))
    style = (getattr(settings_like, "mask_style", "solid") or "solid").lower()
    blur_s = int(getattr(settings_like, "mask_blur_strength", 40))
    blur_d = int(getattr(settings_like, "mask_blur_dim", 25))

    if style == "blur":
        style_txt = (
            f"ព្រិល Blur {blur_s} · ងងឹត {blur_d}%"
            if lang == "km" else
            f"Blur {blur_s} · Dim {blur_d}%"
        )
    else:
        style_txt = (
            f"ខ្មៅ · {int(round(op * 100))}%"
            if lang == "km" else
            f"Solid · {int(round(op * 100))}%"
        )

    parts = [f"Y {y_pct:.0f}%"]
    if w_pct < 99.9 or x_pct > 0.1:
        parts.append(f"X {x_pct:.0f}%")
        parts.append(f"ទទឹង {w_pct:.0f}%" if lang == "km" else f"W {w_pct:.0f}%")
    parts.append(f"កម្ពស់ {h_pct:.0f}%" if lang == "km" else f"H {h_pct:.0f}%")
    parts.append(style_txt)
    return " · ".join(parts)


@dataclass
class BatchJobConfig:
    input_folder: str = ""
    output_folder: str = ""
    include_subfolders: bool = False
    overwrite_existing: bool = False
    src_engine: str = "auto"         # "auto", "ocr", "whisper", "openai"
    trans_engine: str = "google"     # "google", "openai", "gemini"
    run_extract: bool = True
    run_translate: bool = True
    run_tts: bool = True
    run_render: bool = True
    run_export_srt: bool = True
    notify_on_complete: bool = True
    shared_glossary: List[Dict[str, str]] = field(default_factory=list)
    mask_enabled: bool = True
    mask_mode: str = "normal"        # "normal" (copy from single-video settings) | "custom" (batch-specific)
    batch_mask_settings: Dict[str, Any] = field(default_factory=dict)
    audio_mode: str = "normal"       # "normal" (copy from single-video settings) | "custom" (batch-specific)
    batch_audio_settings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchJobConfig":
        valid = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid})


AUDIO_SETTINGS_FIELDS = (
    "original_voice_volume",
    "background_volume",
    "keep_background",
    "mute_original",
    "dubbed_volume",
    "ai_voice_speed",
)


def has_audio_stream(video_path: str) -> bool:
    """Return True if media file contains at least one audio track."""
    if not video_path or not os.path.exists(video_path):
        return False
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return "audio" in (r.stdout or "").lower()
    except Exception:
        return True


def detect_audio_separation_info(lang: str = "km") -> tuple[bool, str]:
    """
    Detect whether Demucs AI weights are available locally; otherwise explain FFmpeg 3-band DSP fallback.
    """
    try:
        import importlib.util
        has_demucs_pkg = importlib.util.find_spec("demucs") is not None
        cache_dir = Path.home() / ".cache"
        has_weights = (
            has_demucs_pkg
            and cache_dir.exists()
            and (
                any(cache_dir.glob("**/htdemucs*.th"))
                or any(cache_dir.glob("**/models--adefossez--HTDemucs/**/*.th"))
            )
        )
        if has_weights:
            return True, (
                "🎛️ Separation: Demucs AI (htdemucs) — កាត់សំឡេងនិយាយច្បាស់ខ្ពស់"
                if lang == "km" else
                "🎛️ Separation: Demucs AI (htdemucs)"
            )
    except Exception:
        pass

    return False, (
        "🎛️ Separation: FFmpeg 3-Band Vocal Removal (Fallback — បន្ថយ/កាត់សំឡេងនិយាយកណ្ដាល; អាចដំឡើង Demucs សម្រាប់ AI ខ្ពស់ជាង)"
        if lang == "km" else
        "🎛️ Separation: FFmpeg 3-Band Vocal Removal (Fallback; install Demucs for AI separation)"
    )


def create_silent_wav(duration_sec: float, out_wav: str, sample_rate: int = 44100) -> str:
    """Create stereo silent WAV file matching duration_sec for videos without an original audio stream."""
    import wave
    dur = max(0.5, float(duration_sec or 1.0))
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    n_frames = int(round(dur * sample_rate))
    chunk = b"\x00\x00\x00\x00" * 4096
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        rem = n_frames
        while rem > 0:
            take = min(rem, 4096)
            wf.writeframes(chunk[: take * 4])
            rem -= take
    return out_wav


def extract_audio_settings_dict(settings: ProjectSettings, auto_fit: bool = False) -> Dict[str, Any]:
    """Extract Batch Audio Mix fields without touching unrelated settings."""
    orig_on = (getattr(settings, "original_voice_volume", 0.0) > 0.001) and (not getattr(settings, "mute_original", False))
    bg_on = (getattr(settings, "background_volume", 0.30) > 0.001) and bool(getattr(settings, "keep_background", True))
    return {
        "orig_enabled": bool(orig_on),
        "original_voice_volume": float(getattr(settings, "original_voice_volume", 0.0)) if orig_on else 0.0,
        "bg_enabled": bool(bg_on),
        "background_volume": float(getattr(settings, "background_volume", 0.30)) if bg_on else 0.0,
        "dubbed_volume": float(getattr(settings, "dubbed_volume", 1.0)),
        "ai_voice_speed": float(getattr(settings, "ai_voice_speed", 1.05)),
        "auto_fit": bool(auto_fit),
    }


def apply_audio_settings_dict(settings: ProjectSettings, audio_dict: Optional[Dict[str, Any]]) -> bool:
    """
    Apply audio mix dictionary onto a ProjectSettings copy safely, returning whether auto_fit is enabled.
    Ensures mute_original is True ONLY when BOTH Original Voice and Music/SFX are OFF, and keeps
    original_volume at 0.3 so AudioEngine.mix_audio_with_ducking preserves background_volume cleanly.
    """
    if not audio_dict:
        return False

    orig_vol = float(audio_dict.get("original_voice_volume", getattr(settings, "original_voice_volume", 0.0)))
    orig_on = bool(audio_dict.get("orig_enabled", orig_vol > 0.001))
    if not orig_on:
        orig_vol = 0.0

    bg_vol = float(audio_dict.get("background_volume", getattr(settings, "background_volume", 0.30)))
    bg_on = bool(audio_dict.get("bg_enabled", bg_vol > 0.001 and bool(audio_dict.get("keep_background", True))))
    if not bg_on:
        bg_vol = 0.0

    settings.original_voice_volume = max(0.0, min(1.0, orig_vol))
    settings.background_volume = max(0.0, min(1.0, bg_vol))
    settings.keep_background = bool(bg_on and bg_vol > 0.001)
    settings.mute_original = (not orig_on or orig_vol <= 0.001) and (not bg_on or bg_vol <= 0.001)
    # Keep legacy original_volume at 0.3 sentinel so AudioEngine never overwrites background_volume=0.3
    settings.original_volume = 0.3
    settings.dubbed_volume = max(0.0, min(1.5, float(audio_dict.get("dubbed_volume", getattr(settings, "dubbed_volume", 1.0)))))
    settings.ai_voice_speed = max(0.75, min(1.5, float(audio_dict.get("ai_voice_speed", getattr(settings, "ai_voice_speed", 1.05)))))
    return bool(audio_dict.get("auto_fit", False))


def format_audio_summary(settings_like: Any, auto_fit: bool = False) -> str:
    """Build live audio badge summary matching single-video format (e.g. 'Original: OFF | BG: 30% | AI: 100%')."""
    if isinstance(settings_like, dict):
        orig_on = bool(settings_like.get("orig_enabled", float(settings_like.get("original_voice_volume", 0.0)) > 0.001))
        orig_pct = int(round(float(settings_like.get("original_voice_volume", 0.0)) * 100))
        bg_on = bool(settings_like.get("bg_enabled", float(settings_like.get("background_volume", 0.3)) > 0.001))
        bg_pct = int(round(float(settings_like.get("background_volume", 0.3)) * 100))
        ai_pct = int(round(float(settings_like.get("dubbed_volume", 1.0)) * 100))
        spd = float(settings_like.get("ai_voice_speed", 1.05))
        af = bool(settings_like.get("auto_fit", auto_fit))
    else:
        mute_all = bool(getattr(settings_like, "mute_original", False))
        orig_v = float(getattr(settings_like, "original_voice_volume", 0.0))
        bg_v = float(getattr(settings_like, "background_volume", 0.30))
        keep_bg = bool(getattr(settings_like, "keep_background", True))
        orig_on = (orig_v > 0.001) and (not mute_all)
        bg_on = (bg_v > 0.001) and keep_bg and (not mute_all)
        orig_pct = int(round(orig_v * 100))
        bg_pct = int(round(bg_v * 100))
        ai_pct = int(round(float(getattr(settings_like, "dubbed_volume", 1.0)) * 100))
        spd = float(getattr(settings_like, "ai_voice_speed", 1.05))
        af = bool(auto_fit)

    orig_str = f"{orig_pct}%" if (orig_on and orig_pct > 0) else "OFF"
    bg_str = f"{bg_pct}%" if (bg_on and bg_pct > 0) else "OFF"
    if af:
        spd_str = "Auto-fit"
    else:
        num_str = f"{spd:.2f}".rstrip("0").rstrip(".")
        if "." not in num_str:
            num_str += ".0"
        spd_str = f"{num_str}x"
    return f"Original: {orig_str} | BG: {bg_str} | AI: {ai_pct}% | {spd_str}"


def resolve_episode_settings(
    base_settings: ProjectSettings,
    config: BatchJobConfig,
    item: Optional[BatchItem] = None,
) -> ProjectSettings:
    """
    Build isolated per-episode ProjectSettings without mutating normal-mode base_settings.
    Precedence: base_settings -> batch_mask/audio (if mode=='custom') -> item overrides.
    """
    ep = copy.deepcopy(base_settings)
    ep.src_mode = config.src_engine
    ep.translation_provider = config.trans_engine

    # 1. Mask settings
    if (config.mask_mode or "normal") == "custom" and config.batch_mask_settings:
        apply_mask_settings_dict(ep, config.batch_mask_settings)
    ep.mask_subtitles = bool(config.mask_enabled)

    if item is not None and isinstance(item.mask_override, dict) and item.mask_override:
        apply_mask_settings_dict(ep, item.mask_override)

    # 2. Audio mix settings
    auto_fit = False
    if (config.audio_mode or "normal") == "custom" and config.batch_audio_settings:
        auto_fit = apply_audio_settings_dict(ep, config.batch_audio_settings)
    else:
        # Normalize normal settings copy cleanly
        norm_audio = extract_audio_settings_dict(ep, auto_fit=False)
        apply_audio_settings_dict(ep, norm_audio)

    if item is not None and isinstance(item.audio_override, dict) and item.audio_override:
        auto_fit = apply_audio_settings_dict(ep, item.audio_override)

    setattr(ep, "_batch_audio_auto_fit", auto_fit)
    return ep


def render_batch_audio_preview(
    video_path: str,
    ep_settings: ProjectSettings,
    lang: str = "km",
    clip_seconds: float = 12.0,
    out_wav: Optional[str] = None,
) -> tuple[str, str]:
    """
    Mix a 10-15s preview WAV from video_path using exact AudioEngine + BackgroundSeparator + TimelineMixer
    logic off the UI thread.
    Returns (preview_wav_path, info_note).
    """
    import tempfile
    from app.config import TEMP_DIR, get_voice_pair_for_lang

    work_dir = Path(tempfile.mkdtemp(prefix="batch_audio_prev_", dir=str(TEMP_DIR)))
    if not out_wav:
        out_wav = str(work_dir / "preview_mix.wav")

    audio_engine = AudioEngine(temp_dir=work_dir)
    tts_engine = TTSEngine()
    has_audio = has_audio_stream(video_path) if video_path and os.path.exists(video_path) else False

    dur = min(15.0, max(10.0, float(clip_seconds)))
    if has_audio:
        vid_dur = audio_engine.get_media_duration(video_path)
        start_at = 2.0 if vid_dur > dur + 4.0 else 0.0
        if vid_dur > 1.0:
            dur = min(dur, max(3.0, vid_dur - start_at))
        orig_clip = str(work_dir / "orig_clip.wav")
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start_at:.2f}", "-t", f"{dur:.2f}",
            "-i", video_path, "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", orig_clip,
        ]
        subprocess.run(
            cmd, capture_output=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    else:
        orig_clip = create_silent_wav(dur, str(work_dir / "orig_silent.wav"))

    # Synthesize a sample Khmer AI sentence (fallback to clean test chime if offline)
    t_lang = getattr(ep_settings, "target_language", "km") or "km"
    male_v, _ = get_voice_pair_for_lang(t_lang)
    sample_txt = (
        "សួស្តី នេះជាការសាកល្បងលាយសំឡេង AI និងភ្លេងផ្ទៃខាងក្រោយ។"
        if lang == "km" else
        "Hello, this is a preview of AI dubbing and background audio mix."
    )
    seg = Segment(id=1, start=1.2, end=min(dur - 0.5, 5.5), text_km=sample_txt, voice=male_v)
    spd = 1.0 if getattr(ep_settings, "_batch_audio_auto_fit", False) else float(getattr(ep_settings, "ai_voice_speed", 1.05))
    try:
        tts_engine.generate_segment_tts(seg, speed_factor=spd)
    except Exception:
        pass

    if not seg.tts_file or not os.path.exists(seg.tts_file):
        # Offline fallback tone so preview never breaks when network TTS is unavailable
        import wave, math, struct
        fb_wav = str(work_dir / "fallback_ai_sample.wav")
        sr = 44100
        n_s = int(2.2 * sr)
        with wave.open(fb_wav, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            frames = bytearray()
            for i in range(n_s):
                env = min(1.0, i / 2000.0) * min(1.0, (n_s - i) / 2000.0)
                val = int(10000 * env * math.sin(2.0 * math.pi * 440.0 * (i / sr)))
                frames.extend(struct.pack("<hh", val, val))
            wf.writeframes(bytes(frames))
        seg.tts_file = fb_wav
        seg.tts_duration = 2.2

    audio_engine.align_speech_and_subtitles([seg])
    dub_wav = audio_engine.build_dubbed_audio_track(dur, [seg], output_wav=str(work_dir / "dub_clip.wav"))

    mute_orig = bool(getattr(ep_settings, "mute_original", False)) or (not has_audio)
    orig_v_vol = float(getattr(ep_settings, "original_voice_volume", 0.0)) if has_audio else 0.0
    bg_vol = float(getattr(ep_settings, "background_volume", 0.30)) if has_audio else 0.0
    keep_bg = bool(getattr(ep_settings, "keep_background", True)) and has_audio

    audio_engine.mix_audio_with_ducking(
        original_audio_path=orig_clip,
        dubbed_audio_path=dub_wav,
        segments=[seg],
        original_vol_factor=0.3,
        original_voice_vol=orig_v_vol,
        background_vol=bg_vol,
        dubbed_vol_factor=float(getattr(ep_settings, "dubbed_volume", 1.0)),
        ducking_db=float(getattr(ep_settings, "ducking_db", -14.0)),
        fade_ms=int(getattr(ep_settings, "ducking_fade_ms", 250)),
        mute_original=mute_orig,
        keep_background=keep_bg,
        output_wav=out_wav,
    )
    _, sep_msg = detect_audio_separation_info(lang=lang)
    if not has_audio:
        sep_msg = "វីដេអូគ្មាន audio track ដើម — ចាក់តែសំឡេង AI" if lang == "km" else "No original audio track — AI voice only"
    return out_wav, sep_msg


def build_output_paths(video_path: str, input_root: str, output_root: str) -> tuple[str, str, str]:
    """Build <output>/<folder_name>/<video_stem>.(mp4|km.srt|zh.srt) preserving folder hierarchy."""
    vp = Path(video_path)
    in_root = Path(input_root) if input_root else vp.parent
    out_root = Path(output_root) if output_root else vp.parent / "batch_output"
    folder_name = in_root.name or "output"
    try:
        rel_parent = vp.parent.relative_to(in_root)
        target_dir = out_root / folder_name / rel_parent
    except Exception:
        target_dir = out_root / folder_name

    stem = vp.stem
    return (
        str(target_dir / f"{stem}.mp4"),
        str(target_dir / f"{stem}.km.srt"),
        str(target_dir / f"{stem}.zh.srt"),
    )


def scan_video_folder(
    input_folder: str,
    output_folder: str,
    include_subfolders: bool = False,
    probe_durations: bool = True,
) -> List[BatchItem]:
    """Scan folder for supported video files and return naturally sorted BatchItems."""
    root = Path(input_folder)
    if not root.exists() or not root.is_dir():
        return []

    files: List[Path] = []
    if include_subfolders:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                files.append(p)
    else:
        for p in root.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                files.append(p)

    files.sort(key=lambda p: natural_sort_key(str(p.relative_to(root))))

    items: List[BatchItem] = []
    for idx, fpath in enumerate(files, start=1):
        out_mp4, out_km, out_zh = build_output_paths(str(fpath), input_folder, output_folder)
        dur = probe_video_duration(str(fpath)) if probe_durations else 0.0
        items.append(
            BatchItem(
                index=idx,
                video_path=str(fpath),
                filename=fpath.name,
                duration_sec=dur,
                out_mp4=out_mp4,
                out_km_srt=out_km,
                out_zh_srt=out_zh,
            )
        )
    return items


def save_batch_state(config: BatchJobConfig, items: List[BatchItem], state_file: Optional[Path] = None) -> bool:
    """Persist batch queue configuration and items to disk (JSON) for crash/close recovery."""
    path = state_file or BATCH_STATE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "config": config.to_dict(),
            "items": [it.to_dict() for it in items],
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.warning("Failed to save batch state: %s", e)
        return False


def load_batch_state(state_file: Optional[Path] = None) -> Optional[tuple[BatchJobConfig, List[BatchItem]]]:
    """Load persisted batch queue state from JSON if present."""
    path = state_file or BATCH_STATE_FILE
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = BatchJobConfig.from_dict(data.get("config", {}))
        items = [BatchItem.from_dict(it) for it in data.get("items", [])]
        # Any item interrupted mid-flight when crashed resets back to Pending
        for it in items:
            if it.status in (ST_EXTRACTING, ST_TRANSLATING, ST_TTS, ST_RENDERING):
                it.status = ST_PENDING
                it.progress = 0
        return cfg, items
    except Exception as e:
        log.warning("Failed to load batch state: %s", e)
        return None


def clear_batch_state(config: Optional[BatchJobConfig] = None, state_file: Optional[Path] = None) -> bool:
    """Clear persisted queue items so next launch never resumes an old cleared batch."""
    path = state_file or BATCH_STATE_FILE
    if config is not None:
        return save_batch_state(config, [], state_file=path)
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception as e:
        log.warning("Failed to clear batch state: %s", e)
        return False


def _write_srt_file(segments: List[Segment], out_path: str, use_source: bool = False) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines: List[str] = []
    idx = 1
    for seg in segments:
        txt = (seg.text_src if use_source else (seg.text_km or seg.text_src) or "").strip()
        if not txt:
            continue
        st = seg.start if use_source else seg.speech_start
        et = seg.end if use_source else seg.speech_end
        lines.append(str(idx))
        lines.append(f"{SubtitleRenderer.format_srt_timestamp(st)} --> {SubtitleRenderer.format_srt_timestamp(et)}")
        lines.append(txt)
        lines.append("")
        idx += 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def should_skip_item(item: BatchItem, config: BatchJobConfig) -> bool:
    """Determine if an episode's requested outputs already exist when overwrite is disabled."""
    if config.overwrite_existing:
        return False
    checks: List[bool] = []
    if config.run_render and item.out_mp4:
        checks.append(os.path.exists(item.out_mp4) and os.path.getsize(item.out_mp4) > 1024)
    if config.run_export_srt and item.out_km_srt:
        checks.append(os.path.exists(item.out_km_srt) and os.path.getsize(item.out_km_srt) > 0)
    return bool(checks) and all(checks)


class BatchPipelineWorker(QThread):
    """Sequential background worker executing the complete dubbing pipeline per episode."""

    item_updated = Signal(int, str, int, str)       # row_idx, status, progress_pct, error_msg
    overall_progress = Signal(int, str, float, float)  # pct, active_name, elapsed_sec, eta_sec
    log_message = Signal(str)
    usage_updated = Signal(object)                   # OpenAIUsageStats
    batch_finished = Signal(int, int, int)           # done_count, failed_count, skipped_count

    def __init__(
        self,
        config: BatchJobConfig,
        items: List[BatchItem],
        base_settings: ProjectSettings,
        retry_failed_only: bool = False,
        state_file: Optional[Path] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.items = items
        self.base_settings = copy.deepcopy(base_settings)
        self.retry_failed_only = retry_failed_only
        self.state_file = state_file or BATCH_STATE_FILE

        self._cancelled = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # Set means NOT paused
        self.total_usage = OpenAIUsageStats()

    def pause(self):
        self._pause_event.clear()
        self.log_message.emit("⏸ បានផ្អាក Batch ជាបណ្ដោះអាសន្ន...")

    def resume(self):
        self._pause_event.set()
        self.log_message.emit("▶ បានបន្តដំណើរការ Batch...")

    def cancel(self):
        self._cancelled = True
        self._pause_event.set()
        self.log_message.emit("⏹ កំពុងបោះបង់ Batch...")

    def _wait_if_paused(self) -> bool:
        while not self._pause_event.is_set():
            if self._cancelled:
                return False
            time.sleep(0.15)
        return not self._cancelled

    def run(self):
        start_ts = time.time()
        audio_engine = AudioEngine()
        tts_engine = TTSEngine()
        sync_engine = SyncEngine(audio_engine, tts_engine, TranslationEngine(self.base_settings))
        export_engine = ExportEngine(audio_engine)

        shared_glossary = [
            GlossaryItem(src_term=g.get("src_term", ""), km_term=g.get("km_term", ""))
            for g in (self.config.shared_glossary or [])
            if g.get("src_term") and g.get("km_term")
        ]

        # Build runnable row indices
        target_rows: List[int] = []
        for idx, it in enumerate(self.items):
            if not it.checked:
                continue
            if self.retry_failed_only and it.status != ST_FAILED:
                continue
            if not self.retry_failed_only and it.status == ST_DONE and not self.config.overwrite_existing:
                continue
            target_rows.append(idx)

        total_targets = max(1, len(target_rows))
        completed_so_far = 0
        done_cnt = 0
        failed_cnt = 0
        skipped_cnt = 0

        # Cache heavy ML engines across episodes in the batch so models load ONCE
        ocr_cache: Dict[str, Any] = {}
        stt_cache: Dict[str, STTEngine] = {}
        last_ui_ts = [0.0]

        def _make_sub_cb(r_idx: int, st_label: str, base_p: int, span_p: int, fname: str):
            def _cb(sub_pct: int, msg: str = ""):
                now = time.time()
                mapped = max(base_p, min(base_p + span_p, base_p + int((max(0, min(100, sub_pct)) / 100.0) * span_p)))
                if mapped == self.items[r_idx].progress and (now - last_ui_ts[0]) < 0.35:
                    return
                last_ui_ts[0] = now
                self.items[r_idx].progress = mapped
                self.item_updated.emit(r_idx, st_label, mapped, "")
                frac_done = completed_so_far + (mapped / 100.0)
                active_lbl = f"{fname} — {msg}" if msg else fname
                self._emit_overall(frac_done, total_targets, active_lbl, start_ts)
            return _cb

        for row_idx in target_rows:
            if not self._wait_if_paused():
                break

            item = self.items[row_idx]
            item.error_msg = ""

            if should_skip_item(item, self.config):
                item.status = ST_SKIPPED
                item.progress = 100
                skipped_cnt += 1
                completed_so_far += 1
                self.item_updated.emit(row_idx, ST_SKIPPED, 100, "")
                self.log_message.emit(f"⏭ រំលង (មានលទ្ធផលរួចហើយ): {item.filename}")
                save_batch_state(self.config, self.items, state_file=self.state_file)
                continue

            ep_settings = resolve_episode_settings(self.base_settings, self.config, item)
            trans_engine = TranslationEngine(ep_settings)

            proj = Project(
                name=Path(item.video_path).stem,
                video_path=item.video_path,
                glossary=list(shared_glossary),
                settings=ep_settings,
            )

            try:
                os.makedirs(os.path.dirname(item.out_mp4), exist_ok=True)
                self.log_message.emit(f"▶ ចាប់ផ្ដើមភាគ {item.index}: {item.filename}")
                video_has_audio = has_audio_stream(item.video_path)
                if not video_has_audio:
                    self.log_message.emit(
                        f"⚠️ ភាគ {item.index} ({item.filename}): វីដេអូគ្មាន audio track ដើម → រំលងការលាយសំឡេងដើម។"
                    )
                    ep_settings.mute_original = True
                    ep_settings.original_voice_volume = 0.0
                    ep_settings.background_volume = 0.0
                    ep_settings.keep_background = False

                # 1. Extract / Transcription
                if self.config.run_extract:
                    item.status = ST_EXTRACTING
                    item.progress = 5
                    self.item_updated.emit(row_idx, ST_EXTRACTING, 5, "")
                    self._emit_overall(completed_so_far + 0.05, total_targets, f"{item.filename} — កំពុងដកស្រង់អត្ថបទ...", start_ts)
                    ext_cb = _make_sub_cb(row_idx, ST_EXTRACTING, 5, 29, item.filename)

                    if not self._wait_if_paused():
                        break

                    src_mode = (self.config.src_engine or "auto").lower()
                    if src_mode == "openai":
                        if not video_has_audio:
                            raise RuntimeError("វីដេអូគ្មាន audio track សម្រាប់ដកស្រង់សំឡេងតាម OpenAI។")
                        self.log_message.emit(f"💬 ភាគ {item.index}: ដកស្រង់អត្ថបទតាម OpenAI API...")
                        oa = OpenAIClient(ep_settings)
                        segs, stt_u = oa.transcribe_video(
                            item.video_path,
                            source_lang=ep_settings.source_language or "zh",
                            progress_cb=ext_cb,
                            cancel_cb=lambda: self._cancelled,
                        )
                        self.total_usage.add(stt_u)
                        self.usage_updated.emit(self.total_usage)
                        proj.segments = segs
                    else:
                        ocr_name = getattr(ep_settings, "ocr_engine", "rapidocr") or "rapidocr"
                        use_ocr = (src_mode == "ocr")
                        if src_mode == "auto":
                            self.log_message.emit(f"🔎 ភាគ {item.index}: កំពុងពិនិត្យមើលអក្សរចិនក្នុងវីដេអូ (Auto Probe)...")
                            self._emit_overall(completed_so_far + 0.06, total_targets, f"{item.filename} — កំពុងស្ទង់អក្សរចិន (Probe)...", start_ts)
                            from app.dubbing.ocr_engine import SubtitleOcrEngine
                            if ocr_name not in ocr_cache:
                                ocr_cache[ocr_name] = SubtitleOcrEngine(engine_name=ocr_name)
                            use_ocr = ocr_cache[ocr_name].detect_hardcoded_subtitles(
                                item.video_path,
                                settings=ep_settings,
                                cancel_cb=lambda: self._cancelled,
                            )
                        if use_ocr:
                            self.log_message.emit(f"👁️ ភាគ {item.index}: រកឃើញអក្សរចិន → កំពុងស្កេន OCR...")
                            from app.dubbing.ocr_engine import SubtitleOcrEngine
                            if ocr_name not in ocr_cache:
                                ocr_cache[ocr_name] = SubtitleOcrEngine(engine_name=ocr_name)
                            ocr_eng = ocr_cache[ocr_name]
                            segs, _ = ocr_eng.scan_video(
                                video_path=item.video_path,
                                settings=ep_settings,
                                fps=float(getattr(ep_settings, "ocr_fps", 5.0)),
                                progress_cb=ext_cb,
                                cancel_cb=lambda: self._cancelled,
                            )
                            proj.segments = segs
                        else:
                            if not video_has_audio:
                                raise RuntimeError("វីដេអូគ្មាន audio track សម្រាប់ Whisper STT។")
                            self.log_message.emit(f"🎙️ ភាគ {item.index}: កំពុងដកស្រង់សំឡេង & ស្ដាប់តាម Whisper...")
                            ext_cb(5, "កំពុងដកស្រង់សំឡេង (FFmpeg)...")
                            extracted_wav = audio_engine.extract_audio(item.video_path)
                            w_model = getattr(ep_settings, "whisper_model", "base") or "base"
                            if w_model not in stt_cache:
                                stt_cache[w_model] = STTEngine(model_size=w_model)
                            stt = stt_cache[w_model]
                            proj.segments = stt.transcribe(
                                extracted_wav,
                                language=ep_settings.source_language,
                                progress_cb=ext_cb,
                            )
                            for s in proj.segments:
                                s.src_source = "Whisper"
                    self.log_message.emit(f"📝 ភាគ {item.index}: ដកស្រង់បាន {len(proj.segments)} ឃ្លា។")
                elif os.path.exists(item.out_zh_srt):
                    from app.dubbing.srt_io import parse_srt_file
                    parsed = parse_srt_file(item.out_zh_srt)
                    proj.segments = [
                        Segment(id=i + 1, start=e.start, end=e.end, text_src=e.text)
                        for i, e in enumerate(parsed.entries)
                    ]

                if not self._wait_if_paused():
                    break

                # Normalize target voices
                t_lang = getattr(ep_settings, "target_language", "km") or "km"
                male_v, female_v = get_voice_pair_for_lang(t_lang)
                for seg in proj.segments:
                    seg.voice = female_v if is_female_voice(seg.voice) else male_v

                if self.config.run_export_srt and proj.segments:
                    _write_srt_file(proj.segments, item.out_zh_srt, use_source=True)

                # 2. Translate
                if self.config.run_translate and proj.segments:
                    item.status = ST_TRANSLATING
                    item.progress = 35
                    self.item_updated.emit(row_idx, ST_TRANSLATING, 35, "")
                    self.log_message.emit(f"🌐 ភាគ {item.index}: កំពុងបកប្រែ {len(proj.segments)} ឃ្លា...")
                    tr_cb = _make_sub_cb(row_idx, ST_TRANSLATING, 35, 24, item.filename)

                    def _on_oa_u(u_stats: OpenAIUsageStats):
                        self.total_usage.add(u_stats)
                        self.usage_updated.emit(self.total_usage)

                    trans_engine.translate_segments(
                        proj.segments,
                        glossary=shared_glossary,
                        target_lang=t_lang,
                        progress_cb=tr_cb,
                        usage_cb=_on_oa_u if self.config.trans_engine == "openai" else None,
                        cancel_cb=lambda: self._cancelled,
                    )

                if not self._wait_if_paused():
                    break

                # 3. AI Voice TTS + Time Sync (Concurrent 5-worker Edge-TTS + episode-isolated clips dir)
                if self.config.run_tts and proj.segments:
                    item.status = ST_TTS
                    item.progress = 60
                    self.item_updated.emit(row_idx, ST_TTS, 60, "")
                    is_auto_fit = bool(getattr(ep_settings, "_batch_audio_auto_fit", False))
                    global_speed = 1.0 if is_auto_fit else float(getattr(ep_settings, "ai_voice_speed", 1.05))
                    self.log_message.emit(
                        f"🗣️ ភាគ {item.index}: កំពុងបង្កើតសំឡេង AI ({'Auto-fit' if is_auto_fit else f'{global_speed:.2f}x'}, 5x Parallel)..."
                    )
                    tts_cb = _make_sub_cb(row_idx, ST_TTS, 60, 19, item.filename)
                    ep_tts_dir = tts_engine.output_dir / f"ep_{item.index}"
                    ep_tts = TTSEngine(output_dir=ep_tts_dir)
                    ep_tts.generate_segments_batch(
                        proj.segments,
                        speed_fn=lambda _seg, sp=global_speed: sp,
                        max_workers=5,
                        progress_cb=lambda p, m: tts_cb(int(p * 0.90), m),
                        cancel_cb=lambda: self._cancelled,
                    )
                    if not self._wait_if_paused():
                        break
                    sync_engine.synchronize_segments(proj)
                    audio_engine.align_speech_and_subtitles(proj.segments)
                    tts_cb(100, "តម្រឹមសំឡេងរួចរាល់")

                if not self._wait_if_paused():
                    break

                if self.config.run_export_srt and proj.segments:
                    _write_srt_file(proj.segments, item.out_km_srt, use_source=False)

                # 4. Render Video
                if self.config.run_render:
                    item.status = ST_RENDERING
                    item.progress = 80
                    self.item_updated.emit(row_idx, ST_RENDERING, 80, "")
                    self.log_message.emit(f"🎬 ភាគ {item.index}: កំពុង Render វីដេអូ MP4 ({format_audio_summary(ep_settings, bool(getattr(ep_settings, '_batch_audio_auto_fit', False)))})...")
                    if not video_has_audio and (not proj.audio_path or not os.path.exists(proj.audio_path)):
                        silent_dur = item.duration_sec or probe_video_duration(item.video_path) or 5.0
                        silent_path = str(Path(item.out_mp4).parent / f".silent_{item.index}.wav")
                        proj.audio_path = create_silent_wav(silent_dur, silent_path)
                    ren_cb = _make_sub_cb(row_idx, ST_RENDERING, 80, 19, item.filename)
                    export_engine.export_all(
                        project=proj,
                        output_mp4_path=item.out_mp4,
                        export_srt_path=item.out_km_srt if self.config.run_export_srt else None,
                        progress_cb=ren_cb,
                    )

                item.status = ST_DONE
                item.progress = 100
                done_cnt += 1
                completed_so_far += 1
                self.item_updated.emit(row_idx, ST_DONE, 100, "")
                self.log_message.emit(f"✅ រួចរាល់: {item.filename}")
                save_batch_state(self.config, self.items, state_file=self.state_file)

            except Exception as exc:
                safe_err = redact_secrets(str(exc))
                log.error("Batch item failed (%s): %s", item.filename, safe_err)
                item.status = ST_FAILED
                item.error_msg = safe_err[:240]
                failed_cnt += 1
                completed_so_far += 1
                self.item_updated.emit(row_idx, ST_FAILED, item.progress, item.error_msg)
                self.log_message.emit(f"❌ បរាជ័យ ({item.filename}): {item.error_msg}")
                save_batch_state(self.config, self.items, state_file=self.state_file)
                continue

            self._emit_overall(completed_so_far, total_targets, item.filename, start_ts)

        self._emit_overall(completed_so_far, total_targets, "", start_ts, force_pct=100 if not self._cancelled else None)
        save_batch_state(self.config, self.items, state_file=self.state_file)
        self.batch_finished.emit(done_cnt, failed_cnt, skipped_cnt)

    def _emit_overall(self, done_count: float, total: int, cur_name: str, start_ts: float, force_pct: Optional[int] = None):
        elapsed = max(0.0, time.time() - start_ts)
        pct = force_pct if force_pct is not None else int((done_count / max(1, total)) * 100)
        if done_count >= 0.08 and done_count < total:
            avg = elapsed / done_count
            eta = max(0.0, avg * (total - done_count))
        else:
            eta = 0.0
        self.overall_progress.emit(min(100, max(0, pct)), cur_name, elapsed, eta)
