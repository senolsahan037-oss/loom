# Sensei — MIDI Dataset and Variation Runtime

> **Agent/developer start point:**
> [Current Development Handoff](docs/CURRENT_DEVELOPMENT_HANDOFF.md)

The primary product contract is **selected Ableton instrument -> verified role
and native genre -> matching locked MIDI/ALC corpus -> safe variation ->
selected clip slot**. See [Instrument and Genre Generation Contract](docs/instrument_genre_generation_contract.md).

Sensei is a MIDI-only Ableton Live companion. It does one thing through one safe path:

```text
locked Ableton dataset → evidenced target binding → constrained MIDI variation → SDK payload
```

It does not scan mutable source material during generation, infer an instrument from a track name, write through Remote Scripts, or contain a second drum-only generator.

## One entry point

`core.midi_runtime.prepare_midi_variation()` is the only generator-facing API.

It reads the immutable Phase 6 release manifest, resolves the target from either a native preset path, an explicit profile, or a verified Drum Rack, then returns either:

- a `sensei.sdk-midi-write.v1` payload for the official `sensei-midi-writer` extension; or
- a structured no-write response explaining why the target/data is not safe.

The runtime never writes to Live itself. The SDK extension owns that final action.

## Directory layout

```text
Sensei/
  ableton/       # Suite index, native metadata, inspectors and dataset builders
  core/          # target_resolver, midi_variation_engine, midi_runtime
  data/          # locked corpus, target profiles, instrument catalogs, releases
  extensions/    # official SDK output extension
  tests/         # current architecture verification
  docs/          # contracts and dataset policy
  tools/         # optional conversational Sensei agent
```

The drum rack inspector remains under `ableton/inspector`: it is evidence for pad-safe drum binding, not a competing generation path.

## Dataset contract

- Canonical MIDI clips are parse-verified `.alc`, `.mid`, and `.midi` content with native Ableton tags and genres.
- Bass and chord presets bind only when their actual loaded path is in the native Suite catalogs.
- Genres are matched against Ableton-native genre metadata, never filename guesses.
- Groove `.agr` files are preserved as parse-verified timing templates; they are not inferred genre data.
- The Phase 6 manifest hashes every artifact consumed by the runtime, including preset identities and the genre-neighbor graph.

## Verify

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider
```

Further details: [MIDI Targeted Dataset Architecture](docs/midi_targeted_dataset_architecture_v1.md).
