#!/usr/bin/env python3
"""Headless verification of AISoundDesigner. Live is not required."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AISoundDesigner"))

from sounddesigner import source_evidence as se  # noqa: E402

checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


check("bounce output does not count as identity", se.is_bounce("Bounce KICK [2025-07-14 235240]-2.wav"))
check("freeze files do not count as identity", se.is_bounce("Freeze bass 01.wav"))
check("a library sample is not counted as a bounce", not se.is_bounce("Kick Golden Era 46.aif"))
check("reverb impulses do not count as a sound source",
      se.is_non_source("Hybrid_Early_Reflections_Ableton Studio Backwards L.aif"))
check("an ordinary sample does count as a source", not se.is_non_source("Zero Hour Bass A0.aif"))

rows = se.load_tracks()
# See the Presetor test: the repo has no measured data, so this must pass
# on the fixture too.
check("evidence data loaded", len(rows) >= se.MIN_ROLE_SAMPLE * 5, len(rows))
check("the data source in use is reported",
      se.data_source() in ("measured", "synthetic_fixture"), se.data_source())
check("the summary carries the source too", se.summary(rows)["data_source"] == se.data_source())

bass = se.palette("bass", rows)
check("the bass role has a palette", bass is not None)
if bass:
    check("the palette is built from samples seen in more than one project",
          all(item.projects >= se.MIN_PROJECTS for item in bass.samples),
          [(i.sample, i.projects) for i in bass.samples[:3]])
    check("the palette is ordered by how many projects a sample spans",
          all(bass.samples[i].projects >= bass.samples[i + 1].projects for i in range(len(bass.samples) - 1)))
    check("bounces are excluded from the palette", not any(se.is_bounce(item.sample) for item in bass.samples))
    check("impulses are excluded from the palette", not any(se.is_non_source(item.sample) for item in bass.samples))
    check("the palette states how many tracks back it", bass.role_sample >= se.MIN_ROLE_SAMPLE, bass.role_sample)

check("an unknown role yields NO palette", se.palette("__yok_boyle_bir_rol__", rows) is None)
few = [{"role": "tek", "all_samples": ["a.wav"], "instruments": [], "project": "p"}] * (se.MIN_ROLE_SAMPLE - 1)
check("a role with too small a sample stays silent", se.palette("tek", few) is None)
# The same sample appears on enough tracks but all inside ONE project: that
# is that project's decision, not a habit -- no palette is produced.
single = [{"role": "tek", "all_samples": ["a.wav"], "instruments": [], "project": "always_same"}] * se.MIN_ROLE_SAMPLE
check("a sample recurring inside one project does not enter the palette", se.palette("tek", single) is None)
# The same sample across two separate projects: now it enters the palette.
spread = [
    {"role": "tek", "all_samples": ["a.wav"], "instruments": [], "project": "project_%d" % (index % 2)}
    for index in range(se.MIN_ROLE_SAMPLE)
]
spread_result = se.palette("tek", spread)
check("a sample seen in two separate projects does enter the palette",
      spread_result is not None and [item.sample for item in spread_result.samples] == ["a.wav"],
      spread_result)

# Live names its own bounces with the marker in the middle, not at the start.
check("a mid-name (Bounce) marker is excluded",
      se.is_bounce("Love Train C1 2 (Bounce) [2025-09-17 033035].wav"))
check("case does not matter", se.is_bounce("Yin Yang 3 (BOUNCE).wav"))
check("a name that merely contains the word is kept",
      not se.is_bounce("Bouncing Ball Perc.wav"))

# --- multisample families ---------------------------------------------------
# One instrument shipped as one file per pitch must not crowd out every other
# source in a role's palette, while numbered one-shots must stay separate.
families = se.multisample_families([
    "Zero Hour Bass A0.aif", "Zero Hour Bass C1.aif",
    "Zero Hour Bass D2.aif", "Zero Hour BassG4.aif",
    "Kick Golden Era 46.aif", "Kick Golden Era 48.aif", "Kick Golden Era 50.aif",
])
check("pitched files collapse into one family", families.get("Zero Hour Bass") == 4, families)
check("a missing separator does not split the family",
      "Zero Hour" not in families and "Zero Hour BassG" not in families, families)
check("numbered one-shots are not a family", "Kick Golden Era" not in families, families)

pitched = [
    {"role": "bas", "project": "p%d" % index, "all_samples": [
        "Zero Hour Bass A0.aif", "Zero Hour Bass C1.aif",
        "Zero Hour Bass D2.aif", "Zero Hour Bass G4.aif", "other.wav"]}
    for index in range(se.MIN_ROLE_SAMPLE)
]
pitched_result = se.palette("bas", pitched)
labels = [item.sample for item in pitched_result.samples]
check("the palette lists the family once, not once per pitch",
      sum(1 for label in labels if label.startswith("Zero Hour Bass")) == 1, labels)
check("the family label says how many pitches it stands for",
      any("multisample, 4 notes" in label for label in labels), labels)
check("a family loaded beside other samples does not hide them",
      "other.wav" in labels, labels)

summary = se.summary(rows)
check("the summary reports the bounce share", 0 < summary["bounce_share"] < 1, summary["bounce_share"])
check("roles with no palette are listed explicitly", isinstance(summary["roles_without_palette"], list))
check("the most frequent identity samples are reported", len(summary["top_identity_samples"]) > 0)

print("%d checks passed:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("FAILED:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("AISOUNDDESIGNER WORKS")
