"""Play sounds on Windows, overlapping, by mixing them into one stream of
our own. winsound plays one sound at a time, so each cut off the one before.

The stream goes through waveOut. It's the oldest of Windows' audio APIs, but
since Vista it feeds the same audio engine as the newest, follows the
default device, and takes 16-bit stereo at our rate as it is, with none of
the COM that WASAPI or DirectSound would need through ctypes. Its weakness
is latency, which doesn't matter here: every sound is late by the same
fraction of a second, so the bars still meet.
"""

import ctypes
import sys
import threading
from ctypes import wintypes

import numpy as np

from .sampler import RATE

CHUNK = int(0.04 * RATE)  # frames per buffer
BUFFERS = 4  # buffers queued at a time, so a busy plugin host doesn't make it skip

WAVE_MAPPER = 0xFFFFFFFF  # the default device
CALLBACK_EVENT = 0x00050000
WHDR_DONE = 0x1


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [('wFormatTag', wintypes.WORD), ('nChannels', wintypes.WORD),
                ('nSamplesPerSec', wintypes.DWORD), ('nAvgBytesPerSec', wintypes.DWORD),
                ('nBlockAlign', wintypes.WORD), ('wBitsPerSample', wintypes.WORD),
                ('cbSize', wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', wintypes.DWORD),
                ('dwBytesRecorded', wintypes.DWORD), ('dwUser', ctypes.c_size_t),
                ('dwFlags', wintypes.DWORD), ('dwLoops', wintypes.DWORD),
                ('lpNext', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]


if sys.platform == 'win32':
    winmm = ctypes.WinDLL('winmm')
    kernel32 = ctypes.WinDLL('kernel32')
    winmm.waveOutOpen.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.UINT,
                                  ctypes.POINTER(WAVEFORMATEX), ctypes.c_size_t,
                                  ctypes.c_size_t, wintypes.DWORD)
    for _name in ('waveOutPrepareHeader', 'waveOutUnprepareHeader', 'waveOutWrite'):
        getattr(winmm, _name).argtypes = (wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT)
    winmm.waveOutReset.argtypes = winmm.waveOutClose.argtypes = (wintypes.HANDLE,)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                      wintypes.LPCWSTR)
    kernel32.SetEvent.argtypes = kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)


class Mixer:
    """Plays WAVs written by music.write_wav, each `delay` seconds from now,
    and fades them out the way mixer.js does on macOS: a sound that is
    fading carries on fading as it was. The stream opens when there is
    something to play and closes when it's all over, so it doesn't keep
    Windows from sleeping."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sounds = []  # of _Sound, playing or due
        self._frame = 0  # the next frame to queue
        self._stream = None  # (handle, event, buffers) while open
        self._reset = False  # whether to drop what's queued, as stop() asks

    @property
    def playing(self):
        """Whether anything is playing or due."""
        with self._lock:
            return bool(self._sounds)

    def play(self, delay, path):
        """Raises OSError if there's no device to play on."""
        frames = _read(path)
        with self._lock:
            if self._stream is None:
                self._stream = _open()
                threading.Thread(target=self._run, args=(self._stream,),
                                 name='Synesthete audio', daemon=True).start()
            start = max(self._frame, self._now() + int(round(delay * RATE)))
            self._sounds.append(_Sound(frames, start))

    def fade(self, seconds):
        with self._lock:
            now = self._now()
            for sound in self._sounds:
                if sound.fade is None:
                    sound.fade = (now, max(1, int(seconds * RATE)))

    def stop(self):
        """Silence everything at once."""
        with self._lock:
            self._sounds = []
            if self._stream is not None:
                self._reset = True
                kernel32.SetEvent(self._stream[1])

    def _run(self, stream):
        """Keep the device's buffers full until everything has played."""
        handle, event, buffers = stream
        try:
            while True:
                with self._lock:
                    if self._reset:
                        self._reset = False
                        winmm.waveOutReset(handle)  # hands every buffer back
                    free = [b for b in buffers if b.free]
                    if not self._sounds and len(free) == len(buffers):
                        self._stream = None  # the next sound opens another
                        return
                    for buffer in free:
                        if self._sounds:
                            buffer.write(handle, self._mix())
                kernel32.WaitForSingleObject(event, 200)  # a buffer is done
        finally:
            with self._lock:
                if self._stream is stream:  # it failed
                    self._stream = None
            winmm.waveOutReset(handle)
            for buffer in buffers:
                buffer.release(handle)
            winmm.waveOutClose(handle)
            kernel32.CloseHandle(event)

    def _now(self):
        """The frame being heard about now: the next to queue, less those
        queued but not yet played."""
        queued = sum(not b.free for b in self._stream[2]) if self._stream else 0
        return self._frame - queued * CHUNK

    def _mix(self):
        """The next CHUNK frames of every sound, as 16-bit stereo PCM."""
        first, last = self._frame, self._frame + CHUNK
        out = np.zeros((CHUNK, 2))
        playing = []
        for sound in self._sounds:
            a, b = max(first, sound.start), min(last, sound.end)
            if a < b:
                part = sound.frames[a - sound.start:b - sound.start].astype(float)
                if sound.fade is not None:
                    since, length = sound.fade
                    x = np.clip((np.arange(a, b) - since) / length, 0.0, 1.0)
                    part *= ((1 - x) ** 2)[:, None]
                out[a - first:b - first] += part
            if last < sound.end and (sound.fade is None or last < sum(sound.fade)):
                playing.append(sound)
        self._sounds = playing
        self._frame = last
        return np.clip(out, -32768, 32767).astype('<i2').tobytes()


class _Sound:
    def __init__(self, frames, start):
        self.frames = frames  # int16, a row of left and right per frame
        self.start = start
        self.end = start + len(frames)
        self.fade = None  # (frame it began, frames it lasts) once fading


class _Buffer:
    def __init__(self, handle):
        self.data = ctypes.create_string_buffer(CHUNK * 4)
        self.header = WAVEHDR(lpData=ctypes.addressof(self.data), dwBufferLength=CHUNK * 4)
        self.queued = False
        _check(winmm.waveOutPrepareHeader(handle, self.header, ctypes.sizeof(WAVEHDR)))

    @property
    def free(self):
        return not self.queued or bool(self.header.dwFlags & WHDR_DONE)

    def write(self, handle, pcm):
        ctypes.memmove(self.data, pcm, len(pcm))
        self.header.dwFlags &= ~WHDR_DONE
        self.queued = True
        _check(winmm.waveOutWrite(handle, self.header, ctypes.sizeof(WAVEHDR)))

    def release(self, handle):
        winmm.waveOutUnprepareHeader(handle, self.header, ctypes.sizeof(WAVEHDR))


def _open():
    """Open the default device for our 16-bit stereo: (handle, event,
    buffers). The event is set whenever the device is done with a buffer."""
    fmt = WAVEFORMATEX(wFormatTag=1, nChannels=2, nSamplesPerSec=RATE,
                       nAvgBytesPerSec=RATE * 4, nBlockAlign=4, wBitsPerSample=16)
    event = kernel32.CreateEventW(None, False, False, None)
    handle = wintypes.HANDLE()
    try:
        _check(winmm.waveOutOpen(ctypes.byref(handle), WAVE_MAPPER, fmt, event, 0,
                                 CALLBACK_EVENT))
    except OSError:
        kernel32.CloseHandle(event)
        raise
    return handle, event, [_Buffer(handle) for _ in range(BUFFERS)]


def _check(result):
    if result:
        raise OSError('Windows could not play sound (waveOut error {})'.format(result))


def _read(path):
    """The frames of a WAV written by music.write_wav."""
    with open(path, 'rb') as f:
        data = f.read()[44:]  # past its header
    return np.frombuffer(data[:len(data) // 4 * 4], dtype='<i2').reshape(-1, 2)
