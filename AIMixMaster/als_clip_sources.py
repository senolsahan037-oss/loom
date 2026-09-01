import gzip, sys
import xml.etree.ElementTree as ET
from collections import defaultdict

als = sys.argv[1]
gap_tolerance = 0.01

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== ALS SOURCE SUMMARY ====")

for track in root.iter():
    if track.tag != "AudioTrack":
        continue

    name_node = track.find("./Name/UserName")
    track_name = name_node.attrib.get("Value") if name_node is not None and name_node.attrib.get("Value") else "(unnamed)"

    groups = defaultdict(list)

    for clip in track.iter("AudioClip"):
        s = clip.find("./CurrentStart")
        e = clip.find("./CurrentEnd")
        if s is None or e is None:
            continue

        start = float(s.attrib.get("Value", "0"))
        end = float(e.attrib.get("Value", "0"))

        disabled = clip.find("./Disabled")
        muted = disabled is not None and disabled.attrib.get("Value") == "true"

        path_node = clip.find(".//SampleRef/FileRef/Path")
        rel_node = clip.find(".//SampleRef/FileRef/RelativePath")

        if path_node is not None and path_node.attrib.get("Value"):
            source = path_node.attrib["Value"]
        elif rel_node is not None and rel_node.attrib.get("Value"):
            source = rel_node.attrib["Value"]
        else:
            source = "UNKNOWN_SOURCE"

        groups[source].append((start, end, muted))

    if not groups:
        continue

    warning = " ⚠ MULTI-SOURCE" if len(groups) > 1 else ""
    print(f"\n[{track_name}]{warning}")

    for idx, (source, clips) in enumerate(groups.items(), 1):
        clips = sorted(clips)
        starts = [c[0] for c in clips]
        ends = [c[1] for c in clips]
        muted_count = sum(1 for c in clips if c[2])

        segments = []
        seg_start, seg_end = clips[0][0], clips[0][1]

        for start, end, muted in clips[1:]:
            if abs(start - seg_end) <= gap_tolerance:
                seg_end = end
            else:
                segments.append((seg_start, seg_end))
                seg_start, seg_end = start, end

        segments.append((seg_start, seg_end))

        source_name = source.split("/")[-1]
        letter = chr(64 + idx)

        print(f"  Source {letter}: {source_name}")
        print(f"    Clips    : {len(clips)}")
        print(f"    Range    : {min(starts):.2f} → {max(ends):.2f}")
        print(f"    Muted    : {muted_count}")
        print(f"    Segments : " + ", ".join([f"{a:.2f}→{b:.2f}" for a, b in segments]))

    if len(groups) > 1:
        print("  Suggestion: split different sources into separate tracks.")
