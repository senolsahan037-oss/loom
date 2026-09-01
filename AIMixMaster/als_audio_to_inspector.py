import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== AUDIO TO INSPECTOR ====")

for track in root.iter():
    if track.tag not in ["AudioTrack", "GroupTrack", "ReturnTrack"]:
        continue

    name = v(track.find("./Name/UserName"), "(unnamed)").strip() or "(unnamed)"

    routing = track.find(".//AudioOutputRouting")
    if routing is None:
        continue

    target = v(routing.find("./Target"), "?")
    upper = v(routing.find("./UpperDisplayString"), "")
    lower = v(routing.find("./LowerDisplayString"), "")
    chooser = v(routing.find("./ChooserName"), "")
    channel = v(routing.find("./Channel"), "")

    print(f"\n[{track.tag}] {name}")
    print(f"  Target : {target}")
    if upper:
        print(f"  Upper  : {upper}")
    if lower:
        print(f"  Lower  : {lower}")
    if chooser:
        print(f"  Chooser: {chooser}")
    if channel:
        print(f"  Channel: {channel}")
