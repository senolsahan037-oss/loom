import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]
min_layers = int(sys.argv[2]) if len(sys.argv) > 2 else 3

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

clips = []

for track in root.iter("AudioTrack"):
    name = v(track.find("./Name/UserName"), "(unnamed)").strip() or "(unnamed)"

    for clip in track.iter("AudioClip"):
        s = clip.find("./CurrentStart")
        e = clip.find("./CurrentEnd")
        if s is None or e is None:
            continue

        disabled = clip.find("./Disabled")
        if disabled is not None and v(disabled) == "true":
            continue

        start = float(v(s, "0"))
        end = float(v(e, "0"))
        if end <= start:
            continue

        source_node = clip.find(".//SampleRef/FileRef/Path")
        source = v(source_node, "")
        source = source.split("/")[-1] if source else "UNKNOWN"

        clips.append((start, end, name, source))

events = sorted(set([x[0] for x in clips] + [x[1] for x in clips]))

print("==== PARALLEL SIGNAL ANALYSIS ====")
print(f"Minimum layers: {min_layers}")

segments = []

for i in range(len(events) - 1):
    a, b = events[i], events[i + 1]
    if b <= a:
        continue

    active = []
    for start, end, track, source in clips:
        if start < b and end > a:
            active.append((track, source))

    if len(active) >= min_layers:
        segments.append((a, b, active))

# ardışık aynı layer setlerini birleştir
merged = []
for a, b, active in segments:
    key = tuple(sorted(set(t for t, _ in active)))
    if merged and merged[-1]["key"] == key and abs(merged[-1]["end"] - a) < 0.01:
        merged[-1]["end"] = b
    else:
        merged.append({"start": a, "end": b, "active": active, "key": key})

for seg in merged:
    tracks = sorted(set(t for t, _ in seg["active"]))
    print(f"\n{seg['start']:.2f} → {seg['end']:.2f} | Layers: {len(tracks)}")

    for t in tracks:
        print(f"  - {t}")

    if len(tracks) >= 8:
        print("  Risk: HIGH DENSITY")
    elif len(tracks) >= 5:
        print("  Risk: MEDIUM DENSITY")
    else:
        print("  Risk: LOW/MODERATE")
