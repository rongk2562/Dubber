import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from app.batch_engine import (
    ST_DONE,
    ST_EXTRACTING,
    ST_PENDING,
    BatchItem,
    BatchJobConfig,
    BatchPipelineWorker,
    clear_batch_state,
    extract_mask_settings_dict,
    load_batch_state,
    resolve_episode_settings,
    save_batch_state,
    scan_video_folder,
    should_skip_item,
)
from app.ui.batch_dialog import BatchQueueDialog, BatchSubtitleMaskDialog
from app.logger import redact_secrets
from app.models import ProjectSettings, Segment
from app.openai_client import (
    OpenAIClient,
    merge_chunk_segments,
    validate_and_extract_batch_json,
)
from app.secrets import delete_secret, get_secret, set_secret


class TestBatchAndOpenAI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="khdub_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_natural_sort_episode_order(self):
        in_dir = self.tmp_dir / "MyDrama"
        out_dir = self.tmp_dir / "Out"
        in_dir.mkdir(parents=True, exist_ok=True)
        for name in ["EP10.mp4", "EP2.mp4", "EP1.mp4", "EP11.mkv"]:
            (in_dir / name).write_bytes(b"0" * 16)

        items = scan_video_folder(str(in_dir), str(out_dir), include_subfolders=False, probe_durations=False)
        self.assertEqual([it.filename for it in items], ["EP1.mp4", "EP2.mp4", "EP10.mp4", "EP11.mkv"])
        self.assertTrue(items[0].out_mp4.endswith(os.path.join("Out", "MyDrama", "EP1.mp4")))
        self.assertTrue(items[0].out_km_srt.endswith(os.path.join("Out", "MyDrama", "EP1.km.srt")))
        self.assertTrue(items[0].out_zh_srt.endswith(os.path.join("Out", "MyDrama", "EP1.zh.srt")))

    def test_merge_chunk_segments_adds_offsets(self):
        chunk_results = [
            (0.0, [{"start": 0.5, "end": 2.0, "text": "你好"}, {"start": 2.2, "end": 4.0, "text": "朋友"}]),
            (300.0, [{"start": 1.0, "end": 3.5, "text": "快跑"}]),
        ]
        merged = merge_chunk_segments(chunk_results)
        self.assertEqual(len(merged), 3)
        self.assertEqual([s.id for s in merged], [1, 2, 3])
        self.assertAlmostEqual(merged[0].start, 0.5, places=3)
        self.assertAlmostEqual(merged[2].start, 301.0, places=3)
        self.assertAlmostEqual(merged[2].end, 303.5, places=3)
        self.assertEqual(merged[2].src_source, "OpenAI")

    def test_batch_state_persistence_and_resume_reset(self):
        state_path = self.tmp_dir / "batch_queue.json"
        cfg = BatchJobConfig(
            input_folder=str(self.tmp_dir / "in"),
            output_folder=str(self.tmp_dir / "out"),
            overwrite_existing=False,
        )
        ep1_mp4 = self.tmp_dir / "out" / "ep1.mp4"
        ep1_srt = self.tmp_dir / "out" / "ep1.km.srt"
        ep1_mp4.parent.mkdir(parents=True, exist_ok=True)
        ep1_mp4.write_bytes(b"x" * 2048)
        ep1_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nសួស្តី\n", encoding="utf-8")

        items = [
            BatchItem(index=1, video_path="ep1.mp4", filename="ep1.mp4", status=ST_DONE, out_mp4=str(ep1_mp4), out_km_srt=str(ep1_srt)),
            BatchItem(index=2, video_path="ep2.mp4", filename="ep2.mp4", status=ST_EXTRACTING, progress=45),
        ]
        self.assertTrue(save_batch_state(cfg, items, state_file=state_path))
        loaded = load_batch_state(state_file=state_path)
        self.assertIsNotNone(loaded)
        l_cfg, l_items = loaded
        self.assertEqual(l_cfg.input_folder, cfg.input_folder)
        self.assertEqual(l_items[0].status, ST_DONE)
        # Mid-flight item resets back to ST_PENDING after restart
        self.assertEqual(l_items[1].status, ST_PENDING)
        self.assertEqual(l_items[1].progress, 0)
        self.assertTrue(should_skip_item(l_items[0], l_cfg))
        l_cfg.overwrite_existing = True
        self.assertFalse(should_skip_item(l_items[0], l_cfg))

    def test_validate_and_extract_batch_json_partial_missing(self):
        raw = '```json\n{"translations": {"0": "សួស្តី", "2": "លាហើយ"}}\n```'
        matched, missing = validate_and_extract_batch_json(raw, [0, 1, 2])
        self.assertEqual(matched, {0: "សួស្តី", 2: "លាហើយ"})
        self.assertEqual(missing, [1])

    def test_openai_batch_translation_targeted_retry_and_fallback(self):
        settings = ProjectSettings(
            openai_trans_model="gpt-4o-mini",
            openai_concurrency=1,
            openai_fallback_google=True,
        )
        client = OpenAIClient(settings)
        segs = [
            Segment(id=1, start=0.0, end=1.5, text_src="你好"),
            Segment(id=2, start=1.5, end=3.0, text_src="怎么了"),
            Segment(id=3, start=3.0, end=4.5, text_src="再见"),
        ]

        # First call returns indices 0 and 2; targeted retry returns index 1
        call_responses = [
            (json.dumps({"translations": {"0": "សួស្តី", "2": "ជួបគ្នាពេលក្រោយ"}}), client._call_chat_completion_with_retry),
        ]
        responses_iter = iter([
            (json.dumps({"translations": {"0": "សួស្តី", "2": "ជួបគ្នាពេលក្រោយ"}}), None),
            (json.dumps({"translations": {"1": "មានរឿងអ្វី?"}}), None),
        ])

        from app.openai_client import OpenAIUsageStats
        def fake_call(messages, model, api_key, base_url, cancel_cb=None, max_retries=4):
            txt, _ = next(responses_iter)
            return txt, OpenAIUsageStats(prompt_tokens=100, completion_tokens=50, estimated_usd=0.0001)

        with patch.object(client, "_resolve_key", return_value="sk-test-fake-1234567890"), \
             patch.object(client, "_call_chat_completion_with_retry", side_effect=fake_call):
            out_segs, usage = client.translate_segments_batch(segs, batch_size=30)

        self.assertEqual(out_segs[0].text_km, "សួស្តី")
        self.assertEqual(out_segs[1].text_km, "មានរឿងអ្វី?")
        self.assertEqual(out_segs[2].text_km, "ជួបគ្នាពេលក្រោយ")
        self.assertEqual(usage.total_tokens, 300)

    def test_secret_storage_and_redaction(self):
        test_key_name = "test_openai_unit_key"
        secret_val = "sk-proj-TestSecretKey987654321"
        try:
            ok = set_secret(test_key_name, secret_val)
            self.assertTrue(ok)
            self.assertEqual(get_secret(test_key_name), secret_val)
        finally:
            delete_secret(test_key_name)
        self.assertEqual(get_secret(test_key_name), "")

        # Ensure ProjectSettings strips plaintext key from JSON serialization
        st = ProjectSettings(openai_api_key=secret_val)
        d = st.to_dict()
        self.assertEqual(d.get("openai_api_key", ""), "")
        self.assertNotIn(secret_val, json.dumps(d))

        # Ensure log redaction hides OpenAI/Gemini/Bearer tokens
        msg = f"Failed Authorization: Bearer {secret_val} and key sk-abcdef1234567890"
        red = redact_secrets(msg)
        self.assertNotIn(secret_val, red)
        self.assertNotIn("sk-abcdef1234567890", red)
        self.assertIn("***REDACTED***", red)

    @staticmethod
    def _ensure_qapp():
        return QApplication.instance() or QApplication([])

    def test_batch_clear_queue_confirm_reset_and_running_disabled(self):
        self._ensure_qapp()
        state_file = self.tmp_dir / "batch_queue.json"
        in_dir = self.tmp_dir / "Drama"
        out_dir = self.tmp_dir / "Out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        vid1 = in_dir / "EP1.mp4"
        vid1.write_bytes(b"video-data")

        dlg = BatchQueueDialog(lang="km", base_settings=ProjectSettings(), state_file=state_file)
        self.assertFalse(dlg.btn_start.isEnabled())
        self.assertFalse(dlg.btn_clear_queue.isEnabled())

        # Scan folder -> items loaded & controls enabled
        dlg.txt_in_dir.setText(str(in_dir))
        dlg.txt_out_dir.setText(str(out_dir))
        with patch("app.ui.batch_dialog.scan_video_folder", return_value=[
            BatchItem(index=1, video_path=str(vid1), filename="EP1.mp4", duration_sec=60.0)
        ]):
            dlg._scan_folder()

        self.assertEqual(len(dlg.items), 1)
        self.assertTrue(dlg.btn_start.isEnabled())
        self.assertTrue(dlg.btn_clear_queue.isEnabled())
        dlg.overall_pbar.setValue(72)
        dlg.lbl_timing.setText("កន្លងផុត: 01:12 | នៅសល់: 00:30")
        dlg.txt_log.appendPlainText("Line 1 log")

        # While worker is running -> Clear must be disabled and no-op
        class _FakeRunningWorker:
            def isRunning(self):
                return True
        dlg.worker = _FakeRunningWorker()
        dlg._update_controls_state()
        self.assertFalse(dlg.btn_clear_queue.isEnabled())
        dlg._clear_queue()
        self.assertEqual(len(dlg.items), 1)

        # Stop fake worker -> Confirm No preserves items; Confirm Yes clears & resets state
        dlg.worker = None
        dlg._update_controls_state()
        self.assertTrue(dlg.btn_clear_queue.isEnabled())

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as q_mock:
            dlg._clear_queue()
            self.assertEqual(q_mock.call_count, 1)
            self.assertIn("តើអ្នកចង់សម្អាតបញ្ជីទាំងអស់មែនទេ?", q_mock.call_args[0][2])
        self.assertEqual(len(dlg.items), 1)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dlg._clear_queue()

        # Real file on disk untouched, queue & JSON & progress reset, Start disabled until next scan
        self.assertTrue(vid1.exists())
        self.assertEqual(len(dlg.items), 0)
        self.assertEqual(dlg.table.rowCount(), 0)
        self.assertEqual(dlg.overall_pbar.value(), 0)
        self.assertFalse(dlg.btn_start.isEnabled())
        self.assertFalse(dlg.btn_clear_queue.isEnabled())
        loaded = load_batch_state(state_file)
        self.assertTrue(loaded is None or len(loaded[1]) == 0)

        # Clear Log clears only log text box
        dlg.btn_clear_log.click()
        self.assertEqual(dlg.txt_log.toPlainText(), "")
        dlg.close()

    def test_batch_mask_settings_persistence_and_isolation(self):
        self._ensure_qapp()
        state_file = self.tmp_dir / "batch_mask_state.json"
        normal_settings = ProjectSettings(
            mask_subtitles=True,
            mask_y_percent=83.0,
            mask_height_percent=12.0,
            mask_style="solid",
        )

        custom_mask = ProjectSettings(
            mask_subtitles=True,
            mask_y_percent=76.0,
            mask_x_percent=5.0,
            mask_width_percent=90.0,
            mask_height_percent=14.0,
            mask_style="blur",
            mask_blur_strength=65,
            mask_blur_dim=30,
            subtitle_font_size=24,
            subtitle_margin_v=40,
        )

        cfg = BatchJobConfig(
            input_folder=str(self.tmp_dir),
            output_folder=str(self.tmp_dir / "out"),
            mask_enabled=True,
            mask_mode="custom",
            batch_mask_settings=extract_mask_settings_dict(custom_mask),
        )
        items = [
            BatchItem(index=1, video_path="ep1.mp4", filename="ep1.mp4"),
            BatchItem(
                index=2,
                video_path="ep2.mp4",
                filename="ep2.mp4",
                mask_override={"mask_subtitles": True, "mask_y_percent": 70.0, "mask_style": "solid", "mask_opacity": 0.85},
            ),
        ]
        self.assertTrue(save_batch_state(cfg, items, state_file=state_file))

        # Re-open BatchQueueDialog: restores custom batch mask without mutating normal_settings
        dlg = BatchQueueDialog(lang="km", base_settings=normal_settings, state_file=state_file)
        self.assertTrue(dlg.rb_mask_custom.isChecked())
        self.assertTrue(dlg.chk_batch_mask.isChecked())
        self.assertIn("Y 76%", dlg.lbl_mask_summary.text())
        self.assertIn("ព្រិល", dlg.lbl_mask_summary.text())
        self.assertEqual(dlg.items[1].mask_override["mask_y_percent"], 70.0)

        # Single-video normal settings untouched
        self.assertEqual(normal_settings.mask_y_percent, 83.0)
        self.assertEqual(normal_settings.mask_style, "solid")
        dlg.close()

    def test_batch_render_applies_mask_settings_and_skips_when_disabled(self):
        base = ProjectSettings(mask_subtitles=True, mask_y_percent=83.0, mask_style="solid")
        custom_cfg = BatchJobConfig(
            input_folder=str(self.tmp_dir),
            output_folder=str(self.tmp_dir / "out"),
            run_extract=False,
            run_translate=False,
            run_tts=False,
            run_export_srt=False,
            run_render=True,
            mask_enabled=True,
            mask_mode="custom",
            batch_mask_settings={
                "mask_subtitles": True,
                "mask_y_percent": 75.0,
                "mask_height_percent": 15.0,
                "mask_style": "blur",
                "mask_blur_strength": 55,
            },
        )
        ep1 = BatchItem(
            index=1,
            video_path=str(self.tmp_dir / "ep1.mp4"),
            filename="ep1.mp4",
            out_mp4=str(self.tmp_dir / "out" / "ep1.mp4"),
            out_km_srt=str(self.tmp_dir / "out" / "ep1.km.srt"),
            out_zh_srt=str(self.tmp_dir / "out" / "ep1.zh.srt"),
        )
        ep2 = BatchItem(
            index=2,
            video_path=str(self.tmp_dir / "ep2.mp4"),
            filename="ep2.mp4",
            out_mp4=str(self.tmp_dir / "out" / "ep2.mp4"),
            out_km_srt=str(self.tmp_dir / "out" / "ep2.km.srt"),
            out_zh_srt=str(self.tmp_dir / "out" / "ep2.zh.srt"),
            mask_override={"mask_subtitles": True, "mask_y_percent": 68.0, "mask_style": "solid"},
        )

        captured_settings = []
        worker = BatchPipelineWorker(
            config=custom_cfg,
            items=[ep1, ep2],
            base_settings=base,
            state_file=self.tmp_dir / "worker_state.json",
        )
        with patch("app.export_engine.ExportEngine.export_all", side_effect=lambda project, **kw: captured_settings.append(project.settings)):
            worker.run()

        self.assertEqual(len(captured_settings), 2)
        self.assertTrue(captured_settings[0].mask_subtitles)
        self.assertEqual(captured_settings[0].mask_style, "blur")
        self.assertEqual(captured_settings[0].mask_y_percent, 75.0)
        self.assertEqual(captured_settings[0].mask_blur_strength, 55)
        # Per-episode override took precedence for ep2
        self.assertEqual(captured_settings[1].mask_style, "solid")
        self.assertEqual(captured_settings[1].mask_y_percent, 68.0)

        # When masking is disabled in Batch, render receives mask_subtitles=False and base stays True
        custom_cfg.mask_enabled = False
        ep3 = BatchItem(
            index=3,
            video_path=str(self.tmp_dir / "ep3.mp4"),
            filename="ep3.mp4",
            out_mp4=str(self.tmp_dir / "out" / "ep3.mp4"),
        )
        disabled_settings = []
        worker2 = BatchPipelineWorker(
            config=custom_cfg,
            items=[ep3],
            base_settings=base,
            state_file=self.tmp_dir / "worker_state2.json",
        )
        with patch("app.export_engine.ExportEngine.export_all", side_effect=lambda project, **kw: disabled_settings.append(project.settings)):
            worker2.run()
        self.assertFalse(disabled_settings[0].mask_subtitles)
        self.assertTrue(base.mask_subtitles)

    def test_batch_mask_dialog_sample_fallback_without_blocking(self):
        self._ensure_qapp()
        st = ProjectSettings()
        dlg = BatchSubtitleMaskDialog(
            settings=st,
            video_path=str(self.tmp_dir / "missing_video.mp4"),
            duration_sec=185.0,
            lang="km",
        )
        self.assertAlmostEqual(dlg.video_duration, 185.0, places=1)
        self.assertIsNone(dlg.canvas.frame_pixmap)
        dlg.close()

    def test_batch_audio_mix_persistence_isolation_and_badge(self):
        self._ensure_qapp()
        state_file = self.tmp_dir / "batch_audio_state.json"
        normal_settings = ProjectSettings(
            original_voice_volume=0.0,
            background_volume=0.30,
            keep_background=True,
            mute_original=False,
            dubbed_volume=1.0,
            ai_voice_speed=1.05,
        )

        dlg = BatchQueueDialog(lang="km", base_settings=normal_settings, state_file=state_file)
        dlg.show()
        self.assertTrue(dlg.rb_audio_normal.isChecked())
        self.assertIn("Original: OFF | BG: 30% | AI: 100%", dlg.audio_mix_widget.lbl_badge.text())
        self.assertFalse(dlg.audio_mix_widget.lbl_warn.isVisible())

        # Edit audio mix inside Batch -> switches to Custom mode, updates JSON, leaves normal_settings untouched
        dlg.audio_mix_widget.sw_orig.setChecked(True)
        dlg.audio_mix_widget.slider_orig.setValue(25)
        dlg.audio_mix_widget.slider_bg.setValue(40)
        dlg.audio_mix_widget.slider_ai.setValue(120)
        dlg.audio_mix_widget.seg_speed.set_value("auto_fit", emit=True)
        self._ensure_qapp().processEvents()

        self.assertTrue(dlg.rb_audio_custom.isChecked())
        self.assertIn("Original: 25% | BG: 40% | AI: 120% | Auto-fit", dlg.audio_mix_widget.lbl_badge.text())
        self.assertEqual(normal_settings.original_voice_volume, 0.0)
        self.assertEqual(normal_settings.background_volume, 0.30)
        self.assertEqual(normal_settings.dubbed_volume, 1.0)
        dlg.close()

        # Re-open from persisted JSON -> restores custom audio mix
        dlg2 = BatchQueueDialog(lang="km", base_settings=normal_settings, state_file=state_file)
        dlg2.show()
        self.assertTrue(dlg2.rb_audio_custom.isChecked())
        self.assertIn("Original: 25% | BG: 40% | AI: 120% | Auto-fit", dlg2.audio_mix_widget.lbl_badge.text())

        # Mute both Original & BG -> shows warning banner
        dlg2.audio_mix_widget.sw_orig.setChecked(False)
        dlg2.audio_mix_widget.sw_bg.setChecked(False)
        self._ensure_qapp().processEvents()
        self.assertIn("Original: OFF | BG: OFF", dlg2.audio_mix_widget.lbl_badge.text())
        self.assertTrue(dlg2.audio_mix_widget.lbl_warn.isVisible())
        dlg2.close()

    def test_batch_audio_mix_mute_orig_bg30_across_3_episodes_and_no_audio_track(self):
        base = ProjectSettings(original_voice_volume=0.5, background_volume=0.6, dubbed_volume=1.0)
        cfg = BatchJobConfig(
            input_folder=str(self.tmp_dir),
            output_folder=str(self.tmp_dir / "out"),
            run_extract=False,
            run_translate=False,
            run_tts=False,
            run_export_srt=False,
            run_render=True,
            audio_mode="custom",
            batch_audio_settings={
                "orig_enabled": False,
                "original_voice_volume": 0.0,
                "bg_enabled": True,
                "background_volume": 0.30,
                "dubbed_volume": 1.0,
                "ai_voice_speed": 1.15,
                "auto_fit": False,
            },
        )
        ep1 = BatchItem(index=1, video_path=str(self.tmp_dir / "ep1.mp4"), filename="ep1.mp4", duration_sec=4.0, out_mp4=str(self.tmp_dir / "out" / "ep1.mp4"))
        ep2 = BatchItem(
            index=2,
            video_path=str(self.tmp_dir / "ep2.mp4"),
            filename="ep2.mp4",
            duration_sec=4.0,
            out_mp4=str(self.tmp_dir / "out" / "ep2.mp4"),
            audio_override={
                "orig_enabled": True,
                "original_voice_volume": 0.20,
                "bg_enabled": True,
                "background_volume": 0.50,
                "dubbed_volume": 1.20,
                "ai_voice_speed": 1.0,
                "auto_fit": True,
            },
        )
        ep3 = BatchItem(index=3, video_path=str(self.tmp_dir / "ep3.mp4"), filename="ep3.mp4", duration_sec=4.0, out_mp4=str(self.tmp_dir / "out" / "ep3.mp4"))

        rendered_projects = []
        logs = []
        worker = BatchPipelineWorker(
            config=cfg,
            items=[ep1, ep2, ep3],
            base_settings=base,
            state_file=self.tmp_dir / "audio_worker_state.json",
        )
        worker.log_message.connect(logs.append)

        def fake_has_audio(vp: str) -> bool:
            return not vp.endswith("ep3.mp4")

        with patch("app.batch_engine.has_audio_stream", side_effect=fake_has_audio), \
             patch("app.export_engine.ExportEngine.export_all", side_effect=lambda project, **kw: rendered_projects.append(project)):
            worker.run()

        self.assertEqual(len(rendered_projects), 3)
        # Ep1: Original OFF, BG 30% -> mute_original MUST be False so BG stays active!
        s1 = rendered_projects[0].settings
        self.assertEqual(s1.original_voice_volume, 0.0)
        self.assertAlmostEqual(s1.background_volume, 0.30, places=2)
        self.assertTrue(s1.keep_background)
        self.assertFalse(s1.mute_original)
        self.assertAlmostEqual(s1.ai_voice_speed, 1.15, places=2)

        # Ep2: Per-episode override
        s2 = rendered_projects[1].settings
        self.assertAlmostEqual(s2.original_voice_volume, 0.20, places=2)
        self.assertAlmostEqual(s2.background_volume, 0.50, places=2)
        self.assertAlmostEqual(s2.dubbed_volume, 1.20, places=2)
        self.assertTrue(getattr(s2, "_batch_audio_auto_fit", False))

        # Ep3: No audio track -> skips mixing cleanly & logs warning
        s3 = rendered_projects[2].settings
        self.assertTrue(s3.mute_original)
        self.assertEqual(s3.background_volume, 0.0)
        self.assertTrue(os.path.exists(rendered_projects[2].audio_path))
        self.assertTrue(any("គ្មាន audio track" in m for m in logs))

        # Base single-video settings remain untouched
        self.assertEqual(base.original_voice_volume, 0.5)
        self.assertEqual(base.background_volume, 0.6)

    def test_batch_audio_preview_renders_wav_with_mix_settings(self):
        import subprocess
        from pydub import AudioSegment
        from app.batch_engine import render_batch_audio_preview

        vid = self.tmp_dir / "sample_av.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=3",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
                "-c:v", "libx264", "-t", "3", "-c:a", "aac", str(vid),
            ],
            capture_output=True, check=True,
        )
        st = ProjectSettings(
            original_voice_volume=0.0,
            original_volume=0.3,
            background_volume=0.30,
            keep_background=True,
            mute_original=False,
            dubbed_volume=1.0,
        )
        out_wav = str(self.tmp_dir / "prev_out.wav")
        with patch("app.tts_engine.TTSEngine.generate_segment_tts", side_effect=RuntimeError("offline test")):
            wav_path, note = render_batch_audio_preview(str(vid), st, lang="km", clip_seconds=3.0, out_wav=out_wav)

        self.assertTrue(os.path.exists(wav_path))
        seg = AudioSegment.from_file(wav_path)
        self.assertEqual(seg.frame_rate, 48000)
        self.assertEqual(seg.channels, 2)
        self.assertGreater(seg.dBFS, -60.0)
        self.assertTrue(bool(note))

    def test_gemini_batch_and_google_concurrent_translation_speed(self):
        from app.translation_engine import TranslationEngine
        segs = [
            Segment(id=1, start=0.0, end=2.0, text_src="今天真冷"),
            Segment(id=2, start=2.0, end=4.0, text_src="我们快回去"),
            Segment(id=3, start=4.0, end=6.0, text_src="别等了"),
        ]
        st_gem = ProjectSettings(translation_provider="gemini", gemini_api_key="AIzaTestDummyKey12345")
        eng = TranslationEngine(st_gem)
        with patch.object(eng, "_call_gemini_api", return_value='{"translations": {"0": "ថ្ងៃនេះត្រជាក់ណាស់", "1": "យើងប្រញាប់ត្រឡប់ទៅវិញ", "2": "កុំចាំអី"}}') as g_mock:
            eng.translate_segments(segs, target_lang="km")
        # All 3 lines translated in ONE single batch API call instead of 3 sequential calls
        self.assertEqual(g_mock.call_count, 1)
        self.assertTrue(all((s.text_km or "").strip() for s in segs))

        # Concurrent Google mode
        segs2 = [Segment(id=i + 1, start=float(i), end=float(i + 1), text_src=f"台词{i}") for i in range(6)]
        st_gg = ProjectSettings(translation_provider="google")
        eng2 = TranslationEngine(st_gg)
        with patch.object(eng2, "_translate_web_or_heuristic", side_effect=lambda txt, gl, tl: f"ប្រែ_{txt}"):
            eng2.translate_segments(segs2, target_lang="km")
        self.assertEqual([s.text_km for s in segs2], [f"ប្រែ_台词{i}។" for i in range(6)])

    def test_tts_generate_segments_batch_concurrent(self):
        from app.tts_engine import TTSEngine
        tts = TTSEngine(output_dir=self.tmp_dir / "tts_out")
        segs = [Segment(id=i + 1, start=float(i), end=float(i + 1.5), text_km=f"សួស្តី {i}") for i in range(8)]
        seen_ids = []

        def fake_gen(seg, speed_factor=None):
            seen_ids.append((seg.id, speed_factor))
            seg.tts_duration = 1.0
            return str(self.tmp_dir / f"seg_{seg.id}.wav"), 1.0

        prog_events = []
        with patch.object(tts, "generate_segment_tts", side_effect=fake_gen):
            tts.generate_segments_batch(
                segs,
                speed_fn=lambda _s: 1.15,
                max_workers=4,
                progress_cb=lambda p, m: prog_events.append((p, m)),
            )

        self.assertEqual(sorted(x[0] for x in seen_ids), [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertTrue(all(abs(x[1] - 1.15) < 1e-6 for x in seen_ids))
        self.assertTrue(any(p == 100 for p, _ in prog_events))


if __name__ == "__main__":
    unittest.main()
