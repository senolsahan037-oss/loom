#!/usr/bin/env python3
"""Re-lock the dataset manifest against the files actually on this machine.

The catalogues are rebuilt per machine by `setup_scan.py` -- that is the whole
point, Loom ships code and never measurements. But the manifest that pins them
by content hash was written once on the machine that built the release, so after
any local scan every hash mismatches and `midi_generate` refuses to run. The
manifest has to be re-locked to describe what this machine holds; only then does
the hash check mean anything again, because from that point a mismatch really is
corruption rather than a stale record.

  relock_dataset.py --check   report which artifacts drifted, write nothing
  relock_dataset.py           re-lock the manifest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "Sensei" / "data"
MANIFEST = DATA_ROOT / "dataset_releases" / "phase6" / "dataset_release.manifest.json"
# A second manifest describes the same regenerated catalogues from the shared
# dataset side, keyed "collections" rather than "artifacts". It carries the same
# disease -- byte counts and hashes frozen on the machine that wrote them -- so
# it is relocked here too instead of failing forever after the first local scan.
SHARED = (ROOT / "Sensei" / "DatasetRoot" / "releases" / "phase6"
          / "shared_release.manifest.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def locate(recorded: str) -> Path:
    """Resolve a recorded path against this checkout, old absolute ones included."""
    path = Path(recorded).expanduser()
    if not path.is_absolute():
        return (DATA_ROOT / path).resolve()
    if path.exists():
        return path.resolve()
    parts = path.parts
    marks = [index for index, part in enumerate(parts) if part == "data"]
    return DATA_ROOT.joinpath(*parts[marks[-1] + 1:]).resolve() if marks else path


def relock_shared(check: bool) -> int:
    """The DatasetRoot manifest, whose paths are relative to its own folder."""
    if not SHARED.exists():
        return 0
    manifest = json.loads(SHARED.read_text(encoding="utf-8"))
    collections = manifest.get("collections") or {}
    drifted, missing = [], []
    for name, record in sorted(collections.items()):
        path = (SHARED.parent / record["path"]).resolve()
        if not path.is_file():
            missing.append(name)
            continue
        digest, size = sha256(path), path.stat().st_size
        if record.get("sha256") == digest and record.get("bytes") == size:
            continue
        drifted.append(name)
        if not check:
            record["sha256"], record["bytes"] = digest, size
    for name in drifted:
        print(f"  drifted    {name}  (shared)")
    for name in missing:
        print(f"  MISSING    {name}  (shared)")
    if drifted and not check:
        SHARED.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"Re-locked {len(drifted)} shared collection(s).")
    return 1 if (drifted and check) or missing else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not MANIFEST.exists():
        sys.exit(f"No manifest at {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts") or {}

    drifted, missing, unchanged = [], [], []
    updated = {}
    for name, record in sorted(artifacts.items()):
        path = locate(record["path"])
        if not path.is_file():
            missing.append((name, path))
            updated[name] = record
            continue
        digest = sha256(path)
        try:
            recorded_path = str(path.relative_to(DATA_ROOT))
        except ValueError:
            recorded_path = str(path)
        (unchanged if digest == record.get("sha256") else drifted).append(name)
        updated[name] = {"path": recorded_path, "sha256": digest,
                         "bytes": path.stat().st_size}

    for name in unchanged:
        print(f"  unchanged  {name}")
    for name in drifted:
        print(f"  drifted    {name}")
    for name, path in missing:
        print(f"  MISSING    {name}  ({path})")

    if missing:
        print(f"\n{len(missing)} artifact(s) missing. Build them first: "
              "python3 scripts/setup_scan.py")
        return 1
    if not drifted:
        print("\nThe release manifest already matches this machine.")
        return relock_shared(args.check)
    if args.check:
        print(f"\n{len(drifted)} artifact(s) drifted. Re-lock with: "
              "python3 scripts/relock_dataset.py")
        return 1

    manifest["artifacts"] = updated
    manifest["relocked_for_this_machine"] = True
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"\nRe-locked {len(drifted)} artifact(s). Paths are now relative to "
          f"Sensei/data, so they survive a rename.")
    relock_shared(args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
