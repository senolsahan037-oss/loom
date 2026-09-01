import gzip, sys, math
import xml.etree.ElementTree as ET

als = sys.argv[1]
width = int(sys.argv[2]) if len(sys.argv) > 2 else 100
manual_end = float(sys.argv[3]) if len(sys.argv) > 3 else None
show_bounds = "--bounds" in sys.argv

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

TRACK_COL = 28
tracks = []
locators = []
max_end = 0.0

for loc in root.findall(".//Locators/Locators/Locator"):
    tnode = loc.find("./Time")
    nnode = loc.find("./Name")
    if tnode is not None:
        t = float(tnode.attrib.get("Value", "0"))
        n = nnode.attrib.get("Value", "").strip() if nnode is not None else ""
        locators.append((t, n))

for track in root.iter():
    if track.tag not in ["AudioTrack", "MidiTrack", "GroupTrack"]:
        continue

    name_node = track.find("./Name/UserName")
    name = name_node.attrib.get("Value") if name_node is not None and name_node.attrib.get("Value") else "(unnamed)"

    clips = []

    if track.tag != "GroupTrack":
        for clip_tag in ["AudioClip", "MidiClip"]:
            for clip in track.iter(clip_tag):
                s = clip.find("./CurrentStart")
                e = clip.find("./CurrentEnd")
                if s is None or e is None:
                    continue

                start = float(s.attrib.get("Value", "0"))
                end = float(e.attrib.get("Value", "0"))

                disabled = clip.find("./Disabled")
                muted = disabled is not None and disabled.attrib.get("Value") == "true"

                if clip_tag == "AudioClip":
                    char = "░" if muted else "█"
                else:
                    char = "▒" if muted else "▓"

                clips.append((start, end, char))

                if not muted:
                    max_end = max(max_end, end)

    muted_count = sum(1 for _, _, ch in clips if ch in ["░", "▒"])
    tracks.append((track.tag, name, clips, muted_count))

if manual_end is not None:
    max_end = manual_end

print("==== ALS ASCII TIMELINE ====")
print("Legend: █ Audio ON | ░ Audio MUTED | ▓ MIDI ON | ▒ MIDI MUTED | ▰ BUS")
print(f"Length: {max_end:.1f} bars | Width: {width}")

ruler = [" "] * width
label_line = [" "] * width

step = 64
for bar in range(0, int(math.ceil(max_end)) + 1, step):
    pos = int((bar / max_end) * (width - 1)) if max_end else 0
    ruler[pos] = "|"
    label = str(bar)
    for i, ch in enumerate(label):
        if pos + i < width:
            label_line[pos + i] = ch

loc_line = [" "] * width
for t, n in locators:
    if 0 <= t <= max_end:
        pos = int((t / max_end) * (width - 1))
        label = n.strip()[:1].upper()
        if label:
            loc_line[pos] = label

print()
print("TRACK".ljust(TRACK_COL) + "│TIMELINE")
print("─" * TRACK_COL + "┼" + "─" * width)
print("Bars".ljust(TRACK_COL) + "│" + "".join(ruler))
print("".ljust(TRACK_COL) + "│" + "".join(label_line))
print("LOC".ljust(TRACK_COL) + "│" + "".join(loc_line))

for track_type, name, clips, muted_count in tracks:
    if track_type == "GroupTrack":
        label = ("▰ " + name + " [BUS]")[:TRACK_COL]
        print(label.ljust(TRACK_COL) + "│" + "▰" * width)
        continue

    line = ["·"] * width

    for start, end, char in clips:
        if start > max_end:
            continue
        end = min(end, max_end)
        a = int((start / max_end) * (width - 1)) if max_end else 0
        b = max(a + 1, int((end / max_end) * (width - 1))) if max_end else a + 1

        for i in range(a, min(b, width)):
            line[i] = char

        if show_bounds:
            if 0 <= a < width:
                line[a] = "|"
            if 0 <= b < width:
                line[b - 1] = "|"

    clip_count = len(clips)
    suffix = f" [{clip_count}/{muted_count}M]" if muted_count else f" [{clip_count}]"
    prefix = "  "
    label = (prefix + name + suffix)[:TRACK_COL]
    print(label.ljust(TRACK_COL) + "│" + "".join(line))
