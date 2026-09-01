#!/usr/bin/env python3
"""Export the verified Golden Step DRUM BUSS template."""

from __future__ import annotations

import argparse
from pathlib import Path

from aimixmaster.template_exporter import export_boom_bap_95_drum_bus


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("als", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("templates/boom_bap_95_drum_bus_v1.json")
    )
    args = parser.parse_args()
    export_boom_bap_95_drum_bus(args.als, args.output)
    print(f"EXPORTED {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
