from ableton.genre_identity import build_genre_neighbor_graph, build_preset_identities, resolve_preset_genre


def _clip(pack, genre, role_tag):
    return {"pack": pack, "source_native": {"ableton_genres": [genre], "ableton_tags": [role_tag, f"Genres|{genre}"]}}


def test_graph_and_resolver_choose_role_backed_neighbor_from_pack_evidence():
    corpus = [
        *[_clip("Beat Tools", "Trap", "Clips|Drum Clip|Full Drum Clip") for _ in range(7)],
        *[_clip("Beat Tools", "Hip Hop", "Clips|Drum Clip|Full Drum Clip") for _ in range(3)],
        _clip("Other", "Trap", "Clips|Music Clip|Bassline"),
    ]
    graph = build_genre_neighbor_graph(corpus)
    index = [{
        "name": "Boom Kit.adg", "path": "/Music/Ableton/Factory Packs/Beat Tools/Boom Kit.adg", "pack": "Beat Tools",
        "source_native": {"ableton_tags": ["device:ableton:instr:DrumGroupDevice"], "ableton_genres": []},
    }]
    identities = build_preset_identities(index, [], [])
    result = resolve_preset_genre(device_name="Boom Kit", role="drum", identities=identities, graph=graph)
    assert result["resolved"] is True
    assert result["genre"] == "Trap"
    assert result["identity"]["profile_id"] == "ableton.drum-rack.v1"


def test_resolver_fails_closed_for_unknown_or_tied_identity():
    graph = build_genre_neighbor_graph([_clip("Pack", "House", "Clips|Drum Clip|Full Drum Clip")])
    assert resolve_preset_genre(device_name="Missing", role="drum", identities=[], graph=graph)["reason"] == "preset_identity_unresolved"
    identities = [
        {"normalized_name": "same", "role": "drum", "profile_id": "ableton.drum-rack.v1", "pack": "A", "native_genres": ["House"]},
        {"normalized_name": "same", "role": "drum", "profile_id": "ableton.drum-rack.v1", "pack": "B", "native_genres": ["Techno"]},
    ]
    assert resolve_preset_genre(device_name="Same", role="drum", identities=identities, graph=graph)["reason"] == "preset_identity_ambiguous"


def test_equal_genre_scores_resolve_as_synthesis():
    corpus = [
        _clip("Equal Pack", "House", "Clips|Drum Clip|Full Drum Clip"),
        _clip("Equal Pack", "Techno", "Clips|Drum Clip|Full Drum Clip"),
    ]
    graph = build_genre_neighbor_graph(corpus)
    identities = [{"normalized_name": "equal kit", "role": "drum", "profile_id": "ableton.drum-rack.v1", "pack": "Equal Pack", "native_genres": []}]
    result = resolve_preset_genre(device_name="Equal Kit", role="drum", identities=identities, graph=graph)
    assert result["resolved"] is True
    assert result["genre_mode"] == "synthesis"
    assert set(result["genres"]) == {"House", "Techno"}


def test_unique_highest_score_wins_even_when_absolute_score_is_low():
    graph = {"pack_genre_counts": {"Sparse": {"House": 1, "Techno": 9}}, "role_genre_counts": {"drum": {"House": 1, "Techno": 1}}, "neighbors": {}}
    identities = [{"normalized_name": "sparse kit", "role": "drum", "profile_id": "ableton.drum-rack.v1", "pack": "Sparse", "native_genres": []}]
    result = resolve_preset_genre(device_name="Sparse Kit", role="drum", identities=identities, graph=graph)
    assert result["resolved"] is True
    assert result["genre"] == "Techno"


def test_default_genre_accepted_only_when_role_has_real_corpus_coverage():
    graph = {"pack_genre_counts": {}, "role_genre_counts": {"bass": {"Pop": 5}}, "neighbors": {}}
    identities = [{"normalized_name": "untagged bass", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "No Genre Pack", "native_genres": []}]
    result = resolve_preset_genre(device_name="Untagged Bass", role="bass", identities=identities, graph=graph, default_genre="Pop")
    assert result["resolved"] is True
    assert result["genre"] == "Pop"
    assert result["genre_source"] == "project_default_genre"


def test_default_genre_rejected_when_role_has_no_corpus_for_it():
    graph = {"pack_genre_counts": {}, "role_genre_counts": {"bass": {"Trap": 5}}, "neighbors": {}}
    identities = [{"normalized_name": "untagged bass", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "No Genre Pack", "native_genres": []}]
    result = resolve_preset_genre(device_name="Untagged Bass", role="bass", identities=identities, graph=graph, default_genre="Pop")
    assert result["resolved"] is False
    assert result["reason"] == "genre_evidence_missing"


def test_no_default_genre_still_fails_closed():
    graph = {"pack_genre_counts": {}, "role_genre_counts": {"bass": {"Pop": 5}}, "neighbors": {}}
    identities = [{"normalized_name": "untagged bass", "role": "bass", "profile_id": "ableton.bass.synth.v1", "pack": "No Genre Pack", "native_genres": []}]
    result = resolve_preset_genre(device_name="Untagged Bass", role="bass", identities=identities, graph=graph)
    assert result["resolved"] is False
    assert result["reason"] == "genre_evidence_missing"


def test_core_library_kit_is_indexed_and_duplicate_live_installations_are_merged():
    index = [
        {"name": "606 Core Kit.adg", "path": "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library/Racks/606 Core Kit.adg", "pack": None, "source_native": {"ableton_tags": ["device:ableton:instr:DrumGroupDevice"], "ableton_genres": []}},
        {"name": "606 Core Kit.adg", "path": "/Applications/Ableton Live 12 Beta.app/Contents/App-Resources/Core Library/Racks/606 Core Kit.adg", "pack": "Core Library", "source_native": {"ableton_tags": ["device:ableton:instr:DrumGroupDevice"], "ableton_genres": []}},
    ]
    identities = build_preset_identities(index, [], [])
    graph = {"pack_genre_counts": {"Core Library": {"Electro": 3}}, "role_genre_counts": {"drum": {"Electro": 2}}, "neighbors": {}}
    result = resolve_preset_genre(device_name="606 Core Kit", role="drum", identities=identities, graph=graph)
    assert len(identities) == 2
    assert result["resolved"] is True
    assert result["genre"] == "Electro"
    assert result["identity"]["pack"] == "Core Library"
