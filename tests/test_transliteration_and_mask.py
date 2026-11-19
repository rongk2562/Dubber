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
        s.mask_style = "blur"
        s.mask_blur_strength = 55
        s.mask_blur_dim = 30
        s.mask_feather = True
        d = s.to_dict()
        s2 = ProjectSettings.from_dict(d)
        self.assertEqual(s2.mask_y_percent, 83.0)
        self.assertEqual(s2.mask_opacity, 0.95)
        self.assertEqual(s2.mask_style, "blur")
        self.assertEqual(s2.mask_blur_strength, 55)
        self.assertEqual(s2.mask_blur_dim, 30)
        self.assertTrue(s2.mask_feather)

    def test_blur_filter_even_dimensions_and_complex_flag(self):
        for vw, vh in [(1280, 720), (720, 1280)]:
            x, y, w, h = SubtitleRenderer.compute_mask_pixel_rect(vw, vh, 23.3, 84.3, 53.3, 12.4)
            self.assertEqual(w % 2, 0)
            self.assertEqual(h % 2, 0)
            self.assertGreaterEqual(w, 4)
            self.assertGreaterEqual(h, 4)
            vf = SubtitleRenderer.build_video_filter(
                srt_path="temp.srt",
                mask_original_subs=True,
                mask_style="blur",
                blur_strength=40,
                blur_dim=25,
                mask_feather=True,
                video_w=vw,
                video_h=vh,
            )
            self.assertTrue(SubtitleRenderer.is_complex_filter(vf))
            self.assertIn("gblur=sigma=", vf)
            self.assertIn("eq=brightness=-0.125", vf)
            self.assertIn("geq=", vf)

    def test_auto_center_subtitle_matches_mask_vertical_center(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.subtitle_mask_dialog import SubtitleMaskDialog
        _app = QApplication.instance() or QApplication([])
        st = ProjectSettings(
            mask_subtitles=True,
            mask_y_percent=64.9,
            mask_height_percent=9.4,
            mask_width_percent=78.0,
            mask_x_percent=16.6,
            subtitle_font_size=20,
            subtitle_margin_v=194,
        )
        dlg = SubtitleMaskDialog(settings=st, video_path="", lang="km")
        dlg._on_center_btn_clicked()
        # Mask vertical center from bottom in 720p = (100 - 69.6)% * 720 = 218.9; minus ~0.45*20 = 210
        self.assertGreaterEqual(dlg.slider_mb.value(), 207)
        self.assertLessEqual(dlg.slider_mb.value(), 213)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
