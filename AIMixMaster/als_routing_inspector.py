import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default="?"):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ALS ROUTING INSPECTOR ====")

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack"]:
        continue

    name = v(track.find("./Name/UserName"), track.tag)

    mixer = track.find("./DeviceChain/Mixer")

    audio_to = v(track.find(".//AudioOutputRouting/Target"), "")
    audio_from = v(track.find(".//AudioInputRouting/Target"), "")
    midi_to = v(track.find(".//MidiOutputRouting/Target"), "")
    midi_from = v(track.find(".//MidiInputRouting/Target"), "")

    monitor = v(track.find(".//MonitoringEnum"), "")
    solo = v(track.find("./DeviceChain/Mixer/SoloSink/Manual"), "")
    arm = v(track.find(".//Arm/Manual"), "")

    print(f"\n[{track.tag}] {name}")

    if audio_from:
        print(f"  Audio In : {audio_from}")
    if audio_to:
        print(f"  Audio Out: {audio_to}")

    if midi_from:
        print(f"  MIDI In  : {midi_from}")
    if midi_to:
        print(f"  MIDI Out : {midi_to}")

    if monitor:
        print(f"  Monitor  : {monitor}")
    if solo:
        print(f"  Solo     : {solo}")
    if arm:
        print(f"  Arm      : {arm}")

    if mixer is not None:
        sends = track.findall("./DeviceChain/Mixer/Sends/TrackSendHolder")
        if sends:
            vals = []
            for i, holder in enumerate(sends):
                manual = holder.find("./Send/Manual")
                if manual is not None:
                    vals.append(f"Send {chr(65+i)}={v(manual)}")
            if vals:
                print("  Sends    : " + " | ".join(vals))
