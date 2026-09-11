#!/usr/bin/env python3
"""A session plan derived from a set the user already arranged.

ArrangementGPS invents a structure from a prompt; when the arrangement
already exists (locators, tempo, key), the plan must come FROM the set:
its sections are the set's own cue points, its tempo the set's tempo, and
the tracks are the ones the caller wants added -- each with a real preset
family, never a stand-in. project_build then adopts the existing cue
points (create_locator on an existing beat adopts, measured) and writes
only the new tracks.

    python3 scripts/plan_from_set.py --als SET.als --out plan.json \
        --track "Dub Kit:drum:Halfstep Kit" --track "Wobble:bass:Reese Filtered" [--key "B Minor"] [--genre Dubstep]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loom_server", ROOT / "mcp_server" / "server.py")
loom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loom)  # type: ignore[union-attr]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--als", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--track", action="append", required=True, metavar="NAME:ROLE:FAMILY", help="a track to add: name, sensei role (drum/bass/chord), real preset family")
    parser.add_argument("--key", default=None, help='e.g. "B Minor"; default: the set\'s scale when it has one, else the analysis tonal candidate is NOT used')
    parser.add_argument("--genre", default="Dubstep")
    parser.add_argument("--name", default=None)
    parser.add_argument("--energy", default=None, help="section energies as name=value pairs, comma separated (default 70; a section named like intro/outro/es 40)")
    args = parser.parse_args()
    arr = loom.handle_project_inspect_arrangement({"als_path": args.als})
    sections = arr.get("sections_from_locators") or []
    if not sections:
        print("the set has no locators; project_build needs sections. Add locators in Live first.", file=sys.stderr)
        return 1
    energies = {k.strip().lower(): float(v) for k, v in (p.split("=") for p in (args.energy or "").split(",") if "=" in p)}
    locators = []
    for index, s in enumerate(sections):
        name = s["name"].strip()
        default = 40.0 if any(w in name.lower() for w in ("intro", "outro", "es", "break")) else 70.0
        locators.append({"id": f"s{index}", "name": name, "start_bar": int(s["start_bar"]), "end_bar": int(s["end_bar"]), "energy": energies.get(name.lower(), default)})
    tracks = []
    for spec_ in args.track:
        name, role, family = (x.strip() for x in spec_.split(":", 2))
        tracks.append({"ableton_name": name, "sensei_role": role, "instrument_family": family})
    plan = {"project": {"name": args.name or f"{Path(args.als).stem} {args.genre}", "bpm": arr["tempo"], "key": args.key or "", "genre": args.genre,
                        "total_bars": arr["total_bars"], "source_set": args.als, "beats_per_bar": arr["beats_per_bar"]},
            "locators": locators, "tracks": tracks}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"plan_path": str(out), "sections": [(l["name"], l["start_bar"], l["end_bar"], l["energy"]) for l in locators], "tracks": tracks, "bpm": arr["tempo"], "key": plan["project"]["key"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
