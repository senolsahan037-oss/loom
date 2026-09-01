#!/usr/bin/env python3
"""Kullanicinin kendi .als projelerinden gercek bolum yapisini cikarir.

Neden: hangi turun hangi iskelete sahip olmasi gerektigi bir fikir sorusu.
Kullanicinin kendi bitmis projeleri bu soruyu zaten cevapliyor -- locator
kullanmasa bile, arrangement'taki klip giris/cikislari bolum sinirlarini
birakiyor. Burada okunan sey odur, tahmin degil.

Kullanim:
  extract_arrangement_shapes.py <kok_dizin> [...] [--out cikti.json] [--limit N]
"""
import argparse
import gzip
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

CLIP_TAGS = ("AudioClip", "MidiClip")
# Bir bar sinirinda kac ayri track'in ayni anda degismesi "bolum degisimi"
# sayilir. 1-2 track girip cikmasi duzenleme detayi; 3+ yapisal bir olay.
MIN_TRACKS_FOR_BOUNDARY = 3
# Iki olay bu kadar beat icindeyse ayni sinir sayilir (50.5 ve 51.5 gibi).
CLUSTER_TOLERANCE_BEATS = 2.0
# Bir bolumun en az uzunlugu. Bundan kisa olan sey bir bolum degil, bir
# gecis/fill/edit -- onceki bolume katilir. Ilk denemede bu esik yokken
# tek bir projeden 38 "bolum" cikmisti, cogu 1 bar.
MIN_SECTION_BARS = 4
# Sinirin, o an calan track'lerin en az bu oranini etkilemesi gerekir.
# Sabit bir sayi 40 track'lik bir projede cok gevsek, 6 track'likte cok siki.
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
    """Ayni beat civarinda birden cok track degisiyorsa orasi bir sinirdir."""
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

    # Bar gridine oturt: 50.5 beat'teki bir giris bar 13'un basidir, kendi
    # basina bir bolum sinir degeri degil.
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
            # Cok kisa: onceki bolumun devami say, yeni bolum acma.
            sections[-1]["length_bars"] += length
            continue
        sections.append({"start_bar": start_bar + 1, "length_bars": length})

    # Sondaki kirinti da ayni sekilde onceki bolume katilsin.
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
            # Codex/ altindakiler ayni projenin otomatik uretilmis
            # varyantlari -- istatistigi sisirirler.
            if "/Backup" in dirpath or "/Factory" in dirpath or "/Codex/" in dirpath:
                continue
            for filename in filenames:
                if filename.endswith(".als"):
                    files.append(os.path.join(dirpath, filename))
    files.sort()
    if args.limit:
        files = files[: args.limit]

    print("%d proje taranacak" % len(files), flush=True)
    results = []
    for index, path in enumerate(files, 1):
        try:
            project = read_project(path)
        except Exception as error:  # bozuk/kismi dosyalar taramayi durdurmasin
            print("  [%d/%d] ATLANDI %s (%s)" % (index, len(files), os.path.basename(path), error), flush=True)
            continue
        sections, song_end = derive_sections(project["events"], project["beats_per_bar"], project["track_count"])
        if not sections:
            print("  [%d/%d] arrangement yok: %s" % (index, len(files), os.path.basename(path)), flush=True)
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
        print("  [%d/%d] %-40s %3d bar, %d bolum" % (index, len(files), results[-1]["name"][:40], results[-1]["total_bars"], len(sections)), flush=True)

    print()
    print("=" * 60)
    print("arrangement iceren proje: %d / %d" % (len(results), len(files)))
    if results:
        lengths = Counter(s["length_bars"] for r in results for s in r["sections"])
        print()
        print("EN SIK BOLUM UZUNLUKLARI (bar):")
        for bars, count in lengths.most_common(12):
            print("  %3d bar  %4d kez  %s" % (bars, count, "#" * min(40, count // 2)))
        print()
        counts = Counter(r["section_count"] for r in results)
        print("PROJE BASINA BOLUM SAYISI:")
        for section_count, count in sorted(counts.items()):
            print("  %2d bolum  %3d proje" % (section_count, count))
        totals = sorted(r["total_bars"] for r in results)
        print()
        print("SARKI UZUNLUGU (bar): medyan %d, min %d, max %d" % (totals[len(totals) // 2], totals[0], totals[-1]))
        tempos = sorted(r["tempo"] for r in results if r["tempo"])
        if tempos:
            print("TEMPO: medyan %.0f, min %.0f, max %.0f" % (tempos[len(tempos) // 2], tempos[0], tempos[-1]))

    if args.out:
        with open(args.out, "w") as handle:
            json.dump({"projects": results}, handle, indent=2)
        print()
        print("kaydedildi: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
