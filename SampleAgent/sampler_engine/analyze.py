"""Tempo, onsets and silence structure of a decoded WAV.

Analysis runs on a 22.05 kHz mono downmix (librosa's default working rate);
every value it returns is in seconds, so it stays valid for the full-rate
stereo audio that actually gets sliced.
"""

from . import compat  # noqa: F401

import librosa
import numpy as np
import soundfile as sf

ANALYSIS_SR = 22050


def load_stereo(path):
    """Full-rate audio exactly as decoded: (frames, channels) float32."""
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    return audio, sr


def load_mono(path, sr=ANALYSIS_SR):
    y, sr = librosa.load(path, sr=sr, mono=True)
    return y, sr


def analyze(path, top_db=30.0):
    y, sr = load_mono(path)
    duration = float(len(y) / sr)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).astype(float)

    onset_times = librosa.onset.onset_detect(
        y=y, sr=sr, backtrack=True, units="time"
    ).astype(float)

    intervals = librosa.effects.split(y, top_db=top_db, frame_length=2048, hop_length=512)
    silence_split = (intervals / float(sr)).astype(float)

    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    rms = float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0

    return {
        "duration_s": duration,
        "analysis_sr": sr,
        "bpm": round(tempo, 3),
        "bpm_method": "librosa.beat.beat_track (estimate, no downbeat detection)",
        "beat_times": beat_times.tolist(),
        "beat_count": int(len(beat_times)),
        "median_beat_interval_s": (
            float(np.median(np.diff(beat_times))) if len(beat_times) > 1 else None
        ),
        "onset_times": onset_times.tolist(),
        "onset_count": int(len(onset_times)),
        "loud_regions": silence_split.tolist(),
        "top_db": float(top_db),
        "peak": peak,
        "peak_dbfs": (20 * np.log10(peak) if peak > 0 else float("-inf")),
        "rms_dbfs": (20 * np.log10(rms) if rms > 0 else float("-inf")),
        "data_source": "measured:librosa",
    }


def apply_bpm_override(analysis, bpm, offset=None):
    """Replace the detected beat grid with a user-supplied tempo.

    Detection is an estimate; when the real tempo is known, a synthetic grid
    is strictly better for bar-length slicing. offset defaults to the first
    detected onset, which is the closest thing to a downbeat available here.
    """
    if offset is None:
        onsets = analysis.get("onset_times") or [0.0]
        offset = float(onsets[0])
    interval = 60.0 / float(bpm)
    grid = np.arange(offset, analysis["duration_s"], interval)

    analysis = dict(analysis)
    analysis["bpm"] = round(float(bpm), 3)
    analysis["bpm_method"] = f"user override (--bpm), grid offset {offset:.4f}s"
    analysis["beat_times"] = grid.tolist()
    analysis["beat_count"] = int(len(grid))
    analysis["median_beat_interval_s"] = interval
    analysis["grid_offset_s"] = float(offset)
    return analysis


def vocal_free_regions(path, vocal_ref, ratio_db=-10.0, min_len=0.4, floor_db=-45.0):
    """Vokalin sustugu araliklar — chop'lanabilir enstrumantal boslukler.

    Kaynagi ve ayrilmis vokal stem'ini kare kare karsilastirir. Mutlak esik
    yerine ORAN kullanilir: ayirma sizintisi enstrumantalin cok altinda kalir,
    gercek vokal kalmaz. Olculdu 2026-09-02: mutlak esik sizintiyi vokal sanip
    bir kaydin enstrumantal payini %0 gosteriyordu.
    """
    inst, sr_i = load_mono(path)
    voc, sr_v = load_mono(vocal_ref)
    n = min(len(inst), len(voc))
    if n < sr_i:
        return []
    frame, hop = 1024, 512
    count = (n - frame) // hop
    if count < 4:
        return []
    ei = np.array([np.sqrt(np.mean(inst[i*hop:i*hop+frame]**2)) for i in range(count)])
    ev = np.array([np.sqrt(np.mean(voc[i*hop:i*hop+frame]**2)) for i in range(count)])
    live = ei > ei.max() * 10 ** (floor_db / 20)
    ratio = 20 * np.log10((ev + 1e-9) / (ei + 1e-9))
    singing = (ratio > ratio_db) & live

    regions, start = [], None
    for i, on in enumerate(singing):
        if not on and start is None:
            start = i
        elif on and start is not None:
            a, b = start * hop / sr_i, i * hop / sr_i
            if b - a >= min_len:
                regions.append({"start": round(a, 6), "end": round(b, 6)})
            start = None
    if start is not None:
        a, b = start * hop / sr_i, count * hop / sr_i
        if b - a >= min_len:
            regions.append({"start": round(a, 6), "end": round(b, 6)})
    return regions
