import gzip
import math
import sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def get_val(node, default=None):
    if node is None:
        return default
    return node.attrib.get("Value", default)

def lin_to_db(v):
    try:
        v = float(v)
        if v <= 0:
            return "-inf"
        return f"{20 * math.log10(v):.2f} dB"
    except:
        return "?"

def send_text(v):
    try:
        raw = float(v)
        if raw <= 0.0004:
            return "OFF"
        db = 20 * math.log10(raw)
        return f"{db:.2f} dB"
    except:
        return "?"

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ROUTING & MIXER REPORT ====")

for track in root.iter():
    if track.tag not in (
        "AudioTrack",
        "MidiTrack",
        "GroupTrack",
        "ReturnTrack",
        "MainTrack",
    ):
        continue

    name = get_val(track.find("./Name/UserName"), "(unnamed)")
    mixer = track.find("./DeviceChain/Mixer")

    print(f"\n[{track.tag}] {name}")
    print("-" * 60)

    # Track On / Mute
    on = get_val(track.find("./DeviceChain/Mixer/On/Manual"), "true")
    print(f"Enabled   : {on}")

    # Volume
    vol = get_val(track.find("./DeviceChain/Mixer/Volume/Manual"), "1")
    print(f"Volume    : {lin_to_db(vol)}")

    # Pan
    pan = get_val(track.find("./DeviceChain/Mixer/Pan/Manual"), "0")
    try:
        p = float(pan)
        if abs(p) < 1e-4:
            pan_txt = "Center"
        elif p < 0:
            pan_txt = f"L {abs(p):.3f}"
        else:
            pan_txt = f"R {p:.3f}"
    except:
        pan_txt = "?"
    print(f"Pan       : {pan_txt}")

    # Monitor
    monitor = get_val(track.find(".//MonitoringEnum"))
    if monitor is not None:
        print(f"Monitor   : {monitor}")

    # Arm
    arm = get_val(track.find(".//Arm/Manual"))
    if arm is not None:
        print(f"Arm       : {arm}")

    # Solo
    solo = get_val(track.find("./DeviceChain/Mixer/SoloSink/Manual"))
    if solo is not None:
        print(f"Solo      : {solo}")

    # Audio Routing
    ain = get_val(track.find(".//AudioInputRouting/Target"))
    aout = get_val(track.find(".//AudioOutputRouting/Target"))

    if ain:
        print(f"Audio In  : {ain}")
    if aout:
        print(f"Audio Out : {aout}")

    # MIDI Routing
    minp = get_val(track.find(".//MidiInputRouting/Target"))
    mout = get_val(track.find(".//MidiOutputRouting/Target"))

    if minp:
        print(f"MIDI In   : {minp}")
    if mout:
        print(f"MIDI Out  : {mout}")

    # Sends
    sends = track.findall("./DeviceChain/Mixer/Sends/TrackSendHolder")
    if sends:
        print("Sends:")
        for idx, holder in enumerate(sends):
            val = get_val(holder.find("./Send/Manual"))
            if val is None:
                continue
            print(f"  {chr(65+idx)} : {send_text(val)}")

    # Crossfade
    xf = get_val(track.find("./DeviceChain/Mixer/CrossFadeState/Manual"))
    if xf is not None:
        print(f"Crossfade : {xf}")

    # Track Delay
    delay = get_val(track.find("./DeviceChain/TrackDelay/Value"))
    if delay is not None:
        print(f"TrackDelay: {delay}")

