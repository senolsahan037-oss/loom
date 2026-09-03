#!/usr/bin/env python3
"""Chord movement, bass motion and melodic shape, counted from annotated songs.

Chords are stored as scale degrees relative to each song's own key, never as
absolute chords: "IV goes to V" is knowledge about pop music, while "F goes to G"
is a fact about one song in C. The degree form is also what a generator can use,
since it transposes to whatever key the user is working in.

Nothing is kept from which a song could be rebuilt -- no melody, no chord
sequence of any one piece, only how often one degree follows another across the
whole corpus.

Source: POP909, 909 annotated Chinese pop songs. That provenance is recorded and
matters: this is one tradition's pop, not pop everywhere.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

PITCH = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
         "E#": 5, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11, "Cb": 11}
MAJOR_DEGREES = ["I", "bII", "II", "bIII", "III", "IV", "bV", "V", "bVI", "VI", "bVII", "VII"]
MINOR_DEGREES = ["i", "bII", "ii", "III", "iii", "iv", "bV", "v", "VI", "vi", "VII", "vii"]
MIN_SONGS = 20


def read_key(path: Path) -> tuple[int, str] | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or ":" not in parts[2]:
            continue
        root, mode = parts[2].split(":", 1)
        if root in PITCH:
            return PITCH[root], mode.strip()
    return None


def read_chords(path: Path) -> list[tuple[float, float, str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[2] in ("N", ""):
            continue
        label = parts[2]
        root = label.split(":")[0].split("/")[0]
        quality = label.split(":")[1] if ":" in label else "maj"
        if root not in PITCH:
            continue
        out.append((float(parts[0]), float(parts[1]), root, quality))
    return out


def degree_of(root: str, quality: str, tonic: int, mode: str) -> str:
    step = (PITCH[root] - tonic) % 12
    table = MINOR_DEGREES if mode.startswith("min") else MAJOR_DEGREES
    name = table[step]
    if quality.startswith("min") and name.isupper():
        name = name.lower()
    elif quality.startswith("maj") and name.islower():
        name = name.upper()
    if "7" in quality:
        name += "7"
    return name


def melody_intervals(path: Path) -> list[int]:
    import mido
    midi = mido.MidiFile(str(path))
    pitches = []
    for track in midi.tracks:
        if not any(m.type == "track_name" and m.name == "MELODY" for m in track):
            continue
        for message in track:
            if message.type == "note_on" and message.velocity > 0:
                pitches.append(message.note)
    return [b - a for a, b in zip(pitches, pitches[1:])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/sources/POP909-Dataset-master/POP909")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    root = Path(args.root)
    songs = sorted(p for p in root.iterdir() if p.is_dir())
    if args.limit:
        songs = songs[:args.limit]

    transitions: collections.Counter = collections.Counter()
    degrees: collections.Counter = collections.Counter()
    intervals: collections.Counter = collections.Counter()
    used, skipped = 0, collections.Counter()

    for song in songs:
        key = read_key(song / "key_audio.txt") if (song / "key_audio.txt").exists() else None
        if key is None:
            skipped["no key"] += 1
            continue
        tonic, mode = key
        chords = read_chords(song / "chord_midi.txt") if (song / "chord_midi.txt").exists() else []
        if len(chords) < 4:
            skipped["too few chords"] += 1
            continue

        sequence = []
        for _, _, chord_root, quality in chords:
            name = degree_of(chord_root, quality, tonic, mode)
            if not sequence or sequence[-1] != name:
                sequence.append(name)
        for name in sequence:
            degrees[name] += 1
        for first, second in zip(sequence, sequence[1:]):
            transitions[(first, second)] += 1

        try:
            for step in melody_intervals(song / f"{song.name}.mid"):
                if abs(step) <= 24:
                    intervals[step] += 1
        except Exception:
            skipped["melody unreadable"] += 1
        used += 1
        if used % 150 == 0:
            print(f"  {used} song(s) read", flush=True)

    if used < MIN_SONGS:
        sys.exit(f"Only {used} songs were readable; not enough to count anything")

    total_moves = sum(transitions.values())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": "POP909 -- 909 annotated Chinese pop songs. One tradition's pop, "
                  "not pop everywhere; the provenance travels with the numbers.",
        "songs_used": used,
        "songs_skipped": dict(skipped),
        "chord_moves_counted": total_moves,
        "degree_share": {name: round(count / sum(degrees.values()), 4)
                         for name, count in degrees.most_common(20)},
        "top_transitions": [
            {"from": first, "to": second, "share": round(count / total_moves, 4),
             "count": count}
            for (first, second), count in transitions.most_common(30)
        ],
        "melody_interval_share": {str(step): round(count / sum(intervals.values()), 4)
                                  for step, count in intervals.most_common(15)},
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\n{used} songs, {total_moves} chord moves -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
