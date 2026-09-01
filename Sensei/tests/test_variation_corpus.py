import gzip
from pathlib import Path

from ableton.variation_corpus import build_clean_variation_corpus


def _write_alc(path: Path, body: bytes) -> None:
    path.write_bytes(gzip.compress(body))


def test_clean_variation_corpus_requires_real_midi_and_native_genre(tmp_path):
    midi_path = tmp_path / "native-tagged.alc"
    genreless_path = tmp_path / "genreless.alc"
    audio_path = tmp_path / "audio.alc"
    _write_alc(midi_path, b"<MidiClip><MidiNoteEvent/><MidiNoteEvent/></MidiClip>")
    _write_alc(genreless_path, b"<MidiClip><MidiNoteEvent/></MidiClip>")
    _write_alc(audio_path, b"<AudioClip/>")

    entries, manifest = build_clean_variation_corpus(
        [tmp_path],
        index_items=[
            {
                "reference_id": "native-tagged",
                "path": str(midi_path),
                "pack": "Test Pack",
                "source_native": {"ableton_genres": ["Hip Hop"], "ableton_tags": ["Genres|Hip Hop"]},
            },
            {
                "reference_id": "genreless",
                "path": str(genreless_path),
                "pack": "Test Pack",
                "source_native": {"ableton_genres": []},
            },
        ],
    )

    assert len(entries) == 1
    assert entries[0]["reference_id"] == "native-tagged"
    assert entries[0]["midi_note_event_count"] == 2
    assert entries[0]["genre_source"] == "ableton_live_browser_metadata"
    assert manifest["audit"]["missing_native_genre"] == 1
    assert manifest["audit"]["not_midi_only"] == 1
