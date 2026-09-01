"""Provisional relative-level transfer for the documented Live raw meter.

It intentionally never produces dBFS.  The result is only a relative change
within the same observed meter chain and is bounded by measured controls.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


MODEL_STATUS = "provisional_relative_only"
CLAMP_WARNING = "raw meter is at or above 1.0; clamp/saturation suspected"


def _completed_result(investigation: dict[str, Any], test: str) -> dict[str, Any] | None:
    for item in investigation.get("tests", []):
        if item.get("test") == test and item.get("status") == "completed":
            return item.get("result")
    return None


def build_transfer(investigation: dict[str, Any]) -> dict[str, Any]:
    baseline = _completed_result(investigation, "BASELINE")
    if not baseline or not isinstance(baseline.get("raw_max"), (int, float)):
        raise ValueError("completed BASELINE raw meter control is required")
    used, excluded = [{"test": "BASELINE", "raw": baseline["raw_max"], "relative_db": 0.0}], []
    for test in ("FADER_MINUS12", "UTILITY_MINUS12", "EQ_OUTPUT_MINUS12"):
        result = _completed_result(investigation, test)
        if result and isinstance(result.get("raw_max"), (int, float)) and 0 < result["raw_max"] < 1.0:
            used.append({"test": test, "raw": result["raw_max"], "relative_db": -12.0})
        else:
            excluded.append({"test": test, "reason": "missing, non-positive, or clamped raw control"})
    plus = _completed_result(investigation, "UTILITY_PLUS12")
    if plus:
        excluded.append({"test": "UTILITY_PLUS12", "raw": plus.get("raw_max"), "reason": "excluded from fit: raw 1.0 may be clamped/saturated"})
    for test in ("MASTER_MINUS6", "PAN_LEFT", "PAN_RIGHT", "SEND_RETURN_ON"):
        excluded.append({"test": test, "reason": "not a level-transfer control"})
    if len(used) < 2:
        raise ValueError("at least baseline and one unclamped minus12 control are required")
    low = mean(point["raw"] for point in used if point["relative_db"] == -12.0)
    high = baseline["raw_max"]
    if not 0 < low < high < 1.0:
        raise ValueError("control raw points are not usable for a bounded monotonic model")
    slope = 12.0 / (high - low)
    affine_raw = {"type": "affine_raw", "equation": f"relative_db = {slope:.12g} * raw + {-slope * high:.12g}", "status": "evaluated", "monotonic": slope > 0}
    log_slope = 12.0 / (math.log10(high) - math.log10(low))
    affine_log = {"type": "affine_log10_raw", "equation": f"relative_db = {log_slope:.12g} * log10(raw) + {-log_slope * math.log10(high):.12g}", "status": "evaluated", "monotonic": log_slope > 0}
    models = [affine_raw, affine_log, {"type": "quadratic_raw", "status": "rejected_insufficient_unique_level_points", "monotonic": None, "reason": "only two unique trusted raw levels exist"}, {"type": "monotonic_interpolation", "status": "selected", "monotonic": True, "table": [{"raw": low, "relative_db": -12.0}, {"raw": high, "relative_db": 0.0}]}]
    return {"schema_version": "1.0", "calibration_status": MODEL_STATUS, "meter_unit": "unknown_raw_live_meter", "absolute_unit": None, "source_als_path": investigation.get("als_path"), "source_als_sha256": investigation.get("als_sha256"), "used_control_points": used, "excluded_control_points": excluded, "selected_model": "monotonic_interpolation", "models_evaluated": models, "model_domain": {"raw_min_inclusive": low, "raw_max_inclusive": high}, "clamp_behavior": {"threshold": 1.0, "warning": CLAMP_WARNING, "estimate": None}, "uncertainty": "Only two trusted relative levels (0 and -12 dB) exist. No extrapolation, absolute dBFS, peak, RMS, LUFS, or gain recommendation is valid.", "scope": "estimated_relative_db is valid only for comparisons through the same Live meter chain and control configuration.", "warnings": ["UTILITY_PLUS12 was excluded because raw=1.0 can be clamped.", "Production gain recommendation remains disabled pending absolute audio measurement."]}


def estimate_relative_db(raw: Any, transfer: dict[str, Any]) -> tuple[float | None, str, str | None]:
    if not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw <= 0:
        return None, "unresolved", "raw meter must be finite and greater than zero"
    if raw >= 1.0:
        return None, "clamped_or_saturated", CLAMP_WARNING
    domain = transfer["model_domain"]
    low, high = domain["raw_min_inclusive"], domain["raw_max_inclusive"]
    if raw < low or raw > high:
        return None, "outside_provisional_domain", "raw meter is outside the two-point trusted domain; no extrapolation"
    relative = -12.0 + (raw - low) * 12.0 / (high - low)
    return round(relative, 6), MODEL_STATUS, None


def apply_transfer_to_report(report: dict[str, Any], transfer: dict[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(report))
    for track in output.get("tracks", []):
        value, status, warning = estimate_relative_db(track.get("raw_max_stereo"), transfer)
        track["estimated_relative_db"] = value
        track["relative_model_status"] = status
        track["relative_model_warning"] = warning
        track["proposed_adjustment_db"] = None
        track["policy_status"] = "blocked_pending_absolute_audio_measurement"
    output["relative_transfer"] = {"calibration_status": transfer["calibration_status"], "selected_model": transfer["selected_model"], "scope": transfer["scope"]}
    return output


def transfer_markdown(data: dict[str, Any]) -> str:
    lines = ["# Live Meter Relative Transfer", "", f"Status: `{data['calibration_status']}`", f"Selected model: `{data['selected_model']}`", "", "## Used controls", "", "| Test | Raw | Relative dB |", "|---|---:|---:|"]
    for point in data["used_control_points"]:
        lines.append(f"| {point['test']} | {point['raw']} | {point['relative_db']} |")
    lines += ["", "## Excluded controls", ""]
    for point in data["excluded_control_points"]:
        lines.append(f"- `{point['test']}`: {point['reason']}")
    selected = next(model for model in data["models_evaluated"] if model["type"] == data["selected_model"])
    lines += ["", "## Selected interpolation table", "", "| Raw | Estimated relative dB |", "|---:|---:|"]
    for point in selected["table"]:
        lines.append(f"| {point['raw']} | {point['relative_db']} |")
    lines += ["", "## Model domain and clamp behavior", "", f"Raw `{data['model_domain']['raw_min_inclusive']}` to `{data['model_domain']['raw_max_inclusive']}` inclusive; no extrapolation.", f"Raw `>= {data['clamp_behavior']['threshold']}`: `{data['clamp_behavior']['warning']}`; no estimate.", "", "## Uncertainty and scope", "", data["uncertainty"], "", data["scope"], ""]
    return "\n".join(lines)


def transfer_svg(data: dict[str, Any]) -> str:
    table = next(model["table"] for model in data["models_evaluated"] if model["type"] == "monotonic_interpolation")
    a, b = table
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="280" viewBox="0 0 640 280"><rect width="100%" height="100%" fill="white"/><text x="40" y="25" font-size="15">Provisional raw meter → relative dB</text><line x1="70" y1="230" x2="590" y2="230" stroke="#333"/><line x1="70" y1="50" x2="70" y2="230" stroke="#333"/><line x1="130" y1="200" x2="510" y2="80" stroke="#2878c7" stroke-width="2"/><circle cx="130" cy="200" r="5" fill="#2878c7"/><circle cx="510" cy="80" r="5" fill="#2878c7"/><text x="92" y="220" font-size="12">raw {a['raw']:.6f}, -12</text><text x="420" y="70" font-size="12">raw {b['raw']:.6f}, 0</text><text x="70" y="255" font-size="12">bounded interpolation only; raw ≥ 1.0 is clamp-unknown</text></svg>\n'''
