// The Loom bridge, inside the Loom extension (id subverselab.loom): the ONLY Live-side endpoint the
// Loom MCP talks to.
//
// File contract (mcp_server/bridge_client.py is the other half):
//   <root>/requests/<id>.json    -> written by the MCP, processed oldest first
//   <root>/processing/<id>.json  -> the claim: an atomic rename out of requests/
//   <root>/done/<id>.json        -> the request plus {status:"ok", result, outcome}
//   <root>/errors/<id>.json      -> the request plus {status:"error"|"indeterminate", error, outcome}
//   <root>/state/live_state.json -> rewritten on a timer and after every answer
//   <root>/state/journal.jsonl   -> the replay journal (one line per attempt)
//   <root>/state/owned_clips.json-> the ownership ledger
//   <root>/journal.marker        -> proves a journal once existed at this root
// A hosted extension may only touch its storageDirectory and tempDirectory
// (Node's --allow-fs-* permissions, GAP-008), so the root lives under
// storage: <storageDirectory>/bridge.
//
// This file imports nothing from the runtime SDK. Live arrives through the
// structural interfaces below, which extension.ts satisfies with thin
// wrappers around the real objects and the tests satisfy with fakes. What
// the SDK cannot do is refused with a named reason, never approximated:
// transport, meters, time signature, key *write*, preset loading
// (insertDevice only knows native devices with their default preset).
import type { NoteDescription } from "@ableton-extensions/sdk";
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// --- versions: three different things, kept apart on purpose ---------------
// 1) The record schema the two sides read and write.
export const SCHEMA_VERSION = "sensei.bridge.v2";
// 2) The queue protocol: claim, expiry, target_session, journal, structured
//    outcome. The MCP refuses to send a MUTATION to a bridge that does not
//    publish exactly this protocol (reads and diagnosis still work).
export const BRIDGE_PROTOCOL = "loom.bridge/3";
export const PROTOCOL_FEATURES = ["claim", "expiry", "target_session", "journal", "structured_outcome"] as const;
// 3) The package version, injected by build.ts from manifest.json (the one
//    place it is written). Under tsx (tests) it is the dev placeholder.
declare const __LOOM_EXTENSION_VERSION__: string | undefined;
export const EXTENSION_VERSION = typeof __LOOM_EXTENSION_VERSION__ === "string" ? __LOOM_EXTENSION_VERSION__ : "0.0.0-dev";
export const EXTENSION_SURFACE_VERSION = `loom-extension/${EXTENSION_VERSION}`;
// 4) The SDK API level the extension initialises against (manifest minimumApiVersion).
export const SDK_API_VERSION = "1.0.0";

// One id per activation. Requests carry the session they were issued for, so
// a request written for an earlier Live is refused instead of applied.
export function newSessionId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
export const MAX_CLIP_NOTES = 4096;

// --- replay journal retention (see readJournal) ------------------------------
// Entries of the CURRENT session are never dropped. Finished entries (ok /
// error) of EARLIER sessions are dropped after JOURNAL_RETENTION_SECONDS, and
// at most JOURNAL_MAX_FOREIGN of them are kept. Entries whose outcome is
// unknown (started / indeterminate) are never dropped by age: they are the
// only evidence that a mutation may have happened.
export const JOURNAL_RETENTION_SECONDS = 30 * 24 * 3600;
export const JOURNAL_MAX_FOREIGN = 5000;
// The file is rewritten (compacted) once it holds more lines than this.
export const JOURNAL_COMPACT_LINES = 2000;

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
  track_delete: true,
  locators: true,
  locator_delete: true,
  mixer: true,
  device_parameters: true,
  audio_import: true,
  render_pre_fx: true,
  tempo: true,
  drum_pads: true,
  // A kit assembled from sample files: one chain + Simpler per pad. Presets
  // (.adg) still cannot be loaded; this is the SDK's own way to a kit.
  drum_kit_build: true,
  // Foreign journal entries (an earlier extension id's journal) can be
  // carried over so replay refusals survive the identity change.
  journal_import: true,
  recording: false,
} as const;

// Operations that change nothing in Live. They are never journaled and the
// MCP may send them to a bridge whose protocol it would refuse to mutate.
export const READ_OPS = new Set(["get_state", "list_device_parameters", "drum_pads"]);

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
  // Simpler only: load an audio file (Live reads it, so the path may lie
  // outside the extension's storage). Resolves to the loaded file's path.
  replaceSample?(filePath: string): Promise<string>;
}
export interface DrumChainLike {
  receivingNote: number;
  readonly devices: DeviceLike[];
  insertDevice(deviceName: string, index: number): Promise<DeviceLike>;
}
export interface DrumRackLike {
  readonly name: string;
  readonly padNotes: number[];
  readonly chains: DrumChainLike[];
  insertChain(index: number): Promise<DrumChainLike>;
}
export interface BridgeClipLike {
  notes: NoteDescription[];
  name: string;
  readonly startTime?: number;
  readonly endTime?: number;
  // The SDK object's Handle.id, valid for this Live session only. It is how
  // the ledger tells "the clip Loom wrote" from "a clip that now sits there".
  readonly handleId?: string;
  // Length in beats of a session clip, when the SDK exposes it.
  readonly lengthBeats?: number;
}
export interface AudioClipLike {
  readonly filePath: string;
  name: string;
}
export interface SlotLike {
  // A slot may hold a MIDI clip (notes) or an audio clip (filePath).
  readonly clip: BridgeClipLike | AudioClipLike | null;
  createMidiClip(length: number): Promise<BridgeClipLike>;
  deleteClip(): Promise<void>;
  createAudioClip(filePath: string, isWarped?: boolean): Promise<AudioClipLike>;
}
export interface ArrangementClipLike {
  readonly name: string;
  readonly startTime: number;
  readonly endTime: number;
  readonly handleId?: string;
}
export interface TrackLike {
  name: string;
  mute: boolean;
  solo: boolean;
  arm: boolean;
  readonly isMidi: boolean;
  readonly isAudio: boolean;
  readonly arrangementClips: ArrangementClipLike[];
  deleteArrangementClip(clip: ArrangementClipLike): Promise<void>;
  // The note-capable object behind an arrangement clip, or null if it is not
  // a MIDI clip. Replacing in place needs the object, not its coordinates.
  arrangementMidiClip(clip: ArrangementClipLike): BridgeClipLike | null;
  // Every Drum Rack on the track with the pad notes its chains receive: the
  // only pad evidence the SDK gives, read from the device, never assumed.
  readonly drumRacks: DrumRackLike[];
  createAudioClipInArrangement(startTime: number, filePath: string, duration?: number, isWarped?: boolean): Promise<AudioClipLike>;
  readonly devices: DeviceLike[];
  readonly mixer: { volume: ParamLike; panning: ParamLike };
  readonly clipSlots: SlotLike[];
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
  // Song.deleteTrack / Song.deleteCuePoint (SDK 1.0.0). Wrappers are fresh
  // objects per access, so the target is named by position, not identity;
  // the op verifies name-at-index before asking.
  deleteTrackAt(index: number): Promise<void>;
  deleteCuePointAt(time: number): Promise<void>;
  withinTransaction<T>(fn: () => T): T;
  // Live copies the file into the project and returns the managed copy's path.
  importIntoProject(filePath: string): Promise<string>;
  // Pre-effects render of an audio track's arrangement range, in beats; the
  // file lands in the extension's temp directory.
  renderPreFxAudio(trackName: string, startTime: number, endTime: number): Promise<string>;
}

export type BridgeRequest = { op?: string; id?: string; [key: string]: unknown };
export type BridgeResult = Record<string, unknown>;

// --- the structured outcome ---------------------------------------------------
// Every answer carries one. `kind` is what the caller must act on; `applied`
// and `verified` are the two facts behind it (null = unknown). A refusal never
// touched Live; a failure touched it and put it back; an indeterminate answer
// may have changed it and says what to look for.
export type OutcomeKind = "applied" | "refused" | "failed" | "indeterminate";
export type Outcome = {
  kind: OutcomeKind;
  code: string;
  applied: boolean | null;
  verified: boolean | null;
  side_effects?: string;
  next_step?: string;
  warning?: string;
  earlier_request_id?: string;
  earlier_session?: string;
};

export class BridgeError extends Error {
  readonly outcome: Partial<Outcome>;
  constructor(message: string, outcome: Partial<Outcome> = {}) {
    super(message);
    this.outcome = outcome;
  }
}

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

// --- ownership --------------------------------------------------------------
// A name is not ownership. Only a clip whose track, name and range match an
// entry this bridge wrote may be replaced, and only on request.
export type LedgerEntry = {
  kind: "arrangement" | "session";
  track: string;
  name: string;
  start: number;
  end: number;
  // The Live session (extension activation) the clip was written in. The SDK
  // gives clips no identity that survives a restart, so ownership never does.
  session?: string;
  // Handle.id of the clip object at write time, when the SDK provided one.
  handle?: string | null;
  // Fingerprint of name + notes as read back from Live right after the write.
  fingerprint?: string;
};

export class LedgerError extends BridgeError {}

function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

// Sorted, rounded, name-inclusive: the same content gives the same string
// whatever order Live hands the notes back in.
export function contentFingerprint(name: string, notes: NoteDescription[]): string {
  const keys = notes
    .map((n) => `${n.pitch}@${n.startTime.toFixed(4)}x${n.duration.toFixed(4)}v${n.velocity ?? 100}m${n.muted ? 1 : 0}`)
    .sort();
  return fnv1a(`${name}\u0000${keys.join("|")}`);
}

export type OwnershipClaim = { name: string; start: number; end: number; handleId?: string; notes?: NoteDescription[] };

export class ClipLedger {
  entries: LedgerEntry[] = [];
  readonly unreadable: boolean = false;
  constructor(readonly path: string | null, readonly sessionId: string) {
    if (path && existsSync(path)) {
      try {
        const data = JSON.parse(readFileSync(path, "utf8")) as { clips?: LedgerEntry[] };
        this.entries = Array.isArray(data.clips) ? data.clips : [];
      } catch {
        // A ledger that exists but cannot be read proves nothing: every
        // ownership question is answered "cannot tell", never "yours".
        this.entries = [];
        this.unreadable = true;
      }
    }
  }
  private static key(kind: LedgerEntry["kind"], track: string, name: string, start: number, end: number): LedgerEntry {
    return { kind, track, name, start: Math.round(start * 10000) / 10000, end: Math.round(end * 10000) / 10000 };
  }
  private static same(a: LedgerEntry, b: LedgerEntry) {
    return a.kind === b.kind && a.track === b.track && a.name === b.name && a.start === b.start && a.end === b.end;
  }
  // Why the clip described by `clip` is NOT Loom's to replace, or null when it is.
  ownership(kind: LedgerEntry["kind"], track: string, clip: OwnershipClaim): string | null {
    if (this.unreadable) return "cannot be proven Loom's: the ownership ledger on disk is unreadable";
    const wanted = ClipLedger.key(kind, track, clip.name, clip.start, clip.end);
    const entry = this.entries.find((existing) => ClipLedger.same(existing, wanted));
    if (!entry) return "was not written by Loom (no ledger entry)";
    if (entry.session !== this.sessionId) {
      return "was written by Loom in an earlier Live session; the SDK gives clips no persistent identity, so ownership cannot be proven across sessions";
    }
    if (entry.handle && clip.handleId && entry.handle !== clip.handleId) {
      return "is not the clip Loom wrote there (that one was deleted; this is a different clip object)";
    }
    if (entry.fingerprint !== undefined && clip.notes !== undefined && contentFingerprint(clip.name, clip.notes) !== entry.fingerprint) {
      return "was changed after Loom wrote it";
    }
    return null;
  }
  owns(kind: LedgerEntry["kind"], track: string, name: string, start: number, end: number) {
    return this.ownership(kind, track, { name, start, end }) === null;
  }
  record(kind: LedgerEntry["kind"], track: string, name: string, start: number, end: number, identity: { handle?: string | null; fingerprint?: string } = {}) {
    const entry: LedgerEntry = { ...ClipLedger.key(kind, track, name, start, end), session: this.sessionId, handle: identity.handle ?? null, ...(identity.fingerprint === undefined ? {} : { fingerprint: identity.fingerprint }) };
    this.entries = this.entries.filter((existing) => !ClipLedger.same(existing, entry));
    this.entries.push(entry);
    this.save();
  }
  forget(kind: LedgerEntry["kind"], track: string, name: string, start: number, end: number) {
    const entry = ClipLedger.key(kind, track, name, start, end);
    this.entries = this.entries.filter((existing) => !ClipLedger.same(existing, entry));
    this.save();
  }
  // Proves the ledger can be written BEFORE a mutation depends on recording it.
  probe() {
    this.save();
  }
  private save() {
    if (!this.path) return;
    if (this.unreadable) throw new LedgerError(`ownership ledger ${this.path} is unreadable; refusing to write over it`, { code: "ledger_unwritable" });
    try {
      mkdirSync(join(this.path, ".."), { recursive: true });
      writeAtomic(this.path, JSON.stringify({ schema_version: SCHEMA_VERSION, clips: this.entries.slice(-2000) }, null, 1));
    } catch (error) {
      throw new LedgerError(`ownership ledger ${this.path} could not be written: ${error instanceof Error ? error.message : String(error)}`, { code: "ledger_unwritable" });
    }
  }
}

function conflictPolicy(payload: BridgeRequest): "refuse" | "replace_owned" {
  const policy = payload.on_conflict ?? "refuse";
  if (policy !== "refuse" && policy !== "replace_owned") throw new BridgeError(`on_conflict must be 'refuse' or 'replace_owned', got ${JSON.stringify(policy)}`);
  return policy;
}

function toNoteDescriptions(raw: unknown, clipEnd?: number): NoteDescription[] {
  if (!Array.isArray(raw)) throw new BridgeError("notes must be a list");
  if (raw.length > MAX_CLIP_NOTES) throw new BridgeError(`too many notes: ${raw.length} > ${MAX_CLIP_NOTES}`);
  return raw.map((note, index) => {
    if (typeof note !== "object" || note === null) throw new BridgeError(`note ${index} is not an object`);
    const n = note as Record<string, unknown>;
    const pitch = num(n.pitch, `note ${index} pitch`);
    const start = num(n.start ?? n.time ?? n.startTime, `note ${index} start`);
    const duration = num(n.duration, `note ${index} duration`);
    const velocity = n.velocity === undefined ? 100 : num(n.velocity, `note ${index} velocity`);
    if (pitch < 0 || pitch > 127 || !Number.isInteger(pitch)) throw new BridgeError(`note ${index} pitch ${pitch} is not a MIDI pitch (0..127)`);
    if (start < 0) throw new BridgeError(`note ${index} start ${start} is negative`);
    if (duration <= 0) throw new BridgeError(`note ${index} duration ${duration} must be > 0`);
    if (clipEnd !== undefined && start >= clipEnd) throw new BridgeError(`note ${index} starts at ${start}, at or after the clip end (${clipEnd} beats)`);
    if (velocity < 1 || velocity > 127) throw new BridgeError(`note ${index} velocity ${velocity} is outside 1..127`);
    return { pitch, startTime: start, duration, velocity };
  });
}

export type StateExtras = { session_id: string; journal?: JournalSummary };

export async function captureState(live: LiveLike, extras: StateExtras, includeDevices = true): Promise<BridgeResult> {
  const tracks = [];
  for (const [index, track] of live.tracks.entries()) {
    tracks.push({
      index,
      name: track.name,
      has_midi_input: track.isMidi,
      is_audio: track.isAudio,
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
    bridge_protocol: BRIDGE_PROTOCOL,
    protocol_features: [...PROTOCOL_FEATURES],
    surface_version: EXTENSION_SURFACE_VERSION,
    extension_version: EXTENSION_VERSION,
    sdk_api_version: SDK_API_VERSION,
    session_id: extras.session_id,
    capabilities: CAPABILITIES,
    journal: extras.journal ?? null,
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

function describe(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

// Removing what an earlier build left behind: a track the OS preset path put
// a preset on by mistake (measured 2026-09-07: Live opened a "10-Riser Basic"
// track instead of loading onto the adopted one), or a cue a plan placed at
// the wrong bar. Both live in the user's set, so the op deletes only what the
// caller names exactly and what holds no clip. A track with a clip on it is
// someone's work: it is never deleted here, and there is no override.
async function opDeleteTrack(live: LiveLike, payload: BridgeRequest) {
  const name = String(payload.name ?? "").trim();
  if (!name) throw new BridgeError("track name is required");
  const matches = live.tracks.map((track, index) => ({ track, index })).filter(({ track }) => track.name === name);
  if (matches.length === 0) throw new BridgeError(`no track named ${JSON.stringify(name)}`, { code: "track_not_found" });
  if (matches.length > 1) throw new BridgeError(`expected exactly one track named ${JSON.stringify(name)}, found ${matches.length}`, { code: "ambiguous_track" });
  const { track, index } = matches[0];
  if (payload.index !== undefined && num(payload.index, "index") !== index) {
    throw new BridgeError(`track ${JSON.stringify(name)} is at index ${index}, not ${JSON.stringify(payload.index)}`, { code: "index_mismatch" });
  }
  const sessionClips = track.clipSlots.filter((slot) => slot.clip !== null).length;
  const arrangementClips = track.arrangementClips.length;
  if (sessionClips + arrangementClips > 0) {
    throw new BridgeError(`track ${JSON.stringify(name)} holds ${arrangementClips} arrangement and ${sessionClips} session clip(s); a track with clips is never deleted by the bridge`, { code: "track_has_clips" });
  }
  const devices = track.devices.map((device) => device.name);
  if (payload.expected_devices !== undefined) {
    const expected = Array.isArray(payload.expected_devices) ? payload.expected_devices.map((d) => String(d)) : null;
    if (!expected) throw new BridgeError("expected_devices must be a list of device names");
    if (JSON.stringify(expected) !== JSON.stringify(devices)) {
      throw new BridgeError(`track ${JSON.stringify(name)} holds devices ${JSON.stringify(devices)}, the caller expected ${JSON.stringify(expected)}`, { code: "devices_differ" });
    }
  } else if (devices.length > 0) {
    throw new BridgeError(`track ${JSON.stringify(name)} holds devices ${JSON.stringify(devices)}; pass expected_devices naming exactly these to delete it`, { code: "track_has_devices" });
  }
  const before = live.tracks.length;
  try {
    await live.deleteTrackAt(index);
  } catch (error) {
    const still = live.tracks.length === before && live.tracks[index]?.name === name;
    throw new BridgeError(`deleteTrack failed for ${JSON.stringify(name)}: ${describe(error)}`, still
      ? { kind: "failed", code: "delete_failed", applied: false, verified: true, side_effects: "nothing: the track is still there" }
      : { kind: "indeterminate", code: "delete_failed", applied: null, verified: false, side_effects: `the track list changed (${before} -> ${live.tracks.length}); look at the set`, next_step: "read live_state and compare the track list before retrying" });
  }
  const remaining = live.tracks.filter((t) => t.name === name).length;
  if (!(live.tracks.length === before - 1 && remaining === 0)) {
    throw new BridgeError(`deleteTrack returned but the set does not show it: ${live.tracks.length} tracks (was ${before}), ${remaining} still named ${JSON.stringify(name)}`,
      { kind: "indeterminate", code: "delete_unverified", applied: null, verified: false, side_effects: "unknown: the track list does not match a single deletion", next_step: "look at the track list in Live before retrying" });
  }
  return { deleted: true, name, index, devices, track_count_before: before, track_count_after: live.tracks.length, verified: true };
}

async function opDeleteLocator(live: LiveLike, payload: BridgeRequest) {
  const beat = num(payload.beat, "beat");
  const at = (cue: CueLike) => Math.abs(cue.time - beat) < 1e-6;
  const cue = live.cuePoints.find(at);
  if (!cue) throw new BridgeError(`no locator at beat ${beat}`, { code: "locator_not_found" });
  if (payload.name !== undefined && String(payload.name) !== cue.name) {
    throw new BridgeError(`the locator at beat ${beat} is named ${JSON.stringify(cue.name)}, not ${JSON.stringify(payload.name)}`, { code: "name_mismatch" });
  }
  const name = cue.name;
  const before = live.cuePoints.length;
  try {
    await live.deleteCuePointAt(beat);
  } catch (error) {
    const still = live.cuePoints.some(at);
    throw new BridgeError(`deleteCuePoint failed at beat ${beat}: ${describe(error)}`, still
      ? { kind: "failed", code: "delete_failed", applied: false, verified: true, side_effects: "nothing: the locator is still there" }
      : { kind: "indeterminate", code: "delete_failed", applied: null, verified: false, side_effects: "the locator is gone although Live reported an error", next_step: "read live_state before retrying" });
  }
  if (live.cuePoints.some(at) || live.cuePoints.length !== before - 1) {
    throw new BridgeError(`deleteCuePoint returned but the cue list does not show it: ${live.cuePoints.length} cues (was ${before})`,
      { kind: "indeterminate", code: "delete_unverified", applied: null, verified: false, side_effects: "unknown: the cue list does not match a single deletion", next_step: "look at the locators in Live before retrying" });
  }
  return { deleted: true, beat, name, cue_count_before: before, cue_count_after: live.cuePoints.length, verified: true };
}

// Every note field, not just pitch/start/duration: a restore must give the
// user back exactly what was there.
function notesIdentical(a: NoteDescription[], b: NoteDescription[]): boolean {
  if (a.length !== b.length) return false;
  const key = (n: NoteDescription) => JSON.stringify([n.pitch, n.startTime, n.duration, n.velocity ?? null, n.muted ?? null, n.probability ?? null, n.velocityDeviation ?? null, n.releaseVelocity ?? null]);
  const left = a.map(key).sort();
  const right = b.map(key).sort();
  return left.every((value, index) => value === right[index]);
}

const FAILED_RESTORED: Partial<Outcome> = { kind: "failed", applied: false, verified: true, side_effects: "nothing: the previous content was restored and verified" };

// Replace a clip's content without deleting the clip: the clip object -- its
// automation, colour, loop and launch settings, which the SDK cannot read or
// restore -- stays; only notes and name change. On any failure the previous
// notes and name are put back and verified; if THAT fails the answer is
// indeterminate and says what the clip holds now, because at that point the
// user has to look.
async function replaceContentInPlace(clip: BridgeClipLike, notes: NoteDescription[], name: string, where: string) {
  const before = { notes: clip.notes.map((note) => ({ ...note })), name: clip.name };
  const restoreAfter = (failure: string): never => {
    try {
      clip.notes = before.notes;
      clip.name = before.name;
    } catch (error) {
      throw new BridgeError(`restore_failed: ${failure}; restoring the previous content of ${where} ALSO failed (${describe(error)}) -- the clip now holds ${clip.notes.length} notes and needs to be recovered by hand`,
        { kind: "indeterminate", code: "restore_failed", applied: true, verified: false, side_effects: `${where} may hold new, old or mixed content (${clip.notes.length} notes now)`, next_step: "open the clip in Live and check its notes; do not retry until it is what you want" });
    }
    if (!notesIdentical(before.notes, clip.notes) || clip.name !== before.name) {
      throw new BridgeError(`restore_failed: ${failure}; the previous content of ${where} could not be restored intact (${clip.notes.length} of ${before.notes.length} notes) -- recover it by hand`,
        { kind: "indeterminate", code: "restore_failed", applied: true, verified: false, side_effects: `${where} holds ${clip.notes.length} notes, the previous clip had ${before.notes.length}`, next_step: "open the clip in Live and check its notes; do not retry until it is what you want" });
    }
    throw new BridgeError(`${failure}; the previous clip was restored intact`, FAILED_RESTORED);
  };
  try {
    clip.notes = notes;
  } catch (error) {
    restoreAfter(`write_notes failed at ${where}: ${describe(error)}`);
  }
  try {
    clip.name = name;
  } catch (error) {
    restoreAfter(`rename failed at ${where}: ${describe(error)}`);
  }
  if (!notesMatch(notes, clip.notes)) {
    restoreAfter(`verify failed at ${where}: wrote ${notes.length} notes but Live holds ${clip.notes.length}`);
  }
  return before;
}

// The clip is written and verified; only its ownership could not be recorded.
// That is an applied outcome with a warning, not an error: reporting it as a
// failure is exactly what makes a retry stack a second clip.
function ledgerWarning(where: string, error: unknown) {
  return `ledger_write_failed: ${where} was written and verified but its ownership could not be recorded (${describe(error)}); Loom will refuse to replace it until the ledger is writable`;
}

async function opWriteArrangementClip(live: LiveLike, payload: BridgeRequest, ledger: ClipLedger) {
  // Validate everything before Live is touched.
  const track = findTrack(live, payload.track);
  if (!track.isMidi) throw new BridgeError(`track ${JSON.stringify(track.name)} is not a MIDI track`);
  const start = num(payload.start_beat, "start_beat");
  const length = num(payload.length_beats, "length_beats");
  if (start < 0 || length <= 0) throw new BridgeError("start_beat must be >= 0 and length_beats > 0");
  const notes = toNoteDescriptions(payload.notes, length);
  const policy = conflictPolicy(payload);
  const name = payload.name ? String(payload.name) : "Loom";
  const end = start + length;
  const where = `beats ${start}-${end} on ${JSON.stringify(track.name)}`;

  // A clip that cannot be proven Loom's -- by session, object identity and
  // unchanged content -- is never touched; Loom's own is replaced only on request.
  const overlapping = track.arrangementClips.filter((clip) => clip.startTime < end && clip.endTime > start);
  const targets = overlapping.map((clip) => ({ clip, midi: track.arrangementMidiClip(clip) }));
  for (const { clip, midi } of targets) {
    const reason = ledger.ownership("arrangement", track.name, { name: clip.name, start: clip.startTime, end: clip.endTime, handleId: clip.handleId, notes: midi?.notes });
    if (reason) throw new BridgeError(`beats ${clip.startTime}-${clip.endTime} on ${JSON.stringify(track.name)} hold clip ${JSON.stringify(clip.name)} that ${reason}; refusing to touch it`, { code: "clip_protected" });
    if (policy !== "replace_owned") {
      throw new BridgeError(`beats ${clip.startTime}-${clip.endTime} on ${JSON.stringify(track.name)} hold Loom's own clip ${JSON.stringify(clip.name)}; pass on_conflict='replace_owned' to replace it`, { code: "owned_clip_needs_policy" });
    }
  }
  ledger.probe();

  if (targets.length > 0) {
    // Replace means: the same clip object, new content. Anything else would
    // delete a clip, and a deleted clip cannot be restored through the SDK
    // (no access to its automation, colour or launch settings) -- so it is refused.
    if (targets.length > 1) {
      throw new BridgeError(`${where} overlaps ${targets.length} of Loom's clips; replace_owned replaces exactly one clip in place and cannot restore deleted clips, so this is refused`, { code: "replace_would_delete" });
    }
    const { clip, midi } = targets[0];
    if (clip.startTime !== start || clip.endTime !== end) {
      throw new BridgeError(`Loom's clip ${JSON.stringify(clip.name)} spans beats ${clip.startTime}-${clip.endTime}, not ${start}-${end}; replacing it would mean deleting it, which the SDK cannot restore, so this is refused -- write the same range or a free one`, { code: "replace_would_delete" });
    }
    if (!midi) throw new BridgeError(`${where}: the clip there is not a MIDI clip; refused`);
    await replaceContentInPlace(midi, notes, name, where);
    let warning: string | undefined;
    try {
      ledger.record("arrangement", track.name, name, start, end, { handle: midi.handleId ?? clip.handleId ?? null, fingerprint: contentFingerprint(midi.name, midi.notes) });
    } catch (error) {
      warning = ledgerWarning(where, error);
    }
    return {
      track: track.name, clip_name: midi.name, start_beat: start, length_beats: length,
      note_count: notes.length, verified_note_count: midi.notes.length, verified_notes_match: notesMatch(notes, midi.notes),
      replaced: 1, replaced_in_place: true, on_conflict: policy, note_api: "sdk", ...(warning ? { warning } : {}),
    };
  }

  // A free range: create, fill, verify; each stage is named in its failure.
  let created: BridgeClipLike;
  try {
    created = await track.createMidiClip(start, length);
  } catch (error) {
    throw new BridgeError(`create failed at ${where}: ${describe(error)}; nothing was written`, { kind: "failed", applied: false, verified: true });
  }
  const removeCreated = async (failure: string): Promise<never> => {
    try {
      await track.deleteArrangementClip({ name: created.name, startTime: start, endTime: end, handleId: created.handleId });
    } catch (error) {
      throw new BridgeError(`rollback_failed: ${failure}; the new, incomplete clip at ${where} could not be removed (${describe(error)}) and is still there`,
        { kind: "indeterminate", code: "rollback_failed", applied: true, verified: false, side_effects: `an incomplete clip ${JSON.stringify(created.name)} remains at ${where}`, next_step: "delete or inspect that clip in Live before retrying" });
    }
    throw new BridgeError(`${failure}; the new clip was removed again`, { kind: "failed", applied: false, verified: true, side_effects: "nothing: the incomplete clip was removed" });
  };
  try {
    created.notes = notes;
    created.name = name;
  } catch (error) {
    await removeCreated(`write_notes failed at ${where}: ${describe(error)}`);
  }
  if (!notesMatch(notes, created.notes)) {
    await removeCreated(`verify failed at ${where}: wrote ${notes.length} notes but Live holds ${created.notes.length}`);
  }
  let warning: string | undefined;
  try {
    ledger.record("arrangement", track.name, name, start, end, { handle: created.handleId ?? null, fingerprint: contentFingerprint(created.name, created.notes) });
  } catch (error) {
    warning = ledgerWarning(where, error);
  }
  return {
    track: track.name,
    clip_name: created.name,
    start_beat: start,
    length_beats: length,
    note_count: notes.length,
    verified_note_count: created.notes.length,
    verified_notes_match: notesMatch(notes, created.notes),
    replaced: 0,
    on_conflict: policy,
    note_api: "sdk",
    ...(warning ? { warning } : {}),
  };
}

async function opWriteClip(live: LiveLike, payload: BridgeRequest, ledger: ClipLedger) {
  const track = payload.track ? findTrack(live, payload.track) : live.tracks.find((t) => t.isMidi);
  if (!track) throw new BridgeError("no MIDI track to write into");
  if (!track.isMidi) throw new BridgeError(`track ${JSON.stringify(track.name)} is not a MIDI track`);
  const length = payload.length_beats === undefined ? 32 : num(payload.length_beats, "length_beats");
  if (length <= 0) throw new BridgeError("length_beats must be > 0");
  const notes = toNoteDescriptions(payload.notes, length);
  const policy = conflictPolicy(payload);
  const name = payload.name ? String(payload.name) : "Loom Clip";
  let slotIndex: number;
  if (payload.slot !== undefined) {
    slotIndex = num(payload.slot, "slot");
  } else {
    slotIndex = track.clipSlots.findIndex((slot) => slot.clip === null);
    if (slotIndex < 0) throw new BridgeError(`no empty clip slot on ${JSON.stringify(track.name)}`);
  }
  const slot = track.clipSlots[slotIndex];
  if (!slot) throw new BridgeError(`track ${JSON.stringify(track.name)} has no clip slot ${slotIndex}`);
  const where = `clip slot ${slotIndex} on ${JSON.stringify(track.name)}`;
  const existing = slot.clip;
  if (existing !== null) {
    const midi = "notes" in existing ? (existing as BridgeClipLike) : null;
    const reason = ledger.ownership("session", track.name, { name: existing.name, start: slotIndex, end: slotIndex, handleId: (existing as BridgeClipLike).handleId, notes: midi?.notes });
    if (reason) throw new BridgeError(`${where} is occupied by clip ${JSON.stringify(existing.name)} that ${reason}; refusing to touch it`, { code: "clip_protected" });
    if (policy !== "replace_owned") {
      throw new BridgeError(`${where} holds Loom's own clip ${JSON.stringify(existing.name)}; pass on_conflict='replace_owned' to replace it`, { code: "owned_clip_needs_policy" });
    }
    if (!midi) throw new BridgeError(`${where} holds an audio clip; refused`);
    if (midi.lengthBeats !== undefined && Math.abs(midi.lengthBeats - length) > 1e-6) {
      throw new BridgeError(`Loom's clip in ${where} is ${midi.lengthBeats} beats long, not ${length}; resizing would mean deleting it, which the SDK cannot restore, so this is refused`, { code: "replace_would_delete" });
    }
    ledger.probe();
    await replaceContentInPlace(midi, notes, name, where);
    let warning: string | undefined;
    try {
      ledger.record("session", track.name, name, slotIndex, slotIndex, { handle: midi.handleId ?? null, fingerprint: contentFingerprint(midi.name, midi.notes) });
    } catch (error) {
      warning = ledgerWarning(where, error);
    }
    return { track: track.name, slot: slotIndex, clip_name: midi.name, length_beats: length, note_count: notes.length, verified_note_count: midi.notes.length, verified_notes_match: notesMatch(notes, midi.notes), replaced: true, replaced_in_place: true, on_conflict: policy, note_api: "sdk", ...(warning ? { warning } : {}) };
  }
  ledger.probe();
  let clip: BridgeClipLike;
  try {
    clip = await slot.createMidiClip(length);
  } catch (error) {
    throw new BridgeError(`create failed at ${where}: ${describe(error)}; nothing was written`, { kind: "failed", applied: false, verified: true });
  }
  const removeCreated = async (failure: string): Promise<never> => {
    try {
      await slot.deleteClip();
    } catch (error) {
      throw new BridgeError(`rollback_failed: ${failure}; the new, incomplete clip in ${where} could not be removed (${describe(error)}) and is still there`,
        { kind: "indeterminate", code: "rollback_failed", applied: true, verified: false, side_effects: `an incomplete clip remains in ${where}`, next_step: "delete or inspect that clip in Live before retrying" });
    }
    throw new BridgeError(`${failure}; the new clip was removed again`, { kind: "failed", applied: false, verified: true, side_effects: "nothing: the incomplete clip was removed" });
  };
  try {
    clip.notes = notes;
    clip.name = name;
  } catch (error) {
    await removeCreated(`write_notes failed at ${where}: ${describe(error)}`);
  }
  if (!notesMatch(notes, clip.notes)) {
    await removeCreated(`verify failed at ${where}: wrote ${notes.length} notes but Live holds ${clip.notes.length}`);
  }
  let warning: string | undefined;
  try {
    ledger.record("session", track.name, name, slotIndex, slotIndex, { handle: clip.handleId ?? null, fingerprint: contentFingerprint(clip.name, clip.notes) });
  } catch (error) {
    warning = ledgerWarning(where, error);
  }
  return { track: track.name, slot: slotIndex, clip_name: clip.name, length_beats: length, note_count: notes.length, verified_note_count: clip.notes.length, verified_notes_match: notesMatch(notes, clip.notes), replaced: false, on_conflict: policy, note_api: "sdk", ...(warning ? { warning } : {}) };
}

// Note-for-note comparison of what was asked and what Live holds now; a
// count alone cannot tell a shifted or re-pitched clip from the right one.
function notesMatch(written: NoteDescription[], held: NoteDescription[]): boolean {
  if (written.length !== held.length) return false;
  const key = (n: NoteDescription) => `${n.pitch}@${n.startTime.toFixed(3)}x${n.duration.toFixed(3)}`;
  const a = written.map(key).sort();
  const b = held.map(key).sort();
  return a.every((value, index) => value === b[index]);
}

async function opDrumPads(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  // Per chain: the pad's MIDI note (SDK receivingNote) and the device classes
  // on it, so the caller can tell a native DrumCell kit from a Simpler rebuild.
  const racks = track.drumRacks.map((rack) => ({
    device: rack.name,
    pad_notes: [...rack.padNotes].sort((a, b) => a - b),
    // className is the SDK's class ("Device" for anything it does not
    // specialise -- a DrumCell included, measured 2026-09-07); the name is
    // what Live shows ("Drum Sampler"). Both are reported.
    chains: rack.chains.map((chain) => ({ note: chain.receivingNote, devices: chain.devices.map((device) => device.className || device.name),
      device_names: chain.devices.map((device) => device.name) })),
  }));
  const padNotes = [...new Set(racks.flatMap((rack) => rack.pad_notes))].sort((a, b) => a - b);
  return { track: track.name, drum_racks: racks, pad_notes: padNotes, verified: padNotes.length > 0,
    reason: racks.length === 0 ? "no Drum Rack on this track" : padNotes.length === 0 ? "the Drum Rack has no chains with a receiving note" : null };
}

// A kit from files: for every pad, a new chain receiving that note with a
// Simpler holding the sample. Validated first (one Drum Rack, free pads,
// absolute paths); the SDK cannot delete a chain, so a pad that fails after
// its chain exists is reported as a partial kit, never as nothing happened.
async function opBuildDrumKit(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  if (!track.isMidi) throw new BridgeError(`track ${JSON.stringify(track.name)} is not a MIDI track`);
  const racks = track.drumRacks;
  if (racks.length !== 1) throw new BridgeError(`expected exactly one Drum Rack on ${JSON.stringify(track.name)}, found ${racks.length}`, { code: racks.length === 0 ? "no_drum_rack" : "ambiguous_drum_rack" });
  const rack = racks[0];
  if (!Array.isArray(payload.pads) || payload.pads.length === 0) throw new BridgeError("pads must be a non-empty list of {note, sample}");
  const pads = payload.pads.map((pad, index) => {
    if (typeof pad !== "object" || pad === null) throw new BridgeError(`pad ${index} is not an object`);
    const p = pad as Record<string, unknown>;
    const note = num(p.note, `pad ${index} note`);
    if (!Number.isInteger(note) || note < 0 || note > 127) throw new BridgeError(`pad ${index} note ${note} is not a MIDI note (0..127)`);
    const sample = String(p.sample ?? "").trim();
    if (!sample.startsWith("/")) throw new BridgeError(`pad ${index} sample must be an absolute path, got ${JSON.stringify(sample)}`);
    return { note, sample, name: p.name === undefined ? null : String(p.name) };
  });
  const seen = new Set<number>();
  for (const pad of pads) {
    if (seen.has(pad.note)) throw new BridgeError(`pad note ${pad.note} is listed twice`);
    seen.add(pad.note);
  }
  const occupied = pads.filter((pad) => rack.padNotes.includes(pad.note)).map((pad) => pad.note);
  if (occupied.length > 0) throw new BridgeError(`pads ${JSON.stringify(occupied)} already have a chain on ${JSON.stringify(rack.name)}; existing pads are never replaced`, { code: "pad_occupied" });

  const built: Array<Record<string, unknown>> = [];
  for (const pad of pads) {
    const where = `pad ${pad.note} on ${JSON.stringify(track.name)}`;
    const partial = (stage: string, error: unknown): never => {
      throw new BridgeError(`${stage} failed at ${where}: ${describe(error)}`,
        { kind: "indeterminate", code: "kit_partial", applied: true, verified: false,
          side_effects: `${built.length} of ${pads.length} pads were built (${built.map((b) => b.note).join(", ") || "none"}); the SDK cannot remove a chain, so what was built stays`,
          next_step: "inspect the affected chains and loaded samples in Live; occupied pads may be incomplete. Do not retry this operation automatically" });
    };
    let chain: DrumChainLike;
    try {
      chain = await rack.insertChain(rack.chains.length);
    } catch (error) {
      partial("insert_chain", error);
    }
    try {
      chain!.receivingNote = pad.note;
    } catch (error) {
      partial("set_receiving_note", error);
    }
    let simpler: DeviceLike;
    try {
      simpler = await chain!.insertDevice("Simpler", 0);
    } catch (error) {
      partial("insert_simpler", error);
    }
    if (!simpler!.replaceSample) partial("load_sample", new Error("the inserted device cannot load a sample"));
    let loaded: string;
    try {
      loaded = await simpler!.replaceSample!(pad.sample);
    } catch (error) {
      partial("load_sample", error);
    }
    if (loaded! !== pad.sample) partial("verify_sample", new Error(`requested ${pad.sample}, loaded ${loaded!}`));
    built.push({ note: pad.note, sample: pad.sample, loaded: loaded!, name: pad.name });
  }
  const padNotes = [...track.drumRacks[0].padNotes].sort((a, b) => a - b);
  const missing = pads.map((pad) => pad.note).filter((note) => !padNotes.includes(note));
  if (missing.length) throw new BridgeError(`kit pad readback mismatch: ${missing.join(", ")}`, {
    kind: "indeterminate", code: "kit_readback_mismatch", applied: true, verified: false,
    side_effects: `${built.length} chains were built; requested notes were not all read back`,
    next_step: "inspect the chains in Live; do not retry automatically",
  });
  return { track: track.name, drum_rack: rack.name, built, pad_notes: padNotes, verified: missing.length === 0, missing_after_build: missing };
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
  if (family && !created && track.devices.length > 0) {
    // The track is the user's, with devices on it: nothing is inserted.
    instrument = "kept: track already existed";
  } else if (family) {
    // A new track, or an adopted one whose chain is empty (an earlier build
    // created it for a preset this path cannot load and left it silent):
    // nothing is overwritten by inserting the device it was asked for.
    // The SDK only inserts native devices with their default preset. A
    // preset name ("Boom Bap Kit") is not loadable here; say so.
    try {
      const device = await track.insertDevice(family, 0);
      instrument = created ? `inserted: ${device.name}` : `inserted: ${device.name} (into the adopted track's empty chain)`;
    } catch (error) {
      instrument = `not_loadable_in_extension: ${family} (${error instanceof Error ? error.message : String(error)})`;
    }
  }
  // Wrappers are fresh objects per access, so locate the track by name.
  return { created, adopted: !created, name: track.name, index: live.tracks.findIndex((t) => t.name === track.name), instrument };
}

async function opImportAudioClip(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  if (!track.isAudio) throw new BridgeError(`track ${JSON.stringify(track.name)} is not an audio track`);
  const source = String(payload.path ?? "").trim();
  if (!source) throw new BridgeError("path is required");
  const warped = payload.warped === undefined ? true : Boolean(payload.warped);
  // The import is Live's own copy step: it is what keeps the clip valid after
  // the pack folder moves, and it is the permission question this op exists
  // to answer -- the file lives outside the extension's storage.
  let imported = source;
  if (payload.import !== false) {
    try {
      imported = await live.importIntoProject(source);
    } catch (error) {
      // Live's importIntoProject rejects with an undefined reason when the set
      // has no project folder yet (unsaved set). Name the stage so the caller
      // can tell that apart from a permission refusal.
      const reason = error instanceof Error ? error.message : String(error);
      throw new BridgeError(`import_into_project failed for ${source}: ${reason === "undefined" || reason === "" ? "Live gave no reason (is the set saved to a project folder?)" : reason}`,
        { kind: "failed", code: "import_into_project_failed", applied: false, verified: true });
    }
  }
  if (payload.start_beat !== undefined) {
    const start = num(payload.start_beat, "start_beat");
    if (start < 0) throw new BridgeError("start_beat must be >= 0");
    const duration = payload.duration_beats === undefined ? undefined : num(payload.duration_beats, "duration_beats");
    const clip = await track.createAudioClipInArrangement(start, imported, duration, warped).catch((error) => {
      throw new BridgeError(`create_audio_clip (arrangement) failed for ${imported}: ${error instanceof Error ? error.message : String(error)}`, { kind: "failed", applied: false, verified: null });
    });
    if (payload.name) clip.name = String(payload.name);
    return { track: track.name, placed: "arrangement", start_beat: start, duration_beats: duration ?? null,
             source_path: source, imported_path: imported, clip_file: clip.filePath, clip_name: clip.name, warped };
  }
  let slotIndex: number;
  if (payload.slot !== undefined) {
    slotIndex = num(payload.slot, "slot");
  } else {
    slotIndex = track.clipSlots.findIndex((slot) => slot.clip === null);
    if (slotIndex < 0) throw new BridgeError(`no empty clip slot on ${JSON.stringify(track.name)}`);
  }
  const slot = track.clipSlots[slotIndex];
  if (!slot) throw new BridgeError(`track ${JSON.stringify(track.name)} has no clip slot ${slotIndex}`);
  if (slot.clip !== null) throw new BridgeError(`clip slot ${slotIndex} on ${JSON.stringify(track.name)} is occupied`, { code: "clip_protected" });
  const clip = await slot.createAudioClip(imported, warped).catch((error) => {
    throw new BridgeError(`create_audio_clip (session slot ${slotIndex}) failed for ${imported}: ${error instanceof Error ? error.message : String(error)}`, { kind: "failed", applied: false, verified: null });
  });
  if (payload.name) clip.name = String(payload.name);
  return { track: track.name, placed: "session", slot: slotIndex, source_path: source, imported_path: imported,
           clip_file: clip.filePath, clip_name: clip.name, warped };
}

async function opRenderPreFx(live: LiveLike, payload: BridgeRequest) {
  const track = findTrack(live, payload.track);
  if (!track.isAudio) throw new BridgeError(`track ${JSON.stringify(track.name)} is not an audio track`);
  const start = num(payload.start_beat, "start_beat");
  const end = num(payload.end_beat, "end_beat");
  if (start < 0 || end <= start) throw new BridgeError("need 0 <= start_beat < end_beat");
  const path = await live.renderPreFxAudio(track.name, start, end);
  return { track: track.name, start_beat: start, end_beat: end, path, note: "pre-effects render in the extension's temp directory; measure it with mix_measure / mix_analyze" };
}

const UNSUPPORTED: Record<string, string> = {
  transport: "the Extensions SDK exposes no transport (play/stop/position)",
  set_key: "the Extensions SDK exposes rootNote/scaleName read-only",
};

export type ApplyContext = { sessionId: string; ledger?: ClipLedger; journal?: JournalSummary; dirs?: BridgeDirs };

// Carries the replay journal of an earlier extension id over: entries of
// OTHER sessions only (this session's history is its own), keys that are
// already here are left alone, and every imported line says where it came
// from. Nothing in Live changes; what changes is which retries get refused.
async function opJournalImport(payload: BridgeRequest, context: ApplyContext) {
  if (!context.dirs) throw new BridgeError("journal_import needs the bridge directories", { code: "no_journal_here" });
  if (!Array.isArray(payload.entries)) throw new BridgeError("entries must be a list of journal entries");
  if (payload.entries.length > 5000) throw new BridgeError(`too many entries: ${payload.entries.length} > 5000`);
  const journal = readJournal(context.dirs);
  if (journal.state === "unreadable") throw new BridgeError("the journal here is unreadable; nothing is imported over it", { code: "journal_unreadable" });
  const source = typeof payload.source === "string" ? payload.source : "unknown";
  let imported = 0, skippedExisting = 0, skippedOwn = 0, skippedInvalid = 0;
  for (const raw of payload.entries) {
    if (typeof raw !== "object" || raw === null) { skippedInvalid++; continue; }
    const entry = raw as Record<string, unknown>;
    const status = String(entry.status ?? "");
    if (typeof entry.key !== "string" || !entry.key || !["started", "ok", "error", "indeterminate"].includes(status)) { skippedInvalid++; continue; }
    const session = String(entry.session ?? "");
    if (session === context.sessionId) { skippedOwn++; continue; }
    if (journal.entries.has(entry.key)) { skippedExisting++; continue; }
    appendJournal(context.dirs, {
      key: entry.key, status: status as JournalStatus, request_id: entry.request_id === undefined ? undefined : String(entry.request_id),
      op: typeof entry.op === "string" ? entry.op : undefined, session: session || `imported:${source}`,
      payload_hash: String(entry.payload_hash ?? ""), at: Number(entry.at) || Date.now() / 1000,
      ...(entry.result !== undefined ? { result: entry.result as BridgeResult } : {}), ...(typeof entry.error === "string" ? { error: entry.error } : {}),
      ...(typeof entry.outcome === "object" && entry.outcome !== null ? { outcome: entry.outcome as Outcome } : {}),
      imported_from: source,
    } as JournalEntry);
    imported++;
  }
  return { imported, skipped_existing: skippedExisting, skipped_own_session: skippedOwn, skipped_invalid: skippedInvalid, source, journal: journalSummary(readJournal(context.dirs)) };
}

export async function applyOperation(live: LiveLike, payload: BridgeRequest, context: ApplyContext): Promise<BridgeResult> {
  const op = payload.op ?? "write_clip";
  const ledger = context.ledger ?? new ClipLedger(null, context.sessionId);
  if (op in UNSUPPORTED) throw new BridgeError(`unsupported_in_extension: ${op} -- ${UNSUPPORTED[op]}; not available through the Extensions SDK`, { code: "unsupported_in_extension" });
  switch (op) {
    case "get_state":
      return captureState(live, { session_id: context.sessionId, journal: context.journal }, payload.include_devices === undefined ? true : Boolean(payload.include_devices));
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
    case "delete_locator":
      return opDeleteLocator(live, payload);
    case "delete_track":
      return opDeleteTrack(live, payload);
    case "write_arrangement_clip":
      return opWriteArrangementClip(live, payload, ledger);
    case "write_clip":
      return opWriteClip(live, payload, ledger);
    case "create_midi_track":
      return opCreateMidiTrack(live, payload);
    case "import_audio_clip":
      return opImportAudioClip(live, payload);
    case "render_pre_fx":
      return opRenderPreFx(live, payload);
    case "drum_pads":
      return opDrumPads(live, payload);
    case "build_drum_kit":
      return opBuildDrumKit(live, payload);
    case "journal_import":
      return opJournalImport(payload, context);
    default:
      throw new BridgeError(`unknown op ${JSON.stringify(op)}`, { code: "unknown_op" });
  }
}

// ---------------------------------------------------------------------------
// Files: the queue on disk.

export type BridgeDirs = { root: string; requests: string; processing: string; done: string; errors: string; state: string; stateFile: string; journalFile: string; journalMarker: string; ledgerFile: string };

export function bridgeDirs(root: string): BridgeDirs {
  return {
    root,
    requests: join(root, "requests"),
    processing: join(root, "processing"),
    done: join(root, "done"),
    errors: join(root, "errors"),
    state: join(root, "state"),
    stateFile: join(root, "state", "live_state.json"),
    journalFile: join(root, "state", "journal.jsonl"),
    journalMarker: join(root, "journal.marker"),
    ledgerFile: join(root, "state", "owned_clips.json"),
  };
}

export function ensureBridgeDirs(dirs: BridgeDirs) {
  for (const dir of [dirs.requests, dirs.processing, dirs.done, dirs.errors, dirs.state]) mkdirSync(dir, { recursive: true });
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

// ---------------------------------------------------------------------------
// The replay journal.
//
// One line per attempt, appended: `started` BEFORE the mutation, then `ok`,
// `error` or `indeterminate` with the outcome (and the result body, so a
// retry can be answered even after done/ has been cleaned). The last line for
// a key is its state. A key found `started` or `indeterminate` is an attempt
// whose effect is unknown; it is never re-applied, whatever session it came
// from. A marker file beside the queue records that a journal once existed at
// this root: a journal that is missing while the marker exists has been lost,
// and keyed mutations are refused until the caller uses keys that cannot have
// been used before -- never by deleting anything.

export type JournalStatus = "started" | "ok" | "error" | "indeterminate";
export type JournalEntry = {
  key: string;
  status: JournalStatus;
  request_id?: string;
  op?: string;
  session: string;
  payload_hash: string;
  at: number;
  result?: BridgeResult;
  error?: string;
  outcome?: Outcome;
  imported_from?: string;
};
export type JournalState = "fresh" | "present" | "missing" | "unreadable";
export type Journal = {
  state: JournalState;
  entries: Map<string, JournalEntry>;
  lines: number;
  torn_tail: boolean;
  marker: { created_at: number } | null;
};
export type JournalSummary = { state: JournalState; entries: number; unknown_outcome: number; sessions: number; torn_tail: boolean; marker_created_at: number | null };

function readMarker(dirs: BridgeDirs): { created_at: number } | null {
  if (!existsSync(dirs.journalMarker)) return null;
  try {
    const parsed = JSON.parse(readFileSync(dirs.journalMarker, "utf8")) as { created_at?: number };
    return { created_at: Number(parsed.created_at) || 0 };
  } catch {
    return { created_at: 0 };
  }
}

export function readJournal(dirs: BridgeDirs): Journal {
  const marker = readMarker(dirs);
  const entries = new Map<string, JournalEntry>();
  if (!existsSync(dirs.journalFile)) {
    return { state: marker ? "missing" : "fresh", entries, lines: 0, torn_tail: false, marker };
  }
  let text: string;
  try {
    text = readFileSync(dirs.journalFile, "utf8");
  } catch {
    return { state: "unreadable", entries, lines: 0, torn_tail: false, marker };
  }
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  let torn = false;
  for (const [index, line] of lines.entries()) {
    try {
      const entry = JSON.parse(line) as JournalEntry;
      if (typeof entry.key !== "string" || typeof entry.status !== "string") throw new Error("shape");
      entries.set(entry.key, entry);
    } catch {
      // A torn LAST line is a write the host did not finish. The `started`
      // line is written and flushed before any mutation begins, so a torn
      // started-line means nothing ran; a torn outcome-line leaves the key
      // `started`, which is refused as indeterminate. Both are safe to skip.
      // A broken line anywhere else is real corruption: nothing is trusted.
      if (index === lines.length - 1) {
        torn = true;
        continue;
      }
      return { state: "unreadable", entries: new Map(), lines: lines.length, torn_tail: false, marker };
    }
  }
  return { state: "present", entries, lines: lines.length, torn_tail: torn, marker };
}

export function journalSummary(journal: Journal): JournalSummary {
  let unknown = 0;
  const sessions = new Set<string>();
  for (const entry of journal.entries.values()) {
    if (entry.status === "started" || entry.status === "indeterminate") unknown++;
    sessions.add(entry.session);
  }
  return { state: journal.state, entries: journal.entries.size, unknown_outcome: unknown, sessions: sessions.size, torn_tail: journal.torn_tail, marker_created_at: journal.marker?.created_at ?? null };
}

// Appends one line. The marker is written first, once. Throws when the line
// could not be written: a caller writing `started` must then refuse.
function appendJournal(dirs: BridgeDirs, entry: JournalEntry) {
  mkdirSync(dirs.state, { recursive: true });
  if (!existsSync(dirs.journalMarker)) {
    writeAtomic(dirs.journalMarker, JSON.stringify({ created_at: Date.now() / 1000, journal: "state/journal.jsonl", protocol: BRIDGE_PROTOCOL }));
  }
  appendFileSync(dirs.journalFile, `${JSON.stringify(entry)}\n`, "utf8");
}

// Rewrites the journal with the retention policy applied. Only called on a
// readable journal; a torn tail is dropped here.
export function compactJournal(dirs: BridgeDirs, journal: Journal, sessionId: string, now = Date.now() / 1000): number {
  const keep: JournalEntry[] = [];
  const foreignFinished: JournalEntry[] = [];
  for (const entry of journal.entries.values()) {
    if (entry.session === sessionId || entry.status === "started" || entry.status === "indeterminate") {
      keep.push(entry);
    } else if (now - entry.at <= JOURNAL_RETENTION_SECONDS) {
      foreignFinished.push(entry);
    }
  }
  foreignFinished.sort((a, b) => b.at - a.at);
  keep.push(...foreignFinished.slice(0, JOURNAL_MAX_FOREIGN));
  keep.sort((a, b) => a.at - b.at);
  writeAtomic(dirs.journalFile, keep.map((entry) => JSON.stringify(entry)).join("\n") + (keep.length ? "\n" : ""));
  return keep.length;
}

// The v2 journal (state/processed_requests.json, one JSON document) is
// carried over once so its keys are not forgotten by the upgrade.
export function migrateLegacyJournal(dirs: BridgeDirs, log: (line: string) => void = () => {}): number {
  const legacy = join(dirs.state, "processed_requests.json");
  if (!existsSync(legacy) || existsSync(dirs.journalFile)) return 0;
  let parsed: { entries?: Array<Record<string, unknown>> };
  try {
    parsed = JSON.parse(readFileSync(legacy, "utf8")) as { entries?: Array<Record<string, unknown>> };
  } catch {
    log(`Loom legacy journal ${legacy} is unreadable; left in place, not migrated`);
    return 0;
  }
  let count = 0;
  for (const raw of parsed.entries ?? []) {
    const record = (raw.record ?? {}) as Record<string, unknown>;
    const status = String(raw.status ?? (record.status === "ok" ? "ok" : "error")) as JournalStatus;
    appendJournal(dirs, {
      key: String(raw.key), status, request_id: raw.request_id === undefined ? undefined : String(raw.request_id), op: typeof record.op === "string" ? record.op : undefined,
      session: String(raw.session ?? ""), payload_hash: String(raw.payload_hash ?? ""), at: Number(raw.started_at) || 0,
      result: record.result as BridgeResult | undefined, error: typeof record.error === "string" ? record.error : undefined,
    });
    count++;
  }
  renameSync(legacy, `${legacy}.migrated`);
  log(`Loom migrated ${count} legacy journal entries`);
  return count;
}

const VOLATILE_KEYS = new Set(["id", "created_at", "issued_at", "expires_at", "target_session", "idempotency_key", "schema_version"]);

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// What the request asks for, independent of when and by whom it was asked.
export function payloadHash(payload: BridgeRequest): string {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) if (!VOLATILE_KEYS.has(key)) body[key] = value;
  return fnv1a(canonical(body));
}

export function journalKeyFor(payload: BridgeRequest): string | null {
  if (typeof payload.idempotency_key === "string" && payload.idempotency_key) return `key:${payload.idempotency_key}`;
  if (payload.id) return `id:${String(payload.id)}`;
  return null;
}

// ---------------------------------------------------------------------------
// Answering.

type Stamp = { completed_at: number; schema_version: string; bridge_protocol: string; surface_version: string; session_id: string };
function stamp(sessionId: string): Stamp {
  return { completed_at: Date.now() / 1000, schema_version: SCHEMA_VERSION, bridge_protocol: BRIDGE_PROTOCOL, surface_version: EXTENSION_SURFACE_VERSION, session_id: sessionId };
}

function outcomeOf(kind: OutcomeKind, code: string, extra: Partial<Outcome> = {}): Outcome {
  const base: Outcome = kind === "applied"
    ? { kind, code, applied: true, verified: true }
    : kind === "refused"
      ? { kind, code, applied: false, verified: true, side_effects: "nothing: the request was refused before Live was touched" }
      : kind === "failed"
        ? { kind, code, applied: false, verified: true }
        : { kind, code, applied: null, verified: false, next_step: "read the state (live_state) before retrying; a retry with the same key is refused, use a new key only for what the state shows is missing" };
  return { ...base, ...extra, kind, code };
}

function outcomeFromError(error: unknown): { name: string; message: string; outcome: Outcome } {
  if (error instanceof BridgeError) {
    const partial = error.outcome;
    const kind = partial.kind ?? "refused";
    const code = partial.code ?? (kind === "refused" ? "validation" : kind === "failed" ? "live_error" : "unknown");
    return { name: "BridgeError", message: error.message, outcome: outcomeOf(kind, code, partial) };
  }
  // Anything the SDK threw that the ops did not classify: whether it ran
  // before or after the mutation is not known here, so it is not guessed.
  const name = error instanceof Error ? error.name || "Error" : "Error";
  return { name, message: describe(error), outcome: outcomeOf("indeterminate", "unexpected_error", { side_effects: "unknown: the SDK threw outside the classified stages" }) };
}

function recordFor(payload: BridgeRequest, sessionId: string, status: "ok" | "error" | "indeterminate", body: { result?: BridgeResult; error?: string }, outcome: Outcome): Record<string, unknown> {
  return { ...payload, ...stamp(sessionId), status, ...body, outcome };
}

export function refuseRecord(payload: BridgeRequest, sessionId: string, code: string, message: string, extra: Partial<Outcome> = {}): Record<string, unknown> {
  return recordFor(payload, sessionId, "error", { error: `BridgeError: ${message}` }, outcomeOf("refused", code, extra));
}

function indeterminateRecord(payload: BridgeRequest, sessionId: string, code: string, message: string, extra: Partial<Outcome> = {}): Record<string, unknown> {
  return recordFor(payload, sessionId, "indeterminate", { error: `BridgeError: ${message}` }, outcomeOf("indeterminate", code, extra));
}

function replayRecord(payload: BridgeRequest, sessionId: string, earlier: JournalEntry): { record: Record<string, unknown>; destination: "done" | "errors" } {
  const status = earlier.status === "ok" ? "ok" : earlier.status === "indeterminate" ? "indeterminate" : "error";
  const outcome = earlier.outcome ?? outcomeOf(status === "ok" ? "applied" : status === "indeterminate" ? "indeterminate" : "failed", "recorded_without_outcome");
  const record = {
    ...recordFor(payload, sessionId, status, earlier.result !== undefined ? { result: earlier.result } : { error: earlier.error ?? "BridgeError: recorded without a message" }, outcome),
    replayed: true, replayed_from: earlier.request_id ?? null, replayed_at: earlier.at,
  };
  return { record, destination: status === "ok" ? "done" : "errors" };
}

// Processes exactly one request file: reads it, applies it, writes the
// outcome into done/ or errors/ and removes the request. Returns what was
// written so a caller (or a test) can look at it without re-reading.
export async function processRequestFile(live: LiveLike, dirs: BridgeDirs, fileName: string, sessionId: string, log: (line: string) => void = () => {}): Promise<Record<string, unknown> | null> {
  const record = await processRequestFileOnly(live, dirs, fileName, sessionId, log);
  if (record === null) return null;  // withdrawn before it was claimed
  // Every answered request republishes the state file, so a caller that reads
  // live_state.json right after a write sees the write, not the last timer tick.
  try {
    await publishState(live, dirs, sessionId);
  } catch (error) {
    log(`Loom bridge state after request failed: ${describe(error)}`);
  }
  return record;
}

// A request found in processing/ at startup was claimed and never answered:
// the host stopped while it was being applied. If the journal already holds
// its outcome (the host died between the journal line and the outcome file)
// that outcome is published; otherwise it is answered indeterminate, the
// journal records that, and it is never re-applied.
export function recoverProcessing(dirs: BridgeDirs, sessionId: string, log: (line: string) => void = () => {}): string[] {
  if (!existsSync(dirs.processing)) return [];
  const answered: string[] = [];
  const journal = readJournal(dirs);
  for (const fileName of readdirSync(dirs.processing).filter((name) => name.endsWith(".json")).sort()) {
    const claimed = join(dirs.processing, fileName);
    if (existsSync(join(dirs.done, fileName)) || existsSync(join(dirs.errors, fileName))) {
      unlinkSync(claimed);  // answered, only the claim file was left behind
      continue;
    }
    let payload: BridgeRequest = {};
    try {
      payload = JSON.parse(readFileSync(claimed, "utf8")) as BridgeRequest;
    } catch {
      payload = {};
    }
    const key = journalKeyFor(payload);
    const earlier = key && journal.state === "present" ? journal.entries.get(key) : undefined;
    if (earlier && earlier.status !== "started") {
      const { record, destination } = replayRecord(payload, sessionId, earlier);
      finish(claimed, join(destination === "done" ? dirs.done : dirs.errors, fileName), { ...record, recovered: "outcome_from_journal" });
      log(`Loom recovered ${fileName} from the journal (${earlier.status})`);
      answered.push(fileName);
      continue;
    }
    const record = indeterminateRecord(payload, sessionId, "indeterminate_after_restart",
      "indeterminate_after_restart: the host stopped while this request was being applied; it may or may not have taken effect and was not re-applied",
      { earlier_request_id: typeof payload.id === "string" ? payload.id : undefined, earlier_session: earlier?.session ?? (typeof payload.target_session === "string" ? payload.target_session : undefined),
        side_effects: `${payload.op ?? "write_clip"} may have been applied` });
    if (key) {
      try {
        appendJournal(dirs, { key, status: "indeterminate", request_id: typeof payload.id === "string" ? payload.id : undefined, op: typeof payload.op === "string" ? payload.op : undefined,
          session: earlier?.session ?? sessionId, payload_hash: earlier?.payload_hash ?? payloadHash(payload), at: Date.now() / 1000, error: String(record.error), outcome: record.outcome as Outcome });
      } catch (error) {
        log(`Loom could not journal the interrupted request ${fileName}: ${describe(error)}`);
      }
    }
    finish(claimed, join(dirs.errors, fileName), record);
    log(`Loom recovered interrupted request ${fileName} as indeterminate`);
    answered.push(fileName);
  }
  return answered;
}

// Why a request must not be applied, or null. Checked before Live is touched.
export function refusalFor(payload: BridgeRequest, sessionId: string, now = Date.now() / 1000): { code: string; message: string } | null {
  if (typeof payload.expires_at === "number" && payload.expires_at < now) {
    return { code: "expired_request", message: `expired_request: issued ${payload.issued_at ?? "?"}, expired ${payload.expires_at}, now ${now.toFixed(0)}; not applied` };
  }
  if (payload.target_session && payload.target_session !== sessionId) {
    return { code: "session_mismatch", message: `session_mismatch: request was issued for Live session ${String(payload.target_session)}, this is ${sessionId}; not applied` };
  }
  return null;
}

async function processRequestFileOnly(live: LiveLike, dirs: BridgeDirs, fileName: string, sessionId: string, log: (line: string) => void): Promise<Record<string, unknown> | null> {
  const requestPath = join(dirs.requests, fileName);
  // The claim: an atomic rename out of requests/. Exactly one of this rename
  // and the MCP's withdrawal (unlink) can win, so a request that is being
  // applied can no longer be taken back and reported as "never applied".
  const claimed = join(dirs.processing, fileName);
  try {
    mkdirSync(dirs.processing, { recursive: true });
    renameSync(requestPath, claimed);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;  // withdrawn first; nothing to do
    throw error;
  }
  let payload: BridgeRequest = {};
  try {
    payload = JSON.parse(readFileSync(claimed, "utf8")) as BridgeRequest;
  } catch (error) {
    const record = refuseRecord({}, sessionId, "unreadable_request", `unreadable request: ${describe(error)}`);
    finish(claimed, join(dirs.errors, fileName), record);
    return record;
  }
  const refuse = (code: string, message: string, extra: Partial<Outcome> = {}) => {
    const record = refuseRecord(payload, sessionId, code, message, extra);
    finish(claimed, join(dirs.errors, fileName), record);
    log(`Loom refused: ${message}`);
    return record;
  };
  const refusal = refusalFor(payload, sessionId);
  if (refusal) return refuse(refusal.code, refusal.message);

  const op = String(payload.op ?? "write_clip");
  const isRead = READ_OPS.has(op);
  // Mutations go through the journal: an earlier attempt with the same key is
  // answered from its record, refused when its outcome is unknown, refused
  // when it asked for something else or came from another session.
  const journalKey = isRead ? null : journalKeyFor(payload);
  const digest = payloadHash(payload);
  let journal: Journal | null = null;
  if (journalKey) {
    journal = readJournal(dirs);
    if (journal.state === "unreadable") {
      return refuse("journal_unreadable", `journal_unreadable: ${dirs.journalFile} cannot be read, so whether ${journalKey} already ran is unknown; not applied`,
        { next_step: "read the state to see what Live holds; retry only what is missing with keys that were never used; keep the journal file as evidence and do not delete it" });
    }
    if (journal.state === "missing") {
      return refuse("journal_missing", `journal_missing: this bridge recorded keyed requests before (marker ${new Date((journal.marker?.created_at ?? 0) * 1000).toISOString()}) but ${dirs.journalFile} is gone, so whether ${journalKey} already ran is unknown; not applied`,
        { next_step: "read the state to see what Live holds; retry only what is missing with keys that were never used (a new run id gives new keys); nothing needs to be deleted" });
    }
    if (journal.torn_tail) {
      // A torn last line is dropped before anything is appended after it:
      // appending to a torn line would glue two records into one bad line.
      compactJournal(dirs, journal, sessionId);
      journal = readJournal(dirs);
    }
    const earlier = journal.entries.get(journalKey);
    if (earlier) {
      if (earlier.status === "started" || earlier.status === "indeterminate") {
        const record = indeterminateRecord(payload, sessionId, "indeterminate_earlier_attempt",
          `indeterminate_earlier_attempt: request ${String(earlier.request_id ?? "?")} with ${journalKey} started in Live session ${earlier.session || "unknown"} and recorded no outcome (the host stopped mid-way); it may or may not have taken effect and is not applied again`,
          { earlier_request_id: earlier.request_id, earlier_session: earlier.session, side_effects: `${earlier.op ?? op} may have been applied by the earlier attempt` });
        finish(claimed, join(dirs.errors, fileName), record);
        log(`Loom refused (indeterminate earlier attempt): ${journalKey}`);
        return record;
      }
      if (earlier.session !== sessionId) {
        return refuse("replay_refused_other_session", `replay_refused_other_session: ${journalKey} was used in Live session ${earlier.session || "unknown"} (possibly another set); its outcome is not replayed here and the request is not applied -- use a new key`,
          { earlier_request_id: earlier.request_id, earlier_session: earlier.session });
      }
      if (earlier.payload_hash !== digest) {
        return refuse("idempotency_conflict", `idempotency_conflict: ${journalKey} was already used for a different request; not applied`, { earlier_request_id: earlier.request_id });
      }
      const { record, destination } = replayRecord(payload, sessionId, earlier);
      finish(claimed, join(destination === "done" ? dirs.done : dirs.errors, fileName), record);
      log(`Loom replayed ${journalKey}`);
      return record;
    }
    // Intent first: if the host dies after this line the key reads as started.
    try {
      appendJournal(dirs, { key: journalKey, status: "started", request_id: typeof payload.id === "string" ? payload.id : undefined, op, session: sessionId, payload_hash: digest, at: Date.now() / 1000 });
    } catch (error) {
      return refuse("journal_write_failed", `journal_write_failed: ${dirs.journalFile} could not be written (${describe(error)}); a mutation whose attempt cannot be recorded is not started`);
    }
  }

  let record: Record<string, unknown>;
  let destination: string;
  try {
    const result = await applyOperation(live, payload, { sessionId, ledger: new ClipLedger(dirs.ledgerFile, sessionId), journal: journal ? journalSummary(journal) : undefined, dirs });
    const warning = typeof result.warning === "string" ? result.warning : undefined;
    record = recordFor(payload, sessionId, "ok", { result }, outcomeOf("applied", warning ? "ledger_write_failed" : "applied", warning ? { warning } : {}));
    destination = dirs.done;
    log(`Loom ok: ${op}`);
  } catch (error) {
    const { name, message, outcome } = outcomeFromError(error);
    record = recordFor(payload, sessionId, outcome.kind === "indeterminate" ? "indeterminate" : "error", { error: `${name}: ${message}` }, outcome);
    destination = dirs.errors;
    log(`Loom ${outcome.kind}: ${name}: ${message}`);
  }
  if (journalKey) {
    try {
      appendJournal(dirs, { key: journalKey, status: record.status as JournalStatus, request_id: typeof payload.id === "string" ? payload.id : undefined, op, session: sessionId, payload_hash: digest, at: Date.now() / 1000,
        ...(record.result !== undefined ? { result: record.result as BridgeResult } : {}), ...(record.error !== undefined ? { error: String(record.error) } : {}), outcome: record.outcome as Outcome });
      if (journal && journal.lines + 2 > JOURNAL_COMPACT_LINES) compactJournal(dirs, readJournal(dirs), sessionId);
    } catch (error) {
      // The outcome file below still answers this request; the key stays
      // `started` in the journal, so a retry is refused rather than re-applied.
      log(`Loom could not journal the outcome of ${journalKey}: ${describe(error)}`);
    }
  }
  finish(claimed, join(destination, fileName), record);
  return record;
}

// The outcome is published whole (temp file + rename): a reader never sees a
// half-written record.
function finish(requestPath: string, outcomePath: string, record: Record<string, unknown>) {
  writeAtomic(outcomePath, JSON.stringify(record, null, 2));
  try {
    unlinkSync(requestPath);
  } catch {
    // Already gone; the outcome file is what the caller reads.
  }
}

export async function publishState(live: LiveLike, dirs: BridgeDirs, sessionId: string) {
  const state = await captureState(live, { session_id: sessionId, journal: journalSummary(readJournal(dirs)) }, true);
  writeAtomic(dirs.stateFile, JSON.stringify(state, null, 2));
  return state;
}

// The poller: one request per tick, oldest first, never two at once; state
// republished on its own cadence. Both timers are unref'd so they never keep
// the host alive on their own.
export type BridgeHandle = { stop(): void; dirs: BridgeDirs; sessionId: string };

export function startBridge(
  live: LiveLike,
  root: string,
  log: (line: string) => void,
  options: { sessionId?: string; requestMs?: number; stateMs?: number } = {},
): BridgeHandle {
  const sessionId = options.sessionId ?? newSessionId();
  const dirs = bridgeDirs(root);
  ensureBridgeDirs(dirs);
  migrateLegacyJournal(dirs, log);
  recoverProcessing(dirs, sessionId, log);
  let processing = false;
  let publishing = false;
  const tick = async () => {
    if (processing) return;
    processing = true;
    try {
      const [next] = pendingRequests(dirs);
      if (next) await processRequestFile(live, dirs, next, sessionId, log);
    } catch (error) {
      log(`Loom bridge tick failed: ${describe(error)}`);
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
      await publishState(live, dirs, sessionId);
    } catch (error) {
      log(`Loom bridge state failed: ${describe(error)}`);
    } finally {
      publishing = false;
    }
  };
  const requestTimer = setInterval(() => void tick(), options.requestMs ?? 250);
  const stateTimer = setInterval(() => void publish(), options.stateMs ?? 1000);
  requestTimer.unref?.();
  stateTimer.unref?.();
  void publish();
  log(`Loom bridge listening at ${dirs.root} (${EXTENSION_SURFACE_VERSION}, ${BRIDGE_PROTOCOL}, session ${sessionId})`);
  return {
    dirs,
    sessionId,
    stop() {
      clearInterval(requestTimer);
      clearInterval(stateTimer);
    },
  };
}
