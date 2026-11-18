"""
Modular Dubbing Engine Suite:
- SegmentPlanner: High-recall VAD & speech onset detector (Zero segment loss)
- TTSFitter: Resilient TTS synthesizer, LUFS normalization & slot fitting
- BackgroundSeparator: Demucs AI & FFmpeg 3-band mid-side accompaniment preservation
- TimelineMixer: Sample-accurate 48 kHz stereo mixing & sidechain ducking
- SubtitleRenderer: Chinese subtitle masking & Noto Sans Khmer styling
- QAChecker: Acceptance verification & dubbing report generator
"""

from app.dubbing.segment_planner import SegmentPlanner
from app.dubbing.tts_fitter import TTSFitter
from app.dubbing.background_separator import BackgroundSeparator
from app.dubbing.mixer import TimelineMixer
from app.dubbing.subtitle_renderer import SubtitleRenderer
from app.dubbing.qa_checker import QAChecker
from app.dubbing.speech_rate import khmer_syllable_count, get_speech_budget, calculate_speech_rate, extract_khmer_syllables

__all__ = [
    "SegmentPlanner",
    "TTSFitter",
    "BackgroundSeparator",
    "TimelineMixer",
    "SubtitleRenderer",
    "QAChecker",
    "khmer_syllable_count",
    "get_speech_budget",
    "calculate_speech_rate",
    "extract_khmer_syllables"
]
