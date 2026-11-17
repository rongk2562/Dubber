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

        # Step 0: Ensure missing audio clips are generated (skip redundant online network sync)
        missing_tts = [
            s for s in project.segments
            if (s.text_km or s.text_src) and (not s.tts_file or not os.path.exists(s.tts_file))
        ]
        if missing_tts:
            from app.tts_engine import TTSEngine
            tts_engine = TTSEngine()
            for idx, seg in enumerate(missing_tts):
                if progress_cb:
                    progress_cb(5 + int((idx / len(missing_tts)) * 5), f"Generating missing voice {idx+1}/{len(missing_tts)}...")
                try:
                    tts_engine.generate_segment_tts(seg)
                except Exception as e:
                    print(f"[ExportEngine] Warning generating TTS for segment {seg.id}: {e}")

        # Step 1: Build continuous dubbed voice track
        if progress_cb:
            progress_cb(15, "1/4 Assembling continuous Khmer voice track...")
        
        dubbed_wav = self.audio_engine.build_dubbed_audio_track(
            total_duration_sec=total_duration,
            segments=project.segments,
            progress_cb=lambda pct, msg: progress_cb(10 + int(pct * 0.2), msg) if progress_cb else None
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
            dubbed_vol_factor=project.settings.dubbed_volume,
            ducking_db=project.settings.ducking_db,
            fade_ms=project.settings.ducking_fade_ms,
            progress_cb=lambda pct, msg: progress_cb(40 + int(pct * 0.25), msg) if progress_cb else None
        )

        # Optional WAV export
        if export_wav_path:
            import shutil
            shutil.copy(final_mixed_wav, export_wav_path)

        # Step 4: Generate SRT subtitles
        if progress_cb:
            progress_cb(70, "3/4 Generating Khmer subtitle file (SRT)...")
        srt_content = generate_srt(project.segments, language="km")
        
        srt_file = export_srt_path or str(Path(output_mp4_path).with_suffix(".srt"))
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write(srt_content)

        # Step 5: Mux / Render MP4 Video with optional Subtitles and Logo Overlay
        if progress_cb:
            progress_cb(80, "4/4 Muxing final video with FFmpeg...")

        norm_srt = srt_file.replace("\\", "/").replace(":", "\\:")
        has_logo = (
            project.settings.logo_enabled
            and bool(project.settings.logo_path)
            and os.path.exists(project.settings.logo_path)
        )

        pos = project.settings.logo_position
        mx = project.settings.logo_margin_x
        my = project.settings.logo_margin_y
        if pos == "top_left":
            pos_expr = f"{mx}:{my}"
        elif pos == "bottom_left":
            pos_expr = f"{mx}:H-h-{my}"
        elif pos == "bottom_right":
            pos_expr = f"W-w-{mx}:H-h-{my}"
        elif pos == "center":
            pos_expr = f"(W-w)/2:(H-h)/2"
        else: # top_right
            pos_expr = f"W-w-{mx}:{my}"

        logo_w = project.settings.logo_width
        logo_op = max(0.1, min(project.settings.logo_opacity, 1.0))

        if burn_subtitles and has_logo:
            # Case 1: Subtitles + Logo
            f_complex = (
                f"[0:v]subtitles='{norm_srt}':force_style='Fontname={project.settings.subtitle_font},Fontsize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=1.5,Shadow=1'[subbed];"
                f"[2:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={logo_op:.2f}[logo];"
                f"[subbed][logo]overlay={pos_expr}[v_out]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-i", project.settings.logo_path,
                "-filter_complex", f_complex,
                "-c:v", "libx264",
                "-crf", "22",
                "-preset", "veryfast",
                "-threads", "0",
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "[v_out]",
                "-map", "1:a:0",
                "-shortest",
                output_mp4_path
            ]
        elif has_logo:
            # Case 2: Logo only
            f_complex = (
                f"[2:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={logo_op:.2f}[logo];"
                f"[0:v][logo]overlay={pos_expr}[v_out]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-i", project.settings.logo_path,
                "-filter_complex", f_complex,
                "-c:v", "libx264",
                "-crf", "22",
                "-preset", "veryfast",
                "-threads", "0",
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "[v_out]",
                "-map", "1:a:0",
                "-shortest",
                output_mp4_path
            ]
        elif burn_subtitles:
            # Case 3: Subtitles only
            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-vf", f"subtitles='{norm_srt}':force_style='Fontname={project.settings.subtitle_font},Fontsize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=1.5,Shadow=1'",
                "-c:v", "libx264",
                "-crf", "22",
                "-preset", "veryfast",
                "-threads", "0",
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_mp4_path
            ]
        else:
            # Case 4: Audio mux only (Ultra-fast direct copy)
            cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
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
            # If complex filtering failed, fallback to audio stream copy
            fallback_cmd = [
                "ffmpeg", "-y",
                "-i", project.video_path,
                "-i", final_mixed_wav,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_mp4_path
            ]
            subprocess.run(
                fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                encoding="utf-8", errors="replace"
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

        # Ensure any missing TTS clips are generated
        missing_tts = [
            s for s in project.segments
            if (s.text_km or s.text_src) and (not s.tts_file or not os.path.exists(s.tts_file))
        ]
        if missing_tts:
            from app.tts_engine import TTSEngine
            tts_engine = TTSEngine()
            for idx, seg in enumerate(missing_tts):
                if progress_cb:
                    progress_cb(2 + int((idx / len(missing_tts)) * 10), f"Generating voice clip {idx+1}/{len(missing_tts)}...")
                try:
                    tts_engine.generate_segment_tts(seg)
                except Exception as e:
                    print(f"[ExportEngine] Note generating TTS for segment {seg.id}: {e}")

        has_any_tts = any(bool(s.tts_file and os.path.exists(s.tts_file)) for s in project.segments)
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
            dubbed_vol_factor=project.settings.dubbed_volume,
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
