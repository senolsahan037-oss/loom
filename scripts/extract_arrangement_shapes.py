#!/usr/bin/env python3
"""Extract the real section structure from the producer's own .als projects.

Why: which skeleton a genre should have is a matter of opinion. The producer's
own finished projects already answer it -- even without locators, where clips
start and stop across the arrangement leaves the section boundaries behind.
That is what is read here, not guessed.

Usage:
  extract_arrangement_shapes.py <root_dir> [...] [--out output.json] [--limit N]
"""
import argparse
import gzip
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

CLIP_TAGS = ("AudioClip", "MidiClip")
# How many separate tracks must change at the same bar to count as a section
# change. One or two entering is an arrangement detail; three or more is
# structural.
MIN_TRACKS_FOR_BOUNDARY = 3
# Two events within this many beats count as the same boundary (50.5 and 51.5).
CLUSTER_TOLERANCE_BEATS = 2.0
# The minimum length of a section. Anything shorter is not a section but a
# transition, fill or edit, and is folded into the previous one. Without this
# threshold the first attempt produced 38 "sections" from a single project,
# most of them one bar long.
MIN_SECTION_BARS = 4
# A boundary must affect at least this share of the tracks playing. A fixed
# count is too loose in a 40-track project and too strict in a 6-track one.
MIN_TRACK_FRACTION = 0.15


def _value(node, path, cast=str, default=None):
    found = node.find(path)
    if found is None:
        return default
    raw = found.attrib.get("Value")
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def read_project(path):
    with gzip.open(path, "rb") as handle:
        root = ET.parse(handle).getroot()

    tempo = None
    beats_per_bar = None
    for node in root.iter("Tempo"):
        tempo = _value(node, "./Manual", float)
        if tempo:
            break
    for node in root.iter("TimeSignature"):
        numerator = _value(node, ".//Numerator", int)
        if numerator:
            beats_per_bar = numerator
            break

    events = []          # (beat, track_index)
    track_names = []
    for index, track in enumerate(root.iter()):
        if track.tag not in ("AudioTrack", "MidiTrack"):
            continue
        name = _value(track, "./Name/EffectiveName") or _value(track, "./Name/UserName") or "(unnamed)"
        track_index = len(track_names)
        track_names.append(name)
        for clip_tag in CLIP_TAGS:
            for clip in track.iter(clip_tag):
                if _value(clip, "./Disabled") == "true":
                    continue
                start = _value(clip, "./CurrentStart", float)
                end = _value(clip, "./CurrentEnd", float)
                if start is None or end is None or end <= start:
                    continue
                events.append((start, track_index))
                events.append((end, track_index))

    return {
        "tempo": tempo,
        "beats_per_bar": beats_per_bar or 4,
        "track_count": len(track_names),
        "events": events,
    }


def derive_sections(events, beats_per_bar, track_count):
    """Where several tracks change around the same beat, that is a boundary."""
    if not events:
        return [], 0.0

    clusters = []
    for beat, track_index in sorted(events):
        if clusters and beat - clusters[-1]["beat"] <= CLUSTER_TOLERANCE_BEATS:
            clusters[-1]["tracks"].add(track_index)
        else:
            clusters.append({"beat": beat, "tracks": {track_index}})

    song_end = max(beat for beat, _ in events)
    threshold = max(MIN_TRACKS_FOR_BOUNDARY, round(track_count * MIN_TRACK_FRACTION))

    # Snap to the bar grid: an entry at beat 50.5 is the start of bar 13, not a
    # boundary value in its own right.
    bars = sorted({
        round(cluster["beat"] / beats_per_bar)
        for cluster in clusters
        if len(cluster["tracks"]) >= threshold
    })
    if not bars or bars[0] != 0:
        bars = [0] + bars

    end_bar = round(song_end / beats_per_bar)
    sections = []
    for index, start_bar in enumerate(bars):
        next_bar = bars[index + 1] if index + 1 < len(bars) else end_bar
        length = next_bar - start_bar
        if length <= 0:
            continue
        if length < MIN_SECTION_BARS and sections:
            # Too short: treat it as a continuation, do not open a new section.
            sections[-1]["length_bars"] += length
            continue
        sections.append({"start_bar": start_bar + 1, "length_bars": length})

    # Fold a trailing scrap into the previous section the same way.
    while len(sections) > 1 and sections[-1]["length_bars"] < MIN_SECTION_BARS:
        tail = sections.pop()
        sections[-1]["length_bars"] += tail["length_bars"]
    return sections, song_end


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    files = []
    for root in args.roots:
        for dirpath, _dirnames, filenames in os.walk(os.path.expanduser(root)):
            # Everything under Codex/ is an auto-generated variant of the same
            # project and would inflate the statistics.
            if "/Backup" in dirpath or "/Factory" in dirpath or "/Codex/" in dirpath:
                continue
            for filename in filenames:
                if filename.endswith(".als"):
                    files.append(os.path.join(dirpath, filename))
    files.sort()
    if args.limit:
        files = files[: args.limit]

    print("%d projects to scan" % len(files), flush=True)
    results = []
    for index, path in enumerate(files, 1):
        try:
            project = read_project(path)
        except Exception as error:  # a broken file must not stop the scan
            print("  [%d/%d] SKIPPED %s (%s)" % (index, len(files), os.path.basename(path), error), flush=True)
            continue
        sections, song_end = derive_sections(project["events"], project["beats_per_bar"], project["track_count"])
        if not sections:
            print("  [%d/%d] no arrangement: %s" % (index, len(files), os.path.basename(path)), flush=True)
            continue
        results.append({
            "path": path,
            "name": os.path.splitext(os.path.basename(path))[0],
            "tempo": project["tempo"],
            "beats_per_bar": project["beats_per_bar"],
            "track_count": project["track_count"],
            "total_bars": round(song_end / project["beats_per_bar"]),
            "section_count": len(sections),
            "sections": sections,
        })
        print("  [%d/%d] %-40s %3d bars, %d sections" % (index, len(files), results[-1]["name"][:40], results[-1]["total_bars"], len(sections)), flush=True)

    print()
    print("=" * 60)
    print("projects with an arrangement: %d / %d" % (len(results), len(files)))
    if results:
        lengths = Counter(s["length_bars"] for r in results for s in r["sections"])
        print()
        print("MOST COMMON SECTION LENGTHS (bars):")
        for bars, count in lengths.most_common(12):
            print("  %3d bars  %4d times  %s" % (bars, count, "#" * min(40, count // 2)))
        print()
        counts = Counter(r["section_count"] for r in results)
        print("SECTIONS PER PROJECT:")
        for section_count, count in sorted(counts.items()):
            print("  %2d sections  %3d projects" % (section_count, count))
        totals = sorted(r["total_bars"] for r in results)
        print()
        print("SONG LENGTH (bars): median %d, min %d, max %d" % (totals[len(totals) // 2], totals[0], totals[-1]))
        tempos = sorted(r["tempo"] for r in results if r["tempo"])
        if tempos:
            print("TEMPO: median %.0f, min %.0f, max %.0f" % (tempos[len(tempos) // 2], tempos[0], tempos[-1]))

    if args.out:
        with open(args.out, "w") as handle:
            json.dump({"projects": results}, handle, indent=2)
        print()
        print("saved: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
