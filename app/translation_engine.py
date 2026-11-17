"""
Translation Engine for AI Movie Translator
F4: Per-segment translation to Khmer, glossary support, shortening for Time-Sync
Supports Gemini, Claude, OpenAI, and Web translation with glossary replacement.
"""

import os
import re
import time
from typing import List, Dict, Optional, Callable
import httpx
from app.models import Segment, GlossaryItem, ProjectSettings
from app.config import TARGET_LANG_MAP

class TranslationEngine:
    def __init__(self, settings: Optional[ProjectSettings] = None):
        self.settings = settings or ProjectSettings()
        
    def apply_glossary(self, text: str, glossary: List[GlossaryItem]) -> str:
        """Replace defined glossary terms in text."""
        result = text
        for item in glossary:
            if not item.src_term or not item.km_term:
                continue
            pattern = re.compile(re.escape(item.src_term), re.IGNORECASE)
            result = pattern.sub(item.km_term, result)
        return result

    def humanize_khmer_dialogue(self, km_text: str, src_text: str = "") -> str:
        """
        Transform formal/robotic textbook translations into natural, expressive movie dubbing dialogue.
        - Replaces stiff book grammar with natural spoken Khmer dialogue particles.
        - Preserves emotional punctuation (? and !) matching original speech for TTS prosody.
        """
        text = km_text.strip()
        if not text:
            return ""

        # Conversational transformation rules for Khmer movie dialogue
        replacements = [
            # 1. Rigid formal question starters
            (r'^តើអ្នកកំពុងធ្វើអ្វី(?:\s*|)(?:នៅទីនេះ|ទីនេះ|)(?:\?|)$', 'ឯងកំពុងធ្វើអីហ្នឹង?'),
            (r'^តើអ្នកកំពុងតែធ្វើអ្វី(?:\?|)$', 'ឯងកំពុងធ្វើអីហ្នឹង?'),
            (r'^តើមានអ្វីកើតឡើង(?:\?|)$', 'មានរឿងអីហ្នឹង?'),
            (r'^តើមានរឿងអ្វីកើតឡើង(?:\?|)$', 'មានរឿងអីកើតឡើងហ្នឹង?'),
            (r'^តើអ្នកចង់មានន័យថាម៉េច(?:\?|)$', 'ឯងចង់មានន័យថាម៉េចហ្នឹង?'),
            (r'^តើអ្នកកំពុងទៅណា(?:\?|)$', 'ឯងទៅណាហ្នឹង?'),
            (r'^តើអ្នកទៅណា(?:\?|)$', 'ឯងទៅណាហ្នឹង?'),
            (r'^តើអ្នកសុខសប្បាយទេ(?:\?|)$', 'សុខសប្បាយជាទេ?'),
            (r'^តើអ្នកដឹងទេ(?:\?|)$', 'ឯងដឹងអត់?'),
            (r'^តើអ្នកយល់ព្រមទេ(?:\?|)$', 'ឯងព្រមទេ?'),
            (r'^តើពិតមែនទេ(?:\?|)$', 'ពិតមែនហ្អ៎?'),
            (r'^មិនអាចទៅរួចទេ(?:\!|)$', 'មិនអាចទេ!'),
            (r'^សូមជួយខ្ញុំផង(?:\!|)$', 'ជួយខ្ញុំផង!'),
            (r'^កុំបារម្ភអំពីវា(?:\!|)$', 'កុំបារម្ភអី'),
            (r'^ខ្ញុំមិនដឹងទេ$', 'ខ្ញុំអត់ដឹងទេ'),
            (r'^ខ្ញុំមិនអាចជឿបានទេ$', 'ខ្ញុំមិនជឿដាច់ខាត!'),
            (r'^ឆាប់ឡើងទៅ$', 'ឆាប់ឡើង!'),
            
            # 2. General conversational particle refinements
            (r'\bតើអ្នក\b', 'ឯង'),
            (r'\bអំពីវា\b', 'អី'),
            (r'\bនៅទីនេះ\b', 'នៅហ្នឹង'),
            (r'\bនៅទីនោះ\b', 'នៅនោះ'),
            (r'\bមិនមែនទេ\b', 'អត់ទេ'),
            (r'\bតើ\b', ''), # In movies, casual questions omit the formal "តើ"
        ]

        for pattern, repl in replacements:
            text = re.sub(pattern, repl, text).strip()

        # Clean multiple spaces
        text = re.sub(r'\s+', ' ', text).strip()

        # Transfer emotional punctuation from src_text if missing
        src = src_text.strip()
        if src.endswith("?") and not text.endswith("?"):
            text = re.sub(r'[\.\,\—\។]+$', '', text) + "?"
        elif src.endswith("!") and not text.endswith("!"):
            text = re.sub(r'[\.\,\—\។]+$', '', text) + "!"

        return text

    def _post_process_translation(self, translated_text: str, src_text: str, target_lang: str) -> str:
        """Apply language-specific dialogue refinement and preserve emotional punctuation."""
        if target_lang == "km":
            return self.humanize_khmer_dialogue(translated_text, src_text)

        text = translated_text.strip()
        if not text:
            return ""
        src = src_text.strip()
        if src.endswith("?") and not (text.endswith("?") or text.endswith("？")):
            text = re.sub(r'[\.\,\—\。]+$', '', text) + "?"
        elif src.endswith("!") and not (text.endswith("!") or text.endswith("！")):
            text = re.sub(r'[\.\,\—\。]+$', '', text) + "!"
        return text

    def translate_segment(
        self,
        src_text: str,
        glossary: Optional[List[GlossaryItem]] = None,
        context_before: str = "",
        context_after: str = "",
        target_lang: Optional[str] = None
    ) -> str:
        """
        Translate a single speech segment into natural conversational target language.
        """
        src_text = src_text.strip()
        if not src_text:
            return ""

        t_lang = target_lang or getattr(self.settings, "target_language", "km") or "km"
        glossary = glossary or []
        provider = self.settings.translation_provider.lower()

        # 1. Try Gemini API
        if provider == "gemini" and self.settings.gemini_api_key:
            try:
                res = self._translate_gemini(src_text, glossary, target_lang=t_lang)
                return self._post_process_translation(res, src_text, t_lang)
            except Exception as e:
                print(f"[Translation] Gemini failed: {e}")

        # 2. Try Claude API
        if provider == "claude" and self.settings.claude_api_key:
            try:
                res = self._translate_claude(src_text, glossary, target_lang=t_lang)
                return self._post_process_translation(res, src_text, t_lang)
            except Exception as e:
                print(f"[Translation] Claude failed: {e}")

        # 3. Try OpenAI API
        if provider == "openai" and self.settings.openai_api_key:
            try:
                res = self._translate_openai(src_text, glossary, target_lang=t_lang)
                return self._post_process_translation(res, src_text, t_lang)
            except Exception as e:
                print(f"[Translation] OpenAI failed: {e}")

        # 4. Fallback: Web / Smart Heuristic Translation
        res = self._translate_web_or_heuristic(src_text, glossary, target_lang=t_lang)
        return self._post_process_translation(res, src_text, t_lang)

    def translate_segments(
        self,
        segments: List[Segment],
        glossary: Optional[List[GlossaryItem]] = None,
        target_lang: Optional[str] = None
    ) -> List[Segment]:
        """Batch translate a list of segments in-place."""
        t_lang = target_lang or getattr(self.settings, "target_language", "km") or "km"
        for seg in segments:
            if seg.text_src and seg.text_src.strip():
                seg.text_km = self.translate_segment(seg.text_src, glossary=glossary, target_lang=t_lang)
        return segments

    def _build_prompt(
        self,
        src_text: str,
        glossary: List[GlossaryItem],
        shorten: bool = False,
        target_lang: str = "km"
    ) -> str:
        lang_meta = TARGET_LANG_MAP.get(target_lang) or TARGET_LANG_MAP["km"]
        lang_name = lang_meta["prompt_name"]

        glossary_rules = ""
        if glossary:
            glossary_rules = "Use the following specific glossary terms:\n" + "\n".join(
                [f"- '{g.src_term}' -> '{g.km_term}'" for g in glossary]
            )

        if target_lang == "km":
            task_instruction = (
                "Translate this dialogue into concise, natural spoken Khmer (movie dubbing style). "
                "KEEP IT SHORT AND PUNCHY with emotional spoken particles so the voice actor can speak it naturally."
                if shorten else
                "You are a top-tier movie dubbing director translating Hollywood dialogue into natural, expressive spoken Khmer (សម្ដីនិយាយបែបភាពយន្តរស់រវើក មានអារម្មណ៍ដូចមនុស្សនិយាយពិតៗ មិនមែនពាក្យសៀវភៅ ឬពាក្យរដ្ឋបាលឡើយ). "
                "Use natural conversational spoken particles (ដូចជា 'ហ្នឹង', 'ណា', 'ហ្អ៎', 'តើ', 'អី', 'ឱ្យសោះ', 'មែនទែន'). "
                "Retain all question marks (?) and exclamation marks (!) to ensure accurate voice actor intonation."
            )
        else:
            task_instruction = (
                f"Translate this dialogue into concise, natural spoken {lang_name} (movie dubbing style). "
                f"KEEP IT SHORT AND PUNCHY so the voice actor can speak it within the scene's timing."
                if shorten else
                f"You are a top-tier movie dubbing director translating film dialogue into natural, expressive spoken {lang_name}. "
                f"Make it sound like real human actors speaking naturally in a movie, and retain question marks (?) and exclamation marks (!)."
            )

        return (
            f"You are a professional movie dubbing translator into {lang_name}.\n"
            f"{task_instruction}\n"
            f"{glossary_rules}\n"
            f"Only return the {lang_name} translation text without quotation marks or explanation.\n\n"
            f"Dialogue: \"{src_text}\""
        )

    def _translate_gemini(self, src_text: str, glossary: List[GlossaryItem], shorten: bool = False, target_lang: str = "km") -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.settings.gemini_api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = self._build_prompt(src_text, glossary, shorten=shorten, target_lang=target_lang)
        response = model.generate_content(prompt)
        return response.text.strip().strip('"').strip("'")

    def _translate_claude(self, src_text: str, glossary: List[GlossaryItem], shorten: bool = False, target_lang: str = "km") -> str:
        prompt = self._build_prompt(src_text, glossary, shorten=shorten, target_lang=target_lang)
        headers = {
            "x-api-key": self.settings.claude_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        body = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 150,
            "messages": [{"role": "user", "content": prompt}]
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
            data = resp.json()
            return data["content"][0]["text"].strip().strip('"').strip("'")

    def _translate_openai(self, src_text: str, glossary: List[GlossaryItem], shorten: bool = False, target_lang: str = "km") -> str:
        lang_meta = TARGET_LANG_MAP.get(target_lang) or TARGET_LANG_MAP["km"]
        lang_name = lang_meta["prompt_name"]
        prompt = self._build_prompt(src_text, glossary, shorten=shorten, target_lang=target_lang)
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json"
        }
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": f"You are a professional movie dubbing translator to {lang_name}."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body)
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip().strip('"').strip("'")

    def _translate_web_or_heuristic(self, src_text: str, glossary: List[GlossaryItem], target_lang: str = "km") -> str:
        """Resilient translator with fallback for common phrases and glossary."""
        # 1. Check Khmer phrase dictionary when target_lang == "km"
        if target_lang == "km":
            common_phrases = {
                "where are you going?": "តើអ្នកកំពុងទៅណា?",
                "where are you going": "ឯងទៅណាហ្នឹង?",
                "hello": "សួស្តី",
                "hi": "សួស្តី",
                "what happened?": "មានរឿងអីកើតឡើង?",
                "what's going on?": "មានរឿងអីហ្នឹង?",
                "let's go": "តោះទៅ",
                "come on": "ឆាប់ឡើង",
                "stop": "ឈប់សិន",
                "wait for me": "ចាំខ្ញុំផង",
                "look at that": "មើលនោះនែ",
                "i love you": "បងស្រឡាញ់អូន",
                "thank you": "អរគុណច្រើន",
                "good morning": "អរុណសួស្តី",
                "good night": "រាត្រីសួស្តី",
                "help me": "ជួយខ្ញុំផង",
                "don't worry": "កុំបារម្ភអី",
                "yes": "បាទ / ចាស",
                "no": "ទេ",
                "of course": "ប្រាកដហើយ",
                "i don't know": "ខ្ញុំមិនដឹងទេ",
                "be careful": "ប្រយ័ត្នខ្លួន",
            }
            cleaned = src_text.strip().lower()
            if cleaned in common_phrases:
                res = common_phrases[cleaned]
                return self.apply_glossary(res, glossary)

        # 2. Try Google Client translation endpoints (clients5 & clients1 dict-chrome-ex)
        import urllib.request
        import urllib.parse
        import json

        encoded = urllib.parse.quote(src_text)
        tl = urllib.parse.quote(target_lang)
        endpoints = [
            f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl={tl}&q={encoded}",
            f"https://clients1.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl={tl}&q={encoded}",
            f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={tl}&dt=t&q={encoded}",
        ]

        for url in endpoints:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
                )
                with urllib.request.urlopen(req, timeout=4) as response:
                    res_raw = response.read().decode("utf-8")
                    data = json.loads(res_raw)
                    if isinstance(data, list) and len(data) > 0:
                        # clients5 dict-chrome-ex format: [['translated_text', 'src_lang']]
                        if isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], str):
                            km = data[0][0].strip()
                            if km:
                                return self.apply_glossary(km, glossary)
                        # gtx single format: [[['translated_text', ...]]]
                        elif isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], list):
                            km = "".join([part[0] for part in data[0] if isinstance(part, list) and part[0]]).strip()
                            if km:
                                return self.apply_glossary(km, glossary)
                        # flat string format: ['translated_text', ...]
                        elif isinstance(data[0], str):
                            km = data[0].strip()
                            if km:
                                return self.apply_glossary(km, glossary)
            except Exception:
                continue

        # 3. Fallback: Return original or glossary-applied string with marker
        return self.apply_glossary(src_text, glossary)

    def shorten_khmer_text(self, src_text: str, current_km: str, glossary: Optional[List[GlossaryItem]] = None) -> str:
        """
        Time-Sync Rule 2:
        'Ask the LLM to shorten the translated text'
        Condenses the translated text to fit into a tighter slot while retaining meaning.
        """
        t_lang = getattr(self.settings, "target_language", "km") or "km"
        glossary = glossary or []
        provider = self.settings.translation_provider.lower()

        # LLM based shortening
        if provider == "gemini" and self.settings.gemini_api_key:
            try:
                return self._translate_gemini(src_text, glossary, shorten=True, target_lang=t_lang)
            except Exception:
                pass

        if provider == "claude" and self.settings.claude_api_key:
            try:
                return self._translate_claude(src_text, glossary, shorten=True, target_lang=t_lang)
            except Exception:
                pass

        if provider == "openai" and self.settings.openai_api_key:
            try:
                return self._translate_openai(src_text, glossary, shorten=True, target_lang=t_lang)
            except Exception:
                pass

        # Heuristic shortening: Remove polite particles, redundant pronouns, or punctuation
        shortened = current_km
        if t_lang == "km":
            words_to_strip = ["សូម", "តើ", "បាទ", "ចាស", "ផងដែរ", "ទាំងអស់គ្នា"]
            for w in words_to_strip:
                if len(shortened) > len(w) + 3:
                    shortened = shortened.replace(w, "").strip()

        return shortened if shortened else current_km
