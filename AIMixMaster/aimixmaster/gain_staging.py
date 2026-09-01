"""Read-only, structural gain-staging analysis for Ableton Live sets.

This module deliberately does not inspect audio media or write ALS XML.  It
reports only gain values that the ALS parameter structure can prove.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
import unicodedata
import xml.etree.ElementTree as ET

# numpy and soundfile are needed only by measure_audio_file. Importing
# them at module level made the whole module -- including the pure-XML
# analysis functions -- unimportable anywhere the audio stack is not
# installed, e.g. the MCP server running on Sensei's venv.

from .project_analyzer import analyze_tracks, direct_devices, track_name, value


SCHEMA_VERSION = "1.0"
MEASUREMENT_STATUS = "unknown_no_audio_measurement"
UTILITY_GAIN_ENCODING = "unverified"


@dataclass(frozen=True)
class GainStageTrackRecord:
    track_name: str
    track_type: str
    parent_bus: str
    current_fader_db: float | None
    utility_gain_db: float | None
    effective_known_gain_db: float | None
    measurement_status: str
    proposed_adjustment_db: None
    proposed_method: str
    confidence: str
    reason: str
    warnings: list[str]
    routing_kind: str
    send_routes: list[str]
    measurement_scope: str
    measured_peak_dbfs: float | None
    measured_rms_dbfs: float | None
    measured_lufs_integrated: float | None
    measured_duration_seconds: float | None
    source_audio_paths: list[str]
    measurement_warnings: list[str]
    policy_version: str | None


@dataclass(frozen=True)
class GainStagingPolicy:
    """Conservative policy for verified full-track renders only."""

    version: str = "render-peak-v1"
    rendered_track_peak_target_dbfs: float = -6.0
    clipping_threshold_dbfs: float = -0.1
    attenuation_trigger_dbfs: float = -3.0
    no_change_floor_dbfs: float = -10.0
    low_level_review_dbfs: float = -18.0
    maximum_attenuation_db: float = -12.0


@dataclass(frozen=True)
class MasterGainStageRecord:
    current_master_fader_db: float | None
    detected_master_devices: list[dict[str, str]]
    limiter_status: str
    clipper_status: str
    compressor_status: str
    headroom_assessment: str
    warnings: list[str]


@dataclass(frozen=True)
class GainStageReport:
    schema_version: str
    tracks: list[GainStageTrackRecord]
    master: MasterGainStageRecord

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def linear_to_db(raw: str) -> float | None:
    try:
        linear = float(raw)
    except (TypeError, ValueError):
        return None
    if linear <= 0:
        return None
    return round(20 * math.log10(linear), 6)


def normalized_device_name(device: ET.Element) -> str:
    aliases = {
        "Eq8": "EQ Eight",
        "Compressor2": "Compressor",
        "GlueCompressor": "Glue Compressor",
        "StereoGain": "Utility",
    }
    return aliases.get(device.tag, device.tag)


def classify_master_processor(device: ET.Element) -> str | None:
    """Classify exact native/normalized device identities; never substrings."""
    native = device.tag
    normalized = normalized_device_name(device)
    if native in {"Limiter"} or normalized == "Limiter":
        return "limiter"
    if native in {"Clipper"} or normalized == "Clipper":
        return "clipper"
    if native in {"Compressor", "Compressor2", "GlueCompressor"} or normalized in {
        "Compressor",
        "Glue Compressor",
    }:
        return "compressor"
    if native in {"Dynamics", "DynamicsProcessor"} or normalized in {
        "Dynamics",
        "Dynamics Processor",
    }:
        return "unknown_dynamics"
    return None


def _utility_gain_db(device: ET.Element) -> tuple[float | None, str | None]:
    """Return dB only when the on-disk parameter encoding is proven.

    Live's StereoGain/Gain value has a numeric range in the fixture, but that
    XML alone does not identify whether its unit is dB, linear, or normalized.
    It is therefore intentionally reported as unknown rather than guessed.
    """
    gain = device.find("./Gain/Manual")
    if gain is None or "Value" not in gain.attrib:
        return None, "Utility Gain parameter is missing"
    return None, (
        "Utility Gain is present but its ALS encoding is unverified; "
        "utility_gain_db is unknown"
    )


def _fader_db(track: ET.Element) -> tuple[float | None, str | None]:
    manual = track.find("./DeviceChain/Mixer/Volume/Manual")
    if manual is None or "Value" not in manual.attrib:
        return None, "Mixer fader value is missing"
    result = linear_to_db(manual.attrib["Value"])
    if result is None:
        return None, "Mixer fader value is not a positive linear gain"
    return result, None


def _send_routes(track: ET.Element, returns_by_index: list[str]) -> list[str]:
    result = []
    for index, holder in enumerate(track.findall("./DeviceChain/Mixer/Sends/TrackSendHolder")):
        manual = holder.find("./Send/Manual")
        if manual is None:
            continue
        try:
            enabled = float(value(manual, "0")) > 0
        except ValueError:
            enabled = False
        if enabled:
            result.append(returns_by_index[index] if index < len(returns_by_index) else f"return_index:{index}")
    return result


def _parent_resolution(
    track: ET.Element,
    groups_by_id: dict[int, str],
    returns_by_id: dict[int, str],
    send_routes: list[str],
) -> tuple[str, str]:
    group_raw = value(track.find("./TrackGroupId"), "")
    try:
        group_id = int(group_raw)
    except ValueError:
        group_id = None
    if group_id is not None and group_id in groups_by_id:
        return groups_by_id[group_id], "direct_group"
    if group_id not in {None, -1}:
        return f"unresolved_group_id:{group_id}", "unresolved"

    target = value(track.find("./DeviceChain/AudioOutputRouting/Target"), "")
    if target == "AudioOut/Main" or track.tag == "MainTrack":
        return "master", "master"
    for return_id, return_name in returns_by_id.items():
        if f"ReturnTrack.{return_id}" in target:
            return return_name, "return"
    if send_routes:
        return "master", "master_with_sends"
    return "unresolved", "unresolved"


def _master_record(root: ET.Element) -> MasterGainStageRecord:
    master = next((track for track in root.iter("MainTrack")), None)
    if master is None:
        return MasterGainStageRecord(
            None, [], "unknown", "unknown", "unknown", "unknown", ["MainTrack is missing"]
        )
    fader, warning = _fader_db(master)
    devices = []
    categories: set[str] = set()
    for device in direct_devices(master):
        name = normalized_device_name(device)
        category = classify_master_processor(device)
        item = {"native_type": device.tag, "name": name}
        if category is not None:
            item["dynamics_classification"] = category
            categories.add(category)
        devices.append(item)
    return MasterGainStageRecord(
        current_master_fader_db=fader,
        detected_master_devices=devices,
        limiter_status="present" if "limiter" in categories else "not_detected",
        clipper_status="present" if "clipper" in categories else "not_detected",
        compressor_status="present" if "compressor" in categories else "not_detected",
        headroom_assessment="unknown_no_audio_measurement",
        warnings=([warning] if warning else [])
        + (["Unclassified dynamics processor detected"] if "unknown_dynamics" in categories else []),
    )


def analyze_gain_staging(root: ET.Element) -> GainStageReport:
    """Build an ALS-order-preserving report without measurements or proposals."""
    track_infos = analyze_tracks(root)
    groups_by_id = {
        info.track_id: info.name or f"group_id:{info.track_id}"
        for info in track_infos
        if info.track_type == "GroupTrack"
    }
    returns = [info for info in track_infos if info.track_type == "ReturnTrack"]
    returns_by_id = {info.track_id: info.name or f"return_id:{info.track_id}" for info in returns}
    return_names = [returns_by_id[info.track_id] for info in returns]
    records = []
    for info in track_infos:
        if info.track_type == "MainTrack":
            continue
        sends = _send_routes(info.element, return_names)
        parent, routing_kind = _parent_resolution(info.element, groups_by_id, returns_by_id, sends)
        fader_db, fader_warning = _fader_db(info.element)
        utility_values = []
        warnings = [fader_warning] if fader_warning else []
        for device in direct_devices(info.element):
            if device.tag == "StereoGain":
                utility_db, utility_warning = _utility_gain_db(device)
                utility_values.append(utility_db)
                if utility_warning:
                    warnings.append(utility_warning)
            elif device.find("./Gain/Manual") is not None:
                warnings.append(
                    f"{normalized_device_name(device)} has an unverified Gain parameter; excluded from effective known gain"
                )
        utility_db = None if not utility_values or any(item is None for item in utility_values) else round(sum(utility_values), 6)
        if fader_db is not None and utility_db is not None:
            effective = round(fader_db + utility_db, 6)
        else:
            effective = fader_db
            if utility_values and utility_db is None:
                warnings.append("Effective known gain excludes unverified Utility Gain")
        if fader_db not in {None, 0.0} and utility_values and utility_db is None:
            warnings.append("Double gain application risk cannot be evaluated because Utility Gain is unknown")
        records.append(
            GainStageTrackRecord(
                track_name=info.name or "(unnamed)",
                track_type=info.track_type,
                parent_bus=parent,
                current_fader_db=fader_db,
                utility_gain_db=utility_db,
                effective_known_gain_db=effective,
                measurement_status=MEASUREMENT_STATUS,
                proposed_adjustment_db=None,
                proposed_method="requires_audio_measurement",
                confidence="none",
                reason="No audio peak, RMS, or LUFS measurement is available in the ALS structural analysis",
                warnings=warnings,
                routing_kind=routing_kind,
                send_routes=sends,
                measurement_scope="unavailable",
                measured_peak_dbfs=None,
                measured_rms_dbfs=None,
                measured_lufs_integrated=None,
                measured_duration_seconds=None,
                source_audio_paths=[],
                measurement_warnings=["Audio measurement was not requested"],
                policy_version=None,
            )
        )
    return GainStageReport(SCHEMA_VERSION, records, _master_record(root))


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def _dbfs(value: float) -> float | None:
    if value <= 0:
        return None
    return round(20 * math.log10(value), 6)


def _measure_lufs(path: Path) -> tuple[float | None, str | None]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None, "Integrated LUFS unavailable: ffmpeg was not found"
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-filter_complex", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Integrated LUFS measurement failed: {type(exc).__name__}"
    matches = re.findall(r"I:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*LUFS", result.stderr)
    if result.returncode or not matches or matches[-1] == "-inf":
        return None, "Integrated LUFS unavailable from ffmpeg ebur128 output"
    return round(float(matches[-1]), 6), None


def measure_audio_file(path: Path, *, include_lufs: bool = True) -> dict[str, float | None | str | list[str]]:
    import numpy as np
    import soundfile as sf

    """Measure a real media file; sample peak is explicitly not true peak."""
    warnings: list[str] = ["Peak is sample peak, not true peak"]
    try:
        with sf.SoundFile(path) as source:
            frames = len(source)
            sample_rate = source.samplerate
            sum_squares = 0.0
            sum_samples = 0.0
            sample_count = 0
            silent_samples = 0
            peak = 0.0
            for block in source.blocks(blocksize=65536, dtype="float64", always_2d=True):
                peak = max(peak, float(np.max(np.abs(block))) if block.size else 0.0)
                sum_squares += float(np.sum(np.square(block)))
                sum_samples += float(np.sum(block))
                sample_count += int(block.size)
                silent_samples += int(np.count_nonzero(np.abs(block) <= 1e-12))
    except (OSError, RuntimeError) as exc:
        return {"peak": None, "rms": None, "lufs": None, "duration": None, "crest_factor_db": None, "true_peak": None, "dc_offset": None, "silence_ratio": None, "warnings": [f"Audio measurement failed: {exc}"]}
    rms = math.sqrt(sum_squares / sample_count) if sample_count else 0.0
    peak_dbfs = _dbfs(peak)
    rms_dbfs = _dbfs(rms)
    if peak_dbfs is None:
        warnings.append("Measured digital silence; peak and RMS dBFS are undefined (-∞)")
    lufs, lufs_warning = (None, None) if not include_lufs else _measure_lufs(path)
    if lufs_warning:
        warnings.append(lufs_warning)
    return {
        "peak": peak_dbfs,
        "rms": rms_dbfs,
        "lufs": lufs,
        "duration": round(frames / sample_rate, 6) if sample_rate else None,
        "crest_factor_db": round(peak_dbfs - rms_dbfs, 6) if peak_dbfs is not None and rms_dbfs is not None else None,
        "true_peak": None,
        "dc_offset": round(sum_samples / sample_count, 12) if sample_count else None,
        "silence_ratio": round(silent_samples / sample_count, 6) if sample_count else None,
        "warnings": warnings,
    }


def _clip_sources(track: ET.Element, als_path: Path) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    warnings: list[str] = []
    project_dir = als_path.parent
    clips = list(track.iter("AudioClip"))
    if len(clips) > 1:
        warnings.append("Multiple clips: a source-file measurement is not a complete track-output measurement")
    for clip in clips:
        raw = value(clip.find(".//SampleRef/FileRef/Path"), "")
        relative = value(clip.find(".//SampleRef/FileRef/RelativePath"), "")
        candidates = [Path(raw)] if raw else []
        if relative:
            candidates.append(project_dir / relative)
        found = next((candidate for candidate in candidates if candidate.is_file()), None)
        if found is None:
            warnings.append(f"Audio source unresolved: {relative or raw or 'missing SampleRef'}")
            continue
        if found not in paths:
            paths.append(found)
        if value(clip.find("./IsWarped"), "false").lower() == "true":
            warnings.append("Warping is enabled; source measurement excludes warp processing")
        sample_volume = value(clip.find("./SampleVolume"), "1")
        if sample_volume not in {"", "1", "1.0"}:
            warnings.append("Clip gain is non-default; source measurement excludes clip gain")
        if clip.find("./Fades") is not None or clip.find("./FadeIn") is not None or clip.find("./FadeOut") is not None:
            warnings.append("Clip fades are present; source measurement excludes fades")
    return paths, list(dict.fromkeys(warnings))


def _render_match(track_name: str, renders_dir: Path) -> tuple[Path | None, str | None]:
    if not renders_dir.is_dir():
        return None, f"Renders directory is unavailable: {renders_dir}"
    wanted = _normalized_name(track_name)
    candidates = sorted(path for path in renders_dir.iterdir() if path.is_file() and _normalized_name(path.stem) == wanted)
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, f"Ambiguous render match for track {track_name!r}; no render selected"
    return None, None


def add_audio_measurements(
    root: ET.Element,
    report: GainStageReport,
    als_path: Path,
    renders_dir: Path | None = None,
    render_paths: dict[int, Path] | None = None,
    policy: GainStagingPolicy = GainStagingPolicy(),
) -> GainStageReport:
    """Enrich a structural report with real-file measurements, never ALS writes."""
    infos = [info for info in analyze_tracks(root) if info.track_type != "MainTrack"]
    updated = []
    for record, info in zip(report.tracks, infos):
        if info.track_type != "AudioTrack":
            updated.append(replace(record, measurement_warnings=["No direct audio file is expected for this track type"], source_audio_paths=[]))
            continue
        if render_paths and info.track_id in render_paths:
            render, render_warning = render_paths[info.track_id], None
        else:
            render, render_warning = _render_match(record.track_name, renders_dir) if renders_dir else (None, None)
        source_paths, source_warnings = _clip_sources(info.element, als_path)
        if render_warning:
            source_warnings.append(render_warning)
        chosen = render or (source_paths[0] if len(source_paths) == 1 else None)
        scope = "rendered_track" if render else "source_file" if chosen else "unavailable"
        if len(source_paths) > 1 and not render:
            source_warnings.append("Multiple distinct source files; no aggregate measurement was selected")
            chosen = None
            scope = "unavailable"
        if chosen is None:
            updated.append(replace(record, measurement_scope=scope, source_audio_paths=[str(path) for path in source_paths], measurement_warnings=source_warnings))
            continue
        measured = measure_audio_file(chosen)
        warnings = source_warnings + list(measured["warnings"])
        status = "measured_rendered_track" if scope == "rendered_track" else "measured_source_file_not_track_output"
        adjustment = None
        method = "requires_track_render"
        confidence = "none"
        reason = "Measured source file is not presented as complete track output"
        peak = measured["peak"]
        if scope == "rendered_track" and peak is not None:
            peak = float(peak)
            confidence = "high"
            if peak > policy.attenuation_trigger_dbfs:
                adjustment = round(max(policy.maximum_attenuation_db, policy.rendered_track_peak_target_dbfs - peak), 6)
                method = "rendered_track_peak_attenuation"
                reason = f"Policy {policy.version}: sample peak above {policy.attenuation_trigger_dbfs} dBFS"
            elif policy.no_change_floor_dbfs <= peak <= policy.attenuation_trigger_dbfs:
                adjustment = 0.0
                method = "rendered_track_peak_no_change"
                reason = f"Policy {policy.version}: sample peak is within the conservative no-change band"
            elif peak < policy.low_level_review_dbfs:
                method = "manual_review_low_level"
                confidence = "medium"
                reason = f"Policy {policy.version}: low-level render is not automatically boosted"
            else:
                method = "manual_review_between_policy_bands"
                confidence = "medium"
                reason = f"Policy {policy.version}: no automatic boost is produced"
            if peak >= policy.clipping_threshold_dbfs:
                warnings.insert(0, "Clipping risk: rendered track sample peak is at or above the policy threshold")
        updated.append(replace(
            record,
            measurement_status=status,
            proposed_adjustment_db=adjustment,
            proposed_method=method,
            confidence=confidence,
            reason=reason,
            measurement_scope=scope,
            measured_peak_dbfs=peak if isinstance(peak, float) else None,
            measured_rms_dbfs=measured["rms"] if isinstance(measured["rms"], float) else None,
            measured_lufs_integrated=measured["lufs"] if isinstance(measured["lufs"], float) else None,
            measured_duration_seconds=measured["duration"] if isinstance(measured["duration"], float) else None,
            source_audio_paths=[str(render)] if render else [str(path) for path in source_paths],
            measurement_warnings=list(dict.fromkeys(warnings)),
            policy_version=policy.version if scope == "rendered_track" else None,
        ))
    return GainStageReport(report.schema_version, updated, report.master)


def markdown_report(report: GainStageReport) -> str:
    lines = ["# Gain Staging Dry-Run Report", "", f"Schema version: `{report.schema_version}`", "", "## Tracks", ""]
    for track in report.tracks:
        lines.extend([
            f"### {track.track_name}",
            f"- Type: `{track.track_type}`",
            f"- Parent bus: `{track.parent_bus}` ({track.routing_kind})",
            f"- Fader: `{track.current_fader_db if track.current_fader_db is not None else 'unknown'}` dB",
            f"- Utility gain: `{track.utility_gain_db if track.utility_gain_db is not None else 'unknown'}` dB",
            f"- Effective known gain: `{track.effective_known_gain_db if track.effective_known_gain_db is not None else 'unknown'}` dB",
            f"- Measurement: `{track.measurement_status}`",
            f"- Measurement scope: `{track.measurement_scope}`",
            f"- Sample peak: `{track.measured_peak_dbfs if track.measured_peak_dbfs is not None else 'unknown'}` dBFS",
            f"- RMS: `{track.measured_rms_dbfs if track.measured_rms_dbfs is not None else 'unknown'}` dBFS",
            f"- Integrated LUFS: `{track.measured_lufs_integrated if track.measured_lufs_integrated is not None else 'unknown'}`",
            f"- Measured duration: `{track.measured_duration_seconds if track.measured_duration_seconds is not None else 'unknown'}` seconds",
            f"- Sources: {', '.join(track.source_audio_paths) if track.source_audio_paths else 'none'}",
            f"- Proposal: `{track.proposed_method}`",
            f"- Policy: `{track.policy_version or 'none'}`",
            f"- Confidence: `{track.confidence}`",
            f"- Reason: {track.reason}",
            f"- Warnings: {'; '.join(track.warnings) if track.warnings else 'none'}",
            f"- Measurement warnings: {'; '.join(track.measurement_warnings) if track.measurement_warnings else 'none'}",
            "",
        ])
    master = report.master
    lines.extend(["## Master", "", f"- Master fader: `{master.current_master_fader_db if master.current_master_fader_db is not None else 'unknown'}` dB", f"- Limiter: `{master.limiter_status}`", f"- Clipper: `{master.clipper_status}`", f"- Compressor: `{master.compressor_status}`", f"- Headroom: `{master.headroom_assessment}`", "- Devices:"])
    lines.extend([f"  - {device['name']} (`{device['native_type']}`)" for device in master.detected_master_devices] or ["  - none detected"])
    lines.extend([f"- Warnings: {'; '.join(master.warnings) if master.warnings else 'none'}", ""])
    return "\n".join(lines)
