#!/usr/bin/env python3
"""Measure a folder of recordings into one evidence file.

Resumable: a work already measured is skipped, so a long scan can be stopped and
picked up. Nothing about the audio itself is stored -- only the shape.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mi.measure import measure, as_dict

AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".aif", ".aiff"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--out", required=True)
    parser.add_argument("--tag", default="", help="corpus label, e.g. 'arabesk-tr'")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    out = Path(args.out)
    done: dict[str, dict] = {}
    if out.exists():
        done = {row["source"]: row for row in json.loads(out.read_text(encoding="utf-8"))["works"]}

    files = sorted(p for p in Path(args.root).expanduser().rglob("*")
                   if p.suffix.lower() in AUDIO)
    if args.limit:
        files = files[:args.limit]
    todo = [p for p in files if p.name not in done]
    print(f"{len(files)} file(s), {len(done)} already measured, {len(todo)} to go\n", flush=True)

    failed = []
    for index, path in enumerate(todo, 1):
        started = time.time()
        try:
            row = as_dict(measure(path))
        except Exception as error:  # a corrupt file should not end the scan
            print(f"  [{index}/{len(todo)}] FAILED {path.name}: {type(error).__name__}")
            failed.append(path.name)
            continue
        row["corpus"] = args.tag
        done[path.name] = row
        print(f"  [{index}/{len(todo)}] {path.name[:44]:46} {row['tempo_bpm']:6} bpm  "
              f"({time.time()-started:.1f}s)", flush=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"works": list(done.values())}, indent=2,
                                  ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n{len(done)} work(s) measured into {out}")
    if failed:
        print(f"{len(failed)} unreadable: {failed[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
