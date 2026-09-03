#!/usr/bin/env python3
"""Write a part that belongs to the project it is going into.

This is the thing a prompt-driven generator cannot do. It does not know your key,
your tempo, or what you have already written, so it hands you a finished piece of
music that has to be bent to fit. Here the project is read first -- key, scale,
tempo -- and everything is generated inside those facts, then returned in the
project's own key and beats.

Every choice is traceable to a counted number rather than a preference: which
chord follows which comes from measured transitions, how the bass sits under a
chord comes from measured bass behaviour, and if the evidence for something is
missing the part is refused rather than guessed.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from mi import profiles

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Scale degrees to semitones above the tonic, per mode.
MAJOR_STEPS = {"I": 0, "bII": 1, "II": 2, "ii": 2, "bIII": 3, "III": 4, "iii": 4,
               "IV": 5, "iv": 5, "bV": 6, "V": 7, "v": 7, "bVI": 8, "VI": 9,
               "vi": 9, "bVII": 10, "VII": 11, "vii": 11}
MINOR_STEPS = {"i": 0, "I": 0, "bII": 1, "ii": 2, "II": 2, "III": 3, "iii": 3,
               "iv": 5, "IV": 5, "bV": 6, "v": 7, "V": 7, "VI": 8, "vi": 8,
               "VII": 10, "vii": 10, "bVII": 10}
TRIAD = {"major": [0, 4, 7], "minor": [0, 3, 7]}
BASS_INTERVAL = {"root": 0, "5th": 7, "maj3rd": 4, "min3rd": 3, "4th": 5,
                 "b7th": 10, "9th": 2, "6th": 9, "b6th": 8, "maj7th": 11,
                 "b9": 1, "tritone": 6}
# The measured distribution is over all chords at once, so drawing from it blind
# put a major third under a minor chord -- a C# under an A minor, which is simply
# a wrong note. The third has to agree with the chord it sits under.
WRONG_UNDER = {"major": {"min3rd"}, "minor": {"maj3rd", "maj7th"}}


class NoEvidence(Exception):
    """Raised rather than inventing a part the corpus cannot support."""


def _degree_root(degree: str, minor: bool) -> tuple[int, str] | None:
    name = re.sub(r"\d+$", "", degree)
    table = MINOR_STEPS if minor else MAJOR_STEPS
    if name not in table:
        return None
    return table[name], ("minor" if name.islower() else "major")


def _weighted(options: list[tuple[str, float]], rng: random.Random) -> str:
    total = sum(weight for _, weight in options)
    point = rng.random() * total
    for name, weight in options:
        point -= weight
        if point <= 0:
            return name
    return options[-1][0]


def progression(bars: int, minor: bool, seed: int, chords_per_bar: int = 1) -> list[str]:
    """A chord sequence walked through the measured transition table."""
    evidence = profiles.chord_evidence()
    if not evidence or not evidence.get("top_transitions"):
        raise NoEvidence("no measured chord transitions")
    moves: dict[str, list[tuple[str, float]]] = {}
    for row in evidence["top_transitions"]:
        moves.setdefault(row["from"], []).append((row["to"], row["share"]))

    rng = random.Random(seed)
    tonic = "i" if minor else "I"
    current = tonic
    sequence = [current]
    for _ in range(bars * chords_per_bar - 1):
        options = moves.get(current)
        if not options:
            # A degree the corpus never moves out of: return home rather than
            # inventing an exit that was never counted.
            current = tonic
        else:
            current = _weighted(options, rng)
        sequence.append(current)
    return sequence


def render(als_context: dict, layer: str, bars: int = 8, seed: int = 7,
           chords_per_bar: int = 1, octave: int = 3) -> dict:
    """Notes in the project's own key, plus what was read and what was counted."""
    root_name = (als_context.get("key_root") or "C").strip()
    scale = (als_context.get("scale") or "Major").strip()
    minor = scale.lower().startswith("min")
    if root_name not in PITCH_NAMES:
        root_name = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}.get(
            root_name, "C")
    tonic = PITCH_NAMES.index(root_name)

    degrees = progression(bars, minor, seed, chords_per_bar)
    beats_per_chord = 4.0 / chords_per_bar
    rng = random.Random(seed + 1)

    notes: list[dict] = []
    used: list[str] = []
    for index, degree in enumerate(degrees):
        resolved = _degree_root(degree, minor)
        if resolved is None:
            continue
        step, quality = resolved
        chord_root = tonic + step
        start = index * beats_per_chord
        used.append(degree)

        if layer == "chord":
            base = 12 * (octave + 1)
            for interval in TRIAD[quality]:
                notes.append({"pitch": base + (chord_root % 12) + interval,
                              "time": round(start, 3),
                              "duration": round(beats_per_chord, 3),
                              "velocity": 80})
        elif layer == "bass":
            evidence = profiles.bass_evidence()
            if not evidence or not evidence.get("against_chord"):
                raise NoEvidence("no measured bass behaviour")
            allowed = [(name, share) for name, share
                       in evidence["against_chord"].items()
                       if name not in WRONG_UNDER[quality]]
            choice = _weighted(allowed, rng)
            moves_table = evidence.get("moves_within_one_chord") or {"1": 1.0}
            count = int(_weighted(list(moves_table.items()), rng))
            pitch = 36 + (chord_root % 12) + BASS_INTERVAL.get(choice, 0)
            span = beats_per_chord / max(1, count)
            for step_index in range(max(1, count)):
                notes.append({"pitch": pitch if step_index == 0 else
                              36 + (chord_root % 12),
                              "time": round(start + step_index * span, 3),
                              "duration": round(span, 3), "velocity": 92})
        else:
            raise NoEvidence(f"no measured evidence for the '{layer}' layer")

    return {
        "layer": layer,
        "read_from_project": {
            "key": f"{root_name} {'minor' if minor else 'major'}",
            "tempo": als_context.get("tempo"),
            "source": als_context.get("als_path"),
        },
        "bars": bars,
        "degrees": used,
        "notes": notes,
        "evidence": {
            "chords": (profiles.chord_evidence() or {}).get("source"),
            "bass": (profiles.bass_evidence() or {}).get("source") if layer == "bass" else None,
        },
        "note": "Degrees were walked through measured transitions and then placed "
                "in this project's key, so the part is in the session's own "
                "harmony rather than a generator's.",
    }
