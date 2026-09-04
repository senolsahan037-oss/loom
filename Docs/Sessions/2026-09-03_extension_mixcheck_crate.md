# Session log — 2026-09-03 → 04

## What shipped (all on `main`, CI green at the end)
- **Extensions SDK 1.0.0-beta.1** adopted via `vendor/` (untracked tarballs); Live 12.4.15b1 beta installed; the extension installed by unpacking the `.ablx` into Live's Extensions folder (no dialog).
- **Bridge inside the extension** (`Sensei/extensions/sensei-midi-writer/src/bridge.ts`): same request/done file contract as the control surface, queue under the extension's storage dir. Ops: state, tempo, mixer, device params, locators, arrangement + session MIDI clips, `create_midi_track`, `import_audio_clip`, `render_pre_fx`. Refuses transport, key write, preset loading with the SDK reason.
- **MCP picks the endpoint per call**: fresh extension bridge first, control surface as fallback; ops the extension refuses are handed to the surface (`fallback` in the answer); presets loaded on adopted tracks via `load_instrument_on_adopt`.
- **GAP-007 closed**: `project_build` creates the plan's tracks (surface `create_midi_track` with browser preset load, `set_key`). Real from-scratch builds proven twice: through the surface (17 tracks, 39 clips, 56 s) and through the extension with surface hand-over (69 s).
- **Three latent bugs found by real runs**: chord default profile id never existed; locators cannot sit past the arrangement end; Live defers its cue-list refresh (a retry deleted the cue).
- **Mix Check inside the MCP** (`MixAnalyzer/subverse_mix`, pip-installable): `mix_measure`, `mix_analyze` (compact by default), `mix_profiles`; the Launchpad service now imports this engine.
- **Crate agent** (`SampleAgent/`): sample-reader + Sampler engine under `crate_fetch/read/spots/chop/agent`; Launchpad packages redirect to the single copy. Proven on the reader's own find (Sükrü – Gülümse Biraz 1986); the user then rejected the sample and the decision ledger records it.
- **Live audio round trip**: `crate_to_live` (in-place clip when the set is unsaved), `mix_from_live` (pre-fx render → Mix Check), `mix_capture` (`resample`: Live records itself through five one-tick surface requests; `tap`: Core Audio process tap, app-bundled).
- **Centercode**: one public suggestion filed for the five SDK gaps (preset/browser loading, key write, transport, signature/length, meters).
- **CI**: audio stack installed for Python 3.13 entries; suites report SKIPPED where deps are missing.

## Measured facts worth keeping
- A second running Live (release Suite) blocks the beta's extension subsystem entirely.
- Kill a developer host before restarting Live; a stale host re-attaches silently and nothing else can handshake.
- One control-surface tick doing create + route + arm + record + play segfaults Live; split across ticks it is fine.
- Live deletes the unsaved set's "Temp Project" folder on close — copy recordings out immediately.
- Dropping a clip on an unnamed track renames the track to the clip name.

## Open at close of day
- `tap`: the user granted System Audio Recording (to which app is unverified); captures were still silent in the afplay test after the grant. Next: test against Live playing, and confirm "Loom LiveTap" is the entry that is on.
- `importIntoProject`: untested on a saved project (fails with an undefined reason on an unsaved set).
- Centercode answer: check the "following" list.
