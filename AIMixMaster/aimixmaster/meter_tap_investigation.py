"""Read-only, controlled experiments for locating the Live meter tap point.

This module deliberately does not calibrate or transform the raw meter.  The
only dB-like quantity is a clearly-labelled 20*log10 raw ratio, used solely to
describe the size of a control change relative to the baseline.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import mean, quantiles
from typing import Any


TESTS = (
    "BASELINE", "FADER_MINUS12", "UTILITY_MINUS12", "UTILITY_PLUS12",
    "EQ_OUTPUT_MINUS12", "MASTER_MINUS6", "SEND_RETURN_ON", "PAN_LEFT", "PAN_RIGHT",
)
LEGACY_EQ_TEST = "EQ_OUTPUT_PLUS12"
EQ_MIGRATION_WARNING = "Legacy session directory name indicated PLUS12, but the performed control was MINUS12."

INSTRUCTIONS = {
    "FADER_MINUS12": "Calibration reference track fader: 0 dB → -12 dB. Do not change anything else.",
    "UTILITY_MINUS12": "Calibration reference track Utility gain: 0 dB → -12 dB. Do not change anything else.",
    "UTILITY_PLUS12": "Calibration reference track Utility gain: 0 dB → +12 dB. Do not change anything else.",
    "EQ_OUTPUT_MINUS12": "Calibration reference track EQ Eight output gain: 0 dB → -12 dB. Do not change anything else.",
    "MASTER_MINUS6": "Master fader: 0 dB → -6 dB. Do not change anything else.",
    "SEND_RETURN_ON": "Enable the documented calibration-reference return send only. Keep its amount at the project’s documented test value; do not change anything else.",
    "PAN_LEFT": "Calibration reference track pan: center → hard left. Do not change anything else.",
    "PAN_RIGHT": "Calibration reference track pan: center → hard right. Do not change anything else.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate_legacy_eq_test(study: dict[str, Any]) -> bool:
    """Canonically rename the test key while preserving its legacy directory/log."""
    manifests = study.setdefault("test_manifests", {})
    if LEGACY_EQ_TEST not in manifests or "EQ_OUTPUT_MINUS12" in manifests:
        return False
    legacy = manifests.pop(LEGACY_EQ_TEST)
    manifests["EQ_OUTPUT_MINUS12"] = legacy | {
        "legacy_test_key": LEGACY_EQ_TEST,
        "migration_warning": EQ_MIGRATION_WARNING,
    }
    return True


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _completed(manifest: dict[str, Any]) -> bool:
    path = Path(manifest["log_path"])
    if not path.is_file():
        return False
    own = [event for event in _events(path) if event.get("session_id") == manifest.get("session_id")]
    return any(e.get("event") == "session_completed" for e in own) and not any(e.get("event") == "session_aborted" for e in own)


def _reference(manifest: dict[str, Any], events: list[dict[str, Any]]) -> tuple[int, str]:
    candidates = manifest.get("included_tracks", [])
    for row in candidates:
        name = str(row.get("live_track_name") or row.get("track_name") or "")
        if "calibrationreference" in "".join(char.lower() for char in name if char.isalnum()):
            return int(row["track_index"]), name
    samples = [e for e in events if e.get("event") == "measurement_sample"]
    names: dict[int, str] = {}
    for event in samples:
        names.setdefault(int(event.get("track_index", -1)), str(event.get("track_name") or ""))
    matching = [(index, name) for index, name in names.items() if "calibrationreference" in "".join(char.lower() for char in name if char.isalnum())]
    if matching:
        return matching[0]
    raise ValueError("calibration reference track is not identifiable")


def summarize_session(manifest: dict[str, Any]) -> dict[str, Any]:
    events = _events(Path(manifest["log_path"]))
    index, name = _reference(manifest, events)
    samples = [e for e in events if e.get("event") == "measurement_sample" and e.get("track_index") == index]
    left = [float(e["meter_left"]) for e in samples if isinstance(e.get("meter_left"), (int, float)) and math.isfinite(e["meter_left"])]
    right = [float(e["meter_right"]) for e in samples if isinstance(e.get("meter_right"), (int, float)) and math.isfinite(e["meter_right"])]
    stereo = [max(a, b) for a, b in zip(left, right)]
    return {"session_id": manifest["session_id"], "reference_track_index": index, "reference_track_name": name,
            "raw_max": max(stereo, default=None), "raw_p95": quantiles(stereo, n=100, method="inclusive")[94] if len(stereo) >= 2 else (stereo[0] if stereo else None),
            "raw_mean": mean(stereo) if stereo else None, "raw_max_left": max(left, default=None), "raw_max_right": max(right, default=None),
            "sample_count": len(stereo), "samples": [{"time": e.get("monotonic_timestamp"), "left": a, "right": b} for e, a, b in zip(samples, left, right)]}


def comparison(baseline: dict[str, Any], test: dict[str, Any]) -> dict[str, Any]:
    value = test.get("raw_max")
    base = baseline.get("raw_max")
    ratio = value / base if value is not None and base not in (None, 0) else None
    return {key: test.get(key) for key in ("session_id", "reference_track_index", "reference_track_name", "raw_max", "raw_p95", "raw_mean", "raw_max_left", "raw_max_right", "sample_count")} | {
        "ratio_to_baseline": ratio,
        "delta_db_equivalent": 20 * math.log10(ratio) if ratio and ratio > 0 else None,
        "meter_unit": "unknown_raw_live_meter",
        "delta_db_equivalent_note": "20*log10(raw_max ratio); descriptive only, not a calibration or transfer model",
    }


def _direction(item: dict[str, Any] | None, expected: str) -> str:
    if not item or item.get("ratio_to_baseline") is None:
        return "not measured"
    ratio = item["ratio_to_baseline"]
    if expected == "down": return "supports" if ratio < .95 else "does not support"
    if expected == "up": return "supports" if ratio > 1.05 else "does not support"
    return "supports" if abs(ratio - 1.0) <= .05 else "does not support"


def hypotheses(results: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    rules = [
        ("post-fader", "FADER_MINUS12", "down"), ("pre-fader", "FADER_MINUS12", "same"),
        ("post-device", "EQ_OUTPUT_MINUS12", "down"), ("pre-device", "EQ_OUTPUT_MINUS12", "same"),
        ("post-master", "MASTER_MINUS6", "down"), ("pre-master", "MASTER_MINUS6", "same"),
        ("send-inclusive", "SEND_RETURN_ON", "up"), ("send-exclusive", "SEND_RETURN_ON", "same"),
    ]
    rows = []
    for name, test, expected in rules:
        verdict = _direction(results.get(test), expected)
        rows.append({"hypothesis": name, "control": test, "expected_raw_change": expected,
                     "evidence_for": f"{test}: {verdict}", "evidence_against": "No contradictory completed control" if verdict == "supports" else ("Control unavailable" if verdict == "not measured" else f"{test} result conflicts"),
                     "confidence": "not assessed" if verdict == "not measured" else ("provisional" if verdict == "supports" else "low")})
    for name, test in (("stereo post-pan", "PAN_LEFT"), ("stereo pre-pan", "PAN_LEFT")):
        row = results.get(test)
        if not row or row.get("raw_max_left") is None or row.get("raw_max_right") is None:
            verdict, confidence = "not measured", "not assessed"
        else:
            separated = abs(row["raw_max_left"] - row["raw_max_right"]) > .05
            supports = separated if name == "stereo post-pan" else not separated
            verdict, confidence = ("supports", "provisional") if supports else ("does not support", "low")
        rows.append({"hypothesis": name, "control": test, "expected_raw_change": "channel separation" if name == "stereo post-pan" else "no channel separation", "evidence_for": f"{test}: {verdict}", "evidence_against": "See raw left/right values", "confidence": confidence})
    return rows


def overlay_svg(baseline: dict[str, Any], test: dict[str, Any], title: str) -> str:
    width, height, pad = 760, 240, 30
    all_values = [max(x["left"], x["right"]) for x in baseline["samples"] + test["samples"]] or [1]
    limit = max(all_values) or 1
    def points(rows):
        if not rows: return ""
        start, end = rows[0]["time"], rows[-1]["time"]
        span = (end - start) or 1
        return " ".join(f"{pad + (width-2*pad)*(r['time']-start)/span:.2f},{height-pad-(height-2*pad)*max(r['left'],r['right'])/limit:.2f}" for r in rows)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><text x="30" y="20" font-size="14">{title} raw meter overlay</text><line x1="30" y1="210" x2="730" y2="210" stroke="#555"/><polyline points="{points(baseline["samples"])}" fill="none" stroke="#2674c9"/><polyline points="{points(test["samples"])}" fill="none" stroke="#d35f2d"/><text x="35" y="232" font-size="11" fill="#2674c9">baseline</text><text x="110" y="232" font-size="11" fill="#d35f2d">test</text></svg>\n'


def report(study: dict[str, Any], destination: Path) -> dict[str, Any]:
    baseline = summarize_session(study["baseline_manifest"])
    results: dict[str, dict[str, Any]] = {"BASELINE": comparison(baseline, baseline)}
    for test, manifest in study.get("test_manifests", {}).items():
        if manifest.get("status") == "skipped_not_configured":
            continue
        if _completed(manifest):
            results[test] = comparison(baseline, summarize_session(manifest))
            (destination / f"overlay_{test.lower()}.svg").write_text(overlay_svg(baseline, summarize_session(manifest), test), encoding="utf-8")
    tests = []
    for test in TESTS:
        session = study.get("test_manifests", {}).get(test, {})
        skipped = session.get("status") == "skipped_not_configured"
        tests.append({"test": test, "status": "skipped_not_configured" if skipped else ("completed" if test in results else "pending"), "instruction": "Skipped: no documented return/send test configuration" if skipped else INSTRUCTIONS.get(test, "Baseline source session"), "result": results.get(test), "warning": session.get("warning") if skipped else None, "migration_warning": session.get("migration_warning")})
    expected = len(TESTS) - sum(1 for item in tests if item["status"] == "skipped_not_configured")
    migrations = [item["migration_warning"] for item in tests if item.get("migration_warning")]
    return {"schema_version": "1.0", "investigation_status": "completed" if len(results) == expected else "in_progress", "als_path": study["als_path"], "als_sha256": study["als_sha256"], "baseline": results["BASELINE"], "tests": tests, "hypotheses": hypotheses(results), "warnings": ["Raw meter comparisons only; no meter-transfer fit, calibration, tolerance change, dBFS conversion, or gain recommendation was used."] + migrations}


def markdown(data: dict[str, Any]) -> str:
    lines = ["# Meter Tap Investigation", "", f"Status: `{data['investigation_status']}`", "", "| Test | Status | Raw max | Raw P95 | Ratio | Δ raw-ratio dB | Samples |", "|---|---|---:|---:|---:|---:|---:|"]
    for item in data["tests"]:
        r = item["result"] or {}
        lines.append(f"| {item['test']} | {item['status']} | {r.get('raw_max')} | {r.get('raw_p95')} | {r.get('ratio_to_baseline')} | {r.get('delta_db_equivalent')} | {r.get('sample_count')} |")
        if item.get("warning"):
            lines.append(f"\n{item['test']}: {item['warning']}\n")
        if item.get("migration_warning"):
            lines.append(f"\n{item['test']}: {item['migration_warning']}\n")
    lines += ["", "## Hypotheses", "", "| Hypothesis | Control | Evidence for | Evidence against | Confidence |", "|---|---|---|---|---|"]
    for row in data["hypotheses"]:
        lines.append(f"| {row['hypothesis']} | {row['control']} | {row['evidence_for']} | {row['evidence_against']} | {row['confidence']} |")
    lines += ["", *data["warnings"], ""]
    return "\n".join(lines)
