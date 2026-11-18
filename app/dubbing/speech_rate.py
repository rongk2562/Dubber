"""
Speech Rate Budget & Khmer Syllable Counter.
Implements phonetic and orthographic Khmer syllable segmentation,
speech-rate calculation, and budget limits according to natural Khmer speech cadence (~5-6 syllables/sec).
"""

import re
from typing import Tuple


def khmer_syllable_count(text: str) -> int:
    """
    Accurately count spoken syllables in Khmer dialogue.
    Handles base consonants, subscript consonants (Coeng), independent/dependent vowels,
    and coda consonants.
    """
    if not text:
        return 0

    # Clean whitespace and non-Khmer punctuation
    clean = re.sub(r'[\s\.\,\!\?\"\'\(\)\—\-\:;។៕ៗ]+', ' ', text).strip()
    if not clean:
        return 0

    words = clean.split()
    total_syllables = 0

    # Pattern matching Khmer syllable onset + vowels + coda
    # Consonants: \u1780-\u17a2
    # Independent vowels: \u17a3-\u17b3
    # Dependent vowels: \u17b6-\u17c5
    # Subscript sign (Coeng): \u17d2
    # Diacritics: \u17c6-\u17d3
    p = re.compile(
        r'(?:'
        r'[\u17a3-\u17b3]|'  # Independent vowel
        r'(?<!\u17d2)[\u1780-\u17a2](?:\u17d2[\u1780-\u17a2])*' # Onset cluster
        r'(?:[\u17b6-\u17c5]|[\u17c6\u17c7\u17cb\u17d0])*'     # Vowels/diacritics
        r'(?:(?<!\u17d2)[\u1780-\u17a2](?:[\u17cb\u17cc\u17cd\u17d1])?(?![\u17b6-\u17c5\u17d2\u17c6]))?' # Coda
        r')',
        re.UNICODE
    )

    for word in words:
        matches = [m.group(0) for m in p.finditer(word) if m.group(0)]
        valid = [m for m in matches if any('\u1780' <= c <= '\u17d3' for c in m)]
        total_syllables += max(1, len(valid))

    return total_syllables


def extract_khmer_syllables(text: str) -> list[str]:
    """
    Extract individual spoken Khmer syllable tokens from text.
    """
    if not text:
        return []
    clean = re.sub(r'[\s\.\,\!\?\"\'\(\)\—\-\:;។៕ៗ]+', ' ', text).strip()
    if not clean:
        return []

    words = clean.split()
    p = re.compile(
        r'(?:'
        r'[\u17a3-\u17b3]|'
        r'(?<!\u17d2)[\u1780-\u17a2](?:\u17d2[\u1780-\u17a2])*'
        r'(?:[\u17b6-\u17c5]|[\u17c6\u17c7\u17cb\u17d0])*'
        r'(?:(?<!\u17d2)[\u1780-\u17a2](?:[\u17cb\u17cc\u17cd\u17d1])?(?![\u17b6-\u17c5\u17d2\u17c6]))?'
        r')',
        re.UNICODE
    )
    sylls = []
    for word in words:
        matches = [m.group(0) for m in p.finditer(word) if m.group(0)]
        valid = [m for m in matches if any('\u1780' <= c <= '\u17d3' for c in m)]
        sylls.extend(valid)
    return sylls


def get_speech_budget(slot_sec: float) -> Tuple[int, int]:
    """
    Calculate target and maximum allowed syllables for a given speaking time slot:
    - Target rate: ~5.5 syllables/sec (natural conversational pacing)
    - Max rate: 6.0 syllables/sec (upper conversational threshold)
    - Hard limit: 7.5 syllables/sec (absolute maximum intelligible limit)
    """
    slot = max(0.3, float(slot_sec))
    target_syllables = max(2, int(round(slot * 5.5)))
    max_syllables = max(3, int(round(slot * 6.0)))
    return target_syllables, max_syllables


def calculate_speech_rate(text: str, duration_sec: float) -> float:
    """Calculate syllables per second."""
    if duration_sec <= 0.01:
        return 0.0
    syllables = khmer_syllable_count(text)
    return round(syllables / duration_sec, 2)


def merge_short_segments(segments: list, max_gap: float = 0.45, min_slot: float = 0.9) -> list:
    """
    Intelligently merge very short segments (< min_slot sec or < 4 syllables)
    into adjacent segments if the silence gap between them is < max_gap sec.
    Prevents overly compressed micro-clips and provides comfortable speech slots.
    """
    if not segments or len(segments) <= 1:
        return segments

    merged = []
    i = 0
    total = len(segments)
    while i < total:
        cur = segments[i]
        if i + 1 < total:
            nxt = segments[i + 1]
            gap = nxt.start - cur.end
            cur_syll = khmer_syllable_count(getattr(cur, "text_km", "") or cur.text_src)
            if (cur.slot < min_slot or cur_syll < 4) and 0.0 <= gap < max_gap:
                cur.end = nxt.end
                if nxt.text_src:
                    cur.text_src = (cur.text_src + " " + nxt.text_src).strip()
                if getattr(cur, "text_km", "") or getattr(nxt, "text_km", ""):
                    cur.text_km = (getattr(cur, "text_km", "") + " " + getattr(nxt, "text_km", "")).strip()
                merged.append(cur)
                i += 2
                continue
        merged.append(cur)
        i += 1
    return merged
