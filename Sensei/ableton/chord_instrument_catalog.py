"""Native Ableton chord instrument/preset catalog inside Sensei's MIDI dataset."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ableton.ableton_index_provider import query_items


SCHEMA_VERSION = "sensei.chord-instrument-catalog.v1"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "chord_instruments"
_ALLOWED_SUFFIXES = {".adg", ".adv"}
_PROFILE_BY_NATIVE_SOUND = {
    "Pad": "ableton.chord.pad.v1",
    "Piano & Keys|Synth Keys": "ableton.chord.synth-keys.v1",
    "Piano & Keys|Electric Piano": "ableton.chord.electric-piano.v1",
    "Piano & Keys|Organ": "ableton.chord.organ.v1",
    "Piano & Keys|Piano": "ableton.chord.piano.v1",
    "Piano & Keys|Misc Keys": "ableton.chord.misc-keys.v1",
    "Piano & Keys|Clav": "ableton.chord.clav.v1",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_profile(native_sounds: Iterable[Any]) -> str | None:
    sounds = {str(value) for value in native_sounds}
    # Pad is intentionally first: a preset tagged both Pad and Keys behaves as
    # a sustained chord target, not a generic keyboard target.
    for sound in _PROFILE_BY_NATIVE_SOUND:
        if sound in sounds:
            return _PROFILE_BY_NATIVE_SOUND[sound]
    return None


def build_chord_instrument_catalog(index_items: Iterable[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build only native-tagged Suite device/preset bindings for chord targets."""
    supplied_index = index_items is not None
    items = list(index_items) if supplied_index else query_items({"limit": 100_000})
    entries: list[dict[str, Any]] = []
    audit: Counter[str] = Counter()
    seen_paths: set[str] = set()
    for item in items:
        raw_path = item.get("path")
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        # Matches the same Ableton-library rule used for identity-building in
        # genre_identity.build_preset_identities: user Library folder or an
        # installed Live app's bundled Core Library.
        path_str = str(path)
        is_ableton_library = "/Music/Ableton/" in path_str or ("/Ableton Live " in path_str and "/Core Library/" in path_str)
        if not supplied_index and not is_ableton_library:
            audit["skipped_outside_ableton_library"] += 1
            continue
        native = item.get("source_native") or {}
        sounds = [str(value) for value in native.get("ableton_sounds") or [] if value]
        profile_id = _resolve_profile(sounds)
        if profile_id is None:
            continue
        audit["native_chord_target_tagged"] += 1
        if path.suffix.lower() not in _ALLOWED_SUFFIXES:
            audit["skipped_non_midi_device"] += 1
            continue
        if str(path) in seen_paths:
            audit["duplicate_path"] += 1
            continue
        try:
            digest = _sha256(path)
        except OSError:
            audit["unreadable"] += 1
            continue
        seen_paths.add(str(path))
        entries.append({
            "schema_version": SCHEMA_VERSION,
            "reference_id": item.get("reference_id") or f"ableton-chord:{digest[:24]}",
            "name": item.get("name") or path.stem,
            "path": str(path),
            "pack": item.get("pack"),
            "content_type": "ableton_midi_device_preset",
            "profile_id": profile_id,
            "integrity": {"content_sha256": digest, "source_exists": True},
            "source_native": {"ableton_file_path": str(path), "ableton_sounds": sounds, "ableton_tags": list(native.get("ableton_tags") or [])},
        })
        audit["included"] += 1
    entries.sort(key=lambda entry: (entry["profile_id"], entry["path"]))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "profile_counts": dict(sorted(Counter(entry["profile_id"] for entry in entries).items())),
        "audit": dict(sorted(audit.items())),
        "policy": {"require_native_ableton_chord_target_tag": True, "allowed_file_extensions": sorted(_ALLOWED_SUFFIXES), "audio_in_scope": False, "default_root": "/Music/Ableton/"},
    }
    return entries, manifest


def write_chord_instrument_catalog(output_directory: str | Path | None = None) -> dict[str, Any]:
    entries, manifest = build_chord_instrument_catalog()
    output = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog_path = output / "ableton_chord_instruments.jsonl"
    manifest_path = output / "ableton_chord_instruments.manifest.json"
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
