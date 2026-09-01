import gzip
import sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default


# -----------------------------
# FEATURE COLLECTOR
# -----------------------------
kick = 0
snare = 0
hat = 0
bass = 0
fx = 0
acoustic = 0
synthetic = 0
total_tracks = 0

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

for track in root.iter():
    if track.tag not in ["AudioTrack", "GroupTrack", "MidiTrack"]:
        continue

    name = v(track.find("./Name/UserName"), "").lower()
    if not name:
        continue

    total_tracks += 1

    # ROLE DETECTION
    if "kick" in name:
        kick += 1
    if "snare" in name:
        snare += 1
    if "hat" in name or "hh" in name:
        hat += 1
    if "bass" in name or "sub" in name:
        bass += 1
    if "fx" in name or "glitch" in name:
        fx += 1

    # SOUND DNA
    if "909" in name or "808" in name:
        synthetic += 1
    if "acoustic" in name or "live" in name:
        acoustic += 1


# -----------------------------
# NORMALIZATION
# -----------------------------
def norm(x):
    if total_tracks == 0:
        return 0
    return round(x / total_tracks, 3)


k = norm(kick)
s = norm(snare)
h = norm(hat)
b = norm(bass)
f = norm(fx)
syn = norm(synthetic)
acc = norm(acoustic)


# -----------------------------
# GENRE LOGIC v1
# -----------------------------

trap_score = (b * 0.4) + (h * 0.3) + (syn * 0.2) + (k * 0.1)
boom_bap = (s * 0.4) + (k * 0.3) + (acc * 0.2)
edm = (fx * 0.4) + (k * 0.2) + (syn * 0.3)
drill = (k * 0.4) + (b * 0.3) + (h * 0.2)


# -----------------------------
# OUTPUT
# -----------------------------
print("\n==== GENRE DETECTION v1 ====\n")

print("FEATURES")
print(f"Kick Density  : {k}")
print(f"Snare Density : {s}")
print(f"Hat Density   : {h}")
print(f"Bass Density  : {b}")
print(f"FX Density    : {f}")
print(f"Synthetic     : {syn}")
print(f"Acoustic      : {acc}")

print("\nGENRE SCORES")
print(f"Trap     : {round(trap_score,3)}")
print(f"BoomBap  : {round(boom_bap,3)}")
print(f"EDM      : {round(edm,3)}")
print(f"Drill    : {round(drill,3)}")

best = max([
    ("Trap", trap_score),
    ("BoomBap", boom_bap),
    ("EDM", edm),
    ("Drill", drill)
], key=lambda x: x[1])

print("\nFINAL CLASSIFICATION")
print(f"PRIMARY GENRE: {best[0]}")
print(f"CONFIDENCE   : {round(best[1],3)}")
