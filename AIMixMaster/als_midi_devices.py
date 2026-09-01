import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ALS MIDI DEVICE INSPECTOR ====")

track_no = 0

for track in root.iter():
    if track.tag != "MidiTrack":
        continue

    track_no += 1
    name_node = track.find("./Name/UserName")
    track_name = name_node.attrib.get("Value") if name_node is not None and name_node.attrib.get("Value") else "(unnamed)"

    print(f"\n[T{track_no:02d}] {track_name}")

    names = []

    for tag in [
        "InstrumentGroupDevice",
        "DrumGroupDevice",
        "MidiToAudioDevice",
        "AudioToAudioDevice",
        "PluginDevice",
        "MxDevice",
        "ReWireDevice",
        "AudioEffectGroupDevice"
    ]:
        for dev in track.iter(tag):
            uname = dev.find("./UserName")
            ename = dev.find(".//Name/EffectiveName")

            if uname is not None and uname.attrib.get("Value"):
                names.append(f"{tag}: {uname.attrib['Value']}")

            if ename is not None and ename.attrib.get("Value"):
                names.append(f"{tag}: {ename.attrib['Value']}")

    if names:
        seen = set()
        for n in names:
            if n not in seen:
                seen.add(n)
                print("  -", n)
    else:
        print("  - device/preset bulunamadı")
