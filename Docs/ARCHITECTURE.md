# Loom architecture

One MCP, one Live endpoint. This page is the map; the README says how to
install and test.

## Modules and owners

| Path | Owns | Does not own |
|---|---|---|
| `mcp_server/server.py` | tool dispatch, argument validation, path guard, resources/prompts, engine handlers, Live handlers, `project_build`, the JSON-RPC loop | any Live protocol detail |
| `mcp_server/tool_schemas.py` | the 44 public tool names and input schemas | behaviour |
| `mcp_server/bridge_client.py` | the ONE bridge root, the protocol gate, writing a request, reading its structured outcome, the public status vocabulary | engines, orchestration |
| `mcp_server/live_project.py` | OS-level open/quit/status of Live, verified from Live's own log; user-requested only | anything inside a set |
| `extension/src/bridge.ts` | the extension-side queue: claim, expiry, session, journal, ownership ledger, every Live operation, structured outcome | UI |
| `extension/src/extension.ts` | SDK wrappers (the only place SDK objects are touched), `startBridge`, the "Loom: Generate" context command | queue rules |
| `AIMixMaster/aimixmaster/project_analyzer.py` | **the one `.als` reader**: track name, device chain, tempo, key, time signature — each answer naming its source | anything outside a set file |
| `Sensei/ableton/kit_resolver.py` | **the one kit reader**: a Drum Rack preset (.adg) → pads, notes, roles, resolved sample files; the pad-role vocabulary and the General MIDI map | writing anything |
| `Sensei/`, `ArrangementGPS/`, `AIMixMaster/`, `Presetor/`, `AISoundDesigner/`, `MusicalIntelligence/`, `MixAnalyzer/`, `SampleAgent/` | engines: files in, files out; no Live access | Live |
| `install.py` | registering the MCP, packaging the extension, comparing installed vs. shipped versions | installing into Live (Live's own step) |

Removed on 2026-09-06 (see `Docs/Sessions/2026-09-06_stabilize.md`): the
control surface (`AbletonScripts/`), the Remote Scripts, the extension's plan
writer (`arrangement.ts`), the surface fallback.

## The official call flow

```
MCP client
  └─ tools/call ──► server.py: validate_arguments (tool_schemas)
        └─ dispatch_tool ──► handler (engine or Live)
              └─ Live handler ──► bridge_client.submit_request(payload)
                    ├─ check_cancelled            (a cancelled call starts nothing)
                    ├─ resolve_bridge_target      (exactly one root, or a refusal)
                    ├─ mutation_gate              (SDK op? capability? protocol? fresh? session?)
                    ├─ write requests/<id>.json   (atomic; expires_at, target_session, idempotency_key)
                    └─ wait for done|errors/<id>.json ──► read_outcome ──► status + outcome
Live (extension host)
  └─ bridge.ts poller: claim (rename → processing/) → refusalFor (expiry, session)
        → journal lookup (replay / conflict / other session / indeterminate)
        → journal `started` → applyOperation → SDK wrappers (extension.ts)
        → journal outcome → done|errors/<id>.json (atomic) → republish state
```

Every Live mutation, including the extension's own "Loom: Generate"
command, goes through `applyOperation` and the ownership ledger. Nothing else
writes into a set. `live_project` opens or quits Live at the OS level and
never touches a set's contents.

`project_build` is the one orchestration: plan (own run directory) →
validate → gate → tempo → tracks (with target evidence: Drum Rack pads read
from the device, instrument device verified) → clips per section → locators →
readback. Keys: `session:plan-sha:run_id:step`.

## Identity

One product name everywhere the user looks: the extension's manifest is
`name: "Loom"`, `author: "SubverseLab"`, so Live derives the id
**`subverselab.loom`** (storage and bridge under `Extensions Data/subverselab.loom/`);
the package is `extension/dist/loom.ablx`; the MCP is `loom-mcp`; the context
menu says "Loom: Generate". Engine names (Sensei, Presetor, AIMixMaster…) stay
as module names because they say who owns which computation.

The extension shipped under `loom.sensei-midi-writer` (and before that
`ai-producer.sensei-midi-writer`) until 2026-09-06. Those ids are
`LEGACY_EXTENSION_IDS` in `bridge_client`: their bridges are read for
diagnosis, never mutated; while only an old id is installed every mutation is
`LEGACY_EXTENSION`; two fresh bridges are `AMBIGUOUS_BRIDGE` until the old
extension is removed from Live. The old replay journal is carried over
explicitly with `live_command op=journal_import` (the extension appends the
foreign entries with `imported_from`; their keys are then refused as another
session's or as interrupted, never replayed or re-applied). Nothing is
deleted or forgotten by the rename.

## Four ways to change a set, and which Loom uses

| Path | Input | Real artifact | Effect on the open set | Save / reopen | Verified by |
|---|---|---|---|---|---|
| **A** extension / SDK | MCP request | none on disk (the change is in Live's memory) | immediate | the user saves | read-back inside the op (notes, pads, cue list), `outcome` |
| **B** file engines on disk | `.als` path | a new `.als` (backup first) | **none** — Live does not watch the file | reopen the set (`live_project open`, kills the extension host) | reload + compare in the writer, `alsguard`-style checks |
| **C** Live loads what B prepared | `.als` / sample files | — | replaces the open set (open) or adds a clip (import) | — | `live_project` reads Live's log; `import_audio_clip` reads the clip back |
| **D** the user's own step | preset / kit in the browser | — | whatever Live does | — | rebuild with the same plan: the adopted track's device is read from the state |

A Drum Rack preset (.adg) is not hand-converted into set XML: the preset form
(`BranchPresets`) is not the set form (`Branches`), and every attempt at
hand-building clip or device XML has crashed Live (see the safe-edit notes in
the crate bench). What Loom does instead: read the preset (B, read-only),
rebuild its pads through the SDK (A, `build_drum_kit`), state the fidelity.

## Capability inventory (2026-09-06)

Status keys: **code** = in the tree · **isolated** = proven by a headless test ·
**live-past** = proven on a real Live before 2026-09-05 (control surface era) ·
**live-now** = proven on Live 12.4 beta through the extension · **broken** =
shown not to work · **unverified**.

| Capability | Old entry | Implementation | Evidence | Loom tool today | Status | Loss / recovery |
|---|---|---|---|---|---|---|
| `.als` read: tracks, devices, mixer, routing, clips, arrangement, automation | AIMixMaster CLIs | `AIMixMaster/aimixmaster/*`, `als_*_inspector.py` | pytest (27), tools suite | `project_*`, `automation_read`, `automation_list_targets`, `drumbuss_read` | isolated | none |
| `.als` write: automation envelopes, drum buss chain, chain transplant | AIMixMaster / Presetor CLIs | `automation_writer`, `buss_builder`, `presetor/chain_builder` (clone Live-written XML, renumber ids, verify by reload) | pytest, Presetor suite (30) | `automation_write`, `drumbuss_build`, `chain_apply` (dry-run default, backup, reload-compare) | isolated; live-past (opened in Live 2026-09-01/02) | none — path B; the open set must be reopened to see it |
| `.adg` / `.alc` read: pads, notes, samples, effects, macros | Sensei inspector CLIs | `profile_exporter.build_kit_profile`, `alc_inspector` | Sensei pytest (`test_alc_inspector`, `test_kit_resolver`) | `kit_resolver` behind `build_drum_kit kit=` and `project_build kit=` | isolated | none |
| Preset loading into the open set by name | control surface `load_item` (browser search) | removed with the surface | live-past (17 tracks, 6 presets, 2026-09-03) | — | **broken on the extension path** (no SDK API) | drums: rebuilt from the preset's own samples (`build_drum_kit kit=`), fidelity stated; instruments: `device_map` native device, or path D (`needs_preset` names both) |
| Kit from samples: chains, receiving notes, Simpler, sample file | — (new 2026-09-06) | `bridge.ts opBuildDrumKit` | extension tests (chains, notes, Simpler, sample, occupied pad refused, partial kit indeterminate), MCP fake, kit manifest artifact (Boom Bap Kit 16/16 files, Swang Bap Kit 16/16) | `live_command op=build_drum_kit`, `project_build kit=` | isolated; live-now only for the empty Drum Rack insert | live acceptance pending: sample audible, saved set reopens with the files |
| Drum notes onto a kit's pads | surface: pads read from Live | GM-pitched corpus + `kit_pad_mapping` by pad role | MCP fake (16-pad kit at 77–92 written by role) | `project_build` | isolated | before today a non-GM kit got a **0-note clip that read as OK**; now mapped by role or blocked `no_notes_for_pads` |
| MIDI generation (drum / bass / chord) from the locked dataset | Sensei CLI | `core/midi_runtime`, `midi_variation_engine` | Sensei pytest, tools suite | `midi_generate`, `project_build` | isolated; live-now (14 chord + 12 bass clips, 2026-09-06) | evidence gaps are refused per genre (Hip Hop: no bass corpus; Trap: no chord corpus) — reported, not filled |
| Arrangement clips, session clips, tempo, locators, mixer, device parameters, tracks | surface + extension | `bridge.ts` ops | extension tests (122), real-consumer test (22), MCP fake (97) | `midi_write_*`, `live_command`, `project_build` | live-now (tempo, 7 locators, 5 tracks, 26 clips, 2026-09-06) | none |
| Song key, transport, meters, time signature, recording (resample) | surface | — | live-past | `UNSUPPORTED_BY_SDK` | broken on the extension path (SDK gap, filed 2026-09-03) | key: clips are generated in the key; signature: `beats_per_bar` explicit / `.als`; recording: `mix_capture tap` |
| Audio import into the open set, pre-fx render, playback capture, Mix Check | Launchpad services | `SampleAgent`, `MixAnalyzer`, `livetap` | pytest, live-now (render 2026-09-03, tap 2026-09-06) | `crate_*`, `mix_*` | live-now | none |
| Project plan from a prompt | ArrangementGPS CLI | Node chain, per-run directory | tools suite, MCP fake | `plan_create`, `project_build` | isolated | plan names presets; the build states per track what stands in (`simplification`, `needs_preset`) |
| Project packaging (`Builds/<name>/`) | ArrangementGPS `createProjectPackage.js` | JSON manifests + action list, no `.als` | tools suite | `plan_create` (build_dir) | isolated | it never produced a `.als`; the set is built live by `project_build` |

## The reader contract: one owner per fact

The engines read the same facts out of the same files. Until 2026-09-06 five
of those facts had several readers, and two of them disagreed silently. Each
is now owned by one module, and every answer carries where the value came
from — the pattern `beats_per_bar_source` / `data_source` / `target_evidence`
already used, applied to the rest.

| Fact | Owner | Resolution order | Reported as |
|---|---|---|---|
| Track name | `project_analyzer.resolve_track_name` | `Name/UserName` → `Name/EffectiveName` → missing | `source`, plus the field not chosen in `alternatives` |
| Device chain | `project_analyzer.resolve_device_chain` | `top_level` or `rack_expanded` — different facts, never interchanged | `source`, with the top-level chain and `uses_rack` alongside |
| Tempo / key / signature | `project_analyzer.musical_context` | master track → any tempo node; the set's own `ScaleInformation`; its own `TimeSignature` | per field: `source`, `confidence` (`read` / `missing`), never a silent default |
| Pad notes | `kit_resolver.resolve_pad_notes` | `live_drum_rack` → `preset_xml` → `assumed_general_midi` | `source`, `writable` (false for the assumption), plus the role `mapping` |
| Sample reference | `kit_resolver` | `absolute` → `pack_relative` → `missing` | `reference_state`, `found_in`, and a count per state on the kit |

Two consequences worth keeping in view:

- **The General MIDI map is a source with a name, not a default.** Sensei's
  drum corpus is GM-pitched, so a kit whose pads sit elsewhere needs
  `pad_mapping` to reach them by role; a role the kit has no pad for is
  dropped and named. Without this a 16-pad hip-hop rack at notes 77–92
  received an empty clip that still reported OK.
- **A widened track name changes what a scan sees.** `UserName` is empty in
  most projects, so a UserName-only reader skipped unnamed tracks entirely.
  Every reader now falls through to `EffectiveName`, and `find_unique_track`
  fails closed when two tracks resolve to the same name.

Deliberately not merged: `als_mixer_compact.track_name`, which strips a
leading `# ` for mixer display. That is a presentation of the name, not a
second reading of it. `tests/test_reader_contract.py` pins all of the above,
including that every reader in the tree is the same callable.

## Protocol and states

Versions are three different things: record schema `sensei.bridge.v2`, queue
protocol `loom.bridge/3` (what the gate compares), package version
`loom-extension/<manifest.json version>`; the SDK API level (1.0.0) is a fourth.

**MCP statuses** (`bridge_client`): `OK`, `REFUSED_IN_LIVE`, `FAILED_IN_LIVE`,
`INDETERMINATE`, `NOT_CONSUMED`, `INVALID_RESULT`, `QUEUED`,
`UNSUPPORTED_BY_SDK`, `NO_EXTENSION_BRIDGE`, `AMBIGUOUS_BRIDGE`, `NO_STATE`,
`STALE_STATE`, `STALE_SESSION`, `UPGRADE_REQUIRED`, `PROTOCOL_MISMATCH`.
`LEGACY_EXTENSION`. Read ops (`get_state`, `list_device_parameters`, `drum_pads`) pass the gate
on any bridge; mutations need a fresh state, a session id and a supported
protocol.

**Structured outcome** on every answer: `kind` applied / refused / failed /
indeterminate, `code`, `applied` (true / false / null), `verified`,
`side_effects`, `next_step`. Refused = Live untouched. Failed = attempted and
restored. Indeterminate = may have happened; codes `no_outcome_in_time`,
`indeterminate_earlier_attempt`, `indeterminate_after_restart`,
`restore_failed`, `rollback_failed`, `unexpected_error`. A verified write whose
ownership could not be recorded is `applied` with code `ledger_write_failed`
and a `warning`, never an error. Nothing retries an indeterminate answer.

**Journal** (`state/journal.jsonl` + `journal.marker`): one line per attempt,
`started` before the mutation, then the outcome with its result body. Last
line per key wins. Scope of the replay guard: one key = one operation within
one Live session; the same key with other content is `idempotency_conflict`,
in another session `replay_refused_other_session`; a `started` or
`indeterminate` entry is answered `indeterminate_earlier_attempt` from any
session. Retention: current-session entries never dropped; finished entries of
earlier sessions kept 30 days, at most 5000; unknown-outcome entries never
dropped by age; compaction when the file passes 2000 lines. Journal states:
`fresh` (never used), `present`, `missing` (marker exists, file gone),
`unreadable` (a broken non-final line); the last two refuse keyed mutations
and say what to do, and never suggest deleting anything. A torn final line is
an interrupted append and is skipped and repaired.

**Ownership** (`state/owned_clips.json`): a clip is Loom's only with the same
Live session, the same SDK `Handle.id` and unchanged content. `replace_owned`
replaces in place (same object); anything needing a delete is refused.

## Directories

| What | Where |
|---|---|
| bridge root | `~/Library/Application Support/Ableton/Extensions Data/subverselab.loom/bridge/` (`requests/ processing/ done/ errors/ state/ journal.marker`) |
| plans and builds | `ArrangementGPS/engine/runs/<run_id>/` (one directory per `plan_create`), mirror of the newest plan at `engine/output/` for the resource only |
| MCP overflow responses | `mcp_server/responses/` |
| captures, sample packs | `Sessions/MixCaptures/`, `Sessions/SamplePacks/` |
| measured evidence (never committed) | `Presetor/data/measured_*`, `AISoundDesigner/data/measured_*`, `Sensei/data/` |
| extension package | `extension/dist/loom.ablx` |

## Test map

| Claim | Suite |
|---|---|
| queue rules, ownership, journal retention/loss/torn tail/import, outcome kinds, every op incl. build_drum_kit | `extension/tests/bridge.test.ts` (fake Live, real bridge.ts) |
| .adg → pads/samples/roles, relative-path resolution, fidelity report | `Sensei/tests/test_kit_resolver.py` |
| MCP ↔ real extension queue code: OK, INDETERMINATE (held, crashed host, restart), STALE_SESSION, stdio | `mcp_server/tests/test_bridge_consumer_real.py` (needs node + `npm install`) |
| MCP side fast: refusals, gate (old / mismatched / stale / session change), cancel, build order and status | `mcp_server/tests/test_extension_path.py` (Python fake) |
| 45 tools over stdio, protocol conformance | `test_mcp_tools.py`, `test_mcp_protocol.py` |
| Live open/quit verification | `test_live_project.py` |
| 4/4, 3/4, 6/8 time contract | `Sensei/tests/test_time_contract.py` |

Not proven anywhere headless: Live itself. The acceptance run is
`extension/tools/measure_bridge.py` on the beta with the new `.ablx`.
