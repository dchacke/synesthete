"""Play WAV files with whatever the operating system provides."""

import os
import select
import shutil
import subprocess
import sys
import threading

LINUX_PLAYERS = (['paplay'], ['pw-play'], ['aplay', '-q'])
MIXER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mixer.js')


def command():
    """The command that plays a WAV file given as its last argument, or None."""
    if sys.platform == 'darwin':
        return ['afplay']
    if sys.platform.startswith('linux'):
        for candidate in LINUX_PLAYERS:
            if shutil.which(candidate[0]):
                return candidate
    return None


class Player:
    """Starts sounds and can silence all of them at once. Sounds may overlap,
    which is how the ambient drone crossfades from one segment to the next.
    On macOS they go through mixer.js, which can also fade them out, and
    fades out whatever is still playing when Sublime quits, over
    `fade_seconds`. Elsewhere each sound is a process of its own, which
    plays on after Sublime has quit. On Windows a new sound cuts off the
    previous one."""

    def __init__(self, fade_seconds=0.0):
        self._command = command()
        self._fade_seconds = fade_seconds
        self._mixing = sys.platform == 'darwin'  # until the mixer won't start
        self._mixer = None
        self._processes = []
        self._lock = threading.Lock()
        self._finished = False

    @property
    def fades(self):
        """Whether fade() can fade out sounds that have already started."""
        return self._mixing

    def play(self, path, solo=False):
        """Play `path` in the background. With `solo`, silence everything
        else first."""
        if self._finished:
            return
        if solo:
            self.stop()
        if sys.platform == 'win32':
            import winsound
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            winsound.PlaySound(path, flags)
            return
        if self._tell('play 0 {}'.format(path)):
            return
        if self._command is None:
            raise RuntimeError('No audio player found. Install paplay, pw-play, or aplay.')
        process = subprocess.Popen(
            self._command + [path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        with self._lock:
            self._processes = [p for p in self._processes if p.poll() is None]
            self._processes.append(process)

    def fade(self, seconds, due=None):
        """Fade out everything playing over `seconds`, along with `due`,
        (delay, path) of a sound to play `delay` seconds from now. Sounds
        that are fading already carry on as they were. Returns whether it
        could; see fades."""
        if due is None and self._mixing and (self._mixer is None or self._mixer.poll() is not None):
            return True  # nothing is playing
        lines = ['play {:.4f} {}'.format(*due)] if due else []
        return self._tell('\n'.join(lines + ['fade {}'.format(seconds)]))

    def finish(self, path):
        """Play `path`, and nothing after it. Like the sounds already playing,
        it plays on after Sublime has quit. On Windows sounds end with
        Sublime, so this only keeps new ones from starting."""
        self._finished = True
        if sys.platform == 'win32' or self._command is None:
            return
        subprocess.Popen(
            self._command + [path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)

    def stop(self):
        if sys.platform == 'win32':
            import winsound
            winsound.PlaySound(None, 0)
            return
        with self._lock:
            mixer, self._mixer = self._mixer, None
            processes, self._processes = self._processes, []
        if mixer is not None:
            try:
                mixer.stdin.write(b'stop\n')
                mixer.stdin.close()
            except OSError:
                pass  # it's gone already
        for process in processes:
            if process.poll() is None:
                process.terminate()

    def _tell(self, text):
        """Send the mixer `text`, starting it if need be. False if there's
        no mixer, or it just died."""
        with self._lock:
            if not self._mixing:
                return False
            if self._mixer is None or self._mixer.poll() is not None:
                self._mixer = self._start_mixer()
                if self._mixer is None:
                    self._mixing = False  # play sounds the plain way from now on
                    return False
            try:
                self._mixer.stdin.write(text.encode('utf-8') + b'\n')
                self._mixer.stdin.flush()
            except OSError:
                self._mixer = None  # start another next time
                return False
        return True

    def _start_mixer(self):
        """Start mixer.js, in a session of its own so that Sublime quitting
        doesn't take it down, and wait until it's ready. None if it won't."""
        try:
            mixer = subprocess.Popen(
                ['osascript', '-l', 'JavaScript', MIXER, str(self._fade_seconds)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError:
            return None
        ready, _, _ = select.select([mixer.stdout], [], [], 5.0)
        if not ready or mixer.stdout.readline() != b'ready\n':
            mixer.kill()
            mixer.wait()
            return None
        return mixer
