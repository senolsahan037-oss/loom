# 2026-09-06 — Stabilize: three gaps closed, one path, dead code out

Follows `2026-09-05_single_extension_path.md`. Tree still **uncommitted**, no
push, no install run, installed extension untouched. Nothing verified on a
real Live; every claim below is headless.

## The three gaps

1. **Replay journal forgot** (bridge.ts): one JSON document of the last 500
   records. Now `state/journal.jsonl` (append, `started` before the mutation,
   outcome with its result body after) plus `journal.marker` at the bridge
   root. Scope: key × content × Live session; unknown-outcome entries win over
   every other verdict and are never dropped; current-session entries never
   dropped; earlier sessions 30 days / max 5000; compaction past 2000 lines.
   `missing` (marker without file) and `unreadable` refuse keyed mutations and
   say what to do without suggesting a delete; a torn final line is skipped and
   repaired; read ops are never journaled; a claim file whose outcome the
   journal holds is answered from the journal at restart; the v2 document is
   migrated once.
2. **INDETERMINATE lost** its meaning in error strings. Every record now
   carries `outcome {kind, code, applied, verified, side_effects, next_step}`
   and `status: ok | error | indeterminate`; `bridge_client.read_outcome` maps
   by structure (`REFUSED_IN_LIVE` / `FAILED_IN_LIVE` / `INDETERMINATE`),
   `project_build` lists `indeterminate` steps with side effects and aborts
   the rest; nothing retries. Proven with the real `bridge.ts` as consumer
   (held request, host killed after the mutation, restart, retry, build,
   stdio): `mcp_server/tests/test_bridge_consumer_real.py`.
3. **Old extension trusted**: the state now publishes `bridge_protocol`
   (`loom.bridge/3`), `protocol_features`, `extension_version`,
   `sdk_api_version`, `session_id`, `journal`. `bridge_client.mutation_gate`
   refuses mutations without a fresh state, a session id and a supported
   protocol (`NO_STATE` / `STALE_STATE` / `UPGRADE_REQUIRED` /
   `PROTOCOL_MISMATCH`), re-reads the session right before writing
   (`STALE_SESSION`); reads and diagnosis still work. One version source:
   `manifest.json` (build asserts `package.json` equal, injects it into the
   bundle); `install.py --check` compares installed vs. shipped.

## Removed (evidence → replacement → check)

| Removed | Why dead | Replacement | Verified by |
|---|---|---|---|
| `AbletonScripts/Loom/` (control surface, bridge_ops, its test) | product decision: no Control Surface; MCP never discovered it (only `LOOM_EXTENSION_IDS` roots); only entry was `install.py --legacy-surface` | extension bridge | grep for `AbletonScripts`, `bridge_ops`, `legacy-surface` clean; check scripts updated |
| `AbletonScripts/ArrangementGPSBuilder/` | read action lists from `~/Desktop/ArrangementGPS/Builds` and `Desktop/Loom/ArrangementGPS/Builds` (builds now live in `engine/runs/<id>/`), wrote to Extensions Data `ai-producer.sensei-midi-writer` (the extension id is `loom.sensei-midi-writer`) — could never reach the extension | `project_build` | `test_extension_path.py` §9, real consumer §3 |
| `AbletonScripts/MidiImportTest/` | read `~/Desktop/ArrangementGPS/engine/output/agent_outputs/…` (gone) | `midi_write_to_live` | tools suite |
| `sensei-midi-writer/src/arrangement.ts` + test, the two plan commands and the manual JSON command in `extension.ts` | depended on the removed Remote Script's file; wrote through `clearClipsInRange` (deleted user clips) with a hard-coded 4/4 | `project_build` → `write_arrangement_clip` (ledger, beats_per_bar); "Sensei: Generate" now calls `applyOperation` | `bridge.test.ts`, `tsc` |
| `TrackLike.clearClipsInRange` | no caller after the above | — | `tsc` |
| `ArrangementGPS/engine/agents/sensei_drum/` (runSensei.js, midiToNotes.py) | not imported by any chain step; called `Sensei/cli/export_drum_clip.py`, which does not exist; hard-coded `Desktop/Loom` | `midi_generate` | grep `runSensei`, `export_drum_clip` clean; `plan_create` still runs 5 steps |
| `mcp_server/tests/test_live_bridge.py` | tested the removed surface | `test_bridge_consumer_real.py` | check scripts |
| `install.py --legacy-surface`, `install_remote_scripts`, `control_surface_status` | removed path | `extension_status` with protocol verdict | import + `source_versions()` |
| `server.py`: `_is_playing_now`, `follow`/`waited` in `mix_capture`, legacy Remote Scripts listing in `live_bridge_status`, `_target` in tool args | no caller / vestigial / internal context in user args | `write_arrangement_clip(args, target=, idempotency_key=)` | tools suite |
| `mcp_server/README.md`, `Docs/PROJECT_STRUCTURE.md` | contradicted current behaviour (24 tools, venv, "read path missing", Agents/Producer folders) | `README.md`, `Docs/ARCHITECTURE.md` | — |
| `Sensei/.agents/AGENTS.md.bak`, `MixAnalyzer/livetap/livetap` (stale pre-bundle binary, untracked) | backup copy / build output beside source | — | `.gitignore` |

Also fixed on the way: `ArrangementGPS/engine/builder/createSessionPlan.js`
resolved Sensei's catalogue from `~/Desktop/Loom` (any other checkout location
lost the identity catalogue); now relative to the file, `LOOM_SENSEI_IDENTITY_PATH`
overrides. `RUNTIME_VERSION` bumped to `phase6-v13` (embedded Sensei core
changed on 09-05). `tools/measure_bridge.py` uses the MCP's resolver instead
of its own.

Left in place, flagged: `AIMixMaster/remote_scripts/MixConsoleLive2` (a Remote
Script used only by AIMixMaster's own CLI live-meter workflow, not installed,
not reachable from the MCP; hard-coded `~/Desktop/Loom` report path);
`Sensei/testdisk.log` (a tracked TestDisk log, not project material — user's
call); `Sensei/exports/*.mid` (user outputs).

Refactor: `mcp_server/bridge_client.py` (target, gate, submit, outcome,
statuses) and `mcp_server/tool_schemas.py` (the 44 schemas) out of
`server.py` (3700 → 2740 lines); no new framework.

## Results (this machine, 2026-09-06)

| Suite | Result |
|---|---|
| `scripts/check_ci.sh` | 11 passed, 0 failed, 0 skipped |
| extension `tsc --noEmit` + `tests/bridge.test.ts` | clean, 111 checks |
| `mcp_server/tests/test_bridge_consumer_real.py` (real bridge.ts) | 22 checks |
| `mcp_server/tests/test_extension_path.py` | 82 checks |
| `mcp_server/tests/test_mcp_tools.py` (44 tools, stdio) | 109 checks |
| `mcp_server/tests/test_mcp_protocol.py` | 42 checks |
| Sensei time contract + engine + genre synthesis (pytest) | 20 passed |
| `npm run package` | `dist/sensei-midi-writer.ablx`, manifest 0.3.0, bundle carries `0.3.0` and `loom.bridge/3` |
| `scripts/check_all.sh` | every stage green (extension 111, MCP path 82, real consumer 22, tools 109, protocol 42, Sensei 30, AIMixMaster 27, Mix Check, crate) except the last: `Sensei/DatasetRoot` shared-dataset lock — `sensei.genre_neighbor_graph` was regenerated on 09-03 (34,481 bytes) but `DatasetRoot/releases/phase6/shared_release.manifest.json` still locks 34,913 bytes from 09-02. Pre-existing, no file under `Sensei/data` or `DatasetRoot` was touched this session; the user's data, so not relocked here (`scripts/relock_dataset.py`) |

## Later the same night: mix_capture tap works

The user asked for the permission prompt again. Found and fixed two things:
the tap must be launched through LaunchServices so macOS charges the
System Audio Recording permission to `LiveTap.app` itself (as an MCP child it
was charged to the host app and captured silence); and `livetap` exited with
the `AVAudioFile` still open, leaving a 0-byte data chunk in every WAV (2 MB
files that read as empty). Measured from the playing Live: peak −11.3 dBFS,
−23.2 LUFS over 6 s. `handle_mix_capture` and `MixAnalyzer/livetap/main.swift`
changed; the binary rebuilds itself on next use.

## Real Live acceptance, first pass (02:38–02:45, Live 12.4 beta, extension 0.3.0)

The user installed the 0.3.0 `.ablx`; `live_bridge_status` answered
`mutations_allowed: true`, `loom.bridge/3`, journal `fresh`. Asked for "a
basic hip hop project" on the empty default set:

- Plan from `plan_create` (17 tracks, preset names) trimmed by hand to five
  tracks with native device names, because the SDK cannot load presets.
- `project_build` #1 (genre Hip Hop): tempo 90 OK, 7 locators created and
  verified from readback, 5 tracks created with Drum Rack / Operator /
  Electric / Operator / Wavetable inserted, 14 chord clips (Keys, Pad) OK
  note-for-note; `partial` because Kit's empty Drum Rack has no pads
  (`drum_rack_not_verified`, correct) and the bass corpus has no "Hip Hop"
  candidates (`no_native_role_and_genre_candidate`; Trap and House have).
- `project_build` #2 (bass tracks only, genre Trap): both tracks adopted with
  `devices_present: ['Operator']`, 12 bass clips OK, `completed`.
- No INDETERMINATE, no NOT_CONSUMED, no refusal on a user clip (set was empty).
- Left for the user: drop a kit into the Drum Rack on "Kit", then rebuild the
  drum-only plan (pads are read from the device).

Still to run from the 09-05 list: a user clip in the target range, a host kill
mid-request, retry with the same key -- on the real Live.

## Not done — real Live acceptance (remaining)

The installed extension is `loom-extension/0.1.0` (no protocol); the MCP now
refuses to mutate through it (`UPGRADE_REQUIRED`). Resume:

1. Add `dist/sensei-midi-writer.ablx` (0.3.0) in Live 12.4 beta, restart Live.
2. `live_bridge_status` → `mutations_allowed: true`, protocol `loom.bridge/3`,
   `journal.state: fresh`.
3. `tools/measure_bridge.py` on an empty set; then the §5–6 list of the
   09-05 log (user clip untouched, host kill → INDETERMINATE, retry same key).
4. Only then: commit (noreply email, leak audit first) and push.


## Third pass, same day: one identity, the old capabilities back on the map

**Identity.** The extension moved from `Sensei/extensions/sensei-midi-writer`
to `extension/`; manifest `name: Loom`, `author: SubverseLab` → Live id
`subverselab.loom` (Live derives the id from exactly those two fields; the
old id was `loom.sensei-midi-writer`). Package `extension/dist/loom.ablx`
(0.4.0, protocol still `loom.bridge/3`), npm name `loom-extension`, context
menu "Loom: Generate". `bridge_client` mutates only `subverselab.loom`;
`LEGACY_EXTENSION_IDS` are read for diagnosis, refused with `LEGACY_EXTENSION`
when alone, `AMBIGUOUS_BRIDGE` when both are fresh, and their replay journal is
carried over with the new bridge op `journal_import` (foreign entries appended
with `imported_from`; interrupted keys stay INDETERMINATE, finished keys are
refused as another session's). `install.py` names the superseded package and
the three user steps. Nothing was deleted from the old bridge.

**Capabilities.** Inventory table in `Docs/ARCHITECTURE.md` (status keys:
code / isolated / live-past / live-now / broken / unverified). Recovered:
- *The user's own kit* — `Sensei/ableton/kit_resolver.py` reads a `.adg`
  (pads, receiving notes, labels, sample files via the pack-relative path,
  roles), `build_drum_kit kit=` and `project_build kit=` rebuild it through the
  SDK as chain + Simpler + sample, and the answer states what is dropped
  (per-pad effects, macros, choke groups, Simpler parameters, return chains).
  Resolved on this machine: Boom Bap Kit 16/16 WAV files, Swang Bap Kit 16/16
  AIFF files (artifact `kit_manifest_artifact.json` in the scratch dir).
- *Drum notes on any kit* — the corpus is GM-pitched; a kit at pads 77–92
  used to receive a 0-note clip that read as OK (this is what the 2026-09-03
  "39 clips" run most likely wrote for drums). `kit_pad_mapping` maps by pad
  role; no notes left → `no_notes_for_pads`, blocked.
- *Explicit simplification* — `project_build tracks=` and `device_map=`,
  reported in `simplification`; a preset-named instrument the SDK cannot load
  is reported `needs_preset` with the two ways out (device_map, or load it in
  Live and rebuild — the adopted track's device is read from the state).
- *File engines stay* — `.als` readers/writers, `.adg/.alc` readers unchanged
  and on the MCP; path B/C/D documented in ARCHITECTURE.

Not rebuilt: a `.adg` hand-converted into set XML (preset form ≠ set form,
every hand-built clip/device XML has crashed Live); song key, transport,
meters, resample recording (SDK gaps, filed).

**Results:** `check_ci.sh` 11/11 (extension 122, real consumer 22, MCP fake 97,
kit resolver 4 via the Sensei subset, Mix Check, crate, protocol 42);
tools 109; `loom.ablx` built with manifest 0.4.0 / `subverselab.loom`.

**Live acceptance remaining:** add `loom.ablx`, remove the old extension from
Live's Extensions, restart Live, `live_bridge_status` → `subverselab.loom`
fresh, `journal_import`, then `project_build(prompt="90 bpm D minor hip hop",
kit="Boom Bap Kit", dry_run=false)` on an empty set: kit pads audible, drum
clips on the kit's pads, save + reopen keeps the sample files.
