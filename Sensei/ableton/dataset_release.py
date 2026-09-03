"""Versioned integrity manifest for immutable Sensei MIDI dataset releases."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


SCHEMA_VERSION = "sensei.dataset-release.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_dataset_release_manifest(output_directory: str | Path, *, artifacts: Mapping[str, str | Path]) -> dict:
    """Write one manifest that pins every dataset artifact by content hash."""
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    normalized = {}
    for name, value in sorted(artifacts.items()):
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Dataset artifact is missing: {name} ({path})")
        # Recorded relative to the data root whenever the artifact lives under
        # it. An absolute path here is the build machine's, which exists on no
        # other disk and breaks the moment the project is renamed.
        data_root = Path(output_directory).resolve().parents[1]
        try:
            recorded = path.resolve().relative_to(data_root)
        except ValueError:
            recorded = path
        normalized[name] = {"path": str(recorded), "sha256": _sha256(path), "bytes": path.stat().st_size}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": normalized,
        "policy": {"generator_must_consume_manifested_artifacts": True},
    }
    path = output / "dataset_release.manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return {"manifest_path": str(path), "artifact_count": len(normalized), "manifest": manifest}
