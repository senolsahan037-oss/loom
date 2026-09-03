from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

AnalysisStage = Literal["mix", "master"]
ComparisonPolicy = Literal[
    "descriptive_mix",
    "descriptive_master",
    "mix_to_mix",
    "mix_to_master",
    "master_to_mix",
    "master_to_master",
    "mix_to_genre",
    "master_to_genre",
]
ComparisonMetric = Literal[
    "loudness_relative_spectrum",
    "integrated_lufs",
    "sample_peak_dbfs",
    "crest_factor_db",
    "channel_balance_db",
]


class ChannelMeasurement(BaseModel):
    index: int
    label: str
    sample_peak_dbfs: Optional[float]
    rms_dbfs: Optional[float]
    dc_offset: float


class AnalysisResponse(BaseModel):
    analysis_contract_version: Literal["2026-07-29.3"]
    filename: Optional[str] = None
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_peak_dbfs: Optional[float]
    rms_dbfs: Optional[float]
    crest_factor_db: Optional[float]
    integrated_lufs: Optional[float]
    channel_measurements: list[ChannelMeasurement]
    analysis_status: Literal["ok", "silent", "too_short"]


class AnalysisJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: int
    result: Optional[AnalysisResponse] = None
    error: Optional[str] = None


class SpectralBandMeasurement(BaseModel):
    center_hz: float
    low_hz: float
    high_hz: float
    level_dbfs: Optional[float]
    loudness_relative_db: Optional[float]


class MonoFoldDownBand(BaseModel):
    center_hz: float
    low_hz: float
    high_hz: float
    stereo_relative_db: float
    mono_loss_db: float
    active_for_detection: bool


class MonoLossRegion(BaseModel):
    low_hz: float
    high_hz: float
    median_loss_db: float


class MonoCompatibilityMeasurement(BaseModel):
    status: Literal["clear", "loss_detected", "unavailable"]
    method: str
    detection_threshold_db: float
    activity_floor_db: float
    bands: list[MonoFoldDownBand]
    loss_regions: list[MonoLossRegion]
    summary: str

class TonalMap(BaseModel):
    status: Literal["descriptive", "unavailable"]
    dominant_pitch_class: Optional[str] = None
    confidence: float = 0.0
    key_candidate: Optional[dict] = None
    chord_candidate: Optional[dict] = None
    pitch_classes: list[dict]
    method: Optional[str] = None

class SectionSummary(BaseModel):
    index: int
    start_ratio: float
    end_ratio: float
    rms_dbfs: Optional[float] = None
    integrated_lufs: Optional[float] = None
    tonal_map: TonalMap


class WaveformChannelEnvelope(BaseModel):
    index: int
    label: str
    minimums: list[float]
    maximums: list[float]


class WaveformVisualization(BaseModel):
    waveform_contract_version: Literal["2026-07-30.waveform.1"]
    method: str
    normalized: Literal[False]
    bin_count: int
    samples_per_bin: float
    sample_rate: int
    channels: list[WaveformChannelEnvelope]


class MixFeatureSet(BaseModel):
    analysis: AnalysisResponse
    waveform: WaveformVisualization
    spectral_method: str
    spectral_bands: list[SpectralBandMeasurement]
    channel_balance_db: Optional[float]
    mono_compatibility: MonoCompatibilityMeasurement
    tonal_map: TonalMap
    noise_floor_dbfs: Optional[float] = None
    section_summaries: list[SectionSummary]


class SpectralBandDelta(BaseModel):
    center_hz: float
    low_hz: float
    high_hz: float
    mix_relative_db: float
    target_relative_db: float
    delta_db: float
    comparison_status: Literal["active_common", "inactive"] = "active_common"
    relevant_for_findings: bool


class NumericMetricComparison(BaseModel):
    metric: Literal[
        "integrated_lufs",
        "sample_peak_dbfs",
        "crest_factor_db",
        "channel_balance_db",
    ]
    subject_value: float
    target_value: float
    delta: float
    target_p25: Optional[float] = None
    target_p75: Optional[float] = None
    outside_interquartile_range: Optional[bool] = None


class MixComparison(BaseModel):
    source: Literal["reference", "genre"]
    target_id: Optional[str] = None
    target_name: str
    spectral_deltas: list[SpectralBandDelta]
    numeric_metrics: list[NumericMetricComparison]
    integrated_lufs_delta: Optional[float]
    sample_peak_delta_db: Optional[float]
    crest_factor_delta_db: Optional[float]
    channel_balance_delta_db: Optional[float]
    reference_analysis: Optional[AnalysisResponse] = None


class ExcludedComparisonMetric(BaseModel):
    metric: ComparisonMetric
    reason: str


class GenreAffinity(BaseModel):
    profile_id: str
    profile_name: str
    rank: int
    distance: float
    spectral_distance: float
    numeric_distance: Optional[float] = None
    basis: list[ComparisonMetric]


class FindingEvidence(BaseModel):
    metric: str
    value: float
    unit: str
    frequency_low_hz: Optional[float] = None
    frequency_high_hz: Optional[float] = None
    comparison_source: Literal["reference", "genre", "direct"]


class MixFinding(BaseModel):
    code: Literal[
        "relative_spectral_high",
        "relative_spectral_low",
        "integrated_lufs_outside_genre_range",
        "sample_peak_outside_genre_range",
        "crest_factor_outside_genre_range",
        "channel_balance_outside_genre_range",
        "mono_fold_down_loss",
    ]
    evidence: FindingEvidence
    observation: str
    possible_meaning: str
    verification: str
    experiment: str
    confidence: Literal["medium"]


class MixAnalysisResponse(BaseModel):
    mix_contract_version: Literal["2026-07-29.mix.2"]
    finding_policy_version: Literal["2026-08-02.findings.2"]
    mode: Literal["general", "reference", "genre", "affinity"]
    analysis_stage: AnalysisStage
    reference_stage: Optional[AnalysisStage] = None
    comparison_policy: ComparisonPolicy
    compared_metrics: list[ComparisonMetric]
    excluded_metrics: list[ExcludedComparisonMetric]
    summary: str
    selected_genre: Optional[str] = None
    recommendation_basis: Optional[
        Literal["reference", "genre", "closest_profile"]
    ] = None
    genre_affinity: list[GenreAffinity]
    genre_affinity_notice: str
    mix: MixFeatureSet
    comparison: Optional[MixComparison] = None
    findings: list[MixFinding]
    recommendations_enabled: bool
    limitations: list[str]


class GenreProfileSummary(BaseModel):
    id: str
    name: str
    source_count: int
    measurement_contract: Literal["2026-07-29.mix.2"]


class GenreProfileListResponse(BaseModel):
    genres: list[GenreProfileSummary]
