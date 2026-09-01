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
- **Status**: OPEN / IN_PROGRESS / RESOLVED

---

## Log Entries

### GAP-001
- **Timestamp**: 2026-09-01T00:17:00+03:00
- **Category**: Telemetry / State Inspection
- **Description**: Need real-time read access to selected track name, active devices, and current playhead position without writing a file request first.
- **Observed Behavior**: Current `SenseiRemote` is write-only for clip creation via `~/Documents/SenseiV2Bridge/requests/`.
- **Required Implementation**: Add periodic state dumping or bidirectional socket/OSC endpoint in `SenseiRemote` or dedicated bridge.
- **Partially Resolved 2026-09-01**: `SenseiRemote` moves a consumed request to `done/` or `errors/` under the same filename, so the *write* path is now verifiable without any new endpoint. `sensei_write_clip_to_live` polls for that move and reports `WRITTEN_TO_LIVE` / `REJECTED_BY_LIVE` / `NOT_CONSUMED` instead of claiming success. Live *state* (selected track, playhead, device list) is still unreadable — that part remains open.
- **Status**: OPEN (write path resolved, read path still missing)

### GAP-002
- **Timestamp**: 2026-09-01T00:17:18.474543+03:00
- **Category**: Transport
- **Description**: Real-time song play/stop status trigger
- **Observed Behavior**: No current transport trigger in SenseiRemote
- **Required Implementation**: Add OSC or socket listener to trigger playback directly
- **Status**: OPEN

### GAP-003
- **Timestamp**: 2026-09-01T01:15:00+03:00
- **Category**: Arrangement
- **Description**: Bar-to-beat conversion when building the Arrangement View from an ArrangementGPS plan (sections are expressed in bars, `Song.createCuePoint` and `MidiTrack.createMidiClip` both take beats).
- **Observed Behavior**: The Extensions SDK exposes no song time signature. `Song` has `tempo`, `rootNote`, `scaleName`, `gridQuantization` and `gridIsTriplet`, but the only time-signature accessor in the whole API surface is on `Scene`. The Python LOM's `song.signature_numerator`/`signature_denominator` are not mirrored in the SDK.
- **Required Implementation**: A song-level time signature accessor on `Song` in the Extensions SDK, or read it from the Remote Script side and pass it through `arrangementgps_last_build.json`.
- **Current Workaround**: `BEATS_PER_BAR = 4` in `sensei-midi-writer/src/extension.ts`, applied through a single `barToBeat()` helper so there is exactly one place to change. Any project not in 4/4 will place locators and clips at the wrong times.
- **Status**: OPEN

### GAP-004
- **Timestamp**: 2026-09-01T01:15:00+03:00
- **Category**: Arrangement
- **Description**: Setting the loop length of an arrangement clip so a short generated pattern repeats across a longer section.
- **Observed Behavior**: `MidiClip.looping` and `loopStart` are writable but `loopEnd` is read-only in the SDK, so the loop brace cannot be sized from code.
- **Required Implementation**: A writable `loopEnd` on `Clip`.
- **Current Workaround**: `tileNotes()` repeats the pattern explicitly across the section length before writing, so the clip needs no loop brace at all. Side benefit: what is written is exactly what was computed, so reading `clip.notes` back is real verification.
- **Status**: OPEN

### GAP-005
- **Timestamp**: 2026-09-01T01:45:00+03:00
- **Category**: Arrangement / Generation
- **Description**: Varying musical *density* per arrangement section — an intro should play fewer hits than a final hook, not merely quieter ones.
- **Observed Behavior**: The scene's per-lane `activity` value (0–100) now reaches the arrangement builder, but Sensei's live target accepts only `bars`, `seed` and `variation_amount`; there is no density or element-count parameter. Dropping notes to thin a pattern without musical rules would break downbeats, so it is not attempted.
- **Required Implementation**: A density/intensity input on `prepare_midi_variation()` that selects or filters corpus patterns by note count for the target role.
- **Current Workaround**: `velocityScaleFor()` in `sensei-midi-writer/src/arrangement.ts` maps activity to a 0.6–1.0 velocity multiplier, so intensity reads as dynamics. Presence is still decided upstream by `mute_regions`.
- **Status**: OPEN

### GAP-006
- **Timestamp**: 2026-09-01T17:40:00+03:00
- **Category**: Device / Automation
- **Description**: Writing automation envelopes into a project.
- **Observed Behavior**: Was read-only everywhere (`als_automation_inspector` could list envelopes, nothing could create one).
- **Resolved 2026-09-01** for mixer parameters: `aimixmaster/automation_writer.py` writes an `AutomationEnvelope` whose `EnvelopeTarget/PointeeId` points at the parameter's **own existing** `AutomationTarget Id` — the target is never invented. Values are validated against that parameter's `MidiControllerRange`, times must not go backwards, an existing envelope is only replaced with `replace=true`, and the file is reloaded and compared point-by-point after saving. Proven end to end: -12 dB / 0 dB / -6 dB written at beats 0/16/32, saved, reloaded, read back identical.
- **Still Missing**: device parameters (each device needs its own range validation) and clip-level envelopes. Only `volume` and `pan` are exposed.
- **Status**: PARTIALLY RESOLVED
