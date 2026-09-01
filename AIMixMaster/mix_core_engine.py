import sys
from als_model_input import extract_track_objects

def bpm_gate(bpm_value, low, high):
    return 1.0 if low <= bpm_value <= high else 0.35

als_path = sys.argv[1] if len(sys.argv) > 1 else None
if not als_path:
    print("Usage: python3 mix_core_engine.py <project.als>")
    sys.exit(1)

tracks = extract_track_objects(als_path)

bpm = 95

names = " ".join(t.get("name", "").lower() for t in tracks)

k = 1.0 if "kick" in names or "kİck" in names else 0.0
s = 1.0 if "snare" in names else 0.0
h = 1.0 if "hat" in names else 0.0
b = 1.0 if "bass" in names else 0.0
syn = 1.0 if any(x in names for x in ["synth", "pluck", "lead"]) else 0.0
acc = 1.0 if any(x in names for x in ["sample", "voc", "fx"]) else 0.0
f = 1.0 if "fx" in names else 0.0

boom_bap_gate = bpm_gate(bpm, 80, 105)
trap_gate = bpm_gate(bpm, 130, 170)
drill_gate = bpm_gate(bpm, 130, 150)
edm_gate = bpm_gate(bpm, 118, 135)
hybrid_gate = 1.0

scores = {
    "Trap": ((b * 0.4) + (h * 0.3) + (syn * 0.2)) * trap_gate,
    "BoomBap": ((s * 0.4) + (k * 0.3) + (acc * 0.2)) * boom_bap_gate,
    "EDM": ((f * 0.2) + (syn * 0.4) + (k * 0.2)) * edm_gate,
    "Hybrid": ((f * 0.3) + (acc * 0.3) + (syn * 0.2)) * hybrid_gate,
    "Drill": ((k * 0.4) + (b * 0.3) + (h * 0.2)) * drill_gate,
}

best = max(scores.items(), key=lambda x: x[1])

print("AI-MIXMASTERED CORE")
print("-------------------")
print(f"ALS: {als_path}")
print(f"BPM: {bpm}")
print(f"Detected genre: {best[0]} ({best[1]:.2f})")
print()
print("MIX PLAN")
print("- Gain stage drum buss first")
print("- Keep kick and bass mono-focused")
print("- Clean low-mid buildup on sample/music busses")
print("- Treat vocal samples as texture, not lead vocal")
print("- Keep snare FX controlled; avoid washing transient")
print("- Use light glue on drum buss, not heavy master compression")
