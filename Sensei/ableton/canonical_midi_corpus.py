"""Build the immutable, parse-verified MIDI reference corpus for Sensei."""

from __future__ import annotations

import hashlib
import gc
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ableton.inspector.profile_exporter import inspect_alc_clip
from ableton.inspector.midi_reader import read_midi_events
from ableton.variation_corpus import build_clean_variation_corpus


SCHEMA_VERSION = "sensei.canonical-midi-clip.v1"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "canonical_midi_corpus"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for event in events:
        pitch = int(event["note"])
        time = float(event["beat"])
        duration = float(event.get("duration", 0.25))
        velocity = int(event["velocity"])
        if not (0 <= pitch <= 127 and time >= 0 and duration > 0 and 1 <= velocity <= 127):
            raise ValueError("MIDI event is outside the canonical safety bounds")
        normalized.append({"pitch": pitch, "time": time, "duration": duration, "velocity": velocity})
    return sorted(normalized, key=lambda event: (event["time"], event["pitch"], event["duration"]))


def _timeline(profile: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    boundaries = profile.get("clip_boundaries") or {}
    loop_start = boundaries.get("loop_start")
    loop_end = boundaries.get("loop_end")
    if loop_start is None or loop_end is None or loop_end <= loop_start:
        loop_start = 0.0
        loop_end = max((event["time"] + event["duration"] for event in events), default=0.0)
    cycle_beats = float(loop_end) - float(loop_start)
    if cycle_beats <= 0:
        raise ValueError("MIDI clip has no usable timeline")
    return {
        "loop_start": float(loop_start),
        "loop_end": float(loop_end),
        "cycle_beats": cycle_beats,
        "time_signature": None,
        "tempo_bpm": None,
    }


def build_canonical_midi_corpus(
    roots: Iterable[str | Path] | None = None,
    *,
    index_items: Iterable[dict[str, Any]] | None = None,
    existing_entries: Iterable[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build canonical clips from the existing strict native-genre input policy.

    The clean variation corpus remains the admission filter. A candidate is
    additionally rejected unless its actual MIDI events can be parsed; synthetic
    fallbacks are never admitted to this dataset release.
    """
    sources, source_manifest = build_clean_variation_corpus(roots, index_items=index_items)
    entries: list[dict[str, Any]] = list(existing_entries or [])
    audit: Counter[str] = Counter(source_manifest.get("audit") or {})
    seen_content: set[str] = {str((entry.get("integrity") or {}).get("content_sha256")) for entry in entries}

    for source in sources:
        path = Path(source["path"])
        try:
            content_sha256 = _sha256(path)
            if content_sha256 in seen_content:
                audit["duplicate_content"] += 1
                continue
            if path.suffix.lower() == ".alc":
                profile = inspect_alc_clip(path)
                if profile.get("events_source") != "midi_note_event":
                    audit["noncanonical_event_parse"] += 1
                    continue
            else:
                profile = {"events_source": "midi_note_event", "events": read_midi_events(path), "clip_boundaries": {}}
            events = _canonical_events(profile.get("events") or [])
            if not events:
                audit["empty_events"] += 1
                continue
            timeline = _timeline(profile, events)
            seen_content.add(content_sha256)
        except (OSError, ValueError):
            audit["parse_failed"] += 1
            continue
        except Exception:
            # The release is fail-closed: unexpected parser failures never add a clip.
            audit["parse_failed"] += 1
            continue

        canonical_entry = {
            "schema_version": SCHEMA_VERSION,
            "reference_id": source["reference_id"],
            "name": path.stem,
            "path": str(path.resolve()),
            "pack": source.get("pack"),
            "content_type": "midi_clip",
            "source_native": dict(source.get("source_native") or {}),
            "genres": list(source.get("genres") or []),
            "genre_source": source.get("genre_source"),
            "integrity": {"content_sha256": content_sha256, "parse_status": "verified"},
            "timeline": timeline,
            "events": events,
            "capabilities": {"reference": True, "variation": True, "sdk_write": True},
        }
        # Carried straight from the variation-corpus source entry -- key
        # evidence is an Ableton browser tag, not something re-derived from
        # the parsed events here.
        if source.get("key_root") and source.get("key_mode"):
            canonical_entry["key_root"] = source["key_root"]
            canonical_entry["key_mode"] = source["key_mode"]
        entries.append(canonical_entry)
        audit["verified"] += 1
        if audit["verified"] % 32 == 0:
            gc.collect()

    entries.sort(key=lambda entry: (entry["genres"], str(entry["pack"] or ""), entry["path"]))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "source_corpus_schema_version": source_manifest["schema_version"],
        "selection_policy": {
            "require_clean_variation_source": True,
            "require_actual_midi_note_events": True,
            "allow_synthetic_event_fallback": False,
            "require_source_content_sha256": True,
        },
        "audit": dict(sorted(audit.items())),
    }
    return entries, manifest


def write_canonical_midi_corpus(
    output_directory: str | Path | None = None,
    *,
    roots: Iterable[str | Path] | None = None,
    index_items: Iterable[dict[str, Any]] | None = None,
    resume_existing: bool = False,
) -> dict[str, Any]:
    output = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY).expanduser().resolve()
    corpus_path = output / "canonical_midi_clips.jsonl"
    existing_entries = None
    if resume_existing and corpus_path.is_file():
        existing_entries = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries, manifest = build_canonical_midi_corpus(roots, index_items=index_items, existing_entries=existing_entries)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "canonical_midi_clips.manifest.json"
    corpus_temp = corpus_path.with_suffix(".jsonl.tmp")
    manifest_temp = manifest_path.with_suffix(".json.tmp")
    with corpus_temp.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    with manifest_temp.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(corpus_temp, corpus_path)
    os.replace(manifest_temp, manifest_path)
    return {"corpus_path": str(corpus_path), "manifest_path": str(manifest_path), "entry_count": len(entries), "entries": entries}
