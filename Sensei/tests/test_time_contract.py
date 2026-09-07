"""One musical-time contract for the writers: bars x beats_per_bar, where a
beat is a Live beat (quarter note). 4/4 -> 4, 3/4 -> 3, 6/8 -> 3. The engine,
the runtime and the composer must all size clips and place notes by it;
until 2026-09-05 every one of them hard-coded bars x 4."""
from ableton.instrument_capabilities import build_instrument_capability_catalog
from core.midi_variation_engine import generate_midi_variation
import pytest


def _profile(profile_id):
    return next(entry for entry in build_instrument_capability_catalog()[0] if entry["profile_id"] == profile_id)


def _clip(reference_id, *, tags, genres, events, loop_end=4.0):
    return {"reference_id": reference_id, "name": reference_id,
            "source_native": {"ableton_tags": tags, "ableton_genres": genres}, "genres": genres,
            "timeline": {"loop_start": 0.0, "loop_end": loop_end, "cycle_beats": loop_end}, "events": events}


BASS = [_clip("bass", tags=["Clips|Music Clip|Bassline"], genres=["Trap"],
              events=[{"pitch": 40, "time": 0, "duration": 0.5, "velocity": 100},
                      {"pitch": 43, "time": 2, "duration": 0.5, "velocity": 100}])]


@pytest.mark.parametrize("signature,beats_per_bar", [("4/4", 4.0), ("3/4", 3.0), ("6/8", 3.0)])
def test_engine_sizes_the_clip_by_beats_per_bar(signature, beats_per_bar):
    bars = 2
    result = generate_midi_variation(BASS, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap",
                                     bars=bars, seed=7, beats_per_bar=beats_per_bar)
    assert result["generation_safe"] is True, result["error"]
    payload = result["payload"]
    assert payload["clip_length"] == bars * beats_per_bar, signature
    assert all(note["time"] < payload["clip_length"] for note in payload["notes"]), signature
    assert payload["notes"], signature


def test_three_four_is_shorter_than_four_four_for_the_same_bars():
    four = generate_midi_variation(BASS, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=4, seed=7)
    three = generate_midi_variation(BASS, target_profile=_profile("ableton.bass.synth.v1"), genre="Trap", bars=4, seed=7, beats_per_bar=3.0)
    assert four["payload"]["clip_length"] == 16.0 and three["payload"]["clip_length"] == 12.0
    assert max(n["time"] for n in three["payload"]["notes"]) < 12.0


def test_composer_places_chords_on_the_bar_of_the_given_signature():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "MusicalIntelligence"))
    from mi import compose
    context = {"key_root": "D", "scale": "Minor"}
    waltz = compose.render(context, "chord", bars=4, seed=3, chords_per_bar=1, beats_per_bar=3.0)
    starts = sorted({n["time"] for n in waltz["notes"]})
    assert starts == [0.0, 3.0, 6.0, 9.0]
    assert {n["duration"] for n in waltz["notes"]} == {3.0}
    common = compose.render(context, "chord", bars=4, seed=3, chords_per_bar=1)
    assert sorted({n["time"] for n in common["notes"]}) == [0.0, 4.0, 8.0, 12.0]
