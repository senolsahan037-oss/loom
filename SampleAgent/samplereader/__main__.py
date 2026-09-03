"""Command line: read, profile, match."""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
from pathlib import Path
import sys

from .read import Reading, read_file, iter_audio
from .spots import find as find_spots
from .loops import write_page, watch_urls
from .speed import compare as speed_compare
from .profile import build_profile, save_profile, load_profile
from .match import rank


def _collect(targets: list[str], limit: int | None) -> list[Path]:
    paths: list[Path] = []
    for target in targets:
        paths.extend(iter_audio(target))
    seen: set[str] = set()
    unique = []
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique[:limit] if limit else unique


def _read_many(paths: list[Path], workers: int, quiet: bool) -> list[Reading]:
    results: list[Reading] = []
    total = len(paths)
    if workers <= 1:
        for i, p in enumerate(paths, 1):
            results.append(read_file(p))
            if not quiet:
                print(f"\r  {i}/{total}  {p.name[:60]:<60}", end="", file=sys.stderr)
    else:
        with futures.ProcessPoolExecutor(max_workers=workers) as pool:
            for i, reading in enumerate(pool.map(read_file, paths, chunksize=4), 1):
                results.append(reading)
                if not quiet:
                    print(f"\r  {i}/{total}  {reading.name[:60]:<60}", end="", file=sys.stderr)
    if not quiet and total:
        print("", file=sys.stderr)
    return results


def _print_reading(r: Reading) -> None:
    if not r.ok:
        print(f"{r.name}\n  OKUNAMADI: {r.error}\n")
        return
    ch = {1: "mono", 2: "stereo"}.get(r.channels or 0, f"{r.channels}ch")
    print(f"{r.name}")
    print(
        f"  {r.duration_s:.2f}s  {r.sample_rate} Hz  {ch}  {r.subtype}"
        f"   peak {r.peak_dbfs} dBFS  rms {r.rms_dbfs} dBFS  crest {r.crest_db} dB"
    )
    tempo = (
        f"{r.tempo_bpm} BPM [{r.tempo_source}]" if r.tempo_bpm
        else f"yok ({r.tempo_reason})"
    )
    chop = f"chop {r.chop_bpm}" if r.chop_bpm else "chop -"
    window = {True: "PENCEREDE", False: "pencere disi", None: "-"}[r.in_chop_range]
    print(f"  tempo: {tempo}   {chop}   68-98: {window}   onset {r.onset_rate_hz}/s")
    key = r.key if r.key else f"yok ({r.key_reason})"
    print(f"  ton:   {key}")
    print(
        f"  tini:  centroid {r.centroid_hz} Hz  rolloff85 {r.rolloff85_hz} Hz"
        f"  low<120 {r.low_ratio}  air>8k {r.air_ratio}  harmonik {r.harmonic_ratio}"
    )
    print(
        f"  taban gurultusu {r.noise_floor_dbfs} dBFS   stereo genislik {r.stereo_width}"
        f"   sessizlik {r.silence_share}"
    )
    print()


def cmd_read(args) -> int:
    paths = _collect(args.targets, args.limit)
    if not paths:
        print("Ses dosyasi bulunamadi.", file=sys.stderr)
        return 1
    readings = _read_many(paths, args.workers, args.json)
    if args.json:
        print(json.dumps([r.as_dict() for r in readings], indent=2, ensure_ascii=False))
    else:
        for r in readings:
            _print_reading(r)
        ok = sum(1 for r in readings if r.ok)
        print(f"{ok}/{len(readings)} dosya okundu.")
    return 0


def cmd_profile(args) -> int:
    paths = _collect(args.targets, args.limit)
    if not paths:
        print("Ses dosyasi bulunamadi.", file=sys.stderr)
        return 1
    readings = _read_many(paths, args.workers, False)
    profile = build_profile(readings, args.label)
    if args.out:
        save_profile(profile, args.out)
        print(f"Profil yazildi: {args.out}", file=sys.stderr)
    print(json.dumps(profile, indent=2, ensure_ascii=False))
    return 0 if profile.get("ok") else 2


def cmd_match(args) -> int:
    profile = load_profile(args.profile)
    paths = _collect(args.targets, args.limit)
    if not paths:
        print("Ses dosyasi bulunamadi.", file=sys.stderr)
        return 1
    readings = _read_many(paths, args.workers, args.json)
    matches = rank(readings, profile, require_tempo_window=not args.any_tempo)
    if args.json:
        print(json.dumps([m.as_dict() for m in matches], indent=2, ensure_ascii=False))
        return 0
    if not matches:
        print("Profile uyan ve tempo penceresine giren dosya yok.")
        return 0
    print(f"{'mesafe':>7}  {'chop':>6}  {'boyut':>5}  dosya")
    for m in matches[: args.top]:
        print(
            f"{m.distance:>7.3f}  {m.chop_bpm:>6.1f}  "
            f"{m.dimensions_used:>2}/{m.dimensions_total:<2}  {m.name}"
        )
    print(f"\n{len(matches)} / {len(readings)} dosya puanlandi (dusuk mesafe = daha yakin).")
    return 0


def cmd_spots(args) -> int:
    """Chop adaylarini bul, istenirse YouTube uzerinde loop'layan sayfa uret."""
    spots = find_spots(args.file, top=args.top)
    if not spots:
        print("Aday bulunamadi (dosya cok kisa olabilir).", file=sys.stderr)
        return 1
    print(f"{'baslangic':>10} {'bitis':>7} {'puan':>7}  gerekce")
    for s in spots:
        print(f"{s.start_s:>10.1f} {s.end_s:>7.1f} {s.score:>7.3f}  {s.reason}")
    if args.video:
        for url in watch_urls(args.video, spots):
            print(f"  {url}")
        if args.page:
            path = write_page(args.video, spots, args.page,
                              title=args.title or "Chop adaylari",
                              subtitle=f"{len(spots)} aday, her biri "
                                       f"{spots[0].end_s - spots[0].start_s:.0f} saniye.",
                              note=args.note or "")
            print(f"\nsayfa: {path}")
    return 0


def cmd_speed(args) -> int:
    """Ayni parcanin yuklemelerini sureye gore karsilastir."""
    uploads = json.loads(Path(args.uploads).read_text(encoding="utf-8"))
    rows, summary = speed_compare(uploads)
    if not summary.get("ok"):
        print(f"karsilastirilamadi: {summary.get('reason')}", file=sys.stderr)
        return 2
    ref = summary["reference_duration_s"]
    print(f"referans (medyan) sure {int(ref // 60)}:{int(ref % 60):02d}   "
          f"{summary['uploads']} yukleme, kumede {summary['in_cluster']}")
    print(f"\n{'cent':>6} {'karar':<9} {'sure':>6}  baslik")
    for r in rows:
        print(f"{r.cents:>+6.0f} {r.verdict:<9} "
              f"{int(r.duration_s // 60)}:{int(r.duration_s % 60):02d}  {r.title[:52]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="samplereader", description="Sesi olcer, ismi degil.")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--limit", type=int, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="Dosyalari oku ve olc")
    p_read.add_argument("targets", nargs="+")
    p_read.add_argument("--json", action="store_true")
    p_read.set_defaults(func=cmd_read)

    p_prof = sub.add_parser("profile", help="Kendi malzememizden referans profil cikar")
    p_prof.add_argument("targets", nargs="+")
    p_prof.add_argument("--label", default="unnamed")
    p_prof.add_argument("--out", default=None)
    p_prof.set_defaults(func=cmd_profile)

    p_match = sub.add_parser("match", help="Adaylari profile gore siralar")
    p_match.add_argument("profile")
    p_match.add_argument("targets", nargs="+")
    p_match.add_argument("--top", type=int, default=25)
    p_match.add_argument("--any-tempo", action="store_true", help="Tempo kapisini kapat")
    p_match.add_argument("--json", action="store_true")
    p_match.set_defaults(func=cmd_match)

    sp = sub.add_parser("spots", help="Chop adayi noktalari bul")
    sp.add_argument("file")
    sp.add_argument("--top", type=int, default=6)
    sp.add_argument("--video", default=None, help="YouTube video id -- link ve sayfa icin")
    sp.add_argument("--page", default=None, help="loop sayfasini buraya yaz")
    sp.add_argument("--title", default=None)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_spots)

    sd = sub.add_parser("speed", help="Yuklemeleri sureye gore karsilastir")
    sd.add_argument("uploads", help="[{title,id,duration}] iceren JSON")
    sd.set_defaults(func=cmd_speed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
