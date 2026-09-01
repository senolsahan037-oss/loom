import gzip, sys, re
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

tracks = []
id_to_name = {}

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack"]:
        continue

    tid = track.attrib.get("Id", "?")
    name = v(track.find("./Name/UserName"), "").strip()
    if not name:
        name = f"({track.tag})"

    id_to_name[tid] = name
    tracks.append((tid, track.tag, name, track))

print("==== SIGNAL FLOW ====")

for tid, ttype, name, track in tracks:
    out = v(track.find(".//AudioOutputRouting/Target"), "")
    midi_out = v(track.find(".//MidiOutputRouting/Target"), "")

    target = out or midi_out or "?"

    readable = target

    m = re.search(r"Track\.(\d+)", target)
    if m:
        ref_id = m.group(1)
        readable = id_to_name.get(ref_id, f"Track {ref_id}")

    readable = readable.replace("AudioOut/Main", "Master")
    readable = readable.replace("AudioOut/GroupTrack", "Group Bus")
    readable = readable.replace("AudioOut/None", "None")

    print(f"[{ttype}] {name}  →  {readable}")
