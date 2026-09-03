"""Build a genre-safe MIDI variation corpus from Ableton's own metadata."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from ableton.ableton_index_provider import query_items
from ableton.library_scanner import DEFAULT_LIBRARY_ROOTS
from ableton.inspector.midi_reader import read_midi_events


SCHEMA_VERSION = "sensei.clean-variation-corpus.v1"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "variation_corpus"
_ROOT_PATTERN = re.compile(r"^[A-G](#|b)?$")


def _clean_key(tags: Any) -> tuple[str | None, str | None]:
    """Only a clean [Root, Major|Minor] Ableton key tag counts as evidence."""
    if not isinstance(tags, list) or len(tags) != 2:
        return None, None
    root = str(tags[0]).strip().replace("♯", "#").replace("♭", "b")
    mode = str(tags[1]).strip()
    if not _ROOT_PATTERN.match(root) or mode not in ("Major", "Minor"):
        return None, None
    return root, mode


def _read_alc_payload(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        return gzip.decompress(raw)
    except (OSError, EOFError):
        return raw


def _midi_note_event_count(path: Path) -> int | None:
    """Return real MIDI event count, or ``None`` when the clip is not MIDI-only."""
    try:
        payload = _read_alc_payload(path)
    except OSError:
        return None

    has_midi = b"MidiClip" in payload
    has_audio = b"AudioClip" in payload
    if not has_midi or has_audio:
        return None
    return payload.count(b"MidiNoteEvent")


def _native_midi_event_count(path: Path) -> int | None:
    try:
        events = read_midi_events(path)
    except Exception:
        # Library scans must be resilient to any malformed third-party MIDI
        # file. mido raises several parser-specific exceptions (for example
        # KeySignatureError) that do not share one public base class.
        return None
    return len(events) or None


def _index_by_resolved_path(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        path = item.get("path")
        if not path:
            continue
        indexed[str(Path(path).resolve())] = item
    return indexed


def build_clean_variation_corpus(
    root_paths: Iterable[str | Path] | None = None,
    *,
    index_items: Iterable[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return only MIDI clips with a native Ableton genre tag.

    Genre is never inferred from a path, a filename, or a model. This is the
    boundary that lets downstream generation trust every genre-filtered source.
    """
    live_items = list(index_items) if index_items is not None else query_items({"content_type": "clip", "limit": 100_000})
    live_by_path = _index_by_resolved_path(live_items)
    roots = [Path(path).expanduser().resolve() for path in (root_paths or DEFAULT_LIBRARY_ROOTS)]
    if root_paths is None:
        candidates = sorted(Path(path) for path in live_by_path if Path(path).suffix.lower() in {".alc", ".mid", ".midi"})
        roots_for_manifest = sorted({str(path.parent) for path in candidates})
    else:
        candidates = sorted({path for root in roots if root.exists() for suffix in ("*.alc", "*.mid", "*.midi") for path in root.rglob(suffix)})
        roots_for_manifest = [str(root) for root in roots]

    entries: list[dict[str, Any]] = []
    audit = Counter()
    seen_content: set[str] = set()
    for path in candidates:
        audit[f"{path.suffix.lower().lstrip('.')}_files_seen"] += 1
        note_event_count = _midi_note_event_count(path) if path.suffix.lower() == ".alc" else _native_midi_event_count(path)
        if note_event_count is None:
            audit["not_midi_only"] += 1
            continue
        audit["midi_clips_seen"] += 1

        item = live_by_path.get(str(path.resolve()))
        if item is None:
            audit["missing_live_browser_record"] += 1
            continue
        native = item.get("source_native") or {}
        genres = sorted({str(value).strip() for value in native.get("ableton_genres") or [] if str(value).strip()})
        if not genres:
            audit["missing_native_genre"] += 1
            continue
        try:
            content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            audit["read_failed"] += 1
            continue
        if content_sha256 in seen_content:
            audit["duplicate_content"] += 1
            continue
        seen_content.add(content_sha256)
        key_root, key_mode = _clean_key(native.get("ableton_keys"))

        audit["included"] += 1
        entry = {
            "schema_version": SCHEMA_VERSION,
            "reference_id": item.get("reference_id"),
            "path": str(path.resolve()),
            "pack": item.get("pack"),
            "content_type": "midi_clip",
            "midi_note_event_count": note_event_count,
            "content_sha256": content_sha256,
            "genres": genres,
            "genre_source": "ableton_live_browser_metadata",
            "source_native": {
                "ableton_genres": genres,
                "ableton_tags": list(native.get("ableton_tags") or []),
                "ableton_types": list(native.get("ableton_types") or []),
                "ableton_drums": list(native.get("ableton_drums") or []),
            },
        }
        if key_root and key_mode:
            entry["key_root"] = key_root
            entry["key_mode"] = key_mode
        entries.append(entry)

    entries.sort(key=lambda entry: (entry["genres"], str(entry["pack"] or ""), entry["path"]))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_roots": roots_for_manifest,
        "selection_policy": {
            "require_midi_clip": True,
            "require_midi_note_event": True,
            "require_ableton_native_genre": True,
            "disallowed_genre_sources": ["filename", "path", "folder", "heuristic", "llm"],
        },
        "entry_count": len(entries),
        "genre_assignment_count": sum(len(entry["genres"]) for entry in entries),
        "genre_counts": dict(sorted(Counter(genre for entry in entries for genre in entry["genres"]).items())),
        "audit": dict(sorted(audit.items())),
    }
    return entries, manifest


def write_clean_variation_corpus(output_directory: str | Path | None = None) -> dict[str, Any]:
    """Atomically write the clean corpus and its audit manifest."""
    entries, manifest = build_clean_variation_corpus()
    output = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    corpus_path = output / "clean_midi_variation_corpus.jsonl"
    manifest_path = output / "clean_midi_variation_corpus.manifest.json"
    corpus_temp = corpus_path.with_suffix(".jsonl.tmp")
    manifest_temp = manifest_path.with_suffix(".json.tmp")

    with corpus_temp.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    with manifest_temp.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    corpus_temp.replace(corpus_path)
    manifest_temp.replace(manifest_path)
    return {"corpus_path": str(corpus_path), "manifest_path": str(manifest_path), **manifest}
