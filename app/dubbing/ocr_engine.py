"""
Video Hardcoded Subtitle OCR Engine (RapidOCR / PaddleOCR) + OpenCC t2s + VAD Snap + Whisper Comparison.
Extracts hardcoded Chinese subtitles directly from the subtitle ROI to eliminate ASR homophone errors.
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Callable, Dict

import numpy as np

from app.models import Segment, ProjectSettings


@dataclass
class OcrFrameDetection:
    timestamp: float
    text: str
    confidence: float
    box_cx: float
    box_cy: float


@dataclass
class OcrScanReport:
    total_segments: int = 0
    low_conf_count: int = 0
    whisper_mismatch_count: int = 0
    watermarks_filtered: int = 0
    engine_used: str = "rapidocr"
    details: List[str] = field(default_factory=list)


def levenshtein_distance(a: str, b: str) -> int:
    """Compute character-level Levenshtein edit distance."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def levenshtein_similarity(a: str, b: str) -> float:
    """Normalized similarity ratio in [0.0, 1.0]."""
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return 1.0 - (levenshtein_distance(a, b) / float(max_len))


def compute_cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate (CER) = edit_distance / len(reference)."""
    ref = _strip_punct_and_spaces(reference)
    hyp = _strip_punct_and_spaces(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein_distance(ref, hyp) / float(len(ref))


def _strip_punct_and_spaces(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _clean_ocr_chinese_text(text: str, converter=None) -> str:
    """Convert Traditional->Simplified via OpenCC t2s and clean CJK spacing."""
    if not text:
        return ""
    out = text.strip()
    if converter is not None:
        try:
            out = converter.convert(out)
        except Exception:
            pass
    # Remove spaces between CJK characters (OCR frequently inserts gaps)
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _get_opencc_t2s():
    try:
        import opencc
        return opencc.OpenCC("t2s")
    except Exception:
        return None


class SubtitleOcrEngine:
    """
    Scans video frames in the subtitle ROI using RapidOCR (default) or PaddleOCR,
    filters watermarks/noise, merges consecutive frames, and outputs clean Chinese Segments.
    """

    def __init__(self, engine_name: str = "rapidocr"):
        self.engine_name = (engine_name or "rapidocr").lower()
        self._rapid_ocr = None
        self._paddle_ocr = None
        self._opencc = _get_opencc_t2s()

    def _init_ocr(self):
        if self.engine_name == "paddleocr":
            if self._paddle_ocr is None:
                try:
                    from paddleocr import PaddleOCR
                    self._paddle_ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
                except ImportError:
                    # Fallback to RapidOCR if PaddleOCR is not installed
                    self.engine_name = "rapidocr"
        if self.engine_name == "rapidocr" and self._rapid_ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._rapid_ocr = RapidOCR()

    def _run_ocr_on_crop(self, img_rgb: np.ndarray) -> List[Tuple[str, float, float, float]]:
        """
        Run OCR on an RGB numpy image crop.
        Returns list of (text, confidence, norm_cx, norm_cy).
        """
        self._init_ocr()
        h, w = img_rgb.shape[:2]
        results: List[Tuple[str, float, float, float]] = []

        if self.engine_name == "paddleocr" and self._paddle_ocr is not None:
            res = self._paddle_ocr.ocr(img_rgb, cls=False)
            if res and res[0]:
                for line in res[0]:
                    box, (txt, conf) = line[0], line[1]
                    cx = sum(pt[0] for pt in box) / (4.0 * max(1, w))
                    cy = sum(pt[1] for pt in box) / (4.0 * max(1, h))
                    results.append((str(txt), float(conf), cx, cy))
        elif self._rapid_ocr is not None:
            # Upscale small subtitle crops (e.g. 576p/720p) by 2x for crisp CJK recognition
            img_bgr = img_rgb[:, :, ::-1].copy()
            if h < 220:
                import cv2
                img_bgr = cv2.resize(img_bgr, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                uh, uw = img_bgr.shape[:2]
            else:
                uh, uw = h, w
            res, _ = self._rapid_ocr(img_bgr)
            if res:
                for item in res:
                    box, txt, conf = item[0], item[1], item[2]
                    cx = sum(pt[0] for pt in box) / (4.0 * max(1, uw))
                    cy = sum(pt[1] for pt in box) / (4.0 * max(1, uh))
                    results.append((str(txt), float(conf), cx, cy))
        return results

    @staticmethod
    def get_roi_bounds(
        frame_w: int,
        frame_h: int,
        settings: Optional[ProjectSettings] = None,
        custom_roi: Optional[Tuple[float, float, float, float]] = None,
    ) -> Tuple[int, int, int, int]:
        """
        Return pixel (x1, y1, x2, y2) for subtitle ROI.
        Uses custom_roi (x_pct, y_pct, w_pct, h_pct) or ProjectSettings mask values.
        Expands slightly vertically (+/- 2%) to safely capture descenders/top edges.
        """
        if custom_roi is not None:
            x_pct, y_pct, w_pct, h_pct = custom_roi
        elif settings is not None:
            x_pct = getattr(settings, "mask_x_percent", 0.0)
            y_pct = getattr(settings, "mask_y_percent", 78.0)
            w_pct = getattr(settings, "mask_width_percent", 100.0)
            h_pct = getattr(settings, "mask_height_percent", 16.0)
        else:
            x_pct, y_pct, w_pct, h_pct = 0.0, 75.0, 100.0, 22.0

        # Add safety padding around configured mask band so top/bottom of hardcoded subs aren't clipped
        pad_y = 7.0
        y1_pct = max(0.0, y_pct - pad_y)
        y2_pct = min(100.0, y_pct + h_pct + pad_y)
        x1_pct = max(0.0, x_pct)
        x2_pct = min(100.0, x_pct + w_pct)

        x1 = int(frame_w * (x1_pct / 100.0))
        x2 = max(x1 + 10, int(frame_w * (x2_pct / 100.0)))
        y1 = int(frame_h * (y1_pct / 100.0))
        y2 = max(y1 + 10, int(frame_h * (y2_pct / 100.0)))
        return x1, y1, x2, y2

    def detect_hardcoded_subtitles(
        self,
        video_path: str,
        settings: Optional[ProjectSettings] = None,
        custom_roi: Optional[Tuple[float, float, float, float]] = None,
        sample_count: int = 10,
    ) -> bool:
        """
        Quick probe for Auto mode: samples ~10 frames evenly across the video ROI.
        Returns True if hardcoded Chinese subtitles are detected in >= 2 frames.
        """
        import av
        hits = 0
        try:
            with av.open(video_path) as container:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                tb = float(stream.time_base)
                duration = float(stream.duration * tb) if stream.duration else 60.0
                if duration <= 0.5:
                    duration = 30.0
                step = max(1.0, duration / float(sample_count + 1))
                next_t = step
                for frame in container.decode(video=0):
                    if frame.pts is None:
                        continue
                    t = float(frame.pts * tb)
                    if t < next_t:
                        continue
                    next_t = t + step
                    arr = frame.to_ndarray(format="rgb24")
                    fh, fw = arr.shape[:2]
                    x1, y1, x2, y2 = self.get_roi_bounds(fw, fh, settings, custom_roi)
                    crop = arr[y1:y2, x1:x2]
                    dets = self._run_ocr_on_crop(crop)
                    for txt, conf, _, _ in dets:
                        clean = _clean_ocr_chinese_text(txt, self._opencc)
                        if conf >= 0.6 and len(_strip_punct_and_spaces(clean)) >= 2 and _has_cjk(clean):
                            hits += 1
                            break
                    if hits >= 2 or next_t >= duration:
                        break
        except Exception as e:
            print(f"[OCR] Auto-detect probe warning: {e}")
        return hits >= 2

    def scan_video(
        self,
        video_path: str,
        settings: Optional[ProjectSettings] = None,
        fps: float = 5.0,
        min_conf: float = 0.6,
        min_chars: int = 2,
        min_duration: float = 0.3,
        custom_roi: Optional[Tuple[float, float, float, float]] = None,
        vad_segments: Optional[List[Segment]] = None,
        use_ocr_timestamps: bool = True,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[Segment], OcrScanReport]:
        """
        Scan video frames at `fps` (default 5.0 fps) within subtitle ROI,
        filter noise & static watermarks, merge consecutive similar phrases,
        apply OpenCC t2s, and optionally compare against Whisper/VAD.
        """
        import av

        scan_fps = max(1.0, min(12.0, float(fps)))
        step_sec = 1.0 / scan_fps
        raw_frames: List[Tuple[float, List[OcrFrameDetection]]] = []

        with av.open(video_path) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            tb = float(stream.time_base)
            total_dur = float(stream.duration * tb) if stream.duration else 60.0
            if total_dur <= 0.1:
                total_dur = 60.0

            next_sample_t = 0.0
            last_pct = -1

            for frame in container.decode(video=0):
                if cancel_cb and cancel_cb():
                    raise InterruptedError("OCR scan cancelled by user")

                if frame.pts is None:
                    continue
                t = float(frame.pts * tb)
                if t + 1e-4 < next_sample_t:
                    continue
                next_sample_t = t + step_sec

                arr = frame.to_ndarray(format="rgb24")
                fh, fw = arr.shape[:2]
                x1, y1, x2, y2 = self.get_roi_bounds(fw, fh, settings, custom_roi)
                crop = arr[y1:y2, x1:x2]

                dets = self._run_ocr_on_crop(crop)
                frame_dets: List[OcrFrameDetection] = []
                for txt, conf, cx, cy in dets:
                    clean = _clean_ocr_chinese_text(txt, self._opencc)
                    core_len = len(_strip_punct_and_spaces(clean))
                    if conf >= min_conf and core_len >= min_chars and _has_cjk(clean):
                        frame_dets.append(
                            OcrFrameDetection(
                                timestamp=round(t, 3),
                                text=clean,
                                confidence=conf,
                                box_cx=cx,
                                box_cy=cy,
                            )
                        )
                raw_frames.append((round(t, 3), frame_dets))

                pct = min(90, int((t / total_dur) * 90))
                if progress_cb and pct != last_pct:
                    last_pct = pct
                    progress_cb(pct, f"កំពុងស្កេនអក្សរចិនក្នុងវីដេអូ (OCR {scan_fps:.0f}fps): {t:.1f}s / {total_dur:.1f}s...")

        if progress_cb:
            progress_cb(92, "កំពុងច្រោះ watermark និងតម្រឹមឃ្លា (Filtering & Merging)...")

        # Filter static watermarks/logos appearing in the same position across many frames
        filtered_frames, wm_count = self._filter_static_watermarks(raw_frames)

        # Merge consecutive frames with similar text (Levenshtein >= 0.85)
        segments = self._merge_frame_detections(
            filtered_frames,
            step_sec=step_sec,
            min_duration=min_duration,
        )

        # Snap to VAD if requested or if VAD segments are available
        if vad_segments:
            if not use_ocr_timestamps:
                self._snap_segments_to_vad(segments, vad_segments)

        # Combine & compare against Whisper if Whisper segments exist
        report = OcrScanReport(
            total_segments=len(segments),
            watermarks_filtered=wm_count,
            engine_used=self.engine_name,
        )
        if vad_segments:
            self.compare_with_whisper(segments, vad_segments, report)
        else:
            report.low_conf_count = sum(1 for s in segments if s.ocr_confidence < 0.80)

        if progress_cb:
            progress_cb(100, f"ស្កេន OCR រួចរាល់ ({len(segments)} ឃ្លា)!")

        return segments, report

    @staticmethod
    def _filter_static_watermarks(
        raw_frames: List[Tuple[float, List[OcrFrameDetection]]]
    ) -> Tuple[List[Tuple[float, str, float]], int]:
        """
        Detect and remove static watermarks/logos (identical short text at fixed bbox position
        across >= 35% of active frames or spanning > 40% of video frames).
        Returns ([(timestamp, combined_text, avg_conf)], watermarks_filtered_count).
        """
        total_frames = max(1, len(raw_frames))
        pos_counts: Dict[Tuple[str, int, int], int] = {}

        for _, dets in raw_frames:
            seen_in_frame = set()
            for d in dets:
                core = _strip_punct_and_spaces(d.text)
                # Quantize position into 10x10 grid
                key = (core, int(d.box_cx * 10), int(d.box_cy * 10))
                if key not in seen_in_frame:
                    seen_in_frame.add(key)
                    pos_counts[key] = pos_counts.get(key, 0) + 1

        watermark_keys = set()
        for key, cnt in pos_counts.items():
            core, _, _ = key
            # Static watermark/logo: appears in >= 35% of all sampled frames and is short (<= 6 chars)
            if cnt >= max(6, int(total_frames * 0.35)) and len(core) <= 6:
                watermark_keys.add(key)

        cleaned_frames: List[Tuple[float, str, float]] = []
        wm_hits = 0
        for t, dets in raw_frames:
            valid_dets = []
            for d in dets:
                core = _strip_punct_and_spaces(d.text)
                key = (core, int(d.box_cx * 10), int(d.box_cy * 10))
                if key in watermark_keys:
                    wm_hits += 1
                    continue
                valid_dets.append(d)

            if valid_dets:
                # Sort top-to-bottom then left-to-right
                valid_dets.sort(key=lambda x: (round(x.box_cy, 1), x.box_cx))
                joined = "".join(d.text for d in valid_dets) if len(valid_dets) > 1 and all(_has_cjk(d.text) for d in valid_dets) else " ".join(d.text for d in valid_dets)
                avg_conf = sum(d.confidence for d in valid_dets) / len(valid_dets)
                cleaned_frames.append((t, joined.strip(), avg_conf))
            else:
                cleaned_frames.append((t, "", 0.0))

        return cleaned_frames, len(watermark_keys)

    @staticmethod
    def _merge_frame_detections(
        frames: List[Tuple[float, str, float]],
        step_sec: float,
        min_duration: float = 0.3,
    ) -> List[Segment]:
        """
        Group consecutive frames where text similarity (Levenshtein) >= 0.85
        into continuous Segments, picking the highest-confidence representative text.
        """
        groups: List[Dict] = []
        current: Optional[Dict] = None

        for t, txt, conf in frames:
            if not txt:
                if current is not None:
                    # Allow 1 missed frame (up to 1.5 * step_sec) before closing
                    if t - current["last_t"] > step_sec * 1.6:
                        groups.append(current)
                        current = None
                continue

            if current is None:
                current = {
                    "start": t,
                    "last_t": t,
                    "texts": [(txt, conf)],
                }
            else:
                rep = current["texts"][0][0]
                sim = levenshtein_similarity(_strip_punct_and_spaces(rep), _strip_punct_and_spaces(txt))
                if sim >= 0.85 and (t - current["last_t"]) <= step_sec * 2.2:
                    current["last_t"] = t
                    current["texts"].append((txt, conf))
                else:
                    groups.append(current)
                    current = {
                        "start": t,
                        "last_t": t,
                        "texts": [(txt, conf)],
                    }

        if current is not None:
            groups.append(current)

        # Merge adjacent groups if very close and still similar (>= 0.85)
        merged_groups: List[Dict] = []
        for g in groups:
            if merged_groups:
                prev = merged_groups[-1]
                prev_best = max(prev["texts"], key=lambda x: (len(_strip_punct_and_spaces(x[0])), x[1]))[0]
                curr_best = max(g["texts"], key=lambda x: (len(_strip_punct_and_spaces(x[0])), x[1]))[0]
                if (g["start"] - prev["last_t"]) <= 0.45 and levenshtein_similarity(
                    _strip_punct_and_spaces(prev_best), _strip_punct_and_spaces(curr_best)
                ) >= 0.85:
                    prev["last_t"] = g["last_t"]
                    prev["texts"].extend(g["texts"])
                    continue
            merged_groups.append(g)

        segments: List[Segment] = []
        seg_id = 1
        for g in merged_groups:
            start_t = round(g["start"], 3)
            end_t = round(g["last_t"] + step_sec, 3)
            if (end_t - start_t) < min_duration:
                continue

            # Vote for best text: prefer most frequent clean text, tie-break by confidence
            freq: Dict[str, List[float]] = {}
            for txt, conf in g["texts"]:
                freq.setdefault(txt, []).append(conf)
            best_txt = max(freq.keys(), key=lambda k: (len(freq[k]), sum(freq[k]) / len(freq[k])))
            avg_conf = round(sum(freq[best_txt]) / len(freq[best_txt]), 3)

            seg = Segment(
                id=seg_id,
                start=start_t,
                end=end_t,
                text_src=best_txt,
                src_source="OCR",
                ocr_confidence=avg_conf,
                needs_review=(avg_conf < 0.75),
            )
            segments.append(seg)
            seg_id += 1

        return segments

    @staticmethod
    def _snap_segments_to_vad(ocr_segments: List[Segment], vad_segments: List[Segment], max_snap_sec: float = 0.5):
        """Snap OCR start/end timestamps to overlapping VAD/Whisper speech boundaries."""
        for o_seg in ocr_segments:
            overlapping = [
                v for v in vad_segments
                if min(o_seg.end, v.end) - max(o_seg.start, v.start) > 0.05
            ]
            if not overlapping:
                continue
            v_start = min(v.start for v in overlapping)
            v_end = max(v.end for v in overlapping)
            if abs(o_seg.start - v_start) <= max_snap_sec:
                o_seg.start = round(v_start, 3)
            if abs(o_seg.end - v_end) <= max_snap_sec:
                o_seg.end = round(v_end, 3)

    @staticmethod
    def compare_with_whisper(
        ocr_segments: List[Segment],
        whisper_segments: List[Segment],
        report: OcrScanReport,
    ) -> None:
        """
        Compare OCR ground-truth against Whisper transcription.
        Flags rows where CER > 30% for user review, and counts low-confidence rows.
        """
        low_conf = 0
        mismatches = 0
        for o_seg in ocr_segments:
            if o_seg.ocr_confidence < 0.80:
                low_conf += 1

            overlapping = [
                w for w in whisper_segments
                if min(o_seg.end, w.end) - max(o_seg.start, w.start) > 0.1
            ]
            if not overlapping:
                continue
            whisper_txt = "".join(w.text_src for w in overlapping if w.text_src)
            cer = compute_cer(o_seg.text_src, whisper_txt)
            o_seg.whisper_cer = round(cer, 3)
            if cer > 0.30:
                o_seg.needs_review = True
                mismatches += 1
                report.details.append(
                    f"#{o_seg.id}: OCR='{o_seg.text_src}' vs Whisper='{whisper_txt}' (CER={cer*100:.0f}%)"
                )

        report.low_conf_count = low_conf
        report.whisper_mismatch_count = mismatches
