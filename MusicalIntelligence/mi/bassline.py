#!/usr/bin/env python3
"""The lower layers of a song, counted: bass motion under the harmony.

POP909 has no separate bass track -- the accompaniment is one PIANO part. So the
bass is taken as the lowest sounding voice, read chord span by chord span, which
is how a bass line is actually heard against the harmony rather than as a
separate melody.

What is stored is motion, not notes: how far the bass moves between chords, how
often it sits on the chord root against a third or a fifth, how many times it
moves within one chord. A bass line cannot be rebuilt from that, but a generator
can be held to it.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mi.harmony import PITCH, read_chords, read_key, degree_of

MIN_SONGS = 20
# Every interval named, not five buckets and an "other". The first pass folded
# seconds, sixths and the tritone into one label that came out the largest
# category at 38% -- which said nothing except that the labels were too coarse.
CHORD_TONE = {0: "root", 1: "b9", 2: "9th", 3: "min3rd", 4: "maj3rd", 5: "4th",
              6: "tritone", 7: "5th", 8: "b6th", 9: "6th", 10: "b7th", 11: "maj7th"}


def _tempo_map(midi) -> list[tuple[int, int]]:
    """(tick, microseconds per beat), from wherever in the file they are written.

    Tempo lives in track 0 of nearly every file, so reading it only from the
    track being parsed left every song at the default 120 BPM. Times were then
    wrong by whatever the real tempo was, and notes landed under the wrong
    chords -- which showed up as the bass sitting on the root only 22% of the
    time, far below anything a pop bass actually does.
    """
    changes = []
    for track in midi.tracks:
        clock = 0
        for message in track:
            clock += message.time
            if message.type == "set_tempo":
                changes.append((clock, message.tempo))
    return sorted(changes) or [(0, 500000)]


def _seconds_at(tick: int, tempo_map: list[tuple[int, int]], ticks_per_beat: int) -> float:
    seconds, last_tick, tempo = 0.0, 0, tempo_map[0][1]
    for change_tick, change_tempo in tempo_map:
        if change_tick >= tick:
            break
        seconds += (change_tick - last_tick) * tempo / 1e6 / ticks_per_beat
        last_tick, tempo = change_tick, change_tempo
    return seconds + (tick - last_tick) * tempo / 1e6 / ticks_per_beat


def piano_notes(path: Path) -> list[tuple[float, int]]:
    """(seconds, pitch) for the accompaniment, in time order."""
    import mido

    midi = mido.MidiFile(str(path))
    ticks_per_beat = midi.ticks_per_beat or 480
    tempo_map = _tempo_map(midi)
    notes = []
    for track in midi.tracks:
        if not any(m.type == "track_name" and m.name == "PIANO" for m in track):
            continue
        clock = 0
        for message in track:
            clock += message.time
            if message.type == "note_on" and message.velocity > 0:
                notes.append((_seconds_at(clock, tempo_map, ticks_per_beat), message.note))
    return sorted(notes)


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

    motion: collections.Counter = collections.Counter()     # semitone step between chords
    against: collections.Counter = collections.Counter()    # bass note vs chord root
    per_chord: collections.Counter = collections.Counter()  # how many bass moves inside one chord
    by_degree: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    used, skipped = 0, collections.Counter()

    for song in songs:
        key = read_key(song / "key_audio.txt") if (song / "key_audio.txt").exists() else None
        chords = read_chords(song / "chord_midi.txt") if (song / "chord_midi.txt").exists() else []
        if key is None or len(chords) < 4:
            skipped["no key or chords"] += 1
            continue
        try:
            notes = piano_notes(song / f"{song.name}.mid")
        except Exception:
            skipped["midi unreadable"] += 1
            continue
        if not notes:
            skipped["no accompaniment track"] += 1
            continue
        tonic, mode = key

        previous_low = None
        for start, end, chord_root, quality in chords:
            span = [pitch for seconds, pitch in notes if start <= seconds < end]
            if not span:
                continue
            low = min(span)
            # Distinct low notes in the span: a walking bass moves inside a chord,
            # a held one does not, and the difference is the whole character.
            lows = sorted({pitch for seconds, pitch in notes
                           if start <= seconds < end and pitch <= low + 4})
            per_chord[min(len(lows), 6)] += 1

            interval = (low - PITCH[chord_root]) % 12
            tone = CHORD_TONE[interval]
            against[tone] += 1
            by_degree[degree_of(chord_root, quality, tonic, mode)][tone] += 1

            if previous_low is not None:
                step = low - previous_low
                if abs(step) <= 12:
                    motion[step] += 1
            previous_low = low
        used += 1
        if used % 200 == 0:
            print(f"  {used} song(s) read", flush=True)

    if used < MIN_SONGS:
        sys.exit(f"Only {used} songs were readable")

    moves = sum(motion.values())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": "POP909 -- lowest voice of the annotated accompaniment. One "
                  "tradition's pop; the provenance travels with the numbers.",
        "songs_used": used,
        "songs_skipped": dict(skipped),
        "chord_changes_counted": sum(against.values()),
        "bass_against_chord": {tone: round(count / sum(against.values()), 4)
                               for tone, count in against.most_common()},
        "motion_between_chords": {str(step): round(count / moves, 4)
                                  for step, count in motion.most_common(14)},
        "moves_within_one_chord": {str(n): round(c / sum(per_chord.values()), 4)
                                   for n, c in sorted(per_chord.items())},
        "bass_role_by_degree": {
            degree: {tone: round(count / sum(counter.values()), 3)
                     for tone, count in counter.most_common(3)}
            for degree, counter in sorted(by_degree.items(),
                                          key=lambda kv: -sum(kv[1].values()))[:10]
        },
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\n{used} songs, {sum(against.values())} chord spans -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
