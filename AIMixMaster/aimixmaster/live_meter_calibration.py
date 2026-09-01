"""Offline calibration of MixConsoleLive2 raw meter sessions."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import mean

from .live_meter import RAW_SILENCE_THRESHOLD


POINTS = (-30.0, -24.0, -18.0, -12.0, -6.0, -3.0, 0.0)
SEGMENTS = [(2.0, 5.0, value) for value in POINTS]


def _sha(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def find_latest_completed_session(als_path: Path, reports_dir: Path) -> tuple[Path, Path, dict]:
    candidates = sorted(reports_dir.glob("live_measurement_*/live_measurement_manifest.json"), reverse=True)
    for manifest_path in candidates:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if Path(manifest.get("als_path", "")).resolve() != als_path.resolve():
            continue
        if manifest.get("als_sha256") != _sha(als_path):
            continue
        log_path = Path(manifest.get("log_path", ""))
        if not log_path.is_file():
            continue
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        own = [event for event in events if event.get("session_id") == manifest.get("session_id")]
        if any(event.get("event") == "session_aborted" for event in own):
            continue
        if any(event.get("event") == "session_completed" for event in own):
            return manifest_path, log_path, manifest
    raise ValueError("no completed session found")


def _select_reference(events: list[dict], manifest: dict) -> tuple[int, str]:
    samples = [event for event in events if event.get("event") == "measurement_sample"]
    names = {}
    for event in samples:
        names.setdefault(event.get("track_index"), event.get("track_name") or "")
    preferred = [index for index, name in names.items() if "calibrationref" in "".join(ch.lower() for ch in name if ch.isalnum())]
    if preferred:
        index = preferred[0]
        return index, names[index]
    by_index = {}
    for event in samples:
        raw = max(float(event.get("meter_left", 0)), float(event.get("meter_right", 0)))
        if raw > RAW_SILENCE_THRESHOLD:
            by_index.setdefault(event["track_index"], []).append(raw)
    if not by_index:
        raise ValueError("calibration reference track unresolved")
    index = max(by_index, key=lambda key: len(by_index[key]))
    return index, names.get(index, "")


def _fit_affine(points: list[dict]) -> tuple[float, float]:
    x = [math.log10(point["raw_p95_stereo"]) for point in points]
    y = [point["known_dbfs"] for point in points]
    x_mean, y_mean = mean(x), mean(y)
    denominator = sum((value - x_mean) ** 2 for value in x)
    if denominator == 0:
        raise ValueError("insufficient samples")
    a = sum((a0 - x_mean) * (b0 - y_mean) for a0, b0 in zip(x, y)) / denominator
    b = y_mean - a * x_mean
    return a, b


def calibrate(events: list[dict], manifest: dict) -> dict:
    reference_index, reference_name = _select_reference(events, manifest)
    samples = [event for event in events if event.get("event") == "measurement_sample" and event.get("track_index") == reference_index]
    if not samples:
        raise ValueError("calibration reference track unresolved")
    first = min(float(event["monotonic_timestamp"]) for event in samples)
    points, offset = [], 0.0
    for silence, tone_duration, known in SEGMENTS:
        start = offset + silence + 0.25
        end = offset + silence + tone_duration - 0.25
        selected = [event for event in samples if start <= float(event["monotonic_timestamp"]) - first <= end]
        raw_l = [float(event["meter_left"]) for event in selected]
        raw_r = [float(event["meter_right"]) for event in selected]
        stereo = [max(left, right) for left, right in zip(raw_l, raw_r)]
        point = {"known_dbfs": known, "sample_count": len(stereo), "raw_max_left": max(raw_l, default=None), "raw_max_right": max(raw_r, default=None), "raw_max_stereo": max(stereo, default=None), "raw_p95_stereo": sorted(stereo)[int(.95 * (len(stereo) - 1))] if stereo else None, "raw_mean_stereo": mean(stereo) if stereo else None, "warnings": []}
        if len(stereo) < 10:
            point["warnings"].append("insufficient samples")
        if stereo and sum(value <= RAW_SILENCE_THRESHOLD for value in stereo) / len(stereo) > .1:
            point["warnings"].append("silence contamination warning")
        points.append(point)
        offset += silence + tone_duration
    if any(point["sample_count"] < 10 or not point["raw_p95_stereo"] for point in points):
        return _failed(manifest, reference_name, points, "insufficient samples")
    raw = [point["raw_p95_stereo"] for point in points]
    if any(right <= left for left, right in zip(raw, raw[1:])):
        return _failed(manifest, reference_name, points, "non-monotonic calibration")
    def direct(value): return 20 * math.log10(value)
    for point in points:
        point["predicted_dbfs"] = round(direct(point["raw_p95_stereo"]), 6)
        point["error_db"] = round(point["predicted_dbfs"] - point["known_dbfs"], 6)
    direct_errors = [point["error_db"] for point in points]
    model_type, params = "direct_log20", {"a": 20.0, "b": 0.0}
    if max(abs(error) for error in direct_errors) > .5:
        a, b = _fit_affine(points)
        model_type, params = "affine_log10", {"a": round(a, 9), "b": round(b, 9)}
        for point in points:
            point["predicted_dbfs"] = round(a * math.log10(point["raw_p95_stereo"]) + b, 6)
            point["error_db"] = round(point["predicted_dbfs"] - point["known_dbfs"], 6)
    errors = [point["error_db"] for point in points]
    max_error = max(abs(error) for error in errors)
    status = "success" if max_error <= .5 else "failed"
    return {"schema_version": "1.0", "calibration_status": status, "source_session_id": manifest["session_id"], "source_als_path": manifest["als_path"], "source_als_sha256": manifest["als_sha256"], "reference_track_name": reference_name, "meter_unit_before": "unknown_raw_live_meter", "calibrated_unit": "dbfs_peak_estimate" if status == "success" else None, "meter_tap_point_status": "unresolved", "model_type": model_type, "model_parameters": params, "calibration_points": points, "max_absolute_error_db": round(max_error, 6), "rms_error_db": round(math.sqrt(mean(error * error for error in errors)), 6), "warnings": [] if status == "success" else ["calibration error above tolerance"]}


def _failed(manifest, name, points, warning):
    return {"schema_version": "1.0", "calibration_status": "failed", "source_session_id": manifest["session_id"], "source_als_path": manifest["als_path"], "source_als_sha256": manifest["als_sha256"], "reference_track_name": name, "meter_unit_before": "unknown_raw_live_meter", "calibrated_unit": None, "meter_tap_point_status": "unresolved", "model_type": None, "model_parameters": None, "calibration_points": points, "max_absolute_error_db": None, "rms_error_db": None, "warnings": [warning]}


def calibration_markdown(profile: dict) -> str:
    lines = ["# Live Meter Calibration", "", f"Status: `{profile['calibration_status']}`", f"Model: `{profile['model_type']}`", "", "| Known dBFS | Raw P95 | Predicted | Error |", "|---:|---:|---:|---:|"]
    for point in profile["calibration_points"]:
        lines.append(f"| {point['known_dbfs']} | {point['raw_p95_stereo']} | {point.get('predicted_dbfs')} | {point.get('error_db')} |")
    return "\n".join(lines) + "\n"
