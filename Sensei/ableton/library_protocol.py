from __future__ import annotations

from pathlib import Path
from typing import Any

from ableton.ableton_index_provider import query_items as query_index_items
from ableton.inspector.profile_exporter import (
    build_kit_profile,
    inspect_alc_clip,
    inspect_alc_embedded_kit,
)
from ableton.library_scanner import scan_ableton_library


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _genre_match_details(item: dict[str, Any], genre: str | None) -> tuple[bool, str | None, str | None]:
    if not genre:
        return True, None, None

    normalized_genre = _normalized(genre)
    source_native = item.get("source_native") or {}
    native_genres = [str(value) for value in source_native.get("ableton_genres", []) if value is not None]
    normalized_native_genres = {_normalized(value) for value in native_genres if _normalized(value)}

    if normalized_native_genres:
        if normalized_genre in normalized_native_genres:
            return True, "source_native.ableton_genres", "high"
        return False, None, None
    # A folder/category is an organisational location, not a genre. Do not
    # promote it to a genre when Live did not assign a Genres|... metadata tag.
    return False, None, None


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _normalized(item.get("pack")),
            _normalized(item.get("folder_category") or item.get("category")),
            _normalized(item.get("name")),
            _normalized(item.get("path") or item.get("browser_path")),
        ),
    )


def _match_item_against_query_intent(item: dict[str, Any], tag: str | None, keywords: list[str], role_hint: str | None) -> bool:
    if tag:
        native_tags = [t.lower() for t in item.get("source_native", {}).get("ableton_tags", [])]
        if tag.lower() not in native_tags:
            return False
            
    if keywords:
        name_lower = item.get("name", "").lower()
        path_lower = item.get("path", "").lower()
        if not all(kw.lower() in name_lower or kw.lower() in path_lower for kw in keywords):
            return False
            
    if role_hint:
        role_hint_lower = role_hint.lower()
        name_lower = item.get("name", "").lower()
        path_lower = item.get("path", "").lower()
        category = (item.get("folder_category") or item.get("category") or "").lower()
        instrument = (item.get("instrument_hint") or "").lower()
        source_native = item.get("source_native", {})
        native_tags = [t.lower() for t in source_native.get("ableton_tags", []) if t]
        native_drums = source_native.get("ableton_drums", [])

        if role_hint_lower == "drums":
            is_drum = (
                category == "drums" or 
                instrument == "drum" or 
                native_drums or 
                any("drum" in t for t in native_tags) or
                item.get("is_drum_candidate") or
                any(k in path_lower for k in ["/clips/beats/", "/clips/drums/", "/construction kits/"]) or
                any(k in name_lower for k in ["drum", "kit", "beat", "groove", "perc", "snare", "kick", "hihat", "cymbal", "tom", "clap"])
            )
            if not is_drum:
                return False
        elif role_hint_lower == "bass":
            is_bass = (
                category == "bass" or 
                instrument == "bass" or 
                any("bass" in t for t in native_tags) or
                "/clips/bass/" in path_lower or
                "bass" in name_lower
            )
            if not is_bass:
                return False
        elif role_hint_lower == "chords":
            is_chords = (
                category in {"chords", "harmony", "keys", "synth"} or 
                instrument in {"keys", "synth"} or 
                any(t in native_tags for t in ["chords", "harmony", "keys", "synth", "piano", "pad"]) or
                any(k in path_lower for k in ["/clips/chords/", "/clips/harmony/", "/clips/keys/", "/clips/synth/"]) or
                any(k in name_lower for k in ["chord", "pad", "keys", "piano", "rhodes", "harmony", "synth"])
            )
            if not is_chords:
                return False

    return True


def query_library_items(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    tag = filters.get("tag")
    keywords = filters.get("keywords") or []
    role_hint = filters.get("role_hint")

    db_filters = {k: v for k, v in filters.items() if k not in {"tag", "keywords", "role_hint"}}
    try:
        index_items = query_index_items(db_filters)
    except Exception:
        index_items = []

    merged_items: dict[str, dict[str, Any]] = {}

    for item in index_items:
        if not _match_item_against_query_intent(item, tag, keywords, role_hint):
            continue
        enriched = dict(item)
        enriched["source"] = "ableton_live_db"
        enriched["query_source"] = "ableton_live_db"
        enriched["fallback_used"] = False
        
        # Ensure "genre" is exposed at the top-level when available
        if "genre" not in enriched or enriched["genre"] is None:
            native_genres = enriched.get("source_native", {}).get("ableton_genres", [])
            enriched["genre"] = native_genres[0] if native_genres else None
            
        path = enriched.get("path")
        if path:
            merged_items[str(Path(path).resolve())] = enriched

    library = scan_ableton_library()
    items = list(library.get("items", []))

    pack = filters.get("pack")
    content_type = filters.get("content_type")
    category = filters.get("category") or filters.get("folder_category")
    musical_category = filters.get("musical_category")
    instrument_hint = filters.get("instrument_hint")
    genre = filters.get("genre")

    for item in items:
        path = item.get("path")
        if not path:
            continue
        abs_path = str(Path(path).resolve())
        if abs_path in merged_items:
            continue

        if pack and item.get("pack") != pack:
            continue
        if content_type and item.get("content_type") != content_type:
            continue

        item_folder_category = item.get("folder_category") or item.get("category")
        if category and item_folder_category != category:
            continue

        if musical_category and _normalized(item.get("musical_category")) != _normalized(musical_category):
            continue
        if instrument_hint and _normalized(item.get("instrument_hint")) != _normalized(instrument_hint):
            continue

        if not _match_item_against_query_intent(item, tag, keywords, role_hint):
            continue

        genre_match, genre_source, genre_confidence = _genre_match_details(item, genre)
        if not genre_match:
            continue

        enriched_item = dict(item)
        enriched_item["source"] = "scanner_fallback"
        enriched_item["query_source"] = "scanner_fallback"
        enriched_item["fallback_used"] = True
        
        if "genre" not in enriched_item or enriched_item["genre"] is None:
            native_genres = enriched_item.get("source_native", {}).get("ableton_genres", [])
            enriched_item["genre"] = native_genres[0] if native_genres else None
            
        if genre:
            enriched_item["genre_match"] = {
                "source": genre_source,
                "confidence": genre_confidence,
            }

        merged_items[abs_path] = enriched_item

    return _sort_items(list(merged_items.values()))


def resolve_reference_clip(query: dict[str, Any] | None = None) -> dict[str, Any] | None:
    clip_query = dict(query or {})
    clip_query["content_type"] = "clip"
    clip_candidates = query_library_items(clip_query)
    if not clip_candidates:
        return None

    selected = next((item for item in clip_candidates if item.get("path")), clip_candidates[0])
    clip_path = selected.get("path")
    if not clip_path:
        return None

    clip_profile = inspect_alc_clip(selected["path"])
    return {
        "path": selected.get("path"),
        "pack": selected.get("pack"),
        "content_type": selected.get("content_type"),
        "folder_category": selected.get("folder_category") or selected.get("category"),
        "musical_category": selected.get("musical_category"),
        "instrument_hint": selected.get("instrument_hint"),
        "source_native": selected.get("source_native") or {},
        "source": selected.get("source", "scanner_fallback"),
        "query_source": selected.get("query_source", "scanner_fallback"),
        "fallback_used": bool(selected.get("fallback_used", True)),
        "notes_used": clip_profile.get("notes_used", []),
        "events": clip_profile.get("events", []),
        "events_source": clip_profile.get("events_source"),
    }


def resolve_kit_context(clip_path: str | Path, selected_kit_path: str | Path | None = None) -> dict[str, Any]:
    clip_path = Path(clip_path)
    selected_kit = Path(selected_kit_path) if selected_kit_path else None

    embedded_kit = inspect_alc_embedded_kit(clip_path)
    embedded_pads = embedded_kit.get("pads", {})
    if embedded_kit.get("pad_count", 0) > 0 and embedded_pads:
        notes = sorted(int(note) for note in embedded_pads.keys())
        return {
            "sound_source": "embedded_alc",
            "clip_path": str(clip_path),
            "selected_kit_path": str(selected_kit) if selected_kit else None,
            "resolved_kit_path": str(clip_path),
            "kit_profile": {
                "kit_id": "embedded_kit",
                "kit_name": clip_path.stem,
                "pads": embedded_pads,
            },
            "note_space": notes,
        }

    if selected_kit:
        kit_profile = build_kit_profile(selected_kit)
        notes = sorted(int(note) for note in kit_profile.get("pads", {}).keys())
        return {
            "sound_source": "adg_fallback",
            "clip_path": str(clip_path),
            "selected_kit_path": str(selected_kit),
            "resolved_kit_path": str(selected_kit),
            "kit_profile": kit_profile,
            "note_space": notes,
        }

    return {
        "sound_source": "none",
        "clip_path": str(clip_path),
        "selected_kit_path": None,
        "resolved_kit_path": None,
        "kit_profile": None,
        "note_space": [],
    }


def build_variation_context(reference_clip: dict[str, Any], kit_context: dict[str, Any]) -> dict[str, Any]:
    events = list(reference_clip.get("events", []))
    note_space = sorted(
        {
            int(event["note"])
            for event in events
            if isinstance(event, dict) and event.get("note") is not None
        }
        | {int(note) for note in reference_clip.get("notes_used", [])}
    )

    return {
        "reference": reference_clip,
        "kit_context": kit_context,
        "events": events,
        "note_space": note_space,
        "variation_contract": {
            "preserve": ["bar_length", "main_pulse", "kit_note_space"],
            "change": ["velocity", "density", "fills", "ghost_notes"],
        },
    }


def emit_diagnostics(context: dict[str, Any]) -> dict[str, Any]:
    reference = context.get("reference", {})
    kit_context = context.get("kit_context", {})
    events = context.get("events", [])

    clip_notes = sorted({int(note) for note in reference.get("notes_used", [])})
    kit_notes = sorted({int(note) for note in kit_context.get("note_space", [])})
    matched_notes = sorted(note for note in clip_notes if note in kit_notes)
    missing_notes = sorted(note for note in clip_notes if note not in kit_notes)

    return {
        "fallback_used": reference.get("events_source") != "midi_note_event",
        "sound_source": kit_context.get("sound_source", "none"),
        "selected_clip_path": reference.get("path") or kit_context.get("clip_path"),
        "selected_kit_path": kit_context.get("selected_kit_path"),
        "resolved_kit_path": kit_context.get("resolved_kit_path"),
        "matched_notes": matched_notes,
        "missing_notes": missing_notes,
        "all_notes_matched": bool(clip_notes) and not missing_notes,
        "event_count": len(events),
        "note_count": len(clip_notes),
    }


def remap_drum_events(
    events: list[dict[str, Any]],
    clip_path: str | Path,
    target_kit_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Remaps drum MIDI events from a reference clip to either a target kit profile's notes
    or standard General MIDI notes based on pad semantic roles.
    """
    clip_path = Path(clip_path)

    # 1. Resolve source notes' roles by inspecting clip's embedded kit
    try:
        embedded_kit = inspect_alc_embedded_kit(clip_path)
    except Exception:
        embedded_kit = {}
    embedded_pads = embedded_kit.get("pads", {})

    # 2. Define standard role assignments for target notes
    DEFAULT_TARGET_GM = {
        "kick": 36,
        "snare": 38,
        "clap": 39,
        "closed_hat": 42,
        "open_hat": 46,
        "hat": 42,
        "tom": 43,
        "perc": 37,
        "cymbal": 49,
    }

    # 3. Resolve target note lookup by role
    target_role_to_note = {}
    if target_kit_profile and target_kit_profile.get("pads"):
        for note_str, pad in target_kit_profile["pads"].items():
            role = pad.get("normalized_role")
            if role and role != "unknown_pad":
                if role not in target_role_to_note:
                    target_role_to_note[role] = []
                target_role_to_note[role].append((pad.get("confidence", 0.0), -int(note_str), int(note_str)))

        # Pick the best note per role
        best_target_by_role = {}
        for role, candidates in target_role_to_note.items():
            candidates.sort(reverse=True)
            best_target_by_role[role] = candidates[0][2]
    else:
        # Fall back to standard General MIDI assignments
        best_target_by_role = DEFAULT_TARGET_GM

    role_fallbacks = {
        "closed_hat": ["hat", "open_hat"],
        "open_hat": ["hat", "closed_hat"],
        "hat": ["closed_hat", "open_hat"],
        "clap": ["snare"],
        "snare": ["clap"],
        "tom": ["perc"],
        "perc": ["tom"],
    }

    def get_target_note(role: str | None) -> int | None:
        if not role:
            return None
        if role in best_target_by_role:
            return best_target_by_role[role]
        for fb in role_fallbacks.get(role, []):
            if fb in best_target_by_role:
                return best_target_by_role[fb]
        return None

    # General MIDI standard note-to-role mappings if clip lacks detailed pad roles
    DEFAULT_GM_ROLES = {
        35: "kick", 36: "kick",
        38: "snare", 40: "snare",
        39: "clap",
        42: "closed_hat", 44: "closed_hat",
        46: "open_hat",
        41: "tom", 43: "tom", 45: "tom", 47: "tom", 48: "tom", 50: "tom",
        49: "cymbal", 51: "cymbal", 52: "cymbal", 55: "cymbal", 57: "cymbal",
        37: "perc", 56: "perc", 58: "perc",
    }

    # 4. Perform the remapping
    remapped_events = []
    for ev in events:
        orig_note = int(ev["note"])

        # Resolve source role
        role = None
        orig_note_str = str(orig_note)
        if orig_note_str in embedded_pads:
            role = embedded_pads[orig_note_str].get("normalized_role")
            if role == "unknown_pad":
                role = None

        if not role:
            role = DEFAULT_GM_ROLES.get(orig_note)

        # Target note mapping
        target_note = get_target_note(role)
        if target_note is None:
            # Keep original note if it couldn't be resolved or fallback doesn't exist
            target_note = orig_note

        new_ev = dict(ev)
        new_ev["note"] = target_note
        remapped_events.append(new_ev)

    return remapped_events
