#!/usr/bin/env python3
"""Extract the sound sources the producer actually uses.

What the measurement showed: of 189 MIDI tracks, 113 are Simpler or Sampler --
so what defines the sound is not the device type but the SAMPLE LOADED INTO IT.
That is why the device tag is read together with the file name under
SampleRef/FileRef.

Audio clip sources are collected the same way -- the producer's palette is the
union of both.
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AIMixMaster"))
sys.path.insert(0, str(ROOT / "scripts"))

from aimixmaster.als_io import load_als  # noqa: E402
from aimixmaster.gain_staging import normalized_device_name  # noqa: E402
from aimixmaster.project_analyzer import direct_devices, iter_tracks  # noqa: E402
from extract_device_chains import display_name, expand_devices, role_for  # noqa: E402

INSTRUMENT_TAGS = {
    "OriginalSimpler", "MultiSampler", "Operator", "UltraAnalog", "InstrumentVector",
    "Collision", "Tension", "Electric", "InstrumentImpulse", "DrumGroupDevice",
    "PluginDevice", "MxDeviceInstrument", "AuPluginDevice", "InstrumentGroupDevice",
}


def _sample_name(node):
    """The file name under SampleRef. The name is kept, not the path -- a name travels."""
    for path in ("./Name", "./FileRef/Name"):
        found = node.find(path)
        if found is not None:
            text = (found.attrib.get("Value") or "").strip()
            if text:
                return text
    for path in ("./FileRef/Path", "./FileRef/RelativePath"):
        found = node.find(path)
        if found is not None:
            text = (found.attrib.get("Value") or "").strip()
            if text:
                return unquote(text).replace("\\", "/").rsplit("/", 1)[-1]
    return None


def read_sources(als_path):
    root = load_als(Path(als_path)).getroot()
    rows = []
    for track in iter_tracks(root):
        name = display_name(track)
        if not name:
            continue
        devices = expand_devices(direct_devices(track))
        instruments = [normalized_device_name(d) for d in devices if d.tag in INSTRUMENT_TAGS]

        samples = []
        for device in devices:
            if device.tag not in INSTRUMENT_TAGS:
                continue
            for ref in device.iter("SampleRef"):
                sample = _sample_name(ref)
                if sample:
                    samples.append(sample)

        clip_samples = []
        for ref in track.iter("SampleRef"):
            sample = _sample_name(ref)
            if sample:
                clip_samples.append(sample)

        rows.append({
            "track": name,
            "track_type": track.tag,
            "role": role_for(name),
            "instruments": instruments,
            "instrument_samples": sorted(set(samples)),
            # Track'in tumundeki SampleRef'ler: audio klipler dahil.
            "all_samples": sorted(set(clip_samples)),
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
            rows = read_sources(path)
        except Exception as error:
            print("  [%d/%d] SKIPPED %s (%s)" % (index, len(files), Path(path).name, error), flush=True)
            continue
        for row in rows:
            row["project"] = Path(path).stem
        all_rows.extend(rows)
        print("  [%d/%d] %-38s %d tracks" % (index, len(files), Path(path).stem[:38], len(rows)), flush=True)

    print()
    print("=" * 62)
    instrument_tracks = [r for r in all_rows if r["instruments"]]
    print("tracks: %d, with an instrument: %d" % (len(all_rows), len(instrument_tracks)))

    print()
    print("MOST USED SOUND SOURCE DEVICES:")
    for device, count in collections.Counter(d for r in instrument_tracks for d in r["instruments"]).most_common(12):
        print("  %-24s %4d" % (device, count))

    print()
    print("MOST RECURRING SAMPLES:")
    samples = collections.Counter(s for r in all_rows for s in r["all_samples"])
    for sample, count in samples.most_common(20):
        print("  %4d  %s" % (count, sample[:72]))

    if args.out:
        Path(args.out).write_text(json.dumps({"tracks": all_rows}, indent=2), encoding="utf-8")
        print()
        print("saved: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
