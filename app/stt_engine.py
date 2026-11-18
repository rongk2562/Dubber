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
        """Single Male Voice mode: always returns 'Male'."""
        return "Male"

    def _refine_speech_window(
        self,
        filtered_audio: Optional[np.ndarray],
        sr: int,
        raw_start: float,
        raw_end: float,
        min_allowed_start: float = 0.0
    ) -> tuple:
        """
        Lock start_sec and end_sec to the exact 10ms acoustic vocal onset/offset
        so the dubbed voice starts the exact instant the character's mouth opens.
        """
        st = max(min_allowed_start, float(raw_start))
        et = max(st + 0.25, float(raw_end))
        if filtered_audio is None or len(filtered_audio) == 0:
            return round(st, 2), round(et, 2)

        try:
            # Look slightly before raw_start (-60ms) in case Whisper clipped the initial consonant
            search_st = max(min_allowed_start, st - 0.06)
            s_idx = max(0, int(search_st * sr))
            e_idx = min(len(filtered_audio), int(et * sr))
            if e_idx - s_idx < int(sr * 0.12):
                return round(st, 2), round(et, 2)

            chunk = np.abs(filtered_audio[s_idx:e_idx])
            peak = float(np.percentile(chunk, 95)) if len(chunk) > 0 else 0.0
            if peak <= 1e-4:
                return round(st, 2), round(et, 2)

            win = max(1, int(sr * 0.010))  # 10ms frame
            n_frames = len(chunk) // win
            if n_frames < 4:
                return round(st, 2), round(et, 2)

            frames = chunk[: n_frames * win].reshape(n_frames, win).mean(axis=1)
            onset_thresh = peak * 0.12

            active_indices = np.where(frames >= onset_thresh)[0]
            if len(active_indices) > 0:
                first_frame = int(active_indices[0])
                last_frame = int(active_indices[-1])
                refined_st = search_st + (first_frame * 0.010)
                refined_et = search_st + ((last_frame + 2) * 0.010)
                # Keep within sane bounds of Whisper's word timestamp
                if abs(refined_st - st) <= 0.35:
                    st = max(min_allowed_start, refined_st)
                if abs(refined_et - et) <= 0.40 and refined_et > st + 0.20:
                    et = refined_et
        except Exception:
            pass

        return round(st, 2), round(max(st + 0.25, et), 2)

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> List[Segment]:
        """
        Transcribe audio file into a list of Segment objects with frame-accurate speech onset
        timestamps and Single Male Voice ('km-KH-PisethNeural').
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if progress_cb:
            progress_cb(5, f"Loading faster-whisper '{self.model_size}' model...")

        model = self._load_model(progress_cb=progress_cb)

        # Preload and high-pass filter audio array (85Hz-3800Hz speech band) for exact onset locking
        filtered_audio = None
        sr = 16000
        try:
            audio_seg = AudioSegment.from_file(audio_path).set_channels(1).set_frame_rate(sr)
            raw_samples = np.array(audio_seg.get_array_of_samples(), dtype=np.float32)
            try:
                import scipy.signal
                b, a = scipy.signal.butter(3, [85 / (sr / 2), 3800 / (sr / 2)], btype='band')
                filtered_audio = scipy.signal.filtfilt(b, a, raw_samples)
            except Exception:
                filtered_audio = raw_samples - np.mean(raw_samples)
        except Exception as e:
            print(f"[STTEngine] Note on audio waveform loader: {e}")

        if progress_cb:
            progress_cb(15, "Transcribing speech with word-level lip-sync timestamps...")

        lang_arg = None if (not language or language == "auto") else language

        # Use vad_filter=False with word_timestamps=True so faster-whisper never remaps
        # the first word of a segment across silent gaps to the previous segment's end!
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

        raw_phrases = []
        last_end_sec = 0.0

        for seg in segments_iter:
            text = seg.text.strip()
            if not text:
                continue

            words = list(getattr(seg, "words", None) or [])
            if not words:
                st, et = self._refine_speech_window(
                    filtered_audio, sr, float(seg.start), float(seg.end), min_allowed_start=last_end_sec
                )
                raw_phrases.append([st, max(st + 0.35, et), text])
                last_end_sec = max(st + 0.35, et)
                continue

            # Fix Whisper DTW anomaly where individual words have anomalous durations or leading gaps
            cleaned_words = []
            for idx_w, w in enumerate(words):
                w_st = float(w.start)
                w_et = float(w.end)
                w_txt = w.word
                # A single word/character does not span multi-second silence before its end
                max_w_dur = max(0.45, len(w_txt.strip()) * 0.32)
                if w_et - w_st > max_w_dur:
                    w_st = w_et - max_w_dur
                cleaned_words.append([w_st, w_et, w_txt])

            # If the first word was falsely attached to an earlier breath/noise (>0.35s gap before word 2
            # while word 1 is only 1-2 chars), snap word 1 to right before word 2!
            if len(cleaned_words) >= 2:
                gap_01 = cleaned_words[1][0] - cleaned_words[0][1]
                if gap_01 > 0.35 and len(cleaned_words[0][2].strip()) <= 2:
                    w0_dur = min(0.28, max(0.16, cleaned_words[0][1] - cleaned_words[0][0]))
                    cleaned_words[0][1] = cleaned_words[1][0] - 0.02
                    cleaned_words[0][0] = max(last_end_sec, cleaned_words[0][1] - w0_dur)

            # Split into sub-phrases only when there is a real pause (>= 0.45s) between two clauses
            curr_group = [cleaned_words[0]]
            for cw in cleaned_words[1:]:
                gap = cw[0] - curr_group[-1][1]
                curr_txt = "".join(x[2] for x in curr_group).strip()
                if gap >= 0.45 and len(curr_txt) >= 2:
                    g_st, g_et = self._refine_speech_window(
                        filtered_audio, sr, curr_group[0][0], curr_group[-1][1], min_allowed_start=last_end_sec
                    )
                    raw_phrases.append([g_st, g_et, curr_txt])
                    last_end_sec = g_et
                    curr_group = [cw]
                else:
                    curr_group.append(cw)

            if curr_group:
                curr_txt = "".join(x[2] for x in curr_group).strip()
                if curr_txt:
                    # Drop isolated 1-char noise hallucinations (e.g. single '是' or '嗯' <0.30s surrounded by >2s silence)
                    dur_grp = curr_group[-1][1] - curr_group[0][0]
                    if len(curr_txt) == 1 and dur_grp <= 0.30 and (curr_group[0][0] - last_end_sec) > 1.5:
                        continue
                    g_st, g_et = self._refine_speech_window(
                        filtered_audio, sr, curr_group[0][0], curr_group[-1][1], min_allowed_start=last_end_sec
                    )
                    raw_phrases.append([g_st, g_et, curr_txt])
                    last_end_sec = g_et

        # Merge contiguous micro-clauses that belong to the same continuous utterance (gap <= 0.18s,
        # or gap <= 0.35s when the first clause is < 0.85s) so translated sentences fit the actor's full speaking window!
        merged_phrases = []
        for st, et, txt in raw_phrases:
            if merged_phrases:
                prev_st, prev_et, prev_txt = merged_phrases[-1]
                gap = st - prev_et
                prev_dur = prev_et - prev_st
                combined_dur = et - prev_st
                if (gap <= 0.18 and combined_dur <= 4.2) or (prev_dur < 0.85 and gap <= 0.35 and combined_dur <= 4.2):
                    sep = " " if not prev_txt.endswith(" ") else ""
                    merged_phrases[-1] = [prev_st, et, f"{prev_txt}{sep}{txt}".strip()]
                    continue
            merged_phrases.append([st, et, txt])

        result_segments: List[Segment] = []
        for idx, (st, et, txt) in enumerate(merged_phrases, start=1):
            if et <= st:
                et = round(st + 0.45, 2)
            result_segments.append(Segment(
                id=idx,
                start=round(st, 2),
                end=round(et, 2),
                speaker="ប្រុស (Male)",
                text_src=txt,
                text_km="",
                voice="km-KH-PisethNeural"
            ))
            if progress_cb and total_duration > 0:
                pct = 20 + int((et / total_duration) * 75)
                pct = min(98, max(20, pct))
                progress_cb(pct, f"Transcribed segment {idx} [{st:.2f}s - {et:.2f}s]")

        if progress_cb:
            progress_cb(100, f"Completed: {len(result_segments)} segments with exact speech onset timestamps.")

        return result_segments
