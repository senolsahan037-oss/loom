import gzip
import json
from pathlib import Path
import pytest

from ableton.canonical_midi_corpus import (
    build_canonical_midi_corpus,
    write_canonical_midi_corpus,
)
from ableton.dataset_release import write_dataset_release_manifest


def _write_midi_alc(path: Path) -> None:
    xml = b"""<Ableton><MidiClip><LoopStart Value=\"0\"/><LoopEnd Value=\"4\"/>
    <Notes><KeyTrack><MidiKey Value=\"36\"/><Notes>
    <MidiNoteEvent Time=\"0\" Duration=\"0.25\" Velocity=\"110\"/>
    <MidiNoteEvent Time=\"2\" Duration=\"0.5\" Velocity=\"100\"/>
    </Notes></KeyTrack></Notes></MidiClip></Ableton>"""
    path.write_bytes(gzip.compress(xml))


def _index_item(path: Path) -> dict:
    return {
        "reference_id": "native-midi",
        "path": str(path),
        "pack": "Test Pack",
        "source_native": {"ableton_genres": ["Hip Hop"], "ableton_tags": ["Genres|Hip Hop"]},
    }


def test_canonical_corpus_contains_verified_events_timeline_and_source_hash(tmp_path):
    clip = tmp_path / "bass.alc"
    _write_midi_alc(clip)

    entries, manifest = build_canonical_midi_corpus([tmp_path], index_items=[_index_item(clip)])

    assert len(entries) == 1
    entry = entries[0]
    assert entry["schema_version"] == "sensei.canonical-midi-clip.v1"
    assert entry["events"] == [
        {"pitch": 36, "time": 0.0, "duration": 0.25, "velocity": 110},
        {"pitch": 36, "time": 2.0, "duration": 0.5, "velocity": 100},
    ]
    assert entry["timeline"]["loop_start"] == 0.0
    assert entry["timeline"]["loop_end"] == 4.0
    assert entry["integrity"]["parse_status"] == "verified"
    assert len(entry["integrity"]["content_sha256"]) == 64
    assert manifest["audit"]["verified"] == 1


def test_canonical_corpus_accepts_native_midi_and_deduplicates_install_copies(tmp_path):
    mido = pytest.importorskip("mido")
    first = tmp_path / "Suite" / "beat.mid"
    second = tmp_path / "Beta" / "beat.mid"
    first.parent.mkdir()
    second.parent.mkdir()
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", note=36, velocity=100, time=0))
    midi.save(first)
    second.write_bytes(first.read_bytes())
    entries, manifest = build_canonical_midi_corpus([tmp_path], index_items=[_index_item(first), _index_item(second)])
    assert len(entries) == 1
    assert entries[0]["events"][0]["pitch"] == 36
    assert manifest["audit"]["duplicate_content"] == 1


def test_phase_one_release_hashes_all_dataset_artifacts(tmp_path):
    clip = tmp_path / "bass.alc"
    _write_midi_alc(clip)
    canonical = write_canonical_midi_corpus(tmp_path / "canonical", roots=[tmp_path], index_items=[_index_item(clip)])

    variation_path = tmp_path / "variation.jsonl"
    groove_path = tmp_path / "grooves.jsonl"
    variation_path.write_text('{"schema_version":"sensei.clean-variation-corpus.v1"}\n')
    groove_path.write_text('{"schema_version":"sensei.groove-catalog.v1"}\n')

    result = write_dataset_release_manifest(
        tmp_path / "release",
        artifacts={
            "canonical_midi": canonical["corpus_path"],
            "variation_sources": str(variation_path),
            "grooves": str(groove_path),
        },
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["schema_version"] == "sensei.dataset-release.v1"
    assert set(manifest["artifacts"]) == {"canonical_midi", "variation_sources", "grooves"}
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"].values())
