# 2026-09-06 — De-noising the reader layer: one owner per fact

The job named in `2026-09-06_direction_notes.md`: *"bu projenin yüzde 98'i
zaten okuyucu katman … sıradaki iş burada gürültüyü temizleyip tekilliğe
indirmek olmalı"*. Not productisation — the same fact was readable from
several places, and two of those readers disagreed without saying so.

Tree still **uncommitted**. No install script run, no push. The headless work
below was done without touching Live; the real-Live acceptance at the end of
this page was run afterwards, by the user installing the package himself.

## The five, before and after

| Fact | Readers before | Disagreement found | Owner now |
|---|---|---|---|
| Track name | `project_analyzer.track_name` (UserName only), `extract_device_chains.display_name` (User→Effective), `extract_arrangement_shapes` (**Effective→User**), `server.project_inspect` inline, `als_mixer_compact.track_name` | **real, silent**: the arrangement scan and every other reader could report the same track under two different names; the UserName-only reader skipped unnamed tracks entirely (its own comment admitted it and worked around itself) | `project_analyzer.resolve_track_name` |
| Device chain | `project_analyzer.direct_devices` (tags), `presetor.chain_of`, `extract_device_chains` (rack-expanded), `gain_staging.normalized_device_name` (second alias table) | two different views (top-level vs rack-expanded) with nothing naming which was which; two copies of the alias table | `project_analyzer.resolve_device_chain` + `device_name` |
| Tempo / key / signature | `server.project_inspect` inline, `extract_arrangement_shapes` inline, `_beats_per_bar` (already correct) | key defaulted to "Major" when the set carried no `ScaleInformation`; `beats_per_bar` fell back to 4 with no way to tell a reading from a fallback | `project_analyzer.musical_context` |
| Pad notes | `alc_inspector.extract_drum_pads` / `_loose`, SDK `drum_pads`, plan `instrument_family`, **and an unwritten General MIDI assumption inside the generator** | the fourth source was invisible: nothing in an answer said the notes were assumed | `kit_resolver.resolve_pad_notes` |
| Sample reference | `profile_exporter.extract_branch_samples` (3-tier fallback), `kit_resolver._resolve_sample`, plus five `.als` clip readers | resolution returned free text (`relative_from:…`), so "found" and "found how" could not be told apart | `kit_resolver` with named states |

## What each answer carries now

`Resolved(value, source, alternatives, confidence)` from the `.als` reader;
the same shape as plain dicts from the kit reader (it crosses the MCP
boundary as JSON). Concretely:

- `resolve_track_name` → `{"value": "MY KICK", "source": "user_name", "alternatives": {"effective_name": "1-MIDI"}}`
- `resolve_device_chain(expand=True)` → `source: "rack_expanded"`, with the top-level chain and `uses_rack` kept beside it
- `musical_context` → tempo / key / beats_per_bar, each with `source` and `confidence`; **missing stays missing** (no C major, no assumed 4)
- `resolve_pad_notes` → `source` one of `live_drum_rack` / `preset_xml` / `assumed_general_midi`, `writable` false for the last, plus the role `mapping`
- kit pads → `reference_state` ∈ `absolute` / `pack_relative` / `missing`, `found_in`, and `sample_states` counts on the kit

Verified on this machine: Boom Bap Kit 16 pads, all `pack_relative`, 0 missing.

## Routed to the owner (no copies left)

`extract_device_chains`, `extract_sound_sources`, `extract_arrangement_shapes`,
`presetor.chain_builder`, `presetor.chain_planner`, `gain_staging`,
`mcp_server.server` (project_inspect, automation_write,
automation_list_targets, midi_generate, project_build). The contract test
asserts every track-name reader is *the same callable*, not six that agree.

Two library functions were living inside a CLI script (`scripts/extract_device_chains.py`)
and being imported by `sys.path` insertion from Presetor and the MCP; they
moved to `project_analyzer` and the script re-exports the names its three
importers use.

## Behaviour that changed on purpose

- `project_analyzer.track_name` is widened (UserName → EffectiveName). It
  feeds `analyze_tracks`, `find_unique_track` and `preservation_snapshot`,
  so a project whose tracks have no UserName is now visible to the buss
  builder and the transplant instead of resolving to `""`. Where two tracks
  collide on a name, `find_unique_track` still fails closed.
- `project_inspect` reports `tempo_source`, `key_source` and per-track
  `name_source`; a set with no `ScaleInformation` answers `Unknown` /
  `absent_in_set` instead of "Major".
- `extract_arrangement_shapes` reports `tempo_source` and
  `beats_per_bar_source` (`assumed_4_4` when the set carries no signature).
- `midi_generate` reports `pad_source`; the GM path stays `writable_to_live: false`.

## Removed, with evidence

| Removed | Why dead | Replacement | Verified |
|---|---|---|---|
| `extract_device_chains.display_name` / `expand_devices` / `RACK_TAGS` / `MAX_RACK_DEPTH` (definitions) | library code inside a CLI script, imported by path insertion from 3 modules | `project_analyzer` (names re-exported so importers keep working) | contract test: same callable; all three CLIs still run `--help`; CI |
| second device-name alias table in `gain_staging` | duplicate of the owner's table | `project_analyzer.device_name` (alias kept: callers and tests import the old name from here) | contract test, AIMixMaster pytest 30 |
| `ROOT_MAP` in `server.py` | pitch-class table superseded by `project_analyzer.PITCH_CLASSES`; grep shows zero references after routing | owner's table | grep + tools suite 109 |
| `GM_DRUM_ROLES`, `GM_STANDARD_PADS`, `GM_DRUM_PAD_NOTES`, `kit_pad_mapping` body in `server.py` | second copy of the pad vocabulary | `kit_resolver` (thin accessors keep one import site) | contract test asserts the MCP has no second table |

Nothing else was deleted. The `.als`/`.adg` engines, the XML writers, the
research scripts and the old tests are untouched — none of them became
redundant, only their duplicated *readings* did.

## Test isolation fixed

`test_extension_path.py` wrote `build_plan_small.json` and
`build_plan_broken.json` into `tests/fixtures/` (the repo) and deleted them in
a `finally`. It now builds them in a `tempfile.mkdtemp()` directory, so a
failed run cannot leave a fixture behind and `tests/fixtures/` holds committed
evidence only.

## Product identity

`extension/package-lock.json` still carried `sensei-midi-writer` / `0.7.0`
while the manifest and package said Loom / 0.4.0. Now consistent:
manifest `Loom` / `SubverseLab` / 0.4.0 → Live id `subverselab.loom`,
package + lock `loom-extension` 0.4.0, artifact `extension/dist/loom.ablx`
(rebuilt, manifest verified inside the archive).

## Results (this machine, 2026-09-06)

| Suite | Result |
|---|---|
| `scripts/check_ci.sh` | **12 passed, 0 failed, 0 skipped** |
| `tests/test_reader_contract.py` (new) | 32 checks |
| extension `tsc --noEmit` + `bridge.test.ts` | clean, 122 checks |
| `test_bridge_consumer_real.py` (real bridge.ts + fake Live) | 22 checks |
| `test_extension_path.py` | 97 checks |
| `test_mcp_tools.py` (44 tools, stdio) | 109 checks |
| `test_mcp_protocol.py` | 42 checks |
| AIMixMaster pytest | 30 passed |
| Presetor / AISoundDesigner | 28 / 30 checks |
| Sensei (time contract, engine, genre, kit resolver) | 24 passed, 1 skipped (no bass corpus for that genre) |
| Mix Check / crate agent pytest | 27 passed |
| `npm run package` | `dist/loom.ablx`, manifest `Loom` 0.4.0 verified inside the archive |

The three reliability gaps were re-checked, not assumed: journal retention /
loss / torn tail / import, INDETERMINATE end to end (held request, killed
host, restart, retry, build, stdio), and the protocol gate
(`UPGRADE_REQUIRED` / `PROTOCOL_MISMATCH` / `STALE_STATE` / `STALE_SESSION` /
`LEGACY_EXTENSION`) all still hold — they are inside the 122 / 97 / 22 above.

## Still missing (unchanged by this pass)

The decision layers: sound design, automation, musical intelligence measured
from the user's own archive, the habits model, the feedback loop, and a plan
generator that describes this person's practice rather than a template. See
`2026-09-06_direction_notes.md`.

## Real Live acceptance — not done

Code and package are ready for it; nothing here has run against Live.

1. Add `extension/dist/loom.ablx` (0.4.0) in Live 12.4 beta.
2. Remove the old extension (`loom.sensei-midi-writer`) from Live's Extensions.
3. Restart Live → `live_bridge_status`: one fresh `subverselab.loom`,
   `mutations_allowed: true`, protocol `loom.bridge/3`.
4. `live_command op=journal_import` to carry the old replay journal over.
5. On an empty set: `project_build(prompt="90 bpm D minor hip hop",
   kit="Boom Bap Kit", dry_run=false)` — kit pads audible, drum clips on the
   kit's own pads, then save and reopen to confirm the sample references hold.

---

## Real Live acceptance — DONE (2026-09-07 00:33–00:36, Live 12.4 beta, `subverselab.loom` 0.4.0)

The user added `loom.ablx`, removed `loom.sensei-midi-writer` from Live's
Extensions and restarted. First run of the new shape against a real Live.

**Identity and handover, verified from the bridge itself:**
`live_bridge_status` → one fresh root at `Extensions Data/subverselab.loom/bridge`,
`loom-extension/0.4.0`, protocol `loom.bridge/3`, `mutations_allowed: true`,
journal `fresh`. The old id was listed as `stale`, superseded, never mutated.
`live_command op=journal_import` carried its 49 entries over (50 now, 2
sessions, 1 unknown-outcome key preserved — the interrupted one from the crash
test, which must stay refusable).

**Build 1 — drums and chords, 15.8 s, status `completed`:**

| | |
|---|---|
| Kit | Drum Rack inserted, **Boom Bap Kit rebuilt: 16 pads requested, 16 built, 0 samples missing**; pads read back from the device as 77–92, `pad_source: live_drum_rack`, mapping `by_role` |
| Keys | Electric inserted (`device_map`, because the plan named the unloadable preset "Electric Piano Daze") |
| Clips | 13 OK, 1 muted by plan; every one `verified_notes_match: true` |
| Drum clips | 112 / 272 / 256 / 232 / 96 / 572 notes — **the bug that was invisible until today**: with the corpus GM-pitched and this kit at 77–92, these were empty clips reported OK before `pad_mapping` |
| Locators | 7, all verified from the read-back; tempo 90 verified |
| Indeterminate | none; aborted: none |

**Build 2 — bass, 8.4 s, status `completed`:** Main Bass with Operator,
6 clips (36–104 notes), all verified. Genre switched to Trap for this build
*because Hip Hop has no bass corpus* — measured before writing, not discovered
as a blocked write.

**Final set:** tempo 90, 7 locators, Kit / Keys / Main Bass with their devices,
19 verified clips. The template's own empty tracks were left untouched.

### What the run surfaced (nothing broke; three known gaps showed up in practice)

1. **Evidence gaps forced two builds.** Hip Hop has drums and chords but no
   bass; Trap has drums and bass but no chords. One genre per plan means one
   gap per build. This is the data problem from the direction notes, now
   measured in a live run rather than argued.
2. **The plan proposed 17 tracks for "basic".** 3 were built; the other 14 are
   listed in `simplification.dropped` with the reason. The plan generator still
   describes a template, not this producer's practice.
3. **The kit's 16 macros are not carried.** Stated in `kit.fidelity.dropped`
   before the write, as designed — a rebuilt kit is not the preset.

### Still only the user can answer

Whether the rebuilt kit *sounds* right: the pads exist and carry their sample
files, but audibility and the Simpler settings the SDK cannot copy are a
listening test. Same for save-and-reopen: whether the sample references hold
after Live saves the set to a project folder.

## Scripted build, new set, the user's own kit (2026-09-07 00:55)

The user asked for a new project with a real hip hop kit rather than an empty
Drum Rack, and for the MIDI to come only from the MCP's writers, driven by a
script. `scripts/build_live_project.py` is that script: it verifies the
bridge, **refuses to write into a set that is not new** (`--into-current-set`
overrides), resolves the kit, measures the evidence per role, groups the plan's
tracks by a genre that can answer for them, and runs `project_build` per group.
It constructs no note: every one comes from `midi_generate` and is written by
`midi_write_arrangement` inside `project_build`.

Kit: **Black Squid Kit** (Golden Era Hip-Hop Drums, the user's own pack) —
16/16 pads resolved, 0 samples missing, all four core roles present, no
unmapped role. Chosen over Boom Bap Kit after measuring seven candidates;
`Golden Kit` was rejected because none of its pad labels resolve to a core
role (0/4), which would have written nothing.

Result on a fresh set, 25.9 s, both groups `completed`:

| Group | Genre | Tracks | Clips |
|---|---|---|---|
| 1 | Hip Hop (the plan's own) | Kit (Drum Rack + kit rebuilt, 16 pads), Keys (Electric) | 13 written, **13 verified note-for-note, 1,740 notes** |
| 2 | Trap (substituted: Hip Hop has no bass corpus) | Main Bass (Operator) | 6 written, **6 verified, 416 notes** |

7 locators verified from the read-back in both, tempo 90 verified, no
INDETERMINATE, no aborted.

### The script found a bug while being written

`measure_evidence` was passing the kit's **own pad numbers** to
`midi_generate`, but Sensei's drum corpus is General-MIDI pitched and the
kit's pads are reached afterwards through `pad_mapping`. It therefore reported
`no_candidate_satisfies_target_profile` for Hip Hop drums — a role the corpus
answers perfectly well — and would have silently substituted Trap for a genre
that needed no substitution. Same class as the original GM bug: a value passed
in the wrong space. Fixed to pass `mapping["generate_pads"]`, with the reason
written at the function.
