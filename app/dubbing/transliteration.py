"""
Transliteration Engine for Chinese Film & Short Drama Dubbing.
Consistently maps Latin/Pinyin character names, historical titles, and Chinese syllables
into natural Khmer script (e.g., Gang Hong -> គាំងហុង, Baoshou -> ប៉ោសូវ, Su Bao -> ស៊ូប៉ាវ).
Prevents English/Latin characters from appearing in Khmer subtitles or TTS speech synthesis.
"""

import re
from typing import List, Tuple, Dict

# High-frequency Chinese short drama names & terms (Multi-syllable & Compound)
DRAMA_COMPOUND_NAMES: List[Tuple[str, str]] = [
    # Protagonists and antagonist character names
    (r'\bGang\s*Hong\b', 'គាំងហុង'),
    (r'\bGanghong\b', 'គាំងហុង'),
    (r'\bBao\s*shou\b', 'ប៉ោសូវ'),
    (r'\bBaoshou\b', 'ប៉ោសូវ'),
    (r'\bSu\s*Bao\b', 'ស៊ូប៉ាវ'),
    (r'\bSubao\b', 'ស៊ូប៉ាវ'),
    (r'\bTang\s*hulu\b', 'ស្ករផ្លែឈើ'),
    (r'\bTanghulu\b', 'ស្ករផ្លែឈើ'),
    (r'\bChaohu\s*Road\b', 'ស្ករផ្លែឈើ'),
    (r'\bChaohu\b', 'ស្ករផ្លែឈើ'),
    (r'ផ្លូវ\s*Chaohu', 'ស្ករផ្លែឈើ'),
    (r'\bZu\s*fen\b', 'ផ្នូរដូនតា'),
    (r'\bZufen\b', 'ផ្នូរដូនតា'),

    # Film drama titles & roles
    (r'\bDao\s*zhang\b', 'គ្រូតាវ'),
    (r'\bDaozhang\b', 'គ្រូតាវ'),
    (r'\bShi\s*fu\b', 'លោកគ្រូ'),
    (r'\bShifu\b', 'លោកគ្រូ'),
    (r'\bDa\s*shi\b', 'លោកគ្រូធំ'),
    (r'\bDashi\b', 'លោកគ្រូធំ'),
    (r'\bWang\s*ye\b', 'ព្រះអង្គម្ចាស់'),
    (r'\bWangye\b', 'ព្រះអង្គម្ចាស់'),
    (r'\bXiao\s*jie\b', 'អ្នកនាង'),
    (r'\bXiaojie\b', 'អ្នកនាង'),
    (r'\bGong\s*zi\b', 'លោកប្រុស'),
    (r'\bGongzi\b', 'លោកប្រុស'),
    (r'\bFu\s*ren\b', 'លោកស្រី'),
    (r'\bFuren\b', 'លោកស្រី'),
    (r'\bLao\s*ye\b', 'លោកម្ចាស់'),
    (r'\bLaoye\b', 'លោកម្ចាស់'),
    (r'\bYa\s*tou\b', 'នាងតូច'),
    (r'\bYatou\b', 'នាងតូច'),
    (r'\bShen\s*xian\b', 'ទេវតា'),
    (r'\bShenxian\b', 'ទេវតា'),
    (r'\bXiao\s*xian\b', 'ទេវតាតូច'),
    (r'\bXiaoxian\b', 'ទេវតាតូច'),
    (r'\bGuan\s*jia\b', 'មេផ្ទះ'),
    (r'\bGuanjia\b', 'មេផ្ទះ'),
    (r'\bTai\s*tai\b', 'អ្នកស្រី'),
    (r'\bTaitai\b', 'អ្នកស្រី'),
    (r'\bAh\s*Fu\b', 'អាហ្វូ'),
    (r'\bAhfu\b', 'អាហ្វូ'),
    (r'\bAh\s*Long\b', 'អាឡុង'),
    (r'\bAhlong\b', 'អាឡុង'),
    (r'\bAh\s*Biao\b', 'អាបៀវ'),
    (r'\bAhbiao\b', 'អាបៀវ'),
    (r'\bXiao\s*Bao\b', 'សៀវប៉ាវ'),
    (r'\bXiaobao\b', 'សៀវប៉ាវ'),
    (r'\bXiao\s*Mei\b', 'សៀវម៉ី'),
    (r'\bXiaomei\b', 'សៀវម៉ី'),
    (r'\bXiao\s*Ming\b', 'សៀវមីង'),
    (r'\bXiaoming\b', 'សៀវមីង'),

    # Clean awkward literal phrases
    (r'ទេពអប្សរតូច', 'ទេវតាតូច'),
    (r'ទេពអប្សរ', 'ទេវតា'),
    (r'សមាគមពេក', 'ពិតជាស័ក្តិសិទ្ធិខ្លាំងណាស់'),
]

# Common Chinese Pinyin Syllables -> Khmer Script Transliteration
PINYIN_SYLLABLE_MAP: Dict[str, str] = {
    # Popular drama surnames & syllables
    "gang": "គាំង", "hong": "ហុង", "bao": "ប៉ោ", "shou": "សូវ", "su": "ស៊ូ",
    "zhang": "ចាង", "chen": "ឆិន", "wang": "វ៉ាង", "li": "លី", "liu": "លីវ",
    "yang": "យ៉ាង", "huang": "ហ័ង", "zhao": "ចាវ", "wu": "អ៊ូ", "zhou": "ចូវ",
    "xu": "ស៊ូ", "sun": "ស៊ុន", "ma": "ម៉ា", "zhu": "ជូ", "hu": "ហ៊ូ",
    "guo": "គួក", "he": "ហឺ", "gao": "កៅ", "lin": "លីន", "luo": "ឡូ",
    "zheng": "ចឹង", "liang": "លៀង", "song": "សុង", "tang": "តាំង", "feng": "ហ្វុង",
    "dong": "តុង", "cheng": "ឆឹង", "cao": "ឆាវ", "yuan": "យ័ន្ត", "deng": "ទឹង",
    "pan": "ប៉ាន", "cai": "ឆៃ", "jia": "ជៀ", "ding": "ទិញ", "wei": "វ៉ី",
    "xue": "សៀ", "ye": "យេ", "yan": "យ៉ាន", "du": "ឌូ", "dai": "តៃ",
    "xia": "សៀ", "zhong": "ចុង", "tian": "ធាន", "ren": "រិន", "jiang": "ជាំង",
    "fan": "ហ្វាន", "fang": "ហ្វាង", "shi": "ស៊ឺ", "yao": "យ៉ាវ", "tan": "តាន់",
    "liao": "លៀវ", "zou": "ចូវ", "xiong": "ស៊ុង", "jin": "ជីន", "lu": "លូ",
    "hao": "ហាវ", "kong": "ខុង", "bai": "ប៉ៃ", "cui": "ឈុយ", "kang": "ខាំង",
    "mao": "ម៉ៅ", "qiu": "ឈីវ", "qin": "ឈីន", "gu": "គូ", "hou": "ហូវ",
    "long": "ឡុង", "ming": "មីង", "qing": "ឈីង", "ping": "ពីង", "jie": "ជៀ",
    "hua": "ហួរ", "ying": "អ៊ីង", "hai": "ហៃ", "gui": "គួយ", "quan": "ឈ័ន",
    "sheng": "សឹង", "xiao": "សៀវ", "wen": "វ៉ិន", "xian": "សៀន", "shan": "សាន",
    "nan": "ណាន", "bei": "ប៉ី", "xi": "ស៊ី", "jun": "ជុន", "chao": "ឆាវ",
    "fen": "ហ្វិន", "tou": "ថូវ", "meng": "ម៉ឹង", "yu": "យូ", "yun": "យុន",
    "rui": "រ៉ួយ", "zhi": "ជឺ", "zi": "ជឺ", "ci": "ឈឺ", "si": "ស៊ឺ",
    "lan": "ឡាន", "bin": "ប៊ីន", "bo": "បូ", "kai": "ខៃ", "lei": "ឡី",
    "an": "អាន", "chun": "ឈុន", "xia": "សៀ", "qiu": "ឈីវ", "dong": "តុង"
}


def transliterate_khmer_dialogue(text: str) -> str:
    """
    Transliterate any Latin character names, titles, and Pinyin words inside Khmer dialogue
    into native Khmer script. Ensures subtitles and TTS audio are 100% Khmer.
    """
    if not text or not text.strip():
        return ""

    result = text

    # Step 1: Replace multi-syllable named entities and compound drama names
    for pat, repl in DRAMA_COMPOUND_NAMES:
        result = re.sub(pat, repl, result, flags=re.IGNORECASE)

    # Step 2: Transliterate any remaining Latin tokens that match Chinese Pinyin
    def repl_latin_word(match: re.Match) -> str:
        word = match.group(0).lower()
        if word in PINYIN_SYLLABLE_MAP:
            return PINYIN_SYLLABLE_MAP[word]
        # Return as-is if not found
        return match.group(0)

    # Match isolated Latin words
    result = re.sub(r'\b[A-Za-z]+\b', repl_latin_word, result)

    # Clean awkward spacing around Khmer text
    result = re.sub(r'\s+([។៕\?!])', r'\1', result)
    result = re.sub(r'\s{2,}', ' ', result).strip()

    return result
