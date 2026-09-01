"""Fail-closed target-instrument capability dataset for MIDI-only Sensei."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "sensei.instrument-target-profile.v1"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "instrument_capabilities"


def _chord_profile(profile_id: str, *, family: str, pitch_range: list[int], max_polyphony: int, allowed_roles: list[str], max_events_per_bar: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": profile_id,
        "target_role": "chord",
        "allowed_roles": allowed_roles,
        "blocked_roles": ["drum", "percussion", "bass", "sub_bass", "bass_riff", "lead", "melody"],
        "constraints": {"pitch_range": pitch_range, "max_polyphony": max_polyphony, "requires_verified_pad_map": False, "requires_choke_resolution": False},
        "variation_defaults": {"source_role": "chord", "density": "medium", "max_events_per_bar": max_events_per_bar, "preferred_grid": 0.25, "glide_allowed": False, "max_note_duration_beats": 8.0},
        "binding_policy": "native_catalog_or_explicit_user",
        "write_policy": "safe_when_bound",
        "evidence": {"kind": "native_ableton_sound_tag", "native_sound_family": family, "required_device_class": None},
    }


def _chord_profiles() -> list[dict[str, Any]]:
    return [
        _chord_profile("ableton.chord.pad.v1", family="Pad", pitch_range=[48, 84], max_polyphony=6, allowed_roles=["chord", "voicing", "pad"], max_events_per_bar=4),
        _chord_profile("ableton.chord.synth-keys.v1", family="Piano & Keys|Synth Keys", pitch_range=[40, 84], max_polyphony=6, allowed_roles=["chord", "voicing"], max_events_per_bar=8),
        _chord_profile("ableton.chord.electric-piano.v1", family="Piano & Keys|Electric Piano", pitch_range=[36, 84], max_polyphony=8, allowed_roles=["chord", "voicing"], max_events_per_bar=8),
        _chord_profile("ableton.chord.organ.v1", family="Piano & Keys|Organ", pitch_range=[36, 84], max_polyphony=6, allowed_roles=["chord", "voicing"], max_events_per_bar=8),
        _chord_profile("ableton.chord.piano.v1", family="Piano & Keys|Piano", pitch_range=[36, 88], max_polyphony=8, allowed_roles=["chord", "voicing"], max_events_per_bar=8),
        _chord_profile("ableton.chord.misc-keys.v1", family="Piano & Keys|Misc Keys", pitch_range=[40, 84], max_polyphony=6, allowed_roles=["chord", "voicing"], max_events_per_bar=8),
        _chord_profile("ableton.chord.clav.v1", family="Piano & Keys|Clav", pitch_range=[40, 84], max_polyphony=6, allowed_roles=["chord", "voicing"], max_events_per_bar=10),
    ]


def _profile_catalog() -> list[dict[str, Any]]:
    """Profiles are intentionally conservative until a Live target is bound."""
    return [
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": "ableton.drum-rack.v1",
            "target_role": "drum",
            "allowed_roles": ["drum", "percussion"],
            "blocked_roles": ["bass", "sub_bass", "bass_riff", "chord", "pad", "lead", "melody", "arp_pattern"],
            "constraints": {
                "pitch_range": [0, 127],
                "max_polyphony": None,
                "requires_verified_pad_map": True,
                "requires_choke_resolution": True,
            },
            "binding_policy": "verified_drum_rack_only",
            "write_policy": "safe_when_bound",
            "evidence": {"kind": "verified_live_device", "required_device_class": "DrumGroupDevice"},
        },
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": "ableton.bass.monophonic.v1",
            "target_role": "bass",
            "allowed_roles": ["bass", "sub_bass", "bass_riff"],
            "blocked_roles": ["drum", "percussion", "chord", "pad", "lead", "melody", "arp_pattern"],
            "constraints": {
                "pitch_range": [28, 55],
                "max_polyphony": 1,
                "requires_verified_pad_map": False,
                "requires_choke_resolution": False,
            },
            "binding_policy": "explicit_user_or_verified_preset",
            "write_policy": "safe_when_bound",
            "evidence": {"kind": "explicit_binding_required", "required_device_class": None},
        },
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": "ableton.bass.808.v1",
            "target_role": "bass",
            "allowed_roles": ["bass", "sub_bass", "bass_riff"],
            "blocked_roles": ["drum", "percussion", "chord", "pad", "lead", "melody", "arp_pattern"],
            "constraints": {
                "pitch_range": [24, 48],
                "max_polyphony": 1,
                "requires_verified_pad_map": False,
                "requires_choke_resolution": False,
            },
            "variation_defaults": {
                "source_role": "bass",
                "density": "sparse",
                "max_events_per_bar": 6,
                "preferred_grid": 0.25,
                "glide_allowed": True,
                "max_note_duration_beats": 4.0,
            },
            "binding_policy": "explicit_user_or_verified_preset",
            "write_policy": "safe_when_bound",
            "evidence": {"kind": "explicit_binding_required", "required_device_class": None},
        },
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": "ableton.bass.synth.v1",
            "target_role": "bass",
            "allowed_roles": ["bass", "sub_bass", "bass_riff"],
            "blocked_roles": ["drum", "percussion", "chord", "pad", "lead", "melody", "arp_pattern"],
            "constraints": {
                "pitch_range": [28, 60],
                "max_polyphony": 1,
                "requires_verified_pad_map": False,
                "requires_choke_resolution": False,
            },
            "variation_defaults": {
                "source_role": "bass",
                "density": "medium",
                "max_events_per_bar": 10,
                "preferred_grid": 0.25,
                "glide_allowed": False,
                "max_note_duration_beats": 2.0,
            },
            "binding_policy": "explicit_user_or_verified_preset",
            "write_policy": "safe_when_bound",
            "evidence": {"kind": "explicit_binding_required", "required_device_class": None},
        },
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": "ableton.bass.electric.v1",
            "target_role": "bass",
            "allowed_roles": ["bass", "bass_riff"],
            "blocked_roles": ["drum", "percussion", "chord", "pad", "lead", "melody", "arp_pattern"],
            "constraints": {"pitch_range": [28, 60], "max_polyphony": 1, "requires_verified_pad_map": False, "requires_choke_resolution": False},
            "variation_defaults": {"source_role": "bass", "density": "medium", "max_events_per_bar": 10, "preferred_grid": 0.25, "glide_allowed": False, "max_note_duration_beats": 2.0},
            "binding_policy": "native_catalog_or_explicit_user",
            "write_policy": "safe_when_bound",
            "evidence": {"kind": "native_ableton_sound_tag", "required_device_class": None},
        },
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": "ableton.bass.upright.v1",
            "target_role": "bass",
            "allowed_roles": ["bass", "bass_riff"],
            "blocked_roles": ["drum", "percussion", "chord", "pad", "lead", "melody", "arp_pattern"],
            "constraints": {"pitch_range": [28, 55], "max_polyphony": 1, "requires_verified_pad_map": False, "requires_choke_resolution": False},
            "variation_defaults": {"source_role": "bass", "density": "medium", "max_events_per_bar": 8, "preferred_grid": 0.25, "glide_allowed": False, "max_note_duration_beats": 2.0},
            "binding_policy": "native_catalog_or_explicit_user",
            "write_policy": "safe_when_bound",
            "evidence": {"kind": "native_ableton_sound_tag", "required_device_class": None},
        },
        *_chord_profiles(),
    ]


def _catalog_sha256(entries: Iterable[dict[str, Any]]) -> str:
    serialized = [json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for entry in entries]
    return hashlib.sha256(("\n".join(serialized) + "\n").encode("utf-8")).hexdigest()


def build_instrument_capability_catalog() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries = _profile_catalog()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "integrity": {"algorithm": "sha256", "catalog_sha256": _catalog_sha256(entries)},
        "policy": {
            "unknown_target_is_writable": False,
            "track_name_is_binding_evidence": False,
            "all_writes_require_bound_target_profile": True,
        },
    }
    return entries, manifest


def validate_target_profile(profile: dict[str, Any], *, requested_role: str, events: Iterable[dict[str, Any]]) -> tuple[bool, str]:
    """Validate a prepared MIDI result against a profile before SDK payload creation."""
    if requested_role not in profile.get("allowed_roles", []):
        return False, "role_not_allowed"
    constraints = profile.get("constraints") or {}
    low, high = constraints.get("pitch_range", [0, 127])
    allowed_pitches = constraints.get("allowed_pitches")
    grouped: dict[float, int] = {}
    for event in events:
        pitch = int(event.get("pitch", event.get("note")))
        time = float(event.get("time", event.get("beat")))
        if pitch < low or pitch > high:
            return False, "pitch_out_of_range"
        if allowed_pitches is not None and pitch not in allowed_pitches:
            return False, "pitch_not_in_verified_pad_map"
        grouped[time] = grouped.get(time, 0) + 1
    polyphony_limit = constraints.get("max_polyphony")
    if polyphony_limit is not None and any(count > int(polyphony_limit) for count in grouped.values()):
        return False, "polyphony_limit_exceeded"
    return True, ""


def write_instrument_capability_catalog(output_directory: str | Path | None = None) -> dict[str, Any]:
    entries, manifest = build_instrument_capability_catalog()
    output = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog_path = output / "instrument_target_profiles.jsonl"
    manifest_path = output / "instrument_target_profiles.manifest.json"
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
