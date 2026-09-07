# 2026-09-06, night — What this actually is, and where the 98% sits

Not a work log; the technical one is `2026-09-06_stabilize.md`. This is the
conversation that reframed the work, both sides of it, written down because
the next sessions should start from it rather than from the tool list.

## The frame (the user's, in his own words)

> "bu bir ürün değil benim kendi üretim hattım o yüzden bir üründen fazlası
> olmak zorunda"

> "kullanıcıya ürün hazırlıyormuş gibi kasmıyorum … belki 2. yıl sonra gerçek
> bir ürün olarak paylaşırız"

> "şu anda tek tek geliştirdiğim araçları topluca mcp olarak bağlamak hazzı
> yetiyor … bu araçlar benim üretim darboğazlarımın gerekliliği"

> "sonraki adım onların nerelerde yanlış olduğunu bulup düzeltmek"

What this changes, concretely, in what gets built:

- **Stop building product surface.** Two-language README, install paths for
  three MCP clients, fidelity disclaimers addressed to a stranger, storefront
  guides — waste for a production line with one known user. Keep exactly the
  documentation that the author needs to resume work.
- **Correctness is not "works for anyone", it is "removes a real bottleneck
  for this person".** A tool that is generically correct and does not sit on a
  bottleneck is not progress.
- **Tool count is a measure of effort, not of coverage.** The earlier remark
  "44 tools is too many" was the wrong criticism. The right question: which
  tool corresponds to a real bottleneck, and which one exists because it was
  buildable.

## The real tool count: three, plus a weak fourth

The user's own count, and it holds up against the tree:

| Real tool | What it is | Everything hanging off it |
|---|---|---|
| **Crate** | fetch → read → spots → chop → pack | `crate_fetch/read/spots/chop/agent`, `crate_to_live` |
| **MIDI** | generate from the locked dataset, write into Live | `midi_generate`, `midi_write_*`, `project_build`, the extension's writers |
| **XML layer** | read a project, measure the mix, intervene surgically | `project_*`, `automation_*`, `chain_*`, `drumbuss_*`, `mix_*`, `palette_read`, `render_*` |
| *(weak 4th)* | ArrangementGPS: a plan from a prompt | `plan_create`, `plan_verify` |

44 tool names, three capabilities.

## Why it feels like most of the job is done

The finished parts are the *legible* ones: read a file, write a note, cut a
slice. Their correctness is measurable, testable, countable. The missing parts
require judgement, and judgement does not show up in a test count.

## What is missing, and the pattern under it

The user named four. Three of them share one shape: **the measurement layer
exists, the decision layer does not.**

| Missing layer | What exists today | What is absent |
|---|---|---|
| Sound design | AISoundDesigner *reads* the palette (which samples recur in ≥2 projects) | nothing designs anything |
| Automation | `automation_write` writes an envelope onto a real target, verified | nothing decides *what* to automate; the user's own style (envelope-modulated dynamic EQ, parallel glue, Drum Buss for character) is coded nowhere |
| Musical intelligence | measured, but from **outside**: 150 arabesk records, Groove MIDI, POP909 | nothing measured from the user's own 1,235 tracks |
| Habits model | three separate readings: arrangement shapes (58 projects), device chains (1,235 tracks), sample palette | nothing joins them into "how this person works" |

Loom today can observe and can execute. Almost nothing in it decides, and what
does decides by fixed rule.

Two more the user did not name, added here:

- **No feedback loop.** Loom writes a clip, the user fixes it in Live, the fix
  goes nowhere. For a production line that is the load-bearing gap: the user's
  correction should be the next generation's input.
- **The plan generator invents.** It produces 17 tracks and preset names from a
  prompt; the measured shape of the user's own work is 84 bars / 8 sections
  (`reference_user_arrangement_shapes`). The plan describes a generic template,
  not this person's practice.

## The XML finding, and why it matters for what is missing

Measured tonight, and consistent with the earlier crate-bench work:

> **Synthesising XML from nothing fails. Transplanting what Live wrote works.**

- Hand-built clip and device XML broke Live three times, each with a different
  schema rule, and then segfaulted it.
- `buss_builder`, Presetor's chain transplant and the bench's device harvest
  all *clone a subtree Live itself serialised*, renumber the ids, and verify by
  reloading. Those open in Live with zero repairs.

The consequence for the missing layers: **sound design and automation are
device-level work — exactly what the XML path is good at.** Neither needs
clip or set synthesis. So the route is not "hand-write XML from scratch"; it
is harvest → place → renumber → reload and compare. That route is already
proven. What is missing on top of it is only the layer that decides.

The user, on being willing to do it the hard way:

> "bu işi yapmak için bir ableton projesinin xml dosyasını elimle yazmam
> gerekse bile uğraşmak isterim"

## The next job the user named: de-noise the reader layer

> "bu projenin yüzde 98'i zaten okuyucu katman … sıradaki iş burada gürültüyü
> temizleyip tekilliğe indirmek olmalı"

Context he gave: the same data sits in more than one place in the XML, and
finding which layer is the true one took repeated experiments — including for
features he has never used.

Instances of that duplication seen in the tree tonight, as starting points
(each is "the same fact, more than one reader"):

1. **Drum pad notes** — three readers: the preset's XML
   (`profile_exporter.extract_drum_pads`), the live device through the SDK
   (`drum_pads`), and the plan's `instrument_family` name. Tonight's bug lived
   exactly here: the generator assumed a fourth, invisible source (General MIDI).
2. **Sample reference** — `profile_exporter.extract_branch_samples` already
   carries a three-tier fallback (`MultiSamplePart` → `SampleInfo` → any
   `FileRef`). That fallback chain *is* the noise being described; which tier
   is authoritative per device type is not written down anywhere.
3. **Track name** — `UserName` vs `EffectiveName` vs `display_name`.
   `chain_builder.find_track` already had to work around `project_analyzer`
   reading only `UserName`. Two readers, one fact, one silent disagreement.
4. **Device chain** — `project_analyzer.direct_devices`, Presetor's
   `chain_of`, `extract_device_chains.display_name`, and the SDK's
   `track.devices`. Four readers, four normalisations of the same list.
5. **Tempo and key** — the `.als` XML, the live state, the plan's `project`
   block, and `crate_read`'s measurement of the audio. `_beats_per_bar()`
   already has an explicit precedence order and reports its source; that is
   the pattern the others lack.

The shape of the fix, from the one place that already does it right:
**one owner per fact, and every answer says which source it came from.**
`beats_per_bar_source`, `data_source`, `target_evidence` are the existing
precedent.

## Done in the session that followed (same night)

The five duplications above were singularised; `tests/test_reader_contract.py`
(32 checks) pins one owner per fact and that every reader in the tree is the
same callable. Detail in `2026-09-06_reader_layer.md`. Still open from this
page: the missing decision layers, the feedback loop, the corpus measured
from outside, and the plan generator's invented shape.

## Standing items

- Live acceptance of `loom.ablx` (0.4.0, id `subverselab.loom`): add it,
  remove the old extension from Live, restart, `journal_import`, then a build
  with a real kit. Nothing in the new shape has run against a real Live.
- The drum corpus is GM-pitched; the pad-role mapping added tonight is a
  patch over that, not a fix. The corpus itself was built from outside.
- Evidence gaps by genre (Hip Hop bass, Trap chords) are a **data** problem,
  not a code one. The user's own archive is the better source and it exists.
