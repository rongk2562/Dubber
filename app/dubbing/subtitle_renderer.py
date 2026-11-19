"""
Subtitle Renderer: Exact 1-to-1 Subtitle Generation, Noto Sans Khmer Styling,
and User Glossary Support.
Part of the modular Dubbing Engine suite.
"""

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple
from app.models import Segment, GlossaryItem
from app.dubbing.transliteration import transliterate_khmer_dialogue


def _probe_image_size(image_path: str) -> Tuple[int, int]:
    """Read native image dimensions without instantiating a GUI window."""
    if not image_path or not os.path.exists(image_path):
        return (200, 200)
    try:
        from PySide6.QtGui import QImageReader
        reader = QImageReader(image_path)
        sz = reader.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            return (int(sz.width()), int(sz.height()))
    except Exception:
        pass
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            return (max(1, int(im.width)), max(1, int(im.height)))
    except Exception:
        pass
    return (200, 200)


def logo_geometry(
    video_w: int,
    video_h: int,
    logo_w: int,
    logo_h: int,
    pos: str = "top_right",
    width_pct: float = 12.0,
    margin_pct: float = 2.5,
    custom_x_pct: Optional[float] = None,
    custom_y_pct: Optional[float] = None,
) -> Tuple[int, int, int, int]:
    """
    Single geometry source of truth shared by Live QGraphicsView Preview and FFmpeg export.
    Returns exact pixel (x, y, w, h) in video coordinates.
    """
    vw = max(1, int(video_w))
    vh = max(1, int(video_h))
    lw_src = max(1, int(logo_w))
    lh_src = max(1, int(logo_h))

    w_pct = max(5.0, min(40.0, float(width_pct)))
    m_pct = max(0.0, min(10.0, float(margin_pct)))

    w = max(12, int(round(vw * (w_pct / 100.0))))
    h = max(12, int(round(w * (lh_src / float(lw_src)))))
    if h > vh:
        h = vh
        w = max(12, int(round(h * (lw_src / float(lh_src)))))

    margin_px = int(round(min(vw, vh) * (m_pct / 100.0)))

    if pos == "custom" and custom_x_pct is not None and custom_x_pct >= 0.0 and custom_y_pct is not None and custom_y_pct >= 0.0:
        x = int(round(vw * (float(custom_x_pct) / 100.0)))
        y = int(round(vh * (float(custom_y_pct) / 100.0)))
    elif pos == "top_left":
        x, y = margin_px, margin_px
    elif pos == "bottom_left":
        x, y = margin_px, vh - h - margin_px
    elif pos == "bottom_right":
        x, y = vw - w - margin_px, vh - h - margin_px
    elif pos == "center":
        x, y = (vw - w) // 2, (vh - h) // 2
    else:  # "top_right"
        x, y = vw - w - margin_px, margin_px

    x = max(0, min(vw - w, int(round(x))))
    y = max(0, min(vh - h, int(round(y))))
    return (x, y, w, h)


class SubtitleRenderer:
    """
    Renders Khmer subtitles and constructs clean FFmpeg subtitle filters.
    """
    logo_geometry = staticmethod(logo_geometry)

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

    @staticmethod
    def blur_strength_to_sigma(strength: int) -> float:
        """Convert UI Blur Strength (1..100) to 720p base gblur sigma (40 -> 18.0)."""
        s = max(1, min(100, int(strength)))
        return round(max(0.5, (s / 40.0) * 18.0), 2)

    @staticmethod
    def _to_even(val: int, min_val: int = 2) -> int:
        v = max(min_val, int(round(val)))
        return v if (v % 2 == 0) else (v - 1 if v - 1 >= min_val else v + 1)

    @classmethod
    def compute_mask_pixel_rect(
        cls,
        video_w: int,
        video_h: int,
        mask_x_pct: float,
        mask_y_pct: float,
        mask_w_pct: float,
        mask_h_pct: float,
        pad_px: int = 6,
    ) -> tuple[int, int, int, int]:
        """
        Single mapping from percentage coordinates -> even pixel (x, y, w, h)
        with padding around the crop rect to prevent Chinese character edge leaks.
        """
        raw_x = int(round(video_w * (mask_x_pct / 100.0))) - pad_px
        raw_y = int(round(video_h * (mask_y_pct / 100.0))) - pad_px
        raw_w = int(round(video_w * (mask_w_pct / 100.0))) + 2 * pad_px
        raw_h = int(round(video_h * (mask_h_pct / 100.0))) + 2 * pad_px

        crop_x = max(0, min(video_w - 4, raw_x))
        crop_y = max(0, min(video_h - 4, raw_y))
        crop_w = cls._to_even(min(video_w - crop_x, raw_w), 4)
        crop_h = cls._to_even(min(video_h - crop_y, raw_h), 4)
        # Ensure x + w <= video_w and y + h <= video_h
        if crop_x + crop_w > video_w:
            crop_w = cls._to_even(video_w - crop_x, 4)
        if crop_y + crop_h > video_h:
            crop_h = cls._to_even(video_h - crop_y, 4)
        return crop_x, crop_y, crop_w, crop_h

    @staticmethod
    def is_complex_filter(filter_str: str) -> bool:
        """Return True if filter string uses filter_complex stream labels like [v_out]."""
        return "[v_out]" in (filter_str or "")

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
        mask_style: str = "solid",
        blur_strength: int = 40,
        blur_dim: int = 25,
        mask_feather: bool = False,
        subtitle_margin_v: int = 25,
        video_w: int = 1280,
        video_h: int = 720,
        logo_path: Optional[str] = None,
        logo_pos: str = "top_right",
        logo_w: int = 120,
        logo_opacity: float = 0.90,
        logo_margin_x: int = 20,
        logo_margin_y: int = 20,
        logo_width_pct: Optional[float] = None,
        logo_margin_pct: Optional[float] = None,
        logo_custom_x_pct: Optional[float] = None,
        logo_custom_y_pct: Optional[float] = None,
        logo_input_idx: int = 2,
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

        fonts_part = ""
        try:
            from app.config import get_resource_path
            fonts_dir = get_resource_path("assets/fonts")
            if fonts_dir.exists() and fonts_dir.is_dir():
                norm_fonts = str(fonts_dir).replace("\\", "/").replace(":", "\\:")
                fonts_part = f":fontsdir='{norm_fonts}'"
        except Exception:
            fonts_part = ""

        sub_filter = f"subtitles='{norm_srt}'{fonts_part}:force_style='{style_expr}'"

        has_logo = bool(logo_path and os.path.exists(logo_path))
        logo_chain = ""
        lx, ly = 0, 0
        if has_logo:
            nat_w, nat_h = _probe_image_size(logo_path)
            w_pct = logo_width_pct if logo_width_pct is not None else max(5.0, min(40.0, (float(logo_w) / max(1, video_w)) * 100.0))
            m_pct = logo_margin_pct if logo_margin_pct is not None else max(0.0, min(10.0, (float(logo_margin_x) / max(1, min(video_w, video_h))) * 100.0))
            lx, ly, lw_px, lh_px = logo_geometry(
                video_w, video_h, nat_w, nat_h,
                pos=logo_pos, width_pct=w_pct, margin_pct=m_pct,
                custom_x_pct=logo_custom_x_pct, custom_y_pct=logo_custom_y_pct,
            )
            op = max(0.05, min(1.0, float(logo_opacity)))
            logo_chain = f"[{logo_input_idx}:v]scale={lw_px}:{lh_px},format=rgba,colorchannelmixer=aa={op:.2f}[lg];"

        # Full-timeline original Chinese subtitle masking band
        if mask_original_subs and (mask_style or "solid").lower() == "blur":
            crop_x, crop_y, crop_w, crop_h = cls.compute_mask_pixel_rect(
                video_w, video_h, mask_x_pct, mask_y_pct, mask_w_pct, mask_h_pct, pad_px=6
            )
            sigma_video = cls.blur_strength_to_sigma(blur_strength) * (video_h / 720.0)
            dim_b = (max(0, min(60, int(blur_dim))) / 100.0) * 0.5
            feather_part = ""
            if mask_feather:
                feather_px = max(6, int(round(12 * (video_h / 720.0))))
                feather_part = (
                    f",format=yuva420p,"
                    f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                    f"a='255*min(1,min(X,W-1-X)/{feather_px})*min(1,min(Y,H-1-Y)/{feather_px})'"
                )

            blur_graph = (
                f"[0:v]split[a][b];"
                f"[b]crop=w={crop_w}:h={crop_h}:x={crop_x}:y={crop_y},"
                f"gblur=sigma={sigma_video:.2f}:steps=3,"
                f"eq=brightness=-{dim_b:.3f}{feather_part}[bl];"
                f"[a][bl]overlay={crop_x}:{crop_y}[v_masked];"
            )
            if has_logo:
                return (
                    f"{blur_graph}"
                    f"[v_masked]{sub_filter}[v_sub];"
                    f"{logo_chain}"
                    f"[v_sub][lg]overlay={lx}:{ly}[v_out]"
                )
            return f"{blur_graph}[v_masked]{sub_filter}[v_out]"

        # Solid drawbox mode (default)
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

        base_chain = f"{mask_filter}{sub_filter}"
        if has_logo:
            return (
                f"[0:v]{base_chain}[v_sub];"
                f"{logo_chain}"
                f"[v_sub][lg]overlay={lx}:{ly}[v_out]"
            )
        return base_chain

    @classmethod
    def build_logo_only_filter(
        cls,
        video_w: int,
        video_h: int,
        logo_path: str,
        logo_pos: str = "top_right",
        logo_width_pct: float = 12.0,
        logo_margin_pct: float = 2.5,
        logo_opacity: float = 0.90,
        logo_custom_x_pct: Optional[float] = None,
        logo_custom_y_pct: Optional[float] = None,
        logo_input_idx: int = 2,
    ) -> str:
        """Build exact FFmpeg filter_complex graph for logo overlay without subtitle burn."""
        nat_w, nat_h = _probe_image_size(logo_path)
        lx, ly, lw_px, lh_px = logo_geometry(
            video_w, video_h, nat_w, nat_h,
            pos=logo_pos, width_pct=logo_width_pct, margin_pct=logo_margin_pct,
            custom_x_pct=logo_custom_x_pct, custom_y_pct=logo_custom_y_pct,
        )
        op = max(0.05, min(1.0, float(logo_opacity)))
        return f"[{logo_input_idx}:v]scale={lw_px}:{lh_px},format=rgba,colorchannelmixer=aa={op:.2f}[lg];[0:v][lg]overlay={lx}:{ly}[v_out]"
