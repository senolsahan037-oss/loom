# Sensei Instrument and Genre Generation Contract

## Product goal

Sensei must identify the selected Ableton instrument and its musical role,
resolve the instrument's Ableton-native genre context, select only matching
MIDI/ALC material from the locked dataset, create a constrained variation, and
write it to the explicitly selected Live clip slot.

```text
selected Live instrument
  -> verified preset/device identity
  -> role: drum | bass | chord
  -> Ableton-native genre evidence
  -> locked corpus filtered by role + genre
  -> target-safe variation
  -> selected clip slot
```

This is Sensei's primary product contract. Dataset locking, target validation,
variation, and SDK writing exist to support this flow; they are not separate
product goals.

## Required behavior

### Drum channel

- Bind only to a real SDK `DrumRack` on the selected MIDI track.
- Read the rack's actual receiving notes and treat them as the writable pad map.
- Resolve the loaded kit to a preset/pack identity when evidence permits.
- Select only canonical drum clips with the resolved Ableton genre.
- Emit only notes present in the verified pad map.

### Bass channel

- Resolve the selected preset against the locked bass instrument catalog.
- Derive a concrete bass target profile from Ableton `Sounds|Bass|...` evidence.
- Select only canonical bass clips with the resolved Ableton genre.
- Preserve monophony, pitch range, duration, and target-profile constraints.

### Chord channel

- Resolve the selected preset against the locked chord instrument catalog.
- Derive the chord family from Ableton `Sounds|Piano & Keys|...` or `Sounds|Pad`
  evidence.
- Select only canonical chord material with the resolved Ableton genre.
- Preserve voicing groups, polyphony, pitch range, and duration constraints.

## Evidence hierarchy

Sensei must not use a track name as instrument or genre evidence.

Instrument and role evidence, strongest first:

1. Exact loaded preset path matched to a release-pinned catalog entry.
2. A unique preset identity match using SDK device type/name plus catalog pack
   and Ableton sound tags.
3. A verified SDK device structure, such as `DrumRack` plus receiving-note pad
   chains, for role and write-safety only.
4. Explicit user selection from a bounded list of catalog-backed candidates.

Genre evidence, strongest first:

1. Ableton-native `Genres|...` metadata attached to the resolved source/preset.
2. A unique genre associated with the resolved Ableton pack.
3. Explicit user selection from the pack's observed native-genre candidates.

Filename tokens, track names, arbitrary defaults, and model guesses are not
genre evidence.

## Ambiguity policy

If one preset name or pack maps to multiple credible genres, Sensei must show a
bounded genre chooser. It must not silently select the most common genre.

If a device identity maps to multiple catalog presets, Sensei must request a
bounded preset/pack choice or block the write.

Every unresolved or ambiguous state is fail-closed: no MIDI changes.

## Dataset selection contract

Generation candidates must satisfy both:

- native role tag appropriate for the resolved target; and
- exact case-insensitive match to the resolved Ableton-native genre.

The runtime must consume only artifacts pinned and hashed by the active dataset
release manifest. Mutable library scans are allowed to build a future release,
not to feed generation directly.

## Context contract

The active project context records evidence, not assumptions. It must include:

```json
{
  "target_context": {
    "role": "drum",
    "profile_id": "ableton.drum-rack.v1",
    "binding_evidence": "live_sdk_drum_rack",
    "preset_identity": {},
    "verified_pad_notes": [36, 38, 42]
  },
  "genre": "Hip Hop",
  "genre_evidence": {},
  "bars": 4,
  "seed": 1,
  "variation_amount": 0.35
}
```

The Live extension stores this context in its permitted Extensions Data
directory. It must refresh Live-derived evidence before every write so stale
instrument bindings cannot authorize generation.

## Current implementation status

Implemented:

- Phase 5 release-pinned and hash-verified generation artifacts.
- Canonical MIDI/ALC corpus with Ableton-native role and genre metadata.
- Bass and chord preset catalogs with native paths, packs, sound tags, and
  target profiles.
- Deterministic role+genre corpus filtering and constrained variation.
- Fail-closed target resolution and official SDK clip writing.
- Live SDK Drum Rack detection and verified receiving-note extraction.
- Extension sandbox-compatible embedded runtime.
- Phase 6 release-pinned preset identities and dataset-derived genre-neighbor
  graph.
- Unique highest-score genre selection and exact-tie multi-genre synthesis.

Incomplete:

- Automatic bass and chord identity resolution from the current Live device.
- Automatic context refresh for all three roles.
- End-to-end tests covering instrument -> role -> genre -> corpus -> Live write.

Genre resolution never falls back to `default`. The highest proximity score is
authoritative. Exact top-score ties use deterministic synthesis across the tied
genres and preserve every contributing genre in provenance.

## Definition of done

The feature is complete only when all of these pass in Live:

1. Drag a cataloged Drum Rack, select an empty slot, generate, and receive a
   variation from the matching drum genre using only verified pads.
2. Drag a cataloged bass preset and receive a monophonic variation from the
   matching bass genre.
3. Drag a cataloged chord/pad/keys preset and receive matching chord material.
4. Equal highest genre scores synthesize the tied genre pools deterministically.
5. Unknown instruments, ambiguous presets, missing genres, stale contexts, and
   unsupported pads produce a structured block and change no MIDI.
6. Provenance identifies preset/pack evidence, role, genre, source clip, target
   profile, dataset release, seed, and variation amount.
