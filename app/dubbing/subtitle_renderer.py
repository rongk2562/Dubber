"""
Subtitle Renderer: Exact 1-to-1 Subtitle Generation, Noto Sans Khmer Styling,
and User Glossary Support.
Part of the modular Dubbing Engine suite.
"""

import os
import re
from pathlib import Path
from typing import List, Optional
from app.models import Segment, GlossaryItem
from app.dubbing.transliteration import transliterate_khmer_dialogue


class SubtitleRenderer:
    """
    Renders Khmer subtitles and constructs clean FFmpeg subtitle filters.
    """

    @classmethod
    def apply_glossary(cls, text: str, custom_glossary: Optional[List[GlossaryItem]] = None) -> str:
        """
        Replace terms using user custom glossary and transliterate any Chinese/Latin names to Khmer.
        """
        if not text:
            return ""

        result = text
        if custom_glossary:
            for item in custom_glossary:
                if item.src_term and item.km_term:
                    pattern = re.compile(re.escape(item.src_term), re.IGNORECASE)
                    result = pattern.sub(item.km_term, result)

        # Guarantee all Latin/Pinyin names (Gang Hong, Baoshou, Su Bao, etc.) are converted to Khmer
        result = transliterate_khmer_dialogue(result)
        return result

    @classmethod
    def format_srt_timestamp(cls, seconds: float) -> str:
        """Format seconds to SRT timestamp: HH:MM:SS,mmm"""
        total_ms = int(round(seconds * 1000))
        hours = total_ms // 3600000
        minutes = (total_ms % 3600000) // 60000
        secs = (total_ms % 60000) // 1000
        ms = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    @classmethod
    def insert_khmer_word_boundaries(cls, text: str) -> str:
        """
        Insert zero-width spaces (\u200b) or break marks for proper line wrapping in video players.
        """
        # Khmer vowel signs and diacritics
        # Separate punctuation and clauses cleanly
        clean = text.strip()
        # Add soft break after Khmer punctuation (។, ៗ, ?)
        clean = re.sub(r'([។៕\?!])\s*', r'\1 ', clean)
        return clean

    @classmethod
    def generate_srt(
        cls,
        segments: List[Segment],
        output_srt_path: str,
        custom_glossary: Optional[List[GlossaryItem]] = None
    ) -> str:
        """
        Generate exact 1-to-1 SRT subtitle file synchronized with actual speech timestamps.
        """
        sorted_segs = sorted(segments, key=lambda s: s.start)
        lines = []

        sub_index = 1
        for seg in sorted_segs:
            text = seg.text_km or seg.text_src
            if not text or not text.strip():
                continue

            # Apply character glossary
            rendered_text = cls.apply_glossary(text, custom_glossary)
            rendered_text = cls.insert_khmer_word_boundaries(rendered_text)

            # Use exact synchronized speech timestamps
            st = seg.speech_start
            et = seg.speech_end
            if et <= st:
                et = st + 1.0

            st_str = cls.format_srt_timestamp(st)
            et_str = cls.format_srt_timestamp(et)

            lines.append(f"{sub_index}")
            lines.append(f"{st_str} --> {et_str}")
            lines.append(f"{rendered_text.strip()}")
            lines.append("")
            sub_index += 1

        content = "\n".join(lines)
        with open(output_srt_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_srt_path

    @classmethod
    def build_video_filter(
        cls,
        srt_path: str,
        mask_original_subs: bool = False,
        font_name: str = "Noto Sans Khmer",
        font_size: int = 20,
        mask_y_pct: float = 83.0,
        mask_h_pct: float = 12.0,
        mask_w_pct: float = 100.0,
        mask_x_pct: float = 0.0,
        mask_opacity: float = 0.95,
        mask_color: str = "black",
        subtitle_margin_v: int = 25,
        video_w: int = 1280,
        video_h: int = 720,
        logo_path: Optional[str] = None,
        logo_pos: str = "top_right",
        logo_w: int = 120,
        logo_opacity: float = 0.90,
        logo_margin_x: int = 20,
        logo_margin_y: int = 20
    ) -> str:
        """
        Construct clean FFmpeg filter chain for burning high-contrast Khmer subtitles
        with customizable full-timeline Chinese subtitle masking band.
        Uses exact 1:1 PlayResX & PlayResY matching the video dimensions so MarginV
        and Fontsize accurately match the user preview without libass 288p distortion.
        """
        norm_srt = srt_path.replace("\\", "/").replace(":", "\\:")

        # Exact pixel scaling relative to 720p base
        actual_fs = max(12, int(font_size * (video_h / 720.0)))
        actual_mv = int(subtitle_margin_v * (video_h / 720.0))

        # Horizontal margin bounds to center subtitle inside the mask box
        if mask_w_pct < 99.9:
            box_left_x = int(video_w * (mask_x_pct / 100.0))
            box_w = int(video_w * (mask_w_pct / 100.0))
            margin_l = max(5, box_left_x)
            margin_r = max(5, video_w - (box_left_x + box_w))
            margin_lr = f",MarginL={margin_l},MarginR={margin_r}"
        else:
            margin_lr = ",MarginL=10,MarginR=10"

        # Subtitle styling with PlayResX/PlayResY to guarantee exact pixel positioning
        style_expr = (
            f"PlayResX={video_w},"
            f"PlayResY={video_h},"
            f"Fontname={font_name},"
            f"Fontsize={actual_fs},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"BorderStyle=1,"
            f"Outline=2.5,"
            f"Shadow=1.0,"
            f"Alignment=2,"
            f"MarginV={actual_mv}"
            f"{margin_lr}"
        )

        # Full-timeline original Chinese subtitle masking band (resolution-independent percentage)
        mask_filter = ""
        if mask_original_subs:
            y_expr = f"ih*{mask_y_pct / 100.0:.3f}"
            h_expr = f"ih*{mask_h_pct / 100.0:.3f}"
            if mask_w_pct < 99.9:
                w_expr = f"iw*{mask_w_pct / 100.0:.3f}"
                x_expr = f"iw*{mask_x_pct / 100.0:.3f}"
            else:
                w_expr = "iw"
                x_expr = "0"
            color_str = f"{mask_color}@{mask_opacity:.2f}"
            mask_filter = f"drawbox=x={x_expr}:y={y_expr}:w={w_expr}:h={h_expr}:color={color_str}:t=fill,"

        base_chain = f"{mask_filter}subtitles='{norm_srt}':force_style='{style_expr}'"

        # Step 3: Handle logo overlay if provided
        if logo_path and os.path.exists(logo_path):
            mx = logo_margin_x
            my = logo_margin_y
            if logo_pos == "top_left":
                pos_expr = f"{mx}:{my}"
            elif logo_pos == "bottom_left":
                pos_expr = f"{mx}:H-h-{my}"
            elif logo_pos == "bottom_right":
                pos_expr = f"W-w-{mx}:H-h-{my}"
            elif logo_pos == "center":
                pos_expr = f"(W-w)/2:(H-h)/2"
            else:  # top_right
                pos_expr = f"W-w-{mx}:{my}"

            complex_filter = (
                f"[0:v]{base_chain}[v_sub];"
                f"[2:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={logo_opacity:.2f}[logo];"
                f"[v_sub][logo]overlay={pos_expr}[v_out]"
            )
            return complex_filter

        return base_chain
