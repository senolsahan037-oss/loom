from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ableton.ableton_index_provider import health as db_health
from ableton.ableton_index_provider import query_items as db_query_items
from ableton.library_protocol import query_library_items
from ableton.library_scanner import scan_ableton_library
from ableton.canonical_midi_corpus import write_canonical_midi_corpus
from ableton.bass_instrument_catalog import write_bass_instrument_catalog
from ableton.chord_instrument_catalog import write_chord_instrument_catalog
from ableton.dataset_release import write_dataset_release_manifest
from ableton.groove_corpus import write_groove_catalog
from ableton.instrument_capabilities import write_instrument_capability_catalog
from ableton.variation_corpus import write_clean_variation_corpus


ROOT = Path(__file__).resolve().parents[1]


def _print_scan_summary() -> int:
    library = scan_ableton_library()
    items = library.get("items", [])
    kits = library.get("kits", [])
    clips = library.get("clips", [])
    preview_audios = library.get("preview_audios", [])
    samples = library.get("samples", [])
    sets = library.get("sets", [])
    instruments = library.get("instruments", [])
    grooves = library.get("grooves", [])
    midi_references = library.get("midi_references", [])
    max_devices = library.get("max_devices", [])

    print("Ableton Library Scan Summary")
    print(f"packs: {len({item.get('pack') for item in items if item.get('pack')})}")
    print(f"items: {len(items)}")
    print(f"kits: {len(kits)}")
    print(f"clips: {len(clips)}")
    print(f"audio: {len(preview_audios) + len(samples)}")
    print(f"sets: {len(sets)}")
    print(f"instruments: {len(instruments)}")
    print(f"grooves: {len(grooves)}")
    print(f"midi_references: {len(midi_references)}")
    print(f"max_devices: {len(max_devices)}")
    return 0


def _print_query_results(args: argparse.Namespace) -> int:
    filters: dict[str, Any] = {}
    if args.pack:
        filters["pack"] = args.pack
    if args.category:
        filters["category"] = args.category
    if args.content_type:
        filters["content_type"] = args.content_type
    if args.instrument:
        # Match protocol field name
        filters["instrument_hint"] = args.instrument
    if args.genre:
        filters["genre"] = args.genre

    results = query_library_items(filters)
    print(f"query_filters: {json.dumps(filters, ensure_ascii=False)}")
    print(f"result_count: {len(results)}")

    for item in results[:20]:
        category = item.get("folder_category") or item.get("category") or "unknown"
        genre_match = item.get("genre_match") or {}
        genre_match_source = str(genre_match.get("source") or "")
        genre_match_confidence = str(genre_match.get("confidence") or "")
        genre_match_text = ""
        if genre_match_source or genre_match_confidence:
            genre_match_text = f" | genre_match:{genre_match_source}/{genre_match_confidence}"

        print(
            " | ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("pack", "")),
                    str(category),
                    str(item.get("content_type", "")),
                    f"{item.get('path', '')}{genre_match_text}",
                ]
            )
        )

    return 0


def _print_db_health() -> int:
    result = db_health()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _print_db_query_results(args: argparse.Namespace) -> int:
    filters: dict[str, Any] = {}
    if args.pack:
        filters["pack"] = args.pack
    if args.content_type:
        filters["content_type"] = args.content_type
    if args.genre:
        filters["genre"] = args.genre

    results = db_query_items(filters)
    print(f"db_query_filters: {json.dumps(filters, ensure_ascii=False)}")
    print(f"result_count: {len(results)}")
    for item in results[:20]:
        print(
            " | ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("pack", "")),
                    str(item.get("content_type", "")),
                    str(item.get("path") or item.get("browser_path") or ""),
                    str(item.get("reference_id", "")),
                ]
            )
        )
    return 0


def _build_clean_variation_corpus(args: argparse.Namespace) -> int:
    result = write_clean_variation_corpus(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _build_groove_catalog(args: argparse.Namespace) -> int:
    result = write_groove_catalog(args.output)
    result.pop("entries", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _build_phase_one_dataset_release(args: argparse.Namespace) -> int:
    """Build and pin the three immutable Phase 1 data artifacts."""
    variation = write_clean_variation_corpus()
    grooves = write_groove_catalog()
    canonical = write_canonical_midi_corpus()
    release_output = args.output or str(ROOT / "data" / "dataset_releases" / "phase1")
    release = write_dataset_release_manifest(
        release_output,
        artifacts={
            "variation_sources": variation["corpus_path"],
            "canonical_midi": canonical["corpus_path"],
            "grooves": grooves["catalog_path"],
        },
    )
    print(json.dumps({
        "variation_entry_count": variation["entry_count"],
        "canonical_entry_count": canonical["entry_count"],
        "groove_entry_count": grooves["entry_count"],
        "release_manifest_path": release["manifest_path"],
    }, ensure_ascii=False, indent=2))
    return 0


def _build_phase_two_dataset_release(args: argparse.Namespace) -> int:
    capabilities = write_instrument_capability_catalog()
    data_root = ROOT / "data"
    release_output = args.output or str(data_root / "dataset_releases" / "phase2")
    release = write_dataset_release_manifest(
        release_output,
        artifacts={
            "variation_sources": data_root / "variation_corpus" / "clean_midi_variation_corpus.jsonl",
            "canonical_midi": data_root / "canonical_midi_corpus" / "canonical_midi_clips.jsonl",
            "grooves": data_root / "groove_corpus" / "ableton_groove_catalog.jsonl",
            "instrument_capabilities": capabilities["catalog_path"],
        },
    )
    print(json.dumps({
        "instrument_capability_entry_count": capabilities["entry_count"],
        "release_manifest_path": release["manifest_path"],
    }, ensure_ascii=False, indent=2))
    return 0


def _build_phase_four_dataset_release(args: argparse.Namespace) -> int:
    capabilities = write_instrument_capability_catalog()
    bass_instruments = write_bass_instrument_catalog()
    data_root = ROOT / "data"
    release_output = args.output or str(data_root / "dataset_releases" / "phase4")
    release = write_dataset_release_manifest(
        release_output,
        artifacts={
            "variation_sources": data_root / "variation_corpus" / "clean_midi_variation_corpus.jsonl",
            "canonical_midi": data_root / "canonical_midi_corpus" / "canonical_midi_clips.jsonl",
            "grooves": data_root / "groove_corpus" / "ableton_groove_catalog.jsonl",
            "instrument_capabilities": capabilities["catalog_path"],
            "bass_instruments": bass_instruments["catalog_path"],
        },
    )
    print(json.dumps({
        "instrument_capability_entry_count": capabilities["entry_count"],
        "bass_instrument_entry_count": bass_instruments["entry_count"],
        "release_manifest_path": release["manifest_path"],
    }, ensure_ascii=False, indent=2))
    return 0


def _build_phase_five_dataset_release(args: argparse.Namespace) -> int:
    capabilities = write_instrument_capability_catalog()
    bass_instruments = write_bass_instrument_catalog()
    chord_instruments = write_chord_instrument_catalog()
    data_root = ROOT / "data"
    release_output = args.output or str(data_root / "dataset_releases" / "phase5")
    release = write_dataset_release_manifest(
        release_output,
        artifacts={
            "variation_sources": data_root / "variation_corpus" / "clean_midi_variation_corpus.jsonl",
            "canonical_midi": data_root / "canonical_midi_corpus" / "canonical_midi_clips.jsonl",
            "grooves": data_root / "groove_corpus" / "ableton_groove_catalog.jsonl",
            "instrument_capabilities": capabilities["catalog_path"],
            "bass_instruments": bass_instruments["catalog_path"],
            "chord_instruments": chord_instruments["catalog_path"],
        },
    )
    print(json.dumps({
        "instrument_capability_entry_count": capabilities["entry_count"],
        "bass_instrument_entry_count": bass_instruments["entry_count"],
        "chord_instrument_entry_count": chord_instruments["entry_count"],
        "release_manifest_path": release["manifest_path"],
    }, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sensei Library CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="Scan Ableton library and print counts")
    subparsers.add_parser("db-health", help="Show Ableton Live Database health")
    corpus_parser = subparsers.add_parser(
        "build-clean-variation-corpus",
        help="Build MIDI variation corpus using only Live Browser genre metadata",
    )
    corpus_parser.add_argument("--output", default=None, help="Output directory (optional)")
    groove_parser = subparsers.add_parser(
        "build-groove-catalog",
        help="Build a parse-verified Ableton .agr catalog and integrity manifest",
    )
    groove_parser.add_argument("--output", default=None, help="Output directory (optional)")
    phase_one_parser = subparsers.add_parser(
        "build-phase1-dataset-release",
        help="Build canonical MIDI, groove and variation artifacts, then pin their hashes in one release manifest",
    )
    phase_one_parser.add_argument("--output", default=None, help="Release manifest directory (optional)")
    phase_two_parser = subparsers.add_parser(
        "build-phase2-dataset-release",
        help="Add instrument capability profiles and pin the Phase 2 dataset release",
    )
    phase_two_parser.add_argument("--output", default=None, help="Release manifest directory (optional)")
    phase_four_parser = subparsers.add_parser(
        "build-phase4-dataset-release",
        help="Build native Ableton bass-instrument bindings and pin the Phase 4 dataset release",
    )
    phase_four_parser.add_argument("--output", default=None, help="Release manifest directory (optional)")
    phase_five_parser = subparsers.add_parser(
        "build-phase5-dataset-release",
        help="Build native Ableton chord-instrument bindings and pin the Phase 5 dataset release",
    )
    phase_five_parser.add_argument("--output", default=None, help="Release manifest directory (optional)")

    query_parser = subparsers.add_parser("query", help="Query library items with filters")
    query_parser.add_argument("--pack", default="", help="Pack name filter")
    query_parser.add_argument("--category", default="", help="Category/folder_category filter")
    query_parser.add_argument("--content-type", default="", help="content_type filter")
    query_parser.add_argument("--instrument", default="", help="instrument_hint filter")
    query_parser.add_argument("--genre", default="", help="genre filter (source_native.ableton_genres)")

    db_query_parser = subparsers.add_parser("db-query", help="Query Ableton Live Database items")
    db_query_parser.add_argument("--pack", default="", help="Pack name filter")
    db_query_parser.add_argument("--content-type", default="", help="content_type filter")
    db_query_parser.add_argument("--genre", default="", help="Genre filter")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "scan":
        return _print_scan_summary()
    if args.command == "db-health":
        return _print_db_health()
    if args.command == "query":
        return _print_query_results(args)
    if args.command == "db-query":
        return _print_db_query_results(args)
    if args.command == "build-clean-variation-corpus":
        return _build_clean_variation_corpus(args)
    if args.command == "build-groove-catalog":
        return _build_groove_catalog(args)
    if args.command == "build-phase1-dataset-release":
        return _build_phase_one_dataset_release(args)
    if args.command == "build-phase2-dataset-release":
        return _build_phase_two_dataset_release(args)
    if args.command == "build-phase4-dataset-release":
        return _build_phase_four_dataset_release(args)
    if args.command == "build-phase5-dataset-release":
        return _build_phase_five_dataset_release(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
