// Headless proof of the extension-side bridge: fakes stand in for Live, a
// temp directory stands in for the extension's storage. Every op the MCP can
// send is exercised through the real file contract (requests/ -> done/ or
// errors/), so what is proven here is exactly what mcp_server/server.py sees.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { NoteDescription } from "@ableton-extensions/sdk";
import {
  BridgeError,
  CAPABILITIES,
  applyOperation,
  bridgeDirs,
  ensureBridgeDirs,
  processRequestFile,
  publishState,
  type BridgeClipLike,
  type CueLike,
  type DeviceLike,
  type LiveLike,
  type ParamLike,
  type SlotLike,
  type TrackLike,
} from "../src/bridge.js";

class FakeParam implements ParamLike {
  value: number;
  constructor(readonly name: string, value: number, readonly min: number, readonly max: number) {
    this.value = value;
  }
  async getValue() {
    return this.value;
  }
  async setValue(value: number) {
    this.value = value;
  }
}
class FakeDevice implements DeviceLike {
  constructor(readonly name: string, readonly className: string, readonly parameters: ParamLike[] = []) {}
}
class FakeClip implements BridgeClipLike {
  notes: NoteDescription[] = [];
  name = "";
  constructor(readonly startTime: number, readonly length: number) {}
}
class FakeSlot implements SlotLike {
  clip: FakeClip | null = null;
  async createMidiClip(length: number) {
    this.clip = new FakeClip(0, length);
    return this.clip;
  }
}
class FakeTrack implements TrackLike {
  mute = false;
  solo = false;
  arm = false;
  devices: DeviceLike[] = [];
  mixer = { volume: new FakeParam("Track Volume", 0.85, 0, 1), panning: new FakeParam("Track Panning", 0, -1, 1) };
  clipSlots: FakeSlot[] = [new FakeSlot(), new FakeSlot()];
  arrangement: FakeClip[] = [];
  cleared: Array<[number, number]> = [];
  constructor(public name: string, readonly isMidi = true, devices: DeviceLike[] = []) {
    this.devices = devices;
  }
  async clearClipsInRange(start: number, end: number) {
    this.cleared.push([start, end]);
    this.arrangement = this.arrangement.filter((clip) => clip.startTime >= end || clip.startTime + clip.length <= start);
  }
  async createMidiClip(startTime: number, duration: number) {
    const clip = new FakeClip(startTime, duration);
    this.arrangement.push(clip);
    return clip;
  }
  async insertDevice(deviceName: string, index: number) {
    if (deviceName !== "Drum Rack" && deviceName !== "Operator") throw new Error(`unknown native device ${deviceName}`);
    const device = new FakeDevice(deviceName, deviceName === "Drum Rack" ? "DrumRackDevice" : "Operator");
    this.devices.splice(index, 0, device);
    return device;
  }
}
class FakeCue implements CueLike {
  constructor(public name: string, readonly time: number) {}
}
class FakeLive implements LiveLike {
  tempo = 120;
  rootNote = 0;
  scaleName = "Major";
  tracks: FakeTrack[];
  cuePoints: FakeCue[] = [];
  transactions = 0;
  constructor() {
    const eq = new FakeDevice("EQ Eight", "Eq8", [new FakeParam("Gain A", 0, -15, 15)]);
    this.tracks = [new FakeTrack("KICK", true, [eq]), new FakeTrack("BASS"), new FakeTrack("Vocal", false)];
  }
  async createCuePoint(time: number) {
    const cue = new FakeCue("", time);
    this.cuePoints.push(cue);
    return cue;
  }
  async createMidiTrack() {
    const track = new FakeTrack(`${this.tracks.length + 1}-MIDI`);
    this.tracks.push(track);
    return track;
  }
  withinTransaction<T>(fn: () => T): T {
    this.transactions++;
    return fn();
  }
}

const checks: string[] = [];
function ok(label: string, condition: boolean, detail?: unknown) {
  if (!condition) {
    console.error(`FAILED: ${label}${detail === undefined ? "" : ` -- ${JSON.stringify(detail)}`}`);
    process.exit(1);
  }
  checks.push(label);
}
async function rejects(label: string, fn: () => Promise<unknown>, needle: string) {
  try {
    await fn();
    ok(label, false, "did not throw");
  } catch (error) {
    ok(label, error instanceof BridgeError && error.message.includes(needle), error instanceof Error ? error.message : error);
  }
}

async function main() {
  const live = new FakeLive();

  // --- state --------------------------------------------------------------
  const state = await applyOperation(live, { op: "get_state" });
  ok("state carries the schema the MCP already reads", state.schema_version === "sensei.bridge.v2" && state.track_count === 3);
  ok("what the SDK lacks is null, not guessed", state.is_playing === null && state.signature_numerator === null && state.current_song_time === null);
  ok("capabilities are published so the MCP can refuse early", (state.capabilities as typeof CAPABILITIES).transport === false && (state.capabilities as typeof CAPABILITIES).arrangement_clips === true);
  const tracks = state.tracks as Array<Record<string, unknown>>;
  ok("mixer values come from Live's own parameters", (tracks[0].volume as Record<string, unknown>).value === 0.85 && tracks[2].has_midi_input === false);

  // --- tempo / mixer / device parameters ----------------------------------
  const tempo = await applyOperation(live, { op: "set_tempo", bpm: 126 });
  ok("set_tempo reports before/after from Live", tempo.before === 120 && tempo.after === 126 && live.tempo === 126);
  await rejects("a tempo outside Live's range is refused", () => applyOperation(live, { op: "set_tempo", bpm: 5 }), "outside");
  const mixer = await applyOperation(live, { op: "set_mixer", track: "BASS", volume: 0.5, mute: true });
  ok("set_mixer changes exactly what was asked", (mixer.changes as Record<string, { after: unknown }>).volume.after === 0.5 && live.tracks[1].mute === true);
  await rejects("a mixer value outside the parameter range is refused", () => applyOperation(live, { op: "set_mixer", track: "BASS", pan: 2 }), "outside Live's range");
  const param = await applyOperation(live, { op: "set_device_parameter", track: "KICK", device: "EQ Eight", parameter: "Gain A", value: -6 });
  ok("device parameter write is range-checked and read back", param.before === 0 && param.after === -6);
  await rejects("an unknown device is refused with the count", () => applyOperation(live, { op: "set_device_parameter", track: "KICK", device: "Nope", parameter: "x", value: 1 }), "found 0");
  const listed = await applyOperation(live, { op: "list_device_parameters", track: "KICK", device: "EQ Eight" });
  ok("parameters are listed with their ranges", (listed.parameters as Array<Record<string, unknown>>)[0].max === 15);

  // --- locators -----------------------------------------------------------
  const cue = await applyOperation(live, { op: "create_locator", beat: 32, name: "Verse 1" });
  ok("a locator is created, named and verified from the cue list", cue.created === true && cue.verified === true && live.cuePoints[0].name === "Verse 1");
  const adopted = await applyOperation(live, { op: "create_locator", beat: 32, name: "Verse" });
  ok("a cue already on the beat is adopted and renamed, never duplicated", adopted.adopted === true && live.cuePoints.length === 1 && live.cuePoints[0].name === "Verse");

  // --- arrangement clips ----------------------------------------------------
  const notes = [{ pitch: 36, start: 0, duration: 0.5, velocity: 100 }, { pitch: 38, start: 1, duration: 0.5, velocity: 90 }];
  const written = await applyOperation(live, { op: "write_arrangement_clip", track: "KICK", start_beat: 16, length_beats: 8, name: "Intro", notes });
  ok("an arrangement clip is written and read back note-for-note", written.verified_note_count === 2 && written.clip_name === "Intro" && live.tracks[0].arrangement.length === 1);
  ok("the write is one transaction and clears its own range first", live.transactions === 1 && live.tracks[0].cleared[0][0] === 16 && live.tracks[0].cleared[0][1] === 24);
  ok("notes reach the SDK in its own shape", live.tracks[0].arrangement[0].notes[0].startTime === 0 && live.tracks[0].arrangement[0].notes[1].pitch === 38);
  await applyOperation(live, { op: "write_arrangement_clip", track: "KICK", start_beat: 16, length_beats: 8, name: "Intro", notes });
  ok("rewriting the same range replaces the clip instead of stacking", live.tracks[0].arrangement.length === 1);
  await rejects("a note past the clip end is refused", () => applyOperation(live, { op: "write_arrangement_clip", track: "KICK", start_beat: 0, length_beats: 4, notes: [{ pitch: 60, start: 4, duration: 1, velocity: 100 }] }), "after the clip end");
  await rejects("an audio track is refused for MIDI", () => applyOperation(live, { op: "write_arrangement_clip", track: "Vocal", start_beat: 0, length_beats: 4, notes }), "not a MIDI track");

  // --- session clip (the default op) ----------------------------------------
  const session = await applyOperation(live, { track: "BASS", name: "probe", length_beats: 4, notes });
  ok("a request without op writes a session clip into the first empty slot", session.slot === 0 && session.note_count === 2 && live.tracks[1].clipSlots[0].clip?.name === "probe");

  // --- tracks ---------------------------------------------------------------
  const made = await applyOperation(live, { op: "create_midi_track", name: "Kit", instrument_family: "Drum Rack" });
  ok("a missing track is created, named, and a native device inserted", made.created === true && live.tracks[3].name === "Kit" && String(made.instrument).startsWith("inserted: Drum Rack"));
  const preset = await applyOperation(live, { op: "create_midi_track", name: "Keys", instrument_family: "Electric Piano Daze" });
  ok("a preset name is reported as not loadable here, never pretended", String(preset.instrument).startsWith("not_loadable_in_extension"));
  const again = await applyOperation(live, { op: "create_midi_track", name: "Kit", instrument_family: "Drum Rack" });
  ok("a rebuild adopts the track", again.adopted === true && live.tracks.filter((t) => t.name === "Kit").length === 1);
  await rejects("an audio track wearing the name is refused", () => applyOperation(live, { op: "create_midi_track", name: "Vocal" }), "non-MIDI");

  // --- honest refusals ------------------------------------------------------
  await rejects("transport is refused with the SDK reason", () => applyOperation(live, { op: "transport", action: "play" }), "unsupported_in_extension");
  await rejects("set_key is refused with the SDK reason", () => applyOperation(live, { op: "set_key", root: "F", mode: "Minor" }), "read-only");
  await rejects("an unknown op is refused", () => applyOperation(live, { op: "fly" }), "unknown op");

  // --- the file contract ----------------------------------------------------
  const root = mkdtempSync(join(tmpdir(), "loom-bridge-"));
  const dirs = bridgeDirs(root);
  ensureBridgeDirs(dirs);
  writeFileSync(join(dirs.requests, "req_1.json"), JSON.stringify({ op: "set_tempo", bpm: 100, id: "req_1" }));
  writeFileSync(join(dirs.requests, "req_2.json"), JSON.stringify({ op: "transport", action: "play", id: "req_2" }));
  writeFileSync(join(dirs.requests, "req_3.json"), "{not json");
  const logs: string[] = [];
  await processRequestFile(live, dirs, "req_1.json", (line) => logs.push(line));
  await processRequestFile(live, dirs, "req_2.json", (line) => logs.push(line));
  await processRequestFile(live, dirs, "req_3.json", (line) => logs.push(line));
  const done = JSON.parse(readFileSync(join(dirs.done, "req_1.json"), "utf8"));
  ok("a good request lands in done/ with the result inside the request", done.status === "ok" && done.result.after === 100 && done.id === "req_1" && !existsSync(join(dirs.requests, "req_1.json")));
  const failed = JSON.parse(readFileSync(join(dirs.errors, "req_2.json"), "utf8"));
  ok("a refused request lands in errors/ with the reason", failed.status === "error" && String(failed.error).includes("unsupported_in_extension"));
  ok("an unreadable request is moved out of the queue too", existsSync(join(dirs.errors, "req_3.json")) && readdirSync(dirs.requests).length === 0);
  ok("the surface's own log lines are kept", logs[0] === "Loom ok: set_tempo" && logs[1].startsWith("Loom error: BridgeError"));
  const published = await publishState(live, dirs);
  const onDisk = JSON.parse(readFileSync(dirs.stateFile, "utf8"));
  ok("state is published atomically where live_state expects it", onDisk.captured_at === published.captured_at && onDisk.tempo === 100 && !existsSync(`${dirs.stateFile}.tmp`));

  console.log(`${checks.length} checks passed:`);
  for (const label of checks) console.log(`  ok  ${label}`);
  console.log("EXTENSION BRIDGE WORKS");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
