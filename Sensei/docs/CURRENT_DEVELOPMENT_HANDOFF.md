# Sensei Current Development Handoff

Last updated: 2026-08-14, Europe/Istanbul

> START HERE. Read this file, `docs/instrument_genre_generation_contract.md`,
> and `.agents/AGENTS.md` before changing the runtime or Ableton extension.

## 2026-08-14 session log

Extension is now `0.6.10` (`RUNTIME_VERSION: phase6-v11`). Real ArrangementGPS
batch runs against a fresh (non-Pop) genre/mode combination surfaced several
issues the earlier sessions hadn't exercised:

1. **Batch diversity fallback-pool bug (v0.6.9)** — `_exclude_used()` (added
   2026-08-13 for source diversity) can narrow a candidate pool down to
   sources that structurally fail the target profile (e.g. a "bassline"-
   tagged clip that is actually polyphonic, violating `max_polyphony=1`)
   even though the excluded, already-used source itself is valid. Fixed in
   `core/midi_variation_engine.py::generate_midi_variation`: try the
   exclusion-narrowed pool first, and only if every candidate in it fails
   structural validation, retry against the full mode-narrowed pool
   (allowing a repeat) before giving up. New regression test in
   `tests/test_midi_variation_engine.py`.
   **Process note:** the fix had zero effect the first time it "shipped"
   because `npm run package` was never re-run after the source edit — the
   embedded Python runtime is a separate bundled copy baked into the
   `.ablx`, cached by `RUNTIME_VERSION` on disk. Editing `core/*.py` alone
   does nothing until the extension is actually rebuilt and reinstalled.
2. **Drum Rack SDK-recognition gap (v0.6.10)** — some factory "kit" presets
   never resolve to the SDK's `DrumRack` class (`device instanceof DrumRack`
   is false), so `verifiedDrumTarget()` falls through to the bass/chord path
   and fails with `instrument_role_unresolved`. Added a fallback in
   `extension.ts` to also accept any `RackDevice` whose `.chains` are all
   `instanceof DrumChain` (real pad-note evidence, independent of the
   parent device's wrapper class) — kept as a safety net, but it did **not**
   resolve the specific case tested ("Tomorrow Kit.adg", Punch and Tilt
   pack): its chains aren't `DrumChain`-backed either, meaning it's
   architecturally a plain Instrument Rack with zero pad-note data in
   Live's engine — nothing on the extension side can generate for it.
   Dataset-side investigation found 86 drum-role presets across 5 packs
   (Glitch and Wash, Drum Booth, Punch and Tilt, Lost and Found, Voice Box)
   share the same `native_drums` tag combination including `"Drum Loop"` —
   suspected same architecture, only one instance (Tomorrow Kit) actually
   confirmed broken.
3. **ArrangementGPS instrument-selection compatibility pass** (in the
   sibling `ArrangementGPS/engine` repo, see
   [[project_arrangementgps_variants]] memory for full detail): the static
   fallback table in `createSessionPlan.js` used vague single-word browser
   search terms ("Pad", "Strings", "Sub Bass", "Lead Synth"...) that matched
   whatever the Live browser tree hit first — frequently *not* a device in
   Sensei's identity catalog at all, guaranteeing `instrument_role_unresolved`
   downstream. Replaced every fallback with an exact, catalog-confirmed
   preset name (verified 1:1 against `ableton_preset_genre_identities.jsonl`)
   and excluded `"Drum Loop"`-tagged presets from the genre-matched index
   entirely. Melody-role tracks (`melody.lead/counter/texture`) now resolve
   to `null` instead of loading a device — Sensei has no live-wired role for
   melody at all (only bass/chord/drum), so ArrangementGPS no longer opens
   an instrument Sensei can never write to.
4. Also this session (ArrangementGPS side): dynamic per-prompt project
   naming (`Builds/<prompt-slug>-<timestamp>/` instead of always overwriting
   one fixed `Local_Engine_Test` project — `ArrangementGPSBuilder.py`
   already picked the newest by mtime, no change needed there); an offline
   fallback for `generateCreativeBrief()` when `OPENAI_API_KEY` is unset
   (mode/genre already come from the LLM-free `deriveMoodFromPrompt`, so the
   rest of the pipeline doesn't depend on a real brief); and
   `ArrangementGPSBuilder.py` now also sets Live's own Song Key
   (`song.root_note`/`song.scale_name`) from the build's `project.key` —
   previously only the Sensei bridge file got `target_root`/`target_mode`,
   so the project always displayed Live's default C Major regardless of
   what mood the prompt actually derived.

**Not yet verified live as of end of session:** the full compatibility pass
(item 3) — batch has not been re-run against the corrected instrument
selection yet. Next session should start there.

## Product objective

Sensei must inspect the selected Ableton instrument, resolve its role and
genre from release-pinned Ableton evidence, select only the matching dataset
pool, create a constrained variation, and write it to the selected Session
View slot:

```text
selected device -> drum/bass/chord -> genre proximity -> locked corpus -> variation -> selected slot
```

- Drum targets use drum material and only verified Drum Rack pad notes.
- Bass targets use bass material.
- Chord targets use chord material.
- A unique highest genre-proximity score wins regardless of absolute score.
- Exact top-score ties use deterministic multi-genre synthesis.
- Unknown or ambiguous target identity remains fail-closed.

## Current release state

- Source/package version: `0.6.10` (see "2026-08-14 session log" above for
  the latest changes; the rest of this section is historical and may be
  stale on specific numbers — check `package.json`/`manifest.json` directly).
- Package:
  `extensions/sensei-midi-writer/dist/sensei-midi-writer.ablx`
- Locked dataset release: `data/dataset_releases/phase6/`.
- Embedded runtime cache key: `phase6-v11` (was `phase6-v3` when this
  section was first written).
- Latest test result: `105 passed, 2 skipped` (`tests/`, scoped — includes 5
  new bass/chord role-detection cases in `test_live_context_resolver.py`; the
  2 skips are a pre-existing missing `mido` module and a missing `.alc`
  fixture, unrelated to this session's change).
- Preset identities: 2,252.
- Canonical MIDI corpus: 1,812 entries.
- Variation sources: 2,240.
- Suite Core Library canonical ALC sources: 121.
- Direct `.mid`/`.midi` canonical sources: 51.

This session added bass/chord recognition (source only; not yet packaged,
installed, or verified in a running Live instance — see "Bass/chord role
detection" below and the first item under "Remaining product work"). The
extension has not been rebuilt (`npm run build`/`npm run package`) or
reinstalled since these changes; Live still runs whatever `.ablx` was
installed before this session.

## Important fixes already made

1. The extension runtime is gzip/base64 embedded in `dist/extension.js` because
   Extension Host cannot read the Desktop project and `.ablx` ignores arbitrary
   runtime directories during installation.
2. The runtime materializes under Ableton `Extensions Data` on first Generate.
3. Drum Rack device class and receiving-note pads are read through the official
   SDK before every write.
4. `default` genre fallback was removed.
5. Phase 6 contains a dataset-derived neighbor graph and preset identity index.
6. Core Library preset paths are indexed alongside Music/Ableton content.
7. Standard/Suite/Beta duplicates do not receive different trust weights.
8. Related ALC evidence comes from the unified Live index; Suite is not
   penalized or shadowed by a smaller installation.
9. `.alc`, `.mid`, and `.midi` corpus admission is supported. Duplicate content
   is removed by SHA-256 before expensive parsing.
10. Context-menu Generate is registered for both `ClipSlot` and `MidiClip`.
    A MidiClip command resolves its parent ClipSlot before writing.
11. The old runtime-cache bug was fixed by bumping the cache key from
    `phase6-v1` to `phase6-v2`. Every future embedded runtime/data change must
    bump this key again.
12. `RUNTIME_VERSION` was bumped from `phase6-v2` to `phase6-v3` for the
    bass/chord role-detection change (`live_context_resolver.py` +
    `extension.ts` both changed; see "Bass/chord role detection" below).

## Bass/chord role detection

Added this session. The installed `@ableton-extensions/sdk` (`1.0.0-beta.0`)
only exposes rich per-device evidence for `DrumRack` (chains/receivingNote)
and `Simpler` (`sample.filePath`). Every other instrument — Wavetable,
Operator, Bass, Electric, Tension, third-party plugins, and every bass/chord
preset in the dataset — surfaces through the SDK as a generic `Device` with
only `.name`. There is no SDK-exposed loaded-preset file path or Ableton
tag/genre metadata for a generic device, so the `loaded_preset_path` matching
branch in `target_resolver.py` (`_profile_from_native_catalog`) cannot be fed
from the live extension for these instruments.

Instead, `verifiedInstrumentTarget()` in `extension.ts` collects the `.name`
of every non-`DrumRack` device on the selected track (audio effects are
harmless — their names never appear in the identity catalog and are silently
ignored) and forwards the full list as `device_names`. On the Python side,
`core/live_context_resolver.py`'s new `_resolve_instrument_target()` matches
each name against the same release-pinned
`ableton_preset_genre_identities.jsonl` used for genre resolution (437 bass /
732 chord / 1083 drum entries in Phase 6). If exactly one device name matches
exactly one role (bass or chord), that role and device name proceed to the
existing `resolve_preset_genre()` path unchanged. Zero matches, a name
matching both roles, or more than one device resolving on the same track all
raise a `ValueError` and block the write — same fail-closed posture as
`multiple_drum_racks` for drum targets.

This required no new embedded data: the identity catalog was already part of
the Phase 6 release and already embedded via `build.ts`.

## Ableton installation and cache trap

Live Beta does not reliably reload a changed `.ablx`. Use this sequence:

1. Package the extension.
2. Quit Live and choose **Don't Save** for the empty test set.
3. Move the previous installed directory out of
   `~/Library/Application Support/Ableton/Extensions/` if Live refuses update.
4. Start Live Beta.
5. Settings -> Extensions -> Choose File -> select the `.ablx`.
6. Confirm installation.
7. Quit Live again and choose **Don't Save**.
8. Restart Live.

Installed files:

```text
~/Library/Application Support/Ableton/Extensions/ai-producer.sensei-midi-writer
```

Materialized data/runtime:

```text
~/Library/Application Support/Ableton/Extensions Data/ai-producer.sensei-midi-writer
```

Never reuse an old `RUNTIME_VERSION` after changing embedded Python or release
artifacts. A stale `.ready` marker will silently retain old code. This caused a
real failure where the menu appeared but no clip was created because the old
runtime still enforced `genre_neighbor_confidence_low`.

## Live verification checklist

Test both context-menu scopes:

1. Load one cataloged Drum Rack (examples used: `505 Classic Kit`,
   `606 Core Kit`, `212 Kit`).
2. Right-click an empty Session ClipSlot: `Sensei: Generate` must appear.
3. Generate: a MIDI clip must be created in that exact slot.
4. Right-click the created MIDI clip: `Sensei: Generate` must also appear.
5. Generate again: notes must be replaced in the existing clip.
6. Inspect `current_live_target.json`; device name and verified pad notes must
   match the loaded rack.
7. Confirm no generated pitch is outside `verified_pad_notes`.
8. Confirm runtime CLI returns `ready_to_write` before diagnosing SDK writing.

Manual CLI diagnostic:

```bash
runtime="$HOME/Library/Application Support/Ableton/Extensions Data/ai-producer.sensei-midi-writer/runtime/phase6-v3"
/usr/bin/python3 "$runtime/tools/generate_cli.py" \
  --live-target "$HOME/Library/Application Support/Ableton/Extensions Data/ai-producer.sensei-midi-writer/current_live_target.json" \
  --data-root "$runtime/data"
```

## Remaining product work

- Perform the clean 0.5.0 Live install and complete empty-slot and
  existing-MidiClip end-to-end tests for drum, bass, and chord targets.
- Bass/chord device identity/context creation through the SDK is implemented
  (`verifiedInstrumentTarget` in `extension.ts` + role detection in
  `core/live_context_resolver.py`, see below) but has not been verified
  against a real running Live instance yet — that verification still needs to
  happen in this session or the next.
- Confirm what stable SDK evidence is available when Live reports only a
  generic device name rather than the loaded preset name.
- Add structured generation diagnostics/provenance persistence to Extensions
  Data so Live failures do not depend only on a modal message.
- Add a release builder command for incremental/batched Core Library corpus
  updates instead of the one-off merge procedure used in this session.
- Consider cleanup/versioning policy for old materialized runtime directories.

## Files central to the current architecture

- `extensions/sensei-midi-writer/src/extension.ts`
- `extensions/sensei-midi-writer/build.ts`
- `core/live_context_resolver.py`
- `core/midi_runtime.py`
- `core/midi_variation_engine.py`
- `core/generate_gate.py`
- `ableton/genre_identity.py`
- `ableton/canonical_midi_corpus.py`
- `ableton/variation_corpus.py`
- `data/dataset_releases/phase6/dataset_release.manifest.json`

## Safety invariants

- One verified target, one selected slot, one official SDK write path.
- Track names never authorize target identity.
- Drum writes require a real Drum Rack and verified pad notes.
- Runtime consumes only manifest-pinned artifacts with matching hashes.
- A blocked report must never mutate MIDI.
- Preserve existing user work and unrelated workspace changes.

