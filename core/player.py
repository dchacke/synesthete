"""Play WAV files with whatever the operating system provides."""

import shutil
import subprocess
import sys
import threading

LINUX_PLAYERS = (['paplay'], ['pw-play'], ['aplay', '-q'])


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
    On Windows a new sound cuts off the previous one."""

    def __init__(self):
        self._command = command()
        self._processes = []
        self._lock = threading.Lock()
        self._finished = False

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
        if self._command is None:
            raise RuntimeError('No audio player found. Install paplay, pw-play, or aplay.')
        process = subprocess.Popen(
            self._command + [path], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._lock:
            self._processes = [p for p in self._processes if p.poll() is None]
            self._processes.append(process)

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
            processes, self._processes = self._processes, []
        for process in processes:
            if process.poll() is None:
                process.terminate()
