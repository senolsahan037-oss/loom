from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from ableton.ableton_index_provider import (
    find_live_databases,
    health,
    query_items,
)


def _create_minimal_live_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE files (
                file_id INTEGER PRIMARY KEY,
                parent_id INTEGER,
                file_kind INTEGER,
                place_id INTEGER,
                name TEXT
            );
            CREATE TABLE metadata (
                file_id INTEGER,
                key INTEGER,
                value_id INTEGER
            );
            CREATE TABLE metadata_values (
                id INTEGER PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE ancestors (
                file_id INTEGER,
                ancestor_id INTEGER
            );
            CREATE TABLE places (
                file_id INTEGER,
                folder_kind INTEGER,
                level INTEGER,
                name TEXT
            );
            """
        )

        connection.executemany(
            "INSERT INTO files (file_id, parent_id, file_kind, place_id, name) VALUES (?, ?, ?, ?, ?)",
            [
                (100, None, 0, 1, "NoGenre Kit.adg"),
                (101, None, 0, 1, "Trap Clip.alc"),
                (1, None, 0, 1, "Test Pack"),
            ],
        )
        connection.executemany(
            "INSERT INTO places (file_id, folder_kind, level, name) VALUES (?, ?, ?, ?)",
            [
                (1, 0, 0, "Test Pack"),
            ],
        )
        connection.executemany(
            "INSERT INTO ancestors (file_id, ancestor_id) VALUES (?, ?)",
            [
                (100, 1),
                (101, 1),
            ],
        )
        connection.executemany(
            "INSERT INTO metadata_values (id, value) VALUES (?, ?)",
            [
                (1, "Drums|Drum Kit|Hybrid Kit"),
                (2, "Type|MPE Enabled"),
                (3, "Character|Snappy"),
                (4, "Key|C Minor"),
                (5, "Clips|Music Clip|Bassline"),
                (6, "Genres|Trap"),
            ],
        )
        connection.executemany(
            "INSERT INTO metadata (file_id, key, value_id) VALUES (?, ?, ?)",
            [
                (100, 1, 1),
                (100, 1, 2),
                (100, 1, 3),
                (100, 1, 4),
                (101, 1, 5),
                (101, 1, 6),
            ],
        )


def test_query_items_maps_place_and_native_metadata_without_genre_heuristics(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "Live-files-test.db"
    _create_minimal_live_db(db_path)

    monkeypatch.setattr("ableton.ableton_index_provider.get_active_db", lambda: db_path)

    kit_items = query_items({"content_type": "kit", "limit": 20})
    clip_items = query_items({"content_type": "clip", "limit": 20})

    assert kit_items
    assert clip_items

    kit = kit_items[0]
    clip = clip_items[0]

    assert kit["place"] == "Test Pack"
    assert kit["pack"] == "Test Pack"
    assert kit["folder_category"] == "Drums"
    assert kit["category"] == "Drums"
    assert kit["source_native"]["ableton_types"] == ["MPE Enabled"]
    assert kit["source_native"]["ableton_drums"] == ["Drum Kit|Hybrid Kit"]
    assert kit["source_native"]["ableton_characters"] == ["Snappy"]
    assert kit["source_native"]["ableton_keys"] == ["C Minor"]
    assert kit["source_native"]["ableton_genres"] == []

    assert clip["place"] == "Test Pack"
    assert clip["pack"] == "Test Pack"
    assert clip["folder_category"] == "Clips"
    assert clip["category"] == "Clips"
    assert clip["source_native"]["ableton_genres"] == ["Trap"]


def _require_live_db() -> None:
    if not find_live_databases():
        pytest.skip("No Ableton Live Database found")


def test_health_is_readable_when_live_db_exists() -> None:
    _require_live_db()

    result = health()

    assert result["active_db"]
    assert result["readable"] is True
    assert isinstance(result["tables"], dict)
    assert "files" in result["tables"]


def test_query_items_returns_beat_tools_clips() -> None:
    _require_live_db()

    items = query_items({"pack": "Beat Tools", "content_type": "clip", "limit": 20})

    if not items:
        pytest.skip("No Beat Tools clip records found in active Live DB")

    assert items
    assert all(item["source"] == "ableton_live_db" for item in items)
    assert all(item["content_type"] == "clip" for item in items)


def test_query_items_genre_trap_clip_source() -> None:
    _require_live_db()

    items = query_items({"genre": "Trap", "content_type": "clip", "limit": 20})

    if not items:
        pytest.skip("No Trap clip records found in active Live DB")

    assert items
    assert all(item["source"] == "ableton_live_db" for item in items)


def test_reference_id_is_stable_hash_string() -> None:
    _require_live_db()

    first = query_items({"pack": "Beat Tools", "content_type": "clip", "limit": 5})
    second = query_items({"pack": "Beat Tools", "content_type": "clip", "limit": 5})

    if not first or not second:
        pytest.skip("No Beat Tools clip records found in active Live DB")

    first_by_id = {item["file_id"]: item["reference_id"] for item in first}
    second_by_id = {item["file_id"]: item["reference_id"] for item in second}

    overlap = set(first_by_id).intersection(second_by_id)
    assert overlap
    for file_id in overlap:
        assert first_by_id[file_id] == second_by_id[file_id]
        assert re.fullmatch(r"[0-9a-f]{40}", first_by_id[file_id])
