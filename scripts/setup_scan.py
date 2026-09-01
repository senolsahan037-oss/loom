#!/usr/bin/env python3
"""First-run setup: build Loom's catalogues from THIS machine's Ableton install.

Loom ships code and fixtures, never measurements. The catalogues it reasons
with are derived from the stock Ableton library on the machine it runs on, so
every user gets their own -- read from Live's own file index, which Ableton
keeps at ~/Library/Application Support/Ableton/Live Database/.

Nothing here needs a virtual environment or a package install: it runs on the
Python that ships with macOS.

Usage:
  setup_scan.py            build the catalogues
  setup_scan.py --check    report what is present and what is missing, write nothing
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSEI = ROOT / "Sensei"
DATA = SENSEI / "data"
sys.path.insert(0, str(SENSEI))

# (label, output directory, builder) -- ordered so the cheap, always-available
# catalogues run before the corpus, which parses real clip files and is slow.
# Steps that read and parse real clip files rather than just the index. They
# take minutes, so they are announced as such instead of looking frozen.
SLOW_NOTE = {
    "canonical midi corpus": "(parses clip files -- takes a few minutes)",
    "variation corpus": "(parses clip files -- takes a few minutes)",
}

STEPS = [
    ("bass instruments", DATA / "bass_instruments",
     lambda out: __import__("ableton.bass_instrument_catalog", fromlist=["x"]).write_bass_instrument_catalog(output_directory=out)),
    ("chord instruments", DATA / "chord_instruments",
     lambda out: __import__("ableton.chord_instrument_catalog", fromlist=["x"]).write_chord_instrument_catalog(output_directory=out)),
    ("groove catalog", DATA / "groove_corpus",
     lambda out: __import__("ableton.groove_corpus", fromlist=["x"]).write_groove_catalog(output_directory=out)),
    ("canonical midi corpus", DATA / "canonical_midi_corpus",
     lambda out: __import__("ableton.canonical_midi_corpus", fromlist=["x"]).write_canonical_midi_corpus(output_directory=out)),
    ("variation corpus", DATA / "variation_corpus",
     lambda out: __import__("ableton.variation_corpus", fromlist=["x"]).write_clean_variation_corpus(output_directory=out)),
]


def find_ableton() -> dict:
    from ableton import ableton_index_provider as index

    databases = index.find_live_databases()
    active = index.get_active_db()
    return {
        "databases_found": [str(path) for path in databases],
        "readable_index": str(active) if active else None,
    }


def check() -> int:
    found = find_ableton()
    print("Ableton file index")
    for path in found["databases_found"]:
        print("  found   ", path)
    if not found["databases_found"]:
        print("  none found. Is Ableton Live installed and has it indexed its library?")
    print("  readable:", found["readable_index"] or "NO -- the catalogues cannot be built")
    print()
    print("Catalogues")
    missing = 0
    for label, out, _ in STEPS:
        files = sorted(out.glob("*.jsonl")) if out.exists() else []
        if files:
            rows = sum(1 for _ in files[0].open(encoding="utf-8"))
            print("  present  %-24s %6d rows  %s" % (label, rows, files[0].name))
        else:
            missing += 1
            print("  MISSING  %-24s run this script without --check" % label)
    return 0 if not missing and found["readable_index"] else 1


def build() -> int:
    found = find_ableton()
    if not found["readable_index"]:
        print("No readable Ableton file index. Nothing was built.")
        for path in found["databases_found"]:
            print("  found but unreadable:", path)
        print("Install Ableton Live and open it once so it indexes the library, then re-run.")
        return 1

    print("Ableton index:", found["readable_index"])
    print("Building catalogues from this machine's own library.\n", flush=True)
    failures = []
    for index, (label, out, builder) in enumerate(STEPS, 1):
        out.mkdir(parents=True, exist_ok=True)
        # Announce before starting, not after. The corpus steps parse real clip
        # files and take minutes; without this the whole thing looks frozen.
        print("  ...  [%d/%d] %-24s %s" % (index, len(STEPS), label, SLOW_NOTE.get(label, "")), flush=True)
        started = time.time()
        try:
            result = builder(out)
            count = result.get("entry_count") if isinstance(result, dict) else None
            print("  ok   [%d/%d] %-24s %6.1fs  %s entries"
                  % (index, len(STEPS), label, time.time() - started, count if count is not None else "?"), flush=True)
        except Exception as error:  # noqa: BLE001
            failures.append((label, error))
            print("  FAIL [%d/%d] %-24s %6.1fs  %s: %s"
                  % (index, len(STEPS), label, time.time() - started, type(error).__name__, str(error)[:70]), flush=True)

    print()
    if failures:
        print("%d of %d catalogues failed. The tools that depend on them will say so rather than guess."
              % (len(failures), len(STEPS)))
        return 1
    print("All catalogues built. Loom now reasons from your own Ableton library.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report state, write nothing")
    args = parser.parse_args()
    return check() if args.check else build()


if __name__ == "__main__":
    sys.exit(main())
