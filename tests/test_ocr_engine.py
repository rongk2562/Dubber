import unittest

from app.models import Segment
from app.dubbing.ocr_engine import (
    SubtitleOcrEngine,
    OcrFrameDetection,
    OcrScanReport,
    levenshtein_similarity,
    compute_cer,
    _clean_ocr_chinese_text,
    _get_opencc_t2s,
)


class TestOcrEngine(unittest.TestCase):

    def test_opencc_t2s_and_cjk_spacing(self):
        conv = _get_opencc_t2s()
        cleaned = _clean_ocr_chinese_text("剛 哄 迷 糊 豎 讓 自 己", conv)
        self.assertEqual(cleaned, "刚哄迷糊竖让自己")

    def test_static_watermark_filtered_out(self):
        # Simulate 20 frames where "福" sits at fixed (0.1, 0.1) in every frame,
        # while subtitle phrases appear at (0.5, 0.8) across subsets of frames.
        raw_frames = []
        for i in range(20):
            t = i * 0.2
            dets = [OcrFrameDetection(timestamp=t, text="福", confidence=0.95, box_cx=0.12, box_cy=0.12)]
            if 2 <= i <= 6:
                dets.append(OcrFrameDetection(timestamp=t, text="刚哄迷糊", confidence=0.92, box_cx=0.5, box_cy=0.8))
            if 10 <= i <= 16:
                dets.append(OcrFrameDetection(timestamp=t, text="竖着放平让他自己睡", confidence=0.91, box_cx=0.5, box_cy=0.8))
            raw_frames.append((t, dets))

        filtered, wm_count = SubtitleOcrEngine._filter_static_watermarks(raw_frames)
        self.assertGreaterEqual(wm_count, 1)
        all_texts = " ".join(txt for _, txt, _ in filtered if txt)
        self.assertNotIn("福", all_texts)
        self.assertIn("刚哄迷糊", all_texts)
        self.assertIn("竖着放平让他自己睡", all_texts)

    def test_merge_consecutive_similar_frames_and_drop_short(self):
        frames = [
            (0.0, "刚哄迷糊", 0.90),
            (0.2, "刚哄迷糊", 0.95),
            (0.4, "刚哄迷糊", 0.92),
            (1.0, "太短", 0.90),        # single 0.2s frame < 0.3s -> dropped
            (2.0, "好家伙", 0.88),
            (2.2, "好家伙", 0.94),
        ]
        segs = SubtitleOcrEngine._merge_frame_detections(frames, step_sec=0.2, min_duration=0.3)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].text_src, "刚哄迷糊")
        self.assertEqual(segs[0].src_source, "OCR")
        self.assertEqual(segs[1].text_src, "好家伙")

    def test_whisper_comparison_flags_high_cer(self):
        ocr_segs = [
            Segment(id=1, start=1.0, end=2.5, text_src="刚哄迷糊", src_source="OCR", ocr_confidence=0.95),
            Segment(id=2, start=3.0, end=5.0, text_src="晚上咱拿抱枕当替身", src_source="OCR", ocr_confidence=0.92),
        ]
        whisper_segs = [
            Segment(id=1, start=1.0, end=2.5, text_src="刚红迷护"),          # CER = 2/4 = 50% > 30%
            Segment(id=2, start=3.0, end=5.0, text_src="晚上咱拿抱枕当提升"), # CER = 2/9 = 22% <= 30%
        ]
        report = OcrScanReport(total_segments=2)
        SubtitleOcrEngine.compare_with_whisper(ocr_segs, whisper_segs, report)
        self.assertTrue(ocr_segs[0].needs_review)
        self.assertFalse(ocr_segs[1].needs_review)
        self.assertEqual(report.whisper_mismatch_count, 1)
        self.assertGreater(compute_cer("刚哄迷糊", "刚红迷护"), 0.30)
        self.assertGreaterEqual(levenshtein_similarity("刚哄迷糊", "刚哄迷糊"), 0.85)


if __name__ == "__main__":
    unittest.main()
