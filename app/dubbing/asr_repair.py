"""
ASR Homophone Repair & Chinese Text Normalization Suite.
Repairs Whisper ASR phonetic errors, homophones, and applies OpenCC t2s normalization
using scene story context and domain glossaries.
"""

import os
import re
import json
from typing import List, Dict, Optional, Callable, Any
from app.models import Segment, ProjectSettings

try:
    import opencc
    _OPENCC_T2S = opencc.OpenCC("t2s")
except Exception:
    _OPENCC_T2S = None


# High-frequency homophone error mappings in Chinese short dramas / fantasy / mystic cinema
CHINESE_DRAMA_HOMOPHONES: Dict[str, str] = {
    # Food & daily items
    "潮湖路": "糖葫芦",
    "巢湖路": "糖葫芦",
    "朝湖路": "糖葫芦",

    # Mystic / fortune-telling / Taoist terminology
    "祖芬": "祖坟",
    "老祖芬": "老祖坟",
    "两团器中": "两团黑气",
    "两团气中": "两团黑气",
    "插架": "掐架",
    "背几发梁": "背脊发凉",
    "背几发凉": "背脊发凉",
    "背脊发梁": "背脊发凉",
    "可太协会了": "可太邪乎了",
    "算命看象": "算命看相",
    "不说同板": "不收铜板",
    "不说铜板": "不收铜板",
    "躲到风马": "夺刀封门",
    "伤根发亮": "山根发亮",
    "小偏才": "小偏财",
    "轻色的线": "青色的线",
    "做秀礼": "做寿礼",
    "四拆电工": "施舍点功德",
    "前目全家": "钱某全家",
    "前目": "钱某",
    "这点博营": "这点薄银",
    "离火见冲": "烈火尖冲",
    "灾霍币": "灾祸避",
    "烂命格": "烂命格",

    # Character names
    "素宝": "苏宝",
    "速宝": "苏宝",
    "暴守": "宝守",
}


def normalize_chinese_t2s(text: str) -> str:
    """Normalize Traditional Chinese to Simplified Chinese using OpenCC."""
    if not text:
        return ""
    if _OPENCC_T2S:
        try:
            return _OPENCC_T2S.convert(text)
        except Exception:
            pass
    return text


def repair_homophones_rule_based(text: str) -> str:
    """Apply rule-based homophone substitutions for common film terms."""
    res = normalize_chinese_t2s(text)
    for err, fix in CHINESE_DRAMA_HOMOPHONES.items():
        if err in res:
            res = res.replace(err, fix)
    return res


class ASRRepairer:
    """
    Performs multi-stage Chinese ASR repair:
    1. OpenCC traditional-to-simplified normalization.
    2. Context-aware LLM batch repair (30-50 lines with surrounding dialogue).
    3. Rule-based film drama homophone dictionary fallback.
    """

    def __init__(self, settings: Optional[ProjectSettings] = None):
        self.settings = settings or ProjectSettings()

    def repair_segments(
        self,
        segments: List[Segment],
        story_context: str = "Chinese costume mystic fantasy short drama (Little fortune teller Su Bao)",
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> List[Segment]:
        """Repair Whisper homophone mistakes across all segments in batches."""
        total = len(segments)
        if total == 0:
            return segments

        # Step 1: OpenCC Normalization & Instant Rule-based pass
        for s in segments:
            orig = s.text_src
            fixed = repair_homophones_rule_based(orig)
            s.zh_fixed = fixed
            s.asr_changed = bool(fixed != orig)
            s.asr_confidence = 0.95 if s.asr_changed else 1.0

        # Step 2: LLM Context-Aware Repair Pass (if LLM key available)
        provider = (self.settings.translation_provider or "").lower()
        has_key = bool(self.settings.gemini_api_key or self.settings.openai_api_key or self.settings.claude_api_key)

        if has_key:
            batch_size = 35
            for b_idx in range(0, total, batch_size):
                batch = segments[b_idx: b_idx + batch_size]
                if progress_cb:
                    pct = int((b_idx / total) * 100)
                    progress_cb(pct, f"Repairing Chinese homophones with LLM (lines {b_idx+1}-{min(total, b_idx+batch_size)})...")

                try:
                    self._repair_batch_llm(batch, story_context)
                except Exception as e:
                    print(f"[ASRRepairer] Note LLM repair batch failed: {e}")

        # Final guarantee: re-apply domain homophones on zh_fixed
        for s in segments:
            s.zh_fixed = repair_homophones_rule_based(s.zh_fixed or s.text_src)
            s.text_src = s.zh_fixed  # Use repaired Chinese as primary source text

        if progress_cb:
            progress_cb(100, f"ASR repair complete: {sum(1 for s in segments if s.asr_changed)} homophones repaired.")

        return segments

    def _repair_batch_llm(self, batch: List[Segment], story_context: str):
        """Call LLM with batch of lines to fix homophones."""
        lines_payload = [{"id": s.id, "text": s.zh_fixed or s.text_src} for s in batch]
        prompt = (
            f"You are an expert Chinese script supervisor and ASR proofreader for Chinese film dubbing.\n"
            f"Context: {story_context}.\n"
            f"The following speech lines were transcribed by Whisper ASR and contain phonetic homophone mistakes "
            f"(e.g., '潮湖路' -> '糖葫芦', '祖芬' -> '祖坟', '两团器中' -> '两团黑气', '插架' -> '掐架', '背几发梁' -> '背脊发凉', '可太协会了' -> '可太邪乎了').\n"
            f"Fix any obvious phonetic/homophone errors based on the storyline and characters.\n"
            f"Keep correct lines untouched.\n"
            f"Return ONLY valid JSON array of objects:\n"
            f"[{{\"id\": <int>, \"zh_fixed\": \"<string>\", \"confidence\": <float 0.0-1.0>, \"changed\": <bool>}}]\n\n"
            f"Input lines:\n{json.dumps(lines_payload, ensure_ascii=False)}"
        )

        resp_text = ""
        if self.settings.gemini_api_key:
            import httpx
            candidate_models = [
                "gemini-flash-lite-latest",
                "gemini-3.1-flash-lite",
                "gemini-3-flash-preview",
                "gemini-3.5-flash-lite",
                "gemini-flash-latest",
                "gemini-2.5-flash",
                "gemini-1.5-flash",
            ]
            api_key = self.settings.gemini_api_key.strip()
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048}
            }
            with httpx.Client(timeout=15.0) as client:
                for model_name in candidate_models:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    try:
                        resp = client.post(url, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts:
                                    resp_text = parts[0].get("text", "")
                                    break
                        elif resp.status_code in (400, 404, 503):
                            continue
                    except Exception:
                        continue
        elif self.settings.openai_api_key:
            import httpx
            headers = {"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"}
            body = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2
            }
            with httpx.Client(timeout=12.0) as client:
                r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body)
                resp_text = r.json()["choices"][0]["message"]["content"]

        if resp_text:
            match = re.search(r'\[.*\]', resp_text, re.DOTALL)
            if match:
                items = json.loads(match.group(0))
                id_map = {item["id"]: item for item in items if "id" in item}
                for s in batch:
                    if s.id in id_map:
                        item = id_map[s.id]
                        if item.get("changed") and item.get("zh_fixed"):
                            s.zh_fixed = item["zh_fixed"]
                            s.asr_changed = True
                            s.asr_confidence = float(item.get("confidence", 0.9))
