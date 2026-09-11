#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AIMixMaster"))

# Track names, tempo and time signature are read by their owner. This scan
# used to prefer EffectiveName while every other reader preferred UserName --
# the same project could therefore be reported under two different track names
# depending on which tool asked (measured 2026-09-06).
from aimixmaster.project_analyzer import (  # noqa: E402
    resolve_tempo,
    resolve_time_signature,
    track_name as display_name,
)

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


# Where a track keeps its ARRANGEMENT clips. Session clips live under
# ClipSlot/Value and are not on the timeline; counting them as events was a
# measured defect (Diplomat, 2026-09-07: a 32-beat session clip on Bass Loom).
ARRANGEMENT_EVENT_PATHS = (
    "./DeviceChain/MainSequencer/ClipTimeable/ArrangerAutomation/Events",   # MIDI tracks
    "./DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events",         # audio tracks
)
# A clip this far past every other clip is a leftover, not the song's end
# (Diplomat: a 2-beat clip at bar 188 of a 60-bar song).
OUTLIER_GAP_BARS = 16


def arrangement_clips(track):
    """(start_beat, end_beat, tag, clip) for every enabled arrangement clip.

    The timeline position is the clip's `Time` ATTRIBUTE -- Live reads that;
    CurrentStart/CurrentEnd are the clip's own bounds and only give the length
    (measured: reference_als_safe_edit rule 6)."""
    out = []
    for path in ARRANGEMENT_EVENT_PATHS:
        events = track.find(path)
        if events is None:
            continue
        for clip in events:
            if clip.tag not in CLIP_TAGS or _value(clip, "./Disabled") == "true":
                continue
            start = _value(clip, "./CurrentStart", float)
            end = _value(clip, "./CurrentEnd", float)
            if start is None or end is None or end <= start:
                continue
            time = float(clip.attrib.get("Time", start))
            out.append((time, time + (end - start), clip.tag, clip))
    return out


def read_project(path):
    with gzip.open(path, "rb") as handle:
        root = ET.parse(handle).getroot()

    tempo_resolved = resolve_tempo(root)
    signature = resolve_time_signature(root)
    tempo = tempo_resolved.value
    beats_per_bar = signature.value or 4

    events = []          # (beat, track_index)
    track_names = []
    tracks = []
    all_clips = []       # (start, end, track_index, tag, notes)
    for track in root.iter():
        if track.tag not in ("AudioTrack", "MidiTrack"):
            continue
        name = display_name(track) or "(unnamed)"
        track_index = len(track_names)
        track_names.append(name)
        row = {"track": name, "type": track.tag, "midi_clips": 0, "audio_clips": 0, "notes": 0, "first_beat": None, "last_beat": None, "outlier_clips": 0}
        for start, end, tag, clip in arrangement_clips(track):
            notes = len(clip.findall(".//KeyTrack/Notes/MidiNoteEvent")) if tag == "MidiClip" else 0
            row["midi_clips" if tag == "MidiClip" else "audio_clips"] += 1
            row["notes"] += notes
            row["first_beat"] = start if row["first_beat"] is None else min(row["first_beat"], start)
            row["last_beat"] = end if row["last_beat"] is None else max(row["last_beat"], end)
            all_clips.append((start, end, track_index, tag, notes))
        tracks.append(row)

    # The song's end is the last clip end before a gap of OUTLIER_GAP_BARS with
    # nothing on any track; clips after such a gap are reported, not counted.
    ordered = sorted(all_clips)
    main_end = 0.0
    outliers = []
    for start, end, track_index, tag, notes in ordered:
        if main_end and start - main_end >= OUTLIER_GAP_BARS * beats_per_bar:
            outliers.append({"track": track_names[track_index], "clip": tag, "start_beat": start, "end_beat": end,
                             "start_bar": start / beats_per_bar + 1, "gap_bars": round((start - main_end) / beats_per_bar, 1)})
            tracks[track_index]["outlier_clips"] += 1
            continue
        main_end = max(main_end, end)
        events.append((start, track_index))
        events.append((end, track_index))

    return {
        "tempo": tempo,
        "tempo_source": tempo_resolved.source,
        "beats_per_bar": beats_per_bar,
        # 4 is a fallback, not a reading: say so rather than let the number pass
        # for the project's own signature.
        "beats_per_bar_source": signature.source or "assumed_4_4",
        "track_count": len(track_names),
        "tracks": tracks,
        "events": events,
        "song_end_beat": main_end,
        "last_clip_end_beat": max((c[1] for c in all_clips), default=0.0),
        "outlier_clips": outliers,
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
