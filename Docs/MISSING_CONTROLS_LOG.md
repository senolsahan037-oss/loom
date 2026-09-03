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
- **Note**: arrangements in 4/4 are built correctly today; this blocks only non-4/4
  projects. The escape hatch is the Python side, where `song.signature_numerator`
  does exist and could be passed through the bridge.
- **Status**: WORKED AROUND

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
- **Still Missing**: nothing on the generation side. The arrangement builder still has
  to decide *which* density a section deserves; `velocityScaleFor()` in
  `sensei-midi-writer/src/arrangement.ts` maps the scene's activity to a 0.6–1.0
  velocity multiplier, and that activity value is the natural input to feed here.
- **Status**: PARTIALLY RESOLVED

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
