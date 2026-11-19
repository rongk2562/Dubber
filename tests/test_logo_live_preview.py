import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor

from app.database import DatabaseManager
from app.dubbing.subtitle_renderer import SubtitleRenderer, logo_geometry
from app.ui.theme import THEME_QSS
from app.ui.main_window import MainWindow


class TestLogoLivePreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(THEME_QSS)
        cls._autosave_patch = patch.object(DatabaseManager, "has_autosave", return_value=False)
        cls._autosave_patch.start()

        # Create a real transparent PNG logo (200x100 with alpha=0 corners & alpha=255 center)
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.logo_png = str(Path(cls.tmp_dir.name) / "test_alpha_logo.png")
        img = QImage(200, 100, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        for y in range(20, 80):
            for x in range(20, 180):
                img.setPixelColor(x, y, QColor(45, 140, 255, 255))
        img.save(cls.logo_png, "PNG")

    @classmethod
    def tearDownClass(cls):
        cls._autosave_patch.stop()
        cls.tmp_dir.cleanup()

    def test_logo_geometry_parity_16_9_and_9_16_with_ffmpeg_filter(self):
        for vw, vh in [(1280, 720), (720, 1280)]:
            for pos in ("top_left", "top_right", "bottom_left", "bottom_right", "center"):
                x, y, w, h = logo_geometry(vw, vh, 200, 100, pos=pos, width_pct=15.0, margin_pct=3.0)
                vf = SubtitleRenderer.build_logo_only_filter(
                    video_w=vw,
                    video_h=vh,
                    logo_path=self.logo_png,
                    logo_pos=pos,
                    logo_width_pct=15.0,
                    logo_margin_pct=3.0,
                    logo_opacity=0.80,
                    logo_input_idx=1,
                )
                self.assertIn(f"[1:v]scale={w}:{h},format=rgba,colorchannelmixer=aa=0.80[lg];", vf)
                self.assertIn(f"[0:v][lg]overlay={x}:{y}[v_out]", vf)

                # Bounds & aspect ratio check
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + w, vw)
                self.assertLessEqual(y + h, vh)
                self.assertAlmostEqual(w / h, 2.0, delta=0.1)

    def test_live_preview_slider_transforms_and_png_alpha_in_16_9_and_9_16(self):
        win = MainWindow()
        win.show()
        self.app.processEvents()

        # Load transparent PNG logo
        win.project.settings.logo_path = self.logo_png
        win.chk_logo.setChecked(True)
        self.app.processEvents()

        # Verify QGraphicsPixmapItem preserves alpha channel
        self.assertTrue(win.video_player.logo_item.isVisible())
        self.assertTrue(win.video_player.logo_item.pixmap().hasAlphaChannel())

        # Test 9:16 portrait scene dimensions match FFmpeg filter +/- 0px
        win.video_player._apply_scene_video_size(720, 1280)
        win.slider_logo_size.setValue(20)      # 20% of 720 = 144px
        win.slider_logo_margin.setValue(50)    # 5.0% of min(720, 1280) = 36px
        win.slider_logo_opacity.setValue(65)   # 0.65
        idx_bl = win.combo_logo_pos.findData("bottom_left")
        win.combo_logo_pos.setCurrentIndex(idx_bl)
        self.app.processEvents()

        exp_x, exp_y, exp_w, exp_h = logo_geometry(720, 1280, 200, 100, "bottom_left", 20.0, 5.0)
        item = win.video_player.logo_item
        rendered_w = item.pixmap().width() * item.scale()
        self.assertAlmostEqual(item.pos().x(), exp_x, delta=1.5)
        self.assertAlmostEqual(item.pos().y(), exp_y, delta=1.5)
        self.assertAlmostEqual(rendered_w, exp_w, delta=1.5)
        self.assertAlmostEqual(item.opacity(), 0.65, places=2)

        # Drag snapping to top_right corner shows guides mid-drag & snaps cleanly on release
        vw, vh = 720, 1280
        m = int(round(min(vw, vh) * 0.05))
        target_tr_x = vw - exp_w - m
        target_tr_y = m
        win.video_player._handle_logo_drag(target_tr_x + 4, target_tr_y + 4, finished=False)
        self.assertTrue(win.video_player.snap_vline.isVisible())
        self.assertTrue(win.video_player.snap_hline.isVisible())
        win.video_player._handle_logo_drag(target_tr_x + 4, target_tr_y + 4, finished=True)
        self.assertFalse(win.video_player.snap_vline.isVisible())
        self.assertEqual(win.project.settings.logo_position, "top_right")
        win.close()

    def test_mode_switch_hides_burned_result_overlays_and_marks_stale(self):
        win = MainWindow()
        win.show()
        self.app.processEvents()

        # Simulate having an original video + exported result video
        dummy_orig = str(Path(self.tmp_dir.name) / "orig.mp4")
        dummy_res = str(Path(self.tmp_dir.name) / "res.mp4")
        Path(dummy_orig).write_bytes(b"")
        Path(dummy_res).write_bytes(b"")

        win.video_player.original_video_path = dummy_orig
        win.project.settings.logo_path = self.logo_png
        win.chk_logo.setChecked(True)
        win.video_player.set_result_video(dummy_res)
        self.app.processEvents()

        # In burned-in Result mode, live overlays must be hidden
        self.assertEqual(win.video_player.current_mode, "result")
        self.assertFalse(win.video_player.logo_item.isVisible())
        self.assertFalse(win.video_player.btn_rerender_stale.isVisible())

        # Editing Logo setting switches back to Live mode & flags Result outdated
        win.slider_logo_opacity.setValue(50)
        self.app.processEvents()
        self.assertEqual(win.video_player.current_mode, "live")
        self.assertTrue(win.video_player.logo_item.isVisible())
        self.assertTrue(win.video_player.btn_rerender_stale.isVisible())

        # Switching to Logo tab from Result mode auto-switches to Live mode
        win.tool_tabs.setCurrentIndex(0)
        win.video_player.switch_mode("result")
        self.assertEqual(win.video_player.current_mode, "result")
        win.tool_tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(win.video_player.current_mode, "live")
        win.close()


if __name__ == "__main__":
    unittest.main()
