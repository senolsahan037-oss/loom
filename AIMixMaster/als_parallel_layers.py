import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]
keyword = sys.argv[2].lower() if len(sys.argv) > 2 else "kick"

def val(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print(f"==== PARALLEL LAYER ANALYSIS: {keyword.upper()} ====")

tracks = []

for track in root.iter():
    if track.tag not in ["AudioTrack", "GroupTrack"]:
        continue

    name = val(track.find("./Name/UserName"), "").strip()
    if not name:
        continue

    if keyword not in name.lower():
        continue

    clips = []
    for clip in track.iter("AudioClip"):
        s = clip.find("./CurrentStart")
        e = clip.find("./CurrentEnd")
        if s is None or e is None:
            continue

        disabled = clip.find("./Disabled")
        muted = disabled is not None and val(disabled) == "true"

        if muted:
            continue

        clips.append((float(val(s, "0")), float(val(e, "0"))))

    vol = val(track.find("./DeviceChain/Mixer/Volume/Manual"), "1")
    pan = val(track.find("./DeviceChain/Mixer/Pan/Manual"), "0")

    tracks.append({
        "name": name,
        "type": track.tag,
        "clips": clips,
        "volume": vol,
        "pan": pan,
    })

for t in tracks:
    print(f"\n{t['type']}: {t['name']}")
    print(f"  Volume: {t['volume']} | Pan: {t['pan']}")
    if t["clips"]:
        ranges = ", ".join([f"{a:.0f}-{b:.0f}" for a,b in t["clips"][:12]])
        print(f"  Active: {ranges}")
    else:
        print("  Active: no direct audio clips / bus only")

print("\nOVERLAPS")
events = []

for t in tracks:
    for a,b in t["clips"]:
        events.append((a,b,t["name"]))

# pairwise overlap
found = False
for i in range(len(events)):
    a1,b1,n1 = events[i]
    for j in range(i+1, len(events)):
        a2,b2,n2 = events[j]
        start = max(a1,a2)
        end = min(b1,b2)
        if start < end:
            found = True
            print(f"  {start:.0f}-{end:.0f}: {n1} + {n2}")

if not found:
    print("  No direct overlaps found.")
