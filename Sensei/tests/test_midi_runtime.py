import json
import hashlib

from core.midi_runtime import prepare_midi_variation


def _write_jsonl(path, entries):
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")


def test_runtime_is_the_single_release_pinned_midi_path(tmp_path):
    data = tmp_path / "data"
    (data / "dataset_releases" / "phase6").mkdir(parents=True)
    profiles = data / "profiles.jsonl"
    bass = data / "bass.jsonl"
    chords = data / "chords.jsonl"
    corpus = data / "corpus.jsonl"
    identities = data / "identities.jsonl"
    graph = data / "graph.json"
    _write_jsonl(profiles, [{"profile_id": "ableton.bass.synth.v1", "target_role": "bass", "allowed_roles": ["bass"], "constraints": {"pitch_range": [28, 60], "max_polyphony": 1}, "variation_defaults": {"source_role": "bass", "max_events_per_bar": 8, "preferred_grid": 0.25, "max_note_duration_beats": 2}}])
    _write_jsonl(bass, [{"path": "/Suite/Bass.adg", "profile_id": "ableton.bass.synth.v1"}])
    _write_jsonl(chords, [])
    _write_jsonl(corpus, [{"reference_id": "bass-1", "source_native": {"ableton_tags": ["Clips|Music Clip|Bassline"], "ableton_genres": ["Trap"]}, "timeline": {"loop_start": 0, "loop_end": 4}, "events": [{"pitch": 48, "time": 0, "duration": 1, "velocity": 100}]}])
    _write_jsonl(identities, [])
    graph.write_text("{}", encoding="utf-8")
    manifest = {"artifacts": {name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in {"instrument_capabilities": profiles, "bass_instruments": bass, "chord_instruments": chords, "canonical_midi": corpus, "preset_genre_identities": identities, "genre_neighbor_graph": graph}.items()}}
    (data / "dataset_releases" / "phase6" / "dataset_release.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = prepare_midi_variation(target_context={"loaded_preset_path": "/Suite/Bass.adg"}, genre="Trap", bars=1, seed=1, data_root=data)

    assert result["generation_safe"] is True
    assert result["resolution"]["binding_evidence"] == "native_catalog_path"
    assert result["payload"]["schema_version"] == "sensei.sdk-midi-write.v1"
