"""Measurement limits, the only part of the service configuration the
analysis core needs. Environment overrides keep the same names as the
SubverseLab Mix Check service so a tuned deployment behaves the same here."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive number.")
    return value


@dataclass
class Settings:
    max_analysis_seconds: int = _positive_int("MAX_ANALYSIS_SECONDS", 6 * 60)
    max_sample_rate: int = _positive_int("MAX_SAMPLE_RATE", 192_000)
    max_audio_channels: int = _positive_int("MAX_AUDIO_CHANNELS", 8)
    min_analysis_seconds: float = _positive_float("MIN_ANALYSIS_SECONDS", 0.5)


settings = Settings()
