import gzip
import sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

def classify_role(name):
    n = name.lower()
    if "kick" in n:
        return "KICK"
    if "snare" in n:
        return "SNARE"
    if "hat" in n or "hh" in n:
        return "HIHAT"
    if "bass" in n or "sub" in n:
        return "BASS"
    if "perc" in n:
        return "PERC"
    return "MUSIC"

def detect_source(name):
    n = name.lower()
    if "909" in n:
        return "ROLAND TR-909"
    if "808" in n:
        return "ROLAND TR-808"
    if "live" in n:
        return "LIVE KIT"
    if "acoustic" in n:
        return "ACOUSTIC KIT"
    return "SAMPLE / UNKNOWN"

def detect_processing(name):
    n = name.lower()
    if "unprocessed" in n:
        return "UNPROCESSED"
    if "fx" in n:
        return "FX"
    return "PROCESSED"

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== MODEL INPUT LAYER ====")

for track in root.iter():
    if track.tag not in ["AudioTrack", "GroupTrack", "MidiTrack"]:
        continue

    name = v(track.find("./Name/UserName"), "").strip()
    if not name:
        continue

    role = classify_role(name)
    source = detect_source(name)
    processing = detect_processing(name)

    print("\nTRACK_OBJECT")
    print(f"  name       : {name}")
    print(f"  role       : {role}")
    print(f"  source     : {source}")
    print(f"  processing  : {processing}")
