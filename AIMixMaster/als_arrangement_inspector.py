import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ARRANGEMENT INSPECTOR ====")

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack"]:
        continue

    name_node = track.find("./Name/UserName")
    track_name = name_node.attrib.get("Value") if name_node is not None and name_node.attrib.get("Value") else "(unnamed)"

    clips = []

    for clip_tag in ["AudioClip", "MidiClip"]:
        for clip in track.iter(clip_tag):
            clip_name_node = clip.find("./Name")
            clip_name = clip_name_node.attrib.get("Value") if clip_name_node is not None and clip_name_node.attrib.get("Value") else clip_tag

            current_start = clip.find("./CurrentStart")
            current_end = clip.find("./CurrentEnd")
            loop_start = clip.find("./Loop/LoopStart")
            loop_end = clip.find("./Loop/LoopEnd")

            start = current_start.attrib.get("Value") if current_start is not None else "?"
            end = current_end.attrib.get("Value") if current_end is not None else "?"
            ls = loop_start.attrib.get("Value") if loop_start is not None else "?"
            le = loop_end.attrib.get("Value") if loop_end is not None else "?"

            disabled = clip.find("./Disabled")
            clip_state = "MUTED" if disabled is not None and disabled.attrib.get("Value") == "true" else "ON"
            clips.append((clip_tag, clip_state, clip_name, start, end, ls, le))

    if clips:
        print(f"\n[{track.tag}] {track_name}")
        for ctype, state, cname, start, end, ls, le in clips[:30]:
            print(f"  {ctype:9} | {state:5} | {cname} | Start:{start} End:{end} Loop:{ls}-{le}")

        if len(clips) > 30:
            print(f"  ... +{len(clips)-30} more clips")
