from ableton.instrument_capabilities import build_instrument_capability_catalog
from core.target_resolver import resolve_target_profile


def _catalog():
    return build_instrument_capability_catalog()[0]


def test_unknown_or_track_name_only_target_is_not_writable():
    result = resolve_target_profile({"track_name": "My Bass Channel"}, _catalog())

    assert result["writable"] is False
    assert result["profile"] is None
    assert result["reason"] == "target_profile_unresolved"


def test_verified_drum_device_requires_a_verified_pad_map():
    unsafe = resolve_target_profile(
        {"device_classes": ["DrumGroupDevice"], "verified_pad_map": False}, _catalog()
    )
    safe = resolve_target_profile(
        {"device_classes": ["DrumGroupDevice"], "verified_pad_map": True, "verified_pad_notes": [36, 38, 42]}, _catalog()
    )

    assert unsafe["profile"]["profile_id"] == "ableton.drum-rack.v1"
    assert unsafe["writable"] is False
    assert unsafe["reason"] == "verified_pad_map_required"
    assert safe["writable"] is True
    assert safe["reason"] == ""
    assert safe["profile"]["constraints"]["allowed_pitches"] == [36, 38, 42]


def test_verified_drum_device_rejects_missing_pad_notes():
    result = resolve_target_profile(
        {"device_classes": ["DrumGroupDevice"], "verified_pad_map": True}, _catalog()
    )
    assert result["writable"] is False
    assert result["reason"] == "verified_pad_notes_required"


def test_bass_requires_explicit_profile_binding_and_never_uses_track_name():
    unbound = resolve_target_profile(
        {"track_name": "Bass", "device_classes": ["InstrumentVector"]}, _catalog()
    )
    bound = resolve_target_profile(
        {
            "track_name": "Bass",
            "device_classes": ["InstrumentVector"],
            "explicit_profile_id": "ableton.bass.monophonic.v1",
        },
        _catalog(),
    )

    assert unbound["writable"] is False
    assert bound["writable"] is True
    assert bound["profile"]["target_role"] == "bass"
    assert bound["binding_evidence"] == "explicit_profile"


def test_native_catalog_path_binds_bass_and_chord_without_using_track_name(tmp_path):
    bass_path = tmp_path / "Native Bass.adg"
    chord_path = tmp_path / "Native Pad.adg"
    bass_path.write_bytes(b"bass")
    chord_path.write_bytes(b"chord")
    instrument_catalog = [
        {"path": str(bass_path), "profile_id": "ableton.bass.synth.v1"},
        {"path": str(chord_path), "profile_id": "ableton.chord.pad.v1"},
    ]

    bass = resolve_target_profile({"loaded_preset_path": str(bass_path), "track_name": "Pads"}, _catalog(), instrument_catalog)
    chord = resolve_target_profile({"loaded_preset_path": str(chord_path), "track_name": "Bass"}, _catalog(), instrument_catalog)

    assert bass["writable"] is True
    assert bass["profile"]["target_role"] == "bass"
    assert chord["writable"] is True
    assert chord["profile"]["target_role"] == "chord"
    assert bass["binding_evidence"] == chord["binding_evidence"] == "native_catalog_path"
