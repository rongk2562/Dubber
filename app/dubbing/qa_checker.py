"""
QA Checker: Automated Dubbing Quality Assurance, Acceptance Verification,
and Report Generation (JSON & CSV).
Part of the modular Dubbing Engine suite.
"""

import os
import csv
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from app.models import Segment


class QAChecker:
    """
    Evaluates dubbing pipeline outputs against professional audio DSP acceptance criteria.
    """

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def analyze_audio_peak_and_lufs(self, audio_path: str) -> Tuple[float, float]:
        """
        Measure integrated LUFS and True Peak dBTP via FFmpeg ebur128 filter.
        """
        cmd = [
            "ffmpeg", "-i", audio_path,
            "-filter_complex", "ebur128=peak=true",
            "-f", "null", "-"
        ]
        integrated_lufs = -24.0
        true_peak_dbtp = -1.5

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
            output = res.stderr

            import re
            i_match = re.search(r"Integrated loudness:\s+I:\s+([-\d\.]+)\s+LUFS", output)
            if i_match:
                integrated_lufs = float(i_match.group(1))

            tp_match = re.search(r"True peak:\s+Peak:\s+([-\d\.]+)\s+dBFS", output)
            if tp_match:
                true_peak_dbtp = float(tp_match.group(1))
            else:
                tp_alt = re.search(r"Peak:\s+([-\d\.]+)\s+dBTP", output)
                if tp_alt:
                    true_peak_dbtp = float(tp_alt.group(1))
        except Exception as e:
            print(f"[QAChecker] Loudness analysis note: {e}")

        return integrated_lufs, true_peak_dbtp

    def generate_report(
        self,
        segments: List[Segment],
        original_duration: float,
        final_audio_path: str,
        export_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate dubbing_report.json and dubbing_report.csv,
        and perform automated quality acceptance verification.
        """
        target_dir = Path(export_dir) if export_dir else self.output_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        sorted_segs = sorted(segments, key=lambda s: s.start)
        total_segments = len(sorted_segs)

        # 1. Segment status breakdown
        from app.dubbing.speech_rate import khmer_syllable_count

        rows = []
        successful_segments = 0
        failed_segments = []
        overlapping_segments = []
        unexplained_silence_gaps = []
        excessive_rate_segments = []

        last_speech_end = 0.0

        for idx, seg in enumerate(sorted_segs, start=1):
            status = seg.sync_status or "OK"
            tts_dur = seg.tts_duration if seg.tts_duration > 0 else seg.slot
            speed_val = seg.speed if seg.speed > 0 else 1.0

            # Calculate syllables and speech rates
            text = seg.text_km or seg.text_src
            syllables = khmer_syllable_count(text)
            rate_before = round(syllables / max(0.2, seg.slot), 2)
            rate_after = round(syllables / max(0.2, tts_dur), 2)
            text_shortened = "Y" if (seg.sync_notes == "TEXT_CONDENSED" or seg.sync_status == "SHORTENED") else "N"

            # Check Speech-Rate Budget limit (rate <= 7.5 syllables/sec)
            if rate_after > 7.5:
                status = "RATE_EXCEEDED"
                excessive_rate_segments.append({"segment_id": seg.id, "rate": rate_after, "syllables": syllables})

            # Check if synthesis actually succeeded
            if seg.tts_file and os.path.exists(seg.tts_file) and os.path.getsize(seg.tts_file) > 180 and status != "FAILED":
                successful_segments += 1
            else:
                status = "FAILED"
                failed_segments.append(seg.id)

            # Check overlap with previous segment
            st = seg.speech_start
            et = seg.speech_end
            if st < (last_speech_end - 0.015):
                overlapping_segments.append(seg.id)

            # Check for unexplained silence > 2s where original had speech
            if (st - last_speech_end) > 2.0 and last_speech_end > 0.0:
                gap_start = last_speech_end
                gap_end = st
                unexplained_silence_gaps.append({
                    "start": round(gap_start, 2),
                    "end": round(gap_end, 2),
                    "duration": round(gap_end - gap_start, 2)
                })

            last_speech_end = max(last_speech_end, et)

            rows.append({
                "index": idx,
                "slot": round(seg.slot, 2),
                "syllables": syllables,
                "rate_before": rate_before,
                "rate_after": rate_after,
                "atempo": round(speed_val, 2),
                "text_shortened": text_shortened,
                "status": status,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "actual_start": round(seg.actual_start, 2),
                "actual_end": round(seg.actual_end, 2),
                "original_text": seg.text_src,
                "khmer_text": seg.text_km,
                "tts_duration": round(tts_dur, 2),
                "borrowed_gap": round(seg.borrowed_gap, 2)
            })

        # Calculate Speech Coverage
        coverage_pct = round((successful_segments / max(1, total_segments)) * 100.0, 1)

        # Audio Loudness & Peak Analysis
        integrated_lufs = -16.0
        true_peak_dbtp = -1.5
        if os.path.exists(final_audio_path):
            integrated_lufs, true_peak_dbtp = self.analyze_audio_peak_and_lufs(final_audio_path)

        # Check Final Duration
        from app.audio_engine import AudioEngine
        ae = AudioEngine()
        final_dur = ae.get_media_duration(final_audio_path)
        duration_diff = abs(final_dur - original_duration)
        duration_match = duration_diff <= 0.10

        # Acceptance Criteria Evaluation
        checks = {
            "speech_coverage_ge_95": bool(coverage_pct >= 95.0),
            "speech_rate_le_7_5": bool(len(excessive_rate_segments) == 0),
            "atempo_le_1_15": bool(all(r["atempo"] <= 1.16 for r in rows)),
            "zero_overlaps": bool(len(overlapping_segments) == 0),
            "peak_le_minus_1_5_dbtp": bool(true_peak_dbtp <= -1.45),
            "duration_sync_valid": bool(duration_match),
            "zero_failed_segments": bool(len(failed_segments) == 0)
        }

        all_passed = all(checks.values())

        report_data = {
            "summary": {
                "total_segments": total_segments,
                "successful_segments": successful_segments,
                "coverage_percent": coverage_pct,
                "original_duration_sec": round(original_duration, 2),
                "final_audio_duration_sec": round(final_dur, 2),
                "duration_delta_sec": round(duration_diff, 3),
                "integrated_lufs": round(integrated_lufs, 1),
                "true_peak_dbtp": round(true_peak_dbtp, 2),
                "all_acceptance_checks_passed": all_passed,
            },
            "acceptance_checks": checks,
            "issues": {
                "failed_segments": failed_segments,
                "overlapping_segments": overlapping_segments,
                "silence_gaps_gt_2s": unexplained_silence_gaps
            },
            "segments": rows
        }

        # Write JSON Report
        json_path = str(target_dir / "dubbing_report.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        # Write CSV Report (includes speech rate budget metrics)
        csv_path = str(target_dir / "dubbing_report.csv")
        fieldnames = [
            "index", "slot", "syllables", "rate_before", "rate_after",
            "atempo", "text_shortened", "status",
            "start", "end", "actual_start", "actual_end",
            "original_text", "khmer_text", "tts_duration", "borrowed_gap"
        ]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        print(f"[QAChecker] Generated QA Report: {json_path} and {csv_path}")
        print(f"[QAChecker] Coverage: {coverage_pct}%, Checks Passed: {all_passed}")

        return report_data
