"""Read-only access to a release-pinned Loom dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


class DatasetIntegrityError(RuntimeError):
    """Raised when a release or artifact cannot be trusted."""


class SharedDataset:
    def __init__(self, root: Path, manifest_path: Path, manifest: dict[str, Any]):
        self.root = root.resolve()
        self.manifest_path = manifest_path.resolve()
        self.manifest = manifest

    @classmethod
    def open_current(cls, root: Path | str | None = None) -> "SharedDataset":
        dataset_root = Path(root or Path(__file__).resolve().parents[2]).resolve()
        pointer_path = dataset_root / "releases" / "current.json"
        pointer = _read_json(pointer_path)
        manifest_path = (pointer_path.parent / pointer["manifest"]).resolve()
        manifest = _read_json(manifest_path)
        if pointer["release_id"] != manifest.get("release_id"):
            raise DatasetIntegrityError("Current release ID does not match its manifest")
        if manifest.get("schema_version") != "ai-producer.dataset-release.v1":
            raise DatasetIntegrityError("Unsupported shared dataset release schema")
        return cls(dataset_root, manifest_path, manifest)

    @property
    def release_id(self) -> str:
        return str(self.manifest["release_id"])

    def collection_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.manifest["collections"]))

    def collection(self, collection_id: str) -> dict[str, Any]:
        try:
            return dict(self.manifest["collections"][collection_id])
        except KeyError as exc:
            raise KeyError(f"Unknown dataset collection: {collection_id}") from exc

    def path_for(self, collection_id: str) -> Path:
        record = self.collection(collection_id)
        return (self.manifest_path.parent / record["path"]).resolve()

    def verify(self, include_legacy: bool = False) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for collection_id in self.collection_ids():
            record = self.collection(collection_id)
            if record["status"] == "legacy" and not include_legacy:
                results[collection_id] = {"status": "skipped_legacy"}
                continue
            path = self.path_for(collection_id)
            if not path.exists():
                raise DatasetIntegrityError(f"Missing collection {collection_id}: {path}")
            if record["format"] == "directory":
                actual_records = sum(1 for item in path.iterdir() if item.suffix == ".json")
                expected_records = record.get("records")
                if expected_records is not None and actual_records != expected_records:
                    raise DatasetIntegrityError(
                        f"Record count mismatch for {collection_id}: {actual_records} != {expected_records}"
                    )
                results[collection_id] = {"status": "verified_legacy", "records": actual_records}
                continue
            actual_bytes = path.stat().st_size
            actual_sha256 = _sha256(path)
            if actual_bytes != record.get("bytes"):
                raise DatasetIntegrityError(f"Byte count mismatch for {collection_id}")
            if actual_sha256 != record.get("sha256"):
                raise DatasetIntegrityError(f"SHA-256 mismatch for {collection_id}")
            results[collection_id] = {
                "status": "verified",
                "bytes": actual_bytes,
                "sha256": actual_sha256,
            }
        return results

    def read_json(self, collection_id: str) -> Any:
        record = self.collection(collection_id)
        if record["format"] != "json":
            raise TypeError(f"Collection {collection_id} is not JSON")
        return _read_json(self.path_for(collection_id))

    def iter_jsonl(self, collection_id: str) -> Iterator[dict[str, Any]]:
        record = self.collection(collection_id)
        if record["format"] != "jsonl":
            raise TypeError(f"Collection {collection_id} is not JSONL")
        with self.path_for(collection_id).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DatasetIntegrityError(
                            f"Invalid JSONL in {collection_id} at line {line_number}"
                        ) from exc


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise DatasetIntegrityError(f"Cannot read dataset metadata: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

