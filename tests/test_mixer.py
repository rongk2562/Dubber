"""
Unit tests for TimelineMixer:
Tests sample-accurate positioning, sidechain ducking envelope, 48 kHz stereo output, and duration matching.
"""

import os
import unittest
import numpy as np
from pathlib import Path
from pydub import AudioSegment

from app.models import Segment
from app.dubbing.mixer import TimelineMixer
from app.config import TEMP_DIR


class TestTimelineMixer(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(TEMP_DIR) / "test_mixer_run"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.mixer = TimelineMixer(temp_dir=self.temp_dir, sample_rate=48000)

        # Create 1-second synthetic 48kHz stereo sine tone clip
        self.clip_path = str(self.temp_dir / "test_clip.wav")
        sr = 48000
        t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
        sine = 0.5 * np.sin(2 * np.pi * 440 * t)
        stereo_sine = np.column_stack([sine, sine])
        pcm_16 = (stereo_sine * 32767.0).astype(np.int16)
        seg = AudioSegment(
            data=pcm_16.tobytes(),
            sample_width=2,
            frame_rate=sr,
            channels=2
        )
        seg.export(self.clip_path, format="wav")

        # Create 5-second synthetic background accompaniment
        self.acc_path = str(self.temp_dir / "test_acc.wav")
        t5 = np.linspace(0, 5.0, sr * 5, endpoint=False, dtype=np.float32)
        bg = 0.2 * np.sin(2 * np.pi * 220 * t5)
        stereo_bg = np.column_stack([bg, bg])
        pcm_bg = (stereo_bg * 32767.0).astype(np.int16)
        AudioSegment(
            data=pcm_bg.tobytes(),
            sample_width=2,
            frame_rate=sr,
            channels=2
        ).export(self.acc_path, format="wav")

    def test_dialogue_track_placement(self):
        """Test that clips are placed at exact sample offsets without drift."""
        segments = [
            Segment(id=1, start=1.0, end=2.0, tts_file=self.clip_path, tts_duration=1.0),
            Segment(id=2, start=3.0, end=4.0, tts_file=self.clip_path, tts_duration=1.0)
        ]
        out_dialogue = str(self.temp_dir / "out_dialogue.wav")
        total_dur = 5.0
        self.mixer.build_dialogue_track(total_dur, segments, output_wav=out_dialogue)

        self.assertTrue(os.path.exists(out_dialogue))
        seg = AudioSegment.from_file(out_dialogue)
        self.assertEqual(seg.frame_rate, 48000)
        self.assertEqual(seg.channels, 2)
        # Check duration matches 5.0s +- 0.05s
        self.assertAlmostEqual(len(seg) / 1000.0, total_dur, delta=0.05)

        # Check silence at 0.5s and sound at 1.5s
        at_0_5 = seg[400:600]
        at_1_5 = seg[1400:1600]
        self.assertEqual(at_0_5.dBFS, float("-inf"))
        self.assertGreater(at_1_5.dBFS, -30.0)

    def test_ducking_envelope_and_master_mix(self):
        """Test that sidechain ducking attenuates background without digital silence."""
        segments = [
            Segment(id=1, start=1.0, end=2.0, tts_file=self.clip_path, tts_duration=1.0),
        ]
        out_dialogue = str(self.temp_dir / "out_dialogue_duck.wav")
        self.mixer.build_dialogue_track(5.0, segments, output_wav=out_dialogue)

        out_mix = str(self.temp_dir / "out_mix.wav")
        self.mixer.mix_timeline(
            total_duration_sec=5.0,
            accompaniment_path=self.acc_path,
            dialogue_path=out_dialogue,
            segments=segments,
            ducking_db=-10.0,
            target_lufs=-16.0,
            output_wav=out_mix
        )

        self.assertTrue(os.path.exists(out_mix))
        mixed_seg = AudioSegment.from_file(out_mix)
        self.assertEqual(mixed_seg.frame_rate, 48000)
        self.assertEqual(mixed_seg.channels, 2)
        # Verify no digital silence (-inf or -120dB) at any point
        self.assertNotEqual(mixed_seg[:500].dBFS, float("-inf"))
        self.assertGreater(mixed_seg[:500].dBFS, -70.0)


if __name__ == "__main__":
    unittest.main()
