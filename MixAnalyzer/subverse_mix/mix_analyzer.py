from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from scipy.signal import welch

from .analyzer import _load_audio, analyze_decoded_audio

MIX_CONTRACT_VERSION = "2026-07-29.mix.2"
FINDING_POLICY_VERSION = "2026-08-02.findings.2"
WAVEFORM_CONTRACT_VERSION = "2026-07-30.waveform.1"
WAVEFORM_MAX_BINS = 1200
SIGNIFICANT_SPECTRAL_DELTA_DB = 2.0
MIN_SIGNIFICANT_BAND_COUNT = 2
SPECTRAL_RELEVANCE_FLOOR_DB = -50.0
SPECTRAL_ACTIVITY_FLOOR_DBFS = -70.0
MONO_LOSS_DETECTION_THRESHOLD_DB = -4.0
MONO_ACTIVITY_FLOOR_DB = -40.0
TONAL_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MIN_MONO_LOSS_BAND_COUNT = 2
GENRE_NUMERIC_OUTLIER_IQR_MULTIPLIER = 1.5
COMPARISON_METRICS = (
    "loudness_relative_spectrum",
    "integrated_lufs",
    "sample_peak_dbfs",
    "crest_factor_db",
    "channel_balance_db",
)


def _waveform_envelope(
    audio: np.ndarray,
    sample_rate: int,
    max_bins: int = WAVEFORM_MAX_BINS,
) -> Dict[str, Any]:
    """Return an unsmoothed, non-normalized min/max envelope for display."""
    frame_count = len(audio)
    bin_count = min(max_bins, frame_count)
    channel_names = ("L", "R")
    if bin_count == 0:
        edges = np.array([0], dtype=int)
    else:
        edges = np.linspace(0, frame_count, bin_count + 1, dtype=int)

    channels = []
    for index in range(audio.shape[1]):
        minimums = []
        maximums = []
        for bin_index in range(bin_count):
            segment = audio[edges[bin_index] : edges[bin_index + 1], index]
            minimums.append(round(float(np.min(segment)), 6))
            maximums.append(round(float(np.max(segment)), 6))
        channels.append(
            {
                "index": index,
                "label": (
                    channel_names[index]
                    if index < len(channel_names)
                    else f"CH {index + 1}"
                ),
                "minimums": minimums,
                "maximums": maximums,
            }
        )

    return {
        "waveform_contract_version": WAVEFORM_CONTRACT_VERSION,
        "method": "Per-bin raw sample minimum and maximum envelope",
        "normalized": False,
        "bin_count": bin_count,
        "samples_per_bin": (
            0.0 if bin_count == 0 else round(frame_count / bin_count, 3)
        ),
        "sample_rate": sample_rate,
        "channels": channels,
    }

# Preferred one-third-octave centers. Results are descriptive measurements,
# not genre targets or quality scores.
THIRD_OCTAVE_CENTERS_HZ = (
    31.5,
    40.0,
    50.0,
    63.0,
    80.0,
    100.0,
    125.0,
    160.0,
    200.0,
    250.0,
    315.0,
    400.0,
    500.0,
    630.0,
    800.0,
    1000.0,
    1250.0,
    1600.0,
    2000.0,
    2500.0,
    3150.0,
    4000.0,
    5000.0,
    6300.0,
    8000.0,
    10000.0,
    12500.0,
    16000.0,
)


def _comparison_policy(
    analysis_stage: str,
    reference_stage: str | None,
    has_genre_profile: bool,
) -> tuple[str, List[str], List[Dict[str, str]]]:
    if reference_stage is not None:
        policy = f"{analysis_stage}_to_{reference_stage}"
        allowed = {
            "mix_to_mix": {
                "loudness_relative_spectrum",
                "crest_factor_db",
                "channel_balance_db",
            },
            "mix_to_master": {"loudness_relative_spectrum"},
            "master_to_mix": {"loudness_relative_spectrum"},
            "master_to_master": set(COMPARISON_METRICS),
        }[policy]
        cross_stage_reason = (
            "Mix and Master files have different level and dynamics-processing "
            "contexts; this measurement cannot be used as a cross-stage target."
        )
        mix_level_reason = (
            "Mix level is unconstrained; this measurement is not a quality "
            "target in a Mix-to-Mix comparison."
        )
        exclusions = [
            {
                "metric": metric,
                "reason": (
                    cross_stage_reason
                    if analysis_stage != reference_stage
                    else mix_level_reason
                ),
            }
            for metric in COMPARISON_METRICS
            if metric not in allowed
        ]
        return policy, list(metric for metric in COMPARISON_METRICS if metric in allowed), exclusions

    if has_genre_profile:
        policy = f"{analysis_stage}_to_genre"
        allowed = (
            {"loudness_relative_spectrum"}
            if analysis_stage == "mix"
            else set(COMPARISON_METRICS)
        )
        exclusions = [
            {
                "metric": metric,
                "reason": (
                    "Genre Profiles are built from released-master measurements; "
                    "this measurement cannot be used as a Mix-stage target."
                ),
            }
            for metric in COMPARISON_METRICS
            if metric not in allowed
        ]
        return policy, list(metric for metric in COMPARISON_METRICS if metric in allowed), exclusions

    return f"descriptive_{analysis_stage}", [], []


def _optional_delta(
    left: Optional[float],
    right: Optional[float],
) -> Optional[float]:
    if left is None or right is None:
        return None
    return round(left - right, 3)


def _channel_balance_db(analysis: Dict[str, Any]) -> Optional[float]:
    channels = analysis["channel_measurements"]
    if len(channels) < 2:
        return None
    left = channels[0]["rms_dbfs"]
    right = channels[1]["rms_dbfs"]
    if left is None or right is None:
        return None
    return round(abs(float(left) - float(right)), 3)


def _spectral_bands(
    audio: np.ndarray,
    sample_rate: int,
    reference_level_dbfs: Optional[float],
) -> List[Dict[str, Optional[float]]]:
    if sample_rate <= 0 or len(audio) < 256:
        return []

    nperseg = min(8192, len(audio))
    noverlap = nperseg // 2
    frequencies, channel_psd = welch(
        audio,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        detrend=False,
        return_onesided=True,
        scaling="density",
        axis=0,
    )
    if channel_psd.ndim == 1:
        channel_psd = channel_psd[:, np.newaxis]
    mean_psd = np.mean(channel_psd, axis=1)
    frequency_step = (
        float(frequencies[1] - frequencies[0])
        if len(frequencies) > 1
        else 0.0
    )
    nyquist = sample_rate / 2.0
    edge_ratio = 2.0 ** (1.0 / 6.0)
    result: List[Dict[str, Optional[float]]] = []

    for center in THIRD_OCTAVE_CENTERS_HZ:
        low = center / edge_ratio
        high = center * edge_ratio
        if high > nyquist:
            continue
        mask = (frequencies >= low) & (frequencies < high)
        band_power = (
            float(np.sum(mean_psd[mask]) * frequency_step)
            if frequency_step > 0.0 and np.any(mask)
            else 0.0
        )
        level_dbfs = (
            float(10.0 * np.log10(band_power))
            if band_power > 0.0 and np.isfinite(band_power)
            else None
        )
        # Welch dBFS and LUFS are different meter scales. Comparing them
        # created phantom 10-20 dB deltas in quiet bands. Use broadband RMS
        # dBFS as the matching reference for these PSD-derived band levels.
        loudness_relative_db = (
            None
            if level_dbfs is None or reference_level_dbfs is None
            else level_dbfs - reference_level_dbfs
        )
        result.append(
            {
                "center_hz": center,
                "low_hz": round(low, 3),
                "high_hz": round(high, 3),
                "level_dbfs": (
                    None if level_dbfs is None else round(level_dbfs, 3)
                ),
                "loudness_relative_db": (
                    None
                    if loudness_relative_db is None
                    else round(loudness_relative_db, 3)
                ),
            }
        )

    return result


def _tonal_map(audio: np.ndarray, sample_rate: int) -> Dict[str, Any]:
    """Estimate pitch-class energy; descriptive only, not key detection."""
    mono = np.mean(audio, axis=1) if audio.ndim == 2 else audio
    if sample_rate <= 0 or len(mono) < 2048:
        return {"status": "unavailable", "dominant_pitch_class": None, "confidence": 0.0, "pitch_classes": []}
    nperseg = min(8192, len(mono))
    window = np.hanning(nperseg)
    starts = np.linspace(0, max(0, len(mono) - nperseg), min(24, max(1, len(mono) // max(1, nperseg // 2)))).astype(int)
    energies = np.zeros(12, dtype=float)
    for start in starts:
        spectrum = np.abs(np.fft.rfft(mono[start:start + nperseg] * window)) ** 2
        freqs = np.fft.rfftfreq(nperseg, 1.0 / sample_rate)
        valid = (freqs >= 55.0) & (freqs <= 2000.0) & (spectrum > 0)
        midi = 69.0 + 12.0 * np.log2(np.maximum(freqs[valid], 1e-9) / 440.0)
        weights = spectrum[valid]
        for pitch_class in range(12):
            nearest = np.mod(np.round(midi).astype(int), 12)
            energies[pitch_class] += float(np.sum(weights[nearest == pitch_class]))
    total = float(np.sum(energies))
    if total <= 0:
        return {"status": "unavailable", "dominant_pitch_class": None, "confidence": 0.0, "pitch_classes": []}
    shares = energies / total
    order = np.argsort(shares)[::-1]
    confidence = float(shares[order[0]] - shares[order[1]])
    major = np.array([6.0, 2.0, 3.0, 2.0, 5.0, 4.0, 2.0, 5.0, 2.0, 3.0, 2.0, 2.0])
    minor = np.array([6.0, 2.0, 3.0, 5.0, 2.0, 4.0, 2.0, 3.0, 5.0, 2.0, 3.0, 2.0])
    key_scores = []
    for root in range(12):
        for mode, template in (("major", major), ("minor", minor)):
            rotated = np.roll(template, root)
            key_scores.append((float(np.dot(shares, rotated) / np.linalg.norm(rotated)), TONAL_NAMES[root], mode))
    key_scores.sort(reverse=True)
    root = int(order[0])
    major_score = sum(float(shares[i]) for i in {root, (root + 4) % 12, (root + 7) % 12})
    minor_score = sum(float(shares[i]) for i in {root, (root + 3) % 12, (root + 7) % 12})
    chord_mode = "minor" if minor_score > major_score else "major"
    return {"status": "descriptive", "dominant_pitch_class": TONAL_NAMES[root], "confidence": round(min(1.0, confidence * 12.0), 3), "key_candidate": {"root": key_scores[0][1], "mode": key_scores[0][2], "score": round(key_scores[0][0], 4)}, "chord_candidate": {"root": TONAL_NAMES[root], "quality": chord_mode, "score": round(max(major_score, minor_score), 4)}, "pitch_classes": [{"name": TONAL_NAMES[int(i)], "share": round(float(shares[i]), 4)} for i in order], "method": "Frame-averaged FFT pitch-class energy with major/minor template scoring; candidates require listening verification."}


def _noise_floor_dbfs(audio: np.ndarray, sample_rate: int) -> Optional[float]:
    mono = np.mean(audio, axis=1) if audio.ndim == 2 else audio
    frame = max(256, min(2048, int(sample_rate * 0.05)))
    if len(mono) < frame:
        return None
    rms_values = []
    for start in range(0, len(mono) - frame + 1, frame):
        chunk = mono[start:start + frame]
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        if rms > 0 and np.isfinite(rms):
            rms_values.append(20.0 * np.log10(rms))
    if not rms_values:
        return None
    return round(float(np.percentile(rms_values, 10)), 3)


def _section_summaries(audio: np.ndarray, sample_rate: int) -> List[Dict[str, Any]]:
    """Four coarse sections prevent one loud chorus hiding quiet sections."""
    sections = []
    for index, (start_ratio, end_ratio) in enumerate(((0.0, .25), (.25, .5), (.5, .75), (.75, 1.0)), start=1):
        start = int(len(audio) * start_ratio)
        end = max(start + 1, int(len(audio) * end_ratio))
        chunk = audio[start:end]
        analysis = analyze_decoded_audio(chunk, sample_rate)
        sections.append({
            "index": index,
            "start_ratio": start_ratio,
            "end_ratio": end_ratio,
            "rms_dbfs": analysis.get("rms_dbfs"),
            "integrated_lufs": analysis.get("integrated_lufs"),
            "tonal_map": _tonal_map(chunk, sample_rate),
        })
    return sections


def _mono_compatibility(
    audio: np.ndarray,
    sample_rate: int,
) -> Dict[str, Any]:
    unavailable = {
        "status": "unavailable",
        "method": (
            "Mono fold-down M=(L+R)/2; band power compared with "
            "mean stereo channel power"
        ),
        "detection_threshold_db": MONO_LOSS_DETECTION_THRESHOLD_DB,
        "activity_floor_db": MONO_ACTIVITY_FLOOR_DB,
        "bands": [],
        "loss_regions": [],
        "summary": "Stereo Mono Fold-down could not be measured for this file.",
    }
    if (
        sample_rate <= 0
        or len(audio) < 256
        or audio.ndim < 2
        or audio.shape[1] < 2
    ):
        return unavailable

    left = audio[:, 0]
    right = audio[:, 1]
    mono = (left + right) / 2.0
    measurement_audio = np.column_stack((left, right, mono))
    nperseg = min(8192, len(audio))
    frequencies, psd = welch(
        measurement_audio,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend=False,
        return_onesided=True,
        scaling="density",
        axis=0,
    )
    frequency_step = (
        float(frequencies[1] - frequencies[0])
        if len(frequencies) > 1
        else 0.0
    )
    if frequency_step <= 0:
        return unavailable

    stereo_psd = np.mean(psd[:, :2], axis=1)
    mono_psd = psd[:, 2]
    edge_ratio = 2.0 ** (1.0 / 6.0)
    nyquist = sample_rate / 2.0
    raw_bands = []
    for center in THIRD_OCTAVE_CENTERS_HZ:
        low = center / edge_ratio
        high = center * edge_ratio
        if high > nyquist:
            continue
        mask = (frequencies >= low) & (frequencies < high)
        stereo_power = (
            float(np.sum(stereo_psd[mask]) * frequency_step)
            if np.any(mask)
            else 0.0
        )
        mono_power = (
            float(np.sum(mono_psd[mask]) * frequency_step)
            if np.any(mask)
            else 0.0
        )
        raw_bands.append(
            {
                "center_hz": center,
                "low_hz": round(low, 3),
                "high_hz": round(high, 3),
                "stereo_power": stereo_power,
                "mono_power": mono_power,
            }
        )

    peak_stereo_power = max(
        (band["stereo_power"] for band in raw_bands),
        default=0.0,
    )
    if peak_stereo_power <= 0.0:
        return unavailable

    bands = []
    for band in raw_bands:
        stereo_power = band["stereo_power"]
        if stereo_power <= 0.0:
            continue
        stereo_relative_db = float(
            10.0 * np.log10(stereo_power / peak_stereo_power)
        )
        retained_ratio = min(
            1.0,
            max(band["mono_power"] / stereo_power, 1e-12),
        )
        mono_loss_db = float(10.0 * np.log10(retained_ratio))
        bands.append(
            {
                "center_hz": band["center_hz"],
                "low_hz": band["low_hz"],
                "high_hz": band["high_hz"],
                "stereo_relative_db": round(stereo_relative_db, 3),
                "mono_loss_db": round(mono_loss_db, 3),
                "active_for_detection": (
                    stereo_relative_db >= MONO_ACTIVITY_FLOOR_DB
                ),
            }
        )

    regions = []
    current = []

    def flush_region() -> None:
        nonlocal current
        if len(current) >= MIN_MONO_LOSS_BAND_COUNT:
            regions.append(
                {
                    "low_hz": current[0]["low_hz"],
                    "high_hz": current[-1]["high_hz"],
                    "median_loss_db": round(
                        float(
                            np.median(
                                [band["mono_loss_db"] for band in current]
                            )
                        ),
                        3,
                    ),
                }
            )
        current = []

    for band in bands:
        if (
            band["active_for_detection"]
            and band["mono_loss_db"] <= MONO_LOSS_DETECTION_THRESHOLD_DB
        ):
            current.append(band)
        else:
            flush_region()
    flush_region()

    if regions:
        summary = (
            f"Material Mono Fold-down energy loss was detected in "
            f"{len(regions)} frequency region(s)."
        )
        status = "loss_detected"
    else:
        summary = (
            "No material Mono Fold-down loss was detected by this measurement."
        )
        status = "clear"
    return {
        "status": status,
        "method": (
            "Mono fold-down M=(L+R)/2; one-third-octave mono power compared "
            "with mean stereo channel power"
        ),
        "detection_threshold_db": MONO_LOSS_DETECTION_THRESHOLD_DB,
        "activity_floor_db": MONO_ACTIVITY_FLOOR_DB,
        "bands": bands,
        "loss_regions": regions,
        "summary": summary,
    }


def extract_mix_features(
    file_path: str | Path,
    filename: Optional[str] = None,
    max_duration_seconds: float | None = None,
) -> Dict[str, Any]:
    audio, sample_rate = _load_audio(file_path, max_duration_seconds)
    analysis = analyze_decoded_audio(audio, sample_rate)
    if filename is not None:
        analysis["filename"] = filename
    channel_balance = _channel_balance_db(analysis)
    return {
        "analysis": analysis,
        "waveform": _waveform_envelope(audio, sample_rate),
        "spectral_method": (
            "Per-channel mean Welch power spectral density; "
            "one-third-octave center bands"
        ),
        "spectral_bands": _spectral_bands(
            audio,
            sample_rate,
            analysis["rms_dbfs"],
        ),
        "tonal_map": _tonal_map(audio, sample_rate),
        "noise_floor_dbfs": _noise_floor_dbfs(audio, sample_rate),
        "section_summaries": _section_summaries(audio, sample_rate),
        "channel_balance_db": channel_balance,
        "mono_compatibility": _mono_compatibility(audio, sample_rate),
    }


def _relative_band_map(feature_set: Dict[str, Any]) -> Dict[float, Dict[str, Any]]:
    return {
        float(band["center_hz"]): band
        for band in feature_set["spectral_bands"]
        if band["loudness_relative_db"] is not None
    }


def _profile_band_map(profile: Dict[str, Any]) -> Dict[float, float]:
    return {
        float(center): float(value)
        for center, value in profile["spectral_relative_db"].items()
    }


def _profile_band_ranges(
    profile: Dict[str, Any],
) -> Dict[float, tuple[float, float]]:
    p25 = profile.get("spectral_p25_db", {})
    p75 = profile.get("spectral_p75_db", {})
    return {
        float(center): (float(value), float(p75[center]))
        for center, value in p25.items()
        if center in p75
    }


def _genre_affinity(
    feature_set: Dict[str, Any],
    profiles: Iterable[Dict[str, Any]],
    analysis_stage: str,
) -> List[Dict[str, Any]]:
    subject_bands = _relative_band_map(feature_set)
    subject_analysis = feature_set["analysis"]
    subject_numeric = {
        "integrated_lufs": subject_analysis["integrated_lufs"],
        "sample_peak_dbfs": subject_analysis["sample_peak_dbfs"],
        "crest_factor_db": subject_analysis["crest_factor_db"],
        "channel_balance_db": feature_set["channel_balance_db"],
    }
    affinities: List[Dict[str, Any]] = []

    for profile in profiles:
        profile_bands = _profile_band_map(profile)
        profile_ranges = _profile_band_ranges(profile)
        spectral_distances = []
        for center, subject_band in subject_bands.items():
            median = profile_bands.get(center)
            measured_range = profile_ranges.get(center)
            if median is None or measured_range is None:
                continue
            width = measured_range[1] - measured_range[0]
            if width <= 0:
                continue
            spectral_distances.append(
                abs(float(subject_band["loudness_relative_db"]) - median) / width
            )
        if not spectral_distances:
            continue

        spectral_distance = float(np.median(spectral_distances))
        basis = ["loudness_relative_spectrum"]
        numeric_distances: List[float] = []
        if analysis_stage == "master":
            distributions = profile.get("master_metric_distributions", {})
            for metric, subject_value in subject_numeric.items():
                distribution = distributions.get(metric, {})
                median = distribution.get("median")
                p25 = distribution.get("p25")
                p75 = distribution.get("p75")
                if (
                    subject_value is None
                    or median is None
                    or p25 is None
                    or p75 is None
                ):
                    continue
                width = float(p75) - float(p25)
                if width <= 0:
                    continue
                numeric_distances.append(
                    abs(float(subject_value) - float(median)) / width
                )
                basis.append(metric)

        components = [spectral_distance, *numeric_distances]
        affinities.append(
            {
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "distance": round(float(np.median(components)), 4),
                "spectral_distance": round(spectral_distance, 4),
                "numeric_distance": (
                    None
                    if not numeric_distances
                    else round(float(np.median(numeric_distances)), 4)
                ),
                "basis": basis,
            }
        )

    affinities.sort(
        key=lambda item: (item["distance"], item["profile_name"].casefold())
    )
    for rank, affinity in enumerate(affinities, start=1):
        affinity["rank"] = rank
    return affinities


def _spectral_deltas(
    mix: Dict[str, Any],
    target_relative_bands: Dict[float, float],
    target_ranges: Dict[float, tuple[float, float]] | None = None,
    target_levels: Dict[float, float] | None = None,
) -> List[Dict[str, float]]:
    deltas: List[Dict[str, float]] = []
    mix_levels = [
        float(band.get("level_dbfs"))
        for band in mix.get("spectral_bands", [])
        if band.get("level_dbfs") is not None
    ]
    target_level_values = list(target_levels.values()) if target_levels else []
    mix_activity_floor = max(mix_levels) - 60.0 if mix_levels else SPECTRAL_ACTIVITY_FLOOR_DBFS
    target_activity_floor = max(target_level_values) - 60.0 if target_level_values else SPECTRAL_ACTIVITY_FLOOR_DBFS
    for center, mix_band in _relative_band_map(mix).items():
        target = target_relative_bands.get(center)
        if target is None:
            continue
        mix_value = float(mix_band["loudness_relative_db"])
        mix_level = mix_band.get("level_dbfs")
        target_level = None if target_levels is None else target_levels.get(center)
        mix_active = mix_level is not None and float(mix_level) >= mix_activity_floor
        target_active = target_level is None or float(target_level) >= target_activity_floor
        common_active = mix_active and target_active
        target_range = (
            None if target_ranges is None else target_ranges.get(center)
        )
        outside_profile_range = (
            target_range is None
            or mix_value < target_range[0] - SIGNIFICANT_SPECTRAL_DELTA_DB
            or mix_value > target_range[1] + SIGNIFICANT_SPECTRAL_DELTA_DB
        )
        deltas.append(
            {
                "center_hz": center,
                "low_hz": float(mix_band["low_hz"]),
                "high_hz": float(mix_band["high_hz"]),
                "mix_relative_db": round(mix_value, 3),
                "target_relative_db": round(target, 3),
                "delta_db": round(mix_value - target, 3),
                "comparison_status": "active_common" if common_active else "inactive",
                "relevant_for_findings": (
                    common_active
                    and max(mix_value, target) >= SPECTRAL_RELEVANCE_FLOOR_DB
                    and outside_profile_range
                ),
            }
        )
    return deltas


def _significant_regions(
    deltas: Iterable[Dict[str, float]],
) -> List[List[Dict[str, float]]]:
    regions: List[List[Dict[str, float]]] = []
    current: List[Dict[str, float]] = []
    current_sign = 0

    for band in deltas:
        if not band["relevant_for_findings"]:
            if len(current) >= MIN_SIGNIFICANT_BAND_COUNT:
                regions.append(current)
            current, current_sign = [], 0
            continue
        value = band["delta_db"]
        sign = 1 if value >= SIGNIFICANT_SPECTRAL_DELTA_DB else (
            -1 if value <= -SIGNIFICANT_SPECTRAL_DELTA_DB else 0
        )
        if sign == 0:
            if len(current) >= MIN_SIGNIFICANT_BAND_COUNT:
                regions.append(current)
            current, current_sign = [], 0
            continue
        if current and sign != current_sign:
            if len(current) >= MIN_SIGNIFICANT_BAND_COUNT:
                regions.append(current)
            current = []
        current.append(band)
        current_sign = sign

    if len(current) >= MIN_SIGNIFICANT_BAND_COUNT:
        regions.append(current)
    return regions


def _findings(
    deltas: List[Dict[str, float]],
    comparison_source: str,
    analysis_stage: str,
) -> List[Dict[str, Any]]:
    ranked_regions = sorted(
        _significant_regions(deltas),
        key=lambda region: abs(
            float(np.mean([band["delta_db"] for band in region]))
        ),
        reverse=True,
    )[:3]
    findings: List[Dict[str, Any]] = []

    for region in ranked_regions:
        average_delta = float(np.mean([band["delta_db"] for band in region]))
        low = region[0]["low_hz"]
        high = region[-1]["high_hz"]
        is_high = average_delta > 0
        direction = "higher" if is_high else "lower"
        code = "relative_spectral_high" if is_high else "relative_spectral_low"
        possible_meaning = (
            "This frequency region may be perceived as more prominent than the "
            "comparison target."
            if is_high
            else "This frequency region may be perceived as more recessed than "
            "the comparison target."
        )
        if analysis_stage == "mix":
            experiment = (
                "Identify the source channels carrying this range first. Try a "
                "small level or broad EQ reduction, using Mix-bus EQ only as a "
                "last resort."
                if is_high
                else "Check arrangement and source levels first. Test the deficit "
                "with a small source-level or broad EQ change, using Mix-bus EQ "
                "only as a last resort."
            )
        else:
            experiment = (
                "After a loudness-matched A/B, try a small broad Master EQ "
                "reduction if needed. Verify with level-matched bypass."
                if is_high
                else "After a loudness-matched A/B, try a small broad Master EQ "
                "boost if needed. Re-measure headroom and Sample Peak."
            )
        findings.append(
            {
                "code": code,
                "evidence": {
                    "metric": "loudness_matched_spectral_delta",
                    "value": round(average_delta, 3),
                    "unit": "dB",
                    "frequency_low_hz": round(low, 3),
                    "frequency_high_hz": round(high, 3),
                    "comparison_source": comparison_source,
                },
                "observation": (
                    f"{low:.0f}–{high:.0f} Hz is {abs(average_delta):.1f} dB "
                    f"{direction} on average than the loudness-matched target."
                ),
                "possible_meaning": possible_meaning,
                "verification": (
                    "Listen to the same section in a loudness-matched A/B. "
                    + (
                        "The stereo Mix cannot identify which instrument causes "
                        "the difference; verify the source with mute/solo."
                        if analysis_stage == "mix"
                        else "Do not treat the difference alone as a fault; verify "
                        "the effect of tonal intent and arrangement by listening."
                    )
                ),
                "experiment": experiment,
                "confidence": "medium",
            }
        )
    return findings


def _master_genre_numeric_findings(
    comparison: Dict[str, Any],
) -> List[Dict[str, Any]]:
    definitions = {
        "integrated_lufs": {
            "code": "integrated_lufs_outside_genre_range",
            "unit": "LUFS",
            "meaning_high": "The Master is louder than the middle of the Genre Profile distribution.",
            "meaning_low": "The Master is quieter than the middle of the Genre Profile distribution.",
            "verification": (
                "Run a loudness-matched A/B without treating the Genre Profile "
                "as a mandatory target. Check arrangement and release intent."
            ),
            "experiment": (
                "Make one small, reversible Limiter or input-gain change, then "
                "re-measure Integrated Loudness, Sample Peak, and Crest Factor."
            ),
        },
        "sample_peak_dbfs": {
            "code": "sample_peak_outside_genre_range",
            "unit": "dBFS",
            "meaning_high": "Decoded Sample Peak is above the Genre Profile master distribution.",
            "meaning_low": "Decoded Sample Peak is below the Genre Profile master distribution.",
            "verification": (
                "Remember that this is not True Peak. Verify the Sample Peak "
                "change with Limiter bypass and matched output gain."
            ),
            "experiment": (
                "Make a small output-gain or Limiter-ceiling change and "
                "re-measure Sample Peak."
            ),
        },
        "crest_factor_db": {
            "code": "crest_factor_outside_genre_range",
            "unit": "dB",
            "meaning_high": "The Master has a higher Crest Factor than the middle of the Genre Profile distribution.",
            "meaning_low": "The Master has a lower Crest Factor than the middle of the Genre Profile distribution.",
            "verification": (
                "Loudness-match Limiter and bus-compression bypass states and "
                "listen to the transient behavior."
            ),
            "experiment": (
                "Make one small threshold or input change in the dynamics chain, "
                "then re-measure Crest Factor and Integrated Loudness together."
            ),
        },
        "channel_balance_db": {
            "code": "channel_balance_outside_genre_range",
            "unit": "dB",
            "meaning_high": "L/R RMS Difference is above the Genre Profile master distribution.",
            "meaning_low": "L/R RMS Difference is below the Genre Profile master distribution.",
            "verification": (
                "Listen to the same section in Left, Right, and Mono to determine "
                "whether this is arrangement intent or persistent level offset."
            ),
            "experiment": (
                "Try a small channel-gain correction only if a persistent, "
                "unwanted offset is confirmed."
            ),
        },
    }
    findings: List[Dict[str, Any]] = []
    for metric in comparison["numeric_metrics"]:
        if metric["outside_interquartile_range"] is not True:
            continue
        p25 = float(metric["target_p25"])
        p75 = float(metric["target_p75"])
        interquartile_range = p75 - p25
        if interquartile_range <= 0.0:
            continue
        lower_fence = (
            p25
            - GENRE_NUMERIC_OUTLIER_IQR_MULTIPLIER * interquartile_range
        )
        upper_fence = (
            p75
            + GENRE_NUMERIC_OUTLIER_IQR_MULTIPLIER * interquartile_range
        )
        subject_value = float(metric["subject_value"])
        if lower_fence <= subject_value <= upper_fence:
            continue
        definition = definitions[metric["metric"]]
        is_high = subject_value > upper_fence
        findings.append(
            {
                "code": definition["code"],
                "evidence": {
                    "metric": metric["metric"],
                    "value": round(metric["subject_value"], 3),
                    "unit": definition["unit"],
                    "comparison_source": "genre",
                },
                "observation": (
                    f"Measured value is {subject_value:.2f} "
                    f"{definition['unit']}; Genre Profile p25–p75 is "
                    f"{p25:.2f}–{p75:.2f} {definition['unit']}, with a "
                    f"conservative outer fence of {lower_fence:.2f}–"
                    f"{upper_fence:.2f} {definition['unit']}."
                ),
                "possible_meaning": (
                    definition["meaning_high"]
                    if is_high
                    else definition["meaning_low"]
                ),
                "verification": definition["verification"],
                "experiment": definition["experiment"],
                "confidence": "medium",
            }
        )
    return findings


def _mono_compatibility_findings(
    measurement: Dict[str, Any],
    analysis_stage: str,
) -> List[Dict[str, Any]]:
    findings = []
    ranked_regions = sorted(
        measurement["loss_regions"],
        key=lambda region: region["median_loss_db"],
    )[:3]
    for region in ranked_regions:
        low = float(region["low_hz"])
        high = float(region["high_hz"])
        loss = float(region["median_loss_db"])
        if analysis_stage == "master":
            possible_meaning = (
                "L/R cancellation or strong stereo decorrelation may occur in "
                "this range. The loss may be introduced by the mastering chain "
                "or carried from the Premaster; the stereo file cannot identify "
                "the source by itself."
            )
            verification = (
                "Loudness-match the Premaster and Master, then listen in Mono. "
                "Bypass stereo imaging, M/S EQ or dynamics, and band-specific "
                "stereo-width processing in the mastering chain one at a time."
            )
            experiment = (
                "If the loss is introduced by the mastering chain, slightly "
                "reduce Side gain or Stereo Width in the affected band and "
                "re-measure. If the same loss exists in the Premaster, consider "
                "a Mix revision instead of a heavy Master repair."
            )
        else:
            possible_meaning = (
                "L/R cancellation or strong stereo decorrelation may occur in "
                "this range. The stereo file cannot identify which instrument "
                "causes it."
            )
            verification = (
                "Listen to the same section in Mono. Bypass Widener, Chorus, "
                "Delay, polarity, and time-alignment processing on sources or "
                "buses active in this range one at a time."
            )
            experiment = (
                "Slightly reduce Side content or narrow Stereo Width in this "
                "range on the relevant source or bus, then re-measure. If "
                "masking is confirmed by listening, test Dynamic EQ or "
                "Sidechain separately."
            )
        findings.append(
            {
                "code": "mono_fold_down_loss",
                "evidence": {
                    "metric": "mono_fold_down_loss",
                    "value": round(loss, 3),
                    "unit": "dB",
                    "frequency_low_hz": round(low, 3),
                    "frequency_high_hz": round(high, 3),
                    "comparison_source": "direct",
                },
                "observation": (
                    f"A median {abs(loss):.1f} dB energy loss was measured during "
                    f"Mono Fold-down at {low:.0f}–{high:.0f} Hz."
                ),
                "possible_meaning": possible_meaning,
                "verification": verification,
                "experiment": experiment,
                "confidence": "medium",
            }
        )
    return findings


def _reference_comparison(
    mix: Dict[str, Any],
    reference: Dict[str, Any],
    compared_metrics: List[str],
) -> Dict[str, Any]:
    reference_bands = {
        center: float(band["loudness_relative_db"])
        for center, band in _relative_band_map(reference).items()
    }
    reference_levels = {
        center: float(band["level_dbfs"])
        for center, band in _relative_band_map(reference).items()
        if band.get("level_dbfs") is not None
    }
    mix_analysis = mix["analysis"]
    reference_analysis = reference["analysis"]
    numeric_sources = {
        "integrated_lufs": (
            mix_analysis["integrated_lufs"],
            reference_analysis["integrated_lufs"],
        ),
        "sample_peak_dbfs": (
            mix_analysis["sample_peak_dbfs"],
            reference_analysis["sample_peak_dbfs"],
        ),
        "crest_factor_db": (
            mix_analysis["crest_factor_db"],
            reference_analysis["crest_factor_db"],
        ),
        "channel_balance_db": (
            mix["channel_balance_db"],
            reference["channel_balance_db"],
        ),
    }
    return {
        "source": "reference",
        "target_id": None,
        "target_name": reference_analysis.get("filename") or "reference",
        "spectral_deltas": (
            _spectral_deltas(mix, reference_bands, target_levels=reference_levels)
            if "loudness_relative_spectrum" in compared_metrics
            else []
        ),
        "numeric_metrics": [
            {
                "metric": metric,
                "subject_value": float(values[0]),
                "target_value": float(values[1]),
                "delta": round(float(values[0]) - float(values[1]), 3),
            }
            for metric, values in numeric_sources.items()
            if metric in compared_metrics
            and values[0] is not None
            and values[1] is not None
        ],
        "integrated_lufs_delta": (
            _optional_delta(
                mix_analysis["integrated_lufs"],
                reference_analysis["integrated_lufs"],
            )
            if "integrated_lufs" in compared_metrics
            else None
        ),
        "sample_peak_delta_db": (
            _optional_delta(
                mix_analysis["sample_peak_dbfs"],
                reference_analysis["sample_peak_dbfs"],
            )
            if "sample_peak_dbfs" in compared_metrics
            else None
        ),
        "crest_factor_delta_db": (
            _optional_delta(
                mix_analysis["crest_factor_db"],
                reference_analysis["crest_factor_db"],
            )
            if "crest_factor_db" in compared_metrics
            else None
        ),
        "channel_balance_delta_db": (
            _optional_delta(
                mix["channel_balance_db"],
                reference["channel_balance_db"],
            )
            if "channel_balance_db" in compared_metrics
            else None
        ),
        "reference_analysis": reference_analysis,
    }


def _genre_comparison(
    mix: Dict[str, Any],
    profile: Dict[str, Any],
    compared_metrics: List[str],
) -> Dict[str, Any]:
    distributions = profile.get("master_metric_distributions", {})
    mix_analysis = mix["analysis"]

    def median(metric: str) -> Optional[float]:
        distribution = distributions.get(metric, {})
        value = distribution.get("median")
        return None if value is None else float(value)

    numeric_sources = {
        "integrated_lufs": mix_analysis["integrated_lufs"],
        "sample_peak_dbfs": mix_analysis["sample_peak_dbfs"],
        "crest_factor_db": mix_analysis["crest_factor_db"],
        "channel_balance_db": mix["channel_balance_db"],
    }
    numeric_metrics = []
    for metric, subject_value in numeric_sources.items():
        distribution = distributions.get(metric, {})
        target_value = distribution.get("median")
        p25 = distribution.get("p25")
        p75 = distribution.get("p75")
        if (
            metric not in compared_metrics
            or subject_value is None
            or target_value is None
        ):
            continue
        numeric_metrics.append(
            {
                "metric": metric,
                "subject_value": float(subject_value),
                "target_value": float(target_value),
                "delta": round(float(subject_value) - float(target_value), 3),
                "target_p25": p25,
                "target_p75": p75,
                "outside_interquartile_range": (
                    None
                    if p25 is None or p75 is None
                    else not float(p25) <= float(subject_value) <= float(p75)
                ),
            }
        )

    return {
        "source": "genre",
        "target_id": profile["id"],
        "target_name": profile["name"],
        "spectral_deltas": (
            _spectral_deltas(
                mix,
                _profile_band_map(profile),
                _profile_band_ranges(profile),
            )
            if "loudness_relative_spectrum" in compared_metrics
            else []
        ),
        "numeric_metrics": numeric_metrics,
        "integrated_lufs_delta": (
            _optional_delta(
                mix_analysis["integrated_lufs"],
                median("integrated_lufs"),
            )
            if "integrated_lufs" in compared_metrics
            else None
        ),
        "sample_peak_delta_db": (
            _optional_delta(
                mix_analysis["sample_peak_dbfs"],
                median("sample_peak_dbfs"),
            )
            if "sample_peak_dbfs" in compared_metrics
            else None
        ),
        "crest_factor_delta_db": (
            _optional_delta(
                mix_analysis["crest_factor_db"],
                median("crest_factor_db"),
            )
            if "crest_factor_db" in compared_metrics
            else None
        ),
        "channel_balance_delta_db": (
            _optional_delta(
                mix["channel_balance_db"],
                median("channel_balance_db"),
            )
            if "channel_balance_db" in compared_metrics
            else None
        ),
        "reference_analysis": None,
    }


def _remove_unavailable_metrics(
    compared_metrics: List[str],
    excluded_metrics: List[Dict[str, str]],
    comparison: Dict[str, Any] | None,
) -> tuple[List[str], List[Dict[str, str]]]:
    if comparison is None:
        return compared_metrics, excluded_metrics
    result_fields = {
        "integrated_lufs": "integrated_lufs_delta",
        "sample_peak_dbfs": "sample_peak_delta_db",
        "crest_factor_db": "crest_factor_delta_db",
        "channel_balance_db": "channel_balance_delta_db",
    }
    available: List[str] = []
    unavailable = {item["metric"] for item in excluded_metrics}
    for metric in compared_metrics:
        has_value = (
            bool(comparison["spectral_deltas"])
            if metric == "loudness_relative_spectrum"
            else comparison[result_fields[metric]] is not None
        )
        if has_value:
            available.append(metric)
        elif metric not in unavailable:
            excluded_metrics.append(
                {
                    "metric": metric,
                    "reason": (
                        "The measurement required for this comparison is "
                        "unavailable on one side or absent from the profile "
                        "distribution."
                    ),
                }
            )
    return available, excluded_metrics


def analyze_mix(
    mix_path: str | Path,
    mix_filename: str,
    reference_path: str | Path | None = None,
    reference_filename: str | None = None,
    selected_genre: str | None = None,
    genre_profile: Dict[str, Any] | None = None,
    genre_profiles: Iterable[Dict[str, Any]] | None = None,
    use_closest_profile: bool = False,
    analysis_stage: str = "mix",
    reference_stage: str | None = None,
    max_duration_seconds: float | None = None,
) -> Dict[str, Any]:
    if analysis_stage not in {"mix", "master"}:
        raise ValueError("analysis_stage must be 'mix' or 'master'.")
    if reference_path is not None and reference_stage not in {"mix", "master"}:
        raise ValueError(
            "reference_stage must be declared as 'mix' or 'master' "
            "when a reference is submitted."
        )
    if reference_path is None and reference_stage is not None:
        raise ValueError("reference_stage cannot be set without a reference file.")

    mix = extract_mix_features(
        mix_path,
        mix_filename,
        max_duration_seconds=max_duration_seconds,
    )
    affinity_profiles = list(genre_profiles or ())
    if not affinity_profiles and genre_profile is not None:
        affinity_profiles = [genre_profile]
    genre_affinity = _genre_affinity(
        mix,
        affinity_profiles,
        analysis_stage,
    )
    comparison_profile = genre_profile
    if (
        reference_path is None
        and comparison_profile is None
        and use_closest_profile
        and genre_affinity
    ):
        nearest_profile_id = genre_affinity[0]["profile_id"]
        comparison_profile = next(
            (
                profile
                for profile in affinity_profiles
                if profile["id"] == nearest_profile_id
            ),
            None,
        )
    comparison: Optional[Dict[str, Any]] = None
    mode = "general"
    comparison_policy, compared_metrics, excluded_metrics = _comparison_policy(
        analysis_stage,
        reference_stage,
        comparison_profile is not None and reference_path is None,
    )

    if reference_path is not None:
        reference = extract_mix_features(
            reference_path,
            reference_filename or "reference",
            max_duration_seconds=max_duration_seconds,
        )
        comparison = _reference_comparison(mix, reference, compared_metrics)
        mode = "reference"
    elif comparison_profile is not None:
        comparison = _genre_comparison(mix, comparison_profile, compared_metrics)
        mode = "genre" if genre_profile is not None else "affinity"

    compared_metrics, excluded_metrics = _remove_unavailable_metrics(
        compared_metrics,
        excluded_metrics,
        comparison,
    )
    findings = (
        []
        if comparison is None
        else _findings(
            comparison["spectral_deltas"],
            comparison["source"],
            analysis_stage,
        )
    )
    if (
        comparison is not None
        and comparison["source"] == "genre"
        and analysis_stage == "master"
    ):
        findings.extend(_master_genre_numeric_findings(comparison))
    findings.extend(
        _mono_compatibility_findings(
            mix["mono_compatibility"],
            analysis_stage,
        )
    )
    stage_name = "Mix" if analysis_stage == "mix" else "Master"
    if mode == "reference":
        summary = (
            f"The {stage_name} was compared with the user-declared "
            f"{reference_stage} Reference Track using stage-appropriate "
            "measurements."
        )
    elif mode == "genre":
        summary = (
            f"The {stage_name} was compared with the selected Genre Profile, "
            "built from measured released tracks, using stage-appropriate "
            "measurements."
        )
    elif mode == "affinity":
        summary = (
            "No Genre Profile was selected. The technically nearest measured "
            f"profile was {comparison['target_name']}, and {analysis_stage} "
            "guidance was generated from that profile."
        )
    else:
        summary = (
            "No Genre Profile or Reference Track was provided. The report "
            "contains direct measurements without comparative correction "
            "guidance. Direct Mono Fold-down findings are still reported."
        )
    limitations = [
        (
            "A stereo file cannot identify which instrument or channel causes "
            "a spectral difference."
        ),
        (
            "Mono Fold-down loss can indicate L/R cancellation or decorrelation; "
            "it cannot prove inter-source masking or a specific instrument "
            "conflict by itself."
        ),
    ]
    if comparison is None:
        limitations.insert(
            0,
            (
                "No Genre Profile or Reference Track was provided; results are "
                "direct measurements computed from the file."
            ),
        )
        limitations.append(
            "Spectral values are descriptive and do not determine whether a "
            "Mix is good or bad."
        )
    elif mode == "reference":
        limitations.append(
            (
                "The comparison summarizes the full track; arrangement and "
                "section differences can affect the result."
            )
        )
        if not findings:
            limitations.append(
                "Measured differences did not cross the finding threshold; no "
                "correction was suggested."
            )
    elif mode == "genre":
        limitations.append(
            (
                "A Genre Profile aggregates multiple releases; it is not an "
                "artistic target or mandatory threshold."
            )
        )
        if not findings:
            limitations.append(
                "Measurements produced no finding outside the Genre Profile "
                "distribution; no correction was suggested."
            )
    else:
        limitations.append(
            (
                "The nearest profile reports technical distance only among "
                "stored Genre Profiles; it is not a genre classification."
            )
        )
        limitations.append(
            (
                "Automated guidance uses the nearest-profile context and should "
                "not be treated as an artistic target or mandatory correction."
            )
        )
        if not findings:
            limitations.append(
                "Measurements produced no finding outside the nearest profile; "
                "no correction was suggested."
            )

    return {
        "mix_contract_version": MIX_CONTRACT_VERSION,
        "finding_policy_version": FINDING_POLICY_VERSION,
        "mode": mode,
        "analysis_stage": analysis_stage,
        "reference_stage": reference_stage,
        "comparison_policy": comparison_policy,
        "compared_metrics": compared_metrics,
        "excluded_metrics": excluded_metrics,
        "summary": summary,
        "selected_genre": selected_genre,
        "recommendation_basis": (
            None
            if comparison is None
            else (
                "closest_profile"
                if mode == "affinity"
                else comparison["source"]
            )
        ),
        "genre_affinity": genre_affinity,
        "genre_affinity_notice": (
            "This is not a genre classification. The track is ranked only by "
            "technical proximity to stored measurement profiles."
        ),
        "mix": mix,
        "comparison": comparison,
        "findings": findings,
        "recommendations_enabled": bool(findings),
        "limitations": limitations,
    }
