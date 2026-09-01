"""Read-only analysis for MixConsoleLive2 append-only live meter sessions."""

from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import quantiles
from typing import Any


METER_UNIT = "unknown_raw_live_meter"
REQUESTED_SAMPLE_RATE_HZ = 10.0
RAW_SILENCE_THRESHOLD = 0.000001


def analyze_live_meter_log(log_path: Path, manifest: dict[str, Any], calibration: dict[str, Any] | None = None, relative_transfer: dict[str, Any] | None = None) -> dict[str, Any]:
    session_id = manifest["session_id"]
    events: list[dict[str, Any]] = []
    parse_warnings: list[str] = []
    try:
        for line_number, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                parse_warnings.append(f"Invalid JSONL line: {line_number}")
                continue
            events.append(event)
    except OSError as exc:
        return {"schema_version": "1.0", "session_id": session_id, "measurement_status": "unavailable", "warnings": [f"Cannot read meter log: {exc}"], "tracks": []}

    expected = {item["track_index"]: item for item in manifest["included_tracks"]}
    samples: dict[int, list[dict[str, Any]]] = defaultdict(list)
    session_events = [event for event in events if event.get("session_id") == session_id]
    wrong_session = bool(events) and len(session_events) != len(events)
    started = [event for event in session_events if event.get("event") == "session_started"]
    completed = [event for event in session_events if event.get("event") == "session_completed"]
    aborted = [event for event in session_events if event.get("event") == "session_aborted"]
    for event in session_events:
        if event.get("event") == "measurement_sample":
            samples[event.get("track_index")].append(event)

    tracks = []
    identity_mapping = []
    for index, item in expected.items():
        values = samples.get(index, [])
        warnings: list[str] = []
        unsupported = item.get("track_type") == "MidiTrack"
        if unsupported:
            warnings.append("Unresolved: Live meter access is not available for this MIDI-output track")
        if wrong_session:
            warnings.append("Session ID mismatch events are present")
        if not started:
            warnings.append("session_started event is missing")
        if not completed:
            warnings.append("session_completed event is missing; log may be incomplete")
        if aborted:
            warnings.append("Session was aborted")
        if any(event.get("track_name") != item.get("live_track_name", item["track_name"]) for event in values):
            warnings.append("Track index/name mapping mismatch")
        if any(not isinstance(event.get("meter_left"), (int, float)) or not isinstance(event.get("meter_right"), (int, float)) or not math.isfinite(event["meter_left"]) or not math.isfinite(event["meter_right"]) for event in values):
            warnings.append("Invalid raw meter value")
        states = {(event.get("track_activator"), event.get("solo"), event.get("mute"), event.get("track_volume")) for event in values}
        if len({(a, b, c) for a, b, c, _ in states}) > 1:
            warnings.append("Track activator, solo, or mute state changed during session")
        if len({volume for _, _, _, volume in states}) > 1:
            warnings.append("Track volume changed during session")
        valid = [event for event in values if isinstance(event.get("meter_left"), (int, float)) and isinstance(event.get("meter_right"), (int, float)) and math.isfinite(event["meter_left"]) and math.isfinite(event["meter_right"])]
        stereo = [max(float(event["meter_left"]), float(event["meter_right"])) for event in valid]
        times = [float(event["monotonic_timestamp"]) for event in valid if isinstance(event.get("monotonic_timestamp"), (int, float))]
        duration = round(max(times) - min(times), 6) if len(times) >= 2 else 0.0
        observed_hz = round((len(times) - 1) / duration, 6) if duration > 0 and len(times) >= 2 else None
        if len(valid) < 10 and not unsupported:
            warnings.append("Insufficient valid samples (minimum 10)")
        status = "unresolved_no_meter" if unsupported else "valid_raw_meter" if not warnings else "invalid_or_incomplete"
        raw_max = max(stereo, default=None)
        als_name = item.get("track_name") or ""
        display_name = als_name or f"unresolved_track_{item.get('track_id', 'unknown')}"
        observed_names = sorted({str(event.get("track_name")) for event in values if event.get("track_name") is not None})
        mapping_status = "resolved_by_als_name_and_index" if als_name else "unresolved_als_track_name"
        if not als_name:
            warnings.append("ALS track name is blank; fallback unresolved_track_<track_id> is used")
        peak_dbfs = None
        if calibration and calibration.get("calibration_status") == "success" and raw_max and raw_max > 0:
            params = calibration["model_parameters"]
            peak_dbfs = round(params["a"] * math.log10(raw_max) + params["b"], 6)
        if relative_transfer:
            from .live_meter_transfer import estimate_relative_db
            estimated_relative_db, relative_status, relative_warning = estimate_relative_db(raw_max, relative_transfer)
        else:
            estimated_relative_db, relative_status, relative_warning = None, "not_available", "No relative transfer model was supplied"
        tracks.append({
            "track_index": index,
            "track_id": item["track_id"],
            "track_name": display_name,
            "sample_count": len(valid),
            "measurement_duration_seconds": duration,
            "requested_sample_rate_hz": REQUESTED_SAMPLE_RATE_HZ,
            "observed_sample_rate_hz": observed_hz,
            "meter_unit": METER_UNIT,
            "raw_max_left": max((float(event["meter_left"]) for event in valid), default=None),
            "raw_max_right": max((float(event["meter_right"]) for event in valid), default=None),
            "raw_max_stereo": raw_max,
            "raw_p95_stereo": (quantiles(stereo, n=100, method="inclusive")[94] if len(stereo) >= 2 else (stereo[0] if stereo else None)),
            "silence_ratio": (round(sum(value <= RAW_SILENCE_THRESHOLD for value in stereo) / len(stereo), 6) if stereo else None),
            "raw_silence_threshold": RAW_SILENCE_THRESHOLD,
            "clipping_sample_count": None,
            "state_change_warnings": [warning for warning in warnings if "state changed" in warning or "volume changed" in warning],
            "measurement_status": status,
            "max_peak_dbfs": peak_dbfs,
            "estimated_relative_db": estimated_relative_db,
            "relative_model_status": relative_status,
            "relative_model_warning": relative_warning,
            "proposed_adjustment_db": None,
            "policy_status": "blocked_pending_absolute_audio_measurement" if relative_transfer else "blocked_pending_meter_calibration",
            "warnings": warnings,
        })
        identity_mapping.append({"als_track_name": als_name or None, "live_track_index": index, "remote_script_observed_track_names": observed_names, "remote_script_track_object": "unavailable_in_jsonl", "display_track_name": display_name, "mapping_status": mapping_status})
    return {"schema_version": "1.0", "session_id": session_id, "meter_unit": METER_UNIT, "measurement_status": "completed" if completed and not aborted and not wrong_session and not parse_warnings else "invalid_or_incomplete", "warnings": parse_warnings, "track_identity_mapping": identity_mapping, "tracks": tracks}


def live_meter_markdown(report: dict[str, Any]) -> str:
    lines = ["# Live Meter Evidence Report", "", f"Session: `{report['session_id']}`", f"Meter unit: `{report['meter_unit']}`", "", "| Track | Samples | Raw max stereo | Relative dB | Relative status | Status |", "|---|---:|---:|---:|---|---|"]
    for row in report["tracks"]:
        lines.append(f"| {row['track_name']} | {row['sample_count']} | {row['raw_max_stereo']} | {row.get('estimated_relative_db')} | {row.get('relative_model_status')} | {row['measurement_status']} |")
    lines.extend(["", "No absolute dBFS conversion or gain recommendation is produced. Relative estimates, when present, are bounded provisional comparisons only.", ""])
    return "\n".join(lines)
