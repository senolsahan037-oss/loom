#!/usr/bin/env python3
"""One record per song: the lower layers, complete, in transposable form.

The aggregate files answer "what does pop do"; this one answers "how is a song
put together" -- the chord sequence with its lengths in beats, the bass note
under each chord, and where the harmony repeats. That is the shape a generator
needs to build a whole arrangement rather than a four-bar loop.

Everything is relative: chords as scale degrees, bass as an interval above the
chord root, lengths in beats. Nothing absolute is kept, so a record transposes to
any key and no audio or melody is stored. The chord annotations themselves come
from POP909, which publishes them openly.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mi.harmony import PITCH, read_chords, read_key, degree_of
from mi.bassline import piano_notes, CHORD_TONE


def beats_of(path: Path) -> list[float]:
    """Beat positions in seconds, from the dataset's own annotation.

    The file lists eighth-note positions and marks the real beats in its second
    column. Taking every row counted eighths as beats, which halved every
    duration and produced chord lengths like 1.33 beats -- a number no one
    plays.
    """
    if not path.exists():
        return []
    times = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.replace("\t", " ").split()
        if len(parts) < 2:
            continue
        try:
            if float(parts[1]) == 1.0:
                times.append(float(parts[0]))
        except ValueError:
            continue
    return times


def to_beats(seconds: float, beats: list[float]) -> float:
    """Seconds to beats, using the song's measured beat grid, not a fixed tempo."""
    if not beats:
        return round(seconds, 3)
    if seconds <= beats[0]:
        return 0.0
    for index in range(1, len(beats)):
        if seconds < beats[index]:
            span = beats[index] - beats[index - 1]
            return round(index - 1 + (seconds - beats[index - 1]) / span, 3) if span else float(index - 1)
    return float(len(beats) - 1)


def build(song: Path) -> dict | None:
    key = read_key(song / "key_audio.txt") if (song / "key_audio.txt").exists() else None
    chords = read_chords(song / "chord_midi.txt") if (song / "chord_midi.txt").exists() else []
    if key is None or len(chords) < 4:
        return None
    tonic, mode = key
    beats = beats_of(song / "beat_midi.txt")
    try:
        notes = piano_notes(song / f"{song.name}.mid")
    except Exception:
        notes = []

    sequence = []
    for start, end, root, quality in chords:
        degree = degree_of(root, quality, tonic, mode)
        span = [pitch for seconds, pitch in notes if start <= seconds < end]
        bass = None
        if span:
            bass = CHORD_TONE[(min(span) - PITCH[root]) % 12]
        start_beat, end_beat = to_beats(start, beats), to_beats(end, beats)
        if sequence and sequence[-1]["degree"] == degree and sequence[-1]["bass"] == bass:
            sequence[-1]["beats"] = round(end_beat - sequence[-1]["start_beat"], 2)
            continue
        sequence.append({"degree": degree, "bass": bass,
                         "start_beat": start_beat,
                         "beats": round(end_beat - start_beat, 2)})
    if len(sequence) < 4:
        return None

    # Where the progression starts over: the repeat length is the phrase.
    window = [item["degree"] for item in sequence]
    loop = None
    for size in (2, 4, 8, 16):
        if len(window) >= size * 2 and window[:size] == window[size:size * 2]:
            loop = size
            break
    return {
        "song": song.name,
        "mode": "minor" if mode.startswith("min") else "major",
        "chords": len(sequence),
        "loop_length": loop,
        "total_beats": round(sequence[-1]["start_beat"] + sequence[-1]["beats"], 1),
        "sequence": sequence,
    }


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

    maps, skipped = [], 0
    for index, song in enumerate(songs, 1):
        record = build(song)
        if record is None:
            skipped += 1
            continue
        maps.append(record)
        if index % 200 == 0:
            print(f"  {index} song(s) read", flush=True)

    loops = collections.Counter(m["loop_length"] for m in maps if m["loop_length"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": "POP909 chord and beat annotations, expressed as scale degrees "
                  "and beats. Transposable; nothing absolute is stored.",
        "songs": len(maps),
        "songs_skipped": skipped,
        "mode_share": dict(collections.Counter(m["mode"] for m in maps)),
        "loop_length_share": {str(k): round(v / len(maps), 3) for k, v in loops.most_common()},
        "median_chords_per_song": sorted(m["chords"] for m in maps)[len(maps) // 2],
        "maps": maps,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(maps)} song maps -> {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
