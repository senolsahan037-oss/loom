from core.midi_variation_engine import generate_midi_variation


def _entry(reference_id, genre, pitch):
    return {
        "reference_id": reference_id,
        "source_native": {"ableton_tags": ["Clips|Music Clip|Bassline"], "ableton_genres": [genre]},
        "timeline": {"loop_start": 0, "loop_end": 4},
        "events": [{"pitch": pitch, "time": 0, "duration": 1, "velocity": 90}],
    }


def test_equal_neighbor_genres_synthesize_alternating_bars():
    profile = {
        "profile_id": "ableton.bass.synth.v1", "target_role": "bass", "allowed_roles": ["bass"],
        "constraints": {"pitch_range": [28, 60], "max_polyphony": 1},
        "variation_defaults": {"source_role": "bass", "preferred_grid": 0.25, "max_note_duration_beats": 2, "max_events_per_bar": 8},
    }
    result = generate_midi_variation([_entry("house", "House", 36), _entry("techno", "Techno", 40)], target_profile=profile, genre=["House", "Techno"], bars=4, seed=1)
    assert result["generation_safe"] is True
    assert result["payload"]["provenance"]["genre_mode"] == "synthesis"
    assert {event["pitch"] for event in result["events"]} == {36, 40}
    assert {int(event["time"] // 4) % 2 for event in result["events"]} == {0, 1}
