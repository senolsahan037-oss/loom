import json

import pytest

from core.live_context_resolver import resolve_live_context


def test_live_target_resolves_single_genre_context(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "boom kit", "role": "drum", "profile_id": "ableton.drum-rack.v1", "pack": "Beat", "native_genres": ["Trap"]}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {"drum": {"Trap": 10}}, "neighbors": {"Trap": []}}))
    result = resolve_live_context({"role": "drum", "device_name": "Boom Kit", "device_classes": ["DrumGroupDevice"], "verified_pad_map": True, "verified_pad_notes": [36]}, identity_path=identities, graph_path=graph)
    assert result["genre"] == "Trap"
    assert result["target_context"]["verified_pad_notes"] == [36]
    assert "explicit_profile_id" not in result["target_context"]


def test_live_target_without_role_resolves_bass_from_device_name(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "101 bass sidechain fuzz", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "Build and Drop", "native_genres": ["Trap"]}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {"bass": {"Trap": 10}}, "neighbors": {"Trap": []}}))
    result = resolve_live_context({"device_names": ["101 Bass Sidechain Fuzz"], "bars": 4, "seed": 1}, identity_path=identities, graph_path=graph)
    assert result["genre"] == "Trap"
    assert result["target_context"]["explicit_profile_id"] == "ableton.bass.synth.v1"


def test_live_target_without_role_resolves_chord_from_device_name(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "fuzz clav", "role": "chord", "profile_id": "ableton.chord.clav.v1", "pack": "Chop and Swing", "native_genres": ["Funk"]}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {"chord": {"Funk": 5}}, "neighbors": {"Funk": []}}))
    result = resolve_live_context({"device_names": ["Fuzz Clav"], "bars": 4, "seed": 1}, identity_path=identities, graph_path=graph)
    assert result["genre"] == "Funk"
    assert result["target_context"]["explicit_profile_id"] == "ableton.chord.clav.v1"


def test_live_target_default_genre_used_when_preset_has_no_genre_evidence(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "sub bass drone", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "Creative Extensions", "native_genres": []}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"pack_genre_counts": {}, "role_genre_counts": {"bass": {"Pop": 4}}, "neighbors": {}}))
    result = resolve_live_context({"device_names": ["Sub Bass Drone"], "default_genre": "Pop"}, identity_path=identities, graph_path=graph)
    assert result["genre"] == "Pop"
    assert result["genre_resolution"]["genre_source"] == "project_default_genre"


def test_live_target_passes_target_root_and_mode_for_instrument_roles(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "101 bass sidechain fuzz", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "Build and Drop", "native_genres": ["Trap"]}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {"bass": {"Trap": 10}}, "neighbors": {"Trap": []}}))
    result = resolve_live_context(
        {"device_names": ["101 Bass Sidechain Fuzz"], "target_root": "D", "target_mode": "Minor"},
        identity_path=identities,
        graph_path=graph,
    )
    assert result["target_root"] == "D"
    assert result["target_mode"] == "Minor"


def test_live_target_never_passes_target_root_and_mode_for_drum_role(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "boom kit", "role": "drum", "profile_id": "ableton.drum-rack.v1", "pack": "Beat", "native_genres": ["Trap"]}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {"drum": {"Trap": 10}}, "neighbors": {"Trap": []}}))
    result = resolve_live_context(
        {"role": "drum", "device_name": "Boom Kit", "verified_pad_map": True, "verified_pad_notes": [36], "target_root": "D", "target_mode": "Minor"},
        identity_path=identities,
        graph_path=graph,
    )
    assert "target_root" not in result
    assert "target_mode" not in result


def test_live_target_without_role_blocks_when_device_name_matches_multiple_roles(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(
        json.dumps({"normalized_name": "dual role preset", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "Pack", "native_genres": ["Trap"]}) + "\n"
        + json.dumps({"normalized_name": "dual role preset", "role": "chord", "profile_id": "ableton.chord.pad.v1", "pack": "Pack", "native_genres": ["Trap"]}) + "\n"
    )
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {}, "neighbors": {}}))
    with pytest.raises(ValueError, match="instrument_role_ambiguous_multiple_roles"):
        resolve_live_context({"device_names": ["Dual Role Preset"]}, identity_path=identities, graph_path=graph)


def test_live_target_without_role_blocks_when_device_name_unmatched(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(json.dumps({"normalized_name": "fuzz clav", "role": "chord", "profile_id": "ableton.chord.clav.v1", "pack": "Pack", "native_genres": ["Funk"]}) + "\n")
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {}, "neighbors": {}}))
    with pytest.raises(ValueError, match="instrument_role_unresolved"):
        resolve_live_context({"device_names": ["EQ Eight"]}, identity_path=identities, graph_path=graph)


def test_live_target_without_role_blocks_when_multiple_devices_resolve(tmp_path):
    identities = tmp_path / "identities.jsonl"
    identities.write_text(
        json.dumps({"normalized_name": "101 bass sidechain fuzz", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "Pack", "native_genres": ["Trap"]}) + "\n"
        + json.dumps({"normalized_name": "fuzz clav", "role": "chord", "profile_id": "ableton.chord.clav.v1", "pack": "Pack", "native_genres": ["Funk"]}) + "\n"
    )
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"role_genre_counts": {}, "neighbors": {}}))
    with pytest.raises(ValueError, match="instrument_role_ambiguous_multiple_devices"):
        resolve_live_context({"device_names": ["101 Bass Sidechain Fuzz", "Fuzz Clav"]}, identity_path=identities, graph_path=graph)
