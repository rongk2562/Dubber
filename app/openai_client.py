"""
OpenAI / ChatGPT Client for Audio Transcription (Chunked <= 25MB) and Structured Batch SRT Translation.
Features:
- Secure API key lookup via OS credential store (app.secrets)
- Custom Base URL & dynamic model discovery
- FFmpeg silence-aware audio chunking (< 24 MB) + offset timestamp merging
- Batch JSON structured translation (20-40 lines/batch) with index verification & missing-line retry
- Exponential backoff on 429/5xx, concurrency control, token/cost estimation & running totals
- Clear Khmer user-facing error messages; never logs secrets
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from app.logger import get_logger, redact_secrets
from app.models import GlossaryItem, ProjectSettings, Segment
from app.secrets import get_openai_api_key
from app.utils import is_ffmpeg_available, parse_srt

log = get_logger("openai_client")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_STT_MODELS = ["whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"]
DEFAULT_CHAT_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"]

# Approximate per-1M token USD pricing (input, output)
_MODEL_PRICING_PER_1M: Dict[str, Tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
}


@dataclass
class OpenAIUsageStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    audio_minutes: float = 0.0
    estimated_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "OpenAIUsageStats") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.audio_minutes += other.audio_minutes
        self.estimated_usd += other.estimated_usd

    def summary_km(self) -> str:
        parts = []
        if self.audio_minutes > 0:
            parts.append(f"សំឡេង {self.audio_minutes:.2f} នាទី")
        if self.total_tokens > 0:
            parts.append(f"Token {self.total_tokens:,}")
        parts.append(f"តម្លៃប៉ាន់ស្មាន ~${self.estimated_usd:.4f}")
        return " | ".join(parts)


class OpenAIAPIError(RuntimeError):
    """Error carrying a clear user-friendly Khmer message."""


def format_openai_error_km(exc: Exception, status_code: Optional[int] = None, body: str = "") -> str:
    """Convert raw network/API errors into clear Khmer diagnostics without exposing keys."""
    safe_body = redact_secrets(body or str(exc))
    low = safe_body.lower()
    if status_code == 401 or "invalid_api_key" in low or "incorrect api key" in low:
        return "API Key របស់ OpenAI មិនត្រឹមត្រូវ ឬផុតកំណត់។ សូមពិនិត្យក្នុង Settings → OpenAI / ChatGPT។"
    if status_code == 429:
        if "insufficient_quota" in low or "billing" in low or "quota" in low:
            return "គណនី OpenAI អស់ Quota ឬ Credit ហើយ (HTTP 429)។ សូមបញ្ចូលទឹកប្រាក់ ឬពិនិត្យ billing។"
        return "លើសដែនកំណត់សំណើ OpenAI (Rate limit 429)។ កំពុងសាកល្បងម្ដងទៀត..."
    if status_code in (500, 502, 503, 504):
        return f"ម៉ាស៊ីនមេ OpenAI មានបញ្ហាបណ្ដោះអាសន្ន (HTTP {status_code})។"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) or "nodename" in low or "connection" in low:
        return "គ្មានការតភ្ជាប់អ៊ីនធឺណិត ឬមិនអាចភ្ជាប់ទៅ OpenAI Base URL បានទេ។"
    if isinstance(exc, httpx.ReadTimeout) or "timeout" in low:
        return "ការតភ្ជាប់ទៅ OpenAI API យឺតពេក (Timeout)។ សូមពិនិត្យអ៊ីនធឺណិត ឬបន្ថយទំហំ batch។"
    return f"កំហុស OpenAI API: {safe_body[:180]}"


def estimate_tokens_for_text(text: str) -> int:
    """Estimate LLM tokens safely for mixed CJK/Khmer/Latin text."""
    if not text:
        return 0
    return max(1, int(round(len(text) / 1.6)))


def estimate_translation_cost(
    segments: List[Segment],
    model: str = "gpt-4o-mini",
    batch_size: int = 30,
) -> OpenAIUsageStats:
    """Estimate input/output tokens and approximate USD before running ChatGPT translation."""
    non_empty = [s for s in segments if (s.text_src or "").strip()]
    if not non_empty:
        return OpenAIUsageStats()

    num_batches = max(1, (len(non_empty) + batch_size - 1) // batch_size)
    src_tokens = sum(estimate_tokens_for_text(s.text_src) + 8 for s in non_empty)
    prompt_tokens = src_tokens + num_batches * 280
    # Khmer output is typically ~1.5x source token count + JSON key overhead
    completion_tokens = int(src_tokens * 1.6) + len(non_empty) * 10
    in_rate, out_rate = _MODEL_PRICING_PER_1M.get(model, (0.50, 2.00))
    usd = (prompt_tokens / 1_000_000.0) * in_rate + (completion_tokens / 1_000_000.0) * out_rate
    return OpenAIUsageStats(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_usd=round(usd, 5),
    )


def estimate_transcription_cost(duration_sec: float, model: str = "whisper-1") -> OpenAIUsageStats:
    mins = max(0.0, float(duration_sec) / 60.0)
    rate = 0.003 if "mini-transcribe" in (model or "") else 0.006
    return OpenAIUsageStats(audio_minutes=round(mins, 2), estimated_usd=round(mins * rate, 5))


def validate_and_extract_batch_json(raw_text: str, expected_indices: List[int]) -> Tuple[Dict[int, str], List[int]]:
    """
    Parse structured JSON response keyed by segment index and verify exact count & index match.
    Returns (matched_dict, missing_or_invalid_indices).
    """
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    parsed_obj = None
    try:
        parsed_obj = json.loads(cleaned)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                parsed_obj = json.loads(m.group(0))
            except Exception:
                parsed_obj = None

    raw_map: Dict[str, str] = {}
    if isinstance(parsed_obj, dict):
        if isinstance(parsed_obj.get("translations"), dict):
            raw_map = {str(k): str(v) for k, v in parsed_obj["translations"].items() if v is not None}
        elif isinstance(parsed_obj.get("items"), list):
            for it in parsed_obj["items"]:
                if isinstance(it, dict) and "index" in it and ("text" in it or "translation" in it):
                    val = it.get("text", it.get("translation", ""))
                    raw_map[str(it["index"])] = str(val or "")
        else:
            raw_map = {str(k): str(v) for k, v in parsed_obj.items() if v is not None}

    matched: Dict[int, str] = {}
    missing: List[int] = []
    for idx in expected_indices:
        val = raw_map.get(str(idx), "").strip()
        if val:
            matched[idx] = val
        else:
            missing.append(idx)
    return matched, missing


def merge_chunk_segments(chunk_results: List[Tuple[float, List[dict]]]) -> List[Segment]:
    """
    Merge per-chunk OpenAI transcription results by adding chunk start offset to each segment,
    normalizing timestamps, and assigning sequential 1..N IDs.
    """
    merged: List[Segment] = []
    next_id = 1
    for offset_sec, raw_segs in chunk_results:
        off = max(0.0, float(offset_sec))
        for item in raw_segs or []:
            txt = (item.get("text") or "").strip()
            if not txt:
                continue
            st = round(off + max(0.0, float(item.get("start", 0.0))), 3)
            et = round(off + max(0.0, float(item.get("end", st + 0.5))), 3)
            if et <= st:
                et = round(st + 0.4, 3)
            if merged and st < merged[-1].start:
                st = merged[-1].start
                et = max(st + 0.3, et)
            merged.append(
                Segment(
                    id=next_id,
                    start=st,
                    end=et,
                    text_src=txt,
                    src_source="OpenAI",
                )
            )
            next_id += 1
    return merged


class OpenAIClient:
    """Reusable OpenAI / ChatGPT API client supporting custom Base URL & secure credential lookup."""

    def __init__(self, settings: Optional[ProjectSettings] = None):
        self.settings = settings or ProjectSettings()

    def _resolve_key(self, override_key: Optional[str] = None) -> str:
        if override_key and override_key.strip():
            return override_key.strip()
        if getattr(self.settings, "openai_api_key", "").strip():
            return self.settings.openai_api_key.strip()
        return get_openai_api_key()

    def _resolve_base_url(self, override_url: Optional[str] = None) -> str:
        raw = (override_url or getattr(self.settings, "openai_base_url", "") or DEFAULT_OPENAI_BASE_URL).strip()
        return raw.rstrip("/") or DEFAULT_OPENAI_BASE_URL

    def test_connection(
        self, api_key: Optional[str] = None, base_url: Optional[str] = None
    ) -> Tuple[bool, str, List[str]]:
        """Test OpenAI API connectivity and fetch available model IDs."""
        key = self._resolve_key(api_key)
        if not key:
            return False, "សូមបញ្ចូល OpenAI API Key ជាមុនសិន!", []
        url = f"{self._resolve_base_url(base_url)}/models"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers={"Authorization": f"Bearer {key}"})
            if resp.status_code == 200:
                data = resp.json()
                ids = sorted({m.get("id", "") for m in data.get("data", []) if m.get("id")})
                return True, f"តភ្ជាប់ OpenAI ជោគជ័យ! រកឃើញ {len(ids)} models។", ids
            return False, format_openai_error_km(RuntimeError(resp.text), resp.status_code, resp.text), []
        except Exception as e:
            return False, format_openai_error_km(e), []

    @staticmethod
    def categorize_models(model_ids: List[str]) -> Tuple[List[str], List[str]]:
        """Split API model list into (stt_models, chat_models) keeping sensible defaults first."""
        stt = list(DEFAULT_STT_MODELS)
        chat = list(DEFAULT_CHAT_MODELS)
        for mid in model_ids or []:
            low = mid.lower()
            if "whisper" in low or "transcribe" in low:
                if mid not in stt:
                    stt.append(mid)
            elif any(k in low for k in ("gpt-", "o1", "o3", "o4", "chatgpt")):
                if "audio" not in low and "realtime" not in low and "transcribe" not in low and "tts" not in low:
                    if mid not in chat:
                        chat.append(mid)
        return stt, chat

    # --- Audio Extraction & Silence-Aware Chunking (< 25MB limit) ---
    @staticmethod
    def _probe_duration(media_path: str) -> float:
        try:
            cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", media_path,
            ]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return max(0.0, float((r.stdout or "0").strip()))
        except Exception:
            return 0.0

    @staticmethod
    def _detect_silences(audio_path: str) -> List[float]:
        """Detect silence midpoints via FFmpeg silencedetect for clean chunk splits."""
        mids: List[float] = []
        try:
            cmd = [
                "ffmpeg", "-hide_banner", "-i", audio_path,
                "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-",
            ]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            starts: List[float] = []
            for line in (r.stderr or "").splitlines():
                m_st = re.search(r"silence_start:\s*([0-9.]+)", line)
                if m_st:
                    starts.append(float(m_st.group(1)))
                m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
                if m_end and starts:
                    s0 = starts.pop(0)
                    e0 = float(m_end.group(1))
                    mids.append(round((s0 + e0) / 2.0, 3))
        except Exception:
            pass
        return mids

    def extract_and_split_audio_chunks(
        self,
        video_path: str,
        work_dir: str,
        max_chunk_mb: float = 22.0,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[Tuple[str, float]], float]:
        """
        Extract compressed mono 16kHz MP3 via FFmpeg and split at silence boundaries
        if size > max_chunk_mb (stays safely under OpenAI's 25MB limit).
        """
        if not is_ffmpeg_available():
            raise OpenAIAPIError("រកមិនឃើញកម្មវិធី FFmpeg សម្រាប់ដកស្រង់សំឡេង! សូមដំឡើង FFmpeg ជាមុនសិន។")

        os.makedirs(work_dir, exist_ok=True)
        full_mp3 = str(Path(work_dir) / "openai_full.mp3")
        if progress_cb:
            progress_cb(5, "កំពុងដកស្រង់ និងបង្ហាប់សំឡេងដោយ FFmpeg...")

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", full_mp3,
        ]
        subprocess.run(
            cmd, capture_output=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if cancel_cb and cancel_cb():
            raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")

        dur = self._probe_duration(full_mp3) or self._probe_duration(video_path)
        size_mb = os.path.getsize(full_mp3) / (1024 * 1024)
        if size_mb <= max_chunk_mb or dur <= 60.0:
            return [(full_mp3, 0.0)], dur

        # Compute split points targeting ~max_chunk_mb * 0.85 per chunk
        bytes_per_sec = max(1000.0, os.path.getsize(full_mp3) / max(1.0, dur))
        target_dur = (max_chunk_mb * 0.85 * 1024 * 1024) / bytes_per_sec
        target_dur = max(120.0, min(1800.0, target_dur))
        silences = self._detect_silences(full_mp3)

        cuts = [0.0]
        cur = 0.0
        while cur + target_dur < dur - 5.0:
            ideal = cur + target_dur
            window = [s for s in silences if cur + target_dur * 0.6 <= s <= ideal + 30.0]
            split_t = min(window, key=lambda s: abs(s - ideal)) if window else ideal
            cuts.append(round(split_t, 3))
            cur = split_t
        cuts.append(round(dur, 3))

        chunks: List[Tuple[str, float]] = []
        for idx in range(len(cuts) - 1):
            if cancel_cb and cancel_cb():
                raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")
            st = cuts[idx]
            et = cuts[idx + 1]
            c_path = str(Path(work_dir) / f"openai_chunk_{idx:03d}.mp3")
            c_cmd = [
                "ffmpeg", "-y", "-ss", f"{st:.3f}", "-to", f"{et:.3f}",
                "-i", full_mp3, "-c", "copy", c_path,
            ]
            subprocess.run(
                c_cmd, capture_output=True, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            chunks.append((c_path, st))
        return chunks, dur

    def transcribe_video(
        self,
        video_path: str,
        source_lang: str = "zh",
        stt_model: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[Segment], OpenAIUsageStats]:
        """Transcribe a video via OpenAI Audio Transcriptions API with chunking, retry, and timestamp merging."""
        key = self._resolve_key()
        if not key:
            raise OpenAIAPIError("សូមបញ្ចូល OpenAI API Key ក្នុង Settings → OpenAI / ChatGPT ជាមុនសិន!")

        model = (stt_model or getattr(self.settings, "openai_stt_model", "whisper-1") or "whisper-1").strip()
        base_url = self._resolve_base_url()
        endpoint = f"{base_url}/audio/transcriptions"

        with tempfile.TemporaryDirectory(prefix="openai_stt_") as tmp_dir:
            chunks, total_dur = self.extract_and_split_audio_chunks(
                video_path, tmp_dir, progress_cb=progress_cb, cancel_cb=cancel_cb
            )
            chunk_results: List[Tuple[float, List[dict]]] = []
            total_chunks = max(1, len(chunks))

            for idx, (c_path, offset_sec) in enumerate(chunks):
                if cancel_cb and cancel_cb():
                    raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")
                if progress_cb:
                    pct = 15 + int(((idx) / total_chunks) * 75)
                    progress_cb(pct, f"កំពុងបម្លែងសំឡេងតាម OpenAI ({model}) ភាគ {idx + 1}/{total_chunks}...")

                raw_segs = self._post_transcription_chunk_with_retry(
                    endpoint=endpoint,
                    api_key=key,
                    chunk_path=c_path,
                    model=model,
                    source_lang=source_lang,
                    cancel_cb=cancel_cb,
                )
                chunk_results.append((offset_sec, raw_segs))

        segments = merge_chunk_segments(chunk_results)
        usage = estimate_transcription_cost(total_dur, model=model)
        log.info("OpenAI STT (%s) finished: %d segments, %.2f min", model, len(segments), usage.audio_minutes)
        return segments, usage

    def _post_transcription_chunk_with_retry(
        self,
        endpoint: str,
        api_key: str,
        chunk_path: str,
        model: str,
        source_lang: str,
        cancel_cb: Optional[Callable[[], bool]] = None,
        max_retries: int = 4,
    ) -> List[dict]:
        """POST single audio chunk with verbose_json fallback and exponential backoff."""
        resp_fmt = "verbose_json" if "whisper" in model.lower() else "json"
        last_err: Exception = RuntimeError("Unknown error")

        for attempt in range(max_retries):
            if cancel_cb and cancel_cb():
                raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")
            try:
                data_fields = {"model": model, "response_format": resp_fmt}
                if source_lang and source_lang.lower() != "auto":
                    # Normalize zh-CN -> zh
                    lang_code = source_lang.split("-")[0].lower()
                    data_fields["language"] = lang_code
                if resp_fmt == "verbose_json":
                    data_fields["timestamp_granularities[]"] = "segment"

                with open(chunk_path, "rb") as f_audio:
                    files = {"file": (Path(chunk_path).name, f_audio, "audio/mpeg")}
                    with httpx.Client(timeout=180.0) as client:
                        resp = client.post(
                            endpoint,
                            headers={"Authorization": f"Bearer {api_key}"},
                            data=data_fields,
                            files=files,
                        )

                if resp.status_code == 200:
                    payload = resp.json()
                    if isinstance(payload.get("segments"), list) and payload["segments"]:
                        return payload["segments"]
                    txt = (payload.get("text") or "").strip()
                    if txt:
                        dur = self._probe_duration(chunk_path) or 4.0
                        return [{"start": 0.0, "end": dur, "text": txt}]
                    return []

                # Fallback if model/proxy rejects verbose_json
                if resp.status_code == 400 and resp_fmt == "verbose_json":
                    resp_fmt = "json"
                    continue
                if resp.status_code == 401:
                    raise OpenAIAPIError(format_openai_error_km(RuntimeError("401"), 401, resp.text))
                if resp.status_code == 429 or resp.status_code >= 500:
                    msg = format_openai_error_km(RuntimeError(resp.text), resp.status_code, resp.text)
                    if "អស់ Quota" in msg:
                        raise OpenAIAPIError(msg)
                    last_err = OpenAIAPIError(msg)
                    time.sleep(min(20.0, 2.0 ** (attempt + 1)))
                    continue
                raise OpenAIAPIError(format_openai_error_km(RuntimeError(resp.text), resp.status_code, resp.text))
            except OpenAIAPIError:
                raise
            except Exception as e:
                last_err = e
                time.sleep(min(15.0, 2.0 ** attempt))

        raise OpenAIAPIError(format_openai_error_km(last_err))

    # --- Structured Batch SRT Translation ---
    def _build_batch_messages(
        self,
        batch_items: List[Tuple[int, str, float]],
        prev_context: List[str],
        glossary: List[GlossaryItem],
        target_lang_name: str,
        custom_prompt: str = "",
    ) -> List[Dict[str, str]]:
        glossary_txt = "\n".join(
            f"- {g.src_term} -> {g.km_term}" for g in glossary if g.src_term and g.km_term
        )
        base_sys = (
            f"You are a professional drama/movie subtitle translator translating Chinese dialogue into natural spoken {target_lang_name}.\n"
            "Rules:\n"
            "1. Keep dialogue natural, conversational, emotional, and concise enough to speak comfortably within each line's duration.\n"
            "2. Strictly follow character/term glossary below if provided.\n"
            "3. Preserve exact indices. Return ONLY a JSON object with shape {\"translations\": {\"<index>\": \"<translated_text>\"}}.\n"
            "4. Do NOT include any explanation, markdown commentary, or notes."
        )
        if custom_prompt and custom_prompt.strip():
            base_sys = f"{base_sys}\nAdditional user style instructions:\n{custom_prompt.strip()}"
        if glossary_txt:
            base_sys = f"{base_sys}\nGlossary:\n{glossary_txt}"

        ctx_block = ""
        if prev_context:
            ctx_block = "Previous lines (context only, do not translate):\n" + "\n".join(f"- {c}" for c in prev_context[-3:]) + "\n\n"

        lines_payload = [
            {"index": idx, "slot_sec": round(slot, 2), "text": txt}
            for idx, txt, slot in batch_items
        ]
        user_msg = (
            f"{ctx_block}"
            f"Translate every entry below into {target_lang_name}. Return valid JSON matching every index:\n"
            + json.dumps(lines_payload, ensure_ascii=False)
        )
        return [
            {"role": "system", "content": base_sys},
            {"role": "user", "content": user_msg},
        ]

    def _call_chat_completion_with_retry(
        self,
        messages: List[Dict[str, str]],
        model: str,
        api_key: str,
        base_url: str,
        cancel_cb: Optional[Callable[[], bool]] = None,
        max_retries: int = 4,
    ) -> Tuple[str, OpenAIUsageStats]:
        endpoint = f"{base_url}/chat/completions"
        use_json_format = True
        last_err: Exception = RuntimeError("Unknown error")

        for attempt in range(max_retries):
            if cancel_cb and cancel_cb():
                raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")
            body: Dict[str, object] = {
                "model": model,
                "messages": messages,
                "temperature": 0.3,
            }
            if use_json_format:
                body["response_format"] = {"type": "json_object"}

            try:
                with httpx.Client(timeout=45.0) as client:
                    resp = client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    u_raw = data.get("usage") or {}
                    p_tok = int(u_raw.get("prompt_tokens", 0))
                    c_tok = int(u_raw.get("completion_tokens", 0))
                    in_r, out_r = _MODEL_PRICING_PER_1M.get(model, (0.50, 2.00))
                    usd = (p_tok / 1_000_000.0) * in_r + (c_tok / 1_000_000.0) * out_r
                    return content, OpenAIUsageStats(prompt_tokens=p_tok, completion_tokens=c_tok, estimated_usd=usd)

                if resp.status_code == 400 and use_json_format and "response_format" in resp.text.lower():
                    use_json_format = False
                    continue
                if resp.status_code == 401:
                    raise OpenAIAPIError(format_openai_error_km(RuntimeError("401"), 401, resp.text))
                if resp.status_code == 429 or resp.status_code >= 500:
                    msg = format_openai_error_km(RuntimeError(resp.text), resp.status_code, resp.text)
                    if "អស់ Quota" in msg:
                        raise OpenAIAPIError(msg)
                    last_err = OpenAIAPIError(msg)
                    time.sleep(min(20.0, 2.0 ** (attempt + 1)))
                    continue
                raise OpenAIAPIError(format_openai_error_km(RuntimeError(resp.text), resp.status_code, resp.text))
            except OpenAIAPIError:
                raise
            except Exception as e:
                last_err = e
                time.sleep(min(15.0, 2.0 ** attempt))

        raise OpenAIAPIError(format_openai_error_km(last_err))

    def translate_segments_batch(
        self,
        segments: List[Segment],
        glossary: Optional[List[GlossaryItem]] = None,
        target_lang_name: str = "Khmer (ភាសាខ្មែរ)",
        batch_size: int = 30,
        skip_imported: bool = False,
        fallback_fn: Optional[Callable[[Segment], str]] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        usage_cb: Optional[Callable[[OpenAIUsageStats], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[Segment], OpenAIUsageStats]:
        """
        Translate segments in batches of 20-40 lines with structured JSON verification,
        automatic missing-index retry, concurrency, and optional Google fallback.
        """
        key = self._resolve_key()
        if not key:
            raise OpenAIAPIError("សូមបញ្ចូល OpenAI API Key ក្នុង Settings → OpenAI / ChatGPT ជាមុនសិន!")

        model = (getattr(self.settings, "openai_trans_model", "gpt-4o-mini") or "gpt-4o-mini").strip()
        base_url = self._resolve_base_url()
        custom_prompt = getattr(self.settings, "openai_custom_prompt", "") or ""
        concurrency = max(1, min(6, int(getattr(self.settings, "openai_concurrency", 2) or 2)))
        allow_fallback = bool(getattr(self.settings, "openai_fallback_google", True)) and (fallback_fn is not None)
        bs = max(10, min(40, int(batch_size)))

        indexed_items: List[Tuple[int, Segment]] = []
        for pos_i, seg in enumerate(segments):
            if skip_imported and getattr(seg, "translation_source", "") == "imported":
                continue
            if (seg.text_src or "").strip():
                indexed_items.append((pos_i, seg))

        total_usage = OpenAIUsageStats()
        if not indexed_items:
            return segments, total_usage

        batches: List[Tuple[List[Tuple[int, str, float]], List[str]]] = []
        for start in range(0, len(indexed_items), bs):
            chunk = indexed_items[start : start + bs]
            prev_ctx = [seg.text_src for _, seg in indexed_items[max(0, start - 3) : start]]
            items_payload = [(pos_i, seg.text_src, seg.slot) for pos_i, seg in chunk]
            batches.append((items_payload, prev_ctx))

        def _process_one_batch(batch_pair) -> Tuple[Dict[int, str], OpenAIUsageStats]:
            items_payload, prev_ctx = batch_pair
            expected = [x[0] for x in items_payload]
            item_lookup = {x[0]: x for x in items_payload}
            batch_usage = OpenAIUsageStats()
            results_map: Dict[int, str] = {}

            # Primary attempt + up to 2 targeted retries for missing/invalid indices ONLY
            pending_indices = list(expected)
            for _retry_pass in range(3):
                if cancel_cb and cancel_cb():
                    raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")
                sub_payload = [item_lookup[i] for i in pending_indices]
                msgs = self._build_batch_messages(
                    sub_payload, prev_ctx, glossary or [], target_lang_name, custom_prompt
                )
                raw_out, u = self._call_chat_completion_with_retry(
                    msgs, model=model, api_key=key, base_url=base_url, cancel_cb=cancel_cb
                )
                batch_usage.add(u)
                matched, missing = validate_and_extract_batch_json(raw_out, pending_indices)
                results_map.update(matched)
                if not missing:
                    break
                pending_indices = missing

            return results_map, batch_usage

        done_batches = 0
        total_batches = len(batches)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_map = {pool.submit(_process_one_batch, b): b for b in batches}
            for fut in as_completed(future_map):
                if cancel_cb and cancel_cb():
                    raise OpenAIAPIError("បានបោះបង់ដំណើរការ។")
                batch_pair = future_map[fut]
                items_payload = batch_pair[0]
                try:
                    res_map, b_usage = fut.result()
                    total_usage.add(b_usage)
                    if usage_cb:
                        usage_cb(total_usage)
                    for pos_i, _txt, _slot in items_payload:
                        seg = segments[pos_i]
                        if pos_i in res_map and res_map[pos_i]:
                            seg.text_km = res_map[pos_i]
                            seg.translation_source = "auto"
                            seg.tts_outdated = True
                        elif allow_fallback:
                            seg.text_km = fallback_fn(seg)
                            seg.translation_source = "auto"
                            seg.tts_outdated = True
                except OpenAIAPIError as api_err:
                    # Fatal invalid key / exhausted quota stops batch immediately unless user asked to fallback
                    if "401" in str(api_err) or "API Key" in str(api_err) or "Quota" in str(api_err):
                        raise
                    if allow_fallback:
                        log.warning("Batch OpenAI failed (%s); falling back to Google Translate.", api_err)
                        for pos_i, _txt, _slot in items_payload:
                            seg = segments[pos_i]
                            seg.text_km = fallback_fn(seg)
                            seg.translation_source = "auto"
                            seg.tts_outdated = True
                    else:
                        raise
                except Exception as exc:
                    if allow_fallback:
                        log.warning("Batch OpenAI unexpected error (%s); falling back to Google Translate.", exc)
                        for pos_i, _txt, _slot in items_payload:
                            seg = segments[pos_i]
                            seg.text_km = fallback_fn(seg)
                            seg.translation_source = "auto"
                            seg.tts_outdated = True
                    else:
                        raise OpenAIAPIError(format_openai_error_km(exc))

                done_batches += 1
                if progress_cb:
                    pct = int((done_batches / max(1, total_batches)) * 100)
                    progress_cb(
                        pct,
                        f"ChatGPT កំពុងបកប្រែ batch {done_batches}/{total_batches} "
                        f"({total_usage.prompt_tokens + total_usage.completion_tokens:,} tokens ≈ ${total_usage.estimated_usd:.4f})..."
                    )

        log.info(
            "OpenAI batch translation done (%s): %d lines, %d+%d tokens (~$%.5f)",
            model, len(indexed_items), total_usage.prompt_tokens, total_usage.completion_tokens, total_usage.estimated_usd
        )
        return segments, total_usage
