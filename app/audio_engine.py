"""
Audio and Video Engine: FFmpeg extraction, mixing, ducking, time-stretching
Covers F2, F9, F10 and Time-Sync Rule 4 (FFmpeg atempo)
"""

import os
import subprocess
import json
from pathlib import Path
from typing import Optional, List, Callable
from pydub import AudioSegment
from pydub.silence import detect_leading_silence
from app.config import TEMP_DIR, MAX_ATEMPO_SPEED
from app.models import Segment

class AudioEngine:
    def __init__(self, temp_dir: Optional[Path] = None):
        self.temp_dir = temp_dir or TEMP_DIR

    def extract_audio(self, video_path: str, output_wav: Optional[str] = None, sample_rate: int = 16000) -> str:
        """
        Extract mono WAV audio from video (optimized for Whisper STT and speech analysis).
        """
        if not output_wav:
            video_name = Path(video_path).stem
            output_wav = str(self.temp_dir / f"{video_name}_extracted_16k.wav")
            
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-af", "highpass=f=70,lowpass=f=7600",
            "-ar", str(sample_rate),
            "-ac", "1",
            "-c:a", "pcm_s16le",
            output_wav
        ]
        
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
            
        return output_wav

    def extract_original_full_audio(self, video_path: str, output_wav: Optional[str] = None) -> str:
        """
        Extract stereo 44.1/48kHz audio for final high quality mixing.
        """
        if not output_wav:
            video_name = Path(video_path).stem
            output_wav = str(self.temp_dir / f"{video_name}_original_stereo.wav")
            
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            output_wav
        ]
        
        subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            encoding="utf-8", errors="replace"
        )
        return output_wav

    def get_media_duration(self, media_path: str) -> float:
        """Get media duration in seconds via ffprobe or ffmpeg."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path
        ]
        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
                encoding="utf-8", errors="replace"
            )
            return float(res.stdout.strip())
        except Exception:
            # Fallback with pydub
            try:
                seg = AudioSegment.from_file(media_path)
                return len(seg) / 1000.0
            except Exception:
                return 0.0

    def time_stretch_atempo(self, input_wav: str, speed_factor: float, output_wav: Optional[str] = None) -> str:
        """
        Time-stretch audio clip using FFmpeg atempo filter.
        Speed factor supports 0.50x to 2.00x pitch-preserving scaling.
        Avoids compounding chained filenames (_stretched_1.30_stretched_1.30...).
        """
        import re
        import os
        speed_factor = max(0.5, min(float(speed_factor), 2.0))
        
        p = Path(input_wav)
        clean_stem = re.sub(r'_stretched_[\d\.]+', '', p.stem)
        
        # If an uncompressed base file exists, stretch from the clean base file
        base_wav = p.parent / f"{clean_stem}.wav"
        src_file = str(base_wav) if (base_wav.exists() and str(base_wav) != input_wav) else input_wav

        if not output_wav:
            output_wav = str(p.parent / f"{clean_stem}_stretched_{speed_factor:.2f}.wav")

        # In-place safe output target
        target_wav = output_wav
        temp_out = output_wav + ".tmp.wav" if os.path.abspath(src_file) == os.path.abspath(output_wav) else output_wav

        # FFmpeg atempo supports 0.5 to 2.0 per filter instance
        cmd = [
            "ffmpeg", "-y",
            "-i", src_file,
            "-filter:a", f"atempo={speed_factor:.3f}",
            temp_out
        ]
        subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            encoding="utf-8", errors="replace"
        )
        if temp_out != target_wav:
            os.replace(temp_out, target_wav)
        return target_wav

    def build_dubbed_audio_track(
        self,
        total_duration_sec: float,
        segments: List[Segment],
        output_wav: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """
        Construct a continuous audio track by placing all segment TTS clips at their exact start times.
        Uses the sample-accurate 48 kHz TimelineMixer engine (zero drift, no digital clicks).
        """
        from app.dubbing.mixer import TimelineMixer
        mixer = TimelineMixer(temp_dir=self.temp_dir, sample_rate=48000)
        return mixer.build_dialogue_track(
            total_duration_sec=total_duration_sec,
            segments=segments,
            output_wav=output_wav,
            progress_cb=progress_cb
        )

    def align_speech_and_subtitles(self, segments: List[Segment]) -> None:
        """
        Pre-compute exact actual_start and actual_end for all segments
        locked directly to the character's video timestamps (seg.start) and slot duration.
        """
        sorted_segs = sorted(segments, key=lambda s: s.start)
        for idx, seg in enumerate(sorted_segs):
            dur_sec = seg.tts_duration if seg.tts_duration > 0.001 else seg.slot
            clip_ms = int(dur_sec * 1000)
            start_ms = max(0, int(seg.start * 1000))
            slot_ms = int(max(0.3, seg.slot) * 1000)

            if idx + 1 < len(sorted_segs):
                next_start_ms = max(0, int(sorted_segs[idx + 1].start * 1000))
                gap_to_next = next_start_ms - start_ms
                available_ms = max(350, gap_to_next - 20) if gap_to_next > 0 else clip_ms
            else:
                available_ms = max(slot_ms, clip_ms)

            target_max_ms = min(slot_ms + 25, available_ms)
            if clip_ms > target_max_ms + 25:
                adapt_ratio = min(round(clip_ms / target_max_ms, 2), 1.85)
                if adapt_ratio >= 1.02:
                    clip_ms = int(clip_ms / adapt_ratio)

            seg.actual_start = round(start_ms / 1000.0, 3)
            seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)

    def mix_audio_with_ducking(
        self,
        original_audio_path: str,
        dubbed_audio_path: str,
        segments: List[Segment],
        original_vol_factor: float = 0.3,
        dubbed_vol_factor: float = 1.0,
        ducking_db: float = -14.0,
        fade_ms: int = 250,
        original_voice_vol: float = 0.0,
        background_vol: float = 0.30,
        mute_original: bool = False,
        keep_background: bool = True,
        output_wav: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """
        High-fidelity background preservation and sidechain ducking:
        1. Separates original audio into accompaniment (BGM + SFX) and vocals (Demucs / FFmpeg DSP).
        2. Preserves 100% of BGM & room tone (no digital silence) unless mute_original is True.
        3. If mute_original is True (or background_vol=0 and original_voice_vol=0), completely eliminates
           background audio, resulting in absolute digital silence (< -80 dB) outside AI speech slots.
        4. Smoothly ducks accompaniment by ducking_db with 50ms attack / 300ms release during speech.
        5. Applies EBU R128 master loudnorm and True Peak limiter.
        """
        if not output_wav:
            output_wav = str(self.temp_dir / "final_mixed_audio.wav")

        total_duration = self.get_media_duration(original_audio_path)
        if total_duration <= 0.01:
            total_duration = self.get_media_duration(dubbed_audio_path)

        from app.dubbing.background_separator import BackgroundSeparator
        from app.dubbing.mixer import TimelineMixer

        # Harmonize legacy original_vol_factor with dedicated background_vol / original_voice_vol
        eff_bg_vol = background_vol
        if original_vol_factor != 0.3 and background_vol == 0.3:
            eff_bg_vol = original_vol_factor

        acc_path = None
        voc_path = None
        need_bg = not mute_original and (eff_bg_vol > 0.001 or original_voice_vol > 0.001)

        # Step 1: Vocal and Accompaniment Separation (ONLY when background or vocals are needed)
        if need_bg and original_audio_path and os.path.exists(original_audio_path):
            separator = BackgroundSeparator(temp_dir=self.temp_dir)
            acc_path, voc_path = separator.separate(
                original_audio_path,
                progress_cb=lambda p, msg: progress_cb(int(p * 0.4), msg) if progress_cb else None
            )

        # Step 2: Timeline Mixing with Dedicated Stem Controls
        mixer = TimelineMixer(temp_dir=self.temp_dir, sample_rate=48000)
        final_wav = mixer.mix_timeline(
            total_duration_sec=total_duration,
            accompaniment_path=acc_path,
            dialogue_path=dubbed_audio_path,
            segments=segments,
            vocals_path=voc_path,
            original_voice_vol=original_voice_vol,
            background_vol=eff_bg_vol,
            ai_voice_vol=dubbed_vol_factor,
            mute_original=mute_original,
            keep_background=keep_background,
            ducking_db=ducking_db,
            target_lufs=-16.0,
            output_wav=output_wav,
            progress_cb=lambda p, msg: progress_cb(40 + int(p * 0.6), msg) if progress_cb else None
        )

        return final_wav
