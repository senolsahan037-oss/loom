from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any

LIVE_DATABASE_DIR = Path.home() / "Library" / "Application Support" / "Ableton" / "Live Database"
LIVE_DB_GLOB = "Live-files-*.db"
TABLES_FOR_HEALTH = [
    "files",
    "metadata",
    "metadata_values",
    "keywords",
    "ancestors",
    "places",
    "vfolders",
    "vfolder_patterns",
    "search_aggregation_content",
]


def find_live_databases() -> list[Path]:
    if not LIVE_DATABASE_DIR.exists():
        return []

    candidates = sorted(
        LIVE_DATABASE_DIR.glob(LIVE_DB_GLOB),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [path.resolve() for path in candidates if path.is_file()]


def copy_db_to_temp(db_path: str | Path) -> Path:
    source = Path(db_path).expanduser().resolve()
    target = Path(tempfile.gettempdir()) / f"sensei_ableton_live_files_{os.getpid()}_{source.name}"
    shutil.copy2(source, target)
    return target


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    db_file = Path(db_path).expanduser().resolve()
    connection = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _db_is_readable(db_path: Path) -> bool:
    try:
        db_copy = copy_db_to_temp(db_path)
        with connect_readonly(db_copy) as connection:
            connection.execute("SELECT COUNT(*) AS c FROM files").fetchone()
        return True
    except (OSError, sqlite3.Error):
        return False


def get_active_db() -> Path | None:
    for candidate in find_live_databases():
        if _db_is_readable(candidate):
            return candidate
    return None


def _count_table(connection: sqlite3.Connection, table_name: str) -> int | None:
    try:
        row = connection.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
    except sqlite3.Error:
        return None
    return int(row["c"]) if row else 0


def _content_type_condition(content_type: str) -> tuple[str, list[Any]]:
    normalized = (content_type or "").strip().lower()
    if normalized == "clip":
        return "(lower(f.name) LIKE ? OR lower(f.name) LIKE ? OR lower(f.name) LIKE ?)", ["%.alc", "%.mid", "%.midi"]
    if normalized == "kit":
        return "lower(f.name) LIKE ?", ["%.adg"]
    if normalized == "set":
        return "lower(f.name) LIKE ?", ["%.als"]
    if normalized in {"audio", "sample"}:
        return "(lower(f.name) LIKE ? OR lower(f.name) LIKE ? OR lower(f.name) LIKE ?)", ["%.wav", "%.aif", "%.aiff"]
    if normalized == "device":
        return "(f.file_kind = 32768 OR lower(f.name) LIKE ? OR lower(f.name) LIKE ?)", ["%.adv", "%.amxd"]
    return "1=1", []


def _infer_content_type(name: str) -> str:
    lower_name = name.lower()
    if lower_name.endswith(".alc") or lower_name.endswith(".mid") or lower_name.endswith(".midi"):
        return "clip"
    if lower_name.endswith(".adg"):
        return "kit"
    if lower_name.endswith(".als"):
        return "set"
    if lower_name.endswith(".wav") or lower_name.endswith(".aif") or lower_name.endswith(".aiff"):
        return "audio"
    if lower_name.endswith(".adv") or lower_name.endswith(".amxd"):
        return "device"
    return "unknown"


def _resolve_path_with_conn(connection: sqlite3.Connection, file_id: int) -> dict[str, str | None]:
    rows = connection.execute(
        """
        WITH RECURSIVE chain(file_id, parent_id, name) AS (
            SELECT file_id, parent_id, name
            FROM files
            WHERE file_id = ?
            UNION ALL
            SELECT f.file_id, f.parent_id, f.name
            FROM files f
            JOIN chain c ON c.parent_id = f.file_id
            WHERE c.parent_id IS NOT NULL AND c.parent_id != c.file_id
        )
        SELECT file_id, parent_id, name
        FROM chain
        """,
        (file_id,),
    ).fetchall()

    if not rows:
        return {"path": None, "browser_path": None}

    names = list(reversed([str(row["name"]) for row in rows]))
    joined = "/".join(segment for segment in names if segment)

    if joined.startswith("//"):
        while joined.startswith("//"):
            joined = joined[1:]

    resolved_path: str | None = None
    if joined.startswith("/"):
        resolved_path = joined

    browser_path = joined if joined else None
    return {
        "path": resolved_path,
        "browser_path": browser_path,
    }


def resolve_path(file_id: int, db_path: str | Path | None = None) -> dict[str, str | None]:
    active = Path(db_path).expanduser().resolve() if db_path else get_active_db()
    if active is None:
        return {"path": None, "browser_path": None}

    db_copy = copy_db_to_temp(active)
    with connect_readonly(db_copy) as connection:
        return _resolve_path_with_conn(connection, file_id)


def _resolve_pack_with_conn(connection: sqlite3.Connection, file_id: int) -> str | None:
    row = connection.execute(
        """
        SELECT root.name AS pack_name
        FROM ancestors a
        JOIN places p
          ON p.file_id = a.ancestor_id
         AND p.folder_kind = 0
         AND p.level = 0
        JOIN files root ON root.file_id = p.file_id
        WHERE a.file_id = ?
        LIMIT 1
        """,
        (file_id,),
    ).fetchone()
    if not row:
        return None
    return str(row["pack_name"])


def _place_name_for_file_with_conn(connection: sqlite3.Connection, file_id: int) -> str | None:
    row = connection.execute(
        """
        SELECT p.name AS place_name
        FROM files f
        LEFT JOIN places p ON p.file_id = f.place_id
        WHERE f.file_id = ?
        LIMIT 1
        """,
        (file_id,),
    ).fetchone()
    if not row:
        return None
    place_name = row["place_name"]
    if place_name is None:
        return None
    return str(place_name)


def resolve_pack(file_id: int, db_path: str | Path | None = None) -> str | None:
    active = Path(db_path).expanduser().resolve() if db_path else get_active_db()
    if active is None:
        return None

    db_copy = copy_db_to_temp(active)
    with connect_readonly(db_copy) as connection:
        return _resolve_pack_with_conn(connection, file_id)


def _split_tag_value(value: str) -> tuple[str, str]:
    if "|" not in value:
        return value, ""
    prefix, suffix = value.split("|", 1)
    return prefix, suffix


def _native_folder_category(tags: list[str]) -> str | None:
    category_priority = [
        "Drums",
        "Clips",
        "Sounds",
        "Instruments",
        "Audio Effects",
        "MIDI Effects",
    ]
    tag_prefixes = {_split_tag_value(tag)[0] for tag in tags if tag}
    for category in category_priority:
        if category in tag_prefixes:
            return category
    return None


def _tags_for_file_with_conn(connection: sqlite3.Connection, file_id: int) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT mv.value
        FROM metadata m
        JOIN metadata_values mv ON mv.id = m.value_id
        WHERE m.file_id = ?
        ORDER BY mv.value
        """,
        (file_id,),
    ).fetchall()

    all_tags = [str(row["value"]) for row in rows]
    genres: list[str] = []
    types: list[str] = []
    drums: list[str] = []
    characters: list[str] = []
    keys: list[str] = []
    sounds: list[str] = []

    for tag in all_tags:
        prefix, suffix = _split_tag_value(tag)
        if not suffix:
            continue
        if prefix == "Genres":
            genres.append(suffix)
        elif prefix == "Type":
            types.append(suffix)
        elif prefix == "Drums":
            drums.append(suffix)
        elif prefix == "Character":
            characters.append(suffix)
        elif prefix == "Key":
            keys.append(suffix)
        elif prefix == "Sounds":
            sounds.append(suffix)

    return {
        "ableton_tags": all_tags,
        "ableton_genres": genres,
        "ableton_types": types,
        "ableton_drums": drums,
        "ableton_characters": characters,
        "ableton_keys": keys,
        "ableton_sounds": sounds,
        "folder_category": _native_folder_category(all_tags),
    }


def _reference_id(path: str | None, browser_path: str | None, file_id: int) -> str:
    base = path or browser_path or f"file_id:{file_id}"
    return hashlib.sha1(f"ableton:{base}".encode("utf-8")).hexdigest()


def health() -> dict[str, Any]:
    warnings: list[str] = []
    active = get_active_db()
    if active is None:
        return {
            "active_db": None,
            "readable": False,
            "tables": {},
            "warnings": ["No readable Live-files database found"],
        }

    try:
        db_copy = copy_db_to_temp(active)
        with connect_readonly(db_copy) as connection:
            table_counts = {table: _count_table(connection, table) for table in TABLES_FOR_HEALTH}
    except (OSError, sqlite3.Error) as exc:
        warnings.append(str(exc))
        return {
            "active_db": str(active),
            "readable": False,
            "tables": {},
            "warnings": warnings,
        }

    for table, value in table_counts.items():
        if value is None:
            warnings.append(f"table_not_readable:{table}")

    return {
        "active_db": str(active),
        "readable": True,
        "tables": table_counts,
        "warnings": warnings,
    }


def query_items(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    active = get_active_db()
    if active is None:
        return []

    filters = filters or {}
    pack = str(filters.get("pack") or "").strip()
    content_type = str(filters.get("content_type") or "").strip().lower()
    genre = str(filters.get("genre") or "").strip()
    limit = int(filters.get("limit") or 200)

    where_clauses = ["1=1"]
    params: list[Any] = []

    if pack:
        where_clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM ancestors a
                JOIN places p
                  ON p.file_id = a.ancestor_id
                 AND p.folder_kind = 0
                 AND p.level = 0
                JOIN files root ON root.file_id = p.file_id
                WHERE a.file_id = f.file_id
                  AND root.name = ?
            )
            """
        )
        params.append(pack)

    if content_type:
        condition, condition_params = _content_type_condition(content_type)
        where_clauses.append(condition)
        params.extend(condition_params)

    if genre:
        where_clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM metadata m
                JOIN metadata_values mv ON mv.id = m.value_id
                WHERE m.file_id = f.file_id
                  AND mv.value = ?
            )
            """
        )
        params.append(f"Genres|{genre}")

    sql = f"""
        SELECT f.file_id, f.name, p.name AS place_name
        FROM files f
        LEFT JOIN places p ON p.file_id = f.place_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY f.name
        LIMIT ?
    """
    params.append(limit)

    db_copy = copy_db_to_temp(active)
    items: list[dict[str, Any]] = []
    with connect_readonly(db_copy) as connection:
        rows = connection.execute(sql, params).fetchall()
        for row in rows:
            file_id = int(row["file_id"])
            name = str(row["name"])
            resolved = _resolve_path_with_conn(connection, file_id)
            place_name = str(row["place_name"]) if row["place_name"] is not None else None
            pack_name = _resolve_pack_with_conn(connection, file_id)
            if pack_name is None:
                pack_name = place_name or _place_name_for_file_with_conn(connection, file_id)
            source_native = _tags_for_file_with_conn(connection, file_id)
            native_genres = list(source_native.get("ableton_genres") or [])
            resolved_path = resolved.get("path")
            browser_path = resolved.get("browser_path")
            folder_category = source_native.get("folder_category")
            items.append(
                {
                    "reference_id": _reference_id(resolved_path, browser_path, file_id),
                    "file_id": file_id,
                    "name": name,
                    "path": resolved_path,
                    "browser_path": browser_path,
                    "pack": pack_name,
                    "place": place_name,
                    "content_type": _infer_content_type(name),
                    "folder_category": folder_category,
                    "category": folder_category,
                    "source": "ableton_live_db",
                    "genre": native_genres[0] if native_genres else None,
                    "source_native": {
                        "ableton_genres": native_genres,
                        "ableton_tags": list(source_native.get("ableton_tags") or []),
                        "ableton_types": list(source_native.get("ableton_types") or []),
                        "ableton_drums": list(source_native.get("ableton_drums") or []),
                        "ableton_characters": list(source_native.get("ableton_characters") or []),
                        "ableton_keys": list(source_native.get("ableton_keys") or []),
                        "ableton_sounds": list(source_native.get("ableton_sounds") or []),
                    },
                }
            )

    return items


__all__ = [
    "copy_db_to_temp",
    "connect_readonly",
    "find_live_databases",
    "get_active_db",
    "health",
    "query_items",
    "resolve_pack",
    "resolve_path",
]
