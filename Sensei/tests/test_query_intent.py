import pytest
from ableton.query_intent import parse_prompt_to_intent
from ableton.library_protocol import query_library_items

def test_parse_prompt_to_intent():
    # 1. "808 bass" -> role_hint="bass" + keyword 808
    intent1 = parse_prompt_to_intent("808 bass")
    assert intent1.role_hint == "bass"
    assert "808" in intent1.keywords

    # 2. "trap kit" -> genre Trap + content_type kit
    intent2 = parse_prompt_to_intent("trap kit")
    assert intent2.genre == "Trap"
    assert intent2.content_type == "kit"

    # 3. "hip hop groove" -> genre Hip Hop + content_type clip + role_hint drums
    intent3 = parse_prompt_to_intent("hip hop groove")
    assert intent3.genre == "Hip Hop"
    assert intent3.content_type == "clip"
    assert intent3.role_hint == "drums"

    # 4. "Unnatural chord" -> pack Unnatural Selection + role_hint chords
    intent4 = parse_prompt_to_intent("Unnatural chord")
    assert intent4.pack == "Unnatural Selection"
    assert intent4.role_hint == "chords"

    # 5. "expressive pad" -> tag Expressive + role_hint chords
    intent5 = parse_prompt_to_intent("expressive pad")
    assert intent5.tag == "Expressive"
    assert intent5.role_hint == "chords"

def test_query_library_items_with_intent_filters(monkeypatch):
    # Mock fallback scan items
    mock_scan_items = [
        {
            "reference_id": "item1",
            "name": "Bap Groove 808",
            "path": "/clips/beats/groove.alc",
            "content_type": "clip",
            "source_native": {"ableton_tags": ["Expressive"]}
        },
        {
            "reference_id": "item2",
            "name": "Clean Synth",
            "path": "/clips/synth/clean.alc",
            "content_type": "clip",
            "source_native": {"ableton_tags": []}
        }
    ]

    monkeypatch.setattr("ableton.library_protocol.query_index_items", lambda _filters: [])
    monkeypatch.setattr("ableton.library_protocol.scan_ableton_library", lambda: {"items": mock_scan_items})

    # Test role_hint post-filter
    results_drums = query_library_items({"role_hint": "drums"})
    assert len(results_drums) == 1
    assert results_drums[0]["reference_id"] == "item1"

    # Test tag post-filter
    results_tag = query_library_items({"tag": "Expressive"})
    assert len(results_tag) == 1
    assert results_tag[0]["reference_id"] == "item1"

    # Test keywords post-filter
    results_kw = query_library_items({"keywords": ["808"]})
    assert len(results_kw) == 1
    assert results_kw[0]["reference_id"] == "item1"

    # Test keywords mismatch
    results_none = query_library_items({"keywords": ["missing"]})
    assert len(results_none) == 0
