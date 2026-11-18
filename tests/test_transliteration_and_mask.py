import unittest
import os
import tempfile
from app.models import Segment, ProjectSettings, GlossaryItem
from app.dubbing.transliteration import transliterate_khmer_dialogue
from app.dubbing.subtitle_renderer import SubtitleRenderer


class TestTransliterationAndSubtitleMask(unittest.TestCase):

    def test_transliterate_latin_names(self):
        # 1. Gang Hong and Baoshou
        text1 = "តើ Baoshou និង Gang Hong ទៅណាហើយ?"
        fixed1 = transliterate_khmer_dialogue(text1)
        self.assertIn("ប៉ោសូវ", fixed1)
        self.assertIn("គាំងហុង", fixed1)
        self.assertNotIn("Baoshou", fixed1)
        self.assertNotIn("Gang Hong", fixed1)

        # 2. Case insensitive & spacing
        text2 = "baoshou និយាយជាមួយ ganghong"
        fixed2 = transliterate_khmer_dialogue(text2)
        self.assertIn("ប៉ោសូវ", fixed2)
        self.assertIn("គាំងហុង", fixed2)

        # 3. Su Bao & Chaohu Road
        text3 = "Su Bao ចង់ញ៉ាំ Chaohu Road"
        fixed3 = transliterate_khmer_dialogue(text3)
        self.assertIn("ស៊ូប៉ាវ", fixed3)
        self.assertIn("ស្ករផ្លែឈើ", fixed3)
        self.assertNotIn("Su Bao", fixed3)

    def test_subtitle_renderer_mask_filter(self):
        vf = SubtitleRenderer.build_video_filter(
            srt_path="temp.srt",
            mask_original_subs=True,
            font_name="Noto Sans Khmer",
            font_size=22,
            mask_y_pct=82.5,
            mask_h_pct=13.0,
            mask_w_pct=53.0,
            mask_x_pct=23.5,
            mask_opacity=0.95,
            mask_color="black",
            subtitle_margin_v=30
        )
        self.assertIn("drawbox=", vf)
        self.assertIn("y=ih*0.825", vf)
        self.assertIn("h=ih*0.130", vf)
        self.assertIn("w=iw*0.530", vf)
        self.assertIn("x=iw*0.235", vf)
        self.assertIn("color=black@0.95:t=fill", vf)
        self.assertIn("MarginV=30", vf)
        self.assertIn("Fontsize=22", vf)
        self.assertIn("PlayResX=1280", vf)
        self.assertIn("PlayResY=720", vf)
        self.assertIn("MarginL=", vf)
        self.assertIn("MarginR=", vf)

    def test_subtitle_renderer_apply_glossary_transliteration(self):
        raw = "តើ Gang Hong និង Baoshou ទៅណា?"
        res = SubtitleRenderer.apply_glossary(raw)
        self.assertIn("គាំងហុង", res)
        self.assertIn("ប៉ោសូវ", res)
        self.assertNotIn("Gang Hong", res)
        self.assertNotIn("Baoshou", res)

    def test_project_settings_mask_defaults(self):
        s = ProjectSettings()
        self.assertTrue(s.mask_subtitles)
        self.assertAlmostEqual(s.mask_y_percent, 83.0)
        self.assertAlmostEqual(s.mask_height_percent, 12.0)
        self.assertAlmostEqual(s.mask_opacity, 0.95)
        self.assertEqual(s.subtitle_font_size, 20)

        # Test round-trip dict serialization
        d = s.to_dict()
        s2 = ProjectSettings.from_dict(d)
        self.assertEqual(s2.mask_y_percent, 83.0)
        self.assertEqual(s2.mask_opacity, 0.95)


if __name__ == "__main__":
    unittest.main()
