"""Verified Ableton Groove catalog and SDK-compatible MIDI payloads.

This module deliberately treats ``.agr`` files as Ableton source material, not
as genre-labelled training data.  An entry is admitted only after its actual
MIDI timing template can be parsed; filename-derived BPM/swing hints remain UI
conveniences and are never recorded as authoritative musical metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import gzip
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable



SCHEMA_VERSION = "sensei.groove-catalog.v1"
SDK_PAYLOAD_SCHEMA_VERSION = "sensei.sdk-midi-write.v1"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "groove_corpus"


def scan_grooves(roots: Iterable[str | Path] | None = None) -> list[dict[str, Any]]:
    """Discover .agr files without treating filename hints as musical truth."""
    root_paths = [Path(root).expanduser().resolve() for root in roots] if roots is not None else [
        Path.home() / "Music" / "Ableton" / "Factory Packs",
        Path.home() / "Music" / "Ableton" / "User Library",
    ]
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in root_paths:
        if not root.exists():
            continue
        for path in root.rglob("*.agr"):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            parts = resolved.parts
            category = parts[parts.index("Factory Packs") + 1] if "Factory Packs" in parts and parts.index("Factory Packs") + 1 < len(parts) else resolved.parent.name
            entries.append({"name": resolved.stem, "path": str(resolved), "category": category})
    return entries


def parse_groove_notes(path_str: str) -> list[dict[str, Any]] | None:
    try:
        raw = Path(path_str).read_bytes()
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
        root = ET.fromstring(raw)
        return sorted(
            [{"time": float(note.attrib["Time"]), "velocity": float(note.attrib.get("Velocity", 100.0))} for note in root.findall(".//MidiNoteEvent") if "Time" in note.attrib],
            key=lambda note: note["time"],
        )
    except (OSError, ValueError, ET.ParseError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reference_id(path: Path, content_sha256: str) -> str:
    return f"ableton-groove:{content_sha256[:24]}"


def _catalog_sha256(entries: list[dict[str, Any]]) -> str:
    canonical_lines = [json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for entry in entries]
    return hashlib.sha256(("\n".join(canonical_lines) + "\n").encode("utf-8")).hexdigest()


def _template_length(notes: list[dict[str, Any]]) -> float:
    """Groove templates loop at a bar boundary; Live's standard base is 4 beats."""
    maximum_time = max((float(note["time"]) for note in notes), default=0.0)
    return max(4.0, float(int(maximum_time // 4.0 + 1) * 4))


def build_groove_catalog(roots: Iterable[str | Path] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create a strict catalog of parse-verified Ableton ``.agr`` templates."""
    root_paths = [Path(root).expanduser().resolve() for root in roots] if roots is not None else None
    scanned = scan_grooves(root_paths)
    audit: Counter[str] = Counter(agr_files_seen=len(scanned))
    entries: list[dict[str, Any]] = []

    for item in sorted(scanned, key=lambda value: str(value["path"])):
        path = Path(item["path"]).resolve()
        try:
            content_sha256 = _sha256(path)
        except OSError:
            audit["unreadable"] += 1
            continue
        notes = parse_groove_notes(str(path))
        if not notes:
            audit["unparseable"] += 1
            continue
        if any(float(note["time"]) < 0 or not 0 <= float(note["velocity"]) <= 127 for note in notes):
            audit["invalid_template"] += 1
            continue

        entry = {
            "schema_version": SCHEMA_VERSION,
            "reference_id": _reference_id(path, content_sha256),
            "name": item["name"],
            "path": str(path),
            "content_type": "ableton_groove",
            "source": "ableton_live_library",
            "source_native": {
                "ableton_file_path": str(path),
                "ableton_pack": item.get("category"),
                "ableton_genres": [],
                "ableton_tags": [],
            },
            "content_sha256": content_sha256,
            "parse_status": "verified",
            "usable": True,
            "template": {
                "cycle_beats": _template_length(notes),
                "note_count": len(notes),
                "notes": notes,
            },
        }
        entries.append(entry)
        audit["verified"] += 1

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "entry_count": len(entries),
        "audit": dict(sorted(audit.items())),
        "integrity": {
            "algorithm": "sha256",
            "catalog_is_parse_verified": True,
            "catalog_sha256": _catalog_sha256(entries),
        },
        "policy": {
            "filename_inference_is_authoritative": False,
            "native_genre_required": False,
            "sdk_payload_schema_version": SDK_PAYLOAD_SCHEMA_VERSION,
        },
    }
    return entries, manifest


def write_groove_catalog(
    output_directory: str | Path | None = None,
    *,
    roots: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Atomically write the verified catalog and its immutable audit manifest."""
    entries, manifest = build_groove_catalog(roots)
    output = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog_path = output / "ableton_groove_catalog.jsonl"
    manifest_path = output / "ableton_groove_catalog.manifest.json"
    catalog_temp = catalog_path.with_suffix(".jsonl.tmp")
    manifest_temp = manifest_path.with_suffix(".json.tmp")

    with catalog_temp.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    with manifest_temp.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(catalog_temp, catalog_path)
    os.replace(manifest_temp, manifest_path)
    return {"catalog_path": str(catalog_path), "manifest_path": str(manifest_path), "entry_count": len(entries), "entries": entries}


def build_sdk_midi_payload(
    events: Iterable[dict[str, Any]],
    *,
    clip_length: float,
    groove_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact note fields accepted by the Sensei SDK ClipSlot writer."""
    notes = []
    for event in events:
        notes.append({
            "pitch": int(event.get("pitch", event.get("note"))),
            "time": float(event.get("time", event.get("beat"))),
            "duration": float(event.get("duration", 0.25)),
            "velocity": int(event.get("velocity", 100)),
        })
    payload: dict[str, Any] = {
        "schema_version": SDK_PAYLOAD_SCHEMA_VERSION,
        "notes": notes,
        "clip_length": float(clip_length),
    }
    if groove_entry is not None:
        payload["provenance"] = {
            "groove_reference_id": groove_entry.get("reference_id"),
            "groove_content_sha256": groove_entry.get("content_sha256"),
            "groove_parse_status": groove_entry.get("parse_status"),
        }
    return payload
