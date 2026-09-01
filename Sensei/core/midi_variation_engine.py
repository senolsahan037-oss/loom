"""Dataset-first, deterministic MIDI variation engine for Sensei target profiles."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable

from ableton.instrument_capabilities import validate_target_profile


SCHEMA_VERSION = "sensei.midi-variation.v1"
SDK_SCHEMA_VERSION = "sensei.sdk-midi-write.v1"
_ROLE_TAGS = {
    "bass": {"Clips|Music Clip|Bassline"},
    "chord": {"Clips|Music Clip|Chord Progression", "Clips|Music Clip|Single Chord"},
    "drum": {"Clips|Drum Clip|Full Drum Clip", "Clips|Drum Clip|Top Drum Clip", "Clips|Drum Clip|Percussion Clip"},
    "arp": {"Clips|Music Clip|Arpeggio"},
}


def load_canonical_midi_corpus(path: str | Path) -> list[dict[str, Any]]:
    """Load a release-built canonical corpus; source files are never reparsed here."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _native_role(entry: dict[str, Any], role: str) -> bool:
    tags = {str(value) for value in (entry.get("source_native") or {}).get("ableton_tags") or []}
    return bool(tags & _ROLE_TAGS.get(role, set()))


def _native_genre(entry: dict[str, Any], genre: str | None) -> bool:
    if not genre or genre == "default":
        return True
    wanted = genre.strip().casefold()
    native = (entry.get("source_native") or {}).get("ableton_genres") or []
    return any(str(value).strip().casefold() == wanted for value in native)


_PITCH_CLASS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
    "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}
# Roles whose pitches encode a musical key. A drum rack's pitch selects a
# pad, not a scale degree -- transposing it would move a kick onto whatever
# pad happens to sit a few semitones away, so key/mode never applies there.
_KEY_AWARE_ROLES = ("bass", "chord")


def _semitone_offset(source_root: str, target_root: str) -> int:
    """Smallest signed shift (-6..+5) from source_root to target_root."""
    source_pc = _PITCH_CLASS.get(source_root)
    target_pc = _PITCH_CLASS.get(target_root)
    if source_pc is None or target_pc is None:
        return 0
    return ((target_pc - source_pc + 6) % 12) - 6


def _apply_target_key(entry: dict[str, Any], role: str, target_root: str | None) -> dict[str, Any]:
    if role not in _KEY_AWARE_ROLES or not target_root:
        return entry
    source_root = entry.get("key_root")
    if not source_root:
        return entry
    semitones = _semitone_offset(source_root, target_root)
    if not semitones:
        return entry
    transposed = [{**event, "pitch": int(event["pitch"]) + semitones} for event in entry.get("events") or []]
    return {**entry, "events": transposed}


def _prefer_mode(candidates: list[dict[str, Any]], role: str, target_mode: str | None) -> list[dict[str, Any]]:
    """Narrow to key_mode-matching candidates when any exist; never empties the list."""
    if role not in _KEY_AWARE_ROLES or not target_mode:
        return candidates
    matched = [entry for entry in candidates if entry.get("key_mode") == target_mode]
    return matched or candidates


def _exclude_used(candidates: list[dict[str, Any]], exclude_reference_ids: list[str] | None) -> list[dict[str, Any]]:
    """Narrow away already-used sources when the pool is big enough to spare them.

    Small role+genre pools (a few candidates) are common; forcing exclusion
    when it would empty the pool defeats the point -- it's better to repeat a
    source than to fail a variation that would otherwise be valid.
    """
    if not exclude_reference_ids:
        return candidates
    excluded = {str(value) for value in exclude_reference_ids}
    unused = [entry for entry in candidates if str(entry.get("reference_id")) not in excluded]
    return unused or candidates


def _fit_octave(events: list[dict[str, Any]], low: int, high: int) -> list[dict[str, Any]] | None:
    pitches = [int(event["pitch"]) for event in events]
    for octaves in sorted(range(-5, 6), key=lambda step: (abs(step), step)):
        shift = octaves * 12
        shifted = [pitch + shift for pitch in pitches]
        if all(low <= pitch <= high for pitch in shifted):
            return [{**event, "pitch": int(event["pitch"]) + shift} for event in events]
    return None


def _tile_and_normalize(entry: dict[str, Any], profile: dict[str, Any], bars: int) -> list[dict[str, Any]] | None:
    timeline = entry.get("timeline") or {}
    loop_start = float(timeline.get("loop_start", 0.0))
    loop_end = float(timeline.get("loop_end", 0.0))
    cycle = loop_end - loop_start
    if cycle <= 0:
        return None
    target_beats = float(bars) * 4.0
    source = []
    allowed_pitches = (profile.get("constraints") or {}).get("allowed_pitches")
    for raw in entry.get("events") or []:
        time = float(raw["time"])
        if allowed_pitches is not None and int(raw["pitch"]) not in allowed_pitches:
            continue
        if loop_start <= time < loop_end:
            source.append({"pitch": int(raw["pitch"]), "time": time - loop_start, "duration": float(raw["duration"]), "velocity": int(raw["velocity"])})
    if not source:
        return None
    defaults = profile.get("variation_defaults") or {}
    grid = float(defaults.get("preferred_grid") or 0.0)
    max_duration = float(defaults.get("max_note_duration_beats") or 64.0)
    result: list[dict[str, Any]] = []
    offset = 0.0
    while offset < target_beats:
        for event in source:
            time = offset + event["time"]
            if time >= target_beats:
                continue
            if grid > 0:
                time = round(time / grid) * grid
            duration = min(event["duration"], max_duration, target_beats - time)
            if duration > 0:
                result.append({**event, "time": round(time, 6), "duration": round(duration, 6)})
        offset += cycle
    constraints = profile.get("constraints") or {}
    fitted = _fit_octave(result, *constraints.get("pitch_range", [0, 127]))
    if fitted is None:
        return None
    max_events = int(defaults.get("max_events_per_bar", 9999)) * bars
    if len(fitted) > max_events:
        # Keep complete onset groups so a chord is never split into partial voicings.
        groups: dict[float, list[dict[str, Any]]] = {}
        for event in fitted:
            groups.setdefault(event["time"], []).append(event)
        trimmed: list[dict[str, Any]] = []
        for time in sorted(groups):
            if len(trimmed) + len(groups[time]) > max_events:
                break
            trimmed.extend(groups[time])
        fitted = trimmed
    return sorted(fitted, key=lambda event: (event["time"], event["pitch"]))


def _failure(reason: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "generation_safe": False, "events": [], "payload": None, "diagnostics": {}, "error": reason}


def _apply_variation(events: list[dict[str, Any]], profile: dict[str, Any], *, amount: float, seed: int) -> list[dict[str, Any]]:
    """Make a bounded musical variation without changing the target role.

    Varying happens after source selection and before validation.  It never
    invents a new pitch, breaks a chord voicing, or moves an event off the
    target grid.  That makes the operation deterministic, reversible from the
    seed, and safe for instrument-bound output.
    """
    if amount <= 0:
        return events
    defaults = profile.get("variation_defaults") or {}
    role = str(defaults.get("source_role") or profile.get("target_role"))
    grid = float(defaults.get("preferred_grid") or 0.25)
    max_duration = float(defaults.get("max_note_duration_beats") or 64.0)
    rng = random.Random(f"variation:{seed}:{profile.get('profile_id')}")
    output = [dict(event) for event in events]

    # Velocity changes are intentionally gentle: they introduce phrasing while
    # retaining the source clip's musical identity.
    velocity_span = max(1, round(18 * amount))
    for event in output:
        delta = rng.randint(-velocity_span, velocity_span)
        event["velocity"] = max(1, min(127, int(event["velocity"]) + delta))

    # A bass phrase can move selected onsets by one safe grid step.  Chords use
    # duration phrasing instead so notes in a voicing always stay together.
    if role == "bass":
        # Computed once, before any nudging: this is the clip's true end.
        # Recomputing it inside the loop (the original bug) let each nudged
        # event push the boundary further out, letting later events overshoot
        # the clip length that was already promised in the SDK payload.
        clip_end = max((float(item["time"]) + float(item["duration"]) for item in output), default=0.0)
        occupied = {float(event["time"]) for event in output}
        for event in output:
            if rng.random() > amount * 0.45:
                continue
            direction = -1 if rng.random() < 0.5 else 1
            candidate = round(float(event["time"]) + direction * grid, 6)
            # Bound by the event's own end, not just its onset -- a note
            # nudged later must still finish within the clip.
            if candidate < 0 or candidate + float(event["duration"]) > clip_end:
                continue
            if candidate not in occupied:
                # discard, not remove: two source events can legitimately
                # share the same onset (e.g. a doubled note), so this time
                # value may already have been vacated by an earlier event at
                # the same original time.
                occupied.discard(float(event["time"]))
                occupied.add(candidate)
                event["time"] = candidate
    elif role == "chord":
        grouped: dict[float, list[dict[str, Any]]] = {}
        for event in output:
            grouped.setdefault(float(event["time"]), []).append(event)
        for group in grouped.values():
            if rng.random() <= amount * 0.55:
                factor = 0.75 if rng.random() < 0.5 else 1.25
                for event in group:
                    event["duration"] = round(max(grid, min(max_duration, float(event["duration"]) * factor)), 6)
    return sorted(output, key=lambda event: (event["time"], event["pitch"]))


def generate_midi_variation(
    corpus: Iterable[dict[str, Any]],
    *,
    target_profile: dict[str, Any],
    genre: str | list[str] | None,
    bars: int,
    seed: int,
    variation_amount: float = 0.35,
    target_root: str | None = None,
    target_mode: str | None = None,
    exclude_reference_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Select a native-role/native-genre source and emit a safe SDK MIDI payload."""
    if bars <= 0:
        return _failure("bars_must_be_positive")
    if not 0 <= variation_amount <= 1:
        return _failure("variation_amount_must_be_between_zero_and_one")
    role = str((target_profile.get("variation_defaults") or {}).get("source_role") or target_profile.get("target_role") or "")
    if role not in _ROLE_TAGS:
        return _failure("unsupported_target_role")
    genres = [str(value) for value in genre] if isinstance(genre, list) else ([genre] if genre else [])
    candidates = [entry for entry in corpus if _native_role(entry, role) and (not genres or any(_native_genre(entry, value) for value in genres))]
    if not candidates:
        return _failure("no_native_role_and_genre_candidate")
    mode_narrowed = _prefer_mode(candidates, role, target_mode)
    candidates = _exclude_used(mode_narrowed, exclude_reference_ids)
    candidates.sort(key=lambda entry: str(entry.get("reference_id", "")))
    rng = random.Random(seed)
    if len(genres) > 1:
        blended: list[dict[str, Any]] = []
        sources: list[Any] = []
        for genre_index, genre_name in enumerate(genres):
            genre_candidates = [entry for entry in candidates if _native_genre(entry, genre_name)]
            genre_candidates.sort(key=lambda entry: str(entry.get("reference_id", "")))
            if not genre_candidates:
                return _failure("genre_synthesis_source_missing")
            selected = genre_candidates[random.Random(f"genre-synthesis:{seed}:{genre_name}").randrange(len(genre_candidates))]
            selected = _apply_target_key(selected, role, target_root)
            events = _tile_and_normalize(selected, target_profile, bars)
            if events is None:
                return _failure("genre_synthesis_source_invalid")
            blended.extend(event for event in events if int(float(event["time"]) // 4) % len(genres) == genre_index)
            sources.append(selected.get("reference_id"))
        blended = _apply_variation(sorted(blended, key=lambda event: (event["time"], event["pitch"])), target_profile, amount=variation_amount, seed=seed)
        valid, reason = validate_target_profile(target_profile, requested_role=role, events=blended)
        if not blended or not valid:
            return _failure(reason or "genre_synthesis_empty")
        payload = {
            "schema_version": SDK_SCHEMA_VERSION, "clip_length": float(bars) * 4.0,
            "notes": [{"pitch": event["pitch"], "time": event["time"], "duration": event["duration"], "velocity": event["velocity"]} for event in blended],
            "provenance": {"source_reference_ids": sources, "target_profile_id": target_profile.get("profile_id"), "source_role": role, "genre_mode": "synthesis", "genres": genres, "seed": seed, "variation_amount": variation_amount, "target_root": target_root, "target_mode": target_mode},
        }
        return {"schema_version": SCHEMA_VERSION, "generation_safe": True, "events": blended, "payload": payload, "diagnostics": {"source_reference_ids": sources, "target_profile_id": target_profile.get("profile_id"), "source_role": role, "genre_mode": "synthesis", "genres": genres, "candidate_count": len(candidates), "seed": seed, "variation_amount": variation_amount}, "error": None}
    # Excluding already-used sources can leave only candidates that fail the
    # target profile's structural checks (e.g. a "bassline"-tagged clip that
    # turns out not to be monophonic) even though a previously-used source
    # would pass -- diversity is a preference, never a reason to block an
    # otherwise-valid write, so fall back to the full mode-narrowed pool
    # (allowing a repeat) before giving up entirely.
    attempt_pools = [candidates]
    if len(candidates) < len(mode_narrowed):
        attempt_pools.append(sorted(mode_narrowed, key=lambda entry: str(entry.get("reference_id", ""))))
    for pool in attempt_pools:
        order = list(range(len(pool)))
        random.Random(seed).shuffle(order)
        for index in order:
            selected = _apply_target_key(pool[index], role, target_root)
            events = _tile_and_normalize(selected, target_profile, bars)
            if events is None:
                continue
            events = _apply_variation(events, target_profile, amount=variation_amount, seed=seed)
            valid, reason = validate_target_profile(target_profile, requested_role=role, events=events)
            if not valid:
                continue
            payload = {
                "schema_version": SDK_SCHEMA_VERSION,
                "clip_length": float(bars) * 4.0,
                "notes": [{"pitch": event["pitch"], "time": event["time"], "duration": event["duration"], "velocity": event["velocity"]} for event in events],
                "provenance": {"source_reference_id": selected.get("reference_id"), "target_profile_id": target_profile.get("profile_id"), "source_role": role, "genre": genre, "seed": seed, "variation_amount": variation_amount, "target_root": target_root, "target_mode": target_mode},
            }
            return {"schema_version": SCHEMA_VERSION, "generation_safe": True, "events": events, "payload": payload, "diagnostics": {"source_reference_id": selected.get("reference_id"), "target_profile_id": target_profile.get("profile_id"), "source_role": role, "candidate_count": len(pool), "seed": seed, "variation_amount": variation_amount}, "error": None}
    return _failure("no_candidate_satisfies_target_profile")
