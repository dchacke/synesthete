import io
import os
import shutil
import subprocess
import sys
import time
import unittest
import wave
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import player  # noqa: E402

WAV = os.path.join(ROOT, 'samples', 'harp', 'A2.wav')


class Pipe(io.BytesIO):
    """Keeps what was written after it's closed."""

    def close(self):
        self.written = self.getvalue()
        super().close()


class Process:
    def __init__(self, args, ready=b'ready\n', **options):
        self.args, self.options = args, options
        self.stdin, self.stdout = Pipe(), io.BytesIO(ready)
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self):
        return self.returncode


class MixerTest(unittest.TestCase):
    def setUp(self):
        self.started = []
        for patch in (mock.patch.object(player.sys, 'platform', 'darwin'),
                      mock.patch.object(player.select, 'select',
                                        lambda r, w, x, timeout: (r, w, x)),
                      mock.patch.object(player.subprocess, 'Popen', self.popen)):
            patch.start()
            self.addCleanup(patch.stop)
        self.ready = b'ready\n'

    def popen(self, args, **options):
        process = Process(args, self.ready if args[0] == 'osascript' else b'', **options)
        self.started.append(process)
        return process

    def test_sounds_go_through_the_mixer_on_macos(self):
        p = player.Player(4.5)
        p.play('a.wav')
        p.play('b.wav')
        self.assertTrue(p.fade(4.5, (1.25, 'c.wav')))
        [mixer] = self.started
        self.assertEqual(mixer.args, ['osascript', '-l', 'JavaScript', player.MIXER, '4.5'])
        self.assertTrue(mixer.options['start_new_session'])  # so quitting can't cut it off
        p.stop()
        self.assertEqual(mixer.stdin.written.decode().splitlines(), [
            'play 0 a.wav', 'play 0 b.wav', 'play 1.2500 c.wav', 'fade 4.5', 'stop'])
        self.assertTrue(p.fades)

    def test_fading_with_nothing_playing_starts_no_mixer(self):
        self.assertTrue(player.Player(4.5).fade(4.5))
        self.assertEqual(self.started, [])

    def test_falls_back_to_afplay_if_the_mixer_wont_start(self):
        self.ready = b''
        p = player.Player(4.5)
        p.play('a.wav')
        self.assertFalse(p.fades)
        self.assertFalse(p.fade(4.5))
        mixer, afplay = self.started
        self.assertEqual(mixer.returncode, -9)
        self.assertEqual(afplay.args, ['afplay', 'a.wav'])
        self.assertTrue(afplay.options['start_new_session'])

    def test_starts_the_mixer_again_if_it_died(self):
        p = player.Player(4.5)
        p.play('a.wav')
        self.started[0].returncode = 1
        p.play('b.wav')
        first, second = self.started
        self.assertEqual(second.args[0], 'osascript')
        self.assertEqual(second.stdin.getvalue(), b'play 0 b.wav\n')


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('osascript'), 'macOS only')
class MixerScriptTest(unittest.TestCase):
    """The real mixer.js. Whether it can be heard isn't checked, so this also
    runs where there's no sound."""

    def start(self, seconds):
        mixer = subprocess.Popen(
            ['osascript', '-l', 'JavaScript', player.MIXER, str(seconds)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(mixer.__exit__, None, None, None)  # closes its pipes and waits
        self.addCleanup(mixer.kill)
        self.assertEqual(mixer.stdout.readline(), b'ready\n')
        return mixer

    def exits_within(self, mixer, seconds):
        began = time.monotonic()
        mixer.wait(10)
        return time.monotonic() - began < seconds

    def test_fades_out_whatever_is_playing_when_sublime_quits(self):
        with wave.open(WAV) as w:
            length = w.getnframes() / w.getframerate()
        mixer = self.start(0.3)
        mixer.stdin.write('play 0 {}\n'.format(WAV).encode())
        mixer.stdin.close()
        self.assertTrue(self.exits_within(mixer, min(1.5, length - 0.5)))

    def test_a_fade_under_way_carries_on_as_it_was(self):
        mixer = self.start(30)
        mixer.stdin.write('play 0 {}\nfade 0.3\n'.format(WAV).encode())
        mixer.stdin.close()
        self.assertTrue(self.exits_within(mixer, 1.5))  # not 30 seconds

    def test_stop_silences_everything_at_once(self):
        mixer = self.start(30)
        mixer.stdin.write('play 0 {}\nstop\n'.format(WAV).encode())
        mixer.stdin.flush()
        self.assertTrue(self.exits_within(mixer, 0.5))


if __name__ == '__main__':
    unittest.main()
