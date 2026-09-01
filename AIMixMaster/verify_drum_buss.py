#!/usr/bin/env python3
"""Verify a DRUM BUSS write against its pre-write ALS backup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aimixmaster.als_io import load_als
from aimixmaster.project_analyzer import preservation_snapshot
from aimixmaster.verification import VerificationError, verify_drum_buss


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("als", type=Path)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    try:
        baseline = preservation_snapshot(load_als(args.backup).getroot())
        proof = verify_drum_buss(args.als, preserved_before=baseline)
        print(f"VERIFIED {proof.target_name}: {' > '.join(proof.device_tags)}")
        print(f"Reloaded NextPointeeId: {proof.next_pointee_id}")
        return 0
    except (VerificationError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
