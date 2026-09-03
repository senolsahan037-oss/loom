// The Loom bridge, inside the extension.
//
// Same file contract as the Loom control surface (AbletonScripts/Loom):
//   <root>/requests/<id>.json  -> processed oldest first
//   <root>/done/<id>.json      -> the request plus {status:"ok", result}
//   <root>/errors/<id>.json    -> the request plus {status:"error", error}
//   <root>/state/live_state.json, rewritten on a timer
// so mcp_server/server.py talks to either side unchanged; only the root
// differs. A hosted extension may only touch its storageDirectory and
// tempDirectory (Node's --allow-fs-* permissions, see GAP-008), so the root
// lives under storage: <storageDirectory>/bridge.
//
// Like arrangement.ts this file imports nothing from the runtime SDK. Live
// arrives through the structural interfaces below, which extension.ts
// satisfies with thin wrappers around the real objects and the tests satisfy
// with fakes. What the SDK cannot do is refused with a named reason, never
// approximated: transport, meters, time signature, key *write*, preset
// loading (insertDevice only knows native devices with their default preset).
import type { NoteDescription } from "@ableton-extensions/sdk";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export const SCHEMA_VERSION = "sensei.bridge.v2";
export const EXTENSION_SURFACE_VERSION = "loom-extension/0.1.0";
export const MAX_CLIP_NOTES = 4096;

// What this side can and cannot do, published in every state dump so the
// MCP can say "not available on this bridge" instead of waiting.
export const CAPABILITIES = {
  transport: false,
  meters: false,
  time_signature: false,
  key_write: false,
  preset_load: false,
  native_device_insert: true,
  arrangement_clips: true,
  session_clips: true,
  tracks: true,
  locators: true,
  mixer: true,
  device_parameters: true,
} as const;

export interface ParamLike {
  readonly name: string;
  readonly min: number;
  readonly max: number;
  getValue(): Promise<number>;
  setValue(value: number): Promise<void>;
}
export interface DeviceLike {
  readonly name: string;
  readonly className: string;
  readonly parameters: ParamLike[];
}
export interface BridgeClipLike {
  notes: NoteDescription[];
  name: string;
}
export interface SlotLike {
  readonly clip: BridgeClipLike | null;
  createMidiClip(length: number): Promise<BridgeClipLike>;
}
export interface TrackLike {
  name: string;
  mute: boolean;
  solo: boolean;
  arm: boolean;
  readonly isMidi: boolean;
  readonly devices: DeviceLike[];
  readonly mixer: { volume: ParamLike; panning: ParamLike };
  readonly clipSlots: SlotLike[];
  clearClipsInRange(startTime: number, endTime: number): Promise<void>;
  createMidiClip(startTime: number, duration: number): Promise<BridgeClipLike>;
  insertDevice(deviceName: string, index: number): Promise<DeviceLike>;
}
export interface CueLike {
  name: string;
  readonly time: number;
}
export interface LiveLike {
  tempo: number;
  readonly rootNote: number;
  readonly scaleName: string;
  readonly tracks: TrackLike[];
  readonly cuePoints: CueLike[];
  createCuePoint(time: number): Promise<CueLike>;
  createMidiTrack(): Promise<TrackLike>;
  withinTransaction<T>(fn: () => T): T;
}

export type BridgeRequest = { op?: string; id?: string; [key: string]: unknown };
export type BridgeResult = Record<string, unknown>;

export class BridgeError extends Error {}

const PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function num(value: unknown, label: string): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) throw new BridgeError(`${label} must be a number, got ${JSON.stringify(value)}`);
  return n;
}

function findTrack(live: LiveLike, name: unknown): TrackLike {
  if (!name) throw new BridgeError("track name is required");
  const matches = live.tracks.filter((track) => track.name === name);
  if (matches.length !== 1) throw new BridgeError(`expected exactly one track named ${JSON.stringify(name)}, found ${matches.length}`);
  return matches[0];
}

function findDevice(track: TrackLike, name: unknown): DeviceLike {
  const matches = track.devices.filter((device) => device.name === name);
  if (matches.length !== 1) throw new BridgeError(`expected exactly one device named ${JSON.stringify(name)} on ${JSON.stringify(track.name)}, found ${matches.length}`);
  return matches[0];
}

async function paramSnapshot(param: ParamLike) {
  return { name: param.name, value: await param.getValue(), min: param.min, max: param.max, display_value: null };
}

async function setParam(param: ParamLike, value: number) {
  if (value < param.min || value > param.max) {
    throw new BridgeError(`${param.name}=${value} is outside Live's range [${param.min}, ${param.max}]`);
  }
  const before = await param.getValue();
  await param.setValue(value);
  return { before, after: await param.getValue() };
}

function toNoteDescriptions(raw: unknown): NoteDescription[] {
  if (!Array.isArray(raw)) throw new BridgeError("notes must be a list");
  if (raw.length > MAX_CLIP_NOTES) throw new BridgeError(`too many notes: ${raw.length} > ${MAX_CLIP_NOTES}`);
  return raw.map((note, index) => {
    if (typeof note !== "object" || note === null) throw new BridgeError(`note ${index} is not an object`);
    const n = note as Record<string, unknown>;
    const pitch = num(n.pitch, `note ${index} pitch`);
    const start = num(n.start ?? n.time ?? n.startTime, `note ${index} start`);
    const duration = num(n.duration, `note ${index} duration`);
    const velocity = n.velocity === undefined ? 100 : num(n.velocity, `note ${index} velocity`);
    if (pitch < 0 || pitch > 127 || !Number.isInteger(pitch)) throw new BridgeError(`note ${index} pitch ${pitch} is not a MIDI pitch`);
    if (start < 0 || duration <= 0) throw new BridgeError(`note ${index} has start ${start} / duration ${duration}`);
    if (velocity < 1 || velocity > 127) throw new BridgeError(`note ${index} velocity ${velocity} is outside 1..127`);
    return { pitch, startTime: start, duration, velocity };
  });
}

export async function captureState(live: LiveLike, includeDevices = true): Promise<BridgeResult> {
  const tracks = [];
  for (const [index, track] of live.tracks.entries()) {
    tracks.push({
      index,
      name: track.name,
      has_midi_input: track.isMidi,
      mute: track.mute,
      solo: track.solo,
      arm: track.arm,
      is_selected: false,
      volume: await paramSnapshot(track.mixer.volume),
      panning: await paramSnapshot(track.mixer.panning),
      devices: includeDevices
        ? track.devices.map((device) => ({ name: device.name, class_name: device.className, parameter_count: device.parameters.length }))
        : [],
    });
  }
  return {
    schema_version: SCHEMA_VERSION,
    surface_version: EXTENSION_SURFACE_VERSION,
    capabilities: CAPABILITIES,
    tempo: live.tempo,
    // Not exposed by the SDK; null rather than a guess.
    is_playing: null,
    current_song_time: null,
    signature_numerator: null,
    signature_denominator: null,
    selected_track: null,
    root_note: PITCH_NAMES[((live.rootNote % 12) + 12) % 12],
    scale_name: live.scaleName,
    track_count: tracks.length,
    tracks,
    cue_points: live.cuePoints.map((cue) => ({ name: cue.name, time: cue.time })),
    captured_at: Date.now() / 1000,
  };
}

async function opSetTempo(live: LiveLike, payload: BridgeRequest) {
  const bpm = num(payload.bpm, "bpm");
  if (bpm < 20 || bpm > 999) throw new BridgeError(`tempo ${bpm} is outside Live's range [20, 999]`);
  const before = live.tempo;
  live.tempo = bpm;
  return { before, after: live.tempo };
}

async function opSetMixer(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  const changes: Record<string, unknown> = {};
  if (payload.volume !== undefined) changes.volume = await setParam(track.mixer.volume, num(payload.volume, "volume"));
  if (payload.pan !== undefined) changes.pan = await setParam(track.mixer.panning, num(payload.pan, "pan"));
  if (payload.mute !== undefined) {
    const before = track.mute;
    track.mute = Boolean(payload.mute);
    changes.mute = { before, after: track.mute };
  }
  if (payload.solo !== undefined) {
    const before = track.solo;
    track.solo = Boolean(payload.solo);
    changes.solo = { before, after: track.solo };
  }
  if (Object.keys(changes).length === 0) throw new BridgeError("set_mixer needs volume, pan, mute or solo");
  return { track: track.name, changes };
}

async function opSetDeviceParameter(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  const device = findDevice(track, payload.device);
  const matches = device.parameters.filter((param) => param.name === payload.parameter);
  if (matches.length !== 1) throw new BridgeError(`expected exactly one parameter named ${JSON.stringify(payload.parameter)} on ${JSON.stringify(device.name)}, found ${matches.length}`);
  const change = await setParam(matches[0], num(payload.value, "value"));
  return { track: track.name, device: device.name, parameter: matches[0].name, ...change, min: matches[0].min, max: matches[0].max };
}

async function opListDeviceParameters(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  const device = findDevice(track, payload.device);
  const parameters = [];
  for (const param of device.parameters) parameters.push(await paramSnapshot(param));
  return { track: track.name, device: device.name, class_name: device.className, parameters };
}

async function opCreateLocator(live: LiveLike, payload: BridgeRequest) {
  const beat = num(payload.beat, "beat");
  if (beat < 0) throw new BridgeError("beat must be >= 0");
  const existing = live.cuePoints.find((cue) => Math.abs(cue.time - beat) < 1e-6);
  if (existing) {
    const before = existing.name;
    if (payload.name) existing.name = String(payload.name);
    return { created: false, adopted: true, beat, name_before: before, name: existing.name, verified: true };
  }
  const cue = await live.createCuePoint(beat);
  if (payload.name) cue.name = String(payload.name);
  const verified = live.cuePoints.some((c) => Math.abs(c.time - beat) < 1e-6);
  return { created: true, adopted: false, beat, name: cue.name, verified };
}

async function opWriteArrangementClip(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  if (!track.isMidi) throw new BridgeError(`track ${JSON.stringify(track.name)} is not a MIDI track`);
  const start = num(payload.start_beat, "start_beat");
  const length = num(payload.length_beats, "length_beats");
  if (start < 0 || length <= 0) throw new BridgeError("start_beat must be >= 0 and length_beats > 0");
  const notes = toNoteDescriptions(payload.notes);
  const outside = notes.filter((note) => note.startTime >= length);
  if (outside.length) throw new BridgeError(`${outside.length} notes start at or after the clip end (${length} beats)`);
  const name = payload.name ? String(payload.name) : "Loom";
  // Idempotent: a rebuild of the same range replaces this side's own clip.
  const clip = await live.withinTransaction(async () => {
    await track.clearClipsInRange(start, start + length);
    const created = await track.createMidiClip(start, length);
    created.notes = notes;
    created.name = name;
    return created;
  });
  return {
    track: track.name,
    clip_name: clip.name,
    start_beat: start,
    length_beats: length,
    note_count: notes.length,
    verified_note_count: clip.notes.length,
    note_api: "sdk",
  };
}

async function opWriteClip(live: LiveLike, payload: BridgeRequest) {
  const track = payload.track ? findTrack(live, payload.track) : live.tracks.find((t) => t.isMidi);
  if (!track) throw new BridgeError("no MIDI track to write into");
  if (!track.isMidi) throw new BridgeError(`track ${JSON.stringify(track.name)} is not a MIDI track`);
  const length = payload.length_beats === undefined ? 32 : num(payload.length_beats, "length_beats");
  const notes = toNoteDescriptions(payload.notes);
  let slotIndex: number;
  if (payload.slot !== undefined) {
    slotIndex = num(payload.slot, "slot");
  } else {
    slotIndex = track.clipSlots.findIndex((slot) => slot.clip === null);
    if (slotIndex < 0) throw new BridgeError(`no empty clip slot on ${JSON.stringify(track.name)}`);
  }
  const slot = track.clipSlots[slotIndex];
  if (!slot) throw new BridgeError(`track ${JSON.stringify(track.name)} has no clip slot ${slotIndex}`);
  const clip = slot.clip ?? (await slot.createMidiClip(length));
  clip.notes = notes;
  if (payload.name) clip.name = String(payload.name);
  return { track: track.name, slot: slotIndex, clip_name: clip.name, length_beats: length, note_count: clip.notes.length, note_api: "sdk" };
}

async function opCreateMidiTrack(live: LiveLike, payload: BridgeRequest) {
  const name = String(payload.name ?? "").trim();
  if (!name) throw new BridgeError("track name is required");
  const sameName = live.tracks.filter((track) => track.name === name);
  if (sameName.length > 1) throw new BridgeError(`expected at most one track named ${JSON.stringify(name)}, found ${sameName.length}`);
  if (sameName.length === 1 && !sameName[0].isMidi) throw new BridgeError(`a non-MIDI track is already named ${JSON.stringify(name)}`);
  let track: TrackLike;
  let created: boolean;
  if (sameName.length === 1) {
    track = sameName[0];
    created = false;
  } else {
    track = await live.createMidiTrack();
    track.name = name;
    created = true;
  }
  const family = String(payload.instrument_family ?? "").trim();
  let instrument = "skipped";
  if (family && !created) {
    instrument = "kept: track already existed";
  } else if (family) {
    // The SDK only inserts native devices with their default preset. A
    // preset name ("Boom Bap Kit") is not loadable here; say so.
    try {
      const device = await track.insertDevice(family, 0);
      instrument = `inserted: ${device.name}`;
    } catch (error) {
      instrument = `not_loadable_in_extension: ${family} (${error instanceof Error ? error.message : String(error)})`;
    }
  }
  // Wrappers are fresh objects per access, so locate the track by name.
  return { created, adopted: !created, name: track.name, index: live.tracks.findIndex((t) => t.name === track.name), instrument };
}

const UNSUPPORTED: Record<string, string> = {
  transport: "the Extensions SDK exposes no transport (play/stop/position)",
  set_key: "the Extensions SDK exposes rootNote/scaleName read-only",
};

export async function applyOperation(live: LiveLike, payload: BridgeRequest): Promise<BridgeResult> {
  const op = payload.op ?? "write_clip";
  if (op in UNSUPPORTED) throw new BridgeError(`unsupported_in_extension: ${op} -- ${UNSUPPORTED[op]}; use the Loom control surface`);
  switch (op) {
    case "get_state":
      return captureState(live, payload.include_devices === undefined ? true : Boolean(payload.include_devices));
    case "set_tempo":
      return opSetTempo(live, payload);
    case "set_mixer":
      return opSetMixer(live, payload);
    case "set_device_parameter":
      return opSetDeviceParameter(live, payload);
    case "list_device_parameters":
      return opListDeviceParameters(live, payload);
    case "create_locator":
      return opCreateLocator(live, payload);
    case "write_arrangement_clip":
      return opWriteArrangementClip(live, payload);
    case "write_clip":
      return opWriteClip(live, payload);
    case "create_midi_track":
      return opCreateMidiTrack(live, payload);
    default:
      throw new BridgeError(`unknown op ${JSON.stringify(op)}`);
  }
}

// ---------------------------------------------------------------------------
// Files: the queue on disk.

export type BridgeDirs = { root: string; requests: string; done: string; errors: string; state: string; stateFile: string };

export function bridgeDirs(root: string): BridgeDirs {
  return {
    root,
    requests: join(root, "requests"),
    done: join(root, "done"),
    errors: join(root, "errors"),
    state: join(root, "state"),
    stateFile: join(root, "state", "live_state.json"),
  };
}

export function ensureBridgeDirs(dirs: BridgeDirs) {
  for (const dir of [dirs.requests, dirs.done, dirs.errors, dirs.state]) mkdirSync(dir, { recursive: true });
}

function writeAtomic(path: string, body: string) {
  const temporary = `${path}.tmp`;
  writeFileSync(temporary, body, "utf8");
  renameSync(temporary, path);
}

export function pendingRequests(dirs: BridgeDirs): string[] {
  if (!existsSync(dirs.requests)) return [];
  return readdirSync(dirs.requests)
    .filter((name) => name.endsWith(".json"))
    .sort();
}

// Processes exactly one request file: reads it, applies it, writes the
// outcome into done/ or errors/ and removes the request. Returns what was
// written so a caller (or a test) can look at it without re-reading.
export async function processRequestFile(live: LiveLike, dirs: BridgeDirs, fileName: string, log: (line: string) => void = () => {}) {
  const record = await processRequestFileOnly(live, dirs, fileName, log);
  // Every answered request republishes the state file, so a caller that reads
  // live_state.json right after a write sees the write, not the last timer tick.
  try {
    await publishState(live, dirs);
  } catch (error) {
    log(`Loom bridge state after request failed: ${error instanceof Error ? error.message : String(error)}`);
  }
  return record;
}

async function processRequestFileOnly(live: LiveLike, dirs: BridgeDirs, fileName: string, log: (line: string) => void) {
  const requestPath = join(dirs.requests, fileName);
  let payload: BridgeRequest = {};
  let record: Record<string, unknown>;
  let destination: string;
  try {
    payload = JSON.parse(readFileSync(requestPath, "utf8")) as BridgeRequest;
  } catch (error) {
    record = { status: "error", error: `unreadable request: ${error instanceof Error ? error.message : String(error)}` };
    destination = dirs.errors;
    finish(requestPath, join(destination, fileName), record);
    return record;
  }
  try {
    const result = await applyOperation(live, payload);
    record = { ...payload, completed_at: Date.now() / 1000, schema_version: SCHEMA_VERSION, surface_version: EXTENSION_SURFACE_VERSION, status: "ok", result };
    destination = dirs.done;
    log(`Loom ok: ${payload.op ?? "write_clip"}`);
  } catch (error) {
    // Not constructor.name: the bundle minifies class names to one letter.
    const name = error instanceof BridgeError ? "BridgeError" : error instanceof Error ? error.name || "Error" : "Error";
    const message = error instanceof Error ? error.message : String(error);
    record = { ...payload, completed_at: Date.now() / 1000, schema_version: SCHEMA_VERSION, surface_version: EXTENSION_SURFACE_VERSION, status: "error", error: `${name}: ${message}` };
    destination = dirs.errors;
    log(`Loom error: ${name}: ${message}`);
  }
  finish(requestPath, join(destination, fileName), record);
  return record;
}

function finish(requestPath: string, outcomePath: string, record: Record<string, unknown>) {
  writeFileSync(outcomePath, JSON.stringify(record, null, 2), "utf8");
  try {
    unlinkSync(requestPath);
  } catch {
    // Already gone; the outcome file is what the caller reads.
  }
}

export async function publishState(live: LiveLike, dirs: BridgeDirs) {
  const state = await captureState(live, true);
  writeAtomic(dirs.stateFile, JSON.stringify(state, null, 2));
  return state;
}

// The poller: one request per tick, oldest first, never two at once; state
// republished on its own cadence. Both timers are unref'd so they never keep
// the host alive on their own.
export type BridgeHandle = { stop(): void; dirs: BridgeDirs };

export function startBridge(
  live: LiveLike,
  root: string,
  log: (line: string) => void,
  intervals: { requestMs?: number; stateMs?: number } = {},
): BridgeHandle {
  const dirs = bridgeDirs(root);
  ensureBridgeDirs(dirs);
  let processing = false;
  let publishing = false;
  const tick = async () => {
    if (processing) return;
    processing = true;
    try {
      const [next] = pendingRequests(dirs);
      if (next) await processRequestFile(live, dirs, next, log);
    } catch (error) {
      log(`Loom bridge tick failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      processing = false;
    }
  };
  const publish = async () => {
    // Skip a timer tick while a request is being answered: that request
    // republishes on completion anyway, and two writers would race the file.
    if (publishing || processing) return;
    publishing = true;
    try {
      await publishState(live, dirs);
    } catch (error) {
      log(`Loom bridge state failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      publishing = false;
    }
  };
  const requestTimer = setInterval(() => void tick(), intervals.requestMs ?? 250);
  const stateTimer = setInterval(() => void publish(), intervals.stateMs ?? 1000);
  requestTimer.unref?.();
  stateTimer.unref?.();
  void publish();
  log(`Loom bridge listening at ${dirs.root} (${EXTENSION_SURFACE_VERSION})`);
  return {
    dirs,
    stop() {
      clearInterval(requestTimer);
      clearInterval(stateTimer);
    },
  };
}
