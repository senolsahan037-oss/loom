#!/usr/bin/env python3
"""Measure one recording's musical structure.

What this does NOT do is store the music. No audio is copied, no note sequence
is written that could reconstruct a melody. What comes out is the shape of the
piece -- how fast, how long, where the sections fall, how the low end moves
against the mid -- which is the part that generalises across a genre and the
only part worth learning from.

Every field carries how it was derived and how confident that derivation is,
because a number without its method is a guess wearing a lab coat.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

warnings.filterwarnings("ignore")


def _patch_scipy_for_librosa() -> None:
    """librosa 0.10 still calls window functions SciPy moved under .windows.

    Restoring the aliases is the right fix here rather than pinning SciPy down or
    librosa up: the functions are unchanged, only their home moved.
    """
    import scipy.signal
    for name in ("hann", "hamming", "blackman", "bartlett", "boxcar", "triang"):
        if not hasattr(scipy.signal, name) and hasattr(scipy.signal.windows, name):
            setattr(scipy.signal, name, getattr(scipy.signal.windows, name))


_patch_scipy_for_librosa()

SAMPLE_RATE = 22050
# Kick and bass live below this; snare, percussion and most of the voice above.
# Crude beside real stem separation, but it needs no model and it is honest
# about being a band split rather than an instrument.
LOW_BAND_HZ = 120
MID_BAND = (200, 4000)


@dataclass
class Measurement:
    source: str
    duration_seconds: float
    tempo_bpm: float
    tempo_folded: float
    tempo_confidence: float
    tempo_agreement: str
    grid_steadiness: float
    beat_count: int
    sections: list
    section_count: int
    low_onsets_per_bar: float
    mid_onsets_per_bar: float
    low_to_mid_ratio: float
    low_on_grid_share: float
    mid_on_grid_share: float
    tonal_centre: str
    tonal_confidence: float
    method: dict


FOLD_RANGE = (70.0, 140.0)


def _grid_steadiness(beat_times) -> float:
    """How evenly spaced the chosen grid is.

    Worth reporting, but it is NOT tempo confidence: the tracker picks a regular
    grid by construction, so this came out above 0.6 for all 150 works in the
    first corpus and separated nothing. Kept as its own field, under its own name.
    """
    import numpy as np
    if len(beat_times) < 4:
        return 0.0
    intervals = np.diff(beat_times)
    if intervals.mean() <= 0:
        return 0.0
    return float(max(0.0, min(1.0, 1.0 - (intervals.std() / intervals.mean()) * 4)))


def _fold(bpm: float) -> float:
    """Bring a tempo into one octave so 88 and 176 stop looking like two genres."""
    if bpm <= 0:
        return 0.0
    while bpm < FOLD_RANGE[0]:
        bpm *= 2
    while bpm >= FOLD_RANGE[1]:
        bpm /= 2
    return round(bpm, 1)


def _tempo_agreement(primary: float, second: float) -> tuple[float, str]:
    """Confidence from two independent estimators, not from one talking to itself.

    Beat tracking and autocorrelation fail differently. Agreeing outright is
    strong evidence; agreeing only after halving or doubling means the pulse is
    real but which level counts as the beat is genuinely ambiguous -- common in
    this music, and better reported than hidden.
    """
    if primary <= 0 or second <= 0:
        return 0.0, "no_estimate"
    ratio = max(primary, second) / min(primary, second)
    if ratio <= 1.03:
        return 0.9, "direct"
    if abs(ratio - 2.0) <= 0.06 or abs(ratio - 3.0) <= 0.09:
        return 0.5, "octave_only"
    return 0.2, "disagree"


def _band_onsets(y, sr, low: float | None, high: float | None):
    import librosa
    import numpy as np
    spectrum = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    mask = np.ones_like(freqs, dtype=bool)
    if low is not None:
        mask &= freqs >= low
    if high is not None:
        mask &= freqs <= high
    band = spectrum[mask, :]
    envelope = librosa.onset.onset_strength(S=librosa.amplitude_to_db(band, ref=np.max), sr=sr)
    return librosa.onset.onset_detect(onset_envelope=envelope, sr=sr, hop_length=512, units="time")


def measure(path: str | Path) -> Measurement:
    import librosa
    import numpy as np

    path = Path(path)
    y, sr = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    duration = float(len(y) / sr)

    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    # A second, independent estimate: autocorrelation over the onset envelope.
    onset_envelope = librosa.onset.onset_strength(y=y, sr=sr)
    second = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=onset_envelope, sr=sr))[0])
    confidence, agreement = _tempo_agreement(tempo, second)
    steadiness = _grid_steadiness(beats)
    bars = max(1.0, len(beats) / 4.0)

    low = _band_onsets(y, sr, None, LOW_BAND_HZ)
    mid = _band_onsets(y, sr, *MID_BAND)

    # How much of each band lands on the beat grid at all. Measuring against
    # downbeats only was the wrong question: at nine to twelve low onsets a bar
    # this band is carrying a moving bass line, not a kick pattern, so almost
    # none of it falls on a downbeat and the number said nothing. On-grid share
    # does separate a programmed part from a played one.
    tolerance = (60.0 / tempo) * 0.15 if tempo > 0 else 0.1

    def on_grid(onsets) -> float:
        if not len(onsets) or not len(beats):
            return 0.0
        return float(sum(1 for onset in onsets
                         if np.min(np.abs(beats - onset)) <= tolerance) / len(onsets))

    boundaries = librosa.segment.agglomerative(
        librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13), k=max(2, min(12, int(duration // 20))))
    section_times = sorted({round(float(t), 2) for t in
                            librosa.frames_to_time(boundaries, sr=sr)})

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    centre = names[int(np.argmax(chroma))]
    ordered = np.sort(chroma)[::-1]
    tonal_confidence = float((ordered[0] - ordered[1]) / ordered[0]) if ordered[0] > 0 else 0.0

    return Measurement(
        source=path.name,
        duration_seconds=round(duration, 1),
        tempo_bpm=round(tempo, 1),
        tempo_folded=_fold(tempo),
        tempo_confidence=round(confidence, 2),
        tempo_agreement=agreement,
        grid_steadiness=round(steadiness, 2),
        beat_count=len(beats),
        sections=section_times,
        section_count=len(section_times),
        low_onsets_per_bar=round(len(low) / bars, 2),
        mid_onsets_per_bar=round(len(mid) / bars, 2),
        low_to_mid_ratio=round(len(low) / max(1, len(mid)), 3),
        low_on_grid_share=round(on_grid(low), 3),
        mid_on_grid_share=round(on_grid(mid), 3),
        tonal_centre=centre,
        tonal_confidence=round(tonal_confidence, 3),
        method={
            "tempo": "librosa.beat.beat_track, checked against an autocorrelation "
                     "estimate. Confidence is agreement between the two: 0.9 direct, "
                     "0.5 they agree only at an octave, 0.2 they disagree. "
                     "tempo_folded brings the value into 70-140 so a doubled reading "
                     "and its true tempo stop looking like two different genres.",
            "sections": "agglomerative clustering of MFCCs, k scaled to duration",
            "bands": f"low <{LOW_BAND_HZ}Hz, mid {MID_BAND[0]}-{MID_BAND[1]}Hz; "
                     "a band split, not instrument separation",
            "tonal_centre": "strongest CQT chroma bin in 12-TET. Arabesk and other "
                            "makam-based music uses intervals 12-TET cannot name, so "
                            "this is a pitch centre, NOT a makam and NOT a mode.",
        },
    )


def as_dict(result: Measurement) -> dict:
    return asdict(result)
