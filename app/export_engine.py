"""
Export Engine: MP4 muxing, SRT subtitle generator, WAV audio export, Burn-in Subtitles
F10: MP4, SRT, WAV, burn-in subtitles
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Callable
from app.models import Project
from app.audio_engine import AudioEngine
from app.utils import generate_srt
from app.config import TEMP_DIR

class ExportEngine:
    def __init__(self, audio_engine: Optional[AudioEngine] = None):
        self.audio_engine = audio_engine or AudioEngine()

    def _get_video_dimensions(self, video_path: str) -> tuple[int, int]:
        """Return (width, height) of the video using ffprobe."""
        if not video_path or not os.path.exists(video_path):
            return 1280, 720
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            video_path
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            parts = res.stdout.strip().split("x")
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return 1280, 720

    def export_all(
        self,
        project: Project,
        output_mp4_path: str,
        export_srt_path: Optional[str] = None,
        export_wav_path: Optional[str] = None,
        burn_subtitles: Optional[bool] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> bool:
        """
        Full export pipeline:
        1. Build continuous dubbed track
        2. Mix with original audio & apply ducking
        3. Export SRT
        4. Mux final audio with original video (or encode with burned subtitles)
        """
        if not project.video_path or not os.path.exists(project.video_path):
            raise FileNotFoundError("Original video file not found.")

        if burn_subtitles is None:
            burn_subtitles = project.settings.burn_subtitles

        total_duration = self.audio_engine.get_media_duration(project.video_path)

        # Step 0: Ensure missing audio clips are generated (with TTSFitter retry >= 3 & -16 LUFS norm)
        from app.dubbing.tts_fitter import TTSFitter
        fitter = TTSFitter(temp_dir=self.audio_engine.temp_dir)
        missing_tts = [
            s for s in project.segments
            if (s.text_km or s.text_src) and (not s.tts_file or not os.path.exists(s.tts_file) or os.path.getsize(s.tts_file) <= 180)
        ]
        if missing_tts:
            fitter.process_all_segments(
                missing_tts,
                progress_cb=lambda pct, msg: progress_cb(5 + int(pct * 0.08), msg) if progress_cb else None
            )

        # Step 1: Build continuous dubbed voice track
        if progress_cb:
            progress_cb(15, "1/4 Assembling continuous Khmer voice track...")
        
        dubbed_wav = self.audio_engine.build_dubbed_audio_track(
            total_duration_sec=total_duration,
            segments=project.segments,
            progress_cb=lambda pct, msg: progress_cb(15 + int(pct * 0.15), msg) if progress_cb else None
        )

        # Step 2: Ensure stereo original audio extracted
        if not project.audio_path or not os.path.exists(project.audio_path):
            if progress_cb:
                progress_cb(32, "Extracting original stereo background audio...")
            project.audio_path = self.audio_engine.extract_original_full_audio(project.video_path)

        # Step 3: Mix audio with ducking
        if progress_cb:
            progress_cb(40, "2/4 Mixing dubbed voices with ducked background audio...")
        
        final_mixed_wav = self.audio_engine.mix_audio_with_ducking(
            original_audio_path=project.audio_path,
            dubbed_audio_path=dubbed_wav,
            segments=project.segments,
            original_vol_factor=project.settings.original_volume,
            original_voice_vol=getattr(project.settings, "original_voice_volume", 0.0),
            background_vol=getattr(project.settings, "background_volume", 0.30),
            dubbed_vol_factor=project.settings.dubbed_volume,
            mute_original=project.settings.mute_original,
            keep_background=getattr(project.settings, "keep_background", True),
            ducking_db=project.settings.ducking_db,
            fade_ms=project.settings.ducking_fade_ms,
            progress_cb=lambda pct, msg: progress_cb(40 + int(pct * 0.25), msg) if progress_cb else None
        )

        # Post-export Silence Verification if Mute is enabled
        if project.settings.mute_original:
            from app.dubbing.mixer import TimelineMixer
            passed_silence, max_vol, silence_msg = TimelineMixer.verify_mute_silence(final_mixed_wav, project.segments)
            if not passed_silence:
                print(f"[ExportEngine] WARNING: {silence_msg}")

        # Optional WAV export
        if export_wav_path:
            import shutil
            shutil.copy(final_mixed_wav, export_wav_path)

        # Step 4: Generate SRT subtitles (using SubtitleRenderer with glossary & word wrapping)
        if progress_cb:
            progress_cb(70, "3/4 Generating Khmer subtitle file (SRT)...")
        from app.dubbing.subtitle_renderer import SubtitleRenderer
        srt_file = export_srt_path or str(Path(output_mp4_path).with_suffix(".srt"))
        SubtitleRenderer.generate_srt(
            segments=project.segments,
            output_srt_path=srt_file,
            custom_glossary=project.glossary
        )

        # Step 5: Mux / Render MP4 Video with Subtitles, Chinese Subtitle Masking & Logo Overlay
        if progress_cb:
            progress_cb(80, "4/4 Muxing final video with FFmpeg...")

        has_logo = (
            project.settings.logo_enabled
            and bool(project.settings.logo_path)
            and os.path.exists(project.settings.logo_path)
        )

        font_to_use = getattr(project.settings, "subtitle_font", "Noto Sans Khmer") or "Noto Sans Khmer"
        vid_w, vid_h = self._get_video_dimensions(project.video_path)

        if burn_subtitles:
            video_filter = SubtitleRenderer.build_video_filter(
                srt_path=srt_file,
                mask_original_subs=getattr(project.settings, "mask_subtitles", True),
                font_name=font_to_use,
                font_size=getattr(project.settings, "subtitle_font_size", 20),
                mask_y_pct=getattr(project.settings, "mask_y_percent", 83.0),
                mask_h_pct=getattr(project.settings, "mask_height_percent", 12.0),
                mask_w_pct=getattr(project.settings, "mask_width_percent", 100.0),
                mask_x_pct=getattr(project.settings, "mask_x_percent", 0.0),
                mask_opacity=getattr(project.settings, "mask_opacity", 0.95),
                mask_color=getattr(project.settings, "mask_color", "black"),
                subtitle_margin_v=getattr(project.settings, "subtitle_margin_v", 25),
                video_w=vid_w,
                video_h=vid_h,
                logo_path=project.settings.logo_path if has_logo else None,
                logo_pos=project.settings.logo_position,
                logo_w=project.settings.logo_width,
                logo_opacity=project.settings.logo_opacity,
                logo_margin_x=project.settings.logo_margin_x,
                logo_margin_y=project.settings.logo_margin_y
            )

            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
            ]
            if has_logo:
                cmd.extend(["-i", project.settings.logo_path])
                cmd.extend(["-filter_complex", video_filter, "-map", "[v_out]"])
            else:
                cmd.extend(["-vf", video_filter, "-map", "0:v:0"])

            cmd.extend([
                "-map", "1:a:0",
                "-c:v", "libx264",
                "-crf", "22",
                "-preset", "veryfast",
                "-threads", "0",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-shortest",
                output_mp4_path
            ])
        elif has_logo:
            # Logo only
            video_filter = SubtitleRenderer.build_video_filter(
                srt_path=srt_file,
                mask_original_subs=False,
                logo_path=project.settings.logo_path,
                logo_pos=project.settings.logo_position,
                logo_w=project.settings.logo_width,
                logo_opacity=project.settings.logo_opacity,
                logo_margin_x=project.settings.logo_margin_x,
                logo_margin_y=project.settings.logo_margin_y
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-i", project.settings.logo_path,
                "-filter_complex", video_filter,
                "-map", "[v_out]",
                "-map", "1:a:0",
                "-c:v", "libx264",
                "-crf", "22",
                "-preset", "veryfast",
                "-threads", "0",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-shortest",
                output_mp4_path
            ]
        else:
            # Case 4: Audio mux only
            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_mp4_path
            ]

        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace"
        )
        if res.returncode != 0:
            fallback_cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_mp4_path
            ]
            subprocess.run(
                fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                encoding="utf-8", errors="replace"
            )

        # Step 6: Automated QA Verification & Report Generation
        from app.dubbing.qa_checker import QAChecker
        out_parent = Path(output_mp4_path).parent
        qa = QAChecker(output_dir=out_parent)
        qa.generate_report(
            segments=project.segments,
            original_duration=total_duration,
            final_audio_path=final_mixed_wav,
            export_dir=str(out_parent)
        )

        # Store result video path
        project.settings.result_video_path = output_mp4_path

        if progress_cb:
            progress_cb(100, f"Export successfully completed: {output_mp4_path}")
        return True

    def generate_fast_preview(
        self,
        project: Project,
        progress_cb: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """
        Ultra-fast preview generation BEFORE full export!
        1. Mixes dubbed Khmer audio + original background audio (with ducking).
        2. Muxes original video stream and mixed audio with FFmpeg `-c:v copy` (~1s, zero quality loss).
        3. Returns the path to the temporary preview video MP4.
        Live subtitles and live watermark logo are overlaid natively on the player widget!
        """
        if not project.video_path or not os.path.exists(project.video_path):
            raise FileNotFoundError("សូមនាំចូលវីដេអូដើមជាមុនសិន!")

        # Ensure any missing TTS clips are generated (using TTSFitter retry >= 3)
        from app.dubbing.tts_fitter import TTSFitter
        fitter = TTSFitter(temp_dir=self.audio_engine.temp_dir)
        missing_tts = [
            s for s in project.segments
            if (s.text_km or s.text_src) and (not s.tts_file or not os.path.exists(s.tts_file) or os.path.getsize(s.tts_file) <= 180)
        ]
        if missing_tts:
            fitter.process_all_segments(
                missing_tts,
                progress_cb=lambda pct, msg: progress_cb(2 + int(pct * 0.12), msg) if progress_cb else None
            )

        has_any_tts = any(bool(s.tts_file and os.path.exists(s.tts_file) and os.path.getsize(s.tts_file) > 180) for s in project.segments)
        if not has_any_tts:
            raise ValueError("មិនទាន់មានសំឡេង AI ខ្មែរនៅឡើយទេ។ សូមដំណើរការ '៣. បង្កើតសំឡេង AI ខ្មែរ' ជាមុនសិន!")

        total_duration = self.audio_engine.get_media_duration(project.video_path)

        # Step 1: Assemble continuous dubbed voice track
        if progress_cb:
            progress_cb(15, "1/3 Assembling Khmer speech clips...")

        preview_dubbed_wav = str(Path(TEMP_DIR) / "preview_dubbed.wav")
        self.audio_engine.build_dubbed_audio_track(
            total_duration_sec=total_duration,
            segments=project.segments,
            output_wav=preview_dubbed_wav,
            progress_cb=lambda pct, msg: progress_cb(15 + int(pct * 0.25), msg) if progress_cb else None
        )

        # Step 2: Ensure background audio is extracted
        if not project.audio_path or not os.path.exists(project.audio_path):
            if progress_cb:
                progress_cb(40, "Extracting stereo background audio...")
            project.audio_path = self.audio_engine.extract_original_full_audio(project.video_path)

        # Step 3: Mix audio with ducking
        if progress_cb:
            progress_cb(55, "2/3 Mixing audio with ducking & volume levels...")

        preview_mixed_wav = str(Path(TEMP_DIR) / "preview_mixed.wav")
        self.audio_engine.mix_audio_with_ducking(
            original_audio_path=project.audio_path,
            dubbed_audio_path=preview_dubbed_wav,
            segments=project.segments,
            original_vol_factor=project.settings.original_volume,
            original_voice_vol=getattr(project.settings, "original_voice_volume", 0.0),
            background_vol=getattr(project.settings, "background_volume", 0.30),
            dubbed_vol_factor=project.settings.dubbed_volume,
            mute_original=project.settings.mute_original,
            keep_background=getattr(project.settings, "keep_background", True),
            ducking_db=project.settings.ducking_db,
            fade_ms=project.settings.ducking_fade_ms,
            output_wav=preview_mixed_wav,
            progress_cb=lambda pct, msg: progress_cb(55 + int(pct * 0.25), msg) if progress_cb else None
        )

        # Step 4: Fast-mux into temporary preview MP4 (-c:v copy, instant ~1s)
        if progress_cb:
            progress_cb(85, "3/3 Fast-muxing video and dubbed audio (~1s)...")

        preview_mp4 = str(Path(TEMP_DIR) / "preview_result.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", project.video_path,
            "-i", preview_mixed_wav,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            preview_mp4
        ]
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace"
        )
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg preview muxing failed: {res.stderr}")

        if progress_cb:
            progress_cb(100, "Preview ready!")
        return preview_mp4
