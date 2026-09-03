#!/usr/bin/env python3
"""Extract drum patterns per genre from performed MIDI.

This is the layer the chart work could not reach. A tempo is not a pattern; what
a producer needs is where the kick lands against the snare, how the hats are
subdivided, how hard each lands -- and that only exists where there are notes.

The source is the Groove MIDI Dataset: human drummers, one file per take, each
labelled with a style and a tempo, openly licensed. Nothing here is copied out;
what is stored is where onsets fall on the bar, aggregated over a style, which
no single performance can be reconstructed from.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

GRID = 16          # sixteenth notes in a 4/4 bar
MIN_TAKES = 8      # a style with fewer performances than this says nothing

# General MIDI percussion, grouped into the parts a producer actually reasons
# about. Numbers a kit maps to the same voice are folded together.
ROLES = {
    "kick": {35, 36},
    "snare": {38, 40},
    "rimshot": {37},
    "clap": {39},
    "hat_closed": {42, 44},
    "hat_open": {46},
    "tom": {41, 43, 45, 47, 48, 50},
    "ride": {51, 53, 59},
    "crash": {49, 52, 55, 57},
}
ROLE_OF = {note: role for role, notes in ROLES.items() for note in notes}


def onsets(path: Path) -> list[tuple[str, int, int]]:
    """(role, position in the bar out of 16, velocity) for every drum hit."""
    import mido

    midi = mido.MidiFile(str(path))
    ticks = midi.ticks_per_beat or 480
    hits = []
    for track in midi.tracks:
        clock = 0
        for message in track:
            clock += message.time
            if message.type != "note_on" or message.velocity == 0:
                continue
            role = ROLE_OF.get(message.note)
            if role is None:
                continue
            beat = clock / ticks
            position = int(round(beat * (GRID / 4))) % GRID
            hits.append((role, position, message.velocity))
    return hits


def profile(rows: list[dict], root: Path) -> dict | None:
    """One style's pattern: how often each part lands on each sixteenth."""
    counts = {role: [0] * GRID for role in ROLES}
    velocity = {role: [] for role in ROLES}
    bars = 0
    used = 0
    for row in rows:
        path = root / row["midi_filename"]
        if not path.exists():
            continue
        try:
            hits = onsets(path)
        except Exception:
            continue
        if not hits:
            continue
        used += 1
        # One take covers many bars; the bar count is what turns raw counts into
        # a per-bar probability instead of "this drummer played for longer".
        bars += max(1, round(float(row["duration"]) * float(row["bpm"]) / 240))
        for role, position, level in hits:
            counts[role][position] += 1
            velocity[role].append(level)

    if used < MIN_TAKES or bars == 0:
        return None
    return {
        "takes": used,
        "bars": bars,
        "grid": GRID,
        "parts": {
            role: {
                "per_bar": round(sum(counts[role]) / bars, 2),
                "positions": [round(counts[role][i] / bars, 3) for i in range(GRID)],
                "mean_velocity": round(sum(velocity[role]) / len(velocity[role]))
                if velocity[role] else None,
            }
            for role in ROLES if sum(counts[role])
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/sources/groove")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    rows = list(csv.DictReader((root / "info.csv").open(encoding="utf-8")))
    # 4/4 only: a pattern averaged across metres is a pattern in no metre.
    rows = [row for row in rows if row["time_signature"] == "4-4"]

    by_style: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_style[row["style"].split("/")[0]].append(row)

    styles, thin = {}, {}
    for style, group in sorted(by_style.items()):
        result = profile(group, root)
        if result is None:
            thin[style] = len(group)
            print(f"  {style:14} too few takes ({len(group)}), no profile")
            continue
        styles[style] = result
        print(f"  {style:14} {result['takes']:4} takes, {result['bars']:5} bars, "
              f"kick {result['parts']['kick']['per_bar']}/bar", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": "Groove MIDI Dataset (Magenta), 4/4 takes only",
        "min_takes": MIN_TAKES,
        "styles_without_enough_data": thin,
        "styles": styles,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(styles)} style(s) profiled -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
