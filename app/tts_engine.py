"""
Khmer AI Voice (TTS) Engine using edge-tts Neural Voices
F6: Choose voice, speed, pitch + 8-Profile Cinematic Prosody & Acoustic Intonation Contouring
Generates natural human-like rising and falling vocal intonation (ទម្លាក់តម្លើងសម្លេងតាមអារម្មណ៍)
with 0ms leading delay and exact lip-sync slot matching.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Dict
import numpy as np
from pydub import AudioSegment
from pydub.silence import detect_leading_silence
from app.config import (
    TEMP_DIR, KHMER_VOICES, MAX_TTS_SPEED, MAX_ATEMPO_SPEED,
    get_voice_pair_for_lang, normalize_to_male_voice
)
from app.models import Segment
from app.audio_engine import AudioEngine


class TTSEngine:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or (TEMP_DIR / "tts_clips")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_engine = AudioEngine(self.output_dir.parent)

    def get_available_voices(self) -> Dict[str, Dict[str, str]]:
        """Return available Khmer voices dictionary."""
        return KHMER_VOICES

    def _inject_khmer_breath_and_prosody_cues(self, text: str, is_khmer: bool) -> str:
        """
        Insert natural spoken breath pauses and clause rhythm cues so the neural TTS
        transformer naturally raises and lowers pitch across sub-clauses instead of
        reading a long Khmer string in one flat monotone.
        """
        if not is_khmer or len(text) < 10:
            return text

        # Add subtle comma/space breath pause before spoken Khmer clause connectors
        text = re.sub(
            r'(?<=\S)\s+(តែ|ព្រោះ|មកពី|បើ|ពេលដែល|អ៊ីចឹងហើយ|អ៊ីចឹង|ទោះម៉េចក៏ដោយ|រួចហើយ|បន្ទាប់មក)\b',
            r', \1',
            text
        )
        # Ensure clean spacing after exclamation/question marks inside multi-sentence segments
        text = re.sub(r'([\!\?។])\s*(?=[^\s\!\?។])', r'\1, ', text)
        text = re.sub(r',\s*,+', ',', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _prepare_emotional_prosody(
        self,
        segment: Segment,
        base_speed: float,
        base_pitch_hz: int
    ) -> Tuple[str, str, str, str, str]:
        """
        8-Profile Cinematic Emotion & Prosody Analyzer:
        Determines the conversational mood of the line and calculates dynamic
        pitch, rate, volume, and acoustic intonation contour profile.
        Returns: (speak_text, rate_str, pitch_str, volume_str, mood_profile)
        """
        text = segment.text_km.strip() or segment.text_src.strip()
        src = (segment.text_src or "").strip()

        voice_id = segment.voice or "km-KH-PisethNeural"
        is_khmer_voice = voice_id.startswith("km-")
        is_cjk_voice = voice_id.startswith(("zh-", "ja-"))

        if is_khmer_voice:
            from app.dubbing.transliteration import transliterate_khmer_dialogue
            text = transliterate_khmer_dialogue(text)

        # Classify emotional & conversational tone
        is_surprise = (
            "?!" in text or "!?" in text or "?!" in src or "!?" in src or
            any(kw in text for kw in ["ពិតមែនហ្អ៎", "និយាយលេងមែនទេ", "ឆ្កួតហើយមែនទេ", "មិនអាចសោះឡើយ", "មិនជឿសោះ", "ស្អីគេហ្នឹង", "អីយ៉ា", "ព្រះជាម្ចាស់"])
        )
        is_question = (
            text.endswith("?") or src.endswith("?") or
            any(kw in text for kw in ["តើ", "មែនទេ", "ឬទេ", "ទេ?", "ណា?", "អី?", "យ៉ាងម៉េច", "ហេតុអ្វី", "ទៅណា", "នៅណា", "ម៉េចក៏", "ហ្នឹង?", "ហ្អ៎?", "អត់ហ្នឹង", "អ្នកណាគេ"])
        )
        is_exclamation = (
            text.endswith("!") or src.endswith("!") or
            any(kw in text for kw in ["ប្រយ័ត្ន", "ឈប់", "ឆាប់ឡើង", "ប្រញាប់", "ជួយផង", "ចេញទៅ", "លែងខ្ញុំ", "កុំកម្រើក", "កុំប៉ះ", "បិទមាត់", "ដាច់ខាត", "ឱ្យសោះ", "ភ្លាម"])
        )
        is_sad_or_hesitant = (
            "..." in text or "…" in text or src.endswith("...") or
            any(kw in text for kw in ["សុំទោស", "សោកស្ដាយ", "ពិបាកចិត្ត", "ឈឺចាប់", "ហត់នឿយ", "លាហើយ", "អស់សង្ឃឹម", "អត់ដឹងសោះ", "យំ"])
        )
        is_warm_reassuring = (
            any(kw in text for kw in ["កុំបារម្ភ", "មិនអីទេ", "អត់អីទេ", "ជឿខ្ញុំ", "អរគុណ", "ស្រឡាញ់", "នឹកឯង", "ថែរក្សាខ្លួន", "សូមឱ្យជោគជ័យ", "សម្លាញ់"])
        )
        is_upbeat_short = (
            len(text) <= 18 and any(kw in text for kw in ["សួស្ដី", "បាទ", "មែនហើយ", "ប្រាកដហើយ", "យល់ហើយ", "តោះ", "ត្រូវហើយ"])
        )

        # Clean duplicate dots and inject natural breath-group cues
        speak_text = re.sub(r'\.{3,}', '…', text)
        speak_text = self._inject_khmer_breath_and_prosody_cues(speak_text, is_khmer_voice)

        # Ensure terminal punctuation matches emotional prosody
        if is_question and not (speak_text.endswith("?") or speak_text.endswith("？")):
            speak_text = re.sub(r'[\.\,\—\។\。\!]+$', '', speak_text) + "?"
        elif (is_exclamation or is_surprise) and not (speak_text.endswith("!") or speak_text.endswith("！") or speak_text.endswith("?")):
            speak_text = re.sub(r'[\.\,\—\។\。]+$', '', speak_text) + "!"
        elif not any(speak_text.endswith(p) for p in ["?", "？", "!", "！", "។", "。", ".", "…"]):
            if is_khmer_voice:
                speak_text += "។"
            elif is_cjk_voice:
                speak_text += "。"
            else:
                speak_text += "."

        # Select 8-Profile Cinematic Prosody Parameters (Pitch, Rate, Volume, Contour)
        rate_offset = 6  # Brisk conversational movie baseline
        pitch_offset = 0
        vol_offset = 4
        mood_profile = "NATURAL_DIALOGUE"

        if is_surprise:
            mood_profile = "SURPRISE_SHOCK"
            pitch_offset = 11
            rate_offset = 9
            vol_offset = 12
        elif is_question:
            mood_profile = "QUESTION_RISING"
            pitch_offset = 8
            rate_offset = 7
            vol_offset = 8
        elif is_exclamation:
            mood_profile = "URGENT_SHOUT"
            pitch_offset = 9
            rate_offset = 10
            vol_offset = 14
        elif is_sad_or_hesitant:
            mood_profile = "SAD_FALLING"
            pitch_offset = -7
            rate_offset = -4
            vol_offset = -4
        elif is_warm_reassuring:
            mood_profile = "WARM_EMPATHY"
            pitch_offset = -3
            rate_offset = 3
            vol_offset = 5
        elif is_upbeat_short:
            mood_profile = "UPBEAT_REPLY"
            pitch_offset = 5
            rate_offset = 8
            vol_offset = 8
        else:
            # Alternate subtle conversational pitch between consecutive segments so dialogue feels alive
            seg_mod = (segment.id or 0) % 4
            if seg_mod == 0:
                pitch_offset = 3
                rate_offset = 6
            elif seg_mod == 1:
                pitch_offset = -2
                rate_offset = 5
            elif seg_mod == 2:
                pitch_offset = 4
                rate_offset = 7
            else:
                pitch_offset = -3
                rate_offset = 5

        final_speed = max(0.50, min(base_speed + (rate_offset / 100.0), 2.00))
        rate_pct = int(round((final_speed - 1.0) * 100))
        rate_str = f"{rate_pct:+d}%"

        final_pitch = base_pitch_hz + pitch_offset
        pitch_str = f"{final_pitch:+d}Hz" if final_pitch != 0 else "+0Hz"
        vol_str = f"{vol_offset:+d}%" if vol_offset != 0 else "+0%"

        # Trailing space gives neural TTS decoder a clean release on the final phoneme
        speak_text = speak_text.strip() + " "

        return speak_text, rate_str, pitch_str, vol_str, mood_profile

    def _apply_natural_intonation_contour(self, audio: AudioSegment, mood_profile: str = "NATURAL_DIALOGUE") -> AudioSegment:
        """
        Apply human-like acoustic pitch intonation (ទម្លាក់តម្លើងសម្លេង) and dynamic syllable expression
        while keeping the exact millisecond duration 100% unchanged:
        - QUESTION_RISING / SURPRISE_SHOCK: Smooth pitch lift (+6% / ~1 semitone) and vocal lift at the end of the question.
        - URGENT_SHOUT: Punchy attack & peak emphasis (+4.5% pitch crest + dynamic punch) with decisive terminal drop.
        - SAD_FALLING: Gentle falling intonation contour (-4.5% pitch settle) and soft emotional taper.
        - NATURAL_DIALOGUE / WARM_EMPATHY: Natural conversational arch (rise on opening clause, smooth cadence drop at the end)
          plus micro-dynamic syllable stress expansion so the voice never sounds flat.
        """
        if len(audio) < 180:
            return audio
        try:
            samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
            channels = audio.channels
            if channels > 1:
                samples = samples.reshape((-1, channels))
            else:
                samples = samples.reshape((-1, 1))

            n_frames = samples.shape[0]
            if n_frames < 2000:
                return audio

            t = np.linspace(0.0, 1.0, n_frames, dtype=np.float32)

            # 1. Construct zero-mean instantaneous pitch/rate modulation curve delta(t)
            # Because mean(delta) == 0.0, cumulative integral ends at 1.0 -> 0.0ms duration drift!
            if mood_profile in ("QUESTION_RISING", "SURPRISE_SHOCK"):
                # Dip slightly in mid-sentence, then rise smoothly in the final 30% (rising question intonation)
                raw_curve = -0.025 * np.sin(np.pi * t) + 0.075 * np.power(np.clip((t - 0.55) / 0.45, 0.0, 1.0), 1.6)
                env_curve = 1.0 + 0.08 * np.sin(np.pi * t) + 0.14 * np.power(np.clip((t - 0.65) / 0.35, 0.0, 1.0), 1.5)
            elif mood_profile == "URGENT_SHOUT":
                # Strong initial rise & mid-phrase punch, then decisive authoritative drop at the end
                raw_curve = 0.045 * np.sin(1.4 * np.pi * t) - 0.035 * np.power(t, 2.0)
                env_curve = 1.12 - 0.10 * np.power(t, 2.2) + 0.08 * np.sin(np.pi * t)
            elif mood_profile == "SAD_FALLING":
                # Gentle downward pitch declination across the utterance
                raw_curve = 0.030 * (1.0 - 2.0 * t)
                env_curve = 1.04 - 0.16 * np.power(t, 1.5)
            else:
                # Natural human conversational arch: rise in opening clause, slight mid-breath inflection, settle at end
                raw_curve = 0.032 * np.sin(2.0 * np.pi * t) + 0.015 * np.sin(np.pi * t)
                env_curve = 1.04 + 0.06 * np.sin(np.pi * t) - 0.08 * np.power(np.clip((t - 0.75) / 0.25, 0.0, 1.0), 1.8)

            # Strictly remove mean so total length is 100% invariant
            delta = raw_curve - np.mean(raw_curve)
            inst_rate = 1.0 + delta
            warped_pos = np.cumsum(inst_rate)
            warped_pos = (warped_pos - warped_pos[0]) / max(1e-9, (warped_pos[-1] - warped_pos[0])) * (n_frames - 1)

            idx_floor = np.clip(np.floor(warped_pos).astype(np.int32), 0, n_frames - 2)
            frac = (warped_pos - idx_floor)[:, np.newaxis]
            warped_samples = (1.0 - frac) * samples[idx_floor] + frac * samples[idx_floor + 1]

            # 2. Micro-dynamic syllable emphasis (expands natural vocal stress peaks by ~1.2dB so speech feels expressive)
            abs_env = np.mean(np.abs(warped_samples), axis=1)
            win = max(1, int(audio.frame_rate * 0.035))
            kernel = np.ones(win, dtype=np.float32) / win
            smooth_env = np.convolve(abs_env, kernel, mode="same")
            peak_env = np.percentile(smooth_env, 92) if smooth_env.size > 0 else 1.0
            if peak_env > 10.0:
                norm_syllable = np.clip(smooth_env / peak_env, 0.0, 1.2)
                syllable_boost = 0.94 + 0.14 * np.power(norm_syllable, 0.8)
            else:
                syllable_boost = 1.0

            total_gain = (env_curve * syllable_boost)[:, np.newaxis]
            out_samples = warped_samples * total_gain

            # Soft-clip protection
            max_val = float(1 << (8 * audio.sample_width - 1)) - 1.0
            out_samples = np.clip(out_samples, -max_val, max_val).astype(samples.dtype if audio.sample_width == 4 else np.int16)

            return audio._spawn(out_samples.tobytes())
        except Exception as e:
            print(f"[TTSEngine] Note in intonation contour: {e}")
            return audio

    def _trim_silence(self, audio: AudioSegment) -> AudioSegment:
        """
        Strip all leading and trailing dead silence from edge-tts output with 2ms precision
        so the dubbed voice speaks at millisecond 0 (exact instant the character's mouth opens).
        """
        if len(audio) < 40:
            return audio
        try:
            thresh = max(-45.0, min(-34.0, audio.dBFS - 15.0)) if audio.dBFS != float("-inf") else -40.0
            lead_ms = detect_leading_silence(audio, silence_threshold=thresh, chunk_size=2)
            trail_ms = detect_leading_silence(audio.reverse(), silence_threshold=thresh - 2.0, chunk_size=2)

            start_trim = max(0, lead_ms - 3)
            end_trim = max(0, len(audio) - max(0, trail_ms - 15))
            if end_trim > start_trim + 40:
                audio = audio[start_trim:end_trim]
        except Exception as e:
            print(f"[TTSEngine] Note trimming silence: {e}")
        return audio

    def _apply_studio_vocal_enhancement(self, audio: AudioSegment, mood_profile: str = "NATURAL_DIALOGUE") -> AudioSegment:
        """
        Studio vocal warmth + Natural Intonation Contouring + Zero-Lag Silence Stripping:
        - High-pass at 75Hz to clean mic sub-rumble.
        - Low-pass at 10.5kHz to reduce harsh digital sizzle.
        - Strips all leading/trailing silence so speech starts immediately at segment.start.
        - Applies natural rising/falling pitch & dynamic expression contour matching sentence mood.
        - Peak normalization for balanced movie dialogue presence.
        """
        try:
            audio = audio.high_pass_filter(75)
            audio = audio.low_pass_filter(10500)
            audio = self._trim_silence(audio)
            audio = self._apply_natural_intonation_contour(audio, mood_profile=mood_profile)

            if len(audio) > 30:
                audio = audio.fade_in(4).fade_out(15)

            target_dbfs = -1.2 if mood_profile in ("URGENT_SHOUT", "SURPRISE_SHOCK") else (-2.2 if mood_profile == "SAD_FALLING" else -1.5)
            change_in_dbfs = target_dbfs - audio.max_dBFS
            if abs(change_in_dbfs) > 0.4:
                audio = audio.apply_gain(change_in_dbfs)
        except Exception as e:
            print(f"[TTSEngine] Note during vocal enhancement: {e}")
        return audio

    def generate_segment_tts(
        self,
        segment: Segment,
        speed_factor: Optional[float] = None,
        pitch_hz: int = 0
    ) -> Tuple[str, float]:
        """
        Generate Single Male Voice speech clip for a segment with zero leading delay,
        natural rising/falling vocal intonation, and automatic slot-duration matching.
        Returns: (output_wav_path, duration_seconds)
        """
        text = segment.text_km.strip() or segment.text_src.strip()
        wav_file = str(self.output_dir / f"seg_{segment.id:04d}.wav")
        speed = speed_factor if speed_factor is not None else segment.speed
        speed = max(0.50, min(float(speed), 2.00))

        # Strictly enforce Single Male Voice
        voice = normalize_to_male_voice(segment.voice or "km-KH-PisethNeural")
        segment.voice = voice
        segment.speaker = "ប្រុស (Male)"

        # Check if text contains actual pronounceable letters/digits across supported scripts
        clean_text = re.sub(r'[\s\.\,\-\_\!\?\:\;\(\)\[\]\{\}\/\\\"\'\`\~\@\#\$\%\^\&\*\+\=\|\<\>\—\…\♪\♫\。\？\！\、\，]+', '', text)

        has_km_letters = bool(re.search(r'[\u1780-\u17d3\u17d5-\u17ff]', clean_text))
        has_cjk_or_ko = bool(re.search(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', clean_text))
        has_any_pronounceable = bool(re.search(r'[\w\u1780-\u17ff\u0e00-\u0e7f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', clean_text))

        # Auto-translate on the spot if using a Khmer voice and text has no Khmer letters (e.g. untranslated CJK/Korean/English)
        if voice.startswith("km-") and not has_km_letters and has_any_pronounceable:
            try:
                from app.translation_engine import TranslationEngine
                tr = TranslationEngine()
                auto_km = tr.translate_segment(segment.text_src or text, target_lang="km", slot_duration=segment.slot)
                if auto_km and re.search(r'[\u1780-\u17d3\u17d5-\u17ff]', auto_km):
                    segment.text_km = auto_km
                    text = auto_km
                    clean_text = re.sub(r'[\s\.\,\-\_\!\?\:\;\(\)\[\]\{\}\/\\\"\'\`\~\@\#\$\%\^\&\*\+\=\|\<\>\—\…\♪\♫\。\？\！\、\，]+', '', text)
                    has_km_letters = True
                    has_cjk_or_ko = False
            except Exception as tr_err:
                print(f"[TTSEngine] On-the-spot translation note for seg {segment.id}: {tr_err}")

        if not clean_text or not has_any_pronounceable or (voice.startswith("km-") and has_cjk_or_ko and not has_km_letters):
            duration_sec = self._generate_silence(segment, wav_file)
            segment.speed = speed
            return wav_file, duration_sec

        speak_text, rate_str, pitch_str, vol_str, mood_profile = self._prepare_emotional_prosody(segment, speed, pitch_hz)

        # Output filenames
        mp3_file = str(self.output_dir / f"seg_{segment.id:04d}_temp.mp3")

        # Run edge-tts with thread-safe event loop and multi-tier error handling
        success = self._run_edge_tts(speak_text, voice, rate_str, pitch_str, mp3_file, volume=vol_str)

        # Note: Short 1-word MP3 clips at 24kbps (0.2s-0.35s) are 250-950 bytes, so accept >= 180 bytes!
        if success and os.path.exists(mp3_file) and os.path.getsize(mp3_file) >= 180:
            try:
                # Convert to WAV, strip leading/trailing silence, and apply natural intonation contour
                audio = AudioSegment.from_file(mp3_file)
                audio = audio.set_frame_rate(44100).set_channels(2)
                audio = self._apply_studio_vocal_enhancement(audio, mood_profile=mood_profile)
                audio.export(wav_file, format="wav")

                # Match the dubbed voice duration strictly to the original video's speaking window [segment.start, segment.end]
                raw_dur_sec = len(audio) / 1000.0
                slot_sec = max(0.35, segment.slot)
                if raw_dur_sec > slot_sec + 0.03 and os.path.exists(wav_file):
                    needed_stretch = min(round(raw_dur_sec / slot_sec, 3), 1.85)
                    if needed_stretch >= 1.02:
                        try:
                            self.audio_engine.time_stretch_atempo(wav_file, speed_factor=needed_stretch, output_wav=wav_file)
                            audio = AudioSegment.from_file(wav_file)
                            audio = self._trim_silence(audio)
                            audio.export(wav_file, format="wav")
                        except Exception as st_err:
                            print(f"[TTSEngine] Note during slot stretch for seg {segment.id}: {st_err}")
                elif raw_dur_sec < slot_sec * 0.82 and 0.80 <= slot_sec <= 4.5 and os.path.exists(wav_file):
                    needed_stretch = max(0.86, round(raw_dur_sec / (slot_sec * 0.92), 3))
                    if needed_stretch <= 0.96:
                        try:
                            self.audio_engine.time_stretch_atempo(wav_file, speed_factor=needed_stretch, output_wav=wav_file)
                            audio = AudioSegment.from_file(wav_file)
                            audio = self._trim_silence(audio)
                            audio.export(wav_file, format="wav")
                        except Exception as st_err:
                            print(f"[TTSEngine] Note during slot slow-pace for seg {segment.id}: {st_err}")

                duration_sec = round(len(audio) / 1000.0, 3)
            except Exception as e:
                print(f"[TTSEngine] Note converting mp3 to wav for seg {segment.id}: {e}")
                duration_sec = self._generate_silence(segment, wav_file)
        else:
            # Fallback to silence (or preserve existing valid speech clip if present)
            duration_sec = self._generate_silence(segment, wav_file)

        # Cleanup temp mp3
        try:
            if os.path.exists(mp3_file):
                os.remove(mp3_file)
        except Exception:
            pass

        # Update segment fields per SRS Section 7
        segment.tts_file = wav_file
        segment.tts_duration = duration_sec
        segment.speed = speed
        segment.actual_start = round(segment.start, 3)
        segment.actual_end = round(segment.start + duration_sec, 3)

        return wav_file, duration_sec

    def _generate_silence(self, segment: Segment, wav_file: str) -> float:
        # Never overwrite an already-valid non-silent voice file on disk if a later pass fails
        if os.path.exists(wav_file) and os.path.getsize(wav_file) > 2000:
            try:
                existing = AudioSegment.from_file(wav_file)
                if existing.dBFS != float("-inf") and existing.dBFS > -55.0:
                    dur = round(len(existing) / 1000.0, 3)
                    segment.tts_file = wav_file
                    segment.tts_duration = dur
                    segment.actual_start = round(segment.start, 3)
                    segment.actual_end = round(segment.start + dur, 3)
                    return dur
            except Exception:
                pass

        silence_ms = max(400, int((segment.slot if segment.slot > 0 else 1.0) * 1000))
        audio = AudioSegment.silent(duration=silence_ms, frame_rate=44100).set_channels(2)
        audio.export(wav_file, format="wav")
        dur = round(silence_ms / 1000.0, 3)
        segment.tts_file = wav_file
        segment.tts_duration = dur
        segment.actual_start = round(segment.start, 3)
        segment.actual_end = round(segment.start + dur, 3)
        return dur

    def _run_edge_tts(self, text: str, voice: str, rate: str, pitch: str, output_path: str, volume: Optional[str] = None) -> bool:
        """
        Execute edge-tts with adaptive backoff and 4-tier parameter fallback.
        Accepts short 1-word MP3s (>= 180 bytes) so brief responses are never dropped.
        """
        import edge_tts
        import time

        has_km_letters = bool(re.search(r'[\u1780-\u17d3\u17d5-\u17ff]', text))
        has_cjk_or_ko = bool(re.search(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', text))
        has_any_script = bool(re.search(r'[\w\u1780-\u17ff\u0e00-\u0e7f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', text))
        if not has_any_script or (voice.startswith("km-") and has_cjk_or_ko and not has_km_letters):
            return False

        clean_pitch = pitch if (pitch and pitch != "+0Hz") else None
        clean_rate = rate if (rate and rate != "+0%") else None
        clean_vol = volume if (volume and volume != "+0%") else None

        # Plain stripped text for Tier 4 in case punctuation caused Edge-TTS to reject the utterance
        plain_text = re.sub(r'[,\?\!\។\…\.]+', ' ', text).strip() or text.strip()

        # Strictly use Single Male Voice across all tiers
        voice = normalize_to_male_voice(voice)
        part_path = output_path + ".part"

        tiers = [
            (text, voice, clean_rate, clean_pitch, clean_vol, 0.0),
            (text, voice, clean_rate, None, None, 0.25),
            (text, voice, None, None, None, 0.50),
            (plain_text, voice, None, None, None, 0.90),
        ]

        for idx, (txt_payload, v, r, p, vol, delay) in enumerate(tiers):
            if delay > 0:
                time.sleep(delay)

            for fp in [output_path, part_path]:
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                kwargs = {}
                if r:
                    kwargs["rate"] = r
                if p:
                    kwargs["pitch"] = p
                if vol:
                    kwargs["volume"] = vol

                async def _speak():
                    communicate = edge_tts.Communicate(txt_payload, v, **kwargs)
                    await asyncio.wait_for(communicate.save(part_path), timeout=10.0)

                loop.run_until_complete(_speak())
                if os.path.exists(part_path) and os.path.getsize(part_path) >= 180:
                    os.replace(part_path, output_path)
                    return True
            except Exception as e:
                if idx == len(tiers) - 1:
                    safe_sample = txt_payload[:25].encode('ascii', errors='backslashreplace').decode('ascii')
                    print(f"[TTSEngine] TTS Notice for '{safe_sample}...': {e}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        return False

    def preview_speech(self, text: str, voice: str = "km-KH-PisethNeural", speed: float = 1.0) -> str:
        """Generate a temporary audio preview file."""
        preview_file = str(self.output_dir / "preview.wav")
        dummy_seg = Segment(id=9999, start=0.0, end=5.0, text_km=text, voice=voice, speed=speed)
        wav_file, _ = self.generate_segment_tts(dummy_seg, speed_factor=speed)
        return wav_file
