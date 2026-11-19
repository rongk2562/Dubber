import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolBar, QHeaderView, QScrollArea
from PySide6.QtCore import QRect, Qt
from unittest.mock import patch

from app.models import Project, Segment, ProjectSettings
from app.database import DatabaseManager
from app.ui.theme import (
    TOKENS, THEME_QSS, ToggleSwitch, SegmentedControl,
    get_active_screen, is_geometry_on_any_screen, clamp_widget_to_screen
)
from app.ui.main_window import MainWindow
from app.ui.subtitle_mask_dialog import SubtitleMaskDialog
from app.ui.notification_ui import NotificationCenterDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.update_dialog import UpdateDialog
from app.notifications import NotificationManager


class TestUIRefactor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(THEME_QSS)
        cls._autosave_patch = patch.object(DatabaseManager, "has_autosave", return_value=False)
        cls._autosave_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._autosave_patch.stop()

    def test_main_window_no_toolbar_no_overlap_at_1280x720(self):
        win = MainWindow()
        scr = get_active_screen(win)
        avail = scr.availableGeometry()

        # Minimum size <= available - 20px (e.g. <= 1024x600)
        self.assertLessEqual(win.minimumWidth(), avail.width() - 20)
        self.assertLessEqual(win.minimumHeight(), avail.height() - 20)
        self.assertLessEqual(win.minimumWidth(), 1024)
        self.assertLessEqual(win.minimumHeight(), 600)

        # Left panel uses QScrollArea (ScrollBarAlwaysOff horizontal) & Preview button is OUTSIDE scroll
        self.assertIsInstance(win.left_scroll, QScrollArea)
        self.assertEqual(win.left_scroll.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertIsNot(win.btn_main_preview.parentWidget(), win.left_scroll.widget())

        # Video aspect ratio & min height ~160
        self.assertLessEqual(win.video_player.video_widget.minimumHeight(), 165)
        self.assertEqual(win.video_player.video_widget.aspectRatioMode(), Qt.AspectRatioMode.KeepAspectRatio)

        # Table columns: text columns Stretch, numeric/meta ResizeToContents
        hdr = win.segment_table.table.horizontalHeader()
        self.assertEqual(hdr.sectionResizeMode(win.segment_table.COL_SRC), QHeaderView.ResizeMode.Stretch)
        self.assertEqual(hdr.sectionResizeMode(win.segment_table.COL_KM), QHeaderView.ResizeMode.Stretch)
        self.assertEqual(hdr.sectionResizeMode(win.segment_table.COL_ID), QHeaderView.ResizeMode.ResizeToContents)

        # Test target screen sizes: 1366x768, 1536x864, 1280x720
        for w, h in [(1366, 768), (1536, 864), (1280, 720)]:
            win.resize(w, h)
            win.show()
            self.app.processEvents()
            self.assertTrue(win.rect().contains(win.btn_main_preview.mapTo(win, win.btn_main_preview.rect().topLeft())))
            self.assertTrue(win.btn_main_preview.isVisible())
            self.assertTrue(win.segment_table.btn_gen_tts.isVisible())

        # At 1200px (<1280), compact header & overflow '⋯' visible
        win.resize(1200, 700)
        self.app.processEvents()
        self.assertTrue(win.btn_header_more.isVisible())

        win.resize(1280, 720)
        self.app.processEvents()

        # No legacy QToolBar
        self.assertEqual(len(win.findChildren(QToolBar)), 0)

        # 6 stepper buttons visible and non-overlapping
        self.assertEqual(len(win.step_buttons), 6)
        rects = [b.geometry() for b in win.step_buttons]
        for i in range(len(rects)):
            self.assertGreater(rects[i].width(), 40)
            self.assertGreater(rects[i].height(), 20)
            for j in range(i + 1, len(rects)):
                self.assertFalse(
                    rects[i].intersects(rects[j]),
                    f"Step button {i} overlaps {j}: {rects[i]} vs {rects[j]}"
                )

        # Top-right utilities non-overlapping with stepper
        right_rects = [
            win.btn_notifications.geometry(),
            win.btn_header_settings.geometry(),
            win.cb_ui_lang.geometry(),
        ]
        for rr in right_rects:
            for sr in rects:
                self.assertFalse(rr.intersects(sr), f"Utility {rr} overlaps step {sr}")

        # Engine bar widgets visible, unclipped, and combo widths >= sizeHint (no text truncation)
        self.assertTrue(win.cb_src_mode.isVisible())
        self.assertTrue(win.cb_target_lang.isVisible())
        self.assertTrue(win.cb_trans_provider.isVisible())
        self.assertTrue(win.btn_export_zh_srt.isVisible())
        self.assertTrue(win.btn_import_srt.isVisible())

        for cb in (win.cb_src_mode, win.cb_target_lang, win.cb_trans_provider, win.cb_ui_lang):
            self.assertGreaterEqual(
                cb.width(),
                cb.sizeHint().width(),
                f"{cb} truncated: width={cb.width()} < sizeHint={cb.sizeHint().width()}"
            )

        # Left video mode bar & right engine bar stay completely inside their parent rects
        vp_rect = win.video_player.rect()
        self.assertTrue(vp_rect.contains(win.video_player.lbl_mode_status.geometry()),
                        f"lbl_mode_status clipped: {win.video_player.lbl_mode_status.geometry()} vs {vp_rect}")
        st_rect = win.segment_table.rect()
        self.assertTrue(st_rect.contains(win.lbl_src_mode.geometry()),
                        f"lbl_src_mode clipped: {win.lbl_src_mode.geometry()} vs {st_rect}")
        self.assertTrue(st_rect.contains(win.btn_import_srt.geometry()),
                        f"btn_import_srt clipped: {win.btn_import_srt.geometry()} vs {st_rect}")

        # Empty states: video drop zone & 3-step guide shown when empty
        self.assertTrue(win.video_player.btn_drop_zone.isVisible())
        self.assertEqual(win.segment_table.table_stack.currentIndex(), 0)

        # Populating segments switches table stack to table view
        win.project.segments.append(Segment(id=1, start=0.0, end=2.0, text_src="你好", text_km="សួស្តី"))
        win.segment_table.populate(win.project)
        self.app.processEvents()
        self.assertEqual(win.segment_table.table_stack.currentIndex(), 1)
        win.close()

    def test_audio_switches_preserve_mute_invariants(self):
        win = MainWindow()
        win.show()
        self.app.processEvents()

        # Both switches OFF -> mute_original=True, keep_background=False, volumes 0
        win.sw_orig_voice.setChecked(False)
        win.sw_bg_music.setChecked(False)
        self.app.processEvents()
        self.assertTrue(win.project.settings.mute_original)
        self.assertFalse(win.project.settings.keep_background)
        self.assertEqual(win.project.settings.original_voice_volume, 0.0)
        self.assertEqual(win.project.settings.background_volume, 0.0)

        # Enable BG only
        win.sw_bg_music.setChecked(True)
        self.app.processEvents()
        self.assertTrue(win.project.settings.mute_original)
        self.assertTrue(win.project.settings.keep_background)
        self.assertGreater(win.project.settings.background_volume, 0.0)

        # Speed SegmentedControl updates settings
        win.seg_ai_speed.set_value(1.15, emit=True)
        self.app.processEvents()
        self.assertAlmostEqual(win.project.settings.ai_voice_speed, 1.15, places=2)
        win.close()

    def test_mask_dialog_clean_controls_and_preview_ratio(self):
        st = ProjectSettings()
        dlg = SubtitleMaskDialog(settings=st, video_path="", current_time=0.0, segments=[], lang="km")
        dlg.show()
        self.app.processEvents()

        # No duplicate checkbox emoji string
        self.assertNotIn("☑", dlg.chk_mask.text())
        self.assertIsInstance(dlg.chk_mask, ToggleSwitch)
        self.assertIsInstance(dlg.seg_style, SegmentedControl)

        # Preview area >= 55% of dialog width (layout stretch 3:2 => ~60%)
        self.assertGreaterEqual(dlg.canvas.width() / max(1, dlg.width()), 0.50)

        # Blur switch works via SegmentedControl
        dlg.seg_style.set_value("blur")
        dlg._on_style_toggled()
        self.assertTrue(dlg.chk_mask.isChecked())
        self.assertTrue(dlg.box_blur_opts.isVisible())
        self.assertFalse(dlg.box_solid_opts.isVisible())
        dlg.close()

    def test_notification_center_dark_theme_and_settings_sidebar(self):
        mgr = NotificationManager(app_version="1.3.1")
        dlg = NotificationCenterDialog(mgr, lang="km")
        dlg.show()
        self.app.processEvents()
        self.assertIn(TOKENS["bg"], dlg.styleSheet())
        self.assertEqual(dlg.scroll_body.objectName(), "scrollBody")
        dlg.close()

        st = ProjectSettings()
        sdlg = SettingsDialog(st, lang="km")
        sdlg.show()
        self.app.processEvents()
        scr = get_active_screen(sdlg)
        avail = scr.availableGeometry()
        self.assertLessEqual(sdlg.maximumWidth(), int(avail.width() * 0.90) + 1)
        self.assertLessEqual(sdlg.maximumHeight(), int(avail.height() * 0.90) + 1)
        self.assertGreaterEqual(len(sdlg.findChildren(QScrollArea)), 1)
        self.assertEqual(sdlg.sidebar.count(), 7)
        self.assertEqual(sdlg.stack.count(), 7)
        # Toggle API key echo visibility via the 👁 button on Translation and OpenAI pages
        from PySide6.QtWidgets import QPushButton
        eye_btns = [b for b in sdlg.stack.widget(2).findChildren(QPushButton) if b.text() == "👁"]
        self.assertEqual(len(eye_btns), 3)
        eye_btns[0].setChecked(True)
        self.app.processEvents()
        self.assertEqual(sdlg.txt_gemini.echoMode(), sdlg.txt_gemini.EchoMode.Normal)
        oa_eye_btns = [b for b in sdlg.stack.widget(3).findChildren(QPushButton) if b.text() == "👁"]
        self.assertEqual(len(oa_eye_btns), 1)
        oa_eye_btns[0].setChecked(True)
        self.app.processEvents()
        self.assertEqual(sdlg.txt_oa_sec_key.echoMode(), sdlg.txt_oa_sec_key.EchoMode.Normal)
        sdlg.close()

    def test_screen_geometry_validation_and_clamping(self):
        # Off-screen phantom geometry rejected
        self.assertFalse(is_geometry_on_any_screen(QRect(-9999, -9999, 1000, 700)))
        win = MainWindow()
        win.move(-5000, -5000)
        clamp_widget_to_screen(win)
        avail = get_active_screen(win).availableGeometry()
        self.assertTrue(avail.contains(win.frameGeometry().topLeft()))
        win.close()


if __name__ == "__main__":
    unittest.main()
