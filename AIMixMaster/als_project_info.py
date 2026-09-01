import gzip
import sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

ROOT_MAP = {
    "0": "C",
    "1": "C#",
    "2": "D",
    "3": "D#",
    "4": "E",
    "5": "F",
    "6": "F#",
    "7": "G",
    "8": "G#",
    "9": "A",
    "10": "A#",
    "11": "B",
}

CAMELOT = {
    ("C","Major"):"8B", ("G","Major"):"9B", ("D","Major"):"10B",
    ("A","Major"):"11B", ("E","Major"):"12B", ("B","Major"):"1B",
    ("F#","Major"):"2B", ("C#","Major"):"3B", ("G#","Major"):"4B",
    ("D#","Major"):"5B", ("A#","Major"):"6B", ("F","Major"):"7B",

    ("A","Minor"):"8A", ("E","Minor"):"9A", ("B","Minor"):"10A",
    ("F#","Minor"):"11A", ("C#","Minor"):"12A", ("G#","Minor"):"1A",
    ("D#","Minor"):"2A", ("A#","Minor"):"3A", ("F","Minor"):"4A",
    ("C","Minor"):"5A", ("G","Minor"):"6A", ("D","Minor"):"7A",
}

def val(node, default="?"):
    if node is None:
        return default
    return node.attrib.get("Value", default)

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

tempo = "?"
tempo_node = root.find(".//MasterTrack//Tempo/Manual")
if tempo_node is None:
    tempo_node = root.find(".//Tempo/Manual")
if tempo_node is not None:
    tempo = val(tempo_node)

ts_num = val(root.find(".//TimeSignature/Numerator"), "?")
ts_den = val(root.find(".//TimeSignature/Denominator"), "?")

scale_root_raw = val(root.find(".//ScaleInformation/Root"), "")
scale_name = val(root.find(".//ScaleInformation/Name"), "")

root_name = ROOT_MAP.get(scale_root_raw, scale_root_raw)
scale_title = scale_name.capitalize() if scale_name else "?"

camelot = CAMELOT.get((root_name, scale_title), "?")

audio = len(list(root.iter("AudioTrack")))
midi = len(list(root.iter("MidiTrack")))
group = len(list(root.iter("GroupTrack")))
ret = len(list(root.iter("ReturnTrack")))
locators = len(list(root.iter("Locator")))

print("==== PROJECT INFO ====")
print(f"Tempo          : {tempo} BPM")
print(f"Key            : {root_name}")
print(f"Scale          : {scale_title}")
print(f"Camelot        : {camelot}")
print(f"Time Signature : {ts_num}/{ts_den}")
print()
print("Track Counts")
print(f"  Audio        : {audio}")
print(f"  MIDI         : {midi}")
print(f"  Group        : {group}")
print(f"  Return       : {ret}")
print(f"Locators       : {locators}")
