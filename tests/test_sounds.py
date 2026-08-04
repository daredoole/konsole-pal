import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from konsole_pal.sounds import SAMPLE_RATE, play_sound, sound_path, synthesize_sound


class SoundTests(unittest.TestCase):
    def test_synthesized_sounds_are_valid_mono_wav_files(self):
        durations = {"done": (0.80, 0.84), "attention": (0.41, 0.45)}
        with tempfile.TemporaryDirectory() as directory:
            for kind, bounds in durations.items():
                path = Path(directory) / f"{kind}.wav"
                synthesize_sound(kind, path)
                with wave.open(str(path), "rb") as source:
                    self.assertEqual(source.getnchannels(), 1)
                    self.assertEqual(source.getsampwidth(), 2)
                    self.assertEqual(source.getframerate(), SAMPLE_RATE)
                    duration = source.getnframes() / source.getframerate()
                    self.assertGreaterEqual(duration, bounds[0])
                    self.assertLessEqual(duration, bounds[1])

    def test_sound_path_is_cached_in_private_runtime_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            first = sound_path("done", env)
            mtime = first.stat().st_mtime_ns
            second = sound_path("done", env)
            self.assertEqual(first, second)
            self.assertEqual(second.stat().st_mtime_ns, mtime)

    def test_volume_changes_wave_amplitude_and_cache_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            quiet = sound_path("done", env, volume=0.2)
            loud = sound_path("done", env, volume=1.0)
            self.assertNotEqual(quiet, loud)
            with wave.open(str(quiet), "rb") as source:
                quiet_peak = max(abs(value) for value in array("h", source.readframes(-1)))
            with wave.open(str(loud), "rb") as source:
                loud_peak = max(abs(value) for value in array("h", source.readframes(-1)))
            self.assertLess(quiet_peak, loud_peak)

    @patch("konsole_pal.sounds.subprocess.Popen")
    @patch("konsole_pal.sounds.find_audio_player", return_value="/usr/bin/pw-play")
    def test_playback_is_asynchronous(self, _find, popen):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            self.assertTrue(play_sound("attention", env))
            command = popen.call_args.args[0]
            self.assertEqual(command[0], "/usr/bin/pw-play")
            self.assertTrue(command[1].endswith("attention-v2-100.wav"))

    @patch("konsole_pal.sounds.find_audio_player")
    def test_sound_can_be_disabled(self, find):
        self.assertFalse(play_sound("done", {"KONSOLE_PAL_SOUND": "off"}))
        find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
