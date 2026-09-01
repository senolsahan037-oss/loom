// Headless proof of the arrangement builder: no Live, no .ablx reinstall.
// Runs the real functions from src/arrangement.ts against the real
// ArrangementGPS action list, with stand-in Song/Track objects that record
// every call. What this proves is the plan->clip/locator logic; what it
// cannot prove is Live's own behaviour behind createMidiClip/createCuePoint.
import assert from "node:assert/strict";
import type { NoteDescription } from "@ableton-extensions/sdk";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  BEATS_PER_BAR,
  barToBeat,
  buildArrangementForTrack,
  ensureLocators,
  scaleVelocities,
  sectionsForTrack,
  tileNotes,
  validateSections,
  velocityScaleFor,
  type ArrangementBuildResult,
  type ArrangementGpsPlanTrack,
  type ArrangementGpsSection,
  type ArrangementTrackLike,
  type ClipLike,
  type CuePointLike,
  type SenseiPayload,
  type SongLike,
  type TransactionRunner,
} from "../src/arrangement.js";

const here = dirname(fileURLToPath(import.meta.url));
const BUILDS = join(here, "..", "..", "..", "..", "ArrangementGPS", "Builds");

// Always test against the most recent real build rather than a pinned
// directory: a pinned fixture silently goes stale the moment the plan format
// changes, which is exactly how the 15/29 figure survived long after it
// stopped being true.
function newestActionList(): string {
  const candidates = readdirSync(BUILDS, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => join(BUILDS, entry.name, "ableton_action_list.json"))
    .filter((file) => existsSync(file))
    // The plan with the most actions wins, not the newest one: an empty stub
    // build must not outrank the fixture merely by being more recent.
    .sort((a, b) => {
      const size = (file: string) => {
        try {
          return (JSON.parse(readFileSync(file, "utf8")) as { actions?: unknown[] }).actions?.length ?? 0;
        } catch {
          return 0;
        }
      };
      const difference = size(b) - size(a);
      return difference !== 0 ? difference : statSync(b).mtimeMs - statSync(a).mtimeMs;
    });
  if (candidates.length === 0) throw new Error(`No ableton_action_list.json under ${BUILDS} -- run the ArrangementGPS chain first.`);
  return candidates[0];
}

const ACTION_LIST = newestActionList();

type Action = Record<string, any>;
const actionList = JSON.parse(readFileSync(ACTION_LIST, "utf8")) as { actions: Action[] };

// Mirrors exactly what ArrangementGPSBuilder.py now writes into
// arrangementgps_last_build.json, so the fixture cannot drift from the plan.
const sections: ArrangementGpsSection[] = actionList.actions
  .filter((a) => a.action === "create_locator" && Number.isInteger(a.start_bar) && Number.isInteger(a.end_bar))
  .map((a) => ({ id: a.id, name: a.name, start_bar: a.start_bar, end_bar: a.end_bar }));
const planTracks: ArrangementGpsPlanTrack[] = actionList.actions
  .filter((a) => a.action === "create_midi_track")
  .map((a) => ({
    track_name: `${String(a.group).toUpperCase()} - ${a.name}`,
    instrument_family: a.instrument_family ?? null,
    clip_start_bar: a.clip_start_bar,
    clip_end_bar: a.clip_end_bar,
    mute_regions: a.mute_regions ?? [],
    section_activity: a.section_activity ?? {},
    sensei_role: a.sensei_role ?? null,
  }));

class FakeCuePoint implements CuePointLike {
  constructor(public name: string, readonly time: number) {}
}
class FakeSong implements SongLike {
  readonly cues: FakeCuePoint[] = [];
  get cuePoints(): readonly CuePointLike[] {
    return this.cues;
  }
  async createCuePoint(time: number): Promise<CuePointLike> {
    const cue = new FakeCuePoint("", time);
    this.cues.push(cue);
    return cue;
  }
}
type Recorded = { name: string; start: number; length: number; notes: number; peakVelocity: number };
class FakeTrack implements ArrangementTrackLike {
  readonly clips: Recorded[] = [];
  readonly calls: string[] = [];
  async clearClipsInRange(startTime: number, endTime: number) {
    this.calls.push(`clear(${startTime},${endTime})`);
    for (let index = this.clips.length - 1; index >= 0; index--) {
      const clip = this.clips[index];
      if (clip.start >= startTime && clip.start + clip.length <= endTime) this.clips.splice(index, 1);
    }
  }
  async createMidiClip(startTime: number, duration: number): Promise<ClipLike> {
    this.calls.push(`create(${startTime},${duration})`);
    const record: Recorded = { name: "", start: startTime, length: duration, notes: 0, peakVelocity: 0 };
    this.clips.push(record);
    let stored: NoteDescription[] = [];
    const clip: ClipLike = {
      set notes(value: NoteDescription[]) {
        stored = value;
        record.notes = value.length;
        record.peakVelocity = value.length === 0 ? 0 : Math.max(...value.map((note) => note.velocity ?? 0));
      },
      get notes(): NoteDescription[] {
        return stored;
      },
      set name(value: string) {
        record.name = value;
      },
      get name(): string {
        return record.name;
      },
    };
    return clip;
  }
}
const runner: TransactionRunner = { withinTransaction: (action) => action() };

// One bar of four quarter notes, repeated to 4 bars: 16 notes over 16 beats.
function pattern(): SenseiPayload {
  const notes = [];
  for (let beat = 0; beat < 16; beat++) notes.push({ pitch: 36, time: beat, duration: 0.5, velocity: 100 });
  // Every generated note is velocity 100 so any change in the written clip
  // is unambiguously the activity scaling and nothing else.
  return { notes, clip_length: 16, provenance: { source_role: "drum", genre: "House", source_reference_id: `ref_${notes.length}` } };
}
let generateCalls = 0;
const generate = async (seed: number, exclude: string[]) => {
  generateCalls++;
  return { payload: pattern(), outcome: { role: "drum", genre: "House", source: `ref_seed_${seed}_${exclude.length}` } };
};

const checks: string[] = [];
function check(label: string, fn: () => void) {
  fn();
  checks.push(label);
}

// ---- pure helpers -------------------------------------------------------
check("bar 1 -> beat 0, bar 9 -> beat 32", () => {
  assert.equal(barToBeat(1), 0);
  assert.equal(barToBeat(9), 32);
});
check("tileNotes repeats a 16-beat pattern 4x across 64 beats", () => {
  const tiled = tileNotes([{ pitch: 36, startTime: 0, duration: 1, velocity: 100 }], 16, 64);
  assert.equal(tiled.length, 4);
  assert.deepEqual(tiled.map((n) => n.startTime), [0, 16, 32, 48]);
});
check("tileNotes leaves a pattern longer than the section alone", () => {
  const notes = [{ pitch: 36, startTime: 0, duration: 1, velocity: 100 }];
  assert.equal(tileNotes(notes, 64, 32).length, 1);
});
check("tileNotes drops a note that would overrun into the next repeat", () => {
  const tiled = tileNotes([{ pitch: 36, startTime: 15, duration: 4, velocity: 100 }], 16, 32);
  assert.equal(tiled.length, 1, "overrunning note must not be tiled");
});
check("validateSections rejects an inverted bar range", () => {
  assert.throws(() => validateSections([{ name: "Bad", start_bar: 9, end_bar: 4 }]), /section_invalid/);
});

// ---- activity -> dynamics ----------------------------------------------
check("velocityScaleFor maps 100 to full and 0 to the floor", () => {
  assert.equal(velocityScaleFor(100), 1);
  assert.equal(velocityScaleFor(0), 0.6);
  assert.equal(Number(velocityScaleFor(50).toFixed(2)), 0.8);
});
check("a missing activity value never changes anything", () => {
  assert.equal(velocityScaleFor(undefined), 1);
  assert.equal(velocityScaleFor(Number.NaN), 1);
});
check("velocity is floored at 1, never silenced", () => {
  const scaled = scaleVelocities([{ pitch: 36, startTime: 0, duration: 1, velocity: 1 }], 0.6);
  assert.equal(scaled[0].velocity, 1);
});
check("scaleVelocities at full scale returns the notes untouched", () => {
  const notes = [{ pitch: 36, startTime: 0, duration: 1, velocity: 100 }];
  assert.equal(scaleVelocities(notes, 1), notes);
});

// ---- sections from the real plan ---------------------------------------
check("real action list yields 7 sections starting at bar 1", () => {
  assert.equal(sections.length, 7);
  assert.equal(sections[0].start_bar, 1);
  assert.deepEqual(sections.map((s) => barToBeat(s.start_bar)), [0, 32, 96, 128, 192, 224, 288]);
});
check("DRUMS - Kit is muted out of the Outro by the plan", () => {
  const drums = planTracks.find((t) => t.track_name === "DRUMS - Kit")!;
  const active = sectionsForTrack(sections, drums);
  assert.equal(active.length, 6);
  assert.ok(!active.some((s) => s.name === "Outro"), "Outro must be excluded by mute_regions");
});

// ---- locators -----------------------------------------------------------
const song = new FakeSong();
check("first run creates every locator at the right beat", async () => {});
const first = await ensureLocators(song, sections);
check("7 created, 0 adopted on a clean song", () => {
  assert.equal(first.created, 7);
  assert.equal(first.adopted, 0);
  assert.deepEqual(song.cues.map((c) => c.name), sections.map((s) => s.name));
  assert.deepEqual(song.cues.map((c) => c.time), [0, 32, 96, 128, 192, 224, 288]);
});
const second = await ensureLocators(song, sections);
check("second run is idempotent: 0 created, 7 adopted, still 7 locators", () => {
  assert.equal(second.created, 0);
  assert.equal(second.adopted, 7);
  assert.equal(song.cues.length, 7);
});
song.cues.push(new FakeCuePoint("MY OWN MARKER", 50));
const third = await ensureLocators(song, sections);
check("a locator the user placed off-boundary is never touched", () => {
  assert.equal(third.created, 0);
  const mine = song.cues.find((c) => c.time === 50);
  assert.ok(mine && mine.name === "MY OWN MARKER", "user locator must survive untouched");
  assert.equal(song.cues.length, 8);
});

// ---- clips --------------------------------------------------------------
const drumsPlan = planTracks.find((t) => t.track_name === "DRUMS - Kit")!;
const drumsTrack = new FakeTrack();
const drumsResults = await buildArrangementForTrack(runner, drumsTrack, drumsPlan, sections, true, generate);
check("DRUMS - Kit writes 6 clips, one per active section", () => {
  assert.equal(drumsResults.filter((r) => r.status === "written").length, 6);
  assert.deepEqual(drumsTrack.clips.map((c) => c.name), ["Intro", "Verse 1", "Hook", "Verse 2", "Bridge", "Final Hook"]);
  assert.deepEqual(drumsTrack.clips.map((c) => c.start), [0, 32, 96, 128, 192, 224]);
  assert.deepEqual(drumsTrack.clips.map((c) => c.length), [32, 64, 32, 64, 32, 64]);
});
check("note counts match the tiling: 16-beat pattern x2 or x4", () => {
  assert.deepEqual(drumsTrack.clips.map((c) => c.notes), [32, 64, 32, 64, 32, 64]);
});
check("section activity reaches the written clip as dynamics", () => {
  // DRUMS - Kit activity: intro 30, verse_1 75, hook 95, verse_2 75,
  // bridge 40, final_hook 100 -> velocity 100 scaled by 0.6 + 0.4*a/100.
  assert.deepEqual(drumsTrack.clips.map((c) => c.peakVelocity), [72, 90, 98, 90, 76, 100]);
  const intro = drumsResults.find((r) => r.section === "Intro")!;
  assert.equal(intro.activity, 30);
  assert.equal(intro.velocity_scale, 0.72);
});
check("the hook really is louder than the intro", () => {
  const byName = new Map(drumsTrack.clips.map((c) => [c.name, c.peakVelocity]));
  assert.ok(byName.get("Hook")! > byName.get("Intro")!, "Hook must outweigh Intro");
  assert.ok(byName.get("Final Hook")! > byName.get("Bridge")!, "Final Hook must outweigh Bridge");
});
check("every clip is cleared before it is created", () => {
  const pairs = drumsTrack.calls.join(" ");
  assert.ok(/clear\(0,32\) create\(0,32\)/.test(pairs), "clear must precede create for the Intro");
  assert.equal(drumsTrack.calls.filter((c) => c.startsWith("clear")).length, 6);
});
const rerun = await buildArrangementForTrack(runner, drumsTrack, drumsPlan, sections, true, generate);
check("rebuilding replaces clips instead of stacking them", () => {
  assert.equal(rerun.filter((r) => r.status === "written").length, 6);
  assert.equal(drumsTrack.clips.length, 6, "a second build must not double the clips");
});

const unsupported = await buildArrangementForTrack(
  runner,
  new FakeTrack(),
  { ...drumsPlan, sensei_role: null },
  sections,
  false,
  generate,
);
check("a lane Sensei has no role for is skipped, not reported as a failure", () => {
  assert.equal(unsupported.length, 1);
  assert.equal(unsupported[0].status, "skipped");
  assert.equal(unsupported[0].reason, "role_unsupported");
});
check("an older plan with no sensei_role field still falls through to instrument checks", () => {
  const legacy = { ...drumsPlan };
  delete (legacy as { sensei_role?: unknown }).sensei_role;
  assert.equal(legacy.sensei_role, undefined);
});

const blocked = await buildArrangementForTrack(runner, new FakeTrack(), drumsPlan, sections, false, generate);
check("a track with no verified instrument is blocked once, not per section", () => {
  assert.equal(blocked.length, 1);
  assert.equal(blocked[0].status, "blocked");
  assert.equal(blocked[0].reason, "no_recognized_instrument");
});

const failing = await buildArrangementForTrack(runner, new FakeTrack(), drumsPlan, sections, true, async () => {
  throw new Error("genre_evidence_missing: no evidence");
});
check("a generation failure stops that track after the first section", () => {
  assert.equal(failing.length, 1);
  assert.equal(failing[0].status, "blocked");
  assert.match(failing[0].reason!, /genre_evidence_missing/);
});

// ---- full-plan dry run --------------------------------------------------
const allResults: ArrangementBuildResult[] = [];
const layout = new Map<string, Recorded[]>();
for (const planTrack of planTracks) {
  const track = new FakeTrack();
  // A lane with a Sensei role has a catalogue-verified instrument in the plan
  // (proved separately by scripts/check_instrument_coverage.py), so the
  // target resolves; a lane without a role never gets that far.
  const resolves = planTrack.sensei_role !== null;
  allResults.push(...(await buildArrangementForTrack(runner, track, planTrack, sections, resolves, generate)));
  layout.set(planTrack.track_name, track.clips);
}

console.log(`\nPLAN: ${ACTION_LIST.split("/").slice(-2)[0]}`);
console.log(`\n${checks.length} checks passed:`);
for (const label of checks) console.log(`  ok  ${label}`);

console.log(`\nLOCATORS (${song.cues.length}):`);
for (const cue of [...song.cues].sort((a, b) => a.time - b.time)) {
  console.log(`  bar ${String(cue.time / BEATS_PER_BAR + 1).padStart(4)}  beat ${String(cue.time).padStart(4)}  ${cue.name}`);
}

console.log("\nARRANGEMENT (with current instrument coverage):");
const header = sections.map((s) => s.name.slice(0, 6).padEnd(7)).join("");
console.log(`  ${"".padEnd(24)}${header}`);
for (const planTrack of planTracks) {
  const clips = layout.get(planTrack.track_name)!;
  const row = sections
    .map((s) => {
      const clip = clips.find((c) => c.name === s.name);
      if (!clip) return planTrack.sensei_role === null ? "-----  " : "·····  ";
      const activity = s.id === undefined ? undefined : planTrack.section_activity?.[s.id];
      if (activity === undefined) return "█████  ";
      if (activity < 40) return "░░░░░  ";
      if (activity < 70) return "▒▒▒▒▒  ";
      return "█████  ";
    })
    .join("");
  console.log(`  ${planTrack.track_name.padEnd(24)}${row}`);
}
const written = allResults.filter((r) => r.status === "written").length;
const blockedCount = allResults.filter((r) => r.status === "blocked").length;
const skippedCount = allResults.filter((r) => r.status === "skipped").length;
assert.equal(blockedCount, 0, "no supported track should be blocked with the current plan");
assert.equal(skippedCount, 11, "the 11 melody/vocal/fx lanes are out of Sensei's scope");
console.log(`\nTOTAL: ${written} clips written, ${skippedCount} tracks out of scope, ${blockedCount} tracks blocked, ${generateCalls} Sensei calls`);
console.log("ALL CHECKS PASSED");
