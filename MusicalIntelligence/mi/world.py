#!/usr/bin/env python3
"""Extract number-one songs for every country that has a real chart archive.

Two things generalise badly across countries and are handled explicitly:

Column order. Turkey's tables run date, song, artist; others put the song first,
or add an issue-date column. So the header row is read and the song and artist
columns are found by name, rather than trusting a position that happens to work
for one country.

Correctness. Every parse is checked against the calendar: a year of weekly number
ones must add up to about fifty-two weeks. A year that does not is rejected and
logged, never quietly averaged in -- a wrong parse looks exactly like real data
once it is in the file.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mi.catalogue import _clean, _rows, _ROWSPAN, _QUOTED

UA = ("LoomMusicalIntelligence/1.0 "
      "(https://github.com/senolsahan037-oss/loom; corpus research)")
ROOT_CATEGORY = "Category:Lists of number-one songs"
YEAR = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
WEEKS_MIN, WEEKS_MAX = 44, 57

TITLE_WORDS = re.compile(r"\b(single|song|title|record|track)\b", re.I)
ARTIST_WORDS = re.compile(r"\b(artist|performer|act|band)\b", re.I)


def api(params: dict) -> dict:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    time.sleep(0.35)
    return payload


def members(category: str, kinds: str = "subcat|page") -> list[dict]:
    out, cont = [], None
    while True:
        params = {"action": "query", "list": "categorymembers", "cmtitle": category,
                  "cmlimit": "500", "cmtype": kinds, "format": "json",
                  "formatversion": "2"}
        if cont:
            params["cmcontinue"] = cont
        payload = api(params)
        out.extend(payload.get("query", {}).get("categorymembers", []))
        cont = payload.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return out


def wikitext(title: str) -> str | None:
    url = ("https://en.wikipedia.org/wiki/"
           + urllib.parse.quote(title.replace(" ", "_")) + "?action=raw")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code in (404, 400):
            return None
        raise
    time.sleep(0.35)
    return text


def header_columns(lines: list[str]) -> tuple[int, int] | None:
    """Which column holds the song and which the artist, read from the header.

    Headers come in the same two layouts as the rows: all on one line separated
    by `!!`, or one `!` line per column. Consecutive single-cell header lines are
    gathered into one header before it is read.
    """
    groups, current = [], []
    for line in lines:
        if line.startswith("!"):
            body = line.lstrip("!")
            # Some pages separate header cells with "!!", others with "||".
            separator = "!!" if "!!" in body else ("||" if "||" in body else None)
            current.extend(body.split(separator) if separator else [body])
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    for group in groups:
        cells = [_clean(cell) for cell in group]
        if len(cells) < 2:
            continue
        title_at = artist_at = None
        for index, cell in enumerate(cells):
            if title_at is None and TITLE_WORDS.search(cell):
                title_at = index
            elif artist_at is None and ARTIST_WORDS.search(cell):
                artist_at = index
        if title_at is not None and artist_at is not None:
            return title_at, artist_at
    return None


def tables(text: str) -> list[str]:
    """Each wikitable on the page, separately.

    Pages often open with a small key or legend table before the chart itself.
    Reading the page as one stream stopped at the first `|}` and never reached
    the real table -- the single largest cause of pages parsing to nothing.
    """
    blocks, current = [], None
    for line in text.splitlines():
        if line.lstrip().startswith("{|"):
            current = []
            continue
        if line.lstrip().startswith("|}") and current is not None:
            blocks.append("\n".join(current))
            current = None
            continue
        if current is not None:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def parse_page(text: str, year: int) -> tuple[list[dict], str]:
    """(works, note). An empty list with a note means the page was not usable."""
    notes = []
    for block in sorted(tables(text), key=len, reverse=True):
        works, note = _parse_table(block, year)
        if works:
            return works, ""
        notes.append(note)
    return [], notes[0] if notes else "no table on the page"


def _parse_table(text: str, year: int) -> tuple[list[dict], str]:
    columns = header_columns(text.splitlines())
    if columns is None:
        return [], "no header naming a song and an artist column"
    title_at, artist_at = columns

    works: dict[tuple[str, str], dict] = {}
    inherited = 0
    for row in _rows(text):
        if len(row) <= max(title_at, artist_at):
            if inherited > 0:
                inherited -= 1
            continue
        if inherited > 0:
            inherited -= 1
            continue
        weeks = 1
        span = _ROWSPAN.search(row[title_at])
        if span:
            weeks = int(span.group(1))
            inherited = weeks - 1
        raw_title, raw_artist = _clean(row[title_at]), _clean(row[artist_at])
        quoted = _QUOTED.search(raw_title)
        title = (quoted.group(1) if quoted else raw_title).strip()
        artist = raw_artist.strip()
        if not title or not artist or len(artist) > 70 or len(title) > 90:
            continue
        key = (artist.lower(), title.lower())
        entry = works.setdefault(key, {"artist": artist, "title": title,
                                       "year": year, "weeks_at_one": 0})
        entry["weeks_at_one"] += weeks

    total = sum(w["weeks_at_one"] for w in works.values())
    if not works:
        return [], "no rows parsed"
    if not WEEKS_MIN <= total <= WEEKS_MAX:
        return [], f"weeks sum to {total}, not a plausible year"
    return list(works.values()), ""


_NOISE = re.compile(
    r"^List of\s+|\bnumber[- ]ones?\b|\bnumber[- ]one\b|\bsingles?\b|\bsongs?\b"
    r"|\bhits?\b|\bof\b|\bin\b|\bthe\b", re.I)


def chart_of(title: str, country: str) -> str:
    """Which chart a page is, from its title.

    The United States alone keeps sixteen separate charts -- Hot 100, Country,
    R&B/Hip-Hop, Dance, Latin Pop, Regional Mexican and more -- so a work's chart
    is the genre label the corpus most needs. Losing it would flatten sixteen
    genres into one bucket called "United States".
    """
    name = YEAR.sub("", title)
    name = re.sub(r"\((?:U\.S\.|USA?|UK|" + re.escape(country) + r")\)", "", name)
    name = _NOISE.sub(" ", name)
    name = re.sub(r"[\s,]+", " ", name).strip(" -–—()")
    return name or "national chart"


def country_pages(category: str, depth: int = 3) -> list[tuple[str, int]]:
    pages, seen, frontier = [], {category}, [category]
    for _ in range(depth):
        nxt = []
        for parent in frontier:
            for child in members(parent):
                if child["title"] in seen:
                    continue
                seen.add(child["title"])
                if child["ns"] == 14:
                    nxt.append(child["title"])
                else:
                    years = YEAR.findall(child["title"])
                    if len(years) == 1:
                        pages.append((child["title"], int(years[0])))
        frontier = nxt
        if not frontier:
            break
    return sorted(set(pages), key=lambda item: item[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", action="append", help="limit to these countries")
    parser.add_argument("--max-pages", type=int, help="per country, for a trial run")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {
        "source": "en.wikipedia number-one song lists",
        "check": f"a parsed year must sum to {WEEKS_MIN}-{WEEKS_MAX} weeks",
        "countries": {},
    }

    for entry in members(ROOT_CATEGORY, "subcat"):
        name = entry["title"].replace("Category:Lists of number-one songs in ", "")
        name = name.replace("the ", "").strip()
        if "in Europe" in entry["title"] or name.startswith("Category:"):
            continue
        if args.only and name not in args.only:
            continue
        if name in result["countries"]:
            print(f"{name}: already done, skipping")
            continue

        pages = country_pages(entry["title"])
        if args.max_pages:
            pages = pages[:args.max_pages]
        works, accepted, rejected = [], [], {}
        seen_years = set()
        for title, year in pages:
            text = wikitext(title)
            if text is None:
                rejected[str(year)] = "page not fetchable"
                continue
            parsed, note = parse_page(text, year)
            if note:
                rejected[f"{chart_of(title, name)} {year}"] = note
                continue
            chart = chart_of(title, name)
            for work in parsed:
                work["chart"] = chart
            works.extend(parsed)
            accepted.append(year)
            seen_years.add(year)
        result["countries"][name] = {
            "calendar_years": sorted(seen_years),
            "charts": sorted({w["chart"] for w in works}),
            "years_accepted": sorted(accepted),
            "years_rejected": rejected,
            "work_count": len(works),
            "works": works,
        }
        print(f"{name:20} {len(seen_years):3} calendar year(s), "
              f"{len({w['chart'] for w in works}):2} chart(s), {len(works):5} works, "
              f"{len(rejected):3} page(s) rejected", flush=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")

    total = sum(c["work_count"] for c in result["countries"].values())
    print(f"\n{len(result['countries'])} countries, {total} works -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
