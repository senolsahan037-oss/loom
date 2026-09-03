from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from subverse_mix.analyzer import AudioDecodeError, AudioDurationError, analyze_audio


REMOVED_FIELDS = {
    "peak",
    "peak_dbfs",
    "rms",
    "true_peak",
    "true_peak_db",
    "loudness_analysis",
    "signal_statistics",
    "frequency_balance",
    "spectral_analysis",
    "multiband_stereo",
    "stereo_correlation",
    "stereo_analysis",
}


def test_analyzer_returns_only_the_minimal_measurement_contract(
    tmp_path: Path,
) -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate) / sample_rate
    left = 0.1 * np.sin(2 * np.pi * 997 * time)
    right = 0.08 * np.sin(2 * np.pi * 997 * time)
    path = tmp_path / "minimal.wav"
    sf.write(path, np.column_stack((left, right)), sample_rate, subtype="FLOAT")

    result = analyze_audio(path)

    assert set(result) == {
        "analysis_contract_version",
        "duration_seconds",
        "sample_rate",
        "channels",
        "sample_peak_dbfs",
        "rms_dbfs",
        "crest_factor_db",
        "integrated_lufs",
        "channel_measurements",
        "analysis_status",
    }
    assert result["analysis_contract_version"] == "2026-07-29.3"
    assert REMOVED_FIELDS.isdisjoint(result)
    assert result["sample_rate"] == sample_rate
    assert result["channels"] == 2
    assert result["analysis_status"] == "ok"


def test_channel_measurements_are_direct_signal_values(tmp_path: Path) -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate) / sample_rate
    left = 0.1 * np.sin(2 * np.pi * 997 * time)
    right = 0.01 + 0.05 * np.sin(2 * np.pi * 997 * time)
    path = tmp_path / "channels.wav"
    sf.write(path, np.column_stack((left, right)), sample_rate, subtype="FLOAT")

    channels = analyze_audio(path)["channel_measurements"]

    assert len(channels) == 2
    assert channels[0]["sample_peak_dbfs"] == pytest.approx(-20.0, abs=0.002)
    assert channels[0]["rms_dbfs"] == pytest.approx(-23.0103, abs=0.002)
    assert channels[0]["dc_offset"] == pytest.approx(0.0, abs=1e-7)
    assert channels[1]["dc_offset"] == pytest.approx(0.01, abs=1e-7)


def test_silent_audio_returns_null_instead_of_an_artificial_db_floor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros((48_000, 2), dtype=np.float32), 48_000)

    result = analyze_audio(path)

    assert result["analysis_status"] == "silent"
    assert result["sample_peak_dbfs"] is None
    assert result["rms_dbfs"] is None
    assert result["crest_factor_db"] is None
    assert result["integrated_lufs"] is None
    assert all(
        channel["sample_peak_dbfs"] is None
        and channel["rms_dbfs"] is None
        and channel["dc_offset"] == 0.0
        for channel in result["channel_measurements"]
    )


def test_very_short_audio_has_explicit_status_and_no_fake_lufs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "short.wav"
    sf.write(path, np.full((10, 1), 0.1, dtype=np.float32), 48_000)

    result = analyze_audio(path)

    assert result["analysis_status"] == "too_short"
    assert result["integrated_lufs"] is None


def test_more_than_five_channels_does_not_silently_drop_loudness_channels(
    tmp_path: Path,
) -> None:
    path = tmp_path / "six_channels.wav"
    sf.write(path, np.full((48_000, 6), 0.01, dtype=np.float32), 48_000)

    result = analyze_audio(path)

    assert len(result["channel_measurements"]) == 6
    assert result["integrated_lufs"] is None


def test_every_too_short_result_suppresses_integrated_loudness(
    tmp_path: Path,
) -> None:
    sample_rate = 48_000
    time = np.arange(int(sample_rate * 0.45)) / sample_rate
    path = tmp_path / "450ms.wav"
    sf.write(path, 0.1 * np.sin(2 * np.pi * 997 * time), sample_rate)

    result = analyze_audio(path)

    assert result["analysis_status"] == "too_short"
    assert result["integrated_lufs"] is None


def test_non_finite_decoded_samples_are_rejected(tmp_path: Path) -> None:
    audio = np.zeros((48_000, 2), dtype=np.float32)
    audio[100, 0] = np.nan
    audio[200, 1] = np.inf
    path = tmp_path / "non-finite.wav"
    sf.write(path, audio, 48_000, subtype="FLOAT")

    with pytest.raises(AudioDecodeError):
        analyze_audio(path)


def test_duration_limit_is_checked_before_full_analysis(tmp_path: Path) -> None:
    path = tmp_path / "too-long.wav"
    sf.write(path, np.zeros((96_000, 1), dtype=np.float32), 48_000)

    with pytest.raises(AudioDurationError, match="1 second limit"):
        analyze_audio(path, max_duration_seconds=1)
