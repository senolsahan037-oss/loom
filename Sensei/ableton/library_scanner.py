from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ableton.ableton_metadata import scan_ableton_folder_info

DEFAULT_LIBRARY_ROOTS = [
    Path.home() / "Music" / "Ableton" / "Factory Packs",
    Path.home() / "Music" / "Ableton" / "User Library",
]
# Keep this list to source assets that can inform a musical decision. Live's
# analysis/cache sidecars (.asd and .pkf) are deliberately excluded.
SUPPORTED_SUFFIXES = (
    ".adg",  # Live device / rack preset
    ".adv",  # Live device preset
    ".alc",  # Live clip
    ".als",  # Live set
    ".agr",  # Groove pool preset
    ".amxd", # Max for Live device
    ".mid",  # MIDI reference material
    ".aif", ".aiff", ".wav", ".ogg",  # audio assets
)


def _reference_id(path_str: str) -> str:
    return hashlib.sha1(f"ableton:{path_str}".encode("utf-8")).hexdigest()


def _pack_name_for(path: Path) -> str:
    parts = path.parts
    if "Factory Packs" in parts:
        index = parts.index("Factory Packs")
        if index + 1 < len(parts):
            return parts[index + 1]
    if "User Library" in parts:
        return "User Library"

    try:
        relative_parts = path.relative_to(Path.home() / "Music" / "Ableton")
    except ValueError:
        return "Unknown"

    parts = relative_parts.parts
    if len(parts) >= 2 and parts[0] == "Factory Packs":
        return parts[1]
    if parts[0] == "User Library":
        return "User Library"

    return "Unknown"


def _normalized_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _normalized_path_text(path_text: str) -> str:
    return path_text.replace("\\", "/").strip().lower()


def _contains_segment(path_text: str, segment: str) -> bool:
    return f"/{segment.lower()}/" in path_text.lower()


def _classify_item(path: Path) -> dict[str, Any]:
    path_text = _normalized_path(path)
    path_lower = path_text.lower()
    extension = path.suffix.lower()
    file_name_lower = path.name.lower()

    folder_category = "unknown"
    if _contains_segment(path_text, "Drums"):
        folder_category = "Drums"
    elif _contains_segment(path_text, "MIDI Clips"):
        folder_category = "MIDI Clips"
    elif _contains_segment(path_text, "Clips"):
        folder_category = "Clips"
    elif _contains_segment(path_text, "Construction Kits"):
        folder_category = "Construction Kits"
    elif _contains_segment(path_text, "Demo Sets"):
        folder_category = "Demo Sets"
    elif _contains_segment(path_text, "Sounds"):
        folder_category = "Sounds"
    elif "user library" in path_lower:
        folder_category = "User Library"

    instrument_hint = "unknown"
    if _contains_segment(path_text, "Sounds/Bass") or "bass" in path_lower:
        instrument_hint = "bass"
    elif "synth" in path_lower:
        instrument_hint = "synth"
    elif "keys" in path_lower or "piano" in path_lower:
        instrument_hint = "keys"
    elif "perc" in path_lower or "percussion" in path_lower:
        instrument_hint = "perc"
    elif (
        _contains_segment(path_text, "Drums")
        or "drum" in path_lower
        or "kick" in path_lower
        or "snare" in path_lower
        or "hat" in path_lower
    ):
        instrument_hint = "drum"

    content_type = "unknown"
    if extension == ".als":
        content_type = "set"
    elif extension == ".alc" and (_contains_segment(path_text, "MIDI Clips") or _contains_segment(path_text, "Clips")):
        content_type = "clip"
    elif extension in {".aif", ".aiff", ".wav", ".ogg"}:
        content_type = "sample" if _contains_segment(path_text, "Samples") else "preview_audio"
    elif extension == ".adg" and (_contains_segment(path_text, "Drums") or file_name_lower == "kit.adg"):
        content_type = "kit"
        instrument_hint = "drum"
    elif extension == ".adg" and _contains_segment(path_text, "Sounds") and not _contains_segment(path_text, "Drums"):
        content_type = "instrument"
    elif extension == ".adg":
        content_type = "device_rack"
    elif extension == ".adv":
        content_type = "instrument_preset"
    elif extension == ".amxd":
        content_type = "max_device"
    elif extension == ".agr":
        content_type = "groove"
    elif extension == ".mid":
        content_type = "midi_reference"

    is_user_library = "user library" in path_lower
    has_drum_keywords = any(keyword in path_lower for keyword in ("drum", "kick", "snare", "hat"))
    is_drum_candidate = bool(
        content_type == "kit"
        or instrument_hint in {"drum", "perc"}
        or (is_user_library and has_drum_keywords)
    )

    musical_category = "unknown"
    if is_drum_candidate or folder_category == "Drums":
        musical_category = "Drums"
    elif instrument_hint == "bass":
        musical_category = "Bass"
    elif instrument_hint == "synth":
        musical_category = "Synth"
    elif instrument_hint == "keys":
        musical_category = "Keys"
    elif instrument_hint == "perc":
        musical_category = "Percussion"

    return {
        "extension": extension,
        "content_type": content_type,
        "category": folder_category,
        "folder_category": folder_category,
        "musical_category": musical_category,
        "instrument_hint": instrument_hint,
        "is_drum_candidate": is_drum_candidate,
    }


def _empty_source_native() -> dict[str, Any]:
    return {
        "ableton_file_path": None,
        "ableton_keywords": [],
        "ableton_genres": [],
        "ableton_types": [],
        "ableton_drums": [],
        "ableton_characters": [],
        "ableton_keys": [],
        "ableton_sounds": [],
    }


def _metadata_matcher(metadata_map: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    exact_matches: dict[str, dict[str, Any]] = {}
    suffix_matches: list[tuple[str, dict[str, Any]]] = []

    for metadata in metadata_map.values():
        file_path = str(metadata.get("file_path") or "").strip()
        if not file_path:
            continue
        normalized = _normalized_path_text(file_path)
        exact_matches[normalized] = metadata
        suffix_matches.append((normalized.lstrip("/"), metadata))

    suffix_matches.sort(key=lambda entry: len(entry[0]), reverse=True)
    return exact_matches, suffix_matches


def _resolve_source_native(
    item_path: Path,
    exact_matches: dict[str, dict[str, Any]],
    suffix_matches: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    normalized_item_path = _normalized_path_text(str(item_path))
    metadata = exact_matches.get(normalized_item_path)

    if metadata is None:
        for suffix, candidate in suffix_matches:
            if not suffix:
                continue
            if normalized_item_path == suffix or normalized_item_path.endswith(f"/{suffix}"):
                metadata = candidate
                break

    if not metadata:
        return _empty_source_native()

    return {
        "ableton_file_path": metadata.get("file_path") or None,
        "ableton_keywords": list(metadata.get("keywords") or []),
        "ableton_genres": list(metadata.get("genres") or []),
        "ableton_types": list(metadata.get("types") or []),
        "ableton_drums": list(metadata.get("drums") or []),
        "ableton_characters": list(metadata.get("characters") or []),
        "ableton_keys": list(metadata.get("keys") or []),
        "ableton_sounds": list(metadata.get("sounds") or []),
    }


def _index_files(
    root: Path,
    suffixes: tuple[str, ...],
    exact_matches: dict[str, dict[str, Any]],
    suffix_matches: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    if not root.exists():
        return indexed

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        classification = _classify_item(path)
        source_native = _resolve_source_native(path, exact_matches, suffix_matches)
        path_str = str(path)
        native_genres = source_native.get("ableton_genres") or []
        indexed.append(
            {
                "reference_id": _reference_id(path_str),
                "name": path.stem,
                "path": path_str,
                "pack": _pack_name_for(path),
                **classification,
                "genre": native_genres[0] if native_genres else None,
                "source_native": source_native,
                "derived": {
                    "musical_category": classification.get("musical_category"),
                    "instrument_hint": classification.get("instrument_hint"),
                    "confidence": "fallback_path_heuristic",
                },
                **({"bpm_hint": ""} if suffixes == (".alc",) else {}),
            }
        )

    return indexed


def scan_ableton_library(root_paths: list[Path] | None = None) -> dict[str, list[dict[str, Any]]]:
    roots = [Path(root).expanduser().resolve() for root in (root_paths or DEFAULT_LIBRARY_ROOTS)]
    items = []

    for root in roots:
        metadata_map = scan_ableton_folder_info(root)
        exact_matches, suffix_matches = _metadata_matcher(metadata_map)
        items.extend(_index_files(root, SUPPORTED_SUFFIXES, exact_matches, suffix_matches))

    sorted_items = sorted(items, key=lambda item: item["name"].lower())
    kits = [item for item in sorted_items if item["content_type"] == "kit" and item["is_drum_candidate"]]
    clips = [item for item in sorted_items if item["content_type"] == "clip"]
    sets = [item for item in sorted_items if item["content_type"] == "set"]
    preview_audios = [item for item in sorted_items if item["content_type"] == "preview_audio"]
    samples = [item for item in sorted_items if item["content_type"] == "sample"]
    instruments = [
        item for item in sorted_items
        if item["content_type"] in {"instrument", "instrument_preset", "device_rack"}
    ]
    grooves = [item for item in sorted_items if item["content_type"] == "groove"]
    midi_references = [item for item in sorted_items if item["content_type"] == "midi_reference"]
    max_devices = [item for item in sorted_items if item["content_type"] == "max_device"]

    return {
        "items": sorted_items,
        "kits": kits,
        "clips": clips,
        "sets": sets,
        "preview_audios": preview_audios,
        "samples": samples,
        "instruments": instruments,
        "grooves": grooves,
        "midi_references": midi_references,
        "max_devices": max_devices,
    }
