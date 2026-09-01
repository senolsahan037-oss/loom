import gzip
import sys
import xml.etree.ElementTree as ET

als = sys.argv[1]

def v(node, default=""):
    return node.attrib.get("Value", default) if node is not None else default


# -----------------------------
# FEATURE COLLECTION
# -----------------------------
kick = snare = hat = bass = fx = synthetic = acoustic = 0
total = 0

with gzip.open(als, "rb") as f:
    root = ET.parse(f).getroot()

for t in root.iter():
    if t.tag not in ["AudioTrack", "GroupTrack", "MidiTrack"]:
        continue

    name = v(t.find("./Name/UserName"), "").lower()
    if not name:
        continue

    total += 1

    if "kick" in name: kick += 1
    if "snare" in name: snare += 1
    if "hat" in name or "hh" in name: hat += 1
    if "bass" in name or "sub" in name: bass += 1
    if "fx" in name or "glitch" in name: fx += 1

    if "909" in name or "808" in name:
        synthetic += 1
    if "acoustic" in name or "live" in name:
        acoustic += 1


# -----------------------------
# NORMALIZATION
# -----------------------------
def n(x):
    return x / total if total else 0

k = n(kick)
s = n(snare)
h = n(hat)
b = n(bass)
f = n(fx)
syn = n(synthetic)
acc = n(acoustic)


# -----------------------------
# MULTI-LABEL GENRE MODEL v2
# -----------------------------

scores = {}

# Trap
scores["Trap"] = (
    (b * 0.35) +
    (h * 0.35) +
    (syn * 0.2) +
    (k * 0.1)
)

# BoomBap
scores["BoomBap"] = (
    (s * 0.4) +
    (k * 0.3) +
    (acc * 0.2)
)

# EDM
scores["EDM"] = (
    (f * 0.4) +
    (syn * 0.35) +
    (k * 0.15)
)

# Drill
scores["Drill"] = (
    (k * 0.4) +
    (b * 0.3) +
    (h * 0.2)
)

# Experimental / Sample Based
scores["Hybrid"] = (
    (f * 0.3) +
    (acc * 0.2) +
    (syn * 0.2)
)


# -----------------------------
# SOFTMAX NORMALIZATION
# -----------------------------
import math

exp_vals = {k: math.exp(v) for k, v in scores.items()}
sum_exp = sum(exp_vals.values())

probs = {k: v / sum_exp for k, v in exp_vals.items()}


# -----------------------------
# OUTPUT
# -----------------------------
print("\n==== GENRE DETECTOR v2 (BALANCED) ====\n")

print("FEATURES")
print(f"Kick: {k:.3f} | Snare: {s:.3f} | Hat: {h:.3f}")
print(f"Bass: {b:.3f} | FX: {f:.3f}")
print(f"Synthetic: {syn:.3f} | Acoustic: {acc:.3f}")

print("\nGENRE PROBABILITIES")

for g, p in sorted(probs.items(), key=lambda x: x[1], reverse=True):
    print(f"{g:12}: {p:.3f}")

best = max(probs.items(), key=lambda x: x[1])

print("\nFINAL")
print(f"PRIMARY GENRE: {best[0]}")
print(f"CONFIDENCE   : {best[1]:.3f}")
