"""Deterministic Ableton render manifest, matching, and validation helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

# soundfile is needed only by validate_renders; see gain_staging.py.

from .gain_staging import _normalized_name, measure_audio_file
from .project_analyzer import analyze_tracks


RENDER_SCHEMA_VERSION = "1.0"
FORMATS = {".wav", ".aif", ".aiff"}


@dataclass(frozen=True)
class RenderManifestEntry:
    track_id: int
    original_track_name: str
    normalized_track_name: str
    export_filename: str
    track_type: str
    routing_type: str
    parent_bus: str
    should_render: bool
    exclusion_reason: str | None
    expected_channels: str
    warnings: list[str]


def _safe_stem(name: str) -> str:
    stem = _normalized_name(name)
    return stem or "track"


def build_render_manifest(root, project_name: str) -> dict[str, Any]:
    infos = analyze_tracks(root)
    groups = {item.track_id: item.name or f"group_{item.track_id}" for item in infos if item.track_type == "GroupTrack"}
    entries = []
    seen: dict[str, int] = {}
    for index, item in enumerate(infos, 1):
        safe = _safe_stem(item.name)
        seen[safe] = seen.get(safe, 0) + 1
        suffix = "" if seen[safe] == 1 else f"__{seen[safe]}"
        if item.track_type == "AudioTrack":
            should, reason = True, None
        elif item.track_type == "MidiTrack":
            should, reason = False, "midi_track_requires_freeze_or_audio_render"
        elif item.track_type == "GroupTrack":
            should, reason = False, "group_track_excluded_from_individual_track_manifest"
        elif item.track_type == "ReturnTrack":
            should, reason = False, "return_track_excluded_from_individual_track_manifest"
        else:
            should, reason = False, "master_track_reference_only"
        parent = groups.get(item.group_id, "master" if item.group_id in {None, -1} else f"unresolved_group_id:{item.group_id}")
        entries.append(RenderManifestEntry(
            track_id=item.track_id,
            original_track_name=item.name or "(unnamed)",
            normalized_track_name=_normalized_name(item.name),
            export_filename=f"{index:02d}_{safe}{suffix}.wav",
            track_type=item.track_type,
            routing_type="direct_group" if item.group_id in groups else "master" if item.group_id in {None, -1} else "unresolved",
            parent_bus=parent,
            should_render=should,
            exclusion_reason=reason,
            expected_channels="unknown",
            warnings=[] if should else [reason],
        ))
    return {"schema_version": RENDER_SCHEMA_VERSION, "project_name": project_name, "tracks": [asdict(item) for item in entries]}


def manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = ["# Ableton Individual Render Manifest", "", f"Project: `{manifest['project_name']}`", "", "| Export | Track | Type | Render? | Reason |", "|---|---|---|---|---|"]
    for item in manifest["tracks"]:
        lines.append(f"| {item['export_filename']} | {item['original_track_name']} | {item['track_type']} | {item['should_render']} | {item['exclusion_reason'] or '-'} |")
    return "\n".join(lines) + "\n"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_for(entry: dict[str, Any], files: list[Path]) -> tuple[Path | None, str, list[str]]:
    exact = [path for path in files if path.name == entry["export_filename"]]
    if len(exact) == 1:
        return exact[0], "manifest_export_filename", []
    if len(exact) > 1:
        return None, "unmatched", ["Multiple exact manifest filename candidates"]
    normalized = entry["normalized_track_name"]
    by_name = [path for path in files if _normalized_name(path.stem) == normalized]
    if len(by_name) == 1:
        return by_name[0], "normalized_track_name", []
    if len(by_name) > 1:
        return None, "unmatched", ["Ambiguous exact normalized track-name candidates"]
    indexed = [path for path in files if re.match(rf"^{entry['export_filename'][:2]}_", path.name) and _normalized_name(re.sub(r"^\d+_", "", path.stem)) == normalized]
    if len(indexed) == 1:
        return indexed[0], "index_and_normalized_track_name", []
    if len(indexed) > 1:
        return None, "unmatched", ["Ambiguous index and normalized-name candidates"]
    return None, "unmatched", ["Expected render file is missing"]


def validate_renders(manifest: dict[str, Any], renders_dir: Path) -> dict[str, Any]:
    import soundfile as sf

    files = sorted(path for path in renders_dir.iterdir() if path.is_file()) if renders_dir.is_dir() else []
    usable = [path for path in files if path.suffix.casefold() in FORMATS]
    used: set[Path] = set()
    hash_to_paths: dict[str, list[Path]] = {}
    for path in usable:
        try:
            hash_to_paths.setdefault(_hash(path), []).append(path)
        except OSError:
            pass
    results = []
    for entry in manifest["tracks"]:
        if not entry["should_render"]:
            continue
        path, method, warnings = _candidate_for(entry, usable)
        item: dict[str, Any] = {"track_id": entry["track_id"], "export_filename": entry["export_filename"], "matched_path": str(path) if path else None, "match_method": method, "confidence": "high" if path and method == "manifest_export_filename" else "medium" if path else "none", "warnings": warnings}
        if path is not None:
            used.add(path)
            try:
                with sf.SoundFile(path) as audio:
                    item.update({"format": audio.format, "channels": audio.channels, "sample_rate": audio.samplerate, "frames": len(audio), "duration_seconds": round(len(audio) / audio.samplerate, 6) if audio.samplerate else None, "bit_depth": audio.subtype})
                    if len(audio) == 0:
                        item["warnings"].append("Render duration is zero")
            except (OSError, RuntimeError) as exc:
                item["warnings"].append(f"Render cannot be opened: {exc}")
            measured = measure_audio_file(path)
            item.update({"sample_peak_dbfs": measured["peak"], "sample_rms_dbfs": measured["rms"], "lufs_integrated": measured["lufs"]})
            item["warnings"].extend(measured["warnings"])
            duplicates = hash_to_paths.get(_hash(path), [])
            if len(duplicates) > 1:
                item["warnings"].append("Physical duplicate render: " + ", ".join(str(x) for x in duplicates if x != path))
        results.append(item)
    extras = [str(path) for path in usable if path not in used]
    return {"schema_version": RENDER_SCHEMA_VERSION, "renders_dir": str(renders_dir), "tracks": results, "extra_files": extras, "warnings": ([] if renders_dir.is_dir() else [f"Renders directory is unavailable: {renders_dir}"])}


def validation_markdown(validation: dict[str, Any]) -> str:
    lines = ["# Render Validation", "", "| Export | Match | Confidence | Peak | Warnings |", "|---|---|---|---|---|"]
    for item in validation["tracks"]:
        lines.append(f"| {item['export_filename']} | {item['match_method']} | {item['confidence']} | {item.get('sample_peak_dbfs', 'unknown')} | {'; '.join(item['warnings']) or '-'} |")
    lines.extend(["", "## Extra files", *([f"- {x}" for x in validation["extra_files"]] or ["- none"]), ""])
    return "\n".join(lines)


def render_map_from_validation(validation: dict[str, Any]) -> dict[int, Path]:
    return {item["track_id"]: Path(item["matched_path"]) for item in validation["tracks"] if item["matched_path"] and item["match_method"] != "unmatched"}
