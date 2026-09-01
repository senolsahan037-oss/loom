import gzip
import sys
import xml.etree.ElementTree as ET
from collections import Counter

if len(sys.argv) < 2:
    print("Usage: python als_inspector.py project.als")
    sys.exit(1)

als_path = sys.argv[1]

with gzip.open(als_path, "rb") as f:
    root = ET.parse(f).getroot()

print("ALS:", als_path)
print("Creator:", root.attrib.get("Creator"))

track_tags = ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack", "PreHearTrack"]
counts = Counter()

for elem in root.iter():
    if elem.tag in track_tags:
        counts[elem.tag] += 1

print("\nTRACK COUNTS")
for tag in track_tags:
    print(f"{tag}: {counts[tag]}")

print("\nNON-EMPTY USER NAMES")
names = []
for elem in root.iter("UserName"):
    val = elem.attrib.get("Value", "").strip()
    if val and val not in names:
        names.append(val)

for name in names[:120]:
    print("-", name)

print("\nPROJECT TEMPO")
tempos = []
for elem in root.iter("Tempo"):
    val = elem.attrib.get("Value")
    if val:
        tempos.append(val)

if tempos:
    print("Tempo:", tempos[-1])
else:
    print("Tempo: not found")

print("\nTRACK ON / MUTE STATUS")
track_tags_status = ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"]

for track in root.iter():
    if track.tag not in track_tags_status:
        continue

    uname = track.find("./Name/UserName")
    name = uname.attrib.get("Value") if uname is not None else ""
    if not name:
        name = "(unnamed)"

    on = track.find("./DeviceChain/Mixer/On/Manual")
    val = on.attrib.get("Value") if on is not None else None

    state = "ON" if val == "true" else "MUTED" if val == "false" else "UNKNOWN"
    print(f"{track.tag:12} | {state:7} | {name}")

print("\nTRACKS WITH DEVICE / PRESET NAMES")
track_tags_status = ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"]

for track in root.iter():
    if track.tag not in track_tags_status:
        continue

    uname = track.find("./Name/UserName")
    name = uname.attrib.get("Value") if uname is not None else ""
    if not name:
        name = "(unnamed)"

    on = track.find("./DeviceChain/Mixer/On/Manual")
    val = on.attrib.get("Value") if on is not None else None
    state = "ON" if val == "true" else "MUTED" if val == "false" else "UNKNOWN"

    devices = []
    for elem in track.iter("UserName"):
        v = elem.attrib.get("Value", "").strip()
        if v and v != name and v not in devices:
            devices.append(v)

    device_text = ", ".join(devices[:8]) if devices else "-"
    print(f"{track.tag:12} | {state:7} | {name} | {device_text}")

print("\nTRACK PRESET CHAINS")
for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"]:
        continue

    uname = track.find("./Name/UserName")
    track_name = uname.attrib.get("Value") if uname is not None else ""
    if not track_name:
        track_name = "(unnamed)"

    chains = []
    for dev in track.iter("AudioEffectGroupDevice"):
        preset = dev.find("./UserName")
        preset_name = preset.attrib.get("Value") if preset is not None else ""

        effective = dev.find(".//AudioEffectBranch/Name/EffectiveName")
        effective_name = effective.attrib.get("Value") if effective is not None else ""

        if preset_name or effective_name:
            chains.append(f"{preset_name} => {effective_name}")

    if chains:
        print(f"\n{track_name}")
        for c in chains:
            print(f"  - {c}")

print("\nTRACK MIXER VALUES")
for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"]:
        continue

    uname = track.find("./Name/UserName")
    track_name = uname.attrib.get("Value") if uname is not None else ""
    if not track_name:
        track_name = "(unnamed)"

    volume = track.find("./DeviceChain/Mixer/Volume/Manual")
    pan = track.find("./DeviceChain/Mixer/Pan/Manual")

    import math

    volume_val = volume.attrib.get("Value") if volume is not None else "?"
    pan_val = pan.attrib.get("Value") if pan is not None else "?"

    try:
        v = float(volume_val)
        db = 20 * math.log10(v) if v > 0 else float("-inf")
        vol_text = f"{v:.6f} ({db:.1f} dB)"
    except:
        vol_text = str(volume_val)

    print(f"{track.tag:12} | {track_name:28} | Volume: {vol_text:22} | Pan: {pan_val}")

print("\nTRACK SEND VALUES")
import math

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack"]:
        continue

    uname = track.find("./Name/UserName")
    track_name = uname.attrib.get("Value") if uname is not None and uname.attrib.get("Value") else "(unnamed)"

    send_values = []
    holders = track.findall("./DeviceChain/Mixer/Sends/TrackSendHolder")

    for i, holder in enumerate(holders):
        manual = holder.find("./Send/Manual")
        if manual is None:
            continue

        raw = float(manual.attrib.get("Value", "0"))
        db = 20 * math.log10(raw) if raw > 0 else float("-inf")
        send_name = chr(65 + i)
        send_values.append(f"Send {send_name}: {raw:.6f} ({db:.1f} dB)")

    if send_values:
        print(f"{track_name:30} | " + " | ".join(send_values))

print("\n\n================ MIXER SNAPSHOT ================")

import math

def db_text(raw):
    try:
        v = float(raw)
        db = 20 * math.log10(v) if v > 0 else float("-inf")
        return f"{db:.1f} dB"
    except:
        return "?"

def pan_text(raw):
    try:
        p = float(raw)
        if abs(p) < 0.001:
            return "Center"
        return f"{p:.3f} ({'Right' if p > 0 else 'Left'})"
    except:
        return "?"

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"]:
        continue

    uname = track.find("./Name/UserName")
    name = uname.attrib.get("Value") if uname is not None and uname.attrib.get("Value") else "(unnamed)"

    on = track.find("./DeviceChain/Mixer/On/Manual")
    on_val = on.attrib.get("Value") if on is not None else None
    status = "ON" if on_val == "true" else "MUTED" if on_val == "false" else "UNKNOWN"

    volume = track.find("./DeviceChain/Mixer/Volume/Manual")
    pan = track.find("./DeviceChain/Mixer/Pan/Manual")

    volume_raw = volume.attrib.get("Value") if volume is not None else "?"
    pan_raw = pan.attrib.get("Value") if pan is not None else "?"

    sends = []
    holders = track.findall("./DeviceChain/Mixer/Sends/TrackSendHolder")
    for i, holder in enumerate(holders):
        manual = holder.find("./Send/Manual")
        if manual is not None:
            raw = manual.attrib.get("Value", "0")
            sends.append(f"Send {chr(65+i)}: {db_text(raw)}")

    preset_lines = []
    for dev in track.iter("AudioEffectGroupDevice"):
        preset = dev.find("./UserName")
        preset_name = preset.attrib.get("Value") if preset is not None else ""

        effective = dev.find(".//AudioEffectBranch/Name/EffectiveName")
        chain = effective.attrib.get("Value") if effective is not None else ""

        if preset_name or chain:
            preset_lines.append((preset_name or "-", chain or "-"))

    print(f"\n[{track.tag}] {name}")
    print(f"  Status : {status}")
    print(f"  Volume : {db_text(volume_raw)}")
    print(f"  Pan    : {pan_text(pan_raw)}")
    print(f"  Sends  : {' | '.join(sends) if sends else '-'}")

    if preset_lines:
        for preset_name, chain in preset_lines:
            print(f"  Preset : {preset_name}")
            print(f"  Chain  : {chain}")
    else:
        print("  Preset : -")
        print("  Chain  : -")
