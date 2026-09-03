"""SubverseLab Mix Check, the measurement core only.

Ported from the SubverseLab Launchpad service (subverse-mix-analyzer) on
2026-09-03: the same analyzer, mix analyzer and genre profiles, without the
FastAPI/Firebase/quota layers. Every number is a direct signal measurement or
pyloudnorm's BS.1770 loudness; see analyze_mix for what is deliberately not
returned (true-peak guesses, custom loudness range, genre classification).
"""
from pathlib import Path

from .analyzer import ANALYSIS_CONTRACT_VERSION, AudioDecodeError, AudioDurationError, analyze_audio
from .genre_profiles import GenreProfileError, GenreProfileStore, build_genre_profile
from .mix_analyzer import MIX_CONTRACT_VERSION, analyze_mix, extract_mix_features
from .settings import settings

DEFAULT_PROFILES_PATH = Path(__file__).parent / "data" / "genre_profiles.json"

__all__ = [
    "ANALYSIS_CONTRACT_VERSION", "MIX_CONTRACT_VERSION", "DEFAULT_PROFILES_PATH",
    "AudioDecodeError", "AudioDurationError", "GenreProfileError", "GenreProfileStore",
    "analyze_audio", "analyze_mix", "build_genre_profile", "extract_mix_features", "settings",
]
