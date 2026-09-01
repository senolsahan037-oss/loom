import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ALS MIDI CLIP INSPECTOR ====")

track_no = 0

for track in root.iter():
    if track.tag != "MidiTrack":
        continue

    track_no += 1
    name_node = track.find("./Name/UserName")
    track_name = name_node.attrib.get("Value") if name_node is not None and name_node.attrib.get("Value") else "(unnamed)"

    clips = list(track.iter("MidiClip"))
    if not clips:
        continue

    print(f"\n[T{track_no:02d}] {track_name}")

    for i, clip in enumerate(clips, 1):
        s = clip.find("./CurrentStart")
        e = clip.find("./CurrentEnd")
        start = float(s.attrib.get("Value", "0")) if s is not None else 0
        end = float(e.attrib.get("Value", "0")) if e is not None else 0

        disabled = clip.find("./Disabled")
        state = "MUTED" if disabled is not None and disabled.attrib.get("Value") == "true" else "ON"

        notes = []

        for keytrack in clip.findall(".//KeyTrack"):
            midi_key_node = keytrack.find("./MidiKey")
            if midi_key_node is None:
                continue

            midi_key = int(float(midi_key_node.attrib.get("Value", "0")))

            for note in keytrack.findall("./Notes/MidiNoteEvent"):
                time = float(note.attrib.get("Time", "0"))
                dur = float(note.attrib.get("Duration", "0"))
                vel = float(note.attrib.get("Velocity", "0"))
                notes.append((midi_key, time, dur, vel))

        if notes:
            pitches = [n[0] for n in notes]
            velocities = [n[3] for n in notes]
            print(
                f"  C{i:03d} | {state:5} | {start:7.2f}–{end:7.2f} "
                f"| Notes:{len(notes):3d} | MidiKey:{min(pitches)}–{max(pitches)} "
                f"| AvgVel:{sum(velocities)/len(velocities):.1f}"
            )
        else:
            print(f"  C{i:03d} | {state:5} | {start:7.2f}–{end:7.2f} | Notes:0")
