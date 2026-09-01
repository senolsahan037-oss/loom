"""Minimal MIDI reader used by Ableton inspectors."""

from pathlib import Path
from typing import Dict, List

try:
    import mido
except ImportError:  # pragma: no cover
    mido = None


def read_midi_events(path: str | Path) -> List[Dict]:
    if mido is None:
        raise ImportError("mido is required to parse MIDI files")
    midi = mido.MidiFile(Path(path))
    ticks_per_beat = midi.ticks_per_beat or 480
    events: List[Dict] = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "note_on" and message.velocity > 0:
                events.append({"note": int(message.note), "velocity": int(message.velocity), "beat": float(tick / ticks_per_beat), "track": track_index})
    return events
