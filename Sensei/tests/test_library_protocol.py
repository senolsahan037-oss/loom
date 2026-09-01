from pathlib import Path

import pytest

from ableton.ableton_index_provider import find_live_databases

FIXTURES_DIR = Path(__file__).parent.parent / "ableton" / "fixtures"
from ableton.library_protocol import (
    build_variation_context,
    emit_diagnostics,
    query_library_items,
    resolve_kit_context,
    resolve_reference_clip,
    remap_drum_events,
)


def test_query_library_items_returns_only_requested_content_type(monkeypatch):
    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda _filters: [])

    def fake_scan():
        return {
            "items": [
                {
                    "name": "Clip A",
                    "path": "/tmp/clip_a.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "Clips",
                    "folder_category": "Clips",
                    "musical_category": "Drums",
                    "instrument_hint": "drum",
                },
                {
                    "name": "Kit A",
                    "path": "/tmp/kit_a.adg",
                    "pack": "Pack A",
                    "content_type": "kit",
                    "category": "Drums",
                    "folder_category": "Drums",
                    "musical_category": "Drums",
                    "instrument_hint": "drum",
                },
            ]
        }

    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", fake_scan)

    result = query_library_items({"content_type": "clip"})

    assert len(result) == 1
    assert result[0]["content_type"] == "clip"
    assert result[0]["name"] == "Clip A"


def test_resolve_kit_context_prefers_embedded_kit():
    clip_path = FIXTURES_DIR / "two_worlds_clip.alc"

    context = resolve_kit_context(clip_path, selected_kit_path=str(FIXTURES_DIR / "swang_bap_kit.adg"))

    assert context["sound_source"] == "embedded_alc"
    assert context["note_space"]
    assert context["resolved_kit_path"] == str(clip_path)


def test_build_variation_context_includes_events_and_contract():
    reference_clip = {
        "path": "/tmp/clip.alc",
        "events_source": "midi_note_event",
        "notes_used": [36, 38],
        "events": [
            {"note": 36, "beat": 0.0, "velocity": 100},
            {"note": 38, "beat": 1.0, "velocity": 95},
        ],
    }
    kit_context = {
        "sound_source": "adg_fallback",
        "selected_kit_path": "/tmp/kit.adg",
        "resolved_kit_path": "/tmp/kit.adg",
        "note_space": [36, 38, 42],
    }

    context = build_variation_context(reference_clip, kit_context)

    assert context["events"] == reference_clip["events"]
    assert context["variation_contract"]["preserve"] == ["bar_length", "main_pulse", "kit_note_space"]
    assert context["variation_contract"]["change"] == ["velocity", "density", "fills", "ghost_notes"]


def test_emit_diagnostics_reports_note_match_information():
    context = {
        "reference": {
            "path": "/tmp/clip.alc",
            "events_source": "midi_note_event",
            "notes_used": [36, 38, 42],
        },
        "kit_context": {
            "sound_source": "adg_fallback",
            "selected_kit_path": "/tmp/kit.adg",
            "resolved_kit_path": "/tmp/kit.adg",
            "note_space": [36, 42],
        },
        "events": [
            {"note": 36, "beat": 0.0, "velocity": 100},
            {"note": 38, "beat": 1.0, "velocity": 95},
            {"note": 42, "beat": 2.0, "velocity": 90},
        ],
    }

    diagnostics = emit_diagnostics(context)

    assert diagnostics["fallback_used"] is False
    assert diagnostics["matched_notes"] == [36, 42]
    assert diagnostics["missing_notes"] == [38]
    assert diagnostics["all_notes_matched"] is False
    assert diagnostics["event_count"] == 3
    assert diagnostics["note_count"] == 3


def test_query_library_items_genre_prefers_source_native(monkeypatch):
    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda _filters: [])

    def fake_scan():
        return {
            "items": [
                {
                    "name": "Trap Clip",
                    "path": "/tmp/trap_clip.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "Clips",
                    "folder_category": "Clips",
                    "musical_category": "Jazz",
                    "instrument_hint": "drum",
                    "source_native": {"ableton_genres": ["Trap"]},
                },
                {
                    "name": "Non Trap Clip",
                    "path": "/tmp/non_trap_clip.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "Clips",
                    "folder_category": "Clips",
                    "musical_category": "Trap",
                    "instrument_hint": "drum",
                    "source_native": {"ableton_genres": ["Rock"]},
                },
            ]
        }

    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", fake_scan)

    result = query_library_items({"content_type": "clip", "genre": "Trap"})

    assert len(result) == 1
    assert result[0]["name"] == "Trap Clip"
    assert result[0]["genre"] == "Trap"
    assert result[0]["genre_match"]["source"] == "source_native.ableton_genres"
    assert result[0]["genre_match"]["confidence"] == "high"


def test_query_library_items_genre_does_not_use_musical_category_as_primary(monkeypatch):
    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda _filters: [])

    def fake_scan():
        return {
            "items": [
                {
                    "name": "Only Musical Trap",
                    "path": "/tmp/only_musical_trap.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "Clips",
                    "folder_category": "Clips",
                    "musical_category": "Trap",
                    "instrument_hint": "drum",
                    "source_native": {"ableton_genres": []},
                }
            ]
        }

    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", fake_scan)

    result = query_library_items({"content_type": "clip", "genre": "Trap"})

    assert result == []


def test_query_library_items_genre_does_not_promote_folder_category_to_genre(monkeypatch):
    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda _filters: [])
    monkeypatch.setattr(
        "ableton.library_protocol.scan_ableton_library",
        lambda: {
            "items": [
                {
                    "name": "Genreless folder item",
                    "path": "/tmp/house/clip.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "House",
                    "folder_category": "House",
                    "source_native": {"ableton_genres": []},
                }
            ]
        },
    )

    assert query_library_items({"genre": "House"}) == []


def test_resolve_reference_clip_includes_source_native(monkeypatch):
    def fake_query_library_items(_filters):
        return [
            {
                "name": "Clip A",
                "path": "/tmp/clip_a.alc",
                "pack": "Pack A",
                "content_type": "clip",
                "category": "Clips",
                "folder_category": "Clips",
                "musical_category": "Drums",
                "instrument_hint": "drum",
                "source_native": {
                    "ableton_file_path": "Factory Packs/Pack A/Clips/Clip A.alc",
                    "ableton_genres": ["Trap"],
                },
            }
        ]

    def fake_inspect_alc_clip(_path):
        return {
            "notes_used": [36, 38],
            "events": [{"note": 36, "beat": 0.0, "velocity": 100}],
            "events_source": "midi_note_event",
        }

    monkeypatch.setattr("ableton.library_protocol.query_library_items", fake_query_library_items)
    monkeypatch.setattr("ableton.library_protocol.inspect_alc_clip", fake_inspect_alc_clip)

    result = resolve_reference_clip({"pack": "Pack A"})

    assert result is not None
    assert result["source_native"]["ableton_genres"] == ["Trap"]
    assert result["source_native"]["ableton_file_path"] == "Factory Packs/Pack A/Clips/Clip A.alc"


def test_query_library_items_prefers_live_db_source(monkeypatch):
    monkeypatch.setattr(
        "ableton.library_protocol.query_index_items",
        lambda _filters: [
            {
                "name": "Clip A",
                "path": "/tmp/clip_a.alc",
                "browser_path": "/tmp/clip_a.alc",
                "pack": "Pack A",
                "content_type": "clip",
                "source_native": {"ableton_genres": ["Trap"]},
            }
        ],
    )

    def _fake_scanner():
        return {"items": []}

    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", _fake_scanner)

    result = query_library_items({"content_type": "clip"})

    assert len(result) == 1
    assert result[0]["source"] == "ableton_live_db"
    assert result[0]["query_source"] == "ableton_live_db"
    assert result[0]["fallback_used"] is False


def test_query_library_items_uses_scanner_fallback_when_live_db_empty(monkeypatch):
    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda _filters: [])

    def fake_scan():
        return {
            "items": [
                {
                    "name": "Fallback Clip",
                    "path": "/tmp/fallback_clip.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "Clips",
                    "folder_category": "Clips",
                    "musical_category": "Drums",
                    "instrument_hint": "drum",
                    "source_native": {"ableton_genres": ["Trap"]},
                }
            ]
        }

    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", fake_scan)

    result = query_library_items({"content_type": "clip"})

    assert len(result) == 1
    assert result[0]["source"] == "scanner_fallback"
    assert result[0]["query_source"] == "scanner_fallback"
    assert result[0]["fallback_used"] is True


def test_query_library_items_uses_scanner_fallback_when_live_db_errors(monkeypatch):
    def broken_db_query(_filters):
        raise RuntimeError("db failure")

    monkeypatch.setattr("ableton.library_protocol.query_index_items", broken_db_query)

    def fake_scan():
        return {
            "items": [
                {
                    "name": "Fallback Clip",
                    "path": "/tmp/fallback_clip.alc",
                    "pack": "Pack A",
                    "content_type": "clip",
                    "category": "Clips",
                    "folder_category": "Clips",
                    "musical_category": "Drums",
                    "instrument_hint": "drum",
                    "source_native": {"ableton_genres": ["Trap"]},
                }
            ]
        }

    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", fake_scan)

    result = query_library_items({"content_type": "clip"})

    assert len(result) == 1
    assert result[0]["source"] == "scanner_fallback"


def test_query_library_items_live_db_trap_clip_source_when_available():
    if not find_live_databases():
        pytest.skip("No Live database found")

    result = query_library_items({"genre": "Trap", "content_type": "clip"})
    if not result:
        pytest.skip("No Trap clip rows available in active Live DB")

    live_db_rows = [item for item in result if item.get("source") == "ableton_live_db"]
    if not live_db_rows:
        pytest.skip("Query returned fallback rows only")

    assert live_db_rows[0]["query_source"] == "ableton_live_db"


def test_remap_drum_events_standard_gm(monkeypatch):
    # Mock embedded kit of clip
    fake_kit = {
        "pads": {
            "60": {"normalized_role": "kick"},
            "62": {"normalized_role": "snare"},
            "64": {"normalized_role": "closed_hat"},
        }
    }
    monkeypatch.setattr("ableton.library_protocol.inspect_alc_embedded_kit", lambda _path: fake_kit)

    events = [
        {"note": 60, "beat": 0.0, "velocity": 100},
        {"note": 62, "beat": 1.0, "velocity": 90},
        {"note": 64, "beat": 0.5, "velocity": 80},
        {"note": 99, "beat": 2.0, "velocity": 70}, # Unknown note
    ]

    remapped = remap_drum_events(events, "/tmp/some_clip.alc", target_kit_profile=None)

    assert len(remapped) == 4
    # kick -> 36
    assert remapped[0]["note"] == 36
    # snare -> 38
    assert remapped[1]["note"] == 38
    # closed_hat -> 42
    assert remapped[2]["note"] == 42
    # unknown -> keeps original (99)
    assert remapped[3]["note"] == 99


def test_remap_drum_events_with_target_kit(monkeypatch):
    # Mock embedded kit of clip
    fake_kit = {
        "pads": {
            "60": {"normalized_role": "kick"},
            "62": {"normalized_role": "snare"},
        }
    }
    monkeypatch.setattr("ableton.library_protocol.inspect_alc_embedded_kit", lambda _path: fake_kit)

    # Target kit maps kick to 80 and snare to 82
    target_kit = {
        "pads": {
            "80": {"normalized_role": "kick", "confidence": 0.95},
            "82": {"normalized_role": "snare", "confidence": 0.90},
        }
    }

    events = [
        {"note": 60, "beat": 0.0},
        {"note": 62, "beat": 1.0},
    ]

    remapped = remap_drum_events(events, "/tmp/some_clip.alc", target_kit_profile=target_kit)

    assert len(remapped) == 2
    assert remapped[0]["note"] == 80
    assert remapped[1]["note"] == 82
