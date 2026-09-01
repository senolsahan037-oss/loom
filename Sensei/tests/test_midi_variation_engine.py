from ableton.instrument_capabilities import build_instrument_capability_catalog
from core.midi_variation_engine import _apply_variation, generate_midi_variation


def _profile(profile_id):
    return next(entry for entry in build_instrument_capability_catalog()[0] if entry["profile_id"] == profile_id)


def _clip(reference_id, *, tags, genres, events, loop_end=4.0):
    return {
        "reference_id": reference_id,
        "name": reference_id,
        "source_native": {"ableton_tags": tags, "ableton_genres": genres},
        "genres": genres,
        "timeline": {"loop_start": 0.0, "loop_end": loop_end, "cycle_beats": loop_end},
        "events": events,
    }


def test_bass_variation_uses_only_native_bassline_and_matching_native_genre():
    corpus = [
        _clip("bass", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 72, "time": 0, "duration": 1, "velocity": 100}]),
        _clip("chord", tags=["Clips|Music Clip|Chord Progression"], genres=["Trap"], events=[{"pitch": 60, "time": 0, "duration": 1, "velocity": 100}]),
        _clip("wrong-genre", tags=["Clips|Music Clip|Bassline"], genres=["Jazz"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}]),
    ]

    result = generate_midi_variation(corpus, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=2, seed=7)

    assert result["generation_safe"] is True
    assert result["diagnostics"]["source_reference_id"] == "bass"
    assert result["payload"]["schema_version"] == "sensei.sdk-midi-write.v1"
    assert all(28 <= event["pitch"] <= 60 for event in result["events"])
    assert all(event["pitch"] == 60 for event in result["events"])
    assert result["payload"]["clip_length"] == 8.0


def test_apply_variation_handles_bass_events_sharing_the_same_onset():
    # Regression: two source events sharing the same onset time used to raise
    # KeyError in the bass onset-nudge branch, because the first event to
    # move away from that time value already removed it from the `occupied`
    # set, and the second event at the same original time tried to remove it
    # again. Hit in a real ArrangementGPS batch run (2026-08-12).
    profile = _profile("ableton.bass.synth.v1")
    events = [
        {"pitch": 36, "time": 9.0, "duration": 0.5, "velocity": 100},
        {"pitch": 38, "time": 9.0, "duration": 0.5, "velocity": 90},
        {"pitch": 40, "time": 9.5, "duration": 0.5, "velocity": 80},
    ]
    for seed in range(30):
        result = _apply_variation(events, profile, amount=1.0, seed=seed)
        assert len(result) == len(events)


def test_apply_variation_never_nudges_a_bass_event_past_the_clip_end():
    # Regression: the bass onset-nudge boundary check recomputed max(end) on
    # every iteration from the already-mutated `output`, so an earlier nudge
    # widened the boundary for later events, and the check only bounded the
    # nudged *onset* (with +grid slack) rather than onset+duration -- a late
    # event could be nudged forward until its own end overshot the clip's
    # true length, producing a payload the SDK write path rejected with
    # "Invalid clip_length." Hit in a real ArrangementGPS batch run
    # (2026-08-14, seed reproduces at seed=2 with the pre-fix code).
    profile = _profile("ableton.bass.synth.v1")
    events = [
        {"pitch": 36, "time": 0.0, "duration": 0.5, "velocity": 100},
        {"pitch": 38, "time": 1.0, "duration": 0.5, "velocity": 90},
        {"pitch": 40, "time": 15.75, "duration": 0.25, "velocity": 80},
    ]
    clip_end = max(event["time"] + event["duration"] for event in events)
    for seed in range(200):
        result = _apply_variation(events, profile, amount=1.0, seed=seed)
        assert all(event["time"] + event["duration"] <= clip_end + 1e-9 for event in result)


def test_chord_variation_preserves_voicing_and_rejects_wrong_role():
    corpus = [
        _clip("chord", tags=["Clips|Music Clip|Chord Progression"], genres=["Synthpop"], events=[
            {"pitch": 60, "time": 0, "duration": 1, "velocity": 100},
            {"pitch": 64, "time": 0, "duration": 1, "velocity": 100},
            {"pitch": 67, "time": 0, "duration": 1, "velocity": 100},
        ]),
    ]
    result = generate_midi_variation(corpus, target_profile=_profile("ableton.chord.pad.v1"), genre="Synthpop", bars=1, seed=2)
    assert result["generation_safe"] is True
    assert len(result["events"]) == 3
    assert len({event["pitch"] for event in result["events"]}) == 3

    rejected = generate_midi_variation(corpus, target_profile=_profile("ableton.bass.808.v1"), genre="Synthpop", bars=1, seed=2)
    assert rejected["generation_safe"] is False
    assert rejected["error"] == "no_native_role_and_genre_candidate"


def test_target_mode_prefers_matching_key_mode_candidate():
    minor_clip = _clip("minor-bass", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}])
    minor_clip["key_root"], minor_clip["key_mode"] = "C", "Minor"
    major_clip = _clip("major-bass", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}])
    major_clip["key_root"], major_clip["key_mode"] = "C", "Major"
    corpus = [minor_clip, major_clip]

    result = generate_midi_variation(corpus, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=1, seed=1, target_mode="Minor")

    assert result["generation_safe"] is True
    assert result["diagnostics"]["source_reference_id"] == "minor-bass"


def test_target_root_transposes_selected_clip():
    clip = _clip("c-bass", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}])
    clip["key_root"], clip["key_mode"] = "C", "Minor"

    baseline = generate_midi_variation([clip], target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=1, seed=1, variation_amount=0)
    transposed = generate_midi_variation([clip], target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=1, seed=1, variation_amount=0, target_root="D")

    assert baseline["generation_safe"] is transposed["generation_safe"] is True
    # C -> D is +2 semitones; any additional octave-fit shift is a multiple
    # of 12, so the pitch classes must differ by exactly 2 mod 12.
    baseline_pitch = baseline["events"][0]["pitch"]
    transposed_pitch = transposed["events"][0]["pitch"]
    assert (transposed_pitch - baseline_pitch) % 12 == 2
    assert transposed["payload"]["provenance"]["target_root"] == "D"


def test_target_root_and_mode_never_applied_to_drum_role():
    clip = _clip("kit", tags=["Clips|Drum Clip|Full Drum Clip"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}])
    clip["key_root"], clip["key_mode"] = "C", "Minor"
    drum_profile = _profile("ableton.drum-rack.v1")
    drum_profile = {**drum_profile, "constraints": {**drum_profile["constraints"], "allowed_pitches": [36]}}

    baseline = generate_midi_variation([clip], target_profile=drum_profile, genre="Trap", bars=1, seed=1, variation_amount=0)
    with_key = generate_midi_variation([clip], target_profile=drum_profile, genre="Trap", bars=1, seed=1, variation_amount=0, target_root="D", target_mode="Major")

    assert baseline["generation_safe"] is with_key["generation_safe"] is True
    assert baseline["events"] == with_key["events"]


def test_exclude_reference_ids_narrows_to_the_one_unused_candidate():
    corpus = [
        _clip("bass-a", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}]),
        _clip("bass-b", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 38, "time": 0, "duration": 1, "velocity": 100}]),
        _clip("bass-c", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 40, "time": 0, "duration": 1, "velocity": 100}]),
    ]

    result = generate_midi_variation(
        corpus, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=1, seed=1,
        exclude_reference_ids=["bass-a", "bass-b"],
    )

    assert result["generation_safe"] is True
    assert result["diagnostics"]["source_reference_id"] == "bass-c"


def test_exclude_reference_ids_falls_back_to_a_reused_source_when_the_unused_pool_cannot_satisfy_the_profile():
    # Regression (real Live batch run, 2026-08-14): excluding already-used
    # sources can leave only candidates that structurally fail the target
    # profile -- here a "bassline"-tagged clip with two simultaneous notes,
    # which violates the bass profile's max_polyphony=1 -- even though the
    # excluded source itself passes. Diversity is a preference and must
    # never turn an otherwise-valid write into a blocked one.
    valid_clip = _clip("bass-good", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}])
    polyphonic_clip = _clip("bass-polyphonic", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[
        {"pitch": 36, "time": 0, "duration": 1, "velocity": 100},
        {"pitch": 40, "time": 0, "duration": 1, "velocity": 100},
    ])
    corpus = [valid_clip, polyphonic_clip]

    result = generate_midi_variation(
        corpus, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=1, seed=1,
        exclude_reference_ids=["bass-good"],
    )

    assert result["generation_safe"] is True
    assert result["diagnostics"]["source_reference_id"] == "bass-good"


def test_exclude_reference_ids_never_empties_the_pool():
    corpus = [
        _clip("bass-a", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 36, "time": 0, "duration": 1, "velocity": 100}]),
        _clip("bass-b", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[{"pitch": 38, "time": 0, "duration": 1, "velocity": 100}]),
    ]

    result = generate_midi_variation(
        corpus, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=1, seed=1,
        exclude_reference_ids=["bass-a", "bass-b"],
    )

    assert result["generation_safe"] is True
    assert result["diagnostics"]["source_reference_id"] in ("bass-a", "bass-b")


def test_variation_is_seeded_and_can_be_disabled_without_changing_source_notes():
    corpus = [_clip("bass", tags=["Clips|Music Clip|Bassline"], genres=["Trap"], events=[
        {"pitch": 48, "time": 0, "duration": 1, "velocity": 100},
        {"pitch": 50, "time": 1, "duration": 1, "velocity": 100},
    ])]
    profile = _profile("ableton.bass.synth.v1")
    plain = generate_midi_variation(corpus, target_profile=profile, genre="Trap", bars=1, seed=9, variation_amount=0)
    varied = generate_midi_variation(corpus, target_profile=profile, genre="Trap", bars=1, seed=9, variation_amount=1)

    assert plain["generation_safe"] is varied["generation_safe"] is True
    assert plain["events"] != varied["events"]
    assert varied["payload"]["provenance"]["variation_amount"] == 1
