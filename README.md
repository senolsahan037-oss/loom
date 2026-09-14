# Loom

**Loom** is a measurement-based production system for Ableton Live by
[Şenol Şahan / SubverseLab](https://subverselab.com/loom): a local MCP server
that reads your own `.als` projects and library, answers with counts instead of
guesses, and writes MIDI, device chains, automation and arrangement markers into
a running Live session, verifying every write by reading it back.
<!-- mcp-name: io.github.senolsahan037-oss/loom -->

Canonical home: **https://subverselab.com/loom** · Cite: [`CITATION.cff`](CITATION.cff) ·
Attribution terms: [`NOTICE`](NOTICE). Copies and derivatives must keep the attribution.

[![checks](https://github.com/senolsahan037-oss/loom/actions/workflows/checks.yml/badge.svg)](https://github.com/senolsahan037-oss/loom/actions/workflows/checks.yml)
[![release](https://img.shields.io/github/v/release/senolsahan037-oss/loom?label=extension)](https://github.com/senolsahan037-oss/loom/releases/latest)

## Why it exists

Ask a general-purpose model what belongs on a kick drum and it answers with
confidence, having never heard a record you made. Loom replaces that guess with
evidence from three places: your own project archive (which devices you actually
chain, which samples you actually reach for), real released records (genre
evidence measured from audio, not described from memory), and the Live set that
is open right now. Where the evidence runs out, a tool returns nothing and says
so. Nothing is emulated, nothing is retried on an uncertain outcome, and every
write is a dry run until you approve it.

There is **one** connection to Live: the Loom extension, an Ableton Extension
running inside Live 12.4 beta. No Control Surface, no automatic fallback, no
second writer path.

## What is inside

| Layer | Job |
|---|---|
| **Sensei** | MIDI variation (drum / bass / chord) from a locked, hash-verified dataset |
| **ArrangementGPS** | A project plan from a prompt: tempo, key, genre, channels, sections (Node) |
| **AIMixMaster** | `.als` inspection, gain staging, clip alignment, drum buss, automation writing (on the file) |
| **Presetor / AISoundDesigner** | Device chains and a sound palette measured from the user's own projects |
| **MusicalIntelligence** | Genre evidence measured from real records; part suggestions keyed to the project |
| **Mix Check / SampleAgent** | Audio measurement against profiles of released masters; YouTube → sliced sample pack; a crate-digging agent |
| **Loom extension** | The Ableton Extension inside Live: the MCP's only connection to a running set |
| **mcp_server** | 45 tools, resources, prompts, progress and cancellation over stdio JSON-RPC |

No layer produces a guess: with no evidence there is no suggestion, what the SDK
cannot do is not emulated, and every writing tool is a dry run by default.

Architecture, the canonical call flow and the bridge protocol:
[`Docs/ARCHITECTURE.md`](Docs/ARCHITECTURE.md).

## Install (one path)

```bash
python3 install.py            # installs
python3 install.py --check    # changes nothing, reports state
```

`install.py`:

1. Registers Loom with every MCP client it finds (Claude Desktop, Claude Code,
   Antigravity), backing up each config; it is safe to re-run.
2. Prepares the extension package (`extension/dist/loom.ablx`, built if the
   toolchain is present) and compares the version and bridge protocol of the
   extension installed in Live with this checkout. Live shows it as **Loom**,
   id `subverselab.loom`. An installed legacy package (`loom.sensei-midi-writer`)
   is reported: the MCP never sends it a mutation (`LEGACY_EXTENSION`).
3. Builds the catalogs from your own stock Ableton library.

Live's own step is a single one: add the `.ablx` in Live 12.4 beta's Extensions
settings and restart Live. The extension opens a file bridge in its own storage
directory; the MCP locates that bridge on every call.

The same server is packaged as an MCP bundle on the
[release page](https://github.com/senolsahan037-oss/loom/releases/latest)
(`loom-<version>.mcpb`, described by [`server.json`](server.json)).

Versions are three different things: the package version is written once, in
`extension/manifest.json` (`package.json` must match; the build verifies it);
the bridge protocol is `loom.bridge/3`; the SDK API is `1.0.0`.

## Connection diagnosis (one path)

The MCP tool **`live_bridge_status`** reports which bridge it is talking to and
why, the age of the state, the session id, the capabilities the extension
publishes, `mutations_allowed`, the protocol verdict (`OK` / `UPGRADE_REQUIRED`
/ `PROTOCOL_MISMATCH` / `STALE_STATE` / `NO_STATE`), the journal's condition and
what is queued or in flight. `python3 install.py --check` prints the same in a
terminal.

An old extension (0.1.0 / 0.2.0, publishing no protocol) is **read but never
written**: every mutation is refused with `UPGRADE_REQUIRED` before a request
file is written. A package installed under the legacy id is treated the same
(`LEGACY_EXTENSION`); once the new one is installed and the old one removed from
Live, the old journal is carried over with `live_command op=journal_import`
(nothing forgotten, nothing deleted). More than one Loom bridge in sight is
`AMBIGUOUS_BRIDGE`. `LOOM_BRIDGE_ROOT` exists for tests only.

## Supported MCP tools

| Kind | Tools | Proven by |
|---|---|---|
| **Through Live (extension)** | `live_state`, `live_bridge_status`, `live_command` (set_tempo, set_mixer, set_device_parameter, list_device_parameters, create_locator, create_midi_track, import_audio_clip, render_pre_fx, drum_pads, **build_drum_kit**, journal_import, delete_track, delete_locator), `midi_write_arrangement`, `midi_write_to_live` (session clip), `crate_to_live`, `mix_from_live`, `project_build`, `midi_generate` (auto_write) | `mcp_server/tests/test_bridge_consumer_real.py` (the real `bridge.ts`), `test_extension_path.py`, `extension/tests/bridge.test.ts` |
| **Engines without Live** | `project_*`, `automation_*`, `drumbuss_*`, `chain_*`, `render_*`, `palette_read`, `library_search`, `genre_evidence`, `part_suggest`, `plan_create`, `plan_verify`, `projects_arrangement_shapes`, `mix_measure/analyze/profiles`, `crate_fetch/read/spots/chop/agent`, `setup_scan`, `gap_record` | `mcp_server/tests/test_mcp_tools.py` (45 tools over stdio) and each engine's own pytest suite |
| **OS level, on request** | `live_project` (open / close / status of Live, verified from Live's own log; changing the set restarts the extension host) | `test_live_project.py` |
| **Machine-bound** | `mix_capture` (`method="tap"`: a Core Audio process tap launched as `LiveTap.app` through LaunchServices; macOS grants the "Screen & System Audio Recording" permission to the **LiveTap** entry, not to the app running the MCP; measured from a playing Live on 2026-09-06) | only on a real machine |

The extension's single in-Live user command, **"Loom: Generate"** (right-click,
Session slot), goes through the same `write_clip` implementation and the same
ownership ledger: a clip Loom did not write is never overwritten.

### Kits and presets

The SDK does not load presets (`.adg` / `.adv`). Loom does not call this
"impossible"; it separates the paths:

- **A named kit**: `project_build(kit="Boom Bap Kit")` or a path to an `.adg`.
  The kit is read from the preset's own XML (pads, notes, names, sample files;
  Sensei's `.adg` reader), the files are resolved on this machine, and the pads
  are rebuilt in the extension as chain + Simpler + sample. The answer states
  what was carried over and what was **not**: per-pad effects, macros, choke
  groups, Simpler parameters, return chains. This is not loading the preset as
  is, and it is never presented as such.
- **A kit from samples**: `live_command op=build_drum_kit pads=[{note, sample}]`.
- **An instrument preset** (bass / chord): the SDK cannot load it. Either a
  native device through `device_map` (`Operator`, `Electric`, `Wavetable`, …),
  or you load the preset in Live yourself and re-run the same plan; the channel
  is adopted and its device read from state. The answer lists both routes under
  `needs_preset`.
- **Drum notes onto the kit's pads**: Sensei's drum evidence is in GM pad
  layout; on a kit such as 77–92 the notes are mapped by pad role (kick / snare
  / hat, `pad_mapping: by_role`), unmappable roles are dropped and reported, and
  with no notes left no clip is written (`no_notes_for_pads`).
- **Simplification is explicit**: with `tracks=[...]` and `device_map` the
  answer's `simplification` block says which channels were dropped and why, and
  which device stands in for which preset. Empty channels from the template are
  left alone.
- **The file path (B)**: writers that work on the `.als` (`automation_write`,
  `drumbuss_build`, `chain_apply`) change the set on disk; the open set is not
  touched and must be reopened. Preset XML is never hand-converted into a set;
  that is Live's job.

## Not supported, because of the SDK

Extensions SDK 1.0.0-beta.1 does not provide the following. Loom does **not**
emulate them; it answers `UNSUPPORTED_BY_SDK` with the name of the missing
capability, before any request file is written:

- transport (play / stop / position) → `live_command op=transport`, `mix_capture follow_transport`
- writing the song key → `live_command op=set_key`; the `project_build` step is reported as `UNSUPPORTED_BY_SDK`
- loading presets / the browser → `create_midi_track` only adds a native device with its default preset (`not_loadable_in_extension`); kits are rebuilt (above), instruments go through `device_map` or a user step
- time signature → bar-to-beat conversion asks for an explicit `beats_per_bar`, reads it from the `.als` when given one, and otherwise says it assumed 4/4 in `beats_per_bar_source`
- metering, recording (record mode / resampling) → `mix_capture method="resample"`, the `capture_*` ops

Reported to Ableton through Centercode on 2026-09-03.

## The bridge contract (short)

Every answer from Live carries a structured `outcome`:
`{kind: applied|refused|failed|indeterminate, code, applied, verified, side_effects, next_step}`.
The MCP status derives from it: `OK`, `REFUSED_IN_LIVE` (Live untouched),
`FAILED_IN_LIVE` (attempted, previous content restored), `INDETERMINATE` (may
have applied; side effects and the safe next step are in the answer),
`NOT_CONSUMED`, `INVALID_RESULT`. An uncertain outcome is never retried
automatically, anywhere.

Keyed requests (build steps, `idempotency_key`) are journaled **before** the
mutation: same key + same content → the stored result; different content → a
conflict; another session → refused; half-finished → `INDETERMINATE`. With the
journal missing or corrupt, keyed mutations are refused and the answer says
what to do; deleting the journal is never the advice. Details in
[`Docs/ARCHITECTURE.md`](Docs/ARCHITECTURE.md).

## Tests

```bash
./scripts/check_ci.sh    # needs neither Ableton nor personal data; CI runs this
./scripts/check_all.sh   # everything; needs a real Ableton install
```

`check_ci.sh` counts every suite as passed, failed or skipped; a suite with a
missing dependency is never counted as passed. The extension's own queue code
(`bridge.ts`) runs both on its own (`npm run test:bridge`) and as the real
consumer opposite the MCP (`test_bridge_consumer_real.py`); the Python fake
exists for speed and is not protocol proof on its own.

Tests use temporary directories and isolated fixtures; they never touch a real
Live, the installed extension or user data. The Ableton Extensions SDK is not
redistributable, so the hosted runner cannot typecheck the extension; that job
is marked skipped, never passed.

The real-Live acceptance script is `extension/tools/measure_bridge.py` (it
writes into the running Live's open set; run it on an empty set).

## Data policy

This repository ships code and fixtures, never measurements. Presetor's
device-chain evidence, AISoundDesigner's palette and Sensei's catalogs are
generated from the user's own projects and Ableton installation; none of it is
in the repository. On a clean clone the tests run on synthetic fixtures, and
every answer names its origin in `data_source`: `measured` or
`synthetic_fixture`.

```bash
python3 scripts/extract_device_chains.py --out Presetor/data/measured_device_chains.json
python3 scripts/extract_sound_sources.py --out AISoundDesigner/data/measured_sound_sources.json
python3 scripts/setup_scan.py --check      # catalogs: what is there, what is missing
```

## Status and known limits

- **Real-Live acceptance passed on 2026-09-07.** Starting from an empty set it
  opened real `.adg` / `.adv` presets, resolved the pads (16/16), wrote MIDI and
  locators; after save and reopen everything was in place. **Open edge:**
  `delete_track` / `delete_locator`, added in extension 0.4.4 (2026-09-11), are
  tested headless only; their real-Live acceptance is still to come. Version and
  acceptance claims are updated together with `extension/manifest.json`.
- Rendering needs Live's audio engine; `render_plan` says what should come out,
  `render_verify` says whether what came out matches.
- Automation writing (on the file) covers mixer and device parameters; no clip
  envelopes.
- Tool timeouts are not hard (a Python thread cannot be killed); a call that
  timed out never writes another request to Live.
- Because the SDK cannot read a clip's automation, colour or launch settings,
  `replace_owned` only replaces the same object in place; a replacement that
  would need a delete is refused. No full-preservation guarantee is claimed;
  this narrow policy is the guarantee.
- The journal is not exactly-once: work whose application is unknown is counted
  neither as applied nor as not applied; reading the state is the caller's job.
- `render_verify` and audio measurement need `soundfile` / `numpy`; every other
  tool runs on macOS's own Python.

Gap log: [`Docs/MISSING_CONTROLS_LOG.md`](Docs/MISSING_CONTROLS_LOG.md) (its
opening note separates historical entries).

## Copyright and attribution

© Şenol Şahan / SubverseLab. All rights reserved; the code is published for
reference (see [`LICENSE`](LICENSE)). Canonical home: https://subverselab.com/loom .
Anything that copies, adapts or derives from this code keeps this attribution
and the [`NOTICE`](NOTICE) file, and does not strip the `_source` field the
server puts in every answer. For written or academic citation, [`CITATION.cff`](CITATION.cff).
