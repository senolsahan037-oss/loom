#!/usr/bin/env python3
"""Validate the active shared dataset release without modifying it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dataset.reader import DatasetIntegrityError, SharedDataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-legacy", action="store_true")
    args = parser.parse_args()
    try:
        dataset = SharedDataset.open_current(ROOT)
        result = dataset.verify(include_legacy=args.include_legacy)
    except DatasetIntegrityError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"valid": True, "release_id": dataset.release_id, "collections": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

