import gzip
import sys
import xml.etree.ElementTree as ET
import math

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default

# -----------------------------
# SIMPLE SCORING (heuristic v1)
# -----------------------------

def score_name(name):
    n = name.lower()
    score = 0.0
    if "kick" in n:
        score += 0.6
    if "sub" in n:
        score += 0.3
    if "909" in n or "808" in n:
        score += 0.2
    if "unprocessed" in n:
        score += 0.1
    return min(score, 1.0)

def score_clip_name(clip_name):
    if not clip_name:
        return 0.0
    n = clip_name.lower()
    score = 0.0
    if "kick" in n:
        score += 0.5
    if "808" in n:
        score += 0.3
    if "909" in n:
        score += 0.2
    return min(score, 1.0)

def waveform_proxy(track):
    # proxy: device count / saturation hint (very rough)
    devices = len(list(track.iter()))
    return min(devices / 10.0, 1.0)

# -----------------------------

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

candidates = []

for track in root.iter():
    if track.tag not in ["AudioTrack"]:
        continue

    name = v(track.find("./Name/UserName"), "").strip()
    if not name:
        continue

    clips = []
    for clip in track.iter("AudioClip"):
        ref = clip.find(".//SampleRef/FileRef/Path")
        clip_name = v(ref, "")

        clips.append(clip_name)

    clip_score = max([score_clip_name(c) for c in clips]) if clips else 0.0

    name_score = score_name(name)
    wave_score = waveform_proxy(track)

    total = (name_score * 0.5) + (clip_score * 0.2) + (wave_score * 0.3)

    candidates.append((total, name, clip_score, name_score, wave_score))

# sort best first
candidates.sort(reverse=True, key=lambda x: x[0])

print("==== KICK BUS OPTIMIZER ====\n")

if not candidates:
    print("No candidates found")
    sys.exit()

primary = candidates[0]

print("PRIMARY KICK:")
print(f"  {primary[1]}")
print(f"  SCORE: {primary[0]:.2f}")

print("\nLAYER CANDIDATES:")
for c in candidates[1:4]:
    print(f"  - {c[1]} | score={c[0]:.2f}")

print("\nSUBKICK STRATEGY:")
print("  - extend low-end only")
print("  - avoid transient conflict")
print("  - phase check required")

print("\nFINAL KICK BUS:")
print(f"  1. {primary[1]} (PRIMARY)")
for c in candidates[1:3]:
    print(f"  2. {c[1]} (LAYER)")
print("  3. Sub Kick (LOW END EXTENSION)")
