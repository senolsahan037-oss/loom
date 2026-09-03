#!/usr/bin/env python3
"""Decide which charted works Loom can actually learn anything from.

The rule is the user's: a work with no obtainable composition information is
discarded up front rather than carried along as a name. We hold no audio for
these records, so "obtainable" means an open, structured analysis exists --
MusicBrainz to identify the recording, AcousticBrainz for the measured tempo,
key and rhythm descriptors of it.

This tool measures the coverage rather than assuming it. A source that turns out
to describe one work in twenty is not a source, and it is better to learn that
from a sample of two hundred than after a week of requests.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("LoomMusicalIntelligence/1.0 "
      "(https://github.com/senolsahan037-oss/loom; corpus research)")
MB = "https://musicbrainz.org/ws/2/recording"
AB = "https://acousticbrainz.org/api/v1"
MB_PAUSE = 1.1        # MusicBrainz asks for no more than one request a second
AB_BATCH = 25


def get(url: str, timeout: int = 30):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve(artist: str, title: str) -> dict | None:
    """The best-matching MusicBrainz recording, or nothing if the match is weak."""
    query = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"')
    try:
        payload = get(f"{MB}?query={query}&fmt=json&limit=3")
    except urllib.error.HTTPError:
        return None
    finally:
        time.sleep(MB_PAUSE)
    for recording in payload.get("recordings", []):
        # Below this the match is a different song with similar words in it.
        if recording.get("score", 0) >= 90:
            return {"mbid": recording["id"], "score": recording["score"],
                    "matched_title": recording.get("title"),
                    "matched_artist": recording["artist-credit"][0]["name"]}
    return None


def analysed(mbids: list[str]) -> set[str]:
    """Which of these recordings AcousticBrainz actually holds an analysis for.

    The counts sit at the top level of the response, keyed by MBID. Reading them
    out of `mbid_mapping` instead -- which only lists ids that could not be
    mapped -- reported zero coverage for everything and would have thrown away a
    working source on the strength of a parsing mistake.
    """
    found: set[str] = set()
    for start in range(0, len(mbids), AB_BATCH):
        chunk = mbids[start:start + AB_BATCH]
        try:
            payload = get(f"{AB}/count?recording_ids={';'.join(chunk)}")
        except urllib.error.HTTPError:
            continue
        for mbid, record in payload.items():
            if mbid == "mbid_mapping":
                continue
            if isinstance(record, dict) and record.get("count", 0) > 0:
                found.add(mbid)
        time.sleep(0.5)
    return found


def deezer_bpm(artist: str, title: str) -> float | None:
    """A second, independent tempo. Deezer publishes BPM per track, no key needed.

    Two sources that fail differently are worth more than one that is merely
    large: where they agree the tempo is evidence, where they disagree it is a
    question.
    """
    query = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    try:
        found = get(f"https://api.deezer.com/search?q={query}&limit=1")
        rows = found.get("data") or []
        if not rows:
            return None
        track = get(f"https://api.deezer.com/track/{rows[0]['id']}")
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError):
        return None
    finally:
        time.sleep(0.25)
    bpm = track.get("bpm")
    return float(bpm) if bpm else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default="data/corpus/hits_world.json")
    parser.add_argument("--sample", type=int, default=200,
                        help="how many works to probe before committing to a full pass")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    works = []
    for country, record in data["countries"].items():
        for work in record.get("works", []):
            works.append({**work, "country": country})
    if not works:
        sys.exit("The corpus holds no works yet")

    # Spread the sample across countries and charts rather than taking the first
    # N, which would all be one country's earliest years.
    works.sort(key=lambda w: (w.get("chart", ""), w["country"], w["year"]))
    step = max(1, len(works) // args.sample)
    sample = works[::step][:args.sample]
    print(f"{len(works)} works in the corpus; probing {len(sample)}\n", flush=True)

    resolved, unresolved = [], []
    for index, work in enumerate(sample, 1):
        match = resolve(work["artist"], work["title"])
        if match:
            resolved.append({**work, **match})
        else:
            unresolved.append(work)
        if index % 25 == 0:
            print(f"  {index}/{len(sample)} probed, {len(resolved)} identified",
                  flush=True)

    with_analysis = analysed([r["mbid"] for r in resolved])
    for record in resolved:
        record["acousticbrainz"] = record["mbid"] in with_analysis
    print("\nasking Deezer for tempo...", flush=True)
    for record in resolved:
        record["deezer_bpm"] = deezer_bpm(record["artist"], record["title"])
    usable = [r for r in resolved
              if r["acousticbrainz"] or r["deezer_bpm"]]

    by_chart: dict[str, dict] = {}
    for work in sample:
        chart = work.get("chart", "unknown")
        by_chart.setdefault(chart, {"sampled": 0, "identified": 0, "with_analysis": 0})
        by_chart[chart]["sampled"] += 1
    for work in resolved:
        by_chart[work.get("chart", "unknown")]["identified"] += 1
    for work in usable:
        by_chart[work.get("chart", "unknown")]["with_analysis"] += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "corpus_size": len(works),
        "sampled": len(sample),
        "identified_in_musicbrainz": len(resolved),
        "with_acousticbrainz_analysis": sum(1 for r in resolved if r["acousticbrainz"]),
        "with_deezer_tempo": sum(1 for r in resolved if r["deezer_bpm"]),
        "with_either": len(usable),
        "by_chart": by_chart,
        "usable_examples": usable[:20],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nidentified in MusicBrainz : {len(resolved)}/{len(sample)}"
          f"  ({len(resolved)/len(sample)*100:.0f}%)")
    ab = sum(1 for r in resolved if r["acousticbrainz"])
    dz = sum(1 for r in resolved if r["deezer_bpm"])
    print(f"with AcousticBrainz       : {ab}/{len(sample)}  ({ab/len(sample)*100:.0f}%)")
    print(f"with a Deezer tempo       : {dz}/{len(sample)}  ({dz/len(sample)*100:.0f}%)")
    print(f"usable on either source   : {len(usable)}/{len(sample)}"
          f"  ({len(usable)/len(sample)*100:.0f}%)")
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
