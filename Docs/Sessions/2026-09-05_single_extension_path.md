# 2026-09-05 — Single extension path, reliability hardening

Product direction (user's call): Live 12.4 beta, **one** Live connection — the
Loom extension — and every supported capability offered through the one Loom
MCP. No control-surface requirement, no automatic fallback, no dual-bridge
management. Python/Node engines stay outside the extension; all Live traffic
goes through it.

Nothing here was verified against a real Live. Every claim below is backed
by a headless suite (fake Live objects for the extension, a fake extension
consumer for the MCP). The real-Live acceptance run on the beta is the
remaining step and is listed at the end.

## 1. Tool map: tool → engine → Live access → extension support → V1 status

Derived from `dispatch_tool` and each handler's actual call path, not from
descriptions.

| Tool | Engine | Live access path | Extension support | V1 |
|---|---|---|---|---|
| `live_state` | — | `get_state` request on the extension bridge, else its state file | yes (transport, signature = null) | supported |
| `live_bridge_status` | — | reads the resolved bridge root | yes | supported (the single diagnosis) |
| `live_command` set_tempo / set_mixer / set_device_parameter / list_device_parameters / create_locator / create_midi_track / import_audio_clip / render_pre_fx / drum_pads | — | one request each | yes | supported |
| `live_command` transport / set_key / capture_* | — | **none** — refused before a request exists | no API in the SDK | unsupported by SDK |
| `midi_write_arrangement` | — | `write_arrangement_clip` | yes, with ownership ledger + note-for-note verification | supported |
| `midi_write_to_live` | — | `write_clip` on the common protocol (was a separate legacy queue) | yes | supported |
| `midi_generate` | Sensei `prepare_midi_variation` | none (offline); `auto_write_to_live` → `write_clip` only when `writable_to_live` | — | supported; drum parts need `pad_notes` from Live to be writable |
| `project_build` | ArrangementGPS chain + Sensei + writers | tempo → tracks (+`drum_pads`, device evidence) → clips → locators → `get_state` readback | yes; song key reported UNSUPPORTED_BY_SDK | supported |
| `plan_create` | ArrangementGPS (Node) | none | — | supported (per-run directory) |
| `crate_to_live` | SampleAgent | `import_audio_clip` | yes | supported |
| `mix_from_live` | Mix Check | `render_pre_fx` | yes | supported |
| `mix_capture` method=tap | Mix Check + `livetap` | Core Audio process tap, no bridge | n/a | **experimental** (macOS permission target unverified) |
| `mix_capture` method=resample / follow_transport | — | none — refused | needs record mode / transport | unsupported by SDK |
| `live_project` | `live_project.py` (macOS `open`, Live's log) | OS level, no bridge | — | supported |
| `part_suggest`, `genre_evidence` | MusicalIntelligence | none (`part_suggest` may read `live_state` for key/tempo) | — | supported |
| `project_*`, `automation_*`, `drumbuss_*`, `chain_*`, `render_*`, `palette_read`, `library_search`, `plan_verify`, `projects_arrangement_shapes`, `setup_scan`, `gap_record`, `mix_measure/analyze/profiles`, `crate_fetch/read/spots/chop/agent` | AIMixMaster / Presetor / AISoundDesigner / Mix Check / SampleAgent | none (files only) | — | supported |

Installed SDK: `@ableton-extensions/sdk` 1.0.0-beta.1 (vendored tarball), CLI
1.0.0-beta.1, extension manifest `minimumApiVersion` 1.0.0. Its `CAPABILITIES`
now also publish `tempo`, `drum_pads` and `recording:false`, plus a
`session_id` per activation.

## 2. Findings verified / refuted

- **Clip protection** — confirmed. The surface deleted same-name overlapping
  clips (name = ownership), the extension's `clearClipsInRange` deleted every
  clip in range, session writes overwrote the highlighted slot, validation
  ran after deletion. Fixed in both bridges: validate first, ownership ledger
  (`state/owned_clips.json`), `on_conflict=replace_owned` required for Loom's
  own clips, foreign clips never touched, manual rollback on a failed fill.
- **Queue** — confirmed. Fixed: atomic request/outcome files, `expires_at` +
  `target_session` on every request, result validation (id/status), timeout →
  withdraw → `NOT_CONSUMED`, consumed-but-unanswered → `INDETERMINATE`,
  cancelled/timed-out calls cannot start a mutation, idempotency replay.
- **Fallback / dual bridge** — confirmed and removed: `_select_bridge_root`,
  `_bind_bridge_root`, `DEFAULT_SURFACE_ROOT` and every surface hand-over are
  gone; `resolve_bridge_target()` names exactly one Loom extension root or
  refuses (`NO_EXTENSION_BRIDGE`, `AMBIGUOUS_BRIDGE`).
- **Shared `engine/output`** — confirmed. Every ArrangementGPS stage now takes
  `ARRANGEMENTGPS_OUTPUT_DIR` / `ARRANGEMENTGPS_BUILDS_DIR`; `plan_create`
  gives each run `engine/runs/<run_id>/`; the package stage records its
  location in `package_location.json`; `project_build` reads its own run's plan.
- **Hardcoded `Desktop/Loom` paths** — **refuted for the repo**: `grep` finds
  none in Loom's code; `LOOM_DIR` is derived from `__file__`. The only
  `Desktop/Loom` literals live in the Launchpad's redirect shims (outside the
  repo, overridable with `LOOM_DIR`). Turkish project names: the package
  stage's `[^a-z0-9]` safe-name collapsed a fully non-ASCII name to `""`
  (build dir became `Builds/` itself) — fixed with `\p{L}\p{N}` and a fallback.
- **Fixed pad map** — confirmed. `midi_generate` no longer presents
  `[36,38,42,46]` as verified; it is an offline suggestion
  (`target_evidence: assumed_general_midi_pads`, `writable_to_live: false`).
  Live evidence comes from the new `drum_pads` op (SDK `DrumChain.receivingNote`).
- **Build order / time** — confirmed. Order is now settings → tracks → clips →
  locators → readback; `bars × 4.0` replaced by `bars × beats_per_bar` through
  `prepare_midi_variation`, the engine and `compose.render`.
- **`open_project`** — confirmed (count-based crash compare, no log offset).
  Fixed with a log marker (inode + size, rotation-aware) and crash identity.
- **Tests in `finally`, typecheck job that only echoes** — confirmed, fixed.

## 3. Test results (2026-09-05, this machine)

| Suite | Result |
|---|---|
| `Sensei/extensions/sensei-midi-writer`: `tsc --noEmit` + `npm run test:bridge` | typecheck clean, 64 checks passed |
| `mcp_server/tests/test_extension_path.py` (fake extension, no surface) | 47 checks passed |
| `mcp_server/tests/test_live_project.py` | 6 checks passed |
| `AbletonScripts/Loom/test_bridge_ops.py` (legacy surface, shared clip-policy cases) | 75 checks passed |
| `mcp_server/tests/test_live_bridge.py` (legacy consumer) | 23 checks passed |
| `Sensei` time contract + engine suites (pytest) | 20 passed |
| `scripts/check_ci.sh` | 11 passed, 0 failed, 0 skipped |
| `mcp_server/tests/test_mcp_tools.py` (44 tools, stdio) | 108 checks passed |

## 4. Remaining SDK limits (not emulated, answered `UNSUPPORTED_BY_SDK`)

transport (play/stop/position), meters, time signature, song key write,
browser preset loading (only native devices with default preset), recording
(record mode / resampling routing / arming). Centercode request filed
2026-09-03; check the answer there.

## 5. Not verified on a real Live (acceptance step)

1. Rebuild and reinstall the extension (`npm run package`, add the `.ablx` in
   Live 12.4 beta, restart Live). The running extension is still 0.1.0 — it
   does not know `drum_pads`, `session_id`, expiry or replay yet.
2. `live_bridge_status` → one fresh `loom_extension_fresh` root.
3. `project_build(prompt, dry_run=false)` on an empty set: expect tempo,
   tracks with `target_evidence`, clips with `verified_notes_match`, locators
   verified from readback, status `partial` (chord/bass presets are not
   loadable through the SDK unless the family is a native device name).
4. A user clip in the target range: the write must be refused, the clip
   untouched.
5. Kill the extension host mid-request: `INDETERMINATE`, no double clip on a
   retry with the same key.

## 6. Second pass, same day: the four gaps of the first pass

1. **Timeout/withdraw race** — fixed. The consumer claims a request by an
   atomic rename into `processing/` before reading it; the MCP's unlink can
   only win against an unclaimed file. `NOT_CONSUMED` therefore means "never
   claimed", and a claimed request without an outcome is `INDETERMINATE`.
   Startup recovery answers leftovers in `processing/` as
   `indeterminate_after_restart`. The fake extension mirrors this lifecycle
   (`hold_before_apply`, `crash_after_apply`, `recover()`).
2. **replace_owned data loss** — fixed by not deleting: the owned clip is
   replaced *in place* (same object, new notes and name) with the previous
   notes and name restored and verified on any failure; a replace that would
   need a delete (different range or length, several clips) is refused before
   anything changes, because the SDK exposes no automation, colour or launch
   settings to restore. `restore_failed` / `rollback_failed` /
   `ledger_write_failed` are distinct, explicit answers.
3. **Ownership by name and range** — fixed: a ledger entry now carries the Live
   session, the SDK `Handle.id` and a content fingerprint; ownership needs all
   three. No cross-session ownership (the SDK has no persistent clip identity);
   ledger write failures throw `LedgerError` and are checked (`probe()`) before
   a mutation.
4. **Replay after a crash** — fixed with a write-ahead journal: `started`
   before the mutation, outcome after; `indeterminate_earlier_attempt`,
   `idempotency_conflict`, `replay_refused_other_session`, `journal_unreadable`.
   Build keys are `session:plan-sha:run_id`. This is not exactly-once and the
   README says so.

Regression checks added: 17 in the extension suite (11 of them failed on the
code before the fix, the claim block aborting the run; all pass after), 10 in the MCP-side suite.

## 7. Close of day (2026-09-05)

State of the tree: 26 modified + 7 new files, **uncommitted**, no push, no
install run. All headless suites green (`check_ci.sh` 11/11, extension 81
checks, MCP-side 60, tools 108, legacy 75 + 23, time contract 20).

Resume here, in order:
1. `cd Sensei/extensions/sensei-midi-writer && npm run package` → add the
   new `.ablx` in Live 12.4 beta → restart Live (the installed extension is
   still 0.1.0: no claim, no journal, no in-place replace).
2. `live_bridge_status` → exactly one `loom_extension_fresh` root, version
   `loom-extension/0.2.0`, `session_id` present.
3. Acceptance list in sections 5–6 on an empty set, then on a set with one
   user clip in the target range.
4. Only after that: commit (noreply email, leak audit first) and push.
