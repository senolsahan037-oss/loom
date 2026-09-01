import json
import mido
from pathlib import Path

midi_path = Path("engine/output/agent_outputs/drums/full_drums.mid")
out_path = Path("engine/output/agent_outputs/drums/full_drums.notes.json")

mid = mido.MidiFile(midi_path)

ticks_per_beat = mid.ticks_per_beat
current_tick = 0
active = {}
notes = []
tempo = 500000

for msg in mid:
    current_tick += int(round(msg.time * ticks_per_beat)) if isinstance(msg.time, float) else msg.time

    if msg.type == "set_tempo":
        tempo = msg.tempo

    if msg.type == "note_on" and msg.velocity > 0:
        active[(msg.note, msg.channel)] = (current_tick, msg.velocity)

    if msg.type in ["note_off", "note_on"] and getattr(msg, "velocity", 0) == 0:
        key = (msg.note, msg.channel)
        if key in active:
            start_tick, velocity = active.pop(key)
            notes.append({
                "pitch": msg.note,
                "start": round(start_tick / ticks_per_beat, 4),
                "duration": round((current_tick - start_tick) / ticks_per_beat, 4),
                "velocity": velocity,
                "mute": False
            })

out = {
    "source": str(midi_path),
    "target_track": "DRUMS - Kick",
    "clip_name": "Sensei Full Drums",
    "length_beats": 32,
    "notes": notes
}

out_path.write_text(json.dumps(out, indent=2))
print(f"Saved {out_path}")
print(f"Notes: {len(notes)}")
