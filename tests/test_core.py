"""
Comprehensive Core Tests for AI Movie Translator & Auto Dubbing
Validates all SRS Requirements and Time-Sync Cascade Rules
"""

import unittest
import os
import shutil
from pathlib import Path

from app.models import Segment, Project, Speaker, GlossaryItem, ProjectSettings
from app.database import DatabaseManager
from app.utils import format_timestamp, parse_timestamp, generate_srt, parse_srt
from app.sync_engine import SyncEngine, SyncReport
from app.tts_engine import TTSEngine
from app.translation_engine import TranslationEngine

class TestCoreModules(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("d:/summray_mo/temp_test")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db = DatabaseManager(db_path=self.test_dir / "test.db")

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_segment_data_model(self):
        """Test Segment data model matching SRS Section 7"""
        seg = Segment(
            id=12,
            start=63.40,
            end=66.85,
            speaker="S1",
            text_src="Where are you going?",
            text_km="តើអ្នកកំពុងទៅណា?",
            voice="km-KH-PisethNeural",
            tts_file="seg_012.wav",
            tts_duration=2.90,
            speed=1.0
        )
        self.assertEqual(seg.slot, 3.45)
        self.assertEqual(seg.effective_duration, 2.90)
        self.assertFalse(seg.exceeds_slot)

        d = seg.to_dict()
        seg_restored = Segment.from_dict(d)
        self.assertEqual(seg_restored.id, 12)
        self.assertEqual(seg_restored.slot, 3.45)

    def test_database_persistence(self):
        """Test F1: Project management (JSON / SQLite) & Auto-save"""
        proj = Project(name="Test Movie", video_path="test.mp4")
        proj.segments.append(Segment(id=1, start=0.0, end=3.0, text_src="Hello", text_km="សួស្តី"))
        proj.speakers["S1"] = Speaker(id="S1", name="Actor 1", voice="km-KH-PisethNeural")
        proj.glossary.append(GlossaryItem(src_term="Stark", km_term="ស្តាក"))

        save_path = str(self.test_dir / "movie_project.aidub")
        success = self.db.save_project_to_file(proj, save_path)
        self.assertTrue(success)

        loaded = self.db.load_project_from_file(save_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.segments), 1)
        self.assertEqual(loaded.segments[0].text_km, "សួស្តី")
        self.assertEqual(loaded.speakers["S1"].name, "Actor 1")

    def test_srt_parsing_and_generation(self):
        """Test SRT generation and parsing (F10)"""
        segs = [
            Segment(id=1, start=1.5, end=4.2, text_src="Good morning", text_km="អរុណសួស្តី"),
            Segment(id=2, start=5.0, end=8.0, text_src="How are you?", text_km="សុខសប្បាយទេ?"),
        ]
        srt_text = generate_srt(segs, language="km")
        self.assertIn("00:00:01,500 --> 00:00:04,200", srt_text)
        self.assertIn("អរុណសួស្តី", srt_text)

        parsed_segs = parse_srt(srt_text)
        self.assertEqual(len(parsed_segs), 2)
        self.assertAlmostEqual(parsed_segs[0].start, 1.5, places=1)

    def test_time_sync_cascade(self):
        """Test SRS Section 4: 4-Stage Time-sync Rules"""
        # Case 1: Fits naturally (duration <= slot)
        seg1 = Segment(id=1, start=0.0, end=3.0, tts_duration=2.0)
        
        # Case 2: Needs speed adjustment (duration > slot, fits within 1.25x)
        # slot = 2.0s, tts_duration = 2.4s -> 2.4 / 2.0 = 1.20x <= 1.25x
        seg2 = Segment(id=2, start=4.0, end=6.0, tts_duration=2.4)
        
        # Case 3: Needs gap borrowing
        # slot = 1.0s, duration = 2.2s, next segment starts at 10.0s (huge gap available)
        seg3 = Segment(id=3, start=7.0, end=8.0, tts_duration=2.2)
        seg4 = Segment(id=4, start=12.0, end=15.0, tts_duration=2.0)

        proj = Project(segments=[seg1, seg2, seg3, seg4])

        # Mock or test sync engine
        sync_engine = SyncEngine()
        report = sync_engine.synchronize_segments(proj)

        self.assertEqual(report.total_segments, 4)
        self.assertEqual(seg1.sync_status, "OK")
        self.assertIn(seg2.sync_status, ["SPEED_ADJUSTED", "OK"])
        self.assertIn(seg3.sync_status, ["GAP_BORROWED", "SPEED_ADJUSTED", "TIME_STRETCHED", "OVERFLOW"])

    def test_glossary_application(self):
        """Test F4: Glossary replacement"""
        engine = TranslationEngine()
        glossary = [
            GlossaryItem(src_term="John", km_term="ចន"),
            GlossaryItem(src_term="Commander", km_term="លោកមេបញ្ជាការ")
        ]
        text = "Hello Commander, have you seen John?"
        res = engine.apply_glossary(text, glossary)
        self.assertIn("លោកមេបញ្ជាការ", res)
        self.assertIn("ចន", res)

    def test_logo_settings_and_result_video(self):
        """Test logo watermark settings serialization and result video properties"""
        settings = ProjectSettings(
            logo_enabled=True,
            logo_path="C:/path/to/logo.png",
            logo_position="bottom_right",
            logo_width=150,
            logo_opacity=0.85,
            logo_margin_x=30,
            logo_margin_y=25,
            result_video_path="C:/path/to/result.mp4"
        )
        d = settings.to_dict()
        self.assertTrue(d["logo_enabled"])
        self.assertEqual(d["logo_position"], "bottom_right")
        self.assertEqual(d["logo_width"], 150)
        self.assertEqual(d["result_video_path"], "C:/path/to/result.mp4")

        restored = ProjectSettings.from_dict(d)
        self.assertEqual(restored.logo_position, "bottom_right")
        self.assertEqual(restored.logo_width, 150)
        self.assertEqual(restored.logo_opacity, 0.85)
        self.assertEqual(restored.result_video_path, "C:/path/to/result.mp4")

    def test_fast_preview_validation(self):
        """Test pre-export fast preview validation checks"""
        from app.export_engine import ExportEngine
        engine = ExportEngine()
        proj = Project(video_path="")
        with self.assertRaises(FileNotFoundError):
            engine.generate_fast_preview(proj)

        proj.video_path = str(self.test_dir / "nonexistent.mp4")
        with self.assertRaises(FileNotFoundError):
            engine.generate_fast_preview(proj)

    def test_emotional_prosody_and_humanize(self):
        """Test dialogue humanization and emotional prosody detection"""
        tr_engine = TranslationEngine()
        humanized_q = tr_engine.humanize_khmer_dialogue("តើអ្នកកំពុងទៅណា?", "Where are you going?")
        self.assertTrue(any(cand in humanized_q for cand in ["ឯងទៅណាហ្នឹង?", "ឯងគិតទៅណាហ្នឹង?"]))
        self.assertTrue(humanized_q.endswith("?"))

        humanized_ex = tr_engine.humanize_khmer_dialogue("មិនអាចទៅរួចទេ", "No way!")
        self.assertTrue(humanized_ex.endswith("!"))

        tts_engine = TTSEngine(output_dir=self.test_dir / "tts_out")
        seg_q = Segment(id=1, start=0.0, end=2.0, text_km="ឯងទៅណាហ្នឹង?", text_src="Where are you going?")
        text_out, rate_str, pitch_str = tts_engine._prepare_emotional_prosody(seg_q, 1.0, 0)[:3]
        self.assertTrue(text_out.strip().endswith("?"))
        self.assertTrue("+8Hz" in pitch_str or "+2Hz" in pitch_str) # Inquisitive inflection

    def test_speech_and_subtitle_synchronization(self):
        """Test exact timing synchronization between spoken audio and subtitles"""
        from app.audio_engine import AudioEngine
        ae = AudioEngine()
        seg1 = Segment(id=1, start=1.0, end=2.5, tts_duration=2.2, text_km="សួស្តីបងប្អូន")
        seg2 = Segment(id=2, start=3.0, end=4.5, tts_duration=1.8, text_km="តើអ្នកសុខសប្បាយទេ?")
        proj = Project(segments=[seg1, seg2])

        ae.align_speech_and_subtitles(proj.segments)

        # Verify speech timestamps match exact audio (locked to character slot at ~2.56s)
        self.assertAlmostEqual(seg1.speech_start, 1.0, places=2)
        self.assertAlmostEqual(seg1.speech_end, 2.56, places=1)
        self.assertAlmostEqual(seg2.speech_start, 3.0, places=1)

        # Verify SRT generation matches speech timestamps
        srt_text = generate_srt(proj.segments, language="km")
        self.assertIn("00:00:01,000", srt_text) # Starts with voice
        self.assertIn("សួស្តីបងប្អូន", srt_text)

    def test_custom_and_global_voice_speed(self):
        """Test global and per-segment speed customization"""
        settings = ProjectSettings(ai_voice_speed=1.20)
        data = settings.to_dict()
        self.assertEqual(data["ai_voice_speed"], 1.20)
        loaded = ProjectSettings.from_dict(data)
        self.assertEqual(loaded.ai_voice_speed, 1.20)

        # Test per-segment speed and duration scaling
        seg = Segment(id=1, start=0.0, end=3.0, tts_duration=3.0, speed=1.25)
        self.assertAlmostEqual(seg.effective_duration, 2.4, places=2)

        # Test TTS engine rate string calculation for faster movie speed
        tts_engine = TTSEngine(output_dir=self.test_dir / "tts_speed")
        prosody = tts_engine._prepare_emotional_prosody(seg, base_speed=1.20, base_pitch_hz=0)
        rate_str = prosody[1]
        self.assertTrue("+25%" in rate_str or "+26%" in rate_str)

    def test_version_and_updater_system(self):
        """Test semantic version comparison, manifest parsing, and updater settings"""
        from app.updater import is_newer_version, parse_ver, UpdateInfo
        from app.config import APP_VERSION

        # Test version comparisons
        self.assertTrue(is_newer_version("1.1.0", "1.0.0"))
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertTrue(is_newer_version("v2.0.0", "1.9.9"))
        self.assertTrue(is_newer_version("1.0.10", "1.0.9"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("0.9.9", "1.0.0"))
        self.assertFalse(is_newer_version("1.0.0", "1.1.0"))

        # Test ProjectSettings update fields
        settings = ProjectSettings(auto_check_updates=True, update_check_url="https://example.com/v.json")
        d = settings.to_dict()
        self.assertTrue(d["auto_check_updates"])
        self.assertEqual(d["update_check_url"], "https://example.com/v.json")
        restored = ProjectSettings.from_dict(d)
        self.assertTrue(restored.auto_check_updates)

        # Test local version.json manifest parsing
        v_json_path = Path("d:/summray_mo/version.json")
        self.assertTrue(v_json_path.exists())
        import json
        with open(v_json_path, "r", encoding="utf-8") as f:
            v_data = json.load(f)
        self.assertIn("version", v_data)
        self.assertIn("download_url", v_data)
        self.assertIn("changelog_km", v_data)

if __name__ == "__main__":
    unittest.main()

