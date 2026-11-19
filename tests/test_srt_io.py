import os
import tempfile
import unittest
from pathlib import Path

from app.models import Segment, Project, GlossaryItem
from app.dubbing.srt_io import (
    parse_srt_content,
    parse_srt_file,
    normalize_unicode_and_zwsp,
    match_srt_to_segments,
    apply_srt_diff,
    restore_import_snapshot,
    export_chinese_srt_for_chatgpt,
    build_chatgpt_prompt,
    STATUS_OK,
    STATUS_REVIEW,
    STATUS_TOO_LONG,
    STATUS_LOCKED,
    STATUS_EMPTY,
)


class TestSrtIO(unittest.TestCase):

    def test_parser_bom_crlf_tags_multiline_and_dots(self):
        raw_text = (
            "\ufeff1\r\n"
            "00:00:01.200 --> 00:00:03.500\r\n"
            "{\\an8}<i>សួស្ដី</i> អ្នកទាំងអស់គ្នា\r\n"
            "សុខសប្បាយទេ?\r\n"
            "\r\n\r\n"
            "1\r\n"  # duplicate index
            "00:00:04,000 --> 00:00:06,250\r\n"
            "<b>ខ្ញុំសុខសប្បាយទេ!</b>\r\n"
            "\r\n"
            "BAD_BLOCK_NO_TIMESTAMP\r\n"
            "just random text\r\n"
        )
        res = parse_srt_content(raw_text)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.entries), 2)
        self.assertEqual(len(res.warnings), 1)
        self.assertEqual(res.entries[0].start, 1.2)
        self.assertEqual(res.entries[0].end, 3.5)
        self.assertEqual(res.entries[0].text, "សួស្ដី អ្នកទាំងអស់គ្នា សុខសប្បាយទេ?")
        self.assertEqual(res.entries[1].text, "ខ្ញុំសុខសប្បាយទេ!")

    def test_parser_corrupted_file_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "broken.srt"
            p.write_text("This is not an SRT file at all!\nNo timestamps here.", encoding="utf-8")
            res = parse_srt_file(str(p))
            self.assertFalse(res.is_valid)
            self.assertTrue(len(res.errors) > 0)
            self.assertEqual(len(res.entries), 0)

    def test_parser_utf8_bom_and_utf16_files(self):
        srt_body = "1\n00:00:01,000 --> 00:00:03,000\nតើអ្នកទៅណា?\n\n"
        with tempfile.TemporaryDirectory() as tmp:
            p_bom = Path(tmp) / "bom.srt"
            p_bom.write_bytes(b"\xef\xbb\xbf" + srt_body.encode("utf-8"))
            res_bom = parse_srt_file(str(p_bom))
            self.assertTrue(res_bom.is_valid)
            self.assertEqual(res_bom.encoding_used, "utf-8-sig")
            self.assertEqual(res_bom.entries[0].text, "តើអ្នកទៅណា?")

            p_u16 = Path(tmp) / "u16.srt"
            p_u16.write_bytes(srt_body.encode("utf-16"))
            res_u16 = parse_srt_file(str(p_u16))
            self.assertTrue(res_u16.is_valid)
            self.assertEqual(res_u16.entries[0].text, "តើអ្នកទៅណា?")

    def test_zwsp_and_unicode_nfc_normalize(self):
        # Repeated ZWSP kept as single for subtitles, stripped completely for TTS
        txt = "សួស្ដី\u200b\u200b\u200bមិត្តភក្តិ"
        sub_txt = normalize_unicode_and_zwsp(txt, keep_zwsp=True)
        tts_txt = normalize_unicode_and_zwsp(txt, keep_zwsp=False)
        self.assertEqual(sub_txt, "សួស្ដី\u200bមិត្តភក្តិ")
        self.assertEqual(tts_txt, "សួស្ដីមិត្តភក្តិ")

    def test_51_lines_exact_index_match(self):
        segs = [
            Segment(id=i, start=i * 2.0, end=i * 2.0 + 1.8, text_src=f"中文{i}", text_km=f"ចាស់{i}")
            for i in range(1, 52)
        ]
        srt_lines = []
        for i in range(1, 52):
            s_t = i * 2.0
            e_t = s_t + 1.8
            srt_lines.append(
                f"{i}\n00:{int(s_t//60):02d}:{int(s_t%60):02d},{int((s_t%1)*1000):03d} --> "
                f"00:{int(e_t//60):02d}:{int(e_t%60):02d},{int((e_t%1)*1000):03d}\nថ្មី{i}\n"
            )
        parsed = parse_srt_content("\n".join(srt_lines))
        self.assertEqual(len(parsed.entries), 51)

        diffs = match_srt_to_segments(segs, parsed.entries, mode="auto", replace_target="km")
        self.assertEqual(len(diffs), 51)
        for idx, d in enumerate(diffs, start=1):
            self.assertEqual(d.seg_id, idx)
            self.assertEqual(d.new_text, f"ថ្មី{idx}")
            self.assertEqual(d.status, STATUS_OK)

    def test_49_lines_merged_time_overlap_match(self):
        # 51 Chinese segments, 49 Khmer SRT entries (segments 2 & 3 merged into one entry, segments 10 & 11 merged)
        segs = [
            Segment(id=i, start=i * 2.0, end=i * 2.0 + 1.8, text_src=f"中文{i}", text_km=f"ចាស់{i}")
            for i in range(1, 52)
        ]
        srt_entries = []
        i = 1
        srt_idx = 1
        while i <= 51:
            if i in (2, 10):
                # Merged 2 segments into 1 SRT entry
                s_t = i * 2.0
                e_t = (i + 1) * 2.0 + 1.8
                srt_entries.append(
                    f"{srt_idx}\n00:{int(s_t//60):02d}:{int(s_t%60):02d},000 --> "
                    f"00:{int(e_t//60):02d}:{int(e_t%60):02d},800\nរួម{i}_{i+1}\n"
                )
                i += 2
            else:
                s_t = i * 2.0
                e_t = s_t + 1.8
                srt_entries.append(
                    f"{srt_idx}\n00:{int(s_t//60):02d}:{int(s_t%60):02d},000 --> "
                    f"00:{int(e_t//60):02d}:{int(e_t%60):02d},800\nថ្មី{i}\n"
                )
                i += 1
            srt_idx += 1

        parsed = parse_srt_content("\n".join(srt_entries))
        self.assertEqual(len(parsed.entries), 49)

        diffs = match_srt_to_segments(segs, parsed.entries, mode="auto", replace_target="km")
        review_items = [d for d in diffs if d.status == STATUS_REVIEW]
        self.assertEqual(len(review_items), 2)
        self.assertEqual(review_items[0].new_text, "រួម2_3")
        self.assertEqual(review_items[1].new_text, "រួម10_11")

    def test_split_entries_concatenation_and_review_flag(self):
        segs = [Segment(id=1, start=0.0, end=4.0, text_src="一句话", text_km="ចាស់")]
        srt = (
            "1\n00:00:00,000 --> 00:00:02,000\nផ្នែកទី១\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nផ្នែកទី២\n"
        )
        parsed = parse_srt_content(srt)
        diffs = match_srt_to_segments(segs, parsed.entries, mode="time", replace_target="km")
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].status, STATUS_REVIEW)
        self.assertEqual(diffs[0].new_text, "ផ្នែកទី១ ផ្នែកទី២")

    def test_apply_diff_and_undo_restore(self):
        proj = Project(
            name="test_proj",
            segments=[
                Segment(id=1, start=0.0, end=2.0, text_src="你好", text_km="សួស្ដីចាស់", tts_file="old1.wav", tts_duration=1.2),
                Segment(id=2, start=2.5, end=4.5, text_src="再见", text_km="លាហើយចាស់", tts_file="old2.wav", tts_duration=1.3),
            ]
        )
        parsed = parse_srt_content(
            "1\n00:00:00,000 --> 00:00:02,000\nសួស្ដីថ្មី\n\n"
            "2\n00:00:02,500 --> 00:00:04,500\nលាហើយថ្មី\n"
        )
        diffs = match_srt_to_segments(proj.segments, parsed.entries, mode="index")
        with tempfile.TemporaryDirectory() as tmp:
            snap, changed, backup_path = apply_srt_diff(
                proj, diffs, replace_target="km", use_srt_times=False, clear_changed_tts=True, backup_dir=Path(tmp)
            )
            self.assertEqual(changed, 2)
            self.assertTrue(Path(backup_path).exists())
            self.assertEqual(proj.segments[0].text_km, "សួស្ដីថ្មី")
            self.assertEqual(proj.segments[0].translation_source, "imported")
            self.assertEqual(proj.segments[0].tts_file, "")
            self.assertTrue(proj.segments[0].tts_outdated)

            # Undo restores text and original TTS file paths!
            restore_import_snapshot(proj, snap)
            self.assertEqual(proj.segments[0].text_km, "សួស្ដីចាស់")
            self.assertEqual(proj.segments[0].tts_file, "old1.wav")
            self.assertEqual(proj.segments[1].tts_file, "old2.wav")

    def test_locked_and_too_long_flags(self):
        segs = [
            Segment(id=1, start=0.0, end=1.0, text_src="快", text_km="ចាស់", is_locked=True),
            Segment(id=2, start=1.0, end=2.0, text_src="长", text_km=""),
        ]
        srt = (
            "1\n00:00:00,000 --> 00:00:01,000\nថ្មី\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nថ្ងៃនេះខ្ញុំត្រូវទៅផ្សារទិញបន្លែត្រីសាច់ជាមួយមិត្តភក្តិរបស់ខ្ញុំ\n"
        )
        parsed = parse_srt_content(srt)
        diffs = match_srt_to_segments(segs, parsed.entries, mode="index")
        self.assertEqual(diffs[0].status, STATUS_LOCKED)
        self.assertEqual(diffs[1].status, STATUS_TOO_LONG)

    def test_export_chinese_srt_chunks_and_prompt(self):
        segs = [Segment(id=i, start=i * 1.0, end=i * 1.0 + 0.8, text_src=f"台词{i}") for i in range(1, 105)]
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "drama_zh.srt"
            files = export_chinese_srt_for_chatgpt(segs, str(out_file), chunk_size=50)
            self.assertEqual(len(files), 3)  # 50 + 50 + 4
            self.assertTrue(Path(files[0]).exists())

        prompt = build_chatgpt_prompt([GlossaryItem(src_term="小明", km_term="សៀវមីង")], summary="រឿងគ្រួសារ")
        self.assertIn("小明=សៀវមីង", prompt)
        self.assertIn("រឿងគ្រួសារ", prompt)
        self.assertIn("≈ ១ វិនាទី = ៦ ព្យាង្គ", prompt)


if __name__ == "__main__":
    unittest.main()
