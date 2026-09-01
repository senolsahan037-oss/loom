import json

from ableton.instrument_capabilities import (
    build_instrument_capability_catalog,
    validate_target_profile,
    write_instrument_capability_catalog,
)


def test_catalog_contains_fail_closed_drum_and_bass_profiles(tmp_path):
    entries, manifest = build_instrument_capability_catalog()

    profiles = {entry["profile_id"]: entry for entry in entries}
    drum = profiles["ableton.drum-rack.v1"]
    bass = profiles["ableton.bass.monophonic.v1"]
    bass_808 = profiles["ableton.bass.808.v1"]
    synth_bass = profiles["ableton.bass.synth.v1"]
    assert drum["constraints"]["requires_verified_pad_map"] is True
    assert "chord" in bass["blocked_roles"]
    assert bass["constraints"]["max_polyphony"] == 1
    assert bass_808["variation_defaults"]["glide_allowed"] is True
    assert bass_808["constraints"]["pitch_range"] == [24, 48]
    assert synth_bass["variation_defaults"]["preferred_grid"] == 0.25
    assert synth_bass["constraints"]["pitch_range"] == [28, 60]
    assert manifest["entry_count"] == 13

    result = write_instrument_capability_catalog(tmp_path)
    stored_manifest = json.loads((tmp_path / "instrument_target_profiles.manifest.json").read_text())
    assert result["entry_count"] == 13
    assert len(stored_manifest["integrity"]["catalog_sha256"]) == 64


def test_target_profile_validator_rejects_cross_role_and_unsafe_bass_events():
    profiles, _ = build_instrument_capability_catalog()
    bass = next(profile for profile in profiles if profile["target_role"] == "bass")

    ok, reason = validate_target_profile(bass, requested_role="chord", events=[])
    assert ok is False
    assert reason == "role_not_allowed"

    ok, reason = validate_target_profile(
        bass,
        requested_role="bass",
        events=[
            {"pitch": 36, "time": 0.0, "duration": 1.0, "velocity": 100},
            {"pitch": 43, "time": 0.0, "duration": 1.0, "velocity": 100},
        ],
    )
    assert ok is False
    assert reason == "polyphony_limit_exceeded"

    ok, reason = validate_target_profile(
        bass,
        requested_role="bass",
        events=[{"pitch": 72, "time": 0.0, "duration": 0.5, "velocity": 100}],
    )
    assert ok is False
    assert reason == "pitch_out_of_range"


def test_target_profile_validator_rejects_pitch_outside_verified_pad_map():
    profiles, _ = build_instrument_capability_catalog()
    drum = next(profile for profile in profiles if profile["target_role"] == "drum")
    drum["constraints"]["allowed_pitches"] = [36, 38]
    ok, reason = validate_target_profile(
        drum,
        requested_role="drum",
        events=[{"pitch": 42, "time": 0.0, "duration": 0.25, "velocity": 100}],
    )
    assert ok is False
    assert reason == "pitch_not_in_verified_pad_map"
