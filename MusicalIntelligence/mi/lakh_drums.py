#!/usr/bin/env python3
"""Drum patterns for genres the performance corpus never covered.

Groove MIDI has seven dance takes, below the threshold, so house and techno came
back empty -- and those are not small genres to be missing. These files are
sequenced arrangements rather than recorded performances, which is itself the
right source here: this music is programmed, so a programmed transcription is
closer to the thing than a drummer playing it would be.

Drums are channel 10 in General MIDI. Only files whose drum track is dense
enough to be a real part are used; a two-hit channel is somebody's afterthought.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mi.drums import GRID, ROLES, ROLE_OF

DRUM_CHANNEL = 9          # MIDI channel 10, zero-based
MIN_FILES = 8
MIN_HITS_PER_FILE = 32    # below this the drum track is decorative, not a part


def file_onsets(path: Path) -> tuple[list[tuple[str, int, int]], float]:
    """(role, sixteenth of the bar, velocity) plus how many bars it covers."""
    import mido

    midi = mido.MidiFile(str(path))
    ticks = midi.ticks_per_beat or 480
    hits, last_beat = [], 0.0
    for track in midi.tracks:
        clock = 0
        for message in track:
            clock += message.time
            if message.type != "note_on" or message.velocity == 0:
                continue
            if getattr(message, "channel", None) != DRUM_CHANNEL:
                continue
            role = ROLE_OF.get(message.note)
            if role is None:
                continue
            beat = clock / ticks
            last_beat = max(last_beat, beat)
            hits.append((role, int(round(beat * (GRID / 4))) % GRID, message.velocity))
    return hits, max(1.0, last_beat / 4.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", default="data/corpus/dance_matches.json")
    parser.add_argument("--lakh", default="data/sources/clean_midi")
    parser.add_argument("--label", default="dance")
    parser.add_argument("--out", default="data/corpus/drum_patterns.json")
    args = parser.parse_args()

    matches = json.loads(Path(args.matches).read_text(encoding="utf-8"))["matches"]
    root = Path(args.lakh)

    counts = {role: [0] * GRID for role in ROLES}
    velocity = {role: [] for role in ROLES}
    bars, used, thin = 0.0, 0, 0
    for match in matches:
        path = root / match["midi"]
        if not path.exists():
            continue
        try:
            hits, span = file_onsets(path)
        except Exception:
            continue
        if len(hits) < MIN_HITS_PER_FILE:
            thin += 1
            continue
        used += 1
        bars += span
        for role, position, level in hits:
            counts[role][position] += 1
            velocity[role].append(level)

    if used < MIN_FILES:
        sys.exit(f"Only {used} usable files; not enough to profile '{args.label}'")

    profile = {
        "takes": used,
        "bars": int(bars),
        "grid": GRID,
        "provenance": "sequenced arrangements matched to national dance charts",
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

    out = Path(args.out)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"styles": {}}
    data.setdefault("styles", {})[args.label] = profile
    data.setdefault("extra_sources", {})[args.label] = (
        "Lakh clean_midi files matched to charted dance works; channel 10 only")
    data.get("styles_without_enough_data", {}).pop(args.label, None)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"'{args.label}': {used} file(s), {int(bars)} bars, "
          f"{thin} skipped as too thin -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
