from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf

from .settings import settings

ANALYSIS_CONTRACT_VERSION = "2026-07-29.3"


class AudioDecodeError(Exception):
    """Raised when a supported upload cannot be decoded as audio."""


class AudioDurationError(ValueError):
    """Raised before analysis when decoded audio exceeds the configured limit."""


def _duration_error(max_duration_seconds: float) -> AudioDurationError:
    return AudioDurationError(
        "Audio duration exceeds the configured "
        f"{max_duration_seconds:g} second limit."
    )


def _load_audio(
    file_path: str | Path,
    max_duration_seconds: float | None = None,
) -> Tuple[np.ndarray, int]:
    path = str(file_path)
    try:
        with sf.SoundFile(path) as source:
            sample_rate = int(source.samplerate)
            if sample_rate > settings.max_sample_rate:
                raise AudioDurationError(
                    "Audio sample rate exceeds the configured "
                    f"{settings.max_sample_rate} Hz limit."
                )
            if source.channels > settings.max_audio_channels:
                raise AudioDurationError(
                    "Audio channel count exceeds the configured "
                    f"{settings.max_audio_channels} channel limit."
                )
            if (
                max_duration_seconds is not None
                and sample_rate > 0
                and source.frames / sample_rate > max_duration_seconds
            ):
                raise _duration_error(max_duration_seconds)
            audio = source.read(always_2d=True, dtype="float32")
    except AudioDurationError:
        raise
    except (RuntimeError, ValueError, OSError):
        try:
            audio, sample_rate = librosa.load(
                path,
                sr=None,
                mono=False,
                duration=(
                    None
                    if max_duration_seconds is None
                    else max_duration_seconds + 1.0
                ),
            )
            if audio.ndim == 1:
                audio = audio[np.newaxis, :]
            audio = audio.T.astype(np.float32)
        except Exception as exc:
            raise AudioDecodeError("The uploaded file could not be decoded as audio.") from exc

    if audio.ndim == 1:
        audio = audio[:, np.newaxis]

    decoded = np.asarray(audio, dtype=np.float32)
    if sample_rate > settings.max_sample_rate:
        raise AudioDurationError(
            "Audio sample rate exceeds the configured "
            f"{settings.max_sample_rate} Hz limit."
        )
    if decoded.shape[1] > settings.max_audio_channels:
        raise AudioDurationError(
            "Audio channel count exceeds the configured "
            f"{settings.max_audio_channels} channel limit."
        )
    if not np.all(np.isfinite(decoded)):
        raise AudioDecodeError("The decoded audio contains non-finite samples.")
    if (
        max_duration_seconds is not None
        and sample_rate > 0
        and len(decoded) / sample_rate > max_duration_seconds
    ):
        raise _duration_error(max_duration_seconds)
    return decoded, int(sample_rate)


def _amplitude_dbfs(value: float) -> Optional[float]:
    if not np.isfinite(value) or value <= 0.0:
        return None
    return float(20.0 * np.log10(value))


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


def _integrated_lufs(audio: np.ndarray, sample_rate: int) -> Optional[float]:
    """Measure gated integrated loudness through pyloudnorm's BS.1770 path."""
    if (
        sample_rate <= 0
        or audio.ndim != 2
        or not 1 <= audio.shape[1] <= 5
        or len(audio) < int(round(0.4 * sample_rate))
        or _rms(audio) <= 0.0
    ):
        return None

    try:
        loudness = pyln.Meter(sample_rate).integrated_loudness(audio)
    except (ValueError, IndexError, ZeroDivisionError):
        return None
    return float(loudness) if np.isfinite(loudness) else None


def _rounded(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None else round(value, digits)


def _channel_measurements(audio: np.ndarray) -> List[Dict[str, Any]]:
    channel_names = ("L", "R")
    measurements: List[Dict[str, Any]] = []

    for index in range(audio.shape[1]):
        channel = audio[:, index]
        peak = float(np.max(np.abs(channel))) if channel.size else 0.0
        rms = _rms(channel)
        dc_offset = float(np.mean(channel)) if channel.size else 0.0
        measurements.append(
            {
                "index": index,
                "label": (
                    channel_names[index]
                    if index < len(channel_names)
                    else f"CH {index + 1}"
                ),
                "sample_peak_dbfs": _rounded(_amplitude_dbfs(peak)),
                "rms_dbfs": _rounded(_amplitude_dbfs(rms)),
                "dc_offset": round(dc_offset, 8),
            }
        )

    return measurements


def analyze_decoded_audio(audio: np.ndarray, sample_rate: int) -> Dict[str, Any]:
    channels = int(audio.shape[1])
    duration_seconds = float(len(audio) / sample_rate) if sample_rate else 0.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = _rms(audio)
    peak_dbfs = _amplitude_dbfs(peak)
    rms_dbfs = _amplitude_dbfs(rms)
    crest_factor_db = (
        None
        if peak_dbfs is None or rms_dbfs is None
        else peak_dbfs - rms_dbfs
    )

    if audio.size == 0 or peak <= 0.0:
        analysis_status = "silent"
    elif duration_seconds < settings.min_analysis_seconds:
        analysis_status = "too_short"
    else:
        analysis_status = "ok"

    integrated_lufs = (
        _integrated_lufs(audio, sample_rate)
        if analysis_status == "ok"
        else None
    )

    return {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "duration_seconds": round(duration_seconds, 3),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_peak_dbfs": _rounded(peak_dbfs),
        "rms_dbfs": _rounded(rms_dbfs),
        "crest_factor_db": _rounded(crest_factor_db),
        "integrated_lufs": _rounded(integrated_lufs),
        "channel_measurements": _channel_measurements(audio),
        "analysis_status": analysis_status,
    }


def analyze_audio(
    file_path: str | Path,
    max_duration_seconds: float | None = None,
) -> Dict[str, Any]:
    audio, sample_rate = _load_audio(file_path, max_duration_seconds)
    return analyze_decoded_audio(audio, sample_rate)
