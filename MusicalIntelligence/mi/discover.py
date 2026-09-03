#!/usr/bin/env python3
"""Find which countries actually have chart data, instead of assuming a number.

Wikipedia keeps per-country subcategories of number-one song lists. Walking that
tree tells us the truth: how many countries there are, and for each, how many
years are really recorded. Coverage is measured before anything is built on it,
because a corpus that quietly covers ten countries while claiming a hundred is
worse than a small honest one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("LoomMusicalIntelligence/1.0 "
      "(https://github.com/senolsahan037-oss/loom; corpus research)")
ROOT_CATEGORY = "Category:Lists of number-one songs"
YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def api(params: dict) -> dict:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    time.sleep(0.4)
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


def country_of(title: str) -> str:
    name = title.replace("Category:Lists of number-one songs in ", "")
    return name.replace("the ", "").strip()


def survey(depth: int = 2) -> list[dict]:
    """Per-country year coverage, walking each country's own subtree."""
    countries = []
    for entry in members(ROOT_CATEGORY, "subcat"):
        title = entry["title"]
        if "in Europe" in title:      # a region, not a country
            continue
        name = country_of(title)
        pages, seen = [], {title}
        frontier = [title]
        for _ in range(depth):
            nxt = []
            for category in frontier:
                for child in members(category):
                    if child["title"] in seen:
                        continue
                    seen.add(child["title"])
                    (nxt if child["ns"] == 14 else pages).append(child["title"])
            frontier = nxt
            if not frontier:
                break
        years = sorted({int(match) for page in pages for match in YEAR.findall(page)})
        countries.append({
            "country": name,
            "pages": len(pages),
            "year_count": len(years),
            "first_year": years[0] if years else None,
            "last_year": years[-1] if years else None,
        })
        print(f"  {name:24} {len(pages):4} page(s)  {len(years):3} year(s)"
              + (f"  {years[0]}–{years[-1]}" if years else "  no years found"),
              flush=True)
    return countries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    countries = survey()
    usable = [c for c in countries if c["year_count"] >= 5]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": "en.wikipedia category tree under " + ROOT_CATEGORY,
        "countries_found": len(countries),
        "countries_with_five_or_more_years": len(usable),
        "countries": sorted(countries, key=lambda c: -c["year_count"]),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(countries)} country subcategories, "
          f"{len(usable)} with five or more years of data -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
