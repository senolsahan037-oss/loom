import gzip
import sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default


with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()


tracks_raw = []

for t in root.iter():
    if t.tag not in ["AudioTrack", "MidiTrack", "GroupTrack"]:
        continue

    name = v(t.find("./Name/UserName"), "").strip()
    if not name:
        name = f"({t.tag})"

    tracks_raw.append((t.tag, name))


print("==== BUS TREE (INFERRED) ====")

stack = []

for tag, name in tracks_raw:

    if tag == "GroupTrack":
        # yeni bus başlat
        level = len(stack)
        stack.append(name)
        print("\n" + "  " * level + f"▰ {name} [BUS]")

    else:
        # child track
        level = len(stack)
        print("  " * level + f"├─ {name}")

# stack temizleme (basit flatten)
print("\n==== FLAT VIEW END ====")
