from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import butter, sosfilt

from subverse_mix.genre_profiles import (
    GenreProfileStore,
    build_genre_profile,
)
from subverse_mix.genre_profiles import GenreProfileError
from subverse_mix.mix_analyzer import (
    _master_genre_numeric_findings,
    analyze_mix,
    extract_mix_features,
)
from subverse_mix.schemas import MixAnalysisResponse


def _tone(
    path: Path,
    frequency: float,
    amplitude: float = 0.1,
    duration: float = 4.0,
) -> None:
    sample_rate = 48_000
    time = np.arange(int(sample_rate * duration)) / sample_rate
    signal = amplitude * np.sin(2 * np.pi * frequency * time)
    sf.write(
        path,
        np.column_stack((signal, signal)),
        sample_rate,
        subtype="FLOAT",
    )


def _band_limited_signal(
    low_hz: float = 80.0,
    high_hz: float = 240.0,
    duration: float = 4.0,
) -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    rng = np.random.default_rng(42)
    noise = rng.normal(size=int(sample_rate * duration))
    filter_sos = butter(
        6,
        [low_hz, high_hz],
        btype="bandpass",
        fs=sample_rate,
        output="sos",
    )
    signal = sosfilt(filter_sos, noise)
    signal *= 0.1 / np.max(np.abs(signal))
    return signal, sample_rate


def test_general_mode_is_descriptive_and_has_no_recommendations(
    tmp_path: Path,
) -> None:
    mix_path = tmp_path / "mix.wav"
    _tone(mix_path, 100.0)

    result = MixAnalysisResponse.model_validate(
        analyze_mix(mix_path, mix_path.name)
    )

    assert result.mode == "general"
    assert result.finding_policy_version == "2026-08-02.findings.2"
    assert result.comparison is None
    assert result.findings == []
    assert result.recommendations_enabled is False
    assert result.summary == (
        "No Genre Profile or Reference Track was provided. The report "
        "contains direct measurements without comparative correction "
        "guidance. Direct Mono Fold-down findings are still reported."
    )
    assert result.mix.spectral_bands
    assert result.mix.tonal_map.status == "descriptive"
    assert len(result.mix.tonal_map.pitch_classes) == 12
    assert any("direct measurements" in item for item in result.limitations)


def test_waveform_is_raw_bounded_envelope_from_the_decoded_signal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "waveform.wav"
    audio = np.column_stack(
        (
            np.linspace(-0.5, 0.5, 4800, dtype=np.float32),
            np.linspace(0.25, -0.25, 4800, dtype=np.float32),
        )
    )
    sf.write(path, audio, 48_000, subtype="FLOAT")

    result = MixAnalysisResponse.model_validate(
        analyze_mix(path, path.name)
    )
    waveform = result.mix.waveform

    assert waveform.waveform_contract_version == "2026-07-30.waveform.1"
    assert waveform.normalized is False
    assert waveform.bin_count == 1200
    assert waveform.samples_per_bin == 4
    assert [channel.label for channel in waveform.channels] == ["L", "R"]
    assert min(waveform.channels[0].minimums) == pytest.approx(-0.5)
    assert max(waveform.channels[0].maximums) == pytest.approx(0.5)


def test_reference_mode_compares_loudness_relative_spectra(
    tmp_path: Path,
) -> None:
    mix_path = tmp_path / "mix.wav"
    reference_path = tmp_path / "reference.wav"
    _tone(mix_path, 100.0)
    _tone(reference_path, 1000.0, amplitude=0.25)

    result = MixAnalysisResponse.model_validate(
        analyze_mix(
            mix_path,
            mix_path.name,
            reference_path=reference_path,
            reference_filename=reference_path.name,
            reference_stage="mix",
        )
    )

    assert result.mode == "reference"
    assert result.recommendation_basis == "reference"
    assert result.comparison is not None
    assert result.comparison.source == "reference"
    assert result.comparison.reference_analysis is not None
    assert result.comparison.reference_analysis.filename == reference_path.name
    # Narrow-band tones are intentionally excluded from corrective findings
    # when the compared band is below the adaptive activity floor.
    assert result.findings == []
    assert any(
        band.comparison_status == "inactive"
        for band in result.comparison.spectral_deltas
    )


def test_loudness_matching_removes_plain_gain_difference(
    tmp_path: Path,
) -> None:
    mix_path = tmp_path / "mix.wav"
    reference_path = tmp_path / "reference.wav"
    _tone(mix_path, 440.0, amplitude=0.05)
    _tone(reference_path, 440.0, amplitude=0.3)

    result = MixAnalysisResponse.model_validate(
        analyze_mix(
            mix_path,
            mix_path.name,
            reference_path=reference_path,
            reference_filename=reference_path.name,
            reference_stage="mix",
        )
    )

    assert result.comparison is not None
    assert max(
        abs(band.delta_db)
        for band in result.comparison.spectral_deltas
        if band.relevant_for_findings
    ) < 0.01
    assert result.findings == []


def test_spectral_measurement_does_not_cancel_antiphase_stereo(
    tmp_path: Path,
) -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate * 2) / sample_rate
    signal = 0.1 * np.sin(2 * np.pi * 100 * time)
    path = tmp_path / "antiphase.wav"
    sf.write(
        path,
        np.column_stack((signal, -signal)),
        sample_rate,
        subtype="FLOAT",
    )

    features = extract_mix_features(path, path.name)
    band_100 = next(
        band
        for band in features["spectral_bands"]
        if band["center_hz"] == 100.0
    )

    assert band_100["level_dbfs"] is not None
    assert band_100["level_dbfs"] > -30


def test_in_phase_stereo_reports_clean_mono_translation(
    tmp_path: Path,
) -> None:
    signal, sample_rate = _band_limited_signal()
    path = tmp_path / "in-phase.wav"
    sf.write(
        path,
        np.column_stack((signal, signal)),
        sample_rate,
        subtype="FLOAT",
    )

    result = MixAnalysisResponse.model_validate(
        analyze_mix(path, path.name)
    )

    assert result.mix.mono_compatibility.status == "clear"
    assert result.mix.mono_compatibility.loss_regions == []
    assert "No material Mono Fold-down loss" in result.mix.mono_compatibility.summary
    assert all(
        finding.code != "mono_fold_down_loss"
        for finding in result.findings
    )


def test_one_sided_content_does_not_trigger_mono_loss_warning(
    tmp_path: Path,
) -> None:
    signal, sample_rate = _band_limited_signal()
    path = tmp_path / "one-sided.wav"
    sf.write(
        path,
        np.column_stack((signal, np.zeros_like(signal))),
        sample_rate,
        subtype="FLOAT",
    )

    features = extract_mix_features(path, path.name)
    measurement = features["mono_compatibility"]
    active_losses = [
        band["mono_loss_db"]
        for band in measurement["bands"]
        if band["active_for_detection"]
    ]

    assert measurement["status"] == "clear"
    assert measurement["loss_regions"] == []
    assert active_losses
    assert all(-3.02 <= loss <= -3.0 for loss in active_losses)


def test_antiphase_reports_frequency_localized_mono_loss(
    tmp_path: Path,
) -> None:
    signal, sample_rate = _band_limited_signal()
    path = tmp_path / "antiphase-band.wav"
    sf.write(
        path,
        np.column_stack((signal, -signal)),
        sample_rate,
        subtype="FLOAT",
    )

    result = MixAnalysisResponse.model_validate(
        analyze_mix(path, path.name)
    )
    measurement = result.mix.mono_compatibility
    finding = next(
        item
        for item in result.findings
        if item.code == "mono_fold_down_loss"
    )

    assert measurement.status == "loss_detected"
    assert any(
        region.low_hz < 240 and region.high_hz > 80
        for region in measurement.loss_regions
    )
    assert finding.evidence.comparison_source == "direct"
    assert finding.evidence.frequency_low_hz is not None
    assert finding.evidence.frequency_high_hz is not None
    assert "which instrument" in finding.possible_meaning
    assert "Sidechain" in finding.experiment


def test_master_antiphase_uses_mastering_specific_guidance(
    tmp_path: Path,
) -> None:
    signal, sample_rate = _band_limited_signal()
    path = tmp_path / "antiphase-master.wav"
    sf.write(
        path,
        np.column_stack((signal, -signal)),
        sample_rate,
        subtype="FLOAT",
    )

    result = MixAnalysisResponse.model_validate(
        analyze_mix(
            path,
            path.name,
            analysis_stage="master",
        )
    )
    finding = next(
        item
        for item in result.findings
        if item.code == "mono_fold_down_loss"
    )

    assert result.analysis_stage == "master"
    assert "mastering chain" in finding.possible_meaning
    assert "Premaster and Master" in finding.verification
    assert "M/S EQ" in finding.verification
    assert "Mix revision" in finding.experiment
    assert "Sidechain" not in finding.experiment


def test_reference_wins_when_reference_and_genre_are_both_selected(
    tmp_path: Path,
) -> None:
    mix_path = tmp_path / "mix.wav"
    reference_path = tmp_path / "reference.wav"
    _tone(mix_path, 100.0)
    _tone(reference_path, 1000.0)
    deliberately_incompatible_profile = {
        "id": "house",
        "name": "House",
        "source_count": 3,
        "source_stage": "released_master",
        "measurement_contract": "2026-07-29.mix.2",
        "spectral_relative_db": {"100.0": 999.0},
        "master_metric_distributions": {},
    }

    result = MixAnalysisResponse.model_validate(
        analyze_mix(
            mix_path,
            mix_path.name,
            reference_path=reference_path,
            reference_filename=reference_path.name,
            selected_genre="house",
            genre_profile=deliberately_incompatible_profile,
            reference_stage="mix",
        )
    )

    assert result.mode == "reference"
    assert result.selected_genre == "house"
    assert result.comparison is not None
    assert result.comparison.source == "reference"
    assert result.comparison.target_id is None


def test_genre_profile_is_built_from_real_track_measurements(
    tmp_path: Path,
) -> None:
    tracks = []
    for index, frequency in enumerate((90.0, 100.0, 110.0)):
        path = tmp_path / f"source-{index}.wav"
        _tone(path, frequency)
        tracks.append(path)

    profile = build_genre_profile("test-genre", "Test Genre", tracks)
    catalog_path = tmp_path / "genres.json"
    store = GenreProfileStore(catalog_path)
    store.upsert(profile)

    assert store.list() == [
        {
            "id": "test-genre",
            "name": "Test Genre",
            "source_count": 3,
            "measurement_contract": "2026-07-29.mix.2",
        }
    ]
    stored = store.get("test-genre")
    assert stored is not None
    assert stored["spectral_relative_db"]
    assert stored["source_stage"] == "released_master"
    assert (
        stored["master_metric_distributions"]["integrated_lufs"]["median"]
        is not None
    )


def test_genre_profile_rejects_fewer_than_three_sources(
    tmp_path: Path,
) -> None:
    paths = []
    for index in range(2):
        path = tmp_path / f"source-{index}.wav"
        _tone(path, 100.0)
        paths.append(path)

    try:
        build_genre_profile("invalid", "Invalid", paths)
    except GenreProfileError:
        pass
    else:
        raise AssertionError("A two-track genre profile was accepted.")


def test_genre_only_mode_uses_the_measured_profile(tmp_path: Path) -> None:
    mix_path = tmp_path / "mix.wav"
    _tone(mix_path, 1000.0)
    source_paths = []
    for index in range(3):
        path = tmp_path / f"genre-source-{index}.wav"
        _tone(path, 100.0 + index * 5)
        source_paths.append(path)
    profile = build_genre_profile("house", "House", source_paths)

    result = MixAnalysisResponse.model_validate(
        analyze_mix(
            mix_path,
            mix_path.name,
            selected_genre="house",
            genre_profile=profile,
        )
    )

    assert result.mode == "genre"
    assert result.recommendation_basis == "genre"
    assert result.comparison is not None
    assert result.comparison.source == "genre"
    assert result.comparison.target_id == "house"
    assert result.comparison.reference_analysis is None


def test_stage_policy_exposes_only_valid_reference_comparisons(
    tmp_path: Path,
) -> None:
    subject_path = tmp_path / "subject.wav"
    reference_path = tmp_path / "reference.wav"
    _tone(subject_path, 200.0, amplitude=0.1)
    _tone(reference_path, 1000.0, amplitude=0.2)

    expected = {
        ("mix", "mix"): {
            "loudness_relative_spectrum",
            "crest_factor_db",
            "channel_balance_db",
        },
        ("mix", "master"): {"loudness_relative_spectrum"},
        ("master", "mix"): {"loudness_relative_spectrum"},
        ("master", "master"): {
            "loudness_relative_spectrum",
            "integrated_lufs",
            "sample_peak_dbfs",
            "crest_factor_db",
            "channel_balance_db",
        },
    }

    for (analysis_stage, reference_stage), compared_metrics in expected.items():
        result = MixAnalysisResponse.model_validate(
            analyze_mix(
                subject_path,
                subject_path.name,
                reference_path=reference_path,
                reference_filename=reference_path.name,
                analysis_stage=analysis_stage,
                reference_stage=reference_stage,
            )
        )

        assert result.comparison_policy == (
            f"{analysis_stage}_to_{reference_stage}"
        )
        assert set(result.compared_metrics) == compared_metrics
        assert {
            exclusion.metric for exclusion in result.excluded_metrics
        } == set(COMPARISON_METRICS_FOR_TEST).difference(compared_metrics)


COMPARISON_METRICS_FOR_TEST = {
    "loudness_relative_spectrum",
    "integrated_lufs",
    "sample_peak_dbfs",
    "crest_factor_db",
    "channel_balance_db",
}


def test_genre_policy_changes_with_declared_analysis_stage(
    tmp_path: Path,
) -> None:
    subject_path = tmp_path / "subject.wav"
    _tone(subject_path, 1000.0)
    source_paths = []
    for index, amplitude in enumerate((0.05, 0.1, 0.2)):
        path = tmp_path / f"master-source-{index}.wav"
        _tone(path, 100.0 + index * 5, amplitude=amplitude)
        source_paths.append(path)
    profile = build_genre_profile("house", "House", source_paths)

    mix_result = MixAnalysisResponse.model_validate(
        analyze_mix(
            subject_path,
            subject_path.name,
            selected_genre="house",
            genre_profile=profile,
            analysis_stage="mix",
        )
    )
    master_result = MixAnalysisResponse.model_validate(
        analyze_mix(
            subject_path,
            subject_path.name,
            selected_genre="house",
            genre_profile=profile,
            analysis_stage="master",
        )
    )
    assert mix_result.comparison_policy == "mix_to_genre"
    assert mix_result.compared_metrics == ["loudness_relative_spectrum"]
    assert master_result.comparison_policy == "master_to_genre"
    assert set(master_result.compared_metrics) == COMPARISON_METRICS_FOR_TEST
    assert master_result.comparison is not None
    assert master_result.comparison.sample_peak_delta_db is not None


def test_master_genre_advice_requires_measured_range_outlier(
    tmp_path: Path,
) -> None:
    subject_path = tmp_path / "loud-master.wav"
    _tone(subject_path, 440.0, amplitude=0.7)
    source_paths = []
    for index, amplitude in enumerate((0.04, 0.05, 0.06)):
        path = tmp_path / f"released-master-{index}.wav"
        _tone(path, 440.0, amplitude=amplitude)
        source_paths.append(path)
    profile = build_genre_profile("test", "Test", source_paths)

    result = MixAnalysisResponse.model_validate(
        analyze_mix(
            subject_path,
            subject_path.name,
            selected_genre="test",
            genre_profile=profile,
            analysis_stage="master",
        )
    )

    assert any(
        finding.code == "integrated_lufs_outside_genre_range"
        for finding in result.findings
    )
    assert any(
        metric.metric == "integrated_lufs"
        and metric.outside_interquartile_range is True
        for metric in result.comparison.numeric_metrics
    )


def test_master_genre_numeric_advice_uses_conservative_outer_fence() -> None:
    within_outer_fence = {
        "numeric_metrics": [
            {
                "metric": "integrated_lufs",
                "subject_value": -8.9,
                "target_value": -11.0,
                "delta": 2.1,
                "target_p25": -12.0,
                "target_p75": -10.0,
                "outside_interquartile_range": True,
            }
        ]
    }
    beyond_outer_fence = {
        "numeric_metrics": [
            {
                **within_outer_fence["numeric_metrics"][0],
                "subject_value": -6.9,
                "delta": 4.1,
            }
        ]
    }

    assert _master_genre_numeric_findings(within_outer_fence) == []
    findings = _master_genre_numeric_findings(beyond_outer_fence)
    assert len(findings) == 1
    assert findings[0]["code"] == "integrated_lufs_outside_genre_range"
    assert "outer fence of -15.00–-7.00 LUFS" in findings[0]["observation"]


def test_zero_width_genre_distribution_does_not_generate_advice() -> None:
    comparison = {
        "numeric_metrics": [
            {
                "metric": "sample_peak_dbfs",
                "subject_value": 0.1,
                "target_value": 0.0,
                "delta": 0.1,
                "target_p25": 0.0,
                "target_p75": 0.0,
                "outside_interquartile_range": True,
            }
        ]
    }

    assert _master_genre_numeric_findings(comparison) == []


def test_genre_affinity_ranks_profiles_without_classifying_the_track(
    tmp_path: Path,
) -> None:
    subject_path = tmp_path / "subject.wav"
    _tone(subject_path, 100.0)

    low_sources = []
    high_sources = []
    for index, frequency in enumerate((90.0, 100.0, 110.0)):
        low_path = tmp_path / f"low-{index}.wav"
        high_path = tmp_path / f"high-{index}.wav"
        _tone(low_path, frequency)
        _tone(high_path, frequency * 10)
        low_sources.append(low_path)
        high_sources.append(high_path)
    low_profile = build_genre_profile("low", "Low", low_sources)
    high_profile = build_genre_profile("high", "High", high_sources)

    mix_result = MixAnalysisResponse.model_validate(
        analyze_mix(
            subject_path,
            subject_path.name,
            genre_profiles=[high_profile, low_profile],
            analysis_stage="mix",
        )
    )
    master_result = MixAnalysisResponse.model_validate(
        analyze_mix(
            subject_path,
            subject_path.name,
            genre_profiles=[high_profile, low_profile],
            analysis_stage="master",
        )
    )
    affinity_result = MixAnalysisResponse.model_validate(
        analyze_mix(
            subject_path,
            subject_path.name,
            genre_profiles=[high_profile, low_profile],
            use_closest_profile=True,
            analysis_stage="mix",
        )
    )

    assert [item.profile_id for item in mix_result.genre_affinity] == [
        "low",
        "high",
    ]
    assert mix_result.mode == "general"
    assert mix_result.selected_genre is None
    assert mix_result.recommendation_basis is None
    assert mix_result.comparison is None
    assert mix_result.findings == []
    assert mix_result.recommendations_enabled is False
    assert affinity_result.mode == "affinity"
    assert affinity_result.selected_genre is None
    assert affinity_result.recommendation_basis == "closest_profile"
    assert affinity_result.comparison is not None
    assert affinity_result.comparison.target_id == "low"
    assert affinity_result.comparison_policy == "mix_to_genre"
    assert mix_result.genre_affinity[0].basis == [
        "loudness_relative_spectrum"
    ]
    assert master_result.genre_affinity[0].basis == [
        "loudness_relative_spectrum",
        "integrated_lufs",
    ]
    assert master_result.genre_affinity[0].numeric_distance is not None
    assert "not a genre classification" in master_result.genre_affinity_notice
