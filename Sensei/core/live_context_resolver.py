"""Turn Live SDK device evidence into a release-backed generation context."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ableton.genre_identity import resolve_preset_genre


_INSTRUMENT_ROLES = ("bass", "chord")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _normalized_name(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _resolve_instrument_target(device_names: list[str], identities: list[dict[str, Any]]) -> tuple[str, str]:
    """Resolve exactly one (device_name, role) pair for a non-drum Live target.

    The SDK exposes no device-class or preset-path evidence for a generic
    instrument, only its display name.  Names are matched against the same
    identity catalog used for genre resolution; names that match nothing
    (audio effects, unrelated devices) are silently ignored, since catalog
    membership is the only trustworthy signal available here.
    """
    candidates: list[tuple[str, str]] = []
    for name in device_names:
        normalized = _normalized_name(name)
        roles = {
            str(identity.get("role"))
            for identity in identities
            if identity.get("role") in _INSTRUMENT_ROLES and identity.get("normalized_name") == normalized
        }
        if len(roles) > 1:
            raise ValueError(f"instrument_role_ambiguous_multiple_roles:{name}:{','.join(sorted(roles))}")
        if len(roles) == 1:
            candidates.append((name, next(iter(roles))))
    if not candidates:
        raise ValueError(f"instrument_role_unresolved:{','.join(device_names)}")
    if len(candidates) > 1:
        raise ValueError(f"instrument_role_ambiguous_multiple_devices:{','.join(name for name, _ in candidates)}")
    return candidates[0]


def resolve_live_context(live_target: dict[str, Any], *, identity_path: Path, graph_path: Path) -> dict[str, Any]:
    identities = _jsonl(identity_path)
    role = str(live_target.get("role") or "")
    if role:
        device_name = str(live_target.get("device_name") or "")
    else:
        device_names = [str(name) for name in live_target.get("device_names") or [] if str(name).strip()]
        device_name, role = _resolve_instrument_target(device_names, identities)
    default_genre = live_target.get("default_genre")
    result = resolve_preset_genre(
        device_name=device_name,
        role=role,
        identities=identities,
        graph=json.loads(graph_path.read_text(encoding="utf-8")),
        default_genre=str(default_genre) if default_genre else None,
    )
    if not result["resolved"]:
        raise ValueError(f"{result['reason']}:{device_name}")
    target_context: dict[str, Any] = {
        "device_classes": list(live_target.get("device_classes") or []),
        "verified_pad_map": bool(live_target.get("verified_pad_map")),
        "verified_pad_notes": list(live_target.get("verified_pad_notes") or []),
    }
    if role != "drum":
        target_context["explicit_profile_id"] = result["identity"]["profile_id"]
    context = {
        "target_context": target_context,
        "genre_mode": result["genre_mode"],
        "bars": int(live_target.get("bars", 4)),
        "seed": int(live_target.get("seed", 1)),
        "variation_amount": float(live_target.get("variation_amount", 0.35)),
        "context_source": "live_sdk_release_identity",
        "genre_resolution": result,
    }
    if result["genre_mode"] == "synthesis":
        context["genres"] = result["genres"]
    else:
        context["genre"] = result["genre"]
    # A drum rack's pitch selects a pad, not a scale degree -- key/mode never
    # applies there, so it's deliberately never even read for role=="drum".
    if role != "drum":
        target_root = live_target.get("target_root")
        target_mode = live_target.get("target_mode")
        if target_root:
            context["target_root"] = str(target_root)
        if target_mode:
            context["target_mode"] = str(target_mode)
    # Source diversity is a pool-size concern, not a musical-correctness one,
    # so unlike target_root/target_mode it applies to every role including drum.
    exclude_reference_ids = [str(value) for value in live_target.get("exclude_reference_ids") or [] if str(value).strip()]
    if exclude_reference_ids:
        context["exclude_reference_ids"] = exclude_reference_ids
    return context
