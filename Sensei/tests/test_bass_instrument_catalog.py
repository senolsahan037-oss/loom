import ableton.bass_instrument_catalog as bass_instrument_catalog
from ableton.bass_instrument_catalog import build_bass_instrument_catalog


def test_bass_catalog_uses_only_native_bass_tagged_device_presets(tmp_path):
    synth = tmp_path / "Synth Bass.adg"
    bass_808 = tmp_path / "808.adg"
    audio = tmp_path / "Bass.wav"
    synth.write_bytes(b"synth")
    bass_808.write_bytes(b"808")
    audio.write_bytes(b"audio")
    index = [
        {"reference_id": "synth", "path": str(synth), "pack": "Test", "source_native": {"ableton_sounds": ["Bass|Synth Bass"], "ableton_tags": ["Sounds|Bass|Synth Bass"]}},
        {"reference_id": "808", "path": str(bass_808), "pack": "Test", "source_native": {"ableton_sounds": ["Bass|808 Bass"], "ableton_tags": ["Sounds|Bass|808 Bass"]}},
        {"reference_id": "audio", "path": str(audio), "pack": "Test", "source_native": {"ableton_sounds": ["Bass|Synth Bass"], "ableton_tags": ["Sounds|Bass|Synth Bass"]}},
    ]

    entries, manifest = build_bass_instrument_catalog(index)

    assert [entry["profile_id"] for entry in entries] == ["ableton.bass.808.v1", "ableton.bass.synth.v1"]
    assert manifest["audit"]["skipped_non_midi_device"] == 1
    assert all(entry["source_native"]["ableton_sounds"] for entry in entries)


def test_bass_catalog_admits_core_library_but_not_arbitrary_paths(tmp_path, monkeypatch):
    core = tmp_path / "Applications" / "Ableton Live 12 Beta.app" / "Contents" / "App-Resources" / "Core Library" / "Basic Analog Bass.adg"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"core")
    desktop_copy = tmp_path / "Desktop" / "Basic Analog Bass.adg"
    desktop_copy.parent.mkdir(parents=True)
    desktop_copy.write_bytes(b"copy")
    items = [
        {"reference_id": "core", "path": str(core), "pack": "Core Library", "source_native": {"ableton_sounds": ["Bass|Synth Bass"], "ableton_tags": []}},
        {"reference_id": "desktop", "path": str(desktop_copy), "pack": None, "source_native": {"ableton_sounds": ["Bass|Synth Bass"], "ableton_tags": []}},
    ]
    monkeypatch.setattr(bass_instrument_catalog, "query_items", lambda filters=None: items)

    entries, manifest = build_bass_instrument_catalog()

    assert [entry["reference_id"] for entry in entries] == ["core"]
    assert manifest["audit"]["skipped_outside_ableton_library"] == 1
