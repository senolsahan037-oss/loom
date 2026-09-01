#!/usr/bin/env python3
"""Extract the real device chains from the producer's own .als projects.

Why: what Presetor should recommend is not a matter of opinion. The producer
has already built their own chains on hundreds of tracks; that is what is read
here.

The role is derived from the track name (kick/snare/bass/keys/pad/vocal/fx...).
Where no name matches, the role stays "unknown" -- it is never invented.
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AIMixMaster"))

from aimixmaster.als_io import load_als  # noqa: E402
from aimixmaster.gain_staging import normalized_device_name  # noqa: E402
from aimixmaster.project_analyzer import direct_devices, iter_tracks  # noqa: E402

# Ordered so the more specific term wins ("sub bass" resolves to sub, not bass).
ROLE_KEYWORDS = [
    ("kick", ("kick", "kck", "bd ", "bassdrum")),
    ("snare", ("snare", "clap", "rim", "sd ")),
    ("hat", ("hat", "hh", "ride", "cymbal", "shaker", "tambourine")),
    ("perc", ("perc", "conga", "bongo", "tom")),
    ("sub", ("sub", "808")),
    ("bass", ("bass",)),
    ("keys", ("key", "piano", "rhodes", "epiano", "organ")),
    ("pad", ("pad", "string", "choir")),
    ("lead", ("lead", "melod", "synth", "arp", "pluck", "bell")),
    ("guitar", ("guitar", "gtr")),
    ("vocal", ("vocal", "vox", "adlib", "ad-lib", "voice")),
    ("fx", ("fx", "riser", "impact", "sweep", "noise", "atmos", "transition")),
    ("sample", ("sample", "loop", "chop")),
    ("bus", ("buss", " bus", "master", "mix")),
]


def display_name(track):
    """Track name: the name the producer typed, else Live's effective name.

    project_analyzer.track_name reads only Name/UserName, which is empty in
    most projects -- the first scan skipped unnamed tracks entirely. That
    function was left alone because buss_builder's proven path depends on it;
    the widening lives here instead.
    """
    for path in ("./Name/UserName", "./Name/EffectiveName"):
        node = track.find(path)
        if node is not None:
            text = (node.attrib.get("Value") or "").strip()
            if text:
                return text
    return ""


def role_for(name):
    lowered = " %s " % name.lower()
    for role, keywords in ROLE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return role
    return "unknown"


# A rack (AudioEffectGroupDevice) looks like a single device to
# direct_devices. In the first scan these came out as the most common "chain",
# which says nothing -- the real chain is INSIDE the rack. Its contents are read
# from Branches/AudioEffectBranch/DeviceChain/AudioToAudioDeviceChain/Devices
RACK_TAGS = {"AudioEffectGroupDevice", "InstrumentGroupDevice", "MidiEffectGroupDevice"}
MAX_RACK_DEPTH = 3


def expand_devices(devices, depth=0):
    """Replace racks with their contents; nesting depth is capped."""
    expanded = []
    for device in devices:
        if device.tag in RACK_TAGS and depth < MAX_RACK_DEPTH:
            inner = []
            for branch in device.findall("./Branches/*"):
                inner.extend(branch.findall("./DeviceChain/AudioToAudioDeviceChain/Devices/*"))
                inner.extend(branch.findall("./DeviceChain/MidiToAudioDeviceChain/Devices/*"))
            if inner:
                expanded.extend(expand_devices(inner, depth + 1))
                continue
        expanded.append(device)
    return expanded


def read_chains(als_path):
    root = load_als(Path(als_path)).getroot()
    rows = []
    for track in iter_tracks(root):
        name = display_name(track)
        if not name:
            continue
        top_level = direct_devices(track)
        rows.append({
            "track": name,
            "track_type": track.tag,
            "role": role_for(name),
            "chain": [normalized_device_name(device) for device in expand_devices(top_level)],
            "top_level_chain": [normalized_device_name(device) for device in top_level],
            "uses_rack": any(device.tag in RACK_TAGS for device in top_level),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    roots = args.roots or [str(Path.home() / "Desktop"), str(Path.home() / "Documents"), str(Path.home() / "Music" / "Ableton")]
    files = []
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(os.path.expanduser(root)):
            if "/Backup" in dirpath or "/Factory" in dirpath or "/Codex/" in dirpath:
                continue
            files.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".als"))
    files.sort()
    if args.limit:
        files = files[: args.limit]

    print("%d projects to scan" % len(files), flush=True)
    all_rows = []
    for index, path in enumerate(files, 1):
        try:
            rows = read_chains(path)
        except Exception as error:
            print("  [%d/%d] SKIPPED %s (%s)" % (index, len(files), Path(path).name, error), flush=True)
            continue
        for row in rows:
            row["project"] = Path(path).stem
        all_rows.extend(rows)
        print("  [%d/%d] %-38s %d tracks" % (index, len(files), Path(path).stem[:38], len(rows)), flush=True)

    with_devices = [r for r in all_rows if r["chain"]]
    print()
    print("=" * 62)
    print("tracks: %d, with devices: %d" % (len(all_rows), len(with_devices)))

    device_counts = collections.Counter(d for r in with_devices for d in r["chain"])
    print()
    print("MOST USED DEVICES:")
    for device, count in device_counts.most_common(15):
        print("  %-28s %4d" % (device, count))

    print()
    print("MOST COMMON CHAINS BY ROLE:")
    by_role = collections.defaultdict(collections.Counter)
    for row in with_devices:
        by_role[row["role"]][" > ".join(row["chain"])] += 1
    for role in sorted(by_role, key=lambda r: -sum(by_role[r].values())):
        total = sum(by_role[role].values())
        print("  [%s] %d tracks" % (role, total))
        for chain, count in by_role[role].most_common(3):
            print("      %2dx  %s" % (count, chain[:96]))

    if args.out:
        Path(args.out).write_text(json.dumps({"tracks": all_rows}, indent=2), encoding="utf-8")
        print()
        print("saved: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
