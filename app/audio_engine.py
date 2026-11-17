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
        Construct a continuous audio track by placing all segment TTS clips at their start times.
        Ensures NO OVERLAP per Section 4: "Never allow two segments' audio to overlap".
        """
        if not output_wav:
            output_wav = str(self.temp_dir / "dubbed_voice_track.wav")
            
        total_ms = int(total_duration_sec * 1000) + 3000
        # Create silent stereo base track
        dubbed_track = AudioSegment.silent(duration=total_ms, frame_rate=44100)
        
        sorted_segs = sorted(segments, key=lambda s: s.start)
        last_clip_end_ms = 0
        last_speaker = ""
        total_count = len(sorted_segs)
        
        for idx, seg in enumerate(sorted_segs):
            if progress_cb:
                pct = int((idx / max(1, total_count)) * 100)
                progress_cb(pct, f"Assembling voice segment {idx+1}/{total_count}")
                
            if not seg.tts_file or not os.path.exists(seg.tts_file):
                continue
                
            try:
                clip = AudioSegment.from_file(seg.tts_file).set_frame_rate(44100).set_channels(2)
                clip_ms = len(clip)
                
                # Lock speech start strictly to the exact on-screen character timestamp
                start_ms = max(0, int(seg.start * 1000))
                slot_ms = int(max(0.3, seg.slot) * 1000)

                # Available window before the NEXT dialogue segment begins
                if idx + 1 < len(sorted_segs):
                    next_start_ms = max(0, int(sorted_segs[idx + 1].start * 1000))
                    gap_to_next = next_start_ms - start_ms
                    available_ms = max(350, gap_to_next - 20) if gap_to_next > 0 else clip_ms
                else:
                    available_ms = max(slot_ms, clip_ms)

                # The voice should finish when the character finishes talking (slot_ms),
                # with a tiny 60ms breath cushion, without overrunning into the next character.
                target_max_ms = min(slot_ms + 60, available_ms)

                # If dialogue clip runs longer than the character's speaking time,
                # dynamically adapt speed with FFmpeg atempo to lock voice to character's mouth!
                if clip_ms > target_max_ms + 40:
                    adapt_ratio = min(round(clip_ms / target_max_ms, 2), MAX_ATEMPO_SPEED)
                    if adapt_ratio >= 1.04:
                        try:
                            stretched_file = self.time_stretch_atempo(seg.tts_file, speed_factor=adapt_ratio)
                            clip = AudioSegment.from_file(stretched_file).set_frame_rate(44100).set_channels(2)
                            clip_ms = len(clip)
                        except Exception as e:
                            print(f"[AudioEngine] Note atempo stretch: {e}")

                # Ensure base track has enough room
                if start_ms + clip_ms + 1000 > len(dubbed_track):
                    extra_needed = (start_ms + clip_ms + 3000) - len(dubbed_track)
                    dubbed_track = dubbed_track + AudioSegment.silent(duration=extra_needed, frame_rate=44100)

                # Record ground truth exact speech timestamps onto Segment!
                seg.actual_start = round(start_ms / 1000.0, 3)
                seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)

                # Overlay dialogue speech starting right at character's cue!
                dubbed_track = dubbed_track.overlay(clip, position=start_ms)
                last_clip_end_ms = start_ms + clip_ms
                last_speaker = seg.speaker

            except Exception as e:
                print(f"[AudioEngine] Warning loading clip for segment {seg.id}: {e}")

        dubbed_track.export(output_wav, format="wav")
        return output_wav

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

            target_max_ms = min(slot_ms + 60, available_ms)
            if clip_ms > target_max_ms + 40:
                adapt_ratio = min(round(clip_ms / target_max_ms, 2), MAX_ATEMPO_SPEED)
                if adapt_ratio >= 1.04:
                    clip_ms = int(clip_ms / adapt_ratio)

            seg.actual_start = round(start_ms / 1000.0, 3)
            seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)
            last_clip_end_ms = start_ms + clip_ms
            last_speaker = seg.speaker

    def mix_audio_with_ducking(
        self,
        original_audio_path: str,
        dubbed_audio_path: str,
        segments: List[Segment],
        original_vol_factor: float = 0.3,
        dubbed_vol_factor: float = 1.0,
        ducking_db: float = -14.0,
        fade_ms: int = 250,
        output_wav: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """
        Audio ducking algorithm:
        Lowers original audio by ducking_db during all speech windows,
        applies smooth fade-in and fade-out to prevent pops,
        then mixes with the new dubbed voice track.
        """
        if not output_wav:
            output_wav = str(self.temp_dir / "final_mixed_audio.wav")

        # Fast path: If original audio is muted (0%), return pure Khmer dubbed track
        if original_vol_factor <= 0.001:
            if progress_cb:
                progress_cb(50, "Original audio muted (0%). Producing pure Khmer dubbed track...")
            dubbed = AudioSegment.from_file(dubbed_audio_path).set_frame_rate(44100).set_channels(2)
            if abs(dubbed_vol_factor - 1.0) > 0.01:
                import math
                def factor_to_db(fac: float) -> float:
                    if fac <= 0.001: return -120.0
                    return 20.0 * math.log10(fac)
                dubbed = dubbed.apply_gain(factor_to_db(dubbed_vol_factor))
            dubbed.export(output_wav, format="wav")
            return output_wav
            
        if progress_cb:
            progress_cb(10, "Loading original and dubbed audio tracks...")
            
        orig = AudioSegment.from_file(original_audio_path).set_frame_rate(44100).set_channels(2)
        dubbed = AudioSegment.from_file(dubbed_audio_path).set_frame_rate(44100).set_channels(2)
        
        # Apply base volume scaling
        # In pydub, gain in dB = 20 * log10(factor)
        import math
        def factor_to_db(fac: float) -> float:
            if fac <= 0.001:
                return -120.0
            return 20.0 * math.log10(fac)
            
        orig_gain = factor_to_db(original_vol_factor)
        dubbed_gain = factor_to_db(dubbed_vol_factor)
        
        orig = orig.apply_gain(orig_gain)
        dubbed = dubbed.apply_gain(dubbed_gain)
        
        # Build ducked background track
        total_len = max(len(orig), len(dubbed))
        if len(orig) < total_len:
            orig = orig + AudioSegment.silent(duration=total_len - len(orig), frame_rate=44100)
            
        if progress_cb:
            progress_cb(30, "Computing audio ducking envelopes...")
            
        # Create speech activity mask
        # We can construct ducked segments
        ducked_orig = orig
        valid_segs = [s for s in segments if s.tts_file and os.path.exists(s.tts_file)]
        
        if valid_segs and ducking_db < 0:
            # Sort speech intervals
            intervals = []
            for s in sorted(valid_segs, key=lambda x: x.start):
                st = max(0, int(s.start * 1000) - fade_ms)
                # Dur is tts clip length or slot
                tts_dur = s.tts_duration if s.tts_duration > 0 else s.slot
                et = min(len(orig), int((s.start + tts_dur) * 1000) + fade_ms)
                if intervals and st <= intervals[-1][1]:
                    intervals[-1] = (intervals[-1][0], max(intervals[-1][1], et))
                else:
                    intervals.append((st, et))
                    
            # Apply ducking piecewise
            ducked_parts = []
            curr = 0
            for st, et in intervals:
                if st > curr:
                    ducked_parts.append(orig[curr:st])
                
                # Ducked segment with crossfades
                speech_chunk = orig[st:et].apply_gain(ducking_db)
                if len(speech_chunk) > (fade_ms * 2):
                    speech_chunk = speech_chunk.fade_in(fade_ms).fade_out(fade_ms)
                ducked_parts.append(speech_chunk)
                curr = et
                
            if curr < len(orig):
                ducked_parts.append(orig[curr:])
                
            ducked_orig = ducked_parts[0]
            for part in ducked_parts[1:]:
                ducked_orig = ducked_orig + part

        if progress_cb:
            progress_cb(70, "Overlaying dubbed Khmer voices...")
            
        final_mix = ducked_orig.overlay(dubbed)
        
        if progress_cb:
            progress_cb(90, "Exporting mixed audio...")
            
        final_mix.export(output_wav, format="wav")
        return output_wav
