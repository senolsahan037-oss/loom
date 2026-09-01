#!/usr/bin/env python3
"""Generate synthetic fixtures so the suite runs in a clean clone.

This data is MADE UP and must stay that way. The real measured data comes from
the producer's own projects, is personal, and is never published. A fixture's
job is to prove the code COMPUTES CORRECTLY, not to say anything about the
producer.

So the distinction cannot be lost, every fixture carries "synthetic": true and
the loaders report which source they used through data_source.
"""
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
random.seed(20260901)

PROJECTS = [f"demo_project_{index:02d}" for index in range(1, 13)]

# Role -> (device, probability of appearing). This mimics the SHAPE of the
# real measurement, not its values.
ROLE_CHAINS = {
    "kick":   [("EQ Eight", 0.95), ("Glue Compressor", 0.45), ("DrumBuss", 0.35)],
    "snare":  [("EQ Eight", 0.80), ("Glue Compressor", 0.45), ("Reverb", 0.25)],
    "hat":    [("EQ Eight", 0.65), ("Saturator", 0.55), ("AutoPan", 0.45)],
    "bass":   [("EQ Eight", 0.85), ("Glue Compressor", 0.75), ("Saturator", 0.55)],
    "sub":    [("EQ Eight", 0.95), ("Glue Compressor", 0.65), ("Saturator", 0.60)],
    "keys":   [("EQ Eight", 0.85), ("Saturator", 0.65), ("Utility", 0.65)],
    "pad":    [("EQ Eight", 0.90), ("Saturator", 0.50), ("Reverb", 0.45)],
    "lead":   [("EQ Eight", 0.85), ("Saturator", 0.55), ("Utility", 0.45)],
    "perc":   [("EQ Eight", 0.65), ("Saturator", 0.65), ("Reverb", 0.30)],
    "sample": [("EQ Eight", 0.75), ("Saturator", 0.70), ("Glue Compressor", 0.60)],
    "bus":    [("Glue Compressor", 0.80), ("EQ Eight", 0.70), ("Utility", 0.50)],
    "fx":     [("EQ Eight", 0.90), ("Reverb", 0.45), ("Saturator", 0.45)],
}
ROLE_SAMPLES = {
    "kick":   ["Demo Kick A.aif", "Demo Kick B.aif"],
    "snare":  ["Demo Snare A.aif", "Demo Snare B.aif"],
    "hat":    ["Demo Hat Closed.aif", "Demo Hat Open.aif"],
    "bass":   ["Demo Bass C1.aif", "Demo Bass C2.aif"],
    "keys":   ["Demo Keys Soft.aif"],
    "pad":    ["Demo Pad Wide.aif"],
    "fx":     ["Demo Riser.aif"],
    "sample": ["Demo Break 90.aif"],
}
ROLE_INSTRUMENTS = {"kick": "DrumGroupDevice", "snare": "DrumGroupDevice", "hat": "DrumGroupDevice",
                    "bass": "OriginalSimpler", "sub": "Operator", "keys": "MultiSampler",
                    "pad": "UltraAnalog", "lead": "UltraAnalog", "sample": "OriginalSimpler"}


def build_chain_rows():
    rows = []
    for role, devices in ROLE_CHAINS.items():
        for index in range(24):
            chain = [name for name, probability in devices if random.random() < probability]
            if not chain:
                chain = [devices[0][0]]
            rows.append({"project": PROJECTS[index % len(PROJECTS)], "track": f"{role.upper()} {index + 1}",
                         "track_type": "AudioTrack", "role": role, "chain": chain,
                         "top_level_chain": chain, "uses_rack": False})
    return rows


def build_source_rows():
    rows = []
    for role in ROLE_CHAINS:
        for index in range(14):
            samples = ROLE_SAMPLES.get(role, [])
            instrument = ROLE_INSTRUMENTS.get(role)
            rows.append({"project": PROJECTS[index % len(PROJECTS)], "track": f"{role.upper()} {index + 1}",
                         "track_type": "MidiTrack" if instrument else "AudioTrack", "role": role,
                         "instruments": [instrument] if instrument else [],
                         "instrument_samples": samples,
                         # Include bounces so the exclusion test actually excludes something.
                         "all_samples": samples + [f"Bounce {role} [2026-01-0{index % 9 + 1} 000000]-1.wav"]})
    return rows


def write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  {path.relative_to(ROOT)}  ({len(payload['tracks'])} tracks)")


def main():
    print("generating synthetic fixtures:")
    write(ROOT / "Presetor" / "data" / "fixture_device_chains.json",
          {"synthetic": True, "tracks": build_chain_rows()})
    write(ROOT / "AISoundDesigner" / "data" / "fixture_sound_sources.json",
          {"synthetic": True, "tracks": build_source_rows()})
    print("this data is MADE UP -- it says nothing about the producer.")


if __name__ == "__main__":
    main()
