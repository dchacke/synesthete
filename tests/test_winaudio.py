import os
import sys
import tempfile
import time
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import music, winaudio  # noqa: E402
from core.winaudio import CHUNK  # noqa: E402


def sound(level, frames, start=0):
    return winaudio._Sound(np.full((frames, 2), level, dtype='<i2'), start)


def mixed(mixer):
    return np.frombuffer(mixer._mix(), dtype='<i2').reshape(-1, 2)


class MixTest(unittest.TestCase):
    """The mixing, which runs anywhere; only the device needs Windows."""

    def test_sounds_overlap(self):
        mixer = winaudio.Mixer()
        mixer._sounds = [sound(1000, CHUNK), sound(-300, CHUNK // 2)]
        chunk = mixed(mixer)
        self.assertEqual(chunk[0].tolist(), [700, 700])
        self.assertEqual(chunk[-1].tolist(), [1000, 1000])
        self.assertEqual(mixer._sounds, [])  # both are over

    def test_a_sound_waits_for_its_turn(self):
        mixer = winaudio.Mixer()
        later = sound(1000, CHUNK, start=CHUNK + 10)
        mixer._sounds = [later]
        self.assertEqual(np.abs(mixed(mixer)).max(), 0)
        chunk = mixed(mixer)
        self.assertEqual((chunk[9].tolist(), chunk[10].tolist()), ([0, 0], [1000, 1000]))
        self.assertEqual(mixer._sounds, [later])  # its last 10 frames are still to come

    def test_loud_overlaps_clip_rather_than_wrap_around(self):
        mixer = winaudio.Mixer()
        mixer._sounds = [sound(30000, CHUNK), sound(30000, CHUNK)]
        self.assertEqual(mixed(mixer).min(), 32767)

    def test_fading_sounds_fade_away_and_are_let_go(self):
        mixer = winaudio.Mixer()
        mixer._sounds = [sound(10000, 10 * CHUNK)]
        mixer.fade(2 * CHUNK / winaudio.RATE)
        first = mixed(mixer)
        self.assertEqual(first[0, 0], 10000)
        self.assertLess(first[-1, 0], 10000 / 4 + 10)  # (1 - 1/2) ** 2, as in mixer.js
        mixer.fade(0.001)  # a fade under way carries on as it was
        self.assertGreater(mixed(mixer)[0, 0], 0)
        self.assertEqual(mixer._sounds, [])

    def test_stop_with_nothing_open(self):
        mixer = winaudio.Mixer()
        mixer._sounds = [sound(1, CHUNK)]
        mixer.stop()
        self.assertFalse(mixer.playing)


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class DeviceTest(unittest.TestCase):
    """The real device. Whether it can be heard isn't checked."""

    def test_plays_overlapping_sounds_then_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'quiet.wav')
            silence = [0.0] * (winaudio.RATE // 5)
            music.write_wav(path, silence, silence)
            mixer = winaudio.Mixer()
            try:
                mixer.play(0, path)
            except OSError as e:
                self.skipTest(e)
            mixer.play(0.1, path)
            self.assertTrue(mixer.playing)
            began = time.monotonic()
            while mixer._stream is not None and time.monotonic() - began < 5:
                time.sleep(0.05)
            self.assertIsNone(mixer._stream)  # closed once it's all over
            self.assertGreater(time.monotonic() - began, 0.25)  # not before it played


if __name__ == '__main__':
    unittest.main()
