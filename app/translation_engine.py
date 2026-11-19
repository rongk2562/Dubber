"""
Translation Engine for AI Movie Translator
F4: Per-segment translation to Khmer, glossary support, shortening for Time-Sync
Includes Natural Conversational Movie Dubbing Rewriter & Context-Aware Idiom Adaptation
so translations sound like real humans talking to each other rather than literal word-for-word text.
"""

import os
import re
import time
from typing import List, Dict, Optional, Callable
import httpx
from app.models import Segment, GlossaryItem, ProjectSettings
from app.config import TARGET_LANG_MAP


# Direct idiomatic movie dialogue mappings (English -> Natural Spoken Khmer Dubbing)
MOVIE_DIALOGUE_MAP: Dict[str, str] = {
    "where are you going?": "ឯងគិតទៅណាហ្នឹង?",
    "where are you going": "ឯងគិតទៅណាហ្នឹង?",
    "what are you doing?": "ឯងកំពុងធ្វើអីហ្នឹង?",
    "what are you doing here?": "ឯងមកធ្វើអីនៅហ្នឹង?",
    "what the hell are you doing?": "ឯងកំពុងធ្វើស្អីហ្នឹង?!",
    "what the hell is going on?": "តើវាមានរឿងស្អីកើតឡើងហ្នឹង?!",
    "what the hell is that?": "ស្អីគេហ្នឹង?!",
    "what the hell": "ស្អីគេហ្នឹង!",
    "what's going on?": "មានរឿងអីហ្នឹង?",
    "what is going on?": "មានរឿងអីកើតឡើងហ្នឹង?",
    "what happened?": "មានរឿងអីកើតឡើងហ្នឹង?",
    "what happened to you?": "ឯងកើតអីហ្នឹង?",
    "what's wrong?": "មានរឿងអីមែនទេ?",
    "what's wrong with you?": "ឯងកើតអីហ្នឹង?",
    "are you out of your mind?": "ឯងឆ្កួតហើយមែនទេ?!",
    "have you lost your mind?": "ឯងវង្វេងស្មារតីហើយមែនទេ?!",
    "are you crazy?": "ឯងឆ្កួតមែនទេហ្នឹង?!",
    "are you kidding me?": "ឯងនិយាយលេងមែនទេហ្នឹង?!",
    "you've got to be kidding me": "មិនពិតទេដឹង! និយាយលេងមែនទេហ្នឹង!",
    "you must be joking": "ឯងនិយាយលេងសើចហើយមើលទៅ!",
    "it's none of your business": "មិនមែនរឿងរបស់ឯងទេ កុំចេះ!",
    "it is none of your business": "មិនមែនរឿងរបស់ឯងទេ កុំចេះ!",
    "none of your business": "មិនមែនរឿងឯងទេ!",
    "mind your own business": "គិតតែរឿងខ្លួនឯងទៅ!",
    "give me a break": "ឈប់រករឿងខ្ញុំទៅមើល៍!",
    "leave me alone": "ទុកឱ្យខ្ញុំនៅម្នាក់ឯងទៅ!",
    "get out of here": "ចេញពីទីនេះភ្លាមទៅ!",
    "get out!": "ចេញទៅ!",
    "shut up!": "បិទមាត់ភ្លាម!",
    "shut up": "ឈប់និយាយទៅ!",
    "hold on": "ចាំតិចសិន!",
    "hold on!": "ចាំមួយភ្លែតសិន!",
    "hang on": "ចាំតិចសិនណា៎!",
    "wait a minute": "ចាំមួយភ្លែតសិនមើល៍!",
    "wait a second": "ឈប់សិន! ចាំមួយភ្លែត!",
    "wait for me": "ចាំខ្ញុំផងមើល៍!",
    "watch out!": "ប្រយ័ត្ន!",
    "look out!": "ប្រយ័ត្នណា៎!",
    "heads up!": "ប្រយ័ត្នខាងមុខ!",
    "be careful": "ប្រយ័ត្នខ្លួនផងណា៎!",
    "be careful!": "ប្រយ័ត្នខ្លួនណា៎!",
    "let's go": "តោះយើងទៅ!",
    "let's go!": "តោះឆាប់ទៅ!",
    "come on": "តោះឆាប់ឡើង!",
    "come on!": "ឆាប់ឡើងមើល៍!",
    "hurry up": "ឱ្យលឿនឡើង!",
    "hurry up!": "ប្រញាប់ឡើងមើល៍!",
    "stop": "ឈប់សិន!",
    "stop!": "ឈប់ភ្លាម!",
    "stop it!": "ឈប់ធ្វើអ៊ីចឹងភ្លាម!",
    "don't move!": "កុំកម្រើកឱ្យសោះ!",
    "don't touch me!": "កុំប៉ះខ្ញុំឱ្យសោះ!",
    "let me go!": "លែងខ្ញុំទៅ!",
    "let him go!": "ដោះលែងគាត់ទៅ!",
    "help me!": "ជួយខ្ញុំផង!",
    "help me": "ជួយខ្ញុំផង!",
    "somebody help!": "ជួយផង! មានអ្នកណាឮទេ!",
    "i don't know": "ខ្ញុំអត់ដឹងទេ",
    "i have no idea": "ខ្ញុំអត់ដឹងសោះហ្មង",
    "i don't care": "ខ្ញុំអត់ខ្វល់ទេ!",
    "i don't think so": "ខ្ញុំគិតថាមិនអ៊ីចឹងទេ",
    "i told you so": "ខ្ញុំប្រាប់ឯងហើយតើ!",
    "i told you": "ខ្ញុំប្រាប់ហើយតើ!",
    "trust me": "ជឿខ្ញុំចុះណា៎!",
    "believe me": "ជឿពាក្យខ្ញុំទៅ!",
    "don't worry": "កុំបារម្ភអីណា៎",
    "don't worry about it": "កុំបារម្ភរឿងហ្នឹងអី",
    "it doesn't matter": "មិនអីទេ កុំគិតច្រើនអី",
    "never mind": "មិនអីទេ បំភ្លេចចោលទៅ",
    "forget it": "បំភ្លេចវាចោលទៅ!",
    "no way!": "មិនអាចសោះឡើយ!",
    "no way": "គ្មានផ្លូវដាច់ខាត!",
    "are you okay?": "ឯងអត់អីទេមែនទេ?",
    "are you alright?": "ឯងមិនអីទេហ្អ៎?",
    "i'm fine": "ខ្ញុំអត់អីទេ",
    "i'm okay": "ខ្ញុំមិនអីទេណា៎",
    "i got it": "ខ្ញុំយល់ហើយ!",
    "got it": "យល់ហើយ!",
    "got it?": "យល់អត់ហ្នឹង?",
    "do you understand?": "ឯងយល់អត់ហ្នឹង?",
    "you understand?": "យល់ទេហ្នឹង?",
    "what do you mean?": "ឯងចង់មានន័យថាម៉េចហ្នឹង?",
    "what do you want?": "តើឯងចង់បានអី?",
    "who are you?": "ឯងជាអ្នកណាគេហ្នឹង?",
    "where is he?": "តើគាត់នៅឯណាទៅ?",
    "where are they?": "ពួកគេនៅណាអស់ហើយ?",
    "follow me": "មកតាមខ្ញុំមក!",
    "stay here": "នៅចាំទីនេះសិនហើយណា៎!",
    "stay back!": "ថយក្រោយភ្លាម!",
    "calm down": "សម្រួលអារម្មណ៍សិនទៅ!",
    "take it easy": "មួយៗសិនទៅ កុំឆេវឆាវ!",
    "take care": "ថែរក្សាខ្លួនផងណា៎!",
    "good luck": "សូមឱ្យជោគជ័យណា៎!",
    "thank you": "អរគុណច្រើនណា៎",
    "thank you so much": "អរគុណខ្លាំងណាស់ណា៎!",
    "thanks a lot": "អរគុណច្រើនហើយ!",
    "i'm sorry": "ខ្ញុំសុំទោសណា៎",
    "i am so sorry": "ខ្ញុំសុំទោសពិតមែនណា៎",
    "excuse me": "សុំទោសផងណា៎",
    "of course": "ប្រាកដហើយតើ!",
    "absolutely": "ពិតប្រាកដណាស់!",
    "exactly": "ត្រូវហើយហ្នឹង!",
    "really?": "ពិតមែនហ្អ៎?",
    "seriously?": "និយាយមែនទែនហ្អ៎?",
    "is that true?": "រឿងហ្នឹងពិតមែនហ្អ៎?",
    "i love you": "បងស្រឡាញ់អូន",
    "i miss you": "ខ្ញុំនឹកឯងណាស់",
    "see you later": "ចាំជួបគ្នាពេលក្រោយណា៎",
    "long time no see": "ខានជួបគ្នាយូរហើយណា៎!",
    "hello": "សួស្ដី!",
    "hi": "សួស្ដី!",
    "hey": "ហេ៎!",
    "yes": "បាទ",
    "yeah": "មែនហើយ",
    "no": "អត់ទេ",
    "nope": "អត់សោះហ្មង",
}

# Chinese Short Drama / Film dialogue mappings to Natural Spoken Khmer
CHINESE_DRAMA_KHMER_MAP: Dict[str, str] = {
    "只要一根糖葫芦": "ទាមតែស្ករផ្លែឈើមួយដើម",
    "只要一根潮湖路": "ទាមតែស្ករផ្លែឈើមួយដើម",
    "糖葫芦": "ស្ករផ្លែឈើ",
    "可太邪乎了": "ពិតជាស័ក្តិសិទ្ធិខ្លាំងណាស់",
    "可太协会了": "ពិតជាស័ក្តិសិទ្ធិខ្លាំងណាស់",
    "两团黑气在你脖子上掐架了": "មានផ្សែងខ្មៅពីរដុំខាំគ្នានៅលើកឯង",
    "两团器中在你脖子上插架了": "មានផ្សែងខ្មៅពីរដុំខាំគ្នានៅលើកឯង",
    "我今日老觉得背几发梁 是何远过": "សព្វថ្ងៃខ្ញុំចេះតែមានអារម្មណ៍ថាត្រជាក់ឆ្អឹងខ្នង តើមកពីរឿងអីទៅ?",
    "我今日老觉得背脊发凉 是何缘故": "សព្វថ្ងៃខ្ញុំចេះតែមានអារម្មណ៍ថាត្រជាក់ឆ្អឹងខ្នង តើមកពីរឿងអីទៅ?",
    "背脊发凉": "ត្រជាក់ឆ្អឹងខ្នង",
    "背几发梁": "ត្រជាក់ឆ្អឹងខ្នង",
    "算命看相 不收铜板": "ទស្សន៍ទាយមើលជោគជតារាសី មិនយកលុយមួយកាក់មួយសេនទេ",
    "算命看象 不说同板": "ទស្សន៍ទាយមើលជោគជតារាសី មិនយកលុយមួយកាក់មួយសេនទេ",
    "想起来我家老祖芬在西南": "នឹកឃើញថាផ្នូរដូនតាខ្ញុំនៅទិសនិរតី",
    "想起来我家老祖坟在西南": "នឹកឃើញថាផ្នូរដូនតាខ្ញុំនៅទិសនិរតី",
    "大叔 这就去西南祖芬看朔一根": "ពូទៅមើលផ្នូរដូនតានៅទិសនិរតីឥឡូវនេះហើយ",
    "大叔 这就去西南祖坟看朔一根": "ពូទៅមើលផ្នូរដូនតានៅទិសនិរតីឥឡូវនេះហើយ",
    "这真是救了前目全家老小的命啊": "នេះពិតជាបានជួយសង្គ្រោះជីវិតគ្រួសារខ្ញុំទាំងមូលពិតមែន",
    "这真是救了钱某全家老小的命啊": "នេះពិតជាបានជួយសង្គ្រោះជីវិតគ្រួសារខ្ញុំទាំងមូលពិតមែន",
    "这点博营小神仙物必拿着买饭吃": "ប្រាក់បន្តិចបន្តួចនេះ ទេវតាតូចយកទៅទិញអីហូបចុះ",
    "这点薄银小神仙务必拿着买饭吃": "ប្រាក់បន្តិចបន្តួចនេះ ទេវតាតូចយកទៅទិញអីហូបចុះ",
    "银子 速宝能买一千只大白包子": "ប្រាក់នេះ ស៊ូប៉ាវអាចទិញនំប៉ាវសធំបានមួយពាន់ឯណោះ",
    "银子 苏宝能买一千只大白包子": "ប្រាក់នេះ ស៊ូប៉ាវអាចទិញនំប៉ាវសធំបានមួយពាន់ឯណោះ",
    "求你算一挂救救素宝吧": "សូមជួយទស្សន៍ទាយសង្គ្រោះស៊ូប៉ាវផង",
    "求你算一挂救救苏宝吧": "សូមជួយទស្សន៍ទាយសង្គ្រោះស៊ូប៉ាវផង",
    "嗯 素宝三天没吃上一口宝饭了": "ស៊ូប៉ាវអត់បានញ៉ាំបាយបីថ្ងៃហើយ",
    "嗯 苏宝三天没吃上一口饱饭了": "ស៊ូប៉ាវអត់បានញ៉ាំបាយបីថ្ងៃហើយ",
    "去去去 小小花子别挡到 反正": "ចេញទៅៗ កូនអ្នកសុំទាន កុំមកពាំងផ្លូវ",
    "小神仙": "ទេវតាតូច",
    "神仙": "លោកទេវតា",
    "祖坟": "ផ្នូរដូនតា",
    "老祖坟": "ផ្នូរដូនតា",
    "老祖芬": "ផ្នូរដូនតា",
    "双鬼征印": "ត្រាខ្មោចទ្វេ",
    "连夫人都不知晓": "សូម្បីតែលោកជំទាវក៏មិនដឹងផង",
}

# Short drama Chinese-Khmer domain glossary
CHINESE_SHORT_DRAMA_GLOSSARY: List[GlossaryItem] = [
    GlossaryItem(src_term="只要一根糖葫芦", km_term="ទាមតែស្ករផ្លែឈើមួយដើម"),
    GlossaryItem(src_term="糖葫芦", km_term="ស្ករផ្លែឈើ"),
    GlossaryItem(src_term="祖坟", km_term="ផ្នូរដូនតា"),
    GlossaryItem(src_term="老祖坟", km_term="ផ្នូរដូនតា"),
    GlossaryItem(src_term="小神仙", km_term="ទេវតាតូច"),
    GlossaryItem(src_term="苏宝", km_term="ស៊ូប៉ាវ"),
    GlossaryItem(src_term="宝守", km_term="ប៉ោសូវ"),
    GlossaryItem(src_term="算命", km_term="ទស្សន៍ទាយ"),
    GlossaryItem(src_term="看相", km_term="ក្បួនទាយលក្ខណៈ"),
    GlossaryItem(src_term="可太邪乎了", km_term="ពិតជាស័ក្តិសិទ្ធិខ្លាំងណាស់"),
    GlossaryItem(src_term="背脊发凉", km_term="ត្រជាក់ឆ្អឹងខ្នង"),
    GlossaryItem(src_term="两团黑气", km_term="ផ្សែងខ្មៅពីរដុំ"),
    GlossaryItem(src_term="掐架", km_term="ខាំគ្នា"),
    GlossaryItem(src_term="偏财", km_term="លាភសំណាង"),
    GlossaryItem(src_term="正财", km_term="ទ្រព្យធន"),
    GlossaryItem(src_term="大白包子", km_term="នំប៉ាវសធំ"),
    GlossaryItem(src_term="不收铜板", km_term="មិនយកលុយមួយកាក់មួយសេនទេ"),
]

# Strict Latin-to-Khmer character names & unwanted translation corrections
LATIN_AND_GLOSSARY_FIXES = [
    (r'\bSu\s*Bao\b', 'ស៊ូប៉ាវ'),
    (r'\bSubao\b', 'ស៊ូប៉ាវ'),
    (r'\bBao\s*shou\b', 'ប៉ោសូវ'),
    (r'\bBaoshou\b', 'ប៉ោសូវ'),
    (r'\bGang\s*Hong\b', 'គាំងហុង'),
    (r'\bGanghong\b', 'គាំងហុង'),
    (r'\bZu\s*fen\b', 'ផ្នូរដូនតា'),
    (r'\bZufen\b', 'ផ្នូរដូនតា'),
    (r'\bTang\s*hulu\b', 'ស្ករផ្លែឈើ'),
    (r'\bTanghulu\b', 'ស្ករផ្លែឈើ'),
    (r'\bChaohu\s*Road\b', 'ស្ករផ្លែឈើ'),
    (r'ផ្លូវ\s*Chaohu', 'ស្ករផ្លែឈើ'),
    (r'ទេពអប្សរតូច', 'ទេវតាតូច'),
    (r'ទេពអប្សរ', 'ទេវតា'),
    (r'សមាគមពេក', 'ពិតជាស័ក្តិសិទ្ធិខ្លាំងណាស់'),
    (r'ស្ករផ្លែឈើ,?\s*តែមួយ', 'ទាមតែស្ករផ្លែឈើមួយដើម'),
]


class TranslationEngine:
    def __init__(self, settings: Optional[ProjectSettings] = None):
        self.settings = settings or ProjectSettings()
        self._cached_gemini_model: Optional[str] = None
        self._http_client: Optional[httpx.Client] = None

    def _get_http_client(self, timeout: float = 12.0) -> httpx.Client:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(timeout=timeout)
        return self._http_client

    def apply_glossary(self, text: str, glossary: List[GlossaryItem]) -> str:
        """Replace defined glossary terms in text."""
        result = text
        for item in glossary:
            if not item.src_term or not item.km_term:
                continue
            pattern = re.compile(re.escape(item.src_term), re.IGNORECASE)
            result = pattern.sub(item.km_term, result)
        return result

    def _adapt_source_idioms_for_dubbing(self, src_text: str) -> str:
        """
        Pre-adapt English idioms and figurative expressions BEFORE sending to a web translator
        so Google Translate does not translate them word-for-word into nonsensical Khmer
        (e.g., preventing 'none of your business' -> 'មិនមែនជាអាជីវកម្មរបស់អ្នក').
        """
        text = src_text.strip()
        if not text:
            return text

        idiom_rewrites = [
            (r'\bwhat on earth\b', 'what'),
            (r'\bwhat in the world\b', 'what'),
            (r'\bout of your mind\b', 'unreasonable'),
            (r'\blost your mind\b', 'unreasonable'),
            (r'\bnone of your business\b', 'not your affair to worry about'),
            (r'\bmind your own business\b', 'worry about yourself'),
            (r'\bare you kidding me\b', 'are you joking with me'),
            (r'\bgot to be kidding me\b', 'must be joking'),
            (r'\bi have no idea\b', 'I do not know at all'),
            (r'\bno idea\b', 'do not know'),
            (r'\bmake sure\b', 'be certain'),
            (r'\bkeep in mind\b', 'remember'),
            (r'\bchanged my mind\b', 'decided differently'),
            (r'\bchange your mind\b', 'decide differently'),
            (r'\brunning out of time\b', 'almost out of time'),
            (r'\brun out of time\b', 'have no time left'),
            (r'\bfigure it out\b', 'find the answer'),
            (r'\bfigure out\b', 'understand'),
            (r'\bcount on me\b', 'trust me completely'),
            (r'\bcount on you\b', 'rely on you'),
            (r'\bget rid of\b', 'eliminate'),
            (r'\btake care of\b', 'handle and protect'),
            (r'\bas soon as possible\b', 'very quickly'),
            (r'\bright away\b', 'immediately'),
            (r'\bat once\b', 'immediately'),
            (r'\bby the way\b', 'also'),
            (r'\bdon\'t get me wrong\b', 'do not misunderstand me'),
            (r'\bgive me a break\b', 'stop bothering me'),
            (r'\bpiece of cake\b', 'very easy'),
            (r'\bso far so good\b', 'everything is good right now'),
            (r'\bin charge of\b', 'leading'),
            (r'\bon my way\b', 'coming right now'),
            (r'\bno matter what\b', 'under any circumstance'),
        ]

        adapted = text
        for pattern, repl in idiom_rewrites:
            adapted = re.sub(pattern, repl, adapted, flags=re.IGNORECASE)
        return adapted

    def humanize_khmer_dialogue(self, km_text: str, src_text: str = "", slot_duration: float = 0.0) -> str:
        """
        Transform literal word-for-word translations into natural, expressive movie dubbing dialogue
        that sounds like real humans talking to each other (សម្ដីនិយាយគ្នាធម្មជាតិ មិនមែនប្រែតាមពាក្យសៀវភៅ):
        1. Eliminates stiff translationese ('នូវ', 'ធ្វើការ...', 'បាន...', 'ត្រូវបាន...', 'របស់អ្នក').
        2. Converts stiff bookish vocabulary into natural spoken Khmer dialogue.
        3. Adds natural spoken sentence-ending particles ('ហ្នឹង', 'ណា៎', 'តើ', 'ហ្អ៎', 'មែនទេ') matching mood.
        4. Inserts subtle breath pauses so neural TTS inflects pitch up and down naturally.
        """
        text = km_text.strip()
        if not text:
            return ""

        src_clean = re.sub(r'\s+', ' ', src_text.strip().lower())
        if src_clean in MOVIE_DIALOGUE_MAP:
            return MOVIE_DIALOGUE_MAP[src_clean]
        src_no_punct = re.sub(r'[\.\!\?]+$', '', src_clean).strip()
        if src_no_punct in MOVIE_DIALOGUE_MAP and len(src_clean.split()) <= 6:
            mapped = MOVIE_DIALOGUE_MAP[src_no_punct]
            if src_clean.endswith("!") and not mapped.endswith("!"):
                mapped = re.sub(r'[\.\?\!។]+$', '', mapped) + "!"
            elif src_clean.endswith("?") and not mapped.endswith("?"):
                mapped = re.sub(r'[\.\?\!។]+$', '', mapped) + "?"
            return mapped

        # Phase 1: Whole-sentence & phrase-level conversational rewrites
        phrase_replacements = [
            # Full rigid questions -> Natural spoken Khmer questions
            (r'^តើអ្នកកំពុងធ្វើអ្វី(?:\s*|)(?:នៅទីនេះ|ទីនេះ|នៅហ្នឹង|)(?:\?|)$', 'ឯងកំពុងធ្វើអីហ្នឹង?'),
            (r'^តើអ្នកកំពុងតែធ្វើអ្វី(?:\?|)$', 'ឯងកំពុងធ្វើអីហ្នឹង?'),
            (r'^តើមានអ្វីកើតឡើង(?:\?|)$', 'មានរឿងអីហ្នឹង?'),
            (r'^តើមានរឿងអ្វីកើតឡើង(?:\?|)$', 'មានរឿងអីកើតឡើងហ្នឹង?'),
            (r'^តើអ្នកចង់មានន័យថាម៉េច(?:\?|)$', 'ឯងចង់មានន័យថាម៉េចហ្នឹង?'),
            (r'^តើអ្នកមានន័យថាម៉េច(?:\?|)$', 'ឯងចង់និយាយថាម៉េចហ្នឹង?'),
            (r'^តើអ្នកកំពុងទៅណា(?:\?|)$', 'ឯងគិតទៅណាហ្នឹង?'),
            (r'^តើអ្នកទៅណា(?:\?|)$', 'ឯងទៅណាហ្នឹង?'),
            (r'^តើអ្នកសុខសប្បាយជាទេថ្ងៃនេះ(?:\?|)$', 'ថ្ងៃនេះសុខសប្បាយជាទេហ្នឹង?'),
            (r'^សួស្ដី\s*តើអ្នកសុខសប្បាយជាទេថ្ងៃនេះ(?:\?|)$', 'សួស្ដី! ថ្ងៃនេះសុខសប្បាយជាទេហ្នឹង?'),
            (r'^តើអ្នកសុខសប្បាយទេ(?:\?|)$', 'សុខសប្បាយជាទេហ្នឹង?'),
            (r'^តើអ្នកដឹងទេ(?:\?|)$', 'ឯងដឹងអត់ហ្នឹង?'),
            (r'^តើអ្នកយល់ទេ(?:\?|)$', 'ឯងយល់អត់ហ្នឹង?'),
            (r'^តើអ្នកយល់ព្រមទេ(?:\?|)$', 'ឯងព្រមអត់ហ្នឹង?'),
            (r'^តើពិតមែនទេ(?:\?|)$', 'ពិតមែនហ្អ៎?'),
            (r'^មិនអាចទៅរួចទេ(?:\!|។|)$', 'មិនអាចសោះឡើយ!'),
            (r'^វាមិនអាចទៅរួចទេ(?:\!|។|)$', 'មិនអាចទៅរួចដាច់ខាត!'),
            (r'^សូមជួយខ្ញុំផង(?:\!|។|)$', 'ជួយខ្ញុំផង!'),
            (r'^កុំបារម្ភអំពីវា(?:\!|។|)$', 'កុំបារម្ភរឿងហ្នឹងអី'),
            (r'^ខ្ញុំមិនដឹងទេ(?:។|)$', 'ខ្ញុំអត់ដឹងសោះ'),
            (r'^ខ្ញុំមិនអាចជឿបានទេ(?:។|\!|)$', 'ខ្ញុំមិនជឿសោះហ្មង!'),
            (r'^ឆាប់ឡើងទៅ(?:\!|។|)$', 'តោះឆាប់ឡើង!'),

            # Eliminate literal translationese ('ធ្វើការ' + Verb, 'នូវ' object marker, stiff past tense 'បាន')
            (r'(?:វា|)មិនមែន(?:ជា|)អាជីវកម្មរបស់(?:អ្នក|ឯង)(?:នោះ|)(?:ទេ|)', 'មិនមែនរឿងរបស់ឯងទេ'),
            (r'ធ្វើការ(វាយប្រហារ|ស៊ើបអង្កេត|សម្រេចចិត្ត|ពិភាក្សា|ត្រួតពិនិត្យ|ផ្លាស់ប្ដូរ|ជ្រើសរើស|ការពារ|ស្វែងរក|ដោះស្រាយ|បញ្ជាក់|រៀបចំ|ចាប់ខ្លួន|តាមដាន)', r'\1'),
            (r'(មាន|ឃើញ|ធ្វើ|ទទួលបាន|ផ្តល់|បង្ហាញ|បង្កើត|រកឃើញ|បាត់បង់|និយាយ|ស្ដាប់)នូវ\s*', r'\1'),
            (r'ខ្ញុំបានប្រាប់(អ្នក|ឯង)ថា', 'ខ្ញុំប្រាប់ឯងហើយតើថា'),
            (r'ខ្ញុំបាន(ប្រាប់|ឃើញ|ឮ|ដឹង|ធ្វើ|ទៅ|មក|ជួប|សួរ|ទុក|យក)', r'ខ្ញុំ\1'),
            (r'អ្នកបាន(ប្រាប់|ឃើញ|ឮ|ដឹង|ធ្វើ|ទៅ|មក|ជួប|សួរ|និយាយ)', r'ឯង\1'),
            (r'មិនបាន(ប្រាប់|ឃើញ|ឮ|ដឹង|ធ្វើ|ទៅ|មក|ជួប|សួរ|គិត)', r'អត់បាន\1'),
            (r'គាត់បាន(ប្រាប់|ឃើញ|ឮ|ដឹង|ធ្វើ|ទៅ|មក|ជួប|សួរ|និយាយ)', r'គាត់\1'),
            (r'យើងបាន(ប្រាប់|ឃើញ|ឮ|ដឹង|ធ្វើ|ទៅ|មក|ជួប)', r'ពួកយើង\1'),
            (r'អ្នកនឹងត្រូវបានគេ(សម្លាប់|ចាប់ខ្លួន|រកឃើញ|បំផ្លាញ|លួច|វាយ)', r'ឯងច្បាស់ជាត្រូវគេ\1'),
            (r'នឹងត្រូវបានគេ(សម្លាប់|ចាប់ខ្លួន|រកឃើញ|បំផ្លាញ|លួច|វាយ)', r'ច្បាស់ជាត្រូវគេ\1'),
            (r'នឹងត្រូវបាន(សម្លាប់|ចាប់ខ្លួន|រកឃើញ)', r'ច្បាស់ជាត្រូវគេ\1'),
            (r'ត្រូវបានគេ(សម្លាប់|ចាប់ខ្លួន|រកឃើញ|បំផ្លាញ|លួច|វាយ)', r'ត្រូវគេ\1'),
            (r'ត្រូវបាន(សម្លាប់|ចាប់ខ្លួន|រកឃើញ)', r'ត្រូវគេ\1'),
            (r'អ្នកនឹង', 'ឯងនឹង'),

            # Possessive reductions (Spoken Khmer drops 'របស់' on close personal nouns)
            (r'ឪពុកម្ដាយរបស់ខ្ញុំ', 'ប៉ាម៉ាក់ខ្ញុំ'),
            (r'ឪពុករបស់ខ្ញុំ', 'ប៉ាខ្ញុំ'),
            (r'ម្ដាយរបស់ខ្ញុំ', 'ម៉ាក់ខ្ញុំ'),
            (r'ឪពុករបស់អ្នក', 'ប៉ាឯង'),
            (r'ម្ដាយរបស់អ្នក', 'ម៉ាក់ឯង'),
            (r'(ផ្ទះ|ឡាន|ដៃ|ជើង|ភ្នែក|ក្បាល|ចិត្ត|កូន|ប្រពន្ធ|ប្ដី|បង|ប្អូន|មិត្តភក្តិ|ឈ្មោះ|រឿង|ការងារ|លុយ|ទូរស័ព្ទ|ជីវិត|កំហុស|សម្ដី|បន្ទប់)របស់ខ្ញុំ', r'\1ខ្ញុំ'),
            (r'(ផ្ទះ|ឡាន|ដៃ|ជើង|ភ្នែក|ក្បាល|ចិត្ត|កូន|ប្រពន្ធ|ប្ដី|បង|ប្អូន|មិត្តភក្តិ|ឈ្មោះ|រឿង|ការងារ|លុយ|ទូរស័ព្ទ|ជីវិត|កំហុស|សម្ដី|បន្ទប់)របស់អ្នក', r'\1ឯង'),
            (r'(ផ្ទះ|ឡាន|ដៃ|ជើង|ភ្នែក|ក្បាល|ចិត្ត|កូន|ប្រពន្ធ|ប្ដី|បង|ប្អូន|មិត្តភក្តិ|ឈ្មោះ|រឿង|ការងារ|លុយ|ទូរស័ព្ទ|ជីវិត|កំហុស|សម្ដី|បន្ទប់)របស់គាត់', r'\1គាត់'),
            (r'របស់អ្នក', 'របស់ឯង'),

            # Pronoun & conversational verb refinements
            (r'តើអ្នកកំពុង', 'ឯងកំពុង'),
            (r'តើអ្នកនឹង', 'ឯងនឹង'),
            (r'តើអ្នកអាច', 'ឯងអាច'),
            (r'តើអ្នកចង់', 'ឯងចង់'),
            (r'តើអ្នកដឹង', 'ឯងដឹង'),
            (r'តើអ្នកមាន', 'ឯងមាន'),
            (r'តើអ្នកគិត', 'ឯងគិត'),
            (r'តើអ្នកបាន', 'ឯងបាន'),
            (r'តើអ្នក', 'ឯង'),
            (r'ប្រសិនបើអ្នក', 'បើឯង'),
            (r'នៅពេលដែលអ្នក', 'ពេលឯង'),
            (r'អ្នកត្រូវតែ', 'ឯងត្រូវតែ'),
            (r'អ្នកគួរតែ', 'ឯងគួរតែ'),
            (r'អ្នកមិនអាច', 'ឯងមិនអាច'),
            (r'អ្នកមិនគួរ', 'ឯងមិនគួរ'),
            (r'អ្នកមិនដឹង', 'ឯងអត់ដឹង'),
            (r'អ្នកកំពុងតែ', 'ឯងកំពុង'),
            (r'អ្នកកំពុង', 'ឯងកំពុង'),
            (r'ប្រាប់អ្នក', 'ប្រាប់ឯង'),
            (r'ជួយអ្នក', 'ជួយឯង'),
            (r'ជាមួយអ្នក', 'ជាមួយឯង'),
            (r'សម្រាប់អ្នក', 'សម្រាប់ឯង'),
            (r'មករកអ្នក', 'មករកឯង'),
            (r'សម្លាប់អ្នក', 'សម្លាប់ឯង'),
            (r'ស្រឡាញ់អ្នក', 'ស្រឡាញ់ឯង'),
            (r'រង់ចាំអ្នក', 'ចាំឯង'),

            # Stiff bookish connectors -> Natural spoken Khmer connectors
            (r'ហេតុអ្វីបានជាអ្នក', 'ម៉េចក៏ឯង'),
            (r'ហេតុអ្វីបានជា', 'ម៉េចក៏'),
            (r'តើហេតុអ្វី', 'ម៉េចក៏'),
            (r'ហេតុអ្វី', 'ម៉េចក៏'),
            (r'យ៉ាងដូចម្ដេច', 'យ៉ាងម៉េច'),
            (r'ដូចម្ដេច', 'ម៉េច'),
            (r'ប្រសិនបើ', 'បើ'),
            (r'ពីព្រោះតែ', 'មកពី'),
            (r'ដោយសារតែ', 'មកពី'),
            (r'ពីព្រោះ', 'ព្រោះ'),
            (r'ហេតុដូច្នេះហើយ', 'អ៊ីចឹងហើយ'),
            (r'ដូច្នេះហើយ', 'អ៊ីចឹងហើយ'),
            (r'ដូច្នេះ', 'អ៊ីចឹង'),
            (r'ប៉ុន្តែ', 'តែ'),
            (r'ទោះបីជាយ៉ាងណាក៏ដោយ', 'ទោះម៉េចក៏ដោយ'),
            (r'ទោះជាយ៉ាងណាក៏ដោយ', 'តែយ៉ាងណាក៏ដោយ'),
            (r'នៅពេលដែល', 'ពេលដែល'),
            (r'នាពេលឥឡូវនេះ', 'ឥឡូវហ្នឹង'),
            (r'នៅពេលនេះ', 'ពេលហ្នឹង'),
            (r'ឥឡូវនេះ', 'ឥឡូវហ្នឹង'),
            (r'នៅទីនេះ', 'នៅហ្នឹង'),
            (r'មកទីនេះ', 'មកនេះ'),
            (r'នៅទីនោះ', 'នៅនោះ'),
            (r'ទៅទីនោះ', 'ទៅនោះ'),
            (r'ជាមួយនឹង', 'ជាមួយ'),
            (r'នៅក្នុង', 'ក្នុង'),
            (r'នៅលើ', 'លើ'),
            (r'បន្ទាប់មកទៀត', 'រួចហើយ'),
            (r'បន្ទាប់មក', 'រួចហើយ'),

            # Natural spoken negations & question words
            (r'ខ្ញុំមិនដឹងនោះទេ', 'ខ្ញុំអត់ដឹងទេ'),
            (r'ខ្ញុំមិនដឹងទេ', 'ខ្ញុំអត់ដឹងទេ'),
            (r'ខ្ញុំមិនដឹងថា', 'ខ្ញុំអត់ដឹងថា'),
            (r'ខ្ញុំមិនមាន', 'ខ្ញុំអត់មាន'),
            (r'ខ្ញុំមិនចង់', 'ខ្ញុំអត់ចង់'),
            (r'ខ្ញុំមិនអាច', 'ខ្ញុំអត់អាច'),
            (r'ខ្ញុំមិនគិតថា', 'ខ្ញុំអត់គិតថា'),
            (r'វាមិនមែនជា', 'វាមិនមែន'),
            (r'មិនមែនទេ', 'អត់ទេ'),
            (r'មិនមានអ្វី', 'អត់មានអី'),
            (r'គ្មានអ្វី', 'អត់មានអី'),
            (r'ធ្វើអ្វី', 'ធ្វើអី'),
            (r'មានអ្វី', 'មានអី'),
            (r'រឿងអ្វី', 'រឿងអី'),
            (r'និយាយអ្វី', 'និយាយអី'),
            (r'ចង់បានអ្វី', 'ចង់បានអី'),
            (r'គិតអ្វី', 'គិតអី'),
            (r'មើលអ្វី', 'មើលអី'),
            (r'ខ្លាចអ្វី', 'ខ្លាចអី'),
            (r'អំពីវា', 'រឿងហ្នឹង'),
            (r'សម្រាប់វា', 'រឿងហ្នឹង'),
            (r'ផងដែរ(?=[\s\?\!\។]|$)', 'ដែរ'),
            (r'នោះទេ(?=[\s\?\!\។]|$)', 'ទេ'),
            (r'សូម(ប្រាប់|ជួយ|ស្ដាប់|មើល|ចាំ|ឈប់|កុំ)', r'\1'),
            (r'រង់ចាំបន្តិច', 'ចាំតិចសិន'),
            (r'រង់ចាំ', 'ចាំ'),
            (r'^តើ(?=\S)', ''),
        ]

        for pattern, repl in phrase_replacements:
            text = re.sub(pattern, repl, text).strip()

        # Phase 2: Add natural spoken cadence particles (ពាក្យភ្ជាប់សម្ដីនិយាយឱ្យដូចមនុស្សនិយាយគ្នា)
        src = src_text.strip()
        is_question = src.endswith("?") or text.endswith("?") or any(
            kw in text for kw in ["មែនទេ", "ឬទេ", "អីហ្នឹង", "ទៅណា", "នៅណា", "ម៉េចក៏", "យ៉ាងម៉េច", "អ្នកណាគេ"]
        )
        is_exclamation = src.endswith("!") or text.endswith("!") or any(
            kw in text for kw in ["ប្រយ័ត្ន", "ឈប់ភ្លាម", "ឆាប់ឡើង", "ជួយផង", "ចេញទៅ", "ដាច់ខាត", "ឱ្យសោះ"]
        )

        # Strip trailing punctuation temporarily to attach natural spoken tail particle cleanly
        core = re.sub(r'[\.\,\—\។\?\!]+$', '', text).strip()

        if is_question:
            # Make questions sound naturally inquisitive in spoken Khmer
            if core.endswith("ធ្វើអី") or core.endswith("មានអី") or core.endswith("រឿងអី") or core.endswith("ទៅណា") or core.endswith("នៅណា"):
                core += "ហ្នឹង"
            elif core.endswith("មែន") and not core.endswith("ពិតមែន"):
                core += "ទេហ្នឹង"
            elif core.endswith("ពិតមែន"):
                core += "ហ្អ៎"
            elif core.endswith("បាន") or core.endswith("ដឹង") or core.endswith("ឃើញ") or core.endswith("ឮ") or core.endswith("យល់"):
                if not any(core.endswith(p) for p in ["ទេ", "អត់", "ហ្នឹង", "ហ្អ៎", "ណា", "អី"]):
                    core += "អត់ហ្នឹង"
            elif core.endswith("ទេ") and len(core) < 35 and not core.endswith("មែនទេ"):
                core += "ហ្នឹង"
            text = core + "?"
        elif is_exclamation:
            # Make exclamations & commands punchy and urgent
            if any(core.startswith(w) for w in ["ប្រយ័ត្ន", "ចាំតិច", "ស្ដាប់ខ្ញុំ", "មើលនោះ", "កុំបារម្ភ"]) and not any(
                core.endswith(p) for p in ["ណា៎", "ផង", "ទៅ", "មើល៍", "សិន", "ឱ្យសោះ", "ឡើយ", "ហ្នឹង"]
            ):
                core += "ណា៎"
            elif any(core.startswith(w) for w in ["ឆាប់", "ប្រញាប់", "ចេញ", "រត់", "ទៅ"]) and not any(
                core.endswith(p) for p in ["ឡើង", "ទៅ", "ភ្លាម", "មើល៍", "ណា៎"]
            ):
                core += "ទៅ"
            text = core + "!"
        else:
            # Natural conversational statements: add gentle spoken warmth when appropriate
            if any(core.startswith(w) for w in ["កុំបារម្ភ", "មិនអីទេ", "ខ្ញុំសុំទោស", "អរគុណ", "ជឿខ្ញុំ"]) and not any(
                core.endswith(p) for p in ["ណា៎", "ទេ", "តើ", "ផង", "ហ្នឹង", "ហើយ"]
            ):
                core += "ណា៎"
            elif any(core.startswith(w) for w in ["ខ្ញុំប្រាប់", "ខ្ញុំថា"]) and core.endswith("ហើយ"):
                core += "តើ"
            text = core + "។"

        # Phase 3: Insert natural breath-group pauses before mid-sentence clause connectors
        # so the TTS voice actor pauses briefly and inflects pitch up/down between clauses
        if len(text) > 18:
            text = re.sub(r'(?<=\S)\s+(តែ|ព្រោះ|មកពី|បើ|អ៊ីចឹងហើយ|អ៊ីចឹង|ទោះម៉េចក៏ដោយ|រួចហើយ)\b', r', \1', text)

        # Clean duplicate spaces or punctuation
        text = re.sub(r',\s*,', ',', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Phase 4: If slot_duration is very tight (< 1.5s) and text is long, trim redundant filler words
        if 0.2 < slot_duration < 1.6 and len(text) > 28:
            for filler in ["ពិតប្រាកដ", "ទាំងអស់គ្នា", "នៅពេលនេះ", "ឥឡូវហ្នឹង"]:
                if len(text) > 24:
                    text = text.replace(filler, "").strip()

        return text

    def _post_process_translation(
        self,
        translated_text: str,
        src_text: str,
        target_lang: str,
        slot_duration: float = 0.0
    ) -> str:
        """Apply language-specific dialogue refinement, emotional punctuation, and syllable budgeting."""
        if target_lang == "km":
            from app.dubbing.transliteration import transliterate_khmer_dialogue
            # 1. Clean Latin character names and domain glossary corruptions
            cleaned_trans = transliterate_khmer_dialogue(translated_text)
            for pat, repl in LATIN_AND_GLOSSARY_FIXES:
                cleaned_trans = re.sub(pat, repl, cleaned_trans, flags=re.IGNORECASE)

            humanized = self.humanize_khmer_dialogue(cleaned_trans, src_text, slot_duration=slot_duration)

            # Re-apply Latin transliteration & glossary post-check after humanization
            humanized = transliterate_khmer_dialogue(humanized)
            for pat, repl in LATIN_AND_GLOSSARY_FIXES:
                humanized = re.sub(pat, repl, humanized, flags=re.IGNORECASE)

            # Enforce strict speech-rate budget if slot duration is provided
            if slot_duration > 0.2:
                from app.dubbing.speech_rate import khmer_syllable_count, get_speech_budget
                _, max_syll = get_speech_budget(slot_duration)
                if khmer_syllable_count(humanized) > max_syll:
                    humanized = self.condense_khmer_dialogue(
                        humanized, max_syllables=max_syll, src_text=src_text, slot_sec=slot_duration
                    )
            return humanized
            return humanized

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
        target_lang: Optional[str] = None,
        slot_duration: float = 0.0
    ) -> str:
        """
        Translate a single speech segment into natural conversational target language,
        using surrounding dialogue context and idiomatic adaptation.
        """
        src_text = src_text.strip()
        if not src_text:
            return ""

        t_lang = target_lang or getattr(self.settings, "target_language", "km") or "km"
        glossary = glossary or []

        # Check direct Chinese short drama line mappings first
        if t_lang == "km":
            src_clean = re.sub(r'[\s\.\,\!\?\"\'\(\)\—\-\:;។៕ៗ]+', '', src_text)
            for zh_key, km_val in CHINESE_DRAMA_KHMER_MAP.items():
                zh_clean = re.sub(r'[\s\.\,\!\?\"\'\(\)\—\-\:;]+', '', zh_key)
                if src_clean == zh_clean:
                    return self._post_process_translation(km_val, src_text, t_lang, slot_duration=slot_duration)
                if len(zh_clean) >= 4 and (src_clean in zh_clean or zh_clean in src_clean) and abs(len(src_clean) - len(zh_clean)) <= 4:
                    return self._post_process_translation(km_val, src_text, t_lang, slot_duration=slot_duration)

        provider = self.settings.translation_provider.lower()

        # 1. Try Gemini API
        if provider == "gemini" and self.settings.gemini_api_key:
            try:
                res = self._translate_gemini(
                    src_text, glossary, target_lang=t_lang,
                    context_before=context_before, context_after=context_after,
                    slot_duration=slot_duration
                )
                return self._post_process_translation(res, src_text, t_lang, slot_duration=slot_duration)
            except Exception as e:
                print(f"[Translation] Gemini failed: {e}")

        # 2. Try Claude API
        if provider == "claude" and self.settings.claude_api_key:
            try:
                res = self._translate_claude(
                    src_text, glossary, target_lang=t_lang,
                    context_before=context_before, context_after=context_after,
                    slot_duration=slot_duration
                )
                return self._post_process_translation(res, src_text, t_lang, slot_duration=slot_duration)
            except Exception as e:
                print(f"[Translation] Claude failed: {e}")

        # 3. Try OpenAI API
        from app.secrets import get_openai_api_key
        oa_key = (self.settings.openai_api_key or "").strip() or get_openai_api_key()
        if provider == "openai" and oa_key:
            try:
                res = self._translate_openai(
                    src_text, glossary, target_lang=t_lang,
                    context_before=context_before, context_after=context_after,
                    slot_duration=slot_duration
                )
                return self._post_process_translation(res, src_text, t_lang, slot_duration=slot_duration)
            except Exception as e:
                print(f"[Translation] OpenAI failed: {e}")

        # 4. Fallback: Web / Smart Conversational Heuristic Translation
        res = self._translate_web_or_heuristic(src_text, glossary, target_lang=t_lang)
        return self._post_process_translation(res, src_text, t_lang, slot_duration=slot_duration)

    def translate_segments(
        self,
        segments: List[Segment],
        glossary: Optional[List[GlossaryItem]] = None,
        target_lang: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        usage_cb: Optional[Callable[[object], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        skip_imported: bool = False,
    ) -> List[Segment]:
        """Batch translate a list of segments in-place with surrounding conversation context and ASR repair."""
        t_lang = target_lang or getattr(self.settings, "target_language", "km") or "km"

        # Step 1: Repair ASR homophones and normalize Chinese text
        from app.dubbing.asr_repair import ASRRepairer
        repairer = ASRRepairer(self.settings)
        segments = repairer.repair_segments(segments)

        # Merge domain short drama glossary
        active_glossary = list(glossary or [])
        glossary_terms = {g.src_term for g in active_glossary}
        for item in CHINESE_SHORT_DRAMA_GLOSSARY:
            if item.src_term not in glossary_terms:
                active_glossary.append(item)

        provider = (getattr(self.settings, "translation_provider", "google") or "google").lower()
        if provider == "openai":
            from app.openai_client import OpenAIClient
            client = OpenAIClient(self.settings)
            lang_meta = TARGET_LANG_MAP.get(t_lang) or TARGET_LANG_MAP["km"]

            def _google_fallback(seg: Segment) -> str:
                raw = self._translate_web_or_heuristic(seg.text_src, active_glossary, target_lang=t_lang)
                return self._post_process_translation(raw, seg.text_src, t_lang, slot_duration=seg.slot)

            client.translate_segments_batch(
                segments=segments,
                glossary=active_glossary,
                target_lang_name=lang_meta["prompt_name"],
                batch_size=30,
                skip_imported=skip_imported,
                fallback_fn=_google_fallback,
                progress_cb=progress_cb,
                usage_cb=usage_cb,
                cancel_cb=cancel_cb,
            )
            for seg in segments:
                if seg.text_km and not (skip_imported and getattr(seg, "translation_source", "") == "imported"):
                    seg.text_km = self._post_process_translation(
                        seg.text_km, seg.text_src, t_lang, slot_duration=seg.slot
                    )
            return segments

        total = len(segments)
        if provider == "gemini" and (getattr(self.settings, "gemini_api_key", "") or "").strip():
            self._translate_segments_gemini_batch(
                segments, active_glossary, t_lang,
                progress_cb=progress_cb, cancel_cb=cancel_cb, skip_imported=skip_imported,
            )
            return segments

        if provider in ("google", "gemini"):
            # Fast concurrent connection-pooled Google Translate (also used when Gemini key is empty)
            self._translate_segments_google_concurrent(
                segments, active_glossary, t_lang,
                progress_cb=progress_cb, cancel_cb=cancel_cb, skip_imported=skip_imported,
            )
            return segments

        for i, seg in enumerate(segments):
            if cancel_cb and cancel_cb():
                break
            if skip_imported and getattr(seg, "translation_source", "") == "imported":
                continue
            if seg.text_src and seg.text_src.strip():
                if progress_cb:
                    pct = int((i / max(1, total)) * 100)
                    progress_cb(pct, f"កំពុងបកប្រែវគ្គ {i + 1}/{total}...")
                prev_txt = segments[i - 1].text_src if i > 0 else ""
                next_txt = segments[i + 1].text_src if i + 1 < total else ""
                seg.text_km = self.translate_segment(
                    seg.text_src,
                    glossary=active_glossary,
                    context_before=prev_txt,
                    context_after=next_txt,
                    target_lang=t_lang,
                    slot_duration=seg.slot
                )
        return segments

    def _direct_khmer_match(self, src_text: str, target_lang: str, slot_duration: float = 0.0) -> Optional[str]:
        if target_lang != "km":
            return None
        src_clean = re.sub(r'[\s\.\,\!\?\"\'\(\)\—\-\:;។៕ៗ]+', '', src_text)
        for zh_key, km_val in CHINESE_DRAMA_KHMER_MAP.items():
            zh_clean = re.sub(r'[\s\.\,\!\?\"\'\(\)\—\-\:;]+', '', zh_key)
            if src_clean == zh_clean:
                return self._post_process_translation(km_val, src_text, target_lang, slot_duration=slot_duration)
            if len(zh_clean) >= 4 and (src_clean in zh_clean or zh_clean in src_clean) and abs(len(src_clean) - len(zh_clean)) <= 4:
                return self._post_process_translation(km_val, src_text, target_lang, slot_duration=slot_duration)
        return None

    def _translate_segments_gemini_batch(
        self,
        segments: List[Segment],
        glossary: List[GlossaryItem],
        target_lang: str,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        skip_imported: bool = False,
        batch_size: int = 25,
    ) -> None:
        """
        Translate segments via Gemini in structured JSON batches (~25 lines per request)
        using cached working model & persistent HTTP connection pool (10-30x faster).
        """
        import json as _json
        from app.openai_client import validate_and_extract_batch_json

        lang_meta = TARGET_LANG_MAP.get(target_lang) or TARGET_LANG_MAP["km"]
        lang_name = lang_meta["prompt_name"]
        glossary_txt = "\n".join(f"- '{g.src_term}' -> '{g.km_term}'" for g in glossary if g.src_term and g.km_term)

        pending_indices: List[int] = []
        for idx, seg in enumerate(segments):
            if skip_imported and getattr(seg, "translation_source", "") == "imported":
                continue
            src = (seg.text_src or "").strip()
            if not src:
                continue
            direct = self._direct_khmer_match(src, target_lang, seg.slot)
            if direct is not None:
                seg.text_km = direct
            else:
                pending_indices.append(idx)

        total_p = len(pending_indices)
        if total_p == 0:
            if progress_cb:
                progress_cb(100, "បកប្រែរួចរាល់!")
            return

        fallback_indices: List[int] = []
        done = 0
        for start in range(0, total_p, batch_size):
            if cancel_cb and cancel_cb():
                break
            chunk_idxs = pending_indices[start:start + batch_size]
            if progress_cb:
                pct = int((done / max(1, total_p)) * 90)
                progress_cb(pct, f"កំពុងបកប្រែ Batch Gemini ({start + 1}–{min(total_p, start + len(chunk_idxs))}/{total_p})...")

            prev_ctx = [segments[p].text_src for p in range(max(0, chunk_idxs[0] - 3), chunk_idxs[0]) if segments[p].text_src]
            payload = [
                {"index": idx, "slot_sec": round(segments[idx].slot, 2), "text": segments[idx].text_src.strip()}
                for idx in chunk_idxs
            ]
            ctx_str = ("Previous lines (context only):\n" + "\n".join(f"- {c}" for c in prev_ctx) + "\n\n") if prev_ctx else ""
            prompt = (
                f"You are a master {lang_name} movie dubbing translator. Translate dialogue into natural, punchy spoken {lang_name} "
                f"(never literal word-for-word; keep sentences concise to fit each slot_sec).\n"
                + (f"Glossary:\n{glossary_txt}\n" if glossary_txt else "")
                + f"Return ONLY valid JSON with shape {{\"translations\": {{\"<index>\": \"<text>\"}}}} matching EVERY index.\n"
                + ctx_str
                + "Lines:\n"
                + _json.dumps(payload, ensure_ascii=False)
            )

            try:
                raw_out = self._call_gemini_api(prompt, timeout=18.0, response_mime_type="application/json", raw_json=True)
                matched, missing = validate_and_extract_batch_json(raw_out, chunk_idxs)
                for m_idx, m_txt in matched.items():
                    segments[m_idx].text_km = self._post_process_translation(
                        m_txt, segments[m_idx].text_src, target_lang, slot_duration=segments[m_idx].slot
                    )
                fallback_indices.extend(missing)
            except Exception as e:
                print(f"[Translation] Gemini batch fallback note: {e}")
                fallback_indices.extend(chunk_idxs)
            done += len(chunk_idxs)

        if fallback_indices and not (cancel_cb and cancel_cb()):
            if progress_cb:
                progress_cb(90, f"កំពុងបំពេញ {len(fallback_indices)} ជួរដែលនៅសល់...")
            self._translate_indices_google_concurrent(
                segments, fallback_indices, glossary, target_lang, progress_cb=None, cancel_cb=cancel_cb
            )

        if progress_cb:
            progress_cb(100, "ការបកប្រែរួចរាល់!")

    def _translate_segments_google_concurrent(
        self,
        segments: List[Segment],
        glossary: List[GlossaryItem],
        target_lang: str,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        skip_imported: bool = False,
    ) -> None:
        pending_indices: List[int] = []
        for idx, seg in enumerate(segments):
            if skip_imported and getattr(seg, "translation_source", "") == "imported":
                continue
            src = (seg.text_src or "").strip()
            if not src:
                continue
            direct = self._direct_khmer_match(src, target_lang, seg.slot)
            if direct is not None:
                seg.text_km = direct
            else:
                pending_indices.append(idx)

        self._translate_indices_google_concurrent(
            segments, pending_indices, glossary, target_lang, progress_cb=progress_cb, cancel_cb=cancel_cb
        )

    def _translate_indices_google_concurrent(
        self,
        segments: List[Segment],
        indices: List[int],
        glossary: List[GlossaryItem],
        target_lang: str,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(indices)
        if total == 0:
            if progress_cb:
                progress_cb(100, "បកប្រែរួចរាល់!")
            return

        done_cnt = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {}
            for idx in indices:
                if cancel_cb and cancel_cb():
                    break
                futs[pool.submit(self._translate_web_or_heuristic, segments[idx].text_src.strip(), glossary, target_lang)] = idx

            for fut in as_completed(futs):
                idx = futs[fut]
                try:
                    raw = fut.result()
                except Exception:
                    raw = self.apply_glossary(segments[idx].text_src, glossary)
                segments[idx].text_km = self._post_process_translation(
                    raw, segments[idx].text_src, target_lang, slot_duration=segments[idx].slot
                )
                done_cnt += 1
                if progress_cb and (done_cnt % 3 == 0 or done_cnt == total):
                    pct = int((done_cnt / max(1, total)) * 100)
                    progress_cb(pct, f"កំពុងបកប្រែ {done_cnt}/{total}...")

    def _build_prompt(
        self,
        src_text: str,
        glossary: List[GlossaryItem],
        shorten: bool = False,
        target_lang: str = "km",
        context_before: str = "",
        context_after: str = "",
        slot_duration: float = 0.0
    ) -> str:
        lang_meta = TARGET_LANG_MAP.get(target_lang) or TARGET_LANG_MAP["km"]
        lang_name = lang_meta["prompt_name"]

        glossary_rules = ""
        if glossary:
            glossary_rules = "Use the following specific glossary terms:\n" + "\n".join(
                [f"- '{g.src_term}' -> '{g.km_term}'" for g in glossary]
            )

        context_block = ""
        if context_before or context_after:
            context_block = (
                f"Surrounding conversation context (for tone & pronoun continuity only—do NOT translate these):\n"
                f"- Previous line: \"{context_before or '(start of scene)'}\"\n"
                f"- Next line: \"{context_after or '(end of scene)'}\"\n"
            )

        timing_hint = ""
        if slot_duration > 0:
            if target_lang == "km":
                from app.dubbing.speech_rate import get_speech_budget
                target_syll, max_syll = get_speech_budget(slot_duration)
                timing_hint = (
                    f"CRITICAL DURATION & SPEECH BUDGET CONSTRAINT:\n"
                    f"- Character speaking slot: {slot_duration:.2f} seconds.\n"
                    f"- Max syllables allowed: {max_syll} syllables (target ~{target_syll} syllables at 5.5 syllables/sec).\n"
                    f"- The Khmer translation MUST NOT EXCEED {max_syll} syllables!\n"
                    f"- Be punchy, natural, and brief. Never write long descriptions, formal phrasing, or wordy book translations.\n"
                )
            else:
                timing_hint = f"Speaking time window: {slot_duration:.1f} seconds (match the pacing and brevity of this window).\n"

        if target_lang == "km":
            task_instruction = (
                "Translate this dialogue into concise, natural spoken Khmer (movie dubbing style). "
                "NEVER translate word-for-word. Rewrite it into short, punchy everyday spoken Khmer with emotional particles."
                if shorten else
                "You are a master Khmer movie dubbing director. Translate the dialogue by MEANING and EMOTION, NEVER word-for-word! "
                "Write it exactly how real Khmer people talk to each other in daily life and cinema (សម្ដីមនុស្សនិយាយគ្នាពិតៗ ដាច់ខាតមិនប្រែតាមពាក្យសៀវភៅ). "
                "Rules:\n"
                "1. Avoid stiff literal words like 'នូវ', 'ធ្វើការ...', 'របស់អ្នក', 'ខ្ញុំបាន...', 'ប្រសិនបើ', 'ហេតុដូច្នេះ'.\n"
                "2. Use natural spoken pronouns and particles ('ឯង', 'ខ្ញុំ', 'ប៉ាខ្ញុំ', 'ហ្នឹង?', 'ណា៎!', 'តើ!', 'ហ្អ៎?', 'អត់ទេ', 'ម៉េចក៏').\n"
                "3. Place commas (,) at natural breath pauses in longer sentences so the voice actor inflects pitch up and down.\n"
                "4. Keep question marks (?) and exclamation marks (!) to drive vocal pitch intonation.\n"
                "5. CRITICAL: NEVER leave character names or terms in English/Latin letters (e.g., Gang Hong, Baoshou, Su Bao). You MUST write them directly in native Khmer script (គាំងហុង, ប៉ោសូវ, ស៊ូប៉ាវ)!"
            )
        else:
            task_instruction = (
                f"Translate this dialogue into concise, natural spoken {lang_name} (movie dubbing style). "
                f"NEVER translate literally word-for-word; make it punchy so the voice actor fits the character's mouth timing."
                if shorten else
                f"You are a top-tier movie dubbing director translating film dialogue into natural, expressive spoken {lang_name}. "
                f"Translate by conversational meaning and emotion, never word-for-word. Retain (?) and (!) for vocal intonation."
            )

        return (
            f"You are a professional movie dubbing translator into {lang_name}.\n"
            f"{task_instruction}\n"
            f"{timing_hint}"
            f"{context_block}"
            f"{glossary_rules}\n"
            f"Only return the final {lang_name} spoken dialogue line without quotation marks or explanation.\n\n"
            f"Current line to dub: \"{src_text}\""
        )

    def _call_gemini_api(
        self,
        prompt: str,
        timeout: float = 12.0,
        response_mime_type: Optional[str] = None,
        raw_json: bool = False,
    ) -> str:
        """
        Call Google Gemini REST API using persistent httpx connection pool & cached working model
        so dead/404 model names are never retried repeatedly across lines.
        """
        base_candidates = [
            "gemini-flash-lite-latest",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
        ]
        if self._cached_gemini_model and self._cached_gemini_model in base_candidates:
            candidate_models = [self._cached_gemini_model] + [m for m in base_candidates if m != self._cached_gemini_model]
        elif self._cached_gemini_model:
            candidate_models = [self._cached_gemini_model] + base_candidates
        else:
            candidate_models = base_candidates

        api_key = (self.settings.gemini_api_key or "").strip()
        if not api_key:
            raise ValueError("Gemini API key is empty")

        gen_cfg: dict = {
            "temperature": 0.25,
            "maxOutputTokens": 2048 if raw_json else 350,
        }
        if response_mime_type:
            gen_cfg["responseMimeType"] = response_mime_type

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }

        last_err = ""
        client = self._get_http_client(timeout=timeout)
        for model_name in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            try:
                resp = client.post(url, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    self._cached_gemini_model = model_name
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            text = parts[0].get("text", "").strip()
                            if raw_json:
                                return text
                            clean = re.sub(r'^[\*\s>«"\'“]+|[\*\s>»"\'”]+$', '', text).strip()
                            lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
                            if lines:
                                clean = re.sub(r'^[>\-\*\d\.\s]+', '', lines[0]).strip().strip('"').strip("'")
                            return clean
                elif resp.status_code in (401, 403):
                    raise RuntimeError(f"Invalid Gemini API key (HTTP {resp.status_code})")
                elif resp.status_code == 429:
                    last_err = f"HTTP 429 ({model_name})"
                    break
                elif resp.status_code in (400, 404, 503):
                    last_err = f"HTTP {resp.status_code} ({model_name})"
                    continue
                else:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
            except RuntimeError:
                raise
            except Exception as e:
                last_err = f"{model_name}: {str(e)}"
                continue

        raise RuntimeError(f"All Gemini models failed ({last_err})")

    def _translate_gemini(
        self,
        src_text: str,
        glossary: List[GlossaryItem],
        shorten: bool = False,
        target_lang: str = "km",
        context_before: str = "",
        context_after: str = "",
        slot_duration: float = 0.0
    ) -> str:
        prompt = self._build_prompt(
            src_text, glossary, shorten=shorten, target_lang=target_lang,
            context_before=context_before, context_after=context_after,
            slot_duration=slot_duration
        )
        return self._call_gemini_api(prompt)

    def _translate_claude(
        self,
        src_text: str,
        glossary: List[GlossaryItem],
        shorten: bool = False,
        target_lang: str = "km",
        context_before: str = "",
        context_after: str = "",
        slot_duration: float = 0.0
    ) -> str:
        prompt = self._build_prompt(
            src_text, glossary, shorten=shorten, target_lang=target_lang,
            context_before=context_before, context_after=context_after,
            slot_duration=slot_duration
        )
        headers = {
            "x-api-key": self.settings.claude_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        body = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 180,
            "messages": [{"role": "user", "content": prompt}]
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
            data = resp.json()
            return data["content"][0]["text"].strip().strip('"').strip("'")

    def _translate_openai(
        self,
        src_text: str,
        glossary: List[GlossaryItem],
        shorten: bool = False,
        target_lang: str = "km",
        context_before: str = "",
        context_after: str = "",
        slot_duration: float = 0.0
    ) -> str:
        lang_meta = TARGET_LANG_MAP.get(target_lang) or TARGET_LANG_MAP["km"]
        lang_name = lang_meta["prompt_name"]
        prompt = self._build_prompt(
            src_text, glossary, shorten=shorten, target_lang=target_lang,
            context_before=context_before, context_after=context_after,
            slot_duration=slot_duration
        )
        from app.secrets import get_openai_api_key
        from app.openai_client import DEFAULT_OPENAI_BASE_URL
        oa_key = (self.settings.openai_api_key or "").strip() or get_openai_api_key()
        base_url = (getattr(self.settings, "openai_base_url", "") or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
        oa_model = (getattr(self.settings, "openai_trans_model", "gpt-4o-mini") or "gpt-4o-mini").strip()
        headers = {
            "Authorization": f"Bearer {oa_key}",
            "Content-Type": "application/json"
        }
        body = {
            "model": oa_model,
            "messages": [
                {"role": "system", "content": f"You are a professional movie dubbing dialogue writer into {lang_name}."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.4
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip().strip('"').strip("'")

    def _translate_web_or_heuristic(self, src_text: str, glossary: List[GlossaryItem], target_lang: str = "km") -> str:
        """
        Resilient conversational translator:
        1. Checks direct movie dialogue idiom bank for instant natural Khmer dubbing lines.
        2. Pre-adapts English idioms so web translators translate the true meaning rather than literal words.
        3. Queries Google translation endpoints and applies glossary terms.
        """
        if target_lang == "km":
            cleaned = re.sub(r'\s+', ' ', src_text.strip().lower())
            if cleaned in MOVIE_DIALOGUE_MAP:
                return self.apply_glossary(MOVIE_DIALOGUE_MAP[cleaned], glossary)
            cleaned_no_punct = re.sub(r'[\.\!\?]+$', '', cleaned).strip()
            if cleaned_no_punct in MOVIE_DIALOGUE_MAP and len(cleaned.split()) <= 6:
                res = MOVIE_DIALOGUE_MAP[cleaned_no_punct]
                return self.apply_glossary(res, glossary)

        import urllib.request
        import urllib.parse
        import json

        # Pre-adapt English idioms so web translation translates meaning instead of literal words
        query_text = self._adapt_source_idioms_for_dubbing(src_text) if target_lang == "km" else src_text
        encoded = urllib.parse.quote(query_text)
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
                        if isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], str):
                            km = data[0][0].strip()
                            if km:
                                return self.apply_glossary(km, glossary)
                        elif isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], list):
                            km = "".join([part[0] for part in data[0] if isinstance(part, list) and part[0]]).strip()
                            if km:
                                return self.apply_glossary(km, glossary)
                        elif isinstance(data[0], str):
                            km = data[0].strip()
                            if km:
                                return self.apply_glossary(km, glossary)
            except Exception:
                continue

        return self.apply_glossary(src_text, glossary)

    def _heuristic_condense_khmer(self, text: str, max_syllables: int, src_text: str = "") -> str:
        """
        Intelligently condense Khmer dialogue using linguistic pruning of fillers,
        formal affixes, and long clauses while keeping the core colloquial meaning.
        Uses boundary-aware regex to prevent slicing valid Khmer words.
        """
        from app.dubbing.speech_rate import khmer_syllable_count, extract_khmer_syllables
        clean = text.strip()
        if khmer_syllable_count(clean) <= max_syllables:
            return clean

        # Direct idiomatic spoken adaptations for common film dialogue
        src_clean = re.sub(r'\s+', '', src_text.strip().lower())
        if ("睡迷" in src_clean or "迷护" in src_clean) and max_syllables <= 8:
            cand = "ងងុយដេកណាស់"
            if khmer_syllable_count(cand) <= max_syllables:
                return cand
        if "掉个头" in src_clean and max_syllables <= 6:
            cand = "បង្វែរក្បាលគាត់"
            if khmer_syllable_count(cand) <= max_syllables:
                return cand
        if "顶着你" in src_clean and max_syllables <= 8:
            cand = "ខ្លាចគេមិនព្រម"
            if khmer_syllable_count(cand) <= max_syllables:
                return cand

        # 1. Multi-clause check: check all parts
        parts = re.split(r'[។,!?\n]+', clean)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            valid_parts = [p for p in parts if 2 <= khmer_syllable_count(p) <= max_syllables]
            if valid_parts:
                best = max(valid_parts, key=lambda p: khmer_syllable_count(p))
                punct = "!" if clean.endswith("!") else ("?" if clean.endswith("?") else "។")
                return best + punct

        # 2. Prune verbose formal markers and fillers with strict Khmer grapheme boundary awareness
        fillers = [
            "នៅក្នុង", "ចូលទៅក្នុង", "ជាការ", "ក្នុងការ", "ត្រូវបាន", "នៅពេលនេះ", "ឥឡូវហ្នឹង",
            "ទាំងអស់គ្នា", "ពិតប្រាកដ", "ដោយហិង្សា", "ជាដើម", "ប្រសិនបើ", "ម្តងទៀត",
            "របស់អ្នក", "របស់ខ្ញុំ", "របស់គាត់", "របស់នាង", "របស់ឯង",
            "ដើម្បី", "ដែលជា", "ធ្វើការ", "ផងដែរ", "សូម", "បាទ", "ចាស", "តើ",
            "ខ្ញុំបាន", "គាត់បាន", "យើងបាន", "ឯងបាន", "បាន",
            "ខ្ញុំកំពុង", "កំពុងតែ", "កំពុង",
            "ជុំវិញ", "ជាមួយឯង", "ថ្ងៃនេះ", "នោះទេ", "មែនទេ",
            "ផងណា៎", "ណា៎", "តើ!", "ហ្នឹង", "ហ្អ៎"
        ]
        cur = clean
        for f in fillers:
            # Boundary-aware: only replace if NOT preceded or followed by Khmer characters \u1780-\u17d3
            pattern = rf'(?<![\u1780-\u17d3]){re.escape(f)}(?![\u1780-\u17d3])'
            if re.search(pattern, cur):
                temp = re.sub(pattern, '', cur).strip()
                temp = re.sub(r'\s+', ' ', temp)
                # Guarantee no broken leading or trailing combining marks / coengs
                temp = re.sub(r'^[\u17b6-\u17d3\s]+', '', temp)
                temp = re.sub(r'[\u17d2\s]+$', '', temp)
                if len(temp) >= 2:
                    cur = temp
                    if khmer_syllable_count(cur) <= max_syllables:
                        return cur

        # If multi-clause and still too long, take the shortest part
        if len(parts) >= 2:
            cur = min(parts, key=lambda p: khmer_syllable_count(p))
            if khmer_syllable_count(cur) <= max_syllables:
                punct = "!" if clean.endswith("!") else ("?" if clean.endswith("?") else "។")
                return cur + punct

        # 3. Prune starting pronouns if still too long
        for pro in ["ខ្ញុំ", "គាត់", "យើង", "ពួកយើង", "ឯង"]:
            pat_pro = rf'^{re.escape(pro)}(?![\u1780-\u17d3])\s*'
            if re.search(pat_pro, cur) and len(cur) > len(pro) + 2:
                temp = re.sub(pat_pro, '', cur).strip()
                temp = re.sub(r'^[\u17b6-\u17d3\s]+', '', temp)
                if khmer_syllable_count(temp) <= max_syllables:
                    return temp

        # 4. Syllable-level token truncation fallback (guarantees <= max_syllables without broken graphemes)
        sylls = extract_khmer_syllables(cur)
        if len(sylls) > max_syllables:
            truncated_sylls = sylls[:max_syllables]
            res_str = "".join(truncated_sylls)
            # Guarantee no stray leading or trailing Coeng
            res_str = re.sub(r'^[\u17b6-\u17d3\s]+', '', res_str)
            res_str = re.sub(r'[\u17d2\s]+$', '', res_str)
            punct = "!" if clean.endswith("!") else ("?" if clean.endswith("?") else "។")
            return res_str + punct

        return cur

    def condense_khmer_dialogue(
        self,
        text: str,
        max_syllables: int,
        src_text: str = "",
        slot_sec: float = 0.0,
        max_attempts: int = 3
    ) -> str:
        """
        Speech-rate budget: Condense Khmer text to strictly fit within max_syllables.
        Loops up to max_attempts times, verifying syllable count on each pass.
        """
        from app.dubbing.speech_rate import khmer_syllable_count
        cur_text = text
        for attempt in range(1, max_attempts + 1):
            cur_syll = khmer_syllable_count(cur_text)
            if cur_syll <= max_syllables:
                return cur_text

            provider = self.settings.translation_provider.lower()
            prompt = (
                f"You are a master Khmer film dubbing director.\n"
                f"The current Khmer line has {cur_syll} syllables, but the actor's mouth slot is only {slot_sec:.2f}s.\n"
                f"Constraint: Rewrite into punchy colloquial spoken Khmer with AT MOST {max_syllables} syllables!\n"
                f"Original meaning: \"{src_text}\"\n"
                f"Current line: \"{cur_text}\"\n"
                f"Rules: Daily spoken Khmer only, no book words, no filler phrases.\n"
                f"Reply with ONLY the condensed spoken Khmer line."
            )
            condensed = ""
            if provider == "gemini" and self.settings.gemini_api_key:
                try:
                    condensed = self._call_gemini_api(prompt, timeout=8.0)
                except Exception:
                    pass

            if not condensed and provider == "openai" and self.settings.openai_api_key:
                try:
                    import httpx
                    headers = {"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"}
                    body = {
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3
                    }
                    with httpx.Client(timeout=8.0) as client:
                        r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body)
                        condensed = r.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")
                except Exception:
                    pass

            if not condensed or khmer_syllable_count(condensed) >= cur_syll:
                condensed = self._heuristic_condense_khmer(cur_text, max_syllables, src_text=src_text)

            new_syll = khmer_syllable_count(condensed)
            if new_syll < cur_syll:
                cur_text = condensed
                if new_syll <= max_syllables:
                    return cur_text

        return cur_text

    def shorten_khmer_text(self, src_text: str, current_km: str, glossary: Optional[List[GlossaryItem]] = None) -> str:
        """
        Time-Sync Rule 2:
        Condenses the translated text to fit into a tighter slot while retaining natural spoken meaning.
        """
        return self.condense_khmer_dialogue(current_km, max_syllables=10, src_text=src_text, slot_sec=1.5)
