import gzip
from pathlib import Path

import ableton.variation_corpus as variation_corpus
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


def test_clean_variation_corpus_skips_truncated_gzip_clip(tmp_path):
    broken_path = tmp_path / "truncated.alc"
    broken_path.write_bytes(b"\x1f\x8b\x08")

    entries, manifest = build_clean_variation_corpus(
        [tmp_path],
        index_items=[
            {
                "reference_id": "truncated",
                "path": str(broken_path),
                "source_native": {"ableton_genres": ["House"]},
            },
        ],
    )

    assert entries == []
    assert manifest["audit"]["not_midi_only"] == 1


def test_clean_variation_corpus_skips_truncated_midi(tmp_path):
    broken_path = tmp_path / "truncated.mid"
    broken_path.write_bytes(b"MThd")

    entries, manifest = build_clean_variation_corpus(
        [tmp_path],
        index_items=[
            {
                "reference_id": "truncated",
                "path": str(broken_path),
                "source_native": {"ableton_genres": ["House"]},
            },
        ],
    )

    assert entries == []
    assert manifest["audit"]["not_midi_only"] == 1


def test_clean_variation_corpus_skips_parser_specific_midi_error(tmp_path, monkeypatch):
    broken_path = tmp_path / "invalid-key-signature.mid"
    broken_path.write_bytes(b"MThd")

    class ParserSpecificError(Exception):
        pass

    monkeypatch.setattr(
        variation_corpus,
        "read_midi_events",
        lambda _path: (_ for _ in ()).throw(ParserSpecificError("bad metadata")),
    )

    entries, manifest = build_clean_variation_corpus(
        [tmp_path],
        index_items=[
            {
                "reference_id": "invalid-key-signature",
                "path": str(broken_path),
                "source_native": {"ableton_genres": ["House"]},
            },
        ],
    )

    assert entries == []
    assert manifest["audit"]["not_midi_only"] == 1
