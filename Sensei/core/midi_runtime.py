"""The single Sensei MIDI generation entry point.

This module is deliberately small: it binds an evidenced Live target, selects
only release-pinned MIDI data, creates a constrained variation and returns the
payload consumed by the official SDK extension.  It does not write to Live.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from core.midi_variation_engine import generate_midi_variation, load_canonical_midi_corpus
from core.target_resolver import resolve_target_profile


SCHEMA_VERSION = "sensei.midi-runtime.v1"
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _release_artifacts(data_root: Path) -> dict[str, Path]:
    """Read the current locked release, never a mutable discovery source."""
    manifest_path = data_root / "dataset_releases" / "phase6" / "dataset_release.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts") or {}
    # Phase 5's public manifest names describe dataset objects.  The runtime
    # keeps its internal names independent, so one release contract is enough.
    release_names = {
        "canonical_midi_corpus": "canonical_midi",
        "instrument_target_profiles": "instrument_capabilities",
        "bass_instruments": "bass_instruments",
        "chord_instruments": "chord_instruments",
        "preset_genre_identities": "preset_genre_identities",
        "genre_neighbor_graph": "genre_neighbor_graph",
    }
    missing = set(release_names.values()) - set(artifacts)
    if missing:
        raise ValueError(f"dataset_release_missing_artifacts:{','.join(sorted(missing))}")
    resolved: dict[str, Path] = {}
    for name, release_name in release_names.items():
        record = artifacts[release_name]
        path = Path(record["path"]).expanduser()
        if not path.is_absolute():
            path = data_root / path
        path = path.resolve()
        expected_hash = record.get("sha256")
        if not expected_hash:
            raise ValueError(f"dataset_release_hash_missing:{release_name}")
        if _sha256(path) != expected_hash:
            raise ValueError(f"dataset_release_hash_mismatch:{release_name}")
        resolved[name] = path
    return resolved


def prepare_midi_variation(
    *,
    target_context: dict[str, Any],
    genre: str | list[str] | None,
    bars: int,
    seed: int,
    variation_amount: float = 0.35,
    target_root: str | None = None,
    target_mode: str | None = None,
    exclude_reference_ids: list[str] | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Produce one safe SDK MIDI payload, or a structured no-write result."""
    root = Path(data_root or DEFAULT_DATA_ROOT).expanduser().resolve()
    try:
        artifacts = _release_artifacts(root)
        profiles = _read_jsonl(artifacts["instrument_target_profiles"])
        instruments = _read_jsonl(artifacts["bass_instruments"]) + _read_jsonl(artifacts["chord_instruments"])
        resolution = resolve_target_profile(target_context, profiles, instruments)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {"schema_version": SCHEMA_VERSION, "generation_safe": False, "payload": None, "resolution": None, "error": f"dataset_release_invalid:{error}"}

    if not resolution["writable"]:
        return {"schema_version": SCHEMA_VERSION, "generation_safe": False, "payload": None, "resolution": resolution, "error": resolution["reason"]}

    result = generate_midi_variation(
        load_canonical_midi_corpus(artifacts["canonical_midi_corpus"]),
        target_profile=resolution["profile"],
        genre=genre,
        bars=bars,
        seed=seed,
        variation_amount=variation_amount,
        target_root=target_root,
        target_mode=target_mode,
        exclude_reference_ids=exclude_reference_ids,
    )
    return {"schema_version": SCHEMA_VERSION, "generation_safe": result["generation_safe"], "payload": result["payload"], "resolution": resolution, "diagnostics": result["diagnostics"], "error": result["error"]}
