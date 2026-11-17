"""
Utility Functions: Time formatting, SRT parsing & export, Audio helpers
"""

import re
import os
import shutil
from typing import List, Tuple
from app.models import Segment

def format_timestamp(seconds: float, srt_format: bool = True) -> str:
    """
    Format seconds into HH:MM:SS,mmm (SRT) or MM:SS.ss (UI)
    """
    if seconds < 0:
        seconds = 0.0
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
        
    if srt_format:
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    else:
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}.{int(millis/10):02d}"
        return f"{minutes:02d}:{secs:02d}.{int(millis/10):02d}"

def parse_timestamp(timestamp_str: str) -> float:
    """
    Parse HH:MM:SS,mmm or HH:MM:SS.mmm or MM:SS into seconds
    """
    timestamp_str = timestamp_str.strip().replace(",", ".")
    parts = timestamp_str.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600 + float(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return float(m) * 60 + float(s)
    elif len(parts) == 1:
        return float(parts[0])
    return 0.0

def generate_srt(segments: List[Segment], language: str = "km") -> str:
    """
    Generate standard SRT formatted text from segments.
    language: 'km' (Khmer), 'src' (Source), or 'both' (Bilingual)
    """
    lines = []
    idx = 1
    sorted_segs = sorted(segments, key=lambda s: s.start)
    for i, seg in enumerate(sorted_segs):
        if language == "km":
            text = seg.text_km or seg.text_src
        elif language == "src":
            text = seg.text_src
        else: # both
            text = f"{seg.text_km}\n{seg.text_src}" if seg.text_km else seg.text_src
            
        if not text.strip():
            continue

        start_sec = seg.speech_start
        end_sec = seg.speech_end

        # Ensure subtitle ends cleanly before next subtitle begins
        if i + 1 < len(sorted_segs):
            next_start = sorted_segs[i + 1].speech_start
            if end_sec > next_start - 0.04:
                end_sec = max(start_sec + 0.4, next_start - 0.04)
                
        start_str = format_timestamp(start_sec, srt_format=True)
        end_str = format_timestamp(end_sec, srt_format=True)
            
        lines.append(str(idx))
        lines.append(f"{start_str} --> {end_str}")
        lines.append(text.strip())
        lines.append("") # blank line separator
        idx += 1
        
    return "\n".join(lines)

def parse_srt(srt_content: str) -> List[Segment]:
    """
    Parse standard SRT subtitle text into Segment objects.
    """
    segments: List[Segment] = []
    blocks = re.split(r'\n\s*\n', srt_content.strip())
    
    seg_id = 1
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
            
        # Line 0 might be index or timecode
        time_line_idx = 1 if "-->" in lines[1] else (0 if "-->" in lines[0] else -1)
        if time_line_idx == -1:
            continue
            
        time_match = re.match(r'([\d:,.]+)\s*-->\s*([\d:,.]+)', lines[time_line_idx])
        if not time_match:
            continue
            
        start_sec = parse_timestamp(time_match.group(1))
        end_sec = parse_timestamp(time_match.group(2))
        
        text = "\n".join(lines[time_line_idx + 1:]).strip()
        segments.append(Segment(
            id=seg_id,
            start=start_sec,
            end=end_sec,
            speaker="S1",
            text_src=text,
            text_km="",
        ))
        seg_id += 1
        
    return segments

def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is in PATH or available executable."""
    return shutil.which("ffmpeg") is not None
