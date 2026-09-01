import gzip, sys
import xml.etree.ElementTree as ET

als = sys.argv[1]
show_all = "--all" in sys.argv

target = ""
for arg in sys.argv[2:]:
    if not arg.startswith("--"):
        target = arg.lower()
        break
full = "--full" in sys.argv

def val(node, default="?"):
    return node.attrib.get("Value", default) if node is not None else default

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

print("==== AUDIO CLIP DETAIL ====")
print("Mode:", "FULL" if full else "SUMMARY")

for track in root.iter():
    if track.tag != "AudioTrack":
        continue

    name_node = track.find("./Name/UserName")
    track_name = val(name_node, "(unnamed)")

    if (not show_all) and target and target not in track_name.lower():
        continue

    clips = list(track.iter("AudioClip"))
    if not clips:
        continue

    print(f"\nTRACK: {track_name}")
    print("-" * 90)

    for i, clip in enumerate(clips, 1):
        start = val(clip.find("./CurrentStart"), "0")
        end = val(clip.find("./CurrentEnd"), "0")

        disabled = clip.find("./Disabled")
        state = "MUTED" if disabled is not None and val(disabled) == "true" else "ON"

        path_node = clip.find(".//SampleRef/FileRef/Path")
        rel_node = clip.find(".//SampleRef/FileRef/RelativePath")
        source = val(path_node, "") or val(rel_node, "") or "UNKNOWN"
        source_name = source.split("/")[-1]

        print(f"C{i:03d} | {state:5} | {float(start):7.2f}–{float(end):7.2f} | {source_name}")

        if full:
            loop_on = val(clip.find("./Loop/LoopOn"))
            loop_start = val(clip.find("./Loop/LoopStart"))
            loop_end = val(clip.find("./Loop/LoopEnd"))
            loop_len = val(clip.find("./Loop/LoopLength"))
            start_marker = val(clip.find("./Loop/StartMarker"))
            end_marker = val(clip.find("./Loop/EndMarker"))

            warp_mode = val(clip.find("./WarpMode"))
            is_warped = val(clip.find("./IsWarped"))
            tempo = "project/warp"
            gain = val(clip.find("./SampleVolume"))
            pitch = val(clip.find("./PitchCoarse"))
            detune = val(clip.find("./PitchFine"))
            signature_num = val(clip.find("./TimeSignature/Numerator"))
            signature_den = val(clip.find("./TimeSignature/Denominator"))

            print(f"      Loop      : {loop_on} | Start:{loop_start} End:{loop_end} Len:{loop_len}")
            print(f"      Markers   : StartMarker:{start_marker} EndMarker:{end_marker}")
            print(f"      Warp      : {is_warped} | Mode:{warp_mode} | BPM:{tempo}")
            print(f"      Gain/Pitch: Gain:{gain} | Pitch:{pitch} | Detune:{detune}")
            print(f"      Signature : {signature_num}/{signature_den}")
