#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""Phase 1 of a build: the plan's tracks with their REAL presets, as a set
file on disk (project_file_build). Phase 2 -- MIDI and locators into that
set once it is open in Live -- is scripts/build_live_project.py with
--set-manifest, the same plan_path, and --into-current-set.

    python3 scripts/build_project_file.py --prompt "..." [--tracks Kit "Main Bass" Keys] [--open]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loom_server", ROOT / "mcp_server" / "server.py")
loom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loom)  # type: ignore[union-attr]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prompt", help="the musical brief ArrangementGPS plans from")
    parser.add_argument("--plan-path", help="an existing plan instead of a new one")
    parser.add_argument("--tracks", nargs="*", default=None, help="plan tracks to build; default: every writable track")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--open", action="store_true", help="hand the set file to Live (macOS open) when written")
    args = parser.parse_args()
    request = {k: v for k, v in (("prompt", args.prompt), ("plan_path", args.plan_path), ("tracks", args.tracks),
                                 ("out_dir", args.out_dir), ("name", args.name)) if v}
    answer = loom.handle_project_file_build(request)
    print(f"STATUS  {answer.get('status')}" + (f"  ({answer.get('reason')})" if answer.get("reason") else ""))
    print(f"PLAN    {answer['plan'].get('plan_path')}")
    for t in answer.get("tracks") or []:
        print(f"  track {t['track']:12} {t['role']:6} {t['preset_name']!s:26} {t['device']:22} v{t['preset_version']}  devices={t['devices']} pads={t['pads']} macros={t['macros']}"
              + (f"  DRIFT vs set reference: {t['version_drift']}" if t.get("version_drift") else ""))
    for u in answer.get("unresolved") or []:
        print(f"  NOT CREATED {u['track']:12} {u.get('family')!s:26} {u['reason']}")
    if answer.get("written"):
        sv = answer["static_validation"]
        print(f"SET     {answer['artifact']}  ({answer['bytes']} bytes, tempo {answer.get('tempo')})")
        print(f"MANIFEST {answer['manifest']}")
        print(f"STATIC  alsguard {sv['alsguard']}, tags {sv['tag_count_written']} written = {sv['tag_count_read_back']} read back, tracks {sv['tracks_read_back']}")
        print(f"SAMPLES {answer['samples']}  preset back-refs unresolved: {answer.get('preset_back_references', {}).get('unresolved')}")
        print("NEXT    open it in Live, restart the Extension Host if it stopped, then:\n"
              f"        python3 scripts/build_live_project.py --plan-path '{answer['plan']['plan_path']}' --set-manifest '{answer['manifest']}' --into-current-set --tracks ...")
        if args.open:
            subprocess.run(["open", answer["artifact"]], check=False)
    return 0 if answer.get("written") else 1


if __name__ == "__main__":
    sys.exit(main())
