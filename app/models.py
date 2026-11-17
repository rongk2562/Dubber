"""
Data Models for AI Movie Translator & Auto Dubbing App
Conforms to SRS Section 7 Data Model (Segment) and Project Architecture
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
import json
import time

@dataclass
class Segment:
    """
    Segment conforming to SRS Section 7 Data Model
    """
    id: int
    start: float                 # Start timestamp in seconds (e.g. 63.40)
    end: float                   # End timestamp in seconds (e.g. 66.85)
    speaker: str = "S1"          # Speaker identifier (e.g. "S1", "S2")
    text_src: str = ""           # Original transcribed text (e.g. "Where are you going?")
    text_km: str = ""            # Translated Khmer text
    voice: str = "km-KH-PisethNeural" # Voice id or "female_01"
    tts_file: str = ""           # Path/filename to generated TTS audio file
    tts_duration: float = 0.0    # Actual duration of TTS clip in seconds
    speed: float = 1.0           # Playback speed adjustment
    pitch: float = 0.0           # Pitch adjustment
    
    # Extended fields for 4-Stage Synchronization & UI Tracking
    sync_status: str = "OK"      # "OK", "SPEED_ADJUSTED", "SHORTENED", "GAP_BORROWED", "TIME_STRETCHED", "OVERFLOW"
    sync_notes: str = ""         # Details on adjustments made
    borrowed_gap: float = 0.0    # Seconds borrowed from next segment gap
    is_modified: bool = False    # True if user manually adjusted text/time

    # Aligned Speech & Subtitle Synchronization Timestamps
    actual_start: float = 0.0    # Exact synchronized speech start timestamp in seconds
    actual_end: float = 0.0      # Exact synchronized speech end timestamp in seconds

    @property
    def speech_start(self) -> float:
        """Exact start timestamp of the spoken voice (seconds). Defaults to start."""
        if self.actual_start > 0.001:
            return round(self.actual_start, 3)
        return round(self.start, 3)

    @property
    def speech_end(self) -> float:
        """Exact end timestamp of the spoken voice (seconds)."""
        if self.actual_end > 0.001:
            return round(self.actual_end, 3)
        dur = self.tts_duration if self.tts_duration > 0.001 else self.slot
        return round(self.speech_start + dur, 3)

    @property
    def slot(self) -> float:
        """Available time slot in seconds: end - start"""
        return max(0.0, round(self.end - self.start, 3))

    @property
    def effective_duration(self) -> float:
        """Effective TTS duration considering speed factor"""
        if self.speed <= 0:
            return self.tts_duration
        return round(self.tts_duration / self.speed, 3)

    @property
    def exceeds_slot(self) -> bool:
        """Check if TTS duration exceeds slot + borrowed gap"""
        allowed = self.slot + self.borrowed_gap
        return (self.effective_duration - allowed) > 0.05

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Segment":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class Speaker:
    id: str                      # e.g. "S1", "S2"
    name: str                    # e.g. "Speaker 1" or Character Name
    voice: str = "km-KH-PisethNeural" # Default voice
    gender: str = "Male"         # "Male" or "Female"
    default_speed: float = 1.0
    default_pitch: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Speaker":
        return cls(**data)


@dataclass
class GlossaryItem:
    src_term: str                # e.g. "Captain America"
    km_term: str                 # e.g. "ប្រធានក្រុម អាមេរិក"
    category: str = "Character"  # "Character", "Location", "Phrase"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GlossaryItem":
        return cls(**data)


@dataclass
class ProjectSettings:
    whisper_model: str = "base"
    source_language: str = "auto"
    target_language: str = "km"     # Target translation & dubbing language ("km", "en", "zh-CN", "th", "vi", "ja", "ko", "fr", "es")
    translation_provider: str = "google"
    gemini_api_key: str = ""
    claude_api_key: str = ""
    openai_api_key: str = ""
    original_volume: float = 0.3    # Volume factor 0.0 to 1.5
    dubbed_volume: float = 1.0      # Volume factor 0.0 to 2.0
    mute_original: bool = False     # Completely mute original audio (pure Khmer dubbing only)
    ducking_db: float = -14.0       # Ducking reduction in dB during speech
    ducking_fade_ms: int = 250      # Ducking attack/release fade ms
    burn_subtitles: bool = True     # Burn Khmer subtitles onto video export
    subtitle_font: str = "Kantumruy Pro"
    ui_language: str = "km"         # "km" or "en"
    logo_enabled: bool = False      # Overlay custom logo / watermark
    logo_path: str = ""             # Image path (png, jpg, webp)
    logo_position: str = "top_right"# "top_right", "top_left", "bottom_right", "bottom_left", "center"
    logo_width: int = 120           # Logo width in pixels (40 to 350)
    logo_opacity: float = 0.90      # Transparency factor (0.1 to 1.0)
    logo_margin_x: int = 20         # Margin from horizontal edge
    logo_margin_y: int = 20         # Margin from vertical edge
    ai_voice_speed: float = 1.05    # Default AI voice speed factor (0.5 to 2.0)
    result_video_path: str = ""     # Output path of the generated result video
    auto_check_updates: bool = True # Automatically check for updates on startup
    update_check_url: str = ""      # Custom update manifest URL (optional)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectSettings":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class Project:
    name: str = "Untitled Project"
    video_path: str = ""
    audio_path: str = ""
    segments: List[Segment] = field(default_factory=list)
    speakers: Dict[str, Speaker] = field(default_factory=dict)
    glossary: List[GlossaryItem] = field(default_factory=list)
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    project_file_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "video_path": self.video_path,
            "audio_path": self.audio_path,
            "segments": [s.to_dict() for s in self.segments],
            "speakers": {k: v.to_dict() for k, v in self.speakers.items()},
            "glossary": [g.to_dict() for g in self.glossary],
            "settings": self.settings.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_file_path": self.project_file_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        segments = [Segment.from_dict(s) for s in data.get("segments", [])]
        speakers = {k: Speaker.from_dict(v) for k, v in data.get("speakers", {}).items()}
        glossary = [GlossaryItem.from_dict(g) for g in data.get("glossary", [])]
        settings = ProjectSettings.from_dict(data.get("settings", {}))
        
        return cls(
            name=data.get("name", "Untitled Project"),
            video_path=data.get("video_path", ""),
            audio_path=data.get("audio_path", ""),
            segments=segments,
            speakers=speakers,
            glossary=glossary,
            settings=settings,
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            project_file_path=data.get("project_file_path"),
        )
