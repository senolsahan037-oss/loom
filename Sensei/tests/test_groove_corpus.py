import gzip
import json

from ableton.groove_corpus import (
    build_groove_catalog,
    build_sdk_midi_payload,
    write_groove_catalog,
)


def _write_groove(path):
    xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Ableton><Groove><MidiClip><Notes>
  <MidiNoteEvent Time=\"0.0\" Velocity=\"127\" />
  <MidiNoteEvent Time=\"0.31\" Velocity=\"91\" />
</Notes></MidiClip></Groove></Ableton>"""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(xml)


def test_catalog_keeps_only_parse_verified_grooves_and_audits_rejections(tmp_path):
    good = tmp_path / "Grooves" / "Verified Swing.agr"
    good.parent.mkdir()
    _write_groove(good)
    (good.parent / "Unreadable.agr").write_bytes(b"not an ableton document")

    entries, manifest = build_groove_catalog([tmp_path])

    assert len(entries) == 1
    entry = entries[0]
    assert entry["schema_version"] == "sensei.groove-catalog.v1"
    assert entry["parse_status"] == "verified"
    assert entry["usable"] is True
    assert entry["template"]["note_count"] == 2
    assert len(entry["content_sha256"]) == 64
    assert manifest["audit"]["agr_files_seen"] == 2
    assert manifest["audit"]["unparseable"] == 1
    assert len(manifest["integrity"]["catalog_sha256"]) == 64


def test_catalog_write_is_manifested_and_sdk_payload_matches_writer_contract(tmp_path):
    groove = tmp_path / "Grooves" / "Verified Swing.agr"
    groove.parent.mkdir()
    _write_groove(groove)

    result = write_groove_catalog(tmp_path / "catalog", roots=[tmp_path])
    manifest = json.loads((tmp_path / "catalog" / "ableton_groove_catalog.manifest.json").read_text())
    assert result["entry_count"] == 1
    assert manifest["entry_count"] == 1

    payload = build_sdk_midi_payload(
        [{"note": 36, "beat": 0.31, "duration": 0.25, "velocity": 100}],
        clip_length=4,
        groove_entry=result["entries"][0],
    )
    assert payload["schema_version"] == "sensei.sdk-midi-write.v1"
    assert payload["notes"] == [{"pitch": 36, "time": 0.31, "duration": 0.25, "velocity": 100}]
    assert payload["clip_length"] == 4
    assert payload["provenance"]["groove_reference_id"] == result["entries"][0]["reference_id"]
