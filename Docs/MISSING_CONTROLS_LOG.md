# Ableton Live Missing Controls & Gap Log

This log tracks missing controls, unsupported Live Object Model (LOM) methods, unverified edge cases, and feature requests identified during active sessions.

## Entry Schema

Each gap record contains:
- **ID**: `GAP-XXX`
- **Timestamp**: ISO 8601
- **Category**: Transport / MIDI / Device / Routing / Arrangement / Session / Telemetry
- **Description**: What operation was attempted or needed
- **Observed Behavior**: Current response or limitation
- **Required Implementation**: Python remote script / M4L / OSC / file bridge requirement
- **Status**: one of
  - `RESOLVED` — Loom does this, and the entry names where.
  - `WORKED AROUND` — Live's API still lacks the accessor, but Loom produces the
    result another way. The tool is not blocked; only the vendor gap remains.
  - `PARTIALLY RESOLVED` — some of the surface works, the entry says which part does not.
  - `OPEN` — Loom genuinely cannot do this yet.

  The distinction is load-bearing: this log is served to an assistant as
  `loom://docs/gap-log`, and an entry marked `OPEN` is read as "the tool cannot",
  which makes it plan around capabilities that in fact work.

---

## Log Entries

### GAP-001
- **Timestamp**: 2026-09-01T00:17:00+03:00
- **Category**: Telemetry / State Inspection
- **Description**: Need real-time read access to selected track name, active devices, and current playhead position without writing a file request first.
- **Observed Behavior**: Current `SenseiRemote` is write-only for clip creation via `~/Documents/SenseiV2Bridge/requests/`.
- **Required Implementation**: Add periodic state dumping or bidirectional socket/OSC endpoint in the control surface or a dedicated bridge.
- **Resolved 2026-09-02**: both directions now work.
  - *Write path*: the control surface moves a consumed request to `done/` or `errors/`
    under the same filename, so a write is verifiable without any new endpoint.
    `midi_write_to_live` polls for that move and reports `WRITTEN_TO_LIVE` /
    `REJECTED_BY_LIVE` / `NOT_CONSUMED` instead of claiming success.
  - *Read path*: `capture_state()` in `AbletonScripts/Loom/bridge_ops.py` returns the
    track list with `is_selected`, each track's devices with their `class_name`, the
    tempo and `current_song_time` — the playhead this entry said was unreadable. It is
    published to `~/Documents/SenseiV2Bridge/state/live_state.json` and served by the
    `live_state` tool; `live_command` with `get_state` forces a fresh capture.
- **Status**: RESOLVED

### GAP-002
- **Timestamp**: 2026-09-01T00:17:18.474543+03:00
- **Category**: Transport
- **Description**: Real-time song play/stop status trigger
- **Observed Behavior**: No current transport trigger in the control surface
- **Required Implementation**: Add OSC or socket listener to trigger playback directly
- **Resolved 2026-09-02**: `op_transport()` in `AbletonScripts/Loom/bridge_ops.py`
  accepts `play`, `stop` and `continue`, and an optional `position` that sets
  `song.current_song_time` before starting, so playback can begin at a chosen beat.
  Reached through the `live_command` tool. No OSC listener was needed — the file
  bridge carries it, and the same request/`done` move makes the call verifiable.
- **Status**: RESOLVED

### GAP-003
- **Timestamp**: 2026-09-01T01:15:00+03:00
- **Category**: Arrangement
- **Description**: Bar-to-beat conversion when building the Arrangement View from an ArrangementGPS plan (sections are expressed in bars, `Song.createCuePoint` and `MidiTrack.createMidiClip` both take beats).
- **Observed Behavior**: The Extensions SDK exposes no song time signature. `Song` has `tempo`, `rootNote`, `scaleName`, `gridQuantization` and `gridIsTriplet`, but the only time-signature accessor in the whole API surface is on `Scene`. The Python LOM's `song.signature_numerator`/`signature_denominator` are not mirrored in the SDK.
- **Required Implementation**: A song-level time signature accessor on `Song` in the Extensions SDK, or read it from the Remote Script side and pass it through `arrangementgps_last_build.json`.
- **Current Workaround**: `BEATS_PER_BAR = 4` in `sensei-midi-writer/src/extension.ts`, applied through a single `barToBeat()` helper so there is exactly one place to change. Any project not in 4/4 will place locators and clips at the wrong times.
- **Resolved 2026-09-03 on the control-surface path**: the writers now run in the
  Remote Script, and every state it publishes carries `signature_numerator` /
  `signature_denominator`. `_beats_per_bar()` in `mcp_server/server.py` resolves, in
  order: an explicit `beats_per_bar` argument, the running session's own signature
  (fresh state, no bridge round-trip), the `.als` via `project_inspect_arrangement`,
  and only then 4/4 — reported as `beats_per_bar_source: "assumed_4_4"` rather than
  passed off as a reading. Used by `midi_write_arrangement` and `project_build`.
  Checked over real stdio: an explicit 3 beats/bar drives every locator beat.
- **Still true for the optional SDK extension**: the Extensions SDK exposes no song
  time signature, so `barToBeat()` there keeps its 4/4 constant. The extension is no
  longer part of the install.
- **Checked against Ableton on 2026-09-03**: the current Extensions SDK is
  1.0.0-beta.1 and, in Ableton's words, "contains no API changes" — the gap is
  unchanged upstream. Requests go to the Centercode project's feedback ("Öneri Gönder").
- **Status**: RESOLVED

### GAP-004
- **Timestamp**: 2026-09-01T01:15:00+03:00
- **Category**: Arrangement
- **Description**: Setting the loop length of an arrangement clip so a short generated pattern repeats across a longer section.
- **Observed Behavior**: `MidiClip.looping` and `loopStart` are writable but `loopEnd` is read-only in the SDK, so the loop brace cannot be sized from code.
- **Required Implementation**: A writable `loopEnd` on `Clip`.
- **Current Workaround**: `tileNotes()` repeats the pattern explicitly across the section length before writing, so the clip needs no loop brace at all. Side benefit: what is written is exactly what was computed, so reading `clip.notes` back is real verification.
- **Note**: nothing is blocked by this and the workaround is the better design, so a
  writable `loopEnd` would not be adopted even if it arrived.
- **Status**: WORKED AROUND

### GAP-005
- **Timestamp**: 2026-09-01T01:45:00+03:00
- **Category**: Arrangement / Generation
- **Description**: Varying musical *density* per arrangement section — an intro should play fewer hits than a final hook, not merely quieter ones.
- **Observed Behavior**: The scene's per-lane `activity` value (0–100) now reaches the arrangement builder, but Sensei's live target accepts only `bars`, `seed` and `variation_amount`; there is no density or element-count parameter. Dropping notes to thin a pattern without musical rules would break downbeats, so it is not attempted.
- **Required Implementation**: A density/intensity input on `prepare_midi_variation()` that selects or filters corpus patterns by note count for the target role.
- **Resolved 2026-09-02** exactly as prescribed, by selection rather than subtraction:
  `generate_midi_variation(..., density=0.0..1.0)` ranks the role's candidate pool by
  notes per bar (`_notes_per_bar()` reads each entry's own `cycle_beats`) and keeps the
  band around the requested quantile before the seeded pick. An intro asks for a low
  density and gets a take that was already sparse; nothing is thinned, so no downbeat
  is ever removed. Exposed on `midi_generate` as `density`.
  - The band is at least `MIN_DENSITY_POOL` (6) entries wide, or a third of the pool.
  - A pool smaller than that is left alone and `diagnostics.density_applied` comes back
    `false` with the reason, rather than pretending the request was honoured.
  - `density` outside 0–1 is refused, like `variation_amount`.
  - Measured on a 12-entry pool: density 0.0 selects from 2–10 notes/bar, density 1.0
    from 12–40.
- **Arrangement side resolved 2026-09-03**: the section's 0–100 activity now reaches
  Sensei as `density` (`densityFor()` in `sensei-midi-writer/src/arrangement.ts`),
  alongside the plan's genre as `genre_style`, through the live target →
  `live_context_resolver` → `generate_gate` → engine. The gate's report carries the
  engine's diagnostics, so what the evidence did is observable from the CLI.
  Proven headlessly from the *built* extension runtime: density 0.2 selects a
  3.5–4.5 notes/bar band, `layer_fit_applied: true`, `ready_to_write`.
  Two traps closed on the way: the embedded runtime did not carry `mi/` and would
  have reported "no measured evidence" inside Live while the source tree said
  otherwise (build.ts now copies it and RUNTIME_VERSION is bumped), and the packer
  assumed absolute manifest paths after the manifest had been made relative.
- **Status**: RESOLVED

### GAP-006
- **Timestamp**: 2026-09-01T17:40:00+03:00
- **Category**: Device / Automation
- **Description**: Writing automation envelopes into a project.
- **Observed Behavior**: Was read-only everywhere (`als_automation_inspector` could list envelopes, nothing could create one).
- **Resolved 2026-09-01** for mixer parameters: `aimixmaster/automation_writer.py` writes an `AutomationEnvelope` whose `EnvelopeTarget/PointeeId` points at the parameter's **own existing** `AutomationTarget Id` — the target is never invented. Values are validated against that parameter's `MidiControllerRange`, times must not go backwards, an existing envelope is only replaced with `replace=true`, and the file is reloaded and compared point-by-point after saving. Proven end to end: -12 dB / 0 dB / -6 dB written at beats 0/16/32, saved, reloaded, read back identical.
- **Device parameters resolved 2026-09-02**: `list_automatable_parameters()` finds every
  element carrying both a `Manual` value and its own `AutomationTarget Id`, and
  `find_target_by_pointee()` writes to it after validating against that parameter's own
  `MidiControllerRange` — a parameter with no declared range is refused rather than
  guessed at. Reached from `automation_write` with `pointee_id`, discovered through
  `automation_list_targets`. Proven on EQ Eight `GlobalGain`, −12 dB to 0 dB.
- **Still Missing**: clip-level envelopes. Track-level mixer and device parameters both
  work; per-clip modulation does not.
- **Status**: PARTIALLY RESOLVED

### GAP-007
- **Timestamp**: 2026-09-03T20:40:00+03:00
- **Category**: Project / Track creation
- **Description**: `project_build` promised a project "from scratch" but wrote every part to a track *by name*; on an empty set none of the plan's tracks exist, so every write would be rejected. Track creation, naming, instrument loading and the song key lived only in a second control surface (`ArrangementGPSBuilder`) that watches a build directory -- a second trigger, against the one-install-one-trigger rule.
- **Observed Behavior**: Proven on Live 12.4.15b1 (2026-09-03): the MCP wrote a 4-note clip through the Loom surface, but the dry run of a tech-house build named 17 tracks the default set does not have.
- **Resolved 2026-09-03**: the Loom surface gained `create_midi_track` (exact name; adopts an existing MIDI track, refuses an audio track wearing the name or an ambiguous name; loads an `instrument_family` from the browser and reports `loaded / not_found / unavailable / failed` instead of assuming) and `set_key` (Live's own Song Key, before/after). `project_build` now runs a track phase before the writes -- dry run says `exists / would_create` per plan track against the fresh session state -- and a key step after the tempo. Both ops are also reachable standalone through `live_command`. Fakes: `FakeSong.create_midi_track`, `FakeBrowser`; 10 new checks in `test_bridge_ops.py`, 3 in `test_mcp_tools.py`.
- **Proven 2026-09-03 20:44 on Live 12.4.15b1**: from an empty default set, `project_build(dry_run=false)` created 17 tracks, loaded 6 instruments by family name, set tempo 126 and key D minor, wrote 39 arrangement clips verified note-for-note, in 56 s. The run also exposed that the chord default profile id had never existed (fixed, tested against the catalogue) and that Live defers its cue-list refresh (fixed, verified from the arrangement).
- **Still Missing**: `ArrangementGPSBuilder` remains in the tree for its instrument-family search history; the surface no longer needs it.
- **Status**: RESOLVED

### GAP-008
- **Timestamp**: 2026-09-03T21:05:00+03:00
- **Category**: Architecture / Extension as the Live-side endpoint
- **Description**: The user's target is "the only thing I do in Live is add the .ablx". Today the MCP's Live side is the Loom control surface; the SDK extension only registers context-menu commands. Measured (SDK 1.0.0-beta.1 API docs + host logs) what a bridge inside the extension can and cannot do.
- **Observed Behavior — covered by the SDK**: track list with name/mute/solo/arm, devices and parameters (`getValue/setValue/min/max`), mixer volume/pan as `DeviceParameter`, `song.tempo` (read+write), `cuePoints` and `createCuePoint(time)`, `rootNote/scaleName` (read), `createMidiTrack()` + `name`, `MidiTrack.createMidiClip(start, duration)` and `ClipSlot.createMidiClip(length)` with `clip.notes = [...]`, `insertDevice(builtInDeviceName, index)` for native devices with their *default* preset, `withinTransaction` for undo grouping, child processes allowed (`--allow-child-process`).
- **Observed Behavior — not in the SDK**: transport (no play/stop, no `isPlaying`, no `currentSongTime`), meters, song time signature, arrangement length, **song key write** (`rootNote`/`scaleName` are getters only), **preset/browser loading** (no `.adg/.adv`, no browser search — the surface loaded "Boom Bap Kit.adg" by family name; the SDK can only drop a bare "Drum Rack").
- **Filesystem**: hosted extensions may read/write only `storageDirectory` and `tempDirectory` (Node `--allow-fs-read/--allow-fs-write`; proven by the 2026-08-12 host log `ERR_ACCESS_DENIED FileSystemRead` when the extension read outside them). The bridge root must therefore live under the extension's storage directory, and the MCP must be pointed there; `~/Documents/SenseiV2Bridge` is unreachable from a hosted extension.
- **Decision (user, 2026-09-03)**: move the bridge into the extension. Ops the SDK lacks stay on the control surface as the release-Live fallback and are logged as Centercode feature requests: transport, meters, time signature, key setter, preset loading.
- **Measured 2026-09-03 21:25, Live 12.4.15b1, hosted (.ablx installed, Developer Mode off, Live owns the host)**: Live started the host at launch, the extension logged `Loom bridge listening at ~/Library/Application Support/Ableton/Extensions Data/loom.sensei-midi-writer/bridge`, state published every second. Through the MCP's own handlers, all under 0.7 s: `set_tempo` OK (before/after), `set_mixer` OK (volume 0.85→0.60, mute), `list_device_parameters` OK (17 on Drum Rack), `create_locator` OK (verified), `create_midi_track` OK with a native `Drum Rack` inserted, a preset name reported `not_loadable_in_extension`, `write_arrangement_clip` OK (4 notes, 4 read back), session clip WRITTEN_TO_LIVE; `set_key` and `transport` refused with `unsupported_in_extension`. Nothing NOT_CONSUMED. Live also logged a harmless warning for the old, moved `ai-producer.sensei-midi-writer` directory it still remembered.
- **Still on the control surface only**: transport, meters, time signature, key write, preset/browser loading (`project_build` therefore loads bare native devices on the extension path and real presets only through the surface).
- **Selection and hand-over (2026-09-03 21:40)**: with LOOM_BRIDGE_ROOT unset the MCP picks a *fresh* extension bridge over the control surface per call (`bridge_selection: fresh_extension_bridge`), and an op the extension refuses with `unsupported_in_extension` is re-sent to the surface when that one is fresh too — measured: `set_key F Minor` and `transport play/stop` answered OK via the surface with `fallback` recorded. A track the extension creates but cannot dress with a preset is handed to the surface with `load_instrument_on_adopt`; `live_state` now returns the `get_state` answer itself (`state_source: get_state_answer`) instead of racing the bridge's timer file. The hosted extension republishes state after every answered request.
- **Status**: RESOLVED for everything the SDK exposes; the five SDK gaps above stay open as Centercode feature requests
