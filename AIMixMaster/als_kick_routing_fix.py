import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

tracks = []

for t in root.iter():
    if t.tag not in ["AudioTrack", "GroupTrack"]:
        continue

    name = v(t.find("./Name/UserName"), "").strip()
    if not name:
        continue

    tracks.append((t.tag, name))

print("==== KICK ROUTING FIXED MODEL ====")

kick_bus = None
kick_children = []
subkick_children = []

for tag, name in tracks:
    n = name.lower()

    if "kick buss" in n or "kick bus" in n:
        kick_bus = name

    if "kick" == n.strip() or "kick 1" in n:
        kick_children.append(name)

    if "sub kick" in n or "subkick" in n:
        subkick_children.append(name)

print("\nKICK BUSS")
print("  ├── KICK")

for k in kick_children:
    print(f"  │    ├── {k}")

print("  │")
print("  └── SUBKICK (signal from KICK)")

for s in subkick_children:
    print(f"       ├── {s}")
