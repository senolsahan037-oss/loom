import pytest
from pathlib import Path
from ableton.library_protocol import query_library_items

def test_library_protocol_merge_and_midi(monkeypatch):
    # Mock SQLite DB query items
    db_items = [
        {
            "reference_id": "db_clip_1",
            "name": "Bap Groove 01.alc",
            "path": "/Music/Ableton/User Library/c1.alc",
            "pack": "User Library",
            "content_type": "clip",
            "source": "ableton_live_db",
            "source_native": {
                "ableton_tags": ["Genres|Hip Hop"],
                "ableton_genres": ["Hip Hop"]
            }
        },
        {
            "reference_id": "db_clip_2",
            "name": "Midi Clip 01.mid",
            "path": "/Music/Ableton/User Library/m1.mid",
            "pack": "User Library",
            "content_type": "clip",
            "source": "ableton_live_db",
            "source_native": {}
        }
    ]

    # Mock filesystem scanner
    scanner_library = {
        "items": [
            # Duplicate path - should be deduped (retaining DB version)
            {
                "reference_id": "scanner_clip_1",
                "name": "Bap Groove 01.alc",
                "path": "/Music/Ableton/User Library/c1.alc",
                "pack": "User Library",
                "content_type": "clip",
                "source_native": {}
            },
            # Scanner-only clip (unique path)
            {
                "reference_id": "scanner_clip_2",
                "name": "Scanner Only Clip.alc",
                "path": "/Music/Ableton/Factory Packs/Beat Tools/Clips/c3.alc",
                "pack": "Beat Tools",
                "content_type": "clip",
                "source_native": {}
            }
        ]
    }

    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda filters: db_items)
    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", lambda: scanner_library)

    results = query_library_items({"content_type": "clip"})

    # Check deduplication and merging:
    # 1. Total items should be 3 (db_clip_1, db_clip_2, scanner_clip_2)
    assert len(results) == 3

    # 2. Retains the DB version of duplicate path (db_clip_1) which has Genre tag
    db_clip = next(item for item in results if item["path"] == "/Music/Ableton/User Library/c1.alc")
    assert db_clip["reference_id"] == "db_clip_1"
    assert db_clip["source"] == "ableton_live_db"
    assert db_clip["genre"] == "Hip Hop"

    # 3. Includes the scanner-only clip
    scan_clip = next(item for item in results if item["path"] == "/Music/Ableton/Factory Packs/Beat Tools/Clips/c3.alc")
    assert scan_clip["reference_id"] == "scanner_clip_2"
    assert scan_clip["source"] == "scanner_fallback"

    # 4. Supports .mid clip type
    midi_clip = next(item for item in results if item["path"] == "/Music/Ableton/User Library/m1.mid")
    assert midi_clip["content_type"] == "clip"
