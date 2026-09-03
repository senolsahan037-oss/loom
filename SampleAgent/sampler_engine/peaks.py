"""Min/max peak envelopes for waveform drawing, computed on demand.

The UI asks for a window (start, end) and a bucket count; reading only that
frame range keeps zooming cheap regardless of how long the source is.
"""

import numpy as np
import soundfile as sf


def envelope(path, start=0.0, end=None, buckets=1200):
    with sf.SoundFile(path) as handle:
        sr = handle.samplerate
        total = len(handle)
        first = max(0, int(start * sr))
        last = total if end is None else min(total, int(end * sr))
        if last <= first:
            return {"min": [], "max": [], "sample_rate": sr, "frames": 0}
        handle.seek(first)
        block = handle.read(last - first, dtype="float32", always_2d=True)

    mono = block.mean(axis=1)
    buckets = max(1, min(int(buckets), len(mono)))
    edges = np.linspace(0, len(mono), buckets + 1).astype(int)

    lows = np.empty(buckets, dtype=np.float32)
    highs = np.empty(buckets, dtype=np.float32)
    for i in range(buckets):
        chunk = mono[edges[i]:edges[i + 1]]
        if chunk.size == 0:
            lows[i] = highs[i] = 0.0
        else:
            lows[i] = chunk.min()
            highs[i] = chunk.max()

    return {
        "min": np.round(lows, 5).tolist(),
        "max": np.round(highs, 5).tolist(),
        "sample_rate": sr,
        "frames": last - first,
    }
