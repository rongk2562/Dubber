"""
Speech-to-Text (STT) Engine using faster-whisper
F3: Auto-detect language, output timestamps in seconds, produces segments
Acoustic Pitch Detection: Automatically recognizes Male vs Female human voice and assigns appropriate Khmer voice
"""

import os
from pathlib import Path
from typing import List, Callable, Optional
import numpy as np
from pydub import AudioSegment
from app.models import Segment
from app.config import DATA_DIR, get_resource_path

_HF_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}


class STTEngine:
    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _resolve_or_download_model(self, progress_cb: Optional[Callable[[int, str], None]] = None) -> str:
        """
        Locate bundled/cached faster-whisper model or download it directly with live UI progress
        (avoids Windows symlink errors and silent 5% UI freezes on new PCs).
        """
        # 1. Check bundled whisper_models/<size> or writable DATA_DIR/whisper_models/<size>
        candidate_dirs = [
            get_resource_path(f"whisper_models/{self.model_size}"),
            DATA_DIR / "whisper_models" / self.model_size,
        ]
        # Also check standard HuggingFace cache if already present on the PC
        hf_cache_snapshots = (
            Path.home()
            / ".cache"
            / "huggingface"
            / "hub"
            / f"models--Systran--faster-whisper-{self.model_size}"
            / "snapshots"
        )
        if hf_cache_snapshots.exists():
            for snap in hf_cache_snapshots.iterdir():
                if snap.is_dir():
                    candidate_dirs.append(snap)

        for d in candidate_dirs:
            if d.exists() and (d / "model.bin").exists() and (d / "model.bin").stat().st_size > 1_000_000:
                return str(d)

        # 2. Download directly into DATA_DIR / "whisper_models" / model_size with live progress
        target_dir = DATA_DIR / "whisper_models" / self.model_size
        target_dir.mkdir(parents=True, exist_ok=True)

        repo_id = _HF_REPOS.get(self.model_size, f"Systran/faster-whisper-{self.model_size}")
        import httpx

        files_to_fetch = [
            ("config.json", True),
            ("tokenizer.json", True),
            ("vocabulary.txt", False),
            ("vocabulary.json", False),
            ("preprocessor_config.json", False),
            ("model.bin", True),
        ]

        headers = {"User-Agent": "AI-Movie-Dubbing-Studio/1.0"}
        with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
            for fname, required in files_to_fetch:
                dest_file = target_dir / fname
                if dest_file.exists() and dest_file.stat().st_size > 100:
                    continue
                url = f"https://huggingface.co/{repo_id}/resolve/main/{fname}"
                part_file = target_dir / f"{fname}.part"
                try:
                    with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            if required:
                                raise RuntimeError(f"HTTP {resp.status_code} downloading {fname}")
                            continue
                        total_bytes = int(resp.headers.get("content-length", 0))
                        downloaded = 0
                        with open(part_file, "wb") as f:
                            for chunk in resp.iter_bytes(chunk_size=131072):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if fname == "model.bin" and progress_cb and total_bytes > 0:
                                        dl_pct = int((downloaded / total_bytes) * 100)
                                        bar_pct = 5 + int((downloaded / total_bytes) * 9)
                                        mb_done = downloaded / (1024 * 1024)
                                        mb_tot = total_bytes / (1024 * 1024)
                                        progress_cb(
                                            bar_pct,
                                            f"កំពុងទាញយក Whisper '{self.model_size}' Model លើកដំបូង: {mb_done:.1f} MB / {mb_tot:.1f} MB ({dl_pct}%)..."
                                        )
                    if part_file.exists():
                        os.replace(part_file, dest_file)
                except Exception as e:
                    if part_file.exists():
                        try:
                            part_file.unlink()
                        except Exception:
                            pass
                    if required:
                        raise RuntimeError(f"Failed to download Whisper model '{self.model_size}': {e}")

        return str(target_dir)

    def _load_model(self, progress_cb: Optional[Callable[[int, str], None]] = None):
        """Lazy load faster-whisper model from local path or auto-download with progress."""
        if self._model is None:
            from faster_whisper import WhisperModel
            model_dir = self._resolve_or_download_model(progress_cb=progress_cb)
            if progress_cb:
                progress_cb(14, f"Initializing faster-whisper '{self.model_size}' engine...")
            self._model = WhisperModel(
                model_dir,
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True
            )
        return self._model

    def detect_slice_gender(self, filtered_audio: np.ndarray, sr: int, st: float, et: float) -> str:
        """
        Detect human voice gender from audio chunk fundamental frequency (F0).
        F0 >= 165Hz -> Female (Sreymom)
        F0 < 165Hz  -> Male (Piseth)
        """
        if filtered_audio is None:
            return "Male"
        try:
            s_idx = max(0, int(st * sr))
            e_idx = min(len(filtered_audio), int(et * sr))
            clip = filtered_audio[s_idx:e_idx]
            if len(clip) < sr * 0.2:
                return "Male"

            clip_norm = clip / (np.max(np.abs(clip)) + 1e-6)
            frame_len = int(sr * 0.05) # 50ms
            hop_len = int(sr * 0.025)  # 25ms
            min_lag = int(sr / 350)    # 350 Hz max human pitch
            max_lag = int(sr / 75)     # 75 Hz min human pitch

            f0_list = []
            for i in range(0, len(clip_norm) - frame_len, hop_len):
                fr = clip_norm[i:i+frame_len]
                if np.mean(fr**2) < 0.001:
                    continue
                corr = np.correlate(fr, fr, mode='full')[frame_len-1:]
                if len(corr) <= max_lag:
                    continue
                peak_lag = min_lag + np.argmax(corr[min_lag:max_lag])
                if corr[peak_lag] > 0.35 * corr[0]:
                    f0 = sr / peak_lag
                    if 80 <= f0 <= 320:
                        f0_list.append(f0)

            if not f0_list:
                return "Male"

            med_f0 = float(np.median(f0_list))
            return "Female" if med_f0 >= 165.0 else "Male"
        except Exception:
            return "Male"

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> List[Segment]:
        """
        Transcribe audio file into a list of Segment objects with precise timestamps in seconds
        and automatic acoustic voice gender detection (Male / Female).
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if progress_cb:
            progress_cb(5, f"Loading faster-whisper '{self.model_size}' model...")

        model = self._load_model(progress_cb=progress_cb)

        # Preload and filter audio array for acoustic voice gender detection
        filtered_audio = None
        sr = 16000
        try:
            audio_seg = AudioSegment.from_file(audio_path).set_channels(1).set_frame_rate(sr)
            raw_samples = np.array(audio_seg.get_array_of_samples(), dtype=np.float32)
            try:
                import scipy.signal
                b, a = scipy.signal.butter(3, 70 / (sr / 2), btype='high')
                filtered_audio = scipy.signal.filtfilt(b, a, raw_samples)
            except Exception:
                # Pure NumPy high-pass fallback (first-order difference + DC removal)
                filtered_audio = raw_samples - np.mean(raw_samples)
        except Exception as e:
            print(f"[STTEngine] Note on acoustic gender analyzer: {e}")

        if progress_cb:
            progress_cb(15, "Transcribing speech segments & detecting character voices...")

        lang_arg = None if (not language or language == "auto") else language

        segments_iter, info = model.transcribe(
            audio_path,
            language=lang_arg,
            beam_size=5,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4
        )

        detected_lang = info.language
        detected_prob = info.language_probability
        total_duration = info.duration or 1.0

        if progress_cb:
            progress_cb(20, f"Detected language: {detected_lang.upper()} ({detected_prob*100:.1f}%)")

        result_segments: List[Segment] = []
        seg_id = 1

        for seg in segments_iter:
            text = seg.text.strip()
            if not text:
                continue

            start_sec = round(float(seg.start), 2)
            end_sec = round(float(seg.end), 2)

            if end_sec <= start_sec:
                end_sec = start_sec + 0.5

            # Automatic Voice Gender Detection from the audio itself!
            gender = self.detect_slice_gender(filtered_audio, sr, start_sec, end_sec)
            voice_id = "km-KH-SreymomNeural" if gender == "Female" else "km-KH-PisethNeural"
            speaker_label = "ស្រី (Female)" if gender == "Female" else "ប្រុស (Male)"

            result_segments.append(Segment(
                id=seg_id,
                start=start_sec,
                end=end_sec,
                speaker=speaker_label,
                text_src=text,
                text_km="",
                voice=voice_id
            ))
            seg_id += 1

            if progress_cb and total_duration > 0:
                pct = 20 + int((seg.end / total_duration) * 75)
                pct = min(98, max(20, pct))
                progress_cb(pct, f"Transcribed segment {seg_id-1} [{start_sec}s - {end_sec}s] ({gender})")

        if progress_cb:
            progress_cb(100, f"Completed: {len(result_segments)} segments transcribed with voice gender detection.")

        return result_segments
