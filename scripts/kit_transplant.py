#!/usr/bin/env python3
"""One real Drum Rack preset (.adg) into a Live set file. The conversion and
every check live in Presetor/presetor/preset_transplant.py; this is the
single-kit command that proved it on a real Live (2026-09-07).

    python3 scripts/kit_transplant.py --out DIR [--adg PATH] [--track Kit]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Presetor"))
from presetor import preset_transplant as pt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--adg", default=str(pt.LIVE_APP / "Core Library" / "Racks" / "Drum Racks" / "Electronic" / "BNYX Boot Kit.adg"))
    parser.add_argument("--out", required=True, help="an isolated directory; a Live project folder is created inside it")
    parser.add_argument("--track", default="Kit")
    args = parser.parse_args()
    try:
        builder = pt.SetBuilder()
        track = builder.add_midi_track(args.track)
        info = builder.place_preset(track, Path(args.adg))
        report = builder.finish(Path(args.out) / "Loom Kit Transplant Project" / f"Loom Kit Transplant - {info['name']}.als")
        report["source_adg"] = args.adg
    except pt.Refused as error:
        report = {"written": False, "refused": str(error), "source_adg": args.adg}
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("written") else 1


if __name__ == "__main__":
    sys.exit(main())
