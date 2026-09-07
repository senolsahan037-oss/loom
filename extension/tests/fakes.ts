// Fakes standing in for Live behind bridge.ts's structural interfaces. Used
// by tests/bridge.test.ts (in-process) and tests/fake_live_host.ts (a real
// consumer process the MCP-side tests talk to through the file contract).
// Nothing here imports the runtime SDK.
import type { NoteDescription } from "@ableton-extensions/sdk";
import { existsSync } from "node:fs";
import type { ArrangementClipLike, AudioClipLike, BridgeClipLike, CueLike, DeviceLike, DrumChainLike, DrumRackLike, LiveLike, ParamLike, SlotLike, TrackLike } from "../src/bridge.js";

// Fault injection, set by whoever drives the fake:
//   failNextNoteWrite  the next `clip.notes = ...` throws (one-shot)
//   holdFile           while this file exists, mutations that create objects wait
//   onMutation         called right after every mutation (snapshots, crashes)
export const faults: { failNextNoteWrite: boolean; holdFile: string | null; onMutation: (() => void) | null } = {
  failNextNoteWrite: false,
  holdFile: null,
  onMutation: null,
};

async function hold() {
  while (faults.holdFile && existsSync(faults.holdFile)) await new Promise((resolve) => setTimeout(resolve, 20));
}
function mutated() {
  faults.onMutation?.();
}

let fakeHandles = 0;

export class FakeParam implements ParamLike {
  value: number;
  constructor(readonly name: string, value: number, readonly min: number, readonly max: number) {
    this.value = value;
  }
  async getValue() {
    return this.value;
  }
  async setValue(value: number) {
    this.value = value;
    mutated();
  }
}
export class FakeDevice implements DeviceLike {
  constructor(readonly name: string, readonly className: string, readonly parameters: ParamLike[] = []) {}
}
export class FakeSimpler extends FakeDevice {
  sample: string | null = null;
  constructor() {
    super("Simpler", "Simpler");
  }
  async replaceSample(filePath: string) {
    if (filePath.includes("missing")) throw new Error(`no such file ${filePath}`);
    this.sample = filePath;
    mutated();
    return filePath;
  }
}
export class FakeDrumChain implements DrumChainLike {
  receivingNote = -1;
  devices: DeviceLike[] = [];
  async insertDevice(deviceName: string, index: number) {
    const device = deviceName === "Simpler" ? new FakeSimpler() : new FakeDevice(deviceName, deviceName);
    this.devices.splice(index, 0, device);
    mutated();
    return device;
  }
}
export class FakeDrumRack implements DrumRackLike {
  chains: FakeDrumChain[] = [];
  constructor(readonly name = "Drum Rack", padNotes: number[] = []) {
    for (const note of padNotes) {
      const chain = new FakeDrumChain();
      chain.receivingNote = note;
      this.chains.push(chain);
    }
  }
  get padNotes() {
    return [...new Set(this.chains.map((chain) => chain.receivingNote))].filter((note) => note >= 0 && note <= 127);
  }
  async insertChain(index: number) {
    const chain = new FakeDrumChain();
    this.chains.splice(index, 0, chain);
    mutated();
    return chain;
  }
}
export class FakeClip implements BridgeClipLike {
  private _notes: NoteDescription[] = [];
  private _name = "";
  get name() {
    return this._name;
  }
  set name(value: string) {
    this._name = value;
    mutated();
  }
  // Like the SDK's Handle.id: unique per clip object, never reused.
  readonly handleId = `fake-${++fakeHandles}`;
  constructor(readonly startTime: number, readonly length: number) {}
  get lengthBeats() {
    return this.length;
  }
  get notes() {
    return this._notes;
  }
  set notes(value: NoteDescription[]) {
    if (faults.failNextNoteWrite) {
      faults.failNextNoteWrite = false;
      throw new Error("Live refused the notes");
    }
    this._notes = value;
    mutated();
  }
  get endTime() {
    return this.startTime + this.length;
  }
}
export class FakeAudioClip implements AudioClipLike {
  name = "";
  constructor(readonly filePath: string, readonly startTime = 0, readonly duration?: number, readonly warped = true) {}
}
export class FakeSlot implements SlotLike {
  clip: FakeClip | FakeAudioClip | null = null;
  async createMidiClip(length: number) {
    await hold();
    this.clip = new FakeClip(0, length);
    mutated();
    return this.clip;
  }
  async deleteClip() {
    this.clip = null;
    mutated();
  }
  async createAudioClip(filePath: string, isWarped = true) {
    const clip = new FakeAudioClip(filePath, 0, undefined, isWarped);
    this.clip = clip;
    mutated();
    return clip;
  }
}
export class FakeTrack implements TrackLike {
  mute = false;
  solo = false;
  arm = false;
  devices: DeviceLike[] = [];
  mixer = { volume: new FakeParam("Track Volume", 0.85, 0, 1), panning: new FakeParam("Track Panning", 0, -1, 1) };
  clipSlots: FakeSlot[] = [new FakeSlot(), new FakeSlot()];
  arrangement: FakeClip[] = [];
  // The track's Drum Rack, if any. `drumPadNotes` is the shorthand tests use.
  drumRack: FakeDrumRack | null = null;
  get drumPadNotes(): number[] | null {
    return this.drumRack ? this.drumRack.padNotes : null;
  }
  set drumPadNotes(notes: number[] | null) {
    this.drumRack = notes === null ? null : new FakeDrumRack("Drum Rack", notes);
  }
  get drumRacks(): DrumRackLike[] {
    return this.drumRack ? [this.drumRack] : [];
  }
  audioClips: FakeAudioClip[] = [];
  constructor(public name: string, readonly isMidi = true, devices: DeviceLike[] = []) {
    this.devices = devices;
  }
  get isAudio() {
    return !this.isMidi;
  }
  async createAudioClipInArrangement(startTime: number, filePath: string, duration?: number, isWarped = true) {
    const clip = new FakeAudioClip(filePath, startTime, duration, isWarped);
    this.audioClips.push(clip);
    mutated();
    return clip;
  }
  get arrangementClips(): ArrangementClipLike[] {
    return this.arrangement.map((clip) => ({ name: clip.name, startTime: clip.startTime, endTime: clip.startTime + clip.length, handleId: clip.handleId }));
  }
  arrangementMidiClip(target: ArrangementClipLike) {
    return this.arrangement.find((clip) => (target.handleId ? clip.handleId === target.handleId : clip.name === target.name && clip.startTime === target.startTime && clip.startTime + clip.length === target.endTime)) ?? null;
  }
  async deleteArrangementClip(target: ArrangementClipLike) {
    this.arrangement = this.arrangement.filter((clip) => (target.handleId ? clip.handleId !== target.handleId : !(clip.name === target.name && clip.startTime === target.startTime && clip.startTime + clip.length === target.endTime)));
    mutated();
  }
  // In-process hold used by bridge.test.ts (a promise the test resolves).
  holdCreate: Promise<void> | null = null;
  async createMidiClip(startTime: number, duration: number) {
    if (this.holdCreate) await this.holdCreate;
    await hold();
    const clip = new FakeClip(startTime, duration);
    this.arrangement.push(clip);
    mutated();
    return clip;
  }
  async insertDevice(deviceName: string, index: number) {
    if (deviceName !== "Drum Rack" && deviceName !== "Operator") throw new Error(`unknown native device ${deviceName}`);
    const device = new FakeDevice(deviceName, deviceName === "Drum Rack" ? "DrumRackDevice" : "Operator");
    this.devices.splice(index, 0, device);
    if (deviceName === "Drum Rack") this.drumRack = new FakeDrumRack("Drum Rack", []);
    mutated();
    return device;
  }
}
export class FakeCue implements CueLike {
  constructor(public name: string, readonly time: number) {}
}
export class FakeLive implements LiveLike {
  private _tempo = 120;
  rootNote = 0;
  scaleName = "Major";
  tracks: FakeTrack[];
  cuePoints: FakeCue[] = [];
  transactions = 0;
  imports: string[] = [];
  renders: Array<[string, number, number]> = [];
  constructor() {
    const eq = new FakeDevice("EQ Eight", "Eq8", [new FakeParam("Gain A", 0, -15, 15)]);
    this.tracks = [new FakeTrack("KICK", true, [eq]), new FakeTrack("BASS"), new FakeTrack("Vocal", false)];
  }
  get tempo() {
    return this._tempo;
  }
  set tempo(value: number) {
    this._tempo = value;
    mutated();
  }
  async createCuePoint(time: number) {
    await hold();
    const cue = new FakeCue("", time);
    this.cuePoints.push(cue);
    mutated();
    return cue;
  }
  async createMidiTrack() {
    await hold();
    const track = new FakeTrack(`${this.tracks.length + 1}-MIDI`);
    this.tracks.push(track);
    mutated();
    return track;
  }
  withinTransaction<T>(fn: () => T): T {
    this.transactions++;
    return fn();
  }
  async importIntoProject(filePath: string) {
    this.imports.push(filePath);
    return `/Project/Samples/Imported/${filePath.split("/").pop()}`;
  }
  async renderPreFxAudio(trackName: string, startTime: number, endTime: number) {
    this.renders.push([trackName, startTime, endTime]);
    return `/tmp/ext/render_${trackName}_${startTime}_${endTime}.wav`;
  }
  // What the fake set holds, for a test on the other side of the file contract.
  snapshot() {
    return {
      tempo: this._tempo,
      cues: this.cuePoints.map((cue) => ({ name: cue.name, time: cue.time })),
      tracks: this.tracks.map((track) => ({
        name: track.name,
        is_midi: track.isMidi,
        devices: track.devices.map((device) => device.name),
        arrangement: track.arrangement.map((clip) => ({ name: clip.name, start: clip.startTime, end: clip.startTime + clip.length, notes: clip.notes.length })),
        slots: track.clipSlots.map((slot) => (slot.clip ? { name: slot.clip.name, notes: "notes" in slot.clip ? slot.clip.notes.length : null } : null)),
      })),
    };
  }
}
