#!/usr/bin/env python3
"""Apply and reload-verify Golden Step's conservative DRUM BUSS parameters."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from aimixmaster.als_io import load_als, save_als_atomic
from aimixmaster.drum_buss_parameters import (
    DrumBussParameterError,
    apply_conservative_drum_buss_parameters,
    verify_conservative_drum_buss_parameters,
)
from aimixmaster.project_analyzer import preservation_snapshot
from aimixmaster.verification import verify_drum_buss


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("als", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        tree = load_als(args.als)
        preserved_before = preservation_snapshot(tree.getroot())
        changes = apply_conservative_drum_buss_parameters(tree.getroot())
        for change in changes:
            print(f"{change.path}: {change.old} -> {change.new}")
        if not args.apply:
            print("READY: no ALS write performed")
            return 0

        backup = args.als.with_suffix(args.als.suffix + ".drum-buss-v1_1-backup")
        if backup.exists():
            raise DrumBussParameterError(f"Backup already exists; refusing to overwrite: {backup}")
        shutil.copy2(args.als, backup)
        try:
            save_als_atomic(tree, args.als)
            reloaded = load_als(args.als).getroot()
            verify_drum_buss(args.als, preserved_before=preserved_before)
            verify_conservative_drum_buss_parameters(reloaded)
        except BaseException:
            shutil.copy2(backup, args.als)
            raise
        print("VERIFIED: exact three-device chain and v1.1 parameter state after reload")
        print(f"Backup: {backup}")
        return 0
    except (DrumBussParameterError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
