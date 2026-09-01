"""Deterministic-first, read-only gain-staging decisions.

Source-file measurements are a reference, never a claim about a processed
Ableton track.  A Probe is requested only when the device chain prevents a
safe pre-fader estimate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .gain_staging import measure_audio_file
from .project_analyzer import analyze_tracks, direct_devices, value

DETERMINISTIC_GAIN = "DETERMINISTIC_GAIN"
BOUNDED_ESTIMATE = "BOUNDED_ESTIMATE"
CONTENT_DEPENDENT = "CONTENT_DEPENDENT"
UNKNOWN = "UNKNOWN"

_DETERMINISTIC = {"StereoGain", "Eq8"}
_BOUNDED = {"Pan", "Balance", "MonoUtility", "Width"}
_CONTENT = {
    "Compressor", "Compressor2", "GlueCompressor", "Limiter", "Gate", "Eq8Dynamic",
    "MultibandDynamics", "Saturator", "Overdrive", "Redux", "DynamicTube", "Reverb",
    "Corpus", "Amp", "Pedal", "DrumBuss", "Roar", "AutoPan",
}
_HIGH_RISK = {"Limiter", "Saturator", "Overdrive", "Redux", "DynamicTube", "Roar", "Amp", "Pedal"}
_ROLE_TARGETS = {
    "kick": -8.0, "snare": -9.5, "clap": -9.5, "hi_hat": -14.0,
    "percussion": -14.0, "bass": -9.5, "sub_bass": -9.5,
    "lead_vocal": -9.0, "backing_vocal": -14.0, "synth_lead": -12.0,
    "synth_pad": -13.0, "keys": -13.0, "guitar": -13.0, "sample": -13.0,
}


def classify_device(device: ET.Element) -> str:
    """Classify exact native device tags; unrecognised devices are unsafe."""
    if device.tag in _DETERMINISTIC:
        return DETERMINISTIC_GAIN
    if device.tag in _BOUNDED:
        return BOUNDED_ESTIMATE
    if device.tag in _CONTENT:
        return CONTENT_DEPENDENT
    return UNKNOWN


def classify_role(name: str) -> str:
    text = name.casefold()
    checks = (("kick", "kick"), ("snare", "snare"), ("clap", "clap"),
              ("hat", "hi_hat"), ("perc", "percussion"), ("sub", "sub_bass"),
              ("bass", "bass"), ("lead vocal", "lead_vocal"), ("vocal", "backing_vocal"),
              ("pad", "synth_pad"), ("lead", "synth_lead"), ("keys", "keys"),
              ("guitar", "guitar"), ("sample", "sample"))
    return next((role for token, role in checks if token in text), "unknown")


def _clip_gain_db(track: ET.Element) -> tuple[float | None, str | None]:
    clips = list(track.iter("AudioClip"))
    if len(clips) != 1:
        return None, "A single clip is required for a deterministic source estimate"
    raw = value(clips[0].find("./SampleVolume"), "1")
    try:
        linear = float(raw)
    except ValueError:
        return None, "Clip gain is not numeric"
    if linear <= 0:
        return None, "Clip gain is not positive"
    return 20.0 * math.log10(linear), None


def _chain_decision(devices: list[ET.Element]) -> tuple[bool, str, list[str]]:
    classes = [classify_device(device) for device in devices]
    if UNKNOWN in classes or any(device.tag in _HIGH_RISK for device in devices):
        return True, "LOW", classes
    if CONTENT_DEPENDENT in classes:
        return False, "MEDIUM", classes
    if BOUNDED_ESTIMATE in classes:
        return False, "MEDIUM", classes
    return False, "HIGH", classes


def _clip_measurement(track: ET.Element, als_path: Path) -> tuple[dict[str, float | None] | None, list[str], bool]:
    """Aggregate arrangement clips conservatively without simulating DSP/warping."""
    items, warnings, windows = [], [], []
    for clip in track.iter("AudioClip"):
        if value(clip.find("./Disabled"), "false").lower() == "true":
            continue
        raw = value(clip.find(".//SampleRef/FileRef/Path"), "")
        relative = value(clip.find(".//SampleRef/FileRef/RelativePath"), "")
        candidates = ([Path(raw)] if raw else []) + ([als_path.parent / relative] if relative else [])
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            warnings.append(f"Audio source unresolved: {relative or raw or 'missing SampleRef'}")
            continue
        metric = measure_audio_file(path, include_lufs=False)
        if metric.get("peak") is None or metric.get("rms") is None:
            warnings.append(f"Invalid or silent clip measurement: {path.name}")
            continue
        try:
            gain = 20.0 * math.log10(float(value(clip.find("./SampleVolume"), "1")))
        except (ValueError, ZeroDivisionError):
            warnings.append(f"Clip gain unresolved: {path.name}"); continue
        start, end = float(value(clip.find("./CurrentStart"), "0")), float(value(clip.find("./CurrentEnd"), "0"))
        windows.append((start, end))
        items.append((float(metric["peak"]) + gain, float(metric["rms"]) + gain, max(end - start, 0.0), metric))
        if value(clip.find("./IsWarped"), "false").lower() == "true": warnings.append("Warping: source estimate")
        if clip.find("./Fades") is not None: warnings.append("Fades: source estimate")
    if not items:
        return None, list(dict.fromkeys(warnings)), False
    overlap = any(a[0] < b[1] and b[0] < a[1] for n, a in enumerate(windows) for b in windows[n + 1:])
    if overlap: warnings.append("Overlapping clips: summed peak is uncertain")
    weights = [item[2] or 1.0 for item in items]
    power = sum((10 ** (item[1] / 20)) ** 2 * weight for item, weight in zip(items, weights)) / sum(weights)
    peak = max(item[0] for item in items)
    dc = sum((item[3].get("dc_offset") or 0.0) * weight for item, weight in zip(items, weights)) / sum(weights)
    silence = sum((item[3].get("silence_ratio") or 0.0) * weight for item, weight in zip(items, weights)) / sum(weights)
    rms = 20 * math.log10(math.sqrt(power)) if power else None
    return {"peak": round(peak, 6), "rms": round(rms, 6) if rms is not None else None, "dc_offset": round(dc, 12), "silence_ratio": round(silence, 6), "crest_factor_db": round(peak - rms, 6) if rms is not None else None}, list(dict.fromkeys(warnings)), overlap


def analyze_gain_staging_v2(root: ET.Element, als_path: Path) -> dict[str, Any]:
    tracks: list[dict[str, Any]] = []
    for info in analyze_tracks(root):
        if info.track_type not in {"AudioTrack", "MidiTrack", "GroupTrack"}:
            continue
        role = classify_role(info.name)
        devices = direct_devices(info.element)
        required, confidence, device_classes = _chain_decision(devices)
        reasons = [f"{device.tag}: {category}" for device, category in zip(devices, device_classes)]
        row: dict[str, Any] = {
            "track": info.name or "(unnamed)", "track_id": info.track_id, "track_type": info.track_type,
            "role": role, "devices": [{"name": d.tag, "classification": c} for d, c in zip(devices, device_classes)],
            "clip_peak": None, "clip_rms": None, "crest_factor_db": None, "true_peak": None,
            "dc_offset": None, "silence_ratio": None, "estimated_prefader_peak": None,
            "measurement_required": required, "confidence": confidence,
            "recommended_fader_db": None, "reason": reasons or ["No devices: deterministic source-to-pre-fader path"],
        }
        if info.track_type != "AudioTrack":
            row.update(measurement_required=True, confidence="LOW", decision="MEASUREMENT_REQUIRED")
            row["reason"].append("Bus/MIDI output has no direct source clip")
            tracks.append(row); continue
        metrics, warnings, overlap = _clip_measurement(info.element, als_path)
        if metrics is None:
            row.update(measurement_required=True, confidence="LOW", decision="MEASUREMENT_REQUIRED")
            row["reason"].extend(warnings or ["Audio source is unresolved or invalid"])
            tracks.append(row); continue
        row.update(clip_peak=metrics.get("peak"), clip_rms=metrics.get("rms"),
                   crest_factor_db=metrics.get("crest_factor_db"), true_peak=None,
                   dc_offset=metrics.get("dc_offset"), silence_ratio=metrics.get("silence_ratio"))
        row["reason"].extend(warnings)
        if overlap:
            row.update(measurement_required=True, confidence="LOW", decision="MEASUREMENT_REQUIRED")
            row["reason"].append("Overlap prevents a safe summed-peak estimate")
        elif not row["measurement_required"]:
            row["estimated_prefader_peak"] = float(metrics["peak"])
            target = _ROLE_TARGETS.get(role, -12.0)
            row["recommended_fader_db"] = round(target - row["estimated_prefader_peak"], 6)
            if warnings and confidence == "HIGH":
                confidence = row["confidence"] = "MEDIUM"
            row["decision"] = "DETERMINISTIC" if confidence == "HIGH" else "ESTIMATED"
            row["reason"].append("Aggregated clip peak; track fader is the only recommended control")
        else:
            row["decision"] = "MEASUREMENT_REQUIRED"
        tracks.append(row)
    for track in tracks:
        track.setdefault("decision", "MEASUREMENT_REQUIRED" if track["measurement_required"] else "UNRESOLVED")
    summary = {"total_tracks": len(tracks), "deterministic": sum(t["decision"] == "DETERMINISTIC" for t in tracks),
               "estimated": sum(t["decision"] == "ESTIMATED" for t in tracks),
               "probe_required": sum(t["measurement_required"] for t in tracks),
               "unresolved": sum(t["decision"] == "UNRESOLVED" for t in tracks),
               "unknown_device": sum(any(d["classification"] == UNKNOWN for d in t["devices"]) for t in tracks)}
    trigger_distribution = {"bus_or_midi_no_direct_source": sum("Bus/MIDI output" in " ".join(t["reason"]) for t in tracks)}
    return {"schema_version": "2.0", "engine": "deterministic_first", "summary": summary,
            "probe_trigger_distribution": trigger_distribution,
            "policy_comparison": {"previous_probe_required": len(tracks), "current_probe_required": summary["probe_required"], "removed_triggers": ["unknown_role", "multiple_clips", "medium_risk_content_dependent"]},
            "tracks": tracks}


def markdown_v2(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = ["# Gain Staging Engine v2", "", f"Total Track: {s['total_tracks']}", f"Deterministic: {s['deterministic']}", f"Estimated: {s['estimated']}", f"Probe Required: {s['probe_required']}", f"Unresolved: {s['unresolved']}", f"Unknown Device: {s['unknown_device']}", "", "| Track | Decision | Role | Clip peak | Estimated pre-fader | Probe | Confidence | Fader recommendation |", "|---|---|---|---:|---:|---|---|---:|"]
    for t in report["tracks"]:
        lines.append(f"| {t['track']} | {t['decision']} | {t['role']} | {t['clip_peak'] if t['clip_peak'] is not None else 'unknown'} | {t['estimated_prefader_peak'] if t['estimated_prefader_peak'] is not None else 'unknown'} | {t['measurement_required']} | {t['confidence']} | {t['recommended_fader_db'] if t['recommended_fader_db'] is not None else 'none'} |")
    return "\n".join(lines) + "\n"
