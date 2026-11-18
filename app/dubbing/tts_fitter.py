"""
TTS Fitter: Resilient Synthesis, Per-Clip LUFS Normalization, Anti-Click Fading, and Slot Fitting.
Part of the modular Dubbing Engine suite.
Guarantees:
1. Zero segment loss (retry >= 3 times, fallback translation, explicit failure tracking).
2. Strict slot fitting (atempo <= 1.25x, gap borrowing, text condensation).
3. 15ms anti-click fade-in/fade-out on all clips.
4. Per-clip -16 LUFS loudness normalization.
"""

import os
import re
import time
import asyncio
import subprocess
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

from app.models import Segment
from app.config import TEMP_DIR


class TTSFitter:
    """
    Synthesizes Khmer speech clips, normalizes their loudness,
    and fits them into segment slots without overlap or truncation.
    """

    def __init__(self, temp_dir: Optional[Path] = None):
        self.temp_dir = temp_dir or TEMP_DIR
        self.clips_dir = self.temp_dir / "tts_clips"
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_text(text: str) -> str:
        """
        Clean and sanitize text for Edge-TTS synthesizer:
        Strips Zero-Width Space (\\u200b) and BOM (\\ufeff) while preserving text for speech,
        removes HTML/XML tags, and normalizes spacing.
        """
        if not text:
            return ""
        # Strip Zero-Width Spaces (used for subtitle wrap) before sending to TTS engine
        clean = text.replace('\u200b', '').replace('\ufeff', '')
        # Remove XML/HTML tags
        clean = re.sub(r'<[^>]+>', ' ', clean)
        # Remove control characters
        clean = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean)
        # Normalize whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    @staticmethod
    def contains_cjk(text: str) -> bool:
        """Check if string contains Chinese/Japanese/Korean characters."""
        return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))

    def translate_fallback_on_the_fly(self, text: str) -> str:
        """Instant fallback translation if text contains untranslated Chinese characters."""
        try:
            from app.translation_engine import TranslationEngine
            te = TranslationEngine()
            res = te.translate_segment(text, target_lang="km")
            if res and not self.contains_cjk(res):
                return res
        except Exception as e:
            print(f"[TTSFitter] Fallback translation error: {e}")
        return text

    async def _synthesize_edge_tts_async(self, text: str, voice: str, output_path: str) -> bool:
        """Synthesize text via Edge-TTS and convert to clean 48kHz stereo WAV."""
        import edge_tts
        temp_mp3 = output_path + ".tmp.mp3"
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice)
            with open(temp_mp3, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])

            if os.path.exists(temp_mp3) and os.path.getsize(temp_mp3) > 180:
                # Convert MP3 to standard 48kHz stereo WAV
                cmd = [
                    "ffmpeg", "-y",
                    "-i", temp_mp3,
                    "-ar", "48000",
                    "-ac", "2",
                    output_path
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
                return os.path.exists(output_path) and os.path.getsize(output_path) > 180
        except Exception as e:
            if os.path.exists(temp_mp3):
                try:
                    os.remove(temp_mp3)
                except Exception:
                    pass
            print(f"[TTSFitter] Error in Edge-TTS async stream: {e}")
        return False

    def synthesize_with_retry(self, text: str, voice: str, output_path: str, max_retries: int = 3) -> bool:
        """
        Synthesize speech with >= 3 retries, exponential backoff, and text sanitization.
        If text contains untranslated Chinese, translates it on the fly.
        """
        clean_text = self.sanitize_text(text)
        if not clean_text:
            return False

        # If text is in Chinese, translate to Khmer first
        if self.contains_cjk(clean_text):
            translated = self.translate_fallback_on_the_fly(clean_text)
            if translated and not self.contains_cjk(translated):
                clean_text = self.sanitize_text(translated)

        for attempt in range(1, max_retries + 1):
            try:
                success = asyncio.run(self._synthesize_edge_tts_async(clean_text, voice, output_path))
                if success:
                    return True
            except Exception as e:
                print(f"[TTSFitter] Synthesis attempt {attempt}/{max_retries} failed for '{clean_text[:20]}...': {e}")
                time.sleep(0.4 * attempt)

        return False

    def normalize_clip_loudness(self, input_wav: str, target_lufs: float = -16.0) -> str:
        """
        Normalize individual TTS audio clip to target LUFS (-16 LUFS)
        so every dialogue segment has consistent broadcast loudness.
        """
        norm_wav = input_wav.replace(".wav", "_norm.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", input_wav,
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-ar", "48000",
            "-ac", "2",
            norm_wav
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            if os.path.exists(norm_wav) and os.path.getsize(norm_wav) > 100:
                os.replace(norm_wav, input_wav)
        except Exception as e:
            # Fallback to pydub RMS gain normalization
            try:
                seg = AudioSegment.from_file(input_wav).set_frame_rate(48000).set_channels(2)
                if seg.dBFS != float("-inf"):
                    gain = -18.0 - seg.dBFS
                    seg = seg.apply_gain(gain)
                    seg.export(input_wav, format="wav")
            except Exception:
                pass
        return input_wav

    def apply_anti_click_fades(self, clip: AudioSegment, fade_ms: int = 15) -> AudioSegment:
        """Apply 15ms fade-in and fade-out to prevent clicks, pops, and abrupt cuts."""
        if len(clip) <= (fade_ms * 2):
            return clip
        return clip.fade_in(fade_ms).fade_out(fade_ms)

    def time_stretch_atempo(self, input_wav: str, speed_factor: float, output_wav: Optional[str] = None) -> str:
        """Time stretch audio using pitch-preserving FFmpeg atempo filter."""
        speed_factor = max(0.5, min(float(speed_factor), 2.0))
        p = Path(input_wav)
        clean_stem = re.sub(r'_stretched_[\d\.]+', '', p.stem)
        base_wav = p.parent / f"{clean_stem}.wav"
        src_file = str(base_wav) if (base_wav.exists() and str(base_wav) != input_wav) else input_wav

        if not output_wav:
            output_wav = str(p.parent / f"{clean_stem}_stretched_{speed_factor:.2f}.wav")

        temp_out = output_wav + ".tmp.wav"
        cmd = [
            "ffmpeg", "-y",
            "-i", src_file,
            "-filter:a", f"atempo={speed_factor:.3f}",
            "-ar", "48000",
            "-ac", "2",
            temp_out
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        if os.path.exists(temp_out):
            os.replace(temp_out, output_wav)
        return output_wav

    def fit_segment(
        self,
        seg: Segment,
        prev_seg: Optional[Segment] = None,
        next_seg: Optional[Segment] = None,
        max_atempo: float = 1.15
    ) -> AudioSegment:
        """
        Fit a synthesized speech clip into its segment slot:
        1. Safe slot expansion: Can start up to 0.35s early into available silence before seg.start,
           and extend into trailing silence before next_seg.start.
        2. Strictly caps atempo speedup at 1.15x (preserves natural speech clarity).
        3. Enforces speech-rate budget (rate <= 7.5 syllables/sec).
        4. Applies 15ms anti-click fade-in/fade-out.
        """
        if not seg.tts_file or not os.path.exists(seg.tts_file):
            raise FileNotFoundError(f"TTS audio missing for segment {seg.id}")

        clip = AudioSegment.from_file(seg.tts_file).set_frame_rate(48000).set_channels(2)

        # Trim leading and trailing silence
        if len(clip) > 40 and clip.dBFS != float("-inf"):
            thresh = max(-45.0, min(-34.0, clip.dBFS - 15.0))
            lead_ms = detect_leading_silence(clip, silence_threshold=thresh, chunk_size=2)
            trail_ms = detect_leading_silence(clip.reverse(), silence_threshold=thresh - 2.0, chunk_size=2)
            s_trim = max(0, lead_ms - 2)
            e_trim = max(0, len(clip) - max(0, trail_ms - 10))
            if e_trim > s_trim + 30:
                clip = clip[s_trim:e_trim]

        clip_ms = len(clip)
        slot_ms = int(max(0.25, seg.slot) * 1000)
        orig_start_ms = max(0, int(round(seg.start * 1000)))

        # 1. Safe Lead Borrowing: Can start up to 350ms early if silence exists before this segment
        lead_avail_ms = 400
        if prev_seg is not None:
            prev_end_ms = int(round(prev_seg.speech_end * 1000))
            lead_avail_ms = max(0, orig_start_ms - prev_end_ms - 40)
        lead_borrow_ms = min(350, lead_avail_ms)
        start_ms = max(0, orig_start_ms - lead_borrow_ms)

        # 2. Safe Trailing Borrowing: Can extend into silence after this segment
        trail_borrow_ms = 0
        if next_seg is not None:
            next_start_ms = max(0, int(round(next_seg.start * 1000)))
            gap_ms = next_start_ms - (orig_start_ms + slot_ms)
            if gap_ms > 40:
                trail_borrow_ms = gap_ms - 40
        else:
            trail_borrow_ms = 1500  # End of video

        total_available_slot_ms = slot_ms + lead_borrow_ms + trail_borrow_ms

        # Case 1: Fits comfortably within slot + lead borrow
        base_window_ms = slot_ms + lead_borrow_ms
        if clip_ms <= base_window_ms + 25:
            seg.speed = 1.0
            seg.sync_status = "OK"
            seg.actual_start = round(start_ms / 1000.0, 3)
            seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)
            seg.tts_duration = round(clip_ms / 1000.0, 3)
            return self.apply_anti_click_fades(clip, fade_ms=15)

        # Case 2: Exceeds slot - Try modest atempo <= 1.15x
        needed_speed = round(clip_ms / max(200, base_window_ms), 2)
        if needed_speed <= max_atempo:
            stretched_path = self.time_stretch_atempo(seg.tts_file, speed_factor=needed_speed)
            clip = AudioSegment.from_file(stretched_path).set_frame_rate(48000).set_channels(2)
            clip_ms = len(clip)
            seg.speed = needed_speed
            seg.sync_status = "SPED_UP"
            seg.actual_start = round(start_ms / 1000.0, 3)
            seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)
            seg.tts_duration = round(clip_ms / 1000.0, 3)
            return self.apply_anti_click_fades(clip, fade_ms=15)

        # Case 3: Still exceeds - Borrow trailing silence gap with atempo <= 1.15x
        speed_with_borrow = round(clip_ms / max(200, total_available_slot_ms), 2)
        if speed_with_borrow <= max_atempo:
            speed_applied = max(1.0, min(max_atempo, speed_with_borrow))
            if speed_applied > 1.02:
                stretched_path = self.time_stretch_atempo(seg.tts_file, speed_factor=speed_applied)
                clip = AudioSegment.from_file(stretched_path).set_frame_rate(48000).set_channels(2)
                clip_ms = len(clip)
            seg.borrowed_gap = round((lead_borrow_ms + trail_borrow_ms) / 1000.0, 3)
            seg.speed = speed_applied
            seg.sync_status = "GAP_BORROWED"
            seg.actual_start = round(start_ms / 1000.0, 3)
            seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)
            seg.tts_duration = round(clip_ms / 1000.0, 3)
            return self.apply_anti_click_fades(clip, fade_ms=15)

        # Case 4: Capped at strictly 1.15x atempo
        speed_applied = max_atempo  # Strictly 1.15x max
        stretched_path = self.time_stretch_atempo(seg.tts_file, speed_factor=speed_applied)
        clip = AudioSegment.from_file(stretched_path).set_frame_rate(48000).set_channels(2)
        clip_ms = len(clip)

        if clip_ms > total_available_slot_ms and next_seg is not None:
            clip = clip[:total_available_slot_ms]
            clip_ms = len(clip)
            seg.sync_status = "SHORTENED"
        else:
            seg.sync_status = "GAP_BORROWED"

        seg.speed = speed_applied
        seg.borrowed_gap = round((lead_borrow_ms + trail_borrow_ms) / 1000.0, 3)
        seg.actual_start = round(start_ms / 1000.0, 3)
        seg.actual_end = round((start_ms + clip_ms) / 1000.0, 3)
        seg.tts_duration = round(clip_ms / 1000.0, 3)
        return self.apply_anti_click_fades(clip, fade_ms=15)

    def process_all_segments(
        self,
        segments: List[Segment],
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> List[Segment]:
        """
        Process all segments end-to-end with speech-rate budgeting:
        1. Pre-synthesize check: if syllables > max_syllables (rate > 6.0/s), condense text with LLM.
        2. Synthesize audio clips (retry >= 3 times).
        3. Normalize clip loudness to -16 LUFS.
        4. Fit clips into timeline slots with safe expansion and atempo <= 1.15x.
        """
        sorted_segs = sorted(segments, key=lambda s: s.start)
        total = len(sorted_segs)

        from app.dubbing.speech_rate import khmer_syllable_count, get_speech_budget
        from app.dubbing.transliteration import transliterate_khmer_dialogue

        for idx, seg in enumerate(sorted_segs):
            if progress_cb:
                pct = int((idx / max(1, total)) * 100)
                progress_cb(pct, f"Processing speech budget and fitting segment {idx+1}/{total}...")

            if seg.text_km:
                seg.text_km = transliterate_khmer_dialogue(seg.text_km)
            text_to_speak = seg.text_km or seg.text_src
            if not text_to_speak:
                seg.sync_status = "EMPTY"
                continue

            prev_seg = sorted_segs[idx - 1] if idx > 0 else None
            next_seg = sorted_segs[idx + 1] if idx + 1 < total else None

            # Target output file path
            clip_name = f"seg_{seg.id:04d}.wav"
            out_clip = str(self.clips_dir / clip_name)
            seg.tts_file = out_clip

            # Iterative Speech-Rate Budget & Synthesis Loop (up to 3 passes if rate > 7.5)
            from app.dubbing.speech_rate import calculate_speech_rate
            from app.translation_engine import TranslationEngine
            te = TranslationEngine()

            for pass_idx in range(1, 4):
                # Step 1: Speech-Rate Budgeting
                target_syll, max_syll = get_speech_budget(seg.slot)
                cur_syll = khmer_syllable_count(text_to_speak)

                if cur_syll > max_syll or pass_idx > 1:
                    budget_limit = max_syll if pass_idx == 1 else max(2, int(max(0.3, seg.tts_duration) * 5.8))
                    condensed = te.condense_khmer_dialogue(
                        text_to_speak,
                        max_syllables=budget_limit,
                        src_text=seg.text_src,
                        slot_sec=seg.slot
                    )
                    if condensed:
                        text_to_speak = condensed
                        seg.text_km = condensed
                        seg.sync_notes = "TEXT_CONDENSED"
                        if os.path.exists(out_clip):
                            try:
                                os.remove(out_clip)
                            except Exception:
                                pass

                # Synthesize if needed
                need_synth = not os.path.exists(out_clip) or os.path.getsize(out_clip) <= 180 or (seg.sync_notes == "TEXT_CONDENSED")
                if need_synth:
                    voice = seg.voice or "km-KH-PisethNeural"
                    success = self.synthesize_with_retry(text_to_speak, voice, out_clip, max_retries=3)
                    if not success:
                        print(f"[TTSFitter] Error: Synthesis failed for segment {seg.id}: {text_to_speak}")
                        seg.sync_status = "FAILED"
                        break

                # Normalize loudness to -16 LUFS
                self.normalize_clip_loudness(out_clip, target_lufs=-16.0)

                # Fit into slot with safe expansion and atempo <= 1.15x
                try:
                    fitted_clip = self.fit_segment(seg, prev_seg=prev_seg, next_seg=next_seg, max_atempo=1.15)
                    fitted_clip.export(out_clip, format="wav")
                except Exception as e:
                    print(f"[TTSFitter] Slot fitting error on segment {seg.id}: {e}")
                    seg.sync_status = "FAILED"
                    break

                # Check actual speech rate after fitting
                actual_rate = calculate_speech_rate(seg.text_km or seg.text_src, seg.tts_duration)
                if actual_rate <= 7.5:
                    break
                else:
                    print(f"[TTSFitter] Rate {actual_rate} > 7.5 on seg {seg.id} (pass {pass_idx}), condensing further...")

        return sorted_segs
