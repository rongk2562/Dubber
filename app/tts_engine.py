"""
Khmer AI Voice (TTS) Engine using edge-tts Neural Voices
F6: Choose voice (male / female), speed, pitch
Conforms to SRS Section 6 & 7 Data Model
Thread-safe event loop with automatic silence fallback for non-pronounceable segments
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Dict
from pydub import AudioSegment
from app.config import TEMP_DIR, KHMER_VOICES, MAX_TTS_SPEED, get_voice_pair_for_lang
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

    def _prepare_emotional_prosody(
        self,
        segment: Segment,
        base_speed: float,
        base_pitch_hz: int
    ) -> Tuple[str, str, str]:
        """
        Enhance text and prosodic parameters for natural, emotive human-like dialogue:
        1. Preserves and restores question intonation (?) and exclamation dynamics (!).
        2. Dynamically modulates pitch and rate based on emotional tone.
        """
        text = segment.text_km.strip() or segment.text_src.strip()
        src = (segment.text_src or "").strip()

        # Determine emotional tone
        is_question = (
            text.endswith("?") or src.endswith("?") or
            any(kw in text for kw in ["តើ", "មែនទេ", "ឬទេ", "ទេ?", "ណា?", "អី?", "យ៉ាងម៉េច", "ហេតុអ្វី", "ទៅណា", "ម៉េចក៏", "ហ្នឹង?"])
        )
        is_exclamation = (
            text.endswith("!") or src.endswith("!") or
            any(kw in text for kw in ["ប្រយ័ត្ន", "ឈប់", "ឆាប់ឡើង", "ជួយផង", "កុំ", "ឱ្យសោះ", "មែនទែន", "អីយ៉ា", "ឱ"])
        )
        is_thoughtful = "..." in text or "…" in text or src.endswith("...")

        # Clean duplicate dots/ellipses while preserving punctuation
        speak_text = re.sub(r'\.{3,}', '…', text)
        speak_text = re.sub(r'\s+', ' ', speak_text).strip()

        # Ensure correct terminal punctuation token for neural prosody
        voice_id = segment.voice or "km-KH-PisethNeural"
        is_khmer_voice = voice_id.startswith("km-")
        is_cjk_voice = voice_id.startswith(("zh-", "ja-"))

        if is_question and not (speak_text.endswith("?") or speak_text.endswith("？")):
            speak_text = re.sub(r'[\.\,\—\។\。]+$', '', speak_text) + "?"
        elif is_exclamation and not (speak_text.endswith("!") or speak_text.endswith("！")):
            speak_text = re.sub(r'[\.\,\—\។\。]+$', '', speak_text) + "!"
        elif not any(speak_text.endswith(p) for p in ["?", "？", "!", "！", "។", "。", ".", "…"]):
            if is_khmer_voice:
                speak_text += "។"
            elif is_cjk_voice:
                speak_text += "。"
            else:
                speak_text += "."

        # Lively movie dialogue cadence (matched to energetic actor pacing)
        rate_offset = 6  # +6% brisk movie baseline eliminates sluggish reading
        pitch_offset = 0

        if is_question:
            # Inquisitive pitch inflection without sluggish drag
            pitch_offset += 2
            rate_offset += 1
        elif is_exclamation:
            # Energetic vocal presence
            pitch_offset += 3
            rate_offset += 4
        elif is_thoughtful:
            # Soft reflective phrasing
            rate_offset -= 3
            pitch_offset -= 1

        final_speed = max(0.50, min(base_speed + (rate_offset / 100.0), 2.00))
        rate_pct = int(round((final_speed - 1.0) * 100))
        rate_str = f"{rate_pct:+d}%"

        final_pitch = base_pitch_hz + pitch_offset
        pitch_str = f"{final_pitch:+d}Hz" if final_pitch != 0 else "+0Hz"

        # Trailing spacing gives neural TTS decoder clean release on the final phoneme
        speak_text = speak_text.strip() + " "

        return speak_text, rate_str, pitch_str

    def _apply_studio_vocal_enhancement(self, audio: AudioSegment) -> AudioSegment:
        """
        Studio vocal warmth enhancement:
        - High-pass at 75Hz to clean mic sub-rumble.
        - Low-pass at 10.5kHz to reduce harsh digital sizzle.
        - Compact 60ms natural room decay so syllables ring out cleanly without dragging out time.
        - Peak normalization for balanced movie dialogue punch.
        """
        try:
            audio = audio.high_pass_filter(75)
            audio = audio.low_pass_filter(10500)

            # Compact 60ms room cushion ensures clean phoneme release without adding dead air
            tail_cushion = AudioSegment.silent(duration=60, frame_rate=audio.frame_rate).set_channels(audio.channels)
            audio = audio + tail_cushion

            if len(audio) > 60:
                audio = audio.fade_in(15).fade_out(30)

            target_dbfs = -1.5
            change_in_dbfs = target_dbfs - audio.max_dBFS
            if abs(change_in_dbfs) > 0.5:
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
        Generate Khmer speech clip for a segment with natural emotional prosody.
        Returns: (output_wav_path, duration_seconds)
        """
        text = segment.text_km.strip() or segment.text_src.strip()
        wav_file = str(self.output_dir / f"seg_{segment.id:04d}.wav")
        speed = speed_factor if speed_factor is not None else segment.speed
        speed = max(0.50, min(float(speed), 2.00))

        voice = segment.voice or "km-KH-PisethNeural"

        # Check if text contains actual pronounceable letters/digits across supported scripts
        clean_text = re.sub(r'[\s\.\,\-\_\!\?\:\;\(\)\[\]\{\}\/\\\"\'\`\~\@\#\$\%\^\&\*\+\=\|\<\>\—\…\♪\♫\。\？\！\、\，]+', '', text)

        has_km_letters = bool(re.search(r'[\u1780-\u17d3\u17d5-\u17ff]', clean_text))
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', clean_text))
        has_any_pronounceable = bool(re.search(r'[\w\u1780-\u17ff\u0e00-\u0e7f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', clean_text))

        # Only block untranslated Chinese when using a Khmer voice; allow all scripts for their respective voices
        if not clean_text or not has_any_pronounceable or (voice.startswith("km-") and has_chinese and not has_km_letters):
            duration_sec = self._generate_silence(segment, wav_file)
            segment.speed = speed
            return wav_file, duration_sec

        speak_text, rate_str, pitch_str = self._prepare_emotional_prosody(segment, speed, pitch_hz)

        # Output filenames
        mp3_file = str(self.output_dir / f"seg_{segment.id:04d}_temp.mp3")

        # Run edge-tts with thread-safe event loop and multi-tier error handling
        success = self._run_edge_tts(speak_text, voice, rate_str, pitch_str, mp3_file)

        if success and os.path.exists(mp3_file) and os.path.getsize(mp3_file) >= 1024:
            try:
                # Convert to WAV & apply studio vocal warmth enhancement
                audio = AudioSegment.from_file(mp3_file)
                audio = audio.set_frame_rate(44100).set_channels(2)
                audio = self._apply_studio_vocal_enhancement(audio)
                audio.export(wav_file, format="wav")

                # If custom speed is requested, apply precision offline FFmpeg atempo time-stretch
                if abs(speed - 1.0) > 0.02 and os.path.exists(wav_file):
                    try:
                        self.audio_engine.time_stretch_atempo(wav_file, speed_factor=speed, output_wav=wav_file)
                        audio = AudioSegment.from_file(wav_file)
                    except Exception as st_err:
                        print(f"[TTSEngine] Note during speed stretch for seg {segment.id}: {st_err}")

                duration_sec = round(len(audio) / 1000.0, 3)
            except Exception as e:
                print(f"[TTSEngine] Note converting mp3 to wav for seg {segment.id}: {e}")
                duration_sec = self._generate_silence(segment, wav_file)
        else:
            # Fallback to silence so pipeline continues smoothly
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
        silence_ms = max(400, int((segment.slot if segment.slot > 0 else 1.0) * 1000))
        audio = AudioSegment.silent(duration=silence_ms, frame_rate=44100).set_channels(2)
        audio.export(wav_file, format="wav")
        dur = round(silence_ms / 1000.0, 3)
        segment.tts_file = wav_file
        segment.tts_duration = dur
        segment.actual_start = round(segment.start, 3)
        segment.actual_end = round(segment.start + dur, 3)
        return dur

    def _run_edge_tts(self, text: str, voice: str, rate: str, pitch: str, output_path: str) -> bool:
        """
        Execute edge-tts with adaptive backoff and multi-tier parameter fallback.
        Handles Microsoft Edge WebSocket throttling and rate anomalies with strict 8s timeout.
        """
        import edge_tts
        import time

        # Edge-TTS Khmer voices reject Chinese-only characters, whereas multi-lingual voices speak their native scripts
        has_km_letters = bool(re.search(r'[\u1780-\u17d3\u17d5-\u17ff]', text))
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))
        has_any_script = bool(re.search(r'[\w\u1780-\u17ff\u0e00-\u0e7f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', text))
        if not has_any_script or (voice.startswith("km-") and has_chinese and not has_km_letters):
            return False

        clean_pitch = pitch if (pitch and pitch != "+0Hz") else None
        clean_rate = rate if (rate and rate != "+0%") else None

        # Choose alternate voice within the same language for Tier 4 fallback
        v_meta = KHMER_VOICES.get(voice, {})
        v_lang = v_meta.get("lang", "km")
        male_v, female_v = get_voice_pair_for_lang(v_lang)
        alt_voice = male_v if voice == female_v else female_v
        part_path = output_path + ".part"

        # Tiered attempts:
        # Tier 1: Requested parameters
        # Tier 2: Safe standard rate/pitch (bypasses Edge-TTS rate bugs)
        # Tier 3: Standard rate with 0.4s backoff (bypasses server throttling)
        # Tier 4: Alternate Khmer voice with 0.8s backoff
        tiers = [
            (voice, clean_rate, clean_pitch, 0.0),
            (voice, None, None, 0.2),
            (voice, None, None, 0.4),
            (alt_voice, None, None, 0.8),
        ]

        for idx, (v, r, p, delay) in enumerate(tiers):
            if delay > 0:
                time.sleep(delay)

            # Clean previous partial files
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

                async def _speak():
                    communicate = edge_tts.Communicate(text, v, **kwargs)
                    await asyncio.wait_for(communicate.save(part_path), timeout=8.0)

                loop.run_until_complete(_speak())
                if os.path.exists(part_path) and os.path.getsize(part_path) >= 1024:
                    os.replace(part_path, output_path)
                    return True
            except Exception as e:
                # If Tier 1 failed due to rate / NoAudioReceived, loop immediately advances to Tier 2
                if idx == len(tiers) - 1:
                    safe_sample = text[:25].encode('ascii', errors='backslashreplace').decode('ascii')
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
