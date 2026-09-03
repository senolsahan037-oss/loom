#!/usr/bin/env python3
"""Build the hit catalogue the product's genre evidence is drawn from.

The list is never chosen by hand. It comes from a published chart -- Nielsen
Music Control's Türkçe Top 20, whose weekly number ones Wikipedia records per
year -- so the corpus is a measurement someone else made and anyone can check.
A hand-picked list would smuggle one person's taste in as evidence.

Coverage is what it is: Wikipedia holds these years and no others. Years with no
chart are absent rather than filled in from somewhere softer.

  catalogue.py --years 2006-2017 --out data/corpus/hits_tr.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WIKI = "https://tr.wikipedia.org/wiki/"
# Wikimedia refuses a User-Agent without a contact, with 429 rather than 403,
# which reads like rate limiting and is not. A descriptive one is the fix.
USER_AGENT = ("LoomMusicalIntelligence/1.0 "
              "(https://github.com/senolsahan037-oss/loom; corpus research)")
TITLE = "{year} yılı Türkçe Top 20 bir numara parçaların listesi"
SOURCE = "Nielsen Music Control — Türkçe Top 20 weekly number ones, via tr.wikipedia"


def fetch_wikitext(year: int, attempts: int = 4) -> str | None:
    """One year's page, backing off when Wikipedia asks us to slow down."""
    for attempt in range(attempts):
        try:
            return _fetch(year)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == attempts - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"  rate limited, waiting {wait}s", flush=True)
            time.sleep(wait)
    return None


def _fetch(year: int) -> str | None:
    url = WIKI + urllib.parse.quote(TITLE.format(year=year).replace(" ", "_")) + "?action=raw"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    time.sleep(1.0)  # courtesy pause between page requests
    return text


_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
# A cell may carry several HTML attributes before its content pipe --
# `style="..." rowspan="10"|Gökhan Türkmen`. Stripping only the first left the
# rest in the artist name, so the whole attribute run is dropped at once.
# Attribute values may be quoted or bare -- `rowspan=2 align="center"|`
# appears as often as the fully quoted form, and requiring quotes left the
# whole prefix sitting inside the artist name.
_ATTR = re.compile(r'^(?:\s*[a-z-]+=(?:"[^"]*"|[^\s|]+))+\s*\|')
_ROWSPAN = re.compile(r'rowspan\s*=\s*"?(\d+)"?')
_QUOTED = re.compile(r'"([^"]{2,80})"')


def _clean(cell: str) -> str:
    """One table cell, with wiki markup and any HTML attribute prefix removed."""
    cell = _ATTR.sub("", cell)
    # [[Target|Shown]] keeps what is shown; [[Target]] keeps the target.
    cell = _LINK.sub(lambda match: match.group(2) or match.group(1), cell)
    cell = re.sub(r"<[^>]+>|''+|\{\{[^}]*\}\}", "", cell)
    return cell.strip(" |\n\t")


def _rows(wikitext: str) -> list[list[str]]:
    """Table rows as lists of raw cells, whichever layout the year used.

    Some years write a row as one line of `||`-separated cells; others give each
    cell its own `|` line. Both are valid wikitext and both appear in this
    series, so the rows are collected before anything is read out of them.
    """
    rows, current = [], []
    for line in wikitext.splitlines():
        stripped = line.rstrip()
        if stripped.startswith("|-"):
            if current:
                rows.append(current)
            current = []
            continue
        if stripped.startswith("|}"):
            break
        if not stripped.startswith("|") or stripped.startswith("|+"):
            continue
        body = stripped.lstrip("|")
        current.extend(body.split("||") if "||" in body else [body])
    if current:
        rows.append(current)
    return rows


def parse_year(wikitext: str, year: int) -> list[dict]:
    """The weekly number ones of one year.

    A run at number one is written once with rowspan="N" and the N-1 rows under
    it simply omit those columns -- their first cells are then the number *two*
    song. Reading those as number ones inflated 2010 to 88 weeks in a 52-week
    year, so the rowspan is tracked and the inheriting rows are skipped.
    """
    works: dict[tuple[str, str], dict] = {}
    inherited = 0
    for row in _rows(wikitext):
        if len(row) < 3:
            if inherited > 0:
                inherited -= 1
            continue
        if inherited > 0:
            inherited -= 1
            continue
        weeks = 1
        span = _ROWSPAN.search(row[1])
        if span:
            weeks = int(span.group(1))
            inherited = weeks - 1
        title_cell, artist_cell = _clean(row[1]), _clean(row[2])
        quoted = _QUOTED.search(title_cell)
        title = (quoted.group(1) if quoted else title_cell).strip()
        artist = artist_cell.strip()
        if not title or not artist or len(artist) > 60:
            continue
        key = (artist.lower(), title.lower())
        entry = works.setdefault(key, {"artist": artist, "title": title,
                                       "year": year, "weeks_at_one": 0})
        entry["weeks_at_one"] += weeks
    return list(works.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="2006-2017", help="e.g. 2006-2017")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    first, last = (int(part) for part in args.years.split("-"))
    works, missing = [], []
    for year in range(first, last + 1):
        wikitext = fetch_wikitext(year)
        if wikitext is None:
            missing.append(year)
            print(f"  {year}: no chart page -- excluded, not filled in")
            continue
        found = parse_year(wikitext, year)
        works.extend(found)
        print(f"  {year}: {len(found)} distinct number ones")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": SOURCE,
        "years_covered": [y for y in range(first, last + 1) if y not in missing],
        "years_without_chart": missing,
        "works": sorted(works, key=lambda w: (w["year"], w["artist"])),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(works)} work(s) into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
