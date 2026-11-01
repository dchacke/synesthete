"""Play recorded instruments at any pitch by resampling the nearest
recording. The recordings are from VSCO 2 Community Edition (public domain);
see tools/prepare_samples.py.
"""

import json
import math
import os
import struct
from collections import OrderedDict

import numpy as np

RATE = 32000


def read_wav(path):
    """16-bit PCM samples of a WAV file as numpy int16s. Sublime's Python lacks
    audioop, which the wave module needs, so this reads the chunks itself."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('not a WAV file: ' + path)
    pos = 12
    channels = bits = None
    while pos + 8 <= len(data):
        kind, size = struct.unpack('<4sI', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        if kind == b'fmt ':
            channels, _, _, _, bits = struct.unpack('<HIIHH', body[2:16])
        elif kind == b'data':
            if (channels, bits) != (1, 16):
                raise ValueError('expected 16-bit mono: ' + path)
            return np.frombuffer(body[:len(body) // 2 * 2], dtype='<i2')
        pos += 8 + size + (size & 1)
    raise ValueError('no audio in ' + path)


class Library:
    """The instruments in a folder made by tools/prepare_samples.py."""

    def __init__(self, folder, cache_size=96):
        self.folder = folder
        with open(os.path.join(folder, 'samples.json')) as f:
            self.manifest = json.load(f)  # instrument -> {name: Hz or None}
        self._recordings = {}
        self._notes = OrderedDict()  # most recently used last
        self._cache_size = cache_size

    def names(self, instrument):
        return sorted(self.manifest[instrument])

    def note(self, instrument, freq=None, name=None, ratio=1.0, seconds=None):
        """Mono floats of `instrument`. Pitched instruments play `freq` Hz
        from the nearest recording; unpitched ones play recording `name`
        sped up by `ratio`. Cut to `seconds` with a short fade."""
        recordings = self.manifest[instrument]
        if freq is not None:
            pitched = {n: f for n, f in recordings.items() if f}
            name = min(pitched, key=lambda n: abs(math.log(freq / pitched[n])))
            ratio = freq / pitched[name]
        elif name is None:
            name = sorted(recordings)[0]
        key = (instrument, name, round(ratio, 4), seconds)
        cached = self._notes.get(key)
        if cached is not None:
            self._notes.move_to_end(key)
            return cached
        note = resample(self._recording(instrument, name), ratio, seconds)
        self._notes[key] = note
        if len(self._notes) > self._cache_size:
            self._notes.popitem(last=False)
        return note

    def _recording(self, instrument, name):
        key = (instrument, name)
        if key not in self._recordings:
            self._recordings[key] = read_wav(os.path.join(self.folder, instrument, name + '.wav'))
        return self._recordings[key]


def resample(pcm, ratio, seconds=None):
    """Play `pcm` `ratio` times faster (higher), as floats, linearly
    interpolated, optionally cut to `seconds` with a 50 ms fade."""
    pcm = np.asarray(pcm, dtype=float)
    length = int((len(pcm) - 1) / ratio)
    if seconds is not None:
        length = min(length, int(seconds * RATE))
    pos = np.arange(length) * ratio
    j = pos.astype(int)
    a = pcm[j]
    out = (a + (pcm[j + 1] - a) * (pos - j)) * (1 / 32768)
    if seconds is not None and length == int(seconds * RATE):
        fade = min(length, int(0.05 * RATE))
        out[length - fade:] *= 1 - np.arange(1, fade + 1) / fade
    return out
