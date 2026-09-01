import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ALS CLIP INDEX ====")

track_no = 0
global_clip_no = 0

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack"]:
        continue

    track_no += 1

    name_node = track.find("./Name/UserName")
    track_name = name_node.attrib.get("Value") if name_node is not None and name_node.attrib.get("Value") else "(unnamed)"

    clips = []

    for clip_tag in ["AudioClip", "MidiClip"]:
        for clip in track.iter(clip_tag):
            s = clip.find("./CurrentStart")
            e = clip.find("./CurrentEnd")
            if s is None or e is None:
                continue

            start = float(s.attrib.get("Value", "0"))
            end = float(e.attrib.get("Value", "0"))

            disabled = clip.find("./Disabled")
            state = "MUTED" if disabled is not None and disabled.attrib.get("Value") == "true" else "ON"

            clip_name_node = clip.find("./Name")
            clip_name = clip_name_node.attrib.get("Value") if clip_name_node is not None and clip_name_node.attrib.get("Value") else "-"

            clips.append((start, end, clip_tag, state, clip_name))

    if not clips:
        continue

    clips.sort(key=lambda x: (x[0], x[1]))

    print(f"\n[T{track_no:02d}] {track_name} ({track.tag})")

    local_clip_no = 0
    for start, end, clip_tag, state, clip_name in clips:
        local_clip_no += 1
        global_clip_no += 1

        clip_type = "AUDIO" if clip_tag == "AudioClip" else "MIDI"
        clip_id = f"T{track_no:02d}-C{local_clip_no:03d}"

        print(f"  {clip_id} | {state:5} | {clip_type:5} | {start:7.2f}–{end:7.2f} | {clip_name}")

print(f"\nTotal indexed clips: {global_clip_no}")
