"""Read one audio file and report what is measurably in it.

Nothing here is inferred from the file NAME. Every number comes out of the
samples themselves, and anything the signal does not support is returned as
None with a reason -- a wrong BPM is worse than no BPM when the next step
downloads records based on it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
import math
import warnings

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore", category=UserWarning, module="librosa")

# librosa 0.10.1 (the version installed here) calls scipy.signal.hann inside
# beat_track's trim step. SciPy removed that alias in 1.13 and the installed
# SciPy is 1.17, so beat tracking raises AttributeError on every file. The
# window itself still exists under scipy.signal.windows; putting the alias back
# restores librosa's intended behaviour without upgrading a library that other
# tools in this machine's environment already depend on at 0.10.1.
import scipy.signal  # noqa: E402

if not hasattr(scipy.signal, "hann"):
    scipy.signal.hann = scipy.signal.windows.hann

import librosa  # noqa: E402
import librosa.feature  # noqa: E402

# The autocorrelation tempo estimator moved between librosa versions and the
# package lazy-loads its submodules, so librosa.feature.rhythm is not always
# reachable by attribute access. Resolve it once, here, rather than in the hot
# path of every file.
try:  # librosa >= 0.10.1
    import librosa.feature.rhythm as _rhythm

    _tempo_estimator = _rhythm.tempo
except (ImportError, AttributeError):  # older layout
    _tempo_estimator = librosa.feature.tempo

# The chop window the producer works in. A 78rpm record at 140 BPM is a 70 BPM
# chop, so tempo is folded by octaves before it is judged against this.
CHOP_BPM_MIN = 68.0
CHOP_BPM_MAX = 98.0
_CHOP_CENTER = math.sqrt(CHOP_BPM_MIN * CHOP_BPM_MAX)

# Feature sample rate. 22050 keeps everything up to 11 kHz, which is above the
# ceiling of the shellac-era material this is pointed at.
FEATURE_SR = 22050
# Long files are analysed from a window starting a quarter in -- intros are
# unrepresentative, and reading 6 minutes to describe a loop is waste.
ANALYSIS_SECONDS = 90.0
# Below this a file is a one-shot: tempo and key are not defined for it.
MIN_MUSICAL_SECONDS = 1.5
# A file this short or shorter is treated as a loop, and its tempo is taken
# from its LENGTH rather than from beat tracking. Measured against 156 library
# files whose names state their own BPM: beat tracking scored 31% correct,
# loop length scored 81% (octave-tolerant, which is what a chop needs). Beat
# tracking cannot work on a 2.5-second file -- one bar is four beats and the
# tracker needs eight.
LOOP_MAX_SECONDS = 30.0
# Bar counts a loop file plausibly holds, and the tempo range a candidate must
# land in to be considered at all.
LOOP_BAR_COUNTS = (1, 2, 4, 8, 16)
PLAUSIBLE_BPM_MIN = 55.0
PLAUSIBLE_BPM_MAX = 210.0
# Beat tracking that finds fewer than this many beats has not found a pulse.
MIN_BEATS = 8
# Inter-beat intervals wobbling more than this are not a steady tempo.
MAX_IBI_CV = 0.22
# The winning key must beat the runner-up by this much correlation, or the
# reading is called ambiguous instead of guessed.
MIN_KEY_MARGIN = 0.06

_PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler probe-tone profiles.
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


@dataclass(frozen=True)
class Reading:
    path: str
    name: str
    ok: bool
    error: str | None = None

    # Container facts, read from the header, not decoded.
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    subtype: str | None = None

    # Level. dBFS, measured on the analysis window at the file's own rate.
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    crest_db: float | None = None
    clipped_samples: int | None = None
    # Quietest 5% of the frames that are not digital silence. A shellac
    # transfer sits high here (hiss), a modern render sits low. Digital
    # silence is excluded because it would read as -240 dB and say nothing
    # about the recording -- it is reported separately as silence_share.
    noise_floor_dbfs: float | None = None
    silence_share: float | None = None
    # 0.0 = the two channels are identical (a mono record in a stereo file).
    stereo_width: float | None = None

    # Rhythm.
    tempo_bpm: float | None = None
    tempo_source: str | None = None
    tempo_reason: str | None = None
    chop_bpm: float | None = None
    in_chop_range: bool | None = None
    onset_rate_hz: float | None = None

    # Pitch.
    key: str | None = None
    key_reason: str | None = None
    key_confidence: float | None = None

    # Timbre. This is what makes one record sound like the continuation of
    # another, so it is the part the matcher leans on.
    centroid_hz: float | None = None
    rolloff85_hz: float | None = None
    bandwidth_hz: float | None = None
    # Share of energy below 120 Hz and above 8 kHz. Old transfers have almost
    # nothing up top; that absence is the signature, not a defect.
    low_ratio: float | None = None
    air_ratio: float | None = None
    # Harmonic energy over total, from an HPSS split. Loops sit high, drum
    # breaks sit low.
    harmonic_ratio: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _is_ableton_compressed(path: Path) -> bool:
    """True for Live Pack samples in Ableton's own AIFF-C codec.

    Live Packs ship .aif files whose compression tag is "able". No decoder
    outside Live reads them -- libsndfile, ffmpeg and CoreAudio all refuse --
    so the measurement is impossible, not merely failed. Saying which of the
    two it is matters: the first is a fact about the library, the second would
    be a bug here.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
    except OSError:
        return False
    return head[:4] == b"FORM" and head[8:12] == b"AIFC" and b"able" in head


def _db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), 1e-12))


def _fold_to_chop(bpm: float) -> float:
    """Halve or double the tempo into the octave closest to the chop window."""
    best = bpm
    best_dist = abs(math.log2(bpm / _CHOP_CENTER))
    for k in (-3, -2, -1, 1, 2, 3):
        cand = bpm * (2.0**k)
        if cand <= 0:
            continue
        dist = abs(math.log2(cand / _CHOP_CENTER))
        if dist < best_dist:
            best, best_dist = cand, dist
    return best


def _estimate_key(y: np.ndarray, sr: int) -> tuple[str | None, str | None, float | None]:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    if chroma.size == 0:
        return None, "chroma_empty", None
    vec = chroma.mean(axis=1)
    if vec.sum() <= 0:
        return None, "no_pitched_energy", None
    vec = vec / vec.sum()

    scores: list[tuple[float, str]] = []
    for i in range(12):
        rolled = np.roll(vec, -i)
        for profile, mode in ((_MAJOR_PROFILE, "maj"), (_MINOR_PROFILE, "min")):
            corr = float(np.corrcoef(rolled, profile)[0, 1])
            if math.isnan(corr):
                corr = -1.0
            scores.append((corr, f"{_PITCHES[i]} {mode}"))
    scores.sort(reverse=True)
    top, runner = scores[0], scores[1]
    margin = top[0] - runner[0]
    if margin < MIN_KEY_MARGIN:
        return None, f"ambiguous(margin={margin:.3f})", round(margin, 4)
    return top[1], None, round(margin, 4)


def _autocorrelation_hint(onset_env: np.ndarray, sr: int) -> float:
    """A tempo prior from the onset envelope alone. Used to choose the octave;
    on its own it is right about half the time, which is not enough to report."""
    ac = _tempo_estimator(onset_envelope=onset_env, sr=sr, aggregate=None, start_bpm=82.0)
    return float(np.median(ac)) if ac.size else 120.0


def _loop_length_tempo(duration: float, hint: float) -> tuple[float | None, str | None]:
    """Tempo from the file's own length, assuming it holds a whole number of bars.

    A one-bar loop is four beats, so bpm = bars * 4 * 60 / duration. Length
    fixes the tempo exactly but not the octave -- 2.67 s is one bar at 90 and
    two bars at 180 -- so the octave is picked with the autocorrelation hint.
    """
    candidates = [
        (bars, (bars * 4) * 60.0 / duration)
        for bars in LOOP_BAR_COUNTS
        if PLAUSIBLE_BPM_MIN <= (bars * 4) * 60.0 / duration <= PLAUSIBLE_BPM_MAX
    ]
    if not candidates:
        return None, f"no_plausible_bar_count({duration:.2f}s)"
    bars, bpm = min(candidates, key=lambda c: abs(math.log2(c[1] / hint)))
    return round(bpm, 2), None


def _beat_tracked_tempo(
    onset_env: np.ndarray, sr: int
) -> tuple[float | None, str | None]:
    tempo, beats = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, start_bpm=82.0, units="time"
    )
    tempo = float(np.atleast_1d(tempo)[0])
    if tempo <= 0:
        return None, "tracker_returned_zero"
    if len(beats) < MIN_BEATS:
        return None, f"too_few_beats({len(beats)})"
    ibi = np.diff(beats)
    if ibi.size == 0 or ibi.mean() <= 0:
        return None, "no_beat_intervals"
    cv = float(ibi.std() / ibi.mean())
    if cv > MAX_IBI_CV:
        return None, f"unsteady_pulse(cv={cv:.2f})"
    return round(tempo, 2), None


def _estimate_tempo(
    y: np.ndarray, sr: int, window_seconds: float
) -> tuple[float | None, str | None, str | None, float | None]:
    """Returns (tempo, source, reason, chop_bpm)."""
    percussive = librosa.effects.percussive(y)
    onset_env = librosa.onset.onset_strength(y=percussive, sr=sr)
    if not np.any(onset_env):
        return None, None, "no_onsets", None

    if window_seconds <= LOOP_MAX_SECONDS:
        hint = _autocorrelation_hint(onset_env, sr)
        tempo, reason = _loop_length_tempo(window_seconds, hint)
        source = "loop_length"
    else:
        tempo, reason = _beat_tracked_tempo(onset_env, sr)
        source = "beat_tracker"

    if tempo is None:
        return None, source, reason, None
    return tempo, source, None, round(_fold_to_chop(tempo), 2)


def read_file(path: str | Path) -> Reading:
    path = Path(path)
    name = path.name
    try:
        info = sf.info(str(path))
    except Exception as exc:  # unreadable container, missing codec
        if _is_ableton_compressed(path):
            return Reading(
                path=str(path),
                name=name,
                ok=False,
                error="ableton_compressed: only Live can decode this file",
            )
        return Reading(path=str(path), name=name, ok=False, error=f"header: {exc}")

    duration = float(info.frames) / float(info.samplerate) if info.samplerate else 0.0
    offset = duration * 0.25 if duration > ANALYSIS_SECONDS * 1.5 else 0.0

    try:
        raw, native_sr = librosa.load(
            str(path), sr=None, mono=False, offset=offset, duration=ANALYSIS_SECONDS
        )
    except Exception as exc:
        return Reading(path=str(path), name=name, ok=False, error=f"decode: {exc}")

    if raw.size == 0:
        return Reading(
            path=str(path), name=name, ok=False, error="decode: empty window",
            duration_s=round(duration, 3), sample_rate=int(info.samplerate),
            channels=int(info.channels), subtype=info.subtype,
        )

    stereo = raw if raw.ndim == 2 else raw[np.newaxis, :]
    mono = stereo.mean(axis=0)

    peak = float(np.max(np.abs(stereo)))
    rms = float(np.sqrt(np.mean(np.square(mono))))
    clipped = int(np.sum(np.abs(stereo) >= 0.999))

    frame = max(1, int(native_sr * 0.05))
    trimmed = mono[: (len(mono) // frame) * frame]
    noise_floor = None
    silence_share = None
    if trimmed.size:
        frames = trimmed.reshape(-1, frame)
        frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
        silent = frame_rms < 1e-6
        silence_share = round(float(silent.mean()), 4)
        voiced = frame_rms[~silent]
        if voiced.size:
            noise_floor = _db(float(np.percentile(voiced, 5)))

    if stereo.shape[0] >= 2:
        side = stereo[0] - stereo[1]
        mid = stereo[0] + stereo[1]
        mid_e = float(np.mean(np.square(mid)))
        width = float(np.mean(np.square(side)) / mid_e) if mid_e > 0 else 0.0
        width = round(min(width, 1.0), 4)
    else:
        width = 0.0

    y = librosa.resample(mono, orig_sr=native_sr, target_sr=FEATURE_SR)
    if y.size < FEATURE_SR // 8:
        return Reading(
            path=str(path), name=name, ok=False, error="decode: window too short",
            duration_s=round(duration, 3), sample_rate=int(info.samplerate),
            channels=int(info.channels), subtype=info.subtype,
        )

    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=FEATURE_SR, n_fft=2048)
    energy = np.square(spec).sum(axis=1)
    total = float(energy.sum())
    if total > 0:
        low_ratio = round(float(energy[freqs < 120].sum() / total), 4)
        air_ratio = round(float(energy[freqs > 8000].sum() / total), 4)
    else:
        low_ratio = air_ratio = None

    centroid = float(np.median(librosa.feature.spectral_centroid(S=spec, sr=FEATURE_SR)))
    rolloff = float(
        np.median(librosa.feature.spectral_rolloff(S=spec, sr=FEATURE_SR, roll_percent=0.85))
    )
    bandwidth = float(np.median(librosa.feature.spectral_bandwidth(S=spec, sr=FEATURE_SR)))

    harm, perc = librosa.decompose.hpss(spec)
    h_e = float(np.square(harm).sum())
    p_e = float(np.square(perc).sum())
    harmonic_ratio = round(h_e / (h_e + p_e), 4) if (h_e + p_e) > 0 else None

    onsets = librosa.onset.onset_detect(y=y, sr=FEATURE_SR, units="time")
    onset_rate = round(len(onsets) / (len(y) / FEATURE_SR), 3)

    window_seconds = len(y) / FEATURE_SR
    if window_seconds < MIN_MUSICAL_SECONDS:
        tempo = chop = tempo_source = None
        tempo_reason = f"one_shot({window_seconds:.2f}s)"
        key, key_reason, key_conf = None, tempo_reason, None
    else:
        tempo, tempo_source, tempo_reason, chop = _estimate_tempo(
            y, FEATURE_SR, window_seconds
        )
        key, key_reason, key_conf = _estimate_key(y, FEATURE_SR)

    in_range = None
    if chop is not None:
        in_range = CHOP_BPM_MIN <= chop <= CHOP_BPM_MAX

    return Reading(
        path=str(path),
        name=name,
        ok=True,
        duration_s=round(duration, 3),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        subtype=info.subtype,
        peak_dbfs=round(_db(peak), 2),
        rms_dbfs=round(_db(rms), 2),
        crest_db=round(_db(peak) - _db(rms), 2),
        clipped_samples=clipped,
        noise_floor_dbfs=round(noise_floor, 2) if noise_floor is not None else None,
        silence_share=silence_share,
        stereo_width=width,
        tempo_bpm=tempo,
        tempo_source=tempo_source,
        tempo_reason=tempo_reason,
        chop_bpm=chop,
        in_chop_range=in_range,
        onset_rate_hz=onset_rate,
        key=key,
        key_reason=key_reason,
        key_confidence=key_conf,
        centroid_hz=round(centroid, 1),
        rolloff85_hz=round(rolloff, 1),
        bandwidth_hz=round(bandwidth, 1),
        low_ratio=low_ratio,
        air_ratio=air_ratio,
        harmonic_ratio=harmonic_ratio,
    )


AUDIO_SUFFIXES = {".wav", ".aif", ".aiff", ".aifc", ".flac", ".mp3", ".m4a", ".ogg"}


def iter_audio(root: str | Path) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root]
    found = [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and not p.name.startswith("._")
    ]
    return found
