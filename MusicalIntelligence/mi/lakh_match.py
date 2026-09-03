#!/usr/bin/env python3
"""Give the Lakh MIDI files a genre, using the chart corpus we already verified.

Lakh names its files Artist/Title and carries no genre. Our chart catalogue has
both, plus the chart each work topped -- so a file that matches a work from a
dance chart is a dance record on somebody else's authority, not on ours.

This is what the chart work is for. It could never supply drum patterns itself,
but it is a genre label nobody has to take my word for.

The label is exactly as narrow as its source: these are records that charted on
national dance charts, which is commercial dance. Calling the result "house" or
"techno" would claim more than the evidence carries, so it is called what it is.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

# Charts that are a genre. "digital" is a sales format and "urban" is R&B and
# hip-hop; both would quietly pollute the pool.
DANCE_CHARTS = re.compile(r"\b(dance|disco|electronic|club)\b", re.I)
EXCLUDE = re.compile(r"\b(digital|urban|sales)\b", re.I)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)          # (remix), [radio edit]
    text = re.sub(r"\b(feat|ft|featuring|with|vs)\b.*", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def chart_works(path: Path) -> dict[tuple[str, str], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    found: dict[tuple[str, str], dict] = {}
    for country, record in data["countries"].items():
        for work in record.get("works", []):
            chart = work.get("chart") or ""
            if not DANCE_CHARTS.search(chart) or EXCLUDE.search(chart):
                continue
            key = (normalise(work["artist"]), normalise(work["title"]))
            if key[0] and key[1]:
                found.setdefault(key, {**work, "country": country})
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lakh", default="data/sources/clean_midi")
    parser.add_argument("--charts", default="data/corpus/hits_world.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    wanted = chart_works(Path(args.charts))
    print(f"{len(wanted)} distinct works on genuine dance charts", flush=True)

    root = Path(args.lakh)
    if not root.exists():
        sys.exit(f"{root} is not there yet")

    matches, seen_artists = [], collections.Counter()
    for artist_dir in root.iterdir():
        if not artist_dir.is_dir():
            continue
        artist = normalise(artist_dir.name)
        if not artist:
            continue
        for midi in artist_dir.glob("*.mid"):
            title = normalise(midi.stem)
            work = wanted.get((artist, title))
            if work is None:
                continue
            matches.append({
                "midi": str(midi.relative_to(root)),
                "artist": work["artist"], "title": work["title"],
                "chart": work["chart"], "country": work["country"],
                "year": work["year"],
            })
            seen_artists[work["artist"]] += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": "Lakh clean_midi filenames matched to charted dance works",
        "label_meaning": "charted on a national dance chart -- commercial dance, "
                         "not a claim about house or techno specifically",
        "dance_works_in_charts": len(wanted),
        "midi_files_matched": len(matches),
        "distinct_artists": len(seen_artists),
        "top_artists": seen_artists.most_common(10),
        "matches": matches,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(matches)} MIDI file(s) matched, {len(seen_artists)} artists -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
