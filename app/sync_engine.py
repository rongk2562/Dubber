"""
Time-Synchronization Engine
Strictly implements SRS Section 4: Time-sync Rules (Most Critical)
4-Stage cascade:
1. Increase TTS speed (max ~1.25x)
2. Ask LLM to shorten translated text
3. Borrow empty gap time from next segment
4. Time-stretch with FFmpeg atempo (max 1.3x)
Enforces: Never allow two segments' audio to overlap & Generates detailed Sync Report
"""

import os
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable
from app.models import Segment, Project
from app.config import MAX_TTS_SPEED, MAX_ATEMPO_SPEED
from app.audio_engine import AudioEngine
from app.tts_engine import TTSEngine
from app.translation_engine import TranslationEngine

@dataclass
class SyncReportItem:
    segment_id: int
    slot_sec: float
    original_tts_sec: float
    final_tts_sec: float
    sync_status: str       # "OK", "SPEED_ADJUSTED", "SHORTENED", "GAP_BORROWED", "TIME_STRETCHED", "OVERFLOW"
    borrowed_gap_sec: float
    speed_factor: float
    notes: str
    is_overflow: bool

@dataclass
class SyncReport:
    total_segments: int = 0
    ok_count: int = 0
    speed_adjusted_count: int = 0
    shortened_count: int = 0
    gap_borrowed_count: int = 0
    time_stretched_count: int = 0
    overflow_count: int = 0
    items: List[SyncReportItem] = None

    def __post_init__(self):
        if self.items is None:
            self.items = []

class SyncEngine:
    def __init__(
        self,
        audio_engine: Optional[AudioEngine] = None,
        tts_engine: Optional[TTSEngine] = None,
        translation_engine: Optional[TranslationEngine] = None
    ):
        self.audio_engine = audio_engine or AudioEngine()
        self.tts_engine = tts_engine or TTSEngine()
        self.translation_engine = translation_engine or TranslationEngine()

    def synchronize_segments(
        self,
        project: Project,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> SyncReport:
        """
        Execute 4-stage time-sync algorithm across all project segments.
        """
        segments = sorted(project.segments, key=lambda s: s.start)
        report = SyncReport(total_segments=len(segments))
        total = len(segments)

        for i, seg in enumerate(segments):
            if progress_cb:
                pct = int((i / max(1, total)) * 100)
                progress_cb(pct, f"Synchronizing segment {seg.id}/{total}...")

            slot = seg.slot
            if slot <= 0:
                slot = 1.0
                seg.end = seg.start + 1.0

            # 0. Ensure TTS audio exists if text is present
            if (not seg.tts_file or not os.path.exists(seg.tts_file)) and (seg.text_km or seg.text_src):
                self.tts_engine.generate_segment_tts(seg, speed_factor=1.0)

            orig_dur = seg.tts_duration
            final_dur = orig_dur
            status = "OK"
            borrowed_gap = 0.0
            notes = "Fits naturally in slot."

            # Case A: Fits naturally
            if orig_dur <= slot:
                status = "OK"
                seg.speed = 1.0
                seg.borrowed_gap = 0.0
                report.ok_count += 1

            else:
                # Case B: Duration exceeds slot -> Apply 4-Stage Cascade in Order
                status = "UNRESOLVED"
                
                # --- STAGE 1: Increase TTS speed (max ~1.25x) ---
                needed_speed = round(orig_dur / slot, 3)
                if needed_speed <= MAX_TTS_SPEED:
                    if seg.text_km or seg.text_src:
                        self.tts_engine.generate_segment_tts(seg, speed_factor=needed_speed)
                    else:
                        seg.speed = needed_speed
                        seg.tts_duration = round(orig_dur / needed_speed, 3)

                    if seg.tts_duration <= slot + 0.05:
                        status = "SPEED_ADJUSTED"
                        final_dur = seg.tts_duration
                        notes = f"Increased TTS speed to {needed_speed:.2f}x to fit {slot:.2f}s slot."
                        report.speed_adjusted_count += 1
                        
                # --- STAGE 2: Ask the LLM to shorten the translated text ---
                if status == "UNRESOLVED":
                    # Try shortening Khmer text
                    old_text = seg.text_km
                    shortened_text = self.translation_engine.shorten_khmer_text(
                        seg.text_src, seg.text_km, project.glossary
                    )
                    
                    if shortened_text and shortened_text != old_text:
                        seg.text_km = shortened_text
                        self.tts_engine.generate_segment_tts(seg, speed_factor=1.0)
                        
                        if seg.tts_duration <= slot:
                            status = "SHORTENED"
                            final_dur = seg.tts_duration
                            notes = "Shortened Khmer phrasing via LLM."
                            report.shortened_count += 1
                        else:
                            # Try slight speedup on shortened text
                            sub_speed = min(MAX_TTS_SPEED, round(seg.tts_duration / slot, 3))
                            self.tts_engine.generate_segment_tts(seg, speed_factor=sub_speed)
                            if seg.tts_duration <= slot + 0.05:
                                status = "SHORTENED"
                                final_dur = seg.tts_duration
                                notes = f"Shortened text and adjusted speed to {sub_speed:.2f}x."
                                report.shortened_count += 1

                # --- STAGE 3: Borrow empty gap time from the next segment ---
                if status == "UNRESOLVED":
                    # Calculate available gap to next segment
                    next_start = segments[i + 1].start if (i + 1 < len(segments)) else (seg.end + 10.0)
                    available_gap = max(0.0, next_start - seg.end)
                    
                    # Reserve 0.1s margin to ensure no overlap
                    usable_gap = max(0.0, available_gap - 0.10)
                    current_effective_dur = seg.tts_duration
                    deficit = current_effective_dur - slot

                    if deficit <= usable_gap:
                        borrowed_gap = round(deficit + 0.05, 3)
                        seg.borrowed_gap = borrowed_gap
                        status = "GAP_BORROWED"
                        final_dur = current_effective_dur
                        notes = f"Borrowed {borrowed_gap:.2f}s empty gap from next segment."
                        report.gap_borrowed_count += 1
                    else:
                        # Borrow whatever gap is available and proceed to Stage 4
                        if usable_gap > 0.2:
                            borrowed_gap = round(usable_gap, 3)
                            seg.borrowed_gap = borrowed_gap

                # --- STAGE 4: Time-stretch with FFmpeg atempo (max 1.3x) ---
                if status == "UNRESOLVED":
                    allowed_window = slot + borrowed_gap
                    stretch_ratio = round(seg.tts_duration / allowed_window, 3)
                    
                    if stretch_ratio <= MAX_ATEMPO_SPEED:
                        if seg.tts_file and os.path.exists(seg.tts_file):
                            stretched_file = self.audio_engine.time_stretch_atempo(
                                seg.tts_file, speed_factor=stretch_ratio
                            )
                            seg.tts_file = stretched_file
                        # Update new duration
                        new_dur = round(seg.tts_duration / stretch_ratio, 3)
                        seg.tts_duration = new_dur
                        seg.speed = stretch_ratio
                        status = "TIME_STRETCHED"
                        final_dur = new_dur
                        notes = f"Time-stretched with FFmpeg atempo ({stretch_ratio:.2f}x)."
                        report.time_stretched_count += 1
                    else:
                        # OVERFLOW: Even with max speed (1.3x) and gap borrowing, it still exceeds!
                        max_stretch = MAX_ATEMPO_SPEED
                        if seg.tts_file and os.path.exists(seg.tts_file):
                            stretched_file = self.audio_engine.time_stretch_atempo(
                                seg.tts_file, speed_factor=max_stretch
                            )
                            seg.tts_file = stretched_file
                        seg.tts_duration = round(seg.tts_duration / max_stretch, 3)
                        seg.speed = max_stretch
                        status = "OVERFLOW"
                        final_dur = seg.tts_duration
                        overflow_amt = round(final_dur - allowed_window, 2)
                        notes = f"OVERFLOW: Still exceeds window by +{overflow_amt:.2f}s. Manual shortening advised."
                        report.overflow_count += 1

            seg.sync_status = status
            seg.sync_notes = notes

            report.items.append(SyncReportItem(
                segment_id=seg.id,
                slot_sec=slot,
                original_tts_sec=orig_dur,
                final_tts_sec=final_dur,
                sync_status=status,
                borrowed_gap_sec=borrowed_gap,
                speed_factor=seg.speed,
                notes=notes,
                is_overflow=(status == "OVERFLOW")
            ))

        if progress_cb:
            progress_cb(100, f"Synchronization complete! (Fits: {report.ok_count}, Adjusted: {report.speed_adjusted_count + report.shortened_count + report.gap_borrowed_count + report.time_stretched_count}, Overflow: {report.overflow_count})")

        return report
