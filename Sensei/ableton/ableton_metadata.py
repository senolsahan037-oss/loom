from __future__ import annotations

from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


def normalize_keyword(keyword: str) -> dict[str, str]:
    raw = (keyword or "").strip()
    if not raw:
        return {"raw": "", "group": "", "value": ""}

    if "|" not in raw:
        return {"raw": raw, "group": "", "value": raw}

    group, value = raw.split("|", 1)
    return {"raw": raw, "group": group.strip(), "value": value.strip()}


def _extract_keywords(root: ET.Element) -> list[str]:
    keywords: list[str] = []

    for element in root.iter():
        tag_name = element.tag.rsplit("}", 1)[-1]
        if tag_name != "keywords":
            continue

        for child in element.iter():
            child_name = child.tag.rsplit("}", 1)[-1]
            if child_name == "li" and child.text:
                keyword = child.text.strip()
                if keyword:
                    keywords.append(keyword)

    # Preserve original order and remove duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for keyword in keywords:
        if keyword in seen:
            continue
        seen.add(keyword)
        unique.append(keyword)

    return unique


def read_xmp_keywords(xmp_path: str | Path) -> dict[str, Any]:
    path = Path(xmp_path)
    result: dict[str, Any] = {
        "path": str(path),
        "file_path": "",
        "keywords": [],
        "genres": [],
        "types": [],
        "drums": [],
        "characters": [],
        "keys": [],
        "sounds": [],
    }

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        return result

    file_path = ""
    for element in root.iter():
        file_path_attr = element.attrib.get("{http://ns.adobe.com/abl/1.0/file-resolver/}filePath")
        if file_path_attr:
            file_path = file_path_attr.strip()
            break

    keywords = _extract_keywords(root)

    genres: list[str] = []
    types: list[str] = []
    drums: list[str] = []
    characters: list[str] = []
    keys: list[str] = []
    sounds: list[str] = []

    for keyword in keywords:
        normalized = normalize_keyword(keyword)
        group = normalized["group"].lower()
        value = normalized["value"]
        if not value:
            continue
        if group == "genres":
            genres.append(value)
        elif group == "type":
            types.append(value)
        elif group == "drums":
            drums.append(value)
        elif group == "character":
            characters.append(value)
        elif group == "key":
            keys.append(value)
        elif group == "sounds":
            sounds.append(value)

    result["file_path"] = file_path
    result["keywords"] = keywords
    result["genres"] = genres
    result["types"] = types
    result["drums"] = drums
    result["characters"] = characters
    result["keys"] = keys
    result["sounds"] = sounds
    return result


def scan_ableton_folder_info(root: str | Path) -> dict[str, dict[str, Any]]:
    root_path = Path(root).expanduser()
    metadata_map: dict[str, dict[str, Any]] = {}

    if not root_path.exists():
        return metadata_map

    for xmp_path in sorted(root_path.glob("**/Ableton Folder Info/**/*.xmp")):
        if not xmp_path.is_file():
            continue
        metadata = read_xmp_keywords(xmp_path)
        key = metadata.get("file_path") or metadata["path"]
        metadata_map[key] = metadata

    return metadata_map
