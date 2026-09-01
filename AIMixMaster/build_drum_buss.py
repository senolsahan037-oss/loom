#!/usr/bin/env python3
"""Build and prove the native EQ Eight -> Glue -> Utility DRUM BUSS chain."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from aimixmaster.als_io import load_als, save_als_atomic
from aimixmaster.buss_builder import BussBuildError, build_drum_buss
from aimixmaster.project_analyzer import preservation_snapshot
from aimixmaster.verification import VerificationError, verify_drum_buss


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("als", type=Path)
    parser.add_argument("--source", default="KICK BUSS")
    parser.add_argument("--apply", action="store_true", help="Perform the atomic ALS write")
    args = parser.parse_args()

    try:
        tree = load_als(args.als)
        preserved_before = preservation_snapshot(tree.getroot())
        result = build_drum_buss(tree.getroot(), source_name=args.source)
        if not args.apply:
            status = "READY" if result.changed else "ALREADY VERIFIED"
            print(f"{status} {result.target_name}: {' > '.join(result.inserted_tags)}")
            print(f"NextPointeeId after write: {result.next_pointee_id}")
            return 0
        if not result.changed:
            proof = verify_drum_buss(args.als, preserved_before=preserved_before)
            print(f"VERIFIED {proof.target_name}: {' > '.join(proof.device_tags)}")
            print("No write performed; exact direct chain already exists.")
            return 0
        backup = args.als.with_suffix(args.als.suffix + ".aimixmaster-backup")
        backup_created = False
        if backup.exists():
            if backup.read_bytes() != args.als.read_bytes():
                raise BussBuildError(f"Backup already exists; refusing to overwrite: {backup}")
        else:
            shutil.copy2(args.als, backup)
            backup_created = True
        save_als_atomic(tree, args.als)
        proof = verify_drum_buss(args.als, preserved_before=preserved_before)
        print(f"VERIFIED {proof.target_name}: {' > '.join(proof.device_tags)}")
        print(f"Reloaded NextPointeeId: {proof.next_pointee_id}")
        backup_status = "created" if backup_created else "reused"
        print(f"Backup {backup_status}: {backup}")
        return 0
    except (BussBuildError, VerificationError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
