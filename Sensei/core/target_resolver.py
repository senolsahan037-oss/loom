"""Resolve an SDK/Live target context to one writable Sensei target profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from copy import deepcopy


SCHEMA_VERSION = "sensei.target-resolution.v1"


def _by_id(catalog: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entry["profile_id"]): entry for entry in catalog}


def _result(profile: dict[str, Any] | None, *, writable: bool, reason: str, binding_evidence: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "writable": writable,
        "reason": reason,
        "binding_evidence": binding_evidence,
    }


def _profile_from_native_catalog(target_context: dict[str, Any], profiles: dict[str, dict[str, Any]], instrument_catalog: Iterable[dict[str, Any]] | None) -> dict[str, Any] | None:
    loaded_path = target_context.get("loaded_preset_path")
    if not loaded_path or instrument_catalog is None:
        return None
    try:
        expected = Path(loaded_path).expanduser().resolve()
    except OSError:
        return None
    for entry in instrument_catalog:
        path = entry.get("path")
        if not path:
            continue
        try:
            if Path(path).expanduser().resolve() == expected:
                return profiles.get(str(entry.get("profile_id")))
        except OSError:
            continue
    return None


def resolve_target_profile(
    target_context: dict[str, Any],
    catalog: Iterable[dict[str, Any]],
    instrument_catalog: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a profile conservatively; unknown Live targets cannot be written.

    ``track_name`` is intentionally ignored. It may be useful presentation data,
    but is not device evidence and must never authorize a MIDI write.
    """
    profiles = _by_id(catalog)
    native_profile = _profile_from_native_catalog(target_context, profiles, instrument_catalog)
    if native_profile is not None:
        return _result(native_profile, writable=True, reason="", binding_evidence="native_catalog_path")
    explicit = target_context.get("explicit_profile_id")
    if explicit:
        profile = profiles.get(str(explicit))
        if profile is None:
            return _result(None, writable=False, reason="explicit_profile_unknown")
        return _result(profile, writable=True, reason="", binding_evidence="explicit_profile")

    device_classes = {str(value) for value in target_context.get("device_classes") or []}
    if "DrumGroupDevice" in device_classes:
        profile = profiles.get("ableton.drum-rack.v1")
        if profile is None:
            return _result(None, writable=False, reason="drum_profile_missing")
        if not bool(target_context.get("verified_pad_map")):
            return _result(profile, writable=False, reason="verified_pad_map_required", binding_evidence="verified_device")
        pad_notes = target_context.get("verified_pad_notes")
        if not isinstance(pad_notes, list) or not pad_notes or any(not isinstance(note, int) or note < 0 or note > 127 for note in pad_notes):
            return _result(profile, writable=False, reason="verified_pad_notes_required", binding_evidence="verified_device")
        profile = deepcopy(profile)
        profile.setdefault("constraints", {})["allowed_pitches"] = sorted(set(pad_notes))
        return _result(profile, writable=True, reason="", binding_evidence="verified_device")

    return _result(None, writable=False, reason="target_profile_unresolved")
