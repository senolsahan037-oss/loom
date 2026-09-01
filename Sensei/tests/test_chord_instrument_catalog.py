from ableton.chord_instrument_catalog import build_chord_instrument_catalog


def test_chord_catalog_accepts_native_keys_and_pad_presets_but_not_audio(tmp_path):
    piano = tmp_path / "Piano.adg"
    pad = tmp_path / "Pad.adv"
    audio = tmp_path / "Pad.wav"
    for path in (piano, pad, audio):
        path.write_bytes(path.name.encode())
    index = [
        {"reference_id": "piano", "path": str(piano), "source_native": {"ableton_sounds": ["Piano & Keys|Piano"]}},
        {"reference_id": "pad", "path": str(pad), "source_native": {"ableton_sounds": ["Pad"]}},
        {"reference_id": "audio", "path": str(audio), "source_native": {"ableton_sounds": ["Pad"]}},
    ]

    entries, manifest = build_chord_instrument_catalog(index)

    assert [entry["profile_id"] for entry in entries] == ["ableton.chord.pad.v1", "ableton.chord.piano.v1"]
    assert manifest["audit"]["skipped_non_midi_device"] == 1
